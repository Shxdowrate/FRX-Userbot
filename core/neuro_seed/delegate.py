"""
name: delegate
category: agent
title: Рабочий (выжимка)
description: позвать помощника на чтение, когда это ускорит ответ: прочитать много сообщений, найти, где что присылали, собрать сведения из чатов или интернета — вернёт выжимку до ~10 строк. Несколько независимых вопросов — tasks списком: выполняются одновременно. Ничего не отправляет и не меняет
args: task?: string, tasks?: list
example: {"tasks": ["курс доллара к рублю сейчас", "время сейчас в Благовещенске"]}
keywords: выжимк, перескажи чат, о чём спорили, о чем спорили, найди где, собери сведения
"""
# Главный агент (core/neuro.py) обрабатывает вызов delegate сам: показывает шаги рабочих в рамке хода и учитывает расход.
# Эта функция — запасной путь, если инструмент вызвали в обход агента.


async def run(task: str = '', tasks: list = None) -> str:
    from core.neuro import get_agent, _delegate_tasks
    info = await get_agent().delegate_many(_delegate_tasks({'task': task} if task and not tasks else {'tasks': tasks or []}))
    return info['text']
