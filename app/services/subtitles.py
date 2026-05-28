"""Субтитры: текст из Excel, тайминг по Whisper, не более 2 слов."""

from __future__ import annotations

from app.services.mapper import (
    FrameTiming,
    build_frame_word_spans,
    build_frame_word_spans_per_frame,
)
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]


def build_subtitle_cues_from_cells(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    frame_timings: list[FrameTiming],
    *,
    max_words: int = 2,
    max_end_ts: float | None = None,
    direct_whisper_times: bool = False,
    lead_seconds: float = 0.0,
) -> list[SubtitleCue]:
    if max_words < 1:
        raise ValueError("max_words must be >= 1")
    if not words:
        return []

    if direct_whisper_times:
        spans = build_frame_word_spans_per_frame(cells, words, frame_timings)
    else:
        spans = build_frame_word_spans(cells, words)

    by_number = {t.frame_number: t for t in frame_timings}
    entries: list[SubtitleCue] = []

    for span in spans:
        timing = by_number.get(span.frame_number)
        if timing is None or not span.display_words:
            continue

        frame_start = timing.start_ts
        frame_end = timing.end_ts
        if frame_end <= frame_start:
            continue

        if direct_whisper_times:
            entries.extend(
                _cues_direct_whisper(
                    span,
                    words,
                    frame_start,
                    frame_end,
                    max_words=max_words,
                    lead_seconds=lead_seconds,
                )
            )
        else:
            entries.extend(
                _cues_stretched(
                    span,
                    words,
                    frame_start,
                    frame_end,
                    max_words=max_words,
                    lead_seconds=lead_seconds,
                )
            )

    if max_end_ts is not None:
        entries = _clamp_to_audio(entries, max_end_ts)
    return _merge_monotonic(entries)


def _cues_direct_whisper(
    span,
    words: list[WordTS],
    frame_start: float,
    frame_end: float,
    *,
    max_words: int,
    lead_seconds: float,
) -> list[SubtitleCue]:
    """Per-frame TTS: прямые Whisper start/end + небольшое опережение."""
    entries: list[SubtitleCue] = []
    if not span.whisper_indices:
        return entries

    for i in range(0, len(span.display_words), max_words):
        chunk_words = span.display_words[i : i + max_words]
        chunk_indices = span.whisper_indices[i : i + max_words]
        if not chunk_indices:
            continue

        starts: list[float] = []
        ends: list[float] = []
        for wi in chunk_indices:
            idx = max(0, min(wi, len(words) - 1))
            starts.append(words[idx].start)
            ends.append(words[idx].end)

        start = max(starts[0] - lead_seconds, frame_start)
        end = min(ends[-1], frame_end)
        if end <= start:
            end = min(start + 0.04, frame_end)
        entries.append((round(start, 3), round(end, 3), " ".join(chunk_words)))

    return entries


def _cues_stretched(
    span,
    words: list[WordTS],
    frame_start: float,
    frame_end: float,
    *,
    max_words: int,
    lead_seconds: float,
) -> list[SubtitleCue]:
    """Legacy: Whisper растягивается на окно кадра (один voice_full без per-frame TTS)."""
    entries: list[SubtitleCue] = []
    wh_times: list[tuple[float, float]] = []
    for wi in span.whisper_indices:
        idx = max(0, min(wi, len(words) - 1))
        wh_times.append((words[idx].start, words[idx].end))

    if not wh_times:
        return entries

    wh_start = wh_times[0][0]
    wh_end = wh_times[-1][1]
    wh_span = max(wh_end - wh_start, 0.01)

    def map_ts(ts: float) -> float:
        rel = (ts - wh_start) / wh_span
        rel = min(max(rel, 0.0), 1.0)
        return frame_start + rel * (frame_end - frame_start)

    for i in range(0, len(span.display_words), max_words):
        chunk_words = span.display_words[i : i + max_words]
        chunk_times = wh_times[i : i + max_words]
        start = max(map_ts(chunk_times[0][0]) - lead_seconds, frame_start)
        end = min(map_ts(chunk_times[-1][1]), frame_end)
        if end <= start:
            end = min(start + 0.04, frame_end)
        entries.append((round(start, 3), round(end, 3), " ".join(chunk_words)))

    return entries


def _clamp_to_audio(entries: list[SubtitleCue], max_end_ts: float) -> list[SubtitleCue]:
    cap = max(float(max_end_ts), 0.01)
    out: list[SubtitleCue] = []
    for start, end, text in entries:
        if start >= cap:
            continue
        end = min(end, cap)
        if end <= start:
            end = min(start + 0.04, cap)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))
    return out


def _merge_monotonic(entries: list[SubtitleCue]) -> list[SubtitleCue]:
    if not entries:
        return entries
    out: list[SubtitleCue] = [entries[0]]
    for start, end, text in entries[1:]:
        prev_start, prev_end, prev_text = out[-1]
        if text == prev_text and abs(start - prev_start) < 0.02:
            continue
        if start < prev_end:
            prev_end = max(prev_start + 0.04, start)
            out[-1] = (prev_start, round(prev_end, 3), prev_text)
        if end <= start:
            end = start + 0.04
        out.append((start, end, text))
    return out
