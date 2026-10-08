"""
modules/builtin/Loader.py — загрузка, обновление, выгрузка, восстановление и перезагрузка модулей через Telegram.

Команды:
    .loadmod / .lm         ответом на .py или .txt — разбирает файл БЕЗ исполнения (engine.parse_module_meta(), ast)
                           и показывает превью: название, ID, версию, автора, описание, команды. Модуль исполняется
                           только после «Загрузить». Файл .txt сохраняется как user/modules/{MODULE_NAME}.py.
                           Модуль с тем же MODULE_NAME и ДРУГОЙ версией — это обновление: превью «версия X → Y»
                           и кнопка «Обновить» (старая версия тихо выгружается, см. Core._load_from_source());
                           та же версия — ошибка «уже загружен».
    .unloadmod / .ulm      выгружает модуль и переносит его файл в user/modules/unloaded/.
    .restoremods / .rsm    выгруженные модули: список → карточка с «Восстановить» / «Удалить навсегда»;
                           `.restoremods <имя>` открывает карточку сразу (без стационарного бота — восстанавливает).
    .reloadmod / .rm       перезагружает модуль с диска.
    .sendmodule / .sm      присылает файл модуля в чат с подписью (название, ID, версия, автор, описание);
                           работает и для встроенных модулей. То же — кнопка «▸ Отправить модуль» в карточке .modules.

Расширение файла проверяется без учёта регистра, временный файл анализа удаляется после превью.
Логика списков и восстановления — engine.list_unloaded_modules() / restore_module() / delete_unloaded_module().
"""

import ast
import html
import os
from pathlib import Path

import engine
import decorators
import visual

NAME = "Loader"
MODULE_NAME = "loader"
VERSION = "2.5"
AUTHOR = "FRX"
DESCRIPTION = "Управление модулями: загрузка/обновление (.py/.txt, с превью и подтверждением), выгрузка (в user/modules/unloaded), восстановление (.restoremods), перезагрузка и отправка файла модуля (.sendmodule)"

ALLOWED_EXTENSIONS = ('.py', '.txt')


def _parse_module_meta(source: str):
    """
    Статически разбирает исходник модуля (ast, без exec) — достаточно, чтобы показать превью ДО
    реальной загрузки. None — синтаксическая ошибка или нет MODULE_NAME.
    сам разбор переехал в движок (engine.parse_module_meta, core/module_meta.py) —
    им же читаются файлы из user/modules/unloaded/.
    """
    return engine.parse_module_meta(source)


def _build_preview_text(meta: dict, file_name: str, existing=None) -> str:
    """
    existing — ModuleInfo уже загруженного модуля с тем же MODULE_NAME,
    если это не первая загрузка, а версия отличается: показываем разницу версий и
    просим подтвердить ОБНОВЛЕНИЕ, а не загрузку с нуля (см. loadmod_cmd/_confirm).
    """
    commands = meta.get('commands') or []
    if commands:
        cmd_lines = "\n".join(
            visual.item(
                f"<code>.{names[0]}</code>"
                + (f"  <i>алиасы: {', '.join('.' + n for n in names[1:])}</i>" if len(names) > 1 else "")
            )
            for names in commands
        )
    else:
        cmd_lines = visual.hint('команд не найдено')

    if existing is not None:
        header = visual.title('Обновление модуля', 'найдена новая версия — подтвердите обновление')
        version_line = visual.field('версия', f"{existing.version} → {meta['version']}")
        question = "Обновить этот модуль?"
    else:
        header = visual.title('Новый модуль', 'подтвердите загрузку')
        version_line = visual.field('версия', meta['version'])
        question = "Загрузить этот модуль?"

    return "\n".join([
        header,
        visual.field('файл', html.escape(file_name)),
        visual.field('название', meta['name'], code=False),
        visual.field('id', meta['module_name']),
        version_line,
        visual.field('автор', meta['author'], code=False),
        visual.item(f"описание  {meta['description'] or '—'}"),
        "",
        visual.section(f"Команды · {len(commands)}"),
        cmd_lines,
        "",
        visual.hint(question),
    ])


async def _do_load_now(event, source: str, is_update: bool = False):
    """Фолбэк без инлайна (стационарный бот не подключён) — грузит/обновляет сразу, без превью."""
    verb = "обновлён" if is_update else "загружен"
    try:
        info = await engine._load_from_source(source, target='loaded')
        await engine.answer(
            event,
            "\n".join([
                visual.ok(f"Модуль {verb}"),
                visual.field('название', info.name, code=False),
                visual.field('id', info.module_name),
                visual.field('версия', info.version),
                visual.field('автор', info.author, code=False),
                visual.item(f"описание  {info.description or '—'}"),
            ]),
        )
    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка загрузки модуля: {e}")


@decorators.command('loadmod', 'lm')
async def loadmod_cmd(event):
    """показывает превью модуля и загружает его после подтверждения (ответом на .py или .txt файл)"""
    try:
        if not event.is_reply:
            await engine.answer(
                event,
                visual.err('Ответьте на .py или .txt файл с модулем'),
            )
            return

        replied_msg = await event.get_reply_message()

        if not replied_msg.document:
            await engine.answer(
                event,
                visual.err('Ответьте на файл, а не на текст'),
            )
            return

        # filename может быть None (не только отсутствовать) —
        # раньше .endswith() на None падал необработанным исключением.
        # у telethon.types.Document нет атрибута filename (getattr всегда
        # возвращал '' и .loadmod отклонял ЛЮБОЙ файл) — имя берём из Message.file.name.
        file_name = ((replied_msg.file.name if replied_msg.file else '') or '').strip()
        if not file_name.lower().endswith(ALLOWED_EXTENSIONS):
            await engine.answer(
                event,
                visual.err('Файл должен иметь расширение .py или .txt'),
            )
            return

        await engine.answer(event, visual.wait('Анализирую файл…'))

        path = await replied_msg.download_media()
        try:
            source = Path(path).read_text(encoding='utf-8')
        finally:
            # временная копия на диске (data/... от download_media)
            # раньше никогда не удалялась — чистим за собой.
            try:
                if path and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass

        meta = _parse_module_meta(source)
        if meta is None:
            await engine.answer(
                event,
                visual.err('Файл содержит синтаксическую ошибку или не задан MODULE_NAME'),
            )
            return

        # модуль с таким MODULE_NAME уже загружен — раньше это было
        # безусловной ошибкой. Теперь смотрим на версию: та же версия — по-прежнему
        # ошибка (действительно уже загружен, грузить нечего), а другая версия —
        # это ОБНОВЛЕНИЕ существующего модуля, а не повторная загрузка (см.
        # engine._load_from_source(), core/engine.py — там же и происходит
        # фактическая замена), так что просто продолжаем с другим текстом превью.
        existing = engine.modules.get(meta['module_name'])
        if existing is not None and existing.version == meta['version']:
            await engine.answer(
                event,
                visual.warn('Модуль уже загружен') + "\n"
                + visual.field('MODULE_NAME', meta['module_name']) + "\n"
                + visual.field('версия', existing.version) + "  " + visual.hint('та же самая'),
            )
            return

        is_update = existing is not None
        preview_text = _build_preview_text(meta, file_name, existing=existing)

        try:
            form = await engine.create_inlineform(
                chat_id=event.chat_id,
                text=preview_text,
                buttons=[
                    [(visual.btn("Обновить" if is_update else "Загрузить"), "loader_confirm")],
                    [(visual.btn('Отмена', 'close'), "loader_cancel")],
                ],
            )
        except Exception:
            # Стационарный бот не подключён — инлайн-подтверждение недоступно,
            # грузим/обновляем сразу, как это работало до await _do_load_now(event, source, is_update=is_update)
            return

        @form.on_click(data="loader_confirm")
        async def _confirm(click):
            await click.answer("Обновляю…" if is_update else "Загружаю…")
            try:
                info = await engine._load_from_source(source, target='loaded')
                verb = "обновлён" if is_update else "загружен"
                text = "\n".join([
                    visual.ok(f"Модуль {verb}"),
                    visual.field('название', info.name, code=False),
                    visual.field('id', info.module_name),
                    visual.field('версия', info.version),
                    visual.field('автор', info.author, code=False),
                ])
            except Exception as e:
                text = visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(e))}</code>"
                engine.tlog(f"Ошибка загрузки модуля: {e}")
            await form.edit(text, buttons=None)

        @form.on_click(data="loader_cancel")
        async def _cancel(click):
            await click.answer("Отменено")
            await form.edit(visual.warn('Загрузка отменена'), buttons=None)

        await engine.answer(
            event,
            visual.ok('Превью модуля') + "  " + visual.hint('проверьте информацию и подтвердите ниже'),
        )

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка загрузки модуля: {e}")


@decorators.command('unloadmod', 'ulm')
async def unloadmod_cmd(event):
    """выгружает модуль по его ID или отображаемому имени (файл уезжает в user/modules/unloaded, вернуть — .restoremods)"""
    args = engine.get_args()

    if not args:
        await engine.answer(
            event,
            visual.err('Укажите ID модуля для выгрузки') + "\n" + visual.field('пример', '.unloadmod ping'),
        )
        return

    query = args.strip()

    try:
        module_name = engine.resolve_module(query)

        if module_name is None:
            similar = engine.search_module(query)
            if similar:
                await engine.answer(
                    event,
                    visual.warn('Модуль не найден') + "\n"
                    + visual.field('возможно, вы имели в виду', html.escape(str(similar))),
                )
            else:
                await engine.answer(
                    event,
                    visual.err('Модуль не найден') + "\n" + visual.field('запрос', html.escape(query)),
                )
            return

        module_info = engine.modules.get(module_name)
        is_builtin = module_info is not None and module_info.is_builtin
        if is_builtin:
            await engine.answer(
                event,
                visual.warn('Нельзя выгрузить системный модуль') + "\n" + visual.field('модуль', module_name),
            )
            return

        await engine.unload_module(module_name)

        await engine.answer(
            event,
            visual.ok('Модуль выгружен') + "\n"
            + visual.field('модуль', module_name) + "\n"
            + visual.field('файл', 'user/modules/unloaded') + "\n"
            + visual.hint('вернуть — <code>.restoremods</code>'),
        )

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при выгрузке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка выгрузки модуля: {e}")


@decorators.command('reloadmod', 'rm')
async def reload_module_cmd(event):
    """перезагружает модуль из файла по ID или названию"""
    args = engine.get_args()

    if not args:
        await engine.answer(
            event,
            visual.err('Укажите ID модуля для перезагрузки') + "\n" + visual.field('пример', '.reloadmod ping'),
        )
        return

    query = args.strip()

    try:
        module_name = engine.resolve_module(query)

        if module_name is None:
            similar = engine.search_module(query)
            if similar:
                await engine.answer(
                    event,
                    visual.warn('Модуль не найден') + "\n"
                    + visual.field('возможно, вы имели в виду', html.escape(str(similar))),
                )
            else:
                await engine.answer(
                    event,
                    visual.err('Модуль не найден') + "\n" + visual.field('запрос', html.escape(query)),
                )
            return

        await engine.reload_module(module_name)

        await engine.answer(
            event,
            visual.ok('Модуль перезагружен') + "\n" + visual.field('модуль', module_name),
        )

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при перезагрузке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка перезагрузки модуля: {e}")


@decorators.command('sendmodule', 'sm')
async def sendmodule_cmd(event):
    """присылает файл модуля (по ID или названию) сюда, в чат — удобно переслать или сохранить; системные модули тоже"""
    args = engine.get_args()
    if not args:
        await engine.answer(
            event,
            visual.err('Укажите ID или название модуля') + "\n" + visual.field('пример', '.sendmodule ping'),
        )
        return

    query = args.strip()
    try:
        module_name = engine.resolve_module(query)
        if module_name is None:
            similar = engine.search_module(query)
            if similar:
                await engine.answer(
                    event,
                    visual.warn('Модуль не найден') + "\n"
                    + visual.field('возможно, вы имели в виду', html.escape(str(similar))),
                )
            else:
                await engine.answer(
                    event,
                    visual.err('Модуль не найден') + "\n" + visual.field('запрос', html.escape(query)),
                )
            return

        info = engine.modules[module_name]
        path = info.path
        if not path or not os.path.isfile(path):
            await engine.answer(
                event,
                visual.err('Файл модуля не найден на диске') + "\n" + visual.field('модуль', module_name),
            )
            return

        caption = "\n".join([
            visual.title(info.name),
            visual.field('id', module_name),
            visual.field('версия', info.version),
            visual.field('автор', info.author, code=False),
        ])
        if info.description:
            caption += "\n" + visual.item(f"описание  {info.description}")
        caption += "\n\n" + visual.hint('загрузить — ответьте на файл командой <code>.loadmod</code>')
        # подпись к файлу в Telegram — до 1024 символов
        if len(caption) > 1000:
            caption = caption[:997] + '…'

        await engine.client.send_file(event.chat_id, path, caption=caption, parse_mode='html',
                                      force_document=True, reply_to=getattr(event, 'id', None))
        try:
            await event.delete()
        except Exception:
            pass

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при отправке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка отправки модуля: {e}")


# ------------------------------------------------------------------ #
# .restoremods — выгруженные модули
# ------------------------------------------------------------------ #

RESTORE_PAGE_SIZE = 7
RESTORE_TEXT_LIMIT = 3800


def _unloaded_flags(e):
    if e['broken']:
        return f"  {visual.visual('status_error')} <i>повреждён</i>"
    if e['loaded']:
        return f"  {visual.visual('status_warning')} <i>такой модуль уже загружен</i>"
    return ""


def _unloaded_list_text(entries):
    if not entries:
        return (
            visual.title('Выгруженные модули') + "\n"
            + visual.hint('выгруженных модулей нет — сюда попадают модули после <code>.unloadmod</code> '
                          '(папка <code>user/modules/unloaded</code>)')
        )
    lines = [visual.title('Выгруженные модули', f"всего: {len(entries)}")]
    used = len(lines[0])
    hidden = 0
    for e in entries:
        line = (
            visual.item(f"<b>{e['name']}</b>  <code>{e['module_name']}</code> · <code>{e['version']}</code>{_unloaded_flags(e)}")
            + "\n" + visual.item(f"<code>{e['file']}</code>", 1)
        )
        if hidden or used + len(line) + 1 > RESTORE_TEXT_LIMIT:
            hidden += 1
            continue
        lines.append(line)
        used += len(line) + 1
    if hidden:
        lines += ["", visual.hint(f"… и ещё {hidden} — см. «Выбрать модуль»")]
    return "\n".join(lines)


def _restore_menu_buttons(entries):
    return [[(visual.btn('Выбрать модуль'), "rs_pick:0")]] if entries else []


def _restore_picker(entries, page):
    items = [(visual.btn(e['name']), f"rs_open:{e['file']}") for e in entries]
    rows, page, total = engine.picker_page(items, page, per_page=RESTORE_PAGE_SIZE, page_callback="rs_page:")
    rows.append([(visual.btn('К списку выгруженных', 'back'), "rs_menu")])
    text = visual.title('Восстановление', f"выберите модуль · страница {page + 1}/{total}")
    return text, rows, page


def _restore_card_text(e):
    cmds = e.get('commands') or []
    if cmds:
        cmd_str = visual.item(", ".join(f"<code>.{names[0]}</code>" for names in cmds))
    else:
        cmd_str = visual.hint('нет команд')
    return "\n".join([
        visual.title(e['name'], e['description'] or None),
        visual.field('id', e['module_name']),
        visual.field('версия', e['version']),
        visual.field('автор', e['author'], code=False),
        visual.field('файл', f"user/modules/unloaded/{e['file']}") + _unloaded_flags(e),
        "",
        visual.section(f"Команды · {len(cmds)}"),
        cmd_str,
    ])


def _restore_card_buttons(e):
    rows = [[(visual.btn('К выбору модуля', 'back'), "rs_back")]]
    if not e['broken'] and not e['loaded']:
        rows.insert(0, [(visual.btn('Восстановить'), f"rs_do:{e['file']}")])
    rows.append([(visual.btn('Удалить навсегда', 'close'), f"rs_del_ask:{e['file']}")])
    return rows


def _bind_restore_form(form, focus=None):
    """Обработчики клавиатуры .restoremods — по аналогии с .help (список -> выбор -> карточка)."""
    entries = engine.list_unloaded_modules()
    files = [e['file'] for e in entries]
    state = {'page': files.index(focus) // RESTORE_PAGE_SIZE if focus in files else 0}

    async def _show_picker(page):
        current = engine.list_unloaded_modules()
        text, buttons, state['page'] = _restore_picker(current, page)
        await form.edit(text, buttons=buttons)

    async def _show_menu():
        current = engine.list_unloaded_modules()
        await form.edit(_unloaded_list_text(current), buttons=_restore_menu_buttons(current))

    @form.on_click(data_startswith="rs_pick:")
    async def _pick(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data_startswith="rs_page:")
    async def _page(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data="rs_menu")
    async def _menu(click):
        await click.answer()
        await _show_menu()

    @form.on_click(data="rs_back")
    async def _back(click):
        await click.answer()
        await _show_picker(state['page'])

    @form.on_click(data_startswith="rs_open:")
    async def _open(click):
        await click.answer()
        e = engine.find_unloaded_module(click.data.split(":", 1)[1])
        if not e:
            await form.edit(visual.err('Файл уже не найден в unloaded/'),
                            buttons=[[(visual.btn('К выбору модуля', 'back'), "rs_back")]])
            return
        state['page'] = [x['file'] for x in engine.list_unloaded_modules()].index(e['file']) // RESTORE_PAGE_SIZE
        await form.edit(_restore_card_text(e), buttons=_restore_card_buttons(e))

    @form.on_click(data_startswith="rs_do:")
    async def _do(click):
        await click.answer("Восстанавливаю…")
        query = click.data.split(":", 1)[1]
        try:
            info = await engine.restore_module(query)
            text = "\n".join([
                visual.ok('Модуль восстановлен'),
                visual.field('название', info.name, code=False),
                visual.field('id', info.module_name),
                visual.field('версия', info.version),
            ])
        except Exception as e:
            text = visual.err('Не удалось восстановить') + f"\n<code>{html.escape(str(e))}</code>"
            engine.tlog(f"Ошибка восстановления модуля {query}: {e}")
        await form.edit(text, buttons=[[(visual.btn('К списку выгруженных', 'back'), "rs_menu")]])

    @form.on_click(data_startswith="rs_del_ask:")
    async def _del_ask(click):
        await click.answer()
        file = click.data.split(":", 1)[1]
        await form.edit(
            visual.warn(f"Удалить файл <code>{html.escape(file)}</code> навсегда?") + "\n" + visual.hint('это необратимо'),
            buttons=[[(visual.btn('Да, удалить'), f"rs_del_confirm:{file}")], [(visual.btn('Отмена', 'close'), f"rs_open:{file}")]],
        )

    @form.on_click(data_startswith="rs_del_confirm:")
    async def _del_confirm(click):
        await click.answer()
        file = click.data.split(":", 1)[1]
        try:
            await engine.delete_unloaded_module(file)
            text = visual.ok('Файл удалён') + "\n" + visual.field('файл', html.escape(file))
        except Exception as e:
            text = visual.err('Ошибка удаления') + f"\n<code>{html.escape(str(e))}</code>"
        await form.edit(text, buttons=[[(visual.btn('К списку выгруженных', 'back'), "rs_menu")]])


@decorators.command('restoremods', 'rsm')
async def restoremods_cmd(event):
    """список выгруженных (.unloadmod) модулей из user/modules/unloaded с восстановлением; с именем — сразу карточка модуля"""
    args = engine.get_args().strip()
    try:
        entries = engine.list_unloaded_modules()
        if not entries:
            await engine.answer(event, _unloaded_list_text(entries))
            return

        focus = None
        text = _unloaded_list_text(entries)
        buttons = _restore_menu_buttons(entries)
        if args:
            found = engine.find_unloaded_module(args)
            if found is None:
                await engine.answer(
                    event,
                    visual.err('Среди выгруженных нет такого модуля') + "\n"
                    + visual.field('запрос', html.escape(args)) + "\n"
                    + visual.hint('список — <code>.restoremods</code>'),
                )
                return
            focus = found['file']
            text, buttons = _restore_card_text(found), _restore_card_buttons(found)

        try:
            form = await engine.create_inlineform(chat_id=event.chat_id, text=text, buttons=buttons)
        except Exception:
            # Стационарный бот недоступен — без кнопок: с именем восстанавливаем сразу, без — просто список
            if not args:
                await engine.answer(event, text + "\n\n" + visual.hint('восстановить — <code>.restoremods имя</code>'))
                return
            info = await engine.restore_module(args)
            await engine.answer(
                event,
                visual.ok('Модуль восстановлен') + "\n" + visual.field('id', info.module_name),
            )
            return

        _bind_restore_form(form, focus=focus)
        await engine.answer(event, visual.ok('Выгруженные модули') + "  " + visual.hint('ниже'))

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка .restoremods') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка restoremods: {e}")
