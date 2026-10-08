"""
name: random_number
category: utils
title: Случайные числа
description: случайные целые числа в диапазоне (монетка: 0–1, кубик: 1–6); count — сколько штук
args: low?: int, high?: int, count?: int
example: {"low": 1, "high": 6, "count": 2}
keywords: случайн, рандом, брось кубик, подбрось монетку, выбери число, лотерея
"""
import secrets


async def run(low: int = 1, high: int = 100, count: int = 1) -> str:
    lo, hi, n = int(low), int(high), max(1, min(int(count), 50))
    if lo > hi:
        lo, hi = hi, lo
    vals = [lo + secrets.randbelow(hi - lo + 1) for _ in range(n)]
    return ', '.join(map(str, vals))
