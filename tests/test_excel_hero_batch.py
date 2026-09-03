"""Excel-hero: пакетная генерация и recompute при частичном прогрессе."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models import Project, ProjectStatus
from app.orchestrator.steps import generate_hero
from app.services.excel_characters import ExcelCharacter
from app.services.project_state import compute_actual_status


def test_excel_batch_auto_flag() -> None:
    """HITL снят: batch всегда, независимо от auto_mode/ai_control."""
    p = Project(topic="t", slug="t", auto_mode=False, meta={"ai_control": True})
    assert generate_hero._excel_batch_auto(p) is True
    p.auto_mode = True
    p.meta = {}
    assert generate_hero._excel_batch_auto(p) is True


@pytest.mark.asyncio
async def test_excel_ids_with_artifact_counts_disk_png(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """PNG на диске = готовый персонаж, даже без строки Artifact."""
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path)
    p = Project(id=1, topic="t", slug="t")
    chars = p.data_dir / "characters"
    chars.mkdir(parents=True)
    (chars / "c01.png").write_bytes(b"\x89PNG" + b"x" * 2000)
    (chars / "c07.png").write_bytes(b"tiny")

    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)

    ids = await generate_hero._excel_ids_with_artifact(session, p)
    assert "c01" in ids
    assert "c07" not in ids
    assert generate_hero._excel_disk_png(p, "c01") is not None
    assert generate_hero._excel_disk_png(p, "c07") is None


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
    # Stale HITL approved без файла на диске — НЕ deps (после wipe).
    assert not generate_hero._excel_ref_deps_met(
        ch, approved={"c01"}, generated=set(), batch_auto=True
    )
    assert not generate_hero._excel_ref_deps_met(
        ch, approved={"c01"}, generated=set(), batch_auto=False
    )
    assert generate_hero._excel_ref_deps_met(
        ch, approved={"c01"}, generated={"c01"}, batch_auto=False
    )


@pytest.mark.asyncio
async def test_compute_actual_status_no_hero_no_items_stays_frames_ready() -> None:
    """hero_count=0 без excel — не прыгать сразу на items_ready."""
    from unittest.mock import AsyncMock, MagicMock, patch

    p = Project(
        id=1,
        topic="t",
        slug="t",
        hero_mode="auto",
        hero_count=0,
        status=ProjectStatus.frames_ready,
    )
    p.general_plan = "x" * 200
    p.script_text = "script"
    session = AsyncMock()
    values = [5, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]

    async def mock_execute(stmt):
        m = MagicMock()
        m.scalar_one.return_value = values.pop(0) if values else 0
        return m

    session.execute = mock_execute
    with patch(
        "app.services.project_state._primary_image_prompt_counts",
        new=AsyncMock(return_value=(5, 0, 0)),
    ):
        st = await compute_actual_status(session, p)
    assert st is ProjectStatus.frames_ready


@pytest.mark.asyncio
async def test_compute_actual_status_partial_excel_hero() -> None:
    from unittest.mock import AsyncMock, MagicMock, patch

    p = Project(
        id=13,
        topic="t",
        slug="nicshe",
        hero_mode="auto",
        status=ProjectStatus.frames_ready,
        meta={
            "excel_hero": {
                "characters": [
                    {"id": "c01", "name": "A", "look": "лицо"},
                    {"id": "c02", "name": "B", "look": "лицо"},
                ]
            }
        },
    )
    session = AsyncMock()
    p.general_plan = "x" * 200
    p.script_text = "script"

    with (
        patch(
            "app.services.project_state._count_excel_hero_artifacts",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.project_state._primary_image_prompt_counts",
            new=AsyncMock(return_value=(10, 0, 0)),
        ),
    ):
        n = {"i": 0}

        async def mock_execute(stmt):
            n["i"] += 1
            m = MagicMock()
            # 1=fr_total=10, 2=anim=0, 3=hero_arts=1
            if n["i"] == 1:
                m.scalar_one.return_value = 10
            elif n["i"] == 3:
                m.scalar_one.return_value = 1
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
        status=ProjectStatus.image_prompts_ready,
        meta={"excel_hero": {"characters": [{"id": "c01"}, {"id": "c02"}]}},
    )
    p.general_plan = "x" * 200
    p.script_text = "script"
    session = AsyncMock()

    with (
        patch(
            "app.services.project_state._count_excel_hero_artifacts",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.project_state._primary_image_prompt_counts",
            new=AsyncMock(return_value=(10, 10, 10)),
        ),
    ):
        call_count = {"n": 0}

        async def mock_execute(stmt):
            call_count["n"] += 1
            m = MagicMock()
            # 1 fr_total, 2 fr_with_anim, 3 hero_arts, ...
            if call_count["n"] == 1:
                m.scalar_one.return_value = 10
            elif call_count["n"] == 3:
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
        status=ProjectStatus.enrich_3_ready,
        meta={
            "excel_hero": {"characters": [{"id": "c01"}, {"id": "c02"}]},
            "enrich_completed_slots": [1, 2, 3],
        },
    )
    p.general_plan = "x" * 200
    p.script_text = "script"
    session = AsyncMock()

    with (
        patch(
            "app.services.project_state._count_excel_hero_artifacts",
            new=AsyncMock(return_value=1),
        ),
        patch(
            "app.services.project_state._primary_image_prompt_counts",
            new=AsyncMock(return_value=(10, 0, 0)),
        ),
    ):
        call_count = {"n": 0}

        async def mock_execute(stmt):
            call_count["n"] += 1
            m = MagicMock()
            if call_count["n"] == 1:
                m.scalar_one.return_value = 10
            elif call_count["n"] == 3:
                m.scalar_one.return_value = 1
            else:
                m.scalar_one.return_value = 0
            return m

        session.execute = mock_execute
        st = await compute_actual_status(session, p)
        # meta сохраняет enrich_3_ready, но не поднимает ранний status до него
        assert st is ProjectStatus.enrich_3_ready


@pytest.mark.asyncio
async def test_compute_no_enrich_rollback_when_set_children_lack_prompts() -> None:
    """Регресс #58: SET-дети без image_prompt не откатывают в enrich_1→hero."""
    from unittest.mock import AsyncMock, MagicMock, patch

    p = Project(
        id=58,
        topic="t",
        slug="spesivcevy",
        hero_mode="auto",
        status=ProjectStatus.image_prompts_ready,
        meta={
            "excel_hero": {
                "characters": [
                    {"id": f"c{i:02d}", "name": "x", "look": "y"} for i in range(1, 9)
                ]
            },
            "enrich_completed_slots": [1],
            "split_completed": True,
            "scene_design": {"status": "done"},
        },
    )
    p.general_plan = "x" * 200
    p.script_text = "script"
    session = AsyncMock()

    with (
        patch(
            "app.services.project_state._count_excel_hero_artifacts",
            new=AsyncMock(return_value=8),
        ),
        patch(
            "app.services.project_state._primary_image_prompt_counts",
            # 20 primary с промтами, 33 всего с промтами, SET-дети без
            new=AsyncMock(return_value=(20, 20, 33)),
        ),
        patch(
            "app.services.project_state._excel_hero_expected_count",
            return_value=8,
        ),
    ):
        n = {"i": 0}

        async def mock_execute(stmt):
            n["i"] += 1
            m = MagicMock()
            if n["i"] == 1:
                m.scalar_one.return_value = 63  # fr_total
            elif n["i"] == 3:
                m.scalar_one.return_value = 8  # hero_arts
            else:
                m.scalar_one.return_value = 0
            return m

        session.execute = mock_execute
        st = await compute_actual_status(session, p)
        assert st is ProjectStatus.image_prompts_ready
        assert st is not ProjectStatus.enrich_1_ready
        assert st is not ProjectStatus.hero_ready


@pytest.mark.asyncio
async def test_recompute_status_never_downgrades() -> None:
    from unittest.mock import AsyncMock, patch

    from app.services.project_state import recompute_status

    p = Project(
        id=1,
        topic="t",
        slug="t",
        status=ProjectStatus.image_prompts_ready,
        general_plan="x" * 200,
        script_text="script",
    )
    session = AsyncMock()
    with (
        patch(
            "app.services.project_state.compute_actual_status",
            new=AsyncMock(return_value=ProjectStatus.hero_ready),
        ),
        patch(
            "app.services.step_data_guard.ready_status_confirmed_by_data",
            new=AsyncMock(return_value=True),
        ),
    ):
        old, new, changed = await recompute_status(session, p)
    assert old is ProjectStatus.image_prompts_ready
    assert new is ProjectStatus.image_prompts_ready
    assert changed is False
    assert p.status is ProjectStatus.image_prompts_ready
