"""
core/bot_events.py

Telethon-совместимые обёртки над "сырыми" aiogram-объектами — чтобы
хэндлеры, объявленные через core/bot_decorators.py (`import bot` внутри
модуля юзербота), получали событие в ПРИВЫЧНОМ для Telethon стиле, а не
в стиле AIOgram. Юзербот в этом проекте приоритетнее стационарного бота
(см. README) — поэтому весь новый синтаксис подгоняется под Telethon,
а не наоборот.

Сообщения самого юзербота (Telethon) НИКАК не оборачиваются — они и так
уже настоящие telethon.events.NewMessage.Event, просто передаются как
есть в bot.watcher(watch='userbot'/'all'). Обёртки из этого файла нужны
только для событий, пришедших со стороны aiogram (стационарный бот).

Оба класса ниже проксируют неизвестные атрибуты в исходный aiogram-объект
через __getattr__ — как и настоящий telethon-event, который проксирует
в свой .message. Так что при необходимости "сырой" aiogram-объект всегда
доступен и напрямую (event.raw), и через любое поле, которого нет в
обёртке (event.photo, event.document, event.message_id и т.д.)
"""

from pathlib import Path

from .paths import FILES_DIR


class BotMessageEvent:
    """
    Обёртка над aiogram.types.Message — аналог telethon.events.NewMessage.Event.
    Передаётся в bot.command()/bot.watcher(watch='bot'/'all').
    """

    def __init__(self, message, core, origin='bot'):
        self.raw = message           # aiogram.types.Message
        self.message = message       # телетон-стиль: event.message тоже доступен
        self._core = core
        self.origin = origin         # 'bot' | 'userbot' (тут всегда 'bot')

    # ------------------------------------------------------------------ #
    # Поля, аналогичные telethon-событию
    # ------------------------------------------------------------------ #

    @property
    def id(self):
        return self.raw.message_id

    @property
    def raw_text(self):
        return self.raw.text or self.raw.caption or ''

    @property
    def text(self):
        return self.raw_text

    @property
    def sender_id(self):
        return self.raw.from_user.id if self.raw.from_user else None

    @property
    def chat_id(self):
        return self.raw.chat.id

    @property
    def out(self):
        # Сообщение прислано боту КЕМ-ТО ДРУГИМ — своих исходящих отправок
        # aiogram-диспетчер как апдейты не присылает, поэтому у входящих
        # сообщений это всегда False (как и .out=False у чужих сообщений
        # в Telethon).
        return False

    @property
    def is_private(self):
        return self.raw.chat.type == 'private'

    @property
    def is_group(self):
        return self.raw.chat.type in ('group', 'supergroup')

    @property
    def is_channel(self):
        return self.raw.chat.type == 'channel'

    @property
    def is_reply(self):
        return self.raw.reply_to_message is not None

    # ------------------------------------------------------------------ #
    # Методы, аналогичные telethon-событию
    # ------------------------------------------------------------------ #

    async def reply(self, text, buttons=None, file=None):
        """Отвечает на это сообщение (реплаем) от лица стационарного бота."""
        return await self._core.bot.reply(self.raw, text, file=file, buttons=buttons)

    async def respond(self, text, buttons=None, file=None):
        """Отправляет новое сообщение в тот же чат, БЕЗ реплая на это."""
        return await self._core.bot.send_message(self.chat_id, text, file=file, buttons=buttons)

    async def edit(self, text, buttons=None, file=None):
        """
        Как и у Telethon: редактировать можно только СВОЁ сообщение.
        Входящие сообщения (то, что ловят command()/watcher()) отправлены
        не ботом, поэтому здесь всегда PermissionError — используйте
        .reply()/.respond(), либо, если у вас есть именно СВОЁ сообщение
        бота (например, возвращённое из .reply() ранее), редактируйте его
        напрямую через engine.bot.edit(message, ...).
        """
        raise PermissionError(
            "event.edit(): нельзя редактировать сообщение, отправленное не ботом "
            "(как и в Telethon — MessageAuthorRequiredError). Используйте "
            "event.reply()/event.respond(), либо engine.bot.edit(...) для "
            "сообщения, которое бот отправил сам."
        )

    async def delete(self):
        """Удаляет это сообщение (нужны права бота в чате)."""
        return await self._core._aiogram_bot.delete_message(self.chat_id, self.raw.message_id)

    async def get_sender(self):
        """Аналог event.get_sender() — у aiogram данные отправителя уже в апдейте, без похода в API."""
        return self.raw.from_user

    async def download_media(self, file=None):
        """
        Скачивает медиа из сообщения (фото/документ/видео/аудио/голосовое/
        видеосообщение/стикер/анимация) — аналог telethon
        message.download_media(file=...). file — папка (со слэшем на
        конце) либо полный путь, по умолчанию user/data/files/.
        Возвращает путь к скачанному файлу либо None, если медиа нет.
        """
        m = self.raw
        media = (
            (m.photo[-1] if m.photo else None)
            or m.document or m.video or m.audio or m.voice
            or m.video_note or m.sticker or m.animation
        )
        if media is None:
            return None

        dest = file or (str(FILES_DIR) + '/')
        if dest.endswith('/') or dest.endswith('\\'):
            Path(dest).mkdir(parents=True, exist_ok=True)
            name = getattr(media, 'file_name', None) or f"{media.file_unique_id}"
            dest = f"{dest}{name}"
        else:
            Path(dest).parent.mkdir(parents=True, exist_ok=True)

        await self._core._aiogram_bot.download(media.file_id, destination=dest)
        return dest

    def __getattr__(self, item):
        # Escape hatch: всё, чего нет в обёртке, — напрямую из aiogram.types.Message
        return getattr(self.raw, item)

    def __repr__(self):
        return f"<BotMessageEvent chat_id={self.chat_id} sender_id={self.sender_id} text={self.raw_text!r}>"


class BotChatMemberEvent:
    """
    Обёртка над aiogram.types.ChatMemberUpdated — аналог
    telethon.events.ChatAction.Event, только со стороны стационарного
    бота. Передаётся в bot.telegram_event().
    """

    _ACTIVE_STATUSES = {'member', 'administrator', 'creator'}

    def __init__(self, update, core, is_bot_event):
        self.raw = update            # aiogram.types.ChatMemberUpdated
        self._core = core
        self.is_bot_event = is_bot_event   # True — апдейт про самого бота (my_chat_member)
        self.origin = 'bot'

    @property
    def chat_id(self):
        return self.raw.chat.id

    @property
    def user_id(self):
        return self.raw.new_chat_member.user.id

    @property
    def old_status(self):
        return self.raw.old_chat_member.status

    @property
    def new_status(self):
        return self.raw.new_chat_member.status

    @property
    def user_joined(self):
        return (
            self.old_status not in self._ACTIVE_STATUSES
            and self.new_status in self._ACTIVE_STATUSES
        )

    @property
    def user_left(self):
        return (
            self.old_status in self._ACTIVE_STATUSES
            and self.new_status not in self._ACTIVE_STATUSES
        )

    def __getattr__(self, item):
        return getattr(self.raw, item)

    def __repr__(self):
        return (
            f"<BotChatMemberEvent chat_id={self.chat_id} user_id={self.user_id} "
            f"{self.old_status}->{self.new_status} is_bot_event={self.is_bot_event}>"
        )
