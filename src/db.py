"""SQLite persistence for pending reviews and session metadata."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator


@dataclass
class PendingReview:
    id: int
    discord_message_id: int
    discord_channel_id: int
    draft_json: str
    attachment_name: str
    status: str  # pending | approved | rejected


def init_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_reviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                discord_message_id INTEGER NOT NULL UNIQUE,
                discord_channel_id INTEGER NOT NULL,
                draft_json TEXT NOT NULL,
                attachment_name TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


@contextmanager
def _connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def save_pending_review(
    db_path: Path,
    *,
    message_id: int,
    channel_id: int,
    draft: dict[str, Any],
    attachment_name: str,
) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _connect(db_path) as conn:
        cur = conn.execute(
            """
            INSERT INTO pending_reviews (
                discord_message_id, discord_channel_id,
                draft_json, attachment_name, status, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?)
            """,
            (message_id, channel_id, json.dumps(draft, ensure_ascii=False), attachment_name, now),
        )
        conn.commit()
        return int(cur.lastrowid)


def get_review_by_message_id(db_path: Path, message_id: int) -> PendingReview | None:
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM pending_reviews WHERE discord_message_id = ?",
            (message_id,),
        ).fetchone()
    if not row:
        return None
    return PendingReview(
        id=row["id"],
        discord_message_id=row["discord_message_id"],
        discord_channel_id=row["discord_channel_id"],
        draft_json=row["draft_json"],
        attachment_name=row["attachment_name"],
        status=row["status"],
    )


def update_review_status(db_path: Path, message_id: int, status: str) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE pending_reviews SET status = ? WHERE discord_message_id = ?",
            (status, message_id),
        )
        conn.commit()


def update_draft(db_path: Path, message_id: int, draft: dict[str, Any]) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE pending_reviews SET draft_json = ? WHERE discord_message_id = ?",
            (json.dumps(draft, ensure_ascii=False), message_id),
        )
        conn.commit()
