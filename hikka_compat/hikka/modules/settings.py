"""Встроенный модуль пространства Hikka: префикс, алиасы, чёрные списки, NoNick."""

import logging

from telethon.tl.types import Message

from .. import loader, utils

logger = logging.getLogger(__name__)

MAIN = "hikka.main"


@loader.tds
class CoreMod(loader.Module):
    """Префикс, алиасы и ограничения пространства Hikka"""

    strings = {
        "name": "Settings",
        "prefix_set": "✅ <b>Префикс команд Hikka:</b> <code>{}</code>\n<i>Сбросить: {}setprefix {}</i>",
        "prefix_bad": "🚫 <b>Префикс — 1-2 символа без пробелов</b>",
        "prefix_frx": "⚠️ <b>Это префикс FRX</b> — команды с одинаковыми именами выполнятся дважды. Всё равно ставлю.",
        "prefix_now": "ℹ️ <b>Префикс команд Hikka:</b> <code>{}</code>",
        "alias_created": "✅ <b>Алиас</b> <code>{}</code> → <code>{}</code>",
        "alias_removed": "✅ <b>Алиас</b> <code>{}</code> <b>удалён</b>",
        "no_command": "🚫 <b>Команда</b> <code>{}</code> <b>не найдена</b>",
        "no_alias": "🚫 <b>Алиас</b> <code>{}</code> <b>не найден</b>",
        "alias_args": "🚫 <b>Формат:</b> <code>{}addalias &lt;алиас&gt; &lt;команда&gt; [аргументы]</code>",
        "aliases": "🔗 <b>Алиасы:</b>\n{}",
        "no_aliases": "ℹ️ <b>Алиасов нет</b>",
        "blacklisted": "✅ <b>Чат</b> <code>{}</code> <b>в чёрном списке</b> — команды Hikka там не работают",
        "unblacklisted": "✅ <b>Чат</b> <code>{}</code> <b>убран из чёрного списка</b>",
        "user_blacklisted": "✅ <b>Пользователь</b> <code>{}</code> <b>в чёрном списке</b>",
        "user_unblacklisted": "✅ <b>Пользователь</b> <code>{}</code> <b>убран из чёрного списка</b>",
        "what_user": "🚫 <b>Ответьте на сообщение пользователя или укажите его</b>",
        "nn_on": "✅ <b>NoNick включён для</b> <code>{}</code>",
        "nn_off": "✅ <b>NoNick выключен для</b> <code>{}</code>",
        "grep_on": "✅ <b>| grep включён</b>",
        "grep_off": "✅ <b>| grep выключен</b>",
    }

    async def _frx_prefix(self):
        from .. import _frx

        try:
            return await _frx.engine.get_prefix()
        except Exception:
            return "."

    @loader.command(ru_doc="<префикс> — сменить префикс команд Hikka")
    async def setprefix(self, message: Message):
        """<prefix> - change Hikka commands prefix"""
        args = (utils.get_args_raw(message) or "").strip()
        old = self.get_prefix()
        if not args:
            await utils.answer(message, self.strings("prefix_now").format(utils.escape_html(old)))
            return
        if len(args) > 2 or " " in args:
            await utils.answer(message, self.strings("prefix_bad"))
            return
        self._db.set(MAIN, "command_prefix", args)
        text = self.strings("prefix_set").format(utils.escape_html(args), utils.escape_html(args), utils.escape_html(old))
        if args == await self._frx_prefix():
            text = self.strings("prefix_frx") + "\n\n" + text
        await utils.answer(message, text)

    @loader.command(ru_doc="<алиас> <команда> [аргументы] — создать алиас")
    async def addalias(self, message: Message):
        """<alias> <command> [args] - add alias"""
        args = utils.get_args(message) or []
        if len(args) < 2:
            await utils.answer(message, self.strings("alias_args").format(utils.escape_html(self.get_prefix())))
            return
        alias, cmd, rest = args[0], args[1], " ".join(args[2:])
        if self.allmodules.add_alias(alias, cmd, rest or None):
            await utils.answer(message, self.strings("alias_created").format(utils.escape_html(alias),
                                                                             utils.escape_html(f"{cmd} {rest}".strip())))
        else:
            await utils.answer(message, self.strings("no_command").format(utils.escape_html(cmd)))

    @loader.command(ru_doc="<алиас> — удалить алиас")
    async def delalias(self, message: Message):
        """<alias> - remove alias"""
        args = utils.get_args(message) or []
        if not args:
            await utils.answer(message, self.strings("no_alias").format(""))
            return
        if self.allmodules.remove_alias(args[0]):
            await utils.answer(message, self.strings("alias_removed").format(utils.escape_html(args[0])))
        else:
            await utils.answer(message, self.strings("no_alias").format(utils.escape_html(args[0])))

    @loader.command(ru_doc="список алиасов")
    async def aliases(self, message: Message):
        """list aliases"""
        if not self.allmodules.aliases:
            await utils.answer(message, self.strings("no_aliases"))
            return
        lines = "\n".join(
            f"▫️ <code>{utils.escape_html(a)}</code> → <code>{utils.escape_html(c)}</code>"
            for a, c in sorted(self.allmodules.aliases.items())
        )
        await utils.answer(message, self.strings("aliases").format(lines))

    def _toggle(self, key, value, add: bool):
        items = self._db.get(MAIN, key, [])
        if add and value not in items:
            items.append(value)
        if not add and value in items:
            items.remove(value)
        self._db.set(MAIN, key, items)

    @loader.command(ru_doc="[чат] — запретить команды Hikka в чате")
    async def blacklist(self, message: Message):
        """[chat] - disable Hikka commands in chat"""
        chat = int(utils.get_args_raw(message) or utils.get_chat_id(message))
        self._toggle("blacklist_chats", chat, True)
        await utils.answer(message, self.strings("blacklisted").format(chat))

    @loader.command(ru_doc="[чат] — разрешить команды Hikka в чате")
    async def unblacklist(self, message: Message):
        """[chat] - enable Hikka commands in chat"""
        chat = int(utils.get_args_raw(message) or utils.get_chat_id(message))
        self._toggle("blacklist_chats", chat, False)
        await utils.answer(message, self.strings("unblacklisted").format(chat))

    async def _user(self, message):
        args = utils.get_args_raw(message)
        if args:
            try:
                return (await self._client.get_entity(int(args) if args.lstrip("-").isdigit() else args)).id
            except Exception:
                return None
        reply = await message.get_reply_message()
        return reply.sender_id if reply else None

    @loader.command(ru_doc="<пользователь> — игнорировать его команды")
    async def blacklistuser(self, message: Message):
        """<user> - ignore user's commands"""
        user = await self._user(message)
        if not user:
            await utils.answer(message, self.strings("what_user"))
            return
        self._toggle("blacklist_users", user, True)
        await utils.answer(message, self.strings("user_blacklisted").format(user))

    @loader.command(ru_doc="<пользователь> — снова принимать его команды")
    async def unblacklistuser(self, message: Message):
        """<user> - stop ignoring user's commands"""
        user = await self._user(message)
        if not user:
            await utils.answer(message, self.strings("what_user"))
            return
        self._toggle("blacklist_users", user, False)
        await utils.answer(message, self.strings("user_unblacklisted").format(user))

    async def _nn(self, message, key, value):
        items = self._db.get(MAIN, key, [])
        add = value not in items
        self._toggle(key, value, add)
        await utils.answer(message, self.strings("nn_on" if add else "nn_off").format(utils.escape_html(value)))

    @loader.command(ru_doc="<пользователь> — команды без @юзернейма для пользователя")
    async def nonickuser(self, message: Message):
        """<user> - commands without @username for user"""
        user = await self._user(message)
        if not user:
            await utils.answer(message, self.strings("what_user"))
            return
        await self._nn(message, "nonickusers", user)

    @loader.command(ru_doc="[чат] — команды без @юзернейма в чате")
    async def nonickchat(self, message: Message):
        """[chat] - commands without @username in chat"""
        await self._nn(message, "nonickchats", int(utils.get_args_raw(message) or utils.get_chat_id(message)))

    @loader.command(ru_doc="<команда> — команда без @юзернейма для всех")
    async def nonickcmdcmd(self, message: Message):
        """<command> - command without @username"""
        cmd = (utils.get_args_raw(message) or "").strip().lower()
        if cmd not in self.allmodules.commands:
            await utils.answer(message, self.strings("no_command").format(utils.escape_html(cmd)))
            return
        await self._nn(message, "nonickcmds", cmd)

    @loader.command(ru_doc="включить / выключить фильтр «| grep» в командах")
    async def togglegrep(self, message: Message):
        """toggle `| grep` support"""
        value = not self._db.get(MAIN, "grep", False)
        self._db.set(MAIN, "grep", value)
        await utils.answer(message, self.strings("grep_on" if value else "grep_off"))
