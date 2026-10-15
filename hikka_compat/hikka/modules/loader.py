"""Встроенный модуль пространства Hikka: установка, выгрузка и обновление модулей Hikka/Heroku."""

import asyncio
import contextlib
import difflib
import io
import logging
import re
import time
import typing
from urllib.parse import urlparse

from telethon.tl.types import Message

from .. import _frx, loader, utils
from .._local_storage import RemoteStorage
from ..types import CoreOverwriteError, LoadError, SelfSuspend, SelfUnload

logger = logging.getLogger(__name__)

DEFAULT_REPOS = [
    "https://raw.githubusercontent.com/coddrago/modules/main",
    "https://github.com/hikariatama/host/raw/master",
    "https://github.com/MoriSummerz/ftg-mods/raw/main",
    "https://raw.githubusercontent.com/AmoreForever/amoremods/master",
    "https://raw.githubusercontent.com/vsecoder/hikka_modules/main",
    "https://raw.githubusercontent.com/MuRuLOSE/HikkaModulesRepo/main",
    "https://raw.githubusercontent.com/fajox1/famods/main",
    "https://raw.githubusercontent.com/anon97945/hikka-mods/master",
    "https://raw.githubusercontent.com/sqlmerr/hikka_mods/main",
    "https://raw.githubusercontent.com/KorenbZla/HikkaModules/main",
    "https://raw.githubusercontent.com/Den4ikSuperOstryyPer4ik/Astro-modules/main",
    "https://raw.githubusercontent.com/N3rcy/modules/main",
]


@loader.tds
class LoaderMod(loader.Module):
    """Установка и выгрузка модулей Hikka / Heroku"""

    strings = {
        "name": "Loader",
        "repo_config_doc": "Основной репозиторий модулей",
        "add_repo_config_doc": "Дополнительные репозитории",
        "basic_auth_doc": "Логин:пароль для закрытого репозитория (необязательно)",
        "share_link_doc": "Показывать ссылку на модуль после установки",
        "args": "🚫 <b>Укажите ссылку или название модуля</b>",
        "no_module": "🚫 <b>Модуль не найден ни в одном репозитории</b>",
        "download_failed": "🚫 <b>Не удалось скачать модуль</b>\n<code>{}</code>",
        "suggest": "🚫 <b>Модуль не найден.</b> Возможно, вы имели в виду: {}",
        "installing": "🌘 <b>Устанавливаю</b> <code>{}</code><b>...</b>",
        "downloading": "🌘 <b>Скачиваю</b> <code>{}</code><b>...</b>",
        "provide_module": "🚫 <b>Ответьте на файл модуля (.py) или приложите его к команде</b>",
        "bad_unicode": "🚫 <b>Файл — не текст в UTF-8</b>",
        "load_failed": "🚫 <b>Модуль не загрузился</b>\n\n<b>Причина:</b> <code>{}</code>",
        "load_failed_tb": "🚫 <b>Модуль не загрузился</b>\n\n<pre><code class=\"language-python\">{}</code></pre>",
        "requirements_failed": "🚫 <b>Не удалось поставить пакеты:</b> <code>{}</code>",
        "requirements_installing": "🌘 <b>Ставлю пакеты:</b> <code>{}</code>",
        "core_overwrite": "🚫 <b>{} — встроенный, его нельзя перезаписать</b>",
        "inline_init_failed": "🚫 <b>Модулю нужен инлайн-бот, а стационарный бот FRX не запущен</b>",
        "ffmpeg_required": "🚫 <b>Модулю нужен ffmpeg — он не найден</b>",
        "loaded": "🌘 <b>Модуль</b> <code>{}</code>{} <b>загружен</b>{}\n{}{}",
        "by": " <b>от</b> {}",
        "version": " <i>(v{})</i>",
        "cmd_line": "\n▫️ <code>{}{}</code> {}",
        "inline_line": "\n🎹 <code>@{} {}</code> {}",
        "link": "\n🔗 <a href=\"{}\">Ссылка на модуль</a>",
        "unloaded": "🗑 <b>Модуль</b> <code>{}</code> <b>выгружен</b>",
        "not_unloaded": "🚫 <b>Модуль не найден</b>",
        "cannot_unload_core": "🚫 <b>{} — встроенный модуль, выгрузить нельзя</b>",
        "self_unload": "ℹ️ <b>Модуль сам отменил загрузку</b>{}",
        "self_suspend": "ℹ️ <b>Модуль загружен, но приостановил себя</b>{}",
        "all_modules_deleted": "🗑 <b>Все модули удалены</b> (загружено было: {})",
        "confirm_clear": "⚠️ <b>Удалить все установленные модули Hikka?</b>",
        "yes": "🗑 Удалить",
        "no": "🚫 Отмена",
        "repo_added": "✅ <b>Репозиторий добавлен:</b> {}",
        "repo_exists": "ℹ️ <b>Репозиторий уже есть:</b> {}",
        "repo_bad": "🚫 <b>Это не репозиторий:</b> по адресу нет full.txt",
        "repo_deleted": "✅ <b>Репозиторий удалён:</b> {}",
        "repo_not_found": "🚫 <b>Такого репозитория нет в списке</b>",
        "repos": "📚 <b>Репозитории модулей</b>\n\n{}\n\n<i>{}addrepo &lt;ссылка&gt; / {}delrepo &lt;ссылка или №&gt;</i>",
        "repo_list": "📚 <b>Модули в репозиториях</b> (всего: {})\n\n{}",
        "send_module": "📄 <b>Модуль</b> <code>{}</code>",
        "no_file": "🚫 <b>У этого модуля нет файла (он встроенный)</b>",
        "updating": "🔄 <b>Обновляю модули из репозиториев...</b>",
        "updated": "✅ <b>Обновлено:</b> {}\n{}",
        "nothing_to_update": "ℹ️ <b>Модулей, установленных по ссылке, нет</b>",
        "requires": "\n📦 <b>Пакеты:</b> <code>{}</code>",
    }

    def __init__(self):
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "MODULES_REPO",
                DEFAULT_REPOS[0],
                lambda: self.strings("repo_config_doc"),
                validator=loader.validators.Link(),
            ),
            loader.ConfigValue(
                "ADDITIONAL_REPOS",
                DEFAULT_REPOS[1:],
                lambda: self.strings("add_repo_config_doc"),
                validator=loader.validators.Series(validator=loader.validators.Link()),
            ),
            loader.ConfigValue(
                "basic_auth", None, lambda: self.strings("basic_auth_doc"),
                validator=loader.validators.Hidden(loader.validators.RegExp(r"^.*:.*$")),
            ),
            loader.ConfigValue("share_link", True, lambda: self.strings("share_link_doc"),
                               validator=loader.validators.Boolean()),
        )
        self._links_cache: dict = {}
        self._storage = RemoteStorage()
        self.fully_loaded = False

    async def client_ready(self):
        self.fully_loaded = True

    @loader.loop(interval=3, wait_before=True, autostart=True)
    async def _config_autosaver(self):
        """Изменённые значения конфигов модулей и библиотек → БД."""
        for owner, getter in [(m, m.pointer) for m in self.allmodules.modules] + [
            (lib, lib._lib_pointer) for lib in self.allmodules.libraries
        ]:
            config = getattr(owner, "config", None)
            if not isinstance(config, loader.ModuleConfig) or not config:
                continue
            for option, value in config._config.items():
                if not hasattr(value, "_save_marker"):
                    continue
                with contextlib.suppress(Exception):
                    delattr(value, "_save_marker")
                if utils.is_serializable(value.value):
                    with contextlib.suppress(Exception):
                        getter("__config__", {})[option] = value.value

    # ------------------------------------------------------------------ репозитории
    def _repos(self) -> typing.List[str]:
        repos = [self.config["MODULES_REPO"]] + list(self.config["ADDITIONAL_REPOS"] or [])
        return [r.strip("/") for r in dict.fromkeys(repos) if r and str(r).startswith("http")]

    async def _get_repo(self, repo: str) -> typing.List[str]:
        repo = repo.strip("/")
        cached = self._links_cache.get(repo)
        if cached and cached["exp"] >= time.time():
            return cached["data"]
        try:
            auth = self.config["basic_auth"]
            headers = None
            if auth:
                import base64

                headers = {"Authorization": "Basic " + base64.b64encode(auth.encode()).decode()}
            text = await utils.fetch_text(f"{repo}/full.txt", headers=headers, timeout=15)
        except Exception:
            logger.debug("Репозиторий %s недоступен", repo)
            return cached["data"] if cached else []
        data = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
        self._links_cache[repo] = {"exp": time.time() + 300, "data": data}
        return data

    async def get_repo_list(self, only_primary: bool = False) -> dict:
        repos = self._repos()[:1] if only_primary else self._repos()
        lists = await asyncio.gather(*(self._get_repo(r) for r in repos))
        return {
            repo: {f"Mod/{rid}/{i}": f"{repo}/{name}.py" for i, name in enumerate(dict.fromkeys(names))}
            for rid, (repo, names) in enumerate(zip(repos, lists))
        }

    async def get_links_list(self) -> typing.List[str]:
        links = []
        for repo_links in (await self.get_repo_list()).values():
            links += list(repo_links.values())
        return links

    async def _find_link(self, module_name: str):
        name = module_name.lower().removesuffix(".py")
        for link in await self.get_links_list():
            if link.lower().endswith(f"/{name}.py"):
                return link
        return False

    async def _suggest(self, module_name: str) -> list:
        names = {link.rsplit("/", 1)[-1][:-3]: link for link in await self.get_links_list()}
        return difflib.get_close_matches(module_name, list(names), n=5, cutoff=0.55)

    # ------------------------------------------------------------------ установка
    async def download_and_install(self, module_name: str, message: Message = None, force_pm: bool = False):
        try:
            module_name = module_name.strip()
            if urlparse(module_name).netloc:
                url = module_name
                if re.match(r"^https://github\.com/.*?/.*?/blob/.*\.py$|^https://gitlab\.com/.*?/.*?/-/blob/.*\.py$", url):
                    url = url.replace("/blob/", "/raw/")
            else:
                url = await self._find_link(module_name)
                if not url:
                    if message is not None:
                        suggestions = await self._suggest(module_name)
                        await utils.answer(
                            message,
                            self.strings("suggest").format(", ".join(f"<code>{s}</code>" for s in suggestions))
                            if suggestions
                            else self.strings("no_module"),
                        )
                    return False
            if message is not None:
                message = (await utils.answer(message, self.strings("downloading").format(utils.escape_html(url)))) or message
            try:
                source = await self._storage.fetch(url, auth=None)
            except Exception as e:
                if message is not None:
                    await utils.answer(message, self.strings("download_failed").format(utils.escape_html(str(e))))
                return False
            return await self.load_module(source, message, url.rstrip("/").rsplit("/", 1)[-1], url, save_fs=True)
        except Exception:
            logger.exception("Не удалось установить %s", module_name)
            return False

    async def load_module(
        self,
        doc: str,
        message: Message = None,
        name: str = None,
        origin: str = "<string>",
        did_requirements: bool = False,
        save_fs: bool = True,
        blob_link: bool = False,
    ):
        url = origin if utils.check_url(origin) else None
        if isinstance(doc, bytes):
            doc = doc.decode("utf-8")

        if any(line.replace(" ", "") == "#scope:inline" for line in doc.splitlines()) and not self.inline.init_complete:
            if isinstance(message, Message):
                await utils.answer(message, self.strings("inline_init_failed"))
            return False

        if any(line.replace(" ", "") == "#scope:ffmpeg" for line in doc.splitlines()):
            import shutil

            if not shutil.which("ffmpeg"):
                if isinstance(message, Message):
                    await utils.answer(message, self.strings("ffmpeg_required"))
                return False

        reqs = loader.parse_requirements(doc)
        if reqs and isinstance(message, Message):
            with contextlib.suppress(Exception):
                message = (await utils.answer(message, self.strings("requirements_installing").format(", ".join(reqs)))) or message

        file_name = None
        if name:
            file_name = name if name.endswith(".py") else f"{name}.py"
        try:
            instance = await self.allmodules.load_source(
                doc, origin="<string>", save_fs=save_fs, file_name=file_name, from_dlmod=True, url=url
            )
        except CoreOverwriteError as e:
            if isinstance(message, Message):
                await utils.answer(message, self.strings("core_overwrite").format(utils.escape_html(e.target)))
            return False
        except SelfUnload as e:
            if isinstance(message, Message):
                await utils.answer(message, self.strings("self_unload").format(f": {utils.escape_html(str(e))}" if str(e) else ""))
            return False
        except SelfSuspend as e:
            if isinstance(message, Message):
                await utils.answer(message, self.strings("self_suspend").format(f": {utils.escape_html(str(e))}" if str(e) else ""))
            return False
        except LoadError as e:
            if isinstance(message, Message):
                await utils.answer(message, self.strings("load_failed").format(utils.escape_html(str(e))))
            return False
        except ImportError as e:
            logger.exception("Модулю не хватает пакетов")
            if isinstance(message, Message):
                await utils.answer(message, self.strings("requirements_failed").format(utils.escape_html(str(e))))
            return False
        except Exception as e:
            logger.exception("Модуль не загрузился")
            if isinstance(message, Message):
                import traceback

                tb = "".join(traceback.format_exception(type(e), e, e.__traceback__)).strip().splitlines()[-12:]
                await utils.answer(message, self.strings("load_failed_tb").format(utils.escape_html("\n".join(tb))))
            return False

        if isinstance(message, Message):
            await utils.answer(message, self._loaded_text(instance, url))
        return instance

    def _loaded_text(self, instance, url=None) -> str:
        prefix = self.get_prefix()
        lines = ""
        for name, func in instance.commands.items():
            doc = utils.escape_html(self._doc(func))
            lines += self.strings("cmd_line").format(
                prefix, name, (doc if doc.startswith(("<", "[")) else f"— {doc}") if doc else ""
            )
        for name, func in instance.inline_handlers.items():
            doc = utils.escape_html(self._doc(func))
            lines += self.strings("inline_line").format(self.inline.bot_username or "bot", name, f"— {doc}" if doc else "")
        version = getattr(instance, "__version__", None)
        version = self.strings("version").format(".".join(map(str, version))) if isinstance(version, tuple) else ""
        dev = None
        for line in (getattr(instance, "__source__", "") or "").splitlines():
            m = re.match(r"^\s*# ?meta developer: ?(.+)$", line)
            if m:
                dev = m.group(1).strip()
                break
        by = self.strings("by").format(utils.escape_html(dev)) if dev else ""
        link = self.strings("link").format(url) if url and self.config["share_link"] else ""
        doc = utils.escape_html((instance.__doc__ or "").strip()) if instance.__doc__ else ""
        return self.strings("loaded").format(
            utils.escape_html(getattr(instance, "name", instance.__class__.__name__)), version, by,
            f"\n<i>{doc}</i>" if doc else "", lines,
        ) + link

    def _doc(self, func) -> str:
        lang = (self._db.get("hikka.translations", "lang", "ru") or "ru").split()[0]
        doc = " ".join(str(getattr(func, f"{lang}_doc", None) or func.__doc__ or "").split())
        return re.sub(r"^[-—–]\s*", "", doc)[:200]

    async def install_requirements(self, requirements: list) -> bool:
        return await loader.install_requirements(requirements)

    async def approve_internal(self, call, channel, event):
        await self._approve_join(call, channel, event)

    async def _send_stats(self, *args, **kwargs):
        """Статистика Hikka не отправляется."""

    # ------------------------------------------------------------------ команды
    @loader.command(alias="dlm", ru_doc="<ссылка | название> — установить модуль из репозитория или по ссылке")
    async def dlmod(self, message: Message):
        """<link | name> - install module from repo or link"""
        args = utils.get_args_raw(message)
        if not args:
            await self._show_repo(message)
            return
        await self.download_and_install(args.split()[0], message)

    async def _show_repo(self, message):
        repos = await self.get_repo_list()
        pages, block, total = [], "", 0
        for repo, links in repos.items():
            if not links:
                continue
            names = sorted({link.rsplit("/", 1)[-1][:-3] for link in links.values()}, key=str.lower)
            total += len(names)
            chunk = f"<b>{utils.escape_html(urlparse(repo).path.strip('/') or repo)}</b>\n" + " ".join(
                f"<code>{n}</code>" for n in names
            ) + "\n\n"
            if len(block) + len(chunk) > 3000 and block:
                pages.append(block)
                block = ""
            block += chunk
        if block:
            pages.append(block)
        if not pages:
            await utils.answer(message, self.strings("no_module"))
            return
        pages = [self.strings("repo_list").format(total, p) + f"\n<i>{self.get_prefix()}dlm &lt;название&gt;</i>" for p in pages]
        if len(pages) == 1 or not self.inline.init_complete:
            await utils.answer(message, pages[0])
        else:
            await self.inline.list(message, pages)

    @loader.command(alias="lm", ru_doc="ответом на .py — загрузить модуль из файла")
    async def loadmod(self, message: Message):
        """reply to .py file - load module from file"""
        target = message
        if not getattr(message, "file", None):
            target = await message.get_reply_message()
        if not target or not getattr(target, "file", None):
            await utils.answer(message, self.strings("provide_module"))
            return
        data = await target.download_media(bytes)
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError:
            await utils.answer(message, self.strings("bad_unicode"))
            return
        name = getattr(target.file, "name", None) or "module.py"
        await self.load_module(source, message, name, "<file>", save_fs=True)

    @loader.command(alias="ulm", ru_doc="<модуль> — выгрузить модуль")
    async def unloadmod(self, message: Message):
        """<module> - unload module"""
        args = utils.get_args_raw(message)
        if not args:
            await utils.answer(message, self.strings("not_unloaded"))
            return
        target = self.allmodules.lookup(args.strip())
        shown = getattr(target, "name", None) if target else None
        try:
            worked = await self.allmodules.unload_module(args.strip())
        except loader.CoreUnloadError as e:
            await utils.answer(message, self.strings("cannot_unload_core").format(utils.escape_html(e.module)))
            return
        if not worked:
            await utils.answer(message, self.strings("not_unloaded"))
            return
        await utils.answer(message, self.strings("unloaded").format(utils.escape_html(shown or ", ".join(worked))))

    @loader.command(ru_doc="удалить все установленные модули")
    async def clearmodules(self, message: Message):
        """delete all installed modules"""
        await self.inline.form(
            self.strings("confirm_clear"),
            message,
            reply_markup=[
                {"text": self.strings("yes"), "callback": self._clear_confirmed},
                {"text": self.strings("no"), "action": "close"},
            ],
        )

    async def _clear_confirmed(self, call):
        names = [m.__class__.__name__ for m in self.allmodules.modules if not str(m.__origin__).startswith("<core")]
        for name in names:
            with contextlib.suppress(Exception):
                await self.allmodules.unload_module(name)
        await call.edit(self.strings("all_modules_deleted").format(len(names)))

    @loader.command(ru_doc="<ссылка> — добавить репозиторий модулей")
    async def addrepo(self, message: Message):
        """<link> - add modules repo"""
        args = (utils.get_args_raw(message) or "").strip().rstrip("/")
        if not args:
            lines = "\n".join(f"{i}. {utils.escape_html(r)}" for i, r in enumerate(self._repos(), 1))
            await utils.answer(message, self.strings("repos").format(lines, self.get_prefix(), self.get_prefix()))
            return
        if not args.startswith("http"):
            args = f"https://{args}"
        if args in self._repos():
            await utils.answer(message, self.strings("repo_exists").format(utils.escape_html(args)))
            return
        if not await self._get_repo(args):
            await utils.answer(message, self.strings("repo_bad"))
            return
        self.config["ADDITIONAL_REPOS"] = list(self.config["ADDITIONAL_REPOS"] or []) + [args]
        self._save_config()
        await utils.answer(message, self.strings("repo_added").format(utils.escape_html(args)))

    @loader.command(ru_doc="<ссылка | №> — убрать репозиторий")
    async def delrepo(self, message: Message):
        """<link | number> - remove repo"""
        args = (utils.get_args_raw(message) or "").strip().rstrip("/")
        repos = list(self.config["ADDITIONAL_REPOS"] or [])
        target = None
        if args.isdigit():
            allr = self._repos()
            idx = int(args) - 1
            if 0 <= idx < len(allr):
                target = allr[idx]
        else:
            target = next((r for r in repos if r.rstrip("/") == args), None)
        if not target or target.rstrip("/") not in [r.rstrip("/") for r in repos]:
            await utils.answer(message, self.strings("repo_not_found"))
            return
        self.config["ADDITIONAL_REPOS"] = [r for r in repos if r.rstrip("/") != target.rstrip("/")]
        self._save_config()
        await utils.answer(message, self.strings("repo_deleted").format(utils.escape_html(target)))

    def _save_config(self):
        cfg = self._db.get(self.__class__.__name__, "__config__", {})
        for key in self.config:
            cfg[key] = self.config[key]
        self._db.set(self.__class__.__name__, "__config__", cfg)

    @loader.command(alias="ml", ru_doc="<модуль> — прислать файл модуля")
    async def sendmod(self, message: Message):
        """<module> - send module file"""
        args = utils.get_args_raw(message)
        mod = self.allmodules.lookup(args) if args else None
        if not mod:
            await utils.answer(message, self.strings("not_unloaded"))
            return
        if str(mod.__origin__).startswith("<core"):
            await utils.answer(message, self.strings("no_file"))
            return
        file = io.BytesIO((mod.__source__ or "").encode("utf-8"))
        file.name = f"{mod.__class__.__name__}.py"
        url = getattr(mod, "__hikka_url__", None)
        caption = self.strings("send_module").format(utils.escape_html(mod.name)) + (
            self.strings("link").format(url) if url else ""
        )
        await utils.answer_file(message, file, caption=caption)

    @loader.command(ru_doc="обновить модули, установленные по ссылке")
    async def updmods(self, message: Message):
        """update modules installed from links"""
        sources = self._db.get("hikka.compat", "sources", {})
        targets = [(cls, meta["url"]) for cls, meta in sources.items() if isinstance(meta, dict) and meta.get("url")]
        if not targets:
            await utils.answer(message, self.strings("nothing_to_update"))
            return
        message = (await utils.answer(message, self.strings("updating"))) or message
        done, failed = [], []
        for cls, url in targets:
            mod = self.allmodules.lookup(cls)
            try:
                source = await utils.fetch_text(url)
            except Exception:
                failed.append(cls)
                continue
            if mod and getattr(mod, "__source__", None) == source:
                continue
            result = await self.load_module(source, None, url.rsplit("/", 1)[-1], url, save_fs=True)
            (done if result else failed).append(cls)
        await utils.answer(
            message,
            self.strings("updated").format(
                ", ".join(f"<code>{d}</code>" for d in done) or "—",
                ("🚫 <b>Не обновились:</b> " + ", ".join(f"<code>{f}</code>" for f in failed)) if failed else "",
            ),
        )
