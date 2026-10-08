"""
name: convert_units
category: math
title: Перевод единиц
description: переводит величины: длина (мм, см, м, км, дюйм, фут, ярд, миля), масса (г, кг, т, унция, фунт), объём (мл, л, галлон), скорость (м/с, км/ч, миля/ч, узел), данные (б, кб, мб, гб, тб), температура (c, f, k)
args: value: number, from_unit: string, to_unit: string
example: {"value": 5, "from_unit": "миля", "to_unit": "км"}
keywords: переведи в, сколько в, конвертируй, километр, миль, дюйм, фунт, градус цельсия, фаренгейт, мегабайт
"""
_U = {
    'мм': ('len', 0.001), 'mm': ('len', 0.001), 'см': ('len', 0.01), 'cm': ('len', 0.01), 'м': ('len', 1), 'm': ('len', 1), 'км': ('len', 1000), 'km': ('len', 1000),
    'дюйм': ('len', 0.0254), 'inch': ('len', 0.0254), 'in': ('len', 0.0254), 'фут': ('len', 0.3048), 'ft': ('len', 0.3048), 'foot': ('len', 0.3048),
    'ярд': ('len', 0.9144), 'yd': ('len', 0.9144), 'миля': ('len', 1609.344), 'mile': ('len', 1609.344), 'mi': ('len', 1609.344),
    'мг': ('mass', 0.000001), 'г': ('mass', 0.001), 'g': ('mass', 0.001), 'кг': ('mass', 1), 'kg': ('mass', 1), 'т': ('mass', 1000), 'тонна': ('mass', 1000),
    'унция': ('mass', 0.0283495), 'oz': ('mass', 0.0283495), 'фунт': ('mass', 0.45359237), 'lb': ('mass', 0.45359237),
    'мл': ('vol', 0.001), 'ml': ('vol', 0.001), 'л': ('vol', 1), 'l': ('vol', 1), 'галлон': ('vol', 3.78541), 'gal': ('vol', 3.78541), 'пинта': ('vol', 0.473176),
    'м/с': ('spd', 1), 'mps': ('spd', 1), 'км/ч': ('spd', 1 / 3.6), 'kmh': ('spd', 1 / 3.6), 'миля/ч': ('spd', 0.44704), 'mph': ('spd', 0.44704), 'узел': ('spd', 0.514444), 'kn': ('spd', 0.514444),
    'б': ('data', 1), 'байт': ('data', 1), 'b': ('data', 1), 'кб': ('data', 1024), 'kb': ('data', 1024), 'мб': ('data', 1024 ** 2), 'mb': ('data', 1024 ** 2),
    'гб': ('data', 1024 ** 3), 'gb': ('data', 1024 ** 3), 'тб': ('data', 1024 ** 4), 'tb': ('data', 1024 ** 4),
}
_T = {'c': 'c', 'ц': 'c', '°c': 'c', 'цельсий': 'c', 'f': 'f', 'ф': 'f', '°f': 'f', 'фаренгейт': 'f', 'k': 'k', 'к': 'k', 'кельвин': 'k'}


def _norm(u):
    u = (u or '').strip().lower().rstrip('.')
    for suffix in ('ей', 'ов', 'ы', 'и', 'а'):
        if u not in _U and u not in _T and u.endswith(suffix) and (u[:-len(suffix)] in _U or u[:-len(suffix)] in _T):
            u = u[:-len(suffix)]
    return u


async def run(value: float = 0, from_unit: str = '', to_unit: str = '') -> str:
    a, b = _norm(from_unit), _norm(to_unit)
    v = float(value)
    if a in _T and b in _T:
        c = v if _T[a] == 'c' else (v - 32) * 5 / 9 if _T[a] == 'f' else v - 273.15
        r = c if _T[b] == 'c' else c * 9 / 5 + 32 if _T[b] == 'f' else c + 273.15
        return f"{v:g} {from_unit} = {r:.4g} {to_unit}"
    if a not in _U or b not in _U:
        return f"Не знаю единицу: {from_unit if a not in _U else to_unit}. Поддержаны: " + ', '.join(sorted(set(_U) | set(_T)))
    if _U[a][0] != _U[b][0]:
        return f"Нельзя перевести {_U[a][0]} в {_U[b][0]}."
    r = v * _U[a][1] / _U[b][1]
    return f"{v:g} {from_unit} = {r:.6g} {to_unit}"
