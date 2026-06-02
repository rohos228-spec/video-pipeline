"""Главный pipeline в ручном режиме (управляется из Telegram-меню).

Никаких авто-переходов между шагами. Воркер видит только «running»-статусы и
запускает соответствующий шаг. После шага статус становится «*_ready», и
проект ждёт действия пользователя из бота. Все «ready»-статусы воркером
пропускаются.

Маппинг running-status → step.run:
  planning                       → make_plan
  scripting                      → make_script
  splitting                      → split_frames
  generating_hero                → generate_hero
  generating_image_prompts       → generate_image_prompts (только промты)
  generating_images              → generate_images        (только картинки)
  generating_animation_prompts   → make_animation_prompts
  generating_videos              → generate_videos
  generating_audio               → generate_audio
  assembling                     → assemble
  publishing                     → publish

Переходы между шагами инициирует пользователь, тыкая кнопки в бот-меню.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.steps import (
    assemble,
    enrich_xlsx,
    generate_audio,
    generate_hero,
    generate_image_prompts,
    generate_images,
    generate_items,
    generate_videos,
    make_animation_prompts,
    make_plan,
    make_script,
    publish,
    split_frames,
)


async def advance_project(session: AsyncSession, project: Project, bot: Bot) -> None:
    """Один такт стейт-машины. Запускает шаг, если статус — «running»; иначе
    ничего не делает (ждём, пока пользователь нажмёт кнопку в боте)."""
    import asyncio

    from app.services.step_cancel import (
        abort_if_cancelled,
        register_advance_task,
        unregister_advance_task,
    )

    task = asyncio.current_task()
    if task is not None:
        register_advance_task(project.id, task)
    try:
        abort_if_cancelled(project.id)
        status = project.status
        logger.debug("advance #{} status={}", project.id, status.value)

        if status is ProjectStatus.planning:
            await make_plan.run(session, project, bot)
            return

        if status is ProjectStatus.scripting:
            await make_script.run(session, project, bot)
            return

        if status is ProjectStatus.splitting:
            await split_frames.run(session, project)
            return

        if status is ProjectStatus.generating_hero:
            await generate_hero.run(session, project, bot)
            return

        if status is ProjectStatus.generating_items:
            await generate_items.run(session, project, bot)
            return

        if status in (
            ProjectStatus.enriching_1,
            ProjectStatus.enriching_2,
            ProjectStatus.enriching_3,
            ProjectStatus.enriching_4,
            ProjectStatus.enriching_5,
        ):
            await enrich_xlsx.run(session, project, bot)
            return

        if status is ProjectStatus.generating_image_prompts:
            await generate_image_prompts.run(session, project, bot)
            return

        if status is ProjectStatus.generating_images:
            await generate_images.run(session, project, bot)
            return

        if status is ProjectStatus.generating_animation_prompts:
            await make_animation_prompts.run(session, project, bot)
            return

        if status is ProjectStatus.generating_videos:
            await generate_videos.run(session, project, bot)
            return

        if status is ProjectStatus.generating_music:
            from app.orchestrator.steps import generate_music

            await generate_music.run(session, project, bot)
            return

        if status is ProjectStatus.generating_audio:
            await generate_audio.run(session, project, bot)
            return

        if status is ProjectStatus.assembling:
            await assemble.run(session, project, bot)
            return

        if status is ProjectStatus.publishing:
            await publish.run(session, project, bot)
            return
    finally:
        unregister_advance_task(project.id)
