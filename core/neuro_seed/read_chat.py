"""
name: read_chat
category: telegram
title: Прочитать чат
description: последние N сообщений чата (от старых к новым) с #id, автором, временем, вложениями и ответами. chat — id, @username, «me» (избранное), «here» (чат, где открыт диалог), +телефон или имя/название (с падежами). limit — сколько (до 100); before_id — читать раньше этого сообщения (листать назад); from_user — только сообщения этого человека («me» — мои); unread — только непрочитанные
args: chat: string, limit?: int, before_id?: int, from_user?: string, unread?: bool
example: {"chat": "Катя", "limit": 20}
keywords: прочитай чат, последние сообщения, что писали, история чата, что в чате, переписка, что мне написал, что ответила
limit: 9000
"""
from core import neuro_tg as tg

import engine


async def _entity(chat):
    try:
        ent, _how = await tg.resolve_chat(chat)
    except tg.Ambiguous as e:
        raise ValueError(tg.ambiguous_text(e, 'Не выбран чат'))
    return ent


def _who(m, me_id):
    if m.out or (me_id and m.sender_id == me_id):
        return 'я'
    s = getattr(m, 'sender', None)
    if s is None:
        return 'кто-то'
    name = ' '.join(x for x in (getattr(s, 'first_name', None), getattr(s, 'last_name', None)) if x) or getattr(s, 'title', None) or str(s.id)
    return name + (f" (@{s.username})" if getattr(s, 'username', None) else '')


def _media(m):
    for attr, title in (('photo', 'фото'), ('voice', 'голосовое'), ('video_note', 'кружок'), ('video', 'видео'), ('sticker', 'стикер'),
                        ('gif', 'GIF'), ('audio', 'аудио'), ('poll', 'опрос'), ('geo', 'геолокация'), ('contact', 'контакт')):
        if getattr(m, attr, None):
            return title
    if getattr(m, 'document', None):
        f = getattr(m, 'file', None)
        return 'файл ' + (getattr(f, 'name', None) or '')
    return 'вложение' if m.media else ''


def _line(m, me_id):
    t = (m.message or '').replace('\n', ' ⏎ ')
    parts = [f"#{m.id} [{m.date.astimezone().strftime('%d.%m %H:%M')}] {_who(m, me_id)}"]
    if m.fwd_from:
        parts.append('(переслано)')
    if m.reply_to and getattr(m.reply_to, 'reply_to_msg_id', None):
        parts.append(f"(ответ на #{m.reply_to.reply_to_msg_id})")
    med = _media(m)
    body = (f"[{med}] " if med else '') + (t[:500] + ('…' if len(t) > 500 else ''))
    if getattr(m, 'action', None) is not None and not body.strip():
        body = '[служебное: ' + type(m.action).__name__.replace('MessageAction', '') + ']'
    return ' '.join(parts) + ': ' + (body.strip() or '—')


async def run(chat: str = '', limit: int = 20, before_id: int = 0, from_user: str = '', unread: bool = False) -> str:
    if not chat:
        return "Ошибка: не указан чат"
    limit = max(1, min(int(limit or 20), 100))
    try:
        ent = await _entity(chat)
        me = await engine.client.get_me()
        kw = {'limit': limit}
        if before_id:
            kw['offset_id'] = int(before_id)
        if from_user:
            kw['from_user'] = 'me' if str(from_user).lower() in tg.ME else (await _entity(from_user))
        if unread:
            d = next((d async for d in engine.client.iter_dialogs(limit=500) if d.entity.id == getattr(ent, 'id', None)), None)
            n = d.unread_count if d else 0
            if not n:
                return f"Непрочитанных нет — {tg.describe(ent)}"
            kw['limit'] = min(n, 100)
        msgs = [m async for m in engine.client.iter_messages(ent, **kw)]
    except Exception as e:                                  # noqa: BLE001
        return f"Ошибка: {e}"
    if not msgs:
        return "Сообщений нет"
    head = f"{tg.describe(ent)} — {len(msgs)} сообщ. (#{msgs[-1].id}…#{msgs[0].id})"
    tail = f"\nСтарше — read_chat с before_id={msgs[-1].id}" if len(msgs) >= kw['limit'] else ''
    return head + '\n' + '\n'.join(_line(m, me.id) for m in reversed(msgs)) + tail
