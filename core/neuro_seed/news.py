"""
name: news
category: web
title: Новости
description: свежие новости по теме (Google News RSS); без query — главные новости; hours не нужен
args: query?: string, max_results?: int
example: {"query": "Telegram", "max_results": 6}
keywords: новости, новость, что нового, что происходит, последние события, свежие
limit: 3500
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

import xml.etree.ElementTree as ET


async def run(query: str = '', max_results: int = 6) -> str:
    n = max(1, min(int(max_results or 6), 12))
    base = 'https://news.google.com/rss/search?' + urllib.parse.urlencode({'q': query, 'hl': 'ru', 'gl': 'RU', 'ceid': 'RU:ru'}) if (query or '').strip() \
        else 'https://news.google.com/rss?hl=ru&gl=RU&ceid=RU:ru'
    try:
        status, text, _u, _c = await _aget(base)
        if status != 200:
            return f"Лента новостей ответила HTTP {status}."
        root = ET.fromstring(text)
    except Exception as e:
        return f"Ошибка получения новостей: {e}"
    out = []
    for it in root.iter('item'):
        title = _clean(it.findtext('title'))
        src = _clean(it.findtext('source'))
        date = (it.findtext('pubDate') or '')[:22]
        out.append(f"• {title}" + (f" ({src}, {date})" if src else f" ({date})") + f"\n  {(it.findtext('link') or '').strip()}")
        if len(out) >= n:
            break
    return '\n'.join(out) or "Новостей не найдено."
