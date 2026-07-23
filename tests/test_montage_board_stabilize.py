"""Стабилизация монтажной доски: кэш, job state, clear_stop."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.montage_board import build_montage_board
from app.services.montage_board_apply_job import _apply_tasks, get_apply_job
from app.services.montage_board_cache import clear_montage_board_caches
from app.services.montage_board_job_state import resolve_job_status
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.xlsx_v8_import import ROW_VOICEOVER_V8, SHEET_PLAN_V8


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    clear_montage_board_caches()
    _apply_tasks.clear()
    yield
    clear_montage_board_caches()
    _apply_tasks.clear()


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "stab.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=50, slug="stab", topic="Тест", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_get_apply_job_not_running_without_live_task(project: Project) -> None:
    board = montage_meta(project)
    board["apply_job"] = {"status": "running", "total_ops": 2, "done_ops": 0}
    set_montage_meta(project, board)

    job = get_apply_job(project)
    assert job["status"] == "error"
    assert "прервано" in (job.get("error") or "").lower() or job.get("error")


@pytest.mark.asyncio
async def test_get_apply_job_running_with_live_task(project: Project) -> None:
    board = montage_meta(project)
    board["apply_job"] = {"status": "running", "total_ops": 1}
    set_montage_meta(project, board)

    task = asyncio.create_task(asyncio.sleep(60))
    _apply_tasks[project.id] = task

    job = get_apply_job(project)
    assert job["status"] == "running"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_resolve_job_status_stale_running() -> None:
    job = resolve_job_status(1, {"status": "running"}, live_tasks={})
    assert job["status"] == "error"


@pytest.mark.asyncio
async def test_build_montage_board_uses_duration_cache(
    project: Project,
    session: AsyncSession,
    tmp_path: Path,
) -> None:
    xlsx = project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="Текст")
    wb.save(xlsx)

    fr = Frame(project_id=project.id, number=1, voiceover_text="t", status="planned")
    session.add(project)
    session.add(fr)
    await session.flush()

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True, exist_ok=True)
    vid = videos / "clip_001_abc.mp4"
    vid.write_bytes(b"\x00" * 64)

    probe_calls = 0

    async def _fake_probe(path: Path) -> float:
        nonlocal probe_calls
        probe_calls += 1
        return 1.5

    with patch(
        "app.services.montage_board_cache.probe_duration",
        new=AsyncMock(side_effect=_fake_probe),
    ):
        await build_montage_board(session, project)
        await build_montage_board(session, project)

    assert probe_calls == 1


def test_apply_runner_does_not_call_clear_stop() -> None:
    from app.services import montage_board_apply_job as mod

    src = inspect.getsource(mod.spawn_apply_job)
    assert "clear_stop" not in src
