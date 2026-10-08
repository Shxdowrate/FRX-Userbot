"""
name: frx_readme
category: system
title: Подробная документация FRX (README)
description: ищет в полной техдокументации FRX (README.md, очень большой — для разработчиков модулей) разделы по теме; вызывай ТОЛЬКО если путеводитель frx_help не дал ответа
args: query: string
example: {"query": "create_config валидация"}
heavy: ~3000–4000 токенов за вызов (выдержки из огромного README); локальным моделям обычно не по силам
keywords: readme, техдокументация, как написать модуль, api модуля, декоратор, create_config
limit: 7000
"""
import re
from pathlib import Path

_STOP = {'как', 'что', 'это', 'для', 'или', 'при', 'про', 'где', 'какой', 'какая', 'какие', 'мне', 'мой', 'можно', 'есть'}


async def run(query: str = '') -> str:
    try:
        text = Path('README.md').read_text(encoding='utf-8')
    except OSError:
        return 'README.md не найден рядом с юзерботом.'
    if not (query or '').strip():
        return 'Нужен query — тема, по которой искать (например: «@decorators.command», «create_config», «инлайн-форма»).'
    secs, head, buf = [], 'Введение', []
    for line in text.split('\n'):
        if line.startswith('## '):
            secs.append((head, '\n'.join(buf)))
            head, buf = line[3:].strip(), []
        else:
            buf.append(line)
    secs.append((head, '\n'.join(buf)))
    words = [w for w in re.findall(r'[\w.@]{3,}', query.lower()) if w not in _STOP]
    stems = [w[:max(4, len(w) - 2)] if len(w) > 5 else w for w in words]
    scored = []
    for h, b in secs:
        sc = sum(6 for s in stems if s in h.lower()) + sum(min(b.lower().count(s), 6) for s in stems)
        if sc:
            scored.append((sc, h, b))
    if not scored:
        return f'По запросу «{query}» в README ничего не нашлось.'
    scored.sort(key=lambda x: -x[0])
    out = []
    for _, h, b in scored[:2]:
        paras = [p for p in re.split(r'\n\s*\n', b) if p.strip()]
        paras.sort(key=lambda p: -sum(p.lower().count(s) for s in stems))
        out.append(f'## {h}\n' + '\n\n'.join(paras[:4])[:3200])
    return '\n\n'.join(out)
