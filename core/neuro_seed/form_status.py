"""
name: form_status
category: tg_inline
title: Форма: нажатия и правка
description: что нажали в форме, отправленной send_form (кто, какую кнопку, когда); можно изменить текст/кнопки (text, buttons) или закрыть форму (close=true — убрать кнопки, итог остаётся). Без id — список форм этого сеанса
args: id?: string, text?: string, buttons?: string, close?: bool
example: {"id": "a1b2c3"}
keywords: кто нажал, что нажали, результаты опроса, итоги голосования, закрой форму
"""
import time

from core import neuro_tg as tg


def _clicks(rec):
    if not rec['clicks']:
        return 'нажатий нет'
    lines = [f"{time.strftime('%H:%M:%S', time.localtime(c['ts']))} {c['who']} — «{c['label']}»" for c in rec['clicks']]
    tally = {}
    for c in rec['clicks']:
        tally[c['label']] = tally.get(c['label'], 0) + 1
    return '\n'.join(lines) + '\nитого: ' + ', '.join(f"{k}: {v}" for k, v in tally.items())


async def run(id: str = '', text: str = '', buttons: str = '', close: bool = False) -> str:
    if not id:
        if not tg.FORMS:
            return "Твоих форм (send_form) нет. Кнопки под ответом команды или бота нажимает click_button(chat, id, button)."
        return '\n'.join(f"#{k} в {r['chat']}: «{r['text'][:80]}» · нажатий {len(r['clicks'])}{' · закрыта' if r['closed'] else ''}"
                         for k, r in tg.FORMS.items())
    rec = tg.FORMS.get(str(id).lstrip('#'))
    if rec is None:
        return f"Формы #{id} нет"
    if text or buttons or close:
        new_text = str(text) if text else rec['text']
        kb = None
        if buttons and not close:
            rows = tg.parse_buttons(buttons)
            rec['labels'][:] = [b for r in rows for b in r]
            fid, n, kb = str(id).lstrip('#'), 0, []
            for r in rows:
                row = []
                for label in r:
                    row.append((label[:60], f"nf:{fid}:{n}"))
                    n += 1
                kb.append(row)
        elif not close:
            kb = [[(l[:60], f"nf:{str(id).lstrip('#')}:{i}")] for i, l in enumerate(rec['labels'])]
        try:
            await rec['form'].edit(text=new_text, buttons=kb if not close else [])
        except Exception as e:                               # noqa: BLE001
            return f"Не получилось изменить форму: {e}"
        rec['text'] = new_text
        if close:
            rec['closed'] = True
            rec['task'] = ''
    return f"Форма #{str(id).lstrip('#')} в {rec['chat']}: «{rec['text'][:200]}»\n" + _clicks(rec)
