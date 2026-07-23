"""Перемонтаж финального ролика при сбитой синхронизации озвучки и кадров.

Не трогает scene_video (клипы Outsee). Делает:
  1. project.xlsx → voiceover_text кадров (строка 49 / лист «план»)
  2. Сброс только шага assemble (удаляет старый final.mp4)
  3. Повторное выравнивание готовой озвучки (Whisper) по тексту кадров
  4. Новая сборка FFmpeg
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.chatgpt_xlsx import sync_project_xlsx
from app.services.event_bus import publish_project_event
from app.services.frame_audio import find_voice_full_on_disk
from app.services.montage_board_meta import montage_meta, set_montage_meta
from app.services.project_state import compute_actual_status
from app.services.reset_step import reset_step
from app.services.step_cancel import StepCancelledError, raise_if_cancelled


async def _delete_audio_artifacts(session: AsyncSession, project: Project) -> int:
    """Снять audio/whisper из БД — файлы voice_full на диске не удаляем."""
    arts = (
        await session.execute(
            select(Artifact).where(
                Artifact.project_id == project.id,
                Artifact.kind.in_(
                    (ArtifactKind.audio, ArtifactKind.whisper_words)
                ),
            )
        )
    ).scalars().all()
    for art in arts:
        await session.delete(art)
    return len(arts)


async def _publish_remount_progress(
    session: AsyncSession,
    project: Project,
    *,
    phase: str,
    detail: str = "",
) -> None:
    """Commit status + phase в meta — UI видит generating_audio/assembling во время ASR."""
    board = montage_meta(project)
    job = dict(board.get("montage_job") or {})
    if job.get("status") == "running":
        job["phase"] = phase
        if detail:
            job["phase_detail"] = detail
        else:
            job.pop("phase_detail", None)
        set_montage_meta(project, {"montage_job": job})
    await session.flush()
    await session.commit()
    await publish_project_event(
        project.id,
        event_type="project_updated",
        payload={
            "montage_board_montage": True,
            "status": job.get("status", "running"),
            "phase": phase,
            "project_status": project.status.value,
        },
    )


async def remount_video(
    session: AsyncSession,
    project: Project,
    *,
    run_assemble: bool = True,
    bot: Any = None,
) -> dict[str, Any]:
    """Перемонтировать ролик: заново выровнять озвучку по кадрам и собрать mp4."""
    raise_if_cancelled(project.id)
    summary: dict[str, Any] = {
        "project_id": project.id,
        "slug": project.slug,
        "topic": project.topic,
    }

    from app.services.ensure_frames_from_disk import bootstrap_project_frames_from_disk

    boot = await bootstrap_project_frames_from_disk(session, project, sync_xlsx=True)
    if boot:
        summary["disk_bootstrap"] = boot

    frames_before = (
        await session.execute(
            select(Frame)
            .where(Frame.project_id == project.id)
            .order_by(Frame.number.asc())
        )
    ).scalars().all()
    if not frames_before:
        summary["error"] = (
            "нет кадров в БД — положите clip_*/frame_* в videos/scenes "
            "или project.xlsx и повторите"
        )
        return summary

    xlsx = project.data_dir / "project.xlsx"
    if xlsx.is_file():
        try:
            sync_info = await sync_project_xlsx(
                session,
                project,
                xlsx,
                keep_fields=True,
                update_frames_voiceover=True,
            )
            summary["xlsx_sync"] = sync_info
        except Exception as exc:  # noqa: BLE001
            logger.warning("[#{}] remount: xlsx sync failed: {}", project.id, exc)
            summary["xlsx_sync_error"] = str(exc)
    else:
        summary["xlsx_sync"] = {"skipped": "no project.xlsx"}

    voice_path = find_voice_full_on_disk(project.data_dir, meta=project.meta if isinstance(project.meta, dict) else None)
    if voice_path is None:
        audio_art = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.kind == ArtifactKind.audio,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if audio_art and audio_art.path and Path(audio_art.path).is_file():
            voice_path = Path(audio_art.path)
    if voice_path is None or not voice_path.is_file():
        summary["error"] = (
            "нет готовой озвучки (audio/voice*.mp3) — положите файл или пройдите шаг «Аудио»"
        )
        return summary
    summary["voice_file"] = str(voice_path)
    logger.info(
        "[#{}] remount: полный ASR + сборка по {} ({:.0f} MB)",
        project.id,
        voice_path.name,
        voice_path.stat().st_size / 1_000_000,
    )

    reset_info = await reset_step(session, project, "assemble")
    summary["assemble_reset"] = reset_info
    raise_if_cancelled(project.id)

    deleted = await _delete_audio_artifacts(session, project)
    summary["audio_artifacts_removed"] = deleted

    from app.orchestrator.steps import generate_audio

    if bot is None:
        from app.telegram.noop_bot import get_worker_bot

        bot = get_worker_bot(None)

    project.status = ProjectStatus.generating_audio
    voice_secs = voice_path.stat().st_size / 1_000_000  # rough; probe below
    try:
        from app.services.media_probe import probe_duration

        voice_secs = await probe_duration(voice_path)
    except Exception:  # noqa: BLE001
        pass
    eta_min = max(5, int(voice_secs / 60 * 0.25))
    await _publish_remount_progress(
        session,
        project,
        phase="asr",
        detail=f"полный ASR {voice_secs:.0f}s (~{eta_min}+ мин, не закрывайте backend)",
    )
    await generate_audio.run(session, project, bot, force_full_asr=True)
    summary["audio_status"] = project.status.value
    raise_if_cancelled(project.id)

    if project.status is not ProjectStatus.audio_ready:
        summary["error"] = (
            f"выравнивание озвучки не завершилось: status={project.status.value}"
        )
        return summary

    await _publish_remount_progress(session, project, phase="assemble_prep")

    if not run_assemble:
        summary["done"] = True
        summary["next"] = "запустите шаг «Сборка» (assemble) в Studio"
        return summary

    from app.orchestrator.steps import assemble as assemble_mod

    project.status = ProjectStatus.assembling
    await _publish_remount_progress(session, project, phase="assemble", detail="FFmpeg сборка")
    try:
        await assemble_mod.run(session, project, bot)
    except Exception as exc:  # noqa: BLE001
        logger.exception("[#{}] remount: assemble failed", project.id)
        summary["error"] = str(exc)
        summary["final_status"] = project.status.value
        return summary
    summary["final_status"] = project.status.value
    raise_if_cancelled(project.id)

    if project.status is ProjectStatus.assembled:
        summary["done"] = True
        out = project.data_dir / "final" / f"{project.slug}.mp4"
        summary["final_video"] = str(out) if out.is_file() else None
    else:
        summary["error"] = f"сборка не завершилась: status={project.status.value}"

    actual = await compute_actual_status(session, project)
    if project.status != actual:
        project.status = actual
        await session.flush()
        summary["recomputed_status"] = actual.value

    return summary


async def find_project_by_topic_fragment(
    session: AsyncSession,
    fragment: str,
) -> Project | None:
    needle = (fragment or "").strip().casefold()
    if not needle:
        return None
    rows = (await session.execute(select(Project).order_by(Project.id.asc()))).scalars().all()
    for p in rows:
        hay = f"{p.topic or ''} {p.slug or ''}".casefold()
        if needle in hay:
            return p
    return None
