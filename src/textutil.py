"""Texto compartido: recortes, nombres y comparación sin acentos."""

from __future__ import annotations

import re
import unicodedata

_SPACE = re.compile(r"[ \t]+")
_NEWLINES = re.compile(r"\n{3,}")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFD", text.casefold())
    return "".join(char for char in decomposed if unicodedata.category(char) != "Mn")


def clip(text: str, limit: int) -> str:
    cleaned = text.strip()
    if len(cleaned) <= limit:
        return cleaned
    if limit <= 1:
        return cleaned[:limit]
    return cleaned[: limit - 1].rstrip() + "…"


def clean_name(value: str, limit: int = 200) -> str:
    text = _CONTROL.sub("", value.replace("\r", " ").replace("\n", " ").replace("\t", " "))
    text = _SPACE.sub(" ", text).strip()
    return text[:limit]


def clean_prose(value: str, limit: int) -> str:
    text = _CONTROL.sub("", value.replace("\r\n", "\n").replace("\r", "\n"))
    text = _SPACE.sub(" ", text)
    text = _NEWLINES.sub("\n\n", text).strip()
    return text[:limit]


def unique_strings(value: object, *, limit: int, item_limit: int) -> list[str]:
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = clean_name(str(item), item_limit)
        key = fold(text)
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= limit:
            break
    return result
