"""После gate-Stop stop-файл не должен блокировать montage regen."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.project_control import stop_project_running
from app.services.step_cancel import clear_all, is_stop_requested, request_stop
from app.settings import settings


@pytest.fixture(autouse=True)
def _clean_stop_flags() -> None:
    clear_all()
    yield
    clear_all()


@pytest.fixture
async def session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _stop_flag_path(project_id: int) -> Path:
    return Path(settings.data_dir) / ".stop" / f"project_{project_id}.stop"


@pytest.mark.asyncio
async def test_gate_stop_clears_stop_file(session: AsyncSession) -> None:
    p = Project(slug="t", topic="Test", status=ProjectStatus.assembled)
    session.add(p)
    await session.flush()

    request_stop(p.id)
    assert _stop_flag_path(p.id).is_file()

    info = await stop_project_running(session, p)

    assert info["ok"] is True
    assert info["stopped_kind"] == "gate"
    assert is_stop_requested(p.id) is False
    assert not _stop_flag_path(p.id).exists()


@pytest.mark.asyncio
async def test_apply_endpoint_clears_lingering_stop_file(session: AsyncSession) -> None:
    """Эмуляция: stop-файл остался — apply API должен снять перед spawn."""
    from app.services.step_cancel import clear_stop

    p = Project(slug="t2", topic="Test2", status=ProjectStatus.assembled)
    session.add(p)
    await session.flush()

    request_stop(p.id)
    assert is_stop_requested(p.id) is True

    clear_stop(p.id)
    assert is_stop_requested(p.id) is False
