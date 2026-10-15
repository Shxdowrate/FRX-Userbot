"""«Указатели» на значения в БД: list / dict, которые сами сохраняются при изменении (db.pointer)."""

import typing

__all__ = [
    "PointerList",
    "PointerDict",
    "BaseSerializingMiddlewareDict",
    "BaseSerializingMiddlewareList",
    "NamedTupleMiddlewareDict",
    "NamedTupleMiddlewareList",
]


class _Saving:
    _db = None
    _module = None
    _key = None
    _default = None

    def _bind(self, db, module, key, default):
        self._db = db
        self._module = module
        self._key = key
        self._default = default


class PointerList(_Saving, list):
    """Список, привязанный к ключу БД: любое изменение тут же сохраняется."""

    def __init__(self, db, module: str, key: str, default: typing.Any = None):
        self._bind(db, module, key, default)
        value = db._get_raw(module, key, default)
        list.__init__(self, value if isinstance(value, list) else (default or []))

    def _save(self):
        self._db.set(self._module, self._key, list(self))

    @property
    def data(self) -> list:
        return list(self)

    @data.setter
    def data(self, value):
        list.clear(self)
        list.extend(self, value)
        self._save()

    def tolist(self):
        return self._db._get_raw(self._module, self._key, self._default)

    def __repr__(self):
        return f"PointerList({list(self)})"

    __str__ = __repr__

    def __setitem__(self, i, v):
        list.__setitem__(self, i, v)
        self._save()

    def __delitem__(self, i):
        list.__delitem__(self, i)
        self._save()

    def __iadd__(self, other):
        list.extend(self, other)
        self._save()
        return self

    def __imul__(self, n):
        res = list.__imul__(self, n)
        self._save()
        return res

    def append(self, value):
        list.append(self, value)
        self._save()

    def extend(self, values):
        list.extend(self, values)
        self._save()

    def insert(self, index, value):
        list.insert(self, index, value)
        self._save()

    def remove(self, value):
        list.remove(self, value)
        self._save()

    def pop(self, index=-1):
        value = list.pop(self, index)
        self._save()
        return value

    def clear(self):
        list.clear(self)
        self._save()

    def sort(self, *args, **kwargs):
        list.sort(self, *args, **kwargs)
        self._save()

    def reverse(self):
        list.reverse(self)
        self._save()


class PointerDict(_Saving, dict):
    """Словарь, привязанный к ключу БД: любое изменение тут же сохраняется."""

    def __init__(self, db, module: str, key: str, default: typing.Any = None):
        self._bind(db, module, key, default)
        value = db._get_raw(module, key, default)
        dict.__init__(self, value if isinstance(value, dict) else (default or {}))

    def _save(self):
        self._db.set(self._module, self._key, dict(self))

    @property
    def data(self) -> dict:
        return dict(self)

    @data.setter
    def data(self, value):
        dict.clear(self)
        dict.update(self, value)
        self._save()

    def todict(self):
        return self._db._get_raw(self._module, self._key, self._default)

    def __repr__(self):
        return f"PointerDict({dict(self)})"

    __str__ = __repr__

    def __bool__(self):
        return bool(dict(self))

    def __setitem__(self, key, value):
        dict.__setitem__(self, key, value)
        self._save()

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self._save()

    def update(self, *args, **kwargs):
        dict.update(self, *args, **kwargs)
        self._save()

    def setdefault(self, key, default=None):
        value = dict.setdefault(self, key, default)
        self._save()
        return value

    def pop(self, key, *default):
        value = dict.pop(self, key, *default) if default else dict.pop(self, key, None)
        self._save()
        return value

    def popitem(self):
        item = dict.popitem(self)
        self._save()
        return item

    def clear(self):
        dict.clear(self)
        self._save()


class BaseSerializingMiddlewareDict:
    """Обёртка над PointerDict, которая (де)сериализует значения (например, в NamedTuple)."""

    def __init__(self, pointer: PointerDict):
        self._pointer = pointer

    def serialize(self, item):
        raise NotImplementedError

    def deserialize(self, item):
        raise NotImplementedError

    def __getitem__(self, key):
        return self.deserialize(self._pointer[key])

    def __setitem__(self, key, value):
        self._pointer[key] = self.serialize(value)

    def __delitem__(self, key):
        del self._pointer[key]

    def __iter__(self):
        for key, value in self._pointer.items():
            yield key, self.deserialize(value)

    def __len__(self):
        return len(self._pointer)

    def __contains__(self, item):
        return item in self._pointer

    def __repr__(self):
        return f"{type(self).__name__}({self._pointer})"

    __str__ = __repr__

    def get(self, key, default=None):
        return self.deserialize(self._pointer[key]) if key in self._pointer else default

    def pop(self, key):
        return self.deserialize(self._pointer.pop(key))

    def popitem(self):
        key, value = self._pointer.popitem()
        return key, self.deserialize(value)

    def setdefault(self, key, default=None):
        return self.deserialize(self._pointer.setdefault(key, self.serialize(default)))

    def clear(self):
        self._pointer.clear()

    def keys(self):
        return self._pointer.keys()

    def values(self):
        return (self.deserialize(v) for v in self._pointer.values())

    def items(self):
        return ((k, self.deserialize(v)) for k, v in self._pointer.items())

    def todict(self) -> dict:
        return {k: self.deserialize(v) for k, v in self._pointer.data.items()}


class BaseSerializingMiddlewareList:
    """Обёртка над PointerList, которая (де)сериализует элементы."""

    def __init__(self, pointer: PointerList):
        self._pointer = pointer

    def serialize(self, item):
        raise NotImplementedError

    def deserialize(self, item):
        raise NotImplementedError

    def __getitem__(self, index):
        return self.deserialize(self._pointer[index])

    def __setitem__(self, index, value):
        self._pointer[index] = self.serialize(value)

    def __delitem__(self, index):
        del self._pointer[index]

    def __iter__(self):
        return (self.deserialize(i) for i in self._pointer)

    def __reversed__(self):
        return (self.deserialize(i) for i in reversed(self._pointer))

    def __len__(self):
        return len(self._pointer)

    def __contains__(self, item):
        return self.serialize(item) in self._pointer

    def __repr__(self):
        return f"{type(self).__name__}({self._pointer})"

    __str__ = __repr__

    def append(self, item):
        self._pointer.append(self.serialize(item))

    def extend(self, items):
        self._pointer.extend([self.serialize(i) for i in items])

    def insert(self, index, item):
        self._pointer.insert(index, self.serialize(item))

    def remove(self, item):
        self._pointer.remove(self.serialize(item))

    def pop(self, index=-1):
        return self.deserialize(self._pointer.pop(index))

    def clear(self):
        self._pointer.clear()

    def tolist(self) -> list:
        return [self.deserialize(i) for i in self._pointer.data]


class NamedTupleMiddlewareList(BaseSerializingMiddlewareList):
    def __init__(self, pointer: PointerList, item_type):
        super().__init__(pointer)
        self._item_type = item_type

    def serialize(self, item):
        return item._asdict()

    def deserialize(self, item):
        return self._item_type(**item)


class NamedTupleMiddlewareDict(BaseSerializingMiddlewareDict):
    def __init__(self, pointer: PointerDict, item_type):
        super().__init__(pointer)
        self._item_type = item_type

    def serialize(self, item):
        return item._asdict()

    def deserialize(self, item):
        return self._item_type(**item)
