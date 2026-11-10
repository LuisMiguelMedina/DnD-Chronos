from pathlib import Path

import pytest
import yaml

from src.errors import VaultError
from src.models import VaultFolders
from src.obsidian_manager import ObsidianVault, safe_stem


def _vault(tmp_path, folders=None):
    root = tmp_path / "vault"
    root.mkdir()
    return ObsidianVault(root, folders or VaultFolders())


def _frontmatter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    raw = text.split("---", 2)[1]
    return yaml.safe_load(raw)


def test_commit_writes_linked_notes_and_keeps_human_edits(tmp_path, draft):
    vault = _vault(tmp_path)
    first = vault.commit(draft, draft_id="a" * 32, campaign="Las Cenizas", source_filename="../../mesa.mp3")
    second = vault.commit(draft, draft_id="a" * 32, campaign="Las Cenizas", source_filename="mesa.mp3")

    session = vault.root / "Sesiones" / "2026-11-10 La cueva.md"
    npc = vault.root / "NPCs" / "Elara Voss.md"
    assert session.is_file()
    assert (vault.root / "Lugares" / "Cueva del Norte.md").is_file()
    assert (vault.root / "Tramas" / "El mapa robado.md").is_file()
    assert not (vault.root / "Objetos").exists() or not any((vault.root / "Objetos").glob("*.md"))
    session_text = session.read_text(encoding="utf-8")
    assert "Élara" in session_text
    assert "\\u00" not in session_text
    assert "[[NPCs/Elara Voss|Elara Voss]]" in session_text
    assert _frontmatter(session)["chronicler_id"] == "a" * 32
    assert _frontmatter(session)["source"] == "mesa.mp3"
    assert _frontmatter(session)["campaign"] == "Las Cenizas"
    assert first.notes[0].action == "created"
    assert second.notes[0].action == "updated"
    assert session_text.count("El grupo encontró") == 1

    npc_text = npc.read_text(encoding="utf-8")
    npc_text = npc_text.replace("status: canon\n", "status: canon\nsecreto: conservar\n", 1)
    npc_text = npc_text.replace("Tejedora\n", "Tejedora\n\nNota del narrador: es zurda.\n", 1)
    npc_text = npc_text.replace("tags:\n- PJ\n", "tags:\n- PJ\n- aliado\n", 1)
    npc.write_text(npc_text, encoding="utf-8")

    vault.commit(draft, draft_id="a" * 32, campaign="Las Cenizas")
    edited = npc.read_text(encoding="utf-8")
    assert "Nota del narrador: es zurda." in edited
    assert "secreto: conservar" in edited
    assert "aliado" in edited
    assert edited.count(f"<!-- chronicler:session={'a' * 32} -->") == 1
    assert "Les dio un mapa" in edited
    index = (vault.root / "Sesiones" / "Índice.md").read_text(encoding="utf-8")
    assert index.count(f"<!-- chronicler:index={'a' * 32} -->") == 1
    assert index.count("# Índice de sesiones") == 1

    for path in tmp_path.rglob("*"):
        if path.is_symlink():
            assert path.resolve().is_relative_to(vault.root) or path.parent == vault.root
        elif path.is_file():
            assert path.resolve().is_relative_to(vault.root)


def test_names_cannot_escape_the_vault_and_reserved_names_are_safe(tmp_path, draft):
    assert safe_stem("CON") == "CON_"
    assert "/" not in safe_stem("../../etc/passwd")
    vault = _vault(tmp_path)
    escaped = draft
    escaped.npcs[0].name = "../../etc/passwd"
    vault.commit(escaped, draft_id="b" * 32, campaign="Campaña")
    assert not (tmp_path / "etc passwd.md").exists()
    assert not (tmp_path / "passwd.md").exists()
    assert any(path.parent == vault.root / "NPCs" for path in (vault.root / "NPCs").glob("*.md"))
    for path in tmp_path.rglob("*.md"):
        assert path.resolve().is_relative_to(vault.root)


def test_existing_session_note_is_not_overwritten(tmp_path, draft):
    vault = _vault(tmp_path)
    session_dir = vault.root / "Sesiones"
    session_dir.mkdir()
    original = session_dir / "2026-11-10 La cueva.md"
    original.write_text("nota humana\n", encoding="utf-8")
    vault.commit(draft, draft_id="c" * 32, campaign="Campaña")
    assert original.read_text(encoding="utf-8") == "nota humana\n"
    generated = list(session_dir.glob("2026-11-10 La cueva *.md"))
    assert len(generated) == 1
    assert "El grupo encontró" in generated[0].read_text(encoding="utf-8")


def test_custom_folder_search_and_missing_vault(tmp_path, draft):
    vault = _vault(tmp_path, VaultFolders(npcs="Personajes"))
    vault.commit(draft, draft_id="d" * 32, campaign="Campaña")
    assert (vault.root / "Personajes" / "Elara Voss.md").is_file()

    outside = tmp_path / "fuera.md"
    outside.write_text("secreto-fuera del vault\n", encoding="utf-8")
    (vault.root / "puente.md").symlink_to(outside)
    assert vault.search("secreto-fuera") == []

    hits = vault.search("elara")
    assert any(hit.relative_path.endswith("Elara Voss.md") for hit in hits)
    river = vault.search("rio")
    assert any("río" in hit.snippet or "rio" in hit.snippet.casefold() for hit in river)

    missing = ObsidianVault(tmp_path / "no-existe", VaultFolders())
    with pytest.raises(VaultError):
        missing.commit(draft, draft_id="e" * 32, campaign="Campaña")
    assert not (tmp_path / "no-existe").exists()
    with pytest.raises(VaultError):
        missing.search("elara")
