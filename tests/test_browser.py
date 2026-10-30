"""Real Chromium acceptance against a separate process and isolated data."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import httpx
import pytest
from playwright.sync_api import sync_playwright, expect

from app.resources import ResourceManager
from conftest import SKILL

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope='module')
def server(tmp_path_factory):
    root = tmp_path_factory.mktemp('browser')
    data = root / 'data'
    manager = ResourceManager(data)
    manager.seed()
    manager.save_settings(str(root / 'codex'))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    log = (root / 'server.log').open('w', encoding='utf-8')
    process_env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTEST_', 'NICEGUI_'))}
    process = subprocess.Popen([sys.executable, str(ROOT / 'main.py'), '--data-dir', str(data), '--port', str(port), '--no-open'], cwd=ROOT, env=process_env,
                               stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise AssertionError((root / 'server.log').read_text(encoding='utf-8'))
            try:
                if httpx.get(url + '/api/health', timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise AssertionError('Server startup timed out')
        server_state = {'url': url, 'root': root, 'data': data, 'port': port, 'process': process}
        yield server_state
    finally:
        active_process = locals().get('server_state', {}).get('process', process)
        active_process.terminate()
        active_process.wait(timeout=15)
        log.close()


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as pw:
        instance = pw.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context(viewport={'width': 1440, 'height': 1000}, locale='zh-CN')
    page = context.new_page()
    page.set_default_timeout(15000)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    yield page
    assert not errors, errors
    context.close()


def visit(page, server, view='overview'):
    page.goto(server['url'] + '/?view=' + view)
    page.get_by_text('LOCAL FIRST', exact=True).wait_for()


@pytest.mark.browser
def test_pages_load_desktop_and_mobile_without_js_errors(page, server):
    for view, title in [('overview', '把好用的经验，变成顺手的工具。'), ('skill', 'Skills'), ('prompt', 'Prompts'), ('tip', '开发技巧'),
                        ('rule', 'Rules'), ('tokens', 'Token 优化'), ('imports', '导入与审核'), ('sync', 'Codex 同步'), ('settings', '设置与数据')]:
        visit(page, server, view)
        expect(page.locator('.page-title')).to_have_text(title)
        expect(page.get_by_text('数据需要处理', exact=True)).to_have_count(0)
    visit(page, server)
    screenshots = ROOT / '.runtime' / 'screenshots'
    screenshots.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(screenshots / 'overview.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.reload()
    expect(page.locator('.page-title')).to_be_visible()
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth + 1')
    page.screenshot(path=str(screenshots / 'mobile.png'), full_page=True)


@pytest.mark.browser
def test_create_edit_search_and_restore_prompt(page, server):
    visit(page, server, 'prompt')
    page.get_by_role('button', name='新建 Prompts', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('名称', exact=True).fill('browser-prompt')
    dialog.get_by_label('显示名称', exact=True).fill('浏览器验收模板')
    dialog.get_by_label('分类', exact=True).fill('验收测试')
    dialog.get_by_label('标签（逗号分隔）', exact=True).fill('e2e, 测试')
    dialog.get_by_label('正文', exact=True).fill('# Acceptance\nHello {{name}}\n')
    dialog.get_by_role('button', name='保存资源', exact=True).click()
    page.get_by_text('浏览器验收模板', exact=True).wait_for()
    page.get_by_label('搜索名称、正文或标签', exact=True).fill('browser-prompt')
    expect(page.locator('.resource-card')).to_have_count(1)
    resource_card = page.locator('.resource-card').filter(has_text='browser-prompt')
    expect(resource_card).to_have_count(1)
    resource_card.get_by_role('button', name='使用模板', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog).to_contain_text('模板变量')
    dialog.get_by_label('name', exact=True).fill('Ada')
    dialog.get_by_role('button', name='渲染模板', exact=True).click()
    expect(dialog.get_by_label('渲染结果', exact=True)).to_have_value('# Acceptance\nHello Ada\n')
    page.keyboard.press('Escape')
    resource_card.get_by_role('button', name='编辑', exact=True).click()
    page.get_by_role('dialog').get_by_label('正文', exact=True).fill('# Edited\nChanged version\n')
    page.get_by_role('dialog').get_by_role('button', name='保存资源', exact=True).click()
    page.get_by_text('浏览器验收模板', exact=True).wait_for()
    resource_card = page.locator('.resource-card').filter(has_text='browser-prompt')
    resource_card.get_by_role('button', name='历史', exact=True).click()
    dialog = page.get_by_role('dialog')
    dialog.get_by_label('选择历史版本', exact=True).click()
    page.get_by_role('option').filter(has_text='创建').click()
    expect(dialog.locator('pre')).to_contain_text('Hello {{name}}')
    dialog.get_by_role('button', name='恢复此版本', exact=True).click()
    page.get_by_text('浏览器验收模板', exact=True).wait_for()
    manager = ResourceManager(server['data'])
    result = next(r for r in manager.list_resources() if r['name'] == 'browser-prompt')
    assert result['content'] == '# Acceptance\nHello {{name}}\n'
    assert len(manager.history(result['id'])) == 3


@pytest.mark.browser
def test_import_review_enable_and_sync_in_isolation(page, server):
    source = server['root'] / 'external'
    source.mkdir()
    (source / 'SKILL.md').write_bytes(SKILL.replace('test-skill', 'browser-imported').encode())
    (source / 'LICENSE').write_bytes(b'MIT')
    (source / 'references').mkdir()
    (source / 'references' / 'notes.md').write_bytes(b'preserve this reference')
    visit(page, server, 'imports')
    page.get_by_label('本地资源目录或 GitHub 仓库地址', exact=True).fill(str(source))
    page.get_by_role('button', name='导入待审核区', exact=True).click()
    page.get_by_text('browser-imported', exact=True).wait_for()
    page.get_by_role('button', name='查看与审核', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.get_by_role('button', name='批准导入', exact=True)).to_be_disabled()
    dialog.get_by_role('checkbox').click()
    expect(dialog.get_by_role('checkbox')).to_be_checked()
    dialog.get_by_role('button', name='批准导入', exact=True).click()
    page.get_by_text('没有待审核的资源。', exact=True).wait_for()
    manager = ResourceManager(server['data'])
    resource = next(r for r in manager.list_resources() if r['name'] == 'browser-imported')
    assert not manager.effective(resource['id'])
    visit(page, server, 'skill')
    resource_card = page.locator('.resource-card').filter(has_text='browser-imported')
    with page.expect_navigation(wait_until='load'):
        resource_card.get_by_role('switch').click()
    expect(page.locator('.resource-card').filter(has_text='browser-imported').get_by_role('switch')).to_be_checked()
    visit(page, server, 'sync')
    page.get_by_role('button', name='预览同步', exact=True).click()
    expect(page.get_by_role('button', name='确认同步', exact=True)).to_be_enabled()
    expect(page.get_by_text('browser-imported → 新建部署 · 启用 · 3 文件', exact=True)).to_be_visible()
    page.get_by_role('button', name='确认同步', exact=True).click()
    expect(page.get_by_role('button', name='确认同步', exact=True)).to_be_disabled()
    deployed = server['root'] / '.agents' / 'skills' / f'adc-{resource["id"]}'
    assert (deployed / 'references' / 'notes.md').read_bytes() == b'preserve this reference'
    config = (server['root'] / 'codex' / 'config.toml').read_text()
    assert 'enabled = true' in config and 'SKILL.md' in config


@pytest.mark.browser
def test_optimization_requires_preview_and_confirmation(page, server):
    manager = ResourceManager(server['data'])
    resource = manager.create({'type': 'tip', 'name': 'whitespace-test', 'display_name': '空白优化验收'}, '# Keep constraints   \n\n\nDo not remove this rule.\n')
    visit(page, server, 'tokens')
    row = page.locator('.q-card').filter(has=page.get_by_text('空白优化验收', exact=True))
    row.get_by_role('button', name='查看优化差异', exact=True).click()
    dialog = page.get_by_role('dialog')
    expect(dialog.locator('pre')).to_contain_text('Do not remove this rule.')
    assert manager.get(resource['id'])['content'] == resource['content']
    dialog.get_by_role('button', name='接受并保存', exact=True).click()
    page.get_by_text('空白优化验收', exact=True).wait_for()
    assert manager.get(resource['id'])['content'] == '# Keep constraints\n\nDo not remove this rule.\n'
    assert len(manager.history(resource['id'])) == 2


def test_local_access_host_check_and_single_instance(server):
    assert httpx.get(server['url'] + '/api/health').json()['storage'] == 'files'
    assert httpx.get(server['url'] + '/api/health', headers={'Host': 'evil.example'}).status_code == 400
    result = subprocess.run([sys.executable, str(ROOT / 'main.py'), '--data-dir', str(server['data']), '--port', str(server['port'] + 1), '--no-open'],
                            capture_output=True, timeout=15)
    assert result.returncode == 1
    assert '另一实例' in result.stderr.decode('utf-8', errors='replace') or result.stderr


@pytest.mark.browser
def test_process_restart_preserves_resources_states_and_history(page, server):
    manager = ResourceManager(server['data'])
    resource = next(r for r in manager.list_resources() if r['name'] == 'browser-imported')
    manager.update(resource['id'], {}, resource['content'] + '\nPersistent edit.\n')
    versions = len(manager.history(resource['id']))
    assert manager.effective(resource['id'])
    server['process'].terminate()
    server['process'].wait(timeout=15)
    env = {key: value for key, value in os.environ.items() if not key.startswith(('PYTEST_', 'NICEGUI_'))}
    process = subprocess.Popen([sys.executable, str(ROOT / 'main.py'), '--data-dir', str(server['data']), '--port', str(server['port']), '--no-open'],
                               cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    server['process'] = process
    for _ in range(100):
        try:
            if httpx.get(server['url'] + '/api/health', timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    else:
        raise AssertionError('Server did not restart')
    visit(page, server, 'skill')
    resource_card = page.locator('.resource-card').filter(has_text='browser-imported')
    expect(resource_card.get_by_role('switch')).to_be_checked()
    after = ResourceManager(server['data'])
    assert after.get(resource['id'])['content'].endswith('Persistent edit.\n')
    assert len(after.history(resource['id'])) == versions
