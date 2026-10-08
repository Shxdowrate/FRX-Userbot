"""
name: weather
category: web
title: Погода
description: текущая погода и прогноз по городу (Open-Meteo, без ключа); days — на сколько дней прогноз (1–7)
args: city: string, days?: int
example: {"city": "Чита", "days": 3}
keywords: погода, погоду, прогноз, температура на улице, дождь, снег, градус
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


_WMO = {0: 'ясно', 1: 'в основном ясно', 2: 'переменная облачность', 3: 'пасмурно', 45: 'туман', 48: 'изморозь', 51: 'слабая морось', 53: 'морось', 55: 'сильная морось',
        56: 'ледяная морось', 57: 'ледяная морось', 61: 'небольшой дождь', 63: 'дождь', 65: 'сильный дождь', 66: 'ледяной дождь', 67: 'ледяной дождь', 71: 'небольшой снег',
        73: 'снег', 75: 'сильный снег', 77: 'снежная крупа', 80: 'ливень', 81: 'сильный ливень', 82: 'очень сильный ливень', 85: 'снегопад', 86: 'сильный снегопад',
        95: 'гроза', 96: 'гроза с градом', 99: 'сильная гроза с градом'}


async def run(city: str = '', days: int = 1) -> str:
    city = (city or '').strip()
    if not city:
        return "Ошибка: не передан город"
    days = max(1, min(int(days or 1), 7))
    try:
        g = await _json('https://geocoding-api.open-meteo.com/v1/search?' + urllib.parse.urlencode({'name': city, 'count': 1, 'language': 'ru', 'format': 'json'}))
        if not g.get('results'):
            return f"Город «{city}» не найден."
        c = g['results'][0]
        d = await _json('https://api.open-meteo.com/v1/forecast?' + urllib.parse.urlencode({
            'latitude': c['latitude'], 'longitude': c['longitude'], 'timezone': 'auto', 'forecast_days': days, 'wind_speed_unit': 'ms',
            'current': 'temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code,precipitation',
            'daily': 'temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code'}))
    except Exception as e:
        return f"Ошибка запроса погоды: {e}"
    cur = d.get('current', {})
    place = ', '.join(x for x in (c.get('name'), c.get('admin1'), c.get('country')) if x)
    lines = [f"{place}: сейчас {cur.get('temperature_2m')}°C (ощущается {cur.get('apparent_temperature')}°C), {_WMO.get(cur.get('weather_code'), '—')}, "
             f"ветер {cur.get('wind_speed_10m')} м/с, влажность {cur.get('relative_humidity_2m')}%, осадки {cur.get('precipitation')} мм"]
    dl = d.get('daily', {})
    for i, day in enumerate(dl.get('time', [])):
        lines.append(f"{day}: {dl['temperature_2m_min'][i]}…{dl['temperature_2m_max'][i]}°C, {_WMO.get(dl['weather_code'][i], '—')}, осадки {dl['precipitation_sum'][i]} мм")
    return '\n'.join(lines)
