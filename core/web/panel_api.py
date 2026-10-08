"""
core/web/panel_api.py

Три вещи главной страницы и вкладки «files» сайта:

  README   — README.md проекта: оглавление по разделам, показ одного раздела, поиск. Markdown разбирается на сервере в безопасные узлы
             (заголовки, абзацы, списки, таблицы, код, цитаты, ссылки http/https и якоря) — HTML из README в браузер не попадает.
  Логи     — core/logs.py: последние записи терминала юзербота и стационарного бота с меткой «мм.дд чч:мм:сс».
  Files    — файлы юзербота: реестр (user/data/files, core/files.py), «осиротевшие» файлы в user/data/files без записи и ассеты:
             свои (user/assets/, можно загружать и удалять) и стоковые (assets/ проекта, только просмотр).
             Просмотр (картинки, видео, аудио, текст), скачивание, загрузка, удаление.

Безопасность файлов: пути из запроса не используются «как есть» — реестр ищется по id, ассеты и осиротевшие файлы разрешаются через resolve()
и проверяются на выход за пределы своей папки (в том числе по symlink'ам); отдаются только файлы внутри user/data/, user/assets/ и assets/; HTML/XML никогда не
отдаются как документы (text/plain), SVG — только как картинка в песочнице CSP.
"""

import mimetypes
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import web

from .. import logs
from ..paths import ASSETS_DIR, DATA_DIR, FILES_DIR, USER_ASSETS_DIR

README_PATH = Path('README.md')
UPLOAD_MAX = 50 * 1024 * 1024
TEXT_PREVIEW_BYTES = 64 * 1024
MEDIA_PREVIEW_MAX = 40 * 1024 * 1024
LIST_LIMIT = 2000

IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg', '.ico', '.avif'}
VIDEO_EXT = {'.mp4', '.webm', '.mov', '.m4v', '.ogv'}
AUDIO_EXT = {'.mp3', '.ogg', '.oga', '.opus', '.wav', '.m4a', '.flac', '.aac'}
TEXT_EXT = {'.txt', '.md', '.json', '.py', '.csv', '.log', '.yaml', '.yml', '.ini', '.cfg', '.toml', '.xml', '.html', '.htm', '.js', '.css', '.tsv', '.sql', '.sh', '.bat', '.env', '.rst'}


def register(panel, router):
    from .server import ApiError
    api = PanelApi(panel, ApiError)
    r = router
    r.add_get('/api/readme', panel.api(api.readme))
    r.add_get('/api/readme/section', panel.api(api.readme_section))
    r.add_get('/api/readme/search', panel.api(api.readme_search))
    r.add_get('/api/logs', panel.api(api.logs))
    r.add_post('/api/logs/clear', panel.api(api.logs_clear))
    r.add_get('/api/files', panel.api(api.files))
    r.add_get('/api/files/raw', api.files_raw)
    r.add_post('/api/files/link', panel.api(api.files_link))
    r.add_get('/dl/{token}/{name}', api.files_dl)                   # скачивание обычной ссылкой (см. files_link)
    r.add_get('/api/files/text', panel.api(api.files_text))
    r.add_post('/api/files/upload', panel.api(api.files_upload))
    r.add_post('/api/files/delete', panel.api(api.files_delete))
    r.add_post('/api/files/adopt', panel.api(api.files_adopt))
    return api


# --------------------------------------------------------------------------- #
# Markdown → безопасные узлы
# --------------------------------------------------------------------------- #

_INLINE = re.compile(r'(`+)(.+?)\1|\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|\[([^\]]+)\]\(([^)\s]+)\)')
_ESC = re.compile(r'\\([\\`*_{}\[\]()#+\-.!|<>])')
_TABLE_SEP = re.compile(r'^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$')
_LIST = re.compile(r'^(\s*)([-*+]|\d+[.)])\s+(.*)$')
_HEAD = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_HR = re.compile(r'^\s*([-*_])(\s*\1){2,}\s*$')
_FENCE_OPEN = re.compile(r'^\s*(`{3,}|~{3,})([^`]*)$')           # в строке с инфо-строкой не бывает обратных кавычек (иначе это обычный текст)


def _fence_closes(line: str, opener: str) -> bool:
    s = line.strip()
    return bool(s) and set(s) == {opener[0]} and len(s) >= len(opener)


def slug(title: str) -> str:
    """Якорь как на GitHub: нижний регистр, без знаков препинания, пробелы → «-»."""
    return re.sub(r'[^\w\- ]', '', title.lower(), flags=re.UNICODE).replace(' ', '-')


def parse_inline(text: str, anchors: dict) -> list:
    out, pos = [], 0

    def push(s):
        s = _ESC.sub(r'\1', s)
        if s:
            out.append(s)
    for m in _INLINE.finditer(text):
        push(text[pos:m.start()])
        pos = m.end()
        if m.group(1):
            out.append({'t': 'code', 'c': [m.group(2).strip('`') if m.group(2) else '']})
        elif m.group(3):
            out.append({'t': 'b', 'c': parse_inline(m.group(3), anchors)})
        elif m.group(4):
            out.append({'t': 'i', 'c': parse_inline(m.group(4), anchors)})
        else:
            label, url = m.group(5), m.group(6)
            if re.match(r'^https?://', url, re.I):
                out.append({'t': 'a', 'href': url, 'c': parse_inline(label, anchors)})
            elif url.startswith('#') and url[1:] in anchors:
                out.append({'t': 'a', 'jump': anchors[url[1:]], 'c': parse_inline(label, anchors)})
            else:
                out.extend(parse_inline(label, anchors))
    push(text[pos:])
    return out


def parse_blocks(lines: list, anchors: dict) -> list:
    blocks, i, n = [], 0, len(lines)
    para = []

    def flush():
        if para:
            blocks.append({'t': 'p', 'c': parse_inline(' '.join(x.strip() for x in para), anchors)})
            para.clear()
    while i < n:
        line = lines[i]
        fm = _FENCE_OPEN.match(line)
        if fm:
            flush()
            opener, lang = fm.group(1), fm.group(2).strip()
            body = []
            i += 1
            while i < n and not _fence_closes(lines[i], opener):
                body.append(lines[i])
                i += 1
            blocks.append({'t': 'code', 'lang': lang[:20], 'text': '\n'.join(body)[:60000]})
            i += 1
            continue
        if not line.strip():
            flush(); i += 1; continue
        m = _HEAD.match(line)
        if m:
            flush()
            blocks.append({'t': 'h', 'level': len(m.group(1)), 'c': parse_inline(m.group(2), anchors), 'id': slug(m.group(2))})
            i += 1; continue
        if _HR.match(line) and not _LIST.match(line):
            flush(); blocks.append({'t': 'hr'}); i += 1; continue
        if '|' in line and i + 1 < n and _TABLE_SEP.match(lines[i + 1]):
            flush()
            cells = lambda s: [c.strip() for c in s.strip().strip('|').split('|')]       # noqa: E731
            head = cells(line)
            i += 2
            rows = []
            while i < n and '|' in lines[i] and lines[i].strip():
                rows.append(cells(lines[i])); i += 1
            blocks.append({'t': 'table', 'head': [parse_inline(c, anchors) for c in head],
                           'rows': [[parse_inline(c, anchors) for c in r] for r in rows[:500]]})
            continue
        m = _LIST.match(line)
        if m:
            flush()
            items = []
            while i < n:
                m = _LIST.match(lines[i])
                if m:
                    items.append({'level': min(len(m.group(1).replace('\t', '    ')) // 2, 4), 'ordered': m.group(2)[0].isdigit(), 'text': m.group(3)})
                    i += 1
                elif lines[i].strip() and lines[i].startswith((' ', '\t')) and items and not _HEAD.match(lines[i]):
                    items[-1]['text'] += ' ' + lines[i].strip(); i += 1
                else:
                    break
            blocks.append({'t': 'list', 'items': [{'level': it['level'], 'ordered': it['ordered'], 'c': parse_inline(it['text'], anchors)} for it in items[:400]]})
            continue
        if line.lstrip().startswith('>'):
            flush()
            q = []
            while i < n and lines[i].lstrip().startswith('>'):
                q.append(lines[i].lstrip()[1:].strip()); i += 1
            blocks.append({'t': 'quote', 'c': parse_inline(' '.join(q), anchors)})
            continue
        if re.match(r'^\s*<[^>]+>\s*$', line) or re.match(r'^\s*<!--.*-->\s*$', line):
            flush(); i += 1; continue                      # сырой HTML из README не показываем
        para.append(line)
        i += 1
    flush()
    return blocks


class _Readme:
    def __init__(self):
        self.key = None
        self.sections = []                  # [{'i', 'title', 'lines'}]
        self.anchors = {}
        self.cache = {}

    def load(self):
        try:
            st = README_PATH.stat()
        except OSError:
            self.sections, self.anchors, self.key = [], {}, None
            return self
        key = (st.st_mtime_ns, st.st_size)
        if key == self.key:
            return self
        text = README_PATH.read_text(encoding='utf-8', errors='replace')
        lines = text.split('\n')
        sections, cur, opener = [{'title': 'Начало', 'lines': []}], 0, None
        for line in lines:
            if opener is None:
                fm = _FENCE_OPEN.match(line)
                if fm:
                    opener = fm.group(1)
            elif _fence_closes(line, opener):
                opener = None
            m = re.match(r'^##\s+(.*?)\s*$', line) if opener is None else None
            if m:
                sections.append({'title': m.group(1), 'lines': [line]})
                cur = len(sections) - 1
            else:
                sections[cur]['lines'].append(line)
        self.anchors = {}
        for i, s in enumerate(sections):
            s['i'] = i
            self.anchors.setdefault(slug(s['title']), i)
        self.sections, self.key, self.cache = sections, key, {}
        return self


# --------------------------------------------------------------------------- #

def _kind_of(name: str, mime: str = '') -> str:
    ext = Path(name).suffix.lower()
    mime = (mime or mimetypes.guess_type(name)[0] or '').lower()
    if ext in IMAGE_EXT or mime.startswith('image/'):
        return 'image'
    if ext in VIDEO_EXT or mime.startswith('video/'):
        return 'video'
    if ext in AUDIO_EXT or mime.startswith('audio/'):
        return 'audio'
    if ext in TEXT_EXT or mime.startswith('text/') or mime in ('application/json', 'application/xml'):
        return 'text'
    return 'none'


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _split_assets() -> bool:
    """Свои ассеты лежат отдельно от стоковых (user/assets/ и assets/); в старой раскладке это одна папка."""
    try:
        return USER_ASSETS_DIR.resolve() != ASSETS_DIR.resolve()
    except OSError:
        return True


def _servable(p: Path) -> bool:
    return _inside(p, DATA_DIR) or _inside(p, USER_ASSETS_DIR) or _inside(p, ASSETS_DIR)


def _safe_name(name: str) -> str:
    base = Path(str(name or 'file').replace('\\', '/')).name
    base = re.sub(r'[^\w.\- ()]+', '_', base).strip('. ')
    return (base or 'file')[:120]


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    for i in range(2, 1000):
        cand = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not cand.exists():
            return cand
    return path.with_name(f"{path.stem}_{int(time.time())}{path.suffix}")


class PanelApi:
    def __init__(self, panel, api_error):
        self.panel = panel
        self.Err = api_error
        self.readme_cache = _Readme()

    @property
    def core(self):
        return self.panel.core

    # ----------------------------- README ------------------------------- #

    async def readme(self, request):
        rd = self.readme_cache.load()
        if not rd.sections:
            raise self.Err('README.md не найден рядом с юзерботом', 404)
        return {'sections': [{'i': s['i'], 'title': s['title'], 'lines': len(s['lines'])} for s in rd.sections]}

    async def readme_section(self, request):
        rd = self.readme_cache.load()
        try:
            i = int(request.query.get('i', '0'))
        except ValueError:
            i = 0
        if not (0 <= i < len(rd.sections)):
            raise self.Err('Раздел не найден', 404)
        if i not in rd.cache:
            rd.cache[i] = parse_blocks(rd.sections[i]['lines'], rd.anchors)
        return {'i': i, 'title': rd.sections[i]['title'], 'blocks': rd.cache[i], 'prev': i - 1 if i > 0 else None, 'next': i + 1 if i + 1 < len(rd.sections) else None}

    async def readme_search(self, request):
        q = (request.query.get('q') or '').strip().lower()
        if len(q) < 2:
            return {'hits': []}
        rd = self.readme_cache.load()
        hits = []
        for s in rd.sections:
            count = 0
            first = None
            for line in s['lines']:
                if q in line.lower():
                    count += 1
                    if first is None:
                        j = line.lower().index(q)
                        first = re.sub(r'[`*|]', '', line.strip())[max(0, j - 40): j + 100]
            if count:
                hits.append({'i': s['i'], 'title': s['title'], 'count': count, 'snippet': first or ''})
        hits.sort(key=lambda h: (-(q in h['title'].lower()), -h['count']))
        return {'hits': hits[:50]}

    # ------------------------------ Логи -------------------------------- #

    async def logs(self, request):
        q = request.query
        try:
            after = int(q.get('since', '0'))
        except ValueError:
            after = 0
        kind = q.get('kind', 'all')
        level = q.get('level', 'all')
        items, last = logs.BUFFER.since(after, kind if kind in ('userbot', 'bot') else None, level if level in ('info', 'warn', 'error') else None,
                                        q.get('q'), limit=max(1, min(400, int(q.get('limit', '400') or 400))) if str(q.get('limit', '400')).isdigit() else 400)
        return {'items': items, 'last': last, 'installed': logs._installed}

    async def logs_clear(self, request):
        logs.BUFFER.clear()
        return {'ok': True}

    # ------------------------------ Files ------------------------------- #

    def _registry(self):
        return self.core.file_registry

    async def _registry_items(self):
        reg = self._registry()
        out = []
        for key in await reg.storage.list_keys('core:files:'):
            obj = await reg.storage.get(key)
            if isinstance(obj, dict) and obj.get('id'):
                out.append(obj)
        return out

    def _item(self, src, obj_id, name, path, size=None, mime=None, extra=None):
        p = Path(path)
        exists = p.is_file()
        if size is None and exists:
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
        allowed = exists and _servable(p)
        kind = _kind_of(name, mime or '') if allowed else 'none'
        big = (size or 0) > MEDIA_PREVIEW_MAX and kind in ('video', 'audio', 'image')
        return {'src': src, 'id': obj_id, 'name': name, 'path': str(path).replace('\\', '/'), 'size': size or 0, 'mime': mime or mimetypes.guess_type(name)[0] or '',
                'exists': exists, 'servable': bool(allowed), 'kind': kind, 'preview': kind != 'none' and not big, **(extra or {})}

    async def files(self, request):
        records = await self._registry_items()
        known = set()
        registry = []
        for obj in sorted(records, key=lambda o: str(o.get('name', '')).lower())[:LIST_LIMIT]:
            known.add(str(Path(obj.get('path', '')).resolve()) if obj.get('path') else '')
            registry.append(self._item('registry', obj['id'], obj.get('name') or Path(obj.get('path', '')).name, obj.get('path', ''), obj.get('size'), obj.get('type')))
        orphans = []
        if FILES_DIR.is_dir():
            for p in sorted(FILES_DIR.iterdir(), key=lambda x: x.name.lower()):
                if p.is_file() and str(p.resolve()) not in known and len(orphans) < LIST_LIMIT:
                    orphans.append(self._item('orphan', None, p.name, str(p)))
        assets = self._list_assets(USER_ASSETS_DIR, 'assets')
        stock = self._list_assets(ASSETS_DIR, 'stock') if _split_assets() else []
        return {'registry': registry, 'orphans': orphans, 'assets': assets, 'stock': stock,
                'limits': {'upload_mb': UPLOAD_MAX // (1024 * 1024), 'preview_mb': MEDIA_PREVIEW_MAX // (1024 * 1024)}}

    def _list_assets(self, base, src):
        out = []
        if base.is_dir():
            for p in sorted(base.rglob('*'), key=lambda x: str(x).lower()):
                if p.is_file() and not p.is_symlink() and len(out) < LIST_LIMIT:
                    rel = p.relative_to(base).as_posix()
                    out.append(self._item(src, rel, p.name, str(p), extra={'rel': rel, 'readonly': src == 'stock'}))
        return out

    async def _resolve(self, src, ident):
        """(Path, имя) файла из запроса — только внутри user/data/, user/assets/ и assets/. Бросает ApiError."""
        if src == 'registry':
            for obj in await self._registry_items():
                if obj['id'] == ident:
                    p = Path(obj.get('path', ''))
                    name = obj.get('name') or p.name
                    break
            else:
                raise self.Err('Файла нет в реестре', 404)
            if not _servable(p):
                raise self.Err('Файл лежит вне папок user/data/ и assets/ — сайт его не открывает', 403)
        elif src == 'orphan':
            p = FILES_DIR / Path(str(ident or '').replace('\\', '/')).name
            name = p.name
            if not _inside(p, FILES_DIR):
                raise self.Err('Недопустимый путь', 403)
        elif src in ('assets', 'stock'):
            base = USER_ASSETS_DIR if src == 'assets' else ASSETS_DIR
            rel = str(ident or '').replace('\\', '/').strip('/')
            if not rel or any(part in ('', '.', '..') for part in rel.split('/')):
                raise self.Err('Недопустимый путь', 403)
            p = base / rel
            name = p.name
            if not _inside(p, base):
                raise self.Err('Недопустимый путь', 403)
        else:
            raise self.Err('Неизвестный источник файла')
        if p.is_symlink() or not p.is_file():
            raise self.Err('Файл не найден на диске', 404)
        return p, name

    async def files_raw(self, request):
        try:
            p, name = await self._resolve(request.query.get('src', ''), request.query.get('id', ''))
        except self.Err as e:
            return web.json_response({'error': e.message}, status=e.status)
        except Exception as e:                                          # noqa: BLE001
            return web.json_response({'error': f'{type(e).__name__}: {e}'}, status=500)
        dl = request.query.get('dl') == '1'
        ext = p.suffix.lower()
        mime = mimetypes.guess_type(name)[0] or 'application/octet-stream'
        headers = {'Content-Security-Policy': "default-src 'none'; style-src 'unsafe-inline'; sandbox", 'X-Content-Type-Options': 'nosniff'}
        if dl:
            headers['Content-Disposition'] = "attachment; filename*=UTF-8''" + quote(name)
        else:
            headers['Content-Disposition'] = "inline; filename*=UTF-8''" + quote(name)
            if ext in ('.html', '.htm', '.xml', '.xhtml', '.js', '.css') or mime in ('text/html', 'application/xhtml+xml', 'application/xml', 'text/xml'):
                mime = 'text/plain'
        resp = web.FileResponse(p, headers=headers)
        resp.content_type = mime if not mime.startswith('text/') else mime + '; charset=utf-8'
        return resp

    # Скачивание — обычной ссылкой, а не fetch → blob: браузер сразу показывает загрузку с прогрессом (через туннель
    # большой файл идёт не мгновенно), на телефоне и в Telegram Mini App это единственный рабочий способ. Ссылку
    # выдаёт POST /api/files/link (сессия + X-FRX), она живёт DL_TTL секунд и ведёт ровно на один файл; токен в ней
    # сам по себе пропуск — так файл может скачать и загрузчик Telegram, у которого нет cookie сайта.
    DL_TTL = 10 * 60

    async def files_link(self, request):
        b = await self.panel._json(request)
        p, name = await self._resolve(str(b.get('src', '')), str(b.get('id', '')))
        links = self.__dict__.setdefault('_dl_links', {})
        now = time.time()
        for k in [k for k, v in links.items() if v['until'] < now]:
            links.pop(k, None)
        token = secrets.token_urlsafe(24)
        links[token] = {'path': str(p), 'name': name, 'until': now + self.DL_TTL}
        return {'url': f'/dl/{token}/{quote(name)}', 'name': name, 'size': p.stat().st_size}

    async def files_dl(self, request):
        link = self.__dict__.get('_dl_links', {}).get(request.match_info.get('token', ''))
        if not link or link['until'] < time.time():
            return web.Response(status=410, text='Ссылка на файл устарела — нажмите «скачать» на сайте ещё раз.',
                                content_type='text/plain', charset='utf-8')
        p = Path(link['path'])
        if not p.is_file():
            return web.Response(status=404, text='Файла больше нет', content_type='text/plain', charset='utf-8')
        name = link['name']
        headers = {'Content-Disposition': "attachment; filename*=UTF-8''" + quote(name),
                   'Content-Security-Policy': "default-src 'none'; sandbox", 'X-Content-Type-Options': 'nosniff'}
        resp = web.FileResponse(p, headers=headers)
        resp.content_type = 'application/octet-stream'
        return resp

    async def files_text(self, request):
        p, name = await self._resolve(request.query.get('src', ''), request.query.get('id', ''))
        size = p.stat().st_size
        with p.open('rb') as f:
            data = f.read(TEXT_PREVIEW_BYTES)
        head = data[:2048]
        if b'\x00' in head or (head and sum(1 for c in head if c < 9 or 13 < c < 32) / len(head) > 0.1):
            raise self.Err('Это бинарный файл — показать как текст нельзя')
        return {'name': name, 'text': data.decode('utf-8', errors='replace'), 'truncated': size > TEXT_PREVIEW_BYTES, 'size': size}

    async def files_upload(self, request):
        q = request.query
        dest = q.get('dest', 'registry')
        if dest not in ('registry', 'assets'):
            raise self.Err('Куда загружать: registry или assets')
        declared = request.content_length
        if declared is not None and declared > UPLOAD_MAX:
            raise self.Err(f'Файл больше {UPLOAD_MAX // (1024 * 1024)} МБ', 413)
        data = await request.read()
        if not data:
            raise self.Err('Файл пустой')
        if len(data) > UPLOAD_MAX:
            raise self.Err(f'Файл больше {UPLOAD_MAX // (1024 * 1024)} МБ', 413)
        name = _safe_name(q.get('name', 'file'))
        if dest == 'registry':
            FILES_DIR.mkdir(parents=True, exist_ok=True)
            path = _unique(FILES_DIR / name)
            path.write_bytes(data)
            obj = await self._registry().register(str(path).replace('\\', '/'), path.name, mimetypes.guess_type(name)[0] or 'application/octet-stream', len(data))
            self.core.tlog(f"[web] файл загружен в реестр: {path.name} ({len(data)} Б)")
            return {'ok': True, 'id': obj['id'], 'name': path.name}
        sub = str(q.get('dir', '') or '').replace('\\', '/').strip('/')
        parts = [x for x in sub.split('/') if x]
        if any(re.sub(r'[^\w\- ]', '', x) != x or x in ('.', '..') for x in parts):
            raise self.Err('Подпапка: только буквы, цифры, пробел, _ и -')
        USER_ASSETS_DIR.mkdir(parents=True, exist_ok=True)
        folder = USER_ASSETS_DIR.joinpath(*parts) if parts else USER_ASSETS_DIR
        if not _inside(folder, USER_ASSETS_DIR):
            raise self.Err('Недопустимая папка', 403)
        path = folder / name
        if path.exists() and q.get('overwrite') != '1':
            raise self.Err(f'Файл «{name}» в ассетах уже есть — удалите его или загрузите под другим именем', 409)
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self.core.tlog(f"[web] ассет загружен: {path.relative_to(USER_ASSETS_DIR).as_posix()} ({len(data)} Б)")
        return {'ok': True, 'name': path.name, 'rel': path.relative_to(USER_ASSETS_DIR).as_posix()}

    async def files_delete(self, request):
        b = await self.panel._json(request)
        src, ident = b.get('src'), b.get('id')
        if src == 'registry':
            reg = self._registry()
            for key in await reg.storage.list_keys('core:files:'):
                obj = await reg.storage.get(key)
                if isinstance(obj, dict) and obj.get('id') == str(ident):
                    break
            else:
                raise self.Err('Файла нет в реестре', 404)
            p = Path(obj.get('path', ''))
            if _servable(p):
                await reg.delete(file_id=str(ident))
                self.core.tlog(f"[web] файл удалён из реестра: {ident}")
                return {'ok': True}
            # запись указывает за пределы папок сайта — файл на диске сайт не трогает, убираем только запись
            await reg.storage.delete(key)
            self.core.tlog(f"[web] запись реестра {ident} удалена, файл вне user/data/ и assets/ оставлен на месте: {p}")
            return {'ok': True, 'kept_file': True}
        if src == 'stock':
            raise self.Err('Стоковые файлы проекта (assets/) с сайта не удаляются — они придут снова со следующим обновлением', 403)
        p, name = await self._resolve(src, ident)
        p.unlink()
        if src == 'assets':
            parent = p.parent
            while parent != USER_ASSETS_DIR and _inside(parent, USER_ASSETS_DIR) and parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
                parent = parent.parent
        self.core.tlog(f"[web] файл удалён: {name}")
        return {'ok': True}

    async def files_adopt(self, request):
        b = await self.panel._json(request)
        p, name = await self._resolve('orphan', b.get('id'))
        obj = await self._registry().register(str(p).replace('\\', '/'), name, mimetypes.guess_type(name)[0] or 'application/octet-stream', p.stat().st_size)
        return {'ok': True, 'id': obj['id']}
