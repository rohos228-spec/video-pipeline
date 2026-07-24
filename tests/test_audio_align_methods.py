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
from app.services.mapper import (
    FrameTiming,
    count_crumb_frames,
    timings_from_word_transitions,
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


def test_voiceover_match_no_collapse() -> None:
    """153-подобно: много кадров, ASR-слова идут по тексту — границы = речь, 0 крошек."""
    from app.services.mapper import exclusive_asr_word_bounds, timings_match_voiceover

    cells = [(i, f"кадр{i} говорит фразу номер {i}") for i in range(1, 31)]
    words: list[WordTS] = []
    t = 0.0
    for i in range(1, 31):
        for tok in [f"кадр{i}", "говорит", "фразу", "номер", str(i)]:
            words.append(WordTS(tok, t, t + 0.25, 1.0))
            t += 0.28
    master = t + 1.0
    bounds = exclusive_asr_word_bounds(cells, words)
    # Старты matched-кадров строго возрастают
    starts = [b[1] for b in bounds if b[1] >= 0]
    assert starts == sorted(starts)
    assert len(set(starts)) == len(starts)
    timings = timings_match_voiceover(cells, words, master, mode="contiguous")
    assert len(timings) == 30
    assert count_crumb_frames(timings) == 0
    assert timings[0].start_ts == 0.0
    assert abs(timings[-1].end_ts - master) < 0.02
    # Середина должна сидеть около реальной речи, не в нуле
    assert timings[14].start_ts > 5.0


def test_phantom_intro_does_not_steal_asr_words() -> None:
    """R49-интро нет в озвучке → не забирает ASR-слова, хвост не ползёт."""
    from app.services.mapper import (
        align_script_tokens,
        exclusive_asr_word_bounds,
        timings_match_voiceover,
        tokenize_display,
    )

    cells = [
        (1, "титульный кадр которого нет в озвучке совсем"),
        (2, "ведьмы жили в лесу давно"),
        (3, "и варили зелье ночью"),
    ]
    # Речь начинается с паузы 1.0с; слов интро в ASR нет.
    words = [
        WordTS("ведьмы", 1.0, 1.4, 1.0),
        WordTS("жили", 1.4, 1.8, 1.0),
        WordTS("в", 1.8, 1.95, 1.0),
        WordTS("лесу", 1.95, 2.3, 1.0),
        WordTS("давно", 2.3, 2.7, 1.0),
        WordTS("и", 2.7, 2.85, 1.0),
        WordTS("варили", 2.85, 3.3, 1.0),
        WordTS("зелье", 3.3, 3.7, 1.0),
        WordTS("ночью", 3.7, 4.2, 1.0),
    ]
    master = 5.0

    script: list[str] = []
    for _fn, text in cells:
        script.extend(t.lower() for t in tokenize_display(text))
    aligned = align_script_tokens(script, words)
    # Ведущие токены интро — insert (-1), не words[0]
    intro_n = len(tokenize_display(cells[0][1]))
    assert all(w < 0 for w in aligned[:intro_n]), aligned[:intro_n]
    assert aligned[intro_n] == 0  # «ведьмы» → первое ASR-слово

    bounds = exclusive_asr_word_bounds(cells, words)
    assert bounds[0] == (1, -1, -1)
    assert bounds[1][1] == 0  # кадр 2 владеет словом 0
    assert bounds[1][2] > bounds[1][1]

    for method_id in ("nemo_direct", "nemo_contiguous", "nemo_auto"):
        timings = apply_align_method(method_id, cells, words, master)
        assert len(timings) == 3
        # Кадр 2 (первая реальная фраза) стартует около 1.0с, не уехал на 2–3с
        assert abs(timings[1].start_ts - 1.0) < 0.08, (method_id, timings)
        # Кадр 3 — около «и» / стыка после кадра 2
        assert timings[2].start_ts >= 2.5, (method_id, timings)
        assert abs(timings[-1].end_ts - master) < 0.02
        assert timings[0].start_ts == 0.0
        # Интро занимает преролл до речи, не кусок озвучки
        assert timings[0].end_ts <= timings[1].start_ts + 0.001
        assert timings[0].end_ts <= 1.05

    # Прямой contiguous: интро = [0, 1.0)
    tv = timings_match_voiceover(cells, words, master, mode="contiguous")
    assert abs(tv[0].end_ts - 1.0) < 0.05
    assert abs(tv[1].start_ts - 1.0) < 0.05


def test_absorb_eliminates_two_crumbs() -> None:
    from app.services.mapper import absorb_crumb_durations, count_crumb_frames

    # Имитация лога: 153 кадра, 2 крошки
    timings = [
        FrameTiming(1, 0.0, 5.0, 5.0),
        FrameTiming(2, 5.0, 5.05, 0.05),
        FrameTiming(3, 5.05, 10.0, 4.95),
        FrameTiming(4, 10.0, 10.08, 0.08),
        FrameTiming(5, 10.08, 20.0, 9.92),
    ]
    out = absorb_crumb_durations(timings, 20.0)
    assert count_crumb_frames(out) == 0
    assert out[0].start_ts == 0.0
    assert abs(out[-1].end_ts - 20.0) < 0.02

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
