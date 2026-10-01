from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.telemetry import (
    budget_summary,
    decision_summary,
    incident_summary,
    node_request_latency,
)


@dataclass
class FakeIncident:
    type: str
    status: str
    severity: str = "error"
    details: dict = field(default_factory=dict)


@dataclass
class FakeDecisionRecord:
    status: str
    model_steps: int = 0


@dataclass
class FakeEvent:
    id: int
    kind: str
    node_key: str | None
    request_id: str | None
    created_at: datetime


@dataclass
class FakeControl:
    request_limit: int
    requests_used: int
    held: bool = False
    enabled: bool = True


def test_incident_summary_counts_by_type_status_severity():
    incidents = [
        FakeIncident("invalid_json", "resolved_confirmed", details={"blocking": True}),
        FakeIncident("invalid_json", "human_review", details={"blocking": True}),
        FakeIncident("api_error", "human_review", severity="error", details={"blocking": True}),
        FakeIncident("runtime_wait", "open", severity="info", details={"blocking": False}),
    ]
    summary = incident_summary(incidents)
    assert summary["total"] == 4
    assert summary["by_type"] == {"invalid_json": 2, "api_error": 1, "runtime_wait": 1}
    assert summary["resolved_confirmed"] == 1
    # blocking_open: not resolved AND blocking=True -> invalid_json/human_review + api_error/human_review
    assert summary["blocking_open"] == 2


def test_incident_summary_empty():
    summary = incident_summary([])
    assert summary == {"total": 0, "by_type": {}, "by_status": {}, "by_severity": {},
        "resolved_confirmed": 0, "blocking_open": 0}


def test_decision_summary_human_review_rate_and_model_steps():
    records = [
        FakeDecisionRecord("done", model_steps=1),
        FakeDecisionRecord("human_review", model_steps=3),
        FakeDecisionRecord("ready", model_steps=0),  # not terminal, excluded from rate/steps
    ]
    summary = decision_summary(records)
    assert summary["total"] == 3
    assert summary["terminal_count"] == 2
    assert summary["human_review_rate"] == 0.5
    assert summary["avg_model_steps"] == 2.0
    assert summary["max_model_steps"] == 3


def test_decision_summary_no_terminal_records_has_none_rate():
    summary = decision_summary([FakeDecisionRecord("ready", model_steps=0)])
    assert summary["terminal_count"] == 0
    assert summary["human_review_rate"] is None
    assert summary["avg_model_steps"] is None


def test_node_request_latency_pairs_start_and_end_by_request_id():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    events = [
        FakeEvent(1, "model_start", "n1", "req-1", t0),
        FakeEvent(2, "model_end", "n1", "req-1", t0 + timedelta(seconds=5)),
        FakeEvent(3, "model_start", "n1", "req-2", t0),
        FakeEvent(4, "model_end", "n1", "req-2", t0 + timedelta(seconds=15)),
        FakeEvent(5, "model_start", "n2", "req-3", t0),
        # req-3 never completes -> unmatched
    ]
    result = node_request_latency(events)
    assert result["by_node"]["n1"]["count"] == 2
    assert result["by_node"]["n1"]["avg_seconds"] == 10.0
    assert result["by_node"]["n1"]["min_seconds"] == 5.0
    assert result["by_node"]["n1"]["max_seconds"] == 15.0
    assert "n2" not in result["by_node"]
    assert len(result["unmatched_requests"]) == 1
    assert result["unmatched_requests"][0] == {
        "node_key": "n2", "request_id": "req-3", "started_at": t0.isoformat()}


def test_node_request_latency_ignores_events_without_request_id():
    t0 = datetime(2026, 1, 1, 12, 0, 0)
    events = [FakeEvent(1, "model_start", "n1", None, t0), FakeEvent(2, "node_start", "n1", None, t0)]
    result = node_request_latency(events)
    assert result["by_node"] == {}
    assert result["unmatched_requests"] == []


def test_budget_summary_computes_remaining_and_utilization():
    summary = budget_summary(FakeControl(request_limit=20, requests_used=5, held=True))
    assert summary == {"request_limit": 20, "requests_used": 5, "remaining": 15,
        "utilization": 0.25, "held": True, "enabled": True}


def test_budget_summary_missing_control():
    summary = budget_summary(None)
    assert summary["request_limit"] is None
    assert summary["utilization"] is None
