"""
core/web/ui.py

Декларативный интерфейс для страниц сайта FRX: модуль описывает страницу
ФУНКЦИЕЙ на Python (без HTML/JS), движок отдаёт браузеру дерево компонентов,
а клики по кнопкам возвращаются обратно в Python-функции.

    import web

    @web.page("Статистика", icon="◐")
    async def stats_page(ui):
        ui.title("Статистика")
        ui.text(f"Сообщений: {ui.state.get('n', 0)}")
        ui.input("name", label="имя", placeholder="введите имя")
        ui.toggle("loud", label="громко", value=False)
        ui.button("сохранить", on_click=save)

    async def save(ctx):
        ctx.state['n'] = ctx.state.get('n', 0) + 1
        ctx.toast(f"Привет, {ctx.values.get('name') or 'аноним'}!")

Как это работает: страница — ЧИСТАЯ функция от (ui.state, ui.values). При открытии и
после каждого клика движок заново вызывает её и присылает свежее дерево; значения
полей ввода клиент присылает вместе с кликом (ctx.values), а постоянное состояние
между кликами лежит в ui.state / ctx.state (dict, живёт до перезапуска юзербота).
"""

import inspect
from contextlib import contextmanager

__all__ = ['page', 'Ui', 'Ctx']


def page(title, icon=None):
    """
    @web.page("Название", icon="◐")
    Помечает функцию как страницу сайта: она появится в главном меню панели (`.web`).
    Функция получает один аргумент — Ui (sync или async — неважно).
    icon необязателен (один символ/эмодзи, лучше простой знак: ◐ ✦ ☾).
    """
    def deco(func):
        pages = getattr(func, '_frx_web_pages', [])
        func._frx_web_pages = pages + [{'title': str(title), 'icon': icon}]
        return func
    return deco


class Ui:
    """Построитель страницы. Все методы — просто добавляют компонент в дерево."""

    def __init__(self, state: dict, values: dict):
        self.state = state
        self.values = values
        self._stack = [[]]
        self._actions = {}
        self._counter = 0

    # -- служебное ----------------------------------------------------- #

    def _add(self, node):
        self._stack[-1].append(node)
        return node

    @contextmanager
    def _container(self, node):
        node['children'] = []
        self._stack.append(node['children'])
        try:
            yield self
        finally:
            self._stack.pop()
        self._add(node)

    # -- текст --------------------------------------------------------- #

    def title(self, text):
        """Крупный заголовок."""
        self._add({'t': 'title', 'text': str(text)})

    def text(self, text):
        """Обычный текст."""
        self._add({'t': 'text', 'text': str(text)})

    def muted(self, text):
        """Приглушённый мелкий текст (подсказки, пояснения)."""
        self._add({'t': 'muted', 'text': str(text)})

    def code(self, text):
        """Моноширинный блок (логи, JSON, результат команды)."""
        self._add({'t': 'code', 'text': str(text)})

    def divider(self):
        """Тонкий полупрозрачный разделитель."""
        self._add({'t': 'divider'})

    # -- ввод ---------------------------------------------------------- #

    def input(self, name, label='', value='', placeholder='', secret=False, multiline=False):
        """Поле ввода. Введённое значение потом придёт в ctx.values[name]."""
        self._add({
            't': 'input', 'name': str(name), 'label': str(label), 'placeholder': str(placeholder),
            'value': self.values.get(name, value), 'secret': bool(secret), 'multiline': bool(multiline),
        })

    def toggle(self, name, label='', value=False):
        """Переключатель да/нет → ctx.values[name] (bool)."""
        self._add({'t': 'toggle', 'name': str(name), 'label': str(label),
                   'value': bool(self.values.get(name, value))})

    def select(self, name, options, label='', value=None):
        """Выбор одного из options (список строк) → ctx.values[name]."""
        options = [str(o) for o in options]
        current = self.values.get(name, value if value is not None else (options[0] if options else None))
        self._add({'t': 'select', 'name': str(name), 'label': str(label),
                   'options': options, 'value': current})

    def button(self, label, on_click=None, arg=None, danger=False):
        """
        Кнопка. on_click — функция (sync/async) с одним аргументом ctx (см. Ctx);
        arg — любое JSON-значение, которое вернётся в ctx.arg (удобно в циклах:
        одна и та же функция на много кнопок).
        """
        action_id = None
        if on_click is not None:
            action_id = f"a{self._counter}"
            self._counter += 1
            self._actions[action_id] = (on_click, arg)
        self._add({'t': 'button', 'label': str(label), 'action': action_id, 'danger': bool(danger)})

    # -- контейнеры / данные ------------------------------------------- #

    def row(self):
        """with ui.row(): ... — компоненты внутри выстраиваются в ряд."""
        return self._container({'t': 'row'})

    def section(self, title=''):
        """with ui.section("Заголовок"): ... — блок с подзаголовком."""
        return self._container({'t': 'section', 'title': str(title)})

    def list(self, items):
        """Список строк; элемент — str или {'title': ..., 'text': ...}."""
        norm = []
        for it in items:
            if isinstance(it, dict):
                norm.append({'title': str(it.get('title', '')), 'text': str(it.get('text', ''))})
            else:
                norm.append({'title': str(it), 'text': ''})
        self._add({'t': 'list', 'items': norm})

    def table(self, headers, rows):
        """Таблица: headers — список названий колонок, rows — список списков."""
        self._add({'t': 'table', 'headers': [str(h) for h in headers],
                   'rows': [[str(c) for c in r] for r in rows]})

    # -- результат ----------------------------------------------------- #

    def tree(self):
        return self._stack[0]


class Ctx:
    """
    Аргумент обработчика кнопки.
        ctx.values      — dict всех полей ввода страницы (name -> значение)
        ctx.arg         — то, что передали в ui.button(..., arg=...)
        ctx.state       — тот же dict, что ui.state (переживает клики)
        ctx.toast(text, kind='info'|'ok'|'error') — всплывающее сообщение
        ctx.set_value(name, value) — программно поменять значение поля (например, очистить)
    """

    def __init__(self, state, values, arg):
        self.state = state
        self.values = values
        self.arg = arg
        self.toasts = []

    def toast(self, text, kind='info'):
        self.toasts.append({'text': str(text), 'kind': kind if kind in ('info', 'ok', 'error') else 'info'})

    def set_value(self, name, value):
        self.values[name] = value


async def _maybe_await(result):
    if inspect.isawaitable(result):
        return await result
    return result


async def render(func, state, values, action_id=None):
    """
    Вызывает функцию страницы; если пришёл action_id — сперва выполняет обработчик
    этой кнопки, затем перерисовывает страницу. Возвращает (tree, toasts).
    """
    values = dict(values or {})
    ui = Ui(state, values)
    await _maybe_await(func(ui))
    toasts = []
    if action_id:
        entry = ui._actions.get(action_id)
        if entry is None:
            toasts.append({'text': 'Кнопка устарела — страница обновлена', 'kind': 'error'})
        else:
            handler, arg = entry
            ctx = Ctx(state, values, arg)
            try:
                await _maybe_await(handler(ctx))
            except Exception as e:                       # ошибка модуля не должна ронять сайт
                ctx.toast(f"{type(e).__name__}: {e}", 'error')
            toasts.extend(ctx.toasts)
            ui = Ui(state, ctx.values)
            await _maybe_await(func(ui))
    return ui.tree(), toasts
