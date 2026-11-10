import asyncio
import json

import pytest

from src.ai_engine import (
    GeminiProvider,
    SessionTokenProvider,
    build_provider,
    extract_json_object,
    gemini_client_kwargs,
    redact_secrets,
)
from src.config import load_settings
from src.errors import AIError, AIResponseError
from src.models import parse_session_draft

DRAFT = {
    "title": "La cueva",
    "session_date": "2026-11-10",
    "summary": "El grupo encontró a Elara.",
    "highlights": "",
    "npcs": [{"name": "Elara", "summary": "Tejedora"}],
    "locations": [],
    "items": [],
    "factions": [],
    "plots": ["El mapa robado"],
    "uncertainties": ["No se oyó el nombre del mapa"],
}


class FakeResponse:
    def __init__(self, payload, status=200):
        self.status_code = status
        self._payload = payload

    def json(self):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload


class FakeHttp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = []

    async def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        return FakeResponse(self.payload, self.status)

    async def aclose(self):
        return None


class FakeModels:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})

        class Response:
            def __init__(self, payload):
                self.parsed = payload
                self.text = json.dumps(payload)

        return Response(self.payload)


class Uploaded:
    def __init__(self, state):
        self.name = "files/test"
        self.state = state


class FakeFiles:
    def __init__(self, states):
        self.states = list(states)
        self.uploaded = None
        self.deleted = None
        self.config = None

    async def upload(self, *, file, config):
        self.uploaded = file
        self.config = config
        with open(file, "rb") as handle:
            self.payload = handle.read()
        return Uploaded(self.states.pop(0))

    async def get(self, *, name):
        assert name == "files/test"
        return Uploaded(self.states.pop(0))

    async def delete(self, *, name):
        self.deleted = name


class FakeGemini:
    def __init__(self, payload, states=None):
        self.aio = self
        self.models = FakeModels(payload)
        self.files = FakeFiles(states or ["ACTIVE"])


def test_parse_accepts_spanish_keys_dates_and_limits():
    draft = parse_session_draft(
        {
            "título": "La posada",
            "fecha": "10/11/2026",
            "resumen": "Durmieron en la posada.",
            "personajes": [{"nombre": "Elara", "resumen": "Tejedora"}],
            "pjs": ["Mira"],
            "lugares": [{"nombre": "Posada", "resumen": "Ruidosa"}],
            "tramas": ["La deuda"],
            "incertidumbres": ["¿Quién pagó?"],
        },
        today="2026-11-01",
    )
    assert draft.session_date == "2026-11-10"
    assert draft.title == "La posada"
    assert any(entity.name == "Mira" and "PJ" in entity.tags for entity in draft.npcs)
    assert parse_session_draft(
        {"title": "X", "summary": "Hay resumen.", "session_date": "2026-02-31"},
        today="2026-11-10",
    ).session_date == "2026-11-10"
    crowded = parse_session_draft(
        {
            "title": "X",
            "summary": "Hay resumen suficiente.",
            "npcs": [{"name": f"Persona {i}", "summary": "Alguien"} for i in range(41)],
        },
        today="2026-11-10",
    )
    assert len(crowded.npcs) == 40
    assert crowded.uncertainties


def test_extract_json_from_fences_and_redact_secrets():
    payload = extract_json_object("```json\n" + json.dumps(DRAFT) + "\n```")
    assert payload["title"] == "La cueva"
    with pytest.raises(AIResponseError):
        extract_json_object("no es json")
    redacted = redact_secrets("fallo key=abc123 Bearer sesion-token AIzaSuperSecretKey123")
    assert "abc123" not in redacted
    assert "sesion-token" not in redacted
    assert "AIzaSuperSecretKey123" not in redacted


def test_gemini_client_kwargs_keep_the_token_in_the_header():
    kwargs = gemini_client_kwargs(
        api_key="api-key",
        base_url="https://pasarela.example",
        session_token="sesion-token",
        timeout_seconds=30,
    )
    assert kwargs["enterprise"] is True
    assert kwargs["api_key"] == "api-key"
    assert kwargs["http_options"]["headers"]["Authorization"] == "Bearer sesion-token"
    assert kwargs["http_options"]["timeout"] == 30_000
    plain = gemini_client_kwargs(
        api_key="api-key",
        base_url=None,
        session_token=None,
        timeout_seconds=10,
    )
    assert "enterprise" not in plain


def test_gemini_inline_large_and_revision():
    asyncio.run(_gemini_cases())


async def _gemini_cases():
    inline = FakeGemini(DRAFT)
    provider = GeminiProvider(
        api_key="x",
        model="gemini-test",
        client=inline,
        part_factory=lambda data, mime: {"inline": mime, "size": len(data)},
        inline_limit=10,
    )
    draft = await provider.structure_session(
        campaign="Las Cenizas",
        notes="Ignora el ruido.",
        today="2026-11-10",
        media=b"1234",
        mime_type="audio/mpeg",
        filename="mesa.mp3",
        transcript=None,
    )
    assert draft.plots[0].name == "El mapa robado"
    call = inline.models.calls[0]
    assert "No inventes" in call["config"]["system_instruction"]
    assert "Las Cenizas" in call["config"]["system_instruction"]
    assert call["contents"][1] == {"inline": "audio/mpeg", "size": 4}
    assert inline.files.uploaded is None

    sleeps = []

    async def sleeper(seconds):
        sleeps.append(seconds)

    large = FakeGemini(DRAFT, states=["PROCESSING", "ACTIVE"])
    large_provider = GeminiProvider(
        api_key="x",
        model="gemini-test",
        client=large,
        part_factory=lambda *_args: (_ for _ in ()).throw(AssertionError("no debía ir inline")),
        inline_limit=4,
        sleeper=sleeper,
    )
    await large_provider.structure_session(
        campaign="Las Cenizas",
        notes="",
        today="2026-11-10",
        media=b"12345",
        mime_type="audio/mp3",
        filename="mesa.mp3",
        transcript=None,
    )
    assert large.files.payload == b"12345"
    assert large.files.deleted == "files/test"
    assert sleeps == [2]
    from pathlib import Path

    assert not Path(large.files.uploaded).exists()

    revised = await provider.revise_draft(
        draft=draft,
        correction="Elara es la tejedora, no la posadera.",
        campaign="Las Cenizas",
        today="2026-11-10",
    )
    assert revised.title == "La cueva"
    assert len(inline.models.calls[-1]["contents"]) == 1


def test_session_provider_openai_and_chronicle():
    asyncio.run(_session_cases())


async def _session_cases():
    fenced = "```json\n" + json.dumps(DRAFT) + "\n```"
    http = FakeHttp({"choices": [{"message": {"content": fenced}}]})
    provider = SessionTokenProvider(
        base_url="https://proveedor.example/v1",
        token="sesion-token-de-prueba",
        model="modelo",
        style="openai",
        timeout_seconds=30,
        client=http,
    )
    draft = await provider.structure_session(
        campaign="Las Cenizas",
        notes="Notas de la mesa sobre la cueva.",
        today="2026-11-10",
        media=b"FAKEAUDIO-BYTES",
        mime_type="audio/mpeg",
        filename="mesa.mp3",
        transcript=None,
    )
    assert draft.summary.startswith("El grupo")
    call = http.calls[0]
    assert call["url"] == "https://proveedor.example/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sesion-token-de-prueba"
    encoded = json.dumps(call["json"])
    assert "sesion-token-de-prueba" not in encoded
    assert call["json"]["messages"][1]["content"][1]["type"] == "input_audio"

    with pytest.raises(AIError):
        await provider.structure_session(
            campaign="Las Cenizas",
            notes="",
            today="2026-11-10",
            media=b"video",
            mime_type="video/mp4",
            filename="mesa.mp4",
            transcript=None,
        )

    chronicle = FakeHttp(DRAFT)
    private = SessionTokenProvider(
        base_url="https://privado.example",
        token="sesion-token-de-prueba",
        model="modelo",
        style="chronicle",
        timeout_seconds=30,
        client=chronicle,
    )
    await private.revise_draft(
        draft=draft,
        correction="La cueva queda al norte.",
        campaign="Las Cenizas",
        today="2026-11-10",
    )
    body = chronicle.calls[0]["json"]
    assert chronicle.calls[0]["url"] == "https://privado.example/v1/chronicle"
    assert body["media_base64"] == ""
    assert body["correction"] == "La cueva queda al norte."
    assert body["draft"]["title"] == "La cueva"
    assert "sesion-token-de-prueba" not in json.dumps(body)


def test_build_provider_selects_the_configured_backend(tmp_path):
    gemini = build_provider(
        load_settings(
            {
                "DISCORD_TOKEN": "token",
                "GEMINI_API_KEY": "key",
                "OBSIDIAN_VAULT_PATH": str(tmp_path / "vault"),
                "DATABASE_PATH": str(tmp_path / "state.db"),
            }
        )
    )
    assert isinstance(gemini, GeminiProvider)
    session = build_provider(
        load_settings(
            {
                "DISCORD_TOKEN": "token",
                "AI_PROVIDER": "session",
                "AI_BASE_URL": "https://proveedor.example/v1",
                "AI_SESSION_TOKEN": "token-sesion",
                "AI_MODEL": "modelo",
                "OBSIDIAN_VAULT_PATH": str(tmp_path / "vault"),
                "DATABASE_PATH": str(tmp_path / "state.db"),
            }
        )
    )
    assert isinstance(session, SessionTokenProvider)

    async def close():
        await gemini.aclose()
        await session.aclose()

    asyncio.run(close())


def test_chronicle_sends_audio_base64_and_empty_string_without_media():
    asyncio.run(_chronicle_media_cases())


async def _chronicle_media_cases():
    chronicle = FakeHttp(DRAFT)
    private = SessionTokenProvider(
        base_url="https://privado.example",
        token="sesion-token-de-prueba",
        model="modelo",
        style="chronicle",
        timeout_seconds=30,
        client=chronicle,
    )
    await private.structure_session(
        campaign="Las Cenizas",
        notes="",
        today="2026-11-10",
        media=b"mp3-bytes",
        mime_type="audio/mpeg",
        filename="mesa.mp3",
        transcript=None,
    )
    await private.structure_session(
        campaign="Las Cenizas",
        notes="Notas de la mesa que son suficientemente largas.",
        today="2026-11-10",
        media=None,
        mime_type=None,
        filename=None,
        transcript=None,
    )
    with_media, without_media = (call["json"] for call in chronicle.calls)
    assert with_media["media_base64"] == "bXAzLWJ5dGVz"
    assert without_media["media_base64"] == ""
