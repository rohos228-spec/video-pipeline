"""validate_after_audio принимает voice_full.wav / disk_whisper без frame_*.mp3."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project, ProjectStatus
from app.services.post_step_validate import validate_after_audio
from app.services.step_data_guard import ready_status_confirmed_by_data


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    db_path = tmp_path / "audio-val.db"
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
    p = Project(
        id=49,
        slug="dyatlov",
        topic="Test",
        hero_mode="auto",
        status=ProjectStatus.generating_audio,
    )
    p.data_dir.mkdir(parents=True, exist_ok=True)
    (p.data_dir / "audio").mkdir(parents=True, exist_ok=True)
    return p


@pytest.mark.asyncio
async def test_validate_audio_accepts_voice_full_wav_without_frame_mp3(
    session: AsyncSession,
    project: Project,
) -> None:
    session.add(project)
    session.add(Frame(project_id=49, number=1, voiceover_text="a", status="planned"))
    session.add(Frame(project_id=49, number=2, voiceover_text="b", status="planned"))
    voice = project.data_dir / "audio" / "voice_full.wav"
    voice.write_bytes(b"RIFF....WAVE")
    session.add(
        Artifact(
            project_id=49,
            kind=ArtifactKind.audio,
            uuid="a1",
            path=str(voice),
            meta={"mode": "disk_whisper", "source": "disk_whisper"},
        )
    )
    await session.flush()

    result = await validate_after_audio(session, project)
    assert result.ok is True
    assert result.messages == []


@pytest.mark.asyncio
async def test_audio_ready_not_rolled_back_when_voice_on_disk(
    session: AsyncSession,
    project: Project,
) -> None:
    """Даже если scene_image неполные — voice_full подтверждает audio_ready."""
    project.status = ProjectStatus.audio_ready
    session.add(project)
    session.add(
        Frame(
            project_id=49,
            number=1,
            voiceover_text="a",
            image_prompt="p",
            status="planned",
        )
    )
    voice = project.data_dir / "audio" / "voice_full.wav"
    voice.write_bytes(b"wav")
    await session.flush()

    assert await ready_status_confirmed_by_data(
        session, project, ProjectStatus.audio_ready
    )
