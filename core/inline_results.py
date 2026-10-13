"""
core/inline_results.py — настоящие инлайн-результаты модулей (`@bot.inline_query`) и нажатия на их кнопки
(`@bot.inline_click`). См. README, «Инлайн-запросы модулей».

    @bot.inline_query('w', 'weather', access='public')
    async def weather_iq(query):
        return [engine.InlineResult("Москва: +5°", text="<b>Москва</b>\n+5°, облачно", buttons=[[("Обновить", "w:msk")]])]

    @bot.inline_click(data_startswith='w:')
    async def weather_click(click):
        await click.edit("<b>Москва</b>\n+6°", buttons=[[("Обновить", "w:msk")]])
        await click.answer("обновлено")
"""

import uuid


class InlineResult:
    """
    Один результат в списке инлайн-режима.
        title        — заголовок в списке (обязателен)
        text         — что уйдёт в чат (HTML Telegram); по умолчанию — title
        description  — серая строка под заголовком
        buttons      — кнопки под сообщением: как у create_inlineform ((текст, data[, цвет]), 'url:…')
        styles       — цвета по callback_data
        thumb        — картинка-миниатюра в списке (https-ссылка)
        photo        — вместо текста отправить фото по ссылке (text станет подписью)
        link_preview — None (Telegram решит сам) | False — без превью | True — превью первой ссылки |
                       строка-URL — превью этого адреса (картинка, сайт); крупно и над текстом
        id           — свой id результата (по умолчанию случайный)
        on_chosen    — async-функция(inline_message_id), вызывается, когда человек ОТПРАВИЛ этот результат
                       (запросы приходят на каждую букву — сохраняйте данные здесь, а не в самом inline_query)
    """
    __slots__ = ('title', 'text', 'description', 'buttons', 'styles', 'thumb', 'photo', 'link_preview', 'id', 'on_chosen')

    def __init__(self, title, text=None, description=None, buttons=None, styles=None, thumb=None, photo=None,
                 link_preview=None, id=None, on_chosen=None):
        self.title = str(title)[:200] or '…'
        self.text = text
        self.description = description
        self.buttons = buttons
        self.styles = styles
        self.thumb = thumb
        self.photo = photo
        self.link_preview = link_preview
        self.id = id or uuid.uuid4().hex
        self.on_chosen = on_chosen


def link_preview_options(value):
    """None | False | True | 'https://…' → aiogram LinkPreviewOptions (или None)."""
    if value is None:
        return None
    from aiogram.types import LinkPreviewOptions
    if value is False:
        return LinkPreviewOptions(is_disabled=True)
    if value is True:
        return LinkPreviewOptions(prefer_large_media=True)
    return LinkPreviewOptions(url=str(value), prefer_large_media=True, show_above_text=True)


class InlineQueryEvent:
    """То, что получает @bot.inline_query: кто и что набирает."""

    def __init__(self, iq, name, text):
        u = iq.from_user
        self.raw = iq
        self.query = iq.query or ''
        self.name = name
        self.text = text
        self.user_id = u.id if u else None
        self.username = (u.username or '') if u else ''
        self.first_name = (u.first_name or '') if u else ''


class InlineQueryClick:
    """Нажатие кнопки под сообщением, отправленным через @bot.inline_query."""

    def __init__(self, core, callback):
        u = callback.from_user
        self._core = core
        self.raw = callback
        self.data = callback.data or ''
        self.inline_message_id = callback.inline_message_id
        self.user_id = u.id if u else None
        self.username = (u.username or '') if u else ''
        self.first_name = (u.first_name or '') if u else ''
        self._answered = False

    async def answer(self, text=None, show_alert=False, url=None):
        if self._answered:
            return
        self._answered = True
        try:
            await self.raw.answer(text=(str(text)[:200] if text else None), show_alert=show_alert, url=url)
        except Exception:
            pass

    async def edit(self, text, buttons=None, styles=None, link_preview=None):
        """Переписать сообщение (текст HTML). buttons=None — без кнопок."""
        markup = self._core._build_markup(buttons, styles) if buttons else None
        kw = {}
        lp = link_preview_options(link_preview)
        if lp is not None:
            kw['link_preview_options'] = lp
        await self._core._aiogram_bot.edit_message_text(
            text=text, inline_message_id=self.inline_message_id, reply_markup=markup, parse_mode='HTML', **kw)

    async def edit_buttons(self, buttons=None, styles=None):
        markup = self._core._build_markup(buttons, styles) if buttons else None
        await self._core._aiogram_bot.edit_message_reply_markup(inline_message_id=self.inline_message_id, reply_markup=markup)
