"""Вариант B: озвучка по ячейкам plan R49 — один mp3 на кадр."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.bots.elevenlabs import ElevenLabsBot
from app.models import Frame
from app.services.mapper import map_frames
from app.services.media_probe import probe_duration
from app.services.whisper import WordTS, transcribe_words_many

FRAME_AUDIO_PREFIX = "frame_"


@dataclass
class FrameAudioClip:
    frame_number: int
    path: Path
    text: str
    start_ts: float
    end_ts: float
    duration: float


def frame_audio_path(audio_dir: Path, frame_number: int) -> Path:
    return audio_dir / f"{FRAME_AUDIO_PREFIX}{frame_number:03d}.mp3"


def list_frame_audio_paths(audio_dir: Path) -> list[Path]:
    if not audio_dir.is_dir():
        return []
    return sorted(audio_dir.glob(f"{FRAME_AUDIO_PREFIX}*.mp3"))


def has_all_frame_audio(audio_dir: Path, frame_numbers: list[int]) -> bool:
    return all(frame_audio_path(audio_dir, n).is_file() for n in frame_numbers)


def delete_frame_audio_files(audio_dir: Path) -> int:
    deleted = 0
    for path in list_frame_audio_paths(audio_dir):
        try:
            path.unlink()
            deleted += 1
        except OSError as exc:
            logger.warning("frame_audio: не удалил {}: {}", path, exc)
    return deleted


async def _run_ffmpeg(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="ignore") or stdout.decode(errors="ignore"))


async def concat_mp3_files(paths: list[Path], out_path: Path) -> Path:
    if not paths:
        raise ValueError("нет mp3 для склейки")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if len(paths) == 1:
        out_path.write_bytes(paths[0].read_bytes())
        return out_path

    list_file = out_path.with_suffix(".txt")
    list_file.write_text(
        "\n".join(f"file '{p.resolve().as_posix()}'" for p in paths),
        encoding="utf-8",
    )
    await _run_ffmpeg([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        str(out_path),
    ])
    list_file.unlink(missing_ok=True)
    return out_path


async def load_frame_clips_from_disk(
    audio_dir: Path,
    frame_numbers: list[int],
) -> list[FrameAudioClip]:
    """Длительности кадров = ffprobe(frame_NNN.mp3)."""
    clips: list[FrameAudioClip] = []
    pos = 0.0
    for frame_number in frame_numbers:
        path = frame_audio_path(audio_dir, frame_number)
        if not path.is_file():
            raise FileNotFoundError(
                f"нет {path.name} — перезапустите шаг «Аудио» (per-frame TTS)"
            )
        duration = await probe_duration(path)
        clip = FrameAudioClip(
            frame_number=frame_number,
            path=path,
            text="",
            start_ts=round(pos, 3),
            end_ts=round(pos + duration, 3),
            duration=round(duration, 3),
        )
        clips.append(clip)
        pos += duration
    return clips


def _rescale_clips_to_master(clips: list[FrameAudioClip], master: float) -> list[FrameAudioClip]:
    """Масштабирует границы кадров так, чтобы сумма = master (voice_full.mp3)."""
    if not clips:
        return clips
    raw_sum = sum(c.duration for c in clips)
    if raw_sum <= 0:
        raise RuntimeError("сумма длительностей frame_*.mp3 равна нулю")
    if abs(raw_sum - master) <= 0.05:
        out = list(clips)
        out[-1].end_ts = round(master, 3)
        out[-1].duration = round(out[-1].end_ts - out[-1].start_ts, 3)
        return out

    factor = master / raw_sum
    pos = 0.0
    out: list[FrameAudioClip] = []
    for clip in clips:
        dur = round(clip.duration * factor, 3)
        out.append(FrameAudioClip(
            frame_number=clip.frame_number,
            path=clip.path,
            text=clip.text,
            start_ts=round(pos, 3),
            end_ts=round(pos + dur, 3),
            duration=dur,
        ))
        pos += dur
    out[-1].end_ts = round(master, 3)
    out[-1].duration = round(out[-1].end_ts - out[-1].start_ts, 3)
    return out


def frame_clips_from_whisper(
    cells: list[tuple[int, str]],
    words: list[WordTS],
    master: float,
    voice_full_path: Path,
) -> list[FrameAudioClip]:
    """Границы кадров из Whisper + voice_full, когда нет frame_NNN.mp3."""
    text_by_frame = dict(cells)
    timings = map_frames(cells, words, audio_duration=master)
    return [
        FrameAudioClip(
            frame_number=t.frame_number,
            path=voice_full_path,
            text=text_by_frame.get(t.frame_number, ""),
            start_ts=t.start_ts,
            end_ts=t.end_ts,
            duration=t.duration,
        )
        for t in timings
    ]


async def build_assembly_timeline(
    audio_dir: Path,
    voice_full_path: Path,
    frame_numbers: list[int],
    *,
    cells: list[tuple[int, str]] | None = None,
    words: list[WordTS] | None = None,
) -> tuple[list[FrameAudioClip], float, float]:
    """Озвучка — единственное мерило: voice_full задаёт конец ролика.

    Если есть frame_NNN.mp3 — границы кадров из ffprobe(фрагмент).
    Иначе — из Whisper по cells + voice_full (legacy audio без per-frame TTS).

    Returns (clips, master_duration, time_scale).
    """
    master = await probe_duration(voice_full_path)

    if has_all_frame_audio(audio_dir, frame_numbers):
        clips = await load_frame_clips_from_disk(audio_dir, frame_numbers)
        raw_sum = sum(c.duration for c in clips)
        if raw_sum <= 0:
            raise RuntimeError("сумма длительностей frame_*.mp3 равна нулю")
        scale = 1.0 if abs(raw_sum - master) <= 0.05 else master / raw_sum
        clips = _rescale_clips_to_master(clips, master)
        return clips, master, scale

    if not cells or not words:
        raise FileNotFoundError(
            "нет frame_001.mp3 — перезапустите шаг «Аудио» "
            "(per-frame TTS из plan R49)"
        )

    logger.warning(
        "frame_*.mp3 не найдены — границы кадров из Whisper + voice_full ({:.2f}s). "
        "Для точной синхронизации перезапустите «Аудио».",
        master,
    )
    clips = frame_clips_from_whisper(cells, words, master, voice_full_path)
    return clips, master, 1.0


async def synthesize_per_frame_audio(
    el: ElevenLabsBot,
    *,
    frames: list[Frame],
    cells: list[tuple[int, str]],
    audio_dir: Path,
    clip_timeout: float = 180.0,
) -> tuple[list[FrameAudioClip], Path]:
    """TTS для каждой ячейки R49 → frame_NNN.mp3, затем voice_full.mp3."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    delete_frame_audio_files(audio_dir)

    text_by_frame = dict(cells)
    clips: list[FrameAudioClip] = []
    pos = 0.0

    for fr in frames:
        text = (text_by_frame.get(fr.number) or "").strip()
        if not text:
            raise RuntimeError(
                f"кадр {fr.number}: пустая ячейка R49 — "
                "одна колонка plan = одно видео, текст обязателен"
            )

        clip_path = frame_audio_path(audio_dir, fr.number)
        logger.info("[#{}] frame_audio: кадр {} ({} симв.) → {}", fr.project_id, fr.number, len(text), clip_path.name)
        await el.tts(text, clip_path, timeout=clip_timeout)
        duration = await probe_duration(clip_path)
        clip = FrameAudioClip(
            frame_number=fr.number,
            path=clip_path,
            text=text,
            start_ts=round(pos, 3),
            end_ts=round(pos + duration, 3),
            duration=round(duration, 3),
        )
        clips.append(clip)
        pos += duration

    full_path = audio_dir / f"voice_full_{uuid.uuid4().hex[:8]}.mp3"
    await concat_mp3_files([c.path for c in clips], full_path)
    return clips, full_path


def whisper_words_from_clips(
    clips: list[FrameAudioClip],
    *,
    model_name: str,
    language: str = "ru",
) -> list[WordTS]:
    """Whisper по каждому фрагменту; таймкоды сдвигаются на start_ts кадра."""
    if not clips:
        return []
    paths = [c.path for c in clips if c.duration > 0]
    chunks = transcribe_words_many(paths, model_name=model_name, language=language)
    words: list[WordTS] = []
    chunk_idx = 0
    for clip in clips:
        if clip.duration <= 0:
            continue
        chunk = chunks[chunk_idx]
        chunk_idx += 1
        for w in chunk:
            words.append(WordTS(
                word=w.word,
                start=round(w.start + clip.start_ts, 3),
                end=round(w.end + clip.start_ts, 3),
                prob=w.prob,
            ))
    return words
