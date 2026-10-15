"""Кэш скачанных модулей: если репозиторий недоступен, модуль берётся из последней скачанной копии."""

import hashlib
import logging
import re

from . import _frx, utils

logger = logging.getLogger(__name__)


class LocalStorage:
    def _dir(self):
        path = _frx.CACHE_DIR / "modules"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _path(self, repo: str, module_name: str):
        key = hashlib.sha256(f"{repo}/{module_name}".encode()).hexdigest()[:24]
        return self._dir() / f"{re.sub(r'[^0-9a-zA-Z_]', '_', module_name)[:40]}_{key}.py"

    def save(self, repo: str, module_name: str, module_code: str):
        try:
            self._path(repo, module_name).write_text(module_code, encoding="utf-8")
        except Exception:
            logger.debug("Не удалось сохранить кэш модуля", exc_info=True)

    def fetch(self, repo: str, module_name: str):
        path = self._path(repo, module_name)
        return path.read_text(encoding="utf-8") if path.exists() else None


class RemoteStorage:
    def __init__(self, client=None):
        self._client = client
        self._local = LocalStorage()

    @staticmethod
    def _parse_url(url: str):
        url = url.strip()
        repo, _, name = url.rstrip("/").rpartition("/")
        return repo, re.sub(r"\.py$", "", name), url

    async def preload(self, urls):
        for url in urls:
            try:
                await self.fetch(url)
            except Exception:
                pass

    async def fetch(self, url: str, auth: str = None) -> str:
        repo, name, url = self._parse_url(url)
        try:
            headers = {"Authorization": f"token {auth}"} if auth else None
            code = await utils.fetch_text(url, headers=headers)
        except Exception:
            cached = self._local.fetch(repo, name)
            if cached is not None:
                logger.warning("Репозиторий недоступен — беру модуль %s из кэша", name)
                return cached
            raise
        self._local.save(repo, name, code)
        return code
