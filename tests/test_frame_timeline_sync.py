"""frame_timeline_sync: таймкоды из whisper + R49."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project
from app.services.frame_timeline_sync import (
    clips_look_equal_split,
    is_placeholder_voiceover,
    sync_frame_timestamps_from_voice,
    timeline_frames_and_cells,
)
from app.services.frame_audio import FrameAudioClip
from app.services.whisper import WordTS


@pytest.fixture
async def session(tmp_path: Path) -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def test_is_placeholder_voiceover() -> None:
    assert is_placeholder_voiceover("Кадр 1")
    assert is_placeholder_voiceover("кадр 42.")
    assert not is_placeholder_voiceover("История ведьм началась")


def test_clips_look_equal_split_detects_uniform_fallback() -> None:
    master = 120.0
    n = 10
    step = master / n
    clips = [
        FrameAudioClip(
            frame_number=i + 1,
            path=Path("voice.wav"),
            text="x",
            start_ts=round(i * step, 3),
            end_ts=round((i + 1) * step, 3),
            duration=round(step, 3),
        )
        for i in range(n)
    ]
    assert clips_look_equal_split(clips, master)


def test_clips_look_equal_split_ignores_varied_durations() -> None:
    clips = [
        FrameAudioClip(1, Path("v"), "a", 0.0, 1.0, 1.0),
        FrameAudioClip(2, Path("v"), "b", 1.0, 4.0, 3.0),
        FrameAudioClip(3, Path("v"), "c", 4.0, 5.0, 1.0),
        FrameAudioClip(4, Path("v"), "d", 5.0, 10.0, 5.0),
    ]
    assert not clips_look_equal_split(clips, 10.0)


@pytest.mark.asyncio
async def test_sync_retries_whisper_on_equal_split_from_words_json(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    p = Project(id=3, slug="eq", topic="Eq")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    audio = p.data_dir / "audio"
    audio.mkdir()
    voice = audio / "voice_full.wav"
    voice.write_bytes(b"\xff" * 100)
    session.add(p)
    frames = [
        Frame(project_id=3, number=i, voiceover_text=f"слово{i}", status="planned")
        for i in range(1, 6)
    ]
    for fr in frames:
        session.add(fr)
    await session.flush()

    words_path = audio / "words_old.json"
    words_path.write_text("[]", encoding="utf-8")
    from app.models import Artifact, ArtifactKind

    session.add(
        Artifact(
            project_id=3,
            kind=ArtifactKind.whisper_words,
            uuid="w-old",
            path=str(words_path),
        )
    )
    await session.commit()

    realigned = [
        FrameAudioClip(i, voice, f"t{i}", float(i - 1), float(i), 1.0)
        for i in range(1, 6)
    ]
    equal_fallback = list(realigned)

    async def _fake_align(*_a, **_k):
        return realigned, voice, [WordTS("x", 0.0, 5.0, 1.0)]

    with (
        patch(
            "app.services.frame_timeline_sync.read_plan_voiceover_cells",
            return_value=[(i, f"слово{i}") for i in range(1, 6)],
        ),
        patch("app.services.frame_timeline_sync.probe_duration", return_value=5.0),
        patch(
            "app.services.frame_timeline_sync.whisper_words_fresh_for_audio",
            return_value=True,
        ),
        patch(
            "app.services.frame_timeline_sync.frame_clips_from_whisper",
            return_value=equal_fallback,
        ),
        patch(
            "app.services.frame_timeline_sync.align_existing_voice_full",
            side_effect=_fake_align,
        ),
        patch(
            "app.services.frame_timeline_sync._persist_whisper_words",
            return_value=audio / "words_new.json",
        ),
    ):
        info = await sync_frame_timestamps_from_voice(session, p)

    assert "whisper_realigned" in str(info.get("source"))
    await session.refresh(frames[0])
    assert frames[0].start_ts == 0.0
    assert frames[0].end_ts == 1.0


@pytest.mark.asyncio
async def test_timeline_frames_skips_disk_placeholders(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    p = Project(id=1, slug="t", topic="T")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    session.add(p)
    await session.flush()
    session.add(
        Frame(project_id=1, number=1, voiceover_text="Кадр 1", status="planned")
    )
    session.add(
        Frame(
            project_id=1,
            number=2,
            voiceover_text="Реальный текст кадра два",
            status="planned",
        )
    )
    await session.flush()

    with patch(
        "app.services.frame_timeline_sync.read_plan_voiceover_cells",
        return_value=[(1, ""), (2, "Реальный текст кадра два")],
    ):
        timeline, cells = timeline_frames_and_cells(p, list((await session.execute(
            __import__("sqlalchemy", fromlist=["select"]).select(Frame)
        )).scalars().all()))

    assert [fr.number for fr in timeline] == [2]
    assert cells == [(2, "Реальный текст кадра два")]


@pytest.mark.asyncio
async def test_sync_from_existing_words_json(
    session: AsyncSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.settings.settings.data_dir", tmp_path / "data")
    p = Project(id=2, slug="sync", topic="Sync")
    p.data_dir.mkdir(parents=True, exist_ok=True)
    audio = p.data_dir / "audio"
    audio.mkdir()
    voice = audio / "voice_full.wav"
    voice.write_bytes(b"\xff" * 100)
    session.add(p)
    await session.flush()
    fr = Frame(
        project_id=2,
        number=1,
        voiceover_text="Привет мир",
        status="planned",
    )
    session.add(fr)
    await session.flush()

    words = [
        WordTS(word="привет", start=0.0, end=0.5, prob=0.9),
        WordTS(word="мир", start=0.5, end=1.0, prob=0.9),
    ]
    words_path = audio / "words_test.json"
    words_path.write_text(
        '[{"word":"привет","start":0.0,"end":0.5,"prob":0.9},'
        '{"word":"мир","start":0.5,"end":1.0,"prob":0.9}]',
        encoding="utf-8",
    )

    from app.models import Artifact, ArtifactKind

    session.add(
        Artifact(
            project_id=2,
            kind=ArtifactKind.whisper_words,
            uuid="w1",
            path=str(words_path),
        )
    )
    await session.commit()

    with (
        patch(
            "app.services.frame_timeline_sync.read_plan_voiceover_cells",
            return_value=[(1, "Привет мир")],
        ),
        patch(
            "app.services.frame_timeline_sync.probe_duration",
            return_value=1.0,
        ),
        patch(
            "app.services.frame_timeline_sync.whisper_words_fresh_for_audio",
            return_value=True,
        ),
    ):
        info = await sync_frame_timestamps_from_voice(session, p)

    assert info.get("updated") == [1]
    await session.refresh(fr)
    assert fr.start_ts == 0.0
    assert fr.end_ts == 1.0
