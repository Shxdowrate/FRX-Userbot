"""
name: complete_task
category: memory
title: Выполнить задачу
description: отмечает задачу выполненной (id из list_tasks) или удаляет её (remove=true)
args: id: int, remove?: bool
keywords: задача выполнена, отметь выполненной, готово задача, удали задачу
"""
import db


async def run(id: int = 0, remove: bool = False) -> str:
    tasks = await db.get('neuro_tasks') or []
    for t in tasks:
        if t['id'] == int(id):
            if remove:
                tasks.remove(t)
            else:
                t['done'] = True
            await db.set('neuro_tasks', tasks)
            return f"Задача #{id} {'удалена' if remove else 'выполнена'}"
    return f"Задачи #{id} нет"
