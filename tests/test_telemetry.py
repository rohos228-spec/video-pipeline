import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from app.services.telemetry import (
    budget_summary,
    cost_source_label_ru,
    cost_summary,
    decision_summary,
    incident_summary,
    node_request_latency,
    project_cost_summary,
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
    assert summary["blocking_open"] == 2


def test_incident_summary_empty():
    summary = incident_summary([])
    assert summary == {"total": 0, "by_type": {}, "by_status": {}, "by_severity": {},
        "resolved_confirmed": 0, "blocking_open": 0}


def test_decision_summary_human_review_rate_and_model_steps():
    records = [
        FakeDecisionRecord("done", model_steps=1),
        FakeDecisionRecord("human_review", model_steps=3),
        FakeDecisionRecord("ready", model_steps=0),
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


def _row(**kw):
    base = {"model": "gpt-5-6-sol", "cost_usd": 0.0, "prompt_tokens": 0,
        "completion_tokens": 0, "cached_tokens": 0, "status_code": 200}
    base.update(kw)
    return base


def test_cost_summary_sums_pre_priced_cost_without_recomputing():
    rows = [
        _row(model="gpt-5-6-sol", cost_usd=0.0012, prompt_tokens=100, completion_tokens=40),
        _row(model="gpt-5-6-sol", cost_usd=0.0008, prompt_tokens=50, completion_tokens=20),
        _row(model="gpt-5-6-terra", cost_usd=0.002, prompt_tokens=200, completion_tokens=80, cached_tokens=30),
    ]
    summary = cost_summary(rows)
    assert summary["total_calls"] == 3
    assert summary["failed_calls"] == 0
    assert summary["total_cost_usd"] == pytest.approx(0.004)
    assert summary["total_prompt_tokens"] == 350
    assert summary["total_completion_tokens"] == 140
    assert summary["total_cached_tokens"] == 30
    assert summary["by_model"]["gpt-5-6-sol"]["calls"] == 2
    assert summary["by_model"]["gpt-5-6-sol"]["cost_usd"] == pytest.approx(0.002)
    assert summary["by_model"]["gpt-5-6-terra"]["calls"] == 1
    assert summary["source_raw"] == "api_tracker_hook (pre-priced at write time)"
    assert summary["source"] == cost_source_label_ru(summary["source_raw"]) != summary["source_raw"]
    assert "цена посчитана при записи" in summary["source"]


def test_cost_source_labels_are_russian_and_the_raw_code_is_kept():
    from app.services.telemetry import COST_SOURCES, spend_source_label_ru

    for raw in (*COST_SOURCES.values(), "api_tracker_hook (pre-priced at write time)"):
        assert any("а" <= ch.lower() <= "я" for ch in cost_source_label_ru(raw)), raw
    assert cost_source_label_ru("something new") == "something new"
    assert cost_source_label_ru(None) == ""
    assert spend_source_label_ru("media_native") == "платный вызов медиа-провайдера"
    assert spend_source_label_ru("odd") == "odd"
    summary = cost_summary([_row(model="m", provider="kie")])
    assert summary["sources_raw"] == {"kie": COST_SOURCES["kie"]}
    assert summary["sources"]["kie"] == cost_source_label_ru(COST_SOURCES["kie"])
    assert "Kie" in summary["sources"]["kie"] and summary["sources"]["kie"] != summary["sources_raw"]["kie"]


def test_spend_refusal_dict_carries_a_russian_source_next_to_the_code():
    from app.services.spend_guard import SpendCapExceeded

    d = SpendCapExceeded("spend_cap_daily", 1.0, 2.0, 0.5, "planner").as_dict()
    assert d["source"] == "planner" and d["source_ru"] == "планировщик команд"


def test_cost_summary_counts_failed_calls():
    rows = [_row(status_code=200), _row(status_code=429), _row(status_code=0)]
    summary = cost_summary(rows)
    assert summary["failed_calls"] == 2


def test_cost_summary_empty():
    summary = cost_summary([])
    assert summary["total_calls"] == 0
    assert summary["total_cost_usd"] == 0.0
    assert summary["by_model"] == {}


async def test_project_cost_summary_filters_by_project_and_since(tmp_path, monkeypatch):
    db_path = tmp_path / "tracker.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("""CREATE TABLE api_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            provider TEXT, model TEXT, call_type TEXT,
            prompt_tokens INTEGER DEFAULT 0, completion_tokens INTEGER DEFAULT 0,
            cached_tokens INTEGER DEFAULT 0, total_tokens INTEGER DEFAULT 0,
            duration_sec REAL DEFAULT 0.0, cost_usd REAL DEFAULT 0.0,
            status_code INTEGER DEFAULT 200, project_source TEXT DEFAULT ''
        )""")
        old = (datetime.utcnow() - timedelta(days=2)).isoformat()
        new = datetime.utcnow().isoformat()
        conn.execute("INSERT INTO api_calls (timestamp, model, cost_usd, project_source) "
            "VALUES (?, 'gpt-5-6-sol', 0.001, '7')", (new,))
        conn.execute("INSERT INTO api_calls (timestamp, model, cost_usd, project_source) "
            "VALUES (?, 'gpt-5-6-sol', 0.001, '7')", (old,))
        conn.execute("INSERT INTO api_calls (timestamp, model, cost_usd, project_source) "
            "VALUES (?, 'gpt-5-6-sol', 0.001, '8')", (new,))
        conn.commit()
    import app.services.api_tracker_hook as hook
    monkeypatch.setattr(hook, "_TRACKER_DB", db_path)

    unbounded = await project_cost_summary(7, since=None)
    assert unbounded["total_calls"] == 2

    windowed = await project_cost_summary(7, since=datetime.utcnow() - timedelta(hours=1))
    assert windowed["total_calls"] == 1

    other_project = await project_cost_summary(8, since=None)
    assert other_project["total_calls"] == 1


async def test_project_cost_summary_missing_db_file_returns_empty(tmp_path, monkeypatch):
    import app.services.api_tracker_hook as hook
    monkeypatch.setattr(hook, "_TRACKER_DB", tmp_path / "does-not-exist.db")
    summary = await project_cost_summary(1, since=None)
    assert summary["total_calls"] == 0
    assert summary["total_cost_usd"] == 0.0
