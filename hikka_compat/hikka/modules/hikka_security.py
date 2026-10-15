"""Встроенный модуль пространства Hikka: кто, кроме вас, может пользоваться командами модулей Hikka."""

import logging
import re
import time

from telethon.tl.types import Message, User
from telethon.utils import get_display_name

from .. import loader, security, utils

logger = logging.getLogger(__name__)

DB = security.DB_KEY

FLAGS = [
    ("owner", security.OWNER, "😎 Владелец"),
    ("group_owner", security.GROUP_OWNER, "🧛 Владелец группы"),
    ("group_admin_add_admins", security.GROUP_ADMIN_ADD_ADMINS, "🧑‍⚖️ Админ (добавлять админов)"),
    ("group_admin_change_info", security.GROUP_ADMIN_CHANGE_INFO, "🧑‍⚖️ Админ (менять инфо)"),
    ("group_admin_ban_users", security.GROUP_ADMIN_BAN_USERS, "🧑‍⚖️ Админ (банить)"),
    ("group_admin_delete_messages", security.GROUP_ADMIN_DELETE_MESSAGES, "🧑‍⚖️ Админ (удалять)"),
    ("group_admin_pin_messages", security.GROUP_ADMIN_PIN_MESSAGES, "🧑‍⚖️ Админ (закреплять)"),
    ("group_admin_invite_users", security.GROUP_ADMIN_INVITE_USERS, "🧑‍⚖️ Админ (приглашать)"),
    ("group_admin", security.GROUP_ADMIN, "🧑‍⚖️ Админ (любой)"),
    ("group_member", security.GROUP_MEMBER, "👥 Участник группы"),
    ("pm", security.PM, "🤙 В личке"),
    ("everyone", security.EVERYONE, "🌍 Все (инлайн)"),
]


def _duration(text: str) -> int:
    match = re.fullmatch(r"(\d+)\s*([smhd]?)", text or "")
    if not match:
        return 0
    return int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600, "d": 86400}[match.group(2)]


@loader.tds
class HikkaSecurityMod(loader.Module):
    """Права на команды модулей Hikka для других людей"""

    strings = {
        "name": "HikkaSecurity",
        "global": "🔐 <b>Общая «рамка» прав.</b> Выключенное здесь не работает ни для одной команды.",
        "command": "🔐 <b>Права команды</b> <code>{}{}</code>",
        "no_command": "🚫 <b>Команда</b> <code>{}</code> <b>не найдена</b>",
        "owner_added": "😎 <b>{}</b> <b>добавлен во владельцы пространства Hikka</b>",
        "owner_removed": "😎 <b>{}</b> <b>убран из владельцев</b>",
        "owners": "😎 <b>Владельцы пространства Hikka:</b>\n{}",
        "no_user": "🚫 <b>Укажите пользователя (ответом или @юзернеймом)</b>",
        "self": "🚫 <b>Нельзя менять свои права</b>",
        "tsec_added": "⏳ <b>{}</b> <b>получил доступ к</b> <code>{}</code>{}",
        "tsec_removed": "✅ <b>Правила для</b> <b>{}</b> <b>удалены</b>",
        "tsec_list": "⏳ <b>Временные правила:</b>\n{}",
        "tsec_none": "ℹ️ <b>Временных правил нет</b>",
        "tsec_args": "🚫 <b>Формат:</b> <code>{}tsec &lt;пользователь&gt; &lt;команда | модуль&gt; [время: 30m, 2h, 1d]</code>",
        "for": " <b>на</b> {}",
        "forever": " <b>навсегда</b>",
        "close": "🔻 Закрыть",
    }

    async def _user(self, message, args):
        if args:
            try:
                return await self._client.get_entity(int(args[0]) if args[0].lstrip("-").isdigit() else args[0])
            except Exception:
                return None
        reply = await message.get_reply_message()
        if reply:
            return await reply.get_sender()
        return None

    # ------------------------------------------------------------------ маски
    def _mask_key(self, func):
        target = getattr(func, "__func__", func)
        return f"{target.__module__}.{target.__name__}"

    def _markup(self, mask: int, command: str = None):
        rows = []
        for key, bit, title in FLAGS:
            mark = "✅" if mask & bit else "▫️"
            rows.append({"text": f"{mark} {title}", "callback": self._toggle, "args": (command, bit)})
        return list(utils.chunks(rows, 2)) + [[{"text": self.strings("close"), "action": "close"}]]

    def _current(self, command):
        if command is None:
            return self._db.get(DB, "bounding_mask", security.DEFAULT_PERMISSIONS)
        func = self.allmodules.commands[command]
        return self._db.get(DB, "masks", {}).get(self._mask_key(func), getattr(func, "security", security.DEFAULT_PERMISSIONS))

    async def _toggle(self, call, command, bit):
        mask = self._current(command) ^ bit
        if command is None:
            self._db.set(DB, "bounding_mask", mask)
        else:
            masks = self._db.get(DB, "masks", {})
            masks[self._mask_key(self.allmodules.commands[command])] = mask
            self._db.set(DB, "masks", masks)
        await call.edit(self._title(command), reply_markup=self._markup(mask, command))

    def _title(self, command):
        if command is None:
            return self.strings("global")
        return self.strings("command").format(utils.escape_html(self.get_prefix()), utils.escape_html(command))

    @loader.command(ru_doc="[команда] — права команды (без аргумента — общая рамка прав)")
    async def security(self, message: Message):
        """[command] - command permissions (no args - global mask)"""
        command = (utils.get_args_raw(message) or "").strip().lower().lstrip(self.get_prefix()) or None
        if command is not None:
            command = self.allmodules.find_alias(command, include_legacy=True) or command
            command = command.split()[0]
            if command not in self.allmodules.commands:
                await utils.answer(message, self.strings("no_command").format(utils.escape_html(command)))
                return
        await self.inline.form(self._title(command), message, reply_markup=self._markup(self._current(command), command))

    # ------------------------------------------------------------------ владельцы
    @loader.command(ru_doc="<пользователь> — дать полный доступ к командам Hikka")
    async def owneradd(self, message: Message):
        """<user> - add owner"""
        user = await self._user(message, utils.get_args(message))
        if not isinstance(user, User):
            await utils.answer(message, self.strings("no_user"))
            return
        if user.id == self.tg_id:
            await utils.answer(message, self.strings("self"))
            return
        owners = self._client.dispatcher.security._owner
        if user.id not in owners:
            owners.append(user.id)
        await utils.answer(message, self.strings("owner_added").format(utils.escape_html(get_display_name(user))))

    @loader.command(ru_doc="<пользователь> — забрать полный доступ")
    async def ownerrm(self, message: Message):
        """<user> - remove owner"""
        user = await self._user(message, utils.get_args(message))
        if not isinstance(user, User):
            await utils.answer(message, self.strings("no_user"))
            return
        if user.id == self.tg_id:
            await utils.answer(message, self.strings("self"))
            return
        owners = self._client.dispatcher.security._owner
        if user.id in owners:
            owners.remove(user.id)
        await utils.answer(message, self.strings("owner_removed").format(utils.escape_html(get_display_name(user))))

    @loader.command(ru_doc="список владельцев")
    async def ownerlist(self, message: Message):
        """list owners"""
        lines = []
        for uid in self._client.dispatcher.security._owner:
            try:
                name = get_display_name(await self._client.get_entity(uid))
            except Exception:
                name = str(uid)
            lines.append(f"▫️ <a href=\"tg://user?id={uid}\">{utils.escape_html(name)}</a>")
        await utils.answer(message, self.strings("owners").format("\n".join(lines)))

    # ------------------------------------------------------------------ временные правила
    @loader.command(ru_doc="<пользователь> <команда | модуль> [время] — временный доступ")
    async def tsec(self, message: Message):
        """<user> <command | module> [time] - temporary access"""
        args = utils.get_args(message) or []
        reply = await message.get_reply_message()
        if reply and len(args) >= 1 and not (args[0].startswith("@") or args[0].lstrip("-").isdigit()):
            user = await reply.get_sender()
            rest = args
        else:
            user = await self._user(message, args[:1])
            rest = args[1:]
        if not isinstance(user, User) or not rest:
            await utils.answer(message, self.strings("tsec_args").format(utils.escape_html(self.get_prefix())))
            return
        target = rest[0].lower()
        seconds = _duration(rest[1]) if len(rest) > 1 else 0
        mod = self.allmodules.lookup(target)
        if target in self.allmodules.commands:
            rule = f"command/{target}"
        elif mod:
            rule = f"module/{mod.__class__.__name__}"
        else:
            await utils.answer(message, self.strings("no_command").format(utils.escape_html(target)))
            return
        self._client.dispatcher.security.add_rule("user", user, rule, seconds)
        when = self.strings("for").format(f"{seconds} с") if seconds else self.strings("forever")
        await utils.answer(
            message,
            self.strings("tsec_added").format(utils.escape_html(get_display_name(user)), utils.escape_html(rule), when),
        )

    @loader.command(ru_doc="<пользователь> — убрать временные правила")
    async def tsecrm(self, message: Message):
        """<user> - remove temporary rules"""
        user = await self._user(message, utils.get_args(message))
        if not isinstance(user, User):
            await utils.answer(message, self.strings("no_user"))
            return
        self._client.dispatcher.security.remove_rules("user", user.id)
        await utils.answer(message, self.strings("tsec_removed").format(utils.escape_html(get_display_name(user))))

    @loader.command(ru_doc="список временных правил")
    async def tseclist(self, message: Message):
        """list temporary rules"""
        rules = list(self._client.dispatcher.security._tsec_user) + list(self._client.dispatcher.security._tsec_chat)
        if not rules:
            await utils.answer(message, self.strings("tsec_none"))
            return
        lines = []
        for r in rules:
            left = f"{max(0, int(r['expires'] - time.time()))} с" if r.get("expires") else "∞"
            lines.append(f"▫️ {utils.escape_html(r.get('entity_name', r['target']))}: "
                         f"<code>{r['rule_type']}/{utils.escape_html(r['rule'])}</code> ({left})")
        await utils.answer(message, self.strings("tsec_list").format("\n".join(lines)))
