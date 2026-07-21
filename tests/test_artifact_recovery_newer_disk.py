"""Recovery / assemble: newer-on-disk должен побеждать stale Artifact."""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project
from app.services.artifact_recovery import (
    newest_disk_video,
    recover_scene_videos_from_disk,
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "recover.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_recover_video_rebinds_when_disk_newer(
    tmp_path: Path,
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(id=7, slug="rec", topic="t", hero_mode="auto")
    fr = Frame(project_id=7, number=1, voiceover_text="x", status="planned")
    session.add(project)
    session.add(fr)
    await session.flush()

    videos = project.data_dir / "videos"
    videos.mkdir(parents=True)
    old = videos / "clip_001_old.mp4"
    new = videos / "clip_001_new.mp4"
    old.write_bytes(b"0" * 90_000)
    new.write_bytes(b"1" * 90_000)
    # Явные mtime: new строго новее old.
    now = time.time()
    os_utime = __import__("os").utime
    os_utime(old, (now - 10, now - 10))
    os_utime(new, (now, now))

    session.add(
        Artifact(
            project_id=7,
            frame_id=fr.id,
            kind=ArtifactKind.scene_video,
            uuid="oldart",
            path=str(old.resolve()),
            meta={"shot": 1},
        )
    )
    await session.flush()

    recovered = await recover_scene_videos_from_disk(session, project)
    assert 1 in recovered
    await session.flush()

    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == 7,
                Artifact.kind == ArtifactKind.scene_video,
            )
        )
    ).scalars().all()
    assert len(arts) == 1
    assert Path(arts[0].path).name == "clip_001_new.mp4"


def test_newest_disk_video_prefers_mtime(tmp_path: Path) -> None:
    d = tmp_path / "videos"
    d.mkdir()
    a = d / "clip_002_a.mp4"
    b = d / "clip_002_b.mp4"
    a.write_bytes(b"a" * 1000)
    time.sleep(0.05)
    b.write_bytes(b"b" * 1000)
    assert newest_disk_video(d, 2, 1) == b
