"""Тесты панели монтажа (montage-board)."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.orchestrator.steps.generate_images import _XLSX_ROWS_PERSONS
from app.services.montage_board import build_montage_board
from app.services.xlsx_v8_import import SHEET_PLAN_V8, ROW_VOICEOVER_V8


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "montage.db"
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
    p = Project(id=99, slug="montage-test", topic="Тест", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.asyncio
async def test_montage_board_reads_excel_voiceover_and_characters(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    persons_row = _XLSX_ROWS_PERSONS[0]
    ws.cell(row=persons_row, column=3, value="c01, c02")
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="Текст закадровки кадра 1")
    wb.create_sheet("Персонажи")
    persons = wb["Персонажи"]
    persons.cell(row=1, column=2, value="c01")
    persons.cell(row=3, column=2, value="Кот")
    persons.cell(row=1, column=3, value="c02")
    persons.cell(row=3, column=3, value="Мышь")
    wb.save(xlsx)

    chars_dir = montage_project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "c01.png").write_bytes(b"png1")
    (chars_dir / "c02.png").write_bytes(b"png2")

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="из БД",
        status="planned",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board = await build_montage_board(session, montage_project)
    assert board["frame_count"] == 1
    assert "meta" in board
    assert board["meta"]["video_trims"] == {}
    row = board["frames"][0]
    assert row["voiceover_excel"] == "Текст закадровки кадра 1"
    assert row["characters"] == "c01, c02"
    assert row["number"] == 1
    assert len(row["character_refs"]) == 2
    assert row["character_refs"][0]["id"] == "c01"
    assert row["character_refs"][0]["name"] == "Кот"
    assert row["character_refs"][0]["image_url"] is not None
    assert row["character_refs"][1]["id"] == "c02"
