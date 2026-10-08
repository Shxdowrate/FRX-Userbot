"""
core/api.py

ЭТОТ ФАЙЛ НЕ ИСПОЛНЯЕТСЯ В РАНТАЙМЕ И НЕ ИМПОРТИРУЕТСЯ ДВИЖКОМ.
Он существует ИСКЛЮЧИТЕЛЬНО для автодополнения и подсказок типов
в IDE (Pylance/VS Code, PyCharm) при написании модулей FRX.

Реальные функции (command, db, reply, tlog и т.д.) не встроены в
namespace модуля автоматически — при загрузке модуля движок кладёт
соответствующие объекты в sys.modules под короткими именами (engine,
decorators, visual, waiters, db, cache, files, accounts, time_utils —
см. core/engine.py, Core._register_module_imports()), а сам модуль
должен явно их импортировать обычным `import engine`, `import db`
и т.д. (README «Как устроено ядро»). Импорт в рантайме ВСЕ РАВНО нужен — этот
файл лишь даёт IDE знать, какую сигнатуру ожидать за каждым именем.

КАК ПОЛЬЗОВАТЬСЯ (добавить в начало файла модуля):

    from typing import TYPE_CHECKING
    if TYPE_CHECKING:
        from core.api import (
            command, watcher, on_start, on_load, cycle, telegram_event,
            callback, on_error, db, answer, reply, noreply, send_message,
            get_args, load_module, unload_module, kill_module, reload_module,
            get_module_list, get_module_info, search_module, resolve_module, search_dec_module,
            create_config, edit_config, delete_config, get_config,
            download_reply_file, reply_file, get_file, delete_file, create_file,
            wait_message,
            get_prefix, edit_prefix, default_prefix,
            get_time, time_format, edit_time,
            add_account, set_account, delete_account, account_list, account_list_full,
            account_info, set_tag, remove_tag, get_tags, refresh_info, new_login,
            tlog, visual, visual_edit, visual_list, client,
            request_shutdown, asset_path, bot_send_to_owner,
            web,   # @web.page + интерфейс страниц сайта (README «Страницы сайта из модулей»)
        )

`if TYPE_CHECKING:` — это константа typing, которая ВСЕГДА False во
время реального выполнения программы, поэтому импорт внутри блока
физически никогда не выполняется (не переопределяет то, что подставил
движок) — но статические анализаторы (Pylance и т.п.) читают этот блок
как обычный код и дают полноценные подсказки/автодополнение.
"""

from __future__ import annotations
from typing import Any, Callable, Optional, Union
from datetime import datetime

from telethon.events.newmessage import NewMessage
from telethon.events.callbackquery import CallbackQuery
from telethon.events.chataction import ChatAction
from telethon.tl.custom.message import Message
from telethon import TelegramClient


Event = NewMessage.Event
CallbackEvent = CallbackQuery.Event
ChatActionEvent = ChatAction.Event


# ==================================================================== #
# Декораторы
# ==================================================================== #

def command(*names: str) -> Callable:
    """@command('cmd', 'alias') — обработчик команды <префикс>cmd. Функция: async def f(event)."""
    ...

def watcher() -> Callable:
    """@watcher() — следит за ВСЕМИ сообщениями во всех чатах. Функция: async def f(event)."""
    ...

def on_start() -> Callable:
    """@on_start() — запускается при старте юзербота. Функция: async def f()."""
    ...

def on_load() -> Callable:
    """@on_load() — запускается при загрузке ЛЮБОГО модуля. Функция: async def f(module_info)."""
    ...

def cycle(interval: str = '1m') -> Callable:
    """@cycle(interval='1m') — циклично, с интервалом (s/m/h/d). Функция: async def f()."""
    ...

def telegram_event() -> Callable:
    """@telegram_event() — служебные события Telegram. Функция: async def f(event)."""
    ...

def callback() -> Callable:
    """@callback() — нажатие на inline-кнопку. Функция: async def f(event)."""
    ...

def on_error() -> Callable:
    """@on_error() — любая ошибка в любом модуле. Функция: async def f(error_text, context)."""
    ...


# ==================================================================== #
# База данных
# ==================================================================== #

class _DB:
    """Объект `db`, доступный в модуле. get/set — с автопрефиксом текущего модуля."""

    async def get(self, key: str, default: Any = None) -> Any:
        """Возвращает значение ключа этого модуля."""
        ...

    async def set(self, key: str, value: Any) -> Any:
        """Сохраняет значение под ключом этого модуля."""
        ...

    async def delete(self, key: str) -> bool:
        """Удаляет ключ этого модуля."""
        ...

    async def gget(self, key: str, default: Any = None) -> Any:
        """Глобальный доступ: читает "сырой" ключ без префикса модуля."""
        ...

    async def gset(self, key: str, value: Any) -> Any:
        """Глобальный доступ: пишет "сырой" ключ без префикса модуля."""
        ...

    async def gdelete(self, key: str) -> bool:
        """Глобальный доступ: удаляет "сырой" ключ без префикса модуля."""
        ...

    async def list(self, modulename: Optional[str] = None) -> dict:
        """Все ключи заданного модуля (по умолчанию — текущего)."""
        ...

    async def glist(self, prefix: Optional[str] = None) -> dict:
        """Все ключи БД по произвольному префиксу (или вообще все)."""
        ...


db: _DB


# ==================================================================== #
# Управление модулями
# ==================================================================== #

def get_module_list() -> list:
    """Список всех загруженных модулей (их MODULE_NAME)."""
    ...

def get_module_info(module_name: str) -> dict:
    """Вся инфа о модуле: команды, вотчеры, версия, автор и т.д."""
    ...

def search_module(query: str) -> Optional[str]:
    """Ищет наиболее похожий по названию модуль."""
    ...

def resolve_module(query: str) -> Optional[str]:
    """Резолвит MODULE_NAME или NAME в MODULE_NAME (точное совпадение, без учёта регистра у NAME)."""
    ...

def search_dec_module(query: str) -> list:
    """Ищет модуль(и) с похожей по названию командой."""
    ...

async def load_module(file_or_reply: Union[str, Message], target: str = 'loaded') -> Any:
    """Загружает модуль по пути к файлу либо по реплаю (Message) с файлом."""
    ...

async def unload_module(module_name: str, archive: bool = True) -> bool:
    """Выгружает модуль. для loaded-модуля при archive=True файл уезжает в user/modules/unloaded/ (вернуть — restore_module / .restoremods); archive=False — файл остаётся на месте (так работают reload/kill)."""
    ...

def list_unloaded_modules() -> list[dict]:
    """выгруженные модули из user/modules/unloaded/: [{'file','path','module_name','name','version','author','description','commands','broken','loaded'}, ...]."""
    ...

def find_unloaded_module(query: str) -> Optional[dict]:
    """выгруженный модуль по имени файла, MODULE_NAME или NAME (или None)."""
    ...

async def restore_module(query: str) -> Any:
    """возвращает выгруженный модуль в user/modules/ и загружает его. KeyError / ValueError / FileExistsError — см. README 9."""
    ...

async def delete_unloaded_module(query: str) -> bool:
    """удаляет файл выгруженного модуля навсегда."""
    ...

async def exec_modcmd(event: Any, module: str, command: str, args: Any = '') -> bool:
    """запускает КОМАНДУ модуля (по имени команды/алиасу, не имени функции) с аргументами, как будто её ввёл пользователь. True — отработала без исключений, False — упала (ошибка ушла в @on_error); KeyError — нет модуля/команды."""
    ...

def parse_module_meta(source: str) -> Optional[dict]:
    """статический (ast, без исполнения) разбор исходника модуля: NAME/MODULE_NAME/VERSION/AUTHOR/DESCRIPTION/commands или None."""
    ...

async def kill_module(module_name: str) -> Any:
    """Принудительно перезапускает модуль (завис / не читает БД)."""
    ...

async def reload_module(module_name: str) -> Any:
    """Перечитывает файл модуля с диска и перезагружает (после ручной правки)."""
    ...


# ==================================================================== #
# Конфиги модуля
# ==================================================================== #

async def create_config(key: str, description: str = '', data_type: str = 'str', default: Any = None,
                         valid: Optional[dict] = None, options: Optional[list] = None) -> Any:
    """
    Создаёт конфиг для текущего модуля. data_type может быть 'list', а также
    'choice'/'choices'.
    valid — необязательные ограничения на значение:
        'int'/'float': {'max_value': ..., 'min_value': ...}
        'str':         {'max_len': ..., 'min_len': ...}
    Проверяются при edit_config(..., 'edit', ...).
    options — ОБЯЗАТЕЛЕН для 'choice'/'choices': статический
    список допустимых значений. default для 'choice' — один конкретный элемент
    options (не номер!); для 'choices' — непустой список элементов options.
    """
    ...

async def edit_config(key: str, operation_type: str, value: Any = None) -> Any:
    """
    Редактирует конфиг: 'edit' (int/float/bool/str/choice) или 'add'/'del'
    (list/choices). Для 'choice'/'choices' value — конкретный
    элемент из options конфига (не его номер); у 'choices' нельзя 'del'
    последний оставшийся выбранный элемент.
    """
    ...

async def delete_config(key: str) -> bool:
    """Удаляет один ключ конфига, не трогая остальные."""
    ...

async def get_config(key: str, module_name: Optional[str] = None) -> Any:
    """Возвращает текущее значение конфига."""
    ...


# ==================================================================== #
# Сообщения
# ==================================================================== #

async def answer(message: Message, text: str) -> Message:
    """Изменяет сообщение, если возможно, иначе отправляет новое."""
    ...

async def reply(message: Message, text: str) -> Message:
    """Отвечает на message."""
    ...

async def noreply(message: Message, text: str) -> Message:
    """Отправляет сообщение в тот же чат, не отвечая на message."""
    ...

async def send_message(chat_id: Union[int, str], text: str) -> Message:
    """Отправляет сообщение в указанный чат."""
    ...

def get_args() -> str:
    """Аргументы текущей команды одной строкой (без самой команды)."""
    ...


# ==================================================================== #
# Файлы
# ==================================================================== #

async def download_reply_file(message: Message) -> dict:
    """Скачивает файл из реплая. Возвращает {'id','path','name','type','size'}."""
    ...

async def reply_file(message: Message, file: Union[str, Message], text: str = '') -> Message:
    """Отправляет файл (путь или ID из реестра) с подписью, отвечая на message."""
    ...

async def get_file(query: Any = None, ftype: Optional[str] = None) -> Optional[dict]:
    """Ищет файл по ID/названию/пути/весу."""
    ...

async def delete_file(file_id: Optional[str] = None, path: Optional[str] = None) -> bool:
    """Удаляет файл из реестра и с диска."""
    ...

async def create_file(type: str, name: str, text: Union[str, bytes]) -> dict:
    """Создаёт файл с заданным содержимым."""
    ...


# ==================================================================== #
# Ожидание сообщений
# ==================================================================== #

async def wait_message(chat_id: Union[str, int, None] = None, user_id: Union[str, int, None] = None,
                        timeout: Optional[float] = None) -> Event:
    """
    Ждёт следующее сообщение, подходящее под фильтры chat_id/user_id
    (оба None — берутся chat_id/user_id текущего события). timeout=None —
    ждать бессрочно (до отмены через Waiter.cancel()). Поддерживает и
    одиночный `await wait_message(...)`, и `async for msg in wait_message(...)`
    — см. core/waiters.py.
    """
    ...


# ==================================================================== #
# Префикс
# ==================================================================== #

async def get_prefix() -> str:
    """Возвращает текущий префикс юзербота."""
    ...

async def edit_prefix(new_prefix: str) -> str:
    """Меняет префикс на указанный."""
    ...

async def default_prefix() -> str:
    """Сбрасывает префикс на стандартный ('.')."""
    ...


# ==================================================================== #
# Время
# ==================================================================== #

def get_time() -> datetime:
    """Возвращает текущее время."""
    ...

def time_format(time: Union[datetime, int, float], fmt: str) -> str:
    """Форматирует время по strftime-токенам (%Y %m %d %H %M %S %A %B ...)."""
    ...

def edit_time(time: Union[datetime, int, float], value: str) -> datetime:
    """Смещает время. value — например '+1h', '-30m', '+2d'."""
    ...


# ==================================================================== #
# Мультиаккаунт
# ==================================================================== #

async def add_account(number: str) -> Any:
    """Добавляет аккаунт в юзербот (интерактивная авторизация)."""
    ...

async def set_account(number: str) -> Any:
    """Переключается на указанный аккаунт "на лету"."""
    ...

async def delete_account(number: str) -> None:
    """Удаляет сохранённый аккаунт (и корректно отключает его клиент, если он был поднят)."""
    ...

def account_list() -> list:
    """Список номеров аккаунтов, где авторизован юзербот."""
    ...

def account_list_full() -> list:
    """То же, что account_list(), но с tags/info каждого аккаунта: [{'phone','tags','info'}, ...]."""
    ...

def account_info(number: str) -> dict:
    """Данные аккаунта без session: {'phone': ..., 'tags': {...}, 'info': {...}}."""
    ...

def set_tag(number: str, key: str, value) -> None:
    """Вешает на аккаунт произвольный тег, например set_tag(num, 'name', 'Рабочий')."""
    ...

def remove_tag(number: str, key: str) -> None:
    """Снимает тег с аккаунта."""
    ...

def get_tags(number: str) -> dict:
    """Все теги аккаунта."""
    ...

async def refresh_info(number: str = None) -> None:
    """
    Обновляет info (id/first_name/last_name/username) аккаунта(ов) через
    get_me() — работает для ЛЮБОГО сохранённого аккаунта, не только для
    активного (сессии независимы). Без number — обновляет все сразу.
    """
    ...

def new_login() -> Any:
    """
    Пошаговая авторизация НОВОГО аккаунта (для UI, который не может
    просто попросить номер/код в терминале, как add_account) —
    возвращает accounts.AddAccount, см. core/accounts.py и README «accounts: мультиаккаунт из модулей»
    README: enter_phone() -> enter_code() -> (если нужна 2FA) enter_2FA().
    """
    ...


# ==================================================================== #
# Прочее
# ==================================================================== #

def tlog(text: str) -> None:
    """Пишет text в терминал."""
    ...

def guest_id() -> Optional[int]:
    """id гостя, по чьей команде или клику в форме сейчас выполняется код (README «Гости и алиасы в модулях»); None — владелец."""
    ...

def module_danger(module_name: str) -> list:
    """Почему доступ гостя к модулю критически опасен (выполняет код, загружает модули, работает с файлами, сессиями…); [] — не опасен."""
    ...

def botlog(text: Any, level: str = 'info', *, module: Optional[str] = None, function: Optional[str] = None) -> Any:
    """лог в ЛС владельца через стационарного бота (модуль, функция, время, text внизу) с лимитером от флуда. level: info|success|warning|error. Синхронный вызов, возвращает asyncio.Task (await -> 'sent'|'dropped'|'failed'|'disabled'|'no_bot')."""
    ...

def botlog_raw(message: str) -> Any:
    """то же, что botlog(), но для готового HTML-текста (без оформления): лимитер + доставка."""
    ...

def safe_html(text: Any) -> Any:
    """оставляет настоящие HTML-теги Telegram, а всё остальное (<имя>, одиночные <, >, &) экранирует. Применяется автоматически при отправке; явный вызов нужен редко."""
    ...

def picker_page(items: list, page: int = 0, per_page: int = 7, page_callback: str = "pick_page:") -> tuple:
    """страница постраничного ВЫБОРА для form.edit(): (rows, page, total_pages). items — кнопки (text, callback_data)."""
    ...

async def bot_send_to_owner(text: str, file: Any = None, buttons: Any = None) -> Any:
    """
    отправляет владельцу в ЛС ОТ ИМЕНИ стационарного бота (HTML-текст и/или файл).
    Если бот написать не может (нет диалога с текущим аккаунтом / бот в ЧС) — юзербот сам
    снимает бота с ЧС и пишет ему /start, после чего бот отправляет то же сообщение.
    Без стационарного бота — RuntimeError. Лимит Bot API на файл — 50 МБ.
    """
    ...

def request_shutdown(code: int = 0) -> None:
    """просит main.py корректно остановить юзербот и выйти с кодом code (0 — стоп, 42 — перезагрузка, 43 — применить заявку keeper'а). Обычно нужна команда .restart, а не прямой вызов."""
    ...

def asset_path(*parts: str) -> Any:
    """pathlib.Path файла-ассета, например asset_path('logo.png'): сначала user/assets/ (свои файлы), затем assets/ проекта. ValueError — выход за пределы папки, FileNotFoundError — файла нет."""
    ...

def visual(key: str) -> str:
    """Возвращает стандартизированную иконку по ключу."""
    ...

def visual_edit(key: str, value: str = 'standart') -> str:
    """Меняет иконку по ключу ('standart' — сброс к дефолту)."""
    ...

def visual_list() -> dict:
    """Весь текущий набор стандартизированных иконок."""
    ...

client: TelegramClient


# ==================================================================== #
# import web — страницы локального сайта (.web), README «Страницы сайта из модулей»
# ==================================================================== #

class _Ui:
    """Построитель страницы (аргумент функции, помеченной @web.page). Все методы добавляют компонент."""
    state: dict          # dict, живёт между кликами и перерисовками (до перезапуска юзербота)
    values: dict         # текущие значения полей ввода: name -> значение

    def title(self, text: Any) -> None: ...
    def text(self, text: Any) -> None: ...
    def muted(self, text: Any) -> None: ...
    def code(self, text: Any) -> None: ...
    def divider(self) -> None: ...
    def input(self, name: str, label: str = '', value: str = '', placeholder: str = '',
              secret: bool = False, multiline: bool = False) -> None: ...
    def toggle(self, name: str, label: str = '', value: bool = False) -> None: ...
    def select(self, name: str, options: list, label: str = '', value: Optional[str] = None) -> None: ...
    def button(self, label: str, on_click: Optional[Callable] = None, arg: Any = None, danger: bool = False) -> None: ...
    def row(self) -> Any: ...                       # with ui.row(): ...
    def section(self, title: str = '') -> Any: ...  # with ui.section("Заголовок"): ...
    def list(self, items: list) -> None: ...        # str или {'title': ..., 'text': ...}
    def table(self, headers: list, rows: list) -> None: ...


class _Ctx:
    """Аргумент обработчика кнопки (on_click)."""
    values: dict
    arg: Any
    state: dict
    def toast(self, text: Any, kind: str = 'info') -> None: ...   # kind: info / ok / error
    def set_value(self, name: str, value: Any) -> None: ...


class _Web:
    def page(self, title: str, icon: Optional[str] = None) -> Callable:
        """@web.page("Название", icon="✦") — функция async def f(ui: _Ui) станет страницей сайта."""
        ...


web: _Web
