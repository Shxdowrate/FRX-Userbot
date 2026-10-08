"""
name: read_chat
category: telegram
title: Прочитать чат
description: последние сообщения чата (от старых к новым); chat — id, @username, «me» (избранное) или часть названия диалога
args: chat: string, limit?: int
example: {"chat": "me", "limit": 10}
keywords: прочитай чат, последние сообщения, что писали, история чата, что в чате, переписка
limit: 5000
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


async def run(chat: str = '', limit: int = 10) -> str:
    if not chat:
        return "Ошибка: не указан чат"
    try:
        ent = await _entity(chat)
        msgs = [m async for m in engine.client.iter_messages(ent, limit=max(1, min(int(limit), 40)))]
    except Exception as e:
        return f"Ошибка: {e}"
    return '\n'.join(_line(m) for m in reversed(msgs)) or "Сообщений нет"
