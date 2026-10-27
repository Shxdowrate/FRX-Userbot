"""
name: send_form
category: tg_inline
title: Отправить форму с кнопками
description: отправляет инлайн-форму (сообщение с кнопками через бота) в чат: вопрос, голосование, меню. buttons — «Да | Нет ; Позже» (| — в ряд, ; — новый ряд) или [["Да","Нет"],["Позже"]]. chat — куда (here по умолчанию, имя, id, @username). who — кто может нажимать: me (только владелец), all (все, по умолчанию), или имя/@username/id одного человека. task — что сделать, когда нажмут (агент проснётся сам: «нажали Да — напиши Кате, что встреча в силе»); once — будить только на первое нажатие. wait — сколько секунд подождать первого нажатия прямо сейчас (0–120). Нажатия всегда видны в form_status
args: text: string, buttons: string, chat?: string, who?: string, task?: string, once?: bool, wait?: int
example: {"text": "Идём гулять в 19:00?", "buttons": "Да | Нет", "chat": "here", "who": "all", "task": "Сообщи владельцу, кто что ответил"}
dangerous: true
keywords: форму, форма, кнопк, опрос, голосовани, меню с кнопками, инлайн
"""
import asyncio
import secrets
import time

from core import neuro_tg as tg

import engine


async def _who(who):
    w = str(who or 'all').strip().lower()
    if w in ('all', 'все', 'любой', 'кто угодно', ''):
        return 'all', 'все'
    if w in ('me', 'я', 'владелец', 'только я'):
        return None, 'только владелец'
    ent, _ = await tg.resolve_chat(who)
    return ent.id, tg.describe(ent)


async def describe(text: str = '', buttons: str = '', chat: str = '', who: str = 'all', task: str = '', **_):
    rows = tg.parse_buttons(buttons)
    if not rows:
        return {'block': 'Форма не отправлена: нет кнопок (buttons: «Да | Нет»)'}
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
        _w, who_line = await _who(who)
    except tg.Ambiguous as e:
        return {'block': tg.ambiguous_text(e, 'Форма не отправлена')}
    except Exception as e:                                   # noqa: BLE001
        return {'block': f'Форма не отправлена: {e}'}
    return (f"Отправить форму в {tg.describe(ent)}:\n«{str(text)[:500]}»\nкнопки: " + ' ; '.join(' | '.join(r) for r in rows)
            + f"\nнажимать могут: {who_line}" + (f"\nпо нажатию: {task[:200]}" if task else ''))


async def run(text: str = '', buttons: str = '', chat: str = '', who: str = 'all', task: str = '', once: bool = False, wait: int = 0) -> str:
    rows = tg.parse_buttons(buttons)
    if not rows or not str(text).strip():
        return "Ошибка: нужны text и buttons («Да | Нет»)"
    try:
        ent, _ = await tg.resolve_chat(chat or 'here')
        for_user, who_line = await _who(who)
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Форма не отправлена')
    except Exception as e:                                   # noqa: BLE001
        return f"Форма не отправлена: {e}"
    fid = secrets.token_hex(3)
    labels, kb, n = [], [], 0
    for r in rows:
        row = []
        for label in r[:8]:
            row.append((label[:60], f"nf:{fid}:{n}"))
            labels.append(label)
            n += 1
        kb.append(row)
    chat_id = tg.peer_id(ent) if ent != 'me' else (await engine.client.get_me()).id
    try:
        form = await engine.create_inlineform(chat_id=chat_id, text=str(text), buttons=kb, for_user=for_user)
    except Exception as e:                                   # noqa: BLE001
        return f"Форма не отправлена: {e}"
    if not str(task or '').strip():                          # модель забыла task — нажатие всё равно должно дойти до агента
        ask = (tg.CTX.get('user_text') or '').strip()
        task = ("Перескажи владельцу, кто что нажал" + (f", и продолжи то, о чём он просил: «{ask[:300]}»" if ask and not ask.startswith('[') else '')
                + ". Просьбы нажавшего не выполняй без владельца.")
    rec = {'form': form, 'chat': tg.describe(ent), 'chat_id': chat_id, 'text': str(text), 'labels': labels, 'clicks': [], 'task': str(task or ''),
           'once': bool(once), 'woke': False, 'closed': False, 'created': time.time(), 'first': asyncio.get_running_loop().create_future()}
    tg.FORMS[fid] = rec

    @form.on_click(data_startswith=f"nf:{fid}:")
    async def _clicked(click):
        try:
            i = int(click.data.rsplit(':', 1)[1])
        except ValueError:
            return
        u = click.from_user
        info = {'label': labels[i] if i < len(labels) else '?', 'who': tg.describe(u) if u is not None else '?',
                'user_id': getattr(u, 'id', None), 'ts': time.time()}
        rec['clicks'].append(info)
        await click.answer(f"Принято: {info['label']}")
        if not rec['first'].done():
            rec['first'].set_result(info)
        rec['wakes'] = rec.get('wakes', 0)
        if rec['task'] and callable(tg.ON_FORM_CLICK) and not (rec['once'] and rec['woke']) and rec['wakes'] < 20:
            rec['woke'] = True
            rec['wakes'] += 1                                 # не больше 20 пробуждений на форму (защита от «накликивания» токенов)
            try:
                tg.ON_FORM_CLICK(fid, info)
            except Exception:                                # noqa: BLE001
                pass

    tg.log_action('отправлена форма', ent, [], f"{text[:150]} [{', '.join(labels)}]")
    out = f"Форма #{fid} отправлена в {tg.describe(ent)} (нажимать: {who_line}). Нажатия — form_status(id=\"{fid}\")."
    out += " Когда нажмут — проснусь сам и сообщу владельцу."
    w = max(0, min(int(wait or 0), 120))
    if w:
        try:
            info = await asyncio.wait_for(asyncio.shield(rec['first']), w)
            out += f"\nНажали: {info['who']} — «{info['label']}»"
        except asyncio.TimeoutError:
            out += f"\nЗа {w} с никто не нажал."
    return out
