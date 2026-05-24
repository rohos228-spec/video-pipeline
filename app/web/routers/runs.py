"""REST: /api/runs."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    NodeRun,
    Project,
    ProjectStatus,
    Workflow,
    WorkflowRun,
    WorkflowRunStatus,
)
from app.services.event_bus import publish_node_event
from app.services.project_control import stop_project_running
from app.web.deps import get_session
from app.web.routers.projects import _slugify
from app.web.schemas import (
    StartRunRequest,
    WorkflowRunDetail,
    WorkflowRunSummary,
)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.get("", response_model=list[WorkflowRunSummary])
async def list_runs(
    session: AsyncSession = Depends(get_session),
) -> list[WorkflowRun]:
    rows = (
        await session.execute(
            select(WorkflowRun).order_by(WorkflowRun.id.desc()).limit(100)
        )
    ).scalars().all()
    return list(rows)


@router.get("/{run_id}", response_model=WorkflowRunDetail)
async def get_run(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRun:
    run = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    return run


@router.post(
    "/from-workflow/{workflow_id}",
    response_model=WorkflowRunDetail,
    status_code=status.HTTP_201_CREATED,
)
async def start_run(
    workflow_id: int,
    payload: StartRunRequest,
    session: AsyncSession = Depends(get_session),
) -> WorkflowRun:
    """Запустить новый Run на основе Workflow.

    Если `project_id` указан — привязываем к существующему проекту.
    Иначе — создаём новый проект по `topic`.
    """
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")

    if payload.project_id is not None:
        project = await session.get(Project, payload.project_id)
        if project is None:
            raise HTTPException(status_code=404, detail="project not found")
    else:
        if not payload.topic:
            raise HTTPException(
                status_code=400, detail="either project_id or topic is required"
            )
        base_slug = _slugify(payload.topic)
        slug = base_slug
        n = 2
        while (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalar_one_or_none() is not None:
            slug = f"{base_slug}-{n}"
            n += 1
        project = Project(
            slug=slug,
            topic=payload.topic.strip(),
            hero_mode=payload.hero_mode,
            status=ProjectStatus.new,
        )
        session.add(project)
        await session.flush()

    # Гарантия 1:1 — если у проекта уже есть Run, отдаём его (вместо ошибки UNIQUE).
    existing = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.project_id == project.id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    run = WorkflowRun(
        workflow_id=wf.id,
        project_id=project.id,
        status=WorkflowRunStatus.new,
        nodes_snapshot=list(wf.nodes or []),
        edges_snapshot=list(wf.edges or []),
    )
    session.add(run)
    await session.flush()

    # Создаём pending NodeRun для каждого узла снапшота.
    for node in run.nodes_snapshot:
        nr = NodeRun(
            workflow_run_id=run.id,
            node_key=node["id"],
            node_type=node["type"],
        )
        session.add(nr)
    await session.commit()

    # Перечитываем с node_runs eager-load для ответа.
    full = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.id == run.id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one()
    await publish_node_event(run.id, event_type="run_created", payload={
        "project_id": project.id,
        "workflow_id": wf.id,
    })
    return full


@router.post("/{run_id}/cancel", response_model=WorkflowRunDetail)
async def cancel_run(
    run_id: int, session: AsyncSession = Depends(get_session)
) -> WorkflowRun:
    run = (
        await session.execute(
            select(WorkflowRun)
            .where(WorkflowRun.id == run_id)
            .options(selectinload(WorkflowRun.node_runs))
        )
    ).scalar_one_or_none()
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    if run.project_id is not None:
        project = await session.get(Project, run.project_id)
        if project is not None:
            await stop_project_running(session, project)
    run.status = WorkflowRunStatus.cancelled
    run.finished_at = datetime.utcnow()
    await session.commit()
    await publish_node_event(run.id, event_type="run_cancelled")
    return run
