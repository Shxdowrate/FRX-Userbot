/* FRX web. Главная: плашка с вкладками README | changelog | логи; страница «files»; загрузка файлов с прогрессом.
 * Загружается после app.js и использует его утилиты (h, api, act, toast, plural, fmtSize, shell, glass, empty, mount, TOKEN, GUARD…)
 * только во время вызова. Любые данные попадают в DOM через textContent/createElement — никакого innerHTML с чужим текстом. */
'use strict';

/* ------------------------------------------------------------------ *
 * загрузка файла с индикатором прогресса (fetch прогресс отправки не умеет — XMLHttpRequest)
 * ------------------------------------------------------------------ */

function apiUpload(path, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', '/api' + path);
    xhr.setRequestHeader('X-FRX', '1');
    if (onProgress && xhr.upload) xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || '{}'); } catch (e) { /* пустой ответ */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data); else reject(new Error(data.error || ('ошибка ' + xhr.status)));
    };
    xhr.onerror = () => reject(new Error('не удалось отправить файл — связь с юзерботом прервалась'));
    xhr.onabort = () => reject(new Error('загрузка отменена'));
    xhr.send(file);
  });
}

const PN_TAB_KEY = 'frx_home_tab';

/* ------------------------------------------------------------------ *
 * README
 * ------------------------------------------------------------------ */

function rmInline(nodes, ctx) {
  return (nodes || []).map((n) => {
    if (typeof n === 'string') return document.createTextNode(n);
    const kids = rmInline(n.c, ctx);
    switch (n.t) {
      case 'code': return h('code', {}, kids);
      case 'b': return h('b', {}, kids);
      case 'i': return h('i', {}, kids);
      case 'a':
        if (n.jump !== undefined) return h('a', { href: '#', class: 'rm-jump', onclick: (e) => { e.preventDefault(); ctx.open(n.jump); } }, kids);
        return h('a', { href: n.href, target: '_blank', rel: 'noopener noreferrer' }, kids);
      default: return h('span', {}, kids);
    }
  });
}

function rmBlock(b, ctx) {
  switch (b.t) {
    case 'h': return h('h' + Math.min(b.level + 1, 6), { class: 'rm-h' }, rmInline(b.c, ctx));
    case 'p': return h('p', { class: 'rm-p' }, rmInline(b.c, ctx));
    case 'quote': return h('blockquote', { class: 'rm-quote' }, rmInline(b.c, ctx));
    case 'hr': return h('hr', { class: 'rm-hr' });
    case 'code': {
      const pre = h('pre', { class: 'rm-code' });
      if (/^(py|python)$/i.test(b.lang) && typeof pyHighlight === 'function') pre.innerHTML = pyHighlight(b.text); else pre.textContent = b.text;     // pyHighlight экранирует каждый фрагмент
      return h('div', { class: 'rm-codewrap' }, pre, h('button', { class: 'rm-copy', type: 'button', title: 'скопировать', onclick: () => { navigator.clipboard?.writeText(b.text); toast('скопировано', 'ok'); } }, '⧉'));
    }
    case 'table': return h('div', { class: 'rm-tablewrap' }, h('table', { class: 'rm-table' },
      h('thead', {}, h('tr', {}, b.head.map((c) => h('th', {}, rmInline(c, ctx))))),
      h('tbody', {}, b.rows.map((r) => h('tr', {}, r.map((c) => h('td', {}, rmInline(c, ctx))))))));
    case 'list': {
      let n = 0;
      return h('div', { class: 'rm-list' }, b.items.map((it) => {
        n = it.ordered ? n + 1 : 0;
        return h('div', { class: 'rm-li', style: `margin-left:${it.level * 18}px` }, h('span', { class: 'rm-bullet' }, it.ordered ? n + '.' : '•'), h('span', {}, rmInline(it.c, ctx)));
      }));
    }
    default: return h('div', {});
  }
}

/* подсветка найденного слова в уже построенном DOM (без innerHTML) */
function rmHighlight(root, query) {
  const q = query.toLowerCase();
  if (q.length < 2) return null;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const targets = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode()) if (n.nodeValue.toLowerCase().includes(q)) targets.push(n);
  let first = null;
  targets.slice(0, 200).forEach((node) => {
    const text = node.nodeValue, low = text.toLowerCase(), frag = document.createDocumentFragment();
    let pos = 0, i;
    while ((i = low.indexOf(q, pos)) !== -1) {
      if (i > pos) frag.appendChild(document.createTextNode(text.slice(pos, i)));
      const mk = h('mark', {}, text.slice(i, i + q.length));
      if (!first) first = mk;
      frag.appendChild(mk);
      pos = i + q.length;
    }
    if (pos < text.length) frag.appendChild(document.createTextNode(text.slice(pos)));
    node.parentNode.replaceChild(frag, node);
  });
  return first;
}

function readmePane() {
  const state = { sections: [], cur: -1, q: '', timer: 0, hits: null };
  const toc = h('div', { class: 'rm-toc' }), view = h('div', { class: 'rm-view' });
  const search = h('input', { class: 'field rm-search', placeholder: 'поиск по README…', spellcheck: 'false', 'aria-label': 'поиск по README' });
  const nav = h('div', { class: 'rm-nav' });
  const ctx = { open: (i, q) => openSection(i, q) };
  // телефон: разделы — выпадающим списком по кнопке с названием текущего раздела
  const pickTitle = h('span', { class: 'rm-pick-t' }, 'разделы');
  const pickBtn = h('button', { type: 'button', class: 'rm-pick m-only', 'aria-expanded': 'false', onclick: () => setPick(!el.classList.contains('pick-open')) },
    h('span', { class: 'rm-pick-i' }, '☰'), pickTitle, h('span', { class: 'rm-pick-n' }), h('span', { class: 'rm-pick-c' }, '▾'));
  function setPick(on) {
    el.classList.toggle('pick-open', on);
    pickBtn.setAttribute('aria-expanded', on ? 'true' : 'false');
    if (on) { const cur = toc.querySelector('.rm-t.on'); if (cur) cur.scrollIntoView({ block: 'center' }); }
  }

  function drawToc() {
    if (state.hits) {
      toc.replaceChildren(...(state.hits.length ? state.hits.map((x) => h('button', { type: 'button', class: 'rm-t' + (x.i === state.cur ? ' on' : ''), onclick: () => openSection(x.i, state.q) },
        h('span', { class: 'rm-tt' }, x.title), h('span', { class: 'rm-sn' }, `${x.count} · ${x.snippet}`))) : [h('div', { class: 'muted', style: 'padding:8px' }, 'Ничего не найдено')]));
      return;
    }
    toc.replaceChildren(...state.sections.map((s) => h('button', { type: 'button', class: 'rm-t' + (s.i === state.cur ? ' on' : ''), onclick: () => openSection(s.i) }, h('span', { class: 'rm-tt' }, s.title))));
  }

  async function openSection(i, q) {
    try {
      const d = await api('/readme/section?i=' + i);
      state.cur = i;
      setPick(false);
      pickTitle.textContent = (state.sections[i] || {}).title || 'разделы';
      pickBtn.querySelector('.rm-pick-n').textContent = state.sections.length ? `${i + 1}/${state.sections.length}` : '';
      view.replaceChildren(...d.blocks.map((b) => rmBlock(b, ctx)));
      nav.replaceChildren(
        h('button', { class: 'btn small', type: 'button', disabled: d.prev === null, onclick: () => openSection(d.prev) }, '‹ ' + (state.sections[d.prev] ? state.sections[d.prev].title.slice(0, 26) : '')),
        h('span', { class: 'grow' }),
        h('button', { class: 'btn small', type: 'button', disabled: d.next === null, onclick: () => openSection(d.next) }, (state.sections[d.next] ? state.sections[d.next].title.slice(0, 26) : '') + ' ›'));
      const mark = q ? rmHighlight(view, q) : null;
      if (mark) mark.scrollIntoView({ block: 'center' }); else view.scrollTop = 0;
      drawToc();
    } catch (e) { toast(e.message, 'error'); }
  }

  search.addEventListener('input', () => {
    clearTimeout(state.timer);
    state.timer = setTimeout(async () => {
      state.q = search.value.trim();
      if (state.q.length < 2) { state.hits = null; drawToc(); return; }
      try { state.hits = (await api('/readme/search?q=' + encodeURIComponent(state.q))).hits; } catch (e) { state.hits = []; }
      drawToc();
    }, 250);
  });

  const el = h('div', { class: 'rm' }, pickBtn, h('div', { class: 'rm-side' }, search, toc), h('div', { class: 'rm-main' }, view, nav));
  el.addEventListener('click', (e) => { if (el.classList.contains('pick-open') && !e.target.closest('.rm-side, .rm-pick')) setPick(false); });
  el._load = async () => {
    if (state.sections.length) return;
    try {
      state.sections = (await api('/readme')).sections;
      const start = state.sections.findIndex((s) => /^Оглавление/i.test(s.title));
      drawToc();
      await openSection(start >= 0 ? start : 0);
    } catch (e) { view.replaceChildren(h('div', { class: 'empty' }, e.message)); }
  };
  return el;
}

/* ------------------------------------------------------------------ *
 * логи
 * ------------------------------------------------------------------ */

function logsPane(T) {
  const S = { kind: 'all', level: 'all', q: '', last: 0, busy: false, paused: false, follow: true, timer: 0, active: false, rows: [] };
  const list = h('div', { class: 'lg-list' });
  const info = h('span', { class: 'lg-info' });
  const chips = (key, items) => h('div', { class: 'chips' }, items.map(([v, l]) => h('button', { type: 'button', class: 'chip small' + (S[key] === v ? ' on' : ''), 'data-v': v, onclick: (e) => {
    S[key] = v; [...e.currentTarget.parentNode.children].forEach((c) => c.classList.toggle('on', c.dataset.v === v)); reset();
  } }, l)));
  const search = h('input', { class: 'field lg-search', placeholder: 'поиск…', spellcheck: 'false', 'aria-label': 'поиск по логам' });
  let st = 0;
  search.addEventListener('input', () => { clearTimeout(st); st = setTimeout(() => { S.q = search.value.trim(); reset(); }, 250); });
  const pauseBtn = h('button', { class: 'btn small', type: 'button', onclick: () => { S.paused = !S.paused; pauseBtn.textContent = S.paused ? '▶ продолжить' : '⏸ пауза'; if (!S.paused) poll(); } }, '⏸ пауза');
  const followBox = h('input', { type: 'checkbox', class: 'wi-cb' }); followBox.checked = true;
  followBox.addEventListener('change', () => { S.follow = followBox.checked; if (S.follow) list.scrollTop = list.scrollHeight; });
  const toolbar = h('div', { class: 'lg-bar' }, chips('kind', [['all', 'все'], ['userbot', 'юзербот'], ['bot', 'бот']]), chips('level', [['all', 'любые'], ['info', 'инфо'], ['warn', 'предупр.'], ['error', 'ошибки']]), search,
    h('span', { class: 'grow' }), h('label', { class: 'wi-check', title: 'прокручивать вниз при новых записях' }, followBox, h('span', {}, 'следить')), pauseBtn,
    h('button', { class: 'btn small', type: 'button', title: 'скопировать показанные записи', onclick: () => { navigator.clipboard?.writeText(S.rows.map((r) => `${r.stamp} [${r.kind}] ${r.text}`).join('\n')); toast('скопировано', 'ok'); } }, 'копировать'),
    h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/logs/clear', {}); reset(); }) }, 'очистить'));

  function addRows(items) {
    if (!items.length) return;
    const near = list.scrollHeight - list.scrollTop - list.clientHeight < 60;
    const frag = document.createDocumentFragment();
    items.forEach((it) => {
      S.rows.push(it);
      frag.appendChild(h('div', { class: 'lg-row ' + it.level + ' ' + it.kind }, h('span', { class: 'lg-ts' }, it.stamp), h('span', { class: 'lg-kind' }, it.kind === 'bot' ? 'бот' : 'юзербот'), h('span', { class: 'lg-text' }, it.text)));
    });
    list.appendChild(frag);
    while (S.rows.length > 800) { S.rows.shift(); list.firstChild.remove(); }
    if (S.follow && (near || S.rows.length === items.length)) list.scrollTop = list.scrollHeight;
  }
  function reset() { S.last = 0; S.rows = []; list.replaceChildren(); poll(); }

  async function poll() {
    if (!S.active || S.busy || S.paused || T !== TOKEN) return;
    S.busy = true;
    try {
      const q = `/logs?since=${S.last}&kind=${S.kind}&level=${S.level}` + (S.q ? '&q=' + encodeURIComponent(S.q) : '');
      const d = await api(q);
      S.last = d.last;
      addRows(d.items);
      info.textContent = d.installed ? '' : 'Запись логов выключена: юзербот запущен не через main.py.';
      if (!S.rows.length && !list.firstChild) list.appendChild(h('div', { class: 'lg-empty' }, 'Записей пока нет — они появляются по мере работы юзербота и бота.'));
      else list.querySelector('.lg-empty')?.remove();
    } catch (e) { /* сайт перезапускается — следующий опрос повторит */ }
    finally { S.busy = false; }
  }
  const loop = () => { clearTimeout(S.timer); if (!S.active || T !== TOKEN) return; poll().finally(() => { S.timer = setTimeout(loop, document.hidden ? 4000 : 1500); }); };

  const el = h('div', { class: 'lg' }, toolbar, info, list);
  el._start = () => { S.active = true; loop(); };
  el._stop = () => { S.active = false; clearTimeout(S.timer); };
  return el;
}

/* ------------------------------------------------------------------ *
 * главная: три отдельных окна — README слева внизу, changelog по центру, логи справа (пара последних строк).
 * Нажатие раскрывает полное окно: оно «вырастает» из карточки и сворачивается обратно.
 * ------------------------------------------------------------------ */

function hwOpen(title, content, from, onOpen, onClose) {
  const veil = h('div', { class: 'hw-veil' });
  let closed = false;
  const win = h('div', { class: 'glass hw', role: 'dialog', 'aria-label': title });
  const close = () => {
    if (closed) return;
    closed = true;
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', close);
    if (onClose) onClose();
    const done = () => { veil.remove(); win.remove(); };
    if (REDUCED.matches || !from.isConnected) { done(); return; }
    const r0 = from.getBoundingClientRect(), r1 = win.getBoundingClientRect();
    veil.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 260, easing: 'ease', fill: 'forwards' });
    const a = win.animate([{ transformOrigin: 'top left', transform: 'none', opacity: 1 },
      { transformOrigin: 'top left', transform: `translate(${r0.left - r1.left}px, ${r0.top - r1.top}px) scale(${r0.width / r1.width}, ${r0.height / r1.height})`, opacity: 0 }],
    { duration: 340, easing: EASE, fill: 'forwards' });
    a.onfinish = done;
  };
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  win.append(h('div', { class: 'hw-head' }, h('b', {}, title), h('span', { class: 'grow' }), h('button', { class: 'wi-ico', type: 'button', title: 'закрыть (Esc)', onclick: close }, '✕')),
    h('div', { class: 'hw-body' }, content));
  veil.addEventListener('click', close);
  document.body.append(veil, win);
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close);
  if (!REDUCED.matches && from && from.isConnected) {
    const r0 = from.getBoundingClientRect(), r1 = win.getBoundingClientRect();
    veil.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 300, easing: 'ease' });
    win.animate([{ transformOrigin: 'top left', transform: `translate(${r0.left - r1.left}px, ${r0.top - r1.top}px) scale(${r0.width / r1.width}, ${r0.height / r1.height})`, opacity: 0 },
      { transformOrigin: 'top left', transform: 'none', opacity: 1 }], { duration: 460, easing: EASE });
  }
  if (onOpen) onOpen();
  return close;
}

function homeDock(T, entries, clPane) {
  const readme = readmePane(), logs = logsPane(T);
  const openers = {};

  /* README — слева */
  const rmSub = h('span', { class: 'hd-sub' }, 'справка по всем возможностям FRX');
  const rmCard = h('button', { type: 'button', class: 'glass hd-card hd-readme', 'aria-label': 'открыть README' },
    h('span', { class: 'hd-title' }, 'README'), rmSub, h('span', { class: 'hd-open' }, 'открыть ›'));
  api('/readme').then((d) => { if (T === TOKEN) { const n = (d.sections || []).length; if (n) rmSub.textContent = `${n} ${plural(n, 'раздел', 'раздела', 'разделов')} · поиск по тексту`; } }).catch(() => {});
  rmCard.addEventListener('click', () => hwOpen('README', readme, rmCard, () => readme._load()));

  /* changelog — по центру */
  let clCard = null;
  if (entries.length) {
    const e0 = entries[0];
    clCard = h('button', { type: 'button', class: 'glass hd-card hd-cl', 'aria-label': 'открыть changelog' },
      h('span', { class: 'hd-title' }, 'changelog', h('span', { class: 'hd-ver' }, 'v' + e0.version), e0.date ? h('span', { class: 'hd-date' }, e0.date) : null),
      ...e0.items.slice(0, 2).map((t) => h('span', { class: 'hd-line' }, t)), h('span', { class: 'hd-open' }, 'все версии ›'));
    clCard.addEventListener('click', () => hwOpen('changelog', clPane, clCard));
  }

  /* логи — справа, без плашки: пара последних строк */
  const tail = h('div', { class: 'hd-tail' });
  const logBox = h('button', { type: 'button', class: 'hd-logs', 'aria-label': 'открыть логи', title: 'открыть логи' }, tail);
  const rows = [];
  let last = 0, busy = false, timer = 0;
  const draw = () => {
    tail.replaceChildren(...(rows.length ? rows.map((r) => h('div', { class: 'hd-lrow ' + r.level }, h('span', { class: 'hd-lts' }, r.stamp.slice(6)), h('span', { class: 'hd-ltx' }, r.text.split('\n')[0])))
      : [h('div', { class: 'hd-lrow' }, h('span', { class: 'hd-ltx' }, 'логи появятся здесь'))]));
    tail.classList.remove('tick'); void tail.offsetWidth; tail.classList.add('tick');
  };
  async function pollTail() {
    if (T !== TOKEN || busy) return;
    busy = true;
    try {
      const d = await api(`/logs?since=${last}&limit=2`);
      last = d.last;
      if (d.items.length) { rows.push(...d.items); while (rows.length > 2) rows.shift(); draw(); }
      else if (!tail.firstChild) draw();
    } catch (e) { /* сайт перезапускается */ } finally { busy = false; }
  }
  const tailLoop = () => { clearTimeout(timer); if (T !== TOKEN || !logBox.isConnected && timer) return; pollTail().finally(() => { timer = setTimeout(tailLoop, document.hidden ? 5000 : 2000); }); };
  let paused = false;
  logBox.addEventListener('click', () => { paused = true; clearTimeout(timer); hwOpen('логи', logs, logBox, () => logs._start(), () => { logs._stop(); paused = false; last = 0; rows.length = 0; tailLoop(); }); });
  requestAnimationFrame(tailLoop);

  const dock = h('div', { class: 'hd' }, rmCard, clCard || h('span'), logBox);
  [...dock.children].forEach((c, i) => c.style.setProperty('--i', i));
  void openers; void paused;
  return dock;
}

/* ------------------------------------------------------------------ *
 * страница «files»
 * ------------------------------------------------------------------ */

const FL_ICON = { image: '🖼', video: '🎞', audio: '♫', text: '≡', none: '▣' };
const FL_THUMB_MAX = 6 * 1024 * 1024;

async function flBlobUrl(f, dl) {                                       // файл через fetch с заголовком X-FRX → blob-адрес
  const r = await fetch(`/api/files/raw?src=${encodeURIComponent(f.src)}&id=${encodeURIComponent(f.id === null || f.id === undefined ? f.name : f.id)}` + (dl ? '&dl=1' : ''), { headers: { 'X-FRX': '1' } });
  if (!r.ok) { let m = 'файл недоступен'; try { m = (await r.json()).error || m; } catch (e) { /* не JSON */ } throw new Error(m); }
  return URL.createObjectURL(await r.blob());
}
const flKey = (f) => ({ src: f.src, id: f.id === null || f.id === undefined ? f.name : f.id });

async function filesView(T) {
  let data = await api('/files');
  const S = { q: '', kind: 'all', urls: new Set() };
  const root = h('div', { class: 'fl' });
  const search = h('input', { class: 'field fl-search', placeholder: 'поиск по имени…', spellcheck: 'false', 'aria-label': 'поиск файлов' });
  search.addEventListener('input', () => { S.q = search.value.trim().toLowerCase(); draw(); });
  const kinds = [['all', 'все'], ['image', 'картинки'], ['video', 'видео'], ['audio', 'аудио'], ['text', 'текст'], ['none', 'прочее']];
  const kindChips = h('div', { class: 'chips' }, kinds.map(([v, l]) => h('button', { type: 'button', class: 'chip small' + (v === 'all' ? ' on' : ''), 'data-v': v, onclick: (e) => {
    S.kind = v; [...e.currentTarget.parentNode.children].forEach((c) => c.classList.toggle('on', c.dataset.v === v)); draw(); } }, l)));

  const observer = ('IntersectionObserver' in window) ? new IntersectionObserver((entries) => {
    entries.forEach((en) => { if (en.isIntersecting) { observer.unobserve(en.target); en.target._load && en.target._load(); } });
  }, { rootMargin: '200px' }) : null;

  function card(f) {
    const thumb = h('div', { class: 'fl-thumb ' + f.kind }, h('span', { class: 'fl-ico' }, FL_ICON[f.kind] || '▣'));
    if (f.kind === 'image' && f.exists && f.size <= FL_THUMB_MAX && f.servable) {
      thumb._load = async () => {
        try { const url = await flBlobUrl(f); S.urls.add(url); thumb.replaceChildren(h('img', { src: url, alt: f.name, loading: 'lazy' })); } catch (e) { /* остаётся значок */ }
      };
      if (observer) observer.observe(thumb); else thumb._load();
    }
    const missing = !f.exists;
    return h('div', { class: 'fl-card' + (missing ? ' missing' : ''), tabindex: '0', onclick: () => openFile(f), onkeydown: (e) => { if (e.key === 'Enter') openFile(f); } },
      thumb,
      h('div', { class: 'fl-meta' }, h('b', { class: 'fl-name', title: f.name }, f.name),
        h('span', { class: 'fl-sub' }, missing ? 'файла нет на диске' : fmtSize(f.size) + (f.mime ? ' · ' + f.mime : ''))),
      h('div', { class: 'fl-actions' },
        h('button', { class: 'lb-x', type: 'button', title: 'скачать', disabled: missing || !f.servable, onclick: (e) => { e.stopPropagation(); download(f); } }, '⬇'),
        f.readonly ? null : h('button', { class: 'lb-x bad', type: 'button', title: 'удалить', onclick: (e) => { e.stopPropagation(); remove(f); } }, '×')));
  }

  const matches = (f) => (!S.q || f.name.toLowerCase().includes(S.q) || (f.rel || '').toLowerCase().includes(S.q)) && (S.kind === 'all' || f.kind === S.kind);
  function section(title, hint, items, extra) {
    const shown = items.filter(matches);
    return h('div', { class: 'fl-sec' }, h('div', { class: 'h2', style: 'padding-left:6px' }, `${title} · ${items.length}`), hint ? h('div', { class: 'faint', style: 'margin:-4px 0 10px 6px;max-width:760px' }, hint) : null,
      glass(shown.length ? h('div', { class: 'fl-grid' }, shown.map(card)) : h('div', { class: 'empty' }, items.length ? 'ничего не найдено по фильтру' : 'пусто'), extra || null));
  }

  function draw() {
    root.replaceChildren(...[
      section('Файлы юзербота', 'Реестр files: то, что модули создали через files.create_file или скачали командой (download_reply_file). Лежат в user/data/files.', data.registry),
      data.orphans.length ? section('Без записи в реестре', 'Файлы в user/data/files, о которых реестр не знает (например, остались после ручных правок). Откройте файл, чтобы добавить его в реестр.', data.orphans) : null,
      section('Мои ассеты (user/assets/)', 'Ваши файлы, которые модули читают через engine.asset_path(). Файл с тем же именем, что у стокового, заменяет его. Обновление их не трогает.', data.assets),
      (data.stock || []).length ? section('Стоковые ассеты (assets/)', 'Файлы, которые идут вместе с FRX и обновляются командой .update. Только просмотр и скачивание.', data.stock) : null].filter(Boolean));
  }

  async function reload() { data = await api('/files'); draw(); }

  async function download(f) {
    await act(async () => {
      const r = await api('/files/link', flKey(f));
      frxDownload(location.origin + r.url, r.name, r.size);
    });
  }

  function remove(f) {
    const what = f.src === 'registry' ? 'Запись и файл будут удалены (модули, которые на него ссылаются, перестанут его находить).' : 'Файл будет удалён с диска.';
    if (!confirm(`Удалить «${f.name}»?\n${what}`)) return;
    act(async () => { await api('/files/delete', flKey(f)); toast('удалено', 'ok'); await reload(); });
  }

  /* ---------- просмотр ---------- */
  function openFile(f) {
    const view = h('div', { class: 'fl-view' });
    let url = null;
    const info = h('div', { class: 'fl-info' },
      h('div', {}, h('span', { class: 'label' }, 'путь'), h('code', {}, f.path)),
      f.src === 'registry' ? h('div', {}, h('span', { class: 'label' }, 'id'), h('code', {}, f.id)) : null,
      h('div', {}, h('span', { class: 'label' }, 'размер'), h('span', {}, fmtSize(f.size))),
      f.mime ? h('div', {}, h('span', { class: 'label' }, 'тип'), h('span', {}, f.mime)) : null);
    const note = (t) => view.replaceChildren(h('div', { class: 'wi-note' }, t));
    (async () => {
      if (!f.exists) { note('Файла нет на диске — запись реестра можно удалить.'); return; }
      if (!f.servable) { note('Файл лежит вне папок user/data/ и assets/ — сайт его не открывает.'); return; }
      if (!f.preview) { note(f.kind === 'none' ? 'Для этого типа файлов предпросмотра нет — его можно скачать.' : `Файл больше ${data.limits.preview_mb} МБ — предпросмотр отключён, его можно скачать.`); return; }
      try {
        if (f.kind === 'text') {
          const t = await api(`/files/text?src=${encodeURIComponent(f.src)}&id=${encodeURIComponent(flKey(f).id)}`);
          view.replaceChildren(...[h('pre', { class: 'fl-text' }, t.text), t.truncated ? h('div', { class: 'wi-hint' }, `Показано начало файла (${fmtSize(64 * 1024)} из ${fmtSize(t.size)}).`) : null].filter(Boolean));
          return;
        }
        url = await flBlobUrl(f); S.urls.add(url);
        view.replaceChildren(f.kind === 'image' ? h('img', { class: 'fl-img', src: url, alt: f.name })
          : f.kind === 'video' ? h('video', { class: 'fl-media', src: url, controls: '', preload: 'metadata' }) : h('audio', { class: 'fl-audio', src: url, controls: '' }));
      } catch (e) { note(e.message); }
    })();
    wiDrawer(f.name, [view, info], null, (close) => [
      f.src === 'orphan' ? h('button', { class: 'btn', onclick: () => act(async () => { await api('/files/adopt', { id: f.name }); toast('добавлено в реестр', 'ok'); close(); await reload(); }) }, 'добавить в реестр') : null,
      h('button', { class: 'btn', disabled: !f.exists || !f.servable, onclick: () => download(f) }, 'скачать'),
      f.readonly ? null : h('button', { class: 'btn danger', onclick: () => { close(); remove(f); } }, 'удалить'),
      h('button', { class: 'btn primary', onclick: close }, 'закрыть')]);
  }

  /* ---------- загрузка ---------- */
  function openUpload(initial) {
    const st = { dest: 'registry', dir: '', overwrite: false, files: initial ? [...initial] : [] };
    const list = h('div', { class: 'fl-uplist' });
    const dir = h('input', { class: 'wi-field', placeholder: 'подпапка в ассетах (необязательно), например img', spellcheck: 'false' });
    dir.addEventListener('input', () => { st.dir = dir.value.trim(); });
    const dirRow = h('div', { class: 'wi-cform' }, h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Подпапка'), dir), (() => { const cb = h('input', { type: 'checkbox', class: 'wi-cb' }); cb.addEventListener('change', () => { st.overwrite = cb.checked; }); return h('label', { class: 'wi-check' }, cb, h('span', {}, 'заменять файлы с тем же именем')); })());
    dirRow.hidden = true;
    const destSel = h('select', { class: 'wi-field' }, [['registry', 'Файлы юзербота (реестр, user/data/files)'], ['assets', 'Мои ассеты (user/assets/)']].map(([v, l]) => h('option', { value: v }, l)));
    destSel.addEventListener('change', () => { st.dest = destSel.value; dirRow.hidden = st.dest !== 'assets'; });
    const picker = h('input', { type: 'file', multiple: '' });
    picker.addEventListener('change', () => { st.files.push(...picker.files); picker.value = ''; drawList(); });
    const drop = h('div', { class: 'fl-drop' }, 'Перетащите файлы сюда или ', h('button', { class: 'link', type: 'button', onclick: () => picker.click() }, 'выберите'), h('div', { class: 'wi-hint' }, `до ${data.limits.upload_mb} МБ каждый`));
    drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.add('over'); });
    drop.addEventListener('dragleave', () => drop.classList.remove('over'));
    drop.addEventListener('drop', (e) => { e.preventDefault(); drop.classList.remove('over'); st.files.push(...e.dataTransfer.files); drawList(); });
    const bars = new Map();
    function drawList() {
      bars.clear();
      list.replaceChildren(...st.files.map((f, i) => {
        const bar = h('div', { class: 'fl-bar' }, h('i', {})); bars.set(f, bar);
        return h('div', { class: 'fl-upl' }, h('span', { class: 'fl-upn' }, f.name), h('span', { class: 'wi-hint' }, fmtSize(f.size)),
          h('button', { class: 'wi-ico', type: 'button', onclick: () => { st.files.splice(i, 1); drawList(); } }, '×'), bar);
      }));
    }
    drawList();
    let busy = false;
    wiDrawer('Загрузить файлы', [h('div', { class: 'wi-cform' }, h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Куда'), destSel), dirRow, drop, list)], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: async (e) => {
        if (busy || !st.files.length) { if (!st.files.length) toast('выберите файлы', 'error'); return; }
        const btn = e.currentTarget;                    // после await currentTarget уже null
        busy = true; btn.disabled = true;
        let ok = 0;
        for (const f of [...st.files]) {
          const bar = bars.get(f);
          try {
            const q = `/files/upload?dest=${st.dest}&name=${encodeURIComponent(f.name)}` + (st.dest === 'assets' ? `&dir=${encodeURIComponent(st.dir)}${st.overwrite ? '&overwrite=1' : ''}` : '');
            await apiUpload(q, f, (p) => { if (bar) bar.firstChild.style.width = Math.round(p * 100) + '%'; });
            if (bar) bar.classList.add('done');
            ok++;
          } catch (er) { if (bar) { bar.classList.add('fail'); bar.title = er.message; } toast(`${f.name}: ${er.message}`, 'error'); }
        }
        busy = false; btn.disabled = false;
        if (ok) { toast(`загружено: ${ok}`, 'ok'); await reload(); }
        if (ok === st.files.length) close();
      } }, 'загрузить')]);
  }

  /* перетаскивание файлов на страницу */
  root.addEventListener('dragover', (e) => { if (e.dataTransfer && [...e.dataTransfer.types].includes('Files')) e.preventDefault(); });
  root.addEventListener('drop', (e) => { if (e.dataTransfer && e.dataTransfer.files.length) { e.preventDefault(); openUpload(e.dataTransfer.files); } });

  draw();
  const node = shell('files', { side: [h('button', { class: 'btn primary', type: 'button', onclick: () => openUpload() }, 'загрузить')] },
    h('div', { class: 'fl-bar2' }, search, kindChips), root);
  node.classList.add('wide');
  mount(T, node);
  const cleanup = setInterval(() => { if (T !== TOKEN) { clearInterval(cleanup); S.urls.forEach((u) => URL.revokeObjectURL(u)); S.urls.clear(); if (observer) observer.disconnect(); } }, 2000);
}

/* ------------------------------------------------------------------ *
 * панель активных задач — справа, рейка из чёрточек. Открытый модуль / Emulate / редактор запоминаются,
 * к ним можно вернуться, ✕ закрывает. Только в памяти страницы: перезагрузка стирает всё.
 * ------------------------------------------------------------------ */

const TASKS = { list: [], el: null, rail: null };
const TASK_MAX = 9;
const TASK_PATTERNS = [
  [/^modules\/([^/]+)\/emulate$/, (m) => ({ key: 'emu:' + m[1], name: 'Emulate', detail: m[1] + ' · команды и формы' })],
  [/^webide\/([^/]+)$/, (m) => ({ key: 'ide:' + m[1], name: m[1] === '__new__' ? 'WebIDE' : 'WebIDE', detail: (m[1] === '__new__' ? 'новый модуль' : m[1]) + ' · редактор' })],
];

function taskAgo(ts) {
  const s = Math.max(0, Math.round((Date.now() - ts) / 1000));
  if (s < 45) return 'только что';
  const m = Math.round(s / 60);
  return m < 60 ? `${m} мин назад` : `${Math.round(m / 60)} ч назад`;
}

/* Sandbox открывается панелью внутри редактора, у него нет своего адреса — задача добавляется вручную */
function taskAdd(t) {
  const old = TASKS.list.find((x) => x.key === t.key);
  if (old) { old.hash = t.hash; old.seen = Date.now(); }
  else { TASKS.list.push({ ...t, since: Date.now(), seen: Date.now(), fresh: true }); if (TASKS.list.length > TASK_MAX) TASKS.list.shift(); }
  taskRender(curPath());
}

function taskTrack(path) {
  for (const [re, make] of TASK_PATTERNS) {
    const m = path.match(re);
    if (!m) continue;
    const t = make(m.map((x) => (x === undefined ? x : decodeURIComponent(x))));
    const old = TASKS.list.find((x) => x.key === t.key);
    if (old) { old.hash = '#/' + path; old.seen = Date.now(); }
    else {
      TASKS.list.push({ ...t, hash: '#/' + path, since: Date.now(), seen: Date.now(), fresh: true });
      if (TASKS.list.length > TASK_MAX) TASKS.list.shift();
    }
    break;
  }
  taskRender(path);
}

function taskRender(path) {
  if (!TASKS.el) {
    TASKS.rail = h('div', { class: 'tr-rail' });
    TASKS.el = h('div', { class: 'tr', 'aria-label': 'активные задачи' }, TASKS.rail);
    document.body.appendChild(TASKS.el);
  }
  const cur = '#/' + (path || '');
  TASKS.el.hidden = !TASKS.list.length;
  TASKS.rail.replaceChildren(...TASKS.list.map((t) => {
    const on = t.hash === cur;
    const item = h('div', { class: 'tr-item' + (on ? ' on' : '') + (t.fresh ? ' new' : '') },
      h('button', { type: 'button', class: 'tr-card', title: on ? 'вы здесь' : 'вернуться: ' + t.name, onclick: () => { if (!on) location.hash = t.hash; } },
        h('i', { class: 'tr-tick' }), h('span', { class: 'tr-name' }, t.name), h('span', { class: 'tr-detail' }, h('span', {}, t.detail)), h('span', { class: 'tr-ago' }, h('span', {}, on ? 'открыто сейчас' : taskAgo(t.seen)))),
      h('button', { type: 'button', class: 'tr-x', title: 'закрыть задачу', 'aria-label': 'закрыть задачу ' + t.name, onclick: (e) => {
        e.stopPropagation();
        const i = TASKS.list.indexOf(t); if (i >= 0) TASKS.list.splice(i, 1);
        item.classList.add('out');
        setTimeout(() => taskRender(curPath()), REDUCED.matches ? 0 : 260);
      } }, '✕'));
    t.fresh = false;
    return item;
  }));
}
