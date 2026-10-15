"""Валидаторы значений конфига модулей (loader.validators.*), совместимые с Hikka/Heroku.

У каждого валидатора: .validate(value) → приведённое значение или ValidationError; .doc — {язык: описание}
(«в творительном падеже», как в Hikka: «должно быть <doc>»); .internal_id — тип для интерфейса конфига.
"""

import functools
import re
import typing
from urllib.parse import urlparse

ConfigAllowedTypes = typing.Union[tuple, list, str, int, bool, None]

LANGS = ("en", "ru", "uk", "ua", "de", "ja", "jp")


class ValidationError(Exception):
    """Значение не подходит. Текст исключения показывается пользователю в .config."""


def _doc(en: str, ru: str) -> dict:
    d = {lang: en for lang in LANGS}
    d["ru"] = ru
    return d


def _graphemes(text: str) -> list:
    try:
        import grapheme

        return list(grapheme.graphemes(text))
    except Exception:
        pass
    # упрощённое разбиение: модификаторы и ZWJ-последовательности приклеиваются к предыдущему символу
    out = []
    joiner = False
    for ch in text:
        cp = ord(ch)
        glue = (
            joiner
            or ch in "‍️︎⃣"
            or 0x1F3FB <= cp <= 0x1F3FF
            or 0xE0020 <= cp <= 0xE007F
            or 0x0300 <= cp <= 0x036F
        )
        if glue and out:
            out[-1] += ch
        else:
            out.append(ch)
        joiner = ch == "‍"
    return out


def _is_emoji(g: str) -> bool:
    try:
        import emoji as _emoji

        return g in _emoji.EMOJI_DATA or _emoji.is_emoji(g)
    except Exception:
        pass
    return any(
        0x1F000 <= ord(c) <= 0x1FAFF or 0x2600 <= ord(c) <= 0x27BF or 0x2B00 <= ord(c) <= 0x2BFF or 0x2190 <= ord(c) <= 0x21FF
        or c in "©®‼⁉™ℹ〰〽㊗㊙"
        for c in g
    )


class Validator:
    """Базовый класс. validator — функция(value) → значение (или ValidationError)."""

    def __init__(self, validator: typing.Callable, doc=None, _internal_id=None):
        self.validate = validator
        if isinstance(doc, str):
            doc = {lang: doc for lang in LANGS}
        self.doc = doc or _doc("value", "значением")
        self.internal_id = _internal_id


class Boolean(Validator):
    _TRUE = {"true", "1", "yes", "on", "y", "да", "вкл", "+"}
    _FALSE = {"false", "0", "no", "off", "n", "нет", "выкл", "-"}

    def __init__(self):
        super().__init__(self._validate, _doc("boolean value", "логическим значением"), _internal_id="Boolean")

    @staticmethod
    def _validate(value, /) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        v = str(value).strip().lower()
        if v in Boolean._TRUE:
            return True
        if v in Boolean._FALSE:
            return False
        raise ValidationError("Passed value must be a boolean")


class Integer(Validator):
    def __init__(self, *, digits: int = None, minimum: int = None, maximum: int = None):
        en, ru = "integer", "целым числом"
        if minimum == 0 and maximum is None:
            en, ru = "positive integer", "положительным целым числом"
        if maximum == 0 and minimum is None:
            en, ru = "negative integer", "отрицательным целым числом"
        if digits is not None:
            en += f" of exactly {digits} digits"
            ru += f" из {digits} цифр"
        if minimum not in (None, 0) and maximum is not None:
            en += f" from {minimum} to {maximum}"
            ru += f" от {minimum} до {maximum}"
        elif minimum not in (None, 0):
            en += f" greater than {minimum}"
            ru += f" не меньше {minimum}"
        elif maximum not in (None, 0):
            if minimum == 0:
                en += f" up to {maximum}"
                ru += f" до {maximum}"
            else:
                en += f" less than {maximum}"
                ru += f" не больше {maximum}"
        super().__init__(
            functools.partial(self._validate, digits=digits, minimum=minimum, maximum=maximum),
            _doc(en, ru),
            _internal_id="Integer",
        )

    @staticmethod
    def _validate(value, /, *, digits=None, minimum=None, maximum=None) -> int:
        if isinstance(value, bool):
            value = int(value)
        try:
            value = int(str(value).strip())
        except ValueError:
            raise ValidationError(f"Passed value ({value}) must be a number")
        if minimum is not None and value < minimum:
            raise ValidationError(f"Passed value ({value}) is lower than minimum one")
        if maximum is not None and value > maximum:
            raise ValidationError(f"Passed value ({value}) is greater than maximum one")
        if digits is not None and len(str(abs(value))) != digits:
            raise ValidationError(f"The length of passed value ({value}) is incorrect (must be exactly {digits} digits)")
        return value


class Float(Validator):
    def __init__(self, minimum: float = None, maximum: float = None):
        en, ru = "float", "дробным числом"
        if minimum is not None and maximum is not None:
            en += f" from {minimum} to {maximum}"
            ru += f" от {minimum} до {maximum}"
        elif minimum is not None:
            en += f" greater than {minimum}"
            ru += f" не меньше {minimum}"
        elif maximum is not None:
            en += f" less than {maximum}"
            ru += f" не больше {maximum}"
        super().__init__(
            functools.partial(self._validate, minimum=minimum, maximum=maximum), _doc(en, ru), _internal_id="Float"
        )

    @staticmethod
    def _validate(value, /, *, minimum=None, maximum=None) -> float:
        try:
            value = float(str(value).strip().replace(",", "."))
        except ValueError:
            raise ValidationError(f"Passed value ({value}) must be a float")
        if minimum is not None and value < minimum:
            raise ValidationError(f"Passed value ({value}) is lower than minimum one")
        if maximum is not None and value > maximum:
            raise ValidationError(f"Passed value ({value}) is greater than maximum one")
        return value


class Choice(Validator):
    def __init__(self, possible_values: list, /):
        possible = " / ".join(map(str, possible_values))
        super().__init__(
            functools.partial(self._validate, possible_values=possible_values),
            _doc(f"one of the following: {possible}", f"одним из: {possible}"),
            _internal_id="Choice",
        )
        self.possible_values = list(possible_values)

    @staticmethod
    def _validate(value, /, *, possible_values) -> typing.Any:
        if value in possible_values:
            return value
        for option in possible_values:              # «1» из .config для варианта 1, «true» для True
            if str(option) == str(value) or str(option).lower() == str(value).strip().lower():
                return option
        raise ValidationError(
            f"Passed value ({value}) is not one of the following: {' / '.join(map(str, possible_values))}"
        )


class MultiChoice(Validator):
    def __init__(self, possible_values: list, /):
        possible = " / ".join(map(str, possible_values))
        super().__init__(
            functools.partial(self._validate, possible_values=possible_values),
            _doc(f"list of values, each one of: {possible}", f"списком значений, каждое из: {possible}"),
            _internal_id="MultiChoice",
        )
        self.possible_values = list(possible_values)

    @staticmethod
    def _validate(value, /, *, possible_values) -> list:
        if not isinstance(value, (list, tuple, set)):
            value = [v.strip() for v in str(value).split(",")] if isinstance(value, str) else [value]
        result = []
        for item in value:
            try:
                result.append(Choice._validate(item, possible_values=possible_values))
            except ValidationError:
                raise ValidationError(
                    f"One of passed values ({item}) is not one of the following: {' / '.join(map(str, possible_values))}"
                )
        return list(dict.fromkeys(result))


class Series(Validator):
    def __init__(self, validator: Validator = None, min_len: int = None, max_len: int = None, fixed_len: int = None):
        en, ru = "series of values", "списком значений"
        if validator is not None:
            en += f", each of which must be {validator.doc.get('en', '')}"
            ru += f", каждое из которых должно быть {validator.doc.get('ru', validator.doc.get('en', ''))}"
        if fixed_len is not None:
            en += f", exactly {fixed_len} items"
            ru += f", ровно {fixed_len} шт."
        elif min_len is not None and max_len is not None:
            en += f", from {min_len} to {max_len} items"
            ru += f", от {min_len} до {max_len} шт."
        elif min_len is not None:
            en += f", at least {min_len} items"
            ru += f", не меньше {min_len} шт."
        elif max_len is not None:
            en += f", up to {max_len} items"
            ru += f", не больше {max_len} шт."
        super().__init__(
            functools.partial(self._validate, validator=validator, min_len=min_len, max_len=max_len, fixed_len=fixed_len),
            _doc(en, ru),
            _internal_id="Series",
        )
        self.validator = validator

    @staticmethod
    def _validate(value, /, *, validator=None, min_len=None, max_len=None, fixed_len=None) -> list:
        if not isinstance(value, (list, tuple, set)):
            value = str(value).split(",")
        value = [item.strip() if isinstance(item, str) else item for item in list(value)]
        if min_len is not None and len(value) < min_len:
            raise ValidationError(f"Passed value ({value}) contains less than {min_len} items")
        if max_len is not None and len(value) > max_len:
            raise ValidationError(f"Passed value ({value}) contains more than {max_len} items")
        if fixed_len is not None and len(value) != fixed_len:
            raise ValidationError(f"Passed value ({value}) must contain exactly {fixed_len} items")
        if isinstance(validator, Validator):
            for i, item in enumerate(value):
                try:
                    value[i] = validator.validate(item)
                except ValidationError:
                    raise ValidationError(
                        f"Passed value ({value}) contains invalid item ({str(item).strip()}), "
                        f"which must be {validator.doc.get('en', '')}"
                    )
        return [item for item in value if item or item == 0 or item is False]


class Link(Validator):
    def __init__(self):
        super().__init__(lambda value: self._validate(value), _doc("link", "ссылкой"), _internal_id="Link")

    @staticmethod
    def _validate(value, /) -> str:
        try:
            ok = bool(urlparse(str(value)).netloc)
        except Exception:
            ok = False
        if not ok:
            raise ValidationError(f"Passed value ({value}) is not a valid URL")
        return str(value)


class String(Validator):
    def __init__(self, length: int = None, min_len: int = None, max_len: int = None):
        en, ru = "string", "строкой"
        if length is not None:
            en += f" of length {length}"
            ru += f" длиной {length}"
        elif min_len is not None and max_len is not None:
            en += f" of length from {min_len} to {max_len}"
            ru += f" длиной от {min_len} до {max_len}"
        elif min_len is not None:
            en += f" of length at least {min_len}"
            ru += f" длиной не меньше {min_len}"
        elif max_len is not None:
            en += f" of length up to {max_len}"
            ru += f" длиной до {max_len}"
        super().__init__(
            functools.partial(self._validate, length=length, min_len=min_len, max_len=max_len),
            _doc(en, ru),
            _internal_id="String",
        )

    @staticmethod
    def _validate(value, /, *, length=None, min_len=None, max_len=None) -> str:
        if isinstance(value, (list, tuple)):
            value = " ".join(map(str, value))
        n = len(_graphemes(str(value)))
        if length is not None and n != length:
            raise ValidationError(f"Passed value ({value}) must be a length of {length}")
        if min_len is not None and n < min_len:
            raise ValidationError(f"Passed value ({value}) must be a length of at least {min_len}")
        if max_len is not None and n > max_len:
            raise ValidationError(f"Passed value ({value}) must be a length of up to {max_len}")
        return str(value)


class RegExp(Validator):
    def __init__(self, regex: str, flags=None, description=None):
        flags = flags or 0
        try:
            re.compile(regex, flags=flags)
        except re.error as e:
            raise Exception(f"{regex} is not a valid regex") from e
        if description is None:
            doc = _doc(f"string matching pattern «{regex}»", f"строкой по шаблону «{regex}»")
        elif isinstance(description, str):
            doc = {lang: description for lang in LANGS}
        else:
            doc = dict(description)
            for lang in LANGS:
                doc.setdefault(lang, doc.get("en") or next(iter(doc.values()), ""))
        super().__init__(functools.partial(self._validate, regex=regex, flags=flags), doc, _internal_id="RegExp")

    @staticmethod
    def _validate(value, /, *, regex, flags) -> str:
        if not re.match(regex, str(value), flags=flags):
            raise ValidationError(f"Passed value ({value}) must follow pattern {regex}")
        return str(value)


class TelegramID(Validator):
    def __init__(self):
        super().__init__(self._validate, _doc("Telegram ID", "Telegram ID"), _internal_id="TelegramID")

    @staticmethod
    def _validate(value, /) -> int:
        try:
            value = int(str(value).strip())
        except Exception:
            raise ValidationError(f"Passed value ({value}) is not a valid telegram id")
        if str(value).startswith("-100"):
            value = int(str(value)[4:])
        if value > 2**64 - 1 or value < -(2**63):
            raise ValidationError(f"Passed value ({value}) is not a valid telegram id")
        return value


class Union(Validator):
    def __init__(self, *validators):
        en = "one of the following:\n" + "\n".join(f"- {v.doc.get('en', '')}" for v in validators)
        ru = "одним из:\n" + "\n".join(f"- {v.doc.get('ru', v.doc.get('en', ''))}" for v in validators)
        super().__init__(functools.partial(self._validate, validators=validators), _doc(en, ru), _internal_id="Union")
        self.validators = validators

    @staticmethod
    def _validate(value, /, *, validators):
        for validator in validators:
            try:
                return validator.validate(value)
            except ValidationError:
                pass
        raise ValidationError(f"Passed value ({value}) is not valid")


class NoneType(Validator):
    def __init__(self):
        super().__init__(self._validate, _doc("empty value", "пустым значением"), _internal_id="NoneType")

    @staticmethod
    def _validate(value, /):
        if value not in (None, "", "None", "none", [], {}):
            raise ValidationError(f"Passed value ({value}) is not None")
        return None


class Hidden(Validator):
    def __init__(self, validator: Validator = None):
        validator = validator or String()
        super().__init__(functools.partial(self._validate, validator=validator), validator.doc, _internal_id="Hidden")
        self.validator = validator

    @staticmethod
    def _validate(value, /, *, validator):
        return validator.validate(value)


class Emoji(Validator):
    def __init__(self, length: int = None, min_len: int = None, max_len: int = None):
        en, ru = "emoji", "эмодзи"
        if length is not None:
            en, ru = f"{length} emojis", f"{length} эмодзи"
        elif min_len is not None and max_len is not None:
            en, ru = f"from {min_len} to {max_len} emojis", f"от {min_len} до {max_len} эмодзи"
        elif min_len is not None:
            en, ru = f"at least {min_len} emojis", f"не меньше {min_len} эмодзи"
        elif max_len is not None:
            en, ru = f"up to {max_len} emojis", f"не больше {max_len} эмодзи"
        super().__init__(
            functools.partial(self._validate, length=length, min_len=min_len, max_len=max_len),
            _doc(en, ru),
            _internal_id="Emoji",
        )

    @staticmethod
    def _validate(value, /, *, length=None, min_len=None, max_len=None) -> str:
        value = str(value)
        parts = _graphemes(value)
        n = len(parts)
        if length is not None and n != length:
            raise ValidationError(f"Passed value ({value}) is not {length} emojis long")
        if min_len is not None and n < min_len:
            raise ValidationError(f"Passed value ({value}) is not at least {min_len} emojis long")
        if max_len is not None and n > max_len:
            raise ValidationError(f"Passed value ({value}) is no more than {max_len} emojis long")
        if not all(_is_emoji(g) for g in parts):
            raise ValidationError(f"Passed value ({value}) is not a valid string with emojis")
        return value


class EntityLike(RegExp):
    def __init__(self):
        super().__init__(
            r"^(?:@|https?://t\.me/)?(?:[a-zA-Z0-9_]{5,32}|[a-zA-Z0-9_]{1,32}\?[a-zA-Z0-9_]{1,32})$|^-?\d+$",
            description=_doc("username, link or ID of a Telegram entity", "юзернеймом, ссылкой или ID сущности Telegram"),
        )
        self.validate = self._validate_entity

    @staticmethod
    def _validate_entity(value, /):
        value = str(value).strip()
        if re.fullmatch(r"-?\d+", value):
            if value.startswith("-100"):
                value = value[4:]
            return int(value)
        value = RegExp._validate(
            value,
            regex=r"^(?:@|https?://t\.me/)?(?:[a-zA-Z0-9_]{5,32}|[a-zA-Z0-9_]{1,32}\?[a-zA-Z0-9_]{1,32})$",
            flags=0,
        )
        value = re.sub(r"^https?://t\.me/", "", value)
        return value if value.startswith("@") else f"@{value}"


class RandomLinkList(list):
    def __str__(self):
        import random

        return str(random.choice(self)) if self else ""

    def __bytes__(self):
        return str(self).encode("utf-8")

    def __repr__(self):
        return list.__repr__(self)


class RandomLink(Series):
    def __init__(self):
        super().__init__(validator=Link(), min_len=1)
        self.internal_id = "Series"
        self.doc = _doc("list of links, one of which is chosen randomly", "списком ссылок, из которых выбирается случайная")
        self.validate = lambda value: RandomLinkList(Series._validate(value, validator=Link(), min_len=1))


__all__ = [
    "ValidationError",
    "Validator",
    "Boolean",
    "Integer",
    "Float",
    "Choice",
    "MultiChoice",
    "Series",
    "Link",
    "String",
    "RegExp",
    "TelegramID",
    "Union",
    "NoneType",
    "Hidden",
    "Emoji",
    "EntityLike",
    "RandomLink",
    "RandomLinkList",
]
