"""Версия, которую видят модули Hikka/Heroku (слой совместимости FRX HikkaCompat).

Модули сравнивают её с `# scope: hikka_min x.y.z` / `heroku_min x.y.z` и с `version.__version__`.
Указана версия Heroku 2.1 — она больше любой версии Hikka, так что проверки «не ниже» проходят и у тех, и у других.
"""

__version__ = (2, 1, 0)
hikka_version = (1, 6, 3)          # последняя версия оригинальной Hikka
heroku_version = (2, 1, 0)
compat_version = (1, 0, 0)         # версия самого слоя совместимости
branch = "master"
netver = 0
