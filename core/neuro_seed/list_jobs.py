"""
name: list_jobs
category: agent
title: Мои задачи
description: список будильников и ожидаемых событий агента (номер, когда, что сделать)
args:
example: {}
keywords: какие задачи, мои будильники, что ждешь, что запланировал
"""
from core import neuro_tg as tg


async def run() -> str:
    jobs = tg.jobs_read()['jobs']
    return '\n'.join(tg.job_line(j) for j in jobs) or "Задач нет"
