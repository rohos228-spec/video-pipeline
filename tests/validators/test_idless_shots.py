"""Кадры без id сравниваются по уникальному тексту, не по числу."""

from __future__ import annotations

from app.orchestrator.validators.no_deletions import check_frames


def _scene(frames: list[dict]) -> dict:
    return {"сцены": [{"id": "s1", "номер": 1, "кадры": frames}]}


def test_no_deletions_idless_shots_compared_by_content() -> None:
    before = _scene([{"закадр": "первый"}, {"закадр": "второй"}])
    same_count_other_text = _scene([{"закадр": "первый"}, {"закадр": "третий"}])
    lost = check_frames(before, same_count_other_text)
    assert any(
        item.code == "frame_deleted" and item.severity == "error" and item.id == "второй"
        for item in lost
    )

    reordered = _scene([{"закадр": "второй"}, {"закадр": "первый"}])
    assert check_frames(before, reordered) == []

    duplicates = _scene([{"закадр": "первый"}, {"закадр": "первый"}])
    one_left = _scene([{"закадр": "первый"}])
    assert check_frames(duplicates, one_left) == []

    inactive = _scene(
        [
            {"закадр": "хвост", "активен": False},
            {"закадр": "живой"},
        ]
    )
    assert check_frames(inactive, _scene([{"закадр": "живой"}])) == []
