"""Гейты plan_ready → scripting по реальным данным."""

from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.services.step_data_guard import can_enter_running, ready_status_confirmed_by_data


@pytest.mark.asyncio
async def test_scripting_requires_meaningful_plan() -> None:
    p = Project(topic="t", slug="t", general_plan="короткий шаблон")
    ok, reason, fix = await can_enter_running(None, p, ProjectStatus.scripting)  # type: ignore[arg-type]
    assert not ok
    assert "сценарий" in reason
    assert fix == ProjectStatus.new


@pytest.mark.asyncio
async def test_scripting_ok_with_long_plan() -> None:
    p = Project(topic="t", slug="t", general_plan="А" * 250)
    ok, reason, _ = await can_enter_running(None, p, ProjectStatus.scripting)  # type: ignore[arg-type]
    assert ok
    assert reason == ""


@pytest.mark.asyncio
async def test_plan_ready_not_confirmed_without_plan() -> None:
    from unittest.mock import AsyncMock, MagicMock

    p = Project(
        id=1,
        topic="t",
        slug="t",
        status=ProjectStatus.plan_ready,
        general_plan="шаблон",
    )
    session = AsyncMock()

    async def mock_execute(stmt):
        m = MagicMock()
        m.scalar_one.return_value = 0
        return m

    session.execute = mock_execute
    assert not await ready_status_confirmed_by_data(session, p, ProjectStatus.plan_ready)
