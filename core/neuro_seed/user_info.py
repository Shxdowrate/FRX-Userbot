"""
name: user_info
category: telegram
title: Кто это
description: сведения о пользователе, боте, группе или канале: имя, @username, id, телефон (если виден), премиум; user — id, @username или часть названия диалога
args: user: string
example: {"user": "@durov"}
keywords: кто это, информация о пользователе, узнай про, профиль
"""
import re

import engine


async def _entity(chat):
    chat = str(chat).strip()
    if chat.lower() in ('me', 'saved', 'избранное', 'себе'):
        return 'me'
    if re.fullmatch(r'-?\d+', chat):
        return await engine.client.get_entity(int(chat))
    if chat.startswith('@') or re.fullmatch(r'[A-Za-z0-9_]{4,32}', chat):
        try:
            return await engine.client.get_entity(chat)
        except Exception:
            pass
    low = chat.lower().lstrip('@')
    async for d in engine.client.iter_dialogs(limit=300):
        if low in (d.name or '').lower():
            return d.entity
    raise ValueError(f"чат «{chat}» не найден среди диалогов")


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
