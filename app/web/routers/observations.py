"""Explicit observe-only scope and durable history. No execution/recovery endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import NodeRun, Project, WorkflowRun
from app.services.observation import ExecutionControl, ObservationEvent, digest, get_observer, redact

router = APIRouter(prefix="/projects/{project_id}/observations", tags=["observations"])


class EnableObservation(BaseModel):
    node_keys: list[str] = Field(min_length=1, max_length=100)


def scope_json(scope):
    return {key: getattr(scope, key) for key in (
        "execution_id", "project_id", "workflow_run_id", "node_keys", "graph_version",
        "mode", "observer_state", "created_at", "cursors", "gaps")}


async def _scope(session, project_id, execution_id):
    scope = await session.get(ExecutionControl, execution_id)
    if scope is None or scope.project_id != project_id:
        raise HTTPException(404, "Observation not found for project")
    return scope


@router.post("", status_code=201)
async def enable_observation(project_id: int, payload: EnableObservation):
    observer = get_observer()
    async with observer.master() as session:
        run = (await session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project_id))).first()
        if run is None:
            raise HTTPException(404, "Existing workflow run required")
        valid = {str(n.get("id")) for n in run.nodes_snapshot or []}
        if not set(payload.node_keys).issubset(valid):
            raise HTTPException(422, "Node scope does not match workflow graph")
        graph_version = digest({"nodes": run.nodes_snapshot, "edges": run.edges_snapshot})
        run_id = run.id
    try:
        scope = await observer.enable(project_id=project_id, workflow_run_id=run_id,
            node_keys=payload.node_keys, graph_version=graph_version)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return scope_json(scope)


@router.get("")
async def list_observations(project_id: int):
    async with get_observer().master() as session:
        scopes = (await session.scalars(select(ExecutionControl).where(
            ExecutionControl.project_id == project_id).order_by(ExecutionControl.created_at.desc()).limit(100))).all()
        return [scope_json(scope) for scope in scopes]


@router.post("/{execution_id}/stop")
async def stop_observation(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
    await observer.stop(execution_id)
    return {"observer_state": "stopped", "business_action": None}


@router.get("/{execution_id}/events")
async def history(project_id: int, execution_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
        rows = (await session.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id, ObservationEvent.id > after,
        ).order_by(ObservationEvent.id).limit(limit + 1))).all()
        return {"events": [{key: getattr(row, key) for key in (
            "id", "event_id", "execution_id", "project_id", "workflow_run_id", "node_key",
            "attempt_id", "request_id", "db_scope", "kind", "payload", "created_at",
        )} for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_cursor": rows[min(len(rows), limit)-1].id if rows else after}


async def _business_snapshot(session, scope):
    project = await session.get(Project, scope.project_id)
    run = await session.get(WorkflowRun, scope.workflow_run_id)
    nodes = (await session.scalars(select(NodeRun).where(
        NodeRun.workflow_run_id == scope.workflow_run_id, NodeRun.node_key.in_(scope.node_keys),
    ))).all()
    return {"project_status": project.status.value if project else None,
        "workflow_status": run.status.value if run else None,
        "nodes": {n.node_key: {"status": n.status.value, "progress": n.progress,
            "progress_text": redact(n.progress_text), "error": redact(n.error),
            "attempts": n.attempts} for n in nodes}}, project


@router.get("/{execution_id}/context")
async def context(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        scope = await _scope(session, project_id, execution_id)
        master, project = await _business_snapshot(session, scope)
        rows = (await session.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id).order_by(ObservationEvent.id.desc()).limit(500))).all()
        result = scope_json(scope)
    gaps = list(result["gaps"] or [])
    replica = None
    if project and (Path(project.data_dir) / "project.db").exists():
        try:
            from app.project_db import get_project_sessionmaker
            factory = await get_project_sessionmaker(project.data_dir)
            async with factory() as session:
                replica, _ = await _business_snapshot(session, scope)
        except Exception as exc:
            gaps.append({"kind": "project_snapshot_unavailable", "error_type": type(exc).__name__})
    else:
        gaps.append({"kind": "project_database_not_available"})
    states = (replica or master)["nodes"]
    statuses = [v["status"] for v in states.values()]
    outcome = "done" if len(states) == len(scope.node_keys) and all(s == "done" for s in statuses) else (
        "failed" if "failed" in statuses else "incomplete")
    end_attempts = {row.attempt_id for row in rows if row.kind == "node_end"}
    incomplete = [{"node_key": row.node_key, "attempt_id": row.attempt_id} for row in rows
                  if row.kind == "node_start" and row.attempt_id not in end_attempts]
    if incomplete:
        gaps.append({"kind": "attempts_without_recorded_end", "attempts": incomplete})
    if execution_id in observer.failures:
        gaps.append({"kind": "observer_error", "error_type": observer.failures[execution_id]})
    result.update({"master": master, "project": replica,
        "disagreement": replica is not None and master != replica,
        "business_group_outcome": outcome,
        "current_nodes": [key for key, state in states.items() if state["status"] in {"running", "queued", "waiting_hitl"}],
        "latest_problem": next(({"kind": r.kind, "node_key": r.node_key, "payload": r.payload}
                                for r in rows if r.kind in {"node_error", "parse_error", "observer_error"}), None),
        "data_gaps": gaps,
        "unknown": ["Semantic diagnosis is not implemented", "Scope does not stop the pipeline"],
    })
    return result
