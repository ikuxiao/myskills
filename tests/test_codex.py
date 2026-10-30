import base64
from pathlib import Path

import pytest
import tomlkit

import adapters.codex as module
from adapters.codex import CodexAdapter
from app.storage import AppError, ConflictError, json_bytes


def test_deploy_enable_disable_preserves_existing_config(manager, skill, tmp_path):
    target = tmp_path / 'codex'
    target.mkdir()
    existing = '# keep my comment\nmodel = "example-model"\n[features]\nweb_search = true\n\n[[skills.config]]\npath = "/some/unmanaged/SKILL.md"\nenabled = false\n'
    (target / 'config.toml').write_text(existing, encoding='utf-8')
    adapter = CodexAdapter(manager)
    plan = adapter.preview(str(target))
    assert not plan['conflicts']
    assert not plan['actions'][0]['enabled']
    adapter.sync(str(target), expected=plan['fingerprint'])
    deployed = Path(plan['actions'][0]['destination'])
    assert (deployed / 'SKILL.md').read_text() == skill['content']
    assert (deployed / 'references' / 'guide.md').read_bytes() == b'original reference'
    assert (deployed / 'scripts' / 'example.py').read_bytes() == b'print("never run")'
    doc = tomlkit.parse((target / 'config.toml').read_text())
    assert doc['model'] == 'example-model'
    assert doc['features']['web_search'] is True
    assert '# keep my comment' in (target / 'config.toml').read_text()
    assert doc['skills']['config'][0]['path'] == '/some/unmanaged/SKILL.md'
    managed = doc['skills']['config'][1]
    assert managed['path'] == (deployed / 'SKILL.md').as_posix()
    assert managed['enabled'] is False
    manager.set_state(skill['id'], 'global', True)
    adapter.sync(str(target))
    doc = tomlkit.parse((target / 'config.toml').read_text())
    assert doc['skills']['config'][1]['enabled'] is True
    manager.set_state(skill['id'], 'global', False)
    adapter.sync(str(target))
    assert (deployed / 'SKILL.md').exists()
    assert tomlkit.parse((target / 'config.toml').read_text())['skills']['config'][1]['enabled'] is False
    assert adapter.preview(str(target))['changed_files'] == 0


@pytest.mark.parametrize('mutation', ['main', 'aux', 'new', 'missing', 'config'])
def test_any_external_managed_change_blocks_sync(manager, skill, tmp_path, mutation):
    target = tmp_path / 'codex'
    adapter = CodexAdapter(manager)
    plan = adapter.preview(str(target))
    adapter.sync(str(target))
    directory = Path(plan['actions'][0]['destination'])
    if mutation == 'main':
        (directory / 'SKILL.md').write_text('external')
    elif mutation == 'aux':
        (directory / 'references' / 'guide.md').write_text('external')
    elif mutation == 'new':
        (directory / 'extra.txt').write_text('external')
    elif mutation == 'missing':
        (directory / 'scripts' / 'example.py').unlink()
    else:
        config = target / 'config.toml'
        config.write_text(config.read_text().replace('enabled = false', 'enabled = true'))
    assert adapter.preview(str(target))['conflicts']
    with pytest.raises(ConflictError):
        adapter.sync(str(target))


def test_preview_fingerprint_catches_changes_after_review(manager, skill, tmp_path):
    target = tmp_path / 'codex'
    target.mkdir()
    adapter = CodexAdapter(manager)
    plan = adapter.preview(str(target))
    (target / 'config.toml').write_text('model = "changed"\n')
    with pytest.raises(ConflictError, match='预览后'):
        adapter.sync(str(target), expected=plan['fingerprint'])
    assert (target / 'config.toml').read_text() == 'model = "changed"\n'
    plan = adapter.preview(str(target))
    manager.set_state(skill['id'], 'global', True)
    with pytest.raises(ConflictError, match='预览后'):
        adapter.sync(str(target), expected=plan['fingerprint'])


def test_unmanaged_collision_never_overwrites(manager, skill, tmp_path):
    target = tmp_path / 'codex'
    adapter = CodexAdapter(manager)
    plan = adapter.preview(str(target))
    destination = Path(plan['actions'][0]['destination'])
    destination.mkdir(parents=True)
    (destination / 'SKILL.md').write_text('user-owned')
    with pytest.raises(ConflictError):
        adapter.sync(str(target))
    assert (destination / 'SKILL.md').read_text() == 'user-owned'


def test_sync_write_failure_restores_config_and_files(manager, skill, tmp_path, monkeypatch):
    target = tmp_path / 'codex'
    target.mkdir()
    original_config = b'model = "keep"\n'
    (target / 'config.toml').write_bytes(original_config)
    adapter = CodexAdapter(manager)
    destination = Path(adapter.preview(str(target))['actions'][0]['destination'])
    original_write = module.atomic_write
    failed = False
    def fail_once(path, content):
        nonlocal failed
        if path.name == 'config.toml' and not failed:
            failed = True
            raise OSError('simulated no space')
        original_write(path, content)
    monkeypatch.setattr(module, 'atomic_write', fail_once)
    with pytest.raises(OSError):
        adapter.sync(str(target))
    assert (target / 'config.toml').read_bytes() == original_config
    assert not (destination / 'SKILL.md').exists()
    assert not (target / '.adc-manifest.json').exists()


def test_interrupted_sync_recovers_on_restart(manager, tmp_path):
    path = tmp_path / 'codex' / 'config.toml'
    path.parent.mkdir()
    path.write_bytes(b'partial')
    manager.store.commit({'data/sync_transaction.json': json_bytes({'phase': 'applying', 'before': {str(path): base64.b64encode(b'original').decode()}})})
    CodexAdapter(manager)
    assert path.read_bytes() == b'original'


def test_manual_sync_rollback_and_conflict(manager, skill, tmp_path):
    target = tmp_path / 'codex'
    adapter = CodexAdapter(manager)
    result = adapter.sync(str(target))
    manager.set_state(skill['id'], 'global', True)
    enabled = adapter.sync(str(target))
    adapter.rollback(enabled['id'])
    entries = tomlkit.parse((target / 'config.toml').read_text())['skills']['config']
    assert entries[0]['enabled'] is False
    # Rolling back the first sync is safe again after restoring its exact output.
    adapter.rollback(result['id'])
    assert not (target / 'config.toml').exists()
    fresh = adapter.sync(str(target))
    with (target / 'config.toml').open('a') as f:
        f.write('\n# external change\n')
    with pytest.raises(ConflictError):
        adapter.rollback(fresh['id'])


def test_project_deployment_and_disable_precedence(manager, skill, tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    manager.set_state(skill['id'], 'global', True)
    manager.set_state(skill['id'], 'project', False, str(project))
    adapter = CodexAdapter(manager)
    plan = adapter.preview(str(tmp_path / 'codex'), project=str(project))
    assert plan['skill_root'] == str(project / '.agents' / 'skills')
    assert not plan['actions'][0]['enabled']
    adapter.sync(str(tmp_path / 'codex'), project=str(project))
    assert Path(plan['actions'][0]['destination']).exists()


def test_source_update_propagates_and_history_removes_aux_file(manager, skill, tmp_path):
    target = str(tmp_path / 'codex')
    adapter = CodexAdapter(manager)
    plan = adapter.preview(target)
    adapter.sync(target)
    original_version = manager.history(skill['id'])[0]['id']
    manager.write_file(skill['id'], 'references/new.md', 'new')
    adapter.sync(target)
    destination = Path(plan['actions'][0]['destination'])
    assert (destination / 'references' / 'new.md').read_text() == 'new'
    manager.restore(skill['id'], original_version)
    adapter.sync(target)
    assert not (destination / 'references' / 'new.md').exists()


def test_invalid_toml_and_overlapping_data_paths_rejected(manager, skill, tmp_path):
    target = tmp_path / 'codex'
    target.mkdir()
    (target / 'config.toml').write_text('[[invalid')
    adapter = CodexAdapter(manager)
    with pytest.raises(AppError, match='格式'):
        adapter.preview(str(target))
    assert (target / 'config.toml').read_text() == '[[invalid'
    with pytest.raises(AppError, match='重叠'):
        adapter.preview(str(manager.store.root / 'codex'))
