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
from app.bots.chatgpt import ChatGPTBot
from app.bots.outsee import OutseeBot
from app.generation_options import (
    ASPECT_RATIOS_BY_ID,
    DEFAULTS,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS_BY_ID,
    build_gen_id_prefix,
)
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
from app.services.outsee_retry import generate_video_with_retries
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_videos:
        return
    logger.info("[#{}] generate_videos starting", project.id)

    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    out_dir = project.data_dir / "videos"

    # Настройки видео из проекта (с дефолтами).
    vg = VIDEO_GENERATORS_BY_ID.get(
        project.video_generator or DEFAULTS["video_generator"]
    )
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(
        project.video_resolution or DEFAULTS["video_resolution"]
    )
    ar = ASPECT_RATIOS_BY_ID.get(
        project.aspect_ratio or DEFAULTS["aspect_ratio"]
    )
    video_model_slug = vg.outsee_slug if vg else None
    video_res_slug = vr_o.outsee_slug if vr_o else None
    aspect_slug = ar.outsee_slug if ar else "9:16"

    async with browser_session() as bs:
        outsee = OutseeBot(bs)
        # `gpt` — для GPT-rewrite внутри generate_video_with_retries:
        # после 3 неудачных попыток в outsee он попросит ChatGPT переписать
        # animation_prompt без триггеров модерации, потом ещё 3 попытки.
        gpt = ChatGPTBot(bs)

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

            short_uuid = uuid.uuid4().hex[:8]
            file_path = out_dir / f"clip_{fr.number:03d}_{short_uuid}.mp4"
            prompt_id_prefix = build_gen_id_prefix(
                project.id, fr.number, short_uuid
            )
            # Relax по словам пользователя поддерживает только veo-3-1-fast.
            # Для остальных моделей даже если флаг True — _toggle_relax
            # тихо ничего не сделает (кнопки нет).
            video_relax = bool(project.video_relax) and (
                project.video_generator == "veo_3_1_fast"
            )
            # До 3 попыток с исходным animation_prompt; если все 3 провалились
            # — GPT-rewrite (убирает триггеры модерации) + ещё 3 попытки.
            result = await generate_video_with_retries(
                outsee, gpt,
                prompt=fr.animation_prompt,
                out_path=file_path,
                max_attempts_per_prompt=3,
                gpt_rewrite=True,
                start_frame=start_frame_path,
                aspect_ratio=aspect_slug,
                timeout=1200,
                model_slug=video_model_slug,
                resolution=video_res_slug,
                relax=video_relax,
                prompt_id_prefix=prompt_id_prefix,
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
            f"Папка: `{out_dir}`. "
            "Одобри, если всё ок — начну сборку аудио и финала."
        ),
        payload={"step": "videos", "count": len(frames)},
    )
