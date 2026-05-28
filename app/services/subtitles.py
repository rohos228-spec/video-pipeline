"""Субтитры: прямые таймкоды Whisper внутри каждой ячейки R49."""

from __future__ import annotations

from loguru import logger

from app.services.mapper import (
    FrameTiming,
    align_cell_to_local_words,
    extract_local_frame_words,
    tokenize_display,
    whisper_token,
)
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]

_WORD_GAP = 0.03
_MIN_DUR = 0.28
_CHARS_PER_SECOND = 14.0


def build_subtitle_cues_from_cells(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    frame_timings: list[FrameTiming],
    *,
    max_words: int = 1,
    max_end_ts: float | None = None,
    direct_whisper_times: bool = False,
    lead_seconds: float = 0.0,
    chars_per_second: float = _CHARS_PER_SECOND,
) -> list[SubtitleCue]:
    """Субтитры: align Excel ↔ Whisper внутри кадра, старт = word.start − lead."""
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
            chars_per_second=chars_per_second,
        )

        frame_cues: list[SubtitleCue] = []
        for word_text, (local_start, local_end) in zip(display_words, slots, strict=True):
            local_start = max(0.0, local_start - lead_seconds)
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
            frame_cues, frame_start, frame_end, max_end_ts, chars_per_second,
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


def _min_duration_for_word(word: str, chars_per_second: float) -> float:
    cps = max(float(chars_per_second), 1.0)
    return max(_MIN_DUR, len(word) / cps)


def _schedule_words_in_frame(
    display_words: list[str],
    whisper_indices: list[int],
    local_words: list[WordTS],
    *,
    frame_dur: float,
    chars_per_second: float,
) -> list[tuple[float, float]]:
    """Прямые таймкоды Whisper; fallback — распределение по символам."""
    n = len(display_words)
    if n == 0 or frame_dur <= 0:
        return []

    char_slots = _char_weighted_slots(display_words, frame_dur, chars_per_second)
    if not local_words or len(whisper_indices) < n:
        return char_slots

    indices = _monotonic_indices(whisper_indices, len(local_words))
    if _alignment_strong(display_words, indices, local_words):
        direct = _direct_whisper_slots(
            display_words, indices, local_words, frame_dur, chars_per_second,
        )
        if len(direct) == n:
            return direct

    return char_slots


def _alignment_strong(
    display_words: list[str],
    indices: list[int],
    local_words: list[WordTS],
) -> bool:
    """Align Excel ↔ Whisper достаточно точный для прямых таймкодов."""
    n = len(display_words)
    if len(indices) < n or len(set(indices)) < max(1, (n + 1) // 2):
        return False
    matches = 0
    for wi, word in enumerate(display_words):
        idx = indices[wi]
        if idx >= len(local_words):
            continue
        if word.lower() == whisper_token(local_words[idx]):
            matches += 1
    return matches >= max(1, (n + 1) // 2)


def _direct_whisper_slots(
    display_words: list[str],
    indices: list[int],
    local_words: list[WordTS],
    frame_dur: float,
    chars_per_second: float,
) -> list[tuple[float, float]]:
    """start/end = Whisper word.start/end (без растягивания по кадру)."""
    n = len(display_words)
    out: list[tuple[float, float]] = []
    for wi, word in enumerate(display_words):
        idx = indices[wi]
        start = max(0.0, local_words[idx].start)

        if wi + 1 < n:
            next_idx = indices[wi + 1]
            if next_idx > idx:
                end = local_words[next_idx].start - _WORD_GAP
            else:
                end = local_words[idx].end
        elif idx + 1 < len(local_words):
            end = local_words[idx + 1].start - _WORD_GAP
        else:
            end = local_words[idx].end

        min_dur = _min_duration_for_word(word, chars_per_second)
        end = max(end, start + min_dur)
        end = min(end, frame_dur)
        if end <= start:
            end = min(start + min_dur, frame_dur)
        if end <= start:
            continue
        out.append((start, end))
    return out


def _char_weighted_slots(
    display_words: list[str],
    frame_dur: float,
    chars_per_second: float,
) -> list[tuple[float, float]]:
    weights = [max(len(w), 1) for w in display_words]
    total = sum(weights)
    out: list[tuple[float, float]] = []
    pos = 0.0
    for wi, word in enumerate(display_words):
        slot = (weights[wi] / total) * frame_dur
        start = pos
        min_dur = _min_duration_for_word(word, chars_per_second)
        end = min(pos + slot - _WORD_GAP, frame_dur)
        end = max(end, start + min_dur)
        end = min(end, frame_dur)
        out.append((start, end))
        pos += slot
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
    chars_per_second: float,
) -> list[SubtitleCue]:
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
                prev_min = _min_duration_for_word(prev_text, chars_per_second)
                prev_end = max(prev_start + prev_min, start - _WORD_GAP)
                out[-1] = (prev_start, round(prev_end, 3), prev_text)
            if start < out[-1][1]:
                start = out[-1][1] + _WORD_GAP
            if start >= cap:
                continue

        min_dur = _min_duration_for_word(text, chars_per_second)
        end = max(end, start + min_dur)
        end = min(end, cap)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))

    return out
