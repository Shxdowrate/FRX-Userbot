"""
frx_deps.py — всё, что FRX и его модулям нужно скачать, ставится само.

Запускается:
  * SystemKeeper'ом при каждом старте FRX (start.bat / start.sh / автозапуск / служба / Docker) — режим --auto:
    если всё уже на месте, занимает доли секунды (сверяет отметку user/data/deps.json), иначе докачивает недостающее;
  * установщиком (frx_setup.py) — с вопросами (Ollama, модель);
  * после .update, если поменялись requirements*.txt;
  * вручную: .venv/Scripts/python frx_deps.py --force  (Linux/macOS: .venv/bin/python frx_deps.py --force).

Что ставится:
  1. requirements.txt          — ядро (telethon, aiogram, aiohttp, Pillow…). Не встало — FRX не запустится, будет понятная ошибка.
  2. REQUIREMENTS модулей      — каждый модуль из user/modules объявляет свои пакеты сам (REQUIREMENTS = [...],
                                 см. core/requirements.py); недостающие ставятся здесь, до старта юзербота.
     requirements-modules.txt  — общий список пакетов для модулей (необязателен). По одному: что не встало
                                 на этой системе — пропускается (повторная попытка — раз в сутки), остальное работает.
  3. ffmpeg + ffprobe          — если их нет в системе: скачиваются в user/data/bin/ffmpeg (Windows, Linux x64/arm64;
                                 macOS — через Homebrew). Нужны общему медиакоду (`import media`) и модулям.
  4. Ollama (по желанию)       — локальные нейросети без ключей: программа + модель. Спрашивается при установке
                                 (или --ollama yes|no); выбор запоминается в user/data/deps.json.

Только стандартная библиотека Python.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

IS_WIN = os.name == 'nt'
IS_MAC = sys.platform == 'darwin'
IS_ANDROID = sys.platform == 'android' or bool(os.environ.get('FRX_ANDROID'))   # приложение FRX для Android
IN_DOCKER = bool(os.environ.get('FRX_IN_DOCKER')) or Path('/.dockerenv').exists()

CORE_REQ = ROOT / 'requirements.txt'
MOD_REQ = ROOT / 'requirements-modules.txt'
RETRY_FAILED_AFTER = 86400

FFMPEG_URLS = {
    ('win', 'x64'): ['https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip',
                     'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'],
    ('linux', 'x64'): ['https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linux64-gpl.tar.xz'],
    ('linux', 'arm64'): ['https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-linuxarm64-gpl.tar.xz'],
}
OLLAMA_WIN = 'https://ollama.com/download/OllamaSetup.exe'
OLLAMA_MAC = 'https://ollama.com/download/Ollama-darwin.zip'
OLLAMA_LINUX = 'https://ollama.com/install.sh'
OLLAMA_API = 'http://127.0.0.1:11434'
MODELS = [('qwen2.5:3b', '≈2 ГБ, быстрая, хватает для пересказов и простых вопросов'),
          ('qwen2.5:7b', '≈4.7 ГБ, заметно умнее, нужно 8+ ГБ памяти'),
          ('gemma3:4b', '≈3.3 ГБ, хорошо говорит по-русски')]


def say(text=''):
    print(f"[FRX deps] {text}" if text else '', flush=True)


# ----------------------------------------------------------------------------- пути и отметка

def data_dir() -> Path:
    try:
        import frx_systemkeeper as keeper
        return Path(keeper.DATA_DIR)
    except Exception:
        return ROOT / 'user' / 'data'


def bin_dir() -> Path:
    return data_dir() / 'bin'


def stamp_path() -> Path:
    return data_dir() / 'deps.json'


def read_stamp() -> dict:
    try:
        return json.loads(stamp_path().read_text(encoding='utf-8'))
    except Exception:
        return {}


def write_stamp(st: dict) -> None:
    p = stamp_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding='utf-8')
    os.replace(tmp, p)


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16] if p.is_file() else ''


def env_id() -> str:
    """Версия Python + путь к нему: новое окружение (.venv пересоздали, другой Python) — ставим всё заново."""
    return f"{sys.version_info.major}.{sys.version_info.minor}|{Path(sys.prefix).resolve()}"


# ----------------------------------------------------------------------------- pip

def requirement_lines(p: Path) -> list:
    out = []
    if p.is_file():
        for ln in p.read_text(encoding='utf-8').splitlines():
            ln = ln.split(' #', 1)[0].split('\t#', 1)[0].strip()
            if ln and not ln.startswith('#'):
                out.append(ln)
    return out


def pip(*args, quiet=False) -> tuple:
    cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', *args]
    if quiet:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
        return r.returncode == 0, (r.stdout + '\n' + r.stderr).strip()
    r = subprocess.run(cmd)
    return r.returncode == 0, ''


# ----------------------------------------------------------------------------- прогресс

SPIN = '|/-\\'                       # только символы, которые есть в любом шрифте консоли (в т. ч. Windows)


class Progress:
    """Строка прогресса: в консоли — перерисовывается на месте, в логе (не терминал) — строкой раз в несколько секунд."""

    def __init__(self, total: int):
        self.total = max(1, total)
        self.tty = sys.stdout.isatty()
        self.i = 0
        self.last_print = 0.0
        self.width = 0

    def bar(self, done: int) -> str:
        k = int(round(12 * done / self.total))
        return '[' + '█' * k + '░' * (12 - k) + ']'

    def show(self, done: int, name: str, stage: str, started: float, force=False):
        self.i += 1
        el = int(time.time() - started)
        text = f"[FRX deps] {self.bar(done)} {done}/{self.total} - {name}: {stage} - {el // 60}:{el % 60:02}"
        if self.tty:
            line = f"{SPIN[self.i % len(SPIN)]} {text}"
            pad = max(0, self.width - len(line))
            self.width = len(line)
            print('\r' + line + ' ' * pad, end='', flush=True)
        elif force or time.time() - self.last_print > 5:
            self.last_print = time.time()
            print(text, flush=True)

    def done_line(self, text: str):
        if self.tty:
            print('\r' + ' ' * self.width + '\r', end='', flush=True)
            self.width = 0
        say(text)


def _size(line: str) -> str:
    import re
    m = re.search(r'\(([\d.,]+\s*[kKMG]i?B)\)', line)
    return f" ({m.group(1)})" if m else ''


def pip_live(req: str, prog: Progress, done: int) -> tuple:
    """pip install одного пакета с живой строкой: что сейчас делает pip (скачивает / собирает / ставит)."""
    import re
    import threading
    name = re.split(r'[\[<>=!~;\s]', req, maxsplit=1)[0]
    cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--progress-bar', 'off', req]
    started = time.time()
    state = {'stage': 'проверяю', 'out': []}
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                            errors='replace', bufsize=1)

    def reader():
        for line in proc.stdout:
            line = line.rstrip()
            state['out'].append(line)
            low = line.lower().strip()
            if low.startswith('collecting '):
                state['stage'] = 'смотрю ' + line.split()[1][:40]
            elif low.startswith('downloading '):
                state['stage'] = 'скачиваю ' + line.split()[1].rsplit('/', 1)[-1][:40] + _size(line)
            elif low.startswith('building wheel') or 'building wheels' in low:
                state['stage'] = 'собираю (может занять пару минут)'
            elif low.startswith('installing collected packages'):
                state['stage'] = 'устанавливаю ' + line.split(':', 1)[-1].strip()[:50]
    t = threading.Thread(target=reader, daemon=True)
    t.start()
    while proc.poll() is None:
        prog.show(done, name, state['stage'], started)
        time.sleep(0.25)
    t.join(timeout=5)
    return proc.returncode == 0, '\n'.join(state['out']), int(time.time() - started)


def ensure_pip():
    try:
        import pip  # noqa: F401
    except ImportError:
        subprocess.run([sys.executable, '-m', 'ensurepip', '--upgrade'], capture_output=True)


IMPORT_NAMES = {'pillow': 'PIL', 'imageio-ffmpeg': 'imageio_ffmpeg', 'py-tgcalls': 'pytgcalls', 'yt-dlp': 'yt_dlp',
                'yandex-music': 'yandex_music', 'zvuk-music': 'zvuk_music'}


def import_name(req: str) -> str:
    import re
    name = re.split(r'[\[<>=!~;\s]', req, maxsplit=1)[0].strip().lower()
    return IMPORT_NAMES.get(name, name.replace('-', '_'))


def missing(reqs: list) -> list:
    """Каких пакетов на самом деле нет (отметка могла остаться от другого окружения — например, пересобранный Docker)."""
    import importlib.util
    out = []
    for r in reqs:
        if not marker_ok(r):                     # «пакет; sys_platform != 'android'» — этой системе не нужен
            continue
        try:
            if importlib.util.find_spec(import_name(r)) is None or ('deno' in r.split('#')[0] and '[' in r
                                                                    and importlib.util.find_spec('deno') is None):
                out.append(r)
        except (ImportError, ValueError):
            out.append(r)
    return out


def install_core(force: bool, st: dict) -> bool:
    want = sha(CORE_REQ)
    if not force and st.get('core') == want and st.get('env') == env_id() and not missing(requirement_lines(CORE_REQ)):
        return True
    say("ставлю основные пакеты (requirements.txt)…")
    ensure_pip()
    ok, _ = pip('-q', '-r', str(CORE_REQ))
    if not ok:
        say("❌ основные пакеты не поставились (см. выше). Частые причины: нет интернета; на Linux — нет компилятора: "
            "sudo apt install build-essential python3-dev")
        return False
    st['core'] = want
    return True


def install_modules(force: bool, st: dict) -> None:
    want = sha(MOD_REQ)
    failed = st.get('modules_failed') or {}
    retry = [p for p, t in failed.items() if time.time() - t > RETRY_FAILED_AFTER]
    pkgs = requirement_lines(MOD_REQ)
    gone = [p for p in missing(pkgs) if p not in failed or p in retry]
    if not force and st.get('modules') == want and st.get('env') == env_id() and not gone:
        return
    if not pkgs:
        return
    # ставим только то, чего нет: новые строки в файле и пакеты, которых нет в окружении (остальные уже стоят)
    known = set(st.get('modules_lines') or [])
    fresh_env = st.get('env') != env_id()
    if force or fresh_env:
        todo = pkgs
    elif 'modules_lines' not in st:                          # старая отметка без списка — ставим только недостающие
        todo = gone
    else:
        todo = [p for p in pkgs if p not in known or p in gone]
    todo = [p for p in todo if p not in failed or p in retry or force]
    if not todo:
        st['modules'], st['modules_lines'] = want, pkgs
        return
    names = [p.split('[')[0].split('>')[0].split('=')[0].split(' ')[0] for p in todo]
    say(f"ставлю пакеты модулей ({len(todo)} из {len(pkgs)}): " + ", ".join(names))
    prog = Progress(len(todo))
    failed = {k: v for k, v in failed.items() if k not in todo}
    for i, p in enumerate(todo):
        ok1, out1, sec = pip_live(p, prog, i)
        if ok1:
            prog.done_line(f"  √ {names[i]} · {sec} с")
        else:
            failed[p] = time.time()
            last = [x for x in out1.splitlines() if x.strip()][-1:] or ['']
            prog.done_line(f"  × {names[i]} — не встал на этой системе ({last[0][:160]}); модули без него скажут об этом сами")
    ok_n = len(todo) - len([p for p in todo if p in failed])
    say(f"  {prog.bar(len(todo))} готово: {ok_n} из {len(todo)}")
    st['modules'] = want
    st['modules_lines'] = pkgs
    st['modules_failed'] = failed


# ----------------------------------------------------------------------------- REQUIREMENTS модулей

def modules_dir() -> Path:
    return data_dir().parent / 'modules'


def module_requirements() -> dict:
    """{строка pip: [имена import]} из REQUIREMENTS всех модулей в user/modules (разбор без исполнения кода)."""
    import ast
    import re
    known = {'pillow': 'PIL', 'py-tgcalls': 'pytgcalls', 'yt-dlp': 'yt_dlp', 'yandex-music': 'yandex_music',
             'zvuk-music': 'zvuk_music', 'imageio-ffmpeg': 'imageio_ffmpeg', 'beautifulsoup4': 'bs4', 'pyyaml': 'yaml',
             'python-dateutil': 'dateutil', 'opencv-python': 'cv2', 'scikit-learn': 'sklearn', 'audioop-lts': 'audioop',
             'speechrecognition': 'speech_recognition', 'pycryptodome': 'Crypto'}
    out = {}
    d = modules_dir()
    if not d.is_dir():
        return out
    for f in sorted(d.glob('*.py')):
        try:
            src = f.read_text(encoding='utf-8')
            if 'REQUIREMENTS' not in src:
                continue
            tree = ast.parse(src)
        except Exception:
            continue
        for node in tree.body:
            if not (isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'REQUIREMENTS' for t in node.targets)):
                continue
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                break
            for item in ([value] if isinstance(value, str) else value or []):
                if isinstance(item, str):
                    spec, names = item.strip(), None
                elif isinstance(item, (list, tuple)) and len(item) == 2:
                    spec, names = str(item[0]).strip(), [n.strip() for n in str(item[1]).split(',') if n.strip()]
                else:
                    continue
                if not spec:
                    continue
                if not names:
                    name = re.split(r'[\[<>=!~;\s@]', spec, maxsplit=1)[0].strip().lower()
                    names = [known.get(name, name.replace('-', '_'))]
                out.setdefault(spec, names)
            break
    return out


def marker_ok(spec: str) -> bool:
    if ';' not in spec:
        return True
    try:
        from pip._vendor.packaging.markers import Marker
        return bool(Marker(spec.split(';', 1)[1].strip()).evaluate())
    except Exception:
        return True


def install_module_requirements(force: bool, st: dict) -> None:
    import importlib.util
    reqs = module_requirements()
    failed = st.get('modreq_failed') or {}

    def present(names):
        try:
            return all(importlib.util.find_spec(n) is not None for n in names)
        except (ImportError, ValueError):
            return False
    todo = [s for s, n in reqs.items() if marker_ok(s) and (force or not present(n))
            and (s not in failed or time.time() - failed[s] > RETRY_FAILED_AFTER or force)]
    if not todo:
        return
    names = [s.split(';')[0].strip() for s in todo]
    say(f"ставлю пакеты, которые просят модули ({len(todo)}): " + ", ".join(names))
    prog = Progress(len(todo))
    for i, spec in enumerate(todo):
        ok1, out1, sec = pip_live(spec, prog, i)
        if ok1:
            failed.pop(spec, None)
            prog.done_line(f"  √ {names[i]} · {sec} с")
        else:
            failed[spec] = time.time()
            last = [x for x in out1.splitlines() if x.strip()][-1:] or ['']
            prog.done_line(f"  × {names[i]} — не встал ({last[0][:160]}); модуль скажет об этом сам")
    say(f"  {prog.bar(len(todo))} готово: {len(todo) - len([s for s in todo if s in failed])} из {len(todo)}")
    st['modreq_failed'] = failed


# ----------------------------------------------------------------------------- ffmpeg

def arch() -> str:
    m = platform.machine().lower()
    return 'arm64' if m in ('arm64', 'aarch64', 'armv8l') else 'x64' if m in ('x86_64', 'amd64', 'x64') else m


def exe(name: str) -> str:
    return name + ('.exe' if IS_WIN else '')


def ffmpeg_ready() -> bool:
    own = bin_dir() / 'ffmpeg'
    if (own / exe('ffmpeg')).is_file() and (own / exe('ffprobe')).is_file():
        return True
    return bool(shutil.which('ffmpeg') and shutil.which('ffprobe'))


def download(url: str, dest: Path, label: str) -> None:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0 FRX'})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, 'wb') as f:
        total = int(r.headers.get('Content-Length') or 0)
        done, last = 0, 0.0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            if time.time() - last > 3:
                last = time.time()
                pct = f" {done * 100 // total}%" if total else ''
                say(f"  {label}: {done // (1 << 20)} МБ{(' из ' + str(total // (1 << 20)) + ' МБ') if total else ''}{pct}")


def install_ffmpeg() -> None:
    if ffmpeg_ready() or IN_DOCKER or IS_ANDROID:     # на Android ffmpeg (если есть) приносит само приложение
        return
    if IS_MAC:
        if shutil.which('brew'):
            say("ставлю ffmpeg через Homebrew…")
            subprocess.run(['brew', 'install', 'ffmpeg'])
        else:
            say("⚠ нет ffmpeg: поставьте Homebrew (https://brew.sh) и выполните brew install ffmpeg")
        return
    key = ('win' if IS_WIN else 'linux', arch())
    urls = FFMPEG_URLS.get(key)
    if not urls:
        say(f"⚠ нет готового ffmpeg для {key[0]}/{key[1]} — поставьте его системой (apt install ffmpeg)")
        return
    own = bin_dir() / 'ffmpeg'
    own.mkdir(parents=True, exist_ok=True)
    (bin_dir() / '.nobackup').touch()
    names = {exe('ffmpeg'), exe('ffprobe')}
    for url in urls:
        archive = own / ('dl.zip' if url.endswith('.zip') else 'dl.tar.xz')
        try:
            say("скачиваю ffmpeg (≈150 МБ, один раз)…")
            download(url, archive, 'ffmpeg')
            if archive.suffix == '.zip':
                with zipfile.ZipFile(archive) as z:
                    for info in z.infolist():
                        base = os.path.basename(info.filename)
                        if base in names:
                            with z.open(info) as src, open(own / base, 'wb') as out:
                                shutil.copyfileobj(src, out)
            else:
                with tarfile.open(archive) as t:
                    for m in t.getmembers():
                        base = os.path.basename(m.name)
                        if base in names and m.isfile():
                            with t.extractfile(m) as src, open(own / base, 'wb') as out:
                                shutil.copyfileobj(src, out)
                            os.chmod(own / base, 0o755)
            archive.unlink(missing_ok=True)
            if ffmpeg_ready():
                say("  ✓ ffmpeg готов")
                return
        except Exception as e:
            say(f"  не вышло с {url.split('/')[2]}: {e}")
            archive.unlink(missing_ok=True)
    say("⚠ ffmpeg не скачался — модули попробуют ещё раз сами; или поставьте вручную (winget install ffmpeg / apt install ffmpeg)")


# ----------------------------------------------------------------------------- Ollama

def ollama_bin() -> str | None:
    found = shutil.which('ollama')
    if found:
        return found
    cands = []
    if IS_WIN:
        cands.append(Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'Ollama' / 'ollama.exe')
    elif IS_MAC:
        cands += [Path('/Applications/Ollama.app/Contents/Resources/ollama'),
                  Path.home() / 'Applications/Ollama.app/Contents/Resources/ollama', Path('/opt/homebrew/bin/ollama'),
                  Path('/usr/local/bin/ollama')]
    else:
        cands += [Path('/usr/local/bin/ollama'), Path('/usr/bin/ollama')]
    return next((str(p) for p in cands if p.is_file()), None)


def ollama_up() -> bool:
    try:
        with urllib.request.urlopen(OLLAMA_API + '/api/version', timeout=3) as r:
            return r.status == 200
    except Exception:
        return False


def ollama_models() -> list:
    try:
        with urllib.request.urlopen(OLLAMA_API + '/api/tags', timeout=5) as r:
            return [m.get('name', '') for m in json.loads(r.read()).get('models', [])]
    except Exception:
        return []


def install_ollama_app(interactive: bool) -> bool:
    if ollama_bin():
        return True
    tmp = data_dir() / 'cache'
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        if IS_WIN:
            setup = tmp / 'OllamaSetup.exe'
            say("скачиваю Ollama (≈1 ГБ, один раз)…")
            download(OLLAMA_WIN, setup, 'Ollama')
            say("ставлю Ollama (тихо, без окон)…")
            subprocess.run([str(setup), '/VERYSILENT', '/NORESTART', '/SUPPRESSMSGBOXES'], timeout=1800)
            setup.unlink(missing_ok=True)
        elif IS_MAC:
            if shutil.which('brew'):
                subprocess.run(['brew', 'install', 'ollama'])
            else:
                z = tmp / 'Ollama-darwin.zip'
                say("скачиваю Ollama…")
                download(OLLAMA_MAC, z, 'Ollama')
                dest = Path.home() / 'Applications'
                dest.mkdir(exist_ok=True)
                subprocess.run(['ditto', '-x', '-k', str(z), str(dest)])
                z.unlink(missing_ok=True)
        else:
            root = hasattr(os, 'geteuid') and os.geteuid() == 0
            if not root and not interactive:
                say("⚠ Ollama на Linux ставится с правами администратора — запустите: curl -fsSL https://ollama.com/install.sh | sh")
                return False
            say("ставлю Ollama официальным скриптом (может спросить пароль sudo)…")
            subprocess.run(['sh', '-c', f'curl -fsSL {OLLAMA_LINUX} | sh'])
    except Exception as e:
        say(f"⚠ Ollama не поставилась: {e}")
        return False
    ok = bool(ollama_bin())
    say("  ✓ Ollama установлена" if ok else "⚠ Ollama не нашлась после установки — поставьте с https://ollama.com/download")
    return ok


def start_ollama() -> bool:
    if ollama_up():
        return True
    b = ollama_bin()
    if not b:
        return False
    kw = {'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'stdin': subprocess.DEVNULL}
    if IS_WIN:
        kw['creationflags'] = 0x00000008 | 0x00000200          # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    else:
        kw['start_new_session'] = True
    try:
        subprocess.Popen([b, 'serve'], **kw)
    except Exception:
        return False
    for _ in range(30):
        time.sleep(1)
        if ollama_up():
            return True
    return False


def pull_model(name: str) -> bool:
    if any(m == name or m.split(':')[0] == name for m in ollama_models()):
        return True
    say(f"скачиваю модель {name} (один раз)…")
    req = urllib.request.Request(OLLAMA_API + '/api/pull', data=json.dumps({'name': name}).encode(),
                                 headers={'Content-Type': 'application/json'})
    last, status = 0.0, ''
    try:
        with urllib.request.urlopen(req, timeout=3600) as r:
            for line in r:
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get('error'):
                    say(f"⚠ {ev['error']}")
                    return False
                status = ev.get('status', status)
                if ev.get('total') and time.time() - last > 3:
                    last = time.time()
                    say(f"  {name}: {ev.get('completed', 0) * 100 // ev['total']}% из {ev['total'] // (1 << 20)} МБ")
    except Exception as e:
        say(f"⚠ модель не скачалась: {e}")
        return False
    ok = status == 'success' or any(m.startswith(name.split(':')[0]) for m in ollama_models())
    say(f"  ✓ модель {name} готова — выберите её на сайте (.web → neuro)" if ok else f"⚠ модель {name} не скачалась")
    return ok


def _input(prompt: str, timeout=None) -> str | None:
    """input() с таймаутом (None — ждать сколько угодно). Нет ответа — None."""
    if timeout is None:
        try:
            return input(prompt)
        except EOFError:
            return None
    import threading
    box = []

    def read():
        try:
            box.append(input(prompt))
        except Exception:
            pass
    t = threading.Thread(target=read, daemon=True)
    t.start()
    t.join(timeout)
    return box[0] if box else None


def ask_ollama(st: dict, timeout=None) -> None:
    """Вопрос (один раз): ставить ли Ollama и какую модель. timeout — при обычном запуске не ждём вечно."""
    print()
    print("Локальная нейросеть (Ollama): работает на этом компьютере, без ключей и без интернета —")
    print("для .neuro, пересказов (.sum), помощника в WebIDE. Нужно место: программа ≈1 ГБ + модель 2–5 ГБ.")
    print("  0 — не ставить (можно подключить облачные нейросети ключом на сайте)")
    for i, (m, d) in enumerate(MODELS, 1):
        print(f"  {i} — поставить с моделью {m} ({d})")
    hint = f" (через {timeout} с — 0)" if timeout else ''
    ans = _input(f"Ваш выбор [0]{hint}: ", timeout)
    if ans is None and timeout:
        print()
        say("ответа нет — не ставлю; включить потом: frx_deps.py --ollama yes")
    ans = (ans or '0').strip() or '0'
    if ans.isdigit() and 1 <= int(ans) <= len(MODELS):
        st['ollama'], st['ollama_model'] = True, MODELS[int(ans) - 1][0]
    else:
        st['ollama'] = False


def install_ollama(st: dict, interactive: bool) -> None:
    if not st.get('ollama') or IN_DOCKER or IS_ANDROID:
        return
    if st.get('ollama_done') and ollama_bin():
        return
    if not install_ollama_app(interactive):
        return
    if not start_ollama():
        say("⚠ Ollama установлена, но не запустилась — откройте её вручную, модель скачается при следующем старте FRX")
        return
    if pull_model(st.get('ollama_model') or MODELS[0][0]):
        st['ollama_done'] = True


# ----------------------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description='Зависимости FRX')
    ap.add_argument('--auto', action='store_true', help='тихий режим при старте FRX: только недостающее, без вопросов')
    ap.add_argument('--force', action='store_true', help='переставить пакеты, даже если отметка совпадает')
    ap.add_argument('--ask', action='store_true', help='задать вопросы (Ollama), если ещё не задавали')
    ap.add_argument('--ollama', choices=['yes', 'no'], help='ставить ли Ollama (запоминается)')
    ap.add_argument('--model', help='модель Ollama, например qwen2.5:3b')
    ap.add_argument('--no-tools', action='store_true', help='только pip-пакеты (без ffmpeg и Ollama)')
    args = ap.parse_args()
    os.chdir(ROOT)
    st = read_stamp()
    if args.ollama:
        st['ollama'] = args.ollama == 'yes'
        st.pop('ollama_done', None)
    if args.model:
        st['ollama_model'] = args.model
        st.pop('ollama_done', None)
    if 'ollama' not in st and not IN_DOCKER and sys.stdin and sys.stdin.isatty() and (args.ask or args.auto):
        ask_ollama(st, None if args.ask else 45)       # при обычном запуске — один раз и не дольше 45 с

    if not install_core(args.force, st):
        write_stamp(st)
        return 1
    install_modules(args.force, st)
    install_module_requirements(args.force, st)
    st['env'] = env_id()
    write_stamp(st)
    if not args.no_tools:
        install_ffmpeg()
        install_ollama(st, interactive=not args.auto)
    st['checked'] = time.time()
    write_stamp(st)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        say("прервано")
        sys.exit(1)
