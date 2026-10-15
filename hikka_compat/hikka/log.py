"""Логи пространства Hikka: кольцевой буфер для команды logs, HikkaException для красивых трейсбеков и
обработчик ошибок как в Hikka — ошибки модулей приходят вам в личку от стационарного бота FRX с кнопкой
«🪐 Полный traceback» (уровень и фильтр — в ,config Tester: tglog_level, ignore_common)."""

import collections
import logging
import sys
import time
import traceback
import typing

_BUFFER: "collections.deque[logging.LogRecord]" = collections.deque(maxlen=1500)


class MemoryHandler(logging.Handler):
    """Хранит последние записи логов пространства (логгеры hikka.*)."""

    def emit(self, record):
        from . import _frx

        try:
            record.hikka_text = self.format(record)
        except Exception:
            record.hikka_text = record.getMessage()
        record.hikka_space = id(_frx.current())       # у пространства Sandbox свои логи
        _BUFFER.append(record)


class TerminalHandler(logging.Handler):
    """Предупреждения и ошибки модулей — в терминал FRX."""

    def emit(self, record):
        from . import _frx

        try:
            text = self.format(record)
        except Exception:
            text = record.getMessage()
        _frx.tlog(text)


class TelegramLogsHandler(logging.Handler):
    """Записи нужного уровня — в очередь пространства; Space раз в несколько секунд отправляет их ботом (см. sender)."""

    def emit(self, record):
        from . import _frx

        if record.name.startswith("hikka.log"):            # ошибки самой отправки логов — не по кругу
            return
        space = _frx.current().runtime
        if space is None:
            return
        level = getattr(space, "tg_level", logging.ERROR)
        if record.levelno < level:
            return
        try:
            if record.exc_info and record.exc_info[0] is not None:
                item = HikkaException.from_exc_info(*record.exc_info, comment=record.getMessage())
                if getattr(space, "tg_ignore_common", True) and any(
                    s in item.message for s in _COMMON_ERRORS
                ):
                    return
            else:
                item = f"[{record.levelname}] {record.name}: {record.getMessage()}\n"
        except Exception:
            return
        queue = getattr(space, "tg_queue", None)
        if queue is not None and len(queue) < 200:
            queue.append(item)


_COMMON_ERRORS = (
    "InputPeerEmpty() does not have any entity type",
    "https://docs.telethon.dev/en/stable/concepts/entities.html",
)


async def tg_sender(space):
    """Отправить накопленные логи пространства владельцу (ЛС со стационарным ботом). Вызывается циклом Space."""
    queue = space.tg_queue
    if not queue:
        return
    inline = getattr(space, "inline", None)
    if inline is None or not inline.init_complete or inline.bot is None:
        if len(queue) > 100:
            del queue[:-100]
        return
    items, queue[:] = list(queue), []
    chat = space.client.tg_id
    from . import utils

    texts = []
    for item in items:
        if isinstance(item, HikkaException):
            try:
                await inline.bot.send_message(
                    chat, item.message[:4096],
                    reply_markup=inline.generate_markup([{
                        "text": "🪐 Полный traceback", "callback": _show_full_trace, "args": (item,),
                        "disable_security": True,
                    }]),
                )
            except Exception:
                logging.getLogger("hikka.log.tg").debug("лог не отправлен", exc_info=True)
        else:
            texts.append(item)
    if not texts:
        return
    chunks = utils.chunks(utils.escape_html("".join(texts)), 4000)
    try:
        if len(chunks) > 5:
            import io

            file = io.BytesIO("".join(texts).encode("utf-8"))
            file.name = "hikka-logs.txt"
            await inline.bot.send_document(chat, file, caption="🧳 <b>Логов слишком много — отправляю файлом</b>")
        else:
            for chunk in chunks:
                await inline.bot.send_message(chat, f"<code>{chunk}</code>", disable_notification=True)
    except Exception:
        logging.getLogger("hikka.log.tg").debug("логи не отправлены", exc_info=True)


async def _show_full_trace(call, item: "HikkaException"):
    from . import utils

    text = item.message + "\n\n<b>🪐 Полный traceback:</b>\n" + item.full_stack
    try:
        from . import _html

        chunks = list(utils.smart_split(*_html.parse(text), 4096))
    except Exception:
        chunks = [text[:4096]]
    await call.edit(chunks[0])
    bot = call.inline_manager.bot
    for chunk in chunks[1:]:
        try:
            await bot.send_message(call.chat_id or call.from_user.id, chunk)
        except Exception:
            break


_installed = False
_handlers: list = []
_users = set()


def install(owner=None):
    global _installed
    _users.add(owner)
    if _installed:
        return
    root = logging.getLogger("hikka")
    root.setLevel(logging.DEBUG)
    mem = MemoryHandler(level=logging.DEBUG)
    mem.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    term = TerminalHandler(level=logging.WARNING)
    term.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    tg = TelegramLogsHandler(level=logging.DEBUG)
    root.addHandler(mem)
    root.addHandler(term)
    root.addHandler(tg)
    root.propagate = False
    _handlers[:] = [mem, term, tg]
    _installed = True


def uninstall(owner=None):
    global _installed
    _users.discard(owner)
    if _users:
        return
    root = logging.getLogger("hikka")
    for h in _handlers:
        root.removeHandler(h)
    _handlers.clear()
    _installed = False


def get_records(min_level: int = logging.INFO, limit: int = 300) -> typing.List[logging.LogRecord]:
    from . import _frx

    space = id(_frx.current())
    return [r for r in _BUFFER if r.levelno >= min_level and getattr(r, "hikka_space", space) == space][-limit:]


def format_records(min_level: int = logging.INFO, limit: int = 300) -> str:
    lines = []
    for r in get_records(min_level, limit):
        stamp = time.strftime("%H:%M:%S", time.localtime(r.created))
        lines.append(f"{stamp} {getattr(r, 'hikka_text', r.getMessage())}")
        if r.exc_info:
            lines.append("".join(traceback.format_exception(*r.exc_info)).rstrip())
    return "\n".join(lines)


class HikkaException:
    """Разобранное исключение (как в Hikka): message — кратко (причина, место, ошибка), full_stack — весь traceback."""

    def __init__(self, message: str, full_stack: str, sysinfo=None):
        self.message = message
        self.full_stack = full_stack
        self.sysinfo = sysinfo
        self.debug_url = None

    @classmethod
    def from_exc_info(cls, exc_type, exc_value, tb, stack=None, comment=None) -> "HikkaException":
        import html as _h
        import re

        esc = lambda x: _h.escape(str(x), quote=False)  # noqa: E731
        full = "".join(traceback.format_exception(exc_type, exc_value, tb))
        full = full.replace("Traceback (most recent call last):\n", "")
        line_re = re.compile(r'  File "(.*?)", line ([0-9]+), in (.+)')
        source = next((line_re.search(line).groups() for line in reversed(full.splitlines()) if line_re.search(line)),
                      (None, None, None))
        full_html = "\n".join(
            (lambda m: f"👉 <code>{esc(m.group(1))}:{m.group(2)}</code> <b>в</b> <code>{esc(m.group(3))}</code>")(line_re.search(line))
            if line_re.search(line) else f"<code>{esc(line)}</code>"
            for line in full.splitlines()
        )
        cause = ""
        frame_tb = tb
        method = None
        while frame_tb is not None:                       # последний метод модуля Hikka в цепочке вызовов
            local_self = frame_tb.tb_frame.f_locals.get("self")
            if local_self is not None and type(local_self).__module__.startswith("hikka.modules"):
                method = (frame_tb.tb_frame.f_code.co_name, type(local_self).__name__)
            frame_tb = frame_tb.tb_next
        if method:
            cause = f"🔮 <b>Причина: метод</b> <code>{esc(method[0])}</code> <b>модуля</b> <code>{esc(method[1])}</code>\n\n"
        error = esc("".join(traceback.format_exception_only(exc_type, exc_value)).strip())
        message = (
            f"{cause}<b>🎯 Где:</b> <code>{esc(source[0])}:{source[1]}</code> <b>в</b> <code>{esc(source[2])}</code>\n"
            f"<b>❓ Ошибка:</b> <code>{error}</code>"
            + (f"\n💭 <b>Сообщение:</b> <code>{esc(comment)}</code>" if comment else "")
        )
        return cls(override_text(exc_value) or message, full_html, (exc_type, exc_value, tb))


HerokuException = HikkaException


def init():
    install()


def override_text(exception: Exception) -> typing.Optional[str]:
    """Понятный текст для типичных ошибок (как в Hikka) или None."""
    name = type(exception).__name__
    if name in ("ConnectionError", "ClientConnectorError", "ServerDisconnectedError", "NetworkError"):
        return "✈️ <b>Проблемы с интернетом у компьютера с FRX.</b>"
    return None


__all__ = ["HikkaException", "HerokuException", "install", "get_records", "format_records"]
