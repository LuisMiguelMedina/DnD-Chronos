from src.canon_qa import (
    CanonSource,
    build_canon_request,
    gather_sources,
    vault_only_answer,
)
from src.models import VaultFolders
from src.obsidian_manager import ObsidianVault


def test_gather_sources_reads_vault_notes(tmp_path):
    vault = ObsidianVault(tmp_path / "vault", VaultFolders())
    vault.root.mkdir(parents=True)
    npc = vault.root / "NPCs"
    npc.mkdir()
    (npc / "Elara Voss.md").write_text(
        "---\ntype: npc\n---\n# Elara\nLa tejedora del río Norte.\n",
        encoding="utf-8",
    )
    sources = gather_sources(vault, None, "Elara", limit=3)
    assert len(sources) == 1
    assert sources[0].relative_path == "NPCs/Elara Voss.md"
    assert "tejedora" in sources[0].excerpt


def test_build_request_and_fallback():
    sources = [CanonSource("NPCs/Elara.md", "Elara", "Resumen corto.")]
    body = build_canon_request("¿Quién es Elara?", sources)
    assert "¿Quién es Elara?" in body
    assert "NPCs/Elara.md" in body
    fallback = vault_only_answer("Elara", sources)
    assert "Elara" in fallback
    assert "NPCs/Elara.md" in fallback
