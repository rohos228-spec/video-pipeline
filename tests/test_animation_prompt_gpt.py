"""Парсер и batch-сообщения anim_pr."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameStatus, Project, ProjectStatus
from sqlalchemy import select
from app.services.animation_prompt_gpt import (
    FrameImageBatchItem,
    _clean_animation_text,
    build_batch_message,
)
from app.services.project_steps import start_step


def test_clean_animation_text_strips_label() -> None:
    raw = "текст анимации: Camera dolly in slowly."
    assert _clean_animation_text(raw) == "Camera dolly in slowly."


def test_build_batch_message_has_id_and_voiceover() -> None:
    fr = SimpleNamespace(number=3, voiceover_text="Hello")
    item = FrameImageBatchItem(
        frame=fr,
        image_path=Path("/x.png"),
        image_id="[ID: P9-F3-deadbeef]",
        voiceover="Hello",
    )
    msg = build_batch_message([item])
    assert "ID изображения: [ID: P9-F3-deadbeef]" in msg
    assert "Закадровый текст: Hello" in msg
    assert "лента" in msg
    assert "Позиция 1" in msg


@pytest_asyncio.fixture
async def anim_pr_session(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'anim_pr.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        p = Project(
            topic="t",
            slug="anim-skip",
            status=ProjectStatus.image_prompts_ready,
            hero_mode="no_hero",
        )
        session.add(p)
        await session.flush()
        session.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="v",
                image_prompt="ip",
                animation_prompt="already filled " * 2,
                status=FrameStatus.image_prompt_ready,
            )
        )
        await session.commit()
        yield session, p
    await engine.dispose()


@pytest.mark.asyncio
async def test_sync_clears_stale_db_when_xlsx_r48_empty(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """Пустой plan R48 → убираем мусорные animation_prompt из БД."""
    from app.settings import settings
    from app.services.animation_prompt_gpt import (
        has_animation_prompt_for_frame,
        scan_missing_animation_prompts,
        sync_animation_prompts_from_xlsx,
    )
    from openpyxl import Workbook

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    data_dir.mkdir(parents=True)
    scenes = data_dir / "scenes"
    scenes.mkdir()
    (scenes / "frame_001_test.png").write_bytes(b"x" * 250_000)

    xlsx = data_dir / "project.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "план"
    wb.save(xlsx)

    fr = (await session.execute(
        __import__("sqlalchemy").select(Frame).where(Frame.project_id == project.id)
    )).scalar_one()

    changed = await sync_animation_prompts_from_xlsx(session, project)
    assert changed >= 1
    assert not (fr.animation_prompt or "").strip()
    assert not has_animation_prompt_for_frame(project, fr)
    missing = scan_missing_animation_prompts(project, [fr])
    assert missing == [1]


@pytest.mark.asyncio
async def test_start_step_anim_pr_skips_when_no_missing_on_disk(
    anim_pr_session, tmp_path: Path, monkeypatch
) -> None:
    """Промты в БД есть, картинок на диске нет — не уходим в generating_animation_prompts."""
    from app.settings import settings

    session, project = anim_pr_session
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    data_dir = tmp_path / "videos" / project.slug
    data_dir.mkdir(parents=True)
    (data_dir / "scenes").mkdir()

    status = await start_step(session, project, "anim_pr")
    assert status is not ProjectStatus.generating_animation_prompts
    assert project.status is not ProjectStatus.generating_animation_prompts
