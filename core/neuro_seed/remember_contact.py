"""
name: remember_contact
category: telegram
title: Запомнить, кто это
description: запоминает прозвище человека: «шикс — это @shxdowrate», «мама — это +79…». Потом «напиши шиксу», «спроси у мамы» находят его сразу. name — прозвище, who — @username, id, телефон или имя из диалогов
args: name: string, who: string
example: {"name": "шикс", "who": "@shxdowrate"}
keywords: это @, запомни кто, его зовут, её зовут, это мой, это моя
"""
from core import neuro_tg as tg


async def run(name: str = '', who: str = '') -> str:
    if not str(name).strip() or not str(who).strip():
        return "Ошибка: нужны name (прозвище) и who (кто это)"
    try:
        ent, _ = await tg.resolve_chat(who)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не запомнил')
    except Exception as e:                                   # noqa: BLE001
        return f"Не запомнил: {e}"
    key = tg.alias_set(name, ent)
    return f"Запомнил: «{key}» — это {tg.describe(ent)}"
