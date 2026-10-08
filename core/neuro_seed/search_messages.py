"""
name: search_messages
category: telegram
title: Поиск по сообщениям
description: ищет текст в сообщениях чата (серверный поиск Telegram); chat — id, @username, «me» или часть названия
args: chat: string, query: string, limit?: int
example: {"chat": "me", "query": "пароль wifi"}
keywords: найди сообщение, поищи в чате, где я писал, найди в переписке
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


async def run(chat: str = '', query: str = '', limit: int = 10) -> str:
    if not chat or not query:
        return "Ошибка: нужны chat и query"
    try:
        ent = await _entity(chat)
        msgs = [m async for m in engine.client.iter_messages(ent, search=query, limit=max(1, min(int(limit), 30)))]
    except Exception as e:
        return f"Ошибка: {e}"
    return '\n'.join(f"#{m.id} " + _line(m) for m in msgs) or "Ничего не найдено"
