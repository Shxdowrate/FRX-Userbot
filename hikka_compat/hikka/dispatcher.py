"""Диспетчер пространства Hikka: команды с префиксом пространства, вотчеры, raw-обработчики, права, лимиты."""

import asyncio
import collections
import contextlib
import inspect
import logging
import re
import sys
import traceback
import typing

from telethon import events
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.types import Message

from . import _frx, security, utils

logger = logging.getLogger(__name__)

MAIN = "hikka.main"

# Переключение раскладки: «,хелп» набранное в русской раскладке = «?help» и т. п.
_LAYOUT = str.maketrans(
    'ёйцукенгшщзхъфывапролджэячсмитьбю.Ё"№;%:?ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭ/ЯЧСМИТЬБЮ,'
    + "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~@#$%^&QWERTYUIOP{}ASDFGHJKL:\"|ZXCVBNM<>?",
    "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~@#$%^&QWERTYUIOP{}ASDFGHJKL:\"|ZXCVBNM<>?"
    + 'ёйцукенгшщзхъфывапролджэячсмитьбю.Ё"№;%:?ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭ/ЯЧСМИТЬБЮ,',
)

ALL_TAGS = [
    "no_commands", "only_commands", "out", "in", "only_messages", "editable", "no_media", "only_media",
    "only_photos", "only_videos", "only_audios", "only_docs", "only_stickers", "only_inline", "only_channels",
    "only_groups", "only_pm", "no_pm", "no_channels", "no_groups", "no_inline", "no_stickers", "no_docs",
    "no_audios", "no_videos", "no_photos", "no_forwards", "no_reply", "no_mention", "mention", "only_reply",
    "only_forwards", "startswith", "endswith", "contains", "regex", "filter", "from_id", "chat_id", "thumb_url",
    "alias", "aliases",
]


class CommandDispatcher:
    def __init__(self, modules, client, db):
        self._modules = modules
        self._client = client
        self.client = client
        self._db = db
        self._rl_user = collections.defaultdict(int)
        self._rl_chat = collections.defaultdict(int)
        self._rl_max_user = db.get("hikka.dispatcher", "ratelimit_max_user", 30)
        self._rl_max_chat = db.get("hikka.dispatcher", "ratelimit_max_chat", 100)
        self.security = security.SecurityManager(client, db)
        self.check_security = self.security.check
        self.raw_handlers: list = []
        self.suspended_until = 0.0                    # Tester.suspend — пространство «заморожено»
        self.spawn = asyncio.ensure_future            # запуск команд и вотчеров (в Sandbox — задачи песочницы)
        me = client.hikka_me
        self._me = me.id
        self._cached_usernames = {str(me.id)}
        if getattr(me, "username", None):
            self._cached_usernames.add(me.username.lower())
        for item in getattr(me, "usernames", None) or []:
            if getattr(item, "username", None):
                self._cached_usernames.add(item.username.lower())

    # ------------------------------------------------------------------ лимиты
    def _later(self, delay, store, key, severity):
        def dec():
            store[key] = max(0, store[key] - severity)

        asyncio.get_event_loop().call_later(delay, dec)

    async def _handle_ratelimit(self, message, func) -> bool:
        if await self.security.check(message, security.OWNER):
            return True
        func = getattr(func, "__func__", func)
        ok = True
        chat = self._rl_chat[message.chat_id]
        if message.sender_id:
            user = self._rl_user[message.sender_id]
            severity = (5 if getattr(func, "ratelimit", False) else 2) * ((user + chat) // 30 + 1)
            user += severity
            self._rl_user[message.sender_id] = user
            if user > self._rl_max_user:
                ok = False
            self._later(self._rl_max_user * severity, self._rl_user, message.sender_id, severity)
        else:
            severity = (5 if getattr(func, "ratelimit", False) else 2) * (chat // 15 + 1)
        chat += severity
        self._rl_chat[message.chat_id] = chat
        if chat > self._rl_max_chat:
            ok = False
        self._later(self._rl_max_chat * severity, self._rl_chat, message.chat_id, severity)
        return ok

    # ------------------------------------------------------------------ | grep
    def _handle_grep(self, message: Message) -> Message:
        text = message.raw_text or ""
        if re.search(r"\|\| ?grep", text):
            for attr in ("message",):
                setattr(message, attr, re.sub(r"\|\| ?grep", "| grep", getattr(message, attr) or ""))
            message._text = None
            return message
        found = re.search(r".+\| ?grep (.+)", text)
        if not found:
            return message
        grep = found.group(1)
        message.message = re.sub(r"\| ?grep.+", "", message.message or "")
        message._text = None
        ungrep = None
        m = re.search(r"-v (.+)", grep)
        if m:
            ungrep = m.group(1)
            grep = re.sub(r"(.+) -v .+", r"\g<1>", grep)
            if grep.startswith("-v "):
                grep = None
        grep = utils.escape_html(grep).strip() if grep else None
        ungrep = utils.escape_html(ungrep).strip() if ungrep else None

        def process(text: str) -> str:
            res = []
            for line in text.split("\n"):
                plain = utils.remove_html(line)
                if grep and grep in plain and (not ungrep or ungrep not in plain):
                    res.append(utils.remove_html(line, escape=True).replace(grep, f"<u>{grep}</u>"))
                if not grep and ungrep and ungrep not in plain:
                    res.append(utils.remove_html(line, escape=True))
            cond = (f"содержат <b>{grep}</b>" if grep else "") + (" и" if grep and ungrep else "") + (
                f" не содержат <b>{ungrep}</b>" if ungrep else ""
            )
            return f"<i>💬 Строки, которые {cond}:</i>\n" + "\n".join(res) if res else f"💬 <i>Нет строк, которые {cond}</i>"

        old_edit, old_reply, old_respond = message.edit, message.reply, message.respond

        async def my_edit(text, *a, **k):
            k["parse_mode"] = "HTML"
            return await old_edit(process(text), *a, **k)

        async def my_reply(text, *a, **k):
            k["parse_mode"] = "HTML"
            return await old_reply(process(text), *a, **k)

        async def my_respond(text, *a, **k):
            k["parse_mode"] = "HTML"
            k.setdefault("reply_to", utils.get_topic(message))
            return await old_respond(process(text), *a, **k)

        message.edit, message.reply, message.respond = my_edit, my_reply, my_respond
        message.hikka_grepped = True
        message.heroku_grepped = True
        return message

    # ------------------------------------------------------------------ разбор команды
    async def _handle_command(self, event, watcher: bool = False):
        message = getattr(event, "message", None)
        if message is None or not isinstance(getattr(message, "message", None), str):
            return False
        initiator = getattr(event, "sender_id", 0)
        main_prefix = self._modules.get_prefix()
        prefix = main_prefix if initiator == self._client.tg_id else self._db.get(MAIN, "command_prefixes", {}).get(
            str(initiator), main_prefix
        )
        text = message.message
        if not text:
            return False

        # «,,команда» — показать команду, не выполняя (экранирование)
        if (
            message.out
            and len(text) > len(prefix) * 2
            and text.startswith(prefix * 2)
            and any(ch != prefix for ch in text)
        ):
            possible = text[len(prefix) * 2 :].strip().split(maxsplit=1)
            if possible:
                _, func = self._modules.dispatch(possible[0].split("@", 1)[0])
                if func:
                    if not watcher:
                        with contextlib.suppress(Exception):
                            await message.edit(
                                text[len(prefix) :],
                                parse_mode=lambda s: (s, utils.relocate_entities(message.entities, -len(prefix), text) or ()),
                            )
                    return False

        layout_prefix = prefix.translate(_LAYOUT)
        if text.startswith(layout_prefix) and layout_prefix != prefix:
            message.message = text.translate(_LAYOUT)
            message._text = None
            text = message.message
        elif not text.startswith(prefix):
            return False

        if (
            getattr(message, "sticker", None)
            or getattr(message, "dice", None)
            or getattr(message, "audio", None)
            or getattr(message, "via_bot_id", None)
            or (getattr(event, "reactions", False) and getattr(event, "edit_hide", False))
        ):
            return False

        blacklist_chats = self._db.get(MAIN, "blacklist_chats", [])
        whitelist_chats = self._db.get(MAIN, "whitelist_chats", [])
        whitelist_modules = self._db.get(MAIN, "whitelist_modules", [])
        chat_id = utils.get_chat_id(message)
        if chat_id in blacklist_chats or (whitelist_chats and chat_id not in whitelist_chats):
            return False
        if len(text.strip()) == len(prefix):
            return False

        cmd_part = text[len(prefix) :]
        if not cmd_part.strip():
            return False
        command = cmd_part.strip().split(maxsplit=1)[0]
        tag = command.split("@", 1)
        if len(tag) == 2:
            if tag[1] == "me":
                if not message.out:
                    return False
            elif tag[1].lower() not in self._cached_usernames:
                return False
        elif message.out or (
            getattr(message, "mentioned", False)
            and any(f"@{u}" in command.lower() for u in self._cached_usernames)
        ):
            pass
        elif (
            not message.is_private
            and not self._db.get(MAIN, "no_nickname", False)
            and command not in self._db.get(MAIN, "nonickcmds", [])
            and initiator not in self._db.get(MAIN, "nonickusers", [])
            and not self.security.check_tsec(initiator, command)
            and chat_id not in self._db.get(MAIN, "nonickchats", [])
        ):
            return False

        txt, func = self._modules.dispatch(tag[0])
        if not func:
            return False
        if not message.out:
            if not await self._handle_ratelimit(message, func):
                return False
            if not await self.security.check(message, func, usernames=self._cached_usernames):
                return False

        if message.is_channel and message.edit_date and not message.is_group:
            return False

        offset = len(prefix) + len(cmd_part) - len(cmd_part.lstrip())
        if not watcher:
            new_text = prefix + txt + text[offset + len(command) :]
            if new_text != text:
                diff = len(new_text) - len(text)
                if diff:
                    utils.relocate_entities(message.entities, diff, new_text)
                message.message = new_text
                message._text = None

        modname = func.__self__.__module__
        if f"{chat_id}.{modname}" in blacklist_chats or (
            whitelist_modules and f"{chat_id}.{modname}" not in whitelist_modules
        ):
            return False
        if await self._handle_tags(event, func):
            return False
        if self._db.get(MAIN, "grep", False) and not watcher:
            message = self._handle_grep(message)
        return message, prefix, txt, func

    def _suspended(self) -> bool:
        import time

        return self.suspended_until > time.time()

    async def handle_command(self, event):
        if self._suspended():
            return
        result = await self._handle_command(event)
        if not result:
            return
        message, _, _, func = result
        self._modules.last_command = func
        self.spawn(self.future_dispatcher(func, message, self.command_exc))

    async def handle_raw(self, event):
        for handler in list(self.raw_handlers):
            updates = tuple(getattr(handler, "updates", ()) or ())
            if updates and not isinstance(event, updates):
                continue
            try:
                await handler(event)
            except Exception as e:
                logger.exception("Ошибка в raw-обработчике %s: %s", getattr(handler, "id", handler), e)

    async def command_exc(self, exc, message):
        logger.error("Команда %s упала", (message.message or "")[:80], exc_info=(type(exc), exc, exc.__traceback__))
        cmd = utils.escape_html((message.message or "")[:200])
        if isinstance(exc, FloodWaitError):
            hours, rest = divmod(exc.seconds, 3600)
            minutes, seconds = divmod(rest, 60)
            wait = " ".join(x for x in (f"{hours} ч" if hours else "", f"{minutes} мин" if minutes else "",
                                        f"{seconds} с" if seconds else "") if x)
            text = self._tr("fw_error").format(cmd, wait, type(getattr(exc, "request", None)).__name__)
        elif isinstance(exc, RPCError):
            text = self._tr("rpc_error").format(cmd, utils.escape_html(str(exc)))
        elif self._db.get(MAIN, "inlinelogs", True):
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            tb = "\n".join(tb.strip().splitlines()[-25:])
            text = (
                f"🚫 <b>Команда</b> <code>{cmd}</code> <b>упала!</b>\n\n<b>🧾 Лог:</b>\n"
                f"<pre><code class=\"language-python\">{utils.escape_html(tb)[:3500]}</code></pre>"
            )
        else:
            text = f"🚫 <b>Команда</b> <code>{cmd}</code> <b>упала!</b>"
        with contextlib.suppress(Exception):
            await (message.edit if message.out else message.reply)(text, parse_mode="html")

    def _tr(self, key):
        try:
            return self._modules.lookup("translations").strings(key)
        except Exception:
            from .translations import CORE_STRINGS

            return CORE_STRINGS["ru"].get(f"translations.{key}", key)

    async def watcher_exc(self, exc, *_):
        logger.error("Ошибка в вотчере", exc_info=(type(exc), exc, exc.__traceback__))

    # ------------------------------------------------------------------ теги вотчеров
    async def _handle_tags(self, event, func) -> bool:
        return bool(await self._handle_tags_ext(event, func))

    async def _handle_tags_ext(self, event, func) -> typing.Optional[str]:
        m = event if isinstance(event, Message) else getattr(event, "message", event)
        mime = lambda: utils.mime_type(m)  # noqa: E731
        checks = {
            "out": lambda: getattr(m, "out", True),
            "in": lambda: not getattr(m, "out", True),
            "only_messages": lambda: isinstance(m, Message),
            "editable": lambda: not getattr(m, "out", False)
            and not getattr(m, "fwd_from", False)
            and not getattr(m, "sticker", False)
            and not getattr(m, "via_bot_id", False),
            "no_media": lambda: not isinstance(m, Message) or not getattr(m, "media", False),
            "only_media": lambda: isinstance(m, Message) and getattr(m, "media", False),
            "only_photos": lambda: mime().startswith("image/") or bool(getattr(m, "photo", None)),
            "only_videos": lambda: mime().startswith("video/"),
            "only_audios": lambda: mime().startswith("audio/"),
            "only_stickers": lambda: getattr(m, "sticker", False),
            "only_docs": lambda: getattr(m, "document", False),
            "only_inline": lambda: getattr(m, "via_bot_id", False),
            "only_channels": lambda: getattr(m, "is_channel", False) and not getattr(m, "is_group", False),
            "no_channels": lambda: not getattr(m, "is_channel", False),
            "no_groups": lambda: not getattr(m, "is_group", False)
            or getattr(m, "is_private", False)
            or getattr(m, "is_channel", False),
            "only_groups": lambda: getattr(m, "is_group", False)
            or not getattr(m, "is_private", False)
            and not getattr(m, "is_channel", False),
            "no_pm": lambda: not getattr(m, "is_private", False),
            "only_pm": lambda: getattr(m, "is_private", False),
            "no_inline": lambda: not getattr(m, "via_bot_id", False),
            "no_stickers": lambda: not getattr(m, "sticker", False),
            "no_docs": lambda: not getattr(m, "document", False),
            "no_audios": lambda: not mime().startswith("audio/"),
            "no_videos": lambda: not mime().startswith("video/"),
            "no_photos": lambda: not mime().startswith("image/") and not getattr(m, "photo", None),
            "no_forwards": lambda: not getattr(m, "fwd_from", False),
            "no_reply": lambda: not getattr(m, "reply_to_msg_id", False),
            "only_forwards": lambda: getattr(m, "fwd_from", False),
            "only_reply": lambda: getattr(m, "reply_to_msg_id", False),
            "mention": lambda: getattr(m, "mentioned", False),
            "no_mention": lambda: not getattr(m, "mentioned", False),
            "startswith": lambda: isinstance(m, Message) and (m.raw_text or "").startswith(func.startswith),
            "endswith": lambda: isinstance(m, Message) and (m.raw_text or "").endswith(func.endswith),
            "contains": lambda: isinstance(m, Message) and func.contains in (m.raw_text or ""),
            "filter": lambda: callable(func.filter) and func.filter(m),
            "from_id": lambda: getattr(m, "sender_id", None) == func.from_id,
            "chat_id": lambda: utils.get_chat_id(m)
            == (int(str(func.chat_id)[4:]) if str(func.chat_id).startswith("-100") else func.chat_id),
            "regex": lambda: isinstance(m, Message) and re.search(func.regex, m.raw_text or ""),
        }
        if getattr(func, "no_commands", False) and await self._handle_command(event, watcher=True):
            return "no_commands"
        if getattr(func, "only_commands", False) and not await self._handle_command(event, watcher=True):
            return "only_commands"
        for tag in ALL_TAGS:
            if getattr(func, tag, False) and tag in checks:
                try:
                    ok = checks[tag]()
                except Exception:
                    ok = False
                if not ok:
                    return tag
        return None

    # ------------------------------------------------------------------ вотчеры
    async def handle_incoming(self, event):
        if self._suspended():
            return
        message = getattr(event, "message", event)
        chat_id = None
        with contextlib.suppress(Exception):
            chat_id = utils.get_chat_id(message)
        blacklist_chats = self._db.get(MAIN, "blacklist_chats", [])
        whitelist_modules = self._db.get(MAIN, "whitelist_modules", [])
        disabled = self._db.get(MAIN, "disabled_watchers", {})
        for func in list(self._modules.watchers):
            try:
                modname = str(func.__self__.__class__.strings.get("name", func.__self__.__class__.__name__))
            except Exception:
                modname = func.__self__.__class__.__name__
            bl = disabled.get(modname) or disabled.get(func.__self__.__class__.__name__)
            if bl and isinstance(message, Message) and (
                "*" in bl
                or chat_id in bl
                or "only_chats" in bl and message.is_private
                or "only_pm" in bl and not message.is_private
                or "out" in bl and not message.out
                or "in" in bl and message.out
            ):
                continue
            mod_key = f"{chat_id}.{func.__self__.__module__}"
            if mod_key in blacklist_chats or (whitelist_modules and mod_key not in whitelist_modules):
                continue
            try:
                if await self._handle_tags(event, func):
                    continue
            except Exception:
                continue
            for placeholder in ("text", "raw_text", "out"):
                with contextlib.suppress(Exception):
                    if not hasattr(message, placeholder):
                        setattr(message, placeholder, "")
            self.spawn(self.future_dispatcher(func, message, self.watcher_exc))

    async def future_dispatcher(self, func, message, exception_handler, *args):
        try:
            await func(message)
        except Exception as e:
            await exception_handler(e, message, *args)
