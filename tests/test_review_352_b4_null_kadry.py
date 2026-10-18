"""П.4: кадры null не обнуляют лестницу."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'apply.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("incoming", [None, [], "", {}])
async def test_null_kadry_keeps_old_ladder(
    session: AsyncSession, incoming: object
) -> None:
    project = Project(slug="null-kadry", topic="t", hero_mode="auto")
    session.add(project)
    await session.flush()
    ladder = [{"id": "1-K1", "план": "ОБЩИЙ", "закадр": "текст"}]
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="ab" * 12,
        voiceover_text="текст",
        sort_key=10.0,
        status="planned",
        attrs={"кадры": ladder},
    )
    session.add(frame)
    await session.commit()

    await apply_ops(
        session,
        project,
        [{"frame_uuid": frame.uuid, "fields": {"кадры": incoming}}],
    )
    await session.commit()
    live = (
        await session.execute(select(Frame).where(Frame.id == frame.id))
    ).scalar_one()
    assert (live.attrs or {}).get("кадры") == ladder
