"""Кадры в монтаже из вручную скопированных scenes/videos."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.ensure_frames_from_disk import (
    discover_frame_numbers_on_disk,
    ensure_frames_from_disk_media,
)
from app.services.montage_board import build_montage_board


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "disk-frames.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.fixture
def project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    p = Project(id=77, slug="disk-import", topic="Disk", hero_mode="auto")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    return p


def test_discover_frame_numbers_from_scenes_and_videos(tmp_path: Path) -> None:
    scenes = tmp_path / "scenes"
    videos = tmp_path / "videos"
    scenes.mkdir()
    videos.mkdir()
    (scenes / "frame_001_abc.png").write_bytes(b"x")
    (scenes / "frame_009_s2_xyz.png").write_bytes(b"x")
    (videos / "clip_009_s2_vid.mp4").write_bytes(b"x")
    (videos / "clip_012_aaa.mp4").write_bytes(b"x")
    assert discover_frame_numbers_on_disk(tmp_path) == {1, 9, 12}


@pytest.mark.asyncio
async def test_ensure_frames_creates_missing_from_disk(
    session: AsyncSession,
    project: Project,
) -> None:
    session.add(project)
    await session.flush()
    scenes = project.data_dir / "scenes"
    videos = project.data_dir / "videos"
    scenes.mkdir()
    videos.mkdir()
    (scenes / "frame_003_x.png").write_bytes(b"png")
    (videos / "clip_003_y.mp4").write_bytes(b"mp4")
    (videos / "clip_005_s2_z.mp4").write_bytes(b"mp4")

    created = await ensure_frames_from_disk_media(session, project)
    assert created == [3, 5]
    rows = (
        await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Frame).where(
                Frame.project_id == project.id
            )
        )
    ).scalars().all()
    by_num = {fr.number: fr for fr in rows}
    assert by_num[3].status.value == "video_generated"
    assert by_num[5].status.value == "video_generated"
    # повторный вызов — без дублей
    assert await ensure_frames_from_disk_media(session, project) == []


@pytest.mark.asyncio
async def test_build_montage_board_bootstraps_from_disk_folders(
    session: AsyncSession,
    project: Project,
) -> None:
    session.add(project)
    await session.flush()
    scenes = project.data_dir / "scenes"
    videos = project.data_dir / "videos"
    scenes.mkdir()
    videos.mkdir()
    (scenes / "frame_002_a.png").write_bytes(b"png")
    (videos / "clip_002_b.mp4").write_bytes(b"mp4")

    board = await build_montage_board(session, project)
    assert board["frame_count"] == 1
    assert board["frames"][0]["number"] == 2
    assert board["frames"][0]["video_shot1_url"] is not None
