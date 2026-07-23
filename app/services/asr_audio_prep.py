"""Подготовка voice_full для ASR (NeMo/Whisper): mono 16 kHz."""

from __future__ import annotations

import asyncio
from pathlib import Path

from loguru import logger

ASR_SAMPLE_RATE = 16_000


async def probe_audio_channels(path: Path) -> int:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "a:0",
        "-show_entries",
        "stream=channels,sample_rate",
        "-of",
        "csv=p=0",
        str(path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(
            f"ffprobe channels failed for {path}: {stderr.decode(errors='ignore')}"
        )
    parts = stdout.decode().strip().split(",")
    if not parts or not parts[0].isdigit():
        return 1
    return int(parts[0])


async def prepare_audio_for_asr(src: Path) -> Path:
    """Mono 16 kHz WAV рядом с исходником (кэш .asr_mono.wav)."""
    src = src.resolve()
    if not src.is_file():
        raise FileNotFoundError(src)

    cache = src.with_name(f"{src.stem}.asr_mono.wav")
    try:
        channels = await probe_audio_channels(src)
    except Exception as exc:  # noqa: BLE001
        logger.warning("audio_prep: ffprobe {} — конвертируем вслепую: {}", src.name, exc)
        channels = 2

    if (
        cache.is_file()
        and cache.stat().st_size > 1000
        and cache.stat().st_mtime >= src.stat().st_mtime
    ):
        return cache

    if channels == 1 and src.suffix.lower() == ".wav":
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(src),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await proc.communicate()
            rate = int(float(out.decode().strip() or ASR_SAMPLE_RATE))
            if rate == ASR_SAMPLE_RATE:
                return src
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "audio_prep: {} → mono {} Hz ({})",
        src.name,
        ASR_SAMPLE_RATE,
        f"{channels}ch" if channels != 1 else "mono resample",
    )
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(ASR_SAMPLE_RATE),
        "-c:a",
        "pcm_s16le",
        str(cache),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0 or not cache.is_file():
        raise RuntimeError(
            f"ffmpeg mono convert failed for {src.name}: "
            f"{stderr.decode(errors='ignore')[:800]}"
        )
    return cache
