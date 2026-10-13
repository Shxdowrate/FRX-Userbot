"""
core/visual.py

Стандарт визуального отображения. В модулях РЕКОМЕНДУЕТСЯ использовать
visual('key') вместо "сырых" эмодзи — это позволяет пользователю
переопределить иконки глобально через visual_edit(), и все модули,
использующие visual(), сразу подхватят новый стиль.

облик юзербота (в пару к сайту): строгая типографика с красными метками.
  • цвет дают только две метки: 🟥 — заголовок сообщения, 🟧 — раздел; всё остальное — тонкие символы ▸ ◆ ◇ ━;
  • значения — моноширинным (<code>), подписи — обычным текстом, заголовки — капсом;
  • готовые помощники собирают сообщение в этом стиле, чтобы модули не повторяли разметку руками:

      import visual
      text = visual.title('Info') + '\n' + visual.field('версия', '1.0.0') + '\n' + visual.section('Система') + ...
      await engine.answer(event, visual.ok('Готово') )

    title(text, sub=None)       🟥 <b>TEXT</b> + линия ━━━ (+ подзаголовок курсивом)
    section(text)               🟧 <b>TEXT</b>
    field(label, value, code=True)   ▸ label  <code>value</code>
    item(text, level=0)         ▸ text   (level=1 — ▹ с отступом)
    sep()                       ━━━━━━━━━━━━━━━━
    ok(text) / err(text) / warn(text) / wait(text) / hint(text)
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


def visual(key: str) -> str:
    """Возвращает иконку по стандартизированному ключу."""
    if key not in ICONS:
        raise KeyError(f"Неизвестный ключ иконки: '{key}'. См. visual_list().")
    return ICONS[key]


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
        out = f"{get('accent')} <b>{_upper(text)}</b>\n{sep()}"
        return out + (f"\n<i>{sub}</i>" if sub else '')

    def section(text) -> str:
        return f"{get('section')} <b>{_upper(text)}</b>"

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

    def btn(text, kind: str = 'open') -> str:
        key = {'open': 'next', 'next': 'next', 'back': 'back', 'close': 'close', 'on': 'on', 'off': 'off'}.get(kind)
        return f"{get(key)} {text}" if key else str(text)

    return {'sep': sep, 'title': title, 'section': section, 'field': field, 'item': item,
            'ok': ok, 'err': err, 'warn': warn, 'wait': wait, 'hint': hint, 'btn': btn}


_h = make_helpers(visual)
sep, title, section, field, item = _h['sep'], _h['title'], _h['section'], _h['field'], _h['item']
ok, err, warn, wait, hint, btn = _h['ok'], _h['err'], _h['warn'], _h['wait'], _h['hint'], _h['btn']


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


def progress_bar(percent, width: int = 10, style: str = 'block', show_percent: bool = False, brackets: bool = False) -> str:
    """
    Полоска прогресса: progress_bar(37) → «███▋░░░░░░» (style='smooth') / «████░░░░░░» (block).
    percent — 0…100 (дробные можно, за пределами — обрезается); width — ячеек (по умолчанию 10: 1 ячейка = 10 %);
    style — см. PROGRESS_STYLES; show_percent — дописать « 37%»; brackets — обернуть в [ ].
    """
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
