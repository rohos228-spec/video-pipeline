"""Read-only aggregation over the existing Observe/Diagnose/Decision/Actions
journal. This is NOT a new tracing/metrics pipeline — every number here is
derived from rows app/services/observation.py, diagnosis.py, decision_v1.py
and actions_v1.py already write for their own reasons. This module only
summarizes what is already durably recorded; it never writes anything and
never changes what gets recorded.

Aggregation is split into pure functions that take already-fetched rows
(trivially unit-testable, no DB) plus thin async query wrappers that fetch
those rows and call them -- the same pattern app/services/human_review_summary.py
uses.
"""
from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence


def incident_summary(incidents: Sequence[Any]) -> dict[str, Any]:
    by_type = Counter(i.type for i in incidents)
    by_status = Counter(i.status for i in incidents)
    by_severity = Counter(i.severity for i in incidents)
    resolved = sum(1 for i in incidents if i.status == "resolved_confirmed")
    blocking_open = sum(1 for i in incidents
        if i.status != "resolved_confirmed" and i.details.get("blocking", True))
    return {
        "total": len(incidents),
        "by_type": dict(by_type),
        "by_status": dict(by_status),
        "by_severity": dict(by_severity),
        "resolved_confirmed": resolved,
        "blocking_open": blocking_open,
    }


def decision_summary(records: Sequence[Any]) -> dict[str, Any]:
    by_status = Counter(r.status for r in records)
    terminal = [r for r in records if r.status in {"done", "human_review"}]
    model_steps = [r.model_steps for r in terminal]
    human_review_count = by_status.get("human_review", 0)
    return {
        "total": len(records),
        "by_status": dict(by_status),
        "terminal_count": len(terminal),
        "human_review_rate": (human_review_count / len(terminal)) if terminal else None,
        "avg_model_steps": (sum(model_steps) / len(model_steps)) if model_steps else None,
        "max_model_steps": max(model_steps) if model_steps else None,
    }


def node_request_latency(events: Sequence[Any]) -> dict[str, Any]:
    """Pairs model_start/model_end events by (node_key, request_id) and
    reports per-node latency stats. A model_start with no matching
    model_end is reported under `unmatched_requests` rather than silently
    dropped -- an unmatched request is operationally significant (stuck or
    uncertain), not merely missing data; hiding it would understate risk.
    """
    pending: dict[tuple[str | None, str], datetime] = {}
    latencies_by_node: dict[str, list[float]] = {}
    for event in sorted(events, key=lambda e: e.id):
        if event.kind == "model_start" and event.request_id:
            pending[(event.node_key, event.request_id)] = event.created_at
        elif event.kind == "model_end" and event.request_id:
            key = (event.node_key, event.request_id)
            started_at = pending.pop(key, None)
            if started_at is None:
                continue
            latency = (event.created_at - started_at).total_seconds()
            latencies_by_node.setdefault(event.node_key or "unknown", []).append(latency)
    by_node = {}
    for node_key, values in latencies_by_node.items():
        by_node[node_key] = {
            "count": len(values),
            "avg_seconds": sum(values) / len(values),
            "min_seconds": min(values),
            "max_seconds": max(values),
        }
    unmatched_requests = [
        {"node_key": node_key, "request_id": request_id, "started_at": started_at.isoformat()}
        for (node_key, request_id), started_at in pending.items()
    ]
    return {"by_node": by_node, "unmatched_requests": unmatched_requests}


# How each provider's figures were obtained; shown next to the totals so a Vibecode
# estimate is never read as calibrated like Kie's.
COST_SOURCES = {
    "kie": "rates calibrated against Kie credit balance (2026-10-01)",
    "vibecode": "catalog list price from vibecode_models_snapshot.json (snapshot date unknown); "
                "NOT calibrated against the vibecode.moe balance",
}


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _provider_key(row: Any) -> str:
    raw = str(_field(row, "provider") or "").strip().lower()
    return "kie" if raw in {"kie", "kie.ai"} else (raw or "unknown")


def _cost_unknown(row: Any) -> bool:
    """A successful call whose cost the tracker flagged as unknown (e.g. no usage returned)."""
    meta = _field(row, "metadata_json")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return False
    return isinstance(meta, dict) and meta.get("cost_basis") == "unknown"


def cost_summary(rows: Sequence[Any]) -> dict[str, Any]:
    """rows: api-tracker `api_calls` records (sqlite3.Row or dict-like, one
    per tracked LLM call). Costs are never recomputed here --
    app/services/api_tracker_hook.py already priced each call at write time
    (its own rate table owns that); this only sums what's already stored,
    per docs/ARCHITECTURE.md's "no dollar-cost model invented here" stance
    carried forward to the one place a real tariff *is* known.
    """
    total_cost = 0.0
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_cached_tokens = 0
    failed_calls = 0
    by_model: dict[str, dict[str, Any]] = {}
    by_provider: dict[str, dict[str, Any]] = {}
    unknown_cost_calls = 0
    for row in rows:
        model = row["model"] or "unknown"
        cost = float(row["cost_usd"] or 0.0)
        prompt = int(row["prompt_tokens"] or 0)
        completion = int(row["completion_tokens"] or 0)
        cached = int(row["cached_tokens"] or 0)
        total_cost += cost
        total_prompt_tokens += prompt
        total_completion_tokens += completion
        total_cached_tokens += cached
        status = row["status_code"]
        status = int(status) if status is not None else 200
        failed = status >= 400 or status == 0
        if failed:
            failed_calls += 1
        provider = by_provider.setdefault(_provider_key(row),
            {"calls": 0, "cost_usd": 0.0, "unknown_cost_calls": 0})
        provider["calls"] += 1
        provider["cost_usd"] += cost
        if not failed and _cost_unknown(row):
            provider["unknown_cost_calls"] += 1
            unknown_cost_calls += 1
        slot = by_model.setdefault(model, {"calls": 0, "cost_usd": 0.0,
            "prompt_tokens": 0, "completion_tokens": 0})
        slot["calls"] += 1
        slot["cost_usd"] += cost
        slot["prompt_tokens"] += prompt
        slot["completion_tokens"] += completion
    for slot in (*by_model.values(), *by_provider.values()):
        slot["cost_usd"] = round(slot["cost_usd"], 6)
    return {
        "total_calls": len(rows),
        "failed_calls": failed_calls,
        "total_cost_usd": round(total_cost, 6),
        "total_prompt_tokens": total_prompt_tokens,
        "total_completion_tokens": total_completion_tokens,
        "total_cached_tokens": total_cached_tokens,
        "by_model": by_model,
        "by_provider": by_provider,
        "unknown_cost_calls": unknown_cost_calls,
        "sources": {k: v for k, v in COST_SOURCES.items() if k in by_provider},
        "source": "api_tracker_hook (pre-priced at write time)",
    }


def _query_cost_rows_sync(project_id: int, since: datetime | None) -> list[dict[str, Any]]:
    """Synchronous sqlite3 read of api-tracker's own DB -- deliberately not
    the SQLAlchemy session the rest of this module uses, since api-tracker
    is a separate, pre-existing local tool (app/services/api_tracker_hook.py)
    with its own SQLite file, not part of the Observe schema. Called via
    asyncio.to_thread so it never blocks the event loop.
    """
    import sqlite3

    from app.services.api_tracker_hook import _TRACKER_DB

    if not _TRACKER_DB.is_file():
        return []
    tail = " FROM api_calls WHERE project_source = ?"
    params: list[Any] = [str(project_id)]
    if since is not None:
        tail += " AND timestamp >= ?"
        params.append(since.isoformat())
    base = "SELECT model, cost_usd, prompt_tokens, completion_tokens, cached_tokens, status_code"
    # provider / metadata_json first; an older tracker DB without those columns still reads.
    for columns in (", provider, metadata_json", ""):
        try:
            with sqlite3.connect(str(_TRACKER_DB), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(base + columns + tail, params).fetchall()]
        except sqlite3.OperationalError:
            continue
    return []  # Table not created yet (no call tracked for any project so far).


async def project_cost_summary(project_id: int, *, since: datetime | None = None) -> dict[str, Any]:
    import asyncio
    rows = await asyncio.to_thread(_query_cost_rows_sync, project_id, since)
    return cost_summary(rows)


def budget_summary(control: Any) -> dict[str, Any]:
    if control is None:
        return {"request_limit": None, "requests_used": None, "remaining": None,
            "utilization": None, "held": None, "enabled": None}
    remaining = control.request_limit - control.requests_used
    utilization = (control.requests_used / control.request_limit) if control.request_limit else None
    return {
        "request_limit": control.request_limit,
        "requests_used": control.requests_used,
        "remaining": remaining,
        "utilization": utilization,
        "held": control.held,
        "enabled": control.enabled,
    }


async def project_telemetry(session, project_id: int, *, since: datetime | None = None) -> dict[str, Any]:
    """Project-wide summary across every execution this project has ever
    had -- deliberately not scoped to one execution_id, unlike the rest of
    app/web/routers/observations.py's endpoints, since telemetry is only
    useful aggregated over time.
    """
    from sqlalchemy import select

    from app.services.actions_v1 import ActionExecutionControl
    from app.services.decision_state import DecisionRecord
    from app.services.diagnosis import Incident
    from app.services.observation import ObservationEvent

    incident_query = select(Incident).where(Incident.project_id == project_id)
    if since is not None:
        incident_query = incident_query.where(Incident.detected_at >= since)
    incidents = (await session.scalars(incident_query)).all()

    decision_query = select(DecisionRecord).where(DecisionRecord.project_id == project_id)
    if since is not None:
        decision_query = decision_query.where(DecisionRecord.created_at >= since)
    decisions = (await session.scalars(decision_query)).all()

    event_query = select(ObservationEvent).where(
        ObservationEvent.project_id == project_id,
        ObservationEvent.kind.in_(("model_start", "model_end")))
    if since is not None:
        event_query = event_query.where(ObservationEvent.created_at >= since)
    events = (await session.scalars(event_query)).all()

    controls = (await session.scalars(select(ActionExecutionControl).where(
        ActionExecutionControl.project_id == project_id))).all()

    return {
        "project_id": project_id,
        "since": since.isoformat() if since else None,
        "incidents": incident_summary(incidents),
        "decisions": decision_summary(decisions),
        "node_latency": node_request_latency(events),
        "budgets_by_execution": {c.execution_id: budget_summary(c) for c in controls},
        "cost": await project_cost_summary(project_id, since=since),
    }
