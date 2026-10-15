"""Встроенный модуль пространства Hikka: выполнение Python-кода (как .e в Hikka)."""

import ast
import contextlib
import io
import itertools
import logging
import sys
import traceback
import types

import telethon
from telethon.tl.types import Message

from .. import loader, main, utils

logger = logging.getLogger(__name__)


async def run_code(code: str, env: dict):
    """Выполнить код как тело async-функции; значение последнего выражения возвращается (как meval)."""
    tree = ast.parse(code, "<eval>", "exec")
    body = tree.body
    if body and isinstance(body[-1], ast.Expr):
        body[-1] = ast.Return(value=body[-1].value)
    module = ast.parse("async def __hikka_eval():\n    pass\n")
    module.body[0].body = body or [ast.Pass()]
    ast.fix_missing_locations(module)
    namespace = dict(env)
    exec(compile(module, "<eval>", "exec"), namespace)
    return await namespace["__hikka_eval"]()


@loader.tds
class EvaluatorMod(loader.Module):
    """Выполнение кода Python"""

    strings = {
        "name": "Evaluator",
        "eval": "<b>💻 Код:</b>\n<pre><code class=\"language-python\">{}</code></pre>\n<b>✅ Результат:</b>\n<pre><code class=\"language-python\">{}</code></pre>",
        "err": "<b>💻 Код:</b>\n<pre><code class=\"language-python\">{}</code></pre>\n<b>🚫 Ошибка:</b>\n<pre><code class=\"language-python\">{}</code></pre>",
        "no_code": "🚫 <b>Укажите код</b>",
    }

    async def getattrs(self, message: Message) -> dict:
        reply = await message.get_reply_message()
        return {
            "message": message,
            "client": self._client,
            "reply": reply,
            "r": reply,
            "event": message,
            "chat": message.to_id,
            **self.get_sub(telethon.tl.types),
            **self.get_sub(telethon.tl.functions),
            "telethon": telethon,
            "hikkatl": telethon,
            "herokutl": telethon,
            "utils": utils,
            "main": main,
            "loader": loader,
            "f": telethon.tl.functions,
            "c": self._client,
            "m": message,
            "lookup": self.lookup,
            "self": self,
            "db": self.db,
        }

    def get_sub(self, obj, _depth: int = 1) -> dict:
        return {
            name: getattr(obj, name)
            for name in dir(obj)
            if not name.startswith("_") and isinstance(getattr(obj, name, None), type)
        }

    def censor(self, ret):
        """Спрятать номер телефона, ключи и токены в выводе."""
        text = str(ret)
        me = getattr(self._client, "hikka_me", None)
        phone = getattr(me, "phone", None)
        if phone:
            text = text.replace(phone, "📵")
        with contextlib.suppress(Exception):
            from .. import _frx

            cfg = getattr(_frx.engine, "config", {}) or {}
            for key in ("api_hash", "aiogram_token", "bot_token"):
                value = cfg.get(key)
                if value:
                    text = text.replace(str(value), f"<{key}>")
        return text

    @loader.command(alias="eval", ru_doc="<код> — выполнить Python")
    async def e(self, message: Message):
        """<code> - evaluate Python"""
        code = utils.get_args_raw(message)
        if not code:
            await utils.answer(message, self.strings("no_code"))
            return
        try:
            result = await run_code(code, await self.getattrs(message))
        except Exception:
            tb = traceback.format_exc().splitlines()
            tb = "\n".join(tb[-8:])
            await utils.answer(message, self.strings("err").format(utils.escape_html(code), utils.escape_html(self.censor(tb))))
            return
        if callable(getattr(result, "stringify", None)):
            with contextlib.suppress(Exception):
                result = result.stringify()
        await utils.answer(
            message, self.strings("eval").format(utils.escape_html(code), utils.escape_html(self.censor(result)))
        )
