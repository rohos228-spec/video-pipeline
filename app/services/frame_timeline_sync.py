"""Синхронизация Frame.start_ts/end_ts с voice_full + xlsx + Whisper."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project
from app.services.asr import active_asr_backend
from app.services.frame_audio import (
    FrameAudioClip,
    _voiceover_cells_for_frames,
    align_existing_voice_full,
    find_voice_full_on_disk,
    frame_clips_from_whisper,
)
from app.services.mapper import extract_local_frame_words
from app.services.media_probe import probe_duration
from app.services.whisper import (
    WordTS,
    artifact_path_mtime,
    dump_words_json,
    load_words_json,
    whisper_words_fresh_for_audio,
)
from app.settings import settings
from app.storage.plan_sheet_v8 import read_plan_voiceover_cells

_PLACEHOLDER_VO_RE = re.compile(r"^кадр\s+\d+\.?$", re.IGNORECASE)


def is_placeholder_voiceover(text: str) -> bool:
    return bool(_PLACEHOLDER_VO_RE.match((text or "").strip()))


def timeline_frames_and_cells(
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], list[tuple[int, str]]]:
    """Кадры и ячейки R49 для таймлайна — без заглушек «Кадр N» с диска."""
    numbers = [fr.number for fr in frames]
    raw_cells = read_plan_voiceover_cells(project, numbers)
    cells = _voiceover_cells_for_frames(project, frames, raw_cells)
    filtered_cells: list[tuple[int, str]] = []
    allowed: set[int] = set()
    for frame_number, text in cells:
        t = (text or "").strip()
        if not t or is_placeholder_voiceover(t):
            continue
        filtered_cells.append((frame_number, t))
        allowed.add(frame_number)
    timeline_frames = [fr for fr in frames if fr.number in allowed]
    return timeline_frames, filtered_cells


def frames_missing_timestamps(frames: list[Frame]) -> list[int]:
    missing: list[int] = []
    for fr in frames:
        if fr.start_ts is None or fr.end_ts is None:
            missing.append(fr.number)
    return missing


def clips_look_equal_split(
    clips: list[FrameAudioClip],
    master: float,
    *,
    tolerance: float = 0.12,
) -> bool:
    """True если длительности кадров похожи на равномерный fallback (ошибка align)."""
    if len(clips) < 4 or master <= 0:
        return False
    durations = [c.duration for c in clips if c.duration > 0]
    if len(durations) < 4:
        return False
    avg = sum(durations) / len(durations)
    if avg <= 0:
        return False
    uniform = sum(1 for d in durations if abs(d - avg) / avg < tolerance)
    if uniform < len(durations) * 0.8:
        return False
    fair = master / len(durations)
    return abs(avg - fair) / max(fair, 0.01) < 0.2


def _r49_content_hash(cells: list[tuple[int, str]]) -> str:
    """Хеш текста R49 — меняется только при правке озвучки, не при записи R15."""
    payload = "\n".join(f"{n}\t{(t or '').strip()}" for n, t in cells)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _r49_changed_since_whisper(
    whisper_art: Artifact | None,
    cells: list[tuple[int, str]],
) -> bool:
    """True если текст R49 изменился после последнего ASR (не просто запись R15)."""
    if whisper_art is None:
        return True
    stored = (whisper_art.meta or {}).get("r49_hash")
    if not stored:
        return False
    return stored != _r49_content_hash(cells)


def _xlsx_newer_than_whisper(project: Project, whisper_art: Artifact | None) -> bool:
    xlsx = project.data_dir / "project.xlsx"
    if not xlsx.is_file():
        return False
    if whisper_art is None or not whisper_art.path:
        return True
    whisper_mtime = artifact_path_mtime(whisper_art)
    if whisper_mtime is None:
        return True
    return xlsx.stat().st_mtime > whisper_mtime + 1.0


async def _latest_whisper_artifact(
    session: AsyncSession,
    project_id: int,
) -> Artifact | None:
    return (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == ArtifactKind.whisper_words,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _apply_clips_to_frames(
    frames: list[Frame],
    clips: list[FrameAudioClip],
) -> list[int]:
    by_num = {c.frame_number: c for c in clips}
    updated: list[int] = []
    for fr in frames:
        clip = by_num.get(fr.number)
        if clip is None:
            continue
        fr.start_ts = clip.start_ts
        fr.end_ts = clip.end_ts
        fr.duration_seconds = clip.duration
        updated.append(fr.number)
    return updated


async def _persist_whisper_words(
    session: AsyncSession,
    project: Project,
    clips: list[FrameAudioClip],
    words: list[WordTS],
    audio_dir: Path,
    *,
    cells: list[tuple[int, str]] | None = None,
) -> Path:
    """Сохранить words.json + Artifact после realign."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    frame_segments = [
        {
            "frame_number": clip.frame_number,
            "start_ts": clip.start_ts,
            "end_ts": clip.end_ts,
            "text": clip.text,
            "words": [
                {
                    "word": w.word,
                    "start": w.start,
                    "end": w.end,
                    "prob": w.prob,
                }
                for w in extract_local_frame_words(words, clip.start_ts, clip.end_ts)
            ],
        }
        for clip in clips
    ]
    words_path = audio_dir / f"words_{uuid.uuid4().hex[:8]}.json"
    dump_words_json(words, words_path, frames=frame_segments)
    meta: dict[str, str] = {}
    if cells:
        meta["r49_hash"] = _r49_content_hash(cells)
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.whisper_words,
            uuid=uuid.uuid4().hex,
            path=str(words_path),
            meta=meta or None,
        )
    )
    await session.flush()
    return words_path


async def _realign_with_whisper(
    session: AsyncSession,
    project: Project,
    timeline_frames: list[Frame],
    cells: list[tuple[int, str]],
    voice_path: Path,
    audio_dir: Path,
    *,
    persist_words: bool,
) -> tuple[list[FrameAudioClip], list[WordTS], str]:
    clips, _full_path, words = await align_existing_voice_full(
        project,
        timeline_frames,
        cells,
        voice_path,
        audio_dir,
        whisper_model=settings.whisper_model,
    )
    source = "whisper_realigned"
    if persist_words:
        path = await _persist_whisper_words(
            session, project, clips, words, audio_dir, cells=cells
        )
        source = f"whisper_realigned+persist:{path.name}"
    return clips, words, source


async def sync_frame_timestamps_from_voice(
    session: AsyncSession,
    project: Project,
    frames: list[Frame] | None = None,
    *,
    force_whisper: bool = False,
    persist_whisper: bool = False,
) -> dict[str, Any]:
    """Записать start_ts/end_ts в Frame по voice_full + текст R49 + Whisper."""
    if frames is None:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number.asc())
                )
            ).scalars().all()
        )
    timeline_frames, cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        return {"skipped": "no frames with voiceover text (R49/xlsx)"}
    if not any(t.strip() for _, t in cells):
        return {"skipped": "empty voiceover cells"}

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None or not voice_path.is_file():
        return {"skipped": "voice_full not on disk"}

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    master = await probe_duration(voice_path)
    whisper_art = await _latest_whisper_artifact(session, project.id)

    whisper_fresh = whisper_words_fresh_for_audio(whisper_art, voice_path)
    need_realign = force_whisper or not whisper_fresh
    if not need_realign and _r49_changed_since_whisper(whisper_art, cells):
        need_realign = True
    elif not need_realign and whisper_art is not None and (whisper_art.meta or {}).get("r49_hash") is None:
        # Старые артефакты без r49_hash — fallback на mtime xlsx
        if _xlsx_newer_than_whisper(project, whisper_art):
            need_realign = True

    if need_realign:
        clips, _words, source = await _realign_with_whisper(
            session,
            project,
            timeline_frames,
            cells,
            voice_path,
            audio_dir,
            persist_words=persist_whisper or force_whisper,
        )
    else:
        words = load_words_json(Path(whisper_art.path))  # type: ignore[arg-type]
        clips = frame_clips_from_whisper(cells, words, master, voice_path)
        source = "words_json"
        if clips_look_equal_split(clips, master) and not whisper_fresh:
            logger.warning(
                "[#{}] frame_timeline_sync: равномерный fallback из words.json "
                "({} clips, {:.1f}s) — принудительный {} realign",
                project.id,
                len(clips),
                master,
                active_asr_backend(),
            )
            clips, _words, source = await _realign_with_whisper(
                session,
                project,
                timeline_frames,
                cells,
                voice_path,
                audio_dir,
                persist_words=True,
            )
        elif clips_look_equal_split(clips, master):
            logger.info(
                "[#{}] frame_timeline_sync: words.json свежий — equal-split проверку пропускаем",
                project.id,
            )

    updated = _apply_clips_to_frames(timeline_frames, clips)
    if updated:
        await session.flush()
        logger.info(
            "[#{}] frame_timeline_sync: {} кадров ← {}:{} ({} clips)",
            project.id,
            len(updated),
            active_asr_backend(),
            source,
            len(clips),
        )
    return {
        "source": source,
        "asr_backend": active_asr_backend(),
        "updated": updated,
        "clip_count": len(clips),
        "voice_seconds": round(master, 2),
    }


async def sync_frame_timestamps_if_needed(
    session: AsyncSession,
    project: Project,
    frames: list[Frame] | None = None,
) -> dict[str, Any]:
    """Пересчитать таймкоды если NULL или подозрительный equal-split."""
    if frames is None:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number.asc())
                )
            ).scalars().all()
        )
    timeline_frames, _cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        return {"skipped": "no timeline frames"}

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    master = 0.0
    if voice_path is not None and voice_path.is_file():
        master = await probe_duration(voice_path)

    missing = frames_missing_timestamps(timeline_frames)
    whisper_art = await _latest_whisper_artifact(session, project.id)
    whisper_fresh = bool(
        voice_path is not None
        and voice_path.is_file()
        and whisper_art is not None
        and whisper_words_fresh_for_audio(whisper_art, voice_path)
    )
    suspicious = False
    if not missing and master > 0:
        clips_probe = [
            FrameAudioClip(
                frame_number=fr.number,
                path=voice_path or Path("."),
                text="",
                start_ts=float(fr.start_ts or 0),
                end_ts=float(fr.end_ts or 0),
                duration=float(fr.duration_seconds or 0),
            )
            for fr in timeline_frames
            if fr.start_ts is not None and fr.end_ts is not None
        ]
        suspicious = clips_look_equal_split(clips_probe, master)

    if not missing and not suspicious and not _r49_changed_since_whisper(whisper_art, _cells):
        return {"skipped": "timestamps ok"}
    if not missing and suspicious and whisper_fresh and not _r49_changed_since_whisper(
        whisper_art, _cells
    ):
        return {"skipped": "timestamps ok (fresh words)"}

    reason = []
    if missing:
        reason.append(f"missing:{missing[:10]}")
    if suspicious:
        reason.append("equal_split")
    logger.info(
        "[#{}] frame_timeline_sync: пересчёт ({})",
        project.id,
        ", ".join(reason) or "xlsx_newer",
    )
    return await sync_frame_timestamps_from_voice(
        session,
        project,
        frames,
        persist_whisper=suspicious or bool(missing),
    )


async def sync_frame_timestamps_for_board(
    session: AsyncSession,
    project: Project,
    frames: list[Frame] | None = None,
) -> dict[str, Any]:
    """Обновить Frame.start_ts/end_ts из кэша words.json — без ASR на GET /montage-board."""
    if frames is None:
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == project.id)
                    .order_by(Frame.number.asc())
                )
            ).scalars().all()
        )
    timeline_frames, cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        return {"skipped": "no timeline frames"}

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None or not voice_path.is_file():
        return {"skipped": "voice_full not on disk"}

    whisper_art = await _latest_whisper_artifact(session, project.id)
    if whisper_art is None or not whisper_art.path:
        return {"skipped": "no whisper artifact"}
    if not whisper_words_fresh_for_audio(whisper_art, voice_path):
        return {"skipped": "stale words.json"}

    words_path = Path(whisper_art.path)
    if not words_path.is_file():
        return {"skipped": "words file missing"}

    master = await probe_duration(voice_path)
    words = load_words_json(words_path)
    if not words:
        return {"skipped": "empty words.json"}

    clips = frame_clips_from_whisper(cells, words, master, voice_path)
    if not clips:
        return {"skipped": "map_frames empty"}

    updated = _apply_clips_to_frames(timeline_frames, clips)
    if updated:
        await session.flush()
        logger.info(
            "[#{}] montage_board sync: {} кадров ← words.json (без ASR)",
            project.id,
            len(updated),
        )
    return {
        "source": "words_json",
        "updated": updated,
        "clip_count": len(clips),
        "voice_seconds": round(master, 2),
    }
