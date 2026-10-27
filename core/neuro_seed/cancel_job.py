"""
name: cancel_job
category: agent
title: Отменить задачу
description: снимает будильник или ожидание события по номеру (из list_jobs); id="all" — все
args: id: string
example: {"id": "3"}
keywords: отмени будильник, отмени задачу, не жди, больше не следи
"""
from core import neuro_tg as tg


async def run(id: str = '') -> str:
    if str(id).strip().lower() in ('all', 'все', '*'):
        return f"Снято задач: {tg.jobs_clear()}"
    if not str(id).strip():
        return "Ошибка: какой номер?"
    return "Снято" if tg.jobs_remove(id) else f"Задачи #{id} нет"
