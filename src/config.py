"""Configuración leída del entorno. No registra secretos en su representación."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from src.errors import ConfigError
from src.models import VaultFolders
from src.textutil import clean_name

DEFAULT_INLINE_MEDIA_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_MEDIA_BYTES = 25 * 1024 * 1024
_ABSOLUTE_MEDIA_CAP = 100 * 1024 * 1024
_GEMINI_THINKING_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH"})


@dataclass(frozen=True)
class Settings:
    discord_token: str
    guild_id: int | None
    ai_provider: str
    gemini_api_key: str | None
    gemini_model: str
    gemini_thinking_level: str
    gemini_base_url: str | None
    ai_base_url: str | None
    ai_session_token: str | None
    ai_model: str | None
    ai_session_api_style: str
    ai_timeout_seconds: float
    inline_media_bytes: int
    vault_path: Path
    database_path: Path
    campaign_name: str
    watch_channel_ids: frozenset[int]
    max_media_bytes: int
    timezone: str
    folders: VaultFolders

    def __repr__(self) -> str:
        return (
            "Settings("
            f"ai_provider={self.ai_provider!r}, "
            f"vault_path={str(self.vault_path)!r}, "
            f"campaign_name={self.campaign_name!r}, "
            f"timezone={self.timezone!r})"
        )


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    source = os.environ if env is None else env
    provider = _text(source, "AI_PROVIDER", "gemini").lower()
    if provider not in {"gemini", "session"}:
        raise ConfigError("AI_PROVIDER debe ser gemini o session.")

    token = _text(source, "DISCORD_TOKEN")
    if not token:
        raise ConfigError("Falta DISCORD_TOKEN.")

    gemini_key = _optional(source, "GEMINI_API_KEY")
    gemini_base = _optional(source, "GEMINI_BASE_URL")
    session_token = _optional(source, "AI_SESSION_TOKEN")
    session_base = _optional(source, "AI_BASE_URL")
    session_model = _optional(source, "AI_MODEL")
    style = _text(source, "AI_SESSION_API_STYLE", "openai").lower()
    if style not in {"openai", "chronicle"}:
        raise ConfigError("AI_SESSION_API_STYLE debe ser openai o chronicle.")

    if provider == "gemini" and not gemini_key and not (gemini_base and session_token):
        raise ConfigError(
            "Para AI_PROVIDER=gemini define GEMINI_API_KEY, "
            "o GEMINI_BASE_URL junto con AI_SESSION_TOKEN."
        )
    if provider == "session":
        if not session_base or not session_token:
            raise ConfigError("AI_PROVIDER=session requiere AI_BASE_URL y AI_SESSION_TOKEN.")
        if not session_model:
            raise ConfigError("AI_PROVIDER=session requiere AI_MODEL.")

    timeout = _number(source, "AI_TIMEOUT_SECONDS", 300)
    if not 5 <= timeout <= 900:
        raise ConfigError("AI_TIMEOUT_SECONDS debe estar entre 5 y 900.")

    max_media = _integer(source, "MAX_MEDIA_BYTES", DEFAULT_MAX_MEDIA_BYTES)
    if not 1 <= max_media <= _ABSOLUTE_MEDIA_CAP:
        raise ConfigError("MAX_MEDIA_BYTES debe estar entre 1 y 104857600.")
    inline_media = _integer(source, "INLINE_MEDIA_BYTES", DEFAULT_INLINE_MEDIA_BYTES)
    if inline_media < 1:
        raise ConfigError("INLINE_MEDIA_BYTES debe ser positivo.")

    timezone = _text(source, "CHRONICLER_TIMEZONE", "UTC")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Zona horaria desconocida: {timezone}") from exc

    campaign = clean_name(_text(source, "CAMPAIGN_NAME", "Campaña"), 80) or "Campaña"
    return Settings(
        discord_token=token,
        guild_id=_optional_id(source, "DISCORD_GUILD_ID"),
        ai_provider=provider,
        gemini_api_key=gemini_key,
        gemini_model=_text(source, "GEMINI_MODEL", "gemini-3.8-flash"),
        gemini_thinking_level=_gemini_thinking_level(source, "GEMINI_THINKING_LEVEL", "HIGH"),
        gemini_base_url=gemini_base,
        ai_base_url=session_base,
        ai_session_token=session_token,
        ai_model=session_model,
        ai_session_api_style=style,
        ai_timeout_seconds=timeout,
        inline_media_bytes=inline_media,
        vault_path=Path(_text(source, "OBSIDIAN_VAULT_PATH", "./vault")).expanduser(),
        database_path=Path(_text(source, "DATABASE_PATH", "./data/state.db")).expanduser(),
        campaign_name=campaign,
        watch_channel_ids=_id_set(source, "WATCH_CHANNEL_IDS"),
        max_media_bytes=max_media,
        timezone=timezone,
        folders=VaultFolders(
            npcs=_folder(source, "VAULT_DIR_NPCS", "NPCs"),
            locations=_folder(source, "VAULT_DIR_LOCATIONS", "Lugares"),
            sessions=_folder(source, "VAULT_DIR_SESSIONS", "Sesiones"),
            items=_folder(source, "VAULT_DIR_ITEMS", "Objetos"),
            factions=_folder(source, "VAULT_DIR_FACTIONS", "Facciones"),
            plots=_folder(source, "VAULT_DIR_PLOTS", "Tramas"),
        ),
    )


def today_in(timezone_name: str) -> str:
    return datetime.now(ZoneInfo(timezone_name)).date().isoformat()


def _text(env: Mapping[str, str], key: str, default: str = "") -> str:
    return env.get(key, default).strip()


def _optional(env: Mapping[str, str], key: str) -> str | None:
    value = _text(env, key)
    return value or None


def _integer(env: Mapping[str, str], key: str, default: int) -> int:
    raw = _text(env, key)
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} debe ser un entero.") from exc


def _number(env: Mapping[str, str], key: str, default: float) -> float:
    raw = _text(env, key)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ConfigError(f"{key} debe ser un número.") from exc


def _optional_id(env: Mapping[str, str], key: str) -> int | None:
    raw = _text(env, key)
    if not raw:
        return None
    if not raw.isdigit() or int(raw) == 0:
        raise ConfigError(f"{key} debe ser un id numérico de Discord.")
    return int(raw)


def _id_set(env: Mapping[str, str], key: str) -> frozenset[int]:
    raw = _text(env, key)
    if not raw:
        return frozenset()
    values: list[int] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if not item.isdigit() or int(item) == 0:
            raise ConfigError(f"{key} contiene un valor que no es un id de canal: {item}")
        values.append(int(item))
    return frozenset(values)


def _gemini_thinking_level(env: Mapping[str, str], key: str, default: str) -> str:
    raw = _text(env, key, default) or default
    level = raw.upper()
    if level not in _GEMINI_THINKING_LEVELS:
        raise ConfigError(f"{key} debe ser LOW, MEDIUM o HIGH.")
    return level


def _folder(env: Mapping[str, str], key: str, default: str) -> str:
    value = _text(env, key, default) or default
    if (
        value.startswith(".")
        or ".." in value
        or "/" in value
        or "\\" in value
        or any(char in value for char in '<>:"|?*')
    ):
        raise ConfigError(f"{key} no es un nombre de carpeta válido.")
    return value
