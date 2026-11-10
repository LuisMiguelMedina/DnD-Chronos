import json

from src.ai_engine import ChroniclerDraft, _parse_model_json, _strip_json_fence, format_draft_preview


def test_strip_json_fence():
    assert _strip_json_fence("```json\n{\"a\":1}\n```") == '{"a":1}'


def test_parse_model_json():
    data = _parse_model_json('{"session_title":"T","session_summary":"S","npcs":[],"places":[],"events":[],"open_threads":[]}')
    draft = ChroniclerDraft.from_dict(data)
    assert draft.session_title == "T"
    assert draft.session_summary == "S"


def test_format_draft_preview_truncates():
    draft = ChroniclerDraft(
        session_title="X",
        session_summary="y" * 5000,
        npcs=[],
        places=[],
        events=[],
        open_threads=["hilo"],
    )
    preview = format_draft_preview(draft, max_len=100)
    assert len(preview) <= 100
    assert preview.endswith("...")
