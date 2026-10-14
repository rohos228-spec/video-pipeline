"""Запуск advance_project в отдельной asyncio-task — снимается через ⏹."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from aiogram import Bot
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import session_scope
from app.models import Project, ProjectStatus
from app.orchestrator.pipeline import advance_project
from app.project_db import project_db_session_scope
from app.services.run_sync import complete_active_node_for_step


@dataclass(frozen=True)
class AdvanceJobResult:
    project_id: int
    prev_status: str
    new_status: str | None  # None если статус не изменился


def _tick_run_id(project_id: int) -> str:
    """Run of this tick: the confirmed managed execution id, otherwise ``native:<project>:<tick>``."""
    from app.services import request_context
    try:
        from app.services.actions_v1 import get_permission
        permission = get_permission(project_id)
        execution_id = str(getattr(permission, "execution_id", "") or "") if permission is not None else ""
        if execution_id:
            return execution_id
    except Exception:  # noqa: BLE001 - a native id is still a real, unique run id
        pass
    return request_context.native_run_id(project_id)


async def advance_project_job(project_id: int, bot: Bot) -> AdvanceJobResult:
    """Один такт advance_project в своей сессии изолированной проектной БД."""
    from app.services.actions_v1 import worker_entry_admission
    admission=await worker_entry_admission(project_id)
    if not admission.allowed:
        return AdvanceJobResult(project_id,'',None)
    from app.services import request_context
    from app.services.observation import _cost_project
    cost_token = _cost_project.set(project_id)
    # B02: every paid call of this tick carries a project, a run and a request id. Run = the managed execution
    # when the project has one, else a native tick id; request = this tick (the worker has no HTTP request).
    corr_scope = request_context.ensure_request_scope(
        "tick-", run_id=_tick_run_id(project_id), project_id=project_id)
    corr_scope.__enter__()
    try:
        async with project_db_session_scope(project_id) as session:
            project = await session.get(Project, project_id)
            if project is None:
                # Если ещё не в project.db, проверим master
                async with session_scope() as master_sess:
                    project = await master_sess.get(Project, project_id)
                    if project is None:
                        logger.warning("advance_project_job: проект #{} не найден", project_id)
                        return AdvanceJobResult(project_id, "", None)
                    from app.project_db import init_project_db

                    await init_project_db(project.data_dir, project=project)
                project = await session.get(Project, project_id)
                if project is None:
                    logger.warning("advance_project_job: повторное открытие #{} не удалось", project_id)
                    return AdvanceJobResult(project_id, "", None)

            from app.project_db import pull_master_runtime_into_project

            await pull_master_runtime_into_project(session, project)

            prev = project.status.value
            prev_status = project.status
            from app.services.observation import observe_attempt

            async with observe_attempt(session, project):
                await advance_project(session, project, bot)
            new = project.status.value
            if new != prev:
                await complete_active_node_for_step(
                    session,
                    project,
                    prev_status=prev_status,
                    new_status=project.status,
                )
            # Сначала фиксируем project.db, потом показываем итог в master: иначе при сбое коммита master уже
            # новее, а project.db откатился, и платный шаг мог бы выполниться второй раз (аудит W56 K6).
            from app.db import commit_with_retry

            await commit_with_retry(session)
            try:
                from app.project_db import push_runtime_to_master

                await push_runtime_to_master(session, project)
            except Exception as exc:  # noqa: BLE001
                logger.warning("advance_project_job: sync runtime to master failed: {}", exc)
            if new != prev:
                logger.debug(
                    "advance_project_job: #{} {} -> {}", project_id, prev, new
                )
                return AdvanceJobResult(project_id, prev, new)
            return AdvanceJobResult(project_id, prev, None)
    except asyncio.CancelledError:
        logger.info("advance_project_job: #{} hard-cancelled (⏹)", project_id)
        try:
            async with project_db_session_scope(project_id) as session:
                project = await session.get(Project, project_id)
                if project is not None:
                    await session.refresh(project)
        except Exception:  # noqa: BLE001
            logger.warning("advance_project_job: refresh #{} after cancel failed", project_id)
        raise
    finally:
        try:
            corr_scope.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        try:
            _cost_project.reset(cost_token)
        except Exception:  # noqa: BLE001
            pass
        # Recovery tracking spans the entire original job, including project DB
        # commit. pipeline.advance_project unregisters before that commit.
        from app.services.actions_recovery import finish_job_tracking
        finish_job_tracking(project_id)
