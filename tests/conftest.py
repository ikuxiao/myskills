from pathlib import Path

import pytest

from app.resources import ResourceManager

SKILL = '---\nname: test-skill\ndescription: A focused development skill.\n---\n\n# Test\n\nUse this workflow.\n'


@pytest.fixture
def manager(tmp_path):
    return ResourceManager(tmp_path / 'data')


@pytest.fixture
def skill(manager):
    return manager.create({'type': 'skill', 'name': 'test-skill', 'display_name': '测试 Skill', 'category': 'Python', 'tags': ['test']}, SKILL,
                          {'references/guide.md': b'original reference', 'scripts/example.py': b'print("never run")'})


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / 'source'
    directory.mkdir()
    (directory / 'SKILL.md').write_text(SKILL, encoding='utf-8')
    (directory / 'LICENSE').write_text('MIT\n', encoding='utf-8')
    (directory / 'scripts').mkdir()
    (directory / 'scripts' / 'external.py').write_text('raise RuntimeError("must never execute")', encoding='utf-8')
    return directory
