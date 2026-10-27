"""
name: run_command
category: system
title: Команда юзербота
description: запускает команду модуля/юзербота от имени владельца, как будто он сам её набрал (что есть — module_commands). command — имя команды, args — аргументы строкой, chat — где запустить (по умолчанию чат, где открыт .n; иначе Избранное). Результат — что появилось в чате: изменённое сообщение команды, ответы, инлайн-формы с кнопками (нажать — bot_interact click). Без command — список команд
args: command?: string, args?: string, chat?: string
example: {"command": "cfg", "args": "neurodash"}
dangerous: true
keywords: выполни команду, запусти команду, команда юзербота, .help, .modules, .info, .cfg, .ping, покажи модули командой, покажи логи
limit: 3500
"""
import asyncio
import re

import engine

# команды, которые модель запускать не может в принципе: выключение/обновление/выгрузка, выполнение кода и сама нейросеть (рекурсия)
BLOCKED = {'exit', 'restart', 'rs', 'update', 'upd', 'unloadmod', 'ulm', 'restorebackup', 'eval', 'ev', 'exec', 'neuro', 'n', 'sk', 'systemkeeper'}


def _plain(text):
    return re.sub(r'\s+\n', '\n', (text or '').strip())


async def describe(command: str = '', args: str = '', chat: str = '', **_):
    prefix = await engine.get_prefix()
    c = (command or '').strip()
    if not c:
        return {'safe': True}                                # только список команд
    if c.startswith(prefix):
        c = c[len(prefix):]
    return f"Выполнить команду: {prefix}{c}" + (f" {args}" if args else '')


async def run(command: str = '', args: str = '', chat: str = '') -> str:
    prefix = await engine.get_prefix()
    cmds = engine.command_map
    command = (command or '').strip()
    if not command:
        names = sorted(c for c in cmds if c not in BLOCKED)
        return f"Префикс команд: «{prefix}». Доступные команды (алиасы): " + ', '.join(names) + f"\nЗапрещены для нейросети: {', '.join(sorted(BLOCKED))}."
    if command.startswith(prefix):
        command = command[len(prefix):]
    parts = command.split(maxsplit=1)
    cmd = parts[0].lower()
    if len(parts) > 1:                                  # команда пришла вместе с аргументами: «cfg neurodash»
        args = (parts[1] + ' ' + (args or '')).strip()
    args = (args or '').strip()
    tools = getattr(getattr(engine, 'neuro', None), 'tools', None)
    if tools is not None and cmd not in cmds and any(t['name'] == cmd for t in tools.list_tools()):
        # модель перепутала: позвала свой инструмент как команду юзербота (run_command("click_button", "chat=…&id=…"))
        targs = _tool_args(args)
        res = await tools.run(cmd, targs)
        return f"(«{cmd}» — это твой инструмент, а не команда; вызвал его сам с {targs})\n" + str(getattr(res, 'text', res))
    if cmd in BLOCKED:
        return f"Команду «{cmd}» нейросети запускать нельзя (выключение, обновление, выгрузка, выполнение кода или сама нейросеть)."
    if cmd not in cmds:
        close = [c for c in sorted(cmds) if c.startswith(cmd[:2])][:8]
        return f"Команды «{cmd}» нет." + (f" Похожие: {', '.join(close)}." if close else '') + " Вызови run_command без аргументов — покажу список."

    # где запускать: явно указанный чат → чат, где открыт .n → «Избранное». В фоне (событие в чужом чате) — только явно или Избранное.
    from core import neuro_tg as tg
    if chat:
        try:
            ent, _ = await tg.resolve_chat(chat)
        except tg.Ambiguous as e:
            return tg.ambiguous_text(e, 'Команда не запущена')
        except Exception as e:                               # noqa: BLE001
            return f"Команда не запущена: {e}"
        where = ent
    else:
        where = tg.CTX.get('cmd_chat') or 'me'
        if where != 'me':                                    # .n вызвали в личке с человеком — команды туда не шлём (ему не нужны наши меню)
            try:
                e = await engine.client.get_entity(where)
                me = await engine.client.get_me()
                if hasattr(e, 'first_name') and not getattr(e, 'bot', False) and e.id != me.id:
                    where = 'me'
            except Exception:                                # noqa: BLE001
                pass
    text = f"{prefix}{cmd}" + (f" {args}" if args else '')
    msg = await engine.client.send_message(where, text)
    # Telegram не присылает клиенту событие о его же отправленном сообщении — поэтому раньше FRX команду «не видел».
    # Отдаём сообщение диспетчеру сами, как будто оно пришло обычным путём.
    try:
        from telethon import events
        ev = events.NewMessage.Event(msg)
        try:
            ev._set_client(engine.client)
        except Exception:                                    # noqa: BLE001
            ev._client = engine.client
        await asyncio.wait_for(engine._on_new_message(ev), 90)
    except asyncio.TimeoutError:
        pass                                                 # долгая команда — смотрим, что успело появиться
    except Exception as e:                                   # noqa: BLE001
        return f"Команда «{text}» отправлена, но не запустилась: {type(e).__name__}: {e}"
    return await _result(where, msg, text)


def _tool_args(args):
    """«chat=-100…&id=43&button=2» / «chat=x id=43 button=2» / JSON → dict."""
    import json
    a = (args or '').strip()
    if a.startswith('{'):
        try:
            return json.loads(a)
        except ValueError:
            pass
    return {k: v.strip().strip('"\'') for k, v in re.findall(r'(\w+)\s*[=:]\s*("[^"]*"|[^&\s,]+)', a)}


def _buttons(m):
    rows = getattr(m, 'buttons', None) or []
    flat = [b.text for row in rows for b in row if getattr(b, 'text', None)]
    return (' · кнопки: ' + ' | '.join(f"{i}. {t}" for i, t in enumerate(flat, 1))) if flat else ''


def _kind(m):
    for attr, title in (('photo', 'фото'), ('voice', 'голосовое'), ('video', 'видео'), ('sticker', 'стикер'), ('gif', 'GIF')):
        if getattr(m, attr, None):
            return f"[{title}] "
    if getattr(m, 'document', None):
        f = getattr(m, 'file', None)
        return f"[файл {getattr(f, 'name', None) or ''}] "
    return ''


async def _result(where, msg, text):
    """Ждём, пока чат «успокоится» (до ~10 с), и показываем всё, что команда оставила: правку своего сообщения и новые сообщения."""
    seen, stable = None, 0
    cur, newer = msg, []
    for _ in range(20):
        await asyncio.sleep(0.5)
        cur = await engine.client.get_messages(where, ids=msg.id)
        newer = [m async for m in engine.client.iter_messages(where, min_id=msg.id, limit=8)]
        state = ((cur.raw_text if cur else None), tuple((m.id, m.raw_text, bool(m.buttons)) for m in newer))
        if state == seen:
            stable += 1
            if stable >= 3:
                break
        else:
            seen, stable = state, 0
    out = []
    if cur is None:
        out.append("сообщение с командой удалено (команда убрала его сама)")
    elif (cur.raw_text or '') != text:
        out.append(f"#{cur.id} (команда переписала своё сообщение): {_kind(cur)}{_plain(cur.raw_text)[:2500]}{_buttons(cur)}")
    for m in reversed(newer):
        who = 'я' if m.out else 'чат'
        via = ' через бота (инлайн-форма)' if getattr(m, 'via_bot_id', None) else ''
        out.append(f"#{m.id} {who}{via}: {_kind(m)}{_plain(m.raw_text)[:2500] or '—'}{_buttons(m)}")
    if not out:
        return f"Команда «{text}» запущена, но в чате ничего не изменилось (возможно, ещё работает — посмотри чат позже через read_chat)."
    res = f"Результат «{text}»:\n" + '\n'.join(out)
    with_btn = [m for m in ([cur] if cur is not None else []) + list(newer) if getattr(m, 'buttons', None)]
    if with_btn:
        from core import neuro_tg as tg
        m = with_btn[-1]
        flat = [b.text for row in (m.buttons or []) for b in row if getattr(b, 'text', None)]
        tg.CTX.setdefault('attached', []).append(f"сообщение #{m.id} с кнопками: {' | '.join(flat)[:120]}")
        res += (f"\nЭто форма команды, НЕ твоя форма (form_status её не знает). Нажать её кнопку — click_button("
                f"chat=\"{tg.peer_id(where) if where != 'me' else 'me'}\", id=\"{m.id}\", button=\"текст или номер\"); "
                "после нажатия он покажет, что изменилось, — жми дальше, пока не дойдёшь до цели.")
    return res
