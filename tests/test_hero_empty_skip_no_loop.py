"""P0: empty hero skip must not oscillate frames_ready ↔ generating_hero."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.project_state import (
    _excel_hero_expected_count,
    _hero_step_required,
    compute_actual_status,
)


@pytest.fixture
async def mem_db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    @asynccontextmanager
    async def _scope():
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("app.db.session_scope", _scope)
    yield _scope
    await engine.dispose()


@pytest.mark.asyncio
async def test_empty_id_stubs_not_counted_as_excel_heroes(mem_db) -> None:
    async with mem_db() as session:
        p = Project(
            slug=f"hero-stub-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.frames_ready,
            hero_mode="hero",
            meta={
                "excel_hero": {
                    "characters": [
                        {"id": "c01", "name": "", "look": ""},
                        {"id": "c02", "name": "A", "look": "tall"},
                    ]
                }
            },
        )
        session.add(p)
        await session.flush()
        assert _excel_hero_expected_count(p) == 1


@pytest.mark.asyncio
async def test_hero_skipped_empty_keeps_hero_ready(mem_db) -> None:
    async with mem_db() as session:
        p = Project(
            slug=f"hero-skip-{uuid.uuid4().hex[:8]}",
            topic="t",
            status=ProjectStatus.hero_ready,
            hero_mode="hero",
            meta={"hero_skipped_empty": True, "split_completed": True},
            script_text="Закадровый текст для теста пустого hero skip. " * 5,
            general_plan="## План\n\nГлавная тема: тест. Главная мысль: проверка skip. " * 8,
        )
        session.add(p)
        await session.flush()
        # frame so compute is past script
        from app.models import Frame

        session.add(
            Frame(project_id=p.id, number=1, voiceover_text="hi", status="planned")
        )
        await session.flush()
        assert _hero_step_required(p) is False
        actual = await compute_actual_status(session, p)
        assert actual == ProjectStatus.hero_ready
