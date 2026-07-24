"""Montage board open should not re-parse Excel R15 every time."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.frame_audio import FrameAudioClip
from app.services.frame_timeline_sync import (
    _apply_clips_to_frames,
    sync_frame_timestamps_for_board,
)


def test_apply_clips_skips_unchanged_frames() -> None:
    frames = [
        SimpleNamespace(number=1, start_ts=0.0, end_ts=1.0, duration_seconds=1.0),
        SimpleNamespace(number=2, start_ts=1.0, end_ts=2.0, duration_seconds=1.0),
    ]
    clips = [
        FrameAudioClip(1, path=MagicMock(), text="", start_ts=0.0, end_ts=1.0, duration=1.0),
        FrameAudioClip(2, path=MagicMock(), text="", start_ts=1.5, end_ts=2.5, duration=1.0),
    ]
    updated = _apply_clips_to_frames(frames, clips)
    assert updated == [2]
    assert frames[0].start_ts == 0.0
    assert frames[1].start_ts == 1.5


@pytest.mark.asyncio
async def test_board_sync_skips_r15_on_cache_hit(tmp_path) -> None:
    xlsx = tmp_path / "project.xlsx"
    xlsx.write_bytes(b"PK")
    mtime = xlsx.stat().st_mtime

    frames = [
        SimpleNamespace(number=i, start_ts=float(i), end_ts=float(i) + 1.0, duration_seconds=1.0)
        for i in range(1, 11)
    ]
    project = SimpleNamespace(
        id=26,
        data_dir=tmp_path,
        meta={
            "montage_r15_sync": {
                "xlsx_mtime": mtime,
                "frame_count": 10,
                "parsed": 10,
                "source": "r15",
            }
        },
    )
    session = AsyncMock()

    with (
        patch(
            "app.services.frame_timeline_sync.timeline_frames_and_cells",
            side_effect=AssertionError("timeline must not run on cache hit"),
        ),
        patch(
            "app.storage.plan_sheet_v8.read_plan_timestamps_cells",
            side_effect=AssertionError("R15 must not load on cache hit"),
        ),
    ):
        result = await sync_frame_timestamps_for_board(session, project, frames)

    assert result.get("skipped") == "r15_cache_hit"
    assert result.get("updated") == []
