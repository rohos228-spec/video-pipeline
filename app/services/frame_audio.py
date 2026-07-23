"""Вариант B: озвучка по ячейкам plan R49 — один mp3 на кадр."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from app.bots.elevenlabs import ElevenLabsBot
from app.models import Frame, Project
from app.services.elevenlabs_voices import resolve_elevenlabs_voice_id
from app.services.mapper import map_frames
from app.services.media_probe import probe_duration
from app.services.voiceover_split_local import split_voiceover_locally
from app.services.asr import active_asr_backend, transcribe_words, transcribe_words_many
from app.services.whisper import WordTS

FRAME_AUDIO_PREFIX = "frame_"

_VOICE_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac"})

_USER_VOICE_BASENAMES = frozenset({
    "voice_full",
    "voice",
    "voiceover",
    "ozvuchka",
    # Кнопка панели монтажа раньше писала voice_montage.* — тоже принимаем.
    "voice_montage",
})


def _is_user_voice_file(path: Path) -> bool:
    """Готовая полная озвучка (не frame_NNN и не words_*.json)."""
    if not path.is_file():
        return False
    ext = path.suffix.lower()
    if ext not in _VOICE_EXTENSIONS:
        return False
    stem = path.stem.lower()
    if stem.startswith(FRAME_AUDIO_PREFIX) or stem.startswith("words_"):
        return False
    if stem.startswith("voice_full_"):
        return True
    return stem in _USER_VOICE_BASENAMES


def is_protected_voice_or_music_file(path: Path) -> bool:
    """Пользовательские озвучка/музыка — никогда не удалять с диска."""
    if _is_user_voice_file(path):
        return True
    if not path.is_file():
        return False
    name = path.name.lower()
    if name.startswith("bgm.") or name.startswith("music_"):
        return True
    if path.parent.name == "music" and path.suffix.lower() in _VOICE_EXTENSIONS:
        return True
    if path.parent.name == "audio" and path.suffix.lower() in _VOICE_EXTENSIONS:
        if path.stem.lower().startswith("words_"):
            return False
        if path.stem.lower().startswith(FRAME_AUDIO_PREFIX):
            return False
        return True
    return False


def find_voice_full_on_disk(data_dir: Path, *, meta: dict | None = None) -> Path | None:
    """Готовая озвучка на диске (без 11Labs): audio/voice*.{mp3,wav,...} или в корне.

    Также учитывает ``meta["montage_voice_path"]`` после upload с панели монтажа.
    """
    if meta:
        hinted = meta.get("montage_voice_path")
        if hinted:
            hp = Path(str(hinted))
            if hp.is_file():
                return hp
    if not data_dir.is_dir():
        return None
    candidates: list[Path] = []
    audio_dir = data_dir / "audio"
    if audio_dir.is_dir():
        for path in audio_dir.iterdir():
            if _is_user_voice_file(path):
                candidates.append(path)
    for path in data_dir.iterdir():
        if _is_user_voice_file(path):
            candidates.append(path)
    if not candidates:
        return None
    unique = list({p.resolve(): p for p in candidates}.values())
    chosen = max(unique, key=lambda p: p.stat().st_mtime)
    return chosen

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
    per_frame_tts: bool = False,
) -> tuple[list[FrameAudioClip], float, float, bool]:
    """Озвучка — единственное мерило: voice_full задаёт конец ролика.

    Per-frame: границы кадров = ffprobe(frame_NNN.mp3), без rescale.
    При расхождении voice_full пересобирается из клипов.

    Returns (clips, master_duration, time_scale, uses_per_frame_clips).
    """
    master = await probe_duration(voice_full_path)

    if per_frame_tts and not has_all_frame_audio(audio_dir, frame_numbers):
        logger.warning(
            "per_frame в meta, но frame_*.mp3 нет — таймлайн по voice_full + Whisper"
        )
        per_frame_tts = False

    if per_frame_tts and has_all_frame_audio(audio_dir, frame_numbers):
        clips = await load_frame_clips_from_disk(audio_dir, frame_numbers)
        raw_sum = sum(c.duration for c in clips)
        if raw_sum <= 0:
            raise RuntimeError("сумма длительностей frame_*.mp3 равна нулю")

        if abs(raw_sum - master) > 0.05:
            logger.info(
                "voice_full {:.2f}s != clips {:.2f}s — пересборка из frame_*.mp3",
                master,
                raw_sum,
            )
            paths = [frame_audio_path(audio_dir, n) for n in frame_numbers]
            await concat_mp3_files(paths, voice_full_path)
            master = await probe_duration(voice_full_path)

        if abs(raw_sum - master) > 0.15:
            logger.warning(
                "drift voice_full {:.2f}s vs clips {:.2f}s — видео по сумме клипов",
                master,
                raw_sum,
            )
            master = raw_sum

        if clips and abs(clips[-1].end_ts - master) > 0.01:
            clips[-1].end_ts = round(master, 3)
            clips[-1].duration = round(clips[-1].end_ts - clips[-1].start_ts, 3)

        return clips, master, 1.0, True

    if has_all_frame_audio(audio_dir, frame_numbers) and not per_frame_tts:
        logger.warning(
            "frame_*.mp3 на диске, но не от TTS — игнорируем. "
            "Сбросьте «Аудио» и перезапустите для per-frame озвучки."
        )

    if not cells or not words:
        raise FileNotFoundError(
            "нет таймкодов Whisper (words.json) — перезапустите шаг «Аудио» "
            "или включите faster-whisper на сборке"
        )

    logger.info(
        "сборка по voice_full {:.2f}s + Whisper (legacy stretch субтитры)",
        master,
    )
    clips = frame_clips_from_whisper(cells, words, master, voice_full_path)
    return clips, master, 1.0, False


def resolve_full_voiceover_text(project: Project) -> str:
    """Полный закадровый текст — voiceover.txt или script_text в БД."""
    vo_path = project.data_dir / "voiceover.txt"
    if vo_path.is_file():
        text = vo_path.read_text(encoding="utf-8").strip()
        if text:
            return text
    if project.script_text:
        return (project.script_text or "").strip()
    return ""


def _voiceover_cells_for_frames(
    project: Project,
    frames: list[Frame],
    cells: list[tuple[int, str]],
) -> list[tuple[int, str]]:
    """Ячейки R49; если пусто — voiceover.txt, БД, локальный split."""
    cell_map = {n: (t or "").strip() for n, t in cells}
    frame_by_num = {fr.number: fr for fr in frames}
    out: list[tuple[int, str]] = []
    for fr in frames:
        text = cell_map.get(fr.number, "")
        if not text:
            text = (fr.voiceover_text or "").strip()
        out.append((fr.number, text))

    if all(t for _, t in out):
        return out
    if not any(t for _, t in out):
        full = resolve_full_voiceover_text(project)
        if not full:
            return out
        blocks = split_voiceover_locally(full)
        if len(blocks) < len(frames):
            raise RuntimeError(
                f"voiceover: {len(blocks)} блоков после split, нужно {len(frames)} кадров"
            )
        return [(fr.number, blocks[i]) for i, fr in enumerate(frames)]

    empty_n = sum(1 for _, t in out if not t)
    full = resolve_full_voiceover_text(project)
    if full:
        blocks = split_voiceover_locally(full)
        if len(blocks) >= len(frames):
            logger.warning(
                "[#{}] voiceover_cells: R49 частично пуст ({}/{} кадров) — "
                "тайминги из voiceover.txt",
                project.id,
                empty_n,
                len(frames),
            )
            return [(fr.number, blocks[i]) for i, fr in enumerate(frames)]

    return out


def frame_clips_equal_duration(
    frames: list[Frame],
    master: float,
    voice_full_path: Path,
) -> list[FrameAudioClip]:
    """Равные доли mp3 по кадрам, когда нет текста для align."""
    if not frames:
        return []
    n = len(frames)
    step = master / n
    clips: list[FrameAudioClip] = []
    pos = 0.0
    for i, fr in enumerate(frames):
        end = master if i == n - 1 else pos + step
        clips.append(FrameAudioClip(
            frame_number=fr.number,
            path=voice_full_path,
            text="",
            start_ts=round(pos, 3),
            end_ts=round(end, 3),
            duration=round(end - pos, 3),
        ))
        pos = end
    return clips


async def _extract_mp3_segment(
    src: Path,
    out_path: Path,
    start_ts: float,
    end_ts: float,
) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    dur = max(0.05, end_ts - start_ts)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start_ts:.3f}",
        "-t", f"{dur:.3f}",
        "-i", str(src),
    ]
    # wav/m4a → mp3: stream copy невозможен, нужен encode.
    if src.suffix.lower() == ".mp3" and out_path.suffix.lower() == ".mp3":
        cmd.extend(["-c", "copy"])
    else:
        cmd.extend(["-vn", "-c:a", "libmp3lame", "-q:a", "2"])
    cmd.append(str(out_path))
    await _run_ffmpeg(cmd)

async def synthesize_per_frame_audio(
    el: ElevenLabsBot,
    *,
    project: Project,
    frames: list[Frame],
    cells: list[tuple[int, str]],
    audio_dir: Path,
    clip_timeout: float = 180.0,
    whisper_model: str = "large-v3",
    language: str = "ru",
) -> tuple[list[FrameAudioClip], Path, list[WordTS]]:
    """Озвучка: весь voiceover одним запросом в 11Labs, тайминги — Whisper."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    # Только frame_NNN.mp3 от прошлого 11Labs — не трогаем voice_full/voice*.wav
    delete_frame_audio_files(audio_dir)

    full_text = resolve_full_voiceover_text(project)
    if len(full_text) < 50:
        raise RuntimeError(
            "нет voiceover.txt / script_text — сначала шаг «Закадровый текст»"
        )

    cells = _voiceover_cells_for_frames(project, frames, cells)
    if not any(t for _, t in cells):
        raise RuntimeError(
            "нет закадрового текста на листе «план» (строка 49) и не удалось разбить voiceover"
        )

    full_path = audio_dir / f"voice_full_{uuid.uuid4().hex[:8]}.mp3"
    tts_timeout = max(clip_timeout * max(len(frames), 1), 600.0)
    logger.info(
        "[#{}] frame_audio: полный voiceover ({} симв.) → {}",
        project.id,
        len(full_text),
        full_path.name,
    )
    voice_id = resolve_elevenlabs_voice_id(project)
    logger.info("[#{}] frame_audio: 11Labs voice_id={}", project.id, voice_id)
    await el.tts(
        full_text,
        full_path,
        timeout=tts_timeout,
        voice_id=voice_id,
        project_id=project.id,
    )

    master = await probe_duration(full_path)
    words = transcribe_words(
        full_path,
        model_name=whisper_model,
        language=language,
    )
    clips = frame_clips_from_whisper(cells, words, master, full_path)

    for clip in clips:
        seg_path = frame_audio_path(audio_dir, clip.frame_number)
        await _extract_mp3_segment(
            full_path,
            seg_path,
            clip.start_ts,
            clip.end_ts,
        )
        clip.path = seg_path

    return clips, full_path, words


async def align_existing_voice_full(
    project: Project,
    frames: list[Frame],
    cells: list[tuple[int, str]],
    voice_path: Path,
    audio_dir: Path,
    *,
    whisper_model: str,
    language: str = "ru",
) -> tuple[list[FrameAudioClip], Path, list[WordTS]]:
    """Whisper + таймкоды кадров по готовому mp3 — без 11Labs."""
    voice_path = voice_path.resolve()
    if not voice_path.is_file():
        raise FileNotFoundError(f"озвучка не найдена: {voice_path}")

    audio_dir.mkdir(parents=True, exist_ok=True)
    master = await probe_duration(voice_path)
    logger.info(
        "[#{}] align_existing_voice_full: {} по {:.2f}s файлу …",
        project.id,
        active_asr_backend(),
        master,
    )
    words = await asyncio.to_thread(
        transcribe_words,
        voice_path,
        model_name=whisper_model,
        language=language,
        beam_size=1 if master > 300 else 5,
    )
    aligned_cells = _voiceover_cells_for_frames(project, frames, cells)
    if any(t for _, t in aligned_cells):
        clips = frame_clips_from_whisper(aligned_cells, words, master, voice_path)
    else:
        logger.warning(
            "[#{}] align_existing_voice_full: нет текста R49/voiceover — "
            "таймкоды кадров поровну по {:.2f}s",
            project.id,
            master,
        )
        clips = frame_clips_equal_duration(frames, master, voice_path)

    # Для импорта готового voice_full нарезка frame_NNN.mp3 не нужна — сборка
    # идёт по voice_full + words.json (mode=disk_whisper).
    for clip in clips:
        clip.path = voice_path

    if voice_path.parent == audio_dir:
        full_path = voice_path
    else:
        full_path = audio_dir / f"voice_full_{uuid.uuid4().hex[:8]}{voice_path.suffix.lower()}"
        if full_path.resolve() != voice_path.resolve():
            full_path.write_bytes(voice_path.read_bytes())

    logger.info(
        "[#{}] align_existing_voice_full: {} → {:.2f}s, {} words, {} clips",
        project.id,
        voice_path.name,
        master,
        len(words),
        len(clips),
    )
    return clips, full_path, words


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
