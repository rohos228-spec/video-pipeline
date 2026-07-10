"""План не считается готовым, пока general_plan — не шаблон/заглушка."""

from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.services.plan_validation import MIN_GENERAL_PLAN_CHARS, is_meaningful_general_plan
from app.services.project_state import compute_actual_status
from app.storage.project_sheet import resolve_default_template_path
from openpyxl import load_workbook
from app.services.xlsx_v8_import import _read_general_plan


def test_v8_template_general_plan_is_not_meaningful() -> None:
    tpl = resolve_default_template_path()
    wb = load_workbook(tpl, read_only=True, data_only=True)
    text = _read_general_plan(wb)
    assert text
    assert len(text.strip()) < MIN_GENERAL_PLAN_CHARS
    assert not is_meaningful_general_plan(text)


def test_long_plan_is_meaningful() -> None:
    text = "А" * MIN_GENERAL_PLAN_CHARS
    assert is_meaningful_general_plan(text)


@pytest.mark.asyncio
async def test_compute_actual_status_ignores_short_general_plan() -> None:
    from unittest.mock import AsyncMock, MagicMock

    p = Project(
        id=1,
        topic="тест",
        slug="test",
        status=ProjectStatus.plan_ready,
        general_plan="короткий план из шаблона",
    )
    session = AsyncMock()
    call_count = {"n": 0}

    async def mock_execute(stmt):
        call_count["n"] += 1
        m = MagicMock()
        m.scalar_one.return_value = 0
        return m

    session.execute = mock_execute
    st = await compute_actual_status(session, p)
    assert st == ProjectStatus.new
