"""NVIDIA NeMo Parakeet — word-level таймкоды на GPU (чанки по 35 с)."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from app.services.whisper import WordTS

_NEMO_INSTALL_HINT = 'pip install -e ".[nvidia-asr]"  # см. scripts/install-nvidia-asr.ps1'
_CHUNK_SEC = 35.0
_CHUNK_OVERLAP = 2.0

_model_cache: dict[str, object] = {}


def nvidia_asr_available() -> bool:
    try:
        import nemo.collections.asr  # noqa: F401
        return True
    except ImportError:
        return False


def _require_cuda() -> None:
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "NeMo Parakeet: CUDA недоступна. Без GPU montage ASR не запускаем."
        )


def _probe_duration_sec(path: Path) -> float:
    proc = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {proc.stderr}")
    return max(float(proc.stdout.strip() or 0.01), 0.01)


def _extract_wav_chunk(src: Path, start: float, duration: float, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(src),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out),
        ],
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        raise RuntimeError(
            f"ffmpeg chunk failed: {(proc.stderr or b'').decode(errors='ignore')[:500]}"
        )


def _configure_timestamps(model) -> None:
    """Включить preserve_alignments + compute_timestamps в decoding."""
    try:
        from omegaconf import OmegaConf, open_dict

        decoding_cfg = model.cfg.decoding
        with open_dict(decoding_cfg):
            decoding_cfg.preserve_alignments = True
            decoding_cfg.compute_timestamps = True
        if hasattr(model, "change_decoding_strategy"):
            model.change_decoding_strategy(decoding_cfg)
            logger.info("nvidia-asr: decoding strategy → compute_timestamps=True")
    except Exception as exc:  # noqa: BLE001
        logger.warning("nvidia-asr: decoding timestamps config skipped: {}", exc)


def _is_english_only_nemo_model(model_name: str) -> bool:
    low = (model_name or "").lower()
    return (
        "parakeet" in low
        or "stt_en" in low
        or "canary" in low and "multilingual" not in low
    )


def _assert_model_for_language(model_name: str, language: str) -> None:
    lang = (language or "ru").lower()
    if lang.startswith("ru") and _is_english_only_nemo_model(model_name):
        raise RuntimeError(
            f"NVIDIA_ASR_MODEL={model_name!r} — английская модель, русскую озвучку "
            "не разберёт. В .env поставь: "
            "NVIDIA_ASR_MODEL=nvidia/stt_ru_fastconformer_hybrid_large_pc"
        )


def _load_model(model_name: str):
    if model_name in _model_cache:
        return _model_cache[model_name]
    _require_cuda()
    import torch
    from nemo.collections.asr.models import ASRModel

    logger.info("nvidia-asr: loading '{}' on GPU …", model_name)
    try:
        model = ASRModel.from_pretrained(model_name)
    except Exception as first_exc:  # noqa: BLE001
        try:
            from nemo.collections.asr.models import EncDecHybridRNNTCTCBPEModel

            logger.info("nvidia-asr: retry EncDecHybridRNNTCTCBPEModel …")
            model = EncDecHybridRNNTCTCBPEModel.from_pretrained(model_name)
        except Exception as second_exc:  # noqa: BLE001
            raise RuntimeError(
                f"не удалось загрузить NeMo ASR {model_name!r}: {first_exc}; {second_exc}"
            ) from second_exc
    if hasattr(model, "eval"):
        model.eval()
    if hasattr(model, "cuda"):
        model = model.cuda()
    elif hasattr(model, "to"):
        model = model.to(torch.device("cuda"))
    _configure_timestamps(model)
    _model_cache[model_name] = model
    try:
        name = torch.cuda.get_device_name(0)
        logger.info("nvidia-asr: model on {}", name)
    except Exception:  # noqa: BLE001
        pass
    return model


def _model_time_stride(model) -> float:
    try:
        cfg = model.cfg
        pre = getattr(cfg, "preprocessor", None)
        if pre is not None:
            ws = getattr(pre, "window_stride", None)
            if ws is not None:
                return 8.0 * float(ws)
    except Exception:  # noqa: BLE001
        pass
    return 0.08


def _unwrap_hypothesis(hyp: Any) -> Any:
    if hyp is None:
        return None
    if isinstance(hyp, (list, tuple)) and hyp:
        return hyp[0]
    return hyp


def _timestamp_bucket(hyp: Any) -> dict[str, Any] | None:
    for attr in ("timestamp", "timestep", "timestamps"):
        raw = getattr(hyp, attr, None)
        if isinstance(raw, dict):
            return raw
    return None


def _token_from_stamp(stamp: dict[str, Any]) -> str:
    for key in ("word", "char", "text", "segment"):
        val = stamp.get(key)
        if val is not None and str(val).strip():
            return str(val).strip()
    return ""


def _seconds_from_stamp(stamp: dict[str, Any], model, *, time_offset: float) -> tuple[float, float] | None:
    if "start" in stamp and "end" in stamp:
        start = float(stamp["start"]) + time_offset
        end = float(stamp["end"]) + time_offset
        return start, max(end, start + 0.01)
    if "start_time" in stamp and "end_time" in stamp:
        start = float(stamp["start_time"]) + time_offset
        end = float(stamp["end_time"]) + time_offset
        return start, max(end, start + 0.01)
    if "start_offset" in stamp:
        stride = _model_time_stride(model)
        start = float(stamp["start_offset"]) * stride + time_offset
        end_off = stamp.get("end_offset", stamp["start_offset"])
        end = float(end_off) * stride + time_offset
        return start, max(end, start + 0.01)
    return None


def _words_from_timestamp_dict(
    hyp: Any,
    model,
    *,
    time_offset: float = 0.0,
) -> list[WordTS]:
    bucket = _timestamp_bucket(hyp)
    if not bucket:
        return []
    word_rows = bucket.get("word") or bucket.get("words")
    if not word_rows:
        return []

    words: list[WordTS] = []
    for row in word_rows:
        if not isinstance(row, dict):
            continue
        token = _token_from_stamp(row)
        if not token:
            continue
        bounds = _seconds_from_stamp(row, model, time_offset=time_offset)
        if bounds is None:
            continue
        start, end = bounds
        words.append(
            WordTS(
                word=token,
                start=round(start, 3),
                end=round(end, 3),
                prob=float(row.get("confidence", row.get("score", 0.0)) or 0.0),
            )
        )
    return words


def _words_from_alignments(hyp: Any, *, time_offset: float = 0.0) -> list[WordTS]:
    words: list[WordTS] = []
    alignments = getattr(hyp, "alignments", None) or getattr(hyp, "word_alignments", None)
    if not alignments:
        return words
    for row in alignments:
        if isinstance(row, dict):
            token = str(row.get("word", row.get("text", ""))).strip()
            if not token:
                continue
            words.append(
                WordTS(
                    word=token,
                    start=round(
                        float(row.get("start", row.get("start_time", 0.0))) + time_offset,
                        3,
                    ),
                    end=round(
                        float(row.get("end", row.get("end_time", 0.0))) + time_offset,
                        3,
                    ),
                    prob=float(row.get("confidence", row.get("score", 0.0)) or 0.0),
                )
            )
        elif hasattr(row, "word"):
            token = str(row.word).strip()
            if not token:
                continue
            words.append(
                WordTS(
                    word=token,
                    start=round(
                        float(getattr(row, "start", getattr(row, "start_time", 0.0)))
                        + time_offset,
                        3,
                    ),
                    end=round(
                        float(getattr(row, "end", getattr(row, "end_time", 0.0)))
                        + time_offset,
                        3,
                    ),
                    prob=float(getattr(row, "confidence", 0.0) or 0.0),
                )
            )
    return words


def looks_like_fake_uniform_timestamps(words: list[WordTS]) -> bool:
    """Равномерные 0.25 с — старый fallback, не реальный ASR."""
    if len(words) < 4:
        return False
    sample = words[: min(24, len(words))]
    durs = [round(w.end - w.start, 3) for w in sample]
    if len(set(durs)) == 1 and durs[0] in (0.25, 0.2):
        return True
    if max(durs) - min(durs) < 0.02 and max(durs) <= 0.26:
        return True
    return False


def _validate_word_timestamps(
    words: list[WordTS],
    audio_path: Path,
    *,
    chunk_duration: float | None = None,
    time_offset: float = 0.0,
) -> None:
    if not words:
        raise RuntimeError(f"NeMo ASR: пустой результат для {audio_path.name}")
    if looks_like_fake_uniform_timestamps(words):
        raise RuntimeError(
            f"NeMo ASR вернул равномерные фейковые таймкоды (~0.25с/слово) для "
            f"{audio_path.name}. Нужен transcribe(..., timestamps=True) и "
            "compute_timestamps в decoding."
        )
    last_end = words[-1].end
    expected = chunk_duration if chunk_duration is not None else _probe_duration_sec(audio_path)
    if last_end > time_offset + expected + 5.0:
        logger.warning(
            "nvidia-asr: last word end {:.1f}s > chunk {:.1f}s (offset {:.1f})",
            last_end,
            expected,
            time_offset,
        )


def _hypothesis_to_words(hyp: Any, model, *, time_offset: float = 0.0) -> list[WordTS]:
    hyp = _unwrap_hypothesis(hyp)
    if hyp is None:
        return []

    words = _words_from_timestamp_dict(hyp, model, time_offset=time_offset)
    if words:
        return words

    words = _words_from_alignments(hyp, time_offset=time_offset)
    if words:
        return words

    text = (getattr(hyp, "text", None) or "").strip()
    raise RuntimeError(
        "NeMo ASR не вернул word timestamps "
        f"(файл offset {time_offset:.1f}s, text={text[:60]!r}…). "
        "Обновите NeMo или проверьте CUDA/decoding."
    )


def _transcribe_file(
    model,
    audio_path: Path,
    *,
    time_offset: float = 0.0,
    chunk_duration: float | None = None,
) -> list[WordTS]:
    hyps = model.transcribe(
        [str(audio_path)],
        batch_size=1,
        timestamps=True,
    )
    hyp = hyps[0] if hyps else None
    words = _hypothesis_to_words(hyp, model, time_offset=time_offset)
    _validate_word_timestamps(
        words,
        audio_path,
        chunk_duration=chunk_duration,
        time_offset=time_offset,
    )
    return words


def transcribe_words(
    audio_path: Path,
    *,
    model_name: str = "nvidia/stt_ru_fastconformer_hybrid_large_pc",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[WordTS]:
    _assert_model_for_language(model_name, language)
    del beam_size, vad_filter
    if not nvidia_asr_available():
        raise ImportError(f"NeMo ASR не установлен. {_NEMO_INSTALL_HINT}")
    _require_cuda()
    model = _load_model(model_name)
    audio_path = audio_path.resolve()
    duration = _probe_duration_sec(audio_path)
    logger.info("nvidia-asr: audio {:.1f}s → {}", duration, audio_path.name)

    if duration <= _CHUNK_SEC + 2.0:
        words = _transcribe_file(model, audio_path, chunk_duration=duration)
        logger.info(
            "nvidia-asr: got {} words, span {:.1f}s (single pass)",
            len(words),
            words[-1].end if words else 0.0,
        )
        return words

    n_chunks = max(1, int((duration + _CHUNK_SEC - _CHUNK_OVERLAP - 1) // (_CHUNK_SEC - _CHUNK_OVERLAP)))
    logger.info(
        "nvidia-asr: long audio — {} chunks × {:.0f}s (overlap {:.0f}s)",
        n_chunks,
        _CHUNK_SEC,
        _CHUNK_OVERLAP,
    )
    tmpdir = Path(tempfile.mkdtemp(prefix="nvidia-asr-chunks-"))
    all_words: list[WordTS] = []
    try:
        start = 0.0
        idx = 0
        while start < duration - 0.05:
            seg = min(_CHUNK_SEC, duration - start)
            chunk_path = tmpdir / f"chunk_{idx:04d}.wav"
            _extract_wav_chunk(audio_path, start, seg, chunk_path)
            logger.info(
                "nvidia-asr: chunk {}/{} {:.0f}–{:.0f}s …",
                idx + 1,
                n_chunks,
                start,
                start + seg,
            )
            chunk_words = _transcribe_file(
                model,
                chunk_path,
                time_offset=start,
                chunk_duration=seg,
            )
            if idx > 0 and all_words and chunk_words:
                cut = max(0.0, all_words[-1].end - _CHUNK_OVERLAP)
                chunk_words = [w for w in chunk_words if w.start >= cut - 0.05]
            all_words.extend(chunk_words)
            logger.info(
                "nvidia-asr: chunk {}/{} → {} words (last end {:.1f}s)",
                idx + 1,
                n_chunks,
                len(chunk_words),
                chunk_words[-1].end if chunk_words else start,
            )
            start += _CHUNK_SEC - _CHUNK_OVERLAP
            idx += 1
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    if all_words:
        _validate_word_timestamps(all_words, audio_path, chunk_duration=duration)
    logger.info(
        "nvidia-asr: total {} words, span {:.1f}s / audio {:.1f}s",
        len(all_words),
        all_words[-1].end if all_words else 0.0,
        duration,
    )
    return all_words


def transcribe_words_many(
    audio_paths: list[Path],
    *,
    model_name: str = "nvidia/stt_ru_fastconformer_hybrid_large_pc",
    language: str = "ru",
    beam_size: int = 5,
    vad_filter: bool = False,
) -> list[list[WordTS]]:
    return [
        transcribe_words(
            p,
            model_name=model_name,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )
        for p in audio_paths
    ]
