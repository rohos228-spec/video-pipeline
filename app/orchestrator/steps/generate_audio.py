"""Шаг 10: озвучка — VO из БД (Frame.voiceover_text).

Готовый mp3 на диске → только Whisper (без 11Labs).
Иначе: TTS по ячейкам → voice_full → Whisper внутри synthesize_per_frame_audio.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    Project,
    ProjectStatus,
)
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
    recover_whisper_from_disk,
)
from app.services.frame_audio import (
    FrameAudioClip,
    align_existing_voice_full,
    find_voice_full_on_disk,
    synthesize_per_frame_audio,
)
from app.services.mapper import extract_local_frame_words
from app.services.media_probe import probe_duration
from app.services.asr import active_asr_backend
from app.services.whisper import WordTS, dump_words_json
from app.services.audio_verification import AudioStepError, classify_audio_failure, verify_audio
from app.settings import settings


def _emit(kind: str, payload: dict) -> None:
    try:
        from app.services.observation import emit

        emit(kind, payload)
    except Exception:  # noqa: BLE001
        pass


def _report_failure(project: Project, exc: Exception) -> None:
    """Единый путь ошибок звука: тип сбоя, русский текст, факт в журнале; исключение не подменяется."""
    failure_class, text_ru = classify_audio_failure(exc)
    try:
        exc.failure_class = failure_class  # type: ignore[attr-defined]
        exc.operator_text = text_ru  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    logger.error("[#{}] generate_audio сбой [{}]: {}", project.id, failure_class, exc)
    _emit("audio_step_failure", {"node": "audio", "failure_class": failure_class, "error_class": type(exc).__name__,
                                 "text_ru": text_ru, "detail": str(exc)[:400]})


async def _latest_artifact(
    session: AsyncSession,
    project_id: int,
    kind: ArtifactKind,
) -> Artifact | None:
    return (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.kind == kind,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def _persist_audio_results(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
    clips: list[FrameAudioClip],
    full_audio_path: Path,
    words: list[WordTS],
    audio_dir: Path,
    *,
    source: str,
    cells: list[tuple[int, str]] | None = None,
) -> None:
    expected_total = clips[-1].end_ts if clips else 0.0
    verdict = verify_audio(full_audio_path, expect_duration=expected_total or None,
                           duration_tol=max(1.0, 0.05 * expected_total))
    _emit("audio_result_verification", {"node": "audio", "source": source, **verdict.to_dict()})
    if not verdict.ok:
        raise AudioStepError("verification_failed", "; ".join(verdict.problems), verdict=verdict)
    for fr in frames:
        clip = next(c for c in clips if c.frame_number == fr.number)
        fr.start_ts = clip.start_ts
        fr.end_ts = clip.end_ts
        fr.duration_seconds = clip.duration

    audio_duration = await probe_duration(full_audio_path)
    expected = clips[-1].end_ts if clips else 0.0
    if abs(audio_duration - expected) > 0.15:
        logger.warning(
            "[#{}] voice_full duration {:.2f}s != sum clips {:.2f}s",
            project.id,
            audio_duration,
            expected,
        )

    clip_meta = [
        {
            "frame_number": c.frame_number,
            "start_ts": c.start_ts,
            "end_ts": c.end_ts,
            "duration": c.duration,
            "text": c.text,
        }
        for c in clips
    ]
    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.audio,
        uuid=uuid.uuid4().hex,
        path=str(full_audio_path),
        meta={
            "mode": "disk_whisper" if source == "disk_whisper" else "per_frame",
            "source": source,
            "clip_count": len(clips),
            "clips": clip_meta,
            "verification": {"ok": True, "duration": verdict.duration, "format": verdict.format,
                             "peak_db": verdict.peak_db, "rms_db": verdict.rms_db},
        },
    ))
    await session.flush()

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
    whisper_meta: dict[str, str] = {}
    if cells:
        from app.services.frame_timeline_sync import _r49_content_hash

        whisper_meta["r49_hash"] = _r49_content_hash(cells)
    art_uuid = uuid.uuid4().hex
    session.add(Artifact(
        project_id=project.id,
        kind=ArtifactKind.whisper_words,
        uuid=art_uuid,
        path=str(words_path),
        meta=whisper_meta or None,
    ))
    from app.services.asr import active_asr_backend
    from app.services.asr_words_store import replace_project_asr_words

    await replace_project_asr_words(
        session,
        project.id,
        words,
        backend=active_asr_backend(),
        artifact_uuid=art_uuid,
        frame_segments=frame_segments,
    )

    logger.info(
        "[#{}] generate_audio done: {} frames, {:.2f}s total, {} whisper words ({})",
        project.id,
        len(clips),
        clips[-1].end_ts if clips else 0.0,
        len(words),
        source,
    )


async def _finalize_audio_ready(
    session: AsyncSession,
    project: Project,
) -> bool:
    from app.services.post_step_validate import finalize_or_retry

    if not await finalize_or_retry(
        session,
        project,
        step="audio",
        ready_status=ProjectStatus.audio_ready,
        running_status=ProjectStatus.generating_audio,
    ):
        return False

    project.status = ProjectStatus.audio_ready
    await session.flush()
    return True


async def run(
    session: AsyncSession,
    project: Project,
    bot: Bot | None = None,
    *,
    force_full_asr: bool = False,
) -> None:
    try:
        await _run(session, project, bot, force_full_asr=force_full_asr)
    except Exception as exc:  # noqa: BLE001  # отмена (BaseException) проходит мимо
        _report_failure(project, exc)
        raise


async def _run(
    session: AsyncSession,
    project: Project,
    bot: Bot | None = None,
    *,
    force_full_asr: bool = False,
) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return
    logger.info("[#{}] generate_audio starting (per-frame TTS, VO from DB)", project.id)

    await recover_scene_videos_from_disk(session, project)
    await recover_audio_from_disk(session, project)
    if force_full_asr:
        logger.info(
            "[#{}] generate_audio: remount — полный ASR по voice_full, stale words.json игнорируем",
            project.id,
        )
    else:
        await recover_whisper_from_disk(session, project)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise AudioStepError("no_frames", "нет кадров")

    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    voice_path = find_voice_full_on_disk(
        project.data_dir,
        meta=project.meta if isinstance(project.meta, dict) else None,
    )
    if voice_path is None:
        audio_art = await _latest_artifact(session, project.id, ArtifactKind.audio)
        if audio_art is not None and audio_art.path and Path(audio_art.path).is_file():
            voice_path = Path(audio_art.path)
    if voice_path is not None:
        logger.info(
            "[#{}] generate_audio: озвучка на диске → {}",
            project.id,
            voice_path,
        )
    else:
        logger.warning(
            "[#{}] generate_audio: файла озвучки нет в {} — {}",
            project.id,
            project.data_dir / "audio",
            "11Labs" if settings.audio_use_elevenlabs_fallback else "ошибка (11Labs выкл.)",
        )

    from app.services.frame_timeline_sync import timeline_frames_and_cells

    timeline_frames, cells = timeline_frames_and_cells(project, frames)
    if not timeline_frames:
        raise AudioStepError(
            "missing_text",
            "нет закадрового текста в БД (Frame.voiceover_text) — "
            "сделай split/Импорт Excel или заполни Базу",
        )

    if voice_path is not None and voice_path.is_file():
        logger.info(
            "[#{}] generate_audio: озвучка на диске → {} — {} + align R49",
            project.id,
            voice_path,
            active_asr_backend(),
        )
        cached_words: list[WordTS] | None = None
        if not force_full_asr:
            whisper_art = await _latest_artifact(session, project.id, ArtifactKind.whisper_words)
            if whisper_art is not None and whisper_art.path:
                wp = Path(whisper_art.path)
                from app.services.whisper import load_words_json, whisper_words_fresh_for_audio

                if wp.is_file() and whisper_words_fresh_for_audio(whisper_art, voice_path):
                    cached_words = load_words_json(wp)
                    if cached_words:
                        logger.info(
                            "[#{}] generate_audio: words.json актуален — ASR пропущен",
                            project.id,
                        )
        clips, full_audio_path, words = await align_existing_voice_full(
            project,
            timeline_frames,
            cells,
            voice_path,
            audio_dir,
            whisper_model=settings.whisper_model,
            existing_words=cached_words,
        )
        await _persist_audio_results(
            session,
            project,
            timeline_frames,
            clips,
            full_audio_path,
            words,
            audio_dir,
            source="disk_whisper",
            cells=cells,
        )
        await _finalize_audio_ready(session, project)
        return

    if not any(text.strip() for _, text in cells):
        raise AudioStepError(
            "missing_text",
            "нет закадрового текста (строка 49 / voiceover.txt / script_text) — "
            "положите готовый mp3/wav в audio/ или заполните текст",
        )

    import os
    use_11labs = (
        getattr(settings, "audio_use_elevenlabs_fallback", False)
        or os.environ.get("AUDIO_USE_ELEVENLABS_FALLBACK", "").strip() in ("1", "true", "True")
        or bool(getattr(settings, "elevenlabs_api_key", None))
    )
    if not use_11labs:
        audio_hint = project.data_dir / "audio"
        raise AudioStepError(
            "no_voice_source",
            f"[#{project.id}] нет озвучки в {audio_hint} — положите voice.mp3 или "
            f"voice_full_*.wav (проект «{project.slug}»). "
            "11Labs отключён: AUDIO_USE_ELEVENLABS_FALLBACK=0",
        )

    from app.services.elevenlabs_api import tts_run_budget

    with tts_run_budget():
        clips, full_audio_path, words = await synthesize_per_frame_audio(
            project=project,
            frames=timeline_frames,
            cells=cells,
            audio_dir=audio_dir,
            whisper_model=settings.whisper_model,
        )

    await _persist_audio_results(
        session,
        project,
        timeline_frames,
        clips,
        full_audio_path,
        words,
        audio_dir,
        source="elevenlabs",
        cells=cells,
    )
    await _finalize_audio_ready(session, project)
