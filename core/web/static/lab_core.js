/* FRX WebIDE Lab. Ядро конструктора: категории, каталог блоков и компилятор «проект → Python-модуль».
 * Не зависит от DOM (проверяется в Node). Редактор — lab.js.
 *
 * Модель проекта:
 *   { v: 1, meta: {name, module_name, version, description, author_extra, tg_url?, git_url?, locked_author?},
 *     configs: [{key, description, type, default, options}],
 *     scripts: [{id, type, side, p: {…параметры…}, blocks: [block…]}] }
 *   block = {id, t: 'id блока из LAB_BLOCKS', f: {поля}, off?: true, kids?: {имя_слота: [block…]}}
 *
 * Правила значений в полях (одинаковы для всех блоков — это единственное, что нужно запомнить):
 *   «число»      — число, либо {переменная};
 *   «значение»   — число → число, ровно {переменная} → её значение, всё остальное → текст;
 *   «текст»      — любая строка, в которую можно вставить {переменная} и {@встроенное} (в сообщениях значения
 *                  экранируются, чтобы чужой <b> не ломал разметку).
 */
'use strict';

/* ------------------------------------------------------------------ *
 * категории
 * ------------------------------------------------------------------ */

const LAB_CATS = [
  { id: 'msg', title: 'Сообщения', color: '#5b8def', icon: '✉' },
  { id: 'event', title: 'Событие', color: '#3fb6c9', icon: '◉' },
  { id: 'var', title: 'Переменные', color: '#f0974a', icon: 'x' },
  { id: 'list', title: 'Списки и словари', color: '#e0813a', icon: '☰' },
  { id: 'math', title: 'Математика', color: '#52b86b', icon: '∑' },
  { id: 'text', title: 'Текст', color: '#a073e6', icon: 'T' },
  { id: 'ctl', title: 'Управление', color: '#e6c14a', icon: '⑂' },
  { id: 'time', title: 'Время', color: '#e0705f', icon: '◷' },
  { id: 'data', title: 'Хранилище', color: '#4aa3df', icon: '▤' },
  { id: 'form', title: 'Формы', color: '#e069a8', icon: '▭' },
  { id: 'wait', title: 'Ожидание', color: '#b58a5a', icon: '⏳' },
  { id: 'file', title: 'Файлы', color: '#8a96a8', icon: '▣' },
  { id: 'chat', title: 'Чаты и люди', color: '#42c0a0', icon: '☺' },
  { id: 'dbg', title: 'Отладка', color: '#97a0ad', icon: '⚑' },
];
const LAB_CAT = Object.fromEntries(LAB_CATS.map((c) => [c.id, c]));

/* ------------------------------------------------------------------ *
 * скрипты (шапки): тип → контекст
 * ------------------------------------------------------------------ */

/* ctx: какие данные доступны внутри скрипта. Блоки объявляют `ctx` — в каких скриптах их можно использовать. */
const LAB_SCRIPT_TYPES = {
  command: { title: 'Команда', side: ['user', 'bot'], ctx: ['msg'], desc: 'срабатывает на команду (.hello в Telegram или /hello у бота)' },
  watcher: { title: 'Любое сообщение', side: ['user', 'bot'], ctx: ['msg'], desc: 'видит все сообщения (вотчер); можно отфильтровать по чату и направлению' },
  cycle: { title: 'По таймеру', side: ['user', 'bot'], ctx: [], desc: 'выполняется снова и снова через заданный интервал' },
  on_start: { title: 'При запуске', side: ['user', 'bot'], ctx: [], desc: 'один раз при запуске юзербота' },
  on_load: { title: 'При загрузке модуля', side: ['user', 'bot'], ctx: ['load'], desc: 'когда загружается любой модуль (в том числе этот)' },
  tg_event: { title: 'Событие Telegram', side: ['user'], ctx: ['tg'], desc: 'вошёл / вышел / добавили в чат, сменили название…' },
  bot_member: { title: 'Участник чата (бот)', side: ['bot'], ctx: ['tg'], desc: 'бот увидел изменение статуса участника' },
  on_error: { title: 'При ошибке', side: ['user', 'bot'], ctx: ['err'], desc: 'вызывается, когда в модулях упала функция' },
};
const LAB_INTERVAL_UNITS = [['s', 'секунд'], ['m', 'минут'], ['h', 'часов'], ['d', 'дней']];
const LAB_TG_KINDS = [['user_joined', 'вошёл в чат'], ['user_added', 'добавлен в чат'], ['user_left', 'вышел из чата'], ['user_kicked', 'исключён из чата'],
  ['new_title', 'сменили название'], ['new_photo', 'сменили фото'], ['created', 'чат создан']];

/* встроенные значения: {@имя} → [выражение Python, нужен ли контекст, описание]. Доступность зависит от ctx скрипта. */
const LAB_BUILTINS = {
  'текст': { py: '(event.raw_text or "")', ctx: ['msg'], desc: 'текст сообщения' },
  'аргументы': { py: '_args(event)', ctx: ['msg'], desc: 'всё, что написано после команды', helper: '_args' },
  'чат_id': { py: 'event.chat_id', ctx: ['msg', 'tg'], desc: 'id чата' },
  'отправитель_id': { py: 'event.sender_id', ctx: ['msg'], desc: 'id отправителя' },
  'сообщение_id': { py: 'event.id', ctx: ['msg'], desc: 'id сообщения' },
  'пользователь_id': { py: 'event.user_id', ctx: ['tg'], desc: 'id участника, с которым что-то произошло' },
  'кнопка': { py: 'click.data', ctx: ['click'], desc: 'callback data нажатой кнопки' },
  'кликнул_id': { py: '(click.from_user.id if click.from_user else 0)', ctx: ['click'], desc: 'id нажавшего' },
  'кликнул_имя': { py: '_uname(click.from_user)', ctx: ['click'], desc: 'имя нажавшего', helper: '_uname' },
  'введено': { py: 'inp.value', ctx: ['input'], desc: 'текст, который ввели в поле' },
  'ввёл_id': { py: '(inp.from_user.id if inp.from_user else 0)', ctx: ['input'], desc: 'id ввёдшего' },
  'ошибка': { py: 'error_text', ctx: ['err'], desc: 'текст ошибки' },
  'функция': { py: '(context or {}).get("function", "")', ctx: ['err'], desc: 'функция, в которой упало' },
  'модуль': { py: '(context or {}).get("module", "")', ctx: ['err'], desc: 'модуль, в котором упало' },
  'загружен_имя': { py: 'info.name', ctx: ['load'], desc: 'название загруженного модуля' },
  'загружен_id': { py: 'info.module_name', ctx: ['load'], desc: 'MODULE_NAME загруженного модуля' },
  'загружен_версия': { py: 'info.version', ctx: ['load'], desc: 'версия загруженного модуля' },
  'время': { py: '_now("%H:%M:%S")', ctx: [], desc: 'текущее время ЧЧ:ММ:СС', helper: '_now' },
  'дата': { py: '_now("%d.%m.%Y")', ctx: [], desc: 'текущая дата ДД.ММ.ГГГГ', helper: '_now' },
  'метка': { py: 'int(time.time())', ctx: [], desc: 'метка времени Unix', need: 'time' },
};

/* ------------------------------------------------------------------ *
 * вспомогательные функции Python, которые подключаются в модуль только если нужны
 * ------------------------------------------------------------------ */

const LAB_HELPERS = {
  _s: ['def _s(v):', '    """Значение → текст (None → пусто, 3.0 → 3, True → да)."""',
    '    if v is None:', '        return ""', '    if isinstance(v, bool):', '        return "да" if v else "нет"',
    '    if isinstance(v, float) and v == int(v) and abs(v) < 1e15:', '        return str(int(v))', '    if isinstance(v, (list, tuple)):', '        return ", ".join(_s(x) for x in v)',
    '    return str(v)'],
  _h: ['def _h(v):', '    """Значение → безопасный для HTML текст (текст с оформлением из Lab — как есть)."""',
    '    if isinstance(v, _Html):', '        return str(v)', '    return _html.escape(_s(v), quote=False)'],
  _Html: ['class _Html(str):', '    """Текст с оформлением (HTML Telegram): в сообщения вставляется как есть, а не экранируется."""'],
  _cat: ['def _cat(a, b):', '    """Склеить тексты; если хоть один с оформлением — результат тоже с оформлением."""',
    '    if isinstance(a, _Html) or isinstance(b, _Html):', '        return _Html(_h(a) + _h(b))', '    return _s(a) + _s(b)'],
  _n: ['def _n(v):', '    """Значение → число (текст вида «5» и «2,5» тоже подойдёт)."""',
    '    if isinstance(v, bool):', '        return int(v)', '    if isinstance(v, (int, float)):', '        return v', '    if v is None or v == "":', '        return 0',
    '    try:', '        t = str(v).strip().replace(",", ".")', '        f = float(t)', '        return int(f) if f == int(f) and "." not in t and "e" not in t.lower() else f',
    '    except (ValueError, OverflowError):', '        raise ValueError(f"«{v}» — не число")'],
  _cmp: ['def _cmp(a, op, b):', '    """Сравнение: если обе стороны — числа, сравниваем как числа, иначе как текст."""',
    '    def num(x):', '        if isinstance(x, bool):', '            return None', '        if isinstance(x, (int, float)):', '            return x',
    '        try:', '            return float(str(x).strip().replace(",", "."))', '        except ValueError:', '            return None',
    '    if op in ("==", "!=", ">", "<", ">=", "<="):', '        x, y = num(a), num(b)', '        if x is None or y is None:', '            x, y = _s(a), _s(b)',
    '        return {"==": x == y, "!=": x != y, ">": x > y, "<": x < y, ">=": x >= y, "<=": x <= y}[op]',
    '    sa, sb = _s(a), _s(b)', '    if op == "in":', '        return sb.lower() in sa.lower() if not isinstance(a, (list, tuple, dict)) else b in a',
    '    if op == "not in":', '        return not _cmp(a, "in", b)', '    if op == "starts":', '        return sa.lower().startswith(sb.lower())',
    '    if op == "ends":', '        return sa.lower().endswith(sb.lower())', '    if op == "regex":', '        return re.search(sb, sa) is not None',
    '    if op == "empty":', '        return a is None or a == "" or a == [] or a == {}', '    if op == "filled":', '        return not _cmp(a, "empty", None)',
    '    if op == "isnum":', '        return num(a) is not None', '    raise ValueError(f"неизвестное сравнение {op}")'],
  _args: ['def _args(event):', '    """Всё, что написано после команды."""', '    parts = (event.raw_text or "").split(maxsplit=1)', '    return parts[1].strip() if len(parts) > 1 else ""'],
  _uname: ['def _uname(u):', '    """Имя пользователя для показа."""', '    if u is None:', '        return ""',
    '    name = " ".join(p for p in (getattr(u, "first_name", None), getattr(u, "last_name", None)) if p)',
    '    return name or getattr(u, "username", None) or str(getattr(u, "id", ""))'],
  _now: ['def _now(fmt):', '    return _dt.datetime.now().strftime(fmt)'],
  _word: ['def _word(text, n):', '    """n-е слово (с 1) или пусто."""', '    parts = _s(text).split()', '    return parts[n - 1] if 1 <= n <= len(parts) else ""'],
  _item: ['def _item(seq, n):', '    """Элемент списка по номеру с 1 (отрицательный — с конца); нет такого — пусто."""', '    try:', '        n = int(n)', '        return seq[n - 1] if n > 0 else seq[n]',
    '    except (IndexError, TypeError, ValueError):', '        return ""'],
  _dur: ['def _dur(sec):', '    """Секунды → «2 д 3 ч 5 мин»."""', '    sec = int(abs(sec))', '    out = []', '    for unit, n in (("д", 86400), ("ч", 3600), ("мин", 60), ("с", 1)):',
    '        if sec >= n:', '            out.append(f"{sec // n} {unit}")', '            sec %= n', '    return " ".join(out) or "0 с"'],
  _numfmt: ['def _numfmt(x, digits, sep):', '    x = _n(x)', '    t = f"{x:,.{int(digits)}f}"', '    return t.replace(",", sep) if sep != "," else t'],
  _sent: ['_SENT = set()', '', '', 'async def _sent(coro):', '    """Отправляет сообщение и запоминает его: свои же сообщения юзербот получает как новые, ожидание их пропускает."""',
    '    m = await coro', '    mid = (getattr(m, "chat_id", None), getattr(m, "id", None))', '    if mid[1] is not None:', '        if len(_SENT) > 500:', '            _SENT.clear()', '        _SENT.add(mid)', '    return m'],
  _wait: ['async def _wait(event, timeout, onlyme):', '    """Ждёт сообщение, пропуская те, что отправил сам скрипт."""', '    while True:',
    '        kw = {"user_id": event.sender_id} if onlyme else {}', '        w = await engine.wait_message(timeout=timeout, **kw)', '        if (w.chat_id, w.id) not in _SENT:', '            return w'],
  _flat: ['def _flat(v):', '    return list(v) if isinstance(v, (list, tuple)) else ([] if v in (None, "") else [v])'],
};
const LAB_HELPER_DEPS = { _h: ['_s', '_Html'], _cat: ['_h', '_s', '_Html'], _cmp: ['_s'], _n: [], _numfmt: ['_n'], _uname: [], _wait: ['_sent'] };
const LAB_MODULES_FOR = { _h: 'html', _cmp: 're', _now: 'datetime' };

/* ------------------------------------------------------------------ *
 * вспомогательное
 * ------------------------------------------------------------------ */

const labPyStr = (s) => JSON.stringify(String(s));                       // JSON-строка — корректный литерал Python
const labPyNum = (n) => (Number.isFinite(n) ? String(n) : '0');
const LAB_IDENT = /^[A-Za-zА-Яа-яЁё_][\wА-Яа-яЁё]{0,31}$/;
const LAB_NUM = /^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/;
const LAB_VARREF = /^\{([^{}@]+)\}$/;
let LAB_ICON_KEYS = new Set();                                          // ключи visual (заполняет lab.js после загрузки списка)
let labIdSeq = 0;
const labId = () => 'b' + (++labIdSeq).toString(36) + Math.random().toString(36).slice(2, 5);
const labMangle = (name) => 'v_' + name;
const labSlug = (s, fallback) => {
  const t = String(s || '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return t && !/^\d/.test(t) ? t : fallback;
};

/* ------------------------------------------------------------------ *
 * каталог блоков
 *   fields: [{k, type, label, def, ...}]  type: msg | val | num | out | in | sel | bool | conds | buttons | lines | data
 *   slots:  ['body'] — вложенные блоки;  ctx: в каких скриптах доступен ([] = везде);  side: 'user' | 'bot' | undefined
 *   build(b, c): добавляет строки Python через c.line(), читает поля через c.val/c.f, задаёт выходные переменные через c.out
 * ------------------------------------------------------------------ */

const LAB_BLOCKS = {};
function labDef(id, cat, title, def) { LAB_BLOCKS[id] = { id, cat, title, fields: [], ...def }; }

const F = {
  msg: (k, label, def, o) => ({ k, type: 'msg', label, def: def === undefined ? '' : def, ...(o || {}) }),
  val: (k, label, def, o) => ({ k, type: 'val', label, def: def === undefined ? '' : def, ...(o || {}) }),
  num: (k, label, def, o) => ({ k, type: 'num', label, def: def === undefined ? '' : def, ...(o || {}) }),
  out: (k, label, def, o) => ({ k, type: 'out', label: label || 'в переменную', def: def || 'результат', ...(o || {}) }),
  in: (k, label, def, o) => ({ k, type: 'in', label, def: def || '', ...(o || {}) }),
  sel: (k, label, options, def, o) => ({ k, type: 'sel', label, options, def: def === undefined ? options[0][0] : def, ...(o || {}) }),
  bool: (k, label, def, o) => ({ k, type: 'bool', label, def: !!def, ...(o || {}) }),
};

/* ---- как отвечать в текущем контексте ---- */
const labSend = (c, call) => { if (c.side === 'bot') return `await ${call}`; c.helper('_sent'); return `await _sent(${call})`; };
const labReplyExpr = (c, expr, style) => {
  const body = style ? `${style[0]}${expr}${style[1]}` : expr;
  return c.side === 'bot' ? `await event.reply(${body})` : labSend(c, `engine.answer(event, ${body})`);
};

/* ====== Сообщения ====== */
labDef('answer', 'msg', 'Ответить', { ctx: ['msg'], hint: 'В Telegram меняет ваше сообщение с командой на ответ; у бота — отвечает реплаем.', fields: [F.msg('text', 'текст', 'Привет!')],
  build(b, c) { c.line(labReplyExpr(c, c.val(b.f.text, 'msg'))); } });
labDef('reply_new', 'msg', 'Ответить новым сообщением', { ctx: ['msg'], fields: [F.msg('text', 'текст', 'Готово')],
  build(b, c) { c.line(c.side === 'bot' ? `await event.reply(${c.val(b.f.text, 'msg')})` : labSend(c, `engine.reply(event, ${c.val(b.f.text, 'msg')})`)); } });
labDef('say_here', 'msg', 'Написать в этот чат', { ctx: ['msg'], hint: 'Новое сообщение в этом же чате, без ответа на конкретное.', fields: [F.msg('text', 'текст', 'Привет!')],
  build(b, c) { c.line(c.side === 'bot' ? `await event.respond(${c.val(b.f.text, 'msg')})` : labSend(c, `engine.noreply(event, ${c.val(b.f.text, 'msg')})`)); } });
labDef('answer_style', 'msg', 'Ответить с оформлением', { ctx: ['msg'], fields: [F.sel('style', 'стиль', [['b', 'жирный'], ['i', 'курсив'], ['code', 'моноширинный'], ['pre', 'блок кода'], ['u', 'подчёркнутый'], ['s', 'зачёркнутый'], ['spoiler', 'спойлер'], ['quote', 'цитата']]), F.msg('text', 'текст', 'Важное')],
  build(b, c) {
    const tag = { b: 'b', i: 'i', code: 'code', pre: 'pre', u: 'u', s: 's', spoiler: 'tg-spoiler', quote: 'blockquote' }[b.f.style] || 'b';
    c.line(labReplyExpr(c, c.val(b.f.text, 'msg'), [`"<${tag}>" + `, ` + "</${tag}>"`]));
  } });
labDef('send_chat', 'msg', 'Отправить в чат', { fields: [F.val('chat', 'чат (id, @username или me)', 'me'), F.msg('text', 'текст', 'Привет!')],
  build(b, c) { const call = `${c.side === 'bot' ? 'engine.bot' : 'engine'}.send_message(${c.chatRef(b.f.chat)}, ${c.val(b.f.text, 'msg')})`; c.line(c.side === 'bot' ? `await ${call}` : labSend(c, call)); } });
labDef('send_me', 'msg', 'Отправить себе в Избранное', { side: 'user', fields: [F.msg('text', 'текст', 'Заметка')],
  build(b, c) { c.line(labSend(c, `engine.send_message("me", ${c.val(b.f.text, 'msg')})`)); } });
labDef('send_owner', 'msg', 'Написать владельцу от бота', { fields: [F.msg('text', 'текст', 'Уведомление')],
  build(b, c) { c.line(`await engine.bot_send_to_owner(${c.val(b.f.text, 'msg')})`); } });
labDef('delete_msg', 'msg', 'Удалить это сообщение', { ctx: ['msg'], fields: [], build(b, c) { c.line('await event.delete()'); } });
labDef('delete_reply', 'msg', 'Удалить сообщение, на которое ответили', { ctx: ['msg'], side: 'user', fields: [],
  build(b, c) { c.line('_r = await event.get_reply_message()'); c.line('if _r:'); c.ind(() => c.line('await _r.delete()')); } });
labDef('pin_msg', 'msg', 'Закрепить это сообщение', { ctx: ['msg'], side: 'user', fields: [], build(b, c) { c.line('await event.message.pin()'); } });
labDef('forward_msg', 'msg', 'Переслать это сообщение', { ctx: ['msg'], side: 'user', fields: [F.val('chat', 'куда (id, @username или me)', 'me')],
  build(b, c) { c.line(`await event.message.forward_to(${c.chatRef(b.f.chat)})`); } });
labDef('reply_to_text', 'msg', 'Ответить на сообщение, на которое ответили', { ctx: ['msg'], side: 'user', fields: [F.msg('text', 'текст', 'Ответ')],
  build(b, c) { c.line('_r = await event.get_reply_message()'); c.line('if _r:'); c.ind(() => c.line(labSend(c, `engine.reply(_r, ${c.val(b.f.text, 'msg')})`))); } });

/* ====== Событие ====== */
labDef('get_args', 'event', 'Аргументы команды', { ctx: ['msg'], hint: 'Текст после команды: «.cmd что это» → «что это».', fields: [F.out('var', 'в переменную', 'аргументы')],
  build(b, c) { c.set(b.f.var, '_args(event)'); c.helper('_args'); } });
labDef('arg_word', 'event', 'N-е слово аргументов', { ctx: ['msg'], fields: [F.num('n', 'номер слова', '1'), F.out('var', 'в переменную', 'слово')],
  build(b, c) { c.set(b.f.var, `_word(_args(event), ${c.val(b.f.n, 'num')})`); c.helper('_word'); c.helper('_args'); } });
labDef('args_list', 'event', 'Аргументы — списком слов', { ctx: ['msg'], fields: [F.out('var', 'в переменную', 'слова')],
  build(b, c) { c.set(b.f.var, '_args(event).split()'); c.helper('_args'); } });
labDef('msg_text', 'event', 'Текст сообщения', { ctx: ['msg'], fields: [F.out('var', 'в переменную', 'текст')], build(b, c) { c.set(b.f.var, '(event.raw_text or "")'); } });
labDef('chat_info', 'event', 'Тип чата', { ctx: ['msg'], hint: 'личный / группа / канал', fields: [F.out('var', 'в переменную', 'тип_чата')],
  build(b, c) { c.set(b.f.var, '("личный" if event.is_private else "группа" if event.is_group else "канал")'); } });
labDef('is_my_msg', 'event', 'Это моё сообщение?', { ctx: ['msg'], fields: [F.out('var', 'в переменную', 'моё')], build(b, c) { c.set(b.f.var, 'bool(event.out)'); } });
labDef('has_media', 'event', 'Есть вложение?', { ctx: ['msg'], fields: [F.out('var', 'в переменную', 'есть_файл')],
  build(b, c) { c.set(b.f.var, c.side === 'bot' ? 'bool(event.message.photo or event.message.document or event.message.video or event.message.voice or event.message.audio)' : 'bool(event.media)'); } });
labDef('has_reply', 'event', 'Это ответ на сообщение?', { ctx: ['msg'], fields: [F.out('var', 'в переменную', 'есть_ответ')], build(b, c) { c.set(b.f.var, 'bool(event.is_reply)'); } });
labDef('reply_text', 'event', 'Текст сообщения, на которое ответили', { ctx: ['msg'], side: 'user', fields: [F.out('var', 'в переменную', 'текст_ответа'), F.out('who', 'id автора (необязательно)', '', { optional: true })],
  build(b, c) {
    c.line('_r = await event.get_reply_message()');
    c.set(b.f.var, '((_r.raw_text or "") if _r else "")');
    if (b.f.who) c.set(b.f.who, '(_r.sender_id if _r else 0)');
  } });
labDef('event_sense', 'event', 'Свойство сообщения', { ctx: ['msg'], fields: [F.sel('prop', 'что узнать', [['chat_id', 'id чата'], ['sender_id', 'id отправителя'], ['id', 'id сообщения'], ['len', 'длина текста'], ['words', 'число слов']]), F.out('var', 'в переменную', 'свойство')],
  build(b, c) { c.set(b.f.var, { chat_id: 'event.chat_id', sender_id: 'event.sender_id', id: 'event.id', len: 'len(event.raw_text or "")', words: 'len((event.raw_text or "").split())' }[b.f.prop] || 'None'); } });

/* ====== Переменные ====== */
labDef('set', 'var', 'Задать', { hint: 'Число, {переменная} или текст. «{имя}» внутри текста подставит значение.', fields: [F.out('var', 'переменная', 'x'), F.val('value', 'значение', '0')],
  build(b, c) { c.set(b.f.var, c.val(b.f.value, 'any')); } });
const LAB_FMT_RE = /<\/?(b|i|u|s|code|pre|tg-spoiler|blockquote|a)(\s[^>]*)?>/i;   // в тексте есть оформление (кнопка «Aa»)
labDef('set_text', 'var', 'Задать текстом', { hint: 'Оформление («Aa») сохраняется: переменная вставится в сообщение уже жирной, курсивом и т. д.',
  fields: [F.out('var', 'переменная', 'текст'), F.msg('value', 'текст', 'Привет!', { plain: true, fmt: true })],
  build(b, c) {
    if (LAB_FMT_RE.test(String(b.f.value || ''))) { c.set(b.f.var, `_Html(${c.val(b.f.value, 'msg')})`); c.helper('_Html'); c.helper('_h'); }
    else c.set(b.f.var, c.val(b.f.value, 'text'));
  } });
labDef('change', 'var', 'Изменить', { fields: [F.in('var', 'переменную'), F.sel('op', 'как', [['+', '+ прибавить'], ['-', '− вычесть'], ['*', '× умножить'], ['/', '÷ разделить']]), F.num('value', 'на', '1')],
  build(b, c) { c.set(b.f.var, `_n(${c.read(b.f.var)}) ${b.f.op} _n(${c.val(b.f.value, 'num', true)})`, true); c.helper('_n'); } });
labDef('inc', 'var', 'Увеличить на 1', { fields: [F.in('var', 'переменную')], build(b, c) { c.set(b.f.var, `_n(${c.read(b.f.var)}) + 1`, true); c.helper('_n'); } });
labDef('dec', 'var', 'Уменьшить на 1', { fields: [F.in('var', 'переменную')], build(b, c) { c.set(b.f.var, `_n(${c.read(b.f.var)}) - 1`, true); c.helper('_n'); } });
labDef('toggle_var', 'var', 'Переключить да/нет', { fields: [F.in('var', 'переменную')], build(b, c) { c.set(b.f.var, `not ${c.read(b.f.var)}`, true); } });
labDef('append_text', 'var', 'Дописать текст', { fields: [F.in('var', 'в переменную'), F.msg('value', 'текст', '!', { plain: true, fmt: true })],
  build(b, c) {
    const fmt = LAB_FMT_RE.test(String(b.f.value || ''));
    c.set(b.f.var, `_cat(${c.read(b.f.var)}, ${fmt ? `_Html(${c.val(b.f.value, 'msg')})` : c.val(b.f.value, 'text')})`, true);
    c.helper('_cat'); if (fmt) c.helper('_Html');
  } });
labDef('choose', 'var', 'Выбрать по условию', { hint: 'Как «если … то A иначе B» в одну строку.', fields: [{ k: 'conds', type: 'conds', label: 'если', def: [{ a: '{x}', op: '>', b: '0', join: 'and' }] }, F.out('var', 'в переменную', 'выбор'), F.val('a', 'то', 'да'), F.val('b', 'иначе', 'нет')],
  build(b, c) { c.set(b.f.var, `(${c.val(b.f.a, 'any')} if ${c.cond(b.f.conds)} else ${c.val(b.f.b, 'any')})`); } });
labDef('type_of', 'var', 'Тип значения', { fields: [F.in('var', 'переменной'), F.out('res', 'в переменную', 'тип')],
  build(b, c) { c.set(b.f.res, `("число" if isinstance(${c.read(b.f.var)}, (int, float)) and not isinstance(${c.read(b.f.var)}, bool) else "да/нет" if isinstance(${c.read(b.f.var)}, bool) else "список" if isinstance(${c.read(b.f.var)}, list) else "словарь" if isinstance(${c.read(b.f.var)}, dict) else "текст")`); } });

/* ====== Списки и словари ====== */
labDef('list_new', 'list', 'Создать список', { hint: 'Каждый элемент — с новой строки. {переменные} подставляются.', fields: [F.out('var', 'список', 'список'), { k: 'items', type: 'lines', label: 'элементы', def: 'раз\nдва\nтри' }],
  build(b, c) { const items = String(b.f.items || '').split('\n').filter((x) => x.trim() !== ''); c.set(b.f.var, '[' + items.map((x) => c.val(x, 'any')).join(', ') + ']'); } });
labDef('list_add', 'list', 'Добавить в список', { fields: [F.in('var', 'список'), F.val('value', 'элемент', 'новый')], build(b, c) { c.line(`${c.read(b.f.var)}.append(${c.val(b.f.value, 'any')})`); } });
labDef('list_insert', 'list', 'Вставить в список', { fields: [F.in('var', 'список'), F.num('pos', 'на место №', '1'), F.val('value', 'элемент', 'новый')],
  build(b, c) { c.line(`${c.read(b.f.var)}.insert(max(0, _n(${c.val(b.f.pos, 'num')}) - 1), ${c.val(b.f.value, 'any')})`); c.helper('_n'); } });
labDef('list_get', 'list', 'Элемент списка', { hint: 'Номер с 1; -1 — последний. Нет такого — пусто.', fields: [F.in('var', 'список'), F.num('pos', '№', '1'), F.out('res', 'в переменную', 'элемент')],
  build(b, c) { c.set(b.f.res, `_item(${c.read(b.f.var)}, _n(${c.val(b.f.pos, 'num')}))`); c.helper('_item'); c.helper('_n'); } });
labDef('list_set', 'list', 'Заменить элемент списка', { fields: [F.in('var', 'список'), F.num('pos', '№', '1'), F.val('value', 'новое значение', 'x')],
  build(b, c) { c.line(`${c.read(b.f.var)}[_n(${c.val(b.f.pos, 'num')}) - 1] = ${c.val(b.f.value, 'any')}`); c.helper('_n'); } });
labDef('list_remove_at', 'list', 'Удалить элемент по номеру', { fields: [F.in('var', 'список'), F.num('pos', '№', '1')],
  build(b, c) { c.line(`${c.read(b.f.var)}.pop(_n(${c.val(b.f.pos, 'num')}) - 1)`); c.helper('_n'); } });
labDef('list_remove', 'list', 'Удалить значение из списка', { fields: [F.in('var', 'список'), F.val('value', 'значение', 'x')],
  build(b, c) { c.line(`if ${c.val(b.f.value, 'any')} in ${c.read(b.f.var)}:`); c.ind(() => c.line(`${c.read(b.f.var)}.remove(${c.val(b.f.value, 'any')})`)); } });
labDef('list_len', 'list', 'Длина списка', { fields: [F.in('var', 'список'), F.out('res', 'в переменную', 'длина')], build(b, c) { c.set(b.f.res, `len(${c.read(b.f.var)})`); } });
labDef('list_has', 'list', 'Есть в списке?', { fields: [F.in('var', 'список'), F.val('value', 'значение', 'x'), F.out('res', 'в переменную', 'есть')], build(b, c) { c.set(b.f.res, `(${c.val(b.f.value, 'any')} in ${c.read(b.f.var)})`); } });
labDef('list_clear', 'list', 'Очистить список', { fields: [F.in('var', 'список')], build(b, c) { c.line(`${c.read(b.f.var)}.clear()`); } });
labDef('list_join', 'list', 'Склеить список в текст', { fields: [F.in('var', 'список'), F.msg('sep', 'разделитель', ', ', { plain: true }), F.out('res', 'в переменную', 'текст')],
  build(b, c) { c.set(b.f.res, `${c.val(b.f.sep, 'text')}.join(_s(x) for x in ${c.read(b.f.var)})`); c.helper('_s'); } });
labDef('list_sort', 'list', 'Сортировать список', { fields: [F.in('var', 'список'), F.sel('mode', 'как', [['num', 'как числа'], ['text', 'как текст']]), F.bool('rev', 'по убыванию')],
  build(b, c) { c.line(`${c.read(b.f.var)}.sort(key=${b.f.mode === 'num' ? '_n' : '_s'}${b.f.rev ? ', reverse=True' : ''})`); c.helper(b.f.mode === 'num' ? '_n' : '_s'); } });
labDef('list_shuffle', 'list', 'Перемешать список', { fields: [F.in('var', 'список')], build(b, c) { c.need('random'); c.line(`random.shuffle(${c.read(b.f.var)})`); } });
labDef('list_reverse', 'list', 'Развернуть список', { fields: [F.in('var', 'список')], build(b, c) { c.line(`${c.read(b.f.var)}.reverse()`); } });
labDef('list_unique', 'list', 'Убрать повторы', { fields: [F.in('var', 'список')], build(b, c) { c.set(b.f.var, `list(dict.fromkeys(${c.read(b.f.var)}))`, true); } });
labDef('list_random', 'list', 'Случайный элемент', { fields: [F.in('var', 'список'), F.out('res', 'в переменную', 'случайный')], build(b, c) { c.need('random'); c.set(b.f.res, `(random.choice(${c.read(b.f.var)}) if ${c.read(b.f.var)} else "")`); } });
labDef('list_stat', 'list', 'Статистика по числам', { fields: [F.in('var', 'список'), F.sel('op', 'что', [['sum', 'сумма'], ['min', 'минимум'], ['max', 'максимум'], ['avg', 'среднее']]), F.out('res', 'в переменную', 'итог')],
  build(b, c) {
    const L = `[_n(x) for x in ${c.read(b.f.var)}]`;
    c.set(b.f.res, { sum: `sum(${L})`, min: `(min(${L}) if ${L} else 0)`, max: `(max(${L}) if ${L} else 0)`, avg: `(sum(${L}) / len(${L}) if ${L} else 0)` }[b.f.op]); c.helper('_n');
  } });
labDef('list_slice', 'list', 'Часть списка', { fields: [F.in('var', 'список'), F.num('from', 'с №', '1'), F.num('to', 'по №', '3'), F.out('res', 'в переменную', 'часть')],
  build(b, c) { c.set(b.f.res, `${c.read(b.f.var)}[max(0, _n(${c.val(b.f.from, 'num')}) - 1):_n(${c.val(b.f.to, 'num')})]`); c.helper('_n'); } });
labDef('dict_new', 'list', 'Создать словарь', { fields: [F.out('var', 'словарь', 'словарь')], build(b, c) { c.set(b.f.var, '{}'); } });
labDef('dict_set', 'list', 'Словарь: записать', { fields: [F.in('var', 'словарь'), F.val('key', 'ключ', 'имя'), F.val('value', 'значение', 'x')], build(b, c) { c.line(`${c.read(b.f.var)}[_s(${c.val(b.f.key, 'any')})] = ${c.val(b.f.value, 'any')}`); c.helper('_s'); } });
labDef('dict_get', 'list', 'Словарь: прочитать', { fields: [F.in('var', 'словарь'), F.val('key', 'ключ', 'имя'), F.val('default', 'если нет', ''), F.out('res', 'в переменную', 'значение')],
  build(b, c) { c.set(b.f.res, `${c.read(b.f.var)}.get(_s(${c.val(b.f.key, 'any')}), ${c.val(b.f.default, 'any')})`); c.helper('_s'); } });
labDef('dict_del', 'list', 'Словарь: удалить ключ', { fields: [F.in('var', 'словарь'), F.val('key', 'ключ', 'имя')], build(b, c) { c.line(`${c.read(b.f.var)}.pop(_s(${c.val(b.f.key, 'any')}), None)`); c.helper('_s'); } });
labDef('dict_keys', 'list', 'Словарь: список ключей', { fields: [F.in('var', 'словарь'), F.out('res', 'в переменную', 'ключи')], build(b, c) { c.set(b.f.res, `list(${c.read(b.f.var)}.keys())`); } });
labDef('dict_has', 'list', 'Словарь: есть ключ?', { fields: [F.in('var', 'словарь'), F.val('key', 'ключ', 'имя'), F.out('res', 'в переменную', 'есть')], build(b, c) { c.set(b.f.res, `(_s(${c.val(b.f.key, 'any')}) in ${c.read(b.f.var)})`); c.helper('_s'); } });

/* ====== Математика ====== */
labDef('math_op', 'math', 'Вычислить', { fields: [F.num('a', 'A', '1'), F.sel('op', 'действие', [['+', '+'], ['-', '−'], ['*', '×'], ['/', '÷'], ['//', 'целое от ÷'], ['%', 'остаток'], ['**', 'степень'], ['min', 'меньшее'], ['max', 'большее']]), F.num('b', 'B', '2'), F.out('res', 'в переменную', 'итог')],
  build(b, c) {
    const A = `_n(${c.val(b.f.a, 'num')})`, B = `_n(${c.val(b.f.b, 'num')})`;
    const guard = (['/', '//', '%'].includes(b.f.op)) ? true : false;
    if (guard) { c.line(`if ${B} == 0:`); c.ind(() => c.line('raise ZeroDivisionError("деление на ноль")')); }
    c.set(b.f.res, ['min', 'max'].includes(b.f.op) ? `${b.f.op}(${A}, ${B})` : `${A} ${b.f.op} ${B}`); c.helper('_n');
  } });
labDef('math_fn', 'math', 'Функция от числа', { fields: [F.sel('fn', 'функция', [['abs', 'модуль'], ['round', 'округлить'], ['floor', 'вниз'], ['ceil', 'вверх'], ['sqrt', 'корень'], ['sin', 'синус'], ['cos', 'косинус'], ['ln', 'логарифм']]), F.num('a', 'от', '4'), F.out('res', 'в переменную', 'итог')],
  build(b, c) {
    c.need('math');
    const A = `_n(${c.val(b.f.a, 'num')})`;
    if (b.f.fn === 'sqrt') { c.line(`if ${A} < 0:`); c.ind(() => c.line('raise ValueError("корень из отрицательного числа")')); }
    if (b.f.fn === 'ln') { c.line(`if ${A} <= 0:`); c.ind(() => c.line('raise ValueError("логарифм определён только для положительных чисел")')); }
    c.set(b.f.res, { abs: `abs(${A})`, round: `round(${A})`, floor: `math.floor(${A})`, ceil: `math.ceil(${A})`, sqrt: `math.sqrt(${A})`, sin: `math.sin(${A})`, cos: `math.cos(${A})`, ln: `math.log(${A})` }[b.f.fn]); c.helper('_n');
  } });
labDef('round_to', 'math', 'Округлить до знаков', { fields: [F.num('a', 'число', '3.14159'), F.num('d', 'знаков после запятой', '2'), F.out('res', 'в переменную', 'итог')],
  build(b, c) { c.set(b.f.res, `round(_n(${c.val(b.f.a, 'num')}), int(_n(${c.val(b.f.d, 'num')})))`); c.helper('_n'); } });
labDef('random_int', 'math', 'Случайное целое', { fields: [F.num('lo', 'от', '1'), F.num('hi', 'до', '6'), F.out('res', 'в переменную', 'случайное')],
  build(b, c) { c.need('random'); c.set(b.f.res, `random.randint(int(min(_n(${c.val(b.f.lo, 'num')}), _n(${c.val(b.f.hi, 'num')}))), int(max(_n(${c.val(b.f.lo, 'num')}), _n(${c.val(b.f.hi, 'num')}))))`); c.helper('_n'); } });
labDef('random_float', 'math', 'Случайное дробное 0…1', { fields: [F.out('res', 'в переменную', 'случайное')], build(b, c) { c.need('random'); c.set(b.f.res, 'random.random()'); } });
labDef('percent', 'math', 'Процент от числа', { fields: [F.num('p', 'процентов', '10'), F.num('of', 'от числа', '200'), F.out('res', 'в переменную', 'итог')],
  build(b, c) { c.set(b.f.res, `_n(${c.val(b.f.of, 'num')}) * _n(${c.val(b.f.p, 'num')}) / 100`); c.helper('_n'); } });
labDef('clamp', 'math', 'Ограничить число', { fields: [F.num('a', 'число', '15'), F.num('lo', 'не меньше', '0'), F.num('hi', 'не больше', '10'), F.out('res', 'в переменную', 'итог')],
  build(b, c) { c.set(b.f.res, `max(_n(${c.val(b.f.lo, 'num')}), min(_n(${c.val(b.f.hi, 'num')}), _n(${c.val(b.f.a, 'num')})))`); c.helper('_n'); } });
labDef('format_num', 'math', 'Красиво записать число', { fields: [F.num('a', 'число', '1234567.891'), F.num('d', 'знаков после запятой', '2'), F.sel('sep', 'разделитель тысяч', [[' ', 'пробел'], [',', 'запятая'], ['_', 'подчёркивание'], ['', 'нет']]), F.out('res', 'в переменную', 'число_текстом')],
  build(b, c) { c.set(b.f.res, `_numfmt(${c.val(b.f.a, 'num')}, ${c.val(b.f.d, 'num')}, ${labPyStr(b.f.sep)})`); c.helper('_numfmt'); } });
labDef('to_number', 'math', 'Текст в число', { fields: [F.val('a', 'текст', '{@аргументы}'), F.out('res', 'в переменную', 'число'), F.bool('safe', 'если не число — 0, без ошибки', true)],
  build(b, c) {
    c.helper('_n');
    if (b.f.safe) { const v = labMangle(b.f.res || 'число'); c.line('try:'); c.ind(() => c.set(b.f.res, `_n(${c.val(b.f.a, 'any')})`)); c.line('except ValueError:'); c.ind(() => c.set(b.f.res, '0')); void v; }
    else c.set(b.f.res, `_n(${c.val(b.f.a, 'any')})`);
  } });
labDef('is_number', 'math', 'Это число?', { fields: [F.val('a', 'значение', '{@аргументы}'), F.out('res', 'в переменную', 'число_ли')], build(b, c) { c.set(b.f.res, `_cmp(${c.val(b.f.a, 'any')}, "isnum", None)`); c.helper('_cmp'); } });

/* ====== Текст ====== */
const txtOne = (id, title, label, expr, extra) => labDef(id, 'text', title, { fields: [F.val('a', label || 'текст', '{@аргументы}'), ...(extra || []), F.out('res', 'в переменную', 'текст')],
  build(b, c) { c.set(b.f.res, expr(c.str(b.f.a), b, c)); c.helper('_s'); } });
txtOne('txt_len', 'Длина текста', 'текст', (a) => `len(${a})`);
txtOne('txt_upper', 'В ЗАГЛАВНЫЕ', 'текст', (a) => `${a}.upper()`);
txtOne('txt_lower', 'в строчные', 'текст', (a) => `${a}.lower()`);
txtOne('txt_capital', 'С Заглавной буквы', 'текст', (a) => `${a}.capitalize()`);
txtOne('txt_title', 'Каждое Слово С Заглавной', 'текст', (a) => `${a}.title()`);
txtOne('txt_strip', 'Убрать пробелы по краям', 'текст', (a) => `${a}.strip()`);
txtOne('txt_reverse', 'Развернуть текст', 'текст', (a) => `${a}[::-1]`);
txtOne('txt_words', 'Сколько слов', 'текст', (a) => `len(${a}.split())`);
txtOne('txt_escape', 'Экранировать HTML', 'текст', (a) => `_h(${a})`);
LAB_BLOCKS.txt_escape.build = (b, c) => { c.set(b.f.res, `_h(${c.val(b.f.a, 'any')})`); c.helper('_h'); };
labDef('txt_replace', 'text', 'Заменить в тексте', { fields: [F.val('a', 'текст', '{@аргументы}'), F.val('from', 'что', 'а'), F.val('to', 'на', 'б'), F.out('res', 'в переменную', 'текст')],
  build(b, c) { c.set(b.f.res, `${c.str(b.f.a)}.replace(${c.str(b.f.from)}, ${c.str(b.f.to)})`); c.helper('_s'); } });
labDef('txt_slice', 'text', 'Часть текста', { hint: 'Позиции считаются с 1.', fields: [F.val('a', 'текст', '{@аргументы}'), F.num('from', 'с символа №', '1'), F.num('to', 'по символ №', '5'), F.out('res', 'в переменную', 'часть')],
  build(b, c) { c.set(b.f.res, `${c.str(b.f.a)}[max(0, _n(${c.val(b.f.from, 'num')}) - 1):_n(${c.val(b.f.to, 'num')})]`); c.helper('_s'); c.helper('_n'); } });
labDef('txt_find', 'text', 'Найти в тексте', { hint: 'Номер первого совпадения с 1; 0 — не найдено.', fields: [F.val('a', 'текст', '{@аргументы}'), F.val('what', 'что искать', 'слово'), F.out('res', 'в переменную', 'позиция')],
  build(b, c) { c.set(b.f.res, `${c.str(b.f.a)}.lower().find(${c.str(b.f.what)}.lower()) + 1`); c.helper('_s'); } });
labDef('txt_count', 'text', 'Сколько раз встречается', { fields: [F.val('a', 'текст', '{@аргументы}'), F.val('what', 'что', 'а'), F.out('res', 'в переменную', 'раз')],
  build(b, c) { c.set(b.f.res, `${c.str(b.f.a)}.lower().count(${c.str(b.f.what)}.lower())`); c.helper('_s'); } });
labDef('txt_split', 'text', 'Разбить текст на список', { fields: [F.val('a', 'текст', '{@аргументы}'), F.msg('sep', 'по разделителю (пусто — по пробелам)', ',', { plain: true }), F.out('res', 'в переменную', 'части')],
  build(b, c) { const sep = c.val(b.f.sep, 'text'); c.set(b.f.res, `[x.strip() for x in ${c.str(b.f.a)}.split(${sep} or None) if x.strip()]`); c.helper('_s'); } });
labDef('txt_lines', 'text', 'Разбить текст на строки', { fields: [F.val('a', 'текст', '{@аргументы}'), F.out('res', 'в переменную', 'строки')], build(b, c) { c.set(b.f.res, `${c.str(b.f.a)}.splitlines()`); c.helper('_s'); } });
labDef('txt_repeat', 'text', 'Повторить текст', { fields: [F.val('a', 'текст', '=-'), F.num('n', 'раз', '5'), F.out('res', 'в переменную', 'текст')],
  build(b, c) { c.set(b.f.res, `${c.str(b.f.a)} * max(0, min(int(_n(${c.val(b.f.n, 'num')})), 1000))`); c.helper('_s'); c.helper('_n'); } });
labDef('txt_pad', 'text', 'Выровнять текст по ширине', { fields: [F.val('a', 'текст', '{@аргументы}'), F.num('w', 'ширина', '10'), F.sel('how', 'как', [['l', 'слева'], ['r', 'справа'], ['c', 'по центру']]), F.out('res', 'в переменную', 'текст')],
  build(b, c) { const m = { l: 'ljust', r: 'rjust', c: 'center' }[b.f.how] || 'ljust'; c.set(b.f.res, `${c.str(b.f.a)}.${m}(int(_n(${c.val(b.f.w, 'num')})))`); c.helper('_s'); c.helper('_n'); } });
labDef('txt_contains', 'text', 'Текст содержит?', { fields: [F.val('a', 'текст', '{@текст}'), F.val('what', 'что', 'привет'), F.out('res', 'в переменную', 'содержит')],
  build(b, c) { c.set(b.f.res, `_cmp(${c.val(b.f.a, 'any')}, "in", ${c.val(b.f.what, 'any')})`); c.helper('_cmp'); } });
labDef('txt_regex', 'text', 'Найти по шаблону (regex)', { hint: 'Первое совпадение (или первая группа в скобках). Нет совпадения — пусто.', fields: [F.val('a', 'текст', '{@текст}'), F.val('pat', 'шаблон', '\\d+'), F.out('res', 'в переменную', 'найдено')],
  build(b, c) { c.need('re'); c.line(`_m = re.search(${c.str(b.f.pat)}, ${c.str(b.f.a)})`); c.set(b.f.res, '((_m.group(1) if _m.groups() else _m.group(0)) if _m else "")'); c.helper('_s'); } });
labDef('txt_regex_all', 'text', 'Найти все по шаблону', { fields: [F.val('a', 'текст', '{@текст}'), F.val('pat', 'шаблон', '\\d+'), F.out('res', 'в переменную', 'найденное')],
  build(b, c) { c.need('re'); c.set(b.f.res, `re.findall(${c.str(b.f.pat)}, ${c.str(b.f.a)})`); c.helper('_s'); } });
labDef('txt_regex_sub', 'text', 'Заменить по шаблону', { fields: [F.val('a', 'текст', '{@текст}'), F.val('pat', 'шаблон', '\\s+'), F.val('to', 'на', ' '), F.out('res', 'в переменную', 'текст')],
  build(b, c) { c.need('re'); c.set(b.f.res, `re.sub(${c.str(b.f.pat)}, ${c.str(b.f.to)}, ${c.str(b.f.a)})`); c.helper('_s'); } });
labDef('txt_char', 'text', 'Символ по номеру', { fields: [F.val('a', 'текст', '{@аргументы}'), F.num('n', '№', '1'), F.out('res', 'в переменную', 'символ')],
  build(b, c) { c.set(b.f.res, `_item(${c.str(b.f.a)}, _n(${c.val(b.f.n, 'num')}))`); c.helper('_s'); c.helper('_item'); c.helper('_n'); } });
labDef('txt_chars', 'text', 'Текст — списком символов', { fields: [F.val('a', 'текст', '{@аргументы}'), F.out('res', 'в переменную', 'символы')], build(b, c) { c.set(b.f.res, `list(${c.str(b.f.a)})`); c.helper('_s'); } });
labDef('txt_hash_pick', 'text', 'Случайный вариант из строк', { hint: 'Каждый вариант — с новой строки.', fields: [{ k: 'items', type: 'lines', label: 'варианты', def: 'да\nнет\nможет быть' }, F.out('res', 'в переменную', 'вариант')],
  build(b, c) { c.need('random'); const items = String(b.f.items || '').split('\n').filter((x) => x.trim() !== ''); if (!items.length) c.err('Добавьте хотя бы один вариант'); c.set(b.f.res, `random.choice([${items.map((x) => c.val(x, 'any')).join(', ')}] or [""])`); } });

/* ====== Управление ====== */
labDef('if', 'ctl', 'Если', { slots: ['body'], fields: [{ k: 'conds', type: 'conds', label: 'условие', def: [{ a: '{x}', op: '>', b: '0', join: 'and' }] }], chain: 'head',
  build(b, c) { c.line(`if ${c.cond(b.f.conds)}:`); c.kids(b, 'body'); } });
labDef('elif', 'ctl', 'Иначе, если', { slots: ['body'], fields: [{ k: 'conds', type: 'conds', label: 'условие', def: [{ a: '{x}', op: '==', b: '0', join: 'and' }] }], chain: 'mid',
  build(b, c) { c.line(`elif ${c.cond(b.f.conds)}:`); c.kids(b, 'body'); } });
labDef('else', 'ctl', 'Иначе', { slots: ['body'], fields: [], chain: 'tail', build(b, c) { c.line('else:'); c.kids(b, 'body'); } });
labDef('repeat', 'ctl', 'Повторить', { slots: ['body'], fields: [F.num('n', 'раз', '3'), F.out('i', 'номер повтора в', '', { optional: true })],
  build(b, c) { const i = b.f.i ? c.out(b.f.i) : '_i'; c.line(`for ${i} in range(1, min(int(_n(${c.val(b.f.n, 'num')})), 10000) + 1):`); c.markLoop(); c.kids(b, 'body'); c.helper('_n'); } });
labDef('while', 'ctl', 'Пока', { slots: ['body'], hint: 'На всякий случай цикл остановится после 10 000 повторов.', fields: [{ k: 'conds', type: 'conds', label: 'условие', def: [{ a: '{x}', op: '<', b: '10', join: 'and' }] }],
  build(b, c) {
    const g = '_g' + c.uid();
    c.line(`${g} = 0`); c.line(`while ${c.cond(b.f.conds)}:`); c.markLoop();
    c.ind(() => { c.line(`${g} += 1`); c.line(`if ${g} > 10000:`); c.ind(() => c.line('raise RuntimeError("цикл «Пока» повторился больше 10 000 раз — проверьте условие")')); c.line('await asyncio.sleep(0)'); c.kidsRaw(b, 'body', false); });
    c.need('asyncio');
  } });
labDef('foreach', 'ctl', 'Для каждого из списка', { slots: ['body'], fields: [F.in('list', 'список'), F.out('item', 'элемент в', 'элемент'), F.out('idx', 'номер в', '', { optional: true })],
  build(b, c) {
    const item = c.out(b.f.item);
    if (b.f.idx) { const idx = c.out(b.f.idx); c.line(`for ${idx}, ${item} in enumerate(list(${c.read(b.f.list)}), 1):`); } else c.line(`for ${item} in list(${c.read(b.f.list)}):`);
    c.markLoop(); c.kids(b, 'body');
  } });
labDef('for_range', 'ctl', 'Считать от и до', { slots: ['body'], fields: [F.out('var', 'переменная', 'i'), F.num('from', 'от', '1'), F.num('to', 'до (включительно)', '5'), F.num('step', 'шаг', '1')],
  build(b, c) { const v = c.out(b.f.var); c.line(`for ${v} in range(int(_n(${c.val(b.f.from, 'num')})), int(_n(${c.val(b.f.to, 'num')})) + (1 if _n(${c.val(b.f.step, 'num')}) > 0 else -1), int(_n(${c.val(b.f.step, 'num')})) or 1):`); c.markLoop(); c.kids(b, 'body'); c.helper('_n'); } });
labDef('break', 'ctl', 'Выйти из цикла', { fields: [], build(b, c) { if (!c.inLoop()) c.err('«Выйти из цикла» работает только внутри цикла'); c.line('break'); } });
labDef('continue', 'ctl', 'Следующий повтор', { fields: [], build(b, c) { if (!c.inLoop()) c.err('«Следующий повтор» работает только внутри цикла'); c.line('continue'); } });
labDef('stop', 'ctl', 'Остановить скрипт', { fields: [], build(b, c) { c.line('return'); } });
labDef('wait', 'ctl', 'Подождать', { fields: [F.num('sec', 'секунд', '1')], build(b, c) { c.need('asyncio'); c.line(`await asyncio.sleep(max(0, min(_n(${c.val(b.f.sec, 'num')}), 3600)))`); c.helper('_n'); } });
labDef('try', 'ctl', 'Попробовать', { slots: ['body'], hint: 'Если внутри что-то упадёт — выполнится блок «Если ошибка» сразу после этого.', fields: [], chain: 'try',
  build(b, c) { c.line('try:'); c.kids(b, 'body'); } });
labDef('except', 'ctl', 'Если ошибка', { slots: ['body'], fields: [F.out('err', 'текст ошибки в', 'ошибка', { optional: true })], chain: 'except',
  build(b, c) { c.line('except Exception as _e:'); c.ind(() => { if (b.f.err) c.set(b.f.err, 'str(_e)'); c.kidsRaw(b, 'body', !b.f.err); }); } });
labDef('raise', 'ctl', 'Вызвать ошибку', { hint: 'Попадёт в «При ошибке» и в лог.', fields: [F.msg('text', 'текст ошибки', 'Что-то пошло не так', { plain: true })], build(b, c) { c.line(`raise RuntimeError(${c.val(b.f.text, 'text')})`); } });
labDef('require', 'ctl', 'Остановиться, если', { hint: 'Если условие верно — скрипт сразу завершается (удобно для проверок).', fields: [{ k: 'conds', type: 'conds', label: 'условие', def: [{ a: '{@аргументы}', op: 'empty', b: '', join: 'and' }] }],
  build(b, c) { c.line(`if ${c.cond(b.f.conds)}:`); c.ind(() => c.line('return')); } });

/* ====== Время ====== */
labDef('now', 'time', 'Сейчас', { fields: [F.sel('fmt', 'формат', [['%d.%m.%Y %H:%M:%S', 'дата и время'], ['%d.%m.%Y', 'дата'], ['%H:%M:%S', 'время'], ['%H:%M', 'часы:минуты'], ['%Y-%m-%d', 'ГГГГ-ММ-ДД'], ['unix', 'метка Unix (число)']]), F.out('res', 'в переменную', 'сейчас')],
  build(b, c) { if (b.f.fmt === 'unix') { c.need('time'); c.set(b.f.res, 'int(time.time())'); } else { c.set(b.f.res, `_now(${labPyStr(b.f.fmt)})`); c.helper('_now'); } } });
labDef('time_fmt', 'time', 'Метка времени → текст', { fields: [F.num('ts', 'метка Unix', '{@метка}'), F.sel('fmt', 'формат', [['%d.%m.%Y %H:%M:%S', 'дата и время'], ['%d.%m.%Y', 'дата'], ['%H:%M:%S', 'время'], ['%H:%M', 'часы:минуты']]), F.out('res', 'в переменную', 'дата')],
  build(b, c) { c.need('datetime'); c.set(b.f.res, `_dt.datetime.fromtimestamp(_n(${c.val(b.f.ts, 'num')})).strftime(${labPyStr(b.f.fmt)})`); c.helper('_n'); } });
labDef('time_add', 'time', 'Сдвинуть метку времени', { fields: [F.num('ts', 'метка Unix', '{@метка}'), F.num('n', 'на', '1'), F.sel('unit', '', [['1', 'секунд'], ['60', 'минут'], ['3600', 'часов'], ['86400', 'дней']], '3600'), F.out('res', 'в переменную', 'метка_позже')],
  build(b, c) { c.set(b.f.res, `_n(${c.val(b.f.ts, 'num')}) + _n(${c.val(b.f.n, 'num')}) * ${Number(b.f.unit) || 1}`); c.helper('_n'); } });
labDef('time_diff', 'time', 'Разница меток времени', { fields: [F.num('a', 'от', '{@метка}'), F.num('b', 'до', '{@метка}'), F.sel('unit', 'в', [['1', 'секундах'], ['60', 'минутах'], ['3600', 'часах'], ['86400', 'днях']], '60'), F.out('res', 'в переменную', 'разница')],
  build(b, c) { c.set(b.f.res, `(_n(${c.val(b.f.b, 'num')}) - _n(${c.val(b.f.a, 'num')})) / ${Number(b.f.unit) || 1}`); c.helper('_n'); } });
labDef('time_human', 'time', 'Секунды словами', { fields: [F.num('sec', 'секунд', '3725'), F.out('res', 'в переменную', 'длительность')], build(b, c) { c.set(b.f.res, `_dur(_n(${c.val(b.f.sec, 'num')}))`); c.helper('_dur'); c.helper('_n'); } });
labDef('time_part', 'time', 'Часть даты', { fields: [F.sel('part', 'что', [['year', 'год'], ['month', 'месяц'], ['day', 'день'], ['hour', 'час'], ['minute', 'минута'], ['second', 'секунда'], ['weekday', 'день недели (1–7)']]), F.out('res', 'в переменную', 'часть')],
  build(b, c) { c.need('datetime'); c.set(b.f.res, b.f.part === 'weekday' ? '_dt.datetime.now().isoweekday()' : `_dt.datetime.now().${b.f.part}`); } });
labDef('weekday_name', 'time', 'Название дня недели', { fields: [F.out('res', 'в переменную', 'день')], build(b, c) { c.need('datetime'); c.set(b.f.res, '["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"][_dt.datetime.now().weekday()]'); } });
labDef('date_parse', 'time', 'Дата из текста → метка', { hint: 'Формат вроде 31.12.2026 18:30', fields: [F.val('a', 'текст', '31.12.2026 18:30'), F.sel('fmt', 'формат', [['%d.%m.%Y %H:%M', 'ДД.ММ.ГГГГ ЧЧ:ММ'], ['%d.%m.%Y', 'ДД.ММ.ГГГГ'], ['%Y-%m-%d', 'ГГГГ-ММ-ДД'], ['%H:%M', 'ЧЧ:ММ (сегодня)']]), F.out('res', 'в переменную', 'метка')],
  build(b, c) {
    c.need('datetime');
    c.line('try:'); c.ind(() => { if (b.f.fmt === '%H:%M') { c.line(`_p = _dt.datetime.strptime(${c.str(b.f.a)}.strip(), "%H:%M")`); c.set(b.f.res, '_dt.datetime.now().replace(hour=_p.hour, minute=_p.minute, second=0, microsecond=0).timestamp()'); } else c.set(b.f.res, `_dt.datetime.strptime(${c.str(b.f.a)}.strip(), ${labPyStr(b.f.fmt)}).timestamp()`); });
    c.line('except ValueError:'); c.ind(() => c.line(`raise ValueError("не получилось разобрать дату — ожидался формат ${b.f.fmt.replace(/%d/g, 'ДД').replace(/%m/g, 'ММ').replace(/%Y/g, 'ГГГГ').replace(/%H/g, 'ЧЧ').replace(/%M/g, 'ММ')}")`));
    c.helper('_s');
  } });

/* ====== Хранилище ====== */
const scopeSel = () => F.sel('scope', 'где', [['module', 'только этот модуль'], ['global', 'общее для всех модулей']]);
const dbCall = (store, scope, op) => `${store}.${scope === 'global' ? 'g' : ''}${op}`;
for (const [store, title] of [['db', 'на диск'], ['cache', 'в память']]) {
  labDef(`${store}_set`, 'data', `Запомнить ${title}`, { hint: store === 'db' ? 'Сохраняется надолго, переживает перезапуск юзербота.' : 'Живёт до перезапуска юзербота.', fields: [F.msg('key', 'ключ', 'счётчик', { plain: true, single: true }), F.val('value', 'значение', '{x}'), scopeSel()],
    build(b, c) { c.line(`await ${dbCall(store, b.f.scope, 'set')}(${c.val(b.f.key, 'text')}, ${c.val(b.f.value, 'any')})`); } });
  labDef(`${store}_get`, 'data', `Вспомнить ${title}`, { fields: [F.msg('key', 'ключ', 'счётчик', { plain: true, single: true }), F.val('def', 'если нет — вот это', '0'), F.out('res', 'в переменную', 'значение'), scopeSel()],
    build(b, c) { c.set(b.f.res, `await ${dbCall(store, b.f.scope, 'get')}(${c.val(b.f.key, 'text')}, ${c.val(b.f.def, 'any')})`); } });
  labDef(`${store}_del`, 'data', `Забыть ${title}`, { fields: [F.msg('key', 'ключ', 'счётчик', { plain: true, single: true }), scopeSel()], build(b, c) { c.line(`await ${dbCall(store, b.f.scope, 'delete')}(${c.val(b.f.key, 'text')})`); } });
  labDef(`${store}_inc`, 'data', `Увеличить число ${title}`, { fields: [F.msg('key', 'ключ', 'счётчик', { plain: true, single: true }), F.num('by', 'на', '1'), F.out('res', 'новое значение в', 'счётчик', { optional: true }), scopeSel()],
    build(b, c) {
      const k = c.val(b.f.key, 'text'), g = dbCall(store, b.f.scope, 'get'), s = dbCall(store, b.f.scope, 'set');
      c.line(`_k = ${k}`); c.line(`_v = _n(await ${g}(_k, 0)) + _n(${c.val(b.f.by, 'num')})`); c.line(`await ${s}(_k, _v)`);
      if (b.f.res) c.set(b.f.res, '_v'); c.helper('_n');
    } });
  labDef(`${store}_push`, 'data', `Добавить в список ${title}`, { fields: [F.msg('key', 'ключ списка', 'список', { plain: true, single: true }), F.val('value', 'элемент', '{x}'), F.bool('uniq', 'без повторов'), scopeSel()],
    build(b, c) {
      const g = dbCall(store, b.f.scope, 'get'), s = dbCall(store, b.f.scope, 'set');
      c.line(`_k = ${c.val(b.f.key, 'text')}`); c.line(`_l = _flat(await ${g}(_k, []))`); c.line(`_x = ${c.val(b.f.value, 'any')}`);
      if (b.f.uniq) { c.line('if _x not in _l:'); c.ind(() => c.line('_l.append(_x)')); } else c.line('_l.append(_x)');
      c.line(`await ${s}(_k, _l)`); c.helper('_flat');
    } });
  labDef(`${store}_has`, 'data', `Есть ключ ${title}?`, { fields: [F.msg('key', 'ключ', 'счётчик', { plain: true, single: true }), F.out('res', 'в переменную', 'есть'), scopeSel()],
    build(b, c) { c.set(b.f.res, `(await ${dbCall(store, b.f.scope, 'get')}(${c.val(b.f.key, 'text')}, None) is not None)`); } });
}
labDef('cfg_get', 'data', 'Прочитать настройку', { hint: 'Настройки модуля — кнопка «настройки» вверху.', fields: [F.in('key', 'настройка', '', { cfg: true }), F.out('res', 'в переменную', 'настройка')],
  build(b, c) { if (!c.cfgExists(b.f.key)) c.err(`Настройки «${b.f.key}» нет — добавьте её в «настройки модуля»`); c.set(b.f.res, `await engine.get_config(${labPyStr(b.f.key)})`); } });
labDef('cfg_set', 'data', 'Изменить настройку', { hint: 'Для «списка» и «выбора нескольких» значение добавляют или убирают по одному.', fields: [F.in('key', 'настройка', '', { cfg: true }),
  F.sel('op', 'действие', [['edit', 'записать значение'], ['add', 'добавить в список'], ['del', 'убрать из списка'], ['to_default', 'вернуть по умолчанию']], 'edit'), F.val('value', 'значение', '{x}')],
  build(b, c) {
    if (!c.cfgExists(b.f.key)) c.err(`Настройки «${b.f.key}» нет — добавьте её в «настройки модуля»`);
    const op = b.f.op || 'edit', t = c.cfgType(b.f.key), multi = t === 'list' || t === 'choices';
    if (c.cfgExists(b.f.key) && op !== 'to_default') {
      if (multi && op === 'edit') c.err(`Настройка «${b.f.key}» — ${t === 'list' ? 'список' : 'выбор нескольких'}: выберите «добавить в список» или «убрать из списка»`);
      if (!multi && (op === 'add' || op === 'del')) c.err(`«${op === 'add' ? 'Добавить' : 'Убрать'}» работает только со списком и выбором нескольких, а «${b.f.key}» — не они`);
    }
    c.line(op === 'to_default' ? `await engine.edit_config(${labPyStr(b.f.key)}, "to_default")` : `await engine.edit_config(${labPyStr(b.f.key)}, ${labPyStr(op)}, ${c.val(b.f.value, 'any')})`);
  } });

/* ====== Формы ====== */
const FORM_ACTIONS = [['edit', 'сменить текст формы'], ['toast', 'всплывающее уведомление'], ['alert', 'окно-предупреждение'], ['close', 'закрыть форму'], ['none', 'ничего']];
function labFormButtons(raw, c) {                                  // макет кнопок → код Python (общий для «Создать форму» и «Изменить форму»)
  const rows = (raw || []).map((r) => r.filter((x) => x.label !== '' && x.data !== '')).filter((r) => r.length);
  if (!rows.length) c.err('Добавьте хотя бы одну кнопку');
  const seen = new Set();
  rows.flat().forEach((x) => { if (seen.has(x.kind + ':' + x.data)) c.err(`Кнопка «${x.label}»: «${x.data}» уже есть в этой форме`); seen.add(x.kind + ':' + x.data); if (new TextEncoder().encode(x.data).length > 64) c.err(`callback data «${x.data}» длиннее 64 байт`); });
  const st = (x) => (['primary', 'success', 'danger'].includes(x.style) ? x.style : '');
  const btn = (x) => (x.kind === 'input'
    ? `engine.InputButton(${c.val(x.label, 'text', true)}, ${labPyStr(x.data)}${st(x) ? `, style=${labPyStr(st(x))}` : ''})`
    : `(${c.val(x.label, 'text', true)}, ${labPyStr(x.data)}${st(x) ? `, ${labPyStr(st(x))}` : ''})`);
  return { rows, py: `[${rows.map((r) => '[' + r.map(btn).join(', ') + ']').join(', ')}]` };
}
labDef('form_new', 'form', 'Создать форму с кнопками', { side: 'user', ctx: ['msg'], hint: 'Нужен стационарный бот. Ниже — макет, как в Telegram. Кнопки обрабатываются блоками «Нажали кнопку».', fields: [
  F.out('var', 'форма', 'форма'), F.msg('text', 'текст', 'Выберите вариант'),
  { k: 'buttons', type: 'buttons', label: 'кнопки', def: [[{ label: 'Да', data: 'yes', kind: 'click' }, { label: 'Нет', data: 'no', kind: 'click' }]] },
  F.sel('who', 'кто может нажимать', [['me', 'только я'], ['chat', 'все в этом чате']]), F.num('timeout', 'закрыть после простоя, сек (0 — не закрывать)', '0')],
  build(b, c) {
    const kb = labFormButtons(b.f.buttons, c), rows = kb.rows;
    const v = c.out(b.f.var);
    const args = ['chat_id=event.chat_id', `text=${c.val(b.f.text, 'msg')}`, `buttons=${kb.py}`];
    if (b.f.who === 'chat') args.push('for_user=event.chat_id');
    const t = Math.trunc(Number(b.f.timeout)); if (t > 0) args.push(`timeout=${t}`);
    c.line(`${v} = await engine.create_inlineform(${args.join(', ')})`);
    c.forms[b.f.var] = rows.flat();
  } });
labDef('on_click', 'form', 'Нажали кнопку', { slots: ['body'], side: 'user', ctx: ['msg', 'click', 'input'], click: true, hint: 'Всё внутри выполнится, когда нажмут выбранную кнопку формы.', fields: [F.in('form', 'формы'), { k: 'data', type: 'data', label: 'кнопка' }],
  build(b, c) {
    const spec = c.forms[b.f.form];
    if (!spec) { c.err(`Форма «${b.f.form}» не создана выше блоком «Создать форму»`); return; }
    if (!spec.some((x) => x.data === b.f.data && x.kind !== 'input')) c.err(`У формы «${b.f.form}» нет обычной кнопки «${b.f.data}»`);
    c.line(`@${c.read(b.f.form)}.on_click(data=${labPyStr(b.f.data)})`);
    c.nested(`_on_${labSlug(b.f.form, 'form')}_${labSlug(b.f.data, 'btn')}_${c.uid()}`, 'click', b, 'body');
  } });
labDef('on_input', 'form', 'Ввели значение', { slots: ['body'], side: 'user', ctx: ['msg', 'click', 'input'], click: true, hint: 'Для кнопок типа «ввод значения». Введённое — {@введено}.', fields: [F.in('form', 'формы'), { k: 'data', type: 'data', label: 'поле', input: true }],
  build(b, c) {
    const spec = c.forms[b.f.form];
    if (!spec) { c.err(`Форма «${b.f.form}» не создана выше блоком «Создать форму»`); return; }
    if (!spec.some((x) => x.data === b.f.data && x.kind === 'input')) c.err(`У формы «${b.f.form}» нет кнопки ввода «${b.f.data}»`);
    c.line(`@${c.read(b.f.form)}.on_input(data=${labPyStr(b.f.data)})`);
    c.nested(`_on_input_${labSlug(b.f.form, 'form')}_${labSlug(b.f.data, 'field')}_${c.uid()}`, 'input', b, 'body');
  } });
labDef('form_edit', 'form', 'Изменить форму', { side: 'user', ctx: ['msg', 'click', 'input'], hint: 'Меняет текст формы. Кнопки: оставить прежние, убрать совсем или задать новые (на них можно ловить нажатия блоком «Нажали кнопку»).',
  fields: [F.in('form', 'форма'), F.msg('text', 'новый текст', 'Готово!'), F.sel('mode', 'кнопки', [['keep', 'оставить прежние'], ['none', 'убрать все'], ['new', 'задать новые']], 'keep'),
    { k: 'buttons', type: 'buttons', label: 'новые кнопки', def: [[{ label: 'Ок', data: 'ok', kind: 'click' }]], when: (f) => f.mode === 'new' }],
  build(b, c) {
    const mode = b.f.mode || (b.f.nobtn ? 'none' : 'keep'), form = c.read(b.f.form), text = c.val(b.f.text, 'msg');
    if (mode === 'new') {
      const kb = labFormButtons(b.f.buttons, c);
      c.forms[b.f.form] = (c.forms[b.f.form] || []).concat(kb.rows.flat());
      c.line(`await ${form}.edit(${text}, buttons=${kb.py})`);
    } else if (mode === 'none') c.line(`await ${form}.edit(${text}, buttons=None)`);
    else c.line(`await ${form}.edit(${text}, buttons=${form}._raw_buttons)`);          // «оставить»: edit() без buttons убирает клавиатуру
  } });
labDef('form_close', 'form', 'Закрыть форму', { side: 'user', ctx: ['msg', 'click', 'input'], fields: [F.in('form', 'форма')], build(b, c) { c.line(`await ${c.read(b.f.form)}.close()`); } });
labDef('click_answer', 'form', 'Ответить на нажатие', { side: 'user', ctx: ['click'], hint: 'Убирает «часики» на кнопке; можно показать уведомление.', fields: [F.msg('text', 'текст (пусто — просто снять часики)', 'Принято', { plain: true }), F.bool('alert', 'показать окном')],
  build(b, c) { c.line(`await click.answer(${String(b.f.text || '').trim() ? c.val(b.f.text, 'text') : ''}${b.f.alert ? ', show_alert=True' : ''})`.replace('(, ', '(').replace('()', '()')); } });

/* ====== Ожидание ====== */
labDef('ask', 'wait', 'Спросить и дождаться ответа', { slots: ['body'], ctx: ['msg'], side: 'user', hint: 'Задаёт вопрос и ждёт следующее сообщение в этом чате. Блоки внутри — если время вышло.', fields: [F.msg('q', 'вопрос', 'Как вас зовут?'), F.num('timeout', 'ждать, секунд', '30'), F.out('res', 'ответ в переменную', 'ответ'), F.bool('onlyme', 'только от того, кто спросил', true)],
  build(b, c) {
    const to = `max(1, min(_n(${c.val(b.f.timeout, 'num')}), 600))`;
    c.line(labSend(c, `engine.noreply(event, ${c.val(b.f.q, 'msg')})`));
    c.line('try:'); c.ind(() => { c.line(`_w = await _wait(event, ${to}, ${b.f.onlyme ? 'True' : 'False'})`); c.set(b.f.res, '(_w.raw_text or "")'); });
    c.line('except TimeoutError:'); c.kids(b, 'body'); c.helper('_n'); c.helper('_wait');
  } });
labDef('wait_msg', 'wait', 'Дождаться сообщения', { slots: ['body'], ctx: ['msg'], side: 'user', hint: 'Блоки внутри — если время вышло.', fields: [F.num('timeout', 'ждать, секунд', '30'), F.out('res', 'текст в переменную', 'сообщение'), F.out('who', 'id автора в', '', { optional: true }), F.bool('onlyme', 'только от автора команды', true)],
  build(b, c) {
    const to = `max(1, min(_n(${c.val(b.f.timeout, 'num')}), 600))`;
    c.line('try:'); c.ind(() => { c.line(`_w = await _wait(event, ${to}, ${b.f.onlyme ? 'True' : 'False'})`); c.set(b.f.res, '(_w.raw_text or "")'); if (b.f.who) c.set(b.f.who, '_w.sender_id'); });
    c.line('except TimeoutError:'); c.kids(b, 'body'); c.helper('_n'); c.helper('_wait');
  } });

/* ====== Файлы ====== */
labDef('file_make', 'file', 'Создать текстовый файл', { fields: [F.val('name', 'имя файла', 'report.txt'), F.msg('text', 'содержимое', 'Привет!', { plain: true }), F.out('res', 'файл в переменную', 'файл')],
  build(b, c) { c.set(b.f.res, `await files.create_file(type="text/plain", name=${c.str(b.f.name)}, text=${c.val(b.f.text, 'text')})`); c.helper('_s'); c.need('files'); } });
labDef('file_send', 'file', 'Отправить файл', { ctx: ['msg'], fields: [F.in('file', 'файл'), F.msg('text', 'подпись', '', { plain: true, fmt: true })],
  build(b, c) { c.line(labSend(c, `files.reply_file(event, ${c.read(b.f.file)}["id"], ${c.val(b.f.text, 'msg')})`)); c.need('files'); } });
labDef('file_from_reply', 'file', 'Скачать файл из ответа', { ctx: ['msg'], side: 'user', hint: 'Нужен ответ (reply) на сообщение с файлом.', fields: [F.out('res', 'файл в переменную', 'файл')],
  build(b, c) { c.set(b.f.res, 'await files.download_reply_file(event)'); c.need('files'); } });
labDef('file_read', 'file', 'Прочитать текст из файла', { fields: [F.in('file', 'файл'), F.out('res', 'текст в переменную', 'содержимое')],
  build(b, c) { c.line(`with open(${c.read(b.f.file)}["path"], encoding="utf-8", errors="replace") as _f:`); c.ind(() => c.set(b.f.res, '_f.read(1_000_000)')); } });
labDef('file_name', 'file', 'Свойство файла', { fields: [F.in('file', 'файл'), F.sel('prop', 'что', [['name', 'имя'], ['size', 'размер, байт'], ['type', 'тип']]), F.out('res', 'в переменную', 'свойство')], build(b, c) { c.set(b.f.res, `${c.read(b.f.file)}[${labPyStr(b.f.prop)}]`); } });

/* ====== Чаты и люди ====== */
labDef('get_sender', 'chat', 'Кто отправил', { ctx: ['msg'], hint: 'Имя, @username и id отправителя сообщения.', fields: [F.out('name', 'имя в', 'имя'), F.out('uname', '@username в', '', { optional: true }), F.out('id', 'id в', '', { optional: true })],
  build(b, c) {
    c.line('_u = await event.get_sender()'); c.set(b.f.name, '_uname(_u)'); c.helper('_uname');
    if (b.f.uname) c.set(b.f.uname, '(getattr(_u, "username", None) or "")'); if (b.f.id) c.set(b.f.id, '(getattr(_u, "id", 0) or 0)');
  } });
labDef('chat_title', 'chat', 'Название чата', { ctx: ['msg', 'tg'], side: 'user', fields: [F.out('res', 'в переменную', 'чат')],
  build(b, c) { c.line('_c = await event.get_chat()'); c.set(b.f.res, '(getattr(_c, "title", None) or _uname(_c))'); c.helper('_uname'); } });
labDef('find_user', 'chat', 'Найти человека по @username или id', { side: 'user', fields: [F.val('who', '@username или id', '@durov'), F.out('name', 'имя в', 'имя'), F.out('id', 'id в', 'id'), F.out('found', 'нашёлся? в', '', { optional: true })],
  build(b, c) {
    c.line('try:'); c.ind(() => { c.line(`_u = await engine.client.get_entity(${c.chatRef(b.f.who)})`); c.set(b.f.name, '_uname(_u)'); c.set(b.f.id, 'getattr(_u, "id", 0)'); if (b.f.found) c.set(b.f.found, 'True'); });
    c.line('except Exception:'); c.ind(() => { c.set(b.f.name, '""'); c.set(b.f.id, '0'); if (b.f.found) c.set(b.f.found, 'False'); }); c.helper('_uname');
  } });
labDef('my_info', 'chat', 'Это я', { side: 'user', fields: [F.out('name', 'имя в', 'моё_имя'), F.out('id', 'id в', 'мой_id', { optional: true })],
  build(b, c) { c.line('_me = await engine.client.get_me()'); c.set(b.f.name, '_uname(_me)'); if (b.f.id) c.set(b.f.id, '_me.id'); c.helper('_uname'); } });
labDef('tg_user', 'chat', 'Участник события', { ctx: ['tg'], side: 'user', hint: 'Кто вошёл / вышел / был добавлен.', fields: [F.out('name', 'имя в', 'имя'), F.out('uname', '@username в', '', { optional: true })],
  build(b, c) { c.line('_u = await event.get_user()'); c.set(b.f.name, '_uname(_u)'); if (b.f.uname) c.set(b.f.uname, '(getattr(_u, "username", None) or "")'); c.helper('_uname'); } });

/* ====== Отладка ====== */
labDef('log', 'dbg', 'Записать в лог', { hint: 'Строка появится в терминале юзербота (и в журнале Sandbox).', fields: [F.msg('text', 'текст', 'Сработало', { plain: true })], build(b, c) { c.line(`engine.tlog(${c.val(b.f.text, 'text')})`); } });
labDef('botlog', 'dbg', 'Отправить лог владельцу', { fields: [F.sel('level', 'уровень', [['info', 'инфо'], ['success', 'успех'], ['warning', 'предупреждение'], ['error', 'ошибка']]), F.msg('text', 'текст', 'Что-то случилось', { plain: true, fmt: true })],
  build(b, c) { c.line(`engine.botlog(${c.val(b.f.text, 'text')}, ${labPyStr(b.f.level)})`); } });
labDef('note', 'dbg', 'Заметка', { hint: 'Ничего не делает — пояснение для себя (попадёт комментарием в код).', fields: [F.msg('text', 'заметка', 'Здесь считаем очки', { plain: true })],
  build(b, c) { String(b.f.text || '').split('\n').forEach((l) => c.line('# ' + l.replace(/\r/g, ''))); } });
labDef('icon', 'dbg', 'Иконка', { fields: [{ k: 'key', type: 'icon', label: 'иконка', def: 'status_success' }, F.out('res', 'в переменную', 'иконка')],
  build(b, c) { c.need('visual'); c.set(b.f.res, `visual.visual(${labPyStr(b.f.key)})`); } });

/* ------------------------------------------------------------------ *
 * компилятор
 * ------------------------------------------------------------------ */

const LAB_CTX_FOR = (script) => {
  const t = LAB_SCRIPT_TYPES[script.type] || { ctx: [] };
  return t.ctx;
};

/* Доступен ли блок в скрипте: по ctx блока и по стороне (юзербот / бот). */
function labBlockAllowed(def, script, extraCtx) {
  const ctx = new Set([...(LAB_CTX_FOR(script)), ...(extraCtx || [])]);
  if (def.ctx && def.ctx.length && !def.ctx.some((x) => ctx.has(x))) return false;
  if (def.side && script.side === 'bot' && def.side === 'user') return false;
  return true;
}

function labParseTemplate(text) {                                       // «Привет, {имя}! {@время}» → [{lit}|{var}|{builtin}]
  const out = [];
  const re = /\{(@?[^{}\n]+)\}/g;
  let last = 0, m;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) out.push({ lit: text.slice(last, m.index) });
    out.push(m[1].startsWith('@') ? { builtin: m[1].slice(1).trim() } : { var: m[1].trim() });
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ lit: text.slice(last) });
  return out;
}

function labCompile(project) {
  const errors = [];                                                    // {block, script, msg}
  const warnings = [];
  const meta = project.meta || {};
  const needs = new Set(), helpers = new Set();
  const out = [];                                                       // {text, block}
  const sourceMap = [];                                                 // индекс строки (с 0) → block id
  let uid = 0;

  const cfgKeys = new Set((project.configs || []).map((x) => x.key));
  const cfgTypes = new Map((project.configs || []).map((x) => [x.key, x.type]));

  /* ----- один скрипт ----- */
  function compileScript(script, index) {
    const st = LAB_SCRIPT_TYPES[script.type];
    const lines = [];
    let indent = 0, curBlock = null, curScript = script;
    const scopes = [{ defined: new Set(), assigned: new Set(), nonlocal: new Set(), loop: 0, kind: script.type }];
    const forms = {};
    const sc = () => scopes[scopes.length - 1];
    const ctxStack = [new Set(st ? st.ctx : [])];
    const ctxHas = (x) => ctxStack[ctxStack.length - 1].has(x);
    const err = (msg) => errors.push({ block: curBlock, script: script.id, msg });

    const c = {
      side: script.side || 'user', forms,
      uid: () => ++uid,
      line(text) { lines.push({ text: '    '.repeat(indent) + text, block: curBlock }); },
      ind(fn) { indent++; const n = lines.length; fn(); if (lines.length === n) this.line('pass'); indent--; },
      need(m) { needs.add(m); },
      helper(h) { helpers.add(h); },
      err,
      markLoop() { sc().loop++; },
      inLoop() { return sc().loop > 0 || scopes.some((s) => s.loop > 0 && s === sc()); },
      cfgExists: (k) => cfgKeys.has(k),
      cfgType: (k) => cfgTypes.get(k),
      chatRef(v) {
        const s = String(v === undefined ? '' : v).trim();
        if (s === '' ) { err('Не указан чат'); return '"me"'; }
        if (s === 'me') return '"me"';
        if (/^-?\d+$/.test(s)) return s;
        if (LAB_VARREF.test(s)) return this.val(s, 'any');
        if (/^@?[A-Za-z][\w]{3,}$/.test(s)) return labPyStr(s.startsWith('@') ? s : '@' + s);
        return this.val(s, 'any');
      },
      /* чтение существующей переменной (для блоков, которые её меняют) */
      read(name) {
        name = String(name || '').trim();
        if (!name) { err('Не выбрана переменная'); return 'None'; }
        if (!scopes.some((s) => s.defined.has(name))) err(`Переменная «${name}» не задана выше — сначала создайте её блоком «Задать» или другим, который её заполняет`);
        return labMangle(name);
      },
      /* выходная переменная: имя Python + учёт nonlocal в вложенных функциях */
      out(name) {
        name = String(name || '').trim();
        if (!name) { err('Не указано имя переменной'); return '_unnamed'; }
        if (!LAB_IDENT.test(name)) { err(`Имя переменной «${name}»: буквы, цифры и _, не с цифры, до 32 символов`); return '_bad'; }
        const here = sc();
        if (scopes.length > 1 && !here.assigned.has(name)) {
          for (let i = scopes.length - 2; i >= 0; i--) if (scopes[i].defined.has(name)) { here.nonlocal.add(name); break; }
        }
        here.assigned.add(name); here.defined.add(name);
        return labMangle(name);
      },
      set(name, expr, mustExist) {
        if (mustExist) this.read(name);
        const id = this.out(name);
        this.line(`${id} = ${expr}`);
      },
      /* строка из значения: как текст (для методов строк) */
      str(v) { helpers.add('_s'); return `_s(${this.val(v, 'any')})`; },
      /* значение поля по режиму */
      val(raw, mode, quiet) {
        raw = raw === undefined || raw === null ? '' : raw;
        if (typeof raw === 'number') return labPyNum(raw);
        raw = String(raw);
        const m = LAB_VARREF.exec(raw.trim());
        const refVar = (name) => { name = name.trim(); if (!scopes.some((s) => s.defined.has(name))) err(`Переменная «{${name}}» не задана выше`); return labMangle(name); };
        const builtin = (name) => {
          const vm = /^visual\s*[:.]\s*(\w+)$/.exec(name);                  // {@visual:ключ} — значок FRX (visual.visual)
          if (vm) {
            const keys = typeof LAB_ICON_KEYS !== 'undefined' ? LAB_ICON_KEYS : null;
            if (keys && keys.size && !keys.has(vm[1])) err(`Нет значка «${vm[1]}» — выберите из списка visual`);
            needs.add('visual');
            return `visual.visual(${labPyStr(vm[1])})`;
          }
          const b = LAB_BUILTINS[name];
          if (!b) { err(`Нет встроенного значения «{@${name}}»`); return '""'; }
          if (b.ctx.length && !b.ctx.some((x) => ctxHas(x))) err(`«{@${name}}» недоступно в этом скрипте (${b.desc})`);
          if (b.helper) helpers.add(b.helper); if (b.need) needs.add(b.need);
          return b.py;
        };
        if (mode === 'num') {
          const t = raw.trim();
          if (t === '') { err('Пустое число'); return '0'; }
          if (LAB_NUM.test(t)) return labPyNum(Number(t.replace(',', '.')));
          if (m) return refVar(m[1]);
          const bm = /^\{@([^{}]+)\}$/.exec(t);
          if (bm) return builtin(bm[1].trim());
          err(`«${raw}» — ожидалось число или {переменная}`); return '0';
        }
        if (mode === 'any') {
          const t = raw.trim();
          if (LAB_NUM.test(t)) { const n = Number(t.replace(',', '.')); return labPyNum(n); }
          if (m) return refVar(m[1]);
          const bm = /^\{@([^{}]+)\}$/.exec(t);
          if (bm) return builtin(bm[1].trim());
        }
        // текст с подстановками
        const parts = labParseTemplate(raw);
        const esc = mode === 'msg';
        if (parts.every((p) => p.lit !== undefined)) return labPyStr(raw);
        const pieces = parts.map((p) => {
          if (p.lit !== undefined) return JSON.stringify(p.lit).slice(1, -1).replace(/\{/g, '{{').replace(/\}/g, '}}');
          // кавычки внутри f"…{выражение}…" — одинарные: двойные там допустимы только с Python 3.12
          const e = (p.var !== undefined ? refVar(p.var) : builtin(p.builtin)).replace(/"/g, "'");
          helpers.add(esc ? '_h' : '_s');
          return `{${esc ? '_h' : '_s'}(${e})}`;
        });
        return `f"${pieces.join('')}"`;
      },
      /* условия: [{a, op, b, join}] → выражение Python */
      cond(rows) {
        rows = Array.isArray(rows) && rows.length ? rows : [];
        if (!rows.length) { err('Добавьте условие'); return 'False'; }
        helpers.add('_cmp');
        const exprs = rows.map((r) => {
          const unary = ['empty', 'filled', 'isnum'].includes(r.op);
          return `_cmp(${this.val(r.a, 'any')}, ${labPyStr(r.op)}, ${unary ? 'None' : this.val(r.b, 'any')})`;
        });
        let s = exprs[0];
        for (let i = 1; i < exprs.length; i++) s += ` ${rows[i].join === 'or' ? 'or' : 'and'} ${exprs[i]}`;
        return s;
      },
      kids(b, slot) {                                                   // вложенные блоки (indent +1), пустой слот → pass
        indent++; const n = lines.length; compileList((b.kids && b.kids[slot]) || [], b); if (lines.length === n) this.line('pass'); indent--;
      },
      kidsRaw(b, slot, passIfEmpty) {                                   // то же, но без своего indent (вызывается внутри c.ind)
        const n = lines.length; compileList((b.kids && b.kids[slot]) || [], b); if (lines.length === n && passIfEmpty !== false) this.line('pass');
      },
      /* вложенная функция (обработчик формы): nonlocal считаем по присваиваниям внутри */
      nested(name, ctxName, b, slot) {
        const params = ctxName === 'input' ? 'inp' : 'click';
        const head = lines.length;
        this.line(`async def ${name}(${params}):`);
        indent++;
        const fnScope = { defined: new Set(), assigned: new Set(), nonlocal: new Set(), loop: 0, kind: 'nested' };
        scopes.push(fnScope);
        const ctxSet = new Set(ctxStack[ctxStack.length - 1]); ctxSet.add(ctxName); ctxStack.push(ctxSet);
        const bodyStart = lines.length;
        compileList((b.kids && b.kids[slot]) || [], b);
        if (lines.length === bodyStart) this.line('pass');
        const ownLocals = [...fnScope.assigned].filter((n) => !fnScope.nonlocal.has(n)).sort();
        const pre = [];
        if (fnScope.nonlocal.size) pre.push('nonlocal ' + [...fnScope.nonlocal].sort().map(labMangle).join(', '));
        if (ownLocals.length) pre.push(ownLocals.map(labMangle).join(' = ') + ' = None');
        if (pre.length) lines.splice(bodyStart, 0, ...pre.map((t) => ({ text: '    '.repeat(indent) + t, block: b.id })));
        ctxStack.pop(); scopes.pop();
        indent--;
        void head;
      },
    };

    /* ----- список блоков (с учётом цепочек если/иначе, попробовать/ошибка) ----- */
    function compileList(list, parent) {
      let prev = null;                                                  // последний блок цепочки в этом списке
      for (const b of list) {
        if (b.off) { c.line(`# (выключено) ${(LAB_BLOCKS[b.t] || { title: b.t }).title}`); continue; }
        const def = LAB_BLOCKS[b.t];
        curBlock = b.id;
        if (!def) { err(`Неизвестный блок «${b.t}» — возможно, проект создан в более новой версии`); continue; }
        if (!labBlockAllowed(def, script, [...ctxStack[ctxStack.length - 1]])) {
          const where = def.side === 'user' && script.side === 'bot' ? 'в скриптах стационарного бота' : `в скрипте «${st ? st.title : script.type}»`;
          err(`Блок «${def.title}» недоступен ${where}`);
        }
        const chain = def.chain;
        if (chain === 'mid' || chain === 'tail') {
          if (!prev || !(prev === 'head' || prev === 'mid')) err(`«${def.title}» должно стоять сразу после «Если» или «Иначе, если»`);
          prev = chain === 'tail' ? 'tail' : 'mid';
        } else if (chain === 'except') {
          if (prev !== 'try') err('«Если ошибка» должно стоять сразу после «Попробовать»');
          prev = 'except';
        } else if (chain === 'try') prev = 'try';
        else prev = chain === 'head' ? 'head' : null;
        // обязательные поля
        for (const f of def.fields) {
          if (f.optional) continue;
          const v = b.f ? b.f[f.k] : undefined;
          if ((f.type === 'out' || f.type === 'in') && !String(v || '').trim()) err(`Не заполнено поле «${f.label || f.k}»`);
        }
        if (chain === 'try' && !(list[list.indexOf(b) + 1] && list[list.indexOf(b) + 1].t === 'except')) err('После «Попробовать» нужен блок «Если ошибка»');
        b.f = b.f || {};
        for (const f of def.fields) if (b.f[f.k] === undefined) b.f[f.k] = typeof f.def === 'object' ? JSON.parse(JSON.stringify(f.def)) : f.def;
        const before = lines.length;
        try { def.build(b, c); } catch (e) { err(`Не получилось собрать блок: ${e.message}`); }
        if (def.chain === 'head' && !lines.length) void before;
        void parent;
      }
    }

    /* ----- шапка скрипта ----- */
    const p = script.p || {};
    const bot = script.side === 'bot';
    const ns = bot ? 'bot' : 'decorators';
    needs.add(ns);
    let sig = '', deco = '', fname = '', doc = '';
    const guards = [];
    curBlock = null;
    switch (script.type) {
      case 'command': {
        const names = String(p.names || '').split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);
        if (!names.length) err('Укажите хотя бы одну команду');
        names.forEach((n) => { if (!/^\w+$/.test(n)) err(`Команда «${n}»: только буквы, цифры и _`); });
        deco = `@${ns}.command(${names.map(labPyStr).join(', ')}${bot ? `, access=${labPyStr(p.access === 'public' ? 'public' : 'self')}` : ''})`;
        fname = `${bot ? 'bot_' : ''}${labSlug(names[0], 'cmd')}_cmd`; sig = 'event';
        doc = `"""${String(p.desc || 'команда из FRX WebIDE Lab').replace(/"""/g, "'''").replace(/\n/g, ' ')}"""`;
        break;
      }
      case 'watcher':
        deco = bot ? `@bot.watcher(watch=${labPyStr(p.watch || 'bot')}, access=${labPyStr(p.access === 'public' ? 'public' : 'self')})` : '@decorators.watcher()';
        fname = `${bot ? 'bot_' : ''}watch_${index + 1}`; sig = 'event';
        if (!bot && p.dir === 'in') guards.push('if event.out:', '    return');
        if (!bot && p.dir === 'out') guards.push('if not event.out:', '    return');
        if (p.kind === 'private') guards.push('if not event.is_private:', '    return');
        if (p.kind === 'group') guards.push('if not event.is_group:', '    return');
        if (p.kind === 'channel') guards.push('if not event.is_channel:', '    return');
        if (String(p.chat || '').trim()) { const ids = String(p.chat).split(/[\s,]+/).filter(Boolean); if (ids.every((x) => /^-?\d+$/.test(x))) guards.push(`if event.chat_id not in (${ids.join(', ')},):`, '    return'); else err('Чаты для вотчера — числовые id через запятую'); }
        if (String(p.contains || '').trim()) { helpers.add('_cmp'); guards.push(`if not _cmp(event.raw_text or "", "in", ${labPyStr(p.contains)}):`, '    return'); }
        break;
      case 'cycle': {
        const n = Math.trunc(Number(p.n)); if (!(n > 0)) err('Интервал — целое число больше нуля');
        deco = `@${ns}.cycle(interval=${labPyStr(`${n > 0 ? n : 1}${['s', 'm', 'h', 'd'].includes(p.unit) ? p.unit : 'm'}`)})`; fname = `${bot ? 'bot_' : ''}tick_${index + 1}`; break;
      }
      case 'on_start': deco = `@${ns}.on_start()`; fname = `${bot ? 'bot_' : ''}start_${index + 1}`; break;
      case 'on_load': deco = `@${ns}.on_load()`; fname = `${bot ? 'bot_' : ''}loaded_${index + 1}`; sig = 'info'; break;
      case 'tg_event': case 'bot_member': {
        const kinds = Array.isArray(p.kinds) ? p.kinds : [];
        if (script.type === 'tg_event') {
          deco = '@decorators.telegram_event()'; fname = `tg_${index + 1}`; sig = 'event';
          if (kinds.length) guards.push(`if not (${kinds.map((k) => `event.${k}`).join(' or ')}):`, '    return');
          if (String(p.chat || '').trim()) { const ids = String(p.chat).split(/[\s,]+/).filter(Boolean); if (ids.every((x) => /^-?\d+$/.test(x))) guards.push(`if event.chat_id not in (${ids.join(', ')},):`, '    return'); else err('Чаты — числовые id через запятую'); }
        } else {
          deco = '@bot.telegram_event()'; fname = `bot_member_${index + 1}`; sig = 'event';
          if (kinds.includes('join')) guards.push('if not event.user_joined:', '    return');
          if (kinds.includes('left')) guards.push('if not event.user_left:', '    return');
        }
        break;
      }
      case 'on_error': deco = `@${ns}.on_error()`; fname = `${bot ? 'bot_' : ''}on_error_${index + 1}`; sig = 'error_text, context'; break;
      default: err(`Неизвестный тип скрипта «${script.type}»`); return [];
    }
    if (bot && ['tg_event'].includes(script.type)) err('Для бота используйте «Участник чата (бот)»');
    if (script.type === 'tg_event' || script.type === 'bot_member') ctxStack[0].add('tg');
    lines.push({ text: deco, block: null });
    lines.push({ text: `async def ${fname}(${sig}):`, block: null });
    indent = 1;
    if (doc) c.line(doc);
    guards.forEach((g) => c.line(g));
    const bodyStart = lines.length;
    compileList(script.blocks || [], null);
    if (lines.length === bodyStart) c.line('pass');
    const topVars = [...scopes[0].assigned].sort();
    if (topVars.length) lines.splice(bodyStart, 0, { text: '    ' + topVars.map(labMangle).join(' = ') + ' = None', block: null });
    return lines;
  }

  /* ----- сборка файла ----- */
  const body = [];
  const scripts = project.scripts || [];
  if (!scripts.length) errors.push({ block: null, script: null, msg: 'В проекте нет ни одного скрипта — добавьте команду или другой запуск' });
  const compiled = scripts.map((s, i) => ({ s, lines: compileScript(s, i) }));

  // метаданные
  const nameOk = LAB_IDENT.test(meta.module_name || '') || /^[A-Za-z0-9_\-]{1,64}$/.test(meta.module_name || '');
  if (!/^[A-Za-z0-9_\-]{1,64}$/.test(meta.module_name || '')) errors.push({ block: null, script: null, msg: 'MODULE_NAME: латиница, цифры, _ и - (до 64 символов)' });
  if (!String(meta.name || '').trim()) errors.push({ block: null, script: null, msg: 'У модуля нет названия (NAME)' });
  if (!String(meta.version || '').trim()) errors.push({ block: null, script: null, msg: 'У модуля нет версии (VERSION)' });
  void nameOk;
  const extra = String(meta.author_extra || '').trim();
  const author = meta.locked_author !== undefined && meta.locked_author !== null ? meta.locked_author : (extra ? `${extra} | FRX WebIDE Lab` : 'FRX WebIDE Lab');

  // конфиги
  const cfgLines = [];
  (project.configs || []).forEach((cf, i) => {
    const e = (msg) => errors.push({ block: null, script: null, msg: `Настройка №${i + 1} («${cf.key || '…'}»): ${msg}` });
    if (!/^[A-Za-z_]\w*$/.test(cf.key || '')) { e('ключ — латиница, цифры и _, не с цифры'); return; }
    const opts = String(cf.options || '').split(',').map((x) => x.trim()).filter(Boolean);
    let def, extraArgs = [];
    switch (cf.type) {
      case 'int': def = String(Math.trunc(Number(cf.default) || 0)); break;
      case 'float': { def = String(Number(cf.default) || 0); if (!def.includes('.') && !/e/i.test(def)) def += '.0'; break; }
      case 'bool': def = cf.default === true || cf.default === 'true' ? 'True' : 'False'; break;
      case 'link': { const v = String(cf.default ?? '').trim(); if (v && !/^(https?:\/\/|tg:\/\/)?(@[A-Za-z]\w{3,31}|[^\s/]+\.[^\s/]{2,}|localhost)(\S*)$/i.test(v)) e('значение по умолчанию — не ссылка'); def = labPyStr(v); break; }
      case 'list': def = '[' + String(cf.default || '').split(',').map((x) => x.trim()).filter(Boolean).map(labPyStr).join(', ') + ']'; break;
      case 'choices': {
        const picked = String(cf.default || '').split(',').map((x) => x.trim()).filter(Boolean);
        if (opts.length < 2) e('для выбора нужно хотя бы два варианта');
        if (!picked.length) e('отметьте хотя бы один вариант по умолчанию');
        else if (picked.some((x) => !opts.includes(x))) e('значения по умолчанию должны быть среди вариантов');
        def = '[' + picked.map(labPyStr).join(', ') + ']'; extraArgs.push(`options=[${opts.map(labPyStr).join(', ')}]`); break;
      }
      case 'choice': { if (opts.length < 2) e('для выбора нужно хотя бы два варианта'); if (!opts.includes(String(cf.default).trim())) e('значение по умолчанию должно быть одним из вариантов'); def = labPyStr(String(cf.default).trim()); extraArgs.push(`options=[${opts.map(labPyStr).join(', ')}]`); break; }
      default: def = labPyStr(cf.default === undefined ? '' : cf.default);
    }
    cfgLines.push(`    await engine.create_config(key=${labPyStr(cf.key)}, description=${labPyStr(cf.description || '')}, data_type=${labPyStr(cf.type || 'str')}, default=${def}${extraArgs.length ? ', ' + extraArgs.join(', ') : ''})`);
  });
  if (cfgLines.length) { needs.add('decorators'); needs.add('engine'); }
  const dupCfg = (project.configs || []).map((x) => x.key).filter((k, i, a) => k && a.indexOf(k) !== i);
  dupCfg.forEach((k) => errors.push({ block: null, script: null, msg: `Настройка «${k}» объявлена дважды` }));
  void dupCfg;

  // быстрые проверки: команды не должны повторяться
  const seenCmd = new Map();
  scripts.filter((s) => s.type === 'command').forEach((s) => {
    String((s.p || {}).names || '').split(/[\s,]+/).filter(Boolean).forEach((n) => {
      const key = (s.side || 'user') + ':' + n.toLowerCase();
      if (seenCmd.has(key)) errors.push({ block: null, script: s.id, msg: `Команда «${n}» объявлена в двух скриптах` });
      seenCmd.set(key, s.id);
    });
  });

  // собираем нужные импорты и вспомогательные функции
  const allHelpers = new Set();
  const addHelper = (h) => { if (allHelpers.has(h)) return; (LAB_HELPER_DEPS[h] || []).forEach(addHelper); allHelpers.add(h); };
  helpers.forEach(addHelper);
  allHelpers.forEach((h) => { if (LAB_MODULES_FOR[h]) needs.add(LAB_MODULES_FOR[h]); });
  if (allHelpers.has('_now') || needs.has('datetime')) needs.add('datetime');
  if (allHelpers.has('_s') || allHelpers.size) needs.add('engine');
  const importOrder = ['decorators', 'bot', 'engine', 'db', 'cache', 'visual', 'files', 'asyncio', 'time', 'random', 'math', 're', 'html', 'datetime'];
  const usesAny = (txt) => (n) => new RegExp(`(^|[^\\w.])${n}\\.`).test(txt);
  const bodyText = compiled.map((x) => x.lines.map((l) => l.text).join('\n')).join('\n') + cfgLines.join('\n') + [...allHelpers].map((h) => LAB_HELPERS[h].join('\n')).join('\n');
  const usedIn = usesAny(bodyText);
  ['engine', 'db', 'cache'].forEach((n) => { if (usedIn(n)) needs.add(n); });
  if (/\btime\./.test(bodyText.replace(/_dt\./g, ''))) needs.add('time');
  if (usedIn('files')) needs.add('files');
  if (usedIn('visual')) needs.add('visual');

  const imports = [];
  for (const m of importOrder) {
    if (!needs.has(m)) continue;
    if (m === 'html') imports.push('import html as _html');
    else if (m === 'datetime') imports.push('import datetime as _dt');
    else imports.push(`import ${m}`);
  }

  const outLines = [];
  const push = (text, block) => { outLines.push(text); sourceMap.push(block || null); };
  push('# [FRX Lab] Модуль собран в FRX WebIDE Lab. Проект лежит в конце файла — открывайте этот модуль в Lab, чтобы его менять.');
  imports.forEach((l) => push(l));
  push('');
  push(`NAME = ${labPyStr(meta.name || 'Мой модуль')}`);
  push(`MODULE_NAME = ${labPyStr(meta.module_name || 'my_module')}`);
  push(`VERSION = ${labPyStr(meta.version || '1.0')}`);
  push(`AUTHOR = ${labPyStr(author)}`);
  push(`DESCRIPTION = ${labPyStr(meta.description || '')}`);
  if (String(meta.tg_url || '').trim()) push(`TG_URL = ${labPyStr(String(meta.tg_url).trim())}`);
  if (String(meta.git_url || '').trim()) push(`GIT_URL = ${labPyStr(String(meta.git_url).trim())}`);
  const helperOrder = Object.keys(LAB_HELPERS).filter((h) => allHelpers.has(h));
  if (helperOrder.length) {
    push(''); push('');
    push('# ---------- служебные функции Lab ----------');
    helperOrder.forEach((h) => { push(''); LAB_HELPERS[h].forEach((l) => push(l)); });
  }
  if (cfgLines.length) {
    push(''); push('');
    push('@decorators.on_start()');
    push('async def _lab_setup_configs():');
    cfgLines.forEach((l) => push(l));
  }
  compiled.forEach(({ lines }) => {
    push(''); push('');
    lines.forEach((l) => push(l.text, l.block));
  });
  push('');
  void body;

  const seen = new Set();
  const uniq = errors.filter((e) => { const k = (e.block || '') + '|' + (e.script || '') + '|' + e.msg; if (seen.has(k)) return false; seen.add(k); return true; });
  const code = outLines.join('\n');
  return { code, sourceMap, errors: uniq, warnings, imports };
}

/* ------------------------------------------------------------------ *
 * проект ⇄ маркер в файле модуля
 * ------------------------------------------------------------------ */

const LAB_MARK_BEGIN = '# ===== FRX WebIDE Lab: проект (не редактируйте вручную) =====';
const LAB_MARK_END = '# ===== конец проекта FRX WebIDE Lab =====';

function labHash(str) {                                                 // cyrb53 — без crypto.subtle (панель может открываться по http в локальной сети)
  let h1 = 0xdeadbeef, h2 = 0x41c6ce57;
  for (let i = 0; i < str.length; i++) { const ch = str.charCodeAt(i); h1 = Math.imul(h1 ^ ch, 2654435761); h2 = Math.imul(h2 ^ ch, 1597334677); }
  h1 = Math.imul(h1 ^ (h1 >>> 16), 2246822507) ^ Math.imul(h2 ^ (h2 >>> 13), 3266489909);
  h2 = Math.imul(h2 ^ (h2 >>> 16), 2246822507) ^ Math.imul(h1 ^ (h1 >>> 13), 3266489909);
  return (4294967296 * (2097151 & h2) + (h1 >>> 0)).toString(16).padStart(14, '0');
}
function labB64(str) {
  const bytes = new TextEncoder().encode(str);
  let bin = '';
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return btoa(bin);
}
function labUnb64(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return new TextDecoder().decode(bytes);
}

/* Полный текст модуля: код + проект. */
function labBuildSource(project, compiled) {
  const clean = JSON.parse(JSON.stringify(project));
  const json = JSON.stringify(clean);
  const b64 = labB64(json);
  const chunks = b64.match(/.{1,100}/g) || [];
  const hash = labHash(compiled.code.replace(/\n+$/, ''));
  return compiled.code.replace(/\n+$/, '') + '\n\n\n' + [LAB_MARK_BEGIN, `# FRXLAB1 ${hash}`, ...chunks.map((x) => '# ' + x), LAB_MARK_END].join('\n') + '\n';
}

/* {project, codeHash, edited} либо null. edited — код после последней сборки правили руками. */
function labParseSource(source) {
  const i = source.indexOf(LAB_MARK_BEGIN);
  if (i < 0) return null;
  const j = source.indexOf(LAB_MARK_END, i);
  if (j < 0) return null;
  const block = source.slice(i + LAB_MARK_BEGIN.length, j).split('\n').map((l) => l.replace(/^# ?/, '').trim()).filter(Boolean);
  if (!block.length || !block[0].startsWith('FRXLAB1 ')) return null;
  const hash = block[0].slice(8).trim();
  let project;
  try { project = JSON.parse(labUnb64(block.slice(1).join(''))); } catch (e) { return null; }
  if (!project || typeof project !== 'object' || !Array.isArray(project.scripts)) return null;
  const code = source.slice(0, i).replace(/\n+$/, '');
  const stripped = code.replace(/^# \[FRX WebIDE\].*\n/m, '');
  return { project, hash, edited: labHash(stripped.replace(/\n+$/, '')) !== hash && labHash(code.replace(/\n+$/, '')) !== hash };
}

/* Приводим загруженный проект к безопасному виду (чужие/старые данные не должны ломать редактор). */
function labSanitize(project) {
  const clean = { v: 1, meta: {}, configs: [], scripts: [] };
  const m = project.meta || {};
  clean.meta = { name: String(m.name || 'Мой модуль').slice(0, 80), module_name: String(m.module_name || 'my_module').slice(0, 64), version: String(m.version || '1.0').slice(0, 20),
    description: String(m.description || '').slice(0, 300), author_extra: String(m.author_extra || '').slice(0, 60),
    tg_url: String(m.tg_url || '').slice(0, 200), git_url: String(m.git_url || '').slice(0, 300) };
  if (m.locked_author !== undefined && m.locked_author !== null) clean.meta.locked_author = String(m.locked_author);
  clean.configs = (Array.isArray(project.configs) ? project.configs : []).slice(0, 60).map((x) => ({ key: String(x.key || '').slice(0, 40), description: String(x.description || '').slice(0, 200), type: ['str', 'int', 'float', 'bool', 'list', 'choice', 'choices', 'link'].includes(x.type) ? x.type : 'str', default: x.default === undefined ? '' : x.default, options: String(x.options || '') }));
  const walk = (list, depth) => (Array.isArray(list) ? list : []).slice(0, 500).map((b) => {
    const def = LAB_BLOCKS[b && b.t];
    const nb = { id: typeof b.id === 'string' ? b.id.slice(0, 20) : labId(), t: String((b && b.t) || ''), f: (b && typeof b.f === 'object' && b.f) ? b.f : {} };
    if (b && b.off) nb.off = true;
    if (def && def.slots) { nb.kids = {}; def.slots.forEach((s) => { nb.kids[s] = depth < 12 ? walk(b.kids && b.kids[s], depth + 1) : []; }); }
    return nb;
  });
  clean.scripts = (Array.isArray(project.scripts) ? project.scripts : []).slice(0, 80).map((s) => ({ id: typeof s.id === 'string' ? s.id.slice(0, 20) : labId(), type: LAB_SCRIPT_TYPES[s.type] ? s.type : 'command', side: s.side === 'bot' ? 'bot' : 'user', p: (s.p && typeof s.p === 'object') ? s.p : {}, blocks: walk(s.blocks, 0), collapsed: !!s.collapsed }));
  return clean;
}

/* заготовка нового блока */
function labNewBlock(t) {
  const def = LAB_BLOCKS[t];
  const b = { id: labId(), t, f: {} };
  def.fields.forEach((f) => { b.f[f.k] = (f.def !== null && typeof f.def === 'object') ? JSON.parse(JSON.stringify(f.def)) : f.def; });
  if (def.slots) { b.kids = {}; def.slots.forEach((s) => { b.kids[s] = []; }); }
  return b;
}
function labNewScript(type, side) {
  const defaults = { command: { names: 'hello', desc: 'поздороваться', access: 'self' }, watcher: { dir: 'in', kind: 'any', chat: '', contains: '', watch: 'bot', access: 'self' },
    cycle: { n: 1, unit: 'h' }, on_start: {}, on_load: {}, tg_event: { kinds: ['user_joined'], chat: '' }, bot_member: { kinds: ['join'] }, on_error: {} };
  return { id: labId(), type, side: side || 'user', p: JSON.parse(JSON.stringify(defaults[type] || {})), blocks: [] };
}
function labNewProject() {
  const s = labNewScript('command', 'user');
  const a = labNewBlock('answer'); a.f.text = 'Привет!';
  s.blocks.push(a);
  return { v: 1, meta: { name: 'Мой модуль', module_name: 'my_lab_module', version: '1.0', description: 'Собрано в FRX WebIDE Lab', author_extra: '' }, configs: [], scripts: [s] };
}

if (typeof module !== 'undefined') module.exports = { LAB_CATS, LAB_BLOCKS, LAB_SCRIPT_TYPES, LAB_BUILTINS, labCompile, labBuildSource, labParseSource, labSanitize, labNewBlock, labNewScript, labNewProject, labHash, labBlockAllowed, labParseTemplate };
