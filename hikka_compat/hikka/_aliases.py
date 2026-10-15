"""Перенаправление импортов для модулей Hikka/Heroku.

    heroku, heroku.*          → hikka, hikka.*            (тот же объект модуля)
    hikkatl.*, herokutl.*     → telethon.*                (Telethon FRX; форки не ставятся)
    hikkatl.extensions.html   → hikka._html               (HTML с тегами форков)

Устанавливается, пока работает хотя бы одно пространство Hikka, и снимается, когда остановлено последнее.
"""

import importlib
import importlib.abc
import importlib.machinery
import importlib.util
import sys

_TL_FORKS = ("hikkatl", "herokutl", "hikka_tl", "heroku_tl")
_PKG_ALIASES = ("heroku", "legacy")
_SPECIAL = {
    "extensions": "hikka._tl_extensions",
    "extensions.html": "hikka._html",
}
# aiogram 2 → aiogram 3 (только то, чего в aiogram 3 нет)
_AIOGRAM2 = {
    "aiogram.utils.exceptions": "hikka._aiogram2",
    "aiogram.types.inline_keyboard": "aiogram.types",
}


def _target(fullname: str):
    if fullname in _AIOGRAM2:
        return _AIOGRAM2[fullname]
    top, _, rest = fullname.partition(".")
    if top in _TL_FORKS:
        if rest in _SPECIAL:
            return _SPECIAL[rest]
        return "telethon" + (f".{rest}" if rest else "")
    if top in _PKG_ALIASES:
        return "hikka" + (f".{rest}" if rest else "")
    return None


class _AliasLoader(importlib.abc.Loader):
    def __init__(self, target: str):
        self.target = target

    def create_module(self, spec):
        return importlib.import_module(self.target)

    def exec_module(self, module):
        return None


class AliasFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        real = _target(fullname)
        if real is None:
            return None
        try:
            importlib.util.find_spec(real)
        except (ImportError, ValueError):
            return None
        if importlib.util.find_spec(real) is None:
            return None
        return importlib.machinery.ModuleSpec(fullname, _AliasLoader(real), is_package=True)


_finder = AliasFinder()
_users = set()            # кто включил перенаправление (основное пространство, пространства Sandbox)


def install(owner=None):
    _users.add(owner)
    if _finder not in sys.meta_path:
        sys.meta_path.insert(0, _finder)


def uninstall(owner=None, force: bool = False):
    """Снять перенаправление. Пока им пользуется другое пространство — остаётся (если не force)."""
    _users.discard(owner)
    if _users and not force:
        return
    _users.clear()
    while _finder in sys.meta_path:
        sys.meta_path.remove(_finder)
    for name in list(sys.modules):
        if _target(name) is not None:
            sys.modules.pop(name, None)
