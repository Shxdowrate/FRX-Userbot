"""
name: regex_find
category: utils
title: Регулярное выражение
description: ищет по тексту регулярное выражение Python и возвращает совпадения (до 30); flags — i (без регистра), m, s
args: pattern: string, text: string, flags?: string
example: {"pattern": "\\d{3}-\\d{2}", "text": "код 123-45 и 678-90"}
keywords: регулярк, regex, найди по шаблону, выдели из текста
"""
import re


async def run(pattern: str = '', text: str = '', flags: str = '') -> str:
    if len(text or '') > 200000 or len(pattern or '') > 300:
        return "Слишком большой текст или шаблон"
    fl = 0
    for c in (flags or '').lower():
        fl |= {'i': re.I, 'm': re.M, 's': re.S}.get(c, 0)
    try:
        found = [m.group(0) if not m.groups() else m.groups() for m in re.finditer(pattern, text or '', fl)]
    except re.error as e:
        return f"Ошибка в выражении: {e}"
    return f"Совпадений: {len(found)}\n" + '\n'.join(str(x) for x in found[:30]) if found else "Совпадений нет"
