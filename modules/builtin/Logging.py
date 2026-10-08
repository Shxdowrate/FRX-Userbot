"""
modules/builtin/Logging.py

Встроенный модуль логирования. Идея простая: engine.tlog() и так пишет
всё в терминал (core/engine.py), но терминал никто не читает 24/7 — этот
модуль дублирует важные события в ЛС владельцу юзербота ЧЕРЕЗ
СТАЦИОНАРНОГО БОТА (engine.bot.send_message, core/stationary_bot.py), в
удобном отформатированном виде:

    - ошибки юзербота          (@decorators.on_error())
    - ошибки стационарного бота (@bot.on_error())
    - запуск юзербота           (@decorators.on_start(), последним из всех
                                  модулей — см. порядок вызова on_start в
                                  Core.start(), core/engine.py)
    - переключение аккаунта     (@decorators.on_account_switch())

Если стационарный бот ещё не подключён (aiogram_token не задан/бот ещё не
поднялся) — тихо падаем обратно на engine.tlog(), ничего не теряя, просто
не дублируя в ЛС (там и так нечем отправлять).

Конфиги (engine.create_config(), см. modules/builtin/Config.py):
    enabled              — bool. Общий рубильник модуля. Если False —
                            вообще ничего не шлём в ЛС (только тихо
                            молчим, engine.tlog() в терминал никуда не
                            девается — он не зависит от этого модуля).
    log_chat_id           — int. Куда слать (chat_id). 0 — особое значение
                            "в ЛС владельцу юзербота" (engine._owner_id) —
                            это значение по умолчанию; можно
                            переопределить на ID отдельного лог-чата/канала,
                            если владелец захочет вынести логи из личных
                            сообщений.
    notify_startup        — bool. Слать ли сообщение о запуске юзербота.
    notify_userbot_errors — bool. Слать ли ошибки юзербота (@on_error).
    notify_bot_errors     — bool. Слать ли ошибки стационарного бота
                            (@bot.on_error()).
    notify_account_switch — bool. Слать ли уведомление о переключении
                            аккаунта.
    throttle_seconds      — int. Не слать повторно ту же самую ошибку
                            (тот же модуль+функция) в ЛС чаще, чем раз в
                            это число секунд — иначе цикл/watcher,
                            падающий на каждое сообщение, устроит в ЛС
                            «бесконечный» спам. В терминал
                            (engine.tlog()) при этом падает КАЖДАЯ ошибка —
                            троттлится только дублирование
                            в ЛС.

engine.botlog() и лимитер. Модули могут слать собственные логи в ЛС
через engine.botlog(text, level='info') (см. core/engine.py, README 11). Все сообщения,
которые идут через бота — и botlog(), и ошибки/уведомления этого модуля, — проходят
общий ЛИМИТЕР (core/botlog.py): всплеск из botlog_burst сообщений уходит сразу, дальше
сообщения ждут в очереди (botlog_wait=True) либо сжигаются (botlog_wait=False), а по
окончании флуда приходит одно сводное «пропущено N». Настройки:
    botlog_burst          — int. Сколько сообщений можно отправить подряд без пауз.
    botlog_interval       — float. Через сколько секунд после всплеска "освобождается"
                            ещё одно сообщение (скорость восстановления лимита).
    botlog_queue          — int. Сколько сообщений максимум может ждать в очереди;
                            всё сверх — сжигается.
    botlog_wait           — bool. True — лишние сообщения ждут в очереди; False — сразу
                            сжигаются (только всплеск botlog_burst проходит).
Рубильник enabled и log_chat_id действуют и на botlog().
"""

import html
import time

import engine
import decorators
import bot
import visual
import time_utils

NAME = "Logging"
MODULE_NAME = "logging"
VERSION = "1.2"
AUTHOR = "FRX"
DESCRIPTION = "Дублирует ошибки/важные события юзербота в ЛС владельцу через стационарного бота"

# module_name+function -> monotonic-время последней отправки в ЛС —
# используется throttle_seconds ниже; живёт только в памяти процесса
# (переживать перезапуск смысла нет — это защита от "здесь и сейчас"
# всплеска одинаковых ошибок, а не персистентная история).
_last_sent: dict[str, float] = {}


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='enabled',
        description='Включить дублирование логов в ЛС через стационарного бота',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='log_chat_id',
        description='Куда слать логи (chat_id). 0 = в ЛС владельцу юзербота',
        data_type='int', default=0,
    )
    await engine.create_config(
        key='notify_startup',
        description='Слать уведомление о запуске юзербота',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='notify_userbot_errors',
        description='Слать ошибки юзербота (модулей на Telethon)',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='notify_bot_errors',
        description='Слать ошибки стационарного бота (модулей на AIOgram)',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='notify_account_switch',
        description='Слать уведомление при переключении аккаунта юзербота',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='throttle_seconds',
        description='Не дублировать в ЛС одну и ту же ошибку чаще, чем раз в N секунд (0 = без ограничения)',
        data_type='int', default=30,
    )
    # лимитер engine.botlog() и всех сообщений модуля (core/botlog.py)
    await engine.create_config(
        key='botlog_burst',
        description='Лимитер лога: сколько сообщений можно отправить подряд без пауз (всплеск)',
        data_type='int', default=5,
    )
    await engine.create_config(
        key='botlog_interval',
        description='Лимитер лога: раз в сколько секунд освобождается ещё одно сообщение после всплеска',
        data_type='float', default=2.0,
    )
    await engine.create_config(
        key='botlog_queue',
        description='Лимитер лога: сколько сообщений максимум может ждать в очереди (остальные сжигаются)',
        data_type='int', default=20,
    )
    await engine.create_config(
        key='botlog_wait',
        description='Лимитер лога: True — лишние сообщения ждут в очереди, False — сразу сжигаются',
        data_type='bool', default=True,
    )


# --------------------------------------------------------------------- #
# Общий "выход в ЛС" — используется всеми хэндлерами ниже
# --------------------------------------------------------------------- #

async def _target_chat_id():
    chat_id = await engine.get_config('log_chat_id')
    return chat_id if chat_id else engine._owner_id


async def _send(text: str, throttle_key: str = None):
    """
    Отправляет text в лог-чат через стационарного бота.
    throttle_key — если задан, повторные вызовы с тем же ключом раньше,
    чем через throttle_seconds, тихо игнорируются (в ЛС; в терминал
    ошибка уже попала через engine.tlog() до вызова этой функции — там
    троттлинга нет и не должно быть).
    """
    if not await engine.get_config('enabled'):
        return

    if throttle_key is not None:
        throttle = await engine.get_config('throttle_seconds')
        if throttle:
            now = time.monotonic()
            last = _last_sent.get(throttle_key)
            if last is not None and now - last < throttle:
                return
            _last_sent[throttle_key] = now

    if engine._aiogram_bot is None:
        # Стационарный бот ещё не подключён (aiogram_token не задан в
        # user/config.json, либо процесс ещё не успел его поднять) — слать
        # некому, оставляем запись только в терминале.
        engine.tlog(f"{visual.visual('info')} [Logging] бот не подключён, лог остался только в терминале")
        return

    # доставка — через общий лимитер (engine.botlog_raw, core/botlog.py).
    # Он сам берёт log_chat_id, не даёт устроить флуд и не роняет вызывающего при ошибке
    # отправки (ошибка ЛОГИРОВАНИЯ не должна рекурсивно устраивать цикл — только терминал).
    engine.botlog_raw(text)


def _now_str():
    return time_utils.time_format(time_utils.get_time(), '%d.%m.%Y %H:%M:%S')


def _error_text(title: str, module_name, func_name, error_text) -> str:
    """Ошибка в новом облике: заголовок, модуль/функция/время и сам текст ошибки (экранированный) в <pre>."""
    return "\n".join([
        visual.err(title),
        visual.sep(),
        visual.field('модуль', html.escape(str(module_name))),
        visual.field('функция', html.escape(str(func_name))),
        visual.field('время', _now_str()),
        "",
        f"<pre>{html.escape(str(error_text))}</pre>",
    ])


# --------------------------------------------------------------------- #
# Ошибки юзербота
# --------------------------------------------------------------------- #

@decorators.on_error()
async def log_userbot_error(error_text: str, context: dict):
    if not await engine.get_config('notify_userbot_errors'):
        return
    module_name = context.get('module', '?')
    func_name = context.get('function', '?')
    text = _error_text('Ошибка юзербота', module_name, func_name, error_text)
    await _send(text, throttle_key=f"userbot:{module_name}:{func_name}")


# --------------------------------------------------------------------- #
# Ошибки стационарного бота
# --------------------------------------------------------------------- #

@bot.on_error()
async def log_bot_error(error_text: str, context: dict):
    if not await engine.get_config('notify_bot_errors'):
        return
    module_name = context.get('module', '?')
    func_name = context.get('function', '?')
    text = _error_text('Ошибка стационарного бота', module_name, func_name, error_text)
    await _send(text, throttle_key=f"bot:{module_name}:{func_name}")


# --------------------------------------------------------------------- #
# Запуск юзербота
#
# ВАЖНО: намеренно НЕ @decorators.on_start() — тот вызывается ВНУТРИ
# Core.start() (core/engine.py, шаг 2.9), а стационарный бот (main.py)
# подключается ТОЛЬКО ПОСЛЕ того, как core.start() вернёт управление
# (main.py: `await core.start()`, и только потом, если задан токен,
# `create_bot()`/`core.set_bot()`) — то есть на @decorators.on_start()
# engine._aiogram_bot ВСЕГДА None, и уведомление ушло бы только в
# терминал, ни разу не в ЛС, даже если бот давно настроен. @bot.on_start()
# — тот самый декоратор из core/bot_decorators.py (`import bot`),
# который вызывается через Core._fire_bot_on_start() РОВНО в момент,
# когда core.set_bot(bot, dp) отработал и бот реально готов принимать
# сообщения (см. докстринг set_bot() в core/engine.py) — то есть то
# самое "все инициализации завершены", которое нужно этому уведомлению.
# --------------------------------------------------------------------- #

@bot.on_start()
async def notify_startup():
    if not await engine.get_config('notify_startup'):
        return
    text = "\n".join([
        visual.title('Юзербот запущен'),
        visual.field('версия', engine.get_version()),
        visual.field('модулей', len(engine.modules)),
        visual.field('время', _now_str()),
    ])
    await _send(text)


# --------------------------------------------------------------------- #
# Переключение аккаунта (decorators.on_account_switch())
# --------------------------------------------------------------------- #

@decorators.on_account_switch()
async def notify_account_switch(number):
    if not await engine.get_config('notify_account_switch'):
        return
    text = "\n".join([
        visual.title('Смена аккаунта'),
        visual.field('аккаунт', html.escape(str(number))),
        visual.field('время', _now_str()),
    ])
    await _send(text)


@decorators.command('logs', 'logshelp')
async def logs_help_cmd(event):
    """короткая справка по модулю Logging"""
    await engine.answer(
        event,
        visual.title('Logging', 'логи в ЛС через стационарного бота') + "\n"
        + visual.item('дублирует ошибки и важные события юзербота') + "\n"
        + visual.item('ошибки, запуск, смена аккаунта, <code>engine.botlog()</code> модулей') + "\n\n"
        + visual.field('настройки', '.config logging'),
    )
