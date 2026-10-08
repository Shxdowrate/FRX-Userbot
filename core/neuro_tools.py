"""
core/neuro_tools.py

Инструменты нейросети как ФАЙЛЫ-инструкции: user/data/neuro/tools/<категория>/<имя>.py. Файл — обычный Python с заголовком в докстринге и функцией run():

    \"\"\"
    name: calculator
    category: math
    title: Калькулятор
    description: безопасная арифметика и math.* (sqrt, sin, log…)
    args: expression: string
    example: {"expression": "2**10 + sqrt(16)"}
    dangerous: false
    \"\"\"
    async def run(expression: str = '') -> str:
        ...

Категории нужны, чтобы не тратить токены: в запрос идут только описания инструментов тех категорий, что подходят к вопросу (по ключевым словам) или
которые модель сама «открыла» (open_category); остальные категории — одной строкой.

Инструменты добавляются и удаляются: через сайт (neuro → инструменты), командой `.neurotool` (ответом на сообщение с кодом или на .py-файл) и просто
положив файл в папку. Включение/выключение (по инструменту и по категории) хранится в tools_state.json — НЕ в config.json.

Код инструмента выполняется в процессе юзербота с теми же правами, что и модули (можно `import engine, db, cache, files`), поэтому инструмент с
`dangerous: true` перед запуском требует подтверждения (см. настройку allow_dangerous). `heavy: <пояснение>` — «тяжёлый» инструмент (много токенов): перед запуском спрашивает разрешение (настройка allow_heavy). Базовые инструменты NeuroDash 4.0 переехали сюда файлами.
"""

import ast
import asyncio
import inspect
import json
import re
import threading
from pathlib import Path

from .context import current_module
from .paths import DATA_DIR

TOOLS_DIR = DATA_DIR / 'neuro' / 'tools'
MAX_TOOL_BYTES = 40 * 1024
NAME_RE = re.compile(r'^[a-z][a-z0-9_]{1,31}$')
CAT_RE = re.compile(r'^[a-z][a-z0-9_]{1,23}$')
HEADER_KEYS = ('name', 'category', 'title', 'description', 'args', 'example', 'dangerous', 'keywords', 'limit', 'heavy')
OWNER_MODULE = 'neurodash'          # под этим именем инструменты видят db / cache (данные NeuroDash)

DEFAULT_CATEGORIES = {
    'math': {'title': 'Математика', 'description': 'вычисления', 'keywords': 'посчитай, сосчитай, вычисли, сколько будет, умнож, раздели, сложи, вычти, корень, процент, степен'},
    'time': {'title': 'Время', 'description': 'текущие дата и время', 'keywords': 'время, который час, сколько времени, дата, сегодня, сейчас, день недели, часовой пояс'},
    'files': {'title': 'Файлы', 'description': 'чтение и запись файлов в песочнице ai_files', 'keywords': 'файл, запиши в, сохрани в, txt'},
    'memory': {'title': 'Память', 'description': 'заметки, которые переживают перезапуск', 'keywords': 'запомни, помнишь, вспомни, заметк, не забудь, что я говорил'},
    'web': {'title': 'Интернет', 'description': 'открыть страницу по адресу и поиск в сети', 'keywords': 'найди, поищи, поиск, интернет, сайт, http, www., ссылк, страниц, новост, погод, курс'},
    'telegram': {'title': 'Telegram', 'description': 'действия в Telegram от имени аккаунта', 'keywords': 'отправь, напиши пользовател, напиши в чат, сообщение в, telegram, телеграм'},
    'system': {'title': 'Система', 'description': 'сведения о системе и модулях, выполнение Python-кода (опасно)', 'keywords': 'python, питон, выполни код, запусти код, скрипт, eval, система, модул, конфиг, настройк, версия, нагрузка, память, диск'},
    'utils': {'title': 'Утилиты', 'description': 'хеши, пароли, base64, regex, JSON, статистика текста, случайные числа', 'keywords': 'хеш, md5, sha, пароль, base64, regex, регуляр, json, случайн, рандом, количество слов, символов'},
}
DEFAULT_CATEGORIES['system']['keywords'] += ', frx, фрикс, юзербот, справка, документац, что нового, твои команды, команду'
DEFAULT_CATEGORIES['math']['keywords'] += ', единиц, переведи в, километр, миль, градус, статистик, среднее, медиана'
DEFAULT_CATEGORIES['time']['keywords'] += ', через сколько, сколько дней, до дня, прибавь дней, возраст'
DEFAULT_CATEGORIES['files']['keywords'] += ', папк, список файлов, удали файл'
DEFAULT_CATEGORIES['memory']['keywords'] += ', задач, todo, напомни, список дел, забудь'
DEFAULT_CATEGORIES['web']['keywords'] += ', википед, курс валют, доллар, евро, биткоин, bitcoin, крипт, переведи на, github, гитхаб'
DEFAULT_CATEGORIES['telegram']['keywords'] += ', чат, чаты, диалог, переписк, прочитай чат, последние сообщения, найди сообщение, избранное, saved, пользователь, юзер'


class ToolError(Exception):
    pass


def _bool(v):
    return str(v).strip().lower() in ('1', 'true', 'yes', 'да', 'on')


def _int(v):
    try:
        n = int(str(v).strip())
        return n if n > 0 else 0
    except (TypeError, ValueError):
        return 0


def parse_tool(source: str) -> dict:
    """Разбирает и проверяет файл инструмента. Бросает ToolError с понятным текстом."""
    if len(source.encode('utf-8')) > MAX_TOOL_BYTES:
        raise ToolError(f'Файл инструмента больше {MAX_TOOL_BYTES // 1024} КБ')
    try:
        tree = ast.parse(source)
    except SyntaxError as e:
        raise ToolError(f'Синтаксическая ошибка: строка {e.lineno}: {e.msg}')
    doc = ast.get_docstring(tree) or ''
    meta = {}
    for line in doc.split('\n'):
        m = re.match(r'^\s*([a-z_]+)\s*:\s*(.*?)\s*$', line)
        if m and m.group(1) in HEADER_KEYS and m.group(1) not in meta:
            meta[m.group(1)] = m.group(2)
    for key in ('name', 'category', 'description'):
        if not meta.get(key):
            raise ToolError(f"В заголовке (докстринге в начале файла) нет строки «{key}: …»")
    if not NAME_RE.match(meta['name']):
        raise ToolError('name: латиница в нижнем регистре, цифры и _ (2–32 символа, с буквы)')
    if not CAT_RE.match(meta['category']):
        raise ToolError('category: латиница в нижнем регистре, цифры и _ (2–24 символа, с буквы)')
    fn = next((n for n in tree.body if isinstance(n, (ast.AsyncFunctionDef, ast.FunctionDef)) and n.name == 'run'), None)
    if fn is None:
        raise ToolError('В файле нет функции run(...) — именно она вызывается моделью')
    params = [a.arg for a in fn.args.args + fn.args.kwonlyargs]
    return {'name': meta['name'], 'category': meta['category'], 'title': meta.get('title') or meta['name'], 'description': meta['description'],
            'args': meta.get('args') or ', '.join(params), 'example': meta.get('example', ''), 'dangerous': _bool(meta.get('dangerous', 'false')),
            'keywords': meta.get('keywords', ''), 'limit': _int(meta.get('limit')), 'heavy': (meta.get('heavy') or '').strip(), 'params': params, 'async': isinstance(fn, ast.AsyncFunctionDef)}


# --------------------------------------------------------------------------- #
# встроенные инструменты (перенесены из NeuroDash 4.0)
# --------------------------------------------------------------------------- #

SEED = {}

SEED['calculator'] = '''"""
name: calculator
category: math
title: Калькулятор
description: безопасная арифметика и math.* (sqrt, sin, log…), без произвольного кода
args: expression: string
example: {"expression": "2**10 + sqrt(16)"}
"""
import ast
import math

_NAMES = {n: getattr(math, n) for n in dir(math) if not n.startswith('_')}
_NAMES.update({'abs': abs, 'round': round, 'min': min, 'max': max})
_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Load, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
            ast.Mod, ast.Pow, ast.USub, ast.UAdd, ast.Call, ast.Name, ast.Tuple)


def _calc(expr):
    tree = ast.parse(expr, mode='eval')
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise ValueError(f"Недопустимая конструкция: {type(node).__name__}")
        if isinstance(node, ast.Name) and node.id not in _NAMES:
            raise ValueError(f"Недопустимое имя: {node.id}")
        if isinstance(node, ast.Call) and not isinstance(node.func, ast.Name):
            raise ValueError("Недопустимый вызов")
    return eval(compile(tree, '<calc>', 'eval'), {'__builtins__': {}}, _NAMES)


async def run(expression: str = '') -> str:
    if not expression:
        return "Ошибка: не передано выражение"
    try:
        return f"{expression} = {_calc(expression)}"
    except Exception as e:
        return f"Ошибка вычисления: {e}"
'''

SEED['get_current_time'] = '''"""
name: get_current_time
category: time
title: Текущее время
description: текущие дата и время; timezone необязателен, например 'Europe/Moscow' или 'UTC+3'
args: timezone?: string
example: {"timezone": "Europe/Moscow"}
"""
import re
from datetime import datetime, timedelta

_FALLBACK = {'utc': 0, 'europe/moscow': 3, 'msk': 3, 'europe/london': 0, 'europe/paris': 1, 'europe/berlin': 1, 'europe/kyiv': 2,
             'europe/istanbul': 3, 'asia/dubai': 4, 'asia/tokyo': 9, 'asia/shanghai': 8, 'asia/almaty': 6, 'america/new_york': -5,
             'america/los_angeles': -8, 'america/chicago': -6}


def _offset(tz):
    m = re.fullmatch(r'utc\\s*([+-]?\\d{1,2})(?::?(\\d{2}))?', tz.strip(), re.I)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    return h + (1 if h >= 0 else -1) * mi / 60


async def run(timezone: str = '') -> str:
    if not timezone:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(timezone)).strftime('%Y-%m-%d %H:%M:%S %Z').strip()
    except Exception:
        pass
    off = _FALLBACK.get(timezone.strip().lower())
    if off is None:
        off = _offset(timezone)
    if off is None:
        return (f"Не удалось определить часовой пояс '{timezone}' — на сервере, вероятно, нет базы IANA (pip install tzdata). "
                f"Доступны приблизительные: {', '.join(sorted(_FALLBACK))}.")
    now = (datetime.utcnow() + timedelta(hours=off)).strftime('%Y-%m-%d %H:%M:%S')
    return f"{now} (UTC{'+' if off >= 0 else ''}{off:g}, приблизительно, без учёта DST)"
'''

SEED['read_file'] = '''"""
name: read_file
category: files
title: Прочитать файл
description: читает текстовый файл из песочницы ai_files/
args: path: string
"""
from pathlib import Path

from core.paths import DATA_DIR

BASE = DATA_DIR / 'ai_files'


async def run(path: str = '') -> str:
    if not path:
        return "Ошибка: не передан путь"
    BASE.mkdir(parents=True, exist_ok=True)
    target = (BASE / path).resolve()
    if BASE.resolve() not in target.parents and target != BASE.resolve():
        return "Путь выходит за пределы песочницы ai_files"
    if not target.is_file():
        return f"Файл не найден: {path}"
    return target.read_text(encoding='utf-8', errors='replace')
'''

SEED['write_file'] = '''"""
name: write_file
category: files
title: Записать файл
description: пишет текстовый файл в песочницу ai_files/
args: path: string, content: string, append?: bool
dangerous: false
"""
from pathlib import Path

from core.paths import DATA_DIR

BASE = DATA_DIR / 'ai_files'


async def run(path: str = '', content: str = '', append: bool = False) -> str:
    if not path:
        return "Ошибка: не передан путь"
    BASE.mkdir(parents=True, exist_ok=True)
    target = (BASE / path).resolve()
    if BASE.resolve() not in target.parents and target != BASE.resolve():
        return "Путь выходит за пределы песочницы ai_files"
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, 'a' if append else 'w', encoding='utf-8') as f:
        f.write(content or '')
    return f"Записано в ai_files/{path} ({len(content or '')} симв.)"
'''

SEED['remember'] = '''"""
name: remember
category: memory
title: Запомнить
description: сохраняет заметку в постоянной памяти (переживает перезапуск бота)
args: key: string, value: string
example: {"key": "любимый_цвет", "value": "синий"}
"""
import db


async def run(key: str = '', value: str = '') -> str:
    if not key:
        return "Ошибка: не передан ключ"
    notes = await db.get('neuro_notes') or {}
    notes[key] = value
    await db.set('neuro_notes', notes)
    return f"Заметка '{key}' сохранена"
'''

SEED['recall'] = '''"""
name: recall
category: memory
title: Вспомнить
description: достаёт заметку по ключу (или все заметки, если ключ не указан)
args: key?: string
"""
import json

import db


async def run(key: str = '') -> str:
    notes = await db.get('neuro_notes') or {}
    if not key:
        return json.dumps(notes, ensure_ascii=False) if notes else "Заметок нет"
    return str(notes[key]) if key in notes else f"Заметка '{key}' не найдена"
'''

SEED['fetch_url'] = '''"""
name: fetch_url
category: web
title: Открыть страницу
description: скачивает страницу по URL, возвращает очищенный от HTML текст
args: url: string
"""
import asyncio
import html as _html
import re
import urllib.request

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Accept-Language': 'ru-RU,ru;q=0.9,en;q=0.8'}


def _get(url):
    req = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.read(2_000_000).decode('utf-8', errors='replace')


async def run(url: str = '') -> str:
    if not re.match(r'^https?://', url or ''):
        return "Ошибка: нужен адрес вида https://…"
    try:
        page = await asyncio.to_thread(_get, url)
    except Exception as e:
        return f"Ошибка загрузки {url}: {e}"
    text = re.sub(r'<script.*?</script>|<style.*?</style>', ' ', page, flags=re.S | re.I)
    return _html.unescape(re.sub(r'\\s+', ' ', re.sub(r'<[^>]+>', ' ', text)).strip())
'''

SEED['web_search'] = '''"""
name: web_search
category: web
title: Поиск в интернете
description: поиск в интернете (DuckDuckGo HTML, без ключа), возвращает заголовки и ссылки
args: query: string, max_results?: int
"""
import asyncio
import html as _html
import re
import urllib.parse
import urllib.request

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36',
            'Content-Type': 'application/x-www-form-urlencoded'}
_PATTERNS = [r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', r'<a[^>]*class="[^"]*result__url[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>']


def _post(query):
    req = urllib.request.Request('https://html.duckduckgo.com/html/', data=urllib.parse.urlencode({'q': query}).encode(), headers=_HEADERS)
    with urllib.request.urlopen(req, timeout=8) as r:
        return r.status, r.read().decode('utf-8', errors='replace')


async def run(query: str = '', max_results: int = 5) -> str:
    if not query:
        return "Ошибка: не передан поисковый запрос"
    try:
        status, page = await asyncio.to_thread(_post, query)
    except Exception as e:
        return f"Ошибка сети при поиске: {e}"
    found = []
    for pat in _PATTERNS:
        found = re.findall(pat, page, flags=re.S)
        if found:
            break
    if not found:
        return f"Ничего не найдено (HTTP {status}) — возможно, поисковик изменил разметку; попробуйте fetch_url на конкретный сайт."
    lines = [f"- {_html.unescape(re.sub(r'<[^>]+>', '', t)).strip()} — {h}" for h, t in found[:max(1, min(int(max_results), 10))]]
    return "\\n".join(lines)
'''

SEED['send_message'] = '''"""
name: send_message
category: telegram
title: Отправить сообщение
description: отправляет Telegram-сообщение от имени аккаунта в указанный чат или @username
args: chat_id: string, text: string
dangerous: true
"""
import engine


async def run(chat_id: str = '', text: str = '') -> str:
    if not chat_id or not text:
        return "Ошибка: нужны chat_id и text"
    entity = await engine.client.get_entity(int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id)
    await engine.client.send_message(entity, text)
    return f"Сообщение отправлено в {chat_id}"
'''

SEED['run_python'] = '''"""
name: run_python
category: system
title: Выполнить Python
description: выполняет произвольный Python-код в процессе юзербота — ПОЛНЫЙ доступ к client, db, файлам и сети, БЕЗ песочницы (аналог .eval); поддерживает верхнеуровневый await/return
args: code: string
dangerous: true
"""
import ast
import asyncio
import contextlib
import io
import json
import os
import re
import sys
import textwrap
import traceback

import engine
import db
import cache
import files


async def run(code: str = '') -> str:
    if not code or not code.strip():
        return "Ошибка: пустой код"
    env = {'engine': engine, 'db': db, 'cache': cache, 'files': files, 'client': engine.client,
           'asyncio': asyncio, 'os': os, 'sys': sys, 're': re, 'json': json}
    src = "async def __neuro_eval__():\\n" + textwrap.indent(code, '    ') + "\\n"
    try:
        tree = ast.parse(src, mode='exec')
        fn = tree.body[0]
        if fn.body and isinstance(fn.body[-1], ast.Expr):
            fn.body[-1] = ast.copy_location(ast.Return(value=fn.body[-1].value), fn.body[-1])
        ast.fix_missing_locations(tree)
        obj = compile(tree, '<neuro_eval>', 'exec')
    except SyntaxError as e:
        return f"Синтаксическая ошибка: {e}"
    buf = io.StringIO()
    try:
        exec(obj, env)
        with contextlib.redirect_stdout(buf):
            result = await env['__neuro_eval__']()
    except Exception:
        out = buf.getvalue()
        return (f"Вывод:\\n{out}\\n" if out else '') + f"Исключение:\\n{traceback.format_exc()[-1500:]}"
    out = buf.getvalue()
    return (f"Вывод: {out.strip()}\\n" if out else '') + f"Результат: {result!r}"
'''


# --------------------------------------------------------------------------- #
# хранилище
# --------------------------------------------------------------------------- #

LEGACY_HASHES = {}                      # name -> sha1 старых версий из SEED (для обновления нетронутых файлов)


def _sha(text):
    import hashlib
    return hashlib.sha1(text.replace('\r\n', '\n').encode('utf-8')).hexdigest()


def _load_seed_dir():
    """Подгружает расширенный набор инструментов из core/neuro_seed/*.py (имя = имя файла); они заменяют встроенные версии."""
    d = Path(__file__).resolve().parent / 'neuro_seed'
    if not d.is_dir():
        return
    for p in sorted(d.glob('*.py')):
        try:
            src = p.read_text(encoding='utf-8')
            meta = parse_tool(src)
        except (OSError, ToolError):
            continue
        name = meta['name']
        if name in SEED and name not in LEGACY_HASHES:
            LEGACY_HASHES[name] = _sha(SEED[name])
        SEED[name] = src


_load_seed_dir()


class ToolStore:
    def __init__(self, root=None):
        self.root = Path(root) if root else TOOLS_DIR
        self.lock = threading.RLock()
        self._code_cache = {}

    # ---- состояние (включено / выключено, описания категорий) ---- #
    def _state_path(self):
        return self.root.parent / 'tools_state.json'

    def _cats_path(self):
        return self.root.parent / 'tools_categories.json'

    def _read(self, p, default):
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return default

    def _write(self, p, data):
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(p)

    def state(self):
        s = self._read(self._state_path(), {})
        s.setdefault('disabled_tools', [])
        s.setdefault('disabled_categories', [])
        s.setdefault('seeded', [])
        return s

    def seed(self):
        """Кладёт базовые инструменты, которых ещё не было. Удалённый пользователем инструмент не возвращается (хранится в seeded)."""
        with self.lock:
            st = self.state()
            changed = False
            hashes = st.setdefault('seed_hashes', {})
            for name, src in SEED.items():
                meta = parse_tool(src)
                path = self.root / meta['category'] / f'{name}.py'
                cur = _sha(src)
                if name in st['seeded']:
                    # обновление: файл не трогали (совпал с прошлой версией из seed или со старой встроенной) -> заменяем новой
                    if path.exists() and hashes.get(name) != cur:
                        try:
                            have = _sha(path.read_text(encoding='utf-8'))
                        except OSError:
                            continue
                        if have == hashes.get(name) or have == LEGACY_HASHES.get(name):
                            path.write_text(src, encoding='utf-8', newline='\n')
                            hashes[name] = cur
                            changed = True
                    continue
                if not path.exists():
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(src, encoding='utf-8', newline='\n')
                    hashes[name] = cur
                st['seeded'].append(name)
                changed = True
            if changed:
                self._write(self._state_path(), st)

    # ---- чтение ---- #
    def _files(self):
        if not self.root.is_dir():
            return []
        return sorted(p for p in self.root.glob('*/*.py') if p.is_file())

    def list_tools(self):
        st = self.state()
        out = []
        for p in self._files():
            try:
                meta = parse_tool(p.read_text(encoding='utf-8'))
            except (ToolError, OSError):
                out.append({'name': p.stem, 'category': p.parent.name, 'title': p.stem, 'description': '⚠ файл повреждён или не разобран', 'args': '', 'example': '',
                            'dangerous': False, 'broken': True, 'enabled': False, 'path': str(p), 'size': p.stat().st_size})
                continue
            if meta['name'] != p.stem or meta['category'] != p.parent.name:
                meta['description'] = '⚠ имя/категория в заголовке не совпадают с путём файла — ' + meta['description']
            meta.update({'path': str(p), 'size': p.stat().st_size, 'broken': False,
                         'enabled': meta['name'] not in st['disabled_tools'] and meta['category'] not in st['disabled_categories']})
            out.append(meta)
        return out

    def categories(self):
        st = self.state()
        meta = {**{k: dict(v) for k, v in DEFAULT_CATEGORIES.items()}, **self._read(self._cats_path(), {})}
        tools = self.list_tools()
        cats = {}
        for t in tools:
            cats.setdefault(t['category'], 0)
            cats[t['category']] += 1
        out = []
        for cid in sorted(cats, key=lambda c: (c not in DEFAULT_CATEGORIES, c)):
            m = meta.get(cid, {})
            out.append({'id': cid, 'title': m.get('title') or cid, 'description': m.get('description', ''), 'keywords': m.get('keywords', ''), 'count': cats[cid],
                        'enabled': cid not in st['disabled_categories'], 'enabled_tools': sum(1 for t in tools if t['category'] == cid and t['enabled'])})
        return out

    def set_category_meta(self, cid, title=None, description=None, keywords=None):
        with self.lock:
            data = self._read(self._cats_path(), {})
            cur = data.setdefault(cid, {})
            for k, v in (('title', title), ('description', description), ('keywords', keywords)):
                if v is not None:
                    cur[k] = str(v)[:200]
            self._write(self._cats_path(), data)

    def get_source(self, name):
        p = self.path_of(name)
        return p.read_text(encoding='utf-8')

    def path_of(self, name):
        if not NAME_RE.match(str(name or '')):
            raise ToolError('Некорректное имя инструмента')
        for p in self._files():
            if p.stem == name:
                return p
        raise ToolError(f"Инструмента «{name}» нет")

    # ---- запись ---- #
    def add(self, source, replace=False):
        meta = parse_tool(source)
        with self.lock:
            existing = [p for p in self._files() if p.stem == meta['name']]
            if existing and not replace:
                raise ToolError(f"Инструмент «{meta['name']}» уже есть — замените его или переименуйте (name:)")
            for p in existing:
                p.unlink()
            path = self.root / meta['category'] / f"{meta['name']}.py"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(source.replace('\r\n', '\n'), encoding='utf-8', newline='\n')
            self._code_cache.pop(meta['name'], None)
            st = self.state()
            if meta['name'] not in st['seeded'] and meta['name'] in SEED:
                st['seeded'].append(meta['name'])
                self._write(self._state_path(), st)
            return meta

    def delete(self, name):
        with self.lock:
            p = self.path_of(name)
            p.unlink()
            self._code_cache.pop(name, None)
            if not any(p.parent.iterdir()):
                p.parent.rmdir()
            st = self.state()
            st['disabled_tools'] = [x for x in st['disabled_tools'] if x != name]
            self._write(self._state_path(), st)

    def set_enabled(self, name=None, category=None, enabled=True):
        with self.lock:
            st = self.state()
            key, lst = ('disabled_tools', name) if name else ('disabled_categories', category)
            if not lst:
                raise ToolError('Укажите инструмент или категорию')
            cur = [x for x in st[key] if x != lst]
            if not enabled:
                cur.append(lst)
            st[key] = cur
            self._write(self._state_path(), st)

    def export(self, name=None):
        """Пути файлов: одного инструмента или всех (для отправки в Telegram)."""
        if name:
            return [self.path_of(name)]
        return self._files()

    # ---- выбор инструментов для запроса ---- #
    def active(self):
        return [t for t in self.list_tools() if t['enabled'] and not t.get('broken')]

    def categories_for(self, text):
        """Категории, подходящие к вопросу по ключевым словам (дешёвый «роутер» без обращения к модели)."""
        low = (text or '').lower()
        hit = []
        for c in self.categories():
            if not c['enabled'] or not c['enabled_tools']:
                continue
            words = [w.strip() for w in c['keywords'].lower().split(',') if w.strip()]
            if any(w in low for w in words) or (c['id'] == 'math' and re.search(r'\d\s*[-+*/^]\s*\d', low)):
                hit.append(c['id'])
        return hit

    # ---- выполнение ---- #
    def _load(self, name):
        p = self.path_of(name)
        mtime = p.stat().st_mtime_ns
        cached = self._code_cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1], cached[2]
        source = p.read_text(encoding='utf-8')
        meta = parse_tool(source)
        ns = {'__name__': f'neuro_tool_{name}', '__file__': str(p)}
        exec(compile(source, str(p), 'exec'), ns)
        fn = ns.get('run')
        if not callable(fn):
            raise ToolError('В файле нет функции run')
        self._code_cache[name] = (mtime, fn, meta)
        return fn, meta

    async def run(self, name, arguments, timeout=60.0, limit=3500):
        """Вызывает run(**arguments); любые ошибки превращаются в понятный текст (результат инструмента для модели)."""
        try:
            fn, meta = self._load(name)
        except ToolError as e:
            return f"Ошибка инструмента '{name}': {e}"
        except Exception as e:                                          # noqa: BLE001
            return f"Ошибка загрузки инструмента '{name}': {type(e).__name__}: {e}"
        if not isinstance(arguments, dict):
            return f"Ошибка: аргументы инструмента '{name}' должны быть объектом"
        try:
            inspect.signature(fn).bind(**arguments)
        except TypeError as e:
            return f"Ошибка аргументов инструмента '{name}': {e}. Ожидается: {meta['args']}"
        token = current_module.set(OWNER_MODULE)
        try:
            res = fn(**arguments)
            if inspect.isawaitable(res):
                res = await asyncio.wait_for(res, timeout)
        except asyncio.TimeoutError:
            return f"Инструмент '{name}' не уложился в {timeout:g} с"
        except Exception as e:                                          # noqa: BLE001
            import traceback
            return f"Ошибка выполнения инструмента '{name}':\n{traceback.format_exc()[-800:]}"
        finally:
            current_module.reset(token)
        text = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)
        limit = max(limit, meta.get('limit') or 0)
        return text if len(text) <= limit else text[:limit] + f"\n…(обрезано, всего {len(text)} симв.)"


_store = None


def get_tools() -> ToolStore:
    global _store
    if _store is None:
        _store = ToolStore()
        _store.seed()
    return _store
