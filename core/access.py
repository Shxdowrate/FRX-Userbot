"""
core/access.py — доступ к юзерботу для других людей и алиасы команд.

ДОСТУП
------
Владелец может разрешить другим пользователям Telegram запускать команды своего юзербота: гость пишет
`<префикс>команда` в чате, где его видит аккаунт владельца (ЛС, группа), и команда выполняется так же, как
если бы её написал владелец (ответ — новым сообщением на сообщение гостя).

Доступ складывается только из ВЫДАЧ — запретов нет. Выдача бывает трёх видов:

    scope    'all' — все модули, 'builtin' — все встроенные, 'loaded' — все загруженные (user/modules)
    module   модуль целиком (его MODULE_NAME)
    command  одна команда: "MODULE_NAME:первое_имя_команды" (первый аргумент @decorators.command(...))

Выдачи хранятся у КЛАССА (набор выдач с именем) и лично у пользователя:

    класс        = {title, description, scopes: [...], modules: [...], commands: [...], system}
                   ключ класса — его idn: одно слово (буквы, цифры, _), например FULL, helpers, my_friends
    пользователь = {name, username, class (idn или None), scopes, modules, commands, added}

Команда разрешена гостю, если её покрывает хоть одна выдача — его личная или его класса. Исключения:
модули из PROTECTED_MODULES и команды из PROTECTED_COMMANDS (управление доступом, алиасами, автозапуском и полный
лог — всё это в модуле Userbot) — всегда только владелец, даже если гостю выдан модуль целиком или scope.

Ничего не запрещено выдавать, но часть модулей помечается КРИТИЧЕСКИ ОПАСНЫМИ (danger_reasons): доступ к ним
фактически равен доступу ко всему аккаунту — они выполняют код, загружают модули, работают с файлами,
сессиями, перезапуском. Интерфейсы (`.access`, сайт) показывают пометку и переспрашивают перед выдачей.

Классы FULL (системный: все модули, удалить и изменить его выдачи нельзя) и LOADED создаются сами.

АЛИАСЫ
------
`.alias help h` — `.h` запускает `.help`; `.alias config helper c` — `.c` запускает `.config helper`, а
`.c x` — `.config helper x`. Алиас не может совпадать с существующей командой (команда всегда главнее).

Хранение — основная БД: ключи `core:access` и `core:aliases`.
"""

import copy
import re
import time

ACCESS_KEY = 'core:access'
ALIAS_KEY = 'core:aliases'

FULL = 'FULL'
LOADED = 'LOADED'
SCOPES = ('all', 'builtin', 'loaded')
SCOPE_TITLES = {'all': 'все модули', 'builtin': 'все встроенные модули', 'loaded': 'все загруженные модули'}
KINDS = ('scope', 'module', 'command')
_BUCKET = {'scope': 'scopes', 'module': 'modules', 'command': 'commands'}
PROTECTED_MODULES = frozenset({'access', 'aliases'})
# Команды модуля Userbot (modules/builtin/Userbot.py), которые остаются за владельцем: Userbot объединил прежние
# Access, Aliases, Autostart, Logging и др., поэтому защищать модуль целиком больше нельзя — .ping и .info гостю можно.
PROTECTED_COMMANDS = frozenset({'userbot:access', 'userbot:alias', 'userbot:unalias', 'userbot:layout',
                                'userbot:autostart', 'userbot:fulllog'})

# Модули, слитые в Userbot (FRX 1.0.6): старые выдачи «модуль X» / «команда X:y» переносятся на команды Userbot.
# None — команда убрана (служебная), выдача просто снимается.
MERGED_INTO_USERBOT = {
    'ping': {'ping': 'ping', 'dping': 'ping', 'speedtest': 'speedtest', 'checkhost': 'checkhost', 'ip': 'ip'},
    'info': {'info': 'info', 'doctor': 'doctor'},
    'changelog': {'changelog': 'changelog'},
    'logging': {'fulllog': 'fulllog', 'logs': None},
    'inline_stuff': {'forms': None, 'closeform': None},
    'autostart': {'autostart': 'autostart'},
    'access': {'access': 'access'},
    'aliases': {'alias': 'alias', 'unalias': 'unalias', 'layout': 'layout'},
    'waiters': {},
}


def _migrate_merged(obj) -> bool:
    """Выдачи на модули, слитые в Userbot → выдачи на соответствующие команды userbot. True — что-то поменялось."""
    changed = False
    mods, cmds = [], list(obj.get('commands') or [])
    for m in obj.get('modules') or []:
        if m in MERGED_INTO_USERBOT:
            changed = True
            cmds += [f"userbot:{new}" for new in MERGED_INTO_USERBOT[m].values() if new]
        else:
            mods.append(m)
    out = []
    for c in cmds:
        mod, _, name = c.partition(':')
        if mod in MERGED_INTO_USERBOT:
            changed = True
            new = MERGED_INTO_USERBOT[mod].get(name)
            c = f"userbot:{new}" if new else None
        if c and c not in PROTECTED_COMMANDS and c not in out:
            out.append(c)
        elif c:
            changed = True
    obj['modules'], obj['commands'] = mods, out
    return changed

CLASS_RE = re.compile(r'^\w{1,32}$')
ALIAS_RE = re.compile(r'^[^\s]{1,32}$')

DEFAULT_CLASSES = {
    FULL: {'title': 'Полный доступ', 'system': True, 'scopes': ['all'], 'modules': [], 'commands': [],
           'description': 'все команды всех модулей, включая .eval, загрузку модулей и перезапуск — '
                          'это полный контроль над аккаунтом'},
    LOADED: {'title': 'Загруженные модули', 'scopes': ['loaded'], 'modules': [], 'commands': [],
             'description': 'команды ваших модулей из user/modules; встроенные — нет'},
}

# ---------------------------------------------------------------------------
# Опасные модули
# ---------------------------------------------------------------------------

CRITICAL_BUILTIN = {
    'eval': 'выполняет любой Python-код от имени аккаунта',
    'loader': 'загружает, выгружает и удаляет модули — то есть может запустить любой код',
    'systemkeeper': 'обновляет, перезапускает и останавливает юзербот',
    'backup': 'создаёт и восстанавливает бэкапы, а в них сессии и все данные',
    'account_manager': 'добавляет и переключает аккаунты, работает с сессиями',
    'neurodash': 'нейро-агент с инструментами для файлов и кода',
    'web_panel': 'выдаёт ссылку входа на сайт управления — а с сайта доступно всё',
    'config': 'меняет настройки любых модулей, включая префикс и токены',
}

_DANGER_PATTERNS = [
    (re.compile(r'\b(?:exec|eval|compile)\s*\(|__import__\s*\(|\bimportlib\b'), 'выполняет произвольный код'),
    (re.compile(r'\b(?:load_module|_load_from_source|reload_module|unload_module|restore_module|'
                r'delete_unloaded_module|kill_module)\s*\('), 'загружает или выгружает модули'),
    (re.compile(r'\bsubprocess\b|\bos\.(?:system|popen|exec\w*|spawn\w*)\b|create_subprocess_\w+'),
     'запускает программы на компьютере'),
    (re.compile(r'(?<![\w.])open\s*\(|\bos\.(?:remove|unlink|rmdir|rename|replace|makedirs|listdir|walk)\b|'
                r'\bshutil\.|\bpathlib\b|\bPath\s*\(|^\s*import files\b|\bfiles\.\w+\s*\(', re.M),
     'читает или меняет файлы'),
    (re.compile(r'\bdownload_(?:media|file|reply_file)\s*\('), 'скачивает файлы на компьютер'),
    (re.compile(r'^\s*import accounts\b|\baccounts\.\w+\s*\(|StringSession', re.M), 'работает с аккаунтами и сессиями'),
]


def danger_reasons(module_name, source='', is_builtin=False) -> list:
    """Почему доступ к модулю критически опасен ([] — не опасен). Встроенные — по списку, загруженные — по коду."""
    if is_builtin:
        why = CRITICAL_BUILTIN.get(module_name)
        return [why] if why else []
    out = []
    for rx, why in _DANGER_PATTERNS:
        if rx.search(source or '') and why not in out:
            out.append(why)
    return out


class AccessError(Exception):
    """Ошибка управления доступом с понятным текстом."""


def command_key(module_name, func) -> str:
    names = getattr(func, '_frx_commands', None) or [getattr(func, '__name__', '?')]
    return f"{module_name}:{names[0]}"


def _uid(user_id) -> str:
    try:
        return str(int(user_id))
    except (TypeError, ValueError):
        raise AccessError(f"Некорректный id пользователя: {user_id}")


def _as_list(value, allowed=None):
    """Старый формат {ключ: bool} → [ключи с True]; список — как есть (без дублей)."""
    if isinstance(value, dict):
        items = [k for k, v in value.items() if v]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        items = []
    out = []
    for k in items:
        if isinstance(k, str) and k and k not in out and (allowed is None or k in allowed):
            out.append(k)
    return out


def _normalize_grants(obj):
    """Приводит выдачи к спискам; переносит формат ранних сборок (scope + правила да/нет) в «только выдачи»."""
    scopes = obj.get('scopes')
    if scopes is None:
        old = obj.get('scope')
        scopes = [old] if old in SCOPES else []
    obj['scopes'] = _as_list(scopes, SCOPES)
    obj['modules'] = _as_list(obj.get('modules'))
    obj['commands'] = _as_list(obj.get('commands'))
    for legacy in ('scope', 'forms'):
        obj.pop(legacy, None)
    return obj


def describe_grant(kind, value, prefix='.') -> str:
    if kind == 'scope':
        return SCOPE_TITLES.get(value, value)
    if kind == 'module':
        return f"модуль {value}"
    return f"команда {prefix}{value.split(':', 1)[-1]} ({value.split(':', 1)[0]})"


class AccessManager:
    """Права гостей. Данные держатся в памяти и сохраняются в БД при каждом изменении."""

    def __init__(self, db):
        self.db = db
        self._data = None
        self._last_cmd = {}                      # user_id -> monotonic последней команды (антифлуд)

    # ------------------------------------------------------------------ хранение
    async def load(self):
        raw = await self.db.get(ACCESS_KEY, None) or {}
        classes = raw.get('classes') if isinstance(raw.get('classes'), dict) else {}
        migrated = False
        for name, spec in DEFAULT_CLASSES.items():
            if name not in classes:
                classes[name] = copy.deepcopy(spec)
        for name, spec in classes.items():
            default = DEFAULT_CLASSES.get(name, {})
            for key in ('title', 'description'):
                if not spec.get(key) or (key == 'title' and spec[key] == name):
                    spec[key] = default.get(key) or spec.get(key) or (name if key == 'title' else '')
            spec.setdefault('description', '')
            _normalize_grants(spec)
            migrated |= _migrate_merged(spec)
        classes[FULL].update({'system': True, 'scopes': ['all'], 'modules': [], 'commands': []})
        users = raw.get('users') if isinstance(raw.get('users'), dict) else {}
        for u in users.values():
            u.setdefault('name', '')
            u.setdefault('username', '')
            u.setdefault('class', None)
            u.setdefault('added', time.time())
            if u['class'] not in classes:
                u['class'] = None
            _normalize_grants(u)
            migrated |= _migrate_merged(u)
        self._data = {'classes': classes, 'users': users}
        if migrated:
            await self._save()
        return self

    def _d(self):
        if self._data is None:
            raise RuntimeError("AccessManager не загружен (await access.load())")
        return self._data

    async def _save(self):
        await self.db.set(ACCESS_KEY, self._d())

    # ------------------------------------------------------------------ чтение
    @property
    def loaded(self) -> bool:
        return self._data is not None

    def has_users(self) -> bool:
        return bool(self._data and self._data['users'])

    def classes(self) -> dict:
        return self._d()['classes']

    def users(self) -> dict:
        return self._d()['users']

    def get_user(self, user_id):
        return self._d()['users'].get(_uid(user_id))

    def get_class(self, name):
        return self._d()['classes'].get(name)

    def user_class(self, user_id):
        u = self.get_user(user_id)
        return self.get_class(u['class']) if u and u.get('class') else None

    @staticmethod
    def has_grants(obj) -> bool:
        return bool(obj and (obj.get('scopes') or obj.get('modules') or obj.get('commands')))

    def _layers(self, user_id):
        u = self.get_user(user_id)
        if not u:
            return []
        layers = [('личная выдача', u)]
        cls = self.get_class(u['class']) if u.get('class') else None
        if cls:
            layers.append((f"класс {u['class']}", cls))
        return layers

    @staticmethod
    def _scope_covers(scopes, is_builtin):
        for s in scopes:
            if s == 'all' or (s == 'builtin' and is_builtin) or (s == 'loaded' and not is_builtin):
                return s
        return None

    def module_allowed(self, user_id, module_name, is_builtin):
        """(да/нет, причина): доступен ли гостю модуль ЦЕЛИКОМ (выдача модуля или scope)."""
        if module_name in PROTECTED_MODULES:
            return False, 'только владелец'
        for label, layer in self._layers(user_id):
            s = self._scope_covers(layer['scopes'], is_builtin)
            if s:
                return True, f"{label}: {SCOPE_TITLES[s]}"
            if module_name in layer['modules']:
                return True, f"{label}: модуль {module_name}"
        return False, 'нет доступа'

    def check(self, user_id, module_name, cmd_key, is_builtin):
        """(разрешено: bool, причина: str) для команды cmd_key модуля module_name."""
        if not self.get_user(user_id):
            return False, 'нет доступа'
        if cmd_key in PROTECTED_COMMANDS:
            return False, 'только владелец'
        ok, why = self.module_allowed(user_id, module_name, is_builtin)
        if ok or module_name in PROTECTED_MODULES:
            return ok, why
        for label, layer in self._layers(user_id):
            if cmd_key in layer['commands']:
                return True, f"{label}: команда"
        return False, 'нет выдачи на эту команду'

    def cooldown_ok(self, user_id, seconds) -> bool:
        now = time.monotonic()
        key = _uid(user_id)
        if seconds and now - self._last_cmd.get(key, -1e9) < float(seconds):
            return False
        self._last_cmd[key] = now
        return True

    # ------------------------------------------------------------------ пользователи
    async def add_user(self, user_id, name='', username='', cls=None):
        key = _uid(user_id)
        if cls is not None and cls not in self.classes():
            raise AccessError(f"Класса «{cls}» нет")
        users = self.users()
        u = users.get(key)
        if u is None:
            u = users[key] = {'name': name or '', 'username': username or '', 'class': cls,
                              'scopes': [], 'modules': [], 'commands': [], 'added': time.time()}
        else:
            if name:
                u['name'] = name
            if username:
                u['username'] = username
            if cls is not None:
                u['class'] = cls
        await self._save()
        return u

    async def remove_user(self, user_id):
        """Отозвать весь доступ: пользователь исчезает из списка."""
        if self.users().pop(_uid(user_id), None) is None:
            raise AccessError("У этого пользователя нет доступа")
        await self._save()

    def _prune(self, user_id):
        """Пользователь без класса и без выдач больше не гость — убираем запись."""
        u = self.get_user(user_id)
        if u is not None and not u.get('class') and not self.has_grants(u):
            self.users().pop(_uid(user_id), None)

    async def set_user_class(self, user_id, cls, name='', username=''):
        """Выдать класс (cls=None — снять класс). Пользователя без записи заводит."""
        if cls is not None and cls not in self.classes():
            raise AccessError(f"Класса «{cls}» нет")
        u = self.get_user(user_id)
        if u is None:
            if cls is None:
                raise AccessError("У этого пользователя нет доступа")
            u = await self.add_user(user_id, name, username, cls)
            return u
        if name:
            u['name'] = name
        if username:
            u['username'] = username
        u['class'] = cls
        self._prune(user_id)
        await self._save()
        return self.get_user(user_id)

    # ------------------------------------------------------------------ выдачи
    def _target(self, target, ident, create=False):
        if target == 'user':
            obj = self.get_user(ident)
            if not obj and not create:
                raise AccessError("У этого пользователя нет доступа")
        elif target == 'class':
            obj = self.get_class(ident)
            if not obj:
                raise AccessError(f"Класса «{ident}» нет")
            if obj.get('system'):
                raise AccessError("Выдачи системного класса FULL менять нельзя")
        else:
            raise AccessError("target: user или class")
        return obj

    @staticmethod
    def _check_grant(kind, value):
        if kind not in KINDS:
            raise AccessError("Вид выдачи: scope, module или command")
        if not value or not isinstance(value, str):
            raise AccessError("Не указан модуль или команда")
        if kind == 'scope' and value not in SCOPES:
            raise AccessError("scope: all, builtin или loaded")
        if kind == 'command' and ':' not in value:
            raise AccessError("Команда указывается как модуль:команда")
        if kind in ('module', 'command') and value.split(':', 1)[0] in PROTECTED_MODULES:
            raise AccessError("Управление доступом и алиасами — только для владельца")
        if kind == 'command' and value in PROTECTED_COMMANDS:
            raise AccessError("Эта команда — только для владельца (доступ, алиасы, автозапуск, полный лог)")

    async def grant(self, target, ident, kind, value, name='', username=''):
        """Выдать доступ (scope / модуль / команду) классу или пользователю (пользователя заводит при надобности)."""
        self._check_grant(kind, value)
        obj = self._target(target, ident, create=True)
        if obj is None:
            obj = await self.add_user(ident, name, username)
        bucket = obj[_BUCKET[kind]]
        if value not in bucket:
            bucket.append(value)
        if kind == 'module':                      # выдачи отдельных команд этого модуля больше не нужны
            obj['commands'] = [c for c in obj['commands'] if c.split(':', 1)[0] != value]
        await self._save()
        return obj

    async def revoke(self, target, ident, kind, value):
        if kind not in KINDS:
            raise AccessError("Вид выдачи: scope, module или command")
        obj = self._target(target, ident)
        bucket = obj[_BUCKET[kind]]
        if value not in bucket:
            raise AccessError("Такой выдачи нет")
        bucket.remove(value)
        if target == 'user':
            self._prune(ident)
        await self._save()
        return obj

    def grants_of(self, obj) -> list:
        """[(kind, value), ...] выдач объекта (класса или пользователя)."""
        if not obj:
            return []
        return ([('scope', s) for s in obj.get('scopes', [])] + [('module', m) for m in obj.get('modules', [])]
                + [('command', c) for c in obj.get('commands', [])])

    # ------------------------------------------------------------------ классы
    async def create_class(self, idn, title='', description='', copy_from=None):
        idn = (idn or '').strip()
        if not CLASS_RE.match(idn):
            raise AccessError("idn класса — одно слово: буквы, цифры и _ (до 32 символов, без пробелов)")
        if idn in self.classes():
            raise AccessError(f"Класс «{idn}» уже есть")
        spec = {'title': (title or idn).strip()[:64], 'description': (description or '').strip()[:300],
                'scopes': [], 'modules': [], 'commands': []}
        if copy_from:
            src = self.get_class(copy_from)
            if not src:
                raise AccessError(f"Класса «{copy_from}» нет")
            spec.update({k: list(src[k]) for k in ('scopes', 'modules', 'commands')})
        self.classes()[idn] = spec
        await self._save()
        return spec

    async def update_class(self, idn, title=None, description=None):
        spec = self.get_class(idn)
        if not spec:
            raise AccessError(f"Класса «{idn}» нет")
        if title is not None:
            spec['title'] = str(title).strip()[:64] or idn
        if description is not None:
            spec['description'] = str(description).strip()[:300]
        await self._save()
        return spec

    async def rename_class(self, idn, new_idn):
        spec = self.get_class(idn)
        if not spec:
            raise AccessError(f"Класса «{idn}» нет")
        if spec.get('system'):
            raise AccessError("Системный класс переименовать нельзя")
        new_idn = (new_idn or '').strip()
        if new_idn == idn:
            return spec
        if not CLASS_RE.match(new_idn):
            raise AccessError("idn класса — одно слово: буквы, цифры и _ (до 32 символов, без пробелов)")
        if new_idn in self.classes():
            raise AccessError(f"Класс «{new_idn}» уже есть")
        classes = self.classes()
        self._d()['classes'] = {(new_idn if k == idn else k): v for k, v in classes.items()}
        for u in self.users().values():
            if u.get('class') == idn:
                u['class'] = new_idn
        await self._save()
        return spec

    async def delete_class(self, idn):
        spec = self.get_class(idn)
        if not spec:
            raise AccessError(f"Класса «{idn}» нет")
        if spec.get('system'):
            raise AccessError("Системный класс удалить нельзя")
        del self.classes()[idn]
        moved = 0
        for uid, u in list(self.users().items()):
            if u.get('class') == idn:
                u['class'] = None
                moved += 1
                self._prune(uid)
        await self._save()
        return moved


class AliasManager:
    """Алиасы команд: {имя: {'command': 'help', 'args': '...'}}."""

    def __init__(self, db):
        self.db = db
        self._data = None

    async def load(self):
        raw = await self.db.get(ALIAS_KEY, None)
        self._data = raw if isinstance(raw, dict) else {}
        changed = False
        for a in self._data.values():                   # .dping стал режимом .ping -d (модуль Userbot)
            if isinstance(a, dict) and a.get('command') == 'dping':
                a['command'], a['args'] = 'ping', ' '.join(x for x in ('-d', a.get('args') or '') if x)
                changed = True
        if changed:
            await self.db.set(ALIAS_KEY, self._data)
        return self

    def all(self) -> dict:
        return dict(self._data or {})

    def get(self, name):
        return (self._data or {}).get(str(name).lower())

    async def set(self, name, command, args='', existing_commands=()):
        name = str(name or '').strip().lower()
        command = str(command or '').strip().lower()
        if not ALIAS_RE.match(name):
            raise AccessError("Имя алиаса — одно слово без пробелов (до 32 символов)")
        if not command:
            raise AccessError("Не указана команда")
        if name in existing_commands:
            raise AccessError(f"«{name}» — уже существующая команда; алиас не может её перекрыть")
        if command not in existing_commands:
            raise AccessError(f"Команды «{command}» нет")
        if name == command:
            raise AccessError("Алиас совпадает с командой")
        self._data[name] = {'command': command, 'args': str(args or '').strip()}
        await self.db.set(ALIAS_KEY, self._data)
        return self._data[name]

    async def delete(self, name):
        name = str(name or '').strip().lower()
        if self._data.pop(name, None) is None:
            raise AccessError(f"Алиаса «{name}» нет")
        await self.db.set(ALIAS_KEY, self._data)

    def expand(self, name, rest):
        """(команда, аргументы) для алиаса name и того, что пользователь дописал после него; None — не алиас."""
        a = self.get(name)
        if not a:
            return None
        args = ' '.join(x for x in (a.get('args') or '', rest or '') if x)
        return a['command'], args
