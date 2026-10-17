"""Физические delete кадров/ячеек/связей заменены пометкой. Строки остаются."""

from __future__ import annotations

import uuid

import pytest
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fastapi import HTTPException

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Entity,
    Frame,
    FrameEdge,
    FrameText,
    Project,
    PromptVersion,
    SceneDesignCell,
)
from app.services import db_v2
from app.services.frame_tombstones import mark_frame_deleted
from app.services.montage_board_frames import upsert_frame_voiceover
from app.services.reset_step import _wipe_split
from app.services.scene_design import cells as sd_cells
from app.services.scene_design.camera_expand import (
    already_subdivided,
    collapse_shot_children,
)
from app.services.scene_design.truncate_vo import truncate_vo_to_ratio
from app.services.vo_shot_expand import rebuild_vo_cells_from_shots
from app.web.routers.db_browser import (
    delete_edge,
    delete_entity,
    restore_edge,
    restore_entity,
)


@pytest.fixture
def warn_log():
    lines: list[str] = []
    sink = logger.add(lambda message: lines.append(str(message)), level="WARNING")
    try:
        yield lines
    finally:
        logger.remove(sink)


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


async def _project(session: AsyncSession, slug: str) -> Project:
    project = Project(slug=slug, topic="t", meta={})
    session.add(project)
    await session.flush()
    return project


@pytest.mark.asyncio
async def test_wipe_split_marks_frames_and_keeps_rows(session: AsyncSession, warn_log) -> None:
    """Было: ``await session.delete(fr)`` в ``_wipe_split`` (reset_step.py)."""
    project = await _project(session, "wipe-split")
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="frame-keep",
        voiceover_text="Текст кадра",
        sort_key=1.0,
        attrs={},
    )
    session.add(frame)
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=frame.id,
            kind="voiceover",
            text="Текст кадра",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=frame.id,
            kind="img",
            version=1,
            text="промт кадра",
        )
    )
    session.add(
        Artifact(
            project_id=project.id,
            frame_id=frame.id,
            kind=ArtifactKind.scene_image,
            uuid=str(uuid.uuid4()),
            path="/tmp/frame-keep.png",
        )
    )
    await session.commit()
    frame_id = int(frame.id)

    report = await _wipe_split(session, project)
    await session.commit()

    kept = await session.get(Frame, frame_id)
    assert kept is not None
    assert kept.attrs.get("удалён") is True
    assert kept.attrs.get("активен") is False
    assert report["frames_deleted"] == 0
    assert report["frames_marked"] == 1
    texts = list(
        (await session.execute(select(FrameText).where(FrameText.frame_id == frame_id))).scalars()
    )
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.frame_id == frame_id)
            )
        ).scalars()
    )
    arts = list(
        (await session.execute(select(Artifact).where(Artifact.frame_id == frame_id))).scalars()
    )
    assert texts and texts[0].text == "Текст кадра"
    assert prompts and prompts[0].text == "промт кадра"
    assert arts and arts[0].path == "/tmp/frame-keep.png"
    assert any("помечен" in line and "строка сохранена" in line for line in warn_log)

