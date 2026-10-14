"""
modules/builtin/SystemKeeper.py

Интерфейс к frx_systemkeeper.py (материнский файл в корне проекта): перезагрузка
и обновление юзербота. Вся тяжёлая работа (проверка архива, резервная копия,
замена файлов, pip, откат) выполняется самим keeper'ом, пока main.py остановлен,
— этот модуль только принимает команды, готовит пакет и отдаёт его keeper'у.

Команды:
    .restart / .rs      — перезагрузка юзербота.
    .exit               — резервная копия данных (файлом в ЛС от бота), затем полное
                          выключение юзербота (без перезапуска, в отличие от .restart).
    .update  / .upd     — обновление FRX:
                            ответом на .zip-файл (внутри должна быть папка FRX/) — из файла;
                            `.update git` — из GitHub (репозиторий/ветка/токен — конфиги модуля,
                            `.config systemkeeper`).
                          Перед установкой показывается инлайн-превью (версии, что изменится);
                          без стационарного бота подтверждение — словом `yes` в команде.
    .sk / .systemkeeper — статус keeper'а, настройки обновления, резервные копии.

Что обновляется: все файлы кода из архива, включая новые (кроме user/, .git, окружений и кэшей); файлы
старой версии, которых нет в новой, удаляются по манифесту frx_manifest.json. НЕ трогается папка user/
(config.json, БД, сессии, аккаунты, модули, свои ассеты). Версия должна быть строго новее текущей —
та же или более старая не ставится. Перед заменой keeper делает резервную копию
(user/data/backup/) и откатывается, если новая версия не запустилась.

После перезапуска модуль сам редактирует исходное сообщение с командой («Перезагружено
за N сек», «Обновлено 1.0.0 → 1.0.1»), а подробности обновления (списки файлов, pip,
причина отката) отправляет в ЛС через стационарного бота, тем же путём, что и модуль
Logging (учитывает его конфиги enabled / log_chat_id).

Конфиги (`.config systemkeeper`):
    github_repo     — репозиторий: https://github.com/owner/name или owner/name
    github_branch   — ветка (по умолчанию main)
    github_token    — токен доступа (нужен только для приватного репозитория;
                      достаточно fine-grained токена с правом чтения содержимого)
    notify_details  — слать подробности обновления/восстановления в ЛС от бота
"""

import asyncio
import html
import os
import re
import time

import engine
import decorators
import bot
import visual
import frx_systemkeeper as keeper

NAME = "SystemKeeper"
MODULE_NAME = "systemkeeper"
VERSION = "1.3"
AUTHOR = "FRX"
DESCRIPTION = "Перезагрузка и обновление юзербота (из файла и из GitHub) через frx_systemkeeper.py"

MAX_UPDATE_ZIP = 300 * 1024 * 1024
PREVIEW_LINES = 12
DETAIL_LINES = 40

# Подробности, дождавшиеся готовности стационарного бота (см. _send_pending_details)
_pending_details = None


# --------------------------------------------------------------------- #
# Мелочи
# --------------------------------------------------------------------- #

def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _fmt_size(n) -> str:
    n = float(n)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or unit == 'ГБ':
            return f"{n:.0f} {unit}" if unit == 'Б' else f"{n:.1f} {unit}"
        n /= 1024


def _notify(event) -> dict:
    """Куда вернуться после перезапуска, чтобы отредактировать сообщение с командой."""
    return {'chat_id': event.chat_id, 'message_id': event.id}


async def _cfg(key):
    return await engine.get_config(key, 'systemkeeper')


async def _no_keeper(event):
    await engine.answer(
        event,
        visual.err("Юзербот запущен не через SystemKeeper") + "\n"
        + visual.hint("запускайте") + " <code>python frx_systemkeeper.py</code>",
    )


def _diff_lines(added, changed, removed, limit) -> str:
    """
    Список файлов «+ новый / ~ изменён / − удалён» не длиннее limit строк. Если файлов
    больше — лимит делится между категориями поровну (остаток достаётся тем, кому не
    хватило), чтобы удалённые и изменённые файлы не вытеснялись длинным списком новых.
    """
    groups = [('+', list(added)), ('~', list(changed)), ('−', list(removed))]
    total = sum(len(g) for _, g in groups)
    quota = [len(g) for _, g in groups]
    if total > limit:
        base = limit // 3
        quota = [min(len(g), base) for _, g in groups]
        left = limit - sum(quota)
        for i, (_, g) in enumerate(groups):
            extra = min(len(g) - quota[i], left)
            quota[i] += extra
            left -= extra
    lines = [f"{sign} {_esc(name)}" for (sign, g), q in zip(groups, quota) for name in g[:q]]
    hidden = total - sum(quota)
    if hidden > 0:
        lines.append(f"… и ещё {hidden}")
    return "\n".join(lines)


async def _confirm_flow(event, text, on_confirm, on_cancel, yes, tag):
    """
    Превью с кнопками «Подтвердить/Отмена» (инлайн-форма). Если стационарного бота
    нет — действие выполняется только когда в команде есть слово `yes`.
    on_confirm(form) — form=None, если подтверждение пришло словом `yes`.
    """
    if yes:
        await on_confirm(None)
        return
    try:
        form = await engine.create_inlineform(
            chat_id=event.chat_id,
            text=text,
            buttons=[[(visual.btn("Подтвердить"), f"sk_{tag}_ok", "success")], [(visual.btn("Отмена", "close"), f"sk_{tag}_no")]],
        )
    except Exception:
        await engine.answer(
            event,
            text + "\n\n" + visual.warn(visual.hint("стационарный бот недоступен — повторите команду со словом")
                                          + " <code>yes</code> " + visual.hint("для подтверждения")),
        )
        return

    state = {'done': False}

    @form.on_click(data=f"sk_{tag}_ok")
    async def _ok(click):
        if state['done']:
            await click.answer()
            return
        state['done'] = True
        await click.answer("Выполняю…")
        await on_confirm(form)

    @form.on_click(data=f"sk_{tag}_no")
    async def _no(click):
        state['done'] = True
        await click.answer("Отменено")
        await on_cancel()
        await form.edit(visual.warn("<b>Отменено</b>"), buttons=None)

    await engine.answer(event, visual.wait("Проверьте данные в форме ниже и подтвердите"))


# --------------------------------------------------------------------- #
# .restart
# --------------------------------------------------------------------- #

@decorators.command('restart', 'rs')
async def restart_cmd(event):
    """перезагружает юзербот (через frx_systemkeeper)"""
    if not keeper.keeper_active():
        await _no_keeper(event)
        return
    await engine.answer(event, visual.wait("Перезагружаю юзербот…"))
    keeper.write_request('restart', notify=_notify(event))
    engine.request_shutdown(keeper.EXIT_RESTART)


# --------------------------------------------------------------------- #
# .exit
# --------------------------------------------------------------------- #

@decorators.command('exit')
async def exit_cmd(event):
    """делает резервную копию данных (файлом в ЛС от бота) и полностью выключает юзербот (без перезагрузки)"""
    await engine.answer(event, visual.wait("Делаю резервную копию перед выключением…"))

    version = engine.get_version()
    dst = keeper.TMP_DIR / f"frx_backup_v{version}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    backup_note = ""
    try:
        if getattr(engine, '_aiogram_bot', None) is None:
            raise RuntimeError("стационарного бота нет — бэкап некуда отправить")
        info = await asyncio.wait_for(asyncio.to_thread(keeper.make_user_backup, dst, version), 90)
        if info['size'] > 49 * 1024 * 1024:     # лимит Bot API на файл — 50 МБ
            raise ValueError(f"архив {_fmt_size(info['size'])} больше лимита бота (50 МБ)")
        try:
            await engine.answer(event, visual.wait(f"Отправляю резервную копию в ЛС от бота ({_fmt_size(info['size'])})…"))
        except Exception:
            pass
        caption = "\n".join([
            visual.title("FRX backup", sub="перед .exit"),
            visual.field("версия", _esc(version)),
            visual.field("создан", time.strftime('%d.%m.%Y %H:%M:%S')),
            visual.field("файлов", info['files']),
            "",
            f"{visual.visual('security')} {visual.hint('содержит сессии и токен — не пересылайте')}",
            visual.item("восстановление: ответьте <code>.restorebackup</code> на этот файл"),
        ])
        # не дольше 3 минут: .exit не должен «висеть», если сеть медленная — тогда выключаемся без бэкапа
        await asyncio.wait_for(engine.bot_send_to_owner(caption, file=str(dst)), 180)
        backup_note = visual.ok("Резервная копия отправлена в ЛС от бота") + f"  <code>{_fmt_size(info['size'])}</code>\n"
    except Exception as e:
        reason = 'превышено время ожидания' if isinstance(e, asyncio.TimeoutError) else str(e)
        backup_note = (visual.warn("Не удалось создать бэкап — выключаюсь без него")
                       + f"\n<code>{_esc(reason)}</code>\n")
        engine.tlog(f"[SystemKeeper] .exit: ошибка резервной копии: {reason}")
    finally:
        try:
            dst.unlink()
        except OSError:
            pass

    await engine.answer(event, f"{backup_note}{visual.visual('status_stop')} <b>Юзербот выключается…</b>")

    # EXIT_STOP (0) — обычная остановка, keeper (если запущен под ним)
    # тоже завершается, никакого автоматического перезапуска не будет. Если keeper
    # не активен — request_shutdown() всё равно корректно завершит main.py.
    engine.request_shutdown(keeper.EXIT_STOP)


# --------------------------------------------------------------------- #
# .update
# --------------------------------------------------------------------- #

@decorators.command('update', 'upd')
async def update_cmd(event):
    """обновление FRX: ответом на .zip (папка FRX/) или `.update git` из GitHub; `yes` — без инлайн-подтверждения"""
    if not keeper.keeper_active():
        await _no_keeper(event)
        return

    tokens = engine.get_args().lower().split()
    yes = 'yes' in tokens
    from_git = any(t in ('git', 'github', 'gh') for t in tokens)

    if not from_git and not event.is_reply:
        await engine.answer(
            event,
            visual.title("Обновление FRX") + "\n"
            + visual.item("из файла: ответьте <code>.update</code> на .zip-архив (внутри папка <code>FRX/</code>)") + "\n"
            + visual.item("из GitHub: <code>.update git</code> (настройки — <code>.config systemkeeper</code>)"),
        )
        return

    zip_path = keeper.INCOMING_DIR / 'update.zip'
    try:
        keeper.INCOMING_DIR.mkdir(parents=True, exist_ok=True)

        if from_git:
            repo = (await _cfg('github_repo') or '').strip()
            if not repo:
                await engine.answer(
                    event,
                    visual.err("Не указан репозиторий GitHub") + "\n"
                    + visual.hint("задайте") + " <code>github_repo</code> " + visual.hint("в")
                    + " <code>.config systemkeeper</code>",
                )
                return
            branch = await _cfg('github_branch')
            token = await _cfg('github_token')
            await engine.answer(event, visual.wait("Скачиваю обновление с GitHub…"))
            slug = await asyncio.to_thread(keeper.download_github, repo, branch, token, str(zip_path))
            source_kind, source_label = 'github', f"GitHub {slug}"
        else:
            replied = await event.get_reply_message()
            if not replied or not replied.file:
                await engine.answer(event, visual.err("Ответьте на .zip-файл, а не на текст"))
                return
            file_name = replied.file.name or ''
            if not file_name.lower().endswith('.zip'):
                await engine.answer(event, visual.err("Нужен файл с расширением .zip"))
                return
            if (replied.file.size or 0) > MAX_UPDATE_ZIP:
                await engine.answer(event, visual.err("Архив слишком большой") + "\n" + visual.field("лимит", _fmt_size(MAX_UPDATE_ZIP)))
                return
            await engine.answer(event, visual.wait("Скачиваю файл…"))
            await replied.download_media(file=str(zip_path))
            source_kind, source_label = 'file', f"файл {file_name}"

        await engine.answer(event, visual.wait("Проверяю архив…"))
        info = await asyncio.to_thread(keeper.prepare_update, str(zip_path), source_kind, engine.get_version())

    except keeper.KeeperError as e:
        await engine.answer(event, visual.err("Обновление отклонено") + f"\n{_esc(e)}")
        return
    except Exception as e:
        await engine.answer(event, visual.err("Ошибка") + f"\n<code>{_esc(e)}</code>")
        engine.tlog(f"[SystemKeeper] ошибка подготовки обновления: {e}")
        return
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    total = len(info['added']) + len(info['changed']) + len(info['removed'])
    text = "\n".join([
        visual.title("Обновление FRX"),
        f"{visual.visual('element')} версия  <code>{_esc(info['from'])}</code> → <code>{_esc(info['to'])}</code>",
        visual.field("источник", _esc(source_label)),
        visual.field("файлов", f"{total} · +{len(info['added'])} ~{len(info['changed'])} −{len(info['removed'])}"),
    ]) + "\n"
    if info['requirements_changed']:
        text += visual.item("requirements.txt изменился — зависимости обновятся автоматически") + "\n"
    if total:
        text += f"\n{visual.section('Изменения')}\n<pre>{_diff_lines(info['added'], info['changed'], info['removed'], PREVIEW_LINES)}</pre>\n"
    text += (
        "\n" + visual.warn("Юзербот остановится, файлы обновятся, затем он запустится снова.") + "\n"
        + visual.hint("папка user/ (сессии, БД, конфиги, модули) не затрагивается; "
                      "перед заменой создаётся резервная копия, при неудачном запуске — откат")
    )

    async def _go(form):
        progress = visual.wait("Останавливаю юзербот и обновляю файлы…")
        if form is not None:
            await form.edit(progress, buttons=None)
        else:
            await engine.answer(event, progress)
        keeper.write_request('update', notify=_notify(event), meta={
            'from': info['from'], 'to': info['to'], 'source': source_label,
        })
        engine.request_shutdown(keeper.EXIT_APPLY)

    async def _cancel():
        keeper.cleanup_work_dirs()

    await _confirm_flow(event, text, _go, _cancel, yes, 'update')


# --------------------------------------------------------------------- #
# .sk — статус
# --------------------------------------------------------------------- #

@decorators.command('sk', 'systemkeeper')
async def sk_cmd(event):
    """статус SystemKeeper, настройки обновления и резервные копии"""
    active = keeper.keeper_active()
    try:
        repo = (await _cfg('github_repo') or '').strip()
        branch = await _cfg('github_branch')
        has_token = bool((await _cfg('github_token') or '').strip())
    except KeyError:
        repo, branch, has_token = '', 'main', False

    lines = [
        visual.title("SystemKeeper"),
        f"{visual.visual('status_on') if active else visual.visual('status_off')} keeper  "
        + (f"<code>v{_esc(keeper.KEEPER_VERSION)}</code> · pid <code>{_esc(os.environ.get('FRX_KEEPER_PID', '?'))}</code>"
           if active else "<i>не запущен</i>"),
        visual.field("FRX", _esc(engine.get_version())),
        "",
        visual.section("GitHub"),
    ]
    if repo:
        lines += [
            visual.field("репозиторий", f"{_esc(repo)}@{_esc(branch)}"),
            visual.field("токен", "задан" if has_token else "не задан", code=False),
        ]
    else:
        lines.append(visual.item("не настроен — <code>.config systemkeeper</code>"))
    backups = keeper.list_backups()
    if backups:
        lines += ["", visual.section("Резервные копии"), visual.hint("user/data/backup")]
        for name, size, _mtime in backups[:6]:
            lines.append(visual.item(f"<code>{_esc(name)}</code> — {_fmt_size(size)}"))
    lines += [
        "",
        visual.section("Команды"),
        "<code>.restart</code> · <code>.update</code> · <code>.update git</code> · "
        "<code>.createbackup</code> · <code>.restorebackup</code>",
    ]
    await engine.answer(event, "\n".join(lines))


# --------------------------------------------------------------------- #
# Итог после перезапуска: правка исходного сообщения + подробности в лог
# --------------------------------------------------------------------- #

def _elapsed(res) -> str:
    created = res.get('created')
    if not created:
        return ""
    return f" · {max(time.time() - created, 0):.1f} сек"


def _short_text(res) -> str:
    action, ok = res.get('action'), res.get('ok')
    meta = res.get('meta') or {}
    error = _esc(res.get('error') or 'неизвестная ошибка')
    if action == 'update':
        if ok:
            return (visual.ok("FRX обновлён") + "  "
                    f"<code>{_esc(res.get('from'))}</code> → <code>{_esc(res.get('to'))}</code>{_elapsed(res)}")
        tail = "\n" + visual.hint("выполнен откат к прежней версии") if res.get('rolled_back') else ""
        return visual.err("Обновление не удалось") + f"\n<code>{error}</code>{tail}"
    if action == 'restore':
        if ok:
            return (visual.ok("Бэкап восстановлен") + f"{_elapsed(res)}\n"
                    + visual.field("версия бэкапа", _esc(meta.get('backup_version', '?'))) + "\n"
                    + visual.field("создан", _esc(meta.get('created', '?'))))
        tail = "\n" + visual.hint("данные возвращены к состоянию до восстановления") if res.get('rolled_back') else ""
        return visual.err("Восстановление не удалось") + f"\n<code>{error}</code>{tail}"
    if ok:
        return visual.ok("Юзербот перезагружен") + _elapsed(res)
    return visual.err("Перезагрузка не удалась") + f"\n<code>{error}</code>"


def _details_text(res):
    """Подробный отчёт для лога (ЛС от бота). None — для обычного restart подробностей нет."""
    action, ok = res.get('action'), res.get('ok')
    if action == 'restart' and ok:
        return None
    meta = res.get('meta') or {}
    lines = []

    if action == 'update':
        title = "FRX обновлён" if ok else "Обновление FRX не удалось"
        lines.append(visual.ok(title) if ok else visual.err(title))
        lines.append(visual.sep())
        lines.append(f"{visual.visual('element')} версия  <code>{_esc(res.get('from'))}</code> → <code>{_esc(res.get('to'))}</code>")
        if meta.get('source'):
            lines.append(visual.field("источник", _esc(meta['source'])))
    elif action == 'restore':
        title = "Бэкап восстановлен" if ok else "Восстановление бэкапа не удалось"
        lines.append(visual.ok(title) if ok else visual.err(title))
        lines.append(visual.sep())
        lines.append(visual.field("версия бэкапа", _esc(meta.get('backup_version', '?'))))
        lines.append(visual.field("создан", _esc(meta.get('created', '?'))))
        lines.append(visual.field("файлов", _esc(meta.get('files', '?'))))
    else:
        lines.append(visual.err("Перезагрузка не удалась"))
        lines.append(visual.sep())

    if res.get('duration') is not None:
        lines.append(visual.field("замена файлов", f"{res['duration']} сек"))
    if res.get('backup'):
        lines.append(visual.field("резервная копия", _esc(res['backup'])))
    if not ok:
        lines.append(visual.warn("Причина:") + f" <code>{_esc(res.get('error') or '?')}</code>")
        lines.append(visual.hint("откат выполнен" if res.get('rolled_back') else "откат не выполнялся"))

    if action == 'update' and ok:
        added, changed, removed = res.get('added', []), res.get('changed', []), res.get('removed', [])
        lines.append(visual.field("файлов", f"+{len(added)} ~{len(changed)} −{len(removed)}"))
        body = _diff_lines(added, changed, removed, DETAIL_LINES)
        if body:
            lines.append("")
            lines.append(visual.section("Изменения"))
            lines.append(f"<pre>{body}</pre>")
        pip = res.get('pip') or {}
        lines.append("")
        lines.append(visual.section("Зависимости"))
        if pip.get('ran'):
            if pip.get('ok'):
                lines.append(visual.item("обновлены (<code>pip install -r requirements.txt</code>)"))
            else:
                lines.append(visual.warn("<b>ошибка pip</b>") + "\n"
                             f"<pre>{_esc((pip.get('tail') or '')[-700:])}</pre>")
        else:
            lines.append(visual.item("requirements.txt не менялся"))

    text = "\n".join(lines)
    return text[:3900]


async def _edit_origin(res):
    notify = res.get('notify') or {}
    chat_id, message_id = notify.get('chat_id'), notify.get('message_id')
    if not chat_id or not message_id:
        return
    try:
        entity = await engine._resolve_entity(chat_id)
        await engine.client.edit_message(entity, message_id, _short_text(res), parse_mode='html')
    except Exception as e:
        engine.tlog(f"[SystemKeeper] не удалось обновить сообщение с командой: {e}")


@decorators.on_start()
async def setup():
    global _pending_details
    await engine.create_config(
        key='github_repo',
        description='Репозиторий GitHub для .update git: https://github.com/owner/name или owner/name',
        data_type='str', default='https://github.com/Shxdowrate/FRX-Userbot',
    )
    await engine.create_config(
        key='github_branch',
        description='Ветка репозитория для .update git',
        data_type='str', default='main',
    )
    await engine.create_config(
        key='github_token',
        description='Токен GitHub (нужен только для приватного репозитория; права: чтение содержимого)',
        data_type='str', default='',
    )
    await engine.create_config(
        key='notify_details',
        description='Слать подробности обновления/восстановления бэкапа в ЛС через стационарного бота',
        data_type='bool', default=True,
    )

    res = keeper.read_result()
    if not res:
        return
    keeper.clear_result()

    await _edit_origin(res)

    details = _details_text(res)
    if details:
        plain = re.sub(r'<[^>]+>', '', html.unescape(details))
        engine.tlog(f"[SystemKeeper]\n{plain}")
        if await _cfg('notify_details'):
            _pending_details = details


@bot.on_start()
async def _send_pending_details():
    """Стационарный бот поднят — отправляем накопленные подробности (см. Logging.py, тот же путь)."""
    global _pending_details
    text, _pending_details = _pending_details, None
    if not text:
        return
    try:
        try:
            if not await engine.get_config('enabled', 'logging'):
                return
            chat_id = await engine.get_config('log_chat_id', 'logging') or engine._owner_id
        except KeyError:
            chat_id = engine._owner_id
        await engine.bot.send_message(chat_id, text)
    except Exception as e:
        engine.tlog(f"[SystemKeeper] не удалось отправить подробности в ЛС: {e}")
