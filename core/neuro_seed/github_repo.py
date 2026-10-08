"""
name: github_repo
category: web
title: Репозиторий GitHub
description: сведения о репозитории: описание, звёзды, язык, последний релиз, дата обновления; repo — owner/name
args: repo: string
example: {"repo": "LonamiWebs/Telethon"}
keywords: github, гитхаб, репозитор, репо, исходники
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


async def run(repo: str = '') -> str:
    repo = (repo or '').strip().strip('/')
    repo = re.sub(r'^https?://github\.com/', '', repo)
    if not re.fullmatch(r'[\w.\-]+/[\w.\-]+', repo):
        return "Ошибка: repo должен быть вида owner/name"
    try:
        d = await _json(f'https://api.github.com/repos/{repo}', headers={'Accept': 'application/vnd.github+json'})
    except Exception as e:
        return f"Ошибка GitHub API: {e}"
    rel = ''
    try:
        r = await _json(f'https://api.github.com/repos/{repo}/releases/latest', headers={'Accept': 'application/vnd.github+json'})
        rel = f"\nПоследний релиз: {r.get('tag_name')} ({(r.get('published_at') or '')[:10]})"
    except Exception:
        pass
    lic = (d.get('license') or {}).get('spdx_id') or '—'
    return (f"{d.get('full_name')} — {d.get('description') or 'без описания'}\n{d.get('html_url')}\n"
            f"★ {d.get('stargazers_count')} · форков {d.get('forks_count')} · задач {d.get('open_issues_count')} · язык {d.get('language') or '—'} · лицензия {lic}\n"
            f"Обновлён: {(d.get('pushed_at') or '')[:10]}" + rel)
