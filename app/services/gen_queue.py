"""Последовательная генерация проектов по очереди сайдбара (1→2→3…)."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.auto_advance import TRANSITIONS
from app.orchestrator.graph.planner import load_graph_for_project
from app.services.mass_factory import mass_parent_id
from app.services.project_steps import start_step
from app.services.gen_queue_run import (
    gen_queue_slot_skipped,
    is_gen_queue_run_complete,
    is_gen_queue_timeline_complete,
    mark_gen_queue_run_complete,
    skip_gen_queue_slot,
)
from app.services.sidebar_layout import get_gen_queue
from app.telegram.menu import step_by_code, step_by_running_status

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


def _slot_blocked(project: Project) -> bool:
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(
        project.status is ProjectStatus.paused
        or _user_stop_blocks_queue(project)
    )


def _slot_closed(project: Project) -> bool:
    return is_gen_queue_run_complete(project) or gen_queue_slot_skipped(project) is not None


def gen_queue_is_active() -> bool:
    return bool(get_gen_queue())


def project_gated_by_gen_queue(project_id: int) -> bool:
    """Очередь непуста, а проект не в ней — пайплайн не трогаем (только ручной ▶)."""
    queue = get_gen_queue()
    return bool(queue) and project_id not in queue


def enrich_ready_bypasses_gen_queue(project: Project) -> bool:
    """enrich_N_ready + на канвасе есть следующий незавершённый excel_gpt.

    Ручной ▶ одной ноды обходит очередь, но auto_advance после ready
    снова упирался в gen_queue — слот 3 не стартовал. Цепочку excel_gpt
    пропускаем через gate.
    """
    from app.services.excel_gpt_node import (
        next_incomplete_excel_gpt_slot,
        slot_from_ready_status,
    )

    slot = slot_from_ready_status(project.status)
    if slot is None:
        return False
    return next_incomplete_excel_gpt_slot(project, slot) is not None


async def on_project_removed_from_gen_queue(
    session: AsyncSession,
    project: Project,
) -> None:
    """Снятие номера очереди: стоп running + user_stop (иначе script_ready → auto_advance)."""
    from app.services.project_control import _set_user_stop_gate, stop_project_running
    from app.services.step_cancel import request_stop

    if project.status in GEN_QUEUE_BUSY_STATUSES:
        request_stop(project.id)
        await stop_project_running(session, project)
        logger.info(
            "[#{}] gen_queue: снят с очереди — остановлен {}",
            project.id,
            project.status.value,
        )
        return

    _set_user_stop_gate(project)
    await session.flush()
    logger.info(
        "[#{}] gen_queue: снят с очереди — user_stop ({}), без auto_advance",
        project.id,
        project.status.value,
    )


def _user_stop_blocks_queue(project: Project) -> bool:
    """user_stop блокирует очередь, кроме new+auto_mode — там start_step сам снимет gate."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    if not (meta.get("user_stop") or meta.get("mass_lane_user_stop")):
        return False
    if project.status is ProjectStatus.new and project.auto_mode:
        return False
    return True


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

    graph = await load_graph_for_project(session, project)
    if status in TRANSITIONS:
        return graph.next_running_after_ready(project, status) is None
    if status is ProjectStatus.assembled:
        return graph.next_running_after_ready(project, ProjectStatus.assembled) is None
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


async def _advance_ready_project(session: AsyncSession, project: Project) -> bool:
    """Продвинуть *_ready проект в очереди на следующий шаг."""
    from app.orchestrator.auto_advance import maybe_auto_advance

    if project.status not in TRANSITIONS:
        return False
    if not project.auto_mode:
        return False
    return await maybe_auto_advance(session, project, bot=None, force=True)


async def _start_or_advance_project(
    session: AsyncSession, project: Project, *, queue_pos: int
) -> int:
    """Запустить new или продвинуть *_ready. Возвращает 1 если что-то стартовало."""
    if project.status is ProjectStatus.new:
        if not project.auto_mode:
            return 0
        await start_step(session, project, "plan", skip_queue_guard=True)
        await session.flush()
        logger.info(
            "gen_queue: started #{} (queue position {})",
            project.id,
            queue_pos,
        )
        return 1
    if project.status in TRANSITIONS:
        if not project.auto_mode:
            return 0
        advanced = await _advance_ready_project(session, project)
        if advanced:
            logger.info(
                "gen_queue: advanced #{} from {} (queue position {})",
                project.id,
                project.status.value,
                queue_pos,
            )
            return 1
    return 0


def _idle_reason_for_project(project: Project, *, position: int) -> dict[str, Any]:
    if _user_stop_blocks_queue(project):
        return {
            "project_id": project.id,
            "position": position,
            "reason": "user_stop",
            "detail": "Остановлен пользователем (⏹)",
        }
    if project.status is ProjectStatus.paused:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "paused",
            "detail": "Проект на паузе",
        }
    if project.status is ProjectStatus.failed:
        fs = (project.meta or {}).get("step_failure") or {}
        err = str(fs.get("last_error") or "ошибка шага")[:120]
        return {
            "project_id": project.id,
            "position": position,
            "reason": "failed",
            "detail": err,
        }
    if not project.auto_mode:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "auto_mode",
            "detail": "Выключен режим ИИ (auto_mode)",
        }
    if project.status is ProjectStatus.new:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "waiting",
            "detail": "Ожидает запуска",
        }
    return {
        "project_id": project.id,
        "position": position,
        "reason": "status",
        "detail": f"Статус: {project.status.value}",
    }


async def get_gen_queue_idle_info(session: AsyncSession) -> dict[str, Any] | None:
    """Почему очередь стоит (для API/UI). None — очередь пуста или идёт работа."""
    queue = get_gen_queue()
    if not queue:
        return None
    if await gen_queue_busy_project(session) is not None:
        return None

    for idx, pid in enumerate(queue):
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        if _slot_closed(project):
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue
        if project.status is ProjectStatus.failed:
            return _idle_reason_for_project(project, position=idx + 1)
        if _user_stop_blocks_queue(project):
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.paused:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new and not project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status in TRANSITIONS and project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new and project.auto_mode:
            return None
        return _idle_reason_for_project(project, position=idx + 1)
    return None


async def gen_queue_head_project(session: AsyncSession) -> Project | None:
    """Первый незакрытый слот очереди (может быть paused/user_stop)."""
    queue = get_gen_queue()
    for pid in queue:
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        if _slot_closed(project):
            continue
        return project
    return None


async def _rollback_running(
    session: AsyncSession,
    project: Project,
    *,
    reason: str,
) -> bool:
    from app.services.project_control import rollback_running_for_queue

    return await rollback_running_for_queue(session, project, reason=reason)


async def gen_queue_reconcile(session: AsyncSession) -> int:
    """Сбросить «внеочередные» running внутри очереди — только head может выполняться."""
    queue = get_gen_queue()
    if not queue:
        return 0

    head = await gen_queue_head_project(session)
    head_id = head.id if head is not None else None
    head_blocked = head is not None and _slot_blocked(head)
    rolled = 0

    if head is not None and head_blocked and head.status in GEN_QUEUE_BUSY_STATUSES:
        if await _rollback_running(session, head, reason="head blocked"):
            rolled += 1

    for pid in queue:
        if pid == head_id:
            continue
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        if project.status not in GEN_QUEUE_BUSY_STATUSES:
            continue
        if await _rollback_running(session, project, reason="out-of-turn"):
            rolled += 1

    return rolled


async def gen_queue_busy_project(session: AsyncSession) -> int | None:
    """ID единственного разрешённого running-проекта в очереди (только head)."""
    await gen_queue_reconcile(session)
    head = await gen_queue_head_project(session)
    if head is None or _slot_blocked(head):
        return None
    if head.status in GEN_QUEUE_BUSY_STATUSES:
        return head.id
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
        if _slot_closed(project):
            continue
        if _user_stop_blocks_queue(project):
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

    rolled = await gen_queue_reconcile(session)
    if rolled:
        await session.flush()

    logger.debug("gen_queue tick: порядок {}", queue)

    if await gen_queue_busy_project(session) is not None:
        return 0

    for idx, pid in enumerate(queue):
        project = await _load_project(session, pid)
        if project is None or mass_parent_id(project) is not None:
            continue
        if _slot_closed(project):
            continue
        if _user_stop_blocks_queue(project):
            logger.info(
                "gen_queue: ждём #{} (позиция {}, user_stop)",
                project.id,
                idx + 1,
            )
            return 0
        if project.status is ProjectStatus.paused:
            logger.info(
                "gen_queue: #{} paused — очередь стоит (позиция {})",
                project.id,
                idx + 1,
            )
            return 0
        if project.status is ProjectStatus.failed:
            fs = (project.meta or {}).get("step_failure") or {}
            err = str(fs.get("last_error") or "ошибка шага")[:120]
            await skip_gen_queue_slot(
                session,
                project,
                reason="failed",
                detail=err,
            )
            logger.warning(
                "gen_queue: #{} пропущен (ошибка, позиция {}): {}",
                project.id,
                idx + 1,
                err,
            )
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue
        started = await _start_or_advance_project(
            session, project, queue_pos=idx + 1
        )
        if started:
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

    await gen_queue_reconcile(session)
    if await gen_queue_busy_project(session) is not None:
        return 0

    next_id = queue[pos + 1]
    nxt = await _load_project(session, next_id)
    if nxt is None or mass_parent_id(nxt) is not None:
        return 0
    if _slot_closed(nxt):
        return 0
    if _user_stop_blocks_queue(nxt):
        logger.info(
            "gen_queue: #{} done → ждём #{} (user_stop)",
            project.id,
            nxt.id,
        )
        return 0
    if nxt.status is ProjectStatus.paused:
        logger.info(
            "gen_queue: #{} done → очередь стоит на #{} (paused)",
            project.id,
            nxt.id,
        )
        return 0
    if nxt.status is ProjectStatus.failed:
        fs = (nxt.meta or {}).get("step_failure") or {}
        err = str(fs.get("last_error") or "ошибка шага")[:120]
        await skip_gen_queue_slot(session, nxt, reason="failed", detail=err)
        logger.warning(
            "gen_queue: #{} done → #{} пропущен (ошибка): {}",
            project.id,
            nxt.id,
            err,
        )
        return 0
    if await _close_slot_if_already_at_target(
        session, nxt, queue_pos=pos + 2
    ):
        return 0
    started = await _start_or_advance_project(session, nxt, queue_pos=pos + 2)
    if started:
        logger.info(
            "gen_queue: #{} done → started/advanced #{} (queue position {})",
            project.id,
            nxt.id,
            pos + 2,
        )
        return 1
    if not nxt.auto_mode:
        logger.info(
            "gen_queue: #{} done → #{} ждёт auto_mode",
            project.id,
            nxt.id,
        )
        return 0
    logger.info(
        "gen_queue: #{} done → ждём #{} (status={})",
        project.id,
        nxt.id,
        nxt.status.value,
    )
    return 0


async def assert_can_start_in_queue(session: AsyncSession, project: Project) -> None:
    """Ручной start_step: только head слота очереди."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return
    head = await gen_queue_head_project(session)
    if head is None or head.id == project.id:
        return
    blocker = await gen_queue_blocks_project(session, project.id)
    if blocker is not None:
        raise ValueError(
            f"Очередь: сначала завершите #{blocker}, затем #{project.id}"
        )
