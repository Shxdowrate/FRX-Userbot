"""
modules/builtin/Helper.py — помощь по модулям, проверка файлов модулей и карточки модулей.

Команды:
    .modules / .mods       список модулей текстом — «Встроенные» и «Загруженные» — и кнопка «Выбрать модуль»:
                           7 модулей на странице (встроенные ▣, загруженные ▤, навигация — engine.picker_page()).
    .help / .h [модуль]    то же; с именем модуля — сразу его карточка.
    .checkmodule / .checkmod, .checkall — проверка файла модуля и всех модулей на типовые ошибки.

Карточка модуля: описание, команды; кнопки «▸ Отправить модуль» (как `.sendmodule`, modules/builtin/Loader.py),
«Открыть конфиг» — только если у модуля есть настройки, открывает `.config <модуль>` через engine.exec_modcmd(),
«Удалить модуль» — с подтверждением, удаляет файл совсем (мягкая выгрузка — .unloadmod, возврат — .restoremods);
для встроенных модулей удаление недоступно. «Назад» возвращает на ту страницу списка, где стоит модуль.

Без стационарного бота команды отвечают обычным текстом без кнопок. Описания с «<не_тегами>» экранируются
(core/html_utils.py), поэтому не ломают отправку.
"""

import ast
import html
import os

import engine
import decorators
import visual
import db

NAME = "Helper"
MODULE_NAME = "helper"
VERSION = "2.4"
AUTHOR = "FRX"
DESCRIPTION = "Информация о модулях (инлайн-меню), проверка файлов и помощь"


def format_commands_for_list(commands, max_length=40):
    """
    Форматирует команды для вывода в списке модулей.
    Берет первую команду, остальные - самые короткие, чтобы вписаться в лимит.
    """
    if not commands:
        return []

    first_cmd = commands[0]['command']

    if len(commands) == 1:
        return [first_cmd]

    all_commands = [cmd['command'] for cmd in commands]
    other_commands = [cmd for cmd in all_commands if cmd != first_cmd]
    other_commands.sort(key=len)

    result = [first_cmd]
    current_length = len(first_cmd)

    for cmd in other_commands:
        if current_length == 0:
            new_length = len(cmd)
        else:
            new_length = current_length + len(cmd) + 2

        if new_length <= max_length:
            result.append(cmd)
            current_length = new_length
        else:
            remaining = len(all_commands) - len(result)
            if remaining > 0:
                result.append(f"+{remaining}")
            break

    return result


# ------------------------------------------------------------------ #
# Тексты / клавиатуры (общие для .modules и .help)
# ------------------------------------------------------------------ #

# ------------------------------------------------------------------ #
# список модулей — встроенные и загруженные раздельно,
# выбор модуля — отдельным экраном по 7 кнопок с навигацией
# ------------------------------------------------------------------ #

PAGE_SIZE = 7            # модулей на одной странице выбора
LIST_TEXT_LIMIT = 3800   # запас до лимита Telegram (4096) на сообщение


def _module_groups():
    """([встроенные], [загруженные]) — MODULE_NAME, каждая группа по алфавиту."""
    builtin, loaded = [], []
    for module_name in sorted(engine.get_module_list(), key=lambda x: x.lower()):
        info = engine.get_module_info(module_name)
        if not info:
            continue
        (builtin if info.get('is_builtin') else loaded).append(module_name)
    return builtin, loaded


def _ordered_modules():
    """Сперва встроенные, потом загруженные."""
    builtin, loaded = _module_groups()
    return builtin + loaded


def _module_line(module_name):
    info = engine.get_module_info(module_name)
    display_name = info.get('name', module_name)
    commands = info.get('commands', [])
    if commands:
        cmd_list = format_commands_for_list(commands, 40)
        cmd_str = ', '.join([f'<code>.{cmd}</code>' for cmd in cmd_list])
    else:
        cmd_str = "<i>нет команд</i>"
    return visual.item(f"<b>{display_name}</b>  <code>{module_name}</code> · {cmd_str}")


def _modules_list_text():
    """Текст главного экрана (+ упорядоченный список MODULE_NAME): раздел «Встроенные», затем «Загруженные»."""
    builtin, loaded = _module_groups()
    total = len(builtin) + len(loaded)
    if not total:
        return visual.warn('Нет загруженных модулей'), []

    lines = [visual.title('Модули', f"всего: {total}")]
    used = len(lines[0])
    hidden = 0
    for title, names, empty in (
        (visual.section(f"Встроенные · {len(builtin)}"), builtin, visual.hint('нет встроенных модулей')),
        (visual.section(f"Загруженные · {len(loaded)}"), loaded, visual.hint('нет загруженных модулей — <code>.loadmod</code>')),
    ):
        lines.append("")
        lines.append(title)
        used += len(title) + 2
        if not names:
            lines.append(empty)
            used += len(empty) + 1
        for module_name in names:
            line = _module_line(module_name)
            if hidden or used + len(line) + 1 > LIST_TEXT_LIMIT:
                hidden += 1          # не влезло в одно сообщение — остальное доступно через «Выбрать модуль»
                continue
            lines.append(line)
            used += len(line) + 1
    if hidden:
        lines += ["", visual.hint(f"… и ещё {hidden} — см. «Выбрать модуль»")]
    return "\n".join(lines), builtin + loaded


def _menu_buttons():
    """Главный экран: ОДНА кнопка."""
    return [[(visual.btn('Выбрать модуль'), "help_pick:0")]]


def _picker(page):
    """Экран выбора: до 7 модулей (встроенные, затем загруженные) + навигация снизу. -> (text, buttons, page)."""
    items = []
    for module_name in _ordered_modules():
        info = engine.get_module_info(module_name) or {}
        icon = visual.visual('module') if info.get('is_builtin') else visual.visual('file')
        items.append((f"{icon} {info.get('name', module_name)}", f"help_open:{module_name}"))
    rows, page, total = engine.picker_page(items, page, per_page=PAGE_SIZE, page_callback="help_page:")
    rows.append([(visual.btn('К списку модулей', 'back'), "help_menu")])
    text = (
        visual.title('Модули', f"выберите модуль · страница {page + 1}/{total}") + "\n"
        + visual.hint(f"{visual.visual('module')} встроенные, затем {visual.visual('file')} загруженные")
    )
    return text, rows, page


def _module_page(module_name):
    """Номер страницы выбора, на которой находится модуль."""
    ordered = _ordered_modules()
    return ordered.index(module_name) // PAGE_SIZE if module_name in ordered else 0


def _module_detail_text(info, module_name):
    """Подробная карточка модуля — используется и .help <module>, и инлайн-меню."""
    display_name = info.get('name', module_name)
    version = info.get('version', 'Не указана')
    author = info.get('author', 'Не указан')
    description = info.get('description', 'Нет описания')
    commands = info.get('commands', [])
    kind = "встроенный" if info.get('is_builtin') else "загруженный"

    lines = [
        visual.title(display_name, description),
        visual.field('id', module_name),
        visual.field('версия', version),
        visual.field('автор', author, code=False),
        visual.field('тип', kind, code=False),
        "",
    ]

    if commands:
        lines.append(visual.section(f"Команды · {len(commands)}"))
        for cmd_info in commands:
            cmd = cmd_info['command']
            aliases = cmd_info.get('aliases', [])
            cmd_description = cmd_info.get('description', 'Нет описания')
            cmd_str = f"<code>.{cmd}</code>"
            if aliases:
                cmd_str += " " + visual.hint(', '.join(f".{a}" for a in aliases))
            lines.append(visual.item(f"{cmd_str} — {cmd_description}"))
    else:
        lines.append(visual.hint('нет зарегистрированных команд'))

    stats = []
    if info.get('watchers'):
        stats.append(visual.field('вотчеров', len(info['watchers'])))
    if info.get('cycles'):
        stats.append(visual.field('циклов', len(info['cycles'])))
    if info.get('callbacks'):
        stats.append(visual.field('колбэков', len(info['callbacks'])))
    if info.get('on_error'):
        stats.append(visual.item('обработка ошибок'))
    if info.get('on_start'):
        stats.append(visual.field('on_start', len(info['on_start'])))
    if info.get('on_load'):
        stats.append(visual.field('on_load', len(info['on_load'])))

    if stats:
        lines += ["", visual.section('Статистика')] + stats

    return "\n".join(lines) + "\n"


def _config_command_available():
    """Есть ли в системе команда .config (модуль Config), которую можно запустить кнопкой."""
    try:
        info = engine.get_module_info('config')
    except KeyError:
        return False
    return any(c['command'] == 'config' or 'config' in c.get('aliases', []) for c in info.get('commands', []))


async def _has_config(module_name):
    """Есть ли у модуля хотя бы один зарегистрированный конфиг (и есть ли чем его открыть)."""
    configs = await db.gget(f"{module_name}:__config__", {})
    return bool(configs) and _config_command_available()


async def _module_detail_buttons(module_name, show_back=True):
    module_info = engine.modules.get(module_name)
    is_builtin = module_info is not None and module_info.is_builtin

    rows = []
    if show_back:
        rows.append([(visual.btn('К выбору модуля', 'back'), "help_back")])
    if await _has_config(module_name):       # кнопка только если конфиг действительно есть
        rows.append([(visual.btn('Открыть конфиг'), f"help_cfg:{module_name}")])
    rows.append([(visual.btn('Отправить модуль'), f"help_send:{module_name}")])      # тот же .sendmodule
    if not is_builtin:
        rows.append([(visual.btn('Удалить модуль', 'close'), f"help_del_ask:{module_name}")])
    return rows


def _bind_helper_form(form, event, focus=None):
    """
    Общие обработчики клавиатуры .modules/.help — вешаются один раз на форму.
    event — исходное событие команды (нужно кнопке «Открыть конфиг»: engine.exec_modcmd()).
    focus — MODULE_NAME, карточка которого открыта сразу (.help <module>): от него считается страница «назад».
    """
    state = {'page': _module_page(focus) if focus else 0}

    async def _show_picker(page):
        text, buttons, state['page'] = _picker(page)
        await form.edit(text, buttons=buttons)

    @form.on_click(data_startswith="help_pick:")
    async def _pick(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data_startswith="help_page:")
    async def _page(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data="help_menu")
    async def _menu(click):
        await click.answer()
        text, _ = _modules_list_text()
        await form.edit(text, buttons=_menu_buttons())

    @form.on_click(data_startswith="help_open:")
    async def _open(click):
        await click.answer()
        module_name = click.data.split(":", 1)[1]
        info = engine.get_module_info(module_name)
        if not info:
            await form.edit(
                visual.err('Модуль не найден') + "\n" + visual.hint('возможно, он был удалён'),
                buttons=[[(visual.btn('К выбору модуля', 'back'), "help_back")]],
            )
            return
        state['page'] = _module_page(module_name)
        await form.edit(_module_detail_text(info, module_name), buttons=await _module_detail_buttons(module_name))

    @form.on_click(data="help_back")
    async def _back(click):
        await click.answer()
        await _show_picker(state['page'])

    @form.on_click(data_startswith="help_cfg:")
    async def _cfg(click):
        module_name = click.data.split(":", 1)[1]
        await click.answer("Открываю конфиг…")
        try:
            # буквально `.config <module>` — та же инлайн-форма с редактированием
            ok = await engine.exec_modcmd(event, 'config', 'config', module_name)
            if not ok:
                await engine.reply(event, visual.err('Не удалось открыть конфиг') + "\n" + visual.field('модуль', module_name) + "\n" + visual.hint('подробности — в логах'))
        except Exception as e:
            engine.tlog(f"Ошибка открытия конфига {module_name} из .help: {e}")
            await engine.reply(event, visual.err('Не удалось открыть конфиг') + "\n" + visual.field('модуль', module_name) + f"\n<code>{html.escape(str(e))}</code>")

    @form.on_click(data_startswith="help_send:")
    async def _send(click):
        module_name = click.data.split(":", 1)[1]
        await click.answer("Отправляю файл…")
        try:
            # буквально `.sendmodule <module>` — файл модуля приходит в этот же чат
            ok = await engine.exec_modcmd(event, 'loader', 'sendmodule', module_name)
            if not ok:
                await engine.reply(event, visual.err('Не удалось отправить модуль') + "\n" + visual.field('модуль', module_name) + "\n" + visual.hint('подробности — в логах'))
        except Exception as e:
            engine.tlog(f"Ошибка отправки модуля {module_name} из .help: {e}")
            await engine.reply(event, visual.err('Не удалось отправить модуль') + "\n" + visual.field('модуль', module_name) + f"\n<code>{html.escape(str(e))}</code>")

    @form.on_click(data_startswith="help_del_ask:")
    async def _del_ask(click):
        await click.answer()
        module_name = click.data.split(":", 1)[1]
        text = (
            visual.warn(f"<b>Удалить модуль</b> <code>{module_name}</code><b>?</b>") + "\n"
            + visual.hint('он будет выгружен, а его файл — удалён с диска (это необратимо). '
                          'Выгрузить с возможностью вернуть — <code>.unloadmod</code>')
        )
        buttons = [
            [(visual.btn('Да, удалить'), f"help_del_confirm:{module_name}")],
            [(visual.btn('Отмена', 'close'), f"help_open:{module_name}")],
        ]
        await form.edit(text, buttons=buttons)

    @form.on_click(data_startswith="help_del_confirm:")
    async def _del_confirm(click):
        await click.answer()
        module_name = click.data.split(":", 1)[1]
        module_info = engine.modules.get(module_name)

        if module_info is None:
            await form.edit(
                visual.err('Модуль уже не загружен'),
                buttons=[[(visual.btn('К выбору модуля', 'back'), "help_back")]],
            )
            return
        if module_info.is_builtin:
            await form.edit(
                visual.warn('Нельзя удалить системный модуль'),
                buttons=[[(visual.btn('К выбору модуля', 'back'), "help_back")]],
            )
            return

        path = module_info.path
        try:
            # archive=False: файл не уезжает в unloaded/, а удаляется совсем
            await engine.unload_module(module_name, archive=False)
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
            text = visual.ok('Модуль удалён') + "\n" + visual.field('модуль', module_name)
        except Exception as e:
            text = visual.err('Ошибка при удалении') + f"\n<code>{html.escape(str(e))}</code>"
            engine.tlog(f"Ошибка удаления модуля {module_name}: {e}")

        await form.edit(text, buttons=[[(visual.btn('К выбору модуля', 'back'), "help_back")]])


# ------------------------------------------------------------------ #
# Команды
# ------------------------------------------------------------------ #

@decorators.command('modules', 'mods')
async def list_modules_cmd(event):
    """показывает список всех модулей (инлайн-меню, если доступно)"""
    try:
        text, sorted_modules = _modules_list_text()
        if not sorted_modules:
            await engine.answer(event, text)
            return

        try:
            form = await engine.create_inlineform(
                chat_id=event.chat_id, text=text, buttons=_menu_buttons(),
            )
        except Exception:
            # Стационарный бот не подключён — инлайн недоступен, отдаём как раньше
            await engine.answer(event, text)
            return

        _bind_helper_form(form, event)
        await engine.answer(event, visual.ok('Модули') + "  " + visual.hint('ниже'))

    except KeyError as e:
        await engine.answer(event, visual.err('Ошибка') + f"\n<code>{html.escape(str(e))}</code>")
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при получении списка модулей') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка в modules: {e}")


@decorators.command('help', 'h')
async def help_cmd(event):
    """справка по модулям/командам; с аргументом-модулем сразу открывает его карточку (инлайн, если доступно)"""
    args = engine.get_args()

    if not args:
        await list_modules_cmd(event)
        return

    query = args.strip()

    try:
        modules = engine.get_module_list()

        resolved = engine.resolve_module(query)
        if resolved is not None:
            info = engine.get_module_info(resolved)
            if info:
                text = _module_detail_text(info, resolved)
                try:
                    form = await engine.create_inlineform(
                        chat_id=event.chat_id, text=text, buttons=await _module_detail_buttons(resolved),
                    )
                except Exception:
                    await show_module_info(event, info, resolved)
                    return
                _bind_helper_form(form, event, focus=resolved)
                await engine.answer(event, visual.ok('Карточка модуля') + "  " + visual.hint('ниже'))
                return

        # Если модуль не найден, ищем команду среди всех модулей
        for module_name in modules:
            info = engine.get_module_info(module_name)
            if info and 'commands' in info:
                for cmd_info in info['commands']:
                    if cmd_info['command'] == query:
                        await show_command_info(event, query, info, module_name)
                        return
                    if query in cmd_info.get('aliases', []):
                        await show_command_info(event, query, info, module_name, is_alias=True)
                        return

        similar = engine.search_module(query)
        if similar and similar != query:
            await engine.answer(
                event,
                visual.warn('Модуль не найден') + "\n"
                + visual.field('возможно, вы имели в виду', html.escape(str(similar))),
            )
            return

        similar_modules = engine.search_dec_module(query)
        if similar_modules:
            modules_list = [visual.item(f"<code>{html.escape(str(m))}</code>") for m in similar_modules[:3]]
            await engine.answer(
                event,
                visual.warn('Команда не найдена') + "\n"
                + visual.hint('похожие команды есть в модулях:') + "\n"
                + "\n".join(modules_list),
            )
            return

        await engine.answer(event, visual.err('Ничего не найдено') + "\n" + visual.field('запрос', html.escape(query)))

    except KeyError as e:
        await engine.answer(event, visual.err('Ошибка') + f"\n<code>{html.escape(str(e))}</code>")
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при получении справки') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка в help: {e}")


async def show_module_info(event, info, module_name):
    """Текстовый фолбэк (без инлайна) — стационарный бот не подключён."""
    try:
        await engine.answer(event, _module_detail_text(info, module_name))
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при показе информации') + f"\n<code>{html.escape(str(e))}</code>",
        )


async def show_command_info(event, command, info, module_name, is_alias=False):
    """Показывает информацию о команде"""
    try:
        display_name = info.get('name', module_name)

        cmd_info = None
        for cmd in info.get('commands', []):
            if cmd['command'] == command or command in cmd.get('aliases', []):
                cmd_info = cmd
                break

        text = visual.title('Команда') + "\n"

        if is_alias:
            text += visual.field('алиас', f".{command}") + "\n"
        else:
            text += visual.field('команда', f".{command}") + "\n"

        text += visual.item(f"модуль  <b>{display_name}</b> · <code>{module_name}</code>") + "\n"

        if cmd_info:
            if cmd_info.get('aliases') and not is_alias:
                aliases = [f"<code>.{a}</code>" for a in cmd_info['aliases']]
                text += visual.item(f"алиасы  {', '.join(aliases)}") + "\n"

            if cmd_info.get('description'):
                text += "\n" + visual.section('Описание') + f"\n{cmd_info['description']}"

        await engine.answer(event, text)

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при показе команды') + f"\n<code>{html.escape(str(e))}</code>",
        )


@decorators.command('checkmodule', 'checkmod')
async def check_module_cmd(event):
    """проверяет файл на соответствие модулю (ответом на .py файл)"""
    try:
        if not event.is_reply:
            await engine.answer(
                event, 
                visual.err('Ответьте на .py файл для проверки')
            )
            return
        
        replied_msg = await event.get_reply_message()
        
        if not replied_msg.document:
            await engine.answer(
                event,
                visual.err('Ответьте на файл, а не на текст')
            )
            return
        
        # Document.filename в Telethon не существует — имя в Message.file.name
        file_name = (replied_msg.file.name if replied_msg.file else '') or ''
        if not file_name.lower().endswith('.py'):
            await engine.answer(
                event,
                visual.err('Файл должен иметь расширение .py')
            )
            return
        
        await engine.answer(event, visual.wait('Проверяю файл…'))
        
        file_path = await replied_msg.download_media()
        
        if not file_path:
            await engine.answer(
                event,
                visual.err('Не удалось скачать файл')
            )
            return
        
        try:
            errors, warnings = validate_module_file(file_path)
            
            if errors:
                error_text = visual.title('Проверка модуля') + "\n"
                error_text += visual.err('Файл не является корректным модулем') + "\n\n"
                error_text += visual.section(f"Критические ошибки · {len(errors)}") + "\n"
                for i, error in enumerate(errors[:10], 1):
                    error_text += visual.item(f"{i}. {html.escape(str(error))}") + "\n"
                if len(errors) > 10:
                    error_text += visual.hint(f"… и ещё {len(errors)-10} ошибок") + "\n"
                
                if warnings:
                    error_text += "\n" + visual.section(f"Предупреждения · {len(warnings)}") + "\n"
                    for warning in warnings[:5]:
                        error_text += visual.item(html.escape(str(warning))) + "\n"
                    if len(warnings) > 5:
                        error_text += visual.hint(f"… и ещё {len(warnings)-5}") + "\n"
                
                await engine.answer(event, error_text)
                
            elif warnings:
                warn_text = visual.title('Проверка модуля') + "\n"
                warn_text += visual.ok('Файл является корректным модулем') + "\n\n"
                warn_text += visual.section(f"Предупреждения · {len(warnings)}") + "\n"
                for warning in warnings[:10]:
                    warn_text += visual.item(html.escape(str(warning))) + "\n"
                if len(warnings) > 10:
                    warn_text += visual.hint(f"… и ещё {len(warnings)-10}") + "\n"
                
                await engine.answer(event, warn_text)
                
            else:
                await engine.answer(
                    event,
                    visual.title('Проверка модуля') + "\n"
                    + visual.ok('Файл является корректным модулем') + "\n"
                    + visual.hint('все проверки пройдены')
                )
                
        finally:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except:
                pass
                
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при проверке модуля') + f"\n<code>{html.escape(str(e))}</code>"
        )
        engine.tlog(f"Ошибка checkmodule: {e}")

def validate_module_file(file_path):
    """Проверяет файл на соответствие модулю FRX"""
    errors = []
    warnings = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            errors.append(f"Синтаксическая ошибка: {e}")
            return errors, warnings
        
        # Проверяем наличие обязательных переменных
        required_vars = ['NAME', 'MODULE_NAME', 'VERSION', 'AUTHOR', 'DESCRIPTION']
        found_vars = []
        
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        if target.id in required_vars:
                            found_vars.append(target.id)
        
        missing_vars = [var for var in required_vars if var not in found_vars]
        if missing_vars:
            errors.append(f"Отсутствуют обязательные переменные: {', '.join(missing_vars)}")
        
        # Проверяем импорты
        has_engine_import = False
        has_decorators_import = False
        has_visual_import = False
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.module == 'engine':
                    has_engine_import = True
                if node.module == 'decorators':
                    has_decorators_import = True
                if node.module == 'visual':
                    has_visual_import = True
                for alias in node.names:
                    if alias.name == 'engine':
                        has_engine_import = True
                    if alias.name == 'decorators':
                        has_decorators_import = True
                    if alias.name == 'visual':
                        has_visual_import = True
            
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'engine':
                        has_engine_import = True
                    if alias.name == 'decorators':
                        has_decorators_import = True
                    if alias.name == 'visual':
                        has_visual_import = True
        
        if not has_engine_import:
            warnings.append("Рекомендуется импортировать модуль 'engine' (import engine)")
        if not has_decorators_import:
            warnings.append("Рекомендуется импортировать модуль 'decorators' (import decorators)")
        if not has_visual_import:
            warnings.append("Рекомендуется импортировать модуль 'visual' для единообразного оформления")
        
        # Проверяем наличие декораторов
        has_decorators = False
        decorator_names = ['command', 'watcher', 'on_start', 'on_load', 'cycle', 'callback', 'on_error']
        
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Attribute):
                            if hasattr(decorator.func, 'attr') and decorator.func.attr in decorator_names:
                                has_decorators = True
                        elif isinstance(decorator.func, ast.Name):
                            if decorator.func.id in decorator_names:
                                has_decorators = True
                    elif isinstance(decorator, ast.Name):
                        if decorator.id in decorator_names:
                            has_decorators = True
        
        if not has_decorators:
            warnings.append("Не найдено декораторов (команды/вотчеры)")
        
        # Проверяем функции
        functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        if not functions:
            warnings.append("Не найдено функций. Возможно, модуль пустой")
        
        # Проверяем docstrings
        has_docstrings = False
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.ClassDef, ast.Module)):
                if ast.get_docstring(node):
                    has_docstrings = True
                    break
        
        if not has_docstrings:
            warnings.append("Рекомендуется добавить строки документации (docstrings)")
        
        # Проверяем async функции с декораторами
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for decorator in node.decorator_list:
                    if isinstance(decorator, ast.Call):
                        if isinstance(decorator.func, ast.Attribute):
                            if decorator.func.attr in ['command', 'watcher', 'on_start', 'cycle', 'callback']:
                                if not node.async_:
                                    errors.append(f"Функция {node.name} с декоратором должна быть async")
                        elif isinstance(decorator.func, ast.Name):
                            if decorator.func.id in ['command', 'watcher', 'on_start', 'cycle', 'callback']:
                                if not node.async_:
                                    errors.append(f"Функция {node.name} с декоратором должна быть async")
        
        # Проверяем MODULE_NAME
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == 'MODULE_NAME':
                        if isinstance(node.value, ast.Constant):
                            module_name = node.value.value
                            if not isinstance(module_name, str) or not module_name:
                                errors.append("MODULE_NAME должен быть непустой строкой")
                            elif not module_name.isidentifier():
                                errors.append(f"MODULE_NAME '{module_name}' не является корректным идентификатором")
                            else:
                                modules = engine.get_module_list()
                                if module_name in modules:
                                    errors.append(f"MODULE_NAME '{module_name}' уже используется")
        
        # Проверяем использование db
        has_db_import = False
        uses_db = False
        
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'db':
                has_db_import = True
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == 'db':
                        has_db_import = True
            
            if isinstance(node, ast.Call):
                if isinstance(node.func, ast.Attribute):
                    if isinstance(node.func.value, ast.Name):
                        if node.func.value.id == 'db':
                            uses_db = True
        
        if uses_db and not has_db_import:
            errors.append("Используется db, но нет импорта 'import db'")
        
        # Проверяем обработку ошибок
        has_exception_handling = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                has_exception_handling = True
                break
        
        if has_decorators and not has_exception_handling:
            warnings.append("Рекомендуется добавить обработку исключений (try-except)")
        
    except Exception as e:
        errors.append(f"Ошибка при проверке файла: {str(e)}")
    
    return errors, warnings

@decorators.command('checkall')
async def check_all_modules_cmd(event):
    """проверяет все загруженные модули на наличие проблем"""
    try:
        modules = engine.get_module_list()
        
        if not modules:
            await engine.answer(
                event,
                visual.warn('Нет загруженных модулей для проверки')
            )
            return
        
        await engine.answer(event, visual.wait('Проверяю все модули…'))
        
        results = []
        for module_name in modules:
            info = engine.get_module_info(module_name)
            if info:
                issues = []
                if not info.get('name'):
                    issues.append("отсутствует имя")
                if not info.get('version'):
                    issues.append("отсутствует версия")
                if not info.get('author'):
                    issues.append("отсутствует автор")
                if not info.get('description'):
                    issues.append("отсутствует описание")
                if not info.get('commands'):
                    issues.append("нет команд")
                
                if issues:
                    results.append(visual.item(f"<code>{module_name}</code>  {', '.join(issues)}"))
        
        if results:
            text = visual.title('Проверка модулей', f"проверено: {len(modules)}") + "\n"
            text += visual.warn(f"Найдены проблемы в модулях: <b>{len(results)}</b>") + "\n\n"
            text += "\n".join(results)
            await engine.answer(event, text)
        else:
            await engine.answer(
                event,
                visual.title('Проверка модулей', f"проверено: {len(modules)}") + "\n"
                + visual.ok('Все модули в порядке')
            )
            
    except KeyError as e:
        await engine.answer(
            event,
            visual.err('Ошибка') + f"\n<code>{html.escape(str(e))}</code>"
        )
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при проверке') + f"\n<code>{html.escape(str(e))}</code>"
        )
        engine.tlog(f"Ошибка checkall: {e}")