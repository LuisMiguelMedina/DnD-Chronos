import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from src.errors import DraftBusy, DraftError, DraftNotPending, NotAllowed
from src.models import VaultFolders, parse_session_draft
from src.obsidian_manager import ObsidianVault
from src.state_store import StateStore
from src.workflow import approve_draft, reviewer_is_allowed, revise_draft_for_message


class FakeProvider:
    def __init__(self):
        self.corrections = []

    async def revise_draft(self, *, draft, correction, campaign, today):
        self.corrections.append((correction, campaign, today))
        return parse_session_draft(
            {**draft.to_dict(), "summary": draft.summary + " Corregido."},
            today=today,
        )


def _pending(tmp_path, draft, author_id=7):
    root = tmp_path / "vault"
    root.mkdir(parents=True)
    store = StateStore(tmp_path / "state.db")
    record = store.save_pending(
        message_id=10,
        channel_id=20,
        guild_id=30,
        author_id=author_id,
        campaign="Las Cenizas",
        draft=draft,
        source_filename="mesa.mp3",
    )
    return store, ObsidianVault(root, VaultFolders()), record


def test_only_the_author_or_a_manager_can_approve(tmp_path, draft):
    assert reviewer_is_allowed(7, 7, manage_guild=False)
    assert reviewer_is_allowed(7, 9, manage_guild=True)
    assert not reviewer_is_allowed(7, 9, manage_guild=False)

    store, vault, _record = _pending(tmp_path, draft)
    with pytest.raises(NotAllowed):
        approve_draft(store, vault, 10, 9, False)
    assert store.get_by_message(10).status == "pending"
    assert not any(vault.root.rglob("*.md"))

    result = approve_draft(store, vault, 10, 9, True)
    assert store.get_by_message(10).status == "approved"
    assert any(note.relative_path.startswith("Sesiones/") for note in result.notes)
    with pytest.raises(DraftNotPending):
        approve_draft(store, vault, 10, 7, False)
    store.close()


def test_failed_write_returns_the_draft_to_pending(tmp_path, draft):
    store, _vault, _record = _pending(tmp_path, draft)
    missing = ObsidianVault(tmp_path / "ausente", VaultFolders())
    with pytest.raises(Exception):
        approve_draft(store, missing, 10, 7, False)
    assert store.get_by_message(10).status == "pending"
    real = ObsidianVault(tmp_path / "vault", VaultFolders())
    approve_draft(store, real, 10, 7, False)
    assert store.get_by_message(10).status == "approved"
    texts = [path.read_text(encoding="utf-8") for path in (tmp_path / "vault").rglob("*.md")]
    assert any("Élara" in text for text in texts)
    store.close()


def test_stale_writing_can_be_retried_and_a_fresh_one_is_busy(tmp_path, draft):
    store, vault, record = _pending(tmp_path / "stale", draft)
    old = (datetime.now(timezone.utc) - timedelta(seconds=180)).replace(microsecond=0).isoformat()
    store._conn.execute(
        "UPDATE drafts SET status = 'writing', updated_at = ? WHERE id = ?",
        (old, record.id),
    )
    approve_draft(store, vault, 10, 7, False)
    assert store.get_by_message(10).status == "approved"
    store.close()

    store, vault, record = _pending(tmp_path / "busy", draft)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    store._conn.execute(
        "UPDATE drafts SET status = 'writing', updated_at = ? WHERE id = ?",
        (now, record.id),
    )
    with pytest.raises(DraftBusy):
        approve_draft(store, vault, 10, 7, False)
    assert store.get_by_message(10).status == "writing"
    store.close()


def test_correction_updates_the_pending_draft(tmp_path, draft):
    store, _vault, record = _pending(tmp_path, draft)
    provider = FakeProvider()

    async def run():
        return await revise_draft_for_message(
            store,
            provider,
            10,
            "El río era subterráneo.",
            user_id=7,
            manage_guild=False,
            today="2026-11-10",
        )

    updated, revised = asyncio.run(run())
    assert updated.id == record.id
    assert revised.summary.endswith("Corregido.")
    assert store.get_by_message(10).draft.summary.endswith("Corregido.")
    assert provider.corrections == [("El río era subterráneo.", "Las Cenizas", "2026-11-10")]

    async def empty():
        await revise_draft_for_message(
            store,
            provider,
            10,
            "  ",
            user_id=7,
            manage_guild=False,
            today="2026-11-10",
        )

    with pytest.raises(DraftError):
        asyncio.run(empty())
    store.close()
