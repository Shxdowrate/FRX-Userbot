"""БД пространства Hikka: словарь «владелец → {ключ: значение}», хранится в user/data/hikka/db.json.

Интерфейс как у Hikka/Heroku: get / set / pointer / save / store_asset / fetch_asset и обычные методы dict.
Ключи владельцев «heroku.*» и «hikka.*» — одно и то же (модули Heroku пишут heroku.main, Hikka — hikka.main).
"""

import asyncio
import collections.abc
import copy
import json
import logging
import os
import time
import typing

from . import _frx
from .pointers import (
    BaseSerializingMiddlewareDict,
    BaseSerializingMiddlewareList,
    NamedTupleMiddlewareDict,
    NamedTupleMiddlewareList,
    PointerDict,
    PointerList,
)

__all__ = [
    "Database",
    "PointerList",
    "PointerDict",
    "NamedTupleMiddlewareDict",
    "NamedTupleMiddlewareList",
    "BaseSerializingMiddlewareDict",
    "BaseSerializingMiddlewareList",
    "NoAssetsChannel",
    "NoContentChannel",
]

logger = logging.getLogger(__name__)

ASSETS_CHAT = "hikka-assets"


class NoAssetsChannel(Exception):
    """Нет канала для ассетов."""


NoContentChannel = NoAssetsChannel


def _norm(owner):
    if isinstance(owner, str):
        if owner.startswith("heroku."):
            return "hikka." + owner[len("heroku."):]
        if owner.startswith("legacy."):
            return "hikka." + owner[len("legacy."):]
    return owner


def _serializable(value) -> bool:
    try:
        json.dumps(value)
        return True
    except Exception:
        return False


class Database(dict):
    def __init__(self, client=None, path=None):
        super().__init__()
        self._client = client
        self._path = path
        self._save_handle = None
        self._dirty = False
        self._revisions: list = []
        self._next_revision = 0.0
        self._assets_chat_id = None

    def __repr__(self):
        return object.__repr__(self)

    # ------------------------------------------------------------------ чтение / запись файла
    @property
    def _db_file(self):
        return self._path or _frx.DB_FILE

    async def init(self):
        self.read()

    def read(self):
        path = self._db_file
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except Exception:
            logger.exception("Не удалось прочитать БД Hikka")
            return
        try:
            data = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            logger.error("БД Hikka повреждена — начинаю с пустой (старая сохранена как .broken)")
            try:
                path.with_suffix(".broken.json").write_text(raw, encoding="utf-8")
            except Exception:
                pass
            return
        dict.clear(self)
        for owner, values in (data or {}).items():
            if isinstance(values, dict):
                owner = _norm(owner)
                dict.setdefault(self, owner, {}).update(values)

    def _write(self):
        self._save_handle = None
        if not self._dirty:
            return True
        path = self._db_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self, ensure_ascii=False, indent=1), encoding="utf-8")
            for attempt in range(8):
                try:
                    os.replace(tmp, path)
                    break
                except PermissionError:
                    time.sleep(0.02 * (attempt + 1))
            self._dirty = False
            return True
        except Exception:
            logger.exception("Не удалось сохранить БД Hikka")
            return False

    def process_db_autofix(self, db: dict) -> bool:
        if not _serializable(db):
            return False
        for key, value in list(db.items()):
            if not isinstance(key, (str, int)):
                continue
            if not isinstance(value, dict):
                dict.__delitem__(db, key) if isinstance(db, Database) else db.pop(key, None)
                continue
            for sub in list(value):
                if not isinstance(sub, (str, int)):
                    del value[sub]
        return True

    def save(self) -> bool:
        """Сохранить. На диск пишется с задержкой ~0.5 с (несколько set подряд — одна запись)."""
        if self._next_revision < time.time():
            try:
                self._revisions.append(copy.deepcopy(dict(self)))
            except Exception:
                pass
            self._next_revision = time.time() + 3
            del self._revisions[:-15]
        self._dirty = True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return self._write()
        if self._save_handle is None:
            self._save_handle = loop.call_later(0.5, self._write)
        return True

    def flush(self) -> bool:
        if self._save_handle is not None:
            self._save_handle.cancel()
            self._save_handle = None
        return self._write()

    async def remote_force_save(self) -> bool:
        return self.flush()

    # ------------------------------------------------------------------ dict-API
    def __getitem__(self, owner):
        return dict.__getitem__(self, _norm(owner))

    def __contains__(self, owner):
        return dict.__contains__(self, _norm(owner))

    def __setitem__(self, owner, value):
        if not _serializable(owner) or not _serializable(value):
            raise RuntimeError(f"Нельзя записать в БД несериализуемое значение ({owner!r})")
        dict.__setitem__(self, _norm(owner), value)
        self.save()

    def __delitem__(self, owner):
        dict.__delitem__(self, _norm(owner))
        self.save()

    def setdefault(self, owner, default=None):
        value = dict.setdefault(self, _norm(owner), default if default is not None else {})
        self.save()
        return value

    def pop(self, owner, *default):
        value = dict.pop(self, _norm(owner), *default)
        self.save()
        return value

    def update(self, *args, **kwargs):
        items = dict(*args, **kwargs)
        for owner, value in items.items():
            dict.__setitem__(self, _norm(owner), value)
        self.save()

    def clear(self):
        dict.clear(self)
        self.save()

    # ------------------------------------------------------------------ API Hikka
    def _get_raw(self, owner, key, default=None):
        try:
            return dict.__getitem__(self, _norm(owner))[key]
        except (KeyError, TypeError):
            return default

    def get(self, owner, key=None, default=None):
        """get(owner, key, default) как в Hikka; get(owner) — весь раздел (поведение dict)."""
        if key is None and default is None:
            return copy.deepcopy(dict.get(self, _norm(owner)))
        return copy.deepcopy(self._get_raw(owner, key, default))

    def set(self, owner, key, value) -> bool:
        if not _serializable(owner):
            raise RuntimeError(f"Ключ владельца {owner!r} не сериализуется в JSON")
        if not _serializable(key):
            raise RuntimeError(f"Ключ {key!r} не сериализуется в JSON")
        if not _serializable(value):
            raise RuntimeError(f"Значение для {key!r} ({type(value).__name__}) не сериализуется в JSON")
        dict.setdefault(self, _norm(owner), {})[key] = value
        return self.save()

    def pointer(self, owner, key, default=None, item_type=None):
        value = self._get_raw(owner, key, default)
        current = self._get_raw(owner, key, None)
        if current and default is not None and type(current) is not type(default):
            raise ValueError(
                f"Нельзя сменить тип указателя в БД (сейчас {type(current).__name__}, запрошен {type(default).__name__})"
            )
        if isinstance(value, list):
            pointer = PointerList(self, owner, key, default)
            if item_type is not None:
                return NamedTupleMiddlewareList(pointer, item_type)
            return pointer
        if isinstance(value, dict):
            pointer = PointerDict(self, owner, key, default)
            if item_type is not None:
                return NamedTupleMiddlewareDict(pointer, item_type)
            return pointer
        if isinstance(value, collections.abc.Hashable):
            return value
        raise ValueError(f"Указатель для типа {type(value).__name__} не поддерживается")

    # ------------------------------------------------------------------ ассеты (сообщения в служебном канале)
    async def _assets_chat(self):
        if self._assets_chat_id:
            return self._assets_chat_id
        cached = self._get_raw("hikka.forums", "channel_id") or self._get_raw("hikka.main", "assets_chat")
        if cached:
            self._assets_chat_id = cached
            return cached
        from . import utils

        client = self._client or (_frx.runtime.client if _frx.runtime else None)
        if client is None:
            raise NoAssetsChannel("Клиент не готов")
        peer, _ = await utils.asset_channel(
            client, ASSETS_CHAT, "🌙 Ассеты модулей Hikka (FRX HikkaCompat)", silent=True, archive=True
        )
        self._assets_chat_id = peer.id
        self.set("hikka.main", "assets_chat", peer.id)
        return peer.id

    async def store_asset(self, message) -> int:
        from telethon.tl.types import Message

        client = self._client or _frx.runtime.client
        chat = await self._assets_chat()
        if isinstance(message, Message):
            sent = await client.send_message(chat, message)
        else:
            sent = await client.send_file(chat, message, force_document=True)
        return sent.id

    async def fetch_asset(self, asset_id: int):
        client = self._client or _frx.runtime.client
        chat = await self._assets_chat()
        result = await client.get_messages(chat, ids=[asset_id])
        return result[0] if result else None

    async def ensure_content_channel(self):
        return await self._assets_chat()
