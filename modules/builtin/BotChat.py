"""
modules/builtin/BotChat.py

BotChat — «аккаунт» стационарного бота на сайте `.web` (вкладка `botchat`): список чатов, переписка, отправка и правка
сообщений от имени бота, файлы, альбомы, опросы, реакции, закрепы, пересылка, участники — как в Telegram, но в стиле сайта.
Работа — в core/botchat.py (хранилище) и core/web/botchat_api.py (API); интерфейс — static/botchat.js.

Bot API не отдаёт историю и список чатов, поэтому история копится с момента, как этот модуль увидел сообщение
(`history_limit` последних на чат). профиль бота, cross watch для групп, кружки/голосовые (нужен ffmpeg), прогресс отправки.
Бот не нажимает инлайн-кнопки, не видит «печатает»/онлайн/прочтение и скачивает файлы до 20 МБ.

  .botchat           — сколько чатов и сообщений в истории
  .botchat clear     — стереть историю BotChat (сами чаты в Telegram не трогаются)
  .config botchat    — enabled (писать историю), history_limit, media_cache_mb
"""

NAME = "BotChat"
MODULE_NAME = "botchat"
VERSION = "1.2"
AUTHOR = "FRX"
DESCRIPTION = "Аккаунт стационарного бота на сайте: чаты, переписка и отправка от имени бота (вкладка botchat)"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/BotChat.py"

import decorators
import engine
import visual


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='enabled', description='Вести историю чатов бота для вкладки botchat',
        data_type='bool', default=True,
    )
    await engine.create_config(
        key='history_limit', description='Сколько последних сообщений хранить на каждый чат',
        data_type='int', default=500, valid={'min_value': 50, 'max_value': 20000},
    )
    await engine.create_config(
        key='media_cache_mb', description='Предел кэша скачанных файлов (фото, видео, документы), МБ',
        data_type='int', default=300, valid={'min_value': 10, 'max_value': 20000},
    )


@decorators.command('botchat', 'bc')
async def botchat_cmd(event):
    """состояние истории BotChat; `.botchat clear` — стереть историю"""
    arg = engine.get_args().strip().lower()
    bc = engine.botchat

    if arg in ('clear', 'очистить'):
        await bc.clear()
        await engine.answer(event, visual.ok("История BotChat стёрта") + "\n" + visual.hint("новые сообщения снова накопятся"))
        return

    s = bc.stats()
    bot = f"{visual.visual('status_on')} стационарный бот запущен" if bc.available \
        else visual.err("стационарный бот не запущен") + " — " + visual.hint("вкладка botchat работать не будет")
    await engine.answer(
        event,
        "\n".join([
            visual.title("BotChat"),
            bot,
            "",
            visual.section("История"),
            visual.field("чатов", s['chats']),
            visual.field("сообщений", s['messages']),
            visual.field("файл истории", f"{s['store_bytes'] // 1024} КБ"),
            "",
            visual.item("открыть: <code>.web</code> → вкладка <b>botchat</b>"),
            visual.item("сброс истории — <code>.botchat clear</code>"),
        ]),
    )
