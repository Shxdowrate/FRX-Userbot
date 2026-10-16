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


# --------------------------------------------------------------------- #
# Премиум-эмодзи
# --------------------------------------------------------------------- #
# Telegram понимает <tg-emoji emoji-id="ID">🔥</tg-emoji>. Модули Hikka/Heroku и многие авторы пишут
# <emoji document_id=ID>🔥</emoji> — это тоже принимается и переводится в tg-emoji (и для юзербота, и для бота).
_EMOJI_ALIAS_RE = re.compile(
    r'<emoji\s+(?:document_id|document-id|emoji-id|emoji_id|id)\s*=\s*["\']?(\d+)["\']?\s*>(.*?)</emoji>',
    re.IGNORECASE | re.DOTALL,
)
_TG_EMOJI_RE = re.compile(r'<tg-emoji\s+emoji-id\s*=\s*["\']?(\d+)["\']?\s*>(.*?)</tg-emoji>', re.IGNORECASE | re.DOTALL)


def normalize_emoji_tags(text):
    """<emoji document_id=N>x</emoji> → <tg-emoji emoji-id="N">x</tg-emoji>; остальное без изменений."""
    if not isinstance(text, str) or '<emoji' not in text.lower():
        return text
    return _EMOJI_ALIAS_RE.sub(lambda m: f'<tg-emoji emoji-id="{m.group(1)}">{m.group(2) or "▫️"}</tg-emoji>', text)


def strip_custom_emoji(text):
    """Убирает премиум-эмодзи, оставляя обычный эмодзи внутри тега (запасной вариант, если премиум нельзя)."""
    if not isinstance(text, str) or 'emoji' not in text:
        return text
    return _TG_EMOJI_RE.sub(lambda m: m.group(2), normalize_emoji_tags(text))


def has_custom_emoji(text) -> bool:
    return isinstance(text, str) and bool(_TG_EMOJI_RE.search(normalize_emoji_tags(text)))


def split_button_emoji(text):
    """
    Подпись кнопки «<tg-emoji emoji-id=N>🔥</tg-emoji> Текст» → ('Текст', 'N'): премиум-эмодзи в начале подписи
    становится иконкой кнопки (icon_custom_emoji_id, Bot API 9.4). Теги внутри подписи кнопок невозможны —
    остальные премиум-эмодзи заменяются своим обычным эмодзи. Без премиум-эмодзи — (text, None).
    """
    if not isinstance(text, str) or 'emoji' not in text:
        return text, None
    text = normalize_emoji_tags(text)
    m = _TG_EMOJI_RE.match(text.lstrip())
    icon = None
    if m:
        icon = m.group(1)
        text = text.lstrip()[m.end():].lstrip()
    return strip_custom_emoji(text), icon


def html_with_entities(text: str, entities, start: int = 0) -> str:
    """
    HTML-разметка куска text[start:] с entities сообщения Telegram (смещения — в UTF-16, как в Telegram):
    жирный/курсив/ссылки/код и премиум-эмодзи (<tg-emoji emoji-id=…>) сохраняются.
    """
    from telethon.extensions import html as tl_html
    text = text or ''
    if start <= 0:
        return tl_html.unparse(text, list(entities or []))
    off16 = len(text[:start].encode('utf-16-le')) // 2
    shifted = []
    for e in entities or []:
        end = e.offset + e.length
        if end <= off16:
            continue
        clone = e.__class__(**{k: v for k, v in e.to_dict().items() if k != '_'})
        clone.offset = max(0, e.offset - off16)
        clone.length = end - off16 - clone.offset
        shifted.append(clone)
    return tl_html.unparse(text[start:], shifted)


_TELETHON_PATCHED = False


def _frx_parser_class():
    from telethon.extensions import html as tl_html
    from telethon.tl.types import MessageEntitySpoiler

    class FRXHTMLParser(tl_html.HTMLToTelegramParser):
        """Парсер Telethon + спойлеры (<tg-spoiler>, <span class="tg-spoiler">), которых Telethon не знает."""

        def handle_starttag(self, tag, attrs):
            if tag == 'tg-spoiler' or (tag == 'span' and dict(attrs).get('class') == 'tg-spoiler'):
                self._open_tags.appendleft(tag)
                self._open_tags_meta.appendleft(None)
                if tag not in self._building_entities:
                    self._building_entities[tag] = MessageEntitySpoiler(offset=len(self.text), length=0)
                return
            super().handle_starttag(tag, attrs)

    return FRXHTMLParser


def _escape(text: str) -> str:
    return text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def frx_html_parse(html: str):
    """HTML → (текст, entities). Как у Telethon, плюс <emoji document_id=…> и спойлеры."""
    from telethon.helpers import add_surrogate, del_surrogate, strip_text
    if not html:
        return html, []
    parser = _frx_parser_class()()
    parser.feed(add_surrogate(normalize_emoji_tags(html)))
    text = strip_text(parser.text, parser.entities)
    parser.entities.reverse()
    parser.entities.sort(key=lambda entity: entity.offset)
    return del_surrogate(text), parser.entities


def _formatter(entity, text):
    from telethon.tl import types as t
    kind = type(entity)
    if kind is t.MessageEntityBold:
        return '<b>', '</b>'
    if kind is t.MessageEntityItalic:
        return '<i>', '</i>'
    if kind is t.MessageEntityUnderline:
        return '<u>', '</u>'
    if kind is t.MessageEntityStrike:
        return '<s>', '</s>'
    if kind is t.MessageEntitySpoiler:
        return '<tg-spoiler>', '</tg-spoiler>'
    if kind is t.MessageEntityCode:
        return '<code>', '</code>'
    if kind is t.MessageEntityPre:
        lang = getattr(entity, 'language', '') or ''
        return (f'<pre><code class="language-{_escape(lang)}">', '</code></pre>') if lang else ('<pre>', '</pre>')
    if kind is t.MessageEntityBlockquote:
        return ('<blockquote expandable>' if getattr(entity, 'collapsed', False) else '<blockquote>'), '</blockquote>'
    if kind is t.MessageEntityTextUrl:
        return f'<a href="{_escape(entity.url).replace(chr(34), "&quot;")}">', '</a>'
    if kind is t.MessageEntityMentionName:
        return f'<a href="tg://user?id={entity.user_id}">', '</a>'
    if kind is t.MessageEntityCustomEmoji:
        return f'<tg-emoji emoji-id="{entity.document_id}">', '</tg-emoji>'
    return None                 # ссылки, @упоминания, #хэштеги — Telegram распознаёт сам, теги не нужны


def frx_html_unparse(text: str, entities) -> str:
    """
    (текст, entities) → HTML в стиле Telegram: <b> <i> <u> <s> <code> <pre> <tg-spoiler> <blockquote> <a href>
    <tg-emoji emoji-id>. Экранируются только < > & (кавычки и апострофы остаются как есть).
    """
    from telethon.helpers import add_surrogate, del_surrogate, within_surrogate
    from telethon.tl import TLObject
    if not text:
        return text
    if not entities:
        return _escape(text)
    if isinstance(entities, TLObject):
        entities = (entities,)
    text = add_surrogate(text)
    insert_at = []
    for i, entity in enumerate(entities):
        s, e = entity.offset, entity.offset + entity.length
        tags = _formatter(entity, text[s:e])
        if tags:
            insert_at.append((s, i, tags[0]))
            insert_at.append((e, -i, tags[1]))
    insert_at.sort(key=lambda x: (x[0], x[1]))
    next_escape_bound = len(text)
    while insert_at:
        at, _, what = insert_at.pop()
        while within_surrogate(text, at):
            at += 1
        text = text[:at] + what + _escape(text[at:next_escape_bound]) + text[next_escape_bound:]
        next_escape_bound = at
    text = _escape(text[:next_escape_bound]) + text[next_escape_bound:]
    return del_surrogate(text)


def install_telethon_emoji_tags() -> None:
    """
    HTML Telethon (parse_mode='html' — и у клиента FRX по умолчанию) работает по-телеграмному:
      • разбор понимает ещё <emoji document_id=N> (как в Hikka) и спойлеры <tg-spoiler>;
      • message.text отдаёт <b>/<i>/<s>/<u> (а не <strong>/<em>/<del>), <tg-emoji emoji-id=…> для премиум-эмодзи,
        <pre> без лишних отступов и не экранирует кавычки.
    """
    global _TELETHON_PATCHED
    if _TELETHON_PATCHED:
        return
    try:
        from telethon.extensions import html as tl_html
    except Exception:
        return
    frx_html_parse.__wrapped__ = tl_html.parse
    frx_html_unparse.__wrapped__ = tl_html.unparse
    tl_html.parse = frx_html_parse
    tl_html.unparse = frx_html_unparse
    _TELETHON_PATCHED = True


def safe_html(text):
    """
    Возвращает text, пригодный для parse_mode=HTML в Telegram: настоящие
    корректные теги сохраняются, всё остальное экранируется и отображается
    буквально (см. докстринг модуля). None и не-строки возвращаются как есть.
    Премиум-эмодзи в стиле Hikka (<emoji document_id=N>) переводятся в <tg-emoji emoji-id="N">.
    """
    if not isinstance(text, str) or not text:
        return text
    text = normalize_emoji_tags(text)

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


def _strip_markup(markup) -> bool:
    """Убирает icon_custom_emoji_id у кнопок клавиатуры (на месте). True — что-то убрано."""
    changed = False
    for row in getattr(markup, 'inline_keyboard', None) or []:
        for b in row:
            if getattr(b, 'icon_custom_emoji_id', None):
                try:
                    b.icon_custom_emoji_id = None
                    changed = True
                except Exception:
                    pass
    return changed


def strip_bot_method_custom_emoji(method) -> bool:
    """
    Заменяет премиум-эмодзи обычными во всех HTML-полях и кнопках запроса Bot API (на месте).
    True — в запросе было что менять (его стоит повторить), False — премиум-эмодзи не было.
    """
    changed = False
    for field in ('text', 'caption'):
        value = getattr(method, field, None)
        if has_custom_emoji(value):
            setattr(method, field, strip_custom_emoji(value))
            changed = True
    if _strip_markup(getattr(method, 'reply_markup', None)):
        changed = True
    results = getattr(method, 'results', None)
    if isinstance(results, (list, tuple)) and results:
        new = []
        for r in results:
            update = {}
            content = getattr(r, 'input_message_content', None)
            mt = getattr(content, 'message_text', None)
            if has_custom_emoji(mt):
                update['input_message_content'] = content.model_copy(update={'message_text': strip_custom_emoji(mt)})
            cap = getattr(r, 'caption', None)
            if has_custom_emoji(cap):
                update['caption'] = strip_custom_emoji(cap)
            if _strip_markup(getattr(r, 'reply_markup', None)):
                changed = True
            if update:
                changed = True
                r = r.model_copy(update=update)
            new.append(r)
        method.results = new
    return changed
