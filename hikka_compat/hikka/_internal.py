"""Служебные функции: защита от флуда, перезапуск/остановка (через FRX)."""

import asyncio
import random

from . import _frx


async def fw_protect():
    """Небольшая пауза между «тяжёлыми» запросами к Telegram (в Sandbox Telegram нет — без паузы)."""
    if _frx.is_sandbox():
        return
    await asyncio.sleep(random.randint(1000, 3000) / 1000)


def get_startup_callback():
    return restart


def restart(*_, **__):
    """Перезапуск — перезапускается весь FRX (через SystemKeeper); в Sandbox — только пространство песочницы."""
    bridge = _frx.current().sandbox
    if bridge is not None:
        bridge.restart_space()
        return
    if _frx.engine is not None:
        _frx.engine.request_shutdown(42)


def die(*_, **__):
    """Остановка — останавливается весь FRX; в Sandbox — только пространство песочницы."""
    bridge = _frx.current().sandbox
    if bridge is not None:
        bridge.stop_space()
        return
    if _frx.engine is not None:
        _frx.engine.request_shutdown(0)


def print_banner(banner: str):
    _frx.tlog(banner)
