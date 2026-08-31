"""Тесты режимов запуска шагов: mode="full" vs mode="resume"."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (
    Artifact,
    ArtifactKind,
    Base,
    Frame,
    FrameStatus,
    Project,
    ProjectStatus,
)
from app.services.project_steps import start_step


@pytest_asyncio.fixture
async def session(tmp_path: Path):
    db_url = f"sqlite+aiosqlite:///{tmp_path / 't_modes.db'}"
    engine = create_async_engine(db_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
    async with SessionLocal() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_start_step_full_mode_wipes_previous_images(session, tmp_path: Path):
    """mode='full' (по умолчанию) полностью стирает картинки для чистого перезапуска."""
    data_dir = tmp_path / "p1"
    scenes_dir = data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    img_file = scenes_dir / "scene_001.png"
    img_file.write_bytes(b"dummy png data")

    p = Project(
        slug="p1",
        topic="Topic",
        status=ProjectStatus.images_ready,
    )
    session.add(p)
    await session.flush()

    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="Voiceover for frame 1",
        image_prompt="A futuristic city",
        status=FrameStatus.image_generated,
    )
    session.add(fr)
    await session.flush()

    art = Artifact(
        project_id=p.id,
        frame_id=fr.id,
        uuid="art_uuid_1",
        kind=ArtifactKind.scene_image,
        path=str(img_file),
    )
    session.add(art)
    await session.commit()

    # Запускаем шаг "img" с force_wipe=True (mode="full")
    await start_step(session, p, "img", explicit_ui_start=True, force_wipe=True)
    await session.commit()

    # Проверяем, что артефакт удален, а статус кадра сброшен в image_prompt_ready для чистой генерации
    remaining_arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.scene_image,
            )
        )
    ).scalars().all()
    assert len(remaining_arts) == 0

    await session.refresh(fr)
    assert fr.status == FrameStatus.image_prompt_ready


@pytest.mark.asyncio
async def test_start_step_resume_mode_preserves_existing_images(session, tmp_path: Path):
    """mode='resume' (force_wipe=False) сохраняет готовые картинки для доделки."""
    data_dir = tmp_path / "p2"
    scenes_dir = data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)
    img_file = scenes_dir / "scene_001.png"
    img_file.write_bytes(b"dummy png data")

    p = Project(
        slug="p2",
        topic="Topic",
        status=ProjectStatus.generating_images,
    )
    session.add(p)
    await session.flush()

    fr = Frame(
        project_id=p.id,
        number=1,
        voiceover_text="Voiceover for frame 1",
        image_prompt="A futuristic city",
        status=FrameStatus.image_generated,
    )
    session.add(fr)
    await session.flush()

    art = Artifact(
        project_id=p.id,
        frame_id=fr.id,
        uuid="art_uuid_1",
        kind=ArtifactKind.scene_image,
        path=str(img_file),
    )
    session.add(art)
    await session.commit()

    # Запускаем шаг "img" с force_wipe=False (mode="resume")
    await start_step(session, p, "img", explicit_ui_start=True, force_wipe=False)
    await session.commit()

    # Проверяем, что готовый артефакт сохранился
    remaining_arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == p.id,
                Artifact.kind == ArtifactKind.scene_image,
            )
        )
    ).scalars().all()
    assert len(remaining_arts) == 1
    assert img_file.exists()
