"""
core/database.py

Database    — низкоуровневое JSON-хранилище "ключ -> значение".
DynamicDB   — обёртка, которую видят модули как объект `db`.
              Она сама определяет, какой модуль сейчас выполняется
              (через core.context.current_module), и подставляет
              префикс "modulename:" для db.get/db.set — как описано в ТЗ.
              db.gget/db.gset работают с "сырыми" ключами напрямую,
              позволяя менять значения в БД других модулей.
"""

import json
import asyncio
from pathlib import Path

from .context import current_module
from .paths import DB_PATH


class Database:
    """Простое персистентное key-value хранилище на JSON-файле."""

    def __init__(self, path=DB_PATH):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._data = {}
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text(encoding='utf-8'))
            except Exception:
                self._data = {}
        else:
            self._data = {}

    def _save(self):
        self.path.write_text(
            json.dumps(self._data, ensure_ascii=False, indent=2),
            encoding='utf-8',
        )

    async def get(self, key, default=None):
        async with self._lock:
            return self._data.get(key, default)

    async def set(self, key, value):
        async with self._lock:
            self._data[key] = value
            self._save()
        return value

    async def delete(self, key):
        async with self._lock:
            if key in self._data:
                del self._data[key]
                self._save()
                return True
            return False

    async def list_keys(self, prefix=None):
        async with self._lock:
            if prefix is None:
                return list(self._data.keys())
            return [k for k in self._data.keys() if k.startswith(prefix)]


class DynamicDB:
    """
    Объект `db`, доступный внутри модулей.

    db.get(key) / db.set(key, value)   -> реальный ключ "modulename:key"
    db.gget(key) / db.gset(key, value) -> "сырой" ключ, без префикса
                                            (доступ к любым данным, в т.ч.
                                            других модулей)
    db.list(modulename=None)           -> все ключи заданного модуля
                                            (по умолчанию — текущего)
    """

    def __init__(self, storage: Database):
        self.storage = storage

    def _current_module(self):
        module_name = current_module.get()
        if module_name is None:
            raise RuntimeError(
                "db.get/db.set вызваны вне контекста модуля — "
                "используйте db.gget/db.gset, либо вызывайте это "
                "изнутри хэндлера модуля"
            )
        return module_name

    async def get(self, key, default=None):
        return await self.storage.get(f"{self._current_module()}:{key}", default)

    async def set(self, key, value):
        return await self.storage.set(f"{self._current_module()}:{key}", value)

    async def delete(self, key):
        return await self.storage.delete(f"{self._current_module()}:{key}")

    async def gget(self, key, default=None):
        return await self.storage.get(key, default)

    async def gset(self, key, value):
        return await self.storage.set(key, value)

    async def gdelete(self, key):
        return await self.storage.delete(key)

    async def list(self, modulename=None):
        modulename = modulename or self._current_module()
        prefix = f"{modulename}:"
        keys = await self.storage.list_keys(prefix)
        result = {}
        for k in keys:
            result[k[len(prefix):]] = await self.storage.get(k)
        return result

    async def glist(self, prefix=None):
        keys = await self.storage.list_keys(prefix)
        result = {}
        for k in keys:
            result[k] = await self.storage.get(k)
        return result
