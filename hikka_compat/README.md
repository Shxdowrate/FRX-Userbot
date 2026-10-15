# hikka_compat — модули Hikka и Heroku в FRX

Здесь лежит всё, что нужно модулям [Hikka](https://github.com/hikariatama/Hikka) и
[Heroku](https://github.com/coddrago/Heroku), чтобы они работали внутри FRX. Ядро FRX (`core/`) при этом не меняется.
Пространство запускает встроенный модуль `modules/builtin/HikkaCompat.py`.

Это независимая реализация публичного API Hikka/Heroku. Их код (AGPL-3.0) сюда не скопирован, поэтому
FRX остаётся под своей лицензией (MIT).

## Как это выглядит для пользователя

- У модулей Hikka свой префикс (по умолчанию `,`), свои команды и свой список модулей:
  `,help`, `,dlm <название | ссылка>`, `,lm` (ответом на файл), `,ulm <модуль>`, `,config`, `,setprefix`,
  `,addalias`, `,e <код>`, `,logs`, `,security`…
- Команды FRX (`.help`, `.dlm`, …) и Hikka (`,help`, `,dlm`, …) друг другу не мешают. Если сделать префиксы
  одинаковыми, одноимённые команды сработают в обоих местах.
- Инлайн-формы, галереи, списки и кнопки модулей Hikka отправляет стационарный бот FRX. Без бота модули
  работают, а формы и длинные ответы заменяются текстом или файлом.
- Управление из FRX: `.hikka` (состояние), `.hikka on|off|restart`, `.hikka prefix <символ>`,
  `.hikka mods`, `.hikka errors`.

## Где что лежит

```
hikka_compat/                  код (обновляется вместе с FRX)
└── hikka/                     пакет `hikka`, его же видят как `heroku`
    ├── loader.py              Module, декораторы (command, watcher, loop, inline_handler, …), ModuleConfig,
    │                          ConfigValue, реестр модулей Modules (self.allmodules / client.loader)
    ├── types.py               Module, Library, ConfigValue/ModuleConfig, исключения LoadError/SelfUnload/…
    ├── utils.py               utils.answer, get_args_raw, escape_html, asset_channel, smart_split, …
    ├── validators.py          loader.validators.*
    ├── security.py            права на команды (owner, group_admin, unrestricted, …)
    ├── database.py            self.db / self._db (get/set/pointer), pointers.py — PointerList/PointerDict
    ├── translations.py        self.strings, strings_<язык>, языковые пакеты (# packurl:)
    ├── dispatcher.py          разбор команд с префиксом пространства, вотчеры и их теги, raw-обработчики
    ├── tl_cache.py            клиент для модулей: «вид» на клиент Telegram FRX (см. ниже)
    ├── inline/                self.inline: form, list, gallery, query_gallery, inline_handler, кнопки
    ├── _html.py               HTML ↔ сущности, включая <emoji document_id=…> и <tg-spoiler>
    ├── _aliases.py            перенаправление импортов heroku.* → hikka.*, hikkatl.* / herokutl.* → telethon.*
    ├── _aiogram2.py           aiogram.utils.exceptions для модулей, написанных под aiogram 2
    ├── _space.py              запуск и остановка пространства
    ├── _frx.py                ссылки на FRX и папки — свои у каждого пространства (основное / песочницы)
    ├── _sandbox.py            пространство внутри WebIDE Sandbox: клиент без сети, мост к ленте и формам
    ├── _tl_compat.py          отличия Hikka-TL / Heroku-TL от обычного Telethon (from_id, кнопки, методы)
    └── modules/               встроенные модули пространства: Loader, Help, HikkaConfig, Settings,
                               Translations, Tester, Evaluator, HikkaSecurity, HikkaInfo, InlineStuff

user/data/hikka/               данные (попадают в резервные копии FRX)
├── db.json                    БД пространства (настройки модулей, префикс, алиасы, права)
├── modules/                   установленные модули Hikka/Heroku (<ИмяКласса>.py)
└── hikka/                     utils.get_base_dir() — сюда модули иногда пишут свои файлы
user/data/cache/hikka/         кэш (скачанные библиотеки, языковые пакеты, копии модулей) — не бэкапится
user/data/sandbox/hikka/       то же для пространства WebIDE Sandbox (своя БД и модули)
```

## Как устроено

**Импорты.** Модуль Hikka загружается как `hikka.modules.<имя>`, поэтому `from .. import loader, utils`,
`from ..inline.types import InlineCall` и подобные импорты попадают в этот пакет. `import heroku…` даёт тот же объект.
`hikkatl` и `herokutl` (форки Telethon) перенаправляются на обычный Telethon FRX. Ставить форки нельзя: они
заменили бы пакет `telethon` под самим FRX. Перенаправление работает только пока пространство запущено.

**Клиент.** `self.client` / `self._client` — подкласс `TelegramClient`, у которого общее состояние с клиентом
FRX: то же соединение и сессия. Отличия видны только модулям Hikka:

- разметка по умолчанию — HTML с тегами форков; у клиента FRX остаётся своя разметка;
- есть `tg_id`, `hikka_me` / `heroku_me`, `loader`, `dispatcher`;
- `get_entity(…, exp=, force=)` с кэшем, `get_perms_cached`, `get_fullchannel`, `get_fulluser`;
- `send_reaction`, `translate`, `transcribe`, `set_status`; у сообщений — `react`, `translate`, `transcribe`.

События, которые получают модули Hikka, — копии событий FRX, привязанные к этому клиенту. Оригиналы,
которые видят модули FRX, не меняются.

**Объекты Telethon как в форках** (`_tl_compat.py`). Модули написаны под Hikka-TL / Heroku-TL, а в FRX —
обычный Telethon, поэтому отличия форков закрыты:

- `message.from_id` у сообщений пространства — как в Hikka-TL число (id отправителя, есть и у входящих в личке,
  где у Telegram from_id пустой), и одновременно Peer (`.user_id`, `isinstance(…, PeerUser)`) — как в Heroku-TL;
  исходный Peer — в `message.old_from_id`;
- старые классы кнопок (`KeyboardButtonCallback`, `KeyboardButtonUrl`, …) работают с кнопками нового слоя Telegram,
  а у кнопок нового слоя есть `.data` / `.url` / `.query`;
- аргументы форков (`top_msg_id`, `invert_media`, `reply_to=InputReplyToMessage(…)`) переводятся или пропускаются;
- методы форков: темы форума (`create_forum_topic`, `get_forum_topics`, …), истории (`get_user_stories`,
  `send_story`, …), юзернеймы, `send_reaction(keep_existing=)`, `translate`, `transcribe`, `set_status`,
  `appconfig`, `get_saved_gifts`, у сообщений — `react`, `translate`, `transcribe`, `link`;
- `utils.convert_reaction` / `get_input_reply_to`, `hints.Reaction`, ошибки `MsgVoiceMissingError`,
  `PaymentRequiredError`, `channels.*ForumTopic*Request` и т. п.

Обычный Telethon только дополняется тем, чего в нём нет; объекты, которые видит сам FRX, не меняются.

**Инлайн.** Своего бота у пространства нет. К диспетчеру стационарного бота FRX подключается внешний
middleware. Апдейты, которые принадлежат пространству, обрабатываются в нём и дальше не идут; остальные
FRX обрабатывает как обычно. Пространству принадлежат: запрос формы по её id, кнопки форм Hikka, выбор
результата, поле ввода, инлайн-команды модулей Hikka и личные сообщения боту для `aiogram_watcher`.
Боту нужны инлайн-режим и Inline Feedback 100 % — те же требования, что у форм FRX.

**Зависимости.** `# requires: пакет1 пакет2` ставится через установщик FRX до загрузки модуля. Если при
загрузке всё равно не хватает импорта, пакет ставится по имени (с известными соответствиями: `PIL` → Pillow,
`cv2` → opencv-python, `imghdr` → standard-imghdr для Python 3.13…), и загрузка повторяется.

**Ошибки** — как в Hikka: ошибки команд, вотчеров, циклов и загрузки модулей приходят вам в личку от бота FRX
(причина, место, текст ошибки и кнопка «🪐 Полный traceback»); пачки обычных логов — текстом или файлом. С какого
уровня присылать — `,config Tester` → `tglog_level` (по умолчанию ERROR), `ignore_common` — не присылать типичные
неопасные ошибки Telethon. Все логи — `,logs [уровень]`, очистить — `,clearlogs`. В Sandbox сообщения приходят в
«ЛС с ботом» песочницы.

**Права.** По умолчанию команды работают только у вас. Чтобы дать доступ другим, есть `,security` (маска
для команды и общая «рамка»), `,owneradd`, `,tsec <пользователь> <команда | модуль> [время]`.

## В WebIDE Sandbox

HikkaCompat можно запустить в песочнице сайта. Тогда внутри неё поднимается отдельное пространство:

- пакет `hikka` общий, но всё, что отличается у пространств (ядро, рантайм, папки, префикс имён модулей в
  `sys.modules`), хранится в контексте, привязанном к event loop (`_frx.current()`); у песочницы свой поток и loop,
  поэтому основное пространство FRX её не видит и наоборот;
- клиент — `SandboxTelegramClient`: настоящий подкласс `TelegramClient` без соединения. Отправка, правка, удаление,
  файлы, `get_messages`, `get_entity`, диалоги идут в ленту Sandbox, модули получают обычные объекты Telethon;
- сообщения песочницы превращаются в `UpdateNewMessage` и проходят через обработчики Telethon (команды, вотчеры,
  `client.on(...)` модулей); сообщения боту — в `aiogram_watcher`;
- инлайн: `client.inline_query(<бот FRX>, …)` отвечает само пространство, результат рисуется формой Sandbox, нажатия
  и ввод значений приходят обратно как `callback_query` / `chosen_inline_result`;
- `CreateChannelRequest`, темы форума, реакции, `GetHistoryRequest` и т. п. выполняются внутри песочницы; прочие
  MTProto-запросы — ошибка «не эмулируется в Sandbox».

## Что поддерживается

- Модули Hikka 1.x, Heroku 2.x, старые FTG/GeekTG: `loader.Module`, `@loader.tds`, `strings` / `strings_ru` /
  `@loader.command(ru_doc=…)`, конфиг с валидаторами (`ConfigValue`, `ConfigCategory`, `Hidden`, `Union`, …),
  `@loader.watcher` со всеми тегами, `@loader.loop`, `@loader.raw_handler`, `@loader.inline_handler`,
  `@loader.callback_handler`, `@loader.need_update`, `aiogram_watcher`.
- `self.db`, `self.get` / `self.set` / `self.pointer`, `self.lookup`, `self.allmodules`, `self.invoke`,
  `self.animate`, `self.request_join`, `self.import_lib` (библиотеки `loader.Library`), `on_dlmod`,
  `client_ready`, `on_unload`, `SelfUnload` / `SelfSuspend` / `LoadError`.
- `self.inline.form` (кнопки callback / url / input / data / action / copy / web_app / style / emoji_id),
  `list`, `gallery`, `query_gallery`, `generate_markup`, `self.inline.bot` (интерфейс aiogram 2 поверх
  aiogram 3 FRX), `bot_username`, `bot_id`, FSM (`ss` / `gs`).
- `utils.*` — функции Hikka и Heroku, включая `answer` (длинный текст — постранично или файлом), `answer_file`,
  `asset_channel`, `asset_forum_topic`, `get_target`, `smart_split`, плейсхолдеры Heroku.
- `| grep` в командах (включается `,togglegrep`), `,,команда` — показать команду, не выполняя.

## Ограничения

- Модули на Pyrogram (Dragon) не поддерживаются.
- Модули, которые сами создают объекты aiogram 2 (`InlineKeyboardMarkup()` без аргументов, позиционные
  аргументы в `InputTextMessageContent(...)`), упадут на aiogram 3. Словари кнопок в формате Hikka работают.
- Боты не могут отправлять премиум-эмодзи, поэтому в формах и галереях они заменяются обычными. В сообщениях
  от вашего аккаунта премиум-эмодзи работают.
- Перезапуск, обновление и остановка (`_internal.restart`, `die`) действуют на весь FRX.
