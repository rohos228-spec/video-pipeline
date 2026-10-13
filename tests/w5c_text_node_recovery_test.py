"""W5c: native text nodes recover through ONE incident and ONE Decision (plan, script, animation_prompts, sfx_plan,
sd_agent, sd_assemble).

Three defects found by the live runs on Vibecode, each pinned here:
  1. one failed attempt leaves several incidents; the generic contract_rejected / failed result check is only an ECHO
     of the primary invalid_json. Decision opened a second record for the echo, escalated it and held the run, so the
     recovery of the primary never started (diagnosis.bind_echo_incidents / decision_v1 begin+tick);
  2. the Decision started while the diagnosis was still rewriting the incident and the paid answer came back with
     "stale context version" -> human_review (decision_v1: diagnosis_settled gate and StaleContext resume);
  3. the model read the diagnosis label human_review and native_json_retry=false as a ban on retry_same
     (decision_v1: recovery_guidance in the context and in the system prompt).

The DB tests use the synthetic fixtures of tests/observe_v1; the stand tests start the whole application in a
SUBPROCESS in offline mode (stubs only, every non-loopback request refused), so run this file alone.
"""
import asyncio
import importlib
import json
import os
import subprocess
import sys
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests" / "observe_v1"))
sys.path.insert(0, str(ROOT / "tests"))
from _stand_workdir import make_workdir, remove_workdir  # noqa: E402


@pytest.fixture
def tmp_path():
    """Outside the repository: the e2e guard refuses a work folder inside live data."""
    path = make_workdir()
    try:
        yield path
    finally:
        remove_workdir(path)

from app.services.diagnosis import (  # noqa: E402
    bind_echo_incidents, derive_incidents, echo_primary, Incident)
from app.services import observation  # noqa: E402
import test_actions_v1_contract as actions_tests  # noqa: E402

SCOPE = SimpleNamespace(execution_id="execution", project_id=2, node_keys=["plan"], observer_state="active", gaps=[])
_n = [1000]


def ev(kind, /, *, node="plan", attempt="a1", request=None, **payload):
    _n[0] += 1
    return dict(id=_n[0], event_id=f"w5c{_n[0]}", execution_id="execution", project_id=2, node_key=node,
                attempt_id=attempt, request_id=request, kind=kind, payload=payload, db_scope="project",
                created_at="2026-10-08 12:00:00")


def failed_plan_attempt(*, check_request="r1", attempt="a1"):
    """What the native plan executor writes for an unparseable answer (shape taken from a stand journal)."""
    return [ev("node_start", attempt=attempt),
            ev("model_start", attempt=attempt, request="r1"), ev("model_end", attempt=attempt, request="r1", complete=True),
            ev("parse_result", attempt=attempt, request="r1", contract="native_project_text", payload_required=True,
               parsed=False, payload_present=False, schema_valid=False),
            ev("runtime_check", attempt=attempt, request=check_request, check="native_plan_text_requirement",
               performed=True, status="failed"),
            ev("node_error", attempt=attempt)]


@pytest.fixture(autouse=True)
def _synthetic_loopback_credentials(monkeypatch):
    from app.settings import settings
    monkeypatch.setattr(settings, "gpt_api_key", "synthetic-observe-test-key")
    monkeypatch.setattr(settings, "gpt_base_url", "http://127.0.0.1:18766")


# ---------------------------------------------------------------- 1. echo binding (pure)

def test_failed_result_check_of_the_same_request_is_an_echo_of_the_format_incident():
    out = {i["type"]: i for i in derive_incidents(SCOPE, failed_plan_attempt(), {})}
    assert set(out) == {"invalid_json", "contract_rejected"}
    echo, primary = out["contract_rejected"], out["invalid_json"]
    assert echo["echo_of"] == primary["incident_id"] and echo["echo_of_type"] == "invalid_json"
    assert "echo_of" not in primary


def test_a_check_of_another_request_is_not_an_echo():
    out = {i["type"]: i for i in derive_incidents(SCOPE, failed_plan_attempt(check_request="other"), {})}
    assert "echo_of" not in out["contract_rejected"]


def test_the_echo_never_crosses_attempts():
    rows = failed_plan_attempt(attempt="a1") + failed_plan_attempt(attempt="a2")
    for item in derive_incidents(SCOPE, rows, {}):
        if item["type"] == "contract_rejected":
            primary = next(i for i in derive_incidents(SCOPE, rows, {})
                           if i["incident_id"] == item["echo_of"])
            assert primary["attempt_id"] == item["attempt_id"]


def test_rule_rejections_and_observe_only_checks_are_never_echoes():
    base = dict(type="contract_rejected", node_key="n", attempt_id="a", incident_id="c", request_ids=["r"],
                local_fix_candidate=False, status="open")
    primary = dict(type="invalid_json", node_key="n", attempt_id="a", incident_id="p", request_ids=["r"], status="human_review")
    assert echo_primary(base, [base, primary]) is primary
    assert echo_primary({**base, "rule": "R1"}, [base, primary]) is None
    assert echo_primary({**base, "observe_only": True}, [base, primary]) is None
    assert echo_primary({**base, "attempt_id": None}, [base, primary]) is None
    assert echo_primary({**primary}, [base, primary]) is None            # a primary is never an echo
    assert echo_primary(base, [base, {**primary, "status": "resolved_confirmed"}]) is None


def test_a_genuine_local_fix_check_keeps_its_own_record():
    """A failed result check that is NOT the verdict on the primary's request stays a separate local-fix incident."""
    check = dict(type="contract_rejected", node_key="n", attempt_id="a", incident_id="c", request_ids=["rX"],
                 local_fix_candidate=True, status="open")
    primary = dict(type="invalid_json", node_key="n", attempt_id="a", incident_id="p", request_ids=["r"], status="human_review")
    items = [check, primary]
    bind_echo_incidents(items)
    assert "echo_of" not in check
    # without any primary there is nothing to bind to
    lone = dict(check)
    bind_echo_incidents([lone])
    assert "echo_of" not in lone


def test_the_generic_node_error_echo_binds_to_the_most_specific_primary():
    generic = dict(type="contract_rejected", node_key="n", attempt_id="a", incident_id="c", request_ids=[],
                   local_fix_candidate=False, status="human_review")
    hold = dict(type="verification_hold", node_key="n", attempt_id="a", incident_id="h", request_ids=[], status="human_review")
    fmt = dict(type="invalid_json", node_key="n", attempt_id="a", incident_id="p", request_ids=["r"], status="human_review")
    assert echo_primary(generic, [generic, hold, fmt]) is fmt
    assert echo_primary(generic, [generic, hold]) is hold


# ---------------------------------------------------------------- 2. Decision (database)

class DecisionOnOneIncident(actions_tests.DurableActions):
    fixture_node_type = "excel_gpt"

    async def asyncSetUp(self):
        self.service = importlib.import_module("app.services.decision_v1")
        await super().asyncSetUp()
        self.project_sessions = patch("app.project_db.project_db_session_scope", lambda _id: self.sessions())
        self.project_sessions.start()
        self.addCleanup(self.project_sessions.stop)
        await self.exhausted_fixture()
        self.loop = self.service.DecisionSupervisor(observation._observer)
        save_body = observation._observer.save_body
        self.body_fixture = patch.object(observation._observer, "save_body", side_effect=lambda eid, body:
            save_body("00000000-0000-0000-0000-000000000007" if eid == "exec-durable" else eid, body))
        self.body_fixture.start()
        self.addCleanup(self.body_fixture.stop)
        await self.loop.configure(project_id=7, execution_id="exec-durable", enabled=True,
                                  initiator="synthetic-operator", rationale="w5c")

    async def set_incident(self, **details):
        async with self.sessions() as s:
            row = await s.get(Incident, "source-incident")
            row.details = {**row.details, **details}
            await s.commit()

    async def records(self):
        async with self.sessions() as s:
            return (await s.scalars(select(self.service.DecisionRecord))).all()

    async def reply_with(self, action, *, mutate=None):
        from app.services.gpt_api import GptChatResult
        calls = []

        async def reply(**kwargs):
            context = json.loads(kwargs["prompt"])
            calls.append(context)
            if mutate is not None:
                await mutate(len(calls))
            p = dict(action=action, arguments={}, evidence_ids=context["evidence_ids"][:1],
                     context_version=context["context_version"], explanation="synthetic facts")
            return GptChatResult(text=json.dumps(p), model="synthetic", raw={"status": "completed"})
        return reply, calls

    # --- echo gets no record
    async def test_an_echo_incident_opens_no_decision(self):
        await self.set_incident(echo_of="primary-incident")
        self.assertIsNone(await self.loop.begin(project_id=7, execution_id="exec-durable",
                                                incident_id="source-incident", automatic=True))
        with self.assertRaisesRegex(ValueError, "echo"):
            await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")
        self.assertEqual(await self.records(), [])

    async def test_tick_skips_an_echo_and_opens_a_record_for_the_primary(self):
        await self.set_incident(echo_of="primary-incident")
        await self.loop.tick()
        self.assertEqual(await self.records(), [])
        async with self.sessions() as s:
            row = await s.get(Incident, "source-incident")
            row.details = {k: v for k, v in row.details.items() if k != "echo_of"}
            await s.commit()
        reply, calls = await self.reply_with("wait")
        with patch("app.services.gpt_api.chat", side_effect=reply):
            await self.loop.tick()
        self.assertEqual(len(await self.records()), 1)
        self.assertEqual(len(calls), 1)

    # --- diagnosis must be caught up before a paid decision starts
    async def test_no_decision_starts_while_the_diagnosis_lags_the_journal(self):
        from app.services.diagnosis import DiagnosisState
        async with self.sessions() as s:
            state = await s.get(DiagnosisState, "exec-durable")
            self.assertTrue(await self.loop.diagnosis_settled(s, "exec-durable"))
            state.cursor = 3
            await s.commit()
        async with self.sessions() as s:
            self.assertFalse(await self.loop.diagnosis_settled(s, "exec-durable"))
        self.assertIsNone(await self.loop.begin(project_id=7, execution_id="exec-durable",
                                                incident_id="source-incident", automatic=True))
        self.assertEqual(await self.records(), [])
        # a record that is already open is not dispatched either (no claim, no paid request)
        async with self.sessions() as s:
            state = await s.get(DiagnosisState, "exec-durable")
            state.cursor = 6
            await s.commit()
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")
        async with self.sessions() as s:
            (await s.get(DiagnosisState, "exec-durable")).cursor = 3
            await s.commit()
        reply, calls = await self.reply_with("wait")
        with patch("app.services.gpt_api.chat", side_effect=reply):
            await self.loop.tick()
        self.assertEqual(calls, [])
        async with self.sessions() as s:
            self.assertEqual((await s.get(self.service.DecisionRecord, record["decision_id"])).model_steps, 0)

    # --- the evidence moves while the model thinks
    async def test_context_drift_during_the_model_call_asks_again_instead_of_escalating(self):
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")

        async def drift(call_no):
            if call_no == 1:
                await self.set_incident()
                async with self.sessions() as s:
                    (await s.get(Incident, "source-incident")).status = "open"
                    await s.commit()
        reply, calls = await self.reply_with("wait", mutate=drift)
        with patch("app.services.gpt_api.chat", side_effect=reply):
            await self.loop.step(record["decision_id"])
            async with self.sessions() as s:
                row = await s.get(self.service.DecisionRecord, record["decision_id"])
                self.assertEqual(row.status, "ready", row.policy_result)
                self.assertEqual(row.proposal or {}, {})
                self.assertIsNone(row.lease_token)
            await self.loop.step(record["decision_id"])
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0]["context_version"], calls[1]["context_version"])
        self.assertEqual(calls[1]["incident_status"], "open")
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.model_steps), ("waiting_evidence", 2))

    async def test_endless_drift_is_bounded_by_the_step_limit_and_then_goes_to_a_human(self):
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")

        async def drift(call_no):
            async with self.sessions() as s:
                (await s.get(Incident, "source-incident")).status = "open" if call_no % 2 else "human_review"
                await s.commit()
        reply, calls = await self.reply_with("wait", mutate=drift)
        with patch("app.services.gpt_api.chat", side_effect=reply):
            for _ in range(5):
                await self.loop.step(record["decision_id"])
        self.assertEqual(len(calls), self.service.MAX_STEPS)
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record["decision_id"])
            self.assertEqual(row.status, "human_review")
            self.assertIn("kept changing", row.policy_result["reason"])

    async def test_only_a_change_of_the_diagnosis_view_is_resumable(self):
        """Input/config/policy drift after the request went out always denies the late reply (no resume)."""
        self.assertTrue(issubclass(self.service.StaleContext, ValueError))
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")
        token = await self.loop.claim(record["decision_id"])
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record["decision_id"])
            context = await self.loop._context(s, row)
            # the version differs but the stable part is the same: resumable
            with self.assertRaises(self.service.StaleContext):
                await self.loop._live(s, row, token, version="old", stable=context["stable_version"])
            # the stable part differs (input / config / policy): a plain denial, never resumed
            with self.assertRaises(ValueError) as denied:
                await self.loop._live(s, row, token, version="old", stable="another-input")
            self.assertNotIsInstance(denied.exception, self.service.StaleContext)
            with self.assertRaises(ValueError) as unknown:
                await self.loop._live(s, row, token, version="old")
            self.assertNotIsInstance(unknown.exception, self.service.StaleContext)

    async def test_input_change_while_the_model_thinks_still_goes_to_a_human(self):
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")

        async def change_input(_call_no):
            async with self.sessions() as s:
                (await s.get(actions_tests.Project, 7)).topic = "changed after send"
                await s.commit()
        reply, calls = await self.reply_with("wait", mutate=change_input)
        with patch("app.services.gpt_api.chat", side_effect=reply):
            await self.loop.step(record["decision_id"])
        self.assertEqual(len(calls), 1)
        async with self.sessions() as s:
            row = await s.get(self.service.DecisionRecord, record["decision_id"])
            self.assertEqual(row.status, "human_review")
            self.assertIn("stale", row.policy_result["reason"])

    # --- the model sees what the server itself offers
    async def test_the_context_states_what_the_server_offers_for_the_incident(self):
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")
        context = await self.loop.context(record["decision_id"])
        guidance = context["recovery_guidance"]
        self.assertTrue(guidance["retry_same_offered"])
        self.assertEqual(guidance["recovery_class"], "format")
        self.assertTrue(guidance["native_retries_exhausted"])
        self.assertIn("not an earlier escalation", guidance["incident_status_meaning"])
        self.assertEqual(guidance["retry_same_offered"], "retry_same" in context["allowed_actions"])

    async def test_guidance_never_widens_the_allowed_actions(self):
        """The hint is data about the allowed set, not a grant: a node without retry_same gets no offer."""
        record = await self.loop.begin(project_id=7, execution_id="exec-durable", incident_id="source-incident")
        from app.services import node_capabilities
        real = node_capabilities.capability_for

        def without_retry(node_type):
            cap = real(node_type)
            from dataclasses import replace
            return replace(cap, actions=tuple(a for a in cap.actions if a not in {"retry_same", "retry_profile"}))
        with patch("app.services.node_capabilities.capability_for", side_effect=without_retry):
            context = await self.loop.context(record["decision_id"])
        self.assertNotIn("retry_same", context["allowed_actions"])
        self.assertFalse(context["recovery_guidance"]["retry_same_offered"])


# The fixture base brings its own tests; they run (once) in tests/observe_v1, not here.
for _name in dir(actions_tests.DurableActions):
    if _name.startswith("test") and _name not in vars(DecisionOnOneIncident):
        setattr(DecisionOnOneIncident, _name, None)


def test_the_system_prompt_does_not_let_the_model_read_labels_as_bans():
    service = importlib.import_module("app.services.decision_v1")
    text = service.SYSTEM
    assert "NOT an earlier escalation" in text and "native_json_retry=false" in text
    assert "retry_profile is a" in text and "different action" in text
    # the safety statements of the prompt stay in place
    for must in ("UNTRUSTED DATA", "cannot grant permission", "never invent parameters", "Maximum three model steps"):
        assert must in text


# ---------------------------------------------------------------- 3. the whole chain on the stand (offline, stubs)

_STAND = ROOT / "scripts" / "e2e_vibecode_text_nodes.py"


def _run_offline(node, tmp_path, *extra):
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY"):
        env.pop(name, None)
    proc = subprocess.run([sys.executable, "-X", "utf8", str(_STAND), "--node", node, "--work-dir", str(tmp_path),
                           "--settle-s", "6", "--sleep-minutes", "0.01", "--timeout-s", "420", *extra],
                          cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=560)
    path = tmp_path / "result.json"
    return proc.returncode, (json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}), (proc.stdout or "")[-1500:]


@pytest.mark.parametrize("node,extra", [
    ("plan", ()), ("script", ()), ("animation_prompts", ()),
    ("sfx_plan", ("--decision-delay-s", "6")),       # a model that thinks for seconds, as the real one does
    ("sd_agent", ("--sd-agent", "characters")), ("sd_agent", ("--sd-agent", "world")),
    ("sd_assemble", ())])
def test_failure_one_incident_one_decision_retry_same_recovered_done(node, extra, tmp_path):
    code, result, tail = _run_offline(node, tmp_path, *extra)
    if code != 0:
        # Стенд поднимает настоящий сервер и воркер, и при загруженной машине один из семи вариантов иногда не успевает
        # (при разных прогонах падали разные: script, sd_agent, sd_assemble; поодиночке проходят). Одна повторная попытка
        # в чистой папке; если упали обе, тест красный и показывает хвосты обеих.
        first_tail = tail
        code, result, tail = _run_offline(node, tmp_path / "retry", *extra)
        assert code == 0, f"первая попытка:\n{first_tail}\nвторая попытка:\n{tail}"
    assert code == 0, tail
    assert result["mode"] == "offline" and result["ok"] is True, result.get("failed")
    assert result["counters"]["real_total"] == 0 and result["refused_hosts"] == []
    assert result["node_status"][result["target"]] == "done"
    records = result["decisions"]["records"]
    # ONE decision record for the failed attempt (the echo opened none), and it finished by the verified recovery
    assert [r["status"] for r in records] == ["done"], [(r["status"], r["incident_id"]) for r in records]
    recoveries = result["actions"]["recoveries"]
    assert len(recoveries) == 1 and recoveries[0]["status"] == "verified"
    assert recoveries[0]["preconditions"]["action_type"] == "retry_same"
    # the human stays in the loop where no permission was given: nothing was held, nothing waits for approval
    assert result["actions"]["held"] is False


def test_the_stand_has_no_known_blocker_left():
    spec = importlib.util.spec_from_file_location("e2e_vibecode_text_nodes_w5c", _STAND)
    stand = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = stand
    spec.loader.exec_module(stand)
    assert stand.KNOWN_BLOCKERS == {}
    assert stand.EXPECTED_HITL["plan"] == ("approve_plan",)


if __name__ == "__main__":
    unittest.main()
