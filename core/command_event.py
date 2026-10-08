"""
core/command_event.py

CommandEvent — "прокси" настоящего события Telethon для engine.exec_modcmd().

Команды модулей читают свои аргументы через engine.get_args() — то есть из
current_event.raw_text. Когда команду запускает не пользователь сообщением, а
код (например, кнопка в .help открывает `.config <модуль>`), настоящего
сообщения с нужным текстом нет. CommandEvent решает это: делегирует ВСЁ
(chat_id, sender_id, edit(), reply(), respond(), get_reply_message(), ...)
настоящему событию, но подменяет text/raw_text (и то же самое у .message)
на "<префикс><команда> <аргументы>".
"""


class _MessageProxy:
    def __init__(self, message, text):
        object.__setattr__(self, '_message', message)
        object.__setattr__(self, '_text', text)

    raw_text = property(lambda self: self._text)
    text = property(lambda self: self._text)

    def __getattr__(self, name):
        return getattr(self._message, name)


class CommandEvent:
    def __init__(self, event, text):
        object.__setattr__(self, '_event', event)
        object.__setattr__(self, '_text', text)
        message = getattr(event, 'message', None)
        object.__setattr__(self, '_msg', _MessageProxy(message, text) if message is not None else None)

    raw_text = property(lambda self: self._text)
    text = property(lambda self: self._text)

    @property
    def message(self):
        return self._msg if self._msg is not None else self._event.message

    @property
    def original_event(self):
        """Настоящее событие, из которого запущена команда."""
        return self._event

    def __getattr__(self, name):
        return getattr(self._event, name)
