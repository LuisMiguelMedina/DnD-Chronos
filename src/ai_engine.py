"""Gemini-backed audio/video summarization and structured extraction."""

from __future__ import annotations

import json
import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import google.generativeai as genai

STRUCTURE_PROMPT = """Eres un cronista de campañas de rol de mesa. Analiza la transcripción o el audio de la sesión
y devuelve ÚNICAMENTE un objeto JSON válido (sin markdown) con esta forma:

{
  "session_title": "título corto de la sesión",
  "session_summary": "resumen narrativo en 2-4 párrafos",
  "npcs": [{"name": "...", "notes": "..."}],
  "places": [{"name": "...", "notes": "..."}],
  "events": ["evento importante 1", "..."],
  "open_threads": ["gancho pendiente 1", "..."]
}

Reglas:
- Español si el audio está en español; si no, el idioma del audio.
- No inventes nombres que no aparezcan; usa "Desconocido" si hace falta.
- Sé conciso pero útil para un vault de Obsidian.
"""


@dataclass
class ChroniclerDraft:
    session_title: str
    session_summary: str
    npcs: list[dict[str, str]]
    places: list[dict[str, str]]
    events: list[str]
    open_threads: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_title": self.session_title,
            "session_summary": self.session_summary,
            "npcs": self.npcs,
            "places": self.places,
            "events": self.events,
            "open_threads": self.open_threads,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChroniclerDraft:
        return cls(
            session_title=str(data.get("session_title", "Sesión sin título")),
            session_summary=str(data.get("session_summary", "")),
            npcs=list(data.get("npcs") or []),
            places=list(data.get("places") or []),
            events=[str(e) for e in (data.get("events") or [])],
            open_threads=[str(t) for t in (data.get("open_threads") or [])],
        )


class AIEngine(Protocol):
    def process_media_file(self, path: Path, mime_type: str | None = None) -> ChroniclerDraft: ...


def _strip_json_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_model_json(raw: str) -> dict[str, Any]:
    cleaned = _strip_json_fence(raw)
    return json.loads(cleaned)


class GeminiEngine:
    """Uses Google Generative AI SDK; optional env hooks for custom gateways."""

    def __init__(
        self,
        *,
        api_key: str | None,
        model_name: str,
        api_base_url: str | None = None,
        session_token: str | None = None,
    ) -> None:
        key = api_key or session_token
        if not key:
            raise ValueError("GeminiEngine requires GEMINI_API_KEY or GEMINI_SESSION_TOKEN")

        if api_base_url:
            os.environ["GOOGLE_API_BASE"] = api_base_url

        genai.configure(api_key=key)
        self._model = genai.GenerativeModel(model_name)

    def process_media_file(self, path: Path, mime_type: str | None = None) -> ChroniclerDraft:
        if mime_type is None:
            mime_type, _ = mimetypes.guess_type(path.name)
        mime_type = mime_type or "application/octet-stream"

        uploaded = genai.upload_file(path=str(path), mime_type=mime_type)
        response = self._model.generate_content([STRUCTURE_PROMPT, uploaded])
        data = _parse_model_json(response.text or "{}")
        return ChroniclerDraft.from_dict(data)


def refine_draft_with_feedback(engine: GeminiEngine, draft: ChroniclerDraft, feedback: str) -> ChroniclerDraft:
    """Apply user correction text to an existing draft."""
    prompt = (
        f"{STRUCTURE_PROMPT}\n\n"
        f"Borrador actual:\n{json.dumps(draft.to_dict(), ensure_ascii=False)}\n\n"
        f"Correcciones del narrador:\n{feedback}\n\n"
        "Devuelve el JSON corregido completo."
    )
    response = engine._model.generate_content(prompt)
    data = _parse_model_json(response.text or "{}")
    return ChroniclerDraft.from_dict(data)


def format_draft_preview(draft: ChroniclerDraft, max_len: int = 1800) -> str:
    lines = [
        f"**{draft.session_title}**",
        "",
        draft.session_summary,
        "",
        f"**NPCs:** {len(draft.npcs)} | **Lugares:** {len(draft.places)} | **Eventos:** {len(draft.events)}",
    ]
    if draft.open_threads:
        lines.append("")
        lines.append("**Hilos abiertos:**")
        for t in draft.open_threads[:5]:
            lines.append(f"- {t}")
    text = "\n".join(lines)
    if len(text) > max_len:
        text = text[: max_len - 3] + "..."
    return text
