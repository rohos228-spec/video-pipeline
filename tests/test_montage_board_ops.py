"""Тесты операций панели монтажа: meta, apply, assets."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.orchestrator.steps.generate_images import _XLSX_ROWS_PERSONS
from app.services.montage_board_apply import apply_montage_board
from app.services.montage_board_assets import (
    delete_scene_image,
    save_scene_image_upload,
)
from app.services.montage_board_meta import montage_meta, trim_key
from app.services.xlsx_v8_import import SHEET_PLAN_V8, ROW_VOICEOVER_V8


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "montage_ops.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def montage_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=101, slug="montage-ops", topic="Тест", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.asyncio
async def test_apply_saves_video_trims_to_meta(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    session.add(montage_project)
    await session.flush()

    trims = {"1:1": {"start": 0.0, "end": 2.5}}
    result = await apply_montage_board(
        session,
        montage_project,
        video_trims=trims,
        pending_ops=[],
    )
    assert result["ok"] is True
    meta = montage_meta(montage_project)
    assert meta["video_trims"]["1:1"]["end"] == 2.5
    assert meta.get("applied_at")


@pytest.mark.asyncio
async def test_delete_and_upload_scene_image(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="Текст")
    wb.save(xlsx)

    fr = Frame(project_id=montage_project.id, number=1, voiceover_text="t", status="planned")
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    scenes = montage_project.data_dir / "scenes"
    scenes.mkdir(parents=True, exist_ok=True)
    img = scenes / "frame_001_abc.png"
    img.write_bytes(b"png")

    path = await save_scene_image_upload(
        session,
        montage_project,
        1,
        shot=1,
        content=b"newpng",
        suffix=".png",
    )
    assert path.is_file()

    deleted = await delete_scene_image(session, montage_project, 1, shot=1)
    assert deleted is True
    assert not list(scenes.glob("frame_001_*.png"))


def test_trim_key_format() -> None:
    assert trim_key(3, 1) == "3:1"
