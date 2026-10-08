"""
name: base64_codec
category: utils
title: Base64
description: кодирует текст в base64 (mode=encode) или раскодирует (mode=decode)
args: text: string, mode?: string
example: {"text": "привет", "mode": "encode"}
keywords: base64, кодируй, раскодируй, закодируй
"""
import base64
import binascii


async def run(text: str = '', mode: str = 'encode') -> str:
    try:
        if (mode or 'encode').lower().startswith('d'):
            s = (text or '').strip()
            return base64.b64decode(s + '=' * (-len(s) % 4)).decode('utf-8', errors='replace')
        return base64.b64encode((text or '').encode('utf-8')).decode('ascii')
    except (binascii.Error, ValueError) as e:
        return f"Ошибка: {e}"
