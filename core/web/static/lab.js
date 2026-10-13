/* FRX WebIDE Lab. Визуальный конструктор модулей: палитра блоков по категориям (как в Mindustry — вертикальный
 * список инструкций с полями прямо в блоке), скрипты-шапки, вложенные блоки, drag-and-drop, предпросмотр Python, проверка,
 * запуск в Sandbox. Ядро (каталог блоков и компилятор) — lab_core.js; здесь только интерфейс.
 * Любые данные попадают в DOM через textContent/createElement; innerHTML — только для подсветки кода (pyHighlight экранирует). */
'use strict';

const LAB_OPS = [['==', 'равно'], ['!=', 'не равно'], ['>', 'больше'], ['<', 'меньше'], ['>=', 'не меньше'], ['<=', 'не больше'], ['in', 'содержит'], ['not in', 'не содержит'],
  ['starts', 'начинается с'], ['ends', 'заканчивается на'], ['regex', 'подходит под regex'], ['empty', 'пусто'], ['filled', 'не пусто'], ['isnum', 'это число']];
const LAB_UNARY = ['empty', 'filled', 'isnum'];
const LAB_CFG_TYPES = [['str', 'текст'], ['int', 'целое число'], ['float', 'дробное число'], ['bool', 'да / нет'], ['list', 'список'], ['choice', 'выбор одного'], ['choices', 'выбор нескольких'], ['link', 'ссылка']];
const LAB_CLIP = { block: null };

function labClone(x) { return JSON.parse(JSON.stringify(x)); }
function labReid(b) {                                            // новые id для скопированного блока (и вложенных)
  b.id = labId();
  Object.values(b.kids || {}).forEach((l) => l.forEach(labReid));
  return b;
}
function labFindBlock(project, id) {                             // {block, list, index, script}
  for (const s of project.scripts) {
    const hit = (function walk(list) {
      for (let i = 0; i < list.length; i++) {
        if (list[i].id === id) return { block: list[i], list, index: i, script: s };
        for (const slot of Object.values(list[i].kids || {})) { const r = walk(slot); if (r) return r; }
      }
      return null;
    })(s.blocks);
    if (hit) return hit;
  }
  return null;
}
function labWalk(list, fn, depth) {
  (list || []).forEach((b) => { fn(b, depth || 0); Object.values(b.kids || {}).forEach((l) => labWalk(l, fn, (depth || 0) + 1)); });
}
function labVarsOf(script) {                                     // все имена переменных, которые заполняют блоки скрипта
  const names = new Set();
  labWalk(script.blocks, (b) => {
    const def = LAB_BLOCKS[b.t];
    if (!def || b.off) return;
    def.fields.forEach((f) => { if (f.type === 'out' && b.f && String(b.f[f.k] || '').trim()) names.add(String(b.f[f.k]).trim()); });
    if (b.t === 'cfg_get') return;
  });
  return [...names];
}
function labCtxAt(project, id) {                                 // какие контексты доступны внутри блока (нажатие/ввод формы и т.д.)
  const hit = labFindBlock(project, id);
  if (!hit) return [];
  const ctx = new Set((LAB_SCRIPT_TYPES[hit.script.type] || { ctx: [] }).ctx);
  (function walk(list, trail) {
    for (const b of list) {
      if (b.id === id) { trail.forEach((x) => ctx.add(x)); return true; }
      for (const slot of Object.values(b.kids || {})) {
        const add = b.t === 'on_click' ? 'click' : b.t === 'on_input' ? 'input' : null;
        if (walk(slot, add ? [...trail, add] : trail)) return true;
      }
    }
    return false;
  })(hit.script.blocks, []);
  return [...ctx];
}
function labFormsOf(script, upToId) {                            // {имя_формы: [кнопки]} — формы, созданные в скрипте выше блока
  const forms = {};
  labWalk(script.blocks, (b) => {
    if (b.t === 'form_new' && b.f && b.f.var) forms[b.f.var] = (b.f.buttons || []).flat();
    else if (b.t === 'form_edit' && b.f && b.f.form && b.f.mode === 'new' && forms[b.f.form]) forms[b.f.form] = forms[b.f.form].concat((b.f.buttons || []).flat());   // кнопки, добавленные «Изменить форму»
  });
  void upToId;
  return forms;
}

/* вставить текст в позицию курсора поля */
function labInsertAt(el, text) {
  el.focus();
  const s = el.selectionStart === undefined ? el.value.length : el.selectionStart, e = el.selectionEnd === undefined ? s : el.selectionEnd;
  el.setRangeText(text, s, e, 'end');
  el.dispatchEvent(new Event('input', { bubbles: true }));
}

/* ---------- форматирование текста сообщений: выделил — плашка над полем ----------
   Текст сообщений — HTML Telegram, поэтому кнопки оборачивают выделенное в теги (<b>, <i>, <code>…). Повторное
   нажатие на уже оформленном фрагменте снимает оформление. Горячие клавиши: Ctrl+B, I, U, E (код), Shift+X
   (зачёркнутый), Shift+S (спойлер), K (ссылка). */
const LAB_FMT = [
  { k: 'b', label: 'Ж', title: 'жирный · Ctrl+B', open: '<b>', close: '</b>', cls: 'f-b', key: 'b' },
  { k: 'i', label: 'К', title: 'курсив · Ctrl+I', open: '<i>', close: '</i>', cls: 'f-i', key: 'i' },
  { k: 'u', label: 'Ч', title: 'подчёркнутый · Ctrl+U', open: '<u>', close: '</u>', cls: 'f-u', key: 'u' },
  { k: 's', label: 'З', title: 'зачёркнутый · Ctrl+Shift+X', open: '<s>', close: '</s>', cls: 'f-s', key: 'X', shift: true },
  { k: 'code', label: '</>', title: 'код в строке · Ctrl+E', open: '<code>', close: '</code>', cls: 'f-code', key: 'e' },
  { k: 'pre', label: '{ }', title: 'блок кода', open: '<pre>', close: '</pre>', cls: 'f-pre' },
  { k: 'spoiler', label: '▒', title: 'спойлер · Ctrl+Shift+S', open: '<tg-spoiler>', close: '</tg-spoiler>', cls: 'f-sp', key: 'S', shift: true },
  { k: 'quote', label: '❝', title: 'цитата', open: '<blockquote>', close: '</blockquote>', cls: 'f-q' },
  { k: 'link', label: '🔗', title: 'ссылка · Ctrl+K', cls: 'f-a', key: 'k' },
];
let LAB_FMT_BAR = null;
function labFmtOutside(e) {
  if (!LAB_FMT_BAR || LAB_FMT_BAR.el.contains(e.target) || e.target === LAB_FMT_BAR.input || (LAB_FMT_BAR.anchor && LAB_FMT_BAR.anchor.contains(e.target))) return;
  labFmtHide();
}
function labFmtHide() { document.removeEventListener('pointerdown', labFmtOutside, true); if (LAB_FMT_BAR) { LAB_FMT_BAR.el.classList.add('out'); const el = LAB_FMT_BAR.el; setTimeout(() => el.remove(), 160); LAB_FMT_BAR = null; } }
const LAB_FMT_TAGS = 'b|i|u|s|code|pre|tg-spoiler|blockquote|a';
// теги, вплотную окружающие выделение: «…<b><i>|мир|</i></b>…» → [{name, open, close}], от внутреннего к внешнему
function labFmtAround(v, s, e) {
  const before = v.slice(0, s), after = v.slice(e);
  const opens = (before.match(new RegExp(`(?:<(?:${LAB_FMT_TAGS})(?:\\s[^>]*)?>)+$`, 'i')) || [''])[0].match(/<[^>]+>/g) || [];
  const closes = (after.match(new RegExp(`^(?:</(?:${LAB_FMT_TAGS})>)+`, 'i')) || [''])[0].match(/<[^>]+>/g) || [];
  const out = [];
  let ob = s, ca = e;
  for (let i = 0; i < Math.min(opens.length, closes.length); i++) {
    const o = opens[opens.length - 1 - i], c = closes[i];
    const name = o.match(/^<([\w-]+)/)[1].toLowerCase();
    if (c.toLowerCase() !== `</${name}>`) break;
    ob -= o.length;
    out.push({ name, open: o, close: c, from: ob, to: ca + c.length });
    ca += c.length;
  }
  return out;
}
function labFmtName(f) { return f.k === 'link' ? 'a' : f.k === 'spoiler' ? 'tg-spoiler' : f.k === 'quote' ? 'blockquote' : f.k; }
function labFmtApply(el, f, href) {
  if (el.selectionStart === el.selectionEnd && el._fsel && document.activeElement !== el) {     // телефон: выделение сбросилось при нажатии
    const [a, b] = el._fsel;
    if (b <= el.value.length) el.setSelectionRange(a, b);
  }
  const s = el.selectionStart, e = el.selectionEnd, v = el.value;
  if (s === e) {                                          // ничего не выделено — вставляем пустые теги, курсор внутри
    if (f.k === 'link') {
      if (!href) return;
      const open = `<a href="${href.replace(/"/g, '&quot;')}">`, word = 'ссылка';
      el.setRangeText(open + word + '</a>', s, e, 'end');
      el.setSelectionRange(s + open.length, s + open.length + word.length);
    } else {
      el.setRangeText(f.open + f.close, s, e, 'end');
      el.setSelectionRange(s + f.open.length, s + f.open.length);
    }
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.focus();
    return;
  }
  const name = labFmtName(f);
  const sel = v.slice(s, e);
  const around = labFmtAround(v, s, e);
  const hit = around.findIndex((t) => t.name === name);
  if (hit >= 0) {                                         // уже оформлено этим тегом снаружи — снимаем именно его
    const t = around[hit];
    const inner = around.slice(0, hit);                   // теги внутри него остаются
    const innerOpen = inner.map((x) => x.open).reverse().join(''), innerClose = inner.map((x) => x.close).join('');
    el.setRangeText(innerOpen + sel + innerClose, t.from, t.to, 'end');
    const ns = t.from + innerOpen.length;
    el.setSelectionRange(ns, ns + sel.length);
  } else if (new RegExp(`^<${name}(\\s[^>]*)?>[\\s\\S]*</${name}>$`, 'i').test(sel)) {   // выделили вместе с тегами
    const inner = sel.replace(new RegExp(`^<${name}(\\s[^>]*)?>`, 'i'), '').replace(new RegExp(`</${name}>$`, 'i'), '');
    el.setRangeText(inner, s, e, 'select');
  } else {
    if (f.k === 'link' && !href) return;
    const open = f.k === 'link' ? `<a href="${href.replace(/"/g, '&quot;')}">` : f.open, close = f.k === 'link' ? '</a>' : f.close;
    // не захватываем пробелы по краям — Telegram не любит «<b> текст </b>»
    const lead = sel.match(/^\s*/)[0].length, trail = sel.match(/\s*$/)[0].length;
    const core = sel.slice(lead, sel.length - trail);
    if (!core) return;
    el.setRangeText(sel.slice(0, lead) + open + core + close + sel.slice(sel.length - trail), s, e, 'end');
    el.setSelectionRange(s + lead + open.length, s + lead + open.length + core.length);
  }
  el.dispatchEvent(new Event('input', { bubbles: true }));
  el.focus();
}
function labFmtState(el) {                                                 // какие оформления уже на выделении
  const on = new Set(labFmtAround(el.value, el.selectionStart, el.selectionEnd).map((t) => t.name));
  return new Set(LAB_FMT.filter((f) => on.has(labFmtName(f))).map((f) => f.k));
}
function labFmtShow(el, x, y, anchor) {
  // anchor — кнопка «Aa» у поля: плашка открывается всегда и не прячется, пока её не закроют
  if (!anchor && !(LAB_FMT_BAR && LAB_FMT_BAR.pinned && LAB_FMT_BAR.input === el)
      && (el.selectionStart === el.selectionEnd || document.activeElement !== el)) { labFmtHide(); return; }
  const on = labFmtState(el);
  if (LAB_FMT_BAR && LAB_FMT_BAR.input === el && (!anchor || LAB_FMT_BAR.anchor === anchor)) {
    LAB_FMT_BAR.el.querySelectorAll('[data-k]').forEach((b) => b.classList.toggle('on', on.has(b.dataset.k)));
  } else {
    labFmtHide();
    const bar = h('div', { class: 'lab-fmt glass', role: 'toolbar', 'aria-label': 'оформление текста' });
    const linkBox = h('div', { class: 'lab-fmt-link', hidden: true });
    const url = h('input', { class: 'lb-in', type: 'url', placeholder: 'https://…', spellcheck: 'false' });
    const okLink = () => {
      let u = url.value.trim();
      if (u && !/^(https?:\/\/|tg:\/\/|mailto:)/i.test(u)) u = 'https://' + u;
      if (u && !/^(https?:\/\/[^\s/]+\.[^\s]+|tg:\/\/\S+|mailto:\S+@\S+)$/i.test(u)) { url.classList.add('bad'); return; }
      labFmtApply(el, LAB_FMT.find((f) => f.k === 'link'), u);
      labFmtHide();
    };
    url.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); okLink(); } if (e.key === 'Escape') { e.preventDefault(); labFmtHide(); el.focus(); } });
    url.addEventListener('input', () => url.classList.remove('bad'));
    linkBox.append(url, h('button', { type: 'button', class: 'lab-fmt-b', onmousedown: (e) => e.preventDefault(), onclick: okLink }, 'ок'));
    const btns = h('div', { class: 'lab-fmt-row' }, LAB_FMT.map((f) => h('button', {
      type: 'button', class: 'lab-fmt-b ' + f.cls + (on.has(f.k) ? ' on' : ''), title: f.title, 'data-k': f.k,
      onmousedown: (e) => e.preventDefault(),                              // не терять выделение в поле
      onclick: () => {
        if (f.k === 'link') {
          if (labFmtState(el).has('link')) { labFmtApply(el, f); labFmtShow(el, undefined, undefined, LAB_FMT_BAR && LAB_FMT_BAR.pinned ? LAB_FMT_BAR.anchor : undefined); return; }
          btns.hidden = true; linkBox.hidden = false; url.value = ''; url.focus(); return;
        }
        labFmtApply(el, f); labFmtShow(el, undefined, undefined, LAB_FMT_BAR && LAB_FMT_BAR.pinned ? LAB_FMT_BAR.anchor : undefined);
      },
    }, f.label)), h('button', { type: 'button', class: 'lab-fmt-b f-clear', title: 'убрать всё оформление в выделенном', onmousedown: (e) => e.preventDefault(),
      onclick: () => { const s0 = el.selectionStart, e0 = el.selectionEnd; const t = el.value.slice(s0, e0).replace(/<\/?(b|i|u|s|code|pre|tg-spoiler|blockquote|a)(\s[^>]*)?>/gi, ''); el.setRangeText(t, s0, e0, 'select'); el.dispatchEvent(new Event('input', { bubbles: true })); labFmtShow(el, undefined, undefined, LAB_FMT_BAR && LAB_FMT_BAR.pinned ? LAB_FMT_BAR.anchor : undefined); } }, '⌫'));
    bar.append(btns, linkBox);
    if (anchor) {
      btns.append(h('button', { type: 'button', class: 'lab-fmt-b lab-fmt-x', title: 'закрыть', onmousedown: (e) => e.preventDefault(), onclick: () => labFmtHide() }, '✕'));
      bar.append(h('div', { class: 'lab-fmt-tip' }, 'выделите текст и нажмите кнопку; без выделения — теги встанут у курсора'));
    }
    document.body.appendChild(bar);
    LAB_FMT_BAR = { el: bar, input: el, pinned: !!anchor, anchor };
    if (anchor) setTimeout(() => document.addEventListener('pointerdown', labFmtOutside, true), 0);
  }
  if (anchor || (LAB_FMT_BAR.pinned && LAB_FMT_BAR.anchor)) {               // под кнопкой «Aa», справа налево
    const a = (anchor || LAB_FMT_BAR.anchor).getBoundingClientRect(), bar = LAB_FMT_BAR.el, bw = bar.offsetWidth || 360, bh = bar.offsetHeight || 70;
    let top = a.bottom + 6;
    if (top + bh > window.innerHeight - 8) top = Math.max(8, a.top - bh - 6);
    bar.style.left = Math.max(8, Math.min(a.right - bw, window.innerWidth - bw - 8)) + 'px'; bar.style.top = top + 'px';
    return;
  }
  const bar = LAB_FMT_BAR.el, r = el.getBoundingClientRect(), bw = bar.offsetWidth || 340, bh = bar.offsetHeight || 40;
  let left = (x !== undefined ? x : r.left + r.width / 2) - bw / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - bw - 8));
  let top = (y !== undefined ? y - 14 : r.top) - bh - 6;
  if (top < 8) top = r.bottom + 8;
  bar.style.left = left + 'px'; bar.style.top = top + 'px';
}
function labFormatBar(el) {
  let mx, my;
  const remember = () => { if (el.selectionStart !== el.selectionEnd) el._fsel = [el.selectionStart, el.selectionEnd]; };
  ['select', 'keyup', 'mouseup', 'touchend', 'selectionchange'].forEach((ev) => el.addEventListener(ev, remember));
  document.addEventListener('selectionchange', () => { if (document.activeElement === el) remember(); });
  el.addEventListener('mouseup', (e) => { mx = e.clientX; my = e.clientY; setTimeout(() => labFmtShow(el, mx, my), 0); });
  el.addEventListener('keyup', (e) => { if (e.shiftKey || e.key === 'Shift' || (e.ctrlKey && e.key.toLowerCase() === 'a')) labFmtShow(el); });
  el.addEventListener('touchend', () => setTimeout(() => labFmtShow(el), 250));
  el.addEventListener('select', () => labFmtShow(el, mx, my));
  el.addEventListener('blur', () => setTimeout(() => { if (LAB_FMT_BAR && LAB_FMT_BAR.input === el && !LAB_FMT_BAR.pinned && !LAB_FMT_BAR.el.contains(document.activeElement)) labFmtHide(); }, 120));
  el.addEventListener('input', () => { if (el.selectionStart === el.selectionEnd && !(LAB_FMT_BAR && LAB_FMT_BAR.pinned)) labFmtHide(); });
  el.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey) return;
    const f = LAB_FMT.find((x) => x.key && (x.shift ? e.shiftKey && e.key.toUpperCase() === x.key : !e.shiftKey && e.key.toLowerCase() === x.key));
    if (!f || el.selectionStart === el.selectionEnd) return;
    e.preventDefault();
    if (f.k === 'link') { labFmtShow(el); const b = LAB_FMT_BAR && LAB_FMT_BAR.el.querySelector('[data-k="link"]'); if (b) b.click(); return; }
    labFmtApply(el, f); labFmtShow(el, undefined, undefined, LAB_FMT_BAR && LAB_FMT_BAR.pinned ? LAB_FMT_BAR.anchor : undefined);
  });
}
window.addEventListener('scroll', () => { if (LAB_FMT_BAR && !LAB_FMT_BAR.el.querySelector('.lab-fmt-link:not([hidden])')) labFmtHide(); }, true);
window.addEventListener('hashchange', labFmtHide);

/* ------------------------------------------------------------------ *
 * примеры проектов
 * ------------------------------------------------------------------ */

function labExamples() {
  const B = (t, f, kids) => { const b = labNewBlock(t); Object.assign(b.f, f || {}); if (kids) Object.assign(b.kids, kids); return b; };
  const S = (type, p, blocks, side) => { const s = labNewScript(type, side || 'user'); Object.assign(s.p, p || {}); s.blocks = blocks; return s; };
  const P = (id, name, desc, scripts, configs) => ({ v: 1, meta: { name, module_name: id, version: '1.0', description: desc, author_extra: '' }, configs: configs || [], scripts });
  return [
    { title: 'Привет, мир', desc: 'Команда .hello отвечает приветствием', make: () => labNewProject() },
    { title: 'Счётчик команд', desc: '.count считает, сколько раз вас позвали (хранится на диске)', make: () => P('lab_counter', 'Счётчик', 'Считает вызовы команды', [S('command', { names: 'count', desc: 'счётчик' }, [
      B('db_inc', { key: 'count:{@отправитель_id}', by: '1', res: 'n', scope: 'module' }),
      B('if', { conds: [{ a: '{n}', op: '==', b: '1', join: 'and' }] }, { body: [B('answer', { text: '{приветствие}! Вы позвали меня впервые' })] }),
      B('else', {}, { body: [B('answer', { text: '{приветствие}! Вы позвали меня уже {n} раз' })] })])], []) },
    { title: 'Опрос с кнопками', desc: '.vote показывает форму, считает нажатия (нужен стационарный бот)', make: () => P('lab_vote', 'Опрос', 'Форма с кнопками', [S('command', { names: 'vote', desc: 'опрос' }, [
      B('set', { var: 'да', value: '0' }), B('set', { var: 'нет', value: '0' }),
      B('form_new', { var: 'f', text: 'Вам нравится Lab?', buttons: [[{ label: '👍 Да', data: 'yes', kind: 'click' }, { label: '👎 Нет', data: 'no', kind: 'click' }]], who: 'chat', timeout: '0' }),
      B('on_click', { form: 'f', data: 'yes' }, { body: [B('inc', { var: 'да' }), B('form_edit', { form: 'f', text: 'Да: {да} · Нет: {нет}', nobtn: false }), B('click_answer', { text: 'Спасибо!', alert: false })] }),
      B('on_click', { form: 'f', data: 'no' }, { body: [B('inc', { var: 'нет' }), B('form_edit', { form: 'f', text: 'Да: {да} · Нет: {нет}', nobtn: false }), B('click_answer', { text: 'Принято', alert: false })] })])]) },
    { title: 'Спросить и запомнить', desc: '.me спрашивает имя и запоминает его', make: () => P('lab_me', 'Знакомство', 'Спрашивает имя', [S('command', { names: 'me', desc: 'знакомство' }, [
      B('db_get', { key: 'name:{@отправитель_id}', def: '', res: 'имя', scope: 'module' }),
      B('if', { conds: [{ a: '{имя}', op: 'filled', b: '', join: 'and' }] }, { body: [B('answer', { text: 'Я помню вас: {имя}' }), B('stop')] }),
      B('ask', { q: 'Как вас зовут?', timeout: '30', res: 'имя', onlyme: true }, { body: [B('answer', { text: 'Не дождался ответа' }), B('stop')] }),
      B('db_set', { key: 'name:{@отправитель_id}', value: '{имя}', scope: 'module' }),
      B('say_here', { text: 'Приятно познакомиться, {имя}!' })])]) },
    { title: 'Напоминание раз в час', desc: 'Раз в час присылает уведомление от бота', make: () => P('lab_remind', 'Напоминание', 'Периодическое уведомление', [S('cycle', { n: 1, unit: 'h' }, [
      B('now', { fmt: '%H:%M', res: 'время' }), B('send_owner', { text: 'Пора размяться! Сейчас {время}' })])]) },
    { title: 'Встреча в группе', desc: 'Приветствует новичков в чате', make: () => P('lab_welcome', 'Встреча', 'Приветствие новичков', [S('tg_event', { kinds: ['user_joined'], chat: '' }, [
      B('tg_user', { name: 'кто', uname: '' }), B('send_chat', { chat: '{@чат_id}', text: 'Добро пожаловать, {кто}!' })])]) },
  ];
}

/* ------------------------------------------------------------------ *
 * страница редактора Lab
 * ------------------------------------------------------------------ */

const LAB_SLOT_TITLE = { ask: 'если время вышло', wait_msg: 'если время вышло', if: 'то', elif: 'то', else: 'иначе', try: 'пробуем', except: 'если случилась ошибка', on_click: 'выполнить', on_input: 'выполнить' };
const LAB_DRAFT_KEY = 'frx_lab_draft_v1';

async function labEditor(T, param) {
  const isNew = param === '__new__';
  let project = null, moduleId = isNew ? null : param, original = '', edited = false, baseHash = '', drafts = null;

  if (isNew) {
    try { const d = JSON.parse(localStorage.getItem(LAB_DRAFT_KEY) || 'null'); if (d && Array.isArray(d.scripts)) project = labSanitize(d); } catch (e) { /* черновика нет */ }
    if (!project) project = labNewProject();
  } else {
    const r = await api('/webide/source?module=' + enc(param));
    const parsed = labParseSource(r.source);
    if (!parsed) {
      mount(T, shell(param, { back: '#/webide', backLabel: 'webide' }, h('div', { class: 'glass lab-plain' },
        h('h2', {}, 'Этот модуль создан не в Lab'),
        h('div', { class: 'faint' }, 'В файле нет проекта FRX WebIDE Lab, поэтому конструктор не может его открыть. Модуль можно править как код.'),
        h('div', { class: 'actions' }, h('button', { class: 'btn primary', onclick: () => { location.hash = '#/webide/' + enc(param); } }, 'открыть в редакторе кода')))));
      return;
    }
    project = labSanitize(parsed.project);
    project.meta.locked_author = r.meta.author;
    project.meta.module_name = param;
    edited = parsed.edited;
    original = r.source;
    baseHash = r.hash || '';
  }

  const S = { sel: null, active: null, cat: 'msg', search: '', hist: [], fut: [], last: { code: '', errors: [], sourceMap: [] }, dirty: false, icons: null, rtab: 'code', drag: null, savedJson: JSON.stringify(project), histTimer: 0, compTimer: 0, pend: false };
  let dock = null;
  const snapshot = () => JSON.stringify(project);

  /* ---------- история (отмена / повтор) ---------- */
  function pushHist(force) {
    const cur = snapshot();
    if (S.hist.length && S.hist[S.hist.length - 1] === cur) return;
    S.hist.push(cur);
    if (S.hist.length > 120) S.hist.shift();
    S.fut = [];
    void force;
  }
  S.hist.push(snapshot());
  function restore(json) {
    project = JSON.parse(json);
    if (!labFindBlock(project, S.sel)) S.sel = null;
    renderScripts(); renderPalette(); recompile(true); updateTools();
  }
  function undo() { if (S.hist.length < 2) return; S.fut.push(S.hist.pop()); restore(S.hist[S.hist.length - 1]); markDirty(); }
  function redo() { if (!S.fut.length) return; const j = S.fut.pop(); S.hist.push(j); restore(j); markDirty(); }

  /* ---------- изменения проекта ---------- */
  function markDirty() {
    S.dirty = snapshot() !== S.savedJson;
    dirtyTag.hidden = !S.dirty;
    saveBtn.classList.toggle('dirty', S.dirty);                        // на телефоне вместо ярлыка «не сохранено» — точка на кнопке
    if (isNew) { try { localStorage.setItem(LAB_DRAFT_KEY, snapshot()); } catch (e) { /* хранилище недоступно */ } }
    if (drafts) drafts.notify();
  }
  function fieldChanged() {                                            // правка текста в поле: без перерисовки, с «склейкой» истории
    clearTimeout(S.histTimer);
    S.histTimer = setTimeout(() => { pushHist(); updateTools(); }, 700);
    markDirty(); scheduleCompile(); refreshDataFields();
    if (dock) dock.notifyChange();
  }
  function structural() {                                              // добавили / удалили / переместили блок
    clearTimeout(S.histTimer);
    pushHist(); markDirty();
    renderScripts(); renderPalette(); recompile(true); updateTools();
    if (dock) dock.notifyChange();
  }
  const scheduleCompile = () => { clearTimeout(S.compTimer); S.compTimer = setTimeout(() => recompile(false), 220); };

  /* ---------- сборка и проверка ---------- */
  const codeBox = h('div', { class: 'lab-code' });
  const checkBox = h('div', { class: 'lab-check' });
  const varsBox = h('div', { class: 'lab-vars' });
  const errBadge = h('span', { class: 'lab-badge', hidden: true });
  const errBadgeM = h('span', { class: 'lab-badge', hidden: true });     // та же цифра — на переключателе телефона

  function recompile(refreshRight) {
    S.last = labCompile(project);
    const errs = S.last.errors;
    errBadge.hidden = errBadgeM.hidden = !errs.length; errBadge.textContent = errBadgeM.textContent = String(errs.length);
    saveBtn.disabled = !!errs.length;
    // подсветка блоков с ошибками
    wsBox.querySelectorAll('.lb.bad').forEach((n) => { n.classList.remove('bad'); n.removeAttribute('data-err'); });
    wsBox.querySelectorAll('.lb-err').forEach((n) => n.remove());
    const byBlock = new Map();
    errs.forEach((e) => { if (e.block) { if (!byBlock.has(e.block)) byBlock.set(e.block, []); byBlock.get(e.block).push(e.msg); } });
    byBlock.forEach((msgs, id) => {
      const el = wsBox.querySelector(`.lb[data-id="${id}"]`);
      if (!el) return;
      el.classList.add('bad');
      const head = el.querySelector(':scope > .lb-head');
      if (head) el.insertBefore(h('div', { class: 'lb-err' }, msgs.join(' · ')), head.nextSibling);
    });
    renderRight(refreshRight);
    refreshDatalists();
  }

  function renderRight(force) {
    if (S.rtab === 'code' || force) {
      const frag = document.createDocumentFragment();
      S.last.code.split('\n').forEach((line, i) => {
        const blockId = S.last.sourceMap[i];
        const row = h('div', { class: 'lc' + (blockId ? ' lk' : ''), 'data-line': i + 1, title: blockId ? 'перейти к блоку' : '' },
          h('span', { class: 'n' }, String(i + 1)), h('span', { class: 't' }));
        row.lastChild.innerHTML = pyHighlight(line) + ' ';
        if (blockId) row.addEventListener('click', () => focusBlock(blockId));
        frag.appendChild(row);
      });
      codeBox.replaceChildren(frag);
    }
    const errs = S.last.errors;
    checkBox.replaceChildren(errs.length ? h('div', {}, errs.map((e) => h('button', { type: 'button', class: 'lab-errrow', onclick: () => (e.block ? focusBlock(e.block) : e.script ? focusScript(e.script) : openMeta()) },
      h('span', { class: 'bad' }, '✘'), h('span', {}, e.msg)))) : h('div', { class: 'lab-ok' }, '✔ Ошибок нет — модуль можно сохранять и запускать в Sandbox.'));
    // переменные и встроенные значения
    const parts = [];
    project.scripts.forEach((s) => {
      const vs = labVarsOf(s);
      const t = LAB_SCRIPT_TYPES[s.type];
      parts.push(h('div', { class: 'lab-vs' }, h('div', { class: 'wi-lab' }, `${t ? t.title : s.type} · ${labScriptSummary(s)}`),
        vs.length ? h('div', { class: 'chips' }, vs.map((v) => h('span', { class: 'chip small' }, '{' + v + '}'))) : h('div', { class: 'wi-hint' }, 'переменных пока нет')));
    });
    parts.push(h('div', { class: 'wi-lab', style: 'margin-top:14px' }, 'Встроенные значения'),
      h('div', { class: 'wi-hint' }, 'Пишутся как {@имя}; доступность зависит от скрипта. Значки FRX — {@visual:ключ} (кнопка «{ }» → visual). Текст сообщения можно оформить: выделите его — появится плашка (жирный, курсив, код, ссылка…).'),
      Object.entries(LAB_BUILTINS).map(([k, v]) => h('div', { class: 'lab-bi' }, h('code', {}, '{@' + k + '}'), h('span', { class: 'wi-hint' }, ' ' + v.desc + (v.ctx.length ? ' · ' + v.ctx.map((x) => ({ msg: 'сообщение', tg: 'событие чата', click: 'нажатие', input: 'ввод', err: 'ошибка', load: 'загрузка' }[x] || x)).join('/') : '')))));
    varsBox.replaceChildren(...parts);
  }

  function refreshDatalists() {
    project.scripts.forEach((s) => {
      let dl = document.getElementById('lab-vars-' + s.id);
      if (!dl) { dl = h('datalist', { id: 'lab-vars-' + s.id }); document.body.appendChild(dl); }
      dl.replaceChildren(...labVarsOf(s).map((v) => h('option', { value: v })));
    });
  }

  function labScriptSummary(s) {
    const p = s.p || {};
    switch (s.type) {
      case 'command': return (s.side === 'bot' ? '/' : '.') + String(p.names || '').split(/[\s,]+/).filter(Boolean).join(', ' + (s.side === 'bot' ? '/' : '.'));
      case 'cycle': return `каждые ${p.n} ${({ s: 'с', m: 'мин', h: 'ч', d: 'дн' })[p.unit] || p.unit}`;
      case 'watcher': { const bits = []; if (p.dir === 'in') bits.push('входящие'); if (p.dir === 'out') bits.push('исходящие'); if (p.kind && p.kind !== 'any') bits.push({ private: 'личные', group: 'группы', channel: 'каналы' }[p.kind]); if (p.contains) bits.push(`со словом «${p.contains}»`); return bits.join(', ') || 'все сообщения'; }
      case 'tg_event': return (p.kinds || []).map((k) => (LAB_TG_KINDS.find((x) => x[0] === k) || [0, k])[1]).join(', ') || 'любое событие';
      case 'bot_member': return (p.kinds || []).map((k) => ({ join: 'вошёл', left: 'вышел' }[k])).join(', ') || 'любое изменение';
      default: return '';
    }
  }

  function focusBlock(id) {
    const el = wsBox.querySelector(`.lb[data-id="${id}"]`);
    if (!el) return;
    if (S.mview !== 'blocks') setMview('blocks');
    S.sel = id; wsBox.querySelectorAll('.lb.sel').forEach((n) => n.classList.remove('sel')); el.classList.add('sel');
    el.scrollIntoView({ block: 'center', behavior: 'smooth' });
    el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1000);
    updateTools();
  }
  function focusScript(id) {
    if (S.mview !== 'blocks') setMview('blocks');
    const el = wsBox.querySelector(`.ls[data-id="${id}"]`);
    if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 1000); }
  }

  /* ---------- поля блоков ---------- */

  let pickerEl = null;
  function closePicker() { if (pickerEl) { pickerEl.remove(); pickerEl = null; document.removeEventListener('mousedown', outsidePicker, true); } }
  function outsidePicker(e) { if (pickerEl && !pickerEl.contains(e.target)) closePicker(); }
  function openPicker(anchor, input, blockId) {                         // меню «{ }»: переменные и встроенные значения
    closePicker();
    const hit = labFindBlock(project, blockId);
    if (!hit) return;
    const vars = labVarsOf(hit.script);
    const ctx = new Set(labCtxAt(project, blockId));
    const add = (txt) => { closePicker(); labInsertAt(input, txt); };
    const items = [];
    items.push(h('div', { class: 'lab-pk-h' }, 'Переменные'));
    if (vars.length) vars.forEach((v) => items.push(h('button', { type: 'button', class: 'lab-pk', onclick: () => add('{' + v + '}') }, h('code', {}, '{' + v + '}'))));
    else items.push(h('div', { class: 'wi-hint', style: 'padding:2px 10px' }, 'пока нет — создайте блоком «Задать»'));
    items.push(h('button', { type: 'button', class: 'lab-pk lab-pk-visual', onclick: (e) => { e.stopPropagation(); openVisualPicker(anchor, input); } },
      h('code', {}, 'visual'), h('span', { class: 'wi-hint' }, 'значки FRX — выбрать из списка'), h('span', { class: 'lab-pk-go' }, '›')));
    items.push(h('div', { class: 'lab-pk-h' }, 'Встроенные значения'));
    Object.entries(LAB_BUILTINS).filter(([, b]) => !b.ctx.length || b.ctx.some((x) => ctx.has(x))).forEach(([k, b]) =>
      items.push(h('button', { type: 'button', class: 'lab-pk', onclick: () => add('{@' + k + '}') }, h('code', {}, '{@' + k + '}'), h('span', { class: 'wi-hint' }, b.desc))));
    if (mIsPhone()) {                                                  // телефон: список снизу шторкой
      const closeSheet = mSheet('Вставить значение', h('div', { class: 'lab-pk-list' }, items));
      items.forEach((n) => { if (n.tagName === 'BUTTON') n.addEventListener('click', closeSheet); });
      return;
    }
    pickerEl = h('div', { class: 'glass lab-picker' }, items);
    document.body.appendChild(pickerEl);
    const r = anchor.getBoundingClientRect();
    pickerEl.style.left = Math.min(r.left, window.innerWidth - 300) + 'px';
    pickerEl.style.top = Math.min(r.bottom + 4, window.innerHeight - 280) + 'px';
    setTimeout(() => document.addEventListener('mousedown', outsidePicker, true), 0);
  }

  /* «{ } → visual»: сетка значков visual.visual(); вставляет {@visual:ключ} — в коде это visual.visual("ключ"),
     так что значок сменится вместе с темой значков (visual_edit) */
  async function openVisualPicker(anchor, input) {
    const icons = await loadIcons();
    const keys = Object.keys(icons);
    closePicker();
    const add = (k) => { closePicker(); labInsertAt(input, '{@visual:' + k + '}'); };
    const grid = h('div', { class: 'lab-vis-grid' });
    const q = h('input', { class: 'lb-in lab-vis-q', type: 'search', placeholder: 'поиск: ok, wait, back…', spellcheck: 'false' });
    const draw = () => {
      const t = q.value.trim().toLowerCase();
      const list = keys.filter((k) => !t || k.toLowerCase().includes(t) || String(icons[k]).includes(t));
      grid.replaceChildren(...(list.length ? list.map((k) => h('button', { type: 'button', class: 'lab-vis', title: '{@visual:' + k + '}', onclick: () => add(k) },
        h('span', { class: 'lab-vis-i' }, icons[k]), h('span', { class: 'lab-vis-k' }, k))) : [h('div', { class: 'wi-hint', style: 'padding:6px 10px' }, keys.length ? 'ничего не найдено' : 'список значков не загрузился')]));
    };
    q.addEventListener('input', draw);
    draw();
    const head = h('div', { class: 'lab-pk-h lab-vis-h' }, h('button', { type: 'button', class: 'lb-x', title: 'назад', onclick: () => { closePicker(); const bid = input.closest('.lb') && input.closest('.lb').dataset.id; if (bid) openPicker(anchor, input, bid); } }, '‹'), 'visual — значки FRX');
    const body = h('div', { class: 'lab-vis-box' }, q, grid);
    if (mIsPhone()) {
      const closeSheet = mSheet('Значки visual', body);
      grid.addEventListener('click', (e) => { if (e.target.closest('.lab-vis')) closeSheet(); });
      return;
    }
    pickerEl = h('div', { class: 'glass lab-picker lab-vis-picker' }, head, body);
    document.body.appendChild(pickerEl);
    const r = anchor.getBoundingClientRect();
    pickerEl.style.left = Math.min(r.left, window.innerWidth - 340) + 'px';
    pickerEl.style.top = Math.min(r.bottom + 4, window.innerHeight - 360) + 'px';
    setTimeout(() => { document.addEventListener('mousedown', outsidePicker, true); q.focus(); }, 0);
  }

  function autosize(ta) { ta.style.height = 'auto'; ta.style.height = Math.min(ta.scrollHeight, 160) + 'px'; }

  function textField(b, f) {                                           // msg | val | num
    const multi = f.type === 'msg' && !f.single;
    const inp = h(multi ? 'textarea' : 'input', { class: 'lb-in' + (multi ? ' multi' : ''), rows: multi ? 1 : null, type: multi ? null : 'text', spellcheck: 'false', placeholder: f.type === 'num' ? 'число или {переменная}' : '', 'aria-label': f.label || f.k });
    inp.value = b.f[f.k] === undefined || b.f[f.k] === null ? '' : String(b.f[f.k]);
    inp.addEventListener('input', () => { b.f[f.k] = inp.value; if (multi) autosize(inp); fieldChanged(); });
    if (multi) requestAnimationFrame(() => autosize(inp));
    const pick = h('button', { type: 'button', class: 'lb-pick', title: 'вставить переменную или встроенное значение', onclick: () => openPicker(pick, inp, b.id) }, '{ }');
    if (f.type === 'msg' && (!f.plain || f.fmt)) {                   // текст сообщения: выделил — плашка; или кнопка «Aa»
      labFormatBar(inp);
      const fmt = h('button', { type: 'button', class: 'lb-pick lb-fmt', title: 'оформление: жирный, курсив, код, ссылка…', onmousedown: (e) => e.preventDefault(),
        onclick: () => { if (LAB_FMT_BAR && LAB_FMT_BAR.anchor === fmt) { labFmtHide(); return; } labFmtShow(inp, undefined, undefined, fmt); } }, 'Aa');
      return h('span', { class: 'lb-f lb-textf' + (multi ? ' wide' : '') }, inp, fmt, pick);
    }
    return h('span', { class: 'lb-f lb-textf' + (multi ? ' wide' : '') }, inp, pick);
  }

  function datalistInput(b, f, scriptId) {                             // out | in
    const inp = h('input', { class: 'lb-in lb-var ' + (f.type === 'out' ? 'out' : 'in'), type: 'text', list: 'lab-vars-' + scriptId, spellcheck: 'false', placeholder: f.optional ? 'не нужно' : 'имя', 'aria-label': f.label });
    inp.value = b.f[f.k] || '';
    inp.addEventListener('input', () => { b.f[f.k] = inp.value.trim(); refreshDataFields(); fieldChanged(); });
    inp.addEventListener('blur', () => { refreshDatalists(); renderRight(false); });
    return h('span', { class: 'lb-f' }, inp);
  }

  function selField(b, f, options, cb) {
    const sel = h('select', { class: 'lb-in lb-sel', 'aria-label': f.label || f.k }, options.map(([v, l]) => h('option', { value: String(v) }, l)));
    sel.value = String(b.f[f.k] === undefined ? f.def : b.f[f.k]);
    if (sel.value !== String(b.f[f.k]) && options.length) { /* значение не из списка (старое/пустое) */ if (b.f[f.k] === undefined || b.f[f.k] === '') { b.f[f.k] = options[0][0]; sel.value = String(options[0][0]); } }
    sel.addEventListener('change', () => { b.f[f.k] = sel.value; cb ? cb() : fieldChanged(); });
    return h('span', { class: 'lb-f' }, sel);
  }

  function condsField(b, f) {
    const rows = b.f[f.k] = Array.isArray(b.f[f.k]) ? b.f[f.k] : [];
    const box = h('div', { class: 'lb-conds' });
    const draw = () => {
      box.replaceChildren(...rows.map((r, i) => {
        const a = h('input', { class: 'lb-in', value: r.a || '', spellcheck: 'false', 'aria-label': 'левая часть' });
        const bb = h('input', { class: 'lb-in', value: r.b || '', spellcheck: 'false', 'aria-label': 'правая часть' });
        a.addEventListener('input', () => { r.a = a.value; fieldChanged(); }); bb.addEventListener('input', () => { r.b = bb.value; fieldChanged(); });
        const op = h('select', { class: 'lb-in lb-sel', 'aria-label': 'сравнение' }, LAB_OPS.map(([v, l]) => h('option', { value: v }, l)));
        op.value = r.op || '=='; op.addEventListener('change', () => { r.op = op.value; bb.hidden = bbp.hidden = LAB_UNARY.includes(r.op); fieldChanged(); });
        const pa = h('button', { type: 'button', class: 'lb-pick', onclick: () => openPicker(pa, a, b.id) }, '{ }');
        const bbp = h('button', { type: 'button', class: 'lb-pick', onclick: () => openPicker(bbp, bb, b.id) }, '{ }');
        bb.hidden = bbp.hidden = LAB_UNARY.includes(r.op);
        const join = i ? h('select', { class: 'lb-in lb-sel join', 'aria-label': 'связка' }, [['and', 'и'], ['or', 'или']].map(([v, l]) => h('option', { value: v }, l))) : null;
        if (join) { join.value = r.join === 'or' ? 'or' : 'and'; join.addEventListener('change', () => { r.join = join.value; fieldChanged(); }); }
        return h('div', { class: 'lb-cond' }, join, h('span', { class: 'lb-f lb-textf' }, a, pa), op, h('span', { class: 'lb-f lb-textf' }, bb, bbp),
          rows.length > 1 ? h('button', { type: 'button', class: 'lb-x', title: 'убрать условие', onclick: () => { rows.splice(i, 1); draw(); fieldChanged(); } }, '×') : null);
      }), h('button', { type: 'button', class: 'lb-add', onclick: () => { rows.push({ a: '', op: '==', b: '', join: 'and' }); draw(); fieldChanged(); } }, '+ условие'));
    };
    draw();
    return box;
  }

  function buttonsField(b, f) {                                        // макет клавиатуры формы, как в Telegram
    const rows = b.f[f.k] = Array.isArray(b.f[f.k]) ? b.f[f.k] : [];
    let sel = [0, 0];
    const kb = h('div', { class: 'lb-tg' }), ed = h('div', { class: 'lb-tged' });
    const cur = () => (rows[sel[0]] || [])[sel[1]] || null;
    const draw = () => {
      if (rows[sel[0]] === undefined) sel = [Math.max(0, rows.length - 1), 0];
      if (rows[sel[0]] && rows[sel[0]][sel[1]] === undefined) sel[1] = Math.max(0, rows[sel[0]].length - 1);
      kb.replaceChildren(...rows.map((row, ri) => h('div', { class: 'wi-tg-row' },
        row.map((x, ci) => h('button', { type: 'button', class: 'wi-tg-btn' + (x.kind === 'input' ? ' inp' : '') + (x.style ? ' st-' + x.style : '') + (sel[0] === ri && sel[1] === ci ? ' sel' : ''), onclick: () => { sel = [ri, ci]; draw(); } }, x.label || '…')),
        row.length < 6 ? h('button', { type: 'button', class: 'wi-tg-add', title: 'кнопка в этот ряд', onclick: () => { row.push({ label: 'Кнопка', data: 'btn' + (rows.flat().length + 1), kind: 'click' }); sel = [ri, row.length - 1]; draw(); fieldChanged(); } }, '+') : null)),
      rows.length < 8 ? h('button', { type: 'button', class: 'wi-tg-addrow', onclick: () => { rows.push([{ label: 'Кнопка', data: 'btn' + (rows.flat().length + 1), kind: 'click' }]); sel = [rows.length - 1, 0]; draw(); fieldChanged(); } }, '+ ряд') : null);
      const x = cur();
      if (!x) { ed.replaceChildren(); return; }
      const lab = h('input', { class: 'lb-in', value: x.label, spellcheck: 'false' }), dat = h('input', { class: 'lb-in', value: x.data, spellcheck: 'false' });
      lab.addEventListener('input', () => { x.label = lab.value; kb.querySelectorAll('.wi-tg-row')[sel[0]].querySelectorAll('.wi-tg-btn')[sel[1]].textContent = x.label || '…'; fieldChanged(); });
      dat.addEventListener('input', () => { x.data = dat.value.trim(); fieldChanged(); });
      const kind = h('select', { class: 'lb-in lb-sel' }, [['click', 'обычная'], ['input', 'ввод значения']].map(([v, l]) => h('option', { value: v }, l)));
      kind.value = x.kind; kind.addEventListener('change', () => { x.kind = kind.value; draw(); fieldChanged(); });
      const color = h('select', { class: 'lb-in lb-sel', title: 'цвет кнопки (Telegram)' }, [['', 'цвет: обычный'], ['primary', 'синяя'], ['success', 'зелёная'], ['danger', 'красная']].map(([v, l]) => h('option', { value: v }, l)));
      color.value = x.style || ''; color.addEventListener('change', () => { if (color.value) x.style = color.value; else delete x.style; draw(); fieldChanged(); });
      const mv = (dc) => { const row = rows[sel[0]], t = sel[1] + dc; if (t < 0 || t >= row.length) return; [row[sel[1]], row[t]] = [row[t], row[sel[1]]]; sel[1] = t; draw(); fieldChanged(); };
      const del = () => { rows[sel[0]].splice(sel[1], 1); if (!rows[sel[0]].length) rows.splice(sel[0], 1); draw(); fieldChanged(); };
      ed.replaceChildren(h('span', { class: 'lb-lab' }, 'текст'), lab, h('span', { class: 'lb-lab' }, 'callback data'), dat, kind, color,
        h('button', { type: 'button', class: 'lb-x', title: 'левее', onclick: () => mv(-1) }, '←'), h('button', { type: 'button', class: 'lb-x', title: 'правее', onclick: () => mv(1) }, '→'),
        h('button', { type: 'button', class: 'lb-x', title: 'удалить кнопку', onclick: del }, '×'));
    };
    draw();
    return h('div', { class: 'lb-tgbox' }, kb, ed);
  }

  const dataFx = new Map();                                            // id блока → перерисовка списка кнопок
  function refreshDataFields() { dataFx.forEach((fx, id) => { if (fx.wrap.isConnected) { if (!fx.wrap.contains(document.activeElement)) fx.build(); } else if (fx.seen) dataFx.delete(id); }); }
  function dataField(b, f, script) {                                   // выбор кнопки формы
    const wrap = h('span', { class: 'lb-f' });
    const build = () => {                                              // перестраивается сразу при смене формы — не нужно вставлять другой блок
      const forms = labFormsOf(script);
      const spec = (forms[b.f.form] || []).filter((x) => (f.input ? x.kind === 'input' : x.kind !== 'input'));
      const options = spec.length ? spec.map((x) => [x.data, `${x.label} (${x.data})`]) : [['', b.f.form ? 'у формы нет подходящих кнопок' : 'сначала выберите форму']];
      const sel = h('select', { class: 'lb-in lb-sel', 'aria-label': f.label }, options.map(([v, l]) => h('option', { value: v }, l)));
      if (spec.length && !spec.some((x) => x.data === b.f.data)) b.f.data = spec[0].data;
      if (!spec.length) b.f.data = '';
      sel.value = b.f.data || '';
      sel.addEventListener('change', () => { b.f.data = sel.value; fieldChanged(); });
      wrap.replaceChildren(sel);
      const fx = dataFx.get(b.id); if (fx) fx.seen = wrap.isConnected;
    };
    build();
    dataFx.set(b.id, { wrap, build, seen: false });
    return wrap;
  }

  async function loadIcons() {
    if (S.icons) return S.icons;
    try { S.icons = (await api('/webide/visual')).icons || {}; } catch (e) { S.icons = {}; }
    LAB_ICON_KEYS = new Set(Object.keys(S.icons));
    return S.icons;
  }
  function iconField(b, f) {
    const sel = h('select', { class: 'lb-in lb-sel', 'aria-label': 'иконка' }, h('option', { value: b.f[f.k] }, b.f[f.k]));
    loadIcons().then((icons) => { const keys = Object.keys(icons); if (!keys.length) return; sel.replaceChildren(...keys.map((k) => h('option', { value: k }, `${icons[k]}  ${k}`))); sel.value = b.f[f.k]; });
    sel.addEventListener('change', () => { b.f[f.k] = sel.value; fieldChanged(); });
    return h('span', { class: 'lb-f' }, sel);
  }

  function fieldEl(b, f, script) {
    const lab = f.label && f.type !== 'bool' ? h('span', { class: 'lb-lab' }, f.label) : null;
    let ctl;
    switch (f.type) {
      case 'msg': case 'val': case 'num': ctl = textField(b, f); break;
      case 'out': ctl = datalistInput(b, f, script.id); break;
      case 'in':
        if (f.cfg) ctl = selField(b, f, project.configs.length ? [['', '— выберите —'], ...project.configs.map((c) => [c.key, c.key])] : [['', 'нет настроек — добавьте в «настройки»']]);
        else ctl = datalistInput(b, f, script.id);
        break;
      case 'sel': ctl = selField(b, f, f.options); break;
      case 'bool': { const cb = h('input', { type: 'checkbox', class: 'wi-cb' }); cb.checked = !!b.f[f.k]; cb.addEventListener('change', () => { b.f[f.k] = cb.checked; fieldChanged(); }); ctl = h('label', { class: 'lb-f lb-check' }, cb, h('span', {}, f.label)); break; }
      case 'conds': ctl = condsField(b, f); break;
      case 'lines': { const ta = h('textarea', { class: 'lb-in multi lines', rows: 3, spellcheck: 'false' }); ta.value = b.f[f.k] || ''; ta.addEventListener('input', () => { b.f[f.k] = ta.value; fieldChanged(); }); ctl = h('span', { class: 'lb-f wide' }, ta); break; }
      case 'buttons': ctl = buttonsField(b, f); break;
      case 'data': ctl = dataField(b, f, script); break;
      case 'icon': ctl = iconField(b, f); break;
      default: ctl = h('span', {}, '?');
    }
    const wide = ['conds', 'buttons', 'lines'].includes(f.type) || (f.type === 'msg' && !f.single);
    return h('div', { class: 'lb-fld' + (wide ? ' wide' : ''), 'data-type': f.type, 'data-k': f.k }, lab, ctl);
  }

  /* ---------- блоки и скрипты ---------- */

  const wsBox = h('div', { class: 'lab-ws' });

  function clearDropMarks() { wsBox.querySelectorAll('.drop-before,.drop-after,.drop-in').forEach((n) => n.classList.remove('drop-before', 'drop-after', 'drop-in')); }

  function listOf(owner) {                                             // owner: {script} | {block, slot}
    if (owner.script) { const s = project.scripts.find((x) => x.id === owner.script); return s ? s.blocks : null; }
    const hit = labFindBlock(project, owner.block);
    return hit && hit.block.kids ? hit.block.kids[owner.slot] : null;
  }

  function performDrop(dest) {                                         // dest: {block, after} | {owner}
    const drag = S.drag; S.drag = null; clearDropMarks();
    if (!drag) return;
    let blk;
    if (drag.k === 'new') blk = labNewBlock(drag.t);
    else {
      const hit = labFindBlock(project, drag.id);
      if (!hit) return;
      blk = hit.block;
      let inside = false;
      if (dest.block === blk.id) return;
      labWalk(Object.values(blk.kids || {}).flat(), (x) => { if (x.id === (dest.block || (dest.owner && dest.owner.block))) inside = true; });
      if (inside) { toast('нельзя перенести блок внутрь самого себя', 'error'); return; }
      hit.list.splice(hit.index, 1);
    }
    if (dest.block) {
      const t = labFindBlock(project, dest.block);
      if (!t) return;
      t.list.splice(t.index + (dest.after ? 1 : 0), 0, blk);
    } else {
      const list = listOf(dest.owner);
      if (!list) return;
      list.push(blk);
    }
    S.sel = blk.id;
    structural();
  }

  function moveSel(dir) {
    const hit = labFindBlock(project, S.sel);
    if (!hit) return;
    const j = hit.index + dir;
    if (j < 0 || j >= hit.list.length) return;
    [hit.list[hit.index], hit.list[j]] = [hit.list[j], hit.list[hit.index]];
    structural();
  }
  function removeBlock(id) {
    const hit = labFindBlock(project, id);
    if (!hit) return;
    hit.list.splice(hit.index, 1);
    if (S.sel === id) S.sel = null;
    structural();
  }
  function duplicateBlock(id) {
    const hit = labFindBlock(project, id);
    if (!hit) return;
    const copy = labReid(labClone(hit.block));
    hit.list.splice(hit.index + 1, 0, copy);
    S.sel = copy.id;
    structural();
  }
  function toggleOff(id) { const hit = labFindBlock(project, id); if (hit) { hit.block.off = !hit.block.off; structural(); } }

  function duplicateScript(sc, index) {
    const c = labClone(sc); c.id = labId(); c.blocks.forEach(labReid);
    if (c.type === 'command') c.p.names = c.p.names.split(/[\s,]+/).map((x) => x + '_copy').join(', ');
    project.scripts.splice(index + 1, 0, c); structural();
  }
  function deleteScript(sc, index) {
    if (sc.blocks.length && !confirm('Удалить скрипт вместе со всеми его блоками?')) return;
    project.scripts.splice(index, 1); structural();
  }

  /* ---------- телефон: меню «⋯», вложить / вынести, перетаскивание пальцем ---------- */

  function parentOf(id) {                                             // {block, slot} — в чей слот вложен блок; null — лежит прямо в скрипте
    let res = null;
    (function walk(list, parent, slot) {
      for (const x of list) {
        if (x.id === id) { res = parent ? { block: parent, slot } : null; return true; }
        for (const [sl, kids] of Object.entries(x.kids || {})) if (walk(kids, x, sl)) return true;
      }
      return false;
    })(project.scripts.flatMap((sc) => sc.blocks), null, null);
    return res;
  }
  function nestTarget(id) {                                            // блок выше, у которого есть «внутри» — туда можно вложить
    const hit = labFindBlock(project, id);
    if (!hit || hit.index < 1) return null;
    const prev = hit.list[hit.index - 1], def = LAB_BLOCKS[prev.t];
    return def && def.slots && def.slots.length ? { block: prev, slot: def.slots[def.slots.length - 1] } : null;
  }
  function nestIntoPrev(id) {
    const hit = labFindBlock(project, id), tgt = nestTarget(id);
    if (!hit || !tgt) return;
    hit.list.splice(hit.index, 1);
    tgt.block.kids = tgt.block.kids || {};
    (tgt.block.kids[tgt.slot] = tgt.block.kids[tgt.slot] || []).push(hit.block);
    S.sel = id; structural(); focusSoon(id);
  }
  function moveOut(id) {
    const hit = labFindBlock(project, id), par = parentOf(id);
    if (!hit || !par) return;
    hit.list.splice(hit.index, 1);
    const ph = labFindBlock(project, par.block.id);
    ph.list.splice(ph.index + 1, 0, hit.block);
    S.sel = id; structural(); focusSoon(id);
  }
  function focusSoon(id) { requestAnimationFrame(() => { const el = wsBox.querySelector(`.lb[data-id="${id}"]`); if (el) { el.scrollIntoView({ block: 'center', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 900); } }); }
  function pasteAfter(id) {
    if (!LAB_CLIP.block) return;
    const copy = labReid(labClone(LAB_CLIP.block)), hit = labFindBlock(project, id);
    if (!hit) return;
    hit.list.splice(hit.index + 1, 0, copy); S.sel = copy.id; structural(); focusSoon(copy.id);
  }

  function blockMenu(id) {
    const hit = labFindBlock(project, id);
    if (!hit) return;
    const def = LAB_BLOCKS[hit.block.t] || { title: hit.block.t };
    const tgt = nestTarget(id), par = parentOf(id);
    const tgtDef = tgt && LAB_BLOCKS[tgt.block.t], parDef = par && LAB_BLOCKS[par.block.t];
    mActions(def.title, [
      { icon: '+', label: 'Добавить блок после этого', run: () => { S.sel = id; openPalette(); } },
      LAB_CLIP.block ? { icon: '⎘', label: 'Вставить скопированный после этого', hint: (LAB_BLOCKS[LAB_CLIP.block.t] || {}).title, run: () => pasteAfter(id) } : false,
      null,
      { icon: '↑', label: 'Выше', disabled: hit.index < 1, run: () => { S.sel = id; moveSel(-1); focusSoon(id); } },
      { icon: '↓', label: 'Ниже', disabled: hit.index >= hit.list.length - 1, run: () => { S.sel = id; moveSel(1); focusSoon(id); } },
      { icon: '⤵', label: 'Вложить в блок выше', hint: tgt ? `внутрь «${tgtDef.title}»` : 'над ним нет блока, в который можно вкладывать', disabled: !tgt, run: () => nestIntoPrev(id) },
      { icon: '⤴', label: 'Вынести наружу', hint: par ? `из «${parDef ? parDef.title : par.block.t}»` : 'блок и так не вложен', disabled: !par, run: () => moveOut(id) },
      null,
      { icon: '⧉', label: 'Дублировать', run: () => { duplicateBlock(id); focusSoon(S.sel); } },
      { icon: '⎗', label: 'Копировать', hint: 'потом «Вставить» в меню другого блока', run: () => { LAB_CLIP.block = labClone(hit.block); toast('блок скопирован', 'ok'); } },
      { icon: hit.block.off ? '▶' : '⏸', label: hit.block.off ? 'Включить блок' : 'Выключить (не удаляя)', run: () => toggleOff(id) },
      { icon: '×', label: 'Удалить', bad: true, run: () => removeBlock(id) },
    ].filter((x) => x !== false));
  }

  function scriptMenu(sc, index) {
    mActions((LAB_SCRIPT_TYPES[sc.type] || { title: sc.type }).title + ' · ' + labScriptSummary(sc), [
      { icon: '⚙', label: 'Настроить запуск', run: () => openScriptDrawer(sc) },
      { icon: '+', label: 'Добавить блок в конец', run: () => { S.sel = null; S.active = sc.id; openPalette(); } },
      { icon: sc.collapsed ? '▸' : '▾', label: sc.collapsed ? 'Развернуть' : 'Свернуть', run: () => { sc.collapsed = !sc.collapsed; structural(); } },
      null,
      { icon: '↑', label: 'Скрипт выше', disabled: index < 1, run: () => { [project.scripts[index - 1], project.scripts[index]] = [project.scripts[index], project.scripts[index - 1]]; structural(); } },
      { icon: '↓', label: 'Скрипт ниже', disabled: index >= project.scripts.length - 1, run: () => { [project.scripts[index + 1], project.scripts[index]] = [project.scripts[index], project.scripts[index + 1]]; structural(); } },
      { icon: '⧉', label: 'Дублировать скрипт', run: () => duplicateScript(sc, index) },
      { icon: '×', label: 'Удалить скрипт', bad: true, run: () => deleteScript(sc, index) },
    ]);
  }

  // долгое нажатие на шапку блока → тащим пальцем (HTML drag&drop на сенсорных экранах не работает)
  function touchDrag(head, el, id) {
    let timer = 0, x0 = 0, y0 = 0, active = false, ghost = null, dest = null, raf = 0, lastY = 0;
    const cancelWait = () => { clearTimeout(timer); timer = 0; };
    head.addEventListener('touchstart', (e) => {
      if (e.touches.length !== 1 || e.target.closest('input, textarea, select, button, label, .lb-tgbox')) return;
      x0 = e.touches[0].clientX; y0 = lastY = e.touches[0].clientY;
      timer = setTimeout(start, 420);
    }, { passive: true });
    head.addEventListener('touchmove', (e) => { if (timer && Math.hypot(e.touches[0].clientX - x0, e.touches[0].clientY - y0) > 10) cancelWait(); }, { passive: true });
    head.addEventListener('touchend', cancelWait);
    head.addEventListener('touchcancel', cancelWait);
    head.addEventListener('contextmenu', (e) => { if (timer || active) e.preventDefault(); });

    function start() {
      timer = 0; active = true;
      try { navigator.vibrate && navigator.vibrate(12); } catch (x) { /* нет вибро */ }
      S.drag = { k: 'move', id };
      select(id);
      const def = LAB_BLOCKS[(labFindBlock(project, id) || { block: {} }).block.t] || { title: 'блок' };
      ghost = h('div', { class: 'lab-ghost', style: el.getAttribute('style') || '' }, def.title);
      document.body.appendChild(ghost);
      el.classList.add('dragging');
      document.documentElement.classList.add('lab-touchdrag');
      place(x0, y0);
      document.addEventListener('touchmove', onMove, { passive: false });
      document.addEventListener('touchend', onEnd);
      document.addEventListener('touchcancel', onCancel);
      raf = requestAnimationFrame(autoScroll);
    }
    function place(x, y) { ghost.style.transform = `translate(${Math.round(x - 30)}px, ${Math.round(y - 46)}px)`; }
    function onMove(e) {
      e.preventDefault();
      const t = e.touches[0]; lastY = t.clientY;
      place(t.clientX, t.clientY);
      locate(t.clientX, t.clientY);
    }
    function locate(x, y) {
      clearDropMarks(); dest = null;
      const under = document.elementFromPoint(x, y);
      if (!under || el.contains(under)) return;
      const lb = under.closest('.lb');
      const emptyList = under.closest('.lb-empty') ? under.closest('.lb-list') : null;
      if (emptyList && emptyList._owner) { emptyList.classList.add('drop-in'); dest = { owner: emptyList._owner }; return; }
      if (lb && wsBox.contains(lb)) {
        const r = lb.getBoundingClientRect(), after = y - r.top > Math.min(r.height / 2, 30);
        lb.classList.add(after ? 'drop-after' : 'drop-before');
        dest = { block: lb.dataset.id, after };
        return;
      }
      const list = under.closest('.lb-list');
      if (list && list._owner && wsBox.contains(list)) { list.classList.add('drop-in'); dest = { owner: list._owner }; }
    }
    function autoScroll() {
      if (!active) return;
      const edge = 90, H = window.innerHeight;
      if (lastY < edge) window.scrollBy(0, -Math.ceil((edge - lastY) / 6));
      else if (lastY > H - edge) window.scrollBy(0, Math.ceil((lastY - (H - edge)) / 6));
      raf = requestAnimationFrame(autoScroll);
    }
    function finish() {
      active = false; cancelAnimationFrame(raf);
      document.removeEventListener('touchmove', onMove, { passive: false });
      document.removeEventListener('touchend', onEnd);
      document.removeEventListener('touchcancel', onCancel);
      document.documentElement.classList.remove('lab-touchdrag');
      if (ghost) { ghost.remove(); ghost = null; }
      el.classList.remove('dragging');
    }
    function onEnd() { const d = dest; finish(); if (d) { performDrop(d); focusSoon(id); } else { S.drag = null; clearDropMarks(); } }
    function onCancel() { finish(); S.drag = null; clearDropMarks(); }
  }

  function select(id) {
    S.sel = id;
    wsBox.querySelectorAll('.lb.sel').forEach((n) => n.classList.remove('sel'));
    const el = wsBox.querySelector(`.lb[data-id="${id}"]`);
    if (el) el.classList.add('sel');
    const hit = labFindBlock(project, id);
    if (hit) S.active = hit.script.id;
    renderPalette(); updateTools();
  }

  function dropList(listEl, owner) {                                    // зона «в конец списка»
    listEl._owner = owner;
    listEl.addEventListener('dragover', (e) => { if (!S.drag) return; e.preventDefault(); e.stopPropagation(); clearDropMarks(); listEl.classList.add('drop-in'); });
    listEl.addEventListener('dragleave', (e) => { if (e.target === listEl) listEl.classList.remove('drop-in'); });
    listEl.addEventListener('drop', (e) => { e.preventDefault(); e.stopPropagation(); performDrop({ owner }); });
  }

  function blockEl(b, script) {
    const def = LAB_BLOCKS[b.t];
    if (!def) {
      return h('div', { class: 'lb unknown bad', 'data-id': b.id }, h('div', { class: 'lb-head' }, h('b', { class: 'lb-title' }, `Неизвестный блок «${b.t}»`),
        h('span', { class: 'lb-tools' }, h('button', { type: 'button', class: 'lb-x', onclick: () => removeBlock(b.id) }, '×'))));
    }
    const cat = LAB_CAT[def.cat];
    const el = h('div', { class: 'lb' + (b.off ? ' off' : '') + (S.sel === b.id ? ' sel' : '') + (def.slots ? ' cont' : ''), 'data-id': b.id, style: `--c:${cat.color}` });
    const grip = h('span', { class: 'lb-grip', draggable: 'true', title: 'перетащить блок' }, '⋮⋮');
    grip.addEventListener('dragstart', (e) => { S.drag = { k: 'move', id: b.id }; e.dataTransfer.setData('text/lab', b.id); e.dataTransfer.effectAllowed = 'move'; try { e.dataTransfer.setDragImage(el, 12, 12); } catch (x) { /* не критично */ } setTimeout(() => el.classList.add('dragging'), 0); });
    grip.addEventListener('dragend', () => { S.drag = null; el.classList.remove('dragging'); clearDropMarks(); });
    const fields = h('div', { class: 'lb-fields' }, def.fields.map((f) => fieldEl(b, f, script)));
    const whenFields = def.fields.filter((f) => typeof f.when === 'function');
    if (whenFields.length) {                                           // поля, видимые только при определённом значении другого (например, кнопки при «задать новые»)
      const applyWhen = () => whenFields.forEach((f) => { const n = fields.querySelector(`.lb-fld[data-k="${f.k}"]`); if (n) n.hidden = !f.when(b.f); });
      applyWhen();
      fields.addEventListener('change', applyWhen);
      fields.addEventListener('input', applyWhen);
    }
    const tools = h('span', { class: 'lb-tools' },
      h('button', { type: 'button', class: 'lb-x', title: 'выше (Alt+↑)', onclick: (e) => { e.stopPropagation(); S.sel = b.id; moveSel(-1); } }, '↑'),
      h('button', { type: 'button', class: 'lb-x', title: 'ниже (Alt+↓)', onclick: (e) => { e.stopPropagation(); S.sel = b.id; moveSel(1); } }, '↓'),
      h('button', { type: 'button', class: 'lb-x', title: 'дублировать (Ctrl+D)', onclick: (e) => { e.stopPropagation(); duplicateBlock(b.id); } }, '⧉'),
      h('button', { type: 'button', class: 'lb-x', title: b.off ? 'включить блок' : 'выключить блок (не удаляя)', onclick: (e) => { e.stopPropagation(); toggleOff(b.id); } }, b.off ? '▶' : '⏸'),
      h('button', { type: 'button', class: 'lb-x bad', title: 'удалить (Delete)', onclick: (e) => { e.stopPropagation(); removeBlock(b.id); } }, '×'));
    const more = h('button', { type: 'button', class: 'lb-more m-only', title: 'действия с блоком', 'aria-label': 'действия с блоком', onclick: (e) => { e.stopPropagation(); select(b.id); blockMenu(b.id); } }, '⋯');
    const head = h('div', { class: 'lb-head' }, grip, h('span', { class: 'lb-ico' }, cat.icon), h('b', { class: 'lb-title', title: def.hint || '' }, def.title), more, fields, tools);
    touchDrag(head, el, b.id);
    head.addEventListener('mousedown', (e) => { if (!e.target.closest('input,textarea,select,button,label')) select(b.id); });
    head.addEventListener('focusin', () => { if (S.sel !== b.id) select(b.id); });
    el.appendChild(head);
    if (def.hint) head.title = def.hint;
    (def.slots || []).forEach((slot) => {
      const wrap = h('div', { class: 'lb-slot' }, h('div', { class: 'lb-slot-t' }, LAB_SLOT_TITLE[b.t] || 'внутри'));
      const listEl = h('div', { class: 'lb-list' });
      const kids = (b.kids && b.kids[slot]) || [];
      kids.forEach((k) => listEl.appendChild(blockEl(k, script)));
      if (!kids.length) listEl.appendChild(h('div', { class: 'lb-empty' }, h('span', { class: 'm-hide' }, 'перетащите блоки сюда или выберите блок и нажмите на палитре'), h('span', { class: 'm-only' }, 'пусто — перетащите блок сюда или «⋯ → Вложить в блок выше»')));
      dropList(listEl, { block: b.id, slot });
      wrap.appendChild(listEl);
      el.appendChild(wrap);
    });
    // перетаскивание поверх блока: до / после
    el.addEventListener('dragover', (e) => {
      if (!S.drag) return;
      if (e.target.closest('.lb-list') && e.target.closest('.lb-list') !== el.parentElement && el.contains(e.target.closest('.lb-list'))) return;      // над вложенным списком — его зона
      e.preventDefault(); e.stopPropagation();
      const r = el.getBoundingClientRect(), after = e.clientY - r.top > Math.min(r.height / 2, 30);
      clearDropMarks(); el.classList.add(after ? 'drop-after' : 'drop-before'); el._after = after;
    });
    el.addEventListener('drop', (e) => {
      if (!S.drag) return;
      if (e.target.closest('.lb-list') && e.target.closest('.lb-list') !== el.parentElement && el.contains(e.target.closest('.lb-list'))) return;
      e.preventDefault(); e.stopPropagation(); performDrop({ block: b.id, after: !!el._after });
    });
    return el;
  }

  function scriptEl(s, index) {
    const t = LAB_SCRIPT_TYPES[s.type] || { title: s.type };
    const card = h('div', { class: 'ls' + (s.collapsed ? ' collapsed' : ''), 'data-id': s.id });
    const sum = labScriptSummary(s);
    const head = h('div', { class: 'ls-head' },
      h('button', { type: 'button', class: 'lb-x', title: s.collapsed ? 'развернуть' : 'свернуть', onclick: () => { s.collapsed = !s.collapsed; structural(); } }, s.collapsed ? '▸' : '▾'),
      h('span', { class: 'ls-hat' }, t.title), h('span', { class: 'ls-side' }, s.side === 'bot' ? 'стационарный бот' : 'юзербот'),
      h('b', { class: 'ls-sum' }, sum), h('span', { class: 'grow' }),
      h('span', { class: 'lb-tools always' },
        h('button', { type: 'button', class: 'lb-x', title: 'настроить запуск', onclick: () => openScriptDrawer(s) }, '⚙'),
        h('button', { type: 'button', class: 'lb-x m-only', title: 'действия со скриптом', 'aria-label': 'действия со скриптом', onclick: () => scriptMenu(s, index) }, '⋯'),
        h('button', { type: 'button', class: 'lb-x m-hide', title: 'выше', onclick: () => { if (index > 0) { [project.scripts[index - 1], project.scripts[index]] = [project.scripts[index], project.scripts[index - 1]]; structural(); } } }, '↑'),
        h('button', { type: 'button', class: 'lb-x m-hide', title: 'ниже', onclick: () => { if (index < project.scripts.length - 1) { [project.scripts[index + 1], project.scripts[index]] = [project.scripts[index], project.scripts[index + 1]]; structural(); } } }, '↓'),
        h('button', { type: 'button', class: 'lb-x m-hide', title: 'дублировать скрипт', onclick: () => duplicateScript(s, index) }, '⧉'),
        h('button', { type: 'button', class: 'lb-x bad m-hide', title: 'удалить скрипт', onclick: () => deleteScript(s, index) }, '×')));
    
    head.addEventListener('mousedown', () => { S.active = s.id; renderPaletteSoon(); });
    card.appendChild(head);
    if (!s.collapsed) {
      const listEl = h('div', { class: 'lb-list root' });
      s.blocks.forEach((b) => listEl.appendChild(blockEl(b, s)));
      if (!s.blocks.length) listEl.appendChild(h('div', { class: 'lb-empty' }, h('span', { class: 'm-hide' }, 'Скрипт пуст. Выберите блок на палитре слева (клик — добавить, или перетащите сюда).'), h('span', { class: 'm-only' }, 'Скрипт пуст — нажмите «+», чтобы добавить блок.')));
      dropList(listEl, { script: s.id });
      card.appendChild(listEl);
    }
    return card;
  }

  function renderScripts() {
    wsBox.replaceChildren(...project.scripts.map(scriptEl),
      h('button', { type: 'button', class: 'lab-addscript', onclick: () => openScriptDrawer(null) }, '+ новый скрипт (команда, таймер, событие…)'));
    refreshDatalists();
  }
  let paletteTimer = 0;
  const renderPaletteSoon = () => { clearTimeout(paletteTimer); paletteTimer = setTimeout(renderPalette, 30); };

  /* ---------- палитра ---------- */

  const palBox = h('div', { class: 'lab-pal' });
  const palCats = h('div', { class: 'lab-cats' });
  const palList = h('div', { class: 'lab-pallist' });
  const palSearch = h('input', { class: 'wi-field lab-search', placeholder: 'поиск блока…', spellcheck: 'false', 'aria-label': 'поиск блока' });
  palSearch.addEventListener('input', () => { S.search = palSearch.value.trim().toLowerCase(); renderPalette(); });
  palBox.append(palSearch, palCats, palList);

  function activeScript() {
    if (S.sel) { const hit = labFindBlock(project, S.sel); if (hit) return hit.script; }
    return project.scripts.find((s) => s.id === S.active) || project.scripts[project.scripts.length - 1] || null;
  }

  function insertNew(t) {
    const script = activeScript();
    if (!script) { toast('сначала добавьте скрипт', 'error'); openScriptDrawer(null); return; }
    const blk = labNewBlock(t);
    let hit = S.sel ? labFindBlock(project, S.sel) : null;
    const chain = LAB_BLOCKS[t].chain;
    if (hit && (chain === 'mid' || chain === 'tail' || chain === 'except')) {       // «Иначе» / «Если ошибка» вставляем после своего «Если» / «Попробовать», а не внутрь него
      const want = chain === 'except' ? ['try'] : ['head', 'mid'];
      const trail = [];
      (function walk(list) { for (const x of list) { trail.push(x.id); if (x.id === hit.block.id) return true; for (const sl of Object.values(x.kids || {})) if (walk(sl)) return true; trail.pop(); } return false; })(script.blocks);
      for (let i = trail.length - 1; i >= 0; i--) { const cand = labFindBlock(project, trail[i]); if (cand && want.includes((LAB_BLOCKS[cand.block.t] || {}).chain)) { hit = cand; break; } }
    }
    if (hit) hit.list.splice(hit.index + 1, 0, blk); else script.blocks.push(blk);
    S.sel = blk.id; S.active = script.id;
    if (palClose) palClose();
    structural();
    requestAnimationFrame(() => { const el = wsBox.querySelector(`.lb[data-id="${blk.id}"]`); if (el) { el.scrollIntoView({ block: 'nearest', behavior: 'smooth' }); el.classList.add('flash'); setTimeout(() => el.classList.remove('flash'), 900); } });
  }

  function renderPalette() {
    const script = activeScript();
    const ctxExtra = S.sel ? labCtxAt(project, S.sel) : [];
    const allowed = (def) => !script || labBlockAllowed(def, script, ctxExtra);
    palCats.replaceChildren(...LAB_CATS.map((c) => {
      const n = Object.values(LAB_BLOCKS).filter((d) => d.cat === c.id).length;
      return h('button', { type: 'button', class: 'lab-cat' + (!S.search && S.cat === c.id ? ' on' : ''), style: `--c:${c.color}`, title: `${c.title} · ${n}`, onclick: () => { S.cat = c.id; S.search = ''; palSearch.value = ''; renderPalette(); } },
        h('span', { class: 'i' }, c.icon), h('span', { class: 't' }, c.title), h('span', { class: 'n' }, String(n)));
    }));
    let defs = Object.values(LAB_BLOCKS);
    if (S.search) defs = defs.filter((d) => d.title.toLowerCase().includes(S.search) || (d.hint || '').toLowerCase().includes(S.search) || d.id.includes(S.search) || LAB_CAT[d.cat].title.toLowerCase().includes(S.search));
    else defs = defs.filter((d) => d.cat === S.cat);
    const head = S.search ? h('div', { class: 'lab-pal-h' }, `найдено: ${defs.length}`) : h('div', { class: 'lab-pal-h' }, LAB_CAT[S.cat].title);
    palList.replaceChildren(head, ...defs.map((d) => {
      const ok = allowed(d);
      const it = h('div', { class: 'lab-pi' + (ok ? '' : ' dim'), draggable: 'true', style: `--c:${LAB_CAT[d.cat].color}`, title: (d.hint || '') + (ok ? '' : '\nНе подходит для выбранного скрипта') , 'data-t': d.id },
        h('span', { class: 'lab-pi-t' }, d.title), S.search ? h('span', { class: 'lab-pi-c' }, LAB_CAT[d.cat].title) : null);
      it.addEventListener('click', () => insertNew(d.id));
      it.addEventListener('dragstart', (e) => { S.drag = { k: 'new', t: d.id }; e.dataTransfer.setData('text/lab', d.id); e.dataTransfer.effectAllowed = 'copy'; });
      it.addEventListener('dragend', () => { S.drag = null; clearDropMarks(); });
      return it;
    }));
    if (!defs.length) palList.appendChild(h('div', { class: 'wi-hint', style: 'padding:10px' }, 'Ничего не нашлось.'));
  }

  /* ---------- окна: скрипт, о модуле, настройки, примеры ---------- */

  function openScriptDrawer(existing) {
    const st = existing ? { type: existing.type, side: existing.side, p: labClone(existing.p) } : { type: 'command', side: 'user', p: labNewScript('command', 'user').p };
    const formBox = h('div', { class: 'wi-cform' });
    const ui = { changed() { /* без предпросмотра */ }, rebuild() { draw(); } };
    const typesFor = (side) => Object.entries(LAB_SCRIPT_TYPES).filter(([, v]) => v.side.includes(side));
    function setType(type) { st.type = type; st.p = labNewScript(type, st.side).p; draw(); }
    function draw() {
      const parts = [];
      if (!existing) {
        parts.push(wiFld('Для кого', wiSeg({ get side() { return st.side; }, set side(v) { st.side = v; if (!LAB_SCRIPT_TYPES[st.type].side.includes(v)) { st.type = 'command'; } st.p = labNewScript(st.type, st.side).p; } }, 'side', [['user', 'юзербот'], ['bot', 'стационарный бот']], ui, true)));
        const sel = h('select', { class: 'wi-field' }, typesFor(st.side).map(([v, t]) => h('option', { value: v }, t.title)));
        sel.value = st.type; sel.addEventListener('change', () => setType(sel.value));
        parts.push(wiFld('Что запускает скрипт', sel, (LAB_SCRIPT_TYPES[st.type] || {}).desc));
      } else {
        parts.push(h('div', { class: 'wi-note' }, `${LAB_SCRIPT_TYPES[st.type].title} · ${st.side === 'bot' ? 'стационарный бот' : 'юзербот'}. Тип запуска после создания не меняется — блоки внутри рассчитаны на него.`));
      }
      const p = st.p, bot = st.side === 'bot';
      switch (st.type) {
        case 'command':
          parts.push(wiFld(bot ? 'Команды (без «/»)' : 'Команды (без префикса)', wiText(p, 'names', ui), 'через запятую; первая — основная, остальные — синонимы'),
            wiFld('Описание', wiText(p, 'desc', ui)));
          if (bot) parts.push(wiFld('Кто может вызвать', wiSelect(p, 'access', [['self', 'только владелец'], ['public', 'все']], ui)));
          break;
        case 'watcher':
          if (bot) parts.push(wiFld('Что слушает', wiSelect(p, 'watch', [['bot', 'сообщения боту'], ['userbot', 'сообщения юзербота'], ['all', 'и то и другое']], ui)), wiFld('Кто может', wiSelect(p, 'access', [['self', 'только владелец'], ['public', 'все']], ui)));
          else parts.push(wiFld('Какие сообщения', wiSelect(p, 'dir', [['any', 'все'], ['in', 'только входящие'], ['out', 'только мои']], ui)), wiFld('Где', wiSelect(p, 'kind', [['any', 'в любых чатах'], ['private', 'только в личных'], ['group', 'только в группах'], ['channel', 'только в каналах']], ui)),
            wiFld('Только эти чаты (id через запятую)', wiText(p, 'chat', ui), 'пусто — любые'));
          parts.push(wiFld('Только если в тексте есть', wiText(p, 'contains', ui), 'пусто — любые сообщения; регистр не важен'));
          break;
        case 'cycle':
          parts.push(wiFld('Как часто', h('div', { class: 'wi-inline' }, wiText(p, 'n', ui, { type: 'number' }), wiSelect(p, 'unit', LAB_INTERVAL_UNITS, ui))));
          break;
        case 'tg_event': case 'bot_member': {
          const opts = st.type === 'tg_event' ? LAB_TG_KINDS : [['join', 'вошёл в чат'], ['left', 'вышел из чата']];
          p.kinds = Array.isArray(p.kinds) ? p.kinds : [];
          parts.push(h('div', { class: 'wi-lab' }, 'Что должно произойти (если ничего не выбрано — любое событие)'),
            h('div', { class: 'wi-checks' }, opts.map(([k, l]) => { const cb = h('input', { type: 'checkbox', class: 'wi-cb' }); cb.checked = p.kinds.includes(k); cb.addEventListener('change', () => { p.kinds = p.kinds.filter((x) => x !== k); if (cb.checked) p.kinds.push(k); }); return h('label', { class: 'wi-check' }, cb, h('span', {}, l)); })));
          if (st.type === 'tg_event') parts.push(wiFld('Только эти чаты (id через запятую)', wiText(p, 'chat', ui), 'пусто — любые'));
          break;
        }
        default: parts.push(h('div', { class: 'wi-note' }, (LAB_SCRIPT_TYPES[st.type] || {}).desc || ''));
      }
      formBox.replaceChildren(...parts);
    }
    draw();
    wiDrawer(existing ? 'Запуск скрипта' : 'Новый скрипт', formBox, null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => {
        if (existing) { existing.p = st.p; }
        else { const s = labNewScript(st.type, st.side); s.p = st.p; project.scripts.push(s); S.active = s.id; }
        close(); structural();
      } }, existing ? 'готово' : 'добавить')]);
  }

  function openMeta() {
    const m = labClone(project.meta);
    const ui = { changed() { prev.textContent = authorOf(); }, rebuild() { /* нет */ } };
    const locked = m.locked_author !== undefined && m.locked_author !== null;
    const authorOf = () => (locked ? m.locked_author : (String(m.author_extra || '').trim() ? `${String(m.author_extra).trim()} | FRX WebIDE Lab` : 'FRX WebIDE Lab'));
    const prev = h('b', {}, authorOf());
    wiDrawer('О модуле', [
      wiFld('Название (NAME)', wiText(m, 'name', ui)),
      wiFld('Идентификатор (MODULE_NAME)', wiText(m, 'module_name', ui, { disabled: !isNew }), isNew ? 'латиница, цифры, _ и - · по нему модуль хранит данные; после создания не меняется' : 'у созданного модуля не меняется'),
      wiFld('Версия (VERSION)', wiText(m, 'version', ui)),
      wiFld('Описание (DESCRIPTION)', wiText(m, 'description', ui, { area: true, rows: 2 })),
      wiFld('Ваше имя в авторах (необязательно)', wiText(m, 'author_extra', ui, { disabled: locked }), locked ? 'AUTHOR у созданного модуля защищён' : 'в файл попадёт «ваше имя | FRX WebIDE Lab»'),
      h('div', { class: 'wi-hint' }, 'AUTHOR в файле: ', prev)], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => { project.meta = m; close(); markDirty(); pushHist(); recompile(true); title.textContent = m.name || m.module_name; } }, 'готово')]);
  }

  function openConfigs() {
    const cfgs = labClone(project.configs);
    const list = h('div', { class: 'lab-cfgs' });
    const ui = { changed() { /* применится по «готово» */ }, rebuild() { draw(); } };
    const optList = (c) => String(c.options || '').split(',').map((x) => x.trim()).filter(Boolean);
    function multiPicker(c) {                                       // «выбор нескольких»: варианты и отметки «выбрано по умолчанию»
      const box = h('div', { class: 'lab-chips' });
      const paint = () => {
        const opts = optList(c), sel = new Set(String(c.default || '').split(',').map((x) => x.trim()).filter((x) => opts.includes(x)));
        c.default = opts.filter((o) => sel.has(o)).join(',');
        box.replaceChildren(...(opts.length ? opts.map((o) => h('button', { type: 'button', class: 'chip small' + (sel.has(o) ? ' on' : ''), title: 'выбрано по умолчанию',
          onclick: (ev) => { if (sel.has(o)) sel.delete(o); else sel.add(o); c.default = opts.filter((x) => sel.has(x)).join(','); ev.currentTarget.classList.toggle('on', sel.has(o)); } }, o))
          : [h('span', { class: 'wi-hint' }, 'Впишите варианты через запятую — они появятся здесь.')]));
      };
      paint();
      return { box, paint };
    }
    function draw() {
      list.replaceChildren(...cfgs.map((c, i) => {
        const mp = c.type === 'choices' ? multiPicker(c) : null;
        const cui = mp ? { changed() { mp.paint(); }, rebuild() { /* нет */ } } : ui;
        return h('div', { class: 'lab-cfg' },
          h('div', { class: 'wi-inline' }, wiText(c, 'key', ui, { placeholder: 'ключ, например greeting' }), wiSelect(c, 'type', LAB_CFG_TYPES, ui, true),
            h('button', { class: 'wi-ico bad', type: 'button', title: 'удалить настройку', onclick: () => { cfgs.splice(i, 1); draw(); } }, '×')),
          wiText(c, 'description', ui, { placeholder: 'описание — его увидят в «.config»' }),
          c.type === 'choices' ? wiFld('Варианты (через запятую)', wiText(c, 'options', cui)) : null,
          c.type === 'choices' ? wiFld('Выбрано по умолчанию (нажмите, чтобы отметить)', mp.box)
            : c.type === 'bool' ? wiCheck(c, 'default', 'включено по умолчанию', ui) : wiFld(c.type === 'choice' ? 'Выбрано по умолчанию' : 'Значение по умолчанию', wiText(c, 'default', ui, { type: c.type === 'int' || c.type === 'float' ? 'number' : 'text' })),
          c.type === 'choice' ? wiFld('Варианты (через запятую)', wiText(c, 'options', ui)) : null);
      }),
        cfgs.length ? null : h('div', { class: 'wi-note' }, 'Настроек пока нет. Настройка — значение, которое пользователь может менять командой .config, не трогая блоки; читается блоком «Прочитать настройку».'),
        h('button', { class: 'btn small', type: 'button', onclick: () => { cfgs.push({ key: 'option' + (cfgs.length + 1), description: '', type: 'str', default: '', options: '' }); draw(); } }, '+ настройка'));
    }
    draw();
    wiDrawer('Настройки модуля', list, null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => { project.configs = cfgs; close(); structural(); } }, 'готово')]);
  }

  function openExamples() {
    const ex = labExamples();
    const box = h('div', { class: 'lab-examples' }, ex.map((e) => h('button', { type: 'button', class: 'snip', onclick: () => {
      if (S.dirty && !confirm('Заменить текущие скрипты примером? Несохранённые изменения пропадут.')) return;
      const p = labSanitize(e.make());
      if (isNew) project = p; else { project.scripts = p.scripts; project.configs = p.configs; }
      S.sel = null; S.active = null; pushHist();
      document.querySelector('.veil.wi-veil .wi-ico')?.click();
      renderScripts(); renderPalette(); recompile(true); markDirty(); updateTools(); title.textContent = project.meta.name;
    } }, h('div', { class: 't' }, e.title), h('div', { class: 'd' }, e.desc))));
    wiDrawer('Примеры', [h('div', { class: 'wi-hint', style: 'margin-bottom:8px' }, 'Откройте пример, посмотрите, как он собран, поменяйте и запустите в Sandbox.'), box], null, (close) => [h('button', { class: 'btn', onclick: close }, 'закрыть')]);
  }

  /* ---------- сохранение ---------- */

  async function save() {
    if (S.last.errors.length) { toast(`В проекте ошибок: ${S.last.errors.length} — исправьте их (вкладка «проверка»)`, 'error'); if (mIsPhone()) setMview('check'); else setRtab('check'); return; }
    const source = labBuildSource(project, S.last);
    await act(async () => {
      if (isNew) {
        const r = await api('/webide/create', { source });
        try { localStorage.removeItem(LAB_DRAFT_KEY); } catch (e) { /* нет хранилища */ }
        if (drafts) await drafts.done(r.hash);
        S.savedJson = snapshot(); S.dirty = false; GUARD = null;
        toast(`модуль «${r.name}» создан и загружен`, 'ok');
        location.hash = '#/lab/' + enc(r.module);
        return;
      }
      const r = await api('/webide/save', { module: moduleId, source });
      if (r.unchanged) toast('изменений в коде нет', 'ok'); else toast(`сохранено и перезагружено (v${r.version})`, 'ok');
      S.savedJson = snapshot(); S.dirty = false; dirtyTag.hidden = true; saveBtn.classList.remove('dirty'); edited = false; banner.hidden = true; original = r.source || source;
      if (drafts) drafts.done(r.hash);
    });
  }

  /* ---------- панель инструментов и сборка страницы ---------- */

  const title = h('span', {}, project.meta.name || project.meta.module_name);
  const dirtyTag = h('span', { class: 'tag wi-dirty m-hide', hidden: true }, 'не сохранено');
  const saveBtn = h('button', { class: 'btn primary lab-save', type: 'button', onclick: () => save() }, isNew ? ['создать', h('span', { class: 'm-hide' }, ' модуль')] : 'сохранить');
  const undoBtn = h('button', { class: 'btn small', type: 'button', title: 'отменить (Ctrl+Z)', onclick: undo }, '↶');
  const redoBtn = h('button', { class: 'btn small', type: 'button', title: 'повторить (Ctrl+Y)', onclick: redo }, '↷');
  const sbBtn = h('button', { class: 'btn wi-bar-btn', type: 'button', title: 'проверить модуль в песочнице (Ctrl+Enter)', onclick: () => { if (dock.isOpen()) dock.close(); else dock.open(); } }, 'sandbox');
  function updateTools() { undoBtn.disabled = S.hist.length < 2; redoBtn.disabled = !S.fut.length; }

  const banner = h('div', { class: 'glass lab-banner', hidden: !edited },
    h('b', {}, 'Код этого модуля правили вручную. '), h('span', {}, 'Если сохранить из Lab, ручные правки заменятся кодом из проекта.'),
    h('button', { class: 'btn small', type: 'button', onclick: () => { if (GUARD && !GUARD()) return; location.hash = '#/webide/' + enc(moduleId); } }, 'открыть как код'));

  dock = sbCreateDock({
    key: 'lab_' + (isNew ? '__new__' : moduleId), alive: () => T === TOKEN,
    getSource: () => { if (S.last.errors.length) throw new Error(`В проекте ошибок: ${S.last.errors.length} — исправьте их (вкладка «проверка»)`); return labBuildSource(project, S.last); },
    onGoto: (line, mark) => { const id = S.last.sourceMap[line - 1]; if (id) focusBlock(id); else if (line > 0 && mark) toast(`строка ${line} сгенерированного кода — вне блоков`, 'error'); },
    onToggle: (on) => { sbBtn.classList.toggle('on', on); if (on) setTimeout(() => dock.el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 60); },
  });

  const rbody = h('div', { class: 'lab-rbody' });
  const rtabBtns = {};
  const rtabs = h('div', { class: 'wi-tabs' }, [['code', 'код Python'], ['check', 'проверка'], ['vars', 'переменные']].map(([id, label]) => {
    const b = h('button', { type: 'button', class: id === S.rtab ? 'on' : '', onclick: () => setRtab(id) }, label, id === 'check' ? errBadge : null);
    rtabBtns[id] = b; return b;
  }));
  function setRtab(id) { S.rtab = id; Object.entries(rtabBtns).forEach(([k, b]) => b.classList.toggle('on', k === id)); rbody.replaceChildren(id === 'code' ? codeBox : id === 'check' ? checkBox : varsBox); renderRight(true); }
  const rightCol = h('div', { class: 'glass lab-right' }, rtabs, rbody);
  const palWrap = h('div', { class: 'glass lab-palwrap' }, palBox);
  const draftSlot = h('div', { class: 'wi-draftslot' });
  const centerCol = h('div', { class: 'lab-center' }, draftSlot, banner, wsBox);

  const toCode = () => { if (GUARD && !GUARD()) return; location.hash = '#/webide/' + enc(moduleId); };
  const moreBtn = h('button', { class: 'btn small m-only', type: 'button', title: 'ещё', 'aria-label': 'ещё', onclick: () => mActions('Модуль', [
    { icon: 'ℹ', label: 'О модуле', hint: 'название, версия, описание', run: openMeta },
    { icon: '⚙', label: 'Настройки модуля', hint: 'то, что меняется через .config', run: openConfigs },
    { icon: '＋', label: 'Новый скрипт', hint: 'команда, таймер, событие…', run: () => openScriptDrawer(null) },
    { icon: '☰', label: 'Примеры', run: openExamples },
    isNew ? false : { icon: '</>', label: 'Открыть как код', run: toCode },
  ].filter(Boolean)) }, '⋯');
  const side = [...(isNew ? [] : [h('button', { class: 'btn small m-hide', type: 'button', title: 'посмотреть и править как код', onclick: toCode }, 'код')]),
    h('button', { class: 'btn small m-hide', type: 'button', onclick: openExamples }, 'примеры'), h('button', { class: 'btn small m-hide', type: 'button', onclick: openMeta }, 'о модуле'),
    h('button', { class: 'btn small m-hide', type: 'button', onclick: openConfigs }, 'настройки'), undoBtn, redoBtn, moreBtn, sbBtn, dirtyTag, saveBtn];

  // телефон: одна колонка за раз — блоки или код / проверка / переменные; палитра — по кнопке «+»
  const layout = h('div', { class: 'lab-layout' }, palWrap, centerCol, rightCol);
  const mseg = mSeg([['blocks', 'блоки'], ['code', 'код'], ['check', 'проверка', errBadgeM], ['vars', 'перем.']], 'blocks', (id) => setMview(id));
  mseg.el.classList.add('m-only', 'lab-mseg');
  function setMview(id) {
    S.mview = id; mseg.set(id);
    layout.classList.toggle('mv-right', id !== 'blocks');
    if (id !== 'blocks') setRtab(id);
  }
  S.mview = 'blocks';
  let palClose = null;
  function openPalette() {
    if (!mIsPhone()) { palSearch.focus(); return; }
    if (S.mview !== 'blocks') setMview('blocks');
    S.search = ''; palSearch.value = '';
    renderPalette();
    const sc = activeScript(), hit = S.sel ? labFindBlock(project, S.sel) : null;
    const where = !sc ? 'сначала добавьте скрипт' : hit ? `после «${(LAB_BLOCKS[hit.block.t] || {}).title || 'блока'}»` : `в конец: ${labScriptSummary(sc) || (LAB_SCRIPT_TYPES[sc.type] || {}).title}`;
    palClose = mSheet('Добавить блок · ' + where, palBox, { keep: true, full: true, onClose: () => { palClose = null; } });
  }
  const fab = mFab('+', openPalette, 'добавить блок');
  fab.classList.add('lab-fab');

  const node = shell(title, { back: '#/webide', backLabel: 'webide', side },
    h('div', { class: 'wi-hint lab-hint m-hide' }, 'Блоки выполняются сверху вниз. Выберите блок на палитре слева (клик — добавить после выбранного, или перетащите). {переменная} подставляет значение, {@текст} — встроенное. Ctrl+Enter — запустить в Sandbox, Ctrl+S — сохранить.'),
    h('div', { class: 'wi-hint lab-hint m-only' }, '«+» — добавить блок после выбранного. «⋯» у блока — переставить, вложить, удалить. Удерживайте блок, чтобы перетащить его пальцем.'),
    mseg.el, layout, dock.el, fab);
  node.classList.add('wide');
  mount(T, node);
  GUARD = () => !S.dirty || confirm('Есть несохранённые изменения проекта. Уйти без сохранения?');

  /* ---------- клавиатура ---------- */
  function onKey(e) {
    if (T !== TOKEN) { document.removeEventListener('keydown', onKey, true); return; }
    if (document.querySelector('.wi-veil')) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key.toLowerCase();
    if (mod && k === 's') { e.preventDefault(); save(); return; }
    if (mod && e.key === 'Enter') { e.preventDefault(); if (!dock.isOpen()) dock.open(); else dock.run(false); return; }
    if (/^(INPUT|TEXTAREA|SELECT)$/.test((document.activeElement || {}).tagName)) return;
    if (mod && !e.shiftKey && k === 'z') { e.preventDefault(); undo(); }
    else if (mod && (k === 'y' || (e.shiftKey && k === 'z'))) { e.preventDefault(); redo(); }
    else if (mod && k === 'd' && S.sel) { e.preventDefault(); duplicateBlock(S.sel); }
    else if (mod && k === 'c' && S.sel) { const hit = labFindBlock(project, S.sel); if (hit) { LAB_CLIP.block = labClone(hit.block); toast('блок скопирован', 'ok'); } }
    else if (mod && k === 'v' && LAB_CLIP.block) {
      e.preventDefault();
      const copy = labReid(labClone(LAB_CLIP.block)), script = activeScript(), hit = S.sel ? labFindBlock(project, S.sel) : null;
      if (!script) return;
      if (hit) hit.list.splice(hit.index + 1, 0, copy); else script.blocks.push(copy);
      S.sel = copy.id; structural();
    }
    else if ((e.key === 'Delete' || e.key === 'Backspace') && S.sel) { e.preventDefault(); removeBlock(S.sel); }
    else if (e.altKey && e.key === 'ArrowUp' && S.sel) { e.preventDefault(); moveSel(-1); }
    else if (e.altKey && e.key === 'ArrowDown' && S.sel) { e.preventDefault(); moveSel(1); }
  }
  document.addEventListener('keydown', onKey, true);

  renderScripts(); renderPalette(); setRtab('code'); recompile(true); updateTools();

  // черновик проекта у юзербота: переживает смену адреса сайта и другое устройство
  loadIcons().then(() => { if (T === TOKEN) recompile(); }).catch(() => {});             // ключи visual для проверки {@visual:…}
  drafts = wiDraftKeeper({ key: 'lab:' + (isNew ? '__new__' : moduleId), base: baseHash, alive: () => T === TOKEN,
    isDirty: () => S.dirty, getData: () => ({ project }), label: 'проект Lab ' + (isNew ? '(новый)' : moduleId), copyText: null });
  const d = await drafts.load();
  if (T !== TOKEN) return;
  const dp = d && d.data && d.data.project && Array.isArray(d.data.project.scripts) ? d.data.project : null;
  if (dp && JSON.stringify(labSanitize(labClone(dp))) !== snapshot() && JSON.stringify(labSanitize(labClone(dp))) !== S.savedJson) {
    drafts.adopt();
    draftSlot.replaceChildren(wiDraftBanner(d, isNew ? 'новый проект Lab' : 'проект этого модуля', () => {
      const p = labSanitize(dp);
      if (!isNew) { p.meta.locked_author = project.meta.locked_author; p.meta.module_name = project.meta.module_name; }
      project = p; S.sel = null; S.active = null; pushHist();
      renderScripts(); renderPalette(); recompile(true); markDirty(); updateTools(); title.textContent = project.meta.name || project.meta.module_name;
      toast('правки восстановлены — не забудьте сохранить', 'ok');
    }, () => { drafts.done(); toast('черновик отброшен', 'ok'); }));
  } else if (d) drafts.done();
}
