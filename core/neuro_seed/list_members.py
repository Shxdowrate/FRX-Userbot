"""
name: list_members
category: tg_groups
title: Участники
description: участники группы/канала: имя, @username, id, бот или нет, админ, в муте/бане. chat — имя, id, @username, here; query — поиск по имени; admins — только админы; bots — только боты; restricted — только замученные/ограниченные; banned — только забаненные; limit — до 200. Подробно о правах одного — member_rights
args: chat: string, query?: string, admins?: bool, bots?: bool, restricted?: bool, banned?: bool, limit?: int
example: {"chat": "here", "admins": true}
keywords: участники, кто в группе, список участников, админы группы, кто админ, боты в группе, кто в муте, кто замучен, кто в бане, забаненные
limit: 8000
"""
from core import neuro_tg as tg

import engine


async def run(chat: str = 'here', query: str = '', admins: bool = False, bots: bool = False, restricted: bool = False, banned: bool = False,
              limit: int = 50) -> str:
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    from telethon.tl import types
    kw = {'limit': max(1, min(int(limit or 50), 200))}
    if query:
        kw['search'] = str(query)
    if admins:
        kw['filter'] = types.ChannelParticipantsAdmins
    elif bots:
        kw['filter'] = types.ChannelParticipantsBots
    elif restricted:
        kw['filter'] = types.ChannelParticipantsBanned(q=str(query or ''))
        kw.pop('search', None)
    elif banned:
        kw['filter'] = types.ChannelParticipantsKicked(q=str(query or ''))
        kw.pop('search', None)
    out = []
    try:
        async for u in engine.client.iter_participants(ent, **kw):
            p = getattr(u, 'participant', None)
            role = ('создатель' if isinstance(p, (types.ChannelParticipantCreator, types.ChatParticipantCreator)) else
                    'админ' if isinstance(p, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin)) else '')
            if isinstance(p, types.ChannelParticipantBanned):
                br = p.banned_rights
                until = getattr(br, 'until_date', None)
                when = 'навсегда' if not until or until.year > 2037 else 'до ' + until.astimezone().strftime('%d.%m %H:%M')
                role = ('в бане ' if getattr(br, 'view_messages', False) else 'в муте ' if getattr(br, 'send_messages', False) else 'ограничен ') + when
            out.append(tg.describe(u) + (f" · {role}" if role else '') + (f" · «{p.rank}»" if getattr(p, 'rank', None) else ''))
    except Exception as e:                                   # noqa: BLE001
        return f"Список участников недоступен: {e}"
    return f"{tg.describe(ent)} — {len(out)}:\n" + '\n'.join(out) if out else "Никого не нашлось"
