"""
modules/builtin/Helper.py — помощь по модулям, проверка файлов модулей и карточки модулей.

Команды:
    .modules / .mods       список модулей текстом — «Встроенные», «Загруженные» и «Сломанные» (загружены с ошибками:
                           что исполнилось — работает, остальное «спит») — и кнопка «Выбрать модуль»:
                           7 модулей на странице (встроенные ▣, загруженные ▤, навигация — engine.picker_page()).
    .help / .h [модуль]    то же; с именем модуля — сразу его карточка.
    .checkmodule / .checkmod, .checkall — проверка файла модуля и всех модулей на типовые ошибки.

Карточка модуля: описание, команды (у сломанного — ещё «Ошибки загрузки» и «Спят»); кнопки «▸ Отправить модуль» (как `.sendmodule`, modules/builtin/Loader.py),
«Открыть конфиг» — только если у модуля есть настройки, открывает `.config <модуль>` через engine.exec_modcmd(),
«Удалить модуль» — с подтверждением, удаляет файл совсем (мягкая выгрузка — .unloadmod, возврат — .restoremods);
для встроенных модулей удаление недоступно. «Назад» возвращает на ту страницу списка, где стоит модуль.

Без стационарного бота команды отвечают обычным текстом без кнопок. Описания с «<не_тегами>» экранируются
(core/html_utils.py), поэтому не ломают отправку.
"""

import ast
import html
import re
import os

import engine
import decorators
import visual
import db

NAME = "Helper"
MODULE_NAME = "helper"
VERSION = "2.5"
AUTHOR = "FRX"
DESCRIPTION = "Информация о модулях (инлайн-меню), проверка файлов и помощь"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Helper.py"


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


def _module_groups3():
    """([встроенные], [загруженные], [сломанные]) — MODULE_NAME, каждая группа по алфавиту.
    «Сломанные» — загруженные с ошибками (часть кода не исполнилась или часть функций спит), см. .help <модуль>."""
    builtin, loaded, broken = [], [], []
    for module_name in sorted(engine.get_module_list(), key=lambda x: x.lower()):
        info = engine.get_module_info(module_name)
        if not info:
            continue
        if info.get('is_broken'):
            broken.append(module_name)
        else:
            (builtin if info.get('is_builtin') else loaded).append(module_name)
    return builtin, loaded, broken


def _module_groups():
    """([встроенные], [загруженные + сломанные]) — для мест, где нужно только «встроенный или нет»."""
    builtin, loaded, broken = _module_groups3()
    return builtin, loaded + broken


def _ordered_modules():
    """Сперва встроенные, потом загруженные, в конце — сломанные (тот же порядок, что в списке)."""
    builtin, loaded, broken = _module_groups3()
    return builtin + loaded + broken


def _module_line(module_name):
    info = engine.get_module_info(module_name)
    display_name = info.get('name', module_name)
    commands = info.get('commands', [])
    if commands:
        cmd_list = format_commands_for_list(commands, 40)
        cmd_str = ', '.join([f'<code>.{cmd}</code>' for cmd in cmd_list])
    else:
        cmd_str = "<i>нет команд</i>"
    extra = []
    iq = info.get('bot_inline_queries') or []
    if iq:
        extra.append(f"🤖 <code>@{_bot_username() or 'бот'} {iq[0]['name']}</code>" + (f" +{len(iq) - 1}" if len(iq) > 1 else ''))
    if info.get('bot_commands'):
        extra.append(f"/{info['bot_commands'][0]['command']}" + (f" +{len(info['bot_commands']) - 1}" if len(info['bot_commands']) > 1 else ''))
    lines = [visual.item(f"<b>{display_name}</b>  <code>{module_name}</code>"), visual.item(cmd_str, 1)]
    if extra:
        lines.append(visual.item(", ".join(extra), 1))
    return "\n".join(lines)


def _bot_username():
    """username стационарного бота (для подсказок «@бот …»), если он известен."""
    for attr in ('_bot_username', 'bot_username'):
        v = getattr(engine, attr, None)
        if isinstance(v, str) and v:
            return v.lstrip('@')
    me = getattr(engine, '_bot_me', None)
    return getattr(me, 'username', None)


ACCESS_LABEL = {'self': '🔒 только вы', 'public': '🌐 все', 'owner': '🔒 только вы'}


def _access(entry):
    label = ACCESS_LABEL.get(entry.get('access'), entry.get('access') or '')
    if entry.get('for_user'):
        label += ' + избранные'
    return label


def _list_pages():
    """Главный экран постранично: [текст страницы, …] + упорядоченный список MODULE_NAME.
    Всё, что не влезает в одно сообщение, уходит на следующие страницы (стрелки ◂ ▸ под списком)."""
    builtin, loaded, broken = _module_groups3()
    total = len(builtin) + len(loaded) + len(broken)
    if not total:
        return [visual.warn('Нет загруженных модулей')], []

    blocks = []                      # (заголовок раздела | None, строка)
    for title, names, empty in (
        (visual.section(f"Встроенные ({len(builtin)})"), builtin, visual.hint('нет встроенных модулей')),
        (visual.section(f"Загруженные ({len(loaded)})"), loaded, visual.hint('нет загруженных модулей — <code>.loadmod</code>')),
        *([(visual.section(f"Сломанные ({len(broken)})"), broken, '')] if broken else []),
    ):
        if not names:
            blocks.append((title, empty))
        for module_name in names:
            blocks.append((title, _module_line(module_name)))

    # инлайн-запросы и команды бота всех модулей — одной строкой каждый, с доступом
    bot_name = _bot_username() or 'бот'
    inline, bcmds = [], []
    for module_name in builtin + loaded + broken:
        info = engine.get_module_info(module_name) or {}
        for e in (info.get('bot_inline_queries') or []) + (info.get('bot_inline_functions') or []):
            inline.append(f"<code>{e['name']}</code> <i>{_access(e).split(' ', 1)[0]}</i>")
        for e in info.get('bot_commands') or []:
            bcmds.append(f"<code>/{e['command']}</code> <i>{_access(e).split(' ', 1)[0]}</i>")
    for title, items in ((f"Инлайн @{bot_name} ({len(inline)})", inline), (f"Команды бота ({len(bcmds)})", bcmds)):
        if items:
            blocks.append((visual.section(title), ", ".join(items)))
    if inline or bcmds:
        blocks.append((None, "\n" + visual.hints("🔒 — только вы, 🌐 — все", "подробности — в карточке модуля")))

    pages, cur, used, last_title = [], [], 0, object()
    budget = LIST_TEXT_LIMIT - 200                 # запас под заголовок страницы
    for title, line in blocks:
        add = ([""] + [title] if title is not None and title != last_title else []) + [line]
        size = sum(len(x) + 1 for x in add)
        if cur and used + size > budget:
            pages.append(cur)
            cur, used = [], 0
            add = ([""] + [title] if title is not None else []) + [line]      # раздел продолжается — повторяем заголовок
            size = sum(len(x) + 1 for x in add)
        cur += add
        used += size
        last_title = title if title is not None else last_title
    if cur:
        pages.append(cur)
    n = len(pages)
    out = []
    for i, body in enumerate(pages):
        sub = f"всего: {total}" + (f"\nстраница {i + 1} из {n}" if n > 1 else "")
        out.append("\n".join([visual.title('Модули', sub)] + body))
    return out, builtin + loaded


def _modules_list_text(page=0):
    """(текст страницы page главного экрана, упорядоченный список MODULE_NAME) — для совместимости."""
    pages, ordered = _list_pages()
    return pages[max(0, min(page, len(pages) - 1))], ordered


def _menu_buttons(page=0, total=1):
    """Главный экран: стрелки страниц (если список не влез в одно сообщение) и «Выбрать модуль»."""
    rows = []
    if total > 1:
        rows.append([("◂", f"help_lp:{page - 1}") if page > 0 else ("·", "help_noop"),
                     (f"{page + 1} / {total}", "help_noop"),
                     ("▸", f"help_lp:{page + 1}") if page < total - 1 else ("·", "help_noop")])
    rows.append([(visual.btn('Выбрать модуль'), "help_pick:0", "primary")])
    return rows


def _search_modules(query):
    """Модули по запросу: имя, MODULE_NAME, команды и описание (без учёта регистра); ничего — ближайшее по написанию."""
    q = (query or '').strip().lower().lstrip('.')
    if not q:
        return _ordered_modules()
    exact, partial = [], []
    for module_name in _ordered_modules():
        info = engine.get_module_info(module_name) or {}
        names = [module_name.lower(), str(info.get('name', '')).lower()]
        cmds = [c['command'].lower() for c in info.get('commands', [])] + \
               [a.lower() for c in info.get('commands', []) for a in c.get('aliases', [])]
        if q in names or q in cmds:
            exact.append(module_name)
        elif any(q in x for x in names + cmds) or q in str(info.get('description', '')).lower():
            partial.append(module_name)
    found = exact + partial
    if not found:
        similar = engine.search_module(q)
        if similar:
            found = [similar]
    return found


def _picker(page, query=None):
    """Экран выбора: до 7 модулей (встроенные, затем загруженные), поиск, навигация. -> (text, buttons, page)."""
    names = _search_modules(query) if query else _ordered_modules()
    items = []
    for module_name in names:
        info = engine.get_module_info(module_name) or {}
        icon = visual.visual('module') if info.get('is_builtin') else visual.visual('file')
        if info.get('is_broken'):
            icon = visual.visual('status_warning')
        items.append((f"{icon} {info.get('name', module_name)}", f"help_open:{module_name}"))
    if items:
        rows, page, total = engine.picker_page(items, page, per_page=PAGE_SIZE, page_callback="help_page:")
    else:
        rows, page, total = [], 0, 1
    search_row = [engine.SearchButton("🔍 Найти модуль", "help_search", hint="название модуля, команда или слово из описания")]
    if query:
        search_row.append((visual.btn('Сбросить поиск', 'close'), "help_search_reset"))
    rows.insert(0, search_row)
    rows.append([(visual.btn('К списку модулей', 'back'), "help_menu")])
    if query:
        head = visual.title('Модули', f"поиск «{html.escape(query)}»\nнайдено: {len(names)}"
                            + (f"\nстраница {page + 1} из {total}" if total > 1 else ""))
        text = head + ("" if names else "\n" + visual.hint("ничего не нашлось — попробуйте иначе"))
    else:
        text = (
            visual.title('Модули', f"выберите модуль\nстраница {page + 1} из {total}") + "\n"
            + visual.hints(f"{visual.visual('module')} — встроенные, {visual.visual('file')} — загруженные",
                           "🔍 — поиск по названию, команде или описанию")
        )
    return text, rows, page


def _module_page(module_name):
    """Номер страницы выбора, на которой находится модуль."""
    ordered = _ordered_modules()
    return ordered.index(module_name) // PAGE_SIZE if module_name in ordered else 0


def _link(url):
    """Ссылка из шапки модуля коротко: https://t.me/x → t.me/x, https://github.com/a/b → github.com/a/b."""
    from core.module_meta import short_link
    return f'<a href="{html.escape(url, quote=True)}">{html.escape(short_link(url))}</a>'


def _broken_lines(info, module_name):
    """Блок «Ошибки загрузки» и «Спят» в карточке сломанного модуля."""
    errors, sleeping = info.get('load_errors') or [], info.get('sleeping') or []
    if not errors and not sleeping:
        return []
    out = []
    if errors:
        out.append(visual.section(f"Ошибки загрузки ({len(errors)})"))
        for e in errors[:8]:
            where = f"строка {e['line']}" if e.get('line') else (e.get('stage') or '?')
            if e.get('function'):
                where += f", {html.escape(e['function'])}()"
            out.append(visual.item(f"{where}: <code>{html.escape(str(e.get('error') or '?')[:300])}</code>"))
        if len(errors) > 8:
            out.append(visual.hint(f"… и ещё {len(errors) - 8}"))
    if sleeping:
        out.append(visual.section(f"Спят ({len(sleeping)})"))
        for z in sleeping[:12]:
            out.append(visual.item(f"<code>{html.escape(z.get('name') or '?')}</code> — {html.escape(z.get('kind') or '')}: "
                                   f"{html.escape(str(z.get('reason') or '')[:200])}"))
        if len(sleeping) > 12:
            out.append(visual.hint(f"… и ещё {len(sleeping) - 12}"))
    out.append(visual.hint(f"исправьте файл и выполните <code>.reloadmod {html.escape(module_name)}</code> — "
                           "остальные функции модуля работают"))
    out.append("")
    return out


def _module_detail_text(info, module_name):
    """Подробная карточка модуля — используется и .help <module>, и инлайн-меню."""
    display_name = info.get('name', module_name)
    version = info.get('version', 'Не указана')
    author = info.get('author', 'Не указан')
    description = info.get('description', 'Нет описания')
    commands = info.get('commands', [])
    kind = "встроенный" if info.get('is_builtin') else "загруженный"
    if info.get('is_broken'):
        kind += ", с ошибками"

    lines = [
        visual.title(display_name, description),
        visual.field('id', module_name),
        visual.field('версия', version),
        visual.field('автор', author, code=False),
        visual.field('тип', kind, code=False),
    ]
    if info.get('tg_url'):
        lines.append(visual.field('канал', _link(info['tg_url']), code=False))
    if info.get('git_url'):
        lines.append(visual.field('исходник', _link(info['git_url']), code=False))
    lines.append("")
    lines += _broken_lines(info, module_name)

    if commands:
        lines.append(visual.section(f"Команды ({len(commands)})"))
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

    bot_name = _bot_username() or 'бот'
    iq = info.get('bot_inline_queries') or []
    inl = info.get('bot_inline_functions') or []
    if iq or inl:
        lines += ["", visual.section(f"Инлайн @{bot_name} ({len(iq) + len(inl)})")]
        for e in iq + inl:
            al = e.get('aliases') or []
            s_ = f"<code>@{bot_name} {e['name']}</code>"
            if al:
                s_ += " " + visual.hint(', '.join(al))
            lines.append(visual.item(f"{s_} — {e.get('description') or 'без описания'}  <i>{_access(e)}</i>"))
    bcmd = info.get('bot_commands') or []
    if bcmd:
        lines += ["", visual.section(f"Команды бота ({len(bcmd)})")]
        for e in bcmd:
            al = e.get('aliases') or []
            s_ = f"<code>/{e['command']}</code>"
            if al:
                s_ += " " + visual.hint(', '.join('/' + a for a in al))
            lines.append(visual.item(f"{s_} — {e.get('description') or 'без описания'}  <i>{_access(e)}</i>"))

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

    rows = list(engine.module_links(module_name))          # ↗ Канал автора · ↗ GitHub (TG_URL / GIT_URL из шапки)
    if show_back:
        rows.append([(visual.btn('К выбору модуля', 'back'), "help_back")])
    if await _has_config(module_name):       # кнопка только если конфиг действительно есть
        rows.append([(visual.btn('Открыть конфиг'), f"help_cfg:{module_name}", "primary")])
    rows.append([(visual.btn('Отправить модуль'), f"help_send:{module_name}")])      # тот же .sendmodule
    if not is_builtin:
        rows.append([(visual.btn('Удалить модуль', 'close'), f"help_del_ask:{module_name}", "danger")])
    return rows


def _bind_helper_form(form, event, focus=None):
    """
    Общие обработчики клавиатуры .modules/.help — вешаются один раз на форму.
    event — исходное событие команды (нужно кнопке «Открыть конфиг»: engine.exec_modcmd()).
    focus — MODULE_NAME, карточка которого открыта сразу (.help <module>): от него считается страница «назад».
    """
    state = {'page': _module_page(focus) if focus else 0, 'q': None, 'lp': 0}

    async def _show_picker(page):
        text, buttons, state['page'] = _picker(page, state['q'])
        await form.edit(text, buttons=buttons)

    async def _show_list(page):
        pages, _ = _list_pages()
        state['lp'] = max(0, min(page, len(pages) - 1))
        await form.edit(pages[state['lp']], buttons=_menu_buttons(state['lp'], len(pages)))

    @form.on_click(data_startswith="help_lp:")
    async def _list_page(click):
        await click.answer()
        await _show_list(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data="help_noop")
    async def _noop(click):
        await click.answer()

    @form.on_input(data="help_search")
    async def _search(inp):
        q = (inp.value or '').strip()
        state['q'] = q or None
        found = _search_modules(q) if q else []
        if q and len(found) == 1:                   # единственное совпадение — сразу карточка
            module_name = found[0]
            info = engine.get_module_info(module_name)
            await form.edit(_module_detail_text(info, module_name), buttons=await _module_detail_buttons(module_name))
            return
        await _show_picker(0)

    @form.on_click(data="help_search_reset")
    async def _search_reset(click):
        await click.answer()
        state['q'] = None
        await _show_picker(0)

    @form.on_click(data_startswith="help_pick:")
    async def _pick(click):
        await click.answer()
        state['q'] = None
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data_startswith="help_page:")
    async def _page(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data="help_menu")
    async def _menu(click):
        await click.answer()
        await _show_list(state['lp'])

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
            [(visual.btn('Да, удалить'), f"help_del_confirm:{module_name}", "danger")],
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
        pages, sorted_modules = _list_pages()
        if not sorted_modules:
            await engine.answer(event, pages[0])
            return

        try:
            form = await engine.create_inlineform(
                chat_id=event.chat_id, text=pages[0], buttons=_menu_buttons(0, len(pages)),
            )
        except Exception:
            # Стационарный бот не подключён — инлайн недоступен: все страницы отдельными сообщениями
            await engine.answer(event, pages[0])
            for extra in pages[1:]:
                await engine.client.send_message(event.chat_id, extra, parse_mode='html')
            return

        _bind_helper_form(form, event)
        await engine.answer(event, visual.ok('Модули') + "\n" + visual.hint('список — ниже'))

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

    async def open_module(resolved, note=''):
        info = engine.get_module_info(resolved)
        if not info:
            return False
        text = _module_detail_text(info, resolved)
        if note:
            text = note + "\n" + text
        try:
            form = await engine.create_inlineform(
                chat_id=event.chat_id, text=text, buttons=await _module_detail_buttons(resolved),
            )
        except Exception:
            await show_module_info(event, info, resolved, note=note)
            return True
        _bind_helper_form(form, event, focus=resolved)
        await engine.answer(event, visual.ok('Карточка модуля') + "\n" + visual.hint('карточка — ниже'))
        return True

    try:
        modules = engine.get_module_list()

        resolved = engine.resolve_module(query)
        if resolved is not None and await open_module(resolved):
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

        # Точного совпадения нет — сразу показываем самое близкое (модуль или команду), без «возможно, вы имели в виду»
        best = _closest(query, modules)
        if best is not None:
            kind, name, module_name, is_alias = best
            if kind == 'module':
                note = visual.hint(f'«{html.escape(query)}» — точно такого нет, показываю ближайшее: {html.escape(name)}')
                if await open_module(module_name, note):
                    return
            else:
                info = engine.get_module_info(module_name)
                if info:
                    prefix = await engine.get_prefix()
                    note = visual.hint(f'«{html.escape(query)}» — точно такого нет, показываю ближайшее: '
                                       f'{html.escape(prefix + name)}')
                    await show_command_info(event, name, info, module_name, is_alias=is_alias, note=note)
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


async def show_module_info(event, info, module_name, note=''):
    """Текстовый фолбэк (без инлайна) — стационарный бот не подключён."""
    try:
        await engine.answer(event, (note + "\n" if note else '') + _module_detail_text(info, module_name))
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при показе информации') + f"\n<code>{html.escape(str(e))}</code>",
        )


def _closest(query, modules):
    """
    Самое похожее на запрос: модуль (MODULE_NAME / NAME) или команда (имя / алиас).
    -> ('module' | 'command', найденное имя, MODULE_NAME, это алиас?) или None, если ничего даже отдалённо не похоже.
    """
    import difflib
    q = query.strip().lower().lstrip('.,!/')
    if not q:
        return None
    cands = []          # (имя, kind, module_name, is_alias)
    for module_name in modules:
        info = engine.get_module_info(module_name) or {}
        cands.append((module_name.lower(), 'module', module_name, False))
        if info.get('name'):
            cands.append((str(info['name']).lower(), 'module', module_name, False))
        for cmd in info.get('commands', []):
            cands.append((cmd['command'].lower(), 'command', module_name, False))
            for a in cmd.get('aliases', []):
                cands.append((str(a).lower(), 'command', module_name, True))

    def score(name):
        r = difflib.SequenceMatcher(None, q, name).ratio()
        if name.startswith(q) or q.startswith(name):
            r += 0.15                          # «pin» → «ping», «helpme» → «help»
        return r

    best, best_score = None, 0.0
    for name, kind, module_name, is_alias in cands:
        sc = score(name)
        # при равенстве модуль важнее команды: «.help pinh» → карточка модуля ping (там и сама .ping)
        if sc > best_score or (sc == best_score and kind == 'module' and best and best[1] == 'command'):
            best, best_score = (name, kind, module_name, is_alias), sc
    if best is None or best_score < 0.5:
        return None
    name, kind, module_name, is_alias = best
    return kind, name, module_name, is_alias


async def show_command_info(event, command, info, module_name, is_alias=False, note=''):
    """Показывает информацию о команде"""
    try:
        display_name = info.get('name', module_name)

        cmd_info = None
        for cmd in info.get('commands', []):
            if cmd['command'] == command or command in cmd.get('aliases', []):
                cmd_info = cmd
                break

        text = (note + "\n" if note else '') + visual.title('Команда') + "\n"

        if is_alias:
            text += visual.field('алиас', f".{command}") + "\n"
        else:
            text += visual.field('команда', f".{command}") + "\n"

        text += visual.item(f"модуль  <b>{display_name}</b>  <code>{module_name}</code>") + "\n"

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
                error_text += visual.section(f"Критические ошибки ({len(errors)})") + "\n"
                for i, error in enumerate(errors[:10], 1):
                    error_text += visual.item(f"{i}. {html.escape(str(error))}") + "\n"
                if len(errors) > 10:
                    error_text += visual.hint(f"… и ещё {len(errors)-10} ошибок") + "\n"
                
                if warnings:
                    error_text += "\n" + visual.section(f"Предупреждения ({len(warnings)})") + "\n"
                    for warning in warnings[:5]:
                        error_text += visual.item(html.escape(str(warning))) + "\n"
                    if len(warnings) > 5:
                        error_text += visual.hint(f"… и ещё {len(warnings)-5}") + "\n"
                
                await engine.answer(event, error_text)
                
            elif warnings:
                warn_text = visual.title('Проверка модуля') + "\n"
                warn_text += visual.ok('Файл является корректным модулем') + "\n\n"
                warn_text += visual.section(f"Предупреждения ({len(warnings)})") + "\n"
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

        # Ссылки автора (необязательные): TG_URL — канал, GIT_URL — исходник модуля
        from core.module_meta import normalize_module_link
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in ('TG_URL', 'GIT_URL'):
                        kind = 'tg' if target.id == 'TG_URL' else 'git'
                        if node.value.value and not normalize_module_link(node.value.value, kind):
                            warnings.append(f"{target.id} = {node.value.value!r} — не похоже на ссылку "
                                            + ("(нужно @канал или https://t.me/…)" if kind == 'tg' else "(нужно https://…)"))
        if 'TG_URL' not in content and 'GIT_URL' not in content:
            warnings.append("Можно указать ссылки автора: TG_URL = 'https://t.me/канал', GIT_URL = 'https://github.com/…'")
        
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