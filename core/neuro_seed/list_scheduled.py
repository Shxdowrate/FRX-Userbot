"""
name: list_scheduled
category: tg_schedule
title: Отложенные
description: отложенные сообщения чата (что и когда уйдёт); chat — имя, id, @username, here, me (напоминания в «Избранном»)
args: chat: string
example: {"chat": "me"}
"""
from core import neuro_tg as tg

import engine


async def run(chat: str = 'me') -> str:
    try:
        ent, _how = await tg.resolve_chat(chat or 'me')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    from telethon.tl.functions.messages import GetScheduledHistoryRequest
    res = await engine.client(GetScheduledHistoryRequest(peer=await engine.client.get_input_entity(ent), hash=0))
    msgs = [m for m in getattr(res, 'messages', []) if getattr(m, 'date', None)]
    if not msgs:
        return f"Отложенных нет — {tg.describe(ent)}"
    msgs.sort(key=lambda m: m.date)
    return f"{tg.describe(ent)}:\n" + '\n'.join(f"#{m.id} {tg.fmt_when(m.date)}: {(m.message or '[вложение]')[:200]}" for m in msgs)
