"""Chinese local UI; all mutations go through the file service."""
from __future__ import annotations

import base64
import difflib
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nicegui import app, run, ui

from adapters.codex import CodexAdapter
from app.analysis import analyze, optimization_preview, render_prompt, variables
from app.importer import Importer
from app.resources import ResourceManager
from app.storage import AppError

LABELS = {'skill': 'Skills', 'prompt': 'Prompts', 'rule': 'Rules', 'tip': '开发技巧', 'template': '模板'}
NAV = [('overview', '总览', 'space_dashboard'), ('skill', 'Skills', 'auto_awesome'), ('prompt', 'Prompts', 'chat_bubble_outline'),
       ('rule', 'Rules', 'rule'), ('tip', '开发技巧', 'lightbulb'), ('tokens', 'Token 优化', 'data_usage'),
       ('imports', '导入与审核', 'move_to_inbox'), ('sync', 'Codex 同步', 'sync'), ('settings', '设置与数据', 'tune')]
CSS = '''
:root {--ink:#142e2c;--muted:#758581;--paper:#f5f7f5;--line:#e2e9e5;--green:#177b62;}
body {font-family:Inter,"Segoe UI","Microsoft YaHei",sans-serif;background:var(--paper);color:var(--ink);}
.q-page {background:var(--paper)} .q-drawer {background:#102c29!important;color:#d2e2da}
.q-header {background:#fff!important;color:var(--ink)!important;border-bottom:1px solid var(--line)}
.q-card {border-radius:14px!important;box-shadow:none!important;border:1px solid var(--line);}
.nav-item {display:flex;align-items:center;gap:12px;padding:12px 16px;border-radius:9px;color:#adc4ba;text-decoration:none;font-size:14px;}
.nav-item:hover {background:#24443d;color:#fff}.nav-item.active{background:#285547;color:#e4f5aa;}
.overline{font-size:11px;letter-spacing:2px;font-weight:600;color:var(--muted)}
.page-title{font-size:29px;font-weight:650;letter-spacing:-1px;line-height:1.4}
.muted{color:var(--muted)} .metric-number{font-size:35px;font-weight:650;letter-spacing:-1px}
.resource-title{font-size:17px;font-weight:600}.q-btn{text-transform:none;letter-spacing:0}
.q-field--outlined .q-field__control {border-radius:8px}
.q-dialog__inner>.q-card {max-height:92vh}.editor textarea{font-family:Consolas,monospace;font-size:13px;line-height:1.65}
.q-markdown {overflow-wrap:anywhere;max-width:100%}.q-markdown h1 {font-size:25px}.q-markdown h2 {font-size:20px}
.q-markdown pre{white-space:pre-wrap}.q-markdown img{max-width:100%}
.hero{background:#e9f1e7;border:1px solid #d6e4d1;border-radius:16px;padding:28px}
.section-label{font-size:17px;font-weight:600}.resource-card:hover{border-color:#b1cbbc}
.content-shell{width:100%;max-width:1440px;margin:0 auto;padding:30px 36px 52px}
@media(max-width:800px){.content-shell{padding:20px 16px}.page-title{font-size:25px}}
'''


def local_time(value):
    return datetime.fromisoformat(value).astimezone(timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S')


async def perform(fn, *args, **kwargs):
    try:
        return await run.io_bound(fn, *args, **kwargs)
    except (AppError, OSError, TimeoutError) as exc:
        ui.notify(str(exc), type='negative', position='top', timeout=8000, multi_line=True)
    except Exception:
        logging.exception('UI operation failed')
        ui.notify('操作未完成，请查看日志；原文件会保留或从事务恢复。', type='negative', timeout=8000)
    return None


def heading(title: str, subtitle: str, button=None):
    with ui.row().classes('w-full items-center justify-between gap-4 mb-6'):
        with ui.column().classes('gap-1'):
            ui.label(title).classes('page-title')
            ui.label(subtitle).classes('muted text-sm')
        if button:
            button()


def empty(message: str, icon='inventory_2'):
    with ui.column().classes('w-full items-center py-14 gap-3'):
        ui.icon(icon, size='40px').classes('text-gray-300')
        ui.label(message).classes('muted')


def register_ui(manager: ResourceManager):
    importer = Importer(manager)
    adapter = CodexAdapter(manager)

    @app.get('/api/health')
    def health():
        return {'status': 'ok', 'app': 'AI Dev Center', 'storage': 'files', 'version': '0.1.0'}

    def editor(resource_id=None, kind='skill'):
        try:
            resource = manager.get(resource_id) if resource_id else None
        except AppError as exc:
            ui.notify(str(exc), type='negative')
            return
        actual_kind = resource['type'] if resource else kind
        content = resource['content'] if resource else ('---\nname: my-skill\ndescription: 描述何时使用此 Skill。\n---\n\n# 我的 Skill\n\n在这里编写工作流程。\n' if actual_kind == 'skill' else '# 标题\n\n在这里编写内容。\n')
        with ui.dialog() as dialog, ui.card().classes('w-[1000px] max-w-[96vw] p-6 gap-4'):
            with ui.row().classes('w-full items-center justify-between'):
                ui.label(('编辑' if resource else '新建') + ' ' + LABELS[actual_kind]).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label=关闭编辑器')
            with ui.row().classes('w-full gap-4'):
                name = ui.input('名称', value=resource['name'] if resource else ('my-skill' if actual_kind == 'skill' else '')).props('outlined dense').classes('flex-1 min-w-48')
                display = ui.input('显示名称', value=resource['display_name'] if resource else '').props('outlined dense').classes('flex-1 min-w-48')
            with ui.row().classes('w-full gap-4'):
                category = ui.input('分类', value=resource['category'] if resource else '通用').props('outlined dense').classes('flex-1 min-w-48')
                tags = ui.input('标签（逗号分隔）', value=', '.join(resource['tags']) if resource else '').props('outlined dense').classes('flex-1 min-w-48')
            description = ui.input('用途说明', value=resource['description'] if resource else '').props('outlined dense').classes('w-full')
            if actual_kind == 'skill':
                ui.label('Skill 的名称和用途以正文 frontmatter 的 name / description 为准；附属文件请在资源卡片中打开。').classes('text-xs muted')
            with ui.tabs().classes('w-full') as tabs:
                edit_tab = ui.tab('Markdown 正文')
                preview_tab = ui.tab('预览')
            with ui.tab_panels(tabs, value=edit_tab).classes('w-full'):
                with ui.tab_panel(edit_tab).classes('p-0'):
                    body = ui.textarea('正文', value=content).props('outlined input-style="min-height:330px;max-height:55vh"').classes('w-full editor')
                with ui.tab_panel(preview_tab):
                    markdown = ui.markdown(content, sanitize=True).classes('w-full')
            tabs.on_value_change(lambda: markdown.set_content(body.value))
            async def save():
                data = {'type': actual_kind, 'name': name.value, 'display_name': display.value, 'category': category.value, 'tags': tags.value, 'description': description.value}
                if resource:
                    result = await perform(manager.update, resource['id'], data, body.value, expected_revision=resource['revision'])
                else:
                    result = await perform(manager.create, data, body.value)
                if result:
                    ui.notify('已保存，版本快照已记录。', type='positive')
                    dialog.close()
                    ui.navigate.reload()
            with ui.row().classes('w-full justify-end'):
                ui.button('取消', on_click=dialog.close).props('flat color=grey')
                ui.button('保存资源', icon='check', on_click=save).props('unelevated')
        dialog.open()

    def show_history(resource_id):
        resource = manager.get(resource_id)
        snapshots = manager.history(resource_id)
        with ui.dialog() as dialog, ui.card().classes('w-[1000px] max-w-[96vw] p-6'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('版本历史 · ' + resource['display_name']).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label=关闭历史')
            ui.label('回滚会创建新版本，并恢复正文、元数据和全部附属文件。启停状态单独保留。').classes('muted text-sm')
            selection = ui.select({s['id']: f'{local_time(s["at"])} · {s["action"]} · {s["id"][:8]}' for s in snapshots}, value=snapshots[0]['id'], label='选择历史版本').props('outlined').classes('w-full')
            comparison = ui.code('', language='diff').classes('w-full max-h-80 overflow-auto')
            def compare():
                snapshot = next(s for s in snapshots if s['id'] == selection.value)
                main = 'SKILL.md' if resource['type'] == 'skill' else 'content.md'
                before = base64.b64decode(snapshot['files'][main]).decode('utf-8')
                comparison.set_content(''.join(difflib.unified_diff(before.splitlines(keepends=True), resource['content'].splitlines(keepends=True), fromfile='所选历史版本', tofile='当前版本')) or '正文与当前版本一致。附属文件也会按快照恢复。')
            selection.on_value_change(compare)
            compare()
            async def restore():
                result = await perform(manager.restore, resource_id, selection.value, expected_revision=resource['revision'])
                if result:
                    ui.notify('已恢复历史版本。', type='positive')
                    ui.navigate.reload()
            ui.button('恢复此版本', icon='restore', on_click=restore).props('unelevated')
        dialog.open()

    def show_files(resource_id):
        resource = manager.get(resource_id)
        with ui.dialog() as dialog, ui.card().classes('w-[1000px] max-w-[96vw] p-6 gap-4'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('资源文件 · ' + resource['display_name']).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense aria-label=关闭文件')
            file_selection = ui.select(resource['files'], value=resource['files'][0], label='已有文件').props('outlined').classes('w-full')
            path_field = ui.input('文件相对路径', value=file_selection.value).props('outlined').classes('w-full')
            body = ui.textarea('文件内容').props('outlined input-style="min-height:350px"').classes('w-full editor')
            def load_file():
                path_field.value = file_selection.value
                try:
                    body.value = manager.read_file(resource_id, file_selection.value).decode('utf-8')
                    body.enable()
                except UnicodeError:
                    body.value = '此文件为二进制，可通过导出 ZIP 保留。'
                    body.disable()
            file_selection.on_value_change(load_file)
            load_file()
            ui.label('可填写新路径，如 references/example.md，再保存以新增辅助文件。脚本仅作为文本保存。').classes('muted text-sm')
            async def save():
                if not body.enabled:
                    return
                result = await perform(manager.write_file, resource_id, path_field.value, body.value, expected_revision=resource['revision'])
                if result:
                    ui.notify('文件已保存，完整快照已记录。', type='positive')
                    ui.navigate.reload()
            ui.button('保存文件', icon='save', on_click=save).props('unelevated')
        dialog.open()

    def prompt_preview(resource_id):
        resource = manager.get(resource_id)
        with ui.dialog() as dialog, ui.card().classes('w-[850px] max-w-[96vw] p-6 gap-4'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('模板变量 · ' + resource['display_name']).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            fields = {name: ui.input(name).props('outlined').classes('w-full') for name in variables(resource['content'])}
            output = ui.textarea('渲染结果', value=resource['content']).props('outlined readonly input-style="min-height:250px"').classes('w-full editor')
            def render():
                try:
                    output.value = render_prompt(resource['content'], {name: field.value for name, field in fields.items()})
                except AppError as exc:
                    ui.notify(str(exc), type='warning')
            with ui.row():
                ui.button('渲染模板', on_click=render).props('unelevated')
                ui.button('复制结果', icon='content_copy', on_click=lambda: ui.clipboard.write(output.value)).props('flat')
                ui.button('导出 Markdown', icon='download', on_click=lambda: ui.download(output.value.encode('utf-8'), f'{resource["name"]}.md')).props('flat')
        dialog.open()

    def optimize(resource_id):
        resource = manager.get(resource_id)
        preview = optimization_preview(resource['content'])
        with ui.dialog() as dialog, ui.card().classes('w-[1000px] max-w-[96vw] p-6 gap-4'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('优化预览 · ' + resource['display_name']).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            ui.label('仅清理行尾空格与连续空行，不删减规则或约束。接受后保留历史版本。').classes('muted')
            ui.label(f'估算 {preview["before_tokens"]:,} → {preview["after_tokens"]:,} tokens；非真实用量。')
            ui.code(preview['diff'] or '没有可清理的多余空白。', language='diff').classes('w-full max-h-96 overflow-auto')
            async def accept():
                result = await perform(manager.update, resource_id, {}, preview['after'], expected_revision=resource['revision'])
                if result:
                    ui.notify('优化已保存，可在版本历史中回滚。', type='positive')
                    ui.navigate.reload()
            with ui.row().classes('w-full justify-end'):
                ui.button('拒绝建议', on_click=dialog.close).props('flat')
                accept_button = ui.button('接受并保存', icon='check', on_click=accept).props('unelevated')
                if preview['before'] == preview['after']:
                    accept_button.disable()
        dialog.open()

    async def copy_resource(resource_id):
        if await perform(manager.copy, resource_id):
            ui.notify('已创建副本，默认禁用。', type='positive')
            ui.navigate.reload()

    def card(resource):
        resource_id = resource['id']
        with ui.card().classes('w-full p-5 gap-3 resource-card'):
            with ui.row().classes('w-full items-center justify-between no-wrap'):
                with ui.row().classes('items-center gap-3 no-wrap'):
                    ui.icon('auto_awesome' if resource['type'] == 'skill' else 'description', size='23px').classes('text-primary')
                    with ui.column().classes('gap-0'):
                        ui.label(resource['display_name']).classes('resource-title')
                        ui.label(resource['name']).classes('muted text-xs')
                if resource['type'] == 'skill':
                    async def toggle(event):
                        await perform(manager.set_state, resource_id, 'global', bool(event.value))
                        ui.navigate.reload()
                    ui.switch('全局启用', value=resource['enabled'], on_change=toggle).props('dense').classes('text-xs')
                else:
                    ui.badge('按需使用').props('outline color=grey')
            ui.label(resource['description'] or resource['content'].lstrip('# \n')[:100]).classes('muted text-sm line-clamp-2 min-h-10')
            with ui.row().classes('gap-2 items-center'):
                ui.badge(resource['category']).props('color=grey-2 text-color=grey-8')
                for tag in resource['tags'][:4]:
                    ui.badge('# ' + tag).props('outline color=grey-6')
            with ui.row().classes('w-full justify-between items-center text-xs muted'):
                ui.label(f'≈ {resource["estimated_tokens"]:,} tokens · {len(resource["files"])} 个文件')
                ui.label('本地创建' if resource['source'] == 'local' else '已审核导入')
            ui.separator()
            with ui.row().classes('w-full items-center gap-1'):
                ui.button('编辑', icon='edit_note', on_click=lambda: editor(resource_id)).props('flat dense')
                ui.button('历史', icon='history', on_click=lambda: show_history(resource_id)).props('flat dense color=grey-7')
                if resource['type'] in ('prompt', 'template'):
                    ui.button('使用模板', icon='play_arrow', on_click=lambda: prompt_preview(resource_id)).props('flat dense')
                ui.space()
                with ui.button(icon='more_horiz').props('flat round dense color=grey-7 aria-label=更多操作'):
                    with ui.menu():
                        ui.menu_item('附属文件', on_click=lambda: show_files(resource_id))
                        ui.menu_item('复制正文', on_click=lambda: ui.clipboard.write(resource['content']))
                        ui.menu_item('创建副本', on_click=lambda: copy_resource(resource_id))
                        ui.menu_item('导出 ZIP', on_click=lambda: ui.download(manager.export(resource_id), f'{resource["name"]}.zip'))
                        ui.menu_item('优化预览', on_click=lambda: optimize(resource_id))

    def overview():
        resources = manager.list_resources()
        report = analyze(resources)
        pending = importer.list_pending()
        heading('把好用的经验，变成顺手的工具。', '统一管理 AI 开发资源，让每次任务的上下文更聚焦。', lambda: ui.button('新建资源', icon='add', on_click=lambda: editor(kind='skill')).props('unelevated'))
        with ui.element('div').classes('hero w-full mb-6'):
            with ui.row().classes('w-full justify-between items-center gap-5'):
                with ui.column().classes('gap-2'):
                    ui.label('YOUR LOCAL AI WORKSPACE').classes('overline')
                    ui.label('资源有序，开发有据。').classes('text-2xl font-semibold')
                    ui.label('本地文件存储 · 完整版本快照 · 审核后启用').classes('text-sm text-green-900')
                ui.button('查看 Codex 同步', icon='arrow_forward', on_click=lambda: ui.navigate.to('/?view=sync')).props('outline')
        skills = [r for r in resources if r['type'] == 'skill']
        metrics = [('资源总数', len(resources), 'folder_open', 'Skills / Prompts / Rules / 技巧'),
                   ('已启用 Skills', sum(r['enabled'] for r in skills), 'toggle_on', f'共 {len(skills)} 个，变更后需同步'),
                   ('静态 Token 估算', f'{report["estimated_tokens"]:,}', 'data_usage', '资源正文合计，非实际上下文'),
                   ('待审核导入', len(pending), 'fact_check', '审核通过后仍默认禁用')]
        with ui.element('div').classes('grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-4 w-full mb-7'):
            for title, value, icon, note in metrics:
                with ui.card().classes('p-5 gap-1'):
                    with ui.row().classes('w-full justify-between items-center'):
                        ui.label(title).classes('muted text-sm')
                        ui.icon(icon, size='20px').classes('text-primary')
                    ui.label(str(value)).classes('metric-number')
                    ui.label(note).classes('muted text-xs')
        with ui.element('div').classes('grid grid-cols-1 xl:grid-cols-3 gap-6 w-full'):
            with ui.column().classes('xl:col-span-2 w-full gap-4'):
                with ui.row().classes('w-full justify-between items-center'):
                    ui.label('最近更新的资源').classes('section-label')
                    ui.link('全部 Skills →', '/?view=skill').classes('text-primary text-sm no-underline')
                with ui.element('div').classes('grid grid-cols-1 md:grid-cols-2 gap-4 w-full'):
                    for resource in resources[:4]:
                        card(resource)
                if not resources:
                    empty('资源库还是空的，从创建或导入开始。')
            with ui.column().classes('w-full gap-4'):
                ui.label('工作区动态').classes('section-label')
                with ui.card().classes('w-full p-5 gap-4'):
                    ui.label('优化提示').classes('font-semibold')
                    ui.label(f'{len(report["duplicates"])} 组重复正文 · {len(report["long_resources"])} 个过长资源').classes('muted text-sm')
                    ui.label('Skill 只在相关任务中加载。把长篇示例放到 references/，让全局规则保持简洁。').classes('text-sm leading-6')
                    ui.link('进入 Token 优化 →', '/?view=tokens').classes('text-primary no-underline text-sm')
                with ui.card().classes('w-full p-5 gap-4'):
                    ui.label('最近记录').classes('font-semibold')
                    for event in manager.store.events(5):
                        with ui.column().classes('gap-1 w-full'):
                            ui.label(event['action'] + (' · ' + event['name'] if event.get('name') else '')).classes('text-sm')
                            ui.label(local_time(event['at']) + ' UTC+8').classes('muted text-xs')

    def resource_page(kind):
        heading(LABELS[kind], '可搜索、可编辑、可追溯的本地资源。' + ('启停变更需到 Codex 同步页应用。' if kind == 'skill' else ''),
                lambda: ui.button('新建 ' + LABELS[kind], icon='add', on_click=lambda: editor(kind=kind)).props('unelevated'))
        all_resources = manager.list_resources(kind=kind)
        with ui.row().classes('w-full items-center gap-4 mb-5'):
            search = ui.input('搜索名称、正文或标签').props('outlined dense clearable').classes('flex-1 min-w-64')
            with search.add_slot('prepend'):
                ui.icon('search')
            categories = sorted({r['category'] for r in all_resources})
            category = ui.select(['全部分类', *categories], value='全部分类', label='分类筛选').props('outlined dense').classes('w-44')
            tags = sorted({tag for r in all_resources for tag in r['tags']})
            tag = ui.select(['全部标签', *tags], value='全部标签', label='标签筛选').props('outlined dense').classes('w-44')
        count = ui.label().classes('muted text-xs mb-3')
        @ui.refreshable
        def listing():
            found = manager.list_resources(query=search.value or '', kind=kind, category=category.value if category.value != '全部分类' else None, tag=tag.value if tag.value != '全部标签' else None)
            count.set_text(f'{len(found)} 个资源 · 正文和附属文件均保存在本机')
            if not found:
                empty('没有匹配的资源，可以调整筛选或新建资源。')
            with ui.element('div').classes('grid grid-cols-1 md:grid-cols-2 2xl:grid-cols-3 gap-4 w-full'):
                for resource in found:
                    card(resource)
        search.on_value_change(listing.refresh)
        category.on_value_change(listing.refresh)
        tag.on_value_change(listing.refresh)
        listing()

    def tokens_page():
        resources = manager.list_resources()
        report = analyze(resources)
        lookup = {r['id']: r['display_name'] for r in resources}
        heading('Token 优化', '找出重复与过长的内容，每项优化先预览，再由你决定。')
        with ui.card().classes('w-full p-5 mb-6 bg-green-50'):
            ui.label(f'资源正文合计 ≈ {report["estimated_tokens"]:,} tokens').classes('text-xl font-semibold')
            ui.label(report['notice']).classes('muted text-sm')
        with ui.element('div').classes('grid grid-cols-1 lg:grid-cols-2 gap-5 w-full mb-6'):
            with ui.card().classes('p-5 w-full gap-3'):
                ui.label('重复正文与名称').classes('section-label')
                if not report['duplicates'] and not report['duplicate_names']:
                    ui.label('暂未发现重复正文或同名资源。').classes('muted text-sm')
                for ids in report['duplicates']:
                    ui.label('重复正文：' + '、'.join(lookup[i] for i in ids)).classes('text-sm')
                for ids in report['duplicate_names']:
                    ui.label('重复名称：' + '、'.join(lookup[i] for i in ids)).classes('text-sm')
                for passage in report['repeated_passages'][:8]:
                    with ui.expansion('重复段落 · ' + '、'.join(lookup[i] for i in passage['ids'])).classes('w-full'):
                        ui.label(passage['text']).classes('text-sm')
            with ui.card().classes('p-5 w-full gap-3'):
                ui.label('过长资源').classes('section-label')
                if not report['long_resources']:
                    ui.label('当前正文长度适中。').classes('muted text-sm')
                for item in report['long_resources']:
                    ui.label(f'{lookup[item["id"]]} · ≈ {item["tokens"]:,} tokens').classes('font-semibold')
                    ui.label(item['reason']).classes('muted text-sm')
        ui.label('逐项检查与优化').classes('section-label mb-3')
        for resource in sorted(resources, key=lambda r: r['estimated_tokens'], reverse=True):
            with ui.card().classes('w-full px-5 py-3 mb-2'):
                with ui.row().classes('w-full justify-between items-center'):
                    with ui.column().classes('gap-0'):
                        ui.label(resource['display_name']).classes('font-semibold')
                        ui.label(f'{len(resource["content"]):,} 字符 · {resource["bytes"]:,} 字节 · ≈ {resource["estimated_tokens"]:,} tokens').classes('muted text-xs')
                    ui.button('查看优化差异', icon='difference', on_click=lambda rid=resource['id']: optimize(rid)).props('flat')

    def review_dialog(entry):
        try:
            files = importer.files(entry['id'])
        except AppError as exc:
            ui.notify(str(exc), type='negative')
            return
        with ui.dialog() as dialog, ui.card().classes('w-[1000px] max-w-[96vw] p-6 gap-4'):
            with ui.row().classes('w-full justify-between items-center'):
                ui.label('审核 · ' + entry['name']).classes('text-xl font-semibold')
                ui.button(icon='close', on_click=dialog.close).props('flat round dense')
            ui.label('来源：' + entry['source']).classes('text-sm break-all')
            if entry.get('commit'):
                ui.label('Git commit：' + entry['commit']).classes('text-xs muted')
            ui.label('许可证文件：' + ('、'.join(entry['licenses']) or '未找到，请核实授权')).classes('text-sm')
            ui.label('扫描只能提示风险，不能保证内容安全。请检查所有文件；审核不会运行任何脚本。').classes('text-sm text-orange-800')
            for finding in entry['findings']:
                ui.label(f'[{finding["type"]}] {finding["file"]}：{finding["message"]}').classes('text-xs text-orange-800 break-all')
            selection = ui.select(list(files), value='SKILL.md' if entry['type'] == 'skill' else 'content.md', label='文件清单与内容').props('outlined').classes('w-full')
            preview = ui.code('', language='markdown').classes('w-full max-h-72 overflow-auto')
            def load():
                preview.set_content(files[selection.value].decode('utf-8', errors='replace'))
            selection.on_value_change(load)
            load()
            acknowledgement = ui.checkbox('我已检查文件内容、风险提示与许可证，同意加入资源库（保持禁用）')
            async def approve():
                if not acknowledgement.value:
                    ui.notify('请先确认已完成审核。', type='warning')
                    return
                if await perform(importer.review, entry['id'], True, expected=entry['fingerprint']):
                    ui.notify('已加入资源库，当前为禁用状态。', type='positive')
                    ui.navigate.reload()
            async def reject():
                if await perform(importer.review, entry['id'], False, expected=entry['fingerprint']):
                    ui.notify('已拒绝，原始文件与记录保留。')
                    ui.navigate.reload()
            with ui.row().classes('w-full justify-end'):
                ui.button('拒绝导入', on_click=reject).props('flat color=grey')
                ui.button('批准导入', icon='verified_user', on_click=approve).props('unelevated').bind_enabled_from(acknowledgement, 'value')
        dialog.open()

    def imports_page():
        heading('导入与审核', '发现好资源，先审查内容和来源，再加入自己的工具箱。')
        with ui.card().classes('p-6 w-full mb-6 gap-4'):
            ui.label('从本地或公开 GitHub 仓库导入').classes('section-label')
            source = ui.input('本地资源目录或 GitHub 仓库地址', placeholder=r'D:\resources\my-skill 或 https://github.com/owner/repo').props('outlined').classes('w-full')
            with ui.row().classes('w-full gap-4'):
                kind = ui.select(LABELS, value='skill', label='资源类型').props('outlined').classes('w-48')
                subdirectory = ui.input('仓库内目录（可选）', placeholder='skills/example').props('outlined').classes('flex-1')
            async def stage():
                import_button.disable()
                try:
                    result = await perform(importer.stage, source.value, kind.value, subdirectory=subdirectory.value or '')
                    if result:
                        ui.notify('已进入待审核区，没有自动启用。', type='positive')
                        ui.navigate.reload()
                finally:
                    import_button.enable()
            import_button = ui.button('导入待审核区', icon='download', on_click=stage).props('unelevated')
            ui.label('单个资源 ≤ 500 文件 / 20 MB，单文件 ≤ 2 MB。远程导入需要 Git；无需大模型 API。').classes('muted text-xs')
        with ui.expansion('扫描已有 Skills 目录', icon='folder_open').classes('w-full mb-6 bg-white rounded-xl border'):
            path = ui.input('扫描目录', value=str(Path.home() / '.agents' / 'skills')).props('outlined dense').classes('w-full')
            scan_results = ui.column().classes('w-full')
            async def scan():
                result = await perform(importer.scan, path.value)
                if result is None:
                    return
                scan_results.clear()
                with scan_results:
                    ui.label(f'发现 {len(result["skills"])} 个 Skill；{len(result["errors"])} 个格式问题。').classes('text-sm')
                    for skill in result['skills']:
                        async def add(item=skill):
                            if await perform(importer.stage, item['path'], 'skill'):
                                ui.notify('已导入待审核区。', type='positive')
                                ui.navigate.reload()
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label(skill['name']).classes('text-sm')
                            ui.button('送审', on_click=add).props('flat dense')
                    for error in result['errors']:
                        ui.label(error['path'] + '：' + error['error']).classes('text-xs text-orange-800')
                    if result['truncated']:
                        ui.label('仅扫描前 500 个目录，请缩小范围。').classes('text-xs')
            ui.button('扫描目录', icon='search', on_click=scan).props('outline')
        entries = importer.list_pending()
        ui.label(f'待审核 · {len(entries)}').classes('section-label mb-4')
        for entry in entries:
            with ui.card().classes('w-full p-5 mb-3'):
                with ui.row().classes('w-full justify-between items-center'):
                    with ui.column().classes('gap-1'):
                        ui.label(entry['name']).classes('resource-title')
                        ui.label(entry['source']).classes('muted text-sm break-all')
                        ui.label(f'{len(entry["files"])} 个文件 · {len(entry["findings"])} 项检查提示 · 默认禁用').classes('text-xs text-orange-800')
                    ui.button('查看与审核', icon='fact_check', on_click=lambda item=entry: review_dialog(item)).props('outline')
        if not entries:
            empty('没有待审核的资源。', 'verified_user')

    def sync_page():
        settings = manager.settings()
        heading('Codex 同步', '先查看变更与冲突，再将资源部署到工具原生目录。')
        with ui.card().classes('w-full p-6 mb-5 gap-3'):
            ui.label('同步目标').classes('section-label')
            target = ui.input('Codex 配置目录', value=settings['codex_dir']).props('outlined').classes('w-full')
            project = ui.input('项目目录（留空为用户全局）', value=settings.get('project', '')).props('outlined').classes('w-full')
            ui.label('全局资源写入配置目录同级的 .agents/skills；项目资源写入项目的 .agents/skills。原配置会先备份。').classes('muted text-sm')
            ui.label('原生启停维护 config.toml 的 [[skills.config]]。同步完成后需要重新加载 Codex 会话。').classes('muted text-sm')
            state = {'plan': None}
            output = ui.column().classes('w-full')
            async def preview():
                result = await perform(adapter.preview, target.value, project.value or None)
                if result is None:
                    return
                state['plan'] = result
                output.clear()
                with output:
                    ui.label('部署目录：' + result['skill_root']).classes('text-sm break-all')
                    ui.label(f'共 {len(result["actions"])} 个 Skill · {result["changed_files"]} 个文件变更').classes('font-semibold')
                    for item in result['actions']:
                        ui.label(f'{item["name"]} → {item["action"]} · {"启用" if item["enabled"] else "禁用"} · {item["files"]} 文件').classes('text-sm')
                    for conflict in result['conflicts']:
                        ui.label('冲突：' + conflict).classes('text-red-700 text-sm')
                    ui.label(result['notice']).classes('text-sm text-orange-800')
                    if result['manual_resources']:
                        ui.label('需手动复制/导出：' + '、'.join(result['manual_resources'])).classes('text-xs muted')
                confirm.set_enabled(not result['conflicts'])
            async def sync():
                plan = state['plan']
                if not plan:
                    return
                if str(Path(target.value).expanduser().resolve()) != plan['target'] or (project.value or '') != plan['project']:
                    ui.notify('路径已更改，请重新预览。', type='warning')
                    confirm.disable()
                    return
                result = await perform(adapter.sync, target.value, project.value or None, expected=plan['fingerprint'])
                if result:
                    ui.notify(f'同步完成：{result["changed_files"]} 个文件。请重新加载 Codex 会话。', type='positive', timeout=7000)
                    confirm.disable()
                    state['plan'] = None
            with ui.row():
                ui.button('预览同步', icon='preview', on_click=preview).props('outline')
                confirm = ui.button('确认同步', icon='sync', on_click=sync).props('unelevated')
                confirm.disable()
        resources = manager.list_resources(kind='skill')
        if resources:
            with ui.expansion('三级启停设置：任一级显式禁用优先', icon='account_tree').classes('w-full bg-white rounded-xl border mb-5'):
                ui.label('全局开关在 Skill 资源卡片上。下级“继承”沿用上级；下级启用不能覆盖上级显式禁用。').classes('muted text-sm')
                states = manager._document('data/states.json')
                for resource in resources:
                    rid = resource['id']
                    with ui.row().classes('w-full items-center gap-4'):
                        ui.label(resource['display_name']).classes('w-48 text-sm')
                        options = {'inherit': '继承', 'on': '启用', 'off': '禁用'}
                        old_tool = states['tools'].get('codex', {}).get(rid)
                        tool = ui.select(options, value='inherit' if old_tool is None else 'on' if old_tool else 'off', label='Codex 工具级').props('outlined dense').classes('w-40')
                        async def save_tool(event, resource_id=rid):
                            await perform(manager.set_state, resource_id, 'tool', {'inherit': None, 'on': True, 'off': False}[event.value])
                            confirm.disable()
                        tool.on_value_change(save_tool)
                        project_key = manager.project_key(project.value) if project.value else ''
                        old_project = states['projects'].get(project_key, {}).get(rid)
                        project_state = ui.select(options, value='inherit' if old_project is None else 'on' if old_project else 'off', label='当前项目级').props('outlined dense').classes('w-40')
                        async def save_project(event, resource_id=rid):
                            await perform(manager.set_state, resource_id, 'project', {'inherit': None, 'on': True, 'off': False}[event.value], project=project.value)
                            confirm.disable()
                        project_state.on_value_change(save_project)
        records = adapter.records()[:8]
        ui.label('同步与回滚记录').classes('section-label mb-3')
        for record in records:
            with ui.card().classes('w-full px-5 py-3 mb-2'):
                with ui.row().classes('w-full items-center justify-between'):
                    ui.label(f'{record["action"]} · {record["files"]} 文件 · {local_time(record["at"])} UTC+8').classes('text-sm')
                    async def undo(sync_id=record['id']):
                        if await perform(adapter.rollback, sync_id):
                            ui.notify('同步已回滚，请重新加载 Codex 会话。', type='positive')
                            ui.navigate.reload()
                    ui.button('回滚本次同步', icon='undo', on_click=undo).props('flat dense')
        if not records:
            ui.label('尚未执行同步。').classes('muted text-sm')

    def settings_page():
        settings = manager.settings()
        heading('设置与数据', '正文是 Markdown，状态是 JSON，数据始终属于你。')
        with ui.card().classes('w-full p-6 gap-4 mb-5'):
            ui.label('默认同步设置').classes('section-label')
            codex = ui.input('默认 Codex 配置目录', value=settings['codex_dir']).props('outlined').classes('w-full')
            project = ui.input('默认项目目录（可留空）', value=settings.get('project', '')).props('outlined').classes('w-full')
            async def save():
                try:
                    await run.io_bound(manager.save_settings, codex.value, project.value or '')
                    ui.notify('设置已保存。', type='positive')
                except (AppError, OSError) as exc:
                    ui.notify(str(exc), type='negative')
            ui.button('保存设置', icon='save', on_click=save).props('unelevated')
        with ui.card().classes('w-full p-6 gap-3'):
            ui.label('本地数据目录').classes('section-label')
            ui.code(str(manager.store.root), language='text').classes('w-full')
            ui.label('library/ 资源正文 · data/ 索引和状态 · history/ 完整版本 · staging/ 待审核原件 · backups/ 配置与同步备份').classes('text-sm muted')
            ui.label('关闭程序后，复制整个数据目录即可备份。索引损坏时程序会停止写入，请参照 README 的恢复步骤。').classes('text-sm')
            ui.label('首版边界：支持 Codex Skill 自动同步；其他资源手动复制/导出。远程仓库可直接导入，GitHub 搜索与更新检查属于后续迭代。').classes('text-sm muted')

    @ui.page('/')
    def index(view: str = 'overview'):
        ui.colors(primary='#177b62', secondary='#9eb883', accent='#d4eb98', positive='#177b62')
        ui.add_css(CSS)
        with ui.left_drawer(value=None).props('width=238 breakpoint=1000 show-if-above bordered').classes('p-4') as drawer:
            with ui.row().classes('items-center gap-3 px-3 py-5 mb-5'):
                with ui.element('div').classes('w-9 h-9 bg-lime-200 rounded-xl flex items-center justify-center text-green-950'):
                    ui.icon('hub', size='23px')
                with ui.column().classes('gap-0'):
                    ui.label('AI Dev Center').classes('text-lg font-semibold text-white')
                    ui.label('开发效率与资源管理中心').classes('text-[10px] text-green-100 opacity-60')
            ui.label('WORKSPACE').classes('overline px-4 mb-2 text-green-100 opacity-40')
            for key, label, icon in NAV:
                with ui.link(target=f'/?view={key}').classes('nav-item w-full' + (' active' if view == key else '')):
                    ui.icon(icon, size='20px')
                    ui.label(label)
            ui.space()
            with ui.column().classes('px-4 py-4 gap-2 text-xs'):
                ui.label('● 本机运行').classes('text-lime-200')
                ui.label('文件存储 / 无数据库 / v0.1.0').classes('opacity-40')
        with ui.header().classes('h-16 px-6 items-center gap-3'):
            ui.button(icon='menu', on_click=drawer.toggle).props('flat round color=grey-7 aria-label=切换导航')
            ui.label('工作区 / ' + next((label for key, label, _ in NAV if key == view), '总览')).classes('text-sm')
            ui.space()
            ui.badge('LOCAL FIRST').props('outline color=primary').classes('text-[10px] tracking-widest')
            ui.button('导入资源', icon='add', on_click=lambda: ui.navigate.to('/?view=imports')).props('flat dense').classes('ml-4')
        with ui.column().classes('content-shell gap-0'):
            try:
                if view in LABELS:
                    resource_page(view)
                elif view == 'tokens':
                    tokens_page()
                elif view == 'imports':
                    imports_page()
                elif view == 'sync':
                    sync_page()
                elif view == 'settings':
                    settings_page()
                else:
                    overview()
            except (AppError, OSError) as exc:
                ui.label('数据需要处理').classes('page-title')
                ui.label(str(exc)).classes('text-red-700')
                ui.label('原文件已保留，请查看数据目录中的 backups 和 README 恢复说明。').classes('muted')
