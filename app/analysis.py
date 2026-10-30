"""Transparent heuristics: these are estimates, never model billing counts."""
from __future__ import annotations

import difflib
import math
import re
from collections import defaultdict

from app.storage import AppError, digest


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    wide = sum(1 for c in text if '\u2e80' <= c <= '\uffef')
    return math.ceil(wide * 1.5 + (len(text) - wide) / 4)


def variables(text: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'\{\{\s*([\w.-]+)\s*\}\}', text)))


def render_prompt(text: str, values: dict[str, str]) -> str:
    missing = [name for name in variables(text) if name not in values or values[name] == '']
    if missing:
        raise AppError('请填写变量：' + '、'.join(missing))
    return re.sub(r'\{\{\s*([\w.-]+)\s*\}\}', lambda match: str(values[match[1]]), text)


def optimization_preview(text: str) -> dict:
    after = re.sub(r'\n{3,}', '\n\n', '\n'.join(line.rstrip() for line in text.split('\n')))
    return {
        'before': text, 'after': after,
        'diff': ''.join(difflib.unified_diff(text.splitlines(keepends=True), after.splitlines(keepends=True), fromfile='当前', tofile='建议')),
        'before_tokens': estimate_tokens(text), 'after_tokens': estimate_tokens(after),
    }


def analyze(resources: list[dict]) -> dict:
    groups = defaultdict(list)
    names = defaultdict(list)
    paragraphs = defaultdict(set)
    long_resources = []
    total = 0
    for resource in resources:
        content = resource.get('content', '')
        total += estimate_tokens(content)
        normalized = re.sub(r'\s+', ' ', content).strip()
        if normalized:
            groups[digest(normalized)].append(resource['id'])
        names[resource['name'].casefold()].append(resource['id'])
        for paragraph in re.split(r'\n\s*\n', content):
            normalized_paragraph = re.sub(r'\s+', ' ', paragraph).strip()
            if len(normalized_paragraph) >= 60:
                paragraphs[normalized_paragraph].add(resource['id'])
        if estimate_tokens(content) > 2000 or len(content.splitlines()) > 300:
            long_resources.append({'id': resource['id'], 'tokens': estimate_tokens(content), 'reason': '正文过长，建议将详细示例移至 references/，按需读取。'})
    return {
        'estimated_tokens': total,
        'duplicates': [ids for ids in groups.values() if len(ids) > 1],
        'duplicate_names': [ids for ids in names.values() if len(ids) > 1],
        'repeated_passages': [{'text': text[:180], 'ids': sorted(ids)} for text, ids in paragraphs.items() if len(ids) > 1],
        'long_resources': long_resources,
        'notice': '静态启发式估算，不代表实际上下文、模型计费或实际节省。Skill 正文通常按需读取。',
    }
