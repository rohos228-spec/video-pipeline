"""№13: vo_shot_expand помечает лишние кадры leftover и не стирает их."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameText, Project, PromptVersion
from app.services import db_v2
from app.services.vo_shot_expand import (
    is_coverage_leftover,
    rebuild_vo_cells_from_shots,
)


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
async def test_rebuild_archives_empty_vo_instead_of_delete(session: AsyncSession) -> None:
    project = Project(slug="item13", topic="t", status=None)  # type: ignore[arg-type]
    session.add(project)
    await session.flush()
    keep = Frame(
        project_id=project.id,
        number=1,
        uuid="keep-vo-uuid",
        voiceover_text="Живой закадр кадра.",
        attrs={},
    )
    extra = Frame(
        project_id=project.id,
        number=2,
        uuid="extra-vo-uuid",
        voiceover_text="",
        image_prompt="промт без закадра",
        attrs={},
    )
    session.add_all([keep, extra])
    await session.flush()
    session.add(
        FrameText(
            project_id=project.id,
            frame_id=extra.id,
            kind="voiceover",
            text="текст, который нельзя стереть со всего проекта",
        )
    )
    session.add(
        PromptVersion(
            project_id=project.id,
            frame_id=extra.id,
            kind="img",
            version=1,
            text="промт без закадра",
        )
    )
    await session.commit()

    _ordered, report = await rebuild_vo_cells_from_shots(session, project, [keep, extra])
    await session.commit()

    frames = list(
        (await session.execute(select(Frame).where(Frame.project_id == project.id))).scalars()
    )
    assert {fr.uuid for fr in frames} == {"keep-vo-uuid", "extra-vo-uuid"}
    assert report["deleted"] == 0
    assert report["archived"] >= 1
    archived = next(fr for fr in frames if fr.uuid == "extra-vo-uuid")
    assert is_coverage_leftover(archived)
    texts = list(
        (
            await session.execute(
                select(FrameText).where(FrameText.frame_id == extra.id)
            )
        ).scalars()
    )
    assert texts and texts[0].text.startswith("текст, который нельзя")
    prompts = list(
        (
            await session.execute(
                select(PromptVersion).where(PromptVersion.frame_id == extra.id)
            )
        ).scalars()
    )
    assert prompts and prompts[0].text == "промт без закадра"
