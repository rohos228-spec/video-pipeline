"""Сцена не исчезает: id на месте. Номер — только если id нет."""

from __future__ import annotations

import copy

from app.orchestrator.validators.no_deletions import check_scenes


def _project(scenes: list[dict]) -> dict:
    return {"сцены": scenes}


def test_same_scenes_pass() -> None:
    before = _project([{"id": "s1", "номер": 1}, {"id": "s2", "номер": 2}])
    after = _project([{"id": "s1", "номер": 1}, {"id": "s2", "номер": 2}])
    frozen = copy.deepcopy((before, after))
    assert check_scenes(before, after, ops=[]) == []
    assert (before, after) == frozen


def test_missing_scene_id_or_number_is_scene_deleted() -> None:
    before = _project([{"id": "s1", "номер": 1}, {"id": "s2", "номер": 2}])
    lost_id = check_scenes(before, _project([{"id": "s1", "номер": 1}]), ops=None)
    assert [(v.code, v.severity, v.id) for v in lost_id] == [
        ("scene_deleted", "error", "s2"),
    ]
    assert lost_id[0].path == "scenes/s2"

    renumbered = check_scenes(
        before,
        _project([{"id": "s1", "номер": 9}, {"id": "s2", "номер": 2}]),
        ops=None,
    )
    assert renumbered == []

    replaced = check_scenes(
        before,
        _project([{"id": "s1", "номер": 1}, {"id": "s9", "номер": 2}]),
        ops=None,
    )
    assert [v.id for v in replaced] == ["s2"]


def test_renumber_same_id_is_not_scene_deleted() -> None:
    before = _project([{"id": "s1", "номер": 3, "кадры": [{"id": "k1"}]}])
    after = _project([{"id": "s1", "номер": 100003, "кадры": [{"id": "k1"}]}])
    assert check_scenes(before, after) == []

    nameless = _project([{"номер": 3}])
    assert [item.id for item in check_scenes(nameless, _project([{"номер": 4}]))] == ["3"]


def test_archive_and_inactive_scene_remain() -> None:
    before = _project([{"id": "s1", "номер": 1}, {"id": "s2", "номер": 2}])
    after = {
        "сцены": [{"id": "s2", "номер": 2, "активен": False}],
        "архив": {"сцены": [{"id": "s1", "номер": 1, "archived": True}]},
    }
    assert check_scenes(before, after) == []

    inactive = _project([{"id": "s1", "номер": 1, "активен": False, "archived": True}])
    assert check_scenes(inactive, copy.deepcopy(inactive)) == []


def test_inactive_shot_does_not_delete_the_scene() -> None:
    scene = {
        "id": "s1",
        "номер": 1,
        "кадры": [
            {"id": "k1", "закадр": "жив"},
            {"id": "k-off", "активен": False, "закадр": ""},
        ],
    }
    before = _project([scene])
    after = _project([copy.deepcopy(scene)])
    assert check_scenes(before, after) == []

    gone = check_scenes(before, _project([]))
    assert [v.code for v in gone] == ["scene_deleted"]
    assert gone[0].id == "s1"


def test_ops_set_keeps_scene_payloads() -> None:
    before = [{"op": "upsert", "scene": {"id": "s1", "номер": 4}}]
    after = [{"op": "upsert", "scene": {"id": "s1", "номер": 4}}]
    assert check_scenes(before, after) == []
    missing = check_scenes(before, [])
    assert [v.id for v in missing] == ["s1"]
    assert missing[0].code == "scene_deleted"
