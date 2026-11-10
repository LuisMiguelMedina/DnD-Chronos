"""Índice vectorial del vault en Qdrant. Solo se actualiza tras aprobar un borrador."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from src.config import Settings
from src.embeddings import Embedder, GeminiEmbedder
from src.errors import ConfigError
from src.obsidian_manager import CommitResult, ObsidianVault, SearchHit

log = logging.getLogger("dungeonchronicler.vector")
_EMBED_LIMIT = 12_000


def build_vector_index(settings: Settings, vault: ObsidianVault) -> VaultVectorIndex | None:
    if not settings.qdrant_url:
        return None
    if settings.ai_provider != "gemini" or not settings.gemini_api_key:
        raise ConfigError(
            "QDRANT_URL requiere AI_PROVIDER=gemini y GEMINI_API_KEY para generar embeddings."
        )
    embedder = GeminiEmbedder(api_key=settings.gemini_api_key, model=settings.gemini_embedding_model)
    return VaultVectorIndex(
        vault=vault,
        url=settings.qdrant_url,
        collection=settings.qdrant_collection,
        embedder=embedder,
    )


def search_canon(
    vault: ObsidianVault,
    index: VaultVectorIndex | None,
    query: str,
    limit: int = 5,
) -> list[SearchHit]:
    lexical = vault.search(query, limit)
    if index is None:
        return lexical
    try:
        semantic = index.search(query, limit)
    except Exception as exc:
        log.warning("Búsqueda vectorial falló (%s); uso texto.", type(exc).__name__)
        return lexical
    return _merge_hits(lexical, semantic, limit=limit)


class VaultVectorIndex:
    def __init__(
        self,
        *,
        vault: ObsidianVault,
        url: str,
        collection: str,
        embedder: Embedder,
        client: QdrantClient | None = None,
    ) -> None:
        self.vault = vault
        self.collection = collection
        self.embedder = embedder
        self._client = client or QdrantClient(url=url, timeout=30)
        self._ready = False

    def index_commit(self, result: CommitResult) -> None:
        texts: list[str] = []
        paths: list[str] = []
        titles: list[str] = []
        for note in result.notes:
            try:
                text, title = _read_note(self.vault.root, note.relative_path)
            except OSError as exc:
                log.warning("No pude leer %s para Qdrant: %s", note.relative_path, exc)
                continue
            paths.append(note.relative_path)
            titles.append(title)
            texts.append(text)
        if not texts:
            return
        self._ensure_collection()
        vectors = self.embedder.embed(texts)
        points = [
            PointStruct(
                id=_point_id(path),
                vector=vector,
                payload={"relative_path": path, "title": title},
            )
            for path, title, vector in zip(paths, titles, vectors, strict=True)
        ]
        self._client.upsert(collection_name=self.collection, points=points)

    def search(self, query: str, limit: int = 5) -> list[SearchHit]:
        folded = query.strip()
        if len(folded) < 2:
            return []
        limit = max(1, min(limit, 10))
        self._ensure_collection()
        vector = self.embedder.embed([folded])[0]
        response = self._client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=limit,
            with_payload=True,
        )
        hits: list[SearchHit] = []
        for point in response.points or []:
            payload = point.payload or {}
            rel = str(payload.get("relative_path", ""))
            title = str(payload.get("title", rel))
            if not rel:
                continue
            score = int(round(float(point.score or 0) * 100))
            snippet = _snippet_from_vault(self.vault.root, rel, folded)
            hits.append(SearchHit(rel, title, snippet, score))
        return hits

    def _ensure_collection(self) -> None:
        if self._ready:
            return
        if not self._client.collection_exists(self.collection):
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=self.embedder.dimension, distance=Distance.COSINE),
            )
        self._ready = True


def _point_id(relative_path: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"chronicler:{relative_path}"))


def _read_note(root: Path, relative_path: str) -> tuple[str, str]:
    path = (root / relative_path).resolve()
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise OSError("La nota queda fuera del vault.")
    raw = path.read_text(encoding="utf-8", errors="replace")
    body = raw
    if raw.startswith("---\n"):
        parts = raw.split("---", 2)
        if len(parts) >= 3:
            body = parts[2]
    text = f"{path.stem}\n{body.strip()}"
    return text[:_EMBED_LIMIT], path.stem


def _snippet_from_vault(root: Path, relative_path: str, query: str) -> str:
    try:
        text, _ = _read_note(root, relative_path)
    except OSError:
        return ""
    from src.textutil import fold

    folded = fold(query)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if folded in fold(line):
            return line[:300]
    return (lines[0][:300] if lines else "")


def _merge_hits(*groups: list[SearchHit], limit: int) -> list[SearchHit]:
    merged: dict[str, SearchHit] = {}
    for group in groups:
        for hit in group:
            current = merged.get(hit.relative_path)
            if current is None or hit.score > current.score:
                merged[hit.relative_path] = hit
    ordered = sorted(merged.values(), key=lambda item: (-item.score, item.relative_path))
    return ordered[:limit]
