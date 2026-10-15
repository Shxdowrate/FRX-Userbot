"""Встроенный модуль пространства Hikka: список модулей и их команд."""

import difflib
import re
import inspect
import logging

from telethon.tl.types import Message

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class HelpMod(loader.Module):
    """Список модулей Hikka и справка по командам"""

    strings = {
        "name": "Help",
        "header": "🌙 <b>Модули Hikka в FRX</b> — {} шт. (префикс <code>{}</code>)\n",
        "core": "▫️",
        "plain": "▪️",
        "line": "\n{} <b>{}</b>: {}",
        "no_cmds": "<i>нет команд</i>",
        "footer": "\n\n<i>{}help &lt;модуль&gt; — подробнее</i>",
        "hidden": "\n\n<i>Скрыто модулей: {} ({}helphide)</i>",
        "module": "🌙 <b>{}</b>{}\n{}",
        "version": " <i>(v{})</i>",
        "doc": "<i>{}</i>\n",
        "developer": "\n🫶 <b>Разработчик:</b> {}",
        "cmd": "\n▫️ <code>{}{}</code> {}",
        "inline_cmd": "\n🎹 <code>@{} {}</code> {}",
        "aliases": " <i>(алиасы: {})</i>",
        "not_found": "🚫 <b>Модуль не найден</b>",
        "did_you_mean": "🚫 <b>Модуль не найден.</b> Возможно: {}",
        "hidden_ok": "👁 <b>Скрытые модули:</b> {}",
        "no_docs": "без описания",
        "core_note": " <i>(встроенный)</i>",
    }

    def _lang(self):
        return (self._db.get("hikka.translations", "lang", "ru") or "ru").split()[0]

    def _doc(self, func) -> str:
        lang = self._lang()
        doc = getattr(func, f"{lang}_doc", None)
        if not doc:
            owner = getattr(func, "__self__", None)
            name = getattr(func, "__name__", "")
            key = f"_cmd_doc_{name[:-3] if name.endswith('cmd') else name}".lower()
            for attr in (f"strings_{lang}",):
                strings = getattr(owner, attr, None)
                if isinstance(strings, dict) and strings.get(key):
                    doc = strings[key]
        doc = " ".join(str(doc or inspect.getdoc(func) or "").split())
        return re.sub(r"^[-—–]\s*", "", doc)

    @loader.command(alias="h", ru_doc="[модуль] — список модулей или справка по модулю")
    async def help(self, message: Message):
        """[module] - list modules or show module help"""
        args = (utils.get_args_raw(message) or "").strip()
        prefix = self.get_prefix()
        if args:
            await self._module_help(message, args, prefix)
            return
        hidden = self.get("hide", [])
        mods = sorted(self.allmodules.modules, key=lambda m: (not str(m.__origin__).startswith("<core"), str(m.name).lower()))
        text = self.strings("header").format(len(mods), utils.escape_html(prefix))
        shown_hidden = 0
        for mod in mods:
            if mod.__class__.__name__ in hidden or mod.name in hidden:
                shown_hidden += 1
                continue
            cmds = list(mod.commands)
            inline = [f"🎹 {n}" for n in mod.inline_handlers]
            items = " | ".join(cmds + inline) or self.strings("no_cmds")
            icon = self.strings("core") if str(mod.__origin__).startswith("<core") else self.strings("plain")
            text += self.strings("line").format(icon, utils.escape_html(mod.name), items)
        text += self.strings("footer").format(utils.escape_html(prefix))
        if shown_hidden:
            text += self.strings("hidden").format(shown_hidden, utils.escape_html(prefix))
        await utils.answer(message, text)

    async def _module_help(self, message, query: str, prefix: str):
        mod = self.allmodules.lookup(query)
        if not mod:
            command = self.allmodules.commands.get(query.lower().lstrip(prefix))
            if command is not None:
                mod = command.__self__
        if not mod:
            names = [m.name for m in self.allmodules.modules] + [m.__class__.__name__ for m in self.allmodules.modules]
            close = difflib.get_close_matches(query, names, n=3, cutoff=0.5)
            await utils.answer(
                message,
                self.strings("did_you_mean").format(", ".join(f"<code>{utils.escape_html(c)}</code>" for c in close))
                if close
                else self.strings("not_found"),
            )
            return
        version = getattr(mod, "__version__", None)
        version = self.strings("version").format(".".join(map(str, version))) if isinstance(version, tuple) else ""
        if str(mod.__origin__).startswith("<core"):
            version += self.strings("core_note")
        doc = (mod.strings["_cls_doc"] if "_cls_doc" in mod.strings else None) or inspect.getdoc(mod) or ""
        if str(doc).startswith("Unknown strings"):
            doc = inspect.getdoc(mod) or ""
        body = self.strings("doc").format(utils.escape_html(doc)) if doc else ""
        for name, func in mod.commands.items():
            aliases = list(getattr(func, "aliases", None) or []) + ([func.alias] if getattr(func, "alias", None) else [])
            aliases += [a for a, c in self.allmodules.aliases.items() if c.split()[0] == name]
            doc = self._doc(func) or self.strings("no_docs")
            if not doc.startswith(("<", "[")):
                doc = "— " + doc
            line = utils.escape_html(doc)
            if aliases:
                line += self.strings("aliases").format(", ".join(utils.escape_html(a) for a in dict.fromkeys(aliases)))
            body += self.strings("cmd").format(utils.escape_html(prefix), name, line)
        for name, func in mod.inline_handlers.items():
            body += self.strings("inline_cmd").format(
                self.inline.bot_username or "bot", name, utils.escape_html(self._doc(func) or self.strings("no_docs"))
            )
        for line in (getattr(mod, "__source__", "") or "").splitlines():
            if line.strip().startswith(("# meta developer:", "#meta developer:")):
                body += self.strings("developer").format(utils.escape_html(line.split(":", 1)[1].strip()))
                break
        await utils.answer(message, self.strings("module").format(utils.escape_html(mod.name), version, body))

    @loader.command(ru_doc="<модуль ...> — скрыть / показать модули в списке")
    async def helphide(self, message: Message):
        """<module ...> - hide / show modules in help"""
        hidden = self.get("hide", [])
        for name in (utils.get_args(message) or []):
            mod = self.allmodules.lookup(name)
            key = mod.__class__.__name__ if mod else name
            if key in hidden:
                hidden.remove(key)
            else:
                hidden.append(key)
        self.set("hide", hidden)
        await utils.answer(message, self.strings("hidden_ok").format(", ".join(f"<code>{h}</code>" for h in hidden) or "—"))
