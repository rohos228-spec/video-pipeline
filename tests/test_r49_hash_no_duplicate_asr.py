"""R15 write в Excel не должен запускать повторный ASR на assemble."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Artifact, ArtifactKind, Base, Frame, Project
from app.services.frame_timeline_sync import (
    _r49_changed_since_whisper,
    _r49_content_hash,
    sync_frame_timestamps_from_voice,
)


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_r49_hash_unchanged_after_r15_xlsx_touch(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    p = Project(id=26, slug="vedm", topic="V")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    audio = p.data_dir / "audio"
    audio.mkdir()
    voice = audio / "voice_full.wav"
    voice.write_bytes(b"\xff" * 100)
    xlsx = p.data_dir / "project.xlsx"
    xlsx.write_bytes(b"xlsx1")
    session.add(p)
    for i in range(1, 4):
        session.add(
            Frame(project_id=26, number=i, voiceover_text=f"текст {i}", status="planned")
        )
    await session.flush()

    cells = [(1, "текст 1"), (2, "текст 2"), (3, "текст 3")]
    r49_hash = _r49_content_hash(cells)
    words_path = audio / "words_test.json"
    words_path.write_text("[]", encoding="utf-8")
    whisper_art = Artifact(
        project_id=26,
        kind=ArtifactKind.whisper_words,
        uuid="w1",
        path=str(words_path),
        meta={"r49_hash": r49_hash},
    )
    session.add(whisper_art)
    await session.commit()

    assert not _r49_changed_since_whisper(whisper_art, cells)

    # Имитация записи R15 — xlsx mtime меняется, R49 текст тот же
    xlsx.write_bytes(b"xlsx2-updated-r15")
    assert not _r49_changed_since_whisper(whisper_art, cells)

    align_called = False

    async def _no_align(*_a, **_k):
        nonlocal align_called
        align_called = True
        raise AssertionError("ASR must not run when R49 unchanged")

    with (
        patch(
            "app.services.frame_timeline_sync.read_plan_voiceover_cells",
            return_value=cells,
        ),
        patch("app.services.frame_timeline_sync.probe_duration", return_value=508.0),
        patch(
            "app.services.frame_timeline_sync.whisper_words_fresh_for_audio",
            return_value=True,
        ),
        patch(
            "app.services.frame_timeline_sync.load_words_json",
            return_value=[__import__("app.services.whisper", fromlist=["WordTS"]).WordTS("a", 0.0, 1.0, 1.0)],
        ),
        patch(
            "app.services.frame_timeline_sync.frame_clips_from_whisper",
            return_value=[
                __import__(
                    "app.services.frame_audio", fromlist=["FrameAudioClip"]
                ).FrameAudioClip(i, voice, "t", float(i - 1), float(i), 1.0)
                for i in range(1, 4)
            ],
        ),
        patch(
            "app.services.frame_timeline_sync.align_existing_voice_full",
            side_effect=_no_align,
        ),
    ):
        info = await sync_frame_timestamps_from_voice(session, p)

    assert not align_called
    assert info.get("source") == "words_json"


@pytest.mark.asyncio
async def test_fresh_words_skips_equal_split_realign(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Proportional R15 метки ~равные — не запускать ASR повторно если words.json свежий."""
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    p = Project(id=26, slug="vedm", topic="V")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    audio = p.data_dir / "audio"
    audio.mkdir()
    voice = audio / "voice_full.wav"
    voice.write_bytes(b"\xff" * 100)
    session.add(p)
    n = 10
    master = 120.0
    step = master / n
    for i in range(1, n + 1):
        session.add(
            Frame(
                project_id=26,
                number=i,
                voiceover_text=f"текст {i}",
                start_ts=round((i - 1) * step, 3),
                end_ts=round(i * step, 3),
                duration_seconds=round(step, 3),
                status="planned",
            )
        )
    await session.flush()

    cells = [(i, f"текст {i}") for i in range(1, n + 1)]
    words_path = audio / "words_test.json"
    words_path.write_text("[]", encoding="utf-8")
    whisper_art = Artifact(
        project_id=26,
        kind=ArtifactKind.whisper_words,
        uuid="w2",
        path=str(words_path),
        meta={"r49_hash": _r49_content_hash(cells)},
    )
    session.add(whisper_art)
    await session.commit()

    equal_clips = [
        __import__("app.services.frame_audio", fromlist=["FrameAudioClip"]).FrameAudioClip(
            i, voice, "t", round((i - 1) * step, 3), round(i * step, 3), round(step, 3)
        )
        for i in range(1, n + 1)
    ]
    align_called = False

    async def _no_align(*_a, **_k):
        nonlocal align_called
        align_called = True
        raise AssertionError("must not realign on equal split when words fresh")

    with (
        patch(
            "app.services.frame_timeline_sync.read_plan_voiceover_cells",
            return_value=cells,
        ),
        patch("app.services.frame_timeline_sync.probe_duration", return_value=master),
        patch(
            "app.services.frame_timeline_sync.whisper_words_fresh_for_audio",
            return_value=True,
        ),
        patch(
            "app.services.frame_timeline_sync.load_words_json",
            return_value=[__import__("app.services.whisper", fromlist=["WordTS"]).WordTS("a", 0.0, 1.0, 1.0)],
        ),
        patch(
            "app.services.frame_timeline_sync.frame_clips_from_whisper",
            return_value=equal_clips,
        ),
        patch(
            "app.services.frame_timeline_sync.align_existing_voice_full",
            side_effect=_no_align,
        ),
    ):
        info = await sync_frame_timestamps_from_voice(session, p)

    assert not align_called
    assert info.get("source") == "words_json"
