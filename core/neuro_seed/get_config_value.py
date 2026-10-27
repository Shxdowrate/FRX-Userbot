"""
name: get_config_value
category: system
title: Настройки модуля
description: настройки (конфиги) модуля FRX — только чтение. module — модуль (MODULE_NAME или название: «Config», «neurodash», «musics»); key — ключ; без key — все настройки модуля с описанием, типом и значением
args: module: string, key?: string
example: {"module": "musics"}
keywords: настройка модуля, настройки модуля, конфиг модуля, конфиги модуля, значение конфига, какие настройки у
"""
import engine


def _find(module):
    m = str(module or '').strip().lstrip('.')
    mods = getattr(engine, 'modules', {}) or {}
    if m in mods:
        return m
    low = m.lower()
    for name, info in mods.items():
        if low in (name.lower(), str(getattr(info, 'name', '')).lower()):
            return name
    hits = [n for n, i in mods.items() if low and (low in n.lower() or low in str(getattr(i, 'name', '')).lower())]
    return hits[0] if len(hits) == 1 else None


async def run(module: str = '', key: str = '') -> str:
    if not module:
        return "Ошибка: укажи module (какой модуль). Список модулей — list_modules"
    name = _find(module)
    if not name:
        return f"Модуль «{module}» не найден. Список модулей — list_modules"
    try:
        cfg = await engine.db.get(engine._config_key(name), {}) or {}
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    if not cfg:
        return f"У модуля {name} нет настроек"
    if key:
        k = next((x for x in cfg if x.lower() == str(key).lower()), None)
        if k is None:
            return f"У {name} нет настройки «{key}». Есть: {', '.join(cfg)}"
        return f"{name}.{k} = {await engine.get_config(k, module_name=name)!r}"
    lines = [f"Настройки модуля {name} ({len(cfg)}). Изменить — команда .config {name} (через run_command):"]
    for k, e in cfg.items():
        e = e if isinstance(e, dict) else {}
        try:
            val = await engine.get_config(k, module_name=name)
        except Exception:                                    # noqa: BLE001
            val = e.get('value')
        lines.append(f"• {k} = {val!r} [{e.get('data_type', '?')}] — {str(e.get('description', ''))[:120]}")
    return '\n'.join(lines)
