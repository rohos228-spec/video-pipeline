"""Очередь генерации проектов по сайдбару (top-N окно).

При WORKER_MAX_PARALLEL=1 — строго по одному (как раньше).
При N>1 — одновременно выполняются до N runnable слотов.
paused/user_stop пропускаются (не занимают окно) — хвост может работать,
если есть свободные слоты параллели.
"""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Project, ProjectStatus
from app.orchestrator.auto_advance import TRANSITIONS
from app.orchestrator.graph.planner import load_graph_for_project
from app.services.mass_factory import is_mass_factory_child
from app.services.gen_queue_run import (
    gen_queue_slot_skipped,
    is_gen_queue_run_complete,
    is_gen_queue_timeline_complete,
    mark_gen_queue_run_complete,
    skip_gen_queue_slot,
)
from app.services.sidebar_layout import get_gen_queue, is_gen_queue_halted
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
    ProjectStatus.sfx_planning,
    ProjectStatus.generating_sfx,
    ProjectStatus.assembling,
    ProjectStatus.publishing,
]


def _slot_blocked(project: Project) -> bool:
    return bool(
        project.status is ProjectStatus.paused
        or _user_stop_blocks_queue(project)
    )


def _slot_closed(project: Project) -> bool:
    return is_gen_queue_run_complete(project) or gen_queue_slot_skipped(project) is not None


def worker_max_parallel() -> int:
    """Сколько проектов очереди могут выполняться одновременно (1..4).

    Источник: data/runtime_streams.json → env WORKER_MAX_PARALLEL → 1.
    """
    from app.services.runtime_streams import get_worker_max_parallel

    return get_worker_max_parallel()


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
    """user_stop блокирует очередь (ручной ▶ снимет gate через start_step)."""
    meta = project.meta if isinstance(project.meta, dict) else {}
    return bool(meta.get("user_stop") or meta.get("mass_lane_user_stop"))


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
    """Продвинуть *_ready. new — только ручной ▶ (не автостарт).

    Возвращает 1 если что-то стартовало/продвинулось.
    """
    from app.services.project_control import auto_awaits_manual_start

    if project.status is ProjectStatus.new:
        # Автопродвижение ≠ автостарт: первый шаг только руками.
        logger.info(
            "gen_queue: #{} new — ждём ручной ▶ (queue position {})",
            project.id,
            queue_pos,
        )
        return 0
    if auto_awaits_manual_start(project):
        logger.info(
            "gen_queue: #{} {} — auto_mode ждёт ручной ▶ (queue position {})",
            project.id,
            project.status.value,
            queue_pos,
        )
        return 0
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
    if project.status is ProjectStatus.new:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "manual_start",
            "detail": "Ожидает ручного ▶ (автостарт отключён)",
        }
    from app.services.project_control import auto_awaits_manual_start

    if auto_awaits_manual_start(project):
        return {
            "project_id": project.id,
            "position": position,
            "reason": "manual_start",
            "detail": "Автопродвижение включено — ждёт ручного ▶",
        }
    if not project.auto_mode:
        return {
            "project_id": project.id,
            "position": position,
            "reason": "auto_mode",
            "detail": "Выключен режим ИИ (auto_mode)",
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
    if await gen_queue_busy_count(session) > 0:
        return None

    for idx, pid in enumerate(queue):
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if await _close_slot_if_already_at_target(session, project, queue_pos=idx + 1):
            continue
        if _slot_blocked(project):
            # paused/⏹ не считаем простоем очереди — хвост может работать.
            continue
        if project.status is ProjectStatus.failed:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new and not project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status is ProjectStatus.new:
            # auto_mode включён, но первый старт только руками
            return _idle_reason_for_project(project, position=idx + 1)
        if project.status in TRANSITIONS and project.auto_mode:
            return _idle_reason_for_project(project, position=idx + 1)
        return _idle_reason_for_project(project, position=idx + 1)
    return None


async def gen_queue_head_project(session: AsyncSession) -> Project | None:
    """Первый незакрытый слот очереди (может быть paused/user_stop)."""
    window = await gen_queue_window_projects(session)
    return window[0] if window else None


async def gen_queue_window_projects(session: AsyncSession) -> list[Project]:
    """Первые N runnable слотов очереди (top-N окно).

    paused/user_stop не входят в окно — освобождают параллель для следующих.
    """
    queue = get_gen_queue()
    n = worker_max_parallel()
    out: list[Project] = []
    for pid in queue:
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            continue
        out.append(project)
        if len(out) >= n:
            break
    return out


async def _rollback_running(
    session: AsyncSession,
    project: Project,
    *,
    reason: str,
) -> bool:
    from app.services.project_control import rollback_running_for_queue

    return await rollback_running_for_queue(session, project, reason=reason)


async def gen_queue_reconcile(session: AsyncSession) -> int:
    """Сбросить running вне top-N окна; внутри окна — rollback blocked слотов."""
    queue = get_gen_queue()
    if not queue:
        return 0

    window = await gen_queue_window_projects(session)
    window_ids = {p.id for p in window}
    rolled = 0

    for project in window:
        if _slot_blocked(project) and project.status in GEN_QUEUE_BUSY_STATUSES:
            if await _rollback_running(session, project, reason="window slot blocked"):
                rolled += 1

    for pid in queue:
        if pid in window_ids:
            continue
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if project.status not in GEN_QUEUE_BUSY_STATUSES:
            continue
        if await _rollback_running(session, project, reason="out-of-window"):
            rolled += 1

    return rolled


async def gen_queue_busy_project(session: AsyncSession) -> int | None:
    """ID первого running-проекта в top-N окне (или None)."""
    busy_ids = await gen_queue_busy_projects(session)
    return busy_ids[0] if busy_ids else None


async def gen_queue_busy_projects(session: AsyncSession) -> list[int]:
    """ID running-проектов внутри top-N окна (после reconcile)."""
    await gen_queue_reconcile(session)
    out: list[int] = []
    for project in await gen_queue_window_projects(session):
        if _slot_blocked(project):
            continue
        if project.status in GEN_QUEUE_BUSY_STATUSES:
            out.append(project.id)
    return out


async def gen_queue_busy_count(session: AsyncSession) -> int:
    """Сколько проектов очереди сейчас в running внутри окна."""
    return len(await gen_queue_busy_projects(session))


async def gen_queue_incomplete_earlier(
    session: AsyncSession, project_id: int
) -> int | None:
    """Блокирующий более ранний слот, если проект вне top-N окна.

    paused/user_stop пропускаются. Блокирует N-й runnable предшественник
    (окно параллели заполнено).
    """
    queue = get_gen_queue()
    if not queue or project_id not in queue:
        return None
    n = worker_max_parallel()
    pos = queue.index(project_id)
    holding: list[int] = []
    for pid in queue[:pos]:
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            logger.debug(
                "gen_queue: #{} пропуск (paused/user_stop) — не блокирует хвост",
                project.id,
            )
            continue
        holding.append(project.id)
        if len(holding) >= n:
            return holding[n - 1]
    return None


async def gen_queue_blocks_project(session: AsyncSession, project_id: int) -> int | None:
    """Если проект в очереди — ID блокирующего предшественника или None."""
    return await gen_queue_incomplete_earlier(session, project_id)


async def gen_queue_tick(session: AsyncSession) -> int:
    """Заполнить top-N окно: стартовать/продвинуть до N проектов."""
    queue = get_gen_queue()
    if not queue:
        return 0
    if is_gen_queue_halted():
        logger.info("gen_queue tick: halted после ⏹ — следующий не стартует")
        return 0

    rolled = await gen_queue_reconcile(session)
    if rolled:
        await session.flush()

    n = worker_max_parallel()
    logger.debug("gen_queue tick: порядок {} (max_parallel={})", queue, n)

    started_total = 0
    window_filled = 0

    for idx, pid in enumerate(queue):
        if window_filled >= n:
            break
        project = await _load_project(session, pid)
        if project is None or is_mass_factory_child(project):
            continue
        if _slot_closed(project):
            continue
        if _slot_blocked(project):
            logger.info(
                "gen_queue: #{} {} — пропуск слота, ищем свободный (позиция {})",
                project.id,
                "user_stop" if _user_stop_blocks_queue(project) else "paused",
                idx + 1,
            )
            continue
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

        if project.status in GEN_QUEUE_BUSY_STATUSES:
            window_filled += 1
            continue

        started = await _start_or_advance_project(
            session, project, queue_pos=idx + 1
        )
        if started:
            started_total += started
            window_filled += 1
            continue

        # Слот занимает окно (new / ждёт ▶ / без auto_mode), но не running.
        # При N=1 — как раньше: стоп на этом слоте. При N>1 — пробуем
        # следующие слоты окна.
        window_filled += 1
        if n == 1:
            logger.info(
                "gen_queue: ждём #{} (позиция {}, status={})",
                project.id,
                idx + 1,
                project.status.value,
            )
            return started_total
        logger.info(
            "gen_queue: #{} занимает окно (status={}), слот {}/{}",
            project.id,
            project.status.value,
            window_filled,
            n,
        )
    return started_total


async def on_project_timeline_maybe_advance_queue(
    session: AsyncSession, project: Project
) -> int:
    """После завершения шага: закрыть слот и дозаполнить top-N окно."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return 0
    if is_gen_queue_halted():
        logger.info(
            "gen_queue: #{} timeline complete — очередь halted после ⏹",
            project.id,
        )
        return 0
    if is_mass_factory_child(project):
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
    # Дозаполняем окно (при N=1 — следующий слот; при N>1 — несколько).
    return await gen_queue_tick(session)


async def assert_can_start_in_queue(session: AsyncSession, project: Project) -> None:
    """Ручной start_step: проект должен быть внутри top-N окна."""
    queue = get_gen_queue()
    if not queue or project.id not in queue:
        return
    blocker = await gen_queue_blocks_project(session, project.id)
    if blocker is not None:
        raise ValueError(
            f"Очередь: сначала завершите #{blocker}, затем #{project.id}"
        )
