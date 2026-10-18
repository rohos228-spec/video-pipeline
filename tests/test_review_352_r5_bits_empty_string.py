"""Ревью #352: строка «[]» в битах не стирает прежние биты."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services import db_v2
from app.services.db_apply import apply_ops


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'bits.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await db_v2.migrate_db_v2_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_bits_empty_string_does_not_wipe(session: AsyncSession) -> None:
    project = Project(slug="bits", topic="t", hero_mode="auto", meta={})
    session.add(project)
    await session.flush()
    frame = Frame(
        project_id=project.id,
        number=1,
        uuid="ab" * 12,
        voiceover_text="текст",
        sort_key=1.0,
        status="planned",
        attrs={"биты": [{"id": "b1", "text": "было"}]},
    )
    session.add(frame)
    await session.commit()

    await apply_ops(
        session,
        project,
        [{"frame_uuid": frame.uuid, "fields": {"биты": "[]"}}],
    )
    await session.commit()
    live = await session.get(Frame, frame.id)
    bits = (live.attrs or {}).get("биты")
    assert bits == [{"id": "b1", "text": "было"}]
