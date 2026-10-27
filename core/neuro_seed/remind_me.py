"""
name: remind_me
category: tg_schedule
title: Напомнить
description: напоминание владельцу — отложенное сообщение в «Избранное»: в нужное время Telegram пришлёт уведомление, даже если юзербот выключен. at — «через 30 минут», «завтра в 9», «2026-10-27 18:00»
args: text: string, at: string
example: {"text": "Позвонить маме", "at": "через 1 час"}
"""
from core import neuro_tg as tg

import engine


async def run(text: str = '', at: str = '') -> str:
    if not str(text).strip():
        return "Ошибка: о чём напомнить?"
    try:
        when = tg.parse_when(at)
    except Exception as e:                                   # noqa: BLE001
        return f"Не запланировано: {e}"
    from datetime import datetime
    if when <= datetime.now().astimezone():
        return f"Время {tg.fmt_when(when)} уже прошло"
    msg = await engine.client.send_message('me', '⏰ Напоминание: ' + str(text), schedule=when)
    tg.log_action(f'напоминание на {tg.fmt_when(when)}', 'me', [msg.id], text, undo='unschedule')
    return f"Напомню {tg.fmt_when(when)}: {text}"
