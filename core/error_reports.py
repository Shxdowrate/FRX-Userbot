"""
core/error_reports.py

Разбор ошибок модулей: «отчёт» об исключении, понятный человеку.

Модули FRX исполняются из текста (compile(..., '<frx_module>')), поэтому обычный traceback не знает ни имени
модуля, ни строк его кода. Здесь трассировка обходится по кадрам: модуль кадра берётся из его глобалов
(MODULE_NAME), строки — из исходника модуля (get_source). Отчёт — обычный dict (JSON), его можно хранить в БД.

    build(exc, ...)          -> отчёт
    chat_text(rep, guest)    -> короткое сообщение в чат, где упала команда
    dm_short / dm_details / dm_traceback -> сообщения бота в ЛС (кратко, подробно, весь traceback)
    file_text(rep)           -> всё сразу простым текстом (для файла)
"""

import html as _html
import re
import time
import uuid

from . import fulllog as _fulllog
from . import visual as _v

MODULE_FILE = '<frx_module>'
MAX_FRAMES = 15
MAX_LOCALS = 15
REPR_LIMIT = 120
CONTEXT_LINES = 4
_SECRET_NAME = re.compile(r'(?i)(token|passw|secret|api_?hash|api_?key|session|cookie|auth|private)')


def _esc(text) -> str:
    return _html.escape(str(text), quote=False)


def _cut(text, limit) -> str:
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + '…'


def _safe_repr(value) -> str:
    try:
        r = repr(value)
    except Exception as e:
        r = f'<repr упал: {type(e).__name__}>'
    return _cut(_fulllog.mask(r), REPR_LIMIT)


# --------------------------------------------------------------------------- #
# Сборка отчёта
# --------------------------------------------------------------------------- #

def _walk(exc):
    """Кадры трассировки: [(frame, lineno)]."""
    out, tb = [], exc.__traceback__
    while tb is not None:
        out.append((tb.tb_frame, tb.tb_lineno))
        tb = tb.tb_next
    return out


def _line_of(source, lineno) -> str:
    if not source or not lineno:
        return ''
    lines = source.splitlines()
    return lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ''


def _frame_info(frame, lineno, get_source):
    code = frame.f_code
    if code.co_filename == MODULE_FILE:
        mod = frame.f_globals.get('MODULE_NAME') or '?'
        src = get_source(mod) if get_source else None
        return {'own': True, 'where': str(mod), 'line': lineno, 'func': code.co_name,
                'code': _line_of(src, lineno)}
    import linecache
    path = code.co_filename
    short = path.replace('\\', '/')
    for marker in ('/site-packages/', '/FRX/', '/frx/'):
        if marker in short:
            short = short.split(marker, 1)[1]
            break
    else:
        short = short.rsplit('/', 1)[-1]
    return {'own': False, 'where': short, 'line': lineno, 'func': code.co_name,
            'code': (linecache.getline(path, lineno) or '').strip()}


def _locals_of(frame) -> list:
    out = []
    for name, value in list(frame.f_locals.items()):
        if name.startswith('__') or type(value).__name__ in ('module', 'function', 'type', 'builtin_function_or_method'):
            continue
        out.append([name, '***' if _SECRET_NAME.search(name) else _safe_repr(value)])
        if len(out) >= MAX_LOCALS:
            break
    return out


def _chain(exc) -> list:
    out, seen, cur = [], {id(exc)}, exc
    while True:
        nxt = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
        if nxt is None or id(nxt) in seen:
            return out
        seen.add(id(nxt))
        out.append(_fulllog.mask(_cut(f"{type(nxt).__name__}: {nxt}", 500)))
        cur = nxt


def _format_tb(exc, get_source) -> str:
    """Traceback в духе Python, но с именами модулей FRX вместо '<frx_module>' и их строками кода."""
    def one(e):
        lines = ['Traceback:']
        for frame, lineno in _walk(e):
            f = _frame_info(frame, lineno, get_source)
            where = f"модуль {f['where']}" if f['own'] else f['where']
            lines.append(f"  {where}, строка {lineno}, в {f['func']}")
            if f['code']:
                lines.append(f"    {f['code']}")
        lines.append(f"{type(e).__name__}: {e}")
        return "\n".join(lines)

    parts, seen, cur = [], set(), exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        parts.append(cur)
        cur = cur.__cause__ or (None if cur.__suppress_context__ else cur.__context__)
    texts = [one(e) for e in reversed(parts)]
    return _fulllog.mask("\n\nВо время обработки этой ошибки возникла следующая:\n\n".join(texts))


def build(exc, module=None, function=None, command=None, chat_id=None, guest=None, origin='команда',
          get_source=None) -> dict:
    frames = _walk(exc)
    infos = [_frame_info(f, ln, get_source) for f, ln in frames]
    # кадры движка до первого кадра модуля (_safe_call, обёртки) — шум: автору модуля важен его код и то, что ниже
    first = next((i for i, x in enumerate(infos) if x['own']), 0)
    frames, infos = frames[first:], infos[first:]
    # «где»: последний кадр кода модуля (там, где ошибся автор модуля), иначе — самый последний кадр
    idx = max((i for i, x in enumerate(infos) if x['own']), default=len(infos) - 1)
    where = infos[idx] if infos else None
    context, local_vars = [], []
    if where is not None:
        frame = frames[idx][0]
        local_vars = _locals_of(frame)
        src = get_source(where['where']) if (where['own'] and get_source) else None
        if src:
            lines = src.splitlines()
            lo, hi = max(1, where['line'] - CONTEXT_LINES), min(len(lines), where['line'] + CONTEXT_LINES)
            context = [[n, lines[n - 1]] for n in range(lo, hi + 1)]
        elif where['code']:
            context = [[where['line'], where['code']]]
    if len(infos) > MAX_FRAMES:
        infos = infos[:2] + [{'skip': len(infos) - MAX_FRAMES + 2}] + infos[-(MAX_FRAMES - 2):]
    return {
        'id': uuid.uuid4().hex[:10],
        'ts': time.time(),
        'origin': origin,
        'module': module or (where or {}).get('where') or '?',
        'function': function or (where or {}).get('func') or '?',
        'command': command,
        'chat_id': chat_id,
        'guest': guest,
        'type': type(exc).__name__,
        'message': _fulllog.mask(_cut(str(exc), 1500)),
        'where': where,
        'context': context,
        'frames': infos,
        'locals': local_vars,
        'chain': _chain(exc),
        'traceback': _cut(_format_tb(exc, get_source), 20000),
    }


# --------------------------------------------------------------------------- #
# Тексты
# --------------------------------------------------------------------------- #

def _when(rep) -> str:
    return time.strftime('%d.%m.%Y %H:%M:%S', time.localtime(rep.get('ts') or time.time()))


def _ic(key) -> str:
    try:
        return _v.visual(key)
    except Exception:
        return ''


def _func_short(name) -> str:
    """«errbg_cmd.<locals>.job» → «job»."""
    return str(name or '?').rsplit('.', 1)[-1]


def _essence(rep) -> str:
    """Заголовок — тип ошибки: «AttributeError»."""
    return f"<b>{_esc(rep['type'])}</b>"


def _tech(rep, limit=300) -> str:
    """Текст ошибки: «'NoneType' object has no attribute 'text'»."""
    msg = rep.get('message') or ''
    return f"<code>{_esc(_cut(msg, limit))}</code>" if msg else ''


def _cmd_line(rep) -> str:
    """«⌘ .err abc» · у фоновой задачи — «⌘ .errbg → задача job»; без команды — модуль.функция."""
    cmd = rep.get('command')
    bg = rep.get('origin') == 'фоновая задача'
    if cmd:
        out = f"{_ic('code')} <code>{_esc(_cut(cmd, 120))}</code>"
        return out + (f" → задача <code>{_esc(_func_short(rep['function']))}</code>" if bg else '')
    what = 'задача' if bg else 'функция'
    return f"{_ic('code')} {what} <code>{_esc(_func_short(rep['function']))}</code>"


def _place(rep, with_func=False) -> str:
    """«📦 errtest · строка 21» и строка кода с подсветкой."""
    w = rep.get('where')
    if not w:
        return f"{_ic('module')} <code>{_esc(rep['module'])}</code>"
    name = w['where'] if w['own'] else w['where'].rsplit('/', 1)[-1]
    out = f"{_ic('module') if w['own'] else _ic('file')} <code>{_esc(name)}</code>"
    if with_func:
        out += f" · <code>{_esc(_func_short(w['func']))}</code>"
    out += f" · строка {w['line']}"
    if w.get('code'):
        out += f'\n<pre><code class="language-python">{_esc(_cut(w["code"], 200))}</code></pre>'
    return out


def chat_text(rep, guest=False, dm_hint=False) -> str:
    """
    Ошибка в чате:
        🟥 ValueError
        invalid literal for int() with base 10: 'abc'

        ⌘ .err abc
        📦 errtest · строка 21
        value = int(args[0])
    Гостю — без модуля и кода.
    """
    top = "\n".join(x for x in (f"{_ic('status_error')} {_essence(rep)}", _tech(rep)) if x)
    ctx = [_cmd_line(rep)] + ([] if guest else [_place(rep)])
    return top + "\n\n" + "\n".join(ctx)


def _meta_line(rep) -> str:
    parts = []
    if rep.get('chat_id') is not None:
        parts.append(f"{_ic('chat')} <code>{rep['chat_id']}</code>")
    if rep.get('guest') is not None:
        parts.append(f"{_ic('user')} гость <code>{rep['guest']}</code>")
    parts.append(f"{_ic('time')} {_when(rep)}")
    return "  ".join(parts)


def dm_short(rep) -> str:
    top = "\n".join(x for x in (f"{_ic('status_error')} {_essence(rep)}", _tech(rep, 800)) if x)
    return "\n\n".join([top, _cmd_line(rep) + "\n" + _place(rep, with_func=True), _meta_line(rep)])


def dm_details(rep, limit=4000) -> str:
    text = "\n\n".join([
        "\n".join(x for x in (f"{_ic('status_error')} {_essence(rep)}", _tech(rep, 1000)) if x),
        _cmd_line(rep) + "\n" + f"{_ic('module')} <code>{_esc(rep['module'])}</code>",
        _meta_line(rep),
    ])
    blocks = []
    w = rep.get('where')
    if w:
        src = "\n".join(f"{'→' if n == w['line'] else ' '}{n:>5} │ {_cut(t, 110)}" for n, t in rep.get('context') or [])
        name = w['where'] if w['own'] else w['where'].rsplit('/', 1)[-1]
        blocks.append(f"{_ic('code')} <b>Код</b> · <code>{_esc(name)}</code> · <code>{_esc(_func_short(w['func']))}</code>"
                      f" · строка {w['line']}" + (f"\n<pre>{_esc(src)}</pre>" if src else ''))
    if rep.get('locals'):
        body = "\n".join(f"{n} = {r}" for n, r in rep['locals'])
        blocks.append(f"{_ic('info')} <b>Переменные</b>\n<pre>{_esc(body)}</pre>")
    if rep.get('frames'):
        calls = []
        for f in rep['frames']:
            if 'skip' in f:
                calls.append(f"   … ещё {f['skip']}")
                continue
            calls.append(f"{'▸' if f['own'] else ' '} {f['where']}:{f['line']}  {_func_short(f['func'])}")
        blocks.append(f"{_ic('list')} <b>Вызовы</b>\n<pre>{_esc(chr(10).join(calls))}</pre>")
    if rep.get('chain'):
        blocks.append(f"{_ic('status_warning')} <b>Перед этим</b>\n"
                      + "\n".join(f"<code>{_esc(_cut(c, 300))}</code>" for c in rep['chain']))
    for b in blocks:                       # целыми блоками, пока влезает в сообщение
        if len(text) + len(b) + 2 > limit:
            text += "\n\n<i>обрезано — полностью в «Файлом»</i>"
            break
        text += "\n\n" + b
    return text


def dm_traceback(rep, limit=3900) -> str:
    head = f"{_ic('code')} <b>Traceback</b>\n"
    room = limit - len(head) - 40
    esc = _esc(rep.get('traceback') or '')
    if len(esc) > room:                    # хвост важнее начала
        esc = '…' + esc[-room:]
    return head + f"<pre>{esc}</pre>"


def file_text(rep) -> str:
    out = [f"FRX · ошибка {rep['id']}",
           f"время     {_when(rep)}",
           f"источник  {rep.get('origin')}",
           f"модуль    {rep['module']}",
           f"функция   {rep['function']}"]
    if rep.get('command'):
        out.append(f"команда   {rep['command']}")
    if rep.get('chat_id') is not None:
        out.append(f"чат       {rep['chat_id']}")
    if rep.get('guest') is not None:
        out.append(f"гость     {rep['guest']}")
    out += ["", f"{rep['type']}: {rep['message']}"]
    w = rep.get('where')
    if w:
        out += ["", f"{w['where']}:{w['line']} в {w['func']}"]
        for n, t in rep.get('context') or []:
            out.append(f"{'→' if n == w['line'] else ' '}{n:>5} | {t}")
    if rep.get('locals'):
        out += ["", "переменные:"] + [f"  {n} = {r}" for n, r in rep['locals']]
    if rep.get('chain'):
        out += ["", "перед этим:"] + [f"  {c}" for c in rep['chain']]
    out += ["", rep.get('traceback') or '']
    return "\n".join(out) + "\n"
