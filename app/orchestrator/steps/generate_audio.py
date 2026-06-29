"""Шаг audio: 11Labs → один voice_full.mp3. ASR и разбивка по кадрам — только на ПК монтажа."""

from __future__ import annotations

import uuid

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.elevenlabs import ElevenLabsBot
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.frame_audio import synthesize_full_voice_only
from app.services.media_probe import probe_duration


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_audio:
        return

    meta = dict(project.meta or {})
    fleet_hub = bool(
        meta.get("fleet_imported")
        or meta.get("fleet_imported_at")
        or meta.get("fleet_source_project_id")
        or meta.get("montage_ready")
    )
    if fleet_hub:
        logger.info("[#{}] generate_audio: fleet hub — recover / local TTS", project.id)
        await recover_scene_videos_from_disk(session, project)
        from app.services.artifact_recovery import ensure_fleet_montage_voice

        if await recover_audio_from_disk(session, project) or await ensure_fleet_montage_voice(
            session, project
        ):
            project.status = ProjectStatus.audio_ready
            await session.flush()
            logger.info("[#{}] generate_audio: озвучка готова → audio_ready", project.id)
            return
        meta["montage_blocked"] = "нет voice_full в audio/ после импорта с worker"
        project.meta = meta
        await session.flush()
        raise RuntimeError(
            "fleet import: нет озвучки на диске (voice_full.mp3/wav) — не генерируем через 11Labs на hub. "
            "Проверьте audio/ на worker и повторите pull."
        )

    logger.info("[#{}] generate_audio starting (full voice, no ASR)", project.id)

    await recover_scene_videos_from_disk(session, project)
    if await recover_audio_from_disk(session, project):
        project.status = ProjectStatus.audio_ready
        await session.flush()
        logger.info("[#{}] generate_audio: озвучка уже на диске — audio_ready", project.id)
        return

    audio_dir = project.data_dir / "audio"

    from app.services.elevenlabs_api import api_key_configured, synthesize_full_voice_api

    if api_key_configured():
        logger.info("[#{}] generate_audio: ElevenLabs REST API (без браузера)", project.id)
        full_audio_path = await synthesize_full_voice_api(
            project=project,
            audio_dir=audio_dir,
        )
    else:
        async with browser_session() as bs:
            el = ElevenLabsBot(bs)
            full_audio_path = await synthesize_full_voice_only(
                el,
                project=project,
                audio_dir=audio_dir,
            )

    audio_duration = await probe_duration(full_audio_path)
    session.add(
        Artifact(
            project_id=project.id,
            kind=ArtifactKind.audio,
            uuid=uuid.uuid4().hex,
            path=str(full_audio_path),
            meta={
                "mode": "full_voice",
                "duration": audio_duration,
                "source": "elevenlabs_api" if api_key_configured() else "elevenlabs_web",
            },
        )
    )
    await session.flush()

    logger.info(
        "[#{}] generate_audio done: voice_full {:.2f}s (ASR deferred to montage hub)",
        project.id,
        audio_duration,
    )

    from app.services.post_step_validate import finalize_or_retry

    if not await finalize_or_retry(
        session,
        project,
        step="audio",
        ready_status=ProjectStatus.audio_ready,
        running_status=ProjectStatus.generating_audio,
    ):
        return

    project.status = ProjectStatus.audio_ready
    await session.flush()
