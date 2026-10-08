/* FRX WebIDE. Вкладка «webide»: редактор модулей (подсветка Python в палитре Visual Studio Dark+, поиск и замена,
 * история изменений, защита шапки модуля) и «Конструкции» — формы, которые превращаются в код и вставляются в место курсора.
 * Загружается ДО app.js, использует его утилиты (h, api, act, toast, modal, confirmBtn, shell, glass, empty, mount, TOKEN, GUARD…)
 * только во время вызова. Любые данные попадают в DOM через textContent/createElement; innerHTML — только для подсветки кода,
 * где каждый фрагмент предварительно экранируется (wiEsc). */
'use strict';

/* ------------------------------------------------------------------ *
 * подсветка синтаксиса Python (цвета — стандартные Visual Studio Dark+)
 * ------------------------------------------------------------------ */

const WI_CONTROL = new Set(['if', 'elif', 'else', 'for', 'while', 'break', 'continue', 'return', 'yield', 'try', 'except', 'finally', 'raise',
  'with', 'as', 'import', 'from', 'pass', 'assert', 'await', 'async', 'match', 'case', 'del']);
const WI_KEYWORD = new Set(['def', 'class', 'lambda', 'global', 'nonlocal', 'and', 'or', 'not', 'in', 'is', 'True', 'False', 'None', 'self', 'cls']);
const WI_BUILTIN_FN = new Set(['print', 'len', 'range', 'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple', 'min', 'max', 'sum', 'abs', 'round',
  'sorted', 'reversed', 'enumerate', 'zip', 'map', 'filter', 'any', 'all', 'isinstance', 'issubclass', 'getattr', 'setattr', 'hasattr', 'open',
  'type', 'repr', 'input', 'iter', 'next', 'id', 'hash', 'format', 'vars', 'dir', 'callable', 'super', 'chr', 'ord', 'hex', 'bin', 'oct', 'bytes']);
const WI_BUILTIN_TYPE = new Set(['Exception', 'BaseException', 'ValueError', 'KeyError', 'TypeError', 'IndexError', 'RuntimeError', 'AttributeError',
  'OSError', 'ImportError', 'StopIteration', 'TimeoutError', 'ConnectionError', 'NotImplementedError', 'ZeroDivisionError', 'asyncio', 'engine',
  'decorators', 'db', 'cache', 'visual', 'web', 'bot', 'time', 'json', 're', 'os', 'sys']);

const WI_TOKEN = new RegExp([
  '(?<com>#[^\\n]*)',
  '|(?<tstr>(?<![\\w])[rRbBfFuU]{0,2}(?<tq>"""|\'\'\')[\\s\\S]*?(?:\\k<tq>|(?![\\s\\S])))',
  '|(?<str>(?<![\\w])[rRbBfFuU]{0,2}(?<q>["\'])(?:\\\\[\\s\\S]|(?!\\k<q>)[^\\\\\\n])*(?:\\k<q>|$))',
  '|(?<num>(?<![\\w.])(?:0[xX][\\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\\d[\\d_]*(?:\\.[\\d_]*)?(?:[eE][+-]?\\d+)?[jJ]?|\\.\\d[\\d_]*(?:[eE][+-]?\\d+)?[jJ]?))',
  '|(?<deco>^[ \\t]*@[\\w.]+)',
  '|(?<word>[A-Za-z_]\\w*)',
].join(''), 'gm');

const wiEsc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const wiSpan = (cls, text) => '<span class="' + cls + '">' + wiEsc(text) + '</span>';

function wiFString(text) {                                    // f-строка: {выражения} подсвечиваются отдельно
  const m = text.match(/^([rRbBfFuU]{0,2})(["']{1,3})([\s\S]*)$/);
  if (!m || !/[fF]/.test(m[1])) return wiSpan('t-str', text);
  const head = m[1] + m[2];
  let body = m[3], tail = '';
  const q = m[2];
  if (body.endsWith(q) && body.length >= q.length) { tail = q; body = body.slice(0, -q.length); }
  let out = wiSpan('t-str', head), i = 0;
  while (i < body.length) {
    if (body[i] === '{' && body[i + 1] === '{') { out += wiSpan('t-str', '{{'); i += 2; continue; }
    if (body[i] === '{') {
      let depth = 1, j = i + 1;
      while (j < body.length && depth) { if (body[j] === '{') depth++; else if (body[j] === '}') depth--; j++; }
      if (depth === 0) {
        out += wiSpan('t-kw', '{') + wiSpan('t-var', body.slice(i + 1, j - 1)) + wiSpan('t-kw', '}');
        i = j; continue;
      }
    }
    let j = body.indexOf('{', i + 1); if (j < 0) j = body.length;
    out += wiSpan('t-str', body.slice(i, j)); i = j;
  }
  return out + (tail ? wiSpan('t-str', tail) : '');
}

function pyHighlight(src) {
  let out = '', last = 0, importMode = false, prevWord = '', prevPunct = '';
  WI_TOKEN.lastIndex = 0;
  let m;
  while ((m = WI_TOKEN.exec(src)) !== null) {
    if (m[0] === '') { WI_TOKEN.lastIndex++; continue; }
    const gap = src.slice(last, m.index);
    if (gap) {
      out += wiEsc(gap);
      if (gap.includes('\n')) { importMode = false; prevWord = ''; }
      const trimmed = gap.replace(/\s+$/, '');
      prevPunct = trimmed ? trimmed.slice(-1) : (/\S/.test(gap) ? '' : prevPunct);
      if (trimmed) prevWord = '';
    }
    last = m.index + m[0].length;
    const g = m.groups, t = m[0];
    if (g.com !== undefined) { out += wiSpan('t-com', t); continue; }
    if (g.tstr !== undefined || g.str !== undefined) { out += wiFString(t); prevWord = ''; prevPunct = ''; continue; }
    if (g.num !== undefined) { out += wiSpan('t-num', t); prevWord = ''; prevPunct = ''; continue; }
    if (g.deco !== undefined) { out += wiSpan('t-fn', t); importMode = false; prevWord = ''; prevPunct = ''; continue; }
    // слово
    const after = src.slice(last, last + 80).match(/^[ \t]*(.)/);
    const callNext = !!after && after[1] === '(';
    const afterDot = prevPunct === '.';
    let cls = '';
    if (importMode && !WI_CONTROL.has(t)) cls = 't-type';
    else if (WI_CONTROL.has(t) && !afterDot) {
      cls = 't-ctl';
      if (t === 'import' || t === 'from') {
        const lineStart = src.lastIndexOf('\n', m.index - 1) + 1;
        if (!/\S/.test(src.slice(lineStart, m.index)) || t === 'import') importMode = true;
      }
    } else if (WI_KEYWORD.has(t) && !afterDot) cls = 't-kw';
    else if (prevWord === 'def') cls = 't-fn';
    else if (prevWord === 'class') cls = 't-type';
    else if (afterDot) cls = callNext ? 't-fn' : 't-var';
    else if (WI_BUILTIN_FN.has(t) && callNext) cls = 't-fn';
    else if (callNext) cls = /^[A-Z]/.test(t) ? 't-type' : 't-fn';
    else if (WI_BUILTIN_TYPE.has(t)) cls = 't-type';
    else if (/^[A-Z][A-Z0-9_]+$/.test(t)) cls = 't-const';
    else if (/^[A-Z]/.test(t)) cls = 't-type';
    else cls = 't-var';
    out += wiSpan(cls, t);
    prevWord = t; prevPunct = '';
  }
  out += wiEsc(src.slice(last));
  return out;
}

/* ------------------------------------------------------------------ *
 * редактор: textarea + слои (метки поиска, полосы, подсветка) под прозрачным текстом
 * ------------------------------------------------------------------ */

const WI_LINE_H = 20, WI_PAD = 16;
const WI_PROTECTED_RE = /^(NAME|MODULE_NAME|AUTHOR)[ \t]*=.*$/;

function wiProtectedLines(text) {                         // [{label, ls, le}] — строки шапки, которые нельзя менять
  const out = [];
  let pos = 0;
  for (const line of text.split('\n')) {
    const m = WI_PROTECTED_RE.exec(line);
    if (m) out.push({ label: m[1], ls: pos, le: pos + line.length, text: line });
    pos += line.length + 1;
  }
  return out;
}
const wiProtSig = (text) => wiProtectedLines(text).map((p) => p.text).join('\u0001');

function wiLineStart(v, pos) { return v.lastIndexOf('\n', pos - 1) + 1; }
function wiLineEnd(v, pos) { const i = v.indexOf('\n', pos); return i < 0 ? v.length : i; }
function wiLineOf(v, pos) { let n = 0, i = -1; while ((i = v.indexOf('\n', i + 1)) !== -1 && i < pos) n++; return n; }

function wiCreateEditor(opts) {
  opts = opts || {};
  const state = { protect: !!opts.protect, readOnly: !!opts.readOnly, marks: [], cur: -1, errorLine: 0, bypass: false, charW: 7.8 };

  const ta = h('textarea', { class: 'wi-ta', spellcheck: 'false', autocapitalize: 'off', autocomplete: 'off', wrap: 'off', readOnly: state.readOnly,
    'aria-label': 'код модуля' });
  const under = h('pre', { class: 'wi-pre wi-under', 'aria-hidden': 'true' });
  const hl = h('pre', { class: 'wi-pre wi-hl', 'aria-hidden': 'true' });
  const bands = h('div', { class: 'wi-bands', 'aria-hidden': 'true' });
  const scroller = h('div', { class: 'wi-scroller' }, bands, under, hl);
  const view = h('div', { class: 'wi-view' }, scroller);
  const gutterIn = h('div', { class: 'wi-gutter-in' });
  const gutter = h('div', { class: 'wi-gutter' }, gutterIn);
  const findHost = h('div', { class: 'wi-find-host' });
  const foundBtn = h('button', { class: 'wi-found', type: 'button', hidden: true, tabindex: '-1' });
  const el = h('div', { class: 'wi-editor' }, gutter, h('div', { class: 'wi-code' }, view, ta, findHost, foundBtn));

  /* ---------- вспомогательное ---------- */

  const val = () => ta.value;
  const notify = (what) => { if (opts.onChange && what !== 'silent') opts.onChange(ta.value); };

  function measure() {                                    // ширина символа моноширинного шрифта
    const probe = h('span', { class: 'wi-pre', style: 'position:absolute;visibility:hidden;padding:0;white-space:pre' }, '0'.repeat(100));
    el.appendChild(probe);
    const w = probe.getBoundingClientRect().width / 100;
    probe.remove();
    if (w > 3 && w < 20) state.charW = w;
  }

  function rawInsert(text) {
    ta.focus();
    if (!document.execCommand || !document.execCommand('insertText', false, text)) {
      ta.setRangeText(text, ta.selectionStart, ta.selectionEnd, 'end');
      ta.dispatchEvent(new Event('input', { bubbles: true }));
    }
  }

  /* заменить [s, e) текстом и поставить выделение [ss, se] — одной операцией (Ctrl+Z откатывает её целиком) */
  function edit(s, e, text, ss, se) {
    if (state.readOnly) return false;
    ta.focus();
    ta.setSelectionRange(s, e);
    if (text === '' && s === e) { /* нечего вставлять */ } else if (text === '') {
      if (!document.execCommand || !document.execCommand('delete')) { ta.setRangeText('', s, e, 'end'); ta.dispatchEvent(new Event('input', { bubbles: true })); }
    } else rawInsert(text);
    if (ss !== undefined) ta.setSelectionRange(ss, se === undefined ? ss : se);
    return true;
  }

  /* ---------- отрисовка ---------- */

  let lines = 0, raf = 0;
  function renderGutter() {
    const n = ta.value.split('\n').length;
    if (n !== lines) {
      lines = n;
      gutterIn.textContent = Array.from({ length: n }, (_, i) => i + 1).join('\n');
      gutter.style.width = (26 + String(n).length * 9) + 'px';
    }
  }

  function renderMarks() {
    if (!state.marks.length) { under.innerHTML = ''; return; }
    const v = ta.value;
    let out = '', last = 0;
    state.marks.forEach((m, i) => {
      out += wiEsc(v.slice(last, m.a)) + '<mark class="' + (i === state.cur ? 'cur' : '') + '">' + wiEsc(v.slice(m.a, m.b)) + '</mark>';
      last = m.b;
    });
    under.innerHTML = out + wiEsc(v.slice(last)) + '\n';
  }

  function renderBands() {
    const v = ta.value, frag = document.createDocumentFragment();
    const band = (line, cls, h2) => frag.appendChild(h('div', { class: 'wi-band ' + cls, style: `top:${WI_PAD + line * WI_LINE_H}px;height:${(h2 || 1) * WI_LINE_H}px` }));
    if (document.activeElement === ta && ta.selectionStart === ta.selectionEnd) band(wiLineOf(v, ta.selectionStart), 'cur');
    if (state.protect) wiProtectedLines(v).forEach((p) => band(wiLineOf(v, p.ls), 'lock'));
    if (state.found) band(state.found.l0, 'found', state.found.l1 - state.found.l0 + 1);
    if (state.errorLine) band(state.errorLine - 1, 'err');
    bands.replaceChildren(frag);
    placeFound();
    // маркеры в поле нумерации строк
    const marks = [];
    if (state.protect) wiProtectedLines(v).forEach((p) => marks.push([wiLineOf(v, p.ls), 'lock']));
    if (state.errorLine) marks.push([state.errorLine - 1, 'err']);
    gutterIn.querySelectorAll('.wi-gmark').forEach((n) => n.remove());
    marks.forEach(([line, cls]) => gutterIn.appendChild(h('i', { class: 'wi-gmark ' + cls, style: `top:${WI_PAD + line * WI_LINE_H}px` })));
  }

  /* найденная конструкция: подсвеченный блок + кнопка «> править» у его первой строки */
  function placeFound() {
    const f = state.found;
    if (!f) { foundBtn.hidden = true; return; }
    const top = WI_PAD + f.l0 * WI_LINE_H - ta.scrollTop;
    if (top < -WI_LINE_H || top > ta.clientHeight) { foundBtn.hidden = true; return; }
    if (foundBtn.dataset.k !== f.key) {
      foundBtn.dataset.k = f.key;
      foundBtn.textContent = '> править · ' + f.label;
      foundBtn.onmousedown = (e) => e.preventDefault();
      foundBtn.onclick = () => f.onEdit();
      foundBtn.hidden = false;
      foundBtn.classList.remove('in'); void foundBtn.offsetWidth; foundBtn.classList.add('in');
    }
    foundBtn.hidden = false;
    foundBtn.style.top = Math.max(2, top - 1) + 'px';
  }

  function renderAll() {
    hl.innerHTML = pyHighlight(ta.value) + '\n';
    renderMarks(); renderGutter(); renderBands(); syncScroll();
  }
  const schedule = () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; renderAll(); }); };

  function syncScroll() {
    const t = `translate(${-ta.scrollLeft}px, ${-ta.scrollTop}px)`;
    scroller.style.transform = t;
    gutterIn.style.transform = `translateY(${-ta.scrollTop}px)`;
    if (state.found) placeFound();
  }

  function cursorInfo() {
    const v = ta.value, p = ta.selectionStart;
    const line = wiLineOf(v, p) + 1, col = p - wiLineStart(v, p) + 1;
    return { line, col, sel: Math.abs(ta.selectionEnd - ta.selectionStart), lines };
  }
  const emitCursor = () => { if (opts.onCursor) opts.onCursor(cursorInfo()); renderBands(); };

  /* ---------- защита шапки модуля ---------- */

  function guardedRange(inputType, a, b) {                // что именно будет заменено — для типов, где браузер не отдаёт диапазон
    const v = ta.value;
    if (a !== b) return [a, b];
    switch (inputType) {
      case 'deleteContentBackward': return a > 0 ? [a - 1, a] : [a, a];
      case 'deleteContentForward': return a < v.length ? [a, a + 1] : [a, a];
      case 'deleteWordBackward': { let i = a; while (i > 0 && /\s/.test(v[i - 1])) i--; while (i > 0 && /\w/.test(v[i - 1])) i--; if (i === a && i > 0) i--; return [i, a]; }
      case 'deleteWordForward': { let i = a; while (i < v.length && /\s/.test(v[i])) i++; while (i < v.length && /\w/.test(v[i])) i++; if (i === a && i < v.length) i++; return [a, i]; }
      case 'deleteSoftLineBackward': case 'deleteHardLineBackward': return [wiLineStart(v, a), a];
      case 'deleteSoftLineForward': case 'deleteHardLineForward': return [a, wiLineEnd(v, a)];
      default: return [a, b];
    }
  }

  function violatedField(next) {                          // имя защищённого поля, которое изменили, либо ''
    const before = wiProtectedLines(ta.value), after = wiProtectedLines(next);
    const labels = (arr) => arr.map((p) => p.text);
    const A = labels(before), B = labels(after);
    if (A.length === B.length && A.every((t, i) => t === B[i])) return '';
    const changed = before.find((p, i) => !after[i] || after[i].text !== p.text);
    return (changed || after[after.length - 1] || before[0] || { label: 'шапка модуля' }).label;
  }

  function denyProtected(label) { if (opts.onProtected) opts.onProtected(label); }

  ta.addEventListener('beforeinput', (e) => {
    if (!state.protect || state.readOnly || state.bypass) return;
    const type = e.inputType || '';
    if (type.startsWith('history')) return;               // undo/redo проверим после (input)
    let [s, en] = guardedRange(type, ta.selectionStart, ta.selectionEnd);
    let text = '';
    if (type.startsWith('insert')) text = e.data !== null && e.data !== undefined ? e.data : (type === 'insertLineBreak' || type === 'insertParagraph' ? '\n' : '');
    if (type.startsWith('insertFrom') && !text && e.dataTransfer) text = e.dataTransfer.getData('text/plain') || '';
    const v = ta.value;
    const bad = violatedField(v.slice(0, s) + text + v.slice(en));
    if (bad) { e.preventDefault(); denyProtected(bad); }
  });

  let lastGood = ta.value;                                // страховка для действий, диапазон которых мы не знаем (undo, drop…)
  ta.addEventListener('input', () => {
    if (state.protect && !state.readOnly && !state.bypass && wiProtSig(ta.value) !== wiProtSig(lastGood)) {
      const bad = violatedField(lastGood);
      state.bypass = true;
      const ok = document.execCommand && document.execCommand('undo');
      if (!ok || ta.value !== lastGood) { ta.value = lastGood; }
      state.bypass = false;
      denyProtected(bad || 'шапка модуля');
    }
    lastGood = ta.value;
    schedule(); notify(); emitCursor();
    if (finder.open) finder.refresh(false);
  });

  /* ---------- горячие клавиши ---------- */

  const PAIRS = { '(': ')', '[': ']', '{': '}', '"': '"', "'": "'" };
  const CLOSERS = new Set([')', ']', '}']);

  function blockOf() {                                    // строки, затронутые выделением
    const v = ta.value;
    const s = wiLineStart(v, ta.selectionStart);
    let e = ta.selectionEnd;
    if (e > ta.selectionStart && v[e - 1] === '\n') e--;
    return [s, wiLineEnd(v, e)];
  }

  function reindent(dedent) {
    const v = ta.value, [s, e] = blockOf();
    const ss = ta.selectionStart, se = ta.selectionEnd;
    const old = v.slice(s, e).split('\n');
    let first = 0, total = 0;
    const next = old.map((ln, i) => {
      if (!dedent) { if (i === 0) first = 4; total += 4; return '    ' + ln; }
      const m = ln.match(/^( {1,4}|\t)/);
      const cut = m ? m[0].length : 0;
      if (i === 0) first = -cut; total -= cut;
      return ln.slice(cut);
    });
    edit(s, e, next.join('\n'), Math.max(s, ss + first), se + total);
  }

  function toggleComment() {
    const v = ta.value, [s, e] = blockOf();
    const ss = ta.selectionStart, se = ta.selectionEnd;
    const old = v.slice(s, e).split('\n');
    const allCommented = old.filter((l) => l.trim()).every((l) => /^\s*#/.test(l));
    let first = 0, total = 0;
    const next = old.map((ln, i) => {
      if (!ln.trim()) return ln;
      let out;
      if (allCommented) out = ln.replace(/^(\s*)# ?/, '$1');
      else { const ind = ln.match(/^\s*/)[0]; out = ind + '# ' + ln.slice(ind.length); }
      const d = out.length - ln.length;
      if (i === 0) first = d; total += d;
      return out;
    });
    edit(s, e, next.join('\n'), Math.max(s, ss + first), se + total);
  }

  function moveLines(dir) {
    const v = ta.value, [s, e] = blockOf();
    const ss = ta.selectionStart, se = ta.selectionEnd;
    if (dir < 0) {
      if (s === 0) return;
      const ps = wiLineStart(v, s - 1);
      const prev = v.slice(ps, s - 1), blk = v.slice(s, e);
      edit(ps, e, blk + '\n' + prev, ss - (prev.length + 1), se - (prev.length + 1));
    } else {
      if (e >= v.length) return;
      const ne = wiLineEnd(v, e + 1);
      const next = v.slice(e + 1, ne), blk = v.slice(s, e);
      edit(s, ne, next + '\n' + blk, ss + next.length + 1, se + next.length + 1);
    }
  }

  function duplicateLines(dir) {
    const v = ta.value, [s, e] = blockOf();
    const blk = v.slice(s, e);
    const ss = ta.selectionStart, se = ta.selectionEnd;
    edit(e, e, '\n' + blk, dir > 0 ? ss + blk.length + 1 : ss, dir > 0 ? se + blk.length + 1 : se);
  }

  function smartEnter() {
    const v = ta.value, a = ta.selectionStart, b = ta.selectionEnd;
    const ls = wiLineStart(v, a);
    const before = v.slice(ls, a);
    const ind = before.match(/^[ \t]*/)[0];
    const prev = v[a - 1], next = v[b];
    const extra = /:\s*$/.test(before) ? '    ' : '';
    if (prev && next && ('([{'.includes(prev)) && PAIRS[prev] === next) {
      const text = '\n' + ind + '    ' + '\n' + ind;
      edit(a, b, text, a + 1 + ind.length + 4);
      return true;
    }
    if (!ind && !extra) return false;
    edit(a, b, '\n' + ind + extra);
    return true;
  }

  function autoPair(key) {
    const v = ta.value, a = ta.selectionStart, b = ta.selectionEnd, next = v[b], prev = v[a - 1];
    if (a !== b && key in PAIRS) {                        // обернуть выделенное
      const sel = v.slice(a, b);
      edit(a, b, key + sel + PAIRS[key], a + 1, b + 1);
      return true;
    }
    if (CLOSERS.has(key) || (key === '"' || key === "'") && next === key) {
      if (next === key) { ta.setSelectionRange(a + 1, a + 1); emitCursor(); return true; }
      return false;
    }
    if (key in PAIRS) {
      if ((key === '"' || key === "'") && prev && /[\w]/.test(prev)) return false;      // апостроф внутри слова
      if (next && /[\w]/.test(next)) return false;
      edit(a, b, key + PAIRS[key], a + 1);
      return true;
    }
    return false;
  }

  function backspacePair() {
    const v = ta.value, a = ta.selectionStart;
    if (a !== ta.selectionEnd || a === 0) return false;
    const prev = v[a - 1], next = v[a];
    if (prev in PAIRS && PAIRS[prev] === next) { edit(a - 1, a + 1, ''); return true; }
    return false;
  }

  /* Backspace в отступе стирает сразу один уровень (до ближайшей кратной 4 колонки), а не по одному пробелу */
  function backspaceIndent() {
    const v = ta.value, a = ta.selectionStart;
    if (a !== ta.selectionEnd || a === 0) return false;
    const ls = v.lastIndexOf('\n', a - 1) + 1, before = v.slice(ls, a);
    if (!before || /[^ ]/.test(before)) return false;
    const k = before.length % 4 || 4;
    edit(a - k, a, '');
    return true;
  }

  ta.addEventListener('keydown', (e) => {
    if (e.isComposing) return;
    const mod = e.ctrlKey || e.metaKey, k = e.key;
    if (mod && k.toLowerCase() === 's') { e.preventDefault(); if (opts.onSave) opts.onSave(); return; }
    if (mod && k === 'Enter' && opts.onRun) { e.preventDefault(); opts.onRun(); return; }
    if (mod && k.toLowerCase() === 'f') { e.preventDefault(); finder.show(false); return; }
    if (mod && k.toLowerCase() === 'h') { e.preventDefault(); if (!state.readOnly) finder.show(true); return; }
    if (mod && k.toLowerCase() === 'g') { e.preventDefault(); finder.gotoLine(); return; }
    if (k === 'F3') { e.preventDefault(); finder.next(e.shiftKey ? -1 : 1); return; }
    if (state.readOnly) return;
    if (k === 'Tab' && !mod && !e.altKey) {
      e.preventDefault();
      const multi = ta.value.slice(ta.selectionStart, ta.selectionEnd).includes('\n');
      if (multi || e.shiftKey) reindent(e.shiftKey);
      else rawInsert('    ');
      return;
    }
    if (mod && k === '/') { e.preventDefault(); toggleComment(); return; }
    if (e.altKey && (k === 'ArrowUp' || k === 'ArrowDown')) {
      e.preventDefault();
      if (e.shiftKey) duplicateLines(k === 'ArrowDown' ? 1 : -1); else moveLines(k === 'ArrowUp' ? -1 : 1);
      return;
    }
    if (k === 'Enter' && !mod && !e.shiftKey && !e.altKey) { if (smartEnter()) e.preventDefault(); return; }
    if (k === 'Backspace' && !mod && !e.altKey && (backspaceIndent() || backspacePair())) { e.preventDefault(); return; }
    if (!mod && !e.altKey && k.length === 1 && (k in PAIRS || CLOSERS.has(k)) && autoPair(k)) { e.preventDefault(); return; }
  });

  ta.addEventListener('scroll', syncScroll);
  ['keyup', 'mouseup', 'focus', 'blur', 'select'].forEach((n) => ta.addEventListener(n, emitCursor));
  ta.addEventListener('click', emitCursor);

  /* ---------- поиск и замена (Ctrl+F / Ctrl+H, F3, Ctrl+G) ---------- */

  const finder = (() => {
    const f = { open: false, replace: false, caseS: false, regex: false, query: '', error: false };
    const qIn = h('input', { class: 'wi-in', type: 'text', placeholder: 'найти', spellcheck: 'false', 'aria-label': 'найти' });
    const rIn = h('input', { class: 'wi-in', type: 'text', placeholder: 'заменить на', spellcheck: 'false', 'aria-label': 'заменить на' });
    const count = h('span', { class: 'wi-count' }, '');
    const btnCase = h('button', { class: 'wi-tgl', type: 'button', title: 'учитывать регистр', onclick: () => { f.caseS = !f.caseS; btnCase.classList.toggle('on', f.caseS); f.refresh(true); qIn.focus(); } }, 'Aa');
    const btnRe = h('button', { class: 'wi-tgl', type: 'button', title: 'регулярное выражение', onclick: () => { f.regex = !f.regex; btnRe.classList.toggle('on', f.regex); f.refresh(true); qIn.focus(); } }, '.*');
    const btnPrev = h('button', { class: 'wi-ico', type: 'button', title: 'предыдущее (Shift+Enter)', onclick: () => f.next(-1) }, '↑');
    const btnNext = h('button', { class: 'wi-ico', type: 'button', title: 'следующее (Enter)', onclick: () => f.next(1) }, '↓');
    const btnClose = h('button', { class: 'wi-ico', type: 'button', title: 'закрыть (Esc)', onclick: () => f.hide() }, '×');
    const btnRep = h('button', { class: 'wi-txt', type: 'button', onclick: () => f.replaceOne() }, 'заменить');
    const btnAll = h('button', { class: 'wi-txt', type: 'button', onclick: () => f.replaceAll() }, 'все');
    const rowRep = h('div', { class: 'wi-row' }, rIn, btnRep, btnAll);
    const bar = h('div', { class: 'wi-find', hidden: true }, h('div', { class: 'wi-row' }, qIn, btnCase, btnRe, count, btnPrev, btnNext, btnClose), rowRep);
    findHost.appendChild(bar);

    function build() {
      if (!f.query) return null;
      try {
        const src = f.regex ? f.query : f.query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        return new RegExp(src, 'g' + (f.caseS ? '' : 'i') + 'm');
      } catch (e) { return false; }
    }

    function compute() {
      const re = build();
      f.error = re === false;
      qIn.classList.toggle('bad', f.error);
      const out = [];
      if (re) {
        const v = ta.value;
        let m;
        while ((m = re.exec(v)) !== null && out.length < 5000) {
          if (m[0] === '') { re.lastIndex++; continue; }
          out.push({ a: m.index, b: m.index + m[0].length });
        }
      }
      return out;
    }

    f.refresh = (jump) => {
      state.marks = compute();
      if (jump && state.marks.length) {
        const p = ta.selectionStart;
        const i = state.marks.findIndex((m) => m.a >= p);
        state.cur = i < 0 ? 0 : i;
        f.reveal();
      } else if (state.cur >= state.marks.length) state.cur = state.marks.length - 1;
      if (!state.marks.length) state.cur = -1;
      count.textContent = !f.query ? '' : f.error ? 'ошибка' : state.marks.length ? `${state.cur + 1} из ${state.marks.length}` : 'нет';
      renderMarks();
    };

    f.reveal = () => {
      const m = state.marks[state.cur];
      if (!m) return;
      const v = ta.value, line = wiLineOf(v, m.a), col = m.a - wiLineStart(v, m.a);
      const top = WI_PAD + line * WI_LINE_H, vh = ta.clientHeight;
      if (top < ta.scrollTop + 10 || top > ta.scrollTop + vh - 50) ta.scrollTop = Math.max(0, top - vh / 2);
      const x = WI_PAD + col * state.charW, vw = ta.clientWidth;
      if (x < ta.scrollLeft + 10 || x > ta.scrollLeft + vw - 140) ta.scrollLeft = Math.max(0, x - vw / 3);
      syncScroll();
    };

    f.next = (dir) => {
      if (!f.open) { f.show(false); return; }
      if (!state.marks.length) { f.refresh(true); return; }
      state.cur = (state.cur + dir + state.marks.length) % state.marks.length;
      f.refresh(false); f.reveal();
      const m = state.marks[state.cur];
      ta.setSelectionRange(m.a, m.b);
    };

    f.show = (withReplace) => {
      f.mode = null; qIn.placeholder = 'найти';
      f.open = true; f.replace = !!withReplace && !state.readOnly;
      bar.hidden = false; rowRep.hidden = !f.replace;
      const sel = ta.value.slice(ta.selectionStart, ta.selectionEnd);
      if (sel && !sel.includes('\n')) { qIn.value = sel; f.query = sel; }
      qIn.focus(); qIn.select();
      f.refresh(true);
    };

    f.hide = () => {
      f.mode = null; qIn.placeholder = 'найти';
      f.open = false; bar.hidden = true; state.marks = []; state.cur = -1; renderMarks();
      ta.focus();
    };

    f.replaceOne = () => {
      const m = state.marks[state.cur];
      if (!m) return;
      const re = build();
      const piece = ta.value.slice(m.a, m.b);
      const rep = f.regex ? piece.replace(new RegExp(re.source, re.flags.replace('g', '')), rIn.value) : rIn.value;
      edit(m.a, m.b, rep, m.a + rep.length);
      f.refresh(true);
    };

    f.replaceAll = () => {
      const re = build();
      if (!re || !state.marks.length) return;
      const v = ta.value;
      const next = f.regex ? v.replace(re, rIn.value) : v.replace(re, () => rIn.value);
      const n = state.marks.length;
      if (edit(0, v.length, next, 0)) { f.refresh(false); if (opts.onInfo) opts.onInfo(`заменено: ${n}`); }
    };

    f.gotoLine = () => {
      f.open = true; bar.hidden = false; rowRep.hidden = true;
      qIn.value = ''; f.query = '';
      qIn.placeholder = 'перейти к строке…'; f.mode = 'line';
      qIn.focus();
    };

    qIn.addEventListener('input', () => { if (f.mode === 'line') return; f.query = qIn.value; f.refresh(true); });
    const keys = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); f.hide(); }
      else if (e.key === 'Enter') {
        e.preventDefault();
        if (e.target === qIn && f.mode === 'line') { const n = parseInt(qIn.value, 10); f.hide(); if (n > 0) editorApi.gotoLine(n); return; }
        if (e.target === rIn) f.replaceOne(); else f.next(e.shiftKey ? -1 : 1);
      } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'h') { e.preventDefault(); if (!state.readOnly) { f.replace = true; rowRep.hidden = false; rIn.focus(); } }
      else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'f') { e.preventDefault(); qIn.select(); }
    };
    qIn.addEventListener('keydown', keys); rIn.addEventListener('keydown', keys);
    return f;
  })();

  /* ---------- публичный интерфейс ---------- */

  const editorApi = {
    el, ta,
    get value() { return ta.value; },
    setValue(v, keepUndo) {                               // новая версия целиком (история, «сбросить»)
      state.bypass = true;
      if (keepUndo && !state.readOnly) edit(0, ta.value.length, v, 0); else ta.value = v;
      state.bypass = false;
      lastGood = ta.value; renderAll(); emitCursor();
      if (finder.open) finder.refresh(false);
    },
    setBaseline(v) { lastGood = v; },
    setReadOnly(on) { state.readOnly = on; ta.readOnly = on; el.classList.toggle('ro', on); },
    setProtect(on) { state.protect = on; renderBands(); },
    setErrorLine(n) { state.errorLine = n || 0; renderBands(); },
    setFound(f) {                                         // {l0, l1, label, key, onEdit} | null
      const same = (!f && !state.found) || (f && state.found && f.key === state.found.key && f.l0 === state.found.l0 && f.l1 === state.found.l1);
      if (same) { if (f) state.found.onEdit = f.onEdit; return; }
      if (!f) foundBtn.dataset.k = '';
      state.found = f || null; renderBands();
    },
    focus() { ta.focus(); },
    edit,
    selection() { return [ta.selectionStart, ta.selectionEnd]; },
    gotoLine(line) {
      const v = ta.value, ls = v.split('\n');
      let pos = 0;
      for (let i = 0; i < Math.min(line - 1, ls.length - 1); i++) pos += ls[i].length + 1;
      ta.focus(); ta.setSelectionRange(pos, pos + (ls[Math.min(line, ls.length) - 1] || '').length);
      const top = WI_PAD + (Math.min(line, ls.length) - 1) * WI_LINE_H;
      ta.scrollTop = Math.max(0, top - ta.clientHeight / 2); syncScroll(); emitCursor();
    },
    openFind(withReplace) { finder.show(withReplace); },
    refresh: renderAll,
    cursor: cursorInfo,
    key(k) {                                              // полоска символов на телефоне: то же, что соответствующая клавиша
      if (k === 'undo' || k === 'redo') { ta.focus(); if (document.execCommand) document.execCommand(k); return; }
      if (k === 'left' || k === 'right') {
        const p = Math.max(0, Math.min(ta.value.length, (k === 'left' ? ta.selectionStart - 1 : ta.selectionEnd + 1)));
        ta.focus(); ta.setSelectionRange(p, p); emitCursor(); return;
      }
      if (state.readOnly) return;
      if (k === 'tab' || k === 'untab') {
        const multi = ta.value.slice(ta.selectionStart, ta.selectionEnd).includes('\n');
        if (multi || k === 'untab') reindent(k === 'untab'); else rawInsert('    ');
        return;
      }
      if (k === 'comment') { toggleComment(); return; }
      if (!autoPair(k)) rawInsert(k);
    },
  };

  ta.value = opts.value || '';
  lastGood = ta.value;
  el.classList.toggle('ro', state.readOnly);
  requestAnimationFrame(() => { measure(); renderAll(); });
  renderAll();
  return editorApi;
}

/* ------------------------------------------------------------------ *
 * Конструкции: форма → код → вставка в место курсора
 * ------------------------------------------------------------------ */

const wiPyStr = (s) => JSON.stringify(String(s));
function wiPyLit(v) {
  if (v === null || v === undefined) return 'None';
  if (typeof v === 'boolean') return v ? 'True' : 'False';
  if (typeof v === 'number') return Number.isFinite(v) ? String(v) : 'None';
  if (Array.isArray(v)) return '[' + v.map(wiPyLit).join(', ') + ']';
  return wiPyStr(v);
}
const wiSlug = (s, fallback) => {
  const t = String(s || '').toLowerCase().replace(/[^a-z0-9_]+/g, '_').replace(/^_+|_+$/g, '');
  return t && !/^\d/.test(t) ? t : fallback;
};
const wiIndent = (code, ind) => code.split('\n').map((l) => (l ? ind + l : l)).join('\n');
const wiNames = (s) => String(s || '').split(/[\s,]+/).map((x) => x.trim().toLowerCase()).filter(Boolean);

const WI_VISUAL_DESC = {
  status_on: 'включено', status_off: 'выключено', status_error: 'ошибка', status_success: 'успех', status_warning: 'предупреждение',
  status_start: 'старт', status_stop: 'остановлено', status_wait: 'ожидание', status_process: 'в процессе', name: 'название', description: 'описание',
  version: 'версия', author: 'автор', list: 'список', element: 'элемент списка', info: 'информация', question: 'вопрос', config: 'настройки',
  module: 'модуль', file: 'файл', link: 'ссылка', time: 'время', date: 'дата', chat: 'чат', user: 'пользователь', group: 'группа', channel: 'канал',
  photo: 'фото', video: 'видео', audio: 'аудио', code: 'код', result: 'результат', security: 'конфиденциально', system: 'система', web: 'сайт',
};

/* значения из шапки открытого файла */
function wiReadHeader(source) {
  const out = {};
  const re = /^(NAME|MODULE_NAME|VERSION|AUTHOR|DESCRIPTION)[ \t]*=[ \t]*(?:"((?:[^"\\\n]|\\.)*)"|'((?:[^'\\\n]|\\.)*)')[ \t]*(?:#.*)?$/gm;
  let m;
  while ((m = re.exec(source)) !== null) {
    let v = m[2] !== undefined ? m[2] : m[3];
    try { v = JSON.parse('"' + (m[2] !== undefined ? m[2] : m[3].replace(/"/g, '\\"')) + '"'); } catch (e) { /* оставляем как есть */ }
    out[m[1]] = v;
  }
  return Object.keys(out).length ? out : null;
}

/* ---------- вставка ---------- */

function wiEnsureImports(ed, needs) {                      // добавляет недостающие `import x`; возвращает, на сколько сдвинулся текст
  const v = ed.value;
  const missing = (needs || []).filter((n, i, a) => a.indexOf(n) === i &&
    !new RegExp('^\\s*(import\\s+' + n + '\\b|from\\s+' + n + '\\s+import)', 'm').test(v));
  if (!missing.length) return { at: 0, shift: 0 };
  const lines = v.split('\n');
  let idx = -1;
  lines.forEach((l, i) => { if (i < 80 && /^(import\s|from\s+\S+\s+import\s)/.test(l)) idx = i; });
  let skip = 0;
  if (idx < 0) {
    while (skip < lines.length && /^(#!|#.*coding|# \[FRX WebIDE\])/.test(lines[skip])) skip++;
    if (/^\s*("""|''')/.test(lines[skip] || '')) {
      const q = lines[skip].trim().slice(0, 3);
      const same = lines[skip].trim().length > 3 && lines[skip].trim().endsWith(q);
      let end = same ? skip : lines.findIndex((l, i) => i > skip && l.includes(q));
      if (end < 0) end = skip;
      skip = end + 1;
    }
    idx = skip - 1;
  }
  const at = lines.slice(0, idx + 1).join('\n').length + (idx >= 0 ? 1 : 0);
  let text = missing.map((n) => 'import ' + n).join('\n') + '\n';
  const nextLine = lines[idx + 1];
  if (!/^(import\s|from\s)/.test(nextLine || '') && (nextLine || '').trim()) text += '\n';
  const caret = ed.selection()[0];
  ed.edit(at, at, text, caret >= at ? caret + text.length : caret, caret >= at ? ed.selection()[1] + text.length : ed.selection()[1]);
  return { at, shift: caret >= at ? text.length : 0 };
}

function wiPlace(ed, res) {                                // -> {line, note}
  wiEnsureImports(ed, res.needs);
  const v = ed.value;
  let [a, b] = ed.selection();
  const kind = res.kind;
  let note = '';
  if (kind === 'expr') {
    ed.edit(a, b, res.code, a + res.code.length);
    return { note };
  }
  const ls = wiLineStart(v, a), le = wiLineEnd(v, a);
  const lineText = v.slice(ls, le);
  const blank = !lineText.trim();
  if (kind === 'block') {
    const ind = lineText.match(/^[ \t]*/)[0];
    const base = /:\s*(#.*)?$/.test(lineText) ? ind + '    ' : ind;
    if (!base && !blank) note = 'курсор стоит вне функции — вставьте блок внутрь обработчика команды';
    const body = wiIndent(res.code, blank ? (ind || '') : base);
    if (blank) { ed.edit(ls, le, body, ls, ls + body.length); return { note }; }
    ed.edit(le, le, '\n' + body, le + 1, le + 1 + body.length);
    return { note };
  }
  // top: на верхний уровень, с пустыми строками вокруг
  let at;
  if (blank && !lineText.length) at = ls;
  else if (/^\S/.test(lineText) || !lineText.trim()) at = le < v.length ? le + 1 : le;
  else {                                                   // курсор внутри блока — после его конца
    note = 'курсор был внутри блока — вставлено после него';
    let p = le + 1;
    while (p < v.length) {
      const e2 = wiLineEnd(v, p), l = v.slice(p, e2);
      if (l.trim() && !/^\s/.test(l) && !/^#/.test(l)) break;
      p = e2 + 1;
    }
    at = Math.min(p, v.length);
  }
  const before = v.slice(0, at), after = v.slice(at);
  const nlBefore = (before.match(/\n*$/) || [''])[0].length, nlAfter = (after.match(/^\n*/) || [''])[0].length;
  const needBefore = before.trim() ? Math.max(0, 3 - nlBefore) : 0;
  const needAfter = after.trim() ? Math.max(0, 3 - nlAfter) : 0;
  const text = '\n'.repeat(before && !before.endsWith('\n') ? Math.max(needBefore, 1) : needBefore) + res.code + '\n'.repeat(after.trim() ? Math.max(needAfter, 1) : 1);
  ed.edit(at, at, text, at, at + text.length);
  return { note };
}

/* ---------- разбор Python-кода: литералы, вызовы, декораторы (для «править» и обратной синхронизации превью) ---------- */

function wiMatchParen(src, open) {                         // индекс закрывающей скобки или -1
  const pairs = { '(': ')', '[': ']', '{': '}' };
  const stack = [];
  for (let i = open; i < src.length; i++) {
    const c = src[i];
    if (c === '#') { const e = src.indexOf('\n', i); if (e < 0) return -1; i = e; continue; }
    if (c === '"' || c === "'") {
      const q = src.startsWith(c.repeat(3), i) ? c.repeat(3) : c;
      let j = i + q.length;
      while (j < src.length && !src.startsWith(q, j)) { if (src[j] === '\\') j++; else if (q.length === 1 && src[j] === '\n') break; j++; }
      i = j + q.length - 1; continue;
    }
    if (pairs[c]) stack.push(pairs[c]);
    else if (c === ')' || c === ']' || c === '}') { if (stack.pop() !== c) return -1; if (!stack.length) return i; }
  }
  return -1;
}

function wiSplitTop(text) {                                // «сырые» аргументы по запятым верхнего уровня
  const out = []; let depth = 0, from = 0;
  for (let i = 0; i < text.length; i++) {
    const c = text[i];
    if (c === '"' || c === "'") {
      const q = text.startsWith(c.repeat(3), i) ? c.repeat(3) : c;
      let j = i + q.length;
      while (j < text.length && !text.startsWith(q, j)) { if (text[j] === '\\') j++; j++; }
      i = j + q.length - 1; continue;
    }
    if ('([{'.includes(c)) depth++;
    else if (')]}'.includes(c)) depth--;
    else if (c === ',' && depth === 0) { out.push(text.slice(from, i)); from = i + 1; }
  }
  if (text.slice(from).trim()) out.push(text.slice(from));
  return out.map((x) => x.trim());
}

/* Мини-разбор литералов Python. Строка → string; f-строка → {fstr, expr}; None → null; список/кортеж → массив;
   словарь → {dict}; вызов → {call, args, kw}; имя/атрибут → {expr}. Всё остальное — исключение. */
function wiPyParse(text, mode) {
  const s = text; let i = 0;
  const ws = () => { for (;;) { while (i < s.length && /\s/.test(s[i])) i++; if (s[i] === '#') { while (i < s.length && s[i] !== '\n') i++; } else break; } };
  const ESC = { n: '\n', t: '\t', r: '\r', '\\': '\\', '"': '"', "'": "'", 0: '\0' };
  const strStart = () => /^[rRbBfFuU]{0,2}["']/.test(s.slice(i, i + 3));
  function str() {
    const m = /^([rRbBfFuU]{0,2})("""|'''|"|')/.exec(s.slice(i));
    if (!m) throw new Error('str');
    const pre = m[1].toLowerCase(), q = m[2], raw = pre.includes('r');
    let j = i + m[0].length, out = '';
    while (j < s.length && !s.startsWith(q, j)) {
      if (s[j] === '\\') {
        const n = s[j + 1];
        if (raw) { out += '\\' + (n || ''); j += 2; continue; }
        if (n === 'u') { out += String.fromCharCode(parseInt(s.slice(j + 2, j + 6), 16)); j += 6; continue; }
        if (n === '\n') { j += 2; continue; }
        out += n in ESC ? ESC[n] : '\\' + n; j += 2; continue;
      }
      if (q.length === 1 && s[j] === '\n') throw new Error('str');
      out += s[j++];
    }
    if (j >= s.length) throw new Error('str');
    const rawSrc = s.slice(i, j + q.length);
    i = j + q.length;
    return { v: out, f: pre.includes('f'), raw: rawSrc };
  }
  function args(close) {                                  // после «(» до «)»
    const pos = [], kw = {};
    for (;;) {
      ws();
      if (s[i] === close) { i++; break; }
      const km = /^([A-Za-z_]\w*)\s*=(?!=)/.exec(s.slice(i));
      if (km) { i += km[0].length; kw[km[1]] = val(); } else pos.push(val());
      ws();
      if (s[i] === ',') i++; else if (s[i] !== close) throw new Error('args');
    }
    return { pos, kw };
  }
  function val() {
    ws();
    const c = s[i];
    if (c === undefined) throw new Error('eof');
    if (strStart()) {
      const parts = [str()];
      for (;;) { const save = i; ws(); if (strStart()) parts.push(str()); else { i = save; break; } }
      if (parts.length === 1 && !parts[0].f) return parts[0].v;
      return { fstr: parts.map((p) => p.v).join(''), expr: parts.map((p) => p.raw).join(' '), isF: parts.some((p) => p.f) };
    }
    if (c === '[' || c === '(') {
      const close = c === '[' ? ']' : ')'; i++;
      const arr = []; let comma = false;
      for (;;) { ws(); if (s[i] === close) { i++; break; } arr.push(val()); ws(); if (s[i] === ',') { i++; comma = true; } else if (s[i] !== close) throw new Error('list'); }
      return c === '(' && arr.length === 1 && !comma ? arr[0] : arr;
    }
    if (c === '{') {
      i++; const d = {};
      for (;;) { ws(); if (s[i] === '}') { i++; break; } const k = val(); ws(); if (s[i] !== ':' || typeof k !== 'string') throw new Error('dict'); i++; d[k] = val(); ws(); if (s[i] === ',') i++; else if (s[i] !== '}') throw new Error('dict'); }
      return { dict: d };
    }
    let m = /^-?\d[\d_]*(?:\.\d*)?(?:[eE][+-]?\d+)?/.exec(s.slice(i));
    if (m) { i += m[0].length; return Number(m[0].replace(/_/g, '')); }
    m = /^[A-Za-z_][\w.]*/.exec(s.slice(i));
    if (m) {
      i += m[0].length;
      if (m[0] === 'True') return true;
      if (m[0] === 'False') return false;
      if (m[0] === 'None') return null;
      if (s[i] === '(') { i++; const a = args(')'); return { call: m[0], args: a.pos, kw: a.kw }; }
      return { expr: m[0] };
    }
    throw new Error('val');
  }
  let out;
  if (mode === 'args') out = args(undefined); else out = val();
  ws();
  if (i < s.length) throw new Error('tail');
  return out;
}
const wiPyArgs = (text) => wiPyParse(text, 'args');
const wiTry = (fn) => { try { return fn(); } catch (e) { return null; } };
const wiIsStr = (v) => typeof v === 'string';
const wiStrOf = (v) => (wiIsStr(v) ? v : null);

/* строки файла со смещениями */
function wiLines(v) {
  const out = []; let p = 0;
  v.split('\n').forEach((t) => { out.push({ s: p, e: p + t.length, t }); p += t.length + 1; });
  return out;
}
const wiIndentOf = (t) => (t.match(/^[ \t]*/) || [''])[0];

/* «шапка» функции с декоратором на любом уровне вложенности: @ns.type(args) / def name(sig): / однострочный докстринг */
function wiHeads(src) {
  const L = wiLines(src), heads = [];
  for (let k = 0; k < L.length; k++) {
    const dm = /^([ \t]*)@(\w+)\.(\w+)(?:\((.*)\))?\s*$/.exec(L[k].t);
    if (!dm) continue;
    let d = k + 1;
    while (d < L.length && /^\s*@/.test(L[d].t)) d++;
    const fm = d < L.length && /^([ \t]*)(async\s+)?def\s+(\w+)\s*\(([^)]*)\)\s*(?:->[^:]*)?:\s*(?:#.*)?$/.exec(L[d].t);
    if (!fm || fm[1] !== dm[1]) continue;
    let last = d, doc = null;
    const nm = L[d + 1] && /^[ \t]+(?:"""(.*?)"""|'''(.*?)''')\s*$/.exec(L[d + 1].t);
    if (nm) { doc = nm[1] !== undefined ? nm[1] : nm[2]; last = d + 1; }
    let end = last;
    for (let q = last + 1; q < L.length; q++) {
      const t = L[q].t;
      if (!t.trim()) continue;
      if (wiIndentOf(t).length <= dm[1].length && !/^[ \t]*[)\]}]/.test(t)) break;
      end = q;
    }
    heads.push({ indent: dm[1], ns: dm[2], dtype: dm[3], args: dm[4] === undefined ? '' : dm[4], async: !!fm[2], fname: fm[3], sig: fm[4], doc,
      a: L[k].s, b: L[last].e, blockEnd: L[end].e, l0: k, l1: end, decLine: k });
  }
  return heads;
}

/* занятые имена функций и команд (без исключённого диапазона — правка существующего блока) */
function wiTaken(src, excl) {
  const v = excl ? src.slice(0, excl[0]) + src.slice(excl[1]) : src;
  const fns = new Set(), cmds = { user: new Set(), bot: new Set() };
  for (const m of v.matchAll(/^[ \t]*(?:async\s+)?def\s+(\w+)/gm)) fns.add(m[1]);
  for (const m of v.matchAll(/^[ \t]*@(decorators|bot)\.command\(([^\n]*)\)\s*$/gm)) {
    const a = wiTry(() => wiPyArgs(m[2]));
    if (a) a.pos.filter(wiIsStr).forEach((n) => cmds[m[1] === 'bot' ? 'bot' : 'user'].add(n.toLowerCase()));
  }
  return { fns, cmds };
}
const wiFreeName = (base, taken) => { let n = base, i = 2; while (taken.has(n)) n = base + '_' + i++; return n; };
const wiDoc = (t) => { let d = String(t).replace(/\\/g, '\\\\').replace(/"""/g, '\\"\\"\\"'); if (d.endsWith('"')) d = d.slice(0, -1) + '\\"'; return d; };

/* inline-формы, созданные в коде: переменная, текст, кнопки */
function wiParseButtons(v) {
  if (v === undefined || v === null) return [];
  if (!Array.isArray(v)) throw new Error('buttons');
  return v.map((row) => {
    if (!Array.isArray(row)) throw new Error('row');
    return row.map((b) => {
      if (Array.isArray(b) && b.length >= 2 && wiIsStr(b[0]) && wiIsStr(b[1])) return { label: b[0], kind: 'click', data: b[1], dataAuto: false, hint: '' };
      if (b && b.call && /InputButton$/.test(b.call)) {
        const lab = wiStrOf(b.args[0] !== undefined ? b.args[0] : b.kw.label), dat = wiStrOf(b.args[1] !== undefined ? b.args[1] : b.kw.data);
        if (lab === null || dat === null) throw new Error('input');
        return { label: lab, kind: 'input', data: dat, dataAuto: false, hint: wiStrOf(b.kw.hint) || '' };
      }
      throw new Error('btn');
    });
  });
}
function wiScanForms(src) {
  const out = [];
  for (const m of src.matchAll(/^([ \t]*)(?:(\w+)\s*=\s*)?await\s+engine\.create_inlineform\(/gm)) {
    const open = m.index + m[0].length - 1, close = wiMatchParen(src, open);
    if (close < 0) continue;
    const a = wiTry(() => wiPyArgs(src.slice(open + 1, close)));
    if (!a) continue;
    const text = wiStrOf(a.kw.text !== undefined ? a.kw.text : a.pos[1]);
    const rows = wiTry(() => wiParseButtons(a.kw.buttons !== undefined ? a.kw.buttons : a.pos[2]));
    if (text === null || !rows) continue;
    let forUser = 'me', userId = '';
    const fu = a.kw.for_user;
    if (fu && fu.expr === 'event.chat_id') forUser = 'chat'; else if (typeof fu === 'number') { forUser = 'id'; userId = fu; }
    const to = a.kw.timeout;
    out.push({ a: m.index, b: close + 1, indent: m[1], var: m[2] || 'form', text, rows, forUser, userId, timeout: typeof to === 'number' ? to : '' });
  }
  return out;
}

/* ---------- мини-конструктор полей (единый стиль списков, анимации нажатий) ---------- */

function wiFld(label, control, hint) {
  const cc = control && control.classList;                 // кнопочные группы — не <label>: иначе клик по подписи нажимает первую кнопку
  return h(cc && (cc.contains('wi-seg') || cc.contains('wi-drop')) ? 'div' : 'label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, label), control, hint ? h('span', { class: 'wi-hint' }, hint) : null);
}
function wiText(st, key, ui, o) {
  o = o || {};
  const inp = h(o.area ? 'textarea' : 'input', { class: 'wi-field', type: o.area ? null : (o.type || 'text'), rows: o.area ? (o.rows || 3) : null,
    placeholder: o.placeholder || '', spellcheck: 'false', disabled: !!o.disabled });
  inp.value = st[key] === undefined || st[key] === null ? '' : st[key];
  inp.addEventListener('input', () => { st[key] = o.type === 'number' ? (inp.value === '' ? '' : Number(inp.value)) : inp.value; if (o.rebuild) ui.rebuild(); else ui.changed(); });
  return inp;
}

/* Выпадающий список в стиле окна (вместо белого системного): стеклянное меню с анимацией раскрытия.
   wiMenu вешает меню на кнопку: getItems() -> [[value, label]], getCur() -> value, pick(value). */
function wiMenu(btn, getItems, getCur, pick) {
  let pop = null;
  const close = () => {
    if (!pop) return;
    const p = pop; pop = null; btn.setAttribute('aria-expanded', 'false'); btn.classList.remove('open');
    window.removeEventListener('keydown', onKey, true); document.removeEventListener('mousedown', onDown, true);
    p.classList.add('out'); setTimeout(() => p.remove(), 160);
  };
  const onKey = (e) => {
    if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); close(); btn.focus(); return; }
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault(); e.stopPropagation();
      const items = [...pop.querySelectorAll('button')], at = items.indexOf(document.activeElement);
      items[(at + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length].focus();
    }
  };
  const onDown = (e) => { if (pop && !pop.contains(e.target) && !btn.contains(e.target)) close(); };
  btn.addEventListener('click', () => {
    if (pop) { close(); return; }
    const items = getItems(), cur = getCur();
    if (!items.length) return;
    const r = btn.getBoundingClientRect();
    pop = h('div', { class: 'glass wi-pop', role: 'listbox' }, items.map(([v, l]) =>
      h('button', { type: 'button', role: 'option', class: 'wi-pop-i' + (v === cur ? ' on' : ''), onclick: () => { close(); pick(v); } }, l)));
    const below = window.innerHeight - r.bottom, up = below < 240 && r.top > below;
    pop.style.cssText = `left:${r.left}px;min-width:${r.width}px;` + (up ? `bottom:${window.innerHeight - r.top + 4}px;transform-origin:50% 100%` : `top:${r.bottom + 4}px;transform-origin:50% 0`);
    pop.style.maxHeight = Math.max(140, (up ? r.top : below) - 16) + 'px';
    (btn.closest('.veil') || document.body).appendChild(pop);
    btn.setAttribute('aria-expanded', 'true'); btn.classList.add('open');
    window.addEventListener('keydown', onKey, true); document.addEventListener('mousedown', onDown, true);
    const on = pop.querySelector('.on'); if (on) on.scrollIntoView({ block: 'nearest' });
  });
  return close;
}

function wiDrop(st, key, options, ui, rebuild) {            // options: [[value, label]]
  const label = () => (options.find(([v]) => v === st[key]) || options[0] || ['', ''])[1];
  const btn = h('button', { type: 'button', class: 'wi-field wi-drop', 'aria-haspopup': 'listbox', 'aria-expanded': 'false' },
    h('span', { class: 'wi-drop-t' }, label()), h('i', { class: 'wi-drop-c' }));
  wiMenu(btn, () => options, () => st[key], (v) => {
    if (st[key] === v) return;
    st[key] = v; btn.querySelector('.wi-drop-t').textContent = label();
    if (rebuild) setTimeout(() => ui.rebuild(), 120); else ui.changed();
  });
  return btn;
}

/* Все <select> на сайте превращаются в такое же меню (сам select остаётся скрытым и хранит значение — остальной код ничего не замечает) */
function wiEnhanceSelect(sel) {
  if (sel._wiEnh || sel.multiple || sel.size > 1 || typeof h !== 'function') return;
  sel._wiEnh = true;
  const t = h('span', { class: 'wi-drop-t' });
  const btn = h('button', { type: 'button', class: (sel.className || 'wi-field') + ' wi-drop', 'aria-haspopup': 'listbox', 'aria-expanded': 'false' }, t, h('i', { class: 'wi-drop-c' }));
  const al = sel.getAttribute('aria-label'); if (al) btn.setAttribute('aria-label', al);
  const paint = () => { const o = sel.options[sel.selectedIndex]; t.textContent = o ? o.textContent : ''; btn.disabled = sel.disabled; };
  const vd = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value'), id = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'selectedIndex');
  Object.defineProperty(sel, 'value', { get() { return vd.get.call(this); }, set(v) { vd.set.call(this, v); paint(); }, configurable: true });
  Object.defineProperty(sel, 'selectedIndex', { get() { return id.get.call(this); }, set(v) { id.set.call(this, v); paint(); }, configurable: true });
  wiMenu(btn, () => [...sel.options].filter((o) => !o.disabled && !o.hidden).map((o) => [o.value, o.textContent]), () => sel.value, (v) => {
    if (sel.value === v) return;
    sel.value = v;
    sel.dispatchEvent(new Event('input', { bubbles: true })); sel.dispatchEvent(new Event('change', { bubbles: true }));
  });
  sel.addEventListener('change', paint);
  new MutationObserver(paint).observe(sel, { childList: true, subtree: true, attributes: true, attributeFilter: ['disabled'] });
  sel.style.display = 'none';
  sel.after(btn);
  paint();
}
(function wiWatchSelects() {
  const scan = (root) => { if (root.nodeType !== 1) return; if (root.tagName === 'SELECT') wiEnhanceSelect(root); else root.querySelectorAll && root.querySelectorAll('select').forEach(wiEnhanceSelect); };
  const start = () => {
    document.querySelectorAll('select').forEach(wiEnhanceSelect);
    new MutationObserver((ms) => ms.forEach((m) => m.addedNodes.forEach(scan))).observe(document.body, { childList: true, subtree: true });
  };
  if (document.body) start(); else document.addEventListener('DOMContentLoaded', start);
})();
const wiSelect = wiDrop;

function wiCheck(st, key, label, ui, rebuild) {
  const cb = h('input', { type: 'checkbox', class: 'wi-cb' });
  cb.checked = !!st[key];
  cb.addEventListener('change', () => { st[key] = cb.checked; if (rebuild) ui.rebuild(); else ui.changed(); });
  return h('label', { class: 'wi-check' }, cb, h('span', {}, label));
}
function wiSeg(st, key, options, ui, rebuild) {            // переключатель-«таблетки»: нажатие видно сразу, форма перестраивается чуть позже
  const wrap = h('div', { class: 'wi-seg' });
  options.forEach(([v, l]) => {
    const b = h('button', { type: 'button', class: st[key] === v ? 'on' : '' }, l);
    b.addEventListener('animationend', () => b.classList.remove('pop'));
    b.addEventListener('click', () => {
      if (st[key] === v) return;
      st[key] = v;
      wrap.querySelectorAll('button').forEach((x) => x.classList.toggle('on', x === b));
      b.classList.remove('pop'); void b.offsetWidth; b.classList.add('pop');
      ui.changed();
      if (rebuild) ui.rebuild();
    });
    wrap.appendChild(b);
  });
  return wrap;
}

/* окно меняет высоту плавно: до/после изменения содержимого */
function wiSmooth(box, mutate) {
  if (!box || !box.isConnected || (typeof REDUCED !== 'undefined' && REDUCED)) { mutate(); return; }
  const h0 = box.offsetHeight;
  mutate();
  const h1 = box.offsetHeight;
  if (Math.abs(h0 - h1) < 2 || !box.animate) return;
  const ov = box.style.overflow; box.style.overflow = 'hidden';
  const a = box.animate([{ height: h0 + 'px' }, { height: h1 + 'px' }], { duration: 260, easing: 'cubic-bezier(.2,.8,.2,1)' });
  const done = () => { box.style.overflow = ov; };
  a.onfinish = done; a.oncancel = done;
}

/* ---------- Конструкция: шапка модуля ---------- */

const WI_C_STATIC = {
  id: 'static', title: 'Шапка модуля', desc: 'NAME, MODULE_NAME, VERSION, AUTHOR, DESCRIPTION',
  init(ctx) {
    const cur = wiReadHeader(ctx.source);
    return { exists: !!cur, locked: !!(cur && ctx.locked),
      name: cur && cur.NAME || 'Мой модуль', module_name: cur && cur.MODULE_NAME || 'my_module', version: cur && cur.VERSION || '1.0',
      author: cur && cur.AUTHOR || 'me', description: cur && cur.DESCRIPTION || 'Что делает модуль' };
  },
  form(st, ui) {
    const lock = st.locked;
    return [
      st.locked ? h('div', { class: 'wi-note' }, 'NAME, MODULE_NAME и AUTHOR у существующего модуля защищены — меняются только VERSION и DESCRIPTION.') : null,
      wiFld('NAME — название в списках', wiText(st, 'name', ui, { disabled: lock })),
      wiFld('MODULE_NAME — внутренний идентификатор', wiText(st, 'module_name', ui, { disabled: lock }), 'латиница, цифры, _ и -; по нему модуль хранит конфиги и базу'),
      wiFld('VERSION', wiText(st, 'version', ui)),
      wiFld('AUTHOR', wiText(st, 'author', ui, { disabled: lock })),
      wiFld('DESCRIPTION', wiText(st, 'description', ui, { area: true, rows: 2 })),
    ];
  },
  build(st) {
    const errors = [];
    if (!/^[A-Za-z0-9_\-]{1,64}$/.test(st.module_name)) errors.push('MODULE_NAME: только латиница, цифры, _ и - (до 64 символов)');
    if (!String(st.name).trim()) errors.push('NAME не может быть пустым');
    if (!String(st.version).trim()) errors.push('VERSION не может быть пустым');
    const lines = [`NAME = ${wiPyStr(st.name)}`, `MODULE_NAME = ${wiPyStr(st.module_name)}`, `VERSION = ${wiPyStr(st.version)}`,
      `AUTHOR = ${wiPyStr(st.author)}`, `DESCRIPTION = ${wiPyStr(st.description)}`];
    if (st.exists) {
      const shown = [lines[2], lines[4]].join('\n');
      return { code: shown, kind: 'top', errors, note: 'В файле шапка уже есть — будут обновлены только строки VERSION и DESCRIPTION.',
        apply(ed) {
          let n = 0;
          [['VERSION', lines[2]], ['DESCRIPTION', lines[4]]].forEach(([key, line]) => {
            const v = ed.value, re = new RegExp('^' + key + '[ \\t]*=.*$', 'm'), m = re.exec(v);
            if (m && m[0] !== line) { ed.edit(m.index, m.index + m[0].length, line, m.index + line.length); n++; }
          });
          return { note: n ? '' : 'значения уже такие же' };
        } };
    }
    return { code: lines.join('\n'), kind: 'top', needs: [], errors };
  },
};

/* каждая конструкция: init / form / build (форма → код) и scan (код → найденные блоки с состоянием для «править») */

WI_C_STATIC.scan = (src) => {
  const re = /^(NAME|MODULE_NAME|VERSION|AUTHOR|DESCRIPTION)[ \t]*=.*$/gm;
  let m, a = -1, b = -1, n = 0;
  while ((m = re.exec(src)) !== null) { if (a < 0) a = m.index; b = m.index + m[0].length; n++; }
  const cur = wiReadHeader(src);
  if (n < 2 || !cur) return [];
  return [{ a, b, ra: a, rb: b, indent: '', st: { exists: true, name: cur.NAME || '', module_name: cur.MODULE_NAME || '', version: cur.VERSION || '1.0', author: cur.AUTHOR || '', description: cur.DESCRIPTION || '' } }];
};

/* ---------- Конструкция: декоратор (чистый — декоратор, функция и докстринг, без кода внутри) ---------- */

const WI_DECO_USER = [['command', 'команда'], ['watcher', 'вотчер (все сообщения)'], ['on_start', 'при запуске'], ['on_load', 'при загрузке модуля'],
  ['cycle', 'цикл по таймеру'], ['telegram_event', 'служебное событие Telegram'], ['callback', 'нажатие inline-кнопки'], ['on_error', 'ошибка в модуле'],
  ['on_account_switch', 'смена аккаунта']];
const WI_DECO_BOT = [['command', 'команда бота'], ['watcher', 'вотчер бота'], ['on_start', 'при запуске'], ['on_load', 'при загрузке модуля'],
  ['cycle', 'цикл по таймеру'], ['telegram_event', 'служебное событие'], ['on_error', 'ошибка в модуле']];
const WI_DEFAULT_FN = { command: null, watcher: 'watch_messages', on_start: 'on_start', on_load: 'on_module_loaded', cycle: 'tick', telegram_event: 'on_tg_event',
  callback: 'on_callback', on_error: 'on_any_error', on_account_switch: 'on_account_switched' };
const WI_SIG = { command: 'event', watcher: 'event', on_start: '', on_load: 'module_info', cycle: '', telegram_event: 'event', callback: 'event',
  on_error: 'error_text, context', on_account_switch: 'number' };
const WI_DECO_DOC = { command: 'поздороваться', watcher: 'реагирует на все сообщения', on_start: 'выполняется при запуске', on_load: 'выполняется при загрузке модуля',
  cycle: 'выполняется по таймеру', telegram_event: 'реагирует на служебное событие', callback: 'реагирует на нажатие кнопки', on_error: 'реагирует на ошибку в модуле',
  on_account_switch: 'реагирует на смену аккаунта' };

const WI_C_DECO = {
  id: 'deco', title: 'Декоратор', desc: 'команда, вотчер, цикл, события — декоратор, функция и докстринг',
  init() { return { side: 'user', type: 'command', names: 'hello, hi', fname: '', doc: WI_DECO_DOC.command, n: 1, unit: 'h', access: 'self', watch: 'bot', only_self: true }; },
  form(st, ui) {
    const types = st.side === 'bot' ? WI_DECO_BOT : WI_DECO_USER;
    if (!types.some(([v]) => v === st.type)) st.type = 'command';
    if (st._last && st._last !== st.type && (st.doc === WI_DECO_DOC[st._last] || !st.doc)) st.doc = WI_DECO_DOC[st.type];   // докстринг по умолчанию следует за типом
    st._last = st.type;
    const f = [
      wiFld('Для кого', wiSeg(st, 'side', [['user', 'юзербот'], ['bot', 'стационарный бот']], ui, true)),
      wiFld('Что ловим', wiDrop(st, 'type', types, ui, true)),
    ];
    if (st.type === 'command') f.push(wiFld(st.side === 'bot' ? 'Команды (без «/»)' : 'Команды (без префикса)', wiText(st, 'names', ui), 'через запятую; первая — основная, остальные — алиасы'));
    if (st.type === 'cycle') f.push(wiFld('Интервал', h('div', { class: 'wi-inline' }, wiText(st, 'n', ui, { type: 'number' }),
      wiDrop(st, 'unit', [['s', 'секунд'], ['m', 'минут'], ['h', 'часов'], ['d', 'дней']], ui))));
    if (st.side === 'bot' && (st.type === 'command' || st.type === 'watcher')) f.push(wiFld('Кто может вызвать', wiDrop(st, 'access', [['self', 'только владелец (self)'], ['public', 'все (public)']], ui)));
    if (st.side === 'bot' && st.type === 'watcher') f.push(wiFld('Что слушает', wiDrop(st, 'watch', [['bot', 'сообщения бота'], ['userbot', 'сообщения юзербота'], ['all', 'оба']], ui)));
    if (st.type === 'on_load') f.push(wiCheck(st, 'only_self', 'реагировать только на загрузку этого модуля', ui));
    f.push(wiFld('Описание (докстринг)', wiText(st, 'doc', ui)));
    const name = wiText(st, 'fname', ui, { placeholder: 'авто' });
    const hint = h('span', { class: 'wi-hint' }, '');
    ui.hook((res) => { name.placeholder = res.final || 'авто'; hint.textContent = st.fname ? 'имя задано вручную' : `авто → ${res.final}`; });
    f.push(h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Имя функции'), name, hint));
    return f;
  },
  build(st, ctx) {
    const errors = [], user = st.side !== 'bot', ns = user ? 'decorators' : 'bot';
    const taken = ctx.taken();
    let dec, base = WI_DEFAULT_FN[st.type];
    const names = wiNames(st.names);
    if (st.type === 'command') {
      if (!names.length) errors.push('Укажите хотя бы одну команду');
      names.forEach((n) => {
        if (!/^\w+$/.test(n)) errors.push(`Команда «${n}»: только буквы, цифры и _`);
        else if (taken.cmds[user ? 'user' : 'bot'].has(n)) errors.push(`Команда «${n}» уже есть в этом модуле`);
      });
      base = wiSlug(names[0], 'my') + '_cmd';
      dec = `@${ns}.command(${names.map(wiPyStr).join(', ')}${user ? '' : `, access=${wiPyStr(st.access)}`})`;
    } else if (st.type === 'watcher') {
      dec = user ? '@decorators.watcher()' : `@bot.watcher(watch=${wiPyStr(st.watch)}, access=${wiPyStr(st.access)})`;
    } else if (st.type === 'cycle') {
      const n = Number(st.n);
      if (!(n > 0) || !Number.isInteger(n)) errors.push('Интервал — целое число больше нуля');
      dec = `@${ns}.cycle(interval=${wiPyStr(`${st.n}${st.unit}`)})`;
    } else dec = `@${ns}.${st.type}()`;
    if (!user) base = 'bot_' + base;
    const auto = wiFreeName(base, taken.fns);
    const final = String(st.fname || '').trim() || auto;
    if (!/^[A-Za-z_]\w*$/.test(final)) errors.push('Имя функции: латиница, цифры и _, не с цифры');
    else if (st.fname && taken.fns.has(final)) errors.push(`Имя функции «${final}» уже занято в этом модуле`);
    const lines = [dec, `async def ${final}(${WI_SIG[st.type] || ''}):`, `    """${wiDoc(st.doc || WI_DECO_DOC[st.type] || 'описание')}"""`];
    if (st.type === 'on_load' && st.only_self) lines.push('    if module_info.module_name != MODULE_NAME:', '        return');
    return { code: lines.join('\n'), kind: 'top', needs: [ns], errors, final };
  },
  scan(src) {
    const out = [];
    wiHeads(src).forEach((hd) => {
      if (hd.ns !== 'decorators' && hd.ns !== 'bot') return;
      const side = hd.ns === 'bot' ? 'bot' : 'user';
      if (!(side === 'bot' ? WI_DECO_BOT : WI_DECO_USER).some(([v]) => v === hd.dtype)) return;
      const a = wiTry(() => wiPyArgs(hd.args));
      if (!a) return;
      const st = { side, type: hd.dtype, names: 'hello', fname: hd.fname, doc: hd.doc !== null ? hd.doc : '', n: 1, unit: 'h', access: wiStrOf(a.kw.access) || 'self', watch: wiStrOf(a.kw.watch) || 'bot' };
      if (hd.dtype === 'command') st.names = a.pos.filter(wiIsStr).join(', ');
      if (hd.dtype === 'cycle') {
        const iv = wiStrOf(a.kw.interval !== undefined ? a.kw.interval : a.pos[0]), m = iv && /^(\d+)([smhd])$/.exec(iv);
        if (m) { st.n = Number(m[1]); st.unit = m[2]; }
      }
      let rb = hd.b;
      st.only_self = false;
      if (hd.dtype === 'on_load') {
        const p = (hd.sig.split(',')[0] || '').trim() || 'module_info';
        const gm = new RegExp('^\\n' + hd.indent + '    if ' + p + '\\.module_name != MODULE_NAME:\\n' + hd.indent + '        return[ \\t]*(?=\\n|$)').exec(src.slice(hd.b));
        if (gm) { st.only_self = true; rb = hd.b + gm[0].length; }
      }
      out.push({ a: hd.a, b: hd.blockEnd, ra: hd.a, rb, indent: hd.indent, st });
    });
    return out;
  },
};

/* ---------- Конструкция: inline-форма (только создание: form = await engine.create_inlineform) ---------- */

const wiNewBtn = (label, kind) => ({ label: label || 'Кнопка', kind: kind || 'click', data: wiSlug(label, ''), dataAuto: true, hint: 'Введите значение...' });

/* общий макет «как в Telegram» с редактором кнопок; возвращает массив узлов формы */
function wiFormDesigner(st, ui) {
  const kbHost = h('div', { class: 'wi-tg-host' }), edHost = h('div', { class: 'wi-btn-ed' });
  const cur = () => (st.rows[st.sel[0]] || [])[st.sel[1]] || null;
  const fixSel = () => {
    if (!st.rows.length) { st.sel = [0, 0]; return; }
    st.sel[0] = Math.min(st.sel[0], st.rows.length - 1);
    st.sel[1] = Math.min(st.sel[1], Math.max(0, st.rows[st.sel[0]].length - 1));
  };
  const select = (r, c) => { st.sel = [r, c]; renderKb(); renderEditor(); };
  const addBtn = (r) => {
    const n = st.rows.reduce((a, row) => a + row.length, 0) + 1;
    st.rows[r].push(wiNewBtn('Кнопка ' + n)); st.rows[r][st.rows[r].length - 1].data = 'btn_' + n;
    select(r, st.rows[r].length - 1); ui.changed();
  };
  function renderKb() {
    fixSel();
    const rows = st.rows.map((row, ri) => h('div', { class: 'wi-tg-row' },
      row.map((b, ci) => h('button', { type: 'button', class: 'wi-tg-btn' + (b.kind === 'input' ? ' inp' : '') + (st.sel[0] === ri && st.sel[1] === ci ? ' sel' : ''),
        onclick: () => select(ri, ci), title: b.kind === 'input' ? 'кнопка «введите значение»' : 'кнопка с callback data' }, b.label || '…')),
      row.length < 6 ? h('button', { type: 'button', class: 'wi-tg-add', title: 'добавить кнопку в ряд', onclick: () => addBtn(ri) }, '+') : null));
    kbHost.replaceChildren(
      h('div', { class: 'wi-tg-bubble' }, h('div', { class: 'wi-tg-text' }, st.text || ' '), h('div', { class: 'wi-tg-time' }, '12:00')),
      h('div', { class: 'wi-tg-kb' }, rows,
        st.rows.length < 12 ? h('button', { type: 'button', class: 'wi-tg-addrow', onclick: () => { st.rows.push([wiNewBtn('Кнопка')]); st.rows[st.rows.length - 1][0].data = 'btn_' + st.rows.length; select(st.rows.length - 1, 0); ui.changed(); } }, '+ ряд кнопок') : null));
  }
  function renderEditor() {
    const b = cur();
    if (!b) { edHost.replaceChildren(h('div', { class: 'wi-note' }, 'Кнопок нет — добавьте ряд.')); return; }
    const [r, c] = st.sel;
    const labelIn = h('input', { class: 'wi-field', value: b.label, spellcheck: 'false' });
    const dataIn = h('input', { class: 'wi-field', value: b.data, spellcheck: 'false', placeholder: 'callback data' });
    labelIn.addEventListener('input', () => {
      b.label = labelIn.value;
      if (b.dataAuto) { b.data = wiSlug(b.label, b.data || 'btn'); dataIn.value = b.data; }
      const el = kbHost.querySelectorAll('.wi-tg-row')[r].querySelectorAll('.wi-tg-btn')[c]; if (el) el.textContent = b.label || '…';
      ui.changed();
    });
    dataIn.addEventListener('input', () => { b.data = dataIn.value; b.dataAuto = false; ui.changed(); });
    const move = (dr, dc) => {
      const row = st.rows[r];
      if (dc) { const t = c + dc; if (t < 0 || t >= row.length) return; [row[c], row[t]] = [row[t], row[c]]; select(r, t); }
      else { const t = r + dr; if (t < 0 || t >= st.rows.length) return; const [x] = row.splice(c, 1); st.rows[t].push(x); if (!row.length) st.rows.splice(r, 1); select(Math.min(t, st.rows.length - 1), st.rows[Math.min(t, st.rows.length - 1)].length - 1); }
      ui.changed();
    };
    const del = () => { st.rows[r].splice(c, 1); if (!st.rows[r].length) st.rows.splice(r, 1); renderKb(); renderEditor(); ui.changed(); };
    const kindSel = wiDrop(b, 'kind', [['click', 'обычная (callback)'], ['input', 'ввод значения (InputButton)']], { changed: () => { renderKb(); renderEditor(); ui.changed(); }, rebuild() {} }, false);
    const parts = [
      h('div', { class: 'wi-btn-head' }, h('b', {}, `Кнопка · ряд ${r + 1}, место ${c + 1}`),
        h('div', { class: 'wi-btn-tools' },
          h('button', { type: 'button', class: 'wi-ico', title: 'левее', onclick: () => move(0, -1) }, '←'),
          h('button', { type: 'button', class: 'wi-ico', title: 'правее', onclick: () => move(0, 1) }, '→'),
          h('button', { type: 'button', class: 'wi-ico', title: 'ряд выше', onclick: () => move(-1, 0) }, '↑'),
          h('button', { type: 'button', class: 'wi-ico', title: 'ряд ниже', onclick: () => move(1, 0) }, '↓'),
          h('button', { type: 'button', class: 'wi-ico bad', title: 'удалить кнопку', onclick: del }, '×'))),
      wiFld('Текст на кнопке', labelIn),
      wiFld('Тип', kindSel),
      wiFld('callback data', dataIn, 'по ней обработчик узнаёт, какая кнопка нажата (до 64 байт)'),
    ];
    if (b.kind === 'input') {
      const hint = h('input', { class: 'wi-field', value: b.hint, spellcheck: 'false' });
      hint.addEventListener('input', () => { b.hint = hint.value; ui.changed(); });
      parts.push(wiFld('Подсказка в поле ввода', hint));
    }
    edHost.replaceChildren(...parts);
  }
  const textIn = wiText(st, 'text', { changed: () => { renderKb(); ui.changed(); } }, { area: true, rows: 3 });
  renderKb(); renderEditor();
  return [wiFld('Текст сообщения', textIn, 'HTML-теги Telegram: <b>, <i>, <code>, <a href="">'),
    h('div', { class: 'wi-lab' }, 'Так форма выглядит в Telegram — нажмите кнопку, чтобы её настроить'), kbHost, edHost];
}

function wiButtonErrors(st) {
  const errors = [], seen = new Set();
  st.rows.filter((r) => r.length).flat().forEach((b) => {
    if (!String(b.label).trim()) errors.push('У одной из кнопок пустой текст');
    if (!b.data) errors.push(`У кнопки «${b.label}» не задан callback data`);
    else if (new TextEncoder().encode(b.data).length > 64) errors.push(`callback data кнопки «${b.label}» длиннее 64 байт`);
    if (b.data && seen.has(b.kind + ':' + b.data)) errors.push(`callback data «${b.data}» повторяется — кнопки будут неразличимы`);
    seen.add(b.kind + ':' + b.data);
  });
  return errors;
}
function wiButtonsLit(st, ind) {
  const rows = st.rows.filter((r) => r.length);
  if (!rows.length) return null;
  const lit = (b) => b.kind === 'input' ? `engine.InputButton(${wiPyStr(b.label)}, ${wiPyStr(b.data)}, hint=${wiPyStr(b.hint)})` : `(${wiPyStr(b.label)}, ${wiPyStr(b.data)})`;
  return ['[', ...rows.map((r) => '    [' + r.map(lit).join(', ') + '],'), ']'].map((l, i) => (i ? ind : '') + l).join('\n');
}
const wiFormInit = (extra) => {
  const yes = wiNewBtn('Да'), no = wiNewBtn('Нет'); yes.data = 'yes'; no.data = 'no';
  return Object.assign({ text: 'Заголовок формы\nМожно использовать <b>HTML</b>', rows: [[yes, no]], sel: [0, 0] }, extra || {});
};

const WI_C_FORM = {
  id: 'form', title: 'Inline-форма', desc: 'form = await engine.create_inlineform(...) — без обработчиков кнопок',
  init() { return wiFormInit({ var: 'form', for_user: 'me', user_id: '', timeout: '' }); },
  form(st, ui) {
    const out = [wiFld('Переменная формы', wiText(st, 'var', ui)), ...wiFormDesigner(st, ui),
      wiFld('Кто может нажимать', wiDrop(st, 'for_user', [['me', 'только я'], ['chat', 'все в этом чате'], ['id', 'конкретный user id']], ui, true))];
    if (st.for_user === 'id') out.push(wiFld('user id', wiText(st, 'user_id', ui, { type: 'number' })));
    out.push(wiFld('Закрыть после простоя, сек', wiText(st, 'timeout', ui, { type: 'number', placeholder: 'не закрывать' })));
    return out;
  },
  build(st) {
    const errors = wiButtonErrors(st);
    if (!/^[A-Za-z_]\w*$/.test(st.var || '')) errors.push('Имя переменной: латиница, цифры и _');
    const kb = wiButtonsLit(st, '');
    const args = ['chat_id=event.chat_id', `text=${wiPyStr(st.text)}`];
    if (kb) args.push('buttons=' + kb);
    if (st.for_user === 'chat') args.push('for_user=event.chat_id');
    if (st.for_user === 'id') { if (!st.user_id) errors.push('Укажите user id'); else args.push(`for_user=${Math.trunc(Number(st.user_id))}`); }
    if (st.timeout !== '' && Number(st.timeout) > 0) args.push(`timeout=${Math.trunc(Number(st.timeout))}`);
    const code = [`${st.var || 'form'} = await engine.create_inlineform(`, ...args.map((a) => '    ' + a.replace(/\n/g, '\n    ') + ','), ')'].join('\n');
    return { code, kind: 'block', needs: ['engine'], errors, note: 'Нужен стационарный бот с включённым inline-режимом и inline feedback (BotFather). Обработчики кнопок — отдельная конструкция.' };
  },
  scan(src) {
    return wiScanForms(src).map((f) => ({ a: f.a, b: f.b, ra: f.a, rb: f.b, indent: f.indent,
      st: wiFormInit({ var: f.var, text: f.text, rows: f.rows.length ? f.rows : [], for_user: f.forUser, user_id: f.userId, timeout: f.timeout }) }));
  },
};

/* ---------- Конструкция: обработчик кнопки (докстринг, без кода внутри) ---------- */

const WI_C_HANDLER = {
  id: 'handler', title: 'Обработчик кнопки', desc: '@form.on_click / on_input — декоратор, функция и докстринг',
  init(ctx) {
    const st = { var: (ctx.forms[0] && ctx.forms[0].var) || 'form', kind: 'click', data: 'yes', fname: '', doc: 'нажатие кнопки' };
    const first = ctx.buttons().find((b) => !b.handled);
    if (first) { st.data = first.data; st.kind = first.kind; st.doc = `нажатие «${first.label}»`; st.var = first.var; }
    return st;
  },
  form(st, ui, ctx) {
    const f = [wiFld('Переменная формы', wiText(st, 'var', ui)), wiFld('Тип кнопки', wiSeg(st, 'kind', [['click', 'обычная (on_click)'], ['input', 'ввод значения (on_input)']], ui))];
    const known = ctx.buttons();
    if (known.length) {
      f.push(h('div', { class: 'wi-lab' }, 'Кнопки из форм в этом файле (серые — обработчик уже есть)'),
        h('div', { class: 'wi-chips' }, known.map((b) => h('button', { type: 'button', class: 'wi-chip' + (b.handled ? ' dim' : '') + (st.data === b.data && st.kind === b.kind ? ' on' : ''),
          onclick: () => { st.data = b.data; st.kind = b.kind; st.var = b.var; st.doc = `нажатие «${b.label}»`; st.fname = ''; ui.rebuild(); } }, b.label))));
    }
    f.push(wiFld('callback data', wiText(st, 'data', ui), 'по нему обработчик связывается с кнопкой'));
    f.push(wiFld('Описание (докстринг)', wiText(st, 'doc', ui)));
    const name = wiText(st, 'fname', ui, { placeholder: 'авто' });
    const hint = h('span', { class: 'wi-hint' }, '');
    ui.hook((res) => { name.placeholder = res.final || 'авто'; hint.textContent = st.fname ? 'имя задано вручную' : `авто → ${res.final}`; });
    f.push(h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Имя функции'), name, hint));
    return f;
  },
  build(st, ctx) {
    const errors = [], taken = ctx.taken();
    if (!st.data) errors.push('Укажите callback data');
    if (!/^[A-Za-z_]\w*$/.test(st.var || '')) errors.push('Имя переменной формы: латиница, цифры и _');
    const input = st.kind === 'input';
    const auto = wiFreeName((input ? 'on_input_' : 'on_') + wiSlug(st.data, 'btn'), taken.fns);
    const final = String(st.fname || '').trim() || auto;
    if (!/^[A-Za-z_]\w*$/.test(final)) errors.push('Имя функции: латиница, цифры и _, не с цифры');
    else if (st.fname && taken.fns.has(final)) errors.push(`Имя функции «${final}» уже занято в этом модуле`);
    const code = [`@${st.var}.${input ? 'on_input' : 'on_click'}(data=${wiPyStr(st.data)})`, `async def ${final}(${input ? 'inp' : 'click'}):`, `    """${wiDoc(st.doc || 'нажатие кнопки')}"""`].join('\n');
    return { code, kind: 'block', needs: ['engine'], errors, final, note: 'Вставляйте после создания формы — обработчик привязан к её переменной.' };
  },
  scan(src) {
    const out = [];
    wiHeads(src).forEach((hd) => {
      if (!/^on_(click|input)$/.test(hd.dtype) || ['decorators', 'bot', 'web', 'engine'].includes(hd.ns)) return;
      const a = wiTry(() => wiPyArgs(hd.args));
      const data = a && wiStrOf(a.kw.data !== undefined ? a.kw.data : a.pos[0]);
      if (data === null || data === undefined) return;
      out.push({ a: hd.a, b: hd.blockEnd, ra: hd.a, rb: hd.b, indent: hd.indent, st: { var: hd.ns, kind: hd.dtype === 'on_input' ? 'input' : 'click', data, fname: hd.fname, doc: hd.doc !== null ? hd.doc : '' } });
    });
    return out;
  },
};

/* ---------- Конструкция: form.edit (знает формы из файла) ---------- */

const WI_C_FORMEDIT = {
  id: 'form_edit', title: 'form.edit', desc: 'изменить текст и кнопки уже созданной формы',
  init(ctx) {
    const f = ctx.forms[0];
    return wiFormInit(Object.assign({ var: f ? f.var : 'form', buttons: f ? 'new' : 'keep', pick: f ? 0 : -1 }, f ? { text: f.text, rows: f.rows.length ? f.rows : [] } : {}));
  },
  form(st, ui, ctx) {
    const out = [];
    if (ctx.forms.length) {
      out.push(wiFld('Какая форма', wiDrop({ get pick() { return String(st.pick); }, set pick(v) { st.pick = Number(v); } }, 'pick',
        ctx.forms.map((f, i) => [String(i), `${f.var}: «${f.text.replace(/\s+/g, ' ').slice(0, 36)}»`]).concat([['-1', 'другая']]), {
          changed() {}, rebuild() {
            const f = ctx.forms[st.pick];
            if (f) { st.var = f.var; st.text = f.text; st.rows = f.rows.map((r) => r.map((b) => Object.assign({}, b))); st.sel = [0, 0]; st.buttons = f.rows.length ? 'new' : 'none'; }
            ui.rebuild();
          } }, true), 'подставит её текст и кнопки — их можно править'));
    }
    out.push(wiFld('Переменная формы', wiText(st, 'var', ui)));
    out.push(wiFld('Кнопки', wiSeg(st, 'buttons', [['keep', 'оставить как есть'], ['new', 'новые'], ['none', 'убрать']], ui, true)));
    out.push(...wiFormDesigner(st, ui).slice(0, st.buttons === 'new' ? 4 : 1));
    return out;
  },
  build(st) {
    const errors = st.buttons === 'new' ? wiButtonErrors(st) : [];
    if (!/^[A-Za-z_]\w*$/.test(st.var || '')) errors.push('Имя переменной: латиница, цифры и _');
    let tail = '';
    if (st.buttons === 'keep') tail = `, buttons=${st.var}._raw_buttons`;
    else if (st.buttons === 'new') { const kb = wiButtonsLit(st, ''); if (kb) tail = ', buttons=' + kb; else errors.push('Добавьте хотя бы одну кнопку или выберите «убрать»'); }
    else tail = ', buttons=None';
    return { code: `await ${st.var}.edit(${wiPyStr(st.text)}${tail})`, kind: 'block', needs: ['engine'], errors,
      note: st.buttons === 'keep' ? 'form.edit(текст) без buttons убирает клавиатуру — поэтому «как есть» передаёт прежние кнопки формы.' : '' };
  },
  scan(src) {
    const forms = new Set(wiScanForms(src).map((f) => f.var));
    const out = [];
    for (const m of src.matchAll(/^([ \t]*)await\s+(\w+)\.edit\(/gm)) {
      if (!forms.has(m[2]) && !/form/i.test(m[2])) continue;
      const open = m.index + m[0].length - 1, close = wiMatchParen(src, open);
      if (close < 0) continue;
      const a = wiTry(() => wiPyArgs(src.slice(open + 1, close)));
      if (!a) continue;
      const text = wiStrOf(a.kw.text !== undefined ? a.kw.text : a.pos[0]);
      if (text === null) continue;
      const b = a.kw.buttons !== undefined ? a.kw.buttons : a.pos[1];
      let mode = 'none', rows = [];
      if (b && b.expr && /_raw_buttons$/.test(b.expr)) mode = 'keep';
      else if (Array.isArray(b)) { rows = wiTry(() => wiParseButtons(b)); if (!rows) continue; mode = rows.length ? 'new' : 'none'; }
      else if (b !== undefined && b !== null) continue;
      out.push({ a: m.index, b: close + 1, ra: m.index, rb: close + 1, indent: m[1], st: wiFormInit({ var: m[2], text, rows, buttons: mode, pick: -1 }) });
    }
    return out;
  },
};

/* ---------- Конструкция: конфиг (вызов create_config; в какую функцию его поставить — решает автор) ---------- */

const WI_CFG_TYPES = [['str', 'строка'], ['int', 'целое число'], ['float', 'дробное число'], ['bool', 'да / нет'], ['list', 'список'],
  ['choice', 'выбор одного'], ['choices', 'выбор нескольких']];

const WI_C_CONFIG = {
  id: 'config', title: 'Конфиг', desc: 'await engine.create_config(...) — настройка для вкладки «конфиги»',
  init() { return { key: 'my_option', description: 'Описание настройки', type: 'str', def: 'значение', def_bool: true, options: 'один, два, три', def_choice: 'один', min: '', max: '' }; },
  form(st, ui) {
    const f = [
      wiFld('Ключ', wiText(st, 'key', ui), 'по нему настройку читают: await engine.get_config("ключ")'),
      wiFld('Описание', wiText(st, 'description', ui)),
      wiFld('Тип', wiDrop(st, 'type', WI_CFG_TYPES, ui, true)),
    ];
    if (st.type === 'str') f.push(wiFld('Значение по умолчанию', wiText(st, 'def', ui)), wiFld('Длина', h('div', { class: 'wi-inline' }, wiText(st, 'min', ui, { type: 'number', placeholder: 'мин' }), wiText(st, 'max', ui, { type: 'number', placeholder: 'макс' }))));
    if (st.type === 'int' || st.type === 'float') {
      if (typeof st.def !== 'number') st.def = 0;
      f.push(wiFld('Значение по умолчанию', wiText(st, 'def', ui, { type: 'number' })), wiFld('Границы', h('div', { class: 'wi-inline' }, wiText(st, 'min', ui, { type: 'number', placeholder: 'мин' }), wiText(st, 'max', ui, { type: 'number', placeholder: 'макс' }))));
    }
    if (st.type === 'bool') f.push(wiCheck(st, 'def_bool', 'включено по умолчанию', ui));
    if (st.type === 'list') f.push(wiFld('Элементы по умолчанию', wiText(st, 'options', ui), 'через запятую'));
    if (st.type === 'choice' || st.type === 'choices') {
      f.push(wiFld('Варианты', wiText(st, 'options', ui), 'через запятую'));
      f.push(wiFld(st.type === 'choice' ? 'Выбрано по умолчанию' : 'Выбрано по умолчанию (через запятую)', wiText(st, 'def_choice', ui), 'значение из вариантов, не номер'));
    }
    return f;
  },
  build(st) {
    const errors = [];
    const list = String(st.options).split(',').map((x) => x.trim()).filter(Boolean);
    if (!/^[A-Za-z_][\w]*$/.test(st.key)) errors.push('Ключ: латиница, цифры и _, не с цифры');
    let def; const extra = [];
    const valid = (a, b) => { const v = []; if (st.min !== '') v.push(`"${a}": ${Number(st.min)}`); if (st.max !== '') v.push(`"${b}": ${Number(st.max)}`); if (v.length) extra.push(`valid={${v.join(', ')}}`); };
    switch (st.type) {
      case 'str': def = wiPyStr(st.def); valid('min_len', 'max_len'); break;
      case 'int': case 'float': def = st.type === 'int' ? String(Math.trunc(Number(st.def) || 0)) : String(Number(st.def) || 0);
        if (st.type === 'float' && !def.includes('.')) def += '.0';
        valid('min_value', 'max_value'); break;
      case 'bool': def = st.def_bool ? 'True' : 'False'; break;
      case 'list': def = wiPyLit(list); break;
      case 'choice': {
        if (list.length < 2) errors.push('Для выбора нужно хотя бы два варианта');
        if (!list.includes(String(st.def_choice).trim())) errors.push('Значение по умолчанию должно быть одним из вариантов');
        def = wiPyStr(String(st.def_choice).trim()); extra.push(`options=${wiPyLit(list)}`); break; }
      default: {
        const chosen = String(st.def_choice).split(',').map((x) => x.trim()).filter(Boolean);
        if (list.length < 2) errors.push('Для выбора нужно хотя бы два варианта');
        if (!chosen.length || chosen.some((c) => !list.includes(c))) errors.push('По умолчанию должен быть выбран хотя бы один вариант, и только из списка');
        def = wiPyLit(chosen); extra.push(`options=${wiPyLit(list)}`);
      }
    }
    const args = [`key=${wiPyStr(st.key)}`, `description=${wiPyStr(st.description)}`, `data_type=${wiPyStr(st.type)}`, `default=${def}`, ...extra];
    return { code: ['await engine.create_config(', ...args.map((a) => '    ' + a + ','), ')'].join('\n'), kind: 'block', needs: ['engine'], errors,
      note: 'Вызывайте внутри async-функции — обычно в @decorators.on_start() (конструкция «Декоратор»).' };
  },
  scan(src) {
    const out = [];
    for (const m of src.matchAll(/^([ \t]*)await\s+engine\.create_config\(/gm)) {
      const open = m.index + m[0].length - 1, close = wiMatchParen(src, open);
      if (close < 0) continue;
      const a = wiTry(() => wiPyArgs(src.slice(open + 1, close)));
      if (!a) continue;
      const g = (k, i) => (a.kw[k] !== undefined ? a.kw[k] : a.pos[i]);
      const key = wiStrOf(g('key', 0)), desc = wiStrOf(g('description', 1)), type = wiStrOf(g('data_type', 2)), def = g('default', 3);
      if (key === null || !WI_CFG_TYPES.some(([v]) => v === type)) continue;
      const st = { key, description: desc || '', type, def: '', def_bool: true, options: '', def_choice: '', min: '', max: '' };
      const valid = (a.kw.valid && a.kw.valid.dict) || {};
      const opts = Array.isArray(a.kw.options) ? a.kw.options.filter(wiIsStr) : [];
      if (type === 'str') { st.def = wiStrOf(def) || ''; if (typeof valid.min_len === 'number') st.min = valid.min_len; if (typeof valid.max_len === 'number') st.max = valid.max_len; }
      else if (type === 'int' || type === 'float') { st.def = typeof def === 'number' ? def : 0; if (typeof valid.min_value === 'number') st.min = valid.min_value; if (typeof valid.max_value === 'number') st.max = valid.max_value; }
      else if (type === 'bool') st.def_bool = def !== false;
      else if (type === 'list') st.options = (Array.isArray(def) ? def.filter(wiIsStr) : []).join(', ');
      else { st.options = opts.join(', '); st.def_choice = type === 'choice' ? (wiStrOf(def) || '') : (Array.isArray(def) ? def.filter(wiIsStr).join(', ') : ''); }
      out.push({ a: m.index, b: close + 1, ra: m.index, rb: close + 1, indent: m[1], st });
    }
    return out;
  },
};

/* ---------- Конструкция: иконки visual (три способа вставки) ---------- */

const WI_C_VISUAL = {
  id: 'visual', title: 'Иконка (visual)', desc: 'стандартный эмодзи — пользователь может его заменить',
  init(ctx) { return { key: 'status_success', variant: 'raw', var_: 'icon', icons: ctx.icons || {}, q: '' }; },
  form(st, ui) {
    const mk = (k, ic) => h('button', { type: 'button', class: 'wi-icon' + (st.key === k ? ' on' : ''), title: k,
      onclick: (e) => { st.key = k; e.currentTarget.parentNode.querySelectorAll('.wi-icon').forEach((x) => x.classList.toggle('on', x === e.currentTarget)); ui.changed(); } },
      h('span', { class: 'e' }, ic), h('span', { class: 'k' }, WI_VISUAL_DESC[k] || k));
    const match = (k, q) => !q || k.includes(q) || (WI_VISUAL_DESC[k] || '').includes(q);
    const grid = h('div', { class: 'wi-icons' }, Object.entries(st.icons).filter(([k]) => match(k, st.q.toLowerCase())).map(([k, ic]) => mk(k, ic)));
    const search = h('input', { class: 'wi-field', placeholder: 'поиск по названию', spellcheck: 'false', value: st.q });
    search.dataset.keep = 'vsearch'; grid.dataset.keep = 'vgrid';
    search.addEventListener('input', () => { st.q = search.value; const q = st.q.toLowerCase(); grid.replaceChildren(...Object.entries(st.icons).filter(([k]) => match(k, q)).map(([k, ic]) => mk(k, ic))); });
    const f = [
      wiFld('Как вставить', wiSeg(st, 'variant', [['raw', 'вызов'], ['fstr', 'в f-строку'], ['var', 'в переменную']], ui, true)),
    ];
    if (st.variant === 'var') f.push(wiFld('Имя переменной', wiText(st, 'var_', ui)));
    f.push(search, grid,
      Object.keys(st.icons).length ? null : h('div', { class: 'wi-note' }, 'Список иконок не загрузился — введите ключ вручную.'),
      wiFld('Ключ иконки', wiText(st, 'key', ui)));
    return f;
  },
  build(st) {
    const errors = [];
    if (Object.keys(st.icons).length && !(st.key in st.icons)) errors.push(`Нет иконки «${st.key}» — вызов упадёт с KeyError`);
    const call = `visual.visual(${wiPyStr(st.key)})`;
    if (st.variant === 'fstr') return { code: `{${call.replace(/"/g, "'")}}`, kind: 'expr', needs: ['visual'], errors, note: 'Вставляйте внутри f"…" — кавычки подобраны под него.' };
    if (st.variant === 'var') {
      if (!/^[A-Za-z_]\w*$/.test(st.var_ || '')) errors.push('Имя переменной: латиница, цифры и _');
      return { code: `${st.var_} = ${call}`, kind: 'block', needs: ['visual'], errors };
    }
    return { code: call, kind: 'expr', needs: ['visual'], errors };
  },
  scan(src, ctx) {
    const out = [];
    for (const m of src.matchAll(/visual\.visual\(\s*(["'])(\w+)\1\s*\)/g)) {
      const end = m.index + m[0].length, ls = wiLineStart(src, m.index), le = wiLineEnd(src, end);
      const st = { key: m[2], variant: 'raw', var_: 'icon', icons: (ctx && ctx.icons) || {}, q: '' };
      const pre = src.slice(ls, m.index), asg = /^([ \t]*)(\w+)\s*=\s*$/.exec(pre);
      if (asg && !src.slice(end, le).trim()) { st.variant = 'var'; st.var_ = asg[2]; out.push({ a: ls, b: le, ra: ls, rb: end, indent: asg[1], st }); continue; }
      if (src[m.index - 1] === '{' && src[end] === '}') { st.variant = 'fstr'; out.push({ a: m.index - 1, b: end + 1, ra: m.index - 1, rb: end + 1, indent: '', st }); continue; }
      out.push({ a: m.index, b: end, ra: m.index, rb: end, indent: '', st });
    }
    return out;
  },
};

/* ---------- Конструкция: данные (db / cache) ---------- */

const WI_C_DATA = {
  id: 'data', title: 'Данные', desc: 'db и cache: прочитать, записать, увеличить, добавить в список',
  init() { return { store: 'db', op: 'read', key: 'counter', scope: 'module', value: '"значение"', default_: '0', var_: 'value' }; },
  form(st, ui) {
    const ops = [['read', 'прочитать'], ['write', 'записать'], ['inc', 'увеличить на 1'], ['append', 'добавить в список'], ['delete', 'удалить']];
    const f = [
      wiFld('Где', wiSeg(st, 'store', [['db', 'db (на диске)'], ['cache', 'cache (до рестарта)']], ui)),
      wiFld('Что сделать', wiDrop(st, 'op', ops, ui, true)),
      wiFld('Ключ', wiText(st, 'key', ui)),
      wiFld('Область', wiSeg(st, 'scope', [['module', 'только этот модуль'], ['global', 'общая для всех модулей']], ui)),
    ];
    if (st.op === 'read') f.push(wiFld('Переменная', wiText(st, 'var_', ui)));
    if (['read', 'inc'].includes(st.op)) f.push(wiFld('Если ключа нет (выражение Python)', wiText(st, 'default_', ui), 'например 0, [], None, "текст"'));
    if (['write', 'append'].includes(st.op)) f.push(wiFld('Значение (выражение Python)', wiText(st, 'value', ui)));
    return f;
  },
  build(st) {
    const errors = [];
    if (!String(st.key).trim()) errors.push('Укажите ключ');
    const key = wiPyStr(st.key), v = st.var_ || 'value';
    if (!/^[A-Za-z_]\w*$/.test(v)) errors.push('Имя переменной: латиница, цифры и _');
    const p = st.store, g = st.scope === 'global' ? 'g' : '';
    const get = (d) => `await ${p}.${g}get(${key}${d ? ', ' + d : ''})`;
    const set = (x) => `await ${p}.${g}set(${key}, ${x})`;
    const code = { read: `${v} = ${get(st.default_)}`, write: set(st.value || 'None'),
      inc: `${v} = ${get(st.default_ || '0')}\n${set(v + ' + 1')}`,
      append: `items = ${get('[]')}\nitems.append(${st.value || 'None'})\n${set('items')}`,
      delete: `await ${p}.${g}delete(${key})` }[st.op];
    return { code, kind: 'block', needs: [p], errors };
  },
  scan(src) {
    const L = wiLines(src), out = [];
    const RD = /^([ \t]*)(\w+)\s*=\s*await\s+(db|cache)\.(g?)get\((.*)\)\s*$/, WR = /^([ \t]*)await\s+(db|cache)\.(g?)set\((.*)\)\s*$/, DL = /^([ \t]*)await\s+(db|cache)\.(g?)delete\((.*)\)\s*$/;
    const mk = (i, j, indent, st) => out.push({ a: L[i].s, b: L[j].e, ra: L[i].s, rb: L[j].e, indent, st: Object.assign({ store: 'db', op: 'read', key: '', scope: 'module', value: 'None', default_: 'None', var_: 'value' }, st) });
    for (let i = 0; i < L.length; i++) {
      let m = RD.exec(L[i].t);
      if (m) {
        const pr = wiSplitTop(m[5]), key = wiTry(() => wiPyParse(pr[0] || ''));
        if (wiIsStr(key) && pr.length <= 2) {
          const base = { store: m[3], scope: m[4] ? 'global' : 'module', key, var_: m[2], default_: pr[1] || 'None' };
          const n1 = L[i + 1] && WR.exec(L[i + 1].t), n2 = L[i + 2] && WR.exec(L[i + 2].t), ap = L[i + 1] && new RegExp('^' + m[1] + m[2] + '\\.append\\((.*)\\)\\s*$').exec(L[i + 1].t);
          const sp = (x) => (x ? wiSplitTop(x[4]) : []);
          if (n1 && n1[2] === m[3] && n1[3] === m[4] && sp(n1)[0] === pr[0] && sp(n1)[1] === `${m[2]} + 1`) { mk(i, i + 1, m[1], Object.assign(base, { op: 'inc' })); i += 1; continue; }
          if (ap && n2 && n2[2] === m[3] && n2[3] === m[4] && sp(n2)[0] === pr[0] && sp(n2)[1] === m[2]) { mk(i, i + 2, m[1], Object.assign(base, { op: 'append', value: ap[1] })); i += 2; continue; }
          mk(i, i, m[1], Object.assign(base, { op: 'read' })); continue;
        }
      }
      m = WR.exec(L[i].t);
      if (m) { const pr = wiSplitTop(m[4]), key = wiTry(() => wiPyParse(pr[0] || '')); if (wiIsStr(key) && pr.length === 2) mk(i, i, m[1], { store: m[2], scope: m[3] ? 'global' : 'module', key, op: 'write', value: pr[1] }); continue; }
      m = DL.exec(L[i].t);
      if (m) { const pr = wiSplitTop(m[4]), key = wiTry(() => wiPyParse(pr[0] || '')); if (wiIsStr(key) && pr.length === 1) mk(i, i, m[1], { store: m[2], scope: m[3] ? 'global' : 'module', key, op: 'delete' }); }
    }
    return out;
  },
};

/* ---------- Конструкция: логи (терминал / ЛС владельца через бота) ---------- */

const WI_LOG_LEVELS = [['info', 'info — сведение'], ['success', 'success — успех'], ['warning', 'warning — предупреждение'], ['error', 'error — ошибка']];
const WI_C_LOGS = {
  id: 'logs', title: 'Лог', desc: 'engine.tlog — в терминал, engine.botlog — в ЛС владельца через бота',
  init() { return { where: 'tlog', text: 'сработал обработчик', f: false, level: 'info' }; },
  form(st, ui) {
    const f = [wiFld('Куда писать', wiSeg(st, 'where', [['tlog', 'в терминал (tlog)'], ['botlog', 'в ЛС владельца (botlog)']], ui, true))];
    if (st.where === 'botlog') f.push(wiFld('Уровень', wiDrop(st, 'level', WI_LOG_LEVELS, ui)));
    f.push(wiFld('Текст', wiText(st, 'text', ui, { area: true, rows: 2 }), st.where === 'botlog' ? 'HTML допустим; защита от флуда встроена (лимитер)' : ''));
    f.push(wiCheck(st, 'f', 'f-строка — подставлять {переменные}', ui));
    return f;
  },
  build(st) {
    const errors = [];
    if (!String(st.text).trim()) errors.push('Текст не может быть пустым');
    const lit = (st.f ? 'f' : '') + wiPyStr(st.text);
    const code = st.where === 'botlog' ? `engine.botlog(${lit}${st.level && st.level !== 'info' ? `, level=${wiPyStr(st.level)}` : ''})` : `engine.tlog(${lit})`;
    return { code, kind: 'block', needs: ['engine'], errors, note: st.where === 'botlog' ? 'Нужен стационарный бот; без него текст уйдёт в терминал.' : '' };
  },
  scan(src) {
    const out = [];
    for (const m of src.matchAll(/^([ \t]*)(await\s+)?engine\.(tlog|botlog)\(/gm)) {
      const open = m.index + m[0].length - 1, close = wiMatchParen(src, open);
      if (close < 0 || src.slice(close + 1, wiLineEnd(src, close + 1)).trim()) continue;
      const a = wiTry(() => wiPyArgs(src.slice(open + 1, close)));
      if (!a) continue;
      const t = a.pos[0] !== undefined ? a.pos[0] : a.kw.text;
      const text = wiIsStr(t) ? t : (t && t.fstr !== undefined ? t.fstr : null);
      if (text === null) continue;
      const lv = wiStrOf(a.kw.level !== undefined ? a.kw.level : a.pos[1]);
      out.push({ a: m.index, b: close + 1, ra: m.index, rb: close + 1, indent: m[1], st: { where: m[3], text, f: !!(t && t.isF), level: WI_LOG_LEVELS.some(([v]) => v === lv) ? lv : 'info' } });
    }
    return out;
  },
};

const WI_CONSTRUCTS = [WI_C_STATIC, WI_C_DECO, WI_C_FORM, WI_C_HANDLER, WI_C_FORMEDIT, WI_C_CONFIG, WI_C_DATA, WI_C_VISUAL, WI_C_LOGS];

/* найти конструкцию под курсором: самая узкая из найденных */
function wiFindAt(src, pos, icons) {
  let best = null;
  const ctx = { icons };
  WI_CONSTRUCTS.forEach((def) => {
    let list = [];
    try { list = def.scan ? def.scan(src, ctx) : []; } catch (e) { list = []; }
    list.forEach((m) => {
      if (pos < m.a || pos > m.b) return;
      if (!best || (m.b - m.a) < (best.m.b - best.m.a)) best = { def, m };
    });
  });
  return best;
}

/* ---------- окно конструктора ---------- */

function wiDrawer(title, formNode, previewNode, footer, onClose) {
  const veil = h('div', { class: 'veil wi-veil' });
  let closed = false;
  const close = () => { if (closed) return; closed = true; veil.classList.add('out'); setTimeout(() => veil.remove(), 220); document.removeEventListener('keydown', onKey, true); if (onClose) onClose(); };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close, { once: true });          // ушли на другую страницу — окно не должно остаться висеть
  const box = h('div', { class: 'glass wi-drawer', role: 'dialog', 'aria-label': title },
    h('div', { class: 'wi-dhead' }, h('h2', {}, title), h('button', { class: 'wi-ico', type: 'button', onclick: close, title: 'закрыть (Esc)' }, '×')),
    h('div', { class: 'wi-dbody' + (previewNode ? '' : ' one') }, h('div', { class: 'wi-dform' }, formNode), previewNode ? h('div', { class: 'wi-dprev' }, previewNode) : null),
    h('div', { class: 'actions wi-dfoot' }, footer(close)));
  veil.appendChild(box);
  veil.addEventListener('mousedown', (e) => { if (e.target === veil) close(); });
  document.body.appendChild(veil);
  close.box = box;
  return close;
}

/* edit — найденный в коде блок {ra, rb, indent, st}: окно откроется с заполненными полями и заменит этот блок */
async function wiOpenConstruct(def, ed, host, edit) {
  if (host.readOnly) { toast('встроенные модули — только просмотр', 'error'); return; }
  const src = ed.value, range = edit ? [edit.ra, edit.rb] : null;
  const icons = def.id === 'visual' ? await host.icons() : {};
  const without = range ? src.slice(0, range[0]) + src.slice(range[1]) : src;
  const forms = wiScanForms(without);
  const ctx = { source: src, locked: host.locked(), icons, forms,
    taken: () => wiTaken(src, range),
    buttons() {
      const handled = new Set(wiHeads(without).filter((x) => /^on_(click|input)$/.test(x.dtype)).map((x) => {
        const a = wiTry(() => wiPyArgs(x.args)); const d = a && wiStrOf(a.kw.data !== undefined ? a.kw.data : a.pos[0]);
        return x.ns + '|' + (x.dtype === 'on_input' ? 'input' : 'click') + '|' + d;
      }));
      const seen = new Set(), out = [];
      forms.forEach((f) => f.rows.flat().forEach((b) => {
        const k = f.var + '|' + b.kind + '|' + b.data;
        if (seen.has(k)) return; seen.add(k);
        out.push({ label: b.label, data: b.data, kind: b.kind, var: f.var, handled: handled.has(k) });
      }));
      return out;
    } };
  const st = Object.assign(def.init(ctx), edit ? edit.st : {});
  if (def.id === 'visual') st.icons = icons;
  if (edit && (def.id === 'deco' || def.id === 'handler') && st.fname) {   // имя совпало с автоматическим — оставляем «авто»
    const keep = st.fname; st.fname = '';
    if (def.build(st, ctx).final !== keep) st.fname = keep;
  }

  const formBox = h('div', { class: 'wi-cform' });
  const notes = h('div', { class: 'wi-cnotes' });
  let built = null, manual = null, insertBtn, pvTimer = 0, closeFn = null;
  const hooks = [];
  const pv = wiCreateEditor({ value: '', onChange: (t) => onPreview(t) });
  pv.el.classList.add('wi-pv');

  const paintNotes = (res, extra) => {
    const bits = [];
    if (extra) bits.push(h('div', { class: 'wi-note' }, extra));
    ((res && res.errors) || []).forEach((e) => bits.push(h('div', { class: 'wi-err' }, e)));
    if (res && res.note) bits.push(h('div', { class: 'wi-note' }, res.note));
    const miss = ((res && res.needs) || []).filter((n, i, a) => a.indexOf(n) === i && !new RegExp('^\\s*(import\\s+' + n + '\\b|from\\s+' + n + '\\s+import)', 'm').test(ed.value));
    if (miss.length) bits.push(h('div', { class: 'wi-hint' }, 'Будут добавлены импорты: ' + miss.join(', ')));
    notes.replaceChildren(...bits);
    if (insertBtn) insertBtn.disabled = !!(res && res.errors && res.errors.length);
  };
  const update = () => {                                    // форма → код
    manual = null;
    built = def.build(st, ctx);
    pv.setValue(built.code);
    paintNotes(built);
    hooks.forEach((fn) => fn(built));
  };
  const paintForm = () => {
    // переключатели «таблетками» переносим из старой формы как есть: их не пересоздаём — нажатие не теряется, а анимация доигрывается
    const keep = new Map();
    formBox.querySelectorAll('[data-keep]').forEach((n) => keep.set(n.dataset.keep, n));
    const old = new Map();
    formBox.querySelectorAll('.wi-fld').forEach((f) => { const l = f.querySelector('.wi-lab'), c = f.querySelector('.wi-seg'); if (l && c) old.set(l.textContent, c); });
    hooks.length = 0;
    const nodes = [def.form(st, ui, ctx)].flat(Infinity).filter(Boolean);
    nodes.forEach((n) => {
      const l = n.querySelector && n.querySelector('.wi-lab'), c = n.querySelector && n.querySelector('.wi-seg'), o = l && c && old.get(l.textContent);
      if (o && o.children.length === c.children.length && [...o.children].every((b, i) => b.textContent === c.children[i].textContent)) {
        [...o.children].forEach((b, i) => b.classList.toggle('on', c.children[i].classList.contains('on')));
        c.replaceWith(o);
      }
    });
    for (let i = 0; i < nodes.length; i++) { const k = nodes[i].dataset && nodes[i].dataset.keep; if (k && keep.get(k)) nodes[i] = keep.get(k); }
    nodes.forEach((n) => {                                  // тяжёлые узлы (сетка иконок, поиск) не пересоздаём — окно не мигает
      const fresh = n.dataset && n.dataset.keep ? [n] : [...(n.querySelectorAll ? n.querySelectorAll('[data-keep]') : [])];
      fresh.forEach((f) => { const o = keep.get(f.dataset.keep); if (o && o !== f) f.replaceWith(o); });
    });
    formBox.querySelectorAll('.wi-icons .wi-icon').forEach((b) => b.classList.toggle('on', b.title === st.key));
    formBox.replaceChildren(...nodes);
  };
  const ui = { changed: update, hook: (fn) => hooks.push(fn),
    rebuild() { wiSmooth(closeFn && closeFn.box, paintForm); update(); } };

  function onPreview(text) {                                // код → форма (после паузы в наборе)
    clearTimeout(pvTimer);
    pvTimer = setTimeout(() => {
      if (built && text === built.code && manual === null) return;
      manual = text;
      let parsed = null;
      try { const f = def.scan ? def.scan(text, ctx) : []; parsed = f[0] ? f[0].st : null; } catch (e) { parsed = null; }
      if (!parsed) { paintNotes({ errors: [], needs: built && built.needs }, 'Конструктор не смог разобрать этот код — он будет вставлен как есть. Форму слева изменение не затронет.'); return; }
      delete parsed.icons;
      Object.assign(st, parsed);
      wiSmooth(closeFn && closeFn.box, paintForm);
      const res = def.build(st, ctx);
      built = Object.assign({}, res, { code: built ? built.code : res.code });
      paintNotes(res, 'Код изменён вручную — форма подстроилась под него.');
      hooks.forEach((fn) => fn(res));
    }, 260);
  }

  const insert = (close) => {
    const res = def.build(st, ctx);
    if (res.errors && res.errors.length) { toast(res.errors[0], 'error'); return; }
    const text = manual !== null && !res.apply ? manual : res.code;
    close();
    setTimeout(() => {
      let note = '';
      if (res.apply) { const r = res.apply(ed); note = r && r.note; }
      else if (range) {
        const len0 = ed.value.length;
        wiEnsureImports(ed, res.needs);
        const d = ed.value.length - len0, a = range[0] + d, b = range[1] + d;
        const body = res.kind === 'expr' ? text : wiIndent(text, edit.indent);
        ed.edit(a, b, body, a + body.length);
      } else { const r = wiPlace(ed, Object.assign({}, res, { code: text })); note = r && r.note; }
      ed.focus();
      toast((range ? 'заменено' : 'вставлено') + (note ? ' · ' + note : ''), 'ok');
    }, 240);
  };

  const pvHead = h('div', { class: 'wi-lab' }, 'Код — можно править прямо здесь, форма подстроится');
  closeFn = wiDrawer(edit ? def.title + ' · правка' : def.title, formBox, [pvHead, pv.el, notes], (close) => {
    insertBtn = h('button', { class: 'btn primary', onclick: () => insert(close) }, edit ? 'заменить в коде' : 'вставить в курсор');
    return [h('span', { class: 'wi-hint grow m-hide' }, 'Ctrl+Enter — ' + (edit ? 'заменить' : 'вставить')), h('button', { class: 'btn', onclick: close }, 'отмена'), insertBtn];
  }, () => { clearTimeout(pvTimer); });
  document.addEventListener('keydown', function onCE(e) {
    if (!document.querySelector('.wi-drawer')) { document.removeEventListener('keydown', onCE, true); return; }
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') { e.preventDefault(); if (insertBtn && !insertBtn.disabled) insertBtn.click(); }
  }, true);
  formBox.classList.add('first');
  paintForm(); update();
  setTimeout(() => formBox.classList.remove('first'), 400);
  requestAnimationFrame(() => pv.refresh());
}

/* ------------------------------------------------------------------ *
 * экраны: список модулей и сам редактор
 * ------------------------------------------------------------------ */

const WI_TEMPLATE = `import decorators
import engine

NAME = "Мой модуль"
MODULE_NAME = "my_module"
VERSION = "1.0"
AUTHOR = "me"
DESCRIPTION = "Что делает модуль"


@decorators.command("hello")
async def hello_cmd(event):
    """поздороваться"""
    await engine.answer(event, "Привет!")
`;

const wiWhen = (ts) => new Date(ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' });

/* заменить текст редактора на новую версию минимальной правкой (каретка и прокрутка остаются на месте) */
function wiSyncTo(ed, next) {
  const cur = ed.value;
  if (cur === next) return;
  let p = 0;
  const max = Math.min(cur.length, next.length);
  while (p < max && cur[p] === next[p]) p++;
  let q = 0;
  while (q < max - p && cur[cur.length - 1 - q] === next[next.length - 1 - q]) q++;
  const [ss, se] = ed.selection();
  const delta = next.length - cur.length, cutEnd = cur.length - q;
  const map = (x) => (x <= p ? x : x >= cutEnd ? x + delta : p);
  ed.edit(p, cutEnd, next.slice(p, next.length - q), map(ss), map(se));
  ed.setBaseline(next);
}

/* ---------- черновики: несохранённые правки хранятся у юзербота (core/webide.py: Drafts) ----------
   Через ~1,5 с после правки текст уходит на сервер; при открытии модуля — с любого адреса и устройства —
   страница предлагает его восстановить. FRX_DRAFT — состояние для плашки «сайт недоступен» (app.js). */
const FRX_DRAFT = { savedAt: 0, pending: 0, label: '', copy: null };

function wiDraftKeeper(opts) {                        // {key, base, getData, isDirty, alive, label, copyText}
  let timer = 0, has = false, base = opts.base || '', busy = false, again = false;
  FRX_DRAFT.label = opts.label || ''; FRX_DRAFT.copy = opts.copyText || null; FRX_DRAFT.pending = 0; FRX_DRAFT.savedAt = 0;
  async function flush(keepalive) {
    clearTimeout(timer); timer = 0;
    if (!opts.alive()) return;
    if (busy) { again = true; return; }
    busy = true;
    try {
      if (opts.isDirty()) {
        const body = JSON.stringify({ key: opts.key, data: opts.getData(), base });
        const r = await fetch('/api/webide/draft', { method: 'POST', keepalive: !!keepalive && body.length < 60000, headers: { 'X-FRX': '1', 'Content-Type': 'application/json' }, body });
        if (r.ok) { has = true; FRX_DRAFT.savedAt = Date.now(); FRX_DRAFT.pending = 0; }
      } else if (has) {
        const r = await fetch('/api/webide/draft/drop', { method: 'POST', headers: { 'X-FRX': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ key: opts.key }) });
        if (r.ok) { has = false; FRX_DRAFT.pending = 0; }
      } else FRX_DRAFT.pending = 0;
    } catch (e) { /* сеть пропала — правки остаются в FRX_DRAFT.pending, плашка подскажет скопировать */ }
    finally { busy = false; if (again) { again = false; flush(); } }
  }
  const onHide = () => { if (document.visibilityState === 'hidden' && timer) flush(true); };
  document.addEventListener('visibilitychange', onHide);
  window.addEventListener('pagehide', onHide);
  window.addEventListener('hashchange', () => { document.removeEventListener('visibilitychange', onHide); window.removeEventListener('pagehide', onHide); if (FRX_DRAFT.copy === opts.copyText) FRX_DRAFT.copy = null; }, { once: true });
  return {
    notify() { if (!FRX_DRAFT.pending) FRX_DRAFT.pending = Date.now(); clearTimeout(timer); timer = setTimeout(() => flush(false), 1500); },
    flush,
    async done(newBase) {                             // сохранили модуль — черновик больше не нужен
      clearTimeout(timer); timer = 0; base = newBase || base; FRX_DRAFT.pending = 0;
      try { await api('/webide/draft/drop', { key: opts.key }); } catch (e) { /* не страшно: при открытии сравним */ }
      has = false;
    },
    setBase(b) { base = b || ''; },
    async load() { try { return (await api('/webide/draft?key=' + encodeURIComponent(opts.key))).draft; } catch (e) { return null; } },
    adopt() { has = true; },
  };
}

function wiDraftBanner(draft, what, onRestore, onDiscard) {
  const d = new Date((draft.saved || 0) * 1000), p2 = (n) => String(n).padStart(2, '0');
  const today = new Date().toDateString() === d.toDateString();
  const whenText = (today ? 'сегодня' : `${p2(d.getDate())}.${p2(d.getMonth() + 1)}`) + ` в ${p2(d.getHours())}:${p2(d.getMinutes())}`;
  const box = h('div', { class: 'glass wi-draftbar', role: 'status' },
    h('div', { class: 'wi-draft-t' }, h('b', {}, 'Есть несохранённые правки'), ` (${whenText}) — ${what}. Их сохранил сайт, пока вы работали (в том числе с другого адреса или устройства).`,
      draft.stale ? h('div', { class: 'wi-draft-warn' }, 'Файл модуля с тех пор менялся: восстановленные правки заменят текущий текст в редакторе. Сравнить можно во вкладке «история».') : null),
    h('div', { class: 'wi-draft-a' },
      h('button', { class: 'btn small primary', type: 'button', onclick: () => { box.remove(); onRestore(); } }, 'восстановить'),
      h('button', { class: 'btn small', type: 'button', onclick: () => { box.remove(); onDiscard(); } }, 'отбросить')));
  return box;
}

/* Телефон: полоска символов над клавиатурой (Tab, скобки, двоеточие…) — их нет на основной раскладке телефона.
   Видна, пока в редакторе стоит курсор; кнопки не забирают фокус, клавиатура не прячется. */
function wiKeyBar(ed) {
  const KEYS = [['tab', '⇥', 'отступ'], ['untab', '⇤', 'убрать отступ'], ['(', '('], [')', ')'], [':', ':'], ['"', '"'], ["'", "'"], ['=', '='], ['.', '.'], [',', ','],
    ['[', '['], [']', ']'], ['{', '{'], ['}', '}'], ['_', '_'], ['#', '#'], ['comment', '//', 'закомментировать строки'], ['left', '←'], ['right', '→'], ['undo', '↶', 'отменить'], ['redo', '↷', 'повторить'], ['find', '⌕', 'найти']];
  const bar = h('div', { class: 'wi-keybar', role: 'toolbar', 'aria-label': 'символы' }, KEYS.map(([k, label, title]) => {
    const b = h('button', { type: 'button', class: 'wi-key' + (k.length > 1 ? ' fn' : ''), title: title || label, 'aria-label': title || label }, label);
    b.addEventListener('pointerdown', (e) => e.preventDefault());           // не уводить фокус из редактора
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => { if (k === 'find') ed.openFind(false); else ed.key(k); });
    return b;
  }));
  const vv = window.visualViewport;
  function place() {
    if (!vv) return;
    const kb = Math.max(0, window.innerHeight - (vv.height + vv.offsetTop));
    bar.style.transform = `translateY(${-kb}px)`;
  }
  let hideTimer = 0;
  ed.ta.addEventListener('focus', () => { if (!mIsPhone()) return; clearTimeout(hideTimer); if (!bar.isConnected) document.body.appendChild(bar); place(); bar.classList.add('on'); document.documentElement.classList.add('wi-kb'); });
  ed.ta.addEventListener('blur', () => { hideTimer = setTimeout(() => { bar.classList.remove('on'); document.documentElement.classList.remove('wi-kb'); }, 120); });
  if (vv) { vv.addEventListener('resize', place); vv.addEventListener('scroll', place); }
  window.addEventListener('hashchange', () => { bar.remove(); document.documentElement.classList.remove('wi-kb'); }, { once: true });
  return bar;
}

async function webideList(T) {
  const d = await api('/webide/modules');
  const mine = d.modules.filter((m) => !m.builtin), system = d.modules.filter((m) => m.builtin);
  const mk = (m) => row(
    nameSub(m.name, `${m.module} · v${m.version} · ${m.author}`, m.builtin ? 'встроенный' : (m.lab ? 'собран в Lab' : (m.stamp ? 'изменён в WebIDE' : ''))),
    m.history ? h('span', { class: 'val' }, `${m.history} ${plural(m.history, 'версия', 'версии', 'версий')} в истории`) : null,
    { click: () => { location.hash = (m.lab ? '#/lab/' : '#/webide/') + enc(m.module); } });
  const side = [h('button', { class: 'btn', title: 'визуальный конструктор модулей — без программирования', onclick: () => { location.hash = '#/lab'; } }, 'FRX WebIDE Lab'),
    h('button', { class: 'btn primary', onclick: () => { location.hash = '#/webide/__new__'; } }, 'новый модуль')];
  mount(T, shell('FRX WebIDE', { side },
    h('div', { class: 'faint', style: 'margin:-4px 0 18px 6px;max-width:680px' },
      'Редактор модулей FRX: подсветка кода, поиск и замена, история изменений и конструкции, которые собирают код из форм. Встроенные модули открываются только для просмотра.'),
    h('div', { class: 'block' }, h('div', { class: 'h2', style: 'padding-left:6px' }, 'ваши модули')),
    glass(mine.length ? mine.map(mk) : empty('своих модулей пока нет — создайте первый')),
    h('div', { class: 'block' }, h('div', { class: 'h2', style: 'padding-left:6px' }, 'встроенные')),
    glass(system.map(mk))));
}

async function webideEditor(T, param) {
  const isNew = param === '__new__';
  let labFlag = false;
  let moduleId = isNew ? null : param, original = WI_TEMPLATE, editable = true, builtin = false, stamp = null, histCount = 0, baseHash = '';
  if (!isNew) {
    const r = await api('/webide/source?module=' + enc(param));
    original = r.source; editable = r.editable; builtin = r.builtin; stamp = r.stamp; histCount = r.history; labFlag = !!r.lab; baseHash = r.hash || '';
  }
  let drafts = null;
  const protect = !isNew && editable;
  let assistNode = null, phoneBack = null;
  let iconsCache = null, lastCheck = 0, checkTimer = null, lastResult = null, tab = 'constructs';

  const status = h('div', { class: 'wi-status' });
  const pos = h('span', { class: 'wi-pos' });
  const dirtyTag = h('span', { class: 'tag wi-dirty', hidden: true }, 'не сохранено');
  const saveBtn = h('button', { class: 'btn primary', disabled: !editable, onclick: () => save() }, isNew ? 'создать модуль' : 'сохранить');
  const sbBtn = h('button', { class: 'btn wi-bar-btn', type: 'button', title: 'песочница: проверить модуль без Telegram (Ctrl+Enter)', onclick: () => { if (dock.isOpen()) dock.close(); else dock.open(); } }, 'sandbox');
  let dock = null;
  const dirty = () => editable && ed.value !== original;
  GUARD = () => !dirty() || confirm('Есть несохранённые изменения. Уйти без сохранения?');

  const ed = wiCreateEditor({
    value: original, readOnly: !editable, protect,
    onChange: () => { if (phoneBack) phoneBack(); if (drafts) drafts.notify(); refreshFound(); lastEdit = Date.now(); dirtyTag.hidden = !dirty(); clearTimeout(checkTimer); checkTimer = setTimeout(runCheck, 400); if (dock) dock.notifyChange(); },
    onSave: () => save(),
    onRun: () => dock && dock.run(false),
    onCursor: (c) => { refreshFound(); pos.textContent = `строка ${c.line}, столбец ${c.col}` + (c.sel ? ` · выделено ${c.sel}` : ''); },
    onProtected: (label) => toast(`${label} защищено — это поле менять нельзя`, 'error'),
    onInfo: (t) => toast(t, 'ok'),
  });

  /* Конструкция под курсором: блок подсвечивается, у его первой строки — «> править» (открывает конструктор с заполненными полями) */
  let foundTimer = 0;
  function refreshFound() {
    clearTimeout(foundTimer);
    foundTimer = setTimeout(() => {
      if (T !== TOKEN || !editable || !ed) return;
      const [a] = ed.selection();
      let hit = null;
      try { hit = wiFindAt(ed.value, a, iconsCache || {}); } catch (e) { hit = null; }
      if (!hit) { ed.setFound(null); return; }
      const v = ed.value;
      ed.setFound({ key: hit.def.id + ':' + wiLineOf(v, hit.m.a), l0: wiLineOf(v, hit.m.a), l1: wiLineOf(v, Math.max(hit.m.a, hit.m.b)), label: hit.def.title,
        onEdit: () => wiOpenConstruct(hit.def, ed, host, hit.m) });
    }, 120);
  }

  /* Пока человек печатает, синтаксическая ошибка — норма: подсветка строки появляется только после паузы в 2 с
     (и не пропадает от каждого нажатия); уже показанная подсветка просто обновляется по результату проверки. */
  let errTimer = 0, lastEdit = 0, markedLine = 0;
  function markError(line) {
    clearTimeout(errTimer);
    if (!line) { markedLine = 0; ed.setErrorLine(0); return; }
    const wait = 2000 - (Date.now() - lastEdit);
    if (markedLine || wait <= 0) { markedLine = line; ed.setErrorLine(line); return; }
    errTimer = setTimeout(() => { markedLine = line; ed.setErrorLine(line); }, wait);
  }

  async function runCheck() {
    const my = ++lastCheck;
    let r;
    try { r = await api('/webide/check', { source: ed.value, module: moduleId }); } catch (e) { return; }
    if (my !== lastCheck || T !== TOKEN) return;
    lastResult = r;
    const chips = ['NAME', 'MODULE_NAME', 'VERSION', 'AUTHOR', 'DESCRIPTION'].map((k) => h('span', { class: 'check' + (r.have.includes(k) ? ' ok' : '') }, k));
    const imps = ['engine', 'decorators'].map((k) => h('span', { class: 'check' + (r.imports.includes(k) ? ' ok' : '') }, 'import ' + k));
    markError(r.error ? r.error.line : 0);
    const locks = (r.locks || []).map((m) => h('span', { class: 'bad' }, m));
    status.replaceChildren(
      r.error ? h('button', { class: 'link bad', onclick: () => ed.gotoLine(r.error.line) }, `строка ${r.error.line}: ${r.error.msg}`)
        : h('span', { class: 'good' }, 'синтаксис в порядке'),
      ...locks, ...chips, ...imps);
    saveBtn.disabled = !editable || !!r.error || !!locks.length;
  }

  async function save() {
    if (!editable || saveBtn.disabled) return;
    await act(async () => {
      if (isNew) {
        const r = await api('/webide/create', { source: ed.value });
        original = r.source; GUARD = null;
        if (drafts) await drafts.done(r.hash);
        toast(`модуль «${r.name}» создан и загружен`, 'ok');
        location.hash = '#/webide/' + enc(r.module);
        return;
      }
      const r = await api('/webide/save', { module: moduleId, source: ed.value });
      if (r.unchanged) { toast('изменений нет', 'ok'); if (drafts) drafts.done(r.hash); return; }
      wiSyncTo(ed, r.source);
      original = r.source; stamp = r.stamp; histCount = r.history || histCount;
      if (drafts) drafts.done(r.hash);
      dirtyTag.hidden = true; renderTags(); renderSide();
      toast(`сохранено и перезагружено (v${r.version})`, 'ok');
      runCheck();
    });
  }

  /* ---------- боковая панель: конструкции и история ---------- */

  const host = {
    readOnly: !editable,
    locked: () => protect,
    icons: async () => {
      if (iconsCache) return iconsCache;
      try { iconsCache = (await api('/webide/visual')).icons || {}; } catch (e) { iconsCache = {}; }
      return iconsCache;
    },
  };

  const sideBox = h('div', { class: 'glass wi-side' });

  async function openVersion(item) {
    let r;
    try { r = await api(`/webide/version?module=${enc(moduleId)}&id=${enc(item.id)}`); } catch (e) { toast(e.message, 'error'); return; }
    const diff = r.diff.length
      ? h('div', { class: 'wi-diff' }, r.diff.map((d) => h('div', { class: 'wi-dl ' + ({ '+': 'add', '-': 'del', '@': 'hunk' }[d.t] || '') }, h('span', { class: 'm' }, d.t === ' ' ? '' : d.t), h('span', {}, d.text || ' '))))
      : h('div', { class: 'wi-note' }, 'Эта версия совпадает с текущим файлом.');
    wiDrawer(`версия от ${wiWhen(item.time)}`, [h('div', { class: 'wi-hint', style: 'margin-bottom:10px' },
      `Если вернуть эту версию: +${r.added} −${r.removed} строк. Текст попадёт в редактор — сохранить его нужно отдельно.`), diff], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'закрыть'),
      editable ? h('button', { class: 'btn primary', onclick: () => { close(); wiSyncTo(ed, r.source); ed.focus(); dirtyTag.hidden = !dirty(); toast('версия в редакторе — не забудьте сохранить', 'ok'); runCheck(); } }, 'вернуть в редактор') : null]);
  }

  async function renderSide() {
    const tabs = h('div', { class: 'wi-tabs' },
      h('button', { class: tab === 'constructs' ? 'on' : '', onclick: () => { tab = 'constructs'; renderSide(); } }, 'конструкции'),
      isNew ? null : h('button', { class: tab === 'history' ? 'on' : '', onclick: () => { tab = 'history'; renderSide(); } }, 'история' + (histCount ? ` · ${histCount}` : '')),
      typeof wiAssistPane === 'function' ? h('button', { class: tab === 'neuro' ? 'on' : '', title: 'нейросеть-помощник: изменить выделенный код, вставить код, спросить', onclick: () => { tab = 'neuro'; renderSide(); } }, 'neuro') : null);
    let body;
    if (tab === 'neuro' && typeof wiAssistPane === 'function') {
      sideBox.replaceChildren(tabs, h('div', { class: 'wi-sidebody' }, assistNode || (assistNode = wiAssistPane(ed, { filename: () => (moduleId || 'module') + '.py', locked: () => protect }))));
      return;
    }
    if (tab === 'history' && !isNew) {
      let items = [];
      try { items = (await api('/webide/history?module=' + enc(moduleId))).items; } catch (e) { /* пусто */ }
      body = items.length
        ? items.map((it) => h('button', { class: 'snip', onclick: () => openVersion(it) }, h('div', { class: 't' }, wiWhen(it.time)), h('div', { class: 'd' }, (it.note || 'версия') + ' · ' + fmtSize(it.size))))
        : h('div', { class: 'wi-note', style: 'margin:14px 0' }, 'История пуста: версии появятся после первого сохранения в WebIDE.');
    } else {
      body = [
        !editable ? h('div', { class: 'wi-note', style: 'margin:14px 0' }, 'Встроенный модуль открыт только для просмотра.') : null,
        ...WI_CONSTRUCTS.map((c) => h('button', { class: 'snip', disabled: !editable, onclick: () => wiOpenConstruct(c, ed, host) },
          h('div', { class: 't' }, c.title), h('div', { class: 'd' }, c.desc))),
      ];
    }
    if (T !== TOKEN) return;
    sideBox.replaceChildren(tabs, h('div', { class: 'wi-sidebody' }, body));
  }

  /* ---------- сборка страницы ---------- */

  dock = sbCreateDock({
    key: isNew ? '__new__' : moduleId, getSource: () => ed.value, alive: () => T === TOKEN,
    onGoto: (line, mark) => { if (line > 0) { ed.gotoLine(line); if (mark) { markedLine = line; ed.setErrorLine(line); } } },
    onToggle: (on) => { sbBtn.classList.toggle('on', on); if (on && typeof taskAdd === 'function') taskAdd({ key: 'sb:' + (isNew ? '__new__' : moduleId), name: 'Sandbox', detail: (isNew ? 'новый модуль' : moduleId) + ' · песочница', hash: location.hash }); if (on) setTimeout(() => dock.el.scrollIntoView({ behavior: 'smooth', block: 'nearest' }), 60); },
  });

  const tagsBox = h('div', { class: 'wi-tags' });
  function renderTags() {
    const t = [];
    if (builtin) t.push(h('span', { class: 'tag' }, 'встроенный · только просмотр'));
    if (protect) t.push(h('span', { class: 'tag', title: 'VERSION и DESCRIPTION менять можно' }, 'NAME, MODULE_NAME и AUTHOR защищены'));
    if (stamp) t.push(h('span', { class: 'tag' }, stamp.lab ? 'собран в Lab' : (stamp.edited ? 'изменён в WebIDE' : 'создан в WebIDE')));
    t.push(dirtyTag);
    tagsBox.replaceChildren(...t);
  }
  renderTags();

  const hints = editable
    ? 'ctrl+enter запустить в sandbox · ctrl+s сохранить · ctrl+f поиск · ctrl+h замена · ctrl+g к строке · ctrl+/ комментарий · alt+↑↓ строка выше/ниже · tab отступ · при ошибке загрузки прежняя версия возвращается сама'
    : 'ctrl+enter — запустить в sandbox · ctrl+f — поиск по коду · ctrl+g — к строке';

  // телефон: редактор и боковая панель — по очереди, переключателем сверху; над клавиатурой — полоска символов
  const draftSlot = h('div', { class: 'wi-draftslot' });
  const layout = h('div', { class: 'wi-layout' },
    h('div', { class: 'wi-main' }, draftSlot, tagsBox, ed.el, h('div', { class: 'wi-bar' }, status, pos), h('div', { class: 'faint wi-hints m-hide' }, hints)),
    sideBox);
  const mseg = mSeg([['code', 'код'], ['constructs', 'конструкции'], ...(isNew ? [] : [['history', 'история']]), ...(typeof wiAssistPane === 'function' ? [['neuro', 'neuro']] : [])], 'code', (id) => setView(id));
  mseg.el.classList.add('m-only');
  function setView(id) {
    mseg.set(id);
    layout.classList.toggle('mv-side', id !== 'code');
    if (id === 'code') { ed.refresh(); return; }
    tab = id; renderSide();
  }
  phoneBack = () => { if (mIsPhone() && layout.classList.contains('mv-side')) setView('code'); };
  wiKeyBar(ed);

  const node = shell(isNew ? 'новый модуль' : moduleId, { back: '#/webide', backLabel: 'webide', side: [...(labFlag ? [h('button', { class: 'btn small', type: 'button', title: 'этот модуль собран в конструкторе — открыть проект', onclick: () => { if (GUARD && !GUARD()) return; location.hash = '#/lab/' + enc(moduleId); } }, 'открыть в Lab')] : []), sbBtn, ...(editable ? [saveBtn] : [])] },
    mseg.el, layout, dock.el);
  node.classList.add('wide');
  mount(T, node);
  renderSide();
  ed.refresh();
  runCheck();

  if (editable) {
    const key = 'code:' + (isNew ? '__new__' : moduleId);
    drafts = wiDraftKeeper({ key, base: baseHash, alive: () => T === TOKEN, isDirty: () => dirty(), getData: () => ({ text: ed.value }),
      label: 'код модуля ' + (isNew ? '(новый)' : moduleId), copyText: () => ed.value });
    const d = await drafts.load();
    if (T !== TOKEN) return;
    const text = d && d.data && typeof d.data.text === 'string' ? d.data.text : null;
    if (text !== null && text !== ed.value && text !== original) {
      drafts.adopt();
      draftSlot.replaceChildren(wiDraftBanner(d, isNew ? 'новый модуль' : 'код этого модуля', () => {
        wiSyncTo(ed, text); ed.focus(); dirtyTag.hidden = !dirty(); runCheck(); toast('правки восстановлены — не забудьте сохранить', 'ok');
      }, () => { drafts.done(); toast('черновик отброшен', 'ok'); }));
    } else if (d) drafts.done();
  }
}
