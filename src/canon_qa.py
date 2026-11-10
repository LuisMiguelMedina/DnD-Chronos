"""Recupera contexto del vault y responde preguntas del narrador."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from src.errors import AIError, AIResponseError
from src.obsidian_manager import ObsidianVault
from src.textutil import clip
from src.vector_index import VaultVectorIndex, read_note_text, search_canon

log = logging.getLogger("dungeonchronicler.canon_qa")


@dataclass(frozen=True)
class CanonSource:
    relative_path: str
    title: str
    excerpt: str


def gather_sources(
    vault: ObsidianVault,
    index: VaultVectorIndex | None,
    query: str,
    *,
    limit: int = 6,
    per_note: int = 2_500,
    total: int = 16_000,
) -> list[CanonSource]:
    hits = search_canon(vault, index, query, limit)
    sources: list[CanonSource] = []
    budget = total
    for hit in hits:
        try:
            text, title = read_note_text(vault.root, hit.relative_path)
        except OSError as exc:
            log.warning("No pude leer %s: %s", hit.relative_path, exc)
            continue
        excerpt = clip(text, min(per_note, budget))
        if not excerpt.strip():
            continue
        budget -= len(excerpt)
        sources.append(CanonSource(hit.relative_path, title or hit.title, excerpt))
        if budget <= 0:
            break
    return sources


def vault_only_answer(query: str, sources: list[CanonSource]) -> str:
    lines = [
        f"No pude redactar una respuesta con la IA. Esto es lo que hay en el vault sobre «{clip(query, 80)}»:",
        "",
    ]
    for source in sources:
        lines.append(f"**{source.title}** (`{source.relative_path}`)")
        lines.append(clip(source.excerpt, 500))
        lines.append("")
    return clip("\n".join(lines).strip(), 1900)


def canon_qa_rules(campaign: str) -> str:
    return (
        f"Eres el archivero de la campaña «{campaign}». "
        "Respondes en español usando solo el material del vault que te pasan.\n"
        "Reglas:\n"
        "- Si la respuesta no está en el material, dilo sin inventar. "
        "Sugiere qué falta anotar o aprobar en la mesa.\n"
        "- Cita las notas entre corchetes con la ruta, p. ej. [NPCs/Elara Voss.md].\n"
        "- Da contexto breve cuando ayude a entender quién es quién o qué pasó antes.\n"
        "- Máximo unas 900 palabras."
    )


def build_canon_request(question: str, sources: list[CanonSource]) -> str:
    blocks = []
    for source in sources:
        blocks.append(
            f"Archivo: {source.relative_path}\nTítulo: {source.title}\n---\n{source.excerpt}"
        )
    catalog = "\n\n".join(blocks)
    return f"Pregunta del narrador:\n{question.strip()}\n\nMaterial del vault:\n{catalog}"
