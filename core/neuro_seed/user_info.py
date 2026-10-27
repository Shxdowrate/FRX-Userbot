"""
name: user_info
category: telegram
title: Кто это
description: сведения о пользователе, боте, группе или канале: имя, @username, id, телефон (если виден), премиум; user — id, @username, +телефон, «here» (собеседник в этом чате) или имя
args: user: string
example: {"user": "@durov"}
keywords: кто это, информация о пользователе, узнай про, профиль
"""
import re

from core import neuro_tg as tg

import engine


async def _entity(chat):
    try:
        ent, _how = await tg.resolve_chat(chat)
    except tg.Ambiguous as e:
        raise ValueError(tg.ambiguous_text(e, 'Не выбран чат'))
    return ent


def _who(m):
    s = getattr(m, 'sender', None)
    if s is None:
        return 'кто-то'
    return (getattr(s, 'first_name', None) or getattr(s, 'title', None) or getattr(s, 'username', None) or str(s.id))


def _line(m):
    t = (m.message or '').replace('\n', ' ')
    if not t and m.media:
        t = '[вложение]'
    return f"[{m.date.astimezone().strftime('%d.%m %H:%M')}] {_who(m)}: {t[:300]}"


async def run(user: str = '') -> str:
    if not user:
        return "Ошибка: не указан пользователь"
    try:
        e = await _entity(user)
        if e == 'me':
            e = await engine.client.get_me()
    except Exception as ex:
        return f"Ошибка: {ex}"
    name = ' '.join(x for x in (getattr(e, 'first_name', None), getattr(e, 'last_name', None)) if x) or getattr(e, 'title', '') or '—'
    parts = [f"{name}", f"id {e.id}", f"@{e.username}" if getattr(e, 'username', None) else 'без @username']
    if getattr(e, 'phone', None):
        parts.append(f"тел. +{e.phone}")
    if getattr(e, 'bot', False):
        parts.append('бот')
    if getattr(e, 'premium', False):
        parts.append('премиум')
    if getattr(e, 'participants_count', None):
        parts.append(f"участников {e.participants_count}")
    return ' · '.join(parts)
