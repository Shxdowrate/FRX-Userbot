"""self.inline.bot — бот для модулей Hikka.

В Hikka это aiogram 2 Bot, в Heroku — похожая обёртка над Telethon. В FRX бот один — стационарный (aiogram 3).
Здесь — обёртка с интерфейсом aiogram 2 поверх него: тот же порядок позиционных аргументов, разметка
кнопок в формате Hikka (список словарей) превращается в клавиатуру, файлы (bytes, BytesIO, путь) — во
входные файлы aiogram 3. Всё остальное передаётся настоящему боту как есть.
"""

import io
import logging
import os
import re
import typing

logger = logging.getLogger(__name__)

_EMOJI_TAGS = re.compile(r"</?(?:emoji|tg-emoji)(?:\s[^>]*)?>")


def strip_custom_emoji(text):
    """Боты не могут слать премиум-эмодзи — оставляем обычный символ."""
    return _EMOJI_TAGS.sub("", text) if isinstance(text, str) else text


def to_input_file(value, default_name: str = "file"):
    from aiogram.types import BufferedInputFile, FSInputFile, InputFile

    if value is None or isinstance(value, InputFile):
        return value
    if isinstance(value, (bytes, bytearray)):
        return BufferedInputFile(bytes(value), filename=default_name)
    if isinstance(value, io.IOBase) or hasattr(value, "read"):
        try:
            value.seek(0)
        except Exception:
            pass
        data = value.read()
        if isinstance(data, str):
            data = data.encode("utf-8")
        name = os.path.basename(str(getattr(value, "name", "") or default_name)) or default_name
        return BufferedInputFile(data, filename=name)
    if hasattr(value, "data") and isinstance(getattr(value, "data"), (bytes, bytearray)):
        return BufferedInputFile(bytes(value.data), filename=getattr(value, "filename", default_name))
    if isinstance(value, os.PathLike) or (isinstance(value, str) and os.path.isfile(value)):
        return FSInputFile(str(value))
    return value                                     # URL или file_id


class BotFacade:
    _SEND_ORDER = {
        "send_message": ("chat_id", "text", "parse_mode", "entities", "disable_web_page_preview",
                         "disable_notification", "protect_content", "reply_to_message_id",
                         "allow_sending_without_reply", "reply_markup"),
        "send_photo": ("chat_id", "photo", "caption", "parse_mode", "caption_entities", "disable_notification",
                       "protect_content", "reply_to_message_id", "allow_sending_without_reply", "reply_markup"),
        "send_document": ("chat_id", "document", "thumb", "caption", "parse_mode", "caption_entities",
                          "disable_content_type_detection", "disable_notification", "protect_content",
                          "reply_to_message_id", "allow_sending_without_reply", "reply_markup"),
        "send_video": ("chat_id", "video", "duration", "width", "height", "thumb", "caption", "parse_mode",
                       "caption_entities", "supports_streaming", "disable_notification", "protect_content",
                       "reply_to_message_id", "allow_sending_without_reply", "reply_markup"),
        "send_animation": ("chat_id", "animation", "duration", "width", "height", "thumb", "caption", "parse_mode",
                           "caption_entities", "disable_notification", "protect_content", "reply_to_message_id",
                           "allow_sending_without_reply", "reply_markup"),
        "send_audio": ("chat_id", "audio", "caption", "parse_mode", "caption_entities", "duration", "performer",
                       "title", "thumb", "disable_notification", "protect_content", "reply_to_message_id",
                       "allow_sending_without_reply", "reply_markup"),
        "send_voice": ("chat_id", "voice", "caption", "parse_mode", "caption_entities", "duration",
                       "disable_notification", "protect_content", "reply_to_message_id",
                       "allow_sending_without_reply", "reply_markup"),
        "send_sticker": ("chat_id", "sticker", "disable_notification", "protect_content", "reply_to_message_id",
                         "allow_sending_without_reply", "reply_markup"),
        "send_video_note": ("chat_id", "video_note", "duration", "length", "thumb", "disable_notification",
                            "protect_content", "reply_to_message_id", "allow_sending_without_reply", "reply_markup"),
        "edit_message_text": ("text", "chat_id", "message_id", "inline_message_id", "parse_mode", "entities",
                              "disable_web_page_preview", "reply_markup"),
        "edit_message_caption": ("chat_id", "message_id", "inline_message_id", "caption", "parse_mode",
                                 "caption_entities", "reply_markup"),
        "edit_message_media": ("media", "chat_id", "message_id", "inline_message_id", "reply_markup"),
        "edit_message_reply_markup": ("chat_id", "message_id", "inline_message_id", "reply_markup"),
        "delete_message": ("chat_id", "message_id"),
        "answer_callback_query": ("callback_query_id", "text", "show_alert", "url", "cache_time"),
        "forward_message": ("chat_id", "from_chat_id", "message_id", "disable_notification", "protect_content"),
        "copy_message": ("chat_id", "from_chat_id", "message_id", "caption", "parse_mode", "caption_entities",
                         "disable_notification", "protect_content", "reply_to_message_id",
                         "allow_sending_without_reply", "reply_markup"),
    }
    _FILE_FIELDS = ("photo", "document", "video", "animation", "audio", "voice", "sticker", "video_note", "thumbnail")
    _TEXT_FIELDS = ("text", "caption")

    def __init__(self, bot, inline_manager):
        self._bot = bot
        self._inline = inline_manager

    def __repr__(self):
        return f"<HikkaBot {getattr(self._bot, 'id', '?')}>"

    @property
    def bot(self):
        return self._bot

    @property
    def client(self):
        return self._bot

    @property
    def id(self):
        return getattr(self._bot, "id", None) or self._inline.bot_id

    def _convert(self, method: str, args: tuple, kwargs: dict):
        order = self._SEND_ORDER.get(method)
        if order and args:
            for name, value in zip(order, args):
                kwargs.setdefault(name, value)
            args = ()
        if "thumb" in kwargs:
            kwargs.setdefault("thumbnail", kwargs.pop("thumb"))
        if "message_thread_id" in kwargs and kwargs["message_thread_id"] is None:
            kwargs.pop("message_thread_id")
        markup = kwargs.get("reply_markup")
        if isinstance(markup, (list, dict)):
            kwargs["reply_markup"] = self._inline.generate_markup(markup) if markup else None
        for field in self._FILE_FIELDS:
            if field in kwargs:
                kwargs[field] = to_input_file(kwargs[field], f"{field}.{'jpg' if field == 'photo' else 'bin'}")
        for field in self._TEXT_FIELDS:
            if field in kwargs:
                kwargs[field] = strip_custom_emoji(kwargs[field])
        media = kwargs.get("media")
        if media is not None and hasattr(media, "media"):
            try:
                media.media = to_input_file(media.media, "media")
                if getattr(media, "caption", None):
                    media.caption = strip_custom_emoji(media.caption)
            except Exception:
                pass
        return args, kwargs

    def __getattr__(self, item: str):
        attr = getattr(self._bot, item)
        if not callable(attr) or item.startswith("_"):
            return attr

        async def call(*args, **kwargs):
            a, k = self._convert(item, args, dict(kwargs))
            return await attr(*a, **k)

        return call

    async def __call__(self, method):
        return await self._bot(method)

    async def get_me(self):
        return await self._bot.get_me()
