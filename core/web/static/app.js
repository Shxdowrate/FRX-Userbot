/* FRX web — роутер, меню и основные страницы сайта (вкладки botchat, webide, neuro, доступ и др. — в своих файлах). Ванильный JS, без зависимостей.
 * Любые данные попадают в DOM только через textContent / createElement (HTML модулей не исполняется). */
'use strict';

/* ------------------------------------------------------------------ *
 * утилиты
 * ------------------------------------------------------------------ */

/* null / undefined / false среди дочерних узлов не должны превращаться в текст «null» (replaceChildren, append, prepend) */
['replaceChildren', 'append', 'prepend'].forEach((m) => {
  const orig = Element.prototype[m];
  if (typeof orig !== 'function') return;
  Element.prototype[m] = function (...nodes) { return orig.apply(this, nodes.filter((x) => x !== null && x !== undefined && x !== false)); };
});

/* подчёркивания растут из места, где курсор вошёл в элемент (и уходят к месту выхода), а не слева направо */
const UL_SEL = '.btn, .link, .chip, .tab, .top .back, .home .fact, .row.click, .field';
function ulPoint(e, entering) {
  const t = e.target.closest ? e.target.closest(UL_SEL) : null;
  if (!t) return;
  if (entering !== null && e.relatedTarget && t.contains(e.relatedTarget)) return;       // переход между потомками — не вход/выход
  const ref = t.classList.contains('click') ? (t.querySelector('.name') || t) : t;
  const r = ref.getBoundingClientRect();
  const x = r.width > 0 ? Math.min(100, Math.max(0, (e.clientX - r.left) / r.width * 100)) : 50;
  t.style.setProperty('--ux', x.toFixed(1) + '%');
}
document.addEventListener('pointerover', (e) => ulPoint(e, true), true);
document.addEventListener('pointerout', (e) => ulPoint(e, false), true);
document.addEventListener('pointerdown', (e) => ulPoint(e, null), true);

const $ = (sel, root = document) => root.querySelector(sel);
const enc = encodeURIComponent;

function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className = v;
    else if (k === 'value' || k === 'checked' || k === 'disabled' || k === 'readOnly') el[k] = v;
    else el.setAttribute(k, v === true ? '' : v);
  }
  const add = (kid) => {
    if (kid === null || kid === undefined || kid === false) return;
    if (Array.isArray(kid)) kid.forEach(add);
    else el.appendChild(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  };
  kids.forEach(add);
  return el;
}

class SleepError extends Error {}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let SLEEP = false, SLEEP_TIMER = null, LAST_TARGET = null;

/* юзербот перезагружается, а сайт живёт (его порт держит keeper). Пока он «спит» — над страницей
 * плашка, а любая попытка что-то сделать подсвечивает нажатую кнопку красным. */
function setSleep(on) {
  if (on === SLEEP) return;
  SLEEP = on;
  document.body.classList.toggle('asleep', on);
  $('#sleep').classList.toggle('show', on);
  clearTimeout(SLEEP_TIMER);
  if (on) {
    let fails = 0;
    const tick = async () => {
      try {
        const r = await fetch('/api/nav', { headers: { 'X-FRX': '1' } });
        fails = 0;
        if (r.ok) { setSleep(false); toast('юзербот снова на связи', 'ok'); wake(); return; }
        if (r.status === 401) { location.replace('/'); return; }
      } catch (e) {
        if (++fails > 45) { showLost(); return; }       // ~1 минута тишины — сайт выключился совсем
      }
      SLEEP_TIMER = setTimeout(tick, 1300);
    };
    SLEEP_TIMER = setTimeout(tick, 900);
  }
}

/* Связь с сайтом пропала: он выключился (простой, команда) или сменился публичный адрес — со старого адреса
   страница уже ничего не сохранит. Говорим, где взять новую ссылку, и что с несохранёнными правками редактора. */
function showLost() {
  const off = $('#off');
  if (off.classList.contains('show')) return;
  const D = typeof FRX_DRAFT === 'object' ? FRX_DRAFT : null;
  const tm = (t) => { const d = new Date(t), p2 = (n) => String(n).padStart(2, '0'); return `${p2(d.getHours())}:${p2(d.getMinutes())}:${p2(d.getSeconds())}`; };
  const parts = [h('div', { class: 'big' }, 'сайт недоступен'),
    h('div', { class: 'muted off-why' }, 'Сайт выключился (простой или команда) либо сменился публичный адрес. Новая ссылка — в ЛС от бота и на кнопке «Сайт» в меню чата с ботом; или напишите ', h('code', {}, '.web'), ' в Telegram.')];
  if (D && D.label) {
    if (D.pending && D.copy) {
      const copyBtn = h('button', { class: 'btn small primary', type: 'button', onclick: async () => {
        try { await navigator.clipboard.writeText(D.copy()); copyBtn.textContent = 'скопировано'; }
        catch (e) { copyBtn.textContent = 'не вышло — выделите код вручную'; off.classList.remove('show'); }
      } }, 'скопировать код');
      parts.push(h('div', { class: 'off-draft bad' }, `Последние правки (${D.label}) не успели сохраниться` + (D.savedAt ? ` — черновик есть на ${tm(D.savedAt)}.` : '.'), h('div', { class: 'off-act' }, copyBtn)));
    } else if (D.pending) {
      parts.push(h('div', { class: 'off-draft bad' }, `Последние правки (${D.label}) не успели сохраниться` + (D.savedAt ? ` — черновик есть на ${tm(D.savedAt)}.` : '.')));
    } else if (D.savedAt) {
      parts.push(h('div', { class: 'off-draft' }, `Черновик (${D.label}) сохранён в ${tm(D.savedAt)}: откройте модуль по новой ссылке — сайт предложит восстановить правки.`));
    }
  }
  parts.push(h('div', { class: 'off-act' }, h('button', { class: 'btn small', type: 'button', onclick: () => location.reload() }, 'обновить страницу')));
  off.replaceChildren(...parts);
  off.classList.add('show');
}

function wake() {
  refreshNav();
  if (!/^(web)?ide\//.test(curPath())) route();                // редактор не трогаем: там могут быть несохранённые правки
}

async function api(path, body) {
  let r, attempt = 0;
  for (;;) {
    try {
      r = await fetch('/api' + path, {
        method: body === undefined ? 'GET' : 'POST',
        headers: { 'X-FRX': '1', 'Content-Type': 'application/json' },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      break;
    } catch (e) {
      if (attempt++ < 5) { await sleep(250 + attempt * 250); continue; }   // короткий обрыв при передаче порта — не повод гасить сайт
      if (SLEEP) throw new SleepError('юзербот спит');
      showLost();
      throw new Error('сайт выключен');
    }
  }
  let data = {};
  try { data = await r.json(); } catch (e) { /* пустой ответ */ }
  if (r.status === 503 && data.sleeping) { setSleep(true); throw new SleepError('юзербот спит — действия появятся после запуска'); }
  if (r.status === 401 && data.auth) { location.replace('/'); throw new Error('нужно войти'); }   // сессия истекла — страница входа
  if (!r.ok) throw new Error(data.error || ('ошибка ' + r.status));
  return data;
}

function toast(text, kind) {
  const box = $('#toast');
  if ([...box.children].some((t) => t.textContent === text && !t.classList.contains('out'))) return;
  const el = h('div', { class: 'toast ' + (kind || '') }, text);
  box.appendChild(el);
  setTimeout(() => { el.classList.add('out'); setTimeout(() => el.remove(), 280); }, kind === 'error' ? 6000 : 3200);
}

function deny() {                                         // красная «встряска» нажатой кнопки
  const t = LAST_TARGET;
  if (!t || !t.isConnected) return;
  t.classList.remove('deny'); void t.offsetWidth; t.classList.add('deny');
  setTimeout(() => t.classList.remove('deny'), 950);
}

async function act(fn) {
  try { return await fn(); }
  catch (e) {
    if (e instanceof SleepError) deny();
    toast(e.message, 'error');
    return undefined;
  }
}

document.addEventListener('pointerdown', (e) => { LAST_TARGET = e.target.closest ? e.target.closest('button, .btn, .chip, .toggle, .row.click') : null; }, true);
document.addEventListener('keydown', (e) => { if (e.key === 'Enter' && document.activeElement && document.activeElement !== document.body) LAST_TARGET = document.activeElement; }, true);

/* ---------- анимации текста, размера и «блика» ---------- */

function commonPrefix(a, b) { let i = 0; const n = Math.min(a.length, b.length); while (i < n && a[i] === b[i]) i++; return i; }

/* Эффект печати с плавным входом: символы root, начиная с индекса `from`, по очереди проявляются (opacity + размытие). */
function revealChars(root, from) {
  if (REDUCED.matches || !root) return;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const nodes = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) nodes.push(n);
  const total = nodes.reduce((acc, t) => acc + t.data.length, 0) - from;
  if (total <= 0) return;
  const step = Math.min(24, 700 / total), cap = 600;
  let idx = 0, shown = 0;
  for (const t of nodes) {
    const len = t.data.length;
    if (idx + len <= from) { idx += len; continue; }
    const start = Math.max(0, from - idx);
    const frag = document.createDocumentFragment();
    if (start) frag.appendChild(document.createTextNode(t.data.slice(0, start)));
    for (let i = start; i < len; i++) {
      if (shown >= cap) { frag.appendChild(document.createTextNode(t.data.slice(i))); break; }
      const sp = document.createElement('span');
      sp.className = 'ch'; sp.textContent = t.data[i]; sp.style.animationDelay = (shown * step).toFixed(0) + 'ms';
      frag.appendChild(sp); shown++;
    }
    t.replaceWith(frag); idx += len;
  }
  setTimeout(() => {
    root.querySelectorAll('span.ch').forEach((x) => x.replaceWith(document.createTextNode(x.textContent)));
    root.normalize();
  }, shown * step + 900);
}

/* Заменить текст элемента: общее начало остаётся, остальное «допечатывается». */
function typeText(el, text) {
  const old = el.textContent;
  if (old === text) return;
  el.textContent = text;
  revealChars(el, commonPrefix(old, text));
}

/* Плавно сменить размер блока при изменении содержимого (mutate меняет DOM). */
function morph(el, mutate) {
  if (REDUCED.matches) { mutate(); return; }
  const h0 = el.getBoundingClientRect().height;
  mutate();
  const h1 = el.getBoundingClientRect().height;
  if (Math.abs(h1 - h0) < 2) return;
  el.style.overflow = 'hidden';
  const a = el.animate([{ height: h0 + 'px' }, { height: h1 + 'px' }], { duration: 400, easing: EASE });
  a.onfinish = a.oncancel = () => { el.style.overflow = ''; };
}

function growIn(el, extra) {
  if (REDUCED.matches) return;
  const h1 = el.getBoundingClientRect().height;
  el.style.overflow = 'hidden';
  const a = el.animate([{ height: '0px', opacity: 0 }, { height: h1 + 'px', opacity: 1 }], { duration: 420, easing: EASE });
  a.onfinish = a.oncancel = () => { el.style.overflow = ''; };
  if (extra) extra(420);
}

function collapseOut(el, done) {
  el.classList.add('leaving-row');
  if (REDUCED.matches) { el.remove(); if (done) done(); return; }
  const h0 = el.getBoundingClientRect().height;
  el.style.overflow = 'hidden'; el.style.pointerEvents = 'none';
  const a = el.animate([
    { height: h0 + 'px', opacity: 1 },
    { height: '0px', opacity: 0, paddingTop: '0px', paddingBottom: '0px', marginTop: '0px', borderTopWidth: '0px' },
  ], { duration: 340, easing: EASE, fill: 'forwards' });
  a.onfinish = () => { el.remove(); if (done) done(); };
}

/* «Свет по зеркальной поверхности»: блик пробегает по всему блоку. */
function sheen(el) {
  if (REDUCED.matches || !el) return;
  el.classList.add('has-sheen');
  const s = h('span', { class: 'sheen' });
  el.appendChild(s);
  s.addEventListener('animationend', () => s.remove());
  setTimeout(() => s.remove(), 1600);
}

/* Блик по нажатой кнопке (для всех .btn / .chip). Выбор (chip.on) остаётся подсвеченным. */
document.addEventListener('click', (e) => {
  const b = e.target.closest ? e.target.closest('.btn, .chip') : null;
  if (!b || b.disabled || REDUCED.matches) return;
  const g = h('span', { class: 'glint' });
  b.appendChild(g);
  g.addEventListener('animationend', () => g.remove());
  setTimeout(() => g.remove(), 1200);
}, true);

function countUp(el, to) {
  if (REDUCED.matches || !(to > 0)) { el.textContent = to; return; }
  const t0 = performance.now(), dur = 700 + Math.min(500, to * 6);
  const tick = (now) => {
    const k = Math.min(1, (now - t0) / dur), v = 1 - Math.pow(1 - k, 3);
    el.textContent = Math.round(to * v);
    if (k < 1 && el.isConnected) requestAnimationFrame(tick); else el.textContent = to;
  };
  el.textContent = 0; requestAnimationFrame(tick);
}

let lastPing = 0;
['pointerdown', 'keydown', 'wheel'].forEach((ev) =>
  window.addEventListener(ev, () => {
    const now = Date.now();
    if (now - lastPing > 20000) { lastPing = now; fetch('/api/ping', { method: 'POST', headers: { 'X-FRX': '1' } }).catch(() => {}); }
  }, { passive: true }));

function confirmBtn(label, fn, cls) {
  let armed = false, timer = null;
  const b = h('button', { class: cls || 'btn small danger' }, label);
  b.addEventListener('click', () => {
    if (!armed) {
      armed = true; b.textContent = 'точно?';
      timer = setTimeout(() => { armed = false; b.textContent = label; }, 3000);
    } else { clearTimeout(timer); armed = false; b.textContent = label; fn(); }
  });
  return b;
}

function modal(title, body, actions) {
  const veil = h('div', { class: 'veil' });
  const close = () => { veil.classList.add('out'); setTimeout(() => veil.remove(), 220); };
  veil.appendChild(h('div', { class: 'glass' }, h('h2', {}, title), body,
    h('div', { class: 'actions' }, actions(close))));
  veil.addEventListener('mousedown', (e) => { if (e.target === veil) close(); });
  document.body.appendChild(veil);
  return close;
}

function toggle(on, onchange) {
  const t = h('button', { class: 'toggle' + (on ? ' on' : ''), type: 'button', role: 'switch', 'aria-checked': String(!!on) });
  t.addEventListener('click', () => {
    const v = !t.classList.contains('on');
    t.classList.toggle('on', v); t.setAttribute('aria-checked', String(v));
    onchange(v);
  });
  return t;
}

const dots = (n, total) => h('span', { class: 'dots' }, Array.from({ length: total }, (_, i) => h('i', { class: i < n ? 'on' : '' })));
const plural = (n, a, b, c) => { const m = Math.abs(n) % 100, d = m % 10; return (m > 10 && m < 20) ? c : d === 1 ? a : (d > 1 && d < 5) ? b : c; };

/* ------------------------------------------------------------------ *
 * каркас и роутер
 * ------------------------------------------------------------------ */

let TOKEN = 0;
let GUARD = null;          // () => true, если можно уходить со страницы (IDE с несохранёнными правками)
let lastHash = location.hash;
let mountedHash = null;

/* --- направление анимации: вперёд — сдвиг влево, назад — вправо --- */
const REDUCED = window.matchMedia('(prefers-reduced-motion: reduce)');
const EASE = 'cubic-bezier(.22, .8, .24, 1)';
let NAVDIR = null;         // 'forward' | 'back' | null — выставляется на hashchange, забирается в route()
let MOUNT_DIR = null;
let pendingBack = false;   // клик по «‹ назад»
let navStack = [location.hash];
const curPath = () => location.hash.replace(/^#\/?/, '');

function tabIndex(hash) {
  const p = hash.replace(/^#\/?/, '');
  return NAV.tabs.findIndex((t) => t.on(p));
}

function pickDirection(from, to) {
  if (pendingBack) {                                   // явный «назад»
    pendingBack = false;
    navStack.pop();
    if (navStack[navStack.length - 1] !== to) navStack.push(to);
    return 'back';
  }
  const a = tabIndex(from), b = tabIndex(to);
  if (a >= 0 && b >= 0 && a !== b) {                   // переключение пунктов верхнего меню — по порядку слева направо
    navStack = [to];
    return b > a ? 'forward' : 'back';
  }
  if (navStack.length > 1 && navStack[navStack.length - 2] === to) {   // кнопка «назад» браузера
    navStack.pop();
    return 'back';
  }
  if (navStack[navStack.length - 1] !== to) navStack.push(to);
  return 'forward';
}

function mount(T, node) {
  if (T !== TOKEN) return false;
  loadEnd(T);
  const app = $('#app');
  const same = mountedHash === location.hash;         // перерисовка той же страницы — без анимации и без прокрутки
  const old = [...app.children].find((n) => !n.classList.contains('leaving'));
  app.querySelectorAll('.leaving').forEach((n) => n.remove());
  if (same || REDUCED.matches) {
    app.replaceChildren(node);
    if (!same) window.scrollTo(0, 0);
  } else {
    const sy = window.scrollY;
    node.classList.add('enter');
    if (old && MOUNT_DIR) {
      // смена страницы — глитч: старая «срывается» срезами, новая собирается из них, по экрану пробегает красный разрез
      old.classList.add('leaving');
      old.style.top = (-sy) + 'px';
      const out = window.FX ? FX.glitchOut(old, { duration: 200 }) : old.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, fill: 'forwards' });
      if (out) out.onfinish = () => old.remove(); else old.remove();
      app.appendChild(node);
      window.scrollTo(0, 0);
      if (window.FX) { FX.sweep(); FX.glitchIn(node, { duration: 520, delay: 60 }); }
    } else {
      app.replaceChildren(node);
      window.scrollTo(0, 0);
      if (window.FX) FX.glitchIn(node, { duration: 560 });
    }
    [...node.querySelectorAll('.glass > .row, .glass > .rows > .row, .u-body > *, .home .stat, .home .cl')].slice(0, 16)
      .forEach((el, i) => el.style.setProperty('--i', i));
    setTimeout(() => node.classList.remove('enter'), 1300);
  }
  mountedHash = location.hash;
  return true;
}

function shell(title, opts, ...content) {
  opts = opts || {};
  return h('div', { class: 'shell' },
    h('div', { class: 'top' },
      opts.back ? [h('a', { class: 'back', href: opts.back }, '‹ ' + (opts.backLabel || 'назад')), h('div', { class: 'sep' })] : null,
      h('h1', {}, title),
      h('div', { class: 'grow' }),
      opts.side ? h('div', { class: 'side' }, opts.side) : null),
    content);
}

const glass = (...kids) => h('div', { class: 'glass' }, kids);
const empty = (text) => h('div', { class: 'empty' }, text);

function row(main, end, opts) {
  opts = opts || {};
  const r = h('div', { class: 'row' + (opts.click ? ' click' : '') + (opts.wide ? ' wide' : '') + (opts.cfg ? ' cfg' : '') },
    h('div', { class: 'main' }, main), end ? h('div', { class: 'end' }, end) : null);
  if (opts.click) r.addEventListener('click', (e) => { if (!e.target.closest('button, input, select, textarea, a')) opts.click(); });
  return r;
}
const nameSub = (name, sub, tag) => [h('div', { class: 'name' }, name, tag ? h('span', { class: 'tag' }, tag) : null), sub ? h('div', { class: 'sub' }, sub) : null];

const ROUTES = [
  [/^$/, home],
  [/^config$/, configList],
  [/^config\/([^/]+)$/, configModule],
  [/^modules$/, modulesView],
  [/^modules\/([^/]+)$/, moduleDetail],
  [/^modules\/([^/]+)\/emulate$/, emulateModule],
  [/^files$/, filesView],                                    // panel.js
  [/^neuro$/, neuroView],                                    // neuro.js
  [/^webide$/, webideList],                                  // webide.js
  [/^lab$/, (T) => labEditor(T, '__new__')],                 // lab.js (FRX WebIDE Lab)
  [/^lab\/([^/]+)$/, labEditor],
  [/^webide\/([^/]+)$/, webideEditor],
  [/^ide\/([^/]+)$/, async (T, id) => { location.replace('#/webide/' + enc(id)); }],     // старые ссылки на редактор
  [/^accounts$/, accountsView],
  [/^access$/, accessView],                                 // доступ других людей и классы: access.js (алиасы — внизу «конфигов»)
  [/^access\/user\/([^/]+)$/, accessUser],
  [/^access\/class\/([^/]+)$/, accessClass],
  [/^botchat$/, botchatView],                               // botchat.js
  [/^page\/([^/]+)\/(\d+)$/, pageView],
  [/^emulate(\/.*)?$/, async () => { location.replace('#/modules'); }],     // Emulate переехал в раздел «модули»
];

/* Индикатор загрузки страницы: огненная полоска под шапкой (через 120 мс, чтобы не мигать на быстрых переходах)
   и плашка «загрузка…», если ждать дольше 700 мс. Снимается, когда новая страница показана. */
const LOADER = { T: 0, t1: 0, t2: 0, bar: null, pill: null };
function loadStart(T) {
  LOADER.T = T;
  clearTimeout(LOADER.t1); clearTimeout(LOADER.t2);
  if (!LOADER.bar) {
    LOADER.bar = h('div', { class: 'loadbar', 'aria-hidden': 'true' }, h('i'));
    LOADER.pill = h('div', { class: 'loadpill', role: 'status', 'aria-live': 'polite' }, h('span', { class: 'spin' }), 'загрузка…');
    document.body.append(LOADER.bar, LOADER.pill);
  }
  LOADER.t1 = setTimeout(() => { if (LOADER.T === T) document.documentElement.classList.add('is-loading'); }, 120);
  LOADER.t2 = setTimeout(() => { if (LOADER.T === T) document.documentElement.classList.add('is-loading-long'); }, 700);
}
function loadEnd(T) {
  if (T !== undefined && T !== LOADER.T) return;
  clearTimeout(LOADER.t1); clearTimeout(LOADER.t2);
  document.documentElement.classList.remove('is-loading', 'is-loading-long');
}

async function route() {
  const T = ++TOKEN;
  loadStart(T);
  GUARD = null;
  MOUNT_DIR = NAVDIR; NAVDIR = null;
  const path = curPath();
  updateNavActive(path);
  if (typeof taskTrack === 'function') taskTrack(path);          // панель активных задач (panel.js)
  for (const [re, fn] of ROUTES) {
    const m = path.match(re);
    if (m) {
      try { await fn(T, ...m.slice(1).map(decodeURIComponent)); }
      catch (e) {
        if (e instanceof SleepError) mount(T, shell('юзербот спит', {}, glass(empty('сайт работает, а юзербот сейчас перезапускается — страница обновится сама'))));
        else mount(T, shell('ошибка', {}, glass(empty(e.message))));
      }
      finally { loadEnd(T); }
      return;
    }
  }
  location.hash = '#/';
}

let restoring = false;
window.addEventListener('hashchange', () => {
  if (restoring) { restoring = false; return; }
  if (GUARD && !GUARD()) { restoring = true; location.hash = lastHash; return; }
  NAVDIR = pickDirection(lastHash, location.hash);
  lastHash = location.hash;
  route();
});
document.addEventListener('click', (e) => { if (e.target.closest && e.target.closest('a.back')) pendingBack = true; }, true);
window.addEventListener('beforeunload', (e) => { if (GUARD && !GUARD()) { e.preventDefault(); e.returnValue = ''; } });

/* ------------------------------------------------------------------ *
 * шапка-меню: выбор раздела (горизонтальный, прокручивается или разворачивается)
 * ------------------------------------------------------------------ */

const NAV = { el: null, state: null, tabs: [], open: false, key: '' };

function buildTabs(s) {
  const tabs = [
    { label: 'конфиги', href: '#/config', on: (p) => /^config(\/|$)/.test(p) },
    { label: 'модули', href: '#/modules', on: (p) => /^modules(\/|$)/.test(p) },
    { label: 'files', href: '#/files', on: (p) => /^files(\/|$)/.test(p) },                       // 3
    { label: 'neuro', href: '#/neuro', on: (p) => /^neuro(\/|$)/.test(p) },                       // 4
    { label: 'webide', href: '#/webide', on: (p) => /^(webide|ide|lab)(\/|$)/.test(p) },          // FRX WebIDE
    { label: 'аккаунты', href: '#/accounts', on: (p) => p === 'accounts' },
    { label: 'доступ', href: '#/access', on: (p) => /^access(\/|$)/.test(p) },
  ];
  if (s.botchat !== false) tabs.push({ label: 'botchat', href: '#/botchat', on: (p) => p === 'botchat' });
  (s.pages || []).forEach((pg) => {
    const href = `#/page/${enc(pg.module)}/${pg.index}`;
    tabs.push({ label: ((pg.icon ? pg.icon + ' ' : '') + pg.title).toLowerCase(), href, on: (p) => '#/' + p === href });
  });
  return tabs;
}

function setNav(state) { NAV.state = state; renderNav(); }

async function refreshNav() {
  try { setNav(await api('/nav')); } catch (e) { /* сайт выключен — оверлей уже показан */ }
}

function renderNav() {
  const s = NAV.state;
  if (!s) return;
  const tabs = buildTabs(s);
  const key = JSON.stringify(tabs.map((t) => [t.label, t.href])) + '|' + s.version;
  NAV.tabs = tabs;
  if (key === NAV.key) { updateNavActive(curPath()); return; }
  NAV.key = key;

  const tabsEl = h('div', { class: 'tabs', role: 'tablist' }, tabs.map((t) => h('a', { class: 'tab', href: t.href, role: 'tab' }, t.label)));
  const left = h('button', { class: 'arrow', type: 'button', 'aria-label': 'влево', onclick: () => tabsEl.scrollBy({ left: -tabsEl.clientWidth * 0.7, behavior: 'smooth' }) }, '‹');
  const right = h('button', { class: 'arrow', type: 'button', 'aria-label': 'вправо', onclick: () => tabsEl.scrollBy({ left: tabsEl.clientWidth * 0.7, behavior: 'smooth' }) }, '›');
  const more = h('button', { class: 'link more', type: 'button', onclick: toggleNav }, 'все');
  const wrap = h('div', { class: 'tabs-wrap' }, left, tabsEl, right);
  const off = h('button', { class: 'link', type: 'button', onclick: () => act(async () => { await api('/shutdown', {}); $('#off').classList.add('show'); }) }, 'выключить');
  const logout = h('button', { class: 'link', type: 'button', title: 'выйти из сайта на этом устройстве', onclick: () => act(async () => { await api('/auth/logout', {}); location.replace('/'); }) }, 'выйти');
  Object.assign(NAV, { tabsEl, left, right, more, wrap });
  NAV.el.classList.toggle('open', NAV.open);
  NAV.el.replaceChildren(h('div', { class: 'nav-in' },
    h('a', { class: 'brand', href: '#/', onclick: (e) => { if (mobileNav()) { e.preventDefault(); openDrawer(); } } },
      'frx', h('small', {}, s.version), h('span', { class: 'burger', 'aria-hidden': 'true' }, h('i'), h('i'), h('i'))),
    wrap, h('div', { class: 'nav-end' }, more, window.D3 ? D3.button() : null, logout, off)));

  buildDrawer(s, tabs);
  tabsEl.addEventListener('scroll', navOverflow, { passive: true });
  tabsEl.addEventListener('wheel', (e) => {            // колесо над меню листает его вбок
    if (NAV.open || tabsEl.scrollWidth <= tabsEl.clientWidth + 2 || Math.abs(e.deltaY) < Math.abs(e.deltaX)) return;
    e.preventDefault(); tabsEl.scrollLeft += e.deltaY;
  }, { passive: false });
  if (window.ResizeObserver) new ResizeObserver(navOverflow).observe(tabsEl);
  updateNavActive(curPath());
  navOverflow();
}

function navOverflow() {
  const { tabsEl, left, right, more } = NAV;
  if (!tabsEl) return;
  const over = tabsEl.scrollWidth > tabsEl.clientWidth + 2;
  const canL = tabsEl.scrollLeft > 2, canR = tabsEl.scrollLeft + tabsEl.clientWidth < tabsEl.scrollWidth - 2;
  tabsEl.classList.toggle('can-l', canL && !NAV.open);
  tabsEl.classList.toggle('can-r', canR && !NAV.open);
  left.hidden = right.hidden = !over || NAV.open;
  left.disabled = !canL; right.disabled = !canR;
  more.hidden = !over && !NAV.open;
  more.textContent = NAV.open ? 'свернуть' : 'все';
  document.documentElement.style.setProperty('--nav-h', NAV.el.offsetHeight + 'px');
}

function toggleNav() {
  const { wrap } = NAV;
  const h0 = wrap.offsetHeight;
  NAV.open = !NAV.open;
  NAV.el.classList.toggle('open', NAV.open);
  navOverflow();
  const h1 = wrap.offsetHeight;
  if (!REDUCED.matches && h0 !== h1) wrap.animate([{ height: h0 + 'px' }, { height: h1 + 'px' }], { duration: 340, easing: EASE });
  if (!NAV.open) updateNavActive(curPath());
}

/* Телефон: вкладки — в выезжающем слева меню. «frx» в шапке открывает его, «frx» в меню — главная, нажатие мимо
 * меню, Esc или переход по вкладке — закрывают. Стили — mobile.css. */
const DRAWER = { el: null, veil: null, list: null };
const mobileNav = () => window.matchMedia('(max-width: 760px)').matches;
function openDrawer() { if (DRAWER.el) { document.documentElement.classList.add('drawer-open'); updateNavActive(curPath()); } }
function closeDrawer() { document.documentElement.classList.remove('drawer-open'); }
function buildDrawer(s, tabs) {
  if (!DRAWER.el) {
    DRAWER.veil = h('div', { class: 'drawer-veil', onclick: closeDrawer });
    DRAWER.el = h('aside', { class: 'drawer', 'aria-label': 'меню' });
    document.body.append(DRAWER.veil, DRAWER.el);
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeDrawer(); });
    window.addEventListener('hashchange', closeDrawer);
    window.addEventListener('resize', () => { if (!mobileNav()) closeDrawer(); });
  }
  const off = h('button', { class: 'link', type: 'button', onclick: () => act(async () => { closeDrawer(); await api('/shutdown', {}); $('#off').classList.add('show'); }) }, 'выключить сайт');
  const logout = h('button', { class: 'link', type: 'button', onclick: () => act(async () => { await api('/auth/logout', {}); location.replace('/'); }) }, 'выйти');
  DRAWER.list = h('nav', { class: 'drawer-tabs' }, tabs.map((t) => h('a', { class: 'tab', href: t.href, onclick: closeDrawer }, t.label)));
  DRAWER.el.replaceChildren(
    h('a', { class: 'drawer-brand', href: '#/', onclick: closeDrawer }, 'frx', h('small', {}, s.version), h('span', { class: 'drawer-home' }, 'на главную')),
    DRAWER.list,
    h('div', { class: 'drawer-foot' }, window.D3 ? D3.button() : null, logout, off));
}

function updateNavActive(path) {
  if (DRAWER.list) [...DRAWER.list.children].forEach((a, i) => a.classList.toggle('on', !!(NAV.tabs[i] && NAV.tabs[i].on(path))));
  const { tabsEl } = NAV;
  if (!tabsEl) return;
  let active = null;
  [...tabsEl.children].forEach((a, i) => {
    const on = NAV.tabs[i] && NAV.tabs[i].on(path);
    a.classList.toggle('on', !!on);
    if (on) { a.setAttribute('aria-current', 'page'); active = a; } else a.removeAttribute('aria-current');
  });
  if (active && !NAV.open) {                           // активный пункт не должен прятаться за краем
    const box = tabsEl.getBoundingClientRect(), r = active.getBoundingClientRect();
    if (r.left < box.left + 30 || r.right > box.right - 30) tabsEl.scrollTo({ left: active.offsetLeft - 40, behavior: 'smooth' });
  }
}

/* ------------------------------------------------------------------ *
 * главная — тихий экран; разделы выбираются в шапке
 * ------------------------------------------------------------------ */

async function home(T) {
  const [s, cl] = await Promise.all([api('/state'), api('/changelog').catch(() => ({ entries: [] }))]);
  NAV.state = { version: s.version, pages: s.pages }; renderNav();

  const stats = [
    ['модулей', s.counts.modules], ['команд', s.commands],
    ['аккаунтов', s.counts.accounts], ['конфигов', s.counts.configs],
  ].map(([label, val]) => {
    const num = h('span', { class: 'num' }, String(val));
    setTimeout(() => countUp(num, val), 180);
    return h('div', { class: 'stat' }, num, h('span', { class: 'lbl' }, label));
  });
  const online = h('div', { class: 'stat' },
    h('span', { class: 'num dotnum' }, h('i', { class: 'live' })),
    h('span', { class: 'lbl' }, s.account ? (s.account.first_name || ('id ' + s.account.id)) : 'аккаунт'));
  stats.push(online);
  stats.push(h('div', { class: 'stat' }, h('span', { class: 'num small' }, s.bot ? 'да' : 'нет'), h('span', { class: 'lbl' }, 'бот')));

  /* плашка CHANGELOG: по умолчанию — последняя версия */
  let cur = 0;
  const entries = cl.entries || [];
  const chips = h('div', { class: 'chips' });
  const list = h('ul', { class: 'cl-list' });
  const dateEl = h('span', { class: 'cl-date' });
  const drawEntry = (animate) => {
    const e = entries[cur];
    [...chips.children].forEach((c, i) => c.classList.toggle('on', i === cur));
    const fill = () => {
      list.replaceChildren(...e.items.slice(0, 9).map((t, i) => {
        const li = h('li', {}, t.length > 230 ? t.slice(0, 227) + '…' : t);
        li.style.setProperty('--i', i);
        return li;
      }));
      if (e.items.length > 9) list.appendChild(h('li', { class: 'more' }, `…и ещё ${e.items.length - 9} — в файле CHANGELOG.md`));
    };
    if (animate) { morph(body, () => { fill(); list.classList.remove('swap'); void list.offsetWidth; list.classList.add('swap'); typeText(dateEl, e.date || ''); }); }
    else { fill(); dateEl.textContent = e.date || ''; }
  };
  entries.slice(0, 14).forEach((e, i) => chips.appendChild(h('button', { class: 'chip', type: 'button', onclick: () => { if (i !== cur) { cur = i; drawEntry(true); } } }, (i === 0 ? '' : '') + e.version)));
  const body = h('div', { class: 'cl-body' }, list);
  const clPane = entries.length
    ? h('div', { class: 'cl-pane' },
        h('div', { class: 'cl-head' }, h('span', { class: 'cl-title' }, 'последние версии'), dateEl, h('div', { class: 'grow' }), chips),
        body)
    : null;
  const dock = homeDock(T, entries, clPane);                // три отдельных окна внизу — README, changelog, логи (panel.js)

  const node = h('div', { class: 'shell' },
    h('div', { class: 'home' },
      h('div', { class: 'word' }, 'FRX'),
      h('div', { class: 'desc' }, s.description),
      h('div', { class: 'ver' }, 'версия ' + s.version),
      h('div', { class: 'stats' }, stats)),
    dock);
  mount(T, node);
  if (clPane) drawEntry(false);
}

/* ------------------------------------------------------------------ *
 * конфиги (аналог модуля Config)
 * ------------------------------------------------------------------ */

async function configList(T) {
  const { modules } = await api('/configs');
  const body = modules.length
    ? modules.map((m) => row(nameSub(m.name, m.module, m.builtin ? 'встроенный' : ''),
        h('span', { class: 'val' }, `${m.items.length} ${plural(m.items.length, 'настройка', 'настройки', 'настроек')}`),
        { click: () => { location.hash = '#/config/' + encodeURIComponent(m.module); } }))
    : empty('ни у одного модуля пока нет настроек');
  const aliases = typeof acAliasesBlock === 'function' ? await acAliasesBlock() : null;     // access.js
  mount(T, shell('конфиги', {}, glass(body), aliases));
}

async function configModule(T, moduleId) {
  const { modules } = await api('/configs');
  const m = modules.find((x) => x.module === moduleId);
  if (!m) { location.hash = '#/config'; return; }
  mount(T, shell(m.name, { back: '#/config', backLabel: 'конфиги' }, glass(m.items.map((it) => configRow(m, it)))));
}

function fmtVal(v) { return v === null || v === undefined ? '—' : Array.isArray(v) ? '[' + v.join(', ') + ']' : typeof v === 'boolean' ? (v ? 'да' : 'нет') : String(v); }

/* Строка настройки живёт сама: после изменения обновляются только её элементы (без перерисовки страницы).
 * «сбросить» стоит ЛЕВЕЕ полей и всегда занимает своё место (появляется прозрачностью) — ничего не сдвигается. */
function configRow(m, it) {
  let value = it.value;
  const hints = [];
  if (it.valid) {
    const v = it.valid;
    if (v.min_value != null) hints.push('от ' + v.min_value);
    if (v.max_value != null) hints.push('до ' + v.max_value);
    if (v.min_len != null) hints.push('длина от ' + v.min_len);
    if (v.max_len != null) hints.push('длина до ' + v.max_len);
  }
  const sub = [it.description || '', hints.length ? ' · ' + hints.join(', ') : ''].join('');
  const isDefault = () => JSON.stringify(value) === JSON.stringify(it.default);
  const wide = ['choice', 'choices', 'list'].includes(it.type);
  let sync = () => {};

  const reset = h('button', { class: 'link reset', type: 'button', title: 'по умолчанию: ' + fmtVal(it.default), onclick: () => send('to_default') }, 'сбросить');
  const syncReset = () => { const show = !isDefault(); reset.classList.toggle('show', show); reset.tabIndex = show ? 0 : -1; };

  const send = async (op, v) => {
    const ok = await act(async () => {
      const r = await api('/configs/set', { module: m.module, key: it.key, op, value: v });
      value = r.value;
      return true;
    });
    sync(!!ok); syncReset();
  };

  let control;
  if (it.type === 'bool') {
    const t = toggle(!!value, (v) => send('edit', v));
    sync = () => { t.classList.toggle('on', !!value); t.setAttribute('aria-checked', String(!!value)); };
    control = t;
  } else if (it.type === 'choice') {
    const chips = (it.options || []).map((o) => h('button', { class: 'chip', type: 'button', onclick: () => {
      if (o === value) return;
      chips.forEach((c) => c.classList.toggle('on', c === chip(o)));          // сразу показываем выбор, блик — от общего обработчика
      send('edit', o);
    } }, String(o)));
    const chip = (o) => chips[(it.options || []).indexOf(o)];
    sync = () => chips.forEach((c, i) => c.classList.toggle('on', it.options[i] === value));
    control = h('div', { class: 'chips' }, chips); sync();
  } else if (it.type === 'choices') {
    const chips = (it.options || []).map((o) => h('button', { class: 'chip', type: 'button', onclick: (ev) => {
      const on = (value || []).includes(o);
      ev.currentTarget.classList.toggle('on', !on);
      send(on ? 'del' : 'add', o);
    } }, String(o)));
    sync = () => chips.forEach((c, i) => c.classList.toggle('on', (value || []).includes(it.options[i])));
    control = h('div', { class: 'chips' }, chips); sync();
  } else if (it.type === 'list') {
    const inp = h('input', { class: 'field short', placeholder: 'новый элемент' });
    const box = h('div', { class: 'chips' });
    let prev = null;
    sync = () => {
      const cur = value || [];
      box.replaceChildren(...(cur.length
        ? cur.map((v) => {
            const c = h('span', { class: 'chip' + (prev && !prev.includes(v) ? ' pop' : '') }, String(v), h('span', { class: 'x', onclick: () => send('del', v) }, '×'));
            return c;
          })
        : [h('span', { class: 'faint' }, 'список пуст')]));
      prev = cur.slice();
    };
    const add = () => { const v = inp.value.trim(); if (v) { inp.value = ''; send('add', v); } };
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') add(); });
    sync();
    control = h('div', { class: 'stack', style: 'width:100%' }, box, h('div', { class: 'inline' }, inp, h('button', { class: 'btn small', type: 'button', onclick: add }, 'добавить')));
  } else {
    const inp = h('input', { class: 'field short', value: value === null || value === undefined ? '' : String(value), inputmode: (it.type === 'int' || it.type === 'float') ? 'decimal' : 'text' });
    const save = h('button', { class: 'btn small', type: 'button', disabled: true, onclick: () => send('edit', inp.value) }, 'сохранить');
    const cmp = () => { save.disabled = inp.value === String(value ?? ''); };
    sync = (ok) => { if (ok !== false) inp.value = value === null || value === undefined ? '' : String(value); cmp(); };
    inp.addEventListener('input', cmp);
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !save.disabled) send('edit', inp.value); });
    control = h('div', { class: 'inline' }, inp, save);
  }
  syncReset();

  const main = [h('div', { class: 'name' }, it.key, h('span', { class: 'tag' }, it.type)), sub ? h('div', { class: 'sub' }, sub) : null];
  const r = row(main, wide ? control : [reset, control], { wide, cfg: true });
  if (wide) r.appendChild(reset);
  return r;
}

/* ------------------------------------------------------------------ *
 * модули (аналог Loader + Help, установка, удаление)
 * ------------------------------------------------------------------ */

async function modulesView(T) {
  const d = await api('/modules');
  const fileInput = h('input', { type: 'file', accept: '.py,.txt', style: 'display:none' });
  fileInput.addEventListener('change', () => { const f = fileInput.files[0]; fileInput.value = ''; if (f) readAndInstall(f); });

  const side = [
    h('button', { class: 'btn', onclick: () => { location.hash = '#/webide/__new__'; } }, 'создать'),
    h('button', { class: 'btn primary', onclick: () => fileInput.click() }, 'установить файл'),
    fileInput,
  ];

  const loaded = d.loaded.map((m) => row(
    nameSub(m.name, `${m.module} · v${m.version} · ${m.author}`, m.builtin ? 'встроенный' : ''),
    h('span', { class: 'val' }, `${m.commands.length} ${plural(m.commands.length, 'команда', 'команды', 'команд')}`),
    { click: () => { location.hash = '#/modules/' + encodeURIComponent(m.module); } }));

  const parts = [glass(loaded.length ? loaded : empty('модулей нет'))];

  if (d.unloaded.length) {
    parts.push(h('div', { class: 'block' }, h('div', { class: 'h2', style: 'padding-left:6px' }, 'выгруженные')));
    parts.push(glass(d.unloaded.map((u) => row(
      nameSub(u.name, `${u.file} · v${u.version}` + (u.broken ? ' · повреждён' : u.loaded ? ' · уже загружен' : ''), ''),
      [
        (!u.broken && !u.loaded) ? h('button', { class: 'btn small', onclick: () => act(async () => { await api('/modules/restore', { file: u.file }); toast('модуль восстановлен', 'ok'); route(); }) }, 'восстановить') : null,
        confirmBtn('удалить навсегда', () => act(async () => { await api('/modules/delete_unloaded', { file: u.file }); toast('файл удалён', 'ok'); route(); })),
      ]))));
  }
  parts.push(h('div', { class: 'faint', style: 'text-align:center;margin-top:22px' }, 'перетащите файл .py в окно — модуль установится после подтверждения'));
  mount(T, shell('модули', { side }, parts));
}

function readAndInstall(file) {
  const reader = new FileReader();
  reader.onload = () => startInstall(file.name, String(reader.result));
  reader.onerror = () => toast('не удалось прочитать файл', 'error');
  reader.readAsText(file);
}

async function startInstall(fileName, source) {
  const p = await act(() => api('/modules/preview', { source }));
  if (!p) return;
  if (p.existing_builtin) { toast('нельзя перезаписать встроенный модуль', 'error'); return; }
  if (p.same_version) { toast(`модуль «${p.meta.module_name}» версии ${p.existing_version} уже загружен — измените VERSION`, 'error'); return; }
  const upd = p.existing_version !== null;
  const cmds = (p.meta.commands || []).map((n) => n.join(' / ')).join(', ') || '—';
  const kv = h('div', { class: 'kv' },
    h('span', {}, 'файл'), h('span', {}, fileName),
    h('span', {}, 'название'), h('span', {}, p.meta.name),
    h('span', {}, 'id'), h('span', {}, p.meta.module_name),
    h('span', {}, 'версия'), h('span', {}, upd ? `${p.existing_version} → ${p.meta.version}` : p.meta.version),
    h('span', {}, 'автор'), h('span', {}, p.meta.author),
    h('span', {}, 'описание'), h('span', {}, p.meta.description || '—'),
    h('span', {}, 'команды'), h('span', {}, cmds));
  modal(upd ? 'обновить модуль?' : 'установить модуль?', kv, (close) => [
    h('button', { class: 'btn', onclick: close }, 'отмена'),
    h('button', { class: 'btn primary', onclick: () => act(async () => {
      const r = await api('/modules/install', { source });
      close(); toast(`${r.updated ? 'обновлён' : 'установлен'}: ${r.name}`, 'ok'); route();
    }) }, upd ? 'обновить' : 'установить'),
  ]);
}

/* перетаскивание файла в окно */
let dragDepth = 0;
window.addEventListener('dragenter', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { dragDepth++; document.body.classList.add('drag'); e.preventDefault(); } });
window.addEventListener('dragover', (e) => { if (document.body.classList.contains('drag')) e.preventDefault(); });
window.addEventListener('dragleave', () => { dragDepth = Math.max(0, dragDepth - 1); if (!dragDepth) document.body.classList.remove('drag'); });
window.addEventListener('drop', (e) => {
  dragDepth = 0; document.body.classList.remove('drag');
  const f = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
  if (f) { e.preventDefault(); readAndInstall(f); }
});

async function moduleDetail(T, moduleId) {
  const d = await api('/modules');
  const m = d.loaded.find((x) => x.module === moduleId);
  if (!m) { location.hash = '#/modules'; return; }
  const P = d.prefix;

  const info = h('div', { class: 'kv', style: 'padding:16px 0' },
    h('span', {}, 'id'), h('span', {}, m.module),
    h('span', {}, 'версия'), h('span', {}, m.version),
    h('span', {}, 'автор'), h('span', {}, m.author),
    h('span', {}, 'описание'), h('span', {}, m.description || '—'));

  const cmds = m.commands.length
    ? m.commands.map((c) => row(nameSub(P + c.command + (c.aliases.length ? '  ·  ' + c.aliases.map((a) => P + a).join('  ') : ''), c.description || 'без описания')))
    : [empty('у модуля нет команд')];

  const extraNames = { watchers: 'вотчеры', telegram_events: 'события telegram', callbacks: 'callback', on_start: 'on_start', on_load: 'on_load', on_error: 'on_error', cycles: 'циклы', bot_commands: 'команды бота', bot_watchers: 'вотчеры бота' };
  const extras = Object.entries(m.other).filter(([, n]) => n > 0).map(([k, n]) => `${extraNames[k] || k}: ${n}`);
  if (m.pages) extras.push('страницы сайта: ' + m.pages);
  const bot = m.bot_commands.length ? m.bot_commands.map((c) => row(nameSub('/' + c.command, c.description || 'без описания'))) : [];

  const actions = [
    m.emulate ? h('button', { class: 'btn primary', type: 'button', onclick: () => { location.hash = '#/modules/' + encodeURIComponent(m.module) + '/emulate'; } }, '> emulate') : null,
    h('button', { class: 'btn', onclick: () => { location.hash = '#/webide/' + encodeURIComponent(m.module); } }, m.builtin ? 'посмотреть код' : 'редактировать'),
    h('button', { class: 'btn', onclick: () => act(async () => { await api('/modules/reload', { module: m.module }); toast('модуль перезагружен', 'ok'); route(); }) }, 'перезагрузить'),
    m.builtin ? null : confirmBtn('выгрузить', () => act(async () => {
      await api('/modules/unload', { module: m.module });
      toast('модуль выгружен — вернуть можно в списке «выгруженные»', 'ok'); location.hash = '#/modules';
    }), 'btn danger'),
  ];

  mount(T, shell(m.name, { back: '#/modules', backLabel: 'модули', side: actions },
    glass(info),
    h('div', { class: 'block' }, h('div', { class: 'h2', style: 'padding-left:6px' }, 'команды')),
    glass(cmds),
    bot.length ? [h('div', { class: 'block' }, h('div', { class: 'h2', style: 'padding-left:6px' }, 'команды бота')), glass(bot)] : null,
    extras.length ? h('div', { class: 'faint', style: 'margin-top:16px;padding-left:6px' }, extras.join('  |  ')) : null));
}

/* ------------------------------------------------------------------ *
 * аккаунты (аналог модуля AccountManager)
 * ------------------------------------------------------------------ */

async function accountsView(T) {
  let data = await api('/accounts');
  let flow = null, panel = null, initial = true;
  const rows = new Map();                                  // phone -> части строки
  const listBox = h('div', { class: 'glass acc-list' });
  const flowSlot = h('div', { class: 'flow-slot' });
  const lblNum = h('span', { class: 'lbl' });
  const btnNum = h('button', { class: 'btn', type: 'button', onclick: () => act(async () => { data = { ...data, ...(await api('/accounts/toggle_numbers', {})) }; await reload(false); }) }, lblNum);
  const btnRef = h('button', { class: 'btn', type: 'button', onclick: () => act(async () => { await api('/accounts/refresh', {}); await reload(false); toast('данные обновлены', 'ok'); }) }, 'обновить');
  const btnAdd = h('button', { class: 'btn primary', type: 'button', onclick: () => { flow = { step: 'phone' }; renderFlow(); } }, 'добавить');
  const emptyEl = h('div', { class: 'empty' });

  const reload = async (effect) => { data = await api('/accounts'); renderList(effect); syncSide(); };
  const syncSide = () => {
    const t = data.show_numbers ? 'скрыть номера' : 'показать номера';
    if (initial) lblNum.textContent = t; else typeText(lblNum, t);
  };

  function endButtons(p) {
    if (p.cur.active) return [h('span', { class: 'dots' }, h('i', { class: 'on' }))];
    return [
      h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/accounts/switch', { phone: p.cur.phone }); toast('юзербот перенесён на ' + p.cur.first_name, 'ok'); await reload(true); }) }, 'переключить'),
      confirmBtn('удалить', () => act(async () => { await api('/accounts/delete', { phone: p.cur.phone }); toast('аккаунт удалён', 'ok'); await reload(true); })),
    ];
  }

  function makeRow() {
    const p = { nm: h('span'), tagSlot: h('span'), user: h('span'), id: h('span'), shown: h('span'), end: h('div', { class: 'end' }), active: null, cur: null };
    p.el = h('div', { class: 'row' },
      h('div', { class: 'main' }, h('div', { class: 'name' }, p.nm, p.tagSlot), h('div', { class: 'sub' }, p.user, p.id, p.shown)),
      p.end);
    return p;
  }

  function fillRow(p, a, fresh) {
    p.cur = a;
    const set = (el, txt) => { if (fresh) el.textContent = txt; else typeText(el, txt); };
    set(p.nm, a.first_name);
    set(p.user, a.username ? '@' + a.username + ' · ' : '');
    set(p.id, a.id ? 'id ' + a.id + ' · ' : '');
    set(p.shown, a.shown);
    if (p.active !== a.active) {
      p.active = a.active;
      p.tagSlot.replaceChildren(...(a.active ? [h('span', { class: 'tag pop' }, 'активный')] : []));
      p.end.replaceChildren(...endButtons(p));
      if (!fresh) { p.end.classList.remove('swap'); void p.end.offsetWidth; p.end.classList.add('swap'); }
    }
  }

  function renderList(effect) {
    const seen = new Set();
    data.accounts.forEach((a) => {
      seen.add(a.phone);
      let p = rows.get(a.phone);
      if (!p) {
        p = makeRow(); rows.set(a.phone, p);
        fillRow(p, a, true);
        if (!initial) p.el.classList.add('acc-new');
      } else fillRow(p, a, false);
    });
    for (const [phone, p] of [...rows]) if (!seen.has(phone)) { rows.delete(phone); collapseOut(p.el); }
    // порядок и пустое состояние (двигаем узлы только если порядок действительно изменился)
    const want = data.accounts.map((a) => rows.get(a.phone).el);
    const have = [...listBox.children].filter((c) => !c.classList.contains('leaving-row') && c !== emptyEl);
    if (want.length !== have.length || want.some((el, i) => el !== have[i])) want.forEach((el, i) => {
      const cur = [...listBox.children].filter((c) => !c.classList.contains('leaving-row') && c !== emptyEl)[i];
      if (cur !== el) listBox.insertBefore(el, cur || null);
    });
    if (!want.length) {
      const t = data.current ? `в списке пока нет аккаунтов · сейчас работает ${data.current.first_name}` : 'аккаунтов нет';
      if (!emptyEl.isConnected) { emptyEl.textContent = ''; listBox.appendChild(emptyEl); }
      typeText(emptyEl, t);
    } else if (emptyEl.isConnected) emptyEl.remove();
    if (effect) sheen(listBox);
  }

  /* ---- добавление аккаунта: панель живёт, меняются только её части ---- */
  function buildPanel() {
    const dotsEl = dots(1, 3), label = h('span', { class: 'muted' }), inp = h('input', { class: 'field', autocomplete: 'off' });
    const goLbl = h('span', { class: 'lbl' });
    const go = () => act(async () => {
      const v = inp.value.trim();
      if (!v) return;
      let r;
      if (flow.step === 'phone') r = await api('/accounts/login/phone', { phone: v });
      else if (flow.step === 'code') r = await api('/accounts/login/code', { code: v });
      else r = await api('/accounts/login/2fa', { password: v });
      if (r.step === 'done') { toast('аккаунт ' + r.phone + ' добавлен', 'ok'); flow = null; renderFlow(); await reload(true); return; }
      flow = { step: r.step, phone: r.phone || flow.phone }; updatePanel();
    });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    const el = h('div', { class: 'block flow' }, glass(h('div', { style: 'padding:18px 0 22px' },
      h('div', { class: 'inline', style: 'margin-bottom:14px' }, dotsEl, h('span', { class: 'vsep' }), label),
      h('div', { class: 'inline' }, h('div', { class: 'grow' }, inp),
        h('button', { class: 'btn primary', type: 'button', onclick: go }, goLbl),
        h('button', { class: 'btn', type: 'button', onclick: () => act(async () => { await api('/accounts/login/cancel', {}); flow = null; renderFlow(); }) }, 'отмена')))));
    return { el, dotsEl, label, inp, goLbl };
  }

  function updatePanel() {
    const steps = { phone: 1, code: 2, '2fa': 3 }, n = steps[flow.step];
    [...panel.dotsEl.children].forEach((d, i) => d.classList.toggle('on', i < n));
    typeText(panel.label, flow.step === 'phone' ? 'номер в международном формате' : flow.step === 'code' ? `код отправлен на ${flow.phone}` : 'на аккаунте включена 2fa');
    typeText(panel.goLbl, flow.step === 'phone' ? 'отправить код' : 'войти');
    panel.inp.type = flow.step === '2fa' ? 'password' : 'text';
    panel.inp.placeholder = flow.step === 'phone' ? '+79991234567' : flow.step === 'code' ? 'код из telegram' : 'пароль двухэтапной защиты';
    panel.inp.value = '';
    panel.inp.focus();
  }

  function renderFlow() {
    btnAdd.classList.toggle('gone', !!flow);
    if (!flow) {
      if (panel) { const old = panel; panel = null; collapseOut(old.el); }
      return;
    }
    if (!panel) { panel = buildPanel(); flowSlot.appendChild(panel.el); growIn(panel.el); }
    updatePanel();
  }

  renderList(false); syncSide();
  initial = false;
  mount(T, shell('аккаунты', { side: [btnNum, btnRef, btnAdd] }, listBox, flowSlot));
}

/* ------------------------------------------------------------------ *
 * Emulate: команды, формы и диалоги ЛЮБОГО модуля.
 * Сервер (core/emulate.py) запускает команду с виртуальным событием и
 * перехватывает ответы/формы/ожидания; тут они рисуются живой лентой.
 * ------------------------------------------------------------------ */

const fmtSize = (n) => (n === null || n === undefined) ? '' : n < 1024 ? n + ' Б' : n < 1048576 ? (n / 1024).toFixed(1) + ' КБ' : (n / 1048576).toFixed(1) + ' МБ';

/* HTML Telegram приходит разобранным в узлы {t, c, href} — собираем DOM только из белого списка */
function inl(n) {
  if (typeof n === 'string') return document.createTextNode(n);
  const kids = (n.c || []).map(inl);
  switch (n.t) {
    case 'b': return h('b', {}, kids);
    case 'i': return h('i', {}, kids);
    case 'u': return h('u', {}, kids);
    case 's': return h('s', {}, kids);
    case 'code': return h('code', {}, kids);
    case 'pre': return h('pre', {}, kids);
    case 'q': return h('span', { class: 'q' }, kids);
    case 'sp': {
      const el = h('span', { class: 'sp', title: 'нажмите, чтобы показать' }, kids);
      el.addEventListener('click', () => el.classList.toggle('open'));
      return el;
    }
    case 'a': return n.href ? h('a', { href: n.href, target: '_blank', rel: 'noopener noreferrer' }, kids) : h('span', {}, kids);
    default: return h('span', {}, kids);
  }
}

async function downloadFile(module, f) {
  const r = await fetch(`/api/emulate/file?module=${enc(module)}&id=${enc(f.id)}`, { headers: { 'X-FRX': '1' } });
  if (!r.ok) throw new Error('файл недоступен');
  const url = URL.createObjectURL(await r.blob());
  const a = h('a', { href: url, download: f.name });
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 4000);
}

async function emulateModule(T, moduleId) {
  const d = await api('/emulate/module?module=' + enc(moduleId));
  const P = d.prefix;
  let rev = d.session.rev, lastToast = 0, fast = 0, timer = null, inflight = false, first = true, state = d.session;
  const nodes = new Map();                                // id записи -> элемент ленты

  const stream = h('div', { class: 'emu-stream' });
  const placeholder = h('div', { class: 'empty' }, 'здесь появятся ответы команд');
  const status = h('span', { class: 'emu-status' });
  const post = (path, body) => act(async () => { await api(path, { module: moduleId, ...body }); poke(); });

  const stopBtn = h('button', { class: 'btn small', type: 'button', disabled: true, onclick: () => post('/emulate/stop', {}) }, 'остановить');
  const clearBtn = h('button', {
    class: 'btn small', type: 'button',
    onclick: () => act(async () => { await api('/emulate/clear', { module: moduleId }); nodes.forEach((el) => collapseOut(el)); nodes.clear(); setTimeout(syncEmpty, 380); poke(); }),
  }, 'очистить');

  const sayInp = h('input', { class: 'field', placeholder: 'модуль ждёт ответ — введите сообщение', autocomplete: 'off' });
  const say = () => {
    const v = sayInp.value;
    if (!v.trim()) return;
    act(async () => { await api('/emulate/say', { module: moduleId, text: v }); sayInp.value = ''; poke(); });
  };
  sayInp.addEventListener('keydown', (e) => { if (e.key === 'Enter') say(); });
  const sayBar = h('div', { class: 'emu-say' }, sayInp, h('button', { class: 'btn small primary', type: 'button', onclick: say }, 'ответить'));

  const syncEmpty = () => { if (nodes.size) placeholder.remove(); else if (!placeholder.isConnected) stream.appendChild(placeholder); };
  const atBottom = () => stream.scrollHeight - stream.scrollTop - stream.clientHeight < 70;

  /* ---- записи ленты ---- */

  function msgNode(e) {
    const box = h('div', { class: 'emu-msg' }, h('div', { class: 'emu-text' }, (e.nodes || []).map(inl)));
    box._sig = 'm' + JSON.stringify((e.files || []).map((f) => [f.id, f.name]));
    if ((e.files || []).length) {
      box.appendChild(h('div', { class: 'emu-files' }, e.files.map((f) => f.id
        ? h('button', { class: 'chip', type: 'button', onclick: () => act(() => downloadFile(moduleId, f)) }, '⬇ ' + f.name + (f.size !== null && f.size !== undefined ? ' · ' + fmtSize(f.size) : ''))
        : h('span', { class: 'chip' }, f.name))));
    }
    return box;
  }

  function formNode(e) {
    const box = h('div', { class: 'emu-msg emu-form' + (e.closed ? ' closed' : '') }, h('div', { class: 'emu-text' }, (e.nodes || []).map(inl)));
    const inpBox = h('div');
    const openInput = (b) => {
      const hint = (b.hint && b.hint !== 'Enter value...') ? b.hint : '';
      const multi = /\n/.test(hint) || hint.length > 70;
      const f = h(multi ? 'textarea' : 'input', { class: 'field', placeholder: 'значение', autocomplete: 'off', spellcheck: 'false' });
      f.value = hint;
      const close = () => inpBox.replaceChildren();
      const send = () => act(async () => { await api('/emulate/input', { module: moduleId, form: e.form, data: b.input, value: f.value }); close(); poke(); });
      f.addEventListener('keydown', (ev) => {
        if (ev.key === 'Escape') close();
        else if (ev.key === 'Enter' && (!multi || ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); send(); }
      });
      inpBox.replaceChildren(h('div', { class: 'emu-inp' }, f,
        h('button', { class: 'btn small primary', type: 'button', onclick: send }, 'ок'),
        h('button', { class: 'btn small', type: 'button', onclick: close }, '×')));
      f.focus();
    };
    if ((e.rows || []).length) {
      box.appendChild(h('div', { class: 'emu-kb' }, e.rows.map((r) => h('div', { class: 'emu-krow' }, r.map((b) => (b.input !== undefined
        ? h('button', { class: 'btn small', type: 'button', onclick: () => openInput(b) }, b.text)
        : /^url:(https?|tg):/.test(b.data || '') ? h('a', { class: 'btn small', href: b.data.slice(4), target: '_blank', rel: 'noopener noreferrer' }, b.text + ' ↗')
        : h('button', { class: 'btn small', type: 'button', onclick: () => post('/emulate/click', { form: e.form, data: b.data }) }, b.text)))))));
    }
    box.appendChild(inpBox);
    box._sig = 'f' + JSON.stringify((e.rows || []).map((r) => r.map((b) => [b.text, b.data, b.input])));
    return box;
  }

  const fileChips = (files) => (files && files.length ? h('div', { class: 'emu-files' }, files.map((f) => h('span', { class: 'chip' }, '📎 ' + f.name + (f.size !== null && f.size !== undefined ? ' · ' + fmtSize(f.size) : '')))) : null);

  function apiNode(e) {                                      // запрос к Telegram, который сделал модуль
    const write = e.risk === 'write';
    return h('div', { class: 'emu-api ' + (write ? 'write' : 'read') + (e.ok === false ? ' bad' : e.ok === null ? ' run' : '') },
      h('span', { class: 'emu-api-proto' }, e.proto === 'bot' ? 'Bot API' : 'MTProto'),
      h('b', { class: 'emu-api-name' }, e.name),
      e.brief ? h('span', { class: 'emu-api-brief' }, e.brief) : null,
      h('span', { class: 'emu-api-tags' },
        h('span', { class: 'emu-api-risk', title: write ? 'меняет что-то в аккаунте или чатах — это увидят другие' : 'только читает данные' }, write ? 'меняет' : 'читает'),
        e.ms !== null && e.ms !== undefined ? h('span', { class: 'emu-api-ms' }, e.ms >= 1000 ? (e.ms / 1000).toFixed(1) + ' с' : Math.round(e.ms) + ' мс') : h('span', { class: 'emu-api-ms' }, '…')),
      e.error ? h('div', { class: 'emu-api-err' }, e.error) : null);
  }

  function replyNode(e) {                                    // «сообщение, на которое отвечает команда»
    const who = e.who === 'me' ? 'ваше сообщение' : (e.name || 'чужое сообщение');
    const box = h('div', { class: 'emu-reply' }, h('div', { class: 'emu-reply-h' }, '↩ reply — ' + who),
      (e.nodes || []).length ? h('div', { class: 'emu-text' }, e.nodes.map(inl)) : h('div', { class: 'emu-text muted' }, '(без текста)'), fileChips(e.files));
    return box;
  }

  function build(e) {
    switch (e.kind) {
      case 'api': return apiNode(e);
      case 'reply': return replyNode(e);
      case 'cmd': return h('div', { class: 'emu-cmd' }, '› ' + e.text, e.reply ? h('span', { class: 'emu-cmd-tag' }, 'в ответ на сообщение') : null, fileChips(e.files));
      case 'in': return h('div', { class: 'emu-in' }, e.text);
      case 'sys': return h('div', { class: 'emu-sys ' + (e.level || '') }, e.text);
      case 'msg': return msgNode(e);
      case 'form': return formNode(e);
      default: return null;
    }
  }

  function followBottom(ms) {
    const t0 = performance.now();
    const step = () => { stream.scrollTop = stream.scrollHeight; if (performance.now() - t0 < ms) requestAnimationFrame(step); };
    requestAnimationFrame(step);
  }

  /* появление нового сообщения: плашка вырастает, текст «печатается» */
  function appear(el, stick) {
    const t = el.querySelector('.emu-text');
    if (t && t.textContent.length < 700) revealChars(t, 0);
    el.querySelectorAll('.emu-kb .btn').forEach((b, i) => b.style.setProperty('--i', i));
    el.classList.add('emu-new');
    setTimeout(() => el.classList.remove('emu-new'), 1200);
    growIn(el, stick ? followBottom : null);
  }

  /* изменение существующей записи: плашка плавно меняет размер, дописанный текст печатается */
  function update(old, fresh, stick) {
    const txt = (n) => (n.querySelector('.emu-text') || n).textContent;
    const t0 = txt(old);
    const sameKeys = old._sig !== undefined && old._sig === fresh._sig;
    morph(old, () => {
      if (sameKeys) {                                      // клавиатура не менялась — не трогаем её, обновляем только текст и состояние
        const ot = old.querySelector('.emu-text'), ft = fresh.querySelector('.emu-text');
        old.className = fresh.className;
        if (ot && ft) ot.replaceChildren(...ft.childNodes);
      } else {
        old.className = fresh.className;
        old.replaceChildren(...fresh.childNodes);
        old._sig = fresh._sig;
        old.querySelectorAll('.emu-kb .btn').forEach((b, i) => b.style.setProperty('--i', i));
        old.classList.add('emu-upd'); setTimeout(() => old.classList.remove('emu-upd'), 1100);
      }
    });
    const t1 = txt(old);
    if (t1 !== t0 && t1.length < 1500) revealChars(old.querySelector('.emu-text') || old, commonPrefix(t0, t1));
    if (stick) followBottom(450);
  }

  function apply(snap) {
    const stick = atBottom() || first;
    let added = false;
    for (const e of snap.entries) {
      if (e.kind === 'toast') {
        if (e.id > lastToast) { lastToast = e.id; toast(e.text, e.level === 'error' ? 'error' : ''); }
        continue;
      }
      const old = nodes.get(e.id);
      if (e.removed) { if (old) { nodes.delete(e.id); if (first) old.remove(); else collapseOut(old); } continue; }
      const el = build(e);
      if (!el) continue;
      if (old) update(old, el, stick);
      else {
        stream.appendChild(el); nodes.set(e.id, el); added = true;
        if (!first) appear(el, stick);
      }
    }
    const busy = snap.running || snap.waiting;
    const st = snap.running ? 'выполняется…' : snap.waiting ? 'модуль ждёт ваш ответ' : '';
    if (first) status.textContent = st; else typeText(status, st);
    status.classList.toggle('busy', !!busy);
    stopBtn.disabled = !busy;
    const wasWaiting = sayBar.classList.contains('show');
    sayBar.classList.toggle('show', snap.waiting > 0);
    if (snap.waiting > 0 && !wasWaiting && document.activeElement === document.body) sayInp.focus();
    state = snap;
    updateApiBadge(snap.api);
    refreshUploads(snap.uploads || []);
    syncEmpty();
    if (added && stick && first) requestAnimationFrame(() => { stream.scrollTop = stream.scrollHeight; });
  }

  /* ---- опрос ленты: чаще, пока что-то выполняется; не считается «активностью» сайта ---- */

  const tick = async () => {
    if (T !== TOKEN) return;
    inflight = true;
    try {
      const snap = await api(`/emulate/poll?module=${enc(moduleId)}&since=${rev}`);
      if (T !== TOKEN) return;
      rev = snap.rev; apply(snap); first = false;
    } catch (e) {
      inflight = false;
      if (e instanceof SleepError) { if (T === TOKEN) timer = setTimeout(tick, 1500); return; }     // юзербот перезагружается — ждём
      if (e.message !== 'сайт выключен' && T === TOKEN) { toast(e.message, 'error'); location.hash = '#/modules/' + enc(moduleId); }
      return;
    }
    inflight = false;
    const busy = state.running || state.waiting;
    timer = setTimeout(tick, document.hidden ? 3000 : (busy || fast-- > 0) ? 450 : 1600);
  };
  const poke = () => {
    fast = 12;
    if (inflight) return;
    clearTimeout(timer);
    timer = setTimeout(tick, 40);
  };

  /* ---- команды ---- */

  /* ---- контекст команды: reply и файлы ---- */

  const ctx = { replyOn: false, text: 'Текст сообщения, на которое отвечаем', from: 'other', replyFile: null, attachFile: null };
  const warned = new Set();
  const replyFields = h('div', { class: 'emu-ctx-fields' });
  const uploadsBox = h('div', { class: 'emu-uploads' });
  const ctxBox = h('details', { class: 'emu-ctx' });
  const apiBadge = h('span', { class: 'emu-apibadge', hidden: true, title: 'запросы к Telegram, которые модуль сделал за эту сессию Emulate' });

  const updateApiBadge = (a) => {
    if (!a || !a.total) { apiBadge.hidden = true; return; }
    apiBadge.hidden = false;
    apiBadge.classList.toggle('warn', a.write > 0);
    apiBadge.textContent = `Telegram: ${a.total} ${plural(a.total, 'запрос', 'запроса', 'запросов')}` + (a.write ? ` · меняют: ${a.write}` : '') + (a.errors ? ` · ошибок: ${a.errors}` : '');
  };

  const pickFile = (onDone) => {
    const inp = h('input', { type: 'file' });
    inp.addEventListener('change', async () => {
      const f = inp.files[0];
      if (!f) return;
      await act(async () => { const up = await apiUpload(`/emulate/upload?module=${enc(moduleId)}&name=${enc(f.name)}`, f); onDone(up); poke(); });
    });
    inp.click();
  };
  const upName = (id) => { const u = (state.uploads || []).find((x) => x.id === id); return u ? `${u.name} · ${fmtSize(u.size)}` : null; };

  function drawCtx() {
    const fileBtn = (label, key) => {
      const cur = ctx[key] ? upName(ctx[key]) : null;
      return h('div', { class: 'emu-ctx-file' },
        h('button', { class: 'btn small', type: 'button', onclick: () => pickFile((up) => { ctx[key] = up.id; drawCtx(); }) }, cur ? 'заменить файл' : label),
        cur ? h('span', { class: 'chip' }, '📎 ' + cur, h('button', { class: 'emu-chip-x', type: 'button', title: 'убрать', onclick: () => { ctx[key] = null; drawCtx(); } }, '×')) : h('span', { class: 'muted' }, 'файл не выбран'));
    };
    const ta = h('textarea', { class: 'field', rows: 2, placeholder: 'текст сообщения, на которое отвечает команда', spellcheck: 'false' });
    ta.value = ctx.text; ta.addEventListener('input', () => { ctx.text = ta.value; });
    const fromSel = h('select', { class: 'field emu-from' }, [['other', 'написал другой человек'], ['me', 'написал я']].map(([v, l]) => h('option', { value: v }, l)));
    fromSel.value = ctx.from; fromSel.addEventListener('change', () => { ctx.from = fromSel.value; });
    replyFields.replaceChildren(...(ctx.replyOn ? [ta, fromSel, h('div', { class: 'label' }, 'файл в этом сообщении'), fileBtn('приложить файл к reply', 'replyFile')] : []));
    ctxBox.querySelector('.emu-ctx-sum').textContent = 'контекст команды: ' + ([ctx.replyOn ? 'reply' + (ctx.replyFile ? ' + файл' : '') : null, ctx.attachFile ? 'файл к команде' : null].filter(Boolean).join(', ') || 'нет');
    const attach = ctxBox.querySelector('.emu-ctx-attach');
    attach.replaceChildren(fileBtn('прикрепить файл к команде', 'attachFile'));
  }
  function refreshUploads(list) {
    ['replyFile', 'attachFile'].forEach((k) => { if (ctx[k] && !list.some((u) => u.id === ctx[k])) ctx[k] = null; });
    const sig = JSON.stringify(list.map((u) => u.id)) + ctx.replyFile + ctx.attachFile;
    if (uploadsBox._sig === sig) return;
    uploadsBox._sig = sig;
    uploadsBox.replaceChildren(...(list.length ? [h('div', { class: 'label' }, 'загруженные файлы (удаляются при выходе)'),
      ...list.map((u) => h('div', { class: 'emu-upl' }, h('span', {}, '📎 ' + u.name), h('span', { class: 'muted' }, fmtSize(u.size)),
        h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/emulate/unupload', { module: moduleId, id: u.id }); poke(); }) }, 'удалить')))] : []));
    if (ctxBox.querySelector('.emu-ctx-attach')) drawCtx();
  }
  ctxBox.append(
    h('summary', {}, h('span', { class: 'emu-ctx-sum' }, 'контекст команды: нет')),
    h('div', { class: 'emu-ctx-body' },
      h('div', { class: 'muted emu-ctx-hint' }, 'Для команд, которым нужен reply или файл: «ответ на сообщение» делает вид, что команду написали в ответ на сообщение (event.is_reply, get_reply_message(), files.download_reply_file); файл можно приложить к этому сообщению или к самой команде.'),
      h('div', { class: 'emu-ctx-row' }, toggle(false, (v) => { ctx.replyOn = v; drawCtx(); }), h('span', {}, 'ответ на сообщение (reply)')),
      replyFields,
      h('div', { class: 'emu-ctx-row' }, h('span', { class: 'label' }, 'файл прикреплён к самой команде')),
      h('div', { class: 'emu-ctx-attach' }),
      uploadsBox));
  drawCtx();
  const openCtx = (msg) => {
    ctxBox.open = true;
    toast(msg, 'error');
    ctxBox.scrollIntoView({ block: 'center', behavior: 'smooth' });
    ctxBox.classList.add('flash'); setTimeout(() => ctxBox.classList.remove('flash'), 1200);
  };
  const ctxBody = () => {
    const out = {};
    if (ctx.replyOn) out.reply = { text: ctx.text, from: ctx.from, file: ctx.replyFile || null };
    if (ctx.attachFile) out.attach = ctx.attachFile;
    return out;
  };

  const commandRow = (c) => {
    const names = P + c.command + (c.aliases.length ? '  ·  ' + c.aliases.map((a) => P + a).join('  ') : '');
    const inp = c.args ? h('input', { class: 'field', placeholder: c.usage || 'аргументы', autocomplete: 'off', spellcheck: 'false' }) : null;
    const run = () => {
      let hint = '';
      if (c.needs_reply && !ctx.replyOn) hint = 'Этой команде нужен reply — включите «ответ на сообщение» в блоке «контекст команды»' + (c.needs_file ? ' и приложите файл.' : '.');
      else if (c.needs_file && !(ctx.replyOn && ctx.replyFile) && !ctx.attachFile) hint = 'Команда работает с файлом — приложите его в блоке «контекст команды».';
      if (hint && !warned.has(c.command)) { warned.add(c.command); openCtx(hint + ' Нажмите «запустить» ещё раз, чтобы запустить без этого.'); return; }
      post('/emulate/run', { command: c.command, args: inp ? inp.value : '', ...ctxBody() });
    };
    const go = c.confirm
      ? confirmBtn('запустить', run, 'btn small danger')
      : h('button', { class: 'btn small primary', type: 'button', onclick: run }, 'запустить');
    if (inp && !c.confirm) inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') run(); });
    return row(nameSub(names, c.description || 'без описания', [c.needs_reply ? 'нужен reply' : '', c.needs_file ? 'нужен файл' : ''].filter(Boolean).join(' · ')),
      h('div', { class: 'inline', style: 'width:100%' }, inp ? h('div', { class: 'grow' }, inp) : null, go), { wide: true });
  };

  const side = d.native.length
    ? [h('a', { class: 'btn', href: `#/page/${enc(moduleId)}/${d.native[0].index}` }, 'своя страница')] : null;

  apply(d.session);
  first = false;
  const grid = h('div', { class: 'emu' },
    h('div', { class: 'emu-main' },
      h('div', { class: 'h2', style: 'padding-left:6px' }, 'команды'),
      glass(ctxBox, h('div', { class: 'emu-cmds rows' }, d.commands.map(commandRow)))),
    h('div', { class: 'emu-side' },
      h('div', { class: 'h2', style: 'padding-left:6px' }, 'лента'),
      glass(h('div', { class: 'emu-head' }, status, apiBadge, h('div', { class: 'grow' }), stopBtn, clearBtn), stream, sayBar)));

  // телефон: команды и лента — по очереди; после «запустить» сразу показываем ленту
  const eseg = mSeg([['cmds', 'команды'], ['feed', 'лента']], 'cmds', (id) => { grid.classList.toggle('mv-feed', id === 'feed'); if (id === 'feed') stream.scrollTop = stream.scrollHeight; });
  eseg.el.classList.add('m-only');
  grid.addEventListener('click', (e) => {
    if (!mIsPhone() || !e.target.closest('.emu-cmds .btn.primary, .emu-cmds .btn.danger.armed')) return;
    setTimeout(() => { eseg.set('feed'); grid.classList.add('mv-feed'); stream.scrollTop = stream.scrollHeight; }, 120);
  });

  if (!mount(T, shell(d.name + ' · emulate', { back: '#/modules/' + enc(moduleId), backLabel: 'модуль', side }, eseg.el, grid))) return;
  stream.scrollTop = stream.scrollHeight;
  timer = setTimeout(tick, 900);
}

/* ------------------------------------------------------------------ *
 * страницы модулей (web.page → дерево компонентов)
 * ------------------------------------------------------------------ */

async function pageView(T, module, index) {
  const vals = {};
  const body = h('div', { class: 'ui' });
  let title = '';

  const load = async (action) => {
    const r = await api('/page/render', { module, index: Number(index), values: vals, action });
    title = r.title;
    (r.toasts || []).forEach((t) => toast(t.text, t.kind));
    body.replaceChildren(...r.tree.map(renderNode));
  };

  function renderNode(n) {
    switch (n.t) {
      case 'title': return h('div', { class: 'u-title' }, n.text);
      case 'text': return h('div', {}, n.text);
      case 'muted': return h('div', { class: 'muted' }, n.text);
      case 'code': return h('div', { class: 'u-code' }, n.text);
      case 'divider': return h('div', { class: 'u-div' });
      case 'input': {
        vals[n.name] = n.value;
        const el = n.multiline
          ? h('textarea', { class: 'field', placeholder: n.placeholder })
          : h('input', { class: 'field', type: n.secret ? 'password' : 'text', placeholder: n.placeholder });
        el.value = n.value ?? '';
        el.addEventListener('input', () => { vals[n.name] = el.value; });
        return h('label', { class: 'stack', style: 'display:flex;flex:1;min-width:160px' }, n.label ? h('span', { class: 'label' }, n.label) : null, el);
      }
      case 'toggle': {
        vals[n.name] = n.value;
        return h('div', { class: 'inline', style: 'align-items:center' }, toggle(n.value, (v) => { vals[n.name] = v; }), n.label ? h('span', { class: 'muted' }, n.label) : null);
      }
      case 'select': {
        vals[n.name] = n.value;
        const wrap = h('div', { class: 'stack' }, n.label ? h('span', { class: 'label' }, n.label) : null);
        const chips = h('div', { class: 'chips' });
        n.options.forEach((o) => {
          const c = h('button', { class: 'chip' + (o === n.value ? ' on' : ''), type: 'button' }, o);
          c.addEventListener('click', () => { vals[n.name] = o; [...chips.children].forEach((x) => x.classList.toggle('on', x === c)); });
          chips.appendChild(c);
        });
        wrap.appendChild(chips);
        return wrap;
      }
      case 'button': {
        const b = h('button', { class: 'btn' + (n.danger ? ' danger' : ' primary'), type: 'button', disabled: !n.action }, n.label);
        if (n.action) b.addEventListener('click', () => act(async () => { b.disabled = true; try { await load(n.action); } finally { b.disabled = false; } }));
        return b;
      }
      case 'row': return h('div', { class: 'u-row' }, (n.children || []).map(renderNode));
      case 'section': return h('div', { class: 'u-sec' }, n.title ? h('div', { class: 'h2' }, n.title) : null, h('div', { class: 'u-body' }, (n.children || []).map(renderNode)));
      case 'list': return h('div', {}, n.items.map((it) => row(nameSub(it.title, it.text))));
      case 'table': return h('div', { class: 'tablewrap' }, h('table', { class: 'u-table' },
        h('thead', {}, h('tr', {}, n.headers.map((x) => h('th', {}, x)))),
        h('tbody', {}, n.rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, c)))))));
      default: return h('div', { class: 'faint' }, '[неизвестный компонент: ' + n.t + ']');
    }
  }

  await load();
  mount(T, shell(title, {}, glass(h('div', { style: 'padding:18px 0 22px' }, body))));
}

/* ------------------------------------------------------------------ */

NAV.el = $('#nav');
refreshNav();
route();
