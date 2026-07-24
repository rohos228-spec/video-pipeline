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
    _timings_from_speech_islands,
)
from app.services.mapper import count_crumb_frames, timings_from_word_transitions
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


def _collapsed_align_fixture():
    """Много кадров R49, но ASR-слова схлопнуты в несколько точек — бывший источник крошек."""
    cells = [(i, f"кадр{i} текст длиннее") for i in range(1, 21)]
    words = [
        WordTS("кадр1", 0.0, 0.4, 1.0),
        WordTS("текст", 0.4, 0.8, 1.0),
        WordTS("длиннее", 0.8, 1.2, 1.0),
        # дальше «схлоп» — мало уникальных стартов на хвост
        WordTS("кадр10", 5.0, 5.3, 1.0),
        WordTS("текст", 5.3, 5.6, 1.0),
        WordTS("кадр20", 9.0, 9.5, 1.0),
        WordTS("хвост", 9.5, 10.0, 1.0),
    ]
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
    assert count_crumb_frames(timings) == 0


@pytest.mark.parametrize(
    "method_id",
    ["nemo_direct", "nemo_contiguous", "nemo_chunks", "nemo_auto"],
)
def test_collapsed_asr_no_crumbs(method_id: str) -> None:
    cells, words, master = _collapsed_align_fixture()
    timings = apply_align_method(method_id, cells, words, master)
    assert len(timings) == len(cells)
    assert count_crumb_frames(timings) == 0, [
        (t.frame_number, t.duration) for t in timings if t.duration <= 0.1
    ]
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - master) < 0.02


def test_word_transitions_splits_identical_starts() -> None:
    cells = [(1, "один два"), (2, "три"), (3, "четыре пять шесть")]
    # Все «первые» слова кадров указывают на один start через poor align —
    # симулируем одинаковые таймкоды слов, которые map схлопнет.
    words = [
        WordTS("один", 1.0, 1.2, 1.0),
        WordTS("два", 1.2, 1.4, 1.0),
        WordTS("три", 1.0, 1.1, 1.0),  # тот же start-кластер
        WordTS("четыре", 1.0, 1.15, 1.0),
        WordTS("пять", 4.0, 4.3, 1.0),
        WordTS("шесть", 4.3, 4.6, 1.0),
    ]
    timings = timings_from_word_transitions(cells, words, master=10.0)
    assert count_crumb_frames(timings) == 0
    assert abs(timings[-1].end_ts - 10.0) < 0.02


def test_speech_islands_not_pure_uniform() -> None:
    cells = [(i, f"кадр{i} " + ("слово " * (i))) for i in range(1, 7)]
    # Два острова речи: [0-3] и [7-12]; длинная тишина 3-7
    silences = [(3.0, 7.0)]
    timings = _timings_from_speech_islands(cells, 12.0, silences)
    assert len(timings) == 6
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - 12.0) < 0.02
    assert count_crumb_frames(timings) == 0
    # Не чистая равномерка 2.0с — веса разные
    durs = [t.duration for t in timings]
    assert max(durs) - min(durs) > 0.2


def test_run_speech_align_silence_no_nemo(tmp_path: Path) -> None:
    fake = tmp_path / "voice.wav"
    fake.write_bytes(b"RIFF")
    cells = [(1, "а а а"), (2, "б"), (3, "в в")]
    with patch(
        "app.services.audio_align_methods.detect_silences",
        return_value=[(1.0, 1.5), (2.5, 3.5)],
    ):
        result = run_speech_align("silence", fake, cells, 5.0)
    assert result.speech_source == "ffmpeg_silence"
    assert result.words == []
    assert len(result.timings) == 3
    assert count_crumb_frames(result.timings) == 0


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
    assert count_crumb_frames(result.timings) == 0


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
