"""Сцены на доске не запускают картинки."""

from __future__ import annotations

from pathlib import Path

from app.services.montage_board_apply import order_montage_pending_ops

ROOT = Path(__file__).resolve().parents[1]


def test_ui_has_generate_with_images_under_split() -> None:
    cells = (ROOT / "web/src/components/canvas/montage-scene-cells.tsx").read_text(
        encoding="utf-8"
    )
    assert "Разобрать на кадры" in cells
    assert "Генерация с картинками" not in cells
    assert "onApplyWithImages" not in cells
    board = (ROOT / "web/src/components/canvas/assemble-montage-board.tsx").read_text(
        encoding="utf-8"
    )
    assert "applySceneActionWithImagesNow" not in board
    assert "generateSceneWithImages" not in board
    assert "improveScene" in board
    assert "Улучшить сцену" in cells
    assert "live.slice(0, chainN)" not in board
    assert "shot_leftover" in board
    assert "scene_master" in board
    assert "Общий план" in board
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
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


def test_generate_scenes_queues_frames_not_images_and_has_vo_span() -> None:
    cells = (ROOT / "web/src/components/canvas/montage-scene-cells.tsx").read_text(
        encoding="utf-8"
    )
    board = (ROOT / "web/src/components/canvas/assemble-montage-board.tsx").read_text(
        encoding="utf-8"
    )
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    assert "закадр этой сцены" in cells
    assert "сохранить как закадр сцены" in cells
    assert "VoSpanEditor" in cells
    assert "VoUnusedWarn" in cells
    assert "неиспользованный закадр" in cells
    assert "unusedBefore" in cells
    assert "unusedAfter" in cells
    assert "vo_unused_before" in board
    assert "vo_unused_after" in board
    editor = cells[cells.index("function VoSpanEditor") : cells.index("export function AnchorCell")]
    assert "readOnly" not in editor
    assert "onChange" in editor
    assert "pendingFrameNumbers" in board
    assert "framesWord" in board
    assert "applyCoverageNow" in board
    assert 'startsWith("image_")' in board
    assert "queueImages" not in board
    assert "frame_numbers" in api
    gen_block = cells[
        cells.index("export function SceneGenerateBlock") : cells.index(
            "export function SceneActionBlock"
        )
    ]
    assert "Сгенерировать сцены" in gen_block
    assert "generateSceneAction" in gen_block
    assert "generateSceneWithImages" not in gen_block
