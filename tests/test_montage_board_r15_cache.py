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
    mtime = round(xlsx.stat().st_mtime, 3)

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


@pytest.mark.asyncio
async def test_board_sync_keeps_db_timestamps_without_cache(tmp_path) -> None:
    """Не переписывать 199 кадров на каждый GET, если шкала в БД уже есть."""
    xlsx = tmp_path / "project.xlsx"
    xlsx.write_bytes(b"PK")
    frames = [
        SimpleNamespace(number=i, start_ts=float(i), end_ts=float(i) + 1.0, duration_seconds=1.0)
        for i in range(1, 11)
    ]
    project = SimpleNamespace(id=53, data_dir=tmp_path, meta={})
    session = AsyncMock()

    with (
        patch(
            "app.services.frame_timeline_sync.timeline_frames_and_cells",
            side_effect=AssertionError("must keep DB without reading timeline"),
        ),
        patch(
            "app.storage.plan_sheet_v8.read_plan_timestamps_cells",
            side_effect=AssertionError("R15 must not load when DB ts ok"),
        ),
    ):
        result = await sync_frame_timestamps_for_board(session, project, frames)

    assert result.get("skipped") == "db_timestamps_ok"
    assert result.get("updated") == []
    # meta пишется в фоне отдельной сессией — request session не flush'ит
    session.flush.assert_not_awaited()
    from app.services.frame_timeline_sync import _r15_memory_hit, _xlsx_mtime_key

    assert _r15_memory_hit(53, _xlsx_mtime_key(xlsx.stat().st_mtime), 10)


@pytest.mark.asyncio
async def test_board_sync_rejects_r15_drift_when_xlsx_changes(tmp_path) -> None:
    xlsx = tmp_path / "project.xlsx"
    xlsx.write_bytes(b"PK")
    mtime = round(xlsx.stat().st_mtime, 3)
    frames = [
        SimpleNamespace(number=i, start_ts=float(i), end_ts=float(i) + 1.0, duration_seconds=1.0)
        for i in range(1, 11)
    ]
    # Старый кэш с другим mtime → путь «xlsx changed»
    project = SimpleNamespace(
        id=53,
        data_dir=tmp_path,
        meta={
            "montage_r15_sync": {
                "xlsx_mtime": mtime - 10.0,
                "frame_count": 10,
                "parsed": 10,
                "source": "db_keep",
            }
        },
    )
    session = AsyncMock()
    voice = tmp_path / "audio" / "voice_full.wav"
    voice.parent.mkdir(parents=True, exist_ok=True)
    voice.write_bytes(b"RIFF")

    # R15 далеко от DB (дрейф) — реальный parse_timecode_range
    ts_cells = [(i, f"0:{i * 10:02d}.00-0:{i * 10 + 5:02d}.00") for i in range(1, 11)]

    with (
        patch(
            "app.services.frame_timeline_sync.timeline_frames_and_cells",
            return_value=(frames, [(i, f"vo{i}") for i in range(1, 11)]),
        ),
        patch(
            "app.services.frame_timeline_sync.find_voice_full_on_disk",
            return_value=voice,
        ),
        patch(
            "app.services.frame_timeline_sync.probe_duration",
            new=AsyncMock(return_value=100.0),
        ),
        patch(
            "app.storage.plan_sheet_v8.read_plan_timestamps_cells",
            return_value=(ts_cells, 15),
        ),
    ):
        result = await sync_frame_timestamps_for_board(session, project, frames)

    assert result.get("skipped") == "r15_drift"
    assert frames[0].start_ts == 1.0  # ORM не трогали
    from app.services.frame_timeline_sync import _R15_MEMORY

    assert _R15_MEMORY.get(53, (None, None, None))[2] == "r15_rejected_drift"
