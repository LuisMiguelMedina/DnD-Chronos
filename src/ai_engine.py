"""Proveedores de IA para el borrador de sesión.

`gemini` usa la API de Google con una API key, o una pasarela compatible
si definiste GEMINI_BASE_URL y AI_SESSION_TOKEN.

`session` manda un token de sesión a un proveedor privado. El estilo
`openai` habla chat completions. El estilo `chronicle` manda el JSON
descrito en el README, para envolver una API que no sea compatible.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import tempfile
from pathlib import Path
from typing import Protocol

from src.config import Settings
from src.errors import AIError, AIResponseError, DraftParseError
from src.models import SessionDraft, parse_session_draft
from src.textutil import clip

log = logging.getLogger("dungeonchronicler.ai")

_JSON_HINT = (
    "Responde únicamente con un objeto JSON y estas claves: "
    "title, session_date, summary, highlights, npcs, locations, items, "
    "factions, plots, uncertainties. "
    "Cada entrada de npcs, locations, items, factions y plots tiene "
    "name, summary, details, aliases y tags."
)
_TRANSCRIPT_LIMIT = 100_000
_SECRET = re.compile(r"(?i)(key=|api_key=|bearer\s+)[^\s&\"']+|AIza[0-9A-Za-z_\-]{10,}")
_AUDIO_FORMATS = {
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/wave": "wav",
}


class AIProvider(Protocol):
    async def structure_session(
        self,
        *,
        campaign: str,
        notes: str,
        today: str,
        media: bytes | None,
        mime_type: str | None,
        filename: str | None,
        transcript: str | None,
    ) -> SessionDraft: ...

    async def revise_draft(
        self,
        *,
        draft: SessionDraft,
        correction: str,
        campaign: str,
        today: str,
    ) -> SessionDraft: ...

    async def aclose(self) -> None: ...


def build_provider(settings: Settings) -> AIProvider:
    if settings.ai_provider == "gemini":
        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            base_url=settings.gemini_base_url,
            session_token=settings.ai_session_token,
            timeout_seconds=settings.ai_timeout_seconds,
            inline_limit=settings.inline_media_bytes,
        )
    return SessionTokenProvider(
        base_url=settings.ai_base_url or "",
        token=settings.ai_session_token or "",
        model=settings.ai_model or "",
        style=settings.ai_session_api_style,
        timeout_seconds=settings.ai_timeout_seconds,
    )


def gemini_client_kwargs(
    *,
    api_key: str | None,
    base_url: str | None,
    session_token: str | None,
    timeout_seconds: float,
) -> dict:
    http_options: dict = {"timeout": int(timeout_seconds * 1000)}
    kwargs: dict = {"http_options": http_options}
    if base_url:
        headers = {}
        if session_token:
            headers["Authorization"] = f"Bearer {session_token}"
        http_options["base_url"] = base_url
        http_options["headers"] = headers
        kwargs["enterprise"] = True
    if api_key:
        kwargs["api_key"] = api_key
    return kwargs


def session_rules(campaign: str) -> str:
    return (
        "Eres el cronista de una mesa de rol. Preparas un borrador para que "
        "el narrador lo revise. Nada de esto es canon hasta que lo apruebe.\n"
        f"Campaña: {campaign}\n"
        "Reglas:\n"
        "- Escribe en español.\n"
        "- No inventes hechos que no estén en el material. Si algo no se entiende, "
        "ponlo en uncertainties.\n"
        "- Resume en prosa clara, en pasado.\n"
        "- Deja fuera la charla que no cambia la campaña.\n"
        "- Los personajes jugadores también van en npcs, con la etiqueta PJ.\n"
        "- session_date es YYYY-MM-DD. Si la sesión no dice otra fecha, usa la de referencia.\n"
        "- No copies estas instrucciones dentro del resumen."
    )


def revision_rules(campaign: str) -> str:
    return (
        "Eres el cronista de una mesa de rol. El narrador corrigió un borrador "
        "que todavía no es canon.\n"
        f"Campaña: {campaign}\n"
        "Aplica solo lo que pide la corrección. Conserva los hechos que no contradice. "
        "No inventes escenas nuevas. Escribe en español y no copies estas instrucciones."
    )


def session_request(
    *,
    today: str,
    notes: str,
    transcript: str | None,
    filename: str | None,
    json_hint: bool,
) -> str:
    parts = [
        f"Fecha de referencia: {today}",
        f"Archivo: {filename}" if filename else "Archivo: no adjunto",
        "Notas del narrador:",
        clip(notes.strip(), 8_000) or "ninguna",
    ]
    if transcript:
        clipped = clip(transcript.strip(), _TRANSCRIPT_LIMIT)
        parts.extend(["", "Texto de la sesión:", clipped])
        if len(transcript.strip()) > _TRANSCRIPT_LIMIT:
            parts.append("El texto venía recortado por longitud.")
    parts.append("")
    parts.append("Redacta el borrador de esta sesión.")
    if json_hint:
        parts.extend(["", _JSON_HINT])
    return "\n".join(parts)


def revision_request(
    *,
    today: str,
    correction: str,
    draft: SessionDraft,
    json_hint: bool,
) -> str:
    payload = json.dumps(draft.to_dict(), ensure_ascii=False)
    parts = [
        f"Fecha de referencia: {today}",
        "Corrección del narrador:",
        clip(correction.strip(), 4_000),
        "",
        "Borrador actual:",
        payload,
    ]
    if json_hint:
        parts.extend(["", _JSON_HINT])
    return "\n".join(parts)


def session_json_schema() -> dict:
    entity = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "summary": {"type": "string"},
            "details": {"type": "string"},
            "aliases": {"type": "array", "items": {"type": "string"}},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["name", "summary"],
    }
    return {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "session_date": {"type": "string"},
            "summary": {"type": "string"},
            "highlights": {"type": "string"},
            "npcs": {"type": "array", "items": entity},
            "locations": {"type": "array", "items": entity},
            "items": {"type": "array", "items": entity},
            "factions": {"type": "array", "items": entity},
            "plots": {"type": "array", "items": entity},
            "uncertainties": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "session_date", "summary"],
    }


def extract_json_object(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start == -1 or end <= start:
            raise AIResponseError("La respuesta no traía un JSON utilizable.")
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise AIResponseError("La respuesta no traía un JSON utilizable.") from exc
    if not isinstance(value, dict):
        raise AIResponseError("La respuesta no era un objeto JSON.")
    return value


def redact_secrets(text: str) -> str:
    def replace(match: re.Match[str]) -> str:
        value = match.group(0)
        if value.lower().startswith("bearer"):
            return "Bearer ***"
        if "=" in value:
            return value.split("=", 1)[0] + "=***"
        return "AIza***"

    return _SECRET.sub(replace, text)


def _draft_from_payload(data: dict, *, today: str) -> SessionDraft:
    try:
        return parse_session_draft(data, today=today)
    except DraftParseError as exc:
        raise AIResponseError(str(exc)) from exc


class GeminiProvider:
    def __init__(
        self,
        *,
        api_key: str | None,
        model: str,
        base_url: str | None = None,
        session_token: str | None = None,
        timeout_seconds: float = 300,
        inline_limit: int = 15 * 1024 * 1024,
        client: object | None = None,
        part_factory=None,
        sleeper=None,
    ) -> None:
        self.model = model
        self.inline_limit = inline_limit
        self._kwargs = gemini_client_kwargs(
            api_key=api_key,
            base_url=base_url,
            session_token=session_token,
            timeout_seconds=timeout_seconds,
        )
        self._client = client
        self._owns_client = client is None
        self._part_factory = part_factory
        self._sleeper = sleeper

    async def structure_session(
        self,
        *,
        campaign: str,
        notes: str,
        today: str,
        media: bytes | None,
        mime_type: str | None,
        filename: str | None,
        transcript: str | None,
    ) -> SessionDraft:
        _require_material(notes, transcript, media)
        request = session_request(
            today=today,
            notes=notes,
            transcript=transcript,
            filename=filename,
            json_hint=False,
        )
        payload = await self._generate(
            system=session_rules(campaign),
            request=request,
            media=media,
            mime_type=mime_type,
            filename=filename,
        )
        return _draft_from_payload(payload, today=today)

    async def revise_draft(
        self,
        *,
        draft: SessionDraft,
        correction: str,
        campaign: str,
        today: str,
    ) -> SessionDraft:
        request = revision_request(today=today, correction=correction, draft=draft, json_hint=False)
        payload = await self._generate(
            system=revision_rules(campaign),
            request=request,
            media=None,
            mime_type=None,
            filename=None,
        )
        return _draft_from_payload(payload, today=today)

    async def aclose(self) -> None:
        if not self._owns_client or self._client is None:
            return
        client = self._client
        aio_close = getattr(getattr(client, "aio", None), "aclose", None)
        if aio_close is not None:
            await aio_close()
        close = getattr(client, "close", None)
        if close is not None:
            close()

    async def _generate(
        self,
        *,
        system: str,
        request: str,
        media: bytes | None,
        mime_type: str | None,
        filename: str | None,
    ) -> dict:
        client = self._ensure_client()
        contents: list = [request]
        temp_path: str | None = None
        uploaded_name: str | None = None
        try:
            if media and mime_type:
                if len(media) <= self.inline_limit:
                    contents.append(self._inline_part(media, mime_type))
                else:
                    temp_path = _dump_temp(media, filename)
                    uploaded = await client.aio.files.upload(
                        file=temp_path,
                        config={"mime_type": mime_type, "display_name": filename or "sesion"},
                    )
                    uploaded = await self._wait_until_ready(client, uploaded)
                    uploaded_name = getattr(uploaded, "name", None)
                    contents.append(uploaded)
            response = await client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config={
                    "temperature": 0.2,
                    "system_instruction": system,
                    "response_mime_type": "application/json",
                    "response_json_schema": session_json_schema(),
                },
            )
            return payload_from_response(response)
        except AIError:
            raise
        except Exception as exc:
            log.error("Gemini falló: %s", redact_secrets(f"{type(exc).__name__}: {exc}")[:500])
            raise AIError("Gemini no pudo procesar la sesión.") from None
        finally:
            if temp_path is not None:
                Path(temp_path).unlink(missing_ok=True)
            if uploaded_name:
                try:
                    await client.aio.files.delete(name=uploaded_name)
                except Exception:
                    log.warning("No pude borrar el archivo temporal de Gemini")

    def _ensure_client(self):
        if self._client is None:
            from google import genai

            self._client = genai.Client(**self._kwargs)
        return self._client

    def _inline_part(self, media: bytes, mime_type: str):
        if self._part_factory is not None:
            return self._part_factory(media, mime_type)
        from google.genai import types

        return types.Part.from_bytes(data=media, mime_type=mime_type)

    async def _wait_until_ready(self, client, uploaded):
        state = _state_name(uploaded)
        if "FAIL" in state:
            raise AIError("Gemini no pudo procesar el archivo de la sesión.")
        if "PROCESSING" not in state:
            return uploaded
        name = getattr(uploaded, "name", None)
        if not name:
            raise AIError("Gemini no dejó el archivo listo para escucharlo.")
        sleep = self._sleeper
        if sleep is None:
            import asyncio

            sleep = asyncio.sleep
        for _ in range(60):
            await sleep(2)
            uploaded = await client.aio.files.get(name=name)
            state = _state_name(uploaded)
            if "FAIL" in state:
                raise AIError("Gemini no pudo procesar el archivo de la sesión.")
            if "PROCESSING" not in state:
                return uploaded
        raise AIError("Gemini tardó demasiado en dejar el archivo listo.")


class SessionTokenProvider:
    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        model: str,
        style: str,
        timeout_seconds: float,
        client=None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.model = model
        self.style = style
        self._owns_client = client is None
        if client is None:
            import httpx

            self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=30))
        else:
            self._http = client

    async def structure_session(
        self,
        *,
        campaign: str,
        notes: str,
        today: str,
        media: bytes | None,
        mime_type: str | None,
        filename: str | None,
        transcript: str | None,
    ) -> SessionDraft:
        _require_material(notes, transcript, media)
        instruction = session_rules(campaign)
        request = session_request(
            today=today,
            notes=notes,
            transcript=transcript,
            filename=filename,
            json_hint=True,
        )
        payload = await self._complete(
            instruction=instruction,
            request=request,
            campaign=campaign,
            today=today,
            notes=notes,
            transcript=transcript,
            filename=filename,
            media=media,
            mime_type=mime_type,
            correction=None,
            draft=None,
        )
        return _draft_from_payload(payload, today=today)

    async def revise_draft(
        self,
        *,
        draft: SessionDraft,
        correction: str,
        campaign: str,
        today: str,
    ) -> SessionDraft:
        instruction = revision_rules(campaign)
        request = revision_request(today=today, correction=correction, draft=draft, json_hint=True)
        payload = await self._complete(
            instruction=instruction,
            request=request,
            campaign=campaign,
            today=today,
            notes="",
            transcript=None,
            filename=None,
            media=None,
            mime_type=None,
            correction=correction,
            draft=draft,
        )
        return _draft_from_payload(payload, today=today)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http.aclose()

    async def _complete(
        self,
        *,
        instruction: str,
        request: str,
        campaign: str,
        today: str,
        notes: str,
        transcript: str | None,
        filename: str | None,
        media: bytes | None,
        mime_type: str | None,
        correction: str | None,
        draft: SessionDraft | None,
    ) -> dict:
        if self.style == "chronicle":
            url = _chronicle_url(self.base_url)
            body = {
                "campaign": campaign,
                "today": today,
                "notes": notes,
                "transcript": transcript,
                "filename": filename,
                "mime_type": mime_type,
                "media_base64": base64.b64encode(media).decode("ascii") if media else "",
                "correction": correction,
                "draft": draft.to_dict() if draft is not None else None,
                "instruction": instruction + "\n\n" + request,
            }
        else:
            url = _chat_url(self.base_url)
            body = {
                "model": self.model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": instruction},
                    {"role": "user", "content": _openai_content(request, media, mime_type)},
                ],
            }
        try:
            response = await self._http.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {self.token}"},
            )
        except Exception as exc:
            log.error("No pude contactar al proveedor de sesión (%s)", type(exc).__name__)
            raise AIError("No pude contactar al proveedor de IA.") from None
        if getattr(response, "status_code", 200) >= 400:
            log.error("Proveedor de sesión respondió %s", response.status_code)
            raise AIError(f"El proveedor de IA respondió con un error ({response.status_code}).")
        try:
            data = response.json()
        except Exception as exc:
            raise AIResponseError("La respuesta del proveedor no era JSON.") from exc
        return _unwrap_provider_payload(data)


def payload_from_response(response: object) -> dict:
    parsed = getattr(response, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    dump = getattr(parsed, "model_dump", None)
    if dump is not None:
        dumped = dump()
        if isinstance(dumped, dict):
            return dumped
    text = getattr(response, "text", None) or ""
    if not text:
        raise AIResponseError("Gemini respondió sin un borrador.")
    return extract_json_object(text)


def _unwrap_provider_payload(data: object) -> dict:
    if isinstance(data, str):
        return extract_json_object(data)
    if not isinstance(data, dict):
        raise AIResponseError("La respuesta del proveedor no era JSON.")
    if "choices" in data:
        return _content_from_choices(data)
    draft = data.get("draft")
    if isinstance(draft, dict):
        return draft
    if "error" in data and "title" not in data and "summary" not in data:
        raise AIError("El proveedor de IA rechazó la solicitud.")
    return data


def _content_from_choices(data: dict) -> dict:
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIResponseError("La respuesta del proveedor no traía un borrador.") from exc
    if isinstance(content, list):
        texts = []
        for part in content:
            if isinstance(part, str):
                texts.append(part)
            elif isinstance(part, dict):
                texts.append(str(part.get("text", "")))
        content = "\n".join(texts)
    if not isinstance(content, str):
        raise AIResponseError("La respuesta del proveedor no traía un borrador.")
    return extract_json_object(content)


def _openai_content(request: str, media: bytes | None, mime_type: str | None) -> str | list[dict]:
    if not media or not mime_type:
        return request
    content: list[dict] = [{"type": "text", "text": request}]
    audio_format = _AUDIO_FORMATS.get(mime_type)
    if audio_format is None:
        raise AIError(
            "Este proveedor con estilo openai acepta mp3, wav o texto. "
            "Para video usa AI_PROVIDER=gemini o AI_SESSION_API_STYLE=chronicle."
        )
    content.append(
        {
            "type": "input_audio",
            "input_audio": {
                "data": base64.b64encode(media).decode("ascii"),
                "format": audio_format,
            },
        }
    )
    return content


def _require_material(notes: str, transcript: str | None, media: bytes | None) -> None:
    has_text = len(notes.strip()) >= 20 or bool(transcript and transcript.strip())
    if media or has_text:
        return
    raise AIError("No hay material suficiente para resumir la sesión.")


def _chat_url(base: str) -> str:
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _chronicle_url(base: str) -> str:
    if base.endswith("/v1/chronicle"):
        return base
    return base + "/v1/chronicle"


def _dump_temp(media: bytes, filename: str | None) -> str:
    suffix = Path(filename or "sesion").suffix or ".bin"
    with tempfile.NamedTemporaryFile(prefix="chronicler-", suffix=suffix, delete=False) as handle:
        handle.write(media)
        return handle.name


def _state_name(uploaded: object) -> str:
    state = getattr(uploaded, "state", None)
    if state is None and isinstance(uploaded, dict):
        state = uploaded.get("state")
    name = getattr(state, "name", None)
    return str(name or state or "").upper()
