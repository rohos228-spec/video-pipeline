from __future__ import annotations

import pytest

from app.services.action_layer import builtin_wave5
from app.services.action_layer.spec import JobState


async def _poll_with(monkeypatch, final_status: str | None, done: bool = True) -> JobState:
    async def fake(ctx, params, started):
        return JobState(done=done, ok=True, result={"final_status": final_status} if final_status else None)

    monkeypatch.setattr(builtin_wave5, "_poll_project_idle", fake)
    return await builtin_wave5._poll_step(None, {}, {})


@pytest.mark.parametrize("final_status,expected_ok", [
    ("failed", False), ("paused", False), ("images_ready", True), ("idle", True),
])
async def test_step_plan_succeeds_only_when_the_project_did_not_fail_or_pause(monkeypatch, final_status, expected_ok):
    state = await _poll_with(monkeypatch, final_status)
    assert state.done and state.ok is expected_ok
    if not expected_ok:
        assert final_status in (state.error or "")


async def test_unfinished_poll_is_passed_through(monkeypatch):
    state = await _poll_with(monkeypatch, None, done=False)
    assert not state.done and state.ok
