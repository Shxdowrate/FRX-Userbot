"""
Настройки протокола инлайн-форм (core/inline_forms.py, engine.create_inlineform)
и служебные команды для дебага. Сама логика форм живёт в Core — этот модуль
только конфигурирует её через engine.create_config/get_config и подчищает
формы, которые бот так и не подтвердил (chosen_inline_result не пришёл).
"""

import html
import time

import engine
import decorators
import visual

NAME = "InlineStuff"
MODULE_NAME = "inline_stuff"
VERSION = "1.2"
AUTHOR = "FRX"
DESCRIPTION = "Настройки и обслуживание системы инлайн-форм (create_inlineform)"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/InlineStuff.py"


@decorators.on_start()
async def setup_configs():
    """Создаёт конфиги протокола инлайн-форм при первом запуске."""
    await engine.create_config(
        key='confirm_timeout',
        description='Сколько секунд ждать подтверждения формы ботом (chosen_inline_result)',
        data_type='int', default=10,
    )
    await engine.create_config(
        key='member_cache_ttl',
        description='TTL кэша проверки участника чата/канала для for_user=chat_id, сек',
        data_type='int', default=300,
    )
    await engine.create_config(
        key='clicks_per_second',
        description='Лимит обработки кликов по инлайн-формам, кликов/сек',
        data_type='int', default=5,
    )
    rate = await engine.get_config('clicks_per_second')
    engine.set_inline_click_rate(rate)


@decorators.cycle(interval='1m')
async def gc_stale_pending():
    """
    Защитная сетка: create_inlineform() сам чистит свою запись в
    _pending_forms после asyncio.wait_for (успех или TimeoutError) —
    в норме этот цикл ничего не находит. Но если ожидающая корутина
    почему-то не смогла завершиться штатно, запись может зависнуть
    навсегда и утекать памятью — здесь просто отпускаем такие Future
    с ошибкой и убираем их из реестра.
    """
    confirm_timeout = await engine.get_config('confirm_timeout')
    grace = confirm_timeout + 30
    now = time.monotonic()

    for form_id, pending in list(engine._pending_forms.items()):
        if now - pending['created_at'] < grace:
            continue
        fut = pending['future']
        if not fut.done():
            fut.set_exception(TimeoutError("Форма зависла в ожидании подтверждения и была снята сборщиком мусора"))
        engine._pending_forms.pop(form_id, None)


@decorators.cycle(interval='10m')
async def gc_stale_input_targets():
    """
    Защитная сетка для InputButton (core/inline_forms.py) — "ожидание
    ввода" (Core._pending_inputs) в норме само перекрывается/снимается
    при form.edit()/form.close(), но если модуль создал форму с
    InputButton и ни разу её не тронул (и не закрыл), запись может висеть
    сколь угодно долго — тут снимаем такие записи старше часа.
    """
    now = time.monotonic()
    stale_after = 60 * 60
    for user_id, pending in list(engine._pending_inputs.items()):
        if now - pending['created_at'] >= stale_after:
            engine._pending_inputs.pop(user_id, None)

    for chat_id, items in list(engine._pending_input_deletes.items()):
        items[:] = [item for item in items if now - item['created_at'] <= 15]
        if not items:
            engine._pending_input_deletes.pop(chat_id, None)

    for chat_id, items in list(engine._recent_bot_echoes.items()):
        items[:] = [item for item in items if now - item['seen_at'] <= 15]
        if not items:
            engine._recent_bot_echoes.pop(chat_id, None)


@decorators.command('forms')
async def list_forms_cmd(event):
    """список активных инлайн-форм (кто создал, чат, кому доступна)"""
    forms = list(engine._active_forms.values())
    if not forms:
        await engine.answer(event, visual.title('Инлайн-формы') + "\n" + visual.hint('активных форм нет'))
        return

    lines = [visual.title('Инлайн-формы', f"активных: {len(forms)}")]
    for form in forms:
        lines.append(visual.item(f"<code>{form.form_id[:8]}</code>  {html.escape(str(form.owner_module))}"))
        lines.append(visual.item(f"чат <code>{form.chat_id}</code> · for_user <code>{html.escape(repr(form.for_user))}</code>", 1))
    lines += ["", visual.hint('закрыть форму — <code>.closeform &lt;id&gt;</code> (первые 8 символов)')]
    await engine.answer(event, "\n".join(lines))


@decorators.command('closeform')
async def close_form_cmd(event):
    """принудительно закрывает инлайн-форму по первым символам её id"""
    query = engine.get_args().strip()
    if not query:
        await engine.answer(event, visual.err('Укажи id формы') + "\n" + visual.hint('список — <code>.forms</code>'))
        return

    matches = [f for f in engine._active_forms.values() if f.form_id.startswith(query)]
    if not matches:
        await engine.answer(event, visual.err('Форма не найдена') + "\n" + visual.field('id', html.escape(query)))
        return
    if len(matches) > 1:
        await engine.answer(event, visual.warn('Совпадений несколько — уточни id') + "\n" + visual.hint('список — <code>.forms</code>'))
        return

    await matches[0].close()
    await engine.answer(event, visual.ok('Форма закрыта') + "\n" + visual.field('id', html.escape(query)))
