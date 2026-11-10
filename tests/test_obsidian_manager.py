from pathlib import Path

from src.ai_engine import ChroniclerDraft
from src.config import ObsidianFolders
from src.obsidian_manager import ObsidianManager, slugify, _snippet


def test_slugify():
    assert slugify("El Dragón Rojo") == "El-Dragón-Rojo"


def test_write_session_creates_files(tmp_path: Path):
    folders = ObsidianFolders(npcs="NPCs", places="Lugares", sessions="Sesiones")
    mgr = ObsidianManager(tmp_path, folders)
    draft = ChroniclerDraft(
        session_title="La cueva",
        session_summary="Entraron a la cueva.",
        npcs=[{"name": "Grom", "notes": "Tabernero"}],
        places=[{"name": "Cueva Norte", "notes": "Oscura"}],
        events=["Combate con murciélagos"],
        open_threads=["¿Qué hay al fondo?"],
    )
    result = mgr.write_session_draft(draft)
    assert result.session_path.exists()
    text = result.session_path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "tags:" in text
    assert "La cueva" in text
    assert (tmp_path / "NPCs" / "Grom.md").exists()
    assert (tmp_path / "Lugares" / "Cueva-Norte.md").exists()


def test_search_canon(tmp_path: Path):
    folders = ObsidianFolders(npcs="NPCs", places="Lugares", sessions="Sesiones")
    mgr = ObsidianManager(tmp_path, folders)
    (tmp_path / "NPCs").mkdir(parents=True)
    (tmp_path / "NPCs" / "aria.md").write_text("# Aria\nHechicera del norte.", encoding="utf-8")
    hits = mgr.search_canon("hechicera")
    assert len(hits) == 1
    assert "aria.md" in hits[0][0]


def test_snippet():
    assert "foo" in _snippet("hello foo world", "foo")
