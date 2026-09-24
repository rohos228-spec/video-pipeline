"""Сцены: улучшить без картинок; отдельная кнопка пишет промты и PNG."""

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
    assert "generateSceneImagesNow" in board
    assert "sceneImageOpsForFrames" in board
    assert "frameStillUrl" in board
    assert "image_parent_url" in board
    assert 'slot: "parent"' in board
    assert "improveScene" in board
    assert "Улучшить сцену" in cells
    assert "Сгенерировать изображения" in cells
    assert "onGenerateImages" in cells
    assert "live.slice(0, chainN)" not in board
    assert "shot_leftover" in board
    assert 'label: "Общий план"' not in board
    assert 'if (key === "scene_master")' not in board
    api = (ROOT / "web/src/lib/api.ts").read_text(encoding="utf-8")
    assert "scene-improve" in api
    apply_src = (ROOT / "app/services/montage_board_apply.py").read_text(encoding="utf-8")
    assert 'slot=str(op.get("slot") or "")' in apply_src
    assert apply_src.count('slot=str(op.get("slot") or "")') >= 2


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


def test_parent_slot_sorts_before_child_shot() -> None:
    ordered = order_montage_pending_ops(
        [
            {"type": "image_ai_change", "frame_number": 2, "shot": 1},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1},
            {"type": "image_ai_change", "frame_number": 1, "shot": 1, "slot": "parent"},
        ]
    )
    imgs = [op for op in ordered if op["type"] == "image_ai_change"]
    assert imgs[0].get("slot") == "parent"
    assert imgs[0]["frame_number"] == 1
    assert imgs[1]["frame_number"] == 1
    assert imgs[1].get("slot") != "parent"
    assert imgs[2]["frame_number"] == 2


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
    assert "пропущен закадр" in cells
    assert "export function VoUnusedWarn" in cells
    assert "createPortal" in cells
    warn = cells[
        cells.index("export function VoUnusedWarn") : cells.index("function VoSpanEditor")
    ]
    assert "pointer-events-none" not in warn
    assert "select-text" in warn
    assert "overPanel" in warn
    assert "cursor-text" in warn
    assert "unusedBefore" in board
    assert "unusedAfter" in board
    assert "unusedBetween" in board
    assert "vo_unused_before" in board
    assert "vo_unused_after" in board
    assert "vo_unused_between" in board
    assert "vo_unused_after_number" in board
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
    assert "Сгенерировать изображения" in gen_block
    assert "onGenerateImages" in gen_block
