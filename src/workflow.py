"""La aprobación es la única puerta hacia el vault."""

from __future__ import annotations

from src.errors import DraftError, DraftNotFound, DraftNotPending, NotAllowed
from src.models import SessionDraft
from src.obsidian_manager import CommitResult, ObsidianVault
from src.state_store import DraftRecord, StateStore


def reviewer_is_allowed(author_id: int, user_id: int, *, manage_guild: bool) -> bool:
    return user_id == author_id or manage_guild


def load_review(
    store: StateStore,
    message_id: int,
    *,
    user_id: int,
    manage_guild: bool,
) -> DraftRecord:
    record = store.get_by_message(message_id)
    if record is None:
        raise DraftNotFound()
    if not reviewer_is_allowed(record.author_id, user_id, manage_guild=manage_guild):
        raise NotAllowed()
    return record


def approve_draft(
    store: StateStore,
    vault: ObsidianVault,
    message_id: int,
    user_id: int,
    manage_guild: bool,
) -> CommitResult:
    record = load_review(store, message_id, user_id=user_id, manage_guild=manage_guild)
    if record.status == "approved":
        raise DraftNotPending("Ese resumen ya está en el vault.")
    claimed = store.claim(message_id)
    try:
        result = vault.commit(
            claimed.draft,
            draft_id=claimed.id,
            campaign=claimed.campaign,
            source_filename=claimed.source_filename,
        )
    except Exception:
        store.release(claimed.id)
        raise
    store.mark_approved(claimed.id)
    return result


async def revise_draft_for_message(
    store: StateStore,
    provider,
    message_id: int,
    correction: str,
    *,
    user_id: int,
    manage_guild: bool,
    today: str,
) -> tuple[DraftRecord, SessionDraft]:
    record = load_review(store, message_id, user_id=user_id, manage_guild=manage_guild)
    if record.status == "approved":
        raise DraftNotPending("Ese resumen ya está en el vault.")
    if record.status != "pending":
        raise DraftNotPending("Ese borrador no se puede corregir ahora.")
    text = correction.strip()
    if len(text) < 3:
        raise DraftError("Escribe qué hay que cambiar.")
    updated = await provider.revise_draft(
        draft=record.draft,
        correction=text,
        campaign=record.campaign,
        today=today,
    )
    store.update_payload(record.id, updated)
    refreshed = store.get_by_message(message_id)
    if refreshed is None:
        raise DraftError("No pude guardar la corrección.")
    return refreshed, updated
