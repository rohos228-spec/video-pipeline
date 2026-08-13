"""db_frames.json snapshot for img_pr after scene_grammar."""

from __future__ import annotations

import json
from types import SimpleNamespace

from app.services.db_frames_context import build_img_pr_db_context


def test_img_pr_db_context_picks_scene_grammar_keys() -> None:
    fr = SimpleNamespace(
        number=1,
        uuid="abc123",
        voiceover_text="закадр кусок — не должен попасть в JSON",
        meaning="",
        attrs={
            "place": "метро вагон",
            "lighting": "fluorescent overhead",
            "scene_lighting": "fluorescent overhead",
            "shot01_bg": "серый пол и поручни",
            "shot01_action": "пассажир читает газету",
            "shot01_description": "средний план, камера на уровне груди",
            "accent": "развёрнутая газета",
            "scene_sense": "пассажиры держатся за поручни",
            "scene_feature": "Средний план персонажа",
            "visual_type": "Кинематографический реализм",
            "shot01_props": "газета, поручень",
            "characters": "c01",
            "noise": "ignore me",
        },
    )
    ctx = build_img_pr_db_context(
        project_id=53,
        slug="aum",
        frames=[fr],
        characters=[{"id": "c01", "имя": "Asahara", "внешность": "…"}],
        general_plan="эпоха 1995",
        include_field_map=True,
    )
    assert ctx["source"] == "db_v2"
    assert ctx["general_plan"] == "эпоха 1995"
    assert len(ctx["frames"]) == 1
    row = ctx["frames"][0]
    assert row["uuid"] == "abc123"
    assert "voiceover_text" not in row
    assert row["place"] == "метро вагон"
    assert row["shot01_bg"].startswith("серый")
    assert row["lighting"] == "fluorescent overhead"
    assert row["персонажи"] == "c01"
    assert row["characters"] == "c01"
    assert "noise" not in row
    assert ctx["characters"][0]["id"] == "c01"
    assert "shot01_bg" in ctx["field_map"]


def test_img_pr_db_context_skips_frames_without_uuid() -> None:
    bad = SimpleNamespace(number=2, uuid="", voiceover_text="x", meaning="", attrs={})
    ctx = build_img_pr_db_context(
        project_id=1,
        slug="x",
        frames=[bad],
        characters=[],
    )
    assert ctx["frames"] == []


def test_excel_gpt_context_is_slim_and_keeps_vo_snippet() -> None:
    fr = SimpleNamespace(
        number=1, uuid="ab" * 12, voiceover_text="слово " * 200,
        meaning="m", attrs={"place": "кухня", "noise": "X" * 5000, "shot01_bg": "стол"},
    )
    from app.services.db_frames_context import build_excel_gpt_db_context
    ctx = build_excel_gpt_db_context(project_id=14, slug="x", frames=[fr], characters=[])
    row = ctx["frames"][0]
    assert "noise" not in row
    assert "attrs" not in row
    assert row["place"] == "кухня"
    assert len(row["voiceover_text"]) <= 400
    blob = json.dumps(ctx)
    assert len(blob) < 20_000
