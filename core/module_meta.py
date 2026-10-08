"""
core/module_meta.py

parse_module_meta(source) — СТАТИЧЕСКИЙ разбор исходника модуля (ast, без
исполнения кода): NAME / MODULE_NAME / VERSION / AUTHOR / DESCRIPTION и список
команд. Раньше жил внутри modules/builtin/Loader.py (превью .loadmod); теперь
общий — им же движок читает файлы из user/modules/unloaded/ (engine.
list_unloaded_modules(), команда .restoremods). Доступен модулям как
engine.parse_module_meta(source).
"""

import ast


def parse_module_meta(source: str):
    """
    Возвращает dict {'name','module_name','version','author','description','commands'}
    (commands — список списков имён: [['loadmod', 'lm'], ...]) либо None, если в файле
    синтаксическая ошибка или не задан MODULE_NAME.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None

    meta = {'name': None, 'module_name': None, 'version': '1.0', 'author': 'unknown', 'description': ''}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                    if target.id == 'NAME':
                        meta['name'] = node.value.value
                    elif target.id == 'MODULE_NAME':
                        meta['module_name'] = node.value.value
                    elif target.id == 'VERSION':
                        meta['version'] = str(node.value.value)
                    elif target.id == 'AUTHOR':
                        meta['author'] = node.value.value
                    elif target.id == 'DESCRIPTION':
                        meta['description'] = node.value.value

    if not meta['module_name'] or not isinstance(meta['module_name'], str):
        return None
    if not meta['name']:
        meta['name'] = meta['module_name']

    commands = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if not isinstance(dec, ast.Call):
                    continue
                fname = dec.func.attr if isinstance(dec.func, ast.Attribute) else getattr(dec.func, 'id', None)
                if fname != 'command':
                    continue
                names = [a.value for a in dec.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
                if names:
                    commands.append(names)
    meta['commands'] = commands
    return meta
