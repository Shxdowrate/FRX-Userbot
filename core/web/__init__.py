"""
core/web — локальный сайт управления FRX (шапка-меню, анимации, Emulate; главная, Emulate в «модулях», сайт переживает рестарт; вкладка BotChat).

    server.py  — WebPanel: aiohttp-сервер на 127.0.0.1, защита, API панели
    ui.py      — `import web`: декоратор @web.page и построитель интерфейса Ui для модулей
    botchat_api.py — API вкладки BotChat (хранилище — core/botchat.py)
    static/    — index.html, style.css, app.js, botchat.js/.css, фон и шрифт

Emulate — отрисовка команд/форм/диалогов любого модуля — живёт в core/emulate.py;
API для сайта — в server.py (`/api/emulate/*`).
"""

from .ui import page, Ui, Ctx
from .server import WebPanel

__all__ = ['page', 'Ui', 'Ctx', 'WebPanel']
