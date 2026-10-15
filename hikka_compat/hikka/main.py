"""hikka.main — модули берут отсюда имя раздела БД (main.__name__ == "hikka.main") и пути.

BASE_DIR, BASE_PATH, CONFIG_PATH и hikka / heroku зависят от пространства (основное или WebIDE Sandbox),
поэтому это свойства модуля, а не константы.
"""

import os
import sys
import types
from pathlib import Path

from . import _frx

IS_DOCKER = "DOCKER" in os.environ
IS_CODESPACES = False
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "")
LATIN_MOCK = []


def _base_dir() -> str:
    data = _frx.DATA_DIR
    return str(data.resolve()) if data.exists() else os.path.abspath(str(data))


def _read_config() -> dict:
    import json

    try:
        return json.loads(Path(_base_dir(), "config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def get_config_key(key: str):
    return _read_config().get(key, False)


def save_config_key(key: str, value) -> bool:
    import json

    config = _read_config()
    config[key] = value
    path = Path(_base_dir(), "config.json")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception:
        return False


def get_app_name() -> str:
    return "FRX HikkaCompat"


def generate_app_name() -> str:
    return get_app_name()


class _MainModule(types.ModuleType):
    BASE_DIR = property(lambda self: _base_dir())
    BASE_PATH = property(lambda self: Path(_base_dir()))
    CONFIG_PATH = property(lambda self: Path(_base_dir()) / "config.json")
    # объект «юзербота» — пространство (у Sandbox своё)
    hikka = property(lambda self: _frx.runtime, lambda self, value: None)
    heroku = hikka


sys.modules[__name__].__class__ = _MainModule
