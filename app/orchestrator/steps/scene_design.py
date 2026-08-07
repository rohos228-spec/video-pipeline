"""Нода scene_design: мульти-агентная режиссура сцен (между split и hero).

5 категорийных GPT-агентов параллельно (characters/world/style/camera/action)
→ финальный агент-сборщик → валидация → apply-ops (scene_registry + attrs
кадров). Выключена (SCENE_DESIGN_ENABLED=false и нет per-project override) —
прозрачный pass-through в scene_design_ready.
"""

from __future__ import annotations

from aiogram import Bot
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project, ProjectStatus
from app.storage import for_project as _sheet_for_project

_MAX_ASSEMBLE_ATTEMPTS = 2


async def run(session: AsyncSession, project: Project, bot: Bot | None = None) -> None:
    if project.status is not ProjectStatus.scene_designing:
        return
    from app.services import db_v2
    from app.services.scene_design import apply as sd_apply
    from app.services.scene_design import assembler as sd_assembler
    from app.services.scene_design import context_builder, runner

    logger.info("[#{}] scene_design starting", project.id)

    if not runner.scene_design_enabled(project):
        logger.info("[#{}] scene_design disabled — pass-through", project.id)
        project.status = ProjectStatus.scene_design_ready
        await session.flush()
        await session.commit()
        return

    await db_v2.backfill_project_v2(session, project)
    frames = (
        await session.execute(
            select(Frame).where(Frame.project_id == project.id).order_by(Frame.number)
        )
    ).scalars().all()
    if not frames:
        raise RuntimeError("scene_design: нет кадров — сначала split")
    if any(not fr.uuid for fr in frames):
        raise RuntimeError("scene_design: у кадров нет uuid — backfill не сработал")

    runner.mark_running(project)
    await session.flush()
    # Отпустить SQLite write-lock на время параллельных GPT-вызовов.
    await session.commit()

    try:
        context = context_builder.build_shared_context(project, frames)
        slices = await runner.run_category_agents(project, context)
        # Чекпоинты агентов уже в meta — зафиксировать до долгой сборки.
        await session.commit()

        full_vo = context_builder.full_voiceover(project, frames)
        payload = None
        feedback: str | None = None
        for attempt in range(1, _MAX_ASSEMBLE_ATTEMPTS + 1):
            candidate = await runner.run_assembler(
                project, context, slices, feedback=feedback
            )
            problems = sd_assembler.validate_payload(project, frames, candidate, full_vo)
            if not problems:
                payload = candidate
                break
            feedback = "\n".join(problems)
            logger.warning(
                "[#{}] scene_design assemble attempt {}/{} problems: {}",
                project.id,
                attempt,
                _MAX_ASSEMBLE_ATTEMPTS,
                problems,
            )
        if payload is None:
            raise RuntimeError(
                f"scene_design: сборка не прошла валидацию: {feedback}"
            )

        applied = await sd_apply.apply_scene_design(session, project, payload)
        runner.mark_done(project, str(payload.get("report") or ""))
        project.status = ProjectStatus.scene_design_ready
        await session.flush()
        await session.commit()
    except Exception as e:
        runner.mark_failed(project, str(e))
        import contextlib

        with contextlib.suppress(Exception):
            await session.commit()
        raise

    logger.info(
        "[#{}] scene_design done: ops={} characters={} scenes={}",
        project.id,
        applied.get("updated"),
        applied.get("characters"),
        applied.get("scenes"),
    )

    try:
        from app.services.storage_step_sync import sync_storage_after_step

        await sync_storage_after_step(
            session, project, "scene_design", log_prefix="scene_design"
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] scene_design: storage sync failed: {}", project.id, e)

    try:
        _sheet_for_project(project).write_general(status=project.status.value)
    except Exception as e:  # noqa: BLE001
        logger.warning("[#{}] project_sheet scene_design write failed: {}", project.id, e)

    from app.services.agent_harness import harness_gate_or_raise

    await harness_gate_or_raise(session, project, step="scene_d")
    await session.commit()
