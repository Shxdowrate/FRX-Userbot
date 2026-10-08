/* FRX web — режим «3D» (тест). Стили — depth.css, всё под html.d3.
 *  - переключатель «3d» в шапке (app.js, renderNav → D3.button()), выбор помнится в браузере;
 *  - наклон панелей за курсором (CSS-переменные --d3rx / --d3ry на <html>, плавно);
 *  - страницам-спискам ставится .d3-ok (рабочие поверхности WebIDE / Lab / BotChat / Emulate / Sandbox не наклоняются);
 *  - над заголовком страницы — счётчик «03 / 08» по положению вкладки в меню;
 *  - смена страницы в 3D: старая уходит вглубь влево, новая прилетает справа из глубины (вместо глитча, см. FX). */
(function () {
  'use strict';
  const KEY = 'frx-3d';
  const root = document.documentElement;
  const RM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const WORK = '.wi-ta, .lab-layout, .bc, .emu, .sb';
  // по умолчанию 3D только на компьютере (мышь, широкий экран); на телефоне — выключен, пока его не включат руками
  let on = window.matchMedia('(min-width: 761px) and (hover: hover) and (pointer: fine)').matches;
  try {
    const saved = localStorage.getItem(KEY);
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
  const fit = (g) => g.style.setProperty('--d3s', Math.max(.12, Math.min(1, 420 / Math.max(1, g.offsetHeight))).toFixed(3));
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

  function set(v) {
    on = !!v;
    root.classList.toggle('d3', on);
    try { localStorage.setItem(KEY, on ? '1' : '0'); } catch (e) { /* не страшно */ }
    if (!on) { root.style.removeProperty('--d3rx'); root.style.removeProperty('--d3ry'); tx = ty = cx = cy = 0; }
    document.querySelectorAll('.d3-switch').forEach((b) => b.classList.toggle('on', on));
    decorate();
    if (window.FX && FX.refresh) FX.refresh();
  }
  function button() {
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'link d3-switch' + (on ? ' on' : '');
    b.title = '3D-оформление (тест): включить / выключить';
    b.textContent = '3d';
    b.addEventListener('click', () => set(!on));
    return b;
  }

  window.D3 = { get on() { return on; }, set, button, flyIn, flyOut, sweep, refresh: decorate };
})();
