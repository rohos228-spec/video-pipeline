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
from app.models import Frame, FrameStatus, Project, ProjectStatus
from app.services.prompt_library import get_project_prompt
from app.services.step_cancel import StepCancelledError, consume_stop, raise_if_cancelled
from app.storage import for_project as _sheet_for_project


async def run(session: AsyncSession, project: Project, bot: Bot) -> None:
    if project.status is not ProjectStatus.generating_animation_prompts:
        return
    logger.info("[#{}] make_animation_prompts starting", project.id)

    video_master = get_project_prompt(project, "anim_pr")
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()

    async with browser_session() as bs:
        gpt = ChatGPTBot(bs)
        try:
            for fr in frames:
                # ⏹ Остановить — проверка между кадрами.
                raise_if_cancelled(project.id)
                if fr.animation_prompt:
                    continue
                ask = (
                    video_master
                    + "\n\n---\n\nЗадача: составь ОДИН промт для анимации следующего кадра. "
                    + "Без лишних пояснений, только текст промта.\n\n"
                    + f"Номер кадра: {fr.number}\n"
                    + f"Длительность: {fr.duration_seconds} сек\n"
                    + f"Закадровый текст: {fr.voiceover_text}\n"
                    + f"Изобразительный промт (контекст кадра):\n{fr.image_prompt or '—'}\n"
                )
                reply = await gpt.ask_fresh(ask, timeout=240, project_id=project.id)
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
        except StepCancelledError as e:
            consume_stop(project.id)
            logger.info("[#{}] make_animation_prompts: {} — выхожу из цикла",
                        project.id, e)
            try:
                await session.refresh(project)
            except Exception:  # noqa: BLE001
                logger.warning("[#{}] не смог refresh project после ⏹", project.id)
            return

    project.status = ProjectStatus.animation_prompts_ready
    await session.flush()
    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] xlsx write_general(status) failed: {}", project.id, e)
