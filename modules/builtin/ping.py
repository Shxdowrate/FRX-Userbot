NAME = "Пинг"
MODULE_NAME = "ping"
VERSION = "2.0"
AUTHOR = "FRX"
DESCRIPTION = "Задержка бота, пинг любого хоста, скорость интернета, check-host из разных стран и данные IP"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/ping.py"

import asyncio
import html
import ipaddress
import os
import socket
import statistics
import time

import aiohttp

import engine
import decorators
import visual

CF = 'https://speed.cloudflare.com'
CH = 'https://check-host.net'
UA = {'User-Agent': 'FRX-Ping/2.0'}


def _esc(x):
    return html.escape(str(x), quote=False)


def flag(cc):
    cc = (cc or '').upper()
    return ''.join(chr(0x1F1E6 + ord(c) - 65) for c in cc) if len(cc) == 2 and cc.isalpha() else '🌐'


# --------------------------------------------------------------------------- задержка бота и хостов

@decorators.command('ping')
async def ping_cmd(event):
    """задержка ответа в мс; [хост[:порт]] — пинг сайта/сервера с этого компьютера (TCP)"""
    target = engine.get_args().strip()
    if target:
        await host_ping(event, target)
        return
    start_time = time.perf_counter()
    await engine.answer(event, visual.wait('Измеряю задержку…'))
    latency_ms = (time.perf_counter() - start_time) * 1000
    await engine.answer(event, visual.title('Пинг') + "\n" + visual.field('задержка', f"{latency_ms:.1f} мс"))


@decorators.command('dping')
async def ping2_cmd(event):
    """показывает задержку с детальной статистикой"""
    start_time = time.perf_counter()
    await engine.answer(event, visual.wait('Тестирую задержку…'))
    send_time = time.perf_counter()
    await engine.answer(event, visual.wait('Редактирование…'))
    edit_time = time.perf_counter()
    await engine.send_message(event.chat_id, visual.ok('Ответ получен'))
    reply_time = time.perf_counter()
    result = "\n".join([
        visual.title('Пинг', 'детальная статистика'),
        visual.field('отправка', f"{(send_time - start_time) * 1000:.1f} мс"),
        visual.field('редактирование', f"{(edit_time - send_time) * 1000:.1f} мс"),
        visual.field('ответ', f"{(reply_time - edit_time) * 1000:.1f} мс"),
        "",
        visual.field('всего', f"{(reply_time - start_time) * 1000:.1f} мс"),
    ])
    await engine.answer(event, result)


def _split_host(target):
    t = target.strip()
    for p in ('https://', 'http://'):
        if t.lower().startswith(p):
            t = t[len(p):]
            port = 443 if p == 'https://' else 80
            break
    else:
        port = None
    t = t.split('/', 1)[0]
    if t.startswith('[') and ']' in t:                  # [ipv6]:port
        host, _, rest = t[1:].partition(']')
        return host, int(rest[1:]) if rest.startswith(':') and rest[1:].isdigit() else (port or 443)
    if t.count(':') == 1:
        host, p = t.split(':')
        return host, int(p) if p.isdigit() else (port or 443)
    return t, port or 443


async def host_ping(event, target):
    host, port = _split_host(target)
    await engine.answer(event, visual.wait(f"Пингую {_esc(host)}:{port}…"))
    loop = asyncio.get_running_loop()
    try:
        t0 = time.perf_counter()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        dns_ms = (time.perf_counter() - t0) * 1000
        ip = infos[0][4][0]
    except Exception as e:
        await engine.answer(event, visual.err(f"Не нашёл адрес {_esc(host)}: {_esc(e)}"))
        return
    times, errors = [], []
    for _ in range(5):
        t0 = time.perf_counter()
        try:
            r, w = await asyncio.wait_for(asyncio.open_connection(ip, port), timeout=5)
            times.append((time.perf_counter() - t0) * 1000)
            w.close()
        except Exception as e:
            errors.append(type(e).__name__)
        await asyncio.sleep(0.3)
    lines = [visual.title('Пинг', sub=f"{_esc(host)}:{port}"), visual.field('адрес', ip), visual.field('DNS', f"{dns_ms:.0f} мс")]
    if times:
        lines += [visual.field('мин / сред / макс', f"{min(times):.0f} / {statistics.mean(times):.0f} / {max(times):.0f} мс"),
                  visual.field('разброс', f"{statistics.pstdev(times):.1f} мс")]
    lines.append(visual.field('ответов', f"{len(times)} из 5"))
    if errors:
        lines.append(visual.warn("не ответил: " + ", ".join(sorted(set(errors)))))
    lines.append(visual.hint("TCP-подключение с компьютера, где запущен FRX · из разных стран — .ch " + _esc(host)))
    await engine.answer(event, "\n".join(lines))


# --------------------------------------------------------------------------- скорость интернета

async def _cf_ping(s, n=6):
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        async with s.get(f"{CF}/__down?bytes=0") as r:
            await r.read()
        out.append((time.perf_counter() - t0) * 1000)
    return out


async def _download(s, seconds, streams, progress):
    total = [0]
    stop = time.perf_counter() + seconds

    async def one():
        while time.perf_counter() < stop:
            async with s.get(f"{CF}/__down?bytes=25000000") as r:
                async for chunk in r.content.iter_chunked(65536):
                    total[0] += len(chunk)
                    if time.perf_counter() >= stop:
                        break
    t0 = time.perf_counter()
    tasks = [asyncio.ensure_future(one()) for _ in range(streams)]
    while not all(t.done() for t in tasks):
        await asyncio.sleep(1)
        await progress('загрузку', total[0] * 8 / max(0.001, time.perf_counter() - t0) / 1e6)
    for t in tasks:
        if t.exception():
            raise t.exception()
    return total[0] * 8 / (time.perf_counter() - t0) / 1e6


async def _upload(s, seconds, streams, progress):
    total = [0]
    stop = time.perf_counter() + seconds
    blob = os.urandom(4_000_000)

    async def body():                                    # отдаём кусками — так видно, сколько уже ушло
        for i in range(0, len(blob), 262144):
            chunk = blob[i:i + 262144]
            total[0] += len(chunk)
            yield chunk

    async def one():
        while time.perf_counter() < stop:
            async with s.post(f"{CF}/__up", data=body(), headers={'Content-Type': 'application/octet-stream'}) as r:
                await r.read()
    t0 = time.perf_counter()
    tasks = [asyncio.ensure_future(one()) for _ in range(streams)]
    while not all(t.done() for t in tasks):
        await asyncio.sleep(1)
        await progress('отдачу', total[0] * 8 / max(0.001, time.perf_counter() - t0) / 1e6)
    for t in tasks:
        if t.exception():
            raise t.exception()
    return total[0] * 8 / (time.perf_counter() - t0) / 1e6


def _mbps(v):
    return f"{v:.0f} Мбит/с" if v >= 100 else f"{v:.1f} Мбит/с"


@decorators.command('speedtest', 'st')
async def speedtest_cmd(event):
    """скорость интернета компьютера с FRX: пинг, загрузка, отдача (через Cloudflare)"""
    state = {'last': 0, 'text': None, 'msg': None}

    async def progress(stage, mbps):
        # правим одно и то же сообщение и только если текст поменялся: engine.answer при неудачной правке
        # (например, «текст не изменился») отправляет новое сообщение — отсюда был спам
        text = visual.wait(f"Измеряю {stage}… {_mbps(mbps)}")
        if time.time() - state['last'] < 2 or text == state['text'] or state['msg'] is None:
            return
        state['last'], state['text'] = time.time(), text
        try:
            await state['msg'].edit(text, parse_mode='html')
        except Exception:
            pass

    state['msg'] = await engine.answer(event, visual.wait("Измеряю пинг…"))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90), headers=UA) as s:
            meta = {}
            try:
                async with s.get(f"{CF}/meta") as r:
                    meta = await r.json(content_type=None)
            except Exception:
                pass
            pings = await _cf_ping(s)
            down = await _download(s, 8, 4, progress)
            up = await _upload(s, 7, 3, progress)
    except Exception as e:
        await engine.answer(event, visual.err(f"Не получилось измерить: {_esc(e)}"))
        return
    ping_ms = statistics.median(pings)
    jitter = statistics.mean(abs(a - b) for a, b in zip(pings, pings[1:])) if len(pings) > 1 else 0
    lines = [visual.title("Скорость интернета", sub="компьютер с FRX"),
             visual.field("⬇ загрузка", _mbps(down)), visual.field("⬆ отдача", _mbps(up)),
             visual.field("пинг", f"{ping_ms:.0f} мс · разброс {jitter:.0f} мс")]
    if meta:
        lines += ["", visual.field("провайдер", _esc(meta.get('asOrganization') or '?'), code=False),
                  visual.field("где", f"{flag(meta.get('country'))} {_esc(meta.get('city') or '')}", code=False),
                  visual.field("сервер", f"Cloudflare {_esc(meta.get('colo') or '')}")]
    try:
        await state['msg'].edit("\n".join(lines), parse_mode='html')
    except Exception:
        await engine.answer(event, "\n".join(lines))


# --------------------------------------------------------------------------- check-host.net

KINDS = {'http': 'HTTP', 'ping': 'пинг', 'tcp': 'TCP', 'dns': 'DNS'}


@decorators.command('checkhost', 'ch')
async def checkhost_cmd(event):
    """доступность сайта/сервера из разных стран (check-host.net): <хост> [http|ping|tcp|dns] [серверов]"""
    parts = engine.get_args().split()
    if not parts:
        await engine.answer(event, visual.err("Что проверить? .ch example.com [http|ping|tcp|dns]"))
        return
    host = parts[0]
    kind = next((p.lower() for p in parts[1:] if p.lower() in KINDS), 'http')
    nodes = next((int(p) for p in parts[1:] if p.isdigit()), 12)
    if kind == 'tcp' and ':' not in host.split('//')[-1]:
        host += ':443'
    if kind in ('ping', 'dns', 'tcp'):
        host = host.split('//')[-1].split('/')[0]
    await engine.answer(event, visual.wait(f"Проверяю {_esc(host)} ({KINDS[kind]})…"))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30),
                                         headers={**UA, 'Accept': 'application/json'}) as s:
            async with s.get(f"{CH}/check-{kind}", params={'host': host, 'max_nodes': min(max(nodes, 3), 40)}) as r:
                job = await r.json(content_type=None)
            rid, info = job['request_id'], job['nodes']
            res = {}
            for _ in range(12):
                await asyncio.sleep(2)
                async with s.get(f"{CH}/check-result/{rid}") as r:
                    res = await r.json(content_type=None) or {}
                if res and all(v is not None for v in res.values()):
                    break
    except Exception as e:
        await engine.answer(event, visual.err(f"check-host.net не ответил: {_esc(e)}"))
        return
    ok_n = 0
    rows = []
    for node, val in sorted(res.items(), key=lambda kv: (info.get(kv[0]) or ['', ''])[1]):
        meta = info.get(node) or ['??', node, '']
        place = f"{flag(meta[0])} {_esc(meta[2] if len(meta) > 2 and meta[2] else meta[1])}"
        status, good = _fmt_result(kind, val)
        ok_n += good
        rows.append(f"{'✓' if good else '✗'} {place} — {status}")
    lines = [visual.title("Check-host", sub=f"{_esc(host)} · {KINDS[kind]}"), visual.field("доступен", f"{ok_n} из {len(rows)}")] \
        + rows + [visual.hint(f"подробно: {CH}/check-report/{rid}")]
    await engine.answer(event, "\n".join(lines))


def _fmt_result(kind, val):
    if val is None:
        return "нет ответа", 0
    try:
        if kind == 'http':
            v = val[0]
            if v and v[0] == 1:
                return f"{v[3]} {_esc(v[2])} · {float(v[1]) * 1000:.0f} мс", 1
            return _esc((v[2] if v and len(v) > 2 else 'ошибка') or 'ошибка'), 0
        if kind == 'ping':
            pings = [p for p in (val[0] or []) if p]
            good = [p[1] * 1000 for p in pings if p[0] == 'OK']
            if good:
                return f"{len(good)}/{len(pings)} · {statistics.mean(good):.0f} мс", 1
            return "не пингуется", 0
        if kind == 'tcp':
            v = val[0]
            if 'time' in v:
                return f"{v['time'] * 1000:.0f} мс · {_esc(v.get('address', ''))}", 1
            return _esc(v.get('error', 'ошибка')), 0
        if kind == 'dns':
            v = val[0]
            a = v.get('A') or []
            aaaa = v.get('AAAA') or []
            if a or aaaa:
                return _esc(", ".join((a + aaaa)[:3])) + f" · TTL {v.get('TTL', '?')}", 1
            return "не резолвится", 0
    except Exception:
        pass
    return "?", 0


# --------------------------------------------------------------------------- IP

@decorators.command('ip', 'айпи')
async def ip_cmd(event):
    """данные IP или домена: страна, город, провайдер, ASN; без аргумента — внешний IP компьютера с FRX"""
    target = engine.get_args().strip()
    ips = []
    if target:
        host = _split_host(target)[0]
        try:
            ipaddress.ip_address(host)
            ips = [host]
        except ValueError:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(host, None)
                ips = list(dict.fromkeys(i[4][0] for i in infos))
            except Exception as e:
                await engine.answer(event, visual.err(f"Не нашёл адрес {_esc(host)}: {_esc(e)}"))
                return
    await engine.answer(event, visual.wait("Смотрю…"))
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15), headers=UA) as s:
            async with s.get(f"https://ipwho.is/{ips[0] if ips else ''}", params={'lang': 'ru'}) as r:
                d = await r.json(content_type=None)
    except Exception as e:
        await engine.answer(event, visual.err(f"Сервис не ответил: {_esc(e)}"))
        return
    if not d or not d.get('success', False):
        await engine.answer(event, visual.err(f"Нет данных: {_esc((d or {}).get('message', '?'))}"))
        return
    conn = d.get('connection') or {}
    tz = d.get('timezone') or {}
    lines = [visual.title("IP", sub=_esc(target) if target else "внешний адрес компьютера с FRX"),
             visual.field("адрес", d.get('ip')),
             visual.field("где", f"{flag(d.get('country_code'))} {_esc(d.get('country') or '')}"
                                 + (f", {_esc(d.get('region'))}" if d.get('region') else '')
                                 + (f", {_esc(d.get('city'))}" if d.get('city') else ''), code=False),
             visual.field("провайдер", _esc(conn.get('isp') or conn.get('org') or '?'), code=False),
             visual.field("ASN", f"AS{conn.get('asn')}" if conn.get('asn') else '?'),
             visual.field("пояс", f"{tz.get('id', '?')} ({tz.get('utc', '')})"),
             visual.field("координаты", f"{d.get('latitude')}, {d.get('longitude')}")]
    if len(ips) > 1:
        lines.append(visual.field("ещё адреса", ", ".join(ips[1:6])))
    lines.append(visual.hint("местоположение по базе адресов — примерное, обычно до города провайдера"))
    await engine.answer(event, "\n".join(lines))
