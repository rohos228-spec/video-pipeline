"""recompute откатывает ложный plan_ready без реального плана."""

from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.services.project_state import recompute_status


@pytest.mark.asyncio
async def test_recompute_downgrades_false_plan_ready() -> None:
    from unittest.mock import AsyncMock, MagicMock

    p = Project(
        id=1,
        topic="тест",
        slug="test",
        status=ProjectStatus.plan_ready,
        general_plan="короткий шаблон из xlsx",
    )
    session = AsyncMock()

    async def mock_execute(stmt):
        m = MagicMock()
        m.scalar_one.return_value = 0
        return m

    session.execute = mock_execute

    old, new, changed = await recompute_status(session, p, log_prefix="test")
    assert changed is True
    assert old is ProjectStatus.plan_ready
    assert new is ProjectStatus.new
    assert p.status is ProjectStatus.new


@pytest.mark.asyncio
async def test_recompute_keeps_plan_ready_with_real_plan() -> None:
    from unittest.mock import AsyncMock, MagicMock

    plan = "А" * 250
    p = Project(
        id=2,
        topic="тест",
        slug="test2",
        status=ProjectStatus.plan_ready,
        general_plan=plan,
    )
    session = AsyncMock()

    async def mock_execute(stmt):
        m = MagicMock()
        m.scalar_one.return_value = 0
        return m

    session.execute = mock_execute

    old, new, changed = await recompute_status(session, p, log_prefix="test")
    assert changed is False
    assert new is ProjectStatus.plan_ready
    assert p.status is ProjectStatus.plan_ready
