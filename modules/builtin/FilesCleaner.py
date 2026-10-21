"""
FilesCleaner — уборка файлов самого FRX: кэш, скачанное, временное, логи, старые бэкапы.

  .fclean           панель: сколько места занимает каждая категория + кнопки очистки
  .fclean all       очистить всё безопасное сразу (кэш, временное, старые скачанные файлы, загрузки сайта)
  .fclean <категория>   очистить одну: cache, files, tmp, uploads, botchat, logs, backups, pycache, bak

Что относится к категориям (всё лежит в папке FRX):
  cache     user/data/cache — шрифты, эмодзи, превью, кэш Hikka; скачаются заново при нужде
  files     user/data/files — файлы, скачанные модулями; удаляются те, что старше `files_max_age_days` дней
  tmp       user/data/keeper/{tmp,staging,incoming} — остатки обновлений и распаковки (не трогается во время обновления)
  uploads   user/data/emulate_uploads — файлы, прикреплённые к командам на сайте (Emulate)
  botchat   user/data/botchat/media и avatars — кэш медиа переписки бота на сайте (история сообщений остаётся)
  logs      keeper.log, console.log, termux_boot.log — обнуляются (не удаляются: в них может писать keeper)
  backups   user/data/backup — остаётся по одной самой свежей копии каждого вида
  pycache   __pycache__ в папке проекта — Python пересоздаст при запуске
  bak       *.bak в папке модулей — старые копии модулей, ставших встроенными

Настройки (.config filescleaner): files_max_age_days, auto_clean (раз в сутки чистить кэш, временное и старые файлы).
Сессии, база данных, конфиги, модули и их данные модуль не трогает никогда.
"""

import asyncio
import html
import os
import shutil
import time
from pathlib import Path

import decorators
import engine
import visual
from core.paths import BACKUP_DIR, DATA_DIR, FILES_DIR, KEEPER_DIR, MODULES_DIR, PROJECT_DIR

NAME = "FilesCleaner"
MODULE_NAME = "filescleaner"
VERSION = "1.0"
AUTHOR = "FRX"
DESCRIPTION = "Уборка файлов FRX: кэш, скачанное, временное, логи, старые бэкапы"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/FilesCleaner.py"

DATA = Path(DATA_DIR)
ROOT = Path(PROJECT_DIR)

CATEGORIES = {
    # ключ: (название, пояснение, входит в «всё безопасное»)
    "cache": ("Кэш", "шрифты, эмодзи, превью — скачаются заново", True),
    "files": ("Скачанные файлы", "файлы, скачанные модулями", True),
    "tmp": ("Временные", "остатки обновлений", True),
    "uploads": ("Загрузки сайта", "файлы команд с сайта", True),
    "botchat": ("Медиа BotChat", "кэш картинок переписки бота", False),
    "logs": ("Логи", "обнуляются", False),
    "backups": ("Старые бэкапы", "остаётся по одной свежей копии", False),
    "pycache": ("__pycache__", "Python пересоздаст", False),
    "bak": ("*.bak модулей", "старые копии модулей", False),
}
SAFE = [k for k, v in CATEGORIES.items() if v[2]]


def _esc(x) -> str:
    return html.escape(str(x), quote=False)


def _size(n: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if n < 1024 or unit == "ГБ":
            return f"{n:.0f} {unit}" if unit == "Б" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} ГБ"


def _file_size(p: Path) -> int:
    try:
        return p.stat().st_size
    except OSError:
        return 0


def _walk_files(d: Path):
    if not d.is_dir():
        return
    for root, dirs, files in os.walk(d):
        for f in files:
            yield Path(root) / f


def _dir_size(d: Path) -> int:
    return sum(_file_size(p) for p in _walk_files(d))


def _remove(p: Path) -> int:
    """Удаляет файл или папку, возвращает освобождённые байты (то, что удалить не удалось, не считается)."""
    if p.is_dir() and not p.is_symlink():
        before = _dir_size(p)
        shutil.rmtree(p, ignore_errors=True)
        return before - (_dir_size(p) if p.exists() else 0)
    size = _file_size(p)
    try:
        p.unlink()
        return size
    except OSError:
        return 0


def _clear_dir_contents(d: Path) -> int:
    if not d.is_dir():
        return 0
    return sum(_remove(child) for child in list(d.iterdir()))


# --------------------------------------------------------------------------- #
# категории: что входит и как чистить
# --------------------------------------------------------------------------- #

def _log_files():
    return [p for p in (KEEPER_DIR / "keeper.log", KEEPER_DIR / "console.log", DATA / "termux_boot.log") if Path(p).is_file()]


def _update_in_progress() -> bool:
    return (Path(KEEPER_DIR) / "request.json").exists()


def _tmp_dirs():
    return [Path(KEEPER_DIR) / n for n in ("tmp", "staging", "incoming")]


def _backup_extra():
    """Все копии, кроме самой свежей каждого вида (вид — имя до даты: code_, userdata_, userdata_prerestore_…)."""
    d = Path(BACKUP_DIR)
    if not d.is_dir():
        return []
    groups = {}
    for f in d.glob("*.zip"):
        kind = f.name.split("_2", 1)[0]          # code_20261015-… → code
        groups.setdefault(kind, []).append(f)
    extra = []
    for files in groups.values():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        extra += files[1:]
    return extra


def _pycache_dirs():
    out = []
    for root, dirs, _files in os.walk(ROOT):
        rel = Path(root).relative_to(ROOT).parts
        if rel and rel[0] in (".venv", "venv", ".git", "user"):
            dirs[:] = []
            continue
        if "__pycache__" in dirs:
            out.append(Path(root) / "__pycache__")
            dirs.remove("__pycache__")
    return out


def _bak_files():
    d = Path(MODULES_DIR)
    return [p for p in d.glob("*.bak")] + [p for p in d.glob("*.py.bak")] if d.is_dir() else []


def _old_files(days: float):
    edge = time.time() - days * 86400
    return [p for p in _walk_files(Path(FILES_DIR)) if p.stat().st_mtime < edge]


def measure(days: float) -> dict:
    return {
        "cache": _dir_size(DATA / "cache"),
        "files": sum(_file_size(p) for p in _old_files(days)),
        "tmp": 0 if _update_in_progress() else sum(_dir_size(d) for d in _tmp_dirs()),
        "uploads": _dir_size(DATA / "emulate_uploads"),
        "botchat": _dir_size(DATA / "botchat" / "media") + _dir_size(DATA / "botchat" / "avatars"),
        "logs": sum(_file_size(p) for p in _log_files()),
        "backups": sum(_file_size(p) for p in _backup_extra()),
        "pycache": sum(_dir_size(d) for d in _pycache_dirs()),
        "bak": sum(_file_size(p) for p in _bak_files()),
    }


def clean(key: str, days: float) -> int:
    """Чистит категорию, возвращает освобождённые байты."""
    if key == "cache":
        return _clear_dir_contents(DATA / "cache")
    if key == "files":
        return sum(_remove(p) for p in _old_files(days))
    if key == "tmp":
        if _update_in_progress():
            return 0
        return sum(_clear_dir_contents(d) for d in _tmp_dirs())
    if key == "uploads":
        return _clear_dir_contents(DATA / "emulate_uploads")
    if key == "botchat":
        return _clear_dir_contents(DATA / "botchat" / "media") + _clear_dir_contents(DATA / "botchat" / "avatars")
    if key == "logs":
        freed = 0
        for p in _log_files():
            size = _file_size(p)
            try:
                with open(p, "w", encoding="utf-8"):
                    pass
                freed += size
            except OSError:
                pass
        return freed
    if key == "backups":
        return sum(_remove(p) for p in _backup_extra())
    if key == "pycache":
        return sum(_remove(d) for d in _pycache_dirs())
    if key == "bak":
        return sum(_remove(p) for p in _bak_files())
    raise KeyError(key)


async def _forget_missing_files():
    """Записи реестра файлов (engine.get_file / reply_file), чьих файлов больше нет на диске, — убрать."""
    reg = getattr(engine, "file_registry", None)
    if reg is None:
        return
    try:
        for key in await reg.storage.list_keys("core:files:"):
            obj = await reg.storage.get(key)
            if obj and not os.path.exists(obj.get("path", "")):
                await reg.storage.delete(key)
    except Exception as e:
        engine.tlog(f"[filescleaner] реестр файлов: {e}")


async def _days() -> float:
    try:
        return float(await engine.get_config("files_max_age_days") or 7)
    except Exception:
        return 7.0


async def run_clean(keys) -> int:
    days = await _days()
    freed = 0
    for k in keys:
        try:
            freed += await asyncio.to_thread(clean, k, days)
        except Exception as e:
            engine.tlog(f"[filescleaner] {k}: {e}")
    if "files" in keys:
        await _forget_missing_files()
    return freed


# --------------------------------------------------------------------------- #
# интерфейс
# --------------------------------------------------------------------------- #

async def _panel(freed=None):
    days = await _days()
    sizes = await asyncio.to_thread(measure, days)
    lines = [visual.title("Файлы FRX", sub="что можно убрать")]
    for k, (title, note, _safe) in CATEGORIES.items():
        label = title if k != "files" else f"{title} (старше {days:g} дн.)"
        lines.append(visual.field(_esc(label), _size(sizes[k])))
        lines.append(visual.item(f"<i>{_esc(note)}</i>", 1))
    total_safe = sum(sizes[k] for k in SAFE)
    lines += ["", visual.field("безопасно освободить", _size(total_safe))]
    if freed is not None:
        lines += ["", visual.ok(f"Освобождено {_size(freed)}")]
    lines += ["", visual.hint("сессии, база, конфиги и модули не трогаются")]

    buttons = [[(visual.btn(f"Всё безопасное · {_size(total_safe)}"), "fc:safe", "success")]]
    row = []
    for k, (title, _note, _safe) in CATEGORIES.items():
        if sizes[k] <= 0:
            continue
        row.append((f"{title} · {_size(sizes[k])}", f"fc:{k}", "danger" if k == "backups" else None))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    buttons.append([(visual.btn("Обновить"), "fc:refresh"), (visual.btn("Закрыть", "close"), "fc:close")])
    # кнопки без цвета — кортеж из двух элементов
    buttons = [[b if b[2:] and b[2] else b[:2] for b in r] for r in buttons]
    return "\n".join(lines), buttons


@decorators.on_start()
async def setup():
    await engine.create_config(key="files_max_age_days", description="Скачанные модулями файлы (user/data/files) "
                               "старше стольких дней считаются ненужными", data_type="int", default=7)
    await engine.create_config(key="auto_clean", description="Раз в сутки самому чистить кэш, временные файлы, "
                               "загрузки сайта и старые скачанные файлы", data_type="bool", default=False)


@decorators.cycle("24h")
async def auto_clean():
    try:
        if not await engine.get_config("auto_clean"):
            return
    except Exception:
        return
    freed = await run_clean(SAFE)
    if freed > 1024 * 1024:
        engine.tlog(f"[filescleaner] автоуборка: освобождено {_size(freed)}")


@decorators.command("fclean", "filescleaner", "cleanfiles")
async def fclean_cmd(event):
    """[all|категория] — уборка файлов FRX: кэш, скачанное, временное, логи, старые бэкапы"""
    arg = engine.get_args().strip().lower()
    if arg in ("all", "safe", "всё", "все"):
        freed = await run_clean(SAFE)
        await engine.answer(event, visual.ok(f"Освобождено {_size(freed)}") + "\n" +
                            visual.hint("кэш, временные файлы, загрузки сайта, старые скачанные файлы"))
        return
    if arg in CATEGORIES:
        freed = await run_clean([arg])
        await engine.answer(event, visual.ok(f"{CATEGORIES[arg][0]}: освобождено {_size(freed)}"))
        return
    if arg:
        await engine.answer(event, visual.err("Нет такой категории") + "\n" +
                            visual.field("есть", ", ".join(["all", *CATEGORIES])))
        return

    text, buttons = await _panel()
    try:
        form = await engine.create_inlineform(event.chat_id, text, buttons=buttons)
    except Exception:
        prefix = await engine.get_prefix()
        await engine.answer(event, text + "\n\n" + visual.hints(f"{prefix}fclean all — убрать всё безопасное", f"{prefix}fclean cache — только кэш"))
        return

    @form.on_click(data_startswith="fc:")
    async def _(click):
        what = click.data[3:]
        if what == "close":
            await click.answer()
            await form.delete()
            return
        if what == "refresh":
            await click.answer()
            t, b = await _panel()
            await form.edit(t, buttons=b)
            return
        await click.answer("Чищу…")
        freed = await run_clean(SAFE if what == "safe" else [what])
        t, b = await _panel(freed)
        await form.edit(t, buttons=b)
