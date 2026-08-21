"""Тощий контекст чанков scene_design."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.scene_design import context_builder as cb


def test_compact_characters_and_world() -> None:
    chars = cb.compact_characters_slice(
        {
            "characters": [
                {
                    "id": "c01",
                    "name": "Александр",
                    "role": "герой",
                    "bio": "очень длинная биография " * 20,
                }
            ]
        }
    )
    assert chars["characters"][0]["id"] == "c01"
    assert "bio" not in chars["characters"][0]

    world = cb.compact_world_slice(
        {
            "locations": [
                {
                    "id": "loc01",
                    "name": "Подъезд",
                    "zones": ["лифт", "ящики", "лестница", "двор", "лишнее"],
                    "essay": "огромный текст",
                }
            ]
        }
    )
    assert world["locations"][0]["id"] == "loc01"
    assert "essay" not in world["locations"][0]


def test_build_shared_context_chunk_mode_short_plan() -> None:
    fr = SimpleNamespace(
        uuid="u1",
        number=1,
        voiceover_text="А" * 28,  # 2 сек при 14 cps
        duration_seconds=99.0,
    )
    project = SimpleNamespace(
        script_text="",
        general_plan="ПЛАН " * 200,
        meta={"image_style": "noir documentary " * 20},
    )
    full = cb.build_shared_context(project, [fr], mode="full")
    chunk = cb.build_shared_context(project, [fr], mode="chunk")
    assert "# ЗАКАДР ЧАНКА" in chunk
    assert "# ПОЛНЫЙ ЗАКАДР" in full
    assert len(chunk) < len(full)
