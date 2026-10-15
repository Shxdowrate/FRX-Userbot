"""Эмуляция пакета Hikka/Heroku для FRX (слой совместимости HikkaCompat).

Модули Hikka пишут `from .. import loader, utils` — они загружаются как hikka.modules.<имя>, и этот пакет
отвечает на их импорты. Имена heroku.*, hikkatl.*, herokutl.* перенаправляются сюда и на telethon
(см. hikka_compat/_aliases.py). Код Hikka/Heroku здесь не используется — это независимая реализация
их публичного API.
"""

__all__ = []
