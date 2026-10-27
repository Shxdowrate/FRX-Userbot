"""
name: member_rights
category: tg_groups
title: Права участника
description: права и ограничения участника группы/канала: админ (какие права), создатель, в муте (до когда / навсегда, что именно запрещено), в бане, вышел; плюс общие запреты группы для всех. chat — группа (here по умолчанию), user — кто (имя, @username, id; «reply» — автор сообщения, на которое ответили); пустой user — только общие права группы
args: chat?: string, user?: string
example: {"chat": "MUSIX", "user": "Unreal"}
keywords: права, разрешения, ограничения, замучен ли, в муте ли, забанен ли, может ли писать, админ ли, какие права, права участника
limit: 4000
"""
from datetime import datetime, timezone

from core import neuro_tg as tg

import engine

BANNED = {'send_messages': 'писать', 'send_media': 'медиа', 'send_stickers': 'стикеры', 'send_gifs': 'GIF', 'send_games': 'игры',
          'send_inline': 'инлайн-ботов', 'embed_links': 'превью ссылок', 'send_polls': 'опросы', 'change_info': 'менять инфо',
          'invite_users': 'приглашать', 'pin_messages': 'закреплять', 'send_photos': 'фото', 'send_videos': 'видео',
          'send_roundvideos': 'кружки', 'send_audios': 'аудио', 'send_voices': 'голосовые', 'send_docs': 'файлы', 'send_plain': 'текст',
          'manage_topics': 'темы'}
ADMIN = {'change_info': 'менять инфо', 'post_messages': 'публиковать', 'edit_messages': 'редактировать чужое', 'delete_messages': 'удалять',
         'ban_users': 'банить/мутить', 'invite_users': 'приглашать', 'pin_messages': 'закреплять', 'add_admins': 'назначать админов',
         'anonymous': 'анонимно', 'manage_call': 'звонки/трансляции', 'manage_topics': 'темы', 'post_stories': 'истории',
         'edit_stories': 'ред. истории', 'delete_stories': 'удалять истории'}


def _until(d):
    if not d:
        return 'навсегда'
    if isinstance(d, (int, float)):
        d = datetime.fromtimestamp(d, timezone.utc)
    if d.year > 2037 or (d - datetime.now(timezone.utc)).days > 366 * 5:
        return 'навсегда'
    return 'до ' + d.astimezone().strftime('%d.%m.%Y %H:%M')


def _denied(rights):
    return [v for k, v in BANNED.items() if getattr(rights, k, False)]


async def run(chat: str = 'here', user: str = '') -> str:
    from telethon.tl import functions, types
    try:
        g, _ = await tg.resolve_chat(chat or 'here')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран чат')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    lines = [tg.describe(g)]
    default = getattr(g, 'default_banned_rights', None)
    den = _denied(default) if default else []
    lines.append('Общие запреты для всех: ' + (', '.join(den) if den else 'нет'))
    if not str(user or '').strip():
        return '\n'.join(lines)
    try:
        if str(user).strip().lower() in tg.REPLY_WORDS and tg.CTX.get('reply_id'):
            m = await engine.client.get_messages(g, ids=int(tg.CTX['reply_id']))
            u = await engine.client.get_entity(m.sender_id)
        else:
            u = await tg.member_in_chat(g, user)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран участник')
    except Exception as e:                                   # noqa: BLE001
        return f"Участник не найден: {e}"
    who = tg.describe(u)
    try:
        if isinstance(g, types.Channel):
            p = (await engine.client(functions.channels.GetParticipantRequest(g, u))).participant
        else:
            full = await engine.client(functions.messages.GetFullChatRequest(g.id))
            p = next((x for x in full.full_chat.participants.participants if getattr(x, 'user_id', None) == u.id), None)
    except Exception as e:                                   # noqa: BLE001
        if 'USER_NOT_PARTICIPANT' in str(e) or 'not a member' in str(e).lower():
            return '\n'.join(lines + [f"{who}: не состоит в группе"])
        return '\n'.join(lines + [f"{who}: права недоступны ({e})"])
    if p is None:
        return '\n'.join(lines + [f"{who}: не состоит в группе"])
    if isinstance(p, (types.ChannelParticipantCreator, types.ChatParticipantCreator)):
        lines.append(f"{who}: создатель — может всё")
    elif isinstance(p, (types.ChannelParticipantAdmin, types.ChatParticipantAdmin)):
        r = getattr(p, 'admin_rights', None)
        can = [v for k, v in ADMIN.items() if getattr(r, k, False)] if r else ['все права админа обычной группы']
        lines.append(f"{who}: админ" + (f" «{p.rank}»" if getattr(p, 'rank', None) else '') + " — может: " + (', '.join(can) or 'ничего особого'))
    elif isinstance(p, types.ChannelParticipantBanned):
        br = p.banned_rights
        if getattr(br, 'view_messages', False):
            lines.append(f"{who}: ЗАБАНЕН {_until(br.until_date)}")
        else:
            lines.append(f"{who}: ОГРАНИЧЕН {_until(br.until_date)} — нельзя: " + (', '.join(_denied(br)) or '—')
                         + (' (это мут: писать не может)' if br.send_messages else ''))
    elif isinstance(p, types.ChannelParticipantLeft):
        lines.append(f"{who}: вышел из группы")
    else:
        lines.append(f"{who}: обычный участник" + (" — ограничен только общими запретами" if den else " — без ограничений"))
    return '\n'.join(lines)
