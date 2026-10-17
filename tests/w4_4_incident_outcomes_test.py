
from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services import diagnosis
from app.services.diagnosis import (
    INCIDENT_OUTCOMES,
    OUTCOME_DEGRADED,
    OUTCOME_DIAGNOSE_ONLY,
    OUTCOME_ESCALATE,
    OUTCOME_RETRY_GATED,
    OUTCOME_WAIT,
    TITLES,
    derive_incidents,
    escalation_text_ru,
    notify_types,
)

SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["n1", "anim"],
                        observer_state="active", gaps=[])
_n = [5000]


def ev(kind, /, *, node="n1", attempt="a1", request=None, **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f"w44e{_n[0]}", execution_id="execution", project_id=2, node_key=node,
                attempt_id=attempt, request_id=request, kind=kind, payload=payload, db_scope="project",
                created_at="2026-10-07 12:00:00")


def api_failure(**error):
    base = dict(error_code="gpt_server", uncertain=False, provider="vibecode", status_code=503,
                error_type="GptApiError", catalog_source="structured_exception_context")
    return [ev("model_start", request="r1", provider="vibecode"),
            ev("model_error", request="r1", **{**base, **error})]



def test_every_one_of_the_16_incident_types_has_an_outcome() -> None:
    assert len(TITLES) == 16
    assert set(INCIDENT_OUTCOMES) == set(TITLES)
    kinds = {OUTCOME_RETRY_GATED, OUTCOME_ESCALATE, OUTCOME_DIAGNOSE_ONLY, OUTCOME_WAIT, OUTCOME_DEGRADED}
    for kind, outcome in INCIDENT_OUTCOMES.items():
        assert outcome["kind"] in kinds, kind
        assert isinstance(outcome["notify"], bool), kind
        assert outcome["paid_retry"] in {"never", "gated", "human_only"}, kind
        text = outcome["summary_ru"]
        assert any("а" <= ch.lower() <= "я" for ch in text), f"{kind}: текст исхода должен быть по-русски"


def test_no_outcome_promises_an_automatic_paid_retry() -> None:
    for kind, outcome in INCIDENT_OUTCOMES.items():
        assert outcome["paid_retry"] != "auto", kind
    for kind in ("stalled", "uncertain_request", "journal_gap", "db_disagreement", "user_stop", "runtime_wait"):
        assert INCIDENT_OUTCOMES[kind]["paid_retry"] == "never", kind
        assert INCIDENT_OUTCOMES[kind]["kind"] != OUTCOME_RETRY_GATED, kind


def test_stalled_is_diagnosis_plus_notification_only() -> None:
    outcome = INCIDENT_OUTCOMES["stalled"]
    assert outcome["kind"] == OUTCOME_ESCALATE and outcome["notify"] is True
    assert "не повторяется" in outcome["summary_ru"]
    assert outcome.get("recovery_class") is None


def test_the_derived_incident_carries_its_outcome() -> None:
    (item,) = derive_incidents(SCOPE, api_failure(), {})
    assert item["outcome"]["kind"] in {OUTCOME_RETRY_GATED, OUTCOME_ESCALATE}
    assert set(item["outcome"]) >= {"kind", "notify", "paid_retry", "summary_ru"}



def test_5xx_answer_is_the_only_api_error_eligible_for_a_gated_retry() -> None:
    (item,) = derive_incidents(SCOPE, api_failure(status_code=503), {})
    assert item["type"] == "api_error" and item["http_status"] == 503
    assert item["safe_retry"]["eligible"] is True
    assert item["outcome"]["kind"] == OUTCOME_RETRY_GATED and item["outcome"]["recovery_class"] == "transient_api"
    assert item["outcome"]["paid_retry"] == "gated"
    assert item["status"] == "human_review" and item["recommendation"]["automatic_action"] is False


@pytest.mark.parametrize("status", [400, 401, 402, 404, 429])
def test_non_5xx_api_errors_are_escalated_not_retried(status) -> None:
    (item,) = derive_incidents(SCOPE, api_failure(status_code=status), {})
    assert item["type"] == "api_error"
    assert item["safe_retry"]["eligible"] is False
    assert item["outcome"]["kind"] == OUTCOME_ESCALATE and item["outcome"]["recovery_class"] is None


def test_api_error_without_a_recorded_status_is_not_proven_safe() -> None:
    (item,) = derive_incidents(SCOPE, api_failure(status_code=None), {})
    assert item["safe_retry"]["eligible"] is False and "не записан" in item["safe_retry"]["reason"]


def test_a_timeout_is_an_uncertain_request_and_never_eligible() -> None:
    rows = [ev("model_start", request="r1"),
            ev("model_error", request="r1", error_code="gpt_timeout", uncertain=True, error_type="ReadTimeout",
               status_code=None, catalog_source="exception_type_or_text")]
    (item,) = derive_incidents(SCOPE, rows, {})
    assert item["type"] == "uncertain_request"
    assert item["safe_retry"]["eligible"] is False
    assert item["outcome"]["kind"] == OUTCOME_ESCALATE and item["outcome"]["paid_retry"] == "never"
    assert item["outcome"]["recovery_class"] is None


def test_a_connect_timeout_with_a_5xx_looking_status_is_still_uncertain() -> None:
    rows = [ev("model_start", request="r1"),
            ev("model_error", request="r1", error_code="gpt_timeout", uncertain=True, error_type="ConnectTimeout",
               status_code=503)]
    (item,) = derive_incidents(SCOPE, rows, {})
    assert item["type"] == "uncertain_request" and item["safe_retry"]["eligible"] is False



def test_a_failed_local_check_is_a_local_fix_candidate_but_never_retried_as_is() -> None:
    rows = [ev("model_start", request="r1"), ev("model_end", request="r1", complete=True),
            ev("runtime_check", request="r1", check="coverage", performed=True, status="failed",
               failure_class="contract_rejected")]
    (item,) = derive_incidents(SCOPE, rows, {})
    assert item["type"] == "contract_rejected" and item["local_fix_candidate"] is True
    assert item["outcome"]["kind"] == OUTCOME_RETRY_GATED and item["outcome"]["recovery_class"] == "local_fix"
    assert "правк" in item["outcome"]["summary_ru"]


def test_an_unknown_cause_contract_rejected_is_not_a_candidate() -> None:
    rows = [ev("model_start", request="r1"), ev("model_end", request="r1", complete=True),
            ev("apply_error", request="r1")]
    (item,) = derive_incidents(SCOPE, rows, {})
    assert item["type"] == "contract_rejected"
    assert not item.get("local_fix_candidate")
    assert item["outcome"]["kind"] == OUTCOME_ESCALATE and item["outcome"]["recovery_class"] is None



def animation_check(status, **extra):
    base = dict(node_type="animation_prompts", status=status, errors=[], reason=None, unknown_checks=[])
    return ev("node_result_verification", node="anim", **{**base, **extra})


def test_failed_animation_result_check_is_a_blocking_incident_of_its_own() -> None:
    row = animation_check("failed", errors=["missing_or_unusable_primary_animation:f1"],
                          reason="missing_or_unusable_primary_animation:f1")
    (item,) = derive_incidents(SCOPE, [row], {})
    assert item["type"] == "verification_hold" and item["blocking"] is True and item["severity"] == "error"
    assert item["status"] == "human_review" and "missing_or_unusable_primary_animation" in item["fact"]
    assert "Автоповтор не выполняется" in item["fact"]
    assert item["outcome"]["kind"] == OUTCOME_ESCALATE and item["outcome"]["paid_retry"] == "never"


def test_unknown_animation_result_check_is_a_non_blocking_warning() -> None:
    row = animation_check("unknown", reason="captured input unavailable or incomplete")
    (item,) = derive_incidents(SCOPE, [row], {})
    assert item["type"] == "verification_hold" and item["blocking"] is False and item["severity"] == "warning"


def test_passed_animation_result_check_raises_nothing() -> None:
    assert derive_incidents(SCOPE, [animation_check("passed")], {}) == []


def test_animation_degradation_is_success_with_degradation_by_policy() -> None:
    from app.services.native_animation_contracts import ANIMATION_DEGRADATION_POLICY, animation_prompts_contract

    row = ev("animation_degraded", node="anim", contract="native_animation_prompts", reason="local_fallback",
             shot=1, frame_numbers=[1, 2])
    (item,) = derive_incidents(SCOPE, [row], {})
    assert item["type"] == "animation_degraded" and item["policy"] == "success_with_degradation"
    assert item["outcome"]["kind"] == OUTCOME_DEGRADED and item["outcome"]["paid_retry"] == "never"
    assert item["blocking"] is False
    assert ANIMATION_DEGRADATION_POLICY["step_status_unchanged"] is True
    assert "успех с деградацией" in ANIMATION_DEGRADATION_POLICY["operator_text_ru"]
    assert animation_prompts_contract()["degradation_facts"]["policy"] is ANIMATION_DEGRADATION_POLICY



def test_default_notify_types_are_the_ones_marked_in_the_table(monkeypatch) -> None:
    monkeypatch.delenv(diagnosis.NOTIFY_ENV, raising=False)
    types = notify_types()
    assert {"stalled", "uncertain_request", "api_error", "contract_rejected", "media_failure"} <= types
    assert not ({"runtime_wait", "user_stop", "journal_gap", "animation_degraded"} & types)
    monkeypatch.setenv(diagnosis.NOTIFY_ENV, "none")
    assert notify_types() == set()
    monkeypatch.setenv(diagnosis.NOTIFY_ENV, "stalled, bogus ,api_error")
    assert notify_types() == {"stalled", "api_error"}


def test_escalation_text_is_russian_and_promises_no_automatic_paid_retry() -> None:
    (item,) = derive_incidents(SCOPE, api_failure(), {})
    text = escalation_text_ru({**item, "type": "api_error"}, project_id=2, node_title="План")
    assert "Нужно решение человека" in text and "Проект 2" in text and "«План»" in text
    assert "Повторный платный запрос сам не выполняется" in text
    assert len(text) <= 1800


class _Observer:

    def __init__(self, factory):
        self.factory = factory
        self.failures = {}
        self.commit_uncertainty = {}

    @asynccontextmanager
    async def master(self):
        async with self.factory() as session:
            yield session


@pytest.fixture
async def world(monkeypatch):
    from app.services import actions_v1

    monkeypatch.delenv(diagnosis.NOTIFY_ENV, raising=False)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    observer = _Observer(factory)
    yield SimpleNamespace(observer=observer, factory=factory, diagnoser=diagnosis.Diagnoser(observer))
    diagnosis.set_escalation_notifier(None)
    await engine.dispose()


async def _add_incident(world, kind="api_error", status="human_review", incident_id="inc-1", **details):
    async with world.factory() as s:
        s.add(diagnosis.Incident(incident_id=incident_id, execution_id="execution", project_id=2, node_key="n1",
                                 attempt_id="a1", type=kind, severity="error", status=status,
                                 details={"currently_relevant": True, "fact": "Шлюз ответил 503", "title": TITLES[kind],
                                          "outcome": INCIDENT_OUTCOMES[kind], **details}))
        await s.commit()


async def _records(world):
    from app.services.actions_v1 import ActionRecord

    async with world.factory() as s:
        return (await s.scalars(select(ActionRecord).where(ActionRecord.action_kind == "incident_notify"))).all()


@pytest.mark.asyncio
async def test_a_human_review_incident_is_announced_exactly_once(world) -> None:
    sent = []

    async def notifier(project_id, text, url):
        sent.append((project_id, text, url))
        return True

    diagnosis.set_escalation_notifier(notifier)
    await _add_incident(world)
    assert await world.diagnoser.escalate("execution") == 1
    assert await world.diagnoser.escalate("execution") == 0
    assert await world.diagnoser.escalate("execution") == 0
    assert len(sent) == 1 and sent[0][0] == 2 and "Нужно решение человека" in sent[0][1]
    (record,) = await _records(world)
    assert record.status == "notified" and record.idempotency_key == "incident-notify:inc-1"
    assert record.verification["business_action"] is False and record.preconditions["business_action"] is False


@pytest.mark.asyncio
async def test_a_failed_delivery_is_retried_at_most_three_times(world) -> None:
    calls = []

    async def broken(project_id, text, url):
        calls.append(1)
        raise ConnectionError("telegram down")

    diagnosis.set_escalation_notifier(broken)
    await _add_incident(world)
    results = [await world.diagnoser.escalate("execution") for _ in range(6)]
    assert results == [0] * 6 and len(calls) == 3
    (record,) = await _records(world)
    assert record.status == "notify_failed" and record.preconditions["tries"] == 3
    assert record.preconditions["last_error"] == "ConnectionError"


@pytest.mark.asyncio
async def test_a_notifier_that_returns_false_is_recorded_as_not_delivered(world) -> None:
    async def refuses(project_id, text, url):
        return False

    diagnosis.set_escalation_notifier(refuses)
    await _add_incident(world)
    assert await world.diagnoser.escalate("execution") == 0
    (record,) = await _records(world)
    assert record.status == "notify_failed" and record.verification["delivered"] is False


@pytest.mark.asyncio
async def test_only_types_that_need_a_person_are_announced(world) -> None:
    sent = []

    async def notifier(project_id, text, url):
        sent.append(text)
        return True

    diagnosis.set_escalation_notifier(notifier)
    await _add_incident(world, "animation_degraded", incident_id="i-anim")
    await _add_incident(world, "runtime_wait", incident_id="i-wait")
    await _add_incident(world, "journal_gap", incident_id="i-gap")
    await _add_incident(world, "stalled", incident_id="i-stalled")
    await _add_incident(world, "api_error", status="resolved_confirmed", incident_id="i-done")
    await _add_incident(world, "api_error", incident_id="i-old", historical_attempt=True, currently_relevant=False)
    assert await world.diagnoser.escalate("execution") == 1
    assert len(sent) == 1 and "Запрос или выполнение зависли" in sent[0]
    assert {r.preconditions["incident_type"] for r in await _records(world)} == {"stalled"}


@pytest.mark.asyncio
async def test_no_notifier_and_no_telegram_means_no_rows_at_all(world, monkeypatch) -> None:
    from app.settings import settings

    monkeypatch.setattr(settings, "telegram_enabled", False)
    diagnosis.set_escalation_notifier(None)
    await _add_incident(world)
    assert await world.diagnoser.escalate("execution") == 0
    assert await _records(world) == []


@pytest.mark.asyncio
async def test_switching_the_types_off_stops_every_announcement(world, monkeypatch) -> None:
    async def notifier(project_id, text, url):
        raise AssertionError("must not be called")

    diagnosis.set_escalation_notifier(notifier)
    monkeypatch.setenv(diagnosis.NOTIFY_ENV, "none")
    await _add_incident(world)
    assert await world.diagnoser.escalate("execution") == 0
    assert await _records(world) == []


async def _seed_run(world, *, status_code=503):
    from datetime import datetime

    from app.services.observation import ExecutionControl, ObservationEvent

    async with world.factory() as s:
        s.add(ExecutionControl(execution_id="execution", project_id=2, workflow_run_id=11, node_keys=["n1"],
                               graph_version="g", mode="observe", observer_state="active", cursors={}, gaps=[]))
        rows = [("node_start", {}, None), ("model_start", {"provider": "vibecode"}, "r1"),
                ("model_error", {"error_code": "gpt_server", "uncertain": False, "status_code": status_code,
                                 "error_type": "GptApiError", "catalog_source": "structured_exception_context"}, "r1"),
                ("node_error", {}, None)]
        for idx, (kind, payload, request) in enumerate(rows, 1):
            s.add(ObservationEvent(id=idx, event_id=f"seed-{idx}", execution_id="execution", project_id=2,
                                   workflow_run_id=11, node_key="n1", attempt_id="a1", request_id=request,
                                   db_scope="project", kind=kind, payload=payload, created_at=datetime.utcnow()))
        await s.commit()


SNAPSHOT = {"nodes": {"n1": {"status": "failed"}}, "pending_approvals": {}, "user_stop": False,
            "disagreement": False, "snapshot_evidence": {}, "gaps": [], "titles": {"n1": "Сценарий"}}


@pytest.mark.asyncio
async def test_update_derives_the_incident_and_announces_it_once_across_passes(world) -> None:
    sent = []

    async def notifier(project_id, text, url):
        sent.append(text)
        return True

    diagnosis.set_escalation_notifier(notifier)
    await _seed_run(world)
    for _ in range(3):
        await world.diagnoser.update("execution", snapshot=dict(SNAPSHOT, gaps=[]))
    async with world.factory() as s:
        rows = (await s.scalars(select(diagnosis.Incident))).all()
    assert sorted(r.type for r in rows) == ["api_error", "contract_rejected"]
    row = next(r for r in rows if r.type == "api_error")
    assert row.status == "human_review"
    assert row.details["safe_retry"]["eligible"] is True and row.details["outcome"]["kind"] == OUTCOME_RETRY_GATED
    assert len(sent) == 1 and "«Сценарий»" in sent[0] and "Повторный платный запрос сам не выполняется" in sent[0]


@pytest.mark.asyncio
async def test_a_broken_escalation_never_breaks_the_diagnosis_pass(world, monkeypatch) -> None:
    async def failing_escalate(execution_id):
        raise RuntimeError("escalate itself failed")

    monkeypatch.setattr(world.diagnoser, "escalate", failing_escalate)
    await _seed_run(world)
    await world.diagnoser.update("execution", snapshot=dict(SNAPSHOT, gaps=[]))
    async with world.factory() as s:
        rows = (await s.scalars(select(diagnosis.Incident))).all()
        state = await s.get(diagnosis.DiagnosisState, "execution")
    assert "api_error" in {r.type for r in rows} and state.cursor == 4
    assert "execution" not in world.diagnoser.failures
