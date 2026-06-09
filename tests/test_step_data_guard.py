"""step_data_guard: не откатывать running-статусы."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models import Project, ProjectStatus
from app.services.step_data_guard import clamp_status_to_data


@pytest.mark.asyncio
async def test_clamp_skips_running_scripting() -> None:
    project = MagicMock(spec=Project)
    project.id = 13
    project.status = ProjectStatus.scripting
    session = AsyncMock()

    with patch(
        "app.services.step_data_guard.compute_actual_status",
        new_callable=AsyncMock,
        return_value=ProjectStatus.plan_ready,
    ) as compute:
        result = await clamp_status_to_data(session, project)

    assert result is None
    assert project.status is ProjectStatus.scripting
    compute.assert_not_called()
