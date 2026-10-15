"""Встроенный модуль пространства Hikka: настройка конфигов модулей (инлайн-меню и fconfig)."""

import ast
import logging
import typing

from telethon.tl.types import Message

from .. import loader, utils, validators
from ..inline.types import InlineCall

logger = logging.getLogger(__name__)


@loader.tds
class HikkaConfigMod(loader.Module):
    """Настройки модулей Hikka"""

    strings = {
        "name": "HikkaConfig",
        "choose": "⚙️ <b>Выберите модуль для настройки</b>",
        "choose_lib": "⚙️ <b>Выберите библиотеку для настройки</b>",
        "configuring_mod": "⚙️ <b>Настройки модуля</b> <code>{}</code>\n\n{}",
        "configuring_lib": "⚙️ <b>Настройки библиотеки</b> <code>{}</code>\n\n{}",
        "option": (
            "⚙️ <b>Модуль</b> <code>{}</code> → <b>опция</b> <code>{}</code>\n\n<i>ℹ️ {}</i>\n\n"
            "<b>По умолчанию:</b> {}\n<b>Сейчас:</b> {}{}"
        ),
        "typehint": "\n\n<b>Значение должно быть {}</b>",
        "set": "✅ <b>Опция</b> <code>{}</code> <b>модуля</b> <code>{}</code> <b>= </b>{}",
        "reset": "✅ <b>Опция</b> <code>{}</code> <b>сброшена по умолчанию</b>",
        "invalid": "🚫 <b>Неверное значение:</b> <i>{}</i>",
        "no_mod": "🚫 <b>Модуль не найден</b>",
        "no_option": "🚫 <b>У модуля нет такой опции</b>",
        "no_config": "ℹ️ <b>У модуля нет настроек</b>",
        "args": "🚫 <b>Формат:</b> <code>{}fconfig &lt;модуль&gt; &lt;опция&gt; &lt;значение&gt;</code>",
        "enter_value": "✍️ Ввести значение",
        "enter_value_hint": "Новое значение опции {}",
        "add_item": "➕ Добавить",
        "remove_item": "➖ Убрать",
        "add_item_hint": "Значение, которое добавить",
        "remove_item_hint": "Значение, которое убрать",
        "default": "♻️ По умолчанию",
        "back": "👈 Назад",
        "close": "🔻 Закрыть",
        "libraries": "📦 Библиотеки",
        "modules": "🧩 Модули",
        "true": "✅ Вкл",
        "false": "❌ Выкл",
        "hidden": "<i>•••• (скрыто)</i>",
        "empty": "<i>пусто</i>",
        "no_inline": "⚙️ <b>Настройки</b> <code>{}</code>\n\n{}\n\n<i>Изменить: {}fconfig {} &lt;опция&gt; &lt;значение&gt;</i>",
    }

    # ------------------------------------------------------------------ helpers
    def _owner(self, name: str, obj_type=False):
        if obj_type == "library":
            return next((lib for lib in self.allmodules.libraries if lib.name.lower() == name.lower()), None)
        mod = self.lookup(name)
        if mod:
            return mod
        return next((lib for lib in self.allmodules.libraries if lib.name.lower() == name.lower()), None)

    @staticmethod
    def prep_value(value):
        if isinstance(value, str):
            return f"<code>{utils.escape_html(value.strip())}</code>" if value.strip() else "<i>пусто</i>"
        if isinstance(value, list) and value:
            return "<code>[</code>" + ", ".join(f"<code>{utils.escape_html(str(v))}</code>" for v in value) + "<code>]</code>"
        return f"<code>{utils.escape_html(str(value))}</code>"

    def hide_value(self, value) -> str:
        if isinstance(value, list) and value:
            return self.prep_value(["*" * len(str(v)) for v in value])
        return self.prep_value("*" * len(str(value)))

    def _shown(self, owner, option, value=None) -> str:
        cv = owner.config._config[option]
        value = cv.value if value is None else value
        if isinstance(cv.validator, validators.Hidden):
            return self.strings("hidden") if value not in (None, "", []) else self.strings("empty")
        return self.prep_value(value)

    def _get_value(self, mod: str, option: str) -> str:
        owner = self._owner(mod)
        return self._shown(owner, option)

    def _lang(self):
        return (self._db.get("hikka.translations", "lang", "ru") or "ru").split()[0]

    def _vdoc(self, validator) -> str:
        if validator is None or not getattr(validator, "doc", None):
            return ""
        doc = validator.doc
        if isinstance(doc, dict):
            doc = doc.get(self._lang()) or doc.get("en") or ""
        return str(doc)

    def _save(self, owner, option):
        getter = owner.pointer if isinstance(owner, loader.Module) else owner._lib_pointer
        value = owner.config._config[option].value
        if utils.is_serializable(value):
            getter("__config__", {})[option] = value

    def _set(self, owner, option, value):
        owner.config[option] = value                 # валидация — ValidationError
        self._save(owner, option)

    # ------------------------------------------------------------------ команды
    @loader.command(alias="cfg", ru_doc="[модуль] — настройки модулей")
    async def configcmd(self, message: Message):
        """[module] - configure modules"""
        args = utils.get_args(message) or []
        if len(args) >= 3:
            await self._direct(message, args[0], args[1], " ".join(args[2:]))
            return
        if args:
            owner = self._owner(args[0])
            if not owner:
                await utils.answer(message, self.strings("no_mod"))
                return
            if not getattr(owner, "config", None):
                await utils.answer(message, self.strings("no_config"))
                return
            if not self.inline.init_complete:
                await utils.answer(message, self._plain(owner))
                return
            form = await self.inline.form("⚙️", message, reply_markup=[{"text": "…", "data": "empty"}], silent=True)
            if form:
                await self.inline__configure(form, owner.__class__.__name__ if isinstance(owner, loader.Module) else owner.name)
            return
        if not self.inline.init_complete:
            mods = [m.name for m in self.allmodules.modules if getattr(m, "config", None)]
            await utils.answer(message, self.strings("choose") + "\n\n" + ", ".join(f"<code>{utils.escape_html(n)}</code>" for n in mods))
            return
        await self.inline.form(self.strings("choose"), message, reply_markup=self._mods_markup())

    def _plain(self, owner) -> str:
        lines = "\n".join(f"▫️ <code>{utils.escape_html(k)}</code>: {self._shown(owner, k)}" for k in owner.config)
        name = getattr(owner, "name", owner.__class__.__name__)
        return self.strings("no_inline").format(utils.escape_html(name), lines, utils.escape_html(self.get_prefix()),
                                                utils.escape_html(name))

    @loader.command(ru_doc="<модуль> <опция> <значение> — задать опцию напрямую")
    async def fconfig(self, message: Message):
        """<module> <option> <value> - set option directly"""
        args = utils.get_args_raw(message).split(maxsplit=2) if utils.get_args_raw(message) else []
        if len(args) < 2:
            await utils.answer(message, self.strings("args").format(utils.escape_html(self.get_prefix())))
            return
        await self._direct(message, args[0], args[1], args[2] if len(args) > 2 else "")

    async def _direct(self, message, mod, option, value):
        owner = self._owner(mod)
        if not owner or not getattr(owner, "config", None):
            await utils.answer(message, self.strings("no_mod"))
            return
        if option not in owner.config._config:
            await utils.answer(message, self.strings("no_option"))
            return
        try:
            self._set(owner, option, value if value != "" else None)
        except validators.ValidationError as e:
            await utils.answer(message, self.strings("invalid").format(utils.escape_html(str(e))))
            return
        await utils.answer(
            message,
            self.strings("set").format(utils.escape_html(option), utils.escape_html(getattr(owner, "name", mod)),
                                       self._shown(owner, option)),
        )

    # ------------------------------------------------------------------ инлайн-меню
    def _mods_markup(self, obj_type=False):
        if obj_type == "library":
            items = [(lib.name, lib.name) for lib in self.allmodules.libraries if getattr(lib, "config", None)]
        else:
            items = sorted(
                [(m.__class__.__name__, m.name) for m in self.allmodules.modules if getattr(m, "config", None)],
                key=lambda x: str(x[1]).lower(),
            )
        buttons = [
            {"text": str(title), "callback": self.inline__configure, "args": (key,), "kwargs": {"obj_type": obj_type}}
            for key, title in items
        ]
        rows = list(utils.chunks(buttons, 3))
        switch = (
            {"text": self.strings("modules"), "callback": self.inline__global_config, "kwargs": {"obj_type": False}}
            if obj_type == "library"
            else {"text": self.strings("libraries"), "callback": self.inline__global_config, "kwargs": {"obj_type": "library"}}
        )
        extra = [switch] if self.allmodules.libraries or obj_type == "library" else []
        return rows + [extra + [{"text": self.strings("close"), "action": "close"}]]

    async def inline__global_config(self, call, obj_type=False, page: int = 0):
        await call.edit(self.strings("choose_lib" if obj_type == "library" else "choose"),
                        reply_markup=self._mods_markup(obj_type))

    async def inline__configure(self, call, mod: str, obj_type: typing.Union[bool, str] = False):
        owner = self._owner(mod, obj_type)
        if not owner:
            await call.answer("Модуль выгружен", show_alert=True)
            return
        buttons = [
            {"text": param, "callback": self.inline__configure_option, "args": (mod, param), "kwargs": {"obj_type": obj_type}}
            for param in owner.config
        ]
        lines = "\n".join(f"▫️ <code>{utils.escape_html(k)}</code>: {self._shown(owner, k)}" for k in owner.config)
        await call.edit(
            self.strings("configuring_lib" if obj_type == "library" else "configuring_mod").format(
                utils.escape_html(getattr(owner, "name", mod)), lines
            ),
            reply_markup=list(utils.chunks(buttons, 2))
            + [[
                {"text": self.strings("back"), "callback": self.inline__global_config, "kwargs": {"obj_type": obj_type}},
                {"text": self.strings("close"), "action": "close"},
            ]],
        )

    async def inline__configure_option(self, call, mod: str, option: str, obj_type=False):
        owner = self._owner(mod, obj_type)
        if not owner or option not in owner.config._config:
            await call.answer("Опция не найдена", show_alert=True)
            return
        cv = owner.config._config[option]
        validator = cv.validator
        inner = validator.validator if isinstance(validator, validators.Hidden) else validator
        doc = utils.escape_html(owner.config.getdoc(option) or "")
        default = self._shown(owner, option, cv.default) if cv.default not in (None, "") else self.strings("empty")
        hint = self.strings("typehint").format(utils.escape_html(self._vdoc(validator))) if self._vdoc(validator) else ""
        text = self.strings("option").format(
            utils.escape_html(getattr(owner, "name", mod)), utils.escape_html(option), doc, default,
            self._shown(owner, option), hint,
        )
        back = {"text": self.strings("back"), "callback": self.inline__configure, "args": (mod,), "kwargs": {"obj_type": obj_type}}
        reset = {"text": self.strings("default"), "callback": self._reset, "args": (mod, option, obj_type)}
        rows = []
        if isinstance(inner, validators.Boolean):
            rows.append([
                {"text": self.strings("true") + (" •" if cv.value else ""), "callback": self._set_cb, "args": (mod, option, True, obj_type)},
                {"text": self.strings("false") + (" •" if not cv.value else ""), "callback": self._set_cb, "args": (mod, option, False, obj_type)},
            ])
        elif isinstance(inner, validators.Choice):
            rows += list(utils.chunks([
                {"text": ("• " if cv.value == v else "") + str(v), "callback": self._set_cb, "args": (mod, option, v, obj_type)}
                for v in inner.possible_values
            ], 3))
        elif isinstance(inner, validators.MultiChoice):
            current = list(cv.value or [])
            rows += list(utils.chunks([
                {"text": ("✅ " if v in current else "▫️ ") + str(v), "callback": self._toggle_multi,
                 "args": (mod, option, v, obj_type)}
                for v in inner.possible_values
            ], 3))
        elif isinstance(inner, validators.Series):
            rows.append([
                {"text": self.strings("add_item"), "input": self.strings("add_item_hint"), "handler": self._series_add,
                 "args": (mod, option, obj_type)},
                {"text": self.strings("remove_item"), "input": self.strings("remove_item_hint"),
                 "handler": self._series_remove, "args": (mod, option, obj_type)},
            ])
            rows.append([{"text": self.strings("enter_value"), "input": self.strings("enter_value_hint").format(option),
                          "handler": self._input_set, "args": (mod, option, obj_type)}])
        else:
            rows.append([{"text": self.strings("enter_value"), "input": self.strings("enter_value_hint").format(option),
                          "handler": self._input_set, "args": (mod, option, obj_type)}])
        rows.append([reset])
        rows.append([back, {"text": self.strings("close"), "action": "close"}])
        await call.edit(text, reply_markup=rows)

    async def _apply(self, call, mod, option, value, obj_type):
        owner = self._owner(mod, obj_type)
        try:
            self._set(owner, option, value)
        except validators.ValidationError as e:
            await call.answer(f"Неверное значение: {e}"[:190], show_alert=True)
            return
        await self.inline__configure_option(call, mod, option, obj_type)

    async def _set_cb(self, call, mod, option, value, obj_type=False):
        await self._apply(call, mod, option, value, obj_type)

    async def _input_set(self, call, query: str, mod, option, obj_type=False):
        await self._apply(call, mod, option, query, obj_type)

    async def _reset(self, call, mod, option, obj_type=False):
        owner = self._owner(mod, obj_type)
        owner.config.set_no_raise(option, owner.config.getdef(option))
        self._save(owner, option)
        await self.inline__configure_option(call, mod, option, obj_type)

    async def _toggle_multi(self, call, mod, option, value, obj_type=False):
        owner = self._owner(mod, obj_type)
        current = list(owner.config[option] or [])
        if value in current:
            current.remove(value)
        else:
            current.append(value)
        await self._apply(call, mod, option, current, obj_type)

    def _parse_item(self, query):
        try:
            return ast.literal_eval(query)
        except Exception:
            return query

    async def _series_add(self, call, query: str, mod, option, obj_type=False):
        owner = self._owner(mod, obj_type)
        current = list(owner.config[option] or [])
        current.append(self._parse_item(query))
        await self._apply(call, mod, option, current, obj_type)

    async def _series_remove(self, call, query: str, mod, option, obj_type=False):
        owner = self._owner(mod, obj_type)
        current = [v for v in (owner.config[option] or []) if str(v) != str(query)]
        await self._apply(call, mod, option, current, obj_type)
