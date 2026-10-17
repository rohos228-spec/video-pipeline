"""Адаптер no_deletions_guard: пропавшие id возвращаются, мягкая пометка — нет."""

from __future__ import annotations

import copy

import pytest
from loguru import logger

from app.pipeline.no_deletions_guard import guard


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


def _guard(before, after, **kwargs):
    kwargs.setdefault("project_id", 13)
    kwargs.setdefault("source", "unit")
    return guard(before, after, **kwargs)


def test_clean_path_returns_the_same_after(warn_log) -> None:
    after = {"кадры": [{"id": "a"}, {"id": "b", "активен": False}]}
    result = _guard({"кадры": [{"id": "a"}, {"id": "b"}]}, after)
    assert result["ok"] is True
    assert result["mode"] == "warn_mark"
    assert result["missing_ids"] == []
    assert result["after"] is after
    assert warn_log == []


def test_inactive_kept_remark_reorder_restore_and_tail_do_not_trip(warn_log) -> None:
    base = [{"id": "a", "закадр": "живой"}, {"id": "b", "закадр": "тихий", "активен": False}]
    cases = [
        ("kept", base, list(base)),
        ("remark", base, [{"id": "a", "закадр": "живой"}, {"id": "b", "закадр": "тихий", "активен": False, "пометка": "ещё"}]),
        ("reorder", base, [base[1], base[0]]),
        ("restore", [{"id": "b", "активен": False}], [{"id": "b", "активен": True}]),
        ("tail", [{"id": "b", "активен": False}, {"id": "a"}], [{"id": "a"}, {"id": "b", "активен": False}]),
        ("flags", [{"id": "b"}], [{"id": "b", "удалён": True, "карантин": True}]),
    ]
    for name, before_rows, after_rows in cases:
        after = {"кадры": after_rows}
        result = _guard({"кадры": before_rows}, after)
        assert result["ok"] is True, name
        assert result["after"] is after, name
    assert warn_log == []
    restored = _guard(
        {"кадры": [{"id": "b", "активен": False}]},
        {"кадры": [{"id": "b", "активен": True}]},
    )
    assert restored["after"]["кадры"][0]["активен"] is True


def test_text_kind_change_and_parked_frame_number_keep_identity(warn_log) -> None:
    texts = _guard(
        {"frame_texts": [{"id": "t1", "text": "привет", "kind": "voiceover"}]},
        {"frame_texts": [{"id": "t1", "text": "привет", "kind": "vo_archived"}]},
    )
    assert texts["ok"] is True
    hashed = _guard(
        {"frame_texts": [{"text": "привет", "kind": "voiceover"}]},
        {"frame_texts": [{"text": "привет", "kind": "vo_archived"}]},
    )
    assert hashed["ok"] is True
    after = {"frames": [{"id": 5, "uuid": "u", "number": 900000, "attrs": {"кадры": [{"id": "a"}]}}]}
    parked = _guard(
        {"frames": [{"id": 5, "uuid": "u", "number": 2, "attrs": {"кадры": [{"id": "a"}]}}]},
        after,
    )
    assert parked["ok"] is True
    assert parked["after"] is after
    assert warn_log == []


def test_idless_shot_keeps_scene_and_number_when_text_changes(warn_log) -> None:
    after = {"кадры": [{"сцена": 1, "номер": 2, "закадр": "стало"}]}
    result = _guard({"кадры": [{"сцена": 1, "номер": 2, "закадр": "было"}]}, after)
    assert result["ok"] is True
    assert result["after"] is after
    assert warn_log == []


def test_moved_shot_is_not_restored_twice(warn_log) -> None:
    before = {
        "frames": [
            {"id": 1, "uuid": "u1", "number": 1, "attrs": {"кадры": [{"id": "a"}, {"id": "b"}]}},
            {"id": 2, "uuid": "u2", "number": 2, "attrs": {"кадры": [{"id": "c"}]}},
        ]
    }
    after = {
        "frames": [
            {"id": 1, "uuid": "u1", "number": 1, "attrs": {"кадры": [{"id": "a"}]}},
            {"id": 2, "uuid": "u2", "number": 2, "attrs": {"кадры": [{"id": "c"}, {"id": "b"}]}},
        ]
    }
    result = _guard(before, after)
    assert result["ok"] is True
    assert result["after"] is after
    assert [row["id"] for row in after["frames"][1]["attrs"]["кадры"]] == ["c", "b"]
    assert warn_log == []


def test_warn_mark_puts_missing_shot_back(warn_log) -> None:
    after = {"кадры": [{"id": "a", "закадр": "один"}]}
    result = _guard(
        {"кадры": [{"id": "a", "закадр": "один"}, {"id": "b", "закадр": "два"}]},
        after,
    )
    assert result["ok"] is False
    assert result["mode"] == "warn_mark"
    assert result["missing_ids"] == ["shot:b"]
    assert result["after"] is after
    assert [row["id"] for row in after["кадры"]] == ["a", "b"]
    assert after["кадры"][1]["активен"] is False
    assert any("no_deletions_guard" in line and "shot:b" in line for line in warn_log)


def test_nested_lists_keep_dropped_shot_text_edge_and_character(warn_log) -> None:
    before = {
        "frames": [
            {
                "id": 1,
                "uuid": "u1",
                "number": 1,
                "attrs": {"кадры": [{"id": "a"}, {"id": "b"}]},
                "frame_texts": [{"id": "t1", "text": "реплика", "kind": "voiceover"}],
                "frame_edges": [{"id": "e1", "from": "a", "to": "b", "type": "next"}],
                "characters": [{"id": "c1", "name": "Анна"}, {"id": "c2", "name": "Борис"}],
            }
        ]
    }
    after = {
        "frames": [
            {
                "id": 1,
                "uuid": "u1",
                "number": 1,
                "attrs": {"кадры": [{"id": "a"}]},
                "frame_texts": [],
                "frame_edges": [],
                "characters": [{"id": "c1", "name": "Анна"}],
            }
        ]
    }
    result = _guard(before, after)
    assert result["ok"] is False
    frame = result["after"]["frames"][0]
    assert [row["id"] for row in frame["attrs"]["кадры"]] == ["a", "b"]
    assert frame["attrs"]["кадры"][1]["активен"] is False
    assert frame["frame_texts"][0]["id"] == "t1"
    assert frame["frame_texts"][0]["активен"] is False
    assert frame["frame_edges"][0]["id"] == "e1"
    assert frame["characters"][1]["id"] == "c2"
    assert frame["characters"][1]["активен"] is False
    assert any("no_deletions_guard" in line for line in warn_log)


def test_soft_delete_of_nested_rows_passes(warn_log) -> None:
    after = {
        "frames": [
            {
                "id": 1,
                "uuid": "u1",
                "number": 1,
                "attrs": {"кадры": [{"id": "a"}, {"id": "b", "активен": False}]},
                "frame_texts": [{"id": "t1", "text": "реплика", "kind": "vo_archived", "активен": False}],
                "characters": [{"id": "c1", "name": "Анна", "активен": False}],
            }
        ]
    }
    before = copy.deepcopy(after)
    before["frames"][0]["attrs"]["кадры"][1]["активен"] = True
    before["frames"][0]["frame_texts"][0]["активен"] = True
    before["frames"][0]["frame_texts"][0]["kind"] = "voiceover"
    before["frames"][0]["characters"][0]["активен"] = True
    result = _guard(before, after)
    assert result["ok"] is True
    assert result["after"] is after
    assert warn_log == []


def test_refuse_does_not_write_or_mutate_after(warn_log) -> None:
    before = {"кадры": [{"id": "a"}, {"id": "b"}]}
    after = {"кадры": [{"id": "a"}]}
    snapshot = copy.deepcopy(after)
    result = _guard(before, after, mode="refuse")
    assert result["ok"] is False
    assert result["mode"] == "refuse"
    assert result["after"] is before
    assert after == snapshot
    assert any("no_deletions_guard" in line and "refuse" in line for line in warn_log)


def test_non_list_kadry_is_invalid_shape_and_shots_return(warn_log) -> None:
    before = {"кадры": [{"id": "a", "закадр": "текст"}]}
    after = {"кадры": {}}
    result = _guard(before, after)
    assert result["ok"] is False
    assert "invalid_shape" in result["reason"]
    assert result["missing_ids"] == ["shot:a"]
    assert [row["id"] for row in after["кадры"]] == ["a"]
    assert after["кадры"][0]["активен"] is False
    assert after["кадры"][0]["закадр"] == "текст"
    assert before["кадры"][0].get("активен") is not False
    assert any("invalid_shape" in line for line in warn_log)


def test_bare_shot_list_warn_mark_and_soft_delete() -> None:
    dropped = [{"id": "a"}]
    result = _guard([{"id": "a"}, {"id": "b", "сцена": 1, "номер": 1}], dropped)
    assert [row["id"] for row in result["after"]] == ["a", "b"]
    assert dropped[1]["активен"] is False
    kept = [{"id": "a"}, {"id": "b", "активен": False}]
    soft = _guard([{"id": "a"}, {"id": "b"}], kept)
    assert soft["ok"] is True
    assert soft["after"] is kept
