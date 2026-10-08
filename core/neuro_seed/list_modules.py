"""
name: list_modules
category: system
title: Модули юзербота
description: список загруженных модулей FRX с версиями и числом команд; details=имя модуля — подробности по одному (команды, описание)
args: details?: string
keywords: какие модули, список модулей, что установлено, команды модуля, что умеет модуль
"""
import engine


async def run(details: str = '') -> str:
    names = engine.get_module_list()
    if details:
        key = engine.resolve_module(details) if hasattr(engine, 'resolve_module') else details
        if not key or key not in names:
            return f"Модуль «{details}» не найден"
        i = engine.get_module_info(key)
        cmds = ', '.join(c['command'] + (f" ({d})" if (d := c.get('description')) else '') for c in i.get('commands', [])) or 'нет команд'
        return f"{i.get('name')} v{i.get('version')} — {i.get('description')}\nавтор: {i.get('author')}; {'встроенный' if i.get('is_builtin') else 'загруженный'}\nкоманды: {cmds}"
    out = []
    for n in sorted(names):
        i = engine.get_module_info(n)
        out.append(f"{n} v{i.get('version')} ({'встроенный' if i.get('is_builtin') else 'загруженный'}, команд {len(i.get('commands', []))})")
    return '\n'.join(out)
