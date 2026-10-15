"""Пространство Hikka внутри FRX WebIDE Sandbox.

В песочнице у FRX нет Telegram: `engine.client` — заглушка Sandbox, сообщения — записи ленты, формы рисует сайт.
Чтобы модули Hikka работали там так же, как в настоящем FRX, здесь:

* SandboxTelegramClient — настоящий подкласс TelegramClient (без соединения): отправка, правка, удаление, файлы,
  получение сообщений и сущностей идут в ленту Sandbox; модули получают обычные объекты Telethon (Message, User,
  Channel…), поэтому utils.answer, message.edit / reply / get_reply_message и т. п. работают как в Telegram;
* входящие сообщения песочницы превращаются в UpdateNewMessage и проходят через обработчики Telethon —
  команды с префиксом пространства, вотчеры, client.on(events.NewMessage(...)) модулей;
* инлайн: client.inline_query(<бот FRX>, …) отвечает пространство (как настоящий бот), результат рисуется
  формой Sandbox; нажатия и ввод с сайта приходят обратно как callback_query / chosen_inline_result;
* у пространства песочницы своя БД и папка модулей (user/data/sandbox/hikka), свой контекст (_frx) —
  основное пространство FRX и его модули песочница не трогает.

Чего нет в песочнице (как и у модулей FRX): сети Telegram — прочие MTProto-запросы дают понятную ошибку
«не эмулируется в Sandbox»; чужие инлайн-боты; виртуальное время Sandbox на циклы Hikka не действует.
"""

import asyncio
import contextlib
import datetime
import io
import logging
import os
import sys
import types as pytypes
import uuid
from pathlib import Path

from telethon import TelegramClient, helpers, utils as tl_utils
from telethon.sessions import StringSession
from telethon.tl import types
from telethon.tl.custom.message import Message

from . import _frx, _html

logger = logging.getLogger(__name__)

_UTC = datetime.timezone.utc
_EPOCH = datetime.datetime(2024, 1, 1, tzinfo=_UTC)


def session_of(engine):
    """Сессия Sandbox, если `engine` — ядро песочницы (иначе None)."""
    if type(engine).__name__ == "SandboxEngine":
        return getattr(engine, "s", None)
    return None


async def enter(engine, session, data_dir, cache_dir):
    """Подготовить контекст пространства для песочницы (вызывается в её потоке). Старое пространство этой же
    песочницы (после «перезапустить» модуль загружается заново в том же потоке) останавливается."""
    ctx = _frx.enter_sandbox()
    old = ctx.sandbox
    if old is not None:
        with contextlib.suppress(Exception):
            await old.shutdown()
    bridge = Bridge(engine, session)
    ctx.sandbox = bridge
    ctx.engine = engine
    _frx.configure(data_dir, cache_dir)
    return bridge


async def leave():
    ctx = _frx.current()
    bridge = ctx.sandbox
    if bridge is not None:
        with contextlib.suppress(Exception):
            await bridge.shutdown()
    _frx.leave_sandbox()


def _ag_user(user):
    """SbUser → объект, похожий на aiogram User (id, username, first_name, …)."""
    first = getattr(user, "first_name", "") or ""
    last = getattr(user, "last_name", None)
    return pytypes.SimpleNamespace(
        id=user.id, is_bot=bool(getattr(user, "bot", False)), first_name=first, last_name=last,
        username=getattr(user, "username", None), language_code="ru", is_premium=bool(getattr(user, "premium", False)),
        full_name=" ".join(x for x in (first, last) if x), url=f"tg://user?id={user.id}",
    )


def _bad_request(text):
    try:
        from aiogram.exceptions import TelegramBadRequest

        return TelegramBadRequest(method=None, message=text)
    except Exception:
        return RuntimeError(text)


# ====================================================================================================== мост
class Bridge:
    """Всё, что связывает пространство Hikka с одной сессией Sandbox."""

    def __init__(self, engine, session):
        self.engine = engine
        self.s = session
        self.sb = sys.modules[type(session).__module__]           # core.sandbox: SbMessage, SbChat, make_media…
        self.space = None
        self.real = None                                           # SandboxTelegramClient
        self.bot = SandboxBot(self)
        self.forms: dict = {}                                      # inline_message_id → форма Sandbox
        self.topics: dict = {}                                     # ключ чата → [(id, название)]
        self._recent: dict = {}                                    # id сообщения → чат (для get_reply_message)
        self._hooks: dict = {}

    @property
    def client(self):
        """Клиент, к которому привязываются объекты для модулей («вид» пространства)."""
        space = self.space
        return space.client if space is not None and space.client is not None else self.real

    # ------------------------------------------------------------------ жизненный цикл
    def make_client(self, space):
        self.space = space
        self.real = SandboxTelegramClient(self)
        return self.real

    def attach(self, space):
        """Подключиться к событиям песочницы: сообщения юзерботу, сообщения боту, кнопки сообщений бота."""
        self.space = space
        s = self.s
        if self._hooks:
            return
        orig_user, orig_bot, orig_cb = s.dispatch_user_message, s.dispatch_bot_message, s.fire_callback
        bridge = self

        async def dispatch_user_message(event):
            await orig_user(event)
            if bridge.space is not None and bridge.space.started:
                await bridge._call(bridge.hikka_dispatch, event)

        async def dispatch_bot_message(m):
            await orig_bot(m)
            if bridge.space is not None and bridge.space.started:
                await bridge._call(bridge.hikka_bot_message, m)

        async def fire_callback(chat_key, sender_id, data, message_id=None):
            if bridge.space is not None and bridge.space.started and bridge._owns_callback(data):
                await bridge._call(bridge.hikka_bot_button, chat_key, sender_id, data, message_id)
                await s.settle()
                return
            return await orig_cb(chat_key, sender_id, data, message_id)

        self._hooks = {"dispatch_user_message": dispatch_user_message, "dispatch_bot_message": dispatch_bot_message,
                       "fire_callback": fire_callback}
        for name, fn in self._hooks.items():
            setattr(s, name, fn)

    def detach(self):
        for name, fn in self._hooks.items():
            if self.s.__dict__.get(name) is fn:
                self.s.__dict__.pop(name, None)
        self._hooks = {}

    async def shutdown(self):
        space = self.space
        if space is not None:
            with contextlib.suppress(Exception):
                await space.stop()
        self.detach()

    def restart_space(self):
        space = self.space
        if space is not None:
            self.spawn(space.restart(), "hikka: перезапуск пространства")

    def stop_space(self):
        space = self.space
        if space is not None:
            self.spawn(space.stop(), "hikka: остановка пространства")

    # ------------------------------------------------------------------ задачи и журнал песочницы
    def spawn(self, coro, label="hikka"):
        return self.s.spawn(coro, label)

    async def _call(self, func, *args):
        info = self.s.module
        module = info.module_name if info is not None else "hikkacompat"
        return await self.s.call(module, func, *args)

    def note(self, kind, text):
        with contextlib.suppress(Exception):
            self.s.note(kind, text)

    def unsupported(self, what):
        self.note("warn", f"{what} не эмулируется в Sandbox")
        return self.sb.SbUnsupported(f"{what} не эмулируется в Sandbox")

    # ------------------------------------------------------------------ сущности
    @property
    def owner(self):
        return self.s.owner

    def user(self, u) -> types.User:
        return types.User(
            id=u.id, is_self=u.id == self.s.owner.id, bot=bool(getattr(u, "bot", False)), access_hash=0,
            first_name=getattr(u, "first_name", None) or "", last_name=getattr(u, "last_name", None),
            username=getattr(u, "username", None), premium=bool(getattr(u, "premium", False)),
            phone=getattr(u, "phone", None),
        )

    def entity(self, chat):
        """SbChat → User / Channel / Chat."""
        if chat.type == "private":
            return self.user(self.s.users.get(chat.peer, self.s.owner))
        if chat.type in ("supergroup", "channel"):
            return types.Channel(
                id=tl_utils.resolve_id(chat.id)[0], title=chat.title, photo=types.ChatPhotoEmpty(), date=_EPOCH,
                creator=True, megagroup=chat.type == "supergroup", broadcast=chat.type == "channel", access_hash=0,
                username=chat.username, forum=bool(self.topics.get(chat.key)),
                participants_count=len(self.s.users),
            )
        return types.Chat(id=abs(chat.id), title=chat.title, photo=types.ChatPhotoEmpty(),
                          participants_count=len(self.s.users), date=_EPOCH, version=0, creator=True)

    @staticmethod
    def peer(chat):
        if chat.type == "private":
            return types.PeerUser(chat.peer)
        if chat.type in ("supergroup", "channel"):
            return types.PeerChannel(tl_utils.resolve_id(chat.id)[0])
        return types.PeerChat(abs(chat.id))

    def chat_of(self, entity):
        """Что угодно, что модуль передаёт как «чат» (id, @username, Message, Peer, InputPeer, User…) → SbChat."""
        s = self.s
        if isinstance(entity, self.sb.SbChat):
            return entity
        if isinstance(entity, Message):
            sbm = getattr(entity, "_sb", None)
            if sbm is not None:
                return sbm._chat
            entity = entity.peer_id
        if isinstance(entity, self.sb.SbUser):
            return s.chat_for_user(entity, "user")
        if entity in ("me", "self") or isinstance(entity, (types.InputPeerSelf, types.InputUserSelf)):
            return s.resolve_chat("me", "user")
        if isinstance(entity, types.User) and entity.is_self:
            return s.resolve_chat("me", "user")
        if isinstance(entity, str):
            text = entity.strip()
            for prefix in ("https://t.me/", "http://t.me/", "t.me/", "tg://resolve?domain="):
                if text.startswith(prefix):
                    text = text[len(prefix):].split("/", 1)[0]
            if text.lstrip("-").isdigit():
                entity = int(text)
            else:
                try:
                    return s.resolve_chat(text, "user")
                except ValueError:
                    user = self.auto_user(username=text.lstrip("@"))
                    if user is None:
                        raise
                    return s.chat_for_user(user, "user")
        if not isinstance(entity, int):
            try:
                entity = tl_utils.get_peer_id(entity)
            except Exception:
                raise ValueError(f"Не удалось понять, что за чат: {entity!r}")
        try:
            return s.resolve_chat(entity, "user")
        except ValueError:
            user = self.auto_user(uid=entity) if entity > 0 else None
            if user is None:
                raise
            return s.chat_for_user(user, "user")

    def auto_user(self, *, username=None, uid=None):
        """Незнакомый @username или id — в Telegram он бы нашёлся. В песочнице появляется участник (только в ней)."""
        import re

        s = self.s
        if username is not None and not re.fullmatch(r"[A-Za-z]\w{3,31}", username or ""):
            return None
        if uid is None:
            uid = 300000000 + (abs(hash(username.lower())) % 90000000)
            while uid in s.users:
                uid += 1
        name = username or f"Пользователь {uid}"
        user = self.sb.SbUser(int(uid), name, None, username, bot=bool(username and username.lower().endswith("bot")))
        s.users[user.id] = user
        s._add_private_chats(user)
        s.touch_state()
        self.note("info", f"{'@' + username if username else uid}: в песочнице такого не было — "
                          f"{'бот' if user.bot else 'участник'} создан автоматически (только в этой песочнице)")
        return user

    def find(self, chat, message_id):
        if isinstance(message_id, Message):
            sbm = getattr(message_id, "_sb", None)
            if sbm is not None:
                return sbm
            message_id = message_id.id
        if isinstance(message_id, self.sb.SbMessage):
            return message_id
        message_id = tl_utils.get_message_id(message_id) if not isinstance(message_id, int) else message_id
        return self.s.find_message(chat, message_id) if message_id is not None else None

    # ------------------------------------------------------------------ сообщения
    @staticmethod
    def _date(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return datetime.datetime.fromtimestamp(value.timestamp(), _UTC)
        return value

    def media(self, md):
        if not md:
            return None
        doc_id = int(uuid.UUID(int=abs(hash(md["id"])) % (1 << 120)).int % (1 << 62))
        if md["kind"] == "photo":
            return types.MessageMediaPhoto(photo=types.Photo(
                id=doc_id, access_hash=0, file_reference=b"", date=_EPOCH,
                sizes=[types.PhotoSize(type="y", w=800, h=600, size=int(md["size"]))], dc_id=2,
            ))
        attrs = [types.DocumentAttributeFilename(md["name"])]
        kind = md["kind"]
        if kind in ("voice", "audio"):
            attrs.append(types.DocumentAttributeAudio(duration=1, voice=kind == "voice"))
        elif kind in ("video", "video_note", "gif"):
            attrs.append(types.DocumentAttributeVideo(duration=1, w=320, h=240, round_message=kind == "video_note"))
            if kind == "gif":
                attrs.append(types.DocumentAttributeAnimated())
        elif kind == "sticker":
            attrs += [types.DocumentAttributeSticker(alt="", stickerset=types.InputStickerSetEmpty()),
                      types.DocumentAttributeImageSize(512, 512)]
        return types.MessageMediaDocument(document=types.Document(
            id=doc_id, access_hash=0, file_reference=b"", date=_EPOCH, mime_type=md["mime"], size=int(md["size"]),
            dc_id=2, attributes=attrs,
        ))

    def message(self, m, _depth: int = 0) -> Message:
        """SbMessage → telethon Message, привязанный к клиенту пространства."""
        chat = m._chat
        text = m._text or ""
        entities = []
        if text and m._html:
            try:
                text, entities = _html.parse(text)
            except Exception:
                pass
        sender = m.sender
        msg = types.Message(                                       # патченный класс Telethon (с to_dict и т. п.)
            id=m.id, peer_id=self.peer(chat), date=self._date(m.date), message=text, out=bool(m.out),
            mentioned=bool(getattr(m, "mentioned", False)), post=bool(getattr(m, "post", False)),
            pinned=bool(getattr(m, "pinned", False)),
            # как в Telegram: у входящих в личке from_id нет (отправитель — сам чат), у постов канала — тоже
            from_id=types.PeerUser(sender.id) if sender is not None and chat.type not in ("channel", "private")
            or (sender is not None and chat.type == "private" and m.out) else None,
            reply_to=types.MessageReplyHeader(reply_to_msg_id=m._reply_id) if m._reply_id else None,
            media=self.media(m._media), entities=entities or None, via_bot_id=m.via_bot_id,
            edit_date=self._date(m.edit_date),
        )
        msg._sb = m
        ents = {}
        chat_entity = self.entity(chat)
        ents[tl_utils.get_peer_id(chat_entity)] = chat_entity
        if sender is not None:
            u = self.user(sender)
            ents[u.id] = u
        if m.via_bot_id:
            bot = self.s.bot_user
            ents[bot.id] = self.user(bot)
        msg._finish_init(self.client, ents, None)
        self._recent[m.id] = chat
        if len(self._recent) > 5000:
            for key in list(self._recent)[:1000]:
                self._recent.pop(key, None)
        if m._reply_id and _depth == 0:
            ref = self.s.find_message(chat, m._reply_id)
            if ref is not None and not ref.deleted:
                msg._reply_message = self.message(ref, _depth + 1)
        return msg

    def post(self, chat, html, *, reply_to=None, media=None):
        """Сообщение от вашего аккаунта в ленту песочницы (как send_message)."""
        if chat.side not in ("user", "both"):
            raise ValueError(f"Чат «{chat.title}» виден только боту")
        m = self.sb.SbMessage(self.s, chat, self.s.owner, html or "", out=True, reply_to=reply_to, media=media, html=True)
        what = (html or "") or (media or {}).get("name") or ""
        self.note("send", f"{'reply' if reply_to else 'send_message'} → «{chat.title}»: {self.sb.preview(what, 140)}")
        self.s._loopback(m)
        return m

    def media_from(self, file, *, voice_note=False, video_note=False, force_document=False):
        """file= модуля (путь, bytes, BytesIO, ссылка, Message, документ) → вложение песочницы."""
        sb = self.sb
        if isinstance(file, Message):
            sbm = getattr(file, "_sb", None)
            if sbm is not None and sbm._media:
                return dict(sbm._media, id=uuid.uuid4().hex[:12])
            file = file.media
        name, data = None, None
        if isinstance(file, (bytes, bytearray)):
            data, name = bytes(file), "file"
        elif hasattr(file, "read"):
            with contextlib.suppress(Exception):
                if hasattr(file, "seek"):
                    file.seek(0)
                data = file.read()
                if isinstance(data, str):
                    data = data.encode("utf-8")
            name = Path(str(getattr(file, "name", "") or "file")).name
        elif isinstance(file, (str, os.PathLike)):
            text = str(file)
            if text.startswith(("http://", "https://")):
                name = text.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or "file"
            else:
                path = Path(text)
                name = path.name or "file"
                if path.is_file():
                    with contextlib.suppress(Exception):
                        data = path.read_bytes()[: 50 * 1024 * 1024]
        else:
            name = {types.MessageMediaPhoto: "photo.jpg", types.Photo: "photo.jpg"}.get(type(file), "file")
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        kind = sb._EXT_KIND.get(ext, "document")
        if voice_note:
            kind = "voice"
        elif video_note:
            kind = "video_note"
        elif force_document:
            kind = "document"
        return sb.make_media(kind, name, content=data)

    async def html_of(self, client, text, parse_mode=(), formatting_entities=None) -> str:
        """Текст модуля (в любой разметке) → HTML для ленты Sandbox."""
        if text is None:
            return None
        if not isinstance(text, str):
            text = str(text)
        if formatting_entities is not None:
            plain, entities = text, formatting_entities
        else:
            plain, entities = await client._parse_message_text(text, parse_mode)
        try:
            return _html.unparse(plain, entities or [])
        except Exception:
            return self.sb._html.escape(plain)

    def create_chat(self, title, *, broadcast=False):
        """CreateChannelRequest: чат появляется только в этой песочнице (в список участников и чатов не сохраняется)."""
        s = self.s
        n = 1
        while f"h:{n}" in s.chats:
            n += 1
        cid = -(1001000000500 + n)
        while any(c.id == cid for c in s.chats.values()):
            cid -= 1
        chat = self.sb.SbChat(f"h:{n}", cid, "channel" if broadcast else "supergroup", (title or "")[:64] or f"Чат {n}",
                              "user" if broadcast else "both")
        s.chats[chat.key] = chat
        s.touch_state()
        self.note("info", f"создан {'канал' if broadcast else 'чат'} «{chat.title}» (только в этой песочнице)")
        return chat

    # ------------------------------------------------------------------ события песочницы → пространство
    async def hikka_dispatch(self, event):
        """Сообщение в песочнице → UpdateNewMessage → обработчики Telethon пространства (команды, вотчеры…)."""
        m = getattr(event, "message", None)
        if m is None or getattr(m, "deleted", False):
            return
        client = self.client
        msg = self.message(m)
        if m._chat.type in ("supergroup", "channel"):
            update = types.UpdateNewChannelMessage(message=msg, pts=0, pts_count=0)
        else:
            update = types.UpdateNewMessage(message=msg, pts=0, pts_count=0)
        ents = {}
        chat_entity = self.entity(m._chat)
        ents[tl_utils.get_peer_id(chat_entity)] = chat_entity
        if m.sender is not None:
            ents[m.sender.id] = self.user(m.sender)
        update._entities = ents
        await client._dispatch_update(update)

    async def hikka_bot_message(self, m):
        """Сообщение боту в песочнице → aiogram_watcher модулей Hikka."""
        inline = self.space.inline if self.space else None
        if inline is None or not inline.init_complete or m.sender is None or m.sender.id == self.s.bot_user.id:
            return
        chat = m._chat
        bot = self.bot

        async def answer(text, **kw):
            return await bot.send_message(chat_id=chat.id, text=text, reply_markup=kw.get("reply_markup"))

        async def reply(text, **kw):
            return await bot.send_message(chat_id=chat.id, text=text, reply_markup=kw.get("reply_markup"),
                                          reply_to_message_id=m.id)

        message = pytypes.SimpleNamespace(
            message_id=m.id, text=m._text or None, caption=None, date=m.date,
            chat=pytypes.SimpleNamespace(id=chat.id, type="private" if chat.type == "private" else chat.type,
                                         title=chat.title, username=chat.username),
            from_user=_ag_user(m.sender), reply_to_message=None, answer=answer, reply=reply,
            photo=None, document=None, sticker=None, voice=None, video=None,
        )
        await inline._message_handler(message)

    def _owns_callback(self, data) -> bool:
        inline = self.space.inline if self.space else None
        if inline is None:
            return False
        data = str(data)
        if data in inline._custom_map:
            return True
        return any(
            isinstance(b, dict) and b.get("_callback_data") == data
            for unit in inline._units.values() for row in (unit.get("buttons") or []) for b in row
        )

    async def hikka_bot_button(self, chat_key, sender_id, data, message_id=None):
        """Кнопка под сообщением, которое отправил сам бот (inline.bot.send_message(..., reply_markup=…))."""
        chat = self.s.chats.get(chat_key)
        user = self.s.users.get(sender_id) or self.s.owner
        msg = pytypes.SimpleNamespace(
            chat=pytypes.SimpleNamespace(id=chat.id if chat else sender_id, type="private"),
            message_id=message_id,
        )
        call = _Call(self, str(data), user, None, None, message=msg)
        await self.space.inline._route(pytypes.SimpleNamespace(callback_query=call))

    # ------------------------------------------------------------------ инлайн: запрос, форма, кнопки
    async def inline_query(self, bot, query, entity=None):
        inline = self.space.inline if self.space else None
        name = (str(bot).lstrip("@").lower() if isinstance(bot, str) else None)
        if inline is None or not inline.init_complete:
            raise RuntimeError("Инлайн-бот пространства не подключён (в Sandbox выключен бот?)")
        if name and name not in ((inline.bot_username or "").lower(), str(inline.bot_id)):
            raise self.unsupported(f"inline_query к @{name} (в Sandbox есть только бот FRX)")
        iq = _InlineQuery(self, query)
        handled = await inline._route(pytypes.SimpleNamespace(inline_query=iq))
        if not handled:
            self.note("info", f"инлайн-запрос «{self.sb.preview(query, 60)}»: пространство его не обработало")
        return helpers.TotalList([_InlineResult(self, r, query) for r in iq.results])

    def rows(self, markup):
        """aiogram InlineKeyboardMarkup → кнопки формы FRX: (текст, данные) / ('…', 'url:…') / InputButton."""
        from core.inline_forms import InputButton

        rows = []
        for row in getattr(markup, "inline_keyboard", None) or []:
            line = []
            for b in row:
                text = getattr(b, "text", "") or "­"
                style = getattr(b, "style", None)
                if getattr(b, "callback_data", None):
                    line.append((text, b.callback_data, style) if style in ("primary", "success", "danger") else (text, b.callback_data))
                elif getattr(b, "url", None):
                    line.append((text, "url:" + b.url))
                elif getattr(b, "switch_inline_query_current_chat", None) is not None:
                    prefix = (b.switch_inline_query_current_chat or "").strip()
                    line.append(InputButton(text, prefix or "query", hint="Введите значение"))
                elif getattr(b, "copy_text", None) is not None:
                    line.append((text, "url:about:blank#copy"))
                elif getattr(b, "web_app", None) is not None:
                    line.append((text, "url:" + getattr(b.web_app, "url", "about:blank")))
                elif getattr(b, "switch_inline_query", None) is not None:
                    line.append((text, "url:about:blank#switch_inline_query"))
            if line:
                rows.append(line)
        return rows

    @staticmethod
    def content(result):
        """Текст инлайн-результата aiogram (статья, фото, гифка, документ…) для формы Sandbox."""
        text = None
        imc = getattr(result, "input_message_content", None)
        if imc is not None:
            text = getattr(imc, "message_text", None)
        if text is None:
            text = getattr(result, "caption", None)
        media = None
        for attr, label in (("photo_url", "🖼 фото"), ("gif_url", "🎞 GIF"), ("video_url", "🎬 видео"),
                            ("document_url", "📄 файл"), ("audio_url", "🎵 аудио"), ("mpeg4_url", "🎞 GIF")):
            url = getattr(result, attr, None)
            if url:
                media = (label, url)
                break
        if getattr(result, "latitude", None) is not None:
            media = ("📍 место", f"https://maps.google.com/?q={result.latitude},{result.longitude}")
        if media:
            url = str(media[1])
            link = f'<a href="{_html.escape(url)}">{media[0]}</a>' if url.startswith(("http://", "https://")) else media[0]
            text = link + ("\n\n" + text if text else "")
        if not text:
            text = _html.escape(getattr(result, "title", None) or "­")
        return text

    async def show_form(self, chat, text, buttons, reply_to=None):
        """Нарисовать «инлайн-сообщение» формой Sandbox и подключить к ней кнопки и ввод."""
        form = await self.engine.create_inlineform(
            chat.id, text, buttons or None, for_user=[-1], reply_to=reply_to if reply_to else False
        )
        bridge = self

        @form.on_click()
        async def hikka_button(click):
            await bridge.on_click(form, click)

        @form.on_input()
        async def hikka_input(inp):
            await bridge.on_input(form, inp)

        self.forms[form.inline_message_id] = form
        return form

    async def on_click(self, form, click):
        call = _Call(self, click.data, click.from_user, form.inline_message_id, click)
        consumed = await self.space.inline._route(pytypes.SimpleNamespace(callback_query=call))
        if not consumed and not click._answered:
            await click.answer()

    async def on_input(self, form, inp):
        user = inp.from_user or self.s.owner
        chosen = pytypes.SimpleNamespace(
            result_id="sandbox-input", query=f"{inp.data} {inp.value}", inline_message_id=form.inline_message_id,
            from_user=_ag_user(user), location=None,
        )
        await self.space.inline._route(pytypes.SimpleNamespace(chosen_inline_result=chosen))

    def form_message(self, imid):
        form = self.forms.get(imid)
        if form is None or getattr(form, "_closed", False):
            return None
        return form


class _Call:
    """Нажатие кнопки в Sandbox в виде aiogram CallbackQuery."""

    def __init__(self, bridge, data, user, inline_message_id, click, message=None):
        self.id = uuid.uuid4().hex
        self.data = data
        self.from_user = _ag_user(user)
        self.inline_message_id = inline_message_id
        self.message = message
        self.chat_instance = "sandbox"
        self.game_short_name = None
        self._bridge = bridge
        self._click = click

    async def answer(self, text=None, show_alert=False, url=None, cache_time=None, **_):
        if self._click is not None:
            await self._click.answer(text, show_alert=bool(show_alert))
        elif text:
            self._bridge.s.toast(text, "error" if show_alert else "info")
        return True


class _InlineQuery:
    """Инлайн-запрос к боту из песочницы (aiogram InlineQuery): ответ собирается в .results."""

    def __init__(self, bridge, query):
        self.id = uuid.uuid4().hex
        self.query = query or ""
        self.offset = ""
        self.chat_type = "sender"
        self.location = None
        self.from_user = _ag_user(bridge.s.owner)
        self.results = []
        self.answered = False

    async def answer(self, results=None, *args, **kwargs):
        self.results = list(results or [])
        self.answered = True
        return True


class _InlineResult:
    """Результат инлайн-запроса (как telethon InlineResult): click() отправляет его в чат песочницы."""

    def __init__(self, bridge, result, query):
        self._bridge = bridge
        self.result = result
        self.query = query
        self.title = getattr(result, "title", None)
        self.description = getattr(result, "description", None)
        self.type = getattr(result, "type", None)
        self.url = getattr(result, "url", None)
        self.id = getattr(result, "id", None)

    async def click(self, entity=None, reply_to=None, *_, **__):
        bridge = self._bridge
        chat = bridge.chat_of(entity if entity is not None else "me")
        reply_id = tl_utils.get_message_id(reply_to) if reply_to is not None else None
        text = bridge.content(self.result)
        form = await bridge.show_form(chat, text, bridge.rows(getattr(self.result, "reply_markup", None)), reply_id)
        chosen = pytypes.SimpleNamespace(
            result_id=self.id, query=self.query, inline_message_id=form.inline_message_id,
            from_user=_ag_user(bridge.s.owner), location=None,
        )
        await bridge.space.inline._route(pytypes.SimpleNamespace(chosen_inline_result=chosen))
        return bridge.message(form._msg) if getattr(form, "_msg", None) is not None else None

    def __repr__(self):
        return f"<InlineResult {self.title or self.id!r}>"


# ====================================================================================================== клиент
class SandboxTelegramClient(TelegramClient):
    """TelegramClient без сети: всё, что модули Hikka делают с сообщениями, происходит в ленте Sandbox.

    Методы вызываются у «вида» пространства (подкласс CustomTelegramClient → этот класс), поэтому `self` —
    вид, а мост песочницы — self._sbx (общий __dict__).
    """

    def __init__(self, bridge):
        super().__init__(StringSession(), 1, "0" * 32, receive_updates=False)
        self._sbx = bridge
        owner = bridge.s.owner
        with contextlib.suppress(Exception):
            self._mb_entity_cache.set_self_user(owner.id, False, 0)
        with contextlib.suppress(Exception):
            self._self_input_peer = types.InputPeerUser(owner.id, 0)

    # ------------------------------------------------------------------ соединение
    def is_connected(self):
        return True

    async def connect(self):
        return True

    async def disconnect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_me(self, input_peer: bool = False):
        owner = self._sbx.s.owner
        return types.InputPeerUser(owner.id, 0) if input_peer else self._sbx.user(owner)

    # ------------------------------------------------------------------ сущности
    async def get_input_entity(self, peer):
        if peer in ("me", "self") or isinstance(peer, (types.InputPeerSelf, types.InputUserSelf)):
            return types.InputPeerSelf()
        if isinstance(peer, (types.InputPeerUser, types.InputPeerChannel, types.InputPeerChat)):
            return peer
        return tl_utils.get_input_peer(await self.get_entity(peer))

    async def get_entity(self, entity, *args, **kwargs):
        bridge = self._sbx
        if isinstance(entity, (list, tuple)):
            return [await SandboxTelegramClient.get_entity(self, e) for e in entity]
        s = bridge.s
        if isinstance(entity, int) and entity in s.users:
            return bridge.user(s.users[entity])
        if isinstance(entity, types.PeerUser) and entity.user_id in s.users:
            return bridge.user(s.users[entity.user_id])
        if isinstance(entity, (types.InputPeerUser, types.InputUser)) and entity.user_id in s.users:
            return bridge.user(s.users[entity.user_id])
        if isinstance(entity, str):
            name = entity.strip().lstrip("@").lower()
            for u in s.users.values():
                if u.username and u.username.lower() == name:
                    return bridge.user(u)
        if isinstance(entity, (types.User, types.Channel, types.Chat)):
            return entity
        try:
            chat = bridge.chat_of(entity)
        except Exception as e:
            raise ValueError(f'Cannot find any entity corresponding to "{entity}"') from e
        if chat.type == "private" and chat.peer in s.users:
            return bridge.user(s.users[chat.peer])
        return bridge.entity(chat)

    async def get_permissions(self, entity, user=None):
        from telethon.tl.custom.participantpermissions import ParticipantPermissions

        bridge = self._sbx
        chat = bridge.chat_of(entity)
        if user is None:
            user_id = bridge.s.owner.id
        elif isinstance(user, int):
            user_id = user
        else:
            user_id = getattr(await SandboxTelegramClient.get_entity(self, user), "id", None)
        if chat.type == "private":
            return None
        if user_id == bridge.s.owner.id:
            participant = types.ChannelParticipantCreator(
                user_id=user_id,
                admin_rights=types.ChatAdminRights(
                    change_info=True, post_messages=True, edit_messages=True, delete_messages=True, ban_users=True,
                    invite_users=True, pin_messages=True, add_admins=True, manage_call=True, other=True,
                ),
            )
        else:
            participant = types.ChannelParticipant(user_id=user_id, date=_EPOCH)
        return ParticipantPermissions(participant, chat.type == "group")

    # ------------------------------------------------------------------ отправка и правка
    async def send_message(self, entity, message="", *, reply_to=None, parse_mode=(), formatting_entities=None,
                           link_preview=True, file=None, buttons=None, **kwargs):
        bridge = self._sbx
        if file is not None:
            return await self.send_file(entity, file, caption=message, reply_to=reply_to, parse_mode=parse_mode,
                                        formatting_entities=formatting_entities, **kwargs)
        chat = bridge.chat_of(entity)
        media = None
        if isinstance(message, Message):
            sbm = getattr(message, "_sb", None)
            media = dict(sbm._media, id=uuid.uuid4().hex[:12]) if sbm is not None and sbm._media else None
            html = _html.unparse(message.message or "", message.entities or [])
        else:
            html = await bridge.html_of(self, message or "", parse_mode, formatting_entities)
        if buttons:
            bridge.note("info", "buttons= у сообщения от аккаунта в Sandbox не показываются (как и в Telegram, "
                                "кнопки бывают только у ботов — модули Hikka используют для этого self.inline)")
        sbm = bridge.post(chat, html, reply_to=tl_utils.get_message_id(reply_to) if reply_to else None, media=media)
        return bridge.message(sbm)

    async def send_file(self, entity, file, *, caption=None, reply_to=None, parse_mode=(), formatting_entities=None,
                        voice_note=False, video_note=False, force_document=False, **kwargs):
        bridge = self._sbx
        chat = bridge.chat_of(entity)
        files = file if isinstance(file, (list, tuple)) else [file]
        captions = caption if isinstance(caption, (list, tuple)) else [caption]
        sent = []
        for i, item in enumerate(files):
            text = captions[i] if i < len(captions) else None
            html = await bridge.html_of(self, text or "", parse_mode, formatting_entities if i == 0 else None) if text else ""
            media = bridge.media_from(item, voice_note=voice_note, video_note=video_note, force_document=force_document)
            sbm = bridge.post(chat, html, reply_to=tl_utils.get_message_id(reply_to) if reply_to else None, media=media)
            sent.append(bridge.message(sbm))
        return sent if isinstance(file, (list, tuple)) else sent[0]

    async def edit_message(self, entity, message=None, text=None, *, parse_mode=(), formatting_entities=None,
                           link_preview=True, file=None, buttons=None, **kwargs):
        bridge = self._sbx
        if isinstance(entity, Message):
            text, message, entity = message, entity, None
        if isinstance(entity, (types.InputBotInlineMessageID, types.InputBotInlineMessageID64)) or isinstance(entity, str) and entity.startswith("sb:"):
            raise bridge.unsupported("Правка инлайн-сообщения от имени аккаунта")
        if entity is None:
            sbm = getattr(message, "_sb", None)
        else:
            sbm = bridge.find(bridge.chat_of(entity), message)
        if sbm is None:
            raise bridge.sb.SbError("MessageIdInvalidError: сообщение не найдено")
        html = await bridge.html_of(self, text, parse_mode, formatting_entities) if text is not None else None
        await sbm.edit(html, parse_mode="html")
        if file is not None:
            sbm._media = bridge.media_from(file)
            sbm._publish()
        return bridge.message(sbm)

    async def delete_messages(self, entity, message_ids, *, revoke=True):
        bridge = self._sbx
        ids = message_ids if isinstance(message_ids, (list, tuple)) else [message_ids]
        chat = bridge.chat_of(entity) if entity is not None else None
        count = 0
        for item in ids:
            sbm = getattr(item, "_sb", None)
            if sbm is None and chat is not None:
                sbm = bridge.find(chat, item)
            if sbm is None or sbm.deleted:
                continue
            form_id = getattr(sbm, "_form_id", None)
            if form_id:
                form = bridge.forms.pop("sb:" + form_id, None) or bridge.s.forms.get(form_id)
                if form is not None:
                    with contextlib.suppress(Exception):
                        await form.close(remove_buttons=True)
                    bridge.s.forms.pop(form_id, None)
            await sbm.delete()
            count += 1
        return [types.messages.AffectedMessages(pts=0, pts_count=count)]

    async def forward_messages(self, entity, messages, from_peer=None, **kwargs):
        bridge = self._sbx
        chat = bridge.chat_of(entity)
        source = bridge.chat_of(from_peer) if from_peer is not None else None
        items = messages if isinstance(messages, (list, tuple)) else [messages]
        out = []
        for item in items:
            sbm = getattr(item, "_sb", None) or (bridge.find(source, item) if source is not None else None)
            if sbm is not None:
                out.append(bridge.message(await sbm.forward_to(chat)))
        return out if isinstance(messages, (list, tuple)) else (out[0] if out else None)

    async def pin_message(self, entity, message=None, *, notify=False, pm_oneside=False):
        bridge = self._sbx
        chat = bridge.chat_of(entity)
        sbm = bridge.find(chat, message)
        if sbm is not None:
            bridge.s.pin(chat, sbm)

    async def unpin_message(self, entity, message=None, *, notify=False):
        bridge = self._sbx
        chat = bridge.chat_of(entity)
        bridge.s.unpin(chat, bridge.find(chat, message) if message else None)

    async def send_read_acknowledge(self, *args, **kwargs):
        return True

    async def download_media(self, message, file=None, *, thumb=None, progress_callback=None, **kwargs):
        bridge = self._sbx
        sbm = getattr(message, "_sb", None)
        if sbm is None and isinstance(message, Message) and message.peer_id is not None:
            with contextlib.suppress(Exception):
                sbm = bridge.find(bridge.chat_of(message.peer_id), message.id)
        md = sbm._media if sbm is not None else None
        if not md:
            return None
        data = md.get("content")
        if data is None:
            data = b"\0" * min(int(md.get("size") or 0), 4096)
        bridge.note("api", f"download_media #{sbm.id}: {md['name']}")
        if file is bytes:
            return bytes(data)
        if file is not None and hasattr(file, "write"):
            file.write(data)
            return file
        if file is None:
            return sbm and await sbm.download_media()
        path = Path(str(file))
        if path.is_dir() or str(file).endswith(("/", os.sep)):
            path.mkdir(parents=True, exist_ok=True)
            path = path / md["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    # ------------------------------------------------------------------ чтение
    def _iter(self, entity, limit=None, *, from_user=None, search=None, reverse=False, ids=None, **kwargs):
        client = self

        class _It:
            _g = None

            def __aiter__(it):
                return it

            async def __anext__(it):
                if it._g is None:
                    it._g = it._gen()
                return await it._g.__anext__()

            async def _gen(it):
                for item in await client.get_messages(entity, limit, from_user=from_user, search=search,
                                                      reverse=reverse, ids=ids, _all=True):
                    if item is not None:
                        yield item

            async def collect(it):
                return await client.get_messages(entity, limit, from_user=from_user, search=search, reverse=reverse,
                                                 ids=ids, _all=True)

            def __await__(it):
                return it.collect().__await__()

        return _It()

    iter_messages = _iter

    async def get_messages(self, entity, limit=None, *, from_user=None, search=None, reverse=False, ids=None,
                           _all=False, **kwargs):
        bridge = self._sbx
        if ids is not None:
            single = not isinstance(ids, (list, tuple))
            result = []
            for item in [ids] if single else ids:
                chat = bridge.chat_of(entity) if entity is not None else None
                if isinstance(item, types.InputMessageReplyTo):
                    chat = chat or bridge._recent.get(item.id)
                    base = bridge.find(chat, item.id) if chat is not None else None
                    sbm = bridge.find(chat, base._reply_id) if base is not None and base._reply_id else None
                else:
                    mid = item if isinstance(item, int) else tl_utils.get_message_id(item)
                    chat = chat or bridge._recent.get(mid)
                    sbm = bridge.find(chat, mid) if chat is not None else None
                result.append(bridge.message(sbm) if sbm is not None and not sbm.deleted else None)
            return result[0] if single else helpers.TotalList(result)
        chat = bridge.chat_of(entity)
        msgs = [m for m in bridge.s.messages_of(chat) if not m.deleted]
        if from_user is not None:
            if from_user in ("me", "self"):
                uid = bridge.s.owner.id
            elif isinstance(from_user, int):
                uid = from_user
            else:
                uid = getattr(await SandboxTelegramClient.get_entity(self, from_user), "id", None)
            msgs = [m for m in msgs if m.sender is not None and m.sender.id == uid]
        if search:
            msgs = [m for m in msgs if str(search).lower() in (m._text or "").lower()]
        msgs = msgs if reverse else list(reversed(msgs))
        if limit is None and not _all:
            limit = 1
        if limit is not None:
            msgs = msgs[: max(0, int(limit))]
        out = helpers.TotalList(bridge.message(m) for m in msgs)
        out.total = len(out)
        return out

    def iter_dialogs(self, limit=None, **kwargs):
        client = self

        class _It:
            _g = None

            def __aiter__(it):
                return it

            async def __anext__(it):
                if it._g is None:
                    it._g = it._gen()
                return await it._g.__anext__()

            async def _gen(it):
                for d in await client.get_dialogs(limit):
                    yield d

            async def collect(it):
                return await client.get_dialogs(limit)

            def __await__(it):
                return it.collect().__await__()

        return _It()

    async def get_dialogs(self, limit=None, **kwargs):
        bridge = self._sbx
        out = helpers.TotalList()
        for chat in bridge.s.chats.values():
            if chat.side not in ("user", "both"):
                continue
            out.append(_Dialog(self, chat))
        out.total = len(out)
        return out[:limit] if limit else out

    async def get_participants(self, entity, limit=None, **kwargs):
        bridge = self._sbx
        chat = bridge.chat_of(entity)
        users = [bridge.s.owner] + ([bridge.s.users[chat.peer]] if chat.type == "private" and chat.peer in bridge.s.users
                                    and chat.peer != bridge.s.owner.id else [])
        if chat.type != "private":
            users = list(bridge.s.users.values())
        out = helpers.TotalList(bridge.user(u) for u in users)
        out.total = len(out)
        return out[:limit] if limit else out

    def iter_participants(self, entity, limit=None, **kwargs):
        client = self

        class _It:
            _g = None

            def __aiter__(it):
                return it

            async def __anext__(it):
                if it._g is None:
                    it._g = it._gen()
                return await it._g.__anext__()

            async def _gen(it):
                for u in await client.get_participants(entity, limit):
                    yield u

            def __await__(it):
                return client.get_participants(entity, limit).__await__()

        return _It()

    # ------------------------------------------------------------------ инлайн-боты
    async def inline_query(self, bot, query, *, entity=None, offset=None, geo_point=None):
        return await self._sbx.inline_query(bot, query, entity)

    # ------------------------------------------------------------------ MTProto
    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        bridge = self._sbx
        if isinstance(request, (list, tuple)):
            return [await self._call(sender, r) for r in request]
        name = type(request).__name__
        if name == "CreateChannelRequest":
            chat = bridge.create_chat(request.title, broadcast=bool(request.broadcast))
            if getattr(request, "forum", False):
                bridge.topics.setdefault(chat.key, [(1, "General")])
            return types.Updates(updates=[], users=[], chats=[bridge.entity(chat)], date=_EPOCH, seq=0)
        if name in ("GetForumTopicsRequest", "GetForumTopicsByIDRequest", "CreateForumTopicRequest", "EditForumTopicRequest"):
            chat = bridge.chat_of(getattr(request, "peer", None) or getattr(request, "channel", None))
            topics = bridge.topics.setdefault(chat.key, [(1, "General")])
            if name == "CreateForumTopicRequest":
                topic_id = max(t[0] for t in topics) + 1
                topics.append((topic_id, request.title))
                bridge.note("info", f"создана тема «{request.title}» в «{chat.title}»")
                return pytypes.SimpleNamespace(updates=[pytypes.SimpleNamespace(id=topic_id)], users=[], chats=[])
            if name == "EditForumTopicRequest":
                return types.Updates(updates=[], users=[], chats=[], date=_EPOCH, seq=0)
            wanted = set(getattr(request, "topics", None) or [])
            found = [pytypes.SimpleNamespace(id=i, title=t, top_message=0) for i, t in topics if not wanted or i in wanted]
            return pytypes.SimpleNamespace(topics=found, messages=[], chats=[], users=[], count=len(found))
        if name in ("GetHistoryRequest", "SearchRequest", "GetMessagesRequest", "GetRepliesRequest"):
            peer = getattr(request, "peer", None) or getattr(request, "channel", None)
            if name == "GetMessagesRequest" and peer is None:
                ids = [getattr(i, "id", i) for i in (request.id or [])]
                msgs = [m for m in await self.get_messages(None, ids=ids) if m is not None]
            else:
                limit = getattr(request, "limit", None) or 100
                msgs = list(await self.get_messages(peer, limit, search=getattr(request, "q", None) or None,
                                                    ids=[getattr(i, "id", i) for i in request.id] if name == "GetMessagesRequest" else None))
                msgs = [m for m in msgs if m is not None]
            users = {u.id: bridge.user(u) for u in bridge.s.users.values()}
            chats = [bridge.entity(c) for c in bridge.s.chats.values() if c.type != "private"]
            kwargs = {"messages": msgs, "chats": chats, "users": list(users.values())}
            if "topics" in getattr(types.messages.Messages.__init__, "__code__").co_varnames:
                kwargs["topics"] = []
            return types.messages.Messages(**kwargs)
        if name == "GetUserPhotosRequest":
            bridge.note("api", "GetUserPhotosRequest: у участников песочницы нет аватарок")
            return types.photos.Photos(photos=[], users=[])
        if name == "SendReactionRequest":
            reaction = ", ".join(getattr(r, "emoticon", None) or str(getattr(r, "document_id", "")) for r in (request.reaction or []))
            bridge.note("send", f"реакция {reaction or '—'} на #{request.msg_id}")
            return types.Updates(updates=[], users=[], chats=[], date=_EPOCH, seq=0)
        if name in ("ReadHistoryRequest", "ReadMentionsRequest", "ReadReactionsRequest", "SetTypingRequest",
                    "UpdateNotifySettingsRequest", "ToggleDialogPinRequest", "EditPeerFoldersRequest", "UpdateStatusRequest",
                    "JoinChannelRequest", "InviteToChannelRequest", "AddChatUserRequest", "EditPhotoRequest",
                    "EditChatPhotoRequest", "EditAdminRequest", "ToggleForumRequest", "MarkDialogUnreadRequest",
                    "SaveDraftRequest", "UpdateDialogFilterRequest", "ReadDiscussionRequest", "ToggleNoForwardsRequest",
                    "BlockRequest", "UnblockRequest", "DeleteHistoryRequest", "LeaveChannelRequest", "DeleteChatUserRequest",
                    "StartBotRequest", "ReportSpamRequest", "UpdateProfileRequest", "UpdateUsernameRequest"):
            bridge.note("api", f"{name} — выполнено условно (в Sandbox Telegram нет)")
            return types.Updates(updates=[], users=[], chats=[], date=_EPOCH, seq=0)
        bridge.note("api", f"client({name}) — MTProto-запрос не выполняется в Sandbox")
        raise bridge.sb.SbUnsupported(f"MTProto-запрос {name} не выполняется в Sandbox (сети Telegram здесь нет)")


class _Dialog:
    """Диалог песочницы (как telethon Dialog)."""

    def __init__(self, client, chat):
        bridge = client._sbx
        self._client, self._chat = client, chat
        self.entity = bridge.entity(chat)
        self.input_entity = tl_utils.get_input_peer(self.entity)
        self.id = chat.id
        self.name = self.title = chat.title
        self.is_user = chat.type == "private"
        self.is_group = chat.type in ("group", "supergroup")
        self.is_channel = chat.type in ("channel", "supergroup")
        self.archived, self.folder_id, self.pinned = False, None, False
        self.unread_count = self.unread_mentions_count = 0
        last = [m for m in bridge.s.messages_of(chat) if not m.deleted]
        self.message = bridge.message(last[-1]) if last else None
        self.date = self.message.date if self.message else _EPOCH
        self.draft = None
        self.dialog = None

    async def send_message(self, *args, **kwargs):
        return await self._client.send_message(self.input_entity, *args, **kwargs)

    async def delete(self, revoke=False):
        bridge = self._client._sbx
        for m in bridge.s.messages_of(self._chat):
            if not m.deleted:
                await m.delete()
        bridge.note("api", f"диалог «{self._chat.title}» очищен (в Sandbox чат остаётся в списке)")

    async def archive(self, folder=1):
        self.archived = bool(folder)
        return True

    def __repr__(self):
        return f"<Dialog {self.name!r}>"


# ====================================================================================================== бот
class SandboxBot:
    """Бот FRX песочницы в виде aiogram Bot для инлайн-менеджера Hikka: правки «инлайн-сообщений» (форм) идут в
    формы Sandbox, остальное — в бота песочницы (engine.bot.raw)."""

    def __init__(self, bridge):
        self._bridge = bridge

    @property
    def _raw(self):
        return self._bridge.engine._aiogram_bot

    @property
    def id(self):
        return self._bridge.s.bot_user.id

    async def get_me(self):
        u = self._bridge.s.bot_user
        return _ag_user(u)

    def _form(self, inline_message_id):
        form = self._bridge.form_message(inline_message_id)
        if form is None:
            raise _bad_request("Bad Request: message to edit not found")
        return form

    async def _edit_form(self, inline_message_id, text=None, reply_markup=None, keep_markup=False):
        form = self._form(inline_message_id)
        buttons = form._raw_buttons if keep_markup else self._bridge.rows(reply_markup)
        await form.edit(form.text if text is None else text, buttons or None)
        return True

    async def edit_message_text(self, text=None, chat_id=None, message_id=None, inline_message_id=None,
                                reply_markup=None, **kwargs):
        if inline_message_id:
            return await self._edit_form(inline_message_id, text, reply_markup)
        return await self._raw.edit_message_text(text, chat_id=chat_id, message_id=message_id, reply_markup=reply_markup)

    async def edit_message_caption(self, chat_id=None, message_id=None, inline_message_id=None, caption=None,
                                   reply_markup=None, **kwargs):
        return await self.edit_message_text(caption, chat_id=chat_id, message_id=message_id,
                                            inline_message_id=inline_message_id, reply_markup=reply_markup)

    async def edit_message_reply_markup(self, chat_id=None, message_id=None, inline_message_id=None,
                                        reply_markup=None, **kwargs):
        if inline_message_id:
            return await self._edit_form(inline_message_id, None, reply_markup)
        bridge = self._bridge
        chat = bridge.s.resolve_chat(chat_id, "bot")
        m = bridge.s.find_message(chat, message_id)
        if m is None:
            raise _bad_request("Bad Request: message to edit not found")
        m._buttons = bridge.sb.markup_to_rows(reply_markup)
        m._publish()
        return True

    async def edit_message_media(self, media, chat_id=None, message_id=None, inline_message_id=None,
                                 reply_markup=None, **kwargs):
        caption = getattr(media, "caption", None) or ""
        source = getattr(media, "media", None)
        label = {"InputMediaPhoto": "🖼 фото", "InputMediaAnimation": "🎞 GIF", "InputMediaVideo": "🎬 видео",
                 "InputMediaAudio": "🎵 аудио", "InputMediaDocument": "📄 файл"}.get(type(media).__name__, "📎 медиа")
        url = source if isinstance(source, str) else getattr(source, "url", None)
        head = f'<a href="{_html.escape(url)}">{label}</a>' if isinstance(url, str) and url.startswith("http") else label
        return await self.edit_message_text(head + ("\n\n" + caption if caption else ""), chat_id=chat_id,
                                            message_id=message_id, inline_message_id=inline_message_id,
                                            reply_markup=reply_markup)

    async def delete_message(self, chat_id, message_id, **kwargs):
        return await self._raw.delete_message(chat_id, message_id)

    async def answer_callback_query(self, callback_query_id=None, text=None, show_alert=False, **kwargs):
        if text:
            self._bridge.s.toast(text, "error" if show_alert else "info")
        return True

    async def answer_inline_query(self, *args, **kwargs):
        return True

    async def send_message(self, chat_id, text, reply_markup=None, **kwargs):
        reply_to = kwargs.get("reply_to_message_id") or getattr(kwargs.get("reply_parameters"), "message_id", None)
        return await self._raw.send_message(chat_id, text, reply_markup=reply_markup, reply_to_message_id=reply_to,
                                            parse_mode=kwargs.get("parse_mode"))

    def __getattr__(self, item):
        if item.startswith("__") or item == "_bridge":
            raise AttributeError(item)
        return getattr(self._raw, item)
