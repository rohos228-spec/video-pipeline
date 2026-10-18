"""Утвердительные строки в маркерах скрытия не пускают шот в генерацию."""

from __future__ import annotations

import pytest

from app.orchestrator.validators.activity_flags import (
    HIDE_MARKS,
    frame_is_active,
    shot_is_active,
)

_HIDE_TRUE_VALUES = (
    "да",
    "Да",
    "true",
    "True",
    "1",
    1,
    True,
    "yes",
    "on",
    "истина",
    " да ",
)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize("mark", HIDE_MARKS)
@pytest.mark.parametrize("value", _HIDE_TRUE_VALUES, ids=lambda value: repr(value))
def test_hide_mark_affirmative_is_inactive(kind: str, mark: str, value: object) -> None:
    """«да»/«true»/«1» в удалён и карантин не отправляют шот в генерацию."""

    payload = {mark: value}
    active = shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
    assert active is False
