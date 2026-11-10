"""Texto que Discord muestra del borrador, sin depender del cliente."""

from __future__ import annotations

from dataclasses import dataclass

from src.models import Entity, SessionDraft
from src.obsidian_manager import CommitResult
from src.textutil import clip

DRAFT_COLOR = 0xC4A35A
SAVED_COLOR = 0x2F6F4E
_DESCRIPTION_LIMIT = 1200
_FIELD_LIMIT = 500
_EMBED_BUDGET = 6000

_SECTIONS = (
    ("npcs", "NPCs"),
    ("locations", "Lugares"),
    ("items", "Objetos"),
    ("factions", "Facciones"),
    ("plots", "Tramas"),
)


@dataclass(frozen=True)
class Field:
    name: str
    value: str


@dataclass(frozen=True)
class DraftMessage:
    title: str
    description: str
    fields: tuple[Field, ...]
    footer: str
    color: int


def build_draft_message(draft: SessionDraft, *, draft_id: str, campaign: str) -> DraftMessage:
    fields = [
        Field("Campaña", clip(campaign, 80)),
        Field("Fecha", draft.session_date),
    ]
    if draft.highlights:
        fields.append(Field("Destacados", clip(draft.highlights, _FIELD_LIMIT)))
    for attr, label in _SECTIONS:
        value = _entity_block(getattr(draft, attr))
        if value:
            fields.append(Field(label, value))
    if draft.uncertainties:
        fields.append(Field("Incertidumbres", clip("\n".join(draft.uncertainties), _FIELD_LIMIT)))
    message = DraftMessage(
        title=clip(draft.title, 256),
        description=clip(draft.summary, _DESCRIPTION_LIMIT),
        fields=tuple(fields),
        footer=f"Borrador {draft_id[:8]} · todavía no está en el vault",
        color=DRAFT_COLOR,
    )
    return _fit(message)


def build_saved_message(draft: SessionDraft, *, draft_id: str, campaign: str) -> DraftMessage:
    message = build_draft_message(draft, draft_id=draft_id, campaign=campaign)
    return DraftMessage(
        title=message.title,
        description=message.description,
        fields=message.fields,
        footer=f"Canon {draft_id[:8]} · guardado en el vault",
        color=SAVED_COLOR,
    )


def format_commit(result: CommitResult) -> str:
    lines = ["Guardado en el vault:"]
    for note in result.notes:
        action = "nuevo" if note.action == "created" else "actualizado"
        lines.append(f"- {note.relative_path} ({action})")
    return clip("\n".join(lines), 1900)


def format_search(query: str, hits: list) -> str:
    lines = [f"Resultados para «{clip(query, 80)}»:"]
    for hit in hits:
        snippet = f" — {hit.snippet}" if hit.snippet else ""
        lines.append(f"- {hit.relative_path}{snippet}")
    return clip("\n".join(lines), 1900)


def draft_message_weight(message: DraftMessage) -> int:
    total = len(message.title) + len(message.description) + len(message.footer)
    for item in message.fields:
        total += len(item.name) + len(item.value)
    return total


def _entity_block(entities: list[Entity]) -> str:
    parts: list[str] = []
    for entity in entities:
        line = entity.name if not entity.summary or entity.summary == entity.name else f"{entity.name} — {clip(entity.summary, 80)}"
        parts.append(line)
    return clip("\n".join(parts), _FIELD_LIMIT)


def _fit(message: DraftMessage) -> DraftMessage:
    if draft_message_weight(message) <= _EMBED_BUDGET:
        return message
    fields = list(message.fields)
    while fields and draft_message_weight(
        DraftMessage(message.title, message.description, tuple(fields), message.footer, message.color)
    ) > _EMBED_BUDGET:
        fields.pop()
    if not fields:
        return DraftMessage(
            message.title,
            clip(message.description, 3000),
            (),
            message.footer,
            message.color,
        )
    return DraftMessage(
        message.title,
        message.description,
        tuple(fields),
        message.footer,
        message.color,
    )
