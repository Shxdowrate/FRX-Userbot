"""
core/time_utils.py

get_time()             — текущее время
time_format(time, fmt) — форматирование времени в строку
edit_time(time, value) — изменение времени на смещение ('+1h', '-30m'...)
parse_interval(str)    — используется движком для разбора @cycle(interval=...)
"""

import re
from datetime import datetime, timedelta

_SECONDS_MAP = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}
_TIMEDELTA_UNIT = {'s': 'seconds', 'm': 'minutes', 'h': 'hours', 'd': 'days'}


def get_time():
    """Возвращает текущее время (datetime)."""
    return datetime.now()


def time_format(time_obj, fmt):
    """
    Форматирует время в строку по правилам strftime.

    Инструкция по созданию формата (основные токены):
        %Y  - год, 4 цифры        (2026)
        %y  - год, 2 цифры        (26)
        %m  - месяц, 2 цифры      (01-12)
        %d  - день, 2 цифры       (01-31)
        %H  - часы, 24ч формат    (00-23)
        %I  - часы, 12ч формат    (01-12)
        %M  - минуты              (00-59)
        %S  - секунды             (00-59)
        %A  - полное название дня недели   (Monday)
        %a  - краткое название дня недели  (Mon)
        %B  - полное название месяца       (January)
        %b  - краткое название месяца      (Jan)
        %p  - AM/PM

    Токены комбинируются в любом порядке вместе с обычным текстом:
        time_format(get_time(), '%d.%m.%Y %H:%M:%S') -> '22.08.2026 14:30:05'
        time_format(get_time(), '%A, %d %B')          -> 'Saturday, 22 August'

    time_obj может быть datetime либо unix-timestamp (int/float).
    """
    if isinstance(time_obj, (int, float)):
        time_obj = datetime.fromtimestamp(time_obj)
    return time_obj.strftime(fmt)


def parse_interval(interval: str) -> int:
    """
    Разбирает строку интервала ('1m', '30s', '2h', '1d') в секунды.
    Используется декоратором @cycle.
    """
    match = re.fullmatch(r'\s*(\d+)\s*([smhd])\s*', interval)
    if not match:
        raise ValueError(
            f"Некорректный формат интервала: '{interval}'. "
            f"Ожидается число + s/m/h/d, например '30s', '1m', '2h', '1d'"
        )
    value, unit = match.groups()
    return int(value) * _SECONDS_MAP[unit]


def edit_time(time_obj, value: str):
    """
    Изменяет указанное время на заданное смещение и возвращает новое.

    value — строка вида '+1h', '-30m', '+2d', '+45s'.

    Пример:
        edit_time(get_time(), '+1h')  # прибавляет 1 час к текущему времени
    """
    if isinstance(time_obj, (int, float)):
        time_obj = datetime.fromtimestamp(time_obj)

    match = re.fullmatch(r'\s*([+-])\s*(\d+)\s*([smhd])\s*', value)
    if not match:
        raise ValueError(
            f"Некорректный формат смещения: '{value}'. "
            f"Ожидается +/- число + s/m/h/d, например '+1h', '-30m'"
        )
    sign, amount, unit = match.groups()
    delta = timedelta(**{_TIMEDELTA_UNIT[unit]: int(amount)})
    return time_obj + delta if sign == '+' else time_obj - delta
