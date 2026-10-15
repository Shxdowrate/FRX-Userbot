"""HTML ⇄ сущности Telegram для модулей Hikka/Heroku.

Стандартный парсер Telethon не знает часть тегов, которыми пользуются модули Hikka (их форки Telethon
— Hikka-TL / Heroku-TL — знают): `<emoji document_id=…>` (премиум-эмодзи в стиле Hikka), `<tg-spoiler>`,
`<span class="tg-spoiler">`, `<ins>`, `<strike>`. Здесь — надстройка над парсером Telethon, которая понимает
и их, плюс обратное преобразование (unparse) со всеми сущностями. Объект модуля годится как parse_mode
(у него есть parse и unparse).
"""

from html import escape as _escape
from typing import Iterable, List, Tuple

from telethon.extensions import html as _tl_html
from telethon.helpers import add_surrogate, del_surrogate, strip_text, within_surrogate
from telethon.tl import TLObject
from telethon.tl import types as _t

CUSTOM_EMOJIS = True          # Hikka позволяет отключать премиум-эмодзи снаружи

_SPOILER = getattr(_t, "MessageEntitySpoiler", None)
_DATE = getattr(_t, "MessageEntityFormattedDate", None)


class HTMLToTelegramParser(_tl_html.HTMLToTelegramParser):
    """Парсер Telethon + теги форков Hikka/Heroku."""

    def handle_starttag(self, tag, attrs):
        amap = dict(attrs)
        entity_type, args, key = None, {}, tag

        if tag in ("ins",):
            entity_type = _t.MessageEntityUnderline
        elif tag in ("strike",):
            entity_type = _t.MessageEntityStrike
        elif tag == "tg-spoiler" and _SPOILER:
            entity_type = _SPOILER
        elif tag == "span" and "tg-spoiler" in (amap.get("class") or "").split() and _SPOILER:
            entity_type = _SPOILER
        elif tag == "emoji":
            raw = amap.get("document_id") or amap.get("emoji-id") or amap.get("id")
            if CUSTOM_EMOJIS and raw:
                try:
                    args["document_id"] = int(str(raw).strip().strip('"\''))
                    entity_type = _t.MessageEntityCustomEmoji
                except ValueError:
                    entity_type = None
        elif tag == "tg-emoji" and not CUSTOM_EMOJIS:
            # отключены — оставляем просто текст эмодзи
            self._open_tags.appendleft(tag)
            self._open_tags_meta.appendleft(None)
            return
        elif tag in ("tg-time", "date") and _DATE:
            try:
                import datetime

                stamp = amap.get("unix") or amap.get("date")
                if stamp is None:
                    raise ValueError
                stamp = str(stamp)
                date = (
                    datetime.datetime.fromtimestamp(int(stamp), datetime.timezone.utc)
                    if stamp.isdigit()
                    else datetime.datetime.fromisoformat(stamp)
                )
                args["date"] = date
                for flag in ("relative", "short_time", "long_time", "short_date", "long_date", "day_of_week"):
                    if flag in amap or flag.replace("_", "-") in amap:
                        args[flag] = True
                entity_type = _DATE
            except Exception:
                entity_type = None
        else:
            return super().handle_starttag(tag, attrs)

        self._open_tags.appendleft(tag)
        self._open_tags_meta.appendleft(None)
        if entity_type and key not in self._building_entities:
            try:
                self._building_entities[key] = entity_type(offset=len(self.text), length=0, **args)
            except TypeError:
                pass


def parse(html: str) -> Tuple[str, List]:
    """HTML → (текст, [сущности])."""
    if not html:
        return html, []
    parser = HTMLToTelegramParser()
    parser.feed(add_surrogate(str(html)))
    parser.close()
    # незакрытые теги — закрываем в конце текста, как это делают клиенты Telegram
    for entity in parser._building_entities.values():
        parser.entities.append(entity)
    parser._building_entities.clear()
    text = strip_text(parser.text, parser.entities)
    parser.entities.reverse()
    parser.entities.sort(key=lambda e: e.offset)
    return del_surrogate(text), parser.entities


def _formatter(entity, text):
    t = type(entity)
    if t is _t.MessageEntityBold:
        return "<b>", "</b>"
    if t is _t.MessageEntityItalic:
        return "<i>", "</i>"
    if t is _t.MessageEntityUnderline:
        return "<u>", "</u>"
    if t is _t.MessageEntityStrike:
        return "<s>", "</s>"
    if t is _t.MessageEntityCode:
        return "<code>", "</code>"
    if t is _t.MessageEntityPre:
        lang = getattr(entity, "language", "") or ""
        if lang:
            return f'<pre><code class="language-{_escape(lang)}">', "</code></pre>"
        return "<pre>", "</pre>"
    if t is _t.MessageEntityBlockquote:
        return ("<blockquote expandable>" if getattr(entity, "collapsed", False) else "<blockquote>"), "</blockquote>"
    if _SPOILER is not None and t is _SPOILER:
        return "<tg-spoiler>", "</tg-spoiler>"
    if t is _t.MessageEntityTextUrl:
        return f'<a href="{_escape(entity.url)}">', "</a>"
    if t is _t.MessageEntityUrl:
        return f'<a href="{_escape(text)}">', "</a>"
    if t is _t.MessageEntityEmail:
        return f'<a href="mailto:{_escape(text)}">', "</a>"
    if t is _t.MessageEntityMentionName:
        return f'<a href="tg://user?id={entity.user_id}">', "</a>"
    if t is _t.MessageEntityCustomEmoji:
        return f'<tg-emoji emoji-id="{entity.document_id}">', "</tg-emoji>"
    if _DATE is not None and t is _DATE:
        try:
            return f'<tg-time unix="{int(entity.date.timestamp())}">', "</tg-time>"
        except Exception:
            return None
    return None


def unparse(text: str, entities: Iterable) -> str:
    """(текст, [сущности]) → HTML."""
    if not text:
        return text
    if not entities:
        return _escape(text)
    if isinstance(entities, TLObject):
        entities = (entities,)

    text = add_surrogate(text)
    marks = []                                   # (позиция, порядок, строка)
    for index, entity in enumerate(entities):
        start, end = entity.offset, entity.offset + entity.length
        fmt = _formatter(entity, del_surrogate(text[start:end]))
        if not fmt:
            continue
        # открывающие — в порядке сущностей, закрывающие — в обратном (правильная вложенность)
        marks.append((start, 1, index, fmt[0]))
        marks.append((end, 0, -index, fmt[1]))

    marks.sort(key=lambda m: (m[0], m[1], m[2]))
    out, pos = [], 0
    for at, _, _, tag in marks:
        while within_surrogate(text, at):
            at += 1
        if at > pos:
            out.append(_escape(text[pos:at]))
            pos = at
        out.append(tag)
    out.append(_escape(text[pos:]))
    return del_surrogate("".join(out))


def escape(text: str) -> str:
    return _escape(str(text), quote=False)
