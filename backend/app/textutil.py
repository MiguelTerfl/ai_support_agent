import unicodedata

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"), None)


def normalize(text: str) -> str:
    """NFKC + strips invisible characters (they are used to hide instructions)."""
    return unicodedata.normalize("NFKC", text).translate(_ZERO_WIDTH)


def fold(text: str) -> str:
    """Lowercase and strip accents, to compare patterns."""
    nfd = unicodedata.normalize("NFD", text.lower())
    return "".join(ch for ch in nfd if unicodedata.category(ch) != "Mn")
