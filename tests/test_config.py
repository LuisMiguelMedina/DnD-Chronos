from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from src.config import load_settings, today_in
from src.errors import ConfigError


def _env(tmp_path, **extra):
    values = {
        "DISCORD_TOKEN": "super-secret-token",
        "GEMINI_API_KEY": "AIzaSuperSecretKey123456",
        "OBSIDIAN_VAULT_PATH": str(tmp_path / "vault"),
        "DATABASE_PATH": str(tmp_path / "state.db"),
    }
    values.update(extra)
    return values


def test_load_settings_hides_secrets(tmp_path):
    settings = load_settings(_env(tmp_path))
    rendered = repr(settings)
    assert "super-secret-token" not in rendered
    assert "AIzaSuperSecretKey123456" not in rendered
    assert settings.ai_provider == "gemini"
    assert settings.gemini_thinking_level == "HIGH"
    assert settings.guild_id is None
    assert settings.folders.npcs == "NPCs"
    assert settings.campaign_name == "Campaña"


def test_session_provider_requires_token_model_and_url(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, AI_PROVIDER="session"))
    settings = load_settings(
        _env(
            tmp_path,
            AI_PROVIDER="session",
            AI_BASE_URL="https://proveedor.example/v1",
            AI_SESSION_TOKEN="sesion-token",
            AI_MODEL="modelo-privado",
            AI_SESSION_API_STYLE="chronicle",
        )
    )
    assert settings.ai_session_api_style == "chronicle"
    assert settings.ai_model == "modelo-privado"


def test_gemini_accepts_gateway_token_without_api_key(tmp_path):
    settings = load_settings(
        {
            "DISCORD_TOKEN": "token",
            "AI_PROVIDER": "gemini",
            "GEMINI_BASE_URL": "https://pasarela.example",
            "AI_SESSION_TOKEN": "sesion-token",
            "OBSIDIAN_VAULT_PATH": str(tmp_path / "vault"),
            "DATABASE_PATH": str(tmp_path / "state.db"),
        }
    )
    assert settings.gemini_api_key is None
    assert settings.gemini_base_url == "https://pasarela.example"


def test_rejects_bad_values(tmp_path):
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, DISCORD_TOKEN=""))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, GEMINI_API_KEY="", AI_PROVIDER="gemini"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, AI_PROVIDER="claude"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, VAULT_DIR_NPCS="../NPCs"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, WATCH_CHANNEL_IDS="12, abc"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, MAX_MEDIA_BYTES="muchos"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, DISCORD_GUILD_ID="0"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, CHRONICLER_TIMEZONE="No/Existe"))
    with pytest.raises(ConfigError):
        load_settings(_env(tmp_path, GEMINI_THINKING_LEVEL="minimal"))


def test_watch_channels_and_timezone(tmp_path):
    settings = load_settings(
        _env(
            tmp_path,
            DISCORD_GUILD_ID="42",
            WATCH_CHANNEL_IDS="12, 34",
            CHRONICLER_TIMEZONE="America/Mexico_City",
            VAULT_DIR_NPCS="Personajes",
        )
    )
    assert settings.guild_id == 42
    assert settings.watch_channel_ids == frozenset({12, 34})
    assert settings.folders.npcs == "Personajes"
    expected = datetime.now(ZoneInfo("America/Mexico_City")).date().isoformat()
    assert today_in(settings.timezone) == expected
