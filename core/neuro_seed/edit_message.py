"""
name: edit_message
category: telegram
title: Изменить сообщение
description: редактирует СВОЁ сообщение. chat — где (here по умолчанию), id — номер или «reply»; text — новый текст
args: id: string, text: string, chat?: string
example: {"id": "reply", "text": "исправленный текст"}
dangerous: true
keywords: исправь сообщение, отредактируй, измени сообщение
"""
from core import neuro_tg as tg

import engine


async def describe(id: str = '', text: str = '', chat: str = '', **_):
    nums = tg.msg_ids(id)
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Не изменено: {e}'}
    if not nums:
        return {'block': 'Не изменено: не указан id сообщения'}
    return f"Изменить #{nums[0]} в {tg.describe(ent)} на:\n«{str(text)[:700]}»"


async def run(id: str = '', text: str = '', chat: str = '') -> str:
    nums = tg.msg_ids(id)
    if not nums or not str(text).strip():
        return "Ошибка: нужны id и text"
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
        m = await engine.client.get_messages(ent, ids=nums[0])
        if m is None:
            return f"Сообщение #{nums[0]} не найдено"
        if not m.out:
            return "Это не ваше сообщение — Telegram даёт менять только свои"
        old = m.message or ''
        await engine.client.edit_message(ent, nums[0], str(text))
    except Exception as e:                                   # noqa: BLE001
        return f"Не изменено: {e}"
    tg.log_action('изменено сообщение', ent, [nums[0]], f"было: {old[:200]}")
    return f"Изменено #{nums[0]} в {tg.describe(ent)}"
