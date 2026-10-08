"""
Настройка таймаута по умолчанию для engine.wait_message() (core/waiters.py) —
применяется, только если timeout не передан явно в самом вызове.
"""

import engine
import decorators

NAME = "Ожидание сообщений"
MODULE_NAME = "waiters"
VERSION = "1.0"
AUTHOR = "FRX"
DESCRIPTION = "Настройка таймаута по умолчанию для engine.wait_message()"


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='default_timeout',
        description='Таймаут по умолчанию для engine.wait_message(), если не передан явно, сек',
        data_type='int', default=60,
    )
