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
       диалог; это же нужно и modules/builtin/Logging.py, чтобы бот вообще
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
    6. При переключении на другой аккаунт (@decorators.on_account_switch())
       — бота НЕ создаёт (он один на весь юзербот, user/config.json общий для
       всех аккаунтов), только проверяет ЧС и пишет /start от лица НОВОГО
       аккаунта, если тот ещё не начинал диалог с ботом.

Имя бота (отображается в профиле бота): "[FRX] Userbot of {first_name}"
Username бота: сперва "frx_{first_name}_userbot", при занятости —
"frx{randint(10,99)}_{first_name}_userbot" (повторяем с новым числом,
пока BotFather не примет — до 20 попыток).

Ограничение: диалог с @BotFather построен на разборе его текстовых
ответов (ключевые фразы вроде "already taken" — сообщения BotFather
приходят на английском) — если Telegram когда-нибудь ощутимо поменяет
формулировки, шаг может не распознаться; в этом случае @on_error
(modules/builtin/Logging.py) пришлёт исключение в ЛС с текстом ответа
BotFather, по которому легко поправить регулярку/условие ниже.
"""

import json
import random
import re
from pathlib import Path

from aiogram import Bot as AiogramBot
from telethon.tl.functions.contacts import GetBlockedRequest, UnblockRequest

import engine
import decorators
import db
import visual
import accounts

NAME = "Startup"
MODULE_NAME = "startup"
VERSION = "2.3"
AUTHOR = "FRX"
DESCRIPTION = "Автосоздание/восстановление стационарного бота через BotFather, включение его инлайн-режима и инлайн-фидбэка (100%), аватарка-логотип FRX, снятие с ЧС и старт диалога при запуске/смене аккаунта"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Startup.py"

from core.paths import CONFIG_PATH
BOTFATHER = 'BotFather'
_TOKEN_RE = re.compile(r'\d{6,}:[A-Za-z0-9_-]{30,}')
_BOTFATHER_TIMEOUT = 30  # секунд ожидания ответа BotFather на каждом шаге диалога


def _slug(name: str, max_len: int) -> str:
    """
    Приводит first_name к виду, пригодному для username бота Telegram:
    только [A-Za-z0-9_], без ведущих цифр/подчёркиваний, обрезано до
    max_len символов (у Telegram-username лимит 32 символа целиком —
    считаем длину с запасом на "frx_"/"frxNN_" + "_userbot"). Если после
    чистки ничего не осталось — 'user'.
    """
    cleaned = re.sub(r'[^A-Za-z0-9_]', '', name or '')
    cleaned = cleaned.lstrip('0123456789_')
    if not cleaned:
        cleaned = 'user'
    return cleaned[:max_len]


async def _botfather_ask_msg(text: str):
    """Отправляет text @BotFather от лица аккаунта и ждёт ответ (само сообщение — из него можно взять кнопки)."""
    await engine.send_message(BOTFATHER, text)
    return await engine.wait_message(chat_id=BOTFATHER, user_id=BOTFATHER, timeout=_BOTFATHER_TIMEOUT)


async def _botfather_ask(text: str) -> str:
    """Отправляет text @BotFather от лица аккаунта и ждёт ответ (raw_text)."""
    msg = await _botfather_ask_msg(text)
    return msg.raw_text or ''


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


async def _configure_bot_inline(username: str):
    """
    Включает у бота @username инлайн-режим (/setinline) и инлайн-фидбэк 100% (/setinlinefeedback)
    диалогом с @BotFather. RuntimeError с текстом ответа BotFather — если он ответил неожиданно.
    """
    handle = f"@{username}"

    try:  # сбрасываем возможный "зависший" прошлый диалог BotFather
        await _botfather_ask('/cancel')
    except Exception:
        pass

    # 1. /setinline -> выбрать бота -> плейсхолдер
    await _botfather_ask('/setinline')
    reply = await _botfather_ask(handle)
    if 'invalid' in reply.lower():
        raise RuntimeError(f"BotFather не принял бота {handle} на шаге /setinline: {reply[:300]}")
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


async def _create_bot_via_botfather(first_name: str) -> tuple[str, str]:
    """
    Проводит диалог с @BotFather и создаёт нового бота.
    Возвращает (token, username). Кидает RuntimeError на неожиданном
    ответе BotFather (лимит числа ботов и т.п.) — подхватывается
    стандартным механизмом @on_error и уходит в Logging.py.
    """
    reply = await _botfather_ask('/newbot')
    if 'too many bots' in reply.lower():
        raise RuntimeError(f"BotFather отказал в создании бота: {reply}")

    display_name = f"[FRX] Userbot of {first_name}"
    await _botfather_ask(display_name)

    base = _slug(first_name, max_len=20)      # "frx_" + base + "_userbot" <= 32
    candidate = f"frx_{base}_userbot"

    for _attempt in range(20):
        reply = await _botfather_ask(f"@{candidate}")

        m = _TOKEN_RE.search(reply)
        if m:
            return m.group(0), candidate

        lowered = reply.lower()
        if 'already taken' in lowered or 'invalid' in lowered or "can't" in lowered or 'must end' in lowered:
            base = _slug(first_name, max_len=18)  # "frx" + 2 цифры + "_" + base + "_userbot" <= 32
            candidate = f"frx{random.randint(10, 99)}_{base}_userbot"
            continue

        raise RuntimeError(f"Неожиданный ответ BotFather при выборе username: {reply}")

    raise RuntimeError("Не удалось подобрать свободный username для бота за 20 попыток")


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


async def _bot_is_alive(token: str) -> bool:
    """
    реальная проверка, что сохранённый токен всё ещё
    рабочий — то есть бот не был удалён владельцем через @BotFather и
    токен не был отозван (/revoke). getMe() с невалидным/отозванным
    токеном отвечает 401 Unauthorized — это и означает "бот недоступен".
    """
    if not token:
        return False
    tmp_bot = AiogramBot(token=token)
    try:
        await tmp_bot.get_me()
        return True
    except Exception as e:
        engine.tlog(
            f"{visual.visual('status_warning')} [Startup] стационарный бот недоступен "
            f"по сохранённому токену (похоже, удалён/токен отозван): {e}"
        )
        return False
    finally:
        await tmp_bot.session.close()


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


async def _save_token(token: str, username: str):
    engine.config['aiogram_token'] = token
    try:
        on_disk = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception:
        on_disk = dict(engine.config)
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

    if had_token and await _bot_is_alive(token):
        return  # бот уже настроен и жив — ничего не делаем

    me = await engine.client.get_me()
    first_name = me.first_name or 'user'

    new_token, username = await _create_bot_via_botfather(first_name)
    await _save_token(new_token, username)

    if had_token:
        # Токен был, но бот оказался недоступен — это восстановление, а
        # не первичная настройка: у нового бота ещё не было ни одного
        # диалога, сбрасываем флаги, чтобы /start ушёл заново на всех
        # аккаунтах, и предупреждаем владельца, что бот был пересоздан.
        await _reset_all_dialog_flags()
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
    if supports_inline and await db.get('inline_configured') == username:
        return

    engine.tlog(f"{visual.visual('info')} [Startup] Настраиваю инлайн-режим бота @{username} через BotFather...")
    try:
        await _configure_bot_inline(username)
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


async def _set_avatar_via_botfather(username: str, path: str):
    """Запасной путь: /setuserpic -> @бот -> фото, диалогом с @BotFather от лица аккаунта."""
    handle = f"@{username}"
    try:
        await _botfather_ask('/cancel')
    except Exception:
        pass
    await _botfather_ask('/setuserpic')
    reply = await _botfather_ask(handle)
    if 'photo' not in reply.lower() and 'фото' not in reply.lower():
        raise RuntimeError(f"BotFather не принял бота {handle} на шаге /setuserpic: {reply[:300]}")
    await engine.client.send_file(BOTFATHER, path)
    msg = await engine.wait_message(chat_id=BOTFATHER, user_id=BOTFATHER, timeout=_BOTFATHER_TIMEOUT)
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
        if await db.get('avatar_set') == username:
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
            await _set_avatar_via_botfather(username, path)
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


@decorators.on_account_switch()
async def on_account_switch(number):
    """
    При переключении аккаунта бота НЕ создаём (см. докстринг модуля) —
    только проверяем ЧС и начинаем диалог с уже существующим ботом, если
    этот аккаунт с ним ещё не общался.
    """
    await _ensure_bot_not_blocked()
    await _ensure_dialog_started()
