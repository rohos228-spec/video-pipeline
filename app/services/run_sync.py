"""Синхронизатор NodeRun ↔ WorkflowRun для веб-UI.

NodeRun.status — единственный источник правды (см. node_status_machine.py).
run_sync НЕ повышает статусы из Project.status; только:
  - skipped для отключённых нод;
  - агрегация WorkflowRun.status;
  - reconcile зависших running/queued без живой задачи.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
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
    complete_node,
    fail_node,
    mark_node_skipped,
    queue_node_for_start,
    reset_node_to_pending,
    start_node_running,
)

_STALE_NODE_RUN_ERROR = "прервано: рабочий процесс не активен"
_STALE_GRACE_SEC = 30.0


async def _get_default_workflow_id(
    session: AsyncSession | None = None,
) -> int | None:
    """Id default Workflow. Optional session — для тестов/вызовов с уже открытой сессией."""

    async def _lookup(s: AsyncSession) -> int | None:
        try:
            wf = (
                await s.execute(
                    select(Workflow)
                    .where(Workflow.is_default == True)  # noqa: E712
                    .order_by(Workflow.id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
        except Exception:  # noqa: BLE001 — пустая/битая БД в тестах
            logger.debug("default workflow lookup failed", exc_info=True)
            return None
        return wf.id if wf is not None else None

    if session is not None:
        return await _lookup(session)
    async with session_scope() as s:
        return await _lookup(s)


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


from app.orchestrator.node_registry import (
    READY_TO_NODE_TYPE,
    RUNNING_TO_NODE_TYPE,
    STEP_CODE_TO_NODE_TYPE,
)
from app.services.disabled_nodes import disabled_node_types
from app.services.excel_gpt_node import (
    EXCEL_GPT_NODE_TYPE,
    active_excel_gpt_node_key,
    resolve_excel_gpt_node_key_for_slot,
    slot_from_ready_status,
    slot_from_running_status,
)


def _canvas_node_type_for_running(status: ProjectStatus) -> str | None:
    """Тип ноды на канвасе для running ProjectStatus (excel_gpt, не enrich_N)."""
    if slot_from_running_status(status) is not None:
        return EXCEL_GPT_NODE_TYPE
    return RUNNING_TO_NODE_TYPE.get(status)


def _canvas_node_type_for_ready(status: ProjectStatus) -> str | None:
    if slot_from_ready_status(status) is not None:
        return EXCEL_GPT_NODE_TYPE
    return READY_TO_NODE_TYPE.get(status)


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


def _aggregate_workflow_run_status(run: WorkflowRun) -> None:
    """Обновить WorkflowRun.status по NodeRun-ам."""
    now = datetime.utcnow()
    any_running = False
    any_pending = False
    any_failed = False
    for nr in run.node_runs:
        if nr.status in (NodeRunStatus.running, NodeRunStatus.queued):
            any_running = True
        elif nr.status == NodeRunStatus.pending:
            any_pending = True
        elif nr.status == NodeRunStatus.failed:
            any_failed = True

    if any_failed:
        run.status = WorkflowRunStatus.failed
    elif any_running:
        run.status = WorkflowRunStatus.running
        if run.started_at is None:
            run.started_at = now
    elif any_pending:
        run.status = WorkflowRunStatus.waiting_hitl
    else:
        run.status = WorkflowRunStatus.done
        if run.finished_at is None:
            run.finished_at = now


async def sync_run_for_project(project_id: int) -> None:
    """Синхронизировать skipped/disabled и агрегировать WorkflowRun (без повышения статусов)."""
    from app.services.excel_gpt_node import completed_node_keys

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

        disabled = disabled_node_types(project)
        for nr in run.node_runs:
            if nr.node_type in disabled:
                if nr.status == NodeRunStatus.pending:
                    old = nr.status
                    if mark_node_skipped(nr, project_id=project_id):
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
            elif nr.status == NodeRunStatus.skipped and nr.node_type not in disabled:
                old = nr.status
                if reset_node_to_pending(nr, project_id=project_id, initiator="api_reset"):
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

        # Heal: excel_gpt в completed_keys, но NodeRun pending/running
        # (auto-chain раньше помечал next done и оставлял prev running).
        # Не трогаем только реально активную ноду.
        active_key = active_excel_gpt_node_key(project)
        done_keys = completed_node_keys(project)
        for nr in run.node_runs:
            if nr.node_type != EXCEL_GPT_NODE_TYPE:
                continue
            if nr.node_key not in done_keys:
                continue
            if active_key and nr.node_key == active_key:
                continue
            if nr.status == NodeRunStatus.done:
                continue
            if nr.status == NodeRunStatus.pending:
                queue_node_for_start(nr, project_id=project_id, initiator="worker")
                start_node_running(nr, project_id=project_id, initiator="worker")
            if nr.status in (
                NodeRunStatus.running,
                NodeRunStatus.queued,
                NodeRunStatus.waiting_hitl,
                NodeRunStatus.pending,
            ):
                old = nr.status.value
                if complete_node(nr, project_id=project_id, initiator="worker"):
                    await publish_node_event(
                        run.id,
                        event_type="node_status_changed",
                        node_key=nr.node_key,
                        payload={
                            "node_type": nr.node_type,
                            "from": old,
                            "to": nr.status.value,
                            "project_id": project_id,
                        },
                    )
                    logger.info(
                        "[#{}] heal NodeRun {} → done (excel_gpt_completed_keys, was {})",
                        project_id,
                        nr.node_key,
                        old,
                    )

        _aggregate_workflow_run_status(run)


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
    """Фоновая задача: агрегация WorkflowRun (без повышения NodeRun из project.status)."""
    while True:
        try:
            await sync_all_active_projects()
        except Exception:  # noqa: BLE001
            logger.exception("background_sync_loop iteration failed")
        await asyncio.sleep(interval_sec)


async def resolve_node_run_for_step(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
    enrich_slot: int | None = None,
) -> NodeRun | None:
    """Найти NodeRun для шага (по node_key или node_type).

    Для excel_gpt при нескольких нодах нужен node_key / active key / enrich_slot.
    """
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return None
    key = (node_key or "").strip() or None
    if step_code == "excel_gpt" and not key:
        key = active_excel_gpt_node_key(project)
    if step_code == "excel_gpt" and not key and enrich_slot is not None:
        key = resolve_excel_gpt_node_key_for_slot(project, enrich_slot)
    if key:
        for nr in run.node_runs:
            if nr.node_key == key:
                return nr
    node_type = STEP_CODE_TO_NODE_TYPE.get(step_code)
    if node_type is None:
        return None
    matches = [nr for nr in run.node_runs if nr.node_type == node_type]
    if len(matches) == 1:
        return matches[0]
    if key:
        for nr in matches:
            if nr.node_key == key:
                return nr
    # Несколько excel_gpt — без явного node_key не угадываем первую.
    if step_code == "excel_gpt" and len(matches) > 1:
        return None
    return matches[0] if matches else None


async def prepare_node_for_step_start(
    session: AsyncSession,
    project: Project,
    step_code: str,
    *,
    node_key: str | None = None,
    enrich_slot: int | None = None,
    strict: bool = False,
    explicit_ui_start: bool = False,
) -> bool:
    """Подготовить ноду к запуску шага: queued → running."""
    from app.services.canvas_graph import sync_run_snapshot_from_canvas_graph
    from app.services.step_cancel import is_generation_active

    default_wf = await _get_default_workflow_id(session)
    if default_wf is None:
        if strict:
            raise ValueError("workflow по умолчанию не найден — сохраните workflow")
        logger.debug(
            "[#{}] prepare_node_for_step_start: нет default workflow, FSM пропущен",
            project.id,
        )
        return False
    # ensure_run пишет в свою session_scope — только если default workflow известен.
    try:
        await ensure_run_for_project(project.id, default_wf)
    except Exception:  # noqa: BLE001
        if strict:
            raise
        logger.debug(
            "[#{}] prepare_node_for_step_start: ensure_run_for_project failed",
            project.id,
            exc_info=True,
        )
        return False
    # Canvas мог вырасти после create_run — дописать недостающие NodeRun.
    try:
        await sync_run_snapshot_from_canvas_graph(session, project)
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] prepare_node_for_step_start: canvas sync failed",
            project.id,
            exc_info=True,
        )
    resolved_key = (node_key or "").strip() or None
    if step_code == "excel_gpt" and not resolved_key and enrich_slot is not None:
        resolved_key = resolve_excel_gpt_node_key_for_slot(project, enrich_slot)
    nr = await resolve_node_run_for_step(
        session,
        project,
        step_code,
        node_key=resolved_key,
        enrich_slot=enrich_slot,
    )
    if nr is None:
        if strict:
            raise ValueError(
                f"нода для шага «{step_code}» не найдена в WorkflowRun — создайте Run на канвасе"
            )
        logger.debug(
            "[#{}] prepare_node_for_step_start: NodeRun для {} не найден"
            + (f" (slot={enrich_slot})" if enrich_slot is not None else ""),
            project.id,
            step_code,
        )
        return False
    if nr.status == NodeRunStatus.skipped:
        if explicit_ui_start:
            # Ручной старт: включаем ноду обратно и продолжаем.
            meta = dict(project.meta or {})
            disabled = [str(x) for x in (meta.get("disabled_nodes") or [])]
            if nr.node_key in disabled:
                meta["disabled_nodes"] = [k for k in disabled if k != nr.node_key]
                project.meta = meta
            reset_node_to_pending(nr, project_id=project.id, initiator="ui_restart")
            logger.info(
                "[#{}] prepare_node: skipped → pending (ui re-enable {})",
                project.id,
                nr.node_key,
            )
        else:
            msg = f"нода «{nr.node_type}» отключена в графе"
            if strict:
                raise ValueError(msg)
            logger.debug("[#{}] {}", project.id, msg)
            return False

    if nr.status in (NodeRunStatus.done, NodeRunStatus.waiting_hitl):
        if explicit_ui_start:
            reset_node_to_pending(nr, project_id=project.id, initiator="ui_restart")
        else:
            msg = (
                f"нода «{nr.node_type}» уже в статусе «{nr.status.value}» — "
                "явный перезапуск только из UI"
            )
            if strict:
                raise ValueError(msg)
            logger.debug("[#{}] {}", project.id, msg)
            return False

    if nr.status in (NodeRunStatus.running, NodeRunStatus.queued):
        if is_generation_active(project.id) and not explicit_ui_start:
            msg = (
                f"нода «{nr.node_type}» уже в работе ({nr.status.value}) — "
                "дождитесь или «Сбросить шаг»"
            )
            if strict:
                raise ValueError(msg)
            return True
        reset_node_to_pending(nr, project_id=project.id, initiator="auto_unstick")
        logger.info(
            "[#{}] prepare_node_for_step_start: auto_unstick {}/{}",
            project.id,
            nr.node_type,
            nr.node_key,
        )

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
    run = await _workflow_run_with_nodes(session, project.id)
    if run is not None:
        await publish_node_event(
            run.id,
            event_type="node_status_changed",
            node_key=nr.node_key,
            payload={
                "node_type": nr.node_type,
                "from": "pending",
                "to": nr.status.value,
                "project_id": project.id,
            },
        )
    return True


async def complete_excel_gpt_node_by_key(
    session: AsyncSession,
    project: Project,
    node_key: str | None,
    *,
    enrich_slot: int | None = None,
) -> bool:
    """Пометить excel_gpt NodeRun done ДО auto-chain на следующий слот.

    Вызывать из enrich_xlsx после успешного скачивания/sync xlsx, пока
    active_key ещё не переключён на next — иначе UI оставляет prev «в работе».
    """
    from app.services.canvas_graph import sync_run_snapshot_from_canvas_graph
    from app.services.excel_gpt_node import resolve_excel_gpt_node_key_for_slot

    key = (node_key or "").strip() or None
    if not key and enrich_slot is not None:
        key = resolve_excel_gpt_node_key_for_slot(project, enrich_slot)
    if not key:
        return False
    try:
        await sync_run_snapshot_from_canvas_graph(session, project)
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] complete_excel_gpt_node: canvas sync failed",
            project.id,
            exc_info=True,
        )
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return False
    for nr in run.node_runs:
        if nr.node_type != EXCEL_GPT_NODE_TYPE or nr.node_key != key:
            continue
        if nr.status == NodeRunStatus.done:
            return True
        if nr.status == NodeRunStatus.pending:
            queue_node_for_start(nr, project_id=project.id, initiator="worker")
            start_node_running(nr, project_id=project.id, initiator="worker")
        if nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
            NodeRunStatus.waiting_hitl,
            NodeRunStatus.pending,
        ):
            old = nr.status.value
            if complete_node(nr, project_id=project.id, initiator="worker"):
                await session.flush()
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": old,
                        "to": nr.status.value,
                        "project_id": project.id,
                    },
                )
                logger.info(
                    "[#{}] excel_gpt NodeRun {} → done (slot complete before chain)",
                    project.id,
                    key,
                )
                return True
        return nr.status == NodeRunStatus.done
    return False


async def complete_active_node_for_step(
    session: AsyncSession,
    project: Project,
    *,
    prev_status: ProjectStatus,
    new_status: ProjectStatus,
) -> None:
    """running → done для ноды после успешного шага воркера.

    Для excel_gpt: active_excel_gpt_node_key часто уже pop'нут в enrich_xlsx
    до этого вызова — резолвим ноду по слоту из prev_status (enriching_N).
    Если prepare не сработал (pending), поднимаем pending→running→done
    (шаг на стороне Project уже успешен).
    """
    from app.services.canvas_graph import sync_run_snapshot_from_canvas_graph

    node_type = _canvas_node_type_for_ready(new_status)
    if node_type is None:
        node_type = _canvas_node_type_for_running(prev_status)
    if node_type is None:
        return
    try:
        await sync_run_snapshot_from_canvas_graph(session, project)
    except Exception:  # noqa: BLE001
        logger.debug(
            "[#{}] complete_active_node: canvas sync failed",
            project.id,
            exc_info=True,
        )
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return

    # excel_gpt auto-chain УЖЕ ставит active_excel_gpt_node_key на СЛЕДУЮЩУЮ
    # ноду до вызова complete (enriching_N → enriching_N+1). Брать active_key
    # первым = пометить next done и оставить prev running — UI врёт, xlsx
    # следующего слота «применён» без работы. Всегда слот из prev_status.
    finished_key: str | None = None
    if node_type == EXCEL_GPT_NODE_TYPE:
        slot = slot_from_running_status(prev_status)
        if slot is None:
            slot = slot_from_ready_status(new_status)
        if slot is not None:
            finished_key = resolve_excel_gpt_node_key_for_slot(project, slot)
        if not finished_key:
            finished_key = active_excel_gpt_node_key(project)
            if finished_key:
                logger.warning(
                    "[#{}] complete_active_node: excel_gpt fallback active_key={} "
                    "(prev={} → {}) — слот prev не резолвится",
                    project.id,
                    finished_key,
                    prev_status.value,
                    new_status.value,
                )

    excel_matches = [
        nr for nr in run.node_runs if nr.node_type == EXCEL_GPT_NODE_TYPE
    ]
    # Несколько excel_gpt без ключа — не трогаем «первую попавшуюся».
    if (
        node_type == EXCEL_GPT_NODE_TYPE
        and not finished_key
        and len(excel_matches) > 1
    ):
        logger.warning(
            "[#{}] complete_active_node: excel_gpt multi-node без ключа "
            "(prev={} → {}), NodeRun не помечен done",
            project.id,
            prev_status.value,
            new_status.value,
        )
        return

    for nr in run.node_runs:
        if nr.node_type != node_type:
            continue
        if finished_key and nr.node_key != finished_key:
            continue
        if nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
            NodeRunStatus.waiting_hitl,
        ):
            if complete_node(nr, project_id=project.id, initiator="worker"):
                await session.flush()
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": "running",
                        "to": nr.status.value,
                        "project_id": project.id,
                    },
                )
                logger.info(
                    "[#{}] NodeRun {} → done (finished slot, prev={} → {})",
                    project.id,
                    nr.node_key,
                    prev_status.value,
                    new_status.value,
                )
            return
        # Recovery: шаг успешен, но prepare не нашёл ноду (осталась pending).
        if (
            node_type == EXCEL_GPT_NODE_TYPE
            and finished_key
            and nr.node_key == finished_key
            and nr.status == NodeRunStatus.pending
        ):
            queue_node_for_start(nr, project_id=project.id, initiator="worker")
            start_node_running(nr, project_id=project.id, initiator="worker")
            if complete_node(nr, project_id=project.id, initiator="worker"):
                await session.flush()
                await publish_node_event(
                    run.id,
                    event_type="node_status_changed",
                    node_key=nr.node_key,
                    payload={
                        "node_type": nr.node_type,
                        "from": "pending",
                        "to": nr.status.value,
                        "project_id": project.id,
                    },
                )
                logger.info(
                    "[#{}] NodeRun {} → done (recovery pending→done, prev={})",
                    project.id,
                    nr.node_key,
                    prev_status.value,
                )
            return


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
    node_type = _canvas_node_type_for_running(project.status)
    if not node_type:
        return
    active_key = (
        active_excel_gpt_node_key(project) if node_type == EXCEL_GPT_NODE_TYPE else None
    )
    if node_type == EXCEL_GPT_NODE_TYPE and not active_key:
        slot = slot_from_running_status(project.status)
        if slot is not None:
            active_key = resolve_excel_gpt_node_key_for_slot(project, slot)
    for nr in run.node_runs:
        if nr.node_type != node_type:
            continue
        if active_key and nr.node_key != active_key:
            continue
        if nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
            NodeRunStatus.waiting_hitl,
        ):
            fail_node(nr, error, project_id=project.id, initiator=initiator)
            await session.flush()
            await publish_node_event(
                run.id,
                event_type="node_status_changed",
                node_key=nr.node_key,
                payload={
                    "node_type": nr.node_type,
                    "from": "running",
                    "to": nr.status.value,
                    "project_id": project.id,
                    "error": error[:200],
                },
            )
            return


async def update_active_node_progress_text(
    session: AsyncSession,
    project: Project,
    progress_text: str | None,
) -> None:
    """Обновить progress_text активной ноды (видно в UI при running)."""
    run = await _workflow_run_with_nodes(session, project.id)
    if run is None:
        return
    node_type = _canvas_node_type_for_running(project.status)
    active_key = (
        active_excel_gpt_node_key(project) if node_type == EXCEL_GPT_NODE_TYPE else None
    )
    if node_type == EXCEL_GPT_NODE_TYPE and not active_key:
        slot = slot_from_running_status(project.status)
        if slot is not None:
            active_key = resolve_excel_gpt_node_key_for_slot(project, slot)
    for nr in run.node_runs:
        if node_type and nr.node_type != node_type:
            continue
        if active_key and nr.node_key != active_key:
            continue
        if nr.status in (NodeRunStatus.running, NodeRunStatus.queued):
            nr.progress_text = (progress_text or None)
            if nr.progress_text:
                nr.progress_text = nr.progress_text[:200]
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
    try:
        enrich_idx = NODE_TYPE_ORDER.index("enrich_1")
    except ValueError:
        enrich_idx = start_idx
    # excel_gpt на канвасе вместо enrich_* — сбрасываем вместе с ранними шагами.
    reset_excel = step_code == "excel_gpt" or start_idx <= enrich_idx
    for nr in run.node_runs:
        if nr.node_type in NODE_TYPE_ORDER:
            idx = NODE_TYPE_ORDER.index(nr.node_type)
            if idx >= start_idx:
                reset_node_to_pending(nr, project_id=project_id, initiator="api_reset")
        elif nr.node_type == EXCEL_GPT_NODE_TYPE and reset_excel:
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

    async def _reset_and_notify(nr: NodeRun) -> None:
        old = nr.status
        if not reset_node_to_pending(nr, project_id=project.id, initiator="api_stop"):
            return
        await publish_node_event(
            run.id,
            event_type="node_status_changed",
            node_key=nr.node_key,
            payload={
                "node_type": nr.node_type,
                "from": old.value,
                "to": nr.status.value,
                "project_id": project.id,
            },
        )

    node_type = _canvas_node_type_for_running(project.status)
    if not node_type:
        for nr in run.node_runs:
            if nr.status in (NodeRunStatus.running, NodeRunStatus.queued):
                await _reset_and_notify(nr)
        await session.flush()
        return
    active_key = (
        active_excel_gpt_node_key(project) if node_type == EXCEL_GPT_NODE_TYPE else None
    )
    for nr in run.node_runs:
        if nr.node_type != node_type:
            continue
        if active_key and nr.node_key != active_key:
            continue
        if nr.status in (
            NodeRunStatus.running,
            NodeRunStatus.queued,
        ):
            await _reset_and_notify(nr)
            await session.flush()
            return


# Линейный порядок типов нод (legacy — reset_nodes_from_step, тесты).
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


async def _reconcile_stale_node_runs(
    *,
    initiator: str,
    require_no_live_task: bool = False,
    grace_sec: float = _STALE_GRACE_SEC,
) -> int:
    """NodeRun running/queued без живого воркера → failed."""
    from app.services.step_cancel import is_generation_active

    fixed = 0
    now = datetime.utcnow()
    grace = timedelta(seconds=grace_sec)
    async with session_scope() as session:
        runs = (
            await session.execute(
                select(WorkflowRun).options(selectinload(WorkflowRun.node_runs))
            )
        ).scalars().all()
        for run in runs:
            if run.project_id is None:
                continue
            live = is_generation_active(run.project_id)
            for nr in run.node_runs:
                if nr.status not in (NodeRunStatus.running, NodeRunStatus.queued):
                    continue
                if require_no_live_task:
                    if live:
                        continue
                    if nr.started_at is not None and now - nr.started_at < grace:
                        continue
                old = nr.status
                if fail_node(
                    nr,
                    _STALE_NODE_RUN_ERROR,
                    project_id=run.project_id,
                    initiator=initiator,
                ):
                    fixed += 1
                    logger.warning(
                        "[#{}] NodeRun {}/{}: {} → failed ({})",
                        run.project_id,
                        nr.node_type,
                        nr.node_key,
                        old.value,
                        initiator,
                    )
        if fixed:
            await session.commit()
    if fixed:
        logger.info("reconcile stale NodeRun: {} → failed ({})", fixed, initiator)
    return fixed


async def reconcile_stale_node_runs_on_startup() -> int:
    """NodeRun running/queued без живого воркера после перезапуска → failed."""
    return await _reconcile_stale_node_runs(initiator="startup_reconcile")


async def background_node_run_reconcile_loop(*, interval_sec: float = 60.0) -> None:
    """Фон: running/queued без живой задачи дольше N сек → failed."""
    while True:
        try:
            await _reconcile_stale_node_runs(
                initiator="background_reconcile",
                require_no_live_task=True,
            )
        except Exception:  # noqa: BLE001
            logger.exception("background_node_run_reconcile_loop failed")
        await asyncio.sleep(interval_sec)
