"""Субтитры: текст из Excel, тайминг по Whisper, не более 2 слов."""

from __future__ import annotations

from app.services.mapper import FrameTiming, FrameWordSpan, build_frame_word_spans
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]


def build_subtitle_cues_from_cells(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    frame_timings: list[FrameTiming],
    *,
    max_words: int = 2,
) -> list[SubtitleCue]:
    if max_words < 1:
        raise ValueError("max_words must be >= 1")
    if not words:
        return []

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

        wh_times: list[tuple[float, float]] = []
        for wi in span.whisper_indices:
            idx = max(0, min(wi, len(words) - 1))
            wh_times.append((words[idx].start, words[idx].end))

        if not wh_times:
            continue

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
            start = map_ts(chunk_times[0][0])
            end = map_ts(chunk_times[-1][1])
            if end <= start:
                end = start + 0.04
            entries.append((round(start, 3), round(end, 3), " ".join(chunk_words)))

    return _merge_monotonic(entries)


def _merge_monotonic(entries: list[SubtitleCue]) -> list[SubtitleCue]:
    if not entries:
        return entries
    out: list[SubtitleCue] = [entries[0]]
    for start, end, text in entries[1:]:
        prev_start, prev_end, prev_text = out[-1]
        if text == prev_text and abs(start - prev_start) < 0.02:
            continue
        if start < prev_end:
            start = prev_end
        if end <= start:
            end = start + 0.04
        out.append((start, end, text))
    return out
