"""
core/cache.py

MemoryCache / DynamicCache — то же самое, что core/database.py (`db`), но
БЕЗ записи на диск: значения живут только пока работает процесс юзербота,
рестарт/перезагрузка стирает всё. Полезно для рантайм-состояния, которое
незачем таскать между перезапусками — например, для передачи данных между
хэндлерами инлайн-формы, временных счётчиков, "черновиков" и т.д., чтобы не
городить свою структуру данных в замыканиях модуля.

API 1-в-1 зеркалит `db`:
    cache.get(key) / cache.set(key, value)   -> "modulename:key"
    cache.gget(key) / cache.gset(key, value) -> "сырой" ключ, без префикса
                                                  (доступ к кэшу других модулей)
    cache.delete(key) / cache.gdelete(key)
    cache.list(modulename=None)              -> все ключи заданного модуля
    cache.glist(prefix=None)
"""

from .context import current_module


class MemoryCache:
    """Низкоуровневое in-memory key-value хранилище (аналог Database, но без диска)."""

    def __init__(self):
        self._data = {}

    async def get(self, key, default=None):
        return self._data.get(key, default)

    async def set(self, key, value):
        self._data[key] = value
        return value

    async def delete(self, key):
        if key in self._data:
            del self._data[key]
            return True
        return False

    async def list_keys(self, prefix=None):
        if prefix is None:
            return list(self._data.keys())
        return [k for k in self._data.keys() if k.startswith(prefix)]


class DynamicCache:
    """
    Объект `cache`, доступный внутри модулей — интерфейс идентичен `db`
    (core/database.py: DynamicDB), только без сохранения на диск.
    """

    def __init__(self, storage: MemoryCache):
        self.storage = storage

    def _current_module(self):
        module_name = current_module.get()
        if module_name is None:
            raise RuntimeError(
                "cache.get/cache.set вызваны вне контекста модуля — "
                "используйте cache.gget/cache.gset, либо вызывайте это "
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
