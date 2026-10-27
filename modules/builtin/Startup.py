"""
modules/builtin/Startup.py

Автонастройка стационарного бота при каждом запуске юзербота.

Что делает:
    1. Если стационарный бот ещё не создан/не указан (aiogram_token пуст
       в user/config.json) — создаёт его через диалог с @BotFather ОТ ЛИЦА
       текущего аккаунта (обычные сообщения через Telethon, как будто
       владелец сам пишет @BotFather), и сохраняет полученный токен И в
       user/config.json на диске, И в engine.config — это ТОТ ЖЕ dict-объект,
       что видит main.py. Благодаря этому main.py, проверяя
       config.get('aiogram_token') СРАЗУ ПОСЛЕ `await core.start()` (см.
       main.py), увидит уже готовый токен и сам поднимет стационарного
       бота (create_bot() + start_polling()) — никакого дублирующего
       кода для подключения бота здесь не нужно.
    2. Если токен ЕСТЬ, но бот стал недоступен (например,
       был удалён владельцем через @BotFather, либо токен был отозван
       через /revoke) — это проверяется реальным вызовом getMe() по
       сохранённому токену при каждом старте. Если бот недоступен, для
       юзербота это равносильно "бота нет вообще": проводится тот же
       диалог с @BotFather и создаётся НОВЫЙ бот, новый токен сохраняется
       туда же (user/config.json + engine.config), старые "диалог уже начат"
       флаги сбрасываются (у нового бота диалогов ни с одним аккаунтом
       ещё не было), владельцу отправляется уведомление в Избранное о
       том, что бот был пересоздан.
    3. Если сам владелец добавил стационарного бота в свой
       личный чёрный список (ЧС) Telegram, юзербот при старте это
       обнаруживает (contacts.GetBlockedRequest) и сам достаёт бота из ЧС
       (contacts.UnblockRequest), затем заново пишет ему /start (пока
       аккаунт держит бота в ЧС, бот в принципе не может ответить), и
       уведомляет владельца в Избранном, что это было сделано.
    4. После создания/восстановления бота — пишет ему /start от лица
       аккаунта, чтобы у бота появился с нами диалог (Bot API не даёт
       боту написать первым — только ответить на уже начатый пользователем
       диалог; это же нужно и логам модуля Userbot (modules/builtin/Userbot.py), чтобы бот вообще
       мог слать нам ЛС).
    5. Инлайн-режим бота: без него инлайн-формы (.accounts, .config, ...) падают с
       BotInlineDisabledError. Поэтому после создания бота, а для уже существующего —
       всякий раз, когда getMe() показывает supports_inline_queries=False, юзербот
       проводит диалог с @BotFather:
       /setinline (плейсхолдер "FRX") и /setinlinefeedback -> 100%. Выбор кнопки
       "100%" берётся из клавиатуры самого BotFather (запасной вариант — "Enabled").
       Что уже настроено, запоминается в БД (ключ inline_configured = username бота),
       повторно диалог не проводится. Ошибка настройки не роняет запуск: она пишется
       в терминал, а попытка повторится при следующем старте.
    5а. Аватарка бота — логотип FRX (assets/logo/frx_avatar.png). Ставится один раз
       на каждого бота (БД: avatar_set = username) и только если у бота ещё нет фото —
       своя аватарка владельца не перезаписывается, а удалённую FRX заново не вернёт.
       Сначала через Bot API (setMyProfilePhoto), если не вышло — диалогом /setuserpic
       с @BotFather.
    5б. Самопроверка инлайна при каждом запуске (@bot.on_start — когда бот уже принимает апдейты): аккаунт
       отправляет боту в ЛС настоящую инлайн-форму и ждёт подтверждения ботом. getMe() показывает только
       «инлайн включён», а выключенный инлайн-фидбэк (формы тогда падают по таймауту) виден лишь так.
       Не вышло — /setinline + /setinlinefeedback через @BotFather и повторная проверка; тестовое сообщение
       удаляется. Итог (если что-то пришлось чинить) — в Избранное.
    5в. .bottoken <токен> — сменить стационарного бота: команда сразу удаляется из чата (токен — секрет),
       токен проверяется (getMe), бот настраивается (инлайн, фидбэк, аватарка, /start), токен пишется в
       user/config.json, и юзербот перезапускается уже с новым ботом. .bottoken без аргументов — текущий бот.
    6. При переключении на другой аккаунт (@decorators.on_account_switch())
       — бота НЕ создаёт (он один на весь юзербот, user/config.json общий для
       всех аккаунтов), только проверяет ЧС и пишет /start от лица НОВОГО
       аккаунта, если тот ещё не начинал диалог с ботом.

Имя бота (отображается в профиле бота): "[FRX] Userbot of {first_name}"
Username бота: "frx_{основа}_userbot", при занятости — "frx{2–4 случайные цифры}_{основа}_userbot",
а после нескольких занятых подряд — основа меняется на случайное слово (до 20 попыток всего).
Основа: username аккаунта → иначе ник (first/last name; кириллица транслитерируется, остаются
только латинские буквы и цифры) → иначе случайно сгенерированное слово (не из списка).

Надёжность диалога с @BotFather (раньше при плохой связи юзербот «терял нить»: ответ на прошлый
шаг принимался за ответ на текущий, BotFather отвечал справкой, а бот создавался заново при каждом
перезапуске):
    - обработчик ответа ставится ДО отправки сообщения, ответом считается только входящее
      сообщение BotFather с id больше id нашего сообщения; потерянные апдейты добираются из истории;
    - каждый шаг /newbot проверяется по ответу; неожиданный ответ — /cancel и стоп, без перебора дальше;
    - все диалоги с BotFather идут строго по одному (asyncio.Lock), перед началом — /cancel;
    - автопоиск: прежде чем создавать бота (и при первом входе в аккаунт, и когда прежний бот недоступен),
      юзербот берёт у BotFather список ботов аккаунта (/token) и, если среди них есть бот с «frx» в username,
      забирает его токен — новый создаётся, только если таких ботов нет. Первым берётся бот, создание которого
      оборвалось (username запоминается в БД: botfather_pending), затем — по схеме имени этого аккаунта;
    - не больше 3 попыток создать бота за сутки (БД: bot_create_log) — дальше только уведомление.

Ограничение: диалог с @BotFather построен на разборе его текстовых
ответов (ключевые фразы вроде "already taken" — сообщения BotFather
приходят на английском) — если Telegram когда-нибудь ощутимо поменяет
формулировки, шаг может не распознаться; в этом случае @on_error
(логи модуля Userbot) пришлёт исключение в ЛС с текстом ответа
BotFather, по которому легко поправить регулярку/условие ниже.
"""

import asyncio
import functools
import json
import re
import secrets
import time
from pathlib import Path

from aiogram import Bot as AiogramBot
from telethon import events as tl_events, utils as tl_utils
from telethon.tl.functions.contacts import GetBlockedRequest, UnblockRequest

import engine
import decorators
import bot
import db
import visual
import accounts

NAME = "Startup"
MODULE_NAME = "startup"
VERSION = "2.5"
AUTHOR = "FRX"
DESCRIPTION = "Автосоздание/восстановление стационарного бота через BotFather, включение его инлайн-режима и инлайн-фидбэка (100%), аватарка-логотип FRX, снятие с ЧС и старт диалога при запуске/смене аккаунта"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Startup.py"

from core.paths import CONFIG_PATH
BOTFATHER = 'BotFather'
_TOKEN_RE = re.compile(r'\d{6,}:[A-Za-z0-9_-]{30,}')
_BOTFATHER_TIMEOUT = 30  # секунд ожидания ответа BotFather на каждом шаге диалога


_BOTFATHER_POLL = 5      # раз в столько секунд ожидания заглядываем в историю — вдруг апдейт потерялся
_BF_LOCK = asyncio.Lock()  # диалоги с BotFather — строго по одному (инлайн, аватарка, создание)


# --: username нового бота ------------------------------------------------

_TRANSLIT = {
    'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'e', 'ж': 'zh', 'з': 'z', 'и': 'i',
    'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm', 'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't',
    'у': 'u', 'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch', 'ъ': '', 'ы': 'y', 'ь': '',
    'э': 'e', 'ю': 'yu', 'я': 'ya', 'і': 'i', 'ї': 'yi', 'є': 'ye', 'ґ': 'g', 'ў': 'u',
}


def _translit(text: str) -> str:
    out = []
    for ch in text or '':
        low = ch.lower()
        if low in _TRANSLIT:
            t = _TRANSLIT[low]
            out.append(t.capitalize() if ch != low and t else t)
        else:
            out.append(ch)
    return ''.join(out)


def _alnum(text: str) -> str:
    """Только латинские буквы и цифры (ведущие цифры можно: username всё равно начинается с «frx»)."""
    return re.sub(r'[^A-Za-z0-9]', '', text or '')


def _random_word(min_len: int = 6, max_len: int = 9) -> str:
    """Случайное «слово» — чередование согласных и гласных, каждый раз новое (не из списка)."""
    consonants, vowels = 'bcdfghjklmnprstvwxz', 'aeiouy'
    n = min_len + secrets.randbelow(max_len - min_len + 1)
    start = secrets.randbelow(2)
    return ''.join(secrets.choice(consonants if (i + start) % 2 == 0 else vowels) for i in range(n))


def _account_username(me) -> str:
    """username аккаунта: основной, а если его нет — первый активный из коллекционных (me.usernames)."""
    if getattr(me, 'username', None):
        return me.username
    for u in getattr(me, 'usernames', None) or []:
        if getattr(u, 'active', False) and getattr(u, 'username', None):
            return u.username
    return ''


def _username_base(me, max_len: int = 20) -> str:
    """
    Основа username бота: username аккаунта → ник (буквы и цифры, кириллица — транслитом) → случайное слово.
    Раньше ник из одной кириллицы превращался в 'user' — и у всех таких людей боты назывались frx_user_userbot,
    который давно занят, поэтому шёл долгий перебор frx34_user_userbot, frx77_user_userbot…
    """
    uname = re.sub(r'_+', '_', re.sub(r'[^A-Za-z0-9_]', '', _account_username(me))).strip('_')
    if uname:
        return uname[:max_len].rstrip('_')
    for part in (getattr(me, 'first_name', None), getattr(me, 'last_name', None)):
        nick = _alnum(_translit(part or ''))
        if nick:                                                   # ник «4» -> frx_4_userbot
            return nick[:max_len]
    return _random_word()[:max_len]


def _username_candidates(me, attempts: int = 20):
    """frx_<основа>_userbot → frx<цифры>_<основа>_userbot → после 6 занятых — та же схема со случайным словом."""
    base = _username_base(me, 20)
    yield f"frx_{base}_userbot"                                   # 4 + 20 + 8 = 32 — лимит Telegram
    short = base[:16].rstrip('_')
    for i in range(1, attempts):
        if i == 6:
            short = _random_word(6, 9)
        digits = str(10 + secrets.randbelow(9990))                # 2–4 цифры: 3 + 4 + 1 + 16 + 8 = 32
        yield f"frx{digits}_{short}_userbot"


def _display_name(me) -> str:
    who = (getattr(me, 'first_name', None) or '').strip() or _account_username(me) or 'user'
    return f"[FRX] Userbot of {who}"[:64]                         # лимит имени бота — 64 символа


# --: диалог с @BotFather -------------------------------------------------

class BotFatherTimeout(RuntimeError):
    """BotFather не ответил за отведённое время (обычно — проблемы со связью)."""


async def _botfather_ask_msg(text: str = None, file: str = None, timeout: float = _BOTFATHER_TIMEOUT):
    """
    Отправляет @BotFather text (или файл) от лица аккаунта и возвращает ПЕРВЫЙ ответ BotFather, пришедший
    ПОСЛЕ нашего сообщения (id ответа > id отправленного).

    Раньше обработчик ответа ставился уже после отправки: при плохой связи ответ мог прийти раньше и
    потеряться, а следующий шаг получал чужой ответ — диалог съезжал (BotFather отвечал справкой на
    «@frx_..._userbot», бот создавался повторно). Теперь обработчик стоит заранее, старые ответы
    отбрасываются по id, а если апдейт так и не пришёл — ответ добирается из истории чата.
    """
    client = engine.client
    peer = await client.get_input_entity(BOTFATHER)
    bf_id = tl_utils.get_peer_id(peer)
    queue: asyncio.Queue = asyncio.Queue()

    async def _on_reply(event):
        await queue.put(event.message)

    builder = tl_events.NewMessage(chats=[bf_id], incoming=True)
    client.add_event_handler(_on_reply, builder)
    try:
        if file is not None:
            sent = await client.send_file(peer, file)
        else:
            sent = await client.send_message(peer, text, parse_mode=None, link_preview=False)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            left = deadline - loop.time()
            if left <= 0:
                break
            try:
                msg = await asyncio.wait_for(queue.get(), min(left, _BOTFATHER_POLL))
                if msg.id > sent.id and not msg.out:
                    return msg
                continue                                           # запоздалый ответ на прошлый шаг — мимо
            except asyncio.TimeoutError:
                pass
            try:                                                   # апдейт мог потеряться — смотрим историю
                history = await client.get_messages(peer, min_id=sent.id, limit=5)
                replies = sorted((m for m in history if not m.out and m.id > sent.id), key=lambda m: m.id)
                if replies:
                    return replies[0]
            except Exception:
                pass
        raise BotFatherTimeout(f"BotFather не ответил за {int(timeout)} с на «{(text or 'файл')[:40]}» (нет связи?)")
    finally:
        client.remove_event_handler(_on_reply, builder)


async def _botfather_ask(text: str, timeout: float = _BOTFATHER_TIMEOUT) -> str:
    """Отправляет text @BotFather от лица аккаунта и ждёт ответ (raw_text)."""
    msg = await _botfather_ask_msg(text, timeout=timeout)
    return msg.raw_text or ''


async def _botfather_reset():
    """/cancel — сбросить незаконченную команду BotFather (best-effort)."""
    try:
        await _botfather_ask('/cancel', timeout=15)
    except Exception:
        pass


def _botfather_dialog(func):
    """Диалог с BotFather целиком под общим замком; при любой ошибке — /cancel, чтобы не оставить его в полушаге."""
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        async with _BF_LOCK:
            await _botfather_reset()
            try:
                return await func(*args, **kwargs)
            except Exception:
                await _botfather_reset()
                raise
    return wrapper


def _flood_wait_seconds(text: str):
    """«Sorry, too many attempts. Please try again in 15 seconds.» -> 15; иначе None."""
    low = (text or '').lower()
    if 'too many attempts' not in low and 'try again in' not in low:
        return None
    m = re.search(r'(\d+)\s*(second|sec|minute|min|hour)', low)
    if not m:
        return 3600
    n = int(m.group(1))
    unit = m.group(2)
    return n * (3600 if unit.startswith('hour') else 60 if unit.startswith('min') else 1)


# --: инлайн-режим стационарного бота --------------------------

_INLINE_PLACEHOLDER = 'FRX'
_INLINE_FEEDBACK_PERCENT = '100%'


def _button_texts(msg) -> list:
    """Тексты кнопок (reply-клавиатуры BotFather) сообщения; [] если кнопок нет."""
    try:
        return [b.text for row in (msg.buttons or []) for b in row]
    except Exception:
        return []


def _pick_feedback_option(buttons: list):
    """Какую кнопку нажать в /setinlinefeedback: '100%', иначе 'Enabled', иначе None."""
    for wanted in (_INLINE_FEEDBACK_PERCENT.lower(), 'enabled'):
        for b in buttons:
            if b.strip().lower() == wanted:
                return b
    return None


class ForeignBotError(RuntimeError):
    """BotFather: «Invalid bot selected» — бот принадлежит другому аккаунту, настраивать его отсюда нельзя."""


@_botfather_dialog
async def _configure_bot_inline(username: str):
    """
    Включает у бота @username инлайн-режим (/setinline) и инлайн-фидбэк 100% (/setinlinefeedback)
    диалогом с @BotFather. RuntimeError с текстом ответа BotFather — если он ответил неожиданно
    (/cancel после ошибки и замок — в @_botfather_dialog).
    """
    handle = f"@{username}"

    # 1. /setinline -> выбрать бота -> плейсхолдер
    await _botfather_ask('/setinline')
    reply = await _botfather_ask(handle)
    if 'invalid' in reply.lower():
        raise ForeignBotError(f"бот {handle} создан другим аккаунтом — BotFather этого аккаунта им не управляет ({reply[:120]})")
    reply = await _botfather_ask(_INLINE_PLACEHOLDER)
    if 'success' not in reply.lower():
        raise RuntimeError(f"BotFather ответил неожиданно на шаге /setinline (плейсхолдер): {reply[:300]}")

    # 2. /setinlinefeedback -> выбрать бота -> 100% (кнопку берём из клавиатуры BotFather)
    await _botfather_ask('/setinlinefeedback')
    msg = await _botfather_ask_msg(handle)
    if 'invalid' in (msg.raw_text or '').lower():
        raise RuntimeError(f"BotFather не принял бота {handle} на шаге /setinlinefeedback: {msg.raw_text[:300]}")
    choice = _pick_feedback_option(_button_texts(msg)) or _INLINE_FEEDBACK_PERCENT
    for _ in range(3):  # на случай двухшагового выбора (Enabled -> процент)
        msg = await _botfather_ask_msg(choice)
        text = msg.raw_text or ''
        if 'success' in text.lower():
            return
        nxt = _pick_feedback_option(_button_texts(msg))
        if not nxt or nxt == choice:
            raise RuntimeError(f"BotFather ответил неожиданно на шаге /setinlinefeedback: {text[:300]}")
        choice = nxt
    raise RuntimeError("BotFather не подтвердил настройку /setinlinefeedback")


_PENDING_TTL = 7 * 86400          # сколько помним username, токен которого не дошёл
_CREATE_LIMIT = 3                 # не больше стольких попыток создать бота…
_CREATE_WINDOW = 86400            # …за сутки


_BOT_HANDLE_RE = re.compile(r'@([A-Za-z][A-Za-z0-9_]{3,31})')


def _bot_list_from(msg) -> list:
    """Username'ы ботов из ответа BotFather на /token: кнопки клавиатуры, а если кнопок нет — @упоминания в тексте."""
    buttons = _button_texts(msg)
    sources = [t.strip() if t.strip().startswith('@') else '@' + t.strip() for t in buttons] if buttons else [msg.raw_text or '']
    names = []
    for text in sources:
        for m in _BOT_HANDLE_RE.finditer(text):
            u = m.group(1)
            if u.lower() != 'botfather' and u.lower() not in (n.lower() for n in names):
                names.append(u)
    return names


def _rank_frx_bots(names: list, me, prefer: str = None) -> list:
    """
    Боты аккаунта, в username которых есть «frx», в порядке «какого брать»: тот, создание которого оборвалось
    (prefer) → по нашей схеме для этого аккаунта (frx…_<основа>_userbot) → любой frx…_userbot → любой с «frx».
    Внутри группы — как в списке BotFather.
    """
    base = _username_base(me, 20).lower() if me is not None else ''

    def rank(u):
        low = u.lower()
        if prefer and low == prefer.lower():
            return 0
        if low.startswith('frx') and low.endswith('_userbot') and base and f"_{base}_" in f"_{low.split('_', 1)[-1]}":
            return 1
        if low.startswith('frx') and low.endswith('userbot'):
            return 2
        return 3
    found = [u for u in names if 'frx' in u.lower()]
    return sorted(found, key=lambda u: (rank(u), names.index(u)))


@_botfather_dialog
async def _find_frx_bot(me, prefer: str = None):
    """
    Автопоиск уже существующего бота FRX у аккаунта: /token → список ботов (кнопки BotFather) → первый подходящий
    бот с «frx» в username → его токен. /token токен НЕ меняет (это делает /revoke), так что бот, который где-то
    работает, не ломается. -> (token, username, [все найденные frx-боты]) или None, если таких ботов нет.
    """
    tried = []
    for _round in range(3):
        msg = await _botfather_ask_msg('/token')
        text = (msg.raw_text or '').lower()
        if 'no bots' in text or "don't have" in text:
            return None                                          # ботов у аккаунта нет вовсе
        if (wait := _flood_wait_seconds(text)) is not None:
            if wait > 120:
                raise RuntimeError(f"BotFather просит подождать {wait} с: {msg.raw_text[:200]}")
            await asyncio.sleep(wait + 2)
            continue
        if 'bot' not in text:
            raise RuntimeError(f"BotFather ответил неожиданно на /token: {(msg.raw_text or '')[:200]}")
        ranked = [u for u in _rank_frx_bots(_bot_list_from(msg), me, prefer) if u not in tried]
        if not ranked:
            await _botfather_reset()                             # BotFather ждёт выбор бота — снимаем
            return None
        pick = ranked[0]
        tried.append(pick)
        reply = await _botfather_ask(f"@{pick}")
        m = _TOKEN_RE.search(reply)
        if m:
            return m.group(0), pick, [pick] + ranked[1:]
        engine.tlog(f"{visual.visual('status_warning')} [Startup] BotFather не отдал токен @{pick}: {reply[:150]}")
    return None


@_botfather_dialog
async def _create_bot_via_botfather(me) -> tuple[str, str]:
    """
    Проводит диалог с @BotFather и создаёт нового бота. Возвращает (token, username).
    Каждый шаг проверяется по ответу BotFather: если диалог «съехал» (ответ не тот, что ждём на этом шаге,
    например справка BotFather) — /cancel и RuntimeError, без слепого перебора дальше.
    """
    for _flood in range(3):
        reply = await _botfather_ask('/newbot')
        low = reply.lower()
        if 'too many bots' in low or 'more than 20' in low:
            raise RuntimeError(f"BotFather отказал в создании бота (у аккаунта уже 20 ботов): {reply[:300]}")
        wait = _flood_wait_seconds(reply)
        if wait is None:
            break
        # «Sorry, too many attempts. Please try again in 52 seconds.» — /newbot НЕ начат. Раньше юзербот всё равно
        # слал имя бота, BotFather отвечал справкой, и диалог разваливался.
        if wait > 120:
            raise RuntimeError(f"BotFather просит подождать {wait} с перед созданием бота: {reply[:200]}")
        engine.tlog(f"{visual.visual('info')} [Startup] BotFather просит подождать {wait} с — жду и повторяю /newbot")
        await asyncio.sleep(wait + 2)
    else:
        raise RuntimeError(f"BotFather не даёт начать /newbot (слишком много попыток): {reply[:200]}")
    if 'name' not in low:
        raise RuntimeError(f"Неожиданный ответ BotFather на /newbot: {reply[:300]}")

    reply = await _botfather_ask(_display_name(me))
    if 'username' not in reply.lower():
        raise RuntimeError(f"Неожиданный ответ BotFather на имя бота: {reply[:300]}")

    tried = []
    for candidate in _username_candidates(me):
        for _flood in range(3):
            await db.set('botfather_pending', {'username': candidate, 'ts': time.time()})
            reply = await _botfather_ask(f"@{candidate}")
            wait = _flood_wait_seconds(reply)
            if wait is None:
                break
            await db.delete('botfather_pending')
            if wait > 60:
                raise RuntimeError(f"BotFather просит подождать {wait} с (слишком много попыток): {reply[:200]}")
            await asyncio.sleep(wait + 1)                     # BotFather остаётся на шаге username — повторяем тот же
        else:
            raise RuntimeError(f"BotFather не принимает username — просит подождать: {reply[:200]}")
        tried.append(candidate)

        m = _TOKEN_RE.search(reply)
        if m:
            await db.delete('botfather_pending')
            return m.group(0), candidate

        low = reply.lower()
        if 'error has occurred' in low or 'try again later' in low:
            # «Sorry, an error has occurred during your request» — создан бот или нет, неизвестно: pending оставляем,
            # при следующем запуске /token проверит этот username, а не создаст ещё одного
            raise RuntimeError(f"BotFather вернул ошибку на username @{candidate} (проверю при следующем запуске): {reply[:200]}")
        await db.delete('botfather_pending')                   # BotFather ответил — этот username точно не создан
        if any(k in low for k in ('already taken', 'is taken', 'occupied', 'invalid', "can't", 'cannot',
                                  'must end', 'sorry')):
            continue                                            # занят/не подходит — BotFather ждёт новый вариант
        raise RuntimeError(f"Неожиданный ответ BotFather при выборе username (диалог сброшен): {reply[:300]}")

    raise RuntimeError(f"Не удалось подобрать свободный username для бота за {len(tried)} попыток: "
                       f"{', '.join(tried[:5])}…")


async def _create_attempt_allowed() -> bool:
    """Предохранитель: не больше _CREATE_LIMIT попыток создать бота за _CREATE_WINDOW — чтобы при любом
    непредвиденном сбое юзербот не плодил ботов при каждом перезапуске."""
    now = time.time()
    log = [t for t in (await db.get('bot_create_log', []) or []) if isinstance(t, (int, float)) and now - t < _CREATE_WINDOW]
    if len(log) >= _CREATE_LIMIT:
        await db.set('bot_create_log', log)
        return False
    log.append(now)
    await db.set('bot_create_log', log)
    return True


async def _obtain_new_bot(me):
    """
    Бот для юзербота, когда своего (рабочего) нет — в том числе при первом входе в аккаунт:
      1. автопоиск: у аккаунта уже есть бот с «frx» в username (в том числе тот, создание которого оборвалось
         прошлый раз — botfather_pending) — берём его токен через /token;
      2. совпадений нет — создаём нового через /newbot.
    -> (token, username, 'found' | 'created', [найденные frx-боты]) или None, если создавать сейчас нельзя.
    """
    pending = await db.get('botfather_pending', None)
    prefer = None
    if isinstance(pending, dict) and pending.get('username') and time.time() - float(pending.get('ts') or 0) < _PENDING_TTL:
        prefer = pending['username']
    try:
        found = await _find_frx_bot(me, prefer)
    except BotFatherTimeout:
        raise                                                    # связи нет — ничего не трогаем, попробуем позже
    except Exception as e:
        found = None
        engine.tlog(f"{visual.visual('status_warning')} [Startup] автопоиск ботов через /token не сработал: {e}")
    await db.delete('botfather_pending')                         # бот из pending либо найден, либо так и не создан
    if found:
        token, username, all_found = found
        engine.tlog(f"{visual.visual('status_success')} [Startup] Найден существующий бот @{username} — беру его, "
                    f"новый не создаю" + (f" (ещё frx-боты: {', '.join('@' + u for u in all_found[1:])})" if len(all_found) > 1 else ''))
        return token, username, 'found', all_found

    if not await _create_attempt_allowed():
        engine.tlog(f"{visual.visual('status_error')} [Startup] Уже {_CREATE_LIMIT} попытки создать бота за сутки — "
                    f"больше не пробую автоматически. Создайте бота в @BotFather и подключите: .bottoken <токен>")
        if not await db.get('create_limit_notified'):
            await db.set('create_limit_notified', True)
            await _notify_owner(
                visual.warn('Стационарный бот не создан') + "\n\n"
                + visual.hint(f'юзербот уже {_CREATE_LIMIT} раза пытался создать бота за сутки и остановился, '
                              'чтобы не плодить ботов. Создайте бота в @BotFather (/newbot) и подключите его: '
                              '<code>.bottoken токен</code>'))
        return None
    await db.delete('create_limit_notified')
    token, username = await _create_bot_via_botfather(me)
    return token, username, 'created', []


async def _bot_username_from_token(token: str):
    """
    Узнаёт username бота по токену через Bot API (getMe), не поднимая
    полноценный Dispatcher/polling — если стационарного бота ещё нет в
    этом процессе, main.py поднимет его сам, увидев aiogram_token в
    user/config.json (см. докстринг модуля выше); эта функция только читает
    username, ничего не запуская.
    """
    tmp_bot = AiogramBot(token=token)
    try:
        me = await tmp_bot.get_me()
        return me.username
    except Exception as e:
        engine.tlog(f"{visual.visual('status_error')} [Startup] не удалось получить username бота по токену: {e}")
        return None
    finally:
        await tmp_bot.session.close()


async def _bot_is_alive(token: str):
    """
    Проверка сохранённого токена: True — бот жив, False — Telegram ТОЧНО сказал, что токена нет
    (401 Unauthorized / 404: бот удалён или /revoke), None — проверить не удалось (нет сети, сбой Telegram).

    Раньше любая ошибка считалась «бот удалён»: при мигающем интернете юзербот создавал нового бота
    и перезаписывал токен старого. Теперь при сетевой ошибке пробуем ещё несколько раз, а если связи
    так и нет — оставляем прежний токен как есть.
    """
    if not token:
        return False
    from aiogram.exceptions import TelegramUnauthorizedError, TelegramNotFound
    last = None
    for attempt in range(4):
        tmp_bot = AiogramBot(token=token)
        try:
            await tmp_bot.get_me()
            return True
        except (TelegramUnauthorizedError, TelegramNotFound) as e:
            engine.tlog(f"{visual.visual('status_warning')} [Startup] Telegram отверг токен стационарного бота "
                        f"(бот удалён или токен отозван): {e}")
            return False
        except Exception as e:                       # сеть, таймаут, 5xx — это не «бот удалён»
            last = e
        finally:
            await tmp_bot.session.close()
        await asyncio.sleep(3 * (attempt + 1))
    engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось проверить стационарного бота (нет связи?): {last} "
                f"— оставляю прежний токен, бота не пересоздаю")
    return None


async def _bot_status(token: str):
    """(username, supports_inline_queries) по getMe() или None, если бот недоступен."""
    tmp_bot = AiogramBot(token=token)
    try:
        me = await tmp_bot.get_me()
        return me.username, bool(getattr(me, 'supports_inline_queries', False))
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось получить getMe() бота: {e}")
        return None
    finally:
        await tmp_bot.session.close()


async def _save_token(token: str, username: str, old_is_dead: bool = False):
    try:                                           # новый токен — тоже секрет для полного лога
        from core import fulllog
        fulllog.add_secret(token)
    except Exception:
        pass
    engine.config['aiogram_token'] = token
    try:
        on_disk = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        on_disk = dict(engine.config)
    old = on_disk.get('aiogram_token')
    if old and old != token and not old_is_dead:   # прежний токен не теряем: вернуть бота — .bottoken <токен из aiogram_token_prev>
                                                   # (токен, который Telegram уже отверг, туда не пишем — не затираем рабочий)
        on_disk['aiogram_token_prev'] = old
    on_disk['aiogram_token'] = token
    CONFIG_PATH.write_text(json.dumps(on_disk, indent=2, ensure_ascii=False), encoding='utf-8')
    await db.set('cached_bot_username', username)


async def _reset_all_dialog_flags():
    """У нового бота ни с одним аккаунтом ещё не было диалога — сбрасываем все флаги разом."""
    for number in accounts.account_list():
        acc_info = accounts.account_info(number).get('info', {})
        acc_id = acc_info.get('id')
        if acc_id:
            await db.delete(f"dialog_started:{acc_id}")


async def _notify_owner(text: str):
    """Уведомление владельцу в Избранное — best-effort, ошибка отправки не должна ронять Startup."""
    try:
        await engine.send_message('me', text)
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось отправить уведомление в Избранное: {e}")


async def _ensure_bot_exists():
    """
    Создаёт стационарного бота, если он ещё не создан/не указан
    (aiogram_token пуст), а ЕЩЁ И если сохранённый токен
    указан, но бот по нему больше недоступен (удалён/отозван) — тогда
    создаётся новый бот взамен. Конфигурирует и "подключает" его к
    юзерботу. Фактическое подключение (create_bot() + start_polling())
    делает main.py сам, увидев обновлённый engine.config['aiogram_token']
    сразу после `await core.start()` (main.py передаёт в Core(config) тот
    же dict, что держит сам, поэтому правка через engine.config видна и
    там).
    """
    token = engine.config.get('aiogram_token')
    had_token = bool(token)

    if had_token:
        alive = await _bot_is_alive(token)
        if alive is not False:
            return  # жив — или проверить не удалось (сеть): прежний токен не трогаем

    me = await engine.client.get_me()
    try:
        got = await _obtain_new_bot(me)
    except BotFatherTimeout as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] {e} — создание бота отложено до следующего запуска")
        return
    if not got:
        return
    new_token, username, how, all_found = got
    await _save_token(new_token, username, old_is_dead=had_token)
    # новый для юзербота бот — флаги «диалог начат» от прежнего бота к нему не относятся
    await _reset_all_dialog_flags()

    if how == 'found':
        others = all_found[1:]
        await _notify_owner(
            visual.title('Подключён существующий бот') + "\n"
            + visual.field('бот', f"@{username}") + "\n\n"
            + visual.hint(('прежний бот стал недоступен, а ' if had_token else '')
                          + 'у аккаунта нашёлся бот FRX — юзербот взял его токен вместо создания нового'
                          + (f". Другие frx-боты аккаунта: {', '.join('@' + u for u in others)} — "
                             'лишние можно удалить в @BotFather → /deletebot' if others else ''))
        )
    elif had_token:
        # Токен был, но бот оказался недоступен, а своего бота у аккаунта не нашлось — создан новый.
        engine.tlog(
            f"{visual.visual('status_warning')} [Startup] Прежний стационарный бот был недоступен "
            f"(удалён или токен отозван) — создан новый бот @{username}"
        )
        await _notify_owner(
            visual.title('Стационарный бот пересоздан') + "\n"
            + visual.field('новый бот', f"@{username}") + "\n\n"
            + visual.hint('прежний бот стал недоступен (похоже, удалён или токен отозван через @BotFather) — '
                          'юзербот создал нового и настроил его вместо старого')
        )
    else:
        engine.tlog(
            f"{visual.visual('status_success')} [Startup] Создан стационарный бот @{username} "
            f"— подключится автоматически (main.py увидит aiogram_token)"
        )


async def _ensure_inline_configured():
    """
    гарантирует, что у стационарного бота включены инлайн-режим и инлайн-фидбэк 100%
    (см. докстринг модуля, п. 5). Идемпотентно: если getMe() показывает включённый инлайн и
    для этого username настройка уже выполнялась (БД: inline_configured) — ничего не делает.
    Ошибка не роняет запуск — попытка повторится при следующем старте.
    """
    token = engine.config.get('aiogram_token')
    if not token:
        return
    status = await _bot_status(token)
    if not status:
        return
    username, supports_inline = status
    await db.set('cached_bot_username', username)        # кэш мог остаться от прошлого бота (после смены токена)
    if supports_inline or await db.get('inline_configured') == username:
        return            # инлайн включён — реально ли доходят формы, проверит самопроверка; BotFather зря не дёргаем
    if await db.get('botfather_foreign') == username:
        return            # бот другого аккаунта — BotFather отсюда им не управляет, не пробуем при каждом запуске

    engine.tlog(f"{visual.visual('info')} [Startup] Настраиваю инлайн-режим бота @{username} через BotFather...")
    try:
        await _configure_bot_inline(username)
    except ForeignBotError as e:
        await db.set('botfather_foreign', username)
        engine.tlog(f"{visual.visual('status_warning')} [Startup] {e}. Инлайн-режим включите у владельца бота: "
                    f"@BotFather → /setinline и /setinlinefeedback → 100%")
        await _notify_owner(
            visual.warn('Инлайн-режим бота выключен') + "\n" + visual.field('бот', f"@{username}") + "\n\n"
            + visual.hint('бот создан другим аккаунтом, поэтому юзербот не может включить инлайн сам. '
                          'С аккаунта-владельца: @BotFather → /setinline и /setinlinefeedback → 100%, затем .restart'))
        return
    except Exception as e:
        engine.tlog(
            f"{visual.visual('status_error')} [Startup] не удалось настроить инлайн-режим @{username}: {e} "
            f"(повторю при следующем запуске; вручную: /setinline и /setinlinefeedback -> 100% в @BotFather)"
        )
        return
    await db.set('inline_configured', username)
    engine.tlog(f"{visual.visual('status_success')} [Startup] Инлайн-режим и инлайн-фидбэк 100% включены для @{username}")
    if not supports_inline:
        await _notify_owner(
            visual.title('Инлайн-режим включён') + "\n"
            + visual.field('бот', f"@{username}") + "\n"
            + visual.field('инлайн-фидбэк', '100%') + "\n\n"
            + visual.hint('инлайн-режим был выключен, из-за этого инлайн-формы не открывались — '
                          'юзербот включил его через @BotFather')
        )



# --: аватарка стационарного бота — логотип FRX ------------------

_AVATAR = ('logo', 'frx_avatar.png')


@_botfather_dialog
async def _set_avatar_via_botfather(username: str, path: str):
    """Запасной путь: /setuserpic -> @бот -> фото, диалогом с @BotFather от лица аккаунта."""
    handle = f"@{username}"
    await _botfather_ask('/setuserpic')
    reply = await _botfather_ask(handle)
    if 'invalid' in reply.lower():
        raise ForeignBotError(f"бот {handle} создан другим аккаунтом — аватарку меняет только его владелец")
    if 'photo' not in reply.lower() and 'фото' not in reply.lower():
        raise RuntimeError(f"BotFather не принял бота {handle} на шаге /setuserpic: {reply[:300]}")
    msg = await _botfather_ask_msg(file=path, timeout=60)
    text = (msg.raw_text or '').lower()
    if 'success' not in text and 'updated' not in text and 'обновл' not in text:
        raise RuntimeError(f"BotFather ответил неожиданно после отправки фото: {(msg.raw_text or '')[:300]}")


async def _ensure_bot_avatar():
    """
    Ставит боту аватарку-логотип FRX (см. докстринг модуля, п. 5а). Идемпотентно: для каждого
    username — один раз (БД: avatar_set); если у бота уже есть фото — не трогает. Ошибка не роняет
    запуск: попытка повторится при следующем старте.
    """
    token = engine.config.get('aiogram_token')
    if not token:
        return
    try:
        path = str(engine.asset_path(*_AVATAR))
    except FileNotFoundError:
        return
    tmp_bot = AiogramBot(token=token)
    try:
        me = await tmp_bot.get_me()
        username = me.username
        await db.set('cached_bot_username', username)
        if await db.get('avatar_set') == username or await db.get('botfather_foreign') == username:
            return
        photos = await tmp_bot.get_user_profile_photos(me.id, limit=1)
        if photos.total_count:
            await db.set('avatar_set', username)          # своя аватарка уже есть — не перезаписываем
            return
        try:
            from aiogram.types import FSInputFile, InputProfilePhotoStatic
            await tmp_bot.set_my_profile_photo(photo=InputProfilePhotoStatic(photo=FSInputFile(path, filename='frx.png')))
        except Exception as e:
            engine.tlog(f"{visual.visual('info')} [Startup] Bot API не поставил аватарку ({e}) — ставлю через BotFather")
            try:
                await _set_avatar_via_botfather(username, path)
            except ForeignBotError as fe:
                await db.set('botfather_foreign', username)
                engine.tlog(f"{visual.visual('info')} [Startup] {fe} — аватарку не трогаю")
                return
        await db.set('avatar_set', username)
        engine.tlog(f"{visual.visual('status_success')} [Startup] Аватарка бота @{username} — логотип FRX")
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось поставить аватарку бота: {e} (повторю при следующем запуске)")
    finally:
        await tmp_bot.session.close()


async def _ensure_bot_not_blocked():
    """
    если владелец аккаунта лично добавил стационарного бота
    в свой чёрный список Telegram — бот физически не может написать нам
    в ЛС (Bot API этого не позволяет для отправителя из ЧС получателя).
    Юзербот сам достаёт его из ЧС и заново открывает диалог.
    """
    token = engine.config.get('aiogram_token')
    if not token:
        return  # бота ещё нет — нечего проверять

    username = await db.get('cached_bot_username', None)
    if not username:
        username = await _bot_username_from_token(token)
        if username:
            await db.set('cached_bot_username', username)
    if not username:
        return

    try:
        bot_entity = await engine.client.get_entity(f"@{username}")
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось получить entity @{username}: {e}")
        return

    try:
        blocked = await engine.client(GetBlockedRequest(offset=0, limit=100))
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Startup] не удалось получить список ЧС: {e}")
        return

    blocked_ids = {
        item.peer_id.user_id for item in getattr(blocked, 'blocked', [])
        if hasattr(item, 'peer_id') and hasattr(item.peer_id, 'user_id')
    }
    if bot_entity.id not in blocked_ids:
        return  # бот не в ЧС — всё в порядке, ничего делать не нужно

    try:
        await engine.client(UnblockRequest(id=bot_entity))
        engine.tlog(f"{visual.visual('status_success')} [Startup] Бот @{username} был в ЧС — убран из ЧС")
    except Exception as e:
        engine.tlog(f"{visual.visual('status_error')} [Startup] не удалось убрать бота @{username} из ЧС: {e}")
        return

    # Пока бот был в ЧС, диалог с ним фактически "сломан" на его стороне —
    # пишем /start заново, даже если для этого аккаунта он уже отмечен
    # начатым ранее.
    me = await engine.client.get_me()
    try:
        await engine.send_message(f"@{username}", '/start')
        await db.set(f"dialog_started:{me.id}", True)
        engine.tlog(f"{visual.visual('status_success')} [Startup] После снятия с ЧС заново начат диалог с @{username}")
    except Exception as e:
        engine.tlog(f"{visual.visual('status_error')} [Startup] не удалось написать /start после снятия с ЧС: {e}")

    await _notify_owner(
        visual.title('Бот был в чёрном списке') + "\n"
        + visual.field('бот', f"@{username}") + "\n\n"
        + visual.hint('стационарный бот был в вашем личном чёрном списке — '
                      'юзербот убрал его оттуда и заново написал ему /start')
    )


async def _ensure_dialog_started():
    """
    Пишет /start стационарному боту от лица ТЕКУЩЕГО аккаунта, если этот
    аккаунт ещё не начинал с ним диалог (без этого бот не сможет писать
    нам первым — обычное ограничение Bot API).
    """
    token = engine.config.get('aiogram_token')
    if not token:
        return  # бота вообще ещё нет — нечему писать /start

    me = await engine.client.get_me()
    dialog_key = f"dialog_started:{me.id}"
    if await db.get(dialog_key, False):
        return

    username = await db.get('cached_bot_username', None)
    if not username:
        username = await _bot_username_from_token(token)
        if username:
            await db.set('cached_bot_username', username)
    if not username:
        return  # не смогли узнать username стационарного бота — не с кем начинать диалог

    try:
        await engine.send_message(f"@{username}", '/start')
        await db.set(dialog_key, True)
        engine.tlog(f"{visual.visual('status_success')} [Startup] Начат диалог с @{username} (/start)")
    except Exception as e:
        engine.tlog(f"{visual.visual('status_error')} [Startup] не удалось написать /start боту: {e}")


@decorators.on_start()
async def startup_sequence():
    """
    Полный сценарий запуска: восстановить/создать бота при необходимости
    + снять его с ЧС, если он там оказался + начать с ним диалог.
    """
    await _ensure_bot_exists()
    await _ensure_inline_configured()
    await _ensure_bot_avatar()
    await _ensure_bot_not_blocked()
    await _ensure_dialog_started()


# --: самопроверка инлайн-формы при запуске (п. 5б) -----------------------

async def _try_inline_form(username: str):
    """Аккаунт шлёт боту в ЛС инлайн-форму и ждёт подтверждения. (True, None) или (False, причина)."""
    form = None
    try:
        form = await engine.create_inlineform(f"@{username}", visual.wait("FRX: проверка инлайн-режима…"))
        await asyncio.wait_for(form._imid(), 25)
        return True, None
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    finally:
        if form is not None:
            try:
                await form.delete()
            except Exception:
                pass


async def inline_selftest(fix: bool = True):
    """
    Проверка «форма реально доходит» и починка через BotFather. Возвращает
    {'ok': bool, 'fixed': bool, 'error': str | None, 'username': str | None}.
    """
    token = engine.config.get('aiogram_token')
    if not token:
        return {'ok': False, 'fixed': False, 'error': 'стационарного бота нет', 'username': None}
    status = await _bot_status(token)
    if not status:
        return {'ok': False, 'fixed': False, 'error': 'бот не отвечает на getMe', 'username': None}
    username = status[0]
    ok, err = await _try_inline_form(username)
    if ok:
        engine.tlog(f"{visual.visual('status_success')} [Startup] Инлайн-формы @{username} работают (самопроверка)")
        await db.set('inline_configured', username)
        return {'ok': True, 'fixed': False, 'error': None, 'username': username}
    engine.tlog(f"{visual.visual('status_warning')} [Startup] Самопроверка инлайна @{username} не прошла: {err} — "
                f"настраиваю /setinline и /setinlinefeedback через BotFather")
    if not fix:
        return {'ok': False, 'fixed': False, 'error': err, 'username': username}
    if await db.get('botfather_foreign') == username:
        engine.tlog(f"{visual.visual('status_error')} [Startup] Инлайн @{username} не работает, а бот принадлежит другому аккаунту — "
                    f"включите /setinline и /setinlinefeedback → 100% у его владельца")
        return {'ok': False, 'fixed': False, 'error': f"{err}; бот другого аккаунта", 'username': username}
    try:
        await _configure_bot_inline(username)
        await db.set('inline_configured', username)
    except ForeignBotError as e:
        await db.set('botfather_foreign', username)
        engine.tlog(f"{visual.visual('status_error')} [Startup] {e}; инлайн не работает — включите его у владельца бота")
        await _notify_owner(
            visual.err('Инлайн-формы не работают') + "\n" + visual.field('бот', f"@{username}") + "\n\n"
            + visual.hint('бот создан другим аккаунтом — с него: @BotFather → /setinline и /setinlinefeedback → 100%, затем .restart'))
        return {'ok': False, 'fixed': False, 'error': f"{err}; {e}", 'username': username}
    except Exception as e:
        engine.tlog(f"{visual.visual('status_error')} [Startup] BotFather не настроил инлайн @{username}: {e}")
        return {'ok': False, 'fixed': False, 'error': f"{err}; BotFather: {e}", 'username': username}
    await asyncio.sleep(3)                         # Telegram применяет настройки бота не мгновенно
    ok, err2 = await _try_inline_form(username)
    if ok:
        engine.tlog(f"{visual.visual('status_success')} [Startup] Инлайн @{username} починен: формы снова работают")
        await _notify_owner(
            visual.title('Инлайн-формы починены') + "\n" + visual.field('бот', f"@{username}") + "\n\n"
            + visual.hint(f'самопроверка при запуске не прошла ({err[:120]}) — юзербот включил инлайн-режим '
                          'и инлайн-фидбэк 100% через @BotFather, формы снова работают'))
        return {'ok': True, 'fixed': True, 'error': None, 'username': username}
    engine.tlog(f"{visual.visual('status_error')} [Startup] Инлайн @{username} не работает и после настройки: {err2}")
    await _notify_owner(
        visual.err('Инлайн-формы не работают') + "\n" + visual.field('бот', f"@{username}") + "\n"
        + f"<code>{err2[:300]}</code>\n\n"
        + visual.hint('вручную: @BotFather → /setinline и /setinlinefeedback → 100%, затем .restart'))
    return {'ok': False, 'fixed': False, 'error': err2, 'username': username}


@bot.on_start()
async def _inline_selftest_on_start():
    """Фоном: проверка занимает секунды (а при поломке — до минуты) и не должна задерживать bot.on_start других модулей."""
    async def run():
        await asyncio.sleep(2)                     # бот только что начал принимать апдейты
        try:
            await inline_selftest()
        except Exception as e:
            engine.tlog(f"{visual.visual('status_warning')} [Startup] самопроверка инлайна упала: {e}")
    asyncio.create_task(run())


# --: смена токена стационарного бота (п. 5в) -----------------------------

def _mask_token(token: str) -> str:
    return (token[:6] + '…' + token[-4:]) if token and len(token) > 12 else '—'


@decorators.command('bottoken', 'settoken')
async def bottoken_cmd(event):
    """сменить стационарного бота: .bottoken <токен от @BotFather> (без аргумента — текущий бот)"""
    raw = (engine.get_args() or '').strip()
    if not raw:
        token = engine.config.get('aiogram_token') or ''
        status = await _bot_status(token) if token else None
        await engine.answer(event, "\n".join([
            visual.title('Стационарный бот'),
            visual.field('бот', f"@{status[0]}" if status else 'не отвечает / не задан', code=False),
            visual.field('инлайн', ('включён' if status and status[1] else 'выключен') if status else '—', code=False),
            visual.field('токен', _mask_token(token)),
            "",
            visual.hint('сменить: <code>.bottoken 123456:ABC…</code> — сообщение с токеном сразу удаляется'),
        ]))
        return
    m = _TOKEN_RE.search(raw)
    chat = event.chat_id or 'me'
    try:
        await event.delete()                      # токен — секрет: убираем из чата сразу
    except Exception:
        pass
    if not m:
        await engine.send_message(chat, visual.err('Это не похоже на токен бота') + "\n" + visual.hint('формат: 123456789:AA…'))
        return
    token = m.group(0)
    status_msg = await engine.send_message(chat, visual.wait(f"Проверяю токен {_mask_token(token)}…"))

    async def say(text):
        try:
            await engine.answer(status_msg, text)
        except Exception:
            pass

    status = await _bot_status(token)
    if not status:
        await say(visual.err('Токен не подошёл') + "\n" + visual.hint('getMe не ответил — проверьте токен в @BotFather'))
        return
    username, supports_inline = status
    if token == engine.config.get('aiogram_token'):
        await say(visual.ok(f"Этот бот уже подключён: @{username}"))
        return
    steps = [visual.ok(f"Бот @{username} отвечает")]
    await say("\n".join(steps + [visual.wait('настраиваю…')]))
    if not supports_inline:
        try:
            await _configure_bot_inline(username)
            await db.set('inline_configured', username)
            steps.append(visual.ok('инлайн-режим и инлайн-фидбэк 100% включены через @BotFather'))
        except Exception as e:
            steps.append(visual.warn(f"инлайн не настроен: <code>{str(e)[:200]}</code> — повторю при запуске"))
    else:
        steps.append(visual.ok('инлайн-режим уже включён (фидбэк проверю при запуске)'))
    me = await engine.client.get_me()
    try:
        await engine.send_message(f"@{username}", '/start')
        await db.set(f"dialog_started:{me.id}", True)
        steps.append(visual.ok('диалог с ботом начат (/start)'))
    except Exception as e:
        steps.append(visual.warn(f"/start не отправлен: {e}"))
    old = engine.config.get('aiogram_token') or ''
    await _save_token(token, username)
    await _reset_all_dialog_flags()
    await db.set(f"dialog_started:{me.id}", True)
    await db.delete('avatar_set')
    steps.append(visual.ok('токен сохранён в user/config.json') + "  " + visual.hint(f"прежний: {_mask_token(old)}"))
    try:
        import frx_systemkeeper as keeper
        can_restart = keeper.keeper_active()
    except Exception:
        keeper, can_restart = None, False
    if can_restart:
        steps.append(visual.wait('перезапускаю юзербот с новым ботом…'))
        await say("\n".join(steps))
        keeper.write_request('restart', notify={'chat_id': status_msg.chat_id, 'message_id': status_msg.id})
        engine.request_shutdown(keeper.EXIT_RESTART)
    else:
        steps.append(visual.warn('перезапустите юзербот, чтобы подключить нового бота'))
        await say("\n".join(steps))


@decorators.on_account_switch()
async def on_account_switch(number):
    """
    При переключении аккаунта бота НЕ создаём (см. докстринг модуля) —
    только проверяем ЧС и начинаем диалог с уже существующим ботом, если
    этот аккаунт с ним ещё не общался.
    """
    await _ensure_bot_not_blocked()
    await _ensure_dialog_started()
