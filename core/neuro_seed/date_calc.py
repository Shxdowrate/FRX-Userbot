"""
name: date_calc
category: time
title: Даты
description: расчёты с датами: разница между двумя датами, дата через N дней, день недели; даты в формате ГГГГ-ММ-ДД или ДД.ММ.ГГГГ, 'today' — сегодня
args: start: string, end?: string, days?: int
example: {"start": "today", "days": 45}
keywords: сколько дней до, через сколько дней, какой день недели, дата через, дней между, прошло дней
"""
import re
from datetime import date, datetime, timedelta

_WD = ['понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота', 'воскресенье']


def _d(s):
    s = (s or '').strip().lower()
    if s in ('today', 'сегодня', 'now', ''):
        return date.today()
    for fmt in ('%Y-%m-%d', '%d.%m.%Y', '%d/%m/%Y', '%d.%m.%y'):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"не понял дату «{s}» (нужно ГГГГ-ММ-ДД или ДД.ММ.ГГГГ)")


async def run(start: str = 'today', end: str = '', days: int = 0) -> str:
    try:
        a = _d(start)
        if end:
            b = _d(end)
            n = (b - a).days
            return f"{a} → {b}: {n} дн. ({n // 7} нед. {abs(n) % 7} дн.); {a} — {_WD[a.weekday()]}, {b} — {_WD[b.weekday()]}"
        if days:
            b = a + timedelta(days=int(days))
            return f"{a} {'+' if int(days) >= 0 else '−'} {abs(int(days))} дн. = {b} ({_WD[b.weekday()]})"
        return f"{a} — {_WD[a.weekday()]}, день года {a.timetuple().tm_yday}"
    except ValueError as e:
        return f"Ошибка: {e}"
