"""
core/web/auth.py — вход на сайт .web.

Сайт открывает только владелец юзербота — и локально (127.0.0.1, SSH-туннель), и через публичный туннель.

«Дверь» входа по умолчанию ЗАКРЫТА: страница входа говорит «вход закрыт», код запросить нельзя. Открывает её
владелец командой `.web` (или `.web login`) в Telegram: появляется 5-значный код (в ЛС от бота, без бота — в
«Избранное»), и на WINDOW_TTL секунд вход открыт — ровно для ОДНОГО входа. После успешного входа дверь снова
закрывается; после MAX_TRIES неверных кодов — тоже (и владелец получает предупреждение). Подобрать код снаружи
нельзя: без команды владельца кода просто нет.

Второй способ — Telegram Mini App: кнопка «Открыть в Telegram» под сообщением бота. Telegram подписывает данные
запуска (initData) токеном бота, сервер проверяет подпись и что это владелец — этот вход от двери не зависит.

После входа браузер получает cookie сессии (HttpOnly; живёт session_days дней, конфиг модуля Web) — устройство,
на котором уже вошли, дверь не замечает. Сессии хранятся в user/data/web_sessions.json (только SHA-256 токенов)
и переживают перезапуск. «Выйти везде» — `.web logout`.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import parse_qsl

from ..paths import DATA_DIR, atomic_write_text

SESSIONS_FILE = DATA_DIR / 'web_sessions.json'
COOKIE = 'frx_session'
CODE_LEN = 5
WINDOW_TTL = 10 * 60          # сколько открыт вход после команды .web
MAX_TRIES = 5                 # неверных кодов — и вход закрывается
RESEND_GAP = 30               # «прислать новый код» — не чаще раза в 30 с
RESENDS_PER_WINDOW = 3
INITDATA_MAX_AGE = 24 * 3600
DEFAULT_SESSION_DAYS = 30
TICKET_TTL = 15 * 60          # ссылка «войти в один клик» из ЛС от бота
HANDOFF_TTL = 90              # переезд открытой страницы на новый публичный адрес


def _h(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


class AuthError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


class WebAuth:
    def __init__(self, core):
        self.core = core
        self._sessions = None
        self._code = None              # {'hash', 'until', 'tries', 'resends', 'sent'} — пока он есть, вход открыт
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ сессии
    def _load(self):
        if self._sessions is None:
            try:
                data = json.loads(SESSIONS_FILE.read_text(encoding='utf-8'))
                self._sessions = data if isinstance(data, dict) else {}
            except (OSError, ValueError):
                self._sessions = {}
        return self._sessions

    def _save(self):
        try:
            atomic_write_text(SESSIONS_FILE, json.dumps(self._sessions, ensure_ascii=False, indent=1))
        except OSError as e:
            self.core.tlog(f"[web] не удалось сохранить сессии сайта: {e}")

    async def session_days(self) -> int:
        try:
            v = int(await self.core.get_config('session_days', module_name='web_panel'))
            return max(1, min(365, v))
        except Exception:
            return DEFAULT_SESSION_DAYS

    def check(self, token) -> bool:
        if not token or len(token) > 200:
            return False
        s = self._load().get(_h(token))
        if not s:
            return False
        now = time.time()
        if now > s.get('until', 0):
            self._sessions.pop(_h(token), None)
            self._save()
            return False
        if now - s.get('seen', 0) > 600:              # не пишем файл на каждый запрос
            s['seen'] = now
            self._save()
        return True

    async def new_session(self, via: str, agent: str = '') -> str:
        token = secrets.token_urlsafe(32)
        now = time.time()
        sessions = self._load()
        for k in [k for k, v in sessions.items() if now > v.get('until', 0)]:
            sessions.pop(k, None)
        sessions[_h(token)] = {'created': now, 'seen': now, 'until': now + await self.session_days() * 86400,
                               'via': via, 'agent': (agent or '')[:160]}
        self._save()
        return token

    def drop(self, token):
        if token and self._load().pop(_h(token), None) is not None:
            self._save()

    def drop_all(self) -> int:
        n = len(self._load())
        self._sessions = {}
        self._save()
        return n

    def count(self) -> int:
        now = time.time()
        return sum(1 for v in self._load().values() if v.get('until', 0) > now)

    # ------------------------------------------------------------------ дверь входа и код
    def is_open(self) -> bool:
        c = self._code
        if c and time.time() > c['until']:
            self._code = None
        return self._code is not None

    def window_left(self) -> int:
        return max(0, int(self._code['until'] - time.time())) if self.is_open() else 0

    def close(self):
        self._code = None

    def _new_code(self, keep=None) -> str:
        code = ''.join(secrets.choice('0123456789') for _ in range(CODE_LEN))
        now = time.time()
        self._code = {'hash': _h(code), 'until': (keep or {}).get('until', now + WINDOW_TTL), 'tries': 0,
                      'resends': (keep or {}).get('resends', 0), 'sent': now}
        return code

    def open_window(self) -> str:
        """Владелец написал .web: вход открыт на WINDOW_TTL для одного входа. Возвращает код (его доставляет вызывающий)."""
        return self._new_code()

    @staticmethod
    def code_text(code, where='') -> str:
        return (f"🔐 <b>Код входа на сайт FRX: <code>{code}</code></b>\n"
                f"Вход открыт {WINDOW_TTL // 60} мин и только для одного устройства" + (f". {where}" if where else '') + ".\n"
                "<i>Никому не сообщайте код.</i>")

    async def resend(self, where: str) -> str:
        """«Прислать новый код» со страницы входа — только пока вход открыт. Возвращает, куда отправлено."""
        async with self._lock:
            if not self.is_open():
                raise AuthError("Вход закрыт — напишите .web в Telegram", 403)
            c = self._code
            if time.time() - c['sent'] < RESEND_GAP:
                raise AuthError("Код только что отправлен — подождите полминуты", 429)
            if c['resends'] >= RESENDS_PER_WINDOW:
                raise AuthError("Новых кодов больше не будет — напишите .web в Telegram ещё раз", 429)
            c['resends'] += 1
            code = self._new_code(keep=c)
        return await self._deliver(self.code_text(code, f"Запрошен со страницы входа: {where}"))

    async def verify_code(self, code: str) -> bool:
        async with self._lock:
            if not self.is_open():
                raise AuthError("Вход закрыт — напишите .web в Telegram, придёт код", 403)
            c = self._code
            code = ''.join(ch for ch in str(code or '') if ch.isdigit())
            if hmac.compare_digest(_h(code), c['hash']):
                self._code = None                       # один вход — и дверь снова закрыта
                return True
            c['tries'] += 1
            if c['tries'] >= MAX_TRIES:
                self._code = None
                asyncio.ensure_future(self._notify_closed())
                raise AuthError("Неверный код. Вход закрыт — откройте его снова командой .web в Telegram", 403)
            raise AuthError(f"Неверный код (осталось попыток: {MAX_TRIES - c['tries']})", 400)

    async def _notify_closed(self):
        try:
            await self._deliver(f"⚠️ <b>Сайт FRX: {MAX_TRIES} неверных кодов подряд — вход закрыт</b>\n"
                                "Если это были не вы — кто-то знает адрес вашего сайта: смените его "
                                "(<code>.web tunnel new</code>) или выключите (<code>.web tunnel off</code>).")
        except Exception:
            pass

    async def _deliver(self, text) -> str:
        core = self.core
        if getattr(core, '_aiogram_bot', None) is not None:
            try:
                await core.bot_send_to_owner(text)
                return 'в ЛС от стационарного бота'
            except Exception as e:
                core.tlog(f"[web] код входа ботом не отправлен ({e}) — кладу в «Избранное»")
        try:
            await core.client.send_message('me', text, parse_mode='html')
            return 'в «Избранное» вашего аккаунта'
        except Exception as e:
            self._code = None
            raise AuthError(f"Не удалось отправить код в Telegram: {e}", 503)

    # ------------------------------------------------------------------ одноразовые билеты входа
    # Билет — ссылка «войти в один клик»: https://адрес/#frx_t=<билет>. Выдаётся только владельцу:
    #   • в ЛС от бота вместе с новой ссылкой на сайт (TICKET_TTL, один вход);
    #   • уже открытой странице, когда сменился публичный адрес (HANDOFF_TTL) — она сама переезжает на новый адрес
    #     и остаётся залогиненной. Билет живёт в памяти (не на диске) и сгорает после первого использования.
    def issue_ticket(self, ttl: int = None, via: str = 'ссылка из Telegram') -> str:
        now = time.time()
        tickets = self.__dict__.setdefault('_tickets', {})
        for k in [k for k, v in tickets.items() if now > v['until']]:
            tickets.pop(k, None)
        while len(tickets) > 20:                          # не копим: самые старые выбрасываем
            tickets.pop(next(iter(tickets)))
        token = secrets.token_urlsafe(24)
        tickets[_h(token)] = {'until': now + (ttl or TICKET_TTL), 'via': via}
        return token

    def use_ticket(self, token: str):
        """Возвращает via билета (и сжигает его) или None, если билета нет / истёк / уже использован."""
        if not token or len(token) > 100:
            return None
        t = self.__dict__.setdefault('_tickets', {}).pop(_h(str(token)), None)
        if not t or time.time() > t['until']:
            return None
        return t['via']

    # ------------------------------------------------------------------ Telegram Mini App
    def verify_init_data(self, init_data: str) -> int:
        """Проверяет initData Mini App (подпись токеном бота) и что это владелец. Возвращает id пользователя."""
        token = str(self.core.config.get('aiogram_token') or '')
        if not token:
            raise AuthError("Вход через Telegram работает только со стационарным ботом", 400)
        pairs = dict(parse_qsl(init_data or '', keep_blank_values=True))
        got = pairs.pop('hash', '')
        if not got:
            raise AuthError("Нет подписи Telegram", 400)
        check = '\n'.join(f"{k}={v}" for k, v in sorted(pairs.items()))
        secret = hmac.new(b'WebAppData', token.encode('utf-8'), hashlib.sha256).digest()
        want = hmac.new(secret, check.encode('utf-8'), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(want, got):
            raise AuthError("Подпись Telegram не сошлась", 403)
        try:
            age = time.time() - int(pairs.get('auth_date', '0'))
        except ValueError:
            age = 1e9
        if age > INITDATA_MAX_AGE:
            raise AuthError("Ссылка устарела — откройте сайт заново командой .web", 403)
        try:
            uid = int(json.loads(pairs.get('user', '{}')).get('id'))
        except (ValueError, TypeError, AttributeError):
            raise AuthError("Нет данных пользователя Telegram", 403)
        if uid != getattr(self.core, '_owner_id', None):
            raise AuthError("Сайт доступен только владельцу юзербота", 403)
        return uid
