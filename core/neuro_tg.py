"""
core/neuro_tg.py — общие помощники Telegram-инструментов нейросети (core/neuro_seed/*.py: send_message, find_contact, read_chat…).

  CTX                       — где открыт диалог с нейросетью: чат, где вызвали .n, сообщение, на которое ответили (ставит агент на время хода)
  await resolve_chat(s)     — «кому/куда» → (entity, how): id, @username, t.me/…, +телефон, «me»/«избранное», «here»/«этот чат», имя.
                              how: 'exact' | 'name' (нашли по имени). Несколько подходящих по имени — Ambiguous(кандидаты).
  await find_people(q)      — поиск людей и чатов по имени с учётом падежей и уменьшительных («кате» → Катя, Катерина, Екатерина, Kate)
  describe(entity)          — «Имя Фамилия · @user · id · тел.» одной строкой
"""

import json
import re
import time
from datetime import datetime, timedelta

CTX = {}                       # {'chat_id', 'chat', 'reply', ...} — см. NeuroDash: _chat_context()
LAST = []                      # последний список вариантов (find_contact / неоднозначное имя): «2» = второй из них

HERE = {'here', 'current', 'this', 'сюда', 'здесь', 'тут', 'этот чат', 'этот', 'текущий', 'текущий чат', 'в этот чат', 'в этом чате'}
ME = {'me', 'self', 'saved', 'избранное', 'в избранное', 'себе', 'мне', 'сохраненные', 'сохранённые'}

# уменьшительные и полные формы — «напиши кате» найдёт и «Екатерина», и «Katya»
NAME_GROUPS = [
    'екатерина катерина катя катюша катька кэт kate katya katia ekaterina katerina',
    'александр александра саша саня шура сашка alex alexander alexandra sasha',
    'анна аня анюта нюра анька anna anya ann',
    'мария маша маня машка маруся maria masha mary',
    'елена лена леночка ленка elena lena helen',
    'ольга оля олечка olga olya',
    'наталья наталия наташа ната natalia natasha',
    'татьяна таня танюша tatiana tanya',
    'светлана света светик svetlana sveta',
    'юлия юля julia yulia',
    'анастасия настя настена nastya anastasia',
    'дарья даша darya dasha daria',
    'ксения ксюша kseniya ksenia ksusha',
    'виктория вика victoria vika',
    'елизавета лиза liza elizaveta',
    'софья софия соня sofia sonya',
    'полина поля polina',
    'алина alina',
    'валерия лера valeria lera',
    'ирина ира irina ira',
    'дмитрий дима димон митя dmitry dima',
    'алексей леша лёша алеша alexey alexei lesha',
    'сергей серега серёжа сережа sergey serega',
    'андрей андрюха andrey andrei',
    'михаил миша мишаня mikhail misha',
    'николай коля nikolay kolya',
    'владимир вова володя vladimir vova',
    'владислав влад vlad vladislav',
    'иван ваня ivan vanya',
    'евгений евгения женя zhenya evgeny',
    'павел паша pavel pasha',
    'максим макс maxim max',
    'артем артём тема тёма artem',
    'никита nikita',
    'егор egor',
    'кирилл кирюха kirill',
    'константин костя konstantin kostya',
    'антон тоша anton',
    'роман рома roman roma',
    'игорь igor',
    'григорий гриша grigory grisha',
    'вячеслав слава slava',
    'станислав стас stas',
    'георгий жора гоша georgy',
    'мама мамуля mom mum',
    'папа батя dad',
]
_GROUPS = [set(g.split()) for g in NAME_GROUPS]
_ENDINGS = ('ой', 'ей', 'ою', 'ею', 'ом', 'ем', 'ам', 'ям', 'ах', 'ях', 'е', 'ю', 'у', 'и', 'ы', 'а', 'я', 'ь', 'й')
_TR = {'a': 'а', 'b': 'б', 'v': 'в', 'g': 'г', 'd': 'д', 'e': 'е', 'z': 'з', 'i': 'и', 'k': 'к', 'l': 'л', 'm': 'м', 'n': 'н', 'o': 'о',
       'p': 'п', 'r': 'р', 's': 'с', 't': 'т', 'u': 'у', 'f': 'ф', 'h': 'х', 'c': 'к', 'y': 'ы', 'j': 'й', 'w': 'в', 'x': 'кс', 'q': 'к'}
_TR2 = (('shch', 'щ'), ('sch', 'щ'), ('zh', 'ж'), ('kh', 'х'), ('ch', 'ч'), ('sh', 'ш'), ('ya', 'я'), ('yu', 'ю'), ('yo', 'е'), ('ts', 'ц'))


class Ambiguous(Exception):
    def __init__(self, query, candidates):
        super().__init__(f"под «{query}» подходят несколько: {len(candidates)}")
        self.query = query
        self.candidates = candidates


def _norm(s) -> str:
    return re.sub(r'[^0-9a-zа-я ]+', ' ', str(s or '').lower().replace('ё', 'е')).strip()


def _translit(word: str) -> str:
    w = word
    for a, b in _TR2:
        w = w.replace(a, b)
    return ''.join(_TR.get(ch, ch) for ch in w)


def _forms(word: str) -> set:
    """«кате» → {кате, кат, ката, катя, …} + полные/уменьшительные формы из NAME_GROUPS."""
    out = {word}
    for e in _ENDINGS:
        if word.endswith(e) and len(word) - len(e) >= 2:
            base = word[:-len(e)]
            out |= {base, base + 'а', base + 'я', base + 'й', base + 'ь', base + 'ия'}
            break
    for g in _GROUPS:
        if out & g:
            out |= g
    return out


def _tokens(*names) -> set:
    toks = set()
    for n in names:
        for t in _norm(n).split():
            toks.add(t)
            if re.fullmatch(r'[a-z]+', t):
                toks.add(_translit(t))
    return toks


def _score(query: str, *names) -> int:
    """Сколько слов запроса нашлось в имени (×10) + бонус за точное совпадение; 0 — не подходит."""
    q = _norm(query).split()
    if not q:
        return 0
    toks = _tokens(*names)
    if not toks:
        return 0
    total = 0
    for w in q:
        forms = _forms(w)
        best = 0
        for t in toks:
            if t in forms:
                best = max(best, 12)
            elif any(len(f) >= 3 and t.startswith(f) for f in forms):
                best = max(best, 8)
            elif len(w) >= 4 and w in t:
                best = max(best, 5)
        if not best:
            return 0
        total += best
    return total


def describe(e) -> str:
    if e == 'me':
        return 'Избранное'
    name = ' '.join(x for x in (getattr(e, 'first_name', None), getattr(e, 'last_name', None)) if x) or getattr(e, 'title', None) or '—'
    parts = [name]
    if getattr(e, 'username', None):
        parts.append('@' + e.username)
    parts.append(f"id {getattr(e, 'id', '?')}")
    if getattr(e, 'phone', None):
        parts.append('тел. +' + str(e.phone).lstrip('+'))
    if getattr(e, 'bot', False):
        parts.append('бот')
    if getattr(e, 'contact', False):
        parts.append('в контактах')
    return ' · '.join(parts)


def kind(e) -> str:
    if getattr(e, 'bot', False):
        return 'бот'
    if hasattr(e, 'first_name'):
        return 'ЛС'
    if getattr(e, 'broadcast', False):
        return 'канал'
    return 'группа'


def _aliases_path():
    from .paths import DATA_DIR
    return DATA_DIR / 'neuro' / 'aliases.json'


def aliases() -> dict:
    """Прозвища людей, которые назвал владелец: {'шикс': {'id': 5195118663, 'line': 'Shx · @Shxdowrate · id …'}}."""
    try:
        d = json.loads(_aliases_path().read_text(encoding='utf-8'))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def alias_set(name: str, ent) -> str:
    d = aliases()
    key = _norm(name)
    d[key] = {'id': peer_id(ent), 'line': describe(ent)}
    p = _aliases_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding='utf-8')
    return key


def _alias_hit(query: str):
    """Прозвище с падежами: «шиксу», «у шикса» → запись из aliases()."""
    d = aliases()
    if not d:
        return None
    words = _norm(query).split()
    for key, v in d.items():
        kw = key.split()
        if len(kw) == 1 and any(kw[0] in _forms(w) or (len(kw[0]) >= 4 and w.startswith(kw[0][:-1])) for w in words):
            return v
        if len(kw) > 1 and key in _norm(query):
            return v
    return None


def _name_bonus(query: str, e) -> int:
    """«Shx» = имя «Shx» целиком — почти наверняка он, а не бот «userbot of @shxdowrate», где «shx» лишь начало слова.
    Боты — ниже людей: «напиши Х» почти всегда про человека."""
    q = ' '.join(_norm(query).split())
    full = ' '.join(_norm(' '.join(x for x in (getattr(e, 'first_name', None), getattr(e, 'last_name', None)) if x)
                          or getattr(e, 'title', None) or '').split())
    first = ' '.join(_norm(getattr(e, 'first_name', None) or '').split())
    bonus = 6 if q and q in (full, first) else 0
    if getattr(e, 'bot', False):
        bonus -= 4
    return bonus


async def find_people(query: str, limit: int = 8, users_only: bool = False) -> list:
    """[{entity, score, kind, last, unread, line}] — лучшие совпадения по имени среди диалогов и контактов (и прозвищ владельца)."""
    import engine
    client = engine.client
    found = {}
    hit = _alias_hit(query)
    if hit:
        try:
            e = await client.get_entity(hit['id'])
            found[e.id] = {'entity': e, 'score': 100, 'kind': kind(e), 'last': None, 'unread': 0}
        except Exception:                                # noqa: BLE001
            pass
    async for d in client.iter_dialogs(limit=500):
        e = d.entity
        if users_only and not hasattr(e, 'first_name'):
            continue
        names = [getattr(e, 'first_name', None), getattr(e, 'last_name', None), getattr(e, 'username', None), getattr(e, 'title', None)]
        s = _score(query, *names)
        if s:
            if hasattr(e, 'first_name'):
                s += 3                                   # люди важнее групп с похожим названием
            s += _name_bonus(query, e)
            found[e.id] = {'entity': e, 'score': s, 'kind': kind(e), 'last': d.date, 'unread': d.unread_count}
    try:
        from telethon.tl.functions.contacts import GetContactsRequest
        res = await client(GetContactsRequest(hash=0))
        for u in getattr(res, 'users', []) or []:
            if u.id in found:
                continue
            s = _score(query, u.first_name, u.last_name, u.username)
            if s:
                found[u.id] = {'entity': u, 'score': s + 2 + _name_bonus(query, u), 'kind': kind(u), 'last': None, 'unread': 0}
    except Exception:                                    # noqa: BLE001
        pass
    out = sorted(found.values(), key=lambda x: (-x['score'], -(x['last'].timestamp() if x['last'] else 0)))[:max(1, limit)]
    if out:
        LAST[:] = [x['entity'] for x in out]
    for x in out:
        when = x['last'].astimezone().strftime('%d.%m.%Y') if x['last'] else 'диалога нет'
        x['line'] = f"{describe(x['entity'])} · {x['kind']} · последнее {when}"
    return out


def _clear_winner(cands) -> bool:
    """Один кандидат — или первый заметно лучше остальных (полное имя против случайного совпадения)."""
    return len(cands) == 1 or (len(cands) > 1 and cands[0]['score'] >= cands[1]['score'] + 6)


async def resolve_chat(chat, allow_name: bool = True):
    """→ (entity, how). how: 'exact' (id, @username, телефон, here, me) или 'name'. Не нашли — ValueError, несколько — Ambiguous."""
    import engine
    client = engine.client
    s = str(chat if chat is not None else '').strip()
    low = s.lower().replace('ё', 'е')
    if not s:
        raise ValueError('не указан чат / получатель')
    if low in HERE:
        cid = CTX.get('chat_id')
        if not cid:
            raise ValueError('неизвестно, в каком чате открыт диалог — укажите чат явно')
        return await client.get_entity(cid), 'exact'
    if low in ME:
        return 'me', 'exact'
    m = re.fullmatch(r'(?:https?://)?(?:t\.me|telegram\.me)/([A-Za-z0-9_]{4,32})/?', s)
    if m:
        s = '@' + m.group(1)
    if re.fullmatch(r'#?\d{1,2}', s) and LAST and 1 <= int(s.lstrip('#')) <= len(LAST):     # «2» — второй из показанных вариантов
        return LAST[int(s.lstrip('#')) - 1], 'exact'
    if re.fullmatch(r'-?\d{3,}', s):
        try:
            return await client.get_entity(int(s)), 'exact'
        except Exception:                                # noqa: BLE001
            for d in [d async for d in client.iter_dialogs(limit=500)]:
                if d.id == int(s) or d.entity.id == abs(int(s)):
                    return d.entity, 'exact'
            raise ValueError(f'чат с id {s} не найден (аккаунт его не видел)')
    if re.fullmatch(r'\+\d[\d\s\-()]{8,}', s):
        try:
            return await client.get_entity(re.sub(r'[^\d+]', '', s)), 'exact'
        except Exception:                                # noqa: BLE001
            raise ValueError(f'номер {s} не найден (обычно виден только номер из контактов)')
    if s.startswith('@') or (re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{3,31}', s) and not allow_name):
        try:
            return await client.get_entity(s.lstrip('@') if s.startswith('@') else s), 'exact'
        except Exception:                                # noqa: BLE001
            if s.startswith('@'):
                raise ValueError(f'пользователь или чат {s} не найден')
    if re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{4,31}', s):  # похоже на username без @ — сначала свои диалоги с таким названием,
        async for d in client.iter_dialogs(limit=300):  # потом глобальный @username («musix» — своя группа MUSIX, а не чужой @MusiX)
            names = {_norm(getattr(d.entity, 'title', None) or ''), _norm(' '.join(x for x in (getattr(d.entity, 'first_name', None),
                                                                                       getattr(d.entity, 'last_name', None)) if x))}
            if _norm(s) in names:
                return d.entity, 'name'
        try:
            return await client.get_entity(s), 'exact'
        except Exception:                                # noqa: BLE001
            pass
    if not allow_name:
        raise ValueError(f'«{s}» — не id, не @username и не номер')
    cands = await find_people(s, limit=6)
    if not cands:
        raise ValueError(f'никого похожего на «{s}» среди диалогов и контактов')
    if _clear_winner(cands):
        return cands[0]['entity'], 'name'
    raise Ambiguous(s, cands)


async def member_in_chat(chat_ent, who):
    """Участник группы по id / @username / имени — ищем среди участников самой группы (человека может не быть в диалогах).
    → entity; ValueError — не нашли; Ambiguous — несколько."""
    import engine
    w = str(who or '').strip()
    if re.fullmatch(r'-?\d{3,}', w) or w.startswith('@'):
        try:
            return await engine.client.get_entity(int(w) if w.lstrip('-').isdigit() else w)
        except Exception:                                # noqa: BLE001
            pass
    cands = []
    q = w.lstrip('@')
    try:
        async for u in engine.client.iter_participants(chat_ent, search=q, limit=20):
            sc = _score(q, getattr(u, 'first_name', None), getattr(u, 'last_name', None), getattr(u, 'username', None)) or 1
            if _norm(q) in (_norm(getattr(u, 'username', None) or ''), _norm(' '.join(x for x in (u.first_name, u.last_name) if x))):
                sc += 20
            cands.append({'entity': u, 'score': sc, 'line': describe(u)})
    except Exception:                                    # noqa: BLE001
        pass
    if not cands:
        ent, _how = await resolve_chat(w)                # не участник (или поиск закрыт) — как обычно, по диалогам
        return ent
    cands.sort(key=lambda c: -c['score'])
    if _clear_winner(cands):
        return cands[0]['entity']
    raise Ambiguous(w, cands[:6])


def ambiguous_text(e: Ambiguous, what='Ничего не сделано') -> str:
    lines = [f"{what}: под «{e.query}» подходят несколько:"]
    lines += [f"{i}. {c['line']}" for i, c in enumerate(e.candidates, 1)]
    lines.append('Спроси пользователя, кого именно он имеет в виду (покажи варианты), и повтори вызов с id выбранного.')
    CTX['auto_choices'] = [f"{describe(c['entity']).split(' · ')[0][:30]} (id {getattr(c['entity'], 'id', '?')})" for c in e.candidates[:6]]
    return '\n'.join(lines)


def context_text() -> str:
    """Для системного промпта: где открыт диалог и на что ответили."""
    c = CTX or {}
    now = datetime.now().astimezone()
    off = now.strftime('%z')
    parts = [f"Сейчас {now.strftime('%Y-%m-%d %H:%M')} ({['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'][now.weekday()]}), часовой пояс UTC{off[:3]}:{off[3:]}."]
    if c.get('chat_line'):
        parts.append(f"Диалог открыт в чате: {c['chat_line']}. «Сюда», «здесь», «в этот чат» — это он (в инструментах можно писать chat/to = \"here\").")
    if c.get('sender_line'):
        parts.append(f"Команду вызвал: {c['sender_line']}.")
    if c.get('reply_line'):
        parts.append(f"Команда вызвана ответом на сообщение {c['reply_line']}")
    if c.get('owner_line'):
        parts.append(f"Владелец аккаунта (от его имени уходят сообщения): {c['owner_line']}.")
    return '\n'.join(parts)


# --------------------------------------------------------------------------- сообщения, время, журнал

REPLY_WORDS = {'reply', 'replied', 'this', 'это', 'это сообщение', 'на которое ответил', 'ответ', 'его', 'её', 'ее'}


def msg_ids(value) -> list:
    """12 | "12, 13" | [12, 13] | "reply"/"это сообщение" (то, на которое ответили командой) → [12, 13]."""
    if value is None or value == '' or (isinstance(value, str) and value.strip().lower() in REPLY_WORDS):
        rid = CTX.get('reply_id')
        return [int(rid)] if rid else []
    if isinstance(value, (list, tuple)):
        out = []
        for v in value:
            out += msg_ids(v) if not isinstance(v, int) else [v]
        return out
    if isinstance(value, int):
        return [value]
    return [int(x) for x in re.findall(r'\d+', str(value))]


_UNITS = (('мин', 60), ('мин', 60), ('час', 3600), ('ч', 3600), ('сек', 1), ('дн', 86400), ('день', 86400), ('дня', 86400),
          ('недел', 604800), ('minute', 60), ('min', 60), ('hour', 3600), ('h', 3600), ('day', 86400), ('week', 604800))


def parse_when(text) -> datetime:
    """«2026-10-27 09:00», «27.10 9:00», «09:30», «через 2 часа», «завтра в 9», «послезавтра 18:30» → datetime (местное время).
    Непонятно — ValueError."""
    s = str(text or '').strip().lower().replace('ё', 'е')
    now = datetime.now().astimezone()
    if not s:
        raise ValueError('не указано время')
    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%d.%m.%Y %H:%M', '%d.%m %H:%M'):
        try:
            d = datetime.strptime(s[:19] if 'T' in s or s[:4].isdigit() else s, fmt)
            if fmt == '%d.%m %H:%M':
                d = d.replace(year=now.year)
            return d.replace(tzinfo=now.tzinfo) if d.tzinfo is None else d
        except ValueError:
            pass
    # «+2m», «+1h», «2 мин», «30m», «1.5 h», «+10 сек» — относительное время (так его часто пишут модели)
    m = re.fullmatch(r'\+?\s*(\d+(?:[.,]\d+)?)\s*(m|min|mins|minute|minutes|мин|минут\w*|h|hr|hrs|hour|hours|ч|час\w*|'
                     r'd|day|days|д|дн\w*|день|s|sec|secs|second|seconds|с|сек\w*|w|week|weeks|нед\w*)\.?', s)
    if m:
        n = float(m.group(1).replace(',', '.'))
        u = m.group(2)
        sec = (60 if u[0] in 'mм' else 3600 if u[0] in 'hч' else 86400 if u[0] in 'dд' else 604800 if u[0] in 'wн' else 1)
        return now + timedelta(seconds=n * sec)
    try:
        d = datetime.fromisoformat(s.upper().replace('Z', '+00:00'))
        return d if d.tzinfo else d.replace(tzinfo=now.tzinfo)
    except ValueError:
        pass
    m = re.search(r'через\s+(?:(\d+(?:[.,]\d+)?)\s*)?([a-zа-я]+)', s) or re.search(r'in\s+(\d+(?:\.\d+)?)\s*([a-z]+)', s)
    if m:
        n = float((m.group(1) or '1').replace(',', '.'))
        unit = m.group(2)
        if unit.startswith('полчас'):
            return now + timedelta(minutes=30)
        for u, sec in _UNITS:
            if unit.startswith(u):
                return now + timedelta(seconds=n * sec)
    day = now
    if 'послезавтра' in s:
        day = now + timedelta(days=2)
    elif 'завтра' in s or 'tomorrow' in s:
        day = now + timedelta(days=1)
    t = re.search(r'(\d{1,2})(?:[:.](\d{2}))?\s*(утра|дня|вечера|ночи)?(?!\d)', re.sub(r'\d+\s*(?:мин|час|дн)', '', s))
    if t:
        h, mi = int(t.group(1)), int(t.group(2) or 0)
        if t.group(3) in ('дня', 'вечера') and h < 12:
            h += 12
        if h > 23 or mi > 59:
            raise ValueError(f'не понял время «{text}»')
        d = day.replace(hour=h, minute=mi, second=0, microsecond=0)
        if d <= now and day is now:
            d += timedelta(days=1)                       # «в 9:00», а уже 12:00 — значит, завтра
        return d
    if day is not now:
        return day.replace(hour=9, minute=0, second=0, microsecond=0)
    raise ValueError(f'не понял время «{text}» — напишите как 2026-10-27 09:00, «через 2 часа» или «завтра в 9»')


def fmt_when(d: datetime) -> str:
    d = d.astimezone()
    now = datetime.now().astimezone()
    day = 'сегодня' if d.date() == now.date() else 'завтра' if d.date() == (now + timedelta(days=1)).date() else d.strftime('%d.%m.%Y')
    return f"{day} в {d.strftime('%H:%M')}"


def peer_id(ent):
    if ent == 'me':
        return 'me'
    try:
        from telethon import utils
        return utils.get_peer_id(ent)
    except Exception:                                    # noqa: BLE001
        return getattr(ent, 'id', None)


def _journal_path():
    from .paths import DATA_DIR
    return DATA_DIR / 'neuro' / 'journal.jsonl'


def log_action(action: str, ent=None, ids=None, text: str = '', undo: str = None, extra: dict = None):
    """Журнал действий нейросети (user/data/neuro/journal.jsonl): что, где, какие сообщения, как отменить.
    undo: 'delete' — удалить отправленное у всех, 'unschedule' — снять отложенное, None — не отменяется."""
    entry = {'ts': time.time(), 'action': action, 'chat': describe(ent) if ent is not None else '', 'peer': peer_id(ent) if ent is not None else None,
             'ids': [int(i) for i in (ids or [])], 'text': str(text or '')[:400], 'undo': undo, 'done': False}
    if extra:
        entry.update(extra)
    try:
        p = _journal_path()
        p.parent.mkdir(parents=True, exist_ok=True)
        lines = p.read_text(encoding='utf-8').splitlines()[-499:] if p.exists() else []
        lines.append(json.dumps(entry, ensure_ascii=False, default=str))
        tmp = p.with_suffix('.tmp')
        tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        tmp.replace(p)
    except Exception:                                    # noqa: BLE001
        pass
    return entry


def journal(n: int = 20) -> list:
    """Последние записи журнала, новые сверху; у каждой — 'n' (номер строки в файле) для отмены."""
    try:
        lines = _journal_path().read_text(encoding='utf-8').splitlines()
    except OSError:
        return []
    out = []
    for i, line in enumerate(lines):
        try:
            e = json.loads(line)
            e['n'] = i
            out.append(e)
        except ValueError:
            continue
    return list(reversed(out))[:n]


def _journal_mark(n: int):
    p = _journal_path()
    lines = p.read_text(encoding='utf-8').splitlines()
    e = json.loads(lines[n])
    e['done'] = True
    lines[n] = json.dumps(e, ensure_ascii=False, default=str)
    p.write_text('\n'.join(lines) + '\n', encoding='utf-8')


async def undo(n: int) -> str:
    """Отменить запись журнала: удалить отправленное (у всех) или снять отложенное."""
    import engine
    entry = next((e for e in journal(500) if e['n'] == n), None)
    if entry is None:
        return 'запись не найдена'
    if entry.get('done'):
        return 'уже отменено'
    if not entry.get('undo') or not entry.get('ids'):
        return 'это действие не отменить'
    peer = entry['peer']
    ent = 'me' if peer == 'me' else await engine.client.get_entity(peer)
    if entry['undo'] == 'delete':
        await engine.client.delete_messages(ent, entry['ids'], revoke=True)
    elif entry['undo'] == 'mod':                         # мут/бан из журнала — снять
        u = await engine.client.get_entity(entry['ids'][0])
        if entry.get('mod') == 'ban':
            await engine.client.edit_permissions(ent, u, view_messages=True, send_messages=True)
        else:
            await engine.client.edit_permissions(ent, u, send_messages=True, send_media=True, send_stickers=True, send_gifs=True,
                                                 embed_link_previews=True)
    elif entry['undo'] == 'unschedule':
        from telethon.tl.functions.messages import DeleteScheduledMessagesRequest
        await engine.client(DeleteScheduledMessagesRequest(peer=await engine.client.get_input_entity(ent), id=entry['ids']))
    _journal_mark(n)
    return 'отменено'


# --------------------------------------------------------------------------- файлы

FOUND = []                     # последние найденные find_files: [(peer, msg_id)] — send_files с ids="found" шлёт их
KINDS = {
    'document': 'InputMessagesFilterDocument', 'doc': 'InputMessagesFilterDocument', 'file': 'InputMessagesFilterDocument',
    'файл': 'InputMessagesFilterDocument', 'документ': 'InputMessagesFilterDocument',
    'photo': 'InputMessagesFilterPhotos', 'фото': 'InputMessagesFilterPhotos', 'картинка': 'InputMessagesFilterPhotos',
    'video': 'InputMessagesFilterVideo', 'видео': 'InputMessagesFilterVideo',
    'media': 'InputMessagesFilterPhotoVideo', 'медиа': 'InputMessagesFilterPhotoVideo',
    'voice': 'InputMessagesFilterVoice', 'голосовое': 'InputMessagesFilterVoice', 'гс': 'InputMessagesFilterVoice',
    'audio': 'InputMessagesFilterMusic', 'музыка': 'InputMessagesFilterMusic', 'round': 'InputMessagesFilterRoundVideo',
    'кружок': 'InputMessagesFilterRoundVideo', 'link': 'InputMessagesFilterUrl', 'ссылка': 'InputMessagesFilterUrl',
    'gif': 'InputMessagesFilterGif',
}


def msg_filter(kind):
    """'document' | 'photo' | … → фильтр Telethon (класс) или None (any)."""
    name = KINDS.get(str(kind or '').lower().strip())
    if not name:
        return None
    from telethon.tl import types
    return getattr(types, name)


def file_title(m) -> str:
    f = getattr(m, 'file', None)
    name = getattr(f, 'name', None) if f is not None else None
    size = getattr(f, 'size', None) if f is not None else None
    kind = 'фото' if getattr(m, 'photo', None) else 'видео' if getattr(m, 'video', None) else 'голосовое' if getattr(m, 'voice', None) else 'файл'
    s = f"{kind} «{name}»" if name else kind
    if size:
        s += f" ({size / 1048576:.1f} МБ)" if size > 1048576 else f" ({max(1, size // 1024)} КБ)"
    return s


def file_match(query: str, m) -> bool:
    """Подходит ли файл под запрос — по имени файла и подписи, с падежами («отчеты» ↔ «Отчёт_Q3.xlsx»)."""
    if not str(query or '').strip():
        return True
    f = getattr(m, 'file', None)
    name = (getattr(f, 'name', None) or '') if f is not None else ''
    name = re.sub(r'[_.\-]+', ' ', name)
    return bool(_score(query, name, m.message or ''))


# --------------------------------------------------------------------------- задачи агента: будильники и события
# user/data/neuro/agent_jobs.json. Будильник — проснуться в момент at (repeat — каждые N минут). Событие — проснуться, когда придёт
# подходящее входящее сообщение (от кого, в каком чате, со словами, с файлом). Просыпается модуль NeuroDash: он и запускает ход агента
# в фоне, присылает итог и запросы на разрешение в ЛС от бота. «Сбросить контекст» в .n снимает все задачи.

MAX_JOBS = 20
MIN_REPEAT_MIN = 5


def _jobs_path():
    from .paths import DATA_DIR
    return DATA_DIR / 'neuro' / 'agent_jobs.json'


def jobs_read() -> dict:
    try:
        d = json.loads(_jobs_path().read_text(encoding='utf-8'))
        if isinstance(d, dict):
            d.setdefault('jobs', [])
            d.setdefault('next', 1)
            d.setdefault('runs', {})
            return d
    except (OSError, ValueError):
        pass
    return {'jobs': [], 'next': 1, 'runs': {}}


def jobs_write(d: dict):
    p = _jobs_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1, default=str), encoding='utf-8')
    tmp.replace(p)


def jobs_add(kind: str, task: str, **fields) -> dict:
    d = jobs_read()
    if len(d['jobs']) >= MAX_JOBS:
        raise ValueError(f'уже {MAX_JOBS} задач — отмените ненужные (cancel_job)')
    job = {'id': d['next'], 'kind': kind, 'task': str(task)[:2000], 'created': time.time(), **fields}
    d['next'] += 1
    d['jobs'].append(job)
    jobs_write(d)
    return job


def jobs_remove(job_id) -> bool:
    d = jobs_read()
    n = len(d['jobs'])
    d['jobs'] = [j for j in d['jobs'] if str(j['id']) != str(job_id).lstrip('#')]
    jobs_write(d)
    return len(d['jobs']) < n


def jobs_clear() -> int:
    d = jobs_read()
    n = len(d['jobs'])
    d['jobs'] = []
    jobs_write(d)
    return n


def job_line(j: dict) -> str:
    if j['kind'] == 'alarm':
        when = fmt_when(datetime.fromtimestamp(j['at']).astimezone())
        rep = f", потом каждые {j['repeat']} мин" if j.get('repeat') else ''
        return f"#{j['id']} ⏰ {when}{rep}: {j['task']}"
    cond = []
    if j.get('from_name'):
        cond.append(f"от {j['from_name']}")
    if j.get('chat_name'):
        cond.append(f"в {j['chat_name']}")
    if j.get('contains'):
        cond.append('со словами: ' + ', '.join(j['contains']))
    if j.get('files_only'):
        cond.append('с файлом')
    until = f", до {fmt_when(datetime.fromtimestamp(j['until']).astimezone())}" if j.get('until') else ''
    return f"#{j['id']} ⚡ когда придёт сообщение {' '.join(cond) or '(любое)'}{'' if j.get('once', True) else ' (каждый раз)'}{until}: {j['task']}"


def job_matches(j: dict, msg, sender_id, chat_id) -> bool:
    """Подходит ли входящее сообщение под задачу-событие."""
    if j.get('kind') != 'event':
        return False
    if j.get('from_id') and int(j['from_id']) != int(sender_id or 0):
        return False
    if j.get('chat_id') and int(j['chat_id']) != int(chat_id or 0):
        return False
    if j.get('files_only') and not (getattr(msg, 'document', None) or getattr(msg, 'photo', None) or getattr(msg, 'video', None)):
        return False
    words = j.get('contains') or []
    if words:
        text = (getattr(msg, 'message', '') or '').lower().replace('ё', 'е')
        f = getattr(msg, 'file', None)
        text += ' ' + ((getattr(f, 'name', None) or '').lower() if f is not None else '')
        if not any(_score(w, text) or w.lower().replace('ё', 'е') in text for w in words):
            return False
    return True


# --------------------------------------------------------------------------- инлайн-формы нейро и чужие кнопки

FORMS = {}                     # id → {'form', 'chat', 'text', 'labels', 'clicks': [], 'task', 'once', 'closed'}
ON_FORM_CLICK = None           # NeuroDash ставит сюда функцию (form_id, info) — клик будит агента, если у формы есть task


def parse_buttons(spec) -> list:
    """«Да | Нет ; Позже» / «Да|Нет\\nПозже» / [["Да","Нет"],["Позже"]] / ["Да","Нет"] → [[подпись, …], …]."""
    if isinstance(spec, str):
        s = spec.strip()
        if s.startswith('['):
            try:
                spec = json.loads(s)
            except ValueError:
                pass
    if isinstance(spec, str):
        rows = [r for r in re.split(r'[;\n]', spec) if r.strip()]
        return [[b.strip() for b in r.split('|') if b.strip()] for r in rows]
    if isinstance(spec, list):
        if all(isinstance(x, str) for x in spec):
            return [[x] for x in spec if x.strip()]
        return [[str(b) for b in row if str(b).strip()] if isinstance(row, list) else [str(row)] for row in spec]
    return []


def buttons_text(m) -> str:
    rows = getattr(getattr(m, 'reply_markup', None), 'rows', None) or []
    out, n = [], 0
    for r in rows:
        names = []
        for b in getattr(r, 'buttons', []):
            n += 1
            kind = ' (ссылка)' if getattr(b, 'url', None) else ''
            names.append(f"{n}. {getattr(b, 'text', '?')}{kind}")
        out.append(' | '.join(names))
    return ('кнопки: ' + ' ; '.join(out)) if out else ''


def msg_line(m, me_id=None) -> str:
    who = 'я' if getattr(m, 'out', False) else 'кто-то'
    s = getattr(m, 'sender', None)
    if not getattr(m, 'out', False) and s is not None:
        who = describe(s).split(' · id')[0]
    via = ' (через бота)' if getattr(m, 'via_bot_id', None) else ''
    text = (getattr(m, 'raw_text', None) or getattr(m, 'message', None) or '').strip()
    b = buttons_text(m)
    return f"#{m.id} {who}{via}: {text[:2000] or '—'}" + (f"\n   {b}" if b else '')


async def chat_changes(where, msg, before: str, wait: float = 8.0) -> list:
    """Что изменилось в чате после действия: правка msg (если before задан) и новые сообщения после msg.id."""
    import asyncio
    import engine
    seen, stable, cur, newer = None, 0, msg, []
    for _ in range(int(wait / 0.5)):
        await asyncio.sleep(0.5)
        cur = await engine.client.get_messages(where, ids=msg.id)
        newer = [m async for m in engine.client.iter_messages(where, min_id=msg.id, limit=8)]
        state = ((getattr(cur, 'raw_text', None) if cur else None), bool(cur and cur.buttons),
                 tuple((m.id, m.raw_text, bool(m.buttons)) for m in newer))
        if state == seen:
            stable += 1
            if stable >= 3:
                break
        else:
            seen, stable = state, 0
    out = []
    if cur is None:
        out.append(f"сообщение #{msg.id} удалено")
    elif before is not None and (cur.raw_text or '') != (before or ''):
        out.append('изменилось ' + msg_line(cur))
    out += ['новое ' + msg_line(m) for m in reversed(newer)]
    return out



# --------------------------------------------------------------------------- получатель из просьбы владельца

_VERB_RE = re.compile(r'(?:напиши|написать|отправь|отправить|скинь|перешли|передай|скажи|сообщи|ответь|спроси(?:\s+у)?|узнай\s+у|поздравь|позови|пригласи)'
                      r'\s+(?:сообщение\s+|пожалуйста\s+)?(?:для\s+)?([^\s,.:;!?"«»]+(?-i:\s+[А-ЯЁA-Z][^\s,.:;!?"«»]*)?)', re.I)
_NOT_PERSON = {'что', 'что-нибудь', 'чтонибудь', 'это', 'сюда', 'туда', 'ему', 'ей', 'им', 'мне', 'пожалуйста', 'сообщение', 'текст', 'всем'}


def recipient_from_request(text: str = None):
    """«напиши любимой "я люблю тебя"» → «любимой»; «спроси у шикса…» → «шикса». Не нашли — None."""
    m = _VERB_RE.search(text if text is not None else (CTX.get('user_text') or ''))
    if not m:
        return None
    who = m.group(1).strip()
    if who.lower() in _NOT_PERSON:
        return {'сюда': 'here', 'мне': 'me'}.get(who.lower())
    return who


async def recent_people(n: int = 8) -> list:
    """Последние личные диалоги (люди, не боты) — подсказка, кому писать, если получатель не нашёлся."""
    import engine
    out = []
    async for d in engine.client.iter_dialogs(limit=60):
        e = d.entity
        if hasattr(e, 'first_name') and not getattr(e, 'bot', False) and not getattr(e, 'is_self', False):
            out.append(e)
            if len(out) >= n:
                break
    return out


async def no_recipient_text(what='Сообщение не отправлено', query=None) -> str:
    """Получатель не найден: показываем последние ЛС и делаем их кнопками — пусть владелец выберет одним нажатием."""
    people = await recent_people(8)
    CTX['auto_choices'] = [f"{describe(e).split(' · ')[0][:30]} (id {e.id})" for e in people[:6]]
    head = f"{what}: " + (f"не нашёл «{query}» среди контактов, диалогов и прозвищ." if query else 'не указан получатель.')
    lines = [head, 'Последние личные диалоги:'] + [f"{i}. {describe(e)}" for i, e in enumerate(people, 1)]
    lines.append('Если человек среди них — повтори с его id. Если нет — спроси владельца, кто это (варианты-кнопки уже предложены), '
                 'и запомни ответ через remember_contact.')
    return '\n'.join(lines)
