"""
core/web/neuro_api.py

Сайт ↔ нейросети (core/llm.py, core/neuro.py, core/neuro_tools.py): вкладка «neuro» (подключения и ключи, модели, планы и расход, настройки, инструменты) и помощник в WebIDE
(`/api/neuro/assist` — потоковый ответ NDJSON).

Ключи: в списках — только маской; целиком — по запросу `/api/neuro/conn/reveal` (кнопка «показать»). В ответах и ошибках ключей нет (scrub в core/llm.py).
При добавлении ключа сразу запрашивается список моделей: если провайдер отверг ключ (401/403), подключение не сохраняется; если не удалось связаться
(нет сети) — подключение остаётся, модели можно обновить позже.
Помощник WebIDE ничего не применяет сам: он возвращает код, а сайт показывает отличия и ждёт «подтвердить».
"""

import asyncio
import json
import re
import time

from aiohttp import web

from ..llm import LLMError, PROVIDERS, approx_tokens, get_llm
from ..neuro import CHOICES, DEFAULTS, Settings, get_agent
from ..neuro_tools import ToolError, get_tools

ASSIST_MAX_CHARS = 60000

CHEATSHEET = """Кратко об API модулей FRX (Python, async):
  import decorators, engine, db, cache, visual, files
  NAME / MODULE_NAME / VERSION / AUTHOR / DESCRIPTION — строки в начале файла (NAME, MODULE_NAME, AUTHOR менять НЕЛЬЗЯ).
  @decorators.command('имя', 'алиас') async def f(event): ...      — команда (.имя); аргументы: engine.get_args()
  @decorators.watcher() / @decorators.cycle(interval='5m') / @decorators.on_start() / @decorators.on_load() / @decorators.on_error()
  @decorators.telegram_event() — служебные события чата; @decorators.callback() — нажатия inline-кнопок
  await engine.answer(event, 'текст') — ответ (HTML: <b>, <i>, <code>); engine.reply / engine.noreply / engine.send_message(chat_id, text)
  await db.get(key, default) / db.set(key, value) / db.delete(key)   — постоянные данные модуля (JSON); cache.* — то же в памяти
  await engine.create_config(key=, description=, data_type='str|int|float|bool|list|choice', default=, valid={...}, options=[...]); await engine.get_config('key')
  form = await engine.create_inlineform(chat_id=, text=, buttons=[[('Текст','data')]]) ; @form.on_click(data='data') async def h(click): await form.edit('...'); await click.answer('...')
  visual.visual('status_success') — иконка; engine.tlog('текст') — запись в лог.
  Весь код — async; блокирующие вызовы не используйте."""


def extract_code(text: str):
    """Код из ответа модели: первый ```-блок (python/py/без языка); если блоков нет — весь текст. → (код, был_ли_блок)."""
    blocks = re.findall(r'```([a-zA-Z0-9_+\-]*)[ \t]*\n(.*?)```', text, flags=re.S)
    for lang, body in blocks:
        if lang.lower() in ('', 'python', 'py', 'python3'):
            return body.rstrip('\n'), True
    if blocks:
        return blocks[0][1].rstrip('\n'), True
    open_block = re.match(r'^\s*```[a-zA-Z0-9_+\-]*[ \t]*\n(.*)$', text, flags=re.S)        # блок не закрыт (ответ оборвался)
    if open_block:
        return open_block.group(1).rstrip('\n'), True
    return text.strip('\n'), False


def build_assist(mode, instruction, selected, before, after, filename, cheatsheet=True):
    """→ (system, user): промпты для режимов edit (заменить выделенное), insert (вставить в курсор), chat (вопрос по коду)."""
    sys_parts = ['Ты — помощник по написанию модулей для Telegram-юзербота FRX на Python. Отвечай по-русски.']
    if cheatsheet:
        sys_parts.append(CHEATSHEET)
    if mode == 'edit':
        sys_parts.append('Тебе дадут выделенный фрагмент кода и задачу. Верни ТОЛЬКО новый код, который заменит выделенный фрагмент, одним блоком ```python ... ``` '
                         'и без пояснений вне блока. Сохрани отступы фрагмента; не выходи за его границы (не переписывай соседний код); не меняй строки NAME, MODULE_NAME, AUTHOR.')
        user = f"Файл: {filename}\n\nКод ПЕРЕД фрагментом (для контекста):\n```python\n{before}\n```\n\nВЫДЕЛЕННЫЙ ФРАГМЕНТ (его нужно заменить):\n```python\n{selected}\n```\n\nКод ПОСЛЕ фрагмента (для контекста):\n```python\n{after}\n```\n\nЗадача: {instruction}"
    elif mode == 'insert':
        sys_parts.append('Тебе дадут код вокруг курсора и задачу. Верни ТОЛЬКО код, который нужно вставить в место курсора, одним блоком ```python ... ``` и без пояснений '
                         'вне блока. Учитывай отступ места вставки; не повторяй код, который уже есть.')
        user = f"Файл: {filename}\n\nКод ПЕРЕД курсором:\n```python\n{before}\n```\n\nКод ПОСЛЕ курсора:\n```python\n{after}\n```\n\nЗадача: {instruction}"
    else:
        sys_parts.append('Отвечай на вопросы о коде кратко и по делу; код в ответе оформляй блоками ```python.')
        ctx = selected if selected.strip() else f"{before}\n{after}"
        user = f"Файл: {filename}\n\nКод:\n```python\n{ctx}\n```\n\nВопрос: {instruction}"
    return '\n\n'.join(sys_parts), user


def register(panel, router):
    from .server import ApiError
    api = NeuroApi(panel, ApiError)
    r = router
    r.add_get('/api/neuro/state', panel.api(api.state))
    for name in ('add', 'update', 'delete', 'refresh', 'reveal', 'plan', 'reset_usage'):
        r.add_post(f'/api/neuro/conn/{name}', panel.api(getattr(api, f'conn_{name}')))
    r.add_post('/api/neuro/model/enable', panel.api(api.model_enable))
    r.add_post('/api/neuro/select', panel.api(api.select))
    r.add_post('/api/neuro/settings', panel.api(api.settings))
    r.add_get('/api/neuro/tools', panel.api(api.tools))
    r.add_get('/api/neuro/tools/source', panel.api(api.tool_source))
    for name in ('add', 'delete', 'enable', 'category'):
        r.add_post(f'/api/neuro/tools/{name}', panel.api(getattr(api, f'tool_{name}')))
    r.add_post('/api/neuro/assist', api.assist)
    return api


class NeuroApi:
    def __init__(self, panel, api_error):
        self.panel = panel
        self.Err = api_error

    @property
    def llm(self):
        return get_llm()

    def _wrap(self, fn):
        """LLMError / ToolError → ApiError с понятным текстом."""
        try:
            return fn()
        except (LLMError, ToolError) as e:
            raise self.Err(str(e), 400)

    # ------------------------------ состояние ------------------------------ #

    def _providers(self):
        return [{'id': k, 'title': v['title'], 'kind': v['kind'], 'hint': v.get('hint', ''), 'key': v['key'], 'url_editable': bool(v.get('url_editable')),
                 'url_required': bool(v.get('url_required')), 'base': v['base'], 'scopes': v.get('scopes', [])} for k, v in PROVIDERS.items()]

    async def state(self, request):
        llm = self.llm
        await llm.scan_local()
        conns = [llm.describe(c) for c in llm.connections()]
        return {'providers': self._providers(), 'connections': conns, 'models': llm.models(only_enabled=False),
                'selected': {'chat': llm.selected('chat'), 'ide': llm.selected('ide')}, 'settings': Settings().get(),
                'choices': {k: list(v) for k, v in CHOICES.items()}, 'days': llm.usage_days(30)}

    # ---------------------------- подключения ----------------------------- #

    async def conn_add(self, request):
        b = await self.panel._json(request)
        llm = self.llm
        c = self._wrap(lambda: llm.add_connection(b.get('provider'), b.get('key', ''), b.get('name', ''), b.get('base_url', ''), b.get('scope', ''), b.get('verify_ssl')))
        warning = None
        try:
            models = await llm.refresh_models(c['id'])
        except LLMError as e:
            if e.kind == 'auth':
                llm.delete_connection(c['id'])
                raise self.Err(str(e), 400)
            models, warning = [], str(e)
        return {'id': c['id'], 'models': len(models), 'warning': warning, 'connection': llm.describe(llm.get(c['id']))}

    async def conn_update(self, request):
        b = await self.panel._json(request)
        fields = {k: b[k] for k in ('name', 'key', 'base_url', 'verify_ssl', 'enabled', 'scope') if k in b}
        c = self._wrap(lambda: self.llm.update_connection(str(b.get('id', '')), **fields))
        return {'connection': self.llm.describe(c)}

    async def conn_delete(self, request):
        b = await self.panel._json(request)
        self._wrap(lambda: self.llm.delete_connection(str(b.get('id', ''))))
        return {'ok': True}

    async def conn_refresh(self, request):
        b = await self.panel._json(request)
        try:
            models = await self.llm.refresh_models(str(b.get('id', '')))
        except LLMError as e:
            raise self.Err(str(e), 400)
        return {'models': len(models)}

    async def conn_reveal(self, request):
        b = await self.panel._json(request)
        c = self._wrap(lambda: self.llm.get(str(b.get('id', ''))))
        return {'key': c.get('key', '')}

    async def conn_plan(self, request):
        b = await self.panel._json(request)
        plan = self._wrap(lambda: self.llm.set_plan(str(b.get('id', '')), b.get('limit'), b.get('period', 'none'), bool(b.get('hard'))))
        return {'plan': plan, 'status': self.llm.plan_status(str(b.get('id', '')))}

    async def conn_reset_usage(self, request):
        b = await self.panel._json(request)
        self._wrap(lambda: self.llm.reset_usage(str(b.get('id', ''))))
        return {'ok': True}

    async def model_enable(self, request):
        b = await self.panel._json(request)
        self._wrap(lambda: self.llm.set_model_enabled(str(b.get('conn', '')), str(b.get('model', '')), bool(b.get('enabled'))))
        return {'ok': True}

    async def select(self, request):
        b = await self.panel._json(request)
        surface = b.get('surface')
        if surface not in ('chat', 'ide'):
            raise self.Err('surface: chat или ide')
        self._wrap(lambda: self.llm.select(surface, str(b.get('ref', ''))))
        return {'ok': True}

    async def settings(self, request):
        b = await self.panel._json(request)
        patch = b.get('patch') if isinstance(b.get('patch'), dict) else {}
        return {'settings': self._wrap(lambda: Settings().set(patch))}

    # ----------------------------- инструменты ----------------------------- #

    async def tools(self, request):
        ts = get_tools()
        return {'categories': ts.categories(), 'tools': ts.list_tools()}

    async def tool_source(self, request):
        return {'source': self._wrap(lambda: get_tools().get_source(request.query.get('name', '')))}

    async def tool_add(self, request):
        b = await self.panel._json(request)
        src = b.get('source')
        if not isinstance(src, str) or not src.strip():
            raise self.Err('Пустой файл инструмента')
        meta = self._wrap(lambda: get_tools().add(src, replace=bool(b.get('replace'))))
        return {'tool': {k: meta[k] for k in ('name', 'category', 'title', 'dangerous', 'heavy')}}

    async def tool_delete(self, request):
        b = await self.panel._json(request)
        self._wrap(lambda: get_tools().delete(str(b.get('name', ''))))
        return {'ok': True}

    async def tool_enable(self, request):
        b = await self.panel._json(request)
        self._wrap(lambda: get_tools().set_enabled(name=b.get('name') or None, category=b.get('category') or None, enabled=bool(b.get('enabled'))))
        return {'ok': True}

    async def tool_category(self, request):
        b = await self.panel._json(request)
        cid = str(b.get('id', ''))
        if not re.match(r'^[a-z][a-z0-9_]{1,23}$', cid):
            raise self.Err('Некорректный id категории')
        get_tools().set_category_meta(cid, b.get('title'), b.get('description'), b.get('keywords'))
        return {'ok': True}

    # ------------------------- помощник WebIDE ----------------------------- #

    async def assist(self, request):
        """POST /api/neuro/assist → поток NDJSON: {"t":"delta","text":…} … {"t":"done","text","code","fenced","in","out","plan","estimated"} | {"t":"error","message"}."""
        try:
            b = await request.json()
        except Exception:
            return web.json_response({'error': 'Некорректный запрос'}, status=400)
        mode = b.get('mode') if b.get('mode') in ('edit', 'insert', 'chat') else 'chat'
        instruction = str(b.get('instruction') or '').strip()
        selected, before, after = (str(b.get(k) or '') for k in ('selected', 'before', 'after'))
        filename = re.sub(r'[^\w.\-]', '_', str(b.get('filename') or 'module.py'))[:80]
        if not instruction:
            return web.json_response({'error': 'Напишите, что нужно сделать'}, status=400)
        if mode == 'edit' and not selected.strip():
            return web.json_response({'error': 'Для режима «изменить» выделите фрагмент кода'}, status=400)
        if len(selected) + len(before) + len(after) + len(instruction) > ASSIST_MAX_CHARS:
            return web.json_response({'error': f'Слишком много кода для одного запроса (больше {ASSIST_MAX_CHARS // 1000} тыс. символов) — выделите фрагмент поменьше'}, status=413)
        llm = self.llm
        ref = str(b.get('ref') or '') or llm.selected('ide')
        system, user = build_assist(mode, instruction, selected, before[-12000:], after[:8000], filename)
        history = [m for m in (b.get('history') or []) if isinstance(m, dict) and m.get('role') in ('user', 'assistant')][-6:] if mode == 'chat' else []
        messages = [{'role': m['role'], 'content': str(m.get('content', ''))[:6000]} for m in history] + [{'role': 'user', 'content': user}]
        resp = web.StreamResponse(headers={'Content-Type': 'application/x-ndjson; charset=utf-8', 'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        await resp.prepare(request)
        cancel = asyncio.Event()

        async def send(obj):
            try:
                await resp.write((json.dumps(obj, ensure_ascii=False) + '\n').encode('utf-8'))
            except (ConnectionResetError, asyncio.CancelledError, RuntimeError):
                cancel.set()
        try:
            res = await llm.chat(ref, messages, system=system, stream=True, on_delta=lambda t: send({'t': 'delta', 'text': t}), cancel=cancel,
                                 temperature=Settings().get().get('temperature'))
        except LLMError as e:
            await send({'t': 'error', 'kind': e.kind, 'message': str(e)})
            return resp
        except Exception as e:                                                  # noqa: BLE001
            await send({'t': 'error', 'kind': 'server', 'message': f'{type(e).__name__}: {e}'[:300]})
            return resp
        code, fenced = extract_code(res.text) if mode in ('edit', 'insert') else (res.text, False)
        conn, _model = llm.parse_ref(ref)
        await send({'t': 'done', 'text': res.text, 'code': code, 'fenced': fenced, 'finish': res.finish, 'in': res.tokens_in, 'out': res.tokens_out,
                    'estimated': res.estimated, 'plan': llm.plan_status(conn['id']), 'ref': ref})
        try:
            await resp.write_eof()
        except Exception:
            pass
        return resp
