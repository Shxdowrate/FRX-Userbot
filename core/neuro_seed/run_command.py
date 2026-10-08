"""
name: run_command
category: system
title: Команда юзербота
description: выполняет команду FRX от имени владельца (как если бы он сам написал её в чат): command — имя команды, args — аргументы строкой; результат — текст ответа команды. Без command — список доступных команд
args: command?: string, args?: string
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


async def run(command: str = '', args: str = '') -> str:
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
    if cmd in BLOCKED:
        return f"Команду «{cmd}» нейросети запускать нельзя (выключение, обновление, выгрузка, выполнение кода или сама нейросеть)."
    if cmd not in cmds:
        close = [c for c in sorted(cmds) if c.startswith(cmd[:2])][:8]
        return f"Команды «{cmd}» нет." + (f" Похожие: {', '.join(close)}." if close else '') + " Вызови run_command без аргументов — покажу список."

    # чат берём из того места, откуда пришёл вопрос к нейросети; нет такого (сайт и т.п.) — «Избранное»
    chat = 'me'
    try:
        from core.context import current_event
        ev = current_event.get()
        if ev is not None and getattr(ev, 'chat_id', None):
            chat = ev.chat_id
    except Exception:
        pass
    text = f"{prefix}{cmd}" + (f" {args}" if args else '')
    # Сообщение уходит от имени владельца — дальше его подхватывает обычный диспетчер, как будто команду набрали руками
    msg = await engine.client.send_message(chat, text)
    first, last, stable = text, text, 0
    for _ in range(14):
        await asyncio.sleep(0.7)
        cur = await engine.client.get_messages(chat, ids=msg.id)
        if cur is None:
            return f"Команда «{text}» выполнена: сообщение с командой удалено ею самой (результат — в чате)."
        now = cur.raw_text or ''
        if now != last:
            last, stable = now, 0
        elif now != first:
            stable += 1
            if stable >= 2:
                break
    if last == first:
        return (f"Команда «{text}» отправлена. Текстового ответа в сообщении не появилось — вероятно, она открыла инлайн-форму или ответила отдельным сообщением; "
                "результат смотри в чате.")
    return f"Результат «{text}»:\n{_plain(last)}"
