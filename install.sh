#!/bin/sh
# FRX — установка на Linux / macOS / Termux. Два режима:
#   * рядом с файлами FRX (есть frx_setup.py) — окружение, зависимости, первый вход в Telegram, автозапуск;
#   * один install.sh (или прямо из интернета) — сам скачивает FRX с GitHub в папку и делает то же самое:
#       curl -fsSL https://raw.githubusercontent.com/Shxdowrate/FRX-Userbot/main/install.sh | sh
# Ключи передаются в frx_setup.py: --yes, --no-service, --no-login, --remove-service.
# Переменные: FRX_DIR — куда ставить (по умолчанию ~/FRX), FRX_REPO — владелец/репозиторий,
#             FRX_GITHUB_TOKEN — токен для приватного репозитория.

REPO="${FRX_REPO:-Shxdowrate/FRX-Userbot}"

# --------------------------------------------------------------------------- Termux (Android)
# Termux — полноценный Linux на телефоне: ставим системой всё, что нужно модулям (компиляторы для пакетов с C/Rust,
# ffmpeg, библиотеки картинок и XML) и готовые сборки тяжёлых Python-пакетов из репозитория Termux.
# Список пакетов продублирован в тесте CI репозитория FRX-Userbot-APK (tests/termux) — меняете здесь, поменяйте и там.
TERMUX_PKGS="python git clang make pkg-config binutils rust libjpeg-turbo libpng freetype libwebp libxml2 libxslt libffi openssl ffmpeg"
TERMUX_PY_PKGS="python-pillow python-lxml python-numpy python-cryptography python-psutil"

is_termux() {
  [ -n "${TERMUX_VERSION:-}" ] && return 0
  case "${PREFIX:-}" in *com.termux*) return 0 ;; esac
  return 1
}

termux_prepare() {
  echo "  Termux: ставлю системные пакеты (Python, компиляторы, ffmpeg, библиотеки). Первый раз — 5–15 минут…"
  # pkg каждый раз выбирает случайное зеркало, и оно бывает битым — до трёх попыток
  ok=""
  for try in 1 2 3; do
    yes | pkg update >/dev/null 2>&1
    # shellcheck disable=SC2086
    if pkg install -y $TERMUX_PKGS; then ok=1; break; fi
    echo "  попытка $try не удалась — пробую другое зеркало…"
  done
  if [ -z "$ok" ]; then
    echo "  Не все пакеты встали. Проверьте интернет, выберите зеркало (termux-change-repo) и запустите install.sh снова."
    exit 1
  fi
  for p in $TERMUX_PY_PKGS; do
    if pkg install -y "$p" >/dev/null 2>&1; then echo "  √ $p"; else echo "  · $p — нет в репозитории Termux, соберётся через pip"; fi
  done
  # готовые сборки под Android тяжёлых пакетов (pydantic-core, lxml, primp, numpy…) из CI FRX-Userbot-APK:
  # без них pip собирает их на телефоне по 5–15 минут каждый
  export PIP_FIND_LINKS="https://github.com/Shxdowrate/FRX-Userbot-APK/releases/expanded_assets/wheels" PIP_PREFER_BINARY=1
  ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null)"
  export ANDROID_API_LEVEL="${ANDROID_API_LEVEL:-24}"     # нужен сборщику Rust-пакетов (maturin) под Android
  command -v termux-wake-lock >/dev/null 2>&1 && termux-wake-lock
}

# вопросы задаём через терминал, даже если скрипт пришёл через «curl … | sh»
has_tty() { ( : < /dev/tty ) 2>/dev/null; }

ask() {
  if has_tty; then
    printf '%s' "$1" > /dev/tty
    read -r REPLY < /dev/tty || REPLY=""
  else
    REPLY=""
  fi
}

find_python() {
  PY=""
  for c in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$c"; return 0
    fi
  done
  return 1
}

sudo_cmd() {
  if [ "$(id -u)" = "0" ]; then "$@"; elif command -v sudo >/dev/null 2>&1; then sudo "$@"; else "$@"; fi
}

install_python() {
  if command -v pkg >/dev/null 2>&1 && [ -n "$PREFIX" ]; then CMD="pkg install -y python"
  elif command -v apt-get >/dev/null 2>&1; then CMD="apt-get update && apt-get install -y python3 python3-venv python3-pip"
  elif command -v dnf >/dev/null 2>&1; then CMD="dnf install -y python3 python3-pip"
  elif command -v pacman >/dev/null 2>&1; then CMD="pacman -S --noconfirm python python-pip"
  elif command -v apk >/dev/null 2>&1; then CMD="apk add python3 py3-pip"
  elif command -v brew >/dev/null 2>&1; then CMD="brew install python"
  else return 1
  fi
  ask "Нужен Python 3.10+. Поставить сейчас ($CMD)? [Д/н]: "
  case "$REPLY" in н|n|no|нет|N|Н) return 1 ;; esac
  case "$CMD" in
    brew*|pkg*) sh -c "$CMD" ;;
    *) sudo_cmd sh -c "$CMD" ;;
  esac
  find_python
}

if is_termux; then
  termux_prepare
fi

if ! find_python && ! install_python; then
  echo "Нужен Python 3.10 или новее. Установите его и запустите install.sh снова:"
  echo "  Debian / Ubuntu:  sudo apt update && sudo apt install -y python3 python3-venv python3-pip"
  echo "  Fedora / RHEL:    sudo dnf install -y python3 python3-pip"
  echo "  Arch:             sudo pacman -S python python-pip"
  echo "  Alpine:           sudo apk add python3 py3-pip"
  echo "  macOS:            brew install python   (https://brew.sh)"
  echo "  Termux:           pkg install python"
  exit 1
fi

# --------------------------------------------------------------------------- режим 1: файлы FRX уже рядом
HERE=""
if [ -f "$0" ]; then HERE="$(cd "$(dirname "$0")" && pwd)"; fi
if [ -n "$HERE" ] && [ -f "$HERE/frx_setup.py" ]; then
  cd "$HERE" || exit 1
  exec "$PY" -X utf8 frx_setup.py "$@"
fi

# --------------------------------------------------------------------------- режим 2: скачать FRX с GitHub
DEST="${FRX_DIR:-$HOME/FRX}"
echo
echo "  FRX будет установлен в:  $DEST"
ask "  Enter — продолжить, или впишите другую папку: "
[ -n "$REPLY" ] && DEST="$REPLY"
case "$DEST" in "~"*) DEST="$HOME${DEST#\~}" ;; esac

TMP="$(mktemp -d 2>/dev/null || echo "/tmp/frx_dl_$$")"
mkdir -p "$TMP"
trap 'rm -rf "$TMP"' EXIT INT TERM

if [ -n "$FRX_GITHUB_TOKEN" ]; then
  URL="https://api.github.com/repos/$REPO/tarball"
else
  URL="https://github.com/$REPO/archive/HEAD.tar.gz"
fi
echo "  Скачиваю FRX: github.com/$REPO …"
if command -v curl >/dev/null 2>&1; then
  if [ -n "$FRX_GITHUB_TOKEN" ]; then
    curl -fL --progress-bar -H "Authorization: token $FRX_GITHUB_TOKEN" -H "User-Agent: FRX-Installer" -o "$TMP/frx.tgz" "$URL"
  else
    curl -fL --progress-bar -H "User-Agent: FRX-Installer" -o "$TMP/frx.tgz" "$URL"
  fi
elif command -v wget >/dev/null 2>&1; then
  if [ -n "$FRX_GITHUB_TOKEN" ]; then
    wget -q --show-progress --header="Authorization: token $FRX_GITHUB_TOKEN" -O "$TMP/frx.tgz" "$URL"
  else
    wget -q --show-progress -O "$TMP/frx.tgz" "$URL"
  fi
else
  "$PY" -c "import sys,urllib.request as u; r=u.Request(sys.argv[1],headers={'User-Agent':'FRX-Installer'}); open(sys.argv[2],'wb').write(u.urlopen(r,timeout=120).read())" "$URL" "$TMP/frx.tgz"
fi
if [ ! -s "$TMP/frx.tgz" ]; then
  echo "  Не скачалось. Проверьте интернет; если репозиторий приватный — задайте FRX_GITHUB_TOKEN."
  exit 1
fi

mkdir -p "$TMP/x"
if ! tar -xzf "$TMP/frx.tgz" -C "$TMP/x" 2>/dev/null; then
  "$PY" -c "import sys,tarfile; tarfile.open(sys.argv[1]).extractall(sys.argv[2])" "$TMP/frx.tgz" "$TMP/x" || { echo "  Архив не распаковался"; exit 1; }
fi
SRC="$(find "$TMP/x" -mindepth 1 -maxdepth 1 -type d | head -n 1)"
if [ ! -f "$SRC/frx_setup.py" ]; then
  echo "  В архиве нет frx_setup.py — тот ли это репозиторий ($REPO)?"
  exit 1
fi

mkdir -p "$DEST"
# код — поверх, папку user/ (ваши данные, если FRX уже стоял) не трогаем
for item in "$SRC"/* "$SRC"/.[!.]*; do
  [ -e "$item" ] || continue
  [ "$(basename "$item")" = "user" ] && continue
  cp -R "$item" "$DEST"/
done
chmod +x "$DEST"/*.sh 2>/dev/null
echo "  Готово: $DEST"
echo

cd "$DEST" || exit 1
if has_tty; then
  "$PY" -X utf8 frx_setup.py "$@" < /dev/tty
else
  "$PY" -X utf8 frx_setup.py "$@"
fi
echo
echo "  Папка FRX: $DEST   (запуск в следующий раз: sh $DEST/start.sh)"
