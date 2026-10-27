"""
name: join_leave
category: tg_groups
title: Вступить / выйти
description: вступить в группу или канал (action=join; chat — @username, ссылка t.me/… или приглашение t.me/+…) или выйти из него (action=leave; chat — имя, id, @username, here)
args: action: string, chat: string
example: {"action": "join", "chat": "https://t.me/durov"}
dangerous: true
keywords: вступи, подпишись, зайди в группу, выйди из, отпишись, покинь
"""
import re

from core import neuro_tg as tg

import engine


def _invite(chat):
    m = re.search(r'(?:t\.me|telegram\.me)/(?:\+|joinchat/)([A-Za-z0-9_\-]+)', str(chat))
    return m.group(1) if m else None


async def describe(action: str = '', chat: str = '', **_):
    act = str(action).lower()
    if act.startswith('j') or act.startswith('вступ') or act.startswith('подпис'):
        if _invite(chat):
            return f"Вступить по приглашению {chat}"
        try:
            ent, _ = await tg.resolve_chat(chat, allow_name=False)
        except Exception as e:                               # noqa: BLE001
            return {'block': f'Не найден чат: {e}'}
        return f"Вступить в {tg.kind(ent)} {tg.describe(ent)}"
    try:
        ent, how = await tg.resolve_chat(chat or 'here')
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не выбран чат')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Ошибка: {e}'}
    return f"Выйти из {tg.kind(ent)} {tg.describe(ent)}" + (' (найден по имени — проверьте)' if how == 'name' else '')


async def run(action: str = '', chat: str = '') -> str:
    act = str(action).lower()
    try:
        if act.startswith('j') or act.startswith('вступ') or act.startswith('подпис'):
            inv = _invite(chat)
            if inv:
                from telethon.tl.functions.messages import ImportChatInviteRequest
                r = await engine.client(ImportChatInviteRequest(inv))
                ent = (r.chats or [None])[0]
            else:
                from telethon.tl.functions.channels import JoinChannelRequest
                ent, _ = await tg.resolve_chat(chat, allow_name=False)
                await engine.client(JoinChannelRequest(ent))
            tg.log_action('вступил', ent)
            return f"Вступил: {tg.describe(ent) if ent else chat}"
        ent, _ = await tg.resolve_chat(chat or 'here')
        await engine.client.delete_dialog(ent)
        tg.log_action('вышел', ent)
        return f"Вышел: {tg.describe(ent)}"
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Не получилось: {e}"
