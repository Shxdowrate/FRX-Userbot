/* FRX WebIDE Sandbox. Панель под редактором: изолированная песочница модуля (бэкенд — core/sandbox.py).
 * Слева — лента чатов (как в Telegram: сообщения, формы, кнопки), справа — инспектор: журнал, ключи db/cache, конфиг, время,
 * события (готовые конструкции), обработчики, участники и чаты, страницы сайта. Загружается после webide.js, использует
 * утилиты app.js (h, api, act, toast, inl, fmtSize, toggle, plural) и wiDrawer/pyHighlight из webide.js только во время вызова.
 * Любые данные попадают в DOM через textContent/createElement. */
'use strict';

const SB_ICON = { event: '⚡', handler: 'ƒ', error: '✘', db: '▤', cache: '◔', config: '⚙', send: '➤', log: '›', time: '◷', warn: '!', info: 'i', toast: '◆', api: '☍' };
const SB_GROUPS = [['event', 'события', ['event']], ['handler', 'вызовы', ['handler']], ['error', 'ошибки', ['error']], ['db', 'db', ['db']], ['cache', 'cache', ['cache']],
  ['send', 'отправка', ['send', 'api']], ['log', 'лог', ['log']], ['config', 'конфиг', ['config']], ['time', 'время', ['time']], ['misc', 'прочее', ['warn', 'info', 'toast']]];
const SB_SIDE = { user: 'юзербот', bot: 'бот', both: 'юзербот + бот' };
const SB_STATUS = { idle: ['не запущена', ''], running: ['работает', 'ok'], error: ['ошибка', 'bad'], stalled: ['завис', 'bad'], dead: ['остановлена', 'bad'] };

const sbTime = (ts) => new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour12: false });
const sbDT = (ts) => new Date(ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });
function sbDur(sec) {
  sec = Math.round(Math.abs(sec));
  const out = [];
  for (const [u, n] of [['д', 86400], ['ч', 3600], ['мин', 60], ['с', 1]]) if (sec >= n) { out.push(Math.floor(sec / n) + ' ' + u); sec %= n; }
  return out.slice(0, 3).join(' ') || '0 с';
}
const sbHue = (id) => (350 + Math.abs(Number(id) * 2654435761 % 55)) % 360;   // тёплые оттенки
const sbLocalInput = (ts) => { const d = new Date(ts * 1000), p = (n) => String(n).padStart(2, '0'); return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`; };

function sbField(label, control, hint) {
  return h('label', { class: 'sb-fld' }, h('span', { class: 'wi-lab' }, label), control, hint ? h('span', { class: 'wi-hint' }, hint) : null);
}
function sbSelect(options, value, onchange) {                         // options: [[value, label]]
  const el = h('select', { class: 'wi-field' }, options.map(([v, l]) => h('option', { value: String(v) }, l)));
  if (value !== undefined && value !== null) el.value = String(value);
  if (onchange) el.addEventListener('change', () => onchange(el.value));
  return el;
}
function sbInput(value, onchange, o) {
  o = o || {};
  const el = h(o.area ? 'textarea' : 'input', { class: 'wi-field', type: o.area ? null : (o.type || 'text'), rows: o.area ? (o.rows || 3) : null, placeholder: o.placeholder || '', spellcheck: 'false' });
  el.value = value === undefined || value === null ? '' : value;
  if (onchange) el.addEventListener('input', () => onchange(el.value));
  return el;
}

/* ------------------------------------------------------------------ *
 * сама панель
 * ------------------------------------------------------------------ */

function sbCreateDock(opts) {
  const key = opts.key;
  const S = { state: null, status: 'idle', statusText: '', msgRev: 0, logRev: 0, rev: 0, chat: 'saved', sender: null, replyTo: null, attach: null,
    tab: 'log', feed: new Map(), feedOrder: [], msgEls: new Map(), journal: new Map(), jEls: new Map(), filters: new Set(SB_GROUPS.map((g) => g[0])), search: '',
    started: false, destroyed: false, open: false, timer: 0, busy: false, fails: 0, auto: false, autoTimer: 0, sigs: {}, dirtyTab: false, userPickedSender: false };

  /* ---------- каркас ---------- */
  const statusPill = h('span', { class: 'sb-pill' }, 'не запущена');
  const statusLine = h('div', { class: 'sb-statusline', hidden: true });
  const btnRun = h('button', { class: 'btn small primary', type: 'button', title: 'загрузить код из редактора (данные и время сохраняются) · Ctrl+Enter в редакторе', onclick: () => run(false) }, '▶ запустить');
  const btnRestart = h('button', { class: 'btn small', type: 'button', title: 'как перезапуск юзербота: cache очищается, db возвращается к записанному на диск', onclick: () => run(true) }, '↻ перезапуск');
  const btnStop = h('button', { class: 'btn small', type: 'button', title: 'остановить поток песочницы', onclick: () => act(async () => { await api('/sandbox/stop', { key }); poll(); }) }, '■ стоп');
  const autoBox = h('input', { type: 'checkbox', class: 'wi-cb' });
  autoBox.addEventListener('change', () => { S.auto = autoBox.checked; if (S.auto) run(false, true); });
  const autoLbl = h('label', { class: 'wi-check', title: 'при каждой правке кода (через секунду) перезагружать модуль в песочнице' }, autoBox, h('span', {}, 'автообновление'));
  const head = h('div', { class: 'sb-head' }, h('div', { class: 'sb-title' }, h('b', {}, 'Sandbox'), statusPill), h('div', { class: 'sb-actions' }, autoLbl, btnRun, btnRestart, btnStop,
    h('button', { class: 'wi-ico', type: 'button', title: 'свернуть', onclick: () => setOpen(false) }, '×')));

  const chatSel = h('select', { class: 'wi-field sb-chatsel', 'aria-label': 'чат' });
  chatSel.addEventListener('change', () => { S.chat = chatSel.value; S.userPickedSender = false; applyChatDefaults(); renderChat(); });
  const sideTag = h('span', { class: 'tag' }, '');
  const clearBtn = h('button', { class: 'wi-ico', type: 'button', title: 'очистить ленту чатов', onclick: () => act(async () => {
    await api('/sandbox/clear', { key, what: 'feed' }); S.feed.clear(); S.feedOrder = []; S.msgEls.clear(); S.msgRev = 0; renderChat(); poll(true); }) }, '⌫');
  const chatHead = h('div', { class: 'sb-chathead' }, chatSel, sideTag, h('span', { class: 'grow' }), clearBtn);
  const list = h('div', { class: 'sb-list' });
  const toasts = h('div', { class: 'sb-toasts' });

  /* композер */
  const senderSel = h('select', { class: 'wi-field sb-sendersel', title: 'от чьего имени отправить сообщение / нажать кнопку', 'aria-label': 'отправитель' });
  senderSel.addEventListener('change', () => { S.sender = Number(senderSel.value); S.userPickedSender = true; });
  const replyChip = h('div', { class: 'sb-replychip', hidden: true });
  const input = h('textarea', { class: 'wi-field sb-input', rows: 1, placeholder: 'сообщение…', title: 'Enter — отправить, Shift+Enter — перенос строки', spellcheck: 'false' });
  input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 110) + 'px'; });
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); } });
  const attachChip = h('div', { class: 'sb-attachchip', hidden: true });
  const attachBox = h('div', { class: 'sb-attachbox', hidden: true });
  const cmdChips = h('div', { class: 'sb-cmds' });
  const composer = h('div', { class: 'sb-composer' }, cmdChips, h('div', { class: 'sb-crow' }, senderSel, replyChip), attachChip, attachBox,
    h('div', { class: 'sb-crow' }, h('button', { class: 'wi-ico', type: 'button', title: 'приложить файл', onclick: () => toggleAttach() }, '⌘'), input,
      h('button', { class: 'btn small primary', type: 'button', onclick: () => send() }, 'отправить')));
  const chatCol = h('div', { class: 'sb-chat' }, chatHead, list, toasts, composer);

  /* инспектор */
  const TABS = [['log', 'журнал'], ['keys', 'ключи'], ['config', 'конфиг'], ['time', 'время'], ['events', 'события'], ['handlers', 'обработчики'], ['people', 'участники'], ['pages', 'страницы']];
  const tabBtns = new Map();
  const tabBar = h('div', { class: 'sb-tabs' }, TABS.map(([id, label]) => {
    const b = h('button', { type: 'button', class: id === S.tab ? 'on' : '', onclick: () => setTab(id) }, label);
    tabBtns.set(id, b);
    return b;
  }));
  tabBar.addEventListener('wheel', (e) => {                      // колесо мыши прокручивает вкладки по горизонтали
    if (tabBar.scrollWidth <= tabBar.clientWidth) return;
    const d = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
    tabBar.scrollBy({ left: d, behavior: 'auto' });
    e.preventDefault();
  }, { passive: false });
  const tabEdges = () => {
    tabBar.classList.toggle('can-l', tabBar.scrollLeft > 4);
    tabBar.classList.toggle('can-r', tabBar.scrollLeft + tabBar.clientWidth < tabBar.scrollWidth - 4);
  };
  tabBar.addEventListener('scroll', tabEdges, { passive: true });
  if (typeof ResizeObserver === 'function') new ResizeObserver(tabEdges).observe(tabBar);
  const inspBody = h('div', { class: 'sb-insp-body' });
  const inspCol = h('div', { class: 'sb-insp' }, tabBar, inspBody);

  const notice = h('div', { class: 'wi-hint sb-notice' }, 'Изолированы Telegram, db, cache, конфиги и часы. Файлы, сеть и сторонние библиотеки модуля работают по-настоящему.');
  const body = h('div', { class: 'sb-body' }, chatCol, inspCol);
  // телефон: песочница — на весь экран, чат и инспектор по очереди
  const mseg = mSeg([['chat', 'чат'], ['insp', 'журнал и данные']], 'chat', (id) => { body.classList.toggle('mv-insp', id === 'insp'); if (id === 'insp') renderTab(); });
  mseg.el.classList.add('m-only', 'sb-mseg');
  const el = h('div', { class: 'glass sb-dock', hidden: true }, head, statusLine, mseg.el, body, notice);

  /* ---------- журнал (создаётся один раз и живёт между вкладками) ---------- */
  const logList = h('div', { class: 'sb-log' });
  const filterRow = h('div', { class: 'sb-filters' }, SB_GROUPS.map(([id, label]) => h('button', { type: 'button', class: 'chip small on', 'data-g': id, onclick: (e) => {
    if (S.filters.has(id)) S.filters.delete(id); else S.filters.add(id);
    e.currentTarget.classList.toggle('on', S.filters.has(id)); applyFilters(); } }, label)));
  const searchIn = h('input', { class: 'wi-field sb-search', placeholder: 'поиск по журналу', spellcheck: 'false' });
  searchIn.addEventListener('input', () => { S.search = searchIn.value.toLowerCase(); applyFilters(); });
  const clearLog = h('button', { class: 'wi-ico', type: 'button', title: 'очистить журнал', onclick: () => act(async () => {
    await api('/sandbox/clear', { key, what: 'log' }); S.journal.clear(); S.jEls.clear(); logList.replaceChildren(); S.logRev = 0; }) }, '⌫');
  const logWrap = h('div', { class: 'sb-logwrap' }, h('div', { class: 'sb-logbar' }, searchIn, clearLog), filterRow, logList);

  const groupOf = (kind) => (SB_GROUPS.find((g) => g[2].includes(kind)) || [''])[0];
  function jVisible(e, elx) {
    if (!S.filters.has(groupOf(e.kind))) return false;
    return !S.search || elx._txt.includes(S.search);
  }
  function applyFilters() { S.journal.forEach((e, id) => { const x = S.jEls.get(id); if (x) x.hidden = !jVisible(e, x); }); }

  function jFill(x, e) {
    x.className = 'sb-j k-' + e.kind + (e.internal ? ' internal' : '') + (e.ok === false ? ' bad' : '') + (e.phase === 'run' ? ' run' : '');
    x._txt = ((e.text || '') + ' ' + (e.fn || '') + ' ' + (e.key || '')).toLowerCase();
    const body = h('div', { class: 'sb-jb' });
    body.appendChild(h('div', { class: 'sb-jt' }, e.text || ''));
    const tags = [];
    if (e.fn && e.kind !== 'handler') tags.push(h('span', { class: 'sb-tag' }, 'в ' + e.fn));
    if (e.kind === 'handler' && e.phase === 'end' && e.ms !== undefined) tags.push(h('span', { class: 'sb-tag' }, e.ms + ' мс'));
    if (e.kind === 'handler' && e.vdelta) tags.push(h('span', { class: 'sb-tag' }, '◷ ' + sbDur(e.vdelta)));
    if (e.kind === 'error') tags.push(h('span', { class: 'sb-tag' }, `on_error: ${e.handlers || 0} ${plural(e.handlers || 0, 'обработчик', 'обработчика', 'обработчиков')}`));
    if (tags.length) body.appendChild(h('div', { class: 'sb-jtags' }, tags));
    if (e.kind === 'error' && (e.frames || []).length) {
      body.appendChild(h('div', { class: 'sb-frames' }, e.frames.map((f) => h('button', { type: 'button', class: 'sb-frame', title: 'перейти к строке в редакторе',
        onclick: () => opts.onGoto(f.line, true) }, h('span', { class: 'n' }, 'строка ' + f.line), h('span', { class: 'f' }, f.func), h('code', {}, f.code || '')))));
    }
    if (e.kind === 'error' && e.tb && e.tb !== e.text) body.appendChild(h('div', { class: 'sb-jtags' }, h('span', { class: 'sb-tag' }, e.tb)));
    x.replaceChildren(h('span', { class: 'sb-jtime', title: 'виртуальное время' }, sbTime(e.vt || e.t)), h('span', { class: 'sb-ji' }, SB_ICON[e.kind] || '·'), body);
  }

  function upsertJournal(e) {
    S.journal.set(e.id, e);
    let x = S.jEls.get(e.id);
    const fresh = !x;
    if (fresh) { x = h('div', {}); S.jEls.set(e.id, x); }
    jFill(x, e);
    x.hidden = !jVisible(e, x);
    if (fresh) {
      const near = logList.scrollHeight - logList.scrollTop - logList.clientHeight < 60;
      logList.appendChild(x);
      if (!S.firstPoll && !x.hidden && !REDUCED.matches) { x.classList.add('sb-jnew'); setTimeout(() => x.classList.remove('sb-jnew'), 500); }
      if (logList.children.length > 700) { const first = logList.firstChild; for (const [id, node] of S.jEls) if (node === first) { S.jEls.delete(id); S.journal.delete(id); break; } first.remove(); }
      if (near) logList.scrollTop = logList.scrollHeight;
    }
  }

  /* ---------- лента чата ---------- */

  const owner = () => (S.state ? S.state.settings.owner_id : 0);
  const chatOf = (k) => (S.state ? S.state.chats.find((c) => c.key === k) : null);
  const userOf = (id) => (S.state ? S.state.users.find((u) => u.id === id) : null);

  function inlinePrompt(box, placeholder, onOk) {
    box.querySelector('.sb-inp')?.remove();
    const f = h('input', { class: 'wi-field', placeholder, spellcheck: 'false', autocomplete: 'off' });
    const close = () => row.remove();
    const go = () => { const v = f.value; close(); onOk(v); };
    f.addEventListener('keydown', (ev) => { if (ev.key === 'Escape') close(); else if (ev.key === 'Enter') { ev.preventDefault(); go(); } });
    const row = h('div', { class: 'sb-inp' }, f, h('button', { class: 'btn small primary', type: 'button', onclick: go }, 'ок'), h('button', { class: 'btn small', type: 'button', onclick: close }, '×'));
    box.appendChild(row);
    f.focus();
  }

  function msgEl(e) {
    const mine = !!(e.sender && e.sender.id === owner());
    const isBot = !!(e.sender && S.state && e.sender.id === S.state.settings.bot.id);
    const box = h('div', { class: 'sb-msg' + (mine ? ' out' : ' in') + (e.deleted ? ' del' : '') + (e.closed ? ' closed' : ''), 'data-mid': e.mid });
    const meta = h('div', { class: 'sb-meta' });
    if (e.sender) meta.appendChild(h('span', { class: 'sb-name', style: `color:hsl(${sbHue(e.sender.id)} 75% 82%)` }, e.sender.name + (isBot ? ' · бот' : '')));
    meta.appendChild(h('span', { class: 'sb-mid' }, '#' + e.mid));
    if (e.via_bot) meta.appendChild(h('span', { class: 'sb-mid' }, 'форма'));
    if (e.pinned) meta.appendChild(h('span', { class: 'sb-mid' }, 'закреплено'));
    box.appendChild(meta);
    if (e.reply) {
      const r = h('button', { type: 'button', class: 'sb-reply', onclick: () => { const t = S.msgEls.get(e.chat + ':' + e.reply.mid); if (t) { t.scrollIntoView({ block: 'center', behavior: 'smooth' }); t.classList.add('flash'); setTimeout(() => t.classList.remove('flash'), 900); } } },
        h('b', {}, e.reply.name || ('#' + e.reply.mid)), h('span', {}, e.reply.text || ''));
      box.appendChild(r);
    }
    if (e.media) box.appendChild(h('div', { class: 'sb-media' }, h('span', { class: 'k' }, e.media.kind), h('span', {}, e.media.name), h('span', { class: 'wi-hint' }, fmtSize(e.media.size))));
    if ((e.nodes || []).length) box.appendChild(h('div', { class: 'sb-text' }, e.nodes.map(inl)));
    else if (!e.media) box.appendChild(h('div', { class: 'sb-text wi-hint' }, e.deleted ? 'сообщение удалено' : '(пусто)'));
    if (e.deleted && (e.nodes || []).length) box.appendChild(h('div', { class: 'wi-hint' }, 'удалено'));
    if (e.edited) box.appendChild(h('div', { class: 'sb-edited' }, 'изменено'));
    if ((e.rows || []).length && !e.closed && !e.deleted) {
      const clicker = () => S.sender || owner();
      box.appendChild(h('div', { class: 'sb-kb' }, e.rows.map((r) => h('div', { class: 'sb-krow' }, r.map((b) => {
        if (b.input !== undefined) {
          return h('button', { class: 'sb-kbtn input' + (b.style ? ' st-' + b.style : ''), type: 'button', title: 'кнопка «введите значение»', onclick: () => inlinePrompt(box, (b.hint && b.hint !== 'Enter value...') ? b.hint : 'значение',
            (v) => act(async () => { await api('/sandbox/input', { key, form: e.form, data: b.input, value: v, user: clicker() }); pollSoon(); })) }, b.text);
        }
        if (/^url:(https?|tg):/.test(b.data || '')) return h('a', { class: 'sb-kbtn' + (b.style ? ' st-' + b.style : ''), href: b.data.slice(4), target: '_blank', rel: 'noopener noreferrer', title: 'кнопка-ссылка' }, b.text + ' ↗');
        return h('button', { class: 'sb-kbtn' + (b.style ? ' st-' + b.style : ''), type: 'button', title: 'callback data: ' + b.data, onclick: () => act(async () => {
          if (e.form) await api('/sandbox/click', { key, form: e.form, data: b.data, user: clicker() });
          else await api('/sandbox/event', { key, kind: 'callback', chat: e.chat, sender: clicker(), data: b.data, message_id: e.mid });
          pollSoon(); }) }, b.text);
      })))));
    }
    const tools = h('div', { class: 'sb-tools' },
      h('button', { type: 'button', title: 'ответить на это сообщение', onclick: () => setReply(e) }, '↩'),
      h('button', { type: 'button', title: 'нажать callback-кнопку на этом сообщении (@decorators.callback)', onclick: () => inlinePrompt(box, 'callback data',
        (v) => v && act(async () => { await api('/sandbox/event', { key, kind: 'callback', chat: e.chat, sender: S.sender || owner(), data: v, message_id: e.mid }); pollSoon(); })) }, '⚡'),
      h('button', { type: 'button', title: 'скопировать текст', onclick: () => { navigator.clipboard?.writeText(e.text || ''); toast('скопировано', 'ok'); } }, '⧉'));
    box.appendChild(tools);
    return box;
  }

  function upsertFeed(e) {
    if (e.kind === 'toast') { showToast(e); return; }
    if (e.kind !== 'msg') return;
    const first = !S.feed.has(e.id);
    S.feed.set(e.id, e);
    if (first) S.feedOrder.push(e.id);
    if (e.chat !== S.chat) { const x = S.msgEls.get(e.chat + ':' + e.mid); if (x) x.remove(); S.msgEls.delete(e.chat + ':' + e.mid); return; }
    placeMsg(e, first);
  }

  function placeMsg(e, first) {
    const k = e.chat + ':' + e.mid;
    const near = list.scrollHeight - list.scrollTop - list.clientHeight < 80;
    const x = msgEl(e);
    const old = S.msgEls.get(k);
    list.querySelector('.sb-empty')?.remove();
    if (old && old.isConnected) {
      old.replaceWith(x);
      if (!S.firstPoll && !S.rendering) { x.classList.add('upd'); setTimeout(() => x.classList.remove('upd'), 700); }
    } else list.appendChild(x);
    S.msgEls.set(k, x);
    if (first && !S.firstPoll && !S.rendering) {                  // как в Emulate: блок вырастает, текст проявляется по буквам
      growIn(x, near ? (ms) => { const t0 = performance.now(); const f = () => { list.scrollTop = list.scrollHeight; if (performance.now() - t0 < ms + 40) requestAnimationFrame(f); }; f(); } : null);
      const tx = x.querySelector('.sb-text');
      if (tx && !x.classList.contains('out') && !x.classList.contains('del')) revealChars(tx, 0);
    }
    if (first && near) list.scrollTop = list.scrollHeight;
  }

  function renderChat() {
    list.replaceChildren();
    S.msgEls.clear();
    let any = false;
    S.rendering = true;
    for (const id of S.feedOrder) {
      const e = S.feed.get(id);
      if (e && e.kind === 'msg' && e.chat === S.chat) { guard('msg', () => placeMsg(e, false)); any = true; }
    }
    S.rendering = false;
    if (!any) list.appendChild(h('div', { class: 'sb-empty' }, 'Здесь появятся сообщения. Отправьте команду модуля или сообщение от лица участника.'));
    else if (!REDUCED.matches) list.animate([{ opacity: 0, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 260, easing: 'cubic-bezier(.22,1,.36,1)' });
    list.scrollTop = list.scrollHeight;
    const c = chatOf(S.chat);
    sideTag.textContent = c ? 'видит: ' + SB_SIDE[c.side] : '';
    renderCmds();
  }

  function showToast(e) {
    if (e.id <= (S.lastToast || 0)) return;
    S.lastToast = e.id;
    if (S.firstPoll) return;
    const t = h('div', { class: 'sb-toast ' + (e.level || '') }, e.text);
    toasts.appendChild(t);
    setTimeout(() => { t.classList.add('out'); setTimeout(() => t.remove(), 280); }, 4000);
  }

  function renderChats() {
    if (!S.state) return;
    const cur = S.chat;
    chatSel.replaceChildren(...S.state.chats.map((c) => h('option', { value: c.key }, c.title)));
    if (!S.state.chats.some((c) => c.key === cur)) S.chat = S.state.chats[0].key;
    chatSel.value = S.chat;
    const prevSender = S.sender;
    senderSel.replaceChildren(...S.state.users.map((u) => h('option', { value: u.id }, u.name + (u.id === owner() ? ' (вы)' : ''))));
    if (prevSender && S.state.users.some((u) => u.id === prevSender)) S.sender = prevSender; else { S.sender = owner(); S.userPickedSender = false; }
    applyChatDefaults();
  }

  function applyChatDefaults() {
    const c = chatOf(S.chat);
    if (!c || !S.state) return;
    if (!S.userPickedSender) {
      S.sender = (c.type === 'private' && !c.saved && c.peer && c.peer !== owner() && c.side !== 'bot') ? c.peer : (c.side === 'bot' && c.peer && c.peer !== owner() ? c.peer : owner());
    }
    senderSel.value = String(S.sender);
    sideTag.textContent = 'видит: ' + SB_SIDE[c.side];
  }

  function renderCmds() {
    const st = S.state, c = chatOf(S.chat);
    cmdChips.replaceChildren();
    if (!st || !c || !st.handlers.commands) return;
    const items = [];
    if (c.side !== 'bot') st.handlers.commands.forEach((x) => items.push([st.settings.prefix + x.alias, x.doc]));
    if (c.side !== 'user') st.handlers.bot_commands.forEach((x) => items.push(['/' + x.alias, x.doc]));
    items.slice(0, 14).forEach(([text, doc]) => cmdChips.appendChild(h('button', { type: 'button', class: 'chip small', title: doc || '', onclick: () => { input.value = text + ' '; input.focus(); input.dispatchEvent(new Event('input')); } }, text)));
  }

  /* ответ и вложение */
  function setReply(e) {
    S.replyTo = e.mid;
    replyChip.hidden = false; replyChip.classList.remove('sb-pop'); void replyChip.offsetWidth; replyChip.classList.add('sb-pop');
    replyChip.replaceChildren(h('span', {}, 'ответ на '), h('b', {}, (e.sender ? e.sender.name : '') + ' #' + e.mid), h('span', { class: 'wi-hint' }, ' ' + (e.text || '').slice(0, 50)),
      h('button', { class: 'wi-ico', type: 'button', onclick: clearReply }, '×'));
    input.focus();
  }
  function clearReply() { S.replyTo = null; replyChip.hidden = true; }

  function showBox(on) {
    if (on === !attachBox.hidden) return;
    if (REDUCED.matches) { attachBox.hidden = !on; return; }
    if (on) { attachBox.hidden = false; growIn(attachBox); } else collapseHide(attachBox);
  }
  function collapseHide(node) {                                    // как collapseOut, но узел остаётся (его только прячем)
    const h0 = node.getBoundingClientRect().height;
    node.style.overflow = 'hidden';
    const a = node.animate([{ height: h0 + 'px', opacity: 1 }, { height: '0px', opacity: 0, paddingTop: '0px', paddingBottom: '0px' }], { duration: 300, easing: 'cubic-bezier(.22,1,.36,1)' });
    a.onfinish = a.oncancel = () => { node.hidden = true; node.style.overflow = ''; };
  }
  function clearAttach() { S.attach = null; attachChip.hidden = true; showBox(false); }
  function setAttach(a) {
    S.attach = a;
    attachChip.hidden = false; attachChip.classList.remove('sb-pop'); void attachChip.offsetWidth; attachChip.classList.add('sb-pop');
    attachChip.replaceChildren(h('span', { class: 'k' }, a.kind), h('span', {}, a.name), h('span', { class: 'wi-hint' }, fmtSize(a.size)), h('button', { class: 'wi-ico', type: 'button', onclick: clearAttach }, '×'));
  }
  function toggleAttach() {
    if (!attachBox.hidden) { showBox(false); return; }
    const kinds = [['document', 'документ'], ['photo', 'фото'], ['voice', 'голосовое'], ['video', 'видео'], ['video_note', 'кружок'], ['audio', 'аудио'], ['gif', 'gif'], ['sticker', 'стикер']];
    const kind = sbSelect(kinds, 'document');
    const fileIn = h('input', { type: 'file', class: 'sb-file', hidden: true, tabindex: '-1' });
    const fileName = h('span', { class: 'sb-filename' }, 'файл не выбран');
    const fileBtn = h('button', { class: 'btn small', type: 'button', onclick: () => fileIn.click() }, 'выбрать файл…');
    const fileBox = h('div', { class: 'sb-filebox' }, fileBtn, fileName, fileIn);
    const nameIn = sbInput('report.txt', null, { placeholder: 'имя файла' });
    const sizeIn = sbInput('2048', null, { type: 'number', placeholder: 'размер, байт' });
    fileIn.addEventListener('change', () => {
      const f = fileIn.files[0];
      if (!f) return;
      if (f.size > 1024 * 1024) { toast('для Sandbox файл — не больше 1 МБ', 'error'); fileIn.value = ''; fileName.textContent = 'файл не выбран'; return; }
      fileName.textContent = f.name; fileName.title = f.name;
      nameIn.value = f.name; sizeIn.value = f.size;
      kind.value = f.type.startsWith('image/') ? 'photo' : f.type.startsWith('video/') ? 'video' : f.type.startsWith('audio/') ? 'audio' : 'document';
      const rd = new FileReader();
      rd.onload = () => { fileIn._b64 = String(rd.result).split(',')[1] || ''; };
      rd.readAsDataURL(f);
    });
    const ok = () => { setAttach({ kind: kind.value, name: nameIn.value.trim() || 'file', size: Number(sizeIn.value) || 0, content_b64: fileIn._b64 || undefined }); showBox(false); };
    attachBox.replaceChildren(h('div', { class: 'wi-hint' }, 'Выберите настоящий файл (до 1 МБ) — модуль сможет его прочитать — или задайте «виртуальный»: только имя, тип и размер.'),
      h('div', { class: 'sb-grid2' }, sbField('Тип', kind), sbField('Файл', fileBox), sbField('Имя', nameIn), sbField('Размер, байт', sizeIn)),
      h('div', { class: 'actions' }, h('button', { class: 'btn small', type: 'button', onclick: () => showBox(false) }, 'отмена'), h('button', { class: 'btn small primary', type: 'button', onclick: ok }, 'приложить')));
    showBox(true);
  }

  async function send() {
    const text = input.value;
    if (!text.trim() && !S.attach) return;
    if (S.status !== 'running') { toast('сначала запустите песочницу', 'error'); return; }
    await act(async () => {
      const body = { key, chat: S.chat, sender: S.sender || owner(), text, reply_to: S.replyTo, media: S.attach || undefined };
      input.value = ''; input.style.height = 'auto'; clearReply(); clearAttach();
      await api('/sandbox/send', body);
      pollSoon();
    });
  }

  /* ---------- состояние и опрос ---------- */

  function renderStatus() {
    const [label, cls] = SB_STATUS[S.status] || SB_STATUS.idle;
    statusPill.textContent = label;
    statusPill.className = 'sb-pill ' + cls;
    statusLine.hidden = !S.statusText;
    statusLine.textContent = S.statusText || '';
    statusLine.className = 'sb-statusline ' + (S.status === 'running' ? '' : 'bad');
    btnStop.disabled = S.status === 'dead' || S.status === 'idle';
  }

  function onState() {
    const first = !S.sigs.chats;
    const sChats = JSON.stringify([S.state.chats, S.state.users]);
    if (S.sigs.chats !== sChats) { S.sigs.chats = sChats; renderChats(); if (first) renderChat(); }
    const sCmd = JSON.stringify([S.state.handlers.commands, S.state.handlers.bot_commands, S.state.settings.prefix]);
    if (S.sigs.cmd !== sCmd) { S.sigs.cmd = sCmd; renderCmds(); }
    refreshTab(false);
  }

  /* любая ошибка отрисовки изолируется: остальное окно продолжает работать */
  function guard(what, fn) { try { fn(); } catch (e) { console.error('sandbox ' + what, e); } }

  /* страховка: строка ввода и лента всегда на месте, даже если что-то пошло не так при перерисовке */
  function ensureComposer() {
    if (composer.parentNode !== chatCol) chatCol.appendChild(composer);
    if (list.parentNode !== chatCol) chatCol.insertBefore(list, composer);
    if (chatHead.parentNode !== chatCol) chatCol.insertBefore(chatHead, chatCol.firstChild);
    if (chatCol.parentNode !== body) body.insertBefore(chatCol, body.firstChild);
    if (inspCol.parentNode !== body) body.appendChild(inspCol);
    if (inspCol.firstChild !== tabBar) inspCol.insertBefore(tabBar, inspCol.firstChild);
    if (inspBody.parentNode !== inspCol) inspCol.appendChild(inspBody);
  }

  async function poll(full) {
    if (S.destroyed || !opts.alive()) { S.destroyed = true; return; }
    if (S.busy) return;
    S.busy = true;
    try {
      const q = `key=${encodeURIComponent(key)}&msg=${S.msgRev}&log=${S.logRev}&rev=${full ? 0 : S.rev}` + (full ? '&full=1' : '');
      const r = await api('/sandbox/poll?' + q);
      S.fails = 0;
      const initial = S.msgRev === 0 && S.logRev === 0;
      S.firstPoll = initial;
      r.feed.forEach((e) => guard('feed', () => upsertFeed(e)));
      S.firstPoll = false;
      r.journal.forEach((e) => guard('journal', () => upsertJournal(e)));
      S.msgRev = r.msg_rev; S.logRev = r.log_rev;
      S.status = r.status; S.statusText = r.status_text;
      if (r.state) { S.state = r.state; S.rev = r.rev; guard('state', onState); }
      guard('status', renderStatus);
      ensureComposer();
    } catch (e) {
      S.fails++;
      if (/не запущена/.test(e.message)) { S.status = 'idle'; renderStatus(); S.fails = 0; }
    } finally { S.busy = false; }
  }

  function loop() {
    clearTimeout(S.timer);
    if (S.destroyed || !S.open) return;
    poll().finally(() => { S.timer = setTimeout(loop, document.hidden ? 3000 : (S.fails > 2 ? 3000 : 700)); });
  }
  const pollSoon = () => { clearTimeout(S.timer); S.timer = setTimeout(loop, 60); };

  async function run(restart, quiet) {
    let source;
    try { source = opts.getSource(); } catch (e) { if (!quiet) toast(e.message, 'error'); return; }
    await act(async () => {
      btnRun.disabled = btnRestart.disabled = true;
      try {
        const r = await api('/sandbox/start', { key, source, restart });
        S.started = true;
        if (!r.result.ok) {
          const er = r.result.error || {};
          // автообновление во время набора: SyntaxError — ожидаемое состояние недописанного кода, без уведомлений и подсветки
          if (!(quiet && er.type === 'SyntaxError')) {
            toast(`${er.type || 'ошибка'}: ${er.msg || ''}`, 'error');
            if (er.line) opts.onGoto(er.line, true);
          }
        } else {
          opts.onGoto(0, false);
          if (restart) toast('перезапущено: cache очищен, db — как на диске', 'ok');
        }
      } finally { btnRun.disabled = btnRestart.disabled = false; }
      await poll(true);
    });
  }

  function setOpen(on) {
    if (on === S.open && !el.hidden === on) return;
    S.open = on;
    document.documentElement.classList.toggle('sb-open', on);
    if (on) { el.hidden = false; if (!mIsPhone()) growIn(el); } else if (REDUCED.matches || mIsPhone()) el.hidden = true; else collapseHide(el);
    opts.onToggle(on);
    if (on) {
      if (!S.started) { S.started = true; run(false); }
      renderTab();
      loop();
    } else clearTimeout(S.timer);
  }

  function notifyChange() {
    if (!S.auto || !S.open) return;
    clearTimeout(S.autoTimer);
    S.autoTimer = setTimeout(() => run(false, true), 1200);
  }

  /* ---------- вкладки инспектора ---------- */

  function setTab(id) {
    S.tab = id;
    tabBtns.forEach((b, k) => b.classList.toggle('on', k === id));
    tabBtns.get(id)?.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: REDUCED.matches ? 'auto' : 'smooth' });
    renderTab(true);
  }

  function renderTab(animate) {
    S.dirtyTab = false;
    const b = inspBody, keep = b.scrollTop;
    const put = (node) => {
      b.replaceChildren(node);
      if (animate) { node.classList.add('sb-enter'); setTimeout(() => node.classList.remove('sb-enter'), 400); b.scrollTop = 0; } else b.scrollTop = keep;
    };
    if (S.tab === 'log') { put(logWrap); logList.scrollTop = logList.scrollHeight; S.sigs.tab = 'log'; return; }
    const builders = { keys: tabKeys, config: tabConfig, time: tabTime, events: tabEvents, handlers: tabHandlers, people: tabPeople, pages: tabPages };
    if (!S.state) { put(h('div', { class: 'sb-placeholder' }, 'Запустите песочницу — данные появятся здесь.')); return; }
    let node;
    try { node = builders[S.tab](S.state); } catch (e) {          // сбой одной вкладки не должен ломать остальной интерфейс
      console.error('sandbox tab', S.tab, e);
      node = h('div', { class: 'wi-note' }, 'Не удалось показать вкладку: ' + (e && e.message ? e.message : e), h('button', { class: 'btn small', type: 'button', style: 'margin-left:10px', onclick: () => renderTab(false) }, 'повторить'));
    }
    S.sigs.tab = tabSig(S.tab);
    put(node);
  }

  function tabSig(tab) {
    const st = S.state;
    if (!st) return '';
    switch (tab) {
      case 'keys': return JSON.stringify([st.db.map((r) => [r.key, r.value, r.reads, r.writes, r.deletes, r.misses]), st.cache.map((r) => [r.key, r.value, r.reads, r.writes, r.deletes, r.misses]), st.static]) + S.keysStore;
      case 'config': return JSON.stringify(st.config);
      case 'time': return JSON.stringify([st.clock.playing, st.clock.speed, st.clock.timers.length, st.cycles, st.clock.fired]);
      case 'events': case 'people': return JSON.stringify([st.chats, st.users, st.settings, st.module && st.module.module_name]);
      case 'handlers': return JSON.stringify([st.handlers, st.cycles]);
      case 'pages': return JSON.stringify(st.handlers.pages);
      default: return '';
    }
  }

  function editing() { const a = document.activeElement; return !!(a && inspBody.contains(a) && /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName)); }

  function refreshTab(force) {
    if (S.tab === 'log' || !S.state) return;
    if (S.tab === 'time') tickClock();
    const sig = tabSig(S.tab);
    if (!force && sig === S.sigs.tab) return;
    if (!force && editing()) { S.dirtyTab = true; return; }
    renderTab();
  }
  inspBody.addEventListener('focusout', () => setTimeout(() => { if (S.dirtyTab && !editing()) refreshTab(true); }, 150));
  S.keysStore = 'db';

  /* ---------- вкладка «ключи»: db и cache ---------- */

  function openKeyEditor(store, row) {
    const isNew = !row;
    const nameIn = sbInput(row ? row.key : (S.state.module ? S.state.module.module_name + ':' : ''), null, { placeholder: 'полный ключ, например my_module:counter' });
    if (!isNew) nameIn.disabled = true;
    const valIn = sbInput(row ? row.json : '0', null, { area: true, rows: 9 });
    valIn.classList.add('mono');
    const err = h('div', { class: 'wi-err', hidden: true });
    wiDrawer(`${store} · ${isNew ? 'новый ключ' : row.key}`, [
      h('div', { class: 'wi-hint' }, 'Значение — JSON: число, строка в кавычках, список, словарь, true/false/null. Это правка «снаружи»: модуль увидит значение при следующем чтении.'),
      sbField('Ключ', nameIn, 'в db ключ модуля начинается с «MODULE_NAME:»'), sbField('Значение (JSON)', valIn), err], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => act(async () => {
        try { JSON.parse(valIn.value); } catch (e) { err.hidden = false; err.textContent = 'Не JSON: ' + e.message; return; }
        await api('/sandbox/data', { key, store, op: 'set', name: nameIn.value.trim(), value: valIn.value });
        close(); pollSoon();
      }) }, 'сохранить')]);
  }

  function tabKeys(st) {
    const store = S.keysStore;
    const rows = (store === 'db' ? st.db : st.cache).filter((r) => !r.config);
    const seg = h('div', { class: 'wi-seg' }, ['db', 'cache'].map((s2) => h('button', { type: 'button', class: s2 === store ? 'on' : '', onclick: () => { S.keysStore = s2; renderTab(); } }, s2)));
    const head = h('div', { class: 'sb-rowbar' }, seg,
      h('button', { class: 'btn small', type: 'button', onclick: () => openKeyEditor(store, null) }, '+ ключ'),
      store === 'db' ? h('button', { class: 'btn small', type: 'button', title: 'скопировать в песочницу данные этого модуля из настоящей базы (настоящая база не меняется)', onclick: () => act(async () => {
        const r = await api('/sandbox/data', { key, store: 'db', op: 'import' }); toast(`импортировано ключей: ${r.imported}`, 'ok'); pollSoon(); }) }, 'импорт из настоящей db') : null,
      h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/sandbox/data', { key, store, op: 'clear' }); pollSoon(); }) }, 'очистить'));
    const note = store === 'db'
      ? 'db как настоящая: значение записывается на «диск» (JSON) при каждом set(). Объект, изменённый без повторного set(), после «↻ перезапуск» вернётся к записанному.'
      : 'cache живёт только в памяти — «↻ перезапуск» его очищает.';
    const table = rows.length ? h('div', { class: 'tablewrap' }, h('table', { class: 'sb-table' },
      h('thead', {}, h('tr', {}, ['ключ', 'значение', 'тип', 'чт.', 'зап.', 'уд.', 'пром.', 'функция', ''].map((x) => h('th', {}, x)))),
      h('tbody', {}, rows.map((r) => h('tr', { class: r.present ? '' : 'absent' },
        h('td', { class: 'mono k' }, r.key, r.scope === 'global' ? h('span', { class: 'sb-tag' }, 'global') : null),
        h('td', { class: 'mono v', title: r.present ? 'нажмите, чтобы изменить' : 'ключа нет', onclick: () => r.present && openKeyEditor(store, r) }, r.present ? r.value : 'нет такого ключа (читался)'),
        h('td', {}, r.type || '—'), h('td', {}, r.reads), h('td', {}, r.writes), h('td', {}, r.deletes), h('td', {}, r.misses ? h('b', { class: 'warn' }, r.misses) : 0),
        h('td', { class: 'mono' }, r.fn || '—'),
        h('td', {}, r.present ? h('button', { class: 'wi-ico bad', type: 'button', title: 'удалить ключ', onclick: () => act(async () => { await api('/sandbox/data', { key, store, op: 'delete', name: r.key }); pollSoon(); }) }, '×') : null)))))) :
      h('div', { class: 'wi-note' }, `В ${store} пока нет ключей модуля.`);
    // «ключи в коде» — по тексту программы
    const mod = st.module ? st.module.module_name : '';
    const code = (st.static.keys || []).filter((k) => k.store === store);
    const seen = new Set(rows.filter((r) => r.reads || r.writes || r.deletes).map((r) => r.key));
    const codeBlock = h('details', { class: 'sb-details' }, h('summary', {}, `ключи в коде: ${code.length}`),
      code.length ? h('div', { class: 'sb-codekeys' }, code.map((k) => {
        const full = k.key === null ? null : (k.scope === 'global' ? k.key : `${mod}:${k.key}`);
        const used = full && !k.dynamic ? seen.has(full) : null;
        return h('button', { type: 'button', class: 'sb-ck', title: 'перейти к строке', onclick: () => opts.onGoto(k.line, false) },
          h('span', { class: 'n' }, 'стр. ' + k.line), h('span', { class: 'op' }, `${k.store}.${k.op}`), h('code', {}, k.key === null ? '(выражение)' : k.key),
          k.dynamic && k.key !== null ? h('span', { class: 'sb-tag' }, 'шаблон') : null, used === null ? null : h('span', { class: 'sb-tag ' + (used ? 'ok' : '') }, used ? 'использован' : 'не вызывался'));
      })) : h('div', { class: 'wi-hint' }, 'Обращений к ' + store + ' с явным ключом в коде нет.'));
    return h('div', { class: 'sb-pane' }, head, h('div', { class: 'wi-hint' }, note), table, codeBlock);
  }

  /* ---------- вкладка «конфиг» ---------- */

  function tabConfig(st) {
    const mk = (c) => {
      const send = (value) => act(async () => { await api('/sandbox/config', { key, config_key: c.key, op: 'edit', value }); pollSoon(); });
      let ctl;
      if (c.type === 'bool') ctl = toggle(!!c.value, (v) => send(v));
      else if (c.type === 'choice') ctl = sbSelect((c.options || []).map((o) => [o, o]), c.value, (v) => send(v));
      else if (c.type === 'choices') {
        const cur = new Set(c.value || []);
        ctl = h('div', { class: 'chips' }, (c.options || []).map((o) => h('button', { type: 'button', class: 'chip small' + (cur.has(o) ? ' on' : ''), onclick: () => { if (cur.has(o)) cur.delete(o); else cur.add(o); send([...cur]); } }, o)));
      } else {
        const f = sbInput(c.type === 'list' ? (c.value || []).join(', ') : c.value, null, { type: c.type === 'int' || c.type === 'float' ? 'number' : 'text' });
        const commit = () => {
          let v = f.value;
          if (c.type === 'int') v = parseInt(v, 10); else if (c.type === 'float') v = parseFloat(v); else if (c.type === 'list') v = v.split(',').map((x) => x.trim()).filter(Boolean);
          if ((c.type === 'int' || c.type === 'float') && Number.isNaN(v)) { toast('нужно число', 'error'); return; }
          send(v);
        };
        f.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); commit(); } });
        f.addEventListener('change', commit);
        ctl = f;
      }
      const valid = c.valid ? Object.entries(c.valid).map(([k2, v]) => `${k2}=${v}`).join(', ') : '';
      return h('div', { class: 'sb-cfg' }, h('div', { class: 'sb-cfg-h' }, h('b', { class: 'mono' }, c.key), h('span', { class: 'sb-tag' }, c.type), valid ? h('span', { class: 'sb-tag' }, valid) : null,
        h('button', { class: 'wi-txt', type: 'button', title: 'вернуть значение по умолчанию: ' + JSON.stringify(c.default), onclick: () => act(async () => { await api('/sandbox/config', { key, config_key: c.key, op: 'to_default' }); pollSoon(); }) }, 'по умолчанию')),
        c.description ? h('div', { class: 'wi-hint' }, c.description) : null, ctl);
    };
    return h('div', { class: 'sb-pane' }, h('div', { class: 'wi-hint' }, 'Конфиги, объявленные модулем через create_config. Правка здесь — то же, что .config в Telegram: модуль увидит новое значение через get_config.'),
      st.config.length ? st.config.map(mk) : h('div', { class: 'wi-note' }, 'Модуль пока не объявил ни одного конфига (create_config обычно вызывают в @on_start).'));
  }

  /* ---------- вкладка «время» ---------- */

  let clockOut = null, clockOff = null;
  function tickClock() {
    if (!clockOut || !S.state) return;
    const c = S.state.clock;
    clockOut.textContent = sbDT(c.now);
    clockOff.textContent = Math.abs(c.now - Date.now() / 1000) < 2 ? 'совпадает с настоящим' : `${c.now > Date.now() / 1000 ? 'впереди' : 'позади'} настоящего на ${sbDur(c.now - Date.now() / 1000)}`;
  }

  function tabTime(st) {
    const c = st.clock;
    const advance = (sec) => act(async () => { const r = await api('/sandbox/time', { key, op: 'advance', seconds: sec }); toast(`сработало таймеров: ${r.fired}` + (r.truncated ? ' (обрезано — слишком много)' : ''), r.truncated ? 'error' : 'ok'); pollSoon(); });
    clockOut = h('div', { class: 'sb-clock' }, sbDT(c.now));
    clockOff = h('div', { class: 'wi-hint' }, '');
    const presets = [['+10 с', 10], ['+1 мин', 60], ['+10 мин', 600], ['+1 ч', 3600], ['+6 ч', 21600], ['+1 д', 86400], ['+7 д', 604800]];
    const amount = sbInput('5', null, { type: 'number' });
    const unit = sbSelect([[1, 'секунд'], [60, 'минут'], [3600, 'часов'], [86400, 'дней']], 60);
    const dt = h('input', { class: 'wi-field', type: 'datetime-local', step: 1, value: sbLocalInput(c.now) });
    const speed = sbSelect([[1, '×1'], [10, '×10'], [60, '×60 (1 мин/с)'], [600, '×600'], [3600, '×3600 (1 ч/с)']], c.speed);
    const timers = c.timers.length ? c.timers.map((t) => h('div', { class: 'sb-timer' }, h('span', {}, 'через ' + sbDur(t.due - c.now)), h('span', { class: 'wi-hint' }, t.label || 'таймер'))) : [h('div', { class: 'wi-hint' }, 'Ожидающих таймеров нет.')];
    const cyc = Object.entries(st.cycles);
    const node = h('div', { class: 'sb-pane' },
      h('div', { class: 'wi-hint' }, 'Виртуальное время модуля: time.time(), datetime.now(), asyncio.sleep, циклы @cycle и таймауты (wait_message, форм) идут по этим часам, а не по настоящим.'),
      clockOut, clockOff,
      h('div', { class: 'wi-lab' }, 'Сдвинуть вперёд'), h('div', { class: 'chips' }, presets.map(([l, s2]) => h('button', { class: 'chip small', type: 'button', onclick: () => advance(s2) }, l))),
      h('div', { class: 'sb-rowbar' }, amount, unit, h('button', { class: 'btn small', type: 'button', onclick: () => { const n = Number(amount.value); if (!(n > 0)) { toast('введите число больше нуля', 'error'); return; } advance(n * Number(unit.value)); } }, 'сдвинуть')),
      h('div', { class: 'wi-lab' }, 'Установить момент'), h('div', { class: 'sb-rowbar' }, dt,
        h('button', { class: 'btn small', type: 'button', onclick: () => { const t = new Date(dt.value).getTime(); if (Number.isNaN(t)) { toast('укажите дату и время', 'error'); return; } act(async () => { await api('/sandbox/time', { key, op: 'set', ts: t / 1000 }); pollSoon(); }); } }, 'установить'),
        h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/sandbox/time', { key, op: 'set', ts: Date.now() / 1000 }); pollSoon(); }) }, '= сейчас')),
      h('div', { class: 'wi-lab' }, 'Ход времени'), h('div', { class: 'sb-rowbar' }, speed,
        h('button', { class: 'btn small' + (c.playing ? '' : ' primary'), type: 'button', onclick: () => act(async () => { await api('/sandbox/time', c.playing ? { key, op: 'pause' } : { key, op: 'play', speed: Number(speed.value) }); pollSoon(); }) }, c.playing ? '⏸ остановить' : '▶ пустить')),
      h('div', { class: 'wi-lab' }, 'Циклы (@cycle)'),
      cyc.length ? cyc.map(([name, x]) => h('div', { class: 'sb-timer' }, h('span', { class: 'mono' }, name + ' · ' + x.interval + (x.bot ? ' · бот' : '')),
        h('span', { class: 'wi-hint' }, `запусков: ${x.runs}` + (x.due ? ` · следующий через ${sbDur(Math.max(0, x.due - c.now))}` : '')))) : h('div', { class: 'wi-hint' }, 'Циклов нет.'),
      h('div', { class: 'wi-lab' }, 'Ожидающие таймеры'), timers,
      h('div', { class: 'wi-hint' }, 'time.sleep() не блокирует (в логе будет предупреждение). Таймауты из asyncio.wait_for и сторонних библиотек идут по настоящему времени.'));
    tickClock();
    return node;
  }

  /* ---------- вкладка «события»: готовые конструкции ---------- */

  const CHAT_ACTIONS = [['user_joined', 'вошёл в чат', 'user_joined'], ['user_added', 'добавлен в чат', 'user_added'], ['user_left', 'вышел из чата', 'user_left'],
    ['user_kicked', 'исключён из чата', 'user_kicked'], ['created', 'чат создан', 'created'], ['new_title', 'сменили название', 'new_title'], ['new_photo', 'сменили фото', 'new_photo'],
    ['new_pin', 'закрепили сообщение', 'new_pin'], ['unpin', 'открепили сообщение', 'unpin']];
  const ERR_TYPES = ['Exception', 'ValueError', 'KeyError', 'TypeError', 'RuntimeError', 'TimeoutError', 'ConnectionError', 'PermissionError', 'OSError', 'AttributeError', 'IndexError', 'ZeroDivisionError'];
  const ERR_PRESETS = [['Тайм-аут сети', 'TimeoutError', 'Превышено время ожидания ответа'], ['Ключ не найден', 'KeyError', "'user_id'"], ['Деление на ноль', 'ZeroDivisionError', 'division by zero'],
    ['Нет доступа', 'PermissionError', 'доступ запрещён'], ['Обрыв соединения', 'ConnectionError', 'Connection reset by peer'], ['Своя ошибка', 'Exception', 'что-то пошло не так']];

  function evCard(title, deco, desc, fields, preview, sendFn, open) {
    const pre = h('pre', { class: 'sb-evprev' });
    const upd = () => { pre.textContent = preview(); };
    const box = h('details', { class: 'sb-evcard', open: !!open }, h('summary', {}, h('b', {}, title), h('span', { class: 'sb-tag' }, deco)),
      h('div', { class: 'wi-hint' }, desc), h('div', { class: 'sb-evform' }, fields(upd)),
      h('div', { class: 'wi-lab' }, 'Что получит модуль'), pre,
      h('div', { class: 'actions' }, h('button', { class: 'btn small primary', type: 'button', onclick: () => { const body = sendFn(); if (body) act(async () => { await api('/sandbox/event', { key, ...body }); pollSoon(); }); } }, 'отправить событие')));
    upd();
    return box;
  }

  function tabEvents(st) {
    const ownerId = st.settings.owner_id;
    const chatOpts = st.chats.map((c) => [c.key, `${c.title} · ${SB_SIDE[c.side]}`]);
    const userOpts = st.users.map((u) => [u.id, u.name + (u.id === ownerId ? ' (вы)' : '')]);
    const chatById = (k) => st.chats.find((c) => c.key === k);
    const q = (s2) => JSON.stringify(s2);
    const mod = st.module ? st.module.module_name : 'my_module';
    const firstFn = (st.handlers.commands[0] || st.handlers.watchers[0] || { name: 'my_function' }).name;
    const groupChat = (st.chats.find((c) => c.type !== 'private') || st.chats[0]).key;
    const bob = (st.users.find((u) => u.id !== ownerId) || st.users[0]).id;
    const cards = [];

    // 1. служебное событие Telegram
    const a = { chat: groupChat, action: 'user_joined', user: bob, actor: ownerId, title: 'Новое название' };
    cards.push(evCard('Служебное событие Telegram', '@decorators.telegram_event', 'Вошёл, вышел, добавили, сменили название… — то, что приходит юзерботу как ChatAction.',
      (upd) => {
        const wrapUser = sbField('Участник', sbSelect(userOpts, a.user, (v) => { a.user = Number(v); upd(); }));
        const wrapActor = sbField('Кто добавил / исключил', sbSelect(userOpts, a.actor, (v) => { a.actor = Number(v); upd(); }));
        const wrapTitle = sbField('Название', sbInput(a.title, (v) => { a.title = v; upd(); }));
        const sync = () => { const act2 = a.action; wrapUser.hidden = !['user_joined', 'user_added', 'user_left', 'user_kicked', 'created'].includes(act2); wrapActor.hidden = !['user_added', 'user_kicked', 'created'].includes(act2); wrapTitle.hidden = !['new_title', 'created'].includes(act2); };
        const actSel = sbSelect(CHAT_ACTIONS.map(([v, l]) => [v, l]), a.action, (v) => { a.action = v; sync(); upd(); });
        sync();
        return [sbField('Чат', sbSelect(chatOpts, a.chat, (v) => { a.chat = v; upd(); })), sbField('Что произошло', actSel), wrapUser, wrapActor, wrapTitle];
      },
      () => {
        const c = chatById(a.chat) || {}, flags = CHAT_ACTIONS.map((x) => x[2]);
        const lines = [`event.chat_id == ${c.id}`, ...flags.map((f) => `event.${f}${f === 'new_title' ? '' : ''} == ${f === a.action ? (f === 'new_title' ? q(a.title) : 'True') : (f === 'new_title' ? 'None' : 'False')}`)];
        if (['user_joined', 'user_added', 'user_left', 'user_kicked', 'created'].includes(a.action)) lines.push(`event.user_id == ${a.user}`);
        if (['user_added', 'created'].includes(a.action)) lines.push(`event.added_by == ${a.actor}`);
        if (a.action === 'user_kicked') lines.push(`event.kicked_by == ${a.actor}`);
        return lines.join('\n');
      },
      () => ({ kind: 'chat_action', chat: a.chat, action: a.action, user: ['user_joined', 'user_added', 'user_left', 'user_kicked', 'created'].includes(a.action) ? a.user : null,
        actor: ['user_added', 'user_kicked', 'created'].includes(a.action) ? a.actor : null, title: ['new_title', 'created'].includes(a.action) ? a.title : '' }), true));

    // 2. событие участника для бота
    const m = { chat: groupChat, user: bob, old: 'left', neu: 'member', botEv: false };
    const STAT = ['creator', 'administrator', 'member', 'restricted', 'left', 'kicked'].map((x) => [x, x]);
    cards.push(evCard('Участник чата — для бота', '@bot.telegram_event', 'Изменился статус участника (chat_member) или самого бота (my_chat_member).',
      (upd) => [sbField('Чат', sbSelect(chatOpts, m.chat, (v) => { m.chat = v; upd(); })), sbField('Участник', sbSelect(userOpts, m.user, (v) => { m.user = Number(v); upd(); })),
        h('div', { class: 'sb-grid2' }, sbField('Был', sbSelect(STAT, m.old, (v) => { m.old = v; upd(); })), sbField('Стал', sbSelect(STAT, m.neu, (v) => { m.neu = v; upd(); }))),
        (() => { const cb = h('input', { type: 'checkbox', class: 'wi-cb' }); cb.addEventListener('change', () => { m.botEv = cb.checked; upd(); }); return h('label', { class: 'wi-check' }, cb, h('span', {}, 'это событие про самого бота (my_chat_member)')); })()],
      () => {
        const act2 = ['member', 'administrator', 'creator'];
        return [`event.chat_id == ${(chatById(m.chat) || {}).id}`, `event.old_status == ${q(m.old)}`, `event.new_status == ${q(m.neu)}`,
          `event.user_joined == ${!act2.includes(m.old) && act2.includes(m.neu) ? 'True' : 'False'}`, `event.user_left == ${act2.includes(m.old) && !act2.includes(m.neu) ? 'True' : 'False'}`,
          `event.is_bot_event == ${m.botEv ? 'True' : 'False'}`, `event.user_id == ${m.botEv ? st.settings.bot.id : m.user}`].join('\n');
      },
      () => ({ kind: 'bot_member', chat: m.chat, user: m.user, old_status: m.old, new_status: m.neu, is_bot_event: m.botEv })));

    // 3. callback
    const cb = { chat: 'saved', sender: ownerId, data: 'my_button', mid: '' };
    cards.push(evCard('Нажатие callback-кнопки', '@decorators.callback', 'Сырое нажатие inline-кнопки (не формы FRX — для форм кликайте по кнопкам в ленте).',
      (upd) => [sbField('Чат', sbSelect(chatOpts, cb.chat, (v) => { cb.chat = v; upd(); })), sbField('Кто нажал', sbSelect(userOpts, cb.sender, (v) => { cb.sender = Number(v); upd(); })),
        h('div', { class: 'sb-grid2' }, sbField('callback data', sbInput(cb.data, (v) => { cb.data = v; upd(); })), sbField('id сообщения (необязательно)', sbInput(cb.mid, (v) => { cb.mid = v; upd(); }, { type: 'number' })))],
      () => [`event.data == b${q(cb.data)}`, `event.sender_id == ${cb.sender}`, `event.chat_id == ${(chatById(cb.chat) || {}).id}`, `event.message_id == ${cb.mid || 'None'}`, 'await event.answer("…")  # всплывающее уведомление'].join('\n'),
      () => ({ kind: 'callback', chat: cb.chat, sender: cb.sender, data: cb.data, message_id: cb.mid === '' ? null : Number(cb.mid) })));

    // 4. ошибка
    const er = { type: 'TimeoutError', text: 'Превышено время ожидания ответа', fn: firstFn, bot: false };
    cards.push(evCard('Ошибка в модуле', '@decorators.on_error · @bot.on_error', 'Вызывает обработчики ошибок так, как если бы функция модуля упала. Настоящие ошибки обработчиков попадают сюда сами.',
      (upd) => {
        const typeSel = sbSelect(ERR_TYPES.map((x) => [x, x]), er.type, (v) => { er.type = v; upd(); });
        const textIn = sbInput(er.text, (v) => { er.text = v; upd(); });
        const preset = sbSelect([['', 'шаблон…'], ...ERR_PRESETS.map(([l], i) => [i, l])], '', (v) => { if (v === '') return; const p = ERR_PRESETS[Number(v)]; er.type = p[1]; er.text = p[2]; typeSel.value = p[1]; textIn.value = p[2]; upd(); });
        const cbx = h('input', { type: 'checkbox', class: 'wi-cb' }); cbx.addEventListener('change', () => { er.bot = cbx.checked; upd(); });
        return [sbField('Шаблон', preset), h('div', { class: 'sb-grid2' }, sbField('Тип ошибки', typeSel), sbField('Функция', sbInput(er.fn, (v) => { er.fn = v; upd(); }))), sbField('Текст ошибки', textIn),
          h('label', { class: 'wi-check' }, cbx, h('span', {}, 'для обработчиков бота (@bot.on_error)'))];
      },
      () => [`text == ${q(er.type + ': ' + er.text)}`, `context == {'module': ${q(mod)}, 'function': ${q(er.fn)}}`].join('\n'),
      () => ({ kind: 'error', text: er.text, exc_type: er.type, function: er.fn, bot: er.bot })));

    // 5. загрузка другого модуля
    const lm = { module_name: 'other_module', name: 'Другой модуль', version: '2.0', author: 'someone', description: 'Модуль, который только что загрузили' };
    cards.push(evCard('Загружен модуль', '@decorators.on_load', 'on_load срабатывает при загрузке любого модуля и получает его описание — так модуль может узнать про соседей.',
      (upd) => {
        const f = { module_name: sbInput(lm.module_name, (v) => { lm.module_name = v; upd(); }), name: sbInput(lm.name, (v) => { lm.name = v; upd(); }), version: sbInput(lm.version, (v) => { lm.version = v; upd(); }),
          author: sbInput(lm.author, (v) => { lm.author = v; upd(); }), description: sbInput(lm.description, (v) => { lm.description = v; upd(); }) };
        const preset = sbSelect([['', 'шаблон…'], ['other', 'другой модуль'], ['self', 'этот же модуль (обновление версии)']], '', (v) => {
          if (v === 'self' && st.module) Object.assign(lm, { module_name: st.module.module_name, name: st.module.name, version: st.module.version + '.1', author: st.module.author, description: st.module.description });
          else if (v === 'other') Object.assign(lm, { module_name: 'other_module', name: 'Другой модуль', version: '2.0', author: 'someone', description: 'Модуль, который только что загрузили' });
          else return;
          Object.keys(f).forEach((k2) => { f[k2].value = lm[k2]; }); upd();
        });
        return [sbField('Шаблон', preset), h('div', { class: 'sb-grid2' }, sbField('MODULE_NAME', f.module_name), sbField('NAME', f.name), sbField('VERSION', f.version), sbField('AUTHOR', f.author)), sbField('DESCRIPTION', f.description)];
      },
      () => [`info.module_name == ${q(lm.module_name)}`, `info.name == ${q(lm.name)}`, `info.version == ${q(lm.version)}`, `info.author == ${q(lm.author)}`, `info.description == ${q(lm.description)}`].join('\n'),
      () => ({ kind: 'loadmod', module: { ...lm } })));

    // 6-7. аккаунт и запуск
    const ac = { number: 2 };
    cards.push(evCard('Смена аккаунта', '@decorators.on_account_switch', 'Юзербот переключился на другой аккаунт.',
      (upd) => [sbField('Номер аккаунта', sbInput(ac.number, (v) => { ac.number = Number(v) || 1; upd(); }, { type: 'number' }))],
      () => `number == ${ac.number}`, () => ({ kind: 'account', number: ac.number })));
    cards.push(evCard('Запуск', '@decorators.on_start', 'Вызвать обработчики on_start ещё раз, не очищая данные (для полного перезапуска есть «↻ перезапуск» вверху).',
      () => [], () => 'on_start() — без аргументов', () => ({ kind: 'start' })));

    return h('div', { class: 'sb-pane' }, h('div', { class: 'wi-hint' }, 'Каждое событие собирается формой — модулю приходит ровно тот объект, который описан под ней. Результат смотрите в журнале и в ленте.'), cards);
  }

  /* ---------- вкладки «обработчики», «участники», «страницы» ---------- */

  function tabHandlers(st) {
    const H = st.handlers;
    if (!st.module) return h('div', { class: 'wi-note' }, st.status === 'error' ? 'Модуль не загрузился — исправьте ошибку.' : 'Модуль не загружен.');
    const sec = (title, items, extra) => (items && items.length) ? h('div', { class: 'sb-hsec' }, h('div', { class: 'wi-lab' }, title),
      items.map((x) => h('div', { class: 'sb-hrow' }, h('button', { type: 'button', class: 'sb-hfn', title: 'перейти к функции в редакторе', onclick: () => opts.onGoto(x.line, false) }, h('span', { class: 'mono' }, x.name), x.line ? h('span', { class: 'wi-hint' }, ' · стр. ' + x.line) : null),
        extra ? extra(x) : null, x.doc ? h('span', { class: 'wi-hint' }, x.doc) : null))) : null;
    const pre = st.settings.prefix;
    const use = (text) => h('button', { class: 'chip small', type: 'button', onclick: () => { input.value = text + ' '; setOpen(true); input.focus(); } }, text);
    return h('div', { class: 'sb-pane' },
      h('div', { class: 'sb-modinfo' }, h('b', {}, st.module.name), h('span', { class: 'sb-tag' }, 'v' + st.module.version), h('span', { class: 'wi-hint' }, st.module.module_name + ' · ' + st.module.author)),
      sec('команды юзербота', H.commands, (x) => use(pre + x.alias)), sec('вотчеры', H.watchers), sec('циклы', H.cycles, (x) => h('span', { class: 'sb-tag' }, 'каждые ' + x.interval)),
      sec('telegram_event', H.telegram_events), sec('callback', H.callbacks), sec('on_error', H.on_error), sec('on_load', H.on_load), sec('on_start', H.on_start), sec('on_account_switch', H.on_account_switch),
      sec('команды бота', H.bot_commands, (x) => h('span', {}, use('/' + x.alias), h('span', { class: 'sb-tag' }, x.access))),
      sec('вотчеры бота', H.bot_watchers, (x) => h('span', { class: 'sb-tag' }, `${x.watch} · ${x.access}`)), sec('bot.telegram_event', H.bot_telegram_events), sec('bot.on_error', H.bot_on_error),
      sec('bot.on_load', H.bot_on_load), sec('bot.on_start', H.bot_on_start), sec('циклы бота', H.bot_cycles, (x) => h('span', { class: 'sb-tag' }, 'каждые ' + x.interval)),
      sec('страницы сайта', H.pages.map((p) => ({ name: p.title, line: 0, doc: p.name })), null),
      h('div', { class: 'wi-hint' }, `Ожидают: обработчиков в работе — ${st.running}, wait_message — ${st.waiters}, открытых форм — ${st.forms.length}.`));
  }

  function tabPeople(st) {
    const nu = { id: '', name: '', last: '', username: '', premium: false }, nc = { id: '', title: '', type: 'group', side: 'both' };
    const prefix = sbInput(st.settings.prefix, null, { placeholder: 'префикс' });
    const botCb = h('input', { type: 'checkbox', class: 'wi-cb' }); botCb.checked = st.settings.bot_enabled;
    const saveSettings = () => act(async () => { await api('/sandbox/settings', { key, prefix: prefix.value, bot_enabled: botCb.checked }); pollSoon(); });
    const premCb = h('input', { type: 'checkbox', class: 'wi-cb' }); premCb.addEventListener('change', () => { nu.premium = premCb.checked; });
    const addUser = () => {
      if (!nu.name.trim() && !nu.id.trim()) { toast('введите имя или ID участника', 'error'); return; }
      act(async () => {
        await api('/sandbox/people', { key, op: 'user', id: nu.id.trim() || null, name: nu.name, last_name: nu.last, username: nu.username, premium: nu.premium });
        toast('участник добавлен и сохранён — он будет во всех модулях', 'ok'); pollSoon();
      });
    };
    const addChat = () => act(async () => {
      await api('/sandbox/people', { key, op: 'chat', id: nc.id.trim() || null, title: nc.title, type: nc.type, side: nc.side });
      toast('чат добавлен и сохранён', 'ok'); pollSoon();
    });
    const idHint = 'ID участника: положительное число, как в Telegram (например 5012345678). Пусто — подберётся свободный.';
    return h('div', { class: 'sb-pane' },
      h('div', { class: 'wi-lab' }, 'Настройки'),
      h('div', { class: 'sb-rowbar' }, sbField('Префикс команд', prefix), h('label', { class: 'wi-check' }, botCb, h('span', {}, 'стационарный бот включён')), h('button', { class: 'btn small', type: 'button', onclick: saveSettings }, 'применить')),
      h('div', { class: 'wi-hint' }, 'Без бота create_inlineform и bot_send_to_owner падают с тем же сообщением, что и в настоящем юзерботе.'),
      h('div', { class: 'wi-lab' }, 'Участники'),
      st.users.map((u) => h('div', { class: 'sb-person' }, h('span', { class: 'sb-avatar', style: `background:hsl(${sbHue(u.id)} 55% 45%)` }, (u.first_name || '?')[0]), h('b', {}, u.name), u.username ? h('span', { class: 'wi-hint' }, '@' + u.username) : null,
        h('span', { class: 'wi-hint mono' }, String(u.id)), u.id === st.settings.owner_id ? h('span', { class: 'sb-tag' }, 'вы') : null, u.premium ? h('span', { class: 'sb-tag' }, 'premium') : null, u.custom ? h('span', { class: 'sb-tag' }, 'свой') : null,
        u.custom ? h('button', { class: 'wi-ico bad sb-del', type: 'button', title: 'удалить участника (и его личные чаты)', onclick: () => act(async () => { await api('/sandbox/people', { key, op: 'delete_user', id: String(u.id) }); pollSoon(); }) }, '✕') : null)),
      h('div', { class: 'wi-hint' }, `Бот: ${st.settings.bot.name} @${st.settings.bot.username} (id ${st.settings.bot.id})`),
      h('div', { class: 'sb-addbox' },
        h('div', { class: 'sb-grid2' }, sbField('ID', sbInput('', (v) => { nu.id = v; }, { placeholder: 'пусто — авто', type: 'text' })), sbField('Имя', sbInput('', (v) => { nu.name = v; }, { placeholder: 'имя' })),
          sbField('Фамилия', sbInput('', (v) => { nu.last = v; }, { placeholder: 'необязательно' })), sbField('@username', sbInput('', (v) => { nu.username = v; }, { placeholder: 'необязательно' }))),
        h('div', { class: 'sb-rowbar' }, h('label', { class: 'wi-check' }, premCb, h('span', {}, 'Telegram Premium')), h('span', { class: 'grow' }), h('button', { class: 'btn small primary', type: 'button', onclick: addUser }, '+ участник')),
        h('div', { class: 'wi-hint' }, idHint + ' Участники сохраняются и доступны во всех модулях; у каждого сразу появляются личные чаты с юзерботом и с ботом.')),
      h('div', { class: 'wi-lab' }, 'Чаты'),
      st.chats.map((c) => h('div', { class: 'sb-person' }, h('b', {}, c.title), h('span', { class: 'sb-tag' }, c.type), h('span', { class: 'sb-tag' }, SB_SIDE[c.side]), h('span', { class: 'wi-hint mono' }, String(c.id)),
        c.custom && c.type !== 'private' ? h('button', { class: 'wi-ico bad sb-del', type: 'button', title: 'удалить чат', onclick: () => act(async () => { await api('/sandbox/people', { key, op: 'delete_chat', chat_key: c.key }); pollSoon(); }) }, '✕') : null)),
      h('div', { class: 'sb-addbox' },
        h('div', { class: 'sb-grid2' }, sbField('ID чата', sbInput('', (v) => { nc.id = v; }, { placeholder: 'пусто — авто (отрицательный)' })), sbField('Название', sbInput('', (v) => { nc.title = v; }, { placeholder: 'название чата' })),
          sbField('Тип', sbSelect([['group', 'группа'], ['supergroup', 'супергруппа'], ['channel', 'канал']], 'group', (v) => { nc.type = v; })),
          sbField('Кто видит', sbSelect([['both', 'юзербот + бот'], ['user', 'только юзербот'], ['bot', 'только бот']], 'both', (v) => { nc.side = v; }))),
        h('div', { class: 'sb-rowbar' }, h('span', { class: 'grow' }), h('button', { class: 'btn small primary', type: 'button', onclick: addChat }, '+ чат')),
        h('div', { class: 'wi-hint' }, 'ID групп и каналов отрицательные (супергруппа: -100…). Добавленное сохраняется на диск.')));
  }

  function tabPages(st) {
    const pages = st.handlers.pages || [];
    if (!pages.length) return h('div', { class: 'wi-note' }, 'У модуля нет страниц сайта (@web.page).');
    const vals = {};
    const body = h('div', { class: 'ui sb-page' });
    let index = 0;
    const load = async (action) => {
      const r = await api('/sandbox/page', { key, index, values: vals, action });
      (r.toasts || []).forEach((t) => toast(t.text, t.kind));
      body.replaceChildren(...r.tree.map(rn));
      pollSoon();
    };
    function rn(n) {
      switch (n.t) {
        case 'title': return h('div', { class: 'u-title' }, n.text);
        case 'text': return h('div', {}, n.text);
        case 'muted': return h('div', { class: 'muted' }, n.text);
        case 'code': return h('div', { class: 'u-code' }, n.text);
        case 'divider': return h('div', { class: 'u-div' });
        case 'input': { vals[n.name] = n.value; const f = h(n.multiline ? 'textarea' : 'input', { class: 'wi-field', placeholder: n.placeholder }); f.value = n.value ?? ''; f.addEventListener('input', () => { vals[n.name] = f.value; }); return sbField(n.label || '', f); }
        case 'toggle': vals[n.name] = n.value; return h('div', { class: 'sb-rowbar' }, toggle(n.value, (v) => { vals[n.name] = v; }), n.label ? h('span', { class: 'muted' }, n.label) : null);
        case 'select': vals[n.name] = n.value; return sbField(n.label || '', sbSelect(n.options.map((o) => [o, o]), n.value, (v) => { vals[n.name] = v; }));
        case 'button': return h('button', { class: 'btn small' + (n.danger ? ' danger' : ' primary'), type: 'button', disabled: !n.action, onclick: () => n.action && act(() => load(n.action)) }, n.label);
        case 'row': return h('div', { class: 'sb-rowbar' }, (n.children || []).map(rn));
        case 'section': return h('div', { class: 'u-sec' }, n.title ? h('div', { class: 'wi-lab' }, n.title) : null, (n.children || []).map(rn));
        case 'list': return h('div', {}, n.items.map((it) => h('div', { class: 'sb-hrow' }, h('b', {}, it.title), h('span', { class: 'wi-hint' }, it.text))));
        case 'table': return h('div', { class: 'tablewrap' }, h('table', { class: 'sb-table' }, h('thead', {}, h('tr', {}, n.headers.map((x) => h('th', {}, x)))), h('tbody', {}, n.rows.map((r) => h('tr', {}, r.map((c2) => h('td', {}, c2)))))));
        default: return h('div', { class: 'wi-hint' }, '[компонент ' + n.t + ']');
      }
    }
    const sel = sbSelect(pages.map((p, i) => [i, p.title]), 0, (v) => { index = Number(v); Object.keys(vals).forEach((k2) => delete vals[k2]); act(() => load()); });
    act(() => load());
    return h('div', { class: 'sb-pane' }, h('div', { class: 'wi-hint' }, 'Страница модуля (@web.page) выполняется в песочнице: данные, время и конфиги — песочницы.'), pages.length > 1 ? sel : null, body);
  }

  /* ---------- публичный интерфейс ---------- */

  return {
    el, open: () => setOpen(true), close: () => setOpen(false), isOpen: () => S.open, run: (restart) => { if (!S.open) setOpen(true); else run(!!restart); },
    notifyChange, destroy() { S.destroyed = true; clearTimeout(S.timer); clearTimeout(S.autoTimer); }, status: () => S.status,
  };
}
