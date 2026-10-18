"""Повторное ревью #352 №8: пустой закадр не обнуляет список кадров.

Совпадает с #354 bbdd47f2; снятие карантина с shots[0] — cbc49690.
"""

from __future__ import annotations

import pytest
from loguru import logger


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def test_assign_scene_vo_keeps_shots_when_voiceover_empty(warn_log) -> None:
    from app.services.shot_templates import _assign_scene_vo

    shots = [
        {"id": "a", "закадр": "уже было", "действие": "стоит"},
        {"id": "b", "закадр": "", "действие": "садится"},
    ]
    out = _assign_scene_vo(shots, "")
    assert [s["id"] for s in out] == ["a", "b"]
    assert all(s.get("карантин") for s in out)
    assert "пустой закадр" in out[0]["пометка"]
    assert any("WARN _assign_scene_vo" in line for line in warn_log)


def test_full_vo_on_first_shot_clears_empty_quarantine(monkeypatch, warn_log) -> None:
    from app.services.shot_templates import _assign_scene_vo

    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.vo_shot_expand.kadry_vo_partition_aligned",
        lambda vo, shots: ["", ""],
    )
    monkeypatch.setattr(
        "app.services.scene_design.camera_expand.split_text_into_parts",
        lambda text, n: ["", ""],
    )
    out = _assign_scene_vo(
        [{"id": "a", "закадр": ""}, {"id": "b", "закадр": ""}],
        "Целое предложение сцены.",
    )
    assert [s["id"] for s in out] == ["a", "b"]
    assert out[0]["закадр"] == "Целое предложение сцены."
    assert out[0].get("карантин") is not True
    assert out[1].get("карантин") is True
    assert any("карантин снят" in line for line in warn_log)
