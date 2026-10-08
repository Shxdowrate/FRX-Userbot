"""
NeuroDash (modules/builtin/NeuroDash.py) — нейросети в Telegram.

Подключения, модели, учёт токенов и агент живут в ядре (engine.llm / engine.neuro). Ключи и модели настраиваются
НА САЙТЕ (вкладка neuro); здесь — чат и управление инструментами.

  .neuro / .n            — чат с нейросетью (инлайн-форма, нужен стационарный бот): кнопки «Написать…», «Модель», «Сбросить контекст».
                           Выбор модели — в два шага: провайдер (подключение) → модель; если подключение одно, сразу список его моделей.
                           Расход — в самой верхней строке блока (где язык кода): context (N) ↑вход ↓выход, ✉ за обращение | всего по подключению (из лимита) | баланс провайдера (DeepSeek, OpenRouter)
  .neurotool / .nt       — инструменты нейросети (файлы-инструкции по категориям):
        .nt                          меню (инлайн): категории → инструменты → вкл/выкл, отправить файлом, удалить
        .nt list                     список текстом
        .nt add                      ответом (reply) на сообщение с кодом ИЛИ на .py-файл — создаёт инструмент (замена: .nt add!)
        .nt send <имя>               присылает файл инструмента;   .nt sendall — все инструменты одним zip-архивом
        .nt del <имя>                удаляет инструмент
        .nt on|off <имя|категория>   включает / выключает инструмент или всю категорию
        .nt help                     формат файла инструмента

Любая модель: Ollama, ChatGPT, Claude, DeepSeek, GigaChat, OpenRouter, Groq, xAI, Gemini, Mistral, свой сервер; потоковый ответ;
инструменты — файлами по категориям (в запрос попадают только подходящие категории). «Опасные» инструменты (run_python,
send_message…, метка ⛨) спрашивают подтверждение кнопками в чате, «тяжёлые» (heavy:, метка ⧗) — разрешение с предупреждением
о расходе токенов; инструмент run_command запускает команды юзербота. Заметки — инструменты remember / recall.
"""

import asyncio
import html
import os
import tempfile
import time
import zipfile
from pathlib import Path

import engine
import decorators
import visual

NAME = "NeuroDash"
MODULE_NAME = "neurodash"
VERSION = "5.6"
AUTHOR = "FRX"
DESCRIPTION = "Нейросети в Telegram: любые модели и ключи, расход токенов, инструменты-файлы по категориям"

EDIT_INTERVAL = 1.0           # как часто обновлять сообщение при потоковом ответе, сек
MAX_LEN = 4000                # предел сообщения Telegram (с запасом)
CURSOR = ' ▌'
PAGE = 6                      # моделей на странице выбора
DANGER = '⛨'                  # метка «опасный» (спрашивает подтверждение)
HEAVY = '⧗'                   # метка «тяжёлый» (много токенов)
CONFIRM_TIMEOUT = 90          # сколько ждём «разрешить / отклонить» для опасного инструмента, сек


def _core_ok() -> bool:
    return hasattr(engine, 'neuro') and hasattr(engine, 'llm')


NEED_CORE = (visual.err('NeuroDash: в ядре FRX нет engine.llm / engine.neuro') + "\n"
             + visual.hint('обновите юзербот командой <code>.update</code> и повторите'))


def _esc(text) -> str:
    return html.escape(str(text or ''), quote=False)


def _n(x) -> str:
    return f"{int(x or 0):,}".replace(',', ' ')


_BAL = {'text': ''}           # остаток денег у провайдера (если он отдаёт его через API) — обновляется в начале каждого вопроса


def _plan_text(plan) -> str:
    """всего потрачено на это подключение (за период плана) · лимит · баланс провайдера"""
    parts = []
    if plan:
        tot = f"всего {_n(plan['used'])}"
        if plan.get('limit'):
            tot += f" из {_n(plan['limit'])}" + (f" ({plan['percent']}%)" if plan.get('percent') is not None else '')
        parts.append(tot)
    if _BAL['text']:
        parts.append(f"баланс {_BAL['text']}")
    return ' | '.join(parts)


def _foot(tin=None, tout=None, plan=None, estimated=False, extra='') -> str:
    """Хвост строки языка блока (самый верх блока): ↑вход ↓выход, ✉ за обращение | всего по подключению | баланс."""
    parts = []
    if tin is not None:
        parts.append(f"↑{_n(tin)} ↓{_n(tout)}, ✉ {_n((tin or 0) + (tout or 0))}{'~' if estimated else ''}")
    if extra:
        parts.append(extra)
    pt = _plan_text(plan)
    if pt:
        parts.append(pt)
    return ' | '.join(parts)


def _code(turn, body: str, foot: str = '') -> str:
    """Токены и расход — в самой верхней строке блока (там, где Telegram пишет язык кода), а не в тексте."""
    label = f"context ({turn})" + (f" {foot}" if foot else '')
    return f"<pre><code class=\"language-{html.escape(label, quote=True)}\">{body}</code></pre>"


def _compose(header: str, blocks: list, tail: str = '') -> str:
    """Шапка + блоки диалога + хвост (статус). Если не влезает в сообщение — отбрасываем САМЫЕ СТАРЫЕ блоки, а не свежие."""
    kept = list(blocks)
    cut = False
    while kept and len(header) + len('\n\n'.join(kept)) + len(tail) + 40 > MAX_LEN:
        kept.pop(0)
        cut = True
    parts = [header] + (['<i>… ранние сообщения скрыты (они остаются в контексте модели)</i>'] if cut else []) + kept
    text = '\n\n'.join(parts) + (('\n\n' + tail) if tail else '')
    return text[-MAX_LEN:] if len(text) > MAX_LEN else text


def _model_title(ref) -> str:
    if not ref:
        return 'модель не выбрана'
    for m in engine.llm.models():
        if m['ref'] == ref:
            return m['model']
    return ref.split('|', 1)[-1]


# ---------------------------------------------------------------------------
# .neuro — чат
# ---------------------------------------------------------------------------

@decorators.command('neuro', 'n')
async def neuro_command(event):
    '''чат с нейросетью (модели и ключи — на сайте, вкладка neuro)'''
    if not _core_ok():
        await engine.answer(event, NEED_CORE)
        return
    await engine.llm.scan_local()
    if not engine.llm.selected('chat'):
        await engine.answer(event, visual.warn('<b>Нет доступных моделей</b>') + "\n"
                                   + visual.hint('добавьте нейросеть на сайте (<code>.web</code> → вкладка <b>neuro</b>) или запустите Ollama с локальной моделью'))
        return

    st = {'history': [], 'loaded': set(), 'blocks': [], 'turn': 0, 'busy': False, 'cancel': None, 'confirm': None, 'picker_page': 0}

    def header() -> str:
        return visual.title('Neuro', _esc(_model_title(engine.llm.selected('chat'))))

    def idle_buttons():
        return [[engine.InputButton(visual.btn('Написать…'), "send_message", hint="")], [(visual.btn('Модель'), 'nd_models'), (visual.btn('Сбросить контекст', 'close'), 'clear_context')]]

    stop_buttons = [[(visual.btn('Остановить', 'close'), 'neuro_stop')]]

    form = await engine.create_inlineform(chat_id=event.chat_id, text=header(), buttons=idle_buttons())

    async def show(tail='', buttons=None):
        await form.edit(text=_compose(header(), st['blocks'], tail), buttons=buttons if buttons is not None else idle_buttons())

    @form.on_click(data='clear_context')
    async def clear_context(click):
        st['history'].clear()
        st['blocks'].clear()
        st['loaded'].clear()
        st['turn'] = 0
        await click.answer("Контекст сброшен")
        await show()

    @form.on_click(data='neuro_stop')
    async def stop_generation(click):
        if st['cancel'] is not None:
            st['cancel'].set()
        if st['confirm'] is not None and not st['confirm'].done():
            st['confirm'].set_result(False)
        await click.answer("Останавливаю…")

    @form.on_click(data='nd_ok')
    async def confirm_ok(click):
        if st['confirm'] is not None and not st['confirm'].done():
            st['confirm'].set_result(True)
        await click.answer("Разрешено")

    @form.on_click(data='nd_no')
    async def confirm_no(click):
        if st['confirm'] is not None and not st['confirm'].done():
            st['confirm'].set_result(False)
        await click.answer("Отклонено")

    # --- выбор модели: подключение (провайдер) > модель ---
    def _conns():
        seen, out = set(), []
        for m in engine.llm.models():
            if m['conn'] not in seen:
                seen.add(m['conn'])
                out.append({'conn': m['conn'], 'name': m['conn_name'], 'provider': m['provider']})
        return out

    def picker_providers():
        conns = _conns()
        models = engine.llm.models()
        cur = engine.llm.selected('chat')
        cur_conn = cur.split('|', 1)[0] if cur else None
        rows = []
        for k, c in enumerate(conns):
            n = sum(1 for m in models if m['conn'] == c['conn'])
            title = c['name'] if c['name'].lower() == c['provider'].lower() else f"{c['name']} ({c['provider']})"
            rows.append([(f"{'◉' if c['conn'] == cur_conn else '○'} {title} · {n}"[:48], f'nd_c:{k}:0')])
        rows.append([(visual.btn('Назад', 'back'), 'nd_back')])
        return visual.title('Провайдер', f"подключений · {len(conns)}") + "\n" + visual.hint('ключи и список моделей — на сайте, вкладка neuro'), rows

    def picker_models(k: int, page: int):
        conns = _conns()
        if not (0 <= k < len(conns)):
            return picker_providers()
        cid = conns[k]['conn']
        models = engine.llm.models()
        mine = [(i, m) for i, m in enumerate(models) if m['conn'] == cid]
        pages = max(1, (len(mine) + PAGE - 1) // PAGE)
        page = max(0, min(page, pages - 1))
        cur = engine.llm.selected('chat')
        rows = []
        for i, m in mine[page * PAGE:(page + 1) * PAGE]:
            rows.append([((('◉ ' if m['ref'] == cur else '○ ') + m['model'])[:48], f'nd_m:{i}')])
        nav = []
        if page > 0:
            nav.append(('◂', f'nd_c:{k}:{page - 1}'))
        nav.append((visual.btn('Назад', 'back'), 'nd_models' if len(conns) > 1 else 'nd_back'))
        if page < pages - 1:
            nav.append(('▸', f'nd_c:{k}:{page + 1}'))
        rows.append(nav)
        return (visual.title(_esc(conns[k]['name']), 'выберите модель') + "\n"
                + visual.field('моделей', len(mine)) + "\n" + visual.field('страница', f"{page + 1}/{pages}")), rows

    @form.on_click(data='nd_models')
    async def open_models(click):
        if st['busy']:
            await click.answer("Подождите, идёт ответ")
            return
        await engine.llm.scan_local()
        if len(_conns()) == 1:
            text, rows = picker_models(0, 0)
        else:
            text, rows = picker_providers()
        await click.answer()
        await form.edit(text=text, buttons=rows)

    @form.on_click(data_startswith='nd_c:')
    async def open_provider(click):
        _, k, page = click.data.split(':')
        text, rows = picker_models(int(k), int(page))
        await click.answer()
        await form.edit(text=text, buttons=rows)

    @form.on_click(data_startswith='nd_m:')
    async def pick_model(click):
        models = engine.llm.models()
        i = int(click.data.split(':', 1)[1])
        if not (0 <= i < len(models)):
            await click.answer("Список изменился — откройте выбор заново")
            return
        try:
            engine.llm.select('chat', models[i]['ref'])
        except Exception as e:
            await click.answer(str(e)[:150], show_alert=True)
            return
        await click.answer(f"Выбрана: {models[i]['model']}")
        await show()

    @form.on_click(data='nd_back')
    async def back_to_chat(click):
        await click.answer()
        await show()

    # --- вопрос пользователя ---
    @form.on_input(data='send_message')
    async def send_message(inp):
        user_text = (inp.value or '').strip()
        if not user_text:
            return
        if st['busy']:
            return
        st['busy'] = True
        st['cancel'] = asyncio.Event()
        turn = st['turn'] + 1
        st['turn'] = turn
        live = {'text': '', 'last': 0.0}
        usage = {'ev': None}
        sums = {'in': 0, 'out': 0, 'steps': 0}
        tail_status = visual.wait('думаю…')

        plan0 = None
        try:
            conn, _m = engine.llm.parse_ref(engine.llm.selected('chat'))
            plan0 = engine.llm.plan_status(conn['id'])
            bal = await engine.llm.balance(conn['id'])
            _BAL['text'] = (bal or {}).get('text') or ''
        except Exception:
            pass
        from_tokens = max(1, len(user_text) // 3)
        st['blocks'].append(_code(turn, f"Вы: {_esc(user_text)}", _foot(extra=f"~{_n(from_tokens)} ток.", plan=plan0)))
        await show(tail_status, stop_buttons)

        async def confirm(name, args, kind='dangerous', note=''):
            """Опасный или тяжёлый инструмент: показываем, что он хочет сделать (и во сколько обойдётся), и ждём кнопку."""
            import json
            try:
                shown = json.dumps(args, ensure_ascii=False)
            except Exception:
                shown = str(args)
            st['confirm'] = asyncio.get_running_loop().create_future()
            await form.edit(text=_compose(header(), st['blocks'], (visual.warn(f"<b>{HEAVY} Тяжёлый инструмент — нужен ваш ответ</b>") + "\n"
                                                                   + visual.field('вызов', f"{_esc(name)}({_esc(shown[:300])})") + "\n"
                                                                   + visual.field('расход', _esc(note or 'большой объём данных'), code=False) + "\n"
                                                                   + visual.hint('может стоить много токенов; локальная модель может не потянуть')
                                                                   if kind == 'heavy' else
                                                                   visual.warn(f"<b>{DANGER} Опасный инструмент — нужен ваш ответ</b>") + "\n"
                                                                   + visual.field('вызов', f"{_esc(name)}({_esc(shown[:600])})"))),
                            buttons=[[(visual.btn('Разрешить'), 'nd_ok'), (visual.btn('Отклонить', 'close'), 'nd_no')]])
            try:
                return await asyncio.wait_for(st['confirm'], CONFIRM_TIMEOUT)
            except asyncio.TimeoutError:
                return False
            finally:
                st['confirm'] = None
                await show(tail_status, stop_buttons)

        async def on_event(ev):
            t = ev['type']
            if t == 'delta':
                live['text'] += ev['text']
                if time.time() - live['last'] > EDIT_INTERVAL:
                    live['last'] = time.time()
                    frame = _code(turn, f"Neuro: {_esc(live['text'])}{CURSOR}")
                    await form.edit(text=_compose(header(), st['blocks'] + [frame], ''), buttons=stop_buttons)
            elif t == 'usage':
                usage['ev'] = ev
                sums['in'] += ev['in']
                sums['out'] += ev['out']
                sums['steps'] += 1
            elif t == 'tool_start':
                try:
                    import json
                    a_s = json.dumps(ev['args'], ensure_ascii=False)
                except Exception:
                    a_s = str(ev['args'])
                await show(visual.wait(f"инструмент {ev['n']} выполняется…") + "\n" + visual.field('вызов', f"{_esc(ev['name'])}({_esc(a_s[:160])})"), stop_buttons)
            elif t == 'tool':
                u = usage['ev'] or {}
                args = ev['args']
                try:
                    import json
                    args_s = json.dumps(args, ensure_ascii=False)
                except Exception:
                    args_s = str(args)
                preview = ev['result'] if len(ev['result']) <= 400 else ev['result'][:400] + '…'
                body = f"{visual.visual('config')} {_esc(ev['name'])}({_esc(args_s[:200])})\n\n{visual.visual('result')} {_esc(preview)}"
                st['blocks'].append(_code(f"{turn}.{ev['n']}", body, _foot(u.get('in'), u.get('out'), u.get('plan'), u.get('estimated'))))
                live['text'] = ''
                await show(visual.wait('читаю результат и думаю…'), stop_buttons)
            elif t == 'answer':
                u = usage['ev'] or {}
                extra = f"за вопрос: {_n(sums['in'] + sums['out'])}" if sums['steps'] > 1 else ''     # были шаги с инструментами — показываем и общий расход
                st['blocks'].append(_code(turn, f"Neuro: {_esc(ev['text'])}", _foot(u.get('in'), u.get('out'), ev.get('plan') or u.get('plan'), u.get('estimated'), extra)))
            elif t == 'error':
                u = usage['ev'] or {}
                st['blocks'].append(_code(turn, f"Neuro: ⚠️ {_esc(ev['message'])}", _foot(plan=u.get('plan') or plan0)))

        try:
            res = await engine.neuro.run_turn(st['history'], user_text, surface='chat', on_event=on_event, cancel=st['cancel'], confirm=confirm, loaded=st['loaded'])
            st['loaded'] = set(res.loaded)
            if res.finish == 'cancelled':
                u = usage['ev'] or {}
                st['blocks'].append(_code(turn, f"Neuro: {_esc(res.text)} {visual.visual('close')} [остановлено]", _foot(u.get('in'), u.get('out'), u.get('plan'), u.get('estimated'))))
            elif res.finish == 'max_steps' and not any(b.endswith('</code></pre>') and 'Превышен' in b for b in st['blocks'][-1:]):
                st['blocks'].append(_code(turn, f"Neuro: {_esc(res.text)}"))
        except Exception as e:
            engine.tlog(f"Ошибка NeuroDash: {e!r}")
            st['blocks'].append(_code(turn, f"Neuro: ⚠️ {_esc(repr(e)[:400])}"))
        finally:
            st['busy'] = False
            st['cancel'] = None
        await show()


# ---------------------------------------------------------------------------
# .neurotool — инструменты
# ---------------------------------------------------------------------------

TOOL_HELP = (
    visual.title('Инструменты · формат', 'инструмент — Python-файл, описание в докстринге') + "\n\n"
    + visual.section('Шаблон') + "\n"
    "<pre><code class='language-python'>\"\"\"\nname: shout\ncategory: fun\ntitle: Крик\ndescription: возвращает текст ЗАГЛАВНЫМИ\n"
    "args: text: string\nexample: {\"text\": \"привет\"}\ndangerous: false\n\"\"\"\nasync def run(text: str = '') -> str:\n    return text.upper() + '!'</code></pre>\n\n"
    + visual.section('Поля') + "\n"
    + visual.item("<code>name</code>, <code>category</code> — латиница, цифры, _") + "\n"
    + visual.item("<code>description</code> — что делает (это видит модель)") + "\n"
    + visual.item(f"<code>dangerous: true</code> — {DANGER} перед запуском чат спросит подтверждение") + "\n"
    + visual.item(f"<code>heavy: ~3000 токенов</code> — {HEAVY} предупредит о стоимости и спросит разрешение") + "\n"
    + visual.item("внутри можно <code>import engine, db, cache, files</code>") + "\n\n"
    + visual.section('Команды') + "\n"
    + visual.item("<code>.nt add</code> — ответом на сообщение с кодом или на .py-файл") + "\n"
    + visual.item("<code>.nt add!</code> — заменить существующий") + "\n\n"
    + visual.hint('категории экономят токены: в запрос попадают только подходящие к вопросу')
)


def _tools():
    return engine.neuro.tools


def _tool_list_text() -> str:
    ts = _tools()
    cats = ts.categories()
    if not cats:
        return visual.title('Инструменты') + "\n" + visual.hint('инструментов нет — добавьте ответом на код командой <code>.nt add</code>')
    lines = [visual.title('Инструменты', f"категорий · {len(cats)}")]
    for c in cats:
        mark = '◉' if c['enabled'] else '○'
        lines.append("")
        lines.append(f"{visual.section(_esc(c['title']))}  <code>{c['id']}</code> · {mark} {c['enabled_tools']}/{c['count']}")
        for t in [x for x in ts.list_tools() if x['category'] == c['id']]:
            lines.append(visual.item(f"{'◉' if t['enabled'] else '○'} <code>{t['name']}</code>{' ' + DANGER if t['dangerous'] else ''}{' ' + HEAVY if t.get('heavy') else ''} — {_esc(t['description'][:70])}", 1))
    lines.append("")
    lines.append(visual.hint(f"◉ вкл · ○ выкл · {DANGER} опасный · {HEAVY} тяжёлый — подробнее: <code>.nt help</code>"))
    return '\n'.join(lines)


async def _send_tool_file(event, name):
    path = _tools().export(name)[0]
    meta = next((t for t in _tools().list_tools() if t['name'] == name), {})
    cap = (visual.title(_esc(meta.get('title', name))) + "\n" + visual.field('имя', name) + "\n"
           + visual.field('категория', _esc(meta.get('category', ''))) + "\n" + visual.hint('загрузить: ответьте на файл командой <code>.nt add</code>'))
    await engine.client.send_file(event.chat_id, str(path), caption=cap, parse_mode='html', force_document=True)


async def _send_all(event):
    paths = _tools().export()
    if not paths:
        await engine.answer(event, visual.hint('инструментов нет'))
        return
    tmp = Path(tempfile.mkdtemp(prefix='neuro_tools_')) / 'neuro_tools.zip'
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED) as zf:
        for p in paths:
            zf.write(p, f"{p.parent.name}/{p.name}")
    try:
        await engine.client.send_file(event.chat_id, str(tmp), caption=(visual.title('Инструменты NeuroDash', 'по папкам-категориям') + "\n" + visual.field('файлов', len(paths)) + "\n"
                                                                       + visual.hint('каждый файл можно загрузить обратно: <code>.nt add</code> ответом на него')),
                                      parse_mode='html', force_document=True)
    finally:
        try:
            tmp.unlink()
            tmp.parent.rmdir()
        except OSError:
            pass


async def _source_from_reply(event):
    """Код инструмента из reply: текст сообщения либо вложенный файл (.py / .txt)."""
    reply = await event.get_reply_message()
    if reply is None:
        return None, "Ответьте (reply) этой командой на сообщение с кодом инструмента или на .py-файл."
    if getattr(reply, 'file', None):
        if (reply.file.size or 0) > 40 * 1024:
            return None, "Файл больше 40 КБ — для инструмента это слишком много."
        tmpdir = tempfile.mkdtemp(prefix='neuro_tool_in_')
        try:
            path = await reply.download_media(file=tmpdir + os.sep)
            text = Path(path).read_text(encoding='utf-8', errors='replace')
        finally:
            for f in Path(tmpdir).glob('*'):
                try:
                    f.unlink()
                except OSError:
                    pass
            try:
                os.rmdir(tmpdir)
            except OSError:
                pass
    else:
        text = reply.raw_text or ''
    text = text.strip()
    if text.startswith('```'):
        text = text.split('\n', 1)[1] if '\n' in text else ''
        text = text.rsplit('```', 1)[0]
    return text.strip('\n') + '\n', None


@decorators.command('neurotool', 'nt')
async def neurotool_command(event):
    '''инструменты нейросети: меню, list, add (reply на код), send, sendall, del, on/off'''
    if not _core_ok():
        await engine.answer(event, NEED_CORE)
        return
    ts = _tools()
    args = engine.get_args().strip().split()
    sub = args[0].lower() if args else ''
    rest = args[1:]
    ok, bad = visual.ok, visual.err

    try:
        if sub in ('help', '?'):
            await engine.answer(event, TOOL_HELP)
        elif sub in ('list', 'ls'):
            await engine.answer(event, _tool_list_text())
        elif sub in ('add', 'add!', 'replace'):
            source, err = await _source_from_reply(event)
            if err:
                await engine.answer(event, bad(err))
                return
            meta = ts.add(source, replace=sub in ('add!', 'replace'))
            await engine.answer(event, ok('Инструмент сохранён') + "\n" + visual.field('имя', meta['name']) + "\n"
                                       + visual.field('категория', meta['category'])
                                       + ("\n" + visual.hint(f"{DANGER} опасный: будет спрашивать подтверждение") if meta['dangerous'] else ''))
        elif sub == 'send':
            if not rest:
                await engine.answer(event, bad('Укажите имя') + "\n" + visual.hint('пример: <code>.nt send calculator</code>'))
                return
            await _send_tool_file(event, rest[0])
            await event.delete()
        elif sub == 'sendall':
            await _send_all(event)
            await event.delete()
        elif sub in ('del', 'rm', 'delete'):
            if not rest:
                await engine.answer(event, bad('Укажите имя') + "\n" + visual.hint('пример: <code>.nt del имя</code>'))
                return
            ts.delete(rest[0])
            await engine.answer(event, ok('Инструмент удалён') + "\n" + visual.field('имя', _esc(rest[0])))
        elif sub in ('on', 'off'):
            if not rest:
                await engine.answer(event, bad('Укажите инструмент или категорию') + "\n" + visual.hint(f"пример: <code>.nt {sub} web</code>"))
                return
            target = rest[0].lower()
            is_cat = any(c['id'] == target for c in ts.categories())
            if is_cat:
                ts.set_enabled(category=target, enabled=sub == 'on')
            else:
                ts.path_of(target)          # проверка, что такой инструмент есть
                ts.set_enabled(name=target, enabled=sub == 'on')
            await engine.answer(event, ok(f"{'Категория' if is_cat else 'Инструмент'} {'включ' if sub == 'on' else 'выключ'}{'ена' if is_cat else 'ён'}") + "\n"
                                       + visual.field('имя', _esc(target)))
        elif sub == '':
            await _tool_menu(event)
        else:
            await engine.answer(event, bad('Неизвестная подкоманда') + "\n" + visual.hint('справка: <code>.nt help</code>'))
    except Exception as e:
        # ToolError и прочее: понятный текст без трассировки
        await engine.answer(event, bad('Ошибка') + f"\n<code>{_esc(str(e)[:600])}</code>")


async def _tool_menu(event):
    """Инлайн-меню: категории → инструменты → вкл/выкл, отправить, удалить."""
    if getattr(engine, '_aiogram_bot', None) is None:
        await engine.answer(event, _tool_list_text())
        return
    ts = _tools()
    form = await engine.create_inlineform(chat_id=event.chat_id, text=visual.title('Инструменты нейросети'), buttons=[[('…', 'x')]])
    S = {'view': 'root', 'cat': None, 'tool': None, 'confirm_del': False}

    def root():
        cats = ts.categories()
        rows = [[(f"{'◉' if c['enabled'] else '○'} {c['title']} · {c['enabled_tools']}/{c['count']}"[:48], f"nt_c:{c['id']}")] for c in cats]
        rows.append([(visual.btn('Отправить все (zip)'), 'nt_all')])
        return (visual.title('Инструменты нейросети', f"категорий · {len(cats)}") + "\n"
                + visual.item('категории экономят токены: в запрос попадают только подходящие') + "\n"
                + visual.item('ключи и модели — на сайте, вкладка neuro') + "\n\n"
                + visual.hint(f"◉ вкл · ○ выкл · {DANGER} опасный · {HEAVY} тяжёлый")), rows

    def category(cid):
        c = next((x for x in ts.categories() if x['id'] == cid), None)
        if c is None:
            return root()
        items = [t for t in ts.list_tools() if t['category'] == cid]
        rows = [[(f"{'◉' if t['enabled'] else '○'} {t['name']}{' ' + DANGER if t['dangerous'] else ''}{' ' + HEAVY if t.get('heavy') else ''}"[:48], f"nt_t:{t['name']}")] for t in items]
        rows.append([((visual.btn('Выключить категорию', 'off') if c['enabled'] else visual.btn('Включить категорию', 'on')), f"nt_ct:{cid}")])
        rows.append([(visual.btn('Назад', 'back'), 'nt_root')])
        return (visual.title(_esc(c['title']), _esc(c['description'])) + "\n"
                + visual.field('id', cid) + "\n" + visual.field('включено', f"{c['enabled_tools']}/{c['count']}")), rows

    def tool(name):
        t = next((x for x in ts.list_tools() if x['name'] == name), None)
        if t is None:
            return root()
        marks = ' · '.join(([f"{DANGER} опасный"] if t['dangerous'] else []) + ([f"{HEAVY} тяжёлый"] if t.get('heavy') else []))
        text = "\n".join([visual.title(_esc(t['title']), _esc(t['description'])),
                          visual.field('имя', t['name']),
                          visual.field('категория', _esc(t['category'])),
                          visual.field('статус', 'включён' if t['enabled'] else 'выключен', code=False)]
                         + ([visual.field('метки', marks, code=False)] if marks else [])
                         + ["", visual.section('Вызов'), f"<code>{_esc(t['name'])}({_esc(t['args'])})</code>"])
        rows = [[((visual.btn('Выкл', 'off') if t['enabled'] else visual.btn('Вкл', 'on')), f"nt_tt:{name}"), (visual.btn('Файл'), f"nt_s:{name}")]]
        rows.append([(visual.btn('Да, удалить') if S['confirm_del'] else visual.btn('Удалить', 'close'), f"nt_d:{name}")])
        rows.append([(visual.btn('Назад', 'back'), f"nt_c:{t['category']}")])
        return text, rows

    async def render(click=None):
        text, rows = {'root': root, 'cat': lambda: category(S['cat']), 'tool': lambda: tool(S['tool'])}[S['view']]()
        await form.edit(text=text, buttons=rows)

    @form.on_click(data='nt_root')
    async def _root(click):
        S.update(view='root', confirm_del=False)
        await click.answer()
        await render()

    @form.on_click(data_startswith='nt_c:')
    async def _cat(click):
        S.update(view='cat', cat=click.data.split(':', 1)[1], confirm_del=False)
        await click.answer()
        await render()

    @form.on_click(data_startswith='nt_t:')
    async def _tool(click):
        S.update(view='tool', tool=click.data.split(':', 1)[1], confirm_del=False)
        await click.answer()
        await render()

    @form.on_click(data_startswith='nt_ct:')
    async def _toggle_cat(click):
        cid = click.data.split(':', 1)[1]
        cur = next((c for c in ts.categories() if c['id'] == cid), None)
        if cur:
            ts.set_enabled(category=cid, enabled=not cur['enabled'])
        await click.answer("Готово")
        await render()

    @form.on_click(data_startswith='nt_tt:')
    async def _toggle_tool(click):
        name = click.data.split(':', 1)[1]
        cur = next((t for t in ts.list_tools() if t['name'] == name), None)
        if cur:
            ts.set_enabled(name=name, enabled=not cur['enabled'])
        await click.answer("Готово")
        await render()

    @form.on_click(data_startswith='nt_s:')
    async def _send(click):
        await click.answer("Отправляю файл…")
        await _send_tool_file(event, click.data.split(':', 1)[1])

    @form.on_click(data='nt_all')
    async def _send_everything(click):
        await click.answer("Собираю архив…")
        await _send_all(event)

    @form.on_click(data_startswith='nt_d:')
    async def _delete(click):
        name = click.data.split(':', 1)[1]
        if not S['confirm_del']:
            S['confirm_del'] = True
            await click.answer("Нажмите ещё раз, чтобы удалить")
            await render()
            return
        try:
            ts.delete(name)
            await click.answer("Удалено")
        except Exception as e:
            await click.answer(str(e)[:150], show_alert=True)
        S.update(view='root', confirm_del=False)
        await render()

    await render()
