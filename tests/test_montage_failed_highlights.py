"""failed_highlights / slot keys for montage board UI."""

from __future__ import annotations

from app.services.montage_board_meta import (
    add_failed_highlight,
    clear_failed_highlight,
    clear_failed_highlights,
    failed_highlights_for_public,
    public_board_meta,
    slot_key_from_op,
)


def test_slot_key_from_op_image_and_video() -> None:
    assert slot_key_from_op({"type": "image_regen", "frame_number": 88, "shot": 1}) == "88:image1"
    assert slot_key_from_op({"type": "image_ai_change", "frame_number": 12, "shot": 2}) == "12:image2"
    assert slot_key_from_op({"type": "video_regen", "frame_number": 105, "shot": 1}) == "105:1"
    assert slot_key_from_op({"type": "video_ai_change", "frame_number": 40, "shot": 2}) == "40:2"
    assert slot_key_from_op({"type": "unknown", "frame_number": 1, "shot": 1}) is None


def test_failed_highlights_fallback_from_apply_job_results() -> None:
    board = {
        "highlights": ["105:image1"],
        "apply_job": {
            "status": "error",
            "results": [
                {"ok": True, "highlight": "105:image1"},
                {
                    "ok": False,
                    "error": "CONTENT_POLICY",
                    "op": {"type": "image_regen", "frame_number": 118, "shot": 1},
                },
                {
                    "ok": False,
                    "error": "kling 401",
                    "op": {"type": "video_ai_change", "frame_number": 32, "shot": 1},
                },
            ],
        },
    }
    assert failed_highlights_for_public(board) == ["118:image1", "32:1"]
    pub = public_board_meta(board)
    assert pub["failed_highlights"] == ["118:image1", "32:1"]
    assert pub["highlights"] == ["105:image1"]


def test_explicit_empty_failed_highlights_skips_stale_results() -> None:
    board = {
        "failed_highlights": [],
        "apply_job": {
            "results": [
                {
                    "ok": False,
                    "op": {"type": "image_regen", "frame_number": 1, "shot": 1},
                }
            ]
        },
    }
    assert failed_highlights_for_public(board) == []


def test_add_clear_failed_highlight() -> None:
    board: dict = {}
    add_failed_highlight(board, "88:image1")
    add_failed_highlight(board, "88:image1")
    assert board["failed_highlights"] == ["88:image1"]
    clear_failed_highlight(board, "88:image1")
    assert board["failed_highlights"] == []
    add_failed_highlight(board, "32:1")
    clear_failed_highlights(board)
    assert board["failed_highlights"] == []
