"""
core/neuro.py

Агент нейросети: настройки, системный промпт с инструментами по категориям, обрезка контекста по бюджету токенов и цикл «модель → инструмент → модель».
Работает с любым провайдером из core/llm.py. Им пользуются модуль NeuroDash (`engine.neuro`) и сайт.

Протокол инструментов — промпт-протокол (как в NeuroDash 4.0): модель отвечает ОДНОЙ строкой `TOOL_CALL: {"name": "...", "arguments": {...}}`, агент
выполняет инструмент и возвращает результат сообщением `[РЕЗУЛЬТАТ name]: …`. Работает с любой моделью, в том числе без родного tool calling.

Экономия токенов: в режиме `categories` (по умолчанию) в запрос попадают описания только тех категорий, которые подходят к вопросу (по ключевым словам)
или которые модель открыла сама вызовом `open_category`; остальные — одной строкой. Режим `full` — все включённые инструменты сразу.

Все ответы агента — события (on_event): context, delta, tool, usage, answer, error. Интерфейс сам решает, как их показать (Telegram-форма, сайт).
"""

import asyncio
import json
import re
import threading
from pathlib import Path

from .llm import LLMError, approx_tokens, get_llm
from .neuro_tools import get_tools
from .paths import DATA_DIR

SETTINGS_PATH = DATA_DIR / 'neuro' / 'settings.json'

DEFAULTS = {
    'tools_mode': 'categories',       # categories | full | off
    'max_steps': 8,                   # сколько раз за один вопрос модель может вызвать инструменты
    'context_budget': 5000,           # токенов на запрос (история подрезается целыми ходами)
    'answer_reserve': 800,
    'allow_dangerous': 'confirm',     # confirm | deny | allow — инструменты с dangerous: true
    'allow_heavy': 'confirm',         # confirm | deny | allow — «тяжёлые» инструменты (heavy: в заголовке) стоят много токенов
    'fewshot': 'auto',                # auto (только Ollama) | on | off
    'tool_output_limit': 3500,
    'tool_timeout': 60,
    'temperature': None,
    'system_extra': '',               # ваши добавочные указания в системный промпт
}
LIMITS = {'max_steps': (1, 20), 'context_budget': (1000, 200000), 'answer_reserve': (100, 8000), 'tool_output_limit': (200, 20000), 'tool_timeout': (5, 600)}
CHOICES = {'tools_mode': ('categories', 'full', 'off'), 'allow_dangerous': ('confirm', 'deny', 'allow'), 'allow_heavy': ('confirm', 'deny', 'allow'), 'fewshot': ('auto', 'on', 'off')}


def _short(text, n=110):
    """Описание инструмента для промпта: первое предложение / до `;`, не длиннее n символов (длинные описания × каждый шаг = токены)."""
    text = (text or '').strip()
    for sep in ('; ', '. '):
        i = text.find(sep)
        if 25 < i < len(text):
            text = text[:i]
            break
    return text if len(text) <= n else text[:n - 1].rstrip() + '…'


class Settings:
    def __init__(self, path=None):
        self.path = Path(path) if path else SETTINGS_PATH
        self.lock = threading.RLock()

    def get(self):
        with self.lock:
            try:
                data = json.loads(self.path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                data = {}
            out = {**DEFAULTS, **{k: v for k, v in data.items() if k in DEFAULTS}}
            # старые значения по умолчанию (1200 / 6) считаем «не задано»: с ними веб-инструменты обрезались до бесполезности
            if data.get('tool_output_limit') == 1200:
                out['tool_output_limit'] = DEFAULTS['tool_output_limit']
            if data.get('max_steps') == 6:
                out['max_steps'] = DEFAULTS['max_steps']
            return out

    def set(self, patch: dict):
        with self.lock:
            cur = self.get()
            for k, v in patch.items():
                if k not in DEFAULTS:
                    continue
                if k in CHOICES:
                    if v not in CHOICES[k]:
                        raise LLMError('config', f"{k}: допустимо {', '.join(CHOICES[k])}")
                elif k in LIMITS:
                    try:
                        v = int(v)
                    except (TypeError, ValueError):
                        raise LLMError('config', f'{k}: нужно целое число')
                    lo, hi = LIMITS[k]
                    v = max(lo, min(hi, v))
                elif k == 'temperature':
                    v = None if v in (None, '') else max(0.0, min(2.0, float(v)))
                elif k == 'system_extra':
                    v = str(v or '')[:4000]
                cur[k] = v
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix('.tmp')
            tmp.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding='utf-8')
            tmp.replace(self.path)
            return cur


# --------------------------------------------------------------------------- #
# контекст
# --------------------------------------------------------------------------- #

def _strip_turn(msg):
    return {k: v for k, v in msg.items() if k != 'turn'}


def _message_tokens(msg):
    return approx_tokens(json.dumps(_strip_turn(msg), ensure_ascii=False, default=str))


def trim_context(history, overhead_tokens, budget=8000, reserve=800):
    """Режет историю под бюджет, оставляя свежие ХОДЫ целиком (вызов инструмента не отрывается от результата). → (сообщения, сколько отброшено)."""
    available = max(500, budget - overhead_tokens - reserve)
    groups, order = {}, []
    for m in history:
        t = m.get('turn', 0)
        if t not in groups:
            groups[t] = []
            order.append(t)
        groups[t].append(m)
    kept, total = [], 0
    for t in reversed(order):
        gt = sum(_message_tokens(m) for m in groups[t])
        if kept and total + gt > available:
            break
        kept.insert(0, t)
        total += gt
    trimmed = [m for t in kept for m in groups[t]]
    return trimmed, len(history) - len(trimmed)


def _balanced_json(text, start):
    depth, in_str, esc = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\':
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


_MARKER = re.compile(r'TOOL_CALL\s*:\s*', re.I)


def extract_tool_call(content, valid):
    """('ok'|'invalid', имя, аргументы) либо None, если это обычный ответ. valid — имена существующих инструментов."""
    if not content:
        return None
    m = _MARKER.search(content)
    text = content[m.end():] if m else content
    if '"name"' not in text:
        return None
    start = text.find('{')
    if start == -1:
        return None
    raw = _balanced_json(text, start)
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    name, args = data.get('name'), data.get('arguments', data.get('parameters', {}))
    if not isinstance(name, str) or not isinstance(args, dict):
        return None
    return ('ok' if name in valid else 'invalid', name, args)


class _Gate:
    """Решает, показывать ли поток пользователю: если ответ начинается с TOOL_CALL — это вызов инструмента, а не текст, его не стримим."""

    MARK = 'TOOL_CALL'

    def __init__(self, emit):
        self.emit = emit
        self.buf = ''
        self.open = False
        self.blocked = False

    async def feed(self, piece):
        if self.blocked:
            return
        if self.open:
            await self.emit(piece)
            return
        self.buf += piece
        probe = self.buf.lstrip()
        if not probe:
            return
        head = probe[:len(self.MARK)].upper()
        if self.MARK.startswith(head) and len(probe) < len(self.MARK):
            return                                              # пока похоже на начало TOOL_CALL — ждём
        if head == self.MARK or probe.startswith('{"name"') or probe.startswith('```'):
            self.blocked = probe.upper().startswith(self.MARK) or probe.startswith('{"name"')
            if self.blocked:
                return
        self.open = True
        await self.emit(self.buf)


class TurnResult:
    def __init__(self):
        self.text = ''
        self.tokens_in = 0
        self.tokens_out = 0
        self.steps = []
        self.finish = 'stop'
        self.loaded = set()
        self.error = None
        self.estimated = False

    @property
    def total(self):
        return self.tokens_in + self.tokens_out


class Agent:
    def __init__(self, llm=None, tools=None, settings=None):
        self.llm = llm or get_llm()
        self.tools = tools or get_tools()
        self.settings = settings or Settings()

    # ---- промпт ---- #
    def _tool_line(self, t, with_example=False):
        line = f"- {t['name']}({t['args']}) — {_short(t['description'])}"
        if with_example and t.get('example') and len(t['example']) < 120:
            line += f" Пример: {t['example']}"
        if t.get('heavy'):
            line += ' [ТЯЖЁЛЫЙ: много токенов, пользователь должен разрешить — вызывай только если без него не обойтись]'
        return line

    def _about_frx(self, cats=None):
        """Короткая справка о платформе (подробности — инструмент frx_help)."""
        try:
            from .engine import FRX_VERSION as ver
        except Exception:                                               # noqa: BLE001
            ver = ''
        has_help = any(t['name'] == 'frx_help' for t in self.tools.active())
        return (f"FRX {ver} — Telegram-юзербот владельца с загружаемыми модулями и командами с префиксом «.»; ты — его модуль NeuroDash. "
                + ("О FRX, командах и модулях отвечай через frx_help, не выдумывай." if has_help else "Не уверен в деталях FRX — так и скажи."))

    def build_prompt(self, loaded, mode, examples=False):
        """→ (текст системного промпта, [имена доступных инструментов], множество загруженных категорий)."""
        cfg = self.settings.get()
        active = self.tools.active() if mode != 'off' else []
        cats = {c['id']: c for c in self.tools.categories()}
        visible = []
        lines = []
        if mode == 'full':
            visible = active
            lines = [self._tool_line(t, True) for t in active]
        elif mode == 'categories' and active:
            by_cat = {}
            for t in active:
                by_cat.setdefault(t['category'], []).append(t)
            closed = []
            for cid, items in by_cat.items():
                if cid in loaded:
                    visible += items
                    lines.append(f"[{cats.get(cid, {}).get('title', cid)}]")
                    lines += [self._tool_line(t, examples) for t in items]
                else:
                    closed.append(f"- {cid}: {cats.get(cid, {}).get('description', '')}")
            if closed:
                lines.append('Закрытые категории (сначала open_category, потом инструмент):')
                lines += closed
                lines.append('- open_category(name) — открывает категорию, например {"name": "web"}')
        names = [t['name'] for t in visible] + (['open_category'] if mode == 'categories' and any(c not in loaded for c in {t['category'] for t in active}) else [])
        parts = ['Ты — Neuro, ассистент, встроенный в Telegram-юзербот FRX. Отвечай по существу и по-русски, если явно не попросили иначе.',
                 self._about_frx(cats)]
        if lines:
            parts.append('Доступные инструменты (используй по одному за раз):\n' + '\n'.join(lines))
            parts.append('Вызов инструмента — ответ РОВНО одной строкой, больше ничего:\nTOOL_CALL: {"name": "<имя>", "arguments": {...}}\n'
                         'Придёт результат — тогда дай обычный ответ. Инструмент не нужен — отвечай текстом.')
            ex = next((t for t in visible if t.get('example')), None) if examples else None
            if ex:
                parts.append(f"Пример формата вызова (инструмент здесь — для примера, выбирай подходящий под вопрос):\nTOOL_CALL: " + '{"name": "' + ex['name'] + '", "arguments": ' + (ex['example'] if ex['example'].lstrip().startswith('{') else '{}') + '}')
            rules = []
            vn = {t['name'] for t in visible}
            if 'calculator' in vn:
                rules.append('Любую арифметику считай через calculator.')
            if {'remember', 'recall'} & vn:
                rules.append('«Запомни» — remember, «что помнишь» — recall; не говори «запомнил», не вызвав инструмент.')
            rules.append('Инструменты — только для действий и актуальных данных. Код, тексты, объяснения делай сам, без инструментов.')
            if any(t.get('heavy') for t in visible):
                rules.append('«ТЯЖЁЛЫЕ» инструменты дороги по токенам — только если обычные не помогли.')
            rules.append('Не выдумывай инструменты, которых нет в списке.')
            parts.append('Правила:\n' + '\n'.join(f'{i}. {r}' for i, r in enumerate(rules, 1)))
        if cfg['system_extra'].strip():
            parts.append(cfg['system_extra'].strip())
        return '\n\n'.join(parts), names, set(loaded)

    def _fewshot(self, provider, visible_names):
        cfg = self.settings.get()
        if cfg['fewshot'] == 'off' or (cfg['fewshot'] == 'auto' and provider != 'ollama'):
            return []
        shots = []
        if 'calculator' in visible_names:
            shots += [{'role': 'user', 'content': 'Сколько будет 37*41?'}, {'role': 'assistant', 'content': 'TOOL_CALL: {"name": "calculator", "arguments": {"expression": "37*41"}}'},
                      {'role': 'user', 'content': '[РЕЗУЛЬТАТ calculator]: 37*41 = 1517'}, {'role': 'assistant', 'content': 'Результат: 1517.'}]
        if 'remember' in visible_names:
            shots += [{'role': 'user', 'content': 'запомни, что мой любимый цвет — синий'},
                      {'role': 'assistant', 'content': 'TOOL_CALL: {"name": "remember", "arguments": {"key": "любимый_цвет", "value": "синий"}}'},
                      {'role': 'user', 'content': "[РЕЗУЛЬТАТ remember]: Заметка 'любимый_цвет' сохранена"}, {'role': 'assistant', 'content': 'Запомнил: твой любимый цвет — синий.'}]
        shots += [{'role': 'user', 'content': 'привет, как дела?'}, {'role': 'assistant', 'content': 'Привет! Всё хорошо, чем могу помочь?'}]
        return shots

    async def _emit(self, on_event, ev):
        if on_event is not None:
            r = on_event(ev)
            if asyncio.iscoroutine(r):
                await r

    # ---- один вопрос ---- #
    async def run_turn(self, history, user_text, *, ref=None, surface='chat', on_event=None, cancel=None, confirm=None, loaded=None, stream=True):
        """
        history — список сообщений диалога (изменяется на месте: добавляются вопрос, вызовы инструментов, ответ; у каждого есть 'turn').
        confirm(name, args) → bool — спрашивает пользователя перед инструментом с dangerous: true (корутина).
        """
        cfg = self.settings.get()
        res = TurnResult()
        llm = self.llm
        ref = ref or llm.selected(surface)
        try:
            conn, model = llm.parse_ref(ref)
        except LLMError as e:
            res.error, res.text, res.finish = e, f'⚠️ {e}', 'error'
            await self._emit(on_event, {'type': 'error', 'kind': e.kind, 'message': str(e)})
            return res
        provider = conn['provider']
        mode = cfg['tools_mode']
        res.loaded = set(loaded or [])
        if mode == 'categories':
            res.loaded |= set(self.tools.categories_for(user_text))
        turn = max([m.get('turn', 0) for m in history] or [0]) + 1
        for m in history:               # результаты инструментов прошлых вопросов в истории сжимаем: они уже использованы, а токены платные
            if m.get('role') == 'user' and str(m.get('content', '')).startswith('[РЕЗУЛЬТАТ') and len(m['content']) > 500:
                m['content'] = m['content'][:480] + '…(сжато)'
        history.append({'role': 'user', 'content': user_text, 'turn': turn})
        tool_no = 0

        async def delta(piece):
            await self._emit(on_event, {'type': 'delta', 'text': piece})

        for step in range(cfg['max_steps']):
            if cancel is not None and cancel.is_set():
                res.finish = 'cancelled'
                break
            examples = cfg['fewshot'] == 'on' or (cfg['fewshot'] == 'auto' and provider == 'ollama')
            prompt, names, res.loaded = self.build_prompt(res.loaded, mode, examples)
            shots = self._fewshot(provider, set(names))
            overhead = approx_tokens(prompt) + sum(approx_tokens(m['content']) for m in shots)
            request, dropped = trim_context(history, overhead, cfg['context_budget'], cfg['answer_reserve'])
            messages = shots + [_strip_turn(m) for m in request]
            await self._emit(on_event, {'type': 'context', 'step': step, 'tokens': overhead + sum(_message_tokens(m) for m in request), 'dropped': dropped,
                                        'categories': sorted(res.loaded), 'tools': len(names)})
            gate = _Gate(delta) if stream else None
            try:
                r = await llm.chat(ref, messages, system=prompt, stream=bool(stream), on_delta=(gate.feed if gate else None), cancel=cancel,
                                   temperature=cfg['temperature'])
            except LLMError as e:
                res.error, res.finish = e, 'error'
                res.text = f'⚠️ {e}'
                await self._emit(on_event, {'type': 'error', 'kind': e.kind, 'message': str(e)})
                break
            res.tokens_in += r.tokens_in
            res.tokens_out += r.tokens_out
            res.estimated = res.estimated or r.estimated
            plan = llm.plan_status(conn['id'])
            if r.finish == 'cancelled':
                res.finish = 'cancelled'
                res.text = r.text
                history.append({'role': 'assistant', 'content': r.text or '…', 'turn': turn})
                await self._emit(on_event, {'type': 'usage', 'step': step, 'in': r.tokens_in, 'out': r.tokens_out, 'plan': plan, 'estimated': r.estimated, 'final': True})
                break
            valid = set(names)
            # модель вызвала инструмент из ЗАКРЫТОЙ категории напрямую (не открыв её) — открываем категорию сами и выполняем вызов
            if mode == 'categories':
                every = {t['name']: t['category'] for t in self.tools.active()}
                call = extract_tool_call(r.text, valid | set(every))
                if call and call[0] != 'invalid' and call[1] in every and call[1] not in valid:
                    res.loaded.add(every[call[1]])
            else:
                call = extract_tool_call(r.text, valid) if mode != 'off' else None
            if call is None:
                res.text = r.text
                history.append({'role': 'assistant', 'content': r.text, 'turn': turn})
                res.steps.append({'kind': 'answer', 'in': r.tokens_in, 'out': r.tokens_out})
                await self._emit(on_event, {'type': 'usage', 'step': step, 'in': r.tokens_in, 'out': r.tokens_out, 'plan': plan, 'estimated': r.estimated, 'final': True})
                await self._emit(on_event, {'type': 'answer', 'text': r.text, 'streamed': bool(gate and gate.open), 'in': res.tokens_in, 'out': res.tokens_out, 'plan': plan})
                break
            status, name, args = call
            tool_no += 1
            history.append({'role': 'assistant', 'content': r.text, 'turn': turn})
            if status == 'invalid':
                result = (f"⚠️ Инструмента '{name}' не существует. Доступные: {', '.join(sorted(valid)) or 'нет'}."
                          + (' Если нужного нет в списке — возможно, закрыта категория: вызови open_category.' if mode == 'categories' else '')
                          + ' Если задача не требует действий в мире (написать код, текст, объяснить) — выполни её сам и ответь обычным текстом, без TOOL_CALL.')
            elif name == 'open_category':
                cid = str(args.get('name', '')).strip().lower()
                cat = next((c for c in self.tools.categories() if c['id'] == cid and c['enabled']), None)
                if cat is None:
                    result = f"Категории '{cid}' нет или она отключена."
                else:
                    res.loaded.add(cid)
                    items = [t for t in self.tools.active() if t['category'] == cid]
                    result = f"Категория '{cid}' открыта. Инструменты:\n" + '\n'.join(self._tool_line(t, True) for t in items)
            else:
                meta = next((t for t in self.tools.active() if t['name'] == name), None)
                result = None
                for flag, key, kind in (('heavy', 'allow_heavy', 'heavy'), ('dangerous', 'allow_dangerous', 'dangerous')):
                    if result is not None or not (meta and meta.get(flag)):
                        continue
                    policy = cfg[key]
                    what = 'тяжёлый (много токенов)' if kind == 'heavy' else 'опасный'
                    if policy == 'deny':
                        result = f"Инструмент '{name}' {what} — запуск запрещён настройками ({key}: deny). Ответь пользователю без него."
                    elif policy == 'confirm':
                        ok = bool(await confirm(name, args, kind=kind, note=meta.get('heavy', ''))) if confirm is not None else False
                        if not ok:
                            result = (f"Пользователь не разрешил запуск инструмента '{name}'. Ответь без него и не повторяй вызов." if confirm is not None else
                                      f"Инструмент '{name}' {what}, а подтвердить запуск здесь нечем — запуск отклонён.")
                if result is None:
                    await self._emit(on_event, {'type': 'tool_start', 'n': tool_no, 'name': name, 'args': args, 'step': step})
                    result = await self.tools.run(name, args, cfg['tool_timeout'], cfg['tool_output_limit'])
            history.append({'role': 'user', 'content': f"[РЕЗУЛЬТАТ {name}]: {result[:max(cfg['tool_output_limit'], 12000)]}", 'turn': turn})
            res.steps.append({'kind': 'tool', 'name': name, 'in': r.tokens_in, 'out': r.tokens_out})
            await self._emit(on_event, {'type': 'usage', 'step': step, 'in': r.tokens_in, 'out': r.tokens_out, 'plan': plan, 'estimated': r.estimated, 'final': False})
            await self._emit(on_event, {'type': 'tool', 'n': tool_no, 'name': name, 'args': args, 'result': result, 'status': status})
        else:
            res.text = res.text or '⚠️ Превышен лимит шагов с инструментами.'
            res.finish = 'max_steps'
            history.append({'role': 'assistant', 'content': res.text, 'turn': turn})
            await self._emit(on_event, {'type': 'answer', 'text': res.text, 'streamed': False, 'in': res.tokens_in, 'out': res.tokens_out, 'plan': self.llm.plan_status(conn['id'])})
        return res


_agent = None


def get_agent() -> Agent:
    global _agent
    if _agent is None:
        _agent = Agent()
    return _agent
