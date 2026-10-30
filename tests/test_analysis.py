import pytest

from app.analysis import analyze, estimate_tokens, optimization_preview, render_prompt, variables
from app.storage import AppError


def test_estimate_distinguishes_cjk_and_empty():
    assert estimate_tokens('') == 0
    assert estimate_tokens('中文') == 3
    assert estimate_tokens('abcdefgh') == 2


def test_variable_render_reports_missing_values():
    assert variables('{{ name }} {{name}} {{project.path}}') == ['name', 'project.path']
    assert render_prompt('Hello {{ name }}', {'name': 'Ada'}) == 'Hello Ada'
    assert render_prompt('{{value}}', {'value': '{{do-not-recursively-render}}'}) == '{{do-not-recursively-render}}'
    with pytest.raises(AppError, match='missing'):
        render_prompt('{{missing}}', {})


def test_duplicate_and_long_content_analysis():
    common = '共同规则必须保留约束与权限边界。' * 10
    result = analyze([{'id': '1', 'name': 'one', 'content': common}, {'id': '2', 'name': 'two', 'content': common + '\n\n'}, {'id': '3', 'name': 'one', 'content': '中' * 2000}])
    assert result['duplicates'] == [['1', '2']]
    assert result['duplicate_names'] == [['1', '3']]
    assert result['long_resources'][0]['id'] == '3'
    assert result['repeated_passages'][0]['ids'] == ['1', '2']


def test_optimization_only_changes_whitespace_and_displays_diff():
    result = optimization_preview('hello   \n\n\nworld')
    assert result['after'] == 'hello\n\nworld'
    assert '--- 当前' in result['diff'] and '+++ 建议' in result['diff']
    assert result['after_tokens'] <= result['before_tokens']
