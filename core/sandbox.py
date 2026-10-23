"""
core/sandbox.py: FRX WebIDE Sandbox

Изолированная среда для проверки модуля прямо из редактора. Модуль выполняется в СВОЁМ потоке со своим
event loop и со своим окружением; настоящие Telegram, db, cache, конфиги и часы ядра ему недоступны:

  import engine / bot  -> SandboxEngine / BotFacade песочницы (сообщения уходят в ленту Sandbox, а не в Telegram)
  import db / cache    -> собственные хранилища песочницы с учётом каждого обращения к ключам
  import time/datetime/asyncio -> виртуальные часы: время можно сдвигать, циклы и таймеры срабатывают по ним
  import visual        -> копия иконок (visual_edit не меняет настоящие)

Что НЕ изолируется (это обычный Python): файловая система, сеть и сторонние библиотеки модуля.

События, которые можно отправить модулю (см. Session): входящее сообщение (команды и вотчеры юзербота и бота),
служебное событие Telegram (вошёл/вышел/…), нажатие callback-кнопки, ошибка (on_error), загрузка другого модуля
(on_load), смена аккаунта, перезапуск (on_start). Форма (create_inlineform) показывается в ленте — по её кнопкам можно
кликать от имени любого участника.

Защита от зависания: если модуль «замкнулся» в цикле без await, heartbeat потока пропадает — поток останавливается
(PyThreadState_SetAsyncExc), а песочница сообщает об этом; сайт и юзербот продолжают работать.
"""

import ast
import asyncio
import builtins
import contextvars
import ctypes
import datetime as _real_datetime
import heapq
import html as _html
import itertools
import json
import os
import re
import shutil
import tempfile
import threading
import time as _real_time
import traceback
import types
import uuid
from pathlib import Path

from .context import current_event, current_module
from .html_utils import safe_html
from .module_meta import parse_module_meta
from .paths import DATA_DIR, FILES_DIR, MODULES_DIR
from .time_utils import parse_interval

SB_FILE_PREFIX = '<sandbox:'
PEOPLE_FILE = DATA_DIR / 'sandbox' / 'people.json'          # участники и чаты, созданные пользователем: общие для всех модулей
_PEOPLE_LOCK = threading.Lock()
STALL_SECONDS = 5.0                 # heartbeat потока пропал дольше — считаем, что модуль завис
MAX_FEED = 4000                     # записей в ленте (сообщения / журнал); старые отбрасываются
MAX_TIMER_FIRES = 5000              # за один «сдвиг времени» — защита от цикла с интервалом в секунду на неделю
MAX_ADVANCE = 60 * 60 * 24 * 90     # не больше 90 суток за раз
LOOPBACK_DEPTH = 8                  # цепочка «модуль отвечает на собственные сообщения»

sb_fn = contextvars.ContextVar('sb_fn', default=None)             # имя выполняющейся функции модуля (для журнала)
sb_depth = contextvars.ContextVar('sb_depth', default=0)          # глубина цепочки собственных сообщений


class SbKilled(BaseException):
    """Поток песочницы остановлен снаружи (кнопка «стоп» или watchdog)."""


class SbUnsupported(AttributeError):
    """Возможность, которой нет в Sandbox (AttributeError — чтобы getattr(obj, 'x', default) работал как обычно)."""


class SbError(Exception):
    """Ошибка уровня Telegram в песочнице (аналог telethon.errors.*)."""


# --------------------------------------------------------------------------- #
# вспомогательное
# --------------------------------------------------------------------------- #

def preview(value, limit=240):
    """Короткое текстовое представление значения для журнала и таблицы ключей."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=repr)
    except Exception:
        text = repr(value)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def type_name(value):
    return type(value).__name__


class Feed:
    """Лента с версиями: add() / update() / snapshot(since). Потокобезопасна (пишет поток песочницы, читает сайт)."""

    def __init__(self, cap=MAX_FEED):
        self._lock = threading.RLock()
        self._cap = cap
        self.items = []
        self.rev = 0
        self._next_id = 0

    def add(self, **fields):
        with self._lock:
            self.rev += 1
            self._next_id += 1
            item = {'id': self._next_id, 'rev': self.rev, 't': _real_time.time(), **fields}
            self.items.append(item)
            if len(self.items) > self._cap:
                del self.items[:len(self.items) - self._cap]
            return item

    def update(self, item, **fields):
        with self._lock:
            self.rev += 1
            item.update(fields)
            item['rev'] = self.rev
            return item

    def snapshot(self, since=0):
        with self._lock:
            return [dict(i) for i in self.items if i['rev'] > since], self.rev

    def clear(self):
        with self._lock:
            self.rev += 1
            self.items = []


# --------------------------------------------------------------------------- #
# виртуальные часы
# --------------------------------------------------------------------------- #

class VirtualClock:
    """
    Время песочницы. now — «текущие» секунды Unix. sleep()/timer() срабатывают только когда время продвигается
    (advance / set_time / play). Всё, что модуль берёт из time/datetime/asyncio.sleep, видит эти часы.
    """

    def __init__(self, journal_cb=None):
        self.now = _real_time.time()
        self.origin = self.now
        self._timers = []                       # (due, seq, future, label)
        self._seq = itertools.count()
        self._note = journal_cb
        self.playing = False
        self.speed = 60.0
        self._play_task = None
        self.fired = 0

    # ---- таймеры ---- #
    def timer(self, seconds, label=''):
        loop = asyncio.get_running_loop()
        fut = loop.create_future()
        due = self.now + max(0.0, float(seconds))
        heapq.heappush(self._timers, (due, next(self._seq), fut, label))
        return fut

    async def sleep(self, delay, result=None):
        if delay is None or delay <= 0:
            await _REAL_SLEEP(0)
            return result
        fut = self.timer(delay, label=f'sleep({delay:g})')
        try:
            await fut
        finally:
            if not fut.done():
                fut.cancel()
        return result

    def pending(self, limit=8):
        live = [t for t in self._timers if not t[2].done()]
        return [{'due': t[0], 'label': t[3]} for t in heapq.nsmallest(limit, live)]

    # ---- перемещение по времени ---- #
    async def _settle(self, rounds=6):
        for _ in range(rounds):
            await _REAL_SLEEP(0)

    async def advance(self, seconds):
        seconds = max(0.0, min(float(seconds), MAX_ADVANCE))
        target = self.now + seconds
        fired = 0
        truncated = False
        while self._timers:
            due, _, fut, label = self._timers[0]
            if fut.done():
                heapq.heappop(self._timers)
                continue
            if due > target:
                break
            if fired >= MAX_TIMER_FIRES:
                truncated = True
                break
            heapq.heappop(self._timers)
            self.now = max(self.now, due)
            if not fut.done():
                fut.set_result(None)
            fired += 1
            await self._settle()
        if not truncated:
            self.now = max(self.now, target)
        self.fired += fired
        await self._settle()
        return {'fired': fired, 'truncated': truncated, 'now': self.now}

    def jump(self, new_now):
        """Переставить часы на момент new_now (вперёд — как advance; назад — таймеры остаются при своих due)."""
        self.now = float(new_now)

    async def set_time(self, ts):
        ts = float(ts)
        if ts >= self.now:
            return await self.advance(ts - self.now)
        self.now = ts
        await self._settle()
        return {'fired': 0, 'truncated': False, 'now': self.now}

    # ---- автоход ---- #
    def play(self, speed):
        self.speed = max(0.1, min(float(speed), 86400.0))
        if self.playing:
            return
        self.playing = True
        loop = asyncio.get_running_loop()
        self._play_task = loop.create_task(self._play_loop())

    def pause(self):
        self.playing = False
        if self._play_task is not None:
            self._play_task.cancel()
            self._play_task = None

    async def _play_loop(self):
        step = 0.25
        try:
            while self.playing:
                await _REAL_SLEEP(step)
                await self.advance(step * self.speed)
        except asyncio.CancelledError:
            pass


_REAL_SLEEP = asyncio.sleep           # настоящий sleep — для кода самой песочницы


class _Shim(types.ModuleType):
    """Модуль-обёртка: переопределённые имена + всё остальное из настоящего модуля."""

    def __init__(self, name, real, overrides):
        super().__init__(name)
        self.__dict__.update(overrides)
        self.__dict__['_real'] = real

    def __getattr__(self, item):
        return getattr(self.__dict__['_real'], item)


def build_shims(clock: VirtualClock, note):
    """time / datetime / asyncio с виртуальными часами."""
    rt = _real_time
    rd = _real_datetime

    def v_sleep(secs):
        note('warn', f'time.sleep({secs}) в Sandbox не блокирует: виртуальное время сдвинуто сразу '
                     f'(в async-коде используйте await asyncio.sleep)')
        clock.now += max(0.0, float(secs))

    time_shim = _Shim('time', rt, {
        'time': lambda: clock.now,
        'time_ns': lambda: int(clock.now * 1e9),
        'monotonic': lambda: clock.now - clock.origin,
        'monotonic_ns': lambda: int((clock.now - clock.origin) * 1e9),
        'perf_counter': lambda: clock.now - clock.origin,
        'perf_counter_ns': lambda: int((clock.now - clock.origin) * 1e9),
        'sleep': v_sleep,
        'localtime': lambda secs=None: rt.localtime(clock.now if secs is None else secs),
        'gmtime': lambda secs=None: rt.gmtime(clock.now if secs is None else secs),
        'ctime': lambda secs=None: rt.ctime(clock.now if secs is None else secs),
        'strftime': lambda fmt, t=None: rt.strftime(fmt, rt.localtime(clock.now) if t is None else t),
    })

    class VDate(rd.date):
        @classmethod
        def today(cls):
            return cls.fromtimestamp(clock.now)

    class VDatetime(VDate, rd.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.fromtimestamp(clock.now, tz)

        @classmethod
        def utcnow(cls):
            return cls.fromtimestamp(clock.now, rd.timezone.utc).replace(tzinfo=None)

        @classmethod
        def today(cls):
            return cls.fromtimestamp(clock.now)

    VDate.__name__ = 'date'
    VDatetime.__name__ = 'datetime'
    datetime_shim = _Shim('datetime', rd, {'datetime': VDatetime, 'date': VDate})

    asyncio_shim = _Shim('asyncio', asyncio, {'sleep': clock.sleep})
    return {'time': time_shim, 'datetime': datetime_shim, 'asyncio': asyncio_shim}


# --------------------------------------------------------------------------- #
# участники, чаты, сообщения
# --------------------------------------------------------------------------- #

def _entity_id(chat_id: int) -> int:
    """chat_id → положительный id сущности, как отдаёт Telethon (-100123 → 123, -5 → 5)."""
    s = str(chat_id)
    if s.startswith('-100'):
        return int(s[4:])
    return abs(chat_id)


class SbUser:
    """Участник (аналог telethon.types.User)."""

    def __init__(self, id, first_name='', last_name=None, username=None, bot=False, premium=False, phone=None, is_self=False):
        self.id = id
        self.first_name = first_name
        self.last_name = last_name
        self.username = username
        self.bot = bot
        self.premium = premium
        self.phone = phone
        self.is_self = is_self
        self.custom = False               # создан пользователем (хранится на диске, можно удалить)
        self.access_hash = 0
        self.deleted = False
        self.verified = False
        self.scam = False
        self.fake = False
        self.restricted = False
        self.contact = False
        self.mutual_contact = False
        self.lang_code = None
        self.status = None
        self.photo = None

    @property
    def title(self):
        return self.name

    @property
    def name(self):
        return ' '.join(p for p in (self.first_name, self.last_name) if p) or (self.username or str(self.id))

    def describe(self):
        return {'id': self.id, 'first_name': self.first_name, 'last_name': self.last_name or '', 'username': self.username or '',
                'bot': self.bot, 'name': self.name, 'custom': bool(getattr(self, 'custom', False)), 'premium': bool(self.premium)}

    def __repr__(self):
        return f"User(id={self.id}, first_name={self.first_name!r}, username={self.username!r})"

    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        raise SbUnsupported(f"user.{item} не эмулируется в Sandbox")


class SbChat:
    """Чат песочницы. side: 'user' — виден юзерботу, 'bot' — виден стационарному боту, 'both' — обоим (группа)."""

    def __init__(self, key, id, type, title, side='user', peer=None, username=None, saved=False):
        self.key = key
        self.id = id                      # «марккированный» chat_id, как в событиях (-100…, -…, id пользователя)
        self.type = type                  # private | group | supergroup | channel
        self.title = title
        self.side = side
        self.peer = peer                  # id собеседника для private
        self.username = username
        self.saved = saved                # «Избранное»
        self.last_id = 0
        self.pinned = None                # id закреплённого сообщения
        self.custom = False

    def next_id(self):
        self.last_id += 1
        return self.last_id

    @property
    def megagroup(self):
        return self.type == 'supergroup'

    @property
    def broadcast(self):
        return self.type == 'channel'

    def describe(self):
        return {'key': self.key, 'id': self.id, 'type': self.type, 'title': self.title, 'side': self.side,
                'peer': self.peer, 'username': self.username or '', 'saved': self.saved, 'custom': bool(getattr(self, 'custom', False))}


class SbEntityChat:
    """То, что отдаёт get_chat() для групп/каналов (telethon.types.Chat/Channel)."""

    def __init__(self, chat: SbChat):
        self._chat = chat
        self.id = _entity_id(chat.id)
        self.title = chat.title
        self.username = chat.username
        self.megagroup = chat.megagroup
        self.broadcast = chat.broadcast
        self.access_hash = 0

    def __repr__(self):
        return f"Channel(id={self.id}, title={self.title!r})"

    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        raise SbUnsupported(f"chat.{item} не эмулируется в Sandbox")


_MEDIA_KINDS = {'photo', 'document', 'voice', 'video', 'audio', 'sticker', 'video_note', 'gif'}
_EXT_KIND = {'jpg': 'photo', 'jpeg': 'photo', 'png': 'photo', 'webp': 'photo', 'ogg': 'voice', 'oga': 'voice', 'mp3': 'audio',
             'm4a': 'audio', 'wav': 'audio', 'mp4': 'video', 'mov': 'video', 'mkv': 'video', 'gif': 'gif'}
_KIND_MIME = {'photo': 'image/jpeg', 'voice': 'audio/ogg', 'audio': 'audio/mpeg', 'video': 'video/mp4', 'video_note': 'video/mp4',
              'gif': 'video/mp4', 'sticker': 'image/webp', 'document': 'application/octet-stream'}


def make_media(kind, name, content=None, size=None, mime=None):
    kind = kind if kind in _MEDIA_KINDS else 'document'
    name = (name or kind).strip() or kind
    data = content.encode('utf-8') if isinstance(content, str) else (content if isinstance(content, (bytes, bytearray)) else None)
    mime = mime or _guess_mime(name, kind)
    return {'kind': kind, 'name': name, 'size': int(size if size is not None else (len(data) if data is not None else 2048)),
            'mime': mime, 'content': data, 'id': uuid.uuid4().hex[:12]}


def _guess_mime(name, kind):
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    table = {'py': 'text/x-python', 'txt': 'text/plain', 'json': 'application/json', 'csv': 'text/csv', 'pdf': 'application/pdf',
             'zip': 'application/zip', 'html': 'text/html', 'md': 'text/markdown'}
    return table.get(ext) or _KIND_MIME.get(kind, 'application/octet-stream')


def media_from_file(path_or_obj):
    """Вложение, отправленное модулем (file=…): путь, объект из реестра файлов или имя."""
    if isinstance(path_or_obj, dict):
        name = path_or_obj.get('name') or Path(str(path_or_obj.get('path', 'file'))).name
        size = path_or_obj.get('size')
    else:
        name = Path(str(path_or_obj)).name or 'file'
        size = None
    ext = name.rsplit('.', 1)[-1].lower() if '.' in name else ''
    return make_media(_EXT_KIND.get(ext, 'document'), name, size=size)


class _Attach:
    """Лёгкий объект вложения: truthy, с name/size/mime_type (для .file, .document, .photo …)."""

    def __init__(self, media):
        self.name = media['name']
        self.size = media['size']
        self.mime_type = media['mime']
        self.id = media['id']
        self.ext = ('.' + media['name'].rsplit('.', 1)[-1]) if '.' in media['name'] else ''
        self.kind = media['kind']

    def __repr__(self):
        return f"<{self.kind} {self.name!r} {self.size}b>"


class SbMessage:
    """Сообщение (аналог telethon.types.Message) — и запись в ленте Sandbox."""

    def __init__(self, session, chat: SbChat, sender: SbUser, text='', *, out=False, reply_to=None, media=None,
                 via_bot=False, buttons=None, form_id=None, html=False):
        self._s = session
        self._chat = chat
        self.sender = sender
        self.id = chat.next_id()
        self._text = text or ''
        self._html = html
        self.out = out
        self.date = session.dt_now()
        self.edit_date = None
        self._reply_id = reply_to
        self._media = media
        self.via_bot_id = session.bot_user.id if via_bot else None
        self._via_bot = via_bot
        self.pinned = False
        self.deleted = False
        self._buttons = buttons
        self._form_id = form_id
        self._bot_view = None
        self._entry = None
        self.mentioned = False
        self.post = chat.type == 'channel'
        session.register_message(self)
        self._publish(new=True)

    # ---- поля, как у Telethon ---- #
    @property
    def text(self):
        return self._text

    @property
    def raw_text(self):
        return self._text

    @property
    def message(self):
        return self._text

    @property
    def chat_id(self):
        return self._chat.id

    @property
    def sender_id(self):
        return self.sender.id if self.sender else None

    @property
    def is_private(self):
        return self._chat.type == 'private'

    @property
    def is_group(self):
        return self._chat.type in ('group', 'supergroup')

    @property
    def is_channel(self):
        return self._chat.type in ('channel', 'supergroup')

    @property
    def reply_to_msg_id(self):
        return self._reply_id

    @property
    def reply_to(self):
        return types.SimpleNamespace(reply_to_msg_id=self._reply_id) if self._reply_id else None

    @property
    def is_reply(self):
        return self._reply_id is not None

    @property
    def peer_id(self):
        return types.SimpleNamespace(user_id=self._chat.peer) if self._chat.type == 'private' else \
            types.SimpleNamespace(channel_id=_entity_id(self._chat.id))

    @property
    def media(self):
        return _Attach(self._media) if self._media else None

    @property
    def file(self):
        return _Attach(self._media) if self._media else None

    def _kind_attach(self, *kinds):
        return _Attach(self._media) if self._media and self._media['kind'] in kinds else None

    photo = property(lambda self: self._kind_attach('photo'))
    voice = property(lambda self: self._kind_attach('voice'))
    video = property(lambda self: self._kind_attach('video', 'video_note', 'gif'))
    audio = property(lambda self: self._kind_attach('audio', 'voice'))
    sticker = property(lambda self: self._kind_attach('sticker'))
    gif = property(lambda self: self._kind_attach('gif'))
    video_note = property(lambda self: self._kind_attach('video_note'))
    document = property(lambda self: _Attach(self._media) if self._media and self._media['kind'] != 'photo' else None)
    fwd_from = None
    entities = None
    web_preview = None
    client = property(lambda self: self._s.client)
    buttons = property(lambda self: None)

    # ---- публикация в ленту ---- #
    def _nodes(self):
        from .emulate import _to_nodes
        return _to_nodes(self._text, 'html' if self._html else 'md') if self._text else []

    def _fields(self):
        reply = None
        if self._reply_id is not None:
            ref = self._s.find_message(self._chat, self._reply_id)
            if ref is not None:
                reply = {'mid': ref.id, 'name': ref.sender.name if ref.sender else '', 'text': (ref._text or (ref._media or {}).get('name') or '')[:90]}
            else:
                reply = {'mid': self._reply_id, 'name': '', 'text': ''}
        media = None
        if self._media:
            m = self._media
            media = {'kind': m['kind'], 'name': m['name'], 'size': m['size'], 'mime': m['mime']}
        rows = []
        if self._buttons:
            from .emulate import serialize_buttons
            try:
                rows = serialize_buttons(self._buttons)
            except Exception:
                rows = []
        return {'chat': self._chat.key, 'side': self._chat.side, 'mid': self.id, 'out': self.out,
                'sender': self.sender.describe() if self.sender else None, 'nodes': self._nodes(), 'text': self._text, 'reply': reply,
                'media': media, 'edited': self.edit_date is not None, 'deleted': self.deleted, 'pinned': self.pinned,
                'rows': rows, 'form': self._form_id, 'via_bot': self._via_bot, 'vt': self._s.clock.now}

    def _publish(self, new=False):
        fields = self._fields()
        if new or self._entry is None:
            self._entry = self._s.feed.add(kind='msg', **fields)
        else:
            self._s.feed.update(self._entry, **fields)

    # ---- методы ---- #
    async def edit(self, text=None, *, file=None, parse_mode=None, buttons=None, link_preview=None, **kw):
        if not self.out:
            raise SbError("MessageAuthorRequiredError: чужое сообщение нельзя редактировать "
                          "(в Telegram так же — engine.answer() в этом случае отправит ответ новым сообщением)")
        if self.deleted:
            raise SbError("MessageIdInvalidError: сообщение удалено")
        if text is not None:
            self._text = text
            self._html = parse_mode == 'html'
        if file is not None:
            self._media = media_from_file(self._s.files.resolve(file))
        if buttons is not None:
            self._buttons = buttons
        self.edit_date = self._s.dt_now()
        self._publish()
        self._s.note('send', f"edit_message #{self.id}: {preview(self._text, 120)}", chat=self._chat.key)
        return self

    async def reply(self, text='', *, file=None, parse_mode=None, buttons=None, link_preview=None, silent=None, **kw):
        return await self._s.send_as_owner(self._chat, text, file=file, parse_mode=parse_mode, reply_to=self.id, buttons=buttons)

    async def respond(self, text='', *, file=None, parse_mode=None, buttons=None, **kw):
        return await self._s.send_as_owner(self._chat, text, file=file, parse_mode=parse_mode, buttons=buttons)

    async def delete(self, *, revoke=True):
        if self.deleted:
            return
        self.deleted = True
        self._publish()
        self._s.note('send', f"delete_messages #{self.id}", chat=self._chat.key)

    async def get_reply_message(self):
        return self._s.find_message(self._chat, self._reply_id) if self._reply_id is not None else None

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return self._s.entity_of(self._chat)

    async def get_input_chat(self):
        return self._s.entity_of(self._chat)

    async def get_input_sender(self):
        return self.sender

    async def download_media(self, file=None, **kw):
        if not self._media:
            return None
        path = self._s.files.write_download(self._media)
        self._s.note('api', f"download_media → {path} (файл во временной папке Sandbox)", chat=self._chat.key)
        return path

    async def pin(self, *, notify=False, pm_oneside=False):
        self._s.pin(self._chat, self)

    async def unpin(self):
        self._s.unpin(self._chat, self)

    async def mark_read(self, *a, **kw):
        return True

    async def forward_to(self, entity):
        chat = self._s.resolve_chat(entity, 'user')
        copy_ = SbMessage(self._s, chat, self._s.owner, self._text, out=True, media=self._media, html=self._html)
        self._s.note('send', f"forward_to({chat.title}) → #{copy_.id}", chat=chat.key)
        return copy_

    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        raise SbUnsupported(f"message.{item} не эмулируется в Sandbox")

    def __repr__(self):
        return f"Message(id={self.id}, chat={self._chat.id}, text={preview(self._text, 40)})"


class SbEvent:
    """Событие нового сообщения (аналог NewMessage.Event): всё, чего нет здесь, берётся из сообщения."""

    def __init__(self, session, message: SbMessage):
        self._s = session
        self.message = message
        self.pattern_match = None
        self.original_update = None
        self.origin = 'userbot'

    def __getattr__(self, item):
        if item.startswith('__') or item in ('message',):
            raise AttributeError(item)
        return getattr(self.message, item)

    def __repr__(self):
        return f"<NewMessage.Event {self.message!r}>"


class SbChatAction:
    """Служебное событие Telegram (аналог ChatAction.Event) — для @decorators.telegram_event."""

    KINDS = ('user_joined', 'user_added', 'user_left', 'user_kicked', 'created', 'new_title', 'new_photo', 'new_pin', 'unpin')

    def __init__(self, session, chat: SbChat, kind, user: SbUser = None, actor: SbUser = None, title=None):
        self._s = session
        self._chat = chat
        self.kind = kind
        self.chat_id = chat.id
        self.user_joined = kind == 'user_joined'
        self.user_added = kind == 'user_added'
        self.user_left = kind == 'user_left'
        self.user_kicked = kind == 'user_kicked'
        self.created = kind == 'created'
        self.new_title = title if kind in ('new_title', 'created') else None
        self.new_photo = kind == 'new_photo'
        self.new_pin = kind == 'new_pin'
        self.unpin = kind == 'unpin'
        self.user = user
        self.user_id = user.id if user else None
        self.user_ids = [user.id] if user else []
        self.added_by = actor.id if actor and kind in ('user_added', 'created') else None
        self.kicked_by = actor.id if actor and kind == 'user_kicked' else None
        self.action_message = types.SimpleNamespace(id=0, action=kind, date=session.dt_now())
        self.origin = 'userbot'

    async def get_user(self):
        return self.user

    async def get_users(self):
        return [self.user] if self.user else []

    async def get_chat(self):
        return self._s.entity_of(self._chat)

    async def get_input_chat(self):
        return self._s.entity_of(self._chat)

    async def get_added_by(self):
        return self._s.users.get(self.added_by)

    async def get_kicked_by(self):
        return self._s.users.get(self.kicked_by)

    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        raise SbUnsupported(f"event.{item} не эмулируется в Sandbox")

    def __repr__(self):
        return f"<ChatAction.Event {self.kind} chat={self.chat_id} user={self.user_id}>"


class SbCallbackEvent:
    """Нажатие callback-кнопки (аналог CallbackQuery.Event) — для @decorators.callback."""

    def __init__(self, session, chat: SbChat, sender: SbUser, data: bytes, message: SbMessage = None):
        self._s = session
        self._chat = chat
        self.data = data
        self.sender_id = sender.id
        self.sender = sender
        self.chat_id = chat.id
        self.message_id = message.id if message else None
        self.message = message
        self.id = uuid.uuid4().int & 0xFFFFFFFF
        self.origin = 'userbot'
        self._answered = False

    async def answer(self, message=None, cache_time=0, *, alert=False, url=None):
        self._answered = True
        if message:
            self._s.toast(message, 'error' if alert else 'info')

    async def get_message(self):
        return self.message

    async def edit(self, text=None, **kw):
        if self.message is None:
            raise SbError("MessageIdInvalidError: у события нет сообщения")
        self.message._text = text if text is not None else self.message._text
        self.message.edit_date = self._s.dt_now()
        self.message._publish()
        return self.message

    async def respond(self, text='', **kw):
        return await self._s.send_as_owner(self._chat, text)

    async def get_sender(self):
        return self.sender

    async def get_chat(self):
        return self._s.entity_of(self._chat)

    def __getattr__(self, item):
        if item.startswith('__'):
            raise AttributeError(item)
        raise SbUnsupported(f"event.{item} не эмулируется в Sandbox")


# ---- сторона aiogram (стационарный бот) ---- #

class SbBotClick:
    """Кнопка под обычным сообщением бота в Sandbox — как core.bot_events.BotClick (для @bot.callback)."""

    def __init__(self, session, chat, sender, data, message):
        self._s = session
        self.data = data
        self.user_id = sender.id
        self.username = sender.username or ''
        self.first_name = sender.first_name or ''
        self.from_user = _ag_user(sender)
        self.chat_id = chat.id
        self.message_id = message.id if message is not None else None
        self.message = SbBotMessage(message) if message is not None else None
        self._chat = chat
        self._msg = message
        self._answered = False

    async def answer(self, text=None, show_alert=False, url=None):
        if self._answered:
            return
        self._answered = True
        if text:
            self._s.toast(text, 'error' if show_alert else 'info')

    async def edit(self, text, buttons=None, styles=None):
        if self._msg is None:
            raise SbError("message to edit not found")
        self._msg._text = text
        self._msg._html = True
        self._msg._buttons = buttons
        self._msg.edit_date = self._s.dt_now()
        self._msg._publish()
        self._s.note('send', f"bot.edit #{self._msg.id}: {preview(text, 120)}", chat=self._chat.key)

    async def delete(self):
        if self._msg is not None:
            await self._msg.delete()


def _ag_user(u: SbUser):
    return types.SimpleNamespace(id=u.id, is_bot=u.bot, first_name=u.first_name, last_name=u.last_name, username=u.username,
                                 full_name=u.name, language_code=None)


def _ag_chat(c: SbChat):
    kind = 'private' if c.type == 'private' else c.type
    return types.SimpleNamespace(id=c.id, type=kind, title=None if kind == 'private' else c.title, username=c.username)


class SbBotMessage:
    """Сообщение в виде aiogram.types.Message (для bot.command/bot.watcher и возвращаемых значений engine.bot.*)."""

    def __init__(self, m: SbMessage):
        self._m = m
        m._bot_view = self

    @property
    def message_id(self):
        return self._m.id

    @property
    def chat(self):
        return _ag_chat(self._m._chat)

    @property
    def from_user(self):
        return _ag_user(self._m.sender) if self._m.sender else None

    @property
    def text(self):
        return None if self._m._media else self._m._text

    @property
    def caption(self):
        return self._m._text if self._m._media else None

    @property
    def date(self):
        return self._m.date

    @property
    def reply_to_message(self):
        ref = self._m._s.find_message(self._m._chat, self._m._reply_id) if self._m._reply_id is not None else None
        return SbBotMessage(ref) if ref is not None else None

    def _media_of(self, *kinds):
        media = self._m._media
        return _Attach(media) if media and media['kind'] in kinds else None

    photo = property(lambda self: [self._media_of('photo')] if self._media_of('photo') else None)
    document = property(lambda self: self._media_of('document'))
    voice = property(lambda self: self._media_of('voice'))
    video = property(lambda self: self._media_of('video'))
    audio = property(lambda self: self._media_of('audio'))
    sticker = property(lambda self: self._media_of('sticker'))
    video_note = property(lambda self: self._media_of('video_note'))
    animation = property(lambda self: self._media_of('gif'))
    entities = None
    reply_markup = None

    def __getattr__(self, item):
        if item.startswith('__') or item == '_m':
            raise AttributeError(item)
        raise SbUnsupported(f"aiogram Message.{item} не эмулируется в Sandbox")


def markup_to_rows(markup):
    """aiogram InlineKeyboardMarkup / список рядов → ряды кнопок формата движка."""
    if markup is None:
        return None
    if hasattr(markup, 'inline_keyboard'):
        return [[(b.text, getattr(b, 'callback_data', None) or '') for b in row] for row in markup.inline_keyboard]
    return markup


class SbAiogramBot:
    """Заменяет aiogram.Bot (engine.bot.raw / engine._aiogram_bot): основные методы пишут в ленту Sandbox."""

    def __init__(self, session):
        self._s = session

    async def get_me(self):
        return _ag_user(self._s.bot_user)

    @property
    def id(self):
        return self._s.bot_user.id

    async def send_message(self, chat_id, text, reply_markup=None, reply_to_message_id=None, parse_mode=None, **kw):
        return await self._s.send_as_bot(chat_id, text, buttons=markup_to_rows(reply_markup), reply_to=reply_to_message_id,
                                          html=parse_mode in (None, 'HTML', 'html'))

    async def _send_media(self, kind, chat_id, media, caption, reply_markup, reply_to_message_id):
        name = getattr(media, 'filename', None) or (Path(str(media)).name if isinstance(media, str) else kind)
        return await self._s.send_as_bot(chat_id, caption or '', buttons=markup_to_rows(reply_markup), reply_to=reply_to_message_id,
                                          media=make_media(kind, name), html=True)

    async def send_photo(self, chat_id, photo, caption=None, reply_markup=None, reply_to_message_id=None, **kw):
        return await self._send_media('photo', chat_id, photo, caption, reply_markup, reply_to_message_id)

    async def send_document(self, chat_id, document, caption=None, reply_markup=None, reply_to_message_id=None, **kw):
        return await self._send_media('document', chat_id, document, caption, reply_markup, reply_to_message_id)

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None, parse_mode=None, **kw):
        chat = self._s.resolve_chat(chat_id, 'bot')
        m = self._s.find_message(chat, message_id)
        if m is None or m.sender is None or m.sender.id != self._s.bot_user.id:
            raise SbError("TelegramBadRequest: message can't be edited")
        m._text = text
        m._html = True
        if reply_markup is not None:
            m._buttons = markup_to_rows(reply_markup)
        m.edit_date = self._s.dt_now()
        m._publish()
        self._s.note('send', f"bot.edit_message_text #{message_id}: {preview(text, 120)}", chat=chat.key)
        return SbBotMessage(m)

    async def delete_message(self, chat_id, message_id):
        chat = self._s.resolve_chat(chat_id, 'bot')
        m = self._s.find_message(chat, message_id)
        if m is not None:
            m.deleted = True
            m._publish()
            self._s.note('send', f"bot.delete_message #{message_id}", chat=chat.key)
        return True

    async def answer_callback_query(self, callback_query_id=None, text=None, show_alert=False, **kw):
        if text:
            self._s.toast(text, 'error' if show_alert else 'info')
        return True

    async def get_chat_member(self, chat_id, user_id):
        member = user_id in self._s.users
        return types.SimpleNamespace(status='member' if member else 'left', user=_ag_user(self._s.users[user_id]) if member else None)

    async def pin_chat_message(self, chat_id, message_id, **kw):
        chat = self._s.resolve_chat(chat_id, 'bot')
        m = self._s.find_message(chat, message_id)
        if m is not None:
            self._s.pin(chat, m)
        return True

    async def unpin_chat_message(self, chat_id, message_id=None, **kw):
        chat = self._s.resolve_chat(chat_id, 'bot')
        m = self._s.find_message(chat, message_id) if message_id else None
        self._s.unpin(chat, m)
        return True

    def __getattr__(self, item):
        if item.startswith('__') or item == '_s':
            raise AttributeError(item)
        raise SbUnsupported(f"bot.raw.{item}() не эмулируется в Sandbox")


class SbBotFacade:
    """engine.bot — то же API, что BotFacade (send_message / reply / edit / raw)."""

    def __init__(self, session):
        self._s = session
        self._raw = SbAiogramBot(session)

    @property
    def raw(self):
        return self._raw

    async def send_message(self, chat_id, text, file=None, buttons=None, reply_to=None):
        media = media_from_file(self._s.files.resolve(file)) if file is not None else None
        return await self._s.send_as_bot(chat_id, safe_html(text) if text else text, buttons=buttons, reply_to=reply_to, media=media, html=True)

    async def reply(self, message, text, file=None, buttons=None):
        return await self.send_message(message.chat.id, text, file=file, buttons=buttons, reply_to=message.message_id)

    async def edit(self, message, text, file=None, buttons=None):
        if file is not None:
            await self._raw.delete_message(message.chat.id, message.message_id)
            return await self.send_message(message.chat.id, text, file=file, buttons=buttons)
        return await self._raw.edit_message_text(safe_html(text), chat_id=message.chat.id, message_id=message.message_id,
                                                  reply_markup=buttons)

    def __getattr__(self, item):
        if item.startswith('__') or item == '_s':
            raise AttributeError(item)
        raise SbUnsupported(f"engine.bot.{item} не эмулируется в Sandbox")


class SbClient:
    """Заменяет telethon.TelegramClient: основные методы работают с лентой Sandbox, остальное — понятная ошибка."""

    def __init__(self, session):
        self._s = session

    def is_connected(self):
        return True

    async def is_user_authorized(self):
        return True

    async def get_me(self, input_peer=False):
        return self._s.owner

    async def send_message(self, entity, message='', *, file=None, parse_mode=None, reply_to=None, buttons=None, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        return await self._s.send_as_owner(chat, message if isinstance(message, str) else getattr(message, 'text', ''),
                                           file=file, parse_mode=parse_mode, reply_to=getattr(reply_to, 'id', reply_to), buttons=buttons)

    async def send_file(self, entity, file, *, caption=None, parse_mode=None, reply_to=None, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        return await self._s.send_as_owner(chat, caption or '', file=file, parse_mode=parse_mode, reply_to=getattr(reply_to, 'id', reply_to))

    async def edit_message(self, entity, message=None, text=None, *, parse_mode=None, buttons=None, file=None, **kw):
        if isinstance(entity, SbMessage):
            msg, text = entity, (message if text is None else text)
        else:
            chat = self._s.resolve_chat(entity, 'user')
            msg = message if isinstance(message, SbMessage) else self._s.find_message(chat, message)
            if msg is None:
                raise SbError("MessageIdInvalidError: сообщение не найдено")
        return await msg.edit(text, parse_mode=parse_mode, buttons=buttons, file=file)

    async def delete_messages(self, entity, message_ids, *, revoke=True):
        chat = self._s.resolve_chat(entity, 'user')
        ids = message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
        for mid in ids:
            m = mid if isinstance(mid, SbMessage) else self._s.find_message(chat, mid)
            if m is not None:
                await m.delete()
        return [types.SimpleNamespace(pts_count=len(ids))]

    async def forward_messages(self, entity, messages, from_peer=None, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        out = []
        for m in (messages if isinstance(messages, (list, tuple)) else [messages]):
            src = m if isinstance(m, SbMessage) else self._s.find_message(self._s.resolve_chat(from_peer, 'user'), m)
            if src is not None:
                out.append(await src.forward_to(chat))
        return out if isinstance(messages, (list, tuple)) else (out[0] if out else None)

    async def pin_message(self, entity, message=None, *, notify=False, pm_oneside=False):
        chat = self._s.resolve_chat(entity, 'user')
        m = message if isinstance(message, SbMessage) else self._s.find_message(chat, message)
        if m is not None:
            self._s.pin(chat, m)

    async def unpin_message(self, entity, message=None, *, notify=False):
        chat = self._s.resolve_chat(entity, 'user')
        m = message if isinstance(message, SbMessage) else (self._s.find_message(chat, message) if message else None)
        self._s.unpin(chat, m)

    async def get_entity(self, entity):
        if isinstance(entity, (list, tuple)):
            return [await self.get_entity(e) for e in entity]
        if isinstance(entity, SbUser):
            return entity
        if isinstance(entity, int) and entity in self._s.users:
            return self._s.users[entity]
        if isinstance(entity, str) and entity.lstrip('@').lower() in {u.username.lower() for u in self._s.users.values() if u.username}:
            return next(u for u in self._s.users.values() if u.username and u.username.lower() == entity.lstrip('@').lower())
        chat = self._s.resolve_chat(entity, 'user')
        return self._s.entity_of(chat)

    get_input_entity = get_entity

    async def get_messages(self, entity, limit=None, *, ids=None, from_user=None, search=None, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        if ids is not None:
            if isinstance(ids, (list, tuple)):
                return [self._s.find_message(chat, i) for i in ids]
            return self._s.find_message(chat, ids)
        items = [m async for m in self.iter_messages(chat, limit=limit or 1, from_user=from_user, search=search)]
        return items if (limit or 1) != 1 else (items[0] if items else None)

    async def iter_messages(self, entity, limit=None, *, from_user=None, search=None, reverse=False, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        msgs = [m for m in self._s.messages_of(chat) if not m.deleted]
        if from_user is not None:
            uid = from_user.id if isinstance(from_user, SbUser) else (self._s.owner.id if from_user == 'me' else from_user)
            msgs = [m for m in msgs if m.sender_id == uid]
        if search:
            msgs = [m for m in msgs if search.lower() in (m._text or '').lower()]
        msgs = msgs if reverse else list(reversed(msgs))
        for m in (msgs[:limit] if limit else msgs):
            yield m

    async def get_participants(self, entity, limit=None, **kw):
        chat = self._s.resolve_chat(entity, 'user')
        if chat.type == 'private':
            return [self._s.owner, self._s.users[chat.peer]] if chat.peer in self._s.users else [self._s.owner]
        users = list(self._s.users.values())
        return users[:limit] if limit else users

    async def iter_participants(self, entity, limit=None, **kw):
        for u in await self.get_participants(entity, limit=limit):
            yield u

    async def get_dialogs(self, limit=None, **kw):
        items = [types.SimpleNamespace(id=c.id, title=c.title, name=c.title, entity=self._s.entity_of(c), is_group=c.type in ('group', 'supergroup'),
                                       is_channel=c.type in ('channel', 'supergroup'), is_user=c.type == 'private')
                 for c in self._s.chats.values() if c.side in ('user', 'both')]
        return items[:limit] if limit else items

    async def send_read_acknowledge(self, *a, **kw):
        return True

    async def download_media(self, message, file=None, **kw):
        return await message.download_media(file)

    async def __call__(self, request):
        name = type(request).__name__
        self._s.note('api', f"client({name}) — MTProto-запрос не выполняется в Sandbox", chat=None)
        raise SbUnsupported(f"MTProto-запрос {name} не выполняется в Sandbox (сети Telegram здесь нет)")

    def __getattr__(self, item):
        if item.startswith('__') or item == '_s':
            raise AttributeError(item)
        self._s.note('warn', f"client.{item}() не эмулируется в Sandbox")
        raise SbUnsupported(f"client.{item}() не эмулируется в Sandbox")


# --------------------------------------------------------------------------- #
# db / cache: хранилища с учётом каждого обращения к ключам
# --------------------------------------------------------------------------- #

class SbStore:
    """
    Низкоуровневое хранилище песочницы (аналог Database / MemoryCache).
    db: как и настоящая база, «пишет на диск» JSON целиком при каждом set/delete — значение, которое нельзя
    превратить в JSON, даёт TypeError (и остаётся в памяти, как в настоящей базе); живые объекты, изменённые без
    повторного set(), после перезапуска песочницы возвращаются к последней записанной версии.
    cache: только память, при перезапуске очищается.
    """

    def __init__(self, session, kind, persistent):
        self.s = session
        self.kind = kind
        self.persistent = persistent
        self.mem = {}
        self.snapshot = '{}'
        self.stats = {}

    # ---- без учёта (внутреннее и действия из интерфейса) ---- #
    def raw_get(self, key, default=None):
        return self.mem.get(key, default)

    def raw_set(self, key, value):
        self.mem[key] = value
        if self.persistent:
            self.snapshot = json.dumps(self.mem, ensure_ascii=False)       # TypeError, если значение не JSON — как в настоящей базе
        return value

    def raw_delete(self, key):
        if key in self.mem:
            del self.mem[key]
            if self.persistent:
                self.snapshot = json.dumps(self.mem, ensure_ascii=False)
            return True
        return False

    def restart(self):
        if self.persistent:
            try:
                self.mem = json.loads(self.snapshot)
            except Exception:
                self.mem = {}
        else:
            self.mem = {}

    # ---- с учётом (то, что делает модуль) ---- #
    def _stat(self, full, scope):
        st = self.stats.get(full)
        if st is None:
            st = {'key': full, 'scope': scope, 'reads': 0, 'writes': 0, 'deletes': 0, 'misses': 0,
                  'first': _real_time.time(), 'last': 0, 'fn': None}
            self.stats[full] = st
        st['last'] = _real_time.time()
        st['fn'] = sb_fn.get()
        return st

    def track(self, op, full, scope, hit=None, value=None):
        internal = full.endswith(':__config__')
        st = None if internal else self._stat(full, scope)
        if st is not None:
            if op == 'get':
                st['reads'] += 1
                if not hit:
                    st['misses'] += 1
            elif op == 'set':
                st['writes'] += 1
            elif op == 'delete':
                st['deletes'] += 1
        if op in ('list', 'glist'):
            detail = ''
        elif op == 'get':
            detail = f"→ {preview(value, 120)}" if hit else '→ нет такого ключа'
        elif op == 'set':
            detail = f"= {preview(value, 120)}"
        else:
            detail = '' if hit else '(ключа не было)'
        self.s.note(self.kind, f"{self.kind}.{op}({full!r}) {detail}".strip(), store=self.kind, op=op, key=full, scope=scope,
                    hit=hit, internal=internal)

    def rows(self):
        """Таблица ключей для интерфейса: текущие значения + статистика обращений."""
        out = []
        for full in sorted(set(self.mem) | set(self.stats)):
            st = self.stats.get(full) or {}
            present = full in self.mem
            val = self.mem.get(full)
            out.append({'key': full, 'present': present, 'value': preview(val, 400) if present else None,
                        'type': type_name(val) if present else None, 'scope': st.get('scope') or ('config' if full.endswith(':__config__') else 'module'),
                        'config': full.endswith(':__config__'), 'reads': st.get('reads', 0), 'writes': st.get('writes', 0),
                        'deletes': st.get('deletes', 0), 'misses': st.get('misses', 0), 'fn': st.get('fn'),
                        'last': st.get('last') or None, 'json': json.dumps(val, ensure_ascii=False, indent=2, default=repr)[:20000] if present else ''})
        return out


class SbRawDB:
    """engine.db — «сырой» async-доступ (так движок хранит конфиги). Не попадает в учёт ключей модуля."""

    def __init__(self, store):
        self._st = store

    async def get(self, key, default=None):
        return self._st.raw_get(key, default)

    async def set(self, key, value):
        return self._st.raw_set(key, value)

    async def delete(self, key):
        return self._st.raw_delete(key)

    async def list_keys(self, prefix=None):
        return [k for k in self._st.mem if prefix is None or k.startswith(prefix)]


class SbDynamic:
    """`import db` / `import cache` внутри модуля: тот же интерфейс, что DynamicDB/DynamicCache, с учётом обращений."""

    def __init__(self, store: SbStore):
        self._st = store

    def _mod(self):
        m = current_module.get()
        if m is None or m == '__loading__':
            raise RuntimeError(f"{self._st.kind}.get/{self._st.kind}.set вызваны вне контекста модуля — "
                               f"используйте {self._st.kind}.gget/{self._st.kind}.gset, либо вызывайте это изнутри хэндлера модуля")
        return m

    async def _get(self, full, scope, default):
        hit = full in self._st.mem
        val = self._st.mem.get(full, default)
        self._st.track('get', full, scope, hit=hit, value=val if hit else None)
        return val

    async def _set(self, full, scope, value):
        try:
            self._st.raw_set(full, value)
        finally:
            self._st.track('set', full, scope, value=value)
        return value

    async def _delete(self, full, scope):
        ok = self._st.raw_delete(full)
        self._st.track('delete', full, scope, hit=ok)
        return ok

    async def get(self, key, default=None):
        return await self._get(f"{self._mod()}:{key}", 'module', default)

    async def set(self, key, value):
        return await self._set(f"{self._mod()}:{key}", 'module', value)

    async def delete(self, key):
        return await self._delete(f"{self._mod()}:{key}", 'module')

    async def gget(self, key, default=None):
        return await self._get(key, 'global', default)

    async def gset(self, key, value):
        return await self._set(key, 'global', value)

    async def gdelete(self, key):
        return await self._delete(key, 'global')

    async def list(self, modulename=None):
        modulename = modulename or self._mod()
        prefix = f"{modulename}:"
        self._st.track('list', prefix, 'module')
        return {k[len(prefix):]: v for k, v in self._st.mem.items() if k.startswith(prefix)}

    async def glist(self, prefix=None):
        self._st.track('glist', prefix or '', 'global')
        return {k: v for k, v in self._st.mem.items() if prefix is None or k.startswith(prefix)}


_DB_OPS = {'get', 'set', 'delete', 'gget', 'gset', 'gdelete', 'list', 'glist'}


def scan_static(source: str) -> dict:
    """
    Что модуль делает с данными по тексту кода (без запуска): обращения к db/cache с литеральным ключом
    (или шаблоном f"user_{…}"), create_config / get_config. Для таблицы «ключи в коде» в Sandbox.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {'keys': [], 'configs': []}
    keys, configs = [], []

    def key_of(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, False
        if isinstance(node, ast.JoinedStr):
            parts = []
            for v in node.values:
                parts.append(v.value if isinstance(v, ast.Constant) else '{…}')
            return ''.join(parts), True
        return None, True

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        base = node.func.value
        if isinstance(base, ast.Name) and base.id in ('db', 'cache') and node.func.attr in _DB_OPS and node.func.attr not in ('list', 'glist'):
            k, dyn = key_of(node.args[0]) if node.args else (None, True)
            keys.append({'store': base.id, 'op': node.func.attr, 'scope': 'global' if node.func.attr.startswith('g') else 'module',
                         'key': k, 'dynamic': dyn, 'line': node.lineno})
        elif isinstance(base, ast.Name) and base.id == 'engine' and node.func.attr in ('create_config', 'get_config', 'edit_config'):
            first = node.args[0] if node.args else next((kw.value for kw in node.keywords if kw.arg == 'key'), None)
            k, dyn = key_of(first) if first is not None else (None, True)
            configs.append({'op': node.func.attr, 'key': k, 'dynamic': dyn, 'line': node.lineno})
    keys.sort(key=lambda x: (x['line'], x['store']))
    configs.sort(key=lambda x: x['line'])
    return {'keys': keys, 'configs': configs}


# --------------------------------------------------------------------------- #
# реестр файлов песочницы
# --------------------------------------------------------------------------- #

class SbFiles:
    """files.create_file / get_file / delete_file во временной папке (удаляется вместе с песочницей)."""

    def __init__(self, session):
        self.s = session
        self.dir = Path(tempfile.mkdtemp(prefix='frx_sandbox_'))
        self.registry = {}

    def close(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _safe_name(self, name):
        return re.sub(r'[^\w.\- ]+', '_', Path(str(name)).name) or 'file'

    async def create(self, ftype, name, text):
        path = self.dir / self._safe_name(name)
        if isinstance(text, (bytes, bytearray)):
            path.write_bytes(bytes(text))
        else:
            path.write_text(str(text), encoding='utf-8')
        obj = {'id': uuid.uuid4().hex[:12], 'name': path.name, 'path': str(path), 'type': ftype, 'size': path.stat().st_size}
        self.registry[obj['id']] = obj
        self.s.note('api', f"files.create_file({name!r}) → {obj['id']}")
        return obj

    async def get(self, query=None, ftype=None):
        for obj in self.registry.values():
            if query is not None and str(query) in (obj['id'], obj['name'], obj['path'], str(obj['size'])):
                if ftype is None or obj['type'] == ftype:
                    return obj
        return None

    async def delete(self, file_id=None, path=None):
        for fid, obj in list(self.registry.items()):
            if (file_id and obj['id'] == file_id) or (path and obj['path'] == path):
                del self.registry[fid]
                Path(obj['path']).unlink(missing_ok=True)
                return True
        return False

    def resolve(self, file):
        """file= (id из реестра / путь / имя) → объект реестра либо исходное значение."""
        if file is None:
            return None
        for obj in self.registry.values():
            if str(file) in (obj['id'], obj['name'], obj['path']):
                return obj
        return file

    def write_download(self, media):
        path = self.dir / (media['id'] + '_' + self._safe_name(media['name']))
        data = media.get('content')
        path.write_bytes(data if data is not None else b'\0' * min(media['size'], 4096))
        obj = {'id': media['id'], 'name': media['name'], 'path': str(path), 'type': media['mime'], 'size': path.stat().st_size}
        self.registry.setdefault(obj['id'], obj)
        return str(path)


# --------------------------------------------------------------------------- #
# ожидание сообщений и inline-формы
# --------------------------------------------------------------------------- #

from .waiters import Waiter, WaiterCancelled                       # noqa: E402
from .inline_forms import InlineClick, InlineInput, InputButton, SearchButton, normalize_button_rows, picker_page, search_items   # noqa: E402


class SbWaiter(Waiter):
    """engine.wait_message() песочницы: фильтры по чату и автору, таймаут — по виртуальным часам."""

    def __init__(self, session, chats, users, timeout):
        self._s = session
        self._timeout = timeout
        self._core = session.engine
        self._chats = chats
        self._users = users
        self._queue = asyncio.Queue()
        self._cancelled = asyncio.Event()
        self._registered = False
        self._timeout_resolved = timeout is not None

    def _register(self):
        if not self._registered:
            self._s.waiters.add(self)
            self._registered = True
            self._s.touch_state()

    def _unregister(self):
        if self._registered:
            self._s.waiters.discard(self)
            self._registered = False
            self._s.touch_state()

    def matches(self, event):
        if self._chats is not None and event.chat_id not in self._chats:
            return False
        if self._users is not None and event.sender_id not in self._users:
            return False
        return True

    async def _on_event(self, event):
        await self._queue.put(event)

    async def _next(self):
        if self._cancelled.is_set():
            raise WaiterCancelled
        if not self._timeout_resolved:
            self._timeout = await self._core._waiters_default_timeout()
            self._timeout_resolved = True
        self._register()
        cancel_task = asyncio.ensure_future(self._cancelled.wait())
        queue_task = asyncio.ensure_future(self._queue.get())
        timer = self._s.clock.timer(self._timeout, label='wait_message') if self._timeout else None
        waits = {cancel_task, queue_task} | ({timer} if timer is not None else set())
        try:
            done, _ = await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in (cancel_task, queue_task, timer):
                if t is not None and not t.done():
                    t.cancel()
        if queue_task in done:
            return queue_task.result()
        if cancel_task in done:
            raise WaiterCancelled
        raise TimeoutError("wait_message: сообщение не пришло за отведённое время")


class SbClick(InlineClick):
    def __init__(self, session, form, data, user):
        super().__init__(data, user, form.chat_id, form.inline_message_id, None)
        self._s = session

    async def answer(self, text=None, show_alert=False):
        if self._answered:
            return
        self._answered = True
        if text:
            self._s.toast(text, 'error' if show_alert else 'info')


class SbForm:
    """Inline-форма песочницы — тот же API, что у InlineForm (on_click / on_input / edit / close)."""

    def __init__(self, session, chat: SbChat, owner_module, text, buttons, for_user=None, black_list=None, timeout=None):
        self._s = session
        self.form_id = uuid.uuid4().hex[:12]
        self.inline_message_id = 'sb:' + self.form_id
        self.chat = chat
        self.chat_id = chat.id
        self.owner_module = owner_module
        self.for_user = for_user
        self.black_list = black_list
        self.timeout = timeout
        self.text = text
        self._raw_buttons = buttons
        self.page = 0
        self._pages = [normalize_button_rows(buttons or [])]
        self._click_handlers = []
        self._input_handlers = []
        self._closed = False
        self._msg = None
        self._last = session.clock.now
        self._idle = asyncio.ensure_future(self._idle_watch()) if timeout else None

    def on_click(self, data=None, data_startswith=None):
        if data is not None and data_startswith is not None:
            raise ValueError("form.on_click(): укажите либо data=, либо data_startswith=, не оба сразу")

        def deco(func):
            self._click_handlers.append((data, data_startswith, func))
            return func
        return deco

    def on_input(self, data=None):
        def deco(func):
            self._input_handlers.append((data, func))
            return func
        return deco

    def on_search(self, data='search'):
        return self.on_input(data=data)

    def _show(self, text, buttons, closed=False):
        if self._msg is None:
            self._msg = SbMessage(self._s, self.chat, self._s.bot_user, text or '', via_bot=True, buttons=None if closed else buttons,
                                  form_id=self.form_id, html=True, reply_to=getattr(self, '_reply_id', None))
        else:
            self._msg._text = text if text is not None else self._msg._text
            self._msg._buttons = None if closed else buttons
            self._msg.edit_date = self._s.dt_now()
            self._msg._publish()
        self._s.feed.update(self._msg._entry, closed=closed)

    async def edit(self, text, buttons=None):
        if self._closed:
            raise RuntimeError(f"Форма '{self.form_id}' уже закрыта (form.close())")
        self.text = text
        self._raw_buttons = buttons
        self._pages = [normalize_button_rows(buttons or [])]
        self._show(safe_html(text), buttons)
        self._s.note('send', f"form.edit({preview(text, 80)})", chat=self.chat.key)

    async def _go_to_page(self, new_page):
        return None

    async def delete(self):
        """form.delete() — в эмуляции просто закрывает форму."""
        await self.close(remove_buttons=True)

    async def close(self, remove_buttons=True):
        if self._closed:
            return
        self._closed = True
        if self._idle is not None:
            self._idle.cancel()
        self._s.forms.pop(self.form_id, None)
        if self._msg is not None:
            self._show(None, None, closed=True) if remove_buttons else self._s.feed.update(self._msg._entry, closed=True)
        self._s.note('send', 'form.close()', chat=self.chat.key)

    async def _dispatch(self, click):
        self._last = self._s.clock.now
        matched = False
        for data_filter, startswith_filter, func in list(self._click_handlers):
            if data_filter is not None and data_filter != click.data:
                continue
            if startswith_filter is not None and not (click.data or '').startswith(startswith_filter):
                continue
            matched = True
            await self._s.call(self.owner_module, func, click)
        if not matched and not click._answered:
            await click.answer()

    async def _dispatch_input(self, inp):
        self._last = self._s.clock.now
        for data_filter, func in list(self._input_handlers):
            if data_filter is not None and data_filter != inp.data:
                continue
            await self._s.call(self.owner_module, func, inp)

    async def _idle_watch(self):
        try:
            while not self._closed:
                await self._s.clock.sleep(self.timeout)
                if self._s.clock.now - self._last >= self.timeout:
                    await self.close()
                    return
        except asyncio.CancelledError:
            pass

    def check_access(self, user_id):
        black = self.black_list or []
        black = black if isinstance(black, list) else [black]
        if user_id in black:
            return False
        allowed = 'me' if self.for_user is None else self.for_user
        for rule in (allowed if isinstance(allowed, list) else [allowed]):
            if rule == 'all':
                return True
            if rule == 'me':
                if user_id == self._s.owner.id:
                    return True
            elif isinstance(rule, int) and rule > 0:
                if user_id == rule:
                    return True
            elif isinstance(rule, int) and rule < 0:
                if user_id in self._s.users:       # в песочнице все участники — члены любого чата
                    return True
        return False


# --------------------------------------------------------------------------- #
# engine песочницы
# --------------------------------------------------------------------------- #

class SandboxEngine:
    """
    `import engine` внутри модуля. Часть методов настоящего Core подключается как есть (конфиги, проверки доступа
    бота) — поэтому поведение create_config / edit_config / get_config и прав доступа в точности как в ядре.
    """

    def __init__(self, session):
        from .engine import Core                                   # ленивый импорт: тянет telethon/aiogram
        self._Core = Core
        self.s = session
        self.db = SbRawDB(session.db_store)
        self.modules = {}
        self._declared_config_keys = {}
        self.file_registry = session.files
        self.client = session.client
        self.bot = session.bot_facade
        self._aiogram_bot = session.bot_facade.raw
        self.version = session.core_info.get('version', '?')
        self._owner_id = session.owner.id
        self._bot_watch_dedup = {}
        self.config = {'prefix': session.prefix}

    InputButton = InputButton
    SearchButton = SearchButton
    InlineInput = InlineInput
    InlineClick = InlineClick
    safe_html = staticmethod(safe_html)
    picker_page = staticmethod(picker_page)
    search_items = staticmethod(search_items)

    # ---- у настоящего Core берём как есть ---- #
    def _bind(name):                                                # noqa: N805
        def method(self, *a, **kw):
            return getattr(self._Core, name)(self, *a, **kw)
        method.__name__ = name
        return method

    _config_key = _bind('_config_key')
    module_links = _bind('module_links')
    _check_valid_spec = _bind('_check_valid_spec')
    _validate_config_value = _bind('_validate_config_value')
    _bot_access_ok = _bind('_bot_access_ok')
    _bot_extended_access_ok = _bind('_bot_extended_access_ok')
    _bot_dedup_seen = _bind('_bot_dedup_seen')
    _as_id_list = staticmethod(lambda value: None if value is None else (value if isinstance(value, list) else [value]))

    async def create_config(self, key, description='', data_type='str', default=None, valid=None, options=None):
        r = await self._Core.create_config(self, key, description, data_type, default, valid, options)
        self.s.note('config', f"create_config({key!r}, {data_type}) = {preview(r, 100)}", key=key)
        self.s.touch_state()
        return r

    async def edit_config(self, key, operation_type, value=None, module_name=None):
        r = await self._Core.edit_config(self, key, operation_type, value, module_name)
        self.s.note('config', f"edit_config({key!r}, {operation_type!r}) → {preview(r, 100)}", key=key)
        self.s.touch_state()
        return r

    async def delete_config(self, key):
        r = await self._Core.delete_config(self, key)
        self.s.note('config', f"delete_config({key!r})", key=key)
        self.s.touch_state()
        return r

    async def get_config(self, key, module_name=None):
        r = await self._Core.get_config(self, key, module_name)
        self.s.note('config', f"get_config({key!r}) → {preview(r, 100)}", key=key)
        return r

    # ---- ответы ---- #
    async def _resolve_file(self, file):
        return self.s.files.resolve(file) if file is not None else None

    async def answer(self, message, text, file=None):
        path = await self._resolve_file(file)
        text = safe_html(text)
        try:
            return await message.edit(text, file=path, parse_mode='html')
        except Exception:
            return await message.reply(text, file=path, parse_mode='html')

    async def reply(self, message, text, file=None):
        path = await self._resolve_file(file)
        return await message.reply(safe_html(text), file=path, parse_mode='html')

    async def noreply(self, message, text, file=None):
        path = await self._resolve_file(file)
        chat = message._chat if isinstance(message, SbMessage) else self.s.resolve_chat(message.chat_id, 'user')
        return await self.s.send_as_owner(chat, safe_html(text), file=path, parse_mode='html')

    async def send_message(self, chat_id, text, file=None):
        path = await self._resolve_file(file)
        chat = self.s.resolve_chat(chat_id, 'user')
        return await self.s.send_as_owner(chat, safe_html(text), file=path, parse_mode='html')

    async def bot_send_to_owner(self, text, file=None, buttons=None):
        if not self.s.bot_enabled:
            raise RuntimeError("Стационарный бот не запущен (aiogram_token не задан)")
        return await self.bot.send_message(self._owner_id, text, file=file, buttons=buttons)

    def get_args(self):
        event = current_event.get()
        if event is None:
            return ''
        parts = (event.raw_text or '').split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ''

    async def download_reply_file(self, message):
        if not message.is_reply:
            raise ValueError("Сообщение не является ответом (reply) на файл")
        replied = await message.get_reply_message()
        if not replied or not replied.file:
            raise ValueError("В сообщении, на которое отвечаем, нет файла")
        path = await replied.download_media(file=str(FILES_DIR) + '/')
        return await self.s.files.get(path) or self.s.files.registry.get(replied._media['id'])

    # ---- ожидание ---- #
    def wait_message(self, chat_id=None, user_id=None, timeout=None):
        chats = self._resolve_wait_target(chat_id, default_current_chat=True)
        users = self._resolve_wait_target(user_id, default_current_chat=False)
        return SbWaiter(self.s, chats, users, timeout)

    def _resolve_wait_target(self, value, default_current_chat):
        if value is None:
            if not default_current_chat:
                return None
            event = current_event.get()
            if event is None:
                raise RuntimeError("wait_message(): chat_id не передан и нет текущего события — укажите chat_id явно")
            return [event.chat_id]
        values = value if isinstance(value, list) else [value]
        return [self._owner_id if v == 'me' else v for v in values]

    async def _waiters_default_timeout(self):
        try:
            return await self.get_config('default_timeout', module_name='waiters')
        except KeyError:
            return 60

    # ---- формы ---- #
    async def create_inlineform(self, chat_id, text, buttons=None, for_user=None, black_list=None, timeout=None, reply_to=None):
        if not self.s.bot_enabled:
            raise RuntimeError("Стационарный бот не запущен (aiogram_token не задан)")
        chat = self.s.resolve_chat(chat_id, 'user')
        from .inline_forms import resolve_form_reply
        form = SbForm(self.s, chat, current_module.get() or self.s.name, text, buttons, for_user=for_user,
                      black_list=black_list, timeout=timeout)
        form._reply_id = resolve_form_reply(current_event.get(), chat.id, reply_to)
        self.s.forms[form.form_id] = form
        form._show(safe_html(text), buttons)
        self.s.note('send', f"create_inlineform({preview(text, 80)})", chat=chat.key)
        self.s.touch_state()
        return form

    # ---- прочее ---- #
    async def get_prefix(self):
        return self.s.prefix

    async def edit_prefix(self, new_prefix):
        self.s.prefix = new_prefix
        self.s.touch_state()
        return new_prefix

    async def default_prefix(self):
        return await self.edit_prefix('.')

    def tlog(self, text):
        self.s.note('log', str(text))

    def botlog(self, text, level='info', *, module=None, function=None):
        async def _go():
            if not self.s.bot_enabled:
                return 'no_bot'
            head = {'info': '◈', 'success': '🔶', 'warning': '⚠️', 'error': '🟥'}.get(level, '◈')
            body = f"{head} <b>{_html.escape(str(level).upper())}</b>\nМодуль: {module or current_module.get()}\n" \
                   f"Функция: {function or sb_fn.get() or '—'}\n\n{safe_html(text)}"
            await self.bot.send_message(self._owner_id, body)
            return 'sent'
        return asyncio.ensure_future(_go())

    def guest_id(self):
        """В песочнице команды запускает владелец — гостей нет."""
        return None

    def module_danger(self, module_name):
        """В песочнице гостей нет — пометки опасности не нужны."""
        return []

    def get_version(self):
        return self.version

    async def set_account(self, number):
        self.s.note('event', f"set_account({number})")
        await self.s.fire_account_switch(number)
        return self.client

    def get_module_list(self):
        """Настоящие имена загруженных модулей юзербота (только чтение) + проверяемый в песочнице модуль."""
        names = list(self.s.core_info.get('module_names') or [])
        for n in self.modules:
            if n not in names:
                names.append(n)
        return names

    def get_module_info(self, name):
        if name in self.modules:
            try:
                return self._Core.get_module_info(self, name)
            except Exception:
                return self.modules.get(name)
        real = (self.s.core_info.get('module_infos') or {}).get(name)
        if real is None:
            raise KeyError(f"Модуль '{name}' не найден")
        import copy
        return copy.deepcopy(real)

    def __getattr__(self, item):
        if item.startswith('__') or item in ('s', '_Core'):
            raise AttributeError(item)
        self.s.note('warn', f"engine.{item} не эмулируется в Sandbox")
        raise SbUnsupported(f"engine.{item} не эмулируется в Sandbox")


def _unsupported_module(name):
    class _M(types.ModuleType):
        def __getattr__(self, item):
            if item.startswith('__'):
                raise AttributeError(item)
            raise SbUnsupported(f"{name}.{item} не эмулируется в Sandbox")
    return _M(name)


# --------------------------------------------------------------------------- #
# поток песочницы
# --------------------------------------------------------------------------- #

class SandboxRuntime:
    """Отдельный поток со своим event loop + heartbeat. Зависший (цикл без await) поток останавливается принудительно."""

    def __init__(self, label):
        self.loop = asyncio.new_event_loop()
        self.ready = threading.Event()
        self.dead = False
        self.error = None
        self.killed = False
        self.last_beat = _real_time.monotonic()
        self.thread = threading.Thread(target=self._main, name=f'sandbox-{label}', daemon=True)
        self.thread.start()
        self.ready.wait(5)

    def _main(self):
        asyncio.set_event_loop(self.loop)
        self.loop.call_soon(self._beat)
        self.ready.set()
        try:
            self.loop.run_forever()
        except SbKilled:
            pass
        except BaseException as e:                          # noqa: BLE001
            self.error = e
        finally:
            self.dead = True
            try:
                for t in asyncio.all_tasks(self.loop):
                    t.cancel()
                self.loop.run_until_complete(asyncio.sleep(0))
            except BaseException:                           # noqa: BLE001
                pass
            try:
                self.loop.close()
            except BaseException:                           # noqa: BLE001
                pass

    def _beat(self):
        self.last_beat = _real_time.monotonic()
        if not self.dead:
            self.loop.call_later(0.5, self._beat)

    def alive(self):
        return not self.dead and self.thread.is_alive()

    def stalled(self):
        return self.alive() and _real_time.monotonic() - self.last_beat > STALL_SECONDS

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    async def run(self, coro, timeout=25):
        if not self.alive():
            coro.close()
            raise RuntimeError('Песочница остановлена — перезапустите её')
        fut = self.submit(coro)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)

    def kill(self):
        """Остановить поток: исключение в его Python-коде + остановка loop. Для кода без await это единственный способ."""
        if self.dead:
            return
        self.killed = True
        try:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(self.thread.ident), ctypes.py_object(SbKilled))
        except Exception:
            pass
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass

    def stop(self):
        if self.dead:
            return
        try:
            self.loop.call_soon_threadsafe(self.loop.stop)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# сессия Sandbox
# --------------------------------------------------------------------------- #

_BOT_MEMBER_ACTIVE = ('member', 'administrator', 'creator')


class Session:
    """Одна песочница = одна «страница» Sandbox. Все методы с async выполняются в потоке песочницы (через runtime.run)."""

    def __init__(self, name, core_info=None):
        self.name = name
        self.core_info = core_info or {}
        self.feed = Feed()                      # лента сообщений
        self.journal = Feed(cap=6000)           # журнал: события, вызовы, ошибки, обращения к ключам
        self.clock = VirtualClock()
        self.state_rev = 1
        self.runtime = None
        self.status = 'idle'                    # idle | running | stalled | dead
        self.status_text = ''
        self.prefix = self.core_info.get('prefix', '.')
        self.bot_enabled = True
        self.owner = SbUser(int(self.core_info.get('owner_id') or 100000001), 'Вы', None, 'me', is_self=True)
        self.bot_user = SbUser(900000001, 'FRX Test Bot', None, 'frx_test_bot', bot=True)
        self.users = {self.owner.id: self.owner}
        self.chats = {}
        self.msgs = {}
        self.chat_msgs = {}
        self.waiters = set()
        self.forms = {}
        self.tasks = set()
        self.db_store = SbStore(self, 'db', True)
        self.cache_store = SbStore(self, 'cache', False)
        self.files = SbFiles(self)
        self.client = SbClient(self)
        self.bot_facade = SbBotFacade(self)
        self.engine = SandboxEngine(self)
        self.module = None
        self.source = ''
        self.static = {'keys': [], 'configs': []}
        self.cycle_state = {}
        self.visual_icons = None
        self.page_states = {}
        self._dedup = {}
        self.module_tasks = []
        self.runs = 0
        self.loaded_version = None
        self._seed()
        self._load_custom()
        self._builtins = None
        self._shims = None
        self.last_state = None

    # ------------------------------------------------------------------ #
    # модель: участники и чаты
    # ------------------------------------------------------------------ #

    def _seed(self):
        for uid, first, last, uname, prem in ((200000001, 'Алиса', 'Тестова', 'alice_test', False),
                                              (200000002, 'Боб', 'Пример', 'bob_test', False),
                                              (200000003, 'Чарли', None, 'charlie_test', True)):
            u = SbUser(uid, first, last, uname, premium=prem)
            self.users[uid] = u
        self.users[self.bot_user.id] = self.bot_user
        add = lambda c: self.chats.__setitem__(c.key, c)                                   # noqa: E731
        add(SbChat('saved', self.owner.id, 'private', 'Избранное', 'user', peer=self.owner.id, saved=True))
        add(SbChat('u:200000001', 200000001, 'private', 'Алиса', 'user', peer=200000001))
        add(SbChat('u:200000002', 200000002, 'private', 'Боб', 'user', peer=200000002))
        add(SbChat('g:1', -1001000000001, 'supergroup', 'Тестовая группа', 'both', username='frx_test_group'))
        add(SbChat('c:1', -1001000000002, 'channel', 'Тестовый канал', 'user', username='frx_test_channel'))
        add(SbChat('b:me', self.owner.id, 'private', 'ЛС с ботом · вы', 'bot', peer=self.owner.id))
        add(SbChat('b:200000001', 200000001, 'private', 'ЛС с ботом · Алиса', 'bot', peer=200000001))

    # --- пользовательские участники и чаты: общие для всех модулей, лежат в user/data/sandbox/people.json ---

    def _load_custom(self):
        try:
            raw = json.loads(PEOPLE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        if not isinstance(raw, dict):
            return
        for d in raw.get('users') or []:
            try:
                uid = int(d['id'])
                if uid in self.users or uid == self.bot_user.id:
                    continue
                u = SbUser(uid, str(d.get('first_name') or f'Участник {uid}')[:64], (str(d['last_name'])[:64] if d.get('last_name') else None),
                           (str(d.get('username') or '').lstrip('@')[:32] or None), premium=bool(d.get('premium')))
                u.custom = True
                self.users[uid] = u
                self._add_private_chats(u)
            except (KeyError, TypeError, ValueError):
                continue
        for d in raw.get('chats') or []:
            try:
                key = str(d['key'])
                if key in self.chats or not key.startswith('x:'):
                    continue
                c = SbChat(key, int(d['id']), str(d['type']), str(d['title'])[:64], str(d.get('side') or 'both'), username=(d.get('username') or None))
                c.custom = True
                self.chats[key] = c
            except (KeyError, TypeError, ValueError):
                continue

    def _save_custom(self):
        data = {'users': [{'id': u.id, 'first_name': u.first_name, 'last_name': u.last_name, 'username': u.username, 'premium': u.premium}
                          for u in self.users.values() if getattr(u, 'custom', False)],
                'chats': [{'key': c.key, 'id': c.id, 'type': c.type, 'title': c.title, 'side': c.side, 'username': c.username}
                          for c in self.chats.values() if getattr(c, 'custom', False)]}
        try:
            with _PEOPLE_LOCK:
                PEOPLE_FILE.parent.mkdir(parents=True, exist_ok=True)
                tmp = PEOPLE_FILE.with_suffix('.tmp')
                tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding='utf-8')
                os.replace(tmp, PEOPLE_FILE)
        except OSError as e:
            self.note('warn', f'участники не сохранены на диск: {e}')

    def _add_private_chats(self, u):
        for key, side, title in ((f'u:{u.id}', 'user', u.name), (f'b:{u.id}', 'bot', f'ЛС с ботом · {u.name}')):
            if key not in self.chats:
                c = SbChat(key, u.id, 'private', title, side, peer=u.id)
                self.chats[key] = c

    def add_user(self, first_name, username=None, premium=False, uid=None, last_name=None):
        if uid in (None, ''):
            uid = 200000100
            while uid in self.users:
                uid += 1
        else:
            try:
                uid = int(uid)
            except (TypeError, ValueError):
                raise ValueError('ID должен быть целым числом')
            if uid <= 0 or uid >= 2 ** 52:
                raise ValueError('ID — положительное целое число (как у настоящего пользователя Telegram)')
            if uid in self.users or uid == self.bot_user.id:
                raise ValueError(f'участник с ID {uid} уже есть')
        uname = (username or '').strip().lstrip('@')[:32] or None
        if uname and any(x.username and x.username.lower() == uname.lower() for x in self.users.values()):
            raise ValueError(f'@{uname} уже занят другим участником')
        u = SbUser(uid, (first_name or '').strip()[:64] or f'Участник {uid}', (last_name or '').strip()[:64] or None, uname, premium=premium)
        u.custom = True
        self.users[uid] = u
        self._add_private_chats(u)
        self._save_custom()
        self.touch_state()
        return u

    def delete_user(self, uid):
        u = self.users.get(int(uid))
        if not u or not getattr(u, 'custom', False):
            raise ValueError('Удалять можно только созданных вами участников')
        del self.users[u.id]
        for k in [k for k, c in self.chats.items() if c.type == 'private' and c.peer == u.id and k not in ('saved', 'b:me')]:
            del self.chats[k]
        self._save_custom()
        self.touch_state()

    def delete_chat(self, key):
        c = self.chats.get(key)
        if not c or not getattr(c, 'custom', False) or c.type == 'private':
            raise ValueError('Удалять можно только созданные вами чаты')
        del self.chats[key]
        self._save_custom()
        self.touch_state()

    def add_chat(self, title, type='group', side='both', cid=None):
        type = type if type in ('group', 'supergroup', 'channel', 'private') else 'group'
        side = side if side in ('user', 'bot', 'both') else 'both'
        if type == 'channel':
            side = 'user'
        if type == 'private':
            raise ValueError('личные чаты создаются вместе с участником')
        n = 1
        while f'x:{n}' in self.chats:
            n += 1
        if cid in (None, ''):
            cid = -(1001000000100 + n) if type in ('supergroup', 'channel') else -(4000000 + n)
            while any(c.id == cid for c in self.chats.values()):
                cid -= 1
        else:
            try:
                cid = int(cid)
            except (TypeError, ValueError):
                raise ValueError('ID чата должен быть целым числом')
            if cid >= 0:
                raise ValueError('ID группы, супергруппы и канала — отрицательное число (например -1001234567890)')
            if any(c.id == cid and c.type != 'private' for c in self.chats.values()):
                raise ValueError(f'чат с ID {cid} уже есть')
        chat = SbChat(f'x:{n}', cid, type, (title or '').strip()[:64] or f'Чат {n}', side)
        chat.custom = True
        self.chats[chat.key] = chat
        self._save_custom()
        self.touch_state()
        return chat

    def chat_for_user(self, user: SbUser, side):
        """Личный чат с участником на нужной стороне (создаётся при необходимости)."""
        if user.id == self.owner.id:
            return self.chats['saved'] if side == 'user' else self.chats['b:me']
        key = ('u:' if side == 'user' else 'b:') + str(user.id)
        if key not in self.chats:
            self.chats[key] = SbChat(key, user.id, 'private', user.name if side == 'user' else f'ЛС с ботом · {user.name}', side, peer=user.id)
            self.touch_state()
        return self.chats[key]

    def resolve_chat(self, entity, side):
        """entity (id / @username / 'me' / SbChat / SbUser) → чат, видимый на стороне side ('user' | 'bot')."""
        ok = lambda c: c.side in (side, 'both')                                            # noqa: E731
        if isinstance(entity, SbChat):
            return entity
        if isinstance(entity, SbUser):
            return self.chat_for_user(entity, side)
        if entity is None:
            raise ValueError('chat не указан')
        if entity in ('me', 'self') or (side == 'user' and entity == self.owner.id):
            return self.chats['saved'] if side == 'user' else self.chats['b:me']
        if isinstance(entity, SbEntityChat):
            entity = entity._chat.id
        if isinstance(entity, int):
            for c in self.chats.values():
                if c.id == entity and ok(c):
                    return c
            if entity in self.users and entity != self.bot_user.id:
                return self.chat_for_user(self.users[entity], side)
            if entity < 0:
                key = f'x:auto{abs(entity)}'
                if key not in self.chats:
                    self.chats[key] = SbChat(key, entity, 'supergroup' if str(entity).startswith('-100') else 'group', f'Чат {entity}', 'both')
                    self.note('info', f'чат {entity} создан автоматически (его не было в списке Sandbox)')
                    self.touch_state()
                return self.chats[key]
            raise ValueError(f"Cannot find any entity corresponding to {entity!r}")
        if isinstance(entity, str):
            name = entity.lstrip('@').lower()
            for u in self.users.values():
                if u.username and u.username.lower() == name:
                    return self.chat_for_user(u, side)
            for c in self.chats.values():
                if c.username and c.username.lower() == name and ok(c):
                    return c
            raise ValueError(f"Cannot find any entity corresponding to {entity!r}")
        raise TypeError(f'Cannot cast {type(entity).__name__} to any kind of chat')

    def entity_of(self, chat: SbChat):
        if chat.type == 'private':
            return self.users.get(chat.peer, self.owner)
        return SbEntityChat(chat)

    # ------------------------------------------------------------------ #
    # сообщения, журнал, состояние
    # ------------------------------------------------------------------ #

    def dt_now(self):
        return _real_datetime.datetime.fromtimestamp(self.clock.now)

    def note(self, kind, text, **extra):
        self.state_rev += 1
        return self.journal.add(kind=kind, text=text, vt=self.clock.now, fn=sb_fn.get(), **extra)

    def toast(self, text, level='info'):
        self.feed.add(kind='toast', text=str(text), level=level, vt=self.clock.now)
        self.note('toast', str(text), level=level)

    def touch_state(self):
        self.state_rev += 1

    def register_message(self, m: SbMessage):
        self.msgs[(m._chat.key, m.id)] = m
        self.chat_msgs.setdefault(m._chat.key, []).append(m)

    def find_message(self, chat: SbChat, mid):
        return self.msgs.get((chat.key, mid))

    def messages_of(self, chat: SbChat):
        return list(self.chat_msgs.get(chat.key, []))

    def pin(self, chat, message):
        old = self.find_message(chat, chat.pinned) if chat.pinned else None
        if old is not None:
            old.pinned = False
            old._publish()
        chat.pinned = message.id
        message.pinned = True
        message._publish()
        self.note('send', f"pin_message #{message.id}", chat=chat.key)

    def unpin(self, chat, message=None):
        target = message or (self.find_message(chat, chat.pinned) if chat.pinned else None)
        if target is not None:
            target.pinned = False
            target._publish()
        chat.pinned = None
        self.note('send', 'unpin_message', chat=chat.key)

    async def send_as_owner(self, chat: SbChat, text, *, file=None, parse_mode=None, reply_to=None, buttons=None):
        media = media_from_file(self.files.resolve(file)) if file is not None else None
        if buttons:
            self.note('info', 'buttons= у сообщения юзербота в Sandbox не отображаются (для кнопок используйте create_inlineform)')
        m = SbMessage(self, chat, self.owner, text or '', out=True, reply_to=reply_to, media=media,
                      html=(parse_mode or '').lower() == 'html')
        self.note('send', f"{'reply' if reply_to else 'send_message'} → «{chat.title}»: {preview(text or (media or {}).get('name') or '', 140)}",
                  chat=chat.key)
        self._loopback(m)
        return m

    async def send_as_bot(self, chat_id, text, *, buttons=None, reply_to=None, media=None, html=True):
        if not self.bot_enabled:
            raise RuntimeError("Стационарный бот не запущен (aiogram_token не задан)")
        chat = self.resolve_chat(chat_id, 'bot')
        m = SbMessage(self, chat, self.bot_user, text or '', out=False, reply_to=reply_to, media=media, via_bot=False,
                      buttons=buttons, html=html)
        self.note('send', f"bot → «{chat.title}»: {preview(text or (media or {}).get('name') or '', 140)}", chat=chat.key)
        if chat.side == 'both':
            self._loopback(m)
        return SbBotMessage(m)

    def _loopback(self, m: SbMessage):
        """Сообщения, которые отправил сам модуль, юзербот в Telegram получает как новые события — вотчеры их видят."""
        if m._chat.side not in ('user', 'both'):
            return
        depth = sb_depth.get()
        if depth >= LOOPBACK_DEPTH:
            self.note('warn', f'цепочка собственных сообщений оборвана на глубине {LOOPBACK_DEPTH}: модуль отвечает сам себе '
                              f'(в настоящем Telegram такая цепочка не остановилась бы)')
            return
        self.spawn(self.dispatch_user_message(SbEvent(self, m)), f'свои сообщения → #{m.id}', depth=depth + 1)

    def spawn(self, coro, label='', depth=None):
        async def _wrap():
            if depth is not None:
                sb_depth.set(depth)
            try:
                await coro
            except asyncio.CancelledError:
                raise
            except SbKilled:
                return
            except Exception as e:                                  # noqa: BLE001
                self.note('error', f'{type(e).__name__}: {e}', label=label)
        t = asyncio.ensure_future(_wrap())
        self.tasks.add(t)
        t.add_done_callback(lambda _t: (self.tasks.discard(_t), self.touch_state()))
        self.touch_state()
        return t

    async def settle(self, timeout=1.2):
        """Дать запущенным обработчикам завершиться (долгие — остаются работать в фоне, ждут время/сообщения)."""
        for _ in range(4):
            await _REAL_SLEEP(0)
        pending = [t for t in self.tasks if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=timeout)
        for _ in range(3):
            await _REAL_SLEEP(0)

    # ------------------------------------------------------------------ #
    # исполнение функций модуля и ошибки
    # ------------------------------------------------------------------ #

    def _frames(self, exc):
        lines = self.source.split('\n')
        out = []
        for fs in traceback.extract_tb(exc.__traceback__):
            if fs.filename.startswith(SB_FILE_PREFIX):
                out.append({'line': fs.lineno, 'func': fs.name, 'code': (lines[fs.lineno - 1].strip() if 0 < fs.lineno <= len(lines) else '')})
        return out

    async def call(self, module, func, *args, bot=False, **kw):
        fname = getattr(func, '__name__', str(func))
        mtoken = current_module.set(module)
        ftoken = sb_fn.set(fname)
        item = self.note('handler', f'▶ {fname}', phase='run', ok=None, name=fname)
        t0 = _real_time.perf_counter()
        v0 = self.clock.now
        self.runs += 1
        try:
            result = await func(*args, **kw)
            self.journal.update(item, text=f'✔ {fname}', phase='end', ok=True, ms=round((_real_time.perf_counter() - t0) * 1000, 1),
                                vdelta=round(self.clock.now - v0, 3))
            return result
        except asyncio.CancelledError:
            self.journal.update(item, text=f'■ {fname} (прерван)', phase='end', ok=None)
            raise
        except Exception as e:                                      # noqa: BLE001
            self.journal.update(item, text=f'✘ {fname}', phase='end', ok=False, ms=round((_real_time.perf_counter() - t0) * 1000, 1))
            await self.dispatch_error(module, func, e, bot=bot)
            return None
        finally:
            sb_fn.reset(ftoken)
            current_module.reset(mtoken)

    async def dispatch_error(self, module, func, exc, bot=False):
        text = f"{type(exc).__name__}: {exc}"
        fname = getattr(func, '__name__', str(func))
        handlers = self.module.bot_on_error_handlers if (bot and self.module) else (self.module.on_error_handlers if self.module else [])
        self.note('error', text, frames=self._frames(exc), function=fname, module=module, bot=bot, handlers=len(handlers),
                  tb=''.join(traceback.format_exception_only(type(exc), exc)).strip())
        await self.run_error_handlers(text, {'module': module, 'function': fname}, bot=bot)

    async def run_error_handlers(self, text, context, bot=False):
        if not self.module:
            return
        handlers = self.module.bot_on_error_handlers if bot else self.module.on_error_handlers
        for handler in list(handlers):
            mtoken = current_module.set(self.module.module_name)
            ftoken = sb_fn.set(handler.__name__)
            try:
                self.note('handler', f'▶ {handler.__name__} (on_error)', phase='run', name=handler.__name__)
                await handler(text, context)
            except asyncio.CancelledError:
                raise
            except Exception as inner:                              # noqa: BLE001
                self.note('error', f"обработчик ошибок {handler.__name__} сам упал — {type(inner).__name__}: {inner}",
                          frames=self._frames(inner), function=handler.__name__, module=self.module.module_name, bot=bot, handlers=0)
            finally:
                sb_fn.reset(ftoken)
                current_module.reset(mtoken)

    # ------------------------------------------------------------------ #
    # загрузка и выгрузка модуля
    # ------------------------------------------------------------------ #

    def _make_env(self):
        from . import decorators as decorators_module, bot_decorators as bot_decorators_module, time_utils as time_utils_module
        from .web import ui as web_ui_module
        from .visual import _DEFAULT_ICONS
        self.visual_icons = dict(_DEFAULT_ICONS)
        icons = self.visual_icons

        def visual(key):
            if key not in icons:
                raise KeyError(f"Неизвестный ключ иконки: '{key}'. См. visual_list().")
            return icons[key]

        def visual_edit(key, value='standart'):
            if key not in _DEFAULT_ICONS:
                raise KeyError(f"Неизвестный ключ иконки: '{key}'. См. visual_list().")
            icons[key] = _DEFAULT_ICONS[key] if value == 'standart' else value
            return icons[key]

        shims = build_shims(self.clock, self.note)
        visual_mod = types.ModuleType('visual')
        visual_mod.visual, visual_mod.visual_edit, visual_mod.visual_list = visual, visual_edit, lambda: dict(icons)
        from .visual import make_helpers
        for _k, _f in make_helpers(visual).items():      # title/section/field/ok/… — на копии иконок песочницы
            setattr(visual_mod, _k, _f)
        waiters_mod = types.ModuleType('waiters')
        waiters_mod.wait_message = self.engine.wait_message
        waiters_mod.WaiterCancelled = WaiterCancelled
        files_mod = types.ModuleType('files')
        files_mod.download_reply_file = self.engine.download_reply_file
        async def create_file(type, name, text):                    # те же имена параметров, что у настоящего files.create_file
            return await self.files.create(type, name, text)

        async def reply_file(message, file, text=''):
            return await message.reply(safe_html(text), file=self.files.resolve(file), parse_mode='html')

        files_mod.get_file, files_mod.delete_file, files_mod.create_file = self.files.get, self.files.delete, create_file
        files_mod.reply_file = reply_file
        tu = _Shim('time_utils', time_utils_module, {'get_time': lambda: shims['datetime'].datetime.now()})
        self._shims = {
            'engine': self.engine, 'decorators': decorators_module, 'bot': bot_decorators_module, 'visual': visual_mod,
            'waiters': waiters_mod, 'files': files_mod, 'db': SbDynamic(self.db_store), 'cache': SbDynamic(self.cache_store),
            'accounts': _unsupported_module('accounts'), 'time_utils': tu, 'web': web_ui_module, **shims,
        }
        real_import = builtins.__import__
        mods = self._shims

        def sb_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0:
                if name in mods:
                    return mods[name]
                top = name.split('.')[0]
                if top in ('time', 'datetime', 'asyncio'):
                    result = real_import(name, globals, locals, fromlist, level)
                    return mods[top] if not fromlist else result
            return real_import(name, globals, locals, fromlist, level)

        b = dict(vars(builtins))
        b['__import__'] = sb_import
        self._builtins = b

    async def unload(self, keep_forms=False):
        info = self.module
        for t in list(self.module_tasks):
            t.cancel()
        self.module_tasks = []
        for w in list(self.waiters):
            w.cancel()
        self.waiters.clear()
        for form in list(self.forms.values()):
            if form._idle is not None:
                form._idle.cancel()
        self.forms.clear()
        self.clock.pause()
        for t in list(self.tasks):
            t.cancel()
        await _REAL_SLEEP(0)
        self.tasks.clear()
        self.cycle_state = {}
        self.module = None
        self.engine.modules.clear()
        self.touch_state()
        return info

    async def load(self, source, fire_start=True):
        """Выполнить исходник модуля в песочнице (без записи в папку модулей и без настоящего ядра)."""
        from .engine import ModuleInfo
        await self.unload()
        self.source = source
        self.static = scan_static(source)
        code = compile(source, f"{SB_FILE_PREFIX}module>", 'exec')          # SyntaxError — с номером строки
        meta = parse_module_meta(source)
        if meta is None:
            raise ValueError("Не удаётся прочитать шапку модуля: MODULE_NAME должен быть строкой")
        self._make_env()
        ns = {'__name__': '__frx_module__', '__builtins__': self._builtins}
        token = current_module.set('__loading__')
        try:
            exec(code, ns)
        finally:
            current_module.reset(token)
        module_name = ns.get('MODULE_NAME')
        if not module_name:
            raise ValueError("В модуле не указан MODULE_NAME")
        self.name = module_name
        info = ModuleInfo(module_name=module_name, name=ns.get('NAME', module_name), version=str(ns.get('VERSION', '1.0')),
                          author=ns.get('AUTHOR', 'unknown'), description=ns.get('DESCRIPTION', ''),
                          path=str(MODULES_DIR / f'{module_name}.py'), source=source, namespace=ns)
        self._scan(info, ns)
        self.module = info
        self.engine.modules[module_name] = info
        self.loaded_version = info.version
        mtoken = current_module.set(module_name)
        try:
            for func, interval in info.cycle_handlers:
                self.module_tasks.append(asyncio.ensure_future(self._cycle(module_name, func, interval, bot=False)))
            self.note('event', f'загрузка модуля «{info.name}» v{info.version}')
            for func in info.on_load_handlers:
                await self.call(module_name, func, info)
            for func in info.bot_on_load_handlers:
                await self.call(module_name, func, info, bot=True)
            if fire_start:
                await self._start_handlers()
        finally:
            current_module.reset(mtoken)
        self.status, self.status_text = 'running', ''
        self.touch_state()
        return info

    async def _start_handlers(self):
        info = self.module
        for func in info.on_start_handlers:
            await self.call(info.module_name, func)
        if self.bot_enabled:
            for func in info.bot_on_start_handlers:
                await self.call(info.module_name, func, bot=True)
            for func, interval in info.bot_cycle_handlers:
                self.module_tasks.append(asyncio.ensure_future(self._cycle(info.module_name, func, interval, bot=True)))

    def _scan(self, info, ns):
        real_cmds = self.core_info.get('real_commands', {}) or {}
        real_bot_cmds = self.core_info.get('real_bot_commands', {}) or {}
        reserved = set(self.core_info.get('bot_reserved', ()) or ())
        for value in list(ns.values()):
            if not callable(value):
                continue
            ftype = getattr(value, '_frx_type', None)
            if ftype == 'command':
                for alias in value._frx_commands:
                    if alias in info.commands:
                        raise ValueError(f"Команда '{alias}' повторяется в модуле")
                    info.commands[alias] = value
                    owner = real_cmds.get(alias)
                    if owner and owner != info.module_name:
                        self.note('warn', f"команда «{alias}» в настоящем ядре занята модулем «{owner}» — там загрузка этого модуля упадёт")
            elif ftype == 'watcher':
                info.watchers.append(value)
            elif ftype == 'telegram_event':
                info.telegram_events.append(value)
            elif ftype == 'callback':
                info.callbacks.append(value)
            elif ftype == 'on_error':
                info.on_error_handlers.append(value)
            elif ftype == 'on_account_switch':
                info.on_account_switch_handlers.append(value)
            elif ftype == 'on_start':
                info.on_start_handlers.append(value)
            elif ftype == 'on_load':
                info.on_load_handlers.append(value)
            elif ftype == 'cycle':
                info.cycle_handlers.append((value, value._frx_cycle))
            for meta in getattr(value, '_frx_web_pages', []):
                info.web_pages.append({'title': meta['title'], 'icon': meta.get('icon'), 'func': value})
            bt = getattr(value, '_frx_bot_type', None)
            if bt == 'command':
                for alias in value._frx_bot_commands:
                    if alias in info.bot_commands or alias in reserved:
                        raise ValueError(f"Команда бота '/{alias}' уже занята" + (' встроенной командой бота' if alias in reserved else ' в модуле'))
                    info.bot_commands[alias] = value
                    owner = real_bot_cmds.get(alias)
                    if owner and owner != info.module_name:
                        self.note('warn', f"команда бота «/{alias}» в настоящем ядре занята модулем «{owner}»")
            elif bt == 'inline_function':
                self.note('warn', f"bot.inline_function «{value.__name__}» не эмулируется в Sandbox")
            elif bt == 'watcher':
                info.bot_watchers.append(value)
            elif bt == 'telegram_event':
                info.bot_telegram_events.append(value)
            elif bt == 'on_error':
                info.bot_on_error_handlers.append(value)
            elif bt == 'on_start':
                info.bot_on_start_handlers.append(value)
            elif bt == 'on_load':
                info.bot_on_load_handlers.append(value)
            elif bt == 'callback':
                info.bot_callbacks.append(value)
            elif bt == 'start_link':
                info.bot_start_links.append(value)
            elif bt == 'cycle':
                info.bot_cycle_handlers.append((value, value._frx_bot_cycle))

    async def _cycle(self, module_name, func, interval, bot=False):
        name = func.__name__
        try:
            seconds = parse_interval(interval)
        except Exception as e:                                      # noqa: BLE001
            self.note('error', f"cycle {name}: {e}", function=name, module=module_name, frames=[], handlers=0)
            return
        st = self.cycle_state[name] = {'interval': interval, 'seconds': seconds, 'due': None, 'runs': 0, 'bot': bot}
        while True:
            try:
                st['due'] = self.clock.now + seconds
                self.touch_state()
                await self.clock.sleep(seconds)
                st['runs'] += 1
                await self.call(module_name, func, bot=bot)
            except asyncio.CancelledError:
                break

    # ------------------------------------------------------------------ #
    # события
    # ------------------------------------------------------------------ #

    def _dedup_seen(self, func, chat_id, mid):
        key = (id(func), chat_id, mid)
        if key in self._dedup:
            return True
        self._dedup[key] = 1
        return False

    async def dispatch_user_message(self, event: SbEvent):
        info = self.module
        if info is None:
            return
        token = current_event.set(event)
        try:
            text = event.raw_text or ''
            if event.out and text.startswith(self.prefix):
                rest = text[len(self.prefix):].strip()
                if rest:
                    cmd = rest.split(maxsplit=1)[0].lower()
                    func = info.commands.get(cmd)
                    if func is not None:
                        await self.call(info.module_name, func, event)
                    else:
                        self.note('info', f"команды «{cmd}» в этом модуле нет (в настоящем ядре её обработал бы другой модуль)")
            for func in list(info.watchers):
                await self.call(info.module_name, func, event)
            for func in list(info.bot_watchers):
                watch = getattr(func, '_frx_bot_watch', 'bot')
                if watch not in ('userbot', 'all'):
                    continue
                access = getattr(func, '_frx_bot_access', 'self')
                if not self.engine._bot_access_ok(access, event.sender_id):
                    continue
                if watch == 'all' and self._dedup_seen(func, event.chat_id, event.message.id):
                    continue
                event.origin = 'userbot'
                await self.call(info.module_name, func, event, bot=True)
        finally:
            current_event.reset(token)
        for w in list(self.waiters):
            if w.matches(event):
                await w._on_event(event)

    async def dispatch_bot_message(self, m: SbMessage):
        from .bot_events import BotMessageEvent
        info = self.module
        if info is None:
            return
        raw = SbBotMessage(m)
        event = BotMessageEvent(raw, self.engine, origin='bot')
        user_id = m.sender_id
        text = event.raw_text
        token = current_event.set(event)
        try:
            payload = text[len('/start'):].strip() if text.startswith('/start') else ''
            link = next((f for f in info.bot_start_links if payload and payload.startswith(f._frx_bot_start_prefix)), None)
            if link is not None:                                 # t.me/<бот>?start=<prefix>… — @bot.start_link модуля
                await self.call(info.module_name, link, event, payload[len(link._frx_bot_start_prefix):], bot=True)
                return
            if text.startswith('/'):
                cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
                func = info.bot_commands.get(cmd)
                if func is not None:
                    ok = self.engine._bot_extended_access_ok(
                        getattr(func, '_frx_bot_access', 'self'), getattr(func, '_frx_bot_for_user', None),
                        getattr(func, '_frx_bot_for_chat', None), getattr(func, '_frx_bot_black_list', None),
                        getattr(func, '_frx_bot_black_chats', None), user_id, event.chat_id)
                    if ok:
                        await self.call(info.module_name, func, event, bot=True)
                    else:
                        self.note('info', f"/{cmd}: доступ запрещён (access / for_user / for_chat / black_list)")
                else:
                    self.note('info', f"команды бота «/{cmd}» в этом модуле нет")
            for func in list(info.bot_watchers):
                watch = getattr(func, '_frx_bot_watch', 'bot')
                if watch not in ('bot', 'all'):
                    continue
                if not self.engine._bot_access_ok(getattr(func, '_frx_bot_access', 'self'), user_id):
                    self.note('info', f"{func.__name__}: вотчер бота пропущен — access='self', отправитель не владелец")
                    continue
                if watch == 'all' and self._dedup_seen(func, event.chat_id, m.id):
                    continue
                await self.call(info.module_name, func, event, bot=True)
        finally:
            current_event.reset(token)

    async def incoming(self, chat_key, sender_id, text='', reply_to=None, media=None):
        chat = self.chats.get(chat_key)
        if chat is None:
            raise ValueError('Чат не найден')
        sender = self.users.get(sender_id)
        if sender is None:
            raise ValueError('Участник не найден')
        if self.module is None:
            raise RuntimeError('Модуль не загружен — исправьте ошибку и перезапустите песочницу')
        text = (text or '')[:4096]
        out = sender.id == self.owner.id and chat.side in ('user', 'both')
        m = SbMessage(self, chat, sender, text, out=out, reply_to=reply_to, media=media)
        self.note('event', f"сообщение от {sender.name} в «{chat.title}»: {preview(text or (media or {}).get('name') or '', 120)}",
                  chat=chat.key, mid=m.id)
        if chat.side in ('user', 'both'):
            self.spawn(self.dispatch_user_message(SbEvent(self, m)), f'сообщение #{m.id} (юзербот)', depth=0)
        if chat.side in ('bot', 'both'):
            self.spawn(self.dispatch_bot_message(m), f'сообщение #{m.id} (бот)')
        await self.settle()
        return m

    async def fire_chat_action(self, chat_key, kind, user_id=None, actor_id=None, title=None):
        chat = self.chats.get(chat_key)
        if chat is None:
            raise ValueError('Чат не найден')
        if kind not in SbChatAction.KINDS:
            raise ValueError('Неизвестный вид события')
        user = self.users.get(user_id) if user_id else None
        actor = self.users.get(actor_id) if actor_id else None
        ev = SbChatAction(self, chat, kind, user, actor, title)
        self.note('event', f"служебное событие Telegram: {kind} · «{chat.title}»" + (f" · {user.name}" if user else ''), chat=chat.key)
        info = self.module
        if info is None:
            raise RuntimeError('Модуль не загружен')

        async def run():
            for func in list(info.telegram_events):
                await self.call(info.module_name, func, ev)
        self.spawn(run(), f'telegram_event {kind}')
        await self.settle()

    async def fire_bot_member(self, chat_key, user_id, old_status, new_status, is_bot_event=False):
        from .bot_events import BotChatMemberEvent
        chat = self.chats.get(chat_key)
        user = self.users.get(user_id) if not is_bot_event else self.bot_user
        if chat is None or user is None:
            raise ValueError('Чат или участник не найдены')
        upd = types.SimpleNamespace(chat=_ag_chat(chat), from_user=_ag_user(self.owner), date=self.dt_now(),
                                    old_chat_member=types.SimpleNamespace(status=old_status, user=_ag_user(user)),
                                    new_chat_member=types.SimpleNamespace(status=new_status, user=_ag_user(user)))
        ev = BotChatMemberEvent(upd, self.engine, is_bot_event=is_bot_event)
        self.note('event', f"событие бота: {user.name} {old_status} → {new_status} · «{chat.title}»", chat=chat.key)
        info = self.module
        if info is None:
            raise RuntimeError('Модуль не загружен')

        async def run():
            for func in list(info.bot_telegram_events):
                await self.call(info.module_name, func, ev, bot=True)
        self.spawn(run(), 'bot telegram_event')
        await self.settle()

    async def fire_callback(self, chat_key, sender_id, data, message_id=None):
        chat = self.chats.get(chat_key)
        sender = self.users.get(sender_id)
        if chat is None or sender is None:
            raise ValueError('Чат или участник не найдены')
        msg = self.find_message(chat, message_id) if message_id else None
        info = self.module
        bot_msg = msg is not None and msg.sender is not None and msg.sender.id == self.bot_user.id
        if info is not None and (bot_msg or chat.side == 'bot'):
            cbs = sorted(info.bot_callbacks, key=lambda f: len(getattr(f, '_frx_bot_click_prefix', '') or ''), reverse=True)
            func = next((f for f in cbs if str(data).startswith(getattr(f, '_frx_bot_click_prefix', '') or '')), None)
            if func is not None:                                # кнопка под сообщением бота — @bot.callback модуля
                self.note('event', f"кнопка бота data={data!r} от {sender.name}", chat=chat.key)
                click = SbBotClick(self, chat, sender, str(data), msg)
                if getattr(func, '_frx_bot_access', 'public') == 'self' and sender.id != self.owner.id:
                    await click.answer("Эта кнопка не для вас", show_alert=True)
                    return

                async def run_bot_click():
                    await self.call(info.module_name, func, click, bot=True)
                    await click.answer()
                self.spawn(run_bot_click(), 'bot.callback')
                await self.settle()
                return
        ev = SbCallbackEvent(self, chat, sender, str(data).encode('utf-8'), msg)
        self.note('event', f"callback-кнопка data={data!r} от {sender.name}", chat=chat.key)
        info = self.module
        if info is None:
            raise RuntimeError('Модуль не загружен')

        async def run():
            for func in list(info.callbacks):
                await self.call(info.module_name, func, ev)
        self.spawn(run(), 'callback')
        await self.settle()

    async def fire_error(self, text, function='my_function', bot=False, exc_type=None):
        if self.module is None:
            raise RuntimeError('Модуль не загружен')
        text = text if not exc_type else f"{exc_type}: {text}"
        ctx = {'module': self.module.module_name, 'function': function or 'my_function'}
        self.note('event', f"ошибка (событие on_error): {preview(text, 140)}", context=ctx)
        self.spawn(self.run_error_handlers(text, ctx, bot=bot), 'on_error')
        await self.settle()

    async def fire_loadmod(self, spec):
        from .engine import ModuleInfo
        if self.module is None:
            raise RuntimeError('Модуль не загружен')
        name = re.sub(r'[^\w\-]', '_', str(spec.get('module_name') or 'other_module'))[:64] or 'other_module'
        info = ModuleInfo(module_name=name, name=str(spec.get('name') or name), version=str(spec.get('version') or '1.0'),
                          author=str(spec.get('author') or 'unknown'), description=str(spec.get('description') or ''),
                          path=str(MODULES_DIR / f'{name}.py'), source='', namespace={})
        self.note('event', f"загружен другой модуль «{info.name}» v{info.version} (on_load)", module=name)
        owner = self.module

        async def run():
            for func in list(owner.on_load_handlers):
                await self.call(owner.module_name, func, info)
            for func in list(owner.bot_on_load_handlers):
                await self.call(owner.module_name, func, info, bot=True)
        self.spawn(run(), 'on_load')
        await self.settle()

    async def fire_account_switch(self, number):
        if self.module is None:
            return
        self.note('event', f"смена аккаунта → {number} (on_account_switch)")

        async def run():
            for func in list(self.module.on_account_switch_handlers):
                await self.call(self.module.module_name, func, number)
        self.spawn(run(), 'on_account_switch')
        await self.settle()

    async def fire_start(self):
        if self.module is None:
            raise RuntimeError('Модуль не загружен')
        self.note('event', 'повторный запуск (on_start)')
        self.spawn(self._start_handlers(), 'on_start')
        await self.settle()

    # ------------------------------------------------------------------ #
    # формы
    # ------------------------------------------------------------------ #

    async def click(self, form_id, data, user_id):
        form = self.forms.get(form_id)
        if form is None:
            raise ValueError('Форма закрыта или не найдена')
        user = self.users.get(user_id) or self.owner
        if not form.check_access(user.id):
            self.toast(f'Нет доступа: форму открыли не для «{user.name}» (for_user / black_list)', 'error')
            self.note('info', f"клик по кнопке {data!r} от {user.name} отклонён: нет доступа (for_user={form.for_user!r})")
            return
        self.note('event', f"клик по кнопке {data!r} · {user.name}", form=form_id)
        self.spawn(form._dispatch(SbClick(self, form, data, user)), f'клик {data}')
        await self.settle()

    async def send_input(self, form_id, data, value, user_id):
        form = self.forms.get(form_id)
        if form is None:
            raise ValueError('Форма закрыта или не найдена')
        user = self.users.get(user_id) or self.owner
        if not form.check_access(user.id):
            self.toast(f'Нет доступа: ввод значения недоступен «{user.name}»', 'error')
            return
        self.note('event', f"ввод значения {data!r} = {preview(value, 80)} · {user.name}", form=form_id)
        self.spawn(form._dispatch_input(InlineInput(data, value, user, form.chat_id, form)), f'ввод {data}')
        await self.settle()

    # ------------------------------------------------------------------ #
    # время, данные, конфиг, страницы
    # ------------------------------------------------------------------ #

    async def advance(self, seconds):
        v0 = self.clock.now
        r = await self.clock.advance(seconds)
        self.note('time', f"время +{_fmt_secs(r['now'] - v0)} → {_fmt_dt(r['now'])}; сработало таймеров: {r['fired']}"
                  + (' (обрезано: слишком много срабатываний)' if r['truncated'] else ''))
        self.touch_state()
        await self.settle(0.6)
        return r

    async def set_time(self, ts):
        r = await self.clock.set_time(ts)
        self.note('time', f"время установлено: {_fmt_dt(self.clock.now)}; сработало таймеров: {r['fired']}")
        self.touch_state()
        await self.settle(0.6)
        return r

    def play(self, speed):
        self.clock.play(speed)
        self.note('time', f"часы идут ×{speed:g}")
        self.touch_state()

    def pause(self):
        self.clock.pause()
        self.note('time', 'часы остановлены')
        self.touch_state()

    def kv_set(self, store, key, value):
        st = self.db_store if store == 'db' else self.cache_store
        st.raw_set(key, value)
        self.note(store, f"{store}.set({key!r}) = {preview(value, 120)} · из интерфейса", store=store, op='ui-set', key=key, internal=False, ui=True)
        self.touch_state()

    def kv_delete(self, store, key):
        st = self.db_store if store == 'db' else self.cache_store
        ok = st.raw_delete(key)
        self.note(store, f"{store}.delete({key!r}) · из интерфейса", store=store, op='ui-delete', key=key, internal=False, ui=True)
        self.touch_state()
        return ok

    def import_db(self, data: dict, prefix: str):
        n = 0
        for k, v in data.items():
            if k.startswith(prefix):
                self.db_store.raw_set(k, copy_json(v))
                n += 1
        self.note('db', f"импортировано из настоящей БД: {n} ключей (копия, настоящие данные не изменяются)")
        self.touch_state()
        return n

    async def config_edit(self, key, operation, value=None):
        mtoken = current_module.set(self.module.module_name if self.module else self.name)
        try:
            return await self.engine.edit_config(key, operation, value)
        finally:
            current_module.reset(mtoken)

    def config_rows(self):
        cfg = self.db_store.mem.get(f"{self.module.module_name if self.module else self.name}:__config__", {}) or {}
        out = []
        for key, e in cfg.items():
            out.append({'key': key, 'value': e.get('value'), 'default': e.get('default'), 'type': e.get('type'),
                        'description': e.get('description', ''), 'options': e.get('options'), 'valid': e.get('valid')})
        return out

    async def render_page(self, index, values, action):
        from .web import ui as ui_mod
        info = self.module
        if info is None or not (0 <= index < len(info.web_pages)):
            raise ValueError('Страница не найдена')
        entry = info.web_pages[index]
        state = self.page_states.setdefault(index, {})
        token = current_module.set(info.module_name)
        ftoken = sb_fn.set(getattr(entry['func'], '__name__', 'page'))
        try:
            tree, toasts = await ui_mod.render(entry['func'], state, values or {}, action)
        except Exception as e:                                      # noqa: BLE001
            self.note('error', f"{type(e).__name__}: {e}", frames=self._frames(e), function=entry['func'].__name__,
                      module=info.module_name, bot=False, handlers=0)
            tree = [{'t': 'title', 'text': 'Ошибка страницы'}, {'t': 'code', 'text': f"{type(e).__name__}: {e}"}]
            toasts = []
        finally:
            sb_fn.reset(ftoken)
            current_module.reset(token)
        return {'title': entry['title'], 'tree': tree, 'toasts': toasts}

    # ------------------------------------------------------------------ #
    # снимки для интерфейса
    # ------------------------------------------------------------------ #

    def handlers(self):
        info = self.module
        if info is None:
            return {}

        def fn(f, **extra):
            doc = (f.__doc__ or '').strip().split('\n')[0][:140]
            return {'name': f.__name__, 'doc': doc, 'line': getattr(getattr(f, '__code__', None), 'co_firstlineno', 0), **extra}

        return {
            'commands': [fn(f, alias=a) for a, f in info.commands.items()],
            'watchers': [fn(f) for f in info.watchers],
            'telegram_events': [fn(f) for f in info.telegram_events],
            'callbacks': [fn(f) for f in info.callbacks],
            'on_error': [fn(f) for f in info.on_error_handlers],
            'on_load': [fn(f) for f in info.on_load_handlers],
            'on_start': [fn(f) for f in info.on_start_handlers],
            'on_account_switch': [fn(f) for f in info.on_account_switch_handlers],
            'cycles': [fn(f, interval=iv) for f, iv in info.cycle_handlers],
            'bot_commands': [fn(f, alias=a, access=getattr(f, '_frx_bot_access', 'self')) for a, f in info.bot_commands.items()],
            'bot_watchers': [fn(f, watch=getattr(f, '_frx_bot_watch', 'bot'), access=getattr(f, '_frx_bot_access', 'self')) for f in info.bot_watchers],
            'bot_telegram_events': [fn(f) for f in info.bot_telegram_events],
            'bot_on_error': [fn(f) for f in info.bot_on_error_handlers],
            'bot_on_load': [fn(f) for f in info.bot_on_load_handlers],
            'bot_on_start': [fn(f) for f in info.bot_on_start_handlers],
            'bot_cycles': [fn(f, interval=iv) for f, iv in info.bot_cycle_handlers],
            'pages': [{'title': p['title'], 'icon': p.get('icon'), 'name': p['func'].__name__} for p in info.web_pages],
        }

    def forms_rows(self):
        out = []
        for f in self.forms.values():
            rows = []
            for page in f._pages:
                for row in page:
                    for b in row:
                        rows.append({'text': getattr(b, 'text', None) or (b[0] if isinstance(b, (list, tuple)) else str(b)),
                                     'data': getattr(b, 'data', None) or (b[1] if isinstance(b, (list, tuple)) else ''),
                                     'input': isinstance(b, InputButton)})
            out.append({'id': f.form_id, 'chat': f.chat.key, 'for_user': f.for_user, 'timeout': f.timeout, 'buttons': rows})
        return out

    def state(self):
        info = self.module
        hung = self.runtime is not None and self.runtime.stalled()
        return {
            'rev': self.state_rev, 'status': self.status, 'status_text': self.status_text,
            'module': ({'name': info.name, 'module_name': info.module_name, 'version': info.version, 'author': info.author,
                        'description': info.description} if info else None),
            'handlers': self.handlers(), 'cycles': {k: dict(v) for k, v in self.cycle_state.items()},
            'clock': {'now': self.clock.now, 'origin': self.clock.origin, 'playing': self.clock.playing, 'speed': self.clock.speed,
                      'timers': self.clock.pending(), 'fired': self.clock.fired},
            'settings': {'prefix': self.prefix, 'owner_id': self.owner.id, 'bot_enabled': self.bot_enabled, 'bot': self.bot_user.describe()},
            'users': [u.describe() for u in self.users.values() if u.id != self.bot_user.id],
            'chats': [c.describe() for c in self.chats.values()],
            'running': len([t for t in self.tasks if not t.done()]), 'waiters': len(self.waiters), 'forms': self.forms_rows(),
            'static': self.static, 'config': self.config_rows(), 'stalled': hung,
            'db': self.db_store.rows(), 'cache': self.cache_store.rows(),
            'files': [{'id': o['id'], 'name': o['name'], 'size': o['size'], 'type': o['type']} for o in self.files.registry.values()],
        }

    def state_light(self):
        return {'rev': self.state_rev, 'status': self.status, 'status_text': self.status_text, 'module': None, 'handlers': {}, 'cycles': {},
                'clock': {'now': self.clock.now, 'origin': self.clock.origin, 'playing': False, 'speed': self.clock.speed, 'timers': [], 'fired': 0},
                'settings': {'prefix': self.prefix, 'owner_id': self.owner.id, 'bot_enabled': self.bot_enabled, 'bot': self.bot_user.describe()},
                'users': [u.describe() for u in self.users.values() if u.id != self.bot_user.id],
                'chats': [c.describe() for c in self.chats.values()], 'running': 0, 'waiters': 0, 'forms': [], 'static': self.static,
                'config': [], 'stalled': False, 'db': [], 'cache': [], 'files': []}

    async def state_async(self):
        st = self.state()
        self.last_state = st
        return st

    def shutdown(self):
        try:
            self.files.close()
        except Exception:
            pass


def _fmt_secs(sec):
    sec = int(round(sec))
    parts = []
    for unit, n in (('д', 86400), ('ч', 3600), ('мин', 60), ('с', 1)):
        if sec >= n:
            parts.append(f'{sec // n} {unit}')
            sec %= n
    return ' '.join(parts) or '0 с'


def _fmt_dt(ts):
    return _real_datetime.datetime.fromtimestamp(ts).strftime('%d.%m.%Y %H:%M:%S')


def copy_json(value):
    try:
        return json.loads(json.dumps(value))
    except Exception:
        return value


# --------------------------------------------------------------------------- #
# хаб: сессии Sandbox, потоки, watchdog
# --------------------------------------------------------------------------- #

MAX_SESSIONS = 6
IDLE_SECONDS = 3600


class SandboxHub:
    def __init__(self):
        self.sessions = {}
        self.used = {}

    def get(self, key, core_info=None, create=True):
        s = self.sessions.get(key)
        if s is None and create:
            self._purge()
            s = self.sessions[key] = Session(key, core_info)
        if s is not None:
            self.used[key] = _real_time.monotonic()
        return s

    def _purge(self):
        now = _real_time.monotonic()
        for key in [k for k, t in self.used.items() if now - t > IDLE_SECONDS]:
            self.drop(key)
        while len(self.sessions) >= MAX_SESSIONS:
            oldest = min(self.used, key=self.used.get)
            self.drop(oldest)

    def drop(self, key):
        s = self.sessions.pop(key, None)
        self.used.pop(key, None)
        if s is not None:
            if s.runtime is not None:
                s.runtime.kill() if s.runtime.stalled() else s.runtime.stop()
            s.shutdown()

    def shutdown_all(self):
        for key in list(self.sessions):
            self.drop(key)

    # ---- потоки ---- #
    def health(self, s: Session):
        """Если поток завис без await — остановить его и сообщить. Вызывается при каждом запросе интерфейса."""
        rt = s.runtime
        if rt is None:
            return
        if rt.stalled():
            rt.kill()
            s.status, s.status_text = 'stalled', ('Модуль завис: обработчик не отдаёт управление (бесконечный цикл без await?). '
                                                  'Поток Sandbox остановлен — исправьте код и перезапустите песочницу.')
            s.note('error', 'Поток Sandbox остановлен: модуль не отдавал управление дольше '
                            f'{STALL_SECONDS:g} с (цикл без await?)', frames=[], function=sb_fn.get() or '', module=s.name, bot=False, handlers=0)
            s.touch_state()
        elif rt.dead and s.status in ('running', 'idle'):
            s.status, s.status_text = 'dead', 'Поток Sandbox остановлен.'
            s.touch_state()

    def ensure_runtime(self, s: Session):
        if s.runtime is None or not s.runtime.alive():
            s.runtime = SandboxRuntime(re.sub(r'\W', '_', s.name)[:24])
            s.status = 'idle'
        return s.runtime

    async def _watch(self, s: Session, coro, timeout):
        """Ждёт результат из потока песочницы, раз в полсекунды проверяя, не завис ли модуль (тогда — сразу ошибка)."""
        rt = s.runtime
        fut = asyncio.wrap_future(rt.submit(coro))
        waited = 0.0
        while True:
            done, _ = await asyncio.wait({fut}, timeout=0.4)
            if done:
                return fut.result()
            waited += 0.4
            self.health(s)
            if s.status == 'stalled':
                fut.cancel()
                raise RuntimeError(s.status_text)
            if waited >= timeout:
                fut.cancel()
                raise asyncio.TimeoutError()

    async def run(self, s: Session, coro_factory, timeout=25):
        self.health(s)
        rt = s.runtime
        if rt is None or not rt.alive():
            raise RuntimeError(s.status_text or 'Песочница не запущена — нажмите «запустить»')
        try:
            return await self._watch(s, coro_factory(s), timeout)
        except asyncio.TimeoutError:
            self.health(s)
            raise RuntimeError('Песочница не ответила вовремя. Если модуль завис, она остановится сама через несколько секунд.')

    async def state(self, s: Session):
        """Состояние для интерфейса: считается внутри потока песочницы (чтобы не читать словари во время записи)."""
        self.health(s)
        rt = s.runtime
        if rt is not None and rt.alive() and not rt.stalled():
            try:
                return await self._watch(s, s.state_async(), 3)
            except Exception:                                       # noqa: BLE001
                pass
        st = dict(s.last_state or s.state_light())
        st['status'], st['status_text'], st['stalled'] = s.status, s.status_text, s.status == 'stalled'
        return st

    # ---- запуск / перезапуск ---- #
    async def start(self, s: Session, source, restart=False):
        """Загрузить исходник. restart=True — как перезапуск процесса: cache очищается, db возвращается к записанному на диск."""
        self.ensure_runtime(s)

        async def go(sess):
            if restart:
                await sess.unload()
                sess.db_store.restart()
                sess.cache_store.restart()
                sess.note('event', 'перезапуск: cache очищен, db восстановлена из записанного на диск')
            sess.clock.pause()
            return await sess.load(source)

        s.status, s.status_text = 'idle', ''
        try:
            info = await self._watch(s, go(s), 30)
            self.health(s)
            return {'ok': True, 'name': info.name, 'version': info.version}
        except (asyncio.TimeoutError, RuntimeError) as e:
            self.health(s)
            msg = s.status_text if s.status == 'stalled' else (str(e) or 'Модуль не закончил загрузку (завис?)')
            return {'ok': False, 'error': {'type': 'Timeout', 'msg': msg, 'frames': []}}
        except SyntaxError as e:
            s.module = None
            s.status, s.status_text = 'error', f'Синтаксическая ошибка: строка {e.lineno}: {e.msg}'
            s.note('error', f"SyntaxError: {e.msg}", frames=[{'line': e.lineno or 1, 'func': '<module>', 'code': (e.text or '').strip()}],
                   function='<загрузка>', module=s.name, bot=False, handlers=0)
            s.touch_state()
            return {'ok': False, 'error': {'type': 'SyntaxError', 'msg': e.msg, 'line': e.lineno, 'frames': []}}
        except Exception as e:                                      # noqa: BLE001
            s.module = None
            s.status, s.status_text = 'error', f'{type(e).__name__}: {e}'
            frames = s._frames(e)
            s.note('error', f"{type(e).__name__}: {e}", frames=frames, function='<загрузка>', module=s.name, bot=False, handlers=0)
            s.touch_state()
            return {'ok': False, 'error': {'type': type(e).__name__, 'msg': str(e), 'frames': frames,
                                           'line': frames[-1]['line'] if frames else None}}

    def stop(self, s: Session):
        if s.runtime is not None:
            s.runtime.kill()
        s.status, s.status_text = 'dead', 'Песочница остановлена.'
        s.note('info', 'песочница остановлена')
        s.touch_state()

    def reset(self, key):
        self.drop(key)
