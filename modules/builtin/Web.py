"""
modules/builtin/Web.py — команда .web: сайт управления FRX (core/web/).

На сайте: конфиги и алиасы, модули (установка, просмотр, редактор FRX WebIDE, создание, удаление, Emulate —
запуск команд модуля прямо на сайте), файлы, логи, аккаунты, BotChat, нейросети, доступ и страницы, которые
описывают сами модули через `import web` (README, «Страницы сайта из модулей»).

Как открыть:
  - на этой же машине — http://127.0.0.1:<порт>;
  - юзербот на сервере — SSH-туннель: ssh -L <порт>:127.0.0.1:<порт> пользователь@сервер, затем тот же адрес у себя;
  - откуда угодно (телефон, другой компьютер, прямо в Telegram) — публичный адрес https://….trycloudflare.com:
    `.web tunnel on`. Туннель держит keeper (frx_systemkeeper.py), домен и открытые порты не нужны.
Вход на сайт по умолчанию ЗАКРЫТ. `.web` открывает его на 10 минут для одного устройства и присылает 5-значный
код (в ЛС от бота, без бота — в «Избранное»); после входа он снова закрыт. Без кода — кнопка «Открыть в Telegram»
(Mini App). Устройство помнит вход `session_days` дней; `.web logout` — выйти на всех устройствах.

Команды:
  .web                 поднять сайт, открыть вход и прислать ссылки и код (в ЛС от бота — с кнопками «Открыть в Telegram» / «в браузере»)
  .web login           только открыть вход и прислать новый код
  .web stop            выключить сайт
  .web tunnel on|off   включить / выключить публичный адрес;  .web tunnel new — получить новый адрес
  .web tunnel setup    способ публичного адреса: без регистрации (меняется) или постоянный (localhost.run с ключом,
                       свой домен в Cloudflare, Tailscale Funnel / свой адрес)
  .web logout          завершить все сессии сайта

Кнопка «Сайт» в меню чата с ботом (у владельца) ведёт на публичный адрес и меняется сама вместе с ним (menu_button).
Настройки (`.config web_panel`): autostart, autostop, autostop_minutes, tunnel, menu_button, session_days, а также настройки
Emulate: command_timeout, hide_modules, history_limit, emulate_upload_mb.
При .restart / .update сайт не выключается: keeper держит порт (и туннель), страница показывает «юзербот спит».
"""

import asyncio
import html
import re
import time

import db
import decorators
import engine
import visual

NAME = "Web"
MODULE_NAME = "web_panel"
VERSION = "2.2"
AUTHOR = "FRX"
DESCRIPTION = "Сайт управления FRX: конфиги, модули (IDE, Emulate), аккаунты, страницы модулей; вход по коду из Telegram, публичный адрес"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Web.py"


def _esc(x):
    return html.escape(str(x))


async def _cfg(key):
    """Конфиг модуля Web — с явным модулем: кнопки панели срабатывают и вне контекста модуля."""
    return await engine.get_config(key, module_name=MODULE_NAME)


async def _set(key, value):
    return await engine.edit_config(key, 'edit', value, module_name=MODULE_NAME)


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='autostart',
        description='Запускать сайт при запуске юзербота',
        data_type='bool', default=False,
    )
    await engine.create_config(
        key='autostop',
        description='Выключать сайт автоматически после простоя',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='autostop_minutes',
        description='Через сколько минут без активности выключать сайт',
        data_type='int', default=5, valid={'min_value': 1, 'max_value': 1440},
    )
    await engine.create_config(
        key='tunnel',
        description='Публичный адрес сайта — открыть сайт с любого устройства и в Telegram (способ: .web tunnel setup)',
        data_type='bool', default=False,
    )
    await engine.create_config(
        key='menu_button',
        description='Кнопка «Сайт» в меню чата со стационарным ботом — открывает сайт прямо в Telegram (только у владельца; адрес обновляется сам)',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='session_days',
        description='Сколько дней помнить вход на сайт на устройстве',
        data_type='int', default=30, valid={'min_value': 1, 'max_value': 365},
    )
    await engine.create_config(
        key='command_timeout',
        description='Emulate: предел времени команды, запущенной с сайта, сек (0 — без предела)',
        data_type='int', default=600, valid={'min_value': 0},
    )
    await engine.create_config(
        key='hide_modules',
        description='Emulate: MODULE_NAME модулей, которые нельзя запускать с сайта',
        data_type='list', default=['web_panel'],
    )
    await engine.create_config(
        key='emulate_upload_mb',
        description='Emulate: максимальный размер файла, который можно приложить к команде (МБ)',
        data_type='int', default=25, valid={'min_value': 1, 'max_value': 50},
    )
    await engine.create_config(
        key='history_limit',
        description='Emulate: сколько последних записей ленты хранить на модуль (пока сайт запущен)',
        data_type='int', default=200, valid={'min_value': 20, 'max_value': 2000},
    )
    _sync_tunnel(await _cfg('tunnel'), hold=False)
    _start_tunnel_watch()
    engine.web.start_hook = _bot_start_web            # /start web в боте — свежая ссылка (aiobot/bot.py)
    # вернуть сайт после рестарта / автозапуск по конфигу — в фоне: передача порта от keeper'а может занять
    # до пары секунд, а запуск юзербота ждать её не должен
    engine.web.start_wake_watch()          # сторож keeper'а — заход на адрес поднимает сайт
    task = asyncio.ensure_future(engine.web.resume_or_autostart())
    task.add_done_callback(lambda t: (not t.cancelled() and t.exception()) and engine.tlog(f"[Web] автозапуск сайта: {t.exception()}"))


_WATCH = {'task': None, 'hold': 0.0}       # hold — команда сама ждёт адрес и пришлёт ссылку


def _start_tunnel_watch():
    """Адрес туннеля сменился (keeper перезапустил cloudflared, перезапуск keeper'а) — присылаем владельцу новую ссылку."""
    t = _WATCH['task']
    if t is not None and not t.done():
        return

    async def loop():
        while True:
            await asyncio.sleep(5)
            if getattr(engine, '_aiogram_bot', None) is not None and not getattr(engine, '_bot_username', None):
                try:
                    engine._bot_username = (await engine._aiogram_bot.get_me()).username
                except Exception:
                    pass
            try:
                await _sync_menu_button()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                engine.tlog(f"[Web] кнопка сайта в боте: {e}")
            try:
                st = engine.web.tunnel_state()
                url = st.get('url') if st.get('status') == 'up' else None
                if not url or not engine.web.tunnel_wanted() or time.time() < _WATCH['hold']:
                    continue
                if url == await db.gget(f'{MODULE_NAME}:last_public_url'):
                    continue
                await db.gset(f'{MODULE_NAME}:last_public_url', url)
                code = engine.web.auth.open_window()
                text = (visual.title("Новый адрес сайта", sub="прежний больше не работает") + "\n"
                        + visual.field("адрес", _esc(url)) + "\n"
                        + visual.hint("открытые вкладки сайта переедут на него сами, без входа") + "\n\n"
                        + engine.web.auth.code_text(code))
                if not await _send_links_to_owner(text, url):
                    try:
                        await engine.web.auth._deliver(text)
                    except Exception:
                        engine.web.auth.close()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                engine.tlog(f"[Web] слежение за адресом туннеля: {e}")
    _WATCH['task'] = asyncio.ensure_future(loop())


# ------------------------------------------------------------------ #
# Кнопка «Сайт» в меню чата с ботом (Telegram: menu button → Mini App)
# ------------------------------------------------------------------ #

_MENU = {'url': ..., 'owner': None, 'fails': 0, 'retry': 0.0}     # ... — ещё не сверялись с Telegram в этом запуске


async def _menu_want():
    """Какой адрес должен стоять на кнопке: str — публичный адрес, None — кнопку убрать, ... — не трогать."""
    if not await _cfg('menu_button') or not engine.web.tunnel_wanted():
        return None
    st = engine.web.tunnel_state()
    if st.get('status') == 'up' and st.get('url'):
        return st['url']
    return ...                     # адрес поднимается / переподключается — старую кнопку не дёргаем, обновим, когда будет новый


async def _sync_menu_button(force=False):
    """Ставит владельцу кнопку меню «Сайт» с текущим публичным адресом (или убирает её). Сверяется каждые 5 с,
    в Telegram ходит только когда адрес изменился."""
    bot = getattr(engine, '_aiogram_bot', None)
    owner = getattr(engine, '_owner_id', None)
    if bot is None or not owner:
        return
    want = await _menu_want()
    if want is ...:
        return
    if _MENU['url'] is ...:                                     # первый проход после запуска: что ставили в прошлый раз
        _MENU['url'] = await db.gget(f'{MODULE_NAME}:menu_button_url')
        _MENU['owner'] = await db.gget(f'{MODULE_NAME}:menu_button_owner') or owner
    from aiogram.types import MenuButtonDefault, MenuButtonWebApp, WebAppInfo
    if _MENU['owner'] != owner:                                 # сменился аккаунт-владелец: у прежнего кнопку убираем
        if _MENU['url']:
            try:
                await bot.set_chat_menu_button(chat_id=_MENU['owner'], menu_button=MenuButtonDefault())
            except Exception:
                pass
        _MENU.update(url=None, owner=owner)
    if want == _MENU['url'] and not force:
        return
    if time.time() < _MENU['retry'] and not force:
        return
    try:
        if want:
            await bot.set_chat_menu_button(chat_id=owner, menu_button=MenuButtonWebApp(text='Сайт', web_app=WebAppInfo(url=want)))
        else:
            await bot.set_chat_menu_button(chat_id=owner, menu_button=MenuButtonDefault())
    except Exception as e:
        _MENU['fails'] += 1
        _MENU['retry'] = time.time() + min(600, 15 * 2 ** min(_MENU['fails'], 5))
        if _MENU['fails'] in (1, 4):
            engine.tlog(f"[Web] кнопку сайта в боте не обновить: {e}")
        return
    _MENU.update(url=want, fails=0, retry=0.0)
    try:
        await db.gset(f'{MODULE_NAME}:menu_button_url', want)
        await db.gset(f'{MODULE_NAME}:menu_button_owner', owner)
    except Exception:
        pass
    engine.tlog(f"[Web] кнопка «Сайт» в боте: {want or 'убрана'}")


def _menu_state_text():
    url = _MENU['url'] if _MENU['url'] is not ... else None
    return "есть — «Сайт» слева от поля ввода в чате с ботом" if url else None


async def _remember_url(url):
    _WATCH['hold'] = 0.0
    try:
        await db.gset(f'{MODULE_NAME}:last_public_url', url)
    except Exception:
        pass


def _sync_tunnel(want, renew=False, hold=True, setup=None):
    """Просит keeper включить/выключить туннель (setup — сменить способ адреса). Возвращает текст ошибки или None."""
    _WATCH['hold'] = time.time() + 160 if (want and hold) else 0.0
    try:
        if want or engine.web.tunnel_wanted() or setup is not None:
            engine.web.request_tunnel(bool(want), renew=renew, setup=setup)
    except Exception as e:
        return str(e)
    return None


async def _minutes_text() -> str:
    limit = await engine.web.idle_limit()
    if not limit:
        return visual.field("автовыключение", "отключено", code=False)
    return visual.field("автовыключение", f"через {limit // 60} мин простоя")


# ------------------------------------------------------------------ #
# Ход подключения публичного адреса: Cloudflare иногда отвечает не с первой попытки — keeper пробует снова сам,
# а владелец видит, какая идёт попытка и что происходит, вместо «зависшего» сообщения.
# ------------------------------------------------------------------ #

def _tunnel_step(st) -> tuple:
    """(номер попытки, сервис, что сейчас происходит, ошибка прошлой попытки | '')."""
    status = st.get('status')
    att = int(st.get('attempt') or 1)
    prov = st.get('provider') or 'cloudflare'
    svc = {'cloudflare': 'Cloudflare', 'cloudflare-token': 'Cloudflare (свой домен)', 'custom': 'свой адрес',
           'localhost.run': 'localhost.run' if st.get('mode') in ('lhr',) else 'localhost.run (запасной)'}.get(prov, prov)
    err = ''
    if status == 'downloading':
        step = 'скачиваю cloudflared — один раз, до минуты'
    elif status == 'connecting':
        step = f'адрес получен, жду соединения с {svc}'
    elif status == 'error':
        nt = st.get('next_try')
        left = int(nt - time.time()) if nt else 0
        step = f'не вышло — следующая попытка через {left} с' if left > 0 else 'не вышло — пробую снова'
        err = str(st.get('error') or '')[:160]
    else:
        step = f'запускаю туннель ({svc})'
    return att, svc, step, err


def _tunnel_progress_text(st, elapsed) -> str:
    att, svc, step, err = _tunnel_step(st)
    lines = [visual.wait(f"Поднимаю публичный адрес сайта… {int(elapsed)} с"),
             visual.field("попытка", f"{att} ({svc})", code=False),
             visual.item(_esc(step))]
    if err:
        lines.append(visual.hint(_esc(err)))
    lines.append(visual.hint("обычно 5–30 с; если сервис не отвечает, FRX сам пробует снова (до 2,5 мин)"))
    return "\n".join(lines)


def _progress_editor(show):
    """on_progress для wait_tunnel: правит сообщение, только когда изменилось, и не чаще раза в 4 с (лимиты Telegram)."""
    last = {'key': None, 't': 0.0}

    async def on_progress(st, elapsed):
        att, _svc, step, _err = _tunnel_step(st)
        key = (st.get('status'), att, st.get('provider'))
        now = time.monotonic()
        if key == last['key'] and now - last['t'] < 10:       # то же состояние — только счётчик секунд, раз в 10 с
            return
        if now - last['t'] < 4:
            return
        last.update(key=key, t=now)
        await show(st, elapsed)
    return on_progress


async def _ensure_tunnel(event):
    """Если туннель включён в настройках — дождаться адреса. Возвращает (url | None, ошибка | None)."""
    if not await _cfg('tunnel'):
        return None, None
    err = _sync_tunnel(True)
    if err:
        return None, err
    st = engine.web.tunnel_state()
    if st.get('status') != 'up':
        await engine.answer(event, _tunnel_progress_text(st, 0))
        st = await engine.web.wait_tunnel(150, on_progress=_progress_editor(
            lambda s_, e_: engine.answer(event, _tunnel_progress_text(s_, e_))))
    if st.get('status') == 'up':
        await _remember_url(st['url'])
        return st['url'], None
    return None, st.get('error') or 'туннель не поднялся'


def _links_text(port, public, tunnel_err, hide_public=False):
    lines = [
        visual.title("Сайт FRX", sub="запущен"),
        visual.field("на этой машине", f"http://127.0.0.1:{port}"),
        visual.item(f"по SSH: <code>ssh -L {port}:127.0.0.1:{port} пользователь@сервер</code> → тот же адрес у себя"),
    ]
    if public and hide_public:
        lines.append(visual.item("публичный адрес — в ЛС от бота"))
    elif public:
        lines.append(visual.field("откуда угодно", _esc(public)))
    elif tunnel_err:
        lines.append(visual.warn("публичный адрес не поднялся: " + _esc(tunnel_err)))
    else:
        lines.append(visual.item("публичный адрес выключен — <code>.web tunnel on</code>"))
    bot = getattr(engine, '_bot_username', None)
    if public and bot:
        lines.append(visual.field("постоянная ссылка", f"t.me/{_esc(bot)}?start=web", code=True))
        lines.append(visual.item("всегда присылает актуальный адрес — сохраните её в закладки", 1))
    lines += ["", visual.hint("вход — по коду ниже (открыт 10 минут для одного устройства)" + (" или кнопкой «Открыть в Telegram»" if public else ""))]
    return "\n".join(lines)


async def _send_links_to_owner(text, public):
    """Ссылки в ЛС от бота (кнопка Mini App работает только там). False — бота нет или отправить не вышло."""
    if getattr(engine, '_aiogram_bot', None) is None:
        return False
    markup = None
    if public:
        try:
            from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
            one_click = engine.web.login_link(public) or public       # одноразовая ссылка: вход без кода
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='Открыть в Telegram', web_app=WebAppInfo(url=public))],
                [InlineKeyboardButton(text='Открыть в браузере — вход в один клик', url=one_click)],
            ])
            text += "\n" + visual.hint("кнопка «в браузере» входит сама — один раз, 15 минут; потом — по коду")
        except Exception:
            markup = None
    try:
        await engine.bot_send_to_owner(text, buttons=markup)
        return True
    except Exception as e:
        engine.tlog(f"[Web] ссылки в ЛС не отправлены: {e}")
        return False


async def _deliver_code(code) -> str:
    """Код входа владельцу: в ЛС от бота, без бота — в «Избранное». Возвращает, куда отправлен."""
    try:
        sent = await engine.web.auth._deliver(engine.web.auth.code_text(code))
        return sent.replace('стационарного ', '')
    except Exception as e:
        engine.web.auth.close()
        return f"не отправлен ({e})"


@decorators.command('web', 'site')
async def web_cmd(event):
    """сайт управления: `.web` — поднять, открыть вход и прислать ссылки и код; login; stop; tunnel on|off|new; logout"""
    args = engine.get_args().strip().lower().split()
    sub = args[0] if args else ''

    if sub in ('stop', 'off', 'выкл'):
        if not engine.web.running:
            await engine.answer(event, visual.hint("Сайт и так не запущен"))
            return
        await engine.web.stop('по команде')
        await engine.answer(event, f"{visual.visual('status_stop')} <b>Сайт выключен</b>")
        return

    if sub in ('login', 'code', 'код'):
        if not engine.web.running:
            await engine.web.start()
        code = engine.web.auth.open_window()
        where = await _deliver_code(code)
        await engine.answer(event, visual.ok("Вход на сайт открыт на 10 минут") + "\n" + visual.field("код", where, code=False)
                            + "\n" + visual.hint("страница входа откроется сама; после входа он снова закроется"))
        return

    if sub == 'logout':
        n = engine.web.auth.drop_all()
        await engine.answer(event, visual.ok("Все входы на сайт завершены") + "\n" + visual.field("сессий", n)
                            + "\n" + visual.hint("на каждом устройстве снова понадобится код из Telegram"))
        return

    if sub == 'tunnel' and len(args) > 1 and args[1] in ('setup', 'mode', 'способ'):
        try:
            await _open_setup(event)
        except Exception as e:
            await engine.answer(event, visual.err("Настройка адреса недоступна") + "\n" + visual.hint(_esc(e)))
        return

    if sub == 'tunnel':
        mode = args[1] if len(args) > 1 else ''
        if mode not in ('on', 'off', 'new'):
            st = engine.web.tunnel_state()
            status = {'up': 'работает', 'starting': 'запускается', 'connecting': 'подключается к Cloudflare',
                      'downloading': 'скачивается cloudflared',
                      'error': 'ошибка', 'off': 'выключен'}.get(st.get('status'), 'выключен')
            lines = [visual.title("Публичный адрес"), visual.field("состояние", status)]
            if st.get('url'):
                lines.append(visual.field("адрес", _esc(st['url'])))
            if st.get('status') == 'error' and st.get('error'):
                lines.append(visual.warn(_esc(st['error'])))
            lines.append(visual.field("способ", _MODE_TITLES.get(engine.web.tunnel_setup().get('mode'), 'авто'), code=False))
            lines += ["", visual.hint(".web tunnel on | off | new (новый адрес) | setup (постоянный адрес)")]
            await engine.answer(event, "\n".join(lines))
            return
        await _set('tunnel', mode != 'off')
        err = _sync_tunnel(mode != 'off', renew=(mode == 'new'))
        if err:
            await _set('tunnel', False)
            await engine.answer(event, visual.err("Туннель недоступен") + "\n" + visual.hint(_esc(err)))
            return
        if mode == 'off':
            await engine.answer(event, visual.ok("Публичный адрес выключен") + "\n"
                                + visual.hint("сайт снова доступен только с этой машины и по SSH"))
            return
        await engine.answer(event, _tunnel_progress_text(engine.web.tunnel_state(), 0))
        st = await engine.web.wait_tunnel(150, on_progress=_progress_editor(
            lambda s_, e_: engine.answer(event, _tunnel_progress_text(s_, e_))))
        if st.get('status') != 'up':
            await engine.answer(event, visual.err("Публичный адрес пока не поднялся") + "\n"
                                + visual.hint(_esc(st.get('error') or 'нет ответа от cloudflared'))
                                + "\n" + visual.hint("FRX продолжает пробовать сам — как только адрес заработает, ссылка придёт в ЛС; "
                                                     "или .web tunnel new"))
            return
        if not engine.web.running:
            await engine.web.start()
        await _remember_url(st['url'])
        code = engine.web.auth.open_window()
        text = _links_text(engine.web.port, st['url'], None)
        sent = await _send_links_to_owner(text + "\n\n" + engine.web.auth.code_text(code), st['url'])
        where = 'в ЛС от бота' if sent else await _deliver_code(code)
        await engine.answer(event, visual.ok("Публичный адрес работает") + "\n"
                            + (visual.field("адрес", _esc(st['url'])) if event.is_private or not sent else visual.item("адрес и кнопки — в ЛС от бота"))
                            + "\n" + visual.field("код входа", where, code=False))
        return

    try:
        await engine.web.start()
    except Exception as e:
        await engine.answer(event, visual.err("Не удалось поднять сайт") + f"\n<code>{_esc(e)}</code>")
        engine.tlog(f"[Web] ошибка запуска сайта: {e}")
        return

    public, tunnel_err = await _ensure_tunnel(event)
    sent_to = await _open_login_and_send(public, tunnel_err)
    try:
        await _open_panel(event, note=f"вход открыт на 10 мин\nкод входа — {sent_to}")
    except Exception:
        # стационарного бота нет — обычный ответ, управление — подкомандами
        port = engine.web.port
        text = (_links_text(port, public, tunnel_err, hide_public=not event.is_private) + "\n" + await _minutes_text()
                + "\n" + visual.field("код входа", sent_to, code=False)
                + f"\n\n{visual.visual('status_stop')} выключить — <code>.web stop</code>")
        await engine.answer(event, text)
        return
    await engine.answer(event, visual.ok("Сайт запущен") + "\n" + visual.hint("панель управления — ниже"))


async def _open_login_and_send(public=None, tunnel_err=None) -> str:
    """Открывает вход (10 мин, одно устройство) и шлёт владельцу ссылки с кодом. Возвращает, куда ушёл код."""
    code = engine.web.auth.open_window()
    if engine.web.running:
        text = _links_text(engine.web.port, public, tunnel_err) + "\n" + await _minutes_text()
        if await _send_links_to_owner(text + "\n\n" + engine.web.auth.code_text(code), public):
            return 'в ЛС от бота'
    return await _deliver_code(code)


# ------------------------------------------------------------------ #
# Инлайн-панель управления сайтом
# ------------------------------------------------------------------ #

def _on(flag, text):
    return f"{visual.visual('on') if flag else visual.visual('off')} {text}"


async def _panel_text(private: bool, note: str = '') -> str:
    web = engine.web
    st = web.tunnel_state()
    status = st.get('status')
    lines = [visual.title("Сайт FRX", sub="панель управления")]
    if web.running:
        lines.append(visual.field("сайт", f"работает, http://127.0.0.1:{web.port}"))
    else:
        lines.append(visual.field("сайт", "выключен", code=False))
    if not await _cfg('tunnel'):
        lines.append(visual.field("публичный адрес", "выключен", code=False))
    elif status == 'up':
        lines.append(visual.field("публичный адрес", _esc(st['url']) if private else "работает (адрес — в ЛС от бота)",
                                  code=private))
    elif status == 'error' and st.get('next_try'):
        att, svc, step, _err = _tunnel_step(st)
        lines.append(visual.field("публичный адрес", f"попытка {att} — {step}", code=False))
    elif status == 'error':
        lines.append(visual.field("публичный адрес", "ошибка", code=False))
        if st.get('error'):
            lines.append(visual.item(f"<i>{_esc(str(st['error'])[:180])}</i>", 1))
    else:
        att, svc, step, _err = _tunnel_step(st)
        lines.append(visual.field("публичный адрес", f"попытка {att} — {step}", code=False))
    if getattr(engine, '_aiogram_bot', None) is not None:
        if not await _cfg('menu_button'):
            lines.append(visual.field("кнопка в боте", "выключена", code=False))
        else:
            lines.append(visual.field("кнопка в боте", _menu_state_text() or "появится, когда заработает публичный адрес", code=False))
    left = web.auth.window_left()
    lines.append(visual.field("вход", f"открыт ещё {left // 60 + 1} мин" if left else "закрыт", code=False))
    lines.append(visual.field("вошедших устройств", web.auth.count()))
    limit = await web.idle_limit()
    lines.append(visual.field("автозапуск", "да" if await _cfg('autostart') else "нет", code=False))
    lines.append(visual.field("автовыключение", f"через {limit // 60} мин простоя" if limit else "нет", code=False))
    if web.running:
        lines += ["", visual.hint(f"по SSH: ssh -L {web.port}:127.0.0.1:{web.port} пользователь@сервер")]
    if note:
        lines += ["", visual.hint(_esc(note))]
    return "\n".join(lines)


async def _panel_buttons(private: bool):
    web = engine.web
    st = web.tunnel_state()
    tunnel = await _cfg('tunnel')
    rows = []
    if web.running and tunnel and st.get('status') == 'up' and private:
        rows.append([(visual.btn('Открыть сайт'), 'url:' + st['url'])])
    rows.append([(visual.btn('Выключить сайт', 'close'), 'wp:stop', "danger") if web.running else (visual.btn('Запустить сайт'), 'wp:start', "success")])
    rows.append([(visual.btn('Пустить устройство — прислать код'), 'wp:login', "primary")])
    rows.append([(_on(tunnel, 'Публичный адрес'), 'wp:tunnel')] + ([(visual.btn('Новый адрес'), 'wp:renew')] if tunnel else []))
    rows.append([(visual.btn('Способ адреса · ' + _MODE_TITLES.get(web.tunnel_setup().get('mode'), 'авто')), 'wp:setup')])
    if getattr(engine, '_aiogram_bot', None) is not None:
        rows.append([(_on(await _cfg('menu_button'), 'Кнопка «Сайт» в боте'), 'wp:menubtn')])
    rows.append([(_on(await _cfg('autostart'), 'Автозапуск'), 'wp:autostart'),
                 (_on(await _cfg('autostop'), 'Автовыключение'), 'wp:autostop')])
    n = web.auth.count()
    if n:
        rows.append([(visual.btn(f'Выйти на всех устройствах ({n})'), 'wp:logout')])
    rows.append([(visual.btn('Обновить'), 'wp:refresh'), (visual.btn('Закрыть', 'close'), 'wp:close')])
    return rows


async def _open_panel(event, note=''):
    """Инлайн-панель сайта. RuntimeError/исключение — нет стационарного бота (вызывающий отвечает текстом)."""
    private = bool(getattr(event, 'is_private', False))
    form = await engine.create_inlineform(chat_id=event.chat_id, text=await _panel_text(private, note),
                                          buttons=await _panel_buttons(private))
    state = {'watch': None}

    async def redraw(note=''):
        await form.edit(await _panel_text(private, note), buttons=await _panel_buttons(private))

    def watch_tunnel():
        """После включения туннеля перерисовываем панель, пока он не поднимется (до 90 с), и шлём ссылки."""
        t = state['watch']
        if t is not None and not t.done():
            t.cancel()

        async def go():
            async def show(st_, el_):
                att, svc, step, err = _tunnel_step(st_)
                await redraw(f"поднимаю публичный адрес, {int(el_)} с\nпопытка {att} ({svc})\n{step}"
                             + (f"\nпрошлая ошибка: {err[:90]}" if err else ''))
            st = await engine.web.wait_tunnel(150, on_progress=_progress_editor(show))
            if st.get('status') == 'up':
                await _remember_url(st['url'])
                await _sync_menu_button()
                sent = await _open_login_and_send(st['url'])
                await redraw(f"публичный адрес работает\nссылка и код — {sent}")
            else:
                await redraw("публичный адрес пока не поднялся — FRX пробует дальше сам, ссылка придёт в ЛС; или «Новый адрес»")
        state['watch'] = asyncio.ensure_future(go())

    @form.on_click(data_startswith='wp:')
    async def _click(click):
        act = click.data[3:]
        try:
            if act == 'close':
                await click.answer()
                t = state['watch']
                if t is not None and not t.done():
                    t.cancel()
                await form.delete()
                return
            if act == 'refresh':
                await click.answer("обновлено")
                await redraw()
            elif act == 'start':
                await click.answer("запускаю…")
                await engine.web.start()
                await redraw("сайт запущен")
            elif act == 'stop':
                await click.answer("выключаю…")
                await engine.web.stop('кнопкой')
                await redraw("сайт выключен")
            elif act == 'login':
                if not engine.web.running:
                    await engine.web.start()
                st = engine.web.tunnel_state()
                public = st.get('url') if st.get('status') == 'up' and await _cfg('tunnel') else None
                sent = await _open_login_and_send(public)
                await click.answer("код отправлен " + sent)
                await redraw(f"вход открыт на 10 мин для одного устройства\nкод входа — {sent}")
            elif act in ('tunnel', 'renew'):
                want = True if act == 'renew' else not await _cfg('tunnel')
                await _set('tunnel', want)
                err = _sync_tunnel(want, renew=(act == 'renew'))
                if err:
                    await _set('tunnel', False)
                    await click.answer(err[:190], show_alert=True)
                    await redraw()
                    return
                await click.answer("поднимаю публичный адрес…" if want else "публичный адрес выключен")
                await redraw("поднимаю публичный адрес… (первый раз скачивается cloudflared — до минуты)" if want else "")
                if want:
                    if not engine.web.running:
                        await engine.web.start()
                    watch_tunnel()
            elif act == 'setup':
                await click.answer()
                await _open_setup(None, form=form, back=redraw)
            elif act == 'menubtn':
                value = not await _cfg('menu_button')
                await _set('menu_button', value)
                await _sync_menu_button(force=True)
                await click.answer(("кнопка «Сайт» появится в меню чата с ботом" if value else "кнопка убрана"))
                await redraw()
            elif act in ('autostart', 'autostop'):
                value = not await _cfg(act)
                await _set(act, value)
                await click.answer(("включено" if value else "выключено"))
                await redraw()
            elif act == 'logout':
                await click.answer()
                await form.edit(visual.warn("Выйти на всех устройствах? На каждом снова понадобится код из Telegram."),
                                buttons=[[(visual.btn('Да, выйти везде'), 'wp:logout!', "danger")], [(visual.btn('Назад', 'back'), 'wp:refresh')]])
            elif act == 'logout!':
                n = engine.web.auth.drop_all()
                await click.answer(f"сессий завершено: {n}")
                await redraw(f"все входы завершены ({n})")
            else:
                await click.answer()
        except Exception as e:
            engine.tlog(f"[Web] панель: {e}")
            try:
                await click.answer(str(e)[:190] or 'ошибка', show_alert=True)
            except Exception:
                pass
    return form


# ------------------------------------------------------------------ #
# Способ публичного адреса — .web tunnel setup
# Бесплатные туннели без регистрации меняют адрес (localhost.run — примерно раз в 20 минут). Открытые страницы
# переезжают сами (core/web/server.py: /api/tunnel/handoff), но постоянный адрес удобнее — здесь его настраивают.
# ------------------------------------------------------------------ #

_MODE_TITLES = {
    'auto': 'авто', 'cloudflare': 'Cloudflare', 'lhr': 'localhost.run',
    'cf_token': 'свой домен (Cloudflare)', 'custom': 'свой адрес',
}


def _mask(v, keep=4):
    v = str(v or '')
    return (v[:keep] + '…' + v[-keep:]) if len(v) > keep * 2 + 3 else ('•' * len(v))


async def _apply_setup(setup: dict):
    """Сохранить способ и (раз туннель включают) поднять адрес заново."""
    await _set('tunnel', True)
    err = _sync_tunnel(True, renew=True, hold=False, setup=setup)   # ссылку пришлёт слежение за адресом
    if err:
        raise RuntimeError(err)
    if not engine.web.running:
        await engine.web.start()


async def _tailscale_url(port):
    """Tailscale Funnel: включает публикацию порта и возвращает https://<машина>.<tailnet>.ts.net."""
    import json as _json
    import shutil
    exe = shutil.which('tailscale')
    if not exe:
        raise RuntimeError("Tailscale не установлен (tailscale.com/download) или его нет в PATH")

    async def run(*a, timeout=40):
        proc = await asyncio.create_subprocess_exec(exe, *a, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
        return proc.returncode, (out or b'').decode('utf-8', 'replace')
    code, out = await run('funnel', '--bg', str(port))
    if code != 0:
        raise RuntimeError("tailscale funnel: " + out.strip()[-300:])
    code, out = await run('status', '--json')
    try:
        name = (_json.loads(out).get('Self') or {}).get('DNSName', '').rstrip('.')
    except ValueError:
        name = ''
    if not name:
        raise RuntimeError("не удалось узнать адрес машины в Tailscale (tailscale status)")
    return 'https://' + name


async def _setup_view(view):
    web = engine.web
    cur = web.tunnel_setup()
    mode = cur.get('mode') or 'auto'
    port = web.port or 8878
    if view == 'lhr':
        try:
            pub = await web.lhr_pubkey()
        except Exception as e:
            pub, err = None, str(e)
        else:
            err = ''
        lines = [visual.title("localhost.run + ключ", sub="бесплатно, адрес держится намного дольше"),
                 visual.item("1. Зарегистрируйтесь на admin.localhost.run (бесплатно)"),
                 visual.item("2. Добавьте этот ключ в разделе SSH keys:")]
        lines.append(f"<code>{_esc(pub)}</code>" if pub else visual.warn(_esc(err)))
        lines += [visual.item("3. Нажмите «Ключ добавлен»"), "",
                  visual.hint("ключ создан только для этого и лежит в user/data/bin; без регистрации адрес всё равно работает, но меняется чаще")]
        rows = [[(visual.btn('admin.localhost.run'), 'url:https://admin.localhost.run/')]]
        if pub:
            rows.append([(visual.btn('Ключ добавлен — включить'), 'ts:lhr_on', "success")])
        rows.append([(visual.btn('Назад', 'back'), 'ts:main')])
        return "\n".join(lines), rows
    if view == 'cf':
        lines = [visual.title("Свой домен через Cloudflare", sub="постоянный адрес, бесплатно; нужен домен в Cloudflare"),
                 visual.item("1. one.dash.cloudflare.com → Networks → Tunnels → Create a tunnel (Cloudflared)"),
                 visual.item("2. Скопируйте токен из команды установки (длинная строка после --token)"),
                 visual.item(f"3. Public Hostname: свой поддомен → Service <code>http://127.0.0.1:{port}</code>"),
                 visual.item("4. Впишите токен и адрес кнопками ниже, затем «Включить»"), "",
                 visual.field("токен", _mask(cur.get('cf_token')) if cur.get('cf_token') else 'не задан', code=False),
                 visual.field("адрес", _esc(cur.get('cf_host') or 'не задан'), code=False),
                 "", visual.hint("cloudflared нужен соединение по порту 7844 — если провайдер его режет, этот способ не поднимется")]
        rows = [[engine.InputButton(visual.btn('Токен'), 'cf_token', hint='токен туннеля'),
                 engine.InputButton(visual.btn('Адрес'), 'cf_host', hint='frx.example.com')]]
        if cur.get('cf_token') and cur.get('cf_host'):
            rows.append([(visual.btn('Включить'), 'ts:cf_on', "success")])
        rows.append([(visual.btn('Назад', 'back'), 'ts:main')])
        return "\n".join(lines), rows
    if view == 'custom':
        lines = [visual.title("Свой адрес", sub="Tailscale Funnel, свой сервер, nginx…"),
                 visual.item("Адрес поднимает другая программа, FRX только проверяет, что он отвечает, и присылает его."),
                 visual.item(f"Tailscale Funnel (бесплатно, постоянный адрес *.ts.net): поставьте Tailscale, войдите — и нажмите кнопку ниже "
                             f"(или вручную: <code>tailscale funnel --bg {port}</code>)."),
                 visual.item(f"Свой сервер / прокси: направьте домен на <code>http://127.0.0.1:{port}</code> и впишите адрес."), "",
                 visual.field("адрес", _esc(cur.get('custom_url') or 'не задан'), code=False)]
        rows = [[(visual.btn('Tailscale Funnel — включить'), 'ts:tailscale', "success")],
                [engine.InputButton(visual.btn('Вписать адрес'), 'custom_url', hint='https://')]]
        if cur.get('custom_url'):
            rows.append([(visual.btn('Включить'), 'ts:custom_on', "success")])
        rows.append([(visual.btn('Назад', 'back'), 'ts:main')])
        return "\n".join(lines), rows
    mark = lambda m: ('● ' if mode == m else '') + _MODE_TITLES[m]
    lines = [visual.title("Публичный адрес", sub="как открыть сайт откуда угодно"),
             visual.field("сейчас", _MODE_TITLES.get(mode, mode), code=False), "",
             visual.item("<b>авто</b> — Cloudflare, при сбое localhost.run. Без регистрации, но адрес меняется: открытые вкладки "
                         "переезжают сами, новая ссылка приходит в ЛС"),
             visual.item("<b>localhost.run</b> — сразу запасной сервис, без ожидания Cloudflare (если он у вас не подключается)"),
             visual.item("<b>localhost.run + ключ</b> — бесплатная регистрация, адрес держится намного дольше"),
             visual.item("<b>свой домен (Cloudflare)</b> — постоянный адрес, нужен домен"),
             visual.item("<b>свой адрес</b> — Tailscale Funnel (постоянный *.ts.net) или свой сервер"), "",
             visual.hint("где бы ни был сайт, t.me/<бот>?start=web всегда присылает актуальную ссылку")]
    rows = [[(mark('auto'), 'ts:auto'), (mark('lhr'), 'ts:lhr_plain')],
            [(visual.btn('localhost.run + ключ'), 'ts:view:lhr')],
            [(mark('cf_token') if mode == 'cf_token' else visual.btn('Свой домен (Cloudflare)'), 'ts:view:cf')],
            [(mark('custom') if mode == 'custom' else visual.btn('Свой адрес / Tailscale'), 'ts:view:custom')],
            [(visual.btn('Назад', 'back'), 'ts:back')]]
    return "\n".join(lines), rows


async def _open_setup(event, form=None, back=None):
    """Форма выбора способа публичного адреса. form — уже открытая панель .web (рисуем в ней)."""
    own = form is None
    if own:
        text, rows = await _setup_view('main')
        form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=rows)
    state = getattr(form, '_frx_setup', None)
    if state is not None:                     # обработчики на этой форме уже есть — просто показываем главный экран
        await state['show']('main')
        return form
    state = {'view': 'main'}
    form._frx_setup = state

    async def show(view, note='', bad=False):
        state['view'] = view
        text, rows = await _setup_view(view)
        if note:
            text += "\n\n" + (visual.warn(_esc(note)) if bad else visual.ok(_esc(note)))
        await form.edit(text, buttons=rows)
    state['show'] = show

    if not own:
        await show('main')

    async def done(note):
        await show('main', note)

    @form.on_click(data_startswith='ts:')
    async def _ts(click):
        act = click.data[3:]
        try:
            if act.startswith('view:'):
                await click.answer()
                await show(act[5:])
            elif act == 'main':
                await click.answer()
                await show('main')
            elif act == 'back':
                await click.answer()
                if back is not None:
                    await back()
                else:
                    await form.edit(visual.ok("Готово") + "\n" + visual.hint("панель сайта — .web"), buttons=None)
                    await form.close()
            elif act in ('auto', 'lhr_plain'):
                await _apply_setup({'mode': 'auto' if act == 'auto' else 'lhr', 'lhr_key': False})
                await click.answer("способ сменён — поднимаю адрес")
                await done("адрес поднимается, ссылка придёт в ЛС")
            elif act == 'lhr_on':
                await _apply_setup({'mode': 'lhr', 'lhr_key': True})
                await click.answer("включаю localhost.run с ключом")
                await done("localhost.run + ключ — ссылка придёт в ЛС")
            elif act == 'cf_on':
                await _apply_setup({'mode': 'cf_token'})
                await click.answer("подключаю туннель Cloudflare")
                await done("свой домен — как только Cloudflare подключится, ссылка придёт в ЛС")
            elif act == 'custom_on':
                await _apply_setup({'mode': 'custom'})
                await click.answer("проверяю адрес")
                await done("свой адрес — FRX проверит его и пришлёт ссылку")
            elif act == 'tailscale':
                await click.answer("включаю Tailscale Funnel…")
                url = await _tailscale_url(engine.web.port or 8878)
                await _apply_setup({'mode': 'custom', 'custom_url': url})
                await done(f"Tailscale Funnel: {url}")
            else:
                await click.answer()
        except Exception as e:
            try:
                await click.answer(str(e)[:190] or 'ошибка', show_alert=True)
            except Exception:
                pass

    @form.on_input()
    async def _inp(inp):
        key, value = inp.data, (inp.value or '').strip()
        try:
            if key == 'cf_token':
                if not re.fullmatch(r'[A-Za-z0-9_=+/.-]{40,}', value):
                    raise ValueError("не похоже на токен туннеля — скопируйте строку после --token целиком")
                engine.web.request_tunnel(engine.web.tunnel_wanted(), setup={'cf_token': value})
                await show('cf', 'токен сохранён')
            elif key == 'cf_host':
                host = re.sub(r'^https?://', '', value).strip('/').lower()
                if not re.fullmatch(r'(?:[a-z0-9-]+\.)+[a-z]{2,}', host):
                    raise ValueError("нужен домен вида frx.example.com")
                engine.web.request_tunnel(engine.web.tunnel_wanted(), setup={'cf_host': host})
                await show('cf', 'адрес сохранён')
            elif key == 'custom_url':
                url = engine._normalize_link('custom_url', value, {'allow_tg': False})
                if not url:
                    raise ValueError("адрес пустой")
                engine.web.request_tunnel(engine.web.tunnel_wanted(), setup={'custom_url': url.rstrip('/')})
                await show('custom', 'адрес сохранён')
        except Exception as e:
            await show(state['view'], str(e), bad=True)
    return form


# ------------------------------------------------------------------ #
# Постоянная ссылка: t.me/<бот>?start=web — бот присылает актуальный адрес и вход в один клик
# ------------------------------------------------------------------ #

async def _bot_start_web():
    """Вызывается ботом на /start web от владельца (aiobot/bot.py)."""
    if not engine.web.running:
        await engine.web.start()
    url = engine.web.public_url() if engine.web.tunnel_wanted() else None
    if not url:
        if not await _cfg('tunnel'):
            await engine.bot_send_to_owner(visual.warn("Публичный адрес выключен") + "\n"
                                           + visual.hint("включите: .web tunnel on — или откройте http://127.0.0.1:" + str(engine.web.port)))
            return
        await engine.bot_send_to_owner(visual.wait("Публичный адрес поднимается — ссылка придёт сюда, как только заработает"))
        _sync_tunnel(True, hold=False)
        return
    code = engine.web.auth.open_window()
    text = _links_text(engine.web.port, url, None) + "\n\n" + engine.web.auth.code_text(code)
    await _send_links_to_owner(text, url)
