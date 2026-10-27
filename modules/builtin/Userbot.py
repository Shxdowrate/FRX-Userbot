"""
modules/builtin/Userbot.py — всё, что касается самого юзербота, в одном модуле.

С FRX 1.0.6 сюда слиты прежние встроенные модули Access, Aliases, Autostart, Changelog, Info, InlineStuff, Logging,
ping и Waiters: команды остались прежними (кроме служебных .forms, .closeform, .logs; .dping стал .ping -d),
настройки и данные переносятся сами при первом запуске (см. _migrate_legacy). Все настройки — `.config userbot`.

Команды
    .info                      сведения о юзерботе и системе; конфиденциальное — по кнопке (только владельцу)
    .doctor / .doc             проверка окружения: Python и библиотеки, бот и инлайн, keeper, сайт, ffmpeg, диск
    .ping                      задержка: Telegram (MTProto), правка сообщения, Bot API бота; аптайм
    .ping -d                   то же, по 5 замеров: мин / сред / макс
    .ping <хост[:порт]>        TCP-пинг сайта или сервера с компьютера, где запущен FRX
    .speedtest / .st           скорость интернета (Cloudflare)
    .checkhost / .ch <хост>    доступность из разных стран (check-host.net): [http|ping|tcp|dns] [серверов]
    .ip [ip | домен]           страна, город, провайдер, ASN; без аргумента — внешний IP компьютера с FRX
    .changelog / .cl [версия]  журнал обновлений (инлайн), list — все версии текстом
    .fulllog / .flog           полный лог: архивом в Избранное; here — сюда; tail [N]; clear
    .access / .acs             доступ других людей к юзерботу (меню; grant / revoke / check / list / classes)
    .alias / .unalias          алиасы команд: .alias <команда> [аргументы] <имя>
    .layout [on|off]           команды в неверной раскладке: «юзштп» → .ping
    .autostart [on|off]        автозапуск FRX вместе с системой (Windows, Linux, macOS, Termux)

Только владельцу (гостю не выдаются даже scope'ом, core/access.py PROTECTED_COMMANDS):
    .access, .alias, .unalias, .layout, .autostart, .fulllog.

Фоновая работа
    - ошибки модулей и бота, запуск, смена аккаунта — в ЛС ботом (или в log_chat_id); одна и та же ошибка не чаще
      раза в log_throttle секунд, а сколько раз она повторилась за это время — приходит в следующем сообщении;
    - после обновления FRX — «что нового» из CHANGELOG.md (notify_update);
    - инлайн-формы: сборка зависших ожиданий, лимит кликов (clicks_per_second применяется без перезапуска);
    - wait_timeout — таймаут engine.wait_message() по умолчанию.
"""

import asyncio
import html
import ipaddress
import os
import platform
import random
import re
import shutil
import socket
import statistics
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import aiohttp

import engine
import decorators
import bot
import db
import visual
import time_utils
import accounts

from core.access import AccessError, PROTECTED_COMMANDS, PROTECTED_MODULES, SCOPES, command_key, describe_grant
from core.paths import DATA_DIR, PROJECT_DIR

NAME = "Userbot"
MODULE_NAME = "userbot"
VERSION = "1.0"
AUTHOR = "FRX"
DESCRIPTION = ("Сам юзербот: сведения и проверка (.info, .doctor), пинг и сеть, журнал обновлений, логи в ЛС, "
               "доступ гостей, алиасы, автозапуск, настройки инлайн-форм и ожиданий")
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Userbot.py"

ROOT = Path(PROJECT_DIR)
_STARTED_AT = time.monotonic()


def _esc(x) -> str:
    return html.escape(str(x), quote=False)


async def cfg(key, default=None):
    """Настройка этого модуля (явно по имени модуля — работает и из колбэков форм, и из хэндлеров движка)."""
    try:
        value = await engine.get_config(key, module_name=MODULE_NAME)
    except Exception:
        return default
    return default if value is None else value


async def set_cfg(key, value):
    await engine.edit_config(key, 'edit', value, module_name=MODULE_NAME)


# =========================================================================== #
# Настройки и перенос со старых модулей
# =========================================================================== #

# (ключ, старый модуль, старый ключ, тип, по умолчанию, описание, valid)
CONFIGS = [
    # логи в ЛС
    ('logs_enabled', 'logging', 'enabled', 'bool', True,
     'Логи · дублировать ошибки и важные события в ЛС через стационарного бота', None),
    ('log_chat_id', 'logging', 'log_chat_id', 'int', 0,
     'Логи · куда слать (chat_id); 0 — в ЛС владельцу', None),
    ('notify_startup', 'logging', 'notify_startup', 'bool', True, 'Логи · сообщать о запуске юзербота', None),
    ('notify_userbot_errors', 'logging', 'notify_userbot_errors', 'bool', True,
     'Логи · слать ошибки модулей юзербота', None),
    ('notify_bot_errors', 'logging', 'notify_bot_errors', 'bool', True, 'Логи · слать ошибки стационарного бота', None),
    ('notify_account_switch', 'logging', 'notify_account_switch', 'bool', True,
     'Логи · сообщать о переключении аккаунта', None),
    ('log_throttle', 'logging', 'throttle_seconds', 'int', 30,
     'Логи · одну и ту же ошибку — не чаще раза в N секунд (повторы посчитаются); 0 — без ограничения',
     {'min_value': 0, 'max_value': 86400}),
    ('botlog_burst', 'logging', 'botlog_burst', 'int', 5,
     'Логи · лимитер: сколько сообщений подряд без пауз', {'min_value': 1, 'max_value': 100}),
    ('botlog_interval', 'logging', 'botlog_interval', 'float', 2.0,
     'Логи · лимитер: раз в сколько секунд освобождается ещё одно сообщение', {'min_value': 0, 'max_value': 600}),
    ('botlog_queue', 'logging', 'botlog_queue', 'int', 20,
     'Логи · лимитер: сколько сообщений может ждать в очереди (остальные — в сводку «пропущено N»)',
     {'min_value': 0, 'max_value': 1000}),
    ('botlog_wait', 'logging', 'botlog_wait', 'bool', True,
     'Логи · лимитер: True — лишние ждут в очереди, False — сразу сжигаются', None),
    ('notify_update', 'changelog', 'notify_update', 'bool', True,
     'Обновления · присылать «что нового» при первом запуске новой версии FRX', None),
    # .info
    ('info_media', 'info', 'media', 'str', 'experience_1',
     'Info · медиа над .info: experience_1 — превью FRX, experience_2 — презентация, experience_3 — прежняя анимация, '
     'none — без медиа, или ссылка / путь к своей картинке (.jpg/.png/.webp) или анимации (.mp4/.gif/.webm)', None),
    # доступ и команды
    ('guest_cooldown', 'access', 'guest_cooldown', 'float', 1.0,
     'Доступ · сколько секунд гость ждёт между командами (антифлуд)', {'min_value': 0, 'max_value': 60}),
    ('log_guest_commands', 'access', 'log_guest_commands', 'bool', True,
     'Доступ · писать команды гостей в терминал и во вкладку логов сайта', None),
    ('layout_commands', 'aliases', 'layout_commands', 'bool', True,
     'Команды · понимать команды в русской раскладке: «юзштп» → .ping (только ваши сообщения; аргументы не '
     'переводятся; кириллические команды главнее)', None),
    # инлайн-формы и ожидания
    ('instant_forms', 'inline_stuff', 'instant_forms', 'bool', True,
     'Формы · мгновенные: заранее готовить заглушку и показывать форму сразу (раз в минуту — один фоновый '
     'инлайн-запрос к своему боту)', None),
    ('form_confirm_timeout', 'inline_stuff', 'confirm_timeout', 'int', 10,
     'Формы · сколько секунд ждать подтверждения формы ботом', {'min_value': 3, 'max_value': 120}),
    ('member_cache_ttl', 'inline_stuff', 'member_cache_ttl', 'int', 300,
     'Формы · сколько секунд помнить проверку «участник чата» для for_user=chat_id', {'min_value': 0, 'max_value': 86400}),
    ('clicks_per_second', 'inline_stuff', 'clicks_per_second', 'int', 5,
     'Формы · лимит обработки кликов по формам, кликов/сек (меняется без перезапуска)', {'min_value': 1, 'max_value': 100}),
    ('wait_timeout', 'waiters', 'default_timeout', 'int', 60,
     'Ожидания · таймаут engine.wait_message() по умолчанию, сек (если модуль не указал свой)', {'min_value': 1}),
]

_LEGACY_MODULES = ('logging', 'changelog', 'info', 'access', 'aliases', 'inline_stuff', 'waiters', 'autostart', 'ping')
_LEGACY_DB = {'changelog:last_version': 'last_version'}


async def _migrate_legacy():
    """
    Однократно переносит настройки и данные модулей, слитых в Userbot: значения старых конфигов → в конфиг userbot
    (до create_config, поэтому сохраняются настройки пользователя, а не значения по умолчанию), ключи БД
    (changelog:last_version) → userbot:*. Старые записи удаляются.
    """
    own_key = f"{MODULE_NAME}:__config__"
    own = await db.gget(own_key, {}) or {}
    moved = 0
    for key, old_mod, old_key, dtype, default, desc, valid in CONFIGS:
        old = await db.gget(f"{old_mod}:__config__", None)
        if not isinstance(old, dict) or old_key not in old or key in own:
            continue
        entry = old[old_key]
        if not isinstance(entry, dict) or entry.get('type', dtype) != dtype:
            continue
        own[key] = {'value': entry.get('value', default), 'description': desc, 'type': dtype, 'valid': valid,
                    'default': default, 'options': None}
        moved += 1
    if moved:
        await db.gset(own_key, own)
    for old_key, new_key in _LEGACY_DB.items():
        value = await db.gget(old_key, None)
        if value is not None:
            if await db.get(new_key, None) is None:
                await db.set(new_key, value)
            await db.gdelete(old_key)
    for old_mod in _LEGACY_MODULES:
        if await db.gget(f"{old_mod}:__config__", None) is not None:
            await db.gdelete(f"{old_mod}:__config__")
    if moved:
        engine.tlog(f"{visual.visual('info')} [Userbot] перенесено настроек из прежних модулей: {moved}")


@decorators.on_start()
async def _setup():
    try:
        await _migrate_legacy()
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Userbot] перенос старых настроек не удался: {e}")
    for key, _om, _ok, dtype, default, desc, valid in CONFIGS:
        await engine.create_config(key=key, description=desc, data_type=dtype, default=default, valid=valid)
    engine.set_inline_click_rate(await cfg('clicks_per_second', 5))


# =========================================================================== #
# .info и .doctor
# =========================================================================== #

INFO_PRESETS = {f'experience_{n}': ('experience', f'experience_{n}.mp4') for n in (1, 2, 3)}


def _duration(seconds) -> str:
    seconds = int(seconds)
    d, seconds = divmod(seconds, 86400)
    h, seconds = divmod(seconds, 3600)
    m, s = divmod(seconds, 60)
    parts = [f"{d}д"] if d else []
    if h or d:
        parts.append(f"{h}ч")
    if m or h or d:
        parts.append(f"{m}м")
    if not d:
        parts.append(f"{s}с")
    return " ".join(parts)


def _process_memory_mb():
    """Сколько памяти занимает процесс FRX (МБ) или None, если узнать нечем."""
    try:
        import psutil
        return psutil.Process().memory_info().rss / 1024 / 1024
    except Exception:
        pass
    try:
        with open('/proc/self/statm') as f:                       # Linux, Android
            return int(f.read().split()[1]) * os.sysconf('SC_PAGE_SIZE') / 1024 / 1024
    except Exception:
        pass
    try:
        import resource
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return rss / 1024 / 1024 if sys.platform == 'darwin' else rss / 1024
    except Exception:
        return None


def _mask(value, keep=6):
    if not value:
        return "—"
    value = str(value)
    return "•" * len(value) if len(value) <= keep else value[:keep] + "•" * max(len(value) - keep, 3)


async def _info_media():
    value = str(await cfg('info_media', 'experience_1') or '').strip()
    if not value or value.lower() in ('none', 'off', 'нет', '-', '0'):
        return None
    if value in INFO_PRESETS:
        try:
            return str(engine.asset_path(*INFO_PRESETS[value]))
        except FileNotFoundError:
            return None
    return value


async def _info_public():
    v = visual
    prefix = await engine.get_prefix()
    names = engine.get_module_list()
    builtin = sum(1 for n in names if (engine.get_module_info(n) or {}).get('is_builtin'))
    mem = _process_memory_mb()
    bot_name = getattr(engine, '_bot_username', None)
    lines = [
        v.title('FRX · Info', 'Fractal Runtime Extension'),
        v.field('версия', engine.get_version()),
        v.field('префикс', _esc(prefix)),
        v.field('модулей', f"{len(names)} (встроенных {builtin}, ваших {len(names) - builtin})"),
        v.field('аккаунтов', len(accounts.account_list())),
        v.field('бот', f"@{_esc(bot_name)}" if bot_name else 'не подключён'),
        v.field('аптайм', _duration(time.monotonic() - _STARTED_AT)),
        "",
        v.section('Система'),
        v.field('python', platform.python_version()),
        v.field('ОС', _esc(f"{platform.system()} {platform.release()}")),
        v.field('архитектура', _esc(platform.machine() or '—')),
    ]
    if mem:
        lines.append(v.field('память FRX', f"{mem:.0f} МБ"))
    lines += ["", f"{v.visual('security')} {v.hint('конфиденциальные сведения — по кнопке ниже')}"]
    return "\n".join(lines)


async def _info_confidential():
    v = visual
    me = await engine.client.get_me()
    token = engine.config.get('aiogram_token') or ''
    numbers = accounts.account_list()
    lines = [
        v.title('Конфиденциально'),
        v.field('активный аккаунт', f"+{me.phone}" if me.phone else '—'),
        v.field('id', me.id),
        v.field('имя', _esc(me.first_name or '—'), code=False),
        "",
        v.section(f"Аккаунты ({len(numbers)})"),
    ]
    for number in numbers:
        info = accounts.account_info(number)
        acc = info.get('info', {})
        username = f"@{acc['username']}" if acc.get('username') else '—'
        lines.append(v.item(f"<code>{info['phone']}</code>  {_esc(acc.get('first_name') or '—')}"))
        lines.append(v.item(f"{_esc(username)}, id <code>{acc.get('id', '—')}</code>", 1))
    lines += [
        "", v.section('Стационарный бот'), v.field('токен', _mask(token, keep=10)),
        "", v.section('Сервер'),
        v.field('хост', _esc(platform.node() or '—')),
        v.field('папка', _esc(ROOT.resolve())),
        v.field('api_id', engine.config.get('api_id', '—')),
        v.field('api_hash', _mask(engine.config.get('api_hash'))),
    ]
    return "\n".join(lines)


_INFO_OPEN = [[(visual.btn('Открыть конфиденциальные сведения'), "info_confidential")]]
_INFO_BACK = [[(visual.btn('Скрыть', 'back'), "info_back")]]


@decorators.command('info')
async def info_cmd(event):
    """сведения о юзерботе и системе (конфиденциальные — по кнопке, только владельцу)"""
    text = await _info_public()
    media = await _info_media()
    try:
        try:
            form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=_INFO_OPEN, media=media)
        except Exception as e:
            if not media or 'бот не запущен' in str(e).lower():
                raise
            engine.tlog(f".info: медиа не показано ({e}) — без него")
            form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=_INFO_OPEN)
    except Exception:
        await engine.answer(event, text.rsplit("\n\n", 1)[0] + "\n\n"
                            + visual.hint('инлайн недоступен — стационарный бот не подключён'))
        return

    @form.on_click(data_startswith="info_")
    async def _click(click):
        if click.data == "info_confidential":
            if engine.guest_id() is not None:            # гость с доступом видит .info, но не личные сведения
                await click.answer("только для владельца юзербота", show_alert=True)
                return
            await click.answer()
            await form.edit(await _info_confidential(), buttons=_INFO_BACK)
        else:
            await click.answer()
            await form.edit(await _info_public(), buttons=_INFO_OPEN)

    await engine.answer(event, visual.ok('FRX · Info') + "\n" + visual.hint('сведения — ниже'))


def _pkg_version(pkg):
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return None


def _vtuple3(v):
    out = []
    for part in str(v or '').split('.')[:3]:
        num = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


@decorators.command('doctor', 'doc')
async def doctor_cmd(event):
    """проверка окружения: Python и библиотеки, бот и инлайн-режим, keeper, сайт и публичный адрес, ffmpeg, ssh, место на диске"""
    msg = await engine.answer(event, visual.wait("Проверяю окружение…"))
    ok, warn, bad = visual.visual('status_success'), visual.visual('status_warning'), visual.visual('status_error')
    prefix = _esc(await engine.get_prefix())
    lines = [visual.title("FRX · Doctor", sub="проверка окружения")]
    problems = 0

    def row(state, label, text, fix=None):
        nonlocal problems
        problems += state != ok
        lines.append(f"{state} <b>{label}</b>  {text}")
        if fix and state != ok:
            lines.append(visual.item(f"<i>{fix}</i>", 1))

    # медленные проверки — разом и не в цикле событий
    async def bot_probe():
        b = getattr(engine, '_aiogram_bot', None)
        if b is None:
            return None
        try:
            return await asyncio.wait_for(b.get_me(), 10)
        except Exception as e:
            return e

    def ffmpeg_probe():
        try:
            from core.media_tools import find_ffmpeg
            return find_ffmpeg()
        except Exception:
            return shutil.which('ffmpeg')

    def disk_probe():
        try:
            return shutil.disk_usage(str(DATA_DIR)).free / 1024 ** 3
        except Exception:
            return None

    me_bot, ffmpeg, free_gb, ssh = await asyncio.gather(
        bot_probe(), asyncio.to_thread(ffmpeg_probe), asyncio.to_thread(disk_probe), asyncio.to_thread(shutil.which, 'ssh'))

    py = sys.version_info
    row(ok if py >= (3, 10) else bad, "Python", f"<code>{py.major}.{py.minor}.{py.micro}</code>", "нужен Python 3.10 или новее")
    for pkg, need, why in (('telethon', (1, 36, 0), 'юзербот'), ('aiogram', (3, 25, 0), 'бот; 3.25+ — цветные кнопки'),
                           ('aiohttp', (3, 9, 0), 'сайт и нейросети')):
        ver = _pkg_version(pkg)
        if ver is None:
            row(bad, pkg, "не установлен", "pip install -r requirements.txt")
        elif _vtuple3(ver) < need:
            row(warn, pkg, f"<code>{ver}</code> — {why}", f"обновите: pip install -U \"{pkg}>={'.'.join(map(str, need))}\"")
        else:
            row(ok, pkg, f"<code>{ver}</code>")
    row(ok if ffmpeg else warn, "ffmpeg", "есть" if ffmpeg else "нет",
        "нужен для кружков, голосовых, стикеров и музыки — скачается сам при следующем запуске FRX")
    if me_bot is None:
        row(warn, "бот", "не запущен", f"без бота нет инлайн-форм и BotChat — {prefix}restart, бот создастся сам")
    elif isinstance(me_bot, Exception):
        row(bad, "бот", f"не отвечает: <code>{_esc(str(me_bot)[:120])}</code>", "проверьте связь или aiogram_token в user/config.json")
    else:
        inline = getattr(me_bot, 'supports_inline_queries', None)
        row(ok if inline is not False else bad, "бот", f"@{_esc(me_bot.username or '?')}" + ("" if inline is not False else ", инлайн выключен"),
            f"/setinline в @BotFather или {prefix}restart — Startup включит сам")
    keeper = os.environ.get('FRX_KEEPER') == '1'
    row(ok if keeper else warn, "SystemKeeper", "работает" if keeper else "FRX запущен без keeper'а",
        f"запускайте через start.bat / start.sh — без keeper'а нет {prefix}restart, {prefix}update, публичного адреса и бэкапов")
    web = getattr(engine, 'web', None)
    if web is not None:
        row(ok, "сайт", f"работает, порт <code>{web.port}</code>" if web.running else f"выключен ({prefix}web — включить)")
        if web.tunnel_wanted():
            st = web.tunnel_state()
            mode = web.tunnel_setup().get('mode') or 'auto'
            if st.get('status') == 'up':
                row(ok, "публичный адрес", f"<code>{_esc(st.get('url', ''))}</code> ({_esc(mode)})")
            else:
                row(warn, "публичный адрес", f"{_esc(st.get('status') or 'нет')} ({_esc(mode)})",
                    _esc(str(st.get('error') or f'подождите — keeper пробует сам; {prefix}web tunnel setup — постоянный адрес')[:200]))
    row(ok if ssh else warn, "ssh", "есть" if ssh else "нет",
        "нужен для запасного публичного адреса (localhost.run): Windows — «Дополнительные компоненты» → OpenSSH Client")
    if free_gb is not None:
        row(ok if free_gb >= 1 else (warn if free_gb >= .2 else bad), "место на диске", f"<code>{free_gb:.1f} ГБ</code> свободно",
            f"освободите место: {prefix}fclean, бэкапы — {prefix}sk")
    lines += ["", visual.ok("Всё в порядке") if not problems else visual.warn(f"<b>Замечаний: {problems}</b> — подсказки под строками")]
    await engine.answer(msg if msg is not None else event, "\n".join(lines))


# =========================================================================== #
# Пинг и сеть
# =========================================================================== #

CF = 'https://speed.cloudflare.com'
CH = 'https://check-host.net'
UA = {'User-Agent': 'FRX-Userbot/1.0'}


def _flag(cc):
    cc = (cc or '').upper()
    return ''.join(chr(0x1F1E6 + ord(c) - 65) for c in cc) if len(cc) == 2 and cc.isalpha() else '🌐'


def _ms(v):
    return f"{v:.0f} мс" if v >= 10 else f"{v:.1f} мс"


def _stat(values):
    if not values:
        return '—'
    if len(values) == 1:
        return _ms(values[0])
    return f"{min(values):.0f} / {statistics.mean(values):.0f} / {max(values):.0f} мс"


async def _mtproto_rtt():
    from telethon.tl.functions import PingRequest
    t0 = time.perf_counter()
    await engine.client(PingRequest(ping_id=random.getrandbits(63)))
    return (time.perf_counter() - t0) * 1000


async def _botapi_rtt():
    b = getattr(engine, '_aiogram_bot', None)
    if b is None:
        return None
    t0 = time.perf_counter()
    await asyncio.wait_for(b.get_me(), 10)
    return (time.perf_counter() - t0) * 1000


async def _samples(func, n):
    out = []
    for _ in range(n):
        try:
            v = await func()
        except Exception:
            continue
        if v is None:
            return None
        out.append(v)
    return out


@decorators.command('ping')
async def ping_cmd(event):
    """задержка: Telegram, правка сообщения, бот и аптайм; -d — по 5 замеров (мин / сред / макс); <хост[:порт]> — TCP-пинг хоста"""
    arg = engine.get_args().strip()
    if arg and arg.lower() not in ('-d', 'd', 'detail', 'подробно'):
        await _host_ping(event, arg)
        return
    detail = bool(arg)
    n = 5 if detail else 1
    t0 = time.perf_counter()
    msg = await engine.answer(event, visual.wait('Измеряю задержку…'))
    edits = [(time.perf_counter() - t0) * 1000]
    mt, ba = await asyncio.gather(_samples(_mtproto_rtt, n), _samples(_botapi_rtt, n))
    if detail and msg is not None:
        for i in range(2):
            t1 = time.perf_counter()
            try:
                await msg.edit(visual.wait(f"Измеряю задержку… {i + 2}/3"), parse_mode='html')
                edits.append((time.perf_counter() - t1) * 1000)
            except Exception:
                break
    lines = [visual.title('Пинг', 'мин / сред / макс' if detail else None),
             visual.field('Telegram', _stat(mt) if mt else 'нет ответа'),
             visual.field('сообщение', _stat(edits))]
    if ba is not None:
        lines.append(visual.field('бот', _stat(ba) if ba else 'нет ответа'))
    lines.append(visual.field('аптайм', _duration(time.monotonic() - _STARTED_AT)))
    if detail:
        lines += ["", visual.hints("Telegram — пинг до сервера Telegram (MTProto)",
                                   "сообщение — отправка или правка ответа", "бот — запрос Bot API стационарного бота")]
    await engine.answer(msg if msg is not None else event, "\n".join(lines))


def _split_host(target):
    t = target.strip()
    port = None
    for p, default in (('https://', 443), ('http://', 80)):
        if t.lower().startswith(p):
            t, port = t[len(p):], default
            break
    t = t.split('/', 1)[0]
    if t.startswith('[') and ']' in t:                  # [ipv6]:port
        host, _, rest = t[1:].partition(']')
        return host, int(rest[1:]) if rest.startswith(':') and rest[1:].isdigit() else (port or 443)
    if t.count(':') == 1:
        host, p = t.split(':')
        return host, int(p) if p.isdigit() else (port or 443)
    return t, port or 443


async def _host_ping(event, target):
    host, port = _split_host(target)
    msg = await engine.answer(event, visual.wait(f"Пингую {_esc(host)}:{port}…"))
    out = msg if msg is not None else event
    loop = asyncio.get_running_loop()
    try:
        t0 = time.perf_counter()
        infos = await asyncio.wait_for(loop.getaddrinfo(host, port, type=socket.SOCK_STREAM), 10)
        dns_ms = (time.perf_counter() - t0) * 1000
        ip = infos[0][4][0]
    except Exception as e:
        await engine.answer(out, visual.err(f"Не нашёл адрес {_esc(host)}: {_esc(e)}"))
        return
    times, errors = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=5)
            times.append((time.perf_counter() - t0) * 1000)
            w.close()
        except Exception as e:
            errors.append(type(e).__name__)
        await asyncio.sleep(0.3)
    prefix = _esc(await engine.get_prefix())
    lines = [visual.title('Пинг', sub=f"{_esc(host)}:{port}"), visual.field('адрес', ip), visual.field('DNS', _ms(dns_ms))]
    if times:
        lines += [visual.field('мин / сред / макс', _stat(times)),
                  visual.field('разброс', f"{statistics.pstdev(times):.1f} мс")]
    lines.append(visual.field('ответов', f"{len(times)} из 5"))
    if errors:
        lines.append(visual.warn("не ответил: " + ", ".join(sorted(set(errors)))))
    lines += ["", visual.hints("TCP-подключение с компьютера, где запущен FRX", f"из разных стран — {prefix}ch {_esc(host)}")]
    await engine.answer(out, "\n".join(lines))


async def _cf_ping(s, n=6):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        async with s.get(f"{CF}/__down?bytes=0") as r:
            await r.read()
        out.append((time.perf_counter() - t0) * 1000)
    return out


async def _transfer(stage, seconds, streams, progress, one):
    total, stop = [0], time.perf_counter() + seconds
    t0 = time.perf_counter()
    tasks = [asyncio.ensure_future(one(total, stop)) for _ in range(streams)]
    while not all(t.done() for t in tasks):
        await asyncio.sleep(1)
        await progress(stage, total[0] * 8 / max(0.001, time.perf_counter() - t0) / 1e6)
    for t in tasks:
        if t.exception():
            raise t.exception()
    return total[0] * 8 / (time.perf_counter() - t0) / 1e6


def _downloader(s):
    async def one(total, stop):
        while time.perf_counter() < stop:
            async with s.get(f"{CF}/__down?bytes=25000000") as r:
                async for chunk in r.content.iter_chunked(65536):
                    total[0] += len(chunk)
                    if time.perf_counter() >= stop:
                        break
    return one


def _uploader(s):
    blob = os.urandom(4_000_000)

    async def one(total, stop):
        async def body():                            # кусками — видно, сколько уже ушло
            for i in range(0, len(blob), 262144):
                chunk = blob[i:i + 262144]
                total[0] += len(chunk)
                yield chunk
        while time.perf_counter() < stop:
            async with s.post(f"{CF}/__up", data=body(), headers={'Content-Type': 'application/octet-stream'}) as r:
                await r.read()
    return one


def _mbps(v):
    return f"{v:.0f} Мбит/с" if v >= 100 else f"{v:.1f} Мбит/с"


@decorators.command('speedtest', 'st')
async def speedtest_cmd(event):
    """скорость интернета компьютера с FRX: пинг, загрузка, отдача (через Cloudflare)"""
    state = {'last': 0, 'text': None}
    msg = await engine.answer(event, visual.wait("Измеряю пинг…"))

    async def progress(stage, mbps):
        text = visual.wait(f"Измеряю {stage}… {_mbps(mbps)}")
        if msg is None or time.time() - state['last'] < 2 or text == state['text']:
            return
        state['last'], state['text'] = time.time(), text
        try:
            await msg.edit(text, parse_mode='html')
        except Exception:
            pass

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), headers=UA) as s:
            meta = {}
            try:
                async with s.get(f"{CF}/meta") as r:
                    meta = await r.json(content_type=None)
            except Exception:
                pass
            pings = await _cf_ping(s)
            down = await _transfer('загрузку', 8, 4, progress, _downloader(s))
            up = await _transfer('отдачу', 7, 3, progress, _uploader(s))
    except Exception as e:
        await engine.answer(msg if msg is not None else event, visual.err(f"Не получилось измерить: {_esc(e)}"))
        return
    jitter = statistics.mean(abs(a - b) for a, b in zip(pings, pings[1:])) if len(pings) > 1 else 0
    lines = [visual.title("Скорость интернета", sub="компьютер с FRX"),
             visual.field("⬇ загрузка", _mbps(down)), visual.field("⬆ отдача", _mbps(up)),
             visual.field("пинг", _ms(statistics.median(pings))), visual.field("разброс", _ms(jitter))]
    if meta:
        lines += ["", visual.field("провайдер", _esc(meta.get('asOrganization') or '?'), code=False),
                  visual.field("где", f"{_flag(meta.get('country'))} {_esc(meta.get('city') or '')}", code=False),
                  visual.field("сервер", f"Cloudflare {_esc(meta.get('colo') or '')}")]
    await engine.answer(msg if msg is not None else event, "\n".join(lines))


CH_KINDS = {'http': 'HTTP', 'ping': 'пинг', 'tcp': 'TCP', 'dns': 'DNS'}


def _ch_result(kind, val):
    if val is None:
        return "нет ответа", 0
    try:
        v = val[0]
        if kind == 'http':
            if v and v[0] == 1:
                return f"{v[3]} {_esc(v[2])}, {float(v[1]) * 1000:.0f} мс", 1
            return _esc((v[2] if v and len(v) > 2 else 'ошибка') or 'ошибка'), 0
        if kind == 'ping':
            pings = [p for p in (v or []) if p]
            good = [p[1] * 1000 for p in pings if p[0] == 'OK']
            return (f"{len(good)}/{len(pings)}, {statistics.mean(good):.0f} мс", 1) if good else ("не пингуется", 0)
        if kind == 'tcp':
            return (f"{v['time'] * 1000:.0f} мс, {_esc(v.get('address', ''))}", 1) if 'time' in v else (_esc(v.get('error', 'ошибка')), 0)
        if kind == 'dns':
            addrs = (v.get('A') or []) + (v.get('AAAA') or [])
            return (_esc(", ".join(addrs[:3])) + f", TTL {v.get('TTL', '?')}", 1) if addrs else ("не резолвится", 0)
    except Exception:
        pass
    return "?", 0


@decorators.command('checkhost', 'ch')
async def checkhost_cmd(event):
    """доступность сайта/сервера из разных стран (check-host.net): <хост> [http|ping|tcp|dns] [серверов]"""
    parts = engine.get_args().split()
    prefix = _esc(await engine.get_prefix())
    if not parts:
        await engine.answer(event, visual.err("Что проверить?") + "\n" + visual.hint(f"{prefix}ch example.com [http|ping|tcp|dns] [серверов]"))
        return
    host = parts[0]
    kind = next((p.lower() for p in parts[1:] if p.lower() in CH_KINDS), 'http')
    nodes = next((int(p) for p in parts[1:] if p.isdigit()), 12)
    if kind == 'tcp' and ':' not in host.split('//')[-1]:
        host += ':443'
    if kind in ('ping', 'dns', 'tcp'):
        host = host.split('//')[-1].split('/')[0]
    msg = await engine.answer(event, visual.wait(f"Проверяю {_esc(host)} ({CH_KINDS[kind]})…"))
    out = msg if msg is not None else event
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                         headers={**UA, 'Accept': 'application/json'}) as s:
            async with s.get(f"{CH}/check-{kind}", params={'host': host, 'max_nodes': min(max(nodes, 3), 40)}) as r:
                job = await r.json(content_type=None)
            rid, info = job['request_id'], job['nodes']
            res = {}
            for _ in range(12):
                await asyncio.sleep(2)
                async with s.get(f"{CH}/check-result/{rid}") as r:
                    res = await r.json(content_type=None) or {}
                if res and all(v is not None for v in res.values()):
                    break
    except Exception as e:
        await engine.answer(out, visual.err(f"check-host.net не ответил: {_esc(e)}"))
        return
    ok_n, rows = 0, []
    for node, val in sorted(res.items(), key=lambda kv: (info.get(kv[0]) or ['', ''])[1]):
        meta = info.get(node) or ['??', node, '']
        place = f"{_flag(meta[0])} {_esc(meta[2] if len(meta) > 2 and meta[2] else meta[1])}"
        status, good = _ch_result(kind, val)
        ok_n += good
        rows.append(f"{'✓' if good else '✗'} {place} — {status}")
    lines = [visual.title("Check-host", sub=f"{_esc(host)}\n{CH_KINDS[kind]}"), visual.field("доступен", f"{ok_n} из {len(rows)}"), ""]
    await engine.answer(out, "\n".join(lines + rows + ["", visual.hint(f"подробно: {CH}/check-report/{rid}")]))


@decorators.command('ip', 'айпи')
async def ip_cmd(event):
    """данные IP или домена: страна, город, провайдер, ASN; без аргумента — внешний IP компьютера с FRX"""
    target = engine.get_args().strip()
    ips = []
    if target:
        host = _split_host(target)[0]
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            try:
                infos = await asyncio.wait_for(asyncio.get_running_loop().getaddrinfo(host, None), 10)
                ips = list(dict.fromkeys(i[4][0] for i in infos))
            except Exception as e:
                await engine.answer(event, visual.err(f"Не нашёл адрес {_esc(host)}: {_esc(e)}"))
                return
    msg = await engine.answer(event, visual.wait("Смотрю…"))
    out = msg if msg is not None else event
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=UA) as s:
            async with s.get(f"https://ipwho.is/{ips[0] if ips else ''}", params={'lang': 'ru'}) as r:
                d = await r.json(content_type=None)
    except Exception as e:
        await engine.answer(out, visual.err(f"Сервис не ответил: {_esc(e)}"))
        return
    if not d or not d.get('success', False):
        await engine.answer(out, visual.err(f"Нет данных: {_esc((d or {}).get('message', '?'))}"))
        return
    conn, tz = d.get('connection') or {}, d.get('timezone') or {}
    where = ", ".join(_esc(x) for x in (d.get('country'), d.get('region'), d.get('city')) if x)
    lines = [visual.title("IP", sub=_esc(target) if target else "внешний адрес компьютера с FRX"),
             visual.field("адрес", d.get('ip')),
             visual.field("где", f"{_flag(d.get('country_code'))} {where}", code=False),
             visual.field("провайдер", _esc(conn.get('isp') or conn.get('org') or '?'), code=False),
             visual.field("ASN", f"AS{conn.get('asn')}" if conn.get('asn') else '?'),
             visual.field("пояс", f"{tz.get('id', '?')} ({tz.get('utc', '')})"),
             visual.field("координаты", f"{d.get('latitude')}, {d.get('longitude')}")]
    if len(ips) > 1:
        lines.append(visual.field("ещё адреса", ", ".join(ips[1:6])))
    lines.append(visual.hint("местоположение по базе адресов — примерное, обычно до города провайдера"))
    await engine.answer(out, "\n".join(lines))


# =========================================================================== #
# Журнал обновлений
# =========================================================================== #

CHANGELOG_PATH = ROOT / 'CHANGELOG.md'
README_PATH = ROOT / 'README.md'
_CL_SAFE_LIMIT = 3800         # запас до лимита Telegram (4096)
_CL_MAX_MESSAGES = 6
_CL_CARD_LIMIT = 3500
CL_PAGE_SIZE = 10
_CL_HEADER_RE = re.compile(r'^(#{2,3})\s+(?:Update\s+|v)?(\d+(?:\.\d+)+)\b\s*(.*)$', re.IGNORECASE)
_cl_cache = {'key': None, 'entries': [], 'source': None}


def _vt(version):
    """'1.0.12' -> (1, 0, 12): 1.0.9 < 1.0.12."""
    nums = re.findall(r'\d+', str(version))
    return tuple(int(n) for n in nums) if nums else (0,)


def _cl_parse(text):
    entries, cur, level = [], None, 0
    for line in text.splitlines():
        m = _CL_HEADER_RE.match(line)
        if m:
            if cur:
                entries.append(cur)
            level = len(m.group(1))
            cur = {'version': m.group(2), 'title': m.group(3).strip(' \t—–-'), 'lines': []}
            continue
        h = re.match(r'^(#{1,3})\s', line)
        if h and cur is not None and len(h.group(1)) <= level:
            entries.append(cur)
            cur = None
            continue
        if cur is not None:
            cur['lines'].append(line)
    if cur:
        entries.append(cur)
    return entries


def _cl_load():
    """(записи, источник): CHANGELOG.md, иначе раздел «## Changelog» из README.md. Разбор кэшируется по времени
    изменения файла — .changelog и листание формы не перечитывают журнал каждый раз."""
    for path in (CHANGELOG_PATH, README_PATH):
        try:
            st = path.stat()
        except OSError:
            continue
        key = (str(path), st.st_mtime_ns, st.st_size)
        if _cl_cache['key'] == key:
            return _cl_cache['entries'], _cl_cache['source']
        try:
            text = path.read_text(encoding='utf-8')
        except OSError:
            continue
        if path == README_PATH:
            m = re.search(r'^##\s+Changelog\s*$', text, re.MULTILINE)
            if not m:
                continue
            text = text[m.end():]
        entries = _cl_parse(text)
        if entries:
            _cl_cache.update(key=key, entries=entries, source=path.name)
            return entries, path.name
    return [], None


def _md_inline(text):
    text = html.escape(text, quote=False)
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r'`([^`\n]+)`', stash, text)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)', r'<a href="\2">\1</a>', text)
    return re.sub(r'\x00(\d+)\x00', lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def _md_join_wrapped(lines):
    """Строки-продолжения пункта (с отступом, без своего «- ») — к пункту: в файле текст переносят по ширине."""
    out, in_code = [], False
    for raw in lines:
        st = raw.strip()
        if st.startswith('```'):
            in_code = not in_code
            out.append(raw)
            continue
        if (not in_code and out and st and raw[:1] in (' ', '\t') and not re.match(r'^\s*([-*]\s|#)', raw)
                and re.match(r'^\s*[-*]\s', out[-1])):
            out[-1] = out[-1].rstrip() + ' ' + st
            continue
        out.append(raw)
    return out


def _md_to_html(lines):
    out, in_code, code_buf = [], False, []
    for raw in _md_join_wrapped(lines):
        line = raw.rstrip()
        if line.strip().startswith('```'):
            if in_code:
                out.append("<pre>" + html.escape("\n".join(code_buf), quote=False) + "</pre>")
                code_buf, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue
        if not line.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        m = re.match(r'^(\s*)[-*]\s+(.*)$', line)
        if m:
            out.append(visual.item(_md_inline(m.group(2)), len(m.group(1).replace('\t', '    ')) // 2))
            continue
        h = re.match(r'^#{3,6}\s+(.*)$', line)
        if h:
            head = _md_inline(h.group(1))
            out.append(visual.section(head) if '<' not in head else f"{visual.visual('section')} <b>{head}</b>")
            continue
        out.append(_md_inline(line.strip() if not line.startswith((' ', '\t')) else line))
    if in_code and code_buf:
        out.append("<pre>" + html.escape("\n".join(code_buf), quote=False) + "</pre>")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _cl_render(entry, nested=False):
    version = _esc(entry['version'])
    sub = _esc(entry['title']) if entry['title'] else None
    if nested:
        head = visual.section(f"Версия {version}") + (f"\n{visual.hint(sub)}" if sub else "") + "\n"
    else:
        head = visual.title(f"Версия {version}", sub)
    return f"{head}\n{_md_to_html(entry['lines'])}".rstrip()


def _cl_pack(blocks, first_header="", more_hint=''):
    messages, cur = [], first_header
    for block in blocks:
        for line in block.split("\n"):
            add = ("\n" if cur else "") + line
            if len(cur) + len(add) > _CL_SAFE_LIMIT and cur:
                messages.append(cur)
                cur = line
            else:
                cur += add
        cur += "\n"
    if cur.strip():
        messages.append(cur.rstrip())
    if len(messages) > _CL_MAX_MESSAGES:
        messages = messages[:_CL_MAX_MESSAGES]
        messages[-1] += "\n\n" + visual.hint(f'… продолжение — <code>{_esc(more_hint)}</code>')
    return messages


@bot.on_start()
async def _notify_update():
    """Первый запуск новой версии FRX — описание обновления ботом в лог-чат (как и остальные служебные сообщения)."""
    current = engine.get_version()
    last = await db.get('last_version')
    if last == current:
        return
    if (last and _vt(current) < _vt(last)) or not await cfg('notify_update', True):
        await db.set('last_version', current)          # откат или уведомления выключены — просто запоминаем
        return
    entries, _src = _cl_load()
    if last:
        chosen = [e for e in entries if _vt(last) < _vt(e['version']) <= _vt(current)]
        header = visual.title('FRX обновлён', f"{_esc(last)} → {_esc(current)}")
    else:
        chosen = [e for e in entries if _vt(e['version']) == _vt(current)]
        header = visual.title(f"Что нового в FRX {_esc(current)}")
    chosen.sort(key=lambda e: _vt(e['version']))
    prefix = await engine.get_prefix()
    if chosen:
        messages = _cl_pack([_cl_render(e, nested=True) for e in chosen], header + "\n", f"{prefix}changelog")
    else:
        messages = [header + "\n" + visual.warn(f"Описание версии <code>{_esc(current)}</code> не найдено") + "\n"
                    + visual.hint("нет файла <code>CHANGELOG.md</code> или в нём нет записи об этой версии")]
    try:
        chat_id = await cfg('log_chat_id', 0) or engine._owner_id
        for text in messages:
            await engine.bot.send_message(chat_id, text)
    except Exception as e:
        engine.tlog(f"{visual.visual('status_warning')} [Userbot] описание обновления не отправлено: {e} "
                    f"(повторю при следующем запуске)")
        return
    await db.set('last_version', current)
    engine.tlog(f"{visual.visual('status_success')} [Userbot] описание обновления {current} отправлено")


def _cl_card_parts(entry) -> list:
    """Запись версии частями по границам строк (каждая строка — законченный HTML, теги не рвутся)."""
    parts, cur = [], ""
    for line in _cl_render(entry).split("\n"):
        add = ("\n" if cur else "") + line
        if cur and len(cur) + len(add) > _CL_CARD_LIMIT:
            parts.append(cur.rstrip())
            cur = line
        else:
            cur += add
    if cur.strip() or not parts:
        parts.append(cur.rstrip())
    return parts


def _cl_card(entry, part=0, back_page=0):
    parts = _cl_card_parts(entry)
    part = max(0, min(part, len(parts) - 1))
    text, rows = parts[part], []
    if len(parts) > 1:
        v = entry['version']
        text += "\n\n" + visual.hint(f"часть {part + 1} из {len(parts)}")
        rows.append([("◂", f"cl_part:{part - 1}:{v}") if part > 0 else ("·", "cl_noop"),
                     (f"{part + 1} / {len(parts)}", "cl_noop"),
                     ("▸", f"cl_part:{part + 1}:{v}") if part < len(parts) - 1 else ("·", "cl_noop")])
    rows.append([(visual.btn('Перейти к версии…'), f"cl_pick:{back_page}")])
    return text, rows


def _cl_picker(entries_sorted, page, query=None):
    if query:
        entries_sorted = engine.search_items(
            entries_sorted, query, key=lambda e: [e['version'], e.get('title') or '', ' '.join(e.get('lines') or [])])
    items = [(e['version'] + (f" — {e['title']}" if e['title'] else ""), f"cl_open:{e['version']}") for e in entries_sorted]
    search = [engine.SearchButton("🔍 Найти в журнале", "cl_search", hint="версия или слово из описания")]
    if query:
        search.append((visual.btn('Сбросить', 'close'), "cl_search_reset"))
    rows, page, total = engine.picker_page(items, page, per_page=CL_PAGE_SIZE, page_callback="cl_page:", search=search)
    rows.append([(visual.btn('К последней версии', 'back'), "cl_latest", "primary")])
    sub = (f"поиск «{_esc(query)}»\nнайдено: {len(items)}" if query else "выберите версию")
    sub += f"\nстраница {page + 1} из {total}" if total > 1 or not query else ""
    return visual.title('Журнал обновлений', sub), rows, page


def _cl_bind(form, entries_sorted):
    st = {'page': 0, 'q': None}
    by_version = {e['version']: e for e in entries_sorted}

    async def show_picker(page):
        text, rows, st['page'] = _cl_picker(entries_sorted, page, st['q'])
        await form.edit(text, buttons=rows)

    async def show_entry(entry, part=0):
        text, rows = _cl_card(entry, part, st['page'])
        await form.edit(text, buttons=rows)

    @form.on_search(data="cl_search")
    async def _search(inp):
        st['q'] = (inp.value or '').strip()[:60] or None
        await show_picker(0)

    @form.on_click(data_startswith="cl_")
    async def _click(click):
        await click.answer()
        d = click.data
        if d == "cl_search_reset":
            st['q'] = None
            await show_picker(0)
        elif d.startswith("cl_part:"):
            _, part, version = d.split(":", 2)
            if version in by_version:
                await show_entry(by_version[version], int(part or 0))
        elif d.startswith(("cl_pick:", "cl_page:")):
            await show_picker(int(d.split(":", 1)[1] or 0))
        elif d == "cl_latest":
            await show_entry(entries_sorted[0])
        elif d.startswith("cl_open:"):
            entry = by_version.get(d.split(":", 1)[1])
            if entry is None:
                await form.edit(visual.err('Запись версии не найдена'),
                                buttons=[[(visual.btn('К списку версий', 'back'), f"cl_pick:{st['page']}")]])
            else:
                await show_entry(entry)


@decorators.command('changelog', 'cl')
async def changelog_cmd(event):
    """журнал обновлений: последняя (или указанная) версия, «Перейти к версии…» — все версии с поиском; list — текстом"""
    args = engine.get_args().strip()
    prefix = await engine.get_prefix()
    entries, source = _cl_load()
    if not entries:
        await engine.answer(event, visual.err('Журнал обновлений не найден') + "\n"
                            + visual.hint('нет файла <code>CHANGELOG.md</code> (и раздела Changelog в <code>README.md</code>)'))
        return
    entries_sorted = sorted(entries, key=lambda e: _vt(e['version']), reverse=True)
    if args.lower() in ('list', 'all', 'список'):
        versions = ", ".join(f"<code>{_esc(e['version'])}</code>" for e in entries_sorted)
        await engine.answer(event, visual.title('Журнал обновлений', f"версий: {len(entries_sorted)}") + "\n"
                            + visual.field('источник', _esc(source)) + "\n\n" + versions + "\n\n"
                            + visual.hint(f"запись версии — <code>{_esc(prefix)}changelog {_esc(entries_sorted[-1]['version'])}</code>"))
        return
    entry = entries_sorted[0]
    if args:
        entry = next((e for e in entries_sorted if _vt(e['version']) == _vt(args)), None)
        if entry is None:
            await engine.answer(event, visual.err('Записи о версии нет') + "\n" + visual.field('версия', _esc(args)) + "\n"
                                + visual.hint(f"список версий — <code>{_esc(prefix)}changelog list</code>"))
            return
    try:
        text, rows = _cl_card(entry)
        form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=rows)
    except Exception:
        messages = _cl_pack([_cl_render(entry)], more_hint=f"{prefix}changelog")
        await engine.answer(event, messages[0])
        for text in messages[1:]:
            await engine.reply(event, text)
        return
    _cl_bind(form, entries_sorted)
    await engine.answer(event, visual.ok('Журнал обновлений') + "\n" + visual.hint('журнал — ниже'))


# =========================================================================== #
# Логи в ЛС
# =========================================================================== #

_throttle: dict = {}          # ключ ошибки -> {'last': monotonic, 'skipped': n}


def _now_str():
    return time_utils.time_format(time_utils.get_time(), '%d.%m.%Y %H:%M:%S')


async def _send_log(text: str, throttle_key: str = None):
    """В лог-чат через общий лимитер (engine.botlog_raw). Одинаковые ошибки троттлятся, но не теряются бесследно:
    сколько раз ошибка повторилась, пока молчали, — допишется к следующему сообщению о ней."""
    if not await cfg('logs_enabled', True):
        return
    if throttle_key is not None:
        limit = await cfg('log_throttle', 30)
        st = _throttle.setdefault(throttle_key, {'last': None, 'skipped': 0})
        now = time.monotonic()
        if limit and st['last'] is not None and now - st['last'] < limit:
            st['skipped'] += 1
            return
        if st['skipped']:
            n = st['skipped']
            times = 'раза' if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else 'раз'
            text += "\n" + visual.hint(f"с прошлого сообщения повторилась ещё {n} {times} за {_duration(now - st['last'])} "
                                       f"— повторы в ЛС не дублировались")
        st['last'], st['skipped'] = now, 0
        if len(_throttle) > 500:
            for k in list(_throttle)[:250]:
                _throttle.pop(k, None)
    if engine._aiogram_bot is None:
        engine.tlog(f"{visual.visual('info')} [Userbot] бот не подключён, лог остался только в терминале")
        return
    engine.botlog_raw(text)


def _error_text(title, module_name, func_name, error_text) -> str:
    return "\n".join([
        visual.err(title),
        visual.field('модуль', _esc(module_name)),
        visual.field('функция', _esc(func_name)),
        visual.field('время', _now_str()),
        "",
        f"<pre>{_esc(error_text)}</pre>",
    ])


@decorators.on_error()
async def _log_userbot_error(error_text: str, context: dict):
    if not await cfg('notify_userbot_errors', True):
        return
    m, f = context.get('module', '?'), context.get('function', '?')
    await _send_log(_error_text('Ошибка юзербота', m, f, error_text), throttle_key=f"userbot:{m}:{f}")


@bot.on_error()
async def _log_bot_error(error_text: str, context: dict):
    if not await cfg('notify_bot_errors', True):
        return
    m, f = context.get('module', '?'), context.get('function', '?')
    await _send_log(_error_text('Ошибка стационарного бота', m, f, error_text), throttle_key=f"bot:{m}:{f}")


# @bot.on_start(), а не @decorators.on_start(): бот поднимается ПОСЛЕ core.start() (main.py), а сообщение уходит ботом.
@bot.on_start()
async def _notify_startup():
    if not await cfg('notify_startup', True) or not await cfg('logs_enabled', True):
        return
    names = engine.get_module_list()
    broken = [n for n in names if (engine.get_module_info(n) or {}).get('is_broken')]
    prefix = await engine.get_prefix()
    lines = [
        visual.title('Юзербот запущен'),
        visual.field('версия', engine.get_version()),
        visual.field('модулей', len(names)),
        visual.field('время', _now_str()),
    ]
    if broken:
        lines += ["", visual.warn(f"с ошибками: {', '.join(_esc(n) for n in broken[:8])}"
                                  + (f" и ещё {len(broken) - 8}" if len(broken) > 8 else '')),
                  visual.hint(f"подробности — {_esc(prefix)}help &lt;модуль&gt;")]
    asyncio.ensure_future(_deliver_startup("\n".join(lines)))     # повторы не задерживают bot.on_start других модулей


async def _deliver_startup(text, attempts=4, pause=10):
    """Сразу после запуска сеть и бот бывают ещё не готовы — несколько попыток."""
    status = None
    for i in range(attempts):
        task = engine.botlog_raw(text)
        status = await task if task is not None else 'no_bot'
        if status in ('sent', 'disabled'):
            return
        if i < attempts - 1:
            await asyncio.sleep(pause)
    engine.tlog(f"[Userbot] уведомление о запуске не доставлено ({status}) — подробности строкой «[botlog] …» выше")


@decorators.on_account_switch()
async def _notify_account_switch(number):
    if not await cfg('notify_account_switch', True):
        return
    await _send_log("\n".join([visual.title('Смена аккаунта'), visual.field('аккаунт', _esc(number)),
                               visual.field('время', _now_str())]))


@decorators.command('fulllog', 'flog')
async def fulllog_cmd(event):
    """полный лог юзербота: архивом в Избранное (here — сюда), tail [N] — последние строки, clear — очистить"""
    from core import fulllog
    args = (engine.get_args() or '').split()
    sub = args[0].lower() if args else ''
    if sub == 'clear':
        freed = fulllog.clear()
        await engine.answer(event, visual.ok('Полный лог очищен') + f"  <code>{freed / 1024 / 1024:.1f} МБ</code>")
        return
    if sub == 'tail':
        try:
            n = max(1, min(int(args[1]), 2000)) if len(args) > 1 else 40
        except ValueError:
            n = 40
        text = fulllog.tail(n)
        await engine.answer(event, visual.title('Полный лог', f'последние {n} строк') + "\n"
                            + (f"<pre>{_esc(text)}</pre>" if text else visual.hint('лог пуст')))
        return
    parts = fulllog.files()
    if not parts:
        await engine.answer(event, visual.warn('Полный лог пока пуст'))
        return
    await engine.answer(event, visual.wait('Собираю полный лог…'))
    dst = DATA_DIR / 'logs' / f"frx_log_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    try:
        extra = [DATA_DIR / 'keeper' / 'keeper.log']
        try:
            import frx_systemkeeper as keeper
            if keeper.LOG_FILE:
                extra = [keeper.LOG_FILE]
        except Exception:
            pass
        await asyncio.to_thread(fulllog.pack, dst, extra)
        caption = "\n".join([
            visual.title('Полный лог FRX', engine.get_version()),
            visual.field('частей', len(parts)),
            visual.field('размер', f"{fulllog.total_size() / 1024 / 1024:.1f} МБ (до сжатия)"),
            visual.hint('секреты (токены, api_hash, сессии) замаскированы, но в логе есть id чатов и тексты '
                        'команд — пересылайте только тому, кому доверяете'),
        ])
        await engine.client.send_file(event.chat_id if sub == 'here' else 'me', str(dst), caption=caption,
                                      parse_mode='html', force_document=True)
        await engine.answer(event, visual.ok('Полный лог отправлен' + ('' if sub == 'here' else ' в Избранное')))
    except Exception as e:
        await engine.answer(event, visual.err('Не удалось отправить лог') + f"\n<code>{_esc(e)}</code>")
    finally:
        try:
            dst.unlink()
        except OSError:
            pass


# =========================================================================== #
# Доступ гостей (.access) — логика прав в core/access.py (engine.access)
# =========================================================================== #

ACC_PAGE = 7
DANGER = '⚠️'


def _acc_find_user(query):
    q = str(query).strip().lstrip('@').lower()
    for uid, u in engine.access.users().items():
        if q in (uid, (u.get('username') or '').lower(), (u.get('name') or '').lower()):
            return uid
    return None


async def _acc_resolve_user(event, query=''):
    """(id, имя, username) по id / @username / ответу на сообщение. AccessError — не найден."""
    query = (query or '').strip()
    if not query and event.is_reply:
        reply = await event.get_reply_message()
        if reply and reply.sender_id:
            query = str(reply.sender_id)
    if not query:
        raise AccessError("Ответьте на сообщение человека или укажите его id / @username")
    existing = _acc_find_user(query)
    if existing:
        u = engine.access.get_user(existing)
        return int(existing), u.get('name', ''), u.get('username', '')
    try:
        entity = await engine.client.get_entity(int(query) if query.lstrip('-').isdigit() else query)
    except Exception:
        raise AccessError(f"Пользователь «{query}» не найден (ваш аккаунт должен хотя бы раз его видеть)")
    if getattr(entity, 'bot', False):
        raise AccessError("Это бот — давать доступ ботам нельзя")
    if not hasattr(entity, 'first_name'):
        raise AccessError("Это чат или канал, а не пользователь")
    if entity.id == engine._owner_id:
        raise AccessError("Это вы сами — у владельца и так есть всё")
    name = ' '.join(x for x in (entity.first_name or '', getattr(entity, 'last_name', '') or '') if x) or str(entity.id)
    return entity.id, name, getattr(entity, 'username', '') or ''


def _acc_commands_of(module):
    """[(ключ, команда, описание)] команд модуля, которые вообще можно выдать (без «только владелец»)."""
    info = engine.get_module_info(module) or {}
    out = []
    for c in info.get('commands', []):
        key = f"{module}:{c['command']}"
        if key not in PROTECTED_COMMANDS:
            out.append((key, c['command'], c.get('description', '')))
    return out


def _acc_modules():
    """[(MODULE_NAME, NAME, встроенный, опасность)] модулей, к которым можно выдать доступ."""
    out = []
    for name in engine.get_module_list():
        if name in PROTECTED_MODULES or not _acc_commands_of(name):
            continue
        info = engine.get_module_info(name)
        out.append((name, info.get('name', name), info.get('is_builtin', False), engine.module_danger(name)))
    out.sort(key=lambda x: (not x[2], x[1].lower()))
    return out


def _acc_is_builtin(module):
    info = engine.modules.get(module)
    return bool(info and info.is_builtin)


def _acc_count(uid):
    total = allowed = 0
    for module, _n, builtin, _d in _acc_modules():
        for key, _c, _ in _acc_commands_of(module):
            total += 1
            allowed += engine.access.check(uid, module, key, builtin)[0]
    return allowed, total


def _acc_grant_target(query):
    """(вид, значение) по тексту: класс, all/builtin/loaded, модуль, .команда, модуль:команда."""
    q = (query or '').strip()
    if not q:
        raise AccessError("Укажите, что выдать: класс, модуль или команду")
    classes = engine.access.classes()
    if q in classes:
        return 'class', q
    low = {k.lower(): k for k in classes}
    if q.lower() in low and not q.startswith('.'):
        return 'class', low[q.lower()]
    if q.lower() in SCOPES:
        return 'scope', q.lower()
    if ':' in q:
        mod, cmd = q.split(':', 1)
        module = engine.resolve_module(mod.strip())
        if not module:
            raise AccessError(f"Модуль «{mod}» не найден")
        func = {k.lower(): f for k, f in engine.modules[module].commands.items()}.get(cmd.strip().lower().lstrip('.'))
        if func is None:
            raise AccessError(f"В модуле {module} нет команды «{cmd}»")
        return 'command', command_key(module, func)
    if not q.startswith('.'):
        module = engine.resolve_module(q)
        if module:
            return 'module', module
    entry = engine.command_map.get(q.lstrip('.').lower())
    if entry:
        return 'command', command_key(entry[0], entry[1])
    raise AccessError(f"«{q}» — не класс, не модуль и не команда")


def _acc_danger(kind, value):
    if kind == 'scope':
        return ['открывает в том числе опасные модули'] if value in ('all', 'builtin') else \
            [f"{m[1]}: {', '.join(m[3])}" for m in _acc_modules() if not m[2] and m[3]]
    if kind in ('module', 'command'):
        return engine.module_danger(value.split(':', 1)[0])
    return []


def _acc_grant_line(kind, value, prefix):
    return (f"{DANGER} " if _acc_danger(kind, value) else '') + _esc(describe_grant(kind, value, prefix))


def _acc_class_lines(idn, prefix, level=1):
    c = engine.access.get_class(idn) or {}
    out = []
    if c.get('description'):
        out.append(visual.item(f"<i>{_esc(c['description'])}</i>", level))
    grants = engine.access.grants_of(c)
    out.append(visual.item("даёт: " + ', '.join(_acc_grant_line(k, v, prefix) for k, v in grants), level) if grants
               else visual.item("в классе пока нет выдач — настройте его на сайте", level))
    return out


def _acc_classes_text(prefix):
    lines = [visual.title("Классы доступа")]
    for idn, c in engine.access.classes().items():
        users = sum(1 for u in engine.access.users().values() if u.get('class') == idn)
        lines.append(visual.item(f"<b>{_esc(idn)}</b> — {_esc(c.get('title') or '')}"))
        lines.append(visual.item(f"пользователей: {users}", 1))
        lines += _acc_class_lines(idn, prefix)
    lines += ["", visual.hint(f"создавать и настраивать классы — на сайте: {_esc(prefix)}web → доступ")]
    return "\n".join(lines)


def _acc_list_text(prefix):
    users = engine.access.users()
    lines = [visual.title("Доступ", sub="кто ещё может пользоваться вашим юзерботом")]
    for uid, u in users.items():
        n = len(engine.access.grants_of(u))
        what = ', '.join(x for x in ((f"класс {u['class']}" if u.get('class') else ''), (f"разрешений: {n}" if n else '')) if x)
        lines.append(visual.item(f"<b>{_esc(u.get('name') or uid)}</b>" + (f" @{_esc(u['username'])}" if u.get('username') else '')))
        lines.append(visual.item(f"id <code>{uid}</code>" + (f", {_esc(what)}" if what else ''), 1))
    if not users:
        lines.append(visual.hint("пока ни у кого нет доступа"))
    lines += ["", visual.hint(f"выдать: ответьте на сообщение человека командой {_esc(prefix)}access")]
    return "\n".join(lines)


def _acc_user_text(uid, name, username, prefix):
    u = engine.access.get_user(uid)
    lines = [visual.title(_esc(name or uid), sub=f"@{_esc(username)}\nid {uid}" if username else f"id {uid}")]
    if not u:
        lines += [visual.item("доступа нет — команды этого человека ваш юзербот не выполняет"), "",
                  visual.hint("«Выдать доступ» — класс (готовый набор прав) или разрешение на модуль / команду")]
        return "\n".join(lines)
    lines.append(visual.section("Доступ сейчас"))
    if u.get('class'):
        c = engine.access.get_class(u['class']) or {}
        lines.append(visual.item(f"класс <b>{_esc(u['class'])}</b> — {_esc(c.get('title') or '')}"))
        lines += _acc_class_lines(u['class'], prefix)
    else:
        lines.append(visual.item("класс: нет"))
    grants = engine.access.grants_of(u)
    if grants:
        lines.append(visual.item("разрешения:"))
        lines += [visual.item(_acc_grant_line(k, v, prefix), 1) for k, v in grants]
    allowed, total = _acc_count(uid)
    lines += [visual.field("доступно команд", f"{allowed} из {total}"),
              visual.hint("доступ, алиасы, автозапуск и полный лог — всегда только у владельца")]
    return "\n".join(lines)


async def _acc_open_form(event, uid=None, name='', username=''):
    prefix = await engine.get_prefix()
    st = {'uid': str(uid) if uid is not None else None, 'name': name, 'username': username,
          'mods': [], 'mod': None, 'cmds': [], 'cmd': None, 'page': 0, 'classes': [], 'cls': None, 'grants': [], 'users': []}

    def back(data):
        return [(visual.btn('Назад', 'back'), data)]

    def close_row():
        return [(visual.btn('Закрыть', 'close'), 'close')]

    def who():
        return _esc(st['name'] or st['uid']) + (f" · @{_esc(st['username'])}" if st['username'] else '')

    async def show_list(page=0):
        st['users'] = list(engine.access.users())
        items = []
        for i, x in enumerate(st['users']):
            u = engine.access.get_user(x)
            items.append(((f"{u.get('name') or x}" + (f" · {u['class']}" if u.get('class') else ''), f"u:{i}"),
                          [str(u.get('name') or ''), str(x), str(u.get('username') or ''), str(u.get('class') or '')]))
        if st.get('uq'):
            items = engine.search_items(items, st['uq'], key=lambda it: it[1])
        items = [it[0] for it in items]
        search = None
        if len(st['users']) > ACC_PAGE or st.get('uq'):
            search = [engine.SearchButton("🔍 Найти человека", "acc_uq", hint="имя, @username или id")]
            if st.get('uq'):
                search.append((visual.btn('Сбросить', 'close'), 'uq_reset'))
        rows, _p, _ = engine.picker_page(items, page, ACC_PAGE, 'lp:', search=search) if items or search else ([], 0, 1)
        rows.append([(visual.btn('Классы'), 'classes')])
        rows.append(close_row())
        await form.edit(_acc_list_text(prefix), buttons=rows)

    async def show_user():
        rows = [[(visual.btn('Выдать доступ'), 'give', "success")]]
        if engine.access.get_user(st['uid']):
            rows.append([(visual.btn('Отозвать доступ'), 'revoke', "danger")])
        rows.append(close_row())
        await form.edit(_acc_user_text(st['uid'], st['name'], st['username'], prefix), buttons=rows)

    async def show_give():
        await form.edit("\n".join([
            visual.title("Выдать доступ", sub=who()),
            visual.item(f"<b>класс</b> — готовый набор прав (создаются на сайте: {_esc(prefix)}web → доступ)"),
            visual.item("<b>разрешение</b> — доступ к одному модулю целиком или к одной его команде"),
        ]), buttons=[[(visual.btn('Выдать класс'), 'gcls', "success")], [(visual.btn('Выдать разрешение'), 'gperm:0', "success")],
                     back('user')])

    async def show_classes():
        u = engine.access.get_user(st['uid']) or {}
        st['classes'] = list(engine.access.classes())
        rows = []
        for i, idn in enumerate(st['classes']):
            c = engine.access.get_class(idn)
            rows.append([(f"{idn} — {c.get('title') or ''}{' · сейчас' if u.get('class') == idn else ''}"[:60], f"c:{i}")])
        rows.append(back('give'))
        await form.edit(visual.title("Выдать класс", sub=who()) + "\n" + visual.hint("выберите класс"), buttons=rows)

    async def show_class_confirm():
        idn = st['cls']
        c = engine.access.get_class(idn)
        u = engine.access.get_user(st['uid']) or {}
        lines = [visual.title(f"Класс {_esc(idn)}", sub=who()), visual.item(f"<b>{_esc(c.get('title') or idn)}</b>")]
        lines += _acc_class_lines(idn, prefix, 0)
        danger = []
        for k, v in engine.access.grants_of(c):
            danger += _acc_danger(k, v)
        if danger:
            lines += ["", visual.warn("<b>критически опасный доступ:</b> " + _esc('; '.join(dict.fromkeys(danger))))]
        if u.get('class') == idn:
            lines += ["", visual.hint("этот класс уже выдан")]
            await form.edit("\n".join(lines), buttons=[back('gcls')])
            return
        if u.get('class'):
            lines += ["", visual.hint(f"текущий класс {_esc(u['class'])} будет заменён; личные разрешения останутся")]
        await form.edit("\n".join(lines), buttons=[[(visual.btn(f'Выдать класс {idn}'), 'cok')], back('gcls')])

    async def show_mods(page=0):
        st['mods'] = _acc_modules()
        has_user = bool(engine.access.get_user(st['uid']))
        items = []
        for i, (mname, title, builtin, danger) in enumerate(st['mods']):
            has = has_user and engine.access.module_allowed(st['uid'], mname, builtin)[0]
            items.append(((f"{DANGER} " if danger else '') + title + (' ✓' if has else ''), f"m:{i}"))
        if st.get('mq'):
            titles = {f"m:{i}": [m[0], m[1]] for i, m in enumerate(st['mods'])}
            items = engine.search_items(items, st['mq'], key=lambda it: titles.get(it[1], [it[0]]))
        search = [engine.SearchButton("🔍 Найти модуль", "acc_mq", hint="название модуля")]
        if st.get('mq'):
            search.append((visual.btn('Сбросить', 'close'), 'mq_reset'))
        rows, st['page'], _ = engine.picker_page(items, page, ACC_PAGE, 'gperm:', search=search)
        rows.append(back('give'))
        await form.edit(visual.title("Выдать разрешение", sub=who()) + "\n"
                        + visual.hints("выберите модуль", f"{DANGER} — критически опасный", "✓ — уже доступен целиком"),
                        buttons=rows)

    async def show_mod():
        mname = st['mod']
        info = engine.get_module_info(mname)
        builtin = _acc_is_builtin(mname)
        danger = engine.module_danger(mname)
        has_user = bool(engine.access.get_user(st['uid']))
        whole, why = engine.access.module_allowed(st['uid'], mname, builtin) if has_user else (False, '')
        st['cmds'] = _acc_commands_of(mname)
        lines = [visual.title(_esc(info.get('name', mname)), sub=who())]
        if info.get('description'):
            lines.append(visual.item(f"<i>{_esc(info['description'])}</i>"))
        if danger:
            lines.append(visual.warn("<b>критически опасный модуль:</b> " + _esc('; '.join(danger))))
        rows = []
        if whole:
            lines.append(visual.item(f"доступен целиком <i>({_esc(why)})</i>"))
        else:
            rows.append([(visual.btn('Выдать доступ ко всему модулю'), 'gm', "success")])
        lines += ["", visual.hint("или выберите одну команду:")]
        for i, (key, cmd, _d) in enumerate(st['cmds']):
            ok = has_user and engine.access.check(st['uid'], mname, key, builtin)[0]
            rows.append([(f"{prefix}{cmd}" + (' ✓' if ok else ''), f"k:{i}")])
        rows.append(back(f"gperm:{st['page']}"))
        await form.edit("\n".join(lines), buttons=rows)

    async def show_cmd():
        mname = st['mod']
        key, cmd, desc = st['cmds'][st['cmd']]
        builtin = _acc_is_builtin(mname)
        ok, why = engine.access.check(st['uid'], mname, key, builtin) if engine.access.get_user(st['uid']) else (False, '')
        danger = engine.module_danger(mname)
        lines = [visual.title(_esc(prefix + cmd), sub=who()), visual.field("модуль", _esc(mname))]
        if desc:
            lines.append(visual.item(f"<i>{_esc(desc)}</i>"))
        if danger:
            lines.append(visual.warn("<b>команда критически опасного модуля:</b> " + _esc('; '.join(danger))))
        rows = []
        if ok:
            lines.append(visual.item(f"уже доступна <i>({_esc(why)})</i>"))
        else:
            rows.append([(visual.btn(f'Выдать доступ к {prefix}{cmd}'), 'gk')])
        rows.append(back('mod'))
        await form.edit("\n".join(lines), buttons=rows)

    async def confirm_danger(kind, value, ok_data, back_data):
        await form.edit("\n".join([
            visual.title("Критически опасный доступ", sub=who()),
            visual.item(f"выдаётся: <b>{_esc(describe_grant(kind, value, prefix))}</b>"),
            visual.warn(_esc('; '.join(_acc_danger(kind, value)))),
            "", visual.hint("по сути это доступ ко всему аккаунту и компьютеру, где запущен юзербот. "
                            "Выдавайте только тем, кому доверяете как себе."),
        ]), buttons=[[(visual.btn('Да, выдать'), ok_data, "success")], back(back_data)])

    async def do_grant(kind, value, click):
        await engine.access.grant('user', st['uid'], kind, value, name=st['name'], username=st['username'])
        await click.answer("выдано: " + describe_grant(kind, value, prefix)[:150])

    async def show_revoke():
        u = engine.access.get_user(st['uid'])
        if not u:
            await show_user()
            return
        st['grants'] = engine.access.grants_of(u)
        rows = [[(f"✕ класс {u['class']}", 'rc')]] if u.get('class') else []
        rows += [[(f"✕ {describe_grant(k, v, prefix)}"[:60], f"rg:{i}")] for i, (k, v) in enumerate(st['grants'])]
        rows.append([(visual.btn('Отозвать весь доступ'), 'rall', "danger")])
        rows.append(back('user'))
        await form.edit(visual.title("Отозвать доступ", sub=who()) + "\n" + visual.hint("нажмите на то, что нужно отозвать"),
                        buttons=rows)

    form = await engine.create_inlineform(event.chat_id, visual.wait("Открываю…"), buttons=[[('…', 'noop')]])

    @form.on_search(data='acc_uq')
    async def _search_users(inp):
        st['uq'] = (inp.value or '').strip()[:60] or None
        await show_list()

    @form.on_search(data='acc_mq')
    async def _search_mods(inp):
        st['mq'] = (inp.value or '').strip()[:60] or None
        await show_mods(0)

    simple = {'classes': None, 'list': show_list, 'user': show_user, 'give': show_give, 'gcls': show_classes,
              'mod': show_mod, 'revoke': show_revoke}

    @form.on_click()
    async def _click(click):
        d = click.data
        try:
            if d in ('uq_reset', 'mq_reset'):
                await click.answer()
                st['uq' if d == 'uq_reset' else 'mq'] = None
                await (show_list() if d == 'uq_reset' else show_mods(0))
            elif d == 'close':
                await click.answer()
                await form.delete()
            elif d == 'classes':
                await click.answer()
                await form.edit(_acc_classes_text(prefix), buttons=[back('list')])
            elif d in simple:
                await click.answer()
                await simple[d]()
            elif d.startswith('lp:'):
                await click.answer()
                await show_list(int(d[3:] or 0))
            elif d.startswith('u:'):
                await click.answer()
                st['uid'] = st['users'][int(d[2:])]
                u = engine.access.get_user(st['uid']) or {}
                st['name'], st['username'] = u.get('name', ''), u.get('username', '')
                await show_user()
            elif d.startswith('c:'):
                await click.answer()
                st['cls'] = st['classes'][int(d[2:])]
                await show_class_confirm()
            elif d == 'cok':
                await engine.access.set_user_class(st['uid'], st['cls'], name=st['name'], username=st['username'])
                await click.answer(f"класс {st['cls']} выдан")
                await show_user()
            elif d.startswith('gperm:'):
                await click.answer()
                await show_mods(int(d[6:] or 0))
            elif d.startswith('m:'):
                await click.answer()
                st['mod'] = st['mods'][int(d[2:])][0]
                await show_mod()
            elif d.startswith('k:'):
                await click.answer()
                st['cmd'] = int(d[2:])
                await show_cmd()
            elif d in ('gm', 'gm!'):
                if d == 'gm' and engine.module_danger(st['mod']):
                    await click.answer()
                    await confirm_danger('module', st['mod'], 'gm!', 'mod')
                    return
                await do_grant('module', st['mod'], click)
                await show_mod()
            elif d in ('gk', 'gk!'):
                key = st['cmds'][st['cmd']][0]
                if d == 'gk' and engine.module_danger(st['mod']):
                    await click.answer()
                    await confirm_danger('command', key, 'gk!', f"k:{st['cmd']}")
                    return
                await do_grant('command', key, click)
                await show_cmd()
            elif d == 'rc':
                await engine.access.set_user_class(st['uid'], None)
                await click.answer("класс отозван")
                await show_revoke()
            elif d.startswith('rg:'):
                kind, value = st['grants'][int(d[3:])]
                await engine.access.revoke('user', st['uid'], kind, value)
                await click.answer("отозвано: " + describe_grant(kind, value, prefix)[:150])
                await show_revoke()
            elif d == 'rall':
                if not engine.access.get_user(st['uid']):
                    await click.answer("доступа уже нет")
                    await show_user()
                    return
                await click.answer()
                await form.edit(visual.warn(f"Отозвать весь доступ у <b>{who()}</b>? Класс и все разрешения будут сняты."),
                                buttons=[[(visual.btn('Да, отозвать всё'), 'rallok', "danger")], back('revoke')])
            elif d == 'rallok':
                await engine.access.remove_user(st['uid'])
                await click.answer("доступ отозван")
                await show_user()
            else:
                await click.answer()
        except (AccessError, IndexError, KeyError, TypeError) as e:
            await click.answer(str(e) or 'меню устарело — откройте заново', show_alert=True)

    await (show_user() if st['uid'] is not None else show_list())
    return form


@decorators.command('access', 'acs')
async def access_cmd(event):
    """доступ других людей: ответом на сообщение — меню этого человека; без ответа — список; grant / revoke / check / list / classes"""
    prefix = await engine.get_prefix()
    args = engine.get_args().split()
    sub = args[0].lower() if args else ''
    try:
        if sub in ('grant', 'revoke', 'check'):
            await _acc_sub(event, sub, args[1:], prefix)
            return
        if sub in ('list', 'classes'):
            await engine.answer(event, _acc_list_text(prefix) if sub == 'list' else _acc_classes_text(prefix))
            return
        uid = name = username = None
        if args or event.is_reply:
            uid, name, username = await _acc_resolve_user(event, ' '.join(args))
        try:
            await _acc_open_form(event, uid, name or '', username or '')
            await engine.answer(event, visual.ok("Доступ") + "\n" + visual.hint("меню — ниже"))
        except RuntimeError:
            text = _acc_user_text(str(uid), name, username, prefix) if uid else _acc_list_text(prefix)
            await engine.answer(event, text + "\n\n" + visual.hint(f"без стационарного бота: {_esc(prefix)}access grant / revoke / check"))
    except AccessError as e:
        await engine.answer(event, visual.err(_esc(e)))


async def _acc_sub(event, sub, args, prefix):
    """grant/revoke/check: пользователь — из ответа или первым словом."""
    if event.is_reply:
        uid, name, username = await _acc_resolve_user(event, '')
    else:
        if not args:
            raise AccessError("Укажите пользователя (id или @username) или отправьте команду ответом на его сообщение")
        uid, name, username = await _acc_resolve_user(event, args.pop(0))
    label = _esc(name or uid)
    if sub == 'grant':
        kind, value = _acc_grant_target(' '.join(args))
        if kind == 'class':
            await engine.access.set_user_class(uid, value, name=name, username=username)
            text = f"{label}: выдан класс {_esc(value)}"
            danger = sum((_acc_danger(k, v) for k, v in engine.access.grants_of(engine.access.get_class(value))), [])
        else:
            await engine.access.grant('user', uid, kind, value, name=name, username=username)
            text = f"{label}: выдано — {_esc(describe_grant(kind, value, prefix))}"
            danger = _acc_danger(kind, value)
        await engine.answer(event, visual.ok(text) + (("\n" + visual.warn("критически опасный доступ: "
                                                                          + _esc('; '.join(dict.fromkeys(danger))))) if danger else ''))
    elif sub == 'revoke':
        if not args:
            await engine.access.remove_user(uid)
            await engine.answer(event, visual.ok(f"{label}: весь доступ отозван"))
            return
        what = ' '.join(args)
        if what.lower() == 'class':
            await engine.access.set_user_class(uid, None)
            await engine.answer(event, visual.ok(f"{label}: класс отозван"))
            return
        kind, value = _acc_grant_target(what)
        if kind == 'class':
            if (engine.access.get_user(uid) or {}).get('class') != value:
                raise AccessError(f"Класс {value} этому пользователю не выдан")
            await engine.access.set_user_class(uid, None)
        else:
            await engine.access.revoke('user', uid, kind, value)
        await engine.answer(event, visual.ok(f"{label}: отозвано — {_esc(value if kind == 'class' else describe_grant(kind, value, prefix))}"))
    else:  # check
        if not args:
            raise AccessError(f"Формат: {prefix}access check [пользователь] <команда>")
        word = args[0][len(prefix):] if args[0].startswith(prefix) else args[0]
        _kind, key = _acc_grant_target(word if ':' in word else '.' + word)
        module, cmd = key.split(':', 1)
        ok, why = engine.access.check(uid, module, key, _acc_is_builtin(module))
        await engine.answer(event, (visual.ok if ok else visual.err)(f"{_esc(prefix + cmd)}: {'разрешено' if ok else 'нет доступа'}")
                            + "\n" + visual.field("пользователь", label) + "\n" + visual.hint(_esc(why)))


# =========================================================================== #
# Алиасы и неверная раскладка — логика в core/access.py (engine.aliases)
# =========================================================================== #

def _strip_prefix(word, prefix):
    return word[len(prefix):] if prefix and word.startswith(prefix) else word


def _alias_target(a):
    return a['command'] + (' ' + a['args'] if a.get('args') else '')


@decorators.command('alias')
async def alias_cmd(event):
    """алиас команды: <команда> [аргументы] <новое_имя>; без аргументов — список"""
    prefix = await engine.get_prefix()
    args = engine.get_args().split()
    p = _esc(prefix)
    if not args:
        items = engine.aliases.all()
        lines = [visual.title("Алиасы", f"всего: {len(items)}" if items else None)]
        if not items:
            lines.append(visual.hints("пока нет", f"пример: {p}alias help h", f"или: {p}alias config helper c"))
        for name, a in sorted(items.items()):
            alive = '' if a['command'] in engine.command_map else '  ' + visual.hint('(команды нет)')
            lines.append(visual.item(f"<code>{_esc(prefix + name)}</code> → <code>{_esc(prefix + _alias_target(a))}</code>{alive}"))
        if items:
            lines += ["", visual.hint(f"удалить: {p}unalias имя [имя…]")]
        await engine.answer(event, "\n".join(lines))
        return
    if len(args) < 2:
        await engine.answer(event, visual.err(f"Формат: {p}alias &lt;команда&gt; [аргументы] &lt;новое_имя&gt;")
                            + "\n" + visual.hint(f"например: {p}alias help h"))
        return
    command = _strip_prefix(args[0], prefix).lower()
    name = _strip_prefix(args[-1], prefix)
    rest = ' '.join(args[1:-1])
    commands = set(engine.command_map)
    if command not in commands and name.lower() in commands and len(args) == 2:
        await engine.answer(event, visual.err("Сначала команда, потом новое имя") + "\n"
                            + visual.hint(f"{p}alias {_esc(name.lower())} {_esc(command)}"))
        return
    try:
        await engine.aliases.set(name, command, rest, existing_commands=commands)
    except AccessError as e:
        await engine.answer(event, visual.err(_esc(e)))
        return
    a = engine.aliases.get(name)
    await engine.answer(event, visual.ok("Алиас создан") + "\n" + visual.field(_esc(prefix + name.lower()), _esc(prefix + _alias_target(a))))


@decorators.command('unalias')
async def unalias_cmd(event):
    """удалить алиасы: <имя> [имя…]"""
    prefix = await engine.get_prefix()
    names = [_strip_prefix(n, prefix) for n in engine.get_args().split()]
    if not names:
        await engine.answer(event, visual.err(f"Формат: {_esc(prefix)}unalias имя [имя…]"))
        return
    done, failed = [], []
    for name in names:
        try:
            await engine.aliases.delete(name)
            done.append(_esc(prefix + name.lower()))
        except AccessError as e:
            failed.append(_esc(e))
    lines = [visual.ok(("Удалён алиас " if len(done) == 1 else "Удалены алиасы ") + ", ".join(done))] if done else []
    lines += [visual.err(f) for f in failed]
    await engine.answer(event, "\n".join(lines))


@decorators.command('layout')
async def layout_cmd(event):
    """команды в неверной раскладке (юзштп → .ping): on / off, без аргументов — показать"""
    prefix = await engine.get_prefix()
    arg = engine.get_args().strip().lower()
    if arg in ('on', 'off', 'вкл', 'выкл', '1', '0'):
        await set_cfg('layout_commands', arg in ('on', 'вкл', '1'))
    elif arg:
        await engine.answer(event, visual.err(f"Формат: {_esc(prefix)}layout on | off"))
        return
    on = await cfg('layout_commands', True)
    example = ('ю' if prefix == '.' else _esc(prefix)) + 'зштп'
    await engine.answer(event, visual.ok("Команды в неверной раскладке: " + ("вкл" if on else "выкл")) + "\n"
                        + visual.hints(f"{example} → {_esc(prefix)}ping", f"переключить: {_esc(prefix)}layout {'off' if on else 'on'}"))


# =========================================================================== #
# Автозапуск — те же файлы, что ставит frx_setup.py (--remove-service их убирает)
# =========================================================================== #

SERVICE = "frx"
LAUNCHD_LABEL = "dev.frx.userbot"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_TERMUX = bool(os.environ.get("TERMUX_VERSION")) or "com.termux" in os.environ.get("PREFIX", "")
IS_APK = bool(os.environ.get("FRX_ANDROID"))
TERMUX_BOOT = Path.home() / ".termux" / "boot" / "frx.sh"
AS_PLATFORM_NAMES = {"apk": "Android (FRX.apk)", "termux": "Android (Termux)", "docker": "Docker", "windows": "Windows",
                     "macos": "macOS", "systemd": "Linux (systemd)", "other": sys.platform}


def _as_python() -> str:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return str(venv if venv.exists() else Path(sys.executable))


def _as_run(cmd, timeout=30):
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def _as_platform() -> str:
    if IS_APK:
        return "apk"
    if IS_TERMUX:
        return "termux"
    if Path("/.dockerenv").exists() or os.environ.get("FRX_IN_DOCKER") == "1":
        return "docker"
    if IS_WIN:
        return "windows"
    if IS_MAC:
        return "macos"
    if sys.platform.startswith("linux") and Path("/run/systemd/system").is_dir() and shutil.which("systemctl"):
        return "systemd"
    return "other"


def _as_is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


def _as_win_file() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup" / "FRX.bat"


def _as_launchd_file() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _as_unit_paths():
    return Path("/etc/systemd/system") / f"{SERVICE}.service", Path.home() / ".config/systemd/user" / f"{SERVICE}.service"


def _as_unit(system: bool) -> str:
    owner = ""
    if system:
        try:
            import pwd
            owner = pwd.getpwuid(ROOT.stat().st_uid).pw_name
        except Exception:
            owner = "root"
    lines = [
        "[Unit]", "Description=FRX Telegram userbot", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", *([f"User={owner}"] if owner else []),
        f"WorkingDirectory={ROOT.resolve()}", f"ExecStart={_as_python()} -u frx_systemkeeper.py", "Environment=PYTHONUNBUFFERED=1",
        "Restart=on-failure", "RestartSec=15", "RestartPreventExitStatus=78", "KillMode=mixed", "TimeoutStopSec=45", "",
        "[Install]", f"WantedBy={'multi-user.target' if system else 'default.target'}",
    ]
    return "\n".join(lines) + "\n"


def autostart_status() -> dict:
    """{'platform', 'supported', 'enabled', 'where', 'note'}"""
    p = _as_platform()
    st = {"platform": p, "supported": True, "enabled": False, "where": "", "note": ""}
    if p == "apk":
        st.update(supported=False, enabled=True, note="приложение FRX само работает службой Android и запускается "
                                                     "после включения телефона — отдельно включать ничего не нужно")
    elif p == "docker":
        st.update(supported=False, note="в Docker автозапуск задаёт docker compose (restart: on-failure)")
    elif p == "other":
        st.update(supported=False, note="для этой системы автозапуск не настраивается; запуск в фоне: tmux new -s frx ./start.sh")
    elif p == "windows":
        f = _as_win_file()
        st.update(enabled=f.exists(), where=str(f))
    elif p == "macos":
        f = _as_launchd_file()
        st.update(enabled=f.exists(), where=str(f))
    elif p == "termux":
        st.update(enabled=TERMUX_BOOT.exists(), where=str(TERMUX_BOOT),
                  note="нужно приложение Termux:Boot (откройте его один раз) и выключенная экономия батареи для Termux")
    elif p == "systemd":
        system_unit, user_unit = _as_unit_paths()
        if system_unit.exists():
            st.update(enabled=_as_run(["systemctl", "is-enabled", SERVICE])[0] == 0, where=str(system_unit))
        elif user_unit.exists():
            st.update(enabled=_as_run(["systemctl", "--user", "is-enabled", SERVICE])[0] == 0, where=str(user_unit))
    return st


def autostart_enable() -> str:
    p = _as_platform()
    root = ROOT.resolve()
    if p == "windows":
        f = _as_win_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f'@echo off\r\ncd /d "{root}"\r\nstart "FRX" /min "{_as_python()}" -u frx_systemkeeper.py\r\n', encoding="utf-8")
        return f"FRX запустится сам при входе в Windows (свёрнутым окном): {f}"
    if p == "macos":
        logs = root / "user" / "data" / "keeper"
        logs.mkdir(parents=True, exist_ok=True)
        f = _as_launchd_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0"><dict>
              <key>Label</key><string>{LAUNCHD_LABEL}</string>
              <key>ProgramArguments</key><array><string>{_as_python()}</string><string>-u</string><string>frx_systemkeeper.py</string></array>
              <key>WorkingDirectory</key><string>{root}</string>
              <key>RunAtLoad</key><true/>
              <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
              <key>StandardOutPath</key><string>{logs / 'console.log'}</string>
              <key>StandardErrorPath</key><string>{logs / 'console.log'}</string>
            </dict></plist>
            """), encoding="utf-8")
        # launchctl load не вызываем: RunAtLoad запустил бы вторую копию прямо сейчас. Агент подхватится при входе.
        return f"FRX запустится сам при следующем входе в macOS: {f}"
    if p == "termux":
        log = root / "user" / "data" / "termux_boot.log"
        TERMUX_BOOT.parent.mkdir(parents=True, exist_ok=True)
        TERMUX_BOOT.write_text(textwrap.dedent(f"""\
            #!/data/data/com.termux/files/usr/bin/sh
            # FRX — автозапуск после включения телефона. Нужно приложение Termux:Boot: откройте его один раз.
            termux-wake-lock
            export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
            export PIP_FIND_LINKS="https://github.com/Shxdowrate/FRX-Userbot-APK/releases/expanded_assets/wheels" PIP_PREFER_BINARY=1
            cd "{root}" || exit 1
            while true; do
              .venv/bin/python -u frx_systemkeeper.py >> "{log}" 2>&1
              code=$?
              if [ "$code" -eq 0 ] || [ "$code" -eq 78 ]; then break; fi
              sleep 15
            done
            termux-wake-unlock
            """), encoding="utf-8")
        TERMUX_BOOT.chmod(0o755)
        return (f"FRX запустится сам после включения телефона: {TERMUX_BOOT}\n"
                "Поставьте приложение Termux:Boot (F-Droid / GitHub), откройте его один раз и выключите экономию "
                "батареи для Termux и Termux:Boot.")
    if p == "systemd":
        system_unit, user_unit = _as_unit_paths()
        if _as_is_root():
            system_unit.write_text(_as_unit(True), encoding="utf-8")
            _as_run(["systemctl", "daemon-reload"])
            code, out = _as_run(["systemctl", "enable", SERVICE])
            if code != 0:
                raise RuntimeError(f"systemctl enable: {out.strip()}")
            return (f"Служба {SERVICE} включена: FRX запустится сам при старте сервера и перезапустится при падении.\n"
                    f"Файл: {system_unit}\nЛоги: journalctl -u {SERVICE} -f")
        user_unit.parent.mkdir(parents=True, exist_ok=True)
        user_unit.write_text(_as_unit(False), encoding="utf-8")
        _as_run(["systemctl", "--user", "daemon-reload"])
        code, out = _as_run(["systemctl", "--user", "enable", SERVICE])
        if code != 0:
            raise RuntimeError(f"systemctl --user enable: {out.strip()}")
        user = os.environ.get("USER") or ""
        linger_ok = bool(shutil.which("loginctl")) and _as_run(["loginctl", "enable-linger", user])[0] == 0
        msg = (f"Служба пользователя {SERVICE} включена: FRX запустится сам при старте системы.\n"
               f"Файл: {user_unit}\nЛоги: journalctl --user -u {SERVICE} -f")
        if not linger_ok:
            msg += ("\nЧтобы служба стартовала без входа в систему (после перезагрузки сервера), выполните один раз: "
                    f"sudo loginctl enable-linger {user}")
        return msg
    raise RuntimeError(autostart_status()["note"] or "автозапуск на этой системе не поддерживается")


def autostart_disable() -> str:
    p = _as_platform()
    if p == "windows":
        _as_win_file().unlink(missing_ok=True)
        return "Автозапуск при входе в Windows убран"
    if p == "macos":
        f = _as_launchd_file()
        if f.exists():
            _as_run(["launchctl", "unload", "-w", f])
            f.unlink(missing_ok=True)
        return "LaunchAgent убран: FRX больше не запускается при входе в macOS"
    if p == "termux":
        TERMUX_BOOT.unlink(missing_ok=True)
        return "Автозапуск Termux:Boot убран"
    if p == "systemd":
        system_unit, user_unit = _as_unit_paths()
        done = []
        if system_unit.exists():
            if not _as_is_root():
                raise RuntimeError(f"служба системная — уберите её с правами root: sudo systemctl disable {SERVICE} "
                                   f"&& sudo rm {system_unit}")
            _as_run(["systemctl", "disable", SERVICE])       # без --now: работающий юзербот не останавливаем
            system_unit.unlink(missing_ok=True)
            _as_run(["systemctl", "daemon-reload"])
            done.append(str(system_unit))
        if user_unit.exists():
            _as_run(["systemctl", "--user", "disable", SERVICE])
            user_unit.unlink(missing_ok=True)
            _as_run(["systemctl", "--user", "daemon-reload"])
            done.append(str(user_unit))
        return "Служба убрана" + (f": {', '.join(done)}" if done else " (её и не было)") + ". Юзербот продолжает работать до остановки."
    raise RuntimeError(autostart_status()["note"] or "автозапуск на этой системе не поддерживается")


def _as_text(st: dict, result: str = "", error: str = "") -> str:
    lines = [visual.title("Автозапуск"), visual.field("система", AS_PLATFORM_NAMES.get(st["platform"], st["platform"]))]
    if st["supported"] or st["enabled"]:
        lines.append(visual.field("состояние", "включён" if st["enabled"] else "выключен"))
    if st["where"] and st["enabled"]:
        lines.append(visual.field("файл", _esc(st["where"])))
    if result:
        lines += ["", visual.ok("Готово"), _esc(result)]
    if error:
        lines += ["", visual.err("Не получилось"), f"<code>{_esc(error)}</code>"]
    if st["note"] and not result and not error:
        lines += ["", visual.hint(_esc(st["note"]))]
    return "\n".join(lines)


def _as_buttons(st: dict):
    if not st["supported"]:
        return None
    if st["enabled"]:
        return [[(visual.btn("Выключить", "off"), "autostart_off", "danger")]]
    return [[(visual.btn("Включить", "on"), "autostart_on", "success")]]


async def _as_apply(action: str):
    st = await asyncio.to_thread(autostart_status)
    try:
        result = await asyncio.to_thread(autostart_enable if action == "on" else autostart_disable)
        return await asyncio.to_thread(autostart_status), result, ""
    except Exception as e:
        return st, "", str(e)


@decorators.command("autostart", "autorun")
async def autostart_cmd(event):
    """[on|off] — автозапуск FRX вместе с системой (Windows, Linux, macOS, Termux)"""
    arg = engine.get_args().strip().lower()
    if arg in ("on", "вкл", "1", "yes", "да", "enable", "off", "выкл", "0", "no", "нет", "disable"):
        on = arg in ("on", "вкл", "1", "yes", "да", "enable")
        await engine.answer(event, _as_text(*await _as_apply("on" if on else "off")))
        return
    st = await asyncio.to_thread(autostart_status)
    buttons = _as_buttons(st)
    if not buttons:
        await engine.answer(event, _as_text(st))
        return
    try:
        form = await engine.create_inlineform(event.chat_id, _as_text(st), buttons=buttons)
    except Exception:
        p = _esc(await engine.get_prefix())
        await engine.answer(event, _as_text(st) + "\n\n" + visual.hint(f"{p}autostart on / {p}autostart off"))
        return

    @form.on_click(data_startswith="autostart_")
    async def _(click):
        await click.answer()
        st2, result, error = await _as_apply("on" if click.data == "autostart_on" else "off")
        await form.edit(_as_text(st2, result, error), buttons=_as_buttons(st2))

    await engine.answer(event, visual.ok("Автозапуск") + "\n" + visual.hint("переключатель — ниже"))


# =========================================================================== #
# Инлайн-формы: защитные сетки и лимит кликов
# =========================================================================== #

@decorators.cycle(interval='1m')
async def _forms_gc():
    """
    Ожидания подтверждения форм, которые почему-то не завершились штатно (в норме create_inlineform() чистит их сам),
    снимаются с ошибкой, чтобы не утекала память. Заодно применяется clicks_per_second — меняется без перезапуска.
    """
    grace = int(await cfg('form_confirm_timeout', 10)) + 30
    now = time.monotonic()
    for form_id, pending in list(engine._pending_forms.items()):
        if now - pending['created_at'] < grace:
            continue
        fut = pending['future']
        if not fut.done():
            fut.set_exception(TimeoutError("Форма зависла в ожидании подтверждения и была снята сборщиком"))
        engine._pending_forms.pop(form_id, None)
    engine.set_inline_click_rate(await cfg('clicks_per_second', 5))


@decorators.cycle(interval='10m')
async def _inputs_gc():
    """Ожидания ввода (InputButton) старше часа и устаревшие служебные записи о вводе и эхе бота."""
    now = time.monotonic()
    for user_id, pending in list(engine._pending_inputs.items()):
        if now - pending['created_at'] >= 3600:
            engine._pending_inputs.pop(user_id, None)
    for store, field in ((engine._pending_input_deletes, 'created_at'), (engine._recent_bot_echoes, 'seen_at')):
        for chat_id, items in list(store.items()):
            items[:] = [item for item in items if now - item[field] <= 15]
            if not items:
                store.pop(chat_id, None)
