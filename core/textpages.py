"""
core/textpages.py

Длинный текст → страницы. Telegram не принимает сообщение длиннее 4096 символов (подпись к файлу — 1024),
и раньше длинный вывод (.eval, списки, логи модулей) падал ошибкой «message is too long». Теперь ядро режет
такой текст на страницы само:

  * инлайн-формы (engine.create_inlineform / form.edit) — страница текста + служебный ряд «◂ 2/5 ▸» и «Файлом»
    под кнопками модуля (core/inline_forms.py, TEXT_*); клики по нему модуль не видит;
  * engine.answer() — в сообщении первая страница, под ним маленький пульт-форма, который листает само
    сообщение (Core._answer_paged); без стационарного бота — первая страница и полный текст файлом.

split_html() понимает HTML Telegram: длина считается по видимому тексту (теги не в счёт, &amp; — один символ,
символы вне BMP — по два, как считает Telegram), страница по возможности кончается на переводе строки, а теги,
открытые на границе, закрываются в конце страницы и открываются заново в начале следующей — разметка
(<b>, <code>, <pre>, <blockquote>, <tg-emoji>…) не ломается. На вход — текст после safe_html() (теги корректны).
"""

import html as _html
import re

TEXT_LIMIT = 4096                # предел Telegram для текста сообщения
CAPTION_LIMIT = 1024             # и для подписи к файлу
PAGE_LIMIT = 3600                # страница короче предела: остаётся место под подпись «стр. 2/5»
CAPTION_PAGE_LIMIT = 900

_TOKEN_RE = re.compile(r'(<[^<>]+>)|(&(?:lt|gt|amp|quot|apos|#\d+|#[xX][0-9a-fA-F]+);)|([^<&]+|[<&])')
_TAG_NAME_RE = re.compile(r'<\s*(/?)\s*([A-Za-z][A-Za-z0-9-]*)')


def _u16(s: str) -> int:
    return len(s.encode('utf-16-le')) // 2


def _atoms(text: str):
    """
    Поток атомов: ('open', tag, name) / ('close', tag, name) / ('ch', кусок, длина, это_перевод_строки).
    Обычный текст режется на символы (длинные строки резать нужно где угодно), сущности — целиком.
    """
    for m in _TOKEN_RE.finditer(text or ''):
        tag, ent, plain = m.group(1), m.group(2), m.group(3)
        if tag:
            tm = _TAG_NAME_RE.match(tag)
            if not tm:
                yield ('ch', _html.escape(tag, quote=False), _u16(tag), False)
                continue
            closing, name = tm.group(1) == '/', tm.group(2).lower()
            yield ('close' if closing else 'open', tag, name)
        elif ent:
            yield ('ch', ent, _u16(_html.unescape(ent)), False)
        else:
            for ch in plain:
                yield ('ch', ch, _u16(ch), ch == '\n')


def visible_len(text: str) -> int:
    """Длина текста так, как её считает Telegram (без тегов, сущности — по символу)."""
    return sum(a[2] for a in _atoms(text) if a[0] == 'ch')


def html_to_plain(text: str) -> str:
    """Текст без разметки — для выгрузки в .txt."""
    return _html.unescape(re.sub(r'<[^<>]+>', '', text or ''))


def _close(stack) -> str:
    return ''.join(f'</{name}>' for name, _tag in reversed(stack))


def _reopen(stack) -> str:
    return ''.join(tag for _name, tag in stack)


def split_html(text: str, limit: int = PAGE_LIMIT, min_fill: float = 0.4) -> list:
    """
    Делит HTML на страницы видимой длиной не больше limit. Короткий текст — список из одной строки.
    Граница ищется на последнем переводе строки страницы (если страница при этом заполнена хотя бы на min_fill),
    иначе текст режется посреди строки.
    """
    text = text or ''
    if visible_len(text) <= limit:
        return [text]
    atoms = list(_atoms(text))
    pages = []
    stack = []                  # открытые теги на текущий момент: [(name, open_tag)]
    page = []                   # куски текущей страницы: (html, видимая_длина)
    page_len = 0
    cp = None                   # последний перевод строки: (индекс в page, длина до него, снимок stack)

    def emit(items, st):
        chunk = ''.join(p for p, _n in items) + _close(st)
        if html_to_plain(chunk).strip():
            pages.append(chunk)

    def restart(rest, st):
        nonlocal page, page_len, cp
        while rest and rest[0][0] == '\n':          # страница не начинается с пустых строк
            rest = rest[1:]
        page = [(_reopen(st), 0)] + rest
        page_len = sum(n for _p, n in rest)
        cp = None

    for atom in atoms:
        kind = atom[0]
        if kind == 'open':
            stack.append((atom[2], atom[1]))
            page.append((atom[1], 0))
            continue
        if kind == 'close':
            for i in range(len(stack) - 1, -1, -1):
                if stack[i][0] == atom[2]:
                    del stack[i]
                    break
            page.append((atom[1], 0))
            continue
        _k, piece, n, is_nl = atom
        if page_len + n > limit:
            if cp is not None and cp[1] >= limit * min_fill:
                idx, _len, st = cp
                emit(page[:idx], st)
                restart(page[idx:], st)
            else:
                st = list(stack)
                emit(page, st)
                restart([], st)
            if is_nl and page_len == 0:
                continue                          # перевод строки на самой границе страницы — не переносим
        page.append((piece, n))
        page_len += n
        if is_nl:
            cp = (len(page), page_len, list(stack))
    emit(page, stack)
    return pages or ['']


def page_footer(index: int, total: int) -> str:
    return f"\n\n<i>стр. {index + 1}/{total}</i>"
