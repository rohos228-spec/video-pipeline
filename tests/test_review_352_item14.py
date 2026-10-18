"""№14: replace_all_frames архивирует старые кадры и не стирает их."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameText, Project, PromptVersion
from app.services import db_v2


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


@pytest.mark.asyncio
async def test_replace_all_frames_archives_previous_cells(session: AsyncSession) -> None:
    project = Project(slug="item14", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    old = Frame(
        project_id=project.id,
        number=1,
        uuid="old-frame-uuid",
        voiceover_text="старый закадр",
        attrs={"keep": 1},
    )
    session.add(old)
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=old.id,
            kind="voiceover",
            text="старый закадр",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=old.id,
            kind="img",
            text="старый промт",
        )
    )
    await session.commit()

    created = await db_v2.replace_all_frames(
        session,
        project,
        [
            {"voiceover_text": "первая новая ячейка"},
            {"voiceover_text": "вторая новая ячейка"},
        ],
    )
    await session.commit()

    frames = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
            )
        ).scalars()
    )
    assert len(created) == 2
    assert len(frames) == 3
    archived = next(fr for fr in frames if fr.uuid == "old-frame-uuid")
    assert archived.voiceover_text == "старый закадр"
    assert (archived.attrs or {}).get("archived") is True
    assert ((archived.attrs or {}).get("camera_subdivide") or {}).get("leftover") is True
    texts = list(
        (await session.execute(select(FrameText).where(FrameText.frame_id == old.id))).scalars()
    )
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.frame_id == old.id)
            )
        ).scalars()
    )
    assert texts[0].text == "старый закадр"
    assert prompts[0].text == "старый промт"
    assert [fr.voiceover_text for fr in created] == [
        "первая новая ячейка",
        "вторая новая ячейка",
    ]
    numbers = [int(fr.number) for fr in frames]
    assert len(numbers) == len(set(numbers))

    again = await db_v2.replace_all_frames(
        session,
        project,
        [
            {"voiceover_text": "ещё раз первая"},
            {"voiceover_text": "ещё раз вторая"},
        ],
    )
    await session.commit()
    frames_again = list(
        (
            await session.execute(
                select(Frame).where(Frame.project_id == project.id)
            )
        ).scalars()
    )
    assert len(again) == 2
    assert len(frames_again) == 5
    assert len({int(fr.number) for fr in frames_again}) == 5
    still = next(fr for fr in frames_again if fr.uuid == "old-frame-uuid")
    assert still.voiceover_text == "старый закадр"
