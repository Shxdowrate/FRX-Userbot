"""Встроенный модуль пространства Hikka: информация о пространстве."""

import platform
import sys

import telethon
from telethon.tl.types import Message

from .. import _frx, loader, utils, version


@loader.tds
class HikkaInfoMod(loader.Module):
    """Информация о пространстве Hikka в FRX"""

    strings = {
        "name": "HikkaInfo",
        "info": (
            "🌙 <b>Hikka в FRX</b> (HikkaCompat {})\n\n"
            "<b>Совместимость:</b> Hikka {} / Heroku {}\n"
            "<b>FRX:</b> {}\n"
            "<b>Префикс:</b> <code>{}</code>\n"
            "<b>Модулей:</b> {} (встроенных {})\n"
            "<b>Инлайн-бот:</b> {}\n"
            "<b>Работает:</b> {}\n"
            "<b>Платформа:</b> {} · Python {} · Telethon {}\n"
            "<b>Память:</b> {} МБ · <b>CPU:</b> {}%"
        ),
    }

    @loader.command(ru_doc="информация о пространстве Hikka")
    async def info(self, message: Message):
        """space info"""
        mods = self.allmodules.modules
        core = [m for m in mods if str(m.__origin__).startswith("<core")]
        try:
            frx = _frx.engine.get_version()
        except Exception:
            frx = "?"
        await utils.answer(
            message,
            self.strings("info").format(
                ".".join(map(str, version.compat_version)),
                ".".join(map(str, version.hikka_version)),
                ".".join(map(str, version.heroku_version)),
                utils.escape_html(frx),
                utils.escape_html(self.get_prefix()),
                len(mods) - len(core),
                len(core),
                f"@{self.inline.bot_username}" if self.inline.init_complete else "нет",
                utils.formatted_uptime(),
                utils.get_named_platform(),
                platform.python_version(),
                telethon.__version__,
                utils.get_ram_usage(),
                utils.get_cpu_usage(),
            ),
        )
