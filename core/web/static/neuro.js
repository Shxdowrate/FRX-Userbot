/* FRX web. Вкладка «neuro»: подключения и ключи нейросетей, модели, планы и расход токенов, настройки агента, инструменты-файлы.
 * И помощник WebIDE (wiAssistPane): выделил код → сказал, что сделать → смотришь отличия → «подтвердить».
 * Использует утилиты app.js / webide.js только во время вызова. Данные попадают в DOM через textContent; ключи в списках — маской, целиком — по кнопке «показать». */
'use strict';

const NR_PERIODS = [['none', 'без периода (считать всё время)'], ['day', 'день'], ['week', 'неделя'], ['month', 'месяц'], ['total', 'всего (до ручного сброса)']];
const nrNum = (n) => String(Math.round(n || 0)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
const nrWhen = (ts) => (ts ? new Date(ts * 1000).toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : '');

/* строка «расход | план | лимит» — один формат и на сайте, и в Telegram (см. NeuroDash) */
function nrPlanLine(plan, spent) {
  const parts = [];
  if (spent !== undefined && spent !== null) parts.push(`за сообщение: ${nrNum(spent)}`);
  if (plan) {
    parts.push(`из плана: ${nrNum(plan.used)}`);
    parts.push(plan.limit ? `лимит: ${nrNum(plan.limit)}${plan.percent !== null ? ` (${plan.percent}%)` : ''}` : 'лимит не задан');
  }
  return parts.join(' | ');
}

const NR_OPEN_CATS = new Set();                 // какие категории инструментов раскрыты на телефоне

async function nrStream(body, onEvent, signal) {
  const r = await fetch('/api/neuro/assist', { method: 'POST', headers: { 'X-FRX': '1', 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal });
  if (!r.ok) { let m = 'ошибка ' + r.status; try { m = (await r.json()).error || m; } catch (e) { /* не JSON */ } throw new Error(m); }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = '';
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, i); buf = buf.slice(i + 1);
      if (line.trim()) { try { onEvent(JSON.parse(line)); } catch (e) { /* битая строка потока */ } }
    }
  }
}

/* ------------------------------------------------------------------ *
 * страница neuro
 * ------------------------------------------------------------------ */

async function neuroView(T) {
  let S = await api('/neuro/state'), tools = await api('/neuro/tools');
  const root = h('div', { class: 'nr' });
  const redraw = async (what) => {
    if (what !== 'tools') S = await api('/neuro/state');
    if (what !== 'conns') tools = await api('/neuro/tools');
    draw();
  };
  const refName = (ref) => { const m = S.models.find((x) => x.ref === ref); return m ? `${m.model} · ${m.conn_name}` : 'не выбрана'; };

  /* ---------- подключения ---------- */
  function connCard(c) {
    const st = c.plan_status, pv = S.providers.find((p) => p.id === c.provider) || {};
    const keyText = h('code', { class: 'nr-key' }, c.key_set ? c.key : (pv.key ? '— не задан —' : 'не нужен'));
    let shown = false;
    const showBtn = h('button', { class: 'btn small', type: 'button', style: c.key_set ? '' : 'display:none', onclick: () => act(async () => {
      if (shown) { keyText.textContent = c.key; showBtn.textContent = 'показать'; shown = false; return; }
      const r = await api('/neuro/conn/reveal', { id: c.id }); keyText.textContent = r.key; showBtn.textContent = 'скрыть'; shown = true;
    }) }, 'показать');
    const copyBtn = h('button', { class: 'btn small', type: 'button', style: c.key_set ? '' : 'display:none', onclick: () => act(async () => { const r = await api('/neuro/conn/reveal', { id: c.id }); await navigator.clipboard?.writeText(r.key); toast('ключ скопирован', 'ok'); }) }, 'копировать');
    const models = S.models.filter((m) => m.conn === c.id);
    const bar = st && st.limit ? h('div', { class: 'nr-bar', title: `${nrNum(st.used)} из ${nrNum(st.limit)}` }, h('i', { style: `width:${Math.min(100, st.percent || 0)}%`, class: st.exceeded ? 'over' : (st.percent > 80 ? 'warn' : '') })) : null;
    return h('div', { class: 'nr-card' + (c.enabled ? '' : ' off') },
      h('div', { class: 'nr-head' }, h('b', {}, c.name), h('span', { class: 'tag' }, c.provider_title), c.enabled ? null : h('span', { class: 'tag' }, 'отключено'), h('span', { class: 'grow' }),
        toggle(c.enabled, (v) => act(async () => { await api('/neuro/conn/update', { id: c.id, enabled: v }); await redraw('conns'); })),
        c.auto ? null : h('button', { class: 'lb-x bad', type: 'button', title: 'удалить подключение', onclick: () => { if (confirm(`Удалить подключение «${c.name}» вместе с ключом?`)) act(async () => { await api('/neuro/conn/delete', { id: c.id }); await redraw('conns'); }); } }, '×')),
      h('div', { class: 'nr-line' }, h('span', { class: 'label' }, 'адрес'), h('code', {}, c.base_url)),
      (pv.key || c.key_set) ? h('div', { class: 'nr-line' }, h('span', { class: 'label' }, 'ключ'), keyText, showBtn, copyBtn) : null,
      h('div', { class: 'nr-line' }, h('span', { class: 'label' }, 'модели'), h('span', {}, `${models.length ? models.length + ' шт.' : 'пока нет'}${c.models_checked ? ' · обновлено ' + nrWhen(c.models_checked) : ''}`),
        h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { const r = await api('/neuro/conn/refresh', { id: c.id }); toast(`моделей: ${r.models}`, 'ok'); await redraw('conns'); }) }, 'обновить список')),
      h('div', { class: 'nr-line' }, h('span', { class: 'label' }, 'расход'),
        h('span', {}, st ? `${nrNum(st.used)} ток. (вход ${nrNum(st.in)}, выход ${nrNum(st.out)}, запросов ${st.calls})` : '—'),
        st && st.limit ? h('span', { class: 'nr-pct' + (st.exceeded ? ' bad' : '') }, `${st.percent}% из ${nrNum(st.limit)}`) : h('span', { class: 'muted' }, 'лимит не задан'), bar),
      st && st.resets_at ? h('div', { class: 'nr-line muted' }, `счётчик обнулится ${nrWhen(st.resets_at)}`) : null,
      h('div', { class: 'actions nr-actions' }, h('button', { class: 'btn small', type: 'button', onclick: () => planDrawer(c) }, 'план и лимит'), h('button', { class: 'btn small', type: 'button', onclick: () => editDrawer(c, pv) }, 'изменить'),
        h('button', { class: 'btn small', type: 'button', onclick: () => act(async () => { await api('/neuro/conn/reset_usage', { id: c.id }); toast('счётчик сброшен', 'ok'); await redraw('conns'); }) }, 'сбросить счётчик')));
  }

  function planDrawer(c) {
    const p = { limit: c.plan.limit === null ? '' : c.plan.limit, period: c.plan.period, hard: !!c.plan.hard };
    const ui = { changed() {}, rebuild() {} };
    wiDrawer(`План · ${c.name}`, [h('div', { class: 'wi-hint', style: 'margin-bottom:8px' }, 'План — лимит токенов на подключение за период. Под каждым сообщением NeuroDash показывает «потрачено за сообщение | из плана | лимит». Токены считаются по данным провайдера (если он их не прислал — оценкой).'),
      wiFld('Лимит токенов', wiText(p, 'limit', ui, { type: 'number', placeholder: 'пусто — без лимита' })), wiFld('Период', wiSelect(p, 'period', NR_PERIODS, ui)),
      wiCheck(p, 'hard', 'жёсткий лимит: после исчерпания запросы не отправляются', ui)], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => act(async () => { await api('/neuro/conn/plan', { id: c.id, limit: p.limit === '' ? null : p.limit, period: p.period, hard: p.hard }); close(); await redraw('conns'); }) }, 'сохранить')]);
  }

  function editDrawer(c, pv) {
    const f = { name: c.name, key: '', base_url: c.base_url, verify_ssl: c.verify_ssl, scope: c.scope };
    const ui = { changed() {}, rebuild() {} };
    wiDrawer(`Изменить · ${c.name}`, [wiFld('Название', wiText(f, 'name', ui)),
      pv.key ? wiFld('Новый ключ', wiText(f, 'key', ui, { type: 'password', placeholder: 'оставьте пустым, чтобы не менять' })) : null,
      pv.url_editable ? wiFld('Адрес сервера', wiText(f, 'base_url', ui)) : null,
      c.provider === 'gigachat' ? wiFld('Область', wiSelect(f, 'scope', pv.scopes.map((x) => [x, x]), ui)) : null,
      c.provider === 'gigachat' ? wiCheck(f, 'verify_ssl', 'проверять сертификат сервера (нужен сертификат Минцифры)', ui) : null].filter(Boolean), null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => act(async () => { const body = { id: c.id, name: f.name, base_url: f.base_url, verify_ssl: f.verify_ssl, scope: f.scope }; if (f.key.trim()) body.key = f.key; await api('/neuro/conn/update', body); close(); await redraw('conns'); toast('сохранено', 'ok'); }) }, 'сохранить')]);
  }

  function addDrawer() {
    const f = { provider: 'openai', name: '', key: '', base_url: '', scope: 'GIGACHAT_API_PERS', verify_ssl: false };
    const box = h('div', { class: 'wi-cform' });
    const ui = { changed() {}, rebuild() { draw2(); } };
    function draw2() {
      const pv = S.providers.find((p) => p.id === f.provider);
      const keyIn = wiText(f, 'key', ui, { type: 'password', placeholder: pv.key ? 'вставьте ключ' : 'необязательно' });
      const eye = h('button', { class: 'btn small', type: 'button', onclick: () => { keyIn.type = keyIn.type === 'password' ? 'text' : 'password'; eye.textContent = keyIn.type === 'password' ? 'показать' : 'скрыть'; } }, 'показать');
      box.replaceChildren(
        wiFld('Провайдер', wiSelect(f, 'provider', S.providers.map((p) => [p.id, p.title]), ui, true), 'провайдер выбирается вручную — ключ уходит только ему'),
        h('div', { class: 'wi-note' }, pv.hint),
        pv.key || f.provider === 'custom' ? wiFld(f.provider === 'gigachat' ? 'Ключ авторизации (Authorization key)' : pv.key ? 'Ключ' : 'Ключ (если нужен)', h('div', { class: 'wi-inline' }, keyIn, eye), f.provider === 'gigachat' ? 'не access token — его FRX получает и обновляет сам каждые ~30 минут' : null) : null,
        pv.url_editable ? wiFld(pv.url_required ? 'Адрес сервера' : 'Адрес (если не по умолчанию)', wiText(f, 'base_url', ui, { placeholder: pv.base || 'http://127.0.0.1:1234/v1' })) : null,
        f.provider === 'gigachat' ? wiFld('Область', wiSelect(f, 'scope', pv.scopes.map((x) => [x, x]), ui)) : null,
        f.provider === 'gigachat' ? wiCheck(f, 'verify_ssl', 'проверять сертификат сервера (нужен корневой сертификат Минцифры; без него оставьте выключенным)', ui) : null,
        wiFld('Название (необязательно)', wiText(f, 'name', ui, { placeholder: pv.title })));
    }
    draw2();
    wiDrawer('Добавить нейросеть', box, null, (close) => [
      h('span', { class: 'wi-hint grow' }, 'После добавления сразу запрашивается список моделей.'),
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: (e) => { const btn = e.currentTarget; btn.disabled = true; act(async () => {
        try { const r = await api('/neuro/conn/add', { provider: f.provider, key: f.key, name: f.name, base_url: f.base_url, scope: f.scope, verify_ssl: f.verify_ssl });
          close(); toast(r.warning ? `добавлено, но моделей получить не удалось: ${r.warning}` : `добавлено · моделей: ${r.models}`, r.warning ? 'error' : 'ok'); await redraw('conns'); }
        catch (err) { close(); throw err; }                                // окно закрывается и при ошибке — уведомление видно, а не прячется за размытием
        finally { btn.disabled = false; }
      }); } }, 'добавить')]);
  }

  /* ---------- модели ---------- */
  function modelsSection() {
    const q = { text: '' };
    const list = h('div', { class: 'nr-models' });
    const sel = (surface) => h('select', { class: 'field nr-sel' }, [h('option', { value: '' }, '— не выбрана —'), ...S.models.filter((m) => m.enabled !== false).map((m) => h('option', { value: m.ref }, `${m.model} · ${m.conn_name}`))]);
    const selChat = sel('chat'), selIde = sel('ide');
    selChat.value = S.selected.chat || ''; selIde.value = S.selected.ide || '';
    const bind = (el, surface) => el.addEventListener('change', () => { if (el.value) act(async () => { await api('/neuro/select', { surface, ref: el.value }); S.selected[surface] = el.value; toast('модель выбрана', 'ok'); }); });
    bind(selChat, 'chat'); bind(selIde, 'ide');
    const draw3 = () => {
      const groups = {};
      S.models.forEach((m) => { if (!q.text || m.model.toLowerCase().includes(q.text)) (groups[m.conn] = groups[m.conn] || []).push(m); });
      list.replaceChildren(...Object.entries(groups).map(([cid, ms]) => h('div', { class: 'nr-mgroup' }, h('div', { class: 'wi-lab' }, `${ms[0].conn_name} · ${ms.length}`),
        h('div', { class: 'nr-mchips' }, ms.map((m) => h('label', { class: 'nr-mchip' + (m.enabled === false ? ' off' : ''), title: m.model },
          (() => { const cb = h('input', { type: 'checkbox', class: 'wi-cb' }); cb.checked = m.enabled !== false; cb.addEventListener('change', () => act(async () => { await api('/neuro/model/enable', { conn: cid, model: m.model, enabled: cb.checked }); m.enabled = cb.checked; })); return cb; })(),
          h('span', {}, m.model)))))));
      if (!Object.keys(groups).length) list.appendChild(h('div', { class: 'wi-note' }, S.models.length ? 'Ничего не найдено.' : 'Моделей пока нет — добавьте подключение или запустите Ollama.'));
    };
    const search = h('input', { class: 'field', placeholder: 'поиск модели…', spellcheck: 'false' });
    search.addEventListener('input', () => { q.text = search.value.trim().toLowerCase(); draw3(); });
    draw3();
    return [h('div', { class: 'nr-pick' }, h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Модель для NeuroDash в Telegram'), selChat), h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Модель для помощника в WebIDE'), selIde)),
      h('div', { class: 'wi-hint', style: 'margin:6px 0 10px' }, 'Галочка — показывать модель в списках выбора. Локальные модели Ollama подхватываются сами.'), search, list];
  }

  /* ---------- настройки агента ---------- */
  function settingsSection() {
    const s = { ...S.settings, temperature: S.settings.temperature === null ? '' : S.settings.temperature };
    const ui = { changed() {}, rebuild() {} };
    const names = { categories: 'по категориям (экономит токены)', full: 'все инструменты сразу', off: 'без инструментов', confirm: 'спрашивать подтверждение', deny: 'запретить', allow: 'разрешить без вопросов', auto: 'авто (только Ollama)', on: 'всегда', off2: 'никогда' };
    const opts = (k) => S.choices[k].map((v) => [v, names[v === 'off' && k === 'fewshot' ? 'off2' : v] || v]);
    return h('div', { class: 'nr-settings' },
      h('div', { class: 'nr-grid' }, wiFld('Инструменты в запросе', wiSelect(s, 'tools_mode', opts('tools_mode'), ui)), wiFld('Опасные инструменты (run_python, run_command…)', wiSelect(s, 'allow_dangerous', opts('allow_dangerous'), ui)), wiFld('Тяжёлые инструменты (много токенов)', wiSelect(s, 'allow_heavy', opts('allow_heavy'), ui), 'README целиком и т.п.: спрашивать разрешение'),
        wiFld('Примеры формата (few-shot)', wiSelect(s, 'fewshot', opts('fewshot'), ui), 'нужны маленьким локальным моделям'), wiFld('Шагов с инструментами на вопрос', wiText(s, 'max_steps', ui, { type: 'number' })),
        wiFld('Бюджет контекста, токенов', wiText(s, 'context_budget', ui, { type: 'number' }), 'старые ходы диалога обрезаются'), wiFld('Резерв под ответ, токенов', wiText(s, 'answer_reserve', ui, { type: 'number' })),
        wiFld('Предел ответа инструмента, символов', wiText(s, 'tool_output_limit', ui, { type: 'number' })), wiFld('Таймаут инструмента, сек', wiText(s, 'tool_timeout', ui, { type: 'number' })),
        wiFld('Температура (необязательно)', wiText(s, 'temperature', ui, { type: 'number', placeholder: 'по умолчанию у модели' }))),
      wiFld('Ваши указания в системный промпт', wiText(s, 'system_extra', ui, { area: true, rows: 3, placeholder: 'например: отвечай коротко и без эмодзи' })),
      h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', onclick: () => act(async () => { const r = await api('/neuro/settings', { patch: s }); S.settings = r.settings; toast('настройки сохранены', 'ok'); }) }, 'сохранить настройки')));
  }

  /* ---------- инструменты ---------- */
  const TOOL_TEMPLATE = '"""\nname: my_tool\ncategory: custom\ntitle: Мой инструмент\ndescription: что делает — одной строкой, это увидит модель\nargs: text: string\nexample: {"text": "привет"}\ndangerous: false\n"""\n\n\nasync def run(text: str = \'\') -> str:\n    return text.upper()\n';

  function toolDrawer(existing) {
    const st = { source: existing || TOOL_TEMPLATE, replace: !!existing };
    const ta = h('textarea', { class: 'wi-field nr-code', rows: 18, spellcheck: 'false' });
    ta.value = st.source; ta.addEventListener('input', () => { st.source = ta.value; });
    const file = h('input', { type: 'file', accept: '.py,text/plain' });
    file.addEventListener('change', async () => { const f = file.files[0]; if (!f) return; if (f.size > 40960) { toast('файл больше 40 КБ', 'error'); return; } ta.value = st.source = await f.text(); });
    wiDrawer(existing ? 'Изменить инструмент' : 'Новый инструмент', [h('div', { class: 'wi-hint' }, 'Инструмент — Python-файл: в докстринге в начале — name, category, description, args (подсказка модели), необязательно example, dangerous: true и heavy: <во сколько токенов обходится>; дальше функция run(). Выполняется в процессе юзербота с правами модулей — инструмент с dangerous: true перед запуском спрашивает подтверждение.'),
      ta, h('div', { class: 'wi-inline' }, h('button', { class: 'btn small', type: 'button', onclick: () => file.click() }, 'загрузить .py'), h('span', { class: 'wi-hint' }, 'или вставьте код выше'))], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => act(async () => { const r = await api('/neuro/tools/add', { source: st.source, replace: st.replace }); toast(`инструмент «${r.tool.name}» сохранён`, 'ok'); close(); await redraw('tools'); }) }, 'сохранить')]);
  }

  async function viewTool(t) {
    const r = await api('/neuro/tools/source?name=' + encodeURIComponent(t.name));
    const pre = h('pre', { class: 'nr-codeview' }); pre.innerHTML = pyHighlight(r.source);
    wiDrawer(`${t.title} · ${t.name}`, [h('div', { class: 'wi-hint' }, t.path), pre], null, (close) => [
      h('button', { class: 'btn', onclick: () => { close(); setTimeout(() => toolDrawer(r.source), 250); } }, 'изменить'), h('button', { class: 'btn primary', onclick: close }, 'закрыть')]);
  }

  function catDrawer(cat) {
    const f = { title: cat.title, description: cat.description, keywords: cat.keywords };
    const ui = { changed() {}, rebuild() {} };
    wiDrawer(`Категория · ${cat.id}`, [wiFld('Название', wiText(f, 'title', ui)), wiFld('Описание (его видит модель)', wiText(f, 'description', ui)),
      wiFld('Ключевые слова (через запятую)', wiText(f, 'keywords', ui, { area: true, rows: 3 }), 'если вопрос содержит такое слово, инструменты категории попадают в запрос сразу; иначе модель откроет категорию сама')], null, (close) => [
      h('button', { class: 'btn', onclick: close }, 'отмена'),
      h('button', { class: 'btn primary', onclick: () => act(async () => { await api('/neuro/tools/category', { id: cat.id, ...f }); close(); await redraw('tools'); }) }, 'сохранить')]);
  }

  function toolsSection() {
    const out = [h('div', { class: 'wi-hint', style: 'margin-bottom:10px' }, 'Инструменты — файлы в user/data/neuro/tools/<категория>/. В Telegram их добавляет команда .neurotool (ответом на код или файл), она же присылает инструменты файлами. Категории экономят токены: в запрос попадают только подходящие.'),
      h('div', { class: 'actions', style: 'justify-content:flex-start;margin-bottom:10px' }, h('button', { class: 'btn small primary', type: 'button', onclick: () => toolDrawer() }, '+ инструмент'))];
    tools.categories.forEach((cat) => {
      const items = tools.tools.filter((t) => t.category === cat.id);
      const catOpen = NR_OPEN_CATS.has(cat.id);
      out.push(h('div', { class: 'nr-cat' + (cat.enabled ? '' : ' off') + (catOpen ? '' : ' mclosed') },
        h('div', { class: 'nr-cathead', onclick: (e) => {                       // на телефоне категории свёрнуты: тап по шапке — раскрыть
          if (!mIsPhone() || e.target.closest('button, input, label, .switch, .toggle')) return;
          const box = e.currentTarget.parentNode, open = box.classList.toggle('mclosed') === false;
          if (open) NR_OPEN_CATS.add(cat.id); else NR_OPEN_CATS.delete(cat.id);
        } }, h('span', { class: 'nr-caret m-only' }, '▸'), h('b', {}, cat.title), h('span', { class: 'tag' }, cat.id), h('span', { class: 'muted' }, `${cat.enabled_tools}/${cat.count} вкл.`), h('span', { class: 'grow' }),
          h('button', { class: 'wi-ico', type: 'button', title: 'описание и ключевые слова категории', onclick: () => catDrawer(cat) }, '✎'),
          toggle(cat.enabled, (v) => act(async () => { await api('/neuro/tools/enable', { category: cat.id, enabled: v }); await redraw('tools'); }))),
        cat.description ? h('div', { class: 'wi-hint' }, cat.description) : null,
        h('div', { class: 'nr-tools' }, items.map((t) => h('div', { class: 'nr-tool' + (t.enabled ? '' : ' off') },
          h('div', { class: 'nr-tmain' }, h('div', {}, h('b', {}, t.title || t.name), h('code', { class: 'nr-tn' }, t.name), t.dangerous ? h('span', { class: 'tag nr-danger' }, 'опасный') : null, t.heavy ? h('span', { class: 'tag' }, 'тяжёлый') : null, t.broken ? h('span', { class: 'tag nr-danger' }, 'повреждён') : null),
            h('div', { class: 'wi-hint' }, t.description), h('div', { class: 'wi-hint mono' }, `${t.name}(${t.args})`)),
          h('div', { class: 'nr-tact' }, h('button', { class: 'wi-ico', type: 'button', title: 'посмотреть код', onclick: () => act(() => viewTool(t)) }, '‹›'),
            toggle(t.enabled && cat.enabled, (v) => act(async () => { await api('/neuro/tools/enable', { name: t.name, enabled: v }); await redraw('tools'); })),
            h('button', { class: 'wi-ico bad', type: 'button', title: 'удалить', onclick: () => { if (confirm(`Удалить инструмент «${t.name}»? Файл будет стёрт.`)) act(async () => { await api('/neuro/tools/delete', { name: t.name }); await redraw('tools'); }); } }, '×')))))));
    });
    return out;
  }

  /* ---------- расход по дням ---------- */
  function usageSection() {
    const days = Object.keys(S.days || {}).sort();
    if (!days.length) return h('div', { class: 'wi-note' }, 'Запросов пока не было — расход появится здесь.');
    const sums = days.map((d) => { let i = 0, o = 0; Object.values(S.days[d]).forEach((models) => Object.values(models).forEach((v) => { i += v[0]; o += v[1]; })); return { d, i, o }; });
    const max = Math.max(...sums.map((x) => x.i + x.o), 1);
    return h('div', { class: 'nr-days' }, sums.map((x) => h('div', { class: 'nr-day', title: `${x.d}: вход ${nrNum(x.i)}, выход ${nrNum(x.o)}` },
      h('div', { class: 'nr-dbar' }, h('i', { class: 'in', style: `height:${(x.i / max) * 100}%` }), h('i', { class: 'out', style: `height:${(x.o / max) * 100}%` })), h('span', {}, x.d.slice(8)))));
  }

  function draw() {
    const sec = (title, ...kids) => h('div', { class: 'nr-sec' }, h('div', { class: 'h2', style: 'padding-left:6px' }, title), glass(...kids));
    root.replaceChildren(
      sec('Подключения', h('div', { class: 'nr-conns' }, S.connections.map(connCard)), h('div', { class: 'actions', style: 'justify-content:flex-start;padding:8px 14px 14px' }, h('button', { class: 'btn primary', type: 'button', onclick: addDrawer }, '+ добавить нейросеть'))),
      sec('Модели', h('div', { class: 'nr-pad' }, ...modelsSection())),
      sec('Расход токенов за 30 дней', h('div', { class: 'nr-pad' }, usageSection(), h('div', { class: 'wi-hint' }, h('span', { class: 'nr-lg in' }), ' вход  ', h('span', { class: 'nr-lg out' }), ' выход'))),
      sec('Агент и инструменты — настройки', h('div', { class: 'nr-pad' }, settingsSection())),
      sec('Инструменты', h('div', { class: 'nr-pad' }, ...toolsSection())));
  }
  draw();
  const node = shell('neuro', { side: [h('span', { class: 'wi-hint' }, 'NeuroDash: ' + refName(S.selected.chat))] },
    h('div', { class: 'faint', style: 'margin:-4px 0 18px 6px;max-width:760px' }, 'Нейросети для NeuroDash в Telegram и для помощника в FRX WebIDE: ключи (скрыты, пока не нажмёте «показать»), модели, лимиты токенов и инструменты. Провайдер выбирается вручную при добавлении ключа.'), root);
  node.classList.add('wide');
  mount(T, node);
}

/* ------------------------------------------------------------------ *
 * помощник в FRX WebIDE: вкладка «neuro» в правой панели редактора
 * ------------------------------------------------------------------ */

function nrDiff(a, b) {                                              // построчная разница (LCS) → [{t:' '|'+'|'-', text}]
  const A = a.split('\n'), B = b.split('\n');
  if (A.length * B.length > 250000) return [...A.map((text) => ({ t: '-', text })), ...B.map((text) => ({ t: '+', text }))];
  const L = Array.from({ length: A.length + 1 }, () => new Uint16Array(B.length + 1));
  for (let i = A.length - 1; i >= 0; i--) for (let j = B.length - 1; j >= 0; j--) L[i][j] = A[i] === B[j] ? L[i + 1][j + 1] + 1 : Math.max(L[i + 1][j], L[i][j + 1]);
  const out = [];
  let i = 0, j = 0;
  while (i < A.length && j < B.length) {
    if (A[i] === B[j]) { out.push({ t: ' ', text: A[i] }); i++; j++; } else if (L[i + 1][j] >= L[i][j + 1]) out.push({ t: '-', text: A[i++] }); else out.push({ t: '+', text: B[j++] });
  }
  while (i < A.length) out.push({ t: '-', text: A[i++] });
  while (j < B.length) out.push({ t: '+', text: B[j++] });
  return out;
}

function wiAssistPane(ed, host) {
  const st = { mode: 'edit', busy: false, abort: null, req: null, result: null, history: [], models: null, selected: null };
  const box = h('div', { class: 'nr-ai' });
  const modelBox = h('div', { class: 'nr-ai-model' });
  const selInfo = h('div', { class: 'wi-hint nr-ai-sel' });
  const ta = h('textarea', { class: 'wi-field', rows: 3, spellcheck: 'false', placeholder: 'что сделать? например: добавь проверку, что аргумент не пустой' });
  const out = h('div', { class: 'nr-ai-out' });
  const foot = h('div', { class: 'wi-hint nr-ai-foot' });
  const sendBtn = h('button', { class: 'btn small primary', type: 'button', onclick: () => send() }, 'отправить');
  const stopBtn = h('button', { class: 'btn small', type: 'button', hidden: true, onclick: () => { if (st.abort) st.abort.abort(); } }, 'остановить');
  const modes = [['edit', 'изменить выделенное', 'модель перепишет выделенный фрагмент'], ['insert', 'вставить в курсор', 'модель напишет код для места курсора'], ['chat', 'спросить', 'вопрос по коду, ничего не меняется']];
  const seg = h('div', { class: 'wi-seg' }, modes.map(([id, label, tip]) => h('button', { type: 'button', class: id === st.mode ? 'on' : '', title: tip, onclick: () => { st.mode = id; seg.querySelectorAll('button').forEach((b, i) => b.classList.toggle('on', modes[i][0] === id)); updSel(); } }, label)));

  function updSel() {
    const [s, e] = ed.selection();
    const n = e - s;
    selInfo.textContent = st.mode === 'edit' ? (n ? `выделено ${nrNum(n)} симв. — их модель и изменит` : 'выделите фрагмент кода в редакторе') : st.mode === 'insert' ? `курсор: позиция ${s}` : (n ? `в контексте выделенное: ${nrNum(n)} симв.` : 'в контексте — весь файл (до 60 тыс. симв.)');
  }
  const poll = setInterval(() => { if (!box.isConnected) { clearInterval(poll); return; } if (!st.busy) updSel(); }, 500);

  async function loadModels() {
    try {
      const d = await api('/neuro/state');
      st.models = d.models.filter((m) => m.enabled !== false); st.selected = d.selected.ide;
    } catch (e) { st.models = []; }
    const sel = h('select', { class: 'wi-field' }, st.models.map((m) => h('option', { value: m.ref }, `${m.model} · ${m.conn_name}`)));
    if (st.selected) sel.value = st.selected;
    sel.addEventListener('change', () => { st.selected = sel.value; act(async () => { await api('/neuro/select', { surface: 'ide', ref: sel.value }); }); });
    modelBox.replaceChildren(st.models.length ? h('label', { class: 'wi-fld' }, h('span', { class: 'wi-lab' }, 'Модель'), sel)
      : h('div', { class: 'wi-note' }, 'Нет моделей. ', h('a', { href: '#/neuro', onclick: (e) => { if (GUARD && !GUARD()) e.preventDefault(); } }, 'Добавьте нейросеть'), ' (или запустите Ollama) на вкладке neuro.'));
  }

  function renderResult() {
    const r = st.result, req = st.req;
    if (!r) return;
    if (r.code !== undefined && !r.error && r.mode !== 'chat' && !r.problem) { const chk = checkApply(r, req); if (chk.problem) r.problem = chk.problem; }     // заранее: нельзя ли применить
    const parts = [];
    if (r.error) parts.push(h('div', { class: 'wi-err' }, r.error));
    if (r.mode === 'chat') parts.push(h('div', { class: 'nr-ai-text' }, r.text));
    else if (r.code !== undefined) {
      if (r.mode === 'edit') {
        const diff = nrDiff(req.selected, r.code);
        parts.push(h('div', { class: 'wi-diff nr-ai-diff' }, diff.map((d) => h('div', { class: 'wi-dl ' + ({ '+': 'add', '-': 'del' }[d.t] || '') }, h('span', { class: 'm' }, d.t === ' ' ? '' : d.t), h('span', {}, d.text || ' ')))));
      } else parts.push(h('div', { class: 'wi-diff nr-ai-diff' }, r.code.split('\n').map((l) => h('div', { class: 'wi-dl add' }, h('span', { class: 'm' }, '+'), h('span', {}, l || ' ')))));
      if (!r.fenced) parts.push(h('div', { class: 'wi-note' }, 'Модель не оформила код блоком ``` — проверьте, что в ответе только код.'));
      if (r.finish === 'length' || r.finish === 'max_tokens') parts.push(h('div', { class: 'wi-note' }, 'Ответ мог оборваться по длине — проверьте конец кода.'));
      if (r.problem) parts.push(h('div', { class: 'wi-err' }, r.problem));
      parts.push(h('div', { class: 'actions nr-ai-act' }, h('button', { class: 'btn small', type: 'button', onclick: () => { st.result = null; out.replaceChildren(); } }, 'отклонить'),
        h('button', { class: 'btn small primary', type: 'button', disabled: !!r.problem, onclick: () => apply() }, 'подтвердить изменения')));
    }
    out.replaceChildren(...parts);
  }

  function checkApply(r, req) {                                       // → {range:[s,e], text, problem}
    const cur = ed.value;
    if (r.mode === 'edit') {
      let s = req.s, e = req.e;
      if (cur.slice(s, e) !== req.selected) {
        const first = cur.indexOf(req.selected);
        if (first < 0 || cur.indexOf(req.selected, first + 1) >= 0) return { problem: 'Фрагмент в редакторе изменился, пока шёл ответ, — повторите запрос.' };
        s = first; e = first + req.selected.length;
      }
      let code = r.code;
      if (req.selected.endsWith('\n') && !code.endsWith('\n')) code += '\n';
      if (!req.selected.endsWith('\n') && code.endsWith('\n')) code = code.replace(/\n+$/, '');
      const next = cur.slice(0, s) + code + cur.slice(e);
      if (host.locked() && typeof wiProtSig === 'function' && wiProtSig(next) !== wiProtSig(cur)) return { problem: 'Ответ меняет защищённые строки шапки (NAME, MODULE_NAME, AUTHOR) — применить нельзя.' };
      return { range: [s, e], text: code };
    }
    const p = Math.min(req.s, cur.length);
    const next = cur.slice(0, p) + r.code + cur.slice(p);
    if (host.locked() && typeof wiProtSig === 'function' && wiProtSig(next) !== wiProtSig(cur)) return { problem: 'Вставка затрагивает защищённые строки шапки — применить нельзя.' };
    return { range: [p, p], text: r.code };
  }

  function apply() {
    const r = st.result;
    if (!r) return;
    const chk = checkApply(r, st.req);
    if (chk.problem) { r.problem = chk.problem; renderResult(); return; }
    const [s, e] = chk.range;
    ed.edit(s, e, chk.text, s, s + chk.text.length);
    ed.focus();
    toast('изменения применены — Ctrl+Z отменит', 'ok');
    st.result = null; out.replaceChildren();
  }

  async function send() {
    if (st.busy) return;
    const instruction = ta.value.trim();
    if (!instruction) { toast('напишите, что нужно сделать', 'error'); return; }
    const [s, e] = ed.selection(), v = ed.value;
    const selected = v.slice(s, e);
    if (st.mode === 'edit' && !selected.trim()) { toast('выделите фрагмент кода', 'error'); return; }
    const req = { mode: st.mode, s, e, selected };
    const body = { mode: st.mode, instruction, selected: st.mode === 'chat' && !selected.trim() ? v.slice(0, 60000) : selected, before: st.mode === 'chat' ? '' : v.slice(0, s), after: st.mode === 'chat' ? '' : v.slice(e), filename: host.filename(),
      history: st.mode === 'chat' ? st.history : [] };
    st.busy = true; st.req = req; st.result = null; st.abort = new AbortController();
    sendBtn.disabled = true; stopBtn.hidden = false; foot.textContent = '';
    const live = h('div', { class: 'nr-ai-text nr-live' }, '…');
    out.replaceChildren(live);
    let acc = '';
    try {
      await nrStream(body, (ev) => {
        if (ev.t === 'delta') { acc += ev.text; live.textContent = acc; live.scrollTop = live.scrollHeight; }
        else if (ev.t === 'error') { st.result = { mode: st.mode, error: ev.message }; }
        else if (ev.t === 'done') {
          st.result = { mode: st.mode, text: ev.text, code: ev.code, fenced: ev.fenced, finish: ev.finish };
          foot.textContent = nrPlanLine(ev.plan, ev.in + ev.out) + (ev.estimated ? ' (оценка)' : '');
          if (st.mode === 'chat') { st.history.push({ role: 'user', content: instruction }, { role: 'assistant', content: ev.text }); st.history = st.history.slice(-6); }
        }
      }, st.abort.signal);
      if (!st.result) st.result = { mode: st.mode, text: acc, code: undefined, error: acc ? null : 'Ответ не получен' };
    } catch (er) {
      st.result = er.name === 'AbortError' ? { mode: st.mode, error: 'Остановлено.' + (acc ? ' Частичный ответ ниже.' : ''), text: acc } : { mode: st.mode, error: er.message };
    } finally { st.busy = false; sendBtn.disabled = false; stopBtn.hidden = true; st.abort = null; }
    if (st.result && st.result.mode === 'chat' && st.result.error && st.result.text) renderResult(); else renderResult();
  }

  box.append(modelBox, seg, selInfo, ta, h('div', { class: 'nr-ai-btns' }, sendBtn, stopBtn), out, foot,
    h('div', { class: 'wi-hint' }, 'Помощник ничего не меняет сам: он покажет отличия, а изменения применятся только после «подтвердить» (Ctrl+Z отменит). Код уходит выбранному провайдеру.'));
  ta.addEventListener('keydown', (ev) => { if ((ev.ctrlKey || ev.metaKey) && ev.key === 'Enter') { ev.preventDefault(); send(); } });
  loadModels(); updSel();
  return box;
}
