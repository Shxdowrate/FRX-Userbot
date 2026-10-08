"""
name: system_info
category: system
title: Состояние системы
description: ОС, версия Python, нагрузка, память, диск и время работы машины, на которой запущен юзербот
args:
keywords: состояние системы, загрузка процессора, сколько памяти, место на диске, аптайм, на чём запущен
"""
import os
import platform
import shutil
import sys
import time


async def run() -> str:
    out = [f"ОС: {platform.platform()}", f"Python {sys.version.split()[0]}, процессов-потоков CPU: {os.cpu_count()}"]
    try:
        l1, l5, l15 = os.getloadavg()
        out.append(f"нагрузка (1/5/15 мин): {l1:.2f} / {l5:.2f} / {l15:.2f}")
    except (AttributeError, OSError):
        pass
    try:
        import psutil
        vm = psutil.virtual_memory()
        out.append(f"память: {vm.used / 2**30:.1f} из {vm.total / 2**30:.1f} ГБ ({vm.percent}%)")
        out.append(f"аптайм машины: {(time.time() - psutil.boot_time()) / 3600:.1f} ч")
    except Exception:
        try:
            mem = {k: int(v.split()[0]) for k, v in (l.split(':', 1) for l in open('/proc/meminfo').read().splitlines()[:3])}
            out.append(f"память: свободно {mem['MemAvailable'] / 2**20:.1f} из {mem['MemTotal'] / 2**20:.1f} ГБ")
        except Exception:
            pass
    du = shutil.disk_usage('.')
    out.append(f"диск: свободно {du.free / 2**30:.1f} из {du.total / 2**30:.1f} ГБ")
    return '\n'.join(out)
