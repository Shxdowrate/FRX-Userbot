"""utils — вспомогательные функции для модулей Hikka/Heroku (utils.answer, get_args_raw, escape_html и т. д.)."""

import asyncio
import atexit as _atexit
import contextlib
import functools
import inspect
import io
import json
import logging
import os
import random
import re
import shlex
import signal
import socket
import string
import sys
import time
import typing
from datetime import timedelta
from urllib.parse import urlparse

import telethon
from telethon import hints
from telethon.tl.functions.account import UpdateNotifySettingsRequest
from telethon.tl.functions.channels import (
    CreateChannelRequest,
    EditAdminRequest,
    EditPhotoRequest,
    InviteToChannelRequest,
)
from telethon.tl.functions.messages import (
    GetDialogFiltersRequest,
    SetHistoryTTLRequest,
    UpdateDialogFilterRequest,
)
from telethon.tl.types import (
    Channel,
    Chat,
    ChatAdminRights,
    InputDocument,
    InputPeerNotifySettings,
    Message,
    MessageEntityBankCard,
    MessageEntityBlockquote,
    MessageEntityBold,
    MessageEntityBotCommand,
    MessageEntityCashtag,
    MessageEntityCode,
    MessageEntityEmail,
    MessageEntityHashtag,
    MessageEntityItalic,
    MessageEntityMention,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityPre,
    MessageEntitySpoiler,
    MessageEntityStrike,
    MessageEntityTextUrl,
    MessageEntityUnderline,
    MessageEntityUnknown,
    MessageEntityUrl,
    MessageMediaDocument,
    MessageMediaPhoto,
    MessageMediaWebPage,
    PeerChannel,
    PeerChat,
    PeerUser,
    UpdateNewChannelMessage,
    User,
)

from . import _frx, _html
from ._internal import fw_protect
from .inline.types import BotInlineCall, InlineCall, InlineMessage
from .types import HikkaReplyMarkup, ListLike, Module

logger = logging.getLogger(__name__)

parser = _html                       # parse_mode пространства (HTML с тегами Hikka)
HerokuReplyMarkup = HikkaReplyMarkup

FormattingEntity = typing.Union[
    MessageEntityUnknown,
    MessageEntityMention,
    MessageEntityHashtag,
    MessageEntityBotCommand,
    MessageEntityUrl,
    MessageEntityEmail,
    MessageEntityBold,
    MessageEntityItalic,
    MessageEntityCode,
    MessageEntityPre,
    MessageEntityTextUrl,
    MessageEntityMentionName,
    MessageEntityPhone,
    MessageEntityCashtag,
    MessageEntityUnderline,
    MessageEntityStrike,
    MessageEntityBlockquote,
    MessageEntityBankCard,
    MessageEntitySpoiler,
]

emoji_pattern = re.compile(
    "["
    "\U0001f600-\U0001f64f"
    "\U0001f300-\U0001f5ff"
    "\U0001f680-\U0001f6ff"
    "\U0001f1e0-\U0001f1ff"
    "]+",
    flags=re.UNICODE,
)

TAG_RE = re.compile(r"</?([a-zA-Z][a-zA-Z0-9\-]*)(?:\s[^<>]*)?>")
TELEGRAM_HTML_TAGS = {
    "strong", "b", "em", "i", "tg-spoiler", "u", "ins", "del", "s", "strike", "blockquote", "code", "pre", "a",
    "tg-emoji", "emoji", "span",
}

IS_DOCKER = "DOCKER" in os.environ
IS_HIKKAHOST = "HIKKAHOST" in os.environ
IS_MACOS = sys.platform == "darwin"
IS_USERLAND = "userland" in os.environ.get("USER", "")
IS_TERMUX = "com.termux" in os.environ.get("PREFIX", "")
IS_WINDOWS = sys.platform == "win32"
IS_WSL = False
with contextlib.suppress(Exception):
    from platform import uname

    IS_WSL = "microsoft-standard" in uname().release

init_ts = time.perf_counter()
custom_placeholders: dict = {}


# ---------------------------------------------------------------------- сеть (своё, асинхронно)


async def fetch_bytes(url: str, *, headers: dict = None, timeout: float = 30) -> bytes:
    """GET по ссылке → bytes (HTTPError при коде ≥ 400)."""
    import aiohttp

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.get(url, headers=headers or {}) as resp:
            if resp.status >= 400:
                raise ConnectionError(f"HTTP {resp.status} при загрузке {url}")
            return await resp.read()


async def fetch_text(url: str, *, headers: dict = None, timeout: float = 30) -> str:
    return (await fetch_bytes(url, headers=headers, timeout=timeout)).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------- аргументы команд


def get_args(message: typing.Union[Message, str]) -> typing.List[str]:
    """Аргументы команды списком (с учётом кавычек)."""
    message = getattr(message, "message", message)
    if not message:
        return False
    parts = message.split(maxsplit=1)
    if len(parts) <= 1:
        return []
    rest = parts[1]
    try:
        split = shlex.split(rest)
    except ValueError:
        return rest
    return [x for x in split if x]


def get_args_raw(message: typing.Union[Message, str]) -> str:
    """Всё после команды одной строкой."""
    message = getattr(message, "message", message)
    if not message:
        return False
    parts = message.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


def get_args_html(message: Message) -> str:
    """Аргументы с HTML-разметкой."""
    try:
        prefix = message.client.loader.get_prefix()
    except Exception:
        prefix = "."
    text = message.text
    if not text:
        return False
    if prefix not in text:
        return text
    raw_text, entities = _html.parse(text)
    raw = telethon.helpers.add_surrogate(raw_text)
    try:
        start = raw.index(prefix)
        command = raw[start : raw.index(" ", start + 1)]
    except ValueError:
        return ""
    cut = len(command) + 1
    rest = raw[cut:]
    return _html.unparse(telethon.helpers.del_surrogate(rest), relocate_entities(entities, -cut, rest))


def get_args_split_by(message: typing.Union[Message, str], separator) -> typing.List[str]:
    args = get_args_raw(message) or ""
    if isinstance(separator, str):
        sections = args.split(separator)
    else:
        sections = [args]
        for sep in separator:
            sections = [part for section in sections for part in section.split(sep)]
    return [s.strip() for s in sections if s.strip()]


def get_args_int(message) -> typing.List[int]:
    out = []
    for arg in get_args(message) or []:
        with contextlib.suppress(ValueError):
            out.append(int(arg))
    return out


def get_args_bool(message) -> typing.List[bool]:
    out = []
    for arg in get_args(message) or []:
        a = arg.lower()
        if a in ("true", "yes", "1", "on"):
            out.append(True)
        elif a in ("false", "no", "0", "off"):
            out.append(False)
    return out


def get_kwargs() -> dict:
    """Аргументы функции, из которой вызвана (для повторного вызова с теми же параметрами)."""
    frame = inspect.currentframe().f_back
    keys, _, _, values = inspect.getargvalues(frame)
    return {key: values[key] for key in keys if key != "self"}


# ---------------------------------------------------------------------- id, ссылки, экранирование


def get_chat_id(message) -> int:
    """id чата без -100 у каналов (для aiogram-сообщений — chat.id)."""
    chat_id = getattr(message, "chat_id", None)
    if chat_id is None:
        chat_id = getattr(getattr(message, "chat", None), "id", None)
    return telethon.utils.resolve_id(chat_id)[0]


def get_entity_id(entity) -> int:
    return telethon.utils.get_peer_id(entity)


def escape_html(text, /) -> str:
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_quotes(text, /) -> str:
    return escape_html(text).replace('"', "&quot;")


def escape_non_html(text: str) -> str:
    out, last = [], 0
    for m in TAG_RE.finditer(text):
        out.append(escape_html(text[last : m.start()]))
        out.append(m.group(0) if m.group(1).lower() in TELEGRAM_HTML_TAGS else escape_html(m.group(0)))
        last = m.end()
    out.append(escape_html(text[last:]))
    return "".join(out)


def remove_html(text: str, escape: bool = False, keep_emojis: bool = False) -> str:
    tags = r"a|b|i|u|s|ins|strong|em|code|strike|del|pre|blockquote|tg-spoiler|span"
    if not keep_emojis:
        tags += r"|emoji|tg-emoji"
    cleaned = re.sub(rf"</?(?:{tags})(?:\s[^>]*)?>", "", str(text))
    return escape_html(cleaned) if escape else cleaned


def remove_emoji(text: str) -> str:
    try:
        import emoji as _emoji

        return "".join(ch for ch in text if ch not in _emoji.EMOJI_DATA)
    except ImportError:
        return emoji_pattern.sub("", text)


def validate_html(html: str) -> str:
    text, entities = _html.parse(html)
    return _html.unparse(text, entities)


def check_url(url: str) -> bool:
    try:
        return bool(urlparse(url).netloc)
    except Exception:
        return False


def is_url(text: str) -> bool:
    return bool(
        re.match(
            r"^https?://(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+[A-Z]{2,63}\.?|localhost|\d{1,3}(?:\.\d{1,3}){3})"
            r"(?::\d+)?(?:/?|[/?]\S+)$",
            str(text),
            re.IGNORECASE,
        )
    )


def extract_urls(text: str) -> typing.List[str]:
    return re.findall(r"https?://[^\s]+", text or "")


def get_link(user, /) -> str:
    if isinstance(user, User):
        return f"tg://user?id={user.id}"
    return f"tg://resolve?domain={user.username}" if getattr(user, "username", None) else ""


def get_entity_url(entity, openmessage: bool = False) -> str:
    if isinstance(entity, User):
        return f"tg://openmessage?id={entity.id}" if openmessage else f"tg://user?id={entity.id}"
    return f"tg://resolve?domain={entity.username}" if getattr(entity, "username", None) else ""


def get_lang_flag(countrycode: str) -> str:
    code = [c for c in countrycode.lower() if c in string.ascii_letters + string.digits]
    if len(code) == 2:
        return "".join(chr(ord(c.upper()) + (ord("🇦") - ord("A"))) for c in code)
    return countrycode


async def get_message_link(message: Message, chat=None) -> str:
    if message.is_private:
        return f"tg://openmessage?user_id={get_chat_id(message)}&message_id={message.id}"
    if not chat:
        chat = message.chat or await message.get_chat()
    topic = (
        f"?topic={message.reply_to.reply_to_msg_id}" if getattr(message.reply_to, "forum_topic", False) else ""
    )
    if getattr(chat, "username", None):
        return f"https://t.me/{chat.username}/{message.id}{topic}"
    return f"https://t.me/c/{chat.id}/{message.id}{topic}"


def get_topic(message) -> typing.Optional[int]:
    if isinstance(message, Message):
        reply = message.reply_to
        if reply and getattr(reply, "forum_topic", False):
            return reply.reply_to_top_id or reply.reply_to_msg_id
        return None
    if isinstance(message, (InlineCall, InlineMessage)):
        return message.form.get("top_msg_id")
    return None


def mime_type(message) -> str:
    if not isinstance(message, Message) or not getattr(message, "media", None):
        return ""
    return getattr(getattr(message, "file", None), "mime_type", None) or getattr(message.media, "mime_type", "") or ""


def has_media(message) -> bool:
    return isinstance(getattr(message, "media", None), (MessageMediaPhoto, MessageMediaDocument, MessageMediaWebPage))


# ---------------------------------------------------------------------- пользователи и цели


async def get_user(message: Message):
    try:
        return await message.get_sender()
    except ValueError:
        pass
    if isinstance(message.peer_id, PeerUser):
        await message.client.get_dialogs()
        return await message.get_sender()
    if isinstance(message.peer_id, (PeerChannel, PeerChat)):
        async for user in message.client.iter_participants(message.peer_id, aggressive=True):
            if user.id == message.sender_id:
                return user
    return None


async def get_target(message: Message, arg_no: int = 0) -> typing.Optional[int]:
    mentions = [e for e in (message.entities or []) if isinstance(e, MessageEntityMentionName)]
    if mentions:
        return sorted(mentions, key=lambda e: e.offset)[0].user_id
    args = get_args(message) or []
    if len(args) > arg_no:
        user = args[arg_no]
    elif message.is_reply:
        return (await message.get_reply_message()).sender_id
    elif hasattr(message.peer_id, "user_id"):
        user = message.peer_id.user_id
    else:
        return None
    try:
        entity = await message.client.get_entity(int(user) if str(user).lstrip("-").isdigit() else user)
    except Exception:
        return None
    return entity.id if isinstance(entity, User) else None


# ---------------------------------------------------------------------- ответ на команду


def relocate_entities(entities: list, offset: int, text: str = None) -> list:
    length = len(text) if text is not None else 0
    for ent in list(entities or []):
        ent.offset += offset
        if ent.offset < 0:
            ent.length += ent.offset
            ent.offset = 0
        if text is not None and ent.offset + ent.length > length:
            ent.length = length - ent.offset
        if ent.length <= 0:
            entities.remove(ent)
    return entities


def _copy_tl(o, **kwargs):
    d = o.to_dict()
    d.pop("_", None)
    d.update(kwargs)
    return o.__class__(**d)


def _graphemes_safe(text: str, index: int) -> int:
    """Не разрезать составной эмодзи / символ с модификатором."""
    while 0 < index < len(text) and (
        text[index] in "‍️︎⃣"
        or 0x1F3FB <= ord(text[index]) <= 0x1F3FF
        or 0x0300 <= ord(text[index]) <= 0x036F
        or text[index - 1] == "‍"
    ):
        index -= 1
    return index


def smart_split(text: str, entities: list, length: int = 4096, split_on=("\n", " "), min_length: int = 1):
    """Разбить текст с сущностями на части ≤ length (в UTF-16) и вернуть их HTML по очереди."""

    def u16(s: str) -> int:
        return len(s.encode("utf-16-le")) // 2

    pending = sorted(entities or [], key=lambda e: (e.offset, -e.length))
    pos = 0                                    # позиция в text (символы Python)
    pos16 = 0                                  # та же позиция в UTF-16
    while pos < len(text):
        rest = text[pos:]
        if u16(rest) <= length:
            chunk_entities = [_copy_tl(e, offset=e.offset - pos16) for e in pending if e.offset + e.length > pos16]
            yield _html.unparse(rest, _clip(chunk_entities, u16(rest)))
            return
        # сколько символов влезает
        fit = 0
        acc = 0
        for ch in rest:
            w = 2 if ord(ch) > 0xFFFF else 1
            if acc + w > length:
                break
            acc += w
            fit += 1
        cut = -1
        for sep in split_on:
            idx = rest.rfind(sep, min_length, fit)
            if idx != -1:
                cut = idx
                break
        if cut == -1:
            cut = fit
        cut = _graphemes_safe(rest, cut) or fit
        chunk = rest[:cut]
        skip = 0
        while cut + skip < len(rest) and rest[cut + skip] in split_on:
            skip += 1
        chunk16 = u16(chunk)
        chunk_entities = []
        for e in pending:
            start, end = e.offset - pos16, e.offset - pos16 + e.length
            if end <= 0 or start >= chunk16:
                continue
            s = max(start, 0)
            chunk_entities.append(_copy_tl(e, offset=s, length=min(end, chunk16) - s))
        yield _html.unparse(chunk, chunk_entities)
        pos += cut + skip
        pos16 += chunk16 + u16(rest[cut : cut + skip])


def _clip(entities, total16):
    out = []
    for e in entities:
        start = max(e.offset, 0)
        end = min(e.offset + e.length, total16)
        if end > start:
            out.append(_copy_tl(e, offset=start, length=end - start))
    return out


async def answer(message, response, *, reply_markup: HikkaReplyMarkup = None, **kwargs):
    """Ответ на команду: правит ваше сообщение (или отвечает на чужое); длинное — списком или файлом;
    с reply_markup — инлайн-форма; файл / bytes — отправка файла."""
    if isinstance(message, list) and message:
        message = message[0]

    if reply_markup is not None:
        if not isinstance(reply_markup, (list, dict)):
            raise ValueError("reply_markup must be a list or dict")
        if reply_markup:
            kwargs.pop("message", None)
            if isinstance(message, (InlineMessage, InlineCall, BotInlineCall)):
                await message.edit(response, reply_markup, **kwargs)
                return
            inline = message.client.loader.inline
            return await inline.form(
                response,
                message=message if message.out else get_chat_id(message),
                reply_markup=inline._normalize_markup(reply_markup),
                **kwargs,
            )

    if isinstance(message, (InlineMessage, InlineCall, BotInlineCall)):
        await message.edit(response)
        return message

    client = message.client
    kwargs.setdefault("link_preview", False)
    edit = bool(message.out and not getattr(message, "via_bot_id", None) and not getattr(message, "fwd_from", None))
    if not edit:
        kwargs.setdefault("reply_to", getattr(message, "reply_to_msg_id", None))
    else:
        kwargs.pop("reply_to", None)

    parse_mode = kwargs.pop("parse_mode", None)
    if parse_mode is None or (isinstance(parse_mode, str) and parse_mode.lower() == "html"):
        parse_mode = _html
    else:
        parse_mode = telethon.utils.sanitize_parse_mode(parse_mode)

    if isinstance(response, str) and not kwargs.pop("asfile", False):
        text, entities = parse_mode.parse(response)
        if len(text) >= 4096 and not hasattr(message, "hikka_grepped") and not hasattr(message, "heroku_grepped"):
            try:
                inline = client.loader.inline
                if not inline.init_complete:
                    raise RuntimeError("inline is not ready")
                strings = list(smart_split(text, entities, 4096))
                if len(strings) > 10:
                    raise RuntimeError("too many pages")
                result = await inline.list(message=message, strings=strings)
                if not result:
                    raise RuntimeError("list failed")
                return result
            except Exception:
                file = io.BytesIO(text.encode("utf-8"))
                file.name = "command_result.txt"
                try:
                    caption = client.loader.lookup("translations").strings("too_long")
                except Exception:
                    caption = "<b>📄 Вывод команды слишком длинный — отправляю файлом.</b>"
                result = await client.send_file(
                    message.peer_id,
                    file,
                    caption=caption,
                    reply_to=kwargs.get("reply_to") or get_topic(message),
                )
                if message.out:
                    with contextlib.suppress(Exception):
                        await message.delete()
                return result

        if edit:
            return await message.edit(text, parse_mode=lambda t: (t, entities), **kwargs)

        file = kwargs.pop("file", None)
        if file is not None:
            reply_to = kwargs.pop("reply_to", None) or get_topic(message)
            kwargs.pop("invert_media", None)
            result = await client.send_file(
                message.peer_id, file, caption=text, parse_mode=lambda t: (t, entities), reply_to=reply_to, **kwargs
            )
            if message.out:
                with contextlib.suppress(Exception):
                    await message.delete()
            return result
        return await message.respond(text, parse_mode=lambda t: (t, entities), **kwargs)

    if isinstance(response, Message):
        if message.media is None and (
            response.media is None or isinstance(response.media, (MessageMediaWebPage, MessageMediaPhoto, MessageMediaDocument))
        ) and edit:
            return await message.edit(
                response.message,
                file=response.media,
                parse_mode=lambda t: (t, response.entities or []),
                link_preview=isinstance(response.media, MessageMediaWebPage),
            )
        return await message.respond(response, **kwargs)

    if isinstance(response, bytes):
        response = io.BytesIO(response)
    elif isinstance(response, str):
        response = io.BytesIO(response.encode("utf-8"))
    name = kwargs.pop("filename", None)
    if name:
        response.name = name
    if message.media is not None and edit:
        return await message.edit(file=response, **kwargs)
    kwargs.setdefault("reply_to", getattr(message, "reply_to_msg_id", None) or get_topic(message))
    kwargs.pop("link_preview", None)
    result = await client.send_file(message.peer_id, response, **kwargs)
    if message.out:
        with contextlib.suppress(Exception):
            await message.delete()
    return result


async def answer_file(message, file, caption: str = None, **kwargs):
    """Ответить файлом (ссылка, путь, bytes) и удалить своё сообщение с командой."""
    if isinstance(message, (InlineCall, InlineMessage)):
        message = message.form["caller"]
    topic = get_topic(message)
    if topic:
        kwargs.setdefault("reply_to", topic)
    try:
        response = await message.client.send_file(message.peer_id, file, caption=caption, **kwargs)
    except Exception:
        if caption:
            logger.warning("Не удалось отправить файл — отправляю текст", exc_info=True)
            return await answer(message, caption, **kwargs)
        raise
    with contextlib.suppress(Exception):
        await message.delete()
    return response


# ---------------------------------------------------------------------- служебные чаты


async def invite_inline_bot(client, peer) -> None:
    try:
        await client(InviteToChannelRequest(peer, [client.loader.inline.bot_username]))
    except Exception as e:
        raise RuntimeError(f"Не удалось пригласить инлайн-бота в служебный чат: {e}")
    with contextlib.suppress(Exception):
        await client(
            EditAdminRequest(
                channel=peer,
                user_id=client.loader.inline.bot_username,
                admin_rights=ChatAdminRights(ban_users=True),
                rank="Hikka",
            )
        )


async def dnd(client, peer, archive: bool = True) -> bool:
    try:
        await client(
            UpdateNotifySettingsRequest(
                peer=peer, settings=InputPeerNotifySettings(show_previews=False, silent=True, mute_until=2**31 - 1)
            )
        )
        if archive:
            await fw_protect()
            await client.edit_folder(peer, 1)
    except Exception:
        logger.exception("utils.dnd error")
        return False
    return True


async def set_avatar(client, peer, avatar) -> bool:
    if isinstance(avatar, str) and check_url(avatar):
        data = await fetch_bytes(avatar)
    elif isinstance(avatar, bytes):
        data = avatar
    else:
        return False
    await fw_protect()
    result = await client(EditPhotoRequest(channel=peer, photo=await client.upload_file(data, file_name="photo.png")))
    await fw_protect()
    with contextlib.suppress(Exception):
        await client.delete_messages(
            peer, message_ids=[next(u for u in result.updates if isinstance(u, UpdateNewChannelMessage)).message.id]
        )
    return True


async def asset_channel(
    client,
    title: str,
    description: str,
    *,
    channel: bool = False,
    silent: bool = False,
    archive: bool = False,
    invite_bot: bool = False,
    avatar: str = None,
    ttl: int = None,
    forum: bool = False,
    hide_general: bool = False,
    _folder: str = None,
) -> typing.Tuple[typing.Any, bool]:
    """Найти чат с таким названием среди диалогов или создать его. → (чат, создан_ли)."""
    cache = getattr(client, "_hikka_channels_cache", None)
    if cache is None:
        cache = {}
        with contextlib.suppress(Exception):
            client._hikka_channels_cache = cache
    if title in cache and cache[title]["exp"] > time.time():
        return cache[title]["peer"], False

    async for dialog in client.iter_dialogs():
        if dialog.title == title:
            cache[title] = {"peer": dialog.entity, "exp": time.time() + 600}
            if invite_bot:
                with contextlib.suppress(Exception):
                    bot_id = client.loader.inline.bot_id
                    if all(p.id != bot_id for p in await client.get_participants(dialog.entity, limit=100)):
                        await fw_protect()
                        await invite_inline_bot(client, dialog.entity)
            return dialog.entity, False

    await fw_protect()
    kwargs = {"broadcast": channel, "megagroup": not channel}
    if forum:
        kwargs["forum"] = True
    peer = (await client(CreateChannelRequest(title, description, **kwargs))).chats[0]
    if invite_bot:
        await fw_protect()
        with contextlib.suppress(Exception):
            await invite_inline_bot(client, peer)
    if silent:
        await fw_protect()
        await dnd(client, peer, archive)
    elif archive:
        await fw_protect()
        with contextlib.suppress(Exception):
            await client.edit_folder(peer, 1)
    if avatar:
        await fw_protect()
        with contextlib.suppress(Exception):
            await set_avatar(client, peer, avatar)
    if hide_general and forum:
        with contextlib.suppress(Exception):
            await client(_topic_request("EditForumTopicRequest", peer, topic_id=1, hidden=True))
    if ttl:
        await fw_protect()
        with contextlib.suppress(Exception):
            await client(SetHistoryTTLRequest(peer=peer, period=ttl))
    if _folder:
        with contextlib.suppress(Exception):
            folders = (await client(GetDialogFiltersRequest())).filters
            folder = next(
                f for f in folders if hasattr(f, "title") and str(getattr(f.title, "text", f.title)).lower() == _folder.lower()
            )
            if not any(peer.id == getattr(p, "channel_id", None) for p in folder.include_peers):
                folder.include_peers.append(await client.get_input_entity(peer))
                await client(UpdateDialogFilterRequest(folder.id, folder))
    cache[title] = {"peer": peer, "exp": time.time() + 600}
    return peer, True


def _topic_request(name: str, peer, **kwargs):
    """Запрос про темы форума: в новых слоях Telegram он в messages (peer=), в старых — в channels (channel=)."""
    from telethon.tl.functions import channels as _ch, messages as _msg

    if hasattr(_msg, name):
        return getattr(_msg, name)(peer=peer, **kwargs)
    return getattr(_ch, name)(channel=peer, **kwargs)


CONTENT_CHAT_TITLE = "frx-hikka"


async def ensure_content_channel(client, db=None):
    """Служебная группа-форум пространства (как «heroku-userbot» в Heroku): там модули заводят свои темы
    (уведомления, ассеты). Создаётся один раз, архивируется и заглушается; id — в БД hikka.forums → channel_id."""
    db = db or getattr(client, "hikka_db", None)
    cid = db.get("hikka.forums", "channel_id", None) if db is not None else None
    if cid:
        with contextlib.suppress(Exception):
            return await client.get_entity(int(cid))
    peer, _ = await asset_channel(
        client,
        CONTENT_CHAT_TITLE,
        "🌙 Служебная группа модулей Hikka / Heroku в FRX (уведомления, ассеты)",
        silent=True,
        archive=True,
        invite_bot=True,
        forum=True,
        hide_general=True,
    )
    if db is not None:
        db.set("hikka.forums", "channel_id", int(peer.id))
    return peer


async def asset_forum_topic(client, db, peer, title: str, description: str = None, icon_emoji_id: int = None,
                            invite_bot: bool = False):
    """Найти или создать тему форума с таким названием (peer пустой — служебная группа пространства)."""
    entity = await ensure_content_channel(client, db) if not peer else await client.get_entity(peer)
    if not peer:
        peer = entity
    cache = db.pointer("hikka.forums", "forums_cache", {}) if db is not None else {}
    title_key = getattr(entity, "title", str(getattr(entity, "id", "")))

    async def by_id(topic_id):
        result = await client(_topic_request("GetForumTopicsByIDRequest", entity, topics=[topic_id]))
        topic = result.topics[0] if result.topics else None
        return None if topic is None or type(topic).__name__ == "ForumTopicDeleted" else topic

    async def find():
        result = await client(
            _topic_request("GetForumTopicsRequest", entity, offset_date=None, offset_id=0, offset_topic=0, limit=100)
        )
        return next((t for t in result.topics if getattr(t, "title", None) == title), None)

    known = (cache.get(title_key) or {}).get(title) if isinstance(cache, dict) else None
    topic = None
    if known:
        with contextlib.suppress(Exception):
            topic = await by_id(known)
    if topic is None:
        topic = await find()
    if topic is None:
        await fw_protect()
        created = await client(
            _topic_request("CreateForumTopicRequest", entity, title=title, icon_emoji_id=icon_emoji_id,
                           random_id=random.randint(1, 2**62))
        )
        topic_id = next((u.id for u in getattr(created, "updates", []) if hasattr(u, "id")), None)
        if description and topic_id:
            with contextlib.suppress(Exception):
                await client.send_message(entity, description, reply_to=topic_id)
        topic = await find()
    if topic is not None and isinstance(cache, dict):
        section = dict(cache.get(title_key) or {})
        section[title] = topic.id
        cache[title_key] = section
    if invite_bot:
        with contextlib.suppress(Exception):
            await invite_inline_bot(client, entity)
    return topic


async def wait_for_content_channel(db, delay: float = 10) -> int:
    from . import _frx

    while True:
        cid = db.get("hikka.forums", "channel_id", None)
        if cid:
            return cid
        space = _frx.runtime
        if space is not None and space.client is not None:
            with contextlib.suppress(Exception):
                return (await ensure_content_channel(space.client, db)).id
        await asyncio.sleep(delay)


async def get_topic_id(db, topic_name: str):
    with contextlib.suppress(Exception):
        for section in db.get("hikka.forums", "forums_cache", {}).values():
            if isinstance(section, dict) and topic_name in section:
                return section[topic_name]
    return None


# ---------------------------------------------------------------------- прочее


def run_sync(func, *args, **kwargs):
    """Запустить синхронную функцию в потоке → awaitable."""
    return asyncio.get_event_loop().run_in_executor(None, functools.partial(func, *args, **kwargs))


def run_async(loop: asyncio.AbstractEventLoop, coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result()


def ensure_child_watcher():
    """Совместимость: в FRX подпроцессы asyncio и так работают."""


def censor(obj, to_censor: typing.Iterable[str] = None, replace_with: str = "redacted_{count}_chars"):
    to_censor = ["phone"] if to_censor is None else list(to_censor)
    with contextlib.suppress(Exception):
        for key, value in list(vars(obj).items()):
            if key in to_censor:
                setattr(obj, key, replace_with.format(count=len(str(value))))
            elif key and key[0] != "_" and hasattr(value, "__dict__") and not callable(value):
                setattr(obj, key, censor(value, to_censor, replace_with))
    return obj


def merge(a: dict, b: dict, /, *, deep: bool = True) -> dict:
    for key, a_value in a.items():
        b_value = b.get(key)
        if key not in b:
            b[key] = a_value
        elif deep and isinstance(a_value, dict) and isinstance(b_value, dict):
            b[key] = merge(a_value, b_value, deep=deep)
        elif isinstance(a_value, list) and isinstance(b_value, list):
            b[key] = list(dict.fromkeys(b_value + a_value))
        else:
            b[key] = a_value
    return b


def chunks(_list, n: int, /) -> list:
    return [_list[i : i + n] for i in range(0, len(_list), n)]


def array_sum(array, /) -> list:
    result = []
    for item in array:
        result += item
    return result


def rand(size: int, /) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz1234567890") for _ in range(size))


def is_serializable(x, /) -> bool:
    try:
        json.dumps(x)
        return True
    except Exception:
        return False


def iter_attrs(obj, /):
    return ((attr, getattr(obj, attr, None)) for attr in dir(obj))


def atexit(func, use_signal: int = None, *args, **kwargs) -> None:
    if use_signal:
        signal.signal(use_signal, lambda *_: func(*args, **kwargs))
        return
    _atexit.register(functools.partial(func, *args, **kwargs))


def find_caller(stack=None):
    """Найти метод модуля, из которого (через цепочку вызовов) пришёл вызов — для наследования прав формой."""
    stack = stack or inspect.stack()
    for frame_info in stack:
        f_globals = frame_info.frame.f_globals
        for value in list(f_globals.values()):
            if inspect.isclass(value) and issubclass(value, Module) and value is not Module:
                method = getattr(value, frame_info.function, None)
                if method is not None and callable(method):
                    return method
    return None


def ascii_face() -> str:
    return escape_html(
        random.choice(
            ["ヽ(๑◠ܫ◠๑)ﾉ", "(◕ᴥ◕ʋ)", "ᕙ(`▽´)ᕗ", "(✿◠‿◠)", "(▰˘◡˘▰)", "(˵ ͡° ͜ʖ ͡°˵)", "ʕっ•ᴥ•ʔっ", "( ͡° ᴥ ͡°)",
             "٩(^‿^)۶", "(っˆڡˆς)", "ψ(｀∇´)ψ", "⊙ω⊙", "(´・ω・)っ由", "✧♡(◕‿◕✿)", "ʕ•ᴥ•ʔ", "ฅ^•ﻌ•^ฅ", "(*•‿•*)",
             "ʘ‿ʘ", "( ･ω･)ﾉ", "(*˘︶˘*)", "ಥ_ಥ", "(┬┬＿┬┬)"]
        )
    )


# ---------------------------------------------------------------------- платформа и система


def get_base_dir() -> str:
    """Папка «установки» для модулей. В FRX — user/data/hikka/hikka (рядом лежат данные пространства)."""
    path = _frx.DATA_DIR / "hikka"
    with contextlib.suppress(Exception):
        path.mkdir(parents=True, exist_ok=True)
    return str(path.resolve())


def get_dir(mod: str) -> str:
    return os.path.abspath(os.path.dirname(os.path.abspath(mod)))


def get_named_platform() -> str:
    with contextlib.suppress(Exception):
        if os.path.isfile("/proc/device-tree/model"):
            with open("/proc/device-tree/model") as f:
                model = f.read().strip()
            if "Orange" in model or "Raspberry" in model:
                return model
    if IS_WSL:
        return "WSL"
    if IS_WINDOWS:
        return "Windows"
    if IS_MACOS:
        return "MacOS"
    if IS_TERMUX:
        return "Termux"
    if IS_USERLAND:
        return "UserLand"
    if IS_HIKKAHOST:
        return "HikkaHost"
    if IS_DOCKER:
        return "Docker"
    return "VDS"


get_platform_name = get_named_platform


def get_named_platform_emoji() -> str:
    return {
        "WSL": "🍀 ", "Windows": "💻 ", "MacOS": "🍏 ", "Termux": "🕶 ", "UserLand": "🐧 ", "HikkaHost": "🌼 ",
        "Docker": "🐳 ",
    }.get(get_named_platform(), "💎 ")


def get_platform_emoji() -> str:
    return "🌘"


def uptime() -> int:
    with contextlib.suppress(Exception):
        import psutil

        return round(time.time() - psutil.Process(os.getpid()).create_time())
    return round(time.perf_counter() - init_ts)


def get_uptime() -> int:
    return uptime()


def formatted_uptime() -> str:
    total = uptime()
    days, rest = divmod(total, 86400)
    formatted = str(timedelta(seconds=rest))
    return f"{days} day(s), {formatted}" if days else formatted


def get_ram_usage() -> float:
    try:
        import psutil

        process = psutil.Process(os.getpid())
        mem = process.memory_info()[0] / 2.0**20
        for child in process.children(recursive=True):
            mem += child.memory_info()[0] / 2.0**20
        return round(mem, 1)
    except Exception:
        return 0


def get_cpu_usage() -> str:
    try:
        import psutil

        return f"{psutil.cpu_percent(interval=0.1):.2f}"
    except Exception:
        return "0.00"


def get_disk_usage() -> dict:
    try:
        import psutil

        disk = psutil.disk_usage(os.path.abspath(os.sep))
        return {
            "total": round(disk.total / 1024**3, 2),
            "used": round(disk.used / 1024**3, 2),
            "free": round(disk.free / 1024**3, 2),
            "percent": disk.percent,
        }
    except Exception:
        return {"total": 0, "used": 0, "free": 0, "percent": 0}


def get_hostname() -> str:
    try:
        return socket.gethostname()
    except Exception:
        return "unknown"


def resolve_domain(domain: str) -> str:
    try:
        return socket.gethostbyname(domain)
    except Exception:
        return ""


def is_port_open(host: str, port: int) -> bool:
    with contextlib.suppress(Exception):
        with socket.create_connection((host, port), timeout=2):
            return True
    return False


def get_network_interfaces() -> dict:
    try:
        import psutil

        return {
            name: next((a.address for a in addrs if a.family == socket.AF_INET), "")
            for name, addrs in psutil.net_if_addrs().items()
        }
    except Exception:
        return {}


def get_ip_address() -> str:
    try:
        import requests

        return requests.get("https://api.ipify.org?format=json", timeout=5).json()["ip"]
    except Exception:
        return "Unknown"


def format_file_size(size_bytes: int) -> str:
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"


def get_iso_time() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def safe_getattr(obj, attr, default=None):
    try:
        return getattr(obj, attr, default)
    except Exception:
        return default


# ---------------------------------------------------------------------- версия и git (в FRX git нет)


def get_version_raw() -> str:
    from . import version as _v

    return ".".join(map(str, _v.__version__))


version = get_version_raw


def get_git_hash() -> typing.Union[str, bool]:
    return False


def get_git_info() -> typing.Tuple[str, str]:
    return "", ""


def get_commit_url() -> str:
    return "FRX HikkaCompat"


def get_git_status() -> str:
    return ""


def get_last_commit_message() -> str:
    return ""


def get_commit_count() -> int:
    return 0


def is_up_to_date() -> bool:
    return True


# ---------------------------------------------------------------------- плейсхолдеры (Heroku)


def register_placeholder(placeholder: str, callback, description: str = None) -> bool:
    owner = getattr(callback, "__self__", None)
    custom_placeholders[placeholder] = {
        "module_name": owner.__class__.__name__ if owner else "",
        "module_instance": owner,
        "callback": callback,
        "description": description,
        "placeholder_name": placeholder,
    }
    return True


async def get_placeholder(placeholder: str, data: dict = None):
    callback = custom_placeholders[placeholder]["callback"]
    try:
        result = callback(data)
    except TypeError:
        result = callback()
    if inspect.isawaitable(result):
        result = await result
    return str(result)


async def get_placeholders(data, custom_message):
    if custom_message is None:
        return data
    for item in custom_placeholders.values():
        if f"{{{item['placeholder_name']}}}" in custom_message:
            data[item["placeholder_name"]] = await get_placeholder(item["placeholder_name"], data)
    return data


def unregister_placeholders(module_name: str) -> bool:
    for name in [n for n, d in custom_placeholders.items() if d.get("module_name") == module_name]:
        del custom_placeholders[name]
    return True


def config_placeholders():
    result = [f"{{{name}}} - {d.get('description') or 'No docs'}" for name, d in custom_placeholders.items()]
    return "\n".join(result) if result else None


def module_placeholders(module_name: str) -> list:
    return [name for name, d in custom_placeholders.items() if d.get("module_name") == module_name]


def help_placeholders(module_name, self=None) -> list:
    return [
        f"▫️ {{{name}}} - {d.get('description') or 'No docs'}"
        for name, d in custom_placeholders.items()
        if d.get("module_name") == module_name
    ]


def debug_placeholders():
    return custom_placeholders
