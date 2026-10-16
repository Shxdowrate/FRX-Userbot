"""
Autostart — автозапуск FRX одной командой на любой системе.

  .autostart            состояние + кнопки «включить / выключить»
  .autostart on         включить
  .autostart off        выключить

Как устроено (те же файлы, что ставит установщик frx_setup.py, поэтому `frx_setup.py --remove-service` тоже их уберёт):
  Windows   — скрипт FRX.bat в папке «Автозагрузка» (запуск при входе в Windows, свёрнутым окном);
  Linux     — служба systemd frx (от root — системная, иначе — служба пользователя + lingering);
  macOS     — LaunchAgent dev.frx.userbot (запуск при входе в систему, перезапуск при падении);
  Termux    — скрипт ~/.termux/boot/frx.sh для приложения Termux:Boot (запуск после включения телефона);
  FRX.apk   — приложение само работает службой Android, отдельно включать нечего;
  Docker    — автозапуск задаёт docker compose (restart: on-failure).

Включение не запускает вторую копию юзербота: служба и LaunchAgent только регистрируются и сработают при следующем
старте системы / входе.
"""

import asyncio
import html
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import decorators
import engine
import visual
from core.paths import PROJECT_DIR

NAME = "Autostart"
MODULE_NAME = "autostart"
VERSION = "1.0"
AUTHOR = "FRX"
DESCRIPTION = "Автозапуск FRX одной командой: Windows, Linux, macOS, Termux"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/Autostart.py"

ROOT = Path(PROJECT_DIR)
SERVICE = "frx"
LAUNCHD_LABEL = "dev.frx.userbot"
IS_WIN = os.name == "nt"
IS_MAC = sys.platform == "darwin"
IS_TERMUX = bool(os.environ.get("TERMUX_VERSION")) or "com.termux" in os.environ.get("PREFIX", "")
IS_APK = bool(os.environ.get("FRX_ANDROID"))
TERMUX_BOOT = Path.home() / ".termux" / "boot" / "frx.sh"


def _esc(x) -> str:
    return html.escape(str(x), quote=False)


def _python() -> str:
    venv = ROOT / ".venv" / ("Scripts/python.exe" if IS_WIN else "bin/python")
    return str(venv if venv.exists() else Path(sys.executable))


def _run(cmd, timeout=30):
    try:
        r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


def _in_docker() -> bool:
    return Path("/.dockerenv").exists() or os.environ.get("FRX_IN_DOCKER") == "1"


def _has_systemd() -> bool:
    return sys.platform.startswith("linux") and Path("/run/systemd/system").is_dir() and bool(shutil.which("systemctl"))


def _is_root() -> bool:
    return hasattr(os, "geteuid") and os.geteuid() == 0


# --------------------------------------------------------------------------- #
# файлы автозапуска
# --------------------------------------------------------------------------- #

def _win_file() -> Path:
    return Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs/Startup" / "FRX.bat"


def _launchd_file() -> Path:
    return Path.home() / "Library/LaunchAgents" / f"{LAUNCHD_LABEL}.plist"


def _unit_paths():
    return Path("/etc/systemd/system") / f"{SERVICE}.service", Path.home() / ".config/systemd/user" / f"{SERVICE}.service"


def _unit(system: bool) -> str:
    owner = ""
    if system:
        try:
            import pwd
            owner = pwd.getpwuid(ROOT.stat().st_uid).pw_name
        except Exception:
            owner = "root"
    lines = [
        "[Unit]", "Description=FRX Telegram userbot", "After=network-online.target", "Wants=network-online.target", "",
        "[Service]", "Type=simple", *([f"User={owner}"] if owner else []),
        f"WorkingDirectory={ROOT}", f"ExecStart={_python()} -u frx_systemkeeper.py", "Environment=PYTHONUNBUFFERED=1",
        "Restart=on-failure", "RestartSec=15", "RestartPreventExitStatus=78", "KillMode=mixed", "TimeoutStopSec=45", "",
        "[Install]", f"WantedBy={'multi-user.target' if system else 'default.target'}",
    ]
    return "\n".join(lines) + "\n"


def _platform() -> str:
    if IS_APK:
        return "apk"
    if IS_TERMUX:
        return "termux"
    if _in_docker():
        return "docker"
    if IS_WIN:
        return "windows"
    if IS_MAC:
        return "macos"
    if _has_systemd():
        return "systemd"
    return "other"


PLATFORM_NAMES = {
    "apk": "Android (FRX.apk)", "termux": "Android (Termux)", "docker": "Docker", "windows": "Windows",
    "macos": "macOS", "systemd": "Linux (systemd)", "other": sys.platform,
}


def status() -> dict:
    """{'platform', 'supported', 'enabled', 'where', 'note'}"""
    p = _platform()
    st = {"platform": p, "supported": True, "enabled": False, "where": "", "note": ""}
    if p == "apk":
        st.update(supported=False, enabled=True, note="приложение FRX само работает службой Android и запускается "
                                                     "после включения телефона — отдельно включать ничего не нужно")
    elif p == "docker":
        st.update(supported=False, note="в Docker автозапуск задаёт docker compose (restart: on-failure)")
    elif p == "other":
        st.update(supported=False, note="для этой системы автозапуск не настраивается; запуск в фоне: "
                                        "tmux new -s frx ./start.sh")
    elif p == "windows":
        f = _win_file()
        st.update(enabled=f.exists(), where=str(f))
    elif p == "macos":
        f = _launchd_file()
        st.update(enabled=f.exists(), where=str(f))
    elif p == "termux":
        st.update(enabled=TERMUX_BOOT.exists(), where=str(TERMUX_BOOT),
                  note="нужно приложение Termux:Boot (откройте его один раз) и выключенная экономия батареи для Termux")
    elif p == "systemd":
        system_unit, user_unit = _unit_paths()
        if system_unit.exists():
            code, _ = _run(["systemctl", "is-enabled", SERVICE])
            st.update(enabled=code == 0, where=str(system_unit))
        elif user_unit.exists():
            code, _ = _run(["systemctl", "--user", "is-enabled", SERVICE])
            st.update(enabled=code == 0, where=str(user_unit))
    return st


def enable() -> str:
    p = _platform()
    if p == "windows":
        f = _win_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(f'@echo off\r\ncd /d "{ROOT}"\r\nstart "FRX" /min "{_python()}" -u frx_systemkeeper.py\r\n',
                     encoding="utf-8")
        return f"FRX запустится сам при входе в Windows (свёрнутым окном): {f}"
    if p == "macos":
        logs = ROOT / "user" / "data" / "keeper"
        logs.mkdir(parents=True, exist_ok=True)
        f = _launchd_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
            <plist version="1.0"><dict>
              <key>Label</key><string>{LAUNCHD_LABEL}</string>
              <key>ProgramArguments</key><array><string>{_python()}</string><string>-u</string><string>frx_systemkeeper.py</string></array>
              <key>WorkingDirectory</key><string>{ROOT}</string>
              <key>RunAtLoad</key><true/>
              <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
              <key>StandardOutPath</key><string>{logs / 'console.log'}</string>
              <key>StandardErrorPath</key><string>{logs / 'console.log'}</string>
            </dict></plist>
            """), encoding="utf-8")
        # launchctl load не вызываем: RunAtLoad запустил бы вторую копию прямо сейчас. Агент подхватится при входе.
        return f"FRX запустится сам при следующем входе в macOS: {f}"
    if p == "termux":
        log = ROOT / "user" / "data" / "termux_boot.log"
        TERMUX_BOOT.parent.mkdir(parents=True, exist_ok=True)
        TERMUX_BOOT.write_text(textwrap.dedent(f"""\
            #!/data/data/com.termux/files/usr/bin/sh
            # FRX — автозапуск после включения телефона. Нужно приложение Termux:Boot: откройте его один раз.
            termux-wake-lock
            export ANDROID_API_LEVEL="$(getprop ro.build.version.sdk 2>/dev/null || echo 24)"
            export PIP_FIND_LINKS="https://github.com/Shxdowrate/FRX-Userbot-APK/releases/expanded_assets/wheels" PIP_PREFER_BINARY=1
            cd "{ROOT}" || exit 1
            while true; do
              .venv/bin/python -u frx_systemkeeper.py >> "{log}" 2>&1
              code=$?
              if [ "$code" -eq 0 ] || [ "$code" -eq 78 ]; then break; fi
              sleep 15
            done
            termux-wake-unlock
            """), encoding="utf-8")
        TERMUX_BOOT.chmod(0o755)
        return (f"FRX запустится сам после включения телефона: {TERMUX_BOOT}\n"
                "Поставьте приложение Termux:Boot (F-Droid / GitHub), откройте его один раз и выключите экономию "
                "батареи для Termux и Termux:Boot.")
    if p == "systemd":
        system_unit, user_unit = _unit_paths()
        if _is_root():
            system_unit.write_text(_unit(True), encoding="utf-8")
            _run(["systemctl", "daemon-reload"])
            code, out = _run(["systemctl", "enable", SERVICE])
            if code != 0:
                raise RuntimeError(f"systemctl enable: {out.strip()}")
            return (f"Служба {SERVICE} включена: FRX запустится сам при старте сервера и перезапустится при падении.\n"
                    f"Файл: {system_unit}\nЛоги: journalctl -u {SERVICE} -f")
        user_unit.parent.mkdir(parents=True, exist_ok=True)
        user_unit.write_text(_unit(False), encoding="utf-8")
        _run(["systemctl", "--user", "daemon-reload"])
        code, out = _run(["systemctl", "--user", "enable", SERVICE])
        if code != 0:
            raise RuntimeError(f"systemctl --user enable: {out.strip()}")
        user = os.environ.get("USER") or ""
        linger_ok = bool(shutil.which("loginctl")) and _run(["loginctl", "enable-linger", user])[0] == 0
        msg = (f"Служба пользователя {SERVICE} включена: FRX запустится сам при старте системы.\n"
               f"Файл: {user_unit}\nЛоги: journalctl --user -u {SERVICE} -f")
        if not linger_ok:
            msg += (f"\nЧтобы служба стартовала без входа в систему (после перезагрузки сервера), выполните один раз: "
                    f"sudo loginctl enable-linger {user}")
        return msg
    raise RuntimeError(status()["note"] or "автозапуск на этой системе не поддерживается")


def disable() -> str:
    p = _platform()
    if p == "windows":
        _win_file().unlink(missing_ok=True)
        return "Автозапуск при входе в Windows убран"
    if p == "macos":
        f = _launchd_file()
        if f.exists():
            _run(["launchctl", "unload", "-w", f])   # если агент был загружен — выгрузится вместе с юзерботом под ним
            f.unlink(missing_ok=True)
        return "LaunchAgent убран: FRX больше не запускается при входе в macOS"
    if p == "termux":
        TERMUX_BOOT.unlink(missing_ok=True)
        return "Автозапуск Termux:Boot убран"
    if p == "systemd":
        system_unit, user_unit = _unit_paths()
        done = []
        if system_unit.exists():
            if not _is_root():
                raise RuntimeError(f"служба системная — уберите её с правами root: sudo systemctl disable {SERVICE} "
                                   f"&& sudo rm {system_unit}")
            _run(["systemctl", "disable", SERVICE])       # без --now: работающий юзербот не останавливаем
            system_unit.unlink(missing_ok=True)
            _run(["systemctl", "daemon-reload"])
            done.append(str(system_unit))
        if user_unit.exists():
            _run(["systemctl", "--user", "disable", SERVICE])
            user_unit.unlink(missing_ok=True)
            _run(["systemctl", "--user", "daemon-reload"])
            done.append(str(user_unit))
        return "Служба убрана" + (f": {', '.join(done)}" if done else " (её и не было)") + \
            ". Юзербот продолжает работать до остановки."
    raise RuntimeError(status()["note"] or "автозапуск на этой системе не поддерживается")


# --------------------------------------------------------------------------- #
# команда
# --------------------------------------------------------------------------- #

def _text(st: dict, result: str = "", error: str = "") -> str:
    lines = [visual.title("Автозапуск"), visual.field("система", PLATFORM_NAMES.get(st["platform"], st["platform"]))]
    if st["supported"] or st["enabled"]:
        lines.append(visual.field("состояние", "включён" if st["enabled"] else "выключен"))
    if st["where"] and st["enabled"]:
        lines.append(visual.field("файл", _esc(st["where"])))
    if result:
        lines += ["", visual.ok("Готово"), _esc(result)]
    if error:
        lines += ["", visual.err("Не получилось"), f"<code>{_esc(error)}</code>"]
    if st["note"] and not result and not error:
        lines += ["", visual.hint(_esc(st["note"]))]
    return "\n".join(lines)


def _buttons(st: dict):
    if not st["supported"]:
        return None
    if st["enabled"]:
        return [[(visual.btn("Выключить", "off"), "autostart_off", "danger")]]
    return [[(visual.btn("Включить", "on"), "autostart_on", "success")]]


async def _apply(action: str):
    st = await asyncio.to_thread(status)
    try:
        result = await asyncio.to_thread(enable if action == "on" else disable)
        return await asyncio.to_thread(status), result, ""
    except Exception as e:
        return st, "", str(e)


@decorators.command("autostart", "autorun")
async def autostart_cmd(event):
    """[on|off] — автозапуск FRX вместе с системой (Windows, Linux, macOS, Termux)"""
    arg = engine.get_args().strip().lower()
    if arg in ("on", "вкл", "1", "yes", "да", "enable"):
        st, result, error = await _apply("on")
        await engine.answer(event, _text(st, result, error))
        return
    if arg in ("off", "выкл", "0", "no", "нет", "disable"):
        st, result, error = await _apply("off")
        await engine.answer(event, _text(st, result, error))
        return

    st = await asyncio.to_thread(status)
    buttons = _buttons(st)
    if not buttons:
        await engine.answer(event, _text(st))
        return
    try:
        form = await engine.create_inlineform(event.chat_id, _text(st), buttons=buttons)
    except Exception:
        prefix = await engine.get_prefix()
        await engine.answer(event, _text(st) + "\n\n" + visual.hint(f"{prefix}autostart on / {prefix}autostart off"))
        return

    @form.on_click(data_startswith="autostart_")
    async def _(click):
        st2, result, error = await _apply("on" if click.data == "autostart_on" else "off")
        await form.edit(_text(st2, result, error), buttons=_buttons(st2))
        await click.answer()
