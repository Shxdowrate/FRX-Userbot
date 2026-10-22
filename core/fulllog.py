"""
core/fulllog.py

Полный лог юзербота — user/data/logs/frx_full.log (ротация: 5 файлов по 5 МБ, старые — frx_full.log.1 … .5).
Нужен, чтобы разобраться в проблеме задним числом: терминал и вкладка «логи» на сайте держат только последние
строки, а Telegram-лог (модуль Logging) — только ошибки.

Что попадает в файл:
  * всё, что юзербот и модули пишут в терминал (print, engine.tlog, трассировки ошибок) — через core/logs.py;
  * журналы библиотек (logging): telethon, aiogram, aiohttp — уровень INFO и выше;
  * события ядра (event()): каждая команда — кто, где, какая, сколько выполнялась и чем кончилась; загрузка и
    выгрузка модулей (и «Сломанные»), клики по инлайн-формам, команды стационарного бота, смена аккаунта, запуск
    и остановка.

Секреты в лог не пишутся: токены ботов, api_hash, строки сессий и токены GitHub заменяются на «***».
Аргументы команд обрезаются до 300 символов; аргументы .eval/.exec и команд с токенами — не пишутся вовсе.

Модулям: engine.fulllog(text, kind='module') — своя строка в полный лог. Отправить лог — .fulllog (модуль Logging).
"""

import logging
import logging.handlers
import re
import threading
import time
from pathlib import Path

from .paths import DATA_DIR

LOG_DIR = DATA_DIR / 'logs'
LOG_FILE = LOG_DIR / 'frx_full.log'
MAX_BYTES = 5 * 1024 * 1024
BACKUPS = 5

_SECRET_RES = [
    re.compile(r'(?<!\d)\d{6,12}:[A-Za-z0-9_-]{30,}'),          # токен бота (и внутри ссылки .../bot<токен>/)
    re.compile(r'\b1[A-Za-z0-9_+/=-]{200,}'),                    # StringSession
    re.compile(r'\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{20,}\b'),   # токены GitHub
    re.compile(r'(?i)(api_hash["\'=:\s]+)[0-9a-f]{32}'),
    re.compile(r'(?i)(bearer\s+)[A-Za-z0-9._-]{16,}'),
    re.compile(r'\bsk-[A-Za-z0-9_-]{20,}\b'),                    # ключи нейросетей
]
_SECRET_LITERALS = set()                  # точные секреты из config.json (api_hash, токен) — см. add_secret()
_NO_ARGS_COMMANDS = {'eval', 'ev', 'exec', 'bottoken', 'settoken', 'e', 'terminal', 'sh'}

_lock = threading.RLock()
_handler = None
_installed = False
_guard = threading.local()                # запись лога не должна вызывать запись лога (рекурсия через stderr)
_failures = 0
MAX_FAILURES = 20                         # столько ошибок записи подряд — файл отключается до перезапуска


def add_secret(value) -> None:
    """Значение, которое никогда не должно попасть в лог как есть (api_hash, токен бота из конфига)."""
    v = str(value or '').strip()
    if len(v) >= 8:
        _SECRET_LITERALS.add(v)


def mask(text: str) -> str:
    text = str(text)
    for secret in list(_SECRET_LITERALS):
        if secret in text:
            text = text.replace(secret, '***')
    for rx in _SECRET_RES:
        if rx.groups:
            text = rx.sub(lambda m: m.group(1) + '***', text)
        else:
            text = rx.sub('***', text)
    return text


def _ensure_handler():
    global _handler
    if _handler is not None:
        return _handler
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding='utf-8',
                                             delay=True)
    h.setFormatter(logging.Formatter('%(message)s'))
    h.handleError = lambda record: None   # иначе logging печатает «Logging error» в stderr → снова сюда → без конца
    _handler = h
    return h


def _write(kind: str, text: str) -> None:
    global _failures
    if _failures >= MAX_FAILURES or getattr(_guard, 'busy', False):
        return
    _guard.busy = True
    try:
        stamp = time.strftime('%Y-%m-%d %H:%M:%S') + f".{int(time.time() * 1000) % 1000:03d}"
        line = f"{stamp} [{kind}] {mask(text)}"
        with _lock:
            h = _ensure_handler()
            if h.shouldRollover(logging.LogRecord('frx', logging.INFO, '', 0, line, None, None)):
                h.doRollover()
            h.stream = h.stream or h._open()
            h.stream.write(line + '\n')
            h.stream.flush()
        _failures = 0
    except Exception:
        _failures += 1                        # диск полон, папка только для чтения, файл занят (Windows) —
    finally:                                  # лог никогда не роняет и не вешает юзербот
        _guard.busy = False


def write_line(line: str, stream: str = 'out') -> None:
    """Строка терминала (из core/logs.py)."""
    if line.strip():
        _write('err' if stream == 'stderr' else 'out', line)


def event(kind: str, text: str) -> None:
    """Событие ядра/модуля: event('cmd', '…'), event('module', '…')."""
    _write(kind, text)


def command_text(prefix: str, cmd: str, args: str) -> str:
    """Как записать команду: аргументы обрезаются, у .eval и команд с секретами — не пишутся."""
    if cmd.lower() in _NO_ARGS_COMMANDS:
        return f"{prefix}{cmd} <аргументы скрыты>" if args else f"{prefix}{cmd}"
    args = (args or '').replace('\n', ' ⏎ ')
    if len(args) > 300:
        args = args[:300] + f'… (+{len(args) - 300})'
    return f"{prefix}{cmd} {args}".rstrip()


class _LibHandler(logging.Handler):
    """
    Журналы библиотек (telethon, aiogram…) → полный лог. WARNING и выше, как и раньше, печатаются в терминал
    (оттуда — во вкладку «логи» и в полный лог через core/logs.py): без этого обработчика их печатал запасной
    обработчик logging (lastResort), а он отключается, как только у логгера появляется свой обработчик.
    """

    def emit(self, record):
        try:
            if record.name == 'frx':
                return
            if record.levelno >= logging.WARNING:
                import sys
                msg = self.format(record)
                sys.stderr.write(msg + '\n')        # как lastResort; в файл попадёт через core/logs.py
                return
            msg = record.getMessage()
            if record.exc_info:
                import traceback
                msg += '\n' + ''.join(traceback.format_exception(*record.exc_info)).rstrip()
            _write(f"lib:{record.name.split('.')[0]}:{record.levelname.lower()}", msg)
        except Exception:
            pass


def install() -> None:
    """Подключить журналы библиотек (повторный вызов безопасен). Строки терминала пишет core/logs.py сам."""
    global _installed
    if _installed:
        return
    _installed = True
    try:
        h = _LibHandler(level=logging.INFO)
        for name in ('telethon', 'aiogram', 'aiohttp', 'asyncio'):
            lg = logging.getLogger(name)
            lg.addHandler(h)
            if lg.level == logging.NOTSET or lg.level > logging.INFO:
                lg.setLevel(logging.INFO)
        # без своего обработчика root-логгер печатал бы INFO библиотек в терминал — оставляем терминал как был:
        # lastResort печатает только WARNING и выше
        _write('start', "==== FRX: процесс запущен ====")
    except Exception:
        pass


def files() -> list:
    """Файлы полного лога, от свежего к старому (frx_full.log, .1, .2…), только существующие."""
    out = [LOG_FILE] + [LOG_FILE.with_name(f"{LOG_FILE.name}.{i}") for i in range(1, BACKUPS + 1)]
    return [p for p in out if p.is_file()]


def total_size() -> int:
    return sum(p.stat().st_size for p in files())


def tail(lines: int = 50) -> str:
    """Последние строки текущего файла лога."""
    try:
        with open(LOG_FILE, 'rb') as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(size - 256 * 1024, 0))
            data = f.read().decode('utf-8', 'replace')
    except OSError:
        return ''
    return '\n'.join(data.splitlines()[-lines:])


def clear() -> int:
    """Удаляет архивные части и обнуляет текущий файл. Возвращает, сколько байт освобождено."""
    freed = 0
    with _lock:
        if _handler is not None:
            try:
                _handler.close()
            except Exception:
                pass
        for p in files():
            try:
                freed += p.stat().st_size
                if p == LOG_FILE:
                    p.write_text('', encoding='utf-8')
                else:
                    p.unlink()
            except OSError:
                pass
    _write('log', 'полный лог очищен (.fulllog clear)')
    return freed


def pack(dst: Path, extra=()) -> Path:
    """
    Все части лога (+ extra — например журнал keeper'а) в один zip. Под блокировкой — только снимок файлов
    (копии), сжатие идёт без неё: иначе каждый print юзербота ждал бы, пока архивируются десятки МБ.
    """
    import shutil
    import tempfile
    import zipfile
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix='frx_log_'))
    try:
        with _lock:
            if _handler is not None and _handler.stream:
                try:
                    _handler.stream.flush()
                except Exception:
                    pass
            current = files()
            snap = []
            for p in current:
                q = tmp / p.name
                if p == LOG_FILE:
                    shutil.copyfile(p, q)                  # текущий файл ещё пишется — копия
                    snap.append(q)
                else:
                    snap.append(p)                         # архивные части не меняются до ротации
        with zipfile.ZipFile(dst, 'w', zipfile.ZIP_DEFLATED) as zf:
            for p in snap:
                try:
                    zf.write(p, p.name)
                except FileNotFoundError:                  # часть успела уйти при ротации
                    pass
            for p in extra:
                p = Path(p)
                if p.is_file():
                    zf.write(p, p.name)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return dst
