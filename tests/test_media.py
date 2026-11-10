import pytest

from src.errors import MediaError
from src.media import classify, detect_kind, prepare_media


def test_classifies_audio_video_and_text():
    assert detect_kind("mesa.mp3", "application/octet-stream") == "audio"
    assert detect_kind("mesa.mp4", "video/mp4") == "video"
    assert detect_kind("notas.md", None) == "text"
    assert detect_kind("foto.png", "image/png") is None
    with pytest.raises(MediaError):
        classify("foto.png", "image/png", 100, max_bytes=1000)


def test_prepare_text_and_audio():
    notes = prepare_media("../../notas.md", "text/plain", b"El grupo cruzo el rio de noche.", max_bytes=1000)
    assert notes.filename == "notas.md"
    assert notes.transcript.startswith("El grupo")
    assert notes.data is None

    audio = prepare_media("mesa.mp3", "application/octet-stream", b"abc", max_bytes=1000)
    assert audio.kind == "audio"
    assert audio.mime_type == "audio/mpeg"
    assert audio.data == b"abc"


def test_rejects_empty_short_and_huge_files():
    with pytest.raises(MediaError):
        prepare_media("mesa.mp3", "audio/mpeg", b"", max_bytes=1000)
    with pytest.raises(MediaError):
        prepare_media("notas.txt", "text/plain", b"muy corto", max_bytes=1000)
    with pytest.raises(MediaError, match="500 bytes"):
        prepare_media("mesa.mp3", "audio/mpeg", b"x" * 600, max_bytes=500)
