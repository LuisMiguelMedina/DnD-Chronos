from dataclasses import dataclass

from qdrant_client import QdrantClient

from src.models import VaultFolders
from src.obsidian_manager import CommitResult, ObsidianVault, WrittenNote
from src.vector_index import VaultVectorIndex, search_canon


@dataclass
class FakeEmbedder:
    dimension: int = 4

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            seed = sum(ord(char) for char in text) % 97
            vectors.append([1.0, seed / 100, 0.2, 0.1])
        return vectors


def test_index_and_semantic_search(tmp_path):
    vault = ObsidianVault(tmp_path / "vault", VaultFolders())
    vault.root.mkdir(parents=True)
    npc_dir = vault.root / "NPCs"
    npc_dir.mkdir()
    note = npc_dir / "Elara Voss.md"
    note.write_text("---\ntype: npc\n---\n# Elara Voss\nLa tejedora del río.\n", encoding="utf-8")

    client = QdrantClient(":memory:")
    index = VaultVectorIndex(
        vault=vault,
        url=":memory:",
        collection="test",
        embedder=FakeEmbedder(),
        client=client,
    )
    index.index_commit(
        CommitResult((WrittenNote("NPCs/Elara Voss.md", "created"),)),
    )
    hits = index.search("tejedora del rio", limit=3)
    assert hits
    assert hits[0].relative_path == "NPCs/Elara Voss.md"
    assert hits[0].score > 0

    merged = search_canon(vault, index, "tejedora", 5)
    assert any(hit.relative_path.endswith("Elara Voss.md") for hit in merged)
