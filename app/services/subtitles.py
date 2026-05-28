"""Субтитры: текст из Excel, одно слово на экран, тайминг по Whisper."""

from __future__ import annotations

from app.services.mapper import FrameTiming, build_frame_word_spans_per_frame
from app.services.whisper import WordTS

SubtitleCue = tuple[float, float, str]

_WORD_GAP = 0.02
_MIN_DUR = 0.04


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
    """Одно слово = одна строка субтитров. direct_whisper_times зарезервирован (всегда direct)."""
    del direct_whisper_times  # всегда прямые Whisper-метки в окне кадра
    if max_words != 1:
        max_words = 1

    if not words:
        return []

    spans = build_frame_word_spans_per_frame(cells, words, frame_timings)
    by_number = {t.frame_number: t for t in frame_timings}

    # Плоский список (frame_end, word_index_in_span, span) для end = start следующего слова
    flat: list[tuple[FrameTiming, int, object]] = []
    for span in spans:
        timing = by_number.get(span.frame_number)
        if timing is None or not span.display_words:
            continue
        for wi in range(len(span.display_words)):
            flat.append((timing, wi, span))

    entries: list[SubtitleCue] = []
    for fi, (timing, wi, span) in enumerate(flat):
        frame_start = timing.start_ts
        frame_end = timing.end_ts
        if frame_end <= frame_start:
            continue

        if wi >= len(span.whisper_indices):
            continue
        idx = max(0, min(span.whisper_indices[wi], len(words) - 1))
        text = span.display_words[wi]
        wh_start = words[idx].start
        wh_end = words[idx].end

        start = max(wh_start - lead_seconds, frame_start)

        # конец = начало следующего слова (глобально по сценарию)
        if fi + 1 < len(flat):
            next_span = flat[fi + 1][2]
            next_wi = flat[fi + 1][1]
            if next_wi < len(next_span.whisper_indices):
                next_idx = max(0, min(next_span.whisper_indices[next_wi], len(words) - 1))
                end = words[next_idx].start - _WORD_GAP
            else:
                end = wh_end + 0.03
        elif idx + 1 < len(words):
            end = words[idx + 1].start - _WORD_GAP
        else:
            end = wh_end + 0.03

        end = min(end, frame_end)
        if max_end_ts is not None:
            end = min(end, max_end_ts)

        if end <= start:
            end = min(start + _MIN_DUR, frame_end)
        if end <= start:
            continue

        entries.append((round(start, 3), round(end, 3), text))

    return entries
