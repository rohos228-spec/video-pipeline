"""Шаг 9: для каждого кадра — сгенерировать 8-сек клип в outsee veo-3-fast
Relax, используя картинку кадра как стартовый кадр. В конце — HITL approve_videos.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.outsee import OutseeBot
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLKind,
    Project,
    ProjectStatus,
)
from app.services.hitl import send_hitl_text
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.animation_prompts_ready:
        return
    logger.info("[#{}] generate_videos starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    out_dir = Path(settings.data_dir) / "videos" / project.slug / "videos"

    async with browser_session() as bs:
        outsee = OutseeBot(bs)

        for fr in frames:
            if fr.status in (FrameStatus.video_generated, FrameStatus.video_approved,
                             FrameStatus.done):
                continue
            if not fr.animation_prompt:
                raise RuntimeError(f"у кадра {fr.number} нет animation_prompt")

            # найдём картинку этого кадра (scene_image)
            img = (
                await session.execute(
                    select(Artifact)
                    .where(
                        Artifact.project_id == project.id,
                        Artifact.frame_id == fr.id,
                        Artifact.kind == ArtifactKind.scene_image,
                    )
                    .order_by(Artifact.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            start_frame_path: Path | None = Path(img.path) if img else None

            file_path = out_dir / f"clip_{fr.number:03d}_{uuid.uuid4().hex[:8]}.mp4"
            result = await outsee.generate_video(
                fr.animation_prompt,
                file_path,
                start_frame=start_frame_path,
                aspect_ratio="9:16",
                timeout=1200,
            )
            session.add(
                Artifact(
                    project_id=project.id,
                    frame_id=fr.id,
                    kind=ArtifactKind.scene_video,
                    uuid=uuid.uuid4().hex,
                    path=str(result.file_path),
                )
            )
            fr.status = FrameStatus.video_generated
            await session.flush()
            logger.info("[#{}] frame {} video: {}", project.id, fr.number, result.file_path)

    project.status = ProjectStatus.videos_ready
    await session.flush()

    await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_videos,
        title=f"Клипы #{project.id}",
        text=(
            f"Готово {len(frames)} клипов по 8 сек. "
            f"Папка: `/data/videos/{project.slug}/videos/`. "
            "Одобри, если всё ок — начну сборку аудио и финала."
        ),
        payload={"step": "videos", "count": len(frames)},
    )
