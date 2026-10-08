"""
core/web/access_api.py — HTTP-слой вкладки «доступ» (#/access) и алиасов (внизу вкладки «конфиги»).
Логика прав и алиасов — core/access.py; все запросы идут через WebPanel.api() (Host / Origin / X-FRX).
"""

from ..access import AccessError, PROTECTED_MODULES, SCOPES, SCOPE_TITLES, command_key, describe_grant


def register(panel, router):
    from .server import ApiError                      # server.py импортирует нас лениво — цикла нет
    api = AccessApi(panel, ApiError)
    r = router
    r.add_get('/api/access/state', panel.api(api.state))
    r.add_get('/api/access/user', panel.api(api.user))
    for name in ('resolve', 'grant', 'revoke', 'user_class', 'user_del', 'class_create', 'class_update',
                 'class_delete', 'check'):
        r.add_post(f'/api/access/{name}', panel.api(getattr(api, name)))
    r.add_get('/api/aliases', panel.api(api.aliases))
    r.add_post('/api/aliases/set', panel.api(api.alias_set))
    r.add_post('/api/aliases/del', panel.api(api.alias_del))
    return api


class AccessApi:
    def __init__(self, panel, err):
        self.panel = panel
        self.core = panel.core
        self.Err = err

    async def _body(self, request):
        return await self.panel._json(request)

    def _am(self):
        if not self.core.access.loaded:
            raise self.Err('доступ ещё не загружен — подождите запуска юзербота', 503)
        return self.core.access

    async def _call(self, coro):
        try:
            return await coro
        except AccessError as e:
            raise self.Err(str(e))

    # ------------------------------------------------------------------ описание модулей и выдач
    def _modules(self, include_protected=False):
        out = []
        for name in sorted(self.core.modules, key=lambda n: (not self.core.modules[n].is_builtin, n.lower())):
            if name in PROTECTED_MODULES and not include_protected:
                continue
            info = self.core.get_module_info(name)
            if not info['commands']:
                continue                                  # без команд — выдавать нечего
            out.append({
                'module': name, 'name': info['name'], 'builtin': info['is_builtin'],
                'description': (info.get('description') or '').split('\n')[0][:200],
                'danger': self.core.module_danger(name),
                'commands': [{'key': f"{name}:{c['command']}", 'command': c['command'], 'aliases': c['aliases'],
                              'description': (c['description'] or '').split('\n')[0][:160]} for c in info['commands']],
            })
        return out

    @staticmethod
    def _scope_danger(scope, modules):
        names = [m['name'] for m in modules if m['danger']
                 and (scope == 'all' or (scope == 'builtin') == m['builtin'])]
        return ['включает критически опасные модули: ' + ', '.join(names)] if names else []

    def _grant_out(self, kind, value, modules, prefix):
        by = {m['module']: m for m in modules}
        if kind == 'scope':
            danger = self._scope_danger(value, modules)
            sub = 'все команды ' + {'all': 'всех модулей', 'builtin': 'встроенных модулей', 'loaded': 'ваших модулей из user/modules'}[value]
            return {'kind': kind, 'value': value, 'label': SCOPE_TITLES[value], 'sub': sub, 'danger': danger}
        mod = value.split(':', 1)[0]
        m = by.get(mod)
        danger = m['danger'] if m else []
        if kind == 'module':
            sub = (f"модуль целиком · {len(m['commands'])} " + ('команд' if len(m['commands']) != 1 else 'команда')) if m else 'модуль не загружен'
            return {'kind': kind, 'value': value, 'label': m['name'] if m else value, 'sub': sub, 'danger': danger, 'module': mod}
        cmd = value.split(':', 1)[-1]
        return {'kind': kind, 'value': value, 'label': prefix + cmd, 'sub': f"команда модуля {m['name'] if m else mod}"
                + ('' if m and any(c['key'] == value for c in m['commands']) else ' · сейчас не загружена'),
                'danger': danger, 'module': mod}

    def _grants(self, obj, modules, prefix):
        return [self._grant_out(k, v, modules, prefix) for k, v in self._am().grants_of(obj)]

    def _count(self, uid, modules):
        allowed = total = 0
        for m in modules:
            for c in m['commands']:
                total += 1
                allowed += self._am().check(uid, m['module'], c['key'], m['builtin'])[0]
        return allowed, total

    def _user_out(self, uid, u, modules, prefix):
        allowed, total = self._count(uid, modules)
        return {'id': uid, 'name': u.get('name') or '', 'username': u.get('username') or '', 'class': u.get('class'),
                'grants': self._grants(u, modules, prefix), 'added': u.get('added'), 'allowed': allowed, 'total': total}

    def _class_out(self, idn, c, modules, prefix):
        users = [uid for uid, u in self._am().users().items() if u.get('class') == idn]
        return {'idn': idn, 'title': c.get('title') or '', 'description': c.get('description') or '',
                'system': bool(c.get('system')), 'grants': self._grants(c, modules, prefix), 'users': users}

    # ------------------------------------------------------------------ чтение
    async def state(self, request):
        am = self._am()
        modules = self._modules()
        prefix = await self.core.get_prefix()
        return {
            'prefix': prefix,
            'users': [self._user_out(uid, u, modules, prefix) for uid, u in am.users().items()],
            'classes': [self._class_out(n, c, modules, prefix) for n, c in am.classes().items()],
            'modules': modules,
            'scopes': [self._grant_out('scope', s, modules, prefix) for s in SCOPES],
        }

    async def user(self, request):
        """Пользователь (или ещё не гость — тогда user=null) и итоговые права по каждой команде."""
        am = self._am()
        uid = str(request.query.get('id', ''))
        if not uid.lstrip('-').isdigit():
            raise self.Err('некорректный id', 400)
        modules = self._modules()
        prefix = await self.core.get_prefix()
        u = am.get_user(uid)
        who = {'id': uid, 'name': (u or {}).get('name', ''), 'username': (u or {}).get('username', '')}
        if not u:
            try:
                who.update(await self._resolve(uid))
            except self.Err:
                pass
        eff = {}
        if u:
            for m in modules:
                for c in m['commands']:
                    ok, why = am.check(uid, m['module'], c['key'], m['builtin'])
                    eff[c['key']] = {'ok': ok, 'why': why}
        return {'who': who, 'user': self._user_out(uid, u, modules, prefix) if u else None, 'effective': eff}

    # ------------------------------------------------------------------ пользователи
    async def _resolve(self, query):
        query = str(query or '').strip()
        if not query:
            raise self.Err('укажите id или @username')
        try:
            entity = await self.core.client.get_entity(int(query) if query.lstrip('-').isdigit() else query)
        except Exception:
            raise self.Err(f'пользователь «{query}» не найден — ваш аккаунт должен хотя бы раз его видеть (ЛС, общий чат)', 404)
        if getattr(entity, 'bot', False) or not hasattr(entity, 'first_name'):
            raise self.Err('доступ выдаётся только людям (не ботам, не чатам)')
        if entity.id == self.core._owner_id:
            raise self.Err('это вы — у владельца и так есть всё')
        name = ' '.join(x for x in (entity.first_name or '', getattr(entity, 'last_name', '') or '') if x) or str(entity.id)
        return {'id': str(entity.id), 'name': name, 'username': getattr(entity, 'username', '') or ''}

    async def resolve(self, request):
        b = await self._body(request)
        query = str(b.get('user') or '').strip()
        existing = None
        q = query.lstrip('@').lower()
        for uid, u in self._am().users().items():
            if q and q in (uid, (u.get('username') or '').lower()):
                existing = {'id': uid, 'name': u.get('name') or '', 'username': u.get('username') or ''}
        return {**(existing or await self._resolve(query)), 'exists': bool(existing)}

    def _check_value(self, kind, value):
        if kind in ('module', 'command'):
            mod = value.split(':', 1)[0]
            if mod not in self.core.modules:
                raise self.Err(f'модуля «{mod}» нет')

    async def grant(self, request):
        b = await self._body(request)
        target, kind, value = b.get('target'), b.get('kind'), str(b.get('value') or '')
        self._check_value(kind, value)
        await self._call(self._am().grant(target, str(b.get('id') or ''), kind, value,
                                          name=str(b.get('name') or ''), username=str(b.get('username') or '')))
        prefix = await self.core.get_prefix()
        self.core.tlog(f"[access] {target} {b.get('id')}: выдано — {describe_grant(kind, value, prefix)}")
        return {'ok': True}

    async def revoke(self, request):
        b = await self._body(request)
        await self._call(self._am().revoke(b.get('target'), str(b.get('id') or ''), b.get('kind'), str(b.get('value') or '')))
        return {'ok': True}

    async def user_class(self, request):
        b = await self._body(request)
        await self._call(self._am().set_user_class(str(b.get('id') or ''), b.get('class') or None,
                                                   name=str(b.get('name') or ''), username=str(b.get('username') or '')))
        return {'ok': True}

    async def user_del(self, request):
        b = await self._body(request)
        await self._call(self._am().remove_user(b.get('id')))
        return {'ok': True}

    async def check(self, request):
        b = await self._body(request)
        am = self._am()
        uid = str(b.get('id') or '')
        word = str(b.get('command') or '').strip()
        prefix = await self.core.get_prefix()
        if word.startswith(prefix):
            word = word[len(prefix):]
        word = (word.split() or [''])[0].lower()
        entry = self.core.command_map.get(word)
        if entry is None and self.core.aliases.get(word):
            entry = self.core.command_map.get(self.core.aliases.get(word)['command'])
        if entry is None:
            raise self.Err(f'команды «{word}» нет')
        module, func = entry
        ok, why = am.check(uid, module, command_key(module, func), self.core.modules[module].is_builtin)
        return {'ok': ok, 'why': why, 'key': command_key(module, func)}

    # ------------------------------------------------------------------ классы
    async def class_create(self, request):
        b = await self._body(request)
        idn = str(b.get('idn') or '').strip()
        await self._call(self._am().create_class(idn, title=str(b.get('title') or ''),
                                                 description=str(b.get('description') or ''),
                                                 copy_from=b.get('copy_from') or None))
        return {'ok': True, 'idn': idn}

    async def class_update(self, request):
        b = await self._body(request)
        idn = str(b.get('idn') or '')
        await self._call(self._am().update_class(idn, title=b.get('title'), description=b.get('description')))
        new = str(b.get('new_idn') or '').strip()
        if new and new != idn:
            await self._call(self._am().rename_class(idn, new))
            idn = new
        return {'ok': True, 'idn': idn}

    async def class_delete(self, request):
        b = await self._body(request)
        moved = await self._call(self._am().delete_class(b.get('idn')))
        return {'ok': True, 'users_without_class': moved}

    # ------------------------------------------------------------------ алиасы
    async def aliases(self, request):
        prefix = await self.core.get_prefix()
        items = [{'name': n, 'command': a['command'], 'args': a.get('args', ''), 'alive': a['command'] in self.core.command_map,
                  'module': (self.core.command_map.get(a['command']) or (None,))[0]}
                 for n, a in sorted(self.core.aliases.all().items())]
        return {'prefix': prefix, 'aliases': items, 'modules': self._modules(include_protected=True),
                'commands': sorted(self.core.command_map)}

    async def alias_set(self, request):
        b = await self._body(request)
        prefix = await self.core.get_prefix()

        def clean(x):
            x = str(x or '').strip()
            return x[len(prefix):] if prefix and x.startswith(prefix) else x
        name, command, old = clean(b.get('name')), clean(b.get('command')), clean(b.get('old'))
        await self._call(self.core.aliases.set(name, command, b.get('args') or '', existing_commands=set(self.core.command_map)))
        if old and old.lower() != name.lower() and self.core.aliases.get(old):
            await self._call(self.core.aliases.delete(old))
        return {'ok': True}

    async def alias_del(self, request):
        b = await self._body(request)
        await self._call(self.core.aliases.delete(b.get('name')))
        return {'ok': True}
