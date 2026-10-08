"""
core/paths.py — где FRX хранит файлы.

Код FRX (core/, aiobot/, modules/builtin/, assets/, main.py, frx_systemkeeper.py) одинаков у всех и обновляется
командой .update. Всё, что принадлежит конкретному владельцу юзербота, лежит в одной папке — user/:

    user/config.json     api_id, api_hash, токен стационарного бота и другие настройки запуска
    user/data/           БД модулей, сессия и аккаунты Telegram, файлы, нейросети, история WebIDE, BotChat,
                         резервные копии (backup/) и служебные файлы keeper'а (keeper/)
    user/modules/        загруженные модули (.loadmod, WebIDE); user/modules/unloaded/ — выгруженные (.unloadmod)
    user/assets/         свои файлы, загруженные на сайте во вкладке «files» (стоковые — в assets/ проекта)

Чтобы держать данные в другом месте (например, несколько юзерботов на одном коде), задайте переменную окружения
FRX_USER_DIR — абсолютный путь или путь относительно папки проекта.

Старая раскладка (config.json, data/ и modules/loaded/ прямо в корне проекта) переносится в user/ автоматически при
запуске frx_systemkeeper.py. Пока перенос не выполнен — например, сразу после .update со старой версии, когда в
памяти ещё работает прежний keeper, — все пути ниже указывают на старые места (LEGACY = True), и юзербот работает
как раньше. Следующий запуск keeper'а выполнит перенос.

Эта логика продублирована в frx_systemkeeper.py (keeper не импортирует ничего из core/) — при изменении правьте оба места.
"""

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def _user_dir() -> Path:
    raw = os.environ.get('FRX_USER_DIR', '').strip()
    return Path(raw) if raw else Path('user')


def _is_legacy(user_dir: Path) -> bool:
    if user_dir.exists():
        return False
    return Path('config.json').is_file() or Path('data').is_dir()


USER_DIR = _user_dir()
LEGACY = _is_legacy(USER_DIR)

if LEGACY:
    CONFIG_PATH = Path('config.json')
    DATA_DIR = Path('data')
    MODULES_DIR = Path('modules') / 'loaded'
    USER_ASSETS_DIR = Path('assets')
else:
    CONFIG_PATH = USER_DIR / 'config.json'
    DATA_DIR = USER_DIR / 'data'
    MODULES_DIR = USER_DIR / 'modules'
    USER_ASSETS_DIR = USER_DIR / 'assets'

UNLOADED_DIR = MODULES_DIR / 'unloaded'
BUILTIN_DIR = Path('modules') / 'builtin'
ASSETS_DIR = Path('assets')                      # стоковые файлы проекта (только чтение)
KEEPER_DIR = DATA_DIR / 'keeper'
BACKUP_DIR = DATA_DIR / 'backup'
FILES_DIR = DATA_DIR / 'files'
SESSION_PATH = DATA_DIR / 'frx_session'          # Telethon сам допишет .session
DB_PATH = DATA_DIR / 'database.json'


def resolve_db_path(value=None) -> Path:
    """
    Путь к БД из config.json (`db_path`, необязательный). Не задан — user/data/database.json.
    Относительный путь вида "data/..." (так писали старые версии) считается относительно user/,
    любой другой относительный — относительно папки проекта, абсолютный — как есть.
    """
    if not value:
        return DB_PATH
    p = Path(str(value))
    if p.is_absolute():
        return p
    parts = p.parts
    if not LEGACY and parts and parts[0] == 'data':
        return DATA_DIR.joinpath(*parts[1:])
    return p


def display(path) -> str:
    """Путь для сообщений пользователю: относительно папки проекта, с прямыми слэшами."""
    p = Path(path)
    try:
        p = p.resolve().relative_to(Path.cwd().resolve())
    except (OSError, ValueError):
        pass
    return p.as_posix()
