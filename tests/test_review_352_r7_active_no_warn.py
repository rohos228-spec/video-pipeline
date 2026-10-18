"""Ревью #352: «0.0» и «да» у активен не пишут WARN."""

from __future__ import annotations

import logging

import pytest

from app.orchestrator.validators import activity_flags
from app.orchestrator.validators.activity_flags import (
    ACTIVE_MARK,
    frame_is_active,
    shot_is_active,
)


@pytest.mark.parametrize("kind", ("shot", "frame"))
@pytest.mark.parametrize(
    ("value", "expected"),
    [("0.0", False), ("да", True)],
)
def test_active_mark_zero_and_da_do_not_warn(
    kind: str,
    value: str,
    expected: bool,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """активен=\"0.0\" гасит кадр, активен=\"да\" оставляет живым. WARN нет."""

    monkeypatch.setattr(activity_flags, "_WARNED_VALUES", set())
    payload = {ACTIVE_MARK: value}
    with caplog.at_level(logging.WARNING, logger=activity_flags.logger.name):
        active = (
            shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
        )
    assert active is expected
    assert caplog.records == []
