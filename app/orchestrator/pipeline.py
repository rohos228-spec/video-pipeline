"""Главный pipeline: стейт-машина, которая на основе `Project.status` и состояния
последнего HITL-запроса решает, какой шаг запустить следующим.

Логика шагов:
  new                       → создаётся в Telegram-боте (вручную)
  planning                  → make_plan         (ChatGPT web)           → plan_ready
                                                 + HITL approve_plan
  plan_ready (approved)     → make_script       (ChatGPT web)           → script_ready
                                                 + HITL approve_script
  script_ready (approved)   → split_frames                              → frames_ready
  frames_ready              → generate_hero     (nano-banana-2)         → hero_ready
                                                 + HITL approve_hero
                              или пропускаем, если hero_mode=no_hero
  hero_ready                → generate_images   (nano-banana-2)         → images_ready
                                                 + HITL approve_images
  images_ready (approved)   → make_animation_prompts (ChatGPT web)      → animation_prompts_ready
  animation_prompts_ready   → generate_videos   (veo-3-fast Relax)      → videos_ready
                                                 + HITL approve_videos
  videos_ready (approved)   → generate_audio    (11Labs web)            → audio_ready
  audio_ready               → assemble          (Whisper → FFmpeg)      → assembled
                                                 + HITL approve_final
  assembled (approved)      → publish           (MoreLogin)             → published
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.orchestrator.steps import (
    assemble,
    generate_audio,
    generate_hero,
    generate_images,
    generate_videos,
    make_animation_prompts,
    make_plan,
    make_script,
    publish,
    split_frames,
)


async def _latest_hitl(
    session: AsyncSession, project_id: int, kind: HITLKind
) -> HITLRequest | None:
    return (
        await session.execute(
            select(HITLRequest)
            .where(HITLRequest.project_id == project_id, HITLRequest.kind == kind)
            .order_by(desc(HITLRequest.id))
            .limit(1)
        )
    ).scalar_one_or_none()


async def advance_project(session: AsyncSession, project: Project, bot: Bot) -> None:
    """Один такт стейт-машины. Если на этапе стоит HITL-гейт, а решения ещё нет —
    возвращаемся; воркер зайдёт позже."""
    status = project.status
    logger.debug("advance #{} status={}", project.id, status.value)

    if status is ProjectStatus.planning:
        await make_plan.run(session, project, bot)
        return

    if status is ProjectStatus.plan_ready:
        decision = await _gate(session, project, HITLKind.approve_plan, on_back=None)
        if decision is HITLDecision.approved:
            await make_script.run(session, project, bot)
        elif decision is HITLDecision.regenerate:
            project.status = ProjectStatus.planning
            project.general_plan = None
        elif decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    if status is ProjectStatus.script_ready:
        decision = await _gate(session, project, HITLKind.approve_script, on_back=ProjectStatus.plan_ready)
        if decision is HITLDecision.approved:
            await split_frames.run(session, project)
        elif decision is HITLDecision.regenerate:
            # Перегенерировать сценарий — откатимся к plan_ready, make_script будет вызван вновь.
            # Сбросим script_text и удалим последний approve_plan (чтобы не прошёл by default)? —
            # проще: перегенерируем сценарий принудительно.
            project.status = ProjectStatus.plan_ready
            project.script_text = None
        elif decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    if status is ProjectStatus.frames_ready:
        await generate_hero.run(session, project, bot)
        return

    if status is ProjectStatus.hero_ready:
        # HITL approve_hero — если запрос был, ждём решения. Если ГГ пропущен
        # (hero_mode=no_hero), HITL-запроса нет — едем сразу на images.
        req = await _latest_hitl(session, project.id, HITLKind.approve_hero)
        if req is None:
            await generate_images.run(session, project, bot)
            return
        if req.decision is HITLDecision.approved:
            await generate_images.run(session, project, bot)
        elif req.decision is HITLDecision.regenerate:
            # Откат на frames_ready — generate_hero увидит последний HITL
            # approve_hero=regenerate и дёрнет «Повторить» на outsee (без
            # похода в ChatGPT и без перезаполнения промта).
            project.status = ProjectStatus.frames_ready
        elif req.decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    if status is ProjectStatus.images_ready:
        decision = await _gate(
            session, project, HITLKind.approve_images, on_back=ProjectStatus.hero_ready
        )
        if decision is HITLDecision.approved:
            await make_animation_prompts.run(session, project, bot)
        elif decision is HITLDecision.regenerate:
            project.status = ProjectStatus.hero_ready
        elif decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    if status is ProjectStatus.animation_prompts_ready:
        await generate_videos.run(session, project, bot)
        return

    if status is ProjectStatus.videos_ready:
        decision = await _gate(
            session, project, HITLKind.approve_videos,
            on_back=ProjectStatus.animation_prompts_ready,
        )
        if decision is HITLDecision.approved:
            await generate_audio.run(session, project, bot)
        elif decision is HITLDecision.regenerate:
            project.status = ProjectStatus.animation_prompts_ready
        elif decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    if status is ProjectStatus.audio_ready:
        await assemble.run(session, project, bot)
        return

    if status is ProjectStatus.assembled:
        decision = await _gate(
            session, project, HITLKind.approve_final, on_back=ProjectStatus.audio_ready
        )
        if decision is HITLDecision.approved:
            await publish.run(session, project, bot)
        elif decision is HITLDecision.regenerate:
            # пересоберём финальный видеофайл из тех же клипов/аудио
            project.status = ProjectStatus.audio_ready
        elif decision is HITLDecision.rejected:
            project.status = ProjectStatus.failed
        return

    # published — терминальный статус


async def _gate(
    session: AsyncSession,
    project: Project,
    kind: HITLKind,
    on_back: ProjectStatus | None,
) -> HITLDecision:
    """Возвращает решение последнего HITL-запроса указанного типа (или pending,
    если запроса ещё не было — в этом случае шаг-генератор должен был его создать;
    если нет — считаем, что ждём)."""
    req = await _latest_hitl(session, project.id, kind)
    if req is None:
        logger.warning("[#{}] gate {}: нет HITL-запроса, ждём", project.id, kind.value)
        return HITLDecision.pending
    return req.decision
