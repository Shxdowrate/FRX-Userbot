"""
name: module_commands
category: modules
title: Модули и их команды
description: какие модули установлены в юзерботе и что умеют: имя, описание, команды с описанием аргументов. query — найти модуль по задаче или названию, можно несколько слов — любое из них («музыка видео», «валюта», «currency»); module — подробно про один модуль. Запустить найденную команду — run_command
args: query?: string, module?: string
example: {"query": "курс валют"}
keywords: модул, команд, плагин, умеешь использовать, воспользуйся
limit: 6000
"""
import re

from core import neuro_tg as tg

import engine

SKIP = {'neurodash'}


def _info(name):
    try:
        return engine.get_module_info(name)
    except Exception:                                        # noqa: BLE001
        return None


def _cmd_line(prefix, c):
    al = f" (= {', '.join(prefix + a for a in c['aliases'])})" if c.get('aliases') else ''
    return f"  {prefix}{c['command']}{al} — {(c.get('description') or '').splitlines()[0][:160] if c.get('description') else 'без описания'}"


async def run(query: str = '', module: str = '') -> str:
    prefix = await engine.get_prefix()
    names = [n for n in engine.get_module_list() if n not in SKIP]
    if module:
        key = str(module).lower().strip()
        name = next((n for n in names if n.lower() == key), None) or next((n for n in names if key in n.lower()), None)
        if name is None:
            hits = [n for n in names if (i := _info(n)) and key in (i.get('name') or '').lower()]
            name = hits[0] if hits else None
        if name is None:
            return f"Модуля «{module}» нет. Найти по задаче: module_commands с query"
        i = _info(name)
        lines = [f"{i['name']} ({name}) v{i.get('version')} — {i.get('description') or ''}"]
        lines += [_cmd_line(prefix, c) for c in i.get('commands', [])] or ['  команд нет (работает сам или через бота)']
        lines.append('Запуск: run_command(command="<команда>", args="<аргументы>")')
        return '\n'.join(lines)
    rows = []
    for n in names:
        i = _info(n)
        if not i:
            continue
        cmds = i.get('commands', [])
        hay = ' '.join([n, i.get('name') or '', i.get('description') or ''] + [c['command'] + ' ' + (c.get('description') or '') for c in cmds])
        if query:
            words = [w for w in re.split(r'[\s,;/|]+', str(query).lower()) if len(w) > 2 and w not in ('или', 'для', 'работы', 'модуль', 'модули')]
            if not any(tg._score(w, hay) or w in hay.lower() for w in (words or [str(query)])):
                continue
        rows.append((n, i, cmds))
    if not rows:
        return f"Под «{query}» модулей не нашлось" if query else "Модулей нет"
    if query and len(rows) <= 6:                             # нашли немного — сразу с командами
        out = []
        for n, i, cmds in rows:
            out.append(f"{i['name']} ({n}) — {i.get('description') or ''}")
            out += [_cmd_line(prefix, c) for c in cmds[:15]]
        out.append('Запуск: run_command(command="<команда>", args="<аргументы>")')
        return '\n'.join(out)
    return '\n'.join(f"{n}: {(i.get('description') or '')[:90]} · команды: {', '.join(prefix + c['command'] for c in cmds[:8]) or '—'}"
                     for n, i, cmds in rows)
