"""SoT закадра для scene_design: кадры, не устаревший script_text."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design.assembler import validate_payload
from app.services.scene_design.context_builder import full_voiceover


def test_full_voiceover_prefers_frame_join_over_short_script() -> None:
    """Баг #59: script_text короткий, VO в кадрах полный → цитаты не матчились."""
    project = SimpleNamespace(
        script_text="Короткий устаревший закадр про начало."
    )
    frames = [
        SimpleNamespace(voiceover_text="К осени подозрительных совпадений стало слишком много."),
        SimpleNamespace(voiceover_text=""),  # SET-ребёнок
        SimpleNamespace(
            voiceover_text="О́льга Гальцева, единственная выжившая потерпевшая, позднее дала показания."
        ),
    ]
    vo = full_voiceover(project, frames)
    assert "К осени подозрительных" in vo
    assert "Гальцева" in vo
    assert "устаревший" not in vo


def test_full_voiceover_falls_back_to_script_when_frames_empty() -> None:
    project = SimpleNamespace(script_text="Только script.")
    frames = [SimpleNamespace(voiceover_text=""), SimpleNamespace(voiceover_text=None)]
    assert full_voiceover(project, frames) == "Только script."


def test_validate_accepts_accented_quotes_via_fold() -> None:
    vo = "Ольга Гальцева дала показания следствию."
    frames = [SimpleNamespace(uuid="u1", duration_seconds=3.0, voiceover_text=vo)]
    payload = {
        "characters": [],
        "scenes": [
            {
                "id_scene": "sc01",
                "start_words": "О́льга Гальцева",  # combining accent
                "end_words": "показания следствию",
                "время_сек": 3.0,
            }
        ],
        "ops": [{"frame_uuid": "u1", "fields": {"id_scene": "sc01", "место": "квартира"}}],
    }
    assert validate_payload(SimpleNamespace(id=1), frames, payload, vo) == []
