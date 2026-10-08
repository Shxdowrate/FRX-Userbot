"""
core/waiters.py

engine.wait_message(chat_id=None, user_id=None, timeout=None) — заменяет
старые wait_message()/wait_my_message() одной функцией с фильтрами:

    chat_id: None (по умолчанию) -> ТЕКУЩИЙ чат (тот, где вызвана команда).
             'me'                -> Избранное (Saved Messages).
             int                 -> конкретный чат.
             list из перечисленного выше -> любое совпадение.
    user_id: None (по умолчанию) -> сообщение от ЛЮБОГО юзера в этом чате
             (Telegram шлёт NewMessage и на исходящие сообщения тоже —
             то есть свои сообщения по умолчанию тоже matчатся; если
             нужны строго свои — user_id='me', строго чужие — конкретный
             user_id/список без 'me').
             'me'                -> только от владельца юзербота.
             int                 -> конкретный user_id.
             list из перечисленного выше -> любое совпадение.
    timeout: None -> берётся из конфига модуля 'waiters' (default_timeout,
             см. modules/builtin/Waiters.py).

Возвращает Waiter — работает и как обычный await (одно сообщение), и как
async-итератор (Python-цикл: ждёт сообщение, выполняет тело, снова ждёт):

    msg = await engine.wait_message(timeout=30)

    async for msg in engine.wait_message(user_id='me', timeout=30):
        ...
        if msg.raw_text == 'стоп':
            break              # обычный break — просто выходим из цикла

Отмена ОЖИДАНИЯ ИЗВНЕ (например, из другого хэндлера, пока Waiter ждёт
где-то ещё) — через объект Waiter, если сохранить ссылку на него:

    waiter = engine.wait_message(timeout=None)
    async for msg in waiter:
        ...

    # где-то в другом месте кода:
    waiter.cancel()   # текущий/следующий await или итерация завершится
"""

import asyncio
from telethon import events


class WaiterCancelled(Exception):
    """wait_message(): ожидание было отменено через Waiter.cancel()."""


class Waiter:
    """Возвращается engine.wait_message() — см. докстринг модуля."""

    def __init__(self, client, chats, from_users, timeout, core=None):
        self._client = client
        self._timeout = timeout
        self._core = core            # для ленивого резолвинга timeout=None через конфиг 'waiters'
        self._ev_builder = events.NewMessage(chats=chats, from_users=from_users)
        self._queue = asyncio.Queue()
        self._cancelled = asyncio.Event()
        self._registered = False
        self._timeout_resolved = timeout is not None

    def _register(self):
        if not self._registered:
            self._client.add_event_handler(self._on_event, self._ev_builder)
            self._registered = True

    def _unregister(self):
        if self._registered:
            self._client.remove_event_handler(self._on_event, self._ev_builder)
            self._registered = False

    async def _on_event(self, event):
        await self._queue.put(event)

    def cancel(self):
        """Останавливает ожидание — снаружи, из любого другого места кода."""
        self._cancelled.set()

    async def _next(self):
        if self._cancelled.is_set():
            raise WaiterCancelled
        if not self._timeout_resolved:
            # timeout=None -> берём дефолт из конфига 'waiters' (async,
            # поэтому резолвится здесь, а не в синхронном wait_message())
            self._timeout = await self._core._waiters_default_timeout() if self._core else 60
            self._timeout_resolved = True
        self._register()

        cancel_task = asyncio.ensure_future(self._cancelled.wait())
        queue_task = asyncio.ensure_future(self._queue.get())
        try:
            done, pending = await asyncio.wait(
                {cancel_task, queue_task}, timeout=self._timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (cancel_task, queue_task):
                if not t.done():
                    t.cancel()

        if queue_task in done:
            return queue_task.result()
        if cancel_task in done:
            raise WaiterCancelled
        raise TimeoutError("wait_message: сообщение не пришло за отведённое время")

    # --- одиночный await: msg = await engine.wait_message(...) ---------- #

    def __await__(self):
        async def _one():
            try:
                return await self._next()
            finally:
                self._unregister()
        return _one().__await__()

    # --- цикл: async for msg in engine.wait_message(...): ---------------- #

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self._next()
        except (TimeoutError, WaiterCancelled):
            # таймаут/отмена -> цикл просто заканчивается, как обычный for
            self._unregister()
            raise StopAsyncIteration
