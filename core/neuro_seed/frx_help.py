"""
name: frx_help
category: system
title: Справка по FRX
description: справка по FRX: что это, команды, модули, сайт .web, нейросеть и инструменты, обновление (короткий путеводитель и журнал обновлений); без query — оглавление
args: query?: string, source?: string  (source: guide — по умолчанию, changelog — что нового)
example: {"query": "как загрузить модуль"}
keywords: frx, фрикс, юзербот, команда, команды, модуль, как пользоваться, что умеешь, справка, документац, что нового, версия
limit: 6000
"""
import re
from pathlib import Path

_STOP = {'как', 'что', 'это', 'для', 'или', 'при', 'про', 'где', 'какой', 'какая', 'какие', 'мне', 'мой', 'можно', 'есть', 'the', 'and'}


def _sections(path, level='## '):
    try:
        text = Path(path).read_text(encoding='utf-8')
    except OSError:
        return []
    out, head, buf = [], 'Введение', []
    for line in text.split('\n'):
        if line.startswith(level):
            out.append((head, '\n'.join(buf).strip()))
            head, buf = line[len(level):].strip(), []
        else:
            buf.append(line)
    out.append((head, '\n'.join(buf).strip()))
    return [(h, b) for h, b in out if b]


def _clean(text):
    text = re.sub(r'\*\*(Update [\d.]+[^*]*)\*\*', '', text)
    return re.sub(r'\n{3,}', '\n\n', text)


async def run(query: str = '', source: str = 'guide') -> str:
    kind = str(source).lower()
    src = 'CHANGELOG.md' if kind.startswith('change') else 'core/frx_guide.md'
    secs = _sections(src)
    if kind.startswith('change') and re.search(r'нов|послед|измен|обнов|update|^$', (query or '').lower()):
        return '\n\n'.join(f'## {h}\n{b[:1800]}' for h, b in secs[1:3])
    if not secs:
        return f'Файл {src} не найден рядом с юзерботом.'
    q = (query or '').strip().lower()
    if not q:
        return f'Разделы справки ({src}):\n' + '\n'.join(f'- {h}' for h, _ in secs[:60]) + '\n\nЗадай query, чтобы получить нужный раздел.'
    words = [w for w in re.findall(r'[\wё.]{3,}', q) if w not in _STOP]
    # учитываем корни русских слов: «загрузить» ~ «загруз»
    stems = [w[:max(4, len(w) - 2)] if len(w) > 5 else w for w in words]
    scored = []
    for h, b in secs:
        hl, bl = h.lower(), b.lower()
        sc = sum(6 for s in stems if s in hl) + sum(min(bl.count(s), 6) for s in stems)
        if sc:
            scored.append((sc, h, b))
    if not scored and src == 'core/frx_guide.md':
        return (f'В путеводителе по запросу «{query}» ничего нет. Для технических деталей (написание модулей, API) есть инструмент frx_readme — '
                'он тяжёлый (много токенов) и перед запуском спросит разрешение у пользователя.')
    if not scored:
        return f'По запросу «{query}» ничего не нашлось. Попробуй другие слова или вызови без query — покажу оглавление.'
    scored.sort(key=lambda x: -x[0])
    out = []
    for sc, h, b in scored[:2]:
        # внутри раздела берём абзацы, где слова встречаются чаще всего
        paras = [p for p in re.split(r'\n\s*\n', _clean(b)) if p.strip()]
        paras.sort(key=lambda p: -sum(p.lower().count(s) for s in stems))
        body = '\n\n'.join(paras[:4])
        out.append(f'## {h}\n{body[:2600]}')
    return '\n\n'.join(out)
