"""144-frame montage timeline tests."""

from app.services.mapper import (
    build_absolute_asr_timeline,
    build_frame_word_spans_for_montage,
    map_frames,
)
from app.services.whisper import WordTS


def test_144_frames_cover_full_audio_without_zero_tail() -> None:
    cells = [(i, f"слово{i} текст") for i in range(1, 145)]
    words = [
        WordTS(f"слово{i}", i * 3.82, i * 3.82 + 0.4, 1.0)
        for i in range(400)
    ]
    spans = build_frame_word_spans_for_montage(cells, words)
    timings = build_absolute_asr_timeline(spans, words, 551.67)
    assert len(timings) == 144
    assert all(t.duration > 0 for t in timings)
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - 551.67) < 0.02
    assert abs(sum(t.duration for t in timings) - 551.67) < 0.1
    avg = 551.67 / 144
    assert all(t.duration < avg * 5 for t in timings)


def test_map_frames_two_cells_at_asr_boundaries() -> None:
    cells = [(1, "один два"), (2, "три четыре")]
    words = [
        WordTS("один", 0.0, 0.8, 1.0),
        WordTS("два", 0.85, 1.6, 1.0),
        WordTS("три", 2.0, 2.7, 1.0),
        WordTS("четыре", 2.75, 3.5, 1.0),
    ]
    timings = map_frames(cells, words, audio_duration=10.0)
    assert len(timings) == 2
    assert timings[0].start_ts == 0.0
    assert abs(timings[0].end_ts - timings[1].start_ts) < 0.02
    assert abs(timings[-1].end_ts - 10.0) < 0.02
