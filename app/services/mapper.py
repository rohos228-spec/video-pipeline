"""Сопоставление текста кадров (Excel) с word-level таймкодами Whisper."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from loguru import logger

from app.services.whisper import WordTS

_WORD_RE = re.compile(r"[^\wа-яА-ЯёЁ]+", re.UNICODE)
_DISPLAY_RE = re.compile(r"[\wа-яА-ЯёЁ]+", re.UNICODE)
_CYR_RE = re.compile(r"[а-яё]", re.IGNORECASE)


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


def _match_cell_tokens_forward(
    lower_tokens: list[str],
    words: list[WordTS],
    cursor: int,
    *,
    max_lookahead: int = 15,
) -> list[int]:
    """Жадное сопоставление вперёд — без difflib-«мостов» через пол-ролика."""
    if not lower_tokens:
        return []
    indices: list[int] = []
    j = max(0, min(cursor, len(words) - 1))
    for token in lower_tokens:
        found: int | None = None
        limit = min(j + max_lookahead, len(words))
        for k in range(j, limit):
            wt = whisper_token(words[k])
            if wt == token or (wt and token and (token in wt or wt in token)):
                found = k
                break
        if found is None:
            found = min(j, len(words) - 1)
        indices.append(found)
        j = min(found + 1, len(words))
    return indices


def _forward_match_ratio(lower: list[str], indices: list[int], words: list[WordTS]) -> float:
    if not lower or not indices:
        return 0.0
    hits = 0
    for token, wi in zip(lower, indices):
        wt = whisper_token(words[wi])
        if wt == token or (wt and token and (token in wt or wt in token)):
            hits += 1
    return hits / len(lower)


def enforce_monotonic_spans(
    spans: list[FrameWordSpan],
    words: list[WordTS],
) -> list[FrameWordSpan]:
    """После global difflib: индексы ASR только вперёд, без прыжков на минуты."""
    if not words:
        return spans
    cursor = 0
    last = len(words) - 1
    out: list[FrameWordSpan] = []
    for span in spans:
        if not span.whisper_indices:
            out.append(span)
            continue
        idxs = list(span.whisper_indices)
        lo, hi = min(idxs), max(idxs)
        if lo < cursor:
            shift = cursor - lo
            idxs = [min(i + shift, last) for i in idxs]
            lo, hi = min(idxs), max(idxs)
        max_width = max(len(span.lower_words) * 3, 12)
        if hi - lo > max_width:
            hi = min(lo + max_width, last)
            idxs = [min(max(i, lo), hi) for i in idxs]
        cursor = min(max(idxs) + 1, len(words))
        out.append(
            FrameWordSpan(span.frame_number, span.display_words, span.lower_words, idxs)
        )
    return out


def build_frame_word_spans_for_montage(
    cells: list[tuple[int, str]],
    words: list[WordTS],
) -> list[FrameWordSpan]:
    """Весь R49 × ASR одним difflib + монотонные индексы (144 кадра)."""
    spans = build_frame_word_spans(cells, words)
    return enforce_monotonic_spans(spans, words)


def build_frame_word_spans_sequential(
    cells: list[tuple[int, str]],
    words: list[WordTS],
) -> list[FrameWordSpan]:
    """Каждая ячейка R49 матчится только с ASR после предыдущего кадра."""
    spans: list[FrameWordSpan] = []
    cursor = 0

    for frame_number, text in cells:
        disp = tokenize_display(text)
        lower = [t.lower() for t in disp]
        if not lower:
            spans.append(FrameWordSpan(frame_number, [], [], []))
            continue

        tail = words[cursor:] if cursor < len(words) else []
        indices: list[int]
        if tail:
            forward = _match_cell_tokens_forward(lower, words, cursor)
            if _forward_match_ratio(lower, forward, words) >= 0.45:
                indices = forward
            else:
                local_alignment = align_script_tokens(lower, tail)
                indices = [cursor + wi for wi in local_alignment]
                span_len = max(indices) - min(indices) if indices else 0
                if span_len > max(len(lower) * 4, 20):
                    indices = _match_cell_tokens_forward(lower, words, cursor)
        else:
            indices = []

        if indices:
            cursor = min(max(indices) + 1, len(words))
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


def _proportional_timeline(
    cells: list[tuple[int, str]],
    audio_duration: float,
) -> list[FrameTiming]:
    raw: list[FrameTiming] = []
    for frame_number, text in cells:
        w = max(len(tokenize_display(text)), 1)
        raw.append(FrameTiming(frame_number, 0.0, 0.0, float(w)))
    return normalize_contiguous(raw, audio_duration)


def _script_mismatch(cells: list[tuple[int, str]], words: list[WordTS]) -> bool:
    """RU текст R49 + EN ASR (Parakeet) — difflib-sync бессмысленен."""
    if not cells or not words:
        return False
    cell_cyr = any(_CYR_RE.search(text or "") for _, text in cells)
    if not cell_cyr:
        return False
    asr_cyr = sum(1 for w in words if _CYR_RE.search(w.word or ""))
    return asr_cyr < max(3, len(words) // 10)


def map_frames(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    *,
    audio_duration: float | None = None,
) -> list[FrameTiming]:
    """Таймкоды кадров: границы по ASR, видео до начала следующей ячейки."""
    spans = build_frame_word_spans_for_montage(cells, words)
    if audio_duration is None:
        return _raw_frame_timings(spans, words)

    if _script_mismatch(cells, words):
        logger.warning(
            "mapper: RU plan + латиница ASR — пропорциональный монтаж по R49 "
            "({:.1f}s)",
            audio_duration,
        )
        return _proportional_timeline(cells, audio_duration)

    return build_voice_aligned_timeline(spans, words, audio_duration)


def _estimate_cell_duration(word_count: int) -> float:
    return max(word_count, 1) * 0.35


def _raw_frame_timings(
    spans: list[FrameWordSpan],
    words: list[WordTS],
) -> list[FrameTiming]:
    raw: list[FrameTiming] = []
    for span in spans:
        if not span.lower_words or not span.whisper_indices or not words:
            dur = _estimate_cell_duration(len(span.lower_words))
            raw.append(FrameTiming(span.frame_number, 0.0, 0.0, dur))
            continue
        wi_start = max(0, min(min(span.whisper_indices), len(words) - 1))
        wi_end = max(0, min(max(span.whisper_indices), len(words) - 1))
        start = words[wi_start].start
        end = words[wi_end].end
        dur = max(end - start, 0.0) or _estimate_cell_duration(len(span.lower_words))
        raw.append(
            FrameTiming(
                span.frame_number,
                round(start, 3),
                round(end, 3),
                round(dur, 3),
            )
        )
    return raw


def _first_word_start(span: FrameWordSpan, words: list[WordTS]) -> float | None:
    if not span.whisper_indices or not words:
        return None
    wi = max(0, min(min(span.whisper_indices), len(words) - 1))
    return words[wi].start


def _last_word_end(span: FrameWordSpan, words: list[WordTS]) -> float | None:
    if not span.whisper_indices or not words:
        return None
    wi = max(0, min(max(span.whisper_indices), len(words) - 1))
    return words[wi].end


def build_whisper_sync_timeline(
    spans: list[FrameWordSpan],
    words: list[WordTS],
    audio_duration: float,
) -> list[FrameTiming]:
    """Legacy alias — voice-aligned boundaries on the voice track."""
    return build_voice_aligned_timeline(spans, words, audio_duration)


def build_voice_aligned_timeline(
    spans: list[FrameWordSpan],
    words: list[WordTS],
    audio_duration: float,
) -> list[FrameTiming]:
    """Кадр i: [первое слово ячейки i, первое слово ячейки i+1). Хвост — до конца mp3."""
    if not spans:
        return []

    audio_duration = max(float(audio_duration), 0.01)
    min_gap = 0.05
    n = len(spans)

    starts: list[float] = []
    for span in spans:
        fs = _first_word_start(span, words)
        if fs is not None:
            starts.append(float(fs))
        elif starts:
            starts.append(starts[-1] + min_gap)
        else:
            starts.append(0.0)

    for i in range(1, n):
        if starts[i] < starts[i - 1] + min_gap:
            starts[i] = starts[i - 1] + min_gap

    out: list[FrameTiming] = []
    for i, span in enumerate(spans):
        start = starts[i]
        end = starts[i + 1] if i + 1 < n else audio_duration
        end = max(end, start + min_gap)
        if i == n - 1:
            end = audio_duration
        out.append(
            FrameTiming(
                span.frame_number,
                round(start, 3),
                round(end, 3),
                round(end - start, 3),
            )
        )

    mapped_span = sum(t.duration for t in out)
    avg = mapped_span / max(len(out), 1)
    long_clips = [t for t in out if t.duration > max(avg * 4, 30.0)]
    if long_clips:
        sample = ", ".join(f"#{t.frame_number}({t.duration:.1f}s)" for t in long_clips[:5])
        logger.warning(
            "mapper: длинные клипы (>{:.0f}s ср.): {}{}",
            max(avg * 4, 30.0),
            sample,
            f" (+{len(long_clips) - 5})" if len(long_clips) > 5 else "",
        )
    logger.debug(
        "mapper: voice-aligned {} frames, span {:.1f}s / audio {:.1f}s",
        len(out),
        mapped_span,
        audio_duration,
    )
    return out


def build_absolute_asr_timeline(
    spans: list[FrameWordSpan],
    words: list[WordTS],
    audio_duration: float,
) -> list[FrameTiming]:
    """Legacy: непрерывная шкала с 0:00 (не использовать для монтажа)."""
    if not spans:
        return []

    audio_duration = max(float(audio_duration), 0.01)
    min_gap = 0.05
    n = len(spans)
    last_wi = max(len(words) - 1, 0)

    first_indices: list[int] = []
    cursor = 0
    for span in spans:
        if span.whisper_indices and words:
            fi = max(min(span.whisper_indices), cursor)
            fi = min(fi, last_wi)
            first_indices.append(fi)
            cursor = min(max(span.whisper_indices) + 1, len(words))
        else:
            first_indices.append(min(cursor, last_wi))
            cursor = min(cursor + 1, len(words))

    # n+1 границ: b[0]=0, b[i]=ASR start i-й ячейки, b[n]=конец mp3
    bounds = [0.0] * (n + 1)
    bounds[0] = 0.0
    for i in range(1, n):
        wi = min(first_indices[i], last_wi)
        bounds[i] = words[wi].start if words else 0.0
    bounds[n] = audio_duration

    for i in range(1, n + 1):
        bounds[i] = max(bounds[i], bounds[i - 1] + min_gap)
    bounds[n] = audio_duration
    for i in range(n - 1, -1, -1):
        bounds[i] = min(bounds[i], bounds[i + 1] - min_gap)
    bounds[0] = 0.0
    bounds[n] = audio_duration

    out: list[FrameTiming] = []
    for i, span in enumerate(spans):
        start, end = bounds[i], bounds[i + 1]
        dur = max(end - start, min_gap)
        out.append(
            FrameTiming(
                span.frame_number,
                round(start, 3),
                round(end, 3),
                round(dur, 3),
            )
        )

    mapped_span = sum(t.duration for t in out)
    bad = [t for t in out if t.duration <= 0]
    if bad:
        logger.warning(
            "mapper: {} frames with non-positive duration",
            len(bad),
        )
    avg = mapped_span / max(len(out), 1)
    long_clips = [t for t in out if t.duration > max(avg * 4, 30.0)]
    if long_clips:
        sample = ", ".join(f"#{t.frame_number}({t.duration:.1f}s)" for t in long_clips[:5])
        logger.warning(
            "mapper: длинные клипы (>{:.0f}s ср.): {}{}",
            max(avg * 4, 30.0),
            sample,
            f" (+{len(long_clips) - 5})" if len(long_clips) > 5 else "",
        )

    logger.debug(
        "mapper: ASR timeline {} frames, span {:.1f}s / audio {:.1f}s, avg {:.2f}s/frame",
        len(out),
        mapped_span,
        audio_duration,
        avg,
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
    durs = [(weight / total_weight) * audio_duration for weight in weights]
    if len(durs) > 1:
        durs[-1] = audio_duration - sum(durs[:-1])
    else:
        durs[0] = audio_duration

    pos = 0.0
    out: list[FrameTiming] = []
    for idx, (timing, dur) in enumerate(zip(timings, durs)):
        if idx == len(timings) - 1:
            end = audio_duration
        else:
            end = pos + dur
        dur = end - pos
        out.append(
            FrameTiming(
                timing.frame_number,
                round(pos, 3),
                round(end, 3),
                round(dur, 3),
            )
        )
        pos = end

    return out
