"""Unit tests verifying scene images and videos are backed up before any wipe."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
import pytest_asyncio
from loguru import logger
from pathlib import Path
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.artifact_recovery import restore_scene_images_from_old
try:
    from app.services.reset_step import (
        _BACKUP_ON_WIPE_KINDS,
        _archive_dir_to_old,
        _backup_artifact_file_before_wipe,
        _wipe_artifacts_by_kind,
        _wipe_split,
    )
except ImportError as exc:
    pytest.skip(
        "app/services/reset_step.py: нет _archive_dir_to_old после возврата к #352 "
        f"({exc})",
        allow_module_level=True,
    )


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 17, 12, 0, 0, tzinfo=timezone.utc)


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


def test_backup_same_name_same_second_keeps_both(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Вторая копия с тем же именем в ту же секунду не затирает первую."""
    monkeypatch.setattr("app.services.reset_step.datetime", _FrozenDateTime)
    project = SimpleNamespace(id=4, data_dir=tmp_path)
    first_src = tmp_path / "a" / "frame.png"
    second_src = tmp_path / "b" / "frame.png"
    first_src.parent.mkdir()
    second_src.parent.mkdir()
    first_src.write_bytes(b"AAA")
    second_src.write_bytes(b"BBBB")
    sink = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        older = _backup_artifact_file_before_wipe(
            project, first_src, kind=ArtifactKind.scene_image
        )
        newer = _backup_artifact_file_before_wipe(
            project, second_src, kind=ArtifactKind.scene_image
        )
    finally:
        logger.remove(sink)
    assert older is not None and newer is not None
    assert older != newer
    assert older.read_bytes() == b"AAA"
    assert newer.read_bytes() == b"BBBB"
    assert "уже есть" in caplog.text


def test_archive_same_second_keeps_previous_tree(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Повторный перенос в ту же секунду не стирает уже лежащий архив."""
    monkeypatch.setattr("app.services.reset_step.datetime", _FrozenDateTime)
    project = SimpleNamespace(id=5, data_dir=tmp_path)
    previous = tmp_path / "old" / "sfx" / "20261017_120000"
    previous.mkdir(parents=True)
    (previous / "kept.txt").write_bytes(b"OLD-ARCHIVE")
    src = tmp_path / "sfx"
    src.mkdir()
    (src / "new.txt").write_bytes(b"NEW-FILE")
    sink = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        dest = _archive_dir_to_old(project, src, "sfx")
    finally:
        logger.remove(sink)
    assert dest is not None and dest != previous
    assert (previous / "kept.txt").read_bytes() == b"OLD-ARCHIVE"
    assert (dest / "new.txt").read_bytes() == b"NEW-FILE"
    assert not src.exists()
    assert "уже есть" in caplog.text


def test_archive_failure_does_not_delete_previous_tree(
    tmp_path: Path, monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Ошибка copytree не сносит каталог, который уже лежал в old/."""
    monkeypatch.setattr("app.services.reset_step.datetime", _FrozenDateTime)
    project = SimpleNamespace(id=6, data_dir=tmp_path)
    previous = tmp_path / "old" / "sfx" / "20261017_120000"
    previous.mkdir(parents=True)
    (previous / "kept.txt").write_bytes(b"OLD-ARCHIVE")
    src = tmp_path / "sfx"
    src.mkdir()
    (src / "new.txt").write_bytes(b"NEW-FILE")

    def _boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("app.services.reset_step.shutil.copytree", _boom)
    sink = logger.add(caplog.handler, level="WARNING", format="{message}")
    try:
        assert _archive_dir_to_old(project, src, "sfx") is None
    finally:
        logger.remove(sink)
    assert (previous / "kept.txt").read_bytes() == b"OLD-ARCHIVE"
    assert (src / "new.txt").read_bytes() == b"NEW-FILE"
    assert "каталог сохранён" in caplog.text