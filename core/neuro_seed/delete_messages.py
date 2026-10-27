"""
name: delete_messages
category: telegram
title: Удалить сообщения
description: удаляет сообщения у всех (свои — всегда; чужие — если вы админ группы). chat — где (here по умолчанию); ids — номера или «reply»
args: ids: string, chat?: string
example: {"ids": "120, 121"}
dangerous: true
keywords: удали сообщение, удали это, сотри
"""
from core import neuro_tg as tg

import engine


async def describe(ids: str = '', chat: str = '', **_):
    nums = tg.msg_ids(ids)
    if not nums:
        return {'block': 'Ничего не удалено: не указаны сообщения'}
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
        msgs = [m for m in await engine.client.get_messages(ent, ids=nums) if m]
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Ничего не удалено: {e}'}
    prev = '\n'.join(f"#{m.id}: {(m.message or '[вложение]')[:120]}" for m in msgs[:8])
    return f"Удалить у всех {len(nums)} сообщ. в {tg.describe(ent)} (без возврата):\n{prev}"


async def run(ids: str = '', chat: str = '') -> str:
    nums = tg.msg_ids(ids)
    if not nums:
        return "Ошибка: не указаны сообщения"
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
        res = await engine.client.delete_messages(ent, nums, revoke=True)
    except Exception as e:                                   # noqa: BLE001
        return f"Не удалено: {e}"
    n = sum(getattr(r, 'pts_count', 0) for r in (res or [])) or len(nums)
    tg.log_action('удалены сообщения', ent, nums)
    return f"Удалено: {n} в {tg.describe(ent)}"
