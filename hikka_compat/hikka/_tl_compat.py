"""Совместимость с форками Telethon: Hikka-TL (hikkatl) и Heroku-TL (herokutl).

Модули Hikka и Heroku написаны под свои форки, а в FRX — обычный Telethon (≥ 1.36). Различия, которые
видят модули, закрываются здесь. Все правки обычного Telethon только ДОБАВЛЯЮТ то, чего в нём нет
(имена в utils / hints / errors, старые классы кнопок, методы сообщений), или действуют только на объекты
пространства Hikka (сообщения, привязанные к его клиенту). Поведение самого FRX не меняется.

* from_id сообщения. В Hikka-TL это число (id отправителя, как sender_id), а исходный Peer — в old_from_id;
  в Heroku-TL — Peer. У сообщений пространства from_id — PeerId: это и число (==, in, int(), f"{…}",
  ключ словаря — как в Hikka), и Peer (.user_id / .channel_id, isinstance(…, PeerUser) — как в Heroku).
* Кнопки. В новых слоях Telegram (Telethon 1.43+) кнопки — KeyboardInlineButton(text, type=…), а модули
  ждут KeyboardButtonCallback / KeyboardButtonUrl / … с полями .data / .url. Старые имена возвращаются:
  isinstance с ними работает для новых объектов, вызов создаёт объект нового слоя, а .data / .url / .query…
  у кнопок нового слоя берутся из их type.
* Аргументы методов клиента, которых нет в обычном Telethon (top_msg_id, invert_media, reply_to=InputReplyTo…),
  переводятся в понятные ему или отбрасываются вместо TypeError (см. adapt_kwargs).
* utils.convert_reaction / get_input_reply_to / get_input_story_id, hints.Reaction / DialogLike / StoryItemLike,
  ошибки MsgVoiceMissingError / PaymentRequiredError / …, ParticipantPermissions.manage_topics,
  channels.*ForumTopic*Request (в новых слоях они в messages), EmojiStatusUntil.
"""

import functools
import inspect
import logging
import typing

from telethon import errors, hints, utils
from telethon.tl import functions, types
from telethon.tl.custom.message import Message

logger = logging.getLogger(__name__)

_installed = False


# ====================================================================================== from_id: число и Peer сразу
class _PeerIdMixin:
    __slots__ = ()

    def __repr__(self):
        return int.__repr__(self)

    __str__ = __repr__

    def __format__(self, spec):
        return int.__format__(self, spec)

    def __eq__(self, other):
        if isinstance(other, int):
            return int(self) == int(other)
        if isinstance(other, (types.PeerUser, types.PeerChannel, types.PeerChat)):
            try:
                return int(self) == utils.get_peer_id(other)
            except Exception:
                return False
        return NotImplemented

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    __hash__ = int.__hash__

    def __bool__(self):
        return True

    def __reduce__(self):
        return (int, (int(self),))


class PeerUserId(_PeerIdMixin, int, types.PeerUser):
    def __new__(cls, user_id):
        obj = int.__new__(cls, user_id)
        obj.user_id = int(user_id)
        return obj

    def __init__(self, *_):
        pass


class PeerChannelId(_PeerIdMixin, int, types.PeerChannel):
    def __new__(cls, marked_id, channel_id):
        obj = int.__new__(cls, marked_id)
        obj.channel_id = int(channel_id)
        return obj

    def __init__(self, *_):
        pass


class PeerChatId(_PeerIdMixin, int, types.PeerChat):
    def __new__(cls, marked_id, chat_id):
        obj = int.__new__(cls, marked_id)
        obj.chat_id = int(chat_id)
        return obj

    def __init__(self, *_):
        pass


def peer_id_of(value):
    """Peer / id → PeerId (число + Peer). None — None."""
    if value is None or isinstance(value, _PeerIdMixin):
        return value
    try:
        if isinstance(value, types.PeerUser):
            return PeerUserId(value.user_id)
        if isinstance(value, types.PeerChannel):
            return PeerChannelId(utils.get_peer_id(value), value.channel_id)
        if isinstance(value, types.PeerChat):
            return PeerChatId(utils.get_peer_id(value), value.chat_id)
        if isinstance(value, int):
            real, kind = utils.resolve_id(value)
            if kind is types.PeerChannel:
                return PeerChannelId(value, real)
            if kind is types.PeerChat:
                return PeerChatId(value, real)
            return PeerUserId(real)
    except Exception:
        return value
    return value


def fix_message(msg):
    """Сообщение для модулей Hikka: from_id как в форках (old_from_id — исходный Peer)."""
    if not isinstance(msg, Message) or getattr(msg, "_hk_fixed", False):
        return msg
    try:
        original = msg.__dict__.get("from_id")
        if isinstance(original, _PeerIdMixin):            # уже поправлено (копия поправленного сообщения)
            msg.__dict__["_hk_fixed"] = True
            return msg
        msg.__dict__["old_from_id"] = original
        sender = getattr(msg, "_sender_id", None)
        if sender is None:
            sender = msg.sender_id
        msg.__dict__["from_id"] = peer_id_of(sender if sender is not None else original)
        msg.__dict__["_hk_fixed"] = True
    except Exception:
        logger.debug("from_id не поправлен", exc_info=True)
    return msg


def _is_space_client(client) -> bool:
    from .tl_cache import CustomTelegramClient

    return isinstance(client, CustomTelegramClient)


def _patch_finish_init():
    original = Message._finish_init
    if getattr(original, "__hikka_compat__", False):
        return

    @functools.wraps(original)
    def _finish_init(self, client, entities, input_chat):
        result = original(self, client, entities, input_chat)
        if _is_space_client(client):
            fix_message(self)
        return result

    _finish_init.__hikka_compat__ = True
    Message._finish_init = _finish_init


# ====================================================================================== кнопки старых слоёв
class _ShimMeta(type):
    """isinstance(<объект нового слоя>, <старый класс>) — по типу кнопки."""

    def __instancecheck__(cls, obj):
        try:
            return cls._match(obj)
        except Exception:
            return False


def _inline_shim(name, type_name, build):
    new_type = getattr(types, type_name, None)
    if new_type is None or not hasattr(types, "KeyboardInlineButton"):
        return None

    def _match(obj):
        return isinstance(obj, types.KeyboardInlineButton) and isinstance(obj.type, new_type)

    def __new__(cls, text, *args, **kwargs):
        return types.KeyboardInlineButton(text=text, type=build(*args, **kwargs))

    return _ShimMeta(name, (), {"__new__": __new__, "_match": staticmethod(_match), "__module__": types.__name__,
                                "SUBCLASS_OF_ID": getattr(types.KeyboardInlineButton, "SUBCLASS_OF_ID", 0)})


def _plain_shim(name, type_name, build):
    new_type = getattr(types, type_name, None)
    if new_type is None or not hasattr(types, "KeyboardButton"):
        return None

    def _match(obj):
        return isinstance(obj, types.KeyboardButton) and isinstance(getattr(obj, "type", None), new_type)

    def __new__(cls, text, *args, **kwargs):
        return types.KeyboardButton(text=text, type=build(*args, **kwargs))

    return _ShimMeta(name, (), {"__new__": __new__, "_match": staticmethod(_match), "__module__": types.__name__})


def _install_buttons():
    if hasattr(types, "KeyboardButtonCallback") or not hasattr(types, "KeyboardInlineButton"):
        return                                         # старый слой — всё и так есть
    t = types
    inline = {
        "KeyboardButtonCallback": ("InlineButtonTypeCallback",
                                   lambda data, requires_password=None: t.InlineButtonTypeCallback(data=data, requires_password=requires_password)),
        "KeyboardButtonUrl": ("InlineButtonTypeUrl", lambda url: t.InlineButtonTypeUrl(url=url)),
        "KeyboardButtonSwitchInline": ("InlineButtonTypeSwitchInline",
                                       lambda query, same_peer=None, peer_types=None: t.InlineButtonTypeSwitchInline(
                                           query=query, same_peer=same_peer, peer_types=peer_types)),
        "KeyboardButtonGame": ("InlineButtonTypeGame", lambda: t.InlineButtonTypeGame()),
        "KeyboardButtonBuy": ("InlineButtonTypeBuy", lambda: t.InlineButtonTypeBuy()),
        "KeyboardButtonUrlAuth": ("InlineButtonTypeUrlAuth",
                                  lambda url, button_id, fwd_text=None: t.InlineButtonTypeUrlAuth(url=url, button_id=button_id, fwd_text=fwd_text)),
        "KeyboardButtonUserProfile": ("InlineButtonTypeUserProfile", lambda user_id: t.InlineButtonTypeUserProfile(user_id=user_id)),
        "KeyboardButtonWebView": ("InlineButtonTypeWebView", lambda url: t.InlineButtonTypeWebView(url=url)),
        "KeyboardButtonCopy": ("InlineButtonTypeCopy", lambda copy_text: t.InlineButtonTypeCopy(copy_text=copy_text)),
        "InputKeyboardButtonUrlAuth": ("InputInlineButtonTypeUrlAuth",
                                       lambda url, bot, request_write_access=None, fwd_text=None: t.InputInlineButtonTypeUrlAuth(
                                           url=url, bot=bot, request_write_access=request_write_access, fwd_text=fwd_text)),
        "InputKeyboardButtonUserProfile": ("InputInlineButtonTypeUserProfile",
                                           lambda user_id: t.InputInlineButtonTypeUserProfile(user_id=user_id)),
    }
    plain = {
        "KeyboardButtonRequestPhone": ("ButtonTypeRequestPhone", lambda: t.ButtonTypeRequestPhone()),
        "KeyboardButtonRequestGeoLocation": ("ButtonTypeRequestGeoLocation", lambda: t.ButtonTypeRequestGeoLocation()),
        "KeyboardButtonRequestPoll": ("ButtonTypeRequestPoll", lambda quiz=None: t.ButtonTypeRequestPoll(quiz=quiz)),
        "KeyboardButtonRequestPeer": ("ButtonTypeRequestPeer",
                                      lambda button_id, peer_type, max_quantity=1: t.ButtonTypeRequestPeer(
                                          button_id=button_id, peer_type=peer_type, max_quantity=max_quantity)),
        "KeyboardButtonSimpleWebView": ("ButtonTypeSimpleWebView", lambda url: t.ButtonTypeSimpleWebView(url=url)),
        "InputKeyboardButtonRequestPeer": ("InputButtonTypeRequestPeer",
                                           lambda button_id, peer_type, max_quantity=1, **kw: t.InputButtonTypeRequestPeer(
                                               button_id=button_id, peer_type=peer_type, max_quantity=max_quantity, **kw)),
    }
    for name, (type_name, build) in inline.items():
        if not hasattr(types, name):
            shim = _inline_shim(name, type_name, build)
            if shim is not None:
                setattr(types, name, shim)
    for name, (type_name, build) in plain.items():
        if not hasattr(types, name):
            shim = _plain_shim(name, type_name, build)
            if shim is not None:
                setattr(types, name, shim)

    def _button_getattr(self, item):
        """.data / .url / .query / .same_peer … кнопки нового слоя — из её type (как у старых классов)."""
        if item.startswith("_") or item == "type":
            raise AttributeError(item)
        kind = self.__dict__.get("type")
        if kind is not None and item in getattr(kind, "__dict__", {}):
            return kind.__dict__[item]
        raise AttributeError(item)

    for cls_name in ("KeyboardInlineButton", "KeyboardButton"):
        cls = getattr(types, cls_name, None)
        if cls is not None and "__getattr__" not in cls.__dict__:
            cls.__getattr__ = _button_getattr


# ====================================================================================== utils / hints / errors / запросы
def convert_reaction(reaction=None):
    if not reaction:
        return None
    if isinstance(reaction, str):
        reaction = types.ReactionEmoji(reaction)
    if isinstance(reaction, int):
        reaction = types.ReactionCustomEmoji(reaction)
    if isinstance(reaction, (types.ReactionEmoji, types.ReactionCustomEmoji)):
        reaction = [reaction]
    out = []
    for r in reaction:
        if isinstance(r, str):
            r = types.ReactionEmoji(r)
        elif isinstance(r, int):
            r = types.ReactionCustomEmoji(r)
        out.append(r)
    return out


def get_input_reply_to(entity=None, reply_to=None, top_msg_id=None, stories: bool = False):
    if isinstance(reply_to, getattr(types, "StoryItem", ())):
        return types.InputReplyToStory(entity, reply_to.id)
    if isinstance(reply_to, Message):
        return types.InputReplyToMessage(reply_to.id, top_msg_id=top_msg_id or getattr(reply_to.reply_to, "reply_to_msg_id", None))
    if isinstance(reply_to, int):
        if stories:
            return types.InputReplyToStory(entity, reply_to)
        return types.InputReplyToMessage(reply_to, top_msg_id=top_msg_id)
    return reply_to or None


def get_input_story_id(story) -> int:
    return story if isinstance(story, int) else story.id


def _install_names():
    for name, value in (("convert_reaction", convert_reaction), ("get_input_reply_to", get_input_reply_to),
                        ("get_input_story_id", get_input_story_id)):
        if not hasattr(utils, name):
            setattr(utils, name, value)
    for name, value in (("Reaction", typing.Union[str, int, "types.TypeReaction", typing.List["types.TypeReaction"]]),
                        ("DialogLike", typing.Union["hints.EntityLike", "types.Dialog"]),
                        ("StoryItemLike", typing.Union[int, "types.StoryItem"])):
        if not hasattr(hints, name):
            setattr(hints, name, value)
    base = getattr(errors, "BadRequestError", errors.RPCError)
    for name, code, message in (("MsgVoiceMissingError", 400, "MSG_VOICE_MISSING"),
                                ("MsgVoiceTooLongError", 400, "MSG_VOICE_TOO_LONG"),
                                ("PaymentRequiredError", 402, "PAYMENT_REQUIRED")):
        if not hasattr(errors, name):
            def __init__(self, request=None, _m=message, _c=code):
                errors.RPCError.__init__(self, request, _m, _c)
            setattr(errors, name, type(name, (base,), {"__init__": __init__, "__module__": errors.__name__}))
    if not hasattr(errors, "ScamDetectionError"):
        errors.ScamDetectionError = type("ScamDetectionError", (Exception,), {"__module__": errors.__name__})
    with_perms = None
    try:
        from telethon.tl.custom.participantpermissions import ParticipantPermissions as with_perms
    except Exception:
        pass
    if with_perms is not None and not hasattr(with_perms, "manage_topics"):
        def manage_topics(self):
            rights = getattr(self.participant, "admin_rights", None)
            return bool(self.is_creator or getattr(rights, "manage_topics", False))

        with_perms.manage_topics = property(manage_topics)
    if not hasattr(types, "EmojiStatusUntil"):
        class EmojiStatusUntil(metaclass=_ShimMeta):
            def __new__(cls, document_id, until=None):
                return types.EmojiStatus(document_id=document_id, until=until)

            _match = staticmethod(lambda obj: isinstance(obj, types.EmojiStatus) and getattr(obj, "until", None))

        types.EmojiStatusUntil = EmojiStatusUntil
    # темы форума: в Hikka-TL — channels.*(channel=…), в новых слоях — messages.*(peer=…)
    for name in ("CreateForumTopicRequest", "GetForumTopicsRequest", "GetForumTopicsByIDRequest", "EditForumTopicRequest",
                 "UpdatePinnedForumTopicRequest", "DeleteTopicHistoryRequest", "ReorderPinnedForumTopicsRequest"):
        target = getattr(functions.messages, name, None)
        if target is None or hasattr(functions.channels, name):
            continue

        def __new__(cls, *args, _target=target, **kwargs):
            if "channel" in kwargs:
                kwargs["peer"] = kwargs.pop("channel")
            return _target(*args, **kwargs)

        setattr(functions.channels, name, _ShimMeta(name, (), {
            "__new__": __new__, "_match": staticmethod(lambda obj, _t=target: isinstance(obj, _t)),
            "__module__": functions.channels.__name__,
        }))


# ====================================================================================== методы сообщений из форков
def _install_message_methods():
    async def react(self, reaction=None, big: bool = False, add_to_recent: bool = False, keep_existing: bool = False):
        if self._client:
            return await self._client.send_reaction(await self.get_input_chat(), self, reaction, big=big,
                                                    add_to_recent=add_to_recent, keep_existing=keep_existing)

    async def translate(self, to_lang: str):
        if self._client:
            return await self._client.translate(await self.get_input_chat(), self, to_lang)

    async def transcribe(self, timeout: int = 15):
        if self._client:
            return await self._client.transcribe(await self.get_input_chat(), self, timeout)

    async def link(self, grouped: bool = False, thread: bool = False):
        if not self._client:
            return None
        channel = await self.get_input_chat()
        if isinstance(channel, types.InputPeerChannel):
            return (await self._client(functions.channels.ExportMessageLinkRequest(channel, self.id, grouped=grouped,
                                                                                    thread=thread))).link
        if isinstance(channel, types.InputPeerUser):
            return f"tg://openmessage?user_id={self.chat_id}&message_id={self.id}"
        if isinstance(channel, types.InputPeerChat):
            return f"tg://openmessage?chat_id={self.chat_id}&message_id={self.id}"
        return None

    for name, fn in (("react", react), ("translate", translate), ("transcribe", transcribe), ("link", link)):
        if not hasattr(Message, name):
            setattr(Message, name, fn)
    if not hasattr(Message, "old_from_id"):
        Message.old_from_id = None


# ====================================================================================== аргументы методов клиента
_SIG_CACHE: dict = {}


def _params(fn):
    key = getattr(fn, "__func__", fn)
    cached = _SIG_CACHE.get(key)
    if cached is None:
        try:
            sig = inspect.signature(fn)
            any_kw = any(p.kind is p.VAR_KEYWORD for p in sig.parameters.values())
            cached = (set(sig.parameters), any_kw)
        except (TypeError, ValueError):
            cached = (set(), True)
        _SIG_CACHE[key] = cached
    return cached


def adapt_kwargs(name: str, fn, kwargs: dict) -> dict:
    """Аргументы форков → аргументы обычного Telethon (или того, кто стоит за видом пространства)."""
    params, any_kw = _params(fn)
    if not kwargs:
        return kwargs
    kwargs = dict(kwargs)
    reply_to = kwargs.get("reply_to")
    if isinstance(reply_to, types.InputReplyToMessage):
        kwargs["reply_to"] = reply_to.reply_to_msg_id
        if getattr(reply_to, "top_msg_id", None) and "top_msg_id" not in params:
            kwargs.setdefault("top_msg_id", reply_to.top_msg_id)
    elif reply_to is not None and type(reply_to).__name__ in ("InputReplyToStory", "StoryItem"):
        kwargs.pop("reply_to")
    if "top_msg_id" in kwargs and "top_msg_id" not in params:
        top = kwargs.pop("top_msg_id")
        if top and not kwargs.get("reply_to") and "reply_to" in params | ({"reply_to"} if any_kw else set()):
            kwargs["reply_to"] = top                    # ответ на корень темы = сообщение в теме
    if not any_kw:
        dropped = [k for k in kwargs if k not in params]
        for key in dropped:
            kwargs.pop(key)
        if dropped:
            logger.debug("%s: аргументы форка %s в Telethon FRX не поддерживаются — пропущены", name, dropped)
    return kwargs


def install():
    global _installed
    if _installed:
        return
    _installed = True
    for step in (_patch_finish_init, _install_buttons, _install_names, _install_message_methods):
        try:
            step()
        except Exception:
            logger.exception("Совместимость с форками Telethon: шаг %s не выполнен", step.__name__)
