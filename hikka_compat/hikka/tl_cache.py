"""Клиент Telegram для модулей Hikka.

Модули Hikka ждут клиента их форка Telethon (Hikka-TL / Heroku-TL): HTML по умолчанию, client.tg_id,
client.hikka_me, client.loader, get_entity(..., exp=, force=) с кэшем, send_reaction, translate и т. п.

FRX работает на обычном Telethon, и ставить форк нельзя — он подменит пакет telethon. Поэтому здесь —
«вид» на настоящий клиент FRX: объект подкласса TelegramClient, у которого ОБЩЕЕ состояние (__dict__)
с клиентом FRX (то же соединение, сессия, обработчики), но свои значения по умолчанию и свои методы.
На сам клиент FRX ничего не влияет: его parse_mode и поведение остаются прежними.
"""

import asyncio
import contextlib
import copy
import functools
import inspect
import logging
import time
import typing

from telethon import TelegramClient, events, utils as tl_utils
from telethon.extensions import html as _tl_html
from telethon.tl import functions, types
from telethon.tl.custom.message import Message

from . import _html
from .types import CacheRecordEntity, CacheRecordFullChannel, CacheRecordFullUser, CacheRecordPerms

logger = logging.getLogger(__name__)


def hashable(value) -> bool:
    try:
        hash(value)
        return True
    except TypeError:
        return False


class _State:
    """Собственное (не общее с клиентом FRX) состояние вида."""

    def __init__(self, real, space):
        self.real = real
        self.space = space
        self.parse_mode = _html
        self.me = None
        self.tg_id = None
        self.handlers: dict = {}              # (id(callback), event type) -> обёртка
        self.entity_cache: dict = {}
        self.perms_cache: dict = {}
        self.fullchannel_cache: dict = {}
        self.fulluser_cache: dict = {}
        self.forbidden: list = []
        self.extra: dict = {}                 # loader / dispatcher / db / inline


def _shallow(obj):
    """Поверхностная копия без copy.copy (у событий Telethon __getattr__ уходит в рекурсию при копировании)."""
    new = object.__new__(type(obj))
    new.__dict__.update(obj.__dict__)
    return new


def _rebind_message(message, client, _depth: int = 0):
    m = _shallow(message)
    m.__dict__.pop("_hk_fixed", None)
    m._client = client
    m._text = None                              # текст пересчитается в HTML пространства
    if m.entities:
        m.entities = [copy.copy(e) for e in m.entities]
    reply = m.__dict__.get("_reply_message")
    if isinstance(reply, Message):
        m._reply_message = _rebind_message(reply, client, _depth + 1) if _depth < 2 else None
    _tl_compat.fix_message(m)                   # from_id как в Hikka-TL / Heroku-TL
    return m


def rebind(obj, client):
    """Копия события/сообщения, привязанная к виду клиента (оригинал, который видит FRX, не трогаем)."""
    if obj is None:
        return obj
    try:
        if isinstance(obj, Message):
            return _rebind_message(obj, client)
        new = _shallow(obj)
    except Exception:
        return obj
    with contextlib.suppress(Exception):
        object.__setattr__(new, "_client", client)
    inner = new.__dict__.get("message") if hasattr(new, "__dict__") else None
    if isinstance(inner, Message):
        with contextlib.suppress(Exception):
            new.__dict__["message"] = _rebind_message(inner, client)
    return new


def _key(entity):
    if not hashable(entity):
        for attr in ("user_id", "channel_id", "chat_id", "id"):
            value = getattr(entity, attr, None)
            if value:
                return value
        return None
    if isinstance(entity, str) and entity.lstrip("-").isdigit():
        entity = int(entity)
    if isinstance(entity, int) and str(entity).startswith("-100"):
        entity = int(str(entity)[4:])
    return entity


class CustomTelegramClient(TelegramClient):
    """Подкласс-«вид». Создаётся только через CustomTelegramClient.view(real, space)."""

    _hk: _State = None

    @classmethod
    def view(cls, real: TelegramClient, space, base: type = None) -> "CustomTelegramClient":
        """base — класс, методы которого идут после методов вида (в Sandbox — клиент песочницы)."""
        state = _State(real, space)
        bases = (cls, base) if base is not None and base is not TelegramClient else (cls,)
        sub = type("HikkaTelegramClient", bases, {"_hk": state, "__module__": cls.__module__})
        obj = object.__new__(sub)
        obj.__dict__ = real.__dict__           # одно состояние на двоих: соединение, сессия, обработчики
        return obj

    # ------------------------------------------------------------------ атрибуты форка
    def _x(name):  # noqa: N805 — фабрика свойств
        def getter(self):
            return self._hk.extra.get(name)

        def setter(self, value):
            self._hk.extra[name] = value

        return property(getter, setter)

    loader = _x("loader")
    dispatcher = _x("dispatcher")
    hikka_db = heroku_db = _x("db")
    hikka_inline = heroku_inline = _x("inline")
    allclients = _x("allclients")
    del _x

    @property
    def tg_id(self):
        return self._hk.tg_id

    @tg_id.setter
    def tg_id(self, value):
        self._hk.tg_id = value

    _tg_id = tg_id

    @property
    def hikka_me(self):
        return self._hk.me

    @hikka_me.setter
    def hikka_me(self, value):
        self._hk.me = value

    heroku_me = hikka_me

    @property
    def real_client(self) -> TelegramClient:
        return self._hk.real

    @property
    def parse_mode(self):
        return self._hk.parse_mode

    @parse_mode.setter
    def parse_mode(self, mode):
        if isinstance(mode, str) and mode.lower() in ("html", "htm"):
            self._hk.parse_mode = _html
        else:
            self._hk.parse_mode = tl_utils.sanitize_parse_mode(mode)

    @property
    def hikka_entity_cache(self):
        return self._hk.entity_cache

    heroku_entity_cache = hikka_entity_cache

    @property
    def hikka_perms_cache(self):
        return self._hk.perms_cache

    heroku_perms_cache = hikka_perms_cache

    @property
    def hikka_fullchannel_cache(self):
        return self._hk.fullchannel_cache

    heroku_fullchannel_cache = hikka_fullchannel_cache

    @property
    def hikka_fulluser_cache(self):
        return self._hk.fulluser_cache

    heroku_fulluser_cache = hikka_fulluser_cache

    @property
    def forbidden_constructors(self):
        return self._hk.forbidden

    def forbid_constructor(self, constructor: int):
        self._hk.forbidden = list(set(self._hk.forbidden + [constructor]))

    def forbid_constructors(self, constructors: list):
        self._hk.forbidden = list(set(self._hk.forbidden + list(constructors)))

    _internal_forbid_ctor = forbid_constructors

    @property
    def raw_updates_processor(self):
        return self._hk.extra.get("raw_updates_processor")

    @raw_updates_processor.setter
    def raw_updates_processor(self, value):
        self._hk.extra["raw_updates_processor"] = value

    # ------------------------------------------------------------------ разметка по умолчанию — HTML пространства
    async def _parse_message_text(self, message, parse_mode):
        if parse_mode == ():
            parse_mode = self._hk.parse_mode
        elif isinstance(parse_mode, str) and parse_mode.lower() in ("html", "htm"):
            parse_mode = _html
        elif parse_mode is _tl_html:
            parse_mode = _html
        return await super(CustomTelegramClient, self)._parse_message_text(message, parse_mode)

    # ------------------------------------------------------------------ обработчики событий модулей
    def add_event_handler(self, callback, event=None):
        client = self

        @functools.wraps(callback)
        async def wrapper(update, *args, **kwargs):
            result = callback(rebind(update, client), *args, **kwargs)
            if asyncio.iscoroutine(result) or isinstance(result, asyncio.Future):
                return await result
            return result

        wrapper.__hikka_original__ = callback
        self._hk.handlers.setdefault(id(callback), []).append(wrapper)
        space = self._hk.space
        if space is not None:
            space.track_handler(wrapper)
        return super(CustomTelegramClient, self).add_event_handler(wrapper, event)

    def remove_event_handler(self, callback, event=None) -> int:
        wrappers = self._hk.handlers.get(id(callback), []) if callback is not None else []
        found = 0
        for wrapper in list(wrappers) or [callback]:
            n = super(CustomTelegramClient, self).remove_event_handler(wrapper, event)
            found += n
            if n and wrapper in wrappers and not event:
                wrappers.remove(wrapper)
        if not wrappers:
            self._hk.handlers.pop(id(callback), None)
        return found

    def list_event_handlers(self):
        return [
            (getattr(cb, "__hikka_original__", cb), ev)
            for cb, ev in super(CustomTelegramClient, self).list_event_handlers()
        ]

    def on(self, event):
        def decorator(f):
            self.add_event_handler(f, event)
            return f

        return decorator

    # ------------------------------------------------------------------ кэш сущностей
    async def get_entity(self, entity, exp: int = 5 * 60, force: bool = False):
        if isinstance(entity, (list, tuple)):
            return await super(CustomTelegramClient, self).get_entity(entity)
        key = _key(entity)
        if key is None:
            return await super(CustomTelegramClient, self).get_entity(entity)
        cache = self._hk.entity_cache
        record = cache.get(key)
        if not force and record is not None and (not exp or record.ts + exp > time.time()):
            return copy.deepcopy(record.entity)
        resolved = await super(CustomTelegramClient, self).get_entity(entity)
        if resolved:
            record = CacheRecordEntity(key, resolved, exp)
            cache[key] = record
            if getattr(resolved, "id", None):
                cache[resolved.id] = record
            username = getattr(resolved, "username", None)
            if username:
                cache[f"@{username}"] = record
                cache[username] = record
        return copy.deepcopy(resolved)

    async def force_get_entity(self, *args, **kwargs):
        return await self.get_entity(*args, force=True, **kwargs)

    async def get_perms_cached(self, entity, user=None, exp: int = 5 * 60, force: bool = False):
        ekey, ukey = _key(entity), _key(user)
        cache = self._hk.perms_cache
        key = (ekey, ukey)
        record = cache.get(key)
        if not force and record is not None and (not exp or record.ts + exp > time.time()):
            return copy.deepcopy(record.perms)
        perms = await self.get_permissions(entity, user)
        cache[key] = CacheRecordPerms(ekey, ukey, perms, exp)
        return copy.deepcopy(perms)

    async def get_fullchannel(self, entity, exp: int = 300, force: bool = False):
        key = _key(entity)
        cache = self._hk.fullchannel_cache
        record = cache.get(key)
        if not force and record is not None and (not exp or record.ts + exp > time.time()):
            return record.full_channel
        full = await self(functions.channels.GetFullChannelRequest(await self.get_input_entity(entity)))
        cache[key] = CacheRecordFullChannel(key, full, exp)
        return full

    async def get_fulluser(self, entity, exp: int = 300, force: bool = False):
        key = _key(entity)
        cache = self._hk.fulluser_cache
        record = cache.get(key)
        if not force and record is not None and (not exp or record.ts + exp > time.time()):
            return record.full_user
        full = await self(functions.users.GetFullUserRequest(await self.get_input_entity(entity)))
        cache[key] = CacheRecordFullUser(key, full, exp)
        return full

    # ------------------------------------------------------------------ методы форков
    @staticmethod
    def _reaction(reaction):
        if reaction is None:
            return None
        out = []
        for r in reaction if isinstance(reaction, (list, tuple)) else [reaction]:
            if isinstance(r, int):
                out.append(types.ReactionCustomEmoji(document_id=r))
            elif isinstance(r, str):
                out.append(types.ReactionEmoji(emoticon=r))
            else:
                out.append(r)
        return out

    async def send_reaction(self, entity, message, reaction=None, big: bool = False, add_to_recent: bool = False,
                            keep_existing: bool = False):
        msg_id = tl_utils.get_message_id(message) or 0
        reactions = self._reaction(reaction)
        if reactions is None:
            reactions = [types.ReactionEmoji(emoticon="👍")]
        if keep_existing and isinstance(message, Message):
            results = getattr(getattr(message, "reactions", None), "results", None) or []
            chosen = sorted((r for r in results if getattr(r, "chosen_order", None) is not None), key=lambda r: r.chosen_order)
            reactions = [r.reaction for r in chosen] + reactions
        return await self(
            functions.messages.SendReactionRequest(
                peer=entity, msg_id=msg_id, big=big, add_to_recent=add_to_recent, reaction=reactions
            )
        )

    async def set_quick_reaction(self, reaction):
        return await self(functions.messages.SetDefaultReactionRequest(reaction=self._reaction(reaction)[0]))

    async def report_reaction(self, peer, id: int, reaction_peer):
        return await self(functions.messages.ReportReactionRequest(peer, id, reaction_peer))

    async def translate(self, peer, message, to_lang: str, raw_text: str = None, entities=None) -> str:
        msg_id = tl_utils.get_message_id(message) or 0
        if not msg_id and raw_text is None:
            return None
        if raw_text is None and not isinstance(message, types.Message):
            message = (await self.get_messages(peer, ids=[msg_id]))[0]
        text = raw_text if raw_text is not None else message.raw_text
        result = await self(
            functions.messages.TranslateTextRequest(
                to_lang=to_lang,
                text=[types.TextWithEntities(text, entities or getattr(message, "entities", None) or [])],
            )
        )
        item = result.result[0]
        return _html.unparse(item.text, item.entities)

    async def transcribe(self, peer, message, timeout: int = 15):
        msg_id = tl_utils.get_message_id(message)
        result = await self(functions.messages.TranscribeAudioRequest(peer, msg_id))
        if not getattr(result, "pending", False):
            return result.text
        done = asyncio.Event()
        text = {"value": None}

        async def handler(update):
            if getattr(update, "transcription_id", None) == result.transcription_id and not update.pending:
                text["value"] = update.text
                done.set()

        builder = events.Raw(types.UpdateTranscribedAudio)
        super(CustomTelegramClient, self).add_event_handler(handler, builder)
        try:
            await asyncio.wait_for(done.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            super(CustomTelegramClient, self).remove_event_handler(handler)
        return text["value"]

    async def set_status(self, document_id: int = None, until=None):
        status = types.EmojiStatus(document_id=document_id, until=until) if document_id else types.EmojiStatusEmpty()
        return await self(functions.account.UpdateEmojiStatusRequest(emoji_status=status))

    # ------------------------------------------------------------------ темы форума (Hikka-TL)
    async def _channel(self, entity):
        entity = await self.get_input_entity(entity)
        if not isinstance(entity, (types.InputPeerChannel, types.InputChannel)):
            raise ValueError("You must pass either a channel or a supergroup")
        return entity

    async def _topics(self, name, entity, **kwargs):
        from .utils import _topic_request

        return await self(_topic_request(name, await self._channel(entity), **kwargs))

    async def create_forum_topic(self, entity, title: str, *, icon_color: int = None, icon_emoji_id: int = None,
                                 send_as=None):
        import random

        return await self._topics("CreateForumTopicRequest", entity, title=title, icon_color=icon_color,
                                  icon_emoji_id=icon_emoji_id, random_id=random.randint(1, 2**62),
                                  send_as=await self.get_input_entity(send_as) if send_as is not None else None)

    async def get_forum_topics(self, entity, *, q: str = None, offset_date=None, offset_id: int = 0,
                               offset_topic: int = 0, limit: int = 0):
        return await self._topics("GetForumTopicsRequest", entity, q=q, offset_date=offset_date, offset_id=offset_id,
                                  offset_topic=offset_topic, limit=limit)

    async def get_forum_topics_by_id(self, entity, topics: list):
        return await self._topics("GetForumTopicsByIDRequest", entity, topics=list(topics))

    async def edit_forum_topic(self, entity, topic_id: int, *, title: str = None, icon_emoji_id: int = None,
                               closed: bool = None, hidden: bool = None):
        return await self._topics("EditForumTopicRequest", entity, topic_id=topic_id, title=title,
                                  icon_emoji_id=icon_emoji_id, closed=closed, hidden=hidden)

    async def update_pinned_forum_topic(self, entity, topic_id: int, pinned: bool):
        return await self._topics("UpdatePinnedForumTopicRequest", entity, topic_id=topic_id, pinned=pinned)

    async def delete_topic_history(self, entity, top_msg_id: int):
        return await self._topics("DeleteTopicHistoryRequest", entity, top_msg_id=top_msg_id)

    async def toggle_forum(self, entity, enabled: bool, tabs: bool = False):
        request = functions.channels.ToggleForumRequest
        kwargs = {"channel": await self._channel(entity), "enabled": enabled}
        if "tabs" in inspect.signature(request.__init__).parameters:
            kwargs["tabs"] = tabs
        return await self(request(**kwargs))

    # ------------------------------------------------------------------ юзернеймы (Hikka-TL)
    async def reorder_channel_usernames(self, entity, order: list):
        return await self(functions.channels.ReorderUsernamesRequest(channel=await self._channel(entity), order=order))

    async def toggle_channel_username(self, entity, username: str, active: bool):
        return await self(functions.channels.ToggleUsernameRequest(channel=await self._channel(entity),
                                                                   username=username, active=active))

    async def deactivate_all_usernames(self, entity):
        return await self(functions.channels.DeactivateAllUsernamesRequest(channel=await self._channel(entity)))

    async def reorder_usernames(self, order: list):
        return await self(functions.account.ReorderUsernamesRequest(order=order))

    async def toggle_username(self, username: str, active: bool):
        return await self(functions.account.ToggleUsernameRequest(username=username, active=active))

    # ------------------------------------------------------------------ истории (Hikka-TL)
    async def _story_request(self, names, user=None, **kwargs):
        """Запрос stories.* под текущий слой: user_id= в старых, peer= в новых; имена тоже менялись."""
        for name in (names if isinstance(names, (list, tuple)) else [names]):
            cls = getattr(functions.stories, name, None)
            if cls is not None:
                break
        else:
            raise NotImplementedError(f"stories.{names} нет в этой версии Telethon")
        params = inspect.signature(cls.__init__).parameters
        if user is not None:
            peer = await self.get_input_entity(user)
            kwargs["peer" if "peer" in params else "user_id"] = peer
        elif "peer" in params and "peer" not in kwargs:
            kwargs["peer"] = types.InputPeerSelf()
        return await self(cls(**{k: v for k, v in kwargs.items() if k in params}))

    @staticmethod
    def _story_ids(stories):
        return [s if isinstance(s, int) else s.id for s in (stories if isinstance(stories, (list, tuple)) else [stories])]

    async def get_all_stories(self, next: bool = True, hidden: bool = True, state: str = None):
        return await self._story_request("GetAllStoriesRequest", next=next, hidden=hidden, state=state)

    async def get_user_stories(self, user):
        return await self._story_request(["GetPeerStoriesRequest", "GetUserStoriesRequest"], user)

    get_peer_stories = get_user_stories

    async def get_pinned_stories(self, user, offset_id: int = 0, limit: int = 100):
        return await self._story_request("GetPinnedStoriesRequest", user, offset_id=offset_id, limit=limit)

    async def get_stories_archive(self, offset_id: int = 0, limit: int = 100):
        return await self._story_request("GetStoriesArchiveRequest", offset_id=offset_id, limit=limit)

    async def get_stories_by_id(self, user, stories):
        return await self._story_request(["GetStoriesByIDRequest", "GetStoriesByIdRequest"], user, id=self._story_ids(stories))

    get_stories = get_stories_by_id

    async def delete_stories(self, stories):
        return await self._story_request("DeleteStoriesRequest", id=self._story_ids(stories))

    async def toggle_all_stories_hidden(self, hidden: bool = True):
        return await self._story_request("ToggleAllStoriesHiddenRequest", hidden=hidden)

    async def read_stories(self, user, max_id: int):
        return await self._story_request("ReadStoriesRequest", user, max_id=max_id)

    async def increment_story_views(self, user, story):
        return await self._story_request("IncrementStoryViewsRequest", user, id=self._story_ids(story))

    async def get_story_views(self, story, offset_date: int = 0, offset_id: int = 0, limit: int = 100, q: str = None):
        sid = self._story_ids(story)[0]
        return await self._story_request("GetStoryViewsListRequest", id=sid, offset_date=offset_date,
                                         offset_id=offset_id, offset=str(offset_id or ""), limit=limit, q=q)

    async def get_stories_views(self, stories):
        return await self._story_request("GetStoriesViewsRequest", id=self._story_ids(stories))

    async def export_story_link(self, story, user=None):
        return await self._story_request("ExportStoryLinkRequest", user, id=self._story_ids(story)[0])

    async def report_story(self, user, stories, reason=None, message: str = ""):
        return await self._story_request(["ReportRequest", "ReportStoryRequest"], user, id=self._story_ids(stories),
                                         reason=reason, message=message, option=b"")

    async def send_story(self, media, *, caption=None, entities=None, pinned: bool = False, noforwards: bool = True,
                         privacy_rules=None, period: int = 86400, parse_mode=(), media_areas=None, peer=None, **kwargs):
        if entities is None:
            caption, entities = await self._parse_message_text(caption or "", parse_mode)
        if getattr(media, "SUBCLASS_OF_ID", None) != types.InputMediaEmpty.SUBCLASS_OF_ID:
            _, media, _ = await self._file_to_media(media)
        import random

        return await self._story_request(
            "SendStoryRequest", peer if peer is not None else "me", media=media, caption=caption or "",
            entities=entities or [], pinned=pinned, noforwards=noforwards,
            privacy_rules=privacy_rules or [types.InputPrivacyValueAllowAll()], period=period,
            media_areas=media_areas or [], random_id=random.randint(1, 2**62),
        )

    # ------------------------------------------------------------------ Heroku-TL: app config и подарки
    @property
    def appconfig(self):
        cfg = self._hk.extra.get("appconfig")
        if cfg is None:
            cfg = self._hk.extra["appconfig"] = _AppConfig(self)
        return cfg

    async def get_saved_gifts(self, peer, collection_id: int = None, exclude_unsaved: bool = None,
                              exclude_saved: bool = None, exclude_unlimited: bool = None,
                              exclude_upgradable: bool = None, exclude_unupgradable: bool = None,
                              exclude_nft: bool = None, sort_by_price: bool = None, limit: int = 0, offset: str = ""):
        """Асинхронный генератор подарков (как в Heroku-TL; элементы — SavedStarGift Telegram)."""
        request = functions.payments.GetSavedStarGiftsRequest
        params = inspect.signature(request.__init__).parameters
        total = limit or (1 << 31) - 1
        current = 0
        peer = await self.get_input_entity(peer)
        while True:
            kwargs = dict(peer=peer, offset=offset, limit=min(100, total), exclude_unsaved=exclude_unsaved,
                          exclude_saved=exclude_saved, exclude_unlimited=exclude_unlimited, exclude_unique=exclude_nft,
                          exclude_upgradable=exclude_upgradable, exclude_unupgradable=exclude_unupgradable,
                          sort_by_value=sort_by_price, collection_id=collection_id)
            result = await self(request(**{k: v for k, v in kwargs.items() if k in params}))
            if not result.gifts:
                return
            for gift in result.gifts:
                yield gift
                current += 1
                if current >= total:
                    return
            offset = getattr(result, "next_offset", None)
            if not offset:
                return

    async def upgrade_gift(self, owned_gift_id: str, keep_original_details: bool = None, star_count: int = None):
        import re

        match = re.match(r"^(-\d+)_(\d+)$", str(owned_gift_id))
        slug = re.match(r"^(?:https?://)?(?:www\.)?t(?:elegram)?\.(?:org|me|dog)/(?:nft/|\+)([\w-]+)$", str(owned_gift_id))
        if match:
            gift = types.InputSavedStarGiftChat(peer=await self.get_input_entity(int(match.group(1))),
                                                saved_id=int(match.group(2)))
        elif slug:
            gift = types.InputSavedStarGiftSlug(slug=slug.group(1))
        else:
            gift = types.InputSavedStarGiftUser(msg_id=int(owned_gift_id))
        from telethon import errors

        try:
            return await self(functions.payments.UpgradeStarGiftRequest(stargift=gift, keep_original_details=keep_original_details))
        except errors.RPCError as e:
            if getattr(e, "code", None) != 402 and "PAYMENT_REQUIRED" not in str(e):
                raise
            invoice = types.InputInvoiceStarGiftUpgrade(stargift=gift, keep_original_details=keep_original_details)
            form = await self(functions.payments.GetPaymentFormRequest(invoice=invoice))
            if star_count is not None and form.invoice.prices[0].amount > star_count:
                raise ValueError("Have not enough Telegram Stars.")
            return await self(functions.payments.SendStarsFormRequest(form_id=form.form_id, invoice=invoice))


class _AppConfig:
    """client.appconfig из Heroku-TL: `await client.appconfig` → dict настроек Telegram (help.getAppConfig)."""

    def __init__(self, client, ttl: float = 3600):
        self._client, self._ttl, self._hash, self._data, self._at = client, ttl, 0, {}, 0.0

    @staticmethod
    def _native(value):
        if isinstance(value, types.JsonObject):
            return {item.key: _AppConfig._native(item.value) for item in value.value}
        if isinstance(value, types.JsonArray):
            return [_AppConfig._native(item) for item in value.value]
        if isinstance(value, (types.JsonString, types.JsonNumber, types.JsonBool)):
            return value.value
        return None

    async def get(self, *, force: bool = False) -> dict:
        now = time.monotonic()
        if not force and self._data and now - self._at < self._ttl:
            return self._data
        result = await self._client(functions.help.GetAppConfigRequest(hash=self._hash))
        self._at = now
        if not isinstance(result, types.help.AppConfigNotModified):
            self._hash = getattr(result, "hash", 0)
            self._data = self._native(getattr(result, "config", result)) or {}
        return self._data

    def __await__(self):
        return self.get().__await__()

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError(name)


# ---------------------------------------------------------------------- методы сообщений из форков


from . import _tl_compat  # noqa: E402

_tl_compat.install()


def _compat_wrapper(name):
    """Метод TelegramClient для вида: аргументы форков (top_msg_id, invert_media, …) переводятся/отбрасываются."""

    async def method(self, *args, **kwargs):
        target = getattr(super(CustomTelegramClient, self), name)
        return await target(*args, **_tl_compat.adapt_kwargs(name, target, kwargs))

    method.__name__ = method.__qualname__ = name
    return method


for _name, _fn in list(vars(TelegramClient).items()) + [
    (n, getattr(TelegramClient, n)) for n in dir(TelegramClient) if not n.startswith("_")
]:
    if _name.startswith("_") or _name in CustomTelegramClient.__dict__ or not inspect.iscoroutinefunction(_fn):
        continue
    setattr(CustomTelegramClient, _name, _compat_wrapper(_name))
del _name, _fn
