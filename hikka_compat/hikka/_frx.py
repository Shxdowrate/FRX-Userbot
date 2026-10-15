"""Связь пространства Hikka с FRX: ссылки на ядро FRX, рантайм и папки. Заполняет HikkaCompat при запуске.

Пространств может быть несколько одновременно: основное (в FRX) и пространства WebIDE Sandbox — те работают в
своём потоке со своим event loop. Пакет `hikka` у них общий (один sys.modules), поэтому всё, что отличается у
пространств (ядро, рантайм, папки, префикс имён модулей), хранится в «контексте», привязанном к event loop.
Код пакета просто обращается к `_frx.engine`, `_frx.runtime`, `_frx.MODULES_DIR`… и получает значения
своего пространства. Вне event loop пространства Sandbox (основной FRX, потоки executor'а) — основной контекст.
"""

import asyncio
import sys
import threading
import types
from pathlib import Path


class _Context:
    def __init__(self):
        self.engine = None             # объект Core FRX (`import engine`) или SandboxEngine
        self.runtime = None            # hikka._space.Space — пространство этого контекста (или None)
        self.sandbox = None            # hikka._sandbox.Bridge — если пространство запущено в WebIDE Sandbox
        self.module_prefix = ""        # префикс имён модулей в sys.modules (у Sandbox свой, чтобы не пересечься)
        self.DATA_DIR = Path("user/data/hikka")          # БД, модули — попадает в резервные копии FRX
        self.MODULES_DIR = self.DATA_DIR / "modules"     # файлы загруженных модулей Hikka
        self.CACHE_DIR = Path("user/data/cache/hikka")   # кэш (библиотеки, языковые пакеты) — в бэкап не идёт
        self.DB_FILE = self.DATA_DIR / "db.json"


_main = _Context()
_by_loop: dict = {}                    # event loop → контекст пространства Sandbox
_by_thread: dict = {}                  # id потока Sandbox → контекст (для кода без запущенного loop)
_FIELDS = ("engine", "runtime", "sandbox", "module_prefix", "DATA_DIR", "MODULES_DIR", "CACHE_DIR", "DB_FILE")


def current() -> _Context:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and _by_loop:
        ctx = _by_loop.get(loop)
        if ctx is not None:
            return ctx
    if _by_thread:
        return _by_thread.get(threading.get_ident(), _main)
    return _main


def enter_sandbox(loop=None) -> _Context:
    """Отдельный контекст для пространства в WebIDE Sandbox (вызывается в потоке песочницы)."""
    loop = loop or asyncio.get_running_loop()
    prune()
    ctx = _by_loop.get(loop)
    if ctx is None:
        ctx = _Context()
        ctx.module_prefix = f"sb{len(_by_loop) + 1}_{id(loop) % 100000}_"
        _by_loop[loop] = ctx
        _by_thread[threading.get_ident()] = ctx
    return ctx


def leave_sandbox(loop=None):
    try:
        loop = loop or asyncio.get_running_loop()
    except RuntimeError:
        return
    ctx = _by_loop.pop(loop, None)
    for tid, item in list(_by_thread.items()):
        if item is ctx:
            _by_thread.pop(tid, None)


def prune() -> int:
    """Забыть пространства остановленных песочниц (их поток и loop уже закрыты). → сколько песочниц живо."""
    for loop, ctx in list(_by_loop.items()):
        if loop.is_closed() or not loop.is_running():
            _by_loop.pop(loop, None)
            for tid, item in list(_by_thread.items()):
                if item is ctx:
                    _by_thread.pop(tid, None)
            for name in ("_aliases", "log"):
                mod = sys.modules.get(f"{__package__}.{name}")
                users = getattr(mod, "_users", None)
                if users is not None:
                    users.discard(ctx.runtime)
    return len(_by_loop)


def is_sandbox() -> bool:
    return current().sandbox is not None


def configure(data_dir, cache_dir):
    ctx = current()
    ctx.DATA_DIR = Path(data_dir)
    ctx.MODULES_DIR = ctx.DATA_DIR / "modules"
    ctx.CACHE_DIR = Path(cache_dir)
    ctx.DB_FILE = ctx.DATA_DIR / "db.json"
    for d in (ctx.DATA_DIR, ctx.MODULES_DIR, ctx.CACHE_DIR):
        d.mkdir(parents=True, exist_ok=True)


def tlog(text):
    """Строка в терминал FRX (в Sandbox — в журнал песочницы)."""
    engine = current().engine
    try:
        if engine is not None:
            engine.tlog(f"[hikka] {text}")
            return
    except Exception:
        pass
    print(f"[FRX] [hikka] {text}")


class _FrxModule(types.ModuleType):
    pass


def _field(name):
    return property(lambda self: getattr(current(), name), lambda self, value: setattr(current(), name, value))


for _name in _FIELDS:
    setattr(_FrxModule, _name, _field(_name))
del _name

sys.modules[__name__].__class__ = _FrxModule
