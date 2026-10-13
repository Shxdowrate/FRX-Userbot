"""
modules/builtin/Changelog.py

Журнал обновлений FRX и уведомление «что нового» после обновления.

1. Файл CHANGELOG.md (в корне проекта, рядом с README.md) — журнал всех обновлений.
   Формат: заголовок второго уровня на каждую версию, под ним — обычный Markdown
   (пункты `- ...`, `**жирный**`, `` `код` ``, ссылки):

        ## 1.0.1 — 2026-10-20
        - что изменилось
        - ещё что-то

2. Первый запуск после обновления. При старте стационарного бота модуль сравнивает
   версию движка (engine.get_version() = FRX_VERSION) с последней версией, о которой
   уже сообщал (ключ БД last_version). Если версия изменилась — присылает ботом в ЛС
   владельцу описание обновления из CHANGELOG.md. Смотрит именно на ВЕРСИЮ ЮБ, поэтому
   работает и когда файлы обновили вручную (распаковали поверх), и после .update.
     - пропущено несколько версий (1.0.0 -> 1.0.3) — приходят записи всех промежуточных;
     - самый первый запуск (last_version ещё нет) — приходит запись текущей версии;
     - откат на более старую версию — молча запоминаем версию, ничего не шлём;
     - отправка не удалась (бот недоступен и т. п.) — версия НЕ запоминается, попытка
       повторится при следующем запуске;
     - записи о версии нет — приходит короткое уведомление, что описание не найдено.
   Если CHANGELOG.md рядом нет, записи берутся из раздела «## Changelog» файла README.md.

3. Команда .changelog / .cl [версия | list] — инлайн-форма: по умолчанию показывает
   запись о ПОСЛЕДНЕЙ версии в журнале, кнопка «▸ Перейти к версии…» открывает
   постраничный выбор (до 10 версий на странице, engine.picker_page) — выбор
   любой версии открывает её запись, с той же кнопкой, чтобы перейти к другой.
   `.changelog <версия>` сразу открывает форму на записи этой версии;
   `.changelog list` — текстовый список всех версий. Без стационарного бота —
   запись текущей/указанной версии текстом, при необходимости несколькими сообщениями.

Конфиг (engine.create_config, см. .config changelog):
    notify_update — bool. Присылать ли описание обновления в ЛС после обновления.
Куда шлём: log_chat_id модуля Logging (0 — ЛС владельца), как и остальные служебные
сообщения бота; Logging.enabled на это уведомление НЕ влияет.
"""

import html
import re
from pathlib import Path

import engine
import decorators
import bot
import db
import visual

NAME = "Changelog"
MODULE_NAME = "changelog"
VERSION = "1.2"
AUTHOR = "FRX"
DESCRIPTION = "Журнал обновлений (CHANGELOG.md): описание обновления в ЛС при первом запуске новой версии и команда .changelog"

CHANGELOG_PATH = Path('CHANGELOG.md')
README_PATH = Path('README.md')

_SAFE_LIMIT = 3800       # запас до лимита Telegram (4096) на одно сообщение
_MAX_MESSAGES = 6        # больше сообщений за раз не шлём — остальное «см. .changelog»

_HEADER_RE = re.compile(r'^(#{2,3})\s+(?:Update\s+|v)?(\d+(?:\.\d+)+)\b\s*(.*)$', re.IGNORECASE)


# ------------------------------------------------------------------ #
# Разбор журнала
# ------------------------------------------------------------------ #

def _vt(version):
    """'1.0.12' -> (1, 0, 12) — для сравнения версий (1.0.9 < 1.0.12)."""
    nums = re.findall(r'\d+', str(version))
    return tuple(int(n) for n in nums) if nums else (0,)


def _parse(text):
    """[{'version': '1.0.0', 'title': '— 2026-10-08', 'lines': [...]}, ...] в порядке появления в тексте."""
    entries, cur, level = [], None, 0
    for line in text.splitlines():
        m = _HEADER_RE.match(line)
        if m:
            if cur:
                entries.append(cur)
            level = len(m.group(1))
            cur = {'version': m.group(2), 'title': m.group(3).strip(' \t—–-'), 'lines': []}
            continue
        h = re.match(r'^(#{1,3})\s', line)
        if h and cur is not None and len(h.group(1)) <= level:
            entries.append(cur)                      # начался другой раздел — запись закончилась
            cur = None
            continue
        if cur is not None:
            cur['lines'].append(line)
    if cur:
        entries.append(cur)
    return entries


def _load_entries():
    """(записи, источник). Сначала CHANGELOG.md, при его отсутствии — раздел «## Changelog» из README.md."""
    try:
        if CHANGELOG_PATH.exists():
            entries = _parse(CHANGELOG_PATH.read_text(encoding='utf-8'))
            if entries:
                return entries, CHANGELOG_PATH.name
    except OSError:
        pass
    try:
        if README_PATH.exists():
            text = README_PATH.read_text(encoding='utf-8')
            m = re.search(r'^##\s+Changelog\s*$', text, re.MULTILINE)
            if m:
                entries = _parse(text[m.end():])
                if entries:
                    return entries, README_PATH.name
    except OSError:
        pass
    return [], None


# ------------------------------------------------------------------ #
# Markdown -> HTML Telegram
# ------------------------------------------------------------------ #

def _inline(text):
    text = html.escape(text, quote=False)
    codes = []

    def stash(m):
        codes.append(m.group(1))
        return f"\x00{len(codes) - 1}\x00"

    text = re.sub(r'`([^`\n]+)`', stash, text)
    text = re.sub(r'\*\*([^*\n]+)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\[([^\]\n]+)\]\((https?://[^)\s]+)\)', r'<a href="\2">\1</a>', text)
    return re.sub(r'\x00(\d+)\x00', lambda m: f"<code>{codes[int(m.group(1))]}</code>", text)


def _md_to_html(lines):
    out, in_code, code_buf = [], False, []
    for raw in lines:
        line = raw.rstrip()
        if line.strip().startswith('```'):
            if in_code:
                out.append("<pre>" + html.escape("\n".join(code_buf), quote=False) + "</pre>")
                code_buf, in_code = [], False
            else:
                in_code = True
            continue
        if in_code:
            code_buf.append(raw)
            continue
        if not line.strip():
            if out and out[-1] != "":
                out.append("")
            continue
        m = re.match(r'^(\s*)[-*]\s+(.*)$', line)
        if m:
            depth = len(m.group(1).replace('\t', '    ')) // 2
            out.append(visual.item(_inline(m.group(2)), depth))
            continue
        h = re.match(r'^#{3,6}\s+(.*)$', line)
        if h:
            head = _inline(h.group(1))
            # капс раздела не должен менять код/ссылки внутри заголовка — тогда только метка раздела
            out.append(visual.section(head) if '<' not in head else f"{visual.visual('section')} <b>{head}</b>")
            continue
        out.append(_inline(line.strip() if not line.startswith((' ', '\t')) else line))
    if in_code and code_buf:
        out.append("<pre>" + html.escape("\n".join(code_buf), quote=False) + "</pre>")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _render(entry, nested=False):
    """nested=True — запись внутри уведомления об обновлении (там уже есть заголовок): версия — разделом."""
    version = html.escape(entry['version'])
    sub = html.escape(entry['title']) if entry['title'] else None
    if nested:
        head = visual.section(f"Версия {version}") + (f"  {visual.hint(sub)}" if sub else "")
    else:
        head = visual.title(f"Версия {version}", sub)
    return f"{head}\n{_md_to_html(entry['lines'])}".rstrip()


def _pack(blocks, first_header=None):
    """Раскладывает блоки текста по сообщениям (до _SAFE_LIMIT символов, режет по строкам)."""
    messages, cur = [], first_header or ""
    for block in blocks:
        for line in block.split("\n"):
            add = ("\n" if cur else "") + line
            if len(cur) + len(add) > _SAFE_LIMIT and cur:
                messages.append(cur)
                cur = line
            else:
                cur += add
        cur += "\n"
    if cur.strip():
        messages.append(cur.rstrip())
    if len(messages) > _MAX_MESSAGES:
        messages = messages[:_MAX_MESSAGES]
        messages[-1] += "\n\n" + visual.hint('… продолжение — <code>.changelog</code>')
    return messages


# ------------------------------------------------------------------ #
# Конфиг
# ------------------------------------------------------------------ #

@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='notify_update',
        description='Присылать в ЛС описание обновления при первом запуске после обновления FRX',
        data_type='bool', default=True,
    )


# ------------------------------------------------------------------ #
# Уведомление после обновления
# ------------------------------------------------------------------ #

async def _target_chat_id():
    try:
        chat_id = await engine.get_config('log_chat_id', module_name='logging')
    except Exception:
        chat_id = 0
    return chat_id if chat_id else engine._owner_id


# Намеренно @bot.on_start(), а не @decorators.on_start(): описание уходит ботом в ЛС,
# а бот к моменту обычного on_start ещё не поднят (см. пояснение в modules/builtin/Logging.py).
@bot.on_start()
async def notify_update():
    """Первый запуск после обновления (версия ЮБ изменилась) — шлёт описание обновления ботом в ЛС."""
    current = engine.get_version()
    last = await db.get('last_version')
    if last == current:
        return

    if last and _vt(current) < _vt(last):        # откат на старую версию — ничего не сообщаем
        await db.set('last_version', current)
        return

    if not await engine.get_config('notify_update'):
        await db.set('last_version', current)
        return

    entries, source = _load_entries()
    if last:
        chosen = [e for e in entries if _vt(last) < _vt(e['version']) <= _vt(current)]
        header = visual.title('FRX обновлён', f"{html.escape(str(last))} → {html.escape(current)}")
    else:
        chosen = [e for e in entries if _vt(e['version']) == _vt(current)]
        header = visual.title(f"Что нового в FRX {html.escape(current)}")
    chosen.sort(key=lambda e: _vt(e['version']))

    if chosen:
        messages = _pack([_render(e, nested=True) for e in chosen], first_header=header + "\n")
    else:
        messages = [
            f"{header}\n"
            + visual.warn(f"Описание версии <code>{html.escape(current)}</code> не найдено") + "\n"
            + visual.hint(
                "в проекте нет файла <code>CHANGELOG.md</code> или в нём нет записи об этой версии. "
                "Положите актуальный <code>CHANGELOG.md</code> рядом с <code>main.py</code> — "
                "после этого его покажет <code>.changelog</code>"
            )
        ]

    try:
        chat_id = await _target_chat_id()
        for text in messages:
            await engine.bot.send_message(chat_id, text)
    except Exception as e:
        engine.tlog(
            f"{visual.visual('status_warning')} [Changelog] не удалось отправить описание обновления в ЛС: {e} "
            f"(повторю при следующем запуске)"
        )
        return

    await db.set('last_version', current)
    engine.tlog(f"{visual.visual('status_success')} [Changelog] описание обновления {current} отправлено в ЛС")


# ------------------------------------------------------------------ #
# Команда — инлайн-форма
# ------------------------------------------------------------------ #

CL_PAGE_SIZE = 10   # версий на странице выбора


def _card_text(entry) -> str:
    """Запись версии в рамках одного сообщения инлайн-формы — не пакуется на несколько
    сообщений (как _pack для обычной отправки), а обрезается с пометкой, куда за полной версией."""
    body = _render(entry)
    if len(body) > _SAFE_LIMIT:
        body = (
            body[:_SAFE_LIMIT].rstrip()
            + "\n\n" + visual.hint(f"… обрезано, полностью — <code>.changelog {html.escape(entry['version'])}</code>")
        )
    return body


def _picker(entries_sorted, page):
    items = [
        (f"{e['version']}" + (f" — {e['title']}" if e['title'] else ""), f"cl_open:{e['version']}")
        for e in entries_sorted
    ]
    rows, page, total = engine.picker_page(items, page, per_page=CL_PAGE_SIZE, page_callback="cl_page:")
    rows.append([(visual.btn('К последней версии', 'back'), "cl_latest", "primary")])
    text = visual.title('Журнал обновлений', f"выберите версию · страница {page + 1}/{total}")
    return text, rows, page


def _bind_form(form, entries_sorted):
    state = {'page': 0}

    async def _show_picker(page):
        text, buttons, state['page'] = _picker(entries_sorted, page)
        await form.edit(text, buttons=buttons)

    async def _show_entry(entry):
        await form.edit(_card_text(entry), buttons=[[(visual.btn('Перейти к версии…'), f"cl_pick:{state['page']}")]])

    @form.on_click(data_startswith="cl_pick:")
    async def _pick(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data_startswith="cl_page:")
    async def _page(click):
        await click.answer()
        await _show_picker(int(click.data.split(":", 1)[1] or 0))

    @form.on_click(data="cl_latest")
    async def _latest(click):
        await click.answer()
        await _show_entry(entries_sorted[0])

    @form.on_click(data_startswith="cl_open:")
    async def _open(click):
        await click.answer()
        version = click.data.split(":", 1)[1]
        entry = next((e for e in entries_sorted if e['version'] == version), None)
        if entry is None:
            await form.edit(
                visual.err('Запись версии не найдена'),
                buttons=[[(visual.btn('К списку версий', 'back'), f"cl_pick:{state['page']}")]],
            )
            return
        await _show_entry(entry)


@decorators.command('changelog', 'cl')
async def changelog_cmd(event):
    """инлайн: запись последней (или указанной) версии, кнопка «Перейти к версии...» — выбор из всех версий (по 10 на странице); list — текстовый список версий"""
    args = engine.get_args().strip()
    entries, source = _load_entries()
    if not entries:
        await engine.answer(
            event,
            visual.err('Журнал обновлений не найден') + "\n"
            + visual.hint('нет файла <code>CHANGELOG.md</code> (и раздела Changelog в <code>README.md</code>)'),
        )
        return

    entries_sorted = sorted(entries, key=lambda e: _vt(e['version']), reverse=True)

    if args.lower() in ('list', 'all', 'список'):
        versions = ", ".join(f"<code>{html.escape(e['version'])}</code>" for e in entries_sorted)
        await engine.answer(
            event,
            visual.title('Журнал обновлений', f"версий: {len(entries_sorted)}") + "\n"
            + visual.field('источник', html.escape(str(source))) + "\n\n"
            + f"{versions}\n\n"
            + visual.hint('запись версии — <code>.changelog 1.0.0</code>'),
        )
        return

    if args:
        entry = next((e for e in entries_sorted if _vt(e['version']) == _vt(args)), None)
        if entry is None:
            await engine.answer(
                event,
                visual.err('Записи о версии нет') + "\n"
                + visual.field('версия', html.escape(args)) + "\n"
                + visual.hint('список версий — <code>.changelog list</code>'),
            )
            return
    else:
        entry = entries_sorted[0]   # по умолчанию — последняя версия

    try:
        form = await engine.create_inlineform(
            chat_id=event.chat_id,
            text=_card_text(entry),
            buttons=[[(visual.btn('Перейти к версии…'), "cl_pick:0")]],
        )
    except Exception:
        # Стационарный бот недоступен — инлайн-форма невозможна, прежнее поведение:
        # текстом, при необходимости несколькими сообщениями.
        messages = _pack([_render(entry)])
        await engine.answer(event, messages[0])
        for text in messages[1:]:
            await engine.reply(event, text)
        return

    _bind_form(form, entries_sorted)
    await engine.answer(event, visual.ok('Журнал обновлений') + "  " + visual.hint('ниже'))
