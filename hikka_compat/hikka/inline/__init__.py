"""Инлайн-часть пространства Hikka (формы, галереи, списки, инлайн-команды) поверх стационарного бота FRX."""

import random

from .types import (
    BotInlineCall,
    BotInlineMessage,
    BotMessage,
    GeekInlineQuery,
    InlineCall,
    InlineMessage,
    InlineQuery,
    InlineUnit,
)


def rand(size: int, /) -> str:
    return "".join(random.choice("abcdefghijklmnopqrstuvwxyz1234567890") for _ in range(size))


def __getattr__(name):                     # InlineManager — лениво (иначе круговой импорт с utils)
    if name == "InlineManager":
        from .core import InlineManager

        return InlineManager
    raise AttributeError(name)


__all__ = [
    "InlineManager", "InlineCall", "BotInlineCall", "InlineMessage", "BotInlineMessage", "BotMessage",
    "InlineQuery", "GeekInlineQuery", "InlineUnit", "rand",
]
