"""Главный pipeline: стейт-машина, которая на основе `Project.status` решает,
какой шаг запустить следующим. Отдельные шаги живут в app.orchestrator.steps.*
и будут наполняться поэтапно.

Логика шагов (коротко):
  new              → создаётся в боте
  planning         → step_make_plan            (ChatGPT web)
  plan_ready       → step_make_script          (ChatGPT web)   [HITL: approve_plan]
  script_ready     → step_split_into_frames
  frames_ready     → step_generate_hero        (outsee nano-banana-2)  [HITL: approve_hero]
                     или skip if hero_mode=no_hero
  hero_ready       → step_generate_images      (outsee nano-banana-2 на каждый кадр)
                                                [GPT Vision auto-check,
                                                 HITL: approve_images]
  images_ready     → step_make_animation_prompts (ChatGPT web)
  animation_prompts_ready → step_generate_videos (outsee veo-3-fast Relax)
                                                [GPT Vision auto-check,
                                                 HITL: approve_videos]
  videos_ready     → step_generate_audio       (11Labs web)
  audio_ready      → step_assemble             (Whisper → Mapper → FFmpeg)
                                                [HITL: approve_final]
  assembled        → step_publish              (MoreLogin: TikTok, YT Shorts,
                                                IG Reels, VK Клипы, Likee)
  published        → терминальный
"""

from __future__ import annotations

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus


async def advance_project(session: AsyncSession, project: Project) -> None:
    """Продвинуть проект на следующий шаг в зависимости от статуса.

    Сейчас — только заглушки-роутер. Конкретные шаги будут реализованы
    последовательно (MVP → image → animation → video → audio → assembly → publish).
    """
    status = project.status
    logger.debug("advance #{} status={}", project.id, status.value)

    # TODO: пока ничего не делаем — просто логируем.
    # Реализация шагов:
    #   from app.orchestrator.steps import make_plan, make_script, ...
    #   if status == ProjectStatus.planning: await make_plan.run(session, project)
    #   elif status == ProjectStatus.plan_ready: await make_script.run(session, project)
    #   ...
    if status == ProjectStatus.planning:
        # Поставить HITL-запрос на одобрение после генерации плана
        # Генерация через ChatGPT web (browser bot)
        pass
    elif status == ProjectStatus.plan_ready:
        pass
    elif status == ProjectStatus.script_ready:
        pass
    elif status == ProjectStatus.frames_ready:
        pass
    elif status == ProjectStatus.hero_ready:
        pass
    elif status == ProjectStatus.images_ready:
        pass
    elif status == ProjectStatus.animation_prompts_ready:
        pass
    elif status == ProjectStatus.videos_ready:
        pass
    elif status == ProjectStatus.audio_ready:
        pass
    elif status == ProjectStatus.assembled:
        pass
    else:
        return
