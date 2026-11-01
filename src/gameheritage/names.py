"""Name helpers: slugs, folder-name normalisation and human-readable labels."""

from __future__ import annotations

import re
import unicodedata

_QUOTES = str.maketrans({"’": "'", "‘": "'", "“": '"', "”": '"'})


def normalize_text(text: str) -> str:
    """Straighten curly quotes and collapse runs of whitespace."""
    return re.sub(r"\s+", " ", text.translate(_QUOTES)).strip()


def slugify(text: str) -> str:
    """Lowercase ASCII slug: ``"Assassin's Creed: Unity"`` -> ``"assassins-creed-unity"``."""
    text = normalize_text(text).replace("'", "")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


_CAMEL = re.compile(r"(?<=[a-z])(?=[A-Z][a-z])")


def humanize(token: str) -> str:
    """Turn a file-name fragment into a readable label.

    ``"Shakespeare_s_Theater"`` -> ``"Shakespeare's Theater"``;
    ``"OlourosFortress"`` -> ``"Olouros Fortress"``.
    """
    s = token.replace("_", " ")
    s = re.sub(r"(?<=\w) s(?= |$)", "'s", s)
    s = _CAMEL.sub(" ", s)
    return normalize_text(s)
