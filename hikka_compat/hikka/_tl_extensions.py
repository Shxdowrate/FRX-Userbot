"""hikkatl.extensions / herokutl.extensions: как telethon.extensions, но html — с тегами форков (см. _html)."""

from telethon.extensions import *  # noqa: F401,F403
from telethon.extensions import binaryreader, markdown, messagepacker  # noqa: F401

from . import _html as html  # noqa: F401
