"""5 методик разбора речи → таймкоды R15.

Только NVIDIA NeMo (+ акустика ffmpeg). Whisper запрещён в этом модуле.
Каждая методика — свой speech-pipeline, не «нарезка одного words.json».
"""

from __future__ import annotations

import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from loguru import logger

from app.services.mapper import (
    FrameTiming,
    build_frame_word_spans,
    enforce_monotonic_timings,
    map_frames,
    normalize_contiguous,
    tokenize_lower,
    timings_have_crumb_durations,
    _segment_durations_from_transitions,
)
from app.services.whisper import WordTS  # структура слова; движок Whisper не вызываем


@dataclass(frozen=True)
class AlignMethodInfo:
    id: str
    title: str
    summary: str


ALIGN_METHODS: tuple[AlignMethodInfo, ...] = (
    AlignMethodInfo(
        id="nemo_direct",
        title="1. NeMo — слова",
        summary="Parakeet ASR: старт/конец кадра = первое и последнее сопоставленное слово.",
    ),
    AlignMethodInfo(
        id="nemo_contiguous",
        title="2. NeMo — до следующего",
        summary="Parakeet ASR: кадр от своего первого слова до старта следующего кадра.",
    ),
    AlignMethodInfo(
        id="nemo_chunks",
        title="3. NeMo — по кускам",
        summary="Режем озвучку по весу R49, ASR NeMo на каждый кусок, склеиваем слова → границы.",
    ),
    AlignMethodInfo(
        id="silence",
        title="4. Паузы (ffmpeg)",
        summary="Режем по самым длинным тишинам (silencedetect) — без ASR-слов.",
    ),
    AlignMethodInfo(
        id="nemo_auto",
        title="5. NeMo — auto",
        summary="Parakeet ASR: direct; при крошках — contiguous по стартам слов (production).",
    ),
)

_METHOD_IDS = {m.id for m in ALIGN_METHODS}

# Старые id из UI/API → новые (без Whisper).
_LEGACY_METHOD_MAP = {
    "direct": "nemo_direct",
    "contiguous": "nemo_contiguous",
    "proportional": "nemo_chunks",
    "uniform": "silence",
    "auto": "nemo_auto",
    "whisper": "nemo_direct",
    "whisper_vad": "nemo_direct",
}


def list_align_methods() -> list[dict[str, str]]:
    return [
        {"id": m.id, "title": m.title, "summary": m.summary} for m in ALIGN_METHODS
    ]


def resolve_align_method(method_id: str) -> str:
    mid = (method_id or "").strip().lower()
    mid = _LEGACY_METHOD_MAP.get(mid, mid)
    if mid not in _METHOD_IDS:
        known = ", ".join(sorted(_METHOD_IDS))
        raise ValueError(f"неизвестная методика {method_id!r}; доступны: {known}")
    return mid


def _require_nemo() -> None:
    from app.services.nvidia_asr import nvidia_asr_available

    if not nvidia_asr_available():
        raise RuntimeError(
            'NeMo ASR недоступен. Установите: pip install -e ".[nvidia]"'
        )


def transcribe_nemo(audio_path: Path, *, language: str = "ru") -> list[WordTS]:
    """Только NVIDIA NeMo — без fallback на Whisper."""
    _require_nemo()
    from app.services.nvidia_asr import normalize_nvidia_asr_model, transcribe_words_nvidia
    from app.settings import settings

    model = normalize_nvidia_asr_model(settings.nvidia_asr_model)
    words = transcribe_words_nvidia(
        audio_path,
        model_name=model,
        language=language,
    )
    if not words:
        raise RuntimeError(f"NeMo не вернул слова для {audio_path.name}")
    return words


def _token_weights(cells: list[tuple[int, str]]) -> list[float]:
    weights = [float(max(len(tokenize_lower(text)), 1)) for _, text in cells]
    total = sum(weights) or float(len(weights))
    return [w / total for w in weights]


def _extract_audio_slice(src: Path, start: float, end: float, dest: Path) -> None:
    dur = max(float(end) - float(start), 0.05)
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{dur:.3f}",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-vn",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 64:
        err = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"ffmpeg slice failed ({start:.2f}-{end:.2f}): {err}")


def speech_nemo_full(audio_path: Path) -> list[WordTS]:
    return transcribe_nemo(audio_path)


def speech_nemo_chunks(
    audio_path: Path,
    cells: list[tuple[int, str]],
    master: float,
) -> list[WordTS]:
    """Провизорные куски по весу R49 → NeMo на каждый → слова со сдвигом."""
    _require_nemo()
    ad = max(float(master), 0.05)
    weights = _token_weights(cells)
    bounds = [0.0]
    acc = 0.0
    for w in weights[:-1]:
        acc += w * ad
        bounds.append(round(acc, 3))
    bounds.append(ad)

    words: list[WordTS] = []
    with tempfile.TemporaryDirectory(prefix="align_chunks_") as tmp:
        tmp_dir = Path(tmp)
        for i, ((fn, _text), start, end) in enumerate(
            zip(cells, bounds[:-1], bounds[1:])
        ):
            if end - start < 0.05:
                continue
            slice_path = tmp_dir / f"f{fn}_{i}.wav"
            _extract_audio_slice(audio_path, start, end, slice_path)
            chunk_words = transcribe_nemo(slice_path)
            for w in chunk_words:
                words.append(
                    WordTS(
                        word=w.word,
                        start=round(float(w.start) + float(start), 3),
                        end=round(float(w.end) + float(start), 3),
                        prob=w.prob,
                    )
                )
            logger.info(
                "nemo_chunks: frame {} [{:.2f}-{:.2f}] → {} слов",
                fn,
                start,
                end,
                len(chunk_words),
            )
    if not words:
        raise RuntimeError("nemo_chunks: NeMo не дал слов ни по одному куску")
    return words


_SILENCE_START_RE = re.compile(r"silence_start:\s*([0-9.]+)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*([0-9.]+)")


def detect_silences(
    audio_path: Path,
    *,
    noise_db: float = -30.0,
    min_dur: float = 0.25,
) -> list[tuple[float, float]]:
    """ffmpeg silencedetect → список (start, end) тишин."""
    af = f"silencedetect=noise={noise_db}dB:d={min_dur}"
    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        af,
        "-f",
        "null",
        "-",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    starts: list[float] = []
    ends: list[float] = []
    for line in text.splitlines():
        m = _SILENCE_START_RE.search(line)
        if m:
            starts.append(float(m.group(1)))
            continue
        m = _SILENCE_END_RE.search(line)
        if m:
            ends.append(float(m.group(1)))
    out: list[tuple[float, float]] = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else None
        if e is None or e <= s:
            continue
        out.append((s, e))
    return out


def _timings_from_silence_cuts(
    cells: list[tuple[int, str]],
    master: float,
    silences: list[tuple[float, float]],
) -> list[FrameTiming]:
    n = len(cells)
    ad = max(float(master), 0.05)
    if n <= 0:
        return []
    if n == 1:
        return [FrameTiming(cells[0][0], 0.0, round(ad, 3), round(ad, 3))]

    candidates: list[tuple[float, float]] = []
    for s, e in silences:
        if e - s < 0.15:
            continue
        mid = (s + e) / 2.0
        if mid <= 0.05 or mid >= ad - 0.05:
            continue
        candidates.append((e - s, mid))
    candidates.sort(key=lambda x: x[0], reverse=True)

    cuts: list[float] = []
    for _, mid in candidates:
        if len(cuts) >= n - 1:
            break
        if any(abs(mid - c) < 0.2 for c in cuts):
            continue
        cuts.append(mid)
    cuts.sort()

    # Не хватило пауз — добиваем равномерными точками в незанятых зонах.
    if len(cuts) < n - 1:
        need = (n - 1) - len(cuts)
        uniform = [ad * (i + 1) / n for i in range(n - 1)]
        for u in uniform:
            if need <= 0:
                break
            if any(abs(u - c) < 0.35 for c in cuts):
                continue
            cuts.append(u)
            need -= 1
        cuts.sort()
        while len(cuts) < n - 1:
            cuts.append(ad * (len(cuts) + 1) / n)
            cuts.sort()
        cuts = cuts[: n - 1]

    bounds = [0.0, *[round(c, 3) for c in cuts], round(ad, 3)]
    out: list[FrameTiming] = []
    for (fn, _), start, end in zip(cells, bounds[:-1], bounds[1:]):
        out.append(
            FrameTiming(fn, round(start, 3), round(end, 3), round(end - start, 3))
        )
    return enforce_monotonic_timings(out, master=ad)


def _timings_contiguous_forced(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
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
    return enforce_monotonic_timings(map_frames(cells, words), master=master)


def _method_contiguous(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    return enforce_monotonic_timings(
        _timings_contiguous_forced(cells, words, master),
        master=master,
    )


def _method_auto(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
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


_TIMING_HANDLERS: dict[
    str, Callable[[list[tuple[int, str]], list[WordTS], float], list[FrameTiming]]
] = {
    "nemo_direct": _method_direct,
    "nemo_contiguous": _method_contiguous,
    "nemo_chunks": _method_direct,
    "nemo_auto": _method_auto,
}


@dataclass
class SpeechAlignResult:
    words: list[WordTS]
    timings: list[FrameTiming]
    speech_source: str


def run_speech_align(
    method_id: str,
    audio_path: Path,
    cells: list[tuple[int, str]],
    master: float,
    *,
    cached_words: list[WordTS] | None = None,
) -> SpeechAlignResult:
    """Полный speech-pipeline методики → слова + таймкоды."""
    mid = resolve_align_method(method_id)
    ad = max(float(master), 0.05)

    if mid == "silence":
        silences = detect_silences(audio_path)
        timings = _timings_from_silence_cuts(cells, ad, silences)
        if not timings:
            raw = [FrameTiming(fn, 0.0, 0.0, 1.0) for fn, _ in cells]
            timings = normalize_contiguous(raw, ad)
        logger.info(
            "audio_align method=silence: {} silences, {} frames, master={:.2f}s",
            len(silences),
            len(timings),
            ad,
        )
        return SpeechAlignResult(words=[], timings=timings, speech_source="ffmpeg_silence")

    if cached_words:
        words = cached_words
        speech_source = "cache"
    elif mid == "nemo_chunks":
        words = speech_nemo_chunks(audio_path, cells, ad)
        speech_source = "nemo_chunks"
    else:
        words = speech_nemo_full(audio_path)
        speech_source = "nemo"

    handler = _TIMING_HANDLERS[mid]
    timings = handler(cells, words, ad)
    if not timings:
        raise RuntimeError(f"методика {mid!r} не дала таймкодов")
    crumbs = sum(1 for t in timings if t.duration <= 0.1 + 1e-9)
    logger.info(
        "audio_align method={}: {} frames, {} words, master={:.2f}s, crumbs≤0.1s={}",
        mid,
        len(timings),
        len(words),
        ad,
        crumbs,
    )
    return SpeechAlignResult(words=words, timings=timings, speech_source=speech_source)


def apply_align_method(
    method_id: str,
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    """Тайминги из уже готовых слов (тесты / silence не сюда)."""
    mid = resolve_align_method(method_id)
    if mid == "silence":
        # Без аудиофайла — равномерный fallback (тесты).
        raw = [FrameTiming(fn, 0.0, 0.0, 1.0) for fn, _ in cells] or [
            FrameTiming(1, 0.0, 0.0, 1.0)
        ]
        return normalize_contiguous(raw, master)
    if not cells:
        raise ValueError("нет ячеек R49 для align")
    if not words:
        raise ValueError("нет ASR-слов NeMo")
    handler = _TIMING_HANDLERS[mid]
    timings = handler(cells, words, float(master))
    if not timings:
        raise RuntimeError(f"методика {mid!r} не дала таймкодов")
    return timings
