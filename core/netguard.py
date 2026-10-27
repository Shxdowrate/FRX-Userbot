"""
core/netguard.py — бережное переподключение Telethon при плохой или переключаемой сети.

Что было (лог при переключении источника интернета, 1.0.6): соединение с Telegram устанавливалось и тут же рвалось
(WinError 10054 — VPN / новая сеть ещё не готовы). Telethon переподключается без паузы, если сам connect прошёл, —
за 10 секунд набегали тысячи переподключений, и на каждое запускался ещё и get_me(). Telegram отвечал на этот шквал
транспортным флуд-баном (HTTP 429), Telethon исчерпывал попытки, main.py падал, keeper перезапускал его — и новый
процесс снова упирался в бан. Связь возвращалась только через 2–3 минуты, когда бан снимался сам.

Что делает install() (один раз, до создания клиентов — main.py):
    - пауза перед переподключением, если прошлое соединение прожило меньше минуты: 0, 1, 2, 4 … 60 с;
      соединение продержалось дольше минуты — счётчик сбрасывается;
    - ответ 429 (Telegram просит притормозить) — пауза не меньше 30 с, а не мгновенный повтор;
    - запрос «я снова тут» после переподключения (_handle_auto_reconnect → get_me) — не чаще раза в 10 с и не
      параллельно: раньше их запускались сотни одновременно.
main.py при обрыве, который Telethon не пережил сам, не падает, а переподключает клиента с той же паузой (reconnect_delay).
"""

import asyncio
import time

STABLE_AFTER = 60.0        # соединение прожило столько — считаем сеть нормальной, паузы сбрасываются
MAX_DELAY = 30.0
FLOOD_DELAY = 30.0         # минимальная пауза после HTTP 429 от Telegram
HELLO_INTERVAL = 10.0      # get_me() после переподключения — не чаще

_installed = False


def is_flood(error) -> bool:
    return isinstance(error, BufferError) and getattr(error, 'code', None) == 429


def next_delay(state: dict, now: float, error=None) -> float:
    """Пауза перед следующей попыткой. state = {'fails', 'up_at'} — живёт у отправителя (или у main.py)."""
    up_at = state.get('up_at')
    if up_at is not None and now - up_at >= STABLE_AFTER:
        state['fails'] = 0
    state['fails'] = state.get('fails', 0) + 1
    delay = 0.0 if state['fails'] == 1 else min(MAX_DELAY, 2.0 ** (state['fails'] - 2))
    if is_flood(error):
        delay = max(delay, FLOOD_DELAY)
    return delay


def reconnect_delay(state: dict, error=None) -> float:
    """Для main.py: пауза перед ручным переподключением клиента (минимум 1 с)."""
    return max(1.0, next_delay(state, time.monotonic(), error))


def _say(text):
    print(f"[FRX] {text}")


def install():
    global _installed
    if _installed:
        return
    from telethon import TelegramClient
    from telethon.network.mtprotosender import MTProtoSender

    orig_reconnect = MTProtoSender._reconnect
    orig_connect = MTProtoSender._connect
    orig_hello = TelegramClient._handle_auto_reconnect

    async def _reconnect(self, last_error):
        st = self.__dict__.setdefault('_frx_net', {'fails': 0, 'up_at': None, 'said': 0.0})
        delay = next_delay(st, time.monotonic(), last_error)
        if delay:
            if self._connection is not None:              # мёртвое соединение закрываем сразу, а не после паузы
                try:
                    await self._connection.disconnect()
                except Exception:
                    pass
            now = time.monotonic()
            if now - st['said'] > 20 or is_flood(last_error):
                st['said'] = now
                why = ("Telegram просит притормозить (429)" if is_flood(last_error)
                       else "соединение с Telegram рвётся сразу после подключения")
                _say(f"{why} — повтор через {delay:g} с, а не мгновенно (попытка {st['fails']})")
            await asyncio.sleep(delay)
        st['up_at'] = None
        await orig_reconnect(self, last_error)
        try:
            if self._user_connected and self._transport_connected():
                st['up_at'] = time.monotonic()
        except Exception:
            pass

    async def _connect(self):
        try:
            return await orig_connect(self)
        except BufferError as e:
            if is_flood(e):                               # иначе Telethon повторяет 429 мгновенно, пока не кончатся попытки
                _say(f"Telegram просит притормозить (429) — жду {FLOOD_DELAY:g} с")
                await asyncio.sleep(FLOOD_DELAY)
            raise

    async def _handle_auto_reconnect(self):
        st = self.__dict__.setdefault('_frx_hello', {'busy': False, 'last': 0.0})
        if st['busy'] or time.monotonic() - st['last'] < HELLO_INTERVAL:
            return
        st['busy'] = True
        try:
            await asyncio.sleep(1)                        # соединение, которое рвётся сразу, не дёргаем лишний раз
            if self.is_connected():
                await orig_hello(self)
        finally:
            st['busy'] = False
            st['last'] = time.monotonic()

    MTProtoSender._reconnect = _reconnect
    MTProtoSender._connect = _connect
    TelegramClient._handle_auto_reconnect = _handle_auto_reconnect
    _installed = True
