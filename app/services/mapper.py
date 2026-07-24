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
    """Индекс ASR-слова для каждого токена сценария.

    Важно: токены, которых нет в озвучке (insert в difflib), остаются -1.
    Их нельзя сажать на первое найденное слово — иначе весь таймлайн ползёт.
    """
    if not script_tokens:
        return []
    if not words:
        return [-1] * len(script_tokens)

    whisper_tokens = [whisper_token(w) for w in words]
    result = [-1] * len(script_tokens)
    matched = [False] * len(script_tokens)
    matcher = difflib.SequenceMatcher(None, script_tokens, whisper_tokens, autojunk=False)

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                result[i1 + offset] = j1 + offset
                matched[i1 + offset] = True
            continue
        if tag == "replace":
            script_len = i2 - i1
            whisper_len = max(j2 - j1, 0)
            if script_len <= 0 or whisper_len <= 0:
                continue
            # Нечёткое совпадение блока — пропорционально внутри блока.
            for offset in range(script_len):
                wi = j1 + min(int(offset * whisper_len / script_len), whisper_len - 1)
                result[i1 + offset] = wi
            continue
        # insert (есть в сценарии, нет в ASR) / delete (есть в ASR, нет в сценарии):
        # insert → оставляем -1; delete игнорируем.
        continue

    # Интерполяция ТОЛЬКО между двумя matched-якорями (дыры внутри речи).
    # Краевые insert (интро/аутро которых нет в аудио) остаются -1.
    known = [(i, result[i]) for i, ok in enumerate(matched) if ok]
    for (a_i, a_w), (b_i, b_w) in zip(known, known[1:]):
        gap = b_i - a_i
        if gap <= 1:
            continue
        for k in range(1, gap):
            idx = a_i + k
            if matched[idx]:
                continue
            if result[idx] >= 0:
                continue
            t = k / gap
            result[idx] = int(round(a_w + t * (b_w - a_w)))

    # Монотонность только среди назначенных (≥0).
    last = -1
    for i, wi in enumerate(result):
        if wi < 0:
            continue
        wi = max(0, min(int(wi), len(words) - 1))
        if last >= 0 and wi < last:
            wi = last
        result[i] = wi
        last = wi
    return result


def exclusive_asr_word_bounds(
    cells: list[tuple[int, str]],
    words: list[WordTS],
) -> list[tuple[int, int, int]]:
    """Для каждого кадра — полуинтервал ASR-слов [lo, hi).

    Кадры без совпадения с озвучкой (интро/текст не из audio) получают
    lo=hi=-1 и НЕ забирают ASR-слова у следующих кадров.
    """
    if not cells:
        return []
    spans = build_frame_word_spans(cells, words)
    n = len(spans)
    w_n = len(words)
    if w_n <= 0:
        return [(s.frame_number, -1, -1) for s in spans]

    # Предпочтительный [lo, hi) только по реально сопоставленным токенам (≥0).
    pref_lo: list[int] = []
    pref_hi: list[int] = []
    has_match: list[bool] = []
    for span in spans:
        hit = [i for i in (span.whisper_indices or []) if i >= 0]
        if hit:
            pref_lo.append(max(0, min(min(hit), w_n - 1)))
            pref_hi.append(max(0, min(max(hit) + 1, w_n)))
            has_match.append(True)
        else:
            pref_lo.append(-1)
            pref_hi.append(-1)
            has_match.append(False)

    matched_idx = [i for i, ok in enumerate(has_match) if ok]
    if not matched_idx:
        # Ничего не сопоставилось — равномерно по всем словам.
        starts = [min(int(i * w_n / n), w_n - 1) for i in range(n)]
        ends = [starts[i + 1] if i + 1 < n else w_n for i in range(n)]
        return [(spans[i].frame_number, starts[i], ends[i]) for i in range(n)]

    # Exclusive starts только среди matched-кадров.
    m_starts: dict[int, int] = {}
    first = matched_idx[0]
    m_starts[first] = max(0, min(pref_lo[first], max(w_n - len(matched_idx), 0)))
    for k in range(1, len(matched_idx)):
        i = matched_idx[k]
        prev = matched_idx[k - 1]
        remain = len(matched_idx) - k
        max_start = max(0, w_n - remain)
        want = max(pref_lo[i], m_starts[prev] + 1)
        m_starts[i] = min(want, max_start)
        if m_starts[i] <= m_starts[prev]:
            m_starts[i] = min(m_starts[prev] + 1, max_start)

    m_ends: dict[int, int] = {}
    for k, i in enumerate(matched_idx):
        if k + 1 < len(matched_idx):
            nxt = matched_idx[k + 1]
            m_ends[i] = max(m_starts[i] + 1, m_starts[nxt])
        else:
            m_ends[i] = w_n
        # Не режем preferred hi, если он внутри своего слота
        if pref_hi[i] > m_starts[i] and pref_hi[i] <= m_ends[i]:
            pass

    out: list[tuple[int, int, int]] = []
    for i, span in enumerate(spans):
        if not has_match[i]:
            out.append((span.frame_number, -1, -1))
        else:
            out.append((span.frame_number, m_starts[i], m_ends[i]))
    return out


def timings_match_voiceover(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
    *,
    mode: str = "contiguous",
) -> list[FrameTiming]:
    """Таймкоды кадров строго по словам озвучки (NeMo).

    Кадры без текста в аудио (интро и т.п.) не едят ASR-слова:
    они получают паузу до первой реальной речи / стык без сдвига хвоста.
    """
    if not cells:
        return []
    ad = max(float(master), 0.01)
    if not words:
        raw = [
            FrameTiming(fn, 0.0, 0.0, float(max(len(tokenize_lower(text)), 1)))
            for fn, text in cells
        ]
        return normalize_contiguous(raw, ad)

    bounds = exclusive_asr_word_bounds(cells, words)
    speech1 = float(words[-1].end)

    # Временные метки matched-кадров по словам; unmatched — None.
    raw_se: list[tuple[float, float] | None] = [None] * len(bounds)
    for i, (fn, lo, hi) in enumerate(bounds):
        if lo < 0 or hi < 0 or hi <= lo:
            continue
        lo_i = max(0, min(lo, len(words) - 1))
        hi_i = max(lo_i + 1, min(hi, len(words)))
        if mode == "direct":
            start = float(words[lo_i].start)
            end = float(words[hi_i - 1].end)
        else:
            start = float(words[lo_i].start)
            # конец = старт следующего matched
            end = float(words[hi_i - 1].end)
            for j in range(i + 1, len(bounds)):
                nlo, nhi = bounds[j][1], bounds[j][2]
                if nlo >= 0 and nhi > nlo:
                    nlo_i = max(0, min(nlo, len(words) - 1))
                    end = float(words[nlo_i].start)
                    break
            if end <= start:
                end = float(words[hi_i - 1].end)
        raw_se[i] = (start, end)

    # Unmatched: ведущие → [0, first_speech); хвостовые → [speech1, master];
    # середина → нулевая точка на стыке (потом absorb/склейка).
    # Важно: преролл до старта ПЕРВОГО matched-кадра, не words[0] —
    # иначе glue сдвинет речь, если в ASR есть «лишние» слова до текста.
    first_matched = next((i for i, se in enumerate(raw_se) if se is not None), None)
    last_matched = next(
        (i for i in range(len(raw_se) - 1, -1, -1) if raw_se[i] is not None),
        None,
    )
    first_speech = (
        float(raw_se[first_matched][0]) if first_matched is not None else 0.0
    )
    out: list[FrameTiming] = []
    for i, (fn, _lo, _hi) in enumerate(bounds):
        se = raw_se[i]
        if se is not None:
            start, end = se
        elif first_matched is not None and i < first_matched:
            # Интро не из аудио — только тишина/преролл до первой речи.
            lead = [k for k in range(first_matched) if raw_se[k] is None]
            if len(lead) > 1 and first_speech > 0.05:
                pos = lead.index(i)
                step = first_speech / len(lead)
                start = round(pos * step, 3)
                end = round((pos + 1) * step, 3)
            elif lead and i == lead[0] and first_speech > 0.05:
                start, end = 0.0, first_speech
            else:
                start = end = first_speech
        elif last_matched is not None and i > last_matched:
            tail = [k for k in range(last_matched + 1, len(bounds)) if raw_se[k] is None]
            if len(tail) > 0 and ad - speech1 > 0.05:
                pos = tail.index(i)
                step = (ad - speech1) / len(tail)
                start = round(speech1 + pos * step, 3)
                end = round(speech1 + (pos + 1) * step, 3)
            else:
                start = end = speech1
        else:
            # Unmatched в середине — точка на предыдущем/следующем стыке
            prev_end = 0.0
            for j in range(i - 1, -1, -1):
                if raw_se[j] is not None:
                    prev_end = raw_se[j][1]
                    break
            start = end = prev_end
        out.append(
            FrameTiming(fn, round(start, 3), round(end, 3), round(max(end - start, 0.0), 3))
        )

    # Покрытие [0, master] без дыр; крошки — absorb.
    if out:
        out[0] = FrameTiming(out[0].frame_number, 0.0, out[0].end_ts, round(out[0].end_ts, 3))
        for i in range(1, len(out)):
            out[i] = FrameTiming(
                out[i].frame_number,
                out[i - 1].end_ts,
                max(out[i].end_ts, out[i - 1].end_ts),
                round(max(out[i].end_ts, out[i - 1].end_ts) - out[i - 1].end_ts, 3),
            )
        out[-1] = FrameTiming(
            out[-1].frame_number,
            out[-1].start_ts,
            round(ad, 3),
            round(ad - out[-1].start_ts, 3),
        )
        if count_crumb_frames(out) > 0:
            out = absorb_crumb_durations(out, ad)
    return out


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
        hit = [i for i in (span.whisper_indices or []) if i >= 0]
        if hit and words:
            wi = max(0, min(min(hit), len(words) - 1))
            s = float(words[wi].start)
            s = max(s, prev)
        else:
            # Unmatched (интро не из аудио) — не крадём words[0].
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
        # Если окно слишком узкое на N кадров — расширяем до следующего
        # уникального старта / master (иначе снова крошки).
        min_span = 0.2 * (j - i)
        if group_end - group_start < min_span - 1e-9:
            k = j
            while k < n and starts[k] - group_start < min_span:
                k += 1
            group_end = starts[k] if k < n else ad
            if group_end - group_start < min_span:
                group_end = min(ad, group_start + min_span)
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


def absorb_crumb_durations(
    timings: list[FrameTiming],
    master: float,
    *,
    crumb_s: float = 0.1,
    target_s: float = 0.2,
) -> list[FrameTiming]:
    """Оставшиеся крошки ≤crumb_s забирают время у соседних длинных кадров.

    Не выдумывает пол: только перераспределяет уже существующий master.
    """
    if not timings:
        return timings
    ad = max(float(master), 0.01)
    out = [
        FrameTiming(t.frame_number, float(t.start_ts), float(t.end_ts), float(t.duration))
        for t in sorted(timings, key=lambda x: x.frame_number)
    ]
    # Склеить в непрерывную ленту на [0, master]
    out[0].start_ts = 0.0
    for i in range(1, len(out)):
        out[i].start_ts = out[i - 1].end_ts
        out[i].duration = max(0.0, out[i].end_ts - out[i].start_ts)
    out[-1].end_ts = ad
    out[-1].duration = max(0.0, out[-1].end_ts - out[-1].start_ts)

    changed = False
    for _ in range(len(out) * 3):
        crumbs = [i for i, t in enumerate(out) if t.duration <= crumb_s + 1e-9]
        if not crumbs:
            break
        progress = False
        for i in crumbs:
            need = target_s - out[i].duration
            if need <= 1e-9:
                continue
            # Кандидаты: левый / правый сосед с запасом
            donors: list[tuple[int, float]] = []
            if i > 0 and out[i - 1].duration > target_s + 0.05:
                donors.append((i - 1, out[i - 1].duration - target_s))
            if i + 1 < len(out) and out[i + 1].duration > target_s + 0.05:
                donors.append((i + 1, out[i + 1].duration - target_s))
            if not donors:
                # крайний случай — любой сосед длиннее крошки
                if i > 0 and out[i - 1].duration > out[i].duration + 0.05:
                    donors.append((i - 1, out[i - 1].duration * 0.5))
                if i + 1 < len(out) and out[i + 1].duration > out[i].duration + 0.05:
                    donors.append((i + 1, out[i + 1].duration * 0.5))
            if not donors:
                continue
            donors.sort(key=lambda x: x[1], reverse=True)
            di, avail = donors[0]
            take = min(need, max(0.0, avail))
            if take <= 1e-9:
                continue
            if di < i:
                # двигаем границу: конец донора раньше → старт крошки раньше
                out[di].end_ts = round(out[di].end_ts - take, 3)
                out[di].duration = round(out[di].end_ts - out[di].start_ts, 3)
                out[i].start_ts = out[di].end_ts
                out[i].duration = round(out[i].end_ts - out[i].start_ts, 3)
            else:
                out[i].end_ts = round(out[i].end_ts + take, 3)
                out[i].duration = round(out[i].end_ts - out[i].start_ts, 3)
                out[di].start_ts = out[i].end_ts
                out[di].duration = round(out[di].end_ts - out[di].start_ts, 3)
            progress = True
            changed = True
        if not progress:
            break

    if count_crumb_frames(out, crumb_s=crumb_s) > 0:
        # Патологический случай (слов ASR << кадров): растянуть по весам max(dur, target).
        weights = [max(float(t.duration), target_s) for t in out]
        total_w = sum(weights) or float(len(weights))
        pos = 0.0
        rebuilt: list[FrameTiming] = []
        for t, w in zip(out, weights):
            dur = (w / total_w) * ad
            rebuilt.append(
                FrameTiming(
                    t.frame_number,
                    round(pos, 3),
                    round(pos + dur, 3),
                    round(dur, 3),
                )
            )
            pos += dur
        rebuilt[-1] = FrameTiming(
            rebuilt[-1].frame_number,
            rebuilt[-1].start_ts,
            round(ad, 3),
            round(ad - rebuilt[-1].start_ts, 3),
        )
        out = rebuilt

    # Финальная склейка без дыр/overlap
    out[0].start_ts = 0.0
    for i in range(1, len(out)):
        out[i].start_ts = out[i - 1].end_ts
    out[-1].end_ts = round(ad, 3)
    for t in out:
        t.duration = round(max(0.0, t.end_ts - t.start_ts), 3)
        t.start_ts = round(t.start_ts, 3)
        t.end_ts = round(t.end_ts, 3)

    if changed or count_crumb_frames(timings, crumb_s=crumb_s) > 0:
        logger.info(
            "absorb_crumb_durations: crumbs {} → {}",
            count_crumb_frames(timings, crumb_s=crumb_s),
            count_crumb_frames(out, crumb_s=crumb_s),
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


def finalize_align_timings(
    timings: list[FrameTiming],
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Гарантия: покрытие master + 0 крошек ≤0.1с (после heal + absorb)."""
    if not timings:
        return timings
    healed = heal_timings_if_crumbs(timings, cells, words, master)
    out = absorb_crumb_durations(healed, master)
    if count_crumb_frames(out) > 0 and words:
        out = absorb_crumb_durations(
            timings_from_word_transitions(cells, words, master),
            master,
        )
    if count_crumb_frames(out) > 0:
        spans = build_frame_word_spans(cells, words) if words else []
        if spans:
            out = absorb_crumb_durations(
                _timings_proportional_to_tokens(spans, master, uniform=False),
                master,
            )
        else:
            raw = [
                FrameTiming(fn, 0.0, 0.0, float(max(len(tokenize_lower(text)), 1)))
                for fn, text in cells
            ]
            out = absorb_crumb_durations(normalize_contiguous(raw, master), master)
    return out


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
