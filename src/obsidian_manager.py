"""Write structured campaign notes into an Obsidian vault on disk."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from src.ai_engine import ChroniclerDraft
from src.config import ObsidianFolders


def slugify(name: str) -> str:
    name = name.strip()
    name = re.sub(r"[^\w\s\-áéíóúñÁÉÍÓÚÑ]", "", name, flags=re.UNICODE)
    name = re.sub(r"\s+", "-", name)
    return name[:120] or "sin-nombre"


@dataclass
class WriteResult:
    session_path: Path
    npc_paths: list[Path]
    place_paths: list[Path]


class ObsidianManager:
    def __init__(self, vault_root: Path, folders: ObsidianFolders) -> None:
        self.vault_root = vault_root
        self.folders = folders

    def ensure_layout(self) -> None:
        for sub in (self.folders.npcs, self.folders.places, self.folders.sessions):
            (self.vault_root / sub).mkdir(parents=True, exist_ok=True)

    def _frontmatter(self, tags: list[str], extra: dict | None = None) -> str:
        meta = {"tags": tags, "source": "DungeonChronicler"}
        if extra:
            meta.update(extra)
        body = yaml.dump(meta, allow_unicode=True, default_flow_style=False, sort_keys=False)
        return f"---\n{body}---\n\n"

    def write_session_draft(self, draft: ChroniclerDraft, session_date: date | None = None) -> WriteResult:
        self.ensure_layout()
        session_date = session_date or date.today()
        session_dir = self.vault_root / self.folders.sessions
        filename = f"{session_date.isoformat()} - {slugify(draft.session_title)}.md"
        session_path = session_dir / filename

        events_md = "\n".join(f"- {e}" for e in draft.events) or "_Sin eventos destacados._"
        threads_md = "\n".join(f"- {t}" for t in draft.open_threads) or "_Ninguno._"

        content = self._frontmatter(
            ["sesion", "campaña"],
            {"title": draft.session_title, "date": session_date.isoformat()},
        )
        content += f"# {draft.session_title}\n\n"
        content += f"{draft.session_summary}\n\n"
        content += "## Eventos\n\n"
        content += f"{events_md}\n\n"
        content += "## Hilos abiertos\n\n"
        content += f"{threads_md}\n"

        session_path.write_text(content, encoding="utf-8")

        npc_paths: list[Path] = []
        for npc in draft.npcs:
            name = npc.get("name") or "Desconocido"
            notes = npc.get("notes") or ""
            path = self._upsert_entity(self.folders.npcs, name, notes, tag="npc")
            npc_paths.append(path)

        place_paths: list[Path] = []
        for place in draft.places:
            name = place.get("name") or "Desconocido"
            notes = place.get("notes") or ""
            path = self._upsert_entity(self.folders.places, name, notes, tag="lugar")
            place_paths.append(path)

        return WriteResult(session_path=session_path, npc_paths=npc_paths, place_paths=place_paths)

    def _upsert_entity(self, folder: str, name: str, notes: str, tag: str) -> Path:
        entity_dir = self.vault_root / folder
        path = entity_dir / f"{slugify(name)}.md"
        if path.exists():
            existing = path.read_text(encoding="utf-8")
            append = f"\n\n## Actualización ({date.today().isoformat()})\n\n{notes}\n"
            if notes.strip() and notes.strip() not in existing:
                path.write_text(existing.rstrip() + append, encoding="utf-8")
        else:
            content = self._frontmatter([tag, "campaña"], {"title": name})
            content += f"# {name}\n\n{notes}\n"
            path.write_text(content, encoding="utf-8")
        return path

    def search_canon(self, query: str, limit: int = 10) -> list[tuple[str, str]]:
        """Simple vault search: filename and first matching line snippet."""
        query_lower = query.lower()
        if not self.vault_root.is_dir():
            return []

        results: list[tuple[str, str]] = []
        for md_path in sorted(self.vault_root.rglob("*.md")):
            rel = md_path.relative_to(self.vault_root).as_posix()
            try:
                text = md_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if query_lower in rel.lower() or query_lower in text.lower():
                snippet = _snippet(text, query_lower)
                results.append((rel, snippet))
            if len(results) >= limit:
                break
        return results


def _snippet(text: str, query_lower: str, context: int = 80) -> str:
    idx = text.lower().find(query_lower)
    if idx < 0:
        line = text.split("\n", 1)[0][:160]
        return line
    start = max(0, idx - context)
    end = min(len(text), idx + len(query_lower) + context)
    s = text[start:end].replace("\n", " ")
    if start > 0:
        s = "…" + s
    if end < len(text):
        s = s + "…"
    return s
