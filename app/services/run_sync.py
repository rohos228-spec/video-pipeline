"""Синхронизатор: ProjectStatus → NodeRun.status (для веб-UI).

Текущий воркер (`app/worker.py` + `app/orchestrator/pipeline.py`) работает на
ProjectStatus. Веб-UI хочет видеть прогресс в терминах NodeRun (status,
progress, started_at, finished_at).

Этот модуль:
  - на старте создаёт `WorkflowRun` для каждого Project, у которого ещё нет;
  - в фоне обновляет статусы NodeRun по текущему ProjectStatus;
  - публикует события в EventBus, чтобы веб-фронтенд получал live-обновления.

После Phase 2 (полноценный NODE_REGISTRY) этот мост можно будет упростить
или удалить, но он позволяет показать работающий веб-UI на текущем коде.
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import session_scope
from app.models import (
    NodeRun,
    NodeRunStatus,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.event_bus import publish_node_event
from app.services.node_status_machine import (
    apply_sync_target,
    fail_node,
    reset_node_to_pending,
    start_node_running,
)


# Маппинг: ProjectStatus -> (node_type, NodeRunStatus).
# «running»-статусы → нода типа N в состоянии running.
# «ready»-статусы   → нода типа N в состоянии done.
# Все ноды до этой в линейном пайплайне считаются done.
STATUS_TO_NODE: dict[ProjectStatus, tuple[str, NodeRunStatus]] = {
    ProjectStatus.new: ("plan", NodeRunStatus.pending),
    ProjectStatus.planning: ("plan", NodeRunStatus.running),
    ProjectStatus.plan_ready: ("plan", NodeRunStatus.done),
    ProjectStatus.scripting: ("script", NodeRunStatus.running),
    ProjectStatus.script_ready: ("script", NodeRunStatus.done),
    ProjectStatus.splitting: ("split", NodeRunStatus.running),
    ProjectStatus.frames_ready: ("split", NodeRunStatus.done),
    ProjectStatus.generating_hero: ("hero", NodeRunStatus.running),
    ProjectStatus.hero_ready: ("hero", NodeRunStatus.done),
    ProjectStatus.generating_items: ("items", NodeRunStatus.running),
    ProjectStatus.items_ready: ("items", NodeRunStatus.done),
    ProjectStatus.enriching_1: ("enrich_1", NodeRunStatus.running),
    ProjectStatus.enrich_1_ready: ("enrich_1", NodeRunStatus.done),
    ProjectStatus.enriching_2: ("enrich_2", NodeRunStatus.running),
    ProjectStatus.enrich_2_ready: ("enrich_2", NodeRunStatus.done),
    ProjectStatus.enriching_3: ("enrich_3", NodeRunStatus.running),
    ProjectStatus.enrich_3_ready: ("enrich_3", NodeRunStatus.done),
    ProjectStatus.enriching_4: ("enrich_4", NodeRunStatus.running),
    ProjectStatus.enrich_4_ready: ("enrich_4", NodeRunStatus.done),
    ProjectStatus.enriching_5: ("enrich_5", NodeRunStatus.running),
    ProjectStatus.enrich_5_ready: ("enrich_5", NodeRunStatus.done),
    ProjectStatus.generating_image_prompts: ("image_prompts", NodeRunStatus.running),
    ProjectStatus.image_prompts_ready: ("image_prompts", NodeRunStatus.done),
    ProjectStatus.generating_images: ("images", NodeRunStatus.running),
    ProjectStatus.images_ready: ("images", NodeRunStatus.done),
    ProjectStatus.generating_animation_prompts: ("animation_prompts", NodeRunStatus.running),
    ProjectStatus.animation_prompts_ready: ("animation_prompts", NodeRunStatus.done),
    ProjectStatus.generating_videos: ("videos", NodeRunStatus.running),
    ProjectStatus.videos_ready: ("videos", NodeRunStatus.done),
    ProjectStatus.generating_audio: ("audio", NodeRunStatus.running),
    ProjectStatus.audio_ready: ("audio", NodeRunStatus.done),
    ProjectStatus.generating_music: ("music", NodeRunStatus.running),
    ProjectStatus.music_ready: ("music", NodeRunStatus.done),
    ProjectStatus.assembling: ("assemble", NodeRunStatus.running),
    ProjectStatus.assembled: ("assemble", NodeRunStatus.done),
    ProjectStatus.publishing: ("publish", NodeRunStatus.running),
    ProjectStatus.published: ("publish", NodeRunStatus.done),
}

# Линейный порядок типов нод (для определения «всё до этой = done»).
NODE_TYPE_ORDER: list[str] = [
    "topic",
    "plan",
    "script",
    "split",
    "hero",
    "items",
    "enrich_1",
    "enrich_2",
    "enrich_3",
    "enrich_4",
    "enrich_5",
    "image_prompts",
    "images",
    "animation_prompts",
    "videos",
    "audio",
    "music",
    "assemble",
    "publish",
]


async def _get_default_workflow_id() -> int | None:
    async with session_scope() as s:
        wf = (
            await s.execute(
                select(Workflow).where(Workflow.is_default == True)  # noqa: E712
            )
        ).scalar_one_or_none()
        return wf.id if wf is not None else None


async def ensure_run_for_project(project_id: int, workflow_id: int) -> int:
    """Гарантирует, что у проекта есть WorkflowRun. Возвращает его id."""
    async with session_scope() as s:
        existing = (
            await s.execute(
                select(WorkflowRun).where(WorkflowRun.project_id == project_id)
            )
        ).scalar_one_or_none()
        if existing is not None:
            return existing.id
        wf = await s.get(Workflow, workflow_id)
        if wf is None:
            raise ValueError(f"workflow {workflow_id} not found")
        project = await s.get(Project, project_id)
        nodes = list(wf.nodes or [])
        edges = list(wf.edges or [])
        if project is not None:
            from app.services.canvas_graph import canvas_graph_from_meta

            cg = canvas_graph_from_meta(
                project.meta if isinstance(project.meta, dict) else {}
            )
            if cg:
                nodes = list(cg["nodes"])
                edges = list(cg["edges"])
        run = WorkflowRun(
            workflow_id=wf.id,
            project_id=project_id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=nodes,
            edges_snapshot=edges,
        )
        s.add(run)
        await s.flush()
        for node in run.nodes_snapshot:
            nr = NodeRun(
                workflow_run_id=run.id,
                node_key=node["id"],
                node_type=node["type"],
            )
            s.add(nr)
        await s.flush()
        return run.id


from app.orchestrator.graph.planner import load_graph_for_project
from app.orchestrator.node_registry import (
    LINEAR_NODE_TYPES,
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
)
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    ready_status_for_slot,
    running_status_for_slot,
    slot_index_from_node,
)
from app.services.project_state import compute_actual_status, is_running_status
from app.services.disabled_nodes import disabled_node_types


def _derived_node_states(
    current_status: ProjectStatus,
    disabled_types: set[str] | None = None,
) -> dict[str, NodeRunStatus]:
    """Из ProjectStatus вычислить ожидаемый статус для каждого `node_type`.

    Логика: всё до текущего шага — done; текущий — running либо done в
    зависимости от того, *_ready он или *ing; всё после — pending.
    HITL-gate ноды (типа `hitl_*`) пока остаются pending — их синхронизация
    придёт через event-bus, когда воркер реально создаст HITLRequest.
    """
    disabled_types = disabled_types or set()
    if current_status is ProjectStatus.new:
        return {
            typ: NodeRunStatus.skipped if typ in disabled_types else NodeRunStatus.pending
            for typ in NODE_TYPE_ORDER
        }
    if current_status not in STATUS_TO_NODE:
        return {}
    target_type, target_state = STATUS_TO_NODE[current_status]
    out: dict[str, NodeRunStatus] = {}
    target_reached = False
    for typ in NODE_TYPE_ORDER:
        if typ in disabled_types:
            out[typ] = NodeRunStatus.skipped
            continue
        if typ == target_type:
            out[typ] = target_state
            target_reached = True
            continue
        if not target_reached:
            out[typ] = NodeRunStatus.done
        else:
            out[typ] = NodeRunStatus.pending
    return out


def _infer_stale_running_node_status(
    node_type: str,
    project_status: ProjectStatus,
) -> NodeRunStatus | None:
    """NodeRun ещё running, проект уже не *ing.

    done — для завершённых upstream-шагов; pending не выставляем (авто-откат запрещён).
    """
    if project_status not in STATUS_TO_NODE:
        return None
    target_type, target_state = STATUS_TO_NODE[project_status]
    try:
        node_idx = NODE_TYPE_ORDER.index(node_type)
        target_idx = NODE_TYPE_ORDER.index(target_type)
    except ValueError:
        return None
    if node_idx < target_idx:
        return NodeRunStatus.done
    if node_idx == target_idx:
        if target_state == NodeRunStatus.done:
            return NodeRunStatus.done
        return None
    return None


def _excel_gpt_status_for_node(
    node: dict,
    current_status: ProjectStatus,
    *,
    disabled: bool,
) -> NodeRunStatus:
    """Статус excel_gpt ноды по slotIndex и Project.status."""
    if disabled:
        return NodeRunStatus.skipped
    slot = slot_index_from_node(node)
    running = running_status_for_slot(slot)
    ready = ready_status_for_slot(slot)
    if current_status == running:
        return NodeRunStatus.running
    if current_status == ready:
        return NodeRunStatus.done
    from app.telegram.menu import status_order as _ord

    if _ord(current_status) > _ord(ready):
        return NodeRunStatus.done
    return NodeRunStatus.pending


async def sync_run_for_project(project_id: int) -> None:
    """Подтянуть NodeRun-статусы из текущего Project.status."""
    async with session_scope() as s:
        project = await s.get(Project, project_id)
        if project is None:
            return
        run = (
            await s.execute(
                select(WorkflowRun)
                .where(WorkflowRun.project_id == project_id)
                .options(selectinload(WorkflowRun.node_runs))
            )
        ).scalar_one_or_none()
        if run is None:
            return

        actual = await compute_actual_status(s, project)
        meta = project.meta if isinstance(project.meta, dict) else {}
        if meta.get("user_stop") or meta.get("mass_lane_user_stop"):
            actual = project.status
        if (
            not is_running_status(project.status)
            and project.status != actual
            and actual is not None
        ):
            from app.telegram.menu import status_order as _ord

            if _ord(actual) < _ord(project.status):
                from app.services.step_data_guard import ready_status_confirmed_by_data

                if not await ready_status_confirmed_by_data(s, project, project.status):
                    logger.warning(
                        "run_sync: #{} status {} would downgrade to {} — пропуск "
                        "(авто-откат project.status запрещён)",
                        project_id,
                        project.status.value,
                        actual.value,
                    )

        derived = _derived_node_states(
            project.status,
            disabled_node_types(project),
        )
        derived_by_key: dict[str, NodeRunStatus] | None = None
        try:
            graph = await load_graph_for_project(s, project)
            derived_by_key = graph.derived_node_states(project)
        except Exception:  # noqa: BLE001
            logger.exception("graph derived states failed for #{}", project_id)
        if not derived:
            return

        snap_by_key = {n["id"]: n for n in (run.nodes_snapshot or []) if "id" in n}
        now = datetime.utcnow()
        any_running = False
        any_pending = False
        any_failed = False
        for nr in run.node_runs:
            if derived_by_key is not None:
                target = derived_by_key.get(nr.node_key)
                if target is None and nr.node_type in derived:
                    target = derived.get(nr.node_type)
            else:
                if nr.node_type.startswith("hitl"):
                    continue
                target = derived.get(nr.node_type)
            if target is None:
                if nr.node_type == EXCEL_GPT_NODE_TYPE:
                    snap = snap_by_key.get(nr.node_key)
                    if snap is not None:
                        target = _excel_gpt_status_for_node(
                            snap,
                            project.status,
                            disabled=nr.node_type in disabled_node_types(project),
                        )
            if target is None:
                if (
                    nr.status == NodeRunStatus.running
                    and not is_running_status(project.status)
                ):
                    target = _infer_stale_running_node_status(nr.node_type, project.status)
                else:
                    continue
            checkpoint_upstream = (
                target == NodeRunStatus.done
                and nr.status == NodeRunStatus.pending
            )
            if nr.status != target:
                old = nr.status
                changed = False
                if target == NodeRunStatus.running and nr.status in (
                    NodeRunStatus.pending,
                    NodeRunStatus.queued,
                    NodeRunStatus.failed,
                ):
                    try:
                        changed = start_node_running(
                            nr, project_id=project_id, initiator="sync"
                        )
                    except ValueError:
                        changed = False
                else:
                    changed = apply_sync_target(
                        nr,
                        target,
                        project_id=project_id,
                        checkpoint_upstream=checkpoint_upstream,
                    )
                if not changed:
                    continue
                # Публикуем только при реальном переходе.
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": old.value,
                        "to": nr.status.value,
                        "project_id": project_id,
                    },
                )
            if nr.status == NodeRunStatus.running:
                any_running = True
            elif nr.status == NodeRunStatus.queued:
                any_running = True
            elif nr.status == NodeRunStatus.pending:
                any_pending = True
            elif nr.status == NodeRunStatus.failed:
                any_failed = True

        # Агрегация: статус Run.
        if any_failed:
            run.status = WorkflowRunStatus.failed
        elif any_running:
            run.status = WorkflowRunStatus.running
            if run.started_at is None:
                run.started_at = now
        elif any_pending:
            # Все ноды до текущего — done, но впереди ещё есть pending →
            # либо ждём действия пользователя, либо closed-ready.
            run.status = WorkflowRunStatus.waiting_hitl
        else:
            run.status = WorkflowRunStatus.done
            if run.finished_at is None:
                run.finished_at = now


async def sync_all_active_projects() -> None:
    """Прогон по всем проектам, у которых статус не `new` и не финальный."""
    default_wf = await _get_default_workflow_id()
    if default_wf is None:
        return
    async with session_scope() as s:
        rows = (
            await s.execute(
                select(Project.id, Project.status).where(
                    Project.status.notin_([ProjectStatus.new, ProjectStatus.paused])
                )
            )
        ).all()
    for pid, _ in rows:
        try:
            await ensure_run_for_project(pid, default_wf)
            await sync_run_for_project(pid)
        except Exception:  # noqa: BLE001
            logger.exception("run_sync failed for project {}", pid)


async def background_sync_loop(*, interval_sec: float = 2.5) -> None:
    """Фоновая задача: каждые N секунд пересинхронизирует все Run.

    Простой пуллер. Когда Phase 2 будет реализована — заменим на
    event-driven подписку.
    """
    while True:
        try:
            await sync_all_active_projects()
        except Exception:  # noqa: BLE001
            logger.exception("background_sync_loop iteration failed")
        await asyncio.sleep(interval_sec)


from sqlalchemy.ext.asyncio import AsyncSession

from app.orchestrator.node_registry import RUNNING_TO_NODE_TYPE, STEP_CODE_TO_NODE_TYPE
from app.services.excel_gpt_node import EXCEL_GPT_NODE_TYPE


async def _workflow_run_with_nodes(
    session: AsyncSession, project_id: int
) -> WorkflowRun | None:
    return (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project_id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()


async def resolve_node_run_for_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
) -> NodeRun | None:
    """Найти NodeRun для шага (по node_key или node_type)."""
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return None
    if node_key:
        for nr in run.node_runs:
            if nr.node_key == node_key:
                return nr
    node_type = STEP_CODE_TO_NODE_TYPE.get(step_code)
    if step_code == "excel_gpt" and node_key:
        for nr in run.node_runs:
            if nr.node_key == node_key:
                return nr
    if node_type is None:
        return None
    matches = [nr for nr in run.node_runs if nr.node_type == node_type]
    if len(matches) == 1:
        return matches[0]
    if node_key:
        for nr in matches:
            if nr.node_key == node_key:
                return nr
    return matches[0] if matches else None


async def prepare_node_for_step_start(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
    strict: bool = False,
) -> bool:
    """pending/failed → queued → running перед записью project.status."""
    from app.services.node_status_machine import queue_node_for_start, start_node_running

    default_wf = await _get_default_workflow_id()
    if default_wf is None:
        if strict:
            raise ValueError("workflow по умолчанию не найден — сохраните workflow")
        logger.debug(
            "[#{}] prepare_node_for_step_start: нет default workflow, FSM пропущен",
            project.id,
        )
        return False
    await ensure_run_for_project(project.id, default_wf)
    nr = await resolve_node_run_for_step(session, project, step_code, node_key=node_key)
    if nr is None:
        if strict:
            raise ValueError(
                f"нода для шага «{step_code}» не найдена в WorkflowRun — создайте Run на канвасе"
            )
        logger.debug(
            "[#{}] prepare_node_for_step_start: NodeRun для {} не найден",
            project.id,
            step_code,
        )
        return False
    if nr.status == NodeRunStatus.skipped:
        msg = f"нода «{nr.node_type}» отключена в графе"
        if strict:
            raise ValueError(msg)
        logger.debug("[#{}] {}", project.id, msg)
        return False
    if nr.status == NodeRunStatus.done:
        msg = (
            f"нода «{nr.node_type}» уже в статусе «готово» — сбросьте шаг перед перезапуском"
        )
        if strict:
            raise ValueError(msg)
        logger.debug("[#{}] {}", project.id, msg)
        return False
    if nr.status == NodeRunStatus.waiting_hitl:
        msg = (
            f"нода «{nr.node_type}» ждёт проверки (HITL) — завершите проверку или сбросьте шаг"
        )
        if strict:
            raise ValueError(msg)
        logger.debug("[#{}] {}", project.id, msg)
        return False
    if nr.status == NodeRunStatus.running:
        return True
    if not queue_node_for_start(nr, project_id=project.id, initiator="api"):
        pass
    if not start_node_running(nr, project_id=project.id, initiator="api"):
        if strict:
            raise ValueError(
                f"нода «{nr.node_type}» не перешла в «выполняется» "
                f"(текущий статус: {nr.status.value})"
            )
        return False
    await session.flush()
    return True


async def mark_running_node_failed(
    session: AsyncSession,
    project: Project,
    error: str,
    *,
    initiator: str = "worker",
) -> None:
    """running → failed для активной ноды проекта."""
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return
    node_type = RUNNING_TO_NODE_TYPE.get(project.status)
    if not node_type:
        return
    for nr in run.node_runs:
        if nr.node_type == node_type and nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
            NodeRunStatus.waiting_hitl,
        ):
            fail_node(nr, error, project_id=project.id, initiator=initiator)
            await session.flush()
            return


async def reset_nodes_from_step(
    session: AsyncSession,
    project_id: int,
    step_code: str,
) -> None:
    """Сбросить ноды начиная с step_code → pending (явный reset)."""
    run = await _workflow_run_with_nodes(session, project_id)
    if run is None:
        return
    node_type = STEP_CODE_TO_NODE_TYPE.get(step_code)
    if step_code == "excel_gpt":
        node_type = EXCEL_GPT_NODE_TYPE
    if node_type is None:
        return
    try:
        start_idx = NODE_TYPE_ORDER.index(node_type)
    except ValueError:
        start_idx = 0
    for nr in run.node_runs:
        if nr.node_type in NODE_TYPE_ORDER:
            idx = NODE_TYPE_ORDER.index(nr.node_type)
            if idx >= start_idx:
                reset_node_to_pending(nr, project_id=project_id, initiator="api_reset")
        elif nr.node_type == EXCEL_GPT_NODE_TYPE and step_code == "excel_gpt":
            reset_node_to_pending(nr, project_id=project_id, initiator="api_reset")
    await session.flush()


async def stop_active_running_node(
    session: AsyncSession,
    project: Project,
) -> None:
    """При ⏹ STOP: running/queued → pending (явное действие пользователя)."""
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return
    node_type = RUNNING_TO_NODE_TYPE.get(project.status)
    if not node_type:
        for nr in run.node_runs:
            if nr.status in (NodeRunStatus.running, NodeRunStatus.queued):
                reset_node_to_pending(nr, project_id=project.id, initiator="api_stop")
        await session.flush()
        return
    for nr in run.node_runs:
        if nr.node_type == node_type and nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
        ):
            reset_node_to_pending(nr, project_id=project.id, initiator="api_stop")
            await session.flush()
            return
