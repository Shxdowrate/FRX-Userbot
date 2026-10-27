"""
name: message_actions
category: telegram
title: Реакция, прочитано, закреп
description: мелкие действия с сообщениями. action: react (emoji — реакция, по умолчанию 👍), read (отметить чат прочитанным), pin / unpin (закрепить / открепить). chat — где (here по умолчанию), id — номер или «reply»
args: action: string, chat?: string, id?: string, emoji?: string
example: {"action": "react", "id": "reply", "emoji": "🔥"}
keywords: поставь реакцию, лайкни, реакци, прочитанн, закрепи, открепи
"""
from core import neuro_tg as tg

import engine


async def run(action: str = '', chat: str = '', id: str = '', emoji: str = '👍') -> str:
    act = str(action or '').lower().strip()
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    nums = tg.msg_ids(id)
    try:
        if act in ('read', 'прочитано', 'mark_read'):
            await engine.client.send_read_acknowledge(ent)
            return f"Отмечено прочитанным: {tg.describe(ent)}"
        if not nums:
            return "Ошибка: укажите id сообщения (или «reply»)"
        if act in ('react', 'reaction', 'реакция'):
            from telethon.tl.functions.messages import SendReactionRequest
            from telethon.tl.types import ReactionEmoji
            await engine.client(SendReactionRequest(peer=await engine.client.get_input_entity(ent), msg_id=nums[0],
                                                    reaction=[ReactionEmoji(emoticon=emoji or '👍')]))
            tg.log_action(f'реакция {emoji}', ent, [nums[0]])
            return f"Реакция {emoji} на #{nums[0]}"
        if act in ('pin', 'закрепить'):
            await engine.client.pin_message(ent, nums[0], notify=False)
            tg.log_action('закреплено', ent, [nums[0]])
            return f"Закреплено #{nums[0]}"
        if act in ('unpin', 'открепить'):
            await engine.client.unpin_message(ent, nums[0])
            tg.log_action('откреплено', ent, [nums[0]])
            return f"Откреплено #{nums[0]}"
    except Exception as e:                                   # noqa: BLE001
        return f"Не получилось: {e}"
    return "Неизвестное действие: react, read, pin, unpin"
