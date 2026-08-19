"""Unit tests verifying scene images and videos are backed up before any wipe."""

from __future__ import annotations

import uuid
import pytest
import pytest_asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.artifact_recovery import restore_scene_images_from_old
from app.services.reset_step import (
    _BACKUP_ON_WIPE_KINDS,
    _backup_artifact_file_before_wipe,
    _wipe_artifacts_by_kind,
    _wipe_split,
)


@pytest_asyncio.fixture
async def db_session(tmp_path):
    db_file = tmp_path / "test_backup.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


def test_backup_kinds_includes_scene_media() -> None:
    """Ensure scene_image and scene_video are protected in _BACKUP_ON_WIPE_KINDS."""
    assert ArtifactKind.scene_image in _BACKUP_ON_WIPE_KINDS
    assert ArtifactKind.scene_video in _BACKUP_ON_WIPE_KINDS


@pytest.mark.asyncio
async def test_wipe_artifacts_backs_up_scene_images(db_session) -> None:
    """Ensure wiping scene_image artifacts copies them to old/scenes/ first."""
    project = Project(
        id=1,
        topic="Тест бэкапа",
        slug="test-backup",
        meta={},
    )
    db_session.add(project)
    await db_session.commit()

    scenes_dir = project.data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    test_png = scenes_dir / "frame_001_c1234567.png"
    test_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 1000)

    art = Artifact(
        project_id=project.id,
        kind=ArtifactKind.scene_image,
        uuid=str(uuid.uuid4()),
        path=str(test_png),
    )
    db_session.add(art)
    await db_session.commit()

    # Perform wipe
    res = await _wipe_artifacts_by_kind(db_session, project, ArtifactKind.scene_image)
    await db_session.commit()

    assert res["artifacts"] == 1
    assert res["files"] == 1

    # Verify backup exists in old/scenes/
    old_scenes = project.data_dir / "old" / "scenes"
    assert old_scenes.is_dir()
    backed_files = list(old_scenes.glob("*frame_001_c1234567.png"))
    assert len(backed_files) >= 1


@pytest.mark.asyncio
async def test_wipe_split_backs_up_frame_images(db_session) -> None:
    """Ensure _wipe_split backs up frame artifact images before delete."""
    project = Project(
        id=2,
        topic="Тест сплит бэкапа",
        slug="test-split-backup",
        meta={},
    )
    db_session.add(project)
    await db_session.commit()

    scenes_dir = project.data_dir / "scenes"
    scenes_dir.mkdir(parents=True, exist_ok=True)

    test_png = scenes_dir / "frame_002_a9876543.png"
    test_png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000)

    f = Frame(
        project_id=project.id,
        number=2,
        voiceover_text="Кадр 2",
    )
    db_session.add(f)
    await db_session.flush()

    art = Artifact(
        project_id=project.id,
        frame_id=f.id,
        kind=ArtifactKind.scene_image,
        uuid=str(uuid.uuid4()),
        path=str(test_png),
    )
    db_session.add(art)
    await db_session.commit()

    # Perform wipe split
    await _wipe_split(db_session, project)
    await db_session.commit()

    # Verify backup in old/scenes/
    old_scenes = project.data_dir / "old" / "scenes"
    assert old_scenes.is_dir()
    backed_files = list(old_scenes.glob("*frame_002_a9876543.png"))
    assert len(backed_files) >= 1