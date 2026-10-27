"""
name: exchange_rate
category: web
title: Курс валют
description: курс и пересчёт валют (живые данные). base/target — код ISO или название по-русски: рубль/RUB, белорусский рубль/буны/BYN, доллар/USD, евро/EUR, гривна/UAH, тенге/KZT, юань/CNY, биткоин — см. crypto_price; amount — сколько единиц base пересчитать («75000 рублей в бунах» → base RUB, target BYN, amount 75000)
args: base: string, target?: string, amount?: number
example: {"base": "USD", "target": "RUB", "amount": 100}
keywords: курс, валют, доллар, евро, рубл, юань, тенге, обмен, конвертируй, бун, byn, гривн
"""
import asyncio
import gzip
import html as _html
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36'


def _check_host(url):
    """Только публичные адреса: модель (или текст страницы) не должна ходить на localhost, в локальную сеть и на сайт FRX."""
    p = urllib.parse.urlsplit(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('нужен адрес вида https://…')
    for info in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM):
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            raise ValueError('локальные и служебные адреса запрещены')


class _Guard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _check_host(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _http(url, data=None, headers=None, timeout=12, max_bytes=1500000):
    """-> (status, text, final_url, content_type). Ошибки HTTP (403, 429…) не исключения: статус возвращается как есть."""
    _check_host(url)
    h = {'User-Agent': _UA, 'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8', 'Accept': 'text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8'}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    opener = urllib.request.build_opener(_Guard)
    try:
        r = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        r = e
    raw = r.read(max_bytes)
    if (r.headers.get('Content-Encoding') or '').lower() == 'gzip':
        try:
            raw = gzip.decompress(raw)
        except Exception:
            pass
    ctype = r.headers.get('Content-Type') or ''
    cs = r.headers.get_content_charset()
    if not cs:
        m = re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', raw[:4000], re.I)
        cs = m.group(1).decode('ascii', 'ignore') if m else 'utf-8'
    try:
        text = raw.decode(cs, errors='replace')
    except LookupError:
        text = raw.decode('utf-8', errors='replace')
    return getattr(r, 'status', None) or r.code, text, getattr(r, 'url', url), ctype


async def _aget(url, **kw):
    return await asyncio.to_thread(_http, url, **kw)


async def _json(url, **kw):
    status, text, _u, _c = await _aget(url, **kw)
    if status != 200:
        raise RuntimeError(f'HTTP {status}')
    return json.loads(text)


def _clean(s):
    return _html.unescape(re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', s or ''))).strip()


async def _rates(base):
    try:
        d = await _json(f'https://open.er-api.com/v6/latest/{base}')
        if d.get('result') == 'success':
            return d['rates'], 'open.er-api.com'
    except Exception:
        pass
    d = await _json('https://www.cbr-xml-daily.ru/daily_json.js')
    r = {'RUB': 1.0}
    for code, v in d['Valute'].items():
        r[code] = v['Nominal'] / v['Value']
    if base not in r:
        raise RuntimeError(f'нет валюты {base}')
    k = r[base]
    return {c: x / k for c, x in r.items()}, 'ЦБ РФ'


NAMES = [  # русские названия → ISO (порядок важен: «белорусский рубль» раньше «рубль»)
    (r'бел\w*\s*руб|бун|зайч|byr', 'BYN'), (r'руб|₽|rur', 'RUB'), (r'доллар|бакс|\$|usd', 'USD'), (r'евро|€|eur', 'EUR'),
    (r'гривн|uah|₴', 'UAH'), (r'тенге|kzt|₸', 'KZT'), (r'юан|cny|rmb', 'CNY'), (r'фунт|gbp|£', 'GBP'), (r'лир|try', 'TRY'),
    (r'иен|йен|jpy', 'JPY'), (r'злот|pln', 'PLN'), (r'драм|amd', 'AMD'), (r'лари|gel', 'GEL'), (r'сум|uzs', 'UZS'),
    (r'сом|kgs', 'KGS'), (r'дирх|aed', 'AED'), (r'франк|chf', 'CHF'), (r'вон|krw', 'KRW'),
]


def _code(v, default=''):
    s = str(v or '').strip()
    if re.fullmatch(r'[A-Za-z]{3}', s):
        return s.upper()
    low = s.lower()
    for pat, code in NAMES:
        if re.search(pat, low):
            return code
    return s.upper() or default


async def run(base: str = 'USD', target: str = '', amount: float = 1) -> str:
    base = _code(base, 'USD')
    target = _code(target)
    if isinstance(amount, str):
        amount = re.sub(r'[^\d.,]', '', amount).replace(',', '.') or 1
    try:
        amount = float(amount or 1)
        rates, src = await _rates(base)
    except Exception as e:
        return f"Ошибка получения курса: {e}"
    if target:
        if target not in rates:
            return f"Нет валюты {target}."
        return f"{amount:g} {base} = {amount * rates[target]:,.4f} {target} (курс 1 {base} = {rates[target]:.6g} {target}; {src})".replace(',', ' ')
    pick = [c for c in ('USD', 'EUR', 'RUB', 'GBP', 'CNY', 'KZT', 'UAH', 'TRY', 'JPY') if c in rates and c != base]
    return f"{amount:g} {base} ({src}): " + '; '.join(f"{amount * rates[c]:,.2f} {c}".replace(',', ' ') for c in pick)
