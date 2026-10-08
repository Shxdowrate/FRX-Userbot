"""
name: hash_text
category: utils
title: Хэш текста
description: считает хэш текста: md5, sha1, sha256, sha512
args: text: string, algo?: string
example: {"text": "hello", "algo": "sha256"}
keywords: хэш, хеш, sha256, md5, контрольная сумма
"""
import hashlib


async def run(text: str = '', algo: str = 'sha256') -> str:
    a = (algo or 'sha256').lower().replace('-', '')
    if a not in ('md5', 'sha1', 'sha256', 'sha512'):
        return "algo: md5, sha1, sha256 или sha512"
    return hashlib.new(a, (text or '').encode('utf-8')).hexdigest()
