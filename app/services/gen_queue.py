"""Последовательная генерация проектов по очереди сайдбара (1→2→3…)."""

from __future__ import annotations

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.auto_advance import TRANSITIONS
from app.orchestrator.graph.planner import graph_executor_enabled, load_graph_for_project
from app.services.mass_factory import mass_parent_id
from app.services.project_steps import start_step
from app.services.gen_queue_run import (
    is_gen_queue_run_complete,
    is_gen_queue_timeline_complete,
    mark_gen_queue_run_complete,
)
from app.services.sidebar_layout import get_gen_queue
from app.telegram.menu import step_by_code

GEN_QUEUE_BUSY_STATUSES = [
    ProjectStatus.planning,
    ProjectStatus.scripting,
    ProjectStatus.splitting,
    ProjectStatus.generating_hero,
    ProjectStatus.generating_items,
    ProjectStatus.enriching_1,
    ProjectStatus.enriching_2,
    ProjectStatus.enriching_3,
    ProjectStatus.enriching_4,
    ProjectStatus.enriching_5,
    ProjectStatus.generating_image_prompts,
    ProjectStatus.generating_images,
    ProjectStatus.generating_animation_prompts,
    ProjectStatus.generating_videos,
    ProjectStatus.generating_music,
    ProjectStatus.generating_audio,
    ProjectStatus.assembling,
    ProjectStatus.publishing,
]


async def is_timeline_complete(session: AsyncSession, project: Project) -> bool:
    """True если последний шаг таймлайна завершён и следующего нет."""
    if is_gen_queue_timeline_complete(project):
        return True
    status = project.status
    if status is ProjectStatus.published:
        return True
    if status in (ProjectStatus.failed, ProjectStatus.paused, ProjectStatus.new):
        return False
    if status in GEN_QUEUE_BUSY_STATUSES:
        return False

    if graph_executor_enabled(project):
        graph = await load_graph_for_project(session, project)
        if status in TRANSITIONS:
            return graph.next_running_after_ready(project, status) is None
        if status is ProjectStatus.assembled:
            return graph.next_running_after_ready(project, ProjectStatus.assembled) is None
        return False

    if status in TRANSITIONS:
        from app.orchestrator.auto_advance import _next_running_with_enrich_cap
        from app.services.disabled_nodes import skip_disabled_running_async

        tr = TRANSITIONS[status]
        nxt = _next_running_with_enrich_cap(project, tr)
        if nxt is None:
            return True
        skipped = await skip_disabled_running_async(session, project, nxt)
        return skipped is None

    if status is ProjectStatus.assembled:
        tr = TRANSITIONS.get(ProjectStatus.assembled)
        if tr is None or tr.next_running is None:
            return True
        from app.services.disabled_nodes import skip_disabled_running_async

        skipped = await skip_disabled_running_async(session, project, tr.next_running)
        return skipped is None
    return False


async def _load_project(session: AsyncSession, project_id: int) -> Project | None:
    return (
        await session.execute(select(Project).where(Project.id == project_id))
    ).scalar_one_or_none()


async def _close_slot_if_already_at_target(
    session: AsyncSession, project: Project, *, queue_pos: int
) -> bool:
    """Закрыть слот очереди, если цель прогона уже достигнута."""
    if is_gen_queue_run_complete(project):
        return False
    if not await is_timeline_complete(session, project):
        return False
    await mark_gen_queue_run_complete(session, project)
    await session.flush()
    logger.info(
        "gen_queue: #{} уже на цели — слот {} закрыт",
        project.id,
        queue_pos,
    )
    return True


async def gen_queue_busy_project(session: AsyncSession) -> int | None:
    queue = get_gen_queue()
    if not queue:
        return None
    for pid in queue:
        p = await _load_project(session, pid)
        if p is None or mass_parent_id(p) is not None:
            continue
        if p.status in GEN_QUEUE_BUSY_STATUSES:
            return p.id
    return None


async def gen_queue_incomplete_earlier(
    session: AsyncSession, project_id: int
) -> int | None:
    """Первый более ранний проект в очереди, чей прогон ещё не завершён."""
    queue = get_gen_queue()
    if not queue or project_id not in queue:
        return None
    pos = queue.index(project_id)
    for pid in queue[:pos]:
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        meta = project.meta if isinstance(project.meta, dict) else {}
        if meta.get("user_stop") or meta.get("mass_lane_user_stop"):
            logger.debug(
                "gen_queue: #{} блокирует очередь (user_stop)",
                project.id,
            )
            return project.id
        if project.status is ProjectStatus.paused:
            logger.debug(
                "gen_queue: #{} блокирует очередь (paused)",
                project.id,
            )
            return project.id
        if not is_gen_queue_run_complete(project):
            return project.id
    return None


async def gen_queue_blocks_project(session: AsyncSession, project_id: int) -> int | None:
    """Если проект в очереди — ID блокирующего предшественника или None."""
    return await gen_queue_incomplete_earlier(session, project_id)


async def gen_queue_tick(session: AsyncSession) -> int:
    """Запустить следующий проект в очереди, если текущий завершил таймлайн."""
    queue = get_gen_queue()
    if not queue:
        return 0

    logger.debug("gen_queue tick: порядок {}", queue)

    if await gen_queue_busy_project(session) is not None:
        return 0

    for idx, pid in enumerate(queue):
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        meta = project.meta if isinstance(project.meta, dict) else {}
        if meta.get("user_stop") or meta.get("mass_lane_user_stop"):
            logger.info(
                "gen_queue: ждём #{} (позиция {}, user_stop)",
                project.id,
                idx + 1,
            )
            return 0
        if project.status is ProjectStatus.paused:
            logger.info(
                "gen_queue: ждём #{} (позиция {}, paused)",
                project.id,
                idx + 1,
            )
            return 0
        if is_gen_queue_run_complete(project):
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue
        if project.status is ProjectStatus.new:
            if not project.auto_mode:
                logger.info(
                    "gen_queue: #{} ждёт auto_mode (позиция {})",
                    project.id,
                    idx + 1,
                )
                return 0
            await start_step(session, project, "plan")
            await session.flush()
            logger.info(
                "gen_queue: started #{} (queue position {})",
                project.id,
                idx + 1,
            )
            return 1
        logger.info(
            "gen_queue: ждём #{} (позиция {}, status={})",
            project.id,
            idx + 1,
            project.status.value,
        )
        return 0
    return 0


async def on_project_timeline_maybe_advance_queue(
    session: AsyncSession, project: Project
) -> int:
    """После завершения шага: старт только следующего слота (pos+1), без перескоков."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return 0
    if mass_parent_id(project) is not None:
        return 0
    if not await is_timeline_complete(session, project):
        return 0
    if not is_gen_queue_run_complete(project):
        await mark_gen_queue_run_complete(session, project)
        await session.flush()
        logger.info(
            "gen_queue: #{} timeline complete — слот {} закрыт",
            project.id,
            queue.index(project.id) + 1,
        )
    pos = queue.index(project.id)
    if pos + 1 >= len(queue):
        return 0
    if await gen_queue_busy_project(session) is not None:
        return 0

    next_id = queue[pos + 1]
    nxt = await _load_project(session, next_id)
    if nxt is None or mass_parent_id(nxt) is not None:
        return 0
    meta = nxt.meta if isinstance(nxt.meta, dict) else {}
    if meta.get("user_stop") or meta.get("mass_lane_user_stop"):
        logger.info(
            "gen_queue: #{} done → ждём #{} (user_stop)",
            project.id,
            nxt.id,
        )
        return 0
    if nxt.status is ProjectStatus.paused:
        logger.info(
            "gen_queue: #{} done → ждём #{} (paused)",
            project.id,
            nxt.id,
        )
        return 0
    if is_gen_queue_run_complete(nxt):
        return 0
    if await _close_slot_if_already_at_target(
        session, nxt, queue_pos=pos + 2
    ):
        return 0
    if nxt.status is not ProjectStatus.new:
        logger.info(
            "gen_queue: #{} done → ждём #{} (status={})",
            project.id,
            nxt.id,
            nxt.status.value,
        )
        return 0
    if not nxt.auto_mode:
        logger.info(
            "gen_queue: #{} done → #{} ждёт auto_mode",
            project.id,
            nxt.id,
        )
        return 0
    step = step_by_code("plan")
    if step is None:
        return 0
    await start_step(session, nxt, "plan")
    await session.flush()
    logger.info(
        "gen_queue: #{} done → started #{} (queue position {})",
        project.id,
        nxt.id,
        pos + 2,
    )
    return 1
