/* FRX web — сторож загрузки. Идёт ПЕРВЫМ скриптом страницы. Если через 25 с сайт так и не запустился (app.js не дошёл или пришёл
 * обрезанным — так бывает на медленном публичном адресе: бесплатный localhost.run отдаёт несколько КБ/с и рвёт длинные ответы),
 * вместо пустого экрана показывает, что случилось, и кнопку «обновить»: уже скачанные файлы лежат в кэше, второй заход быстрее. */
(function () {
  'use strict';
  var failed = [];
  window.addEventListener('error', function (e) {
    var t = e && e.target;
    if (t && t.tagName === 'SCRIPT' && t.src) failed.push(t.src.split('/').pop().split('?')[0]);
  }, true);
  function show() {
    if (window.FRX_BOOTED) return;
    var off = document.getElementById('off');
    if (!off) return;
    var local = /^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
    off.textContent = '';
    var big = document.createElement('div'); big.className = 'big'; big.textContent = 'сайт не догрузился';
    var why = document.createElement('div'); why.className = 'muted off-why';
    why.textContent = local
      ? 'Скрипты сайта не загрузились' + (failed.length ? ' (' + failed.join(', ') + ')' : '') + '. Обновите страницу.'
      : 'Публичный адрес слишком медленный: бесплатный туннель (localhost.run) отдаёт несколько КБ/с и обрывает загрузку. '
        + 'Обновите страницу — уже скачанное берётся из кэша, обычно со второго раза сайт открывается. '
        + 'Надёжнее — свой туннель: .web tunnel setup в Telegram.';
    var act = document.createElement('div'); act.className = 'off-act';
    var b = document.createElement('button'); b.className = 'btn small primary'; b.type = 'button'; b.textContent = 'обновить страницу';
    b.onclick = function () { location.reload(); };
    act.appendChild(b);
    off.appendChild(big); off.appendChild(why); off.appendChild(act);
    off.classList.add('show');
  }
  setTimeout(show, 25000);
  window.addEventListener('load', function () { setTimeout(show, 1500); });   // всё «загрузилось», а сайт не стартовал — обрезанный скрипт
})();
