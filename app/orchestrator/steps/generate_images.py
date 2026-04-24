"""Шаг 6–7: для каждого кадра — промт картинки (ChatGPT web) + генерация
(outsee nano-banana-2). В конце — HITL-гейт approve_images на весь набор.
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
from app.models import (
    Artifact,
    ArtifactKind,
    Frame,
    FrameStatus,
    HITLKind,
    Project,
    ProjectStatus,
    PromptKey,
)
from app.services.hitl import send_hitl_text
from app.services.prompts import get_active_prompt
from app.settings import settings


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.hero_ready:
        return
    logger.info("[#{}] generate_images starting", project.id)

    image_master = await get_active_prompt(session, PromptKey.IMAGE_SHORTS)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("нет кадров — нечего генерировать")

    out_dir = Path(settings.data_dir) / "videos" / project.slug / "scenes"
    hero_line = ""
    if project.hero_description:
        hero_line = (
            "\n\nЭталонное описание главного героя (использовать, если он в кадре):\n"
            + project.hero_description
        )

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        outsee = OutseeBot(bs)

        for fr in frames:
            if fr.status in (FrameStatus.image_generated, FrameStatus.image_approved,
                             FrameStatus.animation_prompt_ready, FrameStatus.video_generated,
                             FrameStatus.video_approved, FrameStatus.done):
                continue

            # 1) промт картинки
            prompt_ask = (
                image_master
                + hero_line
                + "\n\n---\n\nЗадача: составь ОДИН готовый текст промта для генерации "
                + "картинки этого кадра (на английском, строго по правилам выше, "
                + "включая блок `--no ...` в конце).\n\n"
                + f"Номер кадра: {fr.number}\n"
                + f"Длительность: {fr.duration_seconds} сек\n"
                + f"Закадровый текст: {fr.voiceover_text}\n"
                + (f"Смысл: {fr.meaning}\n" if fr.meaning else "")
            )
            image_prompt = await gpt.ask_fresh(prompt_ask, timeout=240)
            if not image_prompt or len(image_prompt) < 40:
                raise RuntimeError(f"пустой image_prompt на кадре {fr.number}")
            fr.image_prompt = image_prompt
            fr.status = FrameStatus.image_prompt_ready
            await session.flush()

            # 2) генерация картинки
            file_path = out_dir / f"frame_{fr.number:03d}_{uuid.uuid4().hex[:8]}.png"
            result = await outsee.generate_image(image_prompt, file_path, aspect_ratio="9:16")
            art = Artifact(
                project_id=project.id,
                frame_id=fr.id,
                kind=ArtifactKind.scene_image,
                uuid=uuid.uuid4().hex,
                path=str(result.file_path),
            )
            session.add(art)
            fr.status = FrameStatus.image_generated
            await session.flush()
            logger.info("[#{}] frame {} image: {}", project.id, fr.number, result.file_path)

    project.status = ProjectStatus.images_ready
    await session.flush()

    await send_hitl_text(
        bot, session, project,
        kind=HITLKind.approve_images,
        title=f"Картинки #{project.id}",
        text=(
            f"Сгенерированы {len(frames)} картинок. "
            f"Посмотри в `{out_dir}` и одобри, "
            "если всё ок. (Превью в Telegram будет добавлено позже — пока без пред-просмотра.)"
        ),
        payload={"step": "images", "count": len(frames)},
    )
