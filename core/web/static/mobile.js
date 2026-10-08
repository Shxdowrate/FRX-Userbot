/* FRX web — помощники для телефона (ширина ≤ 760px). На компьютере ничего не меняют.
 *
 * mIsPhone()                    — сейчас узкий экран?
 * mSheet(title, body, opts)     — шторка снизу: закрывается тапом мимо, свайпом вниз, Esc и «×». Возвращает close().
 *                                 opts: { full, onClose, foot: (close) => [кнопки], keep: true — body не удалять, а вернуть на место }
 * mActions(title, items)        — шторка-список действий: [{ icon, label, hint, bad, disabled, run }] | null (разделитель)
 * mSeg(items, cur, onPick)      — переключатель-сегменты: [[id, label, badgeNode?]] → { el, set(id) }
 * mFab(label, onClick, title)   — плавающая круглая кнопка в правом нижнем углу (только на телефоне)
 */

const M_PHONE = window.matchMedia('(max-width: 760px)');
function mIsPhone() { return M_PHONE.matches; }

let M_SHEETS = 0;
function mSheet(title, body, opts) {
  opts = opts || {};
  const home = opts.keep && body && body.parentNode ? { parent: body.parentNode, next: body.nextSibling } : null;
  const veil = h('div', { class: 'm-veil' });
  const bodyBox = h('div', { class: 'm-sbody' }, body);
  const box = h('div', { class: 'm-sheet' + (opts.full ? ' full' : ''), role: 'dialog', 'aria-label': title || 'окно' },
    h('div', { class: 'm-grab' }, h('i')),
    title ? h('div', { class: 'm-shead' }, h('b', {}, title), h('button', { type: 'button', class: 'm-sx', 'aria-label': 'закрыть', onclick: () => close() }, '×')) : null,
    bodyBox);
  let foot = null;
  let closed = false;
  function close() {
    if (closed) return;
    closed = true;
    M_SHEETS = Math.max(0, M_SHEETS - 1);
    if (!M_SHEETS) document.documentElement.classList.remove('m-sheet-open');
    veil.classList.remove('in'); box.classList.remove('in');
    document.removeEventListener('keydown', onKey, true);
    window.removeEventListener('hashchange', close);
    setTimeout(() => {
      if (home) { if (home.next && home.next.parentNode === home.parent) home.parent.insertBefore(body, home.next); else home.parent.appendChild(body); }
      veil.remove(); box.remove();
    }, 260);
    if (opts.onClose) opts.onClose();
  }
  if (opts.foot) { foot = h('div', { class: 'm-sfoot' }, opts.foot(close)); box.appendChild(foot); }
  const onKey = (e) => { if (e.key === 'Escape') { e.stopPropagation(); close(); } };
  document.addEventListener('keydown', onKey, true);
  window.addEventListener('hashchange', close);
  veil.addEventListener('click', close);

  // свайп вниз за «ручку» или заголовок (или за любое место, если содержимое прокручено к началу)
  let y0 = null, dy = 0;
  box.addEventListener('touchstart', (e) => {
    const t = e.target;
    const onHead = t.closest('.m-grab, .m-shead');
    if (!onHead && (bodyBox.scrollTop > 0 || t.closest('input, textarea, select, .m-noswipe'))) { y0 = null; return; }
    y0 = e.touches[0].clientY; dy = 0;
  }, { passive: true });
  box.addEventListener('touchmove', (e) => {
    if (y0 === null) return;
    dy = e.touches[0].clientY - y0;
    if (dy > 0) { box.style.transition = 'none'; box.style.transform = `translateY(${dy}px)`; }
  }, { passive: true });
  box.addEventListener('touchend', () => {
    if (y0 === null) return;
    box.style.transition = ''; box.style.transform = '';
    if (dy > 90) close();
    y0 = null;
  });

  document.body.append(veil, box);
  M_SHEETS++;
  document.documentElement.classList.add('m-sheet-open');
  requestAnimationFrame(() => requestAnimationFrame(() => { veil.classList.add('in'); box.classList.add('in'); }));
  close.box = box; close.body = bodyBox;
  return close;
}

function mActions(title, items) {
  let close = null;
  const list = h('div', { class: 'm-acts' }, items.map((it) => {
    if (!it) return h('div', { class: 'm-actsep' });
    return h('button', { type: 'button', class: 'm-act' + (it.bad ? ' bad' : '') + (it.on ? ' on' : ''), disabled: !!it.disabled,
      onclick: () => { close(); if (it.run) setTimeout(() => it.run(), 10); } },
      h('span', { class: 'i' }, it.icon || ''), h('span', { class: 't' }, it.label, it.hint ? h('small', {}, it.hint) : null));
  }));
  close = mSheet(title, list);
  return close;
}

function mSeg(items, cur, onPick) {
  const btns = {};
  const el = h('div', { class: 'm-seg', role: 'tablist' }, items.map(([id, label, badge]) => {
    const b = h('button', { type: 'button', role: 'tab', class: id === cur ? 'on' : '', onclick: () => { set(id); onPick(id); } }, label, badge || null);
    btns[id] = b; return b;
  }));
  function set(id) { cur = id; Object.entries(btns).forEach(([k, b]) => { b.classList.toggle('on', k === id); b.setAttribute('aria-selected', k === id ? 'true' : 'false'); }); }
  set(cur);
  return { el, set, get: () => cur };
}

function mFab(label, onClick, title) {
  return h('button', { type: 'button', class: 'm-fab', title: title || '', 'aria-label': title || label, onclick: onClick }, label);
}

// песочница на весь экран (sandbox.js) — при уходе со страницы флаг снимаем
window.addEventListener('hashchange', () => document.documentElement.classList.remove('sb-open'));

/* ---------- Telegram Mini App и скачивание файлов ----------
   Сайт открыт внутри Telegram (кнопка «Сайт» / «Открыть в Telegram»)? Telegram передаёт #tgWebAppData при запуске —
   запоминаем на время вкладки. Скачать файл из Mini App можно только через сам Telegram (событие
   web_app_request_file_download, Telegram 8.0+) или открыв ссылку во внешнем браузере (web_app_open_link). */
const FRX_IN_TG = (() => {
  try {
    if (/tgWebAppData=/.test(location.hash)) sessionStorage.setItem('frx_tg', '1');
    return sessionStorage.getItem('frx_tg') === '1' || !!window.TelegramWebviewProxy;
  } catch (e) { return !!window.TelegramWebviewProxy; }
})();

function tgPostEvent(type, data) {
  const body = JSON.stringify(data || {});
  try {
    if (window.TelegramWebviewProxy && window.TelegramWebviewProxy.postEvent) { window.TelegramWebviewProxy.postEvent(type, body); return true; }
    if (window.parent && window.parent !== window) { window.parent.postMessage(JSON.stringify({ eventType: type, eventData: data || {} }), '*'); return true; }
  } catch (e) { /* не в Telegram */ }
  return false;
}

function frxFmtSize(n) {
  if (!n && n !== 0) return '';
  const u = ['Б', 'КБ', 'МБ', 'ГБ']; let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i ? n.toFixed(n < 10 ? 1 : 0) : n) + ' ' + u[i];
}

function frxDownload(url, name, size) {
  if (FRX_IN_TG) {                                   // внутри Telegram обычная ссылка ничего не скачает
    mActions('Скачать «' + name + '»' + (size ? ' · ' + frxFmtSize(size) : ''), [
      { icon: '⬇', label: 'Скачать через Telegram', hint: 'файл придёт в загрузки Telegram (нужна свежая версия приложения)', run: () => tgPostEvent('web_app_request_file_download', { url, file_name: name }) },
      { icon: '↗', label: 'Открыть в браузере', hint: 'ссылка живёт 10 минут, вход не нужен', run: () => { if (!tgPostEvent('web_app_open_link', { url })) window.open(url, '_blank'); } },
    ]);
    return;
  }
  const a = h('a', { href: url, download: name, rel: 'noopener' });
  document.body.appendChild(a); a.click(); a.remove();
  toast('загрузка началась' + (size ? ` · ${frxFmtSize(size)}` : '') + ' — прогресс в загрузках браузера', 'ok');
}
