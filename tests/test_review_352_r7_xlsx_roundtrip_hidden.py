"""Ревью #352: неактивный кадр не сдвигает соседей при export→import."""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.db_apply import export_project_xlsx
from app.services.xlsx_v8_import import SHEET_GENERAL_V8, SHEET_PLAN_V8


def _snapshot(frames: list[Frame]) -> dict[int, tuple]:
    return {
        fr.number: (
            fr.voiceover_text,
            fr.image_prompt,
            fr.animation_prompt,
            fr.duration_seconds,
        )
        for fr in frames
    }


def _frames(project_id: int) -> list[Frame]:
    rows = [
        (1, "голос-1", "картинка-1", "видео-1", 1.5, {}),
        (
            2,
            "голос-2",
            "картинка-2",
            "видео-2",
            2.5,
            {"удалён": True, "активен": False, "карантин": True},
        ),
        (3, "голос-3", "картинка-3", "видео-3", 3.5, {}),
        (4, "голос-4", "картинка-4", "видео-4", 4.5, {}),
    ]
    return [
        Frame(
            project_id=project_id,
            number=number,
            voiceover_text=voice,
            image_prompt=image,
            animation_prompt=anim,
            duration_seconds=dur,
            status=FrameStatus.planned,
            attrs=attrs,
        )
        for number, voice, image, anim, dur, attrs in rows
    ]


async def _project(session: AsyncSession, slug: str, root: Path) -> tuple[Project, list[Frame]]:
    project = Project(
        slug=slug,
        topic="t",
        status=ProjectStatus.frames_ready,
        script_text="голос-1 голос-3 голос-4",
        meta={},
    )
    session.add(project)
    await session.flush()
    frames = _frames(project.id)
    session.add_all(frames)
    await session.flush()
    folder = root / "videos" / slug
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "project.xlsx"
    wb = Workbook()
    wb.active.title = SHEET_PLAN_V8
    wb.create_sheet(SHEET_GENERAL_V8)
    wb.save(path)
    wb.close()
    return project, frames


@pytest.mark.asyncio
async def test_hidden_frame_roundtrip_keeps_neighbors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", str(tmp_path))
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with factory() as session:
        bot_project, bot_frames = await _project(session, "bot-round", tmp_path)
        gpt_project, gpt_frames = await _project(session, "gpt-round", tmp_path)
        before_bot = _snapshot(bot_frames)
        before_gpt = _snapshot(gpt_frames)
        export_project_xlsx(bot_project, bot_frames)
        exported = bot_project.data_dir / "project.xlsx"
        target = gpt_project.data_dir / "project.xlsx"
        target.write_bytes(exported.read_bytes())

        from app.services.xlsx_v8_import import import_v8_xlsx

        await import_v8_xlsx(
            session,
            bot_project,
            exported,
            keep_fields=False,
            update_frames_voiceover=True,
        )
        await session.refresh(bot_frames[0])
        for fr in bot_frames:
            await session.refresh(fr)
        assert _snapshot(bot_frames) == before_bot

        from app.services.chatgpt_xlsx import sync_project_xlsx

        await sync_project_xlsx(session, gpt_project, target)
        for fr in gpt_frames:
            await session.refresh(fr)
        assert _snapshot(gpt_frames) == before_gpt

    await engine.dispose()
