"""Tests for 5 NeMo/acoustic audio-align methodologies (no Whisper)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.audio_align_methods import (
    ALIGN_METHODS,
    apply_align_method,
    detect_silences,
    list_align_methods,
    resolve_align_method,
    run_speech_align,
    _timings_from_silence_cuts,
)
from app.services.whisper import WordTS


def _cells_words():
    cells = [(i, f"слово{i} фраза") for i in range(1, 6)]
    words = []
    t = 0.0
    for i in range(1, 6):
        words.append(WordTS(f"слово{i}", t, t + 0.4, 1.0))
        words.append(WordTS("фраза", t + 0.4, t + 0.9, 1.0))
        t += 2.0
    return cells, words, 12.0


def test_segment_bounds_cap_at_eight() -> None:
    from app.services.audio_align_methods import _segment_time_bounds

    cells = [(i, f"слово{i} ещё текст") for i in range(1, 154)]
    segs = _segment_time_bounds(cells, 508.81, max_chunks=8)
    assert 1 <= len(segs) <= 8
    assert segs[0][0] == 0.0
    assert abs(segs[-1][1] - 508.81) < 0.02
    for prev, cur in zip(segs, segs[1:]):
        assert cur[0] >= prev[0]
        assert cur[1] > cur[0]


def test_list_has_five_nemo_methods() -> None:
    methods = list_align_methods()
    assert len(methods) == 5
    ids = {m["id"] for m in methods}
    assert ids == {m.id for m in ALIGN_METHODS}
    assert "whisper" not in ids
    assert all("whisper" not in m["summary"].lower() for m in methods)


def test_resolve_legacy_and_unknown() -> None:
    assert resolve_align_method("direct") == "nemo_direct"
    assert resolve_align_method("auto") == "nemo_auto"
    with pytest.raises(ValueError):
        resolve_align_method("nope")


@pytest.mark.parametrize(
    "method_id",
    ["nemo_direct", "nemo_contiguous", "nemo_chunks", "nemo_auto"],
)
def test_nemo_timing_methods_cover_master(method_id: str) -> None:
    cells, words, master = _cells_words()
    timings = apply_align_method(method_id, cells, words, master)
    assert len(timings) == 5
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - master) < 0.02
    for prev, cur in zip(timings, timings[1:]):
        assert cur.start_ts >= prev.end_ts - 0.001


def test_silence_cuts_use_longest_pauses() -> None:
    cells = [(i, f"кадр{i}") for i in range(1, 4)]
    silences = [(2.0, 2.8), (5.0, 5.3), (8.0, 9.0)]
    timings = _timings_from_silence_cuts(cells, 12.0, silences)
    assert len(timings) == 3
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - 12.0) < 0.02
    # Длинные паузы ~2.4 и ~8.5 должны стать границами
    cuts = [timings[1].start_ts, timings[2].start_ts]
    assert any(abs(c - 2.4) < 0.05 for c in cuts)
    assert any(abs(c - 8.5) < 0.05 for c in cuts)


def test_run_speech_align_silence_no_nemo(tmp_path: Path) -> None:
    # Минимальный валидный wav не обязателен — mock detect_silences
    fake = tmp_path / "voice.wav"
    fake.write_bytes(b"RIFF")
    cells = [(1, "а"), (2, "б"), (3, "в")]
    with patch(
        "app.services.audio_align_methods.detect_silences",
        return_value=[(1.0, 1.5), (2.5, 3.5)],
    ):
        result = run_speech_align("silence", fake, cells, 5.0)
    assert result.speech_source == "ffmpeg_silence"
    assert result.words == []
    assert len(result.timings) == 3


def test_run_speech_align_uses_nemo_not_whisper(tmp_path: Path) -> None:
    fake = tmp_path / "voice.wav"
    fake.write_bytes(b"RIFF")
    cells, words, master = _cells_words()
    with (
        patch(
            "app.services.audio_align_methods.speech_nemo_full",
            return_value=words,
        ) as nemo,
        patch(
            "app.services.audio_align_methods.transcribe_nemo",
            side_effect=AssertionError("whisper path"),
        ),
    ):
        result = run_speech_align("nemo_direct", fake, cells, master)
    nemo.assert_called_once()
    assert result.speech_source == "nemo"
    assert len(result.timings) == 5


def test_detect_silences_parses_ffmpeg_stderr(tmp_path: Path) -> None:
    fake = tmp_path / "voice.wav"
    fake.write_bytes(b"x")
    stderr = (
        "silence_start: 1.2\n"
        "silence_end: 1.8 | silence_duration: 0.6\n"
        "silence_start: 4.0\n"
        "silence_end: 4.5\n"
    )
    with patch("app.services.audio_align_methods.subprocess.run") as run:
        run.return_value = type("R", (), {"returncode": 0, "stderr": stderr, "stdout": ""})()
        silences = detect_silences(fake)
    assert silences == [(1.2, 1.8), (4.0, 4.5)]
