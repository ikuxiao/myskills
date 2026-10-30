"""External content is copied, inspected and quarantined, never executed."""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from app.resources import KINDS, MAX_FILE_BYTES, MAX_FILES, MAX_TOTAL_BYTES, ResourceManager, parse_skill, validate_files
from app.storage import AppError, ConflictError, digest, is_link, json_bytes, now, safe_path


def collect_files(directory: Path, *, allow_empty=False, skip_vcs=True) -> dict[str, bytes]:
    if not directory.is_dir() or any(is_link(p) for p in [directory, *directory.parents]):
        raise AppError('请选择存在的普通目录，不支持符号链接或 junction。')
    files = {}
    total = 0
    # Walk manually so a large .git tree is never traversed.
    pending = [directory]
    visited = 0
    while pending:
        parent = pending.pop()
        for path in sorted(parent.iterdir()):
            if skip_vcs and path.name in ('.git', '.hg', '.svn'):
                continue
            visited += 1
            if visited > MAX_FILES * 4:
                raise AppError('目录过大，请选择单个资源目录。')
            rel = path.relative_to(directory).as_posix()
            safe_path(directory, rel)
            if path.is_dir():
                pending.append(path)
            elif path.is_file():
                size = path.stat().st_size
                if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES or len(files) >= MAX_FILES:
                    raise AppError('导入限制：最多 500 个文件，单文件 2 MB，总大小 20 MB。')
                value = path.read_bytes()
                total += len(value)
                files[rel] = value
    validate_files(files)
    if not files and not allow_empty:
        raise AppError('目录中没有可导入的文件。')
    return files


def security_report(files: dict[str, bytes]) -> list[dict]:
    findings = []
    patterns = [
        ('danger', r'(?i)(rm\s+-rf|Remove-Item.{0,50}-Recurse|format\s+[a-z]:|Invoke-Expression|\biex\s|curl.{0,150}\|\s*(sh|bash))', '包含危险命令或动态代码执行，请逐行核对。'),
        ('network', r'(?i)(https?://|curl\s|wget\s|requests\.|urllib\.|Invoke-WebRequest)', '包含联网地址或网络操作。'),
        ('instruction', r'(?i)(ignore (all |previous |prior )*instructions|忽略.{0,12}(指令|规则)|disable.{0,20}(security|safety))', '可能包含覆盖其他指令的内容。'),
        ('secret', r'(?i)(-----BEGIN (RSA |OPENSSH )?PRIVATE KEY|sk-[a-zA-Z0-9_-]{20,}|AKIA[A-Z0-9]{16}|(?:api[_-]?key|password|token)\s*[:=]\s*[\x22\x27][^\x22\x27]{12,})', '可能含敏感凭据；导入前请移除。'),
    ]
    for rel, value in files.items():
        if Path(rel).suffix.lower() in ('.py', '.sh', '.ps1', '.bat', '.cmd', '.js', '.exe', '.dll'):
            findings.append({'file': rel, 'type': 'script', 'message': '包含脚本或可执行文件；本工具不会运行它。'})
        if Path(rel).name.lower() in ('.env', 'id_rsa', 'id_ed25519', 'credentials.json'):
            findings.append({'file': rel, 'type': 'secret', 'message': '文件名可能对应敏感配置。'})
        text = value.decode('utf-8', errors='replace')
        for level, pattern, message in patterns:
            if re.search(pattern, text):
                findings.append({'file': rel, 'type': level, 'message': message})
    return findings


class Importer:
    def __init__(self, manager: ResourceManager):
        self.manager = manager
        self.store = manager.store

    def _index(self) -> dict:
        value = self.store.read_json('data/imports.json', {'version': 1, 'imports': []})
        if not isinstance(value, dict) or value.get('version') != 1 or not isinstance(value.get('imports'), list):
            raise AppError('待审核索引已损坏，请从 backups 恢复。')
        return value

    def scan(self, path: str) -> dict:
        directory = Path(path).expanduser().absolute()
        if not directory.is_dir() or any(is_link(p) for p in [directory, *directory.parents]):
            raise AppError('扫描目录不存在或包含链接。')
        skills, errors = [], []
        candidates = [directory] if (directory / 'SKILL.md').is_file() else sorted(directory.iterdir())
        for candidate in candidates[:500]:
            if not candidate.is_dir() or is_link(candidate) or not (candidate / 'SKILL.md').is_file():
                continue
            try:
                main = safe_path(candidate, 'SKILL.md')
                if main.stat().st_size > MAX_FILE_BYTES:
                    raise AppError('SKILL.md 超出 2 MB 限制。')
                metadata = parse_skill(main.read_text(encoding='utf-8-sig'))
                skills.append({**metadata, 'path': str(candidate)})
            except (AppError, OSError, UnicodeError) as exc:
                errors.append({'path': str(candidate), 'error': str(exc)})
        return {'skills': skills, 'errors': errors, 'truncated': len(candidates) > 500}

    def stage(self, source: str, kind='skill', *, subdirectory='') -> dict:
        if kind not in KINDS:
            raise AppError('未知资源类型。')
        source = source.strip()
        if not source:
            raise AppError('请填写来源目录或 GitHub 仓库地址。')
        remote = source.startswith(('https:', 'http:', 'git:', 'ssh:')) or '://' in source
        commit_id = None
        if remote:
            url = urlparse(source)
            if url.scheme != 'https' or url.hostname != 'github.com' or url.username or url.password or url.query or url.fragment or url.port:
                raise AppError('仅支持不带凭据的公开 https://github.com/owner/repo 仓库地址。')
            if not re.fullmatch(r'/[\w.-]+/[\w.-]+/?', url.path) or '..' in url.path:
                raise AppError('请填写仓库根地址，资源目录在“仓库内目录”中指定。')
            with tempfile.TemporaryDirectory(prefix='adc-import-') as temp:
                checkout = Path(temp) / 'repository'
                try:
                    result = subprocess.run(['git', '-c', 'core.hooksPath=NUL', '-c', 'protocol.file.allow=never', 'clone', '--depth', '1', '--no-tags', '--', source, str(checkout)],
                                            capture_output=True, text=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                    if result.returncode:
                        raise AppError('Git 下载失败，请确认公开仓库地址和网络。' + result.stderr[-400:])
                    commit_id = subprocess.run(['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True, text=True, timeout=10).stdout.strip()
                    directory = safe_path(checkout, subdirectory) if subdirectory else checkout
                    files = collect_files(directory)
                    root_licenses = {p.name: p.read_bytes() for p in checkout.iterdir() if p.is_file() and not is_link(p) and p.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')) and p.stat().st_size <= MAX_FILE_BYTES}
                    for name, value in root_licenses.items():
                        files.setdefault(name, value)
                except FileNotFoundError as exc:
                    raise AppError('Git 尚未安装；可先下载仓库，再通过本地目录导入。') from exc
                except subprocess.TimeoutExpired as exc:
                    raise AppError('Git 下载超时，请使用本地目录导入。') from exc
        else:
            base = Path(source).expanduser().absolute()
            directory = safe_path(base, subdirectory) if subdirectory else base
            files = collect_files(directory)
            source = str(directory.resolve())
        validate_files(files)
        main = 'SKILL.md' if kind == 'skill' else 'content.md'
        if main not in files and kind != 'skill':
            markdown = [rel for rel in files if '/' not in rel and rel.lower().endswith('.md') and not rel.upper().startswith(('LICENSE', 'NOTICE'))]
            if len(markdown) == 1:
                files[main] = files.pop(markdown[0])
        if main not in files:
            raise AppError(f'资源目录中缺少 {main}。请选择单个资源目录。')
        try:
            content = files[main].decode('utf-8-sig')
        except UnicodeError as exc:
            raise AppError('主文档必须使用 UTF-8 编码。') from exc
        info = parse_skill(content) if kind == 'skill' else {'name': Path(source.rstrip('/')).stem, 'description': ''}
        entry = {'id': uuid4().hex, 'version': 1, 'name': info['name'], 'description': info['description'], 'type': kind, 'source': source,
                 'subdirectory': subdirectory, 'commit': commit_id, 'created_at': now(), 'status': 'pending',
                 'files': {rel: digest(value) for rel, value in files.items()}, 'findings': security_report(files),
                 'licenses': [rel for rel in files if Path(rel).name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE'))]}
        if not entry['licenses']:
            entry['findings'].append({'file': '', 'type': 'license', 'message': '未找到许可证；请核实使用与再分发权限。'})
        entry['fingerprint'] = digest(json_bytes(entry['files']))
        with self.store.locked():
            index = self._index()
            index['imports'].append(entry)
            changes = {f'staging/imported/{entry["id"]}/{rel}': value for rel, value in files.items()}
            changes['data/imports.json'] = json_bytes(index)
            changes.update(self.store.event_change({'action': '导入待审核', 'name': entry['name'], 'source': source}))
            self.store.commit(changes)
        return entry

    def list_pending(self) -> list[dict]:
        with self.store.locked():
            return [entry for entry in self._index()['imports'] if entry['status'] == 'pending']

    def files(self, entry_id: str) -> dict[str, bytes]:
        with self.store.locked():
            entry = next((e for e in self._index()['imports'] if e['id'] == entry_id), None)
            if not entry:
                raise AppError('待审核资源不存在。')
            files = collect_files(self.store.path(f'staging/imported/{entry_id}'))
            if {rel: digest(value) for rel, value in files.items()} != entry['files']:
                raise ConflictError('待审核文件已被外部修改，请重新导入并审核。')
            return files

    def review(self, entry_id: str, approve: bool, *, expected: str | None = None) -> dict:
        with self.store.locked():
            index = self._index()
            entry = next((e for e in index['imports'] if e['id'] == entry_id), None)
            if not entry or entry['status'] != 'pending':
                raise AppError('此资源已审核或不存在。')
            if expected and expected != entry['fingerprint']:
                raise ConflictError('审核内容已变化，请重新查看。')
            resource = None
            if approve:
                files = self.files(entry_id)
                # Idempotent after a crash between library commit and review status.
                resource = next((r for r in self.manager.list_resources() if r.get('provenance', {}).get('staging_id') == entry_id), None)
                if resource is None:
                    main = 'SKILL.md' if entry['type'] == 'skill' else 'content.md'
                    resource = self.manager.create({'type': entry['type'], 'name': entry['name'], 'description': entry['description'], 'category': '外部导入', 'tags': ['已审核']},
                                                   files[main].decode('utf-8-sig'), files,
                                                   provenance={'source': entry['source'], 'commit': entry['commit'], 'licenses': entry['licenses'], 'staging_id': entry_id, 'reviewed_at': now(), 'fingerprint': entry['fingerprint']})
                entry['resource_id'] = resource['id']
            entry['status'] = 'approved' if approve else 'rejected'
            entry['reviewed_at'] = now()
            self.store.commit({'data/imports.json': json_bytes(index), **self.store.event_change({'action': '审核通过' if approve else '审核拒绝', 'name': entry['name']})})
            return resource or entry
