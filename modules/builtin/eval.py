"""
modules/builtin/eval.py

.eval <код> / .ev <код> / .exec <код> — выполняет Python-код (в т.ч. с await).
Код можно передать: аргументом команды, ответом (reply) на сообщение с
текстом кода, либо ответом на .py/.txt файл с кодом.

Результат:
  - если в коде есть явный `return значение` — результатом будет именно оно
    (код заворачивается в текст функции ДО разбора, поэтому верхнеуровневый
    return допустим);
  - иначе, если последняя строка кода — выражение (в т.ч. вызов последней
    объявленной в коде функции), — результатом будет его значение.

Сообщение — инлайн-форма (engine.create_inlineform) с тремя кнопками:
  ▸ Редактировать    — InputButton с hint=<текущий код>, позволяет
                        поправить код прямо в поле ввода и запустить снова
  ▸ Повторить         — выполняет тот же код ещё раз
  ▸ В .py файл        — сохраняет текущий код в .py и отправляет в этот чат

ВНИМАНИЕ: команда не имеет никаких ограничений — исполняется буквально
любой Python-код с полным доступом к движку (engine, db, client Telethon
и т.д.), файловой системе и сети, включая ЛЮБЫЕ импорты. Запустить её может
владелец аккаунта и тот, кому выдан доступ к модулю eval (модуль помечен как
критически опасный, см. .access), — выдавайте его только тем, кому доверяете как себе.
"""

import ast
import io
import os
import sys
import time
import textwrap
import traceback
import contextlib
from pathlib import Path

from core.paths import FILES_DIR

import engine
import decorators
import visual
import db
import cache
import waiters
import files
import accounts
import time_utils

NAME = "Eval"
MODULE_NAME = "eval"
VERSION = "2.2"
AUTHOR = "FRX"
DESCRIPTION = "Выполнение произвольного Python-кода из чата (.eval/.ev/.exec), инлайн, без ограничений"


def _env(event):
    """
    Пространство имён кода внутри .eval — те же импорты, что доступны
    любому модулю (engine/visual/db/...), плюс сам event и стандартные
    модули, которые обычно нужны под рукой. Словарь передаётся как
    globals в exec()/compile() — Python сам подставляет туда реальный
    '__builtins__', так что ЛЮБЫЕ `import ...` внутри кода .eval работают
    без ограничений, не только перечисленные ниже готовые объекты.
    """
    return {
        'event': event,
        'engine': engine,
        'decorators': decorators,
        'visual': visual,
        'db': db,
        'cache': cache,
        'waiters': waiters,
        'files': files,
        'accounts': accounts,
        'time_utils': time_utils,
        'client': engine.client,
        'asyncio': __import__('asyncio'),
        'os': __import__('os'),
        'sys': sys,
        're': __import__('re'),
        'json': __import__('json'),
    }


async def _aexec(code: str, env: dict):
    """
    Оборачивает код в текст async-функции __frx_aexec__ и исполняет —
    так top-level `await` внутри кода работает, как и раньше. код теперь парсится УЖЕ ВНУТРИ обёртки (func_src), а не сам по
    себе через ast.parse(code, mode='exec') — благодаря этому
    верхнеуровневый `return` в коде .eval больше не синтаксическая
    ошибка (внутри функции он абсолютно легален), а последняя строка,
    если это просто выражение (и она ещё не return), по-прежнему
    автоматически подменяется на `return <выражение>`.
    """
    indented = textwrap.indent(code, '    ') if code.strip() else '    pass'
    func_src = f"async def __frx_aexec__():\n{indented}\n"
    tree = ast.parse(func_src, mode='exec')
    func_def = tree.body[0]

    if func_def.body and isinstance(func_def.body[-1], ast.Expr):
        last = func_def.body[-1]
        func_def.body[-1] = ast.copy_location(ast.Return(value=last.value), last)

    ast.fix_missing_locations(tree)
    code_obj = compile(tree, '<frx_eval>', 'exec')

    exec(code_obj, env)
    return await env['__frx_aexec__']()


async def _run_text(event, code: str) -> str:
    """Выполняет код, возвращает готовый HTML-текст сообщения (Code / [Output] / Result)."""
    env = _env(event)
    buf = io.StringIO()
    code_display = _esc(code)

    error = None
    result = None
    try:
        with contextlib.redirect_stdout(buf):
            result = await _aexec(code, env)
    except Exception:
        error = traceback.format_exc()

    output = buf.getvalue()

    lines = [
        visual.title('Eval'),
        visual.section('Код'),
        f"<code>{code_display}</code>",
        "",
    ]
    if output:
        lines += [
            visual.section('Вывод'),
            f"<code>{_esc(output[-1500:])}</code>",
            "",
        ]
    if error:
        lines += [
            visual.err('Ошибка'),
            f"<code>{_esc(error[-2500:])}</code>",
        ]
    else:
        lines += [
            visual.section('Результат'),
            f"<code>{_esc(repr(result))}</code>",
        ]
    return "\n".join(lines)


async def _resolve_code(event) -> str:
    """Код из аргумента команды, либо (ответом) из текста сообщения, либо из .py/.txt файла."""
    code = engine.get_args()
    if code:
        return code

    if event.is_reply:
        replied = await event.get_reply_message()
        if replied:
            if replied.document:
                # Document.filename в Telethon не существует — имя в Message.file.name
                fname = ((replied.file.name if replied.file else '') or '').lower()
                if fname.endswith('.py') or fname.endswith('.txt'):
                    path = await replied.download_media()
                    try:
                        return Path(path).read_text(encoding='utf-8')
                    finally:
                        try:
                            if path and os.path.exists(path):
                                os.remove(path)
                        except Exception:
                            pass
            if replied.raw_text:
                return replied.raw_text

    return ''


def _eval_buttons(code: str):
    return [
        [engine.InputButton(visual.btn('Редактировать'), "eval_edit", hint=code)],
        [(visual.btn('Повторить'), "eval_repeat")],
        [(visual.btn('В .py файл'), "eval_tofile")],
    ]


async def _present(event, code: str):
    text = await _run_text(event, code)

    try:
        form = await engine.create_inlineform(
            chat_id=event.chat_id, text=text, buttons=_eval_buttons(code),
        )
    except Exception:
        # Стационарный бот не подключён — инлайн (редактирование/повтор/
        # экспорт) недоступен, отдаём хотя бы голый результат сообщением.
        await engine.answer(event, text)
        return

    state = {'code': code}

    @form.on_click(data="eval_repeat")
    async def _repeat(click):
        await click.answer("Выполняю…")
        new_text = await _run_text(event, state['code'])
        await form.edit(new_text, buttons=_eval_buttons(state['code']))

    @form.on_input(data="eval_edit")
    async def _edited(inp):
        new_code = inp.value or ''
        state['code'] = new_code
        new_text = await _run_text(event, new_code)
        await form.edit(new_text, buttons=_eval_buttons(new_code))

    @form.on_click(data="eval_tofile")
    async def _tofile(click):
        await click.answer()
        fname = f"eval_{int(time.time())}.py"
        out_path = FILES_DIR / fname
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(state['code'], encoding='utf-8')
        try:
            await engine.client.send_file(
                form.chat_id, str(out_path),
                caption=visual.title('Код из .eval', fname),
                parse_mode='html',
            )
        except Exception as e:
            engine.tlog(f"Ошибка отправки .py из .eval: {e}")


@decorators.command('eval', 'ev', 'exec')
async def eval_cmd(event):
    """выполняет код (аргументом, ответом на сообщение или .py/.txt файл)"""
    code = await _resolve_code(event)
    if not code:
        await engine.answer(
            event,
            visual.err('Укажите код') + "\n"
            + visual.item("<code>.eval &lt;код&gt;</code>") + "\n"
            + visual.hint('или ответом на сообщение / .py / .txt файл с кодом'),
        )
        return

    await engine.answer(event, visual.wait('Выполняю…'))
    await _present(event, code)


def _esc(text):
    return (text or '').replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
