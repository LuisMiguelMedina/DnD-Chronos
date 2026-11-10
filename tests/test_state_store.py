import json

import pytest

from src.errors import DraftBusy, DraftNotPending, StateError
from src.state_store import StateStore


def _store(tmp_path, draft, message_id=10):
    store = StateStore(tmp_path / "state.db")
    record = store.save_pending(
        message_id=message_id,
        channel_id=20,
        guild_id=30,
        author_id=7,
        campaign="Las Cenizas",
        draft=draft,
        source_filename="mesa.mp3",
    )
    return store, record


def test_roundtrip_and_duplicate_message(tmp_path, draft):
    store, record = _store(tmp_path, draft)
    loaded = store.get_by_message(10)
    assert loaded is not None
    assert loaded.id == record.id
    assert loaded.draft.summary == draft.summary
    assert loaded.campaign == "Las Cenizas"
    payload = json.loads(
        store._conn.execute("SELECT payload FROM drafts").fetchone()["payload"]
    )
    assert "media" not in payload
    assert "audio" not in payload
    with pytest.raises(StateError):
        store.save_pending(
            message_id=10,
            channel_id=20,
            guild_id=None,
            author_id=7,
            campaign="Las Cenizas",
            draft=draft,
            source_filename=None,
        )
    store.close()


def test_claim_release_and_approve_transition(tmp_path, draft):
    store, record = _store(tmp_path, draft)
    claimed = store.claim(10)
    assert claimed.status == "writing"
    with pytest.raises(DraftBusy):
        store.claim(10)
    assert store.get_by_message(10).status == "writing"
    store.release(record.id)
    assert store.get_by_message(10).status == "pending"
    store.claim(10)
    store.mark_approved(record.id)
    assert store.get_by_message(10).status == "approved"
    with pytest.raises(DraftNotPending):
        store.claim(10)
    store.close()
