"""
core/visual.py

Стандарт визуального отображения. В модулях РЕКОМЕНДУЕТСЯ использовать
visual('key') вместо "сырых" эмодзи — это позволяет пользователю
переопределить иконки глобально через visual_edit(), и все модули,
использующие visual(), сразу подхватят новый стиль.

облик юзербота (в пару к сайту): строгая типографика с красными метками.
  • цвет дают только две метки: 🟥 — заголовок сообщения, 🟧 — раздел; всё остальное — тонкие символы ▸ ◆ ◇;
  • значения — моноширинным (<code>), подписи — обычным текстом, заголовки — жирным;
  • одна строка — один факт или одна подсказка: ничего не склеиваем через «·», между блоками — пустая строка;
  • готовые помощники собирают сообщение в этом стиле, чтобы модули не повторяли разметку руками:

      import visual
      text = visual.title('Info') + '\n' + visual.field('версия', '1.0.0') + '\n' + visual.section('Система') + ...
      await engine.answer(event, visual.ok('Готово') )

    title(text, sub=None)       🟥 <b>Text</b> (+ подзаголовок курсивом строкой ниже) и пустая строка после
    section(text)               🟧 <b>Text</b>
    field(label, value, code=True)   ▸ label  <code>value</code>
    item(text, level=0)         ▸ text   (level=1 — ▹ с отступом)
    sep()                       ━━━━━━━━━━━━━━━━
    ok(text) / err(text) / warn(text) / wait(text) / hint(text)
    hints(*lines)               подсказки курсивом — каждая своей строкой (пустые пропускаются)
    btn(text, kind='open')      подпись кнопки: ▸ text · back → ◂ text · close → ✕ text · on/off → ◉/○ text

  Все помощники НЕ экранируют HTML — передавайте уже безопасный текст (html.escape для пользовательских данных).
"""

import re

_DEFAULT_ICONS = {
    'status_on': '🟧',      # Активно / Включено
    'status_off': '⬛',     # Неактивно / Выключено
    'status_error': '🟥',   # Ошибка
    'status_success': '🔶', # Успешно
    'status_warning': '⚠️', # Предупреждение
    'status_start': '▶︎',    # Старт
    'status_stop': '⏸︎',     # Остановлено / Приостановлено
    'status_wait': '⏳',    # Ожидание / Загрузка
    'status_process': '⟳',  # В процессе

    'name': '◆',            # Имя / Название
    'description': '▹',     # Описание
    'version': '◇',         # Версия
    'author': '✎',          # Автор
    'list': '☰',            # Список
    'element': '▸',         # Элемент списка (разделитель)
    'info': '◈',            # Информация
    'question': '◇',        # Вопрос / Помощь

    'config': '⚙︎',          # Настройки / Конфигурация
    'module': '▣',          # Модуль
    'file': '▤',            # Файл
    'link': '↗',            # Ссылка

    'time': '◷',            # Время
    'date': '▦',            # Дата

    'chat': '▢',            # Чат
    'user': '◉',            # Пользователь
    'group': '⦿',           # Группа
    'channel': '◍',         # Канал

    'photo': '▨',           # Фото
    'video': '▶︎',           # Видео
    'audio': '♪',           # Аудио

    'code': '⌘',            # Код (.eval/.exec)
    'result': '⇢',          # Результат выполнения кода
    'security': '⛨',        # Конфиденциальная информация
    'system': '▥',          # Информация о системе/сервере
    'web': '◎',             # веб-сайт

    # элементы оформления
    'accent': '🟥',         # метка заголовка сообщения
    'section': '🟧',        # метка раздела
    'sep': '━',             # символ линии-разделителя
    'back': '◂',            # кнопка «назад»
    'next': '▸',            # кнопка «дальше / открыть»
    'close': '✕',           # кнопка «закрыть / отмена»
    'on': '◉',              # переключатель включён (в кнопках)
    'off': '○',             # переключатель выключен (в кнопках)
}

# Рабочая копия — может быть изменена через visual_edit()
ICONS = dict(_DEFAULT_ICONS)

# Премиум-эмодзи из фирменного пака FRX (t.me/addemoji/frx_by_shxdowrate_userbot): ключ → (document_id, обычный эмодзи).
# Показываются вместо обычной иконки, пока включено (настройка premium_emoji модуля Config) и иконку не
# переопределили через visual_edit(). Внутри тега — обычный эмодзи (Telegram требует там настоящий эмодзи): его видно
# там, где премиум нельзя (аккаунт или владелец бота без Telegram Premium, терминал, всплывашки).
# Тонкие символы разметки (▸ ▹ ━) и значки кнопок (◂ ▸ ✕ ◉ ○) намеренно обычные.
PREMIUM_PACK = 'frx_by_shxdowrate_userbot'
PREMIUM_ICONS = {
    'accent': (5371030807521568895, '🟥'),          # логотип F — метка заголовка
    'section': (5370548109032072321, '🟧'),         # молния — метка раздела
    'status_on': (5370801490627700896, '🟧'),       # кнопка питания
    'status_off': (5368461643984447980, '⬛'),      # запрет
    'status_error': (5368412488083742794, '🟥'),    # крестик в круге
    'status_success': (5371021083715610721, '🔶'),  # галочка в круге
    'status_warning': (5371006931798371227, '⚠️'),  # треугольник
    'status_start': (5370815951782584449, '▶️'),    # play
    'status_stop': (5368478359997165462, '⏸'),     # пауза
    'status_wait': (5368701633872048172, '⏳'),     # песочные часы
    'status_process': (5371073241798453466, '🔄'),  # обновление
    'info': (5370903835403396564, 'ℹ️'),
    'config': (5370894330640770860, '⚙️'),          # шестерёнка
    'module': (5370815548055659638, '📦'),          # куб
    'file': (5370820036296485768, '📄'),
    'link': (5370604952924235358, '🔗'),
    'time': (5370744732634884517, '🕐'),
    'date': (5370654972113365025, '📅'),
    'chat': (5368710009058271258, '💬'),
    'user': (5368843543886472537, '👤'),
    'author': (5368843543886472537, '👤'),
    'photo': (5370853524156489643, '📷'),
    'video': (5370960872569086316, '🎥'),
    'audio': (5370620096978920144, '🎵'),
    'code': (5371070926811080023, '👨‍💻'),
    'security': (5368493482577012597, '🛡'),        # щит
    'system': (5370955671363691356, '🖥'),          # чип
    'web': (5371043898581885395, '🌐'),             # глобус
}
# полоска прогресса из пака (style='premium'): (левый край, середина, правый край) — заполненные и пустые
PREMIUM_BAR = {
    'full': (5368667995688185746, 5371074096496950298, 5370989524295920812),
    'empty': (5370714289906691755, 5370557029679147820, 5368618457535392937),
}
_premium = True


def set_premium(enabled: bool) -> None:
    """Включить / выключить премиум-эмодзи в иконках (обычные остаются запасным вариантом внутри тега)."""
    global _premium
    _premium = bool(enabled)


def premium_enabled() -> bool:
    return _premium


def premium(doc_id, fallback: str) -> str:
    """Премиум-эмодзи как HTML: <tg-emoji emoji-id="…">обычный</tg-emoji>."""
    return f'<tg-emoji emoji-id="{doc_id}">{fallback}</tg-emoji>'


_TG_EMOJI_RE = re.compile(r'<tg-emoji\b[^>]*>(.*?)</tg-emoji>', re.S)


def plain(text) -> str:
    """Текст без премиум-эмодзи (остаются обычные) — для терминала, всплывашек и прочих мест без HTML."""
    if not isinstance(text, str) or 'tg-emoji' not in text:
        return text
    return _TG_EMOJI_RE.sub(lambda m: m.group(1), text)


def visual(key: str) -> str:
    """Возвращает иконку по стандартизированному ключу."""
    if key not in ICONS:
        raise KeyError(f"Неизвестный ключ иконки: '{key}'. См. visual_list().")
    icon = ICONS[key]
    if _premium and key in PREMIUM_ICONS and icon == _DEFAULT_ICONS[key]:
        doc_id, fallback = PREMIUM_ICONS[key]
        return premium(doc_id, fallback)
    return icon


def visual_edit(key: str, value: str = 'standart') -> str:
    """
    Изменяет иконку по ключу.
    value='standart' — сбрасывает иконку обратно к дефолтному значению.
    """
    if key not in _DEFAULT_ICONS:
        raise KeyError(f"Неизвестный ключ иконки: '{key}'. См. visual_list().")
    ICONS[key] = _DEFAULT_ICONS[key] if value == 'standart' else value
    return ICONS[key]


def visual_list() -> dict:
    """Возвращает весь список стандартизированных иконок (текущие значения)."""
    return dict(ICONS)


# --------------------------------------------------------------------------- #
# помощники оформления
# --------------------------------------------------------------------------- #

_TAG = re.compile(r'(<[^>]*>|&[#\w]+;)')


def _upper(text) -> str:
    """Капс только для видимого текста — теги и HTML-сущности (&amp; …) не трогаем."""
    return ''.join(p if _TAG.fullmatch(p) else p.upper() for p in _TAG.split(str(text)))


def make_helpers(get):
    """Помощники поверх функции получения иконки get(key) — так их же использует Sandbox со своей копией иконок."""
    def sep(n: int = 16) -> str:
        return get('sep') * n

    def title(text, sub=None) -> str:
        # пустая строка после заголовка вместо прежней линии ━━━ — отделяет шапку от содержимого
        out = f"{get('accent')} <b>{text}</b>"
        return out + (f"\n<i>{sub}</i>" if sub else '') + "\n"

    def section(text) -> str:
        return f"{get('section')} <b>{text}</b>"

    def field(label, value, code: bool = True) -> str:
        v = '—' if value is None or value == '' else value
        return f"{get('element')} {label}  " + (f"<code>{v}</code>" if code else f"<b>{v}</b>")

    def item(text, level: int = 0) -> str:
        return f"{get('element')} {text}" if level <= 0 else '   ' * level + f"{get('description')} {text}"

    def ok(text) -> str:
        return f"{get('status_success')} <b>{text}</b>"

    def err(text) -> str:
        return f"{get('status_error')} <b>{text}</b>"

    def warn(text) -> str:
        return f"{get('status_warning')} {text}"

    def wait(text) -> str:
        return f"{get('status_wait')} <i>{text}</i>"

    def hint(text) -> str:
        return f"<i>{text}</i>"

    def hints(*lines) -> str:
        return "\n".join(f"<i>{t}</i>" for t in lines if t)

    def btn(text, kind: str = 'open') -> str:
        key = {'open': 'next', 'next': 'next', 'back': 'back', 'close': 'close', 'on': 'on', 'off': 'off'}.get(kind)
        return f"{get(key)} {text}" if key else str(text)

    return {'sep': sep, 'title': title, 'section': section, 'field': field, 'item': item,
            'ok': ok, 'err': err, 'warn': warn, 'wait': wait, 'hint': hint, 'hints': hints, 'btn': btn}


_h = make_helpers(visual)
sep, title, section, field, item = _h['sep'], _h['title'], _h['section'], _h['field'], _h['item']
ok, err, warn, wait, hint, btn = _h['ok'], _h['err'], _h['warn'], _h['wait'], _h['hint'], _h['btn']
hints = _h['hints']


# --------------------------------------------------------------------------- #
# progress_bar — полоска прогресса текстом
# --------------------------------------------------------------------------- #

# стиль → (заполнено, пусто[, частичные доли от меньшей к большей])
PROGRESS_STYLES = {
    'block':    ('█', '░'),
    'smooth':   ('█', '░', '▏▎▍▌▋▊▉'),     # с долями — плавная
    'shade':    ('▓', '░'),
    'bar':      ('▰', '▱'),
    'square':   ('■', '□'),
    'dot':      ('●', '○'),
    'circle':   ('◉', '◎'),
    'line':     ('━', '─'),
    'arrow':    ('═', '─', '>'),          # «══════>───»
    'ascii':    ('#', '-'),
    'braille':  ('⣿', '⣀', '⡀⡄⡆⡇⣇⣧⣷'),
    'star':     ('★', '☆'),
    'diamond':  ('◆', '◇'),
    'frx':      ('🟧', '⬛'),              # в цветах FRX
    'heart':    ('♥', '♡'),
    'moon':     ('🌕', '🌑', '🌘🌗🌖'),
    'vertical': ('█', ' ', '▁▂▃▄▅▆▇'),     # одна ячейка — столбик (удобно с width=1)
}


def _premium_bar(percent, width, show_percent) -> str:
    """Фирменная полоска из премиум-эмодзи пака: каждая плитка ≈ 3 ячейки (width=10 → 3 плитки), минимум 2.
    Премиум выключен — обычный стиль 'frx' (🟧⬛)."""
    try:
        p = max(0.0, min(100.0, float(percent)))
    except (TypeError, ValueError):
        p = 0.0
    if not _premium:
        return progress_bar(p, width, 'frx', show_percent)
    n = max(2, round(max(1, int(width or 10)) / 3))
    lit = round(p / 100 * n)
    tiles = []
    for i in range(n):
        pos = 0 if i == 0 else (2 if i == n - 1 else 1)
        kind = 'full' if i < lit else 'empty'
        tiles.append(premium(PREMIUM_BAR[kind][pos], '🟧' if kind == 'full' else '⬛'))
    out = ''.join(tiles)
    return out + (f" {round(p)}%" if show_percent else '')


def progress_bar(percent, width: int = 10, style: str = 'block', show_percent: bool = False, brackets: bool = False) -> str:
    """
    Полоска прогресса: progress_bar(37) → «███▋░░░░░░» (style='smooth') / «████░░░░░░» (block).
    percent — 0…100 (дробные можно, за пределами — обрезается); width — ячеек (по умолчанию 10: 1 ячейка = 10 %);
    style — см. PROGRESS_STYLES, а также 'premium' — фирменная полоска из премиум-эмодзи пака FRX; show_percent — дописать « 37%»; brackets — обернуть в [ ].
    """
    if str(style).lower() == 'premium':
        return _premium_bar(percent, width, show_percent)
    st = PROGRESS_STYLES.get(str(style).lower())
    if st is None:
        raise KeyError(f"Неизвестный стиль полоски '{style}'. Доступны: {', '.join(PROGRESS_STYLES)}")
    try:
        p = float(percent)
    except (TypeError, ValueError):
        p = 0.0
    p = max(0.0, min(100.0, p))
    width = max(1, int(width or 10))
    full, empty = st[0], st[1]
    parts = st[2] if len(st) > 2 else ''
    cells = p / 100 * width
    n_full = int(cells)
    out = full * n_full
    if n_full < width:
        frac = cells - n_full
        if style == 'arrow':
            out += parts if p > 0 else empty
        elif parts and frac > 0:
            idx = min(len(parts) - 1, int(frac * (len(parts) + 1)) - 1)
            out += parts[idx] if idx >= 0 else empty
        else:
            out += empty
        out += empty * (width - n_full - 1)
    if brackets:
        out = f"[{out}]"
    if show_percent:
        out += f" {round(p)}%"
    return out
