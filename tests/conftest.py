import pytest

from src.models import parse_session_draft


@pytest.fixture
def draft():
    return parse_session_draft(
        {
            "title": "La cueva",
            "session_date": "2026-11-10",
            "summary": "El grupo encontró a Élara y cruzó el río.",
            "highlights": "Un mapa húmedo.",
            "npcs": [
                {
                    "name": "Elara Voss",
                    "summary": "Tejedora",
                    "details": "Les dio un mapa",
                    "aliases": ["La Tejedora"],
                    "tags": ["PJ"],
                }
            ],
            "locations": [{"name": "Cueva del Norte", "summary": "Húmeda"}],
            "plots": ["El mapa robado"],
            "uncertainties": ["No se oyó el nombre del mapa"],
        },
        today="2026-11-10",
    )
