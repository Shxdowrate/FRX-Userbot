"""
core/decorators.py

Декораторы, доступные внутри модулей FRX.
Каждый декоратор просто помечает функцию метаданными (атрибутами).
Реальная регистрация хэндлеров происходит в core/engine.py при загрузке
модуля — движок сканирует namespace модуля и ищет функции с этими атрибутами.

Автор модуля должен явно импортировать этот файл как `import decorators`
(движок кладёт этот модуль в sys.modules под именем 'decorators' при
старте, см. Core._register_module_imports() в core/engine.py, — поэтому
`import decorators` работает без реального файла decorators.py рядом с
модулем, но сам import всё равно нужен, никакой автоматической
подстановки декораторов как глобальных имён в namespace модуля нет).
Использование: `decorators.command(...)`, `decorators.watcher()` и т.д.,
доступны: command, watcher, on_start, on_load, cycle, telegram_event,
callback, on_error.
"""


def command(*names):
    """
    @command('command1', 'com1')
    Стандартный обработчик команд. Функция вызывается с аргументом event
    (сообщение Telethon), когда пользователь отправляет <префикс><алиас>.
    """
    def deco(func):
        existing = getattr(func, '_frx_commands', [])
        func._frx_commands = list(existing) + [n.lower() for n in names]
        func._frx_type = 'command'
        return func
    return deco


def watcher():
    """
    @watcher()
    Следит за вообще всеми сообщениями во всех чатах (включая команды).
    Функция вызывается с аргументом event.
    """
    def deco(func):
        func._frx_type = 'watcher'
        return func
    return deco


def on_start():
    """
    @on_start()
    Запускается один раз при запуске юзербота (и сразу после загрузки
    модуля, если юзербот уже работает). Функция вызывается без аргументов.
    """
    def deco(func):
        func._frx_type = 'on_start'
        return func
    return deco


def on_load():
    """
    @on_load()
    Запускается при загрузке ЛЮБОГО модуля (в том числе этого самого) —
    передаёт объект модуля (ModuleInfo) в качестве аргумента.
    """
    def deco(func):
        func._frx_type = 'on_load'
        return func
    return deco


def cycle(interval='1m'):
    """
    @cycle(interval='1m')
    Выполняется циклично с заданным интервалом.
    Поддерживаются суффиксы: s (секунды), m (минуты), h (часы), d (дни).
    Функция вызывается без аргументов.
    """
    def deco(func):
        func._frx_type = 'cycle'
        func._frx_cycle = interval
        return func
    return deco


def telegram_event():
    """
    @telegram_event()
    Запускается при любых служебных событиях Telegram (не обычных
    сообщениях) — вход/выход из чата, изменение статуса и т.д.
    Функция вызывается с аргументом event.
    """
    def deco(func):
        func._frx_type = 'telegram_event'
        return func
    return deco


def callback():
    """
    @callback()
    Обрабатывает нажатия на inline-кнопки (CallbackQuery).
    Функция вызывается с аргументом event.
    """
    def deco(func):
        func._frx_type = 'callback'
        return func
    return deco


def on_error():
    """
    @on_error()
    Запускается при любой ошибке в любом модуле.
    Функция вызывается с аргументами (error_text, context), где
    context — словарь {'module': ..., 'function': ...} с условиями,
    при которых произошла ошибка.
    """
    def deco(func):
        func._frx_type = 'on_error'
        return func
    return deco


def on_account_switch():
    """
    @on_account_switch()
    Срабатывает каждый раз, когда юзербот "на лету"
    переключается на другой аккаунт (engine.set_account(number) —
    modules/builtin/AccountManager.py и т.п.), СРАЗУ ПОСЛЕ того, как
    engine.client/engine._owner_id уже обновлены на новый аккаунт.
    Функция вызывается с одним аргументом — number (номер телефона
    аккаунта, на который только что переключились), строкой.
    НЕ вызывается на первом запуске юзербота (там нет "переключения",
    аккаунт просто поднимается — см. Core.start()), только на реальных
    переключениях между уже добавленными аккаунтами.
    """
    def deco(func):
        func._frx_type = 'on_account_switch'
        return func
    return deco
