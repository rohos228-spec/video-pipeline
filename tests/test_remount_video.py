"""Перемонтаж видео при сбитой синхронизации озвучки и кадров."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.services.remount_video import remount_video

_VOICEOVER = "текст закадрового для кадра один " * 6


@pytest.fixture
async def session(tmp_path, monkeypatch) -> AsyncSession:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_remount_calls_audio_and_assemble(
    session: AsyncSession, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    p = Project(
        id=5,
        slug="pochemu-idet-dozhd",
        topic="почему идет дождь",
        status=ProjectStatus.assembled,
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    voice = p.data_dir / "audio" / "voice_full_test.mp3"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"\xff" * 4096)
    session.add(
        Frame(
            project_id=p.id,
            number=1,
            voiceover_text=_VOICEOVER,
            meaning="",
            image_prompt="",
            animation_prompt="",
            duration_seconds=3.0,
            start_ts=0,
            end_ts=3,
        )
    )
    await session.commit()

    audio_run = AsyncMock(side_effect=lambda s, proj, b, **kw: setattr(proj, "status", ProjectStatus.audio_ready))
    assemble_run = AsyncMock(side_effect=lambda s, proj, b: setattr(proj, "status", ProjectStatus.assembled))

    with (
        patch(
            "app.services.remount_video.sync_project_xlsx",
            new_callable=AsyncMock,
            return_value={"frames_updated": 1},
        ),
        patch("app.orchestrator.steps.generate_audio.run", audio_run),
        patch("app.orchestrator.steps.assemble.run", assemble_run),
    ):
        result = await remount_video(session, p, run_assemble=True)

    assert result.get("voice_file")
    audio_run.assert_awaited_once()
    assemble_run.assert_awaited_once()
    assert result.get("done") is True
    assert result.get("final_status") == "assembled"


@pytest.mark.asyncio
async def test_remount_bootstraps_frames_from_disk(
    session: AsyncSession, tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    from app import settings as app_settings

    monkeypatch.setattr(app_settings.settings, "data_dir", tmp_path / "data")

    p = Project(
        id=6,
        slug="manual-import",
        topic="ручной импорт",
        status=ProjectStatus.music_ready,
    )
    session.add(p)
    await session.flush()
    p.data_dir.mkdir(parents=True, exist_ok=True)
    videos = p.data_dir / "videos"
    videos.mkdir()
    (videos / "clip_001_x.mp4").write_bytes(b"mp4")
    voice = p.data_dir / "audio" / "voice_full_test.mp3"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"\xff" * 4096)
    await session.commit()

    audio_run = AsyncMock(side_effect=lambda s, proj, b, **kw: setattr(proj, "status", ProjectStatus.audio_ready))
    assemble_run = AsyncMock(side_effect=lambda s, proj, b: setattr(proj, "status", ProjectStatus.assembled))

    with (
        patch("app.orchestrator.steps.generate_audio.run", audio_run),
        patch("app.orchestrator.steps.assemble.run", assemble_run),
    ):
        result = await remount_video(session, p, run_assemble=True)

    assert result.get("disk_bootstrap", {}).get("frames_created") == [1]
    assert result.get("voice_file")
    assert not result.get("error")
    audio_run.assert_awaited_once()
    assemble_run.assert_awaited_once()
