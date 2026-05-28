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

_WORD_GAP = 0.03
_MIN_DUR = 0.28
_MAX_SILENCE = 0.6


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
    del direct_whisper_times, max_words
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

        slots = _schedule_words_in_frame(
            span.display_words,
            span.whisper_indices,
            words,
            frame_start,
            frame_end,
            lead_seconds=lead_seconds,
        )
        for text, (start, end) in zip(span.display_words, slots, strict=True):
            if max_end_ts is not None:
                end = min(end, max_end_ts)
                start = min(start, max_end_ts)
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


def _schedule_words_in_frame(
    display_words: list[str],
    whisper_indices: list[int],
    words: list[WordTS],
    frame_start: float,
    frame_end: float,
    *,
    lead_seconds: float,
) -> list[tuple[float, float]]:
    """Распределить слова по окну кадра — без длинных пауз и «пулемёта»."""
    n = len(display_words)
    frame_dur = frame_end - frame_start
    if n == 0 or frame_dur <= 0:
        return []

    indices = _monotonic_indices(whisper_indices, len(words))
    if len(indices) < n or not _alignment_usable(indices, n):
        return _equal_slots(n, frame_start, frame_end, lead_seconds)

    wh_starts = [words[i].start for i in indices]
    wh_ends = [words[i].end for i in indices]
    wh_min = min(wh_starts)
    wh_max = max(wh_ends)
    wh_span = max(wh_max - wh_min, 0.01)

    # Whisper-слова сжаты в конец окна или слишком узкий кластер — равномерно по кадру.
    late_start = wh_min > frame_start + frame_dur * 0.35
    narrow = wh_span < frame_dur * 0.25
    if late_start or narrow:
        return _equal_slots(n, frame_start, frame_end, lead_seconds)

    slots: list[tuple[float, float]] = []
    for wi in range(n):
        idx = indices[wi]
        rel = _word_rel_position(wi, n, indices, words, wh_min, wh_span)
        start = frame_start + rel * frame_dur
        if wi == 0 and lead_seconds > 0:
            start = max(frame_start, start - lead_seconds)

        if wi + 1 < n:
            rel_next = _word_rel_position(wi + 1, n, indices, words, wh_min, wh_span)
            end = frame_start + rel_next * frame_dur - _WORD_GAP
        else:
            end = frame_end

        end = max(end, start + _MIN_DUR)
        end = min(end, frame_end)
        if end <= start:
            end = min(start + _MIN_DUR, frame_end)
        slots.append((start, end))

    return slots


def _word_rel_position(
    wi: int,
    n: int,
    indices: list[int],
    words: list[WordTS],
    wh_min: float,
    wh_span: float,
) -> float:
    """Относительная позиция слова 0..1; дубликаты индексов не схлопываются."""
    idx = indices[wi]
    if wi > 0 and indices[wi - 1] == idx:
        run_start = wi - 1
        while run_start > 0 and indices[run_start - 1] == idx:
            run_start -= 1
        run_end = wi
        while run_end + 1 < n and indices[run_end + 1] == idx:
            run_end += 1
        run_len = run_end - run_start + 1
        base = (words[idx].start - wh_min) / wh_span
        offset = (wi - run_start + 1) / (run_len + 1)
        step = 1.0 / max(n, 1)
        return min(1.0, base * 0.85 + offset * step * 0.5)
    return (words[idx].start - wh_min) / wh_span


def _alignment_usable(indices: list[int], word_count: int) -> bool:
    if not indices:
        return False
    unique = len(set(indices))
    return unique >= max(1, (word_count + 1) // 2)


def _equal_slots(
    n: int,
    frame_start: float,
    frame_end: float,
    lead_seconds: float,
) -> list[tuple[float, float]]:
    frame_dur = frame_end - frame_start
    slot = frame_dur / n
    out: list[tuple[float, float]] = []
    for wi in range(n):
        start = frame_start + wi * slot
        if wi == 0 and lead_seconds > 0:
            start = max(frame_start, start - lead_seconds)
        end = frame_start + (wi + 1) * slot - _WORD_GAP
        end = max(end, start + _MIN_DUR)
        end = min(end, frame_end)
        out.append((start, end))
    return out


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
    """Без наложений; короткие паузы заполняем, длинные — не разгоняем слова."""
    if not entries:
        return entries

    cap = max_end_ts if max_end_ts is not None else float("inf")
    sorted_entries = sorted(entries, key=lambda x: x[0])
    out: list[SubtitleCue] = []

    for start, end, text in sorted_entries:
        if start >= cap:
            continue
        end = min(end, cap)

        if out:
            prev_start, prev_end, prev_text = out[-1]
            if text == prev_text and abs(start - prev_start) < 0.02:
                continue
            if start < prev_end:
                start = prev_end + _WORD_GAP
            gap = start - prev_end
            if 0 < gap <= _MAX_SILENCE:
                out[-1] = (prev_start, round(min(prev_end + gap * 0.5, start - _WORD_GAP), 3), prev_text)

        end = max(end, start + _MIN_DUR)
        end = min(end, cap)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))

    return out
