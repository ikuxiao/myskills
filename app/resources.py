"""The resource service owns all state changes, validation and snapshots."""
from __future__ import annotations

import base64
import copy
import io
import json
import re
import zipfile
from pathlib import Path
from uuid import uuid4

import yaml

from app.analysis import estimate_tokens
from app.storage import AppError, ConflictError, FileStore, digest, json_bytes, now, safe_path

KINDS = {'skill': 'skills', 'prompt': 'prompts', 'rule': 'rules', 'tip': 'tips', 'template': 'templates'}
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 20 * 1024 * 1024
MAX_FILES = 500


def parse_skill(content: str) -> dict:
    match = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', content, re.S)
    if not match:
        raise AppError('SKILL.md 必须以 YAML frontmatter 开头，包含 name 和 description。')
    try:
        metadata = yaml.safe_load(match[1])
    except yaml.YAMLError as exc:
        raise AppError('Skill YAML 格式错误，请检查缩进及引号。') from exc
    if not isinstance(metadata, dict):
        raise AppError('Skill frontmatter 必须是键值对象。')
    name = metadata.get('name', '')
    description = metadata.get('description', '')
    if not isinstance(name, str) or len(name) > 64 or not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', name):
        raise AppError('Skill name 应为 1–64 位小写英文、数字及单连字符。')
    if not isinstance(description, str) or not description.strip() or len(description) > 1024:
        raise AppError('Skill description 必须为 1–1024 字符的文字。')
    return {'name': name, 'description': description.strip()}


def validate_files(files: dict[str, bytes]) -> None:
    if len(files) > MAX_FILES or sum(len(value) for value in files.values()) > MAX_TOTAL_BYTES:
        raise AppError('资源超出限制：最多 500 个文件、总大小 20 MB。')
    for rel, value in files.items():
        safe_path(Path.cwd(), rel)
        if any(part.lower() in ('.git', '.svn', '.hg') for part in rel.split('/')):
            raise AppError('资源中不能包含版本库内部目录。')
        if len(value) > MAX_FILE_BYTES:
            raise AppError(f'{rel} 超出单文件 2 MB 限制。')


class ResourceManager:
    def __init__(self, root: str | Path):
        self.store = FileStore(root)
        with self.store.locked():
            defaults = {
                'data/resources.json': {'version': 1, 'resources': []},
                'data/states.json': {'version': 1, 'global': {}, 'tools': {'codex': {}}, 'projects': {}},
                'data/settings.json': {'version': 1, 'codex_dir': str(Path.home() / '.codex'), 'project': ''},
            }
            self.store.commit({rel: json_bytes(value) for rel, value in defaults.items() if not self.store.path(rel).exists()}, backup=False)
            for rel in defaults:
                self._document(rel)

    def _document(self, rel: str) -> dict:
        doc = self.store.read_json(rel)
        if not isinstance(doc, dict) or doc.get('version') != 1:
            raise AppError(f'{rel} 格式或版本不支持；请从 backups 恢复。')
        if rel.endswith('resources.json') and (not isinstance(doc.get('resources'), list) or any(not isinstance(r, dict) for r in doc['resources'])):
            raise AppError('资源索引已损坏，请从 backups 恢复。')
        if rel.endswith('resources.json'):
            ids = set()
            for resource in doc['resources']:
                rid = resource.get('id', '')
                kind = resource.get('type', '')
                required = ('name', 'display_name', 'description', 'category', 'created_at', 'updated_at', 'source')
                if (not isinstance(rid, str) or not re.fullmatch(r'[a-f0-9]{32}', rid) or rid in ids or kind not in KINDS
                        or resource.get('source_path') != f'library/{KINDS.get(kind, "")}/{rid}'
                        or any(not isinstance(resource.get(key), str) for key in required)
                        or not isinstance(resource.get('tags'), list) or any(not isinstance(tag, str) for tag in resource['tags'])
                        or not isinstance(resource.get('provenance'), dict) or type(resource.get('reviewed')) is not bool):
                    raise AppError('资源索引字段已损坏，请从 backups 恢复。')
                ids.add(rid)
        if rel.endswith('states.json') and any(not isinstance(doc.get(key), dict) for key in ('global', 'tools', 'projects')):
            raise AppError('启停状态已损坏，请从 backups 恢复。')
        if rel.endswith('states.json'):
            maps = [doc['global'], *doc['tools'].values(), *doc['projects'].values()]
            if any(not isinstance(level, dict) or any(type(value) is not bool for value in level.values()) for level in maps):
                raise AppError('启停状态字段已损坏，请从 backups 恢复。')
        return doc

    def _index(self) -> dict:
        return self._document('data/resources.json')

    def _find(self, resource_id: str) -> dict:
        for resource in self._index()['resources']:
            if resource['id'] == resource_id:
                return copy.deepcopy(resource)
        raise AppError('资源不存在，可能已在其他窗口修改。')

    def _files(self, resource: dict, *, allow_missing=False) -> dict[str, bytes]:
        directory = self.store.path(resource['source_path'])
        result = {}
        if not directory.exists():
            if allow_missing:
                return {}
            raise AppError('资源正文目录丢失。请通过版本历史恢复，勿覆盖原索引。')
        for path in sorted(directory.rglob('*')):
            rel = path.relative_to(directory).as_posix()
            safe_path(directory, rel)
            if path.is_file():
                result[rel] = path.read_bytes()
        validate_files(result)
        return result

    @staticmethod
    def _revision(metadata: dict, files: dict[str, bytes]) -> str:
        return digest(json_bytes({'metadata': metadata, 'files': {rel: digest(value) for rel, value in sorted(files.items())}}))

    def _get(self, resource: dict) -> dict:
        files = self._files(resource)
        main = 'SKILL.md' if resource['type'] == 'skill' else 'content.md'
        try:
            content = files[main].decode('utf-8')
        except (KeyError, UnicodeError) as exc:
            raise AppError('主文档缺失或不是 UTF-8，请通过历史恢复。') from exc
        return {**resource, 'content': content, 'files': [main, *sorted(rel for rel in files if rel != main)], 'revision': self._revision(resource, files),
                'estimated_tokens': estimate_tokens(content), 'bytes': sum(map(len, files.values()))}

    def get(self, resource_id: str) -> dict:
        with self.store.locked():
            return self._get(self._find(resource_id))

    def list_resources(self, query='', kind=None, category=None, tag=None) -> list[dict]:
        with self.store.locked():
            resources = [self._get(r) for r in self._index()['resources']]
            result = []
            for resource in resources:
                if kind and resource['type'] != kind:
                    continue
                if category and resource['category'] != category:
                    continue
                if tag and tag not in resource['tags']:
                    continue
                haystack = ' '.join([resource['name'], resource['display_name'], resource['description'], resource['category'], *resource['tags'], resource['content']]).casefold()
                if query.casefold().strip() not in haystack:
                    continue
                result.append({**resource, 'enabled': self.effective(resource['id'])})
            return sorted(result, key=lambda r: r['updated_at'], reverse=True)

    def _metadata(self, data: dict, content: str, *, existing: dict | None = None) -> dict:
        kind = (existing or {}).get('type', data.get('type', 'skill'))
        if kind not in KINDS:
            raise AppError('不支持的资源类型。')
        if not isinstance(content, str) or not content.strip():
            raise AppError('资源正文不能为空。')
        metadata = copy.deepcopy(existing or {})
        for key in ('name', 'display_name', 'description', 'category'):
            value = data.get(key, metadata.get(key, ''))
            if not isinstance(value, str) or len(value) > 2048:
                raise AppError(f'{key} 必须是最多 2048 字符的文字。')
            metadata[key] = value.strip()
        if kind == 'skill':
            skill_metadata = parse_skill(content)
            metadata.update(skill_metadata)
        if not metadata['name'] or len(metadata['name']) > 128:
            raise AppError('名称必填且最多 128 字符。')
        metadata['display_name'] = metadata['display_name'] or metadata['name']
        metadata['category'] = metadata['category'] or '通用'
        tags = data.get('tags', metadata.get('tags', []))
        if isinstance(tags, str):
            tags = re.split(r'[,，]', tags)
        if not isinstance(tags, list) or any(not isinstance(t, str) for t in tags) or len(tags) > 50:
            raise AppError('标签必须是最多 50 项的文字列表。')
        metadata['tags'] = list(dict.fromkeys(t.strip()[:64] for t in tags if t.strip()))
        metadata['type'] = kind
        metadata['updated_at'] = now()
        return metadata

    def _snapshot(self, resource: dict, files: dict[str, bytes], action: str) -> tuple[str, bytes]:
        version_id = uuid4().hex
        snapshot = {'version': 1, 'id': version_id, 'at': now(), 'action': action, 'metadata': resource,
                    'files': {rel: base64.b64encode(value).decode('ascii') for rel, value in files.items()}}
        return f'history/resources/{resource["id"]}/{version_id}.json', json_bytes(snapshot)

    def create(self, data: dict, content: str, files: dict[str, bytes] | None = None, *, provenance: dict | None = None) -> dict:
        with self.store.locked():
            metadata = self._metadata(data, content)
            resource_id = uuid4().hex
            metadata.update({'id': resource_id, 'created_at': now(), 'source': 'local' if provenance is None else provenance['source'],
                             'provenance': provenance or {}, 'reviewed': True,
                             'source_path': f'library/{KINDS[metadata["type"]]}/{resource_id}'})
            main = 'SKILL.md' if metadata['type'] == 'skill' else 'content.md'
            resource_files = {**(files or {}), main: content.encode('utf-8')}
            validate_files(resource_files)
            index = self._index()
            index['resources'].append(metadata)
            states = self._document('data/states.json')
            states['global'][resource_id] = False
            snap_path, snapshot = self._snapshot(metadata, resource_files, '创建' if provenance is None else '审核导入')
            changes = {f'{metadata["source_path"]}/{rel}': value for rel, value in resource_files.items()}
            changes.update({'data/resources.json': json_bytes(index), 'data/states.json': json_bytes(states), snap_path: snapshot})
            changes.update(self.store.event_change({'action': '创建' if provenance is None else '审核导入', 'resource_id': resource_id, 'name': metadata['display_name']}))
            self.store.commit(changes)
            return self.get(resource_id)

    def _save(self, resource_id: str, metadata: dict, files: dict[str, bytes], action: str) -> dict:
        validate_files(files)
        old = self._find(resource_id)
        old_files = self._files(old, allow_missing=True)
        index = self._index()
        index['resources'] = [metadata if r['id'] == resource_id else r for r in index['resources']]
        changes = {f'{old["source_path"]}/{rel}': None for rel in old_files if rel not in files}
        changes.update({f'{metadata["source_path"]}/{rel}': value for rel, value in files.items()})
        snap_path, snapshot = self._snapshot(metadata, files, action)
        changes.update({'data/resources.json': json_bytes(index), snap_path: snapshot})
        changes.update(self.store.event_change({'action': action, 'resource_id': resource_id, 'name': metadata['display_name']}))
        self.store.commit(changes)
        return self.get(resource_id)

    def update(self, resource_id: str, data: dict, content: str, *, expected_revision: str | None = None) -> dict:
        with self.store.locked():
            current = self.get(resource_id)
            if expected_revision and current['revision'] != expected_revision:
                raise ConflictError('资源已被其他窗口或外部程序修改，请重新打开后再保存。')
            old = self._find(resource_id)
            metadata = self._metadata(data, content, existing=old)
            files = self._files(old)
            files['SKILL.md' if old['type'] == 'skill' else 'content.md'] = content.encode('utf-8')
            return self._save(resource_id, metadata, files, '编辑')

    def read_file(self, resource_id: str, relative: str) -> bytes:
        with self.store.locked():
            resource = self._find(resource_id)
            safe_path(self.store.path(resource['source_path']), relative)
            files = self._files(resource)
            if relative not in files:
                raise AppError('文件不存在。')
            return files[relative]

    def write_file(self, resource_id: str, relative: str, content: str, *, expected_revision: str | None = None) -> dict:
        with self.store.locked():
            resource = self._find(resource_id)
            safe_path(self.store.path(resource['source_path']), relative)
            if expected_revision and self.get(resource_id)['revision'] != expected_revision:
                raise ConflictError('资源已修改，请重新打开编辑器。')
            if relative == ('SKILL.md' if resource['type'] == 'skill' else 'content.md'):
                return self.update(resource_id, {}, content, expected_revision=expected_revision)
            files = self._files(resource)
            files[relative] = content.encode('utf-8')
            resource['updated_at'] = now()
            return self._save(resource_id, resource, files, f'编辑文件 {relative}')

    def copy(self, resource_id: str) -> dict:
        with self.store.locked():
            resource = self._find(resource_id)
            data = {**resource, 'name': resource['name'] + '-copy', 'display_name': resource['display_name'] + ' 副本'}
            files = self._files(resource)
            main = 'SKILL.md' if resource['type'] == 'skill' else 'content.md'
            content = files[main].decode('utf-8')
            if resource['type'] == 'skill':
                fm = re.match(r'\A---\r?\n(.*?)\r?\n---(?:\r?\n|\Z)', content, re.S)
                content = content[:fm.start(1)] + re.sub(r'^name:.*$', f'name: {resource["name"][:53]}-copy', fm[1], flags=re.M) + content[fm.end(1):]
            return self.create(data, content, files, provenance=resource['provenance'] or None)

    def export(self, resource_id: str) -> bytes:
        with self.store.locked():
            resource = self._find(resource_id)
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
                for rel, value in self._files(resource).items():
                    archive.writestr(f'{resource["name"]}/{rel}', value)
                archive.writestr(f'{resource["name"]}/adc-provenance.json', json_bytes({'source': resource['source'], 'provenance': resource['provenance']}))
            return output.getvalue()

    def history(self, resource_id: str) -> list[dict]:
        with self.store.locked():
            self._find(resource_id)
            directory = self.store.path(f'history/resources/{resource_id}')
            history = [self.store.read_json(path.relative_to(self.store.root).as_posix()) for path in directory.glob('*.json')]
            return sorted(history, key=lambda snapshot: snapshot['at'], reverse=True)

    def restore(self, resource_id: str, version_id: str, *, expected_revision: str | None = None) -> dict:
        with self.store.locked():
            current = self._find(resource_id)
            if expected_revision and self.get(resource_id)['revision'] != expected_revision:
                raise ConflictError('资源已变化，请重新查看历史后再回滚。')
            snapshot = next((v for v in self.history(resource_id) if v['id'] == version_id), None)
            if snapshot is None:
                raise AppError('历史版本不存在。')
            files = {rel: base64.b64decode(value) for rel, value in snapshot['files'].items()}
            main = 'SKILL.md' if current['type'] == 'skill' else 'content.md'
            metadata = self._metadata(snapshot['metadata'], files[main].decode('utf-8'), existing=current)
            return self._save(resource_id, metadata, files, f'回滚至 {version_id[:8]}')

    @staticmethod
    def project_key(project: str | Path) -> str:
        if not str(project).strip():
            raise AppError('请先选择项目目录。')
        return str(Path(project).expanduser().resolve())

    def set_state(self, resource_id: str, scope: str, enabled: bool | None, project=None) -> None:
        if scope not in ('global', 'tool', 'project') or (enabled is not None and type(enabled) is not bool):
            raise AppError('无效的状态或作用域。')
        with self.store.locked():
            resource = self._find(resource_id)
            if enabled and not resource['reviewed']:
                raise AppError('外部资源尚未审核。')
            states = self._document('data/states.json')
            if scope == 'global':
                container = states['global']
            elif scope == 'tool':
                container = states['tools'].setdefault('codex', {})
            else:
                container = states['projects'].setdefault(self.project_key(project), {})
            if enabled is None:
                container.pop(resource_id, None)
            else:
                container[resource_id] = enabled
            changes = {'data/states.json': json_bytes(states)}
            changes.update(self.store.event_change({'action': '启停设置', 'resource_id': resource_id, 'scope': scope, 'enabled': enabled, 'project': str(project or '')}))
            self.store.commit(changes)

    def effective(self, resource_id: str, project=None) -> bool:
        with self.store.locked():
            resource = self._find(resource_id)
            if not resource.get('reviewed'):
                return False
            states = self._document('data/states.json')
            levels = [states['global'].get(resource_id), states['tools'].get('codex', {}).get(resource_id)]
            if project:
                levels.append(states['projects'].get(self.project_key(project), {}).get(resource_id))
            if False in levels:
                return False
            return any(value is True for value in levels)

    def settings(self) -> dict:
        with self.store.locked():
            return self._document('data/settings.json')

    def save_settings(self, codex_dir: str, project='') -> None:
        if not codex_dir.strip():
            raise AppError('Codex 配置目录不能为空。')
        with self.store.locked():
            settings = {'version': 1, 'codex_dir': str(Path(codex_dir).expanduser().resolve()), 'project': self.project_key(project) if project else ''}
            self.store.commit({'data/settings.json': json_bytes(settings), **self.store.event_change({'action': '保存工具设置'})})

    def seed(self) -> None:
        with self.store.locked():
            if self._index()['resources'] or self.settings().get('seeded'):
                return
            self.create({'type': 'skill', 'name': 'focused-development', 'display_name': '聚焦式开发', 'category': 'AI 编程', 'tags': ['开发', '验证', 'Token 优化']},
                        '---\nname: focused-development\ndescription: 用于边界明确的代码修改、影响分析和测试验证。\n---\n\n# 聚焦式开发\n\n先定位相关文件与现有约定，再实现最小完整变更。\n\n1. 明确验收结果和修改范围。\n2. 只读取与任务有关的文档和代码。\n3. 保留用户已有改动。\n4. 运行与改动风险相称的验证。\n5. 汇报结果、证据和仍需关注的限制。\n')
            self.create({'type': 'prompt', 'name': 'code-review', 'display_name': '代码审查模板', 'category': 'AI 编程', 'tags': ['审查', '模板']},
                        '# 代码审查\n\n请审查 {{project}} 中的 {{scope}}。\n\n优先报告能说明触发条件和实际影响的问题，给出文件位置与修复建议。\n验证重点：{{focus}}。\n')
            self.create({'type': 'tip', 'name': 'context-on-demand', 'display_name': '让上下文按需加载', 'category': 'Token 优化', 'tags': ['上下文', '最佳实践']},
                        '# 让上下文按需加载\n\n## 解决的问题\n全局指令中堆积大量与当前任务无关的内容。\n\n## 适用场景\n多个语言、项目和工具共用一套资源库。\n\n## 推荐做法\n全局只保留长期通用约束；语言与任务流程放入 Skill；详细示例移至 references/，需要时再读取。\n\n## 示例\n使用 rg 定位目标文件，再读取相关段落。\n\n## 注意事项\n静态 Token 估算仅用于比较，不能替代真实用量。\n\n来源：本项目设计文档 v1.md。\n')
            self.create({'type': 'rule', 'name': 'verification', 'display_name': '交付验证规范', 'category': '工程规范', 'tags': ['测试', '交付']},
                        '# 交付验证\n\n- 完成与变更范围相符的检查。\n- 区分验证通过、未覆盖和受阻的部分。\n- 不将静态检查通过等同于完整业务验收。\n')
            settings = self.settings()
            settings['seeded'] = True
            self.store.commit({'data/settings.json': json_bytes(settings)})
