"""
core/apilog.py

Учёт запросов к Telegram, которые модуль делает ВО ВРЕМЯ Emulate (кнопка `> emulate` в карточке модуля на сайте).

Зачем: Emulate перехватывает ответы «в свой чат» (engine.answer / reply / noreply / send_message), но модуль может стучаться в Telegram
напрямую — `engine.client.*`, `engine.bot.raw.*`, чужие чаты. Такие запросы выполняются по-настоящему (на реальный аккаунт), и сайту лучше
быть с ними осторожным. Теперь каждый такой запрос виден в ленте Emulate: протокол (MTProto / Bot API), метод, краткое содержание, длительность,
итог — и пометка «читает» / «меняет» (меняет — отправка, редактирование, удаление, вступление… то, что видят другие люди).

Как перехватываем:
  * Telethon — единая точка `TelegramClient._call` (через неё идут и client(request), и все высокоуровневые методы, загрузка и выгрузка файлов);
  * aiogram — `FRXBot.__call__` (aiobot/bot.py) вызывает note_bot().
Записываются только запросы, у которых в ContextVar `current_emulation` есть сессия Emulate: обычная работа юзербота ничего не замедляется и не копится.
"""

import re
import time

from .context import current_emulation

_READ_RE = re.compile(r'^(Get|Resolve|Search|Check|Export|Is|Has|List|Ping|Read(?!History)|Fetch|Download|Contains|Find|Query|Suggest|Hash)', re.I)
_SECRET_FIELDS = {'phone_number', 'phone', 'phone_code', 'password', 'phone_code_hash', 'token', 'bot_auth_token', 'code', 'srp_id', 'a', 'm1', 'api_hash'}


def _short(v, n=70):
    s = repr(v) if not isinstance(v, str) else v
    s = re.sub(r'\s+', ' ', s)
    return s if len(s) <= n else s[:n - 1] + '…'


def _brief(fields: dict) -> str:
    """Короткая строка «ключ=значение, …» без секретов: показываем главное (чат, текст), остальное опускаем."""
    parts = []
    for k, v in fields.items():
        if k in _SECRET_FIELDS or k.startswith('_') or v in (None, [], {}, ''):
            continue
        if isinstance(v, dict):
            inner = v.get('user_id') or v.get('chat_id') or v.get('channel_id') or v.get('_')
            v = inner if inner is not None else '…'
        elif isinstance(v, (list, tuple)):
            v = f'{len(v)} шт.'
        parts.append(f"{k}={_short(v, 60)}")
        if len(parts) >= 4:
            break
    return ', '.join(parts)


def risk_of(name: str) -> str:
    """'read' — запрос только читает; 'write' — меняет состояние аккаунта / чатов."""
    method = name.split('.')[-1]
    return 'read' if _READ_RE.match(method) else 'write'


def _describe(request):
    cls = type(request)
    mod = (cls.__module__ or '').split('.')[-1]
    base = cls.__name__[:-7] if cls.__name__.endswith('Request') else cls.__name__
    name = f"{mod}.{base}" if mod and mod not in ('types', 'functions') else base
    try:
        fields = request.to_dict()
    except Exception:
        fields = {}
    return name, _brief(fields)


def wrap_client(client):
    """Подменяет client._call обёрткой с учётом запросов (идемпотентно)."""
    if client is None or getattr(client, '_frx_apilog', False):
        return
    orig = client._call

    async def _call(sender, request, *args, **kwargs):
        emu = current_emulation.get()
        if emu is None:
            return await orig(sender, request, *args, **kwargs)
        if isinstance(request, (list, tuple)):
            name, brief = f"[{len(request)} запросов]", ', '.join(_describe(r)[0] for r in list(request)[:3])
        else:
            name, brief = _describe(request)
        entry = emu.api_begin('mtproto', name, brief, risk_of(name))
        t0 = time.perf_counter()
        try:
            result = await orig(sender, request, *args, **kwargs)
        except BaseException as e:
            emu.api_end(entry, (time.perf_counter() - t0) * 1000, ok=False, error=f"{type(e).__name__}: {e}")
            raise
        emu.api_end(entry, (time.perf_counter() - t0) * 1000, ok=True)
        return result

    client._call = _call
    client._frx_apilog = True


async def note_bot(method, call):
    """Вызывается из FRXBot.__call__: оборачивает запрос Bot API (call — корутина настоящего запроса)."""
    emu = current_emulation.get()
    if emu is None:
        return await call
    name = type(method).__name__
    try:
        fields = {k: getattr(method, k, None) for k in ('chat_id', 'text', 'caption', 'message_id', 'user_id', 'callback_query_id', 'inline_message_id')}
    except Exception:
        fields = {}
    entry = emu.api_begin('bot', name, _brief(fields), risk_of(name))
    t0 = time.perf_counter()
    try:
        result = await call
    except BaseException as e:
        emu.api_end(entry, (time.perf_counter() - t0) * 1000, ok=False, error=f"{type(e).__name__}: {e}")
        raise
    emu.api_end(entry, (time.perf_counter() - t0) * 1000, ok=True)
    return result
