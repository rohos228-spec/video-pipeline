"""5 методик раскладки ASR-слов → таймкоды кадров (R15).

Одна и та же words.json / NeMo-транскрипция; меняется только способ
границ кадров. Для A/B в Studio («Разбор аудио»).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from loguru import logger

from app.services.mapper import (
    FrameTiming,
    build_frame_word_spans,
    enforce_monotonic_timings,
    map_frames,
    normalize_contiguous,
    _segment_durations_from_transitions,
    _timings_proportional_to_tokens,
)
from app.services.whisper import WordTS


@dataclass(frozen=True)
class AlignMethodInfo:
    id: str
    title: str
    summary: str


ALIGN_METHODS: tuple[AlignMethodInfo, ...] = (
    AlignMethodInfo(
        id="direct",
        title="1. Direct — слова ASR",
        summary="Старт/конец кадра = первое и последнее сопоставленное слово. Overlap чинит monotonic.",
    ),
    AlignMethodInfo(
        id="contiguous",
        title="2. Contiguous — до следующего старта",
        summary="Кадр занимает от старта своего первого слова до старта следующего кадра (без proportional).",
    ),
    AlignMethodInfo(
        id="proportional",
        title="3. Proportional — по числу слов",
        summary="Вся длина voice_full делится пропорционально числу слов в ячейке R49.",
    ),
    AlignMethodInfo(
        id="uniform",
        title="4. Uniform — поровну",
        summary="Все кадры получают одинаковую длительность на всю озвучку.",
    ),
    AlignMethodInfo(
        id="auto",
        title="5. Auto — текущий production",
        summary="Как generate_audio сейчас: direct, при крошках → contiguous/map_frames(audio_duration).",
    ),
)

_METHOD_IDS = {m.id for m in ALIGN_METHODS}


def list_align_methods() -> list[dict[str, str]]:
    return [
        {"id": m.id, "title": m.title, "summary": m.summary} for m in ALIGN_METHODS
    ]


def resolve_align_method(method_id: str) -> str:
    mid = (method_id or "").strip().lower()
    if mid not in _METHOD_IDS:
        known = ", ".join(sorted(_METHOD_IDS))
        raise ValueError(f"неизвестная методика {method_id!r}; доступны: {known}")
    return mid


def _timings_contiguous_forced(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Только переходы по старту первого слова кадра — без proportional fallback."""
    spans = build_frame_word_spans(cells, words)
    if not spans:
        return []
    ad = max(float(master), 0.01)
    segments = _segment_durations_from_transitions(spans, words, ad)
    out: list[FrameTiming] = []
    pos = 0.0
    for span, seg_dur in zip(spans, segments):
        end = pos + max(float(seg_dur), 0.0)
        out.append(
            FrameTiming(
                span.frame_number,
                round(pos, 3),
                round(end, 3),
                round(end - pos, 3),
            )
        )
        pos = end
    if out:
        out[-1].end_ts = round(ad, 3)
        out[-1].duration = round(out[-1].end_ts - out[-1].start_ts, 3)
        for i in range(1, len(out)):
            out[i].start_ts = out[i - 1].end_ts
            out[i].duration = round(out[i].end_ts - out[i].start_ts, 3)
    return out


def _method_direct(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    raw = map_frames(cells, words)
    return enforce_monotonic_timings(raw, master=master)


def _method_contiguous(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    return enforce_monotonic_timings(
        _timings_contiguous_forced(cells, words, master),
        master=master,
    )


def _method_proportional(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    spans = build_frame_word_spans(cells, words)
    if not spans:
        return []
    return enforce_monotonic_timings(
        _timings_proportional_to_tokens(spans, master, uniform=False),
        master=master,
    )


def _method_uniform(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    spans = build_frame_word_spans(cells, words)
    if not spans:
        # нет текста — поровну по номерам ячеек
        raw = [FrameTiming(fn, 0.0, 0.0, 1.0) for fn, _ in cells] or [
            FrameTiming(1, 0.0, 0.0, 1.0)
        ]
        return normalize_contiguous(raw, master)
    return enforce_monotonic_timings(
        _timings_proportional_to_tokens(spans, master, uniform=True),
        master=master,
    )


def _method_auto(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Зеркало frame_clips_from_whisper без Path."""
    from app.services.mapper import timings_have_crumb_durations

    direct = map_frames(cells, words)
    if direct and len(direct) == len(cells):
        good = sum(1 for t in direct if t.duration > 0.05)
        if good >= len(cells) * 0.85:
            mono = enforce_monotonic_timings(direct, master=master)
            if not timings_have_crumb_durations(mono):
                return mono
    return enforce_monotonic_timings(
        map_frames(cells, words, audio_duration=master),
        master=master,
    )


_HANDLERS: dict[str, Callable[[list[tuple[int, str]], list[WordTS], float], list[FrameTiming]]] = {
    "direct": _method_direct,
    "contiguous": _method_contiguous,
    "proportional": _method_proportional,
    "uniform": _method_uniform,
    "auto": _method_auto,
}


def apply_align_method(
    method_id: str,
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    mid = resolve_align_method(method_id)
    if not cells:
        raise ValueError("нет ячеек R49 для align")
    if not words and mid not in ("uniform",):
        raise ValueError("нет ASR-слов (words.json) — сначала прогоните ASR или force_asr")
    handler = _HANDLERS[mid]
    timings = handler(cells, words, float(master))
    if not timings:
        raise RuntimeError(f"методика {mid!r} не дала таймкодов")
    crumbs = sum(1 for t in timings if t.duration <= 0.1 + 1e-9)
    logger.info(
        "audio_align method={}: {} frames, master={:.2f}s, crumbs≤0.1s={}",
        mid,
        len(timings),
        float(master),
        crumbs,
    )
    return timings
