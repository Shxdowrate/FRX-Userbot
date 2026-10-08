"""
core/web/webide_api.py

HTTP-слой FRX WebIDE (`#/webide`): список модулей, чтение/проверка/сохранение исходника, создание модуля,
история изменений. Всё идёт через WebPanel.api() — те же проверки Host/Origin/X-FRX, что и у остальных вкладок.
Логика защиты шапки, клейма и истории — в core/webide.py.

Правила сохранения (на сервере, а не только в интерфейсе — страница могла быть подделана):
  - встроенные модули — только просмотр;
  - NAME, MODULE_NAME и AUTHOR существующего модуля менять нельзя (VERSION и DESCRIPTION — можно);
  - в файл ставится клеймо «изменён в FRX WebIDE» (первая строка-комментарий);
  - прежняя версия файла уходит в историю (user/data/webide/history/<MODULE_NAME>/), если загрузка новой упала —
    движок возвращает прежнюю версию, а история не пополняется.
"""

import time
from pathlib import Path

from ..paths import MODULES_DIR
from ..webide import Drafts, History, apply_stamp, source_hash, changed_lines, is_lab, lock_errors, read_stamp, unified_diff

LOADED_DIR = MODULES_DIR


def register(panel, router):
    from .server import ApiError                      # server.py импортирует нас лениво — цикла нет
    api = WebIdeApi(panel, ApiError)
    r = router
    r.add_get('/api/webide/modules', panel.api(api.modules))
    r.add_get('/api/webide/source', panel.api(api.source))
    r.add_get('/api/webide/history', panel.api(api.history))
    r.add_get('/api/webide/version', panel.api(api.version))
    r.add_get('/api/webide/visual', panel.api(api.visual))
    r.add_get('/api/webide/draft', panel.api(api.draft_get))
    r.add_post('/api/webide/draft', panel.api(api.draft_put))
    r.add_post('/api/webide/draft/drop', panel.api(api.draft_drop))
    for name in ('check', 'save', 'create'):
        r.add_post(f'/api/webide/{name}', panel.api(getattr(api, name)))
    return api


class WebIdeApi:
    def __init__(self, panel, api_error):
        self.panel = panel
        self.Err = api_error
        self.history_store = History()
        self.drafts = Drafts()

    @property
    def core(self):
        return self.panel.core

    # ----------------------------------------------------------- общее #

    def _info(self, module: str):
        info = self.core.modules.get(module)
        if info is None:
            raise self.Err('Модуль не найден', 404)
        return info

    @staticmethod
    def _disk_source(info) -> str:
        try:
            return Path(info.path).read_text(encoding='utf-8')
        except OSError:
            return info.source

    def _stamp_of(self, source: str):
        return read_stamp(source)

    # --------------------------------------------------------- чтение #

    async def modules(self, request):
        items = []
        for module_name, info in self.core.modules.items():
            stamp, lab = None, False
            if not info.is_builtin:
                disk = self._disk_source(info)
                stamp, lab = self._stamp_of(disk), is_lab(disk)
            items.append({'module': module_name, 'name': info.name, 'version': info.version,
                          'author': info.author, 'builtin': info.is_builtin, 'stamp': stamp, 'lab': lab,
                          'history': len(self.history_store.list(module_name))})
        items.sort(key=lambda m: (m['builtin'], m['name'].lower()))
        return {'modules': items}

    async def source(self, request):
        module = request.query.get('module', '')
        info = self._info(module)
        source = self._disk_source(info)
        meta = {'name': info.name, 'module_name': module, 'author': info.author, 'version': info.version}
        return {'module': module, 'source': source, 'editable': not info.is_builtin, 'path': info.path,
                'builtin': info.is_builtin, 'stamp': self._stamp_of(source), 'meta': meta, 'lab': is_lab(source),
                'history': len(self.history_store.list(module)), 'hash': source_hash(source)}

    # ------------------------------------------------------ черновики #
    # Несохранённые правки редактора и Lab: страница пишет их сюда через ~1,5 с после правки, а при открытии модуля
    # предлагает восстановить. Так правки переживают смену публичного адреса, перезагрузку и другое устройство.

    def _draft_key(self, key):
        try:
            self.drafts._safe(key)
        except ValueError as e:
            raise self.Err(str(e))
        return key

    async def draft_get(self, request):
        key = self._draft_key(request.query.get('key', ''))
        d = self.drafts.get(key)
        if not d:
            return {'draft': None}
        name = key.partition(':')[2]
        info = self.core.modules.get(name) if name != '__new__' else None
        cur = source_hash(self._disk_source(info)) if info is not None else ''
        return {'draft': {'data': d.get('data'), 'saved': d.get('saved'), 'base': d.get('base', ''),
                          'stale': bool(info is not None and d.get('base') and d.get('base') != cur)}}

    async def draft_put(self, request):
        b = await self.panel._json(request)
        key = self._draft_key(b.get('key', ''))
        name = key.partition(':')[2]
        if name != '__new__':
            info = self._info(name)
            if info.is_builtin:
                raise self.Err('Встроенные модули — только просмотр')
        try:
            return self.drafts.put(key, b.get('data'), b.get('base', ''))
        except (ValueError, OSError) as e:
            raise self.Err(f'Черновик не сохранён: {e}')

    async def draft_drop(self, request):
        b = await self.panel._json(request)
        return {'dropped': self.drafts.drop(self._draft_key(b.get('key', '')))}

    async def history(self, request):
        module = request.query.get('module', '')
        self._info(module)
        return {'items': self.history_store.list(module)}

    async def version(self, request):
        module = request.query.get('module', '')
        info = self._info(module)
        old = self.history_store.get(module, request.query.get('id', ''))
        if old is None:
            raise self.Err('Версия не найдена', 404)
        current = self._disk_source(info)
        added, removed = changed_lines(current, old)
        return {'source': old, 'diff': unified_diff(current, old), 'added': added, 'removed': removed}

    async def visual(self, request):
        """Текущий набор иконок visual (с учётом переопределений) — для конструкции «Иконка»."""
        from ..visual import visual_list
        return {'icons': visual_list()}

    # ---------------------------------------------------- проверка/запись #

    async def check(self, request):
        from .server import analyze_source
        b = await self.panel._json(request)
        source = str(b.get('source', ''))
        result = analyze_source(source)
        locks = []
        module = b.get('module')
        if module and module in self.core.modules:
            info = self.core.modules[module]
            if not info.is_builtin and result['meta'] is not None:
                locks = lock_errors(self._disk_source(info), source)
        result['locks'] = locks
        return result

    async def save(self, request):
        b = await self.panel._json(request)
        module, source = b.get('module'), str(b.get('source', ''))
        info = self._info(module)
        if info.is_builtin:
            raise self.Err('Встроенные модули редактировать нельзя — только просмотр')
        meta = self.panel._validated_meta(source)
        if meta['module_name'] != module:
            raise self.Err('MODULE_NAME менять нельзя — это уже будет другой модуль (создайте новый)')
        old_source = self._disk_source(info)
        errors = lock_errors(old_source, source)
        if errors:
            raise self.Err('; '.join(errors))
        if source.strip() == old_source.strip():
            return {'module': module, 'version': info.version, 'source': old_source,
                    'stamp': self._stamp_of(old_source), 'unchanged': True, 'hash': source_hash(old_source)}
        stamped = apply_stamp(source)
        new = await self.panel._swap(module, stamped)           # при ошибке прежняя версия возвращается сама
        added, removed = changed_lines(old_source, stamped)
        self.history_store.add(module, old_source, note=f'до сохранения · +{added} −{removed}')
        return {'module': new.module_name, 'version': new.version, 'source': stamped,
                'stamp': self._stamp_of(stamped), 'history': len(self.history_store.list(module)), 'hash': source_hash(stamped)}

    async def create(self, request):
        core = self.core
        b = await self.panel._json(request)
        source = str(b.get('source', ''))
        meta = self.panel._validated_meta(source)
        name = meta['module_name']
        if name in core.modules:
            raise self.Err(f"Модуль «{name}» уже существует — откройте его и редактируйте, "
                           f"либо задайте другой MODULE_NAME")
        stamped = apply_stamp(source, created=True)
        path = LOADED_DIR / f'{name}.py'
        if path.exists():
            raise self.Err(f"Файл {path.name} уже лежит в папке модулей — задайте другой MODULE_NAME")
        try:
            info = await core._load_from_source(stamped, target='loaded')
        except Exception as e:
            self.panel._purge_partial(name)
            path.unlink(missing_ok=True)
            raise self.Err(f'{type(e).__name__}: {e}')
        self.history_store.add(name, stamped, note='создан в WebIDE')
        return {'module': info.module_name, 'name': info.name, 'version': info.version, 'source': stamped,
                'stamp': self._stamp_of(stamped), 'hash': source_hash(stamped)}
