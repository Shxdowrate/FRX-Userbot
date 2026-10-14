"""
core/requirements.py — пакеты, которые модуль объявляет сам:

    REQUIREMENTS = ["ddgs", "psutil>=5.9", ("yt-dlp[default,deno]", "yt_dlp, deno"), "tzdata; sys_platform == 'win32'"]

Элемент — строка pip («пакет», «пакет>=версия», с extras и маркерами окружения после «;») или пара
(строка pip, имена для import через запятую), если имя пакета для pip и для import различаются, а угадать нельзя.
Известные пары (yt-dlp → yt_dlp, py-tgcalls → pytgcalls, Pillow → PIL, …) угадываются сами.

Где используется:
  * движок — перед загрузкой модуля ставит недостающее (`ensure_for_source`), модуль сразу может делать import;
  * Loader — показывает пакеты в превью .loadmod / .dlm;
  * frx_deps.py — при каждом запуске FRX (до старта юзербота, с прогрессом в консоли) читает REQUIREMENTS всех
    модулей из user/modules — своей копией разбора (frx_deps работает без core).
Только стандартная библиотека.
"""

import ast
import asyncio
import importlib
import importlib.util
import os
import re
import subprocess
import sys
import time

KNOWN_IMPORTS = {
    'pillow': 'PIL', 'py-tgcalls': 'pytgcalls', 'yt-dlp': 'yt_dlp', 'yandex-music': 'yandex_music',
    'zvuk-music': 'zvuk_music', 'imageio-ffmpeg': 'imageio_ffmpeg', 'beautifulsoup4': 'bs4', 'python-dateutil': 'dateutil',
    'pyyaml': 'yaml', 'opencv-python': 'cv2', 'opencv-python-headless': 'cv2', 'scikit-learn': 'sklearn',
    'audioop-lts': 'audioop', 'python-magic': 'magic', 'pycryptodome': 'Crypto', 'google-generativeai': 'google.generativeai',
    'speechrecognition': 'speech_recognition', 'pytz': 'pytz', 'qrcode': 'qrcode', 'telethon': 'telethon',
}
RETRY_FAILED_AFTER = 3600          # не повторять неудачную установку чаще раза в час (за время работы)
_failed = {}                       # строка pip -> время неудачи


def parse(source: str):
    """REQUIREMENTS из исходника без исполнения → [(строка pip, [имена import])]. Нет или неверно — []."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'REQUIREMENTS' for t in node.targets):
            try:
                value = ast.literal_eval(node.value)
            except Exception:
                return []
            return normalize(value)
    return []


def normalize(value):
    out = []
    if isinstance(value, str):
        value = [value]
    for item in value or []:
        if isinstance(item, str):
            spec, names = item.strip(), None
        elif isinstance(item, (list, tuple)) and len(item) == 2 and all(isinstance(x, str) for x in item):
            spec, names = item[0].strip(), [n.strip() for n in item[1].split(',') if n.strip()]
        else:
            continue
        if spec:
            out.append((spec, names or [import_name(spec)]))
    return out


def dist_name(spec: str) -> str:
    return re.split(r'[\[<>=!~;\s@]', spec, maxsplit=1)[0].strip()


def import_name(spec: str) -> str:
    name = dist_name(spec).lower()
    return KNOWN_IMPORTS.get(name, name.replace('-', '_'))


def strip_extras(spec: str) -> str:
    """«yt-dlp[default,deno]>=2025; маркер» → «yt-dlp>=2025; маркер»."""
    return re.sub(r'\[[^\]]*\]', '', spec, count=1)


def marker_ok(spec: str) -> bool:
    """Маркер окружения после «;» (sys_platform == 'win32', python_version >= '3.13' …) подходит этой системе?"""
    if ';' not in spec:
        return True
    marker = spec.split(';', 1)[1].strip()
    for mod in ('packaging.markers', 'pip._vendor.packaging.markers'):
        try:
            return bool(importlib.import_module(mod).Marker(marker).evaluate())
        except ImportError:
            continue
        except Exception:
            return True
    return True


def present(names) -> bool:
    for n in names:
        try:
            if importlib.util.find_spec(n) is None:
                return False
        except (ImportError, ValueError):
            return False
    return True


def missing(reqs):
    """Чего из reqs нет в окружении (с учётом маркеров) → [(строка pip, имена)]."""
    # «пакет[дополнения]»: модулю хватает самого пакета — дополнения при каждом старте докачивает frx_deps.py
    # (с повтором раз в сутки), здесь не ставим заново из-за одного недостающего дополнения
    return [(spec, names) for spec, names in reqs
            if marker_ok(spec) and not present(names[:1] if strip_extras(spec) != spec else names)]


def _refresh_paths():
    """Пакет мог встать в пользовательскую папку (pip --user: приложение для Android, PIP_USER), которой при запуске
    Python ещё не было, — тогда её нет в sys.path, и пакет виден только после перезапуска. Добавляем её сразу."""
    import site
    try:
        user = site.getusersitepackages()
        if os.path.isdir(user) and user not in sys.path:
            site.addsitedir(user)
    except Exception:
        pass
    importlib.invalidate_caches()


def _pip_install(specs):
    cmd = [sys.executable, '-m', 'pip', 'install', '--disable-pip-version-check', '--progress-bar', 'off', *specs]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace')
    return r.returncode == 0, (r.stdout + '\n' + r.stderr).strip()


async def install(reqs, log=None):
    """Поставить reqs (по одному — чтобы неудача одного не мешала другим). → (поставлено, {строка: ошибка})."""
    done, failed = [], {}
    for spec, names in reqs:
        if time.time() - _failed.get(spec, 0) < RETRY_FAILED_AFTER:
            failed[spec] = 'не встал недавно — повтор позже'
            continue
        if log:
            log(f"ставлю {spec}…")
        ok, out = await asyncio.to_thread(_pip_install, [spec])
        _refresh_paths()
        base = strip_extras(spec)
        if not (ok and present(names)) and base != spec and not present(names[:1]):
            # с дополнениями не встал (их нечем собрать на этой системе) — ставим сам пакет, без дополнений
            if log:
                log(f"{spec} целиком не встал — ставлю без дополнений: {base}")
            ok2, out2 = await asyncio.to_thread(_pip_install, [base])
            _refresh_paths()
            if ok2 and present(names[:1]):
                done.append(base + ' (без дополнений)')
                _failed[spec] = time.time()             # полную установку попробуем позже
                continue
            out = out2 or out
        if not (ok and present(names)) and base != spec and present(names[:1]):
            done.append(base + ' (без дополнений)')
            _failed[spec] = time.time()
            continue
        if ok and present(names):
            done.append(spec)
            _failed.pop(spec, None)
        else:
            _failed[spec] = time.time()
            last = [x for x in out.splitlines() if x.strip()][-1:] or ['']
            failed[spec] = last[0][:200]
    return done, failed


async def ensure_for_source(source: str, log=None):
    """Для движка: поставить недостающие пакеты модуля перед его загрузкой. → (поставлено, ошибки)."""
    need = missing(parse(source))
    if not need:
        return [], {}
    return await install(need, log=log)
