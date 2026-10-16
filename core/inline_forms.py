"""
core/inline_forms.py

Полноценный "инлайн UI", которым управляет юзербот, но который физически
принадлежит стационарному боту (AIOgram 3) — только у бота есть право
редактировать текст/клавиатуру сообщения и ловить нажатия кнопок.

Идея протокола: юзербот программно повторяет то, что делает живой человек,
когда набирает "@botname query" в инлайн-режиме и тапает на результат:

  1. Core.create_inlineform() генерирует form_id и дёргает Telethon —
     GetInlineBotResultsRequest + SendInlineBotResultRequest с form_id
     в роли query/result id. Сообщение публикуется в чате как "via @bot".
  2. Это триггерит у бота apdейт inline_query (aiobot/bot.py отвечает
     ОДНИМ результатом-заглушкой с id, равным входящему query — т.е. эхо
     form_id обратно).
  3. Если у бота включён "Inline Feedback" в BotFather (/setinlinefeedback
     100%), Telegram присылает chosen_inline_result с inline_message_id —
     aiobot/bot.py зовёт core.resolve_pending_form(form_id, inline_message_id),
     это отпускает Future, который ждёт create_inlineform().
  4. create_inlineform() получает inline_message_id, оборачивает его в
     InlineForm, регистрирует в Core._active_forms и сразу применяет
     реальные text/buttons через form.edit(...).
  5. Клики по кнопкам приходят в aiobot/bot.py как callback_query с
     inline_message_id — он зовёт core.dispatch_inline_click(callback),
     тот находит форму и вызывает её on_click-хендлеры.

Разработчик модуля видит только:

    form = await engine.create_inlineform(event.chat_id, "Привет!",
                                           buttons=[[("Ок", "ok")]])

    @form.on_click(data='ok')
    async def _(click):
        await form.edit("Нажато!")
        await click.answer()

Форма — "живой" объект: не закрывается сама (кроме timeout=... или явного
form.close()), text/buttons можно менять сколько угодно раз.

--------------------------------------------------------------------------
InputButton ("Введите значение") — как в config Hikka Userbot
--------------------------------------------------------------------------

Обычная InlineKeyboardButton(callback_data=...) требует, чтобы пользователь
просто тапнул кнопку — Telegram присылает боту callback_query, и всё. Но
иногда нужно не выбрать вариант, а ВВЕСТИ произвольный текст (значение
конфига, число, ссылку...). Telegram Bot API не даёт открыть обычное поле
ввода по кнопке — зато есть switch_inline_query_current_chat: тап по такой
кнопке мгновенно переключает поле ввода пользователя в инлайн-режим ЭТОГО
ЖЕ бота, в ТЕКУЩЕМ чате, с текстом-подсказкой, предзаполненным в query
("@botname Enter value..."). Пользователь стирает/дополняет подсказку,
печатает своё значение и "отправляет" его, выбрав единственный инлайн-
результат — ровно как в create_inlineform(), только без callback_query.

Поскольку тут нет callback_query (нажатие кнопки — чисто клиентское
действие, сервер о нём не узнаёт), движок не может "среагировать в
моменте" — вместо этого он заранее знает, что кнопка есть, и заранее
регистрирует "ожидание ввода" для разрешённых пользователей в момент,
когда InputButton попадает в text/buttons формы (Core._register_input_targets(),
вызывается из InlineForm.edit() и сразу после создания формы). Дальше:

  1. Пользователь печатает "@botname <значение>" — Telegram шлёт боту
     inline_query на КАЖДОЕ нажатие клавиши. aiobot/bot.py проверяет
     core.get_pending_input(user_id) — если для этого юзера есть
     ожидание ввода, отвечает ОДНИМ результатом, который просто эхо
     набранного текста (живой предпросмотр того, что отправится).
  2. Когда пользователь выбирает этот результат (отправляет), Telegram
     шлёт chosen_inline_result с query == набранный текст — так же, как
     и подтверждение create_inlineform(), это требует включённого у бота
     /setinlinefeedback 100% (BotFather), иначе апдейт до бота не дойдёт.
  3. aiobot/bot.py зовёт core.dispatch_inline_input(chosen) — тот находит
     форму по user_id, собирает InlineInput(value=chosen.query, ...) и
     вызывает form.on_input(...)-хендлеры.
  4. Само "эхо"-сообщение (та самая реплика "via @bot" с введённым
     текстом, которую Telegram-клиент только что опубликовал в чат) —
     движок тут же его удаляет, до того как оно попадёт вотчерам/командам
     модулей. Это сообщение прилетает через MTProto (Core._on_new_message,
     telethon), а chosen_inline_result из шага 2 — отдельным каналом через
     Bot API; порядок доставки между ними НЕ гарантирован (Bot API обычно
     медленнее), поэтому Core._register_input_echo_for_deletion() (из шага 3)
     и Core._handle_bot_echo_message() (из _on_new_message) сверяются друг
     с другом в обе стороны — какой бы канал ни оказался первым, совпадение
     всё равно найдётся и сообщение будет удалено.

Разработчик модуля видит только (InputButton доступен как engine.InputButton,
см. Core.InputButton в core/engine.py — отдельно этот файл импортировать не нужно):

    form = await engine.create_inlineform(event.chat_id, "Введите имя:",
                                           buttons=[[engine.InputButton("✏️ Ввести", "name")]])

    @form.on_input(data='name')
    async def _(inp):
        await form.edit(f"Привет, {inp.value}!", buttons=None)

См. также README.md, раздел «Инлайн-формы» → InputButton, и modules/builtin/Loader.py (превью с кнопками).

--------------------------------------------------------------------------
Пагинация ("страницы")
--------------------------------------------------------------------------

Telegram не даёт повесить под сообщение бесконечную инлайн-клавиатуру —
практический потолок около MAX_BUTTONS_PER_PAGE кнопок на форму. Если
buttons, переданные в create_inlineform()/form.edit(), суммарно превышают
этот потолок, движок сам режет их на "страницы" (paginate_button_rows
ниже): ряды (buttons всегда список РЯДОВ, см. Core._build_markup) не
разрезаются — каждый ряд целиком уходит на какую-то одну страницу. На
каждой странице, если их больше одной, снизу появляется служебный ряд
навигации — ◀ (если это не первая страница), "N/M" (просто индикатор,
неактивная кнопка) и ▶ (если не последняя). Клики по ◀/▶ обрабатывает сам
движок (Core.dispatch_inline_click) — они НЕ доходят до form.on_click(),
просто переключают форму на другую страницу тем же form.edit()-подобным
редактированием сообщения. Разработчик модуля вообще не думает о
страницах — просто передаёт buttons любого размера, как и раньше; если
их <= MAX_BUTTONS_PER_PAGE — одна страница, без ряда навигации.
"""

import asyncio
import time


# --------------------------------------------------------------------------
# Пагинация кнопок ("страницы") — см. докстринг модуля выше
# --------------------------------------------------------------------------

MAX_BUTTONS_PER_PAGE = 100   # практический потолок Telegram на инлайн-клавиатуру
NAV_ROW_RESERVE = 3          # запас под служебный ряд навигации: ◀, "N/M", ▶

PAGE_PREV = "__frxpage_prev__"    # служебные callback_data — см. Core.dispatch_inline_click
PAGE_NEXT = "__frxpage_next__"
PAGE_NOOP = "__frxpage_noop__"    # кнопка-индикатор "N/M" — клик ничего не делает

# тонкие символы вместо эмодзи-стрелок (см. core/visual.py: back ◂ / next ▸)
PAGE_PREV_LABEL = "◂"
PAGE_NEXT_LABEL = "▸"


def normalize_button_rows(buttons):
    """
    Приводит buttons к списку РЯДОВ (список списков кнопок) — те же
    правила, что и у Core._build_markup() (см. его докстринг про
    поддерживаемые форматы): либо уже список рядов, либо один ряд без
    внешней обёртки (шорткат). Вынесено сюда (а не только в Core), чтобы
    пагинация ниже могла работать сама по себе, без круговой зависимости
    от core/engine.py.
    """
    if not buttons:
        return []
    first = buttons[0]
    is_flat_single_row = isinstance(first, (InputButton, dict)) or (
        isinstance(first, (list, tuple)) and len(first) in (2, 3)
        and isinstance(first[0], str) and isinstance(first[1], str)
    )
    return [list(buttons)] if is_flat_single_row else [list(row) for row in buttons]


def paginate_button_rows(buttons, max_per_page=MAX_BUTTONS_PER_PAGE):
    """
    Разбивает buttons на "страницы" — рядов внутри страницы не разрезает,
    просто набирает ряды в текущую страницу, пока не упрётся в лимит.
    Если суммарное число кнопок <= max_per_page — пагинация не нужна,
    возвращается [rows] (одна страница) — поведение как до Возвращает (pages, total_pages): pages — список страниц, каждая
    страница — список рядов БЕЗ служебного ряда навигации (тот
    добавляется отдельно, см. render_page_rows).
    """
    rows = normalize_button_rows(buttons)
    total = sum(len(row) for row in rows)
    if total <= max_per_page:
        return [rows], 1

    capacity = max(max_per_page - NAV_ROW_RESERVE, 1)
    pages = []
    current, current_count = [], 0
    for row in rows:
        rlen = len(row)
        if current and current_count + rlen > capacity:
            pages.append(current)
            current, current_count = [], 0
        current.append(row)
        current_count += rlen
        if current_count >= capacity:
            pages.append(current)
            current, current_count = [], 0
    if current:
        pages.append(current)
    return pages, len(pages)


def render_page_rows(pages, page_index, total_pages):
    """
    Содержимое одной конкретной страницы (список рядов) + служебный ряд
    навигации в конце, если страниц больше одной. Именно это в итоге
    уходит в Core._build_markup()/_inline_edit().
    """
    rows = [list(row) for row in pages[page_index]] if pages else []
    if total_pages > 1:
        nav = []
        if page_index > 0:
            nav.append((PAGE_PREV_LABEL, PAGE_PREV))
        nav.append((f"{page_index + 1}/{total_pages}", PAGE_NOOP))
        if page_index < total_pages - 1:
            nav.append((PAGE_NEXT_LABEL, PAGE_NEXT))
        rows.append(nav)
    return rows


# --------------------------------------------------------------------------
# Постраничный ВЫБОР из списка — engine.picker_page()
# --------------------------------------------------------------------------

PICKER_PAGE_SIZE = 7   # сколько кнопок-вариантов на одной странице выбора


def picker_page(items, page=0, per_page=PICKER_PAGE_SIZE, page_callback="pick_page:", search=None):
    """
    Одна страница списка вариантов для form.edit(): по кнопке в ряду, максимум
    per_page штук, и снизу ряд навигации ◀️ / "N/M" / ▶️ (только если страниц
    больше одной). В отличие от автопагинации формы (paginate_button_rows, 100
    кнопок), эта нарезка — осознанная, для меню "выберите пункт" (.modules,
    .restoremods): навигацию обрабатывает САМ модуль через
    @form.on_click(data_startswith=page_callback) — в callback_data ◀️/▶️
    зашит номер целевой страницы (f"{page_callback}{N}").

    items — список готовых кнопок (text, callback_data). page — номер страницы
    с нуля (за границами — зажимается). Возвращает (rows, page, total_pages);
    rows — список рядов, к нему можно дописать свои ряды (например "Назад").
    search — кнопка SearchButton (или ряд кнопок): ставится первым рядом над вариантами.
    """
    items = list(items)
    total = max(1, -(-len(items) // per_page))
    page = min(max(int(page), 0), total - 1)
    rows = [[btn] for btn in items[page * per_page:(page + 1) * per_page]]
    if total > 1:
        nav = []
        if page > 0:
            nav.append((PAGE_PREV_LABEL, f"{page_callback}{page - 1}"))
        nav.append((f"{page + 1}/{total}", PAGE_NOOP))
        if page < total - 1:
            nav.append((PAGE_NEXT_LABEL, f"{page_callback}{page + 1}"))
        rows.append(nav)
    if search is not None:
        rows.insert(0, list(search) if isinstance(search, (list, tuple)) else [search])
    return rows, page, total


# --------------------------------------------------------------------------
# Цвет кнопок (Bot API: InlineKeyboardButton.style)
# --------------------------------------------------------------------------

BUTTON_STYLES = {
    'primary': 'primary', 'blue': 'primary', 'синий': 'primary', 'синяя': 'primary',
    'success': 'success', 'green': 'success', 'ok': 'success', 'зелёный': 'success', 'зеленый': 'success', 'зелёная': 'success',
    'danger': 'danger', 'red': 'danger', 'error': 'danger', 'красный': 'danger', 'красная': 'danger',
}


def normalize_style(style):
    """'primary' | 'success' | 'danger' (и синонимы: blue/green/red, по-русски) -> значение Bot API; иначе None."""
    if not style:
        return None
    norm = BUTTON_STYLES.get(str(style).strip().lower())
    if norm is None:
        raise ValueError(f"Неизвестный цвет кнопки {style!r}: доступны 'primary' (синий), "
                         f"'success' (зелёный), 'danger' (красный)")
    return norm


def parse_button(btn, styles=None):
    """
    Одна кнопка в любом поддерживаемом виде -> dict:
        {'kind': 'input'|'url'|'callback', 'text', 'data', 'style', 'hint'?, 'url'?}
    Форматы: (text, data) · (text, data, style) · {'text', 'data', 'style'} · InputButton(...).
    styles — {callback_data: style} формы; цвет у самой кнопки важнее.
    Возвращает None, если формат не распознан.
    """
    styles = styles or {}
    if isinstance(btn, InputButton):
        return {'kind': 'input', 'text': str(btn.text), 'data': str(btn.data), 'hint': btn.hint, 'prefill': btn.prefill,
                'style': btn.style or styles.get(str(btn.data))}
    if isinstance(btn, dict):
        text, data, style = btn.get('text'), btn.get('data', btn.get('callback_data')), btn.get('style')
        if btn.get('url'):
            data = 'url:' + str(btn['url'])
    elif isinstance(btn, (list, tuple)) and len(btn) in (2, 3):
        text, data = btn[0], btn[1]
        style = btn[2] if len(btn) == 3 else None
    else:
        return None
    if text is None or data is None:
        return None
    data = str(data)
    style = normalize_style(style) if style else styles.get(data)
    if data.startswith(('url:http://', 'url:https://', 'url:tg://')):
        return {'kind': 'url', 'text': str(text), 'data': data, 'url': data[4:], 'style': style}
    return {'kind': 'callback', 'text': str(text), 'data': data, 'style': style}


class TokenBucket:
    """Простой async token bucket — ограничивает N операций в секунду."""

    def __init__(self, rate: float = 5):
        self.rate = max(float(rate), 0.001)
        self._tokens = self.rate
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def set_rate(self, rate: float):
        self.rate = max(float(rate), 0.001)

    async def acquire(self):
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._updated
                self._updated = now
                self._tokens = min(self.rate, self._tokens + elapsed * self.rate)
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class InputButton:
    """
    Кнопка "Введите значение" (см. докстринг модуля выше) — кладётся в
    buttons= наравне с обычными (text, callback_data) кортежами:

        buttons=[[("Ок", "ok")], [InputButton("✏️ Значение", "myfield")]]

    text     — подпись кнопки.
    data     — ключ поля, фильтр в form.on_input(data=...).
    hint     — подсказка: показывается в строке над клавиатурой, пока
               пользователь набирает значение (поле ввода при этом ПУСТОЕ —
               стирать ничего не нужно). По умолчанию "Введите значение".
    prefill  — текст, которым всё же нужно предзаполнить поле
               ("@botname <prefill>"), например текущее значение для правки.
               По умолчанию пусто.
    for_user — переопределяет form.for_user именно для этой кнопки (кто
               может вводить значение); None (по умолчанию) — наследуется
               из формы. Правила ровно те же, что и у for_user формы,
               КРОМЕ отрицательных id (целые чаты) — тут они не
               поддерживаются, ожидание ввода всегда привязывается к
               конкретному user_id.
    style    — цвет кнопки: 'primary' (синий), 'success' (зелёный),
               'danger' (красный); None — обычная.
    """
    __slots__ = ('text', 'data', 'hint', 'for_user', 'style', 'prefill')

    def __init__(self, text, data, hint="Введите значение", for_user=None, style=None, prefill=""):
        self.text = text
        self.data = data
        self.hint = hint
        self.prefill = prefill or ""
        self.for_user = for_user
        self.style = normalize_style(style)   # цвет кнопки: 'primary' | 'success' | 'danger'


class SearchButton(InputButton):
    """
    Кнопка «🔍 Поиск» для длинных списков — та же InputButton (тап открывает «@бот …», пользователь набирает
    запрос и отправляет), но со своими значениями по умолчанию и своим хендлером:

        buttons = [[engine.SearchButton()], *rows]          # подпись «🔍 Поиск», data='search'

        @form.on_search()                                   # = form.on_input(data='search')
        async def _(inp):
            found = engine.search_items(items, inp.value, key=lambda it: it['name'])
            ...

    text / data / hint / style / prefill / for_user — как у InputButton.
    """
    __slots__ = ()

    def __init__(self, text="🔍 Поиск", data="search", hint="что найти: название, команда или слово",
                 for_user=None, style="primary", prefill=""):
        super().__init__(text, data, hint=hint, for_user=for_user, style=style, prefill=prefill)


def _norm_search(text) -> str:
    return str(text or '').lower().replace('ё', 'е').strip()


def search_items(items, query, key=None, limit=None, fuzzy=True):
    """
    Поиск по списку для кнопки SearchButton (и вообще): возвращает элементы items, подходящие под query,
    лучшие — первыми: точное совпадение → начинается с запроса → слово начинается с запроса → содержит
    запрос → (если ничего) похожее по написанию. Без учёта регистра и разницы «е/ё».

    key(item) -> строка или список строк, по которым искать (по умолчанию str(item)).
    Пустой query — все items как есть. limit — не больше стольких результатов.
    """
    import difflib
    items = list(items)
    q = _norm_search(query)
    if not q:
        return items[:limit] if limit else items
    words_q = q.split()

    def texts(it):
        v = key(it) if key else it
        if isinstance(v, (list, tuple, set)):
            return [_norm_search(x) for x in v if x is not None]
        return [_norm_search(v)]

    ranked = []
    for idx, it in enumerate(items):
        best = None
        for t in texts(it):
            if not t:
                continue
            if t == q:
                score = 0
            elif t.startswith(q):
                score = 1
            elif any(w.startswith(q) for w in t.replace('_', ' ').replace('.', ' ').split()):
                score = 2
            elif q in t or all(w in t for w in words_q):
                score = 3
            else:
                continue
            best = score if best is None else min(best, score)
        if best is not None:
            ranked.append((best, idx, it))
    if not ranked and fuzzy:
        for idx, it in enumerate(items):
            words = [w for t in texts(it) for w in [t] + t.replace('_', ' ').split()]
            if difflib.get_close_matches(q, words, n=1, cutoff=0.72):
                ranked.append((4, idx, it))
    ranked.sort(key=lambda r: (r[0], r[1]))
    out = [it for _s, _i, it in ranked]
    return out[:limit] if limit else out


class InlineInput:
    """Один "ввод значения" через InputButton, передаётся в form.on_input()-хендлеры."""

    def __init__(self, data, value, from_user, chat_id, form):
        self.data = data              # ключ поля (InputButton.data)
        self.value = value            # текст, который реально набрал и отправил пользователь
        self.from_user = from_user    # telethon.types.User | None
        self.chat_id = chat_id
        self.form = form              # InlineForm, к которой относится этот ввод


class InlineClick:
    """Один клик по кнопке инлайн-формы, передаётся в form.on_click()-хендлеры."""

    def __init__(self, data, from_user, chat_id, inline_message_id, raw_callback):
        self.data = data                          # callback_data нажатой кнопки
        self.from_user = from_user                # telethon.types.User | None
        self.chat_id = chat_id
        self.inline_message_id = inline_message_id
        self._raw = raw_callback                  # исходный aiogram CallbackQuery
        self._answered = False

    async def answer(self, text=None, show_alert=False):
        """Снимает "часики" у кнопки в клиенте пользователя. Идемпотентно."""
        if self._answered:
            return
        self._answered = True
        try:
            await self._raw.answer(text=text, show_alert=show_alert)
        except Exception:
            pass  # callback мог протухнуть (>~30 сек) — не критично


def resolve_form_reply(event, chat_id, reply_to=None):
    """
    на какое сообщение отвечает форма. Если команду юзербота отправили ОТВЕТОМ на какое-то сообщение,
    форма тоже уходит ответом на него (reply_to=None — автоматически). reply_to=False/0 — без ответа, int — на это сообщение.
    Автоматика работает только для собственных сообщений-команд (event.out) в том же чате.
    """
    if reply_to is False or reply_to == 0:
        return None
    if isinstance(reply_to, int) and not isinstance(reply_to, bool):
        return reply_to
    try:
        msg = getattr(event, 'message', None)
        if msg is None or not getattr(event, 'out', False):
            return None
        if getattr(event, 'chat_id', None) != chat_id:
            return None
        rid = getattr(getattr(msg, 'reply_to', None), 'reply_to_msg_id', None)
        return rid if isinstance(rid, int) and rid > 0 else None
    except Exception:
        return None


class InlineForm:
    """
    Живая инлайн-форма. НЕ закрывается сама после клика — модуль решает,
    когда вызвать close() (кроме случая timeout=... в create_inlineform,
    тогда форма закрывается сама при простое).
    """

    def __init__(self, core, form_id, inline_message_id, chat_id,
                 owner_module, for_user, black_list, timeout):
        self._core = core
        self.form_id = form_id
        self.inline_message_id = inline_message_id
        self.chat_id = chat_id
        self.owner_module = owner_module
        self.for_user = for_user
        self.black_list = black_list
        self.timeout = timeout

        self._click_handlers = []      # [(data_filter | None, data_startswith_filter | None, func), ...]
        self._input_handlers = []      # [(data_filter | None, func), ...] — InputButton, см. on_input()
        self._closed = False
        self._last_activity = time.monotonic()
        self._idle_task = asyncio.create_task(self._idle_watch()) if timeout else None

        # --- пагинация (см. докстринг модуля выше) ---
        self.text = None               # текущий текст формы (нужен для _go_to_page — там текст не меняется)
        self._raw_buttons = None       # buttons, как их передал разработчик (ДО разбивки на страницы)
        self._pages = [[]]             # [[ряды страницы 0], [ряды страницы 1], ...] — без служебного ряда навигации
        self.page = 0                  # индекс текущей отображаемой страницы
        self.message_id = None         # id сообщения формы в чате (для form.delete())
        self.media = None              # {'kind', 'file_id'}, если форма с картинкой/анимацией (текст — подпись)
        self.styles = {}               # {callback_data: 'primary'|'success'|'danger'} — цвет кнопок (create_inlineform(styles=...))
        self._media_task = None        # загрузка настоящего медиа вместо заглушки «загрузка…» (Core._swap_form_media)

    # ------------------------------------------------------------------ #
    # Публичный API для модуля
    # ------------------------------------------------------------------ #

    def on_click(self, data=None, data_startswith=None):
        """
        @form.on_click()                     — ловит ЛЮБОЙ клик по форме.
        @form.on_click(data='x')             — только клики с callback_data == 'x'.
        @form.on_click(data_startswith='x')  — клики, чей callback_data начинается
            с 'x' — удобно, когда в data зашита динамическая часть,
            например callback_data=f'del_{item_id}' -> data_startswith='del_'.
        data и data_startswith взаимоисключающие: задайте только один из них
        (или ни одного — тогда ловится любой клик).
        Можно вешать сколько угодно раз подряд, в т.ч. с разными data/data_startswith.
        Функция вызывается с аргументом click (InlineClick).
        """
        if data is not None and data_startswith is not None:
            raise ValueError(
                "form.on_click(): укажите либо data=, либо data_startswith=, не оба сразу"
            )

        def deco(func):
            self._click_handlers.append((data, data_startswith, func))
            return func
        return deco

    def on_search(self, data='search'):
        """
        @form.on_search() — запрос из кнопки SearchButton (data='search' по умолчанию). То же самое, что
        form.on_input(data=...): функция получает inp (InlineInput), запрос — inp.value.
        """
        return self.on_input(data=data)

    def on_input(self, data=None):
        """
        @form.on_input()          — ловит ЛЮБОЙ ввод через InputButton формы.
        @form.on_input(data='x')  — только значения от InputButton(data='x').
        Функция вызывается с аргументом inp (InlineInput). См. докстринг
        модуля выше и InputButton.
        """
        def deco(func):
            self._input_handlers.append((data, func))
            return func
        return deco

    async def edit(self, text, buttons=None, styles=None):
        """
        Переписывает текст и/или клавиатуру формы. buttons=None — без кнопок.
        Если buttons содержит InputButton(...) — заново регистрирует
        "ожидание ввода" для разрешённых пользователей (см.
        Core._register_input_targets); если InputButton в новых buttons
        больше нет — старое ожидание снимается автоматически.

        Если buttons суммарно не помещаются в одну страницу (см. докстринг
        модуля выше, MAX_BUTTONS_PER_PAGE) — движок сам режет их на
        страницы и показывает первую (page=0), добавляя служебный ряд
        навигации ◀/N/M/▶. Работает прозрачно — если кнопок немного,
        поведение не отличается от версий до

        styles — цвет кнопок по их callback_data: {'del': 'danger', 'ok': 'success'};
        None — оставить как было (при create_inlineform(styles=...)). Цвет можно задать
        и у самой кнопки третьим элементом: ("Удалить", "del", "danger")."""
        if self._closed:
            raise RuntimeError(f"Форма '{self.form_id}' уже закрыта (form.close())")
        if styles is not None:
            self.styles = {str(k): normalize_style(v) for k, v in dict(styles).items()}
        pages, total_pages = paginate_button_rows(buttons or [])
        rendered = render_page_rows(pages, 0, total_pages)
        await self._core._inline_edit(self.inline_message_id, text, rendered, self.media, styles=self.styles)
        self.text = text
        self._raw_buttons = buttons
        self._pages = pages
        self.page = 0
        self._core._register_input_targets(self, pages[0] if pages else [])

    def _init_state(self, text, raw_buttons, pages):
        """
        Заполняет состояние страниц сразу после создания формы —
        content/markup уже опубликованы (ответом на inline_query, см.
        Core.create_inlineform()), поэтому тут нет лишнего сетевого
        вызова, только регистрация "ожидания ввода" (InputButton) для
        видимой (нулевой) страницы.
        """
        self.text = text
        self._raw_buttons = raw_buttons
        self._pages = pages
        self.page = 0
        self._core._register_input_targets(self, pages[0] if pages else [])

    async def _go_to_page(self, new_page):
        """
        Переключает форму на другую страницу (см. Core.dispatch_inline_click
        — вызывается на клик по служебным кнопкам PAGE_PREV/PAGE_NEXT).
        Текст не трогаем (только клавиатуру) — дешевле одного edit_message_text.
        """
        total_pages = len(self._pages)
        if not (0 <= new_page < total_pages):
            return
        self.page = new_page
        rendered = render_page_rows(self._pages, new_page, total_pages)
        await self._core._inline_edit(self.inline_message_id, None, rendered, styles=self.styles)
        self._core._register_input_targets(self, self._pages[new_page])

    async def delete(self):
        """
        Закрывает форму и УДАЛЯЕТ её сообщение из чата целиком — это то, что
        ждут от кнопки «Закрыть». Если удалить не вышло (нет прав, сообщение
        старше 48 ч в группе без прав админа), сообщение просто остаётся без
        кнопок. Безопасно вызывать повторно.
        """
        was_closed = self._closed
        await self.close(remove_buttons=False)
        if getattr(self, '_deleted', False):
            return
        self._deleted = True
        msg_id = getattr(self, 'message_id', None)
        if msg_id:
            try:
                await self._core.client.delete_messages(self.chat_id, [msg_id], revoke=True)
                return
            except Exception:
                pass
        if not was_closed:
            try:
                await self._core._inline_edit(self.inline_message_id, None, [])
            except Exception:
                pass

    async def close(self, remove_buttons=True):
        """
        Останавливает обработку кликов и вводов по форме (сообщение НЕ
        удаляется — для этого form.delete()). remove_buttons=True —
        дополнительно снимает клавиатуру. Безопасно вызывать повторно.
        """
        if self._closed:
            return
        self._closed = True
        if self._idle_task is not None:
            self._idle_task.cancel()
        self._core._active_forms.pop(self.inline_message_id, None)
        self._core._clear_input_targets_for_form(self)
        if remove_buttons:
            try:
                await self._core._inline_edit(self.inline_message_id, None, [])
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # Внутреннее
    # ------------------------------------------------------------------ #

    async def _dispatch(self, click: InlineClick):
        self._last_activity = time.monotonic()
        matched = False
        for data_filter, data_startswith_filter, func in list(self._click_handlers):
            if data_filter is not None and data_filter != click.data:
                continue
            if data_startswith_filter is not None and not (click.data or '').startswith(data_startswith_filter):
                continue
            matched = True
            await self._core._safe_call(self.owner_module, func, click)
        if not matched and not click._answered:
            await click.answer()

    async def _dispatch_input(self, inp: InlineInput):
        self._last_activity = time.monotonic()
        for data_filter, func in list(self._input_handlers):
            if data_filter is not None and data_filter != inp.data:
                continue
            await self._core._safe_call(self.owner_module, func, inp)

    async def _idle_watch(self):
        try:
            while not self._closed:
                await asyncio.sleep(self.timeout)
                if time.monotonic() - self._last_activity >= self.timeout:
                    await self.close()
                    return
        except asyncio.CancelledError:
            pass
