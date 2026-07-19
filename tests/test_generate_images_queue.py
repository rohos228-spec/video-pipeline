"""Очередь generate_images: промты из xlsx и завершение шага."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.models import Frame, FrameStatus
from app.orchestrator.steps.generate_images import _all_frames_have_image_or_failed
from app.services.scan_frames import frame_needs_shot1_image
from app.services.xlsx_v8_import import (
    ROW_IMAGE_PROMPT_V8,
    read_v8_image_prompts_from_path,
)


def _write_plan_xlsx(path: Path, *, prompts: list[str], voiceovers: list[str | None]) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    for i, prompt in enumerate(prompts, start=1):
        col = i + 2
        ws.cell(row=ROW_IMAGE_PROMPT_V8, column=col, value=prompt)
        vo = voiceovers[i - 1] if i - 1 < len(voiceovers) else None
        if vo:
            ws.cell(row=49, column=col, value=vo)
    wb.save(path)


def test_read_v8_image_prompts_without_voiceover(tmp_path: Path) -> None:
    xlsx = tmp_path / "project.xlsx"
    _write_plan_xlsx(
        xlsx,
        prompts=["prompt one", "prompt two"],
        voiceovers=[None, None],
    )
    got = read_v8_image_prompts_from_path(xlsx)
    assert got == {1: "prompt one", 2: "prompt two"}


async def test_all_frames_done_checks_prompted_frames_not_empty_ones(
    tmp_path: Path,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.models import Base, Project

    scenes = tmp_path / "scenes"
    scenes.mkdir()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        project = Project(slug="t", topic="t")
        session.add(project)
        await session.flush()
        session.add(
            Frame(
                project_id=project.id,
                number=1,
                voiceover_text="v1",
                image_prompt="p1",
                status=FrameStatus.image_prompt_ready,
            )
        )
        session.add(
            Frame(
                project_id=project.id,
                number=2,
                voiceover_text="v2",
                image_prompt="",
                status=FrameStatus.planned,
            )
        )
        await session.commit()
        assert await _all_frames_have_image_or_failed(session, project.id, scenes) is False
        png = scenes / "frame_001_abcd1234.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 250_000)
        assert await _all_frames_have_image_or_failed(session, project.id, scenes) is True
    await engine.dispose()


def test_failed_frame_skipped_from_queue(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    scenes.mkdir()
    fr = Frame(project_id=1, number=1, voiceover_text="v", image_prompt="prompt")
    fr.status = FrameStatus.failed
    assert frame_needs_shot1_image(fr, scenes) is False
