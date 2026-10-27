"""
name: get_current_time
category: time
title: Текущее время
description: текущие дата и время — у владельца или в любом городе/поясе: where — город по-русски или по-английски («Благовещенск», «Нью-Йорк»), IANA-пояс («Asia/Tokyo») или смещение («UTC+9», «МСК+6»). Работает и без базы часовых поясов на компьютере (встроенная таблица городов)
args: where?: string
example: {"where": "Благовещенск"}
keywords: время, который час, сколько времени, часовой пояс, сколько сейчас в
"""
import re
from datetime import datetime, timedelta, timezone as _tz

# город / пояс → (смещение от UTC в часах, IANA). Россия — без перехода на летнее время; для остальных — стандартное время,
# летнее учитываем, только если доступна база IANA (zoneinfo/tzdata).
CITIES = {
    'калининград': (2, 'Europe/Kaliningrad'), 'kaliningrad': (2, 'Europe/Kaliningrad'),
    'москва': (3, 'Europe/Moscow'), 'moscow': (3, 'Europe/Moscow'), 'мск': (3, 'Europe/Moscow'), 'msk': (3, 'Europe/Moscow'),
    'санкт-петербург': (3, 'Europe/Moscow'), 'петербург': (3, 'Europe/Moscow'), 'питер': (3, 'Europe/Moscow'), 'спб': (3, 'Europe/Moscow'),
    'казань': (3, 'Europe/Moscow'), 'нижний новгород': (3, 'Europe/Moscow'), 'ростов': (3, 'Europe/Moscow'), 'краснодар': (3, 'Europe/Moscow'),
    'сочи': (3, 'Europe/Moscow'), 'воронеж': (3, 'Europe/Moscow'), 'волгоград': (3, 'Europe/Volgograd'), 'минск': (3, 'Europe/Minsk'),
    'minsk': (3, 'Europe/Minsk'), 'стамбул': (3, 'Europe/Istanbul'), 'istanbul': (3, 'Europe/Istanbul'),
    'самара': (4, 'Europe/Samara'), 'samara': (4, 'Europe/Samara'), 'ижевск': (4, 'Europe/Samara'), 'ульяновск': (4, 'Europe/Ulyanovsk'),
    'саратов': (4, 'Europe/Saratov'), 'астрахань': (4, 'Europe/Astrakhan'), 'баку': (4, 'Asia/Baku'), 'тбилиси': (4, 'Asia/Tbilisi'),
    'ереван': (4, 'Asia/Yerevan'), 'дубай': (4, 'Asia/Dubai'), 'dubai': (4, 'Asia/Dubai'),
    'екатеринбург': (5, 'Asia/Yekaterinburg'), 'yekaterinburg': (5, 'Asia/Yekaterinburg'), 'челябинск': (5, 'Asia/Yekaterinburg'),
    'пермь': (5, 'Asia/Yekaterinburg'), 'уфа': (5, 'Asia/Yekaterinburg'), 'тюмень': (5, 'Asia/Yekaterinburg'), 'оренбург': (5, 'Asia/Yekaterinburg'),
    'ташкент': (5, 'Asia/Tashkent'), 'tashkent': (5, 'Asia/Tashkent'), 'астана': (5, 'Asia/Almaty'), 'алматы': (5, 'Asia/Almaty'),
    'almaty': (5, 'Asia/Almaty'), 'бишкек': (6, 'Asia/Bishkek'), 'омск': (6, 'Asia/Omsk'), 'omsk': (6, 'Asia/Omsk'),
    'новосибирск': (7, 'Asia/Novosibirsk'), 'novosibirsk': (7, 'Asia/Novosibirsk'), 'томск': (7, 'Asia/Tomsk'), 'барнаул': (7, 'Asia/Barnaul'),
    'кемерово': (7, 'Asia/Novokuznetsk'), 'новокузнецк': (7, 'Asia/Novokuznetsk'), 'красноярск': (7, 'Asia/Krasnoyarsk'),
    'krasnoyarsk': (7, 'Asia/Krasnoyarsk'), 'бангкок': (7, 'Asia/Bangkok'), 'ханой': (7, 'Asia/Ho_Chi_Minh'),
    'иркутск': (8, 'Asia/Irkutsk'), 'irkutsk': (8, 'Asia/Irkutsk'), 'улан-удэ': (8, 'Asia/Irkutsk'), 'пекин': (8, 'Asia/Shanghai'),
    'шанхай': (8, 'Asia/Shanghai'), 'beijing': (8, 'Asia/Shanghai'), 'shanghai': (8, 'Asia/Shanghai'), 'гонконг': (8, 'Asia/Hong_Kong'),
    'сингапур': (8, 'Asia/Singapore'), 'singapore': (8, 'Asia/Singapore'), 'улан-батор': (8, 'Asia/Ulaanbaatar'),
    'чита': (9, 'Asia/Chita'), 'chita': (9, 'Asia/Chita'), 'благовещенск': (9, 'Asia/Yakutsk'), 'blagoveshchensk': (9, 'Asia/Yakutsk'),
    'якутск': (9, 'Asia/Yakutsk'), 'yakutsk': (9, 'Asia/Yakutsk'), 'токио': (9, 'Asia/Tokyo'), 'tokyo': (9, 'Asia/Tokyo'),
    'сеул': (9, 'Asia/Seoul'), 'seoul': (9, 'Asia/Seoul'),
    'владивосток': (10, 'Asia/Vladivostok'), 'vladivostok': (10, 'Asia/Vladivostok'), 'хабаровск': (10, 'Asia/Vladivostok'),
    'khabarovsk': (10, 'Asia/Vladivostok'), 'сидней': (10, 'Australia/Sydney'), 'sydney': (10, 'Australia/Sydney'),
    'магадан': (11, 'Asia/Magadan'), 'magadan': (11, 'Asia/Magadan'), 'южно-сахалинск': (11, 'Asia/Sakhalin'), 'сахалин': (11, 'Asia/Sakhalin'),
    'петропавловск-камчатский': (12, 'Asia/Kamchatka'), 'камчатка': (12, 'Asia/Kamchatka'), 'анадырь': (12, 'Asia/Anadyr'),
    'лондон': (0, 'Europe/London'), 'london': (0, 'Europe/London'), 'лиссабон': (0, 'Europe/Lisbon'),
    'париж': (1, 'Europe/Paris'), 'paris': (1, 'Europe/Paris'), 'берлин': (1, 'Europe/Berlin'), 'berlin': (1, 'Europe/Berlin'),
    'рим': (1, 'Europe/Rome'), 'мадрид': (1, 'Europe/Madrid'), 'варшава': (1, 'Europe/Warsaw'), 'прага': (1, 'Europe/Prague'),
    'амстердам': (1, 'Europe/Amsterdam'), 'киев': (2, 'Europe/Kyiv'), 'kyiv': (2, 'Europe/Kyiv'), 'kiev': (2, 'Europe/Kyiv'),
    'рига': (2, 'Europe/Riga'), 'вильнюс': (2, 'Europe/Vilnius'), 'таллин': (2, 'Europe/Tallinn'), 'хельсинки': (2, 'Europe/Helsinki'),
    'афины': (2, 'Europe/Athens'), 'кишинёв': (2, 'Europe/Chisinau'), 'кишинев': (2, 'Europe/Chisinau'), 'каир': (2, 'Africa/Cairo'),
    'тель-авив': (2, 'Asia/Jerusalem'), 'иерусалим': (2, 'Asia/Jerusalem'), 'дели': (5.5, 'Asia/Kolkata'), 'мумбаи': (5.5, 'Asia/Kolkata'),
    'нью-йорк': (-5, 'America/New_York'), 'new york': (-5, 'America/New_York'), 'нью йорк': (-5, 'America/New_York'),
    'вашингтон': (-5, 'America/New_York'), 'майами': (-5, 'America/New_York'), 'торонто': (-5, 'America/Toronto'),
    'чикаго': (-6, 'America/Chicago'), 'chicago': (-6, 'America/Chicago'), 'мехико': (-6, 'America/Mexico_City'),
    'денвер': (-7, 'America/Denver'), 'лос-анджелес': (-8, 'America/Los_Angeles'), 'los angeles': (-8, 'America/Los_Angeles'),
    'сан-франциско': (-8, 'America/Los_Angeles'), 'ванкувер': (-8, 'America/Vancouver'), 'сан-паулу': (-3, 'America/Sao_Paulo'),
    'буэнос-айрес': (-3, 'America/Argentina/Buenos_Aires'), 'utc': (0, 'UTC'), 'gmt': (0, 'UTC'),
}
_IANA = {v[1].lower(): v[0] for v in CITIES.values()}
_ENDINGS = ('е', 'и', 'у', 'а', 'ом', 'ой', 'ске', 'ска')


def _lookup(where):
    w = re.sub(r'^(в|во|in)\s+', '', where.strip().lower().replace('ё', 'е'))
    if w in CITIES:
        return CITIES[w]
    for e in _ENDINGS:                                       # «в благовещенске» → «благовещенск»
        if w.endswith(e) and w[:-len(e)] in CITIES:
            return CITIES[w[:-len(e)]]
        if w.endswith(e) and w[:-len(e)] + 'а' in CITIES:
            return CITIES[w[:-len(e)] + 'а']
    if w in _IANA:
        return (_IANA[w], where.strip())
    tail = w.rsplit('/', 1)[-1].replace('_', ' ')
    if tail in CITIES:
        return CITIES[tail]
    return None


def _fixed(where):
    """«UTC+9», «GMT-5», «+3», «МСК+6» → смещение в часах."""
    w = where.strip().lower().replace(' ', '')
    m = re.fullmatch(r'(?:мск|msk)([+-]\d{1,2})', w)
    if m:
        return 3 + int(m.group(1))
    m = re.fullmatch(r'(?:utc|gmt)?([+-]\d{1,2})(?::?(\d{2}))?', w)
    if m:
        h, mi = int(m.group(1)), int(m.group(2) or 0)
        return h + (1 if h >= 0 else -1) * mi / 60
    return None


def _fmt(d, label):
    days = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс']
    return f"{label}: {d.strftime('%H:%M')} ({days[d.weekday()]}, {d.strftime('%d.%m.%Y')})"


async def run(where: str = '', timezone: str = '') -> str:
    where = (where or timezone or '').strip()
    local = datetime.now().astimezone()
    loc_off = local.utcoffset().total_seconds() / 3600
    if not where:
        return _fmt(local, f"У владельца (UTC{loc_off:+g})")
    iana, off = None, None
    hit = _lookup(where)
    if hit:
        off, iana = hit
    elif '/' in where:
        iana = where
    else:
        off = _fixed(where)
    if iana:
        try:
            from zoneinfo import ZoneInfo
            d = datetime.now(ZoneInfo(iana))
            diff = d.utcoffset().total_seconds() / 3600 - loc_off
            return _fmt(d, f"{where} ({iana}, UTC{d.utcoffset().total_seconds() / 3600:+g})") + f"; разница с владельцем {diff:+g} ч"
        except Exception:                                    # нет tzdata (Windows) — берём таблицу
            pass
    if off is None:
        return (f"Пояс «{where}» неизвестен таблице. Сделай сам: узнай смещение города от UTC (web_search «время в {where}» или модуль "
                f"часов через module_commands) и посчитай от времени владельца: сейчас у него {local.strftime('%H:%M')} (UTC{loc_off:+g}).")
    d = datetime.now(_tz.utc) + timedelta(hours=off)
    return _fmt(d, f"{where} (UTC{off:+g})") + f"; разница с владельцем {off - loc_off:+g} ч" + ('' if iana in (None, 'UTC') else ' (без учёта летнего времени)' if off < 2 or off > 12 else '')
