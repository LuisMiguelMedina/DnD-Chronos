"""Embeddings de texto para el índice vectorial del vault."""

from __future__ import annotations

from typing import Protocol

# Dimensiones publicadas del modelo por defecto en Google AI Studio.
_EMBEDDING_DIMS = {
    "text-embedding-004": 768,
    "gemini-embedding-001": 3072,
}


class Embedder(Protocol):
    @property
    def dimension(self) -> int: ...

    def embed(self, texts: list[str]) -> list[list[float]]: ...


class GeminiEmbedder:
    def __init__(self, *, api_key: str, model: str = "text-embedding-004", client=None) -> None:
        self._api_key = api_key
        self.model = model
        self._client = client
        self.dimension = _EMBEDDING_DIMS.get(model, 768)

    def _ensure_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self._api_key)
        return self._client

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._ensure_client()
        vectors: list[list[float]] = []
        for text in texts:
            response = client.models.embed_content(model=self.model, contents=text)
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                raise RuntimeError("Gemini no devolvió embeddings.")
            values = getattr(embeddings[0], "values", None)
            if not values:
                raise RuntimeError("El embedding llegó vacío.")
            vectors.append(list(values))
        return vectors
