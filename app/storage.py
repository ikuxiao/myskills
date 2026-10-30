"""Atomic multi-file writes with a recoverable journal and one process lock."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator
from uuid import uuid4

from filelock import FileLock


class AppError(ValueError):
    """An actionable validation or conflict error suitable for the UI."""


class ConflictError(AppError):
    pass


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def digest(value: bytes | str) -> str:
    return hashlib.sha256(value.encode('utf-8') if isinstance(value, str) else value).hexdigest()


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def safe_path(root: Path, relative: str) -> Path:
    """Use portable, relative POSIX paths; reject links even if inside root."""
    if not isinstance(relative, str) or not relative or '\\' in relative or ':' in relative:
        raise AppError('文件路径必须是相对路径，且不能包含反斜杠或盘符。')
    rel = PurePosixPath(relative)
    if rel.is_absolute() or any(part in ('..', '.') for part in relative.split('/')):
        raise AppError('文件路径不能越过资源目录。')
    reserved = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}
    for part in rel.parts:
        if not part or part.endswith((' ', '.')) or part.split('.')[0].upper() in reserved:
            raise AppError('文件名包含 Windows 不支持的名称。')
    candidate = root.joinpath(*rel.parts)
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise AppError('文件路径越界。')
    current = root
    for part in rel.parts:
        current = current / part
        if is_link(current):
            raise AppError('不允许通过符号链接或 junction 读写资源。')
    return candidate


def atomic_write(path: Path, value: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.adc-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class FileStore:
    def __init__(self, root: str | Path):
        raw = Path(root).expanduser().absolute()
        if any(is_link(p) for p in [raw, *raw.parents]):
            raise AppError('数据目录不能位于符号链接或 junction 中。')
        self.root = raw.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = FileLock(str(self.root / '.write.lock'), timeout=15)
        with self.lock:
            self._recover()

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self.lock:
            yield

    def path(self, relative: str) -> Path:
        return safe_path(self.root, relative)

    def read_json(self, relative: str, default=None):
        path = self.path(relative)
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except (json.JSONDecodeError, UnicodeError) as exc:
            raise AppError(f'{relative} 已损坏。原文件已保留，可在 backups 中恢复备份。') from exc

    def _recover(self) -> None:
        journal = self.read_json('.transaction.json')
        if journal is None:
            return
        if journal.get('phase') != 'committed':
            for rel, before in journal['before'].items():
                path = self.path(rel)
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, base64.b64decode(before))
        self.path('.transaction.json').unlink(missing_ok=True)

    def commit(self, changes: dict[str, bytes | None], *, backup: bool = True) -> None:
        if not changes:
            return
        with self.lock:
            self._recover()
            before = {}
            for rel in changes:
                path = self.path(rel)
                before[rel] = base64.b64encode(path.read_bytes()).decode('ascii') if path.exists() else None
            journal = {'version': 1, 'phase': 'applying', 'before': before}
            atomic_write(self.path('.transaction.json'), json_bytes(journal))
            try:
                if backup:
                    for rel, value in before.items():
                        if value is not None and rel.startswith('data/'):
                            atomic_write(self.path(f'backups/{uuid4().hex}-{Path(rel).name}'), base64.b64decode(value))
                for rel, value in changes.items():
                    path = self.path(rel)
                    if value is None:
                        path.unlink(missing_ok=True)
                    else:
                        atomic_write(path, value)
                journal['phase'] = 'committed'
                atomic_write(self.path('.transaction.json'), json_bytes(journal))
            except BaseException:
                self._recover()
                raise
            self.path('.transaction.json').unlink(missing_ok=True)

    def event_change(self, event: dict) -> dict[str, bytes]:
        rel = 'data/events.jsonl'
        old = self.path(rel).read_bytes() if self.path(rel).exists() else b''
        line = json.dumps({'at': now(), **event}, ensure_ascii=False) + '\n'
        return {rel: old + line.encode('utf-8')}

    def events(self, limit=20) -> list[dict]:
        with self.lock:
            path = self.path('data/events.jsonl')
            if not path.exists():
                return []
            return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()[-limit:]][::-1]
