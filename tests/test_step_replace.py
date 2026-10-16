"""Новая попытка шага заменяет его выход — общая механика, не одна нода."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.step_replace import (
    replace_step_outputs_on_enter,
    step_code_for_running_status,
)


def test_running_status_maps_to_step_code() -> None:
    assert step_code_for_running_status(ProjectStatus.generating_image_prompts) == "img_pr"
    assert step_code_for_running_status(ProjectStatus.generating_images) == "img"
    assert step_code_for_running_status(ProjectStatus.generating_videos) == "video"
    assert step_code_for_running_status(ProjectStatus.generating_animation_prompts) == "anim_pr"
    assert step_code_for_running_status(ProjectStatus.enriching_2) == "excel_gpt"
    assert step_code_for_running_status(ProjectStatus.image_prompts_ready) is None


@pytest_asyncio.fixture
async def session(tmp_path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_enter_img_pr_wipes_old_prompts(session):
    p = Project(slug="p1", topic="t", hero_mode="full_auto", status=ProjectStatus.enrich_3_ready)
    session.add(p)
    await session.flush()
    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="vo",
        image_prompt="OLD PROMPT",
        status=FrameStatus.image_prompt_ready,
    )
    session.add(fr)
    await session.flush()

    await replace_step_outputs_on_enter(
        session, p, ProjectStatus.generating_image_prompts
    )
    await session.refresh(fr)
    assert not (fr.image_prompt or "").strip()


ANIM = "Camera slowly pushes in on the subject while light shifts across the scene, subtle motion."


async def _anim_project(session, n=2):
    from app.services import anim_pr_sidecar

    anim_pr_sidecar._RESULTS.clear()
    p = Project(slug="pa", topic="t", hero_mode="full_auto", status=ProjectStatus.images_ready)
    session.add(p)
    await session.flush()
    frs = []
    for i in range(1, n + 1):
        fr = Frame(project_id=p.id, number=i, voiceover_text=f"vo{i}",
                   image_prompt=f"A detailed image prompt number {i} for the scene",
                   animation_prompt=ANIM, status=FrameStatus.animation_prompt_ready)
        session.add(fr)
        frs.append(fr)
    await session.flush()
    return p, frs


@pytest.mark.asyncio
async def test_enter_anim_pr_keeps_fresh_sidecar_result(session):
    from app.services.anim_pr_sidecar import stamp_sidecar_results

    p, frs = await _anim_project(session)
    assert stamp_sidecar_results(p.id, frs) == 2
    out = await replace_step_outputs_on_enter(session, p, ProjectStatus.generating_animation_prompts)
    assert out["anim_pr"]["kept_from_sidecar"] == 2
    for fr in frs:
        await session.refresh(fr)
        assert fr.animation_prompt == ANIM
    await replace_step_outputs_on_enter(session, p, ProjectStatus.generating_animation_prompts)
    for fr in frs:
        await session.refresh(fr)
        assert not (fr.animation_prompt or "").strip()


@pytest.mark.asyncio
async def test_enter_anim_pr_without_stamp_or_stale_input_still_wipes(session):
    from app.services.anim_pr_sidecar import stamp_sidecar_results

    p, frs = await _anim_project(session)
    await replace_step_outputs_on_enter(session, p, ProjectStatus.generating_animation_prompts)
    for fr in frs:
        await session.refresh(fr)
        assert not (fr.animation_prompt or "").strip()

    for fr in frs:
        fr.animation_prompt = ANIM
    stamp_sidecar_results(p.id, frs)
    frs[1].image_prompt = "A completely different image prompt after the sidecar ran"
    await session.flush()
    out = await replace_step_outputs_on_enter(session, p, ProjectStatus.generating_animation_prompts)
    assert out["anim_pr"] == {"kept_from_sidecar": 1, "frames_cleared": 1}
    await session.refresh(frs[0])
    await session.refresh(frs[1])
    assert frs[0].animation_prompt == ANIM
    assert not (frs[1].animation_prompt or "").strip()
