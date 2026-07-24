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
    enforce_monotonic_timings,
    map_frames,
    normalize_contiguous,
    tokenize_lower,
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
        summary="Parakeet/FastConformer ASR целиком: старт/конец = первое и последнее слово кадра.",
    ),
    AlignMethodInfo(
        id="nemo_contiguous",
        title="2. NeMo — до следующего",
        summary="Тот же full-file NeMo (кэш): кадр до старта следующего. Без повторного ASR.",
    ),
    AlignMethodInfo(
        id="nemo_chunks",
        title="3. NeMo — сегменты",
        summary="Режем озвучку на ≤8 сегментов по весу R49, ASR каждого сегмента, склеиваем слова.",
    ),
    AlignMethodInfo(
        id="silence",
        title="4. Паузы (ffmpeg)",
        summary="Острова речи между тишинами; кадры по весу R49 внутри островов (не равномерка).",
    ),
    AlignMethodInfo(
        id="nemo_auto",
        title="5. NeMo — auto",
        summary="Тот же full-file NeMo (кэш): direct, при крошках — contiguous (production).",
    ),
)

# Full-file NeMo методы делят один words.json (разный только тайминг).
SHARED_NEMO_FULL_METHODS = frozenset({"nemo_direct", "nemo_contiguous", "nemo_auto"})


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


def _extract_audio_slice(
    src: Path,
    start: float,
    end: float,
    dest: Path,
    *,
    min_dur: float = 3.0,
) -> float:
    """Вырезать кусок; короткие добиваем тишиной (apad), чтобы NeMo не падал на Windows."""
    raw = max(float(end) - float(start), 0.05)
    dur = max(raw, float(min_dur))
    pad = max(0.0, dur - raw)
    dest.parent.mkdir(parents=True, exist_ok=True)
    af = "aresample=16000"
    if pad > 0.01:
        af = f"{af},apad=pad_dur={pad:.3f}"
    cmd = [
        "ffmpeg",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{raw:.3f}",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-af",
        af,
        "-c:a",
        "pcm_s16le",
        "-vn",
        str(dest),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size < 64:
        err = (proc.stderr or proc.stdout or "")[-400:]
        raise RuntimeError(f"ffmpeg slice failed ({start:.2f}-{end:.2f}): {err}")
    return dur


def _segment_time_bounds(
    cells: list[tuple[int, str]],
    master: float,
    *,
    max_chunks: int = 8,
) -> list[tuple[float, float]]:
    """≤ max_chunks временных сегментов по накопленному весу R49."""
    ad = max(float(master), 0.05)
    n = len(cells)
    if n <= 0:
        return []
    weights = _token_weights(cells)
    k = max(1, min(int(max_chunks), n))
    if k == 1:
        return [(0.0, ad)]

    frame_ends: list[float] = []
    acc = 0.0
    for w in weights:
        acc += w * ad
        frame_ends.append(acc)
    frame_ends[-1] = ad

    # границы групп кадров: кумулятивный вес ≈ i/k
    cuts_idx: list[int] = []
    cum = 0.0
    target_i = 1
    for i, w in enumerate(weights):
        cum += w
        if target_i < k and cum + 1e-9 >= target_i / k:
            cuts_idx.append(i + 1)
            target_i += 1
    while len(cuts_idx) < k - 1:
        cuts_idx.append(min(n, (len(cuts_idx) + 1) * n // k))
    cuts_idx = sorted({c for c in cuts_idx if 0 < c < n})[: k - 1]

    idx_bounds = [0, *cuts_idx, n]
    out: list[tuple[float, float]] = []
    for a, b in zip(idx_bounds[:-1], idx_bounds[1:]):
        start = 0.0 if a == 0 else float(frame_ends[a - 1])
        end = float(frame_ends[b - 1])
        if end <= start + 0.05:
            end = min(ad, start + 0.5)
        out.append((round(start, 3), round(end, 3)))
    if out:
        out[0] = (0.0, out[0][1])
        out[-1] = (out[-1][0], ad)
    return out


def speech_nemo_full(audio_path: Path) -> list[WordTS]:
    return transcribe_nemo(audio_path)


def speech_nemo_chunks(
    audio_path: Path,
    cells: list[tuple[int, str]],
    master: float,
    *,
    max_chunks: int = 8,
) -> list[WordTS]:
    """≤8 сегментов по весу R49 → NeMo по одному сегменту (не 153 раза)."""
    _require_nemo()
    from app.services.nvidia_asr import (
        normalize_nvidia_asr_model,
        transcribe_words_nvidia,
    )
    from app.settings import settings
    import time

    ad = max(float(master), 0.05)
    segments = _segment_time_bounds(cells, ad, max_chunks=max_chunks)
    if not segments:
        raise RuntimeError("nemo_chunks: нет сегментов")

    model = normalize_nvidia_asr_model(settings.nvidia_asr_model)
    words: list[WordTS] = []
    with tempfile.TemporaryDirectory(prefix="align_chunks_") as tmp:
        tmp_dir = Path(tmp)
        logger.info(
            "nemo_chunks: {} сегментов (не по кадру) из {} ячеек, master={:.1f}s",
            len(segments),
            len(cells),
            ad,
        )
        for i, (start, end) in enumerate(segments):
            slice_path = tmp_dir / f"seg_{i:02d}.wav"
            _extract_audio_slice(audio_path, start, end, slice_path, min_dur=3.0)
            try:
                chunk_words = transcribe_words_nvidia(
                    slice_path,
                    model_name=model,
                    language="ru",
                )
            except Exception as exc:  # noqa: BLE001
                logger.exception(
                    "nemo_chunks: сегмент {} [{:.2f}-{:.2f}] упал: {}",
                    i,
                    start,
                    end,
                    exc,
                )
                raise RuntimeError(
                    f"NeMo упал на сегменте {i + 1}/{len(segments)} "
                    f"({start:.1f}-{end:.1f}s): {exc}"
                ) from exc
            for w in chunk_words:
                ws = float(w.start) + float(start)
                we = float(w.end) + float(start)
                if ws >= end + 0.05:
                    continue
                words.append(
                    WordTS(
                        word=w.word,
                        start=round(ws, 3),
                        end=round(min(we, end), 3),
                        prob=w.prob,
                    )
                )
            logger.info(
                "nemo_chunks: seg {}/{} [{:.2f}-{:.2f}] → {} слов",
                i + 1,
                len(segments),
                start,
                end,
                len(chunk_words),
            )
            time.sleep(0.35)
    if not words:
        raise RuntimeError("nemo_chunks: NeMo не дал слов ни по одному сегменту")
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


def _speech_islands(
    master: float,
    silences: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """Интервалы речи = дополнение тишин до [0, master]."""
    ad = max(float(master), 0.05)
    islands: list[tuple[float, float]] = []
    pos = 0.0
    for s, e in sorted(silences, key=lambda x: x[0]):
        s = max(0.0, min(float(s), ad))
        e = max(0.0, min(float(e), ad))
        if s > pos + 0.03:
            islands.append((pos, s))
        pos = max(pos, e)
    if pos < ad - 0.03:
        islands.append((pos, ad))
    if not islands:
        islands = [(0.0, ad)]
    # Склеить крошечные
    merged: list[tuple[float, float]] = [islands[0]]
    for s, e in islands[1:]:
        ps, pe = merged[-1]
        if s <= pe + 0.05:
            merged[-1] = (ps, max(pe, e))
        else:
            merged.append((s, e))
    return merged


def _timings_from_speech_islands(
    cells: list[tuple[int, str]],
    master: float,
    silences: list[tuple[float, float]],
) -> list[FrameTiming]:
    """Раскладка кадров по островам речи (тишина не «равномерка»).

    Кадры распределяются по островам пропорционально длине речи и весу R49;
    внутри острова — по токенам. Затем склеиваем покрытие [0, master].
    """
    n = len(cells)
    ad = max(float(master), 0.05)
    if n <= 0:
        return []
    if n == 1:
        return [FrameTiming(cells[0][0], 0.0, round(ad, 3), round(ad, 3))]

    islands = _speech_islands(ad, silences)
    island_lens = [max(e - s, 0.05) for s, e in islands]
    total_speech = sum(island_lens)
    weights = _token_weights(cells)

    # Сколько «веса» на каждый остров ≈ доля речи.
    island_capacity = [lens / total_speech for lens in island_lens]
    # Назначаем кадры островам, заполняя capacity по порядку.
    assign: list[list[int]] = [[] for _ in islands]
    isl = 0
    filled = 0.0
    for fi, w in enumerate(weights):
        while isl < len(islands) - 1 and filled + 1e-9 >= sum(island_capacity[: isl + 1]):
            isl += 1
        assign[isl].append(fi)
        filled += w

    # Пустые острова — отдать соседним
    for i, group in enumerate(assign):
        if group:
            continue
        donor = i - 1 if i > 0 and assign[i - 1] else (i + 1 if i + 1 < len(assign) else None)
        if donor is not None and assign[donor]:
            # перенос не делаем — просто пропустим остров (тишина между)
            pass

    raw: list[FrameTiming | None] = [None] * n
    for i, ((s, e), group) in enumerate(zip(islands, assign)):
        if not group:
            continue
        g_weights = [weights[j] for j in group]
        g_total = sum(g_weights) or float(len(g_weights))
        pos = s
        for j, w in zip(group, g_weights):
            dur = (w / g_total) * (e - s)
            fn = cells[j][0]
            raw[j] = FrameTiming(fn, round(pos, 3), round(pos + dur, 3), round(dur, 3))
            pos += dur
        # snap last in group to island end
        last_j = group[-1]
        assert raw[last_j] is not None
        prev = raw[last_j]
        raw[last_j] = FrameTiming(prev.frame_number, prev.start_ts, round(e, 3), round(e - prev.start_ts, 3))

    # Кадры без острова — равномерно в оставшиеся дыры позже через contiguous glue
    for i, t in enumerate(raw):
        if t is None:
            raw[i] = FrameTiming(cells[i][0], 0.0, 0.0, 1.0)

    timings = [t for t in raw if t is not None]
    # Склеить в непрерывное покрытие master (тишина уходит в границы соседних кадров).
    from app.services.mapper import absorb_crumb_durations

    return absorb_crumb_durations(normalize_contiguous(timings, ad), ad)


def _method_direct(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    from app.services.mapper import finalize_align_timings

    mono = enforce_monotonic_timings(map_frames(cells, words), master=master)
    return finalize_align_timings(mono, cells, words, master)


def _method_contiguous(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    from app.services.mapper import finalize_align_timings, timings_from_word_transitions

    return finalize_align_timings(
        timings_from_word_transitions(cells, words, master),
        cells,
        words,
        master,
    )


def _method_auto(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
) -> list[FrameTiming]:
    from app.services.mapper import (
        count_crumb_frames,
        finalize_align_timings,
        timings_from_word_transitions,
    )

    direct = map_frames(cells, words)
    if direct and len(direct) == len(cells):
        mono = enforce_monotonic_timings(direct, master=master)
        out = finalize_align_timings(mono, cells, words, master)
        if count_crumb_frames(out) == 0:
            return out
    contig = timings_from_word_transitions(cells, words, master)
    return finalize_align_timings(contig, cells, words, master)


_TIMING_HANDLERS: dict[
    str, Callable[[list[tuple[int, str]], list[WordTS], float], list[FrameTiming]]
] = {
    "nemo_direct": _method_direct,
    "nemo_contiguous": _method_contiguous,
    "nemo_chunks": _method_contiguous,  # сегментный ASR → границы по переходам, не raw direct
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
        timings = _timings_from_speech_islands(cells, ad, silences)
        if not timings:
            raw = [FrameTiming(fn, 0.0, 0.0, 1.0) for fn, _ in cells]
            timings = normalize_contiguous(raw, ad)
        crumbs = sum(1 for t in timings if t.duration <= 0.1 + 1e-9)
        logger.info(
            "audio_align method=silence: {} silences, {} islands→frames, "
            "master={:.2f}s, crumbs≤0.1s={}",
            len(silences),
            len(timings),
            ad,
            crumbs,
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
