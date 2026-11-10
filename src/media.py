"""Clasifica los archivos que el narrador manda al bot."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.errors import MediaError

_EXT_KIND = {
    ".mp3": "audio",
    ".wav": "audio",
    ".ogg": "audio",
    ".oga": "audio",
    ".flac": "audio",
    ".m4a": "audio",
    ".aac": "audio",
    ".opus": "audio",
    ".mp4": "video",
    ".webm": "video",
    ".mov": "video",
    ".mkv": "video",
    ".txt": "text",
    ".md": "text",
    ".markdown": "text",
}

_EXT_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".flac": "audio/flac",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".opus": "audio/opus",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
}


@dataclass(frozen=True)
class PreparedMedia:
    filename: str
    kind: str
    mime_type: str | None
    data: bytes | None
    transcript: str | None


def classify(filename: str, content_type: str | None, size: int, *, max_bytes: int) -> str:
    if size <= 0:
        raise MediaError("El archivo está vacío.")
    if size > max_bytes:
        if max_bytes >= 1024 * 1024:
            limit = f"{max_bytes // (1024 * 1024)} MB"
        else:
            limit = f"{max_bytes} bytes"
        raise MediaError(f"El archivo supera el límite de {limit}.")
    kind = detect_kind(filename, content_type)
    if kind is None:
        raise MediaError("Necesito audio, video o un texto (.txt o .md).")
    return kind


def prepare_media(
    filename: str,
    content_type: str | None,
    data: bytes,
    *,
    max_bytes: int,
) -> PreparedMedia:
    safe_name = Path(filename or "sesion").name[:200] or "sesion"
    kind = classify(safe_name, content_type, len(data), max_bytes=max_bytes)
    if kind == "text":
        transcript = data.decode("utf-8", errors="replace").strip()
        if len(transcript) < 20:
            raise MediaError("El texto es demasiado corto para resumir una sesión.")
        return PreparedMedia(
            filename=safe_name,
            kind="text",
            mime_type="text/plain",
            data=None,
            transcript=transcript,
        )
    return PreparedMedia(
        filename=safe_name,
        kind=kind,
        mime_type=_mime(safe_name, content_type, kind),
        data=data,
        transcript=None,
    )


def detect_kind(filename: str, content_type: str | None) -> str | None:
    extension = Path(filename or "").suffix.lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime in {"", "application/octet-stream", "binary/octet-stream"}:
        return _EXT_KIND.get(extension)
    if mime.startswith("audio/"):
        return "audio"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("text/") or mime in {"application/json", "text/markdown"}:
        return "text"
    return _EXT_KIND.get(extension)


def _mime(filename: str, content_type: str | None, kind: str) -> str:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if mime and mime not in {"application/octet-stream", "binary/octet-stream"}:
        return mime
    return _EXT_MIME.get(Path(filename).suffix.lower(), f"{kind}/octet-stream")
