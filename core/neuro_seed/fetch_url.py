"""
name: fetch_url
category: web
title: Открыть страницу
description: скачивает страницу по URL и возвращает основной текст без HTML (статьи, документация, JSON, обычный текст); max_chars — сколько символов вернуть (по умолчанию 5000)
args: url: string, max_chars?: int
example: {"url": "https://example.com/article", "max_chars": 4000}
limit: 8000
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


_DROP = r'<(script|style|noscript|svg|nav|header|footer|aside|form|iframe|template)\b.*?</\1>'


def _main_text(page):
    title = _clean((re.search(r'<title[^>]*>(.*?)</title>', page, re.S | re.I) or [None, ''])[1])
    body = re.sub(r'<!--.*?-->', ' ', page, flags=re.S)
    body = re.sub(_DROP, ' ', body, flags=re.S | re.I)
    art = re.search(r'<(article|main)\b[^>]*>(.*?)</\1>', body, re.S | re.I)
    if art and len(_clean(art.group(2))) > 400:
        body = art.group(2)
    body = re.sub(r'<(br|/p|/div|/li|/tr|/h[1-6]|/section|/blockquote|/pre)\b[^>]*>', '\n', body, flags=re.I)
    body = re.sub(r'<li\b[^>]*>', '• ', body, flags=re.I)
    text = _html.unescape(re.sub(r'<[^>]+>', ' ', body))
    lines = [re.sub(r'[ \t ]+', ' ', ln).strip() for ln in text.split('\n')]
    out, blank = [], 0
    for ln in lines:
        if ln:
            out.append(ln); blank = 0
        elif not blank:
            out.append(''); blank = 1
    return title, '\n'.join(out).strip()


async def run(url: str = '', max_chars: int = 5000) -> str:
    url = (url or '').strip()
    if not re.match(r'^https?://', url):
        return "Ошибка: нужен адрес вида https://…"
    try:
        status, page, final, ctype = await _aget(url)
    except Exception as e:
        return f"Ошибка загрузки {url}: {e}"
    if status >= 400:
        return f"Сайт ответил HTTP {status} ({url}). Возможно, он закрыт для ботов — попробуйте другой источник."
    limit = max(500, min(int(max_chars or 5000), 7500))
    if 'html' not in ctype.lower() and not page.lstrip().lower().startswith(('<!doctype', '<html')):
        if 'pdf' in ctype.lower():
            return "Это PDF — извлекать текст из PDF инструмент не умеет."
        return page[:limit] + (f"\n…(обрезано, всего {len(page)} симв.)" if len(page) > limit else '')
    title, text = _main_text(page)
    if not text:
        return f"Страница пустая или строится скриптами (нужен браузер): {final}"
    head = (f"{title}\n" if title else '') + f"{final}\n\n"
    return head + text[:limit] + (f"\n…(обрезано, всего {len(text)} симв.)" if len(text) > limit else '')
