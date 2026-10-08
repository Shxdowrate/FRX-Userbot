"""
aiobot/decorators.py

Совместимость: этот модуль теперь просто переэкспортирует
core/bot_decorators.py — канонический набор декораторов стационарного
бота (используется и здесь, внутри aiobot/bot.py для "сырых" хэндлеров,
и внутри модулей юзербота через `import bot`, см. core/bot_decorators.py
и core/engine.py Core._register_module_imports()). Раньше вся реализация
жила прямо тут; теперь она общая для обоих способов подключения.

Использование внутри aiobot/bot.py — БЕЗ ИЗМЕНЕНИЙ, как и раньше:

    from . import decorators as bot

    @bot.command('start', access='public')
    async def cmd_start(message):
        await message.answer("Привет!")

    @bot.command('status', access='self')
    async def cmd_status(message):
        ...  # доступно только владельцу юзербота

ВАЖНО: хэндлеры, объявленные ПРЯМО В aiobot/bot.py (как выше), по-прежнему
получают "сырой" aiogram.types.Message / CallbackQuery — их подключает
register_decorated_handlers(dp, core) в aiobot/bot.py, и она ничего не
знает про Telethon-обёртки. Telethon-совместимая обёртка (core/bot_events.py,
BotMessageEvent/BotChatMemberEvent) применяется ТОЛЬКО к хэндлерам,
объявленным внутри модулей юзербота через `import bot` (core/engine.py,
Core.dispatch_bot_message()/dispatch_bot_telegram_event()) — см. докстринг
core/bot_decorators.py.

access — кто может вызвать хэндлер (command()/watcher()/callback()):
    'public' (по умолчанию) — кто угодно, кто пишет стационарному боту.
    'self'   — только владелец юзербота: пользователь, чей Telegram ID
               совпадает с core._owner_id (тем же аккаунтом, под которым
               залогинен Telethon-клиент юзербота, core/engine.py,
               Core.start()). Сверяется по message.from_user.id /
               callback.from_user.id в момент вызова — см.
               aiobot/bot.py:_is_owner(). Если core не передан в
               create_bot() (bot работает автономно, без ядра юзербота),
               сверять ID не с чем — access='self' хэндлеры в этом
               режиме отключены (тихо не вызываются), см.
               register_decorated_handlers().

watcher() теперь также принимает watch='bot'|'userbot'|'all' (см.
core/bot_decorators.py) — внутри aiobot/bot.py это поле не используется
(register_decorated_handlers() тут всегда трактует "сырой" @bot.watcher()
как наблюдающего только за стационарным ботом, watch игнорируется).

on_load() и telegram_event() теперь ЕСТЬ в наборе (раньше их не было) —
но, как и watch=, эффект от них есть ТОЛЬКО у модулей юзербота
(`import bot`, core/bot_decorators.py) — внутри aiobot/bot.py
register_decorated_handlers() их не подключает (нет тут концепции
"загрузки модуля"), см. предупреждение в логе, если такое всё же
объявить.

command()/watcher()/callback() также принимают for_user=/for_chat=/
black_list=/black_chats= (см. докстринг command() в
core/bot_decorators.py про смысл каждого параметра) — работают и у
"сырых" хэндлеров прямо в aiobot/bot.py (aiobot/bot.py:_guard()/
_extended_access_ok()), не только у модульных.
"""

from core.bot_decorators import (  # noqa: F401
    command,
    watcher,
    callback,
    cycle,
    on_start,
    on_load,
    telegram_event,
    on_error,
)
