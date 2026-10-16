"""
core/emulate.py — Emulate: кнопка `> emulate` в карточке модуля на сайте;
настройки — в конфигах модуля Web, `.config web_panel`.

Emulate — автоматическая «отрисовка» ЛЮБОГО модуля на сайте `.web`, даже если автор
не писал для него страницу (`import web`) и не думал ни про какой сайт.

Идея: команда модуля — это обычная async-функция, которая получает `event` и отвечает через
`engine.answer/reply/noreply/send_message`, рисует формы через `engine.create_inlineform(...)` и ждёт
ввод через `engine.wait_message(...)`. Сайт запускает её с ВИРТУАЛЬНЫМ событием (EmuEvent) внутри
сессии (EmuSession, ContextVar `current_emulation`), и все три канала вывода уходят на сайт, а не в Telegram:

  * команда без аргументов ..................... кнопка «запустить»;
  * команда с аргументом (engine.get_args() /
    event.raw_text в теле, либо <аргумент> в докстринге) поле ввода + «запустить»;
  * ответ команды (answer/reply/noreply/edit) .. сообщение в ленте (HTML Telegram → безопасные узлы);
  * инлайн-форма (create_inlineform) ........... текст + кнопки; клики идут в form.on_click(...),
    InputButton — поле ввода, уходит в form.on_input(...); стационарный бот для этого НЕ нужен;
  * engine.wait_message(...) в чате команды .... сайт показывает поле «ответить», текст уходит в Waiter.

Что уходит на сайт, а что — по-настоящему: всё, что адресовано «своему» чату (чату команды = «Избранное»
владельца, `'me'`), — на сайт; отправка в ЧУЖИЕ чаты (send_message(other_id, ...)) выполняется по-настоящему.
Эмулировать нечего там, где команда ждёт реплай на файл/сообщение (`event.get_reply_message()` — None)
или чужие сообщения из других чатов — такие команды покажут свою обычную ошибку.

Всё, что попадает в браузер, — текст и разобранные узлы (b/i/u/s/code/pre/a/spoiler/quote): HTML модуля
не исполняется (см. html_to_nodes).
"""

import ast
import asyncio
import html as _html
import mimetypes
import os
import re
import shutil
import time
import types
import uuid
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from .context import current_emulation, current_event, current_module
from .html_utils import safe_html
from .paths import DATA_DIR
from .inline_forms import InlineClick, InlineInput, InputButton, normalize_button_rows, parse_button, normalize_style
from .waiters import Waiter

MAX_ENTRIES_DEFAULT = 200
MAX_TEXT = 60000
UPLOAD_DIR = DATA_DIR / 'emulate_uploads'       # файлы, которые пользователь прикрепил к команде в Emulate
UPLOAD_LIMIT_DEFAULT = 25 * 1024 * 1024
MAX_UPLOADS = 20
DANGEROUS_COMMANDS = {'exit', 'restart', 'rs', 'update', 'upd', 'unloadmod', 'ulm', 'restorebackup'}


# ---------------------------------------------------------------------------
# Текст: HTML/Markdown Telegram → безопасные узлы
# ---------------------------------------------------------------------------

_TAGS = {
    'b': 'b', 'strong': 'b', 'i': 'i', 'em': 'i', 'u': 'u', 'ins': 'u',
    's': 's', 'strike': 's', 'del': 's', 'code': 'code', 'pre': 'pre',
    'a': 'a', 'blockquote': 'q', 'tg-spoiler': 'sp',
}
_SAFE_HREF = re.compile(r'^(https?://|tg://|mailto:)', re.I)


class _NodeParser(HTMLParser):
    """Терпимый разбор HTML Telegram: неизвестные теги «прозрачны», незакрытые — закрываются сами."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = {'t': None, 'c': []}
        self.stack = [('#root', self.root)]

    def _top(self):
        return self.stack[-1][1]

    def handle_starttag(self, tag, attrs):
        if tag == 'br':
            self._top()['c'].append('\n')
            return
        attrs = dict(attrs)
        kind = _TAGS.get(tag)
        if tag == 'span' and 'tg-spoiler' in (attrs.get('class') or ''):
            kind = 'sp'
        node = {'t': kind, 'c': []}
        if kind == 'a':
            href = (attrs.get('href') or '').strip()
            if _SAFE_HREF.match(href):
                node['href'] = href
        if len(self.stack) < 24:
            self._top()['c'].append(node)
            self.stack.append((tag, node))

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        if data:
            self._top()['c'].append(data)


def html_to_nodes(text: str) -> list:
    """HTML Telegram → список узлов: str | {'t': b|i|u|s|code|pre|a|q|sp|None, 'c': [...], 'href'?}."""
    parser = _NodeParser()
    try:
        parser.feed(str(text or '')[:MAX_TEXT])
        parser.close()
    except Exception:
        return [str(text or '')[:MAX_TEXT]]
    return parser.root['c']


def nodes_to_text(nodes) -> str:
    out = []
    for n in nodes:
        out.append(n if isinstance(n, str) else nodes_to_text(n.get('c', [])))
    return ''.join(out)


def md_to_html(text: str) -> str:
    """Маленький Markdown Telegram/Telethon (по умолчанию у client.send_message) → HTML."""
    t = _html.escape(str(text or ''), quote=False)
    t = re.sub(r'```(?:\w*\n)?(.*?)```', lambda m: f'<pre>{m.group(1)}</pre>', t, flags=re.S)
    t = re.sub(r'`([^`\n]+)`', r'<code>\1</code>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', t, flags=re.S)
    t = re.sub(r'__(.+?)__', r'<i>\1</i>', t, flags=re.S)
    t = re.sub(r'~~(.+?)~~', r'<s>\1</s>', t, flags=re.S)
    t = re.sub(r'\[([^\]\n]+)\]\(([^)\s]+)\)', r'<a href="\2">\1</a>', t)
    return t


def _to_nodes(text, parse_mode) -> list:
    mode = parse_mode.lower() if isinstance(parse_mode, str) else parse_mode
    if mode == 'html':
        return html_to_nodes(text)
    if mode in (None, '', 'md', 'markdown'):
        return html_to_nodes(md_to_html(text))
    return [str(text or '')[:MAX_TEXT]]                  # незнакомый режим — как есть, без разметки


def serialize_buttons(buttons, styles=None) -> list:
    """buttons формы (любой формат create_inlineform) → [[{'text','data','style'?} | {'text','input','hint'}], ...]."""
    out = []
    for row in normalize_button_rows(buttons or []):
        cells = []
        for b in row:
            try:
                p = parse_button(b, styles)
            except ValueError:
                p = None
            if p is None:
                continue
            if p['kind'] == 'input':
                cell = {'text': p['text'], 'input': p['data'], 'hint': str(p.get('hint') or '')}
            else:
                cell = {'text': p['text'], 'data': p['data']}
            if p.get('style'):
                cell['style'] = p['style']
            cells.append(cell)
        if cells:
            out.append(cells)
    return out


# ---------------------------------------------------------------------------
# Виртуальное сообщение / событие
# ---------------------------------------------------------------------------

class EmuFile:
    """Аналог telethon `message.file` (name / size / mime_type / ext) для файла, прикреплённого пользователем в Emulate."""

    def __init__(self, upload):
        self.id = upload['id']
        self.name = upload['name']
        self.size = upload['size']
        self.mime_type = upload['mime']
        self.ext = Path(upload['name']).suffix
        self.width = self.height = self.duration = None
        self.title = self.performer = None
        self._path = upload['path']

    def __bool__(self):
        return True

    def __repr__(self):
        return f"<EmuFile {self.name!r} {self.size} bytes {self.mime_type}>"


class EmuMessage:
    """Аналог telethon Message: то, что модуль получает как `event.message` / результат reply()/answer()."""

    is_private = True
    is_group = False
    is_channel = False
    is_reply = False
    reply_to_msg_id = None
    via_bot_id = None
    media = None
    file = None
    photo = None
    document = None
    buttons = None
    forward = None

    def __init__(self, session, text='', *, out=True, entry=None, reply_to=None):
        self._session = session
        self._entry = entry
        self.id = session._next_message_id()
        self.out = out
        self.chat_id = session.chat_id
        self.sender_id = session.owner_id
        self.peer_id = session.chat_id
        self.date = datetime.now(timezone.utc)
        self.message = self.text = self.raw_text = str(text or '')
        self.reply_to = reply_to

    # -- вывод на сайт ----------------------------------------------------- #

    def _publish(self, text, parse_mode, file):
        nodes = _to_nodes(text, parse_mode)
        self.message = self.text = self.raw_text = nodes_to_text(nodes)
        files = self._session._register_files(file)
        fields = {'nodes': nodes, 'files': files}
        if self._entry is None:
            self._entry = self._session.add_entry(kind='msg', **fields)
        else:
            self._session.update_entry(self._entry, **fields)

    async def edit(self, text=None, *, file=None, parse_mode='md', **kw):
        if text is None:
            text = self.message
        self._publish(text, parse_mode, file)
        return self

    async def reply(self, text='', *, file=None, parse_mode='md', **kw):
        msg = EmuMessage(self._session, '', reply_to=self.id)
        msg._publish(text, parse_mode, file)
        return msg

    respond = reply

    async def delete(self, *a, **kw):
        if self._entry is not None:
            self._session.remove_entry(self._entry)
            self._entry = None
        return True

    async def get_reply_message(self):
        return getattr(self, '_reply_obj', None)

    async def get_sender(self):
        return await self._session.me()

    get_chat = get_sender

    def attach_upload(self, upload):
        """Прикрепляет файл (словарь из EmuSession.save_upload) — как будто в сообщении есть вложение."""
        f = EmuFile(upload)
        self._file = f
        self.file = f
        self.media = f
        mt = (upload['mime'] or '').lower()
        if mt.startswith('image/'):
            self.photo = f
        else:
            self.document = f
        if mt.startswith('video/'):
            self.video = f
        if mt.startswith('audio/'):
            self.audio = f
        return f

    async def download_media(self, file=None, *a, **kw):
        """Копирует прикреплённый файл туда, куда просит модуль (папка / путь); возвращает путь, как telethon."""
        f = getattr(self, '_file', None)
        if f is None:
            return None
        src = Path(f._path)
        if not src.is_file():
            raise FileNotFoundError("Emulate: прикреплённый файл уже удалён")
        if file is None:
            dest = Path(f.name)
        else:
            dest = Path(str(file))
            if str(file).endswith(('/', '\\')) or dest.is_dir():
                dest = dest / f.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest = dest.with_name(f"{dest.stem}_{uuid.uuid4().hex[:6]}{dest.suffix}")
        shutil.copyfile(src, dest)
        return str(dest)

    @property
    def client(self):
        return self._session.core.client

    def __getattr__(self, name):
        raise AttributeError(f"Emulate: у виртуального сообщения нет атрибута «{name}»")


class EmuEvent:
    """Аналог события NewMessage: то, что получает команда (`event`). Делегирует всё EmuMessage."""

    def __init__(self, session, text):
        object.__setattr__(self, 'message', EmuMessage(session, text))
        object.__setattr__(self, '_session', session)

    raw_text = property(lambda self: self.message.raw_text)
    text = property(lambda self: self.message.text)
    original_event = property(lambda self: self)
    emulated = True                       # модуль может проверить: getattr(event, 'emulated', False)

    async def get_input_chat(self):
        return await self._session.me()

    def __getattr__(self, name):
        return getattr(self.message, name)


class EmuClick(InlineClick):
    """Клик по кнопке формы на сайте (InlineClick без aiogram: answer() → всплывающее сообщение сайта)."""

    def __init__(self, session, form, data, user):
        super().__init__(data, user, form.chat_id, form.inline_message_id, None)
        self._session = session

    async def answer(self, text=None, show_alert=False):
        if self._answered:
            return
        self._answered = True
        if text:
            self._session.toast(text, 'error' if show_alert else 'info')


# ---------------------------------------------------------------------------
# Виртуальная инлайн-форма (тот же API, что у InlineForm)
# ---------------------------------------------------------------------------

class EmuForm:
    """
    Замена InlineForm для сайта: on_click / on_input / edit / close — как в README, раздел «Инлайн-формы».
    Пагинацию кнопок движок здесь не делает — сайт показывает все кнопки (прокрутка).
    """

    def __init__(self, session, chat_id, owner_module, for_user=None, black_list=None, timeout=None):
        self._session = session
        self._core = session.core
        self.form_id = uuid.uuid4().hex
        self.inline_message_id = 'emu:' + self.form_id
        self.chat_id = chat_id
        self.owner_module = owner_module
        self.for_user = for_user
        self.black_list = black_list
        self.timeout = timeout
        self.text = None
        self._raw_buttons = None
        self._pages = [[]]
        self.page = 0
        self.styles = {}
        self._click_handlers = []
        self._input_handlers = []
        self._closed = False
        self._entry = None
        self._last_activity = time.monotonic()
        self._idle_task = asyncio.ensure_future(self._idle_watch()) if timeout else None

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

    def _sync(self, text, buttons, closed=False):
        fields = {'form': self.form_id, 'closed': closed,
                  'nodes': html_to_nodes(safe_html(text)) if text is not None else self._entry_nodes(),
                  'rows': [] if closed else serialize_buttons(buttons, self.styles)}
        if self._entry is None:
            self._entry = self._session.add_entry(kind='form', **fields)
        else:
            self._session.update_entry(self._entry, **fields)

    def _entry_nodes(self):
        return self._entry['nodes'] if self._entry else []

    async def edit(self, text, buttons=None, styles=None):
        if self._closed:
            raise RuntimeError(f"Форма '{self.form_id}' уже закрыта (form.close())")
        if styles is not None:
            self.styles = {str(k): normalize_style(v) for k, v in dict(styles).items()}
        self.text = text
        self._raw_buttons = buttons
        self._pages = [normalize_button_rows(buttons or [])]
        self._sync(text, buttons)

    async def _go_to_page(self, new_page):          # пагинации на сайте нет — совместимость
        return None

    async def delete(self):
        """form.delete() — в эмуляции просто закрывает форму."""
        await self.close(remove_buttons=True)

    async def close(self, remove_buttons=True):
        if self._closed:
            return
        self._closed = True
        if self._idle_task is not None:
            self._idle_task.cancel()
        self._session.forms.pop(self.form_id, None)
        if self._entry is not None:
            if remove_buttons:
                self._sync(None, None, closed=True)
            else:
                self._session.update_entry(self._entry, closed=True)

    async def _dispatch(self, click):
        self._last_activity = time.monotonic()
        matched = False
        for data_filter, startswith_filter, func in list(self._click_handlers):
            if data_filter is not None and data_filter != click.data:
                continue
            if startswith_filter is not None and not (click.data or '').startswith(startswith_filter):
                continue
            matched = True
            await self._session.call(self.owner_module, func, click)
        if not matched and not click._answered:
            await click.answer()

    async def _dispatch_input(self, inp):
        self._last_activity = time.monotonic()
        for data_filter, func in list(self._input_handlers):
            if data_filter is not None and data_filter != inp.data:
                continue
            await self._session.call(self.owner_module, func, inp)

    async def _idle_watch(self):
        try:
            while not self._closed:
                await asyncio.sleep(self.timeout)
                if time.monotonic() - self._last_activity >= self.timeout:
                    await self.close()
                    return
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# Виртуальный Waiter (engine.wait_message в чате команды)
# ---------------------------------------------------------------------------

class EmuWaiter(Waiter):
    """Тот же Waiter (await / async for / cancel), но сообщения приходят с сайта (поле «ответить»)."""

    def __init__(self, session, timeout, core):
        self._session = session
        self._timeout = timeout
        self._core = core
        self._queue = asyncio.Queue()
        self._cancelled = asyncio.Event()
        self._registered = False
        self._timeout_resolved = timeout is not None

    def _register(self):
        if not self._registered:
            self._session.waiters.add(self)
            self._registered = True
            self._session.touch()

    def _unregister(self):
        if self._registered:
            self._session.waiters.discard(self)
            self._registered = False
            self._session.touch()

    async def _on_event(self, event):
        await self._queue.put(event)


# ---------------------------------------------------------------------------
# Сессия: одна на модуль, живёт, пока работает сайт
# ---------------------------------------------------------------------------

class EmuSession:
    def __init__(self, core, module_name, max_entries=MAX_ENTRIES_DEFAULT):
        self.core = core
        self.module = module_name
        self.max_entries = max_entries
        self.owner_id = core._owner_id
        self.chat_id = core._owner_id
        self.entries = []
        self.rev = 0
        self._entry_seq = 0
        self._msg_seq = 0
        self.forms = {}
        self.waiters = set()
        self.tasks = set()
        self.files = {}
        self._me = None
        self.uploads = {}                                   # id → {id, name, size, mime, path}
        self.api_stats = {'total': 0, 'write': 0, 'bot': 0, 'errors': 0}

    # -- служебное ----------------------------------------------------------- #

    def _next_message_id(self):
        self._msg_seq += 1
        return self._msg_seq

    def touch(self):
        self.rev += 1

    async def me(self):
        if self._me is None:
            self._me = await self.core.client.get_me()
        return self._me

    def owns_chat(self, chat_id) -> bool:
        return chat_id == 'me' or (chat_id is not None and chat_id == self.chat_id)

    @property
    def running(self) -> int:
        return len([t for t in self.tasks if not t.done()])

    # -- лента --------------------------------------------------------------- #

    def add_entry(self, **fields):
        self._entry_seq += 1
        self.rev += 1
        entry = {'id': self._entry_seq, 'rev': self.rev, 'ts': time.time(), **fields}
        self.entries.append(entry)
        if len(self.entries) > self.max_entries:
            # сперва выбрасываем давно удалённые/тосты, затем самые старые (живые формы не трогаем)
            stale = self.rev - 60
            self.entries = [e for e in self.entries
                            if not ((e.get('removed') or e['kind'] == 'toast') and e['rev'] < stale)]
            drop = len(self.entries) - self.max_entries
            if drop > 0:
                keep = []
                for e in self.entries:
                    live = e.get('kind') == 'form' and e.get('form') in self.forms
                    if drop > 0 and not live:
                        drop -= 1
                        continue
                    keep.append(e)
                self.entries = keep
        return entry

    def update_entry(self, entry, **fields):
        self.rev += 1
        entry.update(fields)
        entry['rev'] = self.rev

    def remove_entry(self, entry):
        self.update_entry(entry, removed=True)

    def toast(self, text, kind='info'):
        entry = self.add_entry(kind='toast', text=str(text), level=kind)
        return entry

    def note(self, text, level='info'):
        return self.add_entry(kind='sys', text=str(text), level=level)

    def snapshot(self, since=0):
        if since <= 0:
            items = [e for e in self.entries if not e.get('removed') and e['kind'] != 'toast']
        else:
            items = [e for e in self.entries if e['rev'] > since]
        return {'rev': self.rev, 'entries': items, 'waiting': len(self.waiters), 'running': self.running,
                'api': dict(self.api_stats), 'uploads': [{'id': u['id'], 'name': u['name'], 'size': u['size'], 'mime': u['mime']} for u in self.uploads.values()]}

    async def clear(self):
        for form in list(self.forms.values()):
            await form.close(remove_buttons=False)
        for e in self.entries:
            if not e.get('removed'):
                self.update_entry(e, removed=True)

    # -- запросы к Telegram (см. core/apilog.py) ---------------- #

    def api_begin(self, proto, name, brief, risk):
        self.api_stats['total'] += 1
        if risk == 'write':
            self.api_stats['write'] += 1
        if proto == 'bot':
            self.api_stats['bot'] += 1
        return self.add_entry(kind='api', proto=proto, name=name, brief=brief, risk=risk, ok=None, ms=None)

    def api_end(self, entry, ms, ok=True, error=None):
        if not ok:
            self.api_stats['errors'] += 1
        self.update_entry(entry, ok=ok, ms=round(ms, 1), error=(error or '')[:200] or None)

    # -- загруженные файлы и reply-объект ---------------------- #

    def _upload_dir(self):
        d = UPLOAD_DIR / re.sub(r'[^\w\-]', '_', self.module)[:64]
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_upload(self, name, data: bytes, limit=UPLOAD_LIMIT_DEFAULT):
        if not data:
            raise ValueError('Файл пустой')
        if len(data) > limit:
            raise ValueError(f'Файл больше лимита ({limit // (1024 * 1024)} МБ) — лимит меняется в конфигах модуля Web')
        if len(self.uploads) >= MAX_UPLOADS:
            raise ValueError(f'Не больше {MAX_UPLOADS} файлов в одной сессии — удалите ненужные')
        safe = re.sub(r'[^\w.\- ]+', '_', Path(str(name or 'file')).name).strip('. ') or 'file'
        safe = safe[:100]
        uid = uuid.uuid4().hex[:12]
        path = self._upload_dir() / f"{uid}_{safe}"
        path.write_bytes(data)
        mime = mimetypes.guess_type(safe)[0] or 'application/octet-stream'
        upload = {'id': uid, 'name': safe, 'size': len(data), 'mime': mime, 'path': str(path)}
        self.uploads[uid] = upload
        self.touch()
        return upload

    def drop_upload(self, uid):
        u = self.uploads.pop(uid, None)
        if u is not None:
            try:
                Path(u['path']).unlink(missing_ok=True)
            except OSError:
                pass
            self.touch()
        return u is not None

    def _cleanup_uploads(self):
        for uid in list(self.uploads):
            self.drop_upload(uid)
        try:
            d = UPLOAD_DIR / re.sub(r'[^\w\-]', '_', self.module)[:64]
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass

    # -- файлы --------------------------------------------------------------- #

    def _register_files(self, file):
        if file is None:
            return []
        items = file if isinstance(file, (list, tuple)) else [file]
        out = []
        for item in items:
            if isinstance(item, (str, Path)) and Path(item).is_file():
                fid = uuid.uuid4().hex[:12]
                path = Path(item)
                self.files[fid] = path
                try:
                    size = path.stat().st_size
                except OSError:
                    size = None
                out.append({'id': fid, 'name': path.name, 'size': size})
            else:
                out.append({'id': None, 'name': 'вложение', 'size': None})
        return out

    # -- выполнение кода модуля в контексте сессии --------------------------- #

    async def call(self, module_name, func, *args):
        """Вызывает функцию модуля как движок (current_module), но ошибку показывает на сайте."""
        mtoken = current_module.set(module_name)
        try:
            return await func(*args)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.note(f"{type(e).__name__}: {e}", 'error')
            try:
                await self.core._dispatch_error(module_name, func, e)
            except Exception:
                pass
            return None
        finally:
            current_module.reset(mtoken)

    def spawn(self, coro_factory, label=''):
        """Запускает работу фоном (HTTP-запрос не блокируется; результат приходит через poll)."""
        async def runner():
            etoken = current_emulation.set(self)
            try:
                timeout = await self.core.emulate_command_timeout()
                if timeout:
                    await asyncio.wait_for(coro_factory(), timeout)
                else:
                    await coro_factory()
            except asyncio.CancelledError:
                self.note('остановлено' + (f': {label}' if label else ''), 'info')
                raise
            except asyncio.TimeoutError:
                self.note('время выполнения вышло' + (f': {label}' if label else ''), 'error')
            except Exception as e:
                self.note(f"{type(e).__name__}: {e}", 'error')
            finally:
                current_emulation.reset(etoken)
                self.touch()

        task = asyncio.ensure_future(runner())
        self.tasks.add(task)
        task.add_done_callback(lambda t: (self.tasks.discard(t), self.touch()))
        self.touch()
        return task

    async def run_command(self, command, args='', reply=None, attach=None):
        """
        reply  — {'text', 'file': id загрузки | None, 'from': 'me' | 'other', 'name'}: «сообщение, на которое отвечает команда»
                 (event.is_reply, event.get_reply_message(), files.download_reply_file(event));
        attach — id загрузки: файл прикреплён к самому сообщению с командой (event.file / event.photo / event.document).
        """
        info = self.core.modules[self.module]
        func = {k.lower(): f for k, f in info.commands.items()}.get(str(command).lower())
        if func is None:
            raise KeyError(f"Команда '{command}' не найдена в модуле '{self.module}'")
        prefix = await self.core.get_prefix()
        args = str(args or '').strip()
        text = f"{prefix}{command}" + (f" {args}" if args else '')
        reply_obj = None
        if reply:
            who = 'me' if reply.get('from') == 'me' else 'other'
            reply_obj = EmuMessage(self, str(reply.get('text') or '')[:MAX_TEXT], out=(who == 'me'))
            if who == 'other':
                reply_obj.sender_id = -1 if self.owner_id != -1 else -2
            files = []
            if reply.get('file'):
                up = self.uploads.get(reply['file'])
                if up is None:
                    raise ValueError('Файл для reply не найден — загрузите его заново')
                reply_obj.attach_upload(up)
                files = [{'id': None, 'name': up['name'], 'size': up['size']}]
            reply_obj._entry = self.add_entry(kind='reply', text=reply_obj.raw_text, who=who, name=str(reply.get('name') or '')[:40] or None, files=files,
                                              nodes=_to_nodes(reply_obj.raw_text, 'md') if reply_obj.raw_text else [])
        cmd_files = []
        up_cmd = None
        if attach:
            up_cmd = self.uploads.get(attach)
            if up_cmd is None:
                raise ValueError('Файл для команды не найден — загрузите его заново')
            cmd_files = [{'id': None, 'name': up_cmd['name'], 'size': up_cmd['size']}]
        self.add_entry(kind='cmd', text=text, files=cmd_files, reply=bool(reply_obj))
        event = EmuEvent(self, text)
        if reply_obj is not None:
            m = event.message
            m.is_reply = True
            m.reply_to_msg_id = reply_obj.id
            m.reply_to = types.SimpleNamespace(reply_to_msg_id=reply_obj.id)
            m._reply_obj = reply_obj
        if up_cmd is not None:
            event.message.attach_upload(up_cmd)

        async def go():
            etoken = current_event.set(event)
            try:
                await self.call(self.module, func, event)
            finally:
                current_event.reset(etoken)

        return self.spawn(go, text)

    def create_form(self, chat_id, text, buttons=None, for_user=None, black_list=None, timeout=None, styles=None):
        owner = current_module.get() or self.module
        form = EmuForm(self, chat_id, owner, for_user, black_list, timeout)
        form.styles = {str(k): normalize_style(v) for k, v in (styles or {}).items()}
        self.forms[form.form_id] = form
        form.text = text
        form._raw_buttons = buttons
        form._pages = [normalize_button_rows(buttons or [])]
        form._sync(text, buttons)
        return form

    async def send_text(self, text, file=None):
        msg = EmuMessage(self, '')
        msg._publish(text, 'html', file)
        return msg

    def make_waiter(self, timeout=None):
        return EmuWaiter(self, timeout, self.core)

    async def click(self, form_id, data):
        form = self.forms.get(form_id)
        if form is None or form._closed:
            self.toast('Форма больше не активна', 'error')
            return
        click = EmuClick(self, form, str(data), await self.me())
        self.spawn(lambda: form._dispatch(click), 'клик')

    async def send_input(self, form_id, data, value):
        form = self.forms.get(form_id)
        if form is None or form._closed:
            self.toast('Форма больше не активна', 'error')
            return
        inp = InlineInput(str(data), str(value), await self.me(), form.chat_id, form)
        self.spawn(lambda: form._dispatch_input(inp), 'ввод')

    def say(self, text):
        """Текст из поля «ответить» → всем Waiter'ам, что ждут сообщение в этом чате."""
        if not self.waiters:
            raise ValueError('Модуль сейчас ничего не ждёт')
        entry = self.add_entry(kind='in', text=str(text))
        msg = EmuMessage(self, text, entry=entry)
        for w in list(self.waiters):
            w._queue.put_nowait(msg)

    async def shutdown(self):
        self._cleanup_uploads()
        for task in list(self.tasks):
            task.cancel()
        for form in list(self.forms.values()):
            try:
                await form.close(remove_buttons=False)
            except Exception:
                pass
        for w in list(self.waiters):
            w.cancel()


# ---------------------------------------------------------------------------
# Описание команд модуля для сайта
# ---------------------------------------------------------------------------

def _first_param(node):
    args = node.args.args if node is not None else []
    return args[0].arg if args else None


def _uses_args(node, fdefs, depth=0):
    """True, если тело команды читает аргументы: get_args(), event.raw_text/.text (в т.ч. через хелпер)."""
    if node is None or depth > 2:
        return False
    param = _first_param(node)
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            f = sub.func
            name = f.attr if isinstance(f, ast.Attribute) else getattr(f, 'id', None)
            if name == 'get_args':
                return True
            if isinstance(f, ast.Name) and f.id in fdefs and f.id != getattr(node, 'name', None):
                passes_event = any(isinstance(a, ast.Name) and a.id == param for a in sub.args)
                if passes_event and _uses_args(fdefs[f.id], fdefs, depth + 1):
                    return True
        elif isinstance(sub, ast.Attribute) and sub.attr in ('raw_text', 'text'):
            root = sub.value
            while isinstance(root, ast.Attribute):
                root = root.value
            if isinstance(root, ast.Name) and root.id == param:
                return True
    return False


def _needs_reply(node):
    if node is None:
        return False
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute) and sub.attr in ('get_reply_message', 'is_reply', 'reply_to_msg_id', 'reply_to'):
            return True
        if isinstance(sub, ast.Attribute) and sub.attr == 'download_reply_file':
            return True
    return False


_FILE_ATTRS = {'file', 'photo', 'document', 'media', 'video', 'voice', 'audio', 'video_note', 'sticker', 'gif'}


def _needs_file(node):
    """Команда читает вложение: event.file / event.photo / event.message.document … или files.download_reply_file()."""
    if node is None:
        return False
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Attribute):
            continue
        if sub.attr == 'download_reply_file':
            return True
        if sub.attr in _FILE_ATTRS or sub.attr == 'download_media':
            base = sub.value
            if isinstance(base, ast.Name) and base.id in ('event', 'message', 'msg', 'reply', 'replied', 'r'):
                return True
            if isinstance(base, ast.Attribute) and base.attr == 'message':
                return True
    return False


def describe_commands(core, module_name, prefix):
    """[{'command','aliases','description','args','usage','confirm','needs_reply'}, ...] — для сайта."""
    info = core.modules[module_name]
    try:
        tree = ast.parse(info.source)
        fdefs = {n.name: n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    except SyntaxError:
        fdefs = {}
    result, seen = [], set()
    for func in info.commands.values():
        if id(func) in seen:
            continue
        seen.add(id(func))
        names = list(getattr(func, '_frx_commands', [func.__name__]))
        doc = (func.__doc__ or '').strip()
        first = doc.splitlines()[0] if doc else ''
        hints = re.findall(r'<[^<>\n]{1,30}>|\[[^\[\]\n]{1,30}\]', first)
        node = fdefs.get(func.__name__)
        takes_args = _uses_args(node, fdefs) or bool(hints)
        result.append({
            'command': names[0], 'aliases': names[1:], 'description': doc,
            'args': takes_args, 'usage': ' '.join(hints)[:48],
            'confirm': bool(DANGEROUS_COMMANDS & {n.lower() for n in names}),
            'needs_reply': _needs_reply(node),
            'needs_file': _needs_file(node),
        })
    return result
