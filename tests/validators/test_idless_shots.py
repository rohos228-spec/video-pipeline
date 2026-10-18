"""Кадры без id сравниваются по уникальному тексту, не по числу."""

from __future__ import annotations

import pytest

from app.orchestrator.validators.no_deletions import check_frames
from app.orchestrator.validators.preservation import run_preservation_validators


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


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_soft_delete_idless_via_active_false_passes(mode: str) -> None:
    """Шот без id с ``активен: False`` остаётся в списке — это не ``frame_deleted``."""

    before = _scene([{"закадр": "x"}, {"закадр": "y"}])
    after = _scene([{"закадр": "x", "активен": False}, {"закадр": "y"}])
    assert run_preservation_validators(before, after, mode=mode) == []


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_soft_delete_idless_via_quarantine_passes(mode: str) -> None:
    """Тот же мягкий уход через ``карантин``: шот на месте, нарушений нет."""

    before = _scene([{"закадр": "x"}, {"закадр": "y"}])
    after = _scene([{"закадр": "x", "карантин": True}, {"закадр": "y"}])
    assert run_preservation_validators(before, after, mode=mode) == []


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_only_remaining_shot_has_id_passes(mode: str) -> None:
    """После мягкого удаления без id единственный живой шот кадра имеет id."""

    before = _scene([{"закадр": "x"}, {"id": "k", "закадр": "y"}])
    after = _scene(
        [
            {"закадр": "x", "активен": False},
            {"id": "k", "закадр": "y"},
        ]
    )
    assert run_preservation_validators(before, after, mode=mode) == []
