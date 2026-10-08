"""
frx_setup.py — установка FRX на любую машину: сервер (Linux), свой компьютер (Windows, macOS, Linux).

Запускать через install.sh (Linux, macOS) или install.bat (Windows) — они находят Python 3.10+ и вызывают этот файл.
Только стандартная библиотека Python.

Шаги:
    1. Виртуальное окружение .venv в папке проекта и зависимости из requirements.txt.
    2. Первый вход в Telegram (если его ещё не было): api_id / api_hash, номер, код — прямо в этом окне.
       Юзербот запускается в режиме настройки (FRX_SETUP_ONLY=1) и сам останавливается после входа.
    3. Автозапуск (по желанию):
         Linux с systemd — служба frx (от root — системная, иначе — пользовательская + lingering);
         macOS           — LaunchAgent (запуск при входе в систему, перезапуск при падении);
         Windows         — ярлык-скрипт в папке «Автозагрузка» (запуск при входе в Windows, свёрнутым окном).
       В Docker автозапуск — забота docker compose (restart: on-failure).
    4. Подсказка, как открыть сайт: .web в Telegram, SSH-туннель или публичный адрес (.web tunnel on).

Ключи:
    --yes          отвечать «да» на все вопросы (автозапуск включается)
    --no-service   не настраивать автозапуск
    --no-login     не запускать первый вход (например, сессию перенесли из бэкапа)
    --remove-service   убрать автозапуск, установленный раньше
"""

from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
import frx_systemkeeper as keeper  # noqa: E402

SERVICE = 'frx'
IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
VENV = ROOT / '.venv'
VPY = VENV / ('Scripts/python.exe' if IS_WIN else 'bin/python')
LAUNCHD_LABEL = 'dev.frx.userbot'


def say(text=''):
    print(text, flush=True)


def step(n, text):
    say(f"\n[{n}] {text}")


def ask(question, yes_all, default=True) -> bool:
    if yes_all:
        return True
    hint = 'Д/н' if default else 'д/Н'
    try:
        ans = input(f"{question} [{hint}]: ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ('д', 'да', 'y', 'yes')


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], **kw)


# ----------------------------------------------------------------------------- 1. окружение

def ensure_venv():
    if VPY.is_file():
        say(f"    окружение уже есть: {VENV.name}/")
        return
    say(f"    создаю окружение {VENV.name}/ …")
    r = run([sys.executable, '-m', 'venv', VENV], capture_output=True, text=True)
    if r.returncode != 0 or not VPY.is_file():
        say(r.stderr.strip()[-800:])
        if 'ensurepip' in (r.stderr or '') or 'venv' in (r.stderr or ''):
            v = f"{sys.version_info.major}.{sys.version_info.minor}"
            say(f"\n    Не хватает модуля venv. Debian/Ubuntu: sudo apt install python3-venv (или python{v}-venv), "
                "затем запустите установку снова.")
        shutil.rmtree(VENV, ignore_errors=True)
        sys.exit(1)


def install_requirements():
    say("    ставлю зависимости (requirements.txt) — это может занять пару минут…")
    run([VPY, '-m', 'pip', 'install', '--disable-pip-version-check', '-q', '--upgrade', 'pip'])
    r = run([VPY, '-m', 'pip', 'install', '--disable-pip-version-check', '-r', ROOT / 'requirements.txt'])
    if r.returncode != 0:
        say("\n    pip завершился с ошибкой (см. выше). Частые причины: нет интернета, старый pip, "
            "нет компилятора для какого-то пакета (Debian/Ubuntu: sudo apt install build-essential python3-dev).")
        sys.exit(1)


# ----------------------------------------------------------------------------- 2. первый вход

def logged_in() -> bool:
    data = keeper.DATA_DIR
    if data is None:
        return False
    if (data / 'frx_session.session').is_file():
        return True
    acc = keeper.read_json(data / 'accounts.json')
    return bool(acc)


def first_login():
    say(textwrap.dedent("""\
        Сейчас FRX запустится в этом окне и спросит:
          • api_id и api_hash — их выдают на https://my.telegram.org → API development tools;
          • номер телефона, код из Telegram и, если включён, облачный пароль.
        После входа юзербот сам создаст стационарного бота и остановится — установка продолжится.
    """))
    env = dict(os.environ, FRX_SETUP_ONLY='1')
    try:
        proc = subprocess.Popen([str(VPY), '-u', 'frx_systemkeeper.py'], cwd=ROOT, env=env)
        while True:
            try:
                code = proc.wait()
                break
            except KeyboardInterrupt:
                continue                      # Ctrl+C получил и keeper — ждём, пока он аккуратно завершится
    except OSError as e:
        say(f"    не удалось запустить: {e}")
        sys.exit(1)
    keeper._apply_layout()
    if not logged_in():
        say("\n    Вход не выполнен (код выхода %s). Запустите установку ещё раз." % code)
        sys.exit(1)
    say("    вход выполнен")


# ----------------------------------------------------------------------------- 3. автозапуск

def has_systemd() -> bool:
    return sys.platform.startswith('linux') and Path('/run/systemd/system').is_dir() and bool(shutil.which('systemctl'))


def in_docker() -> bool:
    return Path('/.dockerenv').exists() or os.environ.get('FRX_IN_DOCKER') == '1'


def systemd_unit(system: bool) -> str:
    owner = ''
    if system:
        owner = os.environ.get('SUDO_USER') or ''
        if not owner:
            try:
                import pwd
                owner = pwd.getpwuid(ROOT.stat().st_uid).pw_name
            except Exception:
                owner = 'root'
    lines = [
        '[Unit]',
        'Description=FRX Telegram userbot',
        'After=network-online.target',
        'Wants=network-online.target',
        '',
        '[Service]',
        'Type=simple',
        *([f'User={owner}'] if owner else []),
        f'WorkingDirectory={ROOT}',
        f'ExecStart={VPY} -u frx_systemkeeper.py',
        'Environment=PYTHONUNBUFFERED=1',
        '# .exit останавливает юзербот (код 0) — служба не перезапускает; 78 — нужна настройка (первый вход)',
        'Restart=on-failure',
        'RestartSec=15',
        'RestartPreventExitStatus=78',
        '# SIGTERM получает только keeper, он аккуратно останавливает юзербот',
        'KillMode=mixed',
        'TimeoutStopSec=45',
        '',
        '[Install]',
        f"WantedBy={'multi-user.target' if system else 'default.target'}",
    ]
    return '\n'.join(lines) + '\n'


def install_systemd(yes_all):
    root = os.geteuid() == 0
    sudo = [] if root else ['sudo']
    system = root or (bool(shutil.which('sudo')) and ask(
        "    Сделать системную службу (надёжнее, нужен пароль sudo)? Иначе — служба вашего пользователя", yes_all))
    unit = systemd_unit(system)
    if system:
        path = Path('/etc/systemd/system') / f'{SERVICE}.service'
        ctl = sudo + ['systemctl']
        tmp = keeper.TMP_DIR / f'{SERVICE}.service'
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(unit, encoding='utf-8')
        r = run(sudo + ['install', '-m', '644', tmp, path], capture_output=not sudo, text=True)
        tmp.unlink(missing_ok=True)
        if r.returncode != 0:
            say("    не удалось записать службу (нет прав?) — делаю службу пользователя")
            return install_user_systemd()
    else:
        return install_user_systemd()
    run(ctl + ['daemon-reload'])
    r = run(ctl + ['enable', '--now', SERVICE], capture_output=True, text=True)
    if r.returncode != 0:
        say(f"    не удалось включить службу: {(r.stderr or r.stdout).strip()}")
        return False
    say(f"    служба установлена: {path}")
    say(f"    управление: sudo systemctl status|restart|stop {SERVICE}     логи: sudo journalctl -u {SERVICE} -f")
    return True


def install_user_systemd():
    path = Path.home() / '.config/systemd/user' / f'{SERVICE}.service'
    ctl = ['systemctl', '--user']
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(systemd_unit(False), encoding='utf-8')
    run(ctl + ['daemon-reload'])
    r = run(ctl + ['enable', '--now', SERVICE], capture_output=True, text=True)
    if r.returncode != 0:
        say(f"    не удалось включить службу: {(r.stderr or r.stdout).strip()}")
        return False
    user = os.environ.get('USER') or ''
    r = run(['loginctl', 'enable-linger', user], capture_output=True, text=True) if shutil.which('loginctl') else None
    if r is None or r.returncode != 0:
        say(f"    ВНИМАНИЕ: чтобы юзербот работал после выхода из SSH, выполните один раз: sudo loginctl enable-linger {user}")
    say(f"    служба установлена: {path}")
    say(f"    управление: systemctl --user status|restart|stop {SERVICE}     логи: journalctl --user -u {SERVICE} -f")
    return True


def remove_systemd():
    sudo = [] if os.geteuid() == 0 else ['sudo']
    for system, path in ((True, Path('/etc/systemd/system') / f'{SERVICE}.service'),
                         (False, Path.home() / '.config/systemd/user' / f'{SERVICE}.service')):
        if path.exists():
            ctl = (sudo + ['systemctl']) if system else ['systemctl', '--user']
            run(ctl + ['disable', '--now', SERVICE])
            r = run((sudo if system else []) + ['rm', '-f', path])
            run(ctl + ['daemon-reload'])
            if r.returncode == 0:
                say(f"    служба убрана: {path}")


def launchd_path() -> Path:
    return Path.home() / 'Library/LaunchAgents' / f'{LAUNCHD_LABEL}.plist'


def install_launchd():
    logs = keeper.DATA_DIR / 'keeper'
    logs.mkdir(parents=True, exist_ok=True)
    plist = textwrap.dedent(f"""\
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0"><dict>
          <key>Label</key><string>{LAUNCHD_LABEL}</string>
          <key>ProgramArguments</key><array><string>{VPY}</string><string>-u</string><string>frx_systemkeeper.py</string></array>
          <key>WorkingDirectory</key><string>{ROOT}</string>
          <key>RunAtLoad</key><true/>
          <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
          <key>StandardOutPath</key><string>{logs / 'console.log'}</string>
          <key>StandardErrorPath</key><string>{logs / 'console.log'}</string>
        </dict></plist>
        """)
    p = launchd_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    run(['launchctl', 'unload', p], capture_output=True)
    p.write_text(plist, encoding='utf-8')
    r = run(['launchctl', 'load', '-w', p], capture_output=True, text=True)
    if r.returncode != 0:
        say(f"    launchctl: {(r.stderr or r.stdout).strip()}")
        return False
    say(f"    автозапуск установлен: {p}\n    логи: {logs / 'console.log'}")
    return True


def windows_startup_file() -> Path:
    return Path(os.environ.get('APPDATA', '')) / 'Microsoft/Windows/Start Menu/Programs/Startup' / 'FRX.bat'


def install_windows():
    f = windows_startup_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nstart "FRX" /min "{VPY}" -u frx_systemkeeper.py\r\n', encoding='utf-8')
    say(f"    автозапуск при входе в Windows: {f}")
    return True


def setup_autostart(args):
    if in_docker():
        say("    Docker: автозапуск задаёт docker compose (restart: on-failure) — пропускаю")
        return False
    if has_systemd():
        if ask("    Запускать FRX автоматически (служба systemd: старт с системой, перезапуск при падении)?", args.yes):
            return install_systemd(args.yes)
    elif IS_MAC:
        if ask("    Запускать FRX автоматически при входе в macOS (LaunchAgent)?", args.yes):
            return install_launchd()
    elif IS_WIN:
        if ask("    Запускать FRX автоматически при входе в Windows?", args.yes):
            return install_windows()
    else:
        say("    Автозапуск для этой системы не настраиваю. Запуск в фоне: tmux new -s frx ./start.sh (выйти — Ctrl+B, D).")
    return False


# ----------------------------------------------------------------------------- 4. как открыть сайт

def guess_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('1.1.1.1', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return 'адрес-сервера'


def final_hints(service_ok):
    port = keeper.WebSentinel.port()
    user = os.environ.get('SUDO_USER') or os.environ.get('USER') or os.environ.get('USERNAME') or 'пользователь'
    start = 'start.bat' if IS_WIN else './start.sh'
    say("\nГотово.")
    if not service_ok:
        say(f"  Запуск юзербота: {start}  (или {VPY.relative_to(ROOT)} frx_systemkeeper.py)")
    say(textwrap.indent(textwrap.dedent(f"""\
          Сайт управления: напишите .web в любом чате Telegram — ссылки придут в ЛС от вашего бота.
            • на этой машине:      http://127.0.0.1:{port}
            • с другого компьютера: ssh -L {port}:127.0.0.1:{port} {user}@{guess_ip()}  → затем http://127.0.0.1:{port}
            • откуда угодно и прямо в Telegram: .web tunnel on  (публичный адрес https://…, без настройки сети)
          Вход на сайт закрыт, пока вы не напишете .web: тогда бот пришлёт 5-значный код (вход — на 10 минут, для одного устройства).
        """), '  '))


def main() -> int:
    ap = argparse.ArgumentParser(description='Установка FRX')
    ap.add_argument('--yes', action='store_true')
    ap.add_argument('--no-service', action='store_true')
    ap.add_argument('--no-login', action='store_true')
    ap.add_argument('--remove-service', action='store_true')
    args = ap.parse_args()
    os.chdir(ROOT)
    version = keeper.read_engine_version(ROOT) or '?'
    say(f"FRX {version} — установка\nПапка: {ROOT}\nPython: {sys.version.split()[0]}")
    if sys.version_info < (3, 10):
        say("Нужен Python 3.10 или новее.")
        return 1
    if args.remove_service:
        if has_systemd():
            remove_systemd()
        elif IS_MAC and launchd_path().exists():
            run(['launchctl', 'unload', launchd_path()])
            launchd_path().unlink()
            say("    LaunchAgent убран")
        elif IS_WIN and windows_startup_file().exists():
            windows_startup_file().unlink()
            say("    автозапуск убран")
        return 0

    step(1, "Окружение и зависимости")
    ensure_venv()
    install_requirements()
    keeper.migrate_legacy_layout()
    keeper._apply_layout()
    keeper.ensure_user_layout()

    step(2, "Вход в Telegram")
    if logged_in():
        say("    вход уже выполнен — пропускаю")
    elif args.no_login:
        say("    пропускаю (--no-login)")
    else:
        first_login()

    if not IS_WIN and os.geteuid() == 0 and os.environ.get('SUDO_USER'):
        # установку запустили через sudo — файлы проекта возвращаем владельцу (служба работает от его имени)
        run(['chown', '-R', f"{os.environ['SUDO_USER']}:", ROOT])
    service_ok = False
    if not args.no_service:
        step(3, "Автозапуск")
        service_ok = setup_autostart(args)
    final_hints(service_ok)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("\nУстановка прервана.")
        sys.exit(1)
