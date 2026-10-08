"""
name: list_tasks
category: memory
title: Список задач
description: показывает список дел; all=true — вместе с выполненными
args: all?: bool
keywords: список дел, мои задачи, что мне делать, покажи задачи
"""
import db


async def run(all: bool = False) -> str:
    tasks = await db.get('neuro_tasks') or []
    shown = [t for t in tasks if all or not t['done']]
    if not shown:
        return "Задач нет"
    return '\n'.join(f"#{t['id']} [{'x' if t['done'] else ' '}] {t['text']}" + (f" (срок: {t['due']})" if t.get('due') else '') for t in shown)
