"""Главный pipeline в ручном режиме (управляется из Telegram-меню).

Никаких авто-переходов между шагами. Воркер видит только «running»-статусы и
запускает соответствующий шаг. После шага статус становится «*_ready», и
проект ждёт действия пользователя из бота. Все «ready»-статусы воркером
пропускаются.

Маппинг running-status → step.run:
  planning                       → make_plan
  scripting                      → make_script
  splitting                      → split_frames
  scene_designing                → scene_design.run (5 агентов параллельно)
  scene_assembling               → scene_design.run_assemble (сборщик)
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


async def _sync_storage_after_advance(
    session: AsyncSession, project: Project, running_status: ProjectStatus
) -> None:
    """После любого шага — забрать артефакты во все storage по стрелкам.

    plan/script/split/enrich уже синкают сами; повтор идемпотентен (fingerprint).
    Без этого hero/img/video/anim_pr/audio «успешно» бежали, а хранилище пустое.
    """
    from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE
    from app.services.excel_gpt_node import slot_from_running_status
    from app.services.storage_step_sync import sync_storage_after_step

    # excel_gpt: sync внутри enrich_xlsx по конкретному node_key
    if slot_from_running_status(running_status) is not None:
        return
    node_type = RUNNING_TO_NODE_TYPE.get(running_status)
    if not node_type:
        return
    try:
        await sync_storage_after_step(
            session,
            project,
            node_type,
            log_prefix=f"advance/{node_type}",
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "advance #{}: storage sync after {} failed",
            project.id,
            node_type,
            exc_info=True,
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

    project_id = int(project.id)
    task = asyncio.current_task()
    if task is not None:
        register_advance_task(project_id, task)
    ran_status: ProjectStatus | None = None
    _step_lock_cm = None
    try:
        abort_if_cancelled(project_id)
        status = project.status
        ran_status = status
        logger.debug("advance #{} status={}", project_id, status.value)

        from app.services.llm_override import bind_project_llm

        # Параллельные проекты + SQLite: split — строго по одному (иначе db is locked).
        from app.services.step_global_lock import (
            acquire_step_lock,
            step_code_from_status,
        )

        _step_lock_cm = acquire_step_lock(step_code_from_status(status))
        await _step_lock_cm.__aenter__()

        # UI SSoT: NodeRun → running, иначе на ноде нет «в работе».
        try:
            from app.orchestrator.auto_advance import _prepare_node_run_for_status

            await _prepare_node_run_for_status(
                session, project, status, allow_restart=True
            )
        except Exception:  # noqa: BLE001
            logger.debug(
                "advance #{}: prepare NodeRun for {} failed",
                project_id,
                status.value,
                exc_info=True,
            )

        with bind_project_llm(project, status):
            if status is ProjectStatus.planning:
                await make_plan.run(session, project, bot)
            elif status is ProjectStatus.scripting:
                await make_script.run(session, project, bot)
            elif status is ProjectStatus.splitting:
                await split_frames.run(session, project)
            elif status is ProjectStatus.scene_designing:
                from app.orchestrator.steps import scene_design

                await scene_design.run(session, project, bot)
            elif status is ProjectStatus.scene_assembling:
                from app.orchestrator.steps import scene_design

                await scene_design.run_assemble(session, project, bot)
            elif status is ProjectStatus.generating_hero:
                await generate_hero.run(session, project, bot)
            elif status is ProjectStatus.generating_items:
                await generate_items.run(session, project, bot)
            elif status in (
                ProjectStatus.enriching_1,
                ProjectStatus.enriching_2,
                ProjectStatus.enriching_3,
                ProjectStatus.enriching_4,
                ProjectStatus.enriching_5,
            ):
                await enrich_xlsx.run(session, project, bot)
            elif status is ProjectStatus.generating_image_prompts:
                await generate_image_prompts.run(session, project, bot)
            elif status is ProjectStatus.generating_images:
                await generate_images.run(session, project, bot)
            elif status is ProjectStatus.generating_animation_prompts:
                await make_animation_prompts.run(session, project, bot)
            elif status is ProjectStatus.generating_videos:
                await generate_videos.run(session, project, bot)
            elif status is ProjectStatus.generating_music:
                from app.orchestrator.steps import generate_music

                await generate_music.run(session, project, bot)
            elif status is ProjectStatus.sfx_planning:
                from app.orchestrator.steps import plan_sfx

                await plan_sfx.run(session, project, bot)
            elif status is ProjectStatus.generating_sfx:
                from app.orchestrator.steps import generate_sfx

                await generate_sfx.run(session, project, bot)
            elif status is ProjectStatus.generating_audio:
                await generate_audio.run(session, project, bot)
            elif status is ProjectStatus.assembling:
                await assemble.run(session, project, bot)
            elif status is ProjectStatus.publishing:
                await publish.run(session, project, bot)
            else:
                ran_status = None

        if ran_status is not None:
            await _sync_storage_after_advance(session, project, ran_status)
    finally:
        if _step_lock_cm is not None:
            try:
                await _step_lock_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
        unregister_advance_task(project_id)
