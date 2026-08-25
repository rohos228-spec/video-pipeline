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
