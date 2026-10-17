"""Персонаж может стать archived, но не может исчезнуть."""

from __future__ import annotations

import copy

from app.orchestrator.validators.no_deletions import check_characters


def _cast(people: list[dict]) -> dict:
    return {"персонажи": people, "сцены": [{"id": "s1", "номер": 1}]}


def test_same_cast_and_archive_flag_pass() -> None:
    before = _cast([{"id": "c01", "имя": "Анна"}, {"id": "c02", "имя": "Борис"}])
    after = _cast(
        [
            {"id": "c01", "имя": "Анна"},
            {"id": "c02", "имя": "Борис", "archived": True, "inactive": True},
        ]
    )
    frozen = copy.deepcopy((before, after))
    assert check_characters(before, after, ops=[]) == []
    assert (before, after) == frozen

    moved = {
        "персонажи": [{"id": "c01", "имя": "Анна"}],
        "архив": {"персонажи": [{"id": "c02", "имя": "Борис", "archived": True}]},
    }
    assert check_characters(before, moved) == []


def test_missing_character_and_delete_op() -> None:
    before = _cast([{"id": "c01", "имя": "Анна"}, {"id": "c02", "имя": "Борис"}])
    gone = check_characters(before, _cast([{"id": "c01", "имя": "Анна"}]))
    assert [(item.code, item.severity, item.id, item.path) for item in gone] == [
        ("character_deleted", "error", "c02", "characters/c02"),
    ]

    kept = _cast([{"id": "c01"}, {"id": "c02"}])
    ops = [
        {"op": "archive", "entity": "character", "id": "c02"},
        {"op": "delete", "entity": "character", "id": "c01"},
        {"action": "delete_character", "character_id": "c02"},
        {"op": "delete", "entity": "scene", "id": "s1"},
    ]
    deleted = check_characters(kept, copy.deepcopy(kept), ops=ops)
    assert [(item.id, item.path) for item in deleted] == [
        ("c01", "ops/1"),
        ("c02", "ops/2"),
    ]
    assert all(item.code == "character_deleted" and item.severity == "error" for item in deleted)


def test_inactive_shot_does_not_drop_a_character() -> None:
    before = {
        "персонажи": [{"id": "c01", "имя": "Анна"}],
        "сцены": [
            {
                "id": "s1",
                "номер": 1,
                "кадры": [{"id": "k-off", "активен": False, "закадр": ""}],
            }
        ],
    }
    after = copy.deepcopy(before)
    assert check_characters(before, after, ops=[{"op": "update", "entity": "shot", "id": "k-off"}]) == []
