"""
modules/builtin/Access.py — доступ других людей к вашему юзерботу.

Логика прав — core/access.py (engine.access); здесь — интерфейс в Telegram. Сам модуль доступен только
владельцу (гостю его команды не выдаются ничем).

    .access  ответом на сообщение человека   — меню этого человека: какой доступ у него сейчас,
                                               «Выдать доступ» (класс или разрешение на модуль / команду)
                                               и «Отозвать доступ»
    .access <id | @username>                 — то же меню без ответа
    .access                                  — список всех, у кого есть доступ

  Текстом (без стационарного бота или для скриптов):
    .access grant [пользователь] <класс | модуль | .команда | модуль:команда | all | builtin | loaded>
    .access revoke [пользователь] [то же | class]     без «что» — отозвать весь доступ
    .access check [пользователь] <команда>
    .access list | classes
  Если команда отправлена ответом на сообщение, пользователя указывать не нужно.

Классы создаются и настраиваются на сайте (.web → доступ). Алиасы — модуль Aliases (.alias).
Настройки (.config access): guest_cooldown, log_guest_commands. Команды, к которым нет доступа, молча игнорируются.
"""

import html

import engine
import decorators
import visual

from core.access import AccessError, PROTECTED_MODULES, SCOPE_TITLES, SCOPES, command_key, describe_grant

NAME = "Access"
MODULE_NAME = "access"
VERSION = "2.0"
AUTHOR = "FRX"
DESCRIPTION = "Доступ других пользователей к командам юзербота: классы и отдельные разрешения на модули и команды"

PAGE = 7
DANGER = '⚠️'


def _esc(x):
    return html.escape(str(x))


@decorators.on_start()
async def _configs():
    await engine.create_config('guest_cooldown', 'Сколько секунд гость должен ждать между командами (антифлуд)', 'float', 1.0,
                               valid={'min_value': 0, 'max_value': 60})
    await engine.create_config('log_guest_commands', 'Писать команды гостей в терминал (и во вкладку логов сайта)', 'bool', True)
    for old in ('full_only_modules', 'notify_denied'):          # настройки ранних сборок: запретов и ответа «нет доступа» больше нет
        await engine.delete_config(old)


# ------------------------------------------------------------------ #
# Пользователи, модули, команды
# ------------------------------------------------------------------ #

async def _resolve_user(event, query=''):
    """(id, имя, username) по id / @username / ответу на сообщение. AccessError — не найден."""
    query = (query or '').strip()
    if not query and event.is_reply:
        reply = await event.get_reply_message()
        if reply and reply.sender_id:
            query = str(reply.sender_id)
    if not query:
        raise AccessError("Ответьте на сообщение человека или укажите его id / @username")
    existing = _find_user(query)
    if existing:
        u = engine.access.get_user(existing)
        return int(existing), u.get('name', ''), u.get('username', '')
    try:
        entity = await engine.client.get_entity(int(query) if query.lstrip('-').isdigit() else query)
    except Exception:
        raise AccessError(f"Пользователь «{query}» не найден (ваш аккаунт должен хотя бы раз его видеть)")
    if getattr(entity, 'bot', False):
        raise AccessError("Это бот — давать доступ ботам нельзя")
    if not hasattr(entity, 'first_name'):
        raise AccessError("Это чат или канал, а не пользователь")
    if entity.id == (await engine.client.get_me()).id:
        raise AccessError("Это вы сами — у владельца и так есть всё")
    name = ' '.join(x for x in (entity.first_name or '', getattr(entity, 'last_name', '') or '') if x) or str(entity.id)
    return entity.id, name, getattr(entity, 'username', '') or ''


def _find_user(query):
    """id уже добавленного пользователя по id / @username / имени (без регистра)."""
    q = str(query).strip().lstrip('@').lower()
    for uid, u in engine.access.users().items():
        if q in (uid, (u.get('username') or '').lower(), (u.get('name') or '').lower()):
            return uid
    return None


def _modules():
    """[(MODULE_NAME, NAME, builtin, danger)] — модули с командами, к которым можно выдать доступ."""
    out = []
    for name in engine.get_module_list():
        if name in PROTECTED_MODULES:
            continue
        info = engine.get_module_info(name)
        if not info.get('commands'):
            continue
        out.append((name, info.get('name', name), info.get('is_builtin', False), engine.module_danger(name)))
    out.sort(key=lambda x: (not x[2], x[1].lower()))
    return out


def _commands_of(module):
    """[(ключ, команда, описание)] команд модуля."""
    info = engine.get_module_info(module)
    return [(f"{module}:{c['command']}", c['command'], c.get('description', '')) for c in info.get('commands', [])]


def _is_builtin(module):
    info = engine.modules.get(module)
    return bool(info and info.is_builtin)


def _count(uid):
    """(доступно команд, всего команд) для гостя."""
    total = allowed = 0
    for module, _n, builtin, _d in _modules():
        for key, _c, _ in _commands_of(module):
            total += 1
            allowed += engine.access.check(uid, module, key, builtin)[0]
    return allowed, total


def _grant_target(query):
    """(вид, значение) выдачи по тексту: класс → ('class', idn), all/builtin/loaded, модуль, .команда, модуль:команда."""
    q = (query or '').strip()
    if not q:
        raise AccessError("Укажите, что выдать: класс, модуль или команду")
    classes = engine.access.classes()
    if q in classes:
        return 'class', q
    low = {k.lower(): k for k in classes}
    if q.lower() in low and not q.startswith('.'):
        return 'class', low[q.lower()]
    if q.lower() in SCOPES:
        return 'scope', q.lower()
    if ':' in q:
        mod, cmd = q.split(':', 1)
        module = engine.resolve_module(mod.strip())
        if not module:
            raise AccessError(f"Модуль «{mod}» не найден")
        func = {k.lower(): f for k, f in engine.modules[module].commands.items()}.get(cmd.strip().lower().lstrip('.'))
        if func is None:
            raise AccessError(f"В модуле {module} нет команды «{cmd}»")
        return 'command', command_key(module, func)
    if not q.startswith('.'):
        module = engine.resolve_module(q)
        if module:
            return 'module', module
    entry = engine.command_map.get(q.lstrip('.').lower())
    if entry:
        return 'command', command_key(entry[0], entry[1])
    raise AccessError(f"«{q}» — не класс, не модуль и не команда")


def _danger_of_grant(kind, value):
    if kind == 'scope':
        return ['открывает в том числе опасные модули'] if value in ('all', 'builtin') else \
            [f"{m[1]}: {', '.join(m[3])}" for m in _modules() if not m[2] and m[3]]
    if kind == 'module':
        return engine.module_danger(value)
    if kind == 'command':
        return engine.module_danger(value.split(':', 1)[0])
    return []


def _grant_line(kind, value, prefix):
    mark = f"{DANGER} " if _danger_of_grant(kind, value) else ''
    return mark + _esc(describe_grant(kind, value, prefix))


# ------------------------------------------------------------------ #
# Тексты
# ------------------------------------------------------------------ #

def _class_lines(idn, prefix, level=1):
    c = engine.access.get_class(idn) or {}
    out = []
    if c.get('description'):
        out.append(visual.item(f"<i>{_esc(c['description'])}</i>", level))
    grants = engine.access.grants_of(c)
    if grants:
        out.append(visual.item("даёт: " + ', '.join(_grant_line(k, v, prefix) for k, v in grants), level))
    else:
        out.append(visual.item("в классе пока нет выдач — настройте его на сайте", level))
    return out


def _classes_text(prefix):
    lines = [visual.title("Классы доступа")]
    for idn, c in engine.access.classes().items():
        users = sum(1 for u in engine.access.users().values() if u.get('class') == idn)
        lines.append(visual.item(f"<b>{_esc(idn)}</b> — {_esc(c.get('title') or '')} · пользователей: {users}"))
        lines += _class_lines(idn, prefix)
    lines += ["", visual.hint("создавать и настраивать классы — на сайте: .web → доступ")]
    return "\n".join(lines)


def _list_text(prefix):
    users = engine.access.users()
    lines = [visual.title("Доступ", sub="кто ещё может пользоваться вашим юзерботом")]
    if users:
        for uid, u in users.items():
            n = len(engine.access.grants_of(u))
            what = ', '.join(x for x in ((f"класс {u['class']}" if u.get('class') else ''),
                                         (f"разрешений: {n}" if n else '')) if x)
            lines.append(visual.item(f"<b>{_esc(u.get('name') or uid)}</b>"
                                     + (f" @{_esc(u['username'])}" if u.get('username') else '')
                                     + f" · <code>{uid}</code> · {_esc(what)}"))
    else:
        lines.append(visual.hint("пока ни у кого нет доступа"))
    lines += ["", visual.hint(f"выдать: ответьте на сообщение человека командой {prefix}access")]
    return "\n".join(lines)


def _user_text(uid, name, username, prefix):
    u = engine.access.get_user(uid)
    lines = [visual.title(_esc(name or uid), sub=f"@{_esc(username)} · id {uid}" if username else f"id {uid}")]
    if not u:
        lines += [visual.item("доступа нет — команды этого человека ваш юзербот не выполняет"), "",
                  visual.hint("«Выдать доступ» — класс (готовый набор прав) или разрешение на модуль / команду")]
        return "\n".join(lines)
    lines.append(visual.section("Доступ сейчас"))
    if u.get('class'):
        c = engine.access.get_class(u['class']) or {}
        lines.append(visual.item(f"класс <b>{_esc(u['class'])}</b> — {_esc(c.get('title') or '')}"))
        lines += _class_lines(u['class'], prefix)
    else:
        lines.append(visual.item("класс: нет"))
    grants = engine.access.grants_of(u)
    if grants:
        lines.append(visual.item("разрешения:"))
        for kind, value in grants:
            lines.append(visual.item(_grant_line(kind, value, prefix), 1))
    allowed, total = _count(uid)
    lines += [visual.field("доступно команд", f"{allowed} из {total}")]
    return "\n".join(lines)


# ------------------------------------------------------------------ #
# Инлайн-меню
# ------------------------------------------------------------------ #

async def _open_form(event, uid=None, name='', username=''):
    prefix = await engine.get_prefix()
    st = {'uid': str(uid) if uid is not None else None, 'name': name, 'username': username,
          'mods': [], 'mod': None, 'cmds': [], 'cmd': None, 'page': 0, 'classes': [], 'cls': None,
          'grants': [], 'users': []}

    def back(data):
        return [(visual.btn('Назад', 'back'), data)]

    def close_row():
        return [(visual.btn('Закрыть', 'close'), 'close')]

    def who():
        return _esc(st['name'] or st['uid']) + (f" · @{_esc(st['username'])}" if st['username'] else '')

    async def show_list(page=0):
        st['users'] = list(engine.access.users())
        items = []
        for i, x in enumerate(st['users']):
            u = engine.access.get_user(x)
            items.append((f"{u.get('name') or x}" + (f" · {u['class']}" if u.get('class') else ''), f"u:{i}"))
        rows, _p, _ = engine.picker_page(items, page, PAGE, 'lp:') if items else ([], 0, 1)
        rows.append([(visual.btn('Классы'), 'classes')])
        rows.append(close_row())
        await form.edit(_list_text(prefix), buttons=rows)

    async def show_user():
        u = engine.access.get_user(st['uid'])
        rows = [[(visual.btn('Выдать доступ'), 'give')]]
        if u:
            rows.append([(visual.btn('Отозвать доступ'), 'revoke')])
        rows.append(close_row())
        await form.edit(_user_text(st['uid'], st['name'], st['username'], prefix), buttons=rows)

    async def show_give():
        await form.edit("\n".join([
            visual.title("Выдать доступ", sub=who()),
            visual.item("<b>класс</b> — готовый набор прав (создаются на сайте: .web → доступ)"),
            visual.item("<b>разрешение</b> — доступ к одному модулю целиком или к одной его команде"),
        ]), buttons=[[(visual.btn('Выдать класс'), 'gcls')], [(visual.btn('Выдать разрешение'), 'gperm:0')], back('user')])

    async def show_classes():
        u = engine.access.get_user(st['uid']) or {}
        st['classes'] = list(engine.access.classes())
        rows = []
        for i, idn in enumerate(st['classes']):
            c = engine.access.get_class(idn)
            cur = ' · сейчас' if u.get('class') == idn else ''
            rows.append([(f"{idn} — {c.get('title') or ''}{cur}"[:60], f"c:{i}")])
        rows.append(back('give'))
        await form.edit(visual.title("Выдать класс", sub=who()) + "\n" + visual.hint("выберите класс"), buttons=rows)

    async def show_class_confirm():
        idn = st['cls']
        c = engine.access.get_class(idn)
        u = engine.access.get_user(st['uid']) or {}
        lines = [visual.title(f"Класс {_esc(idn)}", sub=who()), visual.item(f"<b>{_esc(c.get('title') or idn)}</b>")]
        lines += _class_lines(idn, prefix, 0)
        danger = []
        for k, v in engine.access.grants_of(c):
            danger += _danger_of_grant(k, v)
        if danger:
            lines += ["", visual.warn("<b>критически опасный доступ:</b> " + _esc('; '.join(dict.fromkeys(danger))))]
        if u.get('class') == idn:
            lines += ["", visual.hint("этот класс уже выдан")]
            await form.edit("\n".join(lines), buttons=[back('gcls')])
            return
        if u.get('class'):
            lines += ["", visual.hint(f"текущий класс {_esc(u['class'])} будет заменён; личные разрешения останутся")]
        await form.edit("\n".join(lines), buttons=[[(visual.btn(f'Выдать класс {idn}'), 'cok')], back('gcls')])

    async def show_mods(page=0):
        st['mods'] = _modules()
        items = []
        for i, (mname, title, builtin, danger) in enumerate(st['mods']):
            has = engine.access.module_allowed(st['uid'], mname, builtin)[0] if engine.access.get_user(st['uid']) else False
            items.append(((f"{DANGER} " if danger else '') + title + (' ✓' if has else ''), f"m:{i}"))
        rows, page, _ = engine.picker_page(items, page, PAGE, 'gperm:')
        st['page'] = page
        rows.append(back('give'))
        await form.edit(visual.title("Выдать разрешение", sub=who()) + "\n"
                        + visual.hint(f"выберите модуль · {DANGER} — критически опасный · ✓ — уже доступен целиком"),
                        buttons=rows)

    async def show_mod():
        mname = st['mod']
        info = engine.get_module_info(mname)
        builtin = _is_builtin(mname)
        danger = engine.module_danger(mname)
        has_user = bool(engine.access.get_user(st['uid']))
        whole, why = engine.access.module_allowed(st['uid'], mname, builtin) if has_user else (False, '')
        st['cmds'] = _commands_of(mname)
        lines = [visual.title(_esc(info.get('name', mname)), sub=who())]
        if info.get('description'):
            lines.append(visual.item(f"<i>{_esc(info['description'])}</i>"))
        if danger:
            lines.append(visual.warn("<b>критически опасный модуль:</b> " + _esc('; '.join(danger))))
        rows = []
        if whole:
            lines.append(visual.item(f"доступен целиком <i>({_esc(why)})</i>"))
        else:
            rows.append([(visual.btn('Выдать доступ ко всему модулю'), 'gm')])
        lines += ["", visual.hint("или выберите одну команду:")]
        for i, (key, cmd, _d) in enumerate(st['cmds']):
            ok = has_user and engine.access.check(st['uid'], mname, key, builtin)[0]
            rows.append([(f"{prefix}{cmd}" + (' ✓' if ok else ''), f"k:{i}")])
        rows.append(back(f"gperm:{st['page']}"))
        await form.edit("\n".join(lines), buttons=rows)

    async def show_cmd():
        mname = st['mod']
        key, cmd, desc = st['cmds'][st['cmd']]
        builtin = _is_builtin(mname)
        ok, why = engine.access.check(st['uid'], mname, key, builtin) if engine.access.get_user(st['uid']) else (False, '')
        danger = engine.module_danger(mname)
        lines = [visual.title(f"{_esc(prefix + cmd)}", sub=who()), visual.field("модуль", _esc(mname))]
        if desc:
            lines.append(visual.item(f"<i>{_esc(desc)}</i>"))
        if danger:
            lines.append(visual.warn("<b>команда критически опасного модуля:</b> " + _esc('; '.join(danger))))
        rows = []
        if ok:
            lines.append(visual.item(f"уже доступна <i>({_esc(why)})</i>"))
        else:
            rows.append([(visual.btn(f'Выдать доступ к {prefix}{cmd}'), 'gk')])
        rows.append(back('mod'))
        await form.edit("\n".join(lines), buttons=rows)

    async def confirm_danger(kind, value, ok_data, back_data):
        reasons = _danger_of_grant(kind, value)
        await form.edit("\n".join([
            visual.title("Критически опасный доступ", sub=who()),
            visual.item(f"выдаётся: <b>{_esc(describe_grant(kind, value, prefix))}</b>"),
            visual.warn(_esc('; '.join(reasons))),
            "", visual.hint("по сути это доступ ко всему аккаунту и компьютеру, где запущен юзербот. "
                            "Выдавайте только тем, кому доверяете как себе."),
        ]), buttons=[[(visual.btn('Да, выдать'), ok_data)], back(back_data)])

    async def do_grant(kind, value, click):
        await engine.access.grant('user', st['uid'], kind, value, name=st['name'], username=st['username'])
        await click.answer("выдано: " + describe_grant(kind, value, prefix)[:150])

    async def show_revoke():
        u = engine.access.get_user(st['uid'])
        if not u:
            await show_user()
            return
        st['grants'] = engine.access.grants_of(u)
        rows = []
        if u.get('class'):
            rows.append([(f"✕ класс {u['class']}", 'rc')])
        for i, (kind, value) in enumerate(st['grants']):
            rows.append([(f"✕ {describe_grant(kind, value, prefix)}"[:60], f"rg:{i}")])
        rows.append([(visual.btn('Отозвать весь доступ'), 'rall')])
        rows.append(back('user'))
        await form.edit(visual.title("Отозвать доступ", sub=who()) + "\n"
                        + visual.hint("нажмите на то, что нужно отозвать"), buttons=rows)

    form = await engine.create_inlineform(event.chat_id, visual.wait("Открываю…"), buttons=[[('…', 'noop')]])

    @form.on_click()
    async def _click(click):
        d = click.data
        try:
            if d == 'close':
                await click.answer()
                await form.edit(visual.ok("Доступ: меню закрыто"), buttons=None)
                await form.close()
                return
            if d == 'noop':
                await click.answer()
                return
            if d.startswith('lp:'):
                await click.answer()
                await show_list(int(d[3:] or 0))
            elif d == 'classes':
                await click.answer()
                await form.edit(_classes_text(prefix), buttons=[back('list')])
            elif d == 'list':
                await click.answer()
                await show_list()
            elif d.startswith('u:'):
                await click.answer()
                st['uid'] = st['users'][int(d[2:])]
                u = engine.access.get_user(st['uid']) or {}
                st['name'], st['username'] = u.get('name', ''), u.get('username', '')
                await show_user()
            elif d == 'user':
                await click.answer()
                await show_user()
            elif d == 'give':
                await click.answer()
                await show_give()
            elif d == 'gcls':
                await click.answer()
                await show_classes()
            elif d.startswith('c:'):
                await click.answer()
                st['cls'] = st['classes'][int(d[2:])]
                await show_class_confirm()
            elif d == 'cok':
                await engine.access.set_user_class(st['uid'], st['cls'], name=st['name'], username=st['username'])
                await click.answer(f"класс {st['cls']} выдан")
                await show_user()
            elif d.startswith('gperm:'):
                await click.answer()
                await show_mods(int(d[6:] or 0))
            elif d.startswith('m:'):
                await click.answer()
                st['mod'] = st['mods'][int(d[2:])][0]
                await show_mod()
            elif d == 'mod':
                await click.answer()
                await show_mod()
            elif d.startswith('k:'):
                await click.answer()
                st['cmd'] = int(d[2:])
                await show_cmd()
            elif d in ('gm', 'gm!'):
                if d == 'gm' and engine.module_danger(st['mod']):
                    await click.answer()
                    await confirm_danger('module', st['mod'], 'gm!', 'mod')
                    return
                await do_grant('module', st['mod'], click)
                await show_mod()
            elif d in ('gk', 'gk!'):
                key = st['cmds'][st['cmd']][0]
                if d == 'gk' and engine.module_danger(st['mod']):
                    await click.answer()
                    await confirm_danger('command', key, 'gk!', f"k:{st['cmd']}")
                    return
                await do_grant('command', key, click)
                await show_cmd()
            elif d == 'revoke':
                await click.answer()
                await show_revoke()
            elif d == 'rc':
                await engine.access.set_user_class(st['uid'], None)
                await click.answer("класс отозван")
                await show_revoke()
            elif d.startswith('rg:'):
                kind, value = st['grants'][int(d[3:])]
                await engine.access.revoke('user', st['uid'], kind, value)
                await click.answer("отозвано: " + describe_grant(kind, value, prefix)[:150])
                await show_revoke()
            elif d == 'rall':
                if not engine.access.get_user(st['uid']):
                    await click.answer("доступа уже нет")
                    await show_user()
                    return
                await click.answer()
                await form.edit(visual.warn(f"Отозвать весь доступ у <b>{who()}</b>? Класс и все разрешения будут сняты."),
                                buttons=[[(visual.btn('Да, отозвать всё'), 'rallok')], back('revoke')])
            elif d == 'rallok':
                await engine.access.remove_user(st['uid'])
                await click.answer("доступ отозван")
                await show_user()
            else:
                await click.answer()
        except (AccessError, IndexError, KeyError, TypeError) as e:
            await click.answer(str(e) or 'меню устарело — откройте заново', show_alert=True)

    if st['uid'] is not None:
        await show_user()
    else:
        await show_list()
    return form


# ------------------------------------------------------------------ #
# Команда
# ------------------------------------------------------------------ #

@decorators.command('access', 'acs')
async def access_cmd(event):
    """доступ других людей: ответом на сообщение — меню этого человека; без ответа — список; grant / revoke / check / list / classes"""
    prefix = await engine.get_prefix()
    args = engine.get_args().split()
    sub = args[0].lower() if args else ''
    try:
        if sub in ('grant', 'revoke', 'check'):
            await _access_sub(event, sub, args[1:], prefix)
            return
        if sub == 'list':
            await engine.answer(event, _list_text(prefix))
            return
        if sub == 'classes':
            await engine.answer(event, _classes_text(prefix))
            return
        uid = name = username = None
        if args or event.is_reply:
            uid, name, username = await _resolve_user(event, ' '.join(args))
        try:
            await _open_form(event, uid, name or '', username or '')
            await engine.answer(event, visual.ok("Доступ") + "  " + visual.hint("меню ниже"))
        except RuntimeError:
            text = _user_text(str(uid), name, username, prefix) if uid else _list_text(prefix)
            await engine.answer(event, text + "\n\n" + visual.hint(
                f"без стационарного бота: {prefix}access grant / revoke / check"))
    except AccessError as e:
        await engine.answer(event, visual.err(_esc(e)))


async def _access_sub(event, sub, args, prefix):
    """grant/revoke/check: пользователь — из ответа или первым словом."""
    if event.is_reply:
        uid, name, username = await _resolve_user(event, '')
    else:
        if not args:
            raise AccessError("Укажите пользователя (id или @username) или отправьте команду ответом на его сообщение")
        uid, name, username = await _resolve_user(event, args.pop(0))
    label = _esc(name or uid)
    if sub == 'grant':
        kind, value = _grant_target(' '.join(args))
        if kind == 'class':
            await engine.access.set_user_class(uid, value, name=name, username=username)
            text = f"{label}: выдан класс {_esc(value)}"
        else:
            await engine.access.grant('user', uid, kind, value, name=name, username=username)
            text = f"{label}: выдано — {_esc(describe_grant(kind, value, prefix))}"
        danger = (sum((_danger_of_grant(k, v) for k, v in engine.access.grants_of(engine.access.get_class(value))), [])
                  if kind == 'class' else _danger_of_grant(kind, value))
        await engine.answer(event, visual.ok(text) + (("\n" + visual.warn("критически опасный доступ: "
                                                                          + _esc('; '.join(dict.fromkeys(danger))))) if danger else ''))
    elif sub == 'revoke':
        if not args:
            await engine.access.remove_user(uid)
            await engine.answer(event, visual.ok(f"{label}: весь доступ отозван"))
            return
        what = ' '.join(args)
        if what.lower() == 'class':
            await engine.access.set_user_class(uid, None)
            await engine.answer(event, visual.ok(f"{label}: класс отозван"))
            return
        kind, value = _grant_target(what)
        if kind == 'class':
            u = engine.access.get_user(uid) or {}
            if u.get('class') != value:
                raise AccessError(f"Класс {value} этому пользователю не выдан")
            await engine.access.set_user_class(uid, None)
        else:
            await engine.access.revoke('user', uid, kind, value)
        await engine.answer(event, visual.ok(f"{label}: отозвано — {_esc(value if kind == 'class' else describe_grant(kind, value, prefix))}"))
    elif sub == 'check':
        if not args:
            raise AccessError(f"Формат: {prefix}access check [пользователь] <команда>")
        word = args[0][len(prefix):] if args[0].startswith(prefix) else args[0]
        kind, key = _grant_target(word if ':' in word else '.' + word)
        module, cmd = key.split(':', 1)
        ok, why = engine.access.check(uid, module, key, _is_builtin(module))
        await engine.answer(event, (visual.ok if ok else visual.err)(f"{_esc(prefix + cmd)}: {'разрешено' if ok else 'нет доступа'}")
                            + "\n" + visual.field("пользователь", label) + "\n" + visual.hint(_esc(why)))
