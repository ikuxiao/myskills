import io
import zipfile

import pytest

from app.resources import ResourceManager, parse_skill
from app.storage import AppError, ConflictError
from conftest import SKILL


def test_resources_and_states_survive_restart(manager, skill):
    manager.set_state(skill['id'], 'global', True)
    restarted = ResourceManager(manager.store.root)
    assert restarted.get(skill['id'])['content'] == SKILL
    assert restarted.effective(skill['id'])
    assert restarted.get(skill['id'])['files'] == ['SKILL.md', 'references/guide.md', 'scripts/example.py']


def test_denials_win_across_scopes_and_inheritance(manager, skill, tmp_path):
    rid = skill['id']
    project = tmp_path / 'project'
    assert not manager.effective(rid)
    manager.set_state(rid, 'project', True, project)
    assert not manager.effective(rid, project)
    manager.set_state(rid, 'global', True)
    assert manager.effective(rid, project)
    manager.set_state(rid, 'tool', False)
    assert not manager.effective(rid, project)
    manager.set_state(rid, 'tool', None)
    assert manager.effective(rid, project)
    manager.set_state(rid, 'project', False, project)
    assert not manager.effective(rid, project)
    assert manager.effective(rid)


def test_history_restores_metadata_main_and_auxiliary_files(manager, skill):
    initial = manager.history(skill['id'])[0]['id']
    manager.update(skill['id'], {'display_name': 'Changed'}, SKILL + '\nchanged')
    manager.write_file(skill['id'], 'references/guide.md', 'updated reference')
    manager.write_file(skill['id'], 'references/new.md', 'new reference')
    manager.set_state(skill['id'], 'global', True)
    result = manager.restore(skill['id'], initial)
    assert result['content'] == SKILL
    assert result['display_name'] == '测试 Skill'
    assert manager.read_file(skill['id'], 'references/guide.md') == b'original reference'
    assert 'references/new.md' not in result['files']
    assert manager.effective(skill['id'])
    assert len(manager.history(skill['id'])) == 5


def test_stale_editor_and_external_changes_are_detected(manager, skill):
    manager.update(skill['id'], {}, SKILL + '\nchanged')
    with pytest.raises(ConflictError):
        manager.update(skill['id'], {}, SKILL, expected_revision=skill['revision'])
    latest = manager.get(skill['id'])
    (manager.store.path(latest['source_path']) / 'references' / 'guide.md').write_text('external')
    with pytest.raises(ConflictError):
        manager.update(skill['id'], {}, SKILL, expected_revision=latest['revision'])


def test_filter_search_and_export(manager, skill):
    assert len(manager.list_resources(query='workflow', kind='skill', category='Python', tag='test')) == 1
    assert not manager.list_resources(query='absent')
    archive = zipfile.ZipFile(io.BytesIO(manager.export(skill['id'])))
    assert archive.read('test-skill/SKILL.md').decode() == SKILL
    assert archive.read('test-skill/scripts/example.py') == b'print("never run")'


@pytest.mark.parametrize('content', ['# Missing', '---\nname: Bad_Name\ndescription: hi\n---\ntext', '---\nname: okay\n---\nbody', '---\nname: [wrong]\ndescription: text\n---\nbody'])
def test_invalid_skills_rejected(content):
    with pytest.raises(AppError):
        parse_skill(content)


def test_malformed_state_is_not_reset(manager):
    path = manager.store.path('data/states.json')
    path.write_text('{"version":1,"global":[]}', encoding='utf-8')
    with pytest.raises(AppError):
        ResourceManager(manager.store.root)
    assert path.read_text().endswith('[]}')


def test_string_state_and_invalid_index_paths_are_rejected(manager, skill):
    import json
    path = manager.store.path('data/states.json')
    original = path.read_bytes()
    doc = json.loads(original)
    doc['global'][skill['id']] = 'false'
    path.write_text(json.dumps(doc), encoding='utf-8')
    with pytest.raises(AppError, match='字段'):
        manager.effective(skill['id'])
    path.write_bytes(original)
    index_path = manager.store.path('data/resources.json')
    index = json.loads(index_path.read_bytes())
    index['resources'][0]['source_path'] = 'data'
    index_path.write_text(json.dumps(index), encoding='utf-8')
    with pytest.raises(AppError, match='索引'):
        manager.get(skill['id'])


def test_history_can_recover_missing_resource_directory(manager, skill):
    import shutil
    version = manager.history(skill['id'])[0]['id']
    resource_dir = manager.store.path(skill['source_path'])
    assert resource_dir.is_relative_to(manager.store.root / 'library')
    shutil.rmtree(resource_dir)
    result = manager.restore(skill['id'], version)
    assert result['content'] == SKILL
    assert manager.read_file(skill['id'], 'references/guide.md') == b'original reference'


def test_all_resource_types_persist(manager):
    for kind in ('prompt', 'rule', 'tip', 'template'):
        r = manager.create({'name': kind, 'type': kind, 'tags': '测试, example'}, '# Example\n正文')
        assert r['tags'] == ['测试', 'example']
    assert len(manager.list_resources()) == 4
    assert len(ResourceManager(manager.store.root).list_resources()) == 4


def test_copy_preserves_attachments_but_stays_disabled(manager, skill):
    manager.set_state(skill['id'], 'global', True)
    copied = manager.copy(skill['id'])
    assert copied['id'] != skill['id']
    assert parse_skill(copied['content'])['name'] == 'test-skill-copy'
    assert manager.read_file(copied['id'], 'references/guide.md') == b'original reference'
    assert not manager.effective(copied['id'])
