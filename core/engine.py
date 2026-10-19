"""
core/engine.py

Core — центральный объект FRX. Хранит:
  - активный Telethon-клиент (+ мультиаккаунт через AccountManager)
  - БД (Database + DynamicDB)
  - реестр загруженных модулей и их хэндлеров
  - диспетчер событий Telegram (команды, вотчеры, telegram_event, callback)
  - все utils.py функции как методы (get_args, reply, wait_message, ...)
  - механизм load_module / unload_module / kill_module / reload_module
  - обработку ошибок (@on_error)

Модули сами импортируют то, что им нужно, обычным `import` — движок при
старте регистрирует нужные объекты в sys.modules под короткими именами
(engine, decorators, bot, visual, waiters, files, db, accounts, time_utils),
см. _register_module_imports(). `bot` (core/bot_decorators.py) — параллельный
к `decorators` набор для объявления хэндлеров СТАЦИОНАРНОГО бота прямо
внутри модулей юзербота (bot.command/bot.watcher/bot.on_start/bot.on_load/
bot.cycle/bot.telegram_event/bot.on_error, см. core/bot_decorators.py и
core/bot_events.py) — юзербота эти хэндлеры не затрагивают, это отдельные
реестры (self.bot_command_map/self.bot_watchers_bot/self.bot_watchers_userbot/
self.bot_telegram_events/self.bot_on_error_handlers ниже). Никакой магии с глобальными именами внутри
namespace модуля больше нет — _build_namespace() отдаёт практически пустое
пространство имён, и всё, чем пользуется модуль, должно быть явно
импортировано в его коде.
"""

import asyncio
import difflib
import html as _html
import os
import re
import sys
import time
import types
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import BotInlineDisabledError, MessageNotModifiedError
from telethon.tl.functions.contacts import UnblockRequest

from .context import current_event, current_module, current_emulation, current_guest
from .access import AccessManager, AliasManager, AccessError, command_key, danger_reasons
from .database import Database, DynamicDB
from .cache import MemoryCache, DynamicCache
from . import decorators as decorators_module
from . import bot_decorators as bot_decorators_module
from . import visual as visual_module
from . import time_utils as time_utils_module
from .time_utils import parse_interval
from .files import FileRegistry
from .waiters import Waiter, WaiterCancelled
from .accounts import AccountManager
from .inline_forms import (
    InlineForm, InlineClick, InputButton, SearchButton, InlineInput, TokenBucket, resolve_form_reply, search_items,
    PAGE_PREV, PAGE_NEXT, PAGE_NOOP, paginate_button_rows, render_page_rows,
    parse_button, normalize_style,
)
from .stationary_bot import BotFacade
from .inline_results import InlineResult, InlineQueryEvent, InlineQueryClick, link_preview_options
from .html_utils import safe_html, html_with_entities, install_telethon_emoji_tags, split_button_emoji
install_telethon_emoji_tags()    # parse_mode="html" у Telethon понимает и <emoji document_id=…>
from .module_meta import parse_module_meta, normalize_module_link, tg_username
from .command_event import CommandEvent
from .botlog import BotLogLimiter
from .inline_forms import picker_page
from .bot_events import BotMessageEvent, BotChatMemberEvent
from .web import WebPanel, ui as web_ui_module
from .botchat import BotChat
from . import paths as paths_module
from .paths import ASSETS_DIR, BUILTIN_DIR, FILES_DIR, MODULES_DIR, SESSION_PATH, UNLOADED_DIR, USER_ASSETS_DIR

# Версия ЯДРА FRX (не путать с VERSION отдельных модулей, см. ModuleInfo) —
# правится вручную при каждом обновлении движка. Показывается в .about
# (modules/builtin/Helper.py) и в баннере запуска (main.py).
FRX_VERSION = "1.0.2"

# Папки (подробно — core/paths.py):
#   ASSETS_DIR      — стоковые статичные файлы FRX (assets/ проекта, обновляются вместе с кодом);
#   USER_ASSETS_DIR — свои ассеты пользователя (user/assets/), имеют приоритет над стоковыми в asset_path();
#   LOADED_DIR      — пользовательские модули (user/modules/): что лежит в папке, то и грузится при старте;
#   UNLOADED_DIR    — выгруженные (.unloadmod) модули (user/modules/unloaded/): автоскан их не видит,
#                     .restoremods (engine.list_unloaded_modules / restore_module) возвращает назад.
LOADED_DIR = MODULES_DIR

# Маркер сообщений bot.inline_function() (см. core/bot_decorators.py) — то, что реально публикуется в чат как "via @бот", когда
# кто-то выбирает инлайн-результат вызова функции. Собирается ботом
# (aiobot/bot.py, handle_inline_query -> build_inline_function_text() ниже)
# и разбирается обратно юзерботом (Core._on_new_message, MTProto — только
# там есть настоящие chat_id/sender_id этого сообщения, см. докстринг
# bot.inline_function()). Единственное место, где живёт этот формат —
# чтобы сборка/разбор не разъезжались.
_INLINE_FUNCTION_MARKER = "⚙️"
_INLINE_FUNCTION_RE = re.compile(r'^\u2699\ufe0f?\s+(\S+)(?:\s+([\s\S]*))?$')


def build_inline_function_text(name, args):
    """Текст, который уйдёт в чат при выборе инлайн-результата bot.inline_function()."""
    return f"{_INLINE_FUNCTION_MARKER} {name} {args}".rstrip() if args else f"{_INLINE_FUNCTION_MARKER} {name}"


@dataclass
class ModuleInfo:
    module_name: str
    name: str
    version: str
    author: str
    description: str
    path: str
    source: str
    namespace: dict
    tg_url: str = ''                                       # TG_URL из шапки — канал автора (см. core/module_meta.py)
    git_url: str = ''                                      # GIT_URL из шапки — модуль на GitHub

    commands: dict = field(default_factory=dict)          # alias -> func
    watchers: list = field(default_factory=list)
    telegram_events: list = field(default_factory=list)
    callbacks: list = field(default_factory=list)
    on_error_handlers: list = field(default_factory=list)
    on_start_handlers: list = field(default_factory=list)
    on_load_handlers: list = field(default_factory=list)
    cycle_handlers: list = field(default_factory=list)     # [(func, interval), ...]
    cycle_tasks: list = field(default_factory=list)        # запущенные asyncio.Task
    # @decorators.on_account_switch() — см. Core.set_account()
    on_account_switch_handlers: list = field(default_factory=list)
    # страницы сайта (`import web`, @web.page) — [{'title', 'icon', 'func'}, ...]
    web_pages: list = field(default_factory=list)

    # --- хэндлеры СТАЦИОНАРНОГО бота, объявленные этим модулем через
    # `import bot` (core/bot_decorators.py) — параллельный, независимый
    # набор к полям выше (те — только про юзербота) ---
    bot_commands: dict = field(default_factory=dict)          # alias -> func
    bot_inline_functions: dict = field(default_factory=dict)  # alias -> func — bot.inline_function()
    bot_watchers: list = field(default_factory=list)
    bot_telegram_events: list = field(default_factory=list)
    bot_on_error_handlers: list = field(default_factory=list)
    bot_on_start_handlers: list = field(default_factory=list)
    bot_on_load_handlers: list = field(default_factory=list)
    bot_cycle_handlers: list = field(default_factory=list)    # [(func, interval), ...]
    bot_cycle_tasks: list = field(default_factory=list)
    # @decorators.on_edit / on_delete / on_unload и @bot.inline_query / inline_click / start_link
    edit_handlers: list = field(default_factory=list)
    delete_handlers: list = field(default_factory=list)
    on_unload_handlers: list = field(default_factory=list)
    bot_inline_queries: dict = field(default_factory=dict)    # name -> func
    bot_inline_clicks: list = field(default_factory=list)
    bot_start_links: list = field(default_factory=list)
    bot_callbacks: list = field(default_factory=list)

    @property
    def is_builtin(self) -> bool:
        """Встроенный ли модуль — лежит ли его файл в modules/builtin/ проекта (сравниваются пути целиком,
        поэтому пользовательский файл user/modules/builtin_x.py встроенным не считается)."""
        try:
            Path(self.path).resolve().relative_to(BUILTIN_DIR.resolve())
            return True
        except (ValueError, OSError):
            return False

    @property
    def kind(self) -> str:
        return 'builtin' if self.is_builtin else 'loaded'


def _caption(text):
    """Подпись к медиа — максимум 1024 символа (Bot API); длиннее — обрезаем по строкам, чтобы не разрезать HTML-тег."""
    text = text or ''
    if len(text) <= 1024:
        return text
    out = ''
    for line in text.split('\n'):
        if len(out) + len(line) + 2 > 1020:
            break
        out += line + '\n'
    return out.rstrip() + '\n…'


class Core:
    # Классы, которые модуль должен уметь ИМПОРТИРОВАТЬ (не только получать
    # готовые объекты в аргументах хэндлеров) — доступны как engine.InputButton
    # / engine.InlineInput / engine.InlineForm / engine.InlineClick, раз
    # `import engine` и так отдаёт сам объект Core (см. _register_module_imports).
    # InputButton нужен модулю, чтобы СОБРАТЬ buttons=[[engine.InputButton(...)]]
    # для create_inlineform()/form.edit() — см. core/inline_forms.py и README 16а.
    InputButton = InputButton
    SearchButton = SearchButton                        # «🔍 Поиск» для длинных списков, form.on_search()
    InlineInput = InlineInput
    InlineForm = InlineForm
    InlineClick = InlineClick

    # утилиты, доступные модулям как engine.<имя>
    picker_page = staticmethod(picker_page)            # постраничный выбор (по 7), см. core/inline_forms.py
    search_items = staticmethod(search_items)          # поиск по списку для SearchButton, см. core/inline_forms.py
    safe_html = staticmethod(safe_html)                # экранирование НЕ-тегов в HTML, см. core/html_utils.py
    parse_module_meta = staticmethod(parse_module_meta)  # статический разбор исходника модуля, см. core/module_meta.py

    def __init__(self, config: dict):
        self.config = config
        self.version = FRX_VERSION
        self.api_id = config.get('api_id')
        self.api_hash = config.get('api_hash')

        self.db = Database(paths_module.resolve_db_path(config.get('db_path')))
        self.db_view = DynamicDB(self.db)
        self.cache_storage = MemoryCache()          # in-memory, без диска — живёт до перезапуска
        self.cache_view = DynamicCache(self.cache_storage)
        self.file_registry = FileRegistry(self.db)
        from .llm import get_llm                  # нейросети — engine.llm (подключения, модели, чат, расход) и engine.neuro (агент с инструментами)
        from .neuro import get_agent
        self.llm = get_llm()
        self.neuro = get_agent()
        self.accounts = AccountManager(self.api_id, self.api_hash)
        # локальный сайт (core/web/) — поднимается командой .web, см. modules/builtin/Web.py
        self.web = WebPanel(self)
        # BotChat — «аккаунт» стационарного бота на сайте (core/botchat.py, modules/builtin/BotChat.py)
        self.botchat = BotChat(self)
        # доступ гостей к командам и алиасы команд (core/access.py, modules/builtin/Access.py)
        self.access = AccessManager(self.db)
        self.aliases = AliasManager(self.db)
        self._guest_replies: dict = {}               # (chat_id, msg_id) команды гостя -> наш ответ (answer() правит его)

        self.client: TelegramClient | None = None

        # стационарный бот (AIOgram 3) — устанавливается aiobot.bot.create_bot()
        # ПОСЛЕ создания Bot/Dispatcher, вызовом core.set_bot(bot, dp, ...)
        # изнутри самой create_bot() (main.py просто вызывает create_bot()).
        # self.bot —
        # дружелюбная обёртка (core/stationary_bot.py, engine.bot.send_message/
        # reply/edit); "сырой" aiogram Bot лежит в self._aiogram_bot и
        # используется внутренней машинерией инлайн-форм.
        self._aiogram_bot = None
        self._aiogram_dp = None                       # aiogram Dispatcher — нужен модульным bot.*-хэндлерам
        self.bot = BotFacade(self)
        self._bot_username = None
        self._bot_id = None                          # кэш id стационарного бота, см. _get_bot_id()
        self._owner_id = None                        # (await client.get_me()).id, кешируется в start()
        self._prefix_cache = (self.config or {}).get('prefix', '.') if isinstance(self.config, dict) else '.'   # последний известный префикс — для синхронного get_args()
        self._dialog_lock = asyncio.Lock()            # bot_send_to_owner() -> _open_bot_dialog()
        self._bot_ready = False                       # True после set_bot(bot, dp) — "все инициализации" завершены
        # лимитер engine.botlog() (core/botlog.py)
        self._botlog = BotLogLimiter(self._botlog_deliver, self._botlog_settings, self.tlog)
        self._bot_reserved_aliases: set = set()        # алиасы "сырых" @bot.command(...) внутри aiobot/bot.py

        # --- реестры хэндлеров СТАЦИОНАРНОГО бота, объявленных модулями
        # юзербота через `import bot` (core/bot_decorators.py) ---
        self.bot_command_map: dict[str, tuple] = {}    # alias -> (module_name, func, access, for_user, for_chat, black_list, black_chats)
        self.bot_inline_function_map: dict[str, tuple] = {}  # alias -> (module_name, func, access, for_user, for_chat, black_list, black_chats) — bot.inline_function()
        self.bot_inline_query_map: dict[str, tuple] = {}    # name -> (module_name, func, access, for_user, for_chat, black_list, black_chats)
        self.bot_inline_clicks: list = []                # [(module_name, prefix, func, access), ...]
        self.bot_callbacks: list = []                    # @bot.callback модулей: [(module_name, prefix, func, access), ...]
        self.bot_start_links: list = []                  # [(module_name, prefix, func), ...]
        self.edit_handlers: list = []                    # [(module_name, func), ...] — @decorators.on_edit
        self.delete_handlers: list = []                  # [(module_name, func), ...] — @decorators.on_delete
        self.bot_watchers_bot: list = []                # [(module_name, func, access, watch), ...] — кормятся aiogram-сообщениями
        self.bot_watchers_userbot: list = []            # [(module_name, func, access, watch), ...] — кормятся Telethon-событиями
        self.bot_telegram_events: list = []             # [(module_name, func), ...]
        self.bot_on_error_handlers: list = []            # [(module_name, func), ...]
        self._bot_watch_dedup: dict = {}                 # (id(func), chat_id, message_id) -> monotonic timestamp

        # инлайн-формы (core/inline_forms.py)
        self._pending_forms: dict[str, dict] = {}     # form_id -> {'future', 'chat_id'}
        self._active_forms: dict[int, InlineForm] = {}  # inline_message_id -> InlineForm
        self._member_cache: dict[tuple, tuple] = {}    # (chat_id, user_id) -> (bool, expires_at)
        self._click_bucket = TokenBucket(rate=5)        # обновляется modules/builtin/InlineStuff.py

        # кнопки "Введите значение" (InputButton, core/inline_forms.py) —
        # user_id -> {'form', 'data', 'chat_id', 'created_at'}; см.
        # _register_input_targets()/dispatch_inline_input() ниже
        self._pending_inputs: dict[int, dict] = {}

        # сообщения, которые Telegram-клиент сам отправил в чат при выборе
        # инлайн-результата InputButton ("эхо" введённого значения) — их
        # надо тихо удалить, как только они долетят до _on_new_message();
        # chat_id -> [{'value': str, 'created_at': float}, ...], см.
        # _register_input_echo_for_deletion()/_handle_bot_echo_message()
        self._pending_input_deletes: dict[int, list] = {}

        # обратный буфер той же самой гонки (Bot API vs MTProto, см.
        # комментарий у _register_input_echo_for_deletion) — "via-bot"
        # сообщения, которые ЕЩЁ НЕ нашли себе пару в _pending_input_deletes
        # (chosen_inline_result мог просто не успеть прийти); chat_id ->
        # [{'text': str, 'message_id': int, 'seen_at': float}, ...]
        self._recent_bot_echoes: dict[int, list] = {}

        self.modules: dict[str, ModuleInfo] = {}
        # какие ключи конфига реально запросил (через create_config())
        # каждый модуль за это подключение движка — module_name -> {key, ...}.
        # Используется в _cleanup_stale_configs(), чтобы понять, какие ключи,
        # лежащие в БД под данным модулем, больше не объявлены в его коде.
        self._declared_config_keys: dict[str, set] = {}
        self.command_map: dict[str, tuple] = {}     # alias -> (module_name, func)
        self.watchers: list = []                    # [(module_name, func), ...]
        self.telegram_events: list = []
        self.callbacks: list = []
        self.on_error_handlers: list = []
        # [(module_name, func), ...] — см. decorators.on_account_switch()
        self.on_account_switch_handlers: list = []

        self._started = False

        # запрос на остановку процесса (см. request_shutdown()) —
        # main.py ждёт этот event, корректно закрывает клиентов и выходит с
        # кодом exit_code; дальше решает frx_systemkeeper.py (перезапуск,
        # обновление, остановка).
        self.exit_code = 0
        self._exit_event = asyncio.Event()

        self._register_module_imports()

    # ------------------------------------------------------------------ #
    # Регистрация "import engine" / "import visual" / ... для модулей
    # ------------------------------------------------------------------ #

    def _register_module_imports(self):
        """
        Кладёт в sys.modules объекты, которые модуль FRX может забрать себе
        обычным `import`, вместо того чтобы движок подсовывал их как готовые
        глобальные имена. Все функции, которые зависят от текущего аккаунта/
        клиента/модуля (current_module, current_event), — это связанные
        (bound) методы Core, поэтому они всегда читают актуальное состояние
        в момент вызова, а не в момент регистрации.

            import engine       -> answer/reply/get_args/load_module/... (сам Core)
            import decorators   -> command/watcher/on_start/.../on_error
            import bot          -> ТЕ ЖЕ ИМЕНА (command/watcher/on_start/on_load/
                                    cycle/telegram_event/on_error), но хэндлеры
                                    для СТАЦИОНАРНОГО бота — см. core/bot_decorators.py
            import visual       -> visual()/visual_edit()/visual_list()
            import waiters      -> wait_message() (async-for ИЛИ обычный await, см. core/waiters.py)
            import files        -> download_reply_file() — file= теперь есть у answer/reply/noreply/send_message
            import db           -> get()/set()/delete()/gget()/gset()/list()/glist() (персистентно, JSON)
            import cache        -> тот же интерфейс, что и db, но in-memory (до перезапуска)
            import accounts     -> add_account()/set_account()/delete_account()/account_list()/
                                    account_list_full()/account_info()/set_tag()/remove_tag()/
                                    get_tags()/refresh_info()/new_login()/
                                    find_forgotten()/restore_forgotten()
            import time_utils   -> get_time()/time_format()/edit_time()
        """
        # engine — сам объект Core: в нём уже есть answer/reply/get_args/
        # load_module/create_config/get_prefix/tlog/client и т.д.
        sys.modules['engine'] = self

        # decorators / visual / time_utils ничего не знают про конкретный
        # Core (стейтлесс), поэтому их можно отдать как есть.
        sys.modules['decorators'] = decorators_module
        sys.modules['bot'] = bot_decorators_module
        sys.modules['visual'] = visual_module
        sys.modules['time_utils'] = time_utils_module
        # `import web` -> @web.page(...) и интерфейс страниц сайта (core/web/ui.py)
        sys.modules['web'] = web_ui_module

        # db — тот же объект, что видят db.get/db.set с автоподстановкой
        # префикса текущего модуля.
        sys.modules['db'] = self.db_view

        # cache — то же самое, но in-memory (см. core/cache.py) — значения
        # не пишутся на диск и стираются при перезапуске юзербота.
        sys.modules['cache'] = self.cache_view

        waiters_module = types.ModuleType('waiters')
        waiters_module.wait_message = self.wait_message
        waiters_module.WaiterCancelled = WaiterCancelled
        sys.modules['waiters'] = waiters_module

        files_module = types.ModuleType('files')
        files_module.download_reply_file = self.download_reply_file
        files_module.get_file = self.file_registry.get
        files_module.delete_file = self.file_registry.delete
        files_module.create_file = self.file_registry.create
        sys.modules['files'] = files_module

        from . import media_tools as media_module                     # `import media` — ffmpeg и Pillow для модулей
        sys.modules['media'] = media_module

        accounts_module = types.ModuleType('accounts')
        accounts_module.add_account = self.accounts.add_account
        accounts_module.set_account = self.set_account
        accounts_module.delete_account = self.accounts.delete_account
        accounts_module.account_list = self.accounts.account_list
        accounts_module.account_list_full = self.accounts.account_list_full
        accounts_module.account_info = self.accounts.account_info
        accounts_module.set_tag = self.accounts.set_tag
        accounts_module.remove_tag = self.accounts.remove_tag
        accounts_module.get_tags = self.accounts.get_tags
        accounts_module.refresh_info = self.accounts.refresh_account_info
        accounts_module.new_login = self.accounts.new_login
        accounts_module.find_forgotten = self.accounts.find_forgotten
        accounts_module.restore_forgotten = self.accounts.restore_forgotten
        sys.modules['accounts'] = accounts_module

    def set_bot(self, bot, dp=None, reserved_aliases=None):
        """
        Подключает стационарный aiogram-бот (и, если он есть,
        Dispatcher) к движку — вызывается из aiobot.bot.create_bot() сразу
        после создания Bot/Dispatcher. Раньше этого вызова engine.bot.* и
        инлайн-формы (create_inlineform) недоступны.

        dp — нужен, чтобы модульные bot.*-хэндлеры (core/bot_decorators.py,
        `import bot` внутри модуля юзербота) вообще начали получать
        события: без dp сам стационарный бот работает (engine.bot.*,
        create_inlineform), но bot.command()/bot.watcher(watch='bot'/'all')/
        bot.telegram_event() из модулей молчат.

        reserved_aliases — алиасы, уже занятые "сырыми" @bot.command(...)
        хэндлерами прямо внутри aiobot/bot.py (например, 'start'/'status')
        — если какой-то модуль успел заранее зарегистрировать команду с
        тем же именем (bot ещё не был подключён на момент загрузки модуля
        — типичный случай при старте, см. main.py), она будет молча
        перекрыта встроенной, залогируем это тут.

        Идемпотентен: повторный вызов после первого успешного подключения
        (dp уже был передан) ничего не переделывает — иначе
        bot.on_start()/bot.cycle() запустились бы повторно.
        """
        if self._bot_ready and dp is not None:
            self._aiogram_bot = bot
            return

        self._aiogram_bot = bot
        self._aiogram_dp = dp
        self._bot_reserved_aliases = set(reserved_aliases or ())

        for alias in self.bot_command_map:
            if alias in self._bot_reserved_aliases:
                owner_module = self.bot_command_map[alias][0]
                self.tlog(
                    f"[FRX] Команда бота '/{alias}' из модуля '{owner_module}' "
                    f"конфликтует со встроенной командой aiobot/bot.py — "
                    f"встроенная имеет приоритет, модульная не будет вызвана"
                )

        if dp is not None:
            self._bot_ready = True
            # "После всех инициализаций": юзербот к этому моменту уже
            # полностью стартовал (core.start() отработал раньше него,
            # см. main.py), а стационарный бот только что подключился —
            # самое время bot.on_start()/bot.cycle() у ВСЕХ уже
            # загруженных модулей.
            asyncio.create_task(self._fire_bot_on_start())

    async def _fire_bot_on_start(self):
        for info in list(self.modules.values()):
            mtoken = current_module.set(info.module_name)
            try:
                for func in info.bot_on_start_handlers:
                    await self._safe_call_bot(info.module_name, func)
                for func, interval in info.bot_cycle_handlers:
                    task = asyncio.create_task(self._run_bot_cycle(info.module_name, func, interval))
                    info.bot_cycle_tasks.append(task)
            finally:
                current_module.reset(mtoken)

    async def _run_bot_cycle(self, module_name, func, interval):
        seconds = parse_interval(interval)
        while True:
            try:
                await asyncio.sleep(seconds)
                mtoken = current_module.set(module_name)
                try:
                    await func()
                finally:
                    current_module.reset(mtoken)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._dispatch_bot_error(module_name, func, e)

    # ------------------------------------------------------------------ #
    # Диспетчер событий СТАЦИОНАРНОГО бота (модульные bot.*-хэндлеры)
    # ------------------------------------------------------------------ #

    def _bot_access_ok(self, access, user_id):
        if access == 'public':
            return True
        return user_id is not None and user_id == self._owner_id

    @staticmethod
    def _as_id_list(value):
        """None остаётся None (= "не задано"); одиночный id оборачивается в список."""
        if value is None:
            return None
        return value if isinstance(value, list) else [value]

    def _bot_extended_access_ok(self, access, for_user, for_chat, black_list,
                                 black_chats, user_id, chat_id):
        """
        Общая проверка для bot.command()/bot.inline_function() (см. их
        докстринги в core/bot_decorators.py) — сначала access ('self'/
        'public'), потом for_user/for_chat/black_list/black_chats:
            - black_list/black_chats — запрещают доступ ВСЕГДА
            - ничего из for_user/for_chat не задано -> разрешено всем/всюду
            - задан только for_user  -> user_id должен входить в список
            - задан только for_chat  -> chat_id должен входить в список
            - заданы оба             -> должны совпасть ОБА
        """
        if not self._bot_access_ok(access, user_id):
            return False

        black_users = self._as_id_list(black_list) or []
        black_chats_ = self._as_id_list(black_chats) or []
        if user_id is not None and user_id in black_users:
            return False
        if chat_id is not None and chat_id in black_chats_:
            return False

        allowed_users = self._as_id_list(for_user)
        allowed_chats = self._as_id_list(for_chat)
        if allowed_users is not None and allowed_chats is not None:
            return user_id in allowed_users and chat_id in allowed_chats
        if allowed_users is not None:
            return user_id in allowed_users
        if allowed_chats is not None:
            return chat_id in allowed_chats
        return True

    def _bot_dedup_seen(self, func, chat_id, message_id, ttl=15):
        """
        Дедупликация для watcher(watch='all'): если ОБА наблюдателя
        (стационарный бот и юзербот-аккаунт) состоят в одном чате и оба
        поймали одно и то же сообщение, id чата/сообщения совпадают между
        Bot API и MTProto (гарантия самого Telegram для групп/супергрупп/
        каналов) — по этой паре и дедуплицируем, отдельно на каждый
        конкретный handler (func), чтобы разные watch='all' хэндлеры не
        мешали друг другу. Возвращает True, если это ПОВТОРНОЕ попадание
        (значит, звать функцию не нужно), и попутно сама регистрирует
        первое попадание.
        """
        key = (id(func), chat_id, message_id)
        now = time.monotonic()
        seen_at = self._bot_watch_dedup.get(key)
        if seen_at is not None and now - seen_at < ttl:
            return True
        self._bot_watch_dedup[key] = now
        if len(self._bot_watch_dedup) > 2000:
            cutoff = now - ttl
            self._bot_watch_dedup = {k: v for k, v in self._bot_watch_dedup.items() if v >= cutoff}
        return False

    async def _safe_call_bot(self, module_name, func, *args, **kwargs):
        # См. комментарий в _safe_call() выше — та же причина: контекст
        # модуля выставляется здесь один раз, централизованно, для ЛЮБОГО
        # пути вызова bot.*-хэндлеров.
        mtoken = current_module.set(module_name)
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            await self._dispatch_bot_error(module_name, func, e)
            return None
        finally:
            current_module.reset(mtoken)

    async def _dispatch_bot_error(self, module_name, func, exc: Exception):
        text = f"{type(exc).__name__}: {exc}"
        context = {'module': module_name, 'function': getattr(func, '__name__', str(func))}
        self.tlog(f"[BOT ERROR] модуль={module_name} функция={context['function']}: {text}")
        for m, handler in list(self.bot_on_error_handlers):
            mtoken = current_module.set(m)
            try:
                await handler(text, context)
            except Exception as inner:
                self.tlog(f"[BOT ERROR] обработчик ошибок бота модуля '{m}' сам упал: {inner}")
            finally:
                current_module.reset(mtoken)

    async def _run_bot_handler(self, module_name, func, event):
        mtoken = current_module.set(module_name)
        etoken = current_event.set(event)
        try:
            await self._safe_call_bot(module_name, func, event)
        finally:
            current_event.reset(etoken)
            current_module.reset(mtoken)

    async def dispatch_bot_message(self, message):
        """
        Вызывается aiobot/bot.py на КАЖДОЕ входящее сообщение стационарного
        бота (см. handle_module_bot_messages в aiobot/bot.py) — аналог
        Core._on_new_message() для юзербота, только со стороны aiogram.
        Сначала пробуем команду (self.bot_command_map, префикс всегда '/'),
        затем — все bot.watcher(watch='bot'/'all') (вотчеры видят вообще
        всё, включая команды — как и у юзербота).
        """
        event = BotMessageEvent(message, self, origin='bot')
        user_id = message.from_user.id if message.from_user else None
        text = event.raw_text

        if text.startswith('/'):
            cmd = text[1:].split(maxsplit=1)[0].split('@')[0].lower()
            entry = self.bot_command_map.get(cmd)
            if entry is not None:
                module_name, func, access, for_user, for_chat, black_list, black_chats = entry
                if self._bot_extended_access_ok(
                    access, for_user, for_chat, black_list, black_chats, user_id, event.chat_id
                ):
                    await self._run_bot_handler(module_name, func, event)

        for module_name, func, access, watch in list(self.bot_watchers_bot):
            if not self._bot_access_ok(access, user_id):
                continue
            if watch == 'all' and self._bot_dedup_seen(func, event.chat_id, message.message_id):
                continue
            await self._run_bot_handler(module_name, func, event)

    async def dispatch_bot_telegram_event(self, update, is_bot_event: bool):
        """
        Вызывается aiobot/bot.py на my_chat_member (is_bot_event=True — про
        самого бота) и chat_member (is_bot_event=False — про других
        участников чата) — см. bot.telegram_event() в core/bot_decorators.py.
        """
        event = BotChatMemberEvent(update, self, is_bot_event=is_bot_event)
        for module_name, func in list(self.bot_telegram_events):
            await self._run_bot_handler(module_name, func, event)

    # ------------------------------------------------------------------ #
    # Запуск юзербота
    # ------------------------------------------------------------------ #

    async def start(self):
        await self.access.load()
        await self.aliases.load()
        self.client = await self.accounts.get_active_client()
        if self.client is None:
            self.client = TelegramClient(str(SESSION_PATH), self.api_id, self.api_hash)
            await self.client.connect()
            if not await self.client.is_user_authorized():
                if os.environ.get('FRX_ANDROID'):
                    # приложение для Android: консоли нет, вход — на экране приложения («Войти заново»)
                    print("[FRX] Сессия Telegram недействительна или её нет — войдите заново в приложении FRX")
                    raise SystemExit(78)
                # первичный вход — ТОЛЬКО через консоль (Telethon спросит номер,
                # код и, если включена, 2FA прямо в терминале). Так надёжнее, когда юзербот
                # стоит на внешнем сервере; сайт (.web) — локальный и работает уже после входа.
                print("[FRX] Вход в Telegram: введите номер телефона, затем код (и пароль 2FA, если включён)")
                await self.client.start()

        self._owner_id = (await self.client.get_me()).id
        self._attach_handlers(self.client)

        # embedded (встроенные) модули — идут "в комплекте", папка modules/builtin
        await self._load_folder(BUILTIN_DIR, target='builtin')

        # user/modules/ — единственный источник правды о загруженных
        # модулях (что лежит в папке — то и грузится; выгруженные лежат в
        # user/modules/unloaded/ и автоскан их не видит). Персистентный
        # список core:loaded_modules больше не "кормит" загрузку — он лишь
        # индекс, который тут же приводится к реальности: записи модулей, файлов
        # которых нет, молча удаляются (раньше на каждом старте — "файл не найден").
        await self._migrate_legacy_disabled()
        old_list = await self.db.get('core:loaded_modules', [])
        failed = 0
        if LOADED_DIR.exists():
            for file in sorted(LOADED_DIR.glob('*.py')):
                if file.name.startswith('_'):
                    continue
                try:
                    src = file.read_text(encoding='utf-8')
                    # модуль стал встроенным (NeuroDash) — старую загруженную копию не грузим, чтобы не было двойника
                    meta = parse_module_meta(src)
                    if meta and meta['module_name'] in self.modules and self.modules[meta['module_name']].is_builtin:
                        file.replace(file.with_name(file.name + '.bak'))
                        print(f"[FRX] {file.name}: модуль «{meta['module_name']}» теперь встроенный — старая копия переименована в {file.name}.bak")
                        continue
                    await self._load_from_source(src, target='loaded', source_path=str(file))
                except Exception as e:
                    failed += 1
                    print(f"[FRX] Ошибка загрузки модуля из {file}: {e}")
        actual = [n for n, i in self.modules.items() if not i.is_builtin]
        ghosts = [n for n in old_list if n not in actual]
        if actual != old_list:
            await self.db.set('core:loaded_modules', actual)
        if ghosts and not failed:
            print(f"[FRX] Убраны из списка загруженных модули без файлов: {', '.join(ghosts)}")

        self._started = True

        # on_start всех уже загруженных модулей
        for info in list(self.modules.values()):
            mtoken = current_module.set(info.module_name)
            try:
                for func in info.on_start_handlers:
                    await self._safe_call(info.module_name, func)
            finally:
                current_module.reset(mtoken)

        # подчищаем в БД конфиги, оставшиеся от версий модулей,
        # где они назывались/были объявлены иначе, чем сейчас в коде —
        # ВАЖНО делать это именно здесь, а не раньше: create_config() может
        # вызываться и из on_load, и из on_start (см. modules/builtin/Config.py),
        # оба этапа к этому моменту уже отработали для всех модулей.
        await self._cleanup_stale_configs()

        print(f"[FRX] Юзербот запущен. Загружено модулей: {len(self.modules)}")

    def _attach_handlers(self, client):
        """
        Вешает диспетчерские обработчики движка на client.

        Идемпотентно: сначала снимает эти же обработчики (remove_event_handler
        безопасен, даже если их ещё не было — просто вернёт 0), и только
        потом вешает заново. Это важно, потому что AccountManager кэширует
        уже созданные TelegramClient в self.clients — при повторном
        переключении на аккаунт, на котором юзербот уже был, сюда придёт ТОТ
        ЖЕ объект client, что и раньше. Без снятия старых обработчиков
        add_event_handler просто добавлял бы вторую (третью, ...) копию при
        каждом переключении туда-обратно, и все @command/@watcher/... начинали
        бы срабатывать по несколько раз на одно и то же событие.
        """
        from .apilog import wrap_client          # запросы к Telegram во время Emulate видны на сайте
        wrap_client(client)
        # Разметка клиента — HTML, как у engine.answer() и в Hikka: message.text — HTML с тегами и премиум-эмодзи
        # (<tg-emoji emoji-id="…">), message.raw_text — простой текст с обычными эмодзи; event.edit/reply/
        # client.send_message без parse_mode тоже понимают HTML (и <emoji document_id=…>).
        client.parse_mode = 'html'
        client.remove_event_handler(self._on_new_message, events.NewMessage())
        client.remove_event_handler(self._on_chat_action, events.ChatAction())
        client.remove_event_handler(self._on_callback, events.CallbackQuery())
        client.remove_event_handler(self._on_edit, events.MessageEdited())
        client.remove_event_handler(self._on_delete, events.MessageDeleted())

        client.add_event_handler(self._on_new_message, events.NewMessage())
        client.add_event_handler(self._on_chat_action, events.ChatAction())
        client.add_event_handler(self._on_callback, events.CallbackQuery())
        client.add_event_handler(self._on_edit, events.MessageEdited())
        client.add_event_handler(self._on_delete, events.MessageDeleted())
        try:                                             # BotChat «cross watch» (группы глазами аккаунта)
            self.botchat.attach_client(client)
        except Exception as e:
            print(f"[FRX] [WARN] BotChat: cross watch не подключён: {e}")

    async def _load_folder(self, folder, target='loaded'):
        folder_path = Path(folder)
        if not folder_path.exists():
            return
        for file in sorted(folder_path.glob('*.py')):
            if file.name.startswith('_'):
                continue
            try:
                await self._load_from_source(file.read_text(encoding='utf-8'), target=target, source_path=str(file))
            except Exception as e:
                print(f"[FRX] Ошибка загрузки модуля из {file}: {e}")

    # ------------------------------------------------------------------ #
    # Диспетчер событий Telegram
    # ------------------------------------------------------------------ #

    async def _on_new_message(self, event):
        token = current_event.set(event)
        try:
            # "эхо" значения, введённого через InputButton (core/inline_forms.py,
            # README «Инлайн-формы» → InputButton) — Telegram-клиент сам публикует его в чат при
            # выборе инлайн-результата; форма уже получила значение через
            # dispatch_inline_input(), само сообщение — просто мусор, удаляем
            # его молча и дальше это событие не разбираем (не команда/вотчер).
            #
            # ВАЖНО: это сообщение прилетает через MTProto (сюда, в telethon),
            # а chosen_inline_result — отдельным каналом через Bot API, и эти
            # два события НЕ гарантированно приходят в одном порядке (Bot API
            # обычно медленнее) — поэтому проверяем "нет ли уже готового
            # совпадения" ДАЖЕ если pending_input_deletes сейчас пуст: сама
            # проверка/буферизация живёт в _handle_bot_echo_message().
            if event.out:
                try:
                    via_bot_id = getattr(event.message, 'via_bot_id', None)
                    if via_bot_id is not None and via_bot_id == await self._get_bot_id():
                        if await self._handle_bot_echo_message(event):
                            return
                except Exception as e:
                    self.tlog(f"Не удалось проверить/удалить эхо InputButton: {e}")

            # bot.inline_function() (core/bot_decorators.py) —
            # сообщение опубликовано "via @бот" (у него via_bot_id ==
            # id стационарного бота), но, в отличие от эха InputButton
            # выше, тут проверяем ЛЮБОЕ сообщение (event.out True ИЛИ
            # False — функцию мог вызвать не только владелец юзербота, а
            # вообще любой участник чата, где юзербот состоит). Найдено
            # совпадение по _INLINE_FUNCTION_RE и зарегистрированной
            # инлайн-функции — дальше это сообщение не разбираем ни как
            # команду, ни отдаём вотчерам (return), не совпало — работаем
            # с ним как с обычным сообщением дальше по коду.
            if self.bot_inline_function_map and self._aiogram_bot is not None:
                try:
                    via_bot_id = getattr(event.message, 'via_bot_id', None)
                    if via_bot_id is not None and via_bot_id == await self._get_bot_id():
                        m = _INLINE_FUNCTION_RE.match(event.raw_text or '')
                        if m and m.group(1).lower() in self.bot_inline_function_map:
                            handled = await self._dispatch_inline_function_message(
                                event, m.group(1), (m.group(2) or '').strip(),
                            )
                            if handled:
                                return
                except Exception as e:
                    self.tlog(f"Не удалось обработать вызов bot.inline_function(): {e}")

            prefix = await self.get_prefix()
            text = event.raw_text or ''

            if text.startswith(prefix) and (event.out or self.access.has_users()):
                await self._dispatch_command(event, text, prefix)

            # вотчеры видят вообще ВСЕ сообщения, включая команды
            for module_name, func in list(self.watchers):
                mtoken = current_module.set(module_name)
                try:
                    await self._safe_call(module_name, func, event)
                finally:
                    current_module.reset(mtoken)

            # bot.watcher(watch='userbot'/'all') из модулей (core/bot_decorators.py,
            # `import bot`) — отдельный, независимый от self.watchers поток;
            # ошибки уходят в _dispatch_bot_error, а не в обычный on_error
            # юзербота. Событие тут — НАСТОЯЩИЙ telethon-event, не обёртка.
            if self.bot_watchers_userbot:
                try:
                    event.origin = 'userbot'
                except Exception:
                    pass
                for module_name, func, access, watch in list(self.bot_watchers_userbot):
                    if not self._bot_access_ok(access, event.sender_id):
                        continue
                    if watch == 'all' and self._bot_dedup_seen(func, event.chat_id, event.message.id):
                        continue
                    await self._run_bot_handler(module_name, func, event)
        finally:
            current_event.reset(token)

    # ------------------------------------------------------------------ #
    # Команды: владелец, гости (core/access.py) и алиасы
    # ------------------------------------------------------------------ #

    async def _access_settings(self) -> dict:
        async def cfg(key, default):
            try:
                value = await self.get_config(key, module_name='access')
            except KeyError:
                return default
            return default if value is None else value
        return {
            'cooldown': float(await cfg('guest_cooldown', 1.0)),
            'log': bool(await cfg('log_guest_commands', True)),
        }

    async def _dispatch_command(self, event, text, prefix):
        """
        Сообщение начинается с префикса. Исходящее — команда владельца; входящее — команда гостя, если у
        автора есть доступ (core/access.py). Неизвестное слово ищется среди алиасов: команда запускается
        с событием-прокси (CommandEvent), у которого текст — «<префикс><команда> <аргументы алиаса> <дописанное>».
        """
        rest = text[len(prefix):].strip()
        if not rest:
            return
        parts = rest.split(maxsplit=1)
        cmd = parts[0].lower()
        run_event = event
        # «. cmd аргументы» — пробелы между префиксом и командой: модуль получает текст «.cmd аргументы»,
        # иначе engine.get_args() и модули, читающие event.raw_text сами, приняли бы команду за аргумент
        if text[len(prefix):len(prefix) + 1].isspace():
            run_event = CommandEvent(event, prefix + text[len(prefix):].lstrip())
        entry = self.command_map.get(cmd)
        if entry is None:
            expanded = self.aliases.expand(cmd, parts[1] if len(parts) > 1 else '') if self.aliases._data is not None else None
            if expanded is None:
                return
            target, args = expanded
            entry = self.command_map.get(target)
            if entry is None:
                if event.out:
                    self.tlog(f"[alias] {prefix}{cmd} ведёт на {prefix}{target}, но такой команды больше нет")
                return
            run_event = CommandEvent(event, f"{prefix}{target} {args}".rstrip())
        module_name, func = entry

        guest = None
        if not event.out:
            guest = event.sender_id
            if guest is None or guest == self._owner_id or not self.access.get_user(guest):
                return
            st = await self._access_settings()
            info = self.modules.get(module_name)
            ok, why = self.access.check(guest, module_name, command_key(module_name, func),
                                        bool(info and info.is_builtin))
            who = self.access.get_user(guest).get('name') or str(guest)
            if not ok:                        # не выдано — молча игнорируем (гость не узнаёт, какие команды есть)
                if st['log']:
                    self.tlog(f"[access] {who} ({guest}): {prefix}{cmd} — нет доступа, проигнорировано ({why})")
                return
            if not self.access.cooldown_ok(guest, st['cooldown']):
                return
            if st['log']:
                self.tlog(f"[access] {who} ({guest}): {prefix}{cmd} ({why})")

        etoken = current_event.set(run_event)
        gtoken = current_guest.set(guest)
        try:
            await self._safe_call(module_name, func, run_event)
        finally:
            current_guest.reset(gtoken)
            current_event.reset(etoken)

    def guest_id(self):
        """id гостя, по чьей команде (или клику в форме) сейчас выполняется код; None — владелец."""
        return current_guest.get()

    def module_danger(self, module_name) -> list:
        """Почему доступ гостя к модулю критически опасен ([] — не опасен). См. core/access.py."""
        info = self.modules.get(module_name)
        if info is None:
            return []
        return danger_reasons(module_name, info.source or '', info.is_builtin)

    def _guest_guard(self, action: str, *owners: str):
        """
        Действия над самим юзерботом (модули, аккаунты, перезапуск, префикс) по команде гостя разрешены, только
        если их выполняет код модуля-владельца действия (его команду гостю уже разрешили) или у гостя есть
        доступ к такому модулю целиком. Так `.dlm`, выданная гостю, работает, а чужой модуль не сможет
        загрузить код от имени гостя без доступа к loader.
        """
        guest = current_guest.get()
        if guest is None:
            return
        if current_module.get() in owners:
            return
        for name in owners:
            info = self.modules.get(name)
            if info is not None and self.access.module_allowed(guest, name, info.is_builtin)[0]:
                return
        raise PermissionError(f"{action}: нужен доступ к модулю {' / '.join(owners)}")

    async def _on_chat_action(self, event):
        for module_name, func in list(self.telegram_events):
            mtoken = current_module.set(module_name)
            try:
                await self._safe_call(module_name, func, event)
            finally:
                current_module.reset(mtoken)

    async def _on_edit(self, event):
        for module_name, func in list(self.edit_handlers):
            await self._safe_call(module_name, func, event)

    async def _on_delete(self, event):
        for module_name, func in list(self.delete_handlers):
            await self._safe_call(module_name, func, event)

    async def _on_callback(self, event):
        for module_name, func in list(self.callbacks):
            mtoken = current_module.set(module_name)
            try:
                await self._safe_call(module_name, func, event)
            finally:
                current_module.reset(mtoken)

    async def _run_cycle(self, module_name, func, interval):
        seconds = parse_interval(interval)
        while True:
            try:
                await asyncio.sleep(seconds)
                mtoken = current_module.set(module_name)
                try:
                    await func()
                finally:
                    current_module.reset(mtoken)
            except asyncio.CancelledError:
                break
            except Exception as e:
                await self._dispatch_error(module_name, func, e)

    # ------------------------------------------------------------------ #
    # Обработка ошибок (@on_error)
    # ------------------------------------------------------------------ #

    async def _safe_call(self, module_name, func, *args, **kwargs):
        # ВАЖНО: current_module выставляется именно здесь, а не у каждого
        # вызывающего кода по отдельности — иначе легко забыть обернуть
        # какой-то новый путь вызова (как это было с кликами/вводом
        # инлайн-форм, core/inline_forms.py: InlineForm._dispatch /
        # _dispatch_input вызывали _safe_call вообще без current_module.set,
        # из-за чего db/cache внутри @form.on_click теряли контекст модуля).
        # Раз ВСЕ хэндлеры модулей в итоге проходят через _safe_call —
        # единственное надёжное место для этого именно тут.
        mtoken = current_module.set(module_name)
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            await self._dispatch_error(module_name, func, e)
            return None
        finally:
            current_module.reset(mtoken)

    async def _dispatch_error(self, module_name, func, exc: Exception):
        text = f"{type(exc).__name__}: {exc}"
        context = {'module': module_name, 'function': getattr(func, '__name__', str(func))}
        self.tlog(f"[ERROR] модуль={module_name} функция={context['function']}: {text}")
        for m, handler in list(self.on_error_handlers):
            mtoken = current_module.set(m)
            try:
                await handler(text, context)
            except Exception as inner:
                # ошибка внутри обработчика ошибок — только в лог, без рекурсии
                self.tlog(f"[ERROR] обработчик ошибок модуля '{m}' сам упал: {inner}")
            finally:
                current_module.reset(mtoken)

    # ------------------------------------------------------------------ #
    # Загрузка / выгрузка модулей
    # ------------------------------------------------------------------ #

    def _build_namespace(self):
        """
        Пространство имён, в котором исполняется код модуля при загрузке.
        Никаких готовых глобальных имён (answer/db/visual/...) в нём больше
        нет — модуль обязан сам сделать `import engine`, `import visual`,
        `import waiters` и т.д. (см. _register_module_imports). Здесь
        остаётся только то, что нужно любому модулю Python по умолчанию.
        """
        return {
            '__name__': '__frx_module__',
            '__builtins__': __builtins__,
        }

    async def load_module(self, file_or_reply, target: str = 'loaded'):
        """
        Загружает модуль.
        file_or_reply — путь к .py файлу, либо Telethon-сообщение (Message),
        на которое отвечаем файлом модуля (.loadmod по реплаю).
        """
        self._guest_guard('загрузка модуля', 'loader')
        if hasattr(file_or_reply, 'is_reply'):
            obj = await self.download_reply_file(file_or_reply)
            path = obj['path']
        else:
            path = file_or_reply
        source = Path(path).read_text(encoding='utf-8')
        return await self._load_from_source(source, target=target)

    async def _load_from_source(self, source: str, target: str = 'loaded', source_path: str | None = None) -> ModuleInfo:
        self._guest_guard('загрузка модуля', 'loader')
        if 'REQUIREMENTS' in source:            # пакеты, которые модуль объявил сам (core/requirements.py)
            await self._ensure_module_requirements(source)
        namespace = self._build_namespace()

        token = current_module.set('__loading__')
        try:
            exec(compile(source, '<frx_module>', 'exec'), namespace)
        finally:
            current_module.reset(token)

        module_name = namespace.get('MODULE_NAME')
        if not module_name:
            raise ValueError("В модуле не указан MODULE_NAME")
        if module_name in self.modules:
            existing = self.modules[module_name]
            new_version = str(namespace.get('VERSION', '1.0'))
            if new_version == existing.version:
                raise ValueError(
                    f"Модуль с MODULE_NAME='{module_name}' уже загружен (версия {existing.version})"
                )
            # другая версия того же MODULE_NAME — это не "новая загрузка"
            # (которая должна была бы упасть с ошибкой выше), а ОБНОВЛЕНИЕ существующего
            # модуля: тихо выгружаем старую версию (без архивации файла — он всё равно
            # тут же будет перезаписан новым исходником ниже) и продолжаем загрузку как
            # обычно. is_builtin проверять не нужно — builtin-модуль обновляется точно так
            # же, если кто-то намеренно .loadmod'ит новую версию встроенного модуля.
            await self.unload_module(module_name, archive=False)

        info = ModuleInfo(
            module_name=module_name,
            name=namespace.get('NAME', module_name),
            version=str(namespace.get('VERSION', '1.0')),
            author=namespace.get('AUTHOR', 'unknown'),
            description=namespace.get('DESCRIPTION', ''),
            path=source_path or str((BUILTIN_DIR if target == 'builtin' else LOADED_DIR) / f'{module_name}.py'),
            source=source,
            namespace=namespace,
            tg_url=normalize_module_link(namespace.get('TG_URL'), 'tg'),
            git_url=normalize_module_link(namespace.get('GIT_URL'), 'git'),
        )

        # Сохраняем файл модуля на диск — НО НЕ для встроенных (target='builtin'):
        # они и так уже лежат на диске под своим реальным именем файла
        # (например modules/builtin/Config.py — файл может называться не так,
        # как MODULE_NAME, регистр тоже может отличаться), и синтезировать
        # путь из module_name для них нельзя — это создаст на диске
        # ДУБЛИКАТ файла модуля с другим регистром имени.
        # Для target='loaded' (.loadmod/.reloadmod) путь,
        # наоборот, всегда канонический: user/modules/{module_name}.py,
        # независимо от того, как назывался присланный файл.
        if target != 'builtin':
            info_path = Path(info.path)
            info_path.parent.mkdir(parents=True, exist_ok=True)
            info_path.write_text(source, encoding='utf-8')

        # сканируем namespace на декорированные функции
        _seen_funcs = set()                     # одна функция под двумя именами (f2 = f) регистрируется один раз
        for value in namespace.values():
            if not callable(value):
                continue
            if hasattr(value, '_frx_type'):
                if id(value) in _seen_funcs:
                    continue
                _seen_funcs.add(id(value))

            if hasattr(value, '_frx_type'):
                ftype = value._frx_type

                if ftype == 'command':
                    for alias in value._frx_commands:
                        if alias in self.command_map:
                            owner = self.command_map[alias][0]
                            raise ValueError(f"Команда '{alias}' уже занята модулем '{owner}'")
                        self.command_map[alias] = (module_name, value)
                        info.commands[alias] = value
                elif ftype == 'watcher':
                    self.watchers.append((module_name, value))
                    info.watchers.append(value)
                elif ftype == 'telegram_event':
                    self.telegram_events.append((module_name, value))
                    info.telegram_events.append(value)
                elif ftype == 'callback':
                    self.callbacks.append((module_name, value))
                    info.callbacks.append(value)
                elif ftype == 'on_error':
                    self.on_error_handlers.append((module_name, value))
                    info.on_error_handlers.append(value)
                elif ftype == 'on_account_switch':
                    self.on_account_switch_handlers.append((module_name, value))
                    info.on_account_switch_handlers.append(value)
                elif ftype == 'on_edit':
                    self.edit_handlers.append((module_name, value))
                    info.edit_handlers.append(value)
                elif ftype == 'on_delete':
                    self.delete_handlers.append((module_name, value))
                    info.delete_handlers.append(value)
                elif ftype == 'on_unload':
                    info.on_unload_handlers.append(value)
                elif ftype == 'on_start':
                    info.on_start_handlers.append(value)
                elif ftype == 'on_load':
                    info.on_load_handlers.append(value)
                elif ftype == 'cycle':
                    info.cycle_handlers.append((value, value._frx_cycle))

            # страницы сайта (`import web`, @web.page — core/web/ui.py)
            for page_meta in getattr(value, '_frx_web_pages', []):
                info.web_pages.append({**page_meta, 'title': page_meta['title'], 'icon': page_meta.get('icon'), 'func': value})

            # --- параллельный набор: хэндлеры СТАЦИОНАРНОГО бота (`import bot`,
            # core/bot_decorators.py) — независимо от _frx_type выше ---
            if hasattr(value, '_frx_bot_type'):
                bftype = value._frx_bot_type

                if bftype == 'command':
                    for alias in value._frx_bot_commands:
                        if alias in self.bot_command_map or alias in self._bot_reserved_aliases:
                            owner = (
                                'aiobot/bot.py (встроенная команда)'
                                if alias in self._bot_reserved_aliases and alias not in self.bot_command_map
                                else self.bot_command_map[alias][0]
                            )
                            raise ValueError(f"Команда бота '/{alias}' уже занята модулем '{owner}'")
                        self.bot_command_map[alias] = (
                            module_name, value, value._frx_bot_access,
                            getattr(value, '_frx_bot_for_user', None),
                            getattr(value, '_frx_bot_for_chat', None),
                            getattr(value, '_frx_bot_black_list', None),
                            getattr(value, '_frx_bot_black_chats', None),
                        )
                        info.bot_commands[alias] = value
                elif bftype == 'inline_function':
                    for alias in value._frx_bot_inline_names:
                        if alias in self.bot_inline_function_map:
                            owner = self.bot_inline_function_map[alias][0]
                            raise ValueError(f"Инлайн-функция '{alias}' уже занята модулем '{owner}'")
                        self.bot_inline_function_map[alias] = (
                            module_name, value, value._frx_bot_access,
                            getattr(value, '_frx_bot_for_user', None),
                            getattr(value, '_frx_bot_for_chat', None),
                            getattr(value, '_frx_bot_black_list', None),
                            getattr(value, '_frx_bot_black_chats', None),
                        )
                        info.bot_inline_functions[alias] = value
                elif bftype == 'inline_query':
                    for alias in value._frx_bot_iq_names:
                        if alias in self.bot_inline_query_map or alias in self.bot_inline_function_map:
                            owner = (self.bot_inline_query_map.get(alias) or self.bot_inline_function_map.get(alias))[0]
                            raise ValueError(f"Инлайн-запрос '{alias}' уже занят модулем '{owner}'")
                        self.bot_inline_query_map[alias] = (
                            module_name, value, value._frx_bot_access,
                            getattr(value, '_frx_bot_for_user', None), getattr(value, '_frx_bot_for_chat', None),
                            getattr(value, '_frx_bot_black_list', None), getattr(value, '_frx_bot_black_chats', None),
                        )
                        info.bot_inline_queries[alias] = value
                elif bftype == 'inline_click':
                    self.bot_inline_clicks.append((module_name, value._frx_bot_click_prefix, value, value._frx_bot_access))
                    info.bot_inline_clicks.append(value)
                elif bftype == 'start_link':
                    self.bot_start_links.append((module_name, value._frx_bot_start_prefix, value))
                    info.bot_start_links.append(value)
                elif bftype == 'watcher':
                    watch = getattr(value, '_frx_bot_watch', 'bot')
                    entry = (module_name, value, value._frx_bot_access, watch)
                    if watch in ('bot', 'all'):
                        self.bot_watchers_bot.append(entry)
                    if watch in ('userbot', 'all'):
                        self.bot_watchers_userbot.append(entry)
                    info.bot_watchers.append(value)
                elif bftype == 'telegram_event':
                    self.bot_telegram_events.append((module_name, value))
                    info.bot_telegram_events.append(value)
                elif bftype == 'on_error':
                    self.bot_on_error_handlers.append((module_name, value))
                    info.bot_on_error_handlers.append(value)
                elif bftype == 'on_start':
                    info.bot_on_start_handlers.append(value)
                elif bftype == 'on_load':
                    info.bot_on_load_handlers.append(value)
                elif bftype == 'cycle':
                    info.bot_cycle_handlers.append((value, value._frx_bot_cycle))
                elif bftype == 'callback':
                    self.bot_callbacks.append((module_name, getattr(value, '_frx_bot_click_prefix', '') or '',
                                               value, value._frx_bot_access))
                    info.bot_callbacks.append(value)

        self.modules[module_name] = info

        mtoken = current_module.set(module_name)
        try:
            # запускаем циклы
            for func, interval in info.cycle_handlers:
                task = asyncio.create_task(self._run_cycle(module_name, func, interval))
                info.cycle_tasks.append(task)

            # вызываем on_load хэндлеры ВСЕХ модулей (в т.ч. этого) — по ТЗ
            # on_load "запускается при загрузке любого модуля, передаёт объект модуля"
            for owner_name, owner_info in self.modules.items():
                for func in owner_info.on_load_handlers:
                    await self._safe_call(owner_name, func, info)
                # bot.on_load() — тот же принцип, но независимый поток
                # (ошибки уходят в _dispatch_bot_error, а не в on_error юзербота)
                for func in owner_info.bot_on_load_handlers:
                    await self._safe_call_bot(owner_name, func, info)

            # если юзербот уже работает — сразу запускаем on_start этого модуля
            if self._started:
                for func in info.on_start_handlers:
                    await self._safe_call(module_name, func)
                # bot.on_start()/bot.cycle() — только если стационарный бот
                # тоже уже подключён (set_bot() уже отработал, self._bot_ready);
                # иначе они запустятся позже, все разом, из _fire_bot_on_start()
                if self._bot_ready:
                    for func in info.bot_on_start_handlers:
                        await self._safe_call_bot(module_name, func)
                    for func, interval in info.bot_cycle_handlers:
                        task = asyncio.create_task(self._run_bot_cycle(module_name, func, interval))
                        info.bot_cycle_tasks.append(task)
        finally:
            current_module.reset(mtoken)

        if target == 'loaded':
            loaded_list = await self.db.get('core:loaded_modules', [])
            if module_name not in loaded_list:
                loaded_list.append(module_name)
                await self.db.set('core:loaded_modules', loaded_list)
            # модуль снова загружен — его копия в unloaded/ больше не нужна
            self._drop_archived_copies(module_name)

        return info

    async def _ensure_module_requirements(self, source: str):
        """REQUIREMENTS модуля: недостающие пакеты ставятся до exec(), чтобы import в модуле сразу работал.
        Не встал пакет — модуль всё равно загружается (многие умеют работать без необязательного пакета),
        а причина пишется в лог."""
        from .requirements import ensure_for_source
        try:
            done, failed = await ensure_for_source(source, log=lambda t: self.tlog(f"[модули] {t}"))
        except Exception as e:
            self.tlog(f"[модули] проверка REQUIREMENTS не удалась: {e}")
            return
        if done:
            self.tlog(f"[модули] поставлены пакеты: {', '.join(done)}")
        for spec, err in failed.items():
            self.tlog(f"[модули] пакет {spec} не поставился: {err}")

    async def unload_module(self, module_name: str, archive: bool = True):
        """
        Выгружает модуль: останавливает хэндлеры и циклы и убирает его из реестров.
        Для модуля из user/modules/ при archive=True (по умолчанию)
        файл ПЕРЕНОСИТСЯ в user/modules/unloaded/ — автоскан папки его больше не
        подхватит, а вернуть можно через .restoremods / engine.restore_module().
        archive=False — файл не трогается (так работают reload/kill, а также
        "удалить модуль" в .help, который после выгрузки удаляет файл сам).
        Встроенные модули никогда не архивируются.
        """
        self._guest_guard('выгрузка модуля', 'loader')
        info = self.modules.get(module_name)
        if not info:
            raise KeyError(f"Модуль '{module_name}' не загружен")

        for func in list(info.on_unload_handlers):             # модуль сам останавливает свои задачи
            try:
                await asyncio.wait_for(self._safe_call(module_name, func), 10)
            except Exception:
                pass
        for alias in list(info.commands.keys()):
            self.command_map.pop(alias, None)
        self.watchers = [(m, f) for m, f in self.watchers if m != module_name]
        self.edit_handlers = [(m, f) for m, f in self.edit_handlers if m != module_name]
        self.delete_handlers = [(m, f) for m, f in self.delete_handlers if m != module_name]
        self.telegram_events = [(m, f) for m, f in self.telegram_events if m != module_name]
        self.callbacks = [(m, f) for m, f in self.callbacks if m != module_name]
        self.on_error_handlers = [(m, f) for m, f in self.on_error_handlers if m != module_name]
        self.on_account_switch_handlers = [
            (m, f) for m, f in self.on_account_switch_handlers if m != module_name
        ]

        # то же самое для хэндлеров СТАЦИОНАРНОГО бота этого модуля (`import bot`)
        for alias in list(info.bot_commands.keys()):
            self.bot_command_map.pop(alias, None)
        for alias in list(info.bot_inline_functions.keys()):
            self.bot_inline_function_map.pop(alias, None)
        for alias in list(info.bot_inline_queries.keys()):
            self.bot_inline_query_map.pop(alias, None)
        self.bot_inline_clicks = [e for e in self.bot_inline_clicks if e[0] != module_name]
        self.bot_callbacks = [e for e in self.bot_callbacks if e[0] != module_name]
        self.bot_start_links = [e for e in self.bot_start_links if e[0] != module_name]
        self.bot_watchers_bot = [e for e in self.bot_watchers_bot if e[0] != module_name]
        self.bot_watchers_userbot = [e for e in self.bot_watchers_userbot if e[0] != module_name]
        self.bot_telegram_events = [(m, f) for m, f in self.bot_telegram_events if m != module_name]
        self.bot_on_error_handlers = [(m, f) for m, f in self.bot_on_error_handlers if m != module_name]
        for task in info.bot_cycle_tasks:
            task.cancel()

        # инлайн-формы, созданные этим модулем, теряют смысл без него —
        # закрываем, чтобы клики не улетали хэндлерам выгруженного модуля
        for form in [f for f in self._active_forms.values() if f.owner_module == module_name]:
            asyncio.create_task(form.close())

        for task in info.cycle_tasks:
            task.cancel()

        # состояние страниц сайта этого модуля больше не нужно
        for key in [k for k in self.web._page_states if k[0] == module_name]:
            self.web._page_states.pop(key, None)
        # сессия Emulate этого модуля (лента, формы, ожидания) тоже больше не нужна
        asyncio.ensure_future(self.web.drop_emulation(module_name))

        del self.modules[module_name]

        loaded_list = await self.db.get('core:loaded_modules', [])
        if module_name in loaded_list:
            loaded_list.remove(module_name)
            await self.db.set('core:loaded_modules', loaded_list)

        if archive and not info.is_builtin:
            self._archive_module_file(info)

        return True

    async def kill_module(self, module_name: str):
        """
        Принудительно "выключает" модуль и включает заново — на случай,
        если модуль завис или не может принять текущие значения из БД.
        """
        self._guest_guard('перезапуск модуля', 'loader')
        info = self.modules.get(module_name)
        if not info:
            raise KeyError(f"Модуль '{module_name}' не загружен")
        source = info.source
        target = info.kind
        path = info.path
        await self.unload_module(module_name, archive=False)
        # source_path=path — иначе модуль с нестандартным именем файла
        # (например, MyModule.py при MODULE_NAME='my_module') после перезагрузки получал дубль-файл
        return await self._load_from_source(source, target=target, source_path=path)

    async def reload_module(self, module_name: str):
        """
        Перезагружает модуль, перечитывая его файл с диска — полезно,
        если модуль был отредактирован вручную в файлах.
        """
        self._guest_guard('перезагрузка модуля', 'loader')
        info = self.modules.get(module_name)
        if not info:
            raise KeyError(f"Модуль '{module_name}' не загружен")
        path = info.path
        target = info.kind
        source = Path(path).read_text(encoding='utf-8')
        await self.unload_module(module_name, archive=False)
        return await self._load_from_source(source, target=target, source_path=path)

    # ------------------------------------------------------------------ #
    # Выгруженные модули: user/modules/unloaded/
    # ------------------------------------------------------------------ #

    def _archive_module_file(self, info):
        """Переносит файл выгружаемого модуля в user/modules/unloaded/ (или пишет туда его исходник, если файла уже нет)."""
        src = Path(info.path)
        UNLOADED_DIR.mkdir(parents=True, exist_ok=True)
        dst = UNLOADED_DIR / src.name
        if src.exists():
            os.replace(src, dst)
        else:
            dst.write_text(info.source, encoding='utf-8')

    def _iter_unloaded_files(self):
        if not UNLOADED_DIR.is_dir():
            return []
        return sorted(
            (f for f in UNLOADED_DIR.glob('*.py') if not f.name.startswith('_')),
            key=lambda f: f.name.lower(),
        )

    def _drop_archived_copies(self, module_name: str):
        """Удаляет из unloaded/ файлы модуля с таким MODULE_NAME (он снова загружен — архивная копия устарела)."""
        for f in self._iter_unloaded_files():
            try:
                meta = parse_module_meta(f.read_text(encoding='utf-8'))
                if (meta and meta['module_name'] == module_name) or f.stem == module_name:
                    f.unlink()
            except OSError:
                pass

    async def _migrate_legacy_disabled(self):
        """
        В старых версиях "выгруженность" хранилась в core:disabled_loaded_modules, а файл лежал
        в папке загруженных модулей. Один раз переносим такие файлы в unloaded/ и удаляем ключ.
        """
        disabled = await self.db.get('core:disabled_loaded_modules', None)
        if disabled is None:
            return
        if LOADED_DIR.exists():
            for file in sorted(LOADED_DIR.glob('*.py')):
                try:
                    meta = parse_module_meta(file.read_text(encoding='utf-8'))
                except OSError:
                    continue
                if file.stem in disabled or (meta and meta['module_name'] in disabled):
                    UNLOADED_DIR.mkdir(parents=True, exist_ok=True)
                    os.replace(file, UNLOADED_DIR / file.name)
        await self.db.delete('core:disabled_loaded_modules')

    def list_unloaded_modules(self) -> list:
        """
        Выгруженные модули (файлы user/modules/unloaded/*.py), по алфавиту имени файла:
        [{'file', 'path', 'module_name', 'name', 'version', 'author', 'description',
          'commands': [[имена], ...], 'broken': bool, 'loaded': bool}, ...]
        Разбор статический (без исполнения кода). broken=True — файл не разобрался
        (синтаксис / нет MODULE_NAME): его можно только удалить. loaded=True — модуль с
        таким MODULE_NAME уже загружен (восстановить нельзя, пока он не выгружен).
        """
        result = []
        for f in self._iter_unloaded_files():
            try:
                meta = parse_module_meta(f.read_text(encoding='utf-8'))
            except (OSError, UnicodeDecodeError):
                meta = None
            entry = {'file': f.name, 'path': str(f), 'broken': meta is None}
            if meta:
                entry.update(meta)
                entry['loaded'] = meta['module_name'] in self.modules
            else:
                entry.update(module_name=f.stem, name=f.stem, version='?', author='?',
                             description='', commands=[], loaded=False)
            result.append(entry)
        return result

    def find_unloaded_module(self, query: str):
        """Ищет выгруженный модуль по имени файла (с .py или без), MODULE_NAME или NAME (без регистра). None — не найден."""
        q = str(query).strip().lower()
        entries = self.list_unloaded_modules()
        for key in (lambda e: e['file'].lower(), lambda e: Path(e['file']).stem.lower(),
                    lambda e: str(e['module_name']).lower(), lambda e: str(e['name']).lower()):
            for e in entries:
                if key(e) == q:
                    return e
        return None

    async def restore_module(self, query: str):
        """
        Возвращает выгруженный модуль: файл из unloaded/ → user/modules/ (под прежним
        именем), модуль загружается, on_load/on_start отрабатывают как при .loadmod.
        KeyError — такого нет в unloaded/; ValueError — файл повреждён либо модуль с этим
        MODULE_NAME уже загружен; FileExistsError — оба имени файла в loaded/ заняты.
        Возвращает ModuleInfo.
        """
        self._guest_guard('восстановление модуля', 'loader')
        entry = self.find_unloaded_module(query)
        if entry is None:
            raise KeyError(f"Выгруженный модуль '{query}' не найден")
        if entry['broken']:
            raise ValueError(f"Файл {entry['file']} не является корректным модулем (нет MODULE_NAME или синтаксическая ошибка)")
        if entry['loaded']:
            raise ValueError(f"Модуль '{entry['module_name']}' уже загружен")
        dest = LOADED_DIR / entry['file']
        if dest.exists():
            dest = LOADED_DIR / f"{entry['module_name']}.py"
            if dest.exists():
                raise FileExistsError(f"В папке модулей уже есть файлы {entry['file']} и {dest.name}")
        source = Path(entry['path']).read_text(encoding='utf-8')
        info = await self._load_from_source(source, target='loaded', source_path=str(dest))
        Path(entry['path']).unlink(missing_ok=True)
        return info

    async def delete_unloaded_module(self, query: str) -> bool:
        """Удаляет файл выгруженного модуля из unloaded/ НАВСЕГДА. KeyError — не найден."""
        self._guest_guard('удаление модуля', 'loader')
        entry = self.find_unloaded_module(query)
        if entry is None:
            raise KeyError(f"Выгруженный модуль '{query}' не найден")
        Path(entry['path']).unlink(missing_ok=True)
        return True

    # ------------------------------------------------------------------ #
    # exec_modcmd
    # ------------------------------------------------------------------ #

    async def exec_modcmd(self, event, module: str, command: str, args=''):
        """
        Запускает КОМАНДУ указанного модуля так, как будто её ввёл пользователь:
        engine.exec_modcmd(event, 'config', 'config', 'helper') == ввод `.config helper`.

        event — настоящее событие (нужны chat_id, edit/reply...; берите то, что пришло в
        вашу команду или замкнули в хэндлере кнопки). module — MODULE_NAME или NAME.
        command — ИМЯ КОМАНДЫ (алиас из @decorators.command(...), с префиксом или без,
        без учёта регистра), а не имя функции. args — строка аргументов (можно пусто).

        Внутри команды engine.get_args() вернёт args, event.raw_text — "<префикс><команда> <args>";
        всё остальное (chat_id, edit, reply, ...) — от настоящего события (core/command_event.py).
        Ошибка ВНУТРИ команды обрабатывается как обычно (@on_error модуля, Logging) и
        приводит к возврату False; True — команда отработала без исключений.
        KeyError — модуль или команда не найдены (сама команда при этом не запускалась).
        """
        if event is None:
            raise ValueError("exec_modcmd: нужен event (откуда брать чат для ответа)")
        module_name = self.resolve_module(str(module))
        if module_name is None:
            raise KeyError(f"Модуль '{module}' не найден")
        info = self.modules[module_name]

        prefix = await self.get_prefix()
        cmd = str(command).strip()
        if cmd.startswith(prefix):
            cmd = cmd[len(prefix):]
        cmd = cmd.lower()
        func = {k.lower(): f for k, f in info.commands.items()}.get(cmd)
        if func is None:
            raise KeyError(f"Команда '{command}' не найдена в модуле '{module_name}'")
        guest = current_guest.get()
        if guest is not None:                 # код выполняется по действию гостя — проверяем его права на эту команду
            ok, why = self.access.check(guest, module_name, command_key(module_name, func), info.is_builtin)
            if not ok:
                raise PermissionError(f"Нет доступа к {prefix}{cmd}: {why}")

        args = str(args).strip() if args is not None else ''
        proxy = CommandEvent(event, f"{prefix}{cmd} {args}" if args else f"{prefix}{cmd}")

        module_token = current_module.set(module_name)
        event_token = current_event.set(proxy)
        try:
            await func(proxy)
            return True
        except Exception as e:
            await self._dispatch_error(module_name, func, e)
            return False
        finally:
            current_event.reset(event_token)
            current_module.reset(module_token)

    # ------------------------------------------------------------------ #
    # botlog
    # ------------------------------------------------------------------ #

    # (помощник visual для строки заголовка, заголовок) для логов
    _BOTLOG_LEVELS = {
        'info': ('title', 'Лог'),
        'success': ('ok', 'Лог'),
        'warning': ('warn', 'Предупреждение'),
        'error': ('err', 'Ошибка'),
    }

    def botlog(self, text, level: str = 'info', *, module: str = None, function: str = None):
        """
        Лог в ЛС владельца через стационарного бота (аналог tlog, но не в терминал).
        Сообщение сразу оформлено: заголовок с уровнем, "Модуль: ...", "Функция: ...",
        время и ВНИЗУ — ваш text (HTML допустим, не-теги экранируются, см. safe_html).
        Модуль берётся из контекста, функция — имя функции, из которой вызван botlog
        (можно переопределить module=/function=). level: info | success | warning | error.

        Защита от флуда — лимитер (core/botlog.py): всплеск уходит сразу, дальше сообщения
        ждут в очереди либо (очередь переполнена / режим "сжигать") отбрасываются, а потом
        приходит одно сводное "пропущено N". Настройки — конфиги модуля Logging
        (botlog_burst, botlog_interval, botlog_queue, botlog_wait). Учитываются также
        Logging.enabled и Logging.log_chat_id. Нет бота — печатается в терминал.

        Вызов синхронный (можно без await, "выстрелил и забыл") и возвращает asyncio.Task:
        `status = await engine.botlog("...")` даёт 'sent' | 'dropped' | 'failed' | 'disabled'
        | 'no_bot'. Вне event loop возвращает None и печатает в терминал.
        """
        if function is None:
            try:
                function = sys._getframe(1).f_code.co_name
            except Exception:
                function = '?'
        if module is None:
            module = current_module.get() or '?'
        return self.botlog_raw(self._format_botlog(text, level, module, function))

    def _format_botlog(self, text, level, module, function) -> str:
        helper, title = self._BOTLOG_LEVELS.get(level, self._BOTLOG_LEVELS['info'])
        v = visual_module
        now = time_utils_module.time_format(time_utils_module.get_time(), '%d.%m.%Y %H:%M:%S')
        body = str(text)
        if len(body) > 3400:
            body = body[:3400] + '…'
        if helper == 'title':
            head = v.title(title)
        elif helper == 'warn':
            head = v.warn(f"<b>{title}</b>") + "\n" + v.sep()
        else:
            head = getattr(v, helper)(title) + "\n" + v.sep()
        return (
            f"{head}\n"
            f"{v.field('модуль', _html.escape(str(module)))}\n"
            f"{v.field('функция', _html.escape(str(function)))}\n"
            f"{v.field('время', now)}\n\n"
            f"{body}"
        )

    def botlog_raw(self, message: str):
        """То же, что botlog(), но для УЖЕ оформленного HTML-текста: только лимитер + доставка (см. botlog())."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.tlog(f"[botlog] {re.sub(r'<[^>]+>', '', _html.unescape(message))}")
            return None
        return asyncio.ensure_future(self._botlog_pipeline(message))

    async def _botlog_pipeline(self, message: str) -> str:
        if not await self._botlog_cfg('enabled', True):
            return 'disabled'
        if self._aiogram_bot is None:
            self.tlog(f"[botlog] бот не подключён: {re.sub(r'<[^>]+>', '', _html.unescape(message))}")
            return 'no_bot'
        return await self._botlog.submit(message)

    async def _botlog_cfg(self, key, default):
        try:
            value = await self.get_config(key, module_name='logging')
        except Exception:
            return default
        return default if value is None else value

    async def _botlog_settings(self) -> dict:
        return {
            'burst': int(await self._botlog_cfg('botlog_burst', 5)),
            'interval': float(await self._botlog_cfg('botlog_interval', 2)),
            'queue_size': int(await self._botlog_cfg('botlog_queue', 20)),
            'wait': bool(await self._botlog_cfg('botlog_wait', True)),
        }

    async def _botlog_deliver(self, text: str):
        chat_id = await self._botlog_cfg('log_chat_id', 0) or self._owner_id
        await self.bot.send_message(chat_id, text)

    # ------------------------------------------------------------------ #
    # отправка владельцу ОТ ИМЕНИ стационарного бота (в ЛС бота)
    # ------------------------------------------------------------------ #

    @staticmethod
    def _bot_cant_write(exc) -> bool:
        """True, если бот не может написать владельцу потому, что диалога нет / бот в ЧС."""
        from aiogram.exceptions import TelegramForbiddenError, TelegramBadRequest
        if isinstance(exc, TelegramForbiddenError):
            return True
        return isinstance(exc, TelegramBadRequest) and 'chat not found' in str(exc).lower()

    async def _open_bot_dialog(self):
        """
        Бот не может писать первым (Bot API), а владелец мог занести его в ЧС — поэтому
        ПИШЕМ МЫ ему: достаём бота из ЧС (UnblockRequest идемпотентен) и отправляем /start
        от лица текущего аккаунта. После этого бот уже может написать нам.
        """
        async with self._dialog_lock:
            entity = await self._get_bot_entity()
            try:
                await self.client(UnblockRequest(id=entity))
            except Exception as e:
                self.tlog(f"[bot] не удалось снять бота с ЧС: {e}")
            await self.client.send_message(entity, '/start')
            try:
                await self.db.set(f"startup:dialog_started:{self._owner_id}", True)
            except Exception:
                pass
            self.tlog("[bot] бот не мог написать владельцу — снят с ЧС и отправлен /start, диалог открыт")

    async def bot_send_to_owner(self, text, file=None, buttons=None):
        """
        Отправляет text (HTML) и/или файл владельцу юзербота в ЛС от имени стационарного бота
        (file — путь на диске / ID из реестра / InputFile, как у engine.bot.send_message).
        Если бот написать не может (нет диалога с текущим аккаунтом / бот в ЧС) — юзербот сам
        открывает диалог (_open_bot_dialog: снять с ЧС + /start), и бот отправляет то же
        сообщение. Без стационарного бота — RuntimeError; если и после /start не вышло —
        RuntimeError с причиной (в Избранное НЕ откатываемся).
        Лимит Bot API на файл — 50 МБ (проверяет вызывающий код, например Backup).
        """
        if self._aiogram_bot is None:
            raise RuntimeError("Стационарный бот не запущен (aiogram_token не задан)")
        try:
            return await self.bot.send_message(self._owner_id, text, file=file, buttons=buttons)
        except Exception as first:
            if not self._bot_cant_write(first):
                raise
        await self._open_bot_dialog()
        last = None
        for _ in range(5):
            await asyncio.sleep(1.0)
            try:
                return await self.bot.send_message(self._owner_id, text, file=file, buttons=buttons)
            except Exception as e:
                if not self._bot_cant_write(e):
                    raise
                last = e
        raise RuntimeError(f"Бот не смог написать владельцу даже после /start: {last}")

    # ------------------------------------------------------------------ #
    # Информация о модулях / поиск
    # ------------------------------------------------------------------ #

    def get_module_list(self):
        """Список всех загруженных модулей."""
        return list(self.modules.keys())

    async def run_unload_hooks(self, timeout=8.0):
        """@decorators.on_unload() всех модулей — при выключении/перезапуске юзербота (модули выходят из голосовых
        чатов, гасят свои серверы и потоки). Не больше timeout секунд на всё, чтобы выключение не зависло."""
        async def one(module_name, func):
            try:
                await asyncio.wait_for(self._safe_call(module_name, func), 5)
            except Exception:
                pass
        jobs = [one(name, f) for name, info in list(self.modules.items()) for f in list(info.on_unload_handlers)]
        if jobs:
            try:
                await asyncio.wait_for(asyncio.gather(*jobs), timeout)
            except Exception:
                pass

    def module_links(self, module_name_or_meta, row: bool = True):
        """
        Кнопки-ссылки автора модуля (TG_URL / GIT_URL из шапки) для инлайн-формы:
        [[('↗ Канал автора', 'url:https://t.me/…'), ('↗ GitHub', 'url:https://github.com/…')]] — один ряд
        (row=False — плоский список кнопок). Ссылок нет — []. Принимает MODULE_NAME или dict с 'tg_url'/'git_url'
        (результат get_module_info() / parse_module_meta()).
        """
        if isinstance(module_name_or_meta, dict):
            tg, git = module_name_or_meta.get('tg_url') or '', module_name_or_meta.get('git_url') or ''
        else:
            info = self.modules.get(module_name_or_meta)
            tg, git = (info.tg_url, info.git_url) if info else ('', '')
        link = visual_module.visual('link')
        buttons = []
        if tg:
            buttons.append((f"{link} Канал автора", 'url:' + tg))
        if git:
            buttons.append((f"{link} " + ('GitHub' if 'github.com' in git.lower() else 'Исходник'), 'url:' + git))
        return ([buttons] if buttons else []) if row else buttons

    async def channel_subscription(self, url):
        """
        Подписан ли текущий аккаунт на канал по ссылке (t.me/name). -> (entity | None, joined: bool | None).
        None — не канал, приватная ссылка или узнать не удалось. Канал находится по username один раз —
        дальше он в кэше сессии.
        """
        name = tg_username(url or '')
        if not name or self.client is None:
            return None, None
        try:
            entity = await self.client.get_entity(name)
        except Exception:
            return None, None
        from telethon.tl.types import Channel
        if not isinstance(entity, Channel):
            return entity, None
        left = getattr(entity, 'left', None)
        return entity, (None if left is None else not left)

    async def join_channel(self, url) -> bool:
        """Подписаться на канал по ссылке t.me/name от текущего аккаунта. True — подписан."""
        entity, joined = await self.channel_subscription(url)
        if entity is None:
            return False
        if joined:
            return True
        from telethon.tl.functions.channels import JoinChannelRequest
        try:
            await self.client(JoinChannelRequest(entity))
            return True
        except Exception as e:
            self.tlog(f"[modules] не удалось подписаться на {url}: {e}")
            return False

    def get_module_info(self, module_name: str) -> dict:
        """
        Вся инфа о модуле — команды (с алиасами и докстрингом) и остальные
        декораторы (тоже с именем функции и докстрингом, а не просто счётчиком).

        commands: [{'command': 'ping', 'aliases': ['пинг'], 'description': '...'}, ...]
                  'command' — ПЕРВЫЙ вариант написания из @command('cmd', 'alias', ...),
                  остальные перечисленные в декораторе имена идут в 'aliases'.
        watchers / telegram_events / callbacks / on_start / on_load / on_error:
            [{'name': 'func_name', 'description': '...'}, ...]
        cycles: [{'name': 'func_name', 'description': '...', 'interval': '1h'}, ...]
        """
        info = self.modules.get(module_name)
        if not info:
            raise KeyError(f"Модуль '{module_name}' не найден")

        def _doc(func):
            return (func.__doc__ or '').strip()

        def _handlers(funcs):
            return [{'name': f.__name__, 'description': _doc(f)} for f in funcs]

        commands = []
        seen = set()
        for func in info.commands.values():
            if id(func) in seen:
                continue
            seen.add(id(func))
            names = getattr(func, '_frx_commands', [func.__name__])
            commands.append({
                'command': names[0],
                'aliases': list(names[1:]),
                'description': _doc(func),
            })

        bot_commands = []
        seen_bot = set()
        for func in info.bot_commands.values():
            if id(func) in seen_bot:
                continue
            seen_bot.add(id(func))
            names = getattr(func, '_frx_bot_commands', [func.__name__])
            bot_commands.append({
                'command': names[0],
                'aliases': list(names[1:]),
                'access': getattr(func, '_frx_bot_access', 'public'),
                'description': _doc(func),
            })

        bot_inline_functions = []
        seen_bot_inline = set()
        for func in info.bot_inline_functions.values():
            if id(func) in seen_bot_inline:
                continue
            seen_bot_inline.add(id(func))
            names = getattr(func, '_frx_bot_inline_names', [func.__name__])
            bot_inline_functions.append({
                'name': names[0],
                'aliases': list(names[1:]),
                'access': getattr(func, '_frx_bot_access', 'public'),
                'description': _doc(func),
            })

        bot_inline_queries = []
        seen_iq = set()
        for func in info.bot_inline_queries.values():
            if id(func) in seen_iq:
                continue
            seen_iq.add(id(func))
            names = getattr(func, '_frx_bot_iq_names', [func.__name__])
            bot_inline_queries.append({
                'name': names[0],
                'aliases': list(names[1:]),
                'access': getattr(func, '_frx_bot_access', 'self'),
                'for_user': getattr(func, '_frx_bot_for_user', None),
                'description': _doc(func),
            })

        return {
            'module_name': info.module_name,
            'name': info.name,
            'version': info.version,
            'author': info.author,
            'description': info.description,
            'tg_url': info.tg_url,
            'git_url': info.git_url,
            'is_builtin': info.is_builtin,
            'commands': commands,
            'watchers': _handlers(info.watchers),
            'telegram_events': _handlers(info.telegram_events),
            'callbacks': _handlers(info.callbacks),
            'on_start': _handlers(info.on_start_handlers),
            'on_load': _handlers(info.on_load_handlers),
            'on_error': _handlers(info.on_error_handlers),
            'cycles': [
                {'name': f.__name__, 'description': _doc(f), 'interval': interval}
                for f, interval in info.cycle_handlers
            ],
            # --- хэндлеры СТАЦИОНАРНОГО бота этого модуля (`import bot`) ---
            'bot_commands': bot_commands,
            'bot_inline_functions': bot_inline_functions,
            'bot_inline_queries': bot_inline_queries,     # @bot.inline_query — «@бот имя запрос» в любом чате
            'bot_watchers': [
                {
                    'name': f.__name__,
                    'description': _doc(f),
                    'watch': getattr(f, '_frx_bot_watch', 'bot'),
                    'access': getattr(f, '_frx_bot_access', 'public'),
                }
                for f in info.bot_watchers
            ],
            'bot_telegram_events': _handlers(info.bot_telegram_events),
            'bot_on_start': _handlers(info.bot_on_start_handlers),
            'bot_on_load': _handlers(info.bot_on_load_handlers),
            'bot_on_error': _handlers(info.bot_on_error_handlers),
            'bot_cycles': [
                {'name': f.__name__, 'description': _doc(f), 'interval': interval}
                for f, interval in info.bot_cycle_handlers
            ],
        }

    def search_module(self, query: str):
        """Ищет наиболее похожий по названию модуль, возвращает его MODULE_NAME."""
        matches = difflib.get_close_matches(query, self.modules.keys(), n=1, cutoff=0.4)
        return matches[0] if matches else None

    def resolve_module(self, query: str):
        """
        Резолвит пользовательский ввод в MODULE_NAME — единственный
        технический идентификатор модуля во всём движке (везде внутри
        Core модули хранятся и ищутся по MODULE_NAME, см. self.modules).

        Тем не менее пользователю не всегда удобно помнить точный
        технический ID — поэтому команды, принимающие модуль от
        пользователя (.help, .config, .unloadmod, .reloadmod), должны
        принимать И MODULE_NAME, И отображаемое NAME. Эта функция —
        единая точка такого резолвинга:
            1. Точное совпадение с MODULE_NAME — возвращается как есть.
            2. Точное совпадение с NAME (без учёта регистра) — возвращается
               MODULE_NAME модуля, у которого такое NAME.
            3. Ничего не найдено — возвращается None (вызывающий код сам
               решает, предлагать ли похожие варианты через search_module()).
        Если NAME совпадает у нескольких модулей (NAME, в отличие от
        MODULE_NAME, разрешено повторять) — возвращается первый найденный
        по порядку self.modules.
        """
        if query in self.modules:
            return query
        query_lower = query.lower()
        for module_name, info in self.modules.items():
            if info.name.lower() == query_lower:
                return module_name
        return None

    def search_dec_module(self, query: str):
        """
        Ищет наиболее похожую по названию команду и возвращает модуль(и),
        где она объявлена. Если совпадений несколько — возвращает список.
        """
        alias_to_module = {}
        for module_name, info in self.modules.items():
            for alias in info.commands:
                alias_to_module.setdefault(alias, []).append(module_name)

        matches = difflib.get_close_matches(query, alias_to_module.keys(), n=5, cutoff=0.4)
        result = []
        for alias in matches:
            for module_name in alias_to_module[alias]:
                if module_name not in result:
                    result.append(module_name)
        return result

    # ------------------------------------------------------------------ #
    # Конфиги модулей (create_config / edit_config / delete_config)
    # ------------------------------------------------------------------ #

    def _config_key(self, module_name):
        return f"{module_name}:__config__"

    def _check_valid_spec(self, key, data_type, valid):
        """
        Проверяет сам valid={...} на осмысленность — какие
        ключи вообще допустимы для этого data_type. Вызывается один раз,
        при create_config().
        """
        allowed = {
            'int': {'max_value', 'min_value'},
            'float': {'max_value', 'min_value'},
            'str': {'max_len', 'min_len'},
            'link': {'https_only', 'domains', 'allow_tg', 'max_len'},
        }.get(data_type)
        if allowed is None:
            raise ValueError(
                f"valid=...  для конфига '{key}' поддерживается только для типов "
                f"'int'/'float'/'str'/'link', не для '{data_type}'"
            )
        unknown = set(valid.keys()) - allowed
        if unknown:
            raise ValueError(
                f"valid=...  для конфига '{key}' (тип '{data_type}') поддерживает "
                f"только {sorted(allowed)}, а не {sorted(unknown)}"
            )

    _LINK_HOST_RE = re.compile(r'^(?:localhost|\[[0-9a-f:]+\]|(?:\d{1,3}\.){3}\d{1,3}|'
                               r'(?:[a-z0-9\u0400-\u04ff](?:[a-z0-9\u0400-\u04ff-]{0,61}[a-z0-9\u0400-\u04ff])?\.)+'
                               r'[a-z\u0400-\u04ff]{2,63})$', re.I)

    def _normalize_link(self, key, value, valid=None):
        """
        Конфиг типа 'link': проверяет, что значение — настоящая ссылка, и приводит её к полному виду.
            example.com/x   -> https://example.com/x   (без схемы — подставляется https://)
            @username       -> https://t.me/username
            t.me/channel    -> https://t.me/channel
        Допустимы http(s):// (с хостом-доменом, IP или localhost) и tg:// (если не запрещено).
        Пустая строка / None — «не задано», разрешено.
        valid: {'https_only': True} — только https; {'domains': ['github.com', 't.me']} — только эти
        домены (и их поддомены); {'allow_tg': False} — запретить tg://; {'max_len': 500}.
        """
        from urllib.parse import urlsplit
        valid = valid or {}
        if value is None:
            return None
        v = str(value).strip()
        if not v:
            return ''
        if re.fullmatch(r'@[A-Za-z][\w]{3,31}', v):
            v = 'https://t.me/' + v[1:]
        if not re.match(r'^[a-z][a-z0-9+.-]*://', v, re.I):
            if re.match(r'^[a-z][a-z0-9+.-]*:', v, re.I) and not re.match(r'^[^:/]+:\d+(?:/|$)', v):
                raise ValueError(f"Конфиг '{key}': «{v}» — не ссылка (нужно http(s)://…)")
            local = re.match(r'^(?:localhost|(?:\d{1,3}\.){3}\d{1,3})(?::\d+)?(?:/|$)', v, re.I)
            v = ('http://' if local else 'https://') + v
        if any(c.isspace() for c in v):
            raise ValueError(f"Конфиг '{key}': в ссылке не должно быть пробелов")
        parts = urlsplit(v)
        scheme = parts.scheme.lower()
        if scheme == 'tg':
            if valid.get('allow_tg') is False or valid.get('https_only'):
                raise ValueError(f"Конфиг '{key}': ссылки tg:// здесь не разрешены")
            if not (parts.netloc or parts.path):
                raise ValueError(f"Конфиг '{key}': пустая tg-ссылка")
        elif scheme in ('http', 'https'):
            if valid.get('https_only') and scheme != 'https':
                raise ValueError(f"Конфиг '{key}': нужна ссылка https://")
            host = (parts.hostname or '').lower()
            if not host or not self._LINK_HOST_RE.match(host):
                raise ValueError(f"Конфиг '{key}': «{v}» — не похоже на ссылку (нет домена)")
            try:
                parts.port
            except ValueError:
                raise ValueError(f"Конфиг '{key}': неверный порт в ссылке")
            domains = [str(d).lower().lstrip('.') for d in (valid.get('domains') or [])]
            if domains and not any(host == d or host.endswith('.' + d) for d in domains):
                raise ValueError(f"Конфиг '{key}': разрешены только ссылки на {', '.join(domains)}")
        else:
            raise ValueError(f"Конфиг '{key}': схема «{scheme}://» не поддерживается — нужна http(s)://"
                             + ("" if valid.get('allow_tg') is False else " или tg://"))
        max_len = valid.get('max_len')
        if max_len is not None and len(v) > max_len:
            raise ValueError(f"Конфиг '{key}': ссылка длиннее {max_len} символов")
        return v

    def _validate_config_value(self, key, data_type, valid, value):
        """
        Проверяет value на соответствие valid={...} —
        вызывается и из create_config() (для default), и из edit_config()
        (для нового значения при operation_type='edit'):
            'int'/'float': {'max_value': ..., 'min_value': ...}
            'str':         {'max_len': ..., 'min_len': ...} (длина строки)
        Ничего не проверяет, если valid не задан (None/{}) — конфиг тогда
        ведёт себя как раньше, без ограничений.
        """
        if not valid:
            return
        if data_type in ('int', 'float'):
            max_value = valid.get('max_value')
            min_value = valid.get('min_value')
            if max_value is not None and value is not None and value > max_value:
                raise ValueError(f"Значение конфига '{key}' не может быть больше {max_value}")
            if min_value is not None and value is not None and value < min_value:
                raise ValueError(f"Значение конфига '{key}' не может быть меньше {min_value}")
        elif data_type == 'str':
            max_len = valid.get('max_len')
            min_len = valid.get('min_len')
            length = len(value) if value is not None else 0
            if max_len is not None and length > max_len:
                raise ValueError(f"Длина значения конфига '{key}' не может превышать {max_len} символов")
            if min_len is not None and length < min_len:
                raise ValueError(f"Длина значения конфига '{key}' не может быть меньше {min_len} символов")

    async def create_config(self, key, description='', data_type='str', default=None, valid: dict = None,
                             options: list = None):
        """
        Создаёт конфиг для текущего модуля (тип может быть 'list', а также
        'choice'/'choices' и 'link').

        'link' — ссылка: значение проверяется на то, что это настоящий адрес
        http(s):// или tg://, и приводится к полному виду (example.com -> https://example.com,
        @user -> https://t.me/user). Пустое значение разрешено («не задано»).
        valid для 'link': {'https_only': True, 'domains': ['github.com'], 'allow_tg': False, 'max_len': 500}.

        valid — необязательные ограничения на значение,
        проверяются при edit_config(key, 'edit', ...) (для 'list'/'bool'/
        'choice'/'choices' не поддерживается — там нет единого "значения",
        которое можно было бы так ограничить):
            для 'int'/'float': {'max_value': 5, 'min_value': 0, ...}
            для 'str':         {'max_len': 32, 'min_len': 1, ...}
        Оба ключа каждой пары не обязательны — можно указать только один.
        default, если задан, тоже сразу проверяется на соответствие valid.

        options — ОБЯЗАТЕЛЕН для 'choice'/'choices': статический
        список допустимых значений. default вводится КОНКРЕТНЫМ значением
        (а не его номером/индексом в options):
            'choice'  — default должен быть одним из options (выбор нельзя
                        оставить пустым — значение обязано быть всегда).
            'choices' — default должен быть непустым списком элементов из
                        options (хотя бы один элемент обязан быть выбран
                        всегда — снять последний нельзя, см. edit_config()).
        В модуле Config (.config) оба типа рисуются как инлайн-список
        вариантов на выбор, а не как поле ввода.
        """
        module_name = current_module.get()
        # запоминаем, что именно ЭТОТ ключ модуль реально
        # объявил в текущем запуске — см. _cleanup_stale_configs().
        self._declared_config_keys.setdefault(module_name, set()).add(key)

        config = await self.db.get(self._config_key(module_name), {})
        if key in config:
            entry = config[key]
            # Конфиг без поля 'default' (созданный ранними сборками,
            # нужного для edit_config(key, 'to_default', ...)) — досоздаём его
            # тем значением default, которое модуль передаёт сейчас в
            # create_config(); дальше это поле уже не трогается.
            if 'default' not in entry:
                entry['default'] = default
                config[key] = entry
                await self.db.set(self._config_key(module_name), config)
            # то же самое для 'options' — конфиг choice/choices,
            # созданный до этого поля (или если модуль позже переопределил
            # список вариантов в коде), должен получить/обновить его здесь.
            if data_type in ('choice', 'choices') and entry.get('options') != options:
                entry['options'] = options
                config[key] = entry
                await self.db.set(self._config_key(module_name), config)
            return entry['value']

        if data_type in ('choice', 'choices'):
            if not options:
                raise ValueError(f"Конфиг '{key}' типа '{data_type}' требует options=[...] (непустой список)")
            if data_type == 'choice':
                if default is None or default not in options:
                    raise ValueError(f"Конфиг '{key}' типа 'choice': default должен быть одним из options")
            else:
                if not default or not isinstance(default, (list, tuple)) or not all(o in options for o in default):
                    raise ValueError(
                        f"Конфиг '{key}' типа 'choices': default должен быть непустым списком элементов из options"
                    )
                default = list(default)
        elif data_type == 'link':
            if valid:
                self._check_valid_spec(key, data_type, valid)
            default = self._normalize_link(key, default, valid)
        elif valid:
            self._check_valid_spec(key, data_type, valid)
            self._validate_config_value(key, data_type, valid, default)

        config[key] = {
            'value': default,
            'description': description,
            'type': data_type,
            'valid': valid,
            'default': default,
            'options': options,
        }
        await self.db.set(self._config_key(module_name), config)
        return default

    async def edit_config(self, key, operation_type, value=None, module_name=None):
        """
        Редактирует значение конфига.
        operation_type: 'edit' (int/float/bool/str/choice), 'add'/'del' (list/choices),
        'to_default' — работает для ЛЮБОГО типа (в т.ч. list/choices),
        просто возвращает конфигу то значение, с которым он был создан
        (поле 'default', см. create_config()); value при этом игнорируется.
        Если конфиг создан с valid=... (см. create_config()) —
        новое значение проверяется на соответствие, иначе — ValueError.

        'choice'/'choices': value всегда КОНКРЕТНЫЙ элемент
        (а не его номер/индекс в options), как и для 'list':
            'choice'  — operation_type='edit', value должен быть одним из
                        options конфига; заменяет текущий выбор целиком.
            'choices' — operation_type='add'/'del', value должен быть одним
                        из options; 'del' последнего оставшегося элемента
                        запрещён — выбор нельзя оставить пустым.
        """
        module_name = module_name or current_module.get()
        config = await self.db.get(self._config_key(module_name), {})
        if key not in config:
            raise KeyError(f"Конфиг '{key}' не найден для модуля '{module_name}'")
        entry = config[key]

        if operation_type == 'to_default':
            entry['value'] = entry.get('default')
            config[key] = entry
            await self.db.set(self._config_key(module_name), config)
            return entry['value']

        if entry['type'] == 'list':
            entry.setdefault('value', [])
            if operation_type == 'add':
                entry['value'].append(value)
            elif operation_type == 'del':
                if value not in entry['value']:
                    raise ValueError(f"Значение '{value}' отсутствует в списке '{key}'")
                entry['value'].remove(value)
            else:
                raise ValueError("Для конфига-списка operation_type должен быть 'add' или 'del'")
        elif entry['type'] == 'choices':
            options = entry.get('options') or []
            entry.setdefault('value', [])
            if operation_type == 'add':
                if value not in options:
                    raise ValueError(f"'{value}' не входит в список допустимых вариантов конфига '{key}'")
                if value not in entry['value']:
                    entry['value'].append(value)
            elif operation_type == 'del':
                if value not in entry['value']:
                    raise ValueError(f"Значение '{value}' сейчас не выбрано в '{key}'")
                if len(entry['value']) <= 1:
                    raise ValueError(f"Нельзя снять последний выбранный вариант в '{key}' — что-то должно быть выбрано")
                entry['value'].remove(value)
            else:
                raise ValueError("Для конфига-choices operation_type должен быть 'add' или 'del'")
        elif entry['type'] == 'choice':
            if operation_type != 'edit':
                raise ValueError("Для конфига типа 'choice' operation_type должен быть 'edit'")
            options = entry.get('options') or []
            if value not in options:
                raise ValueError(f"'{value}' не входит в список допустимых вариантов конфига '{key}'")
            entry['value'] = value
        elif entry['type'] == 'link':
            if operation_type != 'edit':
                raise ValueError("Для конфига типа 'link' operation_type должен быть 'edit'")
            entry['value'] = self._normalize_link(key, value, entry.get('valid'))
        else:
            if operation_type != 'edit':
                raise ValueError(f"Для конфига типа '{entry['type']}' operation_type должен быть 'edit'")
            self._validate_config_value(key, entry['type'], entry.get('valid'), value)
            entry['value'] = value

        config[key] = entry
        await self.db.set(self._config_key(module_name), config)
        return entry['value']

    async def delete_config(self, key):
        """Удаляет один из ключей конфига, не трогая остальные."""
        module_name = current_module.get()
        config = await self.db.get(self._config_key(module_name), {})
        if key in config:
            del config[key]
            await self.db.set(self._config_key(module_name), config)
            return True
        return False

    async def get_config(self, key, module_name=None):
        """Возвращает текущее значение конфига (module_name по умолчанию — текущий модуль)."""
        module_name = module_name or current_module.get()
        config = await self.db.get(self._config_key(module_name), {})
        if key not in config:
            raise KeyError(f"Конфиг '{key}' не найден для модуля '{module_name}'")
        return config[key]['value']

    async def _cleanup_stale_configs(self):
        """
        в БД должны храниться ровно те конфиги, которые
        сейчас указаны в коде модуля (через create_config()). Если у
        модуля в БД лежит ключ, который в этом запуске ни разу не был
        объявлен через create_config() (например, разработчик модуля
        убрал/переименовал его в коде), — такой ключ удаляется.

        Вызывается один раз, в конце Core.start() — после того как все
        модули (builtin + loaded) успели отработать и on_load, и on_start
        хэндлеры, где обычно и вызывается create_config().
        """
        for module_name in self.modules:
            declared = self._declared_config_keys.get(module_name, set())
            config_key = self._config_key(module_name)
            config = await self.db.get(config_key, {})
            if not config:
                continue
            stale_keys = [key for key in config if key not in declared]
            if not stale_keys:
                continue
            for key in stale_keys:
                del config[key]
            await self.db.set(config_key, config)

    # ------------------------------------------------------------------ #
    # Сообщения
    # ------------------------------------------------------------------ #

    async def _resolve_file(self, file):
        """file: путь на диске / ID из реестра (core/files.py) -> путь на диске, либо None."""
        if file is None:
            return None
        obj = await self.file_registry.get(query=file)
        return obj['path'] if obj else file

    async def answer(self, message, text, file=None):
        """
        Изменяет сообщение (если это наше сообщение), иначе — отправляет новое. file — путь/ID вложения (см. core/files.py).
        Чужое сообщение (команда гостя): первый answer отвечает на него, следующие правят этот ответ — как у владельца.
        Файл, который грузится дольше мгновения, сначала показывается анимацией «загрузка…» (см. _deliver_media).
        """
        path = await self._resolve_file(file)
        text = safe_html(text)   # <не_теги> показываются как есть, а не пропадают
        if getattr(message, 'out', True) is False:
            key = (getattr(message, 'chat_id', None), getattr(message, 'id', None))
            prev = self._guest_replies.get(key)
            if prev is not None:
                async def edit_prev(f):
                    try:
                        return await prev.edit(text, file=f, parse_mode='html')
                    except MessageNotModifiedError:
                        return prev
                try:
                    return await self._deliver_media(path, text, edit_prev)
                except Exception:
                    pass
            sent = await self._deliver_media(path, text, lambda f: message.reply(text, file=f, parse_mode='html'))
            self._guest_replies[key] = sent
            while len(self._guest_replies) > 300:
                self._guest_replies.pop(next(iter(self._guest_replies)))
            return sent

        async def edit_or_reply(f):
            try:
                return await message.edit(text, file=f, parse_mode='html')
            except Exception:
                return await message.reply(text, file=f, parse_mode='html')
        return await self._deliver_media(path, text, edit_or_reply)

    async def reply(self, message, text, file=None):
        """Отвечает на message. file — путь на диске / ID из реестра (core/files.py)."""
        path = await self._resolve_file(file)
        text = safe_html(text)
        return await self._deliver_media(path, text, lambda f: message.reply(text, file=f, parse_mode='html'))

    async def noreply(self, message, text, file=None):
        """
        Отправляет сообщение в том же чате, но не отвечая на message.
        chat_id резолвится через _resolve_entity() (см. выше) —
        на практике здесь почти всегда есть быстрый путь (чат уже прогрет
        самим входящим message), но резолв остаётся единообразным со всеми
        остальными функциями движка.
        """
        path = await self._resolve_file(file)
        text = safe_html(text)
        emu = current_emulation.get()                      # Emulate — ответ уходит на сайт
        if emu is not None and emu.owns_chat(getattr(message, 'chat_id', None)):
            return await emu.send_text(text, path)
        entity = await self._resolve_entity(message.chat_id)
        return await self._deliver_media(path, text, lambda f: self.client.send_message(entity, text, file=f, parse_mode='html'))

    async def send_message(self, chat_id, text, file=None):
        """
        Отправляет сообщение в указанный чат. file — путь на диске / ID из
        реестра (core/files.py). chat_id резолвится через _resolve_entity() — сработает и для юзера/чата, которого юзербот ещё
        ни разу не "видел" в этой сессии, а не только для уже закэшированных.
        """
        path = await self._resolve_file(file)
        text = safe_html(text)
        emu = current_emulation.get()                      # Emulate — «свой» чат = сайт
        if emu is not None and emu.owns_chat(chat_id):
            return await emu.send_text(text, path)
        entity = await self._resolve_entity(chat_id)
        return await self._deliver_media(path, text, lambda f: self.client.send_message(entity, text, file=f, parse_mode='html'))

    # ------------------------------------------------------------------ #
    # Медиа с заглушкой «загрузка…»
    # Telegram показывает сообщение с файлом только после загрузки файла — большое видео «висит» секунды.
    # Поэтому: загрузка начинается сразу; если за LOADING_AFTER секунд не успела — сообщение уходит с лёгкой
    # анимацией «загрузка…» (assets/loading.gif, ~9 КБ; свою можно положить в user/assets/loading.gif), а когда файл
    # загрузится — заглушка меняется на него правкой того же сообщения. Уже отправленный файл (тот же путь, размер
    # и время изменения) за время работы второй раз не грузится — уходит ссылкой на загруженный.
    # ------------------------------------------------------------------ #

    LOADING_AFTER = 0.6

    async def _deliver_media(self, path, text, send):
        """send(file) — отправляет/правит сообщение с этим file и возвращает его. path — None, путь, ссылка или id."""
        if not path or not isinstance(path, (str, os.PathLike)) or not os.path.isfile(path) or self.client is None:
            return await send(path)
        from telethon import utils as tl_utils
        from telethon.errors import FileReferenceExpiredError
        st = os.stat(path)
        key = f"{os.path.abspath(path)}|{st.st_size}|{int(st.st_mtime)}"
        cache = self.__dict__.setdefault('_tl_media', {})
        if key in cache:
            try:
                return await send(cache[key])
            except FileReferenceExpiredError:
                cache.pop(key, None)

        def remember(msg):
            try:
                if msg is not None and getattr(msg, 'media', None) is not None:
                    cache[key] = tl_utils.get_input_media(msg.media)
                    while len(cache) > 200:
                        cache.pop(next(iter(cache)))
            except Exception:
                pass
            return msg

        src = path
        if tl_utils.is_image(path):                  # как Telethon при отправке путём: большие фото — ужать
            try:
                from telethon.client.uploads import _resize_photo_if_needed
                src = _resize_photo_if_needed(path, True)
            except Exception:
                src = path
        up = asyncio.ensure_future(self.client.upload_file(src, file_name=os.path.basename(path)))
        done, _ = await asyncio.wait({up}, timeout=self.LOADING_AFTER)
        if up in done and not up.exception():
            return remember(await send(up.result()))
        placeholder = None if up.done() else await self._tl_loading_media()
        if placeholder is None:                     # заглушки нет (или загрузка уже упала) — как раньше
            up.cancel()
            return remember(await send(path))
        try:
            msg = await send(placeholder)
        except Exception:
            up.cancel()
            return remember(await send(path))
        if not isinstance(self._tl_loading, (dict,)) and getattr(msg, 'media', None) is not None and \
                type(self._tl_loading).__name__ in ('InputFile', 'InputFileBig'):
            try:                                    # дальше заглушка уходит ссылкой на уже загруженный документ
                self._tl_loading = tl_utils.get_input_media(msg.media)
            except Exception:
                pass
        try:
            uploaded = await up
        except Exception as e:
            self.tlog(f"[core] файл не загрузился: {e}")
            try:
                await msg.edit(text + "\n\n<i>файл не загрузился</i>", file=None, parse_mode='html')
            except Exception:
                pass
            return msg
        for attempt in range(3):
            try:
                edited = await msg.edit(text, file=uploaded, parse_mode='html')
                return remember(edited if edited is not None else msg)
            except MessageNotModifiedError:
                return msg
            except Exception as e:
                if attempt == 2:                     # правка не прошла — отправляем файл отдельным сообщением
                    self.tlog(f"[core] не удалось заменить «загрузка…» файлом: {e}")
                    try:
                        await msg.delete()
                    except Exception:
                        pass
                    return remember(await send(uploaded))
                await asyncio.sleep(1.0 + attempt)

    async def _tl_loading_media(self):
        """Заглушка «загрузка…» для сообщений юзербота: загружается один раз, дальше — ссылкой на загруженную."""
        cached = getattr(self, '_tl_loading', None)
        if cached is not None:
            return cached
        try:
            path = str(self.asset_path('loading.gif'))
        except Exception:
            return None
        try:
            uploaded = await self.client.upload_file(path)
        except Exception:
            return None
        self._tl_loading = uploaded          # пока не отправлен — InputFile; после первой отправки — ссылка на документ
        return uploaded

    def _command_text(self, text: str) -> str:
        """«.  cmd args» → «.cmd args»: пробелы между префиксом и командой не делают команду аргументом."""
        text = (text or '').lstrip()
        prefix = self._prefix_cache
        if prefix and text.startswith(prefix) and text[len(prefix):len(prefix) + 1].isspace():
            text = prefix + text[len(prefix):].lstrip()
        return text

    def _args_offset(self, text: str) -> int:
        """Индекс в text, с которого начинаются аргументы команды (len(text) — аргументов нет)."""
        text = text or ''
        i = len(text) - len(text.lstrip())
        prefix = self._prefix_cache
        if prefix and text.startswith(prefix, i):
            i += len(prefix)
            while i < len(text) and text[i].isspace():
                i += 1
        while i < len(text) and not text[i].isspace():      # сама команда
            i += 1
        while i < len(text) and text[i].isspace():
            i += 1
        return i

    def get_args(self):
        """Возвращает аргументы текущей команды одной строкой (без самой команды)."""
        event = current_event.get()
        if event is None:
            return ''
        parts = self._command_text(event.raw_text or '').split(maxsplit=1)
        return parts[1] if len(parts) > 1 else ''

    def get_args_html(self):
        """
        Аргументы текущей команды с разметкой — HTML, как у engine.answer(): жирный, ссылки и
        ПРЕМИУМ-ЭМОДЗИ (<tg-emoji emoji-id="…">) из сообщения команды сохраняются. Для команд,
        которые повторяют или сохраняют то, что написал пользователь (тексты, подписи, иконки).
        """
        event = current_event.get()
        if event is None:
            return ''
        msg = getattr(event, 'message', None) or event
        text = getattr(msg, 'raw_text', None) or ''
        entities = list(getattr(msg, 'entities', None) or [])
        if isinstance(event, CommandEvent) or not entities:
            from html import escape
            return escape(self.get_args(), quote=False)
        off = self._args_offset(text)
        return html_with_entities(text, entities, off)

    # ------------------------------------------------------------------ #
    # Файлы
    # ------------------------------------------------------------------ #

    async def download_reply_file(self, message):
        """Скачивает файл, на который отвечает message, регистрирует его и возвращает словарь."""
        if not message.is_reply:
            raise ValueError("Сообщение не является ответом (reply) на файл")
        replied = await message.get_reply_message()
        if not replied or not replied.file:
            raise ValueError("В сообщении, на которое отвечаем, нет файла")
        path = await replied.download_media(file=str(FILES_DIR) + '/')
        return await self.file_registry.register(
            path=path,
            name=os.path.basename(path),
            ftype=(replied.file.mime_type or 'unknown'),
            size=(replied.file.size or os.path.getsize(path)),
        )

    # ------------------------------------------------------------------ #
    # Ожидание сообщений
    # ------------------------------------------------------------------ #

    def wait_message(self, chat_id=None, user_id=None, timeout=None):
        """
        Ждёт сообщение, подходящее под фильтры. Работает и как обычный
        await (одно сообщение), и как async-итератор (цикл) — см.
        докстринг core/waiters.py.

        chat_id: None (по умолчанию) -> текущий чат (тот, где вызвана
                 команда). 'me' -> Избранное. int -> конкретный чат.
                 list из перечисленного -> любое совпадение.
        user_id: None (по умолчанию) -> любой юзер этого чата (в т.ч. и
                 сам владелец юзербота — Telegram шлёт NewMessage и на
                 исходящие сообщения тоже). 'me' -> только владелец
                 юзербота. int -> конкретный user_id. list -> любое совпадение.
        timeout: None -> берётся из конфига модуля 'waiters' (см.
                 modules/builtin/Waiters.py), резолвится лениво при первом
                 реальном ожидании. Для одиночного await — при истечении
                 бросает TimeoutError. Для async-for — истёк таймаут между
                 сообщениями — цикл просто заканчивается (как обычный for).

        Метод СИНХРОННЫЙ (не корутина) — сам await/async-for происходит
        на возвращённом объекте Waiter, а не на вызове wait_message().
        """
        chats = self._resolve_wait_target(chat_id, default_current_chat=True)
        users = self._resolve_wait_target(user_id, default_current_chat=False)
        # команда запущена с сайта (Emulate) и ждёт сообщение в «своём» чате
        # от владельца — сообщение придёт из поля «ответить» на сайте
        emu = current_emulation.get()
        if emu is not None:
            chat_list = chats if isinstance(chats, list) else [chats]
            user_ok = users is None or emu.owner_id in (users if isinstance(users, list) else [users])
            if user_ok and any(emu.owns_chat(c) for c in chat_list):
                return emu.make_waiter(timeout)
        return Waiter(self.client, chats, users, timeout, core=self)

    def _resolve_wait_target(self, value, default_current_chat):
        if value is None:
            if not default_current_chat:
                return None  # user_id=None -> без фильтра по отправителю
            event = current_event.get()
            if event is None:
                raise RuntimeError(
                    "wait_message(): chat_id не передан и нет текущего события — "
                    "укажите chat_id явно"
                )
            return event.chat_id

        values = value if isinstance(value, list) else [value]
        # user_id='me' по команде гостя — это сам гость (тот, кто запустил команду); chat_id='me' — всегда Избранное владельца
        me = self._owner_id if default_current_chat or current_guest.get() is None else current_guest.get()
        return [me if v == 'me' else v for v in values]

    async def emulate_command_timeout(self):
        """предел времени команды, запущенной с сайта (конфиг `command_timeout` модуля Web; 0 — без предела)."""
        try:
            return int(await self.get_config('command_timeout', module_name='web_panel') or 0)
        except (KeyError, TypeError, ValueError):
            return 600

    async def _waiters_default_timeout(self):
        try:
            return await self.get_config('default_timeout', module_name='waiters')
        except KeyError:
            return 60

    # ------------------------------------------------------------------ #
    # Префикс
    # ------------------------------------------------------------------ #

    async def get_prefix(self):
        prefix = await self.db.get('core:prefix', self.config.get('prefix', '.'))
        self._prefix_cache = prefix
        return prefix

    async def edit_prefix(self, new_prefix):
        self._prefix_cache = new_prefix
        self._guest_guard('смена префикса', 'config')
        await self.db.set('core:prefix', new_prefix)
        return new_prefix

    async def default_prefix(self):
        return await self.edit_prefix('.')

    # ------------------------------------------------------------------ #
    # Мультиаккаунт
    # ------------------------------------------------------------------ #

    async def set_account(self, number):
        """
        Переключает активный аккаунт "на лету" и ПЕРЕНОСИТ на него юзербот: обработчики снимаются со старого
        клиента и вешаются на новый, а старый клиент отключается. Раньше старый клиент оставался подключённым
        со всеми обработчиками — и юзербот после «переноса» продолжал работать на обоих аккаунтах сразу.
        """
        self._guest_guard('переключение аккаунта', 'account_manager')
        old = self.client
        client = await self.accounts.set_account(number)
        if not client.is_connected():
            await client.connect()
        me = await client.get_me()
        if me is None:
            raise RuntimeError(f"Аккаунт {number}: сессия недействительна — войдите в него заново (.acc)")
        if old is not None and old is not client:
            self._detach_handlers(old)                 # старый аккаунт сразу перестаёт отвечать на команды
        self.client = client
        self._owner_id = me.id
        self._attach_handlers(self.client)
        # даём знать модулям (см. decorators.on_account_switch(),
        # modules/builtin/Startup.py), что юзербот перешёл на другой аккаунт —
        # ПОСЛЕ того, как self.client/self._owner_id уже обновлены, чтобы
        # хэндлеры сразу видели актуальный аккаунт.
        await self._fire_account_switch(number)
        if old is not None and old is not client:
            # модули (пространство Hikka и др.) уже сняли свои обработчики со старого клиента — отключаем его
            for num, c in list(self.accounts.clients.items()):
                if c is old:
                    self.accounts.clients.pop(num, None)
            try:
                await old.disconnect()
            except Exception as e:
                self.tlog(f"[accounts] старый клиент не отключился: {e}")
        return client

    def _detach_handlers(self, client):
        """Снимает с client все обработчики событий, которые вешает _attach_handlers()."""
        for cb, ev in ((self._on_new_message, events.NewMessage), (self._on_chat_action, events.ChatAction),
                       (self._on_callback, events.CallbackQuery), (self._on_edit, events.MessageEdited),
                       (self._on_delete, events.MessageDeleted)):
            try:
                while client.remove_event_handler(cb, ev):
                    pass
            except Exception:
                pass
        try:
            cross = getattr(self.botchat, '_on_cross', None)
            if cross is not None:
                while client.remove_event_handler(cross, events.NewMessage):
                    pass
        except Exception:
            pass

    async def _fire_account_switch(self, number):
        for module_name, func in list(self.on_account_switch_handlers):
            await self._safe_call(module_name, func, number)

    # ------------------------------------------------------------------ #
    # Лог
    # ------------------------------------------------------------------ #

    def tlog(self, text):
        """Лог — отправляет text в терминал."""
        print(f"[FRX] {text}")

    def get_version(self):
        """Версия ЯДРА FRX (см. FRX_VERSION вверху этого файла), например '1.0.0'."""
        return self.version

    # ------------------------------------------------------------------ #
    # остановка процесса и статические файлы
    # ------------------------------------------------------------------ #

    def request_shutdown(self, code: int = 0):
        """
        Просит main.py корректно остановить юзербот и завершить процесс с
        кодом code. Коды понимает frx_systemkeeper.py: 0 — остановиться,
        42 — перезагрузить, 43 — применить заявку (обновление/восстановление
        бэкапа из user/data/keeper/request.json). Обычным модулям почти всегда
        нужен .restart (modules/builtin/SystemKeeper.py), а не прямой вызов.
        """
        self._guest_guard('остановка юзербота', 'systemkeeper', 'backup')
        self.exit_code = int(code)
        self._exit_event.set()

    async def wait_shutdown(self):
        """Ждёт request_shutdown() — используется main.py."""
        await self._exit_event.wait()

    def asset_path(self, *parts) -> Path:
        """
        Путь к файлу-ассету, например engine.asset_path('logo.png'). Сначала ищется
        в своих ассетах пользователя (user/assets/), затем в стоковых (assets/ проекта),
        так что свой файл с тем же именем подменяет стоковый. Выйти за пределы папки
        ассетов нельзя (ValueError); нет файла — FileNotFoundError.
        """
        for base_dir in (USER_ASSETS_DIR, ASSETS_DIR):
            base = base_dir.resolve()
            path = base.joinpath(*parts).resolve()
            if path != base and base not in path.parents:
                raise ValueError("Путь выходит за пределы папки assets")
            if path.exists():
                return path
        raise FileNotFoundError(f"В assets нет файла: {'/'.join(map(str, parts))}")

    # ------------------------------------------------------------------ #
    # Инлайн-формы (core/inline_forms.py) — публичный API для модулей
    # ------------------------------------------------------------------ #

    async def create_inlineform(self, chat_id, text, buttons=None,
                                 for_user=None, black_list=None, timeout=None, reply_to=None, media=None,
                                 styles=None):
        """
        Создаёт "живую" инлайн-форму — сообщение от лица стационарного
        бота ("via @bot"), которым управляет юзербот: текст/кнопки можно
        менять сколько угодно раз (form.edit(...)), клики обрабатываются
        через form.on_click(...). Форма не закрывается сама (кроме
        timeout=...) — только по form.close().

        for_user — кто может жать кнопки формы:
            None (по умолчанию) или 'me'  -> только владелец юзербота
            int > 0                        -> конкретный user_id
            int < 0                        -> все участники этого чата/канала
            'all'                          -> кто угодно, кто видит сообщение
                                               (открытая игра, голосование)
            list из перечисленного выше    -> любое совпадение (можно
                                               мешать юзеров и чаты)
        black_list — id или список id, которым доступ запрещён ВСЕГДА,
                     даже если они проходят по for_user.
        timeout — секунд бездействия (без кликов), после которых форма
                  закроется сама. None (по умолчанию) — бесконечно.
        media: картинка или анимация над текстом формы: путь к файлу, http(s)-ссылка или file_id
                  бота. Анимация — .mp4/.gif/.webm (как GIF в Telegram, без звука), картинка — .jpg/.png/.webp.
                  Текст тогда становится подписью (Telegram ограничивает её 1024 символами), form.edit()
                  меняет подпись и кнопки. Файл загружается ботом один раз, дальше берётся из кэша.
        styles: цвет кнопок по их callback_data — {'del': 'danger', 'ok': 'success', 'info': 'primary'}
                  ('danger' красный, 'success' зелёный, 'primary' синий; синонимы red/green/blue).
                  Цвет можно задать и у самой кнопки третьим элементом: ("Удалить", "del", "danger"),
                  и у InputButton(..., style='primary'). Нужен aiogram >= 3.25, на старом — кнопки без цвета.
        reply_to: если команда отправлена ОТВЕТОМ на сообщение, форма
                  по умолчанию (None) тоже отвечает на него. False — не отвечать,
                  int — ответить на сообщение с этим id.

        Требует запущенного стационарного бота (aiogram_token в user/config.json)
        и включённого у бота инлайн-режима + inline feedback 100% в BotFather
        (/setinline, /setinlinefeedback) — иначе бот не сможет подтвердить
        форму и вызов упадёт по TimeoutError.

        buttons принимает и обычные кнопки (text, callback_data), и
        InputButton(text, data, hint="Enter value...") — "Введите значение":
        тап сразу переключает поле ввода на "@bot <hint>", пользователь
        печатает значение и отправляет его инлайн-результатом — оно
        приходит в form.on_input(data=...)-хендлеры формы (см.
        core/inline_forms.py, InputButton/InlineInput и README «Инлайн-формы»).
        """
        # команда запущена с сайта (Emulate) — форма рисуется на сайте, бот не нужен
        emu = current_emulation.get()
        if emu is not None and emu.owns_chat(chat_id):
            return emu.create_form(chat_id, text, buttons, for_user=for_user,
                                   black_list=black_list, timeout=timeout, styles=styles)
        if self._aiogram_bot is None:
            raise RuntimeError(
                "Стационарный бот не запущен (aiogram_token не задан в user/config.json) — "
                "инлайн-формы недоступны"
            )
        owner_module = current_module.get()
        if owner_module is None:
            raise RuntimeError("create_inlineform() нужно вызывать изнутри хэндлера модуля")
        guest = current_guest.get()
        if for_user is None and guest is not None and self.access.get_user(guest):
            for_user = ['me', guest]          # форма по команде гостя — её кнопки доступны и ему (если классу разрешены формы)

        styles = {str(k): normalize_style(v) for k, v in (styles or {}).items()}
        confirm_timeout = await self._inline_confirm_timeout()
        # Медиа, которое бот ещё ни разу не загружал (первый показ, большой файл), не задерживает форму: она сразу
        # появляется с лёгкой анимацией «загрузка…» (assets/loading.gif, ~9 КБ), а настоящий файл подменяет её,
        # как только загрузится (_swap_form_media). Уже загруженное (file_id в кэше) показывается сразу.
        media_ref, media_later = None, None
        if media:
            media_ref = await self._bot_media(media, lookup_only=True)
            if media_ref is None:
                placeholder = await self._loading_media()
                if placeholder is not None:
                    media_ref, media_later = placeholder, media
                else:
                    media_ref = await self._bot_media(media)

        # Пагинация (см. core/inline_forms.py) — считаем СРАЗУ,
        # чтобы отдать в pending_forms уже готовую (обрезанную по странице 0,
        # с рядом навигации, если нужно) клавиатуру: именно её aiobot/bot.py
        # отправит первым ответом на inline_query — без этого Telegram мог бы
        # отклонить markup, если buttons исходно больше MAX_BUTTONS_PER_PAGE.
        pages, total_pages = paginate_button_rows(buttons or [])
        rendered0 = render_page_rows(pages, 0, total_pages)

        form_id = uuid.uuid4().hex
        loop = asyncio.get_event_loop()
        fut = loop.create_future()
        self._pending_forms[form_id] = {
            'future': fut, 'chat_id': chat_id, 'created_at': time.monotonic(),
            'text': text, 'media': media_ref, 'buttons': rendered0, 'styles': styles,  # чтобы aiobot/bot.py мог сразу
                                                 # ответить на inline_query РЕАЛЬНЫМ
                                                 # (уже постранично нарезанным)
                                                 # содержимым формы, без лишнего
                                                 # edit() сразу после подтверждения
        }

        try:
            bot_entity = await self._get_bot_entity()
            # раньше здесь был голый self.client.get_entity(chat_id),
            # который падал ValueError'ом, если юзербот ещё не "видел" этого
            # пользователя/чат в этой сессии (нет диалога/апдейта с ним) — см.
            # _resolve_entity() выше по файлу.
            chat_entity = await self._resolve_input_entity(chat_id)   # из кэша сессии, без запроса к Telegram

            # client.inline_query() = аналог GetInlineBotResultsRequest, но
            # без ручной сборки RPC; entity=chat_entity сразу задаёт чат,
            # куда результат уйдёт по умолчанию при .click()
            try:
                results = await self.client.inline_query(bot_entity, form_id, entity=chat_entity)
            except BotInlineDisabledError as e:
                # понятная причина вместо голого BotInlineDisabledError
                raise RuntimeError(
                    "У стационарного бота выключен инлайн-режим (BotFather: /setinline). "
                    "Перезапустите юзербот — Startup включит инлайн-режим и инлайн-фидбэк сам."
                ) from e
            if not results:
                raise RuntimeError(
                    "Бот не вернул результат на инлайн-запрос — проверьте, что "
                    "у бота включён инлайн-режим (/setinline в BotFather)"
                )
            reply_id = resolve_form_reply(current_event.get(), chat_id, reply_to)
            if reply_id:
                sent_msg = await results[0].click(reply_to=reply_id)      # форма — ответ на то же сообщение, что и команда
            else:
                sent_msg = await results[0].click()  # = SendInlineBotResultRequest, random_id генерится сам

            try:
                inline_message_id = await asyncio.wait_for(fut, timeout=confirm_timeout)
            except asyncio.TimeoutError:
                raise TimeoutError(
                    "Бот не подтвердил форму за отведённое время — проверьте "
                    "/setinlinefeedback 100% для бота в BotFather"
                )
        finally:
            self._pending_forms.pop(form_id, None)

        form = InlineForm(
            core=self, form_id=form_id, inline_message_id=inline_message_id,
            chat_id=chat_id, owner_module=owner_module,
            for_user=for_user, black_list=black_list, timeout=timeout,
        )
        form.media = media_ref
        form.styles = styles
        form.message_id = getattr(sent_msg, 'id', None)    # для form.delete()
        self._active_forms[inline_message_id] = form
        form._init_state(text, buttons, pages)
        if media_later is not None:
            form._media_task = asyncio.ensure_future(self._swap_form_media(form, media_later))

        if not buttons:
            # aiobot/bot.py ответил на inline_query с ПЛЕЙСХОЛДЕР-кнопкой
            # (иначе Telegram не отдал бы inline_message_id, см. комментарий
            # в aiobot/bot.py) — раз модуль кнопок не просил, снимаем её
            # единственным edit(). Если buttons были — реальный текст и
            # кнопки уже ушли в чат сразу в ответе на inline_query, edit()
            # здесь не нужен вообще.
            await form.edit(text, buttons=None)
        return form

    _PHOTO_EXT = ('.jpg', '.jpeg', '.png', '.webp')

    async def _bot_media(self, media, lookup_only=False):
        """путь / ссылка / file_id → {'kind': 'animation'|'photo', 'file_id': ...} для инлайн-формы.
        Bot API отдаёт инлайн-результат с медиа только по file_id, поэтому файл один раз отправляется ботом владельцу
        и сразу удаляется; file_id кэшируется в БД (ключ — путь+размер+время изменения или сама ссылка)."""
        if isinstance(media, dict) and media.get('file_id'):
            return {'kind': media.get('kind', 'animation'), 'file_id': media['file_id']}
        src = str(media).strip()
        from aiogram.types import FSInputFile, URLInputFile
        is_url = bool(re.match(r'^https?://', src, re.I))
        if not is_url and not os.path.exists(src):
            if re.fullmatch(r'[\w-]{20,}', src):                    # похоже на file_id бота
                return {'kind': 'animation', 'file_id': src}
            raise FileNotFoundError(f"Медиа для формы не найдено: {src}")
        low = src.lower().split('?')[0]
        kind = 'photo' if low.endswith(self._PHOTO_EXT) else 'animation'
        if is_url:
            key = src
        else:
            st = os.stat(src)
            key = f"{os.path.abspath(src)}|{st.st_size}|{int(st.st_mtime)}"
        cache = await self.db.get('core:form_media', {}) or {}
        if key in cache:
            return {'kind': cache[key][0], 'file_id': cache[key][1]}
        if lookup_only:                                  # ещё не загружали — вызывающий покажет заглушку «загрузка…»
            return None
        bot = self._aiogram_bot
        file = URLInputFile(src) if is_url else FSInputFile(src)

        async def upload():
            if kind == 'photo':
                return await bot.send_photo(self._owner_id, file, disable_notification=True)
            return await bot.send_animation(self._owner_id, file, disable_notification=True)
        try:
            msg = await upload()
        except Exception as e:
            if not self._bot_cant_write(e):
                raise
            await self._open_bot_dialog()
            await asyncio.sleep(1.0)
            msg = await upload()
        if kind == 'photo':
            file_id = msg.photo[-1].file_id
        else:
            obj = msg.animation or msg.video or msg.document
            file_id = obj.file_id
        try:
            await bot.delete_message(self._owner_id, msg.message_id)
        except Exception:
            pass
        cache[key] = [kind, file_id]
        await self.db.set('core:form_media', cache)
        return {'kind': kind, 'file_id': file_id}

    async def _loading_media(self):
        """file_id заглушки «загрузка…» (assets/loading.gif — можно подменить своим user/assets/loading.gif) или None."""
        try:
            return await self._bot_media(str(self.asset_path('loading.gif')))
        except Exception as e:
            self.tlog(f"[core] заглушка «загрузка…» недоступна: {e}")
            return None

    async def _swap_form_media(self, form, media):
        """Загружает настоящее медиа формы и меняет им заглушку «загрузка…» (текст, кнопки и страница — текущие)."""
        from aiogram.types import InputMediaAnimation, InputMediaPhoto
        try:
            real = await self._bot_media(media)
        except Exception as e:
            self.tlog(f"[core] медиа формы не загрузилось: {e}")
            try:
                await self._inline_edit(form.inline_message_id, (form.text or '') + "\n\n<i>медиа не загрузилось</i>",
                                        render_page_rows(form._pages, form.page, len(form._pages)), form.media, styles=form.styles)
            except Exception:
                pass
            return
        if form._closed:
            return
        cls = InputMediaPhoto if real['kind'] == 'photo' else InputMediaAnimation
        rows = render_page_rows(form._pages, form.page, len(form._pages))
        markup = self._build_markup(rows, form.styles) if rows else None
        for attempt in range(3):
            try:
                await self._aiogram_bot.edit_message_media(
                    inline_message_id=form.inline_message_id, reply_markup=markup,
                    media=cls(media=real['file_id'], caption=_caption(form.text) or None, parse_mode='HTML'))
                form.media = real
                return
            except Exception as e:
                if 'not modified' in str(e).lower():
                    form.media = real
                    return
                if attempt == 2:
                    self.tlog(f"[core] не удалось заменить «загрузка…» медиа формы: {e}")
                    return
                await asyncio.sleep(1.0 + attempt)

    # ------------------------------------------------------------------ #
    # @bot.inline_query / @bot.inline_click / @bot.start_link — core/inline_results.py
    # ------------------------------------------------------------------ #

    InlineResult = InlineResult

    async def dispatch_inline_query(self, iq):
        """Инлайн-запрос «@бот имя аргументы»: если имя принадлежит @bot.inline_query — результаты aiogram, иначе None."""
        q = (iq.query or '').strip()
        if not q or not self.bot_inline_query_map:
            return None
        name, _, rest = q.partition(' ')
        entry = self.bot_inline_query_map.get(name.lower())
        if entry is None:
            return None
        module_name, func, access, for_user, for_chat, black_list, black_chats = entry
        user_id = iq.from_user.id if iq.from_user else None
        if not self._bot_extended_access_ok(access, for_user, for_chat, black_list, black_chats, user_id, None):
            return []
        event = InlineQueryEvent(iq, name.lower(), rest.strip())
        gtoken = current_guest.set(None if user_id == self._owner_id else user_id)
        try:
            results = await self._safe_call(module_name, func, event)
        finally:
            current_guest.reset(gtoken)
        results = [InlineResult(**r) if isinstance(r, dict) else r for r in (results or [])[:50] if r is not None]
        chosen = self.__dict__.setdefault('_iq_chosen', {})
        now = time.monotonic()
        for k in [k for k, v in chosen.items() if now - v[2] > 600]:
            chosen.pop(k, None)
        for r in results:
            if r.on_chosen is not None:
                chosen[r.id] = (module_name, r.on_chosen, now)
        return [self._aiogram_inline_result(r) for r in results]

    async def dispatch_inline_chosen(self, result_id, inline_message_id):
        """Результат @bot.inline_query отправлен в чат — его on_chosen. True — это был он."""
        entry = self.__dict__.get('_iq_chosen', {}).pop(result_id, None)
        if entry is None:
            return False
        await self._safe_call(entry[0], entry[1], inline_message_id)
        return True

    def _aiogram_inline_result(self, r):
        from aiogram.types import InlineQueryResultArticle, InlineQueryResultPhoto, InputTextMessageContent
        if isinstance(r, dict):
            r = InlineResult(**r)
        markup = self._build_markup(r.buttons, r.styles) if r.buttons else None
        if r.photo:
            return InlineQueryResultPhoto(id=r.id, photo_url=r.photo, thumbnail_url=r.thumb or r.photo, title=r.title,
                                          description=r.description, caption=_caption(r.text or '') or None,
                                          parse_mode='HTML', reply_markup=markup)
        kw = {}
        lp = link_preview_options(r.link_preview)
        if lp is not None:
            kw['link_preview_options'] = lp
        return InlineQueryResultArticle(
            id=r.id, title=r.title, description=r.description, thumbnail_url=r.thumb or None, reply_markup=markup,
            input_message_content=InputTextMessageContent(message_text=(r.text or r.title)[:4096], parse_mode='HTML', **kw))

    async def _dispatch_module_inline_click(self, callback_query):
        """Кнопка под сообщением @bot.inline_query. True — обработано модулем."""
        data = callback_query.data or ''
        for module_name, prefix, func, access in list(self.bot_inline_clicks):
            if not data.startswith(prefix):
                continue
            click = InlineQueryClick(self, callback_query)
            if not self._bot_access_ok(access, click.user_id):
                await click.answer("Эта кнопка не для вас", show_alert=True)
                return True
            await self._click_bucket.acquire()
            gtoken = current_guest.set(None if click.user_id == self._owner_id else click.user_id)
            try:
                await self._safe_call(module_name, func, click)
            finally:
                current_guest.reset(gtoken)
            await click.answer()
            return True
        return False

    async def dispatch_bot_callback(self, callback_query):
        """
        Кнопка под ОБЫЧНЫМ сообщением бота (engine.bot.send_message(..., buttons=...)) — @bot.callback модулей.
        Вызывается ботом на callback_query без inline_message_id. True — обработано модулем.
        """
        from .bot_events import BotClick
        data = callback_query.data or ''
        for module_name, prefix, func, access in list(self.bot_callbacks):
            if prefix and not data.startswith(prefix):
                continue
            click = BotClick(self, callback_query)
            if not self._bot_access_ok(access, click.user_id):
                await click.answer("Эта кнопка не для вас", show_alert=True)
                return True
            await self._click_bucket.acquire()
            gtoken = current_guest.set(None if click.user_id == self._owner_id else click.user_id)
            try:
                await self._safe_call(module_name, func, click)
            finally:
                current_guest.reset(gtoken)
            await click.answer()
            return True
        return False

    async def dispatch_start_link(self, message, payload):
        """/start <payload> у бота: модуль с @bot.start_link(prefix). True — обработано."""
        from .bot_events import BotMessageEvent
        for module_name, prefix, func in list(self.bot_start_links):
            if payload.startswith(prefix):
                await self._safe_call(module_name, func, BotMessageEvent(message, self), payload[len(prefix):])
                return True
        return False

    @staticmethod
    def progress_bar(percent, width=10, style='block', show_percent=False, brackets=False):
        """СИНХРОННО: полоска прогресса текстом — см. visual.progress_bar (17 стилей: block, smooth, bar, frx, …)."""
        from .visual import progress_bar
        return progress_bar(percent, width, style, show_percent, brackets)

    def get_pending_form(self, form_id):
        """Вызывается ботом на inline_query — отдаёт (text, buttons), которые нужно показать сразу."""
        return self._pending_forms.get(form_id)

    def build_inline_markup(self, buttons, styles=None):
        """Публичная обёртка над _build_markup() — вызывается ботом при ответе на inline_query."""
        return self._build_markup(buttons, styles)

    def set_inline_click_rate(self, rate):
        """Обновляет лимит обработки кликов инлайн-форм (кликов/сек)."""
        self._click_bucket.set_rate(rate)

    # ------------------------------------------------------------------ #
    # bot.inline_function() — см. core/bot_decorators.py
    # ------------------------------------------------------------------ #

    def match_inline_functions(self, query, limit=50):
        """
        Вызывается ботом (aiobot/bot.py, handle_inline_query) на каждый
        инлайн-запрос, который не является ни подтверждением формы, ни
        вводом значения через InputButton (см. Core.get_pending_form/
        Core.get_pending_input) — то есть на "@bot <что угодно>", которое
        может оказаться попыткой вызвать bot.inline_function().

        Разбирает query на "название функции" (первое слово) и "аргументы"
        (всё остальное). Если первое слово ТОЧНО (без учёта регистра)
        совпадает с именем ровно одной зарегистрированной инлайн-функции —
        возвращает список из ОДНОГО результата с exact=True (дальше
        aiobot/bot.py покажет один-единственный вариант "выполнить эту
        функцию с этими аргументами"). Иначе — до `limit` функций, чьё имя
        НАЧИНАЕТСЯ с первого слова (тот самый "выбор" во время набора
        названия) — с exact=False и пустыми args (ввод
        ещё не закончен).

        Возвращает [] на пустой query или если совпадений нет вовсе.
        """
        query = (query or '').strip()
        if not query:
            return []

        name_part, _, args_part = query.partition(' ')
        name_lower = name_part.lower()
        if not name_lower:
            return []

        exact = self.bot_inline_function_map.get(name_lower)
        if exact is not None:
            func = exact[1]
            return [{
                'name': name_lower, 'args': args_part.strip(), 'exact': True,
                'doc': self._first_doc_line(func),
            }]

        matches = []
        for alias, entry in self.bot_inline_function_map.items():
            if not alias.startswith(name_lower):
                continue
            matches.append({
                'name': alias, 'args': '', 'exact': False,
                'doc': self._first_doc_line(entry[1]),
            })
            if len(matches) >= limit:
                break
        return matches

    @staticmethod
    def _first_doc_line(func):
        doc = (func.__doc__ or '').strip()
        return doc.splitlines()[0].strip() if doc else ''

    async def _dispatch_inline_function_message(self, event, name, args):
        """
        Вызывается из Core._on_new_message() на входящее сообщение с
        via_bot_id == id стационарного бота, чей текст распознан как
        приглашение bot.inline_function() (см. _INLINE_FUNCTION_RE в
        aiobot-совместимом парсере ниже / core/bot_decorators.py). event —
        настоящее telethon-событие (chat_id/sender_id уже настоящие,
        в отличие от Bot API инлайн-апдейтов, см. докстринг
        bot.inline_function()).
        """
        entry = self.bot_inline_function_map.get(name.lower())
        if entry is None:
            return False
        module_name, func, access, for_user, for_chat, black_list, black_chats = entry
        if not self._bot_extended_access_ok(
            access, for_user, for_chat, black_list, black_chats,
            event.sender_id, event.chat_id,
        ):
            return False

        mtoken = current_module.set(module_name)
        etoken = current_event.set(event)
        try:
            await self._safe_call_bot(module_name, func, event, args)
        finally:
            current_event.reset(etoken)
            current_module.reset(mtoken)
        return True

    async def _get_bot_entity(self):
        """
        Стационарный бот глазами юзербота. Резолвится по username ОДИН раз (ResolveUsername — сетевой запрос
        с жёстким флуд-лимитом), дальше берётся из кэша: раньше каждая инлайн-форма делала этот запрос заново.
        """
        cached = getattr(self, '_bot_entity_cache', None)
        if cached is not None and cached[0] is self.client:
            return cached[1]
        if self._bot_username is None:
            me = await self._aiogram_bot.get_me()
            self._bot_username = me.username
        entity = await self.client.get_entity(self._bot_username)
        self._bot_entity_cache = (self.client, entity)
        return entity

    async def _resolve_input_entity(self, entity_id, chat_id=None):
        """
        Как _resolve_entity(), но для отправки хватает InputPeer: сначала берётся из кэша сессии Telethon
        (get_input_entity — без сетевого запроса), и только если его там нет — полный резолв.
        """
        try:
            return await self.client.get_input_entity(entity_id)
        except (ValueError, TypeError):
            return await self._resolve_entity(entity_id, chat_id=chat_id)

    async def _get_bot_id(self):
        if self._bot_id is None:
            entity = await self._get_bot_entity()
            self._bot_id = entity.id
        return self._bot_id

    async def _inline_confirm_timeout(self):
        try:
            return await self.get_config('confirm_timeout', module_name='inline_stuff')
        except KeyError:
            return 10

    async def _inline_member_cache_ttl(self):
        try:
            return await self.get_config('member_cache_ttl', module_name='inline_stuff')
        except KeyError:
            return 300

    def _normalize_button_rows(self, buttons):
        """
        Приводит buttons к списку РЯДОВ (список списков кнопок) — общая
        логика для _build_markup() и _register_input_targets(), см.
        докстринг _build_markup() про поддерживаемые форматы.
        """
        if not buttons:
            return []
        first = buttons[0]
        is_flat_single_row = isinstance(first, InputButton) or (
            isinstance(first, (list, tuple)) and len(first) == 2
            and isinstance(first[0], str) and isinstance(first[1], str)
        )
        return [buttons] if is_flat_single_row else buttons

    def _build_markup(self, buttons, styles=None):
        """
        Принимает buttons в двух форматах:
          - список РЯДОВ, каждый ряд — список кнопок:
                [[("Ок", "ok")], [("Да", "yes"), ("Нет", "no")]]
          - один ряд без внешней обёртки (шорткат для формы с одной строкой кнопок):
                [("Ок", "ok"), ("Да", "yes")]
        Каждая кнопка — (text, callback_data), (text, callback_data, style),
        {'text', 'data', 'style'} или InputButton(text, data, hint=..., style=...) —
        кнопка "Введите значение" (core/inline_forms.py, см. docstring
        InputButton и README «Инлайн-формы»). Все виды можно свободно мешать.
        style — цвет кнопки: 'primary' (синий), 'success' (зелёный), 'danger' (красный);
        styles — {callback_data: style} на всю форму (create_inlineform(styles=...)).
        """
        if not buttons:
            return None
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        rows = self._normalize_button_rows(buttons)

        def make(style, **kw):
            # премиум-эмодзи в начале подписи → иконка кнопки (icon_custom_emoji_id), остальные — обычным эмодзи
            text, icon = split_button_emoji(kw.get('text'))
            kw['text'] = text
            if icon:
                kw['icon_custom_emoji_id'] = icon
            for attempt in (dict(kw, style=style) if style else None, kw, {k: v for k, v in kw.items() if k != 'icon_custom_emoji_id'}):
                if attempt is None:
                    continue
                try:                              # старый aiogram не знает style / icon_custom_emoji_id — без них
                    return InlineKeyboardButton(**attempt)
                except Exception:
                    continue
            return InlineKeyboardButton(**kw)

        keyboard = []
        for row in rows:
            keyboard_row = []
            for btn in row:
                b = parse_button(btn, styles)
                if b is None:
                    raise ValueError(
                        "Некорректный формат buttons для create_inlineform()/form.edit() — "
                        "ожидается список рядов, каждый ряд — список (text, data), (text, data, style) или "
                        "InputButton(text, data, hint=...): "
                        "buttons=[[('Ок', 'ok', 'success')], [InputButton('✏️ Значение', 'val')]], "
                        "либо один ряд без вложенности: buttons=[('Ок', 'ok'), ('Отмена', 'cancel', 'danger')]"
                    )
                if b['kind'] == 'input':
                    keyboard_row.append(make(b['style'], text=b['text'], switch_inline_query_current_chat=b.get('prefill') or ''))
                elif b['kind'] == 'url':
                    keyboard_row.append(make(b['style'], text=b['text'], url=b['url']))   # кнопка-ссылка
                else:
                    keyboard_row.append(make(b['style'], text=b['text'], callback_data=b['data']))
            keyboard.append(keyboard_row)
        return InlineKeyboardMarkup(inline_keyboard=keyboard)

    async def _inline_edit(self, inline_message_id, text, buttons, media=None, styles=None):
        try:
            await self._inline_edit_raw(inline_message_id, text, buttons, media, styles)
        except Exception as e:
            # форма уже показывает ровно это (например, клик не в свой ход — модуль перерисовал то же самое):
            # для Telegram это ошибка, для формы — нет
            if 'message is not modified' in str(e).lower():
                return
            raise

    async def _inline_edit_raw(self, inline_message_id, text, buttons, media=None, styles=None):
        markup = self._build_markup(buttons, styles) if buttons is not None else None
        if text is not None and media:                      # форма с картинкой/анимацией — меняем подпись
            await self._aiogram_bot.edit_message_caption(
                caption=_caption(text), inline_message_id=inline_message_id,
                reply_markup=markup, parse_mode='HTML',
            )
            return
        if text is None:
            await self._aiogram_bot.edit_message_reply_markup(
                inline_message_id=inline_message_id, reply_markup=markup,
            )
        else:
            await self._aiogram_bot.edit_message_text(
                text=text, inline_message_id=inline_message_id,
                reply_markup=markup, parse_mode='HTML',
            )

    # --- InputButton ("Введите значение") — регистрация/диспетчеризация ---- #

    def _clear_input_targets_for_form(self, form):
        """Снимает все зарегистрированные InputButton-цели, указывающие на эту форму."""
        for user_id, pending in list(self._pending_inputs.items()):
            if pending['form'] is form:
                self._pending_inputs.pop(user_id, None)

    def _resolve_input_targets(self, form, btn: 'InputButton'):
        """
        Кому засчитается следующий ввод по этой конкретной InputButton —
        те же правила, что и form.for_user (см. _check_form_access), но
        btn.for_user, если задан, переопределяет их именно для этой кнопки.
        В отличие от for_user формы (кликов), сюда НЕ разворачиваются
        отрицательные id (целые чаты) — "ожидание ввода" всегда должно
        указывать на конкретного человека, иначе непонятно, чей ввод ждать.
        """
        allowed = btn.for_user if btn.for_user is not None else form.for_user
        if allowed is None:
            allowed = 'me'
        if not isinstance(allowed, list):
            allowed = [allowed]

        targets = set()
        for rule in allowed:
            if rule == 'me':
                if self._owner_id is not None:
                    targets.add(self._owner_id)
            elif isinstance(rule, int) and rule > 0:
                targets.add(rule)
        return targets          # 'all' и id чатов сюда не разворачиваются: ввод ждут от конкретных людей

    def _register_input_targets(self, form, buttons):
        """
        Сканирует buttons на InputButton и заводит для каждой найденной
        кнопки запись в self._pending_inputs на всех разрешённых юзеров
        (см. _resolve_input_targets) — это и есть то самое "ожидание
        ввода", которое ловит handle_inline_query() в aiobot/bot.py,
        когда юзер печатает "@bot <значение>" после тапа по кнопке.

        Вызывается из InlineForm.edit() и сразу после создания формы в
        create_inlineform() — на каждый вызов сначала СНИМАЮТСЯ все
        предыдущие цели ввода, привязанные к этой форме (актуальный набор
        кнопок мог поменяться или потерять InputButton вовсе), а потом
        регистрируются заново по свежему списку buttons.

        На один user_id может существовать только ОДНА ожидаемая
        InputButton одновременно (по любой форме) — это не искусственное
        ограничение движка, а следствие самого Telegram-клиента: он не
        даст пользователю одновременно "переключиться" в инлайн-режим по
        двум разным кнопкам сразу. Поэтому регистрация новой цели для
        уже занятого user_id молча перекрывает старую.
        """
        self._clear_input_targets_for_form(form)
        for row in self._normalize_button_rows(buttons):
            for btn in row:
                if not isinstance(btn, InputButton):
                    continue
                for user_id in self._resolve_input_targets(form, btn):
                    self._pending_inputs[user_id] = {
                        'form': form, 'data': btn.data, 'chat_id': form.chat_id, 'hint': btn.hint, 'title': btn.text,
                        'created_at': time.monotonic(),
                    }

    def get_pending_input(self, user_id):
        """
        Вызывается ботом (aiobot/bot.py, handle_inline_query) — есть ли у
        этого user_id ожидаемый ввод значения через InputButton прямо
        сейчас. Возвращает dict {'form','data','chat_id','created_at'} | None.
        """
        return self._pending_inputs.get(user_id)

    async def dispatch_inline_input(self, chosen):
        """
        Вызывается ботом (aiobot/bot.py, handle_chosen_inline_result) на
        chosen_inline_result, чей result_id помечен как ответ на
        InputButton (см. aiobot/bot.py, _INPUT_RESULT_PREFIX).
        chosen.query — это ровно тот текст, который набрал и отправил
        пользователь. Как и подтверждение create_inlineform(), это требует
        включённого у бота /setinlinefeedback 100% — иначе chosen_inline_result
        до бота не доходит вообще.
        """
        user_id = chosen.from_user.id if chosen.from_user else None
        if user_id is None:
            return
        pending = self._pending_inputs.get(user_id)
        if pending is None:
            return

        form = pending['form']
        # разовый ввод — снимаем цель сразу; если хэндлеру снова нужен
        # ввод (например, невалидное значение), он сам перевешивает
        # InputButton через form.edit(...), что заново её зарегистрирует
        self._pending_inputs.pop(user_id, None)
        if form._closed:
            return

        await self._click_bucket.acquire()
        from_user = await self._resolve_clicker(user_id, form.chat_id)
        inp = InlineInput(
            data=pending['data'], value=chosen.query, from_user=from_user,
            chat_id=form.chat_id, form=form,
        )
        await self._register_input_echo_for_deletion(form.chat_id, chosen.query)
        gtoken = current_guest.set(None if user_id == self._owner_id else user_id)
        try:
            await form._dispatch_input(inp)
        finally:
            current_guest.reset(gtoken)

    # --- удаление "эхо"-сообщения InputButton: гонка между Bot API и MTProto ----- #
    #
    # "Эхо"-сообщение (текст, который пользователь набрал и отправил через
    # InputButton) прилетает в чат через MTProto — сюда, в Core._on_new_message
    # (telethon). А chosen_inline_result, по которому dispatch_inline_input()
    # выше узнаёт, ЧТО нужно удалить — отдельным каналом, через Bot API
    # (aiobot/aiogram). Эти два события НЕ гарантированно приходят в одном
    # порядке: Bot API обычно медленнее, поэтому сообщение нередко долетает
    # РАНЬШЕ, чем dispatch_inline_input() успевает зарегистрировать, что его
    # нужно удалить. Чтобы порядок доставки не имел значения, проверка идёт
    # в обе стороны:
    #   - _on_new_message()/_handle_bot_echo_message() при виде исходящего
    #     "via-bot"-сообщения сперва пробует найти для него уже готовое
    #     ожидание (self._pending_input_deletes) — обычный порядок, когда
    #     chosen_inline_result пришёл первым; если не нашлось — кладёт само
    #     сообщение в буфer self._recent_bot_echoes (на случай обратного
    #     порядка) и ждёт;
    #   - _register_input_echo_for_deletion() (вызывается из
    #     dispatch_inline_input()) сначала проверяет тот же буфер — если
    #     сообщение уже успело прилететь и осесть там, удаляет его сразу;
    #     если нет — кладёт ожидание в self._pending_input_deletes как раньше.
    # Оба буфера — с коротким TTL (15 секунд), чтобы не копить мусор, если
    # что-то пошло не так (сообщение вообще не пришло/не удалось удалить).

    _ECHO_MATCH_TTL = 15

    async def _register_input_echo_for_deletion(self, chat_id, value):
        """См. блок комментариев выше. Вызывается из dispatch_inline_input()."""
        echo_text = value if value else "…"  # пустая query -> aiobot/bot.py шлёт "…" (см. handle_inline_query)

        # сообщение уже могло прилететь раньше нас (гонка Bot API vs MTProto) —
        # если нашли совпадение в буфере "увиденных" эхо-сообщений, удаляем
        # его прямо сейчас и не заводим отдельное ожидание
        recent = self._recent_bot_echoes.get(chat_id)
        if recent:
            now = time.monotonic()
            recent[:] = [item for item in recent if now - item['seen_at'] <= self._ECHO_MATCH_TTL]
            for i, item in enumerate(recent):
                if item['text'] != echo_text:
                    continue
                recent.pop(i)
                if not recent:
                    self._recent_bot_echoes.pop(chat_id, None)
                await self._delete_message_safely(chat_id, item['message_id'])
                return
            if not recent:
                self._recent_bot_echoes.pop(chat_id, None)

        self._pending_input_deletes.setdefault(chat_id, []).append({
            'value': echo_text, 'created_at': time.monotonic(),
        })

    async def _handle_bot_echo_message(self, event):
        """
        Вызывается из _on_new_message() на КАЖДОЕ исходящее сообщение,
        отправленное "via" наш стационарный бот. Возвращает True, если это
        оказалось эхо InputButton и оно было удалено (тогда _on_new_message
        дальше событие не разбирает) — False, если это что-то другое
        (обычное сообщение, отправленное через inline-режим бота вручную)
        или совпадения пока нет (сообщение прилетело раньше
        chosen_inline_result — тогда оно кладётся в буфер до тех пор, пока
        не подоспеет dispatch_inline_input(), см. блок комментариев выше).
        """
        chat_id = event.chat_id
        text = event.raw_text or ''
        now = time.monotonic()

        # обычный порядок: ожидание уже зарегистрировано dispatch_inline_input()
        pending = self._pending_input_deletes.get(chat_id)
        if pending:
            pending[:] = [item for item in pending if now - item['created_at'] <= self._ECHO_MATCH_TTL]
            for i, item in enumerate(pending):
                if item['value'] != text:
                    continue
                pending.pop(i)
                if not pending:
                    self._pending_input_deletes.pop(chat_id, None)
                await self._delete_message_safely(chat_id, event.id)
                return True
            if not pending:
                self._pending_input_deletes.pop(chat_id, None)

        # обратный порядок: chosen_inline_result ещё не пришёл — запоминаем
        # сообщение и ждём, пока его подхватит _register_input_echo_for_deletion()
        recent = self._recent_bot_echoes.setdefault(chat_id, [])
        recent[:] = [item for item in recent if now - item['seen_at'] <= self._ECHO_MATCH_TTL]
        recent.append({'text': text, 'message_id': event.id, 'seen_at': now})
        return False

    async def _delete_message_safely(self, chat_id, message_id):
        try:
            await self.client.delete_messages(chat_id, [message_id])
        except Exception as e:
            self.tlog(f"Не удалось удалить сообщение с введённым значением: {e}")

    # --- вызывается из aiobot/bot.py ------------------------------------ #

    def resolve_pending_form(self, form_id, inline_message_id):
        """Вызывается ботом на chosen_inline_result — отпускает Future из create_inlineform()."""
        pending = self._pending_forms.get(form_id)
        if pending and not pending['future'].done():
            pending['future'].set_result(inline_message_id)

    async def dispatch_inline_click(self, callback_query):
        """
        Вызывается ботом на callback_query с непустым inline_message_id.
        callback_query — aiogram.types.CallbackQuery.
        """
        inline_message_id = callback_query.inline_message_id
        form = self._active_forms.get(inline_message_id)
        if form is None and await self._dispatch_module_inline_click(callback_query):
            return
        if form is None or form._closed:
            try:
                await callback_query.answer("Форма больше не активна", show_alert=True)
            except Exception:
                pass
            return

        user_id = callback_query.from_user.id

        if not await self._check_form_access(form, user_id):
            try:
                await callback_query.answer("Эта форма не для вас", show_alert=True)
            except Exception:
                pass
            return

        # Клики по служебному ряду навигации (◀/N/M/▶, см.
        # core/inline_forms.py) движок ловит здесь же и обрабатывает сам —
        # form.on_click() их не видит вообще.
        if callback_query.data in (PAGE_PREV, PAGE_NEXT, PAGE_NOOP):
            await self._click_bucket.acquire()
            if callback_query.data != PAGE_NOOP:
                delta = -1 if callback_query.data == PAGE_PREV else 1
                await form._go_to_page(form.page + delta)
            try:
                await callback_query.answer()
            except Exception:
                pass
            return

        click = InlineClick(
            data=callback_query.data, from_user=None, chat_id=form.chat_id,
            inline_message_id=inline_message_id, raw_callback=callback_query,
        )
        await self._click_bucket.acquire()
        click.from_user = await self._resolve_clicker(user_id, form.chat_id)
        gtoken = current_guest.set(None if user_id == self._owner_id else user_id)
        try:
            await form._dispatch(click)
        finally:
            current_guest.reset(gtoken)

    # --- права доступа ---------------------------------------------------- #

    async def _check_form_access(self, form, user_id):
        black = form.black_list or []
        if not isinstance(black, list):
            black = [black]
        if user_id in black:
            return False

        allowed = form.for_user
        if allowed is None:
            allowed = 'me'
        if not isinstance(allowed, list):
            allowed = [allowed]

        for rule in allowed:
            if rule == 'all':                       # кто угодно, кто видит сообщение формы
                return True
            if rule == 'me':
                if user_id == self._owner_id:
                    return True
            elif isinstance(rule, int) and rule > 0:
                if user_id == rule:
                    return True
            elif isinstance(rule, int) and rule < 0:
                if await self._is_chat_member(rule, user_id):
                    return True
        return False

    async def _is_chat_member(self, chat_id, user_id):
        cache_ttl = await self._inline_member_cache_ttl()
        key = (chat_id, user_id)
        cached = self._member_cache.get(key)
        if cached and cached[1] > time.monotonic():
            return cached[0]

        result = await self._resolve_membership(chat_id, user_id)
        self._member_cache[key] = (result, time.monotonic() + cache_ttl)
        return result

    async def _resolve_entity(self, entity_id, chat_id=None):
        """
        Надёжно возвращает полноценный telethon.types.User/Chat/Channel по
        голому id — даже если Telethon ни разу не видел access_hash для
        него в этой сессии ("вне кэша"). Голый self.client.get_entity(id) для
        id, который не был "прогрет" (ни одного входящего сообщения/апдейта
        от этого юзера, ни общих диалогов), кидает
        ValueError("Cannot find any entity corresponding to ...").
        _resolve_entity() — единая точка входа вместо него:
        пробует несколько путей резолва по нарастающей "стоимости",
        останавливается на первом успешном:

          1. client.get_entity(entity_id) — обычный путь, срабатывает,
             если entity уже кэширован (входящее сообщение/апдейт,
             предыдущий get_dialogs/get_participants и т.п.).
          2. Если передан chat_id (entity_id — участник этого чата/канала,
             например кликнувший по инлайн-форме) — форсируем резолв через
             get_permissions(chat_id, entity_id): под капотом это
             GetParticipantRequest, который возвращает участника С
             access_hash и кладёт его в сессию, — затем повторяем
             get_entity().
          3. iter_dialogs() — "прогревает" весь список диалогов аккаунта
             (личные чаты, группы, каналы) вместе с access_hash всех
             входящих в них User/Chat/Channel. Это и есть тот самый
             "автоматически получить полный объект пользователя, с кем мы
             контактируем" — если у юзербота уже есть открытый диалог с
             entity_id, он найдётся и закэшируется здесь.
          4. contacts.GetContactsRequest — то же самое, но по списку
             контактов аккаунта (на случай, когда диалога с человеком нет,
             но он есть в контактах).
          5. Если дан chat_id — последний, самый дорогой шанс:
             iter_participants(chat_id) ищет entity_id среди ВСЕХ
             участников этого чата/канала (покрывает случай большого
             чата, где нужный участник ни разу не писал и не был явно
             зарезолвлен раньше).

        Если ни один из способов не сработал — кидает RuntimeError с
        понятным описанием (вместо голого ValueError от Telethon), чтобы
        разработчик модуля сразу понимал причину, а не разбирал трейсбек
        Telethon.
        """
        try:
            return await self.client.get_entity(entity_id)
        except (ValueError, TypeError):
            pass

        if chat_id is not None and chat_id != entity_id:
            try:
                await self.client.get_permissions(chat_id, entity_id)
                return await self.client.get_entity(entity_id)
            except Exception:
                pass

        try:
            async for dialog in self.client.iter_dialogs():
                if dialog.id == entity_id:
                    return dialog.entity
        except Exception:
            pass

        try:
            from telethon.tl.functions.contacts import GetContactsRequest
            contacts = await self.client(GetContactsRequest(hash=0))
            for user in getattr(contacts, 'users', []):
                if user.id == entity_id:
                    return user
        except Exception:
            pass

        if chat_id is not None:
            try:
                async for participant in self.client.iter_participants(chat_id):
                    if participant.id == entity_id:
                        return participant
            except Exception:
                pass

        raise RuntimeError(
            f"Не удалось получить полный объект пользователя/чата {entity_id} — "
            "он вне кэша Telethon, с ним нет общего диалога/контакта, и (если "
            "был передан chat_id) он не найден среди участников чата. Юзерботу "
            "нужно хотя бы раз 'увидеть' этого пользователя (входящее "
            "сообщение, диалог, общий чат), чтобы Telegram отдал его access_hash."
        )

    async def _resolve_membership(self, chat_id, user_id):
        # 1) сначала пробуем ботом — дешевле, не грузит юзер-аккаунт
        if self._aiogram_bot is not None:
            try:
                member = await self._aiogram_bot.get_chat_member(chat_id, user_id)
                return member.status not in ('left', 'kicked')
            except Exception:
                pass  # бот не в чате / нет доступа -> идём на юзербота

        # 2) фолбэк через сам аккаунт юзербота — работает и тогда, когда
        #    владелец юзербота админ/участник приватного канала, а бота
        #    там вообще нет
        try:
            perms = await self.client.get_permissions(chat_id, user_id)
            return not getattr(perms, 'is_banned', False)
        except Exception:
            return False  # ни бот, ни юзербот не смогли проверить -> fail closed

    async def _resolve_clicker(self, user_id, chat_id):
        """
        Возвращает настоящий telethon.types.User либо None, если юзербот не
        может его резолвнуть. Использует общий _resolve_entity()
        (get_entity -> get_permissions(chat_id, ...) -> iter_dialogs() ->
        контакты -> iter_participants(chat_id)) вместо всего двух попыток —
        покрывает больше случаев "юзер вне кэша", не только участника
        текущего чата.
        """
        cache = self.__dict__.setdefault('_clicker_cache', {})
        now = time.monotonic()
        hit = cache.get(user_id)
        if hit is not None and hit[1] is self.client and now - hit[2] < 600:
            return hit[0]                       # раньше — запрос к Telegram на КАЖДЫЙ клик, до вызова хэндлера
        try:
            user = await self._resolve_entity(user_id, chat_id=chat_id)
        except Exception:
            return None
        if len(cache) > 2000:
            cache.clear()
        cache[user_id] = (user, self.client, now)
        return user
