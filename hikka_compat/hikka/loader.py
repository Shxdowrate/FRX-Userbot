"""loader — то, что модули Hikka импортируют чаще всего: Module, декораторы команд, конфиг, валидаторы,
и реестр модулей пространства (Modules — он же self.allmodules / client.loader)."""

import asyncio
import contextlib
import importlib
import importlib.machinery
import importlib.util
import inspect
import logging
import os
import re
import sys
import typing
import uuid
from pathlib import Path

from . import _frx, security, utils, validators
from .database import Database
from .translations import Strings, Translator
from .types import (
    Command,
    ConfigCategory,
    ConfigValue,
    CoreOverwriteError,
    CoreUnloadError,
    InlineMessage,
    JSONSerializable,
    Library,
    LibraryConfig,
    LoadError,
    Module,
    ModuleConfig,
    SelfSuspend,
    SelfUnload,
    StopLoop,
    StringLoader,
    _scope_version,
    get_callback_handlers,
    get_commands,
    get_inline_handlers,
    get_watchers,
)

__all__ = [
    "Modules", "InfiniteLoop", "Command", "CoreOverwriteError", "CoreUnloadError", "InlineMessage",
    "JSONSerializable", "Library", "LibraryConfig", "LoadError", "Module", "SelfSuspend", "SelfUnload", "StopLoop",
    "StringLoader", "get_commands", "get_inline_handlers", "get_callback_handlers", "validators", "Database",
    "Strings", "Translator", "ConfigCategory", "ConfigValue", "ModuleConfig", "owner", "group_owner",
    "group_admin_add_admins", "group_admin_change_info", "group_admin_ban_users", "group_admin_delete_messages",
    "group_admin_pin_messages", "group_admin_invite_users", "group_admin", "group_member", "pm", "unrestricted",
    "inline_everyone", "loop", "command", "watcher", "inline_handler", "callback_handler", "raw_handler",
    "need_update", "tag", "tds", "translatable_docstring", "ratelimit", "debug_method",
]

logger = logging.getLogger(__name__)

MAIN = "hikka.main"

owner = security.owner
sudo = security.sudo
support = security.support
group_owner = security.group_owner
group_admin_add_admins = security.group_admin_add_admins
group_admin_change_info = security.group_admin_change_info
group_admin_ban_users = security.group_admin_ban_users
group_admin_delete_messages = security.group_admin_delete_messages
group_admin_pin_messages = security.group_admin_pin_messages
group_admin_invite_users = security.group_admin_invite_users
group_admin = security.group_admin
group_member = security.group_member
pm = security.pm
unrestricted = security.unrestricted
inline_everyone = security.inline_everyone

VALID_PIP_PACKAGES = re.compile(
    r"^\s*# ?requires:(?: ?)((?:{url} )*(?:{url}))\s*$".format(url=r"[-[\]_.~:/?#@!$&'()*+,;%<=>a-zA-Z0-9]+"),
    re.MULTILINE,
)
VALID_APT_PACKAGES = re.compile(
    r"^\s*# ?packages:(?: ?)((?:{url} )*(?:{url}))\s*$".format(url=r"[-[\]_.~:/?#@!$&'()*+,;%<=>a-zA-Z0-9]+"),
    re.MULTILINE,
)
IMPORT_PIP_ALIASES = {
    "sklearn": "scikit-learn", "pil": "Pillow", "PIL": "Pillow", "cv2": "opencv-python", "yaml": "PyYAML",
    "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "markdown_it": "markdown-it-py", "magic": "python-magic",
    "Crypto": "pycryptodome", "speech_recognition": "SpeechRecognition", "yt_dlp": "yt-dlp", "pytgcalls": "py-tgcalls",
    "googletrans": "googletrans==4.0.0rc1", "telegraph": "telegraph", "youtube_dl": "youtube-dl",
    # модули стандартной библиотеки, убранные в Python 3.13, — их копии с PyPI
    "imghdr": "standard-imghdr", "cgi": "legacy-cgi", "pipes": "standard-pipes", "telnetlib": "standard-telnetlib",
    "audioop": "audioop-lts", "aifc": "standard-aifc", "sunau": "standard-sunau", "chunk": "standard-chunk",
    "git": "gitpython", "speedtest": "speedtest-cli", "github": "PyGithub", "faker": "Faker", "wand": "Wand",
    "gtts": "gTTS", "pygments": "Pygments", "flag": "emoji-country-flag", "whoosh": "Whoosh", "deep_translator":
    "deep-translator", "fake_useragent": "fake-useragent", "requests_toolbelt": "requests-toolbelt",
    "dataclasses_json": "dataclasses-json", "telethon_secret_chat": "telethon-secret-chat", "Image": "Pillow",
}
_FORBIDDEN_PIP = {"telethon", "hikka-tl", "hikka-tl-new", "herokutl", "heroku-tl-new", "aiogram", "hikka-pyro-new"}

USER_INSTALL = False
MODULES_NAME = "modules"
ru_keys = 'ёйцукенгшщзхъфывапролджэячсмитьбю.Ё"№;%:?ЙЦУКЕНГШЩЗХЪФЫВАПРОЛДЖЭ/ЯЧСМИТЬБЮ,'
en_keys = "`qwertyuiop[]asdfghjkl;'zxcvbnm,./~@#$%^&QWERTYUIOP{}ASDFGHJKL:\"|ZXCVBNM<>?"


class Placeholder:
    """Маркер."""


async def stop_placeholder() -> bool:
    return True


# ---------------------------------------------------------------------- циклы


class InfiniteLoop:
    """@loader.loop(interval=…, autostart=…) — бесконечный цикл метода модуля."""

    _task = None
    status = False
    module_instance = None

    def __init__(self, func, interval: int, autostart: bool, wait_before: bool, stop_clause: str = None):
        self.func = func
        self.interval = interval
        self._wait_before = wait_before
        self._stop_clause = stop_clause
        self.autostart = autostart
        self._wait_for_stop = asyncio.Event()
        functools_wraps = getattr(func, "__doc__", None)
        self.__doc__ = functools_wraps

    def __get__(self, instance, owner_cls=None):
        return self                                 # loop — атрибут класса, у всех экземпляров один объект

    def _stop(self, *_):
        self._wait_for_stop.set()

    def stop(self, *args, **kwargs):
        if self._task:
            self._wait_for_stop = asyncio.Event()
            self.status = False
            self._task.add_done_callback(self._stop)
            self._task.cancel()
            self._task = None
            return asyncio.ensure_future(self._wait_for_stop.wait())
        return asyncio.ensure_future(stop_placeholder())

    def start(self, *args, **kwargs):
        if not self._task:
            self._task = asyncio.ensure_future(self.actual_loop(*args, **kwargs))
            space = _frx.runtime
            if space is not None:
                space.track_task(self._task)

    async def actual_loop(self, *args, **kwargs):
        while not self.module_instance:
            await asyncio.sleep(0.01)
        if isinstance(self._stop_clause, str) and self._stop_clause:
            self.module_instance.set(self._stop_clause, True)
        self.status = True
        try:
            while self.status:
                if self._wait_before:
                    await asyncio.sleep(self.interval)
                if (
                    isinstance(self._stop_clause, str)
                    and self._stop_clause
                    and not self.module_instance.get(self._stop_clause, False)
                ):
                    break
                try:
                    await self.func(self.module_instance, *args, **kwargs)
                except StopLoop:
                    break
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Ошибка в цикле %s", getattr(self.func, "__qualname__", self.func))
                if not self._wait_before:
                    await asyncio.sleep(self.interval)
        finally:
            self._wait_for_stop.set()
            self.status = False
            self._task = None

    def __call__(self, *args, **kwargs):
        return self.func(self.module_instance, *args, **kwargs)

    def __del__(self):
        with contextlib.suppress(Exception):
            if self._task:
                self._task.cancel()


def loop(interval: int = 5, autostart: bool = False, wait_before: bool = False, stop_clause: str = None):
    def wrapped(func):
        return InfiniteLoop(func, interval, autostart, wait_before, stop_clause)

    return wrapped


# ---------------------------------------------------------------------- декораторы


def translatable_docstring(cls):
    """@loader.tds — докстринги команд берутся из strings (_cmd_doc_<команда>, _cls_doc)."""
    original = cls.config_complete

    def config_complete(self, *args, **kwargs):
        reload_only = kwargs.pop("reload_dynamic_translate", None)
        for command_, func_ in get_commands(cls).items():
            _collect_lang_docs(self, func_, f"_cmd_doc_{command_}")
            with contextlib.suppress(Exception):
                _set_doc(func_, self.strings[f"_cmd_doc_{command_}"])
        for handler_, func_ in get_inline_handlers(cls).items():
            _collect_lang_docs(self, func_, f"_ihandle_doc_{handler_}")
            with contextlib.suppress(Exception):
                _set_doc(func_, self.strings[f"_ihandle_doc_{handler_}"])
        with contextlib.suppress(Exception):
            self.__doc__ = self.strings["_cls_doc"]
        return True if reload_only else original(self, *args, **kwargs)

    config_complete._old_ = original
    cls.config_complete = config_complete
    base = dict(cls.strings) if isinstance(cls.strings, dict) else {}
    for command_, func in get_commands(cls).items():
        base[f"_cmd_doc_{command_}"] = inspect.getdoc(func)
    for handler_, func in get_inline_handlers(cls).items():
        base[f"_ihandle_doc_{handler_}"] = inspect.getdoc(func)
    base["_cls_doc"] = inspect.getdoc(cls)
    cls.strings = base
    return cls


tds = translatable_docstring


def _set_doc(func, doc):
    if doc is None or str(doc).startswith("Unknown strings"):
        return
    try:
        func.__doc__ = doc
    except AttributeError:
        func.__func__.__doc__ = doc


def _collect_lang_docs(mod, func, key):
    """@loader.command(ru_doc="…") → strings_ru[key]."""
    for attr in dir(func):
        if attr.endswith("_doc") and len(attr) == 6 and isinstance(getattr(func, attr, None), str):
            var = f"strings_{attr[:2]}"
            if not isinstance(getattr(mod, var, None), dict):
                setattr(mod, var, {})
            getattr(mod, var).setdefault(key, getattr(func, attr))


def ratelimit(func):
    func.ratelimit = True
    return func


def tag(*tags, **kwarg_tags):
    def inner(func):
        for t in tags:
            setattr(func, t, True)
        for t, value in kwarg_tags.items():
            setattr(func, t, value)
        return func

    return inner


def _mark_method(mark: str, *args, **kwargs):
    def decorator(func):
        target = getattr(func, "__func__", func)
        setattr(target, mark, True)
        for arg in args:
            setattr(target, arg, True)
        for key, value in kwargs.items():
            setattr(target, key, value)
        return func

    return decorator


def command(*args, **kwargs):
    return _mark_method("is_command", *args, **kwargs)


def debug_method(*args, **kwargs):
    return _mark_method("is_debug_method", *args, **kwargs)


def inline_handler(*args, **kwargs):
    return _mark_method("is_inline_handler", *args, **kwargs)


def watcher(*args, **kwargs):
    return _mark_method("is_watcher", *args, **kwargs)


def callback_handler(*args, **kwargs):
    return _mark_method("is_callback_handler", *args, **kwargs)


def raw_handler(*updates):
    def inner(func):
        func.is_raw_handler = True
        func.updates = updates
        func.id = uuid.uuid4().hex
        return func

    return inner


def need_update(*update_types):
    def inner(func):
        func.is_bot_update_handler = True
        func.bot_update_types = list(update_types)
        func.id = uuid.uuid4().hex
        return func

    return inner


# ---------------------------------------------------------------------- зависимости


def parse_requirements(source: str) -> typing.List[str]:
    reqs = []
    for match in VALID_PIP_PACKAGES.finditer(source):
        reqs += [x.strip() for x in match.group(1).split() if x.strip() and not x.startswith(("-", "_", "."))]
    return [r for r in dict.fromkeys(reqs) if re.split(r"[\[<>=!~;]", r)[0].lower() not in _FORBIDDEN_PIP]


async def install_requirements(requirements: typing.List[str]) -> bool:
    """pip install недостающего (через установщик FRX). True — всё встало."""
    requirements = [r for r in requirements if r and re.split(r"[\[<>=!~;]", r)[0].lower() not in _FORBIDDEN_PIP]
    if not requirements:
        return True
    try:
        from core import requirements as frx_req

        reqs = []
        for spec in requirements:
            names = [frx_req.import_name(spec)]
            reqs.append((spec, names))
        need = frx_req.missing(reqs)
        if not need:
            return True
        _frx.tlog("ставлю пакеты: " + ", ".join(s for s, _ in need))
        done, failed = await frx_req.install(need, log=_frx.tlog)
        importlib.invalidate_caches()
        if failed:
            _frx.tlog("не встали: " + ", ".join(failed))
        return not failed
    except ImportError:
        pass
    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "pip", "install", "-q", "--disable-pip-version-check", *requirements,
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    importlib.invalidate_caches()
    return rc == 0


def _alias_for(name: str) -> str:
    return IMPORT_PIP_ALIASES.get(name, IMPORT_PIP_ALIASES.get(name.lower(), name.replace("_", "-")))


# ---------------------------------------------------------------------- реестр модулей


class Modules:
    """Все модули пространства. Модули видят его как self.allmodules и client.loader."""

    def __init__(self, client, db: Database, allclients: list, translator: Translator):
        self.commands: dict = {}
        self.inline_handlers: dict = {}
        self.callback_handlers: dict = {}
        self.aliases: dict = dict(db.get(MAIN, "aliases", {}) or {})
        self.modules: list = []
        self.libraries: list = []
        self.watchers: list = []
        self._core_commands: list = []
        self.allclients = allclients
        self.client = client
        self._db = db
        self.db = db
        self.translator = translator
        self.secure_boot = False
        self.fully_loaded = False
        self.inline = None                  # InlineManager — ставит пространство
        self.dispatcher = None
        self.last_command = None

    # ------------------------------------------------------------------ поиск
    def lookup(self, modname: str):
        modname = str(modname).lower()
        for lib in self.libraries:
            if lib.name.lower() == modname:
                return lib
        for mod in self.modules:
            cls_name = mod.__class__.__name__.lower()
            if modname in (cls_name, str(getattr(mod, "name", "")).lower()) or (
                cls_name.endswith("mod") and cls_name[:-3] == modname
            ):
                return mod
        return False

    def get_classname(self, name: str) -> str:
        for module in reversed(self.modules):
            if name in (getattr(module, "name", None), module.__class__.__module__):
                return module.__class__.__module__
        return name

    def get_prefix(self, ent_id: int = None) -> str:
        default = self._default_prefix()
        if ent_id:
            return self._db.get(MAIN, "command_prefixes", {}).get(str(ent_id), default)
        return default

    def _default_prefix(self) -> str:
        space = _frx.runtime
        fallback = getattr(space, "default_prefix", ",") if space else ","
        return self._db.get(MAIN, "command_prefix", fallback) or fallback

    def get_prefixes(self) -> set:
        return set(self._db.get(MAIN, "command_prefixes", {}).values()) | {self._default_prefix()}

    async def log(self, *args, **kwargs):
        """Совместимость (в Hikka — заглушка)."""

    async def check_security(self, *args, **kwargs):
        return await self.dispatcher.security.check(*args, **kwargs)

    # ------------------------------------------------------------------ алиасы
    def _save_aliases(self):
        self._db.set(MAIN, "aliases", self.aliases)

    def add_aliases(self, aliases: dict):
        self.aliases.update(aliases)
        self._save_aliases()

    def add_alias(self, alias: str, cmd: str, args: str = None) -> bool:
        if cmd.lower() not in self.commands:
            return False
        self.aliases[alias.lower().strip()] = f"{cmd} {args}" if args else cmd
        self._save_aliases()
        return True

    def remove_alias(self, alias: str) -> bool:
        removed = bool(self.aliases.pop(alias.lower().strip(), None))
        if removed:
            self._save_aliases()
        return removed

    def find_alias(self, alias: str, include_legacy: bool = False):
        if not alias:
            return None
        alias = alias.lower()
        for name, cmd in self.commands.items():
            aliases = list(getattr(cmd, "aliases", None) or [])
            if getattr(cmd, "alias", None):
                aliases.append(cmd.alias)
            if any(alias == str(a).lower() for a in aliases) and alias not in self._core_commands:
                return name
        if include_legacy and alias in self.aliases:
            return self.aliases[alias]
        return None

    def dispatch(self, _command: str) -> tuple:
        """Команда (без префикса) → (текст команды, функция | None) с учётом алиасов и отключённого."""
        candidates = [_command, self.aliases.get(_command.lower()), self.find_alias(_command)]
        cmd, func = _command, None
        for candidate in candidates:
            if candidate and candidate.split()[0].lower() in self.commands:
                cmd, func = candidate, self.commands[candidate.split()[0].lower()]
                break
        if not func:
            return _command, None
        owner_name = getattr(getattr(func, "__self__", None), "__class__", type(None)).__name__
        if owner_name in self._db.get(MAIN, "disabled_modules", []):
            return _command, None
        disabled = [x.lower() for x in self._db.get(MAIN, "disabled_commands", {}).get(owner_name, [])]
        if cmd.split()[0].lower() in disabled:
            return _command, None
        return cmd, func

    # ------------------------------------------------------------------ регистрация обработчиков
    @property
    def _remove_core_protection(self) -> bool:
        return self._db.get(MAIN, "remove_core_protection", False)

    def register_commands(self, instance: Module):
        if str(instance.__origin__).startswith("<core"):
            self._core_commands += [c.lower() for c in instance.commands]
        for name, cmd in instance.commands.items():
            if (
                not self._remove_core_protection
                and name.lower() in self._core_commands
                and not str(instance.__origin__).startswith("<core")
            ):
                with contextlib.suppress(ValueError):
                    self.modules.remove(instance)
                raise CoreOverwriteError(command=name)
            self.commands[name.lower()] = cmd
        self.register_inline_stuff(instance)

    def register_inline_stuff(self, instance: Module):
        for name, func in instance.inline_handlers.items():
            self.inline_handlers[name.lower()] = func
        for name, func in instance.callback_handlers.items():
            self.callback_handlers[name.lower()] = func

    def unregister_inline_stuff(self, instance: Module, purpose: str = ""):
        cls = instance.__class__.__name__
        for store in (self.inline_handlers, self.callback_handlers):
            for name, func in list(store.items()):
                if getattr(getattr(func, "__self__", None), "__class__", type(None)).__name__ == cls:
                    del store[name]

    def register_watchers(self, instance: Module):
        cls = instance.__class__.__name__
        self.watchers = [w for w in self.watchers if w.__self__.__class__.__name__ != cls]
        self.watchers += list(instance.watchers.values())

    def register_raw_handlers(self, instance: Module):
        for _, handler in utils.iter_attrs(instance):
            if getattr(handler, "is_raw_handler", False) and self.dispatcher is not None:
                self.dispatcher.raw_handlers.append(handler)

    def unregister_raw_handlers(self, instance: Module, purpose: str = ""):
        if self.dispatcher is None:
            return
        cls = instance.__class__.__name__
        self.dispatcher.raw_handlers = [
            h for h in self.dispatcher.raw_handlers if h.__self__.__class__.__name__ != cls
        ]

    def register_bot_update_handlers(self, instance: Module):
        if self.inline is None:
            return
        for _, handler in utils.iter_attrs(instance):
            if getattr(handler, "is_bot_update_handler", False):
                for update_type in getattr(handler, "bot_update_types", []):
                    self.inline.register_bot_update_handler(f"{handler.id}_{update_type}", update_type, handler)

    def unregister_bot_update_handlers(self, instance: Module, purpose: str = ""):
        if self.inline is None:
            return
        for _, handler in utils.iter_attrs(instance):
            if getattr(handler, "is_bot_update_handler", False):
                for update_type in getattr(handler, "bot_update_types", []):
                    self.inline.unregister_bot_update_handler(f"{handler.id}_{update_type}")

    def unregister_loops(self, instance: Module, purpose: str = ""):
        for _, method in utils.iter_attrs(instance):
            if isinstance(method, InfiniteLoop):
                method.stop()

    def unregister_commands(self, instance: Module, purpose: str = ""):
        cls = instance.__class__.__name__
        for name, cmd in list(self.commands.items()):
            if getattr(getattr(cmd, "__self__", None), "__class__", type(None)).__name__ == cls:
                del self.commands[name]

    def unregister_watchers(self, instance: Module, purpose: str = ""):
        cls = instance.__class__.__name__
        self.watchers = [w for w in self.watchers if w.__self__.__class__.__name__ != cls]

    # ------------------------------------------------------------------ конфиг и готовность
    def send_config(self, skip_hook: bool = False):
        for mod in self.modules:
            self.send_config_one(mod, skip_hook)

    def send_config_one(self, mod: Module, skip_hook: bool = False):
        if hasattr(mod, "config"):
            saved = self._db.get(mod.__class__.__name__, "__config__", {})
            try:
                for conf in mod.config:
                    with contextlib.suppress(validators.ValidationError):
                        mod.config.set_no_raise(
                            conf,
                            saved[conf]
                            if conf in saved
                            else os.environ.get(f"{mod.__class__.__name__}.{conf}") or mod.config.getdef(conf),
                        )
            except AttributeError:
                logger.warning("У модуля %s неверный config: %r", mod.__class__.__name__, type(mod.config))
        if not hasattr(mod, "name"):
            with contextlib.suppress(Exception):
                mod.name = mod.strings["name"]
        if skip_hook:
            return
        if not hasattr(mod, "strings"):
            mod.strings = {}
        mod.strings = Strings(mod, self.translator)
        mod.translator = self.translator
        mod.config_complete()

    async def send_ready(self):
        await asyncio.gather(*[self.send_ready_one_wrapper(mod) for mod in list(self.modules)])

    async def send_ready_one_wrapper(self, *args, **kwargs):
        try:
            await self.send_ready_one(*args, **kwargs)
        except Exception as e:
            logger.exception("Модуль не запустился: %s", e)

    async def send_ready_one(self, mod: Module, no_self_unload: bool = False, from_dlmod: bool = False):
        if from_dlmod:
            try:
                if len(inspect.signature(mod.on_dlmod).parameters) == 2:
                    await mod.on_dlmod(self.client, self._db)
                else:
                    await mod.on_dlmod()
            except Exception:
                logger.info("on_dlmod модуля %s упал", mod.__class__.__name__, exc_info=True)

        try:
            if len(inspect.signature(mod.client_ready).parameters) == 2:
                await mod.client_ready(self.client, self._db)
            else:
                await mod.client_ready()
        except SelfUnload as e:
            if no_self_unload:
                raise
            logger.debug("%s выгрузил себя: %s", mod, e)
            with contextlib.suppress(ValueError):
                self.modules.remove(mod)
            return
        except SelfSuspend as e:
            if no_self_unload:
                raise
            logger.debug("%s приостановлен: %s", mod, e)
            return
        except Exception:
            with contextlib.suppress(ValueError):
                self.modules.remove(mod)
            raise

        source = getattr(mod, "__source__", None) or ""
        pack_url = next(
            (
                line.replace(" ", "").split("#packurl:", 1)[1]
                for line in source.splitlines()
                if line.replace(" ", "").startswith("#packurl:")
            ),
            None,
        )
        if pack_url:
            with contextlib.suppress(Exception):
                strings = await self.translator.load_module_translations(
                    pack_url, _frx.CACHE_DIR / "langpacks" / f"{mod.__class__.__name__}.yml"
                )
                if strings:
                    mod.strings.external_strings = strings

        for _, method in utils.iter_attrs(mod):
            if isinstance(method, InfiniteLoop):
                method.module_instance = mod
                if method.autostart:
                    method.start()

        self.unregister_commands(mod, "update")
        self.unregister_raw_handlers(mod, "update")
        self.unregister_bot_update_handlers(mod, "update")
        self.register_commands(mod)
        self.register_watchers(mod)
        self.register_raw_handlers(mod)
        self.register_bot_update_handlers(mod)

    async def complete_registration(self, instance: Module):
        instance.allmodules = self
        instance.internal_init()
        for module in list(self.modules):
            if module.__class__.__name__ == instance.__class__.__name__:
                if not self._remove_core_protection and str(module.__origin__).startswith("<core") and not str(
                    instance.__origin__
                ).startswith("<core"):
                    name = module.__class__.__name__
                    raise CoreOverwriteError(module=name[:-3] if name.endswith("Mod") else name)
                await self._teardown(module)
                with contextlib.suppress(ValueError):
                    self.modules.remove(module)
        self.modules.append(instance)

    async def _teardown(self, module: Module):
        with contextlib.suppress(Exception):
            await module.on_unload()
        self.unregister_raw_handlers(module)
        self.unregister_bot_update_handlers(module)
        self.unregister_loops(module)
        self.unregister_commands(module)
        self.unregister_watchers(module)
        self.unregister_inline_stuff(module)
        with contextlib.suppress(Exception):
            utils.unregister_placeholders(module.__class__.__name__)
        space = _frx.runtime
        if space is not None:
            space.forget_module(module)

    # ------------------------------------------------------------------ загрузка из исходника
    async def register_module(self, spec, module_name: str, origin: str = "<core>", save_fs: bool = False) -> Module:
        """Совместимость с Hikka: зарегистрировать модуль из importlib spec."""
        source = spec.loader.data.decode() if getattr(spec.loader, "data", None) else ""
        return await self.load_source(source, origin=origin, module_name=module_name, save_fs=save_fs, run_ready=False)

    async def load_source(
        self,
        source: str,
        *,
        origin: str = "<string>",
        module_name: str = None,
        save_fs: bool = True,
        file_name: str = None,
        from_dlmod: bool = False,
        run_ready: bool = True,
        url: str = None,
    ) -> Module:
        """Исполнить исходник модуля Hikka, создать экземпляр, зарегистрировать и запустить (client_ready)."""
        if not isinstance(source, str):
            source = source.decode("utf-8")
        if not module_name:
            stem = (file_name or (url.rstrip("/").rsplit("/", 1)[-1] if url else "") or f"__extmod_{uuid.uuid4().hex[:8]}")
            stem = re.sub(r"\.py$", "", stem)
            stem = re.sub(r"[^0-9a-zA-Z_]", "_", stem) or "module"
            # u_ — чтобы не пересечься со встроенными; у пространства Sandbox ещё и свой префикс
            module_name = f"hikka.{MODULES_NAME}.{_frx.module_prefix}u_{stem}"

        reqs = parse_requirements(source)
        if reqs:
            await install_requirements(reqs)

        friendly = f"<{'core' if origin.startswith('<core') else 'file'} {module_name}>"
        attempted = False
        while True:
            spec = importlib.machinery.ModuleSpec(module_name, StringLoader(source, friendly), origin=friendly)
            module = importlib.util.module_from_spec(spec)
            module.__file__ = str(_frx.MODULES_DIR / (file_name or (module_name.rsplit(".", 1)[-1] + ".py")))
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                break
            except ImportError as e:
                sys.modules.pop(module_name, None)
                missing = (getattr(e, "name", None) or "").split(".")[0]
                if attempted or not missing or missing in sys.modules:
                    raise
                attempted = True
                logger.info("Модулю не хватает %s — пробую поставить", missing)
                if not await install_requirements(reqs + [_alias_for(missing)]):
                    raise
            except BaseException:
                sys.modules.pop(module_name, None)
                raise

        classes = [
            value
            for value in vars(module).values()
            if inspect.isclass(value) and issubclass(value, Module) and value is not Module
        ]
        own = [c for c in classes if c.__module__ == module_name]
        cls = (own or classes or [None])[0]
        if cls is not None:
            instance = cls()
        elif hasattr(module, "register"):
            instance = module.register(module_name)
            if not isinstance(instance, Module):
                raise TypeError(f"Instance is not a Module, it is {type(instance)}")
        else:
            sys.modules.pop(module_name, None)
            raise LoadError("В файле нет класса модуля (наследника loader.Module)")

        if hasattr(module, "__version__"):
            instance.__version__ = module.__version__
        instance.__origin__ = origin
        instance.__source__ = source
        instance.__hikka_url__ = url
        instance.__hikka_file__ = file_name
        if not hasattr(instance, "name"):
            with contextlib.suppress(Exception):
                instance.name = instance.strings["name"]
            if not hasattr(instance, "name") or instance.name in (None, "Unknown"):
                instance.name = instance.__class__.__name__[:-3] if instance.__class__.__name__.endswith("Mod") else instance.__class__.__name__

        await self.complete_registration(instance)

        if not run_ready:
            return instance

        try:
            self.send_config_one(instance)
            await self.send_ready_one(instance, no_self_unload=True, from_dlmod=from_dlmod)
        except (SelfUnload, SelfSuspend) as e:
            if isinstance(e, SelfUnload):
                await self._teardown(instance)
                with contextlib.suppress(ValueError):
                    self.modules.remove(instance)
            raise
        except Exception:
            await self._teardown(instance)
            with contextlib.suppress(ValueError):
                self.modules.remove(instance)
            raise

        if save_fs and not origin.startswith("<core"):
            path = _frx.MODULES_DIR / f"{instance.__class__.__name__}.py"
            for other in _frx.MODULES_DIR.glob("*.py"):
                if other != path and other.stem.lower() == instance.__class__.__name__.lower():
                    with contextlib.suppress(Exception):
                        other.unlink()
            path.write_text(source, encoding="utf-8")
            meta = dict(self._db.get("hikka.compat", "sources", {}))
            meta[instance.__class__.__name__] = {"url": url, "file": path.name}
            self._db.set("hikka.compat", "sources", meta)
        return instance

    async def unload_module(self, classname: str) -> typing.List[str]:
        worked = []
        for module in list(self.modules):
            if classname.lower() not in (str(getattr(module, "name", "")).lower(), module.__class__.__name__.lower()):
                continue
            if not self._remove_core_protection and str(module.__origin__).startswith("<core"):
                raise CoreUnloadError(module.__class__.__name__)
            worked.append(module.__class__.__name__)
            for name in {f"{module.__class__.__name__}.py", getattr(module, "__hikka_file__", None) or ""}:
                if name:
                    with contextlib.suppress(FileNotFoundError, IsADirectoryError, PermissionError):
                        (_frx.MODULES_DIR / name).unlink()
            meta = dict(self._db.get("hikka.compat", "sources", {}))
            if meta.pop(module.__class__.__name__, None) is not None:
                self._db.set("hikka.compat", "sources", meta)
            with contextlib.suppress(ValueError):
                self.modules.remove(module)
            await self._teardown(module)
            sys.modules.pop(module.__class__.__module__, None)
        return worked

    # ------------------------------------------------------------------ библиотеки
    async def import_library(self, url: str, *, suspend_on_error: bool = False, _did_requirements: bool = False):
        def fail(exc):
            if suspend_on_error:
                raise SelfSuspend("Нужная библиотека недоступна или повреждена")
            raise exc

        if not utils.check_url(url):
            fail(ValueError("Invalid url for library"))
        cache_file = _frx.CACHE_DIR / "libraries" / (re.sub(r"[^0-9a-zA-Z_.-]", "_", url)[-150:] + ".py")
        try:
            code = await utils.fetch_text(url)
            with contextlib.suppress(Exception):
                cache_file.parent.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(code, encoding="utf-8")
        except Exception as e:
            if cache_file.exists():
                code = cache_file.read_text(encoding="utf-8")
            else:
                fail(e)

        name = "hikka.libraries." + _frx.module_prefix + re.sub(r"[^0-9a-zA-Z_]", "_", url)
        spec = importlib.machinery.ModuleSpec(name, StringLoader(code, f"<library {url}>"), origin=f"<library {url}>")
        instance = importlib.util.module_from_spec(spec)
        sys.modules[name] = instance
        try:
            spec.loader.exec_module(instance)
        except ImportError as e:
            sys.modules.pop(name, None)
            reqs = parse_requirements(code) or [_alias_for((e.name or "").split(".")[0])]
            if _did_requirements or not reqs or not await install_requirements(reqs):
                fail(e)
            return await self.import_library(url, suspend_on_error=suspend_on_error, _did_requirements=True)

        lib_obj = next(
            (v() for v in vars(instance).values() if inspect.isclass(v) and issubclass(v, Library) and v is not Library),
            None,
        )
        if not lib_obj:
            fail(ImportError("Invalid library. No class found"))
        if not lib_obj.__class__.__name__.endswith("Lib"):
            fail(ImportError(f"Invalid library. Classname {lib_obj.__class__.__name__} does not end with 'Lib'"))

        lib_obj.source_url = url.strip("/")
        lib_obj.allmodules = self
        lib_obj.internal_init()

        for old in self.libraries:
            if old.name == lib_obj.name and (
                not isinstance(getattr(old, "version", None), tuple)
                and not isinstance(getattr(lib_obj, "version", None), tuple)
                or getattr(old, "version", ()) >= getattr(lib_obj, "version", ())
            ):
                return old

        if hasattr(lib_obj, "init"):
            if not callable(lib_obj.init):
                fail(ValueError("Library init() must be callable"))
            try:
                await lib_obj.init()
            except Exception as e:
                logger.exception("init() библиотеки упал")
                fail(RuntimeError(f"Library init() failed: {e}"))

        if hasattr(lib_obj, "config") and isinstance(lib_obj.config, LibraryConfig):
            saved = lib_obj.db.get(lib_obj.__class__.__name__, "__config__", {})
            for conf in lib_obj.config:
                with contextlib.suppress(Exception):
                    lib_obj.config.set_no_raise(conf, saved[conf] if conf in saved else lib_obj.config.getdef(conf))

        if hasattr(lib_obj, "strings"):
            lib_obj.strings = Strings(lib_obj, self.translator)
        lib_obj.translator = self.translator

        for old in list(self.libraries):
            if old.name == lib_obj.name:
                if callable(getattr(old, "on_lib_update", None)):
                    with contextlib.suppress(Exception):
                        await old.on_lib_update(lib_obj)
                from ._reference_finder import replace_all_refs

                replace_all_refs(old, lib_obj)
                self.libraries.remove(old)
                self.libraries.append(lib_obj)
                return lib_obj

        self.libraries.append(lib_obj)
        return lib_obj

    async def reload_translations(self) -> bool:
        ok = await self.translator.init()
        for module in self.modules:
            with contextlib.suppress(Exception):
                module.config_complete(reload_dynamic_translate=True)
        return ok
