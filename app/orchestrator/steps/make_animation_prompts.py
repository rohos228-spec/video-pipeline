"""Шаг 8: для каждого кадра — текстовый анимационный промт через ChatGPT web.
Мастер-промт VIDEO_SHORTS пока черновой; пользователь позже пришлёт финальный
и мы заменим prompts/VIDEO_SHORTS.v*.md.
"""

from __future__ import annotations

from aiogram import Bot  # noqa: F401
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.bots.browser import browser_session
from app.bots.chatgpt import ChatGPTBot
from app.generation_options import (
    DEFAULTS,
    VIDEO_GENERATORS_BY_ID,
    VIDEO_RESOLUTIONS_BY_ID,
    render_settings_for_gpt,
)
from app.models import Frame, FrameStatus, Project, ProjectStatus, PromptKey
from app.services.prompts import get_active_prompt
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_animation_prompts:
        return
    logger.info("[#{}] make_animation_prompts starting", project.id)

    video_master = await get_active_prompt(session, PromptKey.VIDEO_SHORTS)
    tech_block = render_settings_for_gpt(
        project.image_generator,
        project.aspect_ratio,
        project.image_resolution,
        project.video_generator,
        project.video_resolution,
    )
    video_master = tech_block + "\n" + video_master
    # Описание модели для компактной строки в per-frame задаче
    vg = VIDEO_GENERATORS_BY_ID.get(
        project.video_generator or DEFAULTS["video_generator"]
    )
    vr_o = VIDEO_RESOLUTIONS_BY_ID.get(
        project.video_resolution or DEFAULTS["video_resolution"]
    )
    video_label = (
        f"{vg.label if vg else 'Veo 3.1 Fast'}, "
        f"{vr_o.label if vr_o else '1080p'}, 8 сек, "
        f"{project.aspect_ratio or '9:16'}"
    )
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        for fr in frames:
            if fr.animation_prompt:
                continue
            ask = (
                video_master
                + "\n\n---\n\nЗадача: составь ОДИН промт для анимации следующего кадра "
                + f"(генератор: {video_label}). Без лишних пояснений, только текст промта.\n\n"
                + f"Номер кадра: {fr.number}\n"
                + f"Длительность: {fr.duration_seconds} сек\n"
                + f"Закадровый текст: {fr.voiceover_text}\n"
                + f"Изобразительный промт (контекст кадра):\n{fr.image_prompt or '—'}\n"
            )
            reply = await gpt.ask_fresh(ask, timeout=240)
            if not reply or len(reply) < 30:
                raise RuntimeError(f"пустой animation_prompt на кадре {fr.number}")
            fr.animation_prompt = reply
            fr.status = FrameStatus.animation_prompt_ready
            await session.flush()
            try:
                _sheet_for_project(project).write_frame(
                    fr.number,
                    animation_prompt=reply,
                    frame_status=fr.status.value,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[#{}] xlsx write_frame(animation_prompt) failed: {}",
                    project.id,
                    e,
                )

    project.status = ProjectStatus.animation_prompts_ready
    await session.flush()
    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_general(status) failed: {}", project.id, e)
