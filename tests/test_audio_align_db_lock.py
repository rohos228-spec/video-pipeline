"""Regression: audio_align не держит DB на speech; R15 важнее frames flush."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.audio_align_run import (
    _is_sqlite_locked,
    run_audio_align_for_project,
)
from app.services.mapper import FrameTiming
from app.services.whisper import WordTS


def test_sqlite_locked_detector() -> None:
    assert _is_sqlite_locked(RuntimeError("database is locked"))
    assert _is_sqlite_locked(RuntimeError("(sqlite3.OperationalError) database is busy"))
    assert not _is_sqlite_locked(RuntimeError("no such table"))


@pytest.mark.asyncio
async def test_r15_written_even_if_db_locked(tmp_path: Path) -> None:
    voice = tmp_path / "voice.wav"
    voice.write_bytes(b"RIFF....")

    cells = [(1, "раз"), (2, "два")]
    words = [
        WordTS("раз", 0.0, 0.5, 1.0),
        WordTS("два", 0.5, 1.0, 1.0),
    ]
    timings = [
        FrameTiming(1, 0.0, 0.5, 0.5),
        FrameTiming(2, 0.5, 1.0, 0.5),
    ]

    speech = type("Speech", (), {})()
    speech.words = words
    speech.timings = timings
    speech.speech_source = "nemo"

    project = MagicMock()
    project.id = 26
    project.data_dir = tmp_path
    project.slug = "t"
    project.meta = {}

    load_inputs = {
        "frame_numbers": [1, 2],
        "cells": cells,
        "voice_path": voice,
        "cached_words": None,
        "data_dir": tmp_path,
        "slug": "t",
    }

    session_cm = MagicMock()
    session = AsyncMock()
    session.get = AsyncMock(return_value=project)
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)

    persist_calls = {"n": 0}

    async def persist_boom(*_a, **_k):
        persist_calls["n"] += 1
        raise RuntimeError("database is locked")

    with (
        patch("app.services.audio_align_run.session_scope", return_value=session_cm),
        patch(
            "app.services.audio_align_run._load_align_inputs",
            AsyncMock(return_value=load_inputs),
        ),
        patch("app.services.audio_align_run.probe_duration", AsyncMock(return_value=1.0)),
        patch(
            "app.services.audio_align_run.run_speech_align",
            return_value=speech,
        ),
        patch(
            "app.services.plan_timestamps.write_asr_timestamps_to_r15",
            return_value=2,
        ) as write_r15,
        patch(
            "app.services.audio_align_run._persist_align_db_with_retry",
            side_effect=persist_boom,
        ),
    ):
        summary = await run_audio_align_for_project(
            26, method="nemo_direct", force_asr=False, run_assemble=False
        )

    assert summary["r15_written"] == 2
    assert summary.get("done") is True
    assert "error" not in summary or not summary.get("error")
    assert "database is locked" in (summary.get("db_frames_error") or "")
    write_r15.assert_called_once()
    assert persist_calls["n"] == 1
