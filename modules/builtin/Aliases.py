"""
modules/builtin/Aliases.py — алиасы команд: свои короткие имена для любых команд (сразу с аргументами).

    .alias <команда> [аргументы…] <новое_имя>     .alias help h        → .h запускает .help
                                                  .alias config helper c → .c запускает .config helper,
                                                                           а .c x — .config helper x
    .alias                                        список алиасов
    .unalias <имя>                                удалить

    .layout [on|off]                              команды в неверной раскладке: «юзштп» → .ping (по умолчанию вкл)

Последнее слово — новое имя, первое — команда, всё между ними — аргументы. Алиас не может совпадать с
существующей командой (команда всегда главнее). На сайте алиасы — внизу вкладки «конфиги».
Логика — core/access.py (engine.aliases). Модуль доступен только владельцу.
"""

import html

import engine
import decorators
import visual

from core.access import AccessError

NAME = "Aliases"
MODULE_NAME = "aliases"
VERSION = "1.1"
AUTHOR = "FRX"
DESCRIPTION = "Алиасы команд (.alias <команда> [аргументы] <новое_имя>) и команды в неверной раскладке (.layout)"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Aliases.py"


def _esc(x):
    return html.escape(str(x))


def _strip(word, prefix):
    return word[len(prefix):] if prefix and word.startswith(prefix) else word


def _target(a):
    return a['command'] + (' ' + a['args'] if a.get('args') else '')


@decorators.command('alias')
async def alias_cmd(event):
    """алиас команды: <команда> [аргументы] <новое_имя>; без аргументов — список"""
    prefix = await engine.get_prefix()
    args = engine.get_args().split()
    if not args:
        items = engine.aliases.all()
        lines = [visual.title("Алиасы")]
        if not items:
            lines.append(visual.hints("пока нет", f"пример: {prefix}alias help h", f"или: {prefix}alias config helper c"))
        for name, a in sorted(items.items()):
            alive = '' if a['command'] in engine.command_map else '  ' + visual.hint('(команды нет)')
            lines.append(visual.item(f"<code>{_esc(prefix + name)}</code> → <code>{_esc(prefix + _target(a))}</code>{alive}"))
        if items:
            lines += ["", visual.hint(f"удалить: {prefix}unalias имя")]
        await engine.answer(event, "\n".join(lines))
        return
    if len(args) < 2:
        await engine.answer(event, visual.err(f"Формат: {_esc(prefix)}alias &lt;команда&gt; [аргументы] &lt;новое_имя&gt;")
                            + "\n" + visual.hint(f"например: {prefix}alias help h"))
        return
    command = _strip(args[0], prefix).lower()
    name = _strip(args[-1], prefix)
    rest = ' '.join(args[1:-1])
    commands = set(engine.command_map)
    if command not in commands and name.lower() in commands and len(args) == 2:
        await engine.answer(event, visual.err("Сначала команда, потом новое имя") + "\n"
                            + visual.hint(f"{prefix}alias {_esc(name.lower())} {_esc(command)}"))
        return
    try:
        await engine.aliases.set(name, command, rest, existing_commands=commands)
    except AccessError as e:
        await engine.answer(event, visual.err(_esc(e)))
        return
    a = engine.aliases.get(name)
    await engine.answer(event, visual.ok("Алиас создан") + "\n"
                        + visual.field(_esc(prefix + name.lower()), _esc(prefix + _target(a))))


@decorators.command('unalias')
async def unalias_cmd(event):
    """удалить алиас: <имя>"""
    prefix = await engine.get_prefix()
    name = _strip(engine.get_args().strip(), prefix)
    try:
        await engine.aliases.delete(name)
    except AccessError as e:
        await engine.answer(event, visual.err(_esc(e)))
        return
    await engine.answer(event, visual.ok(f"Алиас {_esc(prefix + name)} удалён"))


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='layout_commands',
        description='Понимать команды, набранные в русской раскладке: «юзштп» → .ping (только ваши сообщения; '
                    'аргументы не переводятся; кириллические команды главнее)',
        data_type='bool', default=True,
    )


@decorators.command('layout')
async def layout_cmd(event):
    """команды в неверной раскладке (юзштп → .ping): on / off, без аргументов — показать"""
    prefix = await engine.get_prefix()
    arg = engine.get_args().strip().lower()
    if arg in ('on', 'off', 'вкл', 'выкл', '1', '0'):
        await engine.edit_config('layout_commands', 'edit', arg in ('on', 'вкл', '1'))
    elif arg:
        await engine.answer(event, visual.err(f"Формат: {_esc(prefix)}layout on | off"))
        return
    on = await engine.get_config('layout_commands')
    example = ('ю' if prefix == '.' else _esc(prefix)) + 'зштп'
    await engine.answer(event, visual.ok("Команды в неверной раскладке: " + ("вкл" if on else "выкл")) + "\n"
                        + visual.hints(f"{example} → {_esc(prefix)}ping", f"переключить: {_esc(prefix)}layout {'off' if on else 'on'}"))
