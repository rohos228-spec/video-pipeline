"""Кадры сцены не пропадают. Неактивный и архивный кадр — не удаление."""

from __future__ import annotations

import copy

from app.orchestrator.validators.no_deletions import check_frames


def _project(frames: list[dict] | dict | str | None) -> dict:
    return {"сцены": [{"id": "s1", "номер": 1, "кадры": frames}]}


def _ids(violations: list) -> list[tuple[str, str, str]]:
    return [(item.code, item.severity, item.id) for item in violations]


def test_same_frames_pass() -> None:
    frames = [{"id": "k1", "закадр": "а"}, {"id": "k2", "закадр": "б"}]
    before = _project(frames)
    after = _project(copy.deepcopy(frames))
    frozen = copy.deepcopy((before, after))
    assert check_frames(before, after) == []
    assert (before, after) == frozen


def test_missing_active_frame_is_frame_deleted() -> None:
    before = _project([{"id": "k1"}, {"id": "k2"}, {"id": "k3"}])
    after = _project([{"id": "k1"}, {"id": "k3"}])
    violations = check_frames(before, after)
    assert _ids(violations) == [("frame_deleted", "error", "k2")]
    assert violations[0].path == "scenes/s1/кадры/k2"


def test_inactive_or_archived_frame_may_leave() -> None:
    before = _project(
        [
            {"id": "k1", "закадр": "жив"},
            {"id": "k-off", "активен": False, "закадр": "тихо"},
            {"id": "k-box", "archived": True},
        ]
    )
    after = _project([{"id": "k1", "закадр": "жив"}])
    assert check_frames(before, after) == []

    parked = _project([{"id": "k1"}, {"id": "k-off", "активен": False}])
    assert check_frames(parked, copy.deepcopy(parked)) == []

    moved = {
        "сцены": [{"id": "s1", "номер": 1, "кадры": [{"id": "k1"}]}],
        "архив": {"кадры": [{"id": "k2"}]},
    }
    assert check_frames(_project([{"id": "k1"}, {"id": "k2"}]), moved) == []


def test_non_list_kadry_is_warn_not_mass_deletion() -> None:
    before = _project([{"id": "k1"}, {"id": "k2"}, {"id": "k-off", "активен": False}])
    for bad in ({"k1": "x"}, "не список", None):
        violations = check_frames(before, _project(bad))
        assert [item.code for item in violations] == ["frames_no_data"]
        assert violations[0].severity == "warn"
        assert violations[0].id == "s1"
        assert violations[0].path == "scenes/s1/кадры"
        assert all(item.code != "frame_deleted" for item in violations)
