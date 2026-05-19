"""Шаг 11: финальная сборка ролика FFmpeg — нарезка клипов по реальным
длительностям из Whisper, concat, наложение mp3, ASS-субтитры, затем HITL
approve_final.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.assembly import ClipSpec, assemble, make_simple_ass
from app.services.hitl import send_hitl_video
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.assembling:
        return
    logger.info("[#{}] assemble starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров")

    # найдём аудио
    audio = (
        await session.execute(
            select(Artifact)
            .where(Artifact.project_id == project.id, Artifact.kind == ArtifactKind.audio)
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if audio is None:
        raise RuntimeError("нет артефакта аудио")

    # клипы по кадрам.
    # Кадры в FrameStatus.failed пропускаем — это либо «❌ Отклонить» из
    # per-video HITL, либо «нет картинки-источника». Их клип в финальный
    # ролик не вставляем, длительность хронометража просто сжимается.
    clips: list[ClipSpec] = []
    used_frames: list[Frame] = []
    skipped: list[int] = []
    for fr in frames:
        if fr.status is FrameStatus.failed:
            skipped.append(fr.number)
            continue
        video_art = (
            await session.execute(
                select(Artifact)
                .where(
                    Artifact.project_id == project.id,
                    Artifact.frame_id == fr.id,
                    Artifact.kind == ArtifactKind.scene_video,
                )
                .order_by(Artifact.id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if video_art is None:
            raise RuntimeError(f"нет клипа для кадра {fr.number}")
        # На случай если файл артефакта удалён orphan-cleanup'ом /
        # delete_hitl_artifact_file — пропускаем кадр.
        if not Path(video_art.path).is_file():
            logger.warning(
                "[#{}] frame {}: scene_video artifact указывает на "
                "отсутствующий файл {} — пропускаю кадр",
                project.id, fr.number, video_art.path,
            )
            skipped.append(fr.number)
            continue
        duration = fr.duration_seconds or ((fr.end_ts or 0.0) - (fr.start_ts or 0.0))
        if duration <= 0:
            raise RuntimeError(f"длительность кадра {fr.number} ≤ 0")
        clips.append(ClipSpec(src=Path(video_art.path), duration=float(duration)))
        used_frames.append(fr)
    if not clips:
        raise RuntimeError(
            "нет ни одного клипа для финальной сборки "
            f"(всего кадров: {len(frames)}, пропущено: {len(skipped)})"
        )
    if skipped:
        logger.info(
            "[#{}] assemble: пропущено кадров {} (failed/без клипа): {}",
            project.id, len(skipped), skipped,
        )

    # субтитры
    subs_dir = project.data_dir / "subs"
    subs_path = subs_dir / f"subs_{uuid.uuid4().hex[:8]}.ass"
    # Только реально вошедшие в сборку кадры — иначе строка субтитра
    # появится в пустом месте таймлайна.
    make_simple_ass(
        [
            ((fr.start_ts or 0.0), (fr.end_ts or 0.0), fr.voiceover_text or "")
            for fr in used_frames
        ],
        subs_path,
    )
    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.subtitle,
        uuid=uuid.uuid4().hex, path=str(subs_path),
    ))

    # сборка
    out_dir = project.data_dir / "final"
    out_path = out_dir / f"{project.slug}.mp4"
    await assemble(clips, Path(audio.path), out_path, subtitles_ass=subs_path)

    session.add(Artifact(
        project_id=project.id, kind=ArtifactKind.final_video,
        uuid=uuid.uuid4().hex, path=str(out_path),
    ))
    project.status = ProjectStatus.assembled
    await session.flush()

    # HITL approve_final
    await send_hitl_video(
        bot, session, project,
        kind=HITLKind.approve_final,
        video_path=str(out_path),
        caption=f"Финальный ролик #{project.id} готов. Одобрить и публиковать?",
        payload={"step": "final"},
    )
