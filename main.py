"""
main.py — процесс юзербота FRX. Запускается ТОЛЬКО через frx_systemkeeper.py:

    python frx_systemkeeper.py

Запускает:
  1. Ядро юзербота (Telethon) — core.engine.Core
  2. Параллельно, если задан токен — стационарного бота на AIOgram 3

Первичный вход — через консоль (так надёжнее, если юзербот стоит на внешнем сервере):
нет user/config.json / api_id / api_hash — спрашиваем их в терминале (load_config()),
а номер телефона, код и пароль 2FA спрашивает Telethon (Core.start()). Локальный сайт
управления (команда `.web`, core/web/, modules/builtin/Web.py) доступен уже после входа.

Связь с frx_systemkeeper.py (подробно — в его докстринге):
  - код выхода 0   — обычная остановка (keeper тоже завершается);
  - код выхода 42  — перезагрузка;
  - код выхода 43  — применить заявку на обновление/восстановление бэкапа;
  - код выхода 78  — ошибка конфигурации (перезапускать бессмысленно);
  - когда ядро и стационарный бот подняты, main.py пишет user/data/keeper/ready.json
    (путь передаёт keeper через переменную окружения FRX_KEEPER_READY).
Остановку по запросу модуля (engine.request_shutdown(), команды .restart/.update)
main.py выполняет корректно: закрывает бота и отключает всех Telethon-клиентов.
"""

import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path

from core.engine import Core, FRX_VERSION
from core import paths
from aiobot.bot import create_bot

CONFIG_PATH = paths.CONFIG_PATH

EXIT_NO_RETRY = 78


def _config_error(message: str):
    print(message)
    raise SystemExit(EXIT_NO_RETRY)


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        _config_error("[FRX] Нет консольного ввода (stdin закрыт) — первичный вход нужно выполнить в терминале.")
        return ''  # недостижимо


async def load_config() -> dict:
    """
    Читает user/config.json (создаёт с заготовкой, если его нет). Если не заданы api_id/api_hash —
    спрашивает их в консоли (получить: https://my.telegram.org) и сохраняет в user/config.json.
    Вход в Telegram (номер/код/2FA) выполняет Core.start() — тоже в консоли.
    """
    default = {
        "api_id": 0,
        "api_hash": "",
        "aiogram_token": "",
        "prefix": ".",
    }

    if not CONFIG_PATH.exists():
        config = dict(default)
    else:
        config = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
        for k, v in default.items():
            config.setdefault(k, v)

    if not config.get('api_id') or not config.get('api_hash'):
        print("[FRX] Первичная настройка. api_id и api_hash берутся на https://my.telegram.org (API development tools).")
        while not config.get('api_id'):
            raw = _ask("[FRX] api_id: ")
            if raw.isdigit() and int(raw) > 0:
                config['api_id'] = int(raw)
            else:
                print("[FRX] api_id — это число, попробуйте ещё раз")
        while not config.get('api_hash'):
            raw = _ask("[FRX] api_hash: ")
            if len(raw) >= 16 and raw.isalnum():
                config['api_hash'] = raw
            else:
                print("[FRX] api_hash — строка из букв и цифр (32 символа), попробуйте ещё раз")
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding='utf-8')
        print(f"[FRX] {paths.display(CONFIG_PATH)} сохранён.")

    return config


def _notify_keeper_ready():
    """Сообщает keeper'у, что ядро и стационарный бот подняты (см. frx_systemkeeper.py)."""
    path = os.environ.get('FRX_KEEPER_READY')
    if not path:
        return
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(
            json.dumps({'pid': os.getpid(), 'version': FRX_VERSION, 'time': time.time()}),
            encoding='utf-8',
        )
    except OSError as e:
        print(f"[FRX] Не удалось записать ready-файл keeper'а: {e}")


async def _shutdown(core: Core, work, bot):
    """Корректная остановка: задачи -> aiogram-бот -> все Telethon-клиенты. Идемпотентна."""
    global _STOPPING
    _STOPPING = True
    if work is not None and not work.done():
        work.cancel()
        try:
            await work
        except BaseException:
            pass

    # при перезагрузке (.restart / .update, коды 42/43) сайт НЕ выключается — keeper держит его порт
    # заглушкой «юзербот спит», а новый процесс вернёт сайт. Слушатель закрываем в самом конце (после отключения
    # клиентов), чтобы страница в браузере жила как можно дольше.
    try:
        core.web.stop_wake_watch()
    except Exception:
        pass
    keep_web = False
    if core.exit_code in (42, 43):
        try:
            keep_web = await core.web.suspend()
        except Exception:
            keep_web = False
    if not keep_web:
        try:
            await core.web.stop('остановка юзербота')     # локальный сайт
        except Exception:
            pass

    if bot is not None and core.exit_code not in (42, 43):
        # юзербот выключается насовсем (.exit, Ctrl+C) — туннель умрёт вместе с keeper'ом, кнопка «Сайт» в боте
        # вела бы в никуда: возвращаем обычную кнопку меню (при следующем запуске модуль Web поставит её снова)
        try:
            if await core.db.get('web_panel:menu_button_url') and core._owner_id:
                from aiogram.types import MenuButtonDefault
                await asyncio.wait_for(bot.set_chat_menu_button(chat_id=core._owner_id, menu_button=MenuButtonDefault()), 8)
                await core.db.set('web_panel:menu_button_url', None)
        except Exception:
            pass
    if bot is not None:
        try:
            await asyncio.wait_for(bot.session.close(), 10)
        except Exception:
            pass

    clients = {}
    if core.client is not None:
        clients[id(core.client)] = core.client
    for client in list(core.accounts.clients.values()):
        clients[id(client)] = client
    for client in clients.values():
        try:
            result = client.disconnect()
            if inspect.isawaitable(result):
                await asyncio.wait_for(result, 10)
        except Exception:
            pass

    try:
        await core.llm.close()                # HTTP-сессия нейросетей
    except Exception:
        pass

    if keep_web:
        try:
            await core.web.stop('перезагрузка юзербота')
        except Exception:
            pass


EXIT_HIBERNATE = 79        # нет связи с Telegram — keeper делает копию и ждёт сеть (frx_systemkeeper.py)
_NET_ERRORS = (ConnectionError, OSError, asyncio.TimeoutError, TimeoutError)


async def _tg_reachable(timeout: float = 4.0) -> bool:
    """TCP-рукопожатие с серверами Telegram — отличает «нет сети» от обычного переподключения."""
    for host, port in (('149.154.167.51', 443), ('149.154.175.53', 443), ('api.telegram.org', 443)):
        try:
            _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
            w.close()
            return True
        except Exception:
            continue
    return False


async def _net_monitor(core, config):
    """
    если клиент Telegram отключён дольше `hibernate_after_seconds` (user/config.json, по умолчанию 30) и серверы Telegram
    недоступны — просим keeper уйти в гибернацию (резервная копия, юзербот выключен, ожидание сети).
    """
    try:
        grace = max(10.0, float(config.get('hibernate_after_seconds', 30)))
    except (TypeError, ValueError):
        grace = 30.0
    down = None
    while True:
        await asyncio.sleep(3)
        client = core.client
        try:
            ok = client is None or client.is_connected()
        except Exception:
            ok = False
        if ok:
            down = None
            continue
        if down is None:
            down = time.monotonic()
            continue
        if time.monotonic() - down >= grace:
            if await _tg_reachable():
                down = None                     # сеть есть — Telethon просто переподключается
                continue
            print(f"[FRX] Нет связи с Telegram дольше {grace:g} с — ухожу в гибернацию (keeper сделает копию и будет проверять сеть)")
            core.request_shutdown(EXIT_HIBERNATE)
            return


_STOPPING = False          # идёт остановка — обрывы соединений при ней ожидаемы


def _install_loop_handler(loop):
    """
    Windows (Proactor) при закрытии сокета, который собеседник уже оборвал, пишет в консоль
    «Exception in callback _ProactorBasePipeTransport._call_connection_lost() … ConnectionResetError: [WinError 10054]».
    Это безвредный шум (соединение и так закрыто — например, браузер закрыл вкладку сайта или Telegram оборвал сокет),
    поэтому именно такие случаи глушим; любые другие ошибки event loop показываются как обычно.
    """
    previous = loop.get_exception_handler()

    def handler(lp, context):
        exc = context.get('exception')
        text = f"{context.get('message', '')} {context.get('handle', '')}"
        reset = isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)) or \
            (isinstance(exc, OSError) and getattr(exc, 'winerror', None) in (10054, 10053, 10058))
        if reset and ('connection_lost' in text or '_ProactorBase' in text or '_SelectorSocketTransport' in text
                      or 'SSL' in text or 'ssl' in text):
            return
        if _STOPPING and exc is not None and (isinstance(exc, (ConnectionError, asyncio.CancelledError)) or type(exc).__name__ in (
                'ServerDisconnectedError', 'ClientConnectionError', 'ClientOSError', 'TelegramNetworkError', 'ClientConnectorError')):
            return
        if previous is not None:
            previous(lp, context)
        else:
            lp.default_exception_handler(context)

    loop.set_exception_handler(handler)


async def main() -> int:
    _install_loop_handler(asyncio.get_running_loop())
    config = await load_config()
    print(f"[FRX] Версия юзербота: {FRX_VERSION}")
    if paths.LEGACY:
        print("[FRX] Данные пока лежат по-старому (config.json, data/, modules/loaded/ в корне). "
              "Перезапустите frx_systemkeeper.py — он перенесёт их в папку user/.")
    core = Core(config)
    try:
        await core.start()
    except _NET_ERRORS as e:
        if await _tg_reachable():
            raise
        print(f"[FRX] Нет связи с Telegram при запуске ({type(e).__name__}) — гибернация")
        return EXIT_HIBERNATE

    tasks = [asyncio.create_task(core.client.run_until_disconnected()), asyncio.create_task(_net_monitor(core, config))]
    bot = None

    if config.get('aiogram_token'):
        # create_bot() сама вызывает core.set_bot(bot, dp, ...) внутри —
        # это даёт Core доступ к aiogram Bot+Dispatcher (без этого
        # engine.bot.*/create_inlineform() и модульные bot.*-хэндлеры
        # юзербота, core/bot_decorators.py, недоступны), а также запускает
        # bot.on_start()/bot.cycle() у уже загруженных модулей — см.
        # aiobot/bot.py create_bot() и core/engine.py Core.set_bot().
        bot, dp = create_bot(config['aiogram_token'], core=core)
        print("[FRX] Стационарный AIOgram-бот запущен")
        tasks.append(asyncio.create_task(dp.start_polling(bot)))
    else:
        print("[FRX] aiogram_token не задан — стационарный бот не запущен (инлайн-формы недоступны)")

    _notify_keeper_ready()

    if os.environ.get('FRX_SETUP_ONLY') == '1':
        # установщик (frx_setup.py, docker compose run -e FRX_SETUP_ONLY=1): нужен был только первый вход —
        # даём модулям доделать запуск (бот, инлайн-режим) и выходим кодом 0
        print("[FRX] Первичная настройка выполнена: вход в аккаунт есть. Юзербот остановится через несколько секунд.")
        asyncio.get_running_loop().call_later(10, core.request_shutdown, 0)

    # Как и раньше, процесс живёт, пока не завершатся ВСЕ задачи (gather), —
    # но теперь ещё и до запроса остановки от модуля (core.request_shutdown()).
    work = asyncio.gather(*tasks)
    stop_wait = asyncio.create_task(core.wait_shutdown())
    try:
        await asyncio.wait({work, stop_wait}, return_when=asyncio.FIRST_COMPLETED)
        if stop_wait.done():
            print(f"[FRX] Остановка по запросу (код {core.exit_code})")
            code = core.exit_code
        else:
            try:
                work.result()   # ошибка задачи пробрасывается, как и раньше
            except _NET_ERRORS:
                if await _tg_reachable():
                    raise
                print("[FRX] Соединение с Telegram потеряно — гибернация")
                core.exit_code = EXIT_HIBERNATE
            code = core.exit_code if core.exit_code == EXIT_HIBERNATE else 0
    finally:
        stop_wait.cancel()
        await _shutdown(core, work, bot)
    return code


if __name__ == '__main__':
    if os.environ.get('FRX_KEEPER') != '1':
        print("[FRX] main.py напрямую не запускается. Запустите: python frx_systemkeeper.py")
        sys.exit(EXIT_NO_RETRY)
    from core import logs                    # метка времени «мм.дд чч:мм:сс» в терминале + буфер для вкладки «логи»
    logs.install()
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        print("\n[FRX] Остановлено пользователем")
        sys.exit(0)
