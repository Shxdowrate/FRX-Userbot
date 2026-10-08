#!/bin/sh
# FRX — запуск (Linux / macOS). Первый раз — сначала установка (install.sh), её start.sh вызовет сам.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  sh install.sh --no-service || exit 1
fi
exec .venv/bin/python -u frx_systemkeeper.py "$@"
