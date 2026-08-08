"""Таблица asr_words — word-level транскрибация в БД."""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import AsrWord, Base, Frame, FrameStatus, Project, ProjectStatus
from app.services.asr_words_store import (
    asr_words_to_dicts,
    load_project_asr_words,
    replace_project_asr_words,
)
from app.services.whisper import WordTS


@pytest_asyncio.fixture
async def session(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 't.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        p = Project(
            slug="asr-words-test",
            topic="t",
            status=ProjectStatus.generating_audio,
        )
        s.add(p)
        await s.flush()
        s.add(
            Frame(
                project_id=p.id,
                number=1,
                voiceover_text="привет мир",
                status=FrameStatus.planned,
                start_ts=0.0,
                end_ts=1.5,
            )
        )
        s.add(
            Frame(
                project_id=p.id,
                number=2,
                voiceover_text="второй",
                status=FrameStatus.planned,
                start_ts=1.5,
                end_ts=3.0,
            )
        )
        await s.commit()
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_replace_project_asr_words_assigns_frames(session) -> None:
    p = (await session.execute(select(Project))).scalar_one()
    words = [
        WordTS(word="привет", start=0.0, end=0.5, prob=0.9),
        WordTS(word="мир", start=0.5, end=1.2, prob=0.8),
        WordTS(word="второй", start=1.6, end=2.4, prob=0.7),
    ]
    segments = [
        {"frame_number": 1, "start_ts": 0.0, "end_ts": 1.5},
        {"frame_number": 2, "start_ts": 1.5, "end_ts": 3.0},
    ]
    rid = await replace_project_asr_words(
        session,
        p.id,
        words,
        backend="nvidia",
        artifact_uuid="abc",
        frame_segments=segments,
    )
    await session.commit()

    rows = await load_project_asr_words(session, p.id)
    assert len(rows) == 3
    assert all(r.run_uuid == rid for r in rows)
    assert rows[0].frame_number == 1
    assert rows[2].frame_number == 2
    assert rows[0].frame_id is not None
    assert rows[0].backend == "nvidia"

    # Replace clears previous run
    await replace_project_asr_words(
        session, p.id, [WordTS("только", 0.0, 0.3)], backend="whisper"
    )
    await session.commit()
    rows2 = await load_project_asr_words(session, p.id)
    assert len(rows2) == 1
    assert rows2[0].word == "только"
    assert (await session.execute(select(AsrWord))).scalars().all().__len__() == 1

    d = asr_words_to_dicts(rows2)
    assert d[0]["word"] == "только"
    assert d[0]["start"] == 0.0
