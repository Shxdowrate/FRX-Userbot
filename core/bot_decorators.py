"""
core/bot_decorators.py

Декораторы, доступные внутри модулей FRX для объявления хэндлеров
СТАЦИОНАРНОГО бота (AIOgram 3) — параллельный набор к core/decorators.py
(тот — для юзербота на Telethon). Как и там, каждый декоратор ничего не
регистрирует сам, а только помечает функцию метаданными (атрибутами вида
`_frx_bot_*`); реальное подключение к движку происходит:
  - для command/watcher/telegram_event/on_error — динамически, на каждое
    входящее событие, через core.engine.Core.dispatch_bot_message() /
    dispatch_bot_telegram_event() (см. aiobot/bot.py — три статических
    хэндлера, которые просто передают апдейт в эти методы);
  - для on_start/on_load/cycle — при загрузке модуля / подключении
    стационарного бота, см. core/engine.py Core._load_from_source() и
    Core.set_bot().

Движок кладёт этот модуль в sys.modules под именем 'bot' при старте (см.
Core._register_module_imports() в core/engine.py) — поэтому внутри модуля
достаточно `import bot`, ничего больше руками подключать не нужно.
Модуль может (и обычно должен, если хочет влиять сразу на оба бота)
одновременно делать `import decorators` (юзербот) и `import bot`
(стационарный бот) — это два независимых набора хэндлеров в одном файле:

    import decorators
    import bot

    @decorators.command('ping')
    async def ping_userbot(event):
        await engine.answer(event, 'Pong (юзербот)!')

    @bot.command('ping', access='public')
    async def ping_bot(event):
        await event.reply('Pong (стационарный бот)!')

ВАЖНО: эти декораторы влияют ТОЛЬКО на стационарного бота. Они ничего не
меняют в том, как работают core/decorators.py и обычные @decorators.*
хэндлеры юзербота — это полностью отдельная, параллельная система
регистрации (свои списки в Core: bot_command_map, bot_watchers_bot/
bot_watchers_userbot, bot_telegram_events, bot_on_error_handlers, и
свои поля ModuleInfo.bot_*). Единственное место пересечения —
watcher(watch='userbot'/'all'), который ЧИТАЕТ те же входящие события
Telethon, что видит юзербот, но не меняет их обработку декораторами
core/decorators.py.

Событие, приходящее в функцию, ВСЕГДА в стиле Telethon (у юзербота
приоритет — см. README, раздел про гибридные модули):
  - Сообщения от стационарного бота (aiogram) оборачиваются в
    core.bot_events.BotMessageEvent — Telethon-совместимый фасад
    (.raw_text/.text/.sender_id/.chat_id/.out/.is_private/.is_group/
    await .reply()/.respond()/.edit()/.delete()/.download_media(),
    остальные атрибуты — через __getattr__ проксируются в исходный
    aiogram.types.Message, см. core/bot_events.py).
  - Сообщения от юзербота (Telethon) передаются КАК ЕСТЬ — это уже
    настоящий telethon.events.NewMessage.Event, оборачивать не нужно.
  - В обоих случаях у события есть `.origin` — 'bot' или 'userbot',
    чтобы функция могла понять, кто именно поймал сообщение (полезно
    при watch='all').

Доступны: command, inline_function, watcher, on_start, on_load, cycle,
telegram_event, on_error. (callback пока не поддержан на уровне модулей —
см. watcher() для реакции на входящие сообщения; для кликов по кнопкам ПОД
сообщениями самого бота по-прежнему используйте @bot.callback() внутри
aiobot/bot.py напрямую, см. README «Стационарный бот в модулях»).

command() и inline_function() (см. их докстринги ниже) принимают, помимо
access=, ещё четыре опциональных параметра — for_user=, for_chat=,
black_list=, black_chats= — дополнительный фильтр "кто и где может вызвать
хэндлер", ортогональный access= (access — self/public, эти четыре — про
конкретных юзеров/чаты). Каждый принимает один id (int) или список id.
Правило одно и то же для всех четырёх мест, где эти параметры встречаются
(command, inline_function, а также form.for_user/for_chat инлайн-форм,
где применимо):
    - ничего не указано      -> всем юзерам, во всех чатах
    - указан только for_user -> эти юзеры, в ЛЮБОМ чате
    - указан только for_chat -> любой юзер, но только в этих чатах
    - указаны оба            -> только эти юзеры И только в этих чатах
    - black_list/black_chats -> запрещают доступ ВСЕГДА, даже если юзер/чат
                                 проходят по for_user/for_chat
"""

_VALID_ACCESS = ('self', 'public')
_VALID_WATCH = ('bot', 'userbot', 'all')


def _check_access(access):
    if access not in _VALID_ACCESS:
        raise ValueError(f"access должен быть 'self' или 'public', получено: {access!r}")
    return access


def _check_watch(watch):
    if watch not in _VALID_WATCH:
        raise ValueError(f"watch должен быть 'bot', 'userbot' или 'all', получено: {watch!r}")
    return watch


def command(*names, access='public', for_user=None, for_chat=None,
            black_list=None, black_chats=None):
    """
    @bot.command('command', 'com', access='self')
    Обработчик команды СТАЦИОНАРНОГО бота. Префикс команд у бота
    ВСЕГДА стандартный Telegram-префикс '/' (в отличие от юзербота,
    у которого префикс настраиваемый, см. engine.get_prefix()) —
    т.е. пользователь вызывает её как /command или /com.

    access — кто может вызвать хэндлер:
        'public' (по умолчанию) — кто угодно, кто пишет боту.
        'self'   — только владелец юзербота (тот же Telegram-аккаунт,
                   под которым залогинен Telethon-клиент, сверяется
                   по core._owner_id в момент вызова). Отказ — тихий
                   (ответа не будет), чтобы не подтверждать посторонним
                   сам факт существования команды.

    for_user, for_chat, black_list, black_chats — дополнительный фильтр
    ПОВЕРХ access (см. докстринг файла выше — правило одно и то же везде,
    включая bot.inline_function()): по умолчанию все четыре не заданы —
    команда доступна всем/всюду, как обычно (access='self' по-прежнему
    работает как раньше). Указывая for_user/for_chat, можно сузить
    'public'-команду до конкретных людей и/или чатов, не переключаясь на
    access='self'.

    Функция вызывается с одним аргументом event — core.bot_events.BotMessageEvent
    (Telethon-совместимая обёртка над aiogram.types.Message, см. докстринг
    этого файла выше).

    Алиас, уже занятый другим модулем ЛИБО "сырым" @bot.command(...) прямо
    внутри aiobot/bot.py, при загрузке модуля вызовет ValueError (как и у
    декоратора command() юзербота, core/decorators.py) — либо, если
    стационарный бот на момент загрузки модуля ещё не подключён (типичный
    случай — модуль грузится на старте, до создания aiogram-бота), будет
    молча "перекрыт" встроенной командой позже и залогирован через
    engine.tlog() в момент запуска бота (core.engine.Core.set_bot()).
    """
    access = _check_access(access)

    def deco(func):
        existing = getattr(func, '_frx_bot_commands', [])
        func._frx_bot_commands = list(existing) + [n.lower() for n in names]
        func._frx_bot_type = 'command'
        func._frx_bot_access = access
        func._frx_bot_for_user = for_user
        func._frx_bot_for_chat = for_chat
        func._frx_bot_black_list = black_list
        func._frx_bot_black_chats = black_chats
        return func

    return deco


def inline_function(*names, access='public', for_user=None, for_chat=None,
                     black_list=None, black_chats=None):
    """
    @bot.inline_function('weather', access='public')
    Инлайн-функция СТАЦИОНАРНОГО бота — в отличие от command() (обычная
    команда '/...', отправленная боту напрямую), вызывается через родной
    инлайн-режим Telegram, в ЛЮБОМ чате (не обязательно там, где есть
    сам бот):

        @<имя_бота> <название функции> <аргументы>

    Пока пользователь набирает название функции, Telegram сам показывает
    "выбор" — список подходящих по префиксу зарегистрированных инлайн-
    функций (см. aiobot/bot.py:handle_inline_query ->
    core.match_inline_functions()); как только имя набрано полностью и
    однозначно совпадает с одной функцией, дальнейший текст — уже
    аргументы. Пользователь выбирает результат (тап = "отправить") —
    Telegram публикует сообщение "via @бот" в текущем чате; юзербот видит
    его через MTProto (у входящего сообщения есть via_bot_id) и по этому
    сообщению вызывает функцию (core/engine.py, Core._on_new_message ->
    Core._dispatch_inline_function_message()).

    ВАЖНО: сработает только в чатах, где состоит САМ юзербот-аккаунт —
    именно этот канал (Telethon/MTProto) даёт движку реальные chat_id и
    id отправителя этого сообщения; Bot API про инлайн-апдейты их вообще
    не сообщает (только query и from_user, без chat_id).

    access — как у command(): 'public' (по умолчанию) или 'self' (только
    владелец юзербота).

    for_user, for_chat, black_list, black_chats — дополнительный фильтр
    ПОВЕРХ access (см. докстринг файла выше про общее правило).

    Функция вызывается с двумя аргументами: event (настоящее
    telethon-событие NewMessage — как у decorators.command() юзербота, а
    НЕ BotMessageEvent, см. докстринг этого файла выше про .origin) и
    args (str — то, что пользователь набрал после названия функции;
    пустая строка, если ничего не набрал).

    Алиас, уже занятый другой инлайн-функцией любого модуля, при загрузке
    вызовет ValueError (как и у command()).
    """
    access = _check_access(access)

    def deco(func):
        existing = getattr(func, '_frx_bot_inline_names', [])
        func._frx_bot_inline_names = list(existing) + [n.lower() for n in names]
        func._frx_bot_type = 'inline_function'
        func._frx_bot_access = access
        func._frx_bot_for_user = for_user
        func._frx_bot_for_chat = for_chat
        func._frx_bot_black_list = black_list
        func._frx_bot_black_chats = black_chats
        return func

    return deco


def watcher(watch='bot', access='public'):
    """
    @bot.watcher(watch='bot')
    Следит за вообще всеми входящими сообщениями (включая команды) —
    аналог decorators.watcher() юзербота, но с выбором НАБЛЮДАТЕЛЯ:

        watch='bot'     — видит только сообщения, пришедшие стационарному
                           боту (aiogram). Поведение как у watcher() из
                           aiobot/decorators.py.
        watch='userbot' — видит только сообщения юзербота (то же самое,
                           что видит decorators.watcher(), но
                           зарегистрировано отдельно этим модулем через
                           `import bot`, а не `import decorators`).
        watch='all'     — оба сразу: сработает, даже если стационарного
                           бота нет в чате, а юзербот-аккаунт есть (и
                           наоборот). Если ОБА наблюдателя поймали ОДНО И
                           ТО ЖЕ сообщение (стационарный бот и юзербот-
                           аккаунт оба состоят в одном групповом чате,
                           видят один физический апдейт) — функция
                           вызывается только ОДИН раз (дедупликация по
                           паре (chat_id, message_id), см.
                           core.engine.Core._bot_dedup_seen(); совпадающие
                           id канала/сообщения между Bot API и MTProto для
                           групп/супергрупп/каналов Telegram гарантирует
                           сам, так что дополнительная нормализация не
                           нужна).

    access — как в command(): 'public' (по умолчанию) или 'self' —
    сверяется с отправителем (message.from_user.id у 'bot'-источника,
    event.sender_id у 'userbot'-источника) независимо от watch.

    Функция вызывается с одним аргументом event — либо BotMessageEvent
    (источник 'bot'), либо настоящим telethon-событием (источник
    'userbot') — см. докстринг файла выше про .origin.
    """
    access = _check_access(access)
    watch = _check_watch(watch)

    def deco(func):
        func._frx_bot_type = 'watcher'
        func._frx_bot_access = access
        func._frx_bot_watch = watch
        return func

    return deco


def on_start():
    """
    @bot.on_start()
    Запускается ОДИН раз — после того, как отработали ВСЕ инициализации:
    юзербот полностью стартовал (Telethon-клиент подключён, все модули
    загружены, отработали их decorators.on_start()) И стационарный
    aiogram-бот создан и подключён к движку (core.engine.Core.set_bot()).
    Если модуль загружается позже, когда всё это уже произошло (например,
    через .loadmod во время работы) — вызывается сразу при загрузке.
    Если aiogram_token не задан в user/config.json (стационарный бот вообще не
    запускается) — не вызывается никогда.

    Функция вызывается без аргументов.
    """
    def deco(func):
        func._frx_bot_type = 'on_start'
        return func
    return deco


def on_load():
    """
    @bot.on_load()
    Запускается при загрузке ЛЮБОГО модуля (в том числе этого самого) —
    полный аналог decorators.on_load() юзербота, только для регистра
    хэндлеров стационарного бота. Передаёт объект модуля (ModuleInfo) в
    качестве аргумента. Не зависит от того, подключён ли уже стационарный
    бот (в отличие от on_start()/cycle()) — срабатывает всегда, при любой
    загрузке модуля.
    """
    def deco(func):
        func._frx_bot_type = 'on_load'
        return func
    return deco


def cycle(interval='1m'):
    """
    @bot.cycle(interval='1m')
    Выполняется циклично с заданным интервалом, пока стационарный бот
    подключён — аналог decorators.cycle() юзербота. Поддерживаются те же
    суффиксы: s/m/h/d (core/time_utils.py). Запускается (и остаётся
    активным) только пока подключён стационарный бот (aiogram_token
    задан) — как и on_start(), стартует сразу после set_bot(), либо сразу
    при загрузке модуля, если бот уже подключён.

    Функция вызывается без аргументов.
    """
    def deco(func):
        func._frx_bot_type = 'cycle'
        func._frx_bot_cycle = interval
        return func
    return deco


def telegram_event():
    """
    @bot.telegram_event()
    Запускается при служебных событиях СТАЦИОНАРНОГО бота: изменении
    статуса самого бота в чате (добавили/выгнали/дали права — aiogram
    my_chat_member) и изменении статуса ДРУГИХ участников в чатах, где бот
    состоит (aiogram chat_member; приходит не всегда — зависит от прав
    бота в чате). Аналог decorators.telegram_event() юзербота (там —
    Telethon events.ChatAction), но со стороны aiogram — прямого
    сопоставления событий один-в-один нет, поэтому это отдельный поток
    апдейтов, watch= у него нет (только сторона бота, юзербот не
    участвует).

    Функция вызывается с одним аргументом event —
    core.bot_events.BotChatMemberEvent (см. этот файл): event.chat_id,
    event.user_id, event.old_status, event.new_status, event.user_joined,
    event.user_left, event.is_bot_event (True — это апдейт про самого
    бота, False — про другого участника чата), event.raw — исходный
    aiogram.types.ChatMemberUpdated.
    """
    def deco(func):
        func._frx_bot_type = 'telegram_event'
        return func
    return deco


def on_error():
    """
    @bot.on_error()
    Запускается при любой ошибке внутри ЛЮБОГО хэндлера стационарного
    бота, объявленного через bot.command()/bot.watcher()/bot.telegram_event()/
    bot.cycle() (в любом модуле) — аналог decorators.on_error() юзербота,
    но полностью отдельный поток ошибок: ошибки юзербота сюда не попадают,
    и наоборот. Функция вызывается с двумя аргументами (error_text, context),
    где context — словарь {'module': ..., 'function': ...}, как и у
    decorators.on_error().
    """
    def deco(func):
        func._frx_bot_type = 'on_error'
        return func
    return deco


def callback(data_startswith='', access='public'):
    """
    @bot.callback(data_startswith='game:', access='public')
    Нажатие кнопки под ОБЫЧНЫМ сообщением стационарного бота — тем, что модуль отправил сам через
    engine.bot.send_message(chat_id, text, buttons=[[("Ок", "game:ok")]]) (не инлайн-формой и не
    @bot.inline_query). Удобно для личных сообщений: карты игрока, личное меню, подтверждение.

        async def f(click)
            click.data, click.user_id, click.username, click.first_name, click.chat_id, click.message_id
            await click.answer(text=None, show_alert=False, url=None)
            await click.edit(text, buttons=None)      # переписать это сообщение
            await click.delete()

    data_startswith — какие кнопки ловить (по началу callback_data); '' — любые, которые не поймал
    модуль с более точным префиксом. access='self' — нажимать может только владелец (остальным — «не для вас»).
    В aiobot/bot.py тот же декоратор подключается напрямую к aiogram (там click — aiogram CallbackQuery).
    """
    if callable(data_startswith):                      # @bot.callback без скобок
        func, data_startswith = data_startswith, ''
        func._frx_bot_type = 'callback'
        func._frx_bot_access = 'public'
        func._frx_bot_click_prefix = ''
        return func
    if data_startswith in ('public', 'self') and access == 'public':
        data_startswith, access = '', data_startswith   # старый вызов @bot.callback('self')
    access = _check_access(access)

    def deco(func):
        func._frx_bot_type = 'callback'
        func._frx_bot_access = access
        func._frx_bot_click_prefix = str(data_startswith or '')
        return func

    return deco


def inline_query(*names, access='self', for_user=None, for_chat=None, black_list=None, black_chats=None):
    """
    @bot.inline_query('wh', 'whisper', access='self')
    Настоящий инлайн-режим: «@бот wh аргументы» в любом чате — функция САМА возвращает
    результаты, которые человек видит в списке и отправляет (в отличие от inline_function,
    где в чат уходит служебный текст, а работу делает юзербот).

        async def f(query) -> list[engine.InlineResult] | None
            query.text      — всё после имени ('' если ничего)
            query.name      — имя, по которому вызвали
            query.user_id, query.username, query.first_name — кто набирает
            query.query     — запрос целиком

    access: 'self' (по умолчанию — только владелец) | 'public' — кто угодно.
    Кнопки результатов с callback_data ловит @bot.inline_click(data_startswith=...).
    """
    _check_access(access)
    if not names:
        raise ValueError("bot.inline_query(): укажите хотя бы одно имя")

    def deco(func):
        existing = getattr(func, '_frx_bot_iq_names', [])
        func._frx_bot_iq_names = list(existing) + [n.lower() for n in names]
        func._frx_bot_type = 'inline_query'
        func._frx_bot_access = access
        func._frx_bot_for_user = for_user
        func._frx_bot_for_chat = for_chat
        func._frx_bot_black_list = black_list
        func._frx_bot_black_chats = black_chats
        return func
    return deco


def inline_click(data_startswith, access='public'):
    """
    @bot.inline_click(data_startswith='wh:')
    Нажатие кнопки под сообщением, отправленным через @bot.inline_query (не инлайн-формой).
        async def f(click)
            click.data, click.user_id, click.username, click.first_name, click.inline_message_id
            await click.answer(text=None, show_alert=False, url=None)
            await click.edit(text, buttons=None, styles=None, link_preview=None)
    access='self' — нажимать может только владелец (остальным — «не для вас»).
    """
    _check_access(access)

    def deco(func):
        func._frx_bot_type = 'inline_click'
        func._frx_bot_click_prefix = str(data_startswith)
        func._frx_bot_access = access
        return func
    return deco


def start_link(prefix):
    """
    @bot.start_link('wh_')
    Ссылка t.me/<бот>?start=<prefix>… — человек открыл бота по такой ссылке.
        async def f(event, payload)   # event — BotMessageEvent (.reply, .sender_id), payload — всё после prefix
    Удобно, чтобы показать что-то лично (длинный текст, файл) по кнопке из инлайн-сообщения:
    click.answer(url=f"https://t.me/{bot}?start=wh_{id}").
    """
    def deco(func):
        func._frx_bot_type = 'start_link'
        func._frx_bot_start_prefix = str(prefix)
        return func
    return deco
