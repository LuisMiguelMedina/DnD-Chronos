"""Borrador estructurado de una sesión y carpetas del vault."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from src.errors import DraftParseError
from src.textutil import clean_name, clean_prose, fold, unique_strings

_MAX_ENTITIES = 40
_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DMY_DATE = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")


@dataclass(frozen=True)
class VaultFolders:
    npcs: str = "NPCs"
    locations: str = "Lugares"
    sessions: str = "Sesiones"
    items: str = "Objetos"
    factions: str = "Facciones"
    plots: str = "Tramas"


@dataclass
class Entity:
    name: str
    summary: str = ""
    details: str = ""
    aliases: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "summary": self.summary,
            "details": self.details,
            "aliases": list(self.aliases),
            "tags": list(self.tags),
        }


@dataclass
class SessionDraft:
    title: str
    session_date: str
    summary: str
    highlights: str = ""
    npcs: list[Entity] = field(default_factory=list)
    locations: list[Entity] = field(default_factory=list)
    items: list[Entity] = field(default_factory=list)
    factions: list[Entity] = field(default_factory=list)
    plots: list[Entity] = field(default_factory=list)
    uncertainties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "session_date": self.session_date,
            "summary": self.summary,
            "highlights": self.highlights,
            "npcs": [entity.to_dict() for entity in self.npcs],
            "locations": [entity.to_dict() for entity in self.locations],
            "items": [entity.to_dict() for entity in self.items],
            "factions": [entity.to_dict() for entity in self.factions],
            "plots": [entity.to_dict() for entity in self.plots],
            "uncertainties": list(self.uncertainties),
        }

    @classmethod
    def from_dict(cls, data: dict) -> SessionDraft:
        fallback = data.get("session_date")
        today = fallback if isinstance(fallback, str) else "1970-01-01"
        return parse_session_draft(data, today=today)


def parse_session_draft(data: dict, *, today: str) -> SessionDraft:
    if not isinstance(data, dict):
        raise DraftParseError("El borrador no era un objeto JSON.")

    title = clean_name(str(_first(data, "title", "titulo", "título") or ""), 120)
    summary = clean_prose(str(_first(data, "summary", "resumen") or ""), 20_000)
    if not summary:
        raise DraftParseError("El borrador llegó sin resumen.")
    if not title:
        title = "Sesión sin título"

    highlights = _as_prose(_first(data, "highlights", "destacados"), 8_000)
    uncertainties = unique_strings(
        _first(data, "uncertainties", "incertidumbres"),
        limit=20,
        item_limit=300,
    )

    player_characters = _entities(_first(data, "player_characters", "pjs"))
    for entity in player_characters:
        if "PJ" not in entity.tags:
            entity.tags.append("PJ")

    npcs, npc_note = _bounded(
        _dedupe(_entities(_first(data, "npcs", "personajes")) + player_characters),
        "NPCs",
    )
    locations, location_note = _bounded(
        _dedupe(_entities(_first(data, "locations", "lugares", "places"))),
        "lugares",
    )
    items, item_note = _bounded(_dedupe(_entities(_first(data, "items", "objetos"))), "objetos")
    factions, faction_note = _bounded(
        _dedupe(_entities(_first(data, "factions", "facciones"))),
        "facciones",
    )
    plots, plot_note = _bounded(_dedupe(_entities(_first(data, "plots", "tramas"))), "tramas")
    for note in (npc_note, location_note, item_note, faction_note, plot_note):
        if note and note not in uncertainties:
            uncertainties.append(note)

    return SessionDraft(
        title=title,
        session_date=normalize_date(_first(data, "session_date", "fecha", "date"), today),
        summary=summary,
        highlights=highlights,
        npcs=npcs,
        locations=locations,
        items=items,
        factions=factions,
        plots=plots,
        uncertainties=uncertainties,
    )


def normalize_date(value: object, today: str) -> str:
    if not isinstance(value, str):
        return _valid_iso(today)
    text = value.strip()
    if _ISO_DATE.fullmatch(text):
        return _valid_iso(text, fallback=today)
    match = _DMY_DATE.fullmatch(text)
    if match is None:
        return _valid_iso(today)
    day, month, year = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return _valid_iso(today)


def _valid_iso(value: str, fallback: str | None = None) -> str:
    try:
        date.fromisoformat(value)
    except ValueError:
        if fallback is None or fallback == value:
            return "1970-01-01"
        return _valid_iso(fallback)
    return value


def _first(data: dict, *keys: str) -> object:
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_prose(value: object, limit: int) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_as_prose(item, limit) for item in value]
        return clean_prose("\n".join(part for part in parts if part), limit)
    if isinstance(value, dict):
        return ""
    return clean_prose(str(value), limit)


def _entities(value: object) -> list[Entity]:
    if not value:
        return []
    items = [value] if isinstance(value, dict) else value
    if not isinstance(items, list):
        return []
    entities: list[Entity] = []
    for item in items:
        entity = _entity(item)
        if entity is not None:
            entities.append(entity)
    return entities


def _entity(item: object) -> Entity | None:
    if isinstance(item, str):
        name = clean_name(item, 120)
        if not name:
            return None
        return Entity(name=name, summary=name)
    if not isinstance(item, dict):
        return None
    name = clean_name(str(_first(item, "name", "nombre") or ""), 120)
    if not name:
        return None
    aliases = [
        alias
        for alias in unique_strings(_first(item, "aliases", "alias"), limit=10, item_limit=80)
        if fold(alias) != fold(name)
    ]
    return Entity(
        name=name,
        summary=clean_prose(str(_first(item, "summary", "resumen") or ""), 500),
        details=clean_prose(str(_first(item, "details", "detalle", "detalles") or ""), 2_000),
        aliases=aliases,
        tags=unique_strings(_first(item, "tags", "etiquetas"), limit=10, item_limit=40),
    )


def _dedupe(entities: list[Entity]) -> list[Entity]:
    merged: dict[str, Entity] = {}
    order: list[str] = []
    for entity in entities:
        key = fold(entity.name)
        current = merged.get(key)
        if current is None:
            merged[key] = entity
            order.append(key)
            continue
        if not current.summary and entity.summary:
            current.summary = entity.summary
        if entity.details and entity.details not in current.details:
            current.details = clean_prose(f"{current.details}\n{entity.details}".strip(), 2_000)
        for alias in entity.aliases:
            if fold(alias) != key and alias not in current.aliases:
                current.aliases.append(alias)
        for tag in entity.tags:
            if tag not in current.tags:
                current.tags.append(tag)
    return [merged[key] for key in order]


def _bounded(entities: list[Entity], label: str) -> tuple[list[Entity], str | None]:
    if len(entities) <= _MAX_ENTITIES:
        return entities, None
    return entities[:_MAX_ENTITIES], f"Había más {label} de los que entraron en el borrador."
