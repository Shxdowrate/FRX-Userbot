"""
core/botlog.py

Лимитер для engine.botlog() — защита ЛС владельца от флуда логами.

Модель: token bucket (ёмкость `burst`, один токен восстанавливается за
`interval` секунд) + очередь ожидания:
  - есть токен и никто не ждёт — сообщение уходит сразу;
  - токена нет, режим "wait" (по умолчанию) — сообщение встаёт в очередь
    (не длиннее `queue_size`) и уходит, когда накапливается токен;
  - очередь переполнена, либо режим "drop" — сообщение "сжигается" (не
    отправляется), счётчик пропущенных растёт. Когда поток стихает, уходит ОДНО
    сводное сообщение "пропущено N".

Настройки берутся из конфигов модуля Logging (botlog_burst, botlog_interval,
botlog_queue, botlog_wait) — см. Core._botlog_settings().
"""

import asyncio
import time
from collections import deque

from . import visual as visual_module

DEFAULT_SETTINGS = {'burst': 5, 'interval': 2.0, 'queue_size': 20, 'wait': True}
SETTINGS_TTL = 5.0


class BotLogLimiter:
    def __init__(self, deliver, get_settings, tlog=print):
        self._deliver = deliver              # async (html_text) -> None, бросает при неудаче
        self._get_settings = get_settings    # async () -> dict, см. DEFAULT_SETTINGS
        self._tlog = tlog
        self._settings = dict(DEFAULT_SETTINGS)
        self._settings_at = 0.0
        self._tokens = float(DEFAULT_SETTINGS['burst'])
        self._updated = time.monotonic()
        self._queue = deque()                # [(text, future, prepaid)]
        self._dropped = 0
        self._last_drop = 0.0
        self._worker = None

    # -- токены --------------------------------------------------------- #

    def _refill(self):
        now = time.monotonic()
        burst = max(int(self._settings['burst']), 1)
        interval = max(float(self._settings['interval']), 0.05)
        self._tokens = min(float(burst), self._tokens + (now - self._updated) / interval)
        self._updated = now

    async def _wait_token(self):
        while True:
            self._refill()
            if self._tokens >= 1:
                self._tokens -= 1
                return
            interval = max(float(self._settings['interval']), 0.05)
            await asyncio.sleep(max((1 - self._tokens) * interval, 0.05))

    async def _refresh_settings(self):
        if time.monotonic() - self._settings_at < SETTINGS_TTL:
            return
        try:
            fresh = await self._get_settings()
            self._settings = {**DEFAULT_SETTINGS, **fresh}
        except Exception:
            pass
        self._settings_at = time.monotonic()

    # -- публичное ------------------------------------------------------ #

    def submit(self, text):
        """Ставит text на отправку. Возвращает Future: 'sent' | 'dropped' | 'failed'."""
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        self._refill()

        if not any(not p for _, _, p in self._queue) and self._tokens >= 1:
            self._tokens -= 1                                  # уходит сразу, токен уже оплачен
            self._queue.append((text, fut, True))
        else:
            room = int(self._settings['queue_size']) if self._settings['wait'] else 0
            waiting = sum(1 for _, _, p in self._queue if not p)
            if waiting >= room:
                self._dropped += 1
                self._last_drop = time.monotonic()
                fut.set_result('dropped')
            else:
                self._queue.append((text, fut, False))

        self._kick()
        return fut

    def _kick(self):
        if self._worker is None or self._worker.done():
            self._worker = asyncio.ensure_future(self._run())

    # -- воркер --------------------------------------------------------- #

    async def _send(self, text, fut):
        try:
            await self._deliver(text)
            result = 'sent'
        except Exception as e:
            self._tlog(f"[botlog] не удалось отправить лог: {e}")
            result = 'failed'
        if fut is not None and not fut.done():
            fut.set_result(result)

    async def _run(self):
        while True:
            await self._refresh_settings()
            if self._queue:
                text, fut, prepaid = self._queue.popleft()
                if not prepaid:
                    await self._wait_token()
                await self._send(text, fut)
                continue
            if self._dropped:
                interval = max(float(self._settings['interval']), 0.05)
                quiet = self._last_drop + interval - time.monotonic()
                if quiet > 0:                                   # поток ещё не стих
                    await asyncio.sleep(quiet)
                    continue
                count, self._dropped = self._dropped, 0
                await self._wait_token()
                await self._send(
                    visual_module.warn('<b>Лимит botlog</b>') + "\n"
                    + visual_module.field('пропущено', count) + "\n"
                    + visual_module.hint('сообщения сожжены из-за флуда'),
                    None,
                )
                continue
            return
