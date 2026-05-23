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
    ProjectStatus.assembling: ("assemble", NodeRunStatus.running),
    ProjectStatus.assembled: ("assemble", NodeRunStatus.done),
    ProjectStatus.publishing: ("publish", NodeRunStatus.running),
    ProjectStatus.published: ("publish", NodeRunStatus.done),
}

# Линейный порядок типов нод (для определения «всё до этой = done»).
NODE_TYPE_ORDER: list[str] = [
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
        run = WorkflowRun(
            workflow_id=wf.id,
            project_id=project_id,
            status=WorkflowRunStatus.new,
            nodes_snapshot=list(wf.nodes or []),
            edges_snapshot=list(wf.edges or []),
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


from app.orchestrator.graph.planner import graph_executor_enabled, load_graph_for_project
from app.orchestrator.node_registry import (
    LINEAR_NODE_TYPES,
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
)
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

        derived = _derived_node_states(
            project.status,
            disabled_node_types(project),
        )
        derived_by_key: dict[str, NodeRunStatus] | None = None
        if graph_executor_enabled(project):
            try:
                graph = await load_graph_for_project(s, project)
                derived_by_key = graph.derived_node_states(project)
            except Exception:  # noqa: BLE001
                logger.exception("graph derived states failed for #{}", project_id)
        if not derived:
            return

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
                continue
            if nr.status != target:
                old = nr.status
                nr.status = target
                if target == NodeRunStatus.running and nr.started_at is None:
                    nr.started_at = now
                if target == NodeRunStatus.done and nr.finished_at is None:
                    nr.finished_at = now
                # Публикуем только при реальном переходе.
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": old.value,
                        "to": target.value,
                        "project_id": project_id,
                    },
                )
            if nr.status == NodeRunStatus.running:
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
