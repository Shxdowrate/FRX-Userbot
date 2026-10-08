"""
core/logs.py

Логи юзербота и стационарного бота с меткой времени.

  install()  — оборачивает sys.stdout / sys.stderr: каждая строка в терминале получает префикс «мм.дд чч:мм:сс»
               (как в журналах Sandbox), а сама строка попадает в кольцевой буфер BUFFER (последние MAX_LINES записей) —
               его читает вкладка «логи» на сайте. Ничего не нужно менять в модулях: их `print` и `engine.tlog` попадают сюда сами.
  classify() — источник: «бот» (aiogram, стационарный бот, BotChat, bot.*-хэндлеры) или «юзербот»; уровень: info / warn / error.
  stamp()    — «мм.дд чч:мм:сс» (для кода, который пишет в файлы, например журнал keeper'а).

Строки трассировки (с отступом, «Traceback…», «File …») присоединяются к предыдущей записи — ошибка видна на сайте целиком.
Тёмная сторона любых перехватчиков вывода — ошибки внутри самого перехватчика: поэтому write() обёрнут в try/except и при любом сбое
отдаёт текст в исходный поток без изменений.
"""

import re
import sys
import threading
import time
from collections import deque

MAX_LINES = 3000
MAX_LINE_LEN = 4000

_BOT_RE = re.compile(r'aiogram|стационарн|botchat|bot\.(cycle|command|watcher|on_|telegram|inline)|bot_|\bбот|\bBot API|AIOGRAM|AIOgram', re.I)
_ERR_RE = re.compile(r'Traceback|Exception|Error\b|ошибк|не удалось|\[ERROR\]|\bERROR\b|critical|CRITICAL', re.I)
_WARN_RE = re.compile(r'\[WARN\]|\bWARN(ING)?\b|предупрежд', re.I)
_CONT_RE = re.compile(r'^(\s+|Traceback \(most recent|During handling|The above exception|\S*(Error|Exception)\b.*:)')


def stamp(ts=None) -> str:
    return time.strftime('%m.%d %H:%M:%S', time.localtime(ts if ts is not None else time.time()))


def classify(text: str):
    """(источник, уровень) по тексту строки."""
    kind = 'bot' if _BOT_RE.search(text) else 'userbot'
    if '[WARN]' in text:
        level = 'warn'
    else:
        level = 'error' if _ERR_RE.search(text) else 'warn' if _WARN_RE.search(text) else 'info'
    return kind, level


class LogBuffer:
    def __init__(self, cap=MAX_LINES):
        self._lock = threading.RLock()
        self._items = deque(maxlen=cap)
        self._seq = 0

    def add(self, text, kind=None, level=None, ts=None, cont=False):
        text = text.rstrip('\r\n')
        if len(text) > MAX_LINE_LEN:
            text = text[:MAX_LINE_LEN] + '…'
        with self._lock:
            if cont and self._items:
                last = self._items[-1]
                last['text'] += '\n' + text
                if 'Error' in text or 'Exception' in text:
                    last['level'] = 'error'
                return last
            k, lv = classify(text)
            self._seq += 1
            item = {'id': self._seq, 'ts': ts if ts is not None else time.time(), 'kind': kind or k, 'level': level or lv, 'text': text}
            self._items.append(item)
            return item

    def since(self, after=0, kind=None, level=None, query=None, limit=500):
        q = (query or '').lower()
        with self._lock:
            out = []
            for it in self._items:
                if it['id'] <= after:
                    continue
                if kind and kind != 'all' and it['kind'] != kind:
                    continue
                if level and level != 'all' and it['level'] != level:
                    continue
                if q and q not in it['text'].lower():
                    continue
                out.append({**it, 'stamp': stamp(it['ts'])})
            return out[-limit:], self._seq

    def clear(self):
        with self._lock:
            self._items.clear()


BUFFER = LogBuffer()


class _Tee:
    """Обёртка потока вывода: добавляет метку времени в терминал и пишет строки в BUFFER."""

    def __init__(self, stream, name):
        self._s = stream
        self._name = name
        self._part = ''
        self._lock = threading.RLock()
        self._in_trace = False

    def write(self, data):
        try:
            if not isinstance(data, str):
                return self._s.write(data)
            with self._lock:
                self._part += data
                out = []
                while '\n' in self._part:
                    line, self._part = self._part.split('\n', 1)
                    out.append(self._handle(line) + '\n')
            return self._s.write(''.join(out)) if out else len(data)
        except Exception:                                           # перехватчик не должен ронять вывод
            try:
                return self._s.write(data)
            except Exception:
                return 0

    def _handle(self, line):
        if not line.strip():
            self._in_trace = False
            return line
        cont = bool(_CONT_RE.match(line)) and (self._in_trace or line.startswith((' ', '\t')))
        if line.startswith('Traceback (most recent'):
            self._in_trace = True
        elif self._in_trace and not line.startswith((' ', '\t')) and not _CONT_RE.match(line):
            self._in_trace = False
        BUFFER.add(line, cont=cont and bool(BUFFER._items))
        # метку получают только «начальные» строки; продолжения трассировки остаются как есть
        return line if cont else f"{stamp()} {line}"

    def flush(self):
        try:
            with self._lock:
                if self._part:
                    rest, self._part = self._part, ''
                    self._s.write(self._handle(rest))
            self._s.flush()
        except Exception:
            pass

    def __getattr__(self, item):                                    # isatty, encoding, fileno, reconfigure…
        return getattr(self._s, item)


_installed = False


def install():
    """Подключить метки времени и буфер (повторный вызов безопасен)."""
    global _installed
    if _installed:
        return
    _installed = True
    sys.stdout = _Tee(sys.stdout, 'stdout')
    sys.stderr = _Tee(sys.stderr, 'stderr')
