from types import SimpleNamespace

import pytest

from app.importer import Importer, collect_files, security_report
from app.resources import MAX_FILE_BYTES
from app.storage import AppError, ConflictError
from conftest import SKILL


def test_scan_stage_review_preserves_files_without_execution(manager, source):
    importer = Importer(manager)
    assert importer.scan(str(source.parent))['skills'][0]['name'] == 'test-skill'
    pending = importer.stage(str(source), 'skill')
    assert manager.list_resources() == []
    assert len(importer.list_pending()) == 1
    assert pending['licenses'] == ['LICENSE']
    assert any(f['type'] == 'script' for f in pending['findings'])
    resource = importer.review(pending['id'], True, expected=pending['fingerprint'])
    assert not manager.effective(resource['id'])
    assert resource['provenance']['source'] == str(source.resolve())
    assert manager.read_file(resource['id'], 'scripts/external.py').startswith(b'raise RuntimeError')
    assert not importer.list_pending()
    with pytest.raises(AppError):
        importer.review(pending['id'], True)


def test_rejection_keeps_original_and_audit(manager, source):
    importer = Importer(manager)
    staged = importer.stage(str(source))
    importer.review(staged['id'], False)
    assert not manager.list_resources()
    assert importer.files(staged['id'])['SKILL.md'] == (source / 'SKILL.md').read_bytes()
    assert importer._index()['imports'][0]['status'] == 'rejected'


def test_tampering_invalidates_approval(manager, source):
    importer = Importer(manager)
    staged = importer.stage(str(source))
    manager.store.path(f'staging/imported/{staged["id"]}/scripts/external.py').write_text('tampered')
    with pytest.raises(ConflictError):
        importer.review(staged['id'], True)
    assert not manager.list_resources()


def test_original_bom_bytes_are_preserved_in_quarantine(manager, source):
    original = b'\xef\xbb\xbf' + SKILL.encode('utf-8')
    (source / 'SKILL.md').write_bytes(original)
    importer = Importer(manager)
    staged = importer.stage(str(source))
    assert importer.files(staged['id'])['SKILL.md'] == original
    resource = importer.review(staged['id'], True)
    assert resource['content'] == SKILL
    assert importer.files(staged['id'])['SKILL.md'] == original


def test_oversized_file_rejected_before_read(manager, source):
    (source / 'large').write_bytes(b'x' * (MAX_FILE_BYTES + 1))
    with pytest.raises(AppError, match='限制'):
        Importer(manager).stage(str(source))


def test_symlink_rejected(tmp_path):
    source = tmp_path / 'source'
    source.mkdir()
    outside = tmp_path / 'secret'
    outside.write_text('private')
    try:
        (source / 'link').symlink_to(outside)
    except OSError:
        pytest.skip('Windows symlink privilege unavailable; junction is tested separately')
    with pytest.raises(AppError):
        collect_files(source)


def test_junction_rejected_on_windows(tmp_path):
    import os
    import subprocess
    if os.name != 'nt':
        pytest.skip('Windows junction')
    source = tmp_path / 'source'
    source.mkdir()
    target = tmp_path / 'target'
    target.mkdir()
    junction = source / 'linked'
    # This only creates a directory entry inside pytest's isolated temp folder.
    result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(junction), str(target)], capture_output=True)
    assert result.returncode == 0
    try:
        with pytest.raises(AppError):
            collect_files(source)
    finally:
        junction.rmdir()


@pytest.mark.parametrize('url', ['http://github.com/a/b', 'https://evil.example/a/b', 'https://user:secret@github.com/a/b', 'https://github.com/a/b?token=secret', 'file:///tmp/repo', 'https://github.com/a/b/tree/main'])
def test_unsafe_remote_sources_rejected(manager, url):
    with pytest.raises(AppError):
        Importer(manager).stage(url)


def test_public_github_flow_pins_commit_and_inherits_root_license(manager, monkeypatch):
    import app.importer as module
    def fake_run(args, **kwargs):
        if 'clone' in args:
            checkout = module.Path(args[-1])
            (checkout / 'skills' / 'example').mkdir(parents=True)
            (checkout / 'skills' / 'example' / 'SKILL.md').write_text(SKILL, encoding='utf-8')
            (checkout / 'LICENSE').write_text('MIT', encoding='utf-8')
            assert args[1:5] == ['-c', 'core.hooksPath=NUL', '-c', 'protocol.file.allow=never']
            return SimpleNamespace(returncode=0, stderr='', stdout='')
        return SimpleNamespace(returncode=0, stdout='a' * 40)
    monkeypatch.setattr(module.subprocess, 'run', fake_run)
    staged = Importer(manager).stage('https://github.com/example/repo', subdirectory='skills/example')
    assert staged['commit'] == 'a' * 40
    assert staged['licenses'] == ['LICENSE']


def test_single_markdown_prompt_import(manager, tmp_path):
    directory = tmp_path / 'prompt'
    directory.mkdir()
    (directory / 'review.md').write_text('# Review\n{{project}}', encoding='utf-8')
    importer = Importer(manager)
    staged = importer.stage(str(directory), 'prompt')
    resource = importer.review(staged['id'], True)
    assert resource['content'] == (directory / 'review.md').read_bytes().decode('utf-8')


def test_risk_report_flags_commands_without_printing_secret():
    report = security_report({'danger.ps1': b'Remove-Item -Recurse X; curl https://example.org', '.env': b'api_key="sk-this-is-a-fake-test-token-123456"'})
    assert {'danger', 'network', 'script', 'secret'} <= {item['type'] for item in report}
    assert 'sk-this' not in str(report)
