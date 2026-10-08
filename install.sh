#!/bin/sh
# FRX — установка на Linux / macOS: окружение, зависимости, первый вход в Telegram, автозапуск.
# Запуск:  sh install.sh   (ключи: --yes, --no-service, --no-login, --remove-service — см. frx_setup.py)
cd "$(dirname "$0")" || exit 1

PY=""
for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PY="$c"; break
  fi
done

if [ -z "$PY" ]; then
  echo "Нужен Python 3.10 или новее. Установите его и запустите install.sh снова:"
  echo "  Debian / Ubuntu:  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  echo "  Fedora / RHEL:    sudo dnf install -y python3 python3-pip"
  echo "  Arch:             sudo pacman -S python python-pip"
  echo "  Alpine:           sudo apk add python3 py3-pip"
  echo "  macOS:            brew install python   (https://brew.sh)"
  exit 1
fi

exec "$PY" -X utf8 frx_setup.py "$@"
