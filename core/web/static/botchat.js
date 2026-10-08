/* FRX web — BotChat. Вкладка «botchat»: аккаунт стационарного бота — чаты, переписка, отправка от его имени.
 * Загружается ДО app.js и использует его утилиты (h, api, act, toast, modal, confirmBtn, shell, glass, empty, mount, TOKEN)
 * только во время вызова. Данные попадают в DOM через textContent/createElement; ссылки — только http(s)/tg/mailto. */
'use strict';

const BC_KIND = { private: 'личный чат', group: 'группа', supergroup: 'супергруппа', channel: 'канал' };
const BC_MEDIA = { photo: 'фото', video: 'видео', animation: 'gif', document: 'файл', audio: 'аудио', voice: 'голосовое', video_note: 'кружок', sticker: 'стикер', location: 'геопозиция', contact: 'контакт', poll: 'опрос', dice: 'кубик' };
const BC_MAX_DL = 20 * 1048576;
const BC_QUICK = ['👍', '❤', '🔥', '😁', '😢', '🎉', '👎', '🤔'];
const BC_PIPS = { 1: [4], 2: [0, 8], 3: [0, 4, 8], 4: [0, 2, 6, 8], 5: [0, 2, 4, 6, 8], 6: [0, 2, 3, 5, 6, 8] };
const BC_SLOT = ['BAR', '🍇', '🍋', '7'];
const BC_SAFE_URL = /^(https?:\/\/|tg:\/\/|mailto:)/i;

let BC_LAST = null;                 // последний открытый чат — вкладка вернёт его
const BC_DRAFTS = new Map();        // chat -> {text}

const bcTime = (ts) => new Date(ts * 1000).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
function bcDay(ts) {
  const d = new Date(ts * 1000), now = new Date();
  const k = (x) => x.getFullYear() * 10000 + x.getMonth() * 100 + x.getDate();
  if (k(d) === k(now)) return 'сегодня';
  const y = new Date(now); y.setDate(now.getDate() - 1);
  if (k(d) === k(y)) return 'вчера';
  return d.toLocaleDateString('ru-RU', d.getFullYear() === now.getFullYear() ? { day: 'numeric', month: 'long' } : { day: 'numeric', month: 'long', year: 'numeric' });
}
function bcListTime(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000), now = new Date();
  if (d.toDateString() === now.toDateString()) return bcTime(ts);
  if (now - d < 6 * 864e5) return d.toLocaleDateString('ru-RU', { weekday: 'short' });
  return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit', year: '2-digit' });
}
const bcHue = (id) => (350 + (Math.abs(Number(id) * 37) % 50)) % 360;   // тёплые оттенки (красный → оранжевый)
const bcSize = (n) => (n === null || n === undefined) ? '' : n < 1024 ? n + ' Б' : n < 1048576 ? (n / 1024).toFixed(1) + ' КБ' : (n / 1048576).toFixed(1) + ' МБ';
const bcMedia = (c, id, extra) => `/botchat/media?chat=${c}&id=${id}${extra || ''}`;
const bcDur = (s) => s ? Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0') : '';

function bcAvatar(chat, cls) {
  const t = (chat.title || chat.name || '?').trim();
  const el = h('div', { class: 'bc-av ' + (cls || ''), style: `--hue:${bcHue(chat.id)}` }, h('span', {}, (Array.from(t)[0] || '?').toUpperCase()));
  if (chat.id !== undefined && chat.avatar !== false) {
    const img = h('img', { alt: '', loading: 'lazy', src: `/botchat/avatar?chat=${chat.id}${chat.t ? '&t=' + chat.t : ''}` });
    img.addEventListener('error', () => img.remove());
    el.appendChild(img);
  }
  return el;
}

/* ---------- текст с форматированием Telegram (offset/length — в UTF-16, как и в JS) ---------- */

function bcRich(text, ents) {
  const frag = document.createDocumentFragment();
  if (!ents || !ents.length) { frag.appendChild(document.createTextNode(text)); return frag; }
  const pts = new Set([0, text.length]);
  ents.forEach((e) => { pts.add(Math.min(e.o, text.length)); pts.add(Math.min(e.o + e.l, text.length)); });
  const cuts = [...pts].sort((a, b) => a - b);
  for (let i = 0; i < cuts.length - 1; i++) {
    const a = cuts[i], b = cuts[i + 1];
    if (a === b) continue;
    const seg = text.slice(a, b);
    const act = ents.filter((e) => e.o <= a && e.o + e.l >= b).sort((x, y) => (x.o - y.o) || (y.l - x.l));
    let node = document.createTextNode(seg);
    for (const e of act.slice().reverse()) {                 // от внутренней к внешней
      let w;
      switch (e.t) {
        case 'bold': w = h('b', {}); break;
        case 'italic': w = h('i', {}); break;
        case 'underline': w = h('u', {}); break;
        case 'strikethrough': w = h('s', {}); break;
        case 'code': w = h('code', {}); break;
        case 'pre': w = h('pre', { class: 'bc-pre' }); break;
        case 'blockquote': case 'expandable_blockquote': w = h('blockquote', {}); break;
        case 'spoiler': w = h('span', { class: 'bc-spoil', onclick: (ev) => { ev.stopPropagation(); ev.currentTarget.classList.add('open'); } }); break;
        case 'text_link': w = BC_SAFE_URL.test(e.u || '') ? h('a', { href: e.u, target: '_blank', rel: 'noopener noreferrer' }) : h('span', {}); break;
        case 'url': { const u = /^[a-z]+:\/\//i.test(seg) ? seg : 'https://' + seg; w = BC_SAFE_URL.test(u) ? h('a', { href: u, target: '_blank', rel: 'noopener noreferrer' }) : h('span', {}); break; }
        case 'email': w = h('a', { href: 'mailto:' + seg }); break;
        case 'mention': case 'hashtag': case 'cashtag': case 'bot_command': case 'phone_number': w = h('span', { class: 'bc-ent' }); break;
        default: w = null;
      }
      if (w) { w.appendChild(node); node = w; }
    }
    frag.appendChild(node);
  }
  return frag;
}


/* Обратное преобразование: форматирование сообщения -> мини-markdown композера (чтобы правка не стирала жирный, код, ссылки) */
function bcToMd(text, ents) {
  if (!ents || !ents.length) return text;
  const MARK = { bold: ['**', '**'], italic: ['__', '__'], strikethrough: ['~~', '~~'], spoiler: ['||', '||'], code: ['`', '`'] };
  const ins = [];                                         // [позиция, порядок, строка]
  ents.forEach((e) => {
    const a = e.o, b = Math.min(e.o + e.l, text.length);
    let open = null, close = null;
    if (MARK[e.t]) [open, close] = MARK[e.t];
    else if (e.t === 'pre') { open = '```' + (e.g || '') + '\n'; close = '\n```'; }
    else if (e.t === 'text_link' && BC_SAFE_URL.test(e.u || '')) { open = '['; close = '](' + e.u + ')'; }
    else if (e.t === 'blockquote' || e.t === 'expandable_blockquote') { open = '\u0001'; close = '\u0002'; }
    if (open === null) return;
    ins.push([a, 1, -(b - a), open]);                     // открытия: внешние (длинные) раньше
    ins.push([b, 0, (b - a), close]);                     // закрытия: внутренние (короткие) раньше
  });
  ins.sort((x, y) => (x[0] - y[0]) || (x[1] - y[1]) || (x[2] - y[2]));
  let out = '', pos = 0;
  ins.forEach(([at, , , s]) => { out += text.slice(pos, at) + s; pos = at; });
  out += text.slice(pos);
  return out.replace(/\u0001([\s\S]*?)\u0002/g, (_, q) => q.split('\n').map((l) => '> ' + l).join('\n'));
}

/* загрузка через XMLHttpRequest — только он умеет отдавать прогресс отправки файла.
 * onprog(загружено, всего, наСервере): наСервере=true — файл уже у юзербота и теперь уходит в Telegram (длится дольше всего у кружков и GIF). */
function bcUpload(path, form, onprog, ctl) {
  return new Promise((resolve, reject) => {
    const x = new XMLHttpRequest();
    x.open('POST', '/api' + path);
    x.setRequestHeader('X-FRX', '1');
    if (ctl) ctl.abort = () => x.abort();
    x.upload.onprogress = (e) => { if (onprog && e.lengthComputable) onprog(e.loaded, e.total, false); };
    x.upload.onload = () => { if (onprog) onprog(1, 1, true); };
    x.onerror = () => reject(new Error('не удалось отправить файл'));
    x.onabort = () => reject(new Error('отправка отменена'));
    x.onload = () => {
      let data = {};
      try { data = JSON.parse(x.responseText); } catch (e) { /* пусто */ }
      if (x.status === 503 && data.sleeping) reject(new SleepError('юзербот спит — действия появятся после запуска'));
      else if (x.status >= 200 && x.status < 300) resolve(data);
      else reject(new Error(data.error || ('ошибка ' + x.status)));
    };
    x.send(form);
  });
}

/* ---------- контекстное меню ---------- */

function bcMenu(x, y, items, head) {
  document.querySelectorAll('.bc-menu').forEach((n) => n.remove());
  const menu = h('div', { class: 'bc-menu' });
  if (head) menu.appendChild(head);
  items.filter(Boolean).forEach((it) => {
    if (it.sep) { menu.appendChild(h('div', { class: 'sep' })); return; }
    const b = h('button', { class: 'it' + (it.danger ? ' danger' : ''), type: 'button' }, it.label);
    b.addEventListener('click', () => { close(); it.run(); });
    menu.appendChild(b);
  });
  const close = () => { menu.remove(); document.removeEventListener('pointerdown', away, true); document.removeEventListener('keydown', esc, true); };
  const away = (e) => { if (!menu.contains(e.target)) close(); };
  const esc = (e) => { if (e.key === 'Escape') close(); };
  document.body.appendChild(menu);
  const r = menu.getBoundingClientRect();
  menu.style.left = Math.max(8, Math.min(x, innerWidth - r.width - 8)) + 'px';
  menu.style.top = Math.max(8, Math.min(y, innerHeight - r.height - 8)) + 'px';
  setTimeout(() => { document.addEventListener('pointerdown', away, true); document.addEventListener('keydown', esc, true); }, 0);
  return close;
}

/* ====================================================================== *
 * страница
 * ====================================================================== */

async function botchatView(T) {
  const st = await api('/botchat/state');
  const side = st.me ? h('span', { class: 'muted' }, '@' + (st.me.username || st.me.name)) : null;
  if (!st.available) {
    mount(T, shell('botchat', {}, glass(empty('стационарный бот не запущен — укажите aiogram_token в user/config.json (или создайте бота модулем Startup), затем перезапустите юзербот'))));
    return;
  }
  const ui = new BotChatUI(T, st);
  const sh = shell('botchat', { side }, ui.root);
  sh.classList.add('bc-shell');
  if (!mount(T, sh)) return;
  ui.start();
}

class BotChatUI {
  constructor(T, st) {
    this.T = T;
    this.me = st.me;
    this.reactions = st.reactions || BC_QUICK;
    this.chats = new Map(st.chats.map((c) => [c.id, c]));
    this.ver = st.ver;
    this.cur = null;                 // id открытого чата
    this.list = [];                  // сообщения открытого чата (по возрастанию id)
    this.more = false;
    this.rows = new Map();           // id сообщения -> строка DOM
    this.sel = new Set();
    this.reply = null;               // сообщение, на которое отвечаем
    this.editing = null;             // сообщение, которое правим
    this.files = [];                 // вложения в композере [{file, kind, spoiler}]
    this.silent = false;
    this.filter = '';
    this.pinned = null;
    this.unreadFrom = 0;
    this.lastTyping = 0;
    this.loadingOlder = false;
    this.fresh = 0;                  // сколько новых сообщений пришло, пока лента прокручена вверх
    this.rolled = new Set();         // кубики, которые уже «бросили» на этом экране
    this.ffmpeg = !!st.ffmpeg;
    this.rec = null;                 // идущая запись голосового/кружка
    this.build();
    window.addEventListener('focus', this.onFocus = () => this.markRead());
    document.addEventListener('visibilitychange', this.onFocus);
  }

  alive() { return this.T === TOKEN && this.root.isConnected; }

  /* ---------------------------------------------------------- каркас #/ */

  build() {
    this.q = h('input', { class: 'field', placeholder: 'поиск по чатам', autocomplete: 'off' });
    this.q.addEventListener('input', () => { this.filter = this.q.value.trim().toLowerCase(); this.renderList(); });
    this.listEl = h('div', { class: 'bc-list' });
    const meBtn = this.me ? h('button', { class: 'bc-me', type: 'button', title: 'профиль бота: имя, описание, команды, фото', onclick: () => this.botProfile() },
      bcAvatar({ id: this.me.id, title: this.me.name || this.me.username }, 'sm'),
      h('div', { class: 'mt' }, h('div', { class: 'mn' }, this.me.name || 'бот'), h('div', { class: 'mu' }, this.me.username ? '@' + this.me.username : 'профиль бота')),
      h('span', { class: 'mg' }, 'профиль')) : null;
    this.sideEl = h('div', { class: 'bc-side glass' }, meBtn,
      h('div', { class: 'bc-side-top' }, this.q,
        h('button', { class: 'btn small', type: 'button', onclick: () => this.newChat() }, 'новый чат')),
      this.listEl);

    this.mainEl = h('div', { class: 'bc-main glass' });
    this.root = h('div', { class: 'bc' }, this.sideEl, this.mainEl);
    this.renderList();
    this.renderMain();
    this.root.addEventListener('dragenter', (e) => { if (this.cur && e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.stopPropagation(); e.preventDefault(); this.mainEl.classList.add('drop'); } }, true);
    this.root.addEventListener('dragover', (e) => { if (this.cur && e.dataTransfer && [...e.dataTransfer.types].includes('Files')) { e.stopPropagation(); e.preventDefault(); } }, true);
    this.root.addEventListener('dragleave', (e) => { if (!this.mainEl.contains(e.relatedTarget)) this.mainEl.classList.remove('drop'); }, true);
    this.root.addEventListener('drop', (e) => {
      this.mainEl.classList.remove('drop');
      if (this.cur && e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) { e.stopPropagation(); e.preventDefault(); this.attach([...e.dataTransfer.files]); }
    }, true);
  }

  start() {
    this.updateTitle();
    const want = (BC_LAST && this.chats.has(BC_LAST)) ? BC_LAST : null;
    if (want && matchMedia('(min-width: 861px)').matches) this.open(want);
    this.loop();
  }

  /* ----------------------------------------------------- живые события #/ */

  async loop() {
    let wait = 0;
    while (this.alive()) {
      try {
        const r = await api('/botchat/poll?since=' + this.ver);
        if (!this.alive()) break;
        wait = 0;
        this.ver = r.ver;
        if (r.reset) await this.resync(); else r.events.forEach((e) => this.apply(e));
      } catch (e) {
        if (e instanceof SleepError) return;
        await sleep(Math.min(8000, 1500 + (wait += 800)));
      }
    }
    document.title = 'frx';
    window.removeEventListener('focus', this.onFocus);
    document.removeEventListener('visibilitychange', this.onFocus);
  }

  async resync() {
    const st = await api('/botchat/state');
    this.ver = st.ver;
    this.chats = new Map(st.chats.map((c) => [c.id, c]));
    this.renderList();
    if (this.cur && !this.chats.has(this.cur)) this.close(); else if (this.cur) await this.load(this.cur, true);
  }

  apply(ev) {
    const { k: kind, c: cid, d } = ev;
    if (kind === 'chat') { this.chats.set(cid, d); this.queueList(); this.updateTitle(); if (cid === this.cur) { this.updateHeader(); this.updateBlind(); this.updateComposerAccess(); } }
    else if (kind === 'drop') { this.chats.delete(cid); this.queueList(); if (cid === this.cur) this.close(); }
    else if (kind === 'migrate') {                    // группа стала супергруппой: это один и тот же чат
      this.chats.delete(cid); this.chats.set(d.new, d.chat); if (BC_LAST === cid) BC_LAST = d.new;
      this.queueList();
      if (cid === this.cur) { this.cur = d.new; this.load(d.new, true); this.updateHeader(); toast('группа стала супергруппой — переписка сохранена', 'ok'); }
    }
    else if (kind === 'reset') { this.resync(); }
    else if (kind === 'reset_chat') { if (cid === this.cur) this.load(cid, true); }
    else if (cid !== this.cur) { /* чужой чат — список обновится событием chat */ }
    else if (kind === 'msg') this.upsert(d);
    else if (kind === 'del') this.removeIds(d.ids);
  }

  updateTitle() {
    let n = 0; this.chats.forEach((c) => { n += c.unread || 0; });
    document.title = n ? `(${n}) frx` : 'frx';
  }

  /* ------------------------------------------------------ список чатов #/ */

  queueList() { if (this._lq) return; this._lq = requestAnimationFrame(() => { this._lq = 0; this.renderList(); }); }

  sortedChats() {
    const f = this.filter;
    return [...this.chats.values()]
      .filter((c) => !f || (c.title || '').toLowerCase().includes(f) || (c.username || '').toLowerCase().includes(f) || String(c.id) === f)
      .sort((a, b) => (!!b.pinned - !!a.pinned) || ((b.last_ts || 0) - (a.last_ts || 0)));
  }

  renderList() {
    const items = this.sortedChats();
    if (!items.length) {
      this.listEl.replaceChildren(h('div', { class: 'empty small' }, this.chats.size ? 'ничего не найдено' :
        'чатов пока нет — они появятся, когда боту кто-нибудь напишет или добавят в группу; можно добавить вручную («новый чат»)'));
      return;
    }
    this.listEl.replaceChildren(...items.map((c) => {
      const last = c.last ? ((c.last_out ? 'вы: ' : (c.last_from ? c.last_from + ': ' : '')) + c.last) : 'нет сообщений';
      const el = h('div', { class: 'bc-chat' + (c.id === this.cur ? ' on' : '') + (c.left ? ' left' : ''), role: 'button', tabindex: '0' },
        bcAvatar(c),
        h('div', { class: 'bc-chat-body' },
          h('div', { class: 'l1' }, h('span', { class: 'ttl' }, c.title || String(c.id)), h('span', { class: 'tm' }, bcListTime(c.last_ts))),
          h('div', { class: 'l2' }, h('span', { class: 'prev' }, last),
            c.pinned ? h('span', { class: 'pin', title: 'закреплён в списке' }, '◆') : null,
            c.unread ? h('span', { class: 'badge' }, c.unread > 999 ? '999+' : c.unread) : null)));
      el.addEventListener('click', () => this.open(c.id));
      el.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.open(c.id); });
      el.addEventListener('contextmenu', (e) => { e.preventDefault(); this.chatMenu(e.clientX, e.clientY, c); });
      this.longPress(el, (x, y) => this.chatMenu(x, y, c));
      return el;
    }));
  }

  chatMenu(x, y, c) {
    bcMenu(x, y, [
      { label: c.pinned ? 'открепить в списке' : 'закрепить в списке', run: () => act(() => api('/botchat/chatpin', { chat: c.id, on: !c.pinned })) },
      c.unread ? { label: 'отметить прочитанным', run: () => act(() => api('/botchat/read', { chat: c.id })) } : null,
      { label: 'информация', run: () => this.info(c.id) },
      { sep: true },
      { label: 'убрать из списка', danger: true, run: () => act(async () => { await api('/botchat/forget', { chat: c.id }); toast('чат убран из списка (в Telegram он остаётся)', 'ok'); }) },
    ]);
  }

  longPress(el, fn) {
    let t = null, sx = 0, sy = 0;
    el.addEventListener('touchstart', (e) => { const p = e.touches[0]; sx = p.clientX; sy = p.clientY; t = setTimeout(() => { t = null; fn(sx, sy); }, 520); }, { passive: true });
    ['touchend', 'touchcancel'].forEach((n) => el.addEventListener(n, () => { clearTimeout(t); }, { passive: true }));
    el.addEventListener('touchmove', (e) => { const p = e.touches[0]; if (Math.abs(p.clientX - sx) + Math.abs(p.clientY - sy) > 10) clearTimeout(t); }, { passive: true });
  }

  newChat() {
    const inp = h('input', { class: 'field', placeholder: '@username, t.me/ссылка или числовой id', autocomplete: 'off' });
    const note = h('div', { class: 'muted small' }, 'Bot API откроет публичные чаты, каналы и тех, кто уже писал боту. Написать первым человеку, который боту не писал, Telegram не позволяет.');
    const go = () => act(async () => {
      const r = await api('/botchat/open', { ref: inp.value }); closeFn(); this.chats.set(r.chat.id, r.chat); this.renderList(); this.open(r.chat.id);
    });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(); });
    const closeFn = modal('новый чат', h('div', { class: 'stack' }, inp, note), (close) => [
      h('button', { class: 'btn', type: 'button', onclick: () => close() }, 'отмена'),
      h('button', { class: 'btn primary', type: 'button', onclick: go }, 'открыть')]);
    setTimeout(() => inp.focus(), 60);
  }

  /* ---------------------------------------------------------- чат #/ */

  close() {
    if (this.rec) this.rec.stop(false);
    if (this.drawerEl) this.drawerEl._close(true);
    this.cur = null; BC_LAST = null; this.list = []; this.rows.clear(); this.sel.clear();
    this.reply = this.editing = null; this.files = [];
    this.root.classList.remove('in-chat');
    this.renderMain(); this.renderList();
  }

  async open(cid) {
    if (this.cur === cid) { this.root.classList.add('in-chat'); return; }
    this.saveDraft();
    if (this.rec) this.rec.stop(false);
    if (this.drawerEl) this.drawerEl._close(true);
    this.cur = cid; BC_LAST = cid;
    this.sel.clear(); this.reply = this.editing = null; this.files = []; this.pinned = null;
    this.root.classList.add('in-chat');
    this.renderMain();
    this.renderList();
    await act(() => this.load(cid, false));
    this.loadPinned(cid);
  }

  async load(cid, keepScroll) {
    const r = await api(`/botchat/messages?chat=${cid}&limit=60`);
    if (this.cur !== cid) return;
    const chat = this.chats.get(cid) || r.chat;
    this.unreadFrom = 0;
    if (chat && chat.unread) {
      const first = r.messages.find((m) => m.id > (chat.read_id || 0) && !m.out);
      this.unreadFrom = first ? first.id : 0;
    }
    this.list = r.messages; this.more = r.more;
    this.renderMessages(this.unreadFrom ? 'unread' : 'bottom');
    this.markRead();
  }

  async loadPinned(cid) {
    try {
      const r = await api('/botchat/info?chat=' + cid);
      if (this.cur !== cid) return;
      this.pinned = r.pinned; this.updatePinned();
    } catch (e) { /* не критично */ }
  }

  markRead() {
    if (!this.cur || document.hidden) return;
    const c = this.chats.get(this.cur);
    if (c && c.unread) { clearTimeout(this._mr); this._mr = setTimeout(() => api('/botchat/read', { chat: this.cur }).catch(() => {}), 350); }
  }

  /* -------------------------------------------------- правая панель #/ */

  renderMain() {
    this.sendBtnState = null;
    if (!this.cur) {
      this.mainEl.replaceChildren(h('div', { class: 'bc-none' }, h('div', { class: 'big' }, 'выберите чат'),
        h('div', { class: 'muted' }, 'слева — чаты бота. Здесь вы пишете от его имени.')));
      return;
    }
    this.headEl = h('div', { class: 'bc-head' });
    this.blindEl = h('div', { class: 'bc-blind', hidden: true });
    this.pinEl = h('div', { class: 'bc-pinbar', hidden: true });
    this.msgsEl = h('div', { class: 'bc-msgs' });
    this.downEl = h('button', { class: 'bc-down', type: 'button', hidden: true, onclick: () => this.scrollBottom(true) }, '↓');
    this.searchEl = h('div', { class: 'bc-search', hidden: true });
    this.ctxEl = h('div', { class: 'bc-ctx', hidden: true });
    this.attEl = h('div', { class: 'bc-att', hidden: true });
    this.upEl = h('div', { class: 'bc-up', hidden: true });
    this.recEl = h('div', { class: 'bc-rec', hidden: true });
    this.buildComposer();
    this.mainEl.replaceChildren(this.headEl, this.blindEl, this.pinEl,
      h('div', { class: 'bc-stage' }, this.msgsEl, this.downEl, this.searchEl),
      this.ctxEl, this.attEl, this.recEl, this.upEl, this.compEl);
    this.msgsEl.addEventListener('scroll', () => this.onScroll(), { passive: true });
    this.updateHeader();
    this.updateBlind();
    this.updateCtx();
    const dr = BC_DRAFTS.get(this.cur);
    if (dr) { this.ta.value = dr; this.grow(); }
    this.updateComposerAccess();
  }

  updateHeader() {
    if (!this.headEl) return;
    const c = this.chats.get(this.cur);
    if (!c) return;
    if (this.sel.size) {
      const mine = [...this.sel].every((id) => { const m = this.list.find((x) => x.id === id); return m && m.out; });
      this.headEl.replaceChildren(
        h('button', { class: 'link', type: 'button', onclick: () => { this.sel.clear(); this.refreshSel(); } }, 'отмена'),
        h('div', { class: 'ttl grow' }, 'выбрано: ' + this.sel.size),
        h('button', { class: 'btn small', type: 'button', onclick: () => this.forwardDlg([...this.sel]) }, 'переслать'),
        h('button', { class: 'btn small', type: 'button', onclick: () => this.copySel() }, 'копировать'),
        confirmBtn(mine ? 'удалить' : 'убрать из истории', () => this.deleteIds([...this.sel], !mine)));
      return;
    }
    const sub = [BC_KIND[c.type] || c.type, c.username ? '@' + c.username : null, c.left ? 'бота нет в чате' : null].filter(Boolean).join(' · ');
    this.headEl.replaceChildren(
      h('button', { class: 'link bc-back', type: 'button', onclick: () => this.close() }, '‹'),
      h('div', { class: 'bc-head-main', role: 'button', tabindex: '0', onclick: () => this.info(c.id) }, bcAvatar(c),
        h('div', { class: 'tt' }, h('div', { class: 'ttl' }, c.title || String(c.id)), h('div', { class: 'sub' }, sub))),
      h('div', { class: 'grow' }),
      h('button', { class: 'btn small', type: 'button', onclick: () => this.toggleSearch() }, 'поиск'),
      h('button', { class: 'btn small', type: 'button', onclick: () => this.info(c.id) }, 'инфо'));
  }

  /* плавное появление/скрытие плашек — раньше они возникали рывком и сдвигали ленту.
   * `show(el, on)` анимирует высоту (grid 0fr → 1fr), поэтому лента съезжает вниз мягко, а не прыгает. */
  reveal(el, on) {
    if (!el) return;
    clearTimeout(el._rt);
    if (on) {
      if (!el.hidden && !el.classList.contains('out')) return;
      el.hidden = false; el.classList.remove('out', 'in');
      void el.offsetWidth; el.classList.add('in');
    } else if (!el.hidden) {
      el.classList.remove('in'); el.classList.add('out');
      el._rt = setTimeout(() => { el.hidden = true; el.classList.remove('out'); }, REDUCED.matches ? 0 : 320);
    }
  }

  updatePinned() {
    if (!this.pinEl) return;
    const p = this.pinned;
    if (!p) { this.reveal(this.pinEl, false); return; }
    this.pinEl.replaceChildren(h('div', { class: 'ri' }, h('span', { class: 'lab' }, 'закреплено'), h('span', { class: 'txt' }, this.preview(p))));
    this.pinEl.onclick = () => this.jump(p.id);
    this.reveal(this.pinEl, true);
  }

  updateBlind() {
    if (!this.blindEl) return;
    const c = this.chats.get(this.cur);
    const need = !!(c && c.blind && !c.left);
    if (!need) { this.reveal(this.blindEl, false); return; }
    const on = !!c.cross;
    const sw = toggle(on, async (v) => {
      try { await api('/botchat/cross', { chat: c.id, on: v }); toast(v ? 'cross watch включён' : 'cross watch выключен', 'ok'); }
      catch (e) { toast(e.message, 'error'); this.updateBlind(); }
    });
    this.blindEl.replaceChildren(h('div', { class: 'ri bc-blind-in' },
      h('div', { class: 'bt' },
        h('div', { class: 'b1' }, on ? 'cross watch: бот читает эту группу через ваш аккаунт' : 'бот может не видеть сообщения этой группы'),
        h('div', { class: 'b2' }, on ? 'Сообщения участников копируются из аккаунта юзербота. Плашка исчезнет, когда придёт первое сообщение.'
          : 'Без права «читать все сообщения» (или админки) Telegram присылает боту только команды, ответы на него и упоминания. Плашка пропадёт, как только бот получит хоть одно сообщение участника.')),
      h('div', { class: 'bs', title: 'бот видит сообщения группы с аккаунта юзербота — как будто они пришли ему самому' }, h('span', {}, 'cross watch'), sw)));
    this.reveal(this.blindEl, true);
  }

  preview(m) {
    const t = (m.text || '').replace(/\s+/g, ' ').trim();
    return t ? t.slice(0, 140) : (m.media ? '[' + (BC_MEDIA[m.media.kind] || m.media.kind) + ']' : (m.service || ''));
  }

  /* -------------------------------------------------------- сообщения #/ */

  units(list) {
    const out = [];
    list.forEach((m) => {
      const last = out[out.length - 1];
      if (m.group && last && last[0].group === m.group) last.push(m); else out.push([m]);
    });
    return out;
  }

  renderMessages(where) {
    this.rows.clear();
    const frag = document.createDocumentFragment();
    const us = this.units(this.list);
    us.forEach((u, i) => { const prev = us[i - 1]; frag.appendChild(this.row(u, prev ? prev[prev.length - 1] : null)); });
    if (!this.list.length) frag.appendChild(h('div', { class: 'empty small' }, 'сообщений пока нет — история копится с момента подключения BotChat'));
    this.msgsEl.replaceChildren(...(this.more ? [h('div', { class: 'bc-more' }, 'загрузка более ранних…')] : []), frag);
    if (where === 'unread' && this.unreadFrom && this.rows.get(this.unreadFrom)) {
      this.rows.get(this.unreadFrom).scrollIntoView({ block: 'center' });
    } else this.scrollBottom(false);
    this.onScroll();
  }

  row(unit, prev) {
    const m = unit[0], last = unit[unit.length - 1];
    const c = this.chats.get(this.cur) || {};
    const wrap = h('div', { class: 'bc-row' });
    if (!prev || bcDay(prev.ts) !== bcDay(m.ts)) wrap.appendChild(h('div', { class: 'bc-day' }, bcDay(m.ts)));
    if (this.unreadFrom && m.id === this.unreadFrom) wrap.appendChild(h('div', { class: 'bc-new' }, 'новые сообщения'));
    if (m.service) {
      wrap.appendChild(h('div', { class: 'bc-svc' }, m.service));
    } else {
      const out = !!m.out;
      const cont = prev && !prev.service && !!prev.out === out && ((prev.from || {}).id === (m.from || {}).id) && m.ts - prev.ts < 300 && bcDay(prev.ts) === bcDay(m.ts);
      const showName = !out && c.type !== 'private' && !cont;
      const main = unit.find((x) => x.text) || m;
      const bub = h('div', { class: 'bc-bub' + (out ? ' out' : '') + (cont ? ' cont' : '') + (m.media && ['sticker', 'video_note', 'dice'].includes(m.media.kind) && !m.text ? ' bare' : '') });
      bub.dataset.id = main.id;
      if (showName) bub.appendChild(h('div', { class: 'bc-name', style: `--hue:${bcHue((m.from || {}).id)}` }, (m.from || {}).name || ''));
      if (main.fwd) bub.appendChild(h('div', { class: 'bc-fwd' }, 'переслано от ', h('b', {}, main.fwd)));
      if (main.reply) {
        const r = main.reply;
        bub.appendChild(h('div', { class: 'bc-quote', onclick: (e) => { e.stopPropagation(); this.jump(r.id); } },
          h('div', { class: 'qn' }, (r.from || {}).name || 'сообщение'), h('div', { class: 'qt' }, r.text || '…')));
      }
      if (unit.length > 1) bub.appendChild(this.album(unit));
      else if (m.media) bub.appendChild(this.media(m));
      if (main.text) {
        const t = h('div', { class: 'bc-text' }, bcRich(main.text, main.ent));
        bub.appendChild(t);
      }
      if (main.kb) bub.appendChild(this.keyboard(main.kb));
      const rx = this.reactionsEl(main);
      if (rx) bub.appendChild(rx);
      const meta = h('div', { class: 'bc-meta' }, main.via ? h('span', {}, 'via @' + main.via) : null,
        last.edit ? h('span', {}, 'изм.') : null, h('span', {}, bcTime(last.ts)));
      bub.appendChild(meta);
      bub.addEventListener('contextmenu', (e) => { e.preventDefault(); this.msgMenu(e.clientX, e.clientY, this.msgFromEvent(e, main)); });
      this.longPress(bub, (x, y) => this.msgMenu(x, y, main));
      bub.addEventListener('click', (e) => {
        if (this.sel.size) { e.preventDefault(); this.toggleSel(this.msgFromEvent(e, main).id); return; }
      });
      bub.addEventListener('dblclick', (e) => { if (!e.target.closest('a, video, audio, img, button, .bc-spoil')) this.setReply(main); });
      // кнопка «⋯» — снаружи пузыря: справа у собеседника, слева у меня; выезжает из-под пузыря при наведении
      const more = h('button', { class: 'bc-more-btn', type: 'button', title: 'действия', 'aria-label': 'действия' }, '⋯');
      more.addEventListener('click', (e) => { e.stopPropagation(); const r = more.getBoundingClientRect(); this.msgMenu(out ? r.left : r.right, r.bottom + 4, main); });
      wrap.appendChild(h('div', { class: 'bc-line' + (out ? ' out' : '') }, h('div', { class: 'bc-hov' }, ...(out ? [more, bub] : [bub, more]))));
    }
    wrap.dataset.ids = unit.map((x) => x.id).join(',');
    unit.forEach((x) => this.rows.set(x.id, wrap));
    if (unit.some((x) => this.sel.has(x.id))) wrap.classList.add('sel');
    return wrap;
  }

  msgFromEvent(e, fallback) {
    const t = e.target.closest ? e.target.closest('[data-mid]') : null;
    if (t) { const m = this.list.find((x) => x.id === Number(t.dataset.mid)); if (m) return m; }
    return fallback;
  }

  /* --- вложения --- */

  media(m) {
    const md = m.media, c = m.chat, k = md.kind;
    const big = (md.size || 0) > BC_MAX_DL;
    const tooBig = () => h('div', { class: 'bc-file' }, h('div', { class: 'ic' }, '!'), h('div', { class: 'fi' }, h('div', { class: 'fn' }, md.name || BC_MEDIA[k] || k), h('div', { class: 'fs' }, bcSize(md.size) + ' — больше 20 МБ, Bot API такие файлы не отдаёт')));
    const box = (node) => { node.dataset.mid = m.id; return node; };
    if (k === 'photo') {
      const ratio = md.w && md.h ? `aspect-ratio:${md.w}/${md.h}` : '';
      const img = h('img', { class: 'bc-img', loading: 'lazy', alt: 'фото', src: bcMedia(c, m.id, '&thumb=1'), style: ratio });
      img.addEventListener('error', () => { img.replaceWith(h('div', { class: 'bc-miss' }, 'фото недоступно')); });
      img.addEventListener('click', (e) => { if (this.sel.size) return; e.stopPropagation(); this.lightbox(m); });
      return box(h('div', { class: 'bc-media' }, img));
    }
    if (k === 'video' || k === 'animation' || k === 'video_note') {
      if (big) return box(tooBig());
      const v = h('video', { preload: 'metadata', playsinline: '', class: 'bc-vid' + (k === 'video_note' ? ' round' : ''), src: bcMedia(c, m.id) });
      if (md.tfid) v.setAttribute('poster', bcMedia(c, m.id, '&thumb=1'));
      if (md.w && md.h && k !== 'video_note') v.style.aspectRatio = `${md.w}/${md.h}`;
      if (k === 'animation') { v.muted = true; v.loop = true; v.autoplay = true; v.addEventListener('click', () => (v.paused ? v.play() : v.pause())); }
      else v.controls = true;
      v.addEventListener('error', () => v.replaceWith(h('div', { class: 'bc-miss' }, 'видео недоступно')));
      return box(h('div', { class: 'bc-media' }, v));
    }
    if (k === 'sticker') {
      if (md.video) {
        const v = h('video', { class: 'bc-sticker', src: bcMedia(c, m.id), muted: '', loop: '', autoplay: '', playsinline: '' });
        v.muted = true;
        return box(h('div', { class: 'bc-media' }, v));
      }
      const src = md.animated ? (md.tfid ? bcMedia(c, m.id, '&thumb=1') : null) : bcMedia(c, m.id);
      if (!src) return box(h('div', { class: 'bc-emoji' }, md.emoji || '◌'));
      const img = h('img', { class: 'bc-sticker', loading: 'lazy', alt: md.emoji || 'стикер', src });
      img.addEventListener('error', () => img.replaceWith(h('div', { class: 'bc-emoji' }, md.emoji || '◌')));
      return box(h('div', { class: 'bc-media' }, img));
    }
    if (k === 'audio' || k === 'voice') {
      if (big) return box(tooBig());
      return box(h('div', { class: 'bc-aud' },
        k === 'audio' ? h('div', { class: 'fn' }, [md.performer, md.title].filter(Boolean).join(' — ') || md.name || 'аудио') : h('div', { class: 'fn' }, 'голосовое ' + bcDur(md.dur)),
        h('audio', { controls: '', preload: 'none', src: bcMedia(c, m.id) })));
    }
    if (k === 'document') {
      if (big) return box(tooBig());
      const ext = ((md.name || '').split('.').pop() || 'file').slice(0, 4).toUpperCase();
      return box(h('a', { class: 'bc-file', href: bcMedia(c, m.id, '&dl=1'), download: md.name || 'file', onclick: (e) => { if (this.sel.size) e.preventDefault(); } },
        h('div', { class: 'ic' }, ext), h('div', { class: 'fi' }, h('div', { class: 'fn' }, md.name || 'файл'), h('div', { class: 'fs' }, [bcSize(md.size), md.mime].filter(Boolean).join(' · ')))));
    }
    if (k === 'location') {
      const url = `https://www.openstreetmap.org/?mlat=${md.lat}&mlon=${md.lon}#map=16/${md.lat}/${md.lon}`;
      return box(h('a', { class: 'bc-file', href: url, target: '_blank', rel: 'noopener noreferrer' }, h('div', { class: 'ic' }, '◎'),
        h('div', { class: 'fi' }, h('div', { class: 'fn' }, md.title || 'геопозиция'), h('div', { class: 'fs' }, md.address || `${md.lat.toFixed(5)}, ${md.lon.toFixed(5)}`))));
    }
    if (k === 'contact') {
      return box(h('div', { class: 'bc-file' }, h('div', { class: 'ic' }, '☎'), h('div', { class: 'fi' }, h('div', { class: 'fn' }, md.name), h('div', { class: 'fs' }, md.phone))));
    }
    if (k === 'dice') return box(this.diceEl(m));
    if (k === 'poll') return box(this.pollEl(m));
    return box(h('div', { class: 'bc-miss' }, 'вложение не поддерживается'));
  }

  /* ---------- кубики: анимация броска, похожая на выпавший результат ---------- */

  diceEl(m) {
    const md = m.media, v = Number(md.value) || 1, e = md.emoji;
    const key = m.chat + ':' + m.id;
    const fresh = !REDUCED.matches && Date.now() / 1000 - m.ts < 15 && !this.rolled.has(key);
    this.rolled.add(key);
    const num = h('b', { class: 'dnum' + (fresh ? ' wait' : '') }, String(v));
    const stage = h('div', { class: 'dstage' });
    const done = () => { stage.classList.remove('roll'); num.classList.remove('wait'); num.classList.add('pop'); };
    const alive = (fn) => { if (stage.isConnected) fn(); };
    if (e === '🎲') {
      stage.classList.add('d-cube');
      const die = h('div', { class: 'die' }, ...Array.from({ length: 9 }, () => h('i', {})));
      const paint = (n) => { const on = BC_PIPS[n]; [...die.children].forEach((p, i) => p.classList.toggle('on', on.includes(i))); };
      paint(v);
      stage.appendChild(die);
      if (fresh) {
        stage.classList.add('roll');
        let n = 0; const t = setInterval(() => { paint(1 + Math.floor(Math.random() * 6)); if (++n > 11 || !stage.isConnected) { clearInterval(t); paint(v); } }, 90);
        setTimeout(() => alive(done), 1150);
      }
    } else if (e === '🎰') {
      stage.classList.add('d-slot');
      const r = v - 1, fin = [r & 3, (r >> 2) & 3, (r >> 4) & 3];
      const reels = fin.map(() => h('span', { class: 'reel' }));
      const paint = (i, sym) => { reels[i].textContent = BC_SLOT[sym]; reels[i].classList.toggle('seven', sym === 3); reels[i].classList.toggle('bar', sym === 0); };
      fin.forEach((f, i) => paint(i, f));
      stage.appendChild(h('div', { class: 'slot' }, ...reels));
      if (fresh) {
        stage.classList.add('roll');
        const stopAt = [700, 1000, 1300], t0 = Date.now();
        const t = setInterval(() => {
          fin.forEach((f, i) => paint(i, Date.now() - t0 > stopAt[i] ? f : Math.floor(Math.random() * 4)));
          if (Date.now() - t0 > stopAt[2] + 60 || !stage.isConnected) clearInterval(t);
        }, 75);
        setTimeout(() => alive(done), 1450);
      }
    } else if (e === '🎯') {
      stage.classList.add('d-dart');
      const rad = { 6: 0, 5: 14, 4: 24, 3: 33, 2: 41, 1: 58 }[v] || 0;     // расстояние от центра мишени, px
      const ang = ((Number(m.id) * 137) % 360) * Math.PI / 180;
      stage.style.setProperty('--tx', (Math.cos(ang) * rad).toFixed(1) + 'px');
      stage.style.setProperty('--ty', (Math.sin(ang) * rad).toFixed(1) + 'px');
      stage.append(h('div', { class: 'target' }, h('i', { class: 'r1' }), h('i', { class: 'r2' }), h('i', { class: 'r3' }), h('i', { class: 'r4' })), h('div', { class: 'dart' }));
      if (fresh) { stage.classList.add('roll'); setTimeout(() => alive(done), 1000); }
    } else if (e === '🏀') {
      stage.classList.add('d-ball', v >= 4 ? 'win' : 'miss');
      stage.append(h('div', { class: 'hoop' }, h('i', {})), h('div', { class: 'ball' }, '🏀'));
      if (fresh) { stage.classList.add('roll'); setTimeout(() => alive(done), 1300); }
    } else if (e === '⚽') {
      stage.classList.add('d-foot', v >= 3 ? 'win' : 'miss');
      stage.append(h('div', { class: 'goal' }), h('div', { class: 'ball' }, '⚽'));
      if (fresh) { stage.classList.add('roll'); setTimeout(() => alive(done), 1200); }
    } else if (e === '🎳') {
      stage.classList.add('d-bowl');
      const down = v === 6 ? 6 : Math.max(0, v - 1);
      const pins = Array.from({ length: 6 }, (_, i) => h('i', { class: 'pin' + (i < down ? ' down' : '') }));
      stage.append(h('div', { class: 'pins' }, ...pins), h('div', { class: 'bowl' }));
      if (fresh) { stage.classList.add('roll'); setTimeout(() => alive(done), 1300); }
    } else {
      stage.appendChild(h('span', { class: 'dg' }, e));
      done();
    }
    if (!fresh) { num.classList.remove('wait'); }
    return h('div', { class: 'bc-dice' }, stage, num);
  }

  album(unit) {
    const g = h('div', { class: 'bc-album n' + Math.min(unit.length, 4) });
    unit.forEach((m) => {
      const md = m.media || {};
      let tile;
      if (md.kind === 'photo') {
        tile = h('img', { loading: 'lazy', alt: 'фото', src: bcMedia(m.chat, m.id, '&thumb=1') });
        tile.addEventListener('click', (e) => { if (this.sel.size) return; e.stopPropagation(); this.lightbox(m); });
      } else if (md.kind === 'video') {
        tile = h('video', { preload: 'metadata', controls: '', playsinline: '', src: bcMedia(m.chat, m.id), poster: md.tfid ? bcMedia(m.chat, m.id, '&thumb=1') : null });
      } else { tile = this.media(m); }
      const cell = h('div', { class: 'cell' }, tile);
      cell.dataset.mid = m.id;
      g.appendChild(cell);
    });
    return g;
  }

  pollEl(m) {
    const p = m.media, total = p.total || 0;
    const el = h('div', { class: 'bc-poll' },
      h('div', { class: 'pq' }, p.question),
      h('div', { class: 'pt' }, [p.type === 'quiz' ? 'викторина' : (p.anon ? 'анонимный опрос' : 'опрос'), p.multi ? 'несколько ответов' : null, p.closed ? 'завершён' : null].filter(Boolean).join(' · ')),
      p.options.map((o, i) => {
        const pct = total ? Math.round(o.n * 100 / total) : 0;
        return h('div', { class: 'po' + (p.type === 'quiz' && p.correct === i ? ' ok' : '') },
          h('div', { class: 'bar', style: `width:${pct}%` }),
          h('span', { class: 'pl' }, o.text), h('span', { class: 'pp' }, total ? `${pct}% · ${o.n}` : String(o.n)));
      }),
      h('div', { class: 'pt' }, 'голосов: ' + total));
    if (m.out && !p.closed) el.appendChild(confirmBtn('завершить опрос', () => act(() => api('/botchat/poll_stop', { chat: m.chat, id: m.id })), 'btn small danger'));
    return el;
  }

  keyboard(kb) {
    return h('div', { class: 'bc-kb' }, kb.map((row) => h('div', { class: 'kr' }, row.map((b) => b.url && BC_SAFE_URL.test(b.url)
      ? h('a', { class: 'chip', href: b.url, target: '_blank', rel: 'noopener noreferrer' }, b.text)
      : h('button', { class: 'chip', type: 'button', disabled: true, title: 'инлайн-кнопки нажимают пользователи — у бота такой возможности нет' }, b.text)))));
  }

  reactionsEl(m) {
    const rx = m.rx || {}, keys = Object.keys(rx);
    if (!keys.length) return null;
    const mine = new Set(m.my_rx || []);
    return h('div', { class: 'bc-rx' }, keys.map((e) => h('button', { class: 'chip' + (mine.has(e) ? ' on' : ''), type: 'button',
      onclick: (ev) => { ev.stopPropagation(); this.react(m, mine.has(e) ? null : e); } }, e + ' ' + rx[e])));
  }

  lightbox(m) {
    const img = h('img', { src: bcMedia(m.chat, m.id), alt: 'фото' });
    const close = () => { veil.classList.add('out'); setTimeout(() => veil.remove(), 220); document.removeEventListener('keydown', esc, true); };
    const esc = (e) => { if (e.key === 'Escape') close(); };
    const veil = h('div', { class: 'veil bc-lb', onclick: close }, img,
      h('a', { class: 'btn small bc-lb-dl', href: bcMedia(m.chat, m.id, '&dl=1'), download: 'photo.jpg', onclick: (e) => e.stopPropagation() }, 'скачать'));
    document.body.appendChild(veil);
    document.addEventListener('keydown', esc, true);
  }

  /* -------------------------------- вставка / правка / удаление в ленте #/ */

  atBottom() { const e = this.msgsEl; return e.scrollHeight - e.scrollTop - e.clientHeight < 120; }
  scrollBottom(smooth) { const e = this.msgsEl; if (!e) return; e.scrollTo({ top: e.scrollHeight, behavior: smooth && !REDUCED.matches ? 'smooth' : 'auto' }); this.fresh = 0; this.onScroll(); }

  onScroll() {
    const e = this.msgsEl;
    if (!e) return;
    const far = e.scrollHeight - e.scrollTop - e.clientHeight > 300;
    this.downEl.hidden = !far;
    if (!far) this.fresh = 0;
    this.downEl.textContent = this.fresh ? '↓ ' + this.fresh : '↓';
    if (e.scrollTop < 60 && this.more && !this.loadingOlder) this.loadOlder();
    if (!far) this.markRead();
  }

  async loadOlder() {
    if (!this.list.length) return;
    this.loadingOlder = true;
    try {
      const r = await api(`/botchat/messages?chat=${this.cur}&before=${this.list[0].id}&limit=60`);
      if (!r.messages.length) { this.more = false; return; }
      const e = this.msgsEl, h0 = e.scrollHeight, y0 = e.scrollTop;
      this.list = r.messages.concat(this.list); this.more = r.more;
      this.renderMessages('keep');
      e.scrollTop = y0 + (e.scrollHeight - h0);
    } catch (err) { /* не критично */ } finally { this.loadingOlder = false; }
  }

  upsert(m) {
    const i = this.list.findIndex((x) => x.id === m.id);
    const stick = this.atBottom();
    if (i >= 0) {
      this.list[i] = m;
      this.rerenderAround(i);
    } else {
      if (this.list.length && m.id < this.list[0].id && this.more) return;           // старше загруженного окна
      const end = !this.list.length || m.id > this.list[this.list.length - 1].id;
      this.list.push(m); if (!end) this.list.sort((a, b) => a.id - b.id);
      if (!end) { this.renderMessages('keep'); return; }
      this.rerenderTail();
      if (m.out || stick) this.scrollBottom(true);
      else { this.fresh++; this.onScroll(); }
      if (!m.out) this.markRead();
    }
    if (m.service && /закреплено/.test(m.service)) this.loadPinned(this.cur);
  }

  rangeAt(i) {
    const g = this.list[i].group;
    let a = i, b = i;
    if (g) { while (a > 0 && this.list[a - 1].group === g) a--; while (b < this.list.length - 1 && this.list[b + 1].group === g) b++; }
    return [a, b];
  }

  swapRows(a, b) {                                  // заново рисует сообщения list[a..b]
    const prevMsg = a > 0 ? this.list[a - 1] : null;
    const olds = new Set();
    for (let k = a; k <= b; k++) { const r = this.rows.get(this.list[k].id); if (r) olds.add(r); }
    const us = this.units(this.list.slice(a, b + 1));
    let prev = prevMsg;
    const nodes = us.map((u) => { const n = this.row(u, prev); prev = u[u.length - 1]; return n; });
    const first = [...olds][0];
    if (first && first.parentNode) { first.before(...nodes); } else {
      this.msgsEl.querySelector('.empty') && this.msgsEl.querySelector('.empty').remove();
      this.msgsEl.append(...nodes);
    }
    olds.forEach((r) => r.remove());
  }

  rerenderAround(i) { const [a, b] = this.rangeAt(i); this.swapRows(a, b); }
  rerenderTail() { const [a] = this.rangeAt(this.list.length - 1); this.swapRows(a, this.list.length - 1); }

  removeIds(ids) {
    const s = new Set(ids);
    this.list = this.list.filter((m) => !s.has(m.id));
    ids.forEach((id) => this.sel.delete(id));
    this.renderMessages('keep');
    this.updateHeader();
  }

  /* ------------------------------------------------------- выбор / меню #/ */

  toggleSel(id) {
    if (this.sel.has(id)) this.sel.delete(id); else this.sel.add(id);
    this.refreshSel(); 
  }

  refreshSel() {
    this.rows.forEach((row, id) => row.classList.toggle('sel', this.sel.has(id)));
    this.msgsEl.classList.toggle('selecting', this.sel.size > 0);
    this.updateHeader();
  }

  copySel() {
    const t = this.list.filter((m) => this.sel.has(m.id)).map((m) => m.text || '').filter(Boolean).join('\n\n');
    if (t) navigator.clipboard.writeText(t).then(() => toast('скопировано', 'ok')); else toast('в выбранных нет текста');
    this.sel.clear(); this.refreshSel();
  }

  msgMenu(x, y, m) {
    if (!m) return;
    const c = this.chats.get(this.cur) || {};
    const canEdit = m.out && !m.service && (m.text !== undefined || (m.media && !['poll', 'dice', 'location', 'contact', 'sticker', 'video_note'].includes(m.media.kind)));
    const mine = new Set(m.my_rx || []);
    const head = h('div', { class: 'bc-rxbar' },
      this.reactions.slice(0, 8).map((e) => h('button', { class: 'rx' + (mine.has(e) ? ' on' : ''), type: 'button',
        onclick: () => { document.querySelectorAll('.bc-menu').forEach((n) => n.remove()); this.react(m, mine.has(e) ? null : e); } }, e)),
      h('button', { class: 'rx more', type: 'button', onclick: () => { document.querySelectorAll('.bc-menu').forEach((n) => n.remove()); this.reactPicker(m); } }, '…'));
    const delLabel = m.out ? 'удалить' : 'удалить у всех';
    bcMenu(x, y, [
      { label: 'ответить', run: () => this.setReply(m) },
      m.text ? { label: 'копировать текст', run: () => navigator.clipboard.writeText(m.text).then(() => toast('скопировано', 'ok')) } : null,
      canEdit ? { label: 'редактировать', run: () => this.setEdit(m) } : null,
      { label: 'переслать…', run: () => this.forwardDlg([m.id]) },
      c.type !== 'private' || true ? { label: 'закрепить в чате', run: () => act(() => api('/botchat/pin', { chat: m.chat, id: m.id })) } : null,
      (this.pinned && this.pinned.id === m.id) ? { label: 'открепить', run: () => act(async () => { await api('/botchat/pin', { chat: m.chat, id: m.id, unpin: true }); this.pinned = null; this.updatePinned(); }) } : null,
      c.username && c.type !== 'private' ? { label: 'копировать ссылку', run: () => navigator.clipboard.writeText(`https://t.me/${c.username}/${m.id}`).then(() => toast('ссылка скопирована', 'ok')) } : null,
      { label: 'выбрать', run: () => this.toggleSel(m.id) },
      { sep: true },
      { label: delLabel, danger: true, run: () => this.deleteIds([m.id], false) },
      { label: 'убрать из истории сайта', run: () => this.deleteIds([m.id], true) },
    ], head);
  }

  reactPicker(m) {
    const grid = h('div', { class: 'bc-emoji-grid' }, this.reactions.map((e) => h('button', { class: 'rx', type: 'button', onclick: () => { close(); this.react(m, e); } }, e)));
    const close = modal('реакция', grid, (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'закрыть')]);
  }

  react(m, emoji) { act(() => api('/botchat/react', { chat: m.chat, id: m.id, emoji })); }

  deleteIds(ids, local) {
    const groups = new Set(this.list.filter((m) => ids.includes(m.id) && m.group).map((m) => m.group));
    if (groups.size) ids = [...new Set([...ids, ...this.list.filter((m) => m.group && groups.has(m.group)).map((m) => m.id)])];   // альбом удаляется целиком
    ids = ids.slice().sort((a, b) => a - b);
    act(async () => {
      try { await api('/botchat/delete', { chat: this.cur, ids, local }); }
      catch (e) { toast(e.message, 'error'); }
      this.sel.clear(); this.refreshSel();
    });
  }

  async jump(id) {
    for (let n = 0; n < 40 && !this.rows.get(id); n++) {
      if (!this.more) break;
      await this.loadOlder();
    }
    const row = this.rows.get(id);
    if (!row) { toast('сообщение не найдено в истории'); return; }
    row.scrollIntoView({ block: 'center', behavior: REDUCED.matches ? 'auto' : 'smooth' });
    row.classList.remove('flash'); void row.offsetWidth; row.classList.add('flash');
    setTimeout(() => row.classList.remove('flash'), 1800);
  }

  /* -------------------------------------------------------- поиск #/ */

  toggleSearch() {
    if (!this.searchEl.hidden) { this.searchEl.hidden = true; return; }
    const inp = h('input', { class: 'field', placeholder: 'поиск по сообщениям этого чата', autocomplete: 'off' });
    const res = h('div', { class: 'bc-sres' });
    let t = null;
    inp.addEventListener('input', () => {
      clearTimeout(t);
      t = setTimeout(async () => {
        const q = inp.value.trim();
        if (!q) { res.replaceChildren(); return; }
        const r = await api(`/botchat/search?chat=${this.cur}&q=${encodeURIComponent(q)}`).catch(() => ({ messages: [] }));
        res.replaceChildren(...(r.messages.length ? r.messages.reverse().map((m) => h('div', { class: 'sr', onclick: () => { this.searchEl.hidden = true; this.jump(m.id); } },
          h('div', { class: 'srh' }, ((m.from || {}).name || '') + ' · ' + bcDay(m.ts) + ' ' + bcTime(m.ts)), h('div', { class: 'srt' }, this.preview(m)))) : [h('div', { class: 'empty small' }, 'ничего не найдено')]));
      }, 220);
    });
    inp.addEventListener('keydown', (e) => { if (e.key === 'Escape') this.searchEl.hidden = true; });
    this.searchEl.replaceChildren(h('div', { class: 'bc-stop' }, inp, h('button', { class: 'link', type: 'button', onclick: () => { this.searchEl.hidden = true; } }, 'закрыть')), res);
    this.searchEl.hidden = false;
    setTimeout(() => inp.focus(), 40);
  }

  /* ------------------------------------------------------- композер #/ */

  buildComposer() {
    this.ta = h('textarea', { class: 'bc-ta', rows: '1', placeholder: 'сообщение от имени бота…', 'aria-label': 'сообщение' });
    this.ta.addEventListener('input', () => { this.grow(); this.typing(); this.syncSend(); });
    this.ta.addEventListener('keydown', (e) => this.onKey(e));
    this.ta.addEventListener('paste', (e) => {
      const fs = [...(e.clipboardData && e.clipboardData.files || [])];
      if (fs.length) { e.preventDefault(); this.attach(fs); }
    });
    this.fileIn = h('input', { type: 'file', multiple: '', hidden: true });
    this.fileIn.addEventListener('change', () => { this.attach([...this.fileIn.files]); this.fileIn.value = ''; });
    this.plus = h('button', { class: 'btn small bc-plus', type: 'button', title: 'вложение, опрос, кубик…' }, '+');
    this.plus.addEventListener('click', () => { const r = this.plus.getBoundingClientRect(); this.attachMenu(r.left, r.top - 8); });
    this.silentBtn = h('button', { class: 'chip small', type: 'button', title: 'отправить без уведомления', onclick: () => { this.silent = !this.silent; this.silentBtn.classList.toggle('on', this.silent); } }, 'тихо');
    this.sendBtn = h('button', { class: 'btn primary bc-send', type: 'button', 'aria-label': 'отправить', onclick: () => this.send() },
      h('span', { class: 'bc-send-t' }, 'отправить'), h('span', { class: 'bc-send-i', 'aria-hidden': 'true' }, '➤'));
    // телефон: пока пишете — поле во всю ширину, «+» и «тихо» строкой выше, «отправить» — стрелка справа.
    // Кнопки не забирают фокус у поля, чтобы клавиатура не пряталась и вёрстка не прыгала под пальцем.
    [this.plus, this.silentBtn, this.sendBtn].forEach((b) => b.addEventListener('pointerdown', (e) => { if (mIsPhone() && document.activeElement === this.ta) e.preventDefault(); }));
    const syncActive = () => { if (this.compEl) this.compEl.classList.toggle('active', document.activeElement === this.ta || !!this.ta.value || this.silent || this.editing); };
    this.ta.addEventListener('focus', syncActive);
    this.ta.addEventListener('blur', () => setTimeout(syncActive, 0));
    this.ta.addEventListener('input', syncActive);
    this.syncActive = syncActive;
    this.noAccess = h('div', { class: 'bc-noacc', hidden: true }, 'бота нет в этом чате — писать нельзя');
    this.compEl = h('div', { class: 'bc-comp' }, this.noAccess,
      h('div', { class: 'bc-comp-in' }, this.plus, this.ta, this.silentBtn, this.sendBtn), this.fileIn,
      h('div', { class: 'bc-hint' }, '**жирный** · __курсив__ · ~~зачёрк.~~ · ||спойлер|| · `код` · [текст](ссылка) · > цитата — Enter отправляет, Shift+Enter — строка'));
    this.syncSend();
  }

  updateComposerAccess() {
    const c = this.chats.get(this.cur);
    const left = !!(c && c.left);
    if (this.noAccess) this.noAccess.hidden = !left;
    if (this.compEl) this.compEl.querySelector('.bc-comp-in').classList.toggle('dis', left);
  }

  grow() { this.ta.style.height = 'auto'; this.ta.style.height = Math.min(this.ta.scrollHeight, 180) + 'px'; }
  syncSend() {
    this.sendBtn.querySelector('.bc-send-t').textContent = this.editing ? 'сохранить' : 'отправить';
    this.sendBtn.querySelector('.bc-send-i').textContent = this.editing ? '✓' : '➤';
    this.sendBtn.setAttribute('aria-label', this.editing ? 'сохранить' : 'отправить');
    if (this.syncActive) this.syncActive();
  }

  typing() {
    const now = Date.now();
    if (!this.editing && this.ta.value.trim() && now - this.lastTyping > 4500) {
      this.lastTyping = now;
      api('/botchat/action', { chat: this.cur, action: 'typing' }).catch(() => {});
    }
  }

  wrapSel(l, r) {
    const ta = this.ta, a = ta.selectionStart, b = ta.selectionEnd;
    ta.setRangeText(l + ta.value.slice(a, b) + r, a, b, 'end');
    if (a === b) ta.setSelectionRange(a + l.length, a + l.length);
    ta.dispatchEvent(new Event('input'));
  }

  onKey(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); this.send(); return; }
    if (e.key === 'Escape') { if (this.editing || this.reply) { this.cancelCtx(); e.preventDefault(); } return; }
    if (e.key === 'ArrowUp' && !this.ta.value && !this.editing) {
      const m = [...this.list].reverse().find((x) => x.out && !x.service && x.text);
      if (m) { e.preventDefault(); this.setEdit(m); }
      return;
    }
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      const k = e.key.toLowerCase();
      const map = e.shiftKey ? { x: ['~~', '~~'], p: ['||', '||'] } : { b: ['**', '**'], i: ['__', '__'], e: ['`', '`'] };
      if (map[k]) { e.preventDefault(); this.wrapSel(...map[k]); }
      else if (k === 'k' && !e.shiftKey) { e.preventDefault(); const s = this.ta.value.slice(this.ta.selectionStart, this.ta.selectionEnd); this.wrapSel('[', '](https://)'); if (!s) { /* курсор внутри скобок */ } }
    }
  }

  attachMenu(x, y) {
    bcMenu(x, y, [
      { label: 'фото / видео / файл…', run: () => this.fileIn.click() },
      { label: 'записать голосовое', run: () => this.record('voice') },
      { label: 'записать кружок', run: () => this.record('video_note') },
      { label: 'опрос…', run: () => this.pollDlg() },
      { label: 'кубик', run: () => this.diceDlg() },
      { label: 'геопозиция…', run: () => this.locDlg() },
      { label: 'контакт…', run: () => this.contactDlg() },
    ]);
    const m = document.querySelector('.bc-menu');
    if (m) m.style.top = Math.max(8, y - m.getBoundingClientRect().height) + 'px';
  }

  attach(files) {
    files.forEach((f) => { if (f.size > 50 * 1048576) toast(f.name + ': больше 50 МБ — Bot API не примет', 'error'); else this.files.push({ file: f, kind: 'auto', spoiler: false }); });
    this.renderAtt();
    this.ta.focus();
  }

  renderAtt() {
    if (!this.attEl) return;
    this.attEl.hidden = !this.files.length;
    this.attEl.replaceChildren(...this.files.map((it, i) => {
      const isImg = /^image\//.test(it.file.type);
      if (it.rec && !it.url) it.url = URL.createObjectURL(it.file);
      const prev = it.rec ? (it.rec === 'video_note' ? h('video', { class: 'rec-prev', src: it.url, controls: '', playsinline: '', preload: 'metadata' }) : h('audio', { class: 'rec-prev', src: it.url, controls: '', preload: 'metadata' })) : isImg ? h('img', { alt: '', src: URL.createObjectURL(it.file), onload: (e) => URL.revokeObjectURL(e.target.src) }) : h('div', { class: 'ext' }, (it.file.name.split('.').pop() || '').slice(0, 4).toUpperCase());
      const sel = h('select', { class: 'field', 'aria-label': 'как отправить' }, [['auto', 'авто'], ['photo', 'фото'], ['video', 'видео'], ['animation', 'gif'], ['document', 'файлом'], ['audio', 'аудио'], ['voice', 'голосовым'], ['video_note', 'кружком']]
        .map(([v, l]) => h('option', { value: v, selected: it.kind === v }, l)));
      sel.addEventListener('change', () => { it.kind = sel.value; });
      return h('div', { class: 'at' }, prev, h('div', { class: 'ai' }, h('div', { class: 'an' }, it.file.name), h('div', { class: 'as' }, bcSize(it.file.size))), sel,
        h('button', { class: 'chip small' + (it.spoiler ? ' on' : ''), type: 'button', title: 'скрыть под спойлер (фото/видео)', onclick: (e) => { it.spoiler = !it.spoiler; e.currentTarget.classList.toggle('on', it.spoiler); } }, 'спойлер'),
        h('button', { class: 'link', type: 'button', title: 'убрать', onclick: () => { if (it.url) URL.revokeObjectURL(it.url); this.files.splice(i, 1); this.renderAtt(); } }, '✕'));
    }));
  }

  setReply(m) { this.editing = null; this.reply = m; this.updateCtx(); this.ta && this.ta.focus(); this.syncSend(); }
  setEdit(m) { this.reply = null; this.editing = m; this.updateCtx(); this.ta.value = bcToMd(m.text || '', m.ent); this.grow(); this.ta.focus(); this.syncSend(); }
  cancelCtx() { if (this.editing) { this.ta.value = ''; this.grow(); } this.reply = this.editing = null; this.updateCtx(); this.syncSend(); }

  /* Имя автора для шапки ответа: имя -> @username -> «вы» (своё сообщение) -> название чата */
  senderName(m) {
    const f = m.from || {};
    if (m.out) return (this.me && (this.me.name || this.me.username)) || f.name || 'вы';
    const c = this.chats.get(this.cur) || {};
    return f.name || (f.username ? '@' + f.username : '') || c.title || 'сообщение';
  }

  updateCtx() {
    if (!this.ctxEl) return;
    const m = this.editing || this.reply;
    if (!m) { this.reveal(this.ctxEl, false); return; }
    const own = this.editing ? 'редактирование сообщения' : this.senderName(m);
    this.ctxEl.replaceChildren(h('div', { class: 'ri bc-ctx-in' },
      h('div', { class: 'ct' }, h('div', { class: 'cl' }, own), h('div', { class: 'cx' }, this.preview(m) || '…')),
      h('button', { class: 'link', type: 'button', onclick: () => this.cancelCtx() }, '✕')));
    this.reveal(this.ctxEl, true);
  }

  saveDraft() { if (this.cur && this.ta && !this.editing) { const v = this.ta.value; if (v) BC_DRAFTS.set(this.cur, v); else BC_DRAFTS.delete(this.cur); } }

  async send() {
    if (this.sending) return;
    const text = this.ta.value.trim(), cid = this.cur;
    if (!text && !this.files.length) return;
    this.sending = true; this.sendBtn.disabled = true;
    const keep = { text: this.ta.value, files: this.files, reply: this.reply, editing: this.editing };
    try {
      if (this.editing) {
        await api('/botchat/edit', { chat: cid, id: this.editing.id, text });
        this.reply = this.editing = null;
      } else if (this.files.length) {
        const fd = new FormData();
        fd.append('chat', cid); fd.append('text', text);
        if (this.reply) fd.append('reply_to', this.reply.id);
        if (this.silent) fd.append('silent', '1');
        if (this.files.some((f) => f.spoiler)) fd.append('spoiler', '1');
        this.files.forEach((f) => { fd.append('kind', f.kind); fd.append('file', f.file, f.file.name); });
        const total = this.files.reduce((n, f) => n + f.file.size, 0);
        const ctl = {};
        this.showUp(this.files.length > 1 ? this.files.length + ' файла(ов)' : this.files[0].file.name, total, ctl, this.files.some((f) => f.kind === 'video_note' || f.kind === 'voice'));
        try { await bcUpload('/botchat/upload', fd, (l, t, srv) => this.setUp(l, t, srv), ctl); }
        finally { this.hideUp(); }
        this.files.forEach((f) => { if (f.url) URL.revokeObjectURL(f.url); });
        this.files = []; this.reply = null; this.renderAtt();
      } else {
        await api('/botchat/send', { chat: cid, text, reply_to: this.reply ? this.reply.id : null, silent: this.silent });
        this.reply = null;
      }
      this.ta.value = ''; BC_DRAFTS.delete(cid); this.grow(); this.updateCtx();
    } catch (e) {
      Object.assign(this, { reply: keep.reply, editing: keep.editing, files: keep.files });
      if (e instanceof SleepError) deny();
      toast(e.message, 'error');
    } finally { this.sending = false; this.sendBtn.disabled = false; this.syncSend(); this.ta.focus(); }
  }

  /* --------------------------------------------------------- диалоги #/ */

  /* ---------- прогресс отправки файла ---------- */

  showUp(name, total, ctl, converts) {
    this.upCtl = ctl; this.upConv = converts;
    this.upBar = h('i', {}); this.upTxt = h('span', { class: 'ut' }, 'загрузка…'); this.upPct = h('b', { class: 'up' }, '0%');
    this.upEl.replaceChildren(h('div', { class: 'ri' },
      h('div', { class: 'ul' }, h('span', { class: 'un' }, 'отправка: ' + name), this.upTxt, this.upPct,
        h('button', { class: 'link', type: 'button', onclick: () => { if (ctl.abort) ctl.abort(); } }, 'отмена')),
      h('div', { class: 'bar' }, this.upBar)));
    this.upEl.classList.remove('wait');
    this.reveal(this.upEl, true);
    this.upTotal = total;
  }

  setUp(l, t, srv) {
    if (!this.upBar) return;
    if (!srv) {
      const pct = t ? Math.min(99, Math.floor(l * 100 / t)) : 0;
      this.upBar.style.width = pct + '%';
      this.upPct.textContent = pct + '%';
      this.upTxt.textContent = bcSize(l) + ' из ' + bcSize(t);
    } else {
      this.upBar.style.width = '100%';
      this.upEl.classList.add('wait');
      this.upPct.textContent = '';
      this.upTxt.textContent = this.upConv ? 'файл загружен — обработка и отправка в Telegram…' : 'файл загружен — Telegram принимает его…';
    }
  }

  hideUp() { this.upBar = null; this.reveal(this.upEl, false); }

  /* ---------- запись голосового и кружка ---------- */

  async record(kind) {
    if (this.rec) { toast('запись уже идёт'); return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) { toast('браузер не умеет записывать (нужен https или localhost)', 'error'); return; }
    if (kind === 'video_note' && !this.ffmpeg) { toast('для кружков на стороне юзербота нужен ffmpeg — установите его и перезапустите', 'error'); return; }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia(kind === 'video_note'
        ? { audio: true, video: { width: { ideal: 480 }, height: { ideal: 480 }, facingMode: 'user' } } : { audio: true });
    } catch (e) { toast('нет доступа к ' + (kind === 'video_note' ? 'камере или микрофону' : 'микрофону') + ': ' + (e.message || e.name), 'error'); return; }
    const pick = (list) => list.find((t) => MediaRecorder.isTypeSupported(t)) || '';
    const mime = kind === 'video_note' ? pick(['video/webm;codecs=vp9,opus', 'video/webm;codecs=vp8,opus', 'video/webm', 'video/mp4'])
      : pick(['audio/ogg;codecs=opus', 'audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']);
    let mr;
    try { mr = new MediaRecorder(stream, mime ? { mimeType: mime } : {}); } catch (e) { stream.getTracks().forEach((t) => t.stop()); toast('запись не поддерживается: ' + e.message, 'error'); return; }
    const chunks = [], limit = kind === 'video_note' ? 60 : 600;
    const t0 = Date.now();
    const time = h('b', { class: 'rt' }, '0:00');
    let preview = null;
    if (kind === 'video_note') { preview = h('video', { class: 'rec-live', muted: '', playsinline: '', autoplay: '' }); preview.muted = true; preview.srcObject = stream; }
    const stop = (keep) => { mr._keep = keep; if (mr.state !== 'inactive') mr.stop(); };
    this.recEl.replaceChildren(h('div', { class: 'ri' }, preview, h('div', { class: 'rl' },
      h('span', { class: 'dot' }), h('span', {}, kind === 'video_note' ? 'запись кружка' : 'запись голосового'), time,
      h('span', { class: 'muted small' }, 'до ' + (kind === 'video_note' ? '60 с' : '10 мин')),
      h('div', { class: 'grow' }),
      h('button', { class: 'btn small', type: 'button', onclick: () => stop(false) }, 'отмена'),
      h('button', { class: 'btn small primary', type: 'button', onclick: () => stop(true) }, 'готово'))));
    this.reveal(this.recEl, true);
    const tick = setInterval(() => {
      const s = Math.floor((Date.now() - t0) / 1000);
      time.textContent = Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
      if (s >= limit) stop(true);
    }, 250);
    mr.addEventListener('dataavailable', (e) => { if (e.data && e.data.size) chunks.push(e.data); });
    mr.addEventListener('stop', () => {
      clearInterval(tick); stream.getTracks().forEach((t) => t.stop()); this.rec = null; this.reveal(this.recEl, false);
      if (!mr._keep || !chunks.length) return;
      const type = (mr.mimeType || mime || '').split(';')[0] || (kind === 'video_note' ? 'video/webm' : 'audio/webm');
      const ext = type.includes('ogg') ? 'ogg' : type.includes('mp4') ? 'mp4' : 'webm';
      const file = new File(chunks, (kind === 'video_note' ? 'circle.' : 'voice.') + ext, { type });
      // без ffmpeg на стороне юзербота голосовое нельзя превратить в ogg/opus — уйдёт как обычный аудиофайл
      const k = kind === 'voice' && !this.ffmpeg && !type.includes('ogg') ? 'audio' : kind;
      this.files.push({ file, kind: k, spoiler: false, rec: kind });
      this.renderAtt();
      if (k !== kind) toast('ffmpeg не найден — голосовое уйдёт аудиофайлом', 'info');
      this.ta.focus();
    });
    this.rec = { stop };
    mr.start(500);
  }

  withReply() { return this.reply ? this.reply.id : null; }

  diceDlg() {
    const opts = ['🎲', '🎯', '🏀', '⚽', '🎳', '🎰'];
    const grid = h('div', { class: 'bc-emoji-grid' }, opts.map((e) => h('button', { class: 'rx', type: 'button', onclick: () => { close(); act(async () => { await api('/botchat/dice', { chat: this.cur, emoji: e, reply_to: this.withReply() }); this.reply = null; this.updateCtx(); }); } }, e)));
    const close = modal('кубик', grid, (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена')]);
  }

  locDlg() {
    const lat = h('input', { class: 'field', placeholder: 'широта, например 59.9386', inputmode: 'decimal' });
    const lon = h('input', { class: 'field', placeholder: 'долгота, например 30.3141', inputmode: 'decimal' });
    const close = modal('геопозиция', h('div', { class: 'stack' }, lat, lon), (cl) => [
      h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => act(async () => { await api('/botchat/location', { chat: this.cur, lat: lat.value.replace(',', '.'), lon: lon.value.replace(',', '.'), reply_to: this.withReply() }); cl(); this.reply = null; this.updateCtx(); }) }, 'отправить')]);
    setTimeout(() => lat.focus(), 60);
  }

  contactDlg() {
    const phone = h('input', { class: 'field', placeholder: 'телефон, +7…', inputmode: 'tel' });
    const fn = h('input', { class: 'field', placeholder: 'имя' });
    const ln = h('input', { class: 'field', placeholder: 'фамилия (необязательно)' });
    modal('контакт', h('div', { class: 'stack' }, phone, fn, ln), (cl) => [
      h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => act(async () => { await api('/botchat/contact', { chat: this.cur, phone: phone.value, first_name: fn.value, last_name: ln.value, reply_to: this.withReply() }); cl(); this.reply = null; this.updateCtx(); }) }, 'отправить')]);
    setTimeout(() => phone.focus(), 60);
  }

  pollDlg() {
    const q = h('input', { class: 'field', placeholder: 'вопрос' });
    const opts = h('div', { class: 'stack' });
    const addOpt = () => { if (opts.children.length < 10) { const i = h('input', { class: 'field', placeholder: 'вариант ' + (opts.children.length + 1) }); opts.appendChild(i); return i; } };
    addOpt(); addOpt();
    const anon = { v: true }, multi = { v: false }, quiz = { v: false };
    const correct = h('input', { class: 'field short', placeholder: 'номер верного (1…)', inputmode: 'numeric', hidden: true });
    const sw = (label, st, after) => h('div', { class: 'bc-sw' }, h('span', {}, label), toggle(st.v, (v) => { st.v = v; if (after) after(); }));
    const body = h('div', { class: 'stack' }, q, opts,
      h('button', { class: 'link', type: 'button', onclick: () => addOpt() }, '+ вариант'),
      sw('анонимное голосование', anon), sw('несколько ответов', multi), sw('викторина (один верный ответ)', quiz, () => { correct.hidden = !quiz.v; }), correct);
    modal('опрос', body, (cl) => [
      h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'),
      h('button', { class: 'btn primary', type: 'button', onclick: () => act(async () => {
        await api('/botchat/poll_create', { chat: this.cur, question: q.value, options: [...opts.children].map((i) => i.value), anonymous: anon.v, multiple: multi.v && !quiz.v, quiz: quiz.v, correct: (parseInt(correct.value, 10) || 1) - 1, reply_to: this.withReply() });
        cl(); this.reply = null; this.updateCtx();
      }) }, 'отправить')]);
    setTimeout(() => q.focus(), 60);
  }

  forwardDlg(ids) {
    const inp = h('input', { class: 'field', placeholder: 'куда: поиск по чатам', autocomplete: 'off' });
    const list = h('div', { class: 'bc-pick' });
    const copy = { v: false };
    const src = this.cur;
    const render = () => {
      const f = inp.value.trim().toLowerCase();
      list.replaceChildren(...[...this.chats.values()].filter((c) => !f || (c.title || '').toLowerCase().includes(f))
        .sort((a, b) => (b.last_ts || 0) - (a.last_ts || 0)).slice(0, 60).map((c) => h('div', { class: 'bc-chat', role: 'button', tabindex: '0', onclick: () => act(async () => {
          await api('/botchat/forward', { from: src, to: c.id, ids, copy: copy.v }); close(); this.sel.clear(); this.refreshSel(); toast('отправлено в «' + c.title + '»', 'ok');
        }) }, bcAvatar(c), h('div', { class: 'bc-chat-body' }, h('div', { class: 'l1' }, h('span', { class: 'ttl' }, c.title))))));
    };
    inp.addEventListener('input', render);
    const close = modal('переслать ' + ids.length + ' ' + plural(ids.length, 'сообщение', 'сообщения', 'сообщений'),
      h('div', { class: 'stack' }, inp, h('div', { class: 'bc-sw' }, h('span', {}, 'без заголовка «переслано» (копия)'), toggle(false, (v) => { copy.v = v; })), list),
      (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена')]);
    render();
    setTimeout(() => inp.focus(), 60);
  }

  /* ---------- выезжающая панель справа (инфо о чате, профиль бота) — плавно и туда и обратно ---------- */

  drawer(title) {
    if (this.drawerEl) this.drawerEl._close(true);
    const body = h('div', { class: 'bc-dr-body' });
    const ttl = h('div', { class: 'ttl' }, title);
    let closed = false;
    const wrap = h('div', { class: 'bc-dr-wrap' });
    const close = (fast) => {
      if (closed) return; closed = true;
      document.removeEventListener('keydown', esc, true);
      wrap.classList.remove('on');
      if (this.drawerEl === wrap) this.drawerEl = null;
      setTimeout(() => wrap.remove(), fast || REDUCED.matches ? 0 : 360);
    };
    const esc = (e) => { if (e.key === 'Escape') close(); };
    wrap._close = close;
    wrap.append(h('div', { class: 'bc-dr-veil', onclick: () => close() }),
      h('aside', { class: 'bc-dr glass' }, h('div', { class: 'bc-dr-head' }, ttl, h('button', { class: 'link', type: 'button', onclick: () => close(), 'aria-label': 'закрыть' }, '✕')), body));
    this.root.appendChild(wrap);
    this.drawerEl = wrap;
    document.addEventListener('keydown', esc, true);
    requestAnimationFrame(() => requestAnimationFrame(() => wrap.classList.add('on')));
    return { body, close, setTitle: (t) => { ttl.textContent = t; } };
  }

  async info(cid) {
    const c = this.chats.get(cid) || { id: cid, title: String(cid), type: 'private' };
    const dr = this.drawer(c.type === 'private' ? 'профиль' : 'информация о чате');
    const { body, close } = dr;
    const top = h('div', { class: 'bc-info-top' }, bcAvatar({ id: cid, title: c.title }, 'big'),
      h('div', {}, h('div', { class: 'ttl' }, c.title || String(cid)), h('div', { class: 'muted' }, BC_KIND[c.type] || c.type)));
    const rest = h('div', { class: 'bc-info rest' }, h('div', { class: 'empty small' }, 'загрузка…'));
    body.append(top, rest);
    let r;
    try { r = await api('/botchat/info?chat=' + cid); } catch (e) { r = { type: c.type, title: c.title, username: c.username, admins: [], offline: e.message }; }
    if (!body.isConnected) return;
    const kv = (k, v) => v ? [h('span', {}, k), h('span', {}, v)] : [];
    const isPrivate = r.type === 'private';
    const name = isPrivate ? [r.first_name, r.last_name].filter(Boolean).join(' ') : r.title;
    top.replaceChildren(bcAvatar({ id: cid, title: name || c.title }, 'big'), h('div', {}, h('div', { class: 'ttl' }, name || c.title), h('div', { class: 'muted' }, BC_KIND[r.type] || r.type)));
    const parts = [
      r.offline ? h('div', { class: 'muted small' }, 'Telegram не ответил (' + r.offline + ') — показано из истории') : null,
      h('div', { class: 'kv' }, kv('id', String(cid)), kv('username', r.username ? '@' + r.username : ''), kv('участников', r.members ? String(r.members) : ''), kv('описание', r.description || ''), kv('ссылка', r.invite_link || '')),
    ];
    if (r.admins && r.admins.length) {
      parts.push(h('div', { class: 'label' }, 'администраторы'), h('div', { class: 'bc-adm' }, r.admins.map((a) => h('div', {}, a.name + (a.username ? ' @' + a.username : '') + ' · ' + (a.status === 'creator' ? 'владелец' : (a.title || 'админ'))))));
    }
    if (!isPrivate) {
      const cur = this.chats.get(cid) || {};
      if (['group', 'supergroup'].includes(r.type)) {
        const sw = toggle(!!cur.cross, async (v) => { try { await api('/botchat/cross', { chat: cid, on: v }); toast(v ? 'cross watch включён' : 'cross watch выключен', 'ok'); } catch (e) { toast(e.message, 'error'); } });
        parts.push(h('div', { class: 'label' }, 'cross watch'),
          h('div', { class: 'bc-sw' }, h('span', {}, 'бот видит сообщения группы с аккаунта юзербота'), sw),
          h('div', { class: 'muted small' }, 'Нужно, если боту не дали право читать все сообщения группы. Юзербот должен состоять в этой группе.'));
      }
      const uid = h('input', { class: 'field short', placeholder: 'id участника', inputmode: 'numeric' });
      const mb = (label, action, danger) => h('button', { class: 'btn small' + (danger ? ' danger' : ''), type: 'button', onclick: () => act(async () => { await api('/botchat/member', { chat: cid, user: uid.value, action }); toast('готово', 'ok'); }) }, label);
      parts.push(h('div', { class: 'label' }, 'участник по id (нужны права админа)'), h('div', { class: 'inline' }, uid, mb('выгнать', 'kick', true), mb('забанить', 'ban', true), mb('разбанить', 'unban')));
      if (r.can_manage) {
        const t = h('input', { class: 'field', value: r.title || '', placeholder: 'название' });
        const d = h('input', { class: 'field', value: r.description || '', placeholder: 'описание' });
        parts.push(h('div', { class: 'label' }, 'название и описание'),
          h('div', { class: 'inline' }, h('div', { class: 'grow' }, t), h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/botchat/rename', { chat: cid, title: t.value }); toast('название изменено', 'ok'); }) }, 'сохранить')),
          h('div', { class: 'inline' }, h('div', { class: 'grow' }, d), h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/botchat/describe', { chat: cid, description: d.value }); toast('описание изменено', 'ok'); }) }, 'сохранить')));
      }
    }
    parts.push(h('div', { class: 'inline bc-danger' },
      confirmBtn('очистить историю сайта', () => act(async () => { await api('/botchat/clear', { chat: cid }); close(); toast('история чата очищена', 'ok'); })),
      confirmBtn('убрать из списка', () => act(async () => { await api('/botchat/forget', { chat: cid }); close(); })),
      isPrivate ? null : confirmBtn('покинуть чат', () => act(async () => { await api('/botchat/leave', { chat: cid }); close(); toast('бот вышел из чата', 'ok'); }))));
    rest.classList.add('ready');
    rest.replaceChildren(...parts.filter(Boolean));
  }

  /* ---------- профиль самого бота: имя, описания, команды, фото ---------- */

  async botProfile() {
    const dr = this.drawer('профиль бота');
    const { body, close } = dr;
    body.append(h('div', { class: 'empty small' }, 'загрузка…'));
    let p;
    try { p = await api('/botchat/myprofile'); } catch (e) { body.replaceChildren(h('div', { class: 'empty small' }, e.message)); return; }
    if (!body.isConnected) return;
    const field = (label, el, hint) => h('div', { class: 'bc-pf' }, h('div', { class: 'label' }, label), el, hint ? h('div', { class: 'muted small' }, hint) : null);
    const area = (val, rows, ph) => { const t = h('textarea', { class: 'field bc-pta', rows: String(rows), placeholder: ph || '' }); t.value = val || ''; return t; };
    const save = (label, fn) => h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await fn(); toast('сохранено', 'ok'); }) }, label);

    const name = h('input', { class: 'field', value: p.name || p.first_name || '', maxlength: '64', placeholder: 'имя бота' });
    const short = area(p.short_description, 2, 'короткое описание — на странице бота и при пересылке');
    const desc = area(p.description, 5, 'описание — что бот видит новый пользователь перед нажатием «старт»');
    const cmds = area((p.commands || []).map((c) => `${c.command} - ${c.description}`).join('\n'), 6, 'start - запустить бота\nhelp - справка');

    const fileIn = h('input', { type: 'file', accept: 'image/*', hidden: true });
    const av = h('div', { class: 'bc-pf-av' }, bcAvatar({ id: p.id, title: p.first_name || p.username }, 'big'));
    const refreshAv = () => av.replaceChildren(bcAvatar({ id: p.id, title: p.first_name || p.username, t: Date.now() }, 'big'));
    fileIn.addEventListener('change', () => {
      const f = fileIn.files[0]; fileIn.value = '';
      if (!f) return;
      act(async () => {
        const fd = new FormData(); fd.append('file', f, f.name);
        await bcUpload('/botchat/myphoto', fd);
        const img = av.querySelector('img'); if (img) img.remove();
        const i2 = h('img', { alt: '', src: `/botchat/avatar?chat=${p.id}&t=${Date.now()}` }); i2.addEventListener('error', () => i2.remove()); av.firstChild.appendChild(i2);
        toast('фото бота обновлено (Telegram может показывать его с задержкой)', 'ok');
      });
    });
    const parse = () => cmds.value.split('\n').map((l) => l.trim()).filter(Boolean).map((l) => {
      const m = /^\/?([A-Za-z0-9_]+)\s*(?:[-–—:]\s*)?(.*)$/.exec(l);
      if (!m) throw new Error('строка «' + l + '»: формат «команда - описание», сама команда — латиницей, цифрами и «_»');
      return { command: m[1], description: m[2].trim() };
    });

    body.replaceChildren(
      h('div', { class: 'bc-info-top' }, av, h('div', {}, h('div', { class: 'ttl' }, p.name || p.first_name || 'бот'), h('div', { class: 'muted' }, p.username ? '@' + p.username : ''))),
      h('div', { class: 'inline' }, h('button', { class: 'btn small', type: 'button', onclick: () => fileIn.click() }, 'сменить фото'),
        confirmBtn('убрать фото', () => act(async () => { await api('/botchat/myphoto_remove', {}); refreshAv(); toast('фото убрано', 'ok'); })), fileIn),
      field('имя', h('div', { class: 'inline' }, h('div', { class: 'grow' }, name), save('сохранить', async () => {
        await api('/botchat/myprofile_set', { name: name.value });
        if (this.me) { this.me.name = name.value || this.me.name; }
      })), 'до 64 символов; Telegram ограничивает частоту смены имени'),
      field('короткое описание', h('div', {}, short, save('сохранить', () => api('/botchat/myprofile_set', { short_description: short.value }))), 'до 120 символов'),
      field('описание', h('div', {}, desc, save('сохранить', () => api('/botchat/myprofile_set', { description: desc.value }))), 'до 512 символов'),
      field('команды', h('div', {}, cmds, save('сохранить команды', async () => {
        let list; try { list = parse(); } catch (e) { throw e; }
        await api('/botchat/mycommands_set', { commands: list });
      })), 'по одной в строке: «команда - описание»; латиница, цифры и «_». Пустой список снимает все команды'),
      h('div', { class: 'inline bc-danger' }, h('button', { class: 'btn small', type: 'button', onclick: () => close() }, 'закрыть')));
  }
}
