"""
name: send_to_saved
category: telegram
title: В избранное
description: отправляет текст в «Избранное» аккаунта (безопасно: пишет только самому себе)
args: text: string
keywords: сохрани в избранное, отправь мне, запиши себе, в избранное
"""
import engine


async def run(text: str = '') -> str:
    if not (text or '').strip():
        return "Ошибка: пустой текст"
    await engine.client.send_message('me', text)
    return "Отправлено в Избранное"
