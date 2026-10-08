#!/bin/sh
# FRX: свернуть проект перед публикацией — удалить личные данные (user/, сессии, кэш). Подробности: frx_pack.py
cd "$(dirname "$0")" || exit 1
if command -v python3 >/dev/null 2>&1; then PY=python3; else PY=python; fi
exec "$PY" -X utf8 frx_pack.py "$@"
