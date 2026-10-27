"""
name: find_files
category: tg_files
title: Найти файлы
description: ищет файлы, фото, видео, голосовые, ссылки в Telegram. from_user — кто прислал («Дима»; ищет в переписке с ним), from_chat — в каком чате (имя, id, here), query — что в имени файла или подписи («отчет» найдёт «Отчёт_Q3.xlsx»), kind — document | photo | video | media | voice | audio | link | any, days — за сколько последних дней. Без чата и человека — поиск по всем чатам. Найденное потом можно отправить: send_files с ids="found"
args: query?: string, from_user?: string, from_chat?: string, kind?: string, limit?: int, days?: int
example: {"from_user": "Дима", "query": "отчет", "kind": "document"}
keywords: найди файл, найди документ, файлы от, что присылал, кидал, скидывал, присылал, фото от, отчет, документ
limit: 6000
"""
from datetime import datetime, timedelta

from core import neuro_tg as tg

import engine


def _who(m, people):
    s = getattr(m, 'sender', None)
    if s is None:
        return people.get(m.sender_id, '—')
    if getattr(m, 'out', False):
        return 'я'
    return ' '.join(x for x in (getattr(s, 'first_name', None), getattr(s, 'last_name', None)) if x) or getattr(s, 'title', None) or str(s.id)


async def run(query: str = '', from_user: str = '', from_chat: str = '', kind: str = 'any', limit: int = 10, days: int = 0) -> str:
    limit = max(1, min(int(limit or 10), 30))
    flt = tg.msg_filter(kind)
    if flt is None and str(kind or 'any').lower() in ('any', 'все', 'all', ''):
        from telethon.tl.types import InputMessagesFilterDocument, InputMessagesFilterPhotoVideo
        flts = [InputMessagesFilterDocument, InputMessagesFilterPhotoVideo]
    else:
        flts = [flt] if flt else [None]
    since = datetime.now().astimezone() - timedelta(days=int(days)) if days else None
    try:
        person = chat = None
        if from_user:
            person, _ = await tg.resolve_chat(from_user)
        if from_chat:
            chat, _ = await tg.resolve_chat(from_chat)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Поиск не выполнен')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    where = chat if chat is not None else (person if person is not None else None)    # None — по всем чатам
    found, seen = [], set()
    for f in flts:
        for search in ([query] if query else []) + ['']:     # сначала поиск Telegram, потом — свой (с падежами) по последним файлам
            kw = {'limit': 200 if not search else 60}
            if f is not None:
                kw['filter'] = f
            if search:
                kw['search'] = search
            if where is not None and person is not None and where is not person:
                kw['from_user'] = person
            try:
                async for m in engine.client.iter_messages(where, **kw):
                    if since and m.date < since:
                        break
                    if where is None and person is not None and m.sender_id != getattr(person, 'id', None):
                        continue
                    if where is person and person is not None and m.out and from_user:
                        continue                              # «что кидал Дима» — не мои файлы в переписке с ним
                    if not search and not tg.file_match(query, m):
                        continue
                    key = (tg.peer_id(m.chat) if getattr(m, 'chat', None) else getattr(m, 'chat_id', None), m.id)
                    if key in seen:
                        continue
                    seen.add(key)
                    found.append((key, m))
                    if len(found) >= limit:
                        break
            except Exception as e:                           # noqa: BLE001
                if not found:
                    return f"Ошибка поиска: {e}"
            if len(found) >= limit or (search and found):
                break
        if len(found) >= limit:
            break
    if not found:
        return "Ничего не найдено" + (f" (запрос «{query}»)" if query else '')
    found.sort(key=lambda x: x[1].date, reverse=True)
    tg.FOUND[:] = [k for k, _m in found]
    lines = []
    for i, (key, m) in enumerate(found, 1):
        chat_name = tg.describe(m.chat) if getattr(m, 'chat', None) else str(key[0])
        cap = (m.message or '').replace('\n', ' ')[:80]
        lines.append(f"{i}. #{m.id} в {chat_name} · {m.date.astimezone().strftime('%d.%m.%Y %H:%M')} · от {_who(m, {})} · {tg.file_title(m)}"
                     + (f" · «{cap}»" if cap else ''))
    return '\n'.join(lines) + '\nОтправить найденное: send_files с ids="found" (или номерами из списка, например "1,3")'
