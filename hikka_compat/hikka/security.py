"""Права на команды модулей Hikka: кто, кроме владельца, может их вызывать (маски, временные правила, группы)."""

import logging
import time
import typing

from telethon.tl.functions.messages import GetFullChatRequest
from telethon.tl.types import ChatParticipantAdmin, ChatParticipantCreator
from telethon.utils import get_display_name

logger = logging.getLogger(__name__)

DB_KEY = "hikka.security"
MAIN_KEY = "hikka.main"

OWNER = 1 << 0
SUDO = 1 << 1
SUPPORT = 1 << 2
GROUP_OWNER = 1 << 3
GROUP_ADMIN_ADD_ADMINS = 1 << 4
GROUP_ADMIN_CHANGE_INFO = 1 << 5
GROUP_ADMIN_BAN_USERS = 1 << 6
GROUP_ADMIN_DELETE_MESSAGES = 1 << 7
GROUP_ADMIN_PIN_MESSAGES = 1 << 8
GROUP_ADMIN_INVITE_USERS = 1 << 9
GROUP_ADMIN = 1 << 10
GROUP_MEMBER = 1 << 11
PM = 1 << 12
EVERYONE = 1 << 13

BITMAP = {
    "OWNER": OWNER,
    "GROUP_OWNER": GROUP_OWNER,
    "GROUP_ADMIN_ADD_ADMINS": GROUP_ADMIN_ADD_ADMINS,
    "GROUP_ADMIN_CHANGE_INFO": GROUP_ADMIN_CHANGE_INFO,
    "GROUP_ADMIN_BAN_USERS": GROUP_ADMIN_BAN_USERS,
    "GROUP_ADMIN_DELETE_MESSAGES": GROUP_ADMIN_DELETE_MESSAGES,
    "GROUP_ADMIN_PIN_MESSAGES": GROUP_ADMIN_PIN_MESSAGES,
    "GROUP_ADMIN_INVITE_USERS": GROUP_ADMIN_INVITE_USERS,
    "GROUP_ADMIN": GROUP_ADMIN,
    "GROUP_MEMBER": GROUP_MEMBER,
    "PM": PM,
    "EVERYONE": EVERYONE,
}

GROUP_ADMIN_ANY = (
    GROUP_ADMIN_ADD_ADMINS
    | GROUP_ADMIN_CHANGE_INFO
    | GROUP_ADMIN_BAN_USERS
    | GROUP_ADMIN_DELETE_MESSAGES
    | GROUP_ADMIN_PIN_MESSAGES
    | GROUP_ADMIN_INVITE_USERS
    | GROUP_ADMIN
)
DEFAULT_PERMISSIONS = OWNER
PUBLIC_PERMISSIONS = GROUP_OWNER | GROUP_ADMIN_ANY | GROUP_MEMBER | PM
ALL = (1 << 13) - 1


class SecurityGroup(typing.NamedTuple):
    name: str
    users: list
    permissions: list


def _sec(func, flags: int):
    func.security = getattr(func, "security", 0) | OWNER | flags
    return func


def owner(func):
    return _sec(func, OWNER)


def _deprecated(func):
    return func


sudo = _deprecated
support = _deprecated


def group_owner(func):
    return _sec(func, GROUP_OWNER)


def group_admin_add_admins(func):
    return _sec(func, GROUP_ADMIN_ADD_ADMINS)


def group_admin_change_info(func):
    return _sec(func, GROUP_ADMIN_CHANGE_INFO)


def group_admin_ban_users(func):
    return _sec(func, GROUP_ADMIN_BAN_USERS)


def group_admin_delete_messages(func):
    return _sec(func, GROUP_ADMIN_DELETE_MESSAGES)


def group_admin_pin_messages(func):
    return _sec(func, GROUP_ADMIN_PIN_MESSAGES)


def group_admin_invite_users(func):
    return _sec(func, GROUP_ADMIN_INVITE_USERS)


def group_admin(func):
    return _sec(func, GROUP_ADMIN)


def group_member(func):
    return _sec(func, GROUP_MEMBER)


def pm(func):
    return _sec(func, PM)


def unrestricted(func):
    return _sec(func, ALL)


def inline_everyone(func):
    return _sec(func, EVERYONE)


def _chat_id(message):
    from . import utils

    return utils.get_chat_id(message)


class SecurityManager:
    """Проверяет, может ли автор сообщения запустить команду (по маскам и правилам из БД)."""

    def __init__(self, client, db):
        self._client = client
        self._db = db
        self._cache: dict = {}
        self._sgroups: dict = {}
        self._last_reload = 0.0
        self.any_admin = self._any_admin = db.get(DB_KEY, "any_admin", False)
        self.default = self._default = db.get(DB_KEY, "default", DEFAULT_PERMISSIONS)
        self.tsec_chat = self._tsec_chat = db.pointer(DB_KEY, "tsec_chat", [])
        self.tsec_user = self._tsec_user = db.pointer(DB_KEY, "tsec_user", [])
        self.owner = self._owner = db.pointer(DB_KEY, "owner", [])
        self.all_users = self._all_users = db.pointer(DB_KEY, "all_users", [])
        self._reload_rights(force=True)

    def apply_sgroups(self, sgroups: dict):
        self._sgroups = sgroups

    def _reload_rights(self, *, force: bool = False):
        now = time.monotonic()
        if not force and now - self._last_reload < 1.0:
            return
        self._last_reload = now
        me = self._client.tg_id
        if me and me not in self._owner:
            self._owner.append(me)
        for store in (self._tsec_user, self._tsec_chat):
            for rule in list(store):
                if rule.get("expires") and rule["expires"] < time.time():
                    store.remove(rule)
        users = set(self._owner) | {r["target"] for r in self._tsec_user}
        for group in self._sgroups.values():
            users.update(group.users)
        if set(self._all_users) != users:
            self._all_users.data = list(users)
        prefixes = self._db.get(MAIN_KEY, "command_prefixes", {})
        cleaned = {k: v for k, v in prefixes.items() if str(k).lstrip("-").isdigit() and int(k) in users}
        if cleaned != prefixes:
            self._db.set(MAIN_KEY, "command_prefixes", cleaned)

    def add_rule(self, target_type: str, target, rule: str, duration: int):
        if target_type not in ("chat", "user"):
            raise ValueError(f"Invalid target_type: {target_type}")
        if not any(rule.startswith(t) for t in ("command", "module", "inline")):
            raise ValueError(f"Invalid rule: {rule}")
        if duration < 0:
            raise ValueError(f"Invalid duration: {duration}")
        from . import utils

        store = self._tsec_chat if target_type == "chat" else self._tsec_user
        store.append(
            {
                "target": target.id,
                "rule_type": rule.split("/")[0],
                "rule": rule.split("/", 1)[1],
                "expires": int(time.time() + duration) if duration else 0,
                "entity_name": get_display_name(target),
                "entity_url": utils.get_entity_url(target),
            }
        )
        self._reload_rights(force=True)

    def remove_rules(self, target_type: str, target_id: int) -> bool:
        store = {"user": self._tsec_user, "chat": self._tsec_chat}.get(target_type)
        if store is None:
            return False
        removed = False
        for rule in list(store):
            if rule["target"] == target_id:
                store.remove(rule)
                removed = True
        if removed:
            self._reload_rights(force=True)
        return removed

    def remove_rule(self, target_type: str, target_id: int, rule_cont: str) -> bool:
        store = {"user": self._tsec_user, "chat": self._tsec_chat}.get(target_type)
        if store is None:
            return False
        removed = False
        for rule in list(store):
            if rule["target"] == target_id and rule["rule"] == rule_cont:
                store.remove(rule)
                removed = True
        if removed:
            self._reload_rights(force=True)
        return removed

    def get_flags(self, func) -> int:
        if isinstance(func, int):
            config = func
        else:
            target = getattr(func, "__func__", func)
            key = f"{getattr(target, '__module__', '')}.{getattr(target, '__name__', '')}"
            config = self._db.get(DB_KEY, "masks", {}).get(key, getattr(func, "security", self._default))
        if config & ~ALL and not config & EVERYONE:
            logger.error("Маска прав содержит неизвестные биты")
            return 0
        # как в Hikka: по умолчанию «рамка» прав — только владелец; расширяется командой безопасности
        return config & self._db.get(DB_KEY, "bounding_mask", DEFAULT_PERMISSIONS)

    def _check_tsec_inline(self, user_id: int, command: str) -> bool:
        return bool(command) and any(
            r["target"] == user_id and r["rule_type"] == "inline" and r["rule"] == command for r in self._tsec_user
        )

    def check_tsec(self, user_id: int, command: str) -> bool:
        for group in self._sgroups.values():
            if user_id in group.users:
                for perm in group.permissions:
                    if perm.get("rule_type") in ("command", "module") and perm.get("rule") == command:
                        return True
        loader = getattr(self._client, "loader", None)
        for rule in self._tsec_user:
            if rule["target"] != user_id:
                continue
            if rule["rule_type"] == "command" and rule["rule"] == command:
                return True
            if rule["rule_type"] == "module" and loader is not None and command in loader.commands:
                if rule["rule"] == loader.commands[command].__self__.__class__.__name__:
                    return True
        return False

    async def check(self, message, func, user_id: int = None, inline_cmd: str = None, *, usernames=None) -> bool:
        self._reload_rights()
        config = self.get_flags(func)
        if not config:
            return False

        if not user_id and message is not None:
            user_id = getattr(message, "sender_id", None) or getattr(getattr(message, "peer_id", None), "user_id", None)

        if user_id == self._client.tg_id or (message is not None and getattr(message, "out", False)):
            return True
        if user_id in self._owner:
            return True
        if user_id in self._db.get(MAIN_KEY, "blacklist_users", []):
            return False
        if message is None:
            return self._check_tsec_inline(user_id, inline_cmd) or bool(config & EVERYONE)

        try:
            chat = _chat_id(message)
        except Exception:
            chat = None
        try:
            cmd = (message.raw_text or "")[1:].split()[0].strip()
            for username in usernames or ():
                cmd = cmd.replace(f"@{username}", "")
        except Exception:
            cmd = None

        if callable(func):
            loader = getattr(self._client, "loader", None)
            command = (loader.find_alias(cmd, include_legacy=True) if loader and cmd else None) or cmd
            owner_cls = getattr(getattr(func, "__self__", None), "__class__", type(None)).__name__
            for group in self._sgroups.values():
                if user_id in group.users:
                    for perm in group.permissions:
                        if perm.get("rule_type") == "command" and perm.get("rule") == command:
                            return True
                        if perm.get("rule_type") == "module" and perm.get("rule") == owner_cls:
                            return True
            for rule in self._tsec_user:
                if rule["target"] == user_id and (
                    rule["rule_type"] == "command" and rule["rule"] == command
                    or rule["rule_type"] == "module" and rule["rule"] == owner_cls
                ):
                    return True
            if chat:
                for rule in self._tsec_chat:
                    if rule["target"] == chat and (
                        rule["rule_type"] == "command" and rule["rule"] == command
                        or rule["rule_type"] == "module" and rule["rule"] == owner_cls
                    ):
                        return True

        f_owner = config & GROUP_OWNER
        f_admin_any = config & GROUP_ADMIN_ANY
        if config & GROUP_MEMBER and getattr(message, "is_group", False):
            return True
        if config & PM and getattr(message, "is_private", False):
            return True

        if not (f_owner or f_admin_any):
            return False

        if getattr(message, "is_channel", False) and getattr(message, "is_group", False):
            key = f"{chat}/{user_id}"
            cached = self._cache.get(key)
            if cached and cached["exp"] >= time.time():
                participant = cached["user"]
            else:
                participant = await self._client.get_permissions(message.peer_id, user_id)
                self._cache[key] = {"user": participant, "exp": time.time() + 300}
            if participant.is_creator:
                return True
            if participant.is_admin and (
                self._any_admin and f_admin_any
                or config & GROUP_ADMIN
                or config & GROUP_ADMIN_ADD_ADMINS and participant.add_admins
                or config & GROUP_ADMIN_CHANGE_INFO and participant.change_info
                or config & GROUP_ADMIN_BAN_USERS and participant.ban_users
                or config & GROUP_ADMIN_DELETE_MESSAGES and participant.delete_messages
                or config & GROUP_ADMIN_PIN_MESSAGES and participant.pin_messages
                or config & GROUP_ADMIN_INVITE_USERS and participant.invite_users
            ):
                return True
            return False

        if getattr(message, "is_group", False):
            key = f"{chat}/{user_id}"
            cached = self._cache.get(key)
            if cached and cached["exp"] >= time.time():
                participant = cached["user"]
            else:
                full = await self._client(GetFullChatRequest(message.chat_id))
                participant = next(
                    (p for p in full.full_chat.participants.participants if p.user_id == user_id), None
                )
                self._cache[key] = {"user": participant, "exp": time.time() + 300}
            if isinstance(participant, ChatParticipantCreator):
                return True
            if isinstance(participant, ChatParticipantAdmin) and f_admin_any:
                return True
        return False

    _check = check
