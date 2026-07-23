"""Сборка: align по полному R49, выход только кадры с видео."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.services.frame_audio import build_assembly_timeline
from app.services.whisper import WordTS


@pytest.mark.asyncio
async def test_build_assembly_timeline_aligns_full_script_filters_output(tmp_path: Path) -> None:
    voice = tmp_path / "voice_full.wav"
    voice.write_bytes(b"x")

    # 5 кадров в сценарии, видео только у 1,3,5 — кадр 2 без клипа
    align_cells = [
        (1, "один"),
        (2, "два пропущенный"),
        (3, "три"),
        (4, "четыре пропущенный"),
        (5, "пять"),
    ]
    output_cells = [(1, "один"), (3, "три"), (5, "пять")]
    words = [
        WordTS("один", 0.0, 1.0, 1.0),
        WordTS("два", 1.0, 2.0, 1.0),
        WordTS("пропущенный", 2.0, 3.0, 1.0),
        WordTS("три", 3.0, 4.0, 1.0),
        WordTS("четыре", 4.0, 5.0, 1.0),
        WordTS("пропущенный", 5.0, 6.0, 1.0),
        WordTS("пять", 6.0, 7.0, 1.0),
    ]

    with (
        patch(
            "app.services.frame_audio.probe_duration",
            new=AsyncMock(return_value=7.0),
        ),
        patch(
            "app.services.frame_audio.has_all_frame_audio",
            return_value=False,
        ),
    ):
        clips, master, scale, per_frame = await build_assembly_timeline(
            tmp_path,
            voice,
            [1, 3, 5],
            cells=output_cells,
            align_cells=align_cells,
            words=words,
        )

    assert master == 7.0
    assert scale == 1.0
    assert per_frame is False
    assert [c.frame_number for c in clips] == [1, 3, 5]
    # Кадр 3 должен начинаться после речи кадра 2 (~3s), не сразу после кадра 1
    assert clips[1].start_ts >= 2.5
    assert clips[2].start_ts >= 5.5
