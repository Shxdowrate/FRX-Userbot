"""Подмена всех ссылок на объект (нужна при обновлении библиотеки, импортированной несколькими модулями)."""

import gc


def replace_all_refs(old, new):
    for referrer in gc.get_referrers(old):
        try:
            if isinstance(referrer, dict):
                for key, value in list(referrer.items()):
                    if value is old:
                        referrer[key] = new
            elif isinstance(referrer, list):
                for i, value in enumerate(referrer):
                    if value is old:
                        referrer[i] = new
            elif isinstance(referrer, set):
                if old in referrer:
                    referrer.discard(old)
                    referrer.add(new)
        except Exception:
            continue
    return new
