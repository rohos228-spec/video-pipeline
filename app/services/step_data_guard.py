"""Проверка данных в БД/на диске перед переходом на следующий running-шаг."""

from __future__ import annotations

from pathlib import Path

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, ArtifactKind, Frame, Project, ProjectStatus
from app.services.artifact_recovery import (
    recover_audio_from_disk,
    recover_scene_videos_from_disk,
)
from app.services.project_state import compute_actual_status
from app.services.xlsx_v8_import import read_v8_active_frame_count
from app.telegram.menu import status_order


async def _count_kind(session: AsyncSession, project_id: int, kind: ArtifactKind) -> int:
    return (
        await session.execute(
            select(func.count(Artifact.id)).where(
                Artifact.project_id == project_id,
                Artifact.kind == kind,
            )
        )
    ).scalar_one()


async def _frames_with_voiceover(session: AsyncSession, project: Project) -> int:
    xlsx = project.data_dir / "project.xlsx"
    n = read_v8_active_frame_count(xlsx) if xlsx.is_file() else 0
    if n > 0:
        return n
    return (
        await session.execute(
            select(func.count(Frame.id)).where(
                Frame.project_id == project.id,
                Frame.voiceover_text.isnot(None),
                Frame.voiceover_text != "",
            )
        )
    ).scalar_one()


async def can_enter_running(
    session: AsyncSession,
    project: Project,
    target: ProjectStatus,
) -> tuple[bool, str, ProjectStatus | None]:
    """Можно ли ставить `target` (running) по фактическим данным.

    Возвращает (ok, reason, suggested_status при ok=False).
    """
    need_frames = await _frames_with_voiceover(session, project)
    if need_frames == 0:
        return False, "нет кадров с voiceover", ProjectStatus.frames_ready

    if target is ProjectStatus.generating_videos:
        imgs = await _count_kind(session, project.id, ArtifactKind.scene_image)
        if imgs < need_frames:
            return (
                False,
                f"картинок {imgs}/{need_frames}",
                ProjectStatus.image_prompts_ready,
            )
        return True, "", None

    if target is ProjectStatus.generating_audio:
        await recover_scene_videos_from_disk(session, project)
        vids = await _count_kind(session, project.id, ArtifactKind.scene_video)
        if vids < need_frames:
            return (
                False,
                f"видео-клипов {vids}/{need_frames}",
                ProjectStatus.videos_ready,
            )
        return True, "", None

    if target is ProjectStatus.assembling:
        await recover_scene_videos_from_disk(session, project)
        await recover_audio_from_disk(session, project)
        vids = await _count_kind(session, project.id, ArtifactKind.scene_video)
        if vids == 0:
            return (
                False,
                "нет ни одного видео-клипа",
                ProjectStatus.generating_videos,
            )
        audio = (
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
        if audio is None or not Path(audio.path).is_file():
            return False, "нет артефакта аудио", ProjectStatus.generating_audio
        return True, "", None

    return True, "", None


async def clamp_status_to_data(
    session: AsyncSession, project: Project
) -> ProjectStatus | None:
    """Если status «впереди» данных — откатить к compute_actual_status."""
    actual = await compute_actual_status(session, project)
    if status_order(project.status) > status_order(actual):
        old = project.status
        project.status = actual
        await session.flush()
        logger.warning(
            "[#{}] clamp_status_to_data: {} → {} (статус опережал данные)",
            project.id,
            old.value,
            actual.value,
        )
        return actual
    return None
