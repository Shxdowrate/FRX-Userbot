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
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Waiters.py"


@decorators.on_start()
async def setup_configs():
    await engine.create_config(
        key='default_timeout',
        description='Таймаут по умолчанию для engine.wait_message(), если не передан явно, сек',
        data_type='int', default=60,
    )
