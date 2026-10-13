/* FRX — вход на сайт. Вход «закрыт», пока владелец не напишет .web в Telegram: тогда приходит 5-значный код и вход
 * открыт 10 минут для одного устройства. Второй способ — Telegram Mini App (подпись initData). Сервер — core/web/auth.py. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const msg = (text, err) => { const m = $('lg-msg'); m.textContent = text || ''; m.classList.toggle('err', !!err); };
  let shown = null, poll = null, left = 0, tick = null;
  const show = (id) => {
    ['lg-tg', 'lg-closed', 'lg-code'].forEach((x) => { $(x).hidden = x !== id; });
    if (id === 'lg-code' && shown !== 'lg-code') { $('lg-input').value = ''; setTimeout(() => $('lg-input').focus(), 30); }
    shown = id;
  };

  async function call(path, body) {
    const r = await fetch('/api/auth/' + path, {
      method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin',
      headers: { 'X-FRX': '1', 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body),
    });
    let data = {};
    try { data = await r.json(); } catch (e) { /* пусто */ }
    if (r.status === 503 && data.sleeping) throw new Error('юзербот перезапускается — подождите несколько секунд');
    if (!r.ok) { const e = new Error(data.error || ('ошибка ' + r.status)); e.status = r.status; throw e; }
    return data;
  }
  // ссылка «войти в один клик» из бота или переезд открытой страницы на новый адрес: #frx_t=<билет>&to=<раздел>
  const hashQ = new URLSearchParams(location.hash.slice(1));
  const ticket = hashQ.get('frx_t'), goTo = (hashQ.get('to') || '').replace(/^\/+/, '');
  // '/#/раздел' при открытой '/' — это только смена якоря, страница бы не перезагрузилась и осталась на «вход выполнен»
  let entering = false;
  const enter = () => {
    if (entering) return;
    entering = true; msg('вход выполнен');
    history.replaceState(null, '', '/' + (goTo ? '#/' + goTo : ''));
    location.reload();
  };

  function countdown() {
    clearInterval(tick);
    const draw = () => {
      if (left <= 0) { clearInterval(tick); $('lg-left').textContent = ''; return; }
      $('lg-left').textContent = `вход открыт ещё ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
      left -= 1;
    };
    draw(); tick = setInterval(draw, 1000);
  }

  async function refresh() {
    try {
      const st = await call('state');
      if (st.authed) { enter(); return; }
      if (st.open) { if (shown !== 'lg-code') msg(''); show('lg-code'); left = st.left; countdown(); }
      else { if (shown === 'lg-code') msg('время входа вышло — напишите .web ещё раз', true); show('lg-closed'); }
    } catch (e) { msg(e.message, true); if (!shown) show('lg-closed'); }
  }

  async function verify() {
    const v = $('lg-input').value.replace(/\D/g, '');
    if (v.length !== 5) { msg('введите 5 цифр', true); return; }
    $('lg-go').disabled = true;
    try { await call('verify', { code: v }); enter(); }
    catch (e) { msg(e.message, true); if (e.status === 403) show('lg-closed'); else $('lg-input').select(); }
    finally { $('lg-go').disabled = false; }
  }
  async function again() {
    try { const r = await call('request', {}); msg('новый код отправлен ' + r.sent_to); $('lg-input').value = ''; $('lg-input').focus(); }
    catch (e) { msg(e.message, true); if (e.status === 403) show('lg-closed'); }
  }

  $('lg-go').addEventListener('click', verify);
  $('lg-again').addEventListener('click', again);
  $('lg-input').addEventListener('input', (e) => {
    e.target.value = e.target.value.replace(/\D/g, '').slice(0, 5);
    if (e.target.value.length === 5) verify();
  });
  $('lg-input').addEventListener('keydown', (e) => { if (e.key === 'Enter') verify(); });

  // Telegram Mini App: данные запуска приходят во фрагменте адреса (#tgWebAppData=…)
  const init = new URLSearchParams(location.hash.slice(1)).get('tgWebAppData');
  if (ticket) {
    history.replaceState(null, '', location.pathname);            // билет одноразовый — в адресной строке ему не место
    $('lg-tg').querySelector('.lg-note').textContent = 'вход по ссылке…';
    show('lg-tg');
    call('ticket', { ticket }).then(enter).catch((e) => { msg(e.message, true); refresh(); });
  } else if (init) {
    try { sessionStorage.setItem('frx_tg', '1'); } catch (e) { /* без хранилища — сайт узнает Telegram по его мосту */ }
    show('lg-tg');
    call('telegram', { init_data: init }).then(enter).catch((e) => { msg(e.message, true); refresh(); });
  } else {
    refresh();
  }
  poll = setInterval(() => { if (shown !== 'lg-tg') refresh(); }, 3000);   // написали .web — страница откроется сама
})();
