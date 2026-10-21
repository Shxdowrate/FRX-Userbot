"""
modules/builtin/Backup.py

Резервные копии пользовательских данных FRX.

Команды:
    .createbackup   — собирает zip и отправляет файлом в ЛС от стационарного бота
                      Если бот не может написать
                      (нет диалога / он в ЧС) — юзербот сам снимает его с ЧС, пишет ему
                      /start, и бот присылает файл (engine.bot_send_to_owner).
    .restorebackup  — ответом на такой zip восстанавливает данные. Само
                      восстановление делает frx_systemkeeper.py, пока юзербот
                      остановлен (тот же механизм, что и у .update). `yes` в
                      команде — без инлайн-подтверждения.

Что входит в бэкап (пользовательские данные — ровно то, что НЕ трогают обновления):
    вся папка user/: config.json, data/ (БД, сессии, аккаунты, файлы; кроме
    data/backup, data/keeper, data/bin, data/cache, кэша BotChat, папок с файлом-меткой
    .nobackup и одиночных файлов больше 45 МБ), modules/ (загруженные модули), assets/.
Код FRX (core, aiobot, modules/builtin, ...) в бэкап не входит — он обновляется
через .update и защищён собственными копиями в user/data/backup/.

ВАЖНО: в архиве лежат сессии Telegram и токен бота — это полный доступ к
аккаунту. Храните его только в ЛС с ботом, никому не пересылайте.

Перед восстановлением keeper автоматически делает страховую копию текущих данных
(user/data/backup/userdata_prerestore_*.zip) и возвращает их, если после восстановления
юзербот не запустился. Итог операции (правка исходного сообщения и подробности в ЛС
от бота) показывает модуль SystemKeeper.
"""

import asyncio
import html
import time

import engine
import decorators
import visual
import frx_systemkeeper as keeper
from core import database

NAME = "Backup"
MODULE_NAME = "backup"
VERSION = "1.2"
AUTHOR = "FRX"
DESCRIPTION = "Резервные копии данных FRX: .createbackup (в ЛС от бота) и .restorebackup (через SystemKeeper)"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Backup.py"

# лимит Bot API на отправку файла ботом — 50 МБ (с небольшим запасом)
MAX_SEND_SIZE = 49 * 1024 * 1024


def _esc(value) -> str:
    return html.escape(str(value), quote=False)


def _heaviest(top=5):
    """Самые тяжёлые папки из того, что идёт в бэкап: [(путь, байт)]."""
    sizes = {}
    for rel, f in keeper._iter_user_files():
        parts = rel.split('/')
        key = '/'.join(parts[:3]) if len(parts) > 3 else '/'.join(parts[:-1]) or rel
        try:
            sizes[key] = sizes.get(key, 0) + f.stat().st_size
        except OSError:
            pass
    return sorted(sizes.items(), key=lambda x: -x[1])[:top]


def _fmt_size(n) -> str:
    n = float(n)
    for unit in ('Б', 'КБ', 'МБ', 'ГБ'):
        if n < 1024 or unit == 'ГБ':
            return f"{n:.0f} {unit}" if unit == 'Б' else f"{n:.1f} {unit}"
        n /= 1024


@decorators.command('createbackup')
async def createbackup_cmd(event):
    """собирает бэкап папки user/ (config.json, data/, modules/, assets/) и отправляет файлом в ЛС от стационарного бота"""
    version = engine.get_version()
    dst = keeper.TMP_DIR / f"frx_backup_v{version}_{time.strftime('%Y%m%d_%H%M%S')}.zip"
    try:
        await engine.answer(event, visual.wait("Собираю резервную копию…"))
        database.flush_all()                 # отложенная запись базы — в бэкап идёт свежая
        info = await asyncio.to_thread(keeper.make_user_backup, dst, version)

        if info['size'] > MAX_SEND_SIZE:
            heavy = await asyncio.to_thread(_heaviest)
            await engine.answer(
                event,
                visual.err("Бэкап слишком большой для отправки ботом") + "\n"
                + visual.field("размер", _fmt_size(info['size'])) + "\n"
                + visual.field("лимит", "~50 МБ") + "\n"
                + (visual.section("Больше всего места") + "\n" + "\n".join(
                    visual.item(f"<code>{_esc(p)}</code> — {_fmt_size(n)}") for p, n in heavy) + "\n" if heavy else "")
                + visual.hint("лишнее — удалите или положите в папку файл .nobackup (она не попадёт в бэкап)"),
            )
            return

        await engine.answer(event, visual.wait("Отправляю в ЛС от бота…"))
        caption = "\n".join([
            visual.title("FRX backup"),
            visual.field("версия", _esc(version)),
            visual.field("создан", time.strftime('%d.%m.%Y %H:%M:%S')),
            visual.field("файлов", info['files']),
            "",
            f"{visual.visual('security')} {visual.hint('содержит сессии и токен — не пересылайте')}",
            visual.item("восстановление: ответьте <code>.restorebackup</code> на этот файл"),
        ])
        await engine.bot_send_to_owner(caption, file=str(dst))
        await engine.answer(
            event,
            visual.ok("Резервная копия отправлена в ЛС от бота") + "\n"
            + visual.field("размер", _fmt_size(info['size'])) + "\n"
            + visual.field("файлов", info['files']),
        )
    except Exception as e:
        await engine.answer(event, visual.err("Не удалось создать бэкап") + f"\n<code>{_esc(e)}</code>")
        engine.tlog(f"[Backup] ошибка создания бэкапа: {e}")
    finally:
        try:
            dst.unlink()
        except OSError:
            pass


@decorators.command('restorebackup')
async def restorebackup_cmd(event):
    """ответом на файл бэкапа восстанавливает данные (через SystemKeeper); `yes` — без инлайн-подтверждения"""
    if not keeper.keeper_active():
        await engine.answer(
            event,
            visual.err("Юзербот запущен не через SystemKeeper") + "\n"
            + visual.hint("запускайте") + " <code>python frx_systemkeeper.py</code>",
        )
        return

    yes = 'yes' in engine.get_args().lower().split()

    if not event.is_reply:
        await engine.answer(
            event,
            visual.title("Восстановление") + "\n"
            + visual.item("ответьте <code>.restorebackup</code> на .zip-файл бэкапа") + "\n"
            + visual.item("бэкап создаёт <code>.createbackup</code>"),
        )
        return

    zip_path = keeper.INCOMING_DIR / 'restore.zip'
    try:
        replied = await event.get_reply_message()
        if not replied or not replied.file:
            await engine.answer(event, visual.err("Ответьте на .zip-файл, а не на текст"))
            return
        if not (replied.file.name or '').lower().endswith('.zip'):
            await engine.answer(event, visual.err("Нужен файл бэкапа с расширением .zip"))
            return

        keeper.INCOMING_DIR.mkdir(parents=True, exist_ok=True)
        await engine.answer(event, visual.wait("Скачиваю и проверяю бэкап…"))
        await replied.download_media(file=str(zip_path))
        info = await asyncio.to_thread(keeper.prepare_restore, str(zip_path))

    except keeper.KeeperError as e:
        await engine.answer(event, visual.err("Бэкап отклонён") + f"\n{_esc(e)}")
        return
    except Exception as e:
        await engine.answer(event, visual.err("Ошибка") + f"\n<code>{_esc(e)}</code>")
        engine.tlog(f"[Backup] ошибка подготовки восстановления: {e}")
        return
    finally:
        try:
            zip_path.unlink()
        except OSError:
            pass

    current = engine.get_version()
    text = "\n".join([
        visual.title("Восстановление из бэкапа"),
        visual.field("версия в бэкапе", _esc(info['frx_version'])),
        visual.field("версия сейчас", _esc(current)),
        visual.field("создан", _esc(info['created'])),
        visual.field("файлов", info['files']),
        "",
        visual.warn("Текущие <code>config.json</code>, <code>data/</code>, <code>modules/</code> и <code>assets/</code> папки user/ "
                    "будут <b>заменены</b> содержимым бэкапа, юзербот перезапустится."),
        visual.hint("перед заменой создаётся страховая копия текущих данных (user/data/backup); "
                    "если юзербот не запустится — данные вернутся автоматически"),
    ])

    async def _go(form):
        progress = visual.wait("Останавливаю юзербот и восстанавливаю данные…")
        if form is not None:
            await form.edit(progress, buttons=None)
        else:
            await engine.answer(event, progress)
        keeper.write_request(
            'restore',
            notify={'chat_id': event.chat_id, 'message_id': event.id},
            meta={'backup_version': info['frx_version'], 'created': info['created'], 'files': info['files']},
        )
        engine.request_shutdown(keeper.EXIT_APPLY)

    async def _cancel():
        keeper.cleanup_work_dirs()

    if yes:
        await _go(None)
        return

    try:
        form = await engine.create_inlineform(
            chat_id=event.chat_id,
            text=text,
            buttons=[[(visual.btn("Да, восстановить"), "backup_restore_ok", "success")], [(visual.btn("Отмена", "close"), "backup_restore_no")]],
        )
    except Exception:
        await engine.answer(
            event,
            text + "\n\n" + visual.warn(visual.hint("стационарный бот недоступен — повторите команду со словом")
                                          + " <code>yes</code> " + visual.hint("для подтверждения")),
        )
        return

    state = {'done': False}

    @form.on_click(data="backup_restore_ok")
    async def _ok(click):
        if state['done']:
            await click.answer()
            return
        state['done'] = True
        await click.answer("Восстанавливаю…")
        await _go(form)

    @form.on_click(data="backup_restore_no")
    async def _no(click):
        state['done'] = True
        await click.answer("Отменено")
        await _cancel()
        await form.edit(visual.warn("<b>Восстановление отменено</b>"), buttons=None)

    await engine.answer(event, visual.wait("Проверьте данные в форме ниже и подтвердите"))
