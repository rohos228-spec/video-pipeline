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


COST_SOURCES = {
    "kie": "rates calibrated against Kie credit balance (2026-10-01)",
    "vibecode": "catalog list price from vibecode_models_snapshot.json (snapshot date unknown); "
                "NOT calibrated against the vibecode.moe balance",
}


COST_SOURCE_PRICED_AT_WRITE = "api_tracker_hook (pre-priced at write time)"
COST_SOURCE_LABELS_RU = {
    COST_SOURCE_PRICED_AT_WRITE: "сумма из журнала запросов: цена посчитана при записи каждого вызова",
    COST_SOURCES["kie"]: "тарифы откалиброваны по списанию кредитов Kie (2026-10-01)",
    COST_SOURCES["vibecode"]: ("цена из снимка каталога vibecode_models_snapshot.json (дата снимка неизвестна); "
                               "не откалибрована по балансу vibecode.moe"),
}
SPEND_SOURCE_LABELS_RU = {
    "planner": "планировщик команд",
    "action_layer": "слой действий",
    "media_native": "платный вызов медиа-провайдера",
    "media_402": "провайдер ответил «нет средств» (402)",
    "credit_error": "ошибка кредитов провайдера",
    "spend_guard": "защита от перерасхода",
}


def cost_source_label_ru(raw: str | None) -> str:
    text = str(raw or "")
    return COST_SOURCE_LABELS_RU.get(text, text)


def spend_source_label_ru(raw: str | None) -> str:
    text = str(raw or "")
    return SPEND_SOURCE_LABELS_RU.get(text, text)


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError):
        return None


def _provider_key(row: Any) -> str:
    raw = str(_field(row, "provider") or "").strip().lower()
    return "kie" if raw in {"kie", "kie.ai"} else (raw or "unknown")


def _cost_unknown(row: Any) -> bool:
    meta = _field(row, "metadata_json")
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except ValueError:
            return False
    return isinstance(meta, dict) and meta.get("cost_basis") == "unknown"


def cost_summary(rows: Sequence[Any]) -> dict[str, Any]:
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
        "sources": {k: cost_source_label_ru(v) for k, v in COST_SOURCES.items() if k in by_provider},
        "sources_raw": {k: v for k, v in COST_SOURCES.items() if k in by_provider},
        "source": cost_source_label_ru(COST_SOURCE_PRICED_AT_WRITE),
        "source_raw": COST_SOURCE_PRICED_AT_WRITE,
    }


def _query_cost_rows_sync(project_id: int, since: datetime | None) -> list[dict[str, Any]]:
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
    for columns in (", provider, metadata_json", ""):
        try:
            with sqlite3.connect(str(_TRACKER_DB), timeout=5.0) as conn:
                conn.row_factory = sqlite3.Row
                return [dict(row) for row in conn.execute(base + columns + tail, params).fetchall()]
        except sqlite3.OperationalError:
            continue
    return []


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


def reconcile_summary(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {"available": False, "lines_ru": ["Сверка денег недоступна: журнал трат не прочитан."]}
    lines = [
        f"Оценено: ${report['estimated_usd']:.4f}; фактически: ${report['actual_usd']:.4f}; "
        f"всего в журнале трат: ${report['ledger_total_usd']:.4f}.",
        f"Открытые резервы: ${report['open_reserves_usd']:.4f} (запусков: {report['open_reserves_runs']}); "
        "это верхняя граница.",
        f"Журнал трат против трекера запросов: разница ${report['ledger_vs_tracker_gap_usd']:+.4f}"
        + (" (выше допуска)." if report["ledger_vs_tracker_flag"] else "."),
    ]
    if report["balance_reconciled"]:
        lines.append(f"Сверок с балансом провайдера: {report['reconciliations_total']}, "
                     f"с расхождением: {report['reconciliations_gap']}; "
                     f"открытых инцидентов расхождения: {report['open_mismatch_incidents']}.")
    else:
        lines.append("Сверка с балансом провайдера не выполнялась: отклонение неизвестно.")
    return {"available": True, "lines_ru": lines, **report}


async def project_telemetry(session, project_id: int, *, since: datetime | None = None) -> dict[str, Any]:
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

    try:
        from app.services import spend_reconcile
        reconcile = reconcile_summary(await spend_reconcile.project_report(session, project_id, since=since))
    except Exception:
        reconcile = reconcile_summary(None)

    return {
        "project_id": project_id,
        "since": since.isoformat() if since else None,
        "reconcile": reconcile,
        "incidents": incident_summary(incidents),
        "decisions": decision_summary(decisions),
        "node_latency": node_request_latency(events),
        "budgets_by_execution": {c.execution_id: budget_summary(c) for c in controls},
        "cost": await project_cost_summary(project_id, since=since),
    }
