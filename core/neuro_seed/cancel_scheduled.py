"""
name: cancel_scheduled
category: tg_schedule
title: Отменить отложенное
description: удаляет отложенные сообщения (номера — из list_scheduled); chat — где они стоят (me — напоминания)
args: chat: string, ids: string
example: {"chat": "me", "ids": "15, 16"}
dangerous: true
"""
from core import neuro_tg as tg

import engine


async def describe(chat: str = 'me', ids: str = '', **_):
    try:
        ent, _how = await tg.resolve_chat(chat or 'me')
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Ошибка: {e}'}
    return f"Отменить отложенные {', '.join('#' + str(i) for i in tg.msg_ids(ids))} в {tg.describe(ent)}"


async def run(chat: str = 'me', ids: str = '') -> str:
    nums = tg.msg_ids(ids)
    if not nums:
        return "Ошибка: укажите номера отложенных (list_scheduled)"
    try:
        ent, _how = await tg.resolve_chat(chat or 'me')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    from telethon.tl.functions.messages import DeleteScheduledMessagesRequest
    await engine.client(DeleteScheduledMessagesRequest(peer=await engine.client.get_input_entity(ent), id=nums))
    tg.log_action('снято отложенное', ent, nums)
    return f"Отменено: {len(nums)} в {tg.describe(ent)}"
