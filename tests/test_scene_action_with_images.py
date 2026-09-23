"""Кнопка «Генерация с картинками» под «Разобрать на кадры»."""

from __future__ import annotations

from pathlib import Path

from app.services.montage_board_apply import order_montage_pending_ops

ROOT = Path(__file__).resolve().parents[1]


def test_ui_has_generate_with_images_under_split() -> None:
    cells = (ROOT / "web/src/components/canvas/montage-scene-cells.tsx").read_text(
        encoding="utf-8"
    )
    assert "Разобрать на кадры" in cells
    assert "Генерация с картинками" in cells
    split_btn = cells.index("        Разобрать на кадры")
    images_btn = cells.index("        Генерация с картинками")
    assert split_btn < images_btn
    assert "onApplyWithImages" in cells
    board = (ROOT / "web/src/components/canvas/assemble-montage-board.tsx").read_text(
        encoding="utf-8"
    )
    assert "applySceneActionWithImagesNow" in board
    assert "generateSceneWithImages" in board
    assert "improveScene" in board
    assert "Улучшить сцену" in cells
    assert "live.slice(0, chainN)" not in board
    assert "shot_leftover" in board
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    assert "scene-generate-with-images" in api
    assert "scene-improve" in api


def test_apply_runs_scene_action_before_image_ai_change() -> None:
    ordered = order_montage_pending_ops(
        [
            {"type": "image_ai_change", "frame_number": 2, "shot": 1},
            {"type": "coverage_scene_action", "frame_number": 1, "action": "a → b"},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1},
        ]
    )
    types = [str(op["type"]) for op in ordered]
    assert types == [
        "coverage_scene_action",
        "image_ai_change",
        "image_ai_change",
    ]
    assert [op["frame_number"] for op in ordered[1:]] == [1, 2]
