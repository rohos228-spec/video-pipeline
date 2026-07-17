"""Тесты панели монтажа (montage-board)."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project
from app.orchestrator.steps.generate_images import _XLSX_ROWS_PERSONS
from app.services.montage_board import build_montage_board
from app.services.montage_board_cache import clear_montage_board_caches
from app.services.plan_shot2 import ROW_IMAGE_PROMPT_2_V8, ROW_VIDEO_PROMPT_2_V8
from app.services.xlsx_v8_import import (
    ROW_IMAGE_PROMPT_V8,
    ROW_VIDEO_PROMPT_V8,
    ROW_VOICEOVER_V8,
    SHEET_PLAN_V8,
)


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


@pytest.mark.asyncio
async def test_montage_board_exposes_source_prompts_from_excel(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    """«Редактировать промт» должен получать промт исходника (Excel → board DTO)."""
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    col = 3  # кадр #1
    ws.cell(row=ROW_IMAGE_PROMPT_V8, column=col, value="IMG SHOT1 PROMPT")
    ws.cell(row=ROW_IMAGE_PROMPT_2_V8, column=col, value="IMG SHOT2 PROMPT")
    ws.cell(row=ROW_VIDEO_PROMPT_V8, column=col, value="VID SHOT1 PROMPT")
    ws.cell(row=ROW_VIDEO_PROMPT_2_V8, column=col, value="VID SHOT2 PROMPT long enough")
    wb.save(xlsx)

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        image_prompt="stale-db-image",
        animation_prompt="stale-db-video",
        status="planned",
        attrs={
            "image_prompt_shot2": "stale-db-img2",
            "animation_prompt_shot2": "stale-db-vid2",
        },
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board = await build_montage_board(session, montage_project)
    row = board["frames"][0]
    assert row["image_prompt_shot1"] == "IMG SHOT1 PROMPT"
    assert row["image_prompt_shot2"] == "IMG SHOT2 PROMPT"
    assert row["animation_prompt_shot1"] == "VID SHOT1 PROMPT"
    assert row["animation_prompt_shot2"] == "VID SHOT2 PROMPT long enough"


@pytest.mark.asyncio
async def test_montage_board_prompt_falls_back_to_frame_db(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    wb.save(xlsx)

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        image_prompt="DB IMAGE PROMPT",
        animation_prompt="DB VIDEO PROMPT",
        status="planned",
        attrs={"image_prompt_shot2": "DB IMAGE SHOT2"},
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board = await build_montage_board(session, montage_project)
    row = board["frames"][0]
    assert row["image_prompt_shot1"] == "DB IMAGE PROMPT"
    assert row["image_prompt_shot2"] == "DB IMAGE SHOT2"
    assert row["animation_prompt_shot1"] == "DB VIDEO PROMPT"


def _write_plan_with_persons(xlsx: Path, person_cell: str = "c01") -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=_XLSX_ROWS_PERSONS[0], column=3, value=person_cell)
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="vo")
    wb.save(xlsx)


@pytest.mark.asyncio
async def test_montage_board_picks_up_character_png_after_xlsx_cache(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    """Кэш Excel по mtime xlsx не должен «заморозить» пустые фото персонажей."""
    clear_montage_board_caches()
    xlsx = montage_project.data_dir / "project.xlsx"
    _write_plan_with_persons(xlsx, "c01")

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        status="planned",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board1 = await build_montage_board(session, montage_project)
    assert board1["frames"][0]["character_refs"][0]["image_url"] is None

    chars_dir = montage_project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "c01.png").write_bytes(b"png-after-cache")

    board2 = await build_montage_board(session, montage_project)
    url = board2["frames"][0]["character_refs"][0]["image_url"]
    assert url is not None
    assert "c01.png" in url


@pytest.mark.asyncio
async def test_montage_board_resolves_legacy_hero_png(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    clear_montage_board_caches()
    xlsx = montage_project.data_dir / "project.xlsx"
    _write_plan_with_persons(xlsx, "c02")

    chars_dir = montage_project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "hero_2_v1_deadbeef.png").write_bytes(b"legacy-hero")

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        status="planned",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board = await build_montage_board(session, montage_project)
    url = board["frames"][0]["character_refs"][0]["image_url"]
    assert url is not None
    assert "hero_2_v1_deadbeef.png" in url


@pytest.mark.asyncio
async def test_montage_board_reads_persons_from_legacy_row7(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    """excel-cells.ts раньше слал id в R7 (фон) — монтаж должен их видеть."""
    clear_montage_board_caches()
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=1, column=1, value="кадр1")
    ws.cell(row=7, column=1, value="фон")
    ws.cell(row=8, column=1, value="персонажи")
    ws.cell(row=7, column=3, value="c01")  # ошибочно записано в «фон»
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="vo")
    wb.save(xlsx)

    chars_dir = montage_project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "c01.png").write_bytes(b"png")

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        status="planned",
    )
    session.add(montage_project)
    session.add(fr)
    await session.flush()

    board = await build_montage_board(session, montage_project)
    refs = board["frames"][0]["character_refs"]
    assert len(refs) == 1
    assert refs[0]["id"] == "c01"
    assert refs[0]["image_url"] is not None


@pytest.mark.asyncio
async def test_montage_board_fallback_when_plan_has_no_person_ids(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    """Hero PNG есть, а план без c0N — всё равно показать персонажей в монтаже."""
    clear_montage_board_caches()
    xlsx = montage_project.data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = SHEET_PLAN_V8
    ws.cell(row=ROW_VOICEOVER_V8, column=3, value="текст кадра")
    persons = wb.create_sheet("Персонажи")
    persons.cell(row=1, column=2, value="c01")
    persons.cell(row=3, column=2, value="Герой")
    persons.cell(row=1, column=3, value="c02")
    persons.cell(row=3, column=3, value="Враг")
    wb.save(xlsx)

    chars_dir = montage_project.data_dir / "characters"
    chars_dir.mkdir(parents=True, exist_ok=True)
    (chars_dir / "c01.png").write_bytes(b"a")
    (chars_dir / "c02.png").write_bytes(b"b")

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
    refs = board["frames"][0]["character_refs"]
    assert [r["id"] for r in refs] == ["c01", "c02"]
    assert all(r["image_url"] for r in refs)


@pytest.mark.asyncio
async def test_montage_board_character_image_from_artifact(
    montage_project: Project,
    session: AsyncSession,
) -> None:
    clear_montage_board_caches()
    xlsx = montage_project.data_dir / "project.xlsx"
    _write_plan_with_persons(xlsx, "c01")

    # Файл лежит вне characters/ — только путь в Artifact.
    orphan = montage_project.data_dir / "old" / "characters" / "c01.png"
    orphan.parent.mkdir(parents=True, exist_ok=True)
    orphan.write_bytes(b"from-artifact")

    fr = Frame(
        project_id=montage_project.id,
        number=1,
        voiceover_text="vo",
        status="planned",
    )
    session.add(montage_project)
    session.add(fr)
    session.add(
        Artifact(
            project_id=montage_project.id,
            kind=ArtifactKind.hero_reference,
            uuid="montage-hero-c01",
            path=str(orphan),
            meta={"excel_id": "c01"},
        )
    )
    await session.flush()

    board = await build_montage_board(session, montage_project)
    url = board["frames"][0]["character_refs"][0]["image_url"]
    assert url is not None
    assert str(orphan) in url
