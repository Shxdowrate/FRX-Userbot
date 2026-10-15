"""Пространство Hikka внутри FRX: запуск, остановка, загрузка модулей, подключение к клиенту и боту FRX.

Пространство живёт поверх уже работающего FRX: использует его Telegram-клиент (через «вид», см. tl_cache)
и его стационарного бота (см. inline/core). Всё, что пространство подключает (обработчики событий,
middleware бота, задачи, перенаправление импортов), оно же снимает при остановке.
"""

import asyncio
import contextlib
import logging
import os
import re
import sys
import time
import traceback
import typing
from pathlib import Path

from telethon import events

from . import _aliases, _frx, log, main
from .database import Database
from .dispatcher import CommandDispatcher
from .inline.core import InlineManager
from .loader import Modules
from .tl_cache import CustomTelegramClient
from .translations import Translator

logger = logging.getLogger("hikka.space")

CORE_MODULES_DIR = Path(__file__).resolve().parent / "modules"
_SANDBOX_NAME = re.compile(r"^hikka\.(modules|libraries)\.sb\d+_\d+_")
DEFAULT_PREFIX = ","


class Space:
    def __init__(self, engine):
        self.engine = engine
        self.default_prefix = DEFAULT_PREFIX
        self.started = False
        self.started_at = None
        self.client: typing.Optional[CustomTelegramClient] = None
        self.db: typing.Optional[Database] = None
        self.translator: typing.Optional[Translator] = None
        self.modules: typing.Optional[Modules] = None
        self.dispatcher: typing.Optional[CommandDispatcher] = None
        self.inline: typing.Optional[InlineManager] = None
        self.failed: typing.Dict[str, str] = {}       # файл → причина, по которой модуль не загрузился
        self._handlers: list = []                      # обёртки обработчиков на клиенте (снять при остановке)
        self._tasks: set = set()
        self._lock = asyncio.Lock()
        self._stopped = False
        self.tg_queue: list = []                       # ошибки/логи для отправки владельцу ботом (log.tg_sender)
        self.tg_level = logging.ERROR                  # Tester: tglog_level
        self.tg_ignore_common = True                   # Tester: ignore_common

    # ------------------------------------------------------------------ служебное
    def track_handler(self, wrapper):
        self._handlers.append(wrapper)

    def track_task(self, task):
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def forget_module(self, module):
        """Модуль выгружен (обработчики, добавленные им напрямую в клиент, снимутся при остановке пространства)."""

    @property
    def prefix(self) -> str:
        return self.modules.get_prefix() if self.modules else (self.db.get("hikka.main", "command_prefix", DEFAULT_PREFIX) if self.db else DEFAULT_PREFIX)

    def _ffmpeg_to_path(self):
        with contextlib.suppress(Exception):
            from core import media_tools

            ff = media_tools.find_ffmpeg()
            if ff:
                folder = str(Path(ff).resolve().parent)
                if folder not in os.environ.get("PATH", "").split(os.pathsep):
                    os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")

    # ------------------------------------------------------------------ запуск
    async def start(self):
        async with self._lock:
            if self.started:
                return
            bridge = _frx.current().sandbox             # пространство в WebIDE Sandbox (см. _sandbox)
            real = self.engine.client
            if real is None and bridge is None:
                raise RuntimeError("Telegram-клиент FRX ещё не запущен")
            _frx.runtime = self
            _aliases.install(self)
            log.install(self)

            if bridge is not None:
                real = bridge.make_client(self)
                client = CustomTelegramClient.view(real, self, base=type(real))
            else:
                self._ffmpeg_to_path()
                client = CustomTelegramClient.view(real, self)
            me = await real.get_me()
            client.tg_id = me.id
            client.hikka_me = me
            self.client = client

            self.db = Database(client)
            self.db.read()
            self.translator = Translator(client, self.db)
            await self.translator.init()
            self.modules = Modules(client, self.db, [client], self.translator)
            client.loader = self.modules
            client.hikka_db = self.db
            client.allclients = [client]
            self.dispatcher = CommandDispatcher(self.modules, client, self.db)
            if bridge is not None:
                self.dispatcher.spawn = bridge.spawn          # команды — задачами песочницы (видны в журнале)
            self.modules.dispatcher = self.dispatcher
            client.dispatcher = self.dispatcher
            self.inline = InlineManager(client, self.db, self.modules)
            self.modules.inline = self.inline
            client.hikka_inline = self.inline
            main.hikka = main.heroku = self

            client.add_event_handler(self.dispatcher.handle_incoming, events.NewMessage())
            client.add_event_handler(self.dispatcher.handle_incoming, events.ChatAction())
            client.add_event_handler(self.dispatcher.handle_command, events.NewMessage(forwards=False))
            client.add_event_handler(self.dispatcher.handle_command, events.MessageEdited())
            client.add_event_handler(self.dispatcher.handle_raw, events.Raw())
            if bridge is not None:
                bridge.attach(self)

            await self.attach_bot()
            self.track_task(asyncio.ensure_future(self._log_loop()))
            await self._load_all()
            self.modules.fully_loaded = True
            self.started = True
            self.started_at = time.time()
            _frx.tlog(
                f"пространство Hikka запущено: модулей {len(self.modules.modules)}, префикс «{self.prefix}»"
                + (f", не загрузились: {', '.join(self.failed)}" if self.failed else "")
            )

    async def _log_loop(self):
        """Обработчик ошибок как в Hikka: раз в 3 с отправить владельцу накопленные ошибки модулей."""
        while True:
            await asyncio.sleep(3)
            try:
                await log.tg_sender(self)
            except asyncio.CancelledError:
                raise
            except Exception:
                logging.getLogger("hikka.log.tg").debug("цикл логов", exc_info=True)

    async def attach_bot(self) -> bool:
        """Подключить инлайн к стационарному боту FRX (если он уже поднят)."""
        if self.inline is None or self.inline.init_complete:
            return bool(self.inline and self.inline.init_complete)
        bridge = _frx.current().sandbox
        if bridge is not None:
            if not getattr(bridge.s, "bot_enabled", True):
                return False
            bot, dp = bridge.bot, None
        else:
            bot = getattr(self.engine, "_aiogram_bot", None)
            dp = getattr(self.engine, "_aiogram_dp", None)
            if bot is None or dp is None:
                return False
        ok = await self.inline.attach(bot, dp)
        if ok and self.modules is not None:
            for mod in self.modules.modules:
                self.modules.register_bot_update_handlers(mod)
        return ok

    async def _load_all(self):
        self.failed = {}
        core = sorted(p for p in CORE_MODULES_DIR.glob("*.py") if not p.name.startswith("_"))
        user = sorted(p for p in _frx.MODULES_DIR.glob("*.py") if not p.name.startswith("_"))
        registered = []
        for path, origin in [(p, "<core>") for p in core] + [(p, "<file>") for p in user]:
            try:
                source = path.read_text(encoding="utf-8")
                module_name = f"hikka.modules.{path.stem}" if origin == "<core>" else None
                mod = await self.modules.load_source(
                    source, origin=origin, module_name=module_name, save_fs=False, file_name=path.name, run_ready=False,
                    url=(self.db.get("hikka.compat", "sources", {}).get(path.stem) or {}).get("url"),
                )
                registered.append((path, mod))
            except Exception as e:
                self.failed[path.name] = f"{type(e).__name__}: {e}"
                logger.error("Модуль %s не загрузился: %s", path.name, e, exc_info=True)

        for path, mod in list(registered):
            try:
                self.modules.send_config_one(mod)
            except Exception as e:
                self.failed[path.name] = f"{type(e).__name__}: {e}"
                logger.error("config_complete модуля %s упал", path.name, exc_info=True)
                registered.remove((path, mod))
                with contextlib.suppress(ValueError):
                    self.modules.modules.remove(mod)

        async def ready(path, mod):
            try:
                await self.modules.send_ready_one(mod)
            except Exception as e:
                self.failed[path.name] = f"{type(e).__name__}: {e}"
                logger.error("client_ready модуля %s упал", path.name, exc_info=True)
                with contextlib.suppress(Exception):
                    await self.modules._teardown(mod)

        await asyncio.gather(*(ready(p, m) for p, m in registered))

    # ------------------------------------------------------------------ остановка
    async def stop(self):
        async with self._lock:
            if not self.started and (self.modules is None or self._stopped):
                return
            self.started = False
            self._stopped = True
            if self.modules is not None:
                for mod in list(self.modules.modules):
                    with contextlib.suppress(Exception):
                        await asyncio.wait_for(self.modules._teardown(mod), 8)
                self.modules.modules.clear()
                self.modules.libraries.clear()
            if self.inline is not None:
                with contextlib.suppress(Exception):
                    await self.inline.detach()
            real = self.engine.client if self.client is None else self.client.real_client
            for wrapper in self._handlers:
                with contextlib.suppress(Exception):
                    real.remove_event_handler(wrapper)
            self._handlers.clear()
            for task in list(self._tasks):
                task.cancel()
            self._tasks.clear()
            if self.db is not None:
                self.db.flush()
            bridge = _frx.current().sandbox
            if bridge is not None:
                bridge.detach()
            prefix = _frx.module_prefix
            for name in list(sys.modules):
                if not name.startswith(("hikka.modules.", "hikka.libraries.")):
                    continue
                own = name.split(".", 2)[2].startswith(prefix) if prefix else not _SANDBOX_NAME.match(name)
                if own:
                    sys.modules.pop(name, None)
            _aliases.uninstall(self)
            log.uninstall(self)
            main.hikka = main.heroku = None
            if _frx.runtime is self:
                _frx.runtime = None
            _frx.tlog("пространство Hikka остановлено")

    async def restart(self):
        await self.stop()
        self.__init__(self.engine)
        await self.start()

    # ------------------------------------------------------------------ сводка
    def status(self) -> dict:
        mods = self.modules.modules if self.modules else []
        return {
            "started": self.started,
            "prefix": self.prefix,
            "modules": len([m for m in mods if not str(m.__origin__).startswith("<core")]),
            "core": len([m for m in mods if str(m.__origin__).startswith("<core")]),
            "commands": len(self.modules.commands) if self.modules else 0,
            "inline": bool(self.inline and self.inline.init_complete),
            "bot": getattr(self.inline, "bot_username", None),
            "failed": dict(self.failed),
            "uptime": int(time.time() - self.started_at) if self.started_at else 0,
        }
