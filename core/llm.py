"""
core/llm.py

Слой нейросетей FRX: подключения (провайдер + ключ), список моделей, чат с потоковой выдачей, учёт токенов и «планов».
Им пользуются и сайт (вкладка neuro, помощник в WebIDE), и модуль NeuroDash (`engine.llm`).

Провайдер выбирается ВРУЧНУЮ при добавлении ключа (автоопределение по ключу не делается — чтобы не рассылать ключ чужим сервисам). Сразу после
добавления запрашивается список моделей этого провайдера — они появляются в списке выбора.

  ollama      — локальные модели (`/api/tags`; при каждом запросе списка сканируется заново)
  openai, deepseek, openrouter, groq, xai, google (Gemini), mistral, custom — OpenAI-совместимый протокол (`/chat/completions`)
  anthropic   — Claude (`/messages`)
  gigachat    — GigaChat (OAuth по «ключу авторизации» + OpenAI-подобный чат; сертификаты Минцифры — см. verify_ssl)

Данные лежат в user/data/neuro/: providers.json (в том числе КЛЮЧИ — открытым текстом, права 600 там, где ОС это умеет), usage.json (расход токенов).
Ключ на сайте показывается маской; целиком — только по кнопке «показать». Ключи никогда не попадают в логи и тексты ошибок (scrub()).

План — лимит токенов на подключение за период (день / неделя / месяц / всего). Если включён «жёсткий» лимит, запросы после исчерпания не отправляются.
"""

import asyncio
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import aiohttp

from .paths import DATA_DIR

NEURO_DIR = DATA_DIR / 'neuro'

PROVIDERS = {
    'ollama': {'title': 'Ollama (локально)', 'kind': 'ollama', 'base': 'http://127.0.0.1:11434', 'key': False, 'url_editable': True,
               'hint': 'Локальные модели. Ключ не нужен; адрес по умолчанию http://127.0.0.1:11434.'},
    'openai': {'title': 'OpenAI (ChatGPT)', 'kind': 'openai', 'base': 'https://api.openai.com/v1', 'key': True, 'hint': 'Ключ вида sk-… из platform.openai.com.'},
    'anthropic': {'title': 'Anthropic (Claude)', 'kind': 'anthropic', 'base': 'https://api.anthropic.com/v1', 'key': True, 'hint': 'Ключ вида sk-ant-… из console.anthropic.com.'},
    'deepseek': {'title': 'DeepSeek', 'kind': 'openai', 'base': 'https://api.deepseek.com/v1', 'key': True, 'hint': 'Ключ из platform.deepseek.com.'},
    'gigachat': {'title': 'GigaChat (Сбер)', 'kind': 'gigachat', 'base': 'https://gigachat.devices.sberbank.ru/api/v1', 'key': True,
                 'auth_url': 'https://ngw.devices.sberbank.ru:9443/api/v2/oauth', 'scopes': ['GIGACHAT_API_PERS', 'GIGACHAT_API_B2B', 'GIGACHAT_API_CORP'],
                 'hint': 'Вставьте «Ключ авторизации» (Authorization key) из личного кабинета GigaChat API — он бессрочный; временный access token (~30 мин) FRX получает и обновляет сам. Можно вставить и пару client_id:client_secret. Область: PERS — для физлиц.'},
    'openrouter': {'title': 'OpenRouter', 'kind': 'openai', 'base': 'https://openrouter.ai/api/v1', 'key': True, 'hint': 'Ключ вида sk-or-… — одним ключом много моделей разных авторов.'},
    'groq': {'title': 'Groq', 'kind': 'openai', 'base': 'https://api.groq.com/openai/v1', 'key': True, 'hint': 'Ключ вида gsk_… из console.groq.com.'},
    'xai': {'title': 'xAI (Grok)', 'kind': 'openai', 'base': 'https://api.x.ai/v1', 'key': True, 'hint': 'Ключ вида xai-… из console.x.ai.'},
    'google': {'title': 'Google (Gemini)', 'kind': 'openai', 'base': 'https://generativelanguage.googleapis.com/v1beta/openai', 'key': True, 'hint': 'Ключ вида AIza… из Google AI Studio.'},
    'mistral': {'title': 'Mistral', 'kind': 'openai', 'base': 'https://api.mistral.ai/v1', 'key': True, 'hint': 'Ключ из console.mistral.ai.'},
    'custom': {'title': 'Свой адрес (OpenAI-совместимый)', 'kind': 'openai', 'base': '', 'key': False, 'url_editable': True, 'url_required': True,
               'hint': 'LM Studio, vLLM, llama.cpp, свой прокси — любой сервер с /v1/chat/completions. Ключ — если нужен.'},
}

CHAT_SKIP = re.compile(r'embed|whisper|tts|dall-e|moderation|davinci-002|babbage|rerank|transcribe|image|audio|realtime|guard|vision-preview|clip', re.I)
PERIODS = ('none', 'day', 'week', 'month', 'total')
DEFAULT_MAX_TOKENS = 4096


def approx_tokens(text: str) -> int:
    """Грубая оценка токенов, когда провайдер не вернул расход: латиница ~4 символа на токен, кириллица ~2,2."""
    if not text:
        return 0
    cyr = sum(1 for c in text if '\u0400' <= c <= '\u04ff')
    return int(cyr / 2.2 + (len(text) - cyr) / 4) + 1


def mask_key(key: str) -> str:
    key = key or ''
    if not key:
        return ''
    if len(key) <= 12:
        return '•' * len(key)
    return f"{key[:4]}…{key[-4:]}"


def gigachat_key(raw):
    """Ключ GigaChat → «ключ авторизации» (base64 от client_id:client_secret), который живёт бессрочно; access token FRX получает сам.
    Принимает: ключ авторизации; его же с префиксом «Basic »; пару «client_id:client_secret» (закодирует сам).
    Временный access token (JWT «eyJ…», живёт ~30 мин) не подходит — вернётся понятная ошибка."""
    import base64
    k = re.sub(r'\s+', '', str(raw or ''))
    if k.lower().startswith('basic'):
        k = k[5:]
    if k.lower().startswith('bearer') or (k.startswith('eyJ') and k.count('.') >= 2) or len(k) > 400:
        raise LLMError('config', 'Это временный access token (живёт ~30 минут) — он не подходит. Вставьте «Ключ авторизации» '
                                 '(Authorization key) из личного кабинета GigaChat API: FRX сам будет получать и обновлять токены.')
    if ':' in k and re.fullmatch(r'[\w-]+:[\w-]+', k):
        k = base64.b64encode(k.encode()).decode()
    try:
        dec = base64.b64decode(k + '=' * (-len(k) % 4), validate=True).decode('utf-8')
        if ':' not in dec:
            raise ValueError
    except Exception:
        raise LLMError('config', 'Не похоже на ключ авторизации GigaChat: это base64 от «client_id:client_secret» '
                                 '(в личном кабинете — «Ключ авторизации» / Authorization key). Можно вставить и саму пару client_id:client_secret.')
    return k


class LLMError(Exception):
    """kind: auth | quota | rate | network | timeout | bad_request | server | cancelled | plan_limit | config."""

    def __init__(self, kind, message, status=None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def scrub(text, *secrets):
    """Убирает ключи/токены из текста (ошибки, логи)."""
    text = str(text)
    for s in secrets:
        if s and len(s) >= 6:
            text = text.replace(s, mask_key(s))
    return re.sub(r'(sk-[A-Za-z0-9_\-]{8,}|AIza[0-9A-Za-z_\-]{20,}|gsk_[A-Za-z0-9]{16,}|xai-[A-Za-z0-9]{16,})', lambda m: mask_key(m.group(1)), text)


class LLMResult:
    def __init__(self, text='', tokens_in=0, tokens_out=0, model='', finish='stop', estimated=False):
        self.text = text
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.model = model
        self.finish = finish
        self.estimated = estimated          # расход оценён, а не взят из ответа провайдера

    @property
    def total(self):
        return self.tokens_in + self.tokens_out


# --------------------------------------------------------------------------- #
# хранилище
# --------------------------------------------------------------------------- #

def _period_start(period, now=None):
    now = now or time.time()
    lt = time.localtime(now)
    if period == 'day':
        return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    if period == 'week':
        day0 = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
        return day0 - lt.tm_wday * 86400
    if period == 'month':
        return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))
    return 0.0


def _next_reset(period, start):
    if period == 'day':
        return start + 86400
    if period == 'week':
        return start + 7 * 86400
    if period == 'month':
        lt = time.localtime(start)
        y, m = (lt.tm_year + 1, 1) if lt.tm_mon == 12 else (lt.tm_year, lt.tm_mon + 1)
        return time.mktime((y, m, 1, 0, 0, 0, 0, 0, -1))
    return None


class Store:
    """providers.json + usage.json. Все изменения — под замком и с атомарной записью."""

    def __init__(self, root=None):
        self.root = Path(root) if root else NEURO_DIR
        self.lock = threading.RLock()
        self._prov = None
        self._usage = None

    # ---- файлы ---- #
    def _read(self, name, default):
        p = self.root / name
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, name, data, private=False):
        self.root.mkdir(parents=True, exist_ok=True)
        p = self.root / name
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        if private:
            try:
                os.chmod(tmp, 0o600)
            except OSError:
                pass
        os.replace(tmp, p)

    @property
    def prov(self):
        with self.lock:
            if self._prov is None:
                d = self._read('providers.json', {})
                d.setdefault('connections', [])
                d.setdefault('selected', {})
                self._prov = d
            return self._prov

    @property
    def usage(self):
        with self.lock:
            if self._usage is None:
                d = self._read('usage.json', {})
                d.setdefault('conns', {})
                d.setdefault('days', {})
                self._usage = d
            return self._usage

    def save_prov(self):
        with self.lock:
            self._write('providers.json', self.prov, private=True)

    def save_usage(self):
        with self.lock:
            self._write('usage.json', self.usage)


# --------------------------------------------------------------------------- #
# основной класс
# --------------------------------------------------------------------------- #

class LLM:
    def __init__(self, root=None):
        self.store = Store(root)
        self._tokens = {}                   # gigachat: conn_id → (token, expires)
        self._tok_locks = {}
        self._models_ts = {}
        self._session = None

    # ------------------------------------------------------------------ #
    # подключения
    # ------------------------------------------------------------------ #

    def connections(self):
        with self.store.lock:
            self._ensure_local()
            return self.store.prov['connections']

    def _ensure_local(self):
        conns = self.store.prov['connections']
        if not any(c['provider'] == 'ollama' for c in conns):
            conns.insert(0, {'id': 'ollama', 'provider': 'ollama', 'name': 'Ollama (локально)', 'key': '', 'base_url': PROVIDERS['ollama']['base'], 'scope': '', 'verify_ssl': True,
                             'enabled': True, 'models': [], 'plan': {'limit': None, 'period': 'none', 'hard': False}, 'created': time.time(), 'auto': True})

    def get(self, conn_id):
        for c in self.connections():
            if c['id'] == conn_id:
                return c
        raise LLMError('config', f"Подключение «{conn_id}» не найдено")

    def describe(self, c, reveal=False):
        """Подключение для интерфейса: ключ — маской (целиком только при reveal=True)."""
        out = {k: v for k, v in c.items() if k != 'key'}
        out['key_set'] = bool(c.get('key'))
        out['key'] = c.get('key', '') if reveal else mask_key(c.get('key', ''))
        out['provider_title'] = PROVIDERS.get(c['provider'], {}).get('title', c['provider'])
        out['plan_status'] = self.plan_status(c['id'])
        return out

    def add_connection(self, provider, key='', name='', base_url='', scope='', verify_ssl=None):
        spec = PROVIDERS.get(provider)
        if spec is None:
            raise LLMError('config', 'Неизвестный провайдер')
        key = (key or '').strip()
        base_url = (base_url or '').strip().rstrip('/')
        if spec['key'] and not key:
            raise LLMError('config', 'Для этого провайдера нужен ключ')
        if spec.get('url_required') and not base_url:
            raise LLMError('config', 'Укажите адрес сервера (например http://127.0.0.1:1234/v1)')
        if base_url and not re.match(r'^https?://[^\s/]+', base_url):
            raise LLMError('config', 'Адрес должен начинаться с http:// или https://')
        if provider == 'gigachat' and scope and scope not in spec['scopes']:
            raise LLMError('config', 'Неизвестная область GigaChat')
        if provider == 'gigachat':
            key = gigachat_key(key)
        with self.store.lock:
            conns = self.connections()
            n, cid = 1, provider
            while any(c['id'] == cid for c in conns):
                n += 1
                cid = f'{provider}{n}'
            c = {'id': cid, 'provider': provider, 'name': (name or '').strip()[:40] or spec['title'], 'key': key,
                 'base_url': base_url or spec['base'], 'scope': scope or (spec['scopes'][0] if provider == 'gigachat' else ''),
                 'verify_ssl': (provider != 'gigachat') if verify_ssl is None else bool(verify_ssl), 'enabled': True, 'models': [],
                 'plan': {'limit': None, 'period': 'none', 'hard': False}, 'created': time.time()}
            conns.append(c)
            self.store.save_prov()
            return c

    def update_connection(self, conn_id, **fields):
        with self.store.lock:
            c = self.get(conn_id)
            if 'name' in fields:
                c['name'] = str(fields['name']).strip()[:40] or c['name']
            if 'key' in fields and fields['key'] is not None and str(fields['key']).strip():
                c['key'] = gigachat_key(fields['key']) if c['provider'] == 'gigachat' else str(fields['key']).strip()
                self._tokens.pop(conn_id, None)
            if 'base_url' in fields and PROVIDERS[c['provider']].get('url_editable'):
                u = str(fields['base_url'] or '').strip().rstrip('/')
                if u and not re.match(r'^https?://[^\s/]+', u):
                    raise LLMError('config', 'Адрес должен начинаться с http:// или https://')
                c['base_url'] = u or PROVIDERS[c['provider']]['base']
            if 'verify_ssl' in fields:
                c['verify_ssl'] = bool(fields['verify_ssl'])
            if 'enabled' in fields:
                c['enabled'] = bool(fields['enabled'])
            if 'scope' in fields and c['provider'] == 'gigachat' and fields['scope'] in PROVIDERS['gigachat']['scopes']:
                c['scope'] = fields['scope']
                self._tokens.pop(conn_id, None)
            self.store.save_prov()
            return c

    def delete_connection(self, conn_id):
        with self.store.lock:
            c = self.get(conn_id)
            if c.get('auto'):
                raise LLMError('config', 'Локальное подключение Ollama удалить нельзя — его можно отключить')
            self.store.prov['connections'] = [x for x in self.connections() if x['id'] != conn_id]
            for k, v in list(self.store.prov['selected'].items()):
                if str(v).startswith(conn_id + '|'):
                    del self.store.prov['selected'][k]
            self.store.usage['conns'].pop(conn_id, None)
            self.store.save_prov()
            self.store.save_usage()

    def set_plan(self, conn_id, limit=None, period='none', hard=False):
        if period not in PERIODS:
            raise LLMError('config', 'Период: none, day, week, month или total')
        if limit not in (None, '') and (int(limit) < 0):
            raise LLMError('config', 'Лимит не может быть отрицательным')
        with self.store.lock:
            c = self.get(conn_id)
            c['plan'] = {'limit': int(limit) if limit not in (None, '') else None, 'period': period, 'hard': bool(hard)}
            self.store.save_prov()
            return c['plan']

    # ------------------------------------------------------------------ #
    # модели
    # ------------------------------------------------------------------ #

    def models(self, only_enabled=True):
        """Плоский список моделей всех включённых подключений: [{ref, conn, provider, model, name}]."""
        out = []
        for c in self.connections():
            if not c.get('enabled', True):
                continue
            for m in c.get('models', []):
                if only_enabled and not m.get('enabled', True):
                    continue
                out.append({'ref': f"{c['id']}|{m['id']}", 'conn': c['id'], 'provider': c['provider'], 'model': m['id'], 'name': m.get('title') or m['id'],
                            'conn_name': c['name'], 'enabled': m.get('enabled', True)})
        return out

    def set_model_enabled(self, conn_id, model_id, enabled):
        with self.store.lock:
            for m in self.get(conn_id).get('models', []):
                if m['id'] == model_id:
                    m['enabled'] = bool(enabled)
            self.store.save_prov()

    def selected(self, surface='chat'):
        """ref выбранной модели для «поверхности» (chat — NeuroDash, ide — помощник WebIDE); если не выбрана или пропала — первая доступная."""
        ref = self.store.prov['selected'].get(surface)
        avail = [m['ref'] for m in self.models()]
        if ref in avail:
            return ref
        fallback = self.store.prov['selected'].get('chat')
        if surface != 'chat' and fallback in avail:
            return fallback
        return avail[0] if avail else None

    def select(self, surface, ref):
        if ref not in [m['ref'] for m in self.models()]:
            raise LLMError('config', 'Такой модели нет в списке (или она отключена)')
        with self.store.lock:
            self.store.prov['selected'][surface] = ref
            self.store.save_prov()

    def parse_ref(self, ref):
        if not ref or '|' not in ref:
            raise LLMError('config', 'Модель не выбрана — добавьте подключение и выберите модель (сайт → neuro)')
        conn_id, model = ref.split('|', 1)
        return self.get(conn_id), model

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #

    async def _http(self):
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=10, sock_connect=10, sock_read=120))
        return self._session

    async def close(self):
        if self._session is not None and not self._session.closed:
            await self._session.close()

    def _headers(self, c, extra=None):
        h = {'Content-Type': 'application/json', 'Accept': 'application/json', 'User-Agent': 'FRX-NeuroDash'}
        kind = PROVIDERS[c['provider']]['kind']
        if kind == 'anthropic':
            h['x-api-key'] = c['key']
            h['anthropic-version'] = '2023-06-01'
        elif kind in ('openai', 'gigachat') and c.get('key'):
            h['Authorization'] = f"Bearer {c['key']}"
        if c['provider'] == 'openrouter':
            h['X-Title'] = 'FRX'
        h.update(extra or {})
        return h

    def _ssl(self, c):
        return None if c.get('verify_ssl', True) else False

    async def _gigachat_token(self, c, force=False):
        """Access token GigaChat по ключу авторизации. Кэш на подключение; обновляется сам за 3 минуты до истечения
        (и сразу, если сервер ответил 401) — ключ менять не нужно, он бессрочный."""
        tok = self._tokens.get(c['id'])
        if tok and not force and tok[1] - time.time() > 180:
            return tok[0]
        lock = self._tok_locks.setdefault(c['id'], asyncio.Lock())
        async with lock:                                     # параллельные запросы не просят токен по несколько раз
            tok = self._tokens.get(c['id'])
            if tok and not force and tok[1] - time.time() > 180:
                return tok[0]
            key = gigachat_key(c['key'])                     # старое подключение с access token вместо ключа — понятная ошибка
            spec = PROVIDERS['gigachat']
            s = await self._http()
            headers = {'Authorization': f"Basic {key}", 'RqUID': str(uuid.uuid4()), 'Content-Type': 'application/x-www-form-urlencoded', 'Accept': 'application/json'}
            try:
                async with s.post(spec['auth_url'], data={'scope': c.get('scope') or spec['scopes'][0]}, headers=headers, ssl=self._ssl(c),
                                  timeout=aiohttp.ClientTimeout(total=30)) as r:
                    body = await r.text()
                    if r.status != 200:
                        err = self._http_error(r.status, body, c)
                        if r.status in (400, 401, 403):
                            err = LLMError('auth', 'GigaChat не принял ключ авторизации или область '
                                                   f"({c.get('scope') or spec['scopes'][0]}): {err}", r.status)
                        raise err
                    data = json.loads(body)
            except aiohttp.ClientError as e:
                raise LLMError('network', self._net_msg(e, c))
            except asyncio.TimeoutError:
                raise LLMError('timeout', 'GigaChat не ответил при получении токена')
            token = data.get('access_token')
            if not token:
                raise LLMError('auth', 'GigaChat не выдал токен — проверьте ключ авторизации и область')
            ea = data.get('expires_at') or 0
            exp = ea / 1000 if ea > 1e11 else (ea if ea > 1e9 else time.time() + 1500)
            self._tokens[c['id']] = (token, exp)
            return token

    def _drop_token(self, c):
        self._tokens.pop(c['id'], None)

    async def _auth_headers(self, c):
        h = self._headers(c)
        if c['provider'] == 'gigachat':
            h['Authorization'] = f"Bearer {await self._gigachat_token(c)}"
        return h

    def _net_msg(self, e, c):
        host = re.sub(r'^https?://', '', c['base_url']).split('/')[0]
        if c['provider'] == 'ollama':
            return f"Не удалось подключиться к Ollama ({host}). Запущена ли она (ollama serve)?"
        return scrub(f"Не удалось подключиться к {host}: {type(e).__name__}", c.get('key'))

    def _http_error(self, status, body, c):
        msg = ''
        try:
            d = json.loads(body)
            err = d.get('error', d)
            msg = err.get('message') if isinstance(err, dict) else str(err)
            msg = msg or d.get('message') or ''
        except (ValueError, AttributeError):
            msg = (body or '')[:200]
        msg = scrub(msg, c.get('key'))[:300]
        low = (body or '').lower()
        if status in (401, 403):
            return LLMError('auth', f"Ключ не принят ({status})" + (f": {msg}" if msg else ''), status)
        if status == 402 or 'insufficient_quota' in low or 'billing' in low and status in (400, 429):
            return LLMError('quota', f"Закончились средства или квота у провайдера" + (f": {msg}" if msg else ''), status)
        if status == 429:
            return LLMError('rate', 'Слишком много запросов (429) — подождите немного' + (f": {msg}" if msg else ''), status)
        if status == 404:
            return LLMError('bad_request', 'Не найдено (404): ' + (msg or 'проверьте адрес и название модели'), status)
        if 400 <= status < 500:
            return LLMError('bad_request', f"Запрос отклонён ({status})" + (f": {msg}" if msg else ''), status)
        return LLMError('server', f"Сервер провайдера ответил {status}" + (f": {msg}" if msg else ''), status)

    # ------------------------------------------------------------------ #
    # список моделей у провайдера
    # ------------------------------------------------------------------ #

    async def fetch_models(self, c):
        spec = PROVIDERS[c['provider']]
        s = await self._http()
        kind = spec['kind']
        try:
            if kind == 'ollama':
                url = c['base_url'] + '/api/tags'
            elif kind == 'anthropic':
                url = c['base_url'] + '/models?limit=100'
            else:
                url = c['base_url'] + '/models'
            for attempt in (1, 2):
                async with s.get(url, headers=await self._auth_headers(c), ssl=self._ssl(c), timeout=aiohttp.ClientTimeout(total=20)) as r:
                    body = await r.text()
                    if r.status == 401 and c['provider'] == 'gigachat' and attempt == 1:
                        self._drop_token(c)
                        continue
                    if r.status != 200:
                        raise self._http_error(r.status, body, c)
                    data = json.loads(body)
                    break
        except aiohttp.ClientError as e:
            raise LLMError('network', self._net_msg(e, c))
        except asyncio.TimeoutError:
            raise LLMError('timeout', 'Провайдер не прислал список моделей вовремя')
        except ValueError:
            raise LLMError('bad_request', 'Ответ со списком моделей не похож на JSON — проверьте адрес')
        if kind == 'ollama':
            items = [{'id': m.get('name') or m.get('model'), 'title': None} for m in data.get('models', [])]
        elif kind == 'anthropic':
            items = [{'id': m['id'], 'title': m.get('display_name')} for m in data.get('data', []) if m.get('id')]
        else:
            raw = data.get('data', data.get('models', []))
            items = []
            for m in raw:
                mid = m.get('id') or m.get('name')
                if mid:
                    items.append({'id': mid.replace('models/', '', 1) if c['provider'] == 'google' else mid, 'title': None})
        items = [i for i in items if i['id'] and not (kind != 'ollama' and CHAT_SKIP.search(i['id']))]
        return sorted(items, key=lambda i: i['id'].lower())

    async def refresh_models(self, conn_id):
        c = self.get(conn_id)
        items = await self.fetch_models(c)
        with self.store.lock:
            old = {m['id']: m for m in c.get('models', [])}
            c['models'] = [{'id': i['id'], 'title': i['title'], 'enabled': old.get(i['id'], {}).get('enabled', True)} for i in items]
            c['models_checked'] = time.time()
            self.store.save_prov()
        self._models_ts[conn_id] = time.time()
        return c['models']

    async def scan_local(self, max_age=30):
        """Локальные модели Ollama подхватываются сами (не чаще раза в max_age секунд); недоступность Ollama — не ошибка."""
        for c in list(self.connections()):
            if c['provider'] == 'ollama' and c.get('enabled', True) and time.time() - self._models_ts.get(c['id'], 0) > max_age:
                try:
                    await self.refresh_models(c['id'])
                except LLMError:
                    self._models_ts[c['id']] = time.time()

    # ------------------------------------------------------------------ #
    # расход токенов и планы
    # ------------------------------------------------------------------ #

    def _usage_entry(self, conn_id):
        c = self.get(conn_id)
        plan = c.get('plan') or {}
        period = plan.get('period', 'none')
        u = self.store.usage['conns'].get(conn_id)
        now = time.time()
        if u is None:
            u = self.store.usage['conns'][conn_id] = {'start': _period_start(period, now) or now, 'in': 0, 'out': 0, 'calls': 0}
        elif period in ('day', 'week', 'month') and u['start'] < _period_start(period, now):
            u.update({'start': _period_start(period, now), 'in': 0, 'out': 0, 'calls': 0})        # новый период — счётчик с нуля
        return u, plan, period

    def plan_status(self, conn_id):
        with self.store.lock:
            try:
                u, plan, period = self._usage_entry(conn_id)
            except LLMError:
                return None
            used = u['in'] + u['out']
            limit = plan.get('limit')
            return {'used': used, 'in': u['in'], 'out': u['out'], 'calls': u['calls'], 'limit': limit, 'period': period, 'hard': bool(plan.get('hard')),
                    'percent': round(used * 100 / limit, 1) if limit else None, 'exceeded': bool(limit) and used >= limit,
                    'since': u['start'], 'resets_at': _next_reset(period, u['start'])}

    async def balance(self, conn_id, max_age=120):
        """
        остаток денег у провайдера, если он отдаёт его через API (DeepSeek, OpenRouter).
        Возвращает {'text': '12.34 USD', 'ts': ...} либо None (провайдер баланс не отдаёт / запрос не удался).
        Результат кэшируется на max_age секунд, ошибки сети не бросаются.
        """
        try:
            c = self.get(conn_id)
        except LLMError:
            return None
        cache = self.__dict__.setdefault('_balance_cache', {})
        hit = cache.get(conn_id)
        if hit and time.time() - hit[0] < max_age:
            return hit[1]
        prov, text = c['provider'], None
        if prov in ('deepseek', 'openrouter') and c.get('key'):
            try:
                s = await self._http()
                base = PROVIDERS[prov]['base']
                if prov == 'deepseek':
                    url = base.rsplit('/v1', 1)[0] + '/user/balance'
                else:
                    url = base + '/credits'
                async with s.get(url, headers=self._headers(c), timeout=aiohttp.ClientTimeout(total=8)) as r:
                    data = await r.json(content_type=None) if r.status == 200 else None
                if data and prov == 'deepseek':
                    infos = data.get('balance_infos') or []
                    if infos:
                        text = ', '.join(f"{i.get('total_balance')} {i.get('currency')}" for i in infos[:2])
                elif data and prov == 'openrouter':
                    d = data.get('data') or {}
                    if 'total_credits' in d:
                        text = f"{float(d['total_credits']) - float(d.get('total_usage') or 0):.2f} USD"
            except Exception:
                text = None
        res = {'text': text, 'ts': time.time()} if text else None
        cache[conn_id] = (time.time(), res)
        return res

    def check_plan(self, conn_id):
        st = self.plan_status(conn_id)
        if st and st['hard'] and st['exceeded']:
            raise LLMError('plan_limit', f"План исчерпан: потрачено {st['used']:,} из {st['limit']:,} токенов".replace(',', ' ')
                           + (f" (обнулится {time.strftime('%d.%m %H:%M', time.localtime(st['resets_at']))})" if st['resets_at'] else '. Сбросьте счётчик или увеличьте лимит на сайте (neuro)'))

    def record(self, conn_id, model, tokens_in, tokens_out):
        with self.store.lock:
            u, _plan, _period = self._usage_entry(conn_id)
            u['in'] += int(tokens_in)
            u['out'] += int(tokens_out)
            u['calls'] += 1
            day = time.strftime('%Y-%m-%d')
            row = self.store.usage['days'].setdefault(day, {}).setdefault(conn_id, {}).setdefault(model, [0, 0, 0])
            row[0] += int(tokens_in)
            row[1] += int(tokens_out)
            row[2] += 1
            days = self.store.usage['days']
            if len(days) > 120:
                for old in sorted(days)[:len(days) - 120]:
                    del days[old]
            self.store.save_usage()

    def reset_usage(self, conn_id):
        with self.store.lock:
            self.get(conn_id)
            self.store.usage['conns'].pop(conn_id, None)
            self.store.save_usage()

    def usage_days(self, last=30):
        with self.store.lock:
            days = self.store.usage['days']
            return {d: days[d] for d in sorted(days)[-last:]}

    # ------------------------------------------------------------------ #
    # чат
    # ------------------------------------------------------------------ #

    @staticmethod
    def _norm_messages(messages, system):
        """[{'role','content'}] → (system_text, [user/assistant сообщения]) без пустых и лишних полей."""
        sys_parts = [system] if system else []
        conv = []
        for m in messages:
            role, text = m.get('role'), str(m.get('content') or '')
            if role == 'system':
                sys_parts.append(text)
            elif role in ('user', 'assistant') and text.strip():
                conv.append({'role': role, 'content': text})
        return '\n\n'.join(p for p in sys_parts if p), conv

    async def chat(self, ref, messages, *, system=None, stream=False, on_delta=None, cancel=None, max_tokens=None, temperature=None, record=True):
        """
        Один запрос к модели → LLMResult (text, tokens_in, tokens_out, finish). stream=True: on_delta(text_chunk) вызывается по мере поступления
        (корутина или функция). cancel — asyncio.Event: если выставлен, запрос обрывается и возвращается то, что успело прийти (finish='cancelled').
        Расход записывается в usage.json (record=False — не учитывать).
        """
        c, model = self.parse_ref(ref)
        if not c.get('enabled', True):
            raise LLMError('config', f"Подключение «{c['name']}» отключено")
        spec = PROVIDERS[c['provider']]
        self.check_plan(c['id'])
        sys_text, conv = self._norm_messages(messages, system)
        if not conv:
            raise LLMError('bad_request', 'Нечего отправлять: пустой запрос')
        kind = spec['kind']
        try:
            if kind == 'anthropic':
                res = await self._chat_anthropic(c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature)
            elif kind == 'ollama':
                res = await self._chat_ollama(c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature)
            else:
                res = await self._chat_openai(c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature)
        except aiohttp.ClientError as e:
            raise LLMError('network', self._net_msg(e, c))
        except asyncio.TimeoutError:
            raise LLMError('timeout', 'Модель не ответила вовремя')
        if not res.tokens_in and not res.tokens_out:                          # провайдер не прислал расход — оцениваем сами
            res.tokens_in = approx_tokens(sys_text) + sum(approx_tokens(m['content']) for m in conv)
            res.tokens_out = approx_tokens(res.text)
            res.estimated = True
        res.model = model
        if record:
            self.record(c['id'], model, res.tokens_in, res.tokens_out)
        return res

    async def _emit(self, on_delta, text):
        if on_delta is not None and text:
            r = on_delta(text)
            if asyncio.iscoroutine(r):
                await r

    async def _lines(self, resp, cancel):
        """Строки потокового ответа; прерывается, если выставлен cancel."""
        buf = b''
        async for chunk in resp.content.iter_any():
            if cancel is not None and cancel.is_set():
                return
            buf += chunk
            while b'\n' in buf:
                line, buf = buf.split(b'\n', 1)
                yield line.decode('utf-8', 'replace').rstrip('\r')
        if buf.strip():
            yield buf.decode('utf-8', 'replace').rstrip('\r')

    async def _chat_openai(self, c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature, _retry=True, _auth=True):
        msgs = ([{'role': 'system', 'content': sys_text}] if sys_text else []) + conv
        payload = {'model': model, 'messages': msgs, 'stream': bool(stream)}
        if stream and c['provider'] != 'gigachat' and _retry:
            payload['stream_options'] = {'include_usage': True}
        if max_tokens:
            payload['max_tokens'] = int(max_tokens)
        if temperature is not None:
            payload['temperature'] = float(temperature)
        s = await self._http()
        async with s.post(c['base_url'] + '/chat/completions', json=payload, headers=await self._auth_headers(c), ssl=self._ssl(c),
                          timeout=aiohttp.ClientTimeout(total=None if stream else 180, sock_read=120, connect=10)) as r:
            if r.status != 200:
                body = await r.text()
                if r.status == 401 and c['provider'] == 'gigachat' and _auth:
                    self._drop_token(c)                          # токен отозван/истёк раньше срока — берём новый и повторяем один раз
                    return await self._chat_openai(c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature, _retry, _auth=False)
                if r.status == 400 and 'stream_options' in body and _retry:
                    return await self._chat_openai(c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature, _retry=False, _auth=_auth)
                raise self._http_error(r.status, body, c)
            if not stream:
                data = await r.json(content_type=None)
                ch = (data.get('choices') or [{}])[0]
                u = data.get('usage') or {}
                return LLMResult(((ch.get('message') or {}).get('content') or ''), u.get('prompt_tokens', 0), u.get('completion_tokens', 0), finish=ch.get('finish_reason') or 'stop')
            text, tin, tout, finish = [], 0, 0, 'stop'
            async for line in self._lines(r, cancel):
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get('error'):
                    raise LLMError('server', scrub(str(obj['error'])[:300], c.get('key')))
                u = obj.get('usage')
                if u:
                    tin, tout = u.get('prompt_tokens', tin) or tin, u.get('completion_tokens', tout) or tout
                for ch in obj.get('choices') or []:
                    piece = (ch.get('delta') or {}).get('content')
                    if piece:
                        text.append(piece)
                        await self._emit(on_delta, piece)
                    if ch.get('finish_reason'):
                        finish = ch['finish_reason']
            if cancel is not None and cancel.is_set():
                finish = 'cancelled'
            return LLMResult(''.join(text), tin, tout, finish=finish)

    async def _chat_anthropic(self, c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature):
        merged = []
        for m in conv:                                                  # Claude требует чередования ролей и первого сообщения от пользователя
            if merged and merged[-1]['role'] == m['role']:
                merged[-1]['content'] += '\n\n' + m['content']
            else:
                merged.append(dict(m))
        if merged[0]['role'] != 'user':
            merged.insert(0, {'role': 'user', 'content': '(продолжение диалога)'})
        payload = {'model': model, 'max_tokens': int(max_tokens or DEFAULT_MAX_TOKENS), 'messages': merged, 'stream': bool(stream)}
        if sys_text:
            payload['system'] = sys_text
        if temperature is not None:
            payload['temperature'] = float(temperature)
        s = await self._http()
        async with s.post(c['base_url'] + '/messages', json=payload, headers=await self._auth_headers(c),
                          timeout=aiohttp.ClientTimeout(total=None if stream else 180, sock_read=120, connect=10)) as r:
            if r.status != 200:
                raise self._http_error(r.status, await r.text(), c)
            if not stream:
                data = await r.json(content_type=None)
                text = ''.join(b.get('text', '') for b in data.get('content', []) if b.get('type') == 'text')
                u = data.get('usage') or {}
                return LLMResult(text, u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0), u.get('output_tokens', 0),
                                 finish=data.get('stop_reason') or 'stop')
            text, tin, tout, finish = [], 0, 0, 'stop'
            async for line in self._lines(r, cancel):
                if not line.startswith('data:'):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except ValueError:
                    continue
                t = obj.get('type')
                if t == 'message_start':
                    u = (obj.get('message') or {}).get('usage') or {}
                    tin = u.get('input_tokens', 0) + u.get('cache_read_input_tokens', 0) + u.get('cache_creation_input_tokens', 0)
                    tout = u.get('output_tokens', 0)
                elif t == 'content_block_delta':
                    piece = (obj.get('delta') or {}).get('text')
                    if piece:
                        text.append(piece)
                        await self._emit(on_delta, piece)
                elif t == 'message_delta':
                    tout = (obj.get('usage') or {}).get('output_tokens', tout)
                    finish = (obj.get('delta') or {}).get('stop_reason') or finish
                elif t == 'error':
                    err = obj.get('error') or {}
                    raise LLMError('rate' if err.get('type') == 'overloaded_error' else 'server', scrub(err.get('message', 'ошибка потока'), c.get('key')))
                elif t == 'message_stop':
                    break
            if cancel is not None and cancel.is_set():
                finish = 'cancelled'
            return LLMResult(''.join(text), tin, tout, finish=finish)

    async def _chat_ollama(self, c, model, sys_text, conv, stream, on_delta, cancel, max_tokens, temperature):
        msgs = ([{'role': 'system', 'content': sys_text}] if sys_text else []) + conv
        opts = {}
        if max_tokens:
            opts['num_predict'] = int(max_tokens)
        if temperature is not None:
            opts['temperature'] = float(temperature)
        payload = {'model': model, 'messages': msgs, 'stream': bool(stream)}
        if opts:
            payload['options'] = opts
        s = await self._http()
        async with s.post(c['base_url'] + '/api/chat', json=payload, headers=self._headers(c),
                          timeout=aiohttp.ClientTimeout(total=None if stream else 600, sock_read=600, connect=10)) as r:
            if r.status != 200:
                body = await r.text()
                err = self._http_error(r.status, body, c)
                if r.status == 404:
                    err = LLMError('bad_request', f"Модели «{model}» нет в Ollama — скачайте её: ollama pull {model}", 404)
                raise err
            if not stream:
                data = await r.json(content_type=None)
                return LLMResult((data.get('message') or {}).get('content') or '', data.get('prompt_eval_count', 0), data.get('eval_count', 0), finish=data.get('done_reason') or 'stop')
            text, tin, tout, finish = [], 0, 0, 'stop'
            async for line in self._lines(r, cancel):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if obj.get('error'):
                    raise LLMError('bad_request', scrub(str(obj['error'])[:300]))
                piece = (obj.get('message') or {}).get('content')
                if piece:
                    text.append(piece)
                    await self._emit(on_delta, piece)
                if obj.get('done'):
                    tin, tout = obj.get('prompt_eval_count', 0), obj.get('eval_count', 0)
                    finish = obj.get('done_reason') or 'stop'
                    break
            if cancel is not None and cancel.is_set():
                finish = 'cancelled'
            return LLMResult(''.join(text), tin, tout, finish=finish)


_instance = None


def get_llm() -> LLM:
    """Общий экземпляр (engine.llm и сайт используют один и тот же — одни ключи, один счётчик расхода)."""
    global _instance
    if _instance is None:
        _instance = LLM()
    return _instance
