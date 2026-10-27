"""
name: bot_interact
category: tg_groups
title: Работа с ботами
description: общение с ботами. action: start (запустить бота, param — параметр /start), command (отправить команду, text — например «/help»), buttons (показать кнопки последнего сообщения бота или сообщения id), click (нажать кнопку: button — текст кнопки или номер; id — сообщение, по умолчанию последнее с кнопками), wait (дождаться и прочитать ответ бота). bot — @username, имя или here (here — чат, где открыт .n: так нажимаются кнопки инлайн-форм, которые открыла команда)
args: action: string, bot: string, text?: string, param?: string, button?: string, id?: string
example: {"action": "click", "bot": "@SomeBot", "button": "Да"}
dangerous: true
keywords: бот, бота, ботом, нажми кнопку, запусти бота, /start, команду боту
"""
import asyncio

from core import neuro_tg as tg

import engine


def _buttons(m):
    rows = getattr(m, 'buttons', None) or []
    flat = [b for row in rows for b in row]
    return flat


async def _last_with_buttons(ent, mid=None):
    if mid:
        return await engine.client.get_messages(ent, ids=int(mid))
    async for m in engine.client.iter_messages(ent, limit=15):
        if m.buttons and (not m.out or getattr(m, 'via_bot_id', None)):     # ответ бота или инлайн-форма юзербота (она «исходящая»)
            return m
    return None


async def _answer(ent, after_id, wait=8):
    """Ждём ответ бота (до wait секунд) и возвращаем его сообщения после after_id."""
    for _ in range(wait * 2):
        await asyncio.sleep(0.5)
        msgs = [m async for m in engine.client.iter_messages(ent, min_id=after_id, limit=10) if not m.out]
        if msgs:
            out = []
            for m in reversed(msgs):
                line = f"#{m.id}: {(m.message or '[вложение]')[:1500]}"
                bs = _buttons(m)
                if bs:
                    line += '\nкнопки: ' + ' | '.join(f"{i}. {b.text}" for i, b in enumerate(bs, 1))
                out.append(line)
            return '\n'.join(out)
    return 'бот пока не ответил'


async def describe(action: str = '', bot: str = '', text: str = '', param: str = '', button: str = '', id: str = '', **_):
    a = str(action).lower()
    if a in ('buttons', 'wait'):
        return {'safe': True}               # только посмотреть — подтверждать нечего
    try:
        ent, how = await tg.resolve_chat(bot or 'here')
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не выбран бот')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Ошибка: {e}'}
    if a == 'start':
        return f"Запустить {tg.describe(ent)}" + (f" с параметром «{param}»" if param else '')
    if a == 'command':
        return f"Отправить {tg.describe(ent)}: «{text}»"
    if a == 'click':
        return f"Нажать кнопку «{button}» в {tg.describe(ent)}"
    return f"{a} — {tg.describe(ent)}"


async def run(action: str = '', bot: str = '', text: str = '', param: str = '', button: str = '', id: str = '') -> str:
    a = str(action).lower().strip()
    try:
        ent, _ = await tg.resolve_chat(bot or 'here')
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не выбран бот')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    try:
        last = await engine.client.get_messages(ent, limit=1)
        last_id = last[0].id if last else 0
        if a == 'start':
            await engine.client.send_message(ent, '/start' + (f" {param}" if param else ''))
            return await _answer(ent, last_id)
        if a == 'command':
            if not str(text).strip():
                return "Ошибка: какую команду отправить (text)?"
            m = await engine.client.send_message(ent, str(text))
            tg.log_action('команда боту', ent, [m.id], text, undo='delete')
            return await _answer(ent, m.id)
        if a == 'buttons':
            m = await _last_with_buttons(ent, tg.msg_ids(id)[0] if id else None)
            if m is None or not m.buttons:
                return "Сообщений с кнопками не нашлось"
            return f"#{m.id}: {(m.message or '')[:500]}\nкнопки: " + ' | '.join(f"{i}. {b.text}" for i, b in enumerate(_buttons(m), 1))
        if a == 'click':
            m = await _last_with_buttons(ent, tg.msg_ids(id)[0] if id else None)
            if m is None or not m.buttons:
                return "Нет сообщения с кнопками"
            bs = _buttons(m)
            b = str(button).strip()
            idx = int(b) - 1 if b.isdigit() and 0 < int(b) <= len(bs) else next((i for i, x in enumerate(bs) if x.text.strip().lower() == b.lower()), None)
            if idx is None:
                idx = next((i for i, x in enumerate(bs) if b.lower() in x.text.lower()), None)
            if idx is None:
                return "Нет такой кнопки. Есть: " + ' | '.join(f"{i}. {x.text}" for i, x in enumerate(bs, 1))
            if getattr(bs[idx], 'url', None):
                return f"Кнопка «{bs[idx].text}» — это ссылка: {bs[idx].url}"
            res = await m.click(idx)
            tg.log_action(f'нажата кнопка «{bs[idx].text}»', ent, [m.id])
            alert = getattr(res, 'message', None)
            after = await _answer(ent, m.id, wait=4)
            return (f"Нажато «{bs[idx].text}»" + (f"; бот ответил всплывашкой: {alert}" if alert else '')
                    + f"\n{after if after != 'бот пока не ответил' else ''}").strip()
        if a == 'wait':
            return await _answer(ent, last_id - 1, wait=10)
    except Exception as e:                                   # noqa: BLE001
        return f"Не получилось: {e}"
    return "Неизвестное действие: start, command, buttons, click, wait"
