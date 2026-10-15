"""Переводы: строки модулей (self.strings) и служебные строки пространства.

Порядок поиска строки модуля, как в Hikka: внешний языковой пакет (# packurl:) → общий пакет переводов →
strings_<язык> для выбранных языков → strings → «Unknown strings: <ключ>».
Язык хранится в БД: hikka.translations → lang (можно несколько через пробел, по приоритету). По умолчанию — ru.
"""

import json
import logging
import typing
from pathlib import Path

logger = logging.getLogger(__name__)

DB_KEY = "hikka.translations"
DEFAULT_LANG = "ru"

SUPPORTED_LANGUAGES = {
    "en": "🇬🇧 English",
    "ru": "🇷🇺 Русский",
    "uk": "🇺🇦 Українська",
    "de": "🇩🇪 Deutsch",
    "ja": "🇯🇵 日本語",
}
LANGUAGE_ALIASES = {"ua": "uk", "jp": "ja"}
LANGUAGE_COMPAT_ALIASES = {"uk": ("ua",), "ja": ("jp",)}
MEME_LANGUAGES = {"leet": "🏴‍☠️ 1337", "uwu": "🏴‍☠️ UwU", "tiktok": "🏴‍☠️ TikTokKid", "neofit": "🏴‍☠️ Neofit"}

# Служебные строки пространства (ключи как в языковых пакетах Hikka: "<раздел>.<ключ>")
CORE_STRINGS = {
    "en": {
        "inline.opening_form": " <b>Opening form...</b>",
        "inline.opening_gallery": " <b>Opening gallery...</b>",
        "inline.opening_list": " <b>Opening list...</b>",
        "inline.inline403": "🚫 <b>You can't send inline units in this chat</b>",
        "inline.invoke_failed": "<b>🚫 Unit invoke failed! More info in logs</b>",
        "inline.invoke_failed_logs": "<b>🚫 Unit invoke failed! More info in detailed logs</b>\n\n<pre><code class=\"language-python\">{}</code></pre>",
        "inline.button403": "You are not allowed to press this button!",
        "inline.keep_id": "⚠️ Do not remove ID! {}",
        "inline.command": "🎹 Command «{}»",
        "inline.command_msg": "🌘 <b>Command «{}»</b>\n\n<i>{}</i>",
        "inline.run_command": "🏌️ Run command",
        "inline.show_inline_cmds": "📄 Show all available inline commands",
        "inline.no_inline_cmds": "You have no available commands",
        "inline.no_inline_cmds_msg": "<b>😔 There are no available inline commands or you lack access to them</b>",
        "inline.inline_cmds": "ℹ️ You have {} available command(-s)",
        "inline.inline_cmds_msg": "<b>ℹ️ Available inline commands:</b>\n\n{}",
        "translations.too_long": "<b>📄 Command output seems to be too long, so it's sent in file.</b>",
        "translations.fw_error": "<b>⏳ Call</b> <code>{}</code> <b>caused FloodWait of {} on method</b> <code>{}</code>",
        "translations.rpc_error": "<b>🚫 Call</b> <code>{}</code> <b>failed due to RPC error:</b> <code>{}</code>",
        "translations.requested_join": "💫 <b>Module</b> <code>{}</code> <b>requested to join channel <a href=\"https://t.me/{}\">{}</a></b>\n\n<b>❓ Reason:</b> <i>{}</i>",
        "translations.lang_saved": "{} <b>Language saved!</b>",
        "translations.pack_saved": "<b>✅ Translate pack saved!</b>",
        "translations.incorrect_language": "🚫 <b>Incorrect language specified</b>",
        "translations.lang_removed": "<b>✅ Translations reset to default ones</b>",
        "translations.check_pack": "🚫 <b>Invalid pack format in url</b>",
        "translations.check_url": "🚫 <b>You need to specify valid url containing a langpack</b>",
        "translations.choose_language": "🗽 <b>Choose language</b>",
        "translations.not_official": "<b>⚠️ This language is not officially supported</b>",
    },
    "ru": {
        "inline.opening_form": " <b>Открываю форму...</b>",
        "inline.opening_gallery": " <b>Открываю галерею...</b>",
        "inline.opening_list": " <b>Открываю список...</b>",
        "inline.inline403": "🚫 <b>В этом чате нельзя отправлять инлайн-сообщения</b>",
        "inline.invoke_failed": "<b>🚫 Не удалось отправить инлайн-сообщение. Подробности — в логах</b>",
        "inline.invoke_failed_logs": "<b>🚫 Не удалось отправить инлайн-сообщение</b>\n\n<pre><code class=\"language-python\">{}</code></pre>",
        "inline.button403": "Вам нельзя нажимать эту кнопку!",
        "inline.keep_id": "⚠️ Не удаляйте ID! {}",
        "inline.command": "🎹 Команда «{}»",
        "inline.command_msg": "🌘 <b>Команда «{}»</b>\n\n<i>{}</i>",
        "inline.run_command": "🏌️ Выполнить",
        "inline.show_inline_cmds": "📄 Все доступные инлайн-команды",
        "inline.no_inline_cmds": "Нет доступных команд",
        "inline.no_inline_cmds_msg": "<b>😔 Нет доступных инлайн-команд или к ним нет доступа</b>",
        "inline.inline_cmds": "ℹ️ Доступно команд: {}",
        "inline.inline_cmds_msg": "<b>ℹ️ Доступные инлайн-команды:</b>\n\n{}",
        "translations.too_long": "<b>📄 Вывод команды слишком длинный — отправляю файлом.</b>",
        "translations.fw_error": "<b>⏳ Команда</b> <code>{}</code> <b>словила FloodWait на {} в методе</b> <code>{}</code>",
        "translations.rpc_error": "<b>🚫 Команда</b> <code>{}</code> <b>упала с ошибкой Telegram:</b> <code>{}</code>",
        "translations.requested_join": "💫 <b>Модуль</b> <code>{}</code> <b>просит вступить в канал <a href=\"https://t.me/{}\">{}</a></b>\n\n<b>❓ Причина:</b> <i>{}</i>",
        "translations.lang_saved": "{} <b>Язык сохранён!</b>",
        "translations.pack_saved": "<b>✅ Пакет переводов сохранён!</b>",
        "translations.incorrect_language": "🚫 <b>Неверный язык</b>",
        "translations.lang_removed": "<b>✅ Переводы сброшены</b>",
        "translations.check_pack": "🚫 <b>Неверный формат пакета</b>",
        "translations.check_url": "🚫 <b>Укажите ссылку на языковой пакет</b>",
        "translations.choose_language": "🗽 <b>Выберите язык</b>",
        "translations.not_official": "<b>⚠️ Этот язык официально не поддерживается</b>",
    },
}


def normalize_language(language: str) -> str:
    return LANGUAGE_ALIASES.get(language, language)


def iter_language_codes(language: str) -> typing.Iterator[str]:
    language = normalize_language(language)
    yield language
    yield from LANGUAGE_COMPAT_ALIASES.get(language, ())


def fmt(text: str, kwargs: dict) -> str:
    for key, value in kwargs.items():
        text = text.replace(f"{{{key}}}", str(value))
    return text


def _load_yaml(text: str):
    try:
        from ruamel.yaml import YAML

        return YAML(typ="safe").load(text)
    except ImportError:
        pass
    try:
        import yaml

        return yaml.safe_load(text)
    except ImportError:
        pass
    return json.loads(text)


def _flatten(pack: dict, prefix: str) -> dict:
    """{module: {key: value}} → {"<prefix><module>.<key>": value}; модули с $ — без префикса."""
    out = {}
    for module, strings in (pack or {}).items():
        if not isinstance(strings, dict):
            continue
        for key, value in strings.items():
            if key == "name":
                continue
            full = f"{module.strip('$')}.{key}" if str(module).startswith("$") else f"{prefix}{module}.{key}"
            out[full] = value
    return out


class BaseTranslator:
    _data: dict = {}

    def getkey(self, key: str):
        return self._data.get(key, False)

    def gettext(self, text: str):
        return self.getkey(text) or text

    async def load_module_translations(self, pack_url: str, cache_path: Path = None):
        from . import utils

        data = None
        content = None
        try:
            content = await utils.fetch_text(pack_url)
            data = _load_yaml(content)
        except Exception:
            logger.debug("Не удалось скачать языковой пакет %s", pack_url, exc_info=True)
        if not isinstance(data, dict):
            if cache_path and Path(cache_path).exists():
                try:
                    data = _load_yaml(Path(cache_path).read_text(encoding="utf-8"))
                except Exception:
                    return {}
            if not isinstance(data, dict):
                return {}
        elif cache_path and content is not None:
            try:
                Path(cache_path).parent.mkdir(parents=True, exist_ok=True)
                Path(cache_path).write_text(content, encoding="utf-8")
            except Exception:
                pass
        if any(len(str(key)) != 2 for key in data):
            return data
        for language in (self.db.get(DB_KEY, "lang", DEFAULT_LANG) or DEFAULT_LANG).split():
            for code in iter_language_codes(language):
                if code in data:
                    return data[code]
        return data.get("en", {})


class Translator(BaseTranslator):
    def __init__(self, client, db):
        self._client = client
        self.db = db
        self._data = {}
        self.raw_data = {}

    def _langs(self):
        return [normalize_language(x) for x in (self.db.get(DB_KEY, "lang", DEFAULT_LANG) or DEFAULT_LANG).split()]

    async def init(self) -> bool:
        self._data = dict(CORE_STRINGS["en"])
        self.raw_data = {lang: dict(strings) for lang, strings in CORE_STRINGS.items()}
        any_ = False
        for language in reversed(self._langs()):
            if language in CORE_STRINGS:
                self._data.update(CORE_STRINGS[language])
                any_ = True
            elif language.startswith(("http://", "https://")):
                try:
                    from . import utils

                    pack = _load_yaml(await utils.fetch_text(language))
                    flat = _flatten(pack, "hikka.modules.") if isinstance(pack, dict) else {}
                    self._data.update(flat)
                    self.raw_data[language] = flat
                    any_ = True
                except Exception:
                    logger.exception("Не удалось загрузить языковой пакет %s", language)
        return any_

    def init_sync(self):
        self._data = dict(CORE_STRINGS["en"])
        self.raw_data = {lang: dict(strings) for lang, strings in CORE_STRINGS.items()}
        for language in reversed(self._langs()):
            if language in CORE_STRINGS:
                self._data.update(CORE_STRINGS[language])


class ExternalTranslator(BaseTranslator):
    def __init__(self):
        self.data = {lang: {k: v for k, v in strings.items()} for lang, strings in CORE_STRINGS.items()}

    def get(self, key: str, lang: str) -> str:
        return self.data.get(lang, {}).get(key) or key

    def getdict(self, key: str, **kwargs) -> dict:
        return {lang: fmt(self.data[lang].get(key) or key, kwargs) for lang in self.data}


class Strings:
    """self.strings модуля: strings["key"], strings("key"), strings.get("key", "ru")."""

    def __init__(self, mod, translator):
        self._mod = mod
        self._translator = translator
        base = mod.strings
        if isinstance(base, Strings):
            base = base._base_strings
        self._base_strings = base if isinstance(base, dict) else {}
        self.external_strings = {}

    def _languages(self):
        if self._translator is None:
            return ["en"]
        langs = []
        for original in (self._translator.db.get(DB_KEY, "lang", DEFAULT_LANG) or DEFAULT_LANG).split():
            langs.extend(iter_language_codes(original))
            if original in ("leet", "uwu", "neofit"):
                langs.append("en")
            elif original == "tiktok":
                langs.append("ru")
        return langs

    def get(self, key: str, lang: str = None) -> str:
        if lang:
            for code in iter_language_codes(lang):
                strings = getattr(self._mod, f"strings_{code}", None)
                if isinstance(strings, dict) and key in strings:
                    return strings[key]
                raw = getattr(self._translator, "raw_data", {}).get(code, {}) if self._translator else {}
                value = raw.get(f"{self._mod.__module__}.{key}")
                if value:
                    return value
        return self[key]

    def __getitem__(self, key: str) -> str:
        value = self.external_strings.get(key)
        if value:
            return value
        if self._translator is not None:
            value = self._translator.getkey(f"{self._mod.__module__}.{key}")
            if value:
                return value
        for lang in self._languages():
            strings = getattr(self._mod, f"strings_{lang}", None)
            if isinstance(strings, dict) and strings.get(key):
                return strings[key]
        value = self._base_strings.get(key)
        if value is not None:
            return value
        return f"Unknown strings: {key}"

    def __call__(self, key: str, _=None) -> str:
        return self[key]

    def __iter__(self):
        return iter(self._base_strings)

    def __contains__(self, key):
        return key in self._base_strings or any(
            isinstance(getattr(self._mod, f"strings_{lang}", None), dict) and key in getattr(self._mod, f"strings_{lang}")
            for lang in self._languages()
        )

    def keys(self):
        return self._base_strings.keys()

    def items(self):
        return ((k, self[k]) for k in self._base_strings)

    def __repr__(self):
        return f"Strings({self._mod.__class__.__name__})"


translator = ExternalTranslator()
