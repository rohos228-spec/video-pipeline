"""Сопоставление текста кадров (Excel) с word-level таймкодами Whisper."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from app.services.whisper import WordTS

_WORD_RE = re.compile(r"[^\wа-яА-ЯёЁ]+", re.UNICODE)
_DISPLAY_RE = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)


def tokenize_lower(text: str) -> list[str]:
    return [t for t in _WORD_RE.split((text or "").lower()) if t]


def tokenize_display(text: str) -> list[str]:
    return _DISPLAY_RE.findall(text or "")


def whisper_token(word: WordTS) -> str:
    toks = tokenize_lower(word.word)
    return toks[0] if toks else ""


@dataclass
class FrameTiming:
    frame_number: int
    start_ts: float
    end_ts: float
    duration: float


@dataclass
class FrameWordSpan:
    frame_number: int
    display_words: list[str]
    lower_words: list[str]
    whisper_indices: list[int]  # индексы в local_words кадра, не глобальные


def align_script_tokens(script_tokens: list[str], words: list[WordTS]) -> list[int]:
    """Индекс whisper-слова для каждого токена сценария."""
    if not script_tokens:
        return []
    if not words:
        return [0] * len(script_tokens)

    whisper_tokens = [whisper_token(w) for w in words]
    result = [-1] * len(script_tokens)
    matcher = difflib.SequenceMatcher(None, script_tokens, whisper_tokens, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                result[i1 + offset] = j1 + offset
            continue
        script_len = i2 - i1
        if script_len <= 0:
            continue
        whisper_len = max(j2 - j1, 0)
        if whisper_len == 0:
            anchor = max(j1 - 1, 0)
            for offset in range(script_len):
                result[i1 + offset] = anchor
            continue
        for offset in range(script_len):
            wi = j1 + min(int(offset * whisper_len / script_len), whisper_len - 1)
            result[i1 + offset] = wi

    last = 0
    for i, wi in enumerate(result):
        if wi < 0:
            result[i] = last
        else:
            last = min(wi, len(words) - 1)
    return result


def build_frame_word_spans(
    cells: list[tuple[int, str]],
    words: list[WordTS],
) -> list[FrameWordSpan]:
    """Токены Excel по кадрам + индексы whisper (глобальное difflib по всему сценарию)."""
    all_lower: list[str] = []
    all_display: list[str] = []
    frame_ranges: list[tuple[int, int, int]] = []

    for frame_number, text in cells:
        disp = tokenize_display(text)
        lower = [t.lower() for t in disp]
        start = len(all_lower)
        all_lower.extend(lower)
        all_display.extend(disp)
        frame_ranges.append((frame_number, start, len(all_lower)))

    alignment = align_script_tokens(all_lower, words)
    spans: list[FrameWordSpan] = []
    for frame_number, start, end in frame_ranges:
        disp = all_display[start:end]
        lower = all_lower[start:end]
        indices = alignment[start:end] if end > start else []
        spans.append(FrameWordSpan(frame_number, disp, lower, indices))
    return spans


def word_indices_in_window(
    words: list[WordTS],
    start: float,
    end: float,
) -> list[tuple[int, WordTS]]:
    """Whisper-слова, пересекающиеся с [start, end)."""
    return [(i, w) for i, w in enumerate(words) if w.end > start and w.start < end]


def extract_local_frame_words(
    words: list[WordTS],
    frame_start: float,
    frame_end: float,
) -> list[WordTS]:
    """Whisper только внутри границ кадра; таймкоды 0..duration кадра."""
    if frame_end <= frame_start:
        return []
    local: list[WordTS] = []
    for w in words:
        if w.end <= frame_start or w.start >= frame_end:
            continue
        local.append(WordTS(
            word=w.word,
            start=round(max(0.0, w.start - frame_start), 3),
            end=round(min(frame_end - frame_start, w.end - frame_start), 3),
            prob=w.prob,
        ))
    return local


def align_cell_to_local_words(
    display_words: list[str],
    local_words: list[WordTS],
) -> list[int]:
    """Индексы local_words для каждого слова текста ячейки (только внутри кадра)."""
    if not display_words:
        return []
    lower = [t.lower() for t in display_words]
    if not local_words:
        return []
    return align_script_tokens(lower, local_words)


def build_frame_word_span_for_cell(
    frame_number: int,
    text: str,
    local_words: list[WordTS],
) -> FrameWordSpan | None:
    """Одна ячейка plan R49 → текст + align только с Whisper этого фрагмента."""
    disp = tokenize_display(text)
    if not disp:
        return None
    lower = [t.lower() for t in disp]
    indices = align_cell_to_local_words(disp, local_words)
    return FrameWordSpan(frame_number, disp, lower, indices)


def build_frame_word_spans_per_frame(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    frame_timings: list[FrameTiming],
) -> list[FrameWordSpan]:
    """Каждая ячейка R49 обрабатывается отдельно; whisper_indices — локальные."""
    by_number = {t.frame_number: t for t in frame_timings}
    spans: list[FrameWordSpan] = []

    for frame_number, text in cells:
        timing = by_number.get(frame_number)
        if timing is None:
            continue
        local_words = extract_local_frame_words(
            words, timing.start_ts, timing.end_ts,
        )
        span = build_frame_word_span_for_cell(frame_number, text, local_words)
        if span is not None:
            spans.append(span)

    return spans


def map_frames(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    *,
    audio_duration: float | None = None,
) -> list[FrameTiming]:
    """Таймкоды кадров: веса из Whisper, шкала под длительность mp3, без дыр."""
    spans = build_frame_word_spans(cells, words)
    raw: list[FrameTiming] = []

    for span in spans:
        if not span.lower_words or not span.whisper_indices:
            raw.append(
                FrameTiming(span.frame_number, 0.0, 0.0, max(len(span.lower_words), 1) * 0.15)
            )
            continue
        wi_start = min(span.whisper_indices)
        wi_end = max(span.whisper_indices)
        wi_start = max(0, min(wi_start, len(words) - 1))
        wi_end = max(0, min(wi_end, len(words) - 1))
        start = words[wi_start].start
        end = words[wi_end].end
        dur = max(end - start, 0.0)
        if dur <= 0:
            dur = max(len(span.lower_words), 1) * 0.15
        raw.append(
            FrameTiming(
                span.frame_number,
                round(start, 3),
                round(end, 3),
                round(dur, 3),
            )
        )

    if audio_duration is None:
        return raw
    return normalize_contiguous(raw, audio_duration)


def normalize_contiguous(timings: list[FrameTiming], audio_duration: float) -> list[FrameTiming]:
    """Склеивает кадры подряд на [0, audio_duration] пропорционально весам Whisper."""
    if not timings:
        return []

    audio_duration = max(float(audio_duration), 0.01)
    weights = [max(t.duration, 0.0) for t in timings]
    if sum(weights) <= 0:
        weights = [1.0] * len(timings)

    total_weight = sum(weights)
    pos = 0.0
    out: list[FrameTiming] = []
    for timing, weight in zip(timings, weights):
        dur = (weight / total_weight) * audio_duration
        out.append(
            FrameTiming(
                timing.frame_number,
                round(pos, 3),
                round(pos + dur, 3),
                round(dur, 3),
            )
        )
        pos += dur

    out[-1].end_ts = round(audio_duration, 3)
    out[-1].duration = round(out[-1].end_ts - out[-1].start_ts, 3)
    for i in range(1, len(out)):
        out[i].start_ts = out[i - 1].end_ts
        out[i].duration = round(out[i].end_ts - out[i].start_ts, 3)
    return out
