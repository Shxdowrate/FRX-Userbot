"""
name: password_gen
category: utils
title: Пароль
description: генерирует надёжный случайный пароль (secrets); length — длина 8–64, symbols — добавлять ли спецсимволы
args: length?: int, symbols?: bool
example: {"length": 20, "symbols": true}
keywords: пароль, сгенерируй пароль, придумай пароль
"""
import secrets
import string


async def run(length: int = 16, symbols: bool = True) -> str:
    n = max(8, min(int(length), 64))
    alphabet = string.ascii_letters + string.digits + ('!@#$%^&*-_=+?' if symbols else '')
    while True:
        pw = ''.join(secrets.choice(alphabet) for _ in range(n))
        if any(c.islower() for c in pw) and any(c.isupper() for c in pw) and any(c.isdigit() for c in pw):
            return pw
