"""
name: search_global
category: tg_files
title: Поиск по всем чатам
description: ищет текст сразу во всех чатах (один запрос к Telegram — не дорого). query — что искать; kind — any | link | document | photo…; days — за сколько дней; limit — до 30
args: query: string, kind?: string, days?: int, limit?: int
example: {"query": "таблица бюджета", "kind": "link"}
keywords: во всех чатах, где мне кидали, где мне писали, где-то писали, по всем чатам, найди везде
limit: 6000
"""
from datetime import datetime, timedelta

from core import neuro_tg as tg

import engine


async def run(query: str = '', kind: str = 'any', days: int = 0, limit: int = 15) -> str:
    if not str(query).strip():
        return "Ошибка: что искать?"
    kw = {'search': str(query), 'limit': max(1, min(int(limit or 15), 30))}
    flt = tg.msg_filter(kind)
    if flt is not None:
        kw['filter'] = flt
    since = datetime.now().astimezone() - timedelta(days=int(days)) if days else None
    out = []
    found = []
    try:
        async for m in engine.client.iter_messages(None, **kw):
            if since and m.date < since:
                continue
            chat = tg.describe(m.chat) if getattr(m, 'chat', None) else str(getattr(m, 'chat_id', '?'))
            text = (m.message or '').replace('\n', ' ')
            found.append((tg.peer_id(m.chat) if getattr(m, 'chat', None) else m.chat_id, m.id))
            out.append(f"{len(out) + 1}. #{m.id} в {chat} · {m.date.astimezone().strftime('%d.%m.%Y %H:%M')}"
                       + (f" · {tg.file_title(m)}" if m.media and getattr(m, 'file', None) else '') + f": {text[:200]}")
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка поиска: {e}"
    if found:
        tg.FOUND[:] = found
    return '\n'.join(out) or f"Ничего не найдено по «{query}»"
