"""Модули Dragon-Userbot (Pyrogram) не поддерживаются слоем совместимости FRX."""


def is_dragon(code: str) -> bool:
    return "from pyrogram" in code and "loader.Module" not in code
