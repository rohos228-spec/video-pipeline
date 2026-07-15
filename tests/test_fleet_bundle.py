"""Fleet bundle export — any project status."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.fleet import bundle as bundle_svc
from app.models import Base, Project, ProjectStatus


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "fleet.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_bundle_any_status(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    project = Project(slug="early-export", topic="test", status=ProjectStatus.videos_ready)
    session.add(project)
    await session.flush()
    data_dir = project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "clip.mp4").write_bytes(b"fake-video")
    (data_dir / "notes.txt").write_text("draft", encoding="utf-8")

    blob, filename = await bundle_svc.export_project_bundle(session, project.id)

    assert filename == "early-export-fleet-bundle.tar.gz"
    assert len(blob) > 100


@pytest.mark.asyncio
async def test_import_bundle_without_montage_queue(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir(parents=True)
    monkeypatch.setattr("app.settings.settings.data_dir", str(data_root))
    source = Project(slug="import-me", topic="src", status=ProjectStatus.images_ready)
    session.add(source)
    await session.flush()
    data_dir = source.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "frame.png").write_bytes(b"\x89PNG")

    blob, _ = await bundle_svc.export_project_bundle(session, source.id)
    imported = await bundle_svc.import_project_bundle(session, blob, run_assemble=False)
    await session.flush()

    assert imported.slug == "import-me"
    assert (imported.data_dir / "frame.png").exists()
