"""
name: list_chats
category: telegram
title: Мои чаты
description: список последних диалогов аккаунта: id, название, тип, число непрочитанных; query — фильтр по названию
args: limit?: int, query?: string
example: {"limit": 15}
keywords: мои чаты, список чатов, диалоги, непрочитанные, какие чаты
"""
import engine


async def run(limit: int = 15, query: str = '') -> str:
    out = []
    async for d in engine.client.iter_dialogs(limit=300):
        if query and query.lower() not in (d.name or '').lower():
            continue
        kind = 'канал' if d.is_channel and not d.is_group else 'группа' if d.is_group else 'ЛС'
        out.append(f"{d.id} | {d.name} | {kind} | непрочитано {d.unread_count}")
        if len(out) >= max(1, min(int(limit), 50)):
            break
    return '\n'.join(out) or "Ничего не найдено"
