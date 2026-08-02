"""После split нельзя откатывать frames_ready→new (ломает auto_advance)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Project, ProjectStatus
from app.services.project_state import compute_actual_status, recompute_status
from app.services.step_data_guard import ready_status_confirmed_by_data


def _session_with_frame_count(n: int) -> AsyncMock:
    session = AsyncMock()

    async def mock_execute(stmt):
        m = MagicMock()
        m.scalar_one.return_value = n
        m.scalars.return_value.all.return_value = []
        m.scalar_one_or_none.return_value = None
        return m

    session.execute = mock_execute
    session.refresh = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_compute_actual_script_and_frames_without_plan_not_new() -> None:
    p = Project(
        id=1,
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        general_plan="",
        script_text="x" * 100,
        meta={"split_completed": True},
    )
    st = await compute_actual_status(_session_with_frame_count(8), p)
    assert st is not ProjectStatus.new


@pytest.mark.asyncio
async def test_ready_confirmed_frames_ready_keeps_current_status() -> None:
    p = Project(
        id=1,
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        general_plan="",
        script_text="script",
        meta={},  # split_completed мог пропасть — статус всё равно frames_ready
    )
    ok = await ready_status_confirmed_by_data(
        _session_with_frame_count(3), p, ProjectStatus.frames_ready
    )
    assert ok is True


@pytest.mark.asyncio
async def test_clamp_never_downgrades_frames_ready_to_new(monkeypatch) -> None:
    """auto_advance зовёт clamp — он тоже не имеет права frames_ready→new."""
    from app.services.step_data_guard import clamp_status_to_data

    p = Project(
        id=50,
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        general_plan="",
        script_text="script text",
        meta={"split_completed": True},
        auto_mode=True,
    )

    async def _fake_compute(session, project):
        return ProjectStatus.new

    monkeypatch.setattr(
        "app.services.step_data_guard.compute_actual_status", _fake_compute
    )
    monkeypatch.setattr(
        "app.services.project_state.compute_actual_status", _fake_compute
    )
    result = await clamp_status_to_data(_session_with_frame_count(8), p)
    assert result is None
    assert p.status is ProjectStatus.frames_ready


@pytest.mark.asyncio
async def test_recompute_never_downgrades_frames_ready_to_new(monkeypatch) -> None:
    """Железо: web_get/list не имеют права сносить frames_ready в new."""
    p = Project(
        id=50,
        topic="t",
        slug="t",
        status=ProjectStatus.frames_ready,
        general_plan="",  # пустой план — старый триггер бага
        script_text="script text",
        meta={"split_completed": True},
        auto_mode=True,
    )

    async def _fake_compute(session, project):
        return ProjectStatus.new  # как ломало months: compute врёт «new»

    monkeypatch.setattr(
        "app.services.project_state.compute_actual_status", _fake_compute
    )
    old, new, changed = await recompute_status(
        _session_with_frame_count(8), p, log_prefix="recompute(web_get)"
    )
    assert old is ProjectStatus.frames_ready
    assert new is ProjectStatus.frames_ready
    assert changed is False
    assert p.status is ProjectStatus.frames_ready
