"""
core/clicks.py — клики по инлайн-кнопкам чужих ботов от имени юзербота.

    await msg.click(0, 1)                     # ряд 0, кнопка 1 (как в Telethon)
    await msg.click(2)                        # третья кнопка по счёту (как в Telethon)
    await msg.click("Атаковать")              # по тексту: регистр, эмодзи-варианты и невидимые символы не важны;
                                              # сначала точное совпадение, потом — кнопка, где этот текст встречается
    await msg.click((1, 0))                   # координаты кортежем
    await msg.click(lambda b: b.text.endswith('→'))  # своя проверка кнопки (b — кнопка из reply_markup)
    await engine.click(msg, "Атаковать")      # то же самое функцией

    new = await msg.click("Атаковать", wait="edit")   # дождаться, пока бот изменит это сообщение → новое сообщение
    reply = await msg.click("Меню", wait="new")       # дождаться нового сообщения бота в этом чате
    ok = await msg.click("Удалить", wait="delete")    # дождаться удаления сообщения → True/False
    ans = await msg.click("Профиль", wait="answer")   # дождаться ответа на нажатие (всплывашка: ans.message)
    timeout=…                                         # секунд ждать (по умолчанию: answer 10, остальное 5)

Без wait клик только отправляется (не дольше ~0.25 с) и возвращает True/False — бот обычно отвечает позже.
По тайм-ауту — None (для delete — False). Кнопки-ссылки, «поделиться номером», переключение в инлайн и т.п.
нажимаются как в Telethon. Обычный вызов Telethon (msg.click(i, j), text=, filter=, опросы) работает как раньше.

Почему быстро:
  * клик и ожидание идут одновременно — правка может прийти раньше ответа на callback;
  * ожидание — событие Telegram ИЛИ опрос сообщения с шагом 0.3 → 1 с: в группах Telegram присылает правки
    не всегда, и ждать весь тайм-аут незачем;
  * хендлеры этих событий одни на клиент и стоят первыми в очереди Telethon — событие не ждёт вотчеров модулей;
  * кнопка ищется в уже полученном сообщении; перечитываем его, только если кнопки там нет (refresh=True — всегда).
"""

import asyncio

from telethon import events, utils
from telethon.tl.custom.message import Message
from telethon.tl.functions.messages import GetBotCallbackAnswerRequest

POLL_FIRST = 0.3
POLL_MAX = 1.0
WAITS = (None, 'edit', 'new', 'delete', 'answer')
_FAILED = object()
_GONE = object()
_INVISIBLE = dict.fromkeys(map(ord, '️︎‍​‌ 　 ⁠ '), None)


def _clean(s) -> str:
    return ' '.join(str(s or '').translate(_INVISIBLE).casefold().split())


def _rows(message):
    rm = getattr(message, 'reply_markup', None)
    return getattr(rm, 'rows', None) or []


def find_button(message, button=0, col=None):
    """(ряд, кнопка) или None. button: номер по счёту / (ряд, кнопка) / текст / функция(кнопка) -> bool."""
    rows = _rows(message)
    if not rows:
        return None
    try:
        if isinstance(button, bool):
            return None
        if isinstance(button, int):
            if col is not None:
                rows[button].buttons[col]
                return (button, col)
            n = button
            for r, row in enumerate(rows):
                if n < len(row.buttons):
                    return (r, n)
                n -= len(row.buttons)
            return None
        if isinstance(button, tuple) and len(button) == 2:
            rows[button[0]].buttons[button[1]]
            return tuple(button)
    except (IndexError, TypeError):
        return None
    if isinstance(button, str):
        want = _clean(button)
        if not want:
            return None
        loose = None
        for r, row in enumerate(rows):
            for c, b in enumerate(row.buttons):
                have = _clean(getattr(b, 'text', ''))
                if have == want:
                    return (r, c)
                if loose is None and want in have:
                    loose = (r, c)
        return loose
    if callable(button):
        for r, row in enumerate(rows):
            for c, b in enumerate(row.buttons):
                try:
                    if button(b):
                        return (r, c)
                except Exception:
                    pass
    return None


def button_data(btn):
    """callback-данные кнопки или None (не callback-кнопка). Telethon 1.45: KeyboardInlineButton.type.data;
    старые версии: KeyboardButtonCallback.data; MessageButton (msg.buttons) — .data."""
    data = getattr(btn, 'data', None)
    if isinstance(data, bytes):
        return data
    data = getattr(getattr(btn, 'type', None), 'data', None)
    return data if isinstance(data, bytes) else None


def _state(msg):
    """Текст + кнопки (с callback-данными) + время правки: изменилось что угодно — бот ответил."""
    btns = tuple((getattr(b, 'text', None), button_data(b)) for row in _rows(msg) for b in row.buttons)
    return ((getattr(msg, 'raw_text', None) or ''), btns, getattr(msg, 'edit_date', None))


def _peer(msg):
    try:
        return utils.get_peer_id(msg.peer_id)
    except Exception:
        return None


class _Hub:
    """Один набор хендлеров (новое / правка / удаление) на клиент; ожидания — в словарях."""

    def __init__(self, client):
        self.client = client
        self.edits = {}      # (peer, msg_id) -> {(pred, fut)}
        self.news = {}       # peer -> {(pred, fut)}
        self.dels = {}       # msg_id -> {(peer, fut)}
        for cb, ev in self._handlers():
            client.add_event_handler(cb, ev())
        try:                 # в Telethon хендлеры одного апдейта идут по очереди — свои (мгновенные) ставим первыми
            lst = client._event_builders
            mine = {cb for cb, _ in self._handlers()}
            lst[:] = [x for x in lst if x[1] in mine] + [x for x in lst if x[1] not in mine]
        except Exception:
            pass

    def _handlers(self):
        return ((self._on_new, events.NewMessage), (self._on_edit, events.MessageEdited),
                (self._on_del, events.MessageDeleted))

    @staticmethod
    def add(bucket, key, item):
        bucket.setdefault(key, set()).add(item)

    @staticmethod
    def discard(bucket, key, item):
        s = bucket.get(key)
        if s is not None:
            s.discard(item)
            if not s:
                bucket.pop(key, None)

    @staticmethod
    def _fire(waiters, msg):
        for pred, fut in list(waiters):
            try:
                if not fut.done() and pred(msg):
                    fut.set_result(msg)
            except Exception:
                pass

    async def _on_edit(self, ev):
        msg = getattr(ev, 'message', None)
        if msg is not None and self.edits:
            self._fire(self.edits.get((_peer(msg), msg.id), ()), msg)

    async def _on_new(self, ev):
        msg = getattr(ev, 'message', None)
        if msg is not None and self.news:
            self._fire(self.news.get(_peer(msg), ()), msg)

    async def _on_del(self, ev):
        if not self.dels:
            return
        chat = getattr(ev, 'chat_id', None)        # в ЛС и обычных группах Telegram не говорит, из какого чата
        for mid in (getattr(ev, 'deleted_ids', None) or []):
            for peer, fut in list(self.dels.get(mid, ())):
                if not fut.done() and (chat is None or peer is None or chat == peer):
                    fut.set_result(True)


_hubs = {}


def _hub(client):
    h = _hubs.get(id(client))
    if h is None or h.client is not client:
        h = _hubs[id(client)] = _Hub(client)
    return h


async def _race(fut, timeout, poll, click_task):
    """fut (хендлер) или poll() (опрос с растущим шагом); click_task упал → _FAILED; тайм-аут → None."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    step = POLL_FIRST
    next_poll = loop.time() + step
    while True:
        if fut.done():
            return fut.result()
        if click_task is not None and click_task.done():
            if click_task.result() is False:
                return _FAILED
            click_task = None
        now = loop.time()
        if now >= deadline:
            return None
        if poll is not None and now >= next_poll:
            res = await poll()
            if res is not None:
                return res
            step = min(step * 1.5, POLL_MAX)
            next_poll = loop.time() + step
            continue
        wake = deadline if poll is None else min(next_poll, deadline)
        waiting = {fut} if click_task is None else {fut, click_task}
        await asyncio.wait(waiting, timeout=wake - now, return_when=asyncio.FIRST_COMPLETED)


async def _send(client, message, data, timeout=0.25):
    """Нажатие callback-кнопки. Ответ бота дольше timeout не ждём: клик уже ушёл — это успех."""
    try:
        await asyncio.wait_for(client(GetBotCallbackAnswerRequest(
            peer=message.peer_id, msg_id=message.id, data=data)), timeout)
        return True
    except asyncio.TimeoutError:
        return True
    except Exception as e:
        print(f"[FRX] клик по кнопке не прошёл: {type(e).__name__}: {e}")
        return False


async def click(message, button=0, col=None, *, wait=None, timeout=None, refresh=False):
    """См. докстринг модуля. Возвращает: без wait — True/False; edit/new — Message или None;
    delete — True/False; answer — BotCallbackAnswer или None."""
    if wait not in WAITS:
        raise ValueError(f"wait: одно из {', '.join(repr(w) for w in WAITS)}")
    client = getattr(message, '_client', None)
    if client is None:
        raise RuntimeError("у сообщения нет клиента — передайте сообщение, полученное через Telethon")
    if refresh or find_button(message, button, col) is None:
        fresh = await client.get_messages(message.peer_id, ids=message.id)
        if fresh is None:
            return False if wait == 'delete' else None
        message = fresh
    coords = find_button(message, button, col)
    if coords is None:
        raise LookupError(f"кнопка {button!r} не найдена")
    r, c = coords
    data = button_data(message.reply_markup.rows[r].buttons[c])

    if data is None:
        # ссылка, «поделиться номером», переключение в инлайн, кнопка обычной клавиатуры — как в Telethon
        return await _orig_click(message, r, c)

    if wait == 'answer':
        try:
            return await asyncio.wait_for(client(GetBotCallbackAnswerRequest(
                peer=message.peer_id, msg_id=message.id, data=data)), timeout or 10)
        except asyncio.TimeoutError:
            return None
    if wait is None:
        return await _send(client, message, data)

    timeout = timeout or 5
    hub = _hub(client)
    peer, msg_id = _peer(message), message.id
    fut = asyncio.get_running_loop().create_future()
    poll = None
    if wait == 'edit':
        orig = _state(message)
        key, bucket, item = (peer, msg_id), hub.edits, (lambda m: _state(m) != orig, fut)

        async def poll():
            try:
                cur = await client.get_messages(message.peer_id, ids=msg_id)
            except Exception:
                return None
            if cur is None:
                return _GONE
            return cur if _state(cur) != orig else None
    elif wait == 'new':
        key, bucket, item = peer, hub.news, (lambda m: not m.out and m.id > msg_id, fut)
    else:  # delete
        key, bucket, item = msg_id, hub.dels, (peer, fut)

        async def poll():
            try:
                return True if await client.get_messages(message.peer_id, ids=msg_id) is None else None
            except Exception:
                return None

    hub.add(bucket, key, item)                       # до клика — ответ не пропустим
    click_task = asyncio.ensure_future(_send(client, message, data))
    try:
        res = await _race(fut, timeout, poll, click_task)
    finally:
        hub.discard(bucket, key, item)
        if not click_task.done():
            click_task.cancel()
    if res is _FAILED or res is _GONE or res is None:
        return False if wait == 'delete' else None
    return res


# ---------------------------------------------------------------------------------------------- message.click

_orig_click = Message.click


async def _message_click(self, i=None, j=None, *, wait=None, timeout=None, refresh=False, **kwargs):
    """
    Telethon Message.click + удобства FRX: текст кнопки первым аргументом, (ряд, кнопка) кортежем, wait=….
    Привычные вызовы Telethon (номера, text=, filter=, data=, опросы) идут в оригинал без изменений.
    """
    ours = wait is not None or refresh or isinstance(i, (str, tuple)) or (callable(i) and not isinstance(i, int))
    if not ours or kwargs or getattr(self, 'poll', None) is not None:
        if isinstance(i, str) and 'text' not in kwargs:      # опрос / вызов с text=: текст — как в Telethon
            kwargs['text'], i = i, None
        return await _orig_click(self, i, j, **kwargs)
    return await click(self, 0 if i is None else i, j, wait=wait, timeout=timeout, refresh=refresh)


def install():
    """Подменить Message.click (идемпотентно)."""
    if getattr(Message.click, '_frx', False):
        return
    _message_click._frx = True
    Message.click = _message_click
