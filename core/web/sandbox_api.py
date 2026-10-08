"""
core/web/sandbox_api.py: HTTP-слой FRX WebIDE Sandbox (логика — core/sandbox.py).

Все запросы идут через WebPanel.api() (те же проверки Host/Origin/X-FRX, что у остальных вкладок).
Песочница ключуется строкой `key` — id открытого модуля либо «__new__» для ещё не сохранённого. Исходник приходит
вместе с запросом запуска: проверяется то, что сейчас в редакторе, а не то, что лежит на диске. Настоящее ядро
используется только для чтения (владелец, префикс, занятые команды, копирование данных модуля из настоящей БД).
"""

import base64
import binascii
import json
import re

from ..sandbox import SbError, SandboxHub, make_media, preview

KEY_RE = re.compile(r'^[\w\-]{1,64}$')
MAX_UPLOAD = 1024 * 1024
MEDIA_KINDS = ('photo', 'document', 'voice', 'video', 'audio', 'sticker', 'video_note', 'gif')


def register(panel, router):
    from .server import ApiError
    api = SandboxApi(panel, ApiError)
    r = router
    r.add_get('/api/sandbox/poll', panel.api(api.poll))
    for name in ('start', 'stop', 'reset', 'send', 'event', 'click', 'input', 'time', 'data', 'config', 'people', 'settings', 'page', 'clear'):
        r.add_post(f'/api/sandbox/{name}', panel.api(getattr(api, name)))
    return api


class SandboxApi:
    def __init__(self, panel, api_error):
        self.panel = panel
        self.Err = api_error
        self.hub = SandboxHub()

    @property
    def core(self):
        return self.panel.core

    # ------------------------------------------------------------------ #

    def _key(self, value):
        if not isinstance(value, str) or not KEY_RE.match(value):
            raise self.Err('Некорректный ключ песочницы')
        return value

    async def _core_info(self):
        core = self.core
        try:
            prefix = await core.get_prefix()
        except Exception:
            prefix = '.'
        real = {alias: owner for alias, (owner, _f) in getattr(core, 'command_map', {}).items()}
        real_bot = {alias: entry[0] for alias, entry in getattr(core, 'bot_command_map', {}).items()}
        names, infos = [], {}
        try:
            names = list(core.get_module_list())
            for n in names:
                try:
                    infos[n] = core.get_module_info(n)
                except Exception:
                    pass
        except Exception:
            pass
        return {'module_names': names, 'module_infos': infos,
                'owner_id': getattr(core, '_owner_id', None) or 100000001, 'prefix': prefix, 'version': core.get_version(),
                'real_commands': real, 'real_bot_commands': real_bot, 'bot_reserved': set(getattr(core, '_bot_reserved_aliases', ()) or ())}

    async def _session(self, body_or_query, create=True):
        key = self._key(body_or_query.get('key'))
        s = self.hub.get(key, await self._core_info() if create and key not in self.hub.sessions else None, create=create)
        if s is None:
            raise self.Err('Песочница не запущена — нажмите «запустить»', 404)
        return s

    async def _run(self, s, factory, timeout=25):
        try:
            return await self.hub.run(s, factory, timeout)
        except (ValueError, TypeError, SbError) as e:
            raise self.Err(str(e))
        except KeyError as e:
            raise self.Err(str(e.args[0]) if e.args else 'KeyError')
        except RuntimeError as e:
            raise self.Err(str(e), 409)

    def _int(self, value, name, default=None, minimum=None):
        try:
            v = int(value)
        except (TypeError, ValueError):
            if default is not None:
                return default
            raise self.Err(f'Поле «{name}» должно быть числом')
        if minimum is not None and v < minimum:
            v = minimum
        return v

    def _num(self, value, name, default=None):
        try:
            v = float(value)
        except (TypeError, ValueError):
            if default is not None:
                return default
            raise self.Err(f'Поле «{name}» должно быть числом')
        if v != v or v in (float('inf'), float('-inf')):
            raise self.Err(f'Поле «{name}» должно быть конечным числом')
        return v

    # ------------------------------------------------------------------ #
    # состояние
    # ------------------------------------------------------------------ #

    async def poll(self, request):
        q = request.query
        s = await self._session(q)
        self.hub.health(s)

        def i(name):
            try:
                return int(q.get(name, 0))
            except ValueError:
                return 0
        feed, feed_rev = s.feed.snapshot(i('msg'))
        journal, log_rev = s.journal.snapshot(i('log'))
        out = {'feed': feed, 'journal': journal, 'msg_rev': feed_rev, 'log_rev': log_rev, 'rev': s.state_rev,
               'status': s.status, 'status_text': s.status_text}
        if i('rev') != s.state_rev or q.get('full'):
            out['state'] = await self.hub.state(s)
        return out

    async def start(self, request):
        b = await self.panel._json(request)
        s = await self._session(b)
        source = b.get('source')
        if not isinstance(source, str) or not source.strip():
            raise self.Err('Нет исходника модуля')
        if len(source) > 2_000_000:
            raise self.Err('Исходник слишком большой')
        s.core_info = await self._core_info()
        result = await self.hub.start(s, source, restart=bool(b.get('restart')))
        return {'result': result, 'rev': s.state_rev}

    async def stop(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        self.hub.stop(s)
        return {'ok': True}

    async def reset(self, request):
        b = await self.panel._json(request)
        self.hub.reset(self._key(b.get('key')))
        return {'ok': True}

    async def clear(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        what = b.get('what')
        if what in ('feed', 'all'):
            s.feed.clear()
            s.msgs.clear()
            s.chat_msgs.clear()
            for c in s.chats.values():
                c.last_id = 0
                c.pinned = None
        if what in ('log', 'all'):
            s.journal.clear()
        s.touch_state()
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # сообщения и события
    # ------------------------------------------------------------------ #

    def _media(self, spec):
        if not spec:
            return None
        if not isinstance(spec, dict):
            raise self.Err('Некорректное вложение')
        kind = spec.get('kind') if spec.get('kind') in MEDIA_KINDS else 'document'
        name = str(spec.get('name') or kind)[:120]
        content = None
        if spec.get('content_b64'):
            try:
                content = base64.b64decode(spec['content_b64'], validate=True)
            except (binascii.Error, ValueError):
                raise self.Err('Не удалось прочитать содержимое файла')
            if len(content) > MAX_UPLOAD:
                raise self.Err('Файл для Sandbox — не больше 1 МБ')
        size = spec.get('size')
        try:
            size = int(size) if size not in (None, '') else None
        except (TypeError, ValueError):
            size = None
        return make_media(kind, name, content=content, size=size)

    async def send(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        chat, sender = b.get('chat'), self._int(b.get('sender'), 'sender')
        reply_to = b.get('reply_to')
        reply_to = self._int(reply_to, 'reply_to') if reply_to not in (None, '') else None
        media = self._media(b.get('media'))
        text = str(b.get('text') or '')
        if not text.strip() and media is None:
            raise self.Err('Введите текст или приложите файл')
        m = await self._run(s, lambda ss: ss.incoming(chat, sender, text, reply_to=reply_to, media=media))
        return {'mid': m.id}

    async def event(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        kind = b.get('kind')
        if kind == 'chat_action':
            user = b.get('user')
            actor = b.get('actor')
            await self._run(s, lambda ss: ss.fire_chat_action(
                b.get('chat'), b.get('action'), self._int(user, 'user') if user not in (None, '') else None,
                self._int(actor, 'actor') if actor not in (None, '') else None, str(b.get('title') or '')[:128] or None))
        elif kind == 'bot_member':
            statuses = ('creator', 'administrator', 'member', 'restricted', 'left', 'kicked')
            old, new = b.get('old_status'), b.get('new_status')
            if old not in statuses or new not in statuses:
                raise self.Err('Неизвестный статус участника')
            await self._run(s, lambda ss: ss.fire_bot_member(b.get('chat'), self._int(b.get('user'), 'user'), old, new, bool(b.get('is_bot_event'))))
        elif kind == 'callback':
            mid = b.get('message_id')
            await self._run(s, lambda ss: ss.fire_callback(b.get('chat'), self._int(b.get('sender'), 'sender'),
                                                           str(b.get('data') or '')[:64], self._int(mid, 'message_id') if mid not in (None, '') else None))
        elif kind == 'error':
            exc = b.get('exc_type')
            if exc is not None and not re.fullmatch(r'[A-Za-z_][\w.]{0,60}', str(exc)):
                raise self.Err('Некорректное имя типа ошибки')
            await self._run(s, lambda ss: ss.fire_error(str(b.get('text') or 'Что-то пошло не так')[:2000], str(b.get('function') or 'my_function')[:80],
                                                        bool(b.get('bot')), exc or None))
        elif kind == 'loadmod':
            spec = b.get('module') if isinstance(b.get('module'), dict) else {}
            await self._run(s, lambda ss: ss.fire_loadmod({k: str(v)[:200] for k, v in spec.items()}))
        elif kind == 'account':
            await self._run(s, lambda ss: ss.fire_account_switch(self._int(b.get('number'), 'number', default=1, minimum=1)))
        elif kind == 'start':
            await self._run(s, lambda ss: ss.fire_start())
        else:
            raise self.Err('Неизвестный вид события')
        return {'ok': True}

    async def click(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        await self._run(s, lambda ss: ss.click(str(b.get('form') or ''), str(b.get('data') or ''), self._int(b.get('user'), 'user')))
        return {'ok': True}

    async def input(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        await self._run(s, lambda ss: ss.send_input(str(b.get('form') or ''), str(b.get('data') or ''), str(b.get('value') or '')[:4096],
                                                    self._int(b.get('user'), 'user')))
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # время
    # ------------------------------------------------------------------ #

    async def time(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        op = b.get('op')
        if op == 'advance':
            seconds = self._num(b.get('seconds'), 'seconds')
            if seconds <= 0:
                raise self.Err('Сдвиг должен быть больше нуля')
            r = await self._run(s, lambda ss: ss.advance(seconds), timeout=60)
            return {'fired': r['fired'], 'truncated': r['truncated']}
        if op == 'set':
            ts = self._num(b.get('ts'), 'ts')
            if not (0 < ts < 4102444800):
                raise self.Err('Дата вне допустимых пределов (1970–2100)')
            r = await self._run(s, lambda ss: ss.set_time(ts), timeout=60)
            return {'fired': r['fired'], 'truncated': r['truncated']}
        if op == 'play':
            speed = self._num(b.get('speed'), 'speed')

            async def go(ss):
                ss.play(speed)
            await self._run(s, go)
            return {'ok': True}
        if op == 'pause':
            async def go(ss):
                ss.pause()
            await self._run(s, go)
            return {'ok': True}
        raise self.Err('Неизвестная операция со временем')

    # ------------------------------------------------------------------ #
    # данные, конфиг, участники, настройки
    # ------------------------------------------------------------------ #

    async def data(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        store, op = b.get('store'), b.get('op')
        if store not in ('db', 'cache'):
            raise self.Err('store: db или cache')
        name = str(b.get('name') or '')

        if op == 'set':
            if not name or len(name) > 300:
                raise self.Err('Укажите ключ (до 300 символов)')
            try:
                value = json.loads(b.get('value') if isinstance(b.get('value'), str) else json.dumps(b.get('value')))
            except ValueError as e:
                raise self.Err(f'Значение должно быть корректным JSON: {e}')

            async def go(ss):
                ss.kv_set(store, name, value)
            await self._run(s, go)
        elif op == 'delete':
            async def go(ss):
                ss.kv_delete(store, name)
            await self._run(s, go)
        elif op == 'clear':
            async def go(ss):
                st = ss.db_store if store == 'db' else ss.cache_store
                st.mem.clear()
                if st.persistent:
                    st.snapshot = '{}'
                ss.note(store, f'{store} очищена из интерфейса')
            await self._run(s, go)
        elif op == 'import':
            if store != 'db':
                raise self.Err('Импортировать можно только db')
            module = s.module.module_name if s.module else s.name
            if not re.fullmatch(r'[\w\-]{1,64}', module or ''):
                raise self.Err('Сначала запустите песочницу — нужен MODULE_NAME модуля')
            prefix = f'{module}:'
            core = self.core
            data = {}
            for k in await core.db.list_keys(prefix):
                data[k] = await core.db.get(k)
            if not data:
                raise self.Err(f'В настоящей базе нет данных модуля «{module}»')

            async def go(ss):
                return ss.import_db(data, prefix)
            n = await self._run(s, go)
            return {'imported': n}
        else:
            raise self.Err('Неизвестная операция с данными')
        return {'ok': True}

    async def config(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        key, op = str(b.get('config_key') or ''), b.get('op')
        if op not in ('edit', 'to_default'):
            raise self.Err('Неизвестная операция с конфигом')
        value = b.get('value')

        async def go(ss):
            return await ss.config_edit(key, op, value)
        try:
            await self._run(s, go)
        except self.Err:
            raise
        return {'ok': True}

    async def people(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        op = b.get('op')

        async def go(ss):
            if op == 'user':
                u = ss.add_user(str(b.get('name') or ''), b.get('username'), bool(b.get('premium')), b.get('id'), b.get('last_name'))
                return u.describe()
            if op == 'chat':
                c = ss.add_chat(str(b.get('title') or ''), b.get('type') or 'group', b.get('side') or 'both', b.get('id'))
                return c.describe()
            if op == 'delete_user':
                ss.delete_user(self._int(b.get('id'), 'id'))
                return {'ok': True}
            if op == 'delete_chat':
                ss.delete_chat(str(b.get('chat_key') or ''))
                return {'ok': True}
            raise ValueError('Неизвестная операция')
        return await self._run(s, go)

    async def settings(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)

        async def go(ss):
            if 'prefix' in b:
                p = str(b['prefix'])
                if not p or len(p) > 5 or p.isspace():
                    raise ValueError('Префикс — от 1 до 5 символов')
                ss.prefix = p
            if 'bot_enabled' in b:
                ss.bot_enabled = bool(b['bot_enabled'])
            ss.note('info', f"настройки: префикс «{ss.prefix}», бот {'включён' if ss.bot_enabled else 'выключен'}")
            ss.touch_state()
        await self._run(s, go)
        return {'ok': True}

    async def page(self, request):
        b = await self.panel._json(request)
        s = await self._session(b, create=False)
        index = self._int(b.get('index'), 'index')
        values = b.get('values') if isinstance(b.get('values'), dict) else {}
        return await self._run(s, lambda ss: ss.render_page(index, values, b.get('action')))

