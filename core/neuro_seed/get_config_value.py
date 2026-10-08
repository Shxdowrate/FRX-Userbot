"""
name: get_config_value
category: system
title: Значение конфига
description: читает настройку модуля FRX (module — MODULE_NAME, key — ключ конфига); только чтение
args: module: string, key: string
example: {"module": "web_panel", "key": "autostop_minutes"}
keywords: настройка модуля, конфиг модуля, значение конфига
"""
import engine


async def run(module: str = '', key: str = '') -> str:
    if not module or not key:
        return "Ошибка: нужны module и key"
    try:
        return f"{module}.{key} = {await engine.get_config(key, module_name=module)!r}"
    except Exception as e:
        return f"Ошибка: {e}"
