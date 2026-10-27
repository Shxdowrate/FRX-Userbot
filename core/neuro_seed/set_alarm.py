"""
name: set_alarm
category: agent
title: Будильник для себя
description: разбудить СЕБЯ (агента) позже и выполнить задачу без запроса пользователя: «вечером проверь, ответила ли Катя», «через час напомни и пришли сводку». at — «через 2 часа», «завтра в 9», «2026-10-27 18:00»; task — что сделать при пробуждении (пиши подробно, с именами и id — контекста разговора может не хватить); repeat — повторять каждые N минут (0 — один раз). Итог придёт владельцу в ЛС от бота
args: at: string, task: string, repeat?: int
example: {"at": "сегодня в 20:00", "task": "Проверь, ответила ли Катя Петрова (id 2) на моё сообщение; если нет — напомни мне"}
keywords: проверь потом, проверь позже, проверь вечером, разбуди, будильник, через час проверь, следи, каждые, каждый час, каждый день
"""
from datetime import datetime

from core import neuro_tg as tg


async def run(at: str = '', task: str = '', repeat: int = 0) -> str:
    if not str(task).strip():
        return "Ошибка: что сделать при пробуждении (task)?"
    try:
        when = tg.parse_when(at)
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    if when <= datetime.now().astimezone():
        return f"Время {tg.fmt_when(when)} уже прошло"
    rep = int(repeat or 0)
    if rep and rep < tg.MIN_REPEAT_MIN:
        rep = tg.MIN_REPEAT_MIN
    try:
        job = tg.jobs_add('alarm', task, at=when.timestamp(), repeat=rep)
    except ValueError as e:
        return f"Не поставлено: {e}"
    return f"Будильник поставлен: {tg.job_line(job)}"
