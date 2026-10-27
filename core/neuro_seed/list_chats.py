"""
name: list_chats
category: telegram
title: Мои чаты
description: список диалогов аккаунта (свежие сверху): id, название, @username, тип, телефон (если виден), непрочитанные. kind — all | users | groups | channels | bots; query — фильтр по имени/@username; unread — только с непрочитанными
args: limit?: int, query?: string, kind?: string, unread?: bool
example: {"limit": 30, "kind": "users"}
keywords: мои чаты, список чатов, все чаты, диалоги, непрочитанные, какие чаты, список диалогов, кто мне писал
limit: 8000
"""
from core import neuro_tg as tg

import engine

KINDS = {'users': ('ЛС',), 'people': ('ЛС',), 'лс': ('ЛС',), 'groups': ('группа',), 'группы': ('группа',),
         'channels': ('канал',), 'каналы': ('канал',), 'bots': ('бот',), 'боты': ('бот',)}


async def run(limit: int = 30, query: str = '', kind: str = 'all', unread: bool = False) -> str:
    limit = max(1, min(int(limit or 30), 200))
    want = KINDS.get(str(kind or 'all').lower())
    out = []
    async for d in engine.client.iter_dialogs(limit=1000):
        e = d.entity
        k = tg.kind(e)
        if want and k not in want:
            continue
        if unread and not d.unread_count:
            continue
        if query and not tg._score(query, d.name, getattr(e, 'username', None)) and str(query).lower() not in (d.name or '').lower():
            continue
        parts = [str(d.id), d.name or '—', k]
        if getattr(e, 'username', None):
            parts.append('@' + e.username)
        if getattr(e, 'phone', None):
            parts.append('+' + str(e.phone).lstrip('+'))
        if d.unread_count:
            parts.append(f'непрочитано {d.unread_count}')
        out.append(' | '.join(parts))
        if len(out) >= limit:
            break
    return '\n'.join(out) or "Ничего не найдено"
