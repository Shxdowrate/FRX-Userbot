"""
core/webide.py

Логика FRX WebIDE без привязки к HTTP (HTTP-слой — core/web/webide_api.py):

  - lock_errors(old, new)      — защита шапки модуля: NAME, MODULE_NAME и AUTHOR менять нельзя
                                 (VERSION и DESCRIPTION — можно);
  - apply_stamp(src, ...)      — метка «изменён в FRX WebIDE» в AUTHOR;
  - read_stamp(src)            — разбор клейма (для списка модулей и для самого редактора);
  - History                    — история изменений файла: снимок исходника перед каждым сохранением
                                 (user/data/webide/history/<MODULE_NAME>/<время>.py, хранится HISTORY_KEEP последних);
  - unified_diff(a, b)         — построчное сравнение версий для просмотра в редакторе.

Блокировка сравнивает значения через ast (module_meta.parse_module_meta), а не текст, поэтому обойти её
перестановкой кавычек/пробелов нельзя. Клеймо — обычный комментарий: на работу модуля и его разбор не влияет.
"""

import difflib
import hashlib
import json
import re
import time
from pathlib import Path

from .module_meta import parse_module_meta
from .paths import DATA_DIR

LOCKED_FIELDS = (('name', 'NAME'), ('module_name', 'MODULE_NAME'), ('author', 'AUTHOR'))
HISTORY_DIR = DATA_DIR / 'webide' / 'history'
DRAFTS_DIR = DATA_DIR / 'webide' / 'drafts'          # несохранённые правки редактора и Lab (Drafts)
HISTORY_KEEP = 60

STAMP_PREFIX = '# [FRX WebIDE]'
STAMP_RE = re.compile(r'^# \[FRX WebIDE\] (?P<body>.*)$')


def lock_errors(old_source: str, new_source: str) -> list:
    """
    Список текстов-ошибок, если в new_source изменены NAME / MODULE_NAME / AUTHOR относительно old_source.
    Пустой список — шапка не тронута. Если старый исходник не разбирается (модуль «битый») — защищать нечего.
    """
    old = parse_module_meta(old_source)
    new = parse_module_meta(new_source)
    if old is None:
        return []
    if new is None:
        return ['не удаётся прочитать шапку модуля (MODULE_NAME должен быть строкой)']
    errors = []
    for key, label in LOCKED_FIELDS:
        if old[key] != new[key]:
            errors.append(f"{label} менять нельзя: было «{old[key]}», стало «{new[key]}»")
    return errors


LAB_MARK = '# ===== FRX WebIDE Lab: проект'


def is_lab(source: str) -> bool:
    """Модуль собран в FRX WebIDE Lab (в конце файла лежит проект конструктора)."""
    return LAB_MARK in source


_AUTHOR_RE = re.compile(r'^(?P<pre>AUTHOR\s*=\s*)(?P<q>["\'])(?P<val>(?:(?!(?P=q)).)*)(?P=q)(?P<post>\s*(?:#.*)?)$', re.M)
MARK_EDITED = 'изменён в FRX WebIDE'
MARK_CREATED = 'создан в FRX WebIDE'
MARK_LAB = 'FRX WebIDE Lab'


def read_stamp(source: str):
    """Метка в AUTHOR: {'created': bool, 'edited': bool, 'lab': bool} либо None, если модуль не трогал WebIDE."""
    m = _AUTHOR_RE.search(source)
    if not m:
        return None
    val = m.group('val')
    if 'FRX WebIDE' not in val:
        return None
    return {'created': MARK_CREATED in val, 'edited': MARK_EDITED in val, 'lab': MARK_LAB in val}


def strip_stamp(source: str) -> str:
    """Убирает старое клеймо-комментарий (в ранних сборках оно стояло первой строкой файла)."""
    lines = source.split('\n')
    keep = [ln for i, ln in enumerate(lines) if not (i < 4 and STAMP_RE.match(ln))]
    return '\n'.join(keep)


def apply_stamp(source: str, created: bool = False, now: str = None) -> str:
    """
    метка «изменён в FRX WebIDE» живёт в AUTHOR — `AUTHOR = "имя_автора | изменён в FRX WebIDE"` —
    а не комментарием сверху. Дублей нет: если метка уже есть, она не добавляется второй раз;
    у модулей Lab (`| FRX WebIDE Lab`) AUTHOR не меняется. Старый комментарий-клеймо удаляется.
    """
    source = strip_stamp(source)
    m = _AUTHOR_RE.search(source)
    if not m:
        return source
    val = m.group('val')
    if MARK_LAB in val:
        return source
    if created:
        mark = MARK_CREATED
        if 'FRX WebIDE' in val:
            return source
    else:
        mark = MARK_EDITED
        if MARK_EDITED in val:
            return source
        if MARK_CREATED in val:
            new = val.replace(MARK_CREATED, MARK_EDITED)
            return source[:m.start('val')] + new + source[m.end('val'):]
        if 'FRX WebIDE' in val:
            return source
    new = f'{val} | {mark}' if val.strip() else mark
    return source[:m.start('val')] + new + source[m.end('val'):]


def unified_diff(old: str, new: str, context: int = 3) -> list:
    """Строки diff'а в виде [{'t': ' ' | '+' | '-' | '@', 'text': ...}] — для подсветки в редакторе."""
    out = []
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm='', n=context):
        if line.startswith(('---', '+++')):
            continue
        t = line[:1] if line[:1] in '+- ' else '@'
        out.append({'t': t, 'text': line if t == '@' else line[1:]})
    return out


def changed_lines(old: str, new: str):
    """(добавлено, удалено) — сколько строк различается между версиями."""
    added = removed = 0
    for d in unified_diff(old, new, context=0):
        if d['t'] == '+':
            added += 1
        elif d['t'] == '-':
            removed += 1
    return added, removed


class Drafts:
    """Черновики WebIDE и Lab на стороне юзербота: несохранённые правки переживают смену адреса сайта, перезагрузку
    страницы и переход на другое устройство. Ключ — 'code:<MODULE_NAME>', 'lab:<MODULE_NAME>' или '…:__new__'.
    Файл на ключ; хранится последняя версия, старше DRAFT_DAYS дней — удаляется."""

    MAX_BYTES = 2 * 1024 * 1024
    DRAFT_DAYS = 30

    def __init__(self, root: Path = None):
        self.root = Path(root) if root else DRAFTS_DIR

    @staticmethod
    def _safe(key: str) -> str:
        kind, _, name = str(key or '').partition(':')
        if kind not in ('code', 'lab') or not re.fullmatch(r'[\w.-]{1,80}', name or ''):
            raise ValueError('неверный ключ черновика')
        return f"{kind}__{name}"

    def _path(self, key: str) -> Path:
        return self.root / (self._safe(key) + '.json')

    def get(self, key: str):
        p = self._path(key)
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
        if time.time() - float(d.get('saved', 0)) > self.DRAFT_DAYS * 86400:
            self.drop(key)
            return None
        return d

    def put(self, key: str, data, base: str = '') -> dict:
        body = json.dumps(data, ensure_ascii=False)
        if len(body.encode('utf-8')) > self.MAX_BYTES:
            raise ValueError('черновик слишком большой')
        p = self._path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        rec = {'key': key, 'saved': time.time(), 'base': str(base or '')[:80], 'data': data}
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(rec, ensure_ascii=False), encoding='utf-8')
        tmp.replace(p)
        return {'saved': rec['saved']}

    def drop(self, key: str) -> bool:
        try:
            self._path(key).unlink()
            return True
        except OSError:
            return False


def source_hash(text: str) -> str:
    """Короткий отпечаток исходника — «на какой версии файла сделан черновик»."""
    return hashlib.sha256(str(text or '').encode('utf-8')).hexdigest()[:16]


class History:
    """Снимки исходников модулей. Имя файла = метка времени (UTC-миллисекунды), по ней же сортируем."""

    def __init__(self, root: Path = None, keep: int = HISTORY_KEEP):
        self.root = Path(root) if root else HISTORY_DIR
        self.keep = keep

    def _dir(self, module: str) -> Path:
        return self.root / module

    def add(self, module: str, source: str, note: str = '') -> dict:
        d = self._dir(module)
        d.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        while (d / f'{stamp}.py').exists():
            stamp += 1
        (d / f'{stamp}.py').write_text(source, encoding='utf-8', newline='\n')
        if note:
            (d / f'{stamp}.note').write_text(note[:120], encoding='utf-8')
        self._trim(d)
        return {'id': str(stamp)}

    def _trim(self, d: Path):
        files = sorted(d.glob('*.py'), key=lambda p: int(p.stem))
        for old in files[:-self.keep]:
            old.unlink(missing_ok=True)
            old.with_suffix('.note').unlink(missing_ok=True)

    def list(self, module: str) -> list:
        d = self._dir(module)
        if not d.is_dir():
            return []
        items = []
        for f in sorted(d.glob('*.py'), key=lambda p: int(p.stem), reverse=True):
            note = ''
            nf = f.with_suffix('.note')
            if nf.exists():
                try:
                    note = nf.read_text(encoding='utf-8')
                except OSError:
                    pass
            items.append({'id': f.stem, 'time': int(f.stem) / 1000, 'size': f.stat().st_size, 'note': note})
        return items

    def get(self, module: str, version_id: str):
        if not re.fullmatch(r'\d{10,16}', version_id or ''):
            return None
        f = self._dir(module) / f'{version_id}.py'
        try:
            return f.read_text(encoding='utf-8')
        except OSError:
            return None

    def latest(self, module: str):
        items = self.list(module)
        return self.get(module, items[0]['id']) if items else None
