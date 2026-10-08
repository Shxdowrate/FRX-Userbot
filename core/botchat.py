"""
core/botchat.py

BotChat — «аккаунт» стационарного бота (aiogram) на сайте `.web`: список чатов, переписка, отправка от имени бота.

Bot API не умеет отдавать список чатов и историю — бот видит только то, что приходит ему апдейтами и что он сам
отправляет. Поэтому BotChat ведёт ЛОКАЛЬНОЕ хранилище (user/data/botchat/store.json):

  * входящее — внешний middleware диспетчера (aiobot/bot.py) отдаёт сюда каждый Update (сообщения, правки,
    реакции, опросы, статус бота в чате);
  * исходящее — FRXBot.__call__ (aiobot/bot.py) отдаёт сюда результат КАЖДОГО вызова Bot API, так что сообщения,
    отправленные модулями (engine.bot.*, инлайн-формы, bot.*-хэндлеры) и самим сайтом, тоже попадают в историю.

История копится с момента подключения этого модуля; глубина — конфиг `history_limit` модуля BotChat.
Сайту хранилище отдаёт и «живые» события (длинный опрос `wait()`), а файлы (фото, видео, документы, аватарки)
скачиваются у Telegram лениво и кэшируются в user/data/botchat/media (лимит — конфиг `media_cache_mb`).

Ограничения Bot API, которые нельзя обойти: бот не видит чаты и сообщения до своего появления в них, не нажимает
инлайн-кнопки (их нажимают пользователи), не видит «печатает», онлайн и прочтение, скачивает файлы до 20 МБ.
"""

import asyncio
import bisect
import html as _html
import json
import re
import time
from collections import deque
from pathlib import Path

from .paths import DATA_DIR as _USER_DATA

DATA_DIR = _USER_DATA / 'botchat'
STORE_FILE = DATA_DIR / 'store.json'
MEDIA_DIR = DATA_DIR / 'media'
AVATAR_DIR = DATA_DIR / 'avatars'
MODULE = 'botchat'
DEFAULT_LIMIT = 500
DEFAULT_MEDIA_MB = 300
AVATAR_TTL = 24 * 3600
SAFE_ID = re.compile(r'^[A-Za-z0-9_\-]{1,200}$')

# какие типы апдейтов нужны BotChat сверх тех, что уже подписаны хэндлерами (см. aiobot/bot.py)
EXTRA_UPDATES = {
    'message', 'edited_message', 'channel_post', 'edited_channel_post', 'message_reaction',
    'message_reaction_count', 'my_chat_member', 'poll',
}

MEDIA_LABEL = {
    'photo': 'фото', 'video': 'видео', 'animation': 'gif', 'document': 'файл', 'audio': 'аудио',
    'voice': 'голосовое', 'video_note': 'кружок', 'sticker': 'стикер', 'location': 'геопозиция',
    'contact': 'контакт', 'poll': 'опрос', 'dice': 'кубик',
}


# ---------------------------------------------------------------------- #
# Разметка: мини-markdown -> HTML Telegram (для отправки с сайта)
# ---------------------------------------------------------------------- #

def md_to_html(text: str) -> str:
    """
    **жирный**, __курсив__, ~~зачёркнутый~~, ||спойлер||, `код`, ```блок кода```, [текст](ссылка), «> цитата».
    Всё остальное экранируется и уходит как есть.
    """
    stash = []

    def keep(s):
        stash.append(s)
        return f'\x00{len(stash) - 1}\x00'

    def fence(m):
        lang = (m.group(1) or '').strip()
        body = _html.escape(m.group(2).strip('\n'), quote=False)
        if re.fullmatch(r'[A-Za-z0-9_+\-]{1,20}', lang):
            return keep(f'<pre><code class="language-{lang}">{body}</code></pre>')
        return keep(f'<pre>{body}</pre>')

    text = re.sub(r'```([^\n`]*)\n(.*?)```', fence, text, flags=re.S)
    text = re.sub(r'`([^`\n]+)`', lambda m: keep(f'<code>{_html.escape(m.group(1), quote=False)}</code>'), text)
    text = _html.escape(text, quote=False)

    def link(m):
        url = _html.unescape(m.group(2)).strip()
        if not re.match(r'^(https?://|tg://|mailto:)', url, re.I):
            return m.group(0)
        return f'<a href="{_html.escape(url, quote=True)}">{m.group(1)}</a>'

    text = re.sub(r'\[([^\]\n]+)\]\(([^)\s]+)\)', link, text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text, flags=re.S)
    text = re.sub(r'__(.+?)__', r'<i>\1</i>', text, flags=re.S)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text, flags=re.S)
    text = re.sub(r'\|\|(.+?)\|\|', r'<tg-spoiler>\1</tg-spoiler>', text, flags=re.S)

    lines, out, quote = text.split('\n'), [], []
    for ln in lines + [None]:
        if ln is not None and ln.startswith('&gt; '):
            quote.append(ln[5:])
            continue
        if quote:
            out.append('<blockquote>' + '\n'.join(quote) + '</blockquote>')
            quote = []
        if ln is not None:
            out.append(ln)
    text = '\n'.join(out)
    return re.sub(r'\x00(\d+)\x00', lambda m: stash[int(m.group(1))], text)


# ---------------------------------------------------------------------- #
# aiogram -> компактные dict
# ---------------------------------------------------------------------- #

def _uname(u) -> str:
    return ' '.join(x for x in (u.first_name, getattr(u, 'last_name', None)) if x) or u.username or str(u.id)


def _user(u):
    if u is None:
        return None
    return {'id': u.id, 'name': _uname(u), 'username': u.username, 'bot': bool(getattr(u, 'is_bot', False))}


def _chat_name(c) -> str:
    if getattr(c, 'title', None):
        return c.title
    return ' '.join(x for x in (getattr(c, 'first_name', None), getattr(c, 'last_name', None)) if x) \
        or getattr(c, 'username', None) or str(c.id)


def _entities(items):
    out = []
    for e in items or []:
        d = {'t': e.type, 'o': e.offset, 'l': e.length}
        if e.url:
            d['u'] = e.url
        if e.language:
            d['g'] = e.language
        if e.user:
            d['id'] = e.user.id
        out.append(d)
    return out


def _snippet(d, n=120):
    t = (d.get('text') or '').strip().replace('\n', ' ')
    if not t:
        m = d.get('media')
        t = ('[' + MEDIA_LABEL.get(m['kind'], m['kind']) + ']') if m else (d.get('service') or '')
    return t[:n]


def _media(m):
    """Сообщение aiogram -> описание вложения (или None)."""
    if m.photo:
        sizes = sorted(m.photo, key=lambda p: p.width * p.height)
        big = sizes[-1]
        small = next((p for p in sizes if p.width >= 320), sizes[0])
        return {'kind': 'photo', 'fid': big.file_id, 'uid': big.file_unique_id, 'tfid': small.file_id,
                'tuid': small.file_unique_id, 'w': big.width, 'h': big.height, 'size': big.file_size}
    for kind in ('video', 'animation', 'video_note', 'audio', 'voice', 'document', 'sticker'):
        o = getattr(m, kind, None)
        if o is None:
            continue
        d = {'kind': kind, 'fid': o.file_id, 'uid': o.file_unique_id, 'size': getattr(o, 'file_size', None),
             'mime': getattr(o, 'mime_type', None), 'name': getattr(o, 'file_name', None),
             'w': getattr(o, 'width', None), 'h': getattr(o, 'height', None),
             'dur': getattr(o, 'duration', None)}
        if kind == 'video_note':
            d['w'] = d['h'] = getattr(o, 'length', None)
        if kind == 'audio':
            d['title'], d['performer'] = o.title, o.performer
        if kind == 'sticker':
            d.update({'emoji': o.emoji, 'animated': bool(o.is_animated), 'video': bool(o.is_video), 'set': o.set_name})
            d['mime'] = 'video/webm' if o.is_video else ('application/x-tgsticker' if o.is_animated else 'image/webp')
        th = getattr(o, 'thumbnail', None) or getattr(o, 'thumb', None)
        if th:
            d['tfid'], d['tuid'] = th.file_id, th.file_unique_id
        return d
    if m.venue:
        return {'kind': 'location', 'lat': m.venue.location.latitude, 'lon': m.venue.location.longitude,
                'title': m.venue.title, 'address': m.venue.address}
    if m.location:
        return {'kind': 'location', 'lat': m.location.latitude, 'lon': m.location.longitude}
    if m.contact:
        return {'kind': 'contact', 'name': ' '.join(x for x in (m.contact.first_name, m.contact.last_name) if x),
                'phone': m.contact.phone_number, 'uid_tg': m.contact.user_id}
    if m.dice:
        return {'kind': 'dice', 'emoji': m.dice.emoji, 'value': m.dice.value}
    if m.poll:
        return {'kind': 'poll', **_poll(m.poll)}
    return None


def _poll(p):
    correct = getattr(p, 'correct_option_id', None)
    if correct is None:
        ids = getattr(p, 'correct_option_ids', None)
        correct = ids[0] if ids else None
    return {'pid': p.id, 'question': p.question, 'options': [{'text': o.text, 'n': o.voter_count} for o in p.options],
            'total': p.total_voter_count, 'closed': bool(p.is_closed), 'anon': bool(p.is_anonymous),
            'multi': bool(getattr(p, 'allows_multiple_answers', False)), 'type': p.type, 'correct': correct}


def _service(m):
    if m.new_chat_members:
        return 'в чат добавлены: ' + ', '.join(_uname(u) for u in m.new_chat_members)
    if m.left_chat_member:
        return f'{_uname(m.left_chat_member)} покидает чат'
    if m.new_chat_title:
        return f'название чата изменено: {m.new_chat_title}'
    if m.new_chat_photo:
        return 'фото чата обновлено'
    if m.delete_chat_photo:
        return 'фото чата удалено'
    if m.group_chat_created or m.supergroup_chat_created:
        return 'группа создана'
    if m.channel_chat_created:
        return 'канал создан'
    if m.pinned_message:
        return 'сообщение закреплено'
    if m.migrate_to_chat_id:
        return 'группа стала супергруппой'
    for attr, label in (('forum_topic_created', 'тема создана'), ('forum_topic_closed', 'тема закрыта'),
                        ('video_chat_started', 'видеочат начат'), ('video_chat_ended', 'видеочат завершён'),
                        ('message_auto_delete_timer_changed', 'изменён таймер автоудаления')):
        if getattr(m, attr, None):
            return label
    return None


def _is_service(m) -> bool:
    return bool(_service(m))


def _keyboard(markup):
    rows = []
    for row in getattr(markup, 'inline_keyboard', None) or []:
        r = []
        for b in row:
            d = {'text': b.text}
            if b.url:
                d['url'] = b.url
            elif b.callback_data is not None:
                d['cb'] = True
            r.append(d)
        rows.append(r)
    return rows or None


def ser_message(m, me_id=None):
    """aiogram.types.Message -> dict для хранилища/сайта."""
    sender = _user(m.from_user)
    if m.sender_chat:
        sender = {'id': m.sender_chat.id, 'name': _chat_name(m.sender_chat), 'username': m.sender_chat.username, 'bot': False}
    d = {'id': m.message_id, 'chat': m.chat.id, 'ts': int(m.date.timestamp()), 'from': sender,
         'out': bool(m.from_user and me_id and m.from_user.id == me_id and not m.sender_chat)}
    text = m.text or m.caption
    if text:
        d['text'] = text
        ents = _entities(m.entities or m.caption_entities)
        if ents:
            d['ent'] = ents
    if m.edit_date:
        d['edit'] = int(m.edit_date.timestamp()) if hasattr(m.edit_date, 'timestamp') else int(m.edit_date)
    media = _media(m)
    if media:
        d['media'] = media
    if m.media_group_id:
        d['group'] = m.media_group_id
    svc = _service(m)
    if svc:
        d['service'] = svc
    if m.reply_to_message:
        r = m.reply_to_message
        rd = {'id': r.message_id, 'from': _user(r.from_user) if not r.sender_chat else {'name': _chat_name(r.sender_chat)},
              'text': (r.text or r.caption or '')[:120]}
        rm = _media(r)
        if rm and not rd['text']:
            rd['text'] = '[' + MEDIA_LABEL.get(rm['kind'], rm['kind']) + ']'
        d['reply'] = rd
    fo = m.forward_origin
    if fo:
        t = fo.type
        if t == 'user':
            d['fwd'] = _uname(fo.sender_user)
        elif t == 'hidden_user':
            d['fwd'] = fo.sender_user_name
        elif t == 'chat':
            d['fwd'] = _chat_name(fo.sender_chat)
        elif t == 'channel':
            d['fwd'] = _chat_name(fo.chat)
        else:
            d['fwd'] = '?'
    if m.via_bot:
        d['via'] = m.via_bot.username
    kb = _keyboard(m.reply_markup)
    if kb:
        d['kb'] = kb
    return d


_TL_ENT = {
    'MessageEntityBold': 'bold', 'MessageEntityItalic': 'italic', 'MessageEntityUnderline': 'underline',
    'MessageEntityStrike': 'strikethrough', 'MessageEntityCode': 'code', 'MessageEntityPre': 'pre',
    'MessageEntityTextUrl': 'text_link', 'MessageEntityUrl': 'url', 'MessageEntityMention': 'mention',
    'MessageEntityHashtag': 'hashtag', 'MessageEntityBotCommand': 'bot_command', 'MessageEntityEmail': 'email',
    'MessageEntityPhone': 'phone_number', 'MessageEntitySpoiler': 'spoiler', 'MessageEntityBlockquote': 'blockquote',
    'MessageEntityCashtag': 'cashtag',
}
CROSS_MAX_FILE = 50 * 1024 * 1024


def _tl_name(u) -> str:
    if u is None:
        return ''
    t = getattr(u, 'title', None)
    if t:
        return t
    return ' '.join(x for x in (getattr(u, 'first_name', None), getattr(u, 'last_name', None)) if x) \
        or getattr(u, 'username', None) or str(getattr(u, 'id', ''))


def ser_telethon(m, cid, sender, store=None) -> dict:
    """Сообщение Telethon (аккаунт пользователя) -> тот же dict, что и у сообщений бота. Помечается 'x': True —
    файлы такого сообщения скачивает юзербот, а не бот (у бота нет их file_id)."""
    d = {'id': m.id, 'chat': cid, 'ts': int(m.date.timestamp()), 'out': False, 'x': True,
         'from': {'id': getattr(sender, 'id', 0), 'name': _tl_name(sender), 'username': getattr(sender, 'username', None),
                  'bot': bool(getattr(sender, 'bot', False))}}
    text = m.message or ''
    if text:
        d['text'] = text
        ents = []
        for e in m.entities or []:
            t = _TL_ENT.get(type(e).__name__)
            if not t:
                continue
            x = {'t': t, 'o': e.offset, 'l': e.length}
            if t == 'text_link' and getattr(e, 'url', None):
                x['u'] = e.url
            if t == 'pre' and getattr(e, 'language', None):
                x['g'] = e.language
            ents.append(x)
        if ents:
            d['ent'] = ents
    if m.edit_date:
        d['edit'] = int(m.edit_date.timestamp())
    f = getattr(m, 'file', None)
    uid = f'x{abs(cid)}_{m.id}'
    base = {'uid': uid, 'fid': 'x', 'x': True, 'size': getattr(f, 'size', None), 'mime': getattr(f, 'mime_type', None),
            'name': getattr(f, 'name', None), 'w': getattr(f, 'width', None), 'h': getattr(f, 'height', None),
            'dur': getattr(f, 'duration', None)}
    kind = None
    if m.photo:
        kind = 'photo'
    elif m.sticker:
        kind = 'sticker'
        base['emoji'] = getattr(f, 'emoji', None)
        base['animated'] = (base['mime'] == 'application/x-tgsticker')
        base['video'] = (base['mime'] == 'video/webm')
    elif m.video_note:
        kind = 'video_note'
        base['w'] = base['h'] = base['w'] or 240
    elif m.gif:
        kind = 'animation'
    elif m.voice:
        kind = 'voice'
    elif m.video:
        kind = 'video'
    elif m.audio:
        kind = 'audio'
    elif m.document:
        kind = 'document'
    if kind:
        base['kind'] = kind
        base['tfid'] = 'x'
        base['tuid'] = uid + '_t'
        d['media'] = base
    elif m.geo:
        d['media'] = {'kind': 'location', 'lat': m.geo.lat, 'lon': m.geo.long}
    elif m.contact:
        d['media'] = {'kind': 'contact', 'name': ' '.join(x for x in (m.contact.first_name, m.contact.last_name) if x),
                      'phone': m.contact.phone_number, 'uid_tg': m.contact.user_id}
    elif getattr(m, 'dice', None):
        d['media'] = {'kind': 'dice', 'emoji': m.dice.emoticon, 'value': m.dice.value}
    elif m.poll:
        d['text'] = d.get('text') or '[опрос]'
    if m.fwd_from:
        d['fwd'] = getattr(m.fwd_from, 'from_name', None) or '?'
    rid = getattr(m.reply_to, 'reply_to_msg_id', None) if m.reply_to else None
    if rid:
        old = store.message(cid, rid) if store else None
        d['reply'] = {'id': rid, 'from': (old or {}).get('from'), 'text': ((old or {}).get('text') or '')[:120] or '…'}
    return d


def _old_group_id(cid: int) -> int:
    """-1001234567890 -> -1234567890 (id группы до превращения в супергруппу)."""
    t = str(cid)
    return int('-' + t[4:]) if t.startswith('-100') and len(t) > 4 else cid


def chat_meta(c) -> dict:
    return {'id': c.id, 'type': c.type, 'title': _chat_name(c), 'username': getattr(c, 'username', None)}


# ---------------------------------------------------------------------- #
# Хранилище
# ---------------------------------------------------------------------- #

class BotChat:
    def __init__(self, core):
        self.core = core
        self.chats: dict[int, dict] = {}
        self.msgs: dict[int, list] = {}          # chat_id -> [message dict], по возрастанию id
        self.ver = int(time.time())              # растёт при каждом событии (и между запусками)
        self._base = self.ver                    # события этого запуска — строго новее
        self.events = deque(maxlen=1000)         # (ver, kind, chat_id, data)
        self._waiters: list = []
        self._save_handle = None
        self._me = None
        self._polls: dict[str, tuple] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._load()

    # ------------------------------------------------------------ жизнь #

    @property
    def available(self) -> bool:
        return self.core._aiogram_bot is not None

    @property
    def bot(self):
        if self.core._aiogram_bot is None:
            raise RuntimeError('стационарный бот не запущен (aiogram_token не задан)')
        return self.core._aiogram_bot

    async def me(self):
        if self._me is None:
            self._me = await self.bot.me()
        return self._me

    def _me_id(self):
        return self._me.id if self._me else None

    async def _cfg(self, key, default):
        try:
            v = await self.core.get_config(key, module_name=MODULE)
            return default if v is None else v
        except Exception:
            return default

    def _load(self):
        try:
            data = json.loads(STORE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        self.chats = {int(k): v for k, v in (data.get('chats') or {}).items()}
        self.msgs = {int(k): v for k, v in (data.get('msgs') or {}).items()}
        for cid, lst in self.msgs.items():
            for m in lst:
                if (m.get('media') or {}).get('kind') == 'poll':
                    self._polls[m['media']['pid']] = (cid, m['id'])

    def _dirty(self):
        if self._save_handle is not None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._save_handle = loop.call_later(3, lambda: asyncio.ensure_future(self.save()))

    async def save(self):
        self._save_handle = None
        try:
            payload = json.dumps({'chats': self.chats, 'msgs': self.msgs}, ensure_ascii=False)
            await asyncio.to_thread(self._write, payload)
        except Exception as e:
            self.core.tlog(f'[BotChat] не удалось сохранить историю: {e}')

    @staticmethod
    def _write(payload):
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = STORE_FILE.with_suffix('.tmp')
        tmp.write_text(payload, encoding='utf-8')
        tmp.replace(STORE_FILE)

    def stats(self) -> dict:
        try:
            size = STORE_FILE.stat().st_size
        except OSError:
            size = 0
        return {'chats': len(self.chats), 'messages': sum(len(v) for v in self.msgs.values()), 'store_bytes': size}

    async def clear(self):
        self.chats.clear(); self.msgs.clear(); self._polls.clear()
        await self.save()
        self._emit('reset', 0, None)

    # ----------------------------------------------------------- события #

    def _emit(self, kind, chat_id, data):
        self.ver += 1
        self.events.append((self.ver, kind, chat_id, data))
        waiters, self._waiters = self._waiters, []
        for f in waiters:
            if not f.done():
                f.set_result(True)

    async def wait(self, since: int, timeout: float = 25.0) -> dict:
        """Длинный опрос для сайта: события новее `since` (или reset, если клиент отстал/сервер перезапускался)."""
        if not since or since > self.ver or since < self._base \
                or (self.events and since < self.events[0][0] - 1):
            return {'ver': self.ver, 'reset': True, 'events': []}
        if since == self.ver:
            fut = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            try:
                await asyncio.wait_for(fut, timeout)
            except asyncio.TimeoutError:
                if fut in self._waiters:
                    self._waiters.remove(fut)
                return {'ver': self.ver, 'reset': False, 'events': []}
        evs = [{'v': v, 'k': k, 'c': c, 'd': d} for (v, k, c, d) in self.events if v > since]
        return {'ver': self.ver, 'reset': False, 'events': evs}

    # ------------------------------------------------------------- чаты #

    def _touch_chat(self, meta: dict, ts=None) -> dict:
        c = self.chats.get(meta['id'])
        if c is None:
            c = {'id': meta['id'], 'type': meta['type'], 'title': meta['title'], 'username': meta.get('username'),
                 'last': '', 'last_ts': ts or int(time.time()), 'last_out': False, 'last_from': None,
                 'unread': 0, 'read_id': 0, 'pinned': False, 'left': False,
                 'seen_user': False, 'admin': False, 'cross': False}
            self.chats[meta['id']] = c
            old = _old_group_id(meta['id'])                      # группа -> супергруппа (id тот же + префикс -100)
            if meta['type'] == 'supergroup' and old in self.chats:
                self._merge_group(old, meta['id'])
        else:
            c['type'], c['title'], c['username'] = meta['type'], meta['title'], meta.get('username')
        return c

    def _refresh_last(self, cid):
        c = self.chats.get(cid)
        lst = self.msgs.get(cid) or []
        if not c:
            return
        if lst:
            m = lst[-1]
            c['last'] = _snippet(m, 90)
            c['last_ts'] = m['ts']
            c['last_out'] = bool(m.get('out'))
            c['last_from'] = (m.get('from') or {}).get('name') if c['type'] != 'private' else None
        else:
            c['last'] = ''

    def chat_summary(self, c) -> dict:
        d = {k: c.get(k) for k in ('id', 'type', 'title', 'username', 'last', 'last_ts', 'last_out', 'last_from',
                                   'unread', 'read_id', 'pinned', 'left', 'cross')}
        # в группе бот без права «видеть все сообщения» получает только команды, ответы ему и упоминания;
        # пока не пришло ни одного сообщения от человека (и бот не админ) — сайту показываем предупреждение
        d['blind'] = bool(c.get('type') in ('group', 'supergroup') and not c.get('left') and not c.get('seen_user')
                          and not c.get('admin'))
        return d

    def chat_list(self) -> list:
        items = sorted(self.chats.values(), key=lambda c: (not c.get('pinned'), -(c.get('last_ts') or 0)))
        return [self.chat_summary(c) for c in items]

    def _merge_group(self, old_id, new_id):
        """Обычная группа стала супергруппой: для сайта это один и тот же чат (id совпадает, добавился только -100)."""
        old, new = self.chats.get(old_id), self.chats.get(new_id)
        if old is None or new is None or old_id == new_id:
            return
        for k in ('pinned', 'seen_user', 'admin', 'cross'):
            new[k] = bool(new.get(k) or old.get(k))
        new['unread'] = (new.get('unread') or 0) + (old.get('unread') or 0)
        new['read_id'] = max(new.get('read_id') or 0, old.get('read_id') or 0)
        have = {m['id'] for m in self.msgs.get(new_id, [])}
        moved = []
        for m in self.msgs.pop(old_id, []):
            if m['id'] in have:
                continue
            m['chat'] = new_id
            moved.append(m)
        if moved:
            lst = self.msgs.setdefault(new_id, []) + moved
            lst.sort(key=lambda m: m['id'])
            self.msgs[new_id] = lst
        self.chats.pop(old_id, None)
        for pid, ref in list(self._polls.items()):
            if ref[0] == old_id:
                self._polls[pid] = (new_id, ref[1])
        self._refresh_last(new_id)
        self._emit('migrate', old_id, {'new': new_id, 'chat': self.chat_summary(new)})
        self._dirty()

    def migrate_chat(self, old_id, new_id, meta=None):
        """Явный сигнал Telegram (migrate_to_chat_id / migrate_from_chat_id)."""
        if old_id not in self.chats:
            return
        if new_id not in self.chats:
            base = dict(self.chats[old_id])
            base.update({'id': new_id, 'type': 'supergroup'})
            if meta:
                base.update({k: meta[k] for k in ('title', 'username') if k in meta})
            self.chats[new_id] = base
            self.msgs.setdefault(new_id, [])
        self.chats[new_id]['type'] = 'supergroup'
        self._merge_group(old_id, new_id)

    def get_chat_meta(self, cid):
        c = self.chats.get(cid)
        return self.chat_summary(c) if c else None

    # --------------------------------------------------------- сообщения #

    def _find(self, cid, mid):
        lst = self.msgs.get(cid) or []
        ids = [m['id'] for m in lst]
        i = bisect.bisect_left(ids, mid)
        if i < len(lst) and lst[i]['id'] == mid:
            return lst, i
        return lst, None

    async def _put(self, d: dict, meta: dict, incoming: bool, edited: bool = False):
        cid = d['chat']
        c = self._touch_chat(meta, d['ts'])
        lst = self.msgs.setdefault(cid, [])
        _, i = self._find(cid, d['id'])
        if i is not None:                              # правка / повтор: сохраняем реакции и отметки
            old = lst[i]
            for k in ('rx', 'my_rx'):
                if k in old and k not in d:
                    d[k] = old[k]
            if old.get('out') and not d.get('out'):
                d['out'] = True
            if edited and 'edit' not in d:
                d['edit'] = int(time.time())
            lst[i] = d
        else:
            ids = [m['id'] for m in lst]
            lst.insert(bisect.bisect_left(ids, d['id']), d)
            if incoming and not d.get('out'):
                c['unread'] = c.get('unread', 0) + 1
            limit = max(50, int(await self._cfg('history_limit', DEFAULT_LIMIT)))
            if len(lst) > limit:
                del lst[:len(lst) - limit]
        if (d.get('media') or {}).get('kind') == 'poll':
            self._polls[d['media']['pid']] = (cid, d['id'])
        if not d.get('service'):
            c['left'] = False
        self._refresh_last(cid)
        self._emit('msg', cid, d)
        self._emit('chat', cid, self.chat_summary(c))
        self._dirty()

    # --- вход: апдейты Telegram ---

    async def on_update(self, upd):
        if not await self._cfg('enabled', True):
            return
        try:
            await self.me()
        except Exception:
            pass
        me_id = self._me_id()
        if upd.message:
            m = upd.message
            if m.migrate_to_chat_id:                                  # группа стала супергруппой
                self.migrate_chat(m.chat.id, m.migrate_to_chat_id)
                return
            if m.migrate_from_chat_id:
                self.migrate_chat(m.migrate_from_chat_id, m.chat.id, chat_meta(m.chat))
            await self._put(ser_message(m, me_id), chat_meta(m.chat), incoming=True)
            self._mark_seen(m.chat.id, m)
        elif upd.channel_post:
            m = upd.channel_post
            await self._put(ser_message(m, me_id), chat_meta(m.chat), incoming=True)
        elif upd.edited_message or upd.edited_channel_post:
            m = upd.edited_message or upd.edited_channel_post
            await self._put(ser_message(m, me_id), chat_meta(m.chat), incoming=False, edited=True)
        elif upd.message_reaction:
            await self._on_reaction(upd.message_reaction)
        elif upd.message_reaction_count:
            r = upd.message_reaction_count
            lst, i = self._find(r.chat.id, r.message_id)
            if i is not None:
                rx = {}
                for x in r.reactions:
                    if getattr(x.type, 'emoji', None):
                        rx[x.type.emoji] = x.total_count
                lst[i]['rx'] = rx
                self._emit('msg', r.chat.id, lst[i]); self._dirty()
        elif upd.my_chat_member:
            u = upd.my_chat_member
            c = self._touch_chat(chat_meta(u.chat))
            c['left'] = u.new_chat_member.status in ('left', 'kicked')
            c['admin'] = u.new_chat_member.status in ('administrator', 'creator')
            self._emit('chat', c['id'], self.chat_summary(c)); self._dirty()
        elif upd.poll:
            self._on_poll(upd.poll)

    def _mark_seen(self, cid, m):
        """Первое сообщение живого человека в группе -> предупреждение «бот может не видеть сообщения» пропадает."""
        c = self.chats.get(cid)
        if not c or c.get('seen_user') or c['type'] not in ('group', 'supergroup'):
            return
        u = m.from_user
        if u and not u.is_bot and not _is_service(m):
            c['seen_user'] = True
            self._emit('chat', cid, self.chat_summary(c)); self._dirty()

    async def _on_reaction(self, r):
        lst, i = self._find(r.chat.id, r.message_id)
        if i is None:
            return
        rx = dict(lst[i].get('rx') or {})
        for x in r.old_reaction:
            e = getattr(x, 'emoji', None)
            if e and rx.get(e):
                rx[e] -= 1
                if rx[e] <= 0:
                    del rx[e]
        for x in r.new_reaction:
            e = getattr(x, 'emoji', None)
            if e:
                rx[e] = rx.get(e, 0) + 1
        lst[i]['rx'] = rx
        self._emit('msg', r.chat.id, lst[i]); self._dirty()

    def _on_poll(self, p):
        ref = self._polls.get(p.id)
        if not ref:
            return
        lst, i = self._find(*ref)
        if i is not None:
            lst[i]['media'] = {'kind': 'poll', **_poll(p)}
            self._emit('msg', ref[0], lst[i]); self._dirty()

    # --: cross watch — сообщения группы глазами аккаунта пользователя ---

    def attach_client(self, client):
        from telethon import events
        try:
            client.remove_event_handler(self._on_cross, events.NewMessage())
        except Exception:
            pass
        client.add_event_handler(self._on_cross, events.NewMessage())

    async def _on_cross(self, event):
        cid = event.chat_id
        c = self.chats.get(cid)
        if not c or not c.get('cross') or c['type'] not in ('group', 'supergroup') or c.get('left'):
            return
        m = event.message
        if m.action or not await self._cfg('enabled', True):
            return
        try:
            sender = await m.get_sender()
            await self.me()
        except Exception:
            sender = None
        if sender is not None and sender.id == self._me_id():
            return
        d = ser_telethon(m, cid, sender, self)
        c['seen_user'] = True
        await self._put(d, {'id': cid, 'type': c['type'], 'title': c['title'], 'username': c.get('username')},
                        incoming=not event.out)

    def set_cross(self, cid, on):
        c = self.chats.get(cid)
        if not c:
            raise KeyError(cid)
        c['cross'] = bool(on)
        self._emit('chat', cid, self.chat_summary(c)); self._dirty()

    async def _cross_bytes(self, cid, mid, thumb):
        client = self.core.client
        if client is None:
            raise RuntimeError('юзербот не подключён')
        msg = await client.get_messages(cid, ids=mid)
        if not msg or not msg.media:
            raise RuntimeError('сообщение недоступно')
        if getattr(msg.file, 'size', 0) and msg.file.size > CROSS_MAX_FILE and not thumb:
            raise RuntimeError('файл больше 50 МБ — через cross watch такие не показываются')
        data = await client.download_media(msg, file=bytes, thumb=-1 if thumb else None)
        if not data:
            raise RuntimeError('нет данных')
        return data

    # --- вход: результаты вызовов Bot API (исходящее) ---

    async def record_result(self, method, result):
        from aiogram.types import Message
        name = type(method).__name__
        items = [m for m in (result if isinstance(result, list) else [result]) if isinstance(m, Message)]
        deleted = name in ('DeleteMessage', 'DeleteMessages') and isinstance(getattr(method, 'chat_id', None), int)
        if not items and not deleted:
            return                                   # в т.ч. GetMe: иначе me() -> GetMe -> record_result -> me() ...
        if not await self._cfg('enabled', True):
            return
        try:
            await self.me()
        except Exception:
            pass
        me_id = self._me_id()
        for m in items:
            await self._put(ser_message(m, me_id), chat_meta(m.chat), incoming=False, edited=name.startswith('Edit'))
        if name == 'DeleteMessage' and deleted:
            self.drop_messages(method.chat_id, [method.message_id])
        elif name == 'DeleteMessages' and deleted:
            self.drop_messages(method.chat_id, list(method.message_ids))

    def drop_messages(self, cid, ids):
        lst = self.msgs.get(cid)
        if not lst:
            return
        s = set(ids)
        keep = [m for m in lst if m['id'] not in s]
        if len(keep) != len(lst):
            self.msgs[cid] = keep
            self._refresh_last(cid)
            self._emit('del', cid, {'ids': list(s)})
            c = self.chats.get(cid)
            if c:
                self._emit('chat', cid, self.chat_summary(c))
            self._dirty()

    async def put_local(self, d: dict, meta: dict):
        """Сообщение, созданное локально (copy_message возвращает только id)."""
        await self._put(d, meta, incoming=False)

    def message(self, cid, mid):
        lst, i = self._find(cid, mid)
        return lst[i] if i is not None else None

    def set_reaction(self, cid, mid, emoji):
        lst, i = self._find(cid, mid)
        if i is None:
            return
        m = lst[i]
        rx = dict(m.get('rx') or {})
        for e in m.get('my_rx') or []:
            if rx.get(e):
                rx[e] -= 1
                if rx[e] <= 0:
                    del rx[e]
        if emoji:
            rx[emoji] = rx.get(emoji, 0) + 1
            m['my_rx'] = [emoji]
        else:
            m.pop('my_rx', None)
        m['rx'] = rx
        self._emit('msg', cid, m); self._dirty()

    def set_poll(self, cid, mid, poll):
        lst, i = self._find(cid, mid)
        if i is not None:
            lst[i]['media'] = {'kind': 'poll', **_poll(poll)}
            self._emit('msg', cid, lst[i]); self._dirty()

    def mark_read(self, cid):
        c = self.chats.get(cid)
        if not c:
            return
        lst = self.msgs.get(cid) or []
        c['read_id'] = lst[-1]['id'] if lst else 0
        if c.get('unread'):
            c['unread'] = 0
            self._emit('chat', cid, self.chat_summary(c)); self._dirty()

    def set_pinned(self, cid, on):
        c = self.chats.get(cid)
        if c:
            c['pinned'] = bool(on)
            self._emit('chat', cid, self.chat_summary(c)); self._dirty()

    def add_chat(self, meta: dict):
        c = self._touch_chat(meta)
        self._emit('chat', c['id'], self.chat_summary(c)); self._dirty()
        return c

    def forget_chat(self, cid):
        self.chats.pop(cid, None)
        self.msgs.pop(cid, None)
        self._emit('drop', cid, None); self._dirty()

    def mark_left(self, cid, left=True):
        c = self.chats.get(cid)
        if c:
            c['left'] = left
            self._emit('chat', cid, self.chat_summary(c)); self._dirty()

    def page(self, cid, before=None, limit=60):
        lst = self.msgs.get(cid) or []
        if before:
            lst = [m for m in lst if m['id'] < before]
        more = len(lst) > limit
        return lst[-limit:], more

    def search(self, cid, q, limit=100):
        q = q.lower().strip()
        if not q:
            return []
        out = [m for m in (self.msgs.get(cid) or []) if q in (m.get('text') or '').lower()
               or q in ((m.get('media') or {}).get('name') or '').lower()]
        return out[-limit:]

    # ------------------------------------------------------------ файлы #

    def _lock(self, key):
        return self._locks.setdefault(key, asyncio.Lock())

    async def media_path(self, cid: int, mid: int, thumb: bool = False):
        """Путь к кэшированному файлу вложения (скачивает при первом обращении) -> (path, mime)."""
        m = self.message(cid, mid)
        media = (m or {}).get('media')
        if not media or not media.get('fid'):
            return None, None
        fid, uid = media['fid'], media['uid']
        mime = media.get('mime')
        if media.get('x'):                                    # сообщение пришло через cross watch — качает юзербот
            return await self._cross_path(cid, mid, media, thumb)
        if thumb and media.get('tfid'):
            fid, uid, mime = media['tfid'], media['tuid'], 'image/jpeg'
        elif media['kind'] == 'photo':
            mime = 'image/jpeg'
        if not SAFE_ID.match(uid or ''):
            return None, None
        path = MEDIA_DIR / uid
        if not path.exists():
            async with self._lock(uid):
                if not path.exists():
                    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix('.part')
                    await self.bot.download(fid, destination=tmp)
                    tmp.replace(path)
                    asyncio.get_running_loop().call_later(1, self._trim_media_soon, int(await self._cfg('media_cache_mb', DEFAULT_MEDIA_MB)))
        return path, mime or 'application/octet-stream'

    async def _cross_path(self, cid, mid, media, thumb):
        uid = media['uid'] + ('_t' if thumb else '')
        mime = 'image/jpeg' if (thumb or media['kind'] == 'photo') else (media.get('mime') or 'application/octet-stream')
        if not SAFE_ID.match(uid):
            return None, None
        path = MEDIA_DIR / uid
        if not path.exists():
            async with self._lock(uid):
                if not path.exists():
                    data = await self._cross_bytes(cid, mid, thumb)
                    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
                    tmp = path.with_suffix('.part')
                    await asyncio.to_thread(tmp.write_bytes, data)
                    tmp.replace(path)
                    asyncio.get_running_loop().call_later(1, self._trim_media_soon, int(await self._cfg('media_cache_mb', DEFAULT_MEDIA_MB)))
        return path, mime

    def _trim_media_soon(self, limit_mb):
        try:
            files = sorted((p for p in MEDIA_DIR.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            cap = max(10, limit_mb) * 1024 * 1024
            for p in files:
                if total <= cap:
                    break
                total -= p.stat().st_size
                p.unlink(missing_ok=True)
        except OSError:
            pass

    def drop_avatar(self, cid: int):
        for ext in ('.jpg', '.none'):
            (AVATAR_DIR / f'{cid}{ext}').unlink(missing_ok=True)

    async def avatar_path(self, cid: int):
        """Мини-аватарка чата -> путь к jpg или None (если фото нет)."""
        AVATAR_DIR.mkdir(parents=True, exist_ok=True)
        path, none = AVATAR_DIR / f'{cid}.jpg', AVATAR_DIR / f'{cid}.none'
        for p in (path, none):
            if p.exists() and time.time() - p.stat().st_mtime < AVATAR_TTL:
                return path if p is path else None
        async with self._lock(f'av{cid}'):
            try:
                fid = None
                if cid == (await self.me()).id:                 # собственная аватарка бота — через профильные фото
                    ph = await self.bot.get_user_profile_photos(cid, limit=1)
                    if ph.photos:
                        fid = ph.photos[0][0].file_id
                else:
                    chat = await self.bot.get_chat(cid)
                    fid = chat.photo.small_file_id if chat.photo else None
                if fid:
                    tmp = path.with_suffix('.part')
                    await self.bot.download(fid, destination=tmp)
                    tmp.replace(path)
                    none.unlink(missing_ok=True)
                    return path
            except Exception:
                pass
            path.unlink(missing_ok=True)
            none.write_bytes(b'')
            return None
