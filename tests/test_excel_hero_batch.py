"""Excel-hero: пакетная генерация и recompute при частичном прогрессе."""

from __future__ import annotations

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator.steps import generate_hero
from app.services.excel_characters import ExcelCharacter
from app.services.project_state import compute_actual_status


def test_excel_batch_auto_flag() -> None:
    p = Project(topic="t", slug="t", auto_mode=True, meta={})
    assert generate_hero._excel_batch_auto(p) is True
    p.meta = {"ai_control": True}
    assert generate_hero._excel_batch_auto(p) is False


def test_excel_ref_deps_batch_uses_generated() -> None:
    ch = ExcelCharacter(id="c02", name="x", look="y", ref_ids=["c01"])
    assert generate_hero._excel_ref_deps_met(
        ch, approved=set(), generated={"c01"}, batch_auto=True
    )
    assert not generate_hero._excel_ref_deps_met(
        ch, approved=set(), generated=set(), batch_auto=True
    )
    assert not generate_hero._excel_ref_deps_met(
        ch, approved=set(), generated={"c01"}, batch_auto=False
    )


@pytest.mark.asyncio
async def test_compute_actual_status_no_hero_no_items_stays_frames_ready() -> None:
    """hero_count=0 без excel — не прыгать сразу на items_ready."""
    from unittest.mock import AsyncMock, MagicMock

    p = Project(id=1, topic="t", slug="t", hero_mode="auto", hero_count=0)
    p.general_plan = "plan"
    p.script_text = "script"
    session = AsyncMock()
    call_count = {"n": 0}

    async def mock_execute(stmt):
        call_count["n"] += 1
        m = MagicMock()
        if call_count["n"] == 1:
            m.scalar_one.return_value = 5  # fr_total
        else:
            m.scalar_one.return_value = 0
        return m

    session.execute = mock_execute
    st = await compute_actual_status(session, p)
    assert st is ProjectStatus.frames_ready


@pytest.mark.asyncio
async def test_compute_actual_status_partial_excel_hero() -> None:
    from unittest.mock import AsyncMock, MagicMock

    p = Project(
        id=13,
        topic="t",
        slug="nicshe",
        hero_mode="auto",
        meta={
            "excel_hero": {
                "characters": [
                    {"id": "c01"},
                    {"id": "c02"},
                ]
            }
        },
    )
    session = AsyncMock()
    from unittest.mock import patch

    with patch(
        "app.services.project_state._count_excel_hero_artifacts",
        new=AsyncMock(return_value=1),
    ):
        p.general_plan = "plan"
        p.script_text = "script"

        call_count = {"n": 0}

        async def mock_execute(stmt):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 4:
                m.scalar_one.return_value = 1  # hero_arts
            elif call_count["n"] == 1:
                m.scalar_one.return_value = 10  # fr_total
            else:
                m.scalar_one.return_value = 0
            return m

        session.execute = mock_execute
        st = await compute_actual_status(session, p)
        assert st is ProjectStatus.hero_ready


@pytest.mark.asyncio
async def test_compute_actual_status_img_pr_done_despite_partial_excel_hero() -> None:
    """image_prompt на всех кадрах — не откатывать до hero_ready."""
    from unittest.mock import AsyncMock, MagicMock, patch

    p = Project(
        id=13,
        topic="t",
        slug="nicshe",
        hero_mode="auto",
        meta={"excel_hero": {"characters": [{"id": "c01"}, {"id": "c02"}]}},
    )
    p.general_plan = "plan"
    p.script_text = "script"
    session = AsyncMock()

    with patch(
        "app.services.project_state._count_excel_hero_artifacts",
        new=AsyncMock(return_value=1),
    ):
        call_count = {"n": 0}

        async def mock_execute(stmt):
            call_count["n"] += 1
            m = MagicMock()
            # 1 fr_total, 2 fr_with_img_prompt, 3 fr_with_anim, 4 hero_arts, ...
            if call_count["n"] == 1:
                m.scalar_one.return_value = 10
            elif call_count["n"] == 2:
                m.scalar_one.return_value = 10
            elif call_count["n"] == 4:
                m.scalar_one.return_value = 1
            else:
                m.scalar_one.return_value = 0
            return m

        session.execute = mock_execute
        st = await compute_actual_status(session, p)
        assert st is ProjectStatus.image_prompts_ready


@pytest.mark.asyncio
async def test_compute_actual_status_enrich_meta_beats_partial_excel_hero() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    p = Project(
        id=13,
        topic="t",
        slug="nicshe",
        hero_mode="auto",
        meta={
            "excel_hero": {"characters": [{"id": "c01"}, {"id": "c02"}]},
            "enrich_completed_slots": [1, 2, 3],
        },
    )
    p.general_plan = "plan"
    p.script_text = "script"
    session = AsyncMock()

    with patch(
        "app.services.project_state._count_excel_hero_artifacts",
        new=AsyncMock(return_value=1),
    ):
        call_count = {"n": 0}

        async def mock_execute(stmt):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                m.scalar_one.return_value = 10
            elif call_count["n"] == 2:
                m.scalar_one.return_value = 0
            elif call_count["n"] == 4:
                m.scalar_one.return_value = 1
            else:
                m.scalar_one.return_value = 0
            return m

        session.execute = mock_execute
        st = await compute_actual_status(session, p)
        assert st is ProjectStatus.enrich_3_ready


@pytest.mark.asyncio
async def test_recompute_status_never_downgrades() -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.project_state import recompute_status

    p = Project(
        id=1,
        topic="t",
        slug="t",
        status=ProjectStatus.image_prompts_ready,
    )
    session = AsyncMock()
    with patch(
        "app.services.project_state.compute_actual_status",
        new=AsyncMock(return_value=ProjectStatus.hero_ready),
    ):
        old, new, changed = await recompute_status(session, p)
    assert old is ProjectStatus.image_prompts_ready
    assert new is ProjectStatus.image_prompts_ready
    assert changed is False
    assert p.status is ProjectStatus.image_prompts_ready
