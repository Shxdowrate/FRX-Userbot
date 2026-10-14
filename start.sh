#!/bin/sh
# FRX — запуск (Linux / macOS). Первый раз — сначала установка (install.sh), её start.sh вызовет сам.
cd "$(dirname "$0")" || exit 1
if [ ! -x .venv/bin/python ]; then
  sh install.sh --no-service || exit 1
fi
case "${PREFIX:-}" in
  *com.termux*)                                     # Termux: не давать телефону усыплять FRX; API — для сборки Rust-пакетов
    command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
    [ -n "${ANDROID_API_LEVEL:-}" ] || export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
    # готовые сборки тяжёлых пакетов под Android (CI FRX-Userbot-APK) — пакеты модулей ставятся за секунды
    export PIP_FIND_LINKS="${PIP_FIND_LINKS:-https://github.com/Shxdowrate/FRX-Userbot-APK/releases/expanded_assets/wheels}" PIP_PREFER_BINARY=1 ;;
esac
exec .venv/bin/python -u frx_systemkeeper.py "$@"
