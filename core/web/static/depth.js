/* FRX web — режим «FX» (переключатель «fx» в шапке; до 1.0 назывался «3d»). Стили — depth.css, всё под html.d3.
 *  - выбор помнится в браузере; по умолчанию включён только на компьютере;
 *  - наклон панелей за курсором (CSS-переменные --d3rx / --d3ry на <html>, плавно) и блик под курсором (--gx / --gy);
 *  - страницам-спискам ставится .d3-ok (рабочие поверхности WebIDE / Lab / BotChat / Emulate / Sandbox не наклоняются);
 *  - над заголовком страницы — счётчик «03 / 08» по положению вкладки в меню;
 *  - смена страницы: старая уходит вглубь влево, новая прилетает справа из глубины (вместо глитча, см. fx.js);
 *  - кнопки при нажатии проваливаются вглубь, а из щели вокруг пробивается свет;
 *  - FRX на главной горит, как свеча: неровное живое свечение;
 *  - при первом заходе за сессию — заставка: огненная черта раскрывается в FRX, который встаёт на своё место на главной.
 * Всё выключается при prefers-reduced-motion. */
(function () {
  'use strict';
  const KEY = 'frx-fx', OLD_KEY = 'frx-3d';
  const root = document.documentElement;
  const RM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const WORK = '.wi-ta, .lab-layout, .bc, .emu, .sb';
  // по умолчанию 3D только на компьютере (мышь, широкий экран); на телефоне — выключен, пока его не включат руками
  let on = window.matchMedia('(min-width: 761px) and (hover: hover) and (pointer: fine)').matches;
  try {
    let saved = localStorage.getItem(KEY);
    if (saved === null) saved = localStorage.getItem(OLD_KEY);           // выбор из версий, где режим звался «3d»
    if (saved !== null) on = saved !== '0';
  } catch (e) { /* хранилище недоступно — режим по умолчанию */ }
  root.classList.toggle('d3', on);

  /* ---------------- наклон за курсором ---------------- */
  let tx = 0, ty = 0, cx = 0, cy = 0, raf = 0;
  const MAX_Y = 3.2, MAX_X = 2.2;           // градусы — заметно, но читать и нажимать удобно
  function tick() {
    raf = 0;
    cx += (tx - cx) * .07; cy += (ty - cy) * .07;
    root.style.setProperty('--d3ry', (cx * MAX_Y).toFixed(3) + 'deg');
    root.style.setProperty('--d3rx', (-cy * MAX_X).toFixed(3) + 'deg');
    if (Math.abs(tx - cx) + Math.abs(ty - cy) > .0015) raf = requestAnimationFrame(tick);
  }
  window.addEventListener('pointermove', (e) => {
    if (!on || RM.matches || e.pointerType === 'touch' || window.innerWidth < 760) return;
    tx = (e.clientX / window.innerWidth - .5) * 2;
    ty = (e.clientY / window.innerHeight - .5) * 2;
    if (!raf) raf = requestAnimationFrame(tick);
  }, { passive: true });
  document.addEventListener('pointerleave', () => { tx = 0; ty = 0; if (!raf) raf = requestAnimationFrame(tick); });

  /* ---------------- разметка страницы ---------------- */
  // высокая панель при том же угле наклона «перекашивается» сильнее — угол уменьшается с высотой
  // и с шириной: у широкой панели край при том же угле уходит дальше. Огромные (больше ~40% экрана) — не наклоняем
  // вовсе: на них перспектива «плывёт» перед глазами (вкладка neuro и т. п.)
  const fit = (g) => {
    const w = Math.max(1, g.offsetWidth), hh = Math.max(1, g.offsetHeight);
    const big = w * hh > window.innerWidth * window.innerHeight * .4 || hh > window.innerHeight * .75;
    const k = big ? 0 : Math.min(1, 420 / hh) * Math.min(1, 720 / w);
    g.style.setProperty('--d3s', (big ? 0 : Math.max(.12, k)).toFixed(3));
  };
  const ro = window.ResizeObserver ? new ResizeObserver((es) => es.forEach((e) => fit(e.target))) : null;
  function decorate() {
    const shell = document.querySelector('#app > .shell:not(.leaving)');
    if (!shell) return;
    shell.classList.toggle('d3-ok', !shell.querySelector(WORK));
    shell.querySelectorAll('.glass').forEach((g, i) => {
      g.style.setProperty('--d3k', i % 7);
      fit(g);
      if (ro && !g._d3ro) { g._d3ro = true; ro.observe(g); }
    });
    const h1 = shell.querySelector('.top h1');
    if (!h1 || h1.querySelector('.d3-idx')) return;
    if (shell.querySelector('.top .back')) return;                 // вложенная страница — без счётчика
    const tabs = [...document.querySelectorAll('#nav .tab')];
    const i = tabs.findIndex((t) => t.classList.contains('on'));
    if (i < 0) return;
    const pad = (n) => String(n).padStart(2, '0');
    h1.appendChild(Object.assign(document.createElement('span'), { className: 'd3-idx', textContent: `${pad(i + 1)} / ${pad(tabs.length)}` }));
  }
  function watch() {
    const app = document.getElementById('app');
    if (!app) return;
    let t = 0;
    new MutationObserver(() => { clearTimeout(t); t = setTimeout(decorate, 30); }).observe(app, { childList: true });
    const nav = document.getElementById('nav');
    if (nav) new MutationObserver(() => { clearTimeout(t); t = setTimeout(decorate, 30); }).observe(nav, { subtree: true, attributes: true, attributeFilter: ['class'] });
    decorate();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();

  /* ---------------- переходы между страницами (вызывает fx.js, если режим включён) ---------------- */
  function flyIn(el, o = {}) {
    if (!el || RM.matches) return null;
    return el.animate([
      { opacity: 0, transform: 'perspective(1600px) translate3d(90px, 0, -380px) rotateY(-26deg)', filter: 'blur(6px)' },
      { opacity: 1, transform: 'perspective(1600px) translate3d(0, 0, 0) rotateY(0deg)', filter: 'blur(0px)' },
    ], { duration: o.duration || 700, delay: o.delay || 0, easing: 'cubic-bezier(.16, .9, .2, 1)', fill: 'backwards' });
  }
  function flyOut(el, o = {}) {
    if (!el || RM.matches) return null;
    return el.animate([
      { opacity: 1, transform: 'perspective(1600px) translate3d(0, 0, 0) rotateY(0deg)', filter: 'blur(0px)' },
      { opacity: 0, transform: 'perspective(1600px) translate3d(-120px, 0, -420px) rotateY(24deg)', filter: 'blur(6px)' },
    ], { duration: o.duration || 260, easing: 'cubic-bezier(.5, 0, .8, .3)', fill: 'forwards' });
  }
  // свет, проходящий по экрану слева направо при смене страницы
  let beam = null;
  function sweep() {
    if (RM.matches) return;
    if (!beam) {
      beam = document.createElement('div');
      beam.style.cssText = 'position:fixed;top:0;bottom:0;width:38vw;left:0;z-index:9000;pointer-events:none;opacity:0;'
        + 'background:linear-gradient(100deg,transparent 30%,rgba(255,120,40,.10) 46%,rgba(255,255,255,.10) 50%,rgba(255,90,30,.08) 54%,transparent 70%);';
      document.body.appendChild(beam);
    }
    beam.getAnimations().forEach((a) => a.cancel());
    beam.animate([{ transform: 'translateX(-45vw) skewX(-12deg)', opacity: 1 }, { transform: 'translateX(115vw) skewX(-12deg)', opacity: 1 }],
      { duration: 620, easing: 'cubic-bezier(.5, 0, .3, 1)' });
  }

  /* ================================================================== кнопки: «проваливаются» со светом из щели */
  const PRESS = '.btn, .chip, .lab-fmt-b, .wi-tg-btn';
  let pressed = null;
  function release() {
    const b = pressed; pressed = null;
    if (!b) return;
    b.classList.remove('fx-down');
    b.classList.remove('fx-up'); void b.offsetWidth; b.classList.add('fx-up');
    setTimeout(() => b.classList.remove('fx-up'), 700);
  }
  document.addEventListener('pointerdown', (e) => {
    if (!on || RM.matches || e.button > 0) return;
    const b = e.target.closest ? e.target.closest(PRESS) : null;
    if (!b || b.disabled) return;
    const r = b.getBoundingClientRect();
    b.style.setProperty('--px', ((e.clientX - r.left) / Math.max(1, r.width) * 100).toFixed(1) + '%');
    b.style.setProperty('--py', ((e.clientY - r.top) / Math.max(1, r.height) * 100).toFixed(1) + '%');
    pressed = b; b.classList.remove('fx-up'); b.classList.add('fx-down');
  }, true);
  ['pointerup', 'pointercancel', 'dragstart'].forEach((ev) => document.addEventListener(ev, release, true));
  window.addEventListener('blur', release);

  /* ================================================================== блик под курсором на панелях */
  let glareEl = null;
  document.addEventListener('pointermove', (e) => {
    if (!on || e.pointerType === 'touch') return;
    const g = e.target.closest ? e.target.closest('.d3-ok .glass') : null;
    if (glareEl && glareEl !== g) { glareEl.classList.remove('fx-glare'); glareEl = null; }
    if (!g) return;
    const r = g.getBoundingClientRect();
    g.style.setProperty('--gx', (e.clientX - r.left).toFixed(0) + 'px');
    g.style.setProperty('--gy', (e.clientY - r.top).toFixed(0) + 'px');
    if (glareEl !== g) { g.classList.add('fx-glare'); glareEl = g; }
  }, { passive: true });

  /* ================================================================== FRX-свеча на главной */
  // Неровный живой свет: несколько медленных колебаний + редкие «порывы» (свет на миг проседает и вспыхивает).
  const Candle = (() => {
    let raf = 0, last = 0, gust = 0, gustV = 0, lvl = 1;
    function frame(now) {
      raf = 0;
      const home = on && !RM.matches ? document.querySelector('#app .home') : null;
      if (!home) return;
      if (now - last > 33) {
        const t = now / 1000;
        if (Math.random() < 0.012) gustV = -(0.18 + Math.random() * 0.22);   // порыв
        gust += gustV; gustV *= 0.6; gust *= 0.9;
        const f = 0.86 + 0.07 * Math.sin(t * 1.3) + 0.045 * Math.sin(t * 2.9 + 1.1) + 0.03 * Math.sin(t * 6.7 + 2.3)
          + 0.02 * (Math.random() - 0.5) + gust;
        lvl += (f - lvl) * 0.35;
        home.style.setProperty('--flame', Math.max(0.45, Math.min(1.2, lvl)).toFixed(3));
        home.style.setProperty('--flame-x', (Math.sin(t * 0.9) * 6 + Math.sin(t * 2.3) * 2).toFixed(1) + 'px');
        last = now;
      }
      raf = requestAnimationFrame(frame);
    }
    function kick() { if (!raf) raf = requestAnimationFrame(frame); }
    return { kick };
  })();

  /* ================================================================== заставка при входе */
  // Один раз за сессию, в режиме FX, если открыта главная: в темноте вспыхивает огненная черта, раскрывается в FRX,
  // вспышка и волна света — и слово «встаёт» ровно на своё место на главной, а остальное поднимается из глубины.
  // Клик или любая клавиша — пропустить.
  function intro() {
    let seen = false;
    try { seen = sessionStorage.getItem('frx-fx-intro') === '1'; sessionStorage.setItem('frx-fx-intro', '1'); } catch (e) { /* без хранилища — показываем */ }
    if (seen || !on || RM.matches) return;
    const path = location.hash.replace(/^#\/?/, '');
    if (path && path !== 'home') return;
    const ov = document.createElement('div');
    ov.className = 'fx-intro';
    ov.innerHTML = '<div class="fx-intro-line"></div><div class="fx-intro-flare"></div><div class="fx-intro-ring"></div><div class="fx-intro-word"><span>FRX</span></div>';
    document.body.appendChild(ov);
    root.classList.add('fx-intro-on');
    let done = false;
    const finish = (skip) => {
      if (done) return; done = true;
      const word = ov.querySelector('.fx-intro-word');
      const target = document.querySelector('#app .home .word');
      if (!skip && target) {                              // FLIP: слово летит туда, где стоит настоящее
        const a = word.getBoundingClientRect(), b = target.getBoundingClientRect();
        const s = b.height / Math.max(1, a.height);
        word.animate([{ transform: 'none' }, { transform: `translate(${(b.left + b.width / 2) - (a.left + a.width / 2)}px, ${(b.top + b.height / 2) - (a.top + a.height / 2)}px) scale(${s})` }],
          { duration: 720, easing: 'cubic-bezier(.7, 0, .2, 1)', fill: 'forwards' });
      }
      ov.classList.add('out');
      setTimeout(() => { root.classList.remove('fx-intro-on'); root.classList.add('fx-intro-reveal'); }, skip ? 0 : 700);
      setTimeout(() => { ov.remove(); }, skip ? 320 : 760);
      setTimeout(() => root.classList.remove('fx-intro-reveal'), 2600);
      window.removeEventListener('keydown', skipper, true); ov.removeEventListener('pointerdown', skipper);
    };
    const skipper = () => finish(true);
    window.addEventListener('keydown', skipper, true); ov.addEventListener('pointerdown', skipper);
    setTimeout(() => finish(false), 1500);
  }

  function syncAll() { Candle.kick(); }

  function set(v) {
    on = !!v;
    root.classList.toggle('d3', on);
    try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* не страшно */ }
    if (!on) { root.style.removeProperty('--d3rx'); root.style.removeProperty('--d3ry'); tx = ty = cx = cy = 0; }
    document.querySelectorAll('.d3-switch').forEach((b) => b.classList.toggle('on', on));
    decorate();
    syncAll();
    if (window.FX && FX.refresh) FX.refresh();
  }
  function button() {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'link d3-switch' + (on ? ' on' : '');
    b.title = 'FX — эффекты оформления: глубина, свет, анимации (включить / выключить)';
    b.textContent = 'fx';
    b.addEventListener('click', () => set(!on));
    return b;
  }

  // главная перерисовалась — свеча снова горит; первый заход — заставка
  (function boot() {
    const go = () => {
      syncAll();
      const app = document.getElementById('app');
      if (app) new MutationObserver(() => Candle.kick()).observe(app, { childList: true });
      intro();
    };
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', go); else go();
  })();
  RM.addEventListener?.('change', syncAll);

  window.D3 = { get on() { return on; }, set, button, flyIn, flyOut, sweep, refresh: decorate };
})();
