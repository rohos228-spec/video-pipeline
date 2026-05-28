"""Субтитры: одно слово = один интервал Whisper, без наложений на экране."""

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

_PAUSE_GAP = 0.05  # минимум пустого экрана между словами
_MIN_VISIBLE = 0.10
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
        frame_dur = frame_end - frame_start
        if frame_dur <= 0:
            continue

        local_words = extract_local_frame_words(words, frame_start, frame_end)
        local_indices = align_cell_to_local_words(display_words, local_words)

        slots = _whisper_word_slots(
            display_words,
            local_indices,
            local_words,
            frame_dur=frame_dur,
            chars_per_second=chars_per_second,
        )
        slots = _enforce_non_overlapping_local(slots)

        frame_cues: list[SubtitleCue] = []
        for word_text, (local_start, local_end) in zip(display_words, slots, strict=True):
            start = frame_start + max(0.0, local_start - lead_seconds)
            end = frame_start + local_end
            start = max(start, frame_start)
            end = min(end, frame_end)
            if max_end_ts is not None:
                start = min(start, max_end_ts)
                end = min(end, max_end_ts)
            if end <= start:
                continue
            frame_cues.append((round(start, 3), round(end, 3), word_text))

        frame_cues = _enforce_non_overlapping(frame_cues, frame_start, frame_end)
        all_cues.extend(frame_cues)

    all_cues = _enforce_non_overlapping(all_cues, cap_end=max_end_ts)

    expected = sum(len(tokenize_display(t)) for _, t in cells)
    if len(all_cues) < expected:
        logger.warning(
            "subtitles: {} слов из {} — часть без тайминга",
            len(all_cues),
            expected,
        )
    return sorted(all_cues, key=lambda x: x[0])


def _target_duration(word: str, chars_per_second: float) -> float:
    cps = max(float(chars_per_second), 1.0)
    return max(_MIN_VISIBLE, len(word) / cps)


def _whisper_word_slots(
    display_words: list[str],
    whisper_indices: list[int],
    local_words: list[WordTS],
    *,
    frame_dur: float,
    chars_per_second: float,
) -> list[tuple[float, float]]:
    n = len(display_words)
    if n == 0:
        return []

    if not local_words:
        return _even_slots_from_char_budget(display_words, frame_dur, chars_per_second)

    indices = _sequential_indices(whisper_indices, len(local_words), n)

    raw: list[tuple[float, float]] = []
    for wi in range(n):
        idx = min(max(indices[wi], 0), len(local_words) - 1)
        w = local_words[idx]
        start = max(0.0, w.start)

        end = w.end
        next_wh_start = _next_whisper_start(wi, n, indices, local_words, frame_dur)
        if next_wh_start is not None:
            end = min(end, next_wh_start - _PAUSE_GAP)

        end = min(end, frame_dur)
        if end <= start:
            end = min(max(w.end, start + _MIN_VISIBLE), frame_dur)
        if end <= start:
            continue

        target = _target_duration(display_words[wi], chars_per_second)
        max_end = (next_wh_start - _PAUSE_GAP) if next_wh_start else frame_dur
        if end - start < target and max_end - start >= target:
            end = min(start + target, max_end)

        if end <= start:
            continue
        raw.append((start, end))

    if len(raw) < n:
        return _even_slots_from_char_budget(display_words, frame_dur, chars_per_second)
    return raw


def _next_whisper_start(
    wi: int,
    n: int,
    indices: list[int],
    local_words: list[WordTS],
    frame_dur: float,
) -> float | None:
    if wi + 1 < n:
        nidx = min(max(indices[wi + 1], 0), len(local_words) - 1)
        return local_words[nidx].start
    idx = indices[wi]
    if idx + 1 < len(local_words):
        return local_words[idx + 1].start
    return frame_dur if wi + 1 == n else None


def _sequential_indices(
    whisper_indices: list[int],
    whisper_count: int,
    word_count: int,
) -> list[int]:
    if not whisper_indices or whisper_count <= 0:
        return list(range(min(word_count, max(whisper_count, 1))))
    out: list[int] = []
    last = 0
    max_i = whisper_count - 1
    for idx in whisper_indices:
        idx = max(0, min(int(idx), max_i))
        if out and idx < last:
            idx = min(last + 1, max_i) if last < max_i else last
        out.append(idx)
        last = idx
    while len(out) < word_count:
        nxt = min(last + 1, max_i)
        out.append(nxt)
        last = nxt
    return out[:word_count]


def _even_slots_from_char_budget(
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
        dur = max(_target_duration(word, chars_per_second), slot - _PAUSE_GAP)
        end = min(start + dur, frame_dur)
        out.append((start, end))
        pos += slot
    return _enforce_non_overlapping_local(out)


def _enforce_non_overlapping_local(
    slots: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    if len(slots) < 2:
        return slots
    out: list[tuple[float, float]] = []
    for start, end in slots:
        if out:
            prev_s, prev_e = out[-1]
            if start < prev_e + _PAUSE_GAP:
                start = prev_e + _PAUSE_GAP
            if end <= start:
                end = start + _MIN_VISIBLE
        out.append((start, end))
    return out


def _enforce_non_overlapping(
    entries: list[SubtitleCue],
    frame_start: float | None = None,
    frame_end: float | None = None,
    *,
    cap_end: float | None = None,
) -> list[SubtitleCue]:
    """Строго: end[i] + gap <= start[i+1]. Lead не может создать наслоение."""
    if not entries:
        return entries

    lo = frame_start if frame_start is not None else 0.0
    hi = frame_end if frame_end is not None else (cap_end if cap_end is not None else float("inf"))
    if cap_end is not None:
        hi = min(hi, cap_end)

    sorted_entries = sorted(entries, key=lambda x: (x[0], x[1]))
    out: list[SubtitleCue] = []

    for start, end, text in sorted_entries:
        start = max(start, lo)
        end = min(end, hi)
        if start >= hi:
            continue

        if out:
            ps, pe, pt = out[-1]
            if text == pt and abs(start - ps) < 0.02:
                continue

            if start < pe + _PAUSE_GAP:
                new_pe = min(pe, start - _PAUSE_GAP)
                new_pe = max(new_pe, ps + _MIN_VISIBLE)
                out[-1] = (ps, round(new_pe, 3), pt)
                pe = out[-1][1]

            if start < pe + _PAUSE_GAP:
                start = round(pe + _PAUSE_GAP, 3)

        end = min(end, hi)
        if end <= start:
            end = round(min(start + _MIN_VISIBLE, hi), 3)
        if end <= start:
            continue
        out.append((round(start, 3), round(end, 3), text))

    for i in range(len(out) - 1):
        s, e, t = out[i]
        next_s = out[i + 1][0]
        if e > next_s - _PAUSE_GAP:
            e = max(s + _MIN_VISIBLE, next_s - _PAUSE_GAP)
            out[i] = (s, round(e, 3), t)

    return out
