"""Escribe el canon aprobado en un vault de Obsidian, sin salir de esa carpeta."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from src.errors import VaultError
from src.models import Entity, SessionDraft, VaultFolders
from src.textutil import clean_name, fold

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {f"LPT{i}" for i in range(1, 10)}
_INDEX_NAME = "Índice.md"
_KINDS = (
    ("npcs", "npc", "NPCs"),
    ("locations", "lugar", "Lugares"),
    ("items", "objeto", "Objetos"),
    ("factions", "faccion", "Facciones"),
    ("plots", "trama", "Tramas"),
)


@dataclass(frozen=True)
class WrittenNote:
    relative_path: str
    action: str


@dataclass(frozen=True)
class CommitResult:
    notes: tuple[WrittenNote, ...]


@dataclass(frozen=True)
class SearchHit:
    relative_path: str
    title: str
    snippet: str
    score: int


class ObsidianVault:
    def __init__(self, root: Path, folders: VaultFolders) -> None:
        self.root = root.expanduser().resolve()
        self.folders = folders

    def commit(
        self,
        draft: SessionDraft,
        *,
        draft_id: str,
        campaign: str,
        source_filename: str | None = None,
    ) -> CommitResult:
        self._ensure_root()
        session_dir = self._folder(self.folders.sessions)
        session_path = self._session_path(session_dir, draft, draft_id)
        session_rel = self._relative(session_path)
        session_label = clean_name(f"{draft.session_date} {draft.title}", 160)
        written: list[WrittenNote] = []
        links: dict[str, list[tuple[str, str]]] = {}

        for attr, kind, _heading in _KINDS:
            entities = getattr(draft, attr)
            section: list[tuple[str, str]] = []
            if not entities:
                links[attr] = section
                continue
            folder = self._folder(getattr(self.folders, attr))
            for entity in entities:
                note = self._upsert_entity(
                    folder=folder,
                    kind=kind,
                    entity=entity,
                    draft_id=draft_id,
                    campaign=campaign,
                    session_rel=session_rel,
                    session_label=session_label,
                )
                written.append(note)
                section.append((note.relative_path, entity.summary if entity.summary != entity.name else ""))
            links[attr] = section

        session_existed = session_path.exists()
        self._write(
            session_path,
            _session_meta(draft, draft_id, campaign, source_filename),
            _session_body(draft, links),
        )
        written.insert(
            0,
            WrittenNote(session_rel, "updated" if session_existed else "created"),
        )
        index_path = session_dir / _INDEX_NAME
        index_existed = index_path.exists()
        index_line = (
            f"- {wikilink(session_rel, session_label)} — {draft.session_date} "
            f"<!-- chronicler:index={draft_id} -->"
        )
        previous = index_path.read_text(encoding="utf-8") if index_existed else ""
        self._write_raw(index_path, _upsert_index(previous, draft_id, index_line))
        written.append(WrittenNote(self._relative(index_path), "updated" if index_existed else "created"))
        return CommitResult(tuple(written))

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        self._ensure_root()
        folded = fold(query.strip())
        if len(folded) < 2:
            return []
        limit = max(1, min(limit, 10))
        hits: list[SearchHit] = []
        for path in self.root.rglob("*.md"):
            if not path.is_file():
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if not resolved.is_relative_to(self.root):
                continue
            relative = path.relative_to(self.root)
            if any(part.startswith(".") for part in relative.parts):
                continue
            relative_posix = relative.as_posix()
            title = path.stem
            score = 100 if folded in fold(path.stem) else 0
            snippet = ""
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size <= 1_000_000:
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    text = ""
                meta, body = _split_frontmatter(text, strict=False)
                heading = _title_from(meta, body, path.stem)
                title = heading
                if folded in fold(heading):
                    score = max(score, 80)
                if text and folded in fold(text):
                    score = max(score, 40)
                    snippet = _snippet(body or text, folded)
            if score:
                hits.append(SearchHit(relative_posix, title, snippet, score))
        hits.sort(key=lambda hit: (-hit.score, hit.relative_path))
        return hits[:limit]

    def _upsert_entity(
        self,
        *,
        folder: Path,
        kind: str,
        entity: Entity,
        draft_id: str,
        campaign: str,
        session_rel: str,
        session_label: str,
    ) -> WrittenNote:
        path = self._note_path(folder, safe_stem(entity.name))
        existed = path.exists()
        block = _appearance_block(draft_id, session_rel, session_label, entity.details)
        if existed:
            meta, body = _split_frontmatter(path.read_text(encoding="utf-8"), strict=True, path=path)
            meta = _merge_meta(meta, kind=kind, name=entity.name, campaign=campaign, entity=entity)
            body = body if body.strip() else _new_entity_body(entity)
            body = _merge_appearance(body, block, draft_id)
        else:
            meta = _new_meta(kind, entity, campaign)
            body = _merge_appearance(_new_entity_body(entity), block, draft_id)
        self._write(path, meta, body)
        return WrittenNote(self._relative(path), "updated" if existed else "created")

    def _session_path(self, folder: Path, draft: SessionDraft, draft_id: str) -> Path:
        stem = safe_stem(f"{draft.session_date} {draft.title}")
        primary = self._note_path(folder, stem)
        if not primary.exists() or _chronicler_id(primary) == draft_id:
            return primary
        suffix = draft_id[:8]
        trimmed = stem[: max(80 - len(suffix) - 1, 1)].rstrip(" .")
        alternate = self._note_path(folder, f"{trimmed} {suffix}")
        owner = _chronicler_id(alternate) if alternate.exists() else None
        if owner not in {None, draft_id}:
            raise VaultError("Ya hay otra sesión con un nombre demasiado parecido.")
        return alternate

    def _folder(self, name: str) -> Path:
        path = (self.root / name).resolve()
        if not path.is_relative_to(self.root):
            raise VaultError("La carpeta queda fuera del vault.")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _note_path(self, folder: Path, stem: str) -> Path:
        path = (folder / f"{stem}.md").resolve()
        if not path.is_relative_to(self.root):
            raise VaultError("La nota queda fuera del vault.")
        if path.exists() and not path.is_file():
            raise VaultError(f"{self._relative(path)} existe y no es un archivo.")
        return path

    def _write(self, path: Path, meta: dict, body: str) -> None:
        self._write_raw(path, render_note(meta, body))

    def _write_raw(self, path: Path, text: str) -> None:
        if not path.resolve().is_relative_to(self.root):
            raise VaultError("La nota queda fuera del vault.")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        except OSError as exc:
            raise VaultError(f"No pude escribir {self._relative(path)}.") from exc

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.root).as_posix()

    def _ensure_root(self) -> None:
        if not self.root.is_dir():
            raise VaultError(
                f"No existe el vault en {self.root}. Monta la carpeta de Obsidian en esa ruta."
            )


def safe_stem(name: str) -> str:
    text = clean_name(name, 80)
    text = _INVALID.sub(" ", text).replace("..", " ")
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        return "sin-nombre"
    if text.upper() in _RESERVED:
        text += "_"
    return text[:80]


def wikilink(relative_md: str, label: str) -> str:
    target = relative_md[:-3] if relative_md.endswith(".md") else relative_md
    target = target.replace("|", "").replace("[", "").replace("]", "")
    shown = clean_name(label, 160).replace("|", " ").replace("[", "").replace("]", "") or Path(target).name
    return f"[[{target}|{shown}]]"


def render_note(meta: dict, body: str) -> str:
    visible = {key: value for key, value in meta.items() if value not in (None, [], "")}
    dumped = yaml.safe_dump(visible, allow_unicode=True, sort_keys=False, width=1000).strip()
    return f"---\n{dumped}\n---\n\n{body.strip()}\n"


def _session_meta(draft: SessionDraft, draft_id: str, campaign: str, source: str | None) -> dict:
    meta = {
        "type": "sesion",
        "title": draft.title,
        "date": draft.session_date,
        "campaign": campaign,
        "status": "canon",
        "chronicler_id": draft_id,
        "tags": ["sesion", "canon"],
    }
    if source:
        meta["source"] = Path(source).name[:200]
    return meta


def _session_body(draft: SessionDraft, links: dict[str, list[tuple[str, str]]]) -> str:
    lines = [f"# {draft.title}", "", draft.summary.strip(), ""]
    if draft.highlights.strip():
        lines.extend(["## Destacados", "", draft.highlights.strip(), ""])
    headings = {attr: heading for attr, _kind, heading in _KINDS}
    for attr, _kind, _heading in _KINDS:
        entries = links.get(attr) or []
        if not entries:
            continue
        lines.extend([f"## {headings[attr]}", ""])
        for relative, blurb in entries:
            label = Path(relative).stem
            if blurb:
                lines.append(f"- {wikilink(relative, label)} — {blurb}")
            else:
                lines.append(f"- {wikilink(relative, label)}")
        lines.append("")
    if draft.uncertainties:
        lines.extend(["## Incertidumbres", ""])
        lines.extend(f"- {item}" for item in draft.uncertainties)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _new_meta(kind: str, entity: Entity, campaign: str) -> dict:
    tags = unique_keep(entity.tags, kind)
    meta: dict = {
        "type": kind,
        "name": entity.name,
        "campaign": campaign,
        "status": "canon",
        "tags": tags,
    }
    if entity.aliases:
        meta["aliases"] = list(entity.aliases)
    return meta


def _merge_meta(existing: dict, *, kind: str, name: str, campaign: str, entity: Entity) -> dict:
    meta = dict(existing)
    meta.setdefault("type", kind)
    meta.setdefault("name", name)
    meta.setdefault("campaign", campaign)
    meta.setdefault("status", "canon")
    aliases = unique_keep(_as_list(existing.get("aliases")), *entity.aliases)
    tags = unique_keep(_as_list(existing.get("tags")), kind, *entity.tags)
    if aliases:
        meta["aliases"] = aliases
    meta["tags"] = tags or [kind]
    return meta


def _new_entity_body(entity: Entity) -> str:
    description = entity.summary.strip() or "Sin descripción todavía."
    return f"# {entity.name}\n\n## Descripción\n\n{description}\n"


def _appearance_block(draft_id: str, session_rel: str, session_label: str, details: str) -> str:
    body = details.strip() or "Aparece en esta sesión."
    return (
        f"<!-- chronicler:session={draft_id} -->\n"
        f"### {wikilink(session_rel, session_label)}\n\n"
        f"{body}\n"
    )


def _merge_appearance(body: str, block: str, draft_id: str) -> str:
    marker = f"<!-- chronicler:session={draft_id} -->"
    if marker in body:
        pattern = re.compile(
            rf"<!-- chronicler:session={re.escape(draft_id)} -->.*?(?=\n<!-- chronicler:session=|\Z)",
            re.DOTALL,
        )
        return pattern.sub(block.rstrip() + "\n", body, count=1).rstrip() + "\n"
    if "## Apariciones" not in body:
        body = body.rstrip() + "\n\n## Apariciones\n"
    return body.rstrip() + "\n\n" + block.rstrip() + "\n"


def _upsert_index(text: str, draft_id: str, line: str) -> str:
    marker = f"<!-- chronicler:index={draft_id} -->"
    new_line = line.rstrip() + "\n"
    pattern = re.compile(rf"^.*{re.escape(marker)}.*\n?", re.MULTILINE)
    if pattern.search(text):
        return pattern.sub(new_line, text, count=1)
    if not text.strip():
        text = "# Índice de sesiones\n\n"
    elif "# Índice de sesiones" not in text:
        text = "# Índice de sesiones\n\n" + text
    if text and not text.endswith("\n"):
        text += "\n"
    return text + new_line


def _chronicler_id(path: Path) -> str | None:
    try:
        meta, _body = _split_frontmatter(path.read_text(encoding="utf-8"), strict=False)
    except OSError:
        return None
    value = meta.get("chronicler_id")
    return str(value) if value else None


def _split_frontmatter(text: str, *, strict: bool, path: Path | None = None) -> tuple[dict, str]:
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text
    raw = text[4:end]
    body = text[end + 5 :].lstrip("\n")
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as exc:
        if strict:
            name = path.name if path is not None else "la nota"
            raise VaultError(f"No pude leer el frontmatter de {name}.") from exc
        return {}, text
    if not isinstance(data, dict):
        return {}, text
    return data, body


def _title_from(meta: dict, body: str, fallback: str) -> str:
    for key in ("name", "title"):
        value = meta.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for line in body.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def _snippet(text: str, folded_query: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "---" or stripped.startswith("<!--"):
            continue
        if folded_query in fold(stripped):
            return clean_name(stripped, 180)
    return ""


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def unique_keep(*groups: list[str] | str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for group in groups:
        items = [group] if isinstance(group, str) else group
        for item in items:
            text = clean_name(str(item), 80)
            key = fold(text)
            if not text or key in seen:
                continue
            seen.add(key)
            result.append(text)
    return result
