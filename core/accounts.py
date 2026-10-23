"""
core/accounts.py

AccountManager — управление мультиаккаунтом юзербота.

Юзербот может работать сразу с несколькими Telegram-аккаунтами и
переключаться между ними "на лету" (set_account). При повторном запуске
юзербот автоматически поднимается на том аккаунте, на котором работал
последний раз (данные хранятся в user/data/accounts.json).

Сессии хранятся как StringSession, чтобы не плодить .session файлы
для каждого номера аккаунта.

Формат user/data/accounts.json:
    {
      "accounts": {
        "+79991234567": {
          "session": "1BJWap1s...",     # StringSession, секрет
          "tags": {"name": "Рабочий"},  # произвольные метки — задаёт сам
                                         # пользователь через set_tag()
          "info": {                     # снэпшот профиля из Telegram,
            "id": 123456789,            # обновляется refresh_account_info()
            "first_name": "Ivan",
            "last_name": "Petrov",
            "username": "ivanpetrov",
            "updated_at": "2026-09-01T12:00:00+00:00"
          }
        },
        ...
      },
      "active": "+79991234567"
    }

tags и info — разные вещи: tags пользователь выставляет сам (и они никогда
не трогаются автоматически), а info — это то, что реально лежит в профиле
Telegram у этого аккаунта, обновляется через refresh_account_info()
(см. ниже) и может использоваться, например, как "имя по умолчанию", если
свой tag ещё не задан.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import SessionPasswordNeededError

from .paths import DATA_DIR

ACCOUNTS_FILE = DATA_DIR / 'accounts.json'


def _session_of(entry: dict):
    """
    Сессия для TelegramClient: у первого аккаунта (вход при установке) — его ФАЙЛОВАЯ сессия
    (entry['session_file'], user/data/frx_session.session): в ней кэш собеседников и состояние обновлений,
    которых нет у StringSession (без них после перезапуска не находились пользователи вне недавних диалогов
    и терялись пропущенные апдейты). Файла нет — запасная копия entry['session'] (StringSession).
    """
    sf = entry.get('session_file')
    if sf and Path(str(sf) + '.session').is_file():
        return str(sf)
    return StringSession(entry.get('session') or '')


class AccountManager:
    def __init__(self, api_id, api_hash):
        self.api_id = api_id
        self.api_hash = api_hash
        self.clients = {}   # number -> TelegramClient
        self.active = None
        self._load()

    def _load(self):
        ACCOUNTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        if ACCOUNTS_FILE.exists():
            self.data = json.loads(ACCOUNTS_FILE.read_text(encoding='utf-8'))
        else:
            self.data = {'accounts': {}, 'active': None}

        # Миграция старого формата (phone -> "session_string" строкой) в
        # новый (phone -> {"session": ..., "tags": {}, "info": {}}) — раньше
        # к аккаунту нельзя было прицепить вообще ничего, кроме самой сессии.
        migrated = False
        for number, value in list(self.data.get('accounts', {}).items()):
            if isinstance(value, str):
                self.data['accounts'][number] = {
                    'session': value,
                    'tags': {},
                    'info': {},
                }
                migrated = True
            else:
                value.setdefault('tags', {})
                value.setdefault('info', {})
        if migrated:
            self._save()

    def _save(self):
        ACCOUNTS_FILE.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=2), encoding='utf-8'
        )

    def _require_account(self, number: str) -> dict:
        number = str(number)
        entry = self.data['accounts'].get(number)
        if entry is None:
            raise ValueError(f"Аккаунт '{number}' не найден.")
        return entry

    async def add_account(self, number: str, session_string: str = None):
        """
        Добавляет аккаунт под заданным номером.
        Если session_string не передана — Telethon запросит номер телефона
        и код прямо в консоли (интерактивная авторизация).
        """
        client = TelegramClient(
            StringSession(session_string), self.api_id, self.api_hash
        )
        await client.connect()
        if not await client.is_user_authorized():
            await client.start()
        session_str = client.session.save()
        number = str(number)
        self.data['accounts'][number] = {'session': session_str, 'tags': {}, 'info': {}}
        self._save()
        self.clients[number] = client
        return client

    async def set_account(self, number: str):
        """Переключает активный аккаунт "на лету"."""
        number = str(number)
        entry = self.data['accounts'].get(number)
        if entry is None:
            raise ValueError(f"Аккаунт '{number}' не найден. Сначала используйте add_account().")
        if number not in self.clients:
            client = TelegramClient(_session_of(entry), self.api_id, self.api_hash)
            await client.connect()
            self.clients[number] = client
        self.active = number
        self.data['active'] = number
        self._save()
        return self.clients[number]

    async def delete_account(self, number: str, logout: bool = True) -> dict:
        """
        Удаляет сохранённый аккаунт — из user/data/accounts.json и из
        self.clients (с корректным disconnect, если клиент был поднят).

        logout=False — «забыть»: аккаунт пропадает из списка, но его сессия
        остаётся в Telegram и в data['forgotten']; при повторном добавлении
        того же номера restore_forgotten() войдёт по ней без кода и 2FA.

        logout=True (по умолчанию) — сначала завершает сессию FRX на стороне
        Telegram (auth.logOut): она пропадает из «Устройств» в том аккаунте,
        а сохранённая строка сессии становится недействительной. Если сессия
        уже мертва (её завершили вручную, аккаунт удалён/забанен) — это не
        ошибка, аккаунт просто убирается из базы.

        Возвращает {'logged_out': bool, 'error': str | None}:
          logged_out=True  — сессия точно завершена в Telegram;
          logged_out=False и error=None — выход не запрашивали (logout=False)
                             или сессия уже была недействительна;
          error            — выйти не удалось (нет сети и т.п.); аккаунт всё
                             равно удалён из базы, сессию стоит завершить
                             вручную в «Настройки → Устройства».
        """
        number = str(number)
        entry = self._require_account(number)
        result = {'logged_out': False, 'error': None}

        client = self.clients.pop(number, None)

        if logout:
            own_client = False
            try:
                if client is None:
                    client = TelegramClient(
                        _session_of(entry), self.api_id, self.api_hash
                    )
                    own_client = True
                if not client.is_connected():
                    await client.connect()
                if await client.is_user_authorized():
                    # log_out() сам делает auth.LogOut и отключает клиента
                    result['logged_out'] = bool(await client.log_out())
                    if not result['logged_out']:
                        result['error'] = 'Telegram не подтвердил выход'
            except Exception as e:
                # AuthKeyUnregistered / SessionRevoked и т.п. — сессия уже мертва
                name = type(e).__name__
                if any(k in name for k in ('AuthKey', 'SessionRevoked', 'SessionExpired',
                                            'UserDeactivated', 'Unauthorized')):
                    result['error'] = None
                else:
                    result['error'] = f'{name}: {e}'
            finally:
                if own_client or not result['logged_out']:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
        elif client is not None:
            try:
                await client.disconnect()
            except Exception:
                pass

        del self.data['accounts'][number]
        forgotten = self.data.setdefault('forgotten', {})
        if logout:
            # вышли (или сессия мертва) — помнить нечего
            forgotten.pop(number, None)
        else:
            # «забыть»: из списка убираем, а сессию держим про запас —
            # при повторном добавлении этого номера войдём без кода
            forgotten[number] = entry
        if not self.data['accounts']:
            self.data['active'] = None
        elif self.data.get('active') == number:
            self.data['active'] = next(iter(self.data['accounts']))
        self._save()

        if self.active == number:
            self.active = None

        return result

    @staticmethod
    def _digits(number) -> str:
        return ''.join(ch for ch in str(number) if ch.isdigit())

    def find_forgotten(self, number: str):
        """Ключ «забытого» аккаунта с этим номером (с «+» или без) или None."""
        want = self._digits(number)
        for key in self.data.get('forgotten', {}):
            if want and self._digits(key) == want:
                return key
        return None

    async def restore_forgotten(self, number: str) -> dict:
        """
        Пробует вернуть «забытый» аккаунт по сохранённой сессии, без кода и 2FA.
          {'status': 'success', 'phone': ...}  — сессия жива, аккаунт снова в списке;
          {'status': 'expired'}                — сессию завершили (или аккаунта нет),
                                                 запись стёрта, нужен обычный вход;
          {'status': 'not_found'}              — такого номера среди забытых нет;
          {'status': 'error', 'message': ...}  — не удалось проверить (сеть и т.п.),
                                                 запись сохранена, можно повторить.
        """
        key = self.find_forgotten(number)
        if key is None:
            return {'status': 'not_found'}
        entry = self.data['forgotten'][key]
        client = TelegramClient(_session_of(entry), self.api_id, self.api_hash)
        try:
            await client.connect()
            authorized = await client.is_user_authorized()
        except Exception as e:
            try:
                await client.disconnect()
            except Exception:
                pass
            name = type(e).__name__
            if any(k in name for k in ('AuthKey', 'SessionRevoked', 'SessionExpired',
                                        'UserDeactivated', 'Unauthorized')):
                authorized = False
            else:
                return {'status': 'error', 'message': f'{name}: {e}'}

        if not authorized:
            try:
                await client.disconnect()
            except Exception:
                pass
            self.data['forgotten'].pop(key, None)
            self._save()
            return {'status': 'expired'}

        self.data['forgotten'].pop(key, None)
        entry.setdefault('tags', {})
        entry.setdefault('info', {})
        self.data['accounts'][key] = entry
        if not self.data.get('active'):
            self.data['active'] = key
        self._save()
        self.clients[key] = client
        return {'status': 'success', 'phone': key}

    def account_list(self):
        """Список номеров аккаунтов, где авторизован юзербот."""
        return list(self.data['accounts'].keys())

    def account_info(self, number: str) -> dict:
        """
        Данные аккаунта БЕЗ session (это секрет, наружу его отдавать незачем):
        {'phone': ..., 'tags': {...}, 'info': {...}}
        """
        entry = self._require_account(number)
        return {
            'phone': str(number),
            'tags': dict(entry.get('tags', {})),
            'info': dict(entry.get('info', {})),
        }

    def account_list_full(self):
        """То же, что account_list(), но сразу с tags/info каждого аккаунта."""
        return [self.account_info(number) for number in self.data['accounts']]

    def set_tag(self, number: str, key: str, value):
        """
        Вешает произвольный тег на аккаунт, например:
            set_tag('+79991234567', 'name', 'Рабочий')
        Теги — целиком ваши, движок их не читает и не трогает сам.
        """
        entry = self._require_account(number)
        entry.setdefault('tags', {})[key] = value
        self._save()

    def remove_tag(self, number: str, key: str):
        """Снимает тег с аккаунта (тихо, если его и не было)."""
        entry = self._require_account(number)
        entry.get('tags', {}).pop(key, None)
        self._save()

    def get_tags(self, number: str) -> dict:
        """Все теги аккаунта."""
        entry = self._require_account(number)
        return dict(entry.get('tags', {}))

    def _apply_phone_change(self, old_number: str, new_number: str):
        """
        Переименовывает ключ аккаунта в user/data/accounts.json (и в self.clients),
        если реальный номер телефона аккаунта в Telegram (me.phone) разошёлся
        с тем, под которым он сохранён локально — типичный случай: владелец
        сменил номер телефона прямо в Telegram/у оператора. Сессия при этом
        остаётся полностью рабочей (она привязана к аккаунту, а не к номеру),
        поэтому это не авторизация нового аккаунта — просто переименование
        уже существующей записи, чтобы account_list()/set_account() и т.д.
        и дальше находили этот аккаунт по его АКТУАЛЬНОМУ номеру.
        """
        if new_number == old_number:
            return
        entry = self.data['accounts'].pop(old_number, None)
        if entry is None:
            return
        # Если по новому номеру уже почему-то была отдельная запись (ручная
        # правка user/data/accounts.json, дубликат старого номера и т.п.) — она
        # не более актуальна, чем эта: один физический номер не может быть
        # авторизован в Telegram дважды, так что просто заменяем её.
        self.data['accounts'][new_number] = entry
        if old_number in self.clients:
            self.clients[new_number] = self.clients.pop(old_number)
        if self.data.get('active') == old_number:
            self.data['active'] = new_number
        if self.active == old_number:
            self.active = new_number
        print(f"[FRX] Аккаунт {old_number} сменил номер телефона -> {new_number}, запись обновлена")

    async def refresh_account_info(self, number: str = None):
        """
        Обновляет info (id/first_name/last_name/username) сохранённых
        аккаунтов через get_me(). Без аргумента — по всем сразу, иначе
        только для одного номера.

        Заодно ловит смену номера телефона: если me.phone, вернувшийся от
        Telegram, отличается от номера, под которым аккаунт сохранён
        локально (ключ в user/data/accounts.json), запись переименовывается на
        актуальный номер (см. _apply_phone_change) — иначе аккаунт продолжал
        бы числиться под уже несуществующим номером, пока кто-то не
        отредактирует user/data/accounts.json руками.

        Это НЕ требует, чтобы аккаунт был активным. Частое заблуждение:
        "доступ есть только к текущему аккаунту, к остальным — нет". На
        самом деле каждая сохранённая сессия — это полностью независимый
        вход в Telegram под своим номером; она валидна сама по себе, вне
        зависимости от того, какой аккаунт сейчас active. Так что можно
        просто ненадолго подключиться к сессии ЛЮБОГО сохранённого
        аккаунта, спросить get_me() и отключиться — параллельно с тем, что
        активный клиент продолжает работать как ни в чём не бывало.
        Если клиент аккаунта уже поднят (активный, либо ранее уже
        переключались на него через set_account — он тогда осел в
        self.clients) — используем его напрямую, не открывая второе
        соединение на тот же аккаунт.
        """
        numbers = [str(number)] if number is not None else list(self.data['accounts'].keys())

        for num in numbers:
            entry = self.data['accounts'].get(num)
            if entry is None:
                continue

            client = self.clients.get(num)
            temp_client = None
            if client is None:
                temp_client = TelegramClient(_session_of(entry), self.api_id, self.api_hash)
                await temp_client.connect()
                client = temp_client

            try:
                me = await client.get_me()
                if me is not None:
                    entry.setdefault('info', {})
                    entry['info'].update({
                        'id': me.id,
                        'first_name': me.first_name,
                        'last_name': me.last_name,
                        'username': me.username,
                        'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                    })
                    if me.phone:
                        self._apply_phone_change(num, '+' + str(me.phone).lstrip('+'))
            except Exception:
                # Не роняем весь цикл обновления из-за одного проблемного
                # аккаунта (например, сессию отозвали руками из другого
                # клиента) — просто оставляем его info как было и идём дальше.
                pass
            finally:
                if temp_client is not None:
                    await temp_client.disconnect()

        self._save()

    async def get_active_client(self):
        """
        Возвращает клиент последнего активного аккаунта (для восстановления
        состояния при повторном запуске юзербота), либо None, если
        аккаунтов ещё нет — тогда движок создаст обычный клиент по умолчанию.
        """
        if not self.data['accounts']:
            return None
        if self.active is None and self.data.get('active'):
            return await self.set_account(self.data['active'])
        if self.active is None:
            first = next(iter(self.data['accounts']))
            return await self.set_account(first)
        return self.clients.get(self.active)

    def adopt_client(self, client, me, make_active: bool = True) -> str | None:
        """
        Вносит в user/data/accounts.json аккаунт, вход в который был выполнен НЕ через менеджер — первый вход
        при установке (консоль / приложение FRX) идёт файловой сессией user/data/frx_session.session, и раньше
        этот аккаунт не попадал ни в .acc, ни в accounts.json. Сессия копируется в StringSession (тот же ключ
        авторизации — повторный вход не нужен), клиент становится активным. Возвращает номер или None, если
        номер не удалось узнать. Уже внесённый аккаунт (тот же id) не дублируется.
        """
        if me is None:
            return None
        phone = getattr(me, 'phone', None)
        number = ('+' + str(phone).lstrip('+')) if phone else f"id{me.id}"
        for key, entry in self.data['accounts'].items():
            if (entry.get('info') or {}).get('id') == me.id:
                number = key
                break
        try:
            session_str = StringSession.save(client.session)
        except Exception as e:
            print(f"[FRX] [WARN] Не удалось сохранить сессию первого аккаунта в accounts.json: {e}")
            return None
        entry = self.data['accounts'].get(number) or {'tags': {}, 'info': {}}
        entry['session'] = session_str                     # запасная копия ключа
        filename = getattr(client.session, 'filename', None)
        if filename and str(filename).endswith('.session'):
            entry['session_file'] = str(filename)[:-len('.session')]   # работать — на самой файловой сессии
        entry.setdefault('tags', {})
        entry['info'] = {
            **(entry.get('info') or {}),
            'id': me.id, 'first_name': me.first_name, 'last_name': me.last_name, 'username': me.username,
            'updated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
        }
        self.data['accounts'][number] = entry
        self.data.get('forgotten', {}).pop(number, None)
        if make_active or not self.data.get('active'):
            self.data['active'] = number
            self.active = number
            self.clients[number] = client
        self._save()
        return number

    def has_account_id(self, user_id) -> bool:
        return any((e.get('info') or {}).get('id') == user_id for e in self.data['accounts'].values())

    def new_login(self) -> 'AddAccount':
        """
        Создаёт новый пошаговый процесс авторизации (см. класс AddAccount
        ниже). Инстанс "знает" про AccountManager, поэтому по завершении
        авторизации (успешный enter_code/enter_2FA) сам сохраняет новый
        аккаунт в user/data/accounts.json и кладёт готовый клиент в self.clients —
        отдельно вызывать add_account()/_save() не нужно.
        """
        return AddAccount(self.api_id, self.api_hash, manager=self)


class AddAccount:
    """
    Пошаговая интерактивная авторизация НОВОГО аккаунта — в отличие от
    AccountManager.add_account() (который отдаёт ввод номера/кода прямо
    Telethon, то есть ждёт их в консоли), этот класс не блокирует и не
    вводит ничего сам: каждый шаг — это отдельный вызов, а ввод (номер,
    код, пароль 2FA) приходит откуда угодно (например, из сообщений в
    Telegram-чате, как это делает modules/builtin/AccountManager.py).

    Использование:

        login = accounts.new_login()          # или AccountManager.new_login()
        await login.enter_phone('+79991234567')   # шаг 1 — уходит код

        # ... где-то дальше, когда пользователь прислал код ...
        result = await login.enter_code('12345')  # шаг 2
        if result['status'] == 'password_needed':
            # ... где-то дальше, когда пользователь прислал пароль 2FA ...
            result = await login.enter_2FA('пароль')  # шаг 3

        if result['status'] == 'success':
            print(result['phone'], 'добавлен')

    Класс — конечный автомат с состояниями:
        IDLE            -> ещё не звали enter_phone() (или уже cancel())
        AWAITING_CODE   -> код запрошен у Telegram, ждём enter_code()
        AWAITING_2FA    -> Telegram потребовал пароль, ждём enter_2FA()
        DONE            -> авторизация успешно завершена

    Вызов enter_code()/enter_2FA() вне ожидаемого состояния НЕ бросает
    исключение — оба возвращают
    {'status': 'not_expected', 'message': '...'}, как и просил ТЗ, чтобы
    вызывающий код (например, обработчик сообщения в чате) мог просто
    показать message пользователю, а не оборачивать каждый вызов в try/except.

    Ошибки самого Telegram (неверный номер, неверный/истёкший код и т.п.)
    — это отдельная история: они пробрасываются наружу как есть
    (PhoneNumberInvalidError, PhoneCodeInvalidError, PhoneCodeExpiredError,
    ...), потому что это уже не про "не тот шаг вызвали", а про то, что
    вызывающий код обычно обрабатывает по-разному (например, "код истёк"
    — предлагаем запросить новый, а "неверный пароль 2FA" — предлагаем
    ввести ещё раз).
    """

    STATE_IDLE = 'idle'
    STATE_AWAITING_CODE = 'awaiting_code'
    STATE_AWAITING_2FA = 'awaiting_2fa'
    STATE_DONE = 'done'

    def __init__(self, api_id, api_hash, manager: 'AccountManager' = None):
        self.api_id = api_id
        self.api_hash = api_hash
        self._manager = manager

        self.phone = None
        self.client = None
        self.state = self.STATE_IDLE
        self._phone_code_hash = None

    async def enter_phone(self, number: str):
        """
        Шаг 1. Инициализирует вход: поднимает временный клиент под
        переданный номер и просит Telegram прислать код подтверждения.
        Можно звать повторно (например, после истёкшего кода) — старый
        временный клиент, если был, аккуратно отключается.
        """
        number = str(number)

        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass

        client = TelegramClient(StringSession(), self.api_id, self.api_hash)
        await client.connect()
        try:
            sent = await client.send_code_request(number)
        except Exception:
            await client.disconnect()
            raise

        self.client = client
        self.phone = number
        self._phone_code_hash = sent.phone_code_hash
        self.state = self.STATE_AWAITING_CODE
        return {'status': 'code_sent', 'phone': number}

    async def enter_code(self, code: str):
        """
        Шаг 2. Передаёт код, присланный Telegram. Если класс сейчас не
        ждёт код (enter_phone() не вызывался, либо процесс уже завершён/
        отменён), ничего не делает и сообщает об этом вызывающему коду.
        """
        if self.state != self.STATE_AWAITING_CODE:
            return {'status': 'not_expected', 'message': 'enter_code не ожидает ввода'}

        try:
            await self.client.sign_in(
                phone=self.phone,
                code=str(code),
                phone_code_hash=self._phone_code_hash,
            )
        except SessionPasswordNeededError:
            self.state = self.STATE_AWAITING_2FA
            return {'status': 'password_needed'}

        return await self._finish()

    async def enter_2FA(self, password: str):
        """
        Шаг 3. Передаёт пароль двухфакторной аутентификации. Если класс
        сейчас не ждёт 2FA (либо enter_code() ещё не звали/провалился,
        либо Telegram вообще не запрашивал 2FA для этого аккаунта),
        ничего не делает и сообщает об этом вызывающему коду.
        """
        if self.state != self.STATE_AWAITING_2FA:
            return {'status': 'not_expected', 'message': '2FA не ожидается'}

        await self.client.sign_in(password=password)
        return await self._finish()

    async def cancel(self):
        """
        Отменяет процесс авторизации на любом шаге и отключает временный
        клиент. Не входит в исходное ТЗ (там только 3 шага), но без него
        некуда деть "Отмена"/таймаут в UI — временный клиент иначе так и
        останется висеть подключённым.
        """
        if self.client is not None:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.state = self.STATE_IDLE

    async def _finish(self):
        """Общий хвост enter_code()/enter_2FA() при успешном входе."""
        session_str = self.client.session.save()
        self.state = self.STATE_DONE

        if self._manager is not None:
            old_tags = {}
            key = self._manager.find_forgotten(self.phone)
            if key is not None:
                old_tags = dict(self._manager.data['forgotten'].pop(key, {}).get('tags') or {})
            self._manager.data['accounts'][self.phone] = {
                'session': session_str,
                'tags': old_tags,
                'info': {},
            }
            if not self._manager.data.get('active'):
                self._manager.data['active'] = self.phone
            self._manager._save()
            self._manager.clients[self.phone] = self.client

        return {'status': 'success', 'phone': self.phone}
