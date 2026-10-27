"""
name: click_button
category: telegram
title: Нажать кнопку
description: нажимает инлайн-кнопку под сообщением (бот, группа, канал, инлайн-форма, в т.ч. формы команд FRX после run_command) от имени владельца и показывает, что изменилось: всплывающий ответ, правку сообщения, новые сообщения с кнопками. chat — где (here по умолчанию, имя, @бот, id); id — сообщение (#id, «reply», по умолчанию последнее сообщение с кнопками); button — текст кнопки, номер по счёту (с 1) или «ряд,кнопка». Без button — только показать кнопки
args: chat?: string, id?: string, button?: string
example: {"chat": "@SomeBot", "button": "Подтвердить"}
dangerous: true
keywords: нажми кнопку, нажми, кликни, тыкни, выбери вариант, кнопк
"""
import re

from core import neuro_tg as tg

import engine


async def _target(chat, id):
    ent, _ = await tg.resolve_chat(chat or 'here')
    if id:
        nums = tg.msg_ids(id)
        if nums:
            return ent, await engine.client.get_messages(ent, ids=nums[0])
    async for m in engine.client.iter_messages(ent, limit=20):
        if m.buttons and (not m.out or getattr(m, 'via_bot_id', None)):
            return ent, m
    return ent, None


def _btn(button):
    b = str(button or '').strip()
    if re.fullmatch(r'\d+\s*[.)]', b):                    # «2.» / «2)» — номер из списка «1. … | 2. …»
        b = re.sub(r'\D', '', b)
    m = re.fullmatch(r'(\d+)\s*[,;:]\s*(\d+)', b)
    if m:
        return (int(m.group(1)) - 1, int(m.group(2)) - 1)
    if b.isdigit():
        return int(b) - 1
    return b


async def describe(chat: str = '', id: str = '', button: str = '', **_):
    if not str(button).strip():
        return {'safe': True}                                # только показать кнопки
    try:
        ent, m = await _target(chat, id)
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не нажато')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Не нажато: {e}'}
    if m is None:
        return {'block': 'Не нажато: сообщения с кнопками не нашлось'}
    if engine.find_button(m, _btn(button)) is None:
        return {'block': f"Не нажато: кнопки «{button}» нет. {tg.buttons_text(m)}"}
    return f"Нажать «{button}» под сообщением #{m.id} в {tg.describe(ent)}:\n«{(m.raw_text or '')[:300]}»"


async def run(chat: str = '', id: str = '', button: str = '') -> str:
    try:
        ent, m = await _target(chat, id)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не нажато')
    except Exception as e:                                   # noqa: BLE001
        return f"Ошибка: {e}"
    if m is None:
        return "Сообщения с кнопками не нашлось — укажи chat и id"
    if not str(button).strip():
        return tg.msg_line(m)
    b = _btn(button)
    coords = engine.find_button(m, b)
    if coords is None:
        return f"Кнопки «{button}» нет. {tg.buttons_text(m)}"
    btn = m.reply_markup.rows[coords[0]].buttons[coords[1]]
    if getattr(btn, 'url', None):
        return f"«{btn.text}» — это ссылка, а не действие: {btn.url}"
    before = m.raw_text or ''
    try:
        ans = await engine.click(m, coords, wait='answer', timeout=8)
    except Exception as e:                                   # noqa: BLE001
        return f"Не нажато: {type(e).__name__}: {e}"
    tg.log_action(f"нажата кнопка «{btn.text}»", ent, [m.id])
    out = [f"Нажато «{btn.text}» (#{m.id} в {tg.describe(ent)})"]
    alert = getattr(ans, 'message', None)
    if alert:
        out.append(f"всплывающий ответ: {alert}")
    if getattr(ans, 'url', None):
        out.append(f"бот открыл ссылку: {ans.url}")
    changes = await tg.chat_changes(ent, m, before, wait=6)
    out += changes
    if changes:
        tg.CTX.setdefault('attached', []).append(f"после нажатия «{btn.text}»: " + '; '.join(c.split(':')[0] for c in changes)[:120])
    if len(out) == 1:
        out.append('видимых изменений нет (бот мог ответить позже — посмотри read_chat)')
    return '\n'.join(out)
