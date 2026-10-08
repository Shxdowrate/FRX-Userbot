/* FRX web — эффекты сайта.
 *
 * 1) Глитч появления.  FX.glitchIn(el) / FX.glitchOut(el) — красно-оранжевый «сбой сигнала»: срезы по высоте, рывки, расслоение
 *    цвета. CSS-анимации появления (rise, fade, popin, pop, …) переопределены в accent.css тем же глитчем; плавными остаются наведения,
 *    переключатели, раскрытия по высоте, выпадающие меню и прокрутка.
 * 2) Прокрутка-«стопка».  Блоки страницы при прокрутке не уезжают вверх, а останавливаются под шапкой; следующий блок наезжает
 *    поверх, а накрытый темнеет, уменьшается, размывается и исчезает. Блоки определяются сами: дети страницы, а у слишком высоких
 *    контейнеров (длинные списки) — их строки. Работает на страницах-списках; рабочие поверхности (WebIDE-редактор, Lab, BotChat,
 *    Emulate, Sandbox) прокручиваются как раньше. Раскладка документа не меняется: эффект — только transform/opacity/filter.
 * 3) Плавная прокрутка колесом.  Колесо мыши над страницей двигает её с инерцией (над внутренними прокручиваемыми блоками, в окнах
 *    и полях ввода — обычная прокрутка). Клавиатура, полоса и тач — без изменений.
 * Всё выключается при prefers-reduced-motion и на узких экранах (стопка). */
(function () {
  'use strict';
  const RM = window.matchMedia('(prefers-reduced-motion: reduce)');
  const clamp = (x, a = 0, b = 1) => Math.min(b, Math.max(a, x));

  /* ------------------------------------------------------------------ глитч */
  const RED = '#ff2a1c', ORG = '#ff8a00';
  const sh = (a, b) => `drop-shadow(${-a}px 0 0 ${RED}) drop-shadow(${b}px 0 0 ${ORG})`;
  function glitchFrames(dir) {
    // dir = 1 — появление, -1 — исчезновение; срезы clip-path «прыгают» шагами, конец — плавный
    const f = [
      { offset: 0, opacity: 0, clipPath: 'inset(44% 0 50% 0)', transform: 'translateX(-16px)', filter: sh(5, 5), easing: 'steps(1, end)' },
      { offset: .1, opacity: 1, clipPath: 'inset(6% 0 72% 0)', transform: 'translateX(12px)', filter: sh(4, 3), easing: 'steps(1, end)' },
      { offset: .2, opacity: 1, clipPath: 'inset(64% 0 8% 0)', transform: 'translateX(-8px) skewX(-6deg)', filter: sh(3, 4), easing: 'steps(1, end)' },
      { offset: .3, opacity: .85, clipPath: 'inset(22% 0 38% 0)', transform: 'translateX(6px)', filter: sh(4, 2), easing: 'steps(1, end)' },
      { offset: .42, opacity: 1, clipPath: 'inset(0 0 0 0)', transform: 'translateX(-3px)', filter: sh(2, 2), easing: 'steps(1, end)' },
      { offset: .52, opacity: 1, clipPath: 'inset(50% 0 41% 0)', transform: 'translateX(5px)', filter: sh(3, 1), easing: 'steps(1, end)' },
      { offset: .6, opacity: 1, clipPath: 'inset(0 0 0 0)', transform: 'translateX(0)', filter: sh(1.5, 1.5), easing: 'cubic-bezier(.2, .9, .2, 1)' },
      { offset: 1, opacity: 1, clipPath: 'inset(0 0 0 0)', transform: 'none', filter: 'none' },
    ];
    if (dir > 0) return f;
    return f.slice().reverse().map((k, i, a) => ({ ...k, offset: 1 - k.offset, easing: i < a.length - 2 ? 'steps(1, end)' : 'linear' }));
  }
  const D3ON = () => window.D3 && window.D3.on;                    // режим «3D» (depth.js): свои переходы — из глубины
  function glitchIn(el, o = {}) {
    if (D3ON()) return window.D3.flyIn(el, o);
    if (!el || RM.matches) return null;
    const a = el.animate(glitchFrames(1), { duration: o.duration || 460, delay: o.delay || 0, fill: 'backwards' });
    return a;
  }
  function glitchOut(el, o = {}) {
    if (D3ON()) return window.D3.flyOut(el, o);
    if (!el || RM.matches) return null;
    return el.animate(glitchFrames(-1), { duration: o.duration || 220, fill: 'forwards' });
  }
  // красная полоса-«разрез», пробегающая по экрану при смене страницы
  let scan = null;
  function sweep() {
    if (D3ON()) { window.D3.sweep(); return; }
    if (RM.matches) return;
    if (!scan) { scan = document.createElement('div'); scan.className = 'fx-scan'; document.body.appendChild(scan); }
    scan.getAnimations().forEach((a) => a.cancel());
    scan.animate([
      { transform: 'translateY(-10vh) scaleY(1)', opacity: 1 },
      { transform: 'translateY(55vh) scaleY(2.2)', opacity: 1, offset: .55 },
      { transform: 'translateY(110vh) scaleY(1)', opacity: 0 },
    ], { duration: 380, easing: 'cubic-bezier(.6, 0, .2, 1)' });
  }

  /* ------------------------------------------------------------------ стопка */
  const SKIP = '.wi-ta, .lab-layout, .bc, .emu, .sb, .hd, .veil';     // рабочие поверхности и главная — без стопки
  let units = [], free = [], shell = null, dirty = true, raf = 0;
  const navH = () => (document.getElementById('nav')?.getBoundingClientRect().height || 60);

  function horizontal(kids) {                       // дети стоят в ряд (сетка) — не дробим
    for (let i = 1; i < kids.length; i++) {
      const a = kids[i - 1].getBoundingClientRect(), b = kids[i].getBoundingClientRect();
      if (b.top < a.bottom - 2 && b.left >= a.right - 2) return true;
    }
    return false;
  }
  function collect(el, depth, out, vh) {
    const r = el.getBoundingClientRect();
    if (r.height < 8 || getComputedStyle(el).position === 'absolute' || getComputedStyle(el).position === 'fixed') return;
    const kids = [...el.children].filter((c) => c.getBoundingClientRect().height >= 8);
    const wrapper = !el.classList.contains('glass') && !el.classList.contains('row') && !solid(el) && el.querySelector('.glass');   // обёртка без фона — смотрим внутрь
    if (depth < 4 && (r.height > vh * .9 || wrapper) && kids.length >= 1 && !horizontal(kids)) {
      kids.forEach((c) => collect(c, depth + 1, out, vh));
      return;
    }
    out.push(el);
  }
  function reset(el) {
    el.style.transform = ''; el.style.opacity = ''; el.style.filter = ''; el.style.visibility = '';
    el.classList.remove('fx-unit', 'fx-row', 'fx-pin', 'fx-cover');
    delete el._fxY;
  }
  function solid(el) {
    if (el.classList.contains('fx-row') || el.classList.contains('glass')) return true;
    const m = getComputedStyle(el).backgroundColor.match(/rgba?\(([^)]+)\)/);
    if (!m) return false;
    const p = m[1].split(',').map(Number);
    return (p.length < 4 ? 1 : p[3]) >= .5;
  }
  function rebuild() {
    dirty = false;
    units.forEach(reset); free.forEach(reset); units = []; free = [];
    shell = document.querySelector('#app > .shell:not(.leaving)');
    document.documentElement.classList.remove('fx-stack');
    if (!shell || RM.matches || window.innerWidth < 760 || shell.querySelector(SKIP)) return;
    const vh = window.innerHeight, out = [];
    [...shell.children].forEach((c) => collect(c, 0, out, vh));
    if (out.length < 3 || document.documentElement.scrollHeight < vh * 1.25) return;   // прокручивать почти нечего
    // в «стопку» идут только непрозрачные блоки (панели и строки панелей); текст без фона не накрывает — он гаснет у шапки
    out.forEach((u) => { if (u.classList.contains('row') || (!u.classList.contains('glass') && u.parentElement?.closest('.glass'))) u.classList.add('fx-row'); });
    units = out.filter(solid);
    free = out.filter((u) => !units.includes(u));
    if (units.length < 3) { out.forEach(reset); units = []; free = []; return; }
    document.documentElement.classList.add('fx-stack');
    units.forEach((u) => { u.classList.add('fx-unit'); u._fxY = 0; });
    frame();
  }
  function frame() {
    raf = 0;
    if (dirty) { rebuild(); return; }
    if (!units.length) return;
    const vh = window.innerHeight, top = navH() + 14;
    // положение по раскладке (offsetTop не зависит от transform — наши сдвиги, масштаб и наклон его не искажают)
    const sy = window.scrollY;
    const base = units.map((u) => ({ t: docTop(u) - sy, h: u.offsetHeight }));
    for (let i = 0; i < units.length; i++) {
      const u = units[i], b = base[i];
      const anchor = Math.min(top, vh - b.h - 12);               // высокий блок «останавливается», когда его низ дошёл до низа экрана
      const pin = Math.max(0, anchor - b.t);
      let c = 0;
      if (pin > 0 && i < units.length - 1) {
        const nt = base[i + 1].t;                                 // верх следующего (он ещё не остановлен — едет снизу)
        const span = Math.max(40, Math.min(b.h, vh - top));
        c = clamp((anchor + Math.min(b.h, vh - top) - nt) / span);
      }
      if (pin <= 0 || i === units.length - 1) {
        if (u._fxY) { u.style.transform = ''; u.style.opacity = ''; u.style.filter = ''; u.style.visibility = ''; u._fxY = 0; u.classList.remove('fx-pin'); }
        continue;
      }
      u._fxY = pin;
      u.classList.add('fx-pin');
      const s = 1 - .1 * c, o = 1 - Math.pow(c, 1.4);
      u.style.transform = `perspective(1400px) translate3d(0, ${pin - 34 * c}px, ${-120 * c}px) rotateX(${(9 * c).toFixed(2)}deg) scale(${s})`;
      u.style.opacity = o.toFixed(3);
      u.style.filter = c > .01 ? `blur(${(c * 8).toFixed(2)}px) brightness(${(1 - .6 * c).toFixed(3)}) saturate(${(1 + .6 * c).toFixed(2)})` : '';
      u.style.visibility = c >= .999 ? 'hidden' : '';
    }
    // блоки без фона (текст, заголовки) просто гаснут, подходя к закреплённой стопке
    // (гаснут только под закреплённым блоком — заголовок страницы в покое всегда чёткий)
    const pinned = units.map((u, i) => (u._fxY ? { y: docTop(u), bottom: Math.min(top, vh - base[i].h - 12) + Math.min(base[i].h, vh - top) } : null)).filter(Boolean);
    for (const f of free) {
      const fy = docTop(f), t = fy - sy;
      const z = pinned.filter((p) => p.y < fy).pop();
      const o = z ? clamp((t - z.bottom + 60) / 60) : 1;
      f.style.opacity = o < 1 ? o.toFixed(3) : '';
      f.style.filter = o < 1 ? `blur(${((1 - o) * 4).toFixed(2)}px)` : '';
    }
    // кромка у блока, который сейчас наезжает
    for (let i = 1; i < units.length; i++) units[i].classList.toggle('fx-cover', !!units[i - 1]._fxY && base[i].t < Math.min(top, vh - base[i - 1].h - 12) + Math.min(base[i - 1].h, vh - top) - 2);
  }
  function docTop(el) { let y = 0; for (let n = el; n; n = n.offsetParent) y += n.offsetTop; return y; }
  const kick = () => { if (!raf) raf = requestAnimationFrame(frame); };
  const mark = () => { dirty = true; kick(); };

  /* ------------------------------------------------------------------ плавное колесо */
  let target = window.scrollY, cur = window.scrollY, wheeling = false, ownScroll = false;
  function scrollable(el, dy) {
    for (let n = el; n && n !== document.body && n !== document.documentElement; n = n.parentElement) {
      if (n.matches?.('textarea, select, .veil, .wi-pop, .bc-pick, .lab-picker, [contenteditable="true"]')) return true;
      const st = getComputedStyle(n);
      if (/(auto|scroll)/.test(st.overflowY) && n.scrollHeight > n.clientHeight + 1) {
        if (dy > 0 ? n.scrollTop + n.clientHeight < n.scrollHeight - 1 : n.scrollTop > 0) return true;
      }
    }
    return false;
  }
  function wheel(e) {
    if (RM.matches || e.ctrlKey || e.defaultPrevented || Math.abs(e.deltaX) > Math.abs(e.deltaY)) return;
    if (document.querySelector('.veil')) return;                  // открыто окно — обычная прокрутка
    let dy = e.deltaY * (e.deltaMode === 1 ? 40 : e.deltaMode === 2 ? window.innerHeight : 1);
    if (scrollable(e.target, dy)) return;
    const max = document.documentElement.scrollHeight - window.innerHeight;
    if (max <= 0) return;
    e.preventDefault();
    if (!wheeling) { target = cur = window.scrollY; }
    target = clamp(target + dy, 0, max);
    if (!wheeling) { wheeling = true; requestAnimationFrame(step); }
  }
  function step() {
    cur += (target - cur) * .13;
    if (Math.abs(target - cur) < .4) cur = target;
    ownScroll = true; window.scrollTo(0, cur);
    if (cur !== target) requestAnimationFrame(step); else wheeling = false;
  }
  window.addEventListener('wheel', wheel, { passive: false });
  window.addEventListener('scroll', () => {
    if (!ownScroll && wheeling) { wheeling = false; }              // клавиатура/полоса перехватили — отдаём им
    ownScroll = false;
    kick();
  }, { passive: true });
  window.addEventListener('resize', mark);

  /* ------------------------------------------------------------------ наблюдение за страницей */
  function watch() {
    const app = document.getElementById('app');
    if (!app) return;
    let t = 0;
    new MutationObserver((list) => {
      if (list.every((m) => m.target.closest?.('.fx-unit') && m.type === 'attributes')) return;
      clearTimeout(t); t = setTimeout(mark, 140);
    }).observe(app, { childList: true, subtree: true });
    mark();
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watch); else watch();

  window.FX = { glitchIn, glitchOut, sweep, refresh: mark };
})();
