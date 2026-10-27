"""
aiobot/bot.py

База на AIOgram 3 для параллельной работы стационарного бота рядом
с юзерботом на Telethon. Оба процесса крутятся в одном asyncio event loop
(см. main.py) — это позволяет, например, дать стационарному боту доступ
к той же БД (core.database.Database) и модулям юзербота.

Хэндлеры стационарного бота, объявленные ПРЯМО В ЭТОМ ФАЙЛЕ, используют
декораторы из aiobot/decorators.py (`from . import decorators as bot`,
`@bot.command(...)`/`@bot.watcher(...)`/`@bot.callback(...)`/
`@bot.cycle(...)`/`@bot.on_start()`/`@bot.on_error()`) — подключить свой
хэндлер здесь — значит просто объявить функцию с одним из этих
декораторов; register_decorated_handlers() сама найдёт её и подключит
к Dispatcher при создании бота (create_bot()).

Модулям юзербота НЕ обязательно лезть в этот файл — те же декораторы
(и ещё `bot.on_load()`/`bot.telegram_event()`) доступны прямо внутри
любого модуля юзербота через `import bot` (см. core/bot_decorators.py,
README, «Стационарный бот в модулях») — create_bot() сама подключает такие хэндлеры через
три служебных хэндлера ниже (handle_module_bot_messages/
handle_bot_membership_change/handle_chat_member_change), которые просто
делегируют апдейт в core.dispatch_bot_message()/dispatch_bot_telegram_event().
"""

import asyncio
import sys
import uuid

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InlineQueryResultCachedMpeg4Gif,
    InlineQueryResultCachedPhoto,
    InputTextMessageContent,
)

from . import decorators as bot
from core.time_utils import parse_interval
from core.engine import build_inline_function_text
from core import visual as visual_module
from core.html_utils import sanitize_bot_method, strip_bot_method_custom_emoji
from aiogram.exceptions import TelegramBadRequest


class FRXBot(Bot):
    """
    aiogram Bot, который перед КАЖДЫМ запросом к Bot API прогоняет HTML-поля
    (text/caption, message_text и caption результатов инлайн-запроса) через safe_html():
    настоящие теги остаются, а всё, что тегом не является (`<имя_модуля>`, `<chat_id>`,
    одиночные `<`, `&`), экранируется и показывается как есть — вместо ошибки Bot API
    "can't parse entities: Unsupported start tag". Единая точка для инлайн-форм,
    engine.bot.*, engine.bot.raw.* и message.answer(...) в bot.*-хэндлерах модулей.
    """

    async def __call__(self, method, request_timeout=None):
        try:
            sanitize_bot_method(method)
        except Exception as e:                       # санитайзер не должен ронять отправку
            print(f"[FRX] [WARN] safe_html: не удалось обработать {type(method).__name__}: {e}")
        from core.apilog import note_bot       # запрос Bot API виден в Emulate
        try:
            result = await note_bot(method, super().__call__(method, request_timeout=request_timeout))
        except TelegramBadRequest:
            # Премиум-эмодзи бот может слать, только если у владельца бота есть Telegram Premium (Bot API 9.4),
            # и не везде. Не вышло — тот же запрос с обычными эмодзи вместо премиум, а не ошибка.
            if not strip_bot_method_custom_emoji(method):
                raise
            result = await note_bot(method, super().__call__(method, request_timeout=request_timeout))
        # BotChat — всё, что бот отправил/изменил/удалил (в т.ч. модулями), попадает в историю сайта
        if _core_ref is not None:
            try:
                await _core_ref.botchat.record_result(method, result)
            except Exception as e:
                print(f"[FRX] [WARN] BotChat: не удалось записать результат {type(method).__name__}: {e}")
        return result

# Заглушка, которую видит Telegram-сервер в момент публикации сообщения
# "via @bot" (до того, как create_inlineform() применит реальный текст
# формы через form.edit(...) сразу после подтверждения).
_INLINE_FORM_PLACEHOLDER = visual_module.wait("загрузка формы…")

# ВАЖНО: Telegram кладёт inline_message_id в chosen_inline_result (и позже
# в callback_query) ТОЛЬКО если у отправленного результата с самого начала
# была inline-клавиатура (см. доки ChosenInlineResult — "Available only if
# there is an inline keyboard attached to the message"). Без этого плейсхолдер
# уйдёт в чат, chosen_inline_result придёт, но inline_message_id будет None,
# и create_inlineform() никогда не получит подтверждение (TimeoutError).
_PLACEHOLDER_MARKUP = InlineKeyboardMarkup(
    inline_keyboard=[[InlineKeyboardButton(text="⏳", callback_data="frx_inlineform_pending")]]
)

# Префикс id инлайн-результата, которым бот отвечает на ввод значения через
# InputButton (core/inline_forms.py) — отличает такой chosen_inline_result
# от подтверждения самой формы (там result_id == form_id, см. выше) в
# handle_chosen_inline_result() ниже.
_INPUT_RESULT_PREFIX = "frx_input:"

# Префикс id инлайн-результата вызова bot.inline_function() —
# сама диспетчеризация происходит не по нему, а по тексту сообщения (см.
# core.engine.build_inline_function_text()/_INLINE_FUNCTION_RE и
# Core._on_new_message) — префикс тут только чтобы id были заведомо
# уникальны для Telegram (InlineQueryResultArticle требует уникальный id
# в пределах одного ответа).
_INLINE_FUNCTION_RESULT_PREFIX = "frx_inlinefunc:"


# ------------------------------------------------------------------------ #
# Пример хэндлеров, объявленных через aiobot/decorators.py.
# Добавляйте свои рядом — просто новой функцией с одним из декораторов.
# ------------------------------------------------------------------------ #

@bot.command('start', access='public')
async def cmd_start(message: types.Message):
    """доступно всем, кто пишет боту. /start web от владельца (ссылка t.me/<бот>?start=web) — актуальный адрес сайта."""
    raw_payload = (message.text or '').partition(' ')[2].strip()
    payload = raw_payload.lower()
    core = _core_ref
    if raw_payload and core is not None and payload != 'web':
        try:
            if await core.dispatch_start_link(message, raw_payload):     # @bot.start_link модулей
                return
        except Exception as e:
            core.tlog(f"[bot] /start {raw_payload[:30]}: {e}")
    if payload == 'web' and core is not None and message.from_user and _is_owner(message.from_user.id, core):
        hook = getattr(getattr(core, 'web', None), 'start_hook', None)
        if hook is not None:
            try:
                await hook()
            except Exception as e:
                await message.answer(visual_module.err("Сайт") + "\n" + visual_module.hint(str(e)[:300]))
            return
    await message.answer(
        visual_module.ok("Стационарный бот FRX запущен") + "\n"
        + visual_module.hint("работает параллельно с юзерботом")
    )


@bot.command('status', access='self')
async def cmd_status(message: types.Message):
    """только владельцу юзербота (показывает внутреннее состояние)."""
    if _core_ref is not None:
        modules = _core_ref.get_module_list()
        await message.answer(visual_module.title("Статус") + "\n" + visual_module.field("модулей", len(modules)))
    else:
        await message.answer(visual_module.warn("стационарный бот работает автономно — без ядра юзербота"))


# ------------------------------------------------------------------------ #
# Регистрация декорированных хэндлеров + access-контроль
# ------------------------------------------------------------------------ #

# Ссылка на core устанавливается в create_bot() — нужна декорированным
# хэндлерам этого модуля (например, cmd_status выше), у которых, в отличие
# от "сырых" aiogram-хэндлеров, нет параметра core в сигнатуре.
_core_ref = None


def _is_owner(user_id, core):
    """
    access='self' — сверяем Telegram ID отправителя с core._owner_id
    (аккаунт, на котором залогинен Telethon-клиент юзербота, см.
    core/engine.py Core.start()). Без core (автономный режим бота)
    сверять не с чем — считаем, что владельца нет.
    """
    if core is None or user_id is None:
        return False
    return user_id == core._owner_id


def _user_id_from_message(message: types.Message):
    return message.from_user.id if message.from_user else None


def _user_id_from_callback(callback: types.CallbackQuery):
    return callback.from_user.id if callback.from_user else None


def _chat_id_from_message(message: types.Message):
    return message.chat.id if message.chat else None


def _chat_id_from_callback(callback: types.CallbackQuery):
    # callback.message отсутствует у кликов по инлайн-формам юзербота
    # (inline_message_id вместо message) — но сюда такие клики и не
    # попадают, см. F.inline_message_id.is_(None) в register_decorated_handlers().
    return callback.message.chat.id if callback.message else None


def _as_id_list(value):
    if value is None:
        return None
    return value if isinstance(value, list) else [value]


def _extended_access_ok(access, for_user, for_chat, black_list, black_chats,
                         user_id, chat_id, core):
    """
    access + for_user/for_chat/black_list/black_chats — см. докстринг
    bot.command()/bot.inline_function() в core/bot_decorators.py, то же
    самое правило комбинирования. Дублирует Core._bot_extended_access_ok()
    (а не зовёт её напрямую), потому что "сырым" хэндлерам, объявленным
    прямо в этом файле, core для этих четырёх параметров не нужен вообще —
    он нужен только для access='self' (см. _is_owner выше); в автономном
    режиме (core=None) access='self' и так отключён, а for_user/for_chat/
    black_list/black_chats продолжают работать.
    """
    if access == 'self' and not _is_owner(user_id, core):
        return False

    black_users = _as_id_list(black_list) or []
    black_chats_ = _as_id_list(black_chats) or []
    if user_id is not None and user_id in black_users:
        return False
    if chat_id is not None and chat_id in black_chats_:
        return False

    allowed_users = _as_id_list(for_user)
    allowed_chats = _as_id_list(for_chat)
    if allowed_users is not None and allowed_chats is not None:
        return user_id in allowed_users and chat_id in allowed_chats
    if allowed_users is not None:
        return user_id in allowed_users
    if allowed_chats is not None:
        return chat_id in allowed_chats
    return True


def _guard(func, access, core, get_user_id, get_chat_id=None,
           for_user=None, for_chat=None, black_list=None, black_chats=None):
    """
    Оборачивает декорированный хэндлер проверкой access, а также — если
    заданы — for_user/for_chat/black_list/black_chats (см.
    core/bot_decorators.py:command(), тот же набор параметров и то же
    правило комбинирования, теперь работает и у "сырых" @bot.command(...)/
    @bot.watcher(...)/@bot.callback(...), объявленных прямо в этом файле,
    не только у модульных). При отказе — молча игнорируем (message:
    ничего не отвечаем; callback: закрываем "часики" пустым answer()),
    чтобы не подтверждать постороннему пользователю сам факт
    существования команды.
    """
    if (access == 'public' and for_user is None and for_chat is None
            and black_list is None and black_chats is None):
        return func

    async def wrapped(event, *a, **kw):
        user_id = get_user_id(event)
        chat_id = get_chat_id(event) if get_chat_id else None
        if not _extended_access_ok(access, for_user, for_chat, black_list,
                                    black_chats, user_id, chat_id, core):
            if isinstance(event, types.CallbackQuery):
                await event.answer()
            return
        return await func(event, *a, **kw)

    return wrapped


def register_decorated_handlers(dp: Dispatcher, core=None):
    """
    Сканирует функции этого модуля (aiobot/bot.py), помеченные
    декораторами aiobot/decorators.py (= core/bot_decorators.py, см. его
    докстринг), и подключает их к переданному Dispatcher — по аналогии с
    тем, как core/engine.py сканирует namespace модуля юзербота на
    декораторы core/decorators.py.

    Возвращает (on_start_handlers, on_error_handlers, cycle_handlers,
    reserved_command_aliases) — первые три отдельно подключает create_bot()
    через dp.startup()/dp.errors() (aiogram не даёт под них универсального
    .register(), как у message/callback_query); reserved_command_aliases —
    алиасы команд, уже занятые ЗДЕСЬ (в aiobot/bot.py), передаётся в
    core.set_bot(..., reserved_aliases=...), чтобы модульные bot.command()
    с тем же именем не регистрировались молча "поверх" этих.

    on_load()/telegram_event() из core/bot_decorators.py тут НЕ
    поддерживаются (в aiobot/bot.py нет понятия "загрузка модуля" — все
    хэндлеры тут и так грузятся один раз при старте) — если такой
    декоратор всё же повесить на функцию в этом файле, она будет замечена
    и пропущена с предупреждением в лог; используйте `import bot` внутри
    модуля юзербота (core/bot_decorators.py), там оба работают полноценно.
    """
    module = sys.modules[__name__]

    on_start_handlers = []
    on_error_handlers = []
    cycle_handlers = []
    reserved_command_aliases = set()

    for value in vars(module).values():
        if not callable(value) or not hasattr(value, '_frx_bot_type'):
            continue
        ftype = value._frx_bot_type

        if ftype == 'command':
            reserved_command_aliases.update(value._frx_bot_commands)
            handler = _guard(
                value, value._frx_bot_access, core, _user_id_from_message, _chat_id_from_message,
                for_user=getattr(value, '_frx_bot_for_user', None),
                for_chat=getattr(value, '_frx_bot_for_chat', None),
                black_list=getattr(value, '_frx_bot_black_list', None),
                black_chats=getattr(value, '_frx_bot_black_chats', None),
            )
            dp.message.register(handler, Command(*value._frx_bot_commands))
        elif ftype == 'watcher':
            handler = _guard(
                value, value._frx_bot_access, core, _user_id_from_message, _chat_id_from_message,
                for_user=getattr(value, '_frx_bot_for_user', None),
                for_chat=getattr(value, '_frx_bot_for_chat', None),
                black_list=getattr(value, '_frx_bot_black_list', None),
                black_chats=getattr(value, '_frx_bot_black_chats', None),
            )
            dp.message.register(handler)
        elif ftype == 'callback':
            handler = _guard(
                value, value._frx_bot_access, core, _user_id_from_callback, _chat_id_from_callback,
                for_user=getattr(value, '_frx_bot_for_user', None),
                for_chat=getattr(value, '_frx_bot_for_chat', None),
                black_list=getattr(value, '_frx_bot_black_list', None),
                black_chats=getattr(value, '_frx_bot_black_chats', None),
            )
            # Только клики НЕ по инлайн-формам юзербота (те без message_id,
            # см. handle_callback_query ниже и F.inline_message_id.is_not(None)).
            prefix = getattr(value, '_frx_bot_click_prefix', '') or ''
            if prefix:
                dp.callback_query.register(handler, F.inline_message_id.is_(None), F.data.startswith(prefix))
            else:
                dp.callback_query.register(handler, F.inline_message_id.is_(None))
        elif ftype == 'on_start':
            on_start_handlers.append(value)
        elif ftype == 'on_error':
            on_error_handlers.append(value)
        elif ftype == 'cycle':
            cycle_handlers.append(value)
        elif ftype in ('on_load', 'telegram_event'):
            print(
                f"[FRX] @bot.{ftype}() на '{value.__name__}' в aiobot/bot.py "
                f"не поддерживается для хэндлеров, объявленных прямо в этом "
                f"файле — используйте `import bot` внутри модуля юзербота"
            )

    return on_start_handlers, on_error_handlers, cycle_handlers, reserved_command_aliases


def _run_cycle(func, interval):
    """asyncio.Task для одного @bot.cycle(interval=...) хэндлера."""
    seconds = parse_interval(interval) if isinstance(interval, str) else float(interval)

    async def loop():
        while True:
            try:
                await func()
            except Exception as e:
                print(f"[FRX] Ошибка в bot.cycle({func.__name__}): {e}")
            await asyncio.sleep(seconds)

    return asyncio.create_task(loop())


def create_bot(token: str, core=None):
    """
    Создаёт Bot и Dispatcher AIOgram 3.
    core (опционально) — экземпляр core.engine.Core, если стационарному
    боту нужен доступ к БД юзербота, списку модулей и т.д. Также именно
    через core реализованы инлайн-формы (см. core/inline_forms.py) и
    access='self' у декораторов из aiobot/decorators.py (сверка с
    core._owner_id) — без core все access='self' хэндлеры отключены.

    Если core передан — в конце вызывает core.set_bot(aiogram_bot, dp,
    reserved_aliases=...) сама (раньше это делал main.py отдельным
    вызовом) — это и подключает engine.bot.*/create_inlineform(), и
    "включает" модульные bot.*-хэндлеры юзербота (core/bot_decorators.py,
    `import bot`), и является тем самым моментом "после всех
    инициализаций" для bot.on_start()/bot.cycle().
    """
    global _core_ref
    _core_ref = core

    aiogram_bot = FRXBot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    on_start_handlers, on_error_handlers, cycle_handlers, reserved_aliases = register_decorated_handlers(dp, core=core)

    # BotChat. Внешний middleware видит КАЖДЫЙ апдейт до хэндлеров и ничего в них не меняет;
    # а polling просим дополнительно присылать правки, реакции, опросы и посты каналов (иначе Telegram их не шлёт).
    if core is not None:
        @dp.update.outer_middleware()
        async def _botchat_updates(handler, update, data):
            try:
                await core.botchat.on_update(update)
            except Exception as e:
                print(f"[FRX] [WARN] BotChat: апдейт не записан: {type(e).__name__}: {e}")
            return await handler(update, data)

        _orig_polling = dp.start_polling

        async def _polling_with_botchat(*bots, **kwargs):
            from core.botchat import EXTRA_UPDATES
            from aiogram.exceptions import TelegramNetworkError, TelegramServerError
            if kwargs.get('allowed_updates') is None:
                kwargs['allowed_updates'] = sorted(set(dp.resolve_used_update_types()) | EXTRA_UPDATES)
            # Первый же запрос polling'а (getMe) при мигающем интернете падал TelegramNetworkError — и ронял ВЕСЬ юзербот
            # (keeper перезапускал его, а Startup потом пересоздавал бота). Теперь сеть пропала — ждём и пробуем снова.
            delay = 2
            while True:
                try:
                    return await _orig_polling(*bots, **kwargs)
                except (TelegramNetworkError, TelegramServerError, OSError, asyncio.TimeoutError) as e:
                    print(f"[FRX] [WARN] бот: нет связи с Telegram ({type(e).__name__}) — повтор через {delay} с")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60)

        dp.start_polling = _polling_with_botchat

    if on_start_handlers or cycle_handlers:
        @dp.startup()
        async def _on_bot_startup():
            for handler in on_start_handlers:
                await handler()
            for handler in cycle_handlers:
                _run_cycle(handler, handler._frx_bot_cycle)

    if on_error_handlers:
        @dp.errors()
        async def _on_bot_error(event):
            error_text = f"{type(event.exception).__name__}: {event.exception}"
            context = {'update': event.update}
            for handler in on_error_handlers:
                await handler(error_text, context)
            return True

    # ------------------------------------------------------------------ #
    # Инлайн-формы — см. core/inline_forms.py и Core.create_inlineform()
    # ------------------------------------------------------------------ #

    @dp.inline_query()
    async def handle_inline_query(iq: types.InlineQuery):
        """
        Отвечает на два рода инлайн-запросов, которые сам юзербот так или
        иначе инициировал — и ничего больше (этот бот не предназначен для
        обычного инлайн-поиска живыми людьми):

        1) query == ожидающий подтверждения form_id из create_inlineform()
           (core.get_pending_form) — как и раньше, отвечаем СРАЗУ реальным
           text/buttons формы (а не плейсхолдером с последующим edit()) —
           экономит один лишний HTTP round-trip к Bot API на каждое
           создание формы. Если buttons не заданы — всё равно вешаем
           placeholder-кнопку: без inline-клавиатуры Telegram не отдаст
           inline_message_id в chosen_inline_result (см. коммент у
           _PLACEHOLDER_MARKUP) — create_inlineform() сам снимет её сразу
           после подтверждения, если модуль кнопок не просил.

        2) у отправителя запроса есть активное "ожидание ввода" через
           InputButton (core.get_pending_input, см. core/inline_forms.py) —
           отвечаем ОДНИМ результатом-эхо набранного текста (живой
           предпросмотр того, что отправится) — id помечен
           _INPUT_RESULT_PREFIX, чтобы handle_chosen_inline_result() ниже
           отличил этот случай от подтверждения формы.

        На любой другой инлайн-ввод отвечаем пустым списком результатов.
        """
        if core is None:
            await iq.answer([], cache_time=1, is_personal=True)
            return

        pending_form = core.get_pending_form(iq.query)
        if pending_form is not None:
            text = pending_form['text']
            buttons = pending_form['buttons']
            markup = core.build_inline_markup(buttons, pending_form.get('styles')) if buttons else _PLACEHOLDER_MARKUP

            media = pending_form.get('media')
            if media:                                   # форма с картинкой/анимацией — текст становится подписью
                from core.engine import _caption
                cls = InlineQueryResultCachedPhoto if media['kind'] == 'photo' else InlineQueryResultCachedMpeg4Gif
                fid = {'photo_file_id': media['file_id']} if media['kind'] == 'photo' else {'mpeg4_file_id': media['file_id']}
                result = cls(id=iq.query, caption=_caption(text) or None, parse_mode='HTML', reply_markup=markup, **fid)
                await iq.answer([result], cache_time=0, is_personal=True)
                return
            result = InlineQueryResultArticle(
                id=iq.query,  # echo form_id обратно — по нему SendInlineBotResult выберет именно этот результат
                title="FRX · форма",
                description=(text or "")[:100],
                input_message_content=InputTextMessageContent(message_text=text or _INLINE_FORM_PLACEHOLDER),
                reply_markup=markup,
            )
            await iq.answer([result], cache_time=0, is_personal=True)
            return

        user_id = iq.from_user.id if iq.from_user else None
        pending_input = core.get_pending_input(user_id) if user_id is not None else None

        def input_result():
            """Эхо набранного текста — «отправить значение в форму» (InputButton ждёт ввода)."""
            value = iq.query or ""
            hint = str(pending_input.get('hint') or '').strip()
            if value:
                title, descr = visual_module.btn("Отправить в форму: ") + value[:60], hint or "значение уйдёт в форму"
            else:
                title = "✏️ " + (hint or "Введите значение")
                descr = f"«{pending_input.get('title') or ''}» — наберите после @бота и нажмите сюда".strip()
            return InlineQueryResultArticle(
                id=f"{_INPUT_RESULT_PREFIX}{uuid.uuid4().hex}",
                title=title,
                description=descr,
                input_message_content=InputTextMessageContent(message_text=value or "…"),
            )

        # @bot.inline_query модулей — настоящие инлайн-результаты (core/inline_results.py).
        # Работают и пока форма ждёт ввода: «@bot wh …» — это Whisper, а не значение для формы;
        # вариант «отправить в форму» тогда идёт последним в списке.
        try:
            module_results = await core.dispatch_inline_query(iq)
        except Exception as e:
            core.tlog(f"[bot] инлайн-запрос «{(iq.query or '')[:40]}»: {e}")
            module_results = []
        if module_results is not None and (module_results or pending_input is None):
            if pending_input is not None:
                module_results = list(module_results)[:49] + [input_result()]
            await iq.answer(module_results, cache_time=0, is_personal=True)
            return

        # bot.inline_function() (core/bot_decorators.py) —
        # "@bot <название функции> <аргументы>". См. Core.match_inline_functions()
        # для полного разбора логики "выбора"/точного совпадения.
        matches = core.match_inline_functions(iq.query)
        if matches:
            results = [
                InlineQueryResultArticle(
                    id=f"{_INLINE_FUNCTION_RESULT_PREFIX}{uuid.uuid4().hex}",
                    title=visual_module.btn(m['name']) + (f" {m['args']}" if m['exact'] and m['args'] else ""),
                    description=m['doc'] or ("Выполнить" if m['exact'] else "Инлайн-функция — продолжите набор"),
                    input_message_content=InputTextMessageContent(
                        message_text=build_inline_function_text(m['name'], m['args']),
                    ),
                )
                for m in matches
            ]
            if pending_input is not None:
                results = results[:49] + [input_result()]
            await iq.answer(results, cache_time=0, is_personal=True)
            return

        if pending_input is not None:
            # cache_time=0 — query меняется на каждое нажатие клавиши, кэш не нужен
            await iq.answer([input_result()], cache_time=0, is_personal=True)
            return

        await iq.answer([], cache_time=1, is_personal=True)

    @dp.chosen_inline_result()
    async def handle_chosen_inline_result(chosen: types.ChosenInlineResult):
        """
        И подтверждение create_inlineform(), и ввод значения через
        InputButton (result_id с _INPUT_RESULT_PREFIX) приходят только
        если у бота включён Inline Feedback 100% (/setinlinefeedback в
        BotFather) — без этого chosen_inline_result вообще не долетает
        до бота, независимо от reply_markup/inline_message_id у ответа.

        Для InputButton chosen.inline_message_id при этом не нужен (ветка
        ниже его не использует) — dispatch_inline_input() опирается
        только на chosen.query и chosen.from_user.
        """
        if core is None:
            return
        if chosen.result_id.startswith(_INPUT_RESULT_PREFIX):
            await core.dispatch_inline_input(chosen)
            return
        if await core.dispatch_inline_chosen(chosen.result_id, chosen.inline_message_id):   # @bot.inline_query модулей
            return
        if chosen.inline_message_id is None:
            return
        core.resolve_pending_form(chosen.result_id, chosen.inline_message_id)

    @dp.callback_query(F.inline_message_id.is_not(None))
    async def handle_callback_query(callback: types.CallbackQuery):
        """
        Клик по кнопке инлайн-формы юзербота (inline_message_id задан —
        сообщение отправлено через инлайн-режим, см. handle_inline_query
        выше). Обычные callback'и по кнопкам под сообщениями бота в чате
        (message_id вместо inline_message_id) сюда не попадают — их
        забирают @bot.callback(...)-хэндлеры, зарегистрированные выше
        через register_decorated_handlers() с фильтром
        F.inline_message_id.is_(None).
        """
        if core is not None:
            await core.dispatch_inline_click(callback)
        else:
            await callback.answer()

    # ------------------------------------------------------------------ #
    # Модульные bot.*-хэндлеры юзербота (`import bot`, core/bot_decorators.py)
    # ------------------------------------------------------------------ #
    # Единая точка входа на каждое событие — по аналогии с тем, как
    # core.engine.Core._on_new_message/_on_chat_action сами разбирают
    # команды/вотчеры юзербота внутри одного callback'а, а не регистрируют
    # хэндлер на каждый алиас отдельно. Благодаря этому bot.command()/
    # bot.watcher()/bot.telegram_event() из модулей подключаются "на лету"
    # (в т.ч. модулями, загруженными через .loadmod ПОСЛЕ старта бота) без
    # каких-либо новых dp.register(...) — Core сам смотрит в свои
    # bot_command_map/bot_watchers_bot/bot_telegram_events в момент апдейта.
    #
    # Регистрируются ПОСЛЕДНИМИ среди message/my_chat_member/chat_member —
    # если выше уже есть "сырой" @bot.command(...)/@bot.watcher() прямо в
    # этом файле с подходящим фильтром, aiogram остановится на нём первом
    # (стандартное поведение Dispatcher — один хэндлер на апдейт), и сюда
    # сообщение не дойдёт.

    @dp.callback_query(F.inline_message_id.is_(None))
    async def handle_module_bot_callbacks(callback: types.CallbackQuery):
        """Кнопки под обычными сообщениями бота — @bot.callback(...) модулей юзербота (`import bot`).
        «Сырые» @bot.callback из этого файла зарегистрированы раньше и забирают свои клики первыми."""
        if core is not None and await core.dispatch_bot_callback(callback):
            return
        try:
            await callback.answer()
        except Exception:
            pass

    @dp.message()
    async def handle_module_bot_messages(message: types.Message):
        if core is not None:
            await core.dispatch_bot_message(message)

    @dp.my_chat_member()
    async def handle_bot_membership_change(update: types.ChatMemberUpdated):
        """bot.telegram_event(): статус САМОГО стационарного бота в чате изменился."""
        if core is not None:
            await core.dispatch_bot_telegram_event(update, is_bot_event=True)

    @dp.chat_member()
    async def handle_chat_member_change(update: types.ChatMemberUpdated):
        """bot.telegram_event(): статус ДРУГОГО участника изменился в чате, где есть бот."""
        if core is not None:
            await core.dispatch_bot_telegram_event(update, is_bot_event=False)

    if core is not None:
        core.set_bot(aiogram_bot, dp, reserved_aliases=reserved_aliases)

    return aiogram_bot, dp


async def run_aiogram_bot(token: str, core=None):
    """Запускает polling стационарного бота. Вызывается как отдельная asyncio.Task в main.py."""
    if not token:
        print("[FRX] AIOGRAM_TOKEN не задан — стационарный бот не запущен")
        return
    aiogram_bot, dp = create_bot(token, core=core)
    print("[FRX] Стационарный AIOgram-бот запущен")
    await dp.start_polling(aiogram_bot)
