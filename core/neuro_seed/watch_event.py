"""
name: watch_event
category: agent
title: Ждать события
description: проснуться, когда придёт подходящее ВХОДЯЩЕЕ сообщение, и выполнить задачу: «когда Дима пришлёт отчёт — перешли Юле», «если в группе спросят про сроки — скажи мне». from_user — от кого (имя, @username, id), chat — в каком чате, contains — слова через запятую (любое из них, с падежами), files_only — только сообщения с файлом/фото; once — один раз (по умолчанию) или каждый раз; hours — сколько ждать (по умолчанию 72). Хотя бы одно из from_user / chat / contains обязательно
args: task: string, from_user?: string, chat?: string, contains?: string, files_only?: bool, once?: bool, hours?: int
example: {"task": "Перешли этот файл Юле Смирновой", "from_user": "Дима", "files_only": true}
keywords: когда напишет, когда пришлет, когда придет, если напишет, если пришлет, если спросят, как только, жди сообщени
"""
import time

from core import neuro_tg as tg


async def run(task: str = '', from_user: str = '', chat: str = '', contains: str = '', files_only: bool = False, once: bool = True, hours: int = 72) -> str:
    if not str(task).strip():
        return "Ошибка: что сделать, когда событие случится (task)?"
    if not (from_user or chat or contains):
        return "Ошибка: укажите хотя бы от кого (from_user), чат (chat) или слова (contains)"
    fields = {'once': bool(once) if once is not None else True, 'files_only': bool(files_only),
              'until': time.time() + max(1, min(int(hours or 72), 24 * 30)) * 3600}
    try:
        if from_user:
            u, _ = await tg.resolve_chat(from_user)
            fields.update(from_id=getattr(u, 'id', None), from_name=tg.describe(u))
        if chat:
            c, _ = await tg.resolve_chat(chat)
            fields.update(chat_id=tg.peer_id(c), chat_name=tg.describe(c))
    except tg.Ambiguous as e:
        return tg.ambiguous_text(e, 'Не поставлено')
    except Exception as e:                                   # noqa: BLE001
        return f"Не поставлено: {e}"
    if contains:
        fields['contains'] = [w.strip() for w in str(contains).split(',') if w.strip()][:10]
    try:
        job = tg.jobs_add('event', task, **fields)
    except ValueError as e:
        return f"Не поставлено: {e}"
    return f"Жду: {tg.job_line(job)}"
