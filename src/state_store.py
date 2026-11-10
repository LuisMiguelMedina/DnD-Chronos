"""Borradores pendientes en SQLite, para que los botones sobrevivan a un reinicio."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.errors import DraftBusy, DraftNotFound, DraftNotPending, DraftParseError, StateError
from src.models import SessionDraft

_STALE_WRITING = timedelta(seconds=120)


@dataclass(frozen=True)
class DraftRecord:
    id: str
    message_id: int
    channel_id: int
    guild_id: int | None
    author_id: int
    campaign: str
    status: str
    draft: SessionDraft
    source_filename: str | None
    created_at: str
    updated_at: str


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser()
        self._lock = threading.RLock()
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                self.path,
                timeout=5,
                isolation_level=None,
                check_same_thread=False,
            )
        except (OSError, sqlite3.Error) as exc:
            raise StateError(f"No puedo escribir la base de datos en {self.path}.") from exc
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS drafts (
                    id TEXT PRIMARY KEY,
                    message_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    guild_id INTEGER,
                    author_id INTEGER NOT NULL,
                    campaign TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    source_filename TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_drafts_message ON drafts(message_id)"
            )

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    def save_pending(
        self,
        *,
        message_id: int,
        channel_id: int,
        guild_id: int | None,
        author_id: int,
        campaign: str,
        draft: SessionDraft,
        source_filename: str | None,
    ) -> DraftRecord:
        draft_id = uuid.uuid4().hex
        now = _now()
        payload = json.dumps(draft.to_dict(), ensure_ascii=False)
        with self._lock:
            try:
                self._conn.execute(
                    """
                    INSERT INTO drafts (
                        id, message_id, channel_id, guild_id, author_id, campaign,
                        status, payload, source_filename, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
                    """,
                    (
                        draft_id,
                        message_id,
                        channel_id,
                        guild_id,
                        author_id,
                        campaign,
                        payload,
                        source_filename,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StateError("Ese mensaje ya tiene un borrador.") from exc
        record = self.get_by_message(message_id)
        if record is None:
            raise StateError("No pude leer el borrador que acabo de guardar.")
        return record

    def get_by_message(self, message_id: int) -> DraftRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM drafts WHERE message_id = ?",
                (message_id,),
            ).fetchone()
        return None if row is None else self._record(row)

    def update_payload(self, draft_id: str, draft: SessionDraft) -> None:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE drafts
                SET payload = ?, updated_at = ?
                WHERE id = ? AND status = 'pending'
                """,
                (json.dumps(draft.to_dict(), ensure_ascii=False), _now(), draft_id),
            )
        if cursor.rowcount != 1:
            raise DraftNotPending("Ese borrador ya no se puede corregir.")

    def claim(self, message_id: int) -> DraftRecord:
        with self._lock:
            started = False
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                started = True
                row = self._conn.execute(
                    "SELECT * FROM drafts WHERE message_id = ?",
                    (message_id,),
                ).fetchone()
                if row is None:
                    raise DraftNotFound()
                status = row["status"]
                if status == "approved":
                    raise DraftNotPending("Ese resumen ya está en el vault.")
                if status == "writing":
                    updated = _parse_time(row["updated_at"])
                    if datetime.now(timezone.utc) - updated < _STALE_WRITING:
                        raise DraftBusy()
                elif status != "pending":
                    raise DraftNotPending()
                self._conn.execute(
                    "UPDATE drafts SET status = 'writing', updated_at = ? WHERE id = ?",
                    (_now(), row["id"]),
                )
                self._conn.execute("COMMIT")
                started = False
            except Exception:
                if started:
                    self._conn.execute("ROLLBACK")
                raise
            fresh = self._conn.execute(
                "SELECT * FROM drafts WHERE id = ?",
                (row["id"],),
            ).fetchone()
        return self._record(fresh)

    def release(self, draft_id: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                UPDATE drafts
                SET status = 'pending', updated_at = ?
                WHERE id = ? AND status = 'writing'
                """,
                (_now(), draft_id),
            )

    def mark_approved(self, draft_id: str) -> None:
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE drafts
                SET status = 'approved', updated_at = ?
                WHERE id = ? AND status = 'writing'
                """,
                (_now(), draft_id),
            )
        if cursor.rowcount != 1:
            raise DraftNotPending("No pude marcar el borrador como guardado.")

    def delete(self, draft_id: str) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM drafts WHERE id = ?", (draft_id,))

    def _record(self, row: sqlite3.Row) -> DraftRecord:
        try:
            draft = SessionDraft.from_dict(json.loads(row["payload"]))
        except (json.JSONDecodeError, DraftParseError) as exc:
            raise StateError("El borrador guardado está dañado.") from exc
        return DraftRecord(
            id=row["id"],
            message_id=row["message_id"],
            channel_id=row["channel_id"],
            guild_id=row["guild_id"],
            author_id=row["author_id"],
            campaign=row["campaign"],
            status=row["status"],
            draft=draft,
            source_filename=row["source_filename"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)
