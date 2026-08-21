"""Real SQLite DB tests verifying project status never rolls back when videos/images exist."""

from __future__ import annotations

import uuid
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project, ProjectStatus
from app.services.project_state import compute_actual_status


@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_file = tmp_path / "test_state.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_compute_actual_status_preserves_videos_ready_when_videos_exist(db_session) -> None:
    """Ensure project with 10 frames and 10 scene_videos resolves to videos_ready, even if 1 prompt is missing."""
    project = Project(
        topic="Космос 2050",
        slug="space-2050",
        script_text="Длинный закадровый текст для видео",
        general_plan="План" * 100,
        status=ProjectStatus.videos_ready,
        meta={},
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    for i in range(1, 11):
        f = Frame(
            project_id=project.id,
            number=i,
            voiceover_text=f"Кадр {i}",
            image_prompt=f"Промпт {i}" if i < 10 else None,  # frame 10 prompt is missing
            animation_prompt=f"Движение {i}",
        )
        db_session.add(f)
        await db_session.flush()

        art = Artifact(
            project_id=project.id,
            frame_id=f.id,
            kind=ArtifactKind.scene_video,
            uuid=str(uuid.uuid4()),
            path=f"/fake/video_{i}.mp4",
        )
        db_session.add(art)

        img_art = Artifact(
            project_id=project.id,
            frame_id=f.id,
            kind=ArtifactKind.scene_image,
            uuid=str(uuid.uuid4()),
            path=f"/fake/image_{i}.png",
        )
        db_session.add(img_art)

    await db_session.commit()

    # compute_actual_status must compute videos_ready because all 10 videos exist!
    status = await compute_actual_status(db_session, project)
    assert status == ProjectStatus.videos_ready


@pytest.mark.asyncio
async def test_compute_actual_status_preserves_images_ready_when_images_exist(db_session) -> None:
    """Ensure project with completed images resolves to images_ready."""
    project = Project(
        topic="Город 2050",
        slug="city-2050",
        script_text="Сценарий города будущего",
        general_plan="План" * 100,
        status=ProjectStatus.images_ready,
        meta={},
    )
    db_session.add(project)
    await db_session.commit()
    await db_session.refresh(project)

    for i in range(1, 6):
        f = Frame(
            project_id=project.id,
            number=i,
            voiceover_text=f"Кадр {i}",
            image_prompt=f"Промпт {i}",
        )
        db_session.add(f)
        await db_session.flush()

        img_art = Artifact(
            project_id=project.id,
            frame_id=f.id,
            kind=ArtifactKind.scene_image,
            uuid=str(uuid.uuid4()),
            path=f"/fake/image_{i}.png",
        )
        db_session.add(img_art)

    await db_session.commit()

    status = await compute_actual_status(db_session, project)
    assert status == ProjectStatus.images_ready