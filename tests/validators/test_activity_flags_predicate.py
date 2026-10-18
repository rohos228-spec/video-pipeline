"""Метки активности читает только activity_flags, не ``активен is False``."""

from __future__ import annotations

import pytest

from app.orchestrator.validators.preservation import (
    PreservationRejected,
    run_preservation_validators,
)

_HIDE_MARKS = (
    ("карантин", "да"),
    ("карантин", True),
    ("удалён", "да"),
    ("удалён", True),
    ("активен", "0"),
    ("активен", "нет"),
    ("активен", 0),
)


def _project(frames: list[dict]) -> dict:
    return {"сцены": [{"id": "A", "номер": 1, "кадры": frames}]}


def _marked(mark: str, value: object) -> dict:
    return {"id": "k1", "закадр": "тихо", mark: value}


@pytest.mark.parametrize("action", ["revive", "copy"])
@pytest.mark.parametrize("mode", ["reject", "soft"])
@pytest.mark.parametrize(("mark", "value"), _HIDE_MARKS)
def test_hide_mark_revive_or_copy_is_revived(action: str, mode: str, mark: str, value: object) -> None:
    """Снятие метки или копия в другой кадр как активный — inactive_shot_revived."""

    before = _project([_marked(mark, value)])
    if action == "revive":
        after = _project([{"id": "k1", "закадр": "тихо"}])
    else:
        after = {
            "сцены": [
                {"id": "A", "номер": 1, "кадры": [_marked(mark, value)]},
                {
                    "id": "B",
                    "номер": 2,
                    "кадры": [{"id": "k1", "активен": True, "закадр": "тихо"}],
                },
            ]
        }
    with pytest.raises(PreservationRejected) as raised:
        run_preservation_validators(before, after, mode=mode)
    assert any(
        item.code == "inactive_shot_revived" and item.id == "k1"
        for item in raised.value.violations
    )


@pytest.mark.parametrize("mode", ["reject", "soft"])
@pytest.mark.parametrize("value", ["нет", "0.0", "ложь"])
def test_deleted_false_token_shot_stays_alive(mode: str, value: str) -> None:
    """``удалён`` со значением «нет» / «0.0» / «ложь» — шот живой, ложного отказа нет."""

    marked = _project([{"id": "k1", "закадр": "живой", "удалён": value}])
    assert run_preservation_validators(marked, marked, mode=mode) == []
    cleared = _project([{"id": "k1", "закадр": "живой"}])
    assert run_preservation_validators(marked, cleared, mode=mode) == []
