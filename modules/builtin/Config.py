"""
modules/builtin/Config.py — просмотр и редактирование настроек модулей через инлайн-формы (.config / .cfg).

Типы настроек — см. core/engine.py create_config()/edit_config(): str, int, float, bool, list, а также
'choice' (выбор одного варианта из статичного списка) и 'choices' (выбор нескольких). Для 'choice' и 'choices'
в инлайне рисуется список вариантов вместо поля ввода; выбор нельзя оставить пустым. Значения и ошибки
экранируются; «сбросить» возвращает значение по умолчанию.
"""

import html

import cache
import db
import decorators
import engine
import visual
from core.inline_forms import InlineClick

NAME = "Config"
MODULE_NAME = "config"
VERSION = "2.11"
AUTHOR = "FRX"
DESCRIPTION = "Интерактивный просмотр и редактирование конфигов модулей через Inline-формы"

# Константы для типов конфигов
TYPE_STR = "str"
TYPE_INT = "int"
TYPE_FLOAT = "float"
TYPE_BOOL = "bool"
TYPE_LIST = "list"
TYPE_CHOICE = "choice"      # выбор ОДНОГО значения из options
TYPE_CHOICES = "choices"    # выбор НЕСКОЛЬКИХ значений из options

# тонкие символы вместо пёстрых эмодзи
TYPE_ICONS = {
    "str": "▤",
    "int": "#",
    "float": "#",
    "bool": "◉",
    "list": "☰",
    "choice": "◇",
    "choices": "◈",
}


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _kv(label: str, fragment: str) -> str:
    """Как visual.field, но значение — уже готовый HTML-фрагмент (format_value)."""
    return f"{visual.visual('element')} {label}  {fragment}"


@decorators.on_start()
async def create_config():
    await engine.create_config(
        key="test_config_str",
        description="Тестовый конфиг строки",
        data_type="str",
        default="Тест!",
    )
    await engine.create_config(
        key="test_config_int",
        description="Тестовый конфиг целочисленное",
        data_type="int",
        default=12345,
    )
    await engine.create_config(
        key="test_config_bool",
        description="Тестовый конфиг бул",
        data_type="bool",
        default=True,
    )
    await engine.create_config(
        key="test_config_list_v2",
        description="Тестовый конфиг список",
        data_type="list",
        default=["элемент 1", "элемент 2", "12345"],
    )
    await engine.create_config(
        key="test_config_choice",
        description="Тестовый конфиг — выбор одного варианта",
        data_type="choice",
        default="вариант A",
        options=["вариант A", "вариант B", "вариант C"],
    )
    await engine.create_config(
        key="test_config_choices",
        description="Тестовый конфиг — выбор нескольких вариантов",
        data_type="choices",
        default=["red"],
        options=["red", "green", "blue", "yellow"],
    )


def format_value(value, config_type: str) -> str:
    """Готовый HTML-фрагмент значения (данные экранированы)."""
    unset = "<i>не задано</i>"
    if value is None:
        return unset

    if config_type in (TYPE_LIST, TYPE_CHOICES):
        if not value:
            return "<code>[]</code>"
        return f"<code>[{_esc(', '.join(str(v) for v in value))}]</code>"
    elif config_type == TYPE_CHOICE:
        return f"<code>{_esc(value)}</code>" if value else unset
    elif config_type == TYPE_BOOL:
        return f"<b>{visual.visual('on')} да</b>" if value else f"<b>{visual.visual('off')} нет</b>"
    elif config_type == TYPE_STR:
        if not value:
            return "<code>''</code>"
        return f"<code>{_esc(value)}</code>"
    else:
        return f"<code>{_esc(value)}</code>"


def parse_and_validate(value_str: str, config_type: str):
    if config_type == TYPE_STR:
        return value_str

    if config_type == TYPE_INT:
        try:
            return int(value_str)
        except ValueError:
            raise ValueError(f"значение «{value_str}» должно быть целым числом")

    if config_type == TYPE_FLOAT:
        try:
            return float(value_str)
        except ValueError:
            raise ValueError(f"значение «{value_str}» должно быть числом (float)")

    if config_type == TYPE_BOOL:
        val_lower = value_str.lower()
        if val_lower in ("true", "да", "yes", "1", "on"):
            return True
        if val_lower in ("false", "нет", "no", "0", "off"):
            return False
        raise ValueError("допустимы значения: true/false, да/нет, 1/0")

    return value_str


def _error(title: str, e) -> str:
    return visual.err(title) + f"\n<code>{_esc(e)}</code>"


@decorators.command("config", "cfg")
async def config_cmd(event):
    args = engine.get_args().split()

    target_module = None
    if args:
        query = args[0]
        target_module = engine.resolve_module(query)
        if not target_module:
            await engine.answer(
                event,
                visual.err("Модуль не найден") + "\n" + visual.field("запрос", _esc(query)),
            )
            return

    async def get_modules_with_configs() -> list:
        modules = engine.get_module_list()
        modules_with_configs = []
        for mod in modules:
            config_key = f"{mod}:__config__"
            configs = await db.gget(config_key, {})
            if configs:
                module_info = engine.get_module_info(mod)
                display_name = module_info.get("name", mod)
                grp = "builtin" if module_info.get("is_builtin") else "loaded"
                modules_with_configs.append((mod, display_name, len(configs), grp))
        modules_with_configs.sort(key=lambda x: x[0])
        return modules_with_configs

    GROUP_TITLES = {"builtin": "Встроенные", "loaded": "Загруженные"}

    async def build_group_menu(grp: str, with_back: bool = True) -> tuple[str, list]:
        mods = [m for m in await get_modules_with_configs() if m[3] == grp]
        lines = [visual.title("Конфиги", sub=GROUP_TITLES[grp].lower()), ""]
        buttons = []
        for mod, display_name, count, _g in mods:
            lines.append(visual.item(f"<b>{_esc(mod)}</b> — {_esc(display_name)} · <i>конфигов: {count}</i>"))
            buttons.append([(visual.btn(f"{display_name} · {mod}"), f"open_mod:{mod}")])
        lines += ["", visual.hint("выберите модуль")]
        if with_back:
            buttons.append([(visual.btn("Назад", "back"), "back_to_main")])
        return "\n".join(lines), buttons

    async def build_main_menu() -> tuple[str, list]:
        modules_info = await get_modules_with_configs()
        if not modules_info:
            text = (
                visual.title("Конфиги") + "\n"
                + visual.hint("нет модулей с зарегистрированными конфигами")
            )
            return text, []

        counts = {g: sum(1 for m in modules_info if m[3] == g) for g in GROUP_TITLES}
        present = [g for g in GROUP_TITLES if counts[g]]
        if len(present) == 1:
            return await build_group_menu(present[0], with_back=False)
        lines = [visual.title("Конфиги"), ""]
        lines += [visual.field(GROUP_TITLES[g].lower(), counts[g]) for g in present]
        lines += ["", visual.hint("выберите, чьи настройки открыть")]
        buttons = [[(visual.btn(f"{GROUP_TITLES[g]} · {counts[g]}"), f"open_grp:{g}")] for g in present]
        return "\n".join(lines), buttons

    async def build_module_configs_menu(module_name: str) -> tuple[str, list]:
        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        module_info = engine.get_module_info(module_name)
        display_name = module_info.get("name", module_name)
        grp = "builtin" if module_info.get("is_builtin") else "loaded"

        head = visual.title(f"Конфиги · {_esc(display_name)}", sub=_esc(module_name))
        if not configs:
            text = head + "\n" + visual.hint("у этого модуля нет доступных конфигов")
            buttons = [[(visual.btn("К модулям", "back"), f"open_grp:{grp}")]]
            return text, buttons

        lines = [head, ""]
        buttons = []
        for key, data in sorted(configs.items()):
            value = data.get("value")
            config_type = data.get("type", TYPE_STR)
            disp_val = format_value(value, config_type)

            lines.append(visual.item(f"<b>{_esc(key)}</b>  {disp_val}"))
            buttons.append([(visual.btn(key), f"open_cfg:{module_name}:{key}")])

        lines += ["", visual.hint("выберите конфиг")]
        buttons.append([(visual.btn("К модулям", "back"), f"open_grp:{grp}")])
        return "\n".join(lines), buttons

    async def build_config_details_menu(
        module_name: str, key: str, status_msg: str = ""
    ) -> tuple[str, list]:
        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        config_data = configs.get(key, {})

        if not config_data:
            text = visual.err("Конфиг не найден") + "\n" + visual.field("ключ", _esc(key))
            buttons = [[(visual.btn("К модулю", "back"), f"open_mod:{module_name}")]]
            return text, buttons

        value = config_data.get("value")
        default_val = config_data.get("default")
        config_type = config_data.get("type", TYPE_STR)
        description = config_data.get("description", "")
        valid = config_data.get("valid")

        icon = TYPE_ICONS.get(config_type, "▤")
        disp_val = format_value(value, config_type)
        disp_def = format_value(default_val, config_type)

        lines = [
            visual.title("Конфиг", sub=_esc(key)),
            visual.field("модуль", _esc(module_name)),
            visual.field("тип", f"{icon} {_esc(config_type)}"),
            _kv("значение", disp_val),
            _kv("по умолчанию", disp_def),
        ]
        if valid:
            limits = ", ".join(f"{k}={v}" for k, v in valid.items())
            lines.append(visual.field("ограничения", _esc(limits)))
        if config_type in (TYPE_CHOICE, TYPE_CHOICES):
            lines.append(visual.field("варианты", _esc(", ".join(str(o) for o in (config_data.get("options") or [])))))
        if description:
            lines += ["", visual.hint(_esc(description))]

        if status_msg:
            lines += ["", status_msg]

        text = "\n".join(lines)

        buttons = []

        if config_type == TYPE_BOOL:
            toggle_text = visual.btn("Выключить", "off") if value else visual.btn("Включить", "on")
            buttons.append([(toggle_text, f"cfg_toggle_bool:{module_name}:{key}")])
        elif config_type in (TYPE_CHOICE, TYPE_CHOICES):
            # инлайн-список вариантов вместо поля ввода. Снять выбор целиком
            # нельзя — для choice клик просто переключает на другой вариант, для choices
            # клик по уже выбранному варианту снимает его (кроме случая, когда он последний
            # выбранный — engine.edit_config() сам откажет с понятной ошибкой, см. ниже).
            options = config_data.get("options") or []
            selected = value if config_type == TYPE_CHOICE else (value or [])
            for idx, opt in enumerate(options):
                is_sel = (opt == selected) if config_type == TYPE_CHOICE else (opt in selected)
                buttons.append([(visual.btn(opt, "on" if is_sel else "off"), f"cfg_choice_pick:{module_name}:{key}:{idx}")])
        elif config_type == TYPE_LIST:
            await cache.set(
                f"cfg_ctx:{event.chat_id}",
                {"mod": module_name, "key": key, "action": "add"},
            )
            buttons.append(
                [
                    engine.InputButton(
                        visual.btn("Добавить значение"),
                        "cfg_input_action",
                        hint="",
                    )
                ]
            )
            if value and len(value) > 0:
                buttons.append(
                    [(visual.btn("Удалить значение"), f"cfg_show_del_list:{module_name}:{key}")]
                )
        else:
            await cache.set(
                f"cfg_ctx:{event.chat_id}",
                {"mod": module_name, "key": key, "action": "edit"},
            )
            buttons.append(
                [
                    engine.InputButton(
                        visual.btn("Изменить значение"),
                        "cfg_input_action",
                        hint="",
                    )
                ]
            )

        # Добавляем кнопку сброса на дефолт, если дефолтное значение задано
        if default_val is not None:
            buttons.append([(visual.btn("Сбросить"), f"cfg_reset_ask:{module_name}:{key}")])

        buttons.append([(visual.btn("К конфигам модуля", "back"), f"open_mod:{module_name}")])
        return text, buttons

    async def build_reset_confirm_menu(module_name: str, key: str) -> tuple[str, list]:
        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        config_data = configs.get(key, {})

        if not config_data:
            text = visual.err("Конфиг не найден") + "\n" + visual.field("ключ", _esc(key))
            buttons = [[(visual.btn("К модулю", "back"), f"open_mod:{module_name}")]]
            return text, buttons

        value = config_data.get("value")
        default_val = config_data.get("default")
        config_type = config_data.get("type", TYPE_STR)

        text = "\n".join([
            visual.title("Сброс конфига", sub=_esc(key)),
            visual.field("модуль", _esc(module_name)),
            _kv("сейчас", format_value(value, config_type)),
            _kv("станет", format_value(default_val, config_type)),
            "",
            visual.warn("Вернуть значение по умолчанию?"),
        ])

        buttons = [
            [(visual.btn("Да, сбросить"), f"cfg_reset_confirm:{module_name}:{key}")],
            [(visual.btn("Отмена", "close"), f"open_cfg:{module_name}:{key}")],
        ]
        return text, buttons

    # ---- Инициализация формы ----

    if target_module:
        init_text, init_buttons = await build_module_configs_menu(target_module)
    else:
        init_text, init_buttons = await build_main_menu()

    form = await engine.create_inlineform(
        chat_id=event.chat_id,
        text=init_text,
        buttons=init_buttons,
        for_user=None,
        black_list=None,
        timeout=None,
    )

    # ---- Обработчики событий ----

    @form.on_click(data="back_to_main")
    async def back_to_main(click: InlineClick):
        text, buttons = await build_main_menu()
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="open_grp:")
    async def open_group_menu(click: InlineClick):
        grp = click.data.split(":", 1)[1]
        if grp not in GROUP_TITLES:
            grp = "loaded"
        # если непустая группа одна — «назад» к выбору группы не нужен
        both = len({m[3] for m in await get_modules_with_configs()}) > 1
        text, buttons = await build_group_menu(grp, with_back=both)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="open_mod:")
    async def open_module_menu(click: InlineClick):
        module_name = click.data.split(":", 1)[1]
        text, buttons = await build_module_configs_menu(module_name)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="open_cfg:")
    async def open_config_details(click: InlineClick):
        _, module_name, key = click.data.split(":", 2)
        text, buttons = await build_config_details_menu(module_name, key)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_toggle_bool:")
    async def toggle_bool_config(click: InlineClick):
        _, module_name, key = click.data.split(":", 2)
        current_val = await engine.get_config(key, module_name=module_name)
        new_val = not current_val

        try:
            await engine.edit_config(key, "edit", new_val, module_name=module_name)
            msg = visual.ok("Значение обновлено") + "  " + format_value(new_val, TYPE_BOOL)
        except Exception as e:
            msg = _error("Ошибка", e)

        text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_choice_pick:")
    async def choice_pick_config(click: InlineClick):
        # Кнопки несут индекс варианта (а не сам текст — в нём могут
        # быть двоеточия, которые сломали бы разбор callback_data), но в
        # engine.edit_config() передаём уже настоящее значение варианта — так
        # хочет API (см. create_config()/edit_config(), core/engine.py).
        _, module_name, key, idx_str = click.data.split(":", 3)

        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        config_data = configs.get(key, {})
        options = config_data.get("options") or []

        try:
            opt = options[int(idx_str)]
        except (ValueError, IndexError):
            msg = visual.err("Вариант не найден") + "\n" + visual.hint("список вариантов могли изменить")
            text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
            await form.edit(text=text, buttons=buttons)
            return

        config_type = config_data.get("type")
        try:
            if config_type == TYPE_CHOICE:
                await engine.edit_config(key, "edit", opt, module_name=module_name)
                msg = visual.ok("Выбрано") + f"  <code>{_esc(opt)}</code>"
            else:
                current = config_data.get("value") or []
                if opt in current:
                    await engine.edit_config(key, "del", opt, module_name=module_name)
                    msg = visual.ok("Снято") + f"  <code>{_esc(opt)}</code>"
                else:
                    await engine.edit_config(key, "add", opt, module_name=module_name)
                    msg = visual.ok("Выбрано") + f"  <code>{_esc(opt)}</code>"
        except Exception as e:
            msg = _error("Ошибка", e)

        text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_reset_ask:")
    async def ask_reset_config_default(click: InlineClick):
        _, module_name, key = click.data.split(":", 2)
        text, buttons = await build_reset_confirm_menu(module_name, key)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_reset_confirm:")
    async def reset_config_default(click: InlineClick):
        _, module_name, key = click.data.split(":", 2)

        try:
            # 'to_default' работает для ЛЮБОГО типа конфига,
            # включая list (раньше сброс списка тут ломался — 'edit' для
            # list-конфига в engine.edit_config() всегда кидал ValueError).
            await engine.edit_config(key, "to_default", module_name=module_name)
            msg = visual.ok("Значение сброшено на значение по умолчанию")
        except Exception as e:
            msg = _error("Ошибка при сбросе", e)

        text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
        await form.edit(text=text, buttons=buttons)

    @form.on_input(data="cfg_input_action")
    async def handle_input_action(inp):
        ctx = await cache.get(f"cfg_ctx:{event.chat_id}")
        if not ctx:
            return

        module_name = ctx["mod"]
        key = ctx["key"]
        action = ctx["action"]
        raw_text = str(
            getattr(inp, "value", None) or getattr(inp, "text", str(inp))
        ).strip()

        try:
            if action == "edit":
                config_key = f"{module_name}:__config__"
                configs = await db.gget(config_key, {})
                config_data = configs.get(key, {})
                config_type = config_data.get("type", TYPE_STR)

                parsed_val = parse_and_validate(raw_text, config_type)
                await engine.edit_config(key, "edit", parsed_val, module_name=module_name)
                msg = visual.ok("Обновлено") + f"  <code>{_esc(parsed_val)}</code>"

            elif action == "add":
                await engine.edit_config(key, "add", raw_text, module_name=module_name)
                msg = visual.ok("Добавлено") + f"  <code>{_esc(raw_text)}</code>"

        except Exception as e:
            msg = _error("Ошибка", e)

        text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_show_del_list:")
    async def show_del_list_menu(click: InlineClick):
        _, module_name, key = click.data.split(":", 2)
        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        config_data = configs.get(key, {})
        items = config_data.get("value", [])

        text = (
            visual.title("Удаление из списка", sub=_esc(key)) + "\n"
            + visual.hint("нажмите на элемент, чтобы удалить его из списка")
        )

        buttons = []
        for idx, item in enumerate(items):
            buttons.append([(visual.btn(str(item), "close"), f"cfg_del_item:{module_name}:{key}:{idx}")])

        buttons.append([(visual.btn("К конфигу", "back"), f"open_cfg:{module_name}:{key}")])
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="cfg_del_item:")
    async def delete_list_item(click: InlineClick):
        _, module_name, key, idx_str = click.data.split(":", 3)
        idx = int(idx_str)

        config_key = f"{module_name}:__config__"
        configs = await db.gget(config_key, {})
        config_data = configs.get(key, {})
        items = config_data.get("value", [])

        if 0 <= idx < len(items):
            item_to_del = items[idx]
            try:
                await engine.edit_config(key, "del", item_to_del, module_name=module_name)
                msg = visual.ok("Элемент удалён") + f"  <code>{_esc(item_to_del)}</code>"
            except Exception as e:
                msg = _error("Ошибка при удалении", e)
        else:
            msg = visual.err("Элемент не найден или уже удалён")

        text, buttons = await build_config_details_menu(module_name, key, status_msg=msg)
        await form.edit(text=text, buttons=buttons)


@decorators.command("confighelp", "cfghelp")
async def config_help_cmd(event):
    help_text = "\n".join([
        visual.title("Конфиги", sub="интерактивное управление"),
        "",
        visual.section("Команды"),
        visual.item("<code>.config</code> — инлайн-меню со всеми модулями"),
        visual.item("<code>.config &lt;модуль&gt;</code> — сразу меню конкретного модуля"),
        "",
        visual.hint("сокращение — .cfg"),
    ])
    await engine.answer(event, help_text)