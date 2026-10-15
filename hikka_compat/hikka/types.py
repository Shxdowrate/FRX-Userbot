"""Базовые типы пространства Hikka: Module, Library, конфиги, исключения загрузки, кэш сущностей."""

import ast
import asyncio
import contextlib
import copy
import importlib.machinery
import importlib.util
import inspect
import logging
import re
import sys
import time
import typing
from dataclasses import dataclass, field
from importlib.abc import SourceLoader

from . import version
from .inline.types import (
    BotInlineCall,
    BotInlineMessage,
    BotMessage,
    InlineCall,
    InlineMessage,
    InlineQuery,
    InlineUnit,
)
from .pointers import PointerDict, PointerList

logger = logging.getLogger(__name__)

JSONSerializable = typing.Union[str, int, float, bool, list, dict, None]
HikkaReplyMarkup = typing.Union[typing.List[typing.List[dict]], typing.List[dict], dict]
HerokuReplyMarkup = HikkaReplyMarkup
ListLike = typing.Union[list, set, tuple]
Command = typing.Callable[..., typing.Awaitable[typing.Any]]

__all__ = [
    "JSONSerializable",
    "HikkaReplyMarkup",
    "HerokuReplyMarkup",
    "ListLike",
    "Command",
    "StringLoader",
    "Module",
    "Library",
    "LoadError",
    "CoreOverwriteError",
    "CoreUnloadError",
    "SelfUnload",
    "SelfSuspend",
    "StopLoop",
    "ModuleConfig",
    "LibraryConfig",
    "ConfigValue",
    "ConfigCategory",
    "get_commands",
    "get_inline_handlers",
    "get_callback_handlers",
    "get_watchers",
    "BotInlineCall",
    "BotMessage",
    "InlineCall",
    "InlineMessage",
    "InlineQuery",
    "InlineUnit",
    "BotInlineMessage",
    "PointerDict",
    "PointerList",
]



def _db_of(obj):
    """БД модуля; до регистрации (например, self.pointer в __init__) — БД пространства, как в Hikka."""
    db = obj.__dict__.get("_db")
    if db is None:
        from . import _frx

        space = _frx.runtime
        db = getattr(space, "db", None)
        if db is None:
            raise AttributeError(f"{type(obj).__name__}: БД ещё не подключена")
    return db

class StringLoader(SourceLoader):
    """Загрузчик модуля Python из строки (исходник модуля Hikka)."""

    def __init__(self, data, origin: str):
        self.data = data.encode("utf-8") if isinstance(data, str) else data
        self.origin = origin

    def get_source(self, _=None) -> str:
        return self.data.decode("utf-8")

    def get_code(self, fullname: str):
        source = self.get_data(fullname)
        return compile(source, self.origin, "exec", dont_inherit=True) if source else None

    def get_filename(self, *args, **kwargs) -> str:
        return self.origin

    def get_data(self, *args, **kwargs) -> bytes:
        return self.data


# ---------------------------------------------------------------------- исключения


class LoadError(Exception):
    """Поднимите в client_ready — модуль не загрузится, пользователь увидит причину."""

    def __init__(self, error_message: str = ""):
        super().__init__(error_message)
        self._error = error_message

    def __str__(self) -> str:
        return self._error


class CoreOverwriteError(LoadError):
    def __init__(self, module: str = None, command: str = None):
        self.type = "module" if module else "command"
        self.target = module or command
        super().__init__(str(self))

    def __str__(self) -> str:
        what = "Модуль" if self.type == "module" else "Команда"
        return f"{what} {self.target} встроенная — её нельзя перезаписать"


class CoreUnloadError(Exception):
    def __init__(self, module: str):
        self.module = module
        super().__init__()

    def __str__(self) -> str:
        return f"Модуль {self.module} встроенный — его нельзя выгрузить"


class SelfUnload(Exception):
    """Поднимите в client_ready — модуль тихо выгрузится."""

    def __init__(self, error_message: str = ""):
        super().__init__(error_message)
        self._error = error_message

    def __str__(self) -> str:
        return self._error


class SelfSuspend(Exception):
    """Поднимите в client_ready — модуль останется загруженным, но без команд и вотчеров (до перезапуска)."""

    def __init__(self, error_message: str = ""):
        super().__init__(error_message)
        self._error = error_message

    def __str__(self) -> str:
        return self._error


class StopLoop(Exception):
    """Поднимите внутри @loader.loop — цикл остановится."""


# ---------------------------------------------------------------------- конфиг


class _Placeholder:
    """Маркер «значение не задано»."""


async def _wrap(func):
    with contextlib.suppress(Exception):
        return await func()


def _syncwrap(func):
    with contextlib.suppress(Exception):
        return func()


@dataclass(repr=True)
class ConfigValue:
    option: str
    default: typing.Any = None
    doc: typing.Union[typing.Callable[[], str], str] = "No description"
    value: typing.Any = field(default_factory=_Placeholder)
    validator: typing.Optional[typing.Any] = None
    on_change: typing.Optional[typing.Callable] = None
    folder: typing.Optional[str] = None

    def __post_init__(self):
        if isinstance(self.value, _Placeholder):
            self.value = self.default

    def set_no_raise(self, value) -> bool:
        return self.__setattr__("value", value, ignore_validation=True)

    def __setattr__(self, key, value, *, ignore_validation: bool = False):
        if key == "value":
            if isinstance(value, str):
                try:
                    value = ast.literal_eval(value)
                except Exception:
                    pass
            if isinstance(value, (set, tuple)):
                value = list(value)
            if isinstance(value, list):
                value = [item.strip() if isinstance(item, str) else item for item in value]

            validator = self.__dict__.get("validator")
            if validator is not None:
                if value is not None:
                    from . import validators

                    try:
                        value = validator.validate(value)
                    except validators.ValidationError:
                        if not ignore_validation:
                            raise
                        value = self.__dict__.get("default")
                else:
                    empty = {"String": "", "Integer": 0, "Boolean": False, "Series": [], "Float": 0.0}.get(
                        getattr(validator, "internal_id", None)
                    )
                    if empty is not None:
                        value = empty
            object.__setattr__(self, "_save_marker", True)

        object.__setattr__(self, key, value)

        if key == "value" and not ignore_validation and callable(self.__dict__.get("on_change")):
            on_change = self.__dict__["on_change"]
            if inspect.iscoroutinefunction(on_change):
                asyncio.ensure_future(_wrap(on_change))
            else:
                _syncwrap(on_change)


class ConfigCategory(list):
    """Группа опций конфига (Heroku)."""

    def __init__(self, name: str, *config_values: ConfigValue, doc="No description"):
        super().__init__(config_values)
        self.name = str(name)
        self.doc = doc

    def getdoc(self) -> str:
        if callable(self.doc):
            try:
                return self.doc()
            except Exception:
                return "No description"
        return self.doc

    @property
    def _config_values(self):
        return tuple(self)


class ModuleConfig(dict):
    """Конфиг модуля: ModuleConfig(ConfigValue(...), ...) или старый формат (ключ, значение, описание, ...)."""

    def __init__(self, *entries):
        self._option_categories: dict = {}
        self._categories: dict = {}
        if all(isinstance(e, (ConfigValue, ConfigCategory)) for e in entries):
            self._config = {}
            for entry in entries:
                if isinstance(entry, ConfigCategory):
                    self._categories[entry.name] = entry
                    for cv in entry:
                        self._config[cv.option] = cv
                        self._option_categories[cv.option] = entry.name
                else:
                    self._config[entry.option] = entry
        else:
            keys, defaults, docs = entries[0::3], entries[1::3], entries[2::3]
            self._config = {
                key: ConfigValue(option=key, default=default, doc=doc) for key, default, doc in zip(keys, defaults, docs)
            }
        super().__init__({option: cv.value for option, cv in self._config.items()})

    def getdoc(self, key: str, message=None) -> str:
        doc = self._config[key].doc
        if callable(doc):
            try:
                doc = doc(message)
            except Exception:
                try:
                    doc = doc()
                except Exception:
                    doc = "No description"
        return doc

    def getdef(self, key: str):
        return self._config[key].default

    def get_category(self, key: str):
        name = self._option_categories.get(key)
        return self._categories.get(name) if name else None

    def grouped_options(self):
        import collections

        result = collections.OrderedDict()
        for option in self._config:
            result.setdefault(self._option_categories.get(option), []).append(option)
        return result

    def __setitem__(self, key, value):
        self._config[key].value = value
        super().__setitem__(key, self._config[key].value)

    def set_no_raise(self, key, value):
        self._config[key].set_no_raise(value)
        super().__setitem__(key, self._config[key].value)

    def __getitem__(self, key):
        try:
            return self._config[key].value
        except KeyError:
            return None

    def get(self, key, default=None):
        return self._config[key].value if key in self._config else default

    def reload(self):
        for key in self._config:
            super().__setitem__(key, self._config[key].value)

    def change_validator(self, key, validator):
        self._config[key].validator = validator


LibraryConfig = ModuleConfig


# ---------------------------------------------------------------------- интроспекция


def _get_members(mod, ending: str, attribute: str = None, strict: bool = False) -> dict:
    result = {}
    cls = type(mod)
    for name in dir(mod):
        if isinstance(getattr(cls, name, None), property):
            continue
        try:
            value = getattr(mod, name)
        except Exception:
            continue
        if not callable(value):
            continue
        matched = (name == ending) if strict else name.endswith(ending)
        if not matched and not (attribute and getattr(value, attribute, False)):
            continue
        key = name.rsplit(ending, 1)[0] if matched and not strict else name
        if matched and strict:
            key = name
        result[key.lower()] = value
    return result


def get_commands(mod) -> dict:
    return _get_members(mod, "cmd", "is_command")


def get_inline_handlers(mod) -> dict:
    return _get_members(mod, "_inline_handler", "is_inline_handler")


def get_callback_handlers(mod) -> dict:
    return _get_members(mod, "_callback_handler", "is_callback_handler")


def get_watchers(mod) -> dict:
    return _get_members(mod, "watcher", "is_watcher", strict=True)


# ---------------------------------------------------------------------- Module


class Module:
    strings = {"name": "Unknown"}

    """There is no help for this module"""

    def config_complete(self):
        """Вызывается, когда module.config заполнен."""

    async def client_ready(self):
        """Вызывается, когда клиент готов (после config_complete)."""

    def internal_init(self):
        """Привязка к пространству. Не вызывайте сами."""
        allmodules = self.allmodules
        self.db = allmodules.db
        self._db = allmodules.db
        self.client = allmodules.client
        self._client = allmodules.client
        self.lookup = allmodules.lookup
        self.get_prefix = allmodules.get_prefix
        self.get_prefixes = allmodules.get_prefixes
        self.inline = allmodules.inline
        self.allclients = allmodules.allclients
        self.tg_id = allmodules.client.tg_id
        self._tg_id = allmodules.client.tg_id

    async def on_unload(self):
        """Вызывается при выгрузке / перезагрузке модуля."""

    async def on_dlmod(self):
        """Вызывается после первой загрузки модуля через .dlmod / .loadmod."""

    async def invoke(self, command: str, args: str = None, peer=None, message=None, edit: bool = False):
        """Выполнить команду другого модуля так, будто её ввели."""
        command = command.lower()
        if command not in self.allmodules.commands:
            raise ValueError(f"Command {command} not found")
        if not message and not peer:
            raise ValueError("Either peer or message must be specified")
        text = f"{self.get_prefix()}{command} {args or ''}".strip()
        if peer:
            message = await self._client.send_message(peer, text)
        else:
            message = await (message.edit if edit else message.respond)(text)
        await self.allmodules.commands[command](message)
        return message

    # ------------------------------------------------------------------ списки обработчиков
    @property
    def commands(self) -> dict:
        return get_commands(self)

    @commands.setter
    def commands(self, _):
        pass

    hikka_commands = heroku_commands = commands

    @property
    def inline_handlers(self) -> dict:
        return get_inline_handlers(self)

    @inline_handlers.setter
    def inline_handlers(self, _):
        pass

    hikka_inline_handlers = heroku_inline_handlers = inline_handlers

    @property
    def callback_handlers(self) -> dict:
        return get_callback_handlers(self)

    @callback_handlers.setter
    def callback_handlers(self, _):
        pass

    hikka_callback_handlers = heroku_callback_handlers = callback_handlers

    @property
    def watchers(self) -> dict:
        return get_watchers(self)

    @watchers.setter
    def watchers(self, _):
        pass

    hikka_watchers = heroku_watchers = watchers

    # ------------------------------------------------------------------ helpers
    async def animate(self, message, frames: list, interval: float, *, inline: bool = False):
        from telethon.tl.types import Message as _TlMessage

        from . import utils

        interval = max(0.1, float(interval))
        for frame in frames:
            if isinstance(message, _TlMessage):
                if inline:
                    message = await self.inline.form(
                        message=message, text=frame, reply_markup={"text": " ⠀", "data": "empty"}
                    )
                else:
                    message = await utils.answer(message, frame)
            elif isinstance(message, InlineMessage):
                await message.edit(frame)
            await asyncio.sleep(interval)
        return message

    def get(self, key: str, default=None):
        return _db_of(self).get(self.__class__.__name__, key, default)

    def set(self, key: str, value) -> bool:
        return _db_of(self).set(self.__class__.__name__, key, value)

    def pointer(self, key: str, default=None, item_type=None):
        return _db_of(self).pointer(self.__class__.__name__, key, default, item_type)

    # ------------------------------------------------------------------ запрос подписки на канал
    async def request_join(self, peer, reason: str, assure_joined: bool = False) -> bool:
        from telethon.tl.types import Channel, ChannelForbidden

        from . import utils

        channel = await self._client.get_entity(peer)
        declined = self._db.get("hikka.main", "declined_joins", [])
        if isinstance(channel, ChannelForbidden):
            if assure_joined:
                raise LoadError(f"Нужно вступить в {channel.title}, но вы там забанены")
            return False
        if not isinstance(channel, Channel):
            raise TypeError("`peer` field must be a channel")
        if channel.id in declined:
            if assure_joined:
                raise LoadError(f"Для этого модуля нужно вступить в @{channel.username}")
            return False
        if getattr(channel, "left", True):
            try:
                channel = await self._client.get_entity(peer, force=True)
            except TypeError:
                channel = await self._client.get_entity(peer)
        if not getattr(channel, "left", True):
            return True

        event = asyncio.Event()
        event.status = False
        title = utils.escape_html(getattr(channel, "title", ""))
        link = f"https://t.me/{channel.username}" if getattr(channel, "username", None) else ""
        text = (
            f"💫 <b>Модуль <code>{utils.escape_html(self.__class__.__name__)}</code> просит вступить в канал "
            f"<a href=\"{link}\">{title}</a></b>\n\n<b>Причина:</b> <i>{utils.escape_html(reason)}</i>"
        )
        markup = [
            {"text": "💫 Вступить", "callback": self._approve_join, "args": (channel, event)},
            {"text": "✖️ Отклонить", "callback": self._decline_join, "args": (channel, event)},
        ]
        try:
            await self.inline.bot.send_message(self.tg_id, text, reply_markup=self.inline.generate_markup(markup))
        except Exception:
            logger.exception("Не удалось отправить запрос на вступление")
            if assure_joined:
                raise LoadError("Не удалось спросить разрешение на вступление в канал (бот недоступен)")
            return False

        await event.wait()
        if assure_joined and not event.status:
            raise LoadError(f"Для этого модуля нужно вступить в @{getattr(channel, 'username', '')}")
        return event.status

    async def _approve_join(self, call, channel, event):
        from telethon.tl.functions.channels import JoinChannelRequest

        try:
            await self._client(JoinChannelRequest(channel))
        except Exception:
            logger.exception("Не удалось вступить в канал")
        event.status = True
        event.set()
        with contextlib.suppress(Exception):
            await call.edit("💫 <b>Вступил в канал</b>")

    async def _decline_join(self, call, channel, event):
        self._db.set(
            "hikka.main",
            "declined_joins",
            list(set(self._db.get("hikka.main", "declined_joins", []) + [channel.id])),
        )
        event.status = False
        event.set()
        with contextlib.suppress(Exception):
            await call.edit("✖️ <b>Вступление отклонено</b>")

    # ------------------------------------------------------------------ библиотеки
    async def import_lib(self, url: str, *, suspend_on_error: bool = False, _did_requirements: bool = False):
        return await self.allmodules.import_library(
            url, suspend_on_error=suspend_on_error, _did_requirements=_did_requirements
        )


class Library:
    """Базовый класс внешних библиотек (self.import_lib)."""

    def internal_init(self):
        allmodules = self.allmodules
        self.name = self.__class__.__name__
        self.db = allmodules.db
        self._db = allmodules.db
        self.client = allmodules.client
        self._client = allmodules.client
        self.tg_id = allmodules.client.tg_id
        self._tg_id = allmodules.client.tg_id
        self.lookup = allmodules.lookup
        self.get_prefix = allmodules.get_prefix
        self.get_prefixes = allmodules.get_prefixes
        self.inline = allmodules.inline
        self.allclients = allmodules.allclients

    def _lib_get(self, key: str, default=None):
        return _db_of(self).get(self.__class__.__name__, key, default)

    def _lib_set(self, key: str, value) -> bool:
        return _db_of(self).set(self.__class__.__name__, key, value)

    def _lib_pointer(self, key: str, default=None):
        return _db_of(self).pointer(self.__class__.__name__, key, default)


# ---------------------------------------------------------------------- кэш сущностей (tl_cache)


class _CacheRecord:
    def __init__(self, exp: int):
        self._exp = round(time.time() + exp)
        self.ts = time.time()

    @property
    def expired(self) -> bool:
        return self._exp < time.time()


class CacheRecordEntity(_CacheRecord):
    def __init__(self, hashable_entity, resolved_entity, exp: int):
        super().__init__(exp)
        self.entity = copy.deepcopy(resolved_entity)
        self._hashable_entity = hashable_entity

    def __hash__(self):
        return hash(self._hashable_entity)

    def __eq__(self, other):
        return hash(self) == hash(other)

    def __repr__(self):
        return f"CacheRecordEntity(entity={type(self.entity).__name__}(...), exp={self._exp})"


class CacheRecordPerms(_CacheRecord):
    def __init__(self, hashable_entity, hashable_user, resolved_perms, exp: int):
        super().__init__(exp)
        self.perms = copy.deepcopy(resolved_perms)
        self._hashable_entity = hashable_entity
        self._hashable_user = hashable_user

    def __hash__(self):
        return hash((self._hashable_entity, self._hashable_user))

    def __eq__(self, other):
        return hash(self) == hash(other)


class CacheRecordFullChannel(_CacheRecord):
    def __init__(self, channel_id: int, full_channel, exp: int):
        super().__init__(exp)
        self.channel_id = channel_id
        self.full_channel = full_channel

    def __hash__(self):
        return hash(self.channel_id)


class CacheRecordFullUser(_CacheRecord):
    def __init__(self, user_id: int, full_user, exp: int):
        super().__init__(exp)
        self.user_id = user_id
        self.full_user = full_user

    def __hash__(self):
        return hash(self.user_id)


def _module_spec(name: str, source: str, origin: str):
    return importlib.machinery.ModuleSpec(name, StringLoader(source, origin), origin=origin)


def _scope_version(source: str, key: str):
    match = re.search(rf"# ?scope: ?{key} ((\d+\.){{2}}\d+)", source)
    return tuple(map(int, match[1].split("."))) if match else None
