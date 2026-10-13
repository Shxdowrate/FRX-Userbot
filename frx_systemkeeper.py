"""
frx_systemkeeper.py — «материнский» процесс FRX. Запускать нужно именно его:

    python frx_systemkeeper.py

а он уже сам запускает main.py (юзербот + стационарный бот) подпроцессом.
main.py напрямую не запускается (без keeper он сразу завершается с подсказкой).

Что делает keeper
-----------------
0. Перед первым запуском переносит пользовательские данные старой раскладки
   (config.json, data/, modules/loaded/ в корне проекта) в папку user/ —
   см. migrate_legacy_layout() и core/paths.py.
1. Запускает main.py и следит за ним (ввод/вывод терминала наследуется —
   первая авторизация по номеру телефона идёт прямо в консоли).
2. Перезагрузка: main.py завершается с кодом EXIT_RESTART (42) — keeper
   запускает его заново. Инициатор — модуль modules/builtin/SystemKeeper.py
   (команда .restart).
3. Обновление и восстановление бэкапа: модуль сам скачивает/принимает
   архив, проверяет его и раскладывает во временную папку (staging), пишет
   заявку user/data/keeper/request.json и завершает main.py с кодом
   EXIT_APPLY (43). Дальше работает keeper, пока main.py остановлен (ничего
   не занято, файлы можно спокойно заменять):
       - делает резервную копию того, что будет заменено;
       - заменяет файлы;
       - при изменении requirements.txt сам делает pip install -r;
       - запускает main.py заново;
       - если новая версия не запустилась — откатывает изменения из
         резервной копии и запускает прежнюю версию.
4. Если main.py упал сам: пробует перезапустить ОДИН раз. Если и после
   этого не запустился полноценно — больше не пытается (keeper завершается).
   «Запустился полноценно» — main.py записал ready.json (после старта ядра
   и стационарного бота) и проработал STABLE_SECONDS.
5. Держит порт сайта .web: заглушкой «юзербот перезагружается» во время
   рестарта и «сторожем», который поднимает сайт по первому заходу.
6. Гибернация: нет связи с Telegram — резервная копия данных, юзербот
   выключен, keeper ждёт сеть и запускает его сам.

Что обновляется (манифест — frx_manifest.json)
----------------------------------------------
Код проекта — ВСЕ файлы пакета обновления, кроме личного и мусора (EXCLUDE_TOP, EXCLUDE_PREFIXES, _is_junk):
папка пользователя user/, старые config.json / data/ / modules/loaded/, .git, виртуальные окружения,
__pycache__, логи. Поэтому новая версия может приносить любые новые файлы и папки — они ставятся.

После каждого обновления keeper пишет frx_manifest.json — список файлов установленной версии (с хешами).
Файл, который был в манифесте старой версии и которого нет в новой, удаляется. Файлы, которых в манифесте
нет (ваши собственные в корне проекта, файлы в assets/ с сайта), не трогаются никогда. Без манифеста
(установка старой версии) «старым» считается прежний белый список LEGACY_CODE_DIRS / LEGACY_CODE_FILES.

Никогда не трогается папка пользователя user/ (config.json, data/, modules/, assets/).

Протокол keeper <-> main (папка user/data/keeper/)
--------------------------------------------------
    request.json   модуль -> keeper: {action: restart|update|restore, notify, meta}
    result.json    keeper -> модуль: итог (ok, откат, списки файлов, pip...)
    ready.json     main.py -> keeper: "полностью запущен"
    journal.json   keeper: идёт замена файлов (если процесс прервали на
                   середине — при следующем старте keeper откатит сам)
    staging/       распакованный и проверенный пакет обновления
    restore/       распакованный и проверенный бэкап пользовательских данных
    incoming/, tmp/  временные файлы
    web_state.json, web_takeover, web_wake, web_sentinel.json — сайт .web

Резервные копии — user/data/backup/ (последние BACKUP_KEEP каждого вида):
    code_<время>_v<версия>.zip              перед каждым обновлением
    userdata_prerestore_<время>_v<...>.zip  перед каждым восстановлением бэкапа
    offline_<время>_v<...>.zip              при уходе в гибернацию

Этот файл — только стандартная библиотека Python и НЕ импортирует ничего
из core/, поэтому работает, даже если после неудачного обновления core/
сломан. Из процесса юзербота его функции (prepare_update, make_user_backup,
...) импортируют модули SystemKeeper.py и Backup.py (`import frx_systemkeeper`).
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import socket
import sqlite3
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

KEEPER_VERSION = "2.0"
PROTO = 1

ROOT = Path(__file__).resolve().parent

# --- коды выхода main.py ---
EXIT_STOP = 0        # обычная остановка — keeper тоже завершается
EXIT_RESTART = 42    # перезагрузка
EXIT_APPLY = 43      # применить заявку (update / restore) из request.json
EXIT_NO_RETRY = 78   # ошибка конфигурации — перезапускать бессмысленно
EXIT_HIBERNATE = 79  # нет связи с Telegram — keeper делает копию и спит, пока сеть не вернётся

# --- пользовательские данные: папка user/ ---
# Та же логика, что в core/paths.py (keeper не импортирует core/) — при изменении правьте оба места.
# Новая раскладка: user/config.json, user/data/, user/modules/, user/assets/. Старая (до переезда):
# config.json, data/, modules/loaded/ в корне проекта — переносится в user/ функцией migrate_legacy_layout()
# при запуске keeper'а. Пока перенос не выполнен (LEGACY), пути указывают на старые места.

def _user_dir() -> Path:
    raw = os.environ.get('FRX_USER_DIR', '').strip()
    return ROOT / raw if raw else ROOT / 'user'      # абсолютный raw остаётся абсолютным


def _layout() -> dict:
    user = _user_dir()
    legacy = not user.exists() and ((ROOT / 'config.json').is_file() or (ROOT / 'data').is_dir())
    if legacy:
        return {'USER_DIR': ROOT, 'LEGACY': True, 'CONFIG_PATH': ROOT / 'config.json', 'DATA_DIR': ROOT / 'data',
                'MODULES_DIR': ROOT / 'modules' / 'loaded', 'USER_ASSETS_DIR': None}
    return {'USER_DIR': user, 'LEGACY': False, 'CONFIG_PATH': user / 'config.json', 'DATA_DIR': user / 'data',
            'MODULES_DIR': user / 'modules', 'USER_ASSETS_DIR': user / 'assets'}


def _apply_layout() -> None:
    """(Пере)вычисляет все пути пользовательских данных — после переезда в user/ они меняются."""
    g = globals()
    g.update(_layout())
    d = g['DATA_DIR']
    g['KEEPER_DIR'] = k = d / 'keeper'
    g['BACKUP_DIR'] = d / 'backup'
    g['STAGING_DIR'] = k / 'staging'
    g['RESTORE_DIR'] = k / 'restore'
    g['INCOMING_DIR'] = k / 'incoming'
    g['TMP_DIR'] = k / 'tmp'
    g['REQUEST_FILE'] = k / 'request.json'
    g['RESULT_FILE'] = k / 'result.json'
    g['READY_FILE'] = k / 'ready.json'
    g['JOURNAL_FILE'] = k / 'journal.json'
    g['LOG_FILE'] = k / 'keeper.log'
    g['WEB_STATE_FILE'] = k / 'web_state.json'        # main.py -> keeper: «сайт работал, удержи порт»
    g['WEB_TAKEOVER_FILE'] = k / 'web_takeover'       # новый main.py -> keeper: «отдай порт»
    g['WEB_WAKE_FILE'] = k / 'web_wake'               # сторож keeper'а -> юзербот: «кто-то зашёл на сайт, поднимай»
    g['SENTINEL_FILE'] = k / 'web_sentinel.json'      # keeper -> юзербот: «порт сторожа такой-то»
    g['TUNNEL_REQUEST_FILE'] = k / 'tunnel_request.json'  # юзербот -> keeper: включить/выключить публичный туннель
    g['TUNNEL_STATE_FILE'] = k / 'tunnel.json'            # keeper -> юзербот: адрес туннеля и его состояние
    g['BIN_DIR'] = d / 'bin'                              # скачанные программы (cloudflared)


USER_DIR = LEGACY = CONFIG_PATH = DATA_DIR = MODULES_DIR = USER_ASSETS_DIR = None
KEEPER_DIR = BACKUP_DIR = STAGING_DIR = RESTORE_DIR = INCOMING_DIR = TMP_DIR = None
REQUEST_FILE = RESULT_FILE = READY_FILE = JOURNAL_FILE = LOG_FILE = None
WEB_STATE_FILE = WEB_TAKEOVER_FILE = WEB_WAKE_FILE = SENTINEL_FILE = None
TUNNEL_REQUEST_FILE = TUNNEL_STATE_FILE = BIN_DIR = None
_apply_layout()

WEB_STATIC_DIR = ROOT / 'core' / 'web' / 'static'
WEB_STATIC_FILES = {'login.css', 'login.js', 'style.css', 'app.js', 'bg.webp', 'font.woff2', 'botchat.js', 'botchat.css', 'webide.js', 'webide.css', 'sandbox.js',
                    'lab_core.js', 'lab.js', 'lab.css', 'panel.js', 'panel.css', 'neuro.js', 'neuro.css',
                    'accent.css', 'fx.js', 'access.js', 'access.css', 'depth.js', 'depth.css', 'mobile.css', 'mobile.js'}
WEB_DEFAULT_PORT = 8878
LOCAL_HOSTS = ('127.0.0.1', 'localhost', '[::1]')


def web_bind_host() -> str:
    """Адрес, на котором слушает сайт и его заглушки: 127.0.0.1; в Docker — FRX_WEB_BIND=0.0.0.0 (порт наружу публикует compose)."""
    return (os.environ.get('FRX_WEB_BIND') or '').strip() or '127.0.0.1'


def web_allowed_hosts() -> set:
    """Имена хостов, с которыми можно открывать сайт: локальные, адрес публичного туннеля и web_hosts из config.json."""
    out = set(LOCAL_HOSTS)
    st = read_json(TUNNEL_STATE_FILE) if TUNNEL_STATE_FILE else None
    if isinstance(st, dict) and st.get('host'):
        out.add(str(st['host']).lower())
    if isinstance(st, dict) and st.get('prev_host') and time.time() - float(st.get('since') or 0) < 300:
        out.add(str(st['prev_host']).lower())         # прежний адрес — пока открытые страницы переезжают на новый
    cfg = read_json(CONFIG_PATH, {}) if CONFIG_PATH else {}
    extra = cfg.get('web_hosts') if isinstance(cfg, dict) else None
    if isinstance(extra, list):
        out.update(str(h).strip().lower() for h in extra if str(h).strip())
    return out
WEB_HOLD_MAX = 15 * 60                                # дольше этого заглушка порт не держит

# --- что считается кодом проекта (см. «Что обновляется» в начале файла) ---
MANIFEST_NAME = 'frx_manifest.json'
EXCLUDE_TOP = frozenset({
    'user', 'config.json', 'data', MANIFEST_NAME,                  # личное (и старая раскладка), сам манифест
    '.git', '.hg', '.svn', '.venv', 'venv', 'env', '.env', '.idea', '.vscode', 'node_modules', '__pycache__',
    '.pytest_cache', '.mypy_cache', '.ruff_cache', 'dist', 'build',
})
EXCLUDE_PREFIXES = ('modules/loaded/', 'modules/unloaded/')
_JUNK_NAMES = frozenset({'.DS_Store', 'Thumbs.db', 'desktop.ini'})
_JUNK_EXT = ('.pyc', '.pyo', '.log', '.tmp', '.swp', '.bak', '~')
# Прежний белый список (до манифеста): по нему определяется «старый» код установки без frx_manifest.json.
LEGACY_CODE_DIRS = ('core', 'aiobot', 'assets', 'modules/builtin', 'examples')
LEGACY_CODE_FILES = ('main.py', 'frx_systemkeeper.py', 'requirements.txt', 'config.example.json',
                     'README.md', 'CHANGELOG.md', 'LICENSE', 'SECURITY.md')
# Без этих файлов пакет обновления считается неполным и не принимается.
# SystemKeeper.py и frx_systemkeeper.py — чтобы после обновления не
# потерять саму возможность обновляться.
REQUIRED_FILES = ('main.py', 'frx_systemkeeper.py', 'requirements.txt',
                  'core/engine.py', 'aiobot/bot.py',
                  'modules/builtin/SystemKeeper.py')

USER_BACKUP_ROOT = 'FRX_BACKUP'
USER_BACKUP_TYPE = 'frx-userdata-backup'

BACKUP_KEEP = 3            # сколько копий каждого вида хранить
STABLE_SECONDS = 60        # столько main.py должен проработать после ready.json
RETRY_DELAY = 3            # пауза перед единственной повторной попыткой запуска
MAX_UNPACKED = 500 * 1024 * 1024    # защита от zip-бомб
MAX_DOWNLOAD = 200 * 1024 * 1024    # предел скачивания архива с GitHub

_TEXT_EXT = ('.py', '.md', '.txt', '.json', '.css', '.js', '.html')   # .css/.js/.html — сайт .web


class KeeperError(Exception):
    """Ожидаемая ошибка с понятным пользователю текстом."""


# ===================================================================== #
# Общие утилиты
# ===================================================================== #

def keeper_active() -> bool:
    """True, если процесс запущен через frx_systemkeeper.py."""
    return os.environ.get('FRX_KEEPER') == '1'


def _stamp() -> str:
    return datetime.now().strftime('%Y%m%d_%H%M%S')


def _safe_ver(version) -> str:
    return re.sub(r'[^\w.\-]', '_', str(version or '?'))


def log(text: str) -> None:
    print(f"{time.strftime('%m.%d %H:%M:%S')} [FRX Keeper] {text}", flush=True)
    try:
        KEEPER_DIR.mkdir(parents=True, exist_ok=True)
        if LOG_FILE.exists() and LOG_FILE.stat().st_size > 512 * 1024:
            LOG_FILE.write_bytes(LOG_FILE.read_bytes()[-256 * 1024:])
        with LOG_FILE.open('a', encoding='utf-8') as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {text}\n")
    except OSError:
        pass


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def write_json(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    replace_retry(tmp, path)


def replace_retry(src, dst, tries: int = 8) -> None:
    """os.replace с повтором: на Windows файл на мгновение держат антивирус, индексатор или читающий его
    юзербот — тогда replace падает с WinError 5 / 32 («Отказано в доступе»). Ждём и пробуем ещё раз."""
    delay = 0.02
    for i in range(tries):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == tries - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def rmtree(path) -> None:
    """Удаляет файл/папку (в т.ч. с read-only файлами на Windows); нет пути — не ошибка."""
    path = Path(path)
    if path.is_symlink() or path.is_file():
        try:
            path.unlink()
        except PermissionError:
            os.chmod(path, 0o700)
            path.unlink()
        return
    if not path.exists():
        return

    def _handler(func, p, _exc):
        os.chmod(p, 0o700)
        func(p)

    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=_handler)
    else:
        shutil.rmtree(path, onerror=_handler)


def _safe_rel(name: str):
    """Нормализует имя записи zip; None — если путь небезопасен (zip-slip)."""
    n = name.replace('\\', '/')
    if n.startswith('/') or re.match(r'^[A-Za-z]:', n):
        return None
    parts = [p for p in n.split('/') if p not in ('', '.')]
    if any(p == '..' for p in parts):
        return None
    return '/'.join(parts)


def _is_junk(rel: str) -> bool:
    parts = rel.split('/')
    return '__pycache__' in parts or parts[-1] in _JUNK_NAMES or rel.endswith(_JUNK_EXT)


def _user_rel():
    """Папка пользователя относительно корня проекта (FRX_USER_DIR может указывать и вне его — тогда None)."""
    try:
        return _user_dir().resolve().relative_to(ROOT).as_posix()
    except (ValueError, OSError):
        return None


def is_code_rel(rel: str) -> bool:
    """Относится ли путь (относительно корня проекта) к коду FRX, а не к личным данным или мусору."""
    if not rel or _is_junk(rel):
        return False
    if rel.split('/')[0] in EXCLUDE_TOP or rel.startswith(EXCLUDE_PREFIXES):
        return False
    u = _user_rel()
    return not (u and (rel == u or rel.startswith(u + '/')))


def _in_legacy_whitelist(rel: str) -> bool:
    return rel in LEGACY_CODE_FILES or any(rel.startswith(d + '/') for d in LEGACY_CODE_DIRS)


_in_whitelist = is_code_rel          # совместимость: модули старых версий импортировали это имя


def _sha(path: Path) -> str:
    """SHA-256; у текстовых файлов CRLF/LF не различаются (Windows vs GitHub)."""
    data = Path(path).read_bytes()
    if str(path).lower().endswith(_TEXT_EXT):
        data = data.replace(b'\r\n', b'\n')
    return hashlib.sha256(data).hexdigest()


def _ver_tuple(version: str) -> tuple:
    nums = [int(x) for x in re.findall(r'\d+', str(version))]
    return tuple((nums + [0, 0, 0, 0])[:max(4, len(nums))])


def read_engine_version(base: Path):
    """FRX_VERSION из core/engine.py (без импорта и исполнения кода)."""
    try:
        text = (Path(base) / 'core' / 'engine.py').read_text(encoding='utf-8')
    except OSError:
        return None
    m = re.search(r'^FRX_VERSION\s*=\s*["\']([^"\']+)["\']', text, re.M)
    return m.group(1) if m else None


def scan_code_files(base: Path) -> dict:
    """{относительный_путь: Path} — все файлы кода в папке base (пакет обновления, проект перед публикацией)."""
    base = Path(base)
    out = {}
    if not base.is_dir():
        return out
    for dirpath, dirnames, filenames in os.walk(base):
        d = Path(dirpath)
        rel_dir = d.relative_to(base).as_posix()
        rel_dir = '' if rel_dir == '.' else rel_dir + '/'
        dirnames[:] = [n for n in dirnames if is_code_rel(rel_dir + n + '/x') and not (d / n).is_symlink()]
        for n in filenames:
            rel = rel_dir + n
            if is_code_rel(rel) and not (d / n).is_symlink():
                out[rel] = d / n
    return out


def read_manifest(base: Path = None):
    """Манифест установленной версии {'frx_version', 'files': {rel: sha256}} или None."""
    data = read_json(Path(base or ROOT) / MANIFEST_NAME)
    if not isinstance(data, dict) or not isinstance(data.get('files'), dict):
        return None
    return data


def build_manifest(base: Path, files: dict = None) -> dict:
    files = scan_code_files(base) if files is None else files
    return {
        'type': 'frx-manifest', 'frx_version': read_engine_version(base) or '?',
        'created': datetime.now().isoformat(timespec='seconds'),
        'files': {rel: _sha(p) for rel, p in sorted(files.items())},
    }


def write_manifest(base: Path = None, files: dict = None) -> dict:
    base = Path(base or ROOT)
    data = build_manifest(base, files)
    write_json(base / MANIFEST_NAME, data)
    return data


def installed_code_files(base: Path = None) -> dict:
    """
    {rel: Path} кода текущей установки — то, что обновление вправе заменить или удалить: файлы из манифеста,
    а без манифеста — прежний белый список. Свои файлы пользователя в корне сюда не попадают.
    """
    base = Path(base or ROOT)
    man = read_manifest(base)
    if man:
        rels = [r for r in man['files'] if is_code_rel(r)]
    else:
        # assets/ старых версий хранила и файлы пользователя (вкладка files) — без манифеста их не отличить, не трогаем
        rels = [r for r in scan_code_files(base) if _in_legacy_whitelist(r) and not r.startswith('assets/')]
    return {r: base / r for r in rels if (base / r).is_file()}


collect_code_files = installed_code_files     # совместимость со старыми вызовами


def code_diff(old_base: Path, new_base: Path) -> dict:
    """Что изменит установка пакета new_base поверх old_base: added / changed / removed (только код из манифеста)."""
    old_base, new_base = Path(old_base), Path(new_base)
    new = scan_code_files(new_base)
    old = installed_code_files(old_base)
    return {
        'added': sorted(r for r in new if not (old_base / r).is_file()),
        'removed': sorted(r for r in old if r not in new),
        'changed': sorted(r for r in new if (old_base / r).is_file() and _sha(old_base / r) != _sha(new[r])),
    }


def prune_backups(prefix: str) -> None:
    files = sorted(BACKUP_DIR.glob(f'{prefix}*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in files[BACKUP_KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups() -> list:
    """[(имя, размер, время)] всех резервных копий keeper, новые сверху."""
    if not BACKUP_DIR.is_dir():
        return []
    files = sorted(BACKUP_DIR.glob('*.zip'), key=lambda p: p.stat().st_mtime, reverse=True)
    return [(f.name, f.stat().st_size, f.stat().st_mtime) for f in files]


# ===================================================================== #
# API для модулей юзербота (SystemKeeper.py / Backup.py)
# ===================================================================== #

def write_request(action: str, notify: dict = None, meta: dict = None) -> None:
    """Заявка keeper'у. Сразу после неё модуль обязан завершить процесс кодом EXIT_*."""
    write_json(REQUEST_FILE, {
        'proto': PROTO, 'action': action, 'created': time.time(),
        'notify': notify or {}, 'meta': meta or {},
    })


def read_result():
    return read_json(RESULT_FILE)


def clear_result() -> None:
    try:
        RESULT_FILE.unlink()
    except OSError:
        pass


def cleanup_work_dirs() -> None:
    """Чистит временные папки (staging/restore/incoming/tmp) и заявку."""
    for p in (STAGING_DIR, RESTORE_DIR, INCOMING_DIR, TMP_DIR):
        rmtree(p)
    try:
        REQUEST_FILE.unlink()
    except OSError:
        pass


# --------------------------- обновление: проверка ---------------------- #

def _detect_prefix(names: dict, source: str) -> str:
    if source == 'file':
        if 'FRX/main.py' not in names:
            raise KeeperError(
                "Это не архив FRX: содержимое должно лежать в папке FRX/ "
                "(FRX/main.py, FRX/core/…)."
            )
        return 'FRX/'
    tops = {r.split('/')[0] for r in names if '/' in r}
    if len(tops) != 1:
        raise KeeperError("Неожиданная структура архива GitHub (ожидалась одна корневая папка).")
    base = tops.pop() + '/'
    if f'{base}main.py' not in names and f'{base}FRX/main.py' in names:
        base += 'FRX/'
    if f'{base}main.py' not in names:
        raise KeeperError("В репозитории нет main.py — это не репозиторий FRX.")
    return base


def prepare_update(zip_path, source: str, current_version: str) -> dict:
    """
    Проверяет архив обновления и раскладывает белый список в STAGING_DIR.
    source — 'file' (корень обязан быть FRX/) или 'github'.
    Ничего в рабочих файлах не меняет. Бросает KeeperError с понятным текстом.
    Возвращает {'from','to','added','changed','removed','requirements_changed'}.
    """
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise KeeperError("Файл не является корректным zip-архивом.")

    with zf:
        if sum(i.file_size for i in zf.infolist()) > MAX_UNPACKED:
            raise KeeperError("Архив слишком большой после распаковки.")

        names = {}
        for info in zf.infolist():
            rel = _safe_rel(info.filename)
            if rel is None:
                raise KeeperError(f"Небезопасный путь в архиве: {info.filename}")
            if not info.is_dir() and rel:
                names[rel] = info

        prefix = _detect_prefix(names, source)

        selected = {}
        for rel, info in names.items():
            if not rel.startswith(prefix):
                continue
            inner = rel[len(prefix):]
            if is_code_rel(inner) and not (info.external_attr >> 16) & 0o170000 == 0o120000:   # без симлинков
                selected[inner] = info

        missing = [r for r in REQUIRED_FILES if r not in selected]
        if missing:
            raise KeeperError("В архиве не хватает обязательных файлов: " + ", ".join(missing))

        m = re.search(rb'^FRX_VERSION\s*=\s*["\']([^"\']+)["\']',
                      zf.read(selected['core/engine.py']), re.M)
        if not m:
            raise KeeperError("В core/engine.py архива не найден FRX_VERSION.")
        new_version = m.group(1).decode('utf-8', 'replace')

        if _ver_tuple(new_version) <= _ver_tuple(current_version):
            if _ver_tuple(new_version) == _ver_tuple(current_version):
                raise KeeperError(f"Уже установлена эта версия ({current_version}).")
            raise KeeperError(
                f"Версия в архиве ({new_version}) старше установленной ({current_version}) — "
                f"понижение версии не выполняется."
            )

        for rel, info in selected.items():
            if rel.endswith('.py'):
                try:
                    compile(zf.read(info), rel, 'exec')
                except SyntaxError as e:
                    raise KeeperError(f"Синтаксическая ошибка в {rel}, строка {e.lineno}: {e.msg}")

        rmtree(STAGING_DIR)
        for rel, info in selected.items():
            dst = STAGING_DIR / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zf.read(info))

    diff = code_diff(ROOT, STAGING_DIR)
    old_req, new_req = ROOT / 'requirements.txt', STAGING_DIR / 'requirements.txt'
    req_changed = (not old_req.is_file()) or _sha(old_req) != _sha(new_req)
    return {'from': current_version, 'to': new_version, 'requirements_changed': req_changed, **diff}


# --------------------------- обновление: GitHub ------------------------ #

def parse_repo(repo: str) -> str:
    """'https://github.com/owner/name(.git)' | 'owner/name' -> 'owner/name'."""
    r = (repo or '').strip()
    r = re.sub(r'^(https?://)?(www\.)?github\.com/', '', r).strip('/')
    parts = r.split('/')
    if len(parts) < 2:
        raise KeeperError("Не указан репозиторий GitHub (ожидается owner/name или ссылка).")
    owner, name = parts[0], re.sub(r'\.git$', '', parts[1])
    if not re.fullmatch(r'[\w.\-]+', owner) or not re.fullmatch(r'[\w.\-]+', name):
        raise KeeperError(f"Некорректное имя репозитория: {repo}")
    return f"{owner}/{name}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def download_github(repo: str, branch: str, token: str, dest) -> str:
    """Скачивает zip ветки репозитория в dest. Возвращает 'owner/name@branch'."""
    slug = parse_repo(repo)
    branch = (branch or 'main').strip() or 'main'
    url = f"https://api.github.com/repos/{slug}/zipball/{urllib.parse.quote(branch, safe='/')}"
    headers = {
        'User-Agent': 'FRX-SystemKeeper',
        'Accept': 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
    }
    if token:
        headers['Authorization'] = f'Bearer {token.strip()}'

    opener = urllib.request.build_opener(_NoRedirect)
    resp = None
    try:
        for _ in range(5):
            try:
                resp = opener.open(urllib.request.Request(url, headers=headers), timeout=60)
                break
            except urllib.error.HTTPError as e:
                location = e.headers.get('Location') if e.code in (301, 302, 303, 307, 308) else None
                if not location:
                    raise
                url = urllib.parse.urljoin(url, location)
                if urllib.parse.urlparse(url).netloc != 'api.github.com':
                    headers.pop('Authorization', None)   # токен — только api.github.com
        else:
            raise KeeperError("Слишком много перенаправлений при скачивании с GitHub.")

        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        size = 0
        with dest.open('wb') as out:
            while True:
                chunk = resp.read(1024 * 256)
                if not chunk:
                    break
                size += len(chunk)
                if size > MAX_DOWNLOAD:
                    raise KeeperError("Архив с GitHub слишком большой.")
                out.write(chunk)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise KeeperError(f"Репозиторий или ветка не найдены: {slug}@{branch} "
                              f"(для приватного репозитория нужен токен).")
        if e.code == 401:
            raise KeeperError("GitHub отклонил токен (401) — проверьте github_token.")
        if e.code == 403:
            raise KeeperError("GitHub запретил доступ (403): нет прав у токена или исчерпан лимит запросов.")
        raise KeeperError(f"GitHub вернул ошибку HTTP {e.code}.")
    except urllib.error.URLError as e:
        raise KeeperError(f"Нет соединения с GitHub: {e.reason}")
    except OSError as e:
        raise KeeperError(f"Ошибка скачивания: {e}")
    finally:
        if resp is not None:
            resp.close()
    return f"{slug}@{branch}"


# --------------------------- переезд в user/ ---------------------------- #

def _rewrite_db_paths(db_file: Path, old_prefix: str, new_prefix: str) -> None:
    """В БД реестр файлов (core:files:*) хранит пути вида data/files/x — после переезда они должны вести в user/."""
    data = read_json(db_file)
    if not isinstance(data, dict):
        return
    changed = False
    for key, obj in data.items():
        if key.startswith('core:files:') and isinstance(obj, dict) and isinstance(obj.get('path'), str):
            p = obj['path'].replace('\\', '/')
            if p.startswith(old_prefix):
                obj['path'] = new_prefix + p[len(old_prefix):]
                changed = True
    if data.pop('core:form_media', None) is not None:   # кэш file_id медиа инлайн-форм: ключи — старые пути, соберётся заново
        changed = True
    if changed:
        write_json(db_file, data)


def migrate_legacy_layout() -> bool:
    """
    Переносит пользовательские данные из старой раскладки в user/:
        config.json -> user/config.json,  data/ -> user/data/,  modules/loaded/ -> user/modules/
    Вызывается только из main() keeper'а, пока main.py не запущен (файлы не заняты). Ошибка на середине —
    уже перенесённое возвращается на место, юзербот продолжает работать со старой раскладкой.
    Возвращает True, если перенос выполнен.
    """
    user = _user_dir()
    if user.exists():
        leftovers = [n for n in ('config.json', 'data', 'modules/loaded') if (ROOT / n).exists()
                     and not (n == 'modules/loaded' and not any((ROOT / n).glob('*.py')))]
        if leftovers:
            log(f"Внимание: рядом с {user.name}/ остались файлы старой раскладки ({', '.join(leftovers)}) — "
                f"юзербот их не использует; перенесите нужное в {user.name}/ вручную")
        return False
    moves = [(ROOT / 'config.json', user / 'config.json'),
             (ROOT / 'data', user / 'data'),
             (ROOT / 'modules' / 'loaded', user / 'modules')]
    moves = [(src, dst) for src, dst in moves if src.exists()]
    if not moves:
        return False
    log(f"Переношу пользовательские данные в {user}: " + ', '.join(src.relative_to(ROOT).as_posix() for src, _ in moves))
    done = []
    try:
        user.mkdir(parents=True)
        for src, dst in moves:
            shutil.move(str(src), str(dst))
            done.append((src, dst))
    except Exception as e:
        print(f"[FRX Keeper] Перенос не удался: {e} — возвращаю файлы на место", flush=True)
        for src, dst in reversed(done):
            try:
                shutil.move(str(dst), str(src))
            except Exception as back:
                print(f"[FRX Keeper] Не удалось вернуть {dst} -> {src}: {back}", flush=True)
        try:
            user.rmdir()
        except OSError:
            pass
        return False

    for pyc in (user / 'modules').rglob('__pycache__') if (user / 'modules').is_dir() else ():
        rmtree(pyc)
    cfg = read_json(user / 'config.json')
    if isinstance(cfg, dict) and str(cfg.get('db_path', '')).replace('\\', '/') in ('data/database.json', './data/database.json'):
        cfg.pop('db_path')                           # значение по умолчанию — теперь user/data/database.json
        write_json(user / 'config.json', cfg)
    db_file = user / 'data' / 'database.json'
    if db_file.is_file():
        try:
            rel = user.relative_to(ROOT).as_posix() + '/'
        except ValueError:
            rel = user.as_posix().rstrip('/') + '/'
        _rewrite_db_paths(db_file, 'data/', rel + 'data/')
    _apply_layout()
    log(f"Пользовательские данные перенесены в {user}")
    return True


# --------------------------- бэкап пользовательских данных ------------- #
#
# Внутри zip — папка FRX_BACKUP/ с путями относительно папки пользователя:
#     config.json, data/..., modules/..., assets/...   + manifest.json ("layout": "user")
# Бэкапы старой раскладки (modules/loaded/... вместо modules/..., без assets/) тоже восстанавливаются.

# кэши, которые восстановятся сами (скачанные файлы и аватарки BotChat) — в резервную копию не кладём
CACHE_DIRS = ('data/botchat/media/', 'data/botchat/avatars/', 'data/bin/', 'data/cache/')     # кэш BotChat, программы (cloudflared, ffmpeg) и кэши модулей — не данные
NOBACKUP_MARK = '.nobackup'         # файл-метка: папка с ним (и всё внутри) в резервную копию не идёт — для кэшей и тяжёлых медиа модулей
NOBACKUP_FILE_LIMIT = 45 * 1024 * 1024   # одиночный файл больше этого — точно не данные FRX (видео, архив) — не кладём


def _user_roots() -> list:
    """[(имя в бэкапе, папка на диске)] — что считается пользовательскими данными (кроме config.json)."""
    roots = [('data', DATA_DIR), ('modules', MODULES_DIR)]
    if USER_ASSETS_DIR is not None:
        roots.append(('assets', USER_ASSETS_DIR))
    return roots


def _walk_files(base: Path):
    """Файлы папки, кроме папок с меткой .nobackup и слишком больших одиночных файлов."""
    for root, dirs, files in os.walk(base):
        if NOBACKUP_MARK in files:
            dirs[:] = []
            continue
        dirs[:] = [d for d in dirs if d != '__pycache__']
        for fn in files:
            f = Path(root) / fn
            try:
                if f.is_file() and f.stat().st_size <= NOBACKUP_FILE_LIMIT:
                    yield f
            except OSError:
                continue


STUCK_EXIT_SECONDS = 45


def _force_stuck_exit(proc):
    """main.py попросил перезапуск/обновление (request.json), но сам не завершился за STUCK_EXIT_SECONDS
    (зависший поток библиотеки, сетевой запрос) — завершаем его сами. Возвращает код, как если бы он вышел сам."""
    try:
        if not REQUEST_FILE.exists():
            return None
        req = read_json(REQUEST_FILE) or {}
        created = float(req.get('created') or 0)
    except Exception:
        return None
    if not created or created < getattr(proc, '_frx_started', 0) or time.time() - created < STUCK_EXIT_SECONDS:
        return None                     # заявка старше этого запуска (осталась от прошлого) — не наша
    log(f"main.py не завершился за {STUCK_EXIT_SECONDS} с после запроса «{req.get('action')}» — завершаю принудительно")
    try:
        proc.terminate()
        proc.wait(timeout=5)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=5)
        except Exception:
            pass
    return EXIT_RESTART if req.get('action') == 'restart' else EXIT_APPLY


def _iter_user_files() -> list:
    """[(путь_в_бэкапе, Path)] — всё, что считается пользовательскими данными."""
    items = []
    if CONFIG_PATH.is_file():
        items.append(('config.json', CONFIG_PATH))
    for name, base in _user_roots():
        if not base.is_dir():
            continue
        for f in _walk_files(base):
            rel = f"{name}/{f.relative_to(base).as_posix()}"
            parts = rel.split('/')
            if parts[0] == 'data' and len(parts) > 1 and parts[1] in ('backup', 'keeper'):
                continue
            if rel.startswith(CACHE_DIRS):          # кэш BotChat (копии с серверов Telegram) и программы из data/bin — не данные
                continue
            if '__pycache__' in parts or rel.endswith(('-journal', '-wal', '-shm', '.pyc')):
                continue
            items.append((rel, f))
    return items


def _sqlite_snapshot(path: Path):
    """Целостная копия живой sqlite-БД (сессии Telethon); None — если не вышло."""
    try:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        tmp = TMP_DIR / f"snap_{os.getpid()}_{path.name}"
        src = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        try:
            dst = sqlite3.connect(str(tmp))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return tmp
    except Exception:
        return None


def make_user_backup(dst, version: str) -> dict:
    """
    Пишет zip с пользовательскими данными: config.json, data/ (кроме data/backup,
    data/keeper и кэша BotChat), modules/ и assets/ папки пользователя. Внутри —
    папка FRX_BACKUP/ и manifest.json. Возвращает {'path','files','size'}.
    """
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    items = _iter_user_files()
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel, f in items:
            src, snap = f, None
            if f.suffix == '.session':
                snap = _sqlite_snapshot(f)
                if snap:
                    src = snap
            try:
                zf.write(src, f"{USER_BACKUP_ROOT}/{rel}")
            finally:
                if snap:
                    try:
                        snap.unlink()
                    except OSError:
                        pass
        zf.writestr(f"{USER_BACKUP_ROOT}/manifest.json", json.dumps({
            'type': USER_BACKUP_TYPE, 'proto': PROTO, 'layout': 'user', 'frx_version': str(version),
            'created': datetime.now().isoformat(timespec='seconds'),
            'files': [rel for rel, _ in items],
        }, ensure_ascii=False, indent=2))
    return {'path': dst, 'files': len(items), 'size': dst.stat().st_size}


def _user_backup_entries(zf: zipfile.ZipFile) -> tuple:
    """
    Проверяет zip пользовательского бэкапа. Возвращает (manifest, {rel: info}), где rel уже приведён
    к раскладке папки пользователя: config.json | data/... | modules/... | assets/...
    """
    raw = {}
    manifest = None
    for info in zf.infolist():
        rel = _safe_rel(info.filename)
        if rel is None:
            raise KeeperError(f"Небезопасный путь в архиве: {info.filename}")
        if info.is_dir() or not rel.startswith(USER_BACKUP_ROOT + '/'):
            continue
        inner = rel[len(USER_BACKUP_ROOT) + 1:]
        if inner == 'manifest.json':
            try:
                manifest = json.loads(zf.read(info).decode('utf-8'))
            except ValueError:
                raise KeeperError("manifest.json в бэкапе повреждён.")
            continue
        raw[inner] = info
    if not manifest or manifest.get('type') != USER_BACKUP_TYPE:
        raise KeeperError("Это не бэкап FRX (нет корректного manifest.json).")
    new_layout = manifest.get('layout') == 'user'
    entries = {}
    for inner, info in raw.items():
        parts = inner.split('/')
        if inner == 'config.json':
            entries[inner] = info
        elif parts[0] == 'data' and len(parts) > 1 and parts[1] not in ('backup', 'keeper'):
            entries[inner] = info
        elif not new_layout and inner.startswith('modules/loaded/') and len(parts) > 2:
            entries['modules/' + '/'.join(parts[2:])] = info          # бэкап старой раскладки
        elif new_layout and parts[0] in ('modules', 'assets') and len(parts) > 1:
            entries[inner] = info
    if not entries:
        raise KeeperError("В бэкапе нет файлов для восстановления.")
    return manifest, entries


def _extract_user_backup(zip_path, dest_dir: Path) -> dict:
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise KeeperError("Файл не является корректным zip-архивом.")
    with zf:
        if sum(i.file_size for i in zf.infolist()) > 4 * MAX_UNPACKED:
            raise KeeperError("Архив слишком большой после распаковки.")
        manifest, entries = _user_backup_entries(zf)
        rmtree(dest_dir)
        for rel, info in entries.items():
            dst = dest_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, dst.open('wb') as out:
                shutil.copyfileobj(src, out)
    return {'frx_version': manifest.get('frx_version', '?'),
            'created': manifest.get('created', '?'), 'files': len(entries)}


def prepare_restore(zip_path) -> dict:
    """Проверяет бэкап и раскладывает в RESTORE_DIR. Возвращает {'frx_version','created','files'}."""
    return _extract_user_backup(zip_path, RESTORE_DIR)


def _replace_dir(src: Path, dst: Path, keep=()) -> None:
    """Содержимое dst заменяется содержимым src (имена из keep в dst не трогаются)."""
    dst.mkdir(parents=True, exist_ok=True)
    for child in list(dst.iterdir()):
        if child.name in keep or child.name == '__pycache__':
            continue
        rmtree(child)
    for child in src.iterdir():
        if child.name in keep and (dst / child.name).exists():     # то, что сохранили на месте, бэкапом не перетираем
            continue
        if child.is_dir():
            shutil.copytree(child, dst / child.name)
        else:
            shutil.copy2(child, dst / child.name)


def _apply_user_dir(src: Path) -> None:
    """Заменяет пользовательские данные содержимым src (config.json / data / modules / assets — раскладка папки пользователя)."""
    if (src / 'config.json').is_file():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src / 'config.json', CONFIG_PATH)
    if (src / 'data').is_dir():
        _replace_dir(src / 'data', DATA_DIR, keep=('backup', 'keeper', 'bin'))     # bin — работающий cloudflared не трогаем
    if (src / 'modules').is_dir():
        _replace_dir(src / 'modules', MODULES_DIR)
    if (src / 'assets').is_dir() and USER_ASSETS_DIR is not None:
        _replace_dir(src / 'assets', USER_ASSETS_DIR)


# ===================================================================== #
# Применение обновления / восстановления (работает keeper, main остановлен)
# ===================================================================== #

def _prune_empty_dirs(rels) -> None:
    """Убирает опустевшие после удаления файлов папки (только внутри проекта, снизу вверх)."""
    dirs = {ROOT / Path(r).parent for r in rels}
    for d in sorted(dirs, key=lambda p: len(p.parts), reverse=True):
        while d != ROOT and ROOT in d.parents:
            try:
                d.rmdir()
            except OSError:
                break
            d = d.parent


def _apply_code_from_dir(src: Path, removed=()) -> None:
    """Ставит ВСЕ файлы кода из src поверх проекта, удаляет removed (код старой версии, которого нет в новой), пишет манифест."""
    files = scan_code_files(src)
    for rel, f in files.items():
        dst = ROOT / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.is_dir() and not dst.is_symlink():
            rmtree(dst)
        shutil.copy2(f, dst)
    for rel in removed:
        if rel in files or not is_code_rel(rel):
            continue
        try:
            (ROOT / rel).unlink()
        except FileNotFoundError:
            pass
    _prune_empty_dirs(removed)
    write_manifest(ROOT, {rel: ROOT / rel for rel in files})


def make_code_backup(version: str, incoming: dict = None) -> Path:
    """
    Копия кода перед обновлением: код установки + всё, что пакет перезапишет. В backup.json — список файлов,
    которые обновление СОЗДАСТ (их при откате нужно удалить), и был ли манифест.
    """
    dst = BACKUP_DIR / f"code_{_stamp()}_v{_safe_ver(version)}.zip"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    files = installed_code_files(ROOT)
    created = []
    for rel in (incoming or {}):
        if (ROOT / rel).is_file():
            files.setdefault(rel, ROOT / rel)
        else:
            created.append(rel)
    with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
        zf.writestr('backup.json', json.dumps({
            'type': 'frx-code-backup', 'proto': PROTO, 'frx_version': str(version),
            'created': datetime.now().isoformat(timespec='seconds'), 'created_files': sorted(created),
        }))
        for rel, f in files.items():
            zf.write(f, rel)
        if (ROOT / MANIFEST_NAME).is_file():
            zf.write(ROOT / MANIFEST_NAME, MANIFEST_NAME)
    prune_backups('code_')
    return dst


def restore_code_backup(zip_path) -> None:
    """Возвращает код из копии: файлы копии — на место, созданные обновлением — удаляются, манифест — прежний."""
    with zipfile.ZipFile(zip_path) as zf:
        try:
            meta = json.loads(zf.read('backup.json'))
        except (KeyError, ValueError):
            meta = {}
        entries, manifest = {}, None
        for info in zf.infolist():
            rel = _safe_rel(info.filename)
            if info.is_dir() or rel is None or rel == 'backup.json':
                continue
            if rel == MANIFEST_NAME:
                manifest = info
            elif is_code_rel(rel):
                entries[rel] = info
        if 'core/engine.py' not in entries:
            raise KeeperError("Резервная копия кода повреждена (нет core/engine.py).")
        created = [r for r in meta.get('created_files') or [] if is_code_rel(r) and r not in entries]
        for rel in created:
            try:
                (ROOT / rel).unlink()
            except OSError:
                pass
        _prune_empty_dirs(created)
        for rel, info in entries.items():
            dst = ROOT / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(zf.read(info))
        if manifest is not None:
            (ROOT / MANIFEST_NAME).write_bytes(zf.read(manifest))
        else:
            try:
                (ROOT / MANIFEST_NAME).unlink()
            except OSError:
                pass


def _install_requirements() -> dict:
    deps = ROOT / 'frx_deps.py'
    cmd = ([sys.executable, str(deps), '--force', '--no-tools'] if deps.is_file()      # ядро + пакеты модулей
           else [sys.executable, '-m', 'pip', 'install', '-r', 'requirements.txt', '--disable-pip-version-check'])
    try:
        r = subprocess.run(
            cmd,
            cwd=ROOT, capture_output=True, text=True, encoding='utf-8',
            errors='replace', timeout=900,
        )
        tail = '\n'.join((r.stdout + '\n' + r.stderr).strip().splitlines()[-8:])
        return {'ran': True, 'ok': r.returncode == 0, 'tail': tail}
    except Exception as e:
        return {'ran': True, 'ok': False, 'tail': str(e)}


def _write_result(req: dict, ok: bool, **extra) -> None:
    data = {
        'proto': PROTO, 'action': (req or {}).get('action', 'restart'), 'ok': ok,
        'rolled_back': False, 'error': None,
        'notify': (req or {}).get('notify') or {},
        'meta': (req or {}).get('meta') or {},
        'created': (req or {}).get('created'), 'finished': time.time(),
    }
    data.update(extra)
    write_json(RESULT_FILE, data)


def apply_update(req: dict):
    """Меняет код на пакет из staging. Возвращает pending-запись для проверки запуска или None."""
    started = time.time()
    if not STAGING_DIR.is_dir():
        raise KeeperError("Пакет обновления не найден (staging пуст).")
    missing = [r for r in REQUIRED_FILES if not (STAGING_DIR / r).is_file()]
    if missing:
        raise KeeperError("В staging не хватает файлов: " + ", ".join(missing))

    old_version = read_engine_version(ROOT) or '?'
    new_version = read_engine_version(STAGING_DIR) or '?'
    diff = code_diff(ROOT, STAGING_DIR)
    old_req = ROOT / 'requirements.txt'
    req_changed = (not old_req.is_file()) or _sha(old_req) != _sha(STAGING_DIR / 'requirements.txt')

    backup = make_code_backup(old_version, scan_code_files(STAGING_DIR))
    log(f"Резервная копия кода: {backup.name}")
    write_json(JOURNAL_FILE, {'kind': 'code', 'backup': str(backup)})
    try:
        log(f"Заменяю файлы: {old_version} -> {new_version} (новых {len(diff['added'])}, изменённых "
            f"{len(diff['changed'])}, удаляемых {len(diff['removed'])})")
        _apply_code_from_dir(STAGING_DIR, diff['removed'])
        pip = _install_requirements() if req_changed else {'ran': False}
        if pip.get('ran'):
            log("pip install -r requirements.txt: " + ("ок" if pip['ok'] else "ОШИБКА"))
    except Exception as e:
        log(f"Ошибка при замене файлов: {e} — откатываюсь")
        restore_code_backup(backup)
        JOURNAL_FILE.unlink(missing_ok=True)
        _write_result(req, False, rolled_back=True, error=f"Ошибка при замене файлов: {e}",
                      backup=backup.name, **{'from': old_version, 'to': new_version})
        cleanup_work_dirs()
        return None

    JOURNAL_FILE.unlink(missing_ok=True)
    rmtree(STAGING_DIR)
    REQUEST_FILE.unlink(missing_ok=True)
    _write_result(req, True, backup=backup.name, pip=pip, duration=round(time.time() - started, 1),
                  **{'from': old_version, 'to': new_version}, **diff)
    return {'kind': 'code', 'zip': str(backup), 'req': req,
            'from': old_version, 'to': new_version}


def apply_restore(req: dict):
    """Заменяет пользовательские данные бэкапом из restore/. Возвращает pending-запись."""
    started = time.time()
    if not RESTORE_DIR.is_dir():
        raise KeeperError("Бэкап для восстановления не найден.")
    version = read_engine_version(ROOT) or '?'
    safety = make_user_backup(BACKUP_DIR / f"userdata_prerestore_{_stamp()}_v{_safe_ver(version)}.zip",
                              version)['path']
    prune_backups('userdata_prerestore_')
    log(f"Страховочная копия текущих данных: {safety.name}")
    write_json(JOURNAL_FILE, {'kind': 'userdata', 'backup': str(safety)})
    try:
        _apply_user_dir(RESTORE_DIR)
        _fix_restored_paths()
    except Exception as e:
        log(f"Ошибка при восстановлении: {e} — откатываюсь")
        _rollback_userdata(safety)
        JOURNAL_FILE.unlink(missing_ok=True)
        _write_result(req, False, rolled_back=True, error=f"Ошибка при восстановлении: {e}",
                      backup=safety.name)
        cleanup_work_dirs()
        return None
    JOURNAL_FILE.unlink(missing_ok=True)
    rmtree(RESTORE_DIR)
    REQUEST_FILE.unlink(missing_ok=True)
    _write_result(req, True, backup=safety.name, duration=round(time.time() - started, 1),
                  meta={**(req.get('meta') or {})})
    return {'kind': 'userdata', 'zip': str(safety), 'req': req}


def _fix_restored_paths() -> None:
    """Бэкап старой раскладки хранит в реестре файлов пути data/... — переводим их в папку пользователя."""
    if LEGACY:
        return
    db_file = DATA_DIR / 'database.json'
    if not db_file.is_file():
        return
    try:
        rel = USER_DIR.relative_to(ROOT).as_posix() + '/'
    except ValueError:
        rel = USER_DIR.as_posix().rstrip('/') + '/'
    _rewrite_db_paths(db_file, 'data/', rel + 'data/')


def _rollback_userdata(zip_path) -> None:
    tmp = TMP_DIR / 'rollback_userdata'
    _extract_user_backup(zip_path, tmp)
    try:
        _apply_user_dir(tmp)
    finally:
        rmtree(tmp)


def rollback_pending(pending: dict, exit_code) -> bool:
    """Откат после неудачного запуска новой версии / восстановленных данных."""
    req = pending.get('req') or {}
    try:
        if pending['kind'] == 'code':
            restore_code_backup(pending['zip'])
        else:
            _rollback_userdata(pending['zip'])
    except Exception as e:
        log(f"ОТКАТ НЕ УДАЛСЯ: {e}")
        _write_result(req, False, rolled_back=False,
                      error=f"Новая версия не запустилась, откат не удался: {e}",
                      **{'from': pending.get('from'), 'to': pending.get('to')})
        return False
    _write_result(req, False, rolled_back=True,
                  error=f"Новая версия не запустилась (код выхода {exit_code}) — выполнен откат.",
                  backup=Path(pending['zip']).name,
                  **{'from': pending.get('from'), 'to': pending.get('to')})
    return True


def recover_interrupted() -> None:
    """Если прошлый keeper умер посреди замены файлов — откатывает по journal.json."""
    journal = read_json(JOURNAL_FILE)
    if not journal:
        return
    log("Найдена прерванная замена файлов — откатываю изменения")
    backup = Path(str(journal.get('backup', '')))
    if not backup.is_file() and (BACKUP_DIR / backup.name).is_file():
        journal['backup'] = str(BACKUP_DIR / backup.name)      # копия переехала вместе с данными в user/
    try:
        if journal.get('kind') == 'code':
            restore_code_backup(journal['backup'])
        else:
            _rollback_userdata(journal['backup'])
        log("Откат выполнен")
    except Exception as e:
        log(f"Откат прерванной замены не удался: {e}")
    JOURNAL_FILE.unlink(missing_ok=True)
    cleanup_work_dirs()



# ===================================================================== #
# сайт .web переживает перезагрузку юзербота
# ===================================================================== #
#
# При .restart / .update main.py не гасит сайт «в никуда»: он пишет user/data/keeper/web_state.json
# (порт, остаток таймера простоя) и закрывает порт в самом конце. keeper сразу занимает этот порт
# лёгкой заглушкой на стандартной библиотеке: страница в браузере остаётся открытой, статика отдаётся,
# а любой /api отвечает 503 {"sleeping": true} — сайт показывает «юзербот спит», а нажатия на действия
# подсвечивает красным. Новый main.py после старта просит порт файлом web_takeover — заглушка
# уходит, и сайт продолжает работу уже с живым юзерботом.

def _make_handler(outer):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'FRXKeeper'

        def log_message(self, *args):
            pass

        def _host_ok(self):
            host = self.headers.get('Host', '')
            name = host.split(']')[0] + ']' if host.startswith('[') else host.rsplit(':', 1)[0] if ':' in host else host
            return name.lower() in web_allowed_hosts()

        def _send(self, code, body, ctype, extra=None):
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('X-Frame-Options', 'DENY')
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(body)

        def _handle(self):
            if not self._host_ok():
                return self._send(403, b'forbidden host', 'text/plain')
            path = self.path.split('?', 1)[0]
            if getattr(outer, 'kind', '') == 'sentinel':
                return outer.respond(self, path)
            if path.startswith('/api/'):
                if self.headers.get('X-FRX') != '1':
                    return self._send(403, b'{"error": "forbidden"}', 'application/json')
                outer._touch()
                body = json.dumps({'sleeping': True, 'version': outer._state.get('version'),
                                   'error': 'юзербот перезапускается'}, ensure_ascii=False).encode('utf-8')
                return self._send(503, body, 'application/json; charset=utf-8')
            try:
                if path == '/':
                    return self._send(200, (WEB_STATIC_DIR / 'index.html').read_bytes(), 'text/html; charset=utf-8')
                if path.startswith('/static/') and path[8:] in WEB_STATIC_FILES:
                    name = path[8:]
                    ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
                    if name.endswith('.woff2'):
                        ctype = 'font/woff2'
                    return self._send(200, (WEB_STATIC_DIR / name).read_bytes(), ctype)
            except OSError:
                return self._send(503, b'updating', 'text/plain')
            return self._send(404, b'not found', 'text/plain')

        do_GET = do_HEAD = do_POST = _handle
    return Handler


class WebPlaceholder:
    def __init__(self):
        self.server = None
        self._stop = threading.Event()
        self._state = {}
        self._born = time.time()

    @staticmethod
    def wanted():
        st = read_json(WEB_STATE_FILE)
        if not isinstance(st, dict) or not st.get('port'):
            return None
        if time.time() - float(st.get('saved', 0)) > WEB_HOLD_MAX:
            return None
        return st

    def start(self) -> bool:
        st = self.wanted()
        if st is None or self.server is not None:
            return False
        self._state = st
        outer = self
        WEB_TAKEOVER_FILE.unlink(missing_ok=True)

        Handler = _make_handler(outer)

        try:
            self.server = ThreadingHTTPServer((web_bind_host(), int(st['port'])), Handler)
        except OSError as e:
            log(f"Заглушка сайта: порт {st['port']} занят ({e}) — пропускаю")
            self.server = None
            return False
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        threading.Thread(target=self._watch, daemon=True).start()
        log(f"Сайт .web удерживается заглушкой на порту {st['port']} (юзербот перезагружается)")
        return True

    def _touch(self):
        """Заход на сайт во время сна продлевает таймер простоя нового процесса."""
        st = self._state
        if st.get('idle_left') is not None and st.get('limit'):
            st['saved'], st['idle_left'] = time.time(), st['limit']
            try:
                write_json(WEB_STATE_FILE, st)
            except OSError:
                pass

    def _watch(self):
        while not self._stop.is_set():
            if WEB_TAKEOVER_FILE.exists() or time.time() - self._born > WEB_HOLD_MAX:
                break
            time.sleep(0.1)
        self.stop()

    def stop(self):
        self._stop.set()
        srv, self.server = self.server, None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
            log("Заглушка сайта .web снята")

# ===================================================================== #
# сторож сайта и гибернация
# ===================================================================== #
#
# Сторож — та же лёгкая заглушка на 127.0.0.1:порт_сайта, но живёт ВСЕГДА, пока сайт .web выключен: она ничего не
# показывает, кроме крошечной страницы «запускаю…», зато первый заход на «/» пишет файл web_wake — юзербот видит его,
# поднимает настоящий сайт на этом же порту (через web_takeover сторож уходит), а страница сама перезагружается.
# Когда сайт выключается (кнопка, таймер простоя, .web stop), порт возвращается сторожу. Отключить: "web_sentinel": false
# в user/config.json.
#
# Гибернация: main.py вышел с кодом EXIT_HIBERNATE (нет связи с Telegram) — keeper делает резервную копию
# (user/data/backup/offline_*.zip), юзербот остаётся выключенным, а keeper каждые "hibernate_probe_seconds" секунд (user/config.json,
# по умолчанию 7) стучится к Telegram; как только связь вернулась — юзербот стартует сам.

def read_config() -> dict:
    cfg = read_json(CONFIG_PATH, {})
    return cfg if isinstance(cfg, dict) else {}


def tg_reachable(timeout: float = 4.0) -> bool:
    """TCP-рукопожатие с серверами Telegram (дата-центры по IP и api.telegram.org) — без обращения к API."""
    for host in (('149.154.167.51', 443), ('149.154.175.53', 443), ('api.telegram.org', 443)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


_WAKE_PAGE = """<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FRX</title><style>html,body{height:100%;margin:0;background:#1d3f86;color:#fff;font:400 18px system-ui,sans-serif}
body{display:grid;place-items:center;text-align:center}small{display:block;opacity:.65;margin-top:10px;font-size:14px}</style></head>
<body><div>@TITLE@<small>@NOTE@</small></div><script>setTimeout(function(){location.reload()},@MS@)</script></body></html>"""


def _wake_page(title, note, ms):
    return _WAKE_PAGE.replace('@TITLE@', title).replace('@NOTE@', note).replace('@MS@', str(ms))


class WebSentinel(WebPlaceholder):
    kind = 'sentinel'

    def __init__(self):
        super().__init__()
        self.mode = 'wake'                 # wake — юзербот работает; hibernate — нет связи с Telegram
        self.paused_until = 0.0
        self._quit = threading.Event()
        self._thread = None
        self._probe = 7

    def wanted(self):
        return {'port': self.port(), 'saved': time.time()}

    @staticmethod
    def port() -> int:
        try:
            return int(read_config().get('web_port') or WEB_DEFAULT_PORT)
        except (TypeError, ValueError):
            return WEB_DEFAULT_PORT

    def enabled(self) -> bool:
        return read_config().get('web_sentinel', True) is not False

    def request_wake(self):
        try:
            if not WEB_WAKE_FILE.exists():
                WEB_WAKE_FILE.parent.mkdir(parents=True, exist_ok=True)
                WEB_WAKE_FILE.write_text(str(time.time()), encoding='utf-8')
                log("Заход на сайт — прошу юзербот поднять сайт")
        except OSError:
            pass

    def respond(self, h, path):
        if path.startswith('/api/'):
            if h.headers.get('X-FRX') != '1':
                return h._send(403, b'{"error": "forbidden"}', 'application/json')
            body = json.dumps({'sleeping': True, 'waking': self.mode == 'wake', 'hibernating': self.mode == 'hibernate',
                               'error': 'юзербот запускает сайт' if self.mode == 'wake' else 'нет связи с Telegram — юзербот в гибернации'},
                              ensure_ascii=False).encode('utf-8')
            return h._send(503, body, 'application/json; charset=utf-8')
        if path == '/':
            if self.mode == 'wake':
                self.request_wake()
                page = _wake_page('Запускаю сайт FRX…', 'страница обновится сама', 1200)
            else:
                page = _wake_page('Нет связи с Telegram', f'юзербот в гибернации — проверяю сеть каждые {self._probe:g} с и запущусь сам', 4000)
            return h._send(200, page.encode('utf-8'), 'text/html; charset=utf-8')
        return h._send(404, b'not found', 'text/plain')

    def _bind(self) -> bool:
        port = self.port()
        try:
            srv = ThreadingHTTPServer((web_bind_host(), port), _make_handler(self))
        except OSError:
            return False
        srv.daemon_threads = True
        self.server = srv
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            write_json(SENTINEL_FILE, {'port': port, 'ts': time.time()})
        except OSError:
            pass
        log(f"Сторож сайта: слушаю {web_bind_host()}:{port} (заход на страницу поднимет сайт)")
        return True

    def _release(self):
        srv, self.server = self.server, None
        if srv is not None:
            try:
                srv.shutdown()
                srv.server_close()
            except Exception:
                pass
        SENTINEL_FILE.unlink(missing_ok=True)

    def _loop(self):
        while not self._quit.is_set():
            try:
                if self.server is not None:
                    if WEB_TAKEOVER_FILE.exists():               # юзербот просит порт — отдаём и ждём, пока он займёт
                        self._release()
                        self.paused_until = time.time() + 25
                        log("Сторож сайта: порт передан юзерботу")
                    else:
                        write_json(SENTINEL_FILE, {'port': self.port(), 'ts': time.time()})
                elif time.time() >= self.paused_until and not WEB_STATE_FILE.exists() and not WEB_TAKEOVER_FILE.exists() and self.enabled():
                    self._bind()
            except Exception as e:
                log(f"Сторож сайта: {e}")
            self._quit.wait(1.0)
        self._release()

    def run(self):
        if self._thread is None or not self._thread.is_alive():
            self._quit.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def close(self):
        self._quit.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
        self._release()


def hibernate(sentinel: WebSentinel, version: str) -> bool:
    """Нет связи: копия данных, затем ожидание сети. True — связь вернулась (запускать юзербот), False — прервано Ctrl+C."""
    log("Нет связи с Telegram — делаю резервную копию и ухожу в гибернацию")
    try:
        dst = BACKUP_DIR / f"offline_{_stamp()}_v{_safe_ver(version)}.zip"
        r = make_user_backup(dst, version)
        prune_backups('offline_')
        log(f"Резервная копия: {Path(r['path']).name} ({r['files']} файлов)")
    except Exception as e:
        log(f"Копию сделать не удалось: {e}")
    try:
        interval = max(2.0, min(600.0, float(read_config().get('hibernate_probe_seconds', 7))))
    except (TypeError, ValueError):
        interval = 7.0
    sentinel._probe = interval
    sentinel.mode = 'hibernate'
    log(f"Гибернация: юзербот выключен, keeper проверяет связь каждые {interval:g} с")
    try:
        while True:
            time.sleep(interval)
            if tg_reachable():
                log("Связь с Telegram вернулась — запускаю юзербот")
                return True
    except KeyboardInterrupt:
        return False
    finally:
        sentinel.mode = 'wake'


# ===================================================================== #
# Основной цикл
# ===================================================================== #

def ensure_deps() -> None:
    """Перед запуском: всё, что нужно скачать (пакеты ядра и модулей, ffmpeg, Ollama, если выбрана), — докачать.
    Если всё на месте, frx_deps.py заканчивает за доли секунды. FRX_SKIP_DEPS=1 — не проверять."""
    deps = ROOT / 'frx_deps.py'
    if os.environ.get('FRX_SKIP_DEPS') == '1' or not deps.is_file():
        return
    try:
        r = subprocess.run([sys.executable, str(deps), '--auto'], cwd=ROOT, timeout=3 * 3600)
        if r.returncode != 0:
            log("Зависимости поставились не полностью (см. выше) — пробую запустить как есть")
    except Exception as e:
        log(f"Проверка зависимостей не удалась: {e}")


def _start_main() -> subprocess.Popen:
    env = os.environ.copy()
    ff = BIN_DIR / 'ffmpeg' if BIN_DIR else None          # свой ffmpeg/ffprobe (frx_deps.py) — виден всем: yt-dlp, py-tgcalls, модулям
    if ff and ff.is_dir() and str(ff) not in env.get('PATH', '').split(os.pathsep):
        env['PATH'] = str(ff) + os.pathsep + env.get('PATH', '')
    env.update({
        'FRX_KEEPER': '1',
        'FRX_KEEPER_PID': str(os.getpid()),
        'FRX_KEEPER_VERSION': KEEPER_VERSION,
        'FRX_KEEPER_READY': str(READY_FILE),
    })
    return subprocess.Popen([sys.executable, '-u', 'main.py'], cwd=ROOT, env=env)


_TERM_RECEIVED = False


def _on_term(signum, frame):
    """SIGTERM (systemctl stop, docker stop) — останавливаемся так же аккуратно, как по Ctrl+C."""
    global _TERM_RECEIVED
    _TERM_RECEIVED = True
    raise KeyboardInterrupt


def _signal_child(proc: subprocess.Popen) -> None:
    """Ctrl+C терминал шлёт всей группе процессов, а SIGTERM — только keeper'у: передаём его main.py как Ctrl+C."""
    if proc.poll() is not None:
        return
    try:
        if os.name == 'nt':
            proc.terminate()
        else:
            proc.send_signal(signal.SIGINT)
    except OSError:
        pass


def _stop_child(proc: subprocess.Popen) -> None:
    try:
        proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        log("main.py не завершился за 15 сек — принудительная остановка")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _handle_exit_request(code: int):
    """Обрабатывает EXIT_RESTART / EXIT_APPLY. Возвращает pending-запись или None."""
    req = read_json(REQUEST_FILE) or {}
    action = req.get('action') if code == EXIT_APPLY else 'restart'
    try:
        if action == 'update':
            return apply_update(req)
        if action == 'restore':
            return apply_restore(req)
        if code == EXIT_APPLY:
            log("Получен EXIT_APPLY без корректной заявки — обычный перезапуск")
        _write_result({**req, 'action': 'restart'}, True)
        REQUEST_FILE.unlink(missing_ok=True)
    except Exception as e:
        log(f"Не удалось применить заявку '{action}': {e}")
        _write_result(req, False, error=str(e))
        cleanup_work_dirs()
    return None


# ===================================================================== #
# Публичный туннель сайта (cloudflared quick tunnel)
# ===================================================================== #
#
# Чтобы открыть сайт .web не только с этой машины (сервер без SSH, телефон, Telegram Mini App), keeper поднимает
# «быстрый туннель» Cloudflare: программа cloudflared соединяется с Cloudflare и получает случайный адрес
# https://<слова>.trycloudflare.com, который ведёт на 127.0.0.1:<порт сайта>. Домен, аккаунт Cloudflare и открытые
# порты не нужны. Туннель живёт в keeper'е, поэтому адрес не меняется при .restart / .update; заходы по нему видит
# и заглушка «юзербот спит», и сторож, который поднимает выключенный сайт. Сайт при этом требует входа (core/web/auth.py).
#
# Управление — файлами: юзербот пишет tunnel_request.json {enable, port, renew}, keeper отвечает в tunnel.json
# {status: off|downloading|starting|up|error, url, host, port, error}. cloudflared ищется в PATH, затем в
# user/data/bin/; если его нет — скачивается с GitHub (официальные сборки Cloudflare) под текущую ОС и процессор.
# Если Cloudflare не выдаёт туннель (сеть, лимиты) два раза подряд, keeper пробует запасной вариант — localhost.run
# через обычный ssh-клиент (адрес https://….lhr.life), и потом снова Cloudflare.

TUNNEL_URL_RE = re.compile(r'https://(?!api\.)[a-z0-9-]+\.trycloudflare\.com')
LHR_URL_RE = re.compile(r'https://[a-z0-9-]+\.(?:lhr\.life|lhr\.pro|localhost\.run)')
LHR_KEY_NAME = 'lhr_key'                       # ключ SSH для localhost.run (user/data/bin/lhr_key[.pub]), см. .web tunnel setup
#
# Способы публичного адреса (tunnel_request.json → mode), выбираются в Telegram: .web tunnel setup
#   auto        Cloudflare quick tunnel, при сбоях — localhost.run (без регистрации; адрес меняется)
#   cloudflare  только Cloudflare quick tunnel
#   lhr         только localhost.run; с ключом (lhr_key: true), добавленным на admin.localhost.run, адрес живёт дольше
#   cf_token    именованный туннель Cloudflare (cloudflared tunnel run --token …) на своём домене — адрес постоянный
#   custom      адрес поднимает что-то другое (Tailscale Funnel, свой сервер, nginx…) — keeper только проверяет его
TUNNEL_MODES = ('auto', 'cloudflare', 'lhr', 'cf_token', 'custom')
CLOUDFLARED_BASE = 'https://github.com/cloudflare/cloudflared/releases/latest/download/'


def _cloudflared_asset():
    import platform
    m = platform.machine().lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'x64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64',
            'armv7l': 'arm', 'armv6l': 'arm', 'arm': 'arm', 'i386': '386', 'i686': '386', 'x86': '386'}.get(m)
    if sys.platform.startswith('linux'):
        if not arch:
            raise KeeperError(f"cloudflared: неизвестный процессор {m!r} — поставьте cloudflared вручную (он должен быть в PATH)")
        return f'cloudflared-linux-{arch}', None
    if sys.platform == 'darwin':
        return f"cloudflared-darwin-{'arm64' if arch == 'arm64' else 'amd64'}.tgz", 'tgz'
    if os.name == 'nt':
        return f"cloudflared-windows-{'386' if arch == '386' else 'amd64'}.exe", None
    raise KeeperError(f"cloudflared: система {sys.platform} не поддерживается — поставьте cloudflared вручную")


def find_cloudflared():
    found = shutil.which('cloudflared')
    if found:
        return found
    exe = BIN_DIR / ('cloudflared.exe' if os.name == 'nt' else 'cloudflared')
    return str(exe) if exe.is_file() else None


def download_cloudflared() -> str:
    asset, kind = _cloudflared_asset()
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    exe = BIN_DIR / ('cloudflared.exe' if os.name == 'nt' else 'cloudflared')
    tmp = BIN_DIR / (asset + '.part')
    log(f"Туннель: скачиваю cloudflared ({asset})…")
    req = urllib.request.Request(CLOUDFLARED_BASE + asset, headers={'User-Agent': f'FRX-Keeper/{KEEPER_VERSION}'})
    size = 0
    with urllib.request.urlopen(req, timeout=60) as r, open(tmp, 'wb') as f:
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > 150 * 1024 * 1024:
                raise KeeperError("cloudflared: слишком большой файл")
            f.write(chunk)
    if kind == 'tgz':
        import tarfile
        with tarfile.open(tmp) as tf:
            member = next((m for m in tf.getmembers() if Path(m.name).name == 'cloudflared' and m.isfile()), None)
            if member is None:
                raise KeeperError("cloudflared: в архиве нет программы")
            exe.write_bytes(tf.extractfile(member).read())
        tmp.unlink(missing_ok=True)
    else:
        os.replace(tmp, exe)
    if os.name != 'nt':
        os.chmod(exe, 0o755)
    log(f"Туннель: cloudflared сохранён в {exe}")
    return str(exe)


class TunnelManager:
    RETRY = 8

    def __init__(self):
        self.proc = None
        self.port = None
        self.renew = None
        self._quit = threading.Event()
        self._next_try = 0.0
        self._tail = []
        self._fails = 0
        self._attempt = 0                      # номер попытки с момента включения — юзербот показывает его владельцу
        self.provider = 'cloudflare'
        self.mode = 'auto'                     # способ публичного адреса (TUNNEL_MODES), из tunnel_request.json
        self.setup = None
        self._fixed_url = None

    CONNECT_TIMEOUT = 40                       # адрес выдан, но соединения с Cloudflare нет столько секунд — новая попытка

    def state(self, **kw):
        data = {'ts': time.time(), 'attempt': self._attempt, 'provider': self.provider, 'mode': getattr(self, 'mode', 'auto'), **kw}
        if kw.get('status') == 'error' and self._next_try:
            data['next_try'] = self._next_try
        try:
            write_json(TUNNEL_STATE_FILE, data)
        except OSError:
            pass

    def run(self):
        if TUNNEL_STATE_FILE is not None:
            TUNNEL_STATE_FILE.unlink(missing_ok=True)
        threading.Thread(target=self._loop, daemon=True).start()
        import atexit
        atexit.register(self.close)

    def close(self):
        self._quit.set()
        self._kill()

    def _kill(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            try:
                p.terminate()
                p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass

    def _loop(self):
        while not self._quit.is_set():
            try:
                self._tick()
                self._check()
            except Exception as e:
                log(f"Туннель: {e}")
                self._kill()
                self._next_try = time.time() + self.RETRY
                self.state(status='error', error=str(e), port=self.port)
            self._quit.wait(1.0)

    def _tick(self):
        req = read_json(TUNNEL_REQUEST_FILE) or {}
        want = bool(req.get('enable'))
        if not want:
            self._attempt = 0
            if self.proc is not None:
                self._kill()
                log("Туннель выключен")
            cur = read_json(TUNNEL_STATE_FILE) or {}
            if cur.get('status') not in (None, 'off'):
                self.state(status='off')
            return
        port = int(req.get('port') or WebSentinel.port())
        renew = req.get('renew')
        mode = req.get('mode') if req.get('mode') in TUNNEL_MODES else 'auto'
        setup = (mode, bool(req.get('lhr_key')), str(req.get('cf_token') or ''), str(req.get('cf_host') or ''),
                 str(req.get('custom_url') or ''))
        alive = self.proc is not None and self.proc.poll() is None
        # что из настроек важно для выбранного способа: токен Cloudflare, вписанный «на будущее», не перезапускает
        # работающий авто-туннель (иначе адрес сменился бы зря)
        relevant = {'auto': setup[:2], 'cloudflare': setup[:1], 'lhr': setup[:2], 'cf_token': (mode,) + setup[2:4],
                    'custom': (mode, setup[4])}[mode]
        new_setup = relevant != getattr(self, '_relevant', None)
        self._relevant = relevant
        changed = port != self.port or renew != self.renew or new_setup
        if changed:                                  # новый порт, «новый адрес» или другой способ — пробуем сразу
            self._next_try, self._streak, self._fails = 0.0, 0, 0
            if renew != self.renew or new_setup:
                self._attempt = 0
            if new_setup:
                self.mode = mode
                self.provider = {'lhr': 'localhost.run', 'cf_token': 'cloudflare-token', 'custom': 'custom'}.get(mode, 'cloudflare')
                self._custom_ok_at = 0.0
                if getattr(self, 'setup', None) is not None:
                    log(f"Туннель: способ публичного адреса — {mode}")
        self.setup = setup
        if alive and changed:
            self._kill()
            alive = False
        if self.mode == 'custom':
            self.port, self.renew = port, renew
            self._custom_tick(setup[4], port, force=changed)
            return
        if alive:
            return
        if self.proc is not None:                    # процесс умер сам
            code = self.proc.poll()
            self.proc = None
            last = self._tail[-1] if self._tail else ''
            self._fails += 1
            self._streak = getattr(self, '_streak', 0) + 1
            log(f"Туннель ({self.provider}): процесс завершился (код {code}) {last}")
            err = f"{self.provider}: процесс завершился (код {code}). {last}".strip()
            if self.mode == 'auto' and self._fails >= 2 and shutil.which('ssh'):
                self.provider = 'localhost.run' if self.provider == 'cloudflare' else 'cloudflare'
                self._fails = 0
                log(f"Туннель: пробую {self.provider}")
                self._next_try = time.time() + 2
            else:
                self._next_try = time.time() + min(300, self.RETRY * 2 ** min(6, self._streak - 1))
            self.state(status='error', error=err, port=port)
            return
        if time.time() < self._next_try:
            return
        self.port, self.renew = port, renew
        self._attempt += 1
        if self.provider in ('cloudflare', 'cloudflare-token'):
            if self.provider == 'cloudflare-token' and not (self.setup[2] and self.setup[3]):
                raise KeeperError("именованный туннель Cloudflare: не заданы токен и адрес (.web tunnel setup)")
            exe = find_cloudflared()
            if not exe:
                self.state(status='downloading', port=port, provider=self.provider)
                exe = download_cloudflared()
            # http2 (TCP) вместо QUIC (UDP): на многих серверах и в сетях UDP закрыт — тогда QUIC-соединение не
            # поднимается, а адрес отвечает ошибкой 1033
            if self.provider == 'cloudflare-token':
                token, host = self.setup[2], self.setup[3]
                if not token or not host:
                    raise KeeperError("именованный туннель Cloudflare: не заданы токен и адрес (.web tunnel setup)")
                # куда вести адрес (http://127.0.0.1:<порт>) задаётся в панели Cloudflare у самого туннеля
                cmd = [exe, 'tunnel', '--no-autoupdate', '--protocol', 'http2', 'run', '--token', token]
                self._fixed_url = 'https://' + host.strip().strip('/').removeprefix('https://').removeprefix('http://')
            else:
                cmd = [exe, 'tunnel', '--no-autoupdate', '--protocol', 'http2', '--url', f'http://127.0.0.1:{port}']
        else:
            if not shutil.which('ssh'):
                raise KeeperError("localhost.run: нужен ssh-клиент (на Windows — компонент OpenSSH Client)")
            known = BIN_DIR / 'ssh_known_hosts'
            BIN_DIR.mkdir(parents=True, exist_ok=True)
            key = BIN_DIR / LHR_KEY_NAME
            cmd = ['ssh', '-T', '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={known}',
                   '-o', 'ServerAliveInterval=30', '-o', 'ExitOnForwardFailure=yes', '-o', 'BatchMode=yes']
            if self.setup[1] and key.is_file():        # свой ключ, добавленный на admin.localhost.run — адрес держится дольше
                cmd += ['-i', str(key), '-o', 'IdentitiesOnly=yes', '-R', f'80:127.0.0.1:{port}', 'frx@localhost.run']
            else:
                cmd += ['-R', f'80:127.0.0.1:{port}', 'nokey@localhost.run']
        self.state(status='starting', port=port, provider=self.provider)
        self._tail = []
        flags = {'creationflags': 0x08000000} if os.name == 'nt' else {}     # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding='utf-8', errors='replace', **flags)
        log(f"Туннель ({self.provider}): запускаю → 127.0.0.1:{port}")
        threading.Thread(target=self._read, args=(self.proc, port), daemon=True).start()

    def _read(self, proc, port):
        # cloudflared печатает адрес СРАЗУ после выдачи, а соединение с Cloudflare поднимает позже — до строки
        # «Registered tunnel connection» адрес отвечает ошибкой 1033. Поэтому: адрес → connecting, соединение → up.
        url = None
        for line in proc.stdout:
            line = line.strip()
            if not line or self.proc is not proc:
                continue
            self._tail = (self._tail + [line[-200:]])[-8:]
            if self.provider == 'cloudflare-token':          # адрес известен заранее — ждём только соединения
                if 'Registered tunnel connection' in line:
                    cur = read_json(TUNNEL_STATE_FILE) or {}
                    if cur.get('status') != 'up':
                        self._mark_up(proc, self._fixed_url, port)
                elif url is None:
                    url = self._fixed_url
                    self.state(status='connecting', url=url, host=urllib.parse.urlparse(url).hostname, port=port,
                               pid=proc.pid, provider=self.provider)
                continue
            m = (TUNNEL_URL_RE if self.provider == 'cloudflare' else LHR_URL_RE).search(line)
            if m and m.group(0) != url:
                url = m.group(0)
                if self.provider == 'cloudflare':
                    log(f"Туннель: получен адрес {url}, жду соединения с Cloudflare…")
                    self.state(status='connecting', url=url, host=urllib.parse.urlparse(url).hostname, port=port,
                               pid=proc.pid, provider=self.provider)
                    continue
                self._mark_up(proc, url, port)          # localhost.run: адрес печатается, когда всё уже работает
            elif url and self.provider == 'cloudflare' and 'Registered tunnel connection' in line:
                cur = read_json(TUNNEL_STATE_FILE) or {}
                if cur.get('status') != 'up':
                    self._mark_up(proc, url, port)

    def _mark_up(self, proc, url, port):
        self._fails = self._streak = 0
        self._bad_checks = 0
        self._up_at = time.time()
        log(f"Туннель работает ({self.provider}): {url}")
        prev = read_json(TUNNEL_STATE_FILE) or {}
        extra = {}
        if prev.get('status') == 'up' and prev.get('url') and prev.get('url') != url:
            extra = {'prev_url': prev['url'], 'prev_host': prev.get('host')}      # открытые страницы переедут сами
        self.state(status='up', url=url, host=urllib.parse.urlparse(url).hostname, port=port,
                   since=time.time(), pid=proc.pid, provider=self.provider, **extra)

    def _custom_tick(self, url, port, force=False):
        """Способ «свой адрес»: туннель держит не keeper (Tailscale Funnel, свой сервер…), keeper раз в минуту
        проверяет, что адрес отвечает, и сообщает его юзерботу."""
        if self.proc is not None:
            self._kill()
        url = (url or '').strip().rstrip('/')
        if not re.match(r'^https?://[^\s/]+', url):
            if force:
                self.state(status='error', error='свой адрес не задан (.web tunnel setup)', port=port)
            return
        now = time.time()
        every = self.CHECK_EVERY if (read_json(TUNNEL_STATE_FILE) or {}).get('status') == 'up' else 20
        if not force and now - getattr(self, '_custom_check', 0) < every:
            return
        self._custom_check = now
        ok, err = True, ''
        try:
            req = urllib.request.Request(url + '/__frx_tunnel_check', headers={'User-Agent': f'FRX-Keeper/{KEEPER_VERSION}'})
            urllib.request.urlopen(req, timeout=15).close()
        except urllib.error.HTTPError as e:
            ok, err = e.code not in (502, 503, 504, 530), f"ответ {e.code}"
        except Exception as e:
            ok, err = False, str(e)[:160]
        host = urllib.parse.urlparse(url).hostname
        cur = read_json(TUNNEL_STATE_FILE) or {}
        if ok:
            if cur.get('status') != 'up' or cur.get('url') != url:
                self._attempt += 1
                log(f"Туннель (свой адрес): {url} отвечает")
                self.state(status='up', url=url, host=host, port=port, since=now, provider='custom')
            return
        if cur.get('status') == 'up' and now - float(cur.get('ts', 0)) < 3 * self.CHECK_EVERY and not force:
            return                                       # одна неудачная проверка — ещё не повод
        self._attempt += 1
        self._next_try = now + 20
        self.state(status='error', error=f"{url} не отвечает: {err}", url=url, host=host, port=port, provider='custom')

    # Проверка снаружи: раз в CHECK_EVERY секунд keeper сам открывает адрес туннеля. Ответ 530 (ошибка Cloudflare 1033 —
    # «туннель не подключён») CHECK_FAILS раз подряд — cloudflared перезапускается за новым адресом (не чаще 3 раз в час);
    # юзербот увидит смену адреса и пришлёт новую ссылку владельцу.
    CHECK_EVERY = 60
    CHECK_FAILS = 3

    def _check(self):
        st = read_json(TUNNEL_STATE_FILE) or {}
        if st.get('status') == 'connecting' and self.proc is not None and time.time() - float(st.get('ts', 0)) > self.CONNECT_TIMEOUT:
            last = self._tail[-1] if self._tail else ''
            log(f"Туннель: адрес выдан, но соединиться с Cloudflare не удалось за {self.CONNECT_TIMEOUT} с — перезапускаю. {last}")
            self._kill()
            self._streak = getattr(self, '_streak', 0) + 1
            self._fails += 1
            self._next_try = time.time() + 3
            if self.mode == 'auto' and self._fails >= 2 and shutil.which('ssh'):
                self.provider, self._fails, self._next_try = 'localhost.run', 0, time.time() + 2
                log("Туннель: пробую localhost.run")
            self.state(status='error', error=f"нет соединения с Cloudflare за {self.CONNECT_TIMEOUT} с (порт 7844 закрыт?). {last}".strip(),
                       port=self.port)
            return
        if st.get('status') != 'up' or not st.get('url') or self.proc is None:
            return
        if time.time() - getattr(self, '_up_at', 0) < 20 or time.time() - getattr(self, '_last_check', 0) < self.CHECK_EVERY:
            return
        self._last_check = time.time()
        ok = True
        try:
            req = urllib.request.Request(st['url'].rstrip('/') + '/__frx_tunnel_check',
                                         headers={'User-Agent': f'FRX-Keeper/{KEEPER_VERSION}'})
            urllib.request.urlopen(req, timeout=20).close()
        except urllib.error.HTTPError as e:
            ok = e.code != 530                          # 530 = ошибка Cloudflare 1033; 404/403/502 — туннель жив
        except Exception:
            ok = True                                   # сеть/DNS самой машины — не повод менять адрес (не уверены)
        if ok:
            self._bad_checks = 0
            return
        self._bad_checks = getattr(self, '_bad_checks', 0) + 1
        log(f"Туннель: адрес {st['url']} не отвечает ({self._bad_checks}/{self.CHECK_FAILS})")
        if self._bad_checks >= self.CHECK_FAILS:
            now = time.time()
            self._restarts = [t for t in getattr(self, '_restarts', []) if now - t < 3600]
            if len(self._restarts) >= 3:
                log("Туннель: за час уже 3 перезапуска — больше не перезапускаю сам (.web tunnel new — вручную)")
                self._bad_checks = 0
                return
            self._restarts.append(now)
            log("Туннель: перезапускаю за новым адресом")
            self._bad_checks = 0
            self._kill()
            self.state(status='starting', port=self.port, provider=self.provider)
            self._next_try = 0.0


USER_README = """Это папка ваших личных данных FRX — она создаётся сама при первом запуске.

    config.json   api_id / api_hash, префикс, порт сайта и другие настройки
    data/         база данных, сессии Telegram и аккаунты, файлы, нейросети, история, резервные копии
    modules/      ваши модули (выгруженные — modules/unloaded/)
    assets/       ваши ассеты (картинки, медиа), подменяют стоковые из assets/ проекта

Никому не передавайте эту папку: в ней сессии вашего аккаунта. В git она не попадает (.gitignore),
а pack.bat / pack.sh удаляет её перед публикацией проекта. Перенос на другой компьютер — бэкап (.createbackup).
"""


def ensure_user_layout() -> None:
    """Саморазвёртывание: при первом запуске создаёт папку пользователя со всей структурой (в проекте её нет)."""
    if LEGACY:
        return
    for d in (USER_DIR, DATA_DIR, KEEPER_DIR, BACKUP_DIR, MODULES_DIR, MODULES_DIR / 'unloaded', USER_ASSETS_DIR):
        if d is not None:
            d.mkdir(parents=True, exist_ok=True)
    readme = USER_DIR / 'README.txt'
    if not readme.exists():
        try:
            readme.write_text(USER_README, encoding='utf-8')
        except OSError:
            pass


PID_NAME = 'keeper.pid'


def _write_pid() -> None:
    """keeper.pid — чтобы pack.bat / pack.sh не «сворачивал» работающий юзербот."""
    path = KEEPER_DIR / PID_NAME
    try:
        path.write_text(str(os.getpid()), encoding='utf-8')
    except OSError:
        return
    import atexit

    def _drop():
        try:
            if path.read_text(encoding='utf-8').strip() == str(os.getpid()):
                path.unlink()
        except OSError:
            pass
    atexit.register(_drop)


def pid_alive(pid: int) -> bool:
    """Жив ли процесс pid (без сигналов: на Windows os.kill(pid, 0) процесс убивает)."""
    if pid <= 0:
        return False
    if os.name == 'nt':
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k32.GetExitCodeProcess(h, ctypes.byref(code))
        k32.CloseHandle(h)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def running_keeper_pid():
    """pid работающего keeper'а этого проекта или None."""
    try:
        pid = int((KEEPER_DIR / PID_NAME).read_text(encoding='utf-8').strip())
    except (OSError, ValueError, TypeError):
        return None
    return pid if pid != os.getpid() and pid_alive(pid) else None


def main() -> int:
    os.chdir(ROOT)
    if not (ROOT / 'main.py').is_file():
        print("[FRX Keeper] main.py не найден рядом с frx_systemkeeper.py — запускать нечего", flush=True)
        return 1
    migrate_legacy_layout()
    ensure_user_layout()
    KEEPER_DIR.mkdir(parents=True, exist_ok=True)
    other = running_keeper_pid()
    if other:
        log(f"Внимание: keeper.pid указывает на работающий процесс {other} — если это второй FRX из этой же папки, остановите его")
    _write_pid()
    if hasattr(signal, 'SIGTERM'):
        try:
            signal.signal(signal.SIGTERM, _on_term)
        except (ValueError, OSError):
            pass
    log(f"SystemKeeper v{KEEPER_VERSION}, Python {sys.version.split()[0]}, папка: {ROOT}, данные: {USER_DIR}")

    recover_interrupted()
    if not REQUEST_FILE.exists():
        cleanup_work_dirs()
    ensure_deps()

    retry_used = False
    pending = None
    holder = WebPlaceholder()
    sentinel = WebSentinel()
    sentinel.run()
    TunnelManager().run()

    while True:
        READY_FILE.unlink(missing_ok=True)
        proc = _start_main()
        proc._frx_started = time.time()
        started = time.monotonic()
        ready_at = None

        try:
            while True:
                try:
                    code = proc.wait(timeout=0.5)
                    break
                except subprocess.TimeoutExpired:
                    now = time.monotonic()
                    forced = _force_stuck_exit(proc)
                    if forced is not None:            # main.py завис на выходе после .restart/.update — добиваем
                        code = forced
                        break
                    if ready_at is None and READY_FILE.exists():
                        ready_at = now
                        log("main.py запущен полностью")
                    if ready_at is not None and now - ready_at >= STABLE_SECONDS:
                        if pending or retry_used:
                            log("Запуск подтверждён — работает стабильно")
                        pending, retry_used = None, False
        except KeyboardInterrupt:
            if _TERM_RECEIVED:
                log("Получен сигнал остановки (systemd / docker stop) — останавливаю main.py")
                _signal_child(proc)
            else:
                log("Ctrl+C — останавливаю main.py")
            _stop_child(proc)
            sentinel.close()
            return 0

        if ready_at is None and READY_FILE.exists():
            ready_at = started
        stable = ready_at is not None and time.monotonic() - ready_at >= STABLE_SECONDS

        if code in (EXIT_RESTART, EXIT_APPLY):
            if holder.server is None:           # порт сайта держим, пока юзербот перезагружается
                holder = WebPlaceholder()
                holder.start()
        else:
            holder.stop()

        if code == EXIT_HIBERNATE:              # нет связи с Telegram
            ver = read_engine_version(ROOT) or '?'
            if hibernate(sentinel, str(ver)):
                continue
            sentinel.close()
            return 0

        if code in (EXIT_STOP, EXIT_NO_RETRY):
            sentinel.close()
            log("main.py завершён" + (" (ошибка конфигурации)" if code == EXIT_NO_RETRY else "")
                + " — SystemKeeper остановлен")
            return 0 if code == EXIT_STOP else EXIT_NO_RETRY       # 78 — службе (systemd) не перезапускать

        if code in (EXIT_RESTART, EXIT_APPLY):
            log("Перезагрузка" if code == EXIT_RESTART else "Применяю обновление/восстановление")
            new_pending = _handle_exit_request(code)
            if new_pending:
                pending = new_pending
                retry_used = False
            continue

        # --- main.py упал сам ---
        log(f"main.py аварийно завершился (код {code})")
        if stable:
            retry_used = False
        if pending:
            log("Новая версия не запустилась — откат")
            rolled = rollback_pending(pending, code)
            pending = None
            retry_used = True
            if not rolled:
                log("Остановка: откат не удался")
                return 1
            continue
        if not retry_used:
            retry_used = True
            log(f"Пробую перезапустить (один раз) через {RETRY_DELAY} сек")
            try:
                time.sleep(RETRY_DELAY)
            except KeyboardInterrupt:
                return 0
            continue
        log("Повторный запуск не удался — больше не пытаюсь. SystemKeeper остановлен")
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
