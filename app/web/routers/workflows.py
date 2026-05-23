"""REST: /api/workflows."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Workflow
from app.web.deps import get_session
from app.web.schemas import (
    WorkflowDetail,
    WorkflowSaveRequest,
    WorkflowSummary,
)
from app.web.settings_default import _default_graph

router = APIRouter(prefix="/workflows", tags=["workflows"])


@router.get("", response_model=list[WorkflowSummary])
async def list_workflows(session: AsyncSession = Depends(get_session)) -> list[Workflow]:
    rows = (await session.execute(select(Workflow).order_by(Workflow.id.desc()))).scalars().all()
    return list(rows)


@router.get("/{workflow_id}", response_model=WorkflowDetail)
async def get_workflow(workflow_id: int, session: AsyncSession = Depends(get_session)) -> Workflow:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    return wf


@router.post("", response_model=WorkflowDetail, status_code=status.HTTP_201_CREATED)
async def create_workflow(
    payload: WorkflowSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    wf = Workflow(
        name=payload.name or "Untitled",
        description=payload.description,
        nodes=[n.model_dump() for n in payload.nodes],
        edges=[e.model_dump() for e in payload.edges],
        is_default=False,
        version=1,
    )
    session.add(wf)
    await session.commit()
    await session.refresh(wf)
    return wf


@router.put("/{workflow_id}", response_model=WorkflowDetail)
async def update_workflow(
    workflow_id: int,
    payload: WorkflowSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if payload.name is not None:
        wf.name = payload.name
    if payload.description is not None:
        wf.description = payload.description
    wf.nodes = [n.model_dump() for n in payload.nodes]
    wf.edges = [e.model_dump() for e in payload.edges]
    wf.version = (wf.version or 1) + 1
    await session.commit()
    await session.refresh(wf)
    return wf


@router.delete("/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_workflow(
    workflow_id: int,
    session: AsyncSession = Depends(get_session),
) -> None:
    wf = await session.get(Workflow, workflow_id)
    if wf is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    if wf.is_default:
        raise HTTPException(status_code=400, detail="cannot delete default workflow")
    await session.delete(wf)
    await session.commit()


@router.post("/{workflow_id}/duplicate", response_model=WorkflowDetail)
async def duplicate_workflow(
    workflow_id: int,
    session: AsyncSession = Depends(get_session),
) -> Workflow:
    src = await session.get(Workflow, workflow_id)
    if src is None:
        raise HTTPException(status_code=404, detail="workflow not found")
    copy = Workflow(
        name=f"{src.name} (копия)",
        description=src.description,
        nodes=list(src.nodes or []),
        edges=list(src.edges or []),
        is_default=False,
        version=1,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return copy


@router.post("/default/reset", response_model=WorkflowDetail)
async def reset_default(session: AsyncSession = Depends(get_session)) -> Workflow:
    """Сбросить дефолтный workflow к фабричному графу (полезно после правок)."""
    wf = (
        await session.execute(select(Workflow).where(Workflow.is_default == True))  # noqa: E712
    ).scalar_one_or_none()
    nodes, edges = _default_graph()
    if wf is None:
        wf = Workflow(
            name="Стандартный shorts-пайплайн",
            description="Дефолтный шаблон.",
            nodes=nodes,
            edges=edges,
            is_default=True,
            version=1,
        )
        session.add(wf)
    else:
        wf.nodes = nodes
        wf.edges = edges
        wf.version = (wf.version or 1) + 1
    await session.commit()
    await session.refresh(wf)
    return wf
