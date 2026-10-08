"""
name: text_stats
category: utils
title: Статистика текста
description: считает символы, слова, строки, предложения и показывает самые частые слова
args: text: string
keywords: сколько слов, сколько символов, посчитай слова, длина текста, частота слов
"""
import re
from collections import Counter


async def run(text: str = '') -> str:
    t = text or ''
    words = re.findall(r'[\w\-]+', t.lower())
    top = Counter(w for w in words if len(w) > 3).most_common(5)
    no_spaces = len(re.sub(r'\s', '', t))
    sentences = len(re.findall(r'[.!?…]+(?:\s|$)', t))
    out = f"символов {len(t)} (без пробелов {no_spaces}), слов {len(words)}, строк {len(t.splitlines()) or 0}, предложений {sentences}"
    if top:
        out += ". Частые слова: " + ', '.join(f'{w} ×{n}' for w, n in top)
    return out
