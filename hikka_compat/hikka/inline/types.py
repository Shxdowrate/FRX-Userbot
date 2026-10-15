"""Типы инлайн-интерфейса Hikka: InlineMessage, InlineCall, BotInlineCall, InlineQuery и др.

В Hikka они наследуются от объектов aiogram 2. В FRX стационарный бот — aiogram 3, его объекты неизменяемы,
поэтому здесь — обычные классы-обёртки: всё, чего нет в обёртке, берётся у исходного объекта aiogram.
"""

import logging
import typing

logger = logging.getLogger(__name__)


class InlineUnit:
    """Служебный тип (как в Hikka)."""


class InlineMessage:
    """Сообщение, отправленное через инлайн-бота (форма, галерея, список)."""

    def __init__(self, inline_manager, unit_id: str, inline_message_id: typing.Optional[str]):
        self.inline_message_id = inline_message_id
        self.unit_id = unit_id
        self.inline_manager = inline_manager
        self._units = inline_manager._units
        self.form = {"id": unit_id, **self._units[unit_id]} if unit_id in self._units else {}

    async def edit(self, *args, **kwargs) -> "InlineMessage":
        kwargs.pop("unit_id", None)
        kwargs.pop("inline_message_id", None)
        return await self.inline_manager._edit_unit(
            *args, unit_id=self.unit_id, inline_message_id=self.inline_message_id, **kwargs
        )

    async def delete(self) -> bool:
        return await self.inline_manager._delete_unit_message(self, unit_id=self.unit_id)

    async def unload(self) -> bool:
        return await self.inline_manager._unload_unit(unit_id=self.unit_id)

    def __repr__(self):
        return f"<{type(self).__name__} unit={self.unit_id} inline_message_id={self.inline_message_id}>"


class BotInlineMessage:
    """Сообщение, отправленное самим ботом (не через инлайн)."""

    def __init__(self, inline_manager, unit_id: typing.Optional[str], chat_id: int, message_id: int):
        self.chat_id = chat_id
        self.unit_id = unit_id
        self.inline_manager = inline_manager
        self.message_id = message_id
        self._units = inline_manager._units
        self.form = {"id": unit_id, **self._units[unit_id]} if unit_id and unit_id in self._units else {}

    async def edit(self, *args, **kwargs) -> "BotInlineMessage":
        for key in ("unit_id", "message_id", "chat_id"):
            kwargs.pop(key, None)
        return await self.inline_manager._edit_unit(
            *args, unit_id=self.unit_id, chat_id=self.chat_id, message_id=self.message_id, **kwargs
        )

    async def delete(self) -> bool:
        return await self.inline_manager._delete_unit_message(
            self, unit_id=self.unit_id, chat_id=self.chat_id, message_id=self.message_id
        )

    async def unload(self, *args, **kwargs) -> bool:
        kwargs.pop("unit_id", None)
        return await self.inline_manager._unload_unit(unit_id=self.unit_id)


class _CallMixin:
    """Общее для InlineCall / BotInlineCall: поля CallbackQuery и answer()."""

    _CALL_FIELDS = ("id", "from_user", "message", "inline_message_id", "chat_instance", "data", "game_short_name")

    def _take_call(self, call):
        self.original_call = call
        for attr in self._CALL_FIELDS:
            if attr == "inline_message_id" and isinstance(self, BotInlineMessage):
                continue
            object.__setattr__(self, attr, getattr(call, attr, None))
        user = getattr(call, "from_user", None)
        self.sender_id = getattr(user, "id", None)

    async def answer(self, text: typing.Optional[str] = None, show_alert: bool = False, *args, **kwargs):
        alert = kwargs.pop("alert", None)
        if alert is not None:
            show_alert = alert
        url = kwargs.pop("url", None)
        cache_time = kwargs.pop("cache_time", None)
        if args:                                    # aiogram 2: answer(text, show_alert, url, cache_time)
            url = args[0] if len(args) > 0 else url
            cache_time = args[1] if len(args) > 1 else cache_time
        answer = getattr(self.original_call, "answer", None)
        if answer is None:                          # ChosenInlineResult (кнопка ввода) — отвечать нечем
            return False
        try:
            return await answer(text=text, show_alert=bool(show_alert), url=url, cache_time=cache_time)
        except Exception as e:
            logger.debug("call.answer не удался: %s", e)
            return False

    def __getattr__(self, item):
        call = self.__dict__.get("original_call")
        if call is None or item.startswith("__"):
            raise AttributeError(item)
        return getattr(call, item)


class InlineCall(_CallMixin, InlineMessage):
    """Нажатие на кнопку инлайн-сообщения (формы, галереи, списка)."""

    def __init__(self, call, inline_manager, unit_id: typing.Optional[str]):
        self._take_call(call)
        InlineMessage.__init__(self, inline_manager, unit_id, getattr(call, "inline_message_id", None))
        self.chat_id = None
        self.message_id = None


class BotInlineCall(_CallMixin, BotInlineMessage):
    """Нажатие на кнопку сообщения, которое бот отправил сам."""

    def __init__(self, call, inline_manager, unit_id: typing.Optional[str]):
        self._take_call(call)
        message = getattr(call, "message", None)
        chat = getattr(message, "chat", None)
        self.chat = chat
        BotInlineMessage.__init__(
            self, inline_manager, unit_id, getattr(chat, "id", None), getattr(message, "message_id", None)
        )
        self.inline_message_id = None


class BotMessage:
    """Сообщение бота (aiogram) — для аннотаций типов в модулях."""


_NOTHING = "😶‍🌫️ <i>There is nothing here...</i>"

_ICONS = {
    400: "https://img.icons8.com/color/344/swearing-male--v1.png",
    403: "https://img.icons8.com/external-wanicon-flat-wanicon/344/external-forbidden-new-normal-wanicon-flat-wanicon.png",
    404: "https://img.icons8.com/external-justicon-flat-justicon/344/external-404-error-responsive-web-design-justicon-flat-justicon.png",
    426: "https://img.icons8.com/fluency/344/approve-and-update.png",
    500: "https://img.icons8.com/fluency/344/high-priority.png",
}


class InlineQuery:
    """Инлайн-запрос к боту (@бот <команда> <аргументы>) — для @loader.inline_handler."""

    def __init__(self, inline_query, inline_manager=None):
        self.inline_query = inline_query
        self.inline_manager = inline_manager
        self.id = inline_query.id
        self.from_user = inline_query.from_user
        self.query = inline_query.query or ""
        self.text = self.query
        self.offset = getattr(inline_query, "offset", "")
        self.chat_type = getattr(inline_query, "chat_type", None)
        self.location = getattr(inline_query, "location", None)
        self.sender_id = getattr(self.from_user, "id", None)
        parts = self.query.split(maxsplit=1)
        self.args = parts[1] if len(parts) > 1 else ""

    def __getattr__(self, item):
        query = self.__dict__.get("inline_query")
        if query is None or item.startswith("__"):
            raise AttributeError(item)
        return getattr(query, item)

    async def answer(self, results=None, cache_time: int = 0, is_personal: bool = True, **kwargs):
        from .core import convert_results

        kwargs.pop("private", None)
        results = convert_results(results or [], self.inline_manager)
        try:
            return await self.inline_query.answer(results, cache_time=cache_time, is_personal=is_personal, **kwargs)
        except Exception:
            logger.exception("Не удалось ответить на инлайн-запрос")
            return False

    def _get_res(self, title: str, description: str, thumb_url: str) -> list:
        from aiogram.types import InlineQueryResultArticle, InputTextMessageContent

        from .. import utils

        return [
            InlineQueryResultArticle(
                id=utils.rand(20),
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(message_text=_NOTHING, parse_mode="HTML"),
                thumbnail_url=thumb_url,
                thumbnail_width=128,
                thumbnail_height=128,
            )
        ]

    async def _err(self, code: int, text: str):
        await self.answer(self._get_res(f"🚫 {code}", text, _ICONS[code]), cache_time=0)

    async def e400(self):
        await self._err(400, "Bad request. You need to pass right arguments, follow module's documentation")

    async def e403(self):
        await self._err(403, "You have no permissions to access this result")

    async def e404(self):
        await self._err(404, "No results found")

    async def e426(self):
        await self._err(426, "You need to update userbot before sending this request")

    async def e500(self):
        await self._err(500, "Internal userbot error while processing request. More info in logs")


# Совместимость с GeekTG (from ..inline import GeekInlineQuery)
GeekInlineQuery = InlineQuery

__all__ = [
    "InlineUnit",
    "InlineMessage",
    "BotInlineMessage",
    "InlineCall",
    "BotInlineCall",
    "BotMessage",
    "InlineQuery",
    "GeekInlineQuery",
]
