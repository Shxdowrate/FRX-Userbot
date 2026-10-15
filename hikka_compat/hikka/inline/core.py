"""InlineManager пространства Hikka (self.inline): формы, списки, галереи, инлайн-команды и кнопки.

Своего бота у пространства нет — используется стационарный бот FRX (aiogram 3). К его диспетчеру подключается
внешний middleware: апдейты, которые относятся к пространству Hikka (запрос формы по её id, нажатие кнопки
формы Hikka, выбор результата, инлайн-команды модулей Hikka), обрабатываются здесь и дальше в FRX не идут;
всё остальное FRX обрабатывает как обычно. Ядро FRX при этом не меняется — middleware снимается при выгрузке.

Важно (как и для форм FRX): у бота должен быть включён инлайн-режим (/setinline) и Inline Feedback 100%
(/setinlinefeedback) — без этого Telegram не сообщает id отправленной формы и её нельзя менять.
"""

import asyncio
import contextlib
import copy
import functools
import inspect
import logging
import os
import random
import time
import traceback
import typing
from urllib.parse import urlparse

from telethon.errors.rpcerrorlist import ChatSendInlineForbiddenError
from telethon.tl.types import Message

from .. import _frx, utils
from .bot import BotFacade, strip_custom_emoji
from .types import BotInlineCall, InlineCall, InlineMessage, InlineQuery, InlineUnit

logger = logging.getLogger(__name__)

VALID_BUTTON_STYLES = {"primary", "success", "danger", "default"}
_PLACEHOLDER_BUTTON = {"text": "­", "data": "­"}

VERIFICATION_EMOJIES = list("🐶🐱🐭🐹🐰🦊🐻🐼🐨🐯🦁🐮🐷🐸🐵🐔🐧🐦🦆🦅🦉🐺🐗🐴🦄🐝🦋🐌🐞🐢🐍🦎🐙🦑🦀🐠🐬🐳🦈🐊🐅🐆🦓🦍🐘🦒🐫🐄🐎🐑🐐🦌🐕🐈🐓🦃🦚🦜🦢🦩🌵🌲🌳🍁🍄🌸🌻🌞🌝🌈⭐⚡🔥❄🍏🍎🍐🍊🍋🍌🍉🍇🍓🍒🍑🥝🥥🍍")


class ListGalleryHelper:
    def __init__(self, lst):
        self.lst = lst
        self._current_index = -1

    def __call__(self) -> str:
        self._current_index += 1
        return self.lst[self._current_index % len(self.lst)]

    def by_index(self, index: int) -> str:
        return self.lst[index % len(self.lst)]


class Placeholder:
    """Маркер «разметку не трогали»."""


def _ext(url) -> str:
    try:
        return os.path.splitext(urlparse(url).path)[1].lower()
    except Exception:
        return ""


def convert_results(results, manager=None) -> list:
    """Результаты для answer_inline_query: объекты aiogram — как есть, словари формата Hikka — в объекты."""
    from aiogram import types as t

    out = []
    for res in results or []:
        if not isinstance(res, dict):
            out.append(res)
            continue
        markup = manager.generate_markup(res.get("reply_markup")) if manager is not None and res.get("reply_markup") else None
        clean = strip_custom_emoji
        rid = res.get("id") or utils.rand(20)
        if "message" in res:
            out.append(
                t.InlineQueryResultArticle(
                    id=rid,
                    title=clean(res.get("title") or "Hikka"),
                    description=clean(res.get("description")),
                    input_message_content=t.InputTextMessageContent(
                        message_text=clean(res["message"]),
                        parse_mode="HTML",
                        link_preview_options=t.LinkPreviewOptions(is_disabled=True),
                    ),
                    thumbnail_url=res.get("thumb"),
                    thumbnail_width=128 if res.get("thumb") else None,
                    thumbnail_height=128 if res.get("thumb") else None,
                    reply_markup=markup,
                )
            )
        elif "photo" in res:
            out.append(
                t.InlineQueryResultPhoto(
                    id=rid, photo_url=res["photo"], thumbnail_url=res.get("thumb", res["photo"]),
                    title=clean(res.get("title")), description=clean(res.get("description")),
                    caption=clean(res.get("caption")), parse_mode="HTML", reply_markup=markup,
                )
            )
        elif "gif" in res:
            out.append(
                t.InlineQueryResultGif(
                    id=rid, gif_url=res["gif"], thumbnail_url=res.get("thumb", res["gif"]),
                    title=clean(res.get("title")), caption=clean(res.get("caption")), parse_mode="HTML",
                    reply_markup=markup,
                )
            )
        elif "video" in res:
            out.append(
                t.InlineQueryResultVideo(
                    id=rid, video_url=res["video"], mime_type=res.get("mime_type", "video/mp4"),
                    thumbnail_url=res.get("thumb", res["video"]), title=clean(res.get("title") or "Video"),
                    description=clean(res.get("description")), caption=clean(res.get("caption")),
                    parse_mode="HTML", reply_markup=markup,
                )
            )
        elif "file" in res:
            out.append(
                t.InlineQueryResultDocument(
                    id=rid, document_url=res["file"], mime_type=res.get("mime_type", "application/zip"),
                    title=clean(res.get("title") or "File"), description=clean(res.get("description")),
                    caption=clean(res.get("caption")), parse_mode="HTML", thumbnail_url=res.get("thumb"),
                    reply_markup=markup,
                )
            )
        elif "audio" in res:
            audio = res["audio"] if isinstance(res["audio"], dict) else {"url": res["audio"]}
            out.append(
                t.InlineQueryResultAudio(
                    id=rid, audio_url=audio["url"], title=clean(audio.get("title") or res.get("title") or "Audio"),
                    performer=audio.get("performer"), audio_duration=audio.get("duration"),
                    caption=clean(res.get("caption")), parse_mode="HTML", reply_markup=markup,
                )
            )
    return out


class InlineManager(InlineUnit):
    def __init__(self, client, db, allmodules):
        self._client = client
        self._db = db
        self._allmodules = allmodules
        self.translator = allmodules.translator
        self._units: typing.Dict[str, dict] = {}
        self._custom_map: typing.Dict[str, dict] = {}
        self.fsm: typing.Dict[str, str] = {}
        self._errors: typing.Dict[str, Exception] = {}
        self._markup_ttl = 60 * 60 * 24
        self.init_complete = False
        self._me = client.tg_id
        self._name = None
        self._token = None
        self._raw_bot = None
        self._dp = None
        self.bot = None
        self._bot = None
        self.bot_id = None
        self.bot_username = None
        self._task = None
        self._cleaner_task = None
        self._bot_update_handlers: typing.Dict[str, tuple] = {}
        self._web_auth_tokens: list = []

    # ================================================================== подключение к боту FRX
    async def attach(self, aiogram_bot, dp) -> bool:
        if self.init_complete:
            return True
        try:
            me = await aiogram_bot.get_me()
        except Exception:
            logger.exception("Стационарный бот FRX недоступен — инлайн Hikka выключен")
            return False
        self._raw_bot = aiogram_bot
        self._dp = dp
        self.bot = self._bot = BotFacade(aiogram_bot, self)
        self.bot_id = me.id
        self.bot_username = me.username
        self._name = me.first_name
        if dp is not None:                       # в Sandbox диспетчера нет — апдейты приносит мост песочницы
            dp.update.outer_middleware.register(self._middleware)
        self._cleaner_task = asyncio.ensure_future(self._cleaner())
        self.init_complete = True
        return True

    async def detach(self):
        if self._dp is not None:
            with contextlib.suppress(Exception):
                self._dp.update.outer_middleware.unregister(self._middleware)
        if self._cleaner_task:
            self._cleaner_task.cancel()
        for unit_id in list(self._units):
            with contextlib.suppress(Exception):
                await self._unload_unit(unit_id)
        self.init_complete = False

    _stop = detach

    async def _cleaner(self):
        while True:
            await asyncio.sleep(5)
            now = time.time()
            for unit_id, unit in list(self._units.items()):
                if (unit.get("ttl") or now + self._markup_ttl) < now:
                    await self._unload_unit(unit_id)
            for key, item in list(self._custom_map.items()):
                if item.get("ttl") and item["ttl"] < now:
                    self._custom_map.pop(key, None)

    # ================================================================== маршрутизация апдейтов
    async def _middleware(self, handler, update, data):
        try:
            consumed = await self._route(update)
        except Exception:
            logger.exception("Ошибка обработки апдейта бота в пространстве Hikka")
            consumed = False
        self._fire_update_handlers(update)
        if consumed:
            return None
        return await handler(update, data)

    def _frx_inline_names(self) -> set:
        engine = _frx.engine
        names = set()
        if _frx.is_sandbox():
            return names
        for attr in ("bot_inline_function_map", "bot_inline_query_map"):
            with contextlib.suppress(Exception):
                names |= {str(k).lower() for k in getattr(engine, attr, {}) or {}}
        return names

    def _is_input_query(self, query: str, user_id: int) -> typing.Optional[tuple]:
        first = query.split()[0] if query.split() else ""
        if not first:
            return None
        for unit_id, unit in self._units.items():
            for button in utils.array_sum(unit.get("buttons", []) or []):
                if isinstance(button, dict) and button.get("_switch_query") == first and "input" in button:
                    allowed = [self._me] + list(self._owners()) + unit.get("always_allow", [])
                    if user_id in allowed:
                        return unit_id, button
        return None

    def _owners(self):
        with contextlib.suppress(Exception):
            return list(self._client.dispatcher.security._owner)
        return [self._me]

    async def _route(self, update) -> bool:
        iq = getattr(update, "inline_query", None)
        if iq is not None:
            query = iq.query or ""
            if query in self._units:
                await self._answer_unit_query(iq, self._units[query])
                return True
            if self._is_input_query(query, iq.from_user.id):
                await self._form_input_query(iq)
                return True
            first = query.split()[0].lower() if query.split() else ""
            if first and first in self._allmodules.inline_handlers and first not in self._frx_inline_names():
                await self._inline_handler(iq)
                return True
            return False

        chosen = getattr(update, "chosen_inline_result", None)
        if chosen is not None:
            return await self._chosen_inline_handler(chosen)

        call = getattr(update, "callback_query", None)
        if call is not None:
            return await self._callback_query_handler(call)

        message = getattr(update, "message", None)
        if message is not None:
            await self._message_handler(message)
            return False
        return False

    def register_bot_update_handler(self, handler_id: str, update_type: str, handler):
        self._bot_update_handlers[handler_id] = (update_type, handler)

    def unregister_bot_update_handler(self, handler_id: str):
        self._bot_update_handlers.pop(handler_id, None)

    def _fire_update_handlers(self, update):
        for update_type, handler in list(self._bot_update_handlers.values()):
            obj = getattr(update, update_type, None)
            if obj is None:
                continue

            async def run(h=handler, o=obj):
                try:
                    await h(o)
                except Exception:
                    logger.exception("Ошибка в need_update-обработчике")

            asyncio.ensure_future(run())

    # ================================================================== сообщения боту (aiogram_watcher)
    async def _message_handler(self, message):
        if getattr(message.chat, "type", None) != "private" or (message.text or "").startswith("/start hikka"):
            return
        for mod in list(self._allmodules.modules):
            watcher = getattr(mod, "aiogram_watcher", None)
            if watcher is None:
                continue
            if message.text == "/start" and mod.__class__.__name__ != "InlineStuffMod":
                continue
            try:
                await watcher(message)
            except Exception:
                logger.exception("Ошибка в aiogram_watcher модуля %s", mod.__class__.__name__)

    # ================================================================== инлайн-команды модулей
    async def _inline_handler(self, inline_query):
        query = inline_query.query or ""
        cmd = query.split()[0].lower()
        func = self._allmodules.inline_handlers.get(cmd)
        if func is None or not await self.check_inline_security(func=func, user=inline_query.from_user.id):
            with contextlib.suppress(Exception):
                await inline_query.answer([], cache_time=0, is_personal=True)
            return
        instance = InlineQuery(inline_query, self)
        try:
            result = await func(instance)
        except Exception:
            logger.exception("Ошибка в инлайн-обработчике %s", cmd)
            await instance.e500()
            return
        if not result:
            return
        if isinstance(result, dict):
            result = [result]
        if not isinstance(result, list):
            logger.error("Инлайн-обработчик вернул %s, а нужен dict или list", type(result))
            await instance.e500()
            return
        for res in result:
            if isinstance(res, dict) and not any(k in res for k in ("message", "photo", "gif", "video", "file", "audio")):
                logger.error("В результате инлайн-обработчика нет message/photo/gif/video/file")
                await instance.e500()
                return
        try:
            await inline_query.answer(convert_results(result, self), cache_time=0, is_personal=True)
        except Exception:
            logger.exception("Не удалось ответить на инлайн-запрос %s", cmd)

    # ================================================================== кнопки
    def _call_obj(self, call, unit_id):
        message = getattr(call, "message", None)
        if getattr(message, "chat", None) is not None:
            return BotInlineCall(call, self, unit_id)
        return InlineCall(call, self, unit_id)

    async def _button_allowed(self, call, unit: dict, button: dict) -> bool:
        user = call.from_user.id
        if button.get("disable_security") or unit.get("disable_security"):
            return True
        if unit.get("force_me"):
            if user == self._me:
                return True
        elif "message" in unit:
            flags = unit.get("perms_map", lambda: self._client.dispatcher.security._default)()
            if await self.check_inline_security(func=flags, user=user):
                return True
        return user in self._owners() + list(unit.get("always_allow", [])) + list(button.get("always_allow", []))

    async def _callback_query_handler(self, call) -> bool:
        data = call.data or ""
        engine = _frx.engine
        imid = getattr(call, "inline_message_id", None)
        if imid and not _frx.is_sandbox() and imid in (getattr(engine, "_active_forms", None) or {}):
            return False                                    # кнопка формы FRX

        for unit_id, unit in list(self._units.items()):
            for button in utils.array_sum(unit.get("buttons", []) or []):
                if not isinstance(button, dict) or button.get("_callback_data") != data or "callback" not in button:
                    continue
                if not await self._button_allowed(call, unit, button):
                    with contextlib.suppress(Exception):
                        await call.answer(self.translator.getkey("inline.button403"))
                    return True
                try:
                    await button["callback"](
                        self._call_obj(call, unit_id), *button.get("args", ()), **button.get("kwargs", {})
                    )
                except Exception:
                    logger.exception("Ошибка в обработчике кнопки")
                    with contextlib.suppress(Exception):
                        await call.answer("Ошибка при обработке нажатия. Подробности — в логах", show_alert=True)
                return True

        if data in self._custom_map:
            item = self._custom_map[data]
            unit = {k: v for k, v in item.items() if k in ("force_me", "disable_security", "perms_map", "message", "always_allow")}
            if not await self._button_allowed(call, unit, {}):
                with contextlib.suppress(Exception):
                    await call.answer(self.translator.getkey("inline.button403"))
                return True
            try:
                await item["handler"](self._call_obj(call, item.get("unit_id")), *item.get("args", ()), **item.get("kwargs", {}))
            except Exception:
                logger.exception("Ошибка в обработчике кнопки")
                with contextlib.suppress(Exception):
                    await call.answer("Ошибка при обработке нажатия. Подробности — в логах", show_alert=True)
            return True

        ours = any(u.get("inline_message_id") == imid for u in self._units.values()) if imid else False
        handled = False
        for func in list(self._allmodules.callback_handlers.values()):
            if not await self.check_inline_security(func=func, user=call.from_user.id):
                continue
            handled = True
            try:
                await func(self._call_obj(call, None))
            except Exception:
                logger.exception("Ошибка в callback_handler")
        if ours:
            if data == "­" or data == "empty":
                with contextlib.suppress(Exception):
                    await call.answer()
            return True
        return False                                        # не наше — дальше обработает FRX

    async def _chosen_inline_handler(self, chosen) -> bool:
        query = chosen.query or ""
        unit = self._units.get(query)
        if unit is not None and isinstance(unit.get("future"), asyncio.Event):
            unit["inline_message_id"] = chosen.inline_message_id
            unit["future"].set()
            return True
        found = self._is_input_query(query, chosen.from_user.id)
        if found:
            unit_id, button = found
            value = query.split(maxsplit=1)[1] if len(query.split()) > 1 else ""
            try:
                await button["handler"](
                    InlineCall(chosen, self, unit_id), value, *button.get("args", ()), **button.get("kwargs", {})
                )
            except Exception:
                logger.exception("Ошибка в обработчике поля ввода")
            return True
        return False

    async def _form_input_query(self, inline_query):
        from aiogram import types as t

        found = self._is_input_query(inline_query.query, inline_query.from_user.id)
        if not found:
            return
        _, button = found
        await inline_query.answer(
            [
                t.InlineQueryResultArticle(
                    id=utils.rand(20),
                    title=strip_custom_emoji(button["input"]),
                    description=self.translator.getkey("inline.keep_id").format(random.choice(VERIFICATION_EMOJIES)),
                    input_message_content=t.InputTextMessageContent(
                        message_text="🔄 <b>Передаю значение юзерботу...</b>\n<i>Это сообщение удалится само</i>",
                        parse_mode="HTML",
                        link_preview_options=t.LinkPreviewOptions(is_disabled=True),
                    ),
                )
            ],
            cache_time=60,
            is_personal=True,
        )

    # ================================================================== проверки прав
    def _reverse_method_lookup(self, needle) -> typing.Optional[str]:
        for name, method in list(self._allmodules.inline_handlers.items()) + list(self._allmodules.callback_handlers.items()):
            if method == needle:
                return name
        return None

    async def check_inline_security(self, *, func, user: int) -> bool:
        return await self._client.dispatcher.security.check(
            message=None, func=func, user_id=user, inline_cmd=self._reverse_method_lookup(func)
        )

    def _find_caller_sec_map(self):
        try:
            caller = utils.find_caller()
            if not caller:
                return None
            return lambda: self._client.dispatcher.security.get_flags(getattr(caller, "__self__", caller))
        except Exception:
            return None

    # ================================================================== разметка
    def _normalize_markup(self, reply_markup):
        if isinstance(reply_markup, dict):
            return [[reply_markup]]
        if isinstance(reply_markup, list) and any(isinstance(i, dict) for i in reply_markup):
            return [reply_markup]
        return reply_markup

    def _validate_markup(self, buttons):
        if buttons is None:
            buttons = []
        if not isinstance(buttons, (list, dict)):
            logger.error("Разметка пропущена: неверный тип %s", type(buttons))
            return None
        buttons = self._normalize_markup(buttons)
        if not all(isinstance(row, list) and all(isinstance(b, dict) for b in row) for row in buttons):
            logger.error("Разметка пропущена: одна из кнопок — не dict")
            return None
        keys = ("url", "callback", "input", "data", "action", "switch_inline_query", "switch_inline_query_current_chat",
                "copy", "web_app")
        if not all(any(k in b for k in keys) for row in buttons for b in row):
            logger.error("Разметка пропущена: у кнопки нет url / callback / input / data / action")
            return None
        return buttons

    def _generate_markup(self, markup_obj):
        from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo

        if not markup_obj:
            return None
        if isinstance(markup_obj, InlineKeyboardMarkup):
            return markup_obj
        rows = self._units[markup_obj]["buttons"] if isinstance(markup_obj, str) else markup_obj
        rows = self._normalize_markup(rows)
        if not isinstance(rows, list):
            return None

        for row in rows:
            for button in row:
                if not isinstance(button, dict):
                    logger.error("Кнопка %r — не dict", button)
                    return None
                if "callback" not in button:
                    action = button.get("action")
                    if action == "close":
                        button["callback"] = self._close_unit_handler
                    elif action == "unload":
                        button["callback"] = self._unload_unit_handler
                    elif action == "answer":
                        if not button.get("message"):
                            logger.error("У кнопки %r нет message для ответа", button)
                            return None
                        button["callback"] = functools.partial(
                            self._answer_unit_handler, show_alert=button.get("show_alert", False), text=button["message"]
                        )
                if "callback" in button and "_callback_data" not in button:
                    button["_callback_data"] = utils.rand(30)
                    self._custom_map[button["_callback_data"]] = {
                        "handler": button["callback"],
                        **({"always_allow": button["always_allow"]} if button.get("always_allow") else {}),
                        **({"args": button["args"]} if button.get("args") else {}),
                        **({"kwargs": button["kwargs"]} if button.get("kwargs") else {}),
                        **({"force_me": True} if button.get("force_me") else {}),
                        **({"disable_security": True} if button.get("disable_security") else {}),
                        "ttl": round(time.time()) + self._markup_ttl,
                    }
                if "input" in button and "_switch_query" not in button:
                    button["_switch_query"] = utils.rand(10)

        keyboard = []
        for row in rows:
            line = []
            for button in row:
                try:
                    kwargs = {"text": strip_custom_emoji(str(button.get("text", "")) or "­")}
                    style = button.get("style")
                    if style in VALID_BUTTON_STYLES and style != "default":
                        kwargs["style"] = style
                    if button.get("emoji_id"):
                        with contextlib.suppress(Exception):
                            kwargs["icon_custom_emoji_id"] = str(button["emoji_id"]).strip()
                    if "url" in button:
                        if not utils.check_url(button["url"]):
                            logger.warning("Кнопка пропущена: неверная ссылка %r", button["url"])
                            continue
                        kwargs["url"] = button["url"]
                    elif "callback" in button:
                        kwargs["callback_data"] = button["_callback_data"]
                    elif "input" in button:
                        kwargs["switch_inline_query_current_chat"] = button["_switch_query"] + " "
                    elif "data" in button:
                        kwargs["callback_data"] = str(button["data"])[:64]
                    elif "switch_inline_query_current_chat" in button:
                        kwargs["switch_inline_query_current_chat"] = button["switch_inline_query_current_chat"]
                    elif "switch_inline_query" in button:
                        kwargs["switch_inline_query"] = button["switch_inline_query"]
                    elif "copy" in button:
                        kwargs["copy_text"] = CopyTextButton(text=str(button["copy"]))
                    elif "web_app" in button:
                        web_app = button["web_app"]
                        kwargs["web_app"] = WebAppInfo(url=web_app if isinstance(web_app, str) else web_app.get("url"))
                    else:
                        logger.warning("Кнопка пропущена: непонятный формат %r", button)
                        continue
                    try:
                        line.append(InlineKeyboardButton(**kwargs))
                    except Exception:
                        kwargs.pop("style", None)
                        kwargs.pop("icon_custom_emoji_id", None)
                        line.append(InlineKeyboardButton(**kwargs))
                except KeyError:
                    logger.exception("Ошибка сборки разметки")
                    return False
            if line:
                keyboard.append(line)
        return InlineKeyboardMarkup(inline_keyboard=keyboard) if keyboard else None

    generate_markup = _generate_markup

    def sanitise_text(self, text: str) -> str:
        return strip_custom_emoji(text)

    async def _close_unit_handler(self, call):
        await call.delete()

    async def _unload_unit_handler(self, call):
        await call.unload()

    async def _answer_unit_handler(self, call, text: str, show_alert: bool):
        await call.answer(text, show_alert=show_alert)

    def build_pagination(self, callback, total_pages: int, unit_id: str = None, current_page: int = None):
        if current_page is None:
            current_page = self._units[unit_id]["current_index"] + 1

        def btn(text, page):
            return {"text": str(text), "args": (page,), "callback": callback}

        if total_pages <= 5:
            return [[btn(f"· {n} ·" if n == current_page else n, n - 1) for n in range(1, total_pages + 1)]]
        if current_page <= 3:
            row = []
            for n in range(1, 6):
                if n == current_page:
                    row.append(btn(f"· {n} ·", n - 1))
                elif n == 4:
                    row.append(btn(f"{n} ›", n - 1))
                elif n == 5:
                    row.append(btn(f"{total_pages} »", total_pages - 1))
                else:
                    row.append(btn(n, n - 1))
            return [row]
        if current_page > total_pages - 3:
            row = [btn("« 1", 0), btn(f"‹ {total_pages - 3}", total_pages - 4)]
            for n in range(total_pages - 2, total_pages + 1):
                row.append(btn(f"· {n} ·" if n == current_page else n, n - 1))
            return [row]
        return [[
            btn("« 1", 0),
            btn(f"‹ {current_page - 1}", current_page - 2),
            btn(f"· {current_page} ·", current_page - 1),
            btn(f"{current_page + 1} ›", current_page),
            btn(f"{total_pages} »", total_pages - 1),
        ]]

    # ================================================================== правка / удаление / выгрузка
    @staticmethod
    def _target(inline_message_id, chat_id, message_id) -> dict:
        if inline_message_id:
            return {"inline_message_id": inline_message_id}
        return {"chat_id": chat_id, "message_id": message_id}

    async def _edit_unit(
        self,
        text: str = None,
        reply_markup=None,
        *,
        photo: str = None,
        file: str = None,
        video: str = None,
        audio=None,
        gif: str = None,
        mime_type: str = None,
        force_me: bool = None,
        disable_security: bool = None,
        always_allow: list = None,
        disable_web_page_preview: bool = True,
        query=None,
        unit_id: str = None,
        inline_message_id: str = None,
        chat_id: int = None,
        message_id: int = None,
        **_ignored,
    ) -> bool:
        from aiogram import types as t
        from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

        reply_markup = self._validate_markup(reply_markup) or []
        if text is not None and not isinstance(text, str):
            logger.error("text должен быть str, а не %s", type(text))
            return False
        if isinstance(audio, str):
            audio = {"url": audio}
        if isinstance(text, str):
            text = self.sanitise_text(text)
        if sum(x is not None for x in (photo, gif, file, video, audio)) > 1:
            logger.error("Можно передать только одно медиа")
            return False

        unit = self._units.get(unit_id) if unit_id else None
        if unit is not None:
            unit["buttons"] = reply_markup
            if isinstance(force_me, bool):
                unit["force_me"] = force_me
            if isinstance(disable_security, bool):
                unit["disable_security"] = disable_security
            if isinstance(always_allow, list):
                unit["always_allow"] = always_allow
            if text is not None:
                unit["text"] = text
        else:
            unit = {}

        if not chat_id or not message_id:
            inline_message_id = inline_message_id or unit.get("inline_message_id") or getattr(query, "inline_message_id", None)
        if not inline_message_id and not (chat_id and message_id):
            logger.warning(
                "Нельзя изменить сообщение без inline_message_id (форма без кнопок или у бота выключен Inline Feedback)"
            )
            return False

        if photo is not None and _ext(photo) in (".gif", ".mp4"):
            gif, photo = photo, None
        from .bot import to_input_file

        media_src = next((m for m in (photo, file, video, audio and audio.get("url"), gif) if m), None)
        media = None
        if media_src is not None:
            media_src = to_input_file(media_src, "upload.mp4")
            common = {"caption": text, "parse_mode": "HTML"}
            if file:
                media = t.InputMediaDocument(media=media_src, **common)
            elif photo:
                media = t.InputMediaPhoto(media=media_src, **common)
            elif audio:
                media = t.InputMediaAudio(media=media_src, title=audio.get("title"), performer=audio.get("performer"),
                                          duration=audio.get("duration"), **common)
            elif video:
                media = t.InputMediaVideo(media=media_src, **common)
            elif gif:
                media = t.InputMediaAnimation(media=media_src, **common)

        markup = self.generate_markup(reply_markup if isinstance(reply_markup, list) else unit.get("buttons", []))
        target = self._target(inline_message_id, chat_id, message_id)
        bot = self._raw_bot
        try:
            if media is None and text is None:
                if not reply_markup:
                    logger.error("Нужно передать text, медиа или reply_markup")
                    return False
                await bot.edit_message_reply_markup(**target, reply_markup=markup)
                return True
            if media is None:
                try:
                    await bot.edit_message_text(
                        text=text,
                        **target,
                        link_preview_options=t.LinkPreviewOptions(is_disabled=bool(disable_web_page_preview)),
                        reply_markup=markup,
                    )
                except TelegramBadRequest as e:
                    msg = str(e).lower()
                    if "not modified" in msg:
                        if query:
                            with contextlib.suppress(Exception):
                                await query.answer()
                        return False
                    if "no text in the message" in msg or "there is no text" in msg:
                        await bot.edit_message_caption(caption=text, **target, reply_markup=markup)
                        return True
                    raise
                return True
            await bot.edit_message_media(**target, media=media, reply_markup=markup)
            return True
        except TelegramRetryAfter as e:
            await asyncio.sleep(e.retry_after)
            return await self._edit_unit(
                text, reply_markup, photo=photo, file=file, video=video, audio=audio, gif=gif, mime_type=mime_type,
                query=query, unit_id=unit_id, inline_message_id=inline_message_id, chat_id=chat_id,
                message_id=message_id, disable_web_page_preview=disable_web_page_preview,
            )
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return False
            if "message_id_invalid" in str(e).lower() or "message to edit not found" in str(e).lower():
                if query:
                    with contextlib.suppress(Exception):
                        await query.answer("Сообщение, которое нужно было изменить, удалено :(")
                return False
            logger.exception("Не удалось изменить инлайн-сообщение")
            return False

    async def _delete_unit_message(self, call=None, unit_id: str = None, chat_id: int = None, message_id: int = None) -> bool:
        message = getattr(call, "message", None)
        if getattr(message, "chat", None) is not None:
            try:
                await self._raw_bot.delete_message(chat_id=message.chat.id, message_id=message.message_id)
            except Exception:
                return False
            return True
        if chat_id and message_id:
            try:
                await self._raw_bot.delete_message(chat_id=chat_id, message_id=message_id)
            except Exception:
                return False
            return True
        if not unit_id:
            unit_id = getattr(call, "unit_id", None)
        unit = self._units.get(unit_id)
        if not unit:
            return False
        try:
            await self._client.delete_messages(unit.get("peer") or unit.get("chat"), [unit["message_id"]])
        except Exception:
            logger.debug("Не удалось удалить инлайн-сообщение", exc_info=True)
            return False
        finally:
            await self._unload_unit(unit_id)
        return True

    async def _unload_unit(self, unit_id: str) -> bool:
        unit = self._units.pop(unit_id, None)
        if unit is None:
            return False
        for button in utils.array_sum(unit.get("buttons", []) or []):
            if isinstance(button, dict) and button.get("_callback_data"):
                self._custom_map.pop(button["_callback_data"], None)
        callback = unit.get("on_unload")
        if callable(callback):
            try:
                result = callback()
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.debug("on_unload формы упал", exc_info=True)
        return True

    # ================================================================== FSM (bot_pm)
    def set_fsm_state(self, user, state) -> bool:
        if not isinstance(user, (str, int)) or not isinstance(state, (str, bool)):
            return False
        if state:
            self.fsm[str(user)] = state
        else:
            self.fsm.pop(str(user), None)
        return True

    ss = set_fsm_state

    def get_fsm_state(self, user):
        if not isinstance(user, (str, int)):
            return False
        return self.fsm.get(str(user), False)

    gs = get_fsm_state

    def pop_web_auth_token(self, token: str) -> bool:
        if token in self._web_auth_tokens:
            self._web_auth_tokens.remove(token)
            return True
        return False

    # ================================================================== отправка юнитов
    NO_BOT = (
        "🚫 <b>Инлайн недоступен:</b> у FRX не запущен стационарный бот "
        "(нужен <code>aiogram_token</code>, инлайн-режим и Inline Feedback у бота)"
    )

    async def _require(self, message) -> bool:
        if self.init_complete:
            return True
        logger.warning("Модуль попросил инлайн-форму, но стационарный бот FRX не запущен")
        await self._say(message, self.NO_BOT)
        return False

    async def _status(self, message, key: str, silent: bool):
        if not isinstance(message, Message) or silent:
            return None
        try:
            return await (message.edit if message.out else message.respond)(
                "🌘" + self.translator.getkey(key),
                **({"reply_to": utils.get_topic(message)} if not message.out else {}),
            )
        except Exception:
            return None

    async def _say(self, message, text: str):
        with contextlib.suppress(Exception):
            if isinstance(message, Message):
                await (message.edit if message.out else message.respond)(text)
            else:
                await self._client.send_message(message, text)

    async def _invoke_unit(self, unit_id: str, message) -> Message:
        self._errors.pop(unit_id, None)
        try:
            results = await asyncio.wait_for(self._client.inline_query(self.bot_username, unit_id), timeout=15)
        except asyncio.TimeoutError:
            raise RuntimeError("Бот не ответил на инлайн-запрос (включён ли инлайн-режим у бота?)")
        error = self._errors.pop(unit_id, None)
        if error:
            raise error
        if not results:
            raise RuntimeError("Бот не вернул результат инлайн-запроса")
        if isinstance(message, Message):
            entity = message.peer_id
            reply_to = message.reply_to_msg_id
        else:
            entity = message
            reply_to = None
        return await results[0].click(entity, reply_to=reply_to)

    async def _finish_unit(self, unit_id: str, sent, message, status):
        unit = self._units.get(unit_id)
        if unit is None:
            return None
        future = unit.get("future")
        if future is not None:
            try:
                await asyncio.wait_for(future.wait(), timeout=20)
            except asyncio.TimeoutError:
                logger.warning(
                    "Бот не сообщил id отправленной формы — включите у бота Inline Feedback 100%% (/setinlinefeedback)"
                )
            unit.pop("future", None)
        if sent is not None:
            unit["chat"] = utils.get_chat_id(sent)
            unit["peer"] = sent.chat_id
            unit["message_id"] = sent.id
        if isinstance(message, Message) and message.out:
            with contextlib.suppress(Exception):
                await message.delete()
        if status is not None and isinstance(message, Message) and not message.out:
            with contextlib.suppress(Exception):
                await status.delete()
        return InlineMessage(self, unit_id, unit.get("inline_message_id"))

    async def _send_unit(self, unit_id: str, message, status) -> typing.Union[InlineMessage, bool]:
        try:
            sent = await self._invoke_unit(unit_id, message)
        except ChatSendInlineForbiddenError:
            self._units.pop(unit_id, None)
            await self._say(message, self.translator.getkey("inline.inline403"))
            return False
        except Exception:
            logger.exception("Не удалось отправить инлайн-сообщение")
            self._units.pop(unit_id, None)
            if self._db.get("hikka.main", "inlinelogs", True):
                tb = "\n".join(traceback.format_exc().splitlines()[-8:])
                await self._say(message, self.translator.getkey("inline.invoke_failed_logs").format(utils.escape_html(tb)))
            else:
                await self._say(message, self.translator.getkey("inline.invoke_failed"))
            return False
        return await self._finish_unit(unit_id, sent, message, status)

    async def _answer_unit_query(self, iq, unit: dict):
        """Ответ на инлайн-запрос <unit_id> от юзербота: сама форма / галерея / список."""
        from aiogram import types as t

        try:
            kind = unit.get("type")
            if kind == "form":
                markup = self.generate_markup(unit["uid"])
                text = unit.get("text") or ""
                if "photo" in unit:
                    res = t.InlineQueryResultPhoto(id=utils.rand(20), photo_url=unit["photo"], thumbnail_url=unit["photo"],
                                                   caption=text, parse_mode="HTML", reply_markup=markup)
                elif "gif" in unit:
                    res = t.InlineQueryResultGif(id=utils.rand(20), gif_url=unit["gif"], thumbnail_url=unit["gif"],
                                                 caption=text, parse_mode="HTML", reply_markup=markup)
                elif "video" in unit:
                    res = t.InlineQueryResultVideo(id=utils.rand(20), video_url=unit["video"], mime_type="video/mp4",
                                                   thumbnail_url=unit.get("thumb") or "https://img.icons8.com/cotton/452/moon-satellite.png",
                                                   title="Hikka", caption=text, parse_mode="HTML", reply_markup=markup)
                elif "file" in unit:
                    res = t.InlineQueryResultDocument(id=utils.rand(20), title="Hikka", document_url=unit["file"],
                                                      mime_type=unit.get("mime_type") or "application/zip",
                                                      caption=text, parse_mode="HTML", reply_markup=markup)
                elif "location" in unit:
                    res = t.InlineQueryResultLocation(id=utils.rand(20), latitude=unit["location"][0],
                                                      longitude=unit["location"][1], title="Hikka", reply_markup=markup)
                elif "audio" in unit:
                    audio = unit["audio"]
                    res = t.InlineQueryResultAudio(id=utils.rand(20), audio_url=audio["url"], title=audio.get("title") or "Hikka",
                                                   performer=audio.get("performer"), audio_duration=audio.get("duration"),
                                                   caption=text, parse_mode="HTML", reply_markup=markup)
                else:
                    res = t.InlineQueryResultArticle(
                        id=utils.rand(20), title="Hikka",
                        input_message_content=t.InputTextMessageContent(
                            message_text=text or "­", parse_mode="HTML",
                            link_preview_options=t.LinkPreviewOptions(is_disabled=True),
                        ),
                        reply_markup=markup,
                    )
                await iq.answer([res], cache_time=0, is_personal=True)
            elif kind == "gallery":
                photo = unit["photo_url"]
                common = dict(id=utils.rand(20), thumbnail_url="https://img.icons8.com/fluency/344/loading.png",
                              caption=self._get_caption(unit["uid"], 0), parse_mode="HTML",
                              reply_markup=self._gallery_markup(unit["uid"]), title="Gallery")
                if unit.get("gif") or _ext(photo) in (".gif", ".mp4"):
                    res = t.InlineQueryResultGif(gif_url=photo, **common)
                else:
                    res = t.InlineQueryResultPhoto(photo_url=photo, **common)
                await iq.answer([res], cache_time=0, is_personal=True)
            elif kind == "list":
                await iq.answer(
                    [
                        t.InlineQueryResultArticle(
                            id=utils.rand(20), title="Hikka",
                            input_message_content=t.InputTextMessageContent(
                                message_text=self.sanitise_text(unit["strings"][0]), parse_mode="HTML",
                                link_preview_options=t.LinkPreviewOptions(is_disabled=True),
                            ),
                            reply_markup=self._list_markup(unit["uid"]),
                        )
                    ],
                    cache_time=0,
                    is_personal=True,
                )
            else:
                await iq.answer([], cache_time=0, is_personal=True)
        except Exception as e:
            logger.exception("Не удалось ответить формой на инлайн-запрос")
            self._errors[unit.get("uid")] = e
            with contextlib.suppress(Exception):
                await iq.answer([], cache_time=0, is_personal=True)

    # ------------------------------------------------------------------ form
    async def form(
        self,
        text: str,
        message,
        reply_markup=None,
        *,
        force_me: bool = False,
        always_allow: list = None,
        manual_security: bool = False,
        disable_security: bool = False,
        ttl: int = None,
        on_unload=None,
        photo: str = None,
        gif: str = None,
        file: str = None,
        mime_type: str = None,
        video: str = None,
        location=None,
        audio=None,
        silent: bool = False,
        **_ignored,
    ):
        """Отправить инлайн-форму. → InlineMessage или False."""
        if not await self._require(message):
            return False
        reply_markup = [] if reply_markup is None else reply_markup
        always_allow = always_allow or []
        if not isinstance(text, str):
            logger.error("text формы должен быть str, а не %s", type(text))
            return False
        text = self.sanitise_text(text)
        if not isinstance(message, (Message, int)):
            if hasattr(message, "chat_id"):
                message = message.chat_id
            else:
                logger.error("message формы должен быть Message или int, а не %s", type(message))
                return False
        if photo is not None and _ext(photo) in (".gif", ".mp4"):
            gif, photo = photo, None
        if isinstance(audio, str):
            audio = {"url": audio}
        if sum(x is not None for x in (photo, gif, file, video, audio, location)) > 1:
            logger.error("Можно передать только одно медиа")
            return False
        reply_markup = self._validate_markup(reply_markup) or []
        if ttl is not None and not isinstance(ttl, int) and ttl:
            return False

        status = await self._status(message, "inline.opening_form", silent)
        unit_id = utils.rand(16)
        perms_map = None if manual_security else self._find_caller_sec_map()

        base_markup = Placeholder()
        if not reply_markup and not ttl:
            base_markup = copy.deepcopy(reply_markup) or None
            reply_markup = self._validate_markup(dict(_PLACEHOLDER_BUTTON))
        if not ttl and not any("callback" in b or "input" in b for row in reply_markup for b in row):
            ttl = 10 * 60

        self._units[unit_id] = {
            "type": "form",
            "text": text,
            "buttons": reply_markup,
            "caller": message,
            "chat": None,
            "message_id": None,
            "top_msg_id": utils.get_topic(message),
            "uid": unit_id,
            "on_unload": on_unload,
            "future": asyncio.Event(),
            **({"photo": photo} if photo else {}),
            **({"video": video} if video else {}),
            **({"gif": gif} if gif else {}),
            **({"file": file, "mime_type": mime_type} if file else {}),
            **({"location": location} if location else {}),
            **({"audio": audio} if audio else {}),
            **({"perms_map": perms_map} if perms_map else {}),
            **({"message": message} if isinstance(message, Message) else {}),
            **({"force_me": force_me} if force_me else {}),
            **({"disable_security": disable_security} if disable_security else {}),
            **({"ttl": round(time.time()) + ttl} if ttl else {}),
            **({"always_allow": always_allow} if always_allow else {}),
        }
        result = await self._send_unit(unit_id, message, status)
        if result and not isinstance(base_markup, Placeholder):
            await result.edit(text, reply_markup=base_markup)
        return result

    # ------------------------------------------------------------------ list
    async def list(
        self,
        message,
        strings: typing.List[str],
        *,
        force_me: bool = False,
        always_allow: list = None,
        manual_security: bool = False,
        disable_security: bool = False,
        ttl=False,
        on_unload=None,
        silent: bool = False,
        custom_buttons=None,
        **_ignored,
    ):
        """Постраничный список строк."""
        if not await self._require(message):
            return False
        if not isinstance(strings, list) or not strings or not all(isinstance(s, str) for s in strings):
            logger.error("strings списка должны быть непустым списком строк")
            return False
        if not isinstance(message, (Message, int)):
            message = getattr(message, "chat_id", message)
        custom_buttons = self._validate_markup(custom_buttons) or []
        status = await self._status(message, "inline.opening_list", silent)
        unit_id = utils.rand(16)
        perms_map = None if manual_security else self._find_caller_sec_map()
        self._units[unit_id] = {
            "type": "list",
            "caller": message,
            "chat": None,
            "message_id": None,
            "top_msg_id": utils.get_topic(message),
            "uid": unit_id,
            "current_index": 0,
            "strings": strings,
            "future": asyncio.Event(),
            **({"ttl": round(time.time()) + ttl} if ttl else {}),
            **({"force_me": force_me} if force_me else {}),
            **({"disable_security": disable_security} if disable_security else {}),
            **({"on_unload": on_unload} if callable(on_unload) else {}),
            **({"always_allow": always_allow} if always_allow else {}),
            **({"perms_map": perms_map} if perms_map else {}),
            **({"message": message} if isinstance(message, Message) else {}),
            **({"custom_buttons": custom_buttons} if custom_buttons else {}),
        }
        return await self._send_unit(unit_id, message, status)

    async def _list_page(self, call, page, unit_id: str = None):
        if page == "close":
            await self._delete_unit_message(call, unit_id=unit_id)
            return
        unit = self._units.get(unit_id)
        if unit is None:
            return
        if page < 0 or page >= len(unit["strings"]):
            await call.answer("Нет такой страницы", show_alert=True)
            return
        unit["current_index"] = page
        try:
            await self._raw_bot.edit_message_text(
                inline_message_id=call.inline_message_id,
                text=self.sanitise_text(unit["strings"][page]),
                reply_markup=self._list_markup(unit_id),
                parse_mode="HTML",
            )
            await call.answer()
        except Exception:
            logger.exception("Не удалось перелистнуть список")
            with contextlib.suppress(Exception):
                await call.answer("Ошибка", show_alert=True)

    def _list_markup(self, unit_id: str):
        callback = functools.partial(self._list_page, unit_id=unit_id)
        unit = self._units[unit_id]
        return self.generate_markup(
            unit.get("custom_buttons", [])
            + self.build_pagination(callback=callback, total_pages=len(unit["strings"]), unit_id=unit_id)
            + [[{"text": "🔻 Закрыть", "callback": callback, "args": ("close",)}]]
        )

    # ------------------------------------------------------------------ gallery
    async def gallery(
        self,
        message,
        next_handler,
        caption="",
        *,
        custom_buttons=None,
        force_me: bool = False,
        always_allow: list = None,
        manual_security: bool = False,
        disable_security: bool = False,
        ttl=False,
        on_unload=None,
        preload=False,
        gif: bool = False,
        silent: bool = False,
        _reattempt: bool = False,
        **_ignored,
    ):
        """Галерея фото/гиф с листанием."""
        if not await self._require(message):
            return False
        custom_buttons = self._validate_markup(custom_buttons) or []
        if isinstance(caption, list):
            caption = ListGalleryHelper(caption)
        if not isinstance(message, (Message, int)):
            message = getattr(message, "chat_id", message)
        if isinstance(next_handler, list):
            if not all(isinstance(i, str) for i in next_handler):
                return False
            next_handler = ListGalleryHelper(next_handler)
        try:
            photo_url = next_handler.lst if isinstance(next_handler, ListGalleryHelper) else await self._call_photo(next_handler)
        except Exception:
            logger.exception("Не удалось получить первое фото галереи")
            return False
        if not photo_url:
            return False

        unit_id = utils.rand(16)
        btn_call_data = utils.rand(10)
        perms_map = None if manual_security else self._find_caller_sec_map()
        self._units[unit_id] = {
            "type": "gallery",
            "caption": caption,
            "caller": message,
            "chat": None,
            "message_id": None,
            "top_msg_id": utils.get_topic(message),
            "uid": unit_id,
            "photo_url": photo_url if isinstance(photo_url, str) else photo_url[0],
            "next_handler": next_handler,
            "btn_call_data": btn_call_data,
            "photos": [photo_url] if isinstance(photo_url, str) else list(photo_url),
            "current_index": 0,
            "future": asyncio.Event(),
            **({"ttl": round(time.time()) + ttl} if ttl else {}),
            **({"force_me": force_me} if force_me else {}),
            **({"disable_security": disable_security} if disable_security else {}),
            **({"on_unload": on_unload} if callable(on_unload) else {}),
            **({"preload": preload} if preload else {}),
            **({"gif": gif} if gif else {}),
            **({"always_allow": always_allow} if always_allow else {}),
            **({"perms_map": perms_map} if perms_map else {}),
            **({"message": message} if isinstance(message, Message) else {}),
            **({"custom_buttons": custom_buttons} if custom_buttons else {}),
        }
        status = await self._status(message, "inline.opening_gallery", silent)
        result = await self._send_unit(unit_id, message, status)
        if result and not isinstance(next_handler, ListGalleryHelper):
            asyncio.ensure_future(self._load_gallery_photos(unit_id))
        return result

    async def _call_photo(self, callback):
        if isinstance(callback, str):
            return callback
        if isinstance(callback, list):
            return callback[0] if callback else False
        if callable(callback):
            result = callback()
            if inspect.isawaitable(result):
                result = await result
        else:
            logger.error("next_handler должен быть str, list или функцией")
            return False
        if not isinstance(result, (str, list)):
            logger.error("next_handler вернул %s, а нужен str или list", type(result))
            return False
        return result

    async def _load_gallery_photos(self, unit_id: str):
        unit = self._units.get(unit_id)
        if unit is None:
            return
        try:
            photo = await self._call_photo(unit["next_handler"])
        except Exception:
            logger.exception("next_handler галереи упал")
            return
        if not photo:
            return
        unit["photos"] += [photo] if isinstance(photo, str) else list(photo)
        if unit.get("preload") and len(unit["photos"]) - unit["current_index"] < unit["preload"]:
            asyncio.ensure_future(self._load_gallery_photos(unit_id))

    def _get_caption(self, unit_id: str, index: int = 0) -> str:
        caption = self._units[unit_id].get("caption", "")
        if isinstance(caption, ListGalleryHelper):
            return self.sanitise_text(caption.by_index(index))
        if isinstance(caption, str):
            return self.sanitise_text(caption)
        if callable(caption):
            with contextlib.suppress(Exception):
                return self.sanitise_text(caption())
        return ""

    def _get_current_media(self, unit_id: str):
        from aiogram import types as t

        unit = self._units[unit_id]
        try:
            media = unit["photos"][unit["current_index"]]
        except IndexError:
            media = unit["photos"][0]
        caption = self._get_caption(unit_id, index=unit["current_index"])
        if unit.get("gif") or _ext(media) in (".gif", ".mp4"):
            return t.InputMediaAnimation(media=media, caption=caption, parse_mode="HTML")
        return t.InputMediaPhoto(media=media, caption=caption, parse_mode="HTML")

    async def _gallery_page(self, call, page, unit_id: str = None):
        from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter

        unit = self._units.get(unit_id)
        if unit is None:
            return
        if page == "slideshow":
            await self._gallery_slideshow(call, unit_id)
            return
        if page == "close":
            await self._delete_unit_message(call, unit_id=unit_id)
            return
        if page < 0:
            await call.answer("Дальше назад некуда")
            return
        if page > len(unit["photos"]) - 1 and isinstance(unit["next_handler"], ListGalleryHelper):
            await call.answer("Дальше вперёд некуда")
            return
        unit["current_index"] = page
        if not isinstance(unit["next_handler"], ListGalleryHelper):
            if page >= len(unit["photos"]):
                await self._load_gallery_photos(unit_id)
            if page >= len(unit["photos"]):
                await call.answer("Не удалось загрузить следующее фото")
                return
            if len(unit["photos"]) - page < unit.get("preload", 0) // 2:
                asyncio.ensure_future(self._load_gallery_photos(unit_id))
        try:
            await self._raw_bot.edit_message_media(
                inline_message_id=call.inline_message_id,
                media=self._get_current_media(unit_id),
                reply_markup=self._gallery_markup(unit_id),
            )
        except TelegramRetryAfter as e:
            await call.answer(f"FloodWait: подождите {e.retry_after} с", show_alert=True)
        except TelegramBadRequest as e:
            if "not modified" in str(e).lower():
                return
            with contextlib.suppress(IndexError):
                del unit["photos"][unit["current_index"]]
            unit["current_index"] = max(0, unit["current_index"] - 1)
            if unit["photos"]:
                return await self._gallery_page(call, page, unit_id)
        except Exception:
            logger.exception("Не удалось сменить фото галереи")
            await call.answer("Ошибка", show_alert=True)

    async def _gallery_slideshow(self, call, unit_id: str):
        unit = self._units[unit_id]
        if not unit.get("slideshow"):
            unit["slideshow"] = True
            await self._raw_bot.edit_message_reply_markup(inline_message_id=call.inline_message_id,
                                                          reply_markup=self._gallery_markup(unit_id))
            await call.answer("✅ Слайдшоу включено")
            asyncio.ensure_future(self._gallery_slideshow_loop(call, unit_id))
        else:
            unit.pop("slideshow", None)
            await self._raw_bot.edit_message_reply_markup(inline_message_id=call.inline_message_id,
                                                          reply_markup=self._gallery_markup(unit_id))
            await call.answer("🚫 Слайдшоу выключено")

    async def _gallery_slideshow_loop(self, call, unit_id: str):
        while True:
            await asyncio.sleep(7)
            unit = self._units.get(unit_id)
            if unit is None or not unit.get("slideshow"):
                return
            if unit["current_index"] + 1 >= len(unit["photos"]) and isinstance(unit["next_handler"], ListGalleryHelper):
                unit.pop("slideshow", None)
                unit["current_index"] -= 1
            await self._gallery_page(call, unit["current_index"] + 1, unit_id=unit_id)

    def _gallery_markup(self, unit_id: str):
        callback = functools.partial(self._gallery_page, unit_id=unit_id)
        unit = self._units[unit_id]
        endless = not isinstance(unit["next_handler"], ListGalleryHelper)
        nav = []
        if unit["current_index"] > 0:
            nav.append({"text": "⏪", "callback": callback, "args": (unit["current_index"] - 1,)})
        if unit["current_index"] < len(unit["photos"]) - 1 or endless:
            nav.append({"text": "🛑" if unit.get("slideshow") else "⏱", "callback": callback, "args": ("slideshow",)})
            nav.append({"text": "⏩", "callback": callback, "args": (unit["current_index"] + 1,)})
        rows = list(unit.get("custom_buttons", []))
        if not endless:
            rows += self.build_pagination(unit_id=unit_id, callback=callback, total_pages=len(unit["photos"]))
        if nav:
            rows.append(nav)
        rows.append([{"text": "🔻 Закрыть", "callback": callback, "args": ("close",)}])
        return self.generate_markup(rows)

    # ------------------------------------------------------------------ query_gallery
    async def query_gallery(self, query, items: list, *, force_me: bool = False, disable_security: bool = False,
                            always_allow: list = None) -> bool:
        """Ответить на инлайн-запрос набором галерей (каждая откроется по выбору результата)."""
        from aiogram import types as t

        if not isinstance(items, list) or not all(
            isinstance(i, dict) and "title" in i and "description" in i and "next_handler" in i for i in items
        ):
            logger.error("Неверные items для query_gallery")
            return False
        results = []
        for item in items:
            try:
                thumb = await self._call_photo(item.get("thumb_handler") or item["next_handler"])
            except Exception:
                continue
            if isinstance(thumb, list):
                thumb = thumb[0] if thumb else None
            if not isinstance(thumb, str):
                continue
            id_ = utils.rand(16)
            self._custom_map[id_] = {
                "handler": item["next_handler"],
                "ttl": round(time.time()) + 120,
                **({"always_allow": always_allow} if always_allow else {}),
                **({"force_me": force_me} if force_me else {}),
                **({"disable_security": disable_security} if disable_security else {}),
                **({"caption": item["caption"]} if "caption" in item else {}),
            }
            results.append(
                t.InlineQueryResultArticle(
                    id=utils.rand(20),
                    title=strip_custom_emoji(item["title"]),
                    description=strip_custom_emoji(item["description"]),
                    input_message_content=t.InputTextMessageContent(
                        message_text=f"🌘 <b>Открываю галерею...</b>\n<i>#id: {id_}</i>",
                        parse_mode="HTML",
                        link_preview_options=t.LinkPreviewOptions(is_disabled=True),
                    ),
                    thumbnail_url=thumb,
                    thumbnail_width=128,
                    thumbnail_height=128,
                )
            )
        raw = getattr(query, "inline_query", query)
        await raw.answer(results, cache_time=0, is_personal=True)
        return True

    async def open_query_gallery(self, message) -> bool:
        """Сообщение «Открываю галерею… #id: X» от юзербота → открыть галерею на его месте."""
        import re

        found = re.search(r"#id: ([a-zA-Z0-9]+)", message.raw_text or "")
        if not found or found.group(1) not in self._custom_map:
            return False
        item = self._custom_map.pop(found.group(1))
        await self.gallery(
            message=message.chat_id,
            next_handler=item["handler"],
            caption=item.get("caption", ""),
            force_me=item.get("force_me", False),
            disable_security=item.get("disable_security", False),
            always_allow=item.get("always_allow"),
            silent=True,
        )
        with contextlib.suppress(Exception):
            await message.delete()
        return True
