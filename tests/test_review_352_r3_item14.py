"""Ревью #352 п.14: невыбранные правки остаются в очереди при снимке."""

from app.services.montage_board_apply import merge_kept_ops


def test_held_ops_survive_snapshot() -> None:
    pending = [{"type": "coverage_plan", "frame_number": 1}]
    held = [{"type": "coverage_action", "frame_number": 4, "text": "не выбран"}]
    merged = merge_kept_ops(pending, held)
    assert merged[0]["frame_number"] == 1
    assert merged[1]["text"] == "не выбран"
    again = merge_kept_ops(merged, held)
    assert len(again) == 2
