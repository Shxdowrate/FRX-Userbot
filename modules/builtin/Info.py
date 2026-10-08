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
    experience_1        — встроенная анимация-презентация FRX (assets/experience/experience_1.mp4);
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
VERSION = "2.0"
AUTHOR = "FRX"
DESCRIPTION = "Полная информация о юзерботе и системе (.info) с анимацией; конфиденциальные сведения — по инлайн-кнопке"

_STARTED_AT = _time.monotonic()
PRESETS = {'experience_1': ('experience', 'experience_1.mp4')}     # встроенные медиа (assets/…)


@decorators.on_start()
async def _configs():
    await engine.create_config(
        key='media',
        description='Медиа над .info: experience_1 — встроенная анимация FRX, none — без медиа, '
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
        v.section(f"Аккаунты · {len(accounts.account_list())}"),
    ]
    for number in accounts.account_list():
        info = accounts.account_info(number)
        acc = info.get('info', {})
        name = html.escape(acc.get('first_name') or '—')
        username = f"@{acc['username']}" if acc.get('username') else '—'
        lines.append(v.item(f"<code>{info['phone']}</code>  {name} · {html.escape(username)} · id <code>{acc.get('id', '—')}</code>"))
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

        await engine.answer(event, visual.ok('FRX · Info') + "  " + visual.hint('ниже'))

    except Exception as e:
        await engine.answer(event, visual.err('Ошибка') + f"\n<code>{html.escape(str(e))}</code>")
        engine.tlog(f"Ошибка в .info: {e}")
