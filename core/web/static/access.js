/* FRX — вкладка «доступ» (#/access): кто ещё может пользоваться юзерботом, классы, выдачи;
 * и блок «алиасы команд» внизу вкладки «конфиги» (acAliasesBlock, вызывается из configList в app.js).
 * Сервер — core/web/access_api.py, логика — core/access.py. Загружается до app.js (там роутер и меню). */

function acEnc(s) { return encodeURIComponent(s); }

const AC_KIND = { scope: 'набор модулей', module: 'модуль', command: 'команда' };

/* Метка «критически опасно» с причинами во всплывающей подсказке. */
function acDangerTag(danger, short) {
  if (!danger || !danger.length) return null;
  return h('span', { class: 'tag ac-danger', title: danger.join('\n') }, short ? '⚠' : '⚠ критически опасно');
}

function acWho(w) {
  return [w.username ? '@' + w.username : null, 'id ' + w.id].filter(Boolean).join(' · ');
}

/* Модальное окно пошире, с прокручиваемым телом. body — узел, который можно перерисовывать. */
function acModal(title, body, actions) {
  const close = modal(title, body, actions || ((cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'закрыть')]));
  const g = document.body.lastElementChild && document.body.lastElementChild.querySelector('.glass');
  if (g) g.classList.add('ac-wide');
  return close;
}

/* Список выдач (класса или пользователя) с кнопкой «отозвать» у каждой. */
function acGrantRows(grants, onRevoke, emptyText) {
  if (!grants.length) return [empty(emptyText)];
  return grants.map((g) => row(
    nameSub(h('span', {}, g.label, h('span', { class: 'tag' }, AC_KIND[g.kind]), acDangerTag(g.danger)), g.sub + (g.danger.length ? ' · ' + g.danger.join('; ') : '')),
    onRevoke ? confirmBtn('отозвать', () => onRevoke(g)) : null));
}

/* ------------------------------------------------------------------ окно «+ выдать доступ» ------------------------------------------------------------------
 * opts: { title, d (state), has: (kind, value) => bool, onPick: async (kind, value) => void, scopes: bool }
 * Слева направо: найти → модуль (целиком) или его команда → для опасного — подтверждение. Окно не закрывается после
 * выдачи: можно выдать несколько вещей подряд. */
function acGrantPicker(opts) {
  const d = opts.d, pfx = d.prefix;
  const st = { q: '', open: null, confirm: null };
  const box = h('div', { class: 'ac-pick' });
  const search = h('input', { class: 'field', placeholder: 'найти модуль или команду', spellcheck: 'false', autocomplete: 'off' });
  search.addEventListener('input', () => { st.q = search.value.trim().toLowerCase(); st.open = null; draw(); });

  const pick = (kind, value, label, danger) => {
    if (danger && danger.length && !(st.confirm && st.confirm.value === value)) {
      st.confirm = { kind, value, label, danger }; draw(); return;
    }
    act(async () => {
      await opts.onPick(kind, value);
      st.confirm = null;
      toast('выдано: ' + label, 'ok');
      draw();
    });
  };

  const addBtn = (kind, value, label, danger, text) => opts.has(kind, value)
    ? h('span', { class: 'val ac-has' }, 'выдано ✓')
    : h('button', { class: 'btn small' + (danger && danger.length ? ' danger' : ''), type: 'button', onclick: (e) => { e.stopPropagation(); pick(kind, value, label, danger); } }, text || '+ выдать');

  function confirmView() {
    const c = st.confirm;
    return [
      h('div', { class: 'ac-confirm' },
        h('div', { class: 'name' }, '⚠ критически опасный доступ'),
        h('div', { class: 'ac-confirm-what' }, AC_KIND[c.kind] + ': ', h('b', {}, c.label)),
        h('ul', {}, c.danger.map((x) => h('li', {}, x))),
        h('div', { class: 'faint' }, 'Это по сути доступ ко всему аккаунту и к компьютеру, где запущен юзербот. Выдавайте только тем, кому доверяете как себе.'),
        h('div', { class: 'inline', style: 'margin-top:14px' },
          h('button', { class: 'btn danger', type: 'button', onclick: () => pick(c.kind, c.value, c.label, c.danger) }, 'да, выдать'),
          h('button', { class: 'btn', type: 'button', onclick: () => { st.confirm = null; draw(); } }, 'отмена'))),
    ];
  }

  function draw() {
    if (st.confirm) { box.replaceChildren(...confirmView()); return; }
    const q = st.q;
    const parts = [];
    if (opts.scopes && !q) {
      parts.push(h('div', { class: 'h2' }, 'наборы модулей'));
      parts.push(...d.scopes.map((s) => row(nameSub(h('span', {}, s.label, acDangerTag(s.danger, true)), s.sub), addBtn('scope', s.value, s.label, s.danger))));
      parts.push(h('div', { class: 'h2' }, 'модули'));
    }
    const mods = d.modules.filter((m) => !q || m.module.toLowerCase().includes(q) || m.name.toLowerCase().includes(q)
      || m.commands.some((c) => c.command.includes(q) || c.aliases.some((a) => a.includes(q))));
    if (!mods.length) parts.push(empty('ничего не найдено'));
    for (const m of mods) {
      const open = st.open === m.module || (q && !m.name.toLowerCase().includes(q) && !m.module.toLowerCase().includes(q));
      const n = m.commands.length;
      const head = row(
        nameSub(h('span', {}, (open ? '▾ ' : '▸ ') + m.name, h('span', { class: 'tag' }, m.builtin ? 'встроенный' : 'загруженный'), acDangerTag(m.danger)),
          [m.description, `${n} ${plural(n, 'команда', 'команды', 'команд')}`].filter(Boolean).join(' · ')),
        addBtn('module', m.module, m.name, m.danger, '+ весь модуль'),
        { click: () => { st.open = st.open === m.module ? null : m.module; draw(); } });
      head.classList.add('ac-mod');
      if (!open) { parts.push(head); continue; }
      const covered = opts.has('module', m.module);
      const cmds = m.commands.map((c) => row(
        nameSub(h('span', {}, pfx + c.command, c.aliases.length ? h('span', { class: 'tag' }, c.aliases.map((a) => pfx + a).join(' ')) : null), c.description || null),
        covered ? h('span', { class: 'val ac-has' }, 'в модуле ✓') : addBtn('command', c.key, pfx + c.command, m.danger)));
      cmds.forEach((r) => r.classList.add('ac-cmd'));
      if (m.danger.length) cmds.unshift(h('div', { class: 'ac-cmd ac-warn' }, '⚠ ' + m.danger.join('; ')));
      parts.push(h('div', { class: 'ac-group' }, head, ...cmds));
    }
    box.replaceChildren(...parts);
  }

  draw();
  acModal(opts.title, h('div', { class: 'stack' }, search, box));
  setTimeout(() => search.focus(), 50);
}

/* ------------------------------------------------------------------ главная вкладки ------------------------------------------------------------------ */

async function accessView(T) {
  const d = await api('/access/state');
  const pfx = d.prefix;

  const users = d.users.length
    ? d.users.map((u) => {
        const danger = u.grants.some((g) => g.danger.length) || (u.class && (d.classes.find((c) => c.idn === u.class) || { grants: [] }).grants.some((g) => g.danger.length));
        const what = [u.class ? 'класс ' + u.class : null, u.grants.length ? `${u.grants.length} ${plural(u.grants.length, 'разрешение', 'разрешения', 'разрешений')}` : null].filter(Boolean).join(' + ');
        return row(
          nameSub(h('span', {}, u.name || u.id, danger ? acDangerTag(['есть критически опасный доступ'], true) : null), acWho(u) + ` · доступно ${u.allowed} из ${u.total} команд`),
          h('span', { class: 'val' }, what),
          { click: () => { location.hash = '#/access/user/' + acEnc(u.id); } });
      })
    : empty(`пока ни у кого нет доступа — «выдать доступ» справа или ${pfx}access ответом на сообщение в телеграме`);

  const classes = d.classes.map((c) => row(
    nameSub(h('span', {}, c.idn, h('span', { class: 'tag' }, c.title), c.system ? h('span', { class: 'tag' }, 'системный') : null,
      c.grants.some((g) => g.danger.length) ? acDangerTag(['класс даёт критически опасный доступ'], true) : null),
      [c.description, c.grants.length ? 'даёт: ' + c.grants.map((g) => g.label).join(', ') : 'пока ничего не даёт'].filter(Boolean).join(' · ')),
    h('span', { class: 'val' }, `${c.users.length} ${plural(c.users.length, 'пользователь', 'пользователя', 'пользователей')}`),
    { click: () => { location.hash = '#/access/class/' + acEnc(c.idn); } }));

  const side = [
    h('button', { class: 'btn', type: 'button', onclick: () => acNewClass(d) }, 'новый класс'),
    h('button', { class: 'btn primary', type: 'button', onclick: () => acAddUser() }, 'выдать доступ'),
  ];
  mount(T, shell('доступ', { side },
    h('div', { class: 'faint', style: 'margin:0 0 14px 2px;max-width:820px' },
      `Человек с доступом пишет команды с префиксом «${pfx}» в чате, где его видит ваш аккаунт (ЛС, группа), и они выполняются как ваши. `
      + 'Доступ — это класс (готовый набор) и/или отдельные разрешения на модули и команды. Управление доступом и алиасами всегда только у вас.'),
    h('div', { class: 'h2', style: 'padding-left:6px' }, `у кого есть доступ · ${d.users.length}`), glass(users),
    h('div', { class: 'h2', style: 'padding-left:6px' }, `классы · ${d.classes.length}`), glass(classes)));
}

function acAddUser() {
  const inp = h('input', { class: 'field', placeholder: '@username или id', spellcheck: 'false', autocomplete: 'off' });
  const go = (close) => act(async () => {
    const r = await api('/access/resolve', { user: inp.value });
    close();
    location.hash = '#/access/user/' + acEnc(r.id);
  });
  const close = modal('выдать доступ', h('div', { class: 'stack' }, inp,
    h('div', { class: 'faint' }, 'кому: ваш аккаунт должен хотя бы раз видеть этого человека (ЛС или общий чат). Дальше выберете класс или разрешения.')),
  (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'), h('button', { class: 'btn primary', type: 'button', onclick: () => go(cl) }, 'дальше')]);
  inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(close); });
  setTimeout(() => inp.focus(), 50);
}

function acClassFields(c) {
  const idn = h('input', { class: 'field mono', placeholder: 'например helpers', spellcheck: 'false', autocomplete: 'off', value: c ? c.idn : '', disabled: c && c.system });
  const title = h('input', { class: 'field', placeholder: 'например «Помощники»', value: c ? c.title : '' });
  const desc = h('textarea', { class: 'field ac-desc', placeholder: 'что этот класс разрешает и кому его выдавать', rows: '2' });
  desc.value = c ? c.description : '';
  const idnHint = h('div', { class: 'faint' }, 'idn — ID класса: одно слово, буквы, цифры и _ без пробелов');
  idn.addEventListener('input', () => {
    const ok = !idn.value || /^[\p{L}\p{N}_]{1,32}$/u.test(idn.value);
    idnHint.textContent = ok ? 'idn — ID класса: одно слово, буквы, цифры и _ без пробелов' : 'в idn только буквы, цифры и _ — без пробелов и других символов';
    idnHint.style.color = ok ? '' : 'var(--danger)';
  });
  const el = h('div', { class: 'ac-fields' },
    h('label', {}, h('span', {}, 'idn'), idn), idnHint,
    h('label', {}, h('span', {}, 'название'), title),
    h('label', {}, h('span', {}, 'описание'), desc));
  return { el, idn, title, desc };
}

function acNewClass(d) {
  const f = acClassFields(null);
  let copy = null;
  const copyChips = [[null, 'с нуля'], ...d.classes.map((c) => [c.idn, 'как ' + c.idn])].map(([v, label]) => {
    const b = h('button', { class: 'chip' + (v === copy ? ' on' : ''), type: 'button', onclick: () => { copy = v; copyChips.forEach((x) => x.classList.toggle('on', x === b)); } }, label);
    return b;
  });
  const go = (close) => act(async () => {
    const r = await api('/access/class_create', { idn: f.idn.value, title: f.title.value, description: f.desc.value, copy_from: copy });
    close(); toast('класс создан', 'ok');
    location.hash = '#/access/class/' + acEnc(r.idn);
  });
  modal('новый класс', h('div', { class: 'stack' }, f.el, h('div', { class: 'h2' }, 'выдачи'), h('div', { class: 'chips' }, copyChips),
    h('div', { class: 'faint' }, 'что даёт класс, настроите на следующем шаге: «+ добавить доступ»')),
  (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'), h('button', { class: 'btn primary', type: 'button', onclick: () => go(cl) }, 'создать')]);
  setTimeout(() => f.idn.focus(), 50);
}

/* ------------------------------------------------------------------ пользователь ------------------------------------------------------------------ */

function acPickClass(d, current, onPick) {
  const st = { sel: null };
  const box = h('div', {});
  function draw() {
    if (st.sel) {
      const c = st.sel;
      const danger = c.grants.flatMap((g) => g.danger);
      box.replaceChildren(
        h('div', { class: 'ac-confirm' + (danger.length ? '' : ' calm') },
          h('div', { class: 'name' }, c.idn, h('span', { class: 'tag' }, c.title)),
          c.description ? h('div', { class: 'faint', style: 'margin:4px 0 8px' }, c.description) : null,
          h('div', {}, 'даёт: ', c.grants.length ? c.grants.map((g) => g.label).join(', ') : 'пока ничего (настройте класс)'),
          danger.length ? [h('div', { class: 'name', style: 'margin-top:10px' }, '⚠ критически опасный доступ'), h('ul', {}, [...new Set(danger)].map((x) => h('li', {}, x)))] : null,
          current ? h('div', { class: 'faint', style: 'margin-top:8px' }, `текущий класс ${current} будет заменён; личные разрешения останутся`) : null,
          h('div', { class: 'inline', style: 'margin-top:14px' },
            h('button', { class: 'btn primary' + (danger.length ? ' danger' : ''), type: 'button', onclick: () => onPick(c.idn, close) }, 'выдать класс ' + c.idn),
            h('button', { class: 'btn', type: 'button', onclick: () => { st.sel = null; draw(); } }, 'назад'))));
      return;
    }
    box.replaceChildren(glass(d.classes.map((c) => {
      const r = row(
        nameSub(h('span', {}, c.idn, h('span', { class: 'tag' }, c.title), c.grants.some((g) => g.danger.length) ? acDangerTag(['даёт критически опасный доступ'], true) : null),
          [c.description, c.grants.length ? 'даёт: ' + c.grants.map((g) => g.label).join(', ') : 'пока ничего не даёт'].filter(Boolean).join(' · ')),
        c.idn === current ? h('span', { class: 'val ac-has' }, 'выдан ✓') : h('span', { class: 'val' }, 'выбрать ›'),
        { click: () => { if (c.idn !== current) { st.sel = c; draw(); } } });
      return r;
    })));
  }
  const close = acModal('выдать класс', box);
  draw();
}

async function accessUser(T, id) {
  let d = await api('/access/state');
  let r = await api('/access/user?id=' + acEnc(id));
  const pfx = d.prefix;
  const who = r.who;

  const reload = () => route();
  const body = { id, name: who.name, username: who.username };

  const giveClass = () => acPickClass(d, r.user && r.user.class, (idn, close) => act(async () => {
    await api('/access/user_class', { ...body, class: idn });
    close(); toast('класс ' + idn + ' выдан', 'ok'); reload();
  }));
  const givePerm = () => {
    let u = r.user;
    acGrantPicker({
      title: 'выдать разрешение · ' + (who.name || id), d, scopes: true,
      has: (kind, value) => {
        if (!u) return false;
        if (u.grants.some((g) => g.kind === kind && g.value === value)) return true;
        if (kind === 'command') return u.grants.some((g) => g.kind === 'module' && g.value === value.split(':')[0]);
        return false;
      },
      onPick: async (kind, value) => {
        await api('/access/grant', { ...body, target: 'user', kind, value });
        r = await api('/access/user?id=' + acEnc(id)); u = r.user;
      },
    });
    const veil = document.body.lastElementChild;
    new MutationObserver((_, obs) => { if (!document.body.contains(veil)) { obs.disconnect(); reload(); } }).observe(document.body, { childList: true });
  };

  const u = r.user;
  const cls = u && u.class ? d.classes.find((c) => c.idn === u.class) : null;
  const giveBtns = h('div', { class: 'inline' },
    h('button', { class: 'btn primary', type: 'button', onclick: giveClass }, u && u.class ? 'сменить класс' : 'выдать класс'),
    h('button', { class: 'btn', type: 'button', onclick: givePerm }, '+ выдать разрешение'));

  const sections = [];
  if (!u) {
    sections.push(glass(
      row(nameSub('доступа нет', 'команды этого человека ваш юзербот сейчас не выполняет'), null),
      row(nameSub('выдать доступ', 'класс — готовый набор прав; разрешение — один модуль целиком или одна команда'), giveBtns, { wide: true })));
  } else {
    const chk = h('input', { class: 'field short', placeholder: pfx + 'команда', spellcheck: 'false' });
    const chkOut = h('span', { class: 'faint' });
    const check = () => act(async () => {
      const x = await api('/access/check', { id, command: chk.value });
      chkOut.textContent = (x.ok ? 'доступна' : 'нет доступа') + ' — ' + x.why;
      chkOut.style.color = x.ok ? '' : 'var(--danger)';
    });
    chk.addEventListener('keydown', (e) => { if (e.key === 'Enter') check(); });
    sections.push(glass(
      row(nameSub('доступно команд', `${u.allowed} из ${u.total}`), giveBtns, { wide: true }),
      row(nameSub('проверить команду', 'доступна ли команда и почему'), h('div', { class: 'inline' }, chk, h('button', { class: 'btn small', type: 'button', onclick: check }, 'проверить'), chkOut))));
    sections.push(h('div', { class: 'h2', style: 'padding-left:6px' }, 'класс'));
    sections.push(glass(cls
      ? row(nameSub(h('span', {}, cls.idn, h('span', { class: 'tag' }, cls.title), cls.grants.some((g) => g.danger.length) ? acDangerTag(cls.grants.flatMap((g) => g.danger)) : null),
          [cls.description, cls.grants.length ? 'даёт: ' + cls.grants.map((g) => g.label).join(', ') : 'пока ничего не даёт'].filter(Boolean).join(' · ')),
        [h('a', { class: 'link', href: '#/access/class/' + acEnc(cls.idn) }, 'открыть'),
          confirmBtn('снять класс', () => act(async () => { await api('/access/user_class', { id, class: null }); toast('класс снят', 'ok'); reload(); }))])
      : empty('класса нет')));
    sections.push(h('div', { class: 'h2', style: 'padding-left:6px' }, 'личные разрешения'));
    sections.push(glass(acGrantRows(u.grants, (g) => act(async () => {
      await api('/access/revoke', { target: 'user', id, kind: g.kind, value: g.value });
      toast('отозвано: ' + g.label, 'ok'); reload();
    }), 'личных разрешений нет — «+ выдать разрешение» выше')));
  }

  const side = u ? [confirmBtn('отозвать весь доступ', () => act(async () => { await api('/access/user_del', { id }); toast('доступ отозван', 'ok'); reload(); }), 'btn danger')] : [];
  mount(T, shell(who.name || id, { back: '#/access', backLabel: 'доступ', side },
    h('div', { class: 'faint', style: 'margin:0 0 14px 2px' }, acWho(who)),
    sections));
}

/* ------------------------------------------------------------------ класс ------------------------------------------------------------------ */

async function accessClass(T, idn) {
  const d = await api('/access/state');
  const c = d.classes.find((x) => x.idn === idn);
  if (!c) { location.hash = '#/access'; return; }

  const f = acClassFields(c);
  const save = h('button', { class: 'btn small', type: 'button', disabled: true, onclick: () => act(async () => {
    const r = await api('/access/class_update', { idn, new_idn: c.system ? null : f.idn.value, title: f.title.value, description: f.desc.value });
    toast('сохранено', 'ok');
    if (r.idn !== idn) location.hash = '#/access/class/' + acEnc(r.idn); else route();
  }) }, 'сохранить');
  [f.idn, f.title, f.desc].forEach((x) => x.addEventListener('input', () => {
    save.disabled = f.idn.value === c.idn && f.title.value === c.title && f.desc.value === c.description;
  }));

  const add = () => {
    let cur = c;
    acGrantPicker({
      title: 'добавить доступ · ' + idn, d, scopes: true,
      has: (kind, value) => cur.grants.some((g) => g.kind === kind && g.value === value)
        || (kind === 'command' && cur.grants.some((g) => g.kind === 'module' && g.value === value.split(':')[0])),
      onPick: async (kind, value) => {
        await api('/access/grant', { target: 'class', id: idn, kind, value });
        cur = (await api('/access/state')).classes.find((x) => x.idn === idn) || cur;
      },
    });
    const veil = document.body.lastElementChild;
    new MutationObserver((_, obs) => { if (!document.body.contains(veil)) { obs.disconnect(); route(); } }).observe(document.body, { childList: true });
  };

  const users = c.users.map((uid) => {
    const u = d.users.find((x) => x.id === uid) || { id: uid, name: uid };
    return row(nameSub(u.name || uid, acWho(u)), null, { click: () => { location.hash = '#/access/user/' + acEnc(uid); } });
  });

  const side = c.system ? [] : [confirmBtn('удалить класс', () => act(async () => {
    const r = await api('/access/class_delete', { idn });
    toast(r.users_without_class ? `класс удалён, ${r.users_without_class} без класса` : 'класс удалён', 'ok');
    location.hash = '#/access';
  }), 'btn danger')];
  mount(T, shell(idn, { back: '#/access', backLabel: 'доступ', side },
    glass(row(f.el, null, { wide: true }), row(nameSub(c.system ? 'системный класс: idn и выдачи не меняются, удалить нельзя' : 'изменения применяются сразу после сохранения', null), save)),
    h('div', { class: 'h2', style: 'padding-left:6px' }, 'что даёт класс'),
    glass(acGrantRows(c.grants, c.system ? null : (g) => act(async () => {
      await api('/access/revoke', { target: 'class', id: idn, kind: g.kind, value: g.value });
      toast('убрано: ' + g.label, 'ok'); route();
    }), 'класс пока ничего не даёт'),
    c.system ? null : row(nameSub('добавить доступ', 'модуль целиком, одну команду или набор модулей; опасное помечено ⚠'),
      h('button', { class: 'btn primary', type: 'button', onclick: add }, '+ добавить доступ'))),
    h('div', { class: 'h2', style: 'padding-left:6px' }, `у кого этот класс · ${c.users.length}`),
    glass(users.length ? users : empty('пока ни у кого'))));
}

/* ------------------------------------------------------------------ алиасы (внизу вкладки «конфиги») ------------------------------------------------------------------ */

async function acAliasesBlock() {
  let d;
  try { d = await api('/aliases'); } catch (e) { return null; }
  const pfx = d.prefix;
  const rows = d.aliases.map((a) => row(
    nameSub(h('span', {}, h('code', {}, pfx + a.name), ' → ', h('code', {}, pfx + a.command + (a.args ? ' ' + a.args : ''))),
      a.alive ? (a.module ? 'модуль ' + a.module : '') : 'команды больше нет — алиас не работает'),
    [h('button', { class: 'btn small', type: 'button', onclick: () => acAliasWizard(d, a) }, 'изменить'),
      confirmBtn('удалить', () => act(async () => { await api('/aliases/del', { name: a.name }); toast('алиас удалён', 'ok'); route(); }))]));
  return [
    h('div', { class: 'h2', style: 'padding-left:6px' }, `алиасы команд · ${d.aliases.length}`),
    glass(rows.length ? rows : empty(`алиасов пока нет — например, ${pfx}h вместо ${pfx}help`),
      row(nameSub('новый алиас', `короткое имя для любой команды, сразу с аргументами. В телеграме: ${pfx}alias команда [аргументы] новое_имя`),
        h('button', { class: 'btn primary', type: 'button', onclick: () => acAliasWizard(d, null) }, '+ новый алиас'))),
  ];
}

/* Пошаговое окно алиаса: модуль → команда → аргументы и имя (с живым предпросмотром). */
function acAliasWizard(d, existing) {
  const pfx = d.prefix;
  const st = { step: existing ? 3 : 1, module: existing ? existing.module : null, command: existing ? existing.command : null, q: '' };
  const box = h('div', { class: 'stack' });
  const name = h('input', { class: 'field mono', placeholder: 'например h', spellcheck: 'false', autocomplete: 'off', value: existing ? existing.name : '' });
  const args = h('input', { class: 'field mono', placeholder: 'необязательно', spellcheck: 'false', autocomplete: 'off', value: existing ? existing.args : '' });
  const preview = h('div', { class: 'ac-preview' });
  const updPreview = () => {
    const n = name.value.trim().replace(/^\./, '').toLowerCase();
    const bad = n && d.commands.includes(n);
    preview.replaceChildren(
      h('code', {}, pfx + (n || '…')), ' → ', h('code', {}, pfx + (st.command || '…') + (args.value.trim() ? ' ' + args.value.trim() : '')),
      h('div', { class: 'faint', style: bad ? 'color:var(--danger)' : '' }, bad ? `${pfx}${n} — уже существующая команда, выберите другое имя`
        : `а ${pfx}${n || 'имя'} x запустит ${pfx}${st.command || 'команда'}${args.value.trim() ? ' ' + args.value.trim() : ''} x`));
  };
  name.addEventListener('input', updPreview); args.addEventListener('input', updPreview);

  const crumbs = () => h('div', { class: 'chips ac-steps' },
    [[1, st.module ? 'модуль: ' + ((d.modules.find((m) => m.module === st.module) || {}).name || st.module) : '1 · модуль'],
      [2, st.command ? 'команда: ' + pfx + st.command : '2 · команда'], [3, '3 · имя']].map(([n, label]) =>
      h('button', { class: 'chip' + (st.step === n ? ' on' : ''), type: 'button', disabled: (n === 2 && !st.module) || (n === 3 && !st.command),
        onclick: () => { st.step = n; draw(); } }, label)));

  function draw() {
    const parts = [crumbs()];
    if (st.step === 1) {
      const s = h('input', { class: 'field', placeholder: 'найти модуль или команду', spellcheck: 'false', value: st.q });
      s.addEventListener('input', () => { st.q = s.value.trim().toLowerCase(); draw(); const x = box.querySelector('input'); x.focus(); x.setSelectionRange(x.value.length, x.value.length); });
      const q = st.q;
      const mods = d.modules.filter((m) => !q || m.name.toLowerCase().includes(q) || m.module.includes(q) || m.commands.some((c) => c.command.includes(q)));
      parts.push(s, glass(mods.length ? mods.map((m) => row(
        nameSub(h('span', {}, m.name, h('span', { class: 'tag' }, m.module)), m.commands.map((c) => pfx + c.command).join(' ')),
        h('span', { class: 'val' }, 'выбрать ›'),
        { click: () => { st.module = m.module; st.step = 2; if (!m.commands.some((c) => c.command === st.command)) st.command = null; draw(); } })) : [empty('ничего не найдено')]));
    } else if (st.step === 2) {
      const m = d.modules.find((x) => x.module === st.module);
      parts.push(glass((m ? m.commands : []).map((c) => row(
        nameSub(h('span', {}, pfx + c.command, c.aliases.length ? h('span', { class: 'tag' }, c.aliases.map((a) => pfx + a).join(' ')) : null), c.description || null),
        h('span', { class: 'val' }, st.command === c.command ? 'выбрана ✓' : 'выбрать ›'),
        { click: () => { st.command = c.command; st.step = 3; draw(); setTimeout(() => name.focus(), 30); } }))));
    } else {
      parts.push(h('div', { class: 'ac-fields' },
        h('label', {}, h('span', {}, 'аргументы'), args),
        h('div', { class: 'faint' }, 'подставятся после команды; всё, что вы допишете после алиаса, добавится в конец'),
        h('label', {}, h('span', {}, 'имя алиаса'), name)), preview);
      updPreview();
    }
    box.replaceChildren(...parts);
  }
  const go = (close) => act(async () => {
    if (!st.command) throw new Error('выберите команду');
    await api('/aliases/set', { name: name.value, command: st.command, args: args.value, old: existing ? existing.name : null });
    close(); toast('алиас ' + pfx + name.value.trim().replace(/^\./, '').toLowerCase() + ' сохранён', 'ok'); route();
  });
  name.addEventListener('keydown', (e) => { if (e.key === 'Enter') go(close); });
  const close = acModal(existing ? 'алиас ' + pfx + existing.name : 'новый алиас', box,
    (cl) => [h('button', { class: 'btn', type: 'button', onclick: () => cl() }, 'отмена'), h('button', { class: 'btn primary', type: 'button', onclick: () => go(cl) }, 'сохранить')]);
  draw();
}
