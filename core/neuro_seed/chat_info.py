"""
name: chat_info
category: tg_groups
title: О группе / канале / боте
description: подробности о группе, канале или боте: название, @username, id, описание, участников, админов, ваша роль, связанный чат, ссылка-приглашение (если вы админ), закреп. chat — имя, id, @username, ссылка или here
args: chat: string
example: {"chat": "here"}
keywords: о группе, о канале, сколько участников, описание группы, описание канала, кто админ, информация о чате
"""
from core import neuro_tg as tg

import engine


async def run(chat: str = 'here') -> str:
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    if ent == 'me':
        return "Это «Избранное»"
    lines = [f"{tg.kind(ent)}: {tg.describe(ent)}"]
    try:
        if hasattr(ent, 'first_name'):                      # человек или бот
            from telethon.tl.functions.users import GetFullUserRequest
            full = (await engine.client(GetFullUserRequest(ent))).full_user
            if full.about:
                lines.append(f"о себе: {full.about}")
            bi = getattr(full, 'bot_info', None)
            if bi is not None:
                if bi.description:
                    lines.append(f"описание бота: {bi.description[:400]}")
                if bi.commands:
                    lines.append('команды: ' + ', '.join(f"/{c.command} — {c.description}" for c in bi.commands[:30]))
            if getattr(full, 'common_chats_count', None):
                lines.append(f"общих чатов: {full.common_chats_count}")
            return '\n'.join(lines)
        from telethon.tl.types import Channel
        if isinstance(ent, Channel):
            from telethon.tl.functions.channels import GetFullChannelRequest
            full = (await engine.client(GetFullChannelRequest(ent))).full_chat
            role = 'создатель' if ent.creator else 'админ' if ent.admin_rights else 'участник' if not getattr(ent, 'left', False) else 'не состою'
            lines.append(f"тип: {'канал' if ent.broadcast else 'супергруппа'}, ваша роль: {role}")
            for label, v in (('участников', full.participants_count), ('админов', full.admins_count), ('онлайн', getattr(full, 'online_count', None)),
                             ('забанено', full.kicked_count)):
                if v:
                    lines.append(f"{label}: {v}")
            if full.about:
                lines.append(f"описание: {full.about[:600]}")
            if full.linked_chat_id:
                lines.append(f"связанный чат: id -100{full.linked_chat_id}")
            if full.pinned_msg_id:
                lines.append(f"закреплено: #{full.pinned_msg_id}")
            inv = getattr(full, 'exported_invite', None)
            if inv is not None and getattr(inv, 'link', None):
                lines.append(f"ссылка-приглашение: {inv.link}")
        else:
            from telethon.tl.functions.messages import GetFullChatRequest
            full = (await engine.client(GetFullChatRequest(ent.id))).full_chat
            lines.append(f"участников: {getattr(ent, 'participants_count', '?')}")
            if full.about:
                lines.append(f"описание: {full.about[:600]}")
    except Exception as e:                                   # noqa: BLE001
        lines.append(f"(подробности недоступны: {e})")
    return '\n'.join(lines)
