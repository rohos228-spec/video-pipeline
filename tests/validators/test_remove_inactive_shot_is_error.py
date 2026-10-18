"""Физическое удаление уже помеченного шота — ошибка, перенос с пометкой — нет."""

from __future__ import annotations

import pytest

from app.orchestrator.validators.preservation import (
    PreservationRejected,
    run_preservation_validators,
)


def _scene(shots: list[dict]) -> dict:
    return {"сцены": [{"id": "s1", "номер": 1, "кадры": shots}]}


def _assert_removed(before: dict, after: dict, mode: str, *ids: str) -> None:
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode=mode)
    found = {
        item.id
        for item in raised.value.violations
        if item.code == "marked_shot_removed" and item.severity == "error"
    }
    assert set(ids) <= found


@pytest.mark.parametrize("mode", ["reject", "soft"])
@pytest.mark.parametrize("case", ["id_inactive", "deleted", "nameless", "all_inactive_empty"])
def test_remove_inactive_shot_is_error(mode: str, case: str) -> None:
    """Помеченный шот исчез из «после» целиком — marked_shot_removed."""

    live = {"id": "live", "закадр": "живой"}
    if case == "id_inactive":
        before = _scene([{"id": "k", "активен": False, "закадр": "тихо"}, live])
        after = _scene([live])
        _assert_removed(before, after, mode, "k")
        return
    if case == "deleted":
        for value in (True, "да"):
            before = _scene([{"id": "k", "удалён": value, "закадр": "тихо"}, live])
            after = _scene([dict(live)])
            _assert_removed(before, after, mode, "k")
        return
    if case == "nameless":
        before = _scene([{"закадр": "тихо", "активен": False}, {"закадр": "живой"}])
        after = _scene([{"закадр": "живой"}])
        _assert_removed(before, after, mode, "тихо")
        return
    before = _scene(
        [
            {"id": "k1", "активен": False, "закадр": "а"},
            {"id": "k2", "активен": False, "закадр": "б"},
        ]
    )
    after = _scene([])
    _assert_removed(before, after, mode, "k1", "k2")


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_move_marked_shot_keeps_mark_passes(mode: str) -> None:
    """Перенос в другой кадр с той же пометкой и шот на месте — не ошибка."""

    marked = {"id": "k", "активен": False, "закадр": "тихо"}
    before = {
        "сцены": [
            {"id": "A", "номер": 1, "кадры": [dict(marked), {"id": "a", "закадр": "y"}]},
            {"id": "B", "номер": 2, "кадры": [{"id": "b", "закадр": "z"}]},
        ]
    }
    moved = {
        "сцены": [
            {"id": "A", "номер": 1, "кадры": [{"id": "a", "закадр": "y"}]},
            {"id": "B", "номер": 2, "кадры": [{"id": "b", "закадр": "z"}, dict(marked)]},
        ]
    }
    assert run_preservation_validators(before, moved, mode=mode) == []
    stayed = {
        "сцены": [
            {"id": "A", "номер": 1, "кадры": [dict(marked), {"id": "a", "закадр": "y"}]},
            {"id": "B", "номер": 2, "кадры": [{"id": "b", "закадр": "z"}]},
        ]
    }
    assert run_preservation_validators(before, stayed, mode=mode) == []


@pytest.mark.parametrize("mode", ["reject", "soft"])
@pytest.mark.parametrize(
    ("flag", "value"),
    [("активен", False), ("карантин", "да"), ("удалён", "да")],
)
def test_edit_text_of_marked_idless_shot_passes(mode: str, flag: str, value: object) -> None:
    """Текст помеченного шота без id на том же месте — не удаление.

    Метка остаётся. Соседний живой шот не двигается.
    """

    before = _scene(
        [
            {flag: value, "закадр": "тихо"},
            {"id": "live", "закадр": "живой"},
        ]
    )
    after = _scene(
        [
            {flag: value, "закадр": "громко"},
            {"id": "live", "закадр": "живой"},
        ]
    )
    assert run_preservation_validators(before, after, mode=mode) == []


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_drop_first_of_two_marked_idless_shots_is_error(mode: str) -> None:
    """Удаление первого из двух помеченных безымянных шотов со сдвигом — потеря.

    Второй шот встаёт на индекс первого, но помеченных стало меньше.
    """

    before = _scene(
        [
            {"активен": False, "закадр": "x"},
            {"активен": False, "закадр": "y"},
        ]
    )
    after = _scene([{"активен": False, "закадр": "y"}])
    _assert_removed(before, after, mode, "x")


@pytest.mark.parametrize("mode", ["reject", "soft"])
def test_drop_marked_while_marking_neighbor_is_error(mode: str) -> None:
    """Удалить помеченный x и пометить соседа z — потеря x, а не правка на месте."""

    before = _scene(
        [
            {"активен": False, "закадр": "x"},
            {"активен": False, "закадр": "y"},
            {"закадр": "z"},
        ]
    )
    after = _scene(
        [
            {"активен": False, "закадр": "y"},
            {"активен": False, "закадр": "z"},
        ]
    )
    _assert_removed(before, after, mode, "x")
