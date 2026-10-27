"""
name: schedule_message
category: tg_schedule
title: Отложенное сообщение
description: ставит сообщение в отложенные Telegram (родная функция: уйдёт само в указанное время, даже если юзербот выключен; видно в чате как «отложенное»). to — как в send_message (имя, id, @username, here, me); at — «2026-10-27 09:00», «завтра в 9», «через 2 часа»
args: to: string, text: string, at: string
example: {"to": "Юля", "text": "Доброе утро! Не забудь про встречу", "at": "завтра в 9"}
dangerous: true
"""
from core import neuro_tg as tg

import engine


async def describe(to: str = '', text: str = '', at: str = '', **_):
    try:
        when = tg.parse_when(at)
        ent, how = await tg.resolve_chat(to)
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не запланировано')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Не запланировано: {e}'}
    return (f"Отложенное сообщение {tg.fmt_when(when)} → {tg.describe(ent)}"
            + (' (найден по имени — проверьте)' if how == 'name' else '') + f":\n«{str(text)[:700]}»")


async def run(to: str = '', text: str = '', at: str = '') -> str:
    if not str(text).strip():
        return "Ошибка: пустой текст"
    try:
        when = tg.parse_when(at)
        ent, _how = await tg.resolve_chat(to)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не запланировано')
    except Exception as e:                                   # noqa: BLE001
        return f"Не запланировано: {e}"
    from datetime import datetime
    if when <= datetime.now().astimezone():
        return f"Время {tg.fmt_when(when)} уже прошло — укажите будущее (или отправьте сразу через send_message)"
    msg = await engine.client.send_message(ent, str(text), schedule=when)
    tg.log_action(f'отложено на {tg.fmt_when(when)}', ent, [msg.id], text, undo='unschedule')
    return f"Запланировано: {tg.describe(ent)}, {tg.fmt_when(when)} (отложенное #{msg.id})"
