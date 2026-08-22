"""start_step/sync не должен перетирать готовый script_text склейкой R49."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.services.xlsx_v8_import import ROW_VOICEOVER_V8, import_v8_xlsx


def _v8_xlsx(path: Path, blocks: list[str]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    for i, text in enumerate(blocks, start=3):
        ws.cell(row=ROW_VOICEOVER_V8, column=i, value=text)
    wb.save(path)


@pytest.mark.asyncio
async def test_import_v8_keep_fields_preserves_script_text(tmp_path: Path) -> None:
    xlsx = tmp_path / "project.xlsx"
    _v8_xlsx(xlsx, ["короткий", "блок"])

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    original = "Полный закадровый текст сценария, который нельзя затирать из R49."
    async with factory() as session:
        project = Project(
            slug="keep-fields",
            topic="t",
            status=ProjectStatus.frames_ready,
            script_text=original,
            meta={},
        )
        session.add(project)
        await session.commit()
        await session.refresh(project)

        overwritten = await import_v8_xlsx(
            session, project, xlsx, keep_fields=False
        )
        assert "script_text" in overwritten["project_fields_changed"]
        assert project.script_text != original
        assert "короткий" in (project.script_text or "")

        project.script_text = original
        await session.flush()

        kept = await import_v8_xlsx(session, project, xlsx, keep_fields=True)
        assert "script_text" not in kept["project_fields_changed"]
        assert project.script_text == original

    await engine.dispose()
