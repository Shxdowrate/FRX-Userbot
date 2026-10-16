"""
core/module_meta.py

parse_module_meta(source) — СТАТИЧЕСКИЙ разбор исходника модуля (ast, без
исполнения кода): NAME / MODULE_NAME / VERSION / AUTHOR / DESCRIPTION / TG_URL / GIT_URL и список
команд. Раньше жил внутри modules/builtin/Loader.py (превью .loadmod); теперь
общий — им же движок читает файлы из user/modules/unloaded/ (engine.
list_unloaded_modules(), команда .restoremods). Доступен модулям как
engine.parse_module_meta(source).
"""

import ast
import re

# Ссылки автора в шапке модуля: TG_URL — его канал (или чат), GIT_URL — модуль на GitHub/GitLab/…
LINK_FIELDS = (('tg_url', 'TG_URL'), ('git_url', 'GIT_URL'))
_HOST_RE = re.compile(r'^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$', re.I)


def normalize_module_link(value, kind: str) -> str:
    """
    Приводит ссылку из шапки модуля к полному виду; '' — не задана или не похожа на ссылку.
      kind='tg':  @channel / t.me/channel / telegram.me/… / https://t.me/… / tg://resolve?domain=…
      kind='git': github.com/owner/repo / https://… (только http(s))
    """
    if not isinstance(value, str):
        return ''
    v = value.strip()
    if not v or any(c.isspace() for c in v) or len(v) > 300:
        return ''
    if kind == 'tg':
        if re.fullmatch(r'@[A-Za-z][\w]{3,31}', v):
            return 'https://t.me/' + v[1:]
        if v.lower().startswith('tg://'):
            return v
        v = re.sub(r'^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me|telegram\.dog)/', 'https://t.me/', v, flags=re.I)
        return v if re.match(r'^https://t\.me/[^/?#]+', v) else ''
    if not re.match(r'^https?://', v, re.I):
        v = 'https://' + v
    m = re.match(r'^https?://([^/:?#]+)', v, re.I)
    return v if m and _HOST_RE.match(m.group(1)) else ''


def short_link(url: str, limit: int = 48) -> str:
    """Подпись ссылки: https://t.me/x → t.me/x; github.com/owner/repo/blob/main/a/b.py → repo/a/b.py."""
    u = re.sub(r'^https?://(www\.)?', '', url or '').rstrip('/')
    m = re.match(r'^(?:github|gitlab)\.com/[^/]+/([^/]+)/(?:-/)?(?:blob|tree)/[^/]+/(.+)$', u)
    if m:
        u = f"{m.group(1)}/{m.group(2)}"
    return u if len(u) <= limit else u[:limit - 1] + '…'


def tg_username(url: str):
    """https://t.me/channel → 'channel'; инвайты (t.me/+…, joinchat), посты (t.me/ch/5) → только имя или None."""
    m = re.match(r'^https://t\.me/([A-Za-z][\w]{3,31})(?:/\d+)?/?$', url or '')
    return m.group(1) if m else None


def parse_module_meta(source: str):
    """
    Возвращает dict {'name','module_name','version','author','description','tg_url','git_url','commands','requirements'}
    (commands — список списков имён: [['loadmod', 'lm'], ...]; requirements — строки pip из REQUIREMENTS,
    core/requirements.py) либо None, если в файле синтаксическая ошибка или не задан MODULE_NAME.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    meta = {'name': None, 'module_name': None, 'version': '1.0', 'author': 'unknown', 'description': '',
            'tg_url': '', 'git_url': ''}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    if target.id == 'NAME':
                        meta['name'] = node.value.value
                    elif target.id == 'MODULE_NAME':
                        meta['module_name'] = node.value.value
                    elif target.id == 'VERSION':
                        meta['version'] = str(node.value.value)
                    elif target.id == 'AUTHOR':
                        meta['author'] = node.value.value
                    elif target.id == 'DESCRIPTION':
                        meta['description'] = node.value.value
                    elif target.id == 'TG_URL':
                        meta['tg_url'] = normalize_module_link(node.value.value, 'tg')
                    elif target.id == 'GIT_URL':
                        meta['git_url'] = normalize_module_link(node.value.value, 'git')

    if not meta['module_name'] or not isinstance(meta['module_name'], str):
        return None
    if not meta['name']:
        meta['name'] = meta['module_name']

    commands = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fname = dec.func.attr if isinstance(dec.func, ast.Attribute) else getattr(dec.func, 'id', None)
                if fname != 'command':
                    continue
                names = [a.value for a in dec.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if names:
                    commands.append(names)
    meta['commands'] = commands
    try:
        from .requirements import normalize
    except ImportError:                    # модуль импортирован не как часть пакета core
        from requirements import normalize  # type: ignore
    reqs = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'REQUIREMENTS' for t in node.targets):
            try:
                reqs = [spec for spec, _names in normalize(ast.literal_eval(node.value))]
            except Exception:
                reqs = []
    meta['requirements'] = reqs
    return meta
