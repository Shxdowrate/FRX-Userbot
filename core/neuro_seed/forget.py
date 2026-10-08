"""
name: forget
category: memory
title: Забыть
description: удаляет заметку из постоянной памяти по ключу
args: key: string
keywords: забудь, удали заметку, сотри из памяти
"""
import db


async def run(key: str = '') -> str:
    notes = await db.get('neuro_notes') or {}
    if key not in notes:
        return f"Заметки '{key}' нет"
    del notes[key]
    await db.set('neuro_notes', notes)
    return f"Заметка '{key}' удалена"
