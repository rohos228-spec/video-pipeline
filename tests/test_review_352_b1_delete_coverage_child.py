"""П.1 ревью #352: дочерний кадр покрытия помечается, строка не удаляется."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameText, Project, PromptVersion
from app.services import db_v2
from app.services.montage_coverage_ops import delete_coverage_child


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cov.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_delete_coverage_child_marks_not_deletes(session: AsyncSession) -> None:
    project = Project(slug="cov-del", topic="t", hero_mode="auto")
    session.add(project)
    await session.flush()
    parent = Frame(
        project_id=project.id,
        number=1,
        uuid="aa" * 12,
        voiceover_text="родитель",
        sort_key=10.0,
        status="planned",
        attrs={
            "кадры": [
                {"id": "1-K1", "план": "ОБЩИЙ"},
                {"id": "1-K2", "план": "КРУПНЫЙ", "ракурс": "слева"},
            ],
            "camera_subdivide": {
                "role": "vo_parent",
                "parent_uuid": "aa" * 12,
            },
        },
    )
    child = Frame(
        project_id=project.id,
        number=2,
        uuid="bb" * 12,
        voiceover_text="шот",
        sort_key=20.0,
        status="planned",
        attrs={
            "кадры": [{"id": "1-K2", "план": "КРУПНЫЙ"}],
            "camera_subdivide": {
                "role": "shot",
                "parent_uuid": "aa" * 12,
                "shot_id": "1-K2",
            },
        },
    )
    session.add_all([parent, child])
    await session.flush()
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=child.id,
            kind="image",
            text="промт кадра",
            version=1,
        )
    )
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=child.id,
            kind="voiceover",
            text="шот",
        )
    )
    await session.commit()

    await delete_coverage_child(session, project, child, [parent, child])
    await session.commit()

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert {fr.number for fr in frames} == {1, 2}
    live_child = next(fr for fr in frames if fr.number == 2)
    assert (live_child.attrs or {}).get("активен") is False
    prompts = (
        await session.execute(
            select(func.count()).select_from(PromptVersion).where(PromptVersion.frame_id == child.id)
        )
    ).scalar_one()
    texts = (
        await session.execute(
            select(func.count()).select_from(FrameText).where(FrameText.frame_id == child.id)
        )
    ).scalar_one()
    assert prompts == 1
    assert texts == 1
    live_parent = next(fr for fr in frames if fr.number == 1)
    ladder = list((live_parent.attrs or {}).get("кадры") or [])
    ids = [str(shot.get("id")) for shot in ladder]
    assert "1-K2" in ids
    marked = next(shot for shot in ladder if shot.get("id") == "1-K2")
    assert marked.get("активен") is False
