"""Сопоставление текста кадров (Excel) с word-level таймкодами Whisper."""

from __future__ import annotations

import difflib
import re
import statistics
from dataclasses import dataclass

from loguru import logger

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
    """Индексы local_words для каждого слова ячейки (порядок сохраняется)."""
    if not display_words:
        return []
    if not local_words:
        return []

    lower = [t.lower() for t in display_words]
    n, m = len(lower), len(local_words)

    # Один к одному по порядку — типичный случай TTS + Whisper
    if n == m:
        return list(range(m))

    # Жадное сопоставление по тексту вперёд по потоку Whisper
    indices: list[int] = []
    j = 0
    for token in lower:
        matched = None
        for k in range(j, min(j + 4, m)):
            if whisper_token(local_words[k]) == token:
                matched = k
                break
        if matched is None:
            matched = min(j, m - 1)
        indices.append(matched)
        j = min(matched + 1, m)

    if len(set(indices)) >= max(1, (n + 1) // 2):
        return indices

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


def _timings_proportional_to_tokens(
    spans: list[FrameWordSpan],
    audio_duration: float,
    *,
    uniform: bool = False,
) -> list[FrameTiming]:
    """Распределение длительностей по числу слов в ячейках R49 (или поровну)."""
    if uniform:
        weights = [1.0] * len(spans)
    else:
        weights = [max(len(s.lower_words), 1) for s in spans]
    raw = [
        FrameTiming(s.frame_number, 0.0, 0.0, float(w))
        for s, w in zip(spans, weights)
    ]
    return normalize_contiguous(raw, audio_duration)


def _segment_durations_from_transitions(
    spans: list[FrameWordSpan],
    words: list[WordTS],
    audio_duration: float,
) -> list[float]:
    """Длительности кадров по точкам начала следующего кадра в Whisper."""
    transitions = [0.0]
    for i in range(1, len(spans)):
        span = spans[i]
        if span.whisper_indices and words:
            wi = max(0, min(min(span.whisper_indices), len(words) - 1))
            transitions.append(float(words[wi].start))
        else:
            transitions.append(transitions[-1])
    transitions.append(float(audio_duration))

    for i in range(1, len(transitions)):
        if transitions[i] < transitions[i - 1]:
            transitions[i] = transitions[i - 1]

    return [
        max(transitions[i + 1] - transitions[i], 0.0)
        for i in range(len(spans))
    ]


def _should_use_token_proportional(
    spans: list[FrameWordSpan],
    words: list[WordTS],
    segments: list[float],
    audio_duration: float,
) -> bool:
    if not spans:
        return True
    empty = sum(1 for s in spans if not s.lower_words)
    if empty > max(1, len(spans) // 4):
        return True
    if not words:
        return True
    whisper_end = float(words[-1].end)
    if whisper_end < audio_duration * 0.75:
        return True
    if len(segments) < 2:
        return False
    # Много нулевых/крошечных сегментов = схлопнутый align (одинаковые whisper-индексы).
    tiny = sum(1 for s in segments if s < 0.1)
    if tiny > max(1, len(segments) // 10):
        return True
    mx = max(segments)
    if mx > audio_duration * 0.35:
        return True
    if len(segments) >= 3:
        med = statistics.median(segments)
        if med > 0 and mx > med * 4:
            return True
    return False


def timings_have_crumb_durations(
    timings: list[FrameTiming],
    *,
    crumb_s: float = 0.1,
    max_fraction: float = 0.05,
    max_absolute: int = 2,
) -> bool:
    """True если слишком много кадров с длительностью ≤ crumb_s (сломанный ASR→R15)."""
    if not timings:
        return False
    crumbs = sum(1 for t in timings if float(t.duration) <= crumb_s + 1e-9)
    if crumbs <= max_absolute:
        return False
    return crumbs > max(max_absolute, int(len(timings) * max_fraction))


def count_crumb_frames(
    timings: list[FrameTiming],
    *,
    crumb_s: float = 0.1,
) -> int:
    return sum(1 for t in timings if float(t.duration) <= crumb_s + 1e-9)


def timings_from_word_transitions(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Contiguous по стартам ASR-слов; схлопы (одинаковый start) делим по весу R49.

    Не оставляет нулевых/крошечных кадров: группа с одним start режется
    по числу токенов до следующего уникального старта (или master).
    """
    spans = build_frame_word_spans(cells, words)
    if not spans:
        return []
    ad = max(float(master), 0.01)

    raw_starts: list[float] = []
    prev = 0.0
    for span in spans:
        if span.whisper_indices and words:
            wi = max(0, min(min(span.whisper_indices), len(words) - 1))
            s = float(words[wi].start)
            s = max(s, prev)
        else:
            s = prev
        raw_starts.append(s)
        prev = s

    starts = list(raw_starts)
    ends = [0.0] * len(spans)
    i = 0
    n = len(spans)
    while i < n:
        j = i + 1
        while j < n and abs(starts[j] - starts[i]) < 1e-4:
            j += 1
        group_start = starts[i]
        group_end = starts[j] if j < n else ad
        if group_end <= group_start + 1e-6:
            group_end = ad if j >= n else min(ad, group_start + 0.5)
        weights = [float(max(len(spans[k].lower_words), 1)) for k in range(i, j)]
        total_w = sum(weights) or float(len(weights))
        pos = group_start
        for k, w in zip(range(i, j), weights):
            dur = (w / total_w) * (group_end - group_start)
            starts[k] = pos
            ends[k] = pos + dur
            pos += dur
        ends[j - 1] = group_end
        for k in range(i, j):
            starts[k] = round(starts[k], 3)
            ends[k] = round(ends[k], 3)
        i = j

    out: list[FrameTiming] = []
    for span, s, e in zip(spans, starts, ends):
        if e < s:
            e = s
        out.append(
            FrameTiming(
                span.frame_number,
                round(s, 3),
                round(e, 3),
                round(e - s, 3),
            )
        )
    if not out:
        return out
    # Покрытие [0, master] без дыр.
    out[0] = FrameTiming(out[0].frame_number, 0.0, out[0].end_ts, round(out[0].end_ts, 3))
    out[-1] = FrameTiming(
        out[-1].frame_number,
        out[-1].start_ts,
        round(ad, 3),
        round(ad - out[-1].start_ts, 3),
    )
    for i in range(1, len(out)):
        out[i] = FrameTiming(
            out[i].frame_number,
            out[i - 1].end_ts,
            out[i].end_ts,
            round(out[i].end_ts - out[i - 1].end_ts, 3),
        )
    out[-1] = FrameTiming(
        out[-1].frame_number,
        out[-1].start_ts,
        round(ad, 3),
        round(ad - out[-1].start_ts, 3),
    )
    return out


def heal_timings_if_crumbs(
    timings: list[FrameTiming],
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Если после direct остались крошки — contiguous с разрезом схлопов."""
    if not timings or not words:
        return timings
    before = count_crumb_frames(timings)
    if before == 0:
        return timings
    healed = timings_from_word_transitions(cells, words, master)
    if not healed:
        return timings
    after = count_crumb_frames(healed)
    if after < before:
        logger.info(
            "heal_timings_if_crumbs: {} → {} crumbs≤0.1s (contiguous split)",
            before,
            after,
        )
        return healed
    return timings


def map_frames(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    *,
    audio_duration: float | None = None,
) -> list[FrameTiming]:
    """Таймкоды кадров: границы по Whisper, без «последний кадр на 5 минут»."""
    spans = build_frame_word_spans(cells, words)
    if not spans:
        return []

    if audio_duration is None:
        raw: list[FrameTiming] = []
        for span in spans:
            if not span.lower_words or not span.whisper_indices or not words:
                raw.append(
                    FrameTiming(
                        span.frame_number,
                        0.0,
                        0.0,
                        max(len(span.lower_words), 1) * 0.15,
                    )
                )
                continue
            wi_start = max(0, min(min(span.whisper_indices), len(words) - 1))
            wi_end = max(0, min(max(span.whisper_indices), len(words) - 1))
            start = words[wi_start].start
            end = words[wi_end].end
            dur = max(end - start, max(len(span.lower_words), 1) * 0.15)
            raw.append(
                FrameTiming(
                    span.frame_number,
                    round(start, 3),
                    round(end, 3),
                    round(dur, 3),
                )
            )
        return raw

    ad = max(float(audio_duration), 0.01)
    empty = sum(1 for s in spans if not s.lower_words)
    if empty > max(1, len(spans) // 4):
        return _timings_proportional_to_tokens(spans, ad, uniform=True)

    segments = _segment_durations_from_transitions(spans, words, ad)
    if _should_use_token_proportional(spans, words, segments, ad):
        logger.warning(
            "map_frames: proportional fallback ({} cells, {} words, whisper_end={:.1f}s, audio={:.1f}s)",
            len(spans),
            len(words),
            float(words[-1].end) if words else 0.0,
            ad,
        )
        return _timings_proportional_to_tokens(spans, ad)

    out: list[FrameTiming] = []
    pos = 0.0
    for span, seg_dur in zip(spans, segments):
        end = pos + seg_dur
        out.append(
            FrameTiming(
                span.frame_number,
                round(pos, 3),
                round(end, 3),
                round(seg_dur, 3),
            )
        )
        pos = end

    out[-1].end_ts = round(ad, 3)
    out[-1].duration = round(out[-1].end_ts - out[-1].start_ts, 3)
    for i in range(1, len(out)):
        out[i].start_ts = out[i - 1].end_ts
        out[i].duration = round(out[i].end_ts - out[i].start_ts, 3)
    return out


def enforce_monotonic_timings(
    timings: list[FrameTiming],
    *,
    master: float | None = None,
    min_duration: float = 0.05,
) -> list[FrameTiming]:
    """R15/overlay: start каждого кадра >= end предыдущего (без overlap назад).

    Не складывает цепочки min_duration (0.05s) при сдвиге overlap —
    если исходный end уже позади нового start, кадр занимает до старта
    следующего кадра (сохраняет речь, без крошек 0.05–0.1s в Excel).
    """
    if not timings:
        return []
    sorted_t = sorted(timings, key=lambda t: t.frame_number)
    starts: list[float] = []
    prev_start = 0.0
    fixed = 0
    for t in sorted_t:
        # Монотонные старты по ASR (не по end — иначе каскад сдвигов).
        start = max(float(t.start_ts), prev_start)
        if start > float(t.start_ts) + 0.001:
            fixed += 1
        starts.append(start)
        prev_start = start

    out: list[FrameTiming] = []
    for i, t in enumerate(sorted_t):
        start = starts[i]
        orig_end = float(t.end_ts)
        if i + 1 < len(starts):
            next_start = starts[i + 1]
            if next_start <= start + 1e-9:
                # Схлопнутый старт — нулевая длина (не +min_duration: ломает
                # start_next >= end_prev и плодит крошки). Remap выше по стеку.
                end = start
            elif orig_end > start + 0.001 and orig_end <= next_start + 0.001:
                end = orig_end
            else:
                # Overlap / end позади: до следующего ASR-старта, не +0.05.
                end = next_start
        else:
            end = max(orig_end, start + min_duration)
        out.append(
            FrameTiming(
                t.frame_number,
                round(start, 3),
                round(end, 3),
                round(end - start, 3),
            )
        )
    if master is not None and out:
        m = max(float(master), float(out[-1].end_ts))
        last = out[-1]
        if last.end_ts < m - 0.01:
            out[-1] = FrameTiming(
                last.frame_number,
                last.start_ts,
                round(m, 3),
                round(m - last.start_ts, 3),
            )
        # Если после stretch по master всё ещё каскад крошек — не чиним здесь
        # (нужен map_frames с audio_duration / proportional); только лог.
        if timings_have_crumb_durations(out):
            logger.warning(
                "map_frames: enforce_monotonic — много коротких кадров "
                "(≤0.1s); нужен contiguous/proportional remap"
            )
    if fixed:
        logger.info(
            "map_frames: enforce_monotonic — сдвинуто {} кадров (overlap ASR→R15)",
            fixed,
        )
    return out


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
