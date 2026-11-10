from src.models import Entity, SessionDraft
from src.presentation import (
    build_draft_message,
    build_saved_message,
    draft_message_weight,
    format_commit,
)
from src.obsidian_manager import CommitResult, WrittenNote


def test_draft_message_stays_inside_discord_limits(draft):
    message = build_draft_message(draft, draft_id="abc123def456", campaign="Las Cenizas")
    assert message.title == "La cueva"
    assert "todavía no está" in message.footer
    assert draft_message_weight(message) <= 6000
    saved = build_saved_message(draft, draft_id="abc123def456", campaign="Las Cenizas")
    assert saved.color != message.color
    assert "guardado" in saved.footer

    huge = SessionDraft(
        title="T" * 400,
        session_date="2026-11-10",
        summary="S" * 5000,
        highlights="H" * 2000,
        npcs=[Entity(name=f"NPC {i}", summary="x" * 200) for i in range(40)],
        locations=[Entity(name=f"Lugar {i}", summary="y" * 200) for i in range(10)],
        items=[Entity(name=f"Objeto {i}", summary="z" * 200) for i in range(10)],
        factions=[Entity(name=f"Fac {i}", summary="w" * 200) for i in range(10)],
        plots=[Entity(name=f"Trama {i}", summary="v" * 200) for i in range(10)],
        uncertainties=["duda " * 40 for _ in range(20)],
    )
    heavy = build_draft_message(huge, draft_id="abc123def456", campaign="Campaña")
    assert draft_message_weight(heavy) <= 6000
    assert len(heavy.title) <= 256
    assert len(heavy.description) <= 1200


def test_format_commit_names_created_and_updated_notes():
    text = format_commit(
        CommitResult(
            (
                WrittenNote("Sesiones/2026-11-10 La cueva.md", "created"),
                WrittenNote("NPCs/Elara Voss.md", "updated"),
            )
        )
    )
    assert "Sesiones/2026-11-10 La cueva.md (nuevo)" in text
    assert "NPCs/Elara Voss.md (actualizado)" in text
