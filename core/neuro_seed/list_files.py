"""
name: list_files
category: files
title: Список файлов
description: показывает файлы и папки песочницы ai_files (path — подпапка, pattern — маска вроде *.txt)
args: path?: string, pattern?: string
example: {"pattern": "*.txt"}
keywords: какие файлы, список файлов, покажи файлы, что в папке
"""
from pathlib import Path

from core.paths import DATA_DIR

BASE = DATA_DIR / 'ai_files'


async def run(path: str = '', pattern: str = '*') -> str:
    BASE.mkdir(parents=True, exist_ok=True)
    root = (BASE / (path or '')).resolve()
    if BASE.resolve() not in root.parents and root != BASE.resolve():
        return "Путь выходит за пределы песочницы ai_files"
    if not root.is_dir():
        return f"Папки нет: {path}"
    items = sorted(root.rglob(pattern or '*'))[:200]
    if not items:
        return "Пусто"
    return '\n'.join(f"{'📁' if p.is_dir() else '📄'} {p.relative_to(BASE.resolve()).as_posix()}" + ('' if p.is_dir() else f" ({p.stat().st_size} Б)") for p in items)
