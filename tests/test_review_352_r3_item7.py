"""Ревью #352 п.7: неполный Каркас архивирует персонажа, не удаляет."""

from contextlib import asynccontextmanager
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Entity, Project, ProjectStatus
from app.services.db_apply import upsert_characters


@pytest.fixture
async def mem():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def scope():
        async with factory() as session:
            yield session
            await session.commit()

    yield scope
    await engine.dispose()


@pytest.mark.asyncio
async def test_replace_archives_missing_character(mem) -> None:
    async with mem() as session:
        project = Project(slug=f"c-{uuid.uuid4().hex[:6]}", topic="t", status=ProjectStatus.frames_ready)
        session.add(project)
        await session.flush()
        session.add(Entity(project_id=project.id, type="character", code="c01", name="Анна", attrs={"look": "1"}))
        session.add(Entity(project_id=project.id, type="character", code="c02", name="Борис", attrs={"look": "2"}))
        await session.flush()
        n = await upsert_characters(
            session,
            project,
            [{"id": "c01", "имя": "Анна", "внешность": "1"}],
            replace=True,
        )
        assert n == 1
        rows = list((await session.execute(
            __import__("sqlalchemy").select(Entity).where(Entity.project_id == project.id)
        )).scalars())
        by = {r.code: r for r in rows}
        assert set(by) == {"c01", "c02"}
        assert by["c02"].attrs.get("archived") is True
        assert by["c02"].attrs.get("inactive") is True
