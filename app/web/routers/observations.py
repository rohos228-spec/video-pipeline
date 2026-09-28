"""Explicit observe-only scope and durable history. No execution/recovery endpoints."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import NodeRun, Project, WorkflowRun
from app.services.observation import ExecutionControl, ObservationEvent, digest, get_observer, redact, panel_safe

router = APIRouter(prefix="/projects/{project_id}/observations", tags=["observations"])


class EnableObservation(BaseModel):
    node_keys: list[str] = Field(min_length=1, max_length=100)


def scope_json(scope):
    return panel_safe({key: getattr(scope, key) for key in (
        "execution_id", "project_id", "workflow_run_id", "node_keys", "graph_version",
        "mode", "observer_state", "created_at", "cursors", "gaps")})


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
            node_keys=payload.node_keys, graph_version=graph_version, initiator="observation_api")
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
    await observer.stop(execution_id, initiator="observation_api")
    return {"observer_state": "stopped", "business_action": None}


@router.get("/{execution_id}/events")
async def history(project_id: int, execution_id: str, after: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=500)):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
        rows = (await session.scalars(select(ObservationEvent).where(
            ObservationEvent.execution_id == execution_id, ObservationEvent.id > after,
        ).order_by(ObservationEvent.id).limit(limit + 1))).all()
        return panel_safe({"events": [{key: getattr(row, key) for key in (
            "id", "event_id", "execution_id", "project_id", "workflow_run_id", "node_key",
            "attempt_id", "request_id", "db_scope", "kind", "payload", "created_at",
        )} for row in rows[:limit]], "has_more": len(rows) > limit,
            "next_cursor": rows[min(len(rows), limit)-1].id if rows else after})


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
    ended_requests = {row.request_id for row in rows if row.kind in {"model_end", "model_error"}}
    stream_completed = {row.request_id for row in rows if row.kind == "model_stream_event" and row.payload.get("complete")}
    pending_requests = [{"request_id": row.request_id, "node_key": row.node_key,
                         "transport_completed": row.request_id in stream_completed,
                         "completion_unknown": row.request_id not in stream_completed}
                        for row in rows if row.kind == "model_start" and row.request_id not in ended_requests]
    if pending_requests:
        gaps.append({"kind": "requests_without_recorded_end", "requests": pending_requests})
    for row in rows:
        reference = (row.payload or {}).get("body")
        if isinstance(reference, dict) and reference.get("gap"):
            gaps.append({"kind": "body_not_saved", "event_id": row.event_id, "reason": reference["gap"]})
        elif isinstance(reference, dict) and reference.get("path"):
            target = (observer.body_root / reference["path"]).resolve()
            if not target.is_relative_to(observer.body_root.resolve()) or not target.is_file():
                gaps.append({"kind": "body_not_available", "event_id": row.event_id})
    if execution_id in observer.failures:
        gaps.append({"kind": "observer_error", "error_type": observer.failures[execution_id]})
    node_contexts = {}
    for key in scope.node_keys:
        node_rows = [row for row in rows if row.node_key == key]
        start = next((row for row in node_rows if row.kind == "node_start"), None)
        model = next((row for row in node_rows if row.kind == "model_start"), None)
        node_contexts[key] = {
            "latest_attempt_id": start.attempt_id if start else None,
            "model": model.payload.get("model") if model else None,
            "settings_revision": model.payload.get("settings_revision") if model else None,
            "input_refs": [{"event_id": row.event_id, "request_id": row.request_id,
                            "body": row.payload.get("body")} for row in node_rows
                           if row.kind in {"model_start", "node_input", "apply_input"}],
            "output_refs": [{"event_id": row.event_id, "request_id": row.request_id,
                             "body": row.payload.get("body")} for row in node_rows
                            if row.kind in {"model_end", "node_output", "artifact_created"}],
            "checks": [{"kind": row.kind, "request_id": row.request_id, "result": row.payload}
                       for row in node_rows if row.kind in {"parse_result", "domain_check", "runtime_check"}],
            "retry_recorded": any(row.kind == "json_retry" for row in node_rows),
        }
    problem = next((row for row in rows if row.kind in {"node_error", "model_error", "apply_error"}
                    or (row.kind == "parse_result" and row.payload.get("payload_required")
                        and not row.payload.get("payload_present"))), None)
    result.update({"master": master, "project": replica, "node_contexts": node_contexts,
        "wait_reasons": {key: next((row.payload for row in rows if row.kind == "runtime_wait" and row.node_key == key
                                   and row.id > max((r.id for r in rows if r.kind == "node_start" and r.node_key == key), default=0)),
            {"reason": "waiting_hitl", "initiator": None}) for key, state in states.items()
            if state["status"] == "waiting_hitl" or (state["status"] not in {"done", "skipped"} and any(row.kind == "runtime_wait" and row.node_key == key
                and row.id > max((r.id for r in rows if r.kind == "node_start" and r.node_key == key), default=0) for row in rows))},
        "pinned": observer.is_pinned(execution_id),
        "commit_confirmation": observer.commit_uncertainty.get(execution_id),
        "disagreement": replica is not None and master != replica,
        "business_group_outcome": outcome,
        "current_nodes": [key for key, state in states.items() if state["status"] in {"running", "queued", "waiting_hitl"}],
        "latest_problem": {"kind": problem.kind, "node_key": problem.node_key,
            "event_id": problem.event_id, "request_id": problem.request_id, "payload": problem.payload,
            "later_apply_committed": any(row.node_key == problem.node_key and row.id > problem.id
                                        and row.kind == "data_applied" for row in rows)} if problem else None,
        "data_gaps": gaps,
        "unknown": ["Semantic diagnosis is not implemented", "Scope does not stop the pipeline",
                    "Formal node schema validation is not_checked unless an actual check event is recorded",
                    "Wait reasons and human identity remain unknown when no runtime fact is recorded"],
        "history_window_limited": len(rows) == 500,
    })
    return panel_safe(result)


@router.post("/{execution_id}/pin")
async def pin_observation(project_id: int, execution_id: str):
    observer = get_observer()
    async with observer.master() as session:
        await _scope(session, project_id, execution_id)
    observer.pin(execution_id)
    return {"pinned": True}
