"""
name: add_task
category: memory
title: Добавить задачу
description: добавляет задачу в список дел (хранится постоянно); due — срок текстом, необязательно
args: text: string, due?: string
example: {"text": "позвонить маме", "due": "завтра"}
keywords: добавь задачу, запиши в список дел, напомни сделать, todo, дела
"""
import time

import db


async def run(text: str = '', due: str = '') -> str:
    if not (text or '').strip():
        return "Ошибка: пустая задача"
    tasks = await db.get('neuro_tasks') or []
    nid = max([t['id'] for t in tasks], default=0) + 1
    tasks.append({'id': nid, 'text': text.strip(), 'due': due or '', 'done': False, 'created': int(time.time())})
    await db.set('neuro_tasks', tasks)
    return f"Задача #{nid} добавлена"
