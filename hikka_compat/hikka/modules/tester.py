"""Встроенный модуль пространства Hikka: логи модулей, пинг, приостановка."""

import asyncio
import io
import logging
import time

from telethon.tl.types import Message

from .. import loader, log, utils

logger = logging.getLogger(__name__)

LEVELS = {"debug": 10, "info": 20, "warning": 30, "warn": 30, "error": 40, "critical": 50}


@loader.tds
class TesterMod(loader.Module):
    """Логи, пинг и служебные команды пространства Hikka"""

    strings = {
        "name": "Tester",
        "no_logs": "ℹ️ <b>Логов уровня {} и выше нет</b>",
        "logs": "📄 <b>Логи модулей Hikka</b> (уровень {} и выше)",
        "ping": "⏱ <b>Ответ Telegram:</b> <code>{:.1f}</code> мс\n🕐 <b>Пространство работает:</b> {}",
        "suspend": "🥶 <b>Модули Hikka заморожены на</b> <code>{}</code> <b>с</b>",
        "suspend_args": "🚫 <b>Укажите время в секундах</b>",
    }

    logchat = None          # в Hikka — чат логов; в FRX ошибки приходят вам в личку от бота FRX

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "tglog_level", "ERROR",
                "С какого уровня записи логов модулей Hikka приходят вам в личку от бота FRX (ошибки — с кнопкой "
                "«Полный traceback»). INFO и WARNING — много сообщений",
                validator=loader.validators.Choice(["INFO", "WARNING", "ERROR", "CRITICAL"]),
                on_change=self._pass_config_to_logger,
            ),
            loader.ConfigValue(
                "ignore_common", True,
                "Не присылать типичные неопасные ошибки Telethon (InputPeerEmpty и т. п.)",
                validator=loader.validators.Boolean(),
                on_change=self._pass_config_to_logger,
            ),
        )

    def _pass_config_to_logger(self):
        from .. import _frx

        space = _frx.runtime
        if space is None:
            return
        space.tg_level = LEVELS.get(str(self.config["tglog_level"]).lower(), 40)
        space.tg_ignore_common = bool(self.config["ignore_common"])

    async def client_ready(self):
        self.logchat = self._tg_id
        self._pass_config_to_logger()

    @loader.command(ru_doc="очистить логи модулей Hikka")
    async def clearlogs(self, message: Message):
        """Clear Hikka modules logs"""
        from .. import _frx

        space = id(_frx.current())
        for record in list(log._BUFFER):
            if getattr(record, "hikka_space", space) == space:
                log._BUFFER.remove(record)
        await utils.answer(message, "🗑 <b>Логи очищены</b>")

    @loader.command(ru_doc="ответом на сообщение — показать его внутреннее устройство (Telethon)")
    async def dump(self, message: Message):
        """Show the replied message's raw object"""
        reply = await message.get_reply_message()
        if not reply:
            await utils.answer(message, "🚫 <b>Ответьте на сообщение</b>")
            return
        await utils.answer(message, "<code>" + utils.escape_html(reply.stringify()) + "</code>")

    @loader.command(ru_doc="[уровень: debug/info/warning/error] — логи модулей Hikka файлом")
    async def logs(self, message: Message):
        """[level] - send Hikka modules logs"""
        arg = (utils.get_args_raw(message) or "info").strip().lower()
        level = int(arg) if arg.isdigit() else LEVELS.get(arg, 20)
        text = log.format_records(level, 1500)
        name = logging.getLevelName(level)
        if not text:
            await utils.answer(message, self.strings("no_logs").format(name))
            return
        file = io.BytesIO(text.encode("utf-8"))
        file.name = "hikka-logs.txt"
        await utils.answer_file(message, file, caption=self.strings("logs").format(name))

    @loader.command(ru_doc="задержка до Telegram")
    async def ping(self, message: Message):
        """Telegram latency"""
        start = time.perf_counter()
        message = (await utils.answer(message, "🌘")) or message
        latency = (time.perf_counter() - start) * 1000
        await utils.answer(message, self.strings("ping").format(latency, utils.formatted_uptime()))

    @loader.command(ru_doc="<секунды> — заморозить модули Hikka на время")
    async def suspend(self, message: Message):
        """<seconds> - suspend Hikka modules"""
        args = utils.get_args_raw(message)
        if not args or not args.isdigit():
            await utils.answer(message, self.strings("suspend_args"))
            return
        seconds = int(args)
        await utils.answer(message, self.strings("suspend").format(seconds))
        self._client.dispatcher.suspended_until = time.time() + seconds
