"""
HikkaCompat — модули Hikka и Heroku внутри FRX.

Запускает «пространство Hikka»: свой префикс команд (по умолчанию «,»), свои команды (,help ,dlm ,lm ,ulm ,config
,setprefix …), своя БД и папка модулей (user/data/hikka/). Модули Hikka работают на том же аккаунте Telegram, что и
FRX, а инлайн-формы, галереи и кнопки — через стационарного бота FRX. Ядро FRX не меняется: всё, что нужно модулям
Hikka, лежит в папке hikka_compat/ в корне проекта (эмуляция пакетов hikka/heroku, hikkatl/herokutl → telethon).

Команды FRX этого модуля:
  .hikka                   состояние пространства
  .hikka on | off          включить / выключить пространство (запоминается)
  .hikka restart           перезапустить (перечитать модули с диска)
  .hikka prefix <символ>   префикс команд Hikka
  .hikka mods              установленные модули Hikka
  .hikka errors            какие модули не загрузились и почему

В WebIDE Sandbox модуль запускается так же: пространство Hikka поднимается внутри песочницы (своя БД и папка
модулей — user/data/sandbox/hikka/), команды с префиксом Hikka пишутся в чат песочницы, формы рисуются на сайте.
Основное пространство FRX песочница не трогает.
"""

import asyncio
import contextlib
import html
import sys
from pathlib import Path

import bot
import decorators
import engine
import visual

NAME = "HikkaCompat"
MODULE_NAME = "hikkacompat"
VERSION = "1.0"
AUTHOR = "FRX"
DESCRIPTION = "Модули Hikka / Heroku в FRX: своё пространство со своим префиксом, командами и инлайн-формами"

try:
    from core.paths import DATA_DIR, PROJECT_DIR
except Exception:                                    # старые раскладки
    DATA_DIR, PROJECT_DIR = Path("user/data"), Path.cwd()

COMPAT_DIR = Path(PROJECT_DIR) / "hikka_compat"
_state = {"space": None, "task": None, "error": None}
_aio = sys.modules["asyncio"]           # настоящий asyncio (в Sandbox `import asyncio` — виртуальные часы)


def _sandbox_session():
    """Сессия WebIDE Sandbox, если модуль запущен в песочнице."""
    return getattr(engine, "s", None) if type(engine).__name__ == "SandboxEngine" else None


def _esc(x):
    return html.escape(str(x), quote=False)


async def _import_runtime():
    """Подключить папку hikka_compat/ и вернуть модули пространства."""
    path = str(COMPAT_DIR.resolve())
    if path not in sys.path:
        sys.path.insert(0, path)
    from hikka import _frx

    session = _sandbox_session()
    if session is not None:                          # своё пространство песочницы: свой контекст, БД и модули
        from hikka import _sandbox

        await _sandbox.enter(engine, session, Path(DATA_DIR) / "sandbox" / "hikka",
                             Path(DATA_DIR) / "cache" / "hikka-sandbox")
    else:
        _frx.configure(Path(DATA_DIR) / "hikka", Path(DATA_DIR) / "cache" / "hikka")
        _frx.engine = engine
    from hikka._space import Space

    return _frx, Space


def _purge_runtime():
    """Выгрузить код пространства из памяти (чтобы после обновления FRX загрузился новый).
    Пока работает пространство в Sandbox, код нужен ему — тогда ничего не трогаем."""
    frx = sys.modules.get("hikka._frx")
    if frx is not None and frx.prune():
        return
    for name in [n for n in sys.modules if n == "hikka" or n.startswith("hikka.")]:
        sys.modules.pop(name, None)


async def _start(reason: str = ""):
    if _state["space"] is not None and _state["space"].started:
        return _state["space"]
    try:
        _frx, Space = await _import_runtime()
        space = Space(engine)
        _state["space"] = space
        await space.start()
        await space.attach_bot()
        _state["error"] = None
        return space
    except Exception as e:
        _state["error"] = f"{type(e).__name__}: {e}"
        engine.tlog(f"[hikka] не запустилось ({reason}): {_state['error']}")
        with contextlib.suppress(Exception):
            if _state["space"] is not None:
                await _state["space"].stop()
        _state["space"] = None
        raise


async def _stop():
    space = _state["space"]
    _state["space"] = None
    if space is not None:
        with contextlib.suppress(Exception):
            await space.stop()


def _spawn(coro):
    session = _sandbox_session()
    if session is not None:                          # задачи песочницы видны в её журнале
        task = session.spawn(coro, "hikka")
        _state["task"] = task
        return task
    task = asyncio.ensure_future(coro)
    _state["task"] = task

    def done(t):
        if not t.cancelled() and t.exception() is not None:
            engine.tlog(f"[hikka] {type(t.exception()).__name__}: {t.exception()}")

    task.add_done_callback(done)
    return task


# --------------------------------------------------------------------------- #
# жизненный цикл
# --------------------------------------------------------------------------- #

@decorators.on_start()
async def setup():
    await engine.create_config(key='autostart', description='Запускать пространство Hikka вместе с FRX',
                               data_type='bool', default=True)
    if await engine.get_config('autostart'):
        _spawn(_start("запуск FRX"))


@bot.on_start()
async def attach_bot():
    """Стационарный бот поднят — подключить к нему инлайн Hikka (формы, галереи, кнопки)."""
    async def wait_and_attach():
        for _ in range(240):
            space = _state["space"]
            if space is not None and space.started:
                await space.attach_bot()
                return
            if space is None and _state["task"] is not None and _state["task"].done():
                return
            await _aio.sleep(0.5)

    _spawn(wait_and_attach())


@decorators.on_account_switch()
async def account_switched(number):
    """Аккаунт сменился — пространство переезжает на новый клиент."""
    if _state["space"] is None:
        return
    await _stop()
    _spawn(_start("смена аккаунта"))


@decorators.on_unload()
async def teardown():
    task = _state["task"]
    if task is not None and not task.done():
        task.cancel()
    await _stop()
    if _sandbox_session() is not None:
        with contextlib.suppress(Exception):
            from hikka import _sandbox

            await _sandbox.leave()
        return
    with contextlib.suppress(Exception):
        from hikka import _aliases

        _aliases.uninstall()
    _purge_runtime()


# --------------------------------------------------------------------------- #
# команды FRX
# --------------------------------------------------------------------------- #

def _uptime(sec):
    sec = int(sec)
    d, h, m = sec // 86400, sec % 86400 // 3600, sec % 3600 // 60
    return " ".join(x for x in (f"{d} д" if d else "", f"{h} ч" if h else "", f"{m} мин" if m else "") if x) or "меньше минуты"


def _status_text() -> str:
    space = _state["space"]
    sandbox = _sandbox_session() is not None
    lines = [visual.title("Hikka в FRX", "пространство песочницы: своя БД и модули" if sandbox
                          else "модули Hikka и Heroku в отдельном пространстве")]
    if space is None or not space.started:
        starting = _state["task"] is not None and not _state["task"].done()
        lines.append(visual.wait("запускается…") if starting else visual.warn("пространство выключено"))
        if _state["error"]:
            lines.append(visual.err("ошибка запуска") + "\n" + visual.hint(_esc(_state["error"])))
        lines += ["", visual.hint("включить — <code>.hikka on</code>")]
        return "\n".join(lines)
    st = space.status()
    lines += [
        visual.field("префикс команд", _esc(st["prefix"])),
        visual.field("модулей", f"{st['modules']} (+{st['core']} встроенных)", code=False),
        visual.field("команд", st["commands"], code=False),
        visual.field("инлайн-бот", f"@{st['bot']}" if st["inline"] else "не подключён", code=False),
        visual.field("работает", _uptime(st["uptime"]), code=False),
    ]
    if st["failed"]:
        lines.append(visual.warn(f"не загрузились: {len(st['failed'])} — <code>.hikka errors</code>"))
    p = _esc(st["prefix"])
    lines += [
        "",
        visual.section("Команды пространства"),
        visual.item(f"<code>{p}help</code> — модули и команды"),
        visual.item(f"<code>{p}dlm &lt;название | ссылка&gt;</code> — установить модуль"),
        visual.item(f"<code>{p}lm</code> ответом на .py — загрузить из файла"),
        visual.item(f"<code>{p}ulm &lt;модуль&gt;</code> — выгрузить"),
        visual.item(f"<code>{p}config</code> — настройки модулей"),
        "",
        visual.hint("<code>.hikka on | off | restart | prefix &lt;символ&gt; | mods | errors</code>"),
    ]
    return "\n".join(lines)


@decorators.command('hikka', 'hk', 'heroku')
async def hikka_cmd(event):
    """пространство Hikka: [on | off | restart | prefix <символ> | mods | errors]"""
    args = engine.get_args().strip().split(maxsplit=1)
    action = args[0].lower() if args else ""
    rest = args[1].strip() if len(args) > 1 else ""

    if action in ("", "status", "info"):
        await engine.answer(event, _status_text())
        return

    if action in ("on", "start", "вкл"):
        await engine.edit_config('autostart', 'edit', True)
        await engine.answer(event, visual.wait("запускаю пространство Hikka…"))
        try:
            await _start("команда")
        except Exception as e:
            await engine.answer(event, visual.err("не запустилось") + "\n" + visual.hint(_esc(f"{type(e).__name__}: {e}")))
            return
        await engine.answer(event, _status_text())
        return

    if action in ("off", "stop", "выкл"):
        await engine.edit_config('autostart', 'edit', False)
        await _stop()
        await engine.answer(event, visual.ok("пространство Hikka выключено") + "\n"
                            + visual.hint("модули и настройки сохранены; включить — <code>.hikka on</code>"))
        return

    if action in ("restart", "reload", "rs"):
        await engine.answer(event, visual.wait("перезапускаю пространство Hikka…"))
        await _stop()
        try:
            await _start("перезапуск")
        except Exception as e:
            await engine.answer(event, visual.err("не запустилось") + "\n" + visual.hint(_esc(f"{type(e).__name__}: {e}")))
            return
        await engine.answer(event, _status_text())
        return

    space = _state["space"]
    if space is None or not space.started:
        await engine.answer(event, visual.warn("пространство Hikka выключено") + "\n"
                            + visual.hint("включить — <code>.hikka on</code>"))
        return

    if action in ("prefix", "префикс"):
        if not rest or len(rest) > 2 or " " in rest:
            await engine.answer(event, visual.field("префикс команд Hikka", _esc(space.prefix)) + "\n"
                                + visual.hint("сменить — <code>.hikka prefix ,</code> (1–2 символа)"))
            return
        space.db.set("hikka.main", "command_prefix", rest)
        text = visual.ok(f"префикс команд Hikka: <code>{_esc(rest)}</code>")
        if rest == await engine.get_prefix():
            text += "\n" + visual.warn("совпадает с префиксом FRX — одинаковые команды сработают дважды")
        await engine.answer(event, text)
        return

    if action in ("mods", "modules", "list"):
        mods = [m for m in space.modules.modules if not str(m.__origin__).startswith("<core")]
        lines = [visual.title("Модули Hikka", f"установлено: {len(mods)}")]
        for m in sorted(mods, key=lambda x: str(x.name).lower()):
            cmds = ", ".join(list(m.commands)[:8]) or "—"
            lines.append(visual.item(f"<b>{_esc(m.name)}</b>  " + visual.hint(_esc(cmds))))
        if not mods:
            lines.append(visual.hint(f"пока пусто — <code>{_esc(space.prefix)}dlm &lt;название&gt;</code>"))
        await engine.answer(event, "\n".join(lines))
        return

    if action in ("errors", "failed", "ошибки"):
        failed = space.failed
        if not failed:
            await engine.answer(event, visual.ok("все модули Hikka загрузились"))
            return
        lines = [visual.title("Не загрузились", f"{len(failed)} шт.")]
        for name, why in failed.items():
            lines.append(visual.item(f"<b>{_esc(name)}</b>") + "\n" + visual.item(_esc(why[:300]), 1))
        await engine.answer(event, "\n".join(lines))
        return

    await engine.answer(event, visual.err("неизвестное действие") + "\n"
                        + visual.hint("<code>.hikka on | off | restart | prefix &lt;символ&gt; | mods | errors</code>"))
