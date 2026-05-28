"""Субтитры: одно слово на экран, фиксированная позиция, без пропусков."""

from __future__ import annotations

from loguru import logger

from app.services.mapper import (
    FrameTiming,
    build_frame_word_spans_per_frame,
    tokenize_display,
)
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]

_WORD_GAP = 0.02
_MIN_DUR = 0.05


def build_subtitle_cues_from_cells(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    frame_timings: list[FrameTiming],
    *,
    max_words: int = 1,
    max_end_ts: float | None = None,
    direct_whisper_times: bool = False,
    lead_seconds: float = 0.0,
) -> list[SubtitleCue]:
    del direct_whisper_times
    if not words:
        return []

    spans = build_frame_word_spans_per_frame(cells, words, frame_timings)
    by_number = {t.frame_number: t for t in frame_timings}

    raw: list[SubtitleCue] = []
    for span in spans:
        timing = by_number.get(span.frame_number)
        if timing is None or not span.display_words:
            continue
        frame_start = timing.start_ts
        frame_end = timing.end_ts
        if frame_end <= frame_start:
            continue

        indices = _monotonic_indices(span.whisper_indices, len(words))
        n = len(span.display_words)
        frame_dur = frame_end - frame_start

        for wi, text in enumerate(span.display_words):
            if wi < len(indices):
                idx = indices[wi]
                wh_start = words[idx].start
                wh_end = words[idx].end
                start = max(wh_start - lead_seconds, frame_start)

                if wi + 1 < len(indices):
                    next_idx = indices[wi + 1]
                    end = words[next_idx].start - _WORD_GAP
                elif idx + 1 < len(words):
                    end = words[idx + 1].start - _WORD_GAP
                else:
                    end = wh_end + 0.05
            else:
                # Whisper не сопоставил — равномерно внутри кадра
                slot = frame_dur / max(n, 1)
                start = frame_start + wi * slot
                end = frame_start + (wi + 1) * slot - _WORD_GAP

            end = min(end, frame_end)
            if max_end_ts is not None:
                end = min(end, max_end_ts)
            if end <= start:
                end = min(start + _MIN_DUR, frame_end)
            if end <= start:
                continue
            raw.append((round(start, 3), round(end, 3), text))

    entries = _normalize_contiguous(raw, max_end_ts)
    expected = sum(len(tokenize_display(t)) for _, t in cells)
    if len(entries) < expected:
        logger.warning(
            "subtitles: {} слов из {} — часть без тайминга Whisper",
            len(entries),
            expected,
        )
    return entries


def _monotonic_indices(indices: list[int], word_count: int) -> list[int]:
    """Whisper-индексы не идут назад — иначе субтитры прыгают."""
    if not indices:
        return []
    out: list[int] = []
    last = 0
    max_i = max(word_count - 1, 0)
    for idx in indices:
        idx = max(0, min(int(idx), max_i))
        if out and idx < last:
            idx = last
        out.append(idx)
        last = idx
    return out


def _normalize_contiguous(entries: list[SubtitleCue], max_end_ts: float | None) -> list[SubtitleCue]:
    """Без дыр и наложений: каждое слово до начала следующего."""
    if not entries:
        return entries

    cap = max_end_ts if max_end_ts is not None else float("inf")
    sorted_entries = sorted(entries, key=lambda x: x[0])
    out: list[SubtitleCue] = []

    for i, (start, end, text) in enumerate(sorted_entries):
        if start >= cap:
            continue
        end = min(end, cap)

        if out:
            prev_start, prev_end, prev_text = out[-1]
            if text == prev_text and abs(start - prev_start) < 0.02:
                continue
            if start < prev_end:
                prev_end = max(prev_start + _MIN_DUR, start - _WORD_GAP)
                out[-1] = (prev_start, round(prev_end, 3), prev_text)
            if start < out[-1][1]:
                start = out[-1][1]

        if i + 1 < len(sorted_entries):
            next_start = sorted_entries[i + 1][0]
            end = min(end, next_start - _WORD_GAP)
        end = max(end, start + _MIN_DUR)
        end = min(end, cap)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))

    return out
