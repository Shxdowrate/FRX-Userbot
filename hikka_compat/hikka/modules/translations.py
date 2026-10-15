"""Встроенный модуль пространства Hikka: язык модулей и служебные строки."""

import logging

from telethon.tl.types import Message

from .. import loader, utils
from ..translations import CORE_STRINGS, DB_KEY, MEME_LANGUAGES, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


@loader.tds
class Translations(loader.Module):
    """Язык модулей Hikka"""

    strings = {"name": "Translations", **{k.split(".", 1)[1]: v for k, v in CORE_STRINGS["ru"].items() if k.startswith("translations.")}}
    strings_en = {k.split(".", 1)[1]: v for k, v in CORE_STRINGS["en"].items() if k.startswith("translations.")}
    strings_ru = dict(strings)

    @loader.command(ru_doc="[код языка | ссылка] — язык модулей (ru, en, uk, de, ja; можно несколько через пробел)")
    async def setlang(self, message: Message):
        """[language code | url] - modules language"""
        args = (utils.get_args_raw(message) or "").strip().lower()
        if not args:
            if not self.inline.init_complete:
                langs = ", ".join(f"<code>{c}</code> {n}" for c, n in SUPPORTED_LANGUAGES.items())
                await utils.answer(message, self.strings("choose_language") + "\n\n" + langs)
                return
            buttons = [
                {"text": name, "callback": self._choose, "args": (code,)}
                for code, name in {**SUPPORTED_LANGUAGES, **MEME_LANGUAGES}.items()
            ]
            await self.inline.form(self.strings("choose_language"), message, reply_markup=list(utils.chunks(buttons, 2)))
            return
        for lang in args.split():
            if len(lang) != 2 and not lang.startswith("http") and lang not in MEME_LANGUAGES:
                await utils.answer(message, self.strings("incorrect_language"))
                return
        await self._set(args)
        flag = utils.get_lang_flag(args.split()[0]) if len(args.split()[0]) == 2 else "🌐"
        text = self.strings("lang_saved").format(flag)
        if args.split()[0] not in SUPPORTED_LANGUAGES and not args.startswith("http"):
            text += "\n" + self.strings("not_official")
        await utils.answer(message, text)

    async def _set(self, lang: str):
        self._db.set(DB_KEY, "lang", lang)
        await self.allmodules.reload_translations()

    async def _choose(self, call, code: str):
        await self._set(code)
        await call.edit(self.strings("lang_saved").format(utils.get_lang_flag(code) if len(code) == 2 else "🏴‍☠️"))

    @loader.command(ru_doc="<ссылка> — подключить языковой пакет")
    async def dllangpack(self, message: Message):
        """<url> - load language pack"""
        args = (utils.get_args_raw(message) or "").strip()
        if not utils.check_url(args):
            await utils.answer(message, self.strings("check_url"))
            return
        await self._set(args)
        await utils.answer(message, self.strings("pack_saved"))
