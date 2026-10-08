"""
name: json_format
category: utils
title: Проверить JSON
description: проверяет JSON и возвращает его красиво отформатированным (или говорит, где ошибка)
args: text: string
keywords: json, отформатируй json, проверь json
"""
import json


async def run(text: str = '') -> str:
    try:
        return json.dumps(json.loads(text or ''), ensure_ascii=False, indent=2)
    except json.JSONDecodeError as e:
        return f"Некорректный JSON: строка {e.lineno}, столбец {e.colno}: {e.msg}"
