"""Load runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class ObsidianFolders:
    npcs: str
    places: str
    sessions: str


@dataclass(frozen=True)
class Settings:
    discord_token: str
    discord_guild_id: int | None
    gemini_api_key: str | None
    gemini_api_base_url: str | None
    gemini_session_token: str | None
    gemini_model: str
    vault_path: Path
    folders: ObsidianFolders
    database_path: Path


def _optional_int(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    return int(raw)


def load_settings() -> Settings:
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token:
        raise ValueError("DISCORD_TOKEN is required")

    api_key = os.getenv("GEMINI_API_KEY", "").strip() or None
    session_token = os.getenv("GEMINI_SESSION_TOKEN", "").strip() or None
    if not api_key and not session_token:
        raise ValueError("Set GEMINI_API_KEY and/or GEMINI_SESSION_TOKEN")

    return Settings(
        discord_token=token,
        discord_guild_id=_optional_int("DISCORD_GUILD_ID"),
        gemini_api_key=api_key,
        gemini_api_base_url=os.getenv("GEMINI_API_BASE_URL", "").strip() or None,
        gemini_session_token=session_token,
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash").strip(),
        vault_path=Path(os.getenv("OBSIDIAN_VAULT_PATH", "/vault")),
        folders=ObsidianFolders(
            npcs=os.getenv("OBSIDIAN_FOLDER_NPCS", "NPCs"),
            places=os.getenv("OBSIDIAN_FOLDER_PLACES", "Lugares"),
            sessions=os.getenv("OBSIDIAN_FOLDER_SESSIONS", "Sesiones"),
        ),
        database_path=Path(os.getenv("DATABASE_PATH", "/data/chronicler.db")),
    )
