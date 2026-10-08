"""
core/stationary_bot.py

BotFacade — дружелюбная обёртка над aiogram Bot, доступная модулям как
`engine.bot`. Даёт отправлять/отвечать/редактировать сообщения ОТ ЛИЦА
стационарного бота обычными сообщениями в чате (не через инлайн-формы —
для них есть engine.create_inlineform(), см. core/inline_forms.py), с
той же логикой прикрепления файлов, что и у engine.send_message/
engine.reply (core/engine.py): file — путь на диске, ID из реестра
(core/files.py), либо уже готовый объект aiogram InputFile.

    await engine.bot.send_message(chat_id, "Привет!", file="report.pdf")
    await engine.bot.reply(message, "Принято", buttons=[[("Ок", "ok")]])
    await engine.bot.edit(message, "Обновлено")

message здесь — aiogram.types.Message (то, что прилетает в хэндлеры
aiobot/bot.py), а НЕ Telethon-сообщение (для тех — engine.reply/
engine.answer/engine.send_message в core/engine.py).

"Сырой" aiogram Bot (если понадобится метод, которого нет в обёртке) —
через engine.bot.raw.
"""

from pathlib import Path

_PHOTO_EXT = {'.jpg', '.jpeg', '.png', '.webp', '.gif'}


class BotFacade:
    def __init__(self, core):
        self._core = core

    @property
    def raw(self):
        """"Сырой" aiogram Bot — для методов, которых нет в этой обёртке."""
        if self._core._aiogram_bot is None:
            raise RuntimeError(
                "Стационарный бот не запущен (aiogram_token не задан в user/config.json)"
            )
        return self._core._aiogram_bot

    async def _resolve_input_file(self, file):
        """
        file: путь на диске / ID из file_registry (core/files.py) /
        уже готовый aiogram InputFile -> (aiogram InputFile, "подсказка"
        по имени файла — используется только для определения photo/document).
        """
        from aiogram.types import InputFile, FSInputFile
        if isinstance(file, InputFile):
            return file, getattr(file, 'filename', '') or ''
        obj = await self._core.file_registry.get(query=file)
        path = obj['path'] if obj else file
        return FSInputFile(path), path

    def _markup(self, buttons):
        if not buttons:
            return None
        if type(buttons).__name__ == 'InlineKeyboardMarkup':     # готовая разметка aiogram (url / web_app-кнопки)
            return buttons
        return self._core.build_inline_markup(buttons)

    async def send_message(self, chat_id, text, file=None, buttons=None, reply_to=None):
        """Отправляет сообщение в chat_id. file/buttons опциональны, reply_to — message_id, на который отвечаем."""
        markup = self._markup(buttons)
        if file is None:
            return await self.raw.send_message(
                chat_id, text, reply_markup=markup, reply_to_message_id=reply_to,
            )
        input_file, path_hint = await self._resolve_input_file(file)
        # файлы грузятся с запасом по времени — стандартные 60 с у aiogram обрывали бэкапы и тяжёлые файлы
        if Path(path_hint).suffix.lower() in _PHOTO_EXT:
            return await self.raw.send_photo(
                chat_id, photo=input_file, caption=text,
                reply_markup=markup, reply_to_message_id=reply_to, request_timeout=600,
            )
        return await self.raw.send_document(
            chat_id, document=input_file, caption=text,
            reply_markup=markup, reply_to_message_id=reply_to, request_timeout=600,
        )

    async def reply(self, message, text, file=None, buttons=None):
        """Отвечает на message (aiogram.types.Message)."""
        return await self.send_message(
            message.chat.id, text, file=file, buttons=buttons, reply_to=message.message_id,
        )

    async def edit(self, message, text, file=None, buttons=None):
        """
        Редактирует message (aiogram.types.Message).
        file=... — Bot API не даёт превратить текстовое сообщение в
        сообщение с вложением через edit, поэтому в этом случае старое
        сообщение удаляется и на его место отправляется новое (тот же
        chat_id, без reply — Telegram не позволяет "ответить" на удалённое).
        """
        markup = self._markup(buttons)
        if file is not None:
            try:
                await self.raw.delete_message(message.chat.id, message.message_id)
            except Exception:
                pass  # не критично — например, сообщению больше 48 часов
            return await self.send_message(message.chat.id, text, file=file, buttons=buttons)
        return await self.raw.edit_message_text(
            text, chat_id=message.chat.id, message_id=message.message_id, reply_markup=markup,
        )
