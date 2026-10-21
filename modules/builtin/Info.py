"""
modules/builtin/Info.py · 2.0

Команда .info — сводная информация о юзерботе FRX и системе, на которой он
запущен.

Часть данных (номера телефонов авторизованных аккаунтов, токен
стационарного бота, api_id/api_hash, хост сервера и т.д.) — конфиденциальна.
В основном сообщении она не показывается вообще. Реально эти сведения
открываются отдельным экраном той же инлайн-формы, по нажатию кнопки
"Открыть конфиденциальные сведения" — а инлайн-формы по умолчанию
(for_user=None) принимают клики только от владельца юзербота; если .info запустил
человек с доступом (.access), эту кнопку он нажать может, но сведения откроются только владельцу.

Оформление — visual.title/section/field; над информацией — медиа (настройка media).
Конфиг `media`:
    experience_1        — короткое превью FRX: логотип, канал, GitHub (assets/experience/experience_1.mp4, 5 с, без звука);
    experience_2        — анимация-презентация FRX (assets/experience/experience_2.mp4, 1:40, со звуком);
    experience_3        — прежняя анимация FRX (assets/experience/experience_3.mp4);
    none                — без медиа;
    https://… или путь  — любая своя картинка (.jpg/.png/.webp) или анимация (.mp4/.gif/.webm).
"""

import html
import os
import platform
import time as _time

import engine
import decorators
import visual
import accounts

NAME = "Info"
MODULE_NAME = "info"
VERSION = "2.1"
AUTHOR = "FRX"
DESCRIPTION = "Полная информация о юзерботе и системе (.info) с анимацией; конфиденциальные сведения — по инлайн-кнопке; проверка окружения (.doctor)"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Info.py"

_STARTED_AT = _time.monotonic()
PRESETS = {f'experience_{n}': ('experience', f'experience_{n}.mp4') for n in (1, 2, 3)}     # встроенные медиа (assets/…)


@decorators.on_start()
async def _configs():
    await engine.create_config(
        key='media',
        description='Медиа над .info: experience_1 — превью FRX, experience_2 — презентация, experience_3 — прежняя анимация, none — без медиа, '
                    'или прямая ссылка / путь к своей картинке (.jpg/.png/.webp) или анимации (.mp4/.gif/.webm)',
        data_type='str', default='experience_1',
    )


def _uptime_str():
    seconds = int(_time.monotonic() - _STARTED_AT)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}д")
    if hours or days:
        parts.append(f"{hours}ч")
    if minutes or hours or days:
        parts.append(f"{minutes}м")
    parts.append(f"{seconds}с")
    return " ".join(parts)


def _mask(value, keep=6):
    """Оставляет первые keep символов, остальное — точками. Для секретов."""
    if not value:
        return "—"
    value = str(value)
    if len(value) <= keep:
        return "•" * len(value)
    return value[:keep] + "•" * max(len(value) - keep, 3)


async def _media():
    """Что показать над .info: путь к файлу / ссылка / None."""
    try:
        value = str(await engine.get_config('media') or '').strip()
    except Exception:
        value = 'experience_1'
    if not value or value.lower() in ('none', 'off', 'нет', '-', '0'):
        return None
    if value in PRESETS:
        try:
            return str(engine.asset_path(*PRESETS[value]))
        except FileNotFoundError:
            return None
    return value


async def _public_text():
    v = visual
    prefix = await engine.get_prefix()
    return "\n".join([
        v.title('FRX · Info', 'Fractal Runtime Extension'),
        v.field('версия', engine.get_version()),
        v.field('префикс', html.escape(prefix)),
        v.field('модулей', len(engine.get_module_list())),
        v.field('аккаунтов', len(accounts.account_list())),
        v.field('аптайм', _uptime_str()),
        "",
        v.section('Система'),
        v.field('python', platform.python_version()),
        v.field('ОС', html.escape(f"{platform.system()} {platform.release()}")),
        v.field('архитектура', html.escape(platform.machine() or '—')),
        "",
        f"{v.visual('security')} {v.hint('конфиденциальные сведения — по кнопке ниже')}",
    ])


async def _confidential_text():
    v = visual
    me = await engine.client.get_me()
    token = engine.config.get('aiogram_token') or ''
    lines = [
        v.title('Конфиденциально'),
        v.field('активный аккаунт', f"+{me.phone}" if me.phone else '—'),
        v.field('id', me.id),
        v.field('имя', html.escape(me.first_name or '—'), code=False),
        "",
        v.section(f"Аккаунты ({len(accounts.account_list())})"),
    ]
    for number in accounts.account_list():
        info = accounts.account_info(number)
        acc = info.get('info', {})
        name = html.escape(acc.get('first_name') or '—')
        username = f"@{acc['username']}" if acc.get('username') else '—'
        lines.append(v.item(f"<code>{info['phone']}</code>  {name}"))
        lines.append(v.item(f"{html.escape(username)}, id <code>{acc.get('id', '—')}</code>", 1))
    lines += [
        "",
        v.section('Стационарный бот'),
        v.field('токен', _mask(token, keep=10)),
        "",
        v.section('Сервер'),
        v.field('хост', html.escape(platform.node() or '—')),
        v.field('папка', html.escape(os.getcwd())),
        v.field('api_id', engine.config.get('api_id', '—')),
        v.field('api_hash', _mask(engine.config.get('api_hash'))),
    ]
    return "\n".join(lines)


OPEN = [[(visual.btn('Открыть конфиденциальные сведения'), "info_confidential")]]
BACK = [[(visual.btn('Скрыть', 'back'), "info_back")]]


@decorators.command('info')
async def info_cmd(event):
    """полная информация о юзерботе и системе (конфиденциальные сведения — по инлайн-кнопке)"""
    try:
        text = await _public_text()
        media = await _media()
        try:
            try:
                form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=OPEN, media=media)
            except Exception as e:
                if not media or 'бот не запущен' in str(e).lower():
                    raise
                engine.tlog(f".info: медиа не показано ({e}) — без него")
                form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=OPEN)
        except Exception:
            # Стационарный бот не запущен/не настроен — инлайн недоступен, отдаём публичную часть обычным сообщением.
            await engine.answer(event, text + "\n\n" + visual.warn(visual.hint('инлайн-режим недоступен — стационарный бот не подключён')))
            return

        @form.on_click(data="info_confidential")
        async def _open_confidential(click):
            if engine.guest_id() is not None:        # человек с доступом (.access) видит .info, но не личные сведения
                await click.answer("только для владельца юзербота", show_alert=True)
                return
            await click.answer()
            await form.edit(await _confidential_text(), buttons=BACK)

        @form.on_click(data="info_back")
        async def _back(click):
            await click.answer()
            await form.edit(await _public_text(), buttons=OPEN)

        await engine.answer(event, visual.ok('FRX · Info') + "\n" + visual.hint('сведения — ниже'))

    except Exception as e:
        await engine.answer(event, visual.err('Ошибка') + f"\n<code>{html.escape(str(e))}</code>")
        engine.tlog(f"Ошибка в .info: {e}")


# --------------------------------------------------------------------------- #
# .doctor — проверка окружения одной командой
# --------------------------------------------------------------------------- #

def _ver(pkg):
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:
        return None


def _vtuple(v):
    out = []
    for part in str(v or '').split('.')[:3]:
        num = ''.join(ch for ch in part if ch.isdigit())
        out.append(int(num) if num else 0)
    return tuple(out + [0] * (3 - len(out)))


@decorators.command('doctor', 'doc')
async def doctor_cmd(event):
    """проверка окружения: Python и библиотеки, бот и инлайн-режим, keeper, сайт и публичный адрес, ffmpeg, ssh, место на диске"""
    import shutil
    import sys
    await engine.answer(event, visual.wait("Проверяю окружение…"))
    ok, warn, bad = visual.visual('status_success'), visual.visual('status_warning'), visual.visual('status_error')
    lines = [visual.title("FRX · Doctor", sub="проверка окружения")]
    problems = 0

    def row(state, label, text, fix=None):
        nonlocal problems
        if state != ok:
            problems += 1
        lines.append(f"{state} <b>{label}</b>  {text}")
        if fix and state != ok:
            lines.append(visual.item(f"<i>{fix}</i>", 1))

    py = sys.version_info
    row(ok if py >= (3, 10) else bad, "Python", f"<code>{py.major}.{py.minor}.{py.micro}</code>",
        "нужен Python 3.10 или новее")
    for pkg, need, why in (('telethon', (1, 36, 0), 'юзербот'), ('aiogram', (3, 25, 0), 'бот; 3.25+ — цветные кнопки'),
                           ('aiohttp', (3, 9, 0), 'сайт и нейросети')):
        v = _ver(pkg)
        if v is None:
            row(bad, pkg, "не установлен", f"pip install -r requirements.txt")
        elif _vtuple(v) < need:
            row(warn, pkg, f"<code>{v}</code> — {why}", f"обновите: pip install -U \"{pkg}>={'.'.join(map(str, need))}\"")
        else:
            row(ok, pkg, f"<code>{v}</code>")

    try:
        from core.media_tools import find_ffmpeg
        ffmpeg = find_ffmpeg()
    except Exception:
        ffmpeg = shutil.which('ffmpeg')
    row(ok if ffmpeg else warn, "ffmpeg", "есть" if ffmpeg else "нет",
        "нужен для кружков, голосовых, стикеров и музыки — скачается сам при следующем запуске FRX (или при первом вызове)")

    bot = getattr(engine, '_aiogram_bot', None)
    if bot is None:
        row(warn, "бот", "не запущен", "без бота нет инлайн-форм, BotChat и входа на сайт в один клик — Startup создаст его сам при перезапуске")
    else:
        try:
            me = await bot.get_me()
            inline = getattr(me, 'supports_inline_queries', None)
            row(ok if inline is not False else bad, "бот", f"@{html.escape(me.username or '?')}" + ("" if inline is not False else ", инлайн-режим выключен"),
                "включите инлайн-режим: /setinline в @BotFather (или .restart — Startup включит сам)")
        except Exception as e:
            row(bad, "бот", f"не отвечает: <code>{html.escape(str(e)[:120])}</code>", "проверьте aiogram_token в user/config.json")

    keeper = os.environ.get('FRX_KEEPER') == '1'
    row(ok if keeper else warn, "SystemKeeper", "работает" if keeper else "FRX запущен без keeper'а",
        "запускайте через start.bat / start.sh — без keeper'а нет .restart, .update, публичного адреса и бэкапов")

    web = getattr(engine, 'web', None)
    if web is not None:
        row(ok, "сайт", f"работает, порт <code>{web.port}</code>" if web.running else "выключен (.web — включить)")
        if web.tunnel_wanted():
            st = web.tunnel_state()
            mode = web.tunnel_setup().get('mode') or 'auto'
            if st.get('status') == 'up':
                row(ok, "публичный адрес", f"<code>{html.escape(st.get('url', ''))}</code> ({html.escape(mode)})")
            else:
                row(warn, "публичный адрес", f"{html.escape(str(st.get('status') or 'нет'))} ({html.escape(mode)})",
                    html.escape(str(st.get('error') or 'подождите — keeper пробует сам; .web tunnel setup — постоянный адрес')[:200]))
    row(ok if shutil.which('ssh') else warn, "ssh", "есть" if shutil.which('ssh') else "нет",
        "нужен для запасного публичного адреса (localhost.run): Windows — «Дополнительные компоненты» → OpenSSH Client")

    try:
        from core.paths import DATA_DIR
        free = shutil.disk_usage(str(DATA_DIR)).free
        gb = free / 1024 ** 3
        row(ok if gb >= 1 else (warn if gb >= .2 else bad), "место на диске", f"<code>{gb:.1f} ГБ</code> свободно",
            "освободите место: бэкапы — .sk, файлы — сайт → files")
    except Exception:
        pass

    lines.append("")
    lines.append(visual.ok("Всё в порядке") if not problems else visual.warn(f"<b>Замечаний: {problems}</b> — подсказки под строками"))
    await engine.answer(event, "\n".join(lines))
