"""
name: web_search
category: web
title: Поиск в интернете
description: поиск в интернете без ключа: пробует DuckDuckGo, Bing, DuckDuckGo Lite, потом Википедию; возвращает заголовки, ссылки и описания. Чтобы прочитать найденную страницу — fetch_url
args: query: string, max_results?: int
example: {"query": "курс доллара сегодня", "max_results": 5}
keywords: найди в интернете, поищи, загугли, погугли, гугл, что такое, кто такой, последние новости
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


class _Blocked(Exception):
    pass


def _uddg(href):
    href = _html.unescape(href or '')
    if href.startswith('//'):
        href = 'https:' + href
    q = urllib.parse.urlsplit(href)
    if 'duckduckgo.com' in (q.hostname or '') and q.path.startswith('/l/'):
        v = urllib.parse.parse_qs(q.query).get('uddg')
        if v:
            return v[0]
    return href


def _bing_url(href):
    href = _html.unescape(href or '')
    q = urllib.parse.urlsplit(href)
    if (q.hostname or '').endswith('bing.com') and q.path.startswith('/ck/'):
        u = (urllib.parse.parse_qs(q.query).get('u') or [''])[0]
        if u.startswith('a1'):
            import base64
            try:
                s = u[2:]
                return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4)).decode('utf-8', 'replace')
            except Exception:
                return href
    return href


def _attr(tag, name):
    m = re.search(r'\b' + name + r'\s*=\s*("([^"]*)"|\'([^\']*)\')', tag)
    return (m.group(2) if m.group(2) is not None else m.group(3)) if m else ''


def parse_ddg(page):
    res = []
    anchors = list(re.finditer(r'<a\b[^>]*class\s*=\s*["\'][^"\']*result__a[^"\']*["\'][^>]*>(.*?)</a>', page, re.S))
    for i, m in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(page)
        url = _uddg(_attr(m.group(0), 'href'))
        sn = re.search(r'class\s*=\s*["\'][^"\']*result__snippet[^"\']*["\'][^>]*>(.*?)</(?:a|td|div)>', page[m.end():end], re.S)
        res.append((_clean(m.group(1)), url, _clean(sn.group(1)) if sn else ''))
    return res


def parse_lite(page):
    res = []
    anchors = list(re.finditer(r'<a\b[^>]*class\s*=\s*["\'][^"\']*result-link[^"\']*["\'][^>]*>(.*?)</a>', page, re.S))
    for i, m in enumerate(anchors):
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(page)
        sn = re.search(r'class\s*=\s*["\'][^"\']*result-snippet[^"\']*["\'][^>]*>(.*?)</td>', page[m.end():end], re.S)
        res.append((_clean(m.group(1)), _uddg(_attr(m.group(0), 'href')), _clean(sn.group(1)) if sn else ''))
    return res


def parse_bing(page):
    res = []
    for blk in re.findall(r'<li\b[^>]*class\s*=\s*["\'][^"\']*b_algo[^"\']*["\'][^>]*>(.*?)</li>', page, re.S):
        a = re.search(r'<h2[^>]*>\s*(<a\b[^>]*>)(.*?)</a>', blk, re.S)
        if not a:
            continue
        sn = re.search(r'<p\b[^>]*>(.*?)</p>', blk, re.S) or re.search(r'class\s*=\s*["\'][^"\']*b_lineclamp[^"\']*["\'][^>]*>(.*?)</', blk, re.S)
        res.append((_clean(a.group(2)), _bing_url(_attr(a.group(1), 'href')), _clean(sn.group(1)) if sn else ''))
    return res


def _is_block(status, page):
    low = page[:6000].lower()
    return status in (202, 403, 429) or 'anomaly' in low or 'captcha' in low or 'unusual traffic' in low


async def _ddg(q):
    status, page, _u, _c = await _aget('https://html.duckduckgo.com/html/?' + urllib.parse.urlencode({'q': q, 'kl': 'ru-ru'}))
    res = parse_ddg(page) if status == 200 else []
    if not res and _is_block(status, page):
        raise _Blocked(f'HTTP {status}, похоже на защиту от ботов')
    if not res:
        status, page, _u, _c = await _aget('https://html.duckduckgo.com/html/', data=urllib.parse.urlencode({'q': q, 'kl': 'ru-ru'}).encode(),
                                            headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': 'https://html.duckduckgo.com/'})
        res = parse_ddg(page) if status == 200 else []
        if not res and _is_block(status, page):
            raise _Blocked(f'HTTP {status}, похоже на защиту от ботов')
    return res


async def _bing(q):
    status, page, _u, _c = await _aget('https://www.bing.com/search?' + urllib.parse.urlencode({'q': q, 'setlang': 'ru', 'cc': 'ru'}))
    res = parse_bing(page) if status == 200 else []
    if not res and _is_block(status, page):
        raise _Blocked(f'HTTP {status}')
    return res


async def _lite(q):
    status, page, _u, _c = await _aget('https://lite.duckduckgo.com/lite/', data=urllib.parse.urlencode({'q': q, 'kl': 'ru-ru'}).encode(),
                                        headers={'Content-Type': 'application/x-www-form-urlencoded', 'Referer': 'https://lite.duckduckgo.com/'})
    res = parse_lite(page) if status == 200 else []
    if not res and _is_block(status, page):
        raise _Blocked(f'HTTP {status}')
    return res


async def _wiki(q):
    lang = 'ru' if re.search('[А-Яа-яЁё]', q) else 'en'
    d = await _json(f'https://{lang}.wikipedia.org/w/api.php?' + urllib.parse.urlencode({'action': 'query', 'list': 'search', 'srsearch': q, 'srlimit': 8, 'format': 'json'}))
    return [(it['title'], f"https://{lang}.wikipedia.org/wiki/" + urllib.parse.quote(it['title'].replace(' ', '_')), _clean(it.get('snippet', '')))
            for it in d.get('query', {}).get('search', [])]


async def run(query: str = '', max_results: int = 5) -> str:
    query = (query or '').strip()
    if not query:
        return "Ошибка: не передан поисковый запрос"
    n = max(1, min(int(max_results or 5), 10))
    problems = []
    for label, fn in (('DuckDuckGo', _ddg), ('Bing', _bing), ('DuckDuckGo Lite', _lite), ('Википедия', _wiki)):
        try:
            res = [r for r in await fn(query) if r[1].startswith('http')]
        except Exception as e:
            problems.append(f'{label}: {e}')
            continue
        if res:
            lines = []
            for i, (t, u, s) in enumerate(res[:n], 1):
                lines.append(f"{i}. {t}\n   {u}" + (f"\n   {s[:220]}" if s else ''))
            note = f"\n(источник: {label}; страницу целиком — fetch_url)" if label != 'DuckDuckGo' else ''
            return "\n".join(lines) + note
        problems.append(f'{label}: пусто')
    return "Поиск не дал результатов. " + '; '.join(problems) + ". Проверьте, что у юзербота есть доступ в интернет."
