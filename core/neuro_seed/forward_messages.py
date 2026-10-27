"""
name: forward_messages
category: telegram
title: Переслать
description: пересылает сообщения из одного чата в другой. ids — номера (#id из read_chat / find_files), «reply» — сообщение, на которое ответили командой, или пусто — тогда берётся последнее подходящее (фото/видео/файл — по просьбе); from_chat — откуда (по умолчанию here); to — куда (имя, id, @username, me). copy=true — отправить копией, без «переслано от»
args: to: string, ids: string, from_chat?: string, copy?: bool
example: {"to": "Юля", "ids": "reply"}
dangerous: true
keywords: перешли, пересл, форвард
"""
from core import neuro_tg as tg

import engine


import re

KINDS = (('photo', ('фот', 'картин', 'скрин', 'снимок', 'изображ', 'photo')), ('video', ('видео', 'ролик', 'video')),
         ('voice', ('голосов', 'войс', 'voice')), ('document', ('файл', 'документ', 'отчет', 'отчёт', 'pdf', 'doc')),
         ('sticker', ('стикер',)), ('audio', ('трек', 'песн', 'музык', 'аудио')))


async def _guess(src, hint):
    """ids не указаны («фото, которое я скинул сюда») — берём последние подходящие сообщения из чата-источника сами.
    → (ids, пояснение) или ([], '')"""
    low = (hint or '').lower().replace('ё', 'е')
    kind = next((k for k, words in KINDS if any(w in low for w in words)), None)
    m = re.search(r'последни[ех]\s+(\d{1,2})', low)
    n = int(m.group(1)) if m else 1
    me = (await engine.client.get_me()).id
    found = []
    async for msg in engine.client.iter_messages(src, limit=40):
        if getattr(msg, 'via_bot_id', None) or (msg.message or '').startswith('.'):
            continue                                         # команды и формы юзербота — не то, что просили переслать
        if getattr(msg, 'sender_id', None) not in (me, None) and getattr(getattr(msg, 'sender', None), 'bot', False):
            continue                                         # ответы ботов
        if kind is None and not (msg.media or msg.message):
            continue
        if kind is not None and not getattr(msg, kind, None):
            continue
        found.append(msg.id)
        if len(found) >= n:
            break
    if not found:
        return [], ''
    what = {'photo': 'фото', 'video': 'видео', 'voice': 'голосовое', 'document': 'файл', 'sticker': 'стикер', 'audio': 'аудио'}.get(kind, 'сообщение')
    return sorted(found), f" (ids не указаны — взял последнее: {what} {', '.join('#' + str(i) for i in found)})"


async def _resolve(to, from_chat):
    src, _ = await tg.resolve_chat(from_chat or 'here')
    dst, how = await tg.resolve_chat(to)
    return src, dst, how


async def describe(to: str = '', ids: str = '', from_chat: str = '', copy: bool = False, **_):
    nums = tg.msg_ids(ids)
    try:
        src, dst, how = await _resolve(to, from_chat)
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не переслано')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Не переслано: {e}'}
    if not nums:
        nums, _note = await _guess(src, f"{ids} {tg.CTX.get('user_text') or ''}")
    if not nums:
        return {'block': 'Не переслано: не нашёл, что пересылать — укажи ids (#id из read_chat / find_files)'}
    return (f"{'Скопировать' if copy else 'Переслать'} {len(nums)} сообщ. ({', '.join('#' + str(i) for i in nums[:10])}) из {tg.describe(src)}\n→ {tg.describe(dst)}"
            + (' (найден по имени — проверьте)' if how == 'name' else ''))


async def run(to: str = '', ids: str = '', from_chat: str = '', copy: bool = False) -> str:
    nums = tg.msg_ids(ids)
    try:
        src, dst, _how = await _resolve(to, from_chat)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не переслано')
    except Exception as e:                                   # noqa: BLE001
        return f"Не переслано: {e}"
    note = ''
    if not nums:
        nums, note = await _guess(src, f"{ids} {tg.CTX.get('user_text') or ''}")
    if not nums:
        return "Не переслано: не нашёл, что пересылать — укажи ids (#id из read_chat / find_files)"
    if copy:
        msgs = [m for m in await engine.client.get_messages(src, ids=nums) if m]
        sent = [await engine.client.send_message(dst, m) for m in msgs]
    else:
        sent = await engine.client.forward_messages(dst, nums, from_peer=src)
        sent = sent if isinstance(sent, list) else [sent]
    sent = [m for m in sent if m]
    tg.log_action('переслано' if not copy else 'скопировано', dst, [m.id for m in sent], f"{len(sent)} сообщ. из {tg.describe(src)}", undo='delete')
    return f"Готово: {len(sent)} сообщ. → {tg.describe(dst)}{note}"
