"""
core/web/server.py — HTTP-сервер локального сайта .web: страницы, API, Emulate, FRX WebIDE

WebPanel — сайт управления FRX. Поднимается командой `.web` (modules/builtin/Web.py), сам выключается после
5 минут без активности. Слушает 127.0.0.1 (в Docker — FRX_WEB_BIND); снаружи открывается через SSH-туннель
или публичный туннель Cloudflare, который держит keeper (frx_systemkeeper.py, TunnelManager).

Безопасность:
  - вход обязателен: код из Telegram (вход открывается только командой .web и на один вход) или Telegram Mini App
    (core/web/auth.py), дальше — cookie сессии;
  - Host-заголовок обязан быть из списка разрешённых: 127.0.0.1/localhost/[::1], адрес туннеля, web_hosts из
    user/config.json — закрывает DNS-rebinding;
  - каждый /api-запрос обязан нести заголовок X-FRX: браузер не отправит кастомный
    заголовок с чужого сайта без CORS-preflight, а на preflight мы не отвечаем —
    поэтому никакая открытая вкладка/сайт не сможет ничего дёрнуть у панели;
  - Origin, если он есть, тоже обязан быть из разрешённых; страницы встраиваются во frame только
    в Telegram (Mini App в Telegram Web).
Панель умеет ставить/редактировать модули: не открывайте её порт наружу напрямую (0.0.0.0 без Docker,
проброс порта) — для этого есть туннели.

Что умеет: конфиги (аналог Config), модули (аналог Loader/Help + редактор-IDE),
аккаунты (аналог AccountManager), страницы модулей, описанные через `import web`
(core/web/ui.py), и Emulate — автоматическая отрисовка команд, форм и
диалогов ЛЮБОГО модуля (core/emulate.py, README → «Локальный сайт .web» → Emulate).
"""

import ast
import asyncio
import json
import mimetypes
import os
import re
import time
import traceback
from pathlib import Path
from urllib.parse import urlparse

from aiohttp import web
from telethon.errors import (
    FloodWaitError, PasswordHashInvalidError, PhoneCodeExpiredError, PhoneCodeInvalidError,
    PhoneNumberBannedError, PhoneNumberFloodError, PhoneNumberInvalidError,
)

from ..context import current_module
from ..paths import KEEPER_DIR, MODULES_DIR, CONFIG_PATH, atomic_write_text
from .auth import WebAuth, AuthError, COOKIE
from ..module_meta import parse_module_meta
from ..emulate import EmuSession, describe_commands
from . import ui as ui_mod

STATIC_DIR = Path(__file__).parent / 'static'
STATIC_FILES = {'login.html', 'login.css', 'login.js', 'style.css', 'app.js', 'font.woff2', 'botchat.js', 'botchat.css', 'webide.js', 'webide.css', 'sandbox.js', 'lab_core.js', 'lab.js', 'lab.css', 'panel.js', 'panel.css', 'neuro.js', 'neuro.css', 'accent.css', 'fx.js', 'access.js', 'access.css', 'depth.js', 'depth.css', 'mobile.css', 'mobile.js', 'logo.svg', 'icon.png'}
DEFAULT_PORT = 8878
IDLE_TIMEOUT = 5 * 60          # значение по умолчанию (конфиг `autostop_minutes` модуля Web); секунд без запросов -> сайт выключается
WEB_MODULE = 'web_panel'       # MODULE_NAME модуля Web: его конфиги (автозапуск, автовыключение, настройки Emulate)
STATE_FILE = KEEPER_DIR / 'web_state.json'      # сайт «переживает» рестарт — см. frx_systemkeeper.py
TAKEOVER_FILE = KEEPER_DIR / 'web_takeover'     # новый процесс просит keeper освободить порт
WAKE_FILE = KEEPER_DIR / 'web_wake'             # сторож keeper'а: «кто-то зашёл на localhost — поднимай сайт»
SENTINEL_FILE = KEEPER_DIR / 'web_sentinel.json'  # keeper держит порт сайта лёгким сторожем
RESUME_MAX_AGE = 15 * 60
CHANGELOG_PATH = Path(__file__).resolve().parents[2] / 'CHANGELOG.md'
MODULE_ID_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
REQUIRED_META = ('NAME', 'MODULE_NAME', 'VERSION', 'AUTHOR', 'DESCRIPTION')
LOCAL_HOSTS = {'127.0.0.1', 'localhost', '[::1]'}
TUNNEL_REQUEST_FILE = KEEPER_DIR / 'tunnel_request.json'
TUNNEL_STATE_FILE = KEEPER_DIR / 'tunnel.json'
PREV_HOST_GRACE = 300          # сколько секунд после смены публичного адреса ещё пускать со старого (переезд страниц)
PUBLIC_PATHS = {'/api/auth/request', '/api/auth/verify', '/api/auth/telegram', '/api/auth/state', '/api/auth/ticket'}
TELEGRAM_FRAMES = "https://web.telegram.org https://*.telegram.org"
PASSIVE_PATHS = {'/api/auth/state', '/api/emulate/poll', '/api/botchat/poll', '/botchat/avatar', '/api/logs', '/api/tunnel/where'}      # опрос ленты Emulate сам по себе не «активность» (см. _guard)

mimetypes.add_type('font/woff2', '.woff2')
mimetypes.add_type('image/webp', '.webp')


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _hostname(host: str) -> str:
    if host.startswith('['):
        return host.split(']')[0] + ']'
    return host.rsplit(':', 1)[0] if ':' in host else host


def analyze_source(source: str) -> dict:
    """Статическая проверка исходника модуля для IDE: синтаксис, метаданные, импорты."""
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        return {'ok': False, 'error': {'line': e.lineno or 1, 'msg': e.msg or 'синтаксическая ошибка'},
                'have': [], 'imports': [], 'meta': None}
    have, imports = set(), set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id in REQUIRED_META:
                    have.add(t.id)
        elif isinstance(node, ast.Import):
            imports.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split('.')[0])
    meta = parse_module_meta(source)
    error = None
    if meta is None:
        error = {'line': 1, 'msg': 'не задан MODULE_NAME (строкой)'}
    elif not MODULE_ID_RE.match(meta['module_name']):
        error = {'line': 1, 'msg': 'MODULE_NAME: только латиница, цифры, _ и - (до 64 символов)'}
    return {'ok': error is None, 'error': error, 'have': sorted(have),
            'imports': sorted(imports), 'meta': meta}


class WebPanel:
    def __init__(self, core):
        self.core = core
        self._runner = None
        self._site = None
        self._watch = None
        self._port = None
        self._last = time.monotonic()
        self._login = None
        self._page_states = {}
        self._emu = {}                 # module_name -> EmuSession
        self._webide = None            # WebIdeApi (ставится в _add_routes)
        self.start_hook = None         # /start web в боте (ставит модуль Web: свежая ссылка на сайт)
        self._neuro = None             # NeuroApi
        self._panel_api = None         # README / логи / файлы
        self._sandbox = None           # SandboxApi (песочница модулей)
        self._sleeping = False         # идёт рестарт юзербота — сайт жив, но действия недоступны
        self.auth = WebAuth(core)      # вход: код из Telegram / Mini App, cookie сессии

    # ------------------------------------------------------------------ #
    # Жизненный цикл
    # ------------------------------------------------------------------ #

    @property
    def running(self) -> bool:
        return self._runner is not None

    @property
    def port(self):
        return self._port

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    @staticmethod
    def bind_host() -> str:
        return (os.environ.get('FRX_WEB_BIND') or '').strip() or '127.0.0.1'

    # ---------------- публичный туннель (держит keeper) ----------------
    @staticmethod
    def tunnel_state() -> dict:
        try:
            d = json.loads(TUNNEL_STATE_FILE.read_text(encoding='utf-8'))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    @staticmethod
    def tunnel_wanted() -> bool:
        try:
            return bool(json.loads(TUNNEL_REQUEST_FILE.read_text(encoding='utf-8')).get('enable'))
        except (OSError, ValueError, AttributeError):
            return False

    TUNNEL_SETUP_KEYS = ('mode', 'lhr_key', 'cf_token', 'cf_host', 'custom_url')

    @staticmethod
    def tunnel_setup() -> dict:
        """Способ публичного адреса (см. frx_systemkeeper.py, TUNNEL_MODES): {'mode', 'lhr_key', 'cf_token', 'cf_host', 'custom_url'}."""
        try:
            prev = json.loads(TUNNEL_REQUEST_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prev = {}
        out = {k: prev.get(k) for k in WebPanel.TUNNEL_SETUP_KEYS}
        out['mode'] = out.get('mode') or 'auto'
        return out

    def request_tunnel(self, enable: bool, renew: bool = False, setup: dict = None):
        """Просит keeper включить/выключить туннель (renew — новый адрес; setup — сменить способ, см. tunnel_setup).
        Без keeper'а — RuntimeError."""
        if os.environ.get('FRX_KEEPER') != '1':
            raise RuntimeError("публичный туннель работает только при запуске через frx_systemkeeper.py")
        try:
            prev = json.loads(TUNNEL_REQUEST_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            prev = {}
        port = self._port or int(self.core.config.get('web_port') or DEFAULT_PORT)
        data = {'enable': bool(enable), 'port': port, 'renew': time.time() if renew else prev.get('renew')}
        for k in self.TUNNEL_SETUP_KEYS:
            data[k] = setup[k] if setup is not None and k in setup else prev.get(k)
        if data != {k: prev.get(k) for k in data}:
            atomic_write_text(TUNNEL_REQUEST_FILE, json.dumps(data))

    @staticmethod
    async def lhr_pubkey(create: bool = True):
        """Публичный ключ SSH для localhost.run (user/data/bin/lhr_key.pub). Нет ключа — создаёт его ssh-keygen'ом
        (create=True). Возвращает строку «ssh-ed25519 …» или бросает RuntimeError с понятной причиной."""
        import shutil
        from ..paths import DATA_DIR
        key = DATA_DIR / 'bin' / 'lhr_key'
        pub = key.with_name('lhr_key.pub')
        if pub.is_file() and key.is_file():
            return pub.read_text(encoding='utf-8').strip()
        if not create:
            return None
        exe = shutil.which('ssh-keygen')
        if not exe:
            raise RuntimeError("нет ssh-keygen — поставьте OpenSSH Client (Windows: Параметры → Приложения → Дополнительные компоненты)")
        key.parent.mkdir(parents=True, exist_ok=True)
        for f in (key, pub):
            f.unlink(missing_ok=True)
        proc = await asyncio.create_subprocess_exec(exe, '-q', '-t', 'ed25519', '-N', '', '-C', 'frx-localhost.run', '-f', str(key),
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), 30)
        if proc.returncode != 0 or not pub.is_file():
            raise RuntimeError("ssh-keygen не смог создать ключ: " + (out or b'').decode('utf-8', 'replace')[-200:])
        return pub.read_text(encoding='utf-8').strip()

    async def wait_tunnel(self, timeout: float = 150, on_progress=None) -> dict:
        """Ждёт, пока туннель поднимется. keeper между попытками сам перезапускает cloudflared (и пробует запасной
        localhost.run), поэтому ошибка одной попытки — не конец: ждём до timeout. on_progress(state, elapsed) —
        async-колбэк, вызывается примерно раз в секунду (юзербот показывает владельцу, какая идёт попытка)."""
        started = time.monotonic()
        deadline = started + timeout
        await asyncio.sleep(1.0)
        while time.monotonic() < deadline:
            st = self.tunnel_state()
            status = st.get('status')
            if status == 'up':
                return st
            if status == 'off' and not self.tunnel_wanted():
                return st
            if on_progress is not None:
                try:
                    await on_progress(st, time.monotonic() - started)
                except Exception:
                    pass
            await asyncio.sleep(1.0)
        st = self.tunnel_state() or {}
        if st.get('status') != 'up':
            st = {**st, 'status': 'error', 'error': st.get('error') or f'туннель не поднялся за {int(timeout)} с'}
        return st

    def public_url(self):
        st = self.tunnel_state()
        return st.get('url') if st.get('status') == 'up' else None

    def allowed_hosts(self) -> set:
        out = set(LOCAL_HOSTS)
        st = self.tunnel_state()
        if st.get('host'):
            out.add(str(st['host']).lower())
        # прежний адрес туннеля ещё несколько минут пускаем: открытые на нём страницы успеют переехать на новый
        if st.get('prev_host') and time.time() - float(st.get('since') or 0) < PREV_HOST_GRACE:
            out.add(str(st['prev_host']).lower())
        extra = self.core.config.get('web_hosts')
        if isinstance(extra, list):
            out.update(str(h).strip().lower() for h in extra if str(h).strip())
        return out

    def touch(self):
        self._last = time.monotonic()

    async def _cfg(self, key, default):
        """Конфиг модуля Web; нет модуля/ключа — значение по умолчанию."""
        try:
            return await self.core.get_config(key, module_name=WEB_MODULE)
        except Exception:
            return default

    async def idle_limit(self):
        """Секунд простоя до автовыключения; None — автовыключение отключено в конфигах."""
        if not await self._cfg('autostop', True):
            return None
        minutes = await self._cfg('autostop_minutes', IDLE_TIMEOUT // 60)
        try:
            return max(1, int(minutes)) * 60
        except (TypeError, ValueError):
            return IDLE_TIMEOUT

    async def start(self, port=None, takeover=False, idle_left=None) -> str:
        """
        Поднимает сайт (если ещё не поднят), сбрасывает таймер простоя, возвращает URL.
        takeover=True (после рестарта) — занять ТОТ ЖЕ порт, который до этого держал keeper
        (его «заглушка» с надписью «юзербот спит»): просим её уйти файлом TAKEOVER_FILE и пробуем занять порт.
        """
        if self.running:
            self.touch()
            return self.url
        self._sleeping = False
        if port is None and not takeover:                  # порт держит сторож keeper'а — просим отдать, а не уходим на соседний
            sp = self._sentinel_port()
            if sp:
                port, takeover = sp, True
        app = web.Application(middlewares=[self._guard], client_max_size=60 * 1024 * 1024)
        self._add_routes(app)
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=2)   # открытые long-poll'ы браузера не держат остановку
        await runner.setup()
        first = int(port or self.core.config.get('web_port') or DEFAULT_PORT)
        site = None
        if takeover and port:
            try:
                TAKEOVER_FILE.parent.mkdir(parents=True, exist_ok=True)
                TAKEOVER_FILE.write_text(str(os.getpid()), encoding='utf-8')
            except OSError:
                pass
            deadline = time.monotonic() + 30
            while site is None and time.monotonic() < deadline:
                candidate = web.TCPSite(runner, self.bind_host(), first)
                try:
                    await candidate.start()
                    site, self._port = candidate, first
                except OSError:
                    await asyncio.sleep(0.15)
            TAKEOVER_FILE.unlink(missing_ok=True)
        if site is None:
            for p in range(first, first + 20):
                candidate = web.TCPSite(runner, self.bind_host(), p)
                try:
                    await candidate.start()
                except OSError:
                    continue
                site, self._port = candidate, p
                break
        if site is None:
            await runner.cleanup()
            raise RuntimeError(f"Не нашёл свободный порт в диапазоне {first}-{first + 19}")
        self._runner, self._site = runner, site
        self.touch()
        if idle_left is not None:                      # после рестарта таймер простоя продолжается, а не начинается заново
            limit = await self.idle_limit()
            if limit:
                self._last = time.monotonic() - max(0, limit - max(5, float(idle_left)))
        self._watch = asyncio.create_task(self._watchdog())
        if self.tunnel_wanted():                       # сайт мог встать на соседний порт — туннель ведём туда же
            try:
                self.request_tunnel(True)
            except RuntimeError:
                pass
        limit = await self.idle_limit()
        self.core.tlog(f"Сайт поднят: {self.url} ("
                       + (f"выключится через {limit // 60} мин без активности" if limit else "автовыключение отключено") + ")")
        return self.url

    @staticmethod
    def _sentinel_port():
        if os.environ.get('FRX_KEEPER') != '1':
            return None
        try:
            d = json.loads(SENTINEL_FILE.read_text(encoding='utf-8'))
            if time.time() - float(d.get('ts', 0)) < 6 and d.get('port'):
                return int(d['port'])
        except (OSError, ValueError, TypeError):
            pass
        return None

    def start_wake_watch(self):
        """следим за файлом-сигналом сторожа keeper'а; заход на localhost сразу поднимает сайт."""
        if getattr(self, '_wake_task', None) is not None and not self._wake_task.done():
            return
        WAKE_FILE.unlink(missing_ok=True)

        async def loop():
            while True:
                await asyncio.sleep(0.5)
                try:
                    if WAKE_FILE.exists():
                        WAKE_FILE.unlink(missing_ok=True)
                        if not self.running:
                            self.core.tlog('Кто-то зашёл на адрес сайта — поднимаю сайт')
                            await self.start()
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.core.tlog(f'Не удалось поднять сайт по заходу: {e}')
                    await asyncio.sleep(3)
        self._wake_task = asyncio.ensure_future(loop())

    def stop_wake_watch(self):
        t = getattr(self, '_wake_task', None)
        if t is not None:
            t.cancel()

    async def resume_or_autostart(self):
        """Вызывается из on_start модуля Web: вернуть сайт после рестарта или запустить его по конфигу `autostart`."""
        state = None
        try:
            state = json.loads(STATE_FILE.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        STATE_FILE.unlink(missing_ok=True)
        if self.running:
            return
        if isinstance(state, dict) and time.time() - float(state.get('saved', 0)) < RESUME_MAX_AGE and state.get('port'):
            try:
                left = state.get('idle_left')
                if left is not None:
                    left = max(0.0, float(left) - (time.time() - float(state['saved'])))
                await self.start(port=int(state['port']), takeover=True, idle_left=left)
                return
            except Exception as e:
                self.core.tlog(f"Не удалось вернуть сайт после рестарта: {e}")
        if await self._cfg('autostart', False):
            try:
                await self.start()
            except Exception as e:
                self.core.tlog(f"Автозапуск сайта не удался: {e}")

    async def suspend(self):
        """
        юзербот перезагружается (.restart/.update). Сайт НЕ выключаем: помечаем «спит» (API отвечает
        503 sleeping, страница остаётся открытой), записываем состояние для keeper'а — он удержит порт заглушкой, пока
        новый процесс не поднимется, а тот вернёт сайт (resume_or_autostart). Сам слушатель закроет stop() в самом конце.
        """
        if not self.running:
            return False
        self._sleeping = True
        limit = await self.idle_limit()
        left = None if limit is None else max(0.0, limit - (time.monotonic() - self._last))
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            STATE_FILE.write_text(json.dumps({'port': self._port, 'saved': time.time(), 'idle_left': left, 'limit': limit,
                                              'version': self.core.get_version()}), encoding='utf-8')
        except OSError:
            return False
        return True

    async def stop(self, reason: str = ''):
        if not self.running:
            return
        runner, self._runner, self._site = self._runner, None, None
        watch, self._watch = self._watch, None
        if watch is not None and watch is not asyncio.current_task():
            watch.cancel()
        if self._login is not None:
            try:
                await self._login.cancel()
            except Exception:
                pass
            self._login = None
        for module_name in list(self._emu):
            await self.drop_emulation(module_name)
        if self._sandbox is not None:
            self._sandbox.hub.shutdown_all()
        try:
            await runner.cleanup()
        except Exception:
            pass
        self.core.tlog(f"Сайт выключен{': ' + reason if reason else ''}")

    async def _watchdog(self):
        while True:
            await asyncio.sleep(5)
            if self._sleeping:
                continue
            limit = await self.idle_limit()
            if limit and time.monotonic() - self._last > limit:
                await self.stop(f'нет активности {limit // 60} мин')
                return

    # ------------------------------------------------------------------ #
    # Маршруты и защита
    # ------------------------------------------------------------------ #

    def _add_routes(self, app):
        r = app.router
        r.add_get('/', self.h_index)
        r.add_get('/static/{name}', self.h_static)
        r.add_get('/api/auth/state', self.api(self.a_auth_state))
        for name in ('request', 'verify', 'telegram', 'logout', 'ticket'):
            r.add_post(f'/api/auth/{name}', self.api(getattr(self, f'a_auth_{name}')))
        r.add_get('/api/tunnel/where', self.api(self.a_tunnel_where))
        r.add_post('/api/tunnel/handoff', self.api(self.a_tunnel_handoff))
        r.add_get('/api/state', self.api(self.a_state))
        r.add_get('/api/nav', self.api(self.a_nav))
        r.add_get('/api/changelog', self.api(self.a_changelog))
        r.add_post('/api/ping', self.api(self.a_ping))
        r.add_post('/api/shutdown', self.api(self.a_shutdown))
        r.add_get('/api/configs', self.api(self.a_configs))
        r.add_post('/api/configs/set', self.api(self.a_config_set))
        r.add_get('/api/modules', self.api(self.a_modules))
        r.add_get('/api/modules/source', self.api(self.a_module_source))
        for name in ('check', 'preview', 'install', 'save', 'unload', 'reload', 'restore', 'delete_unloaded'):
            r.add_post(f'/api/modules/{name}', self.api(getattr(self, f'a_module_{name}')))
        r.add_get('/api/accounts', self.api(self.a_accounts))
        for name in ('switch', 'delete', 'refresh', 'toggle_numbers'):
            r.add_post(f'/api/accounts/{name}', self.api(getattr(self, f'a_account_{name}')))
        for name in ('phone', 'code', '2fa', 'cancel'):
            r.add_post(f'/api/accounts/login/{name}', self.api(getattr(self, f'a_login_{name.replace("2fa", "twofa")}')))
        r.add_post('/api/page/render', self.api(self.a_page_render))
        r.add_post('/api/modapp/call', self.api(self.a_modapp_call))           # @web.app — вызовы из скрипта страницы
        r.add_post('/api/modapp/upload', self.api(self.a_modapp_upload))
        r.add_get('/modapp/{module}/{index}/{name:.+}', self.h_modapp_file)
        r.add_get('/api/emulate/module', self.api(self.a_emu_module))
        r.add_get('/api/emulate/poll', self.api(self.a_emu_poll))
        for name in ('run', 'click', 'input', 'say', 'stop', 'clear', 'upload', 'unupload'):
            r.add_post(f'/api/emulate/{name}', self.api(getattr(self, f'a_emu_{name}')))
        r.add_get('/api/emulate/file', self.h_emu_file)
        from .panel_api import register as register_panel_api            # README, логи, файлы
        self._panel_api = register_panel_api(self, r)
        from .botchat_api import register as register_botchat          # вкладка BotChat
        register_botchat(self, r)
        from .webide_api import register as register_webide            # вкладка FRX WebIDE
        self._webide = register_webide(self, r)
        from .neuro_api import register as register_neuro              # нейросети — вкладка neuro и помощник WebIDE
        self._neuro = register_neuro(self, r)
        from .sandbox_api import register as register_sandbox          # FRX WebIDE Sandbox
        self._sandbox = register_sandbox(self, r)
        from .access_api import register as register_access            # доступ гостей, классы, алиасы
        self._access = register_access(self, r)

    @staticmethod
    def is_remote(request) -> bool:
        """Запрос пришёл через туннель / HTTPS-прокси (а не прямо на 127.0.0.1)."""
        h = request.headers
        if h.get('Cf-Ray') or h.get('X-Forwarded-Proto', '').lower() == 'https' or request.secure:
            return True
        origin = h.get('Origin', '')
        return origin.startswith('https://')

    def authed(self, request) -> bool:
        return self.auth.check(request.cookies.get(COOKIE))

    @web.middleware
    async def _guard(self, request, handler):
        host = request.headers.get('Host', '')
        allowed = self.allowed_hosts()
        if _hostname(host).lower() not in allowed:
            return web.Response(status=403, text='forbidden host')
        path = request.path
        if path.startswith('/api/'):
            if request.headers.get('X-FRX') != '1':
                return web.json_response({'error': 'forbidden'}, status=403)
            origin = request.headers.get('Origin')
            if origin and (urlparse(origin).hostname or '').lower() not in allowed:
                return web.json_response({'error': 'forbidden origin'}, status=403)
        if path != '/' and not path.startswith(('/static/', '/dl/')) and path not in PUBLIC_PATHS and not self.authed(request):   # /dl/ — пропуск в самой ссылке
            return web.json_response({'error': 'нужно войти', 'auth': True}, status=401)
        if path.startswith('/api/'):
            if self._sleeping:                           # юзербот перезагружается
                return web.json_response({'sleeping': True, 'version': self.core.get_version(),
                                          'error': 'юзербот перезапускается'}, status=503)
        if request.path not in PASSIVE_PATHS:
            self.touch()
        response = await handler(request)
        response.headers.setdefault('Cache-Control', 'no-store')
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        remote = self.is_remote(request)
        if not remote:
            response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
            f"font-src 'self'; frame-ancestors {TELEGRAM_FRAMES if remote else chr(39) + 'none' + chr(39)}; "
            "base-uri 'none'; form-action 'none'"
        )
        return response

    def api(self, func):
        async def wrapper(request):
            try:
                data = await func(request)
                if isinstance(data, web.StreamResponse):
                    return data
                return web.json_response(data if data is not None else {'ok': True})
            except ApiError as e:
                return web.json_response({'error': e.message}, status=e.status)
            except Exception as e:                         # неожиданная ошибка — в терминал и в UI
                self.core.tlog(f"[web] {request.path}: {type(e).__name__}: {e}")
                return web.json_response({'error': f"{type(e).__name__}: {e}"}, status=500)
        return wrapper

    @staticmethod
    async def _json(request) -> dict:
        try:
            data = await request.json()
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------------ #
    # Статика
    # ------------------------------------------------------------------ #

    async def h_index(self, request):
        page = 'index.html' if self.authed(request) else 'login.html'
        html = (STATIC_DIR / page).read_text(encoding='utf-8')
        return web.Response(text=html, content_type='text/html', headers={'Cache-Control': 'no-cache'})

    # ------------------------------------------------------------------ #
    # Вход (core/web/auth.py)
    # ------------------------------------------------------------------ #

    def _where(self, request) -> str:
        h = request.headers
        ip = h.get('Cf-Connecting-Ip') or h.get('X-Forwarded-For', '').split(',')[0].strip() or request.remote or '?'
        host = _hostname(h.get('Host', ''))
        kind = 'через публичный адрес' if self.is_remote(request) else 'по адресу'
        agent = h.get('User-Agent', '')
        dev = next((n for k, n in (('Android', 'Android'), ('iPhone', 'iPhone'), ('iPad', 'iPad'), ('Windows', 'Windows'),
                                   ('Mac OS', 'macOS'), ('Linux', 'Linux'), ('Telegram', 'Telegram')) if k in agent), 'браузер')
        return f"{kind} {host}, IP {ip}, {dev}"

    async def _set_session(self, request, via) -> web.Response:
        token = await self.auth.new_session(via, request.headers.get('User-Agent', ''))
        days = await self.auth.session_days()
        resp = web.json_response({'ok': True})
        remote = self.is_remote(request)
        resp.set_cookie(COOKIE, token, max_age=days * 86400, path='/', httponly=True,
                        secure=remote, samesite='None' if remote else 'Lax')
        self.core.tlog(f"[web] вход на сайт ({via}): {self._where(request)}")
        return resp

    async def a_auth_state(self, request):
        return {'authed': self.authed(request), 'open': self.auth.is_open(), 'left': self.auth.window_left(),
                'bot': getattr(self.core, '_aiogram_bot', None) is not None}

    async def a_auth_request(self, request):
        try:
            sent = await self.auth.resend(self._where(request))
        except AuthError as e:
            raise ApiError(e.message, e.status)
        return {'ok': True, 'sent_to': sent}

    async def a_auth_verify(self, request):
        body = await self._json(request)
        try:
            await self.auth.verify_code(str(body.get('code') or ''))
        except AuthError as e:
            raise ApiError(e.message, e.status)
        return await self._set_session(request, 'код из Telegram')

    async def a_auth_telegram(self, request):
        body = await self._json(request)
        try:
            self.auth.verify_init_data(str(body.get('init_data') or ''))
        except AuthError as e:
            raise ApiError(e.message, e.status)
        return await self._set_session(request, 'Telegram Mini App')

    async def a_auth_ticket(self, request):
        body = await self._json(request)
        ticket = str(body.get('ticket') or '')
        via = self.auth.use_ticket(ticket)
        if not via and not self.is_remote(request):            # кнопка «Сайт FRX» в приложении для Android
            via = self.auth.use_app_ticket(ticket)
        if not via:
            raise ApiError('Ссылка для входа уже использована или устарела — войдите по коду (.web в Telegram)', 403)
        return await self._set_session(request, via)

    def login_link(self, url=None, ttl=None):
        """Ссылка «войти в один клик» на публичный адрес (одноразовая, см. WebAuth.issue_ticket) или None."""
        url = url or self.public_url()
        if not url:
            return None
        return url.rstrip('/') + '/#frx_t=' + self.auth.issue_ticket(ttl)

    async def a_tunnel_where(self, request):
        st = self.tunnel_state()
        return {'url': self.public_url(), 'status': st.get('status') or 'off', 'mode': st.get('mode') or 'auto'}

    async def a_tunnel_handoff(self, request):
        """Открытая страница увидела новый публичный адрес — даём ей одноразовый билет, чтобы переехать без входа."""
        from .auth import HANDOFF_TTL
        url = self.public_url()
        if not url:
            raise ApiError('Публичного адреса сейчас нет', 409)
        return {'url': url.rstrip('/'), 'ticket': self.auth.issue_ticket(HANDOFF_TTL, 'переезд на новый адрес')}

    async def a_auth_logout(self, request):
        self.auth.drop(request.cookies.get(COOKIE))
        resp = web.json_response({'ok': True})
        resp.del_cookie(COOKIE, path='/')
        return resp

    async def h_static(self, request):
        name = request.match_info['name']
        if name not in STATIC_FILES:
            raise web.HTTPNotFound()
        ctype = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        # шрифт кэшируется, остальное — нет: иначе после обновления браузер (или кэш туннеля Cloudflare) отдаёт старые скрипты
        headers = {'Cache-Control': 'max-age=3600'} if name in ('font.woff2', 'logo.svg', 'icon.png') else {'Cache-Control': 'no-cache'}
        return web.Response(body=(STATIC_DIR / name).read_bytes(), content_type=ctype, headers=headers)

    # ------------------------------------------------------------------ #
    # Общее
    # ------------------------------------------------------------------ #

    def _pages(self):
        result = []
        for module_name, info in self.core.modules.items():
            for idx, entry in enumerate(info.web_pages):
                result.append({'module': module_name, 'index': idx, 'kind': entry.get('kind', 'page'),
                               'title': entry['title'], 'icon': entry.get('icon')})
        return result

    async def a_state(self, request):
        core = self.core
        current = None
        try:
            me = await core.client.get_me()
            current = {'first_name': me.first_name or '', 'id': me.id,
                       'username': me.username, 'phone': me.phone}
        except Exception:
            pass
        cfg_modules = 0
        for module_name in core.modules:
            if await core.db.get(core._config_key(module_name), {}):
                cfg_modules += 1
        bot_username = None
        if core._aiogram_bot is not None:
            bot_username = core._bot_username
            if not bot_username:
                try:
                    bot_username = (await asyncio.wait_for(core._aiogram_bot.get_me(), 5)).username
                    core._bot_username = bot_username
                except Exception:
                    bot_username = None
        return {
            'bot_username': bot_username,
            'version': core.get_version(), 'prefix': await core.get_prefix(),
            'counts': {'configs': cfg_modules, 'modules': len(core.modules),
                       'accounts': len(core.accounts.account_list())},
            'account': current, 'pages': self._pages(),
            'idle_timeout': await self.idle_limit(), 'bot': core._aiogram_bot is not None,
            'commands': sum(len({id(f) for f in i.commands.values()}) for i in core.modules.values()),
            'description': 'Fractal Runtime Extension — модульный Telegram-юзербот с локальным сайтом управления',
        }

    async def a_nav(self, request):
        """Лёгкие данные для верхнего меню: без запросов к Telegram и БД."""
        return {'version': self.core.get_version(), 'pages': self._pages(), 'botchat': self.core._aiogram_bot is not None,
                'bot_username': getattr(self.core, '_bot_username', None)}

    async def a_changelog(self, request):
        """Записи CHANGELOG.md для главной: [{version, date, items: [str]}], новые сверху."""
        try:
            text = CHANGELOG_PATH.read_text(encoding='utf-8')
        except OSError:
            return {'entries': []}
        entries, cur = [], None
        for line in text.splitlines():
            m = re.match(r'^##\s+(\S+)\s*(?:[—-]+\s*(.*))?$', line)
            if m:
                cur = {'version': m.group(1), 'date': (m.group(2) or '').strip(), 'items': []}
                entries.append(cur)
            elif cur is not None:
                if re.match(r'^-\s+', line):
                    cur['items'].append(re.sub(r'^-\s+', '', line))
                elif line.startswith('  ') and cur['items'] and line.strip():
                    cur['items'][-1] += ' ' + line.strip()
        for e in entries:
            e['items'] = [re.sub(r'[*`]', '', it).strip() for it in e['items']][:30]
        return {'entries': entries[:20]}

    async def a_ping(self, request):
        return {'ok': True}

    async def a_shutdown(self, request):
        asyncio.get_running_loop().call_later(0.4, lambda: asyncio.ensure_future(self.stop('по запросу')))
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # Конфиги
    # ------------------------------------------------------------------ #

    async def a_configs(self, request):
        core = self.core
        modules = []
        for module_name, info in core.modules.items():
            cfg = await core.db.get(core._config_key(module_name), {})
            if not cfg:
                continue
            items = []
            for key, e in cfg.items():
                items.append({
                    'key': key, 'type': e.get('type', 'str'), 'value': e.get('value'),
                    'default': e.get('default'), 'description': e.get('description', ''),
                    'options': e.get('options'), 'valid': e.get('valid'),
                })
            modules.append({'module': module_name, 'name': info.name, 'builtin': info.is_builtin, 'items': items})
        modules.sort(key=lambda m: (m['builtin'], m['name'].lower()))
        return {'modules': modules}

    async def a_config_set(self, request):
        core = self.core
        b = await self._json(request)
        module, key, op = b.get('module'), b.get('key'), b.get('op', 'edit')
        if module not in core.modules:
            raise ApiError('Модуль не найден', 404)
        cfg = await core.db.get(core._config_key(module), {})
        entry = cfg.get(key)
        if entry is None:
            raise ApiError('Конфиг не найден', 404)
        value, typ = b.get('value'), entry.get('type', 'str')
        if op == 'edit':
            try:
                if typ == 'int':
                    value = int(str(value).strip())
                elif typ == 'float':
                    value = float(str(value).strip().replace(',', '.'))
                elif typ == 'bool':
                    value = value if isinstance(value, bool) else str(value).strip().lower() in ('1', 'true', 'да', 'yes', 'on')
                elif typ in ('str', 'link'):
                    value = '' if value is None else str(value)
            except ValueError:
                raise ApiError('Ожидалось целое число' if typ == 'int' else 'Ожидалось число')
        try:
            new_value = await core.edit_config(key, op, value, module_name=module)
        except (ValueError, KeyError) as e:
            raise ApiError(str(e.args[0]) if e.args else str(e))
        return {'value': new_value}

    # ------------------------------------------------------------------ #
    # Модули
    # ------------------------------------------------------------------ #

    async def a_modules(self, request):
        core = self.core
        loaded = []
        hidden_emu = set(await self._emu_cfg('hide_modules', [WEB_MODULE]) or [])
        for module_name, info in core.modules.items():
            d = core.get_module_info(module_name)
            other = {k: len(d[k]) for k in ('watchers', 'telegram_events', 'callbacks', 'on_start',
                                            'on_load', 'on_error', 'cycles', 'bot_commands', 'bot_watchers')}
            loaded.append({
                'module': module_name, 'name': info.name, 'version': info.version, 'author': info.author,
                'tg_url': info.tg_url, 'git_url': info.git_url,
                'description': info.description, 'builtin': info.is_builtin, 'commands': d['commands'],
                'bot_commands': d['bot_commands'], 'cycles': d['cycles'], 'other': other,
                'pages': len(info.web_pages), 'emulate': bool(info.commands) and module_name not in hidden_emu,
            })
        loaded.sort(key=lambda m: (m['builtin'], m['name'].lower()))
        unloaded = [
            {'file': e['file'], 'module': e['module_name'], 'name': e['name'], 'version': e['version'],
             'author': e['author'], 'description': e['description'], 'broken': e['broken'],
             'loaded': e['loaded'], 'commands': e['commands']}
            for e in core.list_unloaded_modules()
        ]
        return {'loaded': loaded, 'unloaded': unloaded, 'prefix': await core.get_prefix()}

    async def a_module_source(self, request):
        module = request.query.get('module', '')
        info = self.core.modules.get(module)
        if info is None:
            raise ApiError('Модуль не найден', 404)
        try:
            source = Path(info.path).read_text(encoding='utf-8')
        except OSError:
            source = info.source
        return {'module': module, 'source': source, 'editable': not info.is_builtin, 'path': info.path}

    async def a_module_check(self, request):
        b = await self._json(request)
        return analyze_source(str(b.get('source', '')))

    def _validated_meta(self, source: str) -> dict:
        a = analyze_source(source)
        if not a['ok']:
            err = a['error']
            raise ApiError(f"Строка {err['line']}: {err['msg']}")
        return a['meta']

    async def a_module_preview(self, request):
        b = await self._json(request)
        meta = self._validated_meta(str(b.get('source', '')))
        existing = self.core.modules.get(meta['module_name'])
        return {
            'meta': meta,
            'existing_version': existing.version if existing else None,
            'existing_builtin': bool(existing and existing.is_builtin),
            'same_version': bool(existing and existing.version == str(meta['version'])),
        }

    def _purge_partial(self, module_name: str):
        """Если загрузка упала на полпути — убираем следы (движок чистит только загруженные модули)."""
        core = self.core
        if module_name in core.modules:
            return
        core.command_map = {a: v for a, v in core.command_map.items() if v[0] != module_name}
        core.bot_command_map = {a: v for a, v in core.bot_command_map.items() if v[0] != module_name}
        core.bot_inline_function_map = {a: v for a, v in core.bot_inline_function_map.items() if v[0] != module_name}
        for attr in ('watchers', 'telegram_events', 'callbacks', 'on_error_handlers', 'on_account_switch_handlers',
                     'bot_telegram_events', 'bot_on_error_handlers'):
            setattr(core, attr, [e for e in getattr(core, attr) if e[0] != module_name])
        for attr in ('bot_watchers_bot', 'bot_watchers_userbot'):
            setattr(core, attr, [e for e in getattr(core, attr) if e[0] != module_name])

    async def a_module_install(self, request):
        core = self.core
        b = await self._json(request)
        source = str(b.get('source', ''))
        meta = self._validated_meta(source)
        name = meta['module_name']
        existing = core.modules.get(name)
        if existing and existing.is_builtin:
            raise ApiError('Нельзя перезаписать встроенный модуль')
        if existing and existing.version == str(meta['version']):
            raise ApiError(f"Модуль «{name}» версии {existing.version} уже загружен. "
                           f"Измените VERSION (это будет обновление) или отредактируйте его в редакторе")
        path = MODULES_DIR / f"{name}.py"
        before = path.read_bytes() if path.exists() else None
        try:
            info = await core._load_from_source(source, target='loaded')
        except Exception as e:
            self._purge_partial(name)
            if before is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(before)
            raise ApiError(f"{type(e).__name__}: {e}")
        return {'module': info.module_name, 'name': info.name, 'version': info.version,
                'updated': existing is not None}

    async def _swap(self, module: str, source: str):
        """Перезагрузка модуля с новым исходником; при ошибке возвращаем прежнюю версию."""
        core = self.core
        info = core.modules[module]
        old_source, path, target = info.source, info.path, info.kind
        await core.unload_module(module, archive=False)
        try:
            return await core._load_from_source(source, target=target, source_path=path)
        except Exception as e:
            self._purge_partial(module)
            restored = True
            try:
                await core._load_from_source(old_source, target=target, source_path=path)
            except Exception:
                restored = False
            note = 'прежняя версия возвращена' if restored else 'ВНИМАНИЕ: прежнюю версию вернуть не удалось'
            raise ApiError(f"{type(e).__name__}: {e} — {note}")

    async def a_module_save(self, request):
        """Сохранение модуля — через FRX WebIDE (защита NAME/MODULE_NAME/AUTHOR, клеймо, история)."""
        return await self._webide.save(request)

    async def a_module_reload(self, request):
        b = await self._json(request)
        module = b.get('module')
        info = self.core.modules.get(module)
        if info is None:
            raise ApiError('Модуль не найден', 404)
        try:
            source = Path(info.path).read_text(encoding='utf-8')
        except OSError:
            source = info.source
        await self._swap(module, source)
        return {'ok': True}

    async def a_module_unload(self, request):
        b = await self._json(request)
        module = b.get('module')
        info = self.core.modules.get(module)
        if info is None:
            raise ApiError('Модуль не найден', 404)
        if info.is_builtin:
            raise ApiError('Нельзя выгрузить системный модуль')
        await self.core.unload_module(module)
        return {'ok': True}

    async def a_module_restore(self, request):
        b = await self._json(request)
        try:
            info = await self.core.restore_module(str(b.get('file', '')))
        except (KeyError, ValueError, FileExistsError) as e:
            raise ApiError(str(e.args[0]) if e.args else str(e))
        return {'module': info.module_name}

    async def a_module_delete_unloaded(self, request):
        b = await self._json(request)
        try:
            await self.core.delete_unloaded_module(str(b.get('file', '')))
        except KeyError as e:
            raise ApiError(str(e.args[0]) if e.args else str(e))
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # Аккаунты (аналог modules/builtin/AccountManager.py)
    # ------------------------------------------------------------------ #

    async def _acc_cfg(self, key, default):
        try:
            return await self.core.get_config(key, module_name='account_manager')
        except Exception:
            return default

    async def a_accounts(self, request):
        core, mgr = self.core, self.core.accounts
        for number in list(mgr.account_list()):
            if not mgr.account_info(number)['info'].get('id'):
                try:
                    await mgr.refresh_account_info(number)
                except Exception:
                    pass
        current = None
        active_phone = mgr.active or mgr.data.get('active')
        try:
            me = await core.client.get_me()
            current = {'first_name': me.first_name or '', 'id': me.id, 'username': me.username}
            if me.phone:
                active_phone = '+' + str(me.phone).lstrip('+')
        except Exception:
            pass
        show = bool(await self._acc_cfg('show_phone_number', False))
        prefix = int(await self._acc_cfg('phone_prefix_show', 3) or 0)
        items = []
        for number in mgr.account_list():
            info = mgr.account_info(number)['info']
            shown = number if show else number[:prefix + 1] + '*' * len(number[prefix + 1:])
            items.append({
                'phone': number, 'shown': shown, 'id': info.get('id'),
                'first_name': info.get('first_name') or number, 'username': info.get('username'),
                'active': number == active_phone,
            })
        return {'accounts': items, 'show_numbers': show, 'current': current}

    async def _require_number(self, request):
        b = await self._json(request)
        number = str(b.get('phone', ''))
        if number not in self.core.accounts.account_list():
            raise ApiError('Аккаунт не найден', 404)
        return number

    async def _is_active(self, number) -> bool:
        try:
            me = await self.core.client.get_me()
            if me.phone:
                return number == '+' + str(me.phone).lstrip('+')
        except Exception:
            pass
        return number == (self.core.accounts.active or self.core.accounts.data.get('active'))

    async def a_account_switch(self, request):
        core = self.core
        number = await self._require_number(request)
        if await self._is_active(number):
            raise ApiError('Юзербот уже на этом аккаунте')
        await core.set_account(number)
        try:
            await core.bot_send_to_owner("Юзербот был успешно перенесен на этот аккаунт.")
        except Exception:
            pass                                    # уведомление — best-effort
        return {'ok': True}

    async def a_account_delete(self, request):
        number = await self._require_number(request)
        if await self._is_active(number):
            raise ApiError('Нельзя удалить аккаунт, на котором сейчас работает FRX')
        await self.core.accounts.delete_account(number)
        return {'ok': True}

    async def a_account_refresh(self, request):
        await self.core.accounts.refresh_account_info()
        return {'ok': True}

    async def a_account_toggle_numbers(self, request):
        show = bool(await self._acc_cfg('show_phone_number', False))
        try:
            await self.core.edit_config('show_phone_number', 'edit', not show, module_name='account_manager')
        except Exception as e:
            raise ApiError(f"Не удалось изменить настройку: {e}")
        return {'show_numbers': not show}

    # --- добавление аккаунта: телефон -> код -> (2FA) ---

    async def _drop_login(self):
        if self._login is not None:
            try:
                await self._login.cancel()
            except Exception:
                pass
            self._login = None

    async def a_login_phone(self, request):
        b = await self._json(request)
        number = re.sub(r'[\s()\-]', '', str(b.get('phone', '')))
        if not re.fullmatch(r'\+?\d{7,15}', number):
            raise ApiError('Это не похоже на номер телефона. Формат: +79991234567')
        if not number.startswith('+'):
            number = '+' + number
        if number in self.core.accounts.account_list():
            raise ApiError(f'FRX уже авторизован на аккаунте {number}')
        await self._drop_login()
        login = self.core.accounts.new_login()
        try:
            await login.enter_phone(number)
        except PhoneNumberInvalidError:
            await login.cancel()
            raise ApiError('Номер некорректен')
        except PhoneNumberBannedError:
            await login.cancel()
            raise ApiError('Номер заблокирован в Telegram')
        except (PhoneNumberFloodError, FloodWaitError) as e:
            await login.cancel()
            raise ApiError(f'Telegram временно ограничил запросы для этого номера: {e}')
        except Exception as e:
            await login.cancel()
            raise ApiError(f'Не удалось отправить код: {e}')
        self._login = login
        return {'step': 'code', 'phone': number}

    async def a_login_code(self, request):
        b = await self._json(request)
        login = self._login
        if login is None:
            raise ApiError('Вход не начат — введите номер')
        try:
            result = await login.enter_code(str(b.get('code', '')).strip())
        except PhoneCodeInvalidError:
            raise ApiError('Неверный код — попробуйте ещё раз')
        except PhoneCodeExpiredError:
            await self._drop_login()
            raise ApiError('Код истёк. Вход отменён — начните заново')
        except Exception as e:
            await self._drop_login()
            raise ApiError(f'Ошибка входа: {e}. Вход отменён')
        if result.get('status') == 'password_needed':
            return {'step': '2fa'}
        return await self._login_done(login)

    async def a_login_twofa(self, request):
        b = await self._json(request)
        login = self._login
        if login is None:
            raise ApiError('Вход не начат — введите номер')
        try:
            await login.enter_2FA(str(b.get('password', '')))
        except PasswordHashInvalidError:
            raise ApiError('Неверный пароль — попробуйте ещё раз')
        except Exception as e:
            await self._drop_login()
            raise ApiError(f'Ошибка входа: {e}. Вход отменён')
        return await self._login_done(login)

    async def _login_done(self, login):
        number = login.phone
        self._login = None
        try:
            await self.core.accounts.refresh_account_info(number)
        except Exception:
            pass
        return {'step': 'done', 'phone': number}

    async def a_login_cancel(self, request):
        await self._drop_login()
        return {'ok': True}

    # ------------------------------------------------------------------ #
    # Страницы модулей (import web)
    # ------------------------------------------------------------------ #

    async def a_page_render(self, request):
        b = await self._json(request)
        module, index = b.get('module'), b.get('index')
        info = self.core.modules.get(module)
        if info is None or not isinstance(index, int) or not (0 <= index < len(info.web_pages)):
            raise ApiError('Страница не найдена', 404)
        entry = info.web_pages[index]
        if entry.get('kind') == 'app':                 # @web.app — своя разметка; скрипт и стили — /modapp/…
            import hashlib
            ver = hashlib.md5((entry.get('js', '') + entry.get('css', '')).encode()).hexdigest()[:10]
            return {'title': entry['title'], 'app': {'html': entry.get('html', ''), 'v': ver, 'width': entry.get('width', 'normal')}}
        state = self._page_states.setdefault((module, index), {})
        token = current_module.set(module)
        try:
            tree, toasts = await ui_mod.render(entry['func'], state, b.get('values') or {}, b.get('action'))
        except Exception as e:
            self.core.tlog(f"[web] страница «{entry['title']}» ({module}): {type(e).__name__}: {e}")
            tree = [{'t': 'title', 'text': 'Ошибка страницы'},
                    {'t': 'code', 'text': ''.join(traceback.format_exception_only(type(e), e)).strip()}]
            toasts = []
        finally:
            current_module.reset(token)
        return {'title': entry['title'], 'tree': tree, 'toasts': toasts}

    # ------------------------------------------------------------------ #
    # @web.app — своя страница модуля (HTML/CSS/JS + обработчик на Python)
    # ------------------------------------------------------------------ #

    def _modapp_entry(self, module, index):
        info = self.core.modules.get(module)
        try:
            index = int(index)
        except (TypeError, ValueError):
            index = -1
        if info is None or not (0 <= index < len(info.web_pages)) or info.web_pages[index].get('kind') != 'app':
            raise ApiError('Страница не найдена', 404)
        return info.web_pages[index]

    async def _modapp_run(self, module, entry, req):
        token = current_module.set(module)
        try:
            result = entry['func'](req)
            if asyncio.iscoroutine(result):
                result = await result
            return result
        except ApiError:
            raise
        except Exception as e:
            self.core.tlog(f"[web] {module}: {req.action}: {type(e).__name__}: {e}")
            raise ApiError(f"{type(e).__name__}: {e}", 500)
        finally:
            current_module.reset(token)

    async def a_modapp_call(self, request):
        b = await self._json(request)
        module = str(b.get('module', ''))
        entry = self._modapp_entry(module, b.get('index'))
        result = await self._modapp_run(module, entry, ui_mod.AppRequest(b.get('action'), b.get('data')))
        return result if isinstance(result, dict) else {'result': result}

    async def a_modapp_upload(self, request):
        """Файл со страницы @web.app: сырое тело, ?module=&index=&action=&name=&data=(json)."""
        import json as _json
        import tempfile
        q = request.query
        module = q.get('module', '')
        entry = self._modapp_entry(module, q.get('index'))
        name = os.path.basename(q.get('name', 'file'))[:120] or 'file'
        try:
            data = _json.loads(q.get('data') or '{}')
        except ValueError:
            data = {}
        fd, path = tempfile.mkstemp(prefix='frx_up_', suffix='_' + name)
        try:
            with os.fdopen(fd, 'wb') as f:
                async for chunk in request.content.iter_chunked(256 * 1024):
                    f.write(chunk)
            result = await self._modapp_run(module, entry, ui_mod.AppRequest(q.get('action', 'upload'), data, path, name))
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        return result if isinstance(result, dict) else {'result': result}

    async def h_modapp_file(self, request):
        module, index, name = request.match_info['module'], request.match_info['index'], request.match_info['name']
        try:
            entry = self._modapp_entry(module, index)
        except ApiError:
            return web.Response(status=404, text='not found')
        if name in ('app.js', 'app.css'):
            body = entry.get('js' if name == 'app.js' else 'css', '')
            ctype = 'application/javascript' if name == 'app.js' else 'text/css'
            return web.Response(text=body, content_type=ctype, headers={'Cache-Control': 'no-cache'})
        if name.startswith('res/'):
            try:
                result = await self._modapp_run(module, entry, ui_mod.AppRequest('res', {'name': name[4:]}, query=request.query))
            except ApiError as e:
                return web.Response(status=e.status, text=e.message)
            if isinstance(result, tuple) and len(result) == 2:
                body, ctype = result
                return web.Response(body=body, content_type=ctype or 'application/octet-stream',
                                    headers={'Cache-Control': 'max-age=3600'})
            if isinstance(result, web.StreamResponse):
                return result
            return web.Response(status=404, text='not found')
        return web.Response(status=404, text='not found')

    # ------------------------------------------------------------------ #
    # Emulate: команды, формы и диалоги любого модуля на сайте
    # ------------------------------------------------------------------ #

    async def _emu_cfg(self, key, default):
        return await self._cfg(key, default)       # настройки Emulate живут в конфигах модуля Web

    async def drop_emulation(self, module_name: str):
        """Закрывает сессию Emulate модуля (выгрузка модуля, остановка сайта)."""
        session = self._emu.pop(module_name, None)
        if session is not None:
            try:
                await session.shutdown()
            except Exception:
                pass

    async def _emu_session(self, module_name) -> EmuSession:
        core = self.core
        if module_name not in core.modules:
            raise ApiError('Модуль не найден', 404)
        hidden = await self._emu_cfg('hide_modules', [WEB_MODULE])
        if module_name in (hidden or []):
            raise ApiError('Модуль скрыт в настройках Emulate (hide_modules)', 403)
        session = self._emu.get(module_name)
        if session is None:
            limit = int(await self._emu_cfg('history_limit', 200) or 200)
            session = self._emu[module_name] = EmuSession(core, module_name, max_entries=max(20, limit))
        return session

    async def a_emu_module(self, request):
        module = request.query.get('module', '')
        session = await self._emu_session(module)
        info = self.core.modules[module]
        prefix = await self.core.get_prefix()
        return {
            'module': module, 'name': info.name, 'version': info.version, 'description': info.description,
            'builtin': info.is_builtin, 'prefix': prefix,
            'commands': describe_commands(self.core, module, prefix),
            'native': [{'title': p['title'], 'index': i} for i, p in enumerate(info.web_pages)],
            'session': session.snapshot(0),
        }

    async def a_emu_poll(self, request):
        session = await self._emu_session(request.query.get('module', ''))
        try:
            since = int(request.query.get('since', '0'))
        except ValueError:
            since = 0
        if session.running or session.waiters:
            self.touch()                                # идёт работа — сайт не выключаем
        return session.snapshot(since)

    async def a_emu_run(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        command = str(b.get('command', '')).strip()
        args = str(b.get('args', ''))
        if len(args) > 20000:
            raise ApiError('Слишком длинные аргументы')
        reply = b.get('reply')
        if reply is not None and not isinstance(reply, dict):
            raise ApiError('Некорректный reply')
        if isinstance(reply, dict):
            reply = {'text': str(reply.get('text') or '')[:20000], 'from': 'me' if reply.get('from') == 'me' else 'other',
                     'name': str(reply.get('name') or '')[:40], 'file': str(reply['file']) if reply.get('file') else None}
        attach = str(b['attach']) if b.get('attach') else None
        try:
            await session.run_command(command, args, reply=reply, attach=attach)
        except KeyError as e:
            raise ApiError(str(e.args[0]) if e.args else str(e), 404)
        except ValueError as e:
            raise ApiError(str(e))
        return {'ok': True}

    async def a_emu_upload(self, request):
        """Файл для Emulate: сырое тело запроса, имя — в ?name=. Потом его можно приложить к reply или к самой команде."""
        session = await self._emu_session(request.query.get('module', ''))
        limit_mb = int(await self._cfg('emulate_upload_mb', 25) or 25)
        limit = max(1, min(limit_mb, 50)) * 1024 * 1024
        declared = request.content_length
        if declared is not None and declared > limit:
            raise ApiError(f'Файл больше лимита ({limit // (1024 * 1024)} МБ) — лимит меняется: .config web_panel emulate_upload_mb', 413)
        data = await request.read()
        try:
            up = session.save_upload(request.query.get('name', 'file'), data, limit)
        except ValueError as e:
            raise ApiError(str(e))
        return {'id': up['id'], 'name': up['name'], 'size': up['size'], 'mime': up['mime']}

    async def a_emu_unupload(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        return {'ok': session.drop_upload(str(b.get('id', '')))}

    async def a_emu_click(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        await session.click(str(b.get('form', '')), b.get('data', ''))
        return {'ok': True}

    async def a_emu_input(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        await session.send_input(str(b.get('form', '')), b.get('data', ''), str(b.get('value', ''))[:20000])
        return {'ok': True}

    async def a_emu_say(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        try:
            session.say(str(b.get('text', ''))[:20000])
        except ValueError as e:
            raise ApiError(str(e))
        return {'ok': True}

    async def a_emu_stop(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        for task in list(session.tasks):
            task.cancel()
        for waiter in list(session.waiters):
            waiter.cancel()
        return {'ok': True}

    async def a_emu_clear(self, request):
        b = await self._json(request)
        session = await self._emu_session(b.get('module', ''))
        await session.clear()
        return {'ok': True}

    async def h_emu_file(self, request):
        """Скачивание файла, который команда отправила в ленту (только зарегистрированные сессией)."""
        try:
            session = await self._emu_session(request.query.get('module', ''))
        except ApiError as e:
            return web.json_response({'error': e.message}, status=e.status)
        path = session.files.get(request.query.get('id', ''))
        if path is None or not path.is_file():
            return web.json_response({'error': 'файл не найден'}, status=404)
        from urllib.parse import quote
        return web.FileResponse(path, headers={
            'Content-Disposition': "attachment; filename*=UTF-8''" + quote(path.name)})
