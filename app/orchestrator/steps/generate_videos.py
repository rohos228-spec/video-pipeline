"""Шаг 9: для каждого кадра — сгенерировать 8-сек клип в outsee veo-3-fast
Relax, используя картинку кадра как стартовый кадр. В конце — HITL approve_videos.
"""

from __future__ import annotations

import asyncio
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
from app.services.step_cancel import StepCancelledError, consume_stop, raise_if_cancelled


async def _scene_video_file_on_disk(
    session: AsyncSession, project_id: int, frame_id: int
) -> Path | None:
    """Последний scene_video артефакт кадра, если файл реально есть на диске."""
    art = (
        await session.execute(
            select(Artifact)
            .where(
                Artifact.project_id == project_id,
                Artifact.frame_id == frame_id,
                Artifact.kind == ArtifactKind.scene_video,
            )
            .order_by(Artifact.id.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if art is None or not art.path:
        return None
    path = Path(art.path)
    return path if path.is_file() else None


def _skip_frame_video_generation(fr: Frame, has_video_file: bool) -> bool:
    """Не гонять outsee, если клип уже есть или кадр финально одобрен."""
    if fr.status in (FrameStatus.video_approved, FrameStatus.done):
        return True
    if fr.status is FrameStatus.video_generated and has_video_file:
        return True
    return False


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

        skipped = 0
        generated = 0
        try:
            for fr in frames:
                # ⏹ Остановить — проверка между кадрами.
                raise_if_cancelled(project.id)
                has_video = (
                    await _scene_video_file_on_disk(session, project.id, fr.id)
                    is not None
                )
                if _skip_frame_video_generation(fr, has_video):
                    skipped += 1
                    logger.debug(
                        "[#{}] frame {} skip video (status={}, has_file={})",
                        project.id,
                        fr.number,
                        fr.status.value,
                        has_video,
                    )
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
                # Relax (Безлимит): None = не задан → включаем по умолчанию.
                # False = пользователь явно отключил.
                video_relax = project.video_relax is not False
                # До 3 попыток с исходным animation_prompt; если все 3 провалились
                # — GPT-rewrite (убирает триггеры модерации) + ещё 3 попытки.
                result = await generate_video_with_retries(
                    outsee, gpt,
                    prompt=fr.animation_prompt,
                    out_path=file_path,
                    max_attempts_per_prompt=3,
                    gpt_rewrite=True,
                    project_id=project.id,
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
                generated += 1
                logger.info("[#{}] frame {} video: {}", project.id, fr.number, result.file_path)
        except StepCancelledError as e:
            consume_stop(project.id)
            logger.info("[#{}] generate_videos: {} — выхожу из цикла",
                        project.id, e)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return
        except asyncio.CancelledError:
            logger.info("[#{}] generate_videos: hard-cancel (⏹)", project.id)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                pass
            raise

    logger.info(
        "[#{}] generate_videos done loop: frames={} generated={} skipped={}",
        project.id,
        len(frames),
        generated,
        skipped,
    )
    if not frames:
        logger.warning("[#{}] generate_videos: нет кадров — split не делали?", project.id)
    elif generated == 0 and skipped == len(frames):
        logger.info(
            "[#{}] generate_videos: все кадры уже с клипом — videos_ready",
            project.id,
        )

    raise_if_cancelled(project.id)
    await session.refresh(project)
    if project.status is not ProjectStatus.generating_videos:
        logger.info(
            "[#{}] generate_videos: статус уже {} — не ставлю videos_ready (⏹?)",
            project.id,
            project.status.value,
        )
        return

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
