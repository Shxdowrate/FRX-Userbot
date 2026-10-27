"""
name: find_contact
category: telegram
title: Найти человека
description: ищет человека или чат по имени среди диалогов и контактов, понимает падежи и уменьшительные («кате» → Катя, Екатерина, Kate). Возвращает варианты: имя, @username, id, телефон, когда была переписка. Используй, когда пользователь называет человека по имени
args: name: string, limit?: int
example: {"name": "Катя"}
keywords: найди контакт, кто такая, кто такой, найди человека, контакт, номер телефона
"""
from core import neuro_tg as tg


async def run(name: str = '', limit: int = 6) -> str:
    if not str(name).strip():
        return "Ошибка: не указано имя"
    cands = await tg.find_people(str(name), limit=max(1, min(int(limit or 6), 15)))
    if not cands:
        return f"Никого похожего на «{name}» среди диалогов и контактов"
    return '\n'.join(f"{i}. {c['line']}" for i, c in enumerate(cands, 1))
