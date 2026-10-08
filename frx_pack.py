"""
frx_pack.py — «свернуть» FRX: вернуть папку проекта к состоянию чистого релиза перед публикацией (git, zip).

Запуск: pack.bat (Windows) / sh pack.sh (Linux, macOS) / python frx_pack.py — из корня проекта.

Что делает (по шагам, ничего не удаляет без подтверждения):
    1. Проверяет, что юзербот из этой папки не запущен (keeper.pid).
    2. Ищет в КОДЕ проекта личное: ваши api_id / api_hash, токены, сессии, номера телефонов, id и @username
       аккаунтов — значения берутся из вашей же папки user/ (пока она есть), плюс типовые шаблоны секретов
       (токен бота, StringSession, ключи API). Если что-то нашлось — покажет файл и строку.
    3. Если проект — git-репозиторий, проверяет, не попало ли личное в git (user/, *.session, config.json…).
    4. Удаляет всё личное и мусор: папку user/ (config.json, data/ — БД, сессии, аккаунты, бэкапы;
       ваши модули и ассеты), старые config.json / data/ / modules/loaded/ в корне, *.session,
       __pycache__, *.pyc, логи.
    5. Пересоздаёт frx_manifest.json — список файлов релиза (по нему обновление понимает, какие файлы
       старой версии удалить).
    6. С --zip собирает dist/FRX_<версия>.zip — архив для `.update` (корень архива — FRX/).

Ваши правки кода (core/, modules/builtin/, README…) остаются — сворачиваются только личные данные.
При следующем запуске FRX снова развернётся сам: создаст user/, спросит api_id / api_hash и войдёт в аккаунт.

Ключи:
    --dry-run   только показать, что будет удалено и что найдено, ничего не менять
    --yes       не спрашивать подтверждение
    --zip       после сворачивания собрать dist/FRX_<версия>.zip
    --force     сворачивать, даже если юзербот, похоже, запущен

Коды выхода: 0 — готово и чисто; 2 — свернуто, но в коде или git найдено личное (исправьте перед публикацией);
1 — отменено или ошибка.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True                  # не оставлять __pycache__ от самого сворачивания
import frx_systemkeeper as keeper  # noqa: E402  (только стандартная библиотека, core/ не импортируется)

TEXT_EXT = ('.py', '.md', '.txt', '.json', '.js', '.css', '.html', '.toml', '.cfg', '.ini', '.yml', '.yaml',
            '.bat', '.sh', '.env', '.example')
SKIP_DIRS = {'.git', '.hg', '.svn', '.venv', 'venv', 'env', 'node_modules'}
SECRET_KEYS = ('token', 'hash', 'key', 'secret', 'password', 'passwd', 'session', 'phone', 'auth', 'cookie')

PATTERNS = [
    (re.compile(r'\b\d{8,10}:[A-Za-z0-9_-]{35}\b'), 'токен Telegram-бота'),
    (re.compile(r'\b1[A-Za-z0-9_-]{340,}={0,2}'), 'строка сессии Telethon (StringSession)'),
    (re.compile(r'\bsk-(?:proj-|ant-)?[A-Za-z0-9_-]{20,}'), 'ключ API (sk-…)'),
    (re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), 'ключ Google API'),
    (re.compile(r'\bgh[pousr]_[A-Za-z0-9]{36,}\b'), 'токен GitHub'),
    (re.compile(r'''api_hash['"]?\s*[:=]\s*['"][0-9a-f]{32}['"]''', re.I), 'api_hash'),
]
# номера телефонов, id и @username ищутся точно — по значениям из вашей user/ (personal_literals), а не шаблоном:
# в коде много образцов вида +79991234567


def say(text=''):
    print(text, flush=True)


# ----------------------------------------------------------------------------- личные значения

def _walk_values(obj, key=''):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_values(v, str(k))
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_values(v, key)
    else:
        yield key.lower(), obj


def personal_literals() -> dict:
    """{значение: откуда} — то, чего не должно быть в коде: берётся из конфига и аккаунтов пользователя."""
    out = {}

    def add(value, why):
        s = str(value).strip()
        if isinstance(value, bool) or len(s) < 5 or s.lower() in ('none', 'null', 'true', 'false'):
            return
        if s.isdigit() and len(s) < 6:
            return
        out.setdefault(s, why)

    cfg = keeper.read_json(keeper.CONFIG_PATH) or {}
    for key, value in _walk_values(cfg):
        if key in ('api_id', 'owner_id', 'owner') or any(w in key for w in SECRET_KEYS):
            if isinstance(value, (str, int)):
                add(value, f'config.json → {key}')
    accounts = keeper.read_json(keeper.DATA_DIR / 'accounts.json') if keeper.DATA_DIR else None
    for key, value in _walk_values(accounts or {}):
        if key in ('id', 'user_id', 'username', 'phone') or 'session' in key:
            if isinstance(value, (str, int)):
                add(value, f'accounts.json → {key}')
    return out


def scan_code(literals: dict) -> list:
    """[(rel, line_no, что)] — подозрительные места в файлах кода."""
    found = []
    for rel, path in sorted(keeper.scan_code_files(ROOT).items()):
        if not rel.lower().endswith(TEXT_EXT) and '.' in path.name:
            continue
        try:
            if path.stat().st_size > 3 * 1024 * 1024:
                continue
            text = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            continue
        for no, line in enumerate(text.splitlines(), 1):
            for value, why in literals.items():
                if value in line and re.search(r'(?<![\w@])' + re.escape(value) + r'(?!\w)', line):
                    found.append((rel, no, f'ваше значение из {why}'))
            for rx, why in PATTERNS:
                if rx.search(line):
                    found.append((rel, no, why))
    return found


def git_tracked_personal() -> list:
    """Файлы, которые git уже отслеживает, хотя это не код (user/, сессии, config.json…)."""
    if not (ROOT / '.git').exists() or not shutil.which('git'):
        return []
    try:
        r = subprocess.run(['git', '-C', str(ROOT), 'ls-files', '-z'], capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return []
    if r.returncode != 0:
        return []
    names = [n for n in r.stdout.decode('utf-8', 'replace').split('\0') if n]
    return [n for n in names if not keeper.is_code_rel(n) and n != keeper.MANIFEST_NAME
            and not keeper._is_junk(n) or n.endswith(('.session', '.session-journal'))]


# ----------------------------------------------------------------------------- что удаляется

def targets() -> list:
    """[(Path, описание)] — всё личное и мусор внутри проекта."""
    out, seen = [], set()

    def add(p: Path, why: str):
        p = Path(p)
        if p.exists() and p not in seen and (p == ROOT or ROOT in p.resolve().parents):
            seen.add(p)
            out.append((p, why))

    user = keeper._user_dir().resolve()
    if ROOT in user.parents:
        add(user, 'папка пользователя: config.json, data/ (БД, сессии, аккаунты, бэкапы), ваши модули и ассеты')
    add(ROOT / 'config.json', 'конфиг старой раскладки')
    add(ROOT / 'data', 'данные старой раскладки')
    add(ROOT / 'modules' / 'loaded', 'модули старой раскладки')
    add(ROOT / 'modules' / 'unloaded', 'выгруженные модули старой раскладки')
    for dirpath, dirnames, filenames in os.walk(ROOT):
        d = Path(dirpath)
        dirnames[:] = [n for n in dirnames if n not in SKIP_DIRS and (d / n) not in seen]
        for n in list(dirnames):
            if n in ('__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'):
                add(d / n, 'кэш Python')
                dirnames.remove(n)
        for n in filenames:
            if n.endswith(('.session', '.session-journal')):
                add(d / n, 'сессия Telegram!')
            elif n.endswith(('.pyc', '.pyo', '.log')) or n in ('.DS_Store', 'Thumbs.db', 'desktop.ini'):
                add(d / n, 'мусор')
    return out


def _remove(p: Path):
    if p.is_dir() and not p.is_symlink():
        keeper.rmtree(p)
    else:
        try:
            p.unlink()
        except PermissionError:
            os.chmod(p, 0o700)
            p.unlink()


def build_zip() -> Path:
    files = keeper.scan_code_files(ROOT)
    version = keeper.read_engine_version(ROOT) or '0'
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    dst = dist / f"FRX_{keeper._safe_ver(version)}.zip"
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel, f in sorted(files.items()):
            zf.write(f, 'FRX/' + rel)
        zf.write(ROOT / keeper.MANIFEST_NAME, 'FRX/' + keeper.MANIFEST_NAME)
    return dst


def _rel(p: Path) -> str:
    try:
        return p.relative_to(ROOT).as_posix() + ('/' if p.is_dir() else '')
    except ValueError:
        return str(p)


def main() -> int:
    ap = argparse.ArgumentParser(description='Свернуть FRX: удалить личные данные перед публикацией')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--zip', action='store_true')
    ap.add_argument('--force', action='store_true')
    a = ap.parse_args()
    os.chdir(ROOT)

    version = keeper.read_engine_version(ROOT) or '?'
    say(f"FRX {version} — сворачивание перед публикацией\nПапка: {ROOT}\n")

    pid = keeper.running_keeper_pid()
    if pid and not a.force:
        say(f"Юзербот из этой папки, похоже, запущен (процесс {pid}). Остановите его (.exit в Telegram или Ctrl+C "
            f"в окне keeper'а) и запустите сворачивание снова. Если процесс не FRX — добавьте --force.")
        return 1

    say("1. Ищу личное в коде проекта…")
    found = scan_code(personal_literals())
    if found:
        say(f"   НАЙДЕНО {len(found)} — исправьте перед публикацией:")
        for rel, no, why in found[:60]:
            say(f"     {rel}:{no} — {why}")
        if len(found) > 60:
            say(f"     … и ещё {len(found) - 60}")
    else:
        say("   чисто")

    tracked = git_tracked_personal()
    if tracked:
        say(f"\n   git уже отслеживает личное ({len(tracked)}): после сворачивания выполните "
            f"`git rm -r --cached <путь>`, а если это уже запушено — смените ключи и сессии:")
        for n in tracked[:30]:
            say(f"     {n}")

    items = targets()
    say("\n2. Будет удалено:" if items else "\n2. Удалять нечего — личных данных и мусора в папке нет.")
    for p, why in items:
        say(f"     {_rel(p):<40} {why}")
    if keeper._user_dir().resolve() != ROOT and ROOT not in keeper._user_dir().resolve().parents:
        say(f"   (папка данных FRX_USER_DIR={keeper._user_dir()} — вне проекта, её не трогаю)")

    if a.dry_run:
        say("\n--dry-run: ничего не изменено.")
        return 2 if (found or tracked) else 0

    if items and not a.yes:
        say("\nЭто необратимо: сессии, база, настройки и ваши модули будут удалены. Нужное сохраните заранее "
            "(.createbackup).")
        try:
            ans = input("Введите  да  чтобы свернуть: ").strip().lower()
        except EOFError:
            ans = ''
        if ans not in ('да', 'yes', 'y', 'д'):
            say("Отменено — ничего не удалено.")
            return 1

    errors = []
    for p, _why in items:
        try:
            _remove(p)
        except OSError as e:
            errors.append(f"{_rel(p)}: {e}")
    man = keeper.write_manifest(ROOT)
    say(f"\n3. frx_manifest.json обновлён: файлов кода — {len(man['files'])}, версия {man['frx_version']}.")
    if a.zip:
        z = build_zip()
        say(f"4. Архив для .update: {_rel(z)} ({z.stat().st_size // 1024} КБ)")

    if errors:
        say("\nНе удалось удалить (файл занят? закройте программы, которые его держат, и повторите):")
        for e in errors:
            say(f"     {e}")
        return 1
    if found or tracked:
        say("\nПроект свёрнут, но в коде или git осталось личное (см. выше) — не публикуйте, пока не исправите.")
        return 2
    say("\nГотово: проект в состоянии чистого релиза — можно коммитить и публиковать.\n"
        "Следующий запуск (python frx_systemkeeper.py) развернёт FRX заново.")
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nОтменено.")
        sys.exit(1)
