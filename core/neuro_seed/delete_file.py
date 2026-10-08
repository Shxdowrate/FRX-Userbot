"""
name: delete_file
category: files
title: Удалить файл
description: удаляет файл из песочницы ai_files
args: path: string
dangerous: true
keywords: удали файл
"""
from pathlib import Path

from core.paths import DATA_DIR

BASE = DATA_DIR / 'ai_files'


async def run(path: str = '') -> str:
    if not path:
        return "Ошибка: не передан путь"
    target = (BASE / path).resolve()
    if BASE.resolve() not in target.parents:
        return "Путь выходит за пределы песочницы ai_files"
    if not target.is_file():
        return f"Файл не найден: {path}"
    target.unlink()
    return f"Удалён: {path}"
