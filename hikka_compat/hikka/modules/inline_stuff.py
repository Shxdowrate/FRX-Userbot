"""Встроенный модуль пространства Hikka: служебная работа инлайна (галереи из инлайн-запросов, эхо ввода)."""

import contextlib
import logging

from telethon.tl.types import Message

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class InlineStuffMod(loader.Module):
    """Служебные функции инлайн-бота"""

    strings = {
        "name": "InlineStuff",
        "bot_info": "🤖 <b>Инлайн-бот пространства:</b> @{}\n<i>Это стационарный бот FRX.</i>",
        "no_bot": "🚫 <b>Стационарный бот FRX не запущен — инлайн-формы Hikka недоступны</b>",
    }

    @loader.watcher("out", "only_inline")
    async def watcher(self, message: Message):
        if not self.inline.init_complete or message.via_bot_id != self.inline.bot_id:
            return
        text = message.raw_text or ""
        if "#id: " in text:
            with contextlib.suppress(Exception):
                await self.inline.open_query_gallery(message)
            return
        if "Передаю значение юзерботу" in text or "Transferring value to userbot" in text:
            with contextlib.suppress(Exception):
                await message.delete()

    @loader.command(ru_doc="какой бот обслуживает инлайн Hikka")
    async def ch_hikka_bot(self, message: Message):
        """show inline bot"""
        if not self.inline.init_complete:
            await utils.answer(message, self.strings("no_bot"))
            return
        await utils.answer(message, self.strings("bot_info").format(self.inline.bot_username))
