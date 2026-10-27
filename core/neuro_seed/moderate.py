"""
name: moderate
category: tg_groups
title: Модерация
description: действия админа в группе: action — mute (запретить писать; minutes — на сколько, 0 — НАВСЕГДА), unmute (СНЯТЬ мут — «сними мут», «размуть»), kick (выгнать, сможет вернуться), ban (забанить), unban. chat — группа (here по умолчанию), user — кто (имя, @username, id; «reply» — автор сообщения, на которое ответили)
args: action: string, user: string, chat?: string, minutes?: int
example: {"action": "mute", "user": "reply", "minutes": 60}
dangerous: true
keywords: замуть, мут, забань, бан, кикни, выгони, разбань, размуть
"""
import re
from datetime import datetime, timedelta

from core import neuro_tg as tg

import engine


async def _target(user, chat_ent):
    if str(user).strip().lower() in tg.REPLY_WORDS and tg.CTX.get('reply_id'):
        m = await engine.client.get_messages(chat_ent, ids=int(tg.CTX['reply_id']))
        if m is None or not m.sender_id:
            raise ValueError('у сообщения, на которое ответили, нет автора')
        return await engine.client.get_entity(m.sender_id)
    return await tg.member_in_chat(chat_ent, user)        # ищем среди участников группы, а не только в своих диалогах


ACTIONS = {'mute': 'mute', 'мут': 'mute', 'замуть': 'mute', 'unmute': 'unmute', 'размут': 'unmute', 'размуть': 'unmute',
           'kick': 'kick', 'кик': 'kick', 'выгнать': 'kick', 'ban': 'ban', 'бан': 'ban', 'забанить': 'ban',
           'unban': 'unban', 'разбан': 'unban', 'разбанить': 'unban'}
UNDO_WANT = re.compile(r'сним\w*\s+(ему |ей |с него |с неё |с нее )?(мут|бан|ограничен)|сня\w*\s+мут|размут|размуч|разбан|'
                       r'верн\w*.{0,20}(писать|в группу|в чат)|пусть (снова )?пишет|unmute|unban|прост\w* его|сжалимся', re.I)
DO_WANT = re.compile(r'замут|\bмут\b|забан|\bбан\b|кикни|выгони|заблокир', re.I)
REVERSE = {'mute': 'unmute', 'ban': 'unban'}
DUR_RE = re.compile(r'на\s+(\d+)?\s*(минут\w*|мин\b|час\w*|ч\b|дн\w*|день|сутк\w*|недел\w*)', re.I)
UNIT = {'мин': 1, 'мину': 1, 'час': 60, 'ч': 60, 'дн': 1440, 'ден': 1440, 'сут': 1440, 'нед': 10080}


def _ask_minutes():
    """«замуть на час» — модель часто не передаёт minutes, и мут становится вечным; берём срок из просьбы владельца."""
    m = DUR_RE.search(tg.CTX.get('user_text') or '')
    if not m:
        return 0
    unit = next((v for k, v in UNIT.items() if m.group(2).lower().startswith(k)), 0)
    return int(m.group(1) or 1) * unit


def _action(action):
    """Действие с поправкой на просьбу владельца: слабая модель на «сними мут» нередко зовёт mute (с 0 минут — НАВСЕГДА)."""
    a = ACTIONS.get(str(action).lower().strip(), str(action).lower().strip())
    ask = tg.CTX.get('user_text') or ''
    if a in REVERSE and UNDO_WANT.search(ask) and not DO_WANT.search(UNDO_WANT.sub(' ', ask)):
        return REVERSE[a], True
    return a, False


def _what(a, u, g, minutes):
    if a == 'mute':
        return f"замутить {tg.describe(u)} в {tg.describe(g)} " + (f"на {minutes} мин" if minutes else "НАВСЕГДА (пока не снимут)")
    if a == 'ban':
        return f"забанить {tg.describe(u)} в {tg.describe(g)} " + (f"на {minutes} мин" if minutes else "НАВСЕГДА")
    names = {'unmute': 'снять мут с', 'kick': 'выгнать', 'unban': 'разбанить'}
    return f"{names.get(a, a)} {tg.describe(u)} в {tg.describe(g)}"


async def describe(action: str = '', user: str = '', chat: str = '', minutes: int = 0, **_):
    try:
        g, _ = await tg.resolve_chat(chat or 'here')
        u = await _target(user, g)
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Ничего не сделано')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Ничего не сделано: {e}'}
    a, fixed = _action(action)
    minutes = int(minutes or 0) or (_ask_minutes() if a in ('mute', 'ban') else 0)
    return _what(a, u, g, minutes).capitalize() + (f"\n(модель просила «{action}», но вы просили снять — делаю {a})" if fixed else '')


async def run(action: str = '', user: str = '', chat: str = '', minutes: int = 0) -> str:
    a, fixed = _action(action)
    try:
        g, _ = await tg.resolve_chat(chat or 'here')
        u = await _target(user, g)
        minutes = int(minutes or 0) or (_ask_minutes() if a in ('mute', 'ban') else 0)
        until = datetime.now().astimezone() + timedelta(minutes=minutes) if minutes else None
        if a == 'mute':
            await engine.client.edit_permissions(g, u, until_date=until, send_messages=False)
        elif a == 'unmute':
            await engine.client.edit_permissions(g, u, send_messages=True, send_media=True, send_stickers=True, send_gifs=True, embed_link_previews=True)
        elif a == 'kick':
            await engine.client.kick_participant(g, u)
        elif a == 'ban':
            await engine.client.edit_permissions(g, u, until_date=until, view_messages=False)
        elif a == 'unban':
            await engine.client.edit_permissions(g, u, view_messages=True, send_messages=True)
        else:
            return "Неизвестное действие: mute, unmute, kick, ban, unban"
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Ничего не сделано')
    except Exception as e:                                   # noqa: BLE001
        return f"Не получилось (нужны права админа?): {e}"
    what = _what(a, u, g, minutes)
    tg.log_action(f'модерация: {what}', g, [getattr(u, 'id', 0)] if a in REVERSE else [], undo=('mod' if a in REVERSE else None),
                  extra={'user': tg.describe(u), 'mod': a})
    done = {'mute': 'Замучен', 'unmute': 'Мут снят', 'kick': 'Выгнан', 'ban': 'Забанен', 'unban': 'Разбанен'}[a]
    out = f"Готово — {done}: {tg.describe(u)} в {tg.describe(g)}"
    if a in ('mute', 'ban'):
        out += f" ({'на ' + str(minutes) + ' мин' if minutes else 'НАВСЕГДА, пока не снимут'})"
    if fixed:
        out += f". (Ты вызвал «{action}», но владелец просил снять — выполнено «{a}».)"
    return out + ". Перескажи владельцу ровно это."
