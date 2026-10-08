"""
core/context.py

ContextVar-ы, позволяющие utils-функциям вроде get_args(), wait_message('this')
и db работать БЕЗ явной передачи event/module_name в аргументах —
именно так, как это описано в ТЗ (например get_args() без аргументов).

current_event  — событие Telethon (message), обрабатываемое прямо сейчас
current_module — имя модуля (MODULE_NAME), чей хэндлер выполняется прямо сейчас
current_emulation: сессия Emulate (core/emulate.py), если код выполняется «внутри сайта»:
                 ответы, формы и ожидания сообщений тогда уходят на сайт, а не в Telegram
current_guest  — id гостя (не владельца), если код выполняется по его команде или по его клику в форме
                 (core/access.py); None — действует владелец
"""

from contextvars import ContextVar

current_event: ContextVar = ContextVar('current_event', default=None)
current_module: ContextVar = ContextVar('current_module', default=None)
current_emulation: ContextVar = ContextVar('current_emulation', default=None)
current_guest: ContextVar = ContextVar('current_guest', default=None)
