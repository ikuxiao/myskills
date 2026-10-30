"""Codex deployment: native disabled entries, ownership and full-tree conflicts."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from uuid import uuid4

import tomlkit
from filelock import FileLock

from app.importer import collect_files
from app.resources import ResourceManager
from app.storage import AppError, ConflictError, atomic_write, digest, is_link, json_bytes, now


def checked_absolute(path: str | Path) -> Path:
    candidate = Path(path).expanduser().absolute()
    for parent in [candidate, *candidate.parents]:
        if is_link(parent):
            raise AppError('同步路径中不能包含符号链接或 junction。')
    if candidate == Path(candidate.anchor):
        raise AppError('同步目标不能是磁盘根目录。')
    return candidate.resolve()


def file_value(path: Path) -> bytes | None:
    checked_absolute(path)
    return path.read_bytes() if path.exists() else None


class CodexAdapter:
    def __init__(self, manager: ResourceManager):
        self.manager = manager
        self.store = manager.store
        with self.store.locked():
            self._recover()

    def _recover(self) -> None:
        journal = self.store.read_json('data/sync_transaction.json')
        if not journal:
            return
        # A failed or interrupted apply restores the exact original bytes.
        if journal['phase'] != 'committed':
            for path_string, before in journal['before'].items():
                path = checked_absolute(path_string)
                if before is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, base64.b64decode(before))
        self.store.path('data/sync_transaction.json').unlink(missing_ok=True)

    def _build(self, target: str, project=None) -> tuple[dict, dict[Path, bytes | None], dict]:
        target_dir = checked_absolute(target)
        if project and not checked_absolute(project).is_dir():
            raise AppError('项目目录不存在。')
        skill_root = checked_absolute(Path(project) / '.agents' / 'skills' if project else target_dir.parent / '.agents' / 'skills')
        if any(p.is_relative_to(self.store.root) or self.store.root.is_relative_to(p) for p in (target_dir, skill_root)):
            raise AppError('同步目录不能与本程序数据目录重叠。')
        config_path = checked_absolute(target_dir / 'config.toml')
        manifest_path = checked_absolute(target_dir / '.adc-manifest.json')
        raw_config = file_value(config_path)
        raw_manifest = file_value(manifest_path)
        try:
            config = tomlkit.parse((raw_config or b'').decode('utf-8'))
        except Exception as exc:
            raise AppError('Codex config.toml 格式错误；请修正后重试，原文件未改动。') from exc
        try:
            manifest = json.loads(raw_manifest) if raw_manifest else {'version': 1, 'deployments': {}}
            if manifest.get('version') != 1 or not isinstance(manifest['deployments'], dict):
                raise ValueError('manifest schema')
        except (ValueError, KeyError, TypeError) as exc:
            raise AppError('Codex 托管清单损坏，请从同步备份恢复。') from exc
        if 'skills' not in config:
            config['skills'] = tomlkit.table()
        skills = config['skills']
        if not isinstance(skills, dict):
            raise AppError('Codex skills 配置应为 TOML 表。')
        if 'config' not in skills:
            skills['config'] = tomlkit.aot()
        configs = skills['config']
        if not isinstance(configs, list):
            raise AppError('Codex skills.config 配置应为数组。')
        changes, conflicts, actions, source_versions, external_versions = {}, [], [], {}, {}
        resources = self.manager.list_resources()
        for resource in resources:
            if resource['type'] != 'skill':
                continue
            destination = checked_absolute(skill_root / f'adc-{resource["id"]}')
            skill_path = (destination / 'SKILL.md').as_posix()
            old = manifest['deployments'].get(skill_path)
            source_versions[resource['id']] = {'revision': resource['revision'], 'enabled': self.manager.effective(resource['id'], project)}
            desired_files = self.manager._files(self.manager._find(resource['id']))
            existing = collect_files(destination, allow_empty=True, skip_vcs=False) if destination.exists() else {}
            existing_hashes = {rel: digest(value) for rel, value in existing.items()}
            external_versions[skill_path] = existing_hashes
            if old is None and existing:
                conflicts.append(f'{resource["display_name"]}：目标目录已存在但不属于本工具管理。')
            if old is not None and existing_hashes != old['files']:
                conflicts.append(f'{resource["display_name"]}：目标文件有外部修改、增删或缺失，原文件已保留。')
            entries = [entry for entry in configs if isinstance(entry, dict) and str(entry.get('path', '')).replace('\\', '/') == skill_path]
            if len(entries) > 1 or (entries and old is None):
                conflicts.append(f'{resource["display_name"]}：已有非托管或重复的 skills.config 配置。')
            if old is not None and (not entries or entries[0].get('enabled', True) != old['enabled']):
                conflicts.append(f'{resource["display_name"]}：托管的启停配置被外部修改。')
            enabled = self.manager.effective(resource['id'], project)
            if entries:
                entries[0]['enabled'] = enabled
            else:
                entry = tomlkit.table()
                entry['path'] = skill_path
                entry['enabled'] = enabled
                configs.append(entry)
            hashes = {rel: digest(value) for rel, value in desired_files.items()}
            for rel, value in desired_files.items():
                dest_file = checked_absolute(destination / rel)
                if existing.get(rel) != value:
                    changes[dest_file] = value
            for rel in existing:
                if rel not in desired_files:
                    changes[checked_absolute(destination / rel)] = None
            state = '新建部署' if old is None else ('更新' if old['files'] != hashes else ('启用' if enabled else '禁用') if old['enabled'] != enabled else '已同步')
            actions.append({'id': resource['id'], 'name': resource['display_name'], 'action': state, 'enabled': enabled, 'destination': str(destination), 'files': len(hashes)})
            manifest['deployments'][skill_path] = {'id': resource['id'], 'files': hashes, 'enabled': enabled, 'revision': resource['revision']}
        # Do not create empty config tables for a library without skills.
        if actions:
            next_config = tomlkit.dumps(config).encode('utf-8')
            if next_config != raw_config:
                changes[config_path] = next_config
            if json_bytes(manifest) != raw_manifest:
                changes[manifest_path] = json_bytes(manifest)
        fingerprint = digest(json_bytes({'target': str(target_dir), 'project': str(project or ''), 'config': digest(raw_config or b''), 'manifest': digest(raw_manifest or b''),
                                         'sources': source_versions, 'targets': external_versions}))
        plan = {'target': str(target_dir), 'skill_root': str(skill_root), 'project': str(project or ''), 'fingerprint': fingerprint,
                'actions': actions, 'conflicts': conflicts, 'changed_files': len(changes),
                'manual_resources': [r['display_name'] for r in resources if r['type'] != 'skill'],
                'notice': '同步完成后请重新加载 Codex 会话。Prompt、Rules 与技巧仅支持复制/导出，不会自动注入。'}
        return plan, changes, manifest

    def preview(self, target: str, project=None) -> dict:
        with self.store.locked():
            self._recover()
            return self._build(target, project)[0]

    def _apply(self, changes: dict[Path, bytes | None], label: str) -> str:
        sync_id = uuid4().hex
        before = {str(path): base64.b64encode(value).decode('ascii') if (value := file_value(path)) is not None else None for path in changes}
        after = {str(path): digest(value) if value is not None else None for path, value in changes.items()}
        journal = {'version': 1, 'id': sync_id, 'at': now(), 'phase': 'applying', 'before': before, 'after': after, 'action': label}
        self.store.commit({f'backups/sync/{sync_id}.json': json_bytes(journal), 'data/sync_transaction.json': json_bytes(journal)})
        try:
            for path, value in changes.items():
                checked_absolute(path)
                if value is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, value)
            for path, value in changes.items():
                if file_value(path) != value:
                    raise OSError(f'写入后验证失败：{path.name}')
            journal['phase'] = 'committed'
            log_path = self.store.path('data/sync_records.jsonl')
            old_log = log_path.read_bytes() if log_path.exists() else b''
            record = {'id': sync_id, 'at': now(), 'action': label, 'files': len(changes), 'ok': True}
            self.store.commit({'data/sync_transaction.json': json_bytes(journal), 'data/sync_records.jsonl': old_log + json.dumps(record, ensure_ascii=False).encode('utf-8') + b'\n',
                               **self.store.event_change({'action': label, 'sync_id': sync_id, 'files': len(changes)})})
        except BaseException:
            self._recover()
            raise
        self.store.path('data/sync_transaction.json').unlink(missing_ok=True)
        return sync_id

    def sync(self, target: str, project=None, *, expected: str | None = None) -> dict:
        with self.store.locked():
            self._recover()
            target_dir = checked_absolute(target)
            target_dir.mkdir(parents=True, exist_ok=True)
            with FileLock(str(target_dir / '.adc-sync.lock'), timeout=10):
                plan, changes, _ = self._build(target, project)
                if expected and plan['fingerprint'] != expected:
                    raise ConflictError('资源、状态或目标配置在预览后发生变化，请重新预览。')
                if plan['conflicts']:
                    raise ConflictError('同步已停止：' + '；'.join(plan['conflicts']))
                sync_id = self._apply(changes, 'Codex 同步') if changes else None
                return {'ok': True, 'id': sync_id, 'changed_files': len(changes), 'notice': plan['notice']}

    def rollback(self, sync_id: str) -> dict:
        with self.store.locked():
            self._recover()
            if not len(sync_id) == 32 or any(c not in '0123456789abcdef' for c in sync_id):
                raise AppError('无效的同步记录。')
            backup = self.store.read_json(f'backups/sync/{sync_id}.json')
            if not backup:
                raise AppError('同步备份不存在。')
            changes = {}
            for path_string, old in backup['before'].items():
                path = checked_absolute(path_string)
                current = file_value(path)
                if (digest(current) if current is not None else None) != backup['after'][path_string]:
                    raise ConflictError('同步后的文件又被修改，不能直接回滚。请先手动备份或处理冲突。')
                changes[path] = base64.b64decode(old) if old is not None else None
            result_id = self._apply(changes, f'回滚同步 {sync_id[:8]}')
            return {'ok': True, 'id': result_id}

    def records(self) -> list[dict]:
        with self.store.locked():
            path = self.store.path('data/sync_records.jsonl')
            return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()][::-1] if path.exists() else []
