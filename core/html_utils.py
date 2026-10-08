"""
core/html_utils.py

Безопасный HTML для Telegram: `safe_html(text)`.

Проблема, ради которой файл появился. Всё, что FRX отправляет от лица
стационарного бота (инлайн-формы, `engine.bot.*`, лог-сообщения), уходит в
Bot API с parse_mode=HTML, а Bot API очень строг: любой `<...>`, который не
является настоящим тегом Telegram, роняет запрос ошибкой
`can't parse entities: Unsupported start tag "..."`. А такие `<...>` в
текстах встречаются постоянно — например, в докстринге команды
`.unloadmod / .ulm <имя_модуля>` (карточка `.help`) или в описании конфига
`... for_user=<chat_id> ...`. Telethon (сообщения от лица юзербота) в такой
ситуации не падает, но молча выбрасывает "тег" вместе с его содержимым — текст
`<имя_модуля>` просто исчезает.

Что делает `safe_html()`:
  - настоящие теги Telegram (`b`, `strong`, `i`, `em`, `u`, `ins`, `s`,
    `strike`, `del`, `a href=...`, `code`, `pre`, `blockquote`, `tg-spoiler`,
    `span class="tg-spoiler"`, `tg-emoji emoji-id=...`) остаются как есть —
    пока они корректно закрыты и вложены друг в друга;
  - всё остальное, что похоже на тег, но им не является (`<module_name>`,
    `<имя>`, `<br>`, `<b` без `>`, лишний `</b>`, незакрытый `<i>`), а также
    одиночные `<`, `>` и `&` превращаются в `&lt;`, `&gt;`, `&amp;` — то есть
    отправляются "как есть", буквально, как их и написал автор текста;
  - уже существующие сущности (`&lt;`, `&gt;`, `&amp;`, `&quot;`, `&#123;`)
    не трогаются — функция идемпотентна: `safe_html(safe_html(x)) == safe_html(x)`.

Где применяется автоматически (модулям вызывать её самим обычно не нужно):
  - `engine.answer/reply/noreply/send_message` (Telethon, от лица юзербота);
  - любой запрос к Bot API (`aiobot/bot.py`, класс FRXBot — единая точка для
    инлайн-форм, `engine.bot.*`, `engine.bot.raw.*`, `message.answer(...)`
    внутри bot.*-хэндлеров модулей и т.д.), см. `sanitize_bot_method()` ниже.
Явный вызов нужен только там, где текст собирается и отправляется мимо этих
путей — он доступен как `engine.safe_html(text)`.
"""

import re

# Теги, которые Telegram (Bot API HTML) реально понимает.
_SIMPLE_TAGS = frozenset({'b', 'strong', 'i', 'em', 'u', 'ins', 's', 'strike', 'del', 'tg-spoiler'})

# "Кандидат в тег": <name ...> / </name> / <name .../>. Имя — только латиница,
# цифры и дефис (так что <имя_модуля>, <module_name>, <1> кандидатами не являются
# вообще и экранируются как обычный текст).
_TAG_RE = re.compile(r'<(/?)([A-Za-z][A-Za-z0-9-]*)((?:\s[^<>]*)?)>')

# Сущности, которые Bot API понимает: 4 именованные + любые числовые.
_ENTITY_RE = re.compile(r'&(?:lt|gt|amp|quot|#\d+|#[xX][0-9a-fA-F]+);')

_ATTR_RE = re.compile(r'\s+([A-Za-z][A-Za-z0-9-]*)(?:\s*=\s*("[^"]*"|\'[^\']*\'|[^\s"\'=<>`]+))?')


def _escape_text(chunk: str) -> str:
    """Экранирует < > и & (кроме уже готовых сущностей) в куске обычного текста."""
    out = []
    pos = 0
    for m in _ENTITY_RE.finditer(chunk):
        out.append(_escape_plain(chunk[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_escape_plain(chunk[pos:]))
    return ''.join(out)


def _escape_plain(chunk: str) -> str:
    return chunk.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def _parse_attrs(raw: str):
    """' href="x" class=y' -> {'href': 'x', 'class': 'y'}; None — если синтаксис атрибутов некорректен."""
    attrs = {}
    rest = raw
    while rest.strip():
        m = _ATTR_RE.match(rest)
        if not m:
            return None
        value = m.group(2)
        if value is None:
            value = ''
        elif value[:1] in ('"', "'"):
            value = value[1:-1]
        attrs[m.group(1).lower()] = value
        rest = rest[m.end():]
    return attrs


def _valid_open_tag(name: str, raw_attrs: str, self_closing: bool) -> bool:
    """Настоящий ли это ОТКРЫВАЮЩИЙ тег Telegram (с допустимыми атрибутами)."""
    if self_closing:
        return False
    attrs = _parse_attrs(raw_attrs)
    if attrs is None:
        return False
    if name in _SIMPLE_TAGS:
        return not attrs
    if name == 'a':
        return bool(attrs.get('href')) and set(attrs) <= {'href'}
    if name == 'code':
        return set(attrs) <= {'class'} and (
            'class' not in attrs or attrs['class'].startswith('language-')
        )
    if name == 'pre':
        return not attrs
    if name == 'blockquote':
        return set(attrs) <= {'expandable'}
    if name == 'span':
        return attrs == {'class': 'tg-spoiler'}
    if name == 'tg-emoji':
        return bool(attrs.get('emoji-id')) and set(attrs) <= {'emoji-id'}
    return False


def safe_html(text):
    """
    Возвращает text, пригодный для parse_mode=HTML в Telegram: настоящие
    корректные теги сохраняются, всё остальное экранируется и отображается
    буквально (см. докстринг модуля). None и не-строки возвращаются как есть.
    """
    if not isinstance(text, str) or not text:
        return text

    out = []                 # куски результата; открывающие теги можно заменить задним числом
    stack = []               # [(имя_тега, индекс_в_out, исходный_текст_тега)]
    pos = 0

    for m in _TAG_RE.finditer(text):
        out.append(_escape_text(text[pos:m.start()]))
        pos = m.end()

        raw = m.group(0)
        closing, name, raw_attrs = m.group(1) == '/', m.group(2).lower(), m.group(3)
        self_closing = raw_attrs.rstrip().endswith('/')
        if self_closing:
            raw_attrs = raw_attrs.rstrip()[:-1]

        if closing:
            if not raw_attrs.strip() and not self_closing and stack and stack[-1][0] == name:
                stack.pop()
                out.append(raw)
            else:
                out.append(_escape_plain(raw))       # лишний/перепутанный закрывающий тег — как текст
        elif _valid_open_tag(name, raw_attrs, self_closing):
            stack.append((name, len(out), raw))
            out.append(raw)
        else:
            out.append(_escape_plain(raw))           # <module_name>, <br>, <b class=x> ... — как текст

    out.append(_escape_text(text[pos:]))

    # Открывающие теги, которые так и не закрылись, — тоже просто текст.
    for _name, index, raw in stack:
        out[index] = _escape_plain(raw)

    return ''.join(out)


# ---------------------------------------------------------------------- #
# Bot API: санитайзер запросов aiogram (используется классом FRXBot)
# ---------------------------------------------------------------------- #

def _is_html_mode(parse_mode) -> bool:
    """
    parse_mode запроса aiogram — HTML? Default('parse_mode') — это "взять
    значение по умолчанию у Bot", а у стационарного бота FRX оно HTML
    (aiobot/bot.py, DefaultBotProperties). None/Markdown/... — не трогаем.
    """
    if parse_mode is None:
        return False
    if isinstance(parse_mode, str):
        return parse_mode.upper() == 'HTML'
    return type(parse_mode).__name__ == 'Default'


def _fix_result(result):
    """Санитизирует один InlineQueryResult* (возвращает копию; исходный объект не мутируем)."""
    update = {}

    content = getattr(result, 'input_message_content', None)
    message_text = getattr(content, 'message_text', None)
    if isinstance(message_text, str) and _is_html_mode(getattr(content, 'parse_mode', None)):
        fixed = safe_html(message_text)
        if fixed != message_text:
            update['input_message_content'] = content.model_copy(update={'message_text': fixed})

    caption = getattr(result, 'caption', None)
    if isinstance(caption, str) and _is_html_mode(getattr(result, 'parse_mode', None)):
        fixed = safe_html(caption)
        if fixed != caption:
            update['caption'] = fixed

    return result.model_copy(update=update) if update else result


def sanitize_bot_method(method) -> None:
    """
    Прогоняет через safe_html все HTML-поля метода Bot API (aiogram TelegramMethod):
    text (sendMessage/editMessageText), caption (sendPhoto/sendDocument/...),
    а для answerInlineQuery — message_text и caption каждого результата.
    Поля с parse_mode, отличным от HTML, не трогаются. Вызывается из
    FRXBot.__call__ (aiobot/bot.py) перед КАЖДЫМ запросом.
    """
    parse_mode = getattr(method, 'parse_mode', None)
    if _is_html_mode(parse_mode):
        for field in ('text', 'caption'):
            value = getattr(method, field, None)
            if isinstance(value, str):
                fixed = safe_html(value)
                if fixed != value:
                    setattr(method, field, fixed)

    results = getattr(method, 'results', None)
    if isinstance(results, (list, tuple)) and results:
        method.results = [_fix_result(r) for r in results]
