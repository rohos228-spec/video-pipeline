"""Очередь монтажа должна сохраняться на сервер и не затирать highlights."""

from __future__ import annotations

from app.services.montage_board_meta import (
    add_highlight,
    clear_failed_highlight,
    montage_meta,
    normalize_queue_ops,
    set_montage_meta,
    should_accept_queue_save,
    slot_key_from_op,
)


def test_slot_key_from_op_image_and_video() -> None:
    assert slot_key_from_op({"type": "image_regen", "frame_number": 9, "shot": 1}) == "9:image1"
    assert slot_key_from_op({"type": "video_regen", "frame_number": 9, "shot": 1}) == "9:1"


def test_apply_should_keep_prior_highlights_logic() -> None:
    """Симуляция нового apply: старые highlights остаются, failed чистится по op."""
    board: dict = {
        "highlights": ["1:1", "2:image1"],
        "failed_highlights": ["3:1", "4:1"],
        "pending_ops": [],
    }
    ops = [{"type": "video_regen", "frame_number": 3, "shot": 1}]
    for op in ops:
        key = slot_key_from_op(op)
        assert key
        clear_failed_highlight(board, key)
    add_highlight(board, "3:1")
    assert "1:1" in board["highlights"]
    assert "2:image1" in board["highlights"]
    assert "3:1" in board["highlights"]
    assert board["failed_highlights"] == ["4:1"]


class _P:
    def __init__(self) -> None:
        self.meta: dict = {}
        self.id = 99


def test_set_montage_meta_pending_ops_roundtrip() -> None:
    p = _P()
    board = montage_meta(p)
    board["pending_ops"] = [
        {
            "type": "video_regen_prompt",
            "frame_number": 42,
            "shot": 1,
            "prompt": "fix",
        }
    ]
    set_montage_meta(p, board)
    again = montage_meta(p)
    assert again["pending_ops"][0]["frame_number"] == 42


def test_normalize_queue_keeps_all_scene_row_fields() -> None:
    """Строки сцены: ракурс / движение / стык / свет / набор доживают до apply."""
    cleaned = normalize_queue_ops(
        [
            {"type": "coverage_angle", "frame_number": 2, "shot": 1, "angle": "с плеча"},
            {"type": "coverage_move", "frame_number": 2, "shot": 1, "move": "наезд"},
            {
                "type": "coverage_stitch",
                "frame_number": 2,
                "shot": 1,
                "stitch": "cut_on_action",
            },
            {"type": "coverage_light", "frame_number": 1, "shot": 1, "light": "контровой"},
            {"type": "coverage_set", "frame_number": 1, "shot": 1, "set": "кабинет"},
            {"type": "coverage_plan", "frame_number": 1, "shot": 1, "plan": "ДАЛЬНИЙ"},
            {"type": "coverage_action", "frame_number": 1, "shot": 1, "action": "вошёл"},
            {
                "type": "coverage_anchors",
                "frame_number": 1,
                "shot": 1,
                "anchors": [{"якорь": "В сентябре", "изменение": "было → стало"}],
            },
            {"type": "coverage_kind", "frame_number": 3, "shot": 1, "kind": "child",
             "parent_number": "1"},
            {"type": "image_regen", "frame_number": 4, "shot": 2, "prompt": "p"},
            {"type": "unknown_op", "frame_number": 5, "shot": 1},
            {"type": "coverage_plan", "frame_number": 0, "shot": 1, "plan": "ОБЩИЙ"},
        ]
    )
    by_type = {op["type"]: op for op in cleaned}
    assert by_type["coverage_angle"]["angle"] == "с плеча"
    assert by_type["coverage_move"]["move"] == "наезд"
    assert by_type["coverage_stitch"]["stitch"] == "cut_on_action"
    assert by_type["coverage_light"]["light"] == "контровой"
    assert by_type["coverage_set"]["set"] == "кабинет"
    assert by_type["coverage_plan"]["plan"] == "ДАЛЬНИЙ"
    assert by_type["coverage_anchors"]["anchors"][0]["главный"] is True
    assert by_type["coverage_kind"]["parent_number"] == 1
    assert by_type["image_regen"]["shot"] == 2
    # Неизвестный тип и кадр < 1 отбрасываются.
    assert "unknown_op" not in by_type
    assert len(cleaned) == 10


def test_refuse_empty_queue_overwrite() -> None:
    existing = [{"type": "video_regen", "frame_number": 1, "shot": 1}]
    ok, reason = should_accept_queue_save(
        cleaned=[], existing=existing, apply_running=False, force_clear=False
    )
    assert ok is False
    assert reason == "refuse_empty_overwrite"
    ok2, _ = should_accept_queue_save(
        cleaned=[], existing=existing, apply_running=False, force_clear=True
    )
    assert ok2 is True


def test_refuse_shorter_queue_while_apply_running() -> None:
    existing = [
        {"type": "video_regen", "frame_number": 1, "shot": 1},
        {"type": "video_regen", "frame_number": 2, "shot": 1},
    ]
    cleaned = [{"type": "video_regen", "frame_number": 2, "shot": 1}]
    ok, reason = should_accept_queue_save(
        cleaned=cleaned, existing=existing, apply_running=True, force_clear=False
    )
    assert ok is False
    assert reason == "apply_running"
    # Без apply — ужать можно (клиент сохранил remaining после ручного снятия).
    ok2, _ = should_accept_queue_save(
        cleaned=cleaned, existing=existing, apply_running=False, force_clear=False
    )
    assert ok2 is True
