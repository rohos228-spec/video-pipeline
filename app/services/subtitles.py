"""Субтитры: одно слово на экран, строго внутри границ каждой ячейки R49."""

from __future__ import annotations

from loguru import logger

from app.services.mapper import (
    FrameTiming,
    align_cell_to_local_words,
    extract_local_frame_words,
    tokenize_display,
)
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]

_WORD_GAP = 0.03
_MIN_DUR = 0.28


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
    """Субтитры по кадрам: каждая ячейка plan → свой клип, без выхода за границы."""
    del direct_whisper_times, max_words
    if not frame_timings:
        return []

    by_number = {t.frame_number: t for t in frame_timings}
    cell_by_number = dict(cells)
    all_cues: list[SubtitleCue] = []

    for timing in sorted(frame_timings, key=lambda t: t.frame_number):
        text = cell_by_number.get(timing.frame_number, "")
        display_words = tokenize_display(text)
        if not display_words:
            continue

        frame_start = timing.start_ts
        frame_end = timing.end_ts
        if frame_end <= frame_start:
            continue

        local_words = extract_local_frame_words(words, frame_start, frame_end)
        local_indices = align_cell_to_local_words(display_words, local_words)

        slots = _schedule_words_in_frame(
            display_words,
            local_indices,
            local_words,
            frame_dur=frame_end - frame_start,
            lead_seconds=lead_seconds,
        )

        frame_cues: list[SubtitleCue] = []
        for word_text, (local_start, local_end) in zip(display_words, slots, strict=True):
            start = frame_start + local_start
            end = frame_start + local_end
            start = max(start, frame_start)
            end = min(end, frame_end)
            if max_end_ts is not None:
                start = min(start, max_end_ts)
                end = min(end, max_end_ts)
            if end <= start:
                continue
            frame_cues.append((round(start, 3), round(end, 3), word_text))

        frame_cues = _normalize_within_frame(
            frame_cues, frame_start, frame_end, max_end_ts,
        )
        all_cues.extend(frame_cues)

    expected = sum(len(tokenize_display(t)) for _, t in cells)
    if len(all_cues) < expected:
        logger.warning(
            "subtitles: {} слов из {} — часть без тайминга в ячейках",
            len(all_cues),
            expected,
        )
    return sorted(all_cues, key=lambda x: x[0])


def _schedule_words_in_frame(
    display_words: list[str],
    whisper_indices: list[int],
    local_words: list[WordTS],
    *,
    frame_dur: float,
    lead_seconds: float,
) -> list[tuple[float, float]]:
    """Распределить слова по [0, frame_dur] — локальные координаты кадра."""
    n = len(display_words)
    if n == 0 or frame_dur <= 0:
        return []

    indices = _monotonic_indices(whisper_indices, len(local_words))
    if len(indices) < n or not _alignment_usable(indices, n):
        return _equal_slots(n, frame_dur, lead_seconds)

    wh_starts = [local_words[i].start for i in indices]
    wh_ends = [local_words[i].end for i in indices]
    wh_min = min(wh_starts)
    wh_max = max(wh_ends)
    wh_span = max(wh_max - wh_min, 0.01)

    late_start = wh_min > frame_dur * 0.35
    narrow = wh_span < frame_dur * 0.25
    if late_start or narrow:
        return _equal_slots(n, frame_dur, lead_seconds)

    slots: list[tuple[float, float]] = []
    for wi in range(n):
        rel = _word_rel_position(wi, n, indices, local_words, wh_min, wh_span)
        start = rel * frame_dur
        if wi == 0 and lead_seconds > 0:
            start = max(0.0, start - lead_seconds)

        if wi + 1 < n:
            rel_next = _word_rel_position(wi + 1, n, indices, local_words, wh_min, wh_span)
            end = rel_next * frame_dur - _WORD_GAP
        else:
            end = frame_dur

        end = max(end, start + _MIN_DUR)
        end = min(end, frame_dur)
        if end <= start:
            end = min(start + _MIN_DUR, frame_dur)
        slots.append((start, end))

    return slots


def _word_rel_position(
    wi: int,
    n: int,
    indices: list[int],
    local_words: list[WordTS],
    wh_min: float,
    wh_span: float,
) -> float:
    idx = indices[wi]
    if wi > 0 and indices[wi - 1] == idx:
        run_start = wi - 1
        while run_start > 0 and indices[run_start - 1] == idx:
            run_start -= 1
        run_end = wi
        while run_end + 1 < n and indices[run_end + 1] == idx:
            run_end += 1
        run_len = run_end - run_start + 1
        base = (local_words[idx].start - wh_min) / wh_span
        offset = (wi - run_start + 1) / (run_len + 1)
        step = 1.0 / max(n, 1)
        return min(1.0, base * 0.85 + offset * step * 0.5)
    return (local_words[idx].start - wh_min) / wh_span


def _alignment_usable(indices: list[int], word_count: int) -> bool:
    if not indices:
        return False
    unique = len(set(indices))
    return unique >= max(1, (word_count + 1) // 2)


def _equal_slots(n: int, frame_dur: float, lead_seconds: float) -> list[tuple[float, float]]:
    slot = frame_dur / n
    out: list[tuple[float, float]] = []
    for wi in range(n):
        start = wi * slot
        if wi == 0 and lead_seconds > 0:
            start = max(0.0, start - lead_seconds)
        end = (wi + 1) * slot - _WORD_GAP
        end = max(end, start + _MIN_DUR)
        end = min(end, frame_dur)
        out.append((start, end))
    return out


def _monotonic_indices(indices: list[int], word_count: int) -> list[int]:
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


def _normalize_within_frame(
    entries: list[SubtitleCue],
    frame_start: float,
    frame_end: float,
    max_end_ts: float | None,
) -> list[SubtitleCue]:
    """Сглаживание только внутри одного кадра — не трогаем соседние ячейки."""
    if not entries:
        return entries

    cap = min(frame_end, max_end_ts if max_end_ts is not None else frame_end)
    sorted_entries = sorted(entries, key=lambda x: x[0])
    out: list[SubtitleCue] = []

    for start, end, text in sorted_entries:
        start = max(start, frame_start)
        end = min(end, cap)
        if start >= cap:
            continue

        if out:
            prev_start, prev_end, prev_text = out[-1]
            if text == prev_text and abs(start - prev_start) < 0.02:
                continue
            if start < prev_end:
                start = prev_end + _WORD_GAP
            if start >= cap:
                continue

        end = max(end, start + _MIN_DUR)
        end = min(end, cap)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))

    if out:
        last_start, last_end, last_text = out[-1]
        if last_end < cap - 0.01:
            out[-1] = (last_start, round(min(last_end, cap), 3), last_text)

    return out
