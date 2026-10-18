"""Ревью #352, решение Heil: blob 56a6e8cf.

«активен» = «0.0» и «да» — неизвестные строки: запись живая, один WARN.
"""

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
@pytest.mark.parametrize("value", ("0.0", "да"))
def test_active_mark_zero_and_da_do_not_warn(
    kind: str,
    value: str,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """активен=\"0.0\" и активен=\"да\" оставляют запись живой и пишут один WARN."""

    monkeypatch.setattr(activity_flags, "_WARNED_VALUES", set())
    payload = {ACTIVE_MARK: value}
    with caplog.at_level(logging.WARNING, logger=activity_flags.logger.name):
        active = (
            shot_is_active(payload) if kind == "shot" else frame_is_active(payload)
        )
    assert active is True
    assert len(caplog.records) == 1
    assert "активен" in caplog.records[0].getMessage()
