"""
name: translate
category: web
title: Перевод
description: перевод текста (MyMemory, до ~500 символов за раз); язык определяется сам: русский → английский, иначе → русский; to/from_lang — коды (en, ru, de, fr, es…)
args: text: string, to?: string, from_lang?: string
example: {"text": "Hello world", "to": "ru"}
keywords: переведи, перевод, как будет по-английски, как по-русски, translate
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


async def run(text: str = '', to: str = '', from_lang: str = '') -> str:
    text = (text or '').strip()
    if not text:
        return "Ошибка: нет текста"
    cyr = bool(re.search('[А-Яа-яЁё]', text))
    src = (from_lang or ('ru' if cyr else 'en')).lower()
    dst = (to or ('en' if cyr else 'ru')).lower()
    try:
        d = await _json('https://api.mymemory.translated.net/get?' + urllib.parse.urlencode({'q': text[:500], 'langpair': f'{src}|{dst}'}))
    except Exception as e:
        return f"Ошибка перевода: {e}"
    out = _html.unescape((d.get('responseData') or {}).get('translatedText') or '')
    if not out or 'MYMEMORY WARNING' in out.upper():
        return f"Перевод недоступен: {d.get('responseDetails') or 'пустой ответ'}"
    return f"{out}" + (' (текст обрезан до 500 символов)' if len(text) > 500 else '')
