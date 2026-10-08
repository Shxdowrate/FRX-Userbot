"""
name: statistics
category: math
title: Статистика чисел
description: сумма, среднее, медиана, минимум, максимум, разброс и стандартное отклонение списка чисел
args: numbers: string
example: {"numbers": "3, 5, 8, 13, 21"}
keywords: среднее, медиана, сумма чисел, статистика, максимум из, минимум из
"""
import re
import statistics as st


async def run(numbers: str = '') -> str:
    if isinstance(numbers, (list, tuple)):
        numbers = ' '.join(str(x) for x in numbers)
    vals = [float(x.replace(',', '.')) for x in re.findall(r'-?\d+(?:[.,]\d+)?', str(numbers))]
    if not vals:
        return "Ошибка: не нашёл чисел"
    out = [f"количество {len(vals)}", f"сумма {sum(vals):g}", f"среднее {st.mean(vals):.6g}", f"медиана {st.median(vals):g}",
           f"минимум {min(vals):g}", f"максимум {max(vals):g}", f"размах {max(vals) - min(vals):g}"]
    if len(vals) > 1:
        out.append(f"станд. отклонение {st.stdev(vals):.6g}")
    return ', '.join(out)
