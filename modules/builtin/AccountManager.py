import asyncio
import html
import re
import accounts
import cache
import db
import decorators
import engine
import visual
from core.inline_forms import InlineClick
from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberBannedError,
    PhoneNumberFloodError,
    PhoneNumberInvalidError,
)

NAME = "AccountManager"
MODULE_NAME = "account_manager"
VERSION = "2.3"
AUTHOR = "FRX"
DESCRIPTION = "Управление аккаунтами юзербота"
TG_URL = "https://t.me/mescr_m"
GIT_URL = "https://github.com/Shxdowrate/FRX-Userbot/blob/main/modules/builtin/AccountManager.py"

def _esc(value) -> str:
    return html.escape(str(value), quote=False)


# user_id -> accounts.AddAccount — активный процесс добавления аккаунта
_pending_logins = {}


@decorators.on_load()
async def _create_config(module_info):
    if module_info.module_name != MODULE_NAME:
        return
    await engine.create_config(
        key="show_phone_number",
        description="Показывать ли номер телефона при открытии меню аккаунтов?",
        data_type="bool",
        default=False,
    )
    await engine.create_config(
        key="phone_prefix_show",
        description="Сколько первых цифр будет отображено в номере телефона, если он скрыт",
        data_type="int",
        default=3,
    )


# ==== Вспомогательные функции ====


async def cache_phone() -> str:
    """Получает номер текущего аккаунта и кэширует его."""
    me = await engine.client.get_me()
    my_phone = me.phone
    cached_phone = await cache.gget("accounts_command_phone")

    if my_phone != cached_phone:
        await cache.gset("accounts_command_phone", my_phone)

    return f"+{my_phone}"


async def get_main_menu_buttons() -> list:
    """Генерирует базовые кнопки для главного меню."""
    show_phone = await engine.get_config(key="show_phone_number")
    button_text = visual.btn("Номера", "on") if show_phone else visual.btn("Номера", "off")

    return [
        [(visual.btn("Добавить аккаунт"), "add_account_button", "success")],
        [(visual.btn("Переключить аккаунт"), "reset_account_button", "primary")],
        [(visual.btn("Удалить аккаунт"), "delete_account_button", "danger")],
        [(button_text, "show_hide_numbers_button")],
    ]


async def standart_text_accounts() -> str:
    """Формирует текст списка всех аккаунтов."""
    my_phone = f"+{await cache.gget('accounts_command_phone')}"
    show_numbers = await engine.get_config(key="show_phone_number")
    phone_prefix = await engine.get_config(key="phone_prefix_show")

    lines = [visual.title("Аккаунты", sub=f"всего: {len(accounts.account_list())}"), ""]

    for acc in accounts.account_list():
        acc_info = accounts.account_info(acc)
        acc_number = acc_info["phone"]
        acc_id = acc_info["info"]["id"]
        acc_first_name = acc_info["info"]["first_name"]

        if show_numbers:
            phone_format = acc_number
        else:
            visible_part = acc_number[: phone_prefix + 1]
            hidden_part = "*" * len(acc_number[phone_prefix + 1 :])
            phone_format = f"{visible_part}{hidden_part}"

        status_icon = (
            visual.visual("status_success")
            if acc_number == my_phone
            else visual.visual("element")
        )

        lines.append(
            f"{status_icon} <b>{_esc(acc_first_name)}</b>  <code>{acc_id}</code> · <code>{_esc(phone_format)}</code>"
        )

    lines += ["", visual.hint(f"{visual.visual('status_success')} — активный аккаунт · выберите действие")]
    return "\n".join(lines)


async def generate_accounts_buttons(op_type: str) -> list:
    """Генерирует динамические кнопки со списком аккаунтов для действий."""
    if op_type not in ["freset", "fdelete"]:
        raise ValueError(
            f"Select op_type: freset/fdelete, selected: {op_type}"
        )

    buttons = []
    for acc in accounts.account_list():
        info = accounts.account_info(acc)
        number = info["phone"]
        acc_id = info["info"]["id"]
        first_name = info["info"]["first_name"]

        buttons.append([(f"{first_name} | {acc_id}", f"{op_type}_{number}")])

    buttons.append([(visual.btn("Назад", "back"), "back_to_home_button")])
    return buttons


# ==== Команды и хэндлеры ====


@decorators.command("accounts", "acc")
async def accounts_command(event):
    """Открыть менеджер аккаунтов"""
    my_phone = await cache_phone()
    text = await standart_text_accounts()
    fbuttons = await get_main_menu_buttons()

    form = await engine.create_inlineform(
        chat_id=event.chat_id,
        text=text,
        buttons=fbuttons,
        for_user=None,
        black_list=None,
        timeout=None,
    )

    @form.on_click(data="show_hide_numbers_button")
    async def show_hide_numbers_button(click: InlineClick):
        current_state = await engine.get_config(
            key="show_phone_number", module_name=MODULE_NAME
        )
        await engine.edit_config(
            key="show_phone_number",
            value=not current_state,
            operation_type="edit",
        )

        await form.edit(
            text=await standart_text_accounts(),
            buttons=await get_main_menu_buttons(),
        )

    @form.on_click(data="back_to_home_button")
    async def back_to_home_button(click: InlineClick):
        login = await cache.get("login_object")
        if login is not None:
            try:
                await login.cancel()
            except Exception as e:
                engine.tlog(e)

        await form.edit(
            text=await standart_text_accounts(),
            buttons=await get_main_menu_buttons(),
        )

    @form.on_click(data="reset_account_button")
    async def reset_account_button(click: InlineClick):
        buttons = await generate_accounts_buttons(op_type="freset")
        await form.edit(
            text=await standart_text_accounts(), buttons=buttons
        )

    @form.on_click(data_startswith="freset_")
    async def reset_account_set_button(click: InlineClick):
        number = click.data.split("_")[1]
        info = accounts.account_info(number)
        first_name = info["info"]["first_name"]
        acc_id = info["info"]["id"]
        my_phone = await cache_phone()

        if my_phone == number:
            await click.answer(
                f"{visual.visual('status_warning')} Юзербот уже на этом аккаунте",
                show_alert=True,
            )
            return

        await form.edit(
            text=visual.wait(f"Переключаюсь на {_esc(first_name)}…") + "\n" + visual.field("id", acc_id)
        )
        await accounts.set_account(number)
        await engine.bot.send_message(
            acc_id, visual.ok("Юзербот перенесён на этот аккаунт")
        )

    @form.on_click(data="delete_account_button")
    async def delete_account_button(click: InlineClick):
        buttons = await generate_accounts_buttons(op_type="fdelete")
        await form.edit(
            text=await standart_text_accounts(), buttons=buttons
        )

    @form.on_click(data_startswith="fdelete_")
    async def delete_account_set_button(click: InlineClick):
        number = click.data.split("_")[1]
        info = accounts.account_info(number)
        first_name = info["info"]["first_name"]
        acc_id = info["info"]["id"]
        my_phone = await cache_phone()

        if number == my_phone:
            await click.answer(
                f"{visual.visual('status_warning')} Нельзя удалить аккаунт, на котором сейчас работает FRX",
                show_alert=True,
            )
            return

        text = "\n".join([
            visual.title("Удаление аккаунта"),
            visual.field("имя", _esc(first_name), code=False),
            visual.field("id", acc_id),
            "",
            visual.warn("Удалить этот аккаунт из FRX?"),
        ])
        buttons = [
            [
                (visual.btn("Да, удалить"), f"confirm_delete_{number}", "danger"),
                (visual.btn("Отмена", "close"), "back_to_home_button"),
            ],
        ]
        await form.edit(text=text, buttons=buttons)

    @form.on_click(data_startswith="confirm_delete_")
    async def confirm_delete_account_set_button(click: InlineClick):
        number = click.data.split("_")[2]
        await accounts.delete_account(number)
        await form.edit(
            text=visual.ok("Аккаунт удалён") + "\n" + visual.field("номер", _esc(number)),
            buttons=[(visual.btn("Назад", "back"), "back_to_home_button")],
        )

    # ==== Добавление аккаунта ====

    @form.on_click(data="add_account_button")
    async def add_account_button(click: InlineClick):
        await cache.set("login_text", "")
        text = (
            visual.title("Новый аккаунт") + "\n"
            + visual.hint("введите номер в международном формате, например") + " <code>+79991234567</code>"
        )
        buttons = [
            [engine.InputButton(visual.btn("Ввести номер"), "input_number", hint="номер телефона, +7…", style="primary")],
            [(visual.btn("Назад", "back"), "back_to_home_button")],
        ]
        await form.edit(text=text, buttons=buttons)

    @form.on_input(data="input_number")
    async def step_one_input_number(inp):
        number = inp.value
        await cache.set("login_number", number)

        base_text = (
            visual.title("Новый аккаунт") + "\n"
            + visual.hint("введите номер в международном формате, например") + " <code>+79991234567</code>\n\n"
        )
        input_buttons = [
            [engine.InputButton(visual.btn("Ввести номер"), "input_number", hint="номер телефона, +7…", style="primary")],
            [(visual.btn("Назад", "back"), "back_to_home_button")],
        ]

        if not re.fullmatch(r"\+?\d{7,15}", number):
            error_text = f"{base_text}{visual.warn('Это не номер телефона:')} <code>{_esc(number)}</code>"
            await form.edit(text=error_text, buttons=input_buttons)
            return

        if number in accounts.account_list():
            error_text = f"{base_text}{visual.warn('FRX уже авторизован на этом номере:')} <code>{_esc(number)}</code>"
            await form.edit(text=error_text, buttons=input_buttons)
            return

        confirm_text = f"{base_text}{visual.field('номер', _esc(number))}\n{visual.hint('продолжаем?')}"
        confirm_buttons = [
            [
                (
                    visual.btn("Да, продолжить"),
                    f"to_step_two:{number}",
                ),
                (
                    visual.btn("Отмена", "close"),
                    "back_to_home_button",
                ),
            ]
        ]
        await cache.set("login_text", confirm_text)
        await form.edit(text=confirm_text, buttons=confirm_buttons)

    @form.on_click(data_startswith="to_step_two:")
    async def step_two_check_number(click: InlineClick):
        number = click.data.split(":")[1]
        login = accounts.new_login()
        await cache.set("login_object", login)
        await cache.set("login_number", number)

        text = await cache.get("login_text")
        text += "\n" + visual.wait("Проверяю номер…")
        back_buttons = [[(visual.btn("Назад", "back"), "back_to_home_button")]]

        await form.edit(text=text, buttons=back_buttons)

        try:
            await login.enter_phone(number)
        except PhoneNumberInvalidError:
            await login.cancel()
            text += "\n" + visual.err("Номер некорректен")
            await form.edit(text=text, buttons=back_buttons)
            return
        except PhoneNumberBannedError:
            await login.cancel()
            text += "\n" + visual.err("Номер заблокирован в Telegram")
            await form.edit(text=text, buttons=back_buttons)
            return
        except (PhoneNumberFloodError, FloodWaitError) as e:
            await login.cancel()
            text += "\n" + visual.err("Telegram временно ограничил запросы для этого номера") + f"\n<code>{_esc(e)}</code>"
            await form.edit(text=text, buttons=back_buttons)
            return
        except Exception as e:
            await login.cancel()
            text += "\n" + visual.err("Не удалось отправить код") + f"\n<code>{_esc(e)}</code>"
            await form.edit(text=text, buttons=back_buttons)
            return

        text += "\n" + visual.ok("Код отправлен")
        text += "\n" + visual.hint("введите код из Telegram")

        step_three_buttons = [
            [engine.InputButton(visual.btn("Ввести код"), "input_code", hint="код из Telegram", style="primary")],
            [(visual.btn("Назад", "back"), "back_to_home_button")],
        ]
        await cache.set("login_text", text)
        await form.edit(text=text, buttons=step_three_buttons)

        @form.on_input(data="input_code")
        async def step_three_input_code(inp):
            code = inp.value
            text = await cache.get("login_text")
            number = await cache.get("login_number")

            text += "\n" + visual.field("код", _esc(code))
            await form.edit(text=text, buttons=back_buttons)

            try:
                result = await login.enter_code(code)
            except PhoneCodeInvalidError:
                text += "\n" + visual.err("Неверный код — вход отменён")
                await login.cancel()
                await form.edit(text=text, buttons=back_buttons)
                return
            except PhoneCodeExpiredError:
                text += "\n" + visual.err("Код истёк — вход отменён")
                await login.cancel()
                await form.edit(text=text, buttons=back_buttons)
                return
            except Exception as e:
                text += "\n" + visual.err("Ошибка входа — вход отменён") + f"\n<code>{_esc(e)}</code>"
                await login.cancel()
                await form.edit(text=text, buttons=back_buttons)
                return

            if result.get("status") == "password_needed":
                text += "\n" + visual.warn("На аккаунте включена 2FA — введите пароль")
                await cache.set("login_text", text)
                step_four_buttons = [
                    [
                        engine.InputButton(
                            visual.btn("Ввести пароль"), "input_2fa", hint=""
                        )
                    ],
                    [(visual.btn("Назад", "back"), "back_to_home_button")],
                ]
                await form.edit(text=text, buttons=step_four_buttons)
                return

            text += "\n" + visual.ok("Вход выполнен — аккаунт добавлен")
            await cache.set("login_text", text)
            await accounts.refresh_info(number)
            await form.edit(text=text, buttons=back_buttons)

        @form.on_input(data="input_2fa")
        async def step_four_input_2fa_password(inp):
            password = inp.value
            text = await cache.get("login_text")
            number = await cache.get("login_number")

            text += "\n" + visual.wait("Проверяю пароль…")
            await form.edit(text=text, buttons=back_buttons)

            try:
                await login.enter_2FA(password)
            except PasswordHashInvalidError:
                await login.cancel()
                text += "\n" + visual.err("Неверный пароль — вход отменён")
                await form.edit(text=text, buttons=back_buttons)
                return
            except Exception as e:
                await login.cancel()
                text += "\n" + visual.err("Ошибка входа — вход отменён") + f"\n<code>{_esc(e)}</code>"
                await form.edit(text=text, buttons=back_buttons)
                return

            text += "\n" + visual.ok("Аккаунт добавлен") + "\n" + visual.field("номер", _esc(number))
            await accounts.refresh_info(number)
            await form.edit(text=text, buttons=back_buttons)