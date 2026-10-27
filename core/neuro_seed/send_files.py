"""
name: send_files
category: tg_files
title: Отправить файлы
description: отправляет файлы/сообщения из Telegram другому человеку или в чат. ids — "found" (всё, что нашёл find_files), номера из списка find_files («1,3»), или #id сообщений вместе с from_chat; to — кому (имя, id, @username, here, me). copy=true (по умолчанию) — как свои, без «переслано от»; copy=false — переслать. caption — подпись к первому
args: to: string, ids?: string, from_chat?: string, caption?: string, copy?: bool
example: {"to": "Юля", "ids": "found", "caption": "Отчёты от Димы"}
dangerous: true
keywords: скинь, отправь файл, пришли файл, перекинь, отправь документ
"""
from core import neuro_tg as tg

import engine


async def _items(ids, from_chat):
    """→ [(entity_чата, [msg_id, …])] в порядке находки."""
    raw = str(ids or 'found').strip().lower()
    if from_chat:
        src, _ = await tg.resolve_chat(from_chat)
        return [(src, tg.msg_ids(ids))]
    if not tg.FOUND:
        raise ValueError('нечего отправлять: сначала найдите файлы (find_files) или укажите from_chat и ids')
    if raw in ('found', 'all', 'все', 'найденное', 'last', ''):
        picked = list(tg.FOUND)
    else:
        nums = tg.msg_ids(ids)
        picked = [tg.FOUND[n - 1] for n in nums if 1 <= n <= len(tg.FOUND)]
        if not picked:
            raise ValueError(f'номера {ids} не из списка find_files (там {len(tg.FOUND)})')
    out = []
    for peer, mid in picked:
        if out and out[-1][0] == peer:
            out[-1][1].append(mid)
        else:
            out.append((peer, [mid]))
    res = []
    for peer, mids in out:
        res.append((await engine.client.get_entity(peer) if peer != 'me' else 'me', mids))
    return res


async def describe(to: str = '', ids: str = 'found', from_chat: str = '', caption: str = '', copy: bool = True, **_):
    try:
        dst, how = await tg.resolve_chat(to)
        items = await _items(ids, from_chat)
        msgs = []
        for src, mids in items:
            msgs += [m for m in await engine.client.get_messages(src, ids=mids) if m]
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Не отправлено')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Не отправлено: {e}'}
    lst = '\n'.join(f"• {tg.file_title(m)}" for m in msgs[:10]) + (f"\n… и ещё {len(msgs) - 10}" if len(msgs) > 10 else '')
    return (f"{'Отправить' if copy else 'Переслать'} {len(msgs)} → {tg.describe(dst)}" + (' (найден по имени — проверьте)' if how == 'name' else '')
            + f":\n{lst}" + (f"\nподпись: «{caption[:200]}»" if caption else ''))


async def run(to: str = '', ids: str = 'found', from_chat: str = '', caption: str = '', copy: bool = True) -> str:
    try:
        dst, _how = await tg.resolve_chat(to)
        items = await _items(ids, from_chat)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не отправлено')
    except Exception as e:                                   # noqa: BLE001
        return f"Не отправлено: {e}"
    sent, first = [], True
    for src, mids in items:
        msgs = [m for m in await engine.client.get_messages(src, ids=mids) if m]
        if not copy:
            r = await engine.client.forward_messages(dst, [m.id for m in msgs], from_peer=src)
            sent += r if isinstance(r, list) else [r]
            continue
        for m in msgs:
            cap = caption if (first and caption) else (m.message or '')
            if m.media:
                sent.append(await engine.client.send_file(dst, m.media, caption=cap))
            else:
                sent.append(await engine.client.send_message(dst, cap or '…'))
            first = False
    if caption and not copy:
        sent.append(await engine.client.send_message(dst, caption))
    sent = [m for m in sent if m]
    tg.log_action('отправлены файлы', dst, [m.id for m in sent], f"{len(sent)} шт." + (f", подпись: {caption[:100]}" if caption else ''), undo='delete')
    return f"Отправлено {len(sent)} → {tg.describe(dst)}"
