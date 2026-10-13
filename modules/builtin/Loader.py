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

Каталог модулей (репозитории на GitHub, список — настройка repos, по умолчанию github.com/Shxdowrate/FRX-Modules):
    .dlm <имя>             найти модуль во всех репозиториях и установить (то же превью с подтверждением, что у .lm);
                           имя — имя файла (weather или weather.py), MODULE_NAME или NAME модуля
    .dlm <ссылка>          установить по прямой ссылке: github.com/…/blob/…/x.py, raw.githubusercontent.com/…, любой .py
    .dlm upd               проверить обновления установленных модулей и обновить кнопкой
    .modlist / .ml [поиск] каталог: репозитории → модули репозитория → карточка → «Установить»; кнопка «Поиск»
                           ищет по названию во всех репозиториях (на первом экране) или в открытом;
                           `.modlist имя` — сразу результаты поиска, `.modlist refresh` — перечитать репозитории
    .repo                  репозитории: .repo add <ссылка> · .repo del <№|ссылка> · .repo reset
Репозиторий — это просто .py-файлы (модуль ищется по имени файла, вложенные папки тоже смотрятся). Если в корне
лежит index.json — берётся из него: [{"file": "weather.py", "name": "…", "version": "…", "description": "…"}].
Приватный репозиторий — нужен токен GitHub: .cfg loader github_token.

Расширение файла проверяется без учёта регистра, временный файл анализа удаляется после превью.
Логика списков и восстановления — engine.list_unloaded_modules() / restore_module() / delete_unloaded_module().
"""

import asyncio
import html
import os
import re
import time
from pathlib import Path

import aiohttp

import engine
import decorators
import visual

NAME = "Loader"
MODULE_NAME = "loader"
VERSION = "2.9"
AUTHOR = "FRX"
DESCRIPTION = "Управление модулями: загрузка/обновление (.py/.txt, с превью и подтверждением), каталог модулей с GitHub (.dlm, .modlist, .repo), выгрузка, восстановление, перезагрузка и отправка файла модуля"

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
    ] + _requirements_lines(meta) + [
        "",
        visual.hint(question),
    ])


def _requirements_lines(meta):
    """Пакеты из REQUIREMENTS модуля: что уже есть и что поставится при загрузке (core/requirements.py)."""
    reqs = meta.get('requirements') or []
    if not reqs:
        return []
    try:
        from core.requirements import missing, normalize
        need = {spec for spec, _names in missing(normalize(reqs))}
    except Exception:
        need = set()
    lines = ["", visual.section(f"Пакеты · {len(reqs)}")]
    for spec in reqs:
        lines.append(visual.item(f"<code>{html.escape(spec)}</code> — " + ("поставится при загрузке" if spec in need else "есть")))
    return lines


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


async def _offer_source(event, source: str, file_name: str, origin: str = None):
    """Превью модуля из исходника и загрузка после «Загрузить» (общая часть .loadmod и .dlm)."""
    meta = _parse_module_meta(source)
    if meta is None:
        await engine.answer(event, visual.err('Файл содержит синтаксическую ошибку или не задан MODULE_NAME'))
        return
    existing = engine.modules.get(meta['module_name'])
    if existing is not None and existing.version == meta['version']:
        await engine.answer(event, visual.warn('Модуль уже загружен') + "\n" + visual.field('MODULE_NAME', meta['module_name'])
                            + "\n" + visual.field('версия', existing.version) + "  " + visual.hint('та же самая'))
        return
    if existing is not None and existing.is_builtin:
        await engine.answer(event, visual.err('Это встроенный модуль — он обновляется вместе с FRX (.update)'))
        return
    is_update = existing is not None
    preview_text = _build_preview_text(meta, file_name, existing=existing)
    if origin:
        preview_text += "\n" + visual.hint(f"источник: {html.escape(origin)}")
    try:
        form = await engine.create_inlineform(
            chat_id=event.chat_id, text=preview_text,
            buttons=[[(visual.btn("Обновить" if is_update else "Загрузить"), "loader_confirm", "success")],
                     [(visual.btn('Отмена', 'close'), "loader_cancel")]])
    except Exception:
        await _do_load_now(event, source, is_update=is_update)
        return

    @form.on_click(data="loader_confirm")
    async def _confirm(click):
        await click.answer("Обновляю…" if is_update else "Загружаю…")
        try:
            info = await engine._load_from_source(source, target='loaded')
            text = "\n".join([visual.ok(f"Модуль {'обновлён' if is_update else 'загружен'}"),
                              visual.field('название', info.name, code=False), visual.field('id', info.module_name),
                              visual.field('версия', info.version), visual.field('автор', info.author, code=False)])
        except Exception as e:
            text = visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(e))}</code>"
            engine.tlog(f"Ошибка загрузки модуля: {e}")
        await form.edit(text, buttons=None)

    @form.on_click(data="loader_cancel")
    async def _cancel(click):
        await click.answer("Отменено")
        await form.edit(visual.warn('Загрузка отменена'), buttons=None)

    try:
        await event.delete()
    except Exception:
        pass


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

        await _offer_source(event, source, file_name)

    except Exception as e:
        await engine.answer(
            event,
            visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(e))}</code>",
        )
        engine.tlog(f"Ошибка загрузки модуля: {e}")


NEWMOD_TEMPLATE = '''"""
{title} — модуль FRX.
Создан командой .newmod: поправьте код в FRX WebIDE (сайт .web → webide) или в любом редакторе
(user/modules/{name}.py) и перезагрузите — .rm {name}. Справка по API — README.md.
"""

import decorators
import engine
import visual

NAME = "{title}"
MODULE_NAME = "{name}"
VERSION = "1.0"
AUTHOR = "{author}"
DESCRIPTION = "Мой модуль: команда .{name}"


@decorators.on_start()
async def setup():
    # настройки модуля — меняются в .config и на сайте
    await engine.create_config(key="greeting", description="Что отвечает .{name}", data_type="str", default="Привет!")


@decorators.command("{name}")
async def {name}_cmd(event):
    """пример команды: ответ и форма с цветными кнопками"""
    greeting = await engine.get_config("greeting")
    try:
        form = await engine.create_inlineform(
            event.chat_id, visual.ok(engine.safe_html(greeting)) + "\\n" + visual.hint("нажмите кнопку"),
            buttons=[[("Да", "yes", "success"), ("Нет", "no", "danger")]],
        )
    except Exception:                       # нет стационарного бота — просто текст
        await engine.answer(event, visual.ok(engine.safe_html(greeting)))
        return

    @form.on_click()
    async def _(click):
        await click.answer("Вы выбрали: " + ("да" if click.data == "yes" else "нет"))
        await form.edit(visual.ok("Готово"), buttons=None)
'''


@decorators.command('newmod', 'nm')
async def newmod_cmd(event):
    """создать заготовку модуля: .newmod имя — файл в user/modules, сразу загружен, команда .имя работает"""
    import re
    name = engine.get_args().strip().lower()
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,30}', name or ''):
        await engine.answer(event, visual.err('Укажите имя модуля') + "\n"
                            + visual.hint('латиница, цифры и _, начинается с буквы: <code>.newmod weather</code>'))
        return
    if name in engine.modules:
        await engine.answer(event, visual.err('Модуль с таким MODULE_NAME уже есть') + "\n" + visual.field('MODULE_NAME', name))
        return
    if any(name in (getattr(i, 'commands', None) or {}) for i in engine.modules.values()):
        await engine.answer(event, visual.err(f'Команда .{name} уже занята другим модулем'))
        return
    try:
        me = await engine.client.get_me()
        author = (me.first_name or me.username or 'me').replace('"', "'")
    except Exception:
        author = 'me'
    title = name.replace('_', ' ').title()
    source = NEWMOD_TEMPLATE.format(name=name, title=title, author=author)
    try:
        info = await engine._load_from_source(source, target='loaded')
    except Exception as e:
        await engine.answer(event, visual.err('Не удалось создать модуль') + f"\n<code>{html.escape(str(e))}</code>")
        return
    prefix = await engine.get_prefix()
    lines = [visual.ok('Модуль создан и загружен'),
             visual.field('id', info.module_name), visual.field('команда', f'{prefix}{name}'),
             visual.field('файл', f'user/modules/{name}.py'), '']
    web = getattr(engine, 'web', None)
    url = web.public_url() if web is not None and web.running else None
    if url:
        lines.append(visual.item(f'редактировать: <a href="{html.escape(url)}/#/webide/{name}">FRX WebIDE</a>'))
    else:
        lines.append(visual.item('редактировать — на сайте: <code>.web</code> → webide, или в любом редакторе'))
    lines.append(visual.hint(f'после правок файла — {prefix}rm {name}'))
    await engine.answer(event, "\n".join(lines))


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
    return [[(visual.btn('Выбрать модуль'), "rs_pick:0", "primary")]] if entries else []


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
        rows.insert(0, [(visual.btn('Восстановить'), f"rs_do:{e['file']}", "success")])
    rows.append([(visual.btn('Удалить навсегда', 'close'), f"rs_del_ask:{e['file']}", "danger")])
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
            buttons=[[(visual.btn('Да, удалить'), f"rs_del_confirm:{file}", "danger")], [(visual.btn('Отмена', 'close'), f"rs_open:{file}")]],
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


# ------------------------------------------------------------------ #
# каталог модулей: репозитории на GitHub (.dlm, .modlist, .repo)
# ------------------------------------------------------------------ #

DEFAULT_REPOS = ['https://github.com/Shxdowrate/FRX-Modules']
CATALOG_TTL = 600
_CATALOG = {}        # url репозитория -> (время, [запись])
_SOURCES = {}        # raw-ссылка -> (время, исходник)


@decorators.on_start()
async def _loader_setup():
    await engine.create_config(key='repos', description='Репозитории модулей (.dlm, .modlist): ссылки на GitHub', data_type='list',
                               default=list(DEFAULT_REPOS))
    await engine.create_config(key='github_token', description='Токен GitHub — только для приватных репозиториев (и выше лимит запросов)',
                               data_type='str', default='', valid={'max_len': 200})


async def _cfg(key, default=None):
    try:
        v = await engine.get_config(key, module_name=MODULE_NAME)
        return default if v is None else v
    except KeyError:
        return default


def _gh(url):
    """https://github.com/owner/repo[/tree/branch/path] → (owner, repo, branch|None, path) | None."""
    m = re.match(r'^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/tree/([^/]+)(?:/(.*))?)?/?$', url.strip())
    return (m.group(1), m.group(2), m.group(3), (m.group(4) or '').strip('/')) if m else None


async def _http(url, as_json=False, api=False):
    headers = {'User-Agent': 'FRX-Loader'}
    token = (await _cfg('github_token', '') or '').strip()
    if token and ('github.com' in url or 'githubusercontent.com' in url):
        headers['Authorization'] = f'token {token}'
    if api:
        headers['Accept'] = 'application/vnd.github+json'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20), headers=headers, trust_env=True) as s:
        async with s.get(url) as r:
            if r.status == 404:
                return None
            if r.status in (401, 403) and api:
                left = r.headers.get('X-RateLimit-Remaining')
                raise RuntimeError("лимит запросов GitHub исчерпан — подождите час или задайте .cfg loader github_token"
                                   if left == '0' else "нет доступа (приватный репозиторий? нужен .cfg loader github_token)")
            if r.status >= 400:
                raise RuntimeError(f"ответ {r.status}")
            return await r.json(content_type=None) if as_json else await r.text(encoding='utf-8', errors='replace')


async def _http_bytes(url, limit=40 * 1024 * 1024):
    headers = {'User-Agent': 'FRX-Loader'}
    token = (await _cfg('github_token', '') or '').strip()
    if token and ('github.com' in url or 'githubusercontent.com' in url):
        headers['Authorization'] = f'token {token}'
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60), headers=headers, trust_env=True) as s:
        async with s.get(url) as r:
            if r.status == 404:
                return None
            if r.status >= 400:
                raise RuntimeError(f"ответ {r.status}" + (" — приватный репозиторий? нужен .cfg loader github_token"
                                                          if r.status in (401, 403) else ""))
            data = bytearray()
            async for chunk in r.content.iter_chunked(1 << 16):
                data += chunk
                if len(data) > limit:
                    raise RuntimeError("репозиторий слишком большой для каталога (больше 40 МБ)")
            return bytes(data)


def _zip_entries(blob, url, sub, raw_base):
    """Модули из zip-архива репозитория: .py-файлы (и index.json, если есть). Исходники сразу кладутся в кэш."""
    import io
    import json
    import zipfile
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    root = names[0].split('/', 1)[0] + '/' if names else ''
    files = {}
    for n in names:
        if n.endswith('/') or not n.startswith(root):
            continue
        rel = n[len(root):]
        if sub and not rel.startswith(sub + '/'):
            continue
        files[rel] = n
    now = time.time()
    index_rel = (sub + '/' if sub else '') + 'index.json'
    index = None
    if index_rel in files:
        try:
            index = json.loads(z.read(files[index_rel]).decode('utf-8'))
            if isinstance(index, dict):
                index = index.get('modules') or [dict(v, file=k) if isinstance(v, dict) else {'file': k} for k, v in index.items()]
        except Exception:
            index = None
    out = []
    if isinstance(index, list) and index:
        items = [(it.get('file') or it.get('path'), it) for it in index]
    else:
        items = [(rel[len(sub) + 1:] if sub else rel, {}) for rel in files if rel.lower().endswith('.py')]
    for f, it in items:
        if not f:
            continue
        rel = (sub + '/' if sub else '') + f
        if rel not in files:
            continue
        name = rel.rsplit('/', 1)[-1]
        if not it and (name.startswith(('_', '.')) or any(part.startswith('.') for part in rel.split('/'))):
            continue
        raw = raw_base + rel
        try:
            _SOURCES[raw] = (now, z.read(files[rel]).decode('utf-8', errors='replace'))
        except Exception:
            continue
        out.append({'file': name, 'path': rel, 'raw': raw, 'name': it.get('name') or _stem(name),
                    'module_name': it.get('module_name') or '', 'version': str(it.get('version') or ''),
                    'description': it.get('description') or '', 'repo': url})
    return out


def _stem(path):
    return path.rsplit('/', 1)[-1][:-3] if path.lower().endswith('.py') else path.rsplit('/', 1)[-1]


async def _repo_list(url, force=False):
    """[{'file', 'path', 'raw', 'name', 'module_name', 'version', 'description', 'repo'}] — модули репозитория."""
    hit = _CATALOG.get(url)
    if hit and not force and time.time() - hit[0] < CATALOG_TTL:
        return hit[1]
    gh = _gh(url)
    out = []
    if gh:
        owner, repo, branch, sub = gh
        ref = branch or 'HEAD'
        # 1) весь репозиторий одним архивом: без API GitHub и его лимита 60 запросов в час
        try:
            token = (await _cfg('github_token', '') or '').strip()
            zurl = (f'https://api.github.com/repos/{owner}/{repo}/zipball/{branch or ""}'.rstrip('/') if token
                    else f'https://github.com/{owner}/{repo}/archive/{"refs/heads/" + branch if branch else "HEAD"}.zip')
            blob = await _http_bytes(zurl)
            if blob is None:
                raise RuntimeError("репозиторий не найден (или он приватный — нужен .cfg loader github_token)")
            out = _zip_entries(blob, url, sub, f'https://raw.githubusercontent.com/{owner}/{repo}/{ref}/')
            out.sort(key=lambda e: e['name'].lower())
            _CATALOG[url] = (time.time(), out)
            return out
        except RuntimeError:
            raise
        except Exception as e:
            engine.tlog(f"[loader] архив {owner}/{repo}: {e} — пробую через API")
        # 2) запасной путь — API GitHub
        if not branch:
            info = await _http(f'https://api.github.com/repos/{owner}/{repo}', as_json=True, api=True)
            if info is None:
                raise RuntimeError("репозиторий не найден (или он приватный — нужен github_token)")
            branch = info.get('default_branch') or 'main'
        raw_base = f'https://raw.githubusercontent.com/{owner}/{repo}/{branch}/'
        index = None
        try:
            index = await _http(raw_base + (sub + '/' if sub else '') + 'index.json', as_json=True)
        except Exception:
            index = None
        if isinstance(index, dict):
            index = index.get('modules') or [dict(v, file=k) if isinstance(v, dict) else {'file': k} for k, v in index.items()]
        if isinstance(index, list) and index:
            for it in index:
                f = it.get('file') or it.get('path')
                if not f:
                    continue
                path = (sub + '/' if sub else '') + f
                out.append({'file': f.rsplit('/', 1)[-1], 'path': path, 'raw': raw_base + path, 'name': it.get('name') or _stem(f),
                            'module_name': it.get('module_name') or '', 'version': str(it.get('version') or ''),
                            'description': it.get('description') or '', 'repo': url})
        else:
            tree = await _http(f'https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1', as_json=True, api=True)
            for node in (tree or {}).get('tree') or []:
                path = node.get('path', '')
                if node.get('type') != 'blob' or not path.lower().endswith('.py'):
                    continue
                if sub and not path.startswith(sub + '/'):
                    continue
                name = path.rsplit('/', 1)[-1]
                if name.startswith(('_', '.')) or any(part.startswith('.') for part in path.split('/')):
                    continue
                out.append({'file': name, 'path': path, 'raw': raw_base + path, 'name': _stem(name), 'module_name': '',
                            'version': '', 'description': '', 'repo': url})
    else:                                               # не GitHub: база с index.json или full.txt (как у Hikka)
        base = url.rstrip('/') + '/'
        index = await _http(base + 'index.json', as_json=True)
        if isinstance(index, list):
            for it in index:
                f = it.get('file')
                if f:
                    out.append({'file': f, 'path': f, 'raw': base + f, 'name': it.get('name') or _stem(f),
                                'module_name': it.get('module_name') or '', 'version': str(it.get('version') or ''),
                                'description': it.get('description') or '', 'repo': url})
        else:
            full = await _http(base + 'full.txt') or ''
            for ln in full.split():
                f = ln if ln.endswith('.py') else ln + '.py'
                out.append({'file': f, 'path': f, 'raw': base + f, 'name': _stem(f), 'module_name': '', 'version': '',
                            'description': '', 'repo': url})
    out.sort(key=lambda e: e['name'].lower())
    _CATALOG[url] = (time.time(), out)
    return out


async def _catalog(force=False):
    """Все модули из всех репозиториев + ошибки по репозиториям."""
    entries, errors = [], {}
    for url in await _cfg('repos', list(DEFAULT_REPOS)) or []:
        try:
            entries += await _repo_list(url, force)
        except Exception as e:
            errors[url] = str(e)
    return entries, errors


async def _source(entry_or_url):
    url = entry_or_url['raw'] if isinstance(entry_or_url, dict) else entry_or_url
    hit = _SOURCES.get(url)
    if hit and time.time() - hit[0] < CATALOG_TTL:
        return hit[1]
    src = await _http(url)
    if src is None:
        raise RuntimeError("файл не найден")
    _SOURCES[url] = (time.time(), src)
    return src


def _direct_raw(url):
    """Ссылка на файл на GitHub → raw-ссылка; другие http(s) .py — как есть."""
    m = re.match(r'^(?:https?://)?(?:www\.)?github\.com/([\w.-]+)/([\w.-]+)/(?:blob|raw)/(.+\.py)$', url.strip())
    if m:
        return f'https://raw.githubusercontent.com/{m.group(1)}/{m.group(2)}/{m.group(3)}'
    if re.match(r'^https?://\S+\.(py|txt)(\?.*)?$', url.strip()):
        return url.strip()
    return None


def _norm(s):
    return re.sub(r'[\s_\-.]', '', (s or '').lower())


async def _find(query, entries):
    """Подходящие записи: имя файла → MODULE_NAME/NAME из индекса → по исходникам (если среди файлов нет)."""
    q = _norm(query[:-3] if query.lower().endswith('.py') else query)
    exact = [e for e in entries if _norm(e['name']) == q or _norm(_stem(e['file'])) == q or _norm(e['module_name']) == q]
    if exact:
        return exact
    part = [e for e in entries if q in _norm(e['name']) or q in _norm(_stem(e['file']))]
    return part


def _vkey(v):
    return tuple(int(x) if x.isdigit() else 0 for x in re.findall(r'\d+', str(v or '0'))) or (0,)


@decorators.command('dlm', 'dlmod')
async def dlm_cmd(event):
    """установить модуль из репозиториев: <имя> или <ссылка на .py>; upd — проверить обновления"""
    arg = engine.get_args().strip()
    if not arg:
        await engine.answer(event, visual.err('Какой модуль? .dlm weather · .dlm <ссылка> · .dlm upd') + "\n"
                            + visual.hint('весь каталог — .modlist'))
        return
    if arg.lower() in ('upd', 'update', 'обновить'):
        await _updates(event)
        return
    raw = _direct_raw(arg)
    try:
        if raw:
            await engine.answer(event, visual.wait('Скачиваю…'))
            src = await _source(raw)
            await _offer_source(event, src, raw.rsplit('/', 1)[-1].split('?')[0], origin=raw)
            return
        await engine.answer(event, visual.wait('Ищу в репозиториях…'))
        entries, errors = await _catalog()
        found = await _find(arg, entries)
        if not found:
            msg = visual.err(f"Не нашёл «{html.escape(arg)}» в репозиториях")
            if errors:
                msg += "\n" + "\n".join(visual.warn(f"{html.escape(u)}: {html.escape(e)}") for u, e in errors.items())
            await engine.answer(event, msg + "\n" + visual.hint('все модули — .modlist · репозитории — .repo'))
            return
        if len(found) > 1:
            await _pick_list(event, found, f"Нашлось {len(found)}: «{html.escape(arg)}»")
            return
        e = found[0]
        src = await _source(e)
        await _offer_source(event, src, e['file'], origin=_repo_label(e['repo']) + '/' + e['path'])
    except Exception as e:
        await engine.answer(event, visual.err('Не вышло') + f"\n<code>{html.escape(str(e))}</code>")


def _repo_label(url):
    gh = _gh(url)
    return f"{gh[0]}/{gh[1]}" if gh else url


async def _repos_entries(force=False):
    """[(url, [записи] | None, ошибка | None)] — по каждому репозиторию из настроек."""
    out = []
    for url in await _cfg('repos', list(DEFAULT_REPOS)) or []:
        try:
            out.append((url, await _repo_list(url, force), None))
        except Exception as e:
            out.append((url, None, str(e)))
    return out


def _search(query, entries):
    """Поиск по названию: имя файла, NAME из индекса, MODULE_NAME — частичное совпадение без учёта регистра и «_-.»."""
    q = _norm(query[:-3] if query.lower().endswith('.py') else query)
    if not q:
        return list(entries)
    exact = [e for e in entries if q in (_norm(e['name']), _norm(_stem(e['file'])), _norm(e['module_name']))]
    part = [e for e in entries if e not in exact and (q in _norm(e['name']) or q in _norm(_stem(e['file']))
                                                    or (e['module_name'] and q in _norm(e['module_name'])))]
    return exact + part


async def _catalog_form(event, repos, query='', scope=None, preset=None):
    """
    Каталог одной формой: репозитории → модули репозитория → карточка → «Установить».
    Поиск по названию — кнопкой ввода: на экране репозиториев ищет по всем, в репозитории — только в нём.
    repos — из _repos_entries(); scope — url репозитория (сразу открыть его) или None;
    preset — (записи, заголовок): готовый список (например, несколько совпадений у .dlm).
    """
    by_url = {u: (ents or []) for u, ents, _err in repos}
    errors = {u: err for u, _ents, err in repos if err}
    st = {'view': 'repos', 'scope': scope, 'query': query, 'page': 0, 'list': [], 'card': None, 'preset': preset}

    def all_entries():
        return [e for _u, ents, _err in repos for e in (ents or [])]

    def current_list():
        if st['preset'] is not None:
            return list(st['preset'][0])
        base = by_url.get(st['scope'], []) if st['scope'] else all_entries()
        return _search(st['query'], base) if st['query'] else list(base)

    def where():
        return _repo_label(st['scope']) if st['scope'] else 'все репозитории'

    def view_repos():
        total = sum(len(v) for v in by_url.values())
        lines = [visual.title('Каталог модулей', f"{len(by_url)} репоз. · {total} модулей"),
                 visual.hint('выберите репозиторий или найдите модуль по названию во всех сразу')]
        rows = []
        for i, (url, ents, err) in enumerate(repos):
            rows.append([(f"⚠ {_repo_label(url)} — недоступен" if err else f"📦 {_repo_label(url)} · {len(ents)}", f"ml_r:{i}")])
        if len(repos) > 1:
            rows.append([(visual.btn(f"Все модули · {total}"), "ml_a")])
        rows.append([engine.InputButton("🔍 Поиск по всем", "ml_q", hint="название модуля", style="primary")])
        rows.append([(visual.btn('Закрыть', 'close'), "ml_x")])
        return "\n".join(lines), rows

    def view_list():
        lst = current_list()
        st['list'] = lst
        multi = len({e['repo'] for e in lst}) > 1
        items = [(e['name'] + (f" · {_repo_label(e['repo']).split('/')[0]}" if multi else ""), f"ml_o:{i}")
                 for i, e in enumerate(lst)]
        rows, st['page'], total = engine.picker_page(items, st['page'], per_page=8, page_callback="ml_p:")
        if st['preset'] is not None:
            head = st['preset'][1]
        else:
            head = where() + (f" · «{html.escape(st['query'])}»" if st['query'] else "") + f" · {len(lst)}"
        lines = [visual.title('Каталог модулей', head + (f" · стр. {st['page'] + 1}/{total}" if total > 1 else ""))]
        if not lst:
            lines.append(visual.err('Ничего не нашлось' if st['query'] else 'Модулей нет'))
        if st['scope'] and errors.get(st['scope']):
            lines.append(visual.warn(html.escape(errors[st['scope']])))
        if st['preset'] is None:
            search = engine.InputButton("🔍 Поиск" + (" в " + _repo_label(st['scope']).split('/')[-1] if st['scope'] else " по всем"),
                                        "ml_q", hint="название модуля", style="primary", prefill=st['query'] or "")
            rows.append([search] + ([("✕ Сбросить", "ml_c")] if st['query'] else []))
        rows.append([(visual.btn('Репозитории', 'back'), "ml_h"), (visual.btn('Закрыть', 'close'), "ml_x")])
        return "\n".join(lines), rows

    async def view_card(i):
        e = st['list'][i]
        try:
            meta = _parse_module_meta(await _source(e)) or {}
        except Exception as ex:
            return visual.err(f"Не скачался: {html.escape(str(ex))}"), [[(visual.btn('К списку', 'back'), "ml_b")]]
        cur = engine.modules.get(meta.get('module_name') or '')
        cmds = meta.get('commands') or []
        lines = [visual.title(meta.get('name') or e['name'], html.escape(meta.get('description') or e['description'] or '') or None),
                 visual.field('id', meta.get('module_name') or '?'), visual.field('версия', meta.get('version') or '?'),
                 visual.field('автор', meta.get('author') or '?', code=False),
                 visual.field('файл', f"{_repo_label(e['repo'])}/{e['path']}")]
        if cmds:
            lines += ["", visual.section(f"Команды · {len(cmds)}"),
                      visual.item(", ".join(f"<code>.{html.escape(n[0])}</code>" for n in cmds[:25]))]
        lines += _requirements_lines(meta)
        newer = cur is not None and _vkey(meta.get('version')) > _vkey(cur.version)
        if cur is not None:
            lines += ["", visual.hint(f"установлен: версия {cur.version}" + (" — есть новее" if newer else ""))]
        rows = [] if cur is not None and not newer else [[(visual.btn("Обновить" if newer else "Установить"), f"ml_i:{i}", "success")]]
        rows.append([(visual.btn('К списку', 'back'), "ml_b")])
        return "\n".join(lines), rows

    if preset is not None or scope or query:
        st['view'] = 'list'
        text, rows = view_list()
    else:
        text, rows = view_repos()
    try:
        form = await engine.create_inlineform(event.chat_id, text, buttons=rows, timeout=900)
    except Exception:
        lst = current_list() if st['view'] == 'list' else all_entries()
        lines = [visual.title('Каталог модулей', f"{len(lst)}")] + [
            visual.item(f"<code>{html.escape(_stem(e['file']))}</code> — {html.escape(_repo_label(e['repo']))}") for e in lst[:80]]
        await engine.answer(event, "\n".join(lines) + "\n\n" + visual.hint('установить — .dlm имя · поиск — .modlist имя'))
        return
    try:
        await event.delete()
    except Exception:
        pass

    async def show(view):
        st['view'] = view
        t, r = view_repos() if view == 'repos' else view_list()
        await form.edit(t, buttons=r)

    @form.on_input(data='ml_q')
    async def _(inp):
        st['query'] = (inp.value or '').strip()[:60]
        st['page'] = 0
        st['preset'] = None
        await show('list')

    @form.on_click(data_startswith="ml_")
    async def _(click):
        d = click.data
        if d == 'ml_x':
            await form.delete()
            return
        if d == 'ml_h':                                   # к репозиториям
            st.update(scope=None, query='', page=0, preset=None)
            await click.answer()
            await show('repos')
            return
        if d == 'ml_a':                                   # все модули всех репозиториев
            st.update(scope=None, query='', page=0, preset=None)
            await click.answer()
            await show('list')
            return
        if d == 'ml_c':                                   # сбросить поиск
            st.update(query='', page=0)
            await click.answer()
            await show('list')
            return
        if d == 'ml_b':
            await click.answer()
            await show('list')
            return
        kind, _sep, arg = d.partition(':')
        if kind == 'ml_r':
            i = int(arg)
            if 0 <= i < len(repos):
                st.update(scope=repos[i][0], query='', page=0, preset=None)
            await click.answer()
            await show('list')
            return
        if kind == 'ml_p':
            st['page'] = int(arg)
            await click.answer()
            await show('list')
            return
        i = int(arg)
        if i >= len(st['list']):
            await click.answer("Список обновился — откройте заново")
            return
        if kind == 'ml_o':
            await click.answer()
            st['view'] = 'card'
            t, r = await view_card(i)
            await form.edit(t, buttons=r)
            return
        if kind == 'ml_i':
            e = st['list'][i]
            await click.answer("Устанавливаю…")
            try:
                info = await engine._load_from_source(await _source(e), target='loaded')
                text = "\n".join([visual.ok('Модуль установлен'), visual.field('название', info.name, code=False),
                                  visual.field('id', info.module_name), visual.field('версия', info.version)])
            except Exception as ex:
                text = visual.err('Ошибка при загрузке модуля') + f"\n<code>{html.escape(str(ex))}</code>"
            await form.edit(text, buttons=[[(visual.btn('К списку', 'back'), "ml_b")]])


async def _pick_list(event, entries, title):
    """Несколько совпадений (.dlm) — тот же каталог, сразу со списком найденного."""
    await _catalog_form(event, await _repos_entries(), preset=(entries, title))


@decorators.command('modlist', 'ml')
async def modlist_cmd(event):
    """каталог модулей: репозитории → модули → карточка → установка; поиск кнопкой. [название] — сразу искать; refresh — обновить"""
    q = engine.get_args().strip()
    force = q.lower() == 'refresh'
    if force:
        q = ''
    await engine.answer(event, visual.wait('Загружаю каталог…'))
    repos = await _repos_entries(force=force)
    if not repos:
        await engine.answer(event, visual.err('Репозиториев нет') + "\n" + visual.hint('.repo add <ссылка> или .repo reset'))
        return
    if all(err for _u, _e, err in repos):
        await engine.answer(event, visual.err('Каталог недоступен') + "\n"
                            + "\n".join(visual.warn(f"{html.escape(_repo_label(u))}: {html.escape(err)}") for u, _e, err in repos))
        return
    await _catalog_form(event, repos, query=q)


async def _updates(event):
    await engine.answer(event, visual.wait('Проверяю обновления…'))
    entries, errors = await _catalog(force=True)
    loaded = {n: i for n, i in engine.modules.items() if not i.is_builtin}
    by_key = {}
    for e in entries:
        by_key.setdefault(_norm(_stem(e['file'])), []).append(e)
        if e['module_name']:
            by_key.setdefault(_norm(e['module_name']), []).append(e)
    found = []
    for name, info in loaded.items():
        cands = by_key.get(_norm(name)) or by_key.get(_norm(Path(info.path or '').stem)) or []
        for e in cands[:2]:
            try:
                meta = _parse_module_meta(await _source(e))
            except Exception:
                continue
            if meta and meta['module_name'] == name and _vkey(meta['version']) > _vkey(info.version):
                found.append((info, meta, e))
                break
        await asyncio.sleep(0)
    if not found:
        await engine.answer(event, visual.ok('Все модули из репозиториев — последних версий')
                            + ("\n" + "\n".join(visual.warn(f"{html.escape(u)}: {html.escape(x)}") for u, x in errors.items())
                               if errors else ""))
        return
    lines = [visual.title('Обновления', str(len(found)))]
    lines += [visual.item(f"{html.escape(i.name)}  <code>{html.escape(i.version)}</code> → <code>{html.escape(m['version'])}</code>")
              for i, m, _e in found]
    rows = [[(visual.btn(f"Обновить всё ({len(found)})"), "up_all", "success")]] + \
           [[(f"↑ {i.name} {m['version']}", f"up_{k}")] for k, (i, m, _e) in enumerate(found[:10])] + \
           [[(visual.btn('Закрыть', 'close'), "up_x")]]
    try:
        form = await engine.create_inlineform(event.chat_id, "\n".join(lines), buttons=rows, timeout=900)
    except Exception:
        await engine.answer(event, "\n".join(lines) + "\n\n" + visual.hint('обновить — .dlm имя'))
        return
    try:
        await event.delete()
    except Exception:
        pass
    done = set()

    @form.on_click(data_startswith="up_")
    async def _(click):
        d = click.data[3:]
        if d == 'x':
            await form.delete()
            return
        idx = range(len(found)) if d == 'all' else [int(d)]
        await click.answer("Обновляю…")
        res = []
        for k in idx:
            if k in done:
                continue
            info, meta, e = found[k]
            try:
                await engine._load_from_source(await _source(e), target='loaded')
                done.add(k)
                res.append(f"√ {info.name} → {meta['version']}")
            except Exception as ex:
                res.append(f"× {info.name}: {ex}")
        await form.edit("\n".join(lines) + "\n\n" + html.escape("\n".join(res)),
                        buttons=None if len(done) == len(found) else rows)


@decorators.command('repo', 'repos')
async def repo_cmd(event):
    """репозитории модулей: список; add <ссылка> · del <№|ссылка> · reset"""
    parts = engine.get_args().strip().split(maxsplit=1)
    sub = parts[0].lower() if parts else ''
    rest = parts[1].strip() if len(parts) > 1 else ''
    repos = list(await _cfg('repos', list(DEFAULT_REPOS)) or [])
    if sub in ('add', '+'):
        url = rest.rstrip('/')
        if not url:
            await engine.answer(event, visual.err('Формат: .repo add https://github.com/автор/репозиторий'))
            return
        if not re.match(r'^https?://', url):
            url = 'https://' + url
        if url in repos:
            await engine.answer(event, visual.warn('Уже есть'))
            return
        await engine.answer(event, visual.wait('Проверяю репозиторий…'))
        try:
            n = len(await _repo_list(url, force=True))
        except Exception as e:
            await engine.answer(event, visual.err(f"Не открылся: {html.escape(str(e))}"))
            return
        await engine.edit_config('repos', 'add', url, module_name=MODULE_NAME)
        await engine.answer(event, visual.ok(f"Добавлен: {html.escape(_repo_label(url))}") + "\n" + visual.field('модулей', n))
        return
    if sub in ('del', 'rm', '-'):
        target = repos[int(rest) - 1] if rest.isdigit() and 1 <= int(rest) <= len(repos) else rest.rstrip('/')
        if target not in repos:
            await engine.answer(event, visual.err('Нет такого — номер из .repo или ссылка'))
            return
        await engine.edit_config('repos', 'del', target, module_name=MODULE_NAME)
        _CATALOG.pop(target, None)
        await engine.answer(event, visual.ok(f"Удалён: {html.escape(_repo_label(target))}"))
        return
    if sub == 'reset':
        await engine.edit_config('repos', 'to_default', module_name=MODULE_NAME)
        _CATALOG.clear()
        await engine.answer(event, visual.ok('Репозитории — по умолчанию'))
        return
    lines = [visual.title('Репозитории модулей', str(len(repos)))]
    for i, url in enumerate(repos, 1):
        try:
            n = f"{len(await _repo_list(url))} модулей"
        except Exception as e:
            n = f"недоступен: {e}"
        lines.append(f"{i}. <a href=\"{html.escape(url)}\">{html.escape(_repo_label(url))}</a> — {html.escape(n)}")
    if not repos:
        lines.append(visual.hint('пусто — .repo add <ссылка> или .repo reset'))
    lines += ["", visual.hint('.repo add <ссылка> · .repo del <№> · .modlist — каталог · .dlm имя — установить')]
    await engine.answer(event, "\n".join(lines))
