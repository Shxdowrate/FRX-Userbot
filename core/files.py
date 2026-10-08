"""
core/files.py

FileRegistry — реестр файлов юзербота. Метаданные (id, путь, имя, тип, вес)
хранятся в общей БД под ключами "core:files:<id>", сами файлы физически
лежат в user/data/files/.

Через этот реестр работают утилиты:
    download_reply_file(message) -> dict
    reply_file(message, файл/ID, text)
    get_file(...) -> dict | None
    delete_file(...) -> bool
    create_file(type, name, text) -> dict
"""

import uuid
import os
from pathlib import Path

from .paths import FILES_DIR
FILES_DIR.mkdir(parents=True, exist_ok=True)

_PREFIX = 'core:files:'


class FileRegistry:
    def __init__(self, storage):
        # storage — объект core.database.Database (низкоуровневое хранилище)
        self.storage = storage

    async def register(self, path: str, name: str, ftype: str, size: int) -> dict:
        file_id = uuid.uuid4().hex[:12]
        obj = {'id': file_id, 'path': str(path), 'name': name, 'type': ftype, 'size': size}
        await self.storage.set(_PREFIX + file_id, obj)
        return obj

    async def get(self, query=None, ftype=None):
        """
        Ищет файл по ID, названию, пути или весу.
        query — искомое значение (строка/число), ftype — опционально
        ограничивает поиск заданным типом.
        """
        keys = await self.storage.list_keys(_PREFIX)
        for key in keys:
            obj = await self.storage.get(key)
            if obj is None:
                continue
            if query is not None and str(query) in (
                obj['id'], obj['name'], obj['path'], str(obj['size'])
            ):
                if ftype is None or obj['type'] == ftype:
                    return obj
        return None

    async def delete(self, file_id=None, path=None):
        """Удаляет файл (запись из реестра + сам файл с диска)."""
        keys = await self.storage.list_keys(_PREFIX)
        for key in keys:
            obj = await self.storage.get(key)
            if obj is None:
                continue
            if (file_id and obj['id'] == file_id) or (path and obj['path'] == path):
                await self.storage.delete(key)
                if os.path.exists(obj['path']):
                    os.remove(obj['path'])
                return True
        return False

    async def create(self, ftype: str, name: str, text) -> dict:
        """Создаёт файл с заданным содержимым (str или bytes) и регистрирует его."""
        path = FILES_DIR / name
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding='utf-8')
        size = path.stat().st_size
        return await self.register(str(path), name, ftype, size)
