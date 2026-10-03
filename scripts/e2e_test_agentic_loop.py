#!/usr/bin/env python
"""E2E exercise of the Decision/Self-Healing agentic loop:

    Observe -> Diagnose -> Decide -> Act -> Verify -> Continue / Human review

against two scripted incidents on an isolated, disposable SQLite database
(never the real data/state.db):

  1. "invalid_json" -- DecisionSupervisor is scripted to pick retry_profile
     using the capability-driven recommended_profile_id this session added
     to app/services/decision_v1.py::_context() (the repair-json-only-v1
     fallback profile). app/services/profiles_v1.py::ProfilesSupervisor
     approves and reserves the overlay for real (same code path production
     uses), a RecoveryAction ticket is created, and this script marks it
     verified the same way the real native verifier would (own worker
     execution is out of scope here -- see "What this script does NOT
     exercise" below). DecisionSupervisor.tick() then picks up the verified
     ticket and the record reaches status='done'.

  2. "uncertain_request" (simulating a classified ReadTimeout) -- retry_same
     and retry_profile are never offered for this incident type (by design:
     see docs/ARCHITECTURE.md "Decision cycle" -- retrying an ambiguous HTTP
     outcome risks a duplicate provider side effect). The model is scripted
     to choose human_review; DecisionSupervisor.escalate() fires and
     app/services/human_review_summary.py produces the structured
     REQUIRES_HUMAN_INTERVENTION summary, which this script prints in full.

Safety / cost:
  --dry-run (default): app.services.gpt_api.chat is monkeypatched to a
    scripted, local, synchronous responder. ZERO network calls, ZERO
    tokens, ZERO cost. This is the only mode this script runs in unless
    --live is passed explicitly.
  --live: the monkeypatch is NOT applied. DecisionSupervisor.step() calls
    the REAL configured Decision model via app.services.gpt_api.chat, which
    spends real tokens against whatever endpoint/model
    app.services.decision_config.runtime_config() resolves to (see
    config/orchestrator-runtime.self-healing-example.json for the recipe,
    and set ORCHESTRATOR_RUNTIME_CONFIG to point at your own copy with
    online_enabled=true and a real GPT_API_KEY). --live does not relax or
    bypass any existing guardrail (budget ledger, synthetic-loopback
    checks, approval flow) -- it only stops fighting the real HTTP path
    with a mock. In --live mode the model makes its own real decision, so
    the two scenario assertions below are relaxed to "did it reach a
    terminal, non-stuck state" rather than "did it pick exactly X", since a
    live model is not a scripted puppet.

What this script does NOT exercise (explicitly out of scope, to keep the
cost estimate below accurate and the dry-run path honest about what it
proves): the real excel_gpt node executor (app/orchestrator/steps via
enrich_xlsx.py) re-running against actual project XLSX data. Scenario 1's
"worker completes successfully" step is simulated by marking the
RecoveryAction ticket verified directly, exactly as
tests/observe_v1/test_actions_v1_contract.py's own fixtures do -- this is
an established pattern in this codebase, not a shortcut invented for this
script. A full native-worker E2E (the real excel_gpt executor, real XLSX
apply-ops) is a separate, larger script if needed later.

This script has not been executed yet -- see the chat report accompanying
its creation for the cost estimate and the DRY_RUN/live approval question.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")  # Windows console defaults to cp1251/cp866; incident titles are Russian


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Scripted, zero-network, zero-cost run (default).")
    mode.add_argument("--live", action="store_true",
        help="Real model calls via the configured Decision provider. Spends real tokens. "
             "Requires a real runtime config (see "
             "config/orchestrator-runtime.self-healing-example.json) and explicit intent.")
    return parser.parse_args()


_args = _parse_args()
_env_dry_run = os.environ.get("DRY_RUN", "").strip().lower()
_live = _args.live or (_env_dry_run in {"false", "0", "no"} and not _args.dry_run)
DRY_RUN = not _live

# CRITICAL: app.settings.Settings() is instantiated once at import time and
# reads GPT_API_KEY/GPT_BASE_URL from the environment right then -- setting
# them later has no effect. For DRY_RUN, default them to the same loopback/
# synthetic values tests/observe_v1/container_test_entry.py uses, so this
# script needs no manual env setup to run safely. Only set as *defaults*
# (os.environ.setdefault) so an operator's real .env / shell env always wins
# if already present -- this never overrides an explicitly configured value.
if DRY_RUN:
    os.environ.setdefault("GPT_API_KEY", "synthetic-e2e-script-key")
    os.environ.setdefault("GPT_BASE_URL", "http://127.0.0.1:18799")

import asyncio  # noqa: E402
import json  # noqa: E402
import tempfile  # noqa: E402
import uuid  # noqa: E402
from datetime import datetime  # noqa: E402
from unittest.mock import patch  # noqa: E402

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker  # noqa: E402

from app.models import (  # noqa: E402
    Base, Project, Workflow, WorkflowRun, NodeRun, NodeRunStatus, ProjectStatus,
)
from app.services import observation  # noqa: E402
from app.services.observation import ExecutionControl, ObservationEvent, digest  # noqa: E402
from app.services.diagnosis import Incident, DiagnosisState  # noqa: E402
from app.services.actions_v1 import ActionSupervisor, clear_permission  # noqa: E402
from app.services.actions_recovery import RecoveryAction, input_revision  # noqa: E402
from app.services.decision_v1 import DecisionSupervisor  # noqa: E402
from app.services.managed_launch import ManagedLaunch  # noqa: E402
from app.services import profiles_v1  # noqa: E402
from app.services import gpt_api  # noqa: E402
from app.services.gpt_api import GptChatResult  # noqa: E402

NODE_KEY = "n1"
EXECUTION_ID = "00000000-0000-0000-0000-000000e2e001"  # Observer.save_body() requires a real UUID
PROJECT_ID = 70100
WORKFLOW_RUN_ID = 70102


class Harness:
    """Isolated SQLite master DB + the minimal project/graph/execution
    fixture ActionSupervisor/DecisionSupervisor/Observer expect -- the same
    shape tests/observe_v1/test_actions_v1_contract.py::DurableActions
    builds, reproduced standalone so this script has no test-suite import
    dependency and can live in scripts/.
    """

    async def __aenter__(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'master.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(conn)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        nodes = [{"id": NODE_KEY, "type": "excel_gpt", "data": {"groupId": "g1"}}]
        self.graph = digest({"nodes": nodes, "edges": []})
        async with self.sessions() as s:
            s.add(Project(id=PROJECT_ID, slug="e2e-agentic-loop", topic="synthetic", meta={}))
            s.add(Workflow(id=70101, name="e2e fixture", nodes=nodes, edges=[]))
            s.add(WorkflowRun(id=WORKFLOW_RUN_ID, workflow_id=70101, project_id=PROJECT_ID,
                nodes_snapshot=nodes, edges_snapshot=[]))
            s.add(NodeRun(workflow_run_id=WORKFLOW_RUN_ID, node_key=NODE_KEY, node_type="excel_gpt",
                status=NodeRunStatus.pending))
            s.add(ExecutionControl(execution_id=EXECUTION_ID, project_id=PROJECT_ID,
                workflow_run_id=WORKFLOW_RUN_ID, node_keys=[NODE_KEY], graph_version=self.graph,
                mode="observe", observer_state="active", created_at=datetime.utcnow(), cursors={}, gaps=[]))
            await s.commit()
        self.saved_observer = observation._observer
        observation._observer = observation.Observer(self.sessions, body_root=Path(self.tmp.name) / "bodies")
        self.supervisor = ActionSupervisor(self.sessions)
        self.decisions = DecisionSupervisor(observation._observer)
        # Same redirect tests/observe_v1/test_decision_v1.py::DecisionDurability
        # uses: Project.data_dir is truthy (derived from slug) even though no
        # real per-project SQLite file exists here, so anything that opens a
        # "project DB" session must be pointed back at this one disposable
        # master session instead of trying to open a nonexistent file.
        import app.project_db as project_db
        self._project_sessions_patch = patch.object(
            project_db, "project_db_session_scope", lambda _id: self.sessions())
        self._project_sessions_patch.start()
        return self

    async def __aexit__(self, *exc_info):
        self._project_sessions_patch.stop()
        clear_permission(PROJECT_ID)
        from app.services.actions_recovery import finish_job_tracking
        finish_job_tracking(PROJECT_ID)
        observation._observer = self.saved_observer
        await self.engine.dispose()
        self.tmp.cleanup()

    async def seed_incident(self, *, incident_type: str, source_attempt_id: str = "source-attempt",
                             with_profile_launch: bool = False) -> None:
        """Mirrors test_actions_v1_contract.py::DurableActions.exhausted_fixture
        -- a native-exhausted excel_gpt node with a classified incident ready
        for Decision, built from the same ActionSupervisor.enable() call
        production uses (no shortcut around permission setup).
        """
        await self.supervisor.enable(project_id=PROJECT_ID, execution_id=EXECUTION_ID, group_id="g1",
            node_keys=[NODE_KEY], graph_version=self.graph, initiator="e2e-script",
            rationale="scripted agentic-loop e2e run",
            request_limit=20 if DRY_RUN else 4)  # --live: hard cap on real paid model requests
        async with self.sessions() as s:
            project = await s.get(Project, PROJECT_ID)
            project.status = ProjectStatus.paused
            project.meta = {"active_excel_gpt_node_key": NODE_KEY, "step_failure": {
                "total_fails": {"enriching_1": 9}, "last_running": "enriching_1", "abandoned_at": "e2e"}}
            node = await s.scalar(select(NodeRun).where(NodeRun.node_key == NODE_KEY))
            node.status = NodeRunStatus.failed
            node.finished_at = datetime.utcnow()
            node.attempts = 9
            revision = await input_revision(s, project, NODE_KEY)
            is_timeout = incident_type == "uncertain_request"
            # ProfilesSupervisor.approve() independently re-reads the exact
            # request body that produced this incident (never trusts a
            # summary) -- it must be saved through Observer.save_body() and
            # referenced from the model_start event, the same way
            # tests/observe_v1/test_profiles_v1.py::Profiles.asyncSetUp()
            # does, or approve() correctly refuses with "source body audit
            # unavailable".
            source_body = {"model": "gpt-5-6-sol",
                "input": "Original node prompt that produced a malformed response.",
                "stream": True, "reasoning": {"effort": "high"}}
            body_ref = observation._observer.save_body(EXECUTION_ID, source_body)
            facts = [
                ("node_start", {"recovery_input_revision": revision}, None),
                ("model_start", {"input_revision": digest(source_body), "body": body_ref, "provider": "kie"}, "request-source"),
                ("model_end", {"complete": not is_timeout}, "request-source"),
                *([] if is_timeout else [("json_retry", {}, None)]),
                ("parse_result", {"payload_required": True, "payload_present": False,
                    "parsed": False, "syntax_valid": False}, "request-source"),
                ("node_error", {"uncertain": is_timeout,
                    "error_type": "ReadTimeout" if is_timeout else None,
                    "error_code": "gpt_timeout" if is_timeout else None}, None),
            ]
            for idx, (kind, payload, request_id) in enumerate(facts, 1):
                s.add(ObservationEvent(id=idx, event_id=str(uuid.uuid4()), execution_id=EXECUTION_ID,
                    project_id=PROJECT_ID, workflow_run_id=WORKFLOW_RUN_ID, node_key=NODE_KEY,
                    attempt_id=source_attempt_id, request_id=request_id, db_scope="master",
                    kind=kind, payload=payload, created_at=datetime.utcnow()))
            s.add(Incident(incident_id="e2e-incident", execution_id=EXECUTION_ID, project_id=PROJECT_ID,
                node_key=NODE_KEY, attempt_id=source_attempt_id, type=incident_type, severity="error",
                status="human_review" if is_timeout else "open",
                details={"currently_relevant": True, "event_ids": [],
                    **({"catalog_code": "gpt_timeout"} if is_timeout else {})}))
            s.add(DiagnosisState(execution_id=EXECUTION_ID, cursor=len(facts)))
            if with_profile_launch:
                catalog = profiles_v1.active_catalog()
                profile_id = next(pid for pid, cand in catalog.items()
                    if cand.get("prompt_variant") == "repair-json-only-v1")
                s.add(ManagedLaunch(launch_id="e2e-launch", execution_id=EXECUTION_ID, project_id=PROJECT_ID,
                    workflow_run_id=WORKFLOW_RUN_ID, idempotency_key="e2e-launch-key", request_hash="e2e",
                    entry_node=NODE_KEY, status="submitted", result={},
                    policy={"allowed_actions": ["read_events", "read_contract", "wait", "human_review",
                        "retry_same", "retry_profile", "finish"],
                        "profile_ids": [profile_id], "profile_catalog_hash": profiles_v1.catalog_hash(),
                        "profiles": {profile_id: catalog[profile_id]}}))
            await s.commit()


def scripted_chat_factory(*, pick: str):
    """Builds a app.services.gpt_api.chat side_effect for DRY_RUN mode. It
    reads the real context JSON the real step() serialized into `prompt`,
    so the scripted proposal always references real, current evidence_ids /
    context_version / recommended_profile_id -- it is not a fixed string.
    """
    async def scripted_chat(*, model, prompt, **_kwargs):
        context = json.loads(prompt)
        if pick == "retry_profile":
            profile_id = context["recommended_profile_id"]
            assert profile_id, "expected a recommended_profile_id in context for the invalid_json scenario"
            proposal = {"action": "retry_profile", "arguments": {"profile_id": profile_id},
                "evidence_ids": context["evidence_ids"][:1], "context_version": context["context_version"],
                "explanation": "Diagnosed cause is a format/payload failure; using the "
                                "recommended repair-only prompt profile per context.recommended_profile_id."}
        elif pick == "human_review":
            proposal = {"action": "human_review", "arguments": {}, "evidence_ids": [],
                "context_version": context["context_version"],
                "explanation": "Request outcome is uncertain (classified timeout); retrying an "
                                "ambiguous HTTP result risks a duplicate provider side effect, "
                                "so this is escalated rather than retried."}
        else:
            raise ValueError(f"unknown scripted pick: {pick}")
        return GptChatResult(text=json.dumps(proposal), model=model)
    return scripted_chat


async def run_scenario_invalid_json(dry_run: bool) -> dict:
    print("\n=== Scenario 1: invalid_json -> self-healing via repair-json-only-v1 ===")
    async with Harness() as h:
        await h.seed_incident(incident_type="invalid_json", with_profile_launch=True)
        await h.decisions.configure(project_id=PROJECT_ID, execution_id=EXECUTION_ID, enabled=True,
            initiator="e2e-script", rationale="scripted decision ownership")
        record = await h.decisions.begin(project_id=PROJECT_ID, execution_id=EXECUTION_ID,
            incident_id="e2e-incident")
        print(f"DecisionRecord created: {record['decision_id']} status={record['status']}")

        context = await h.decisions.context(record["decision_id"])
        print(f"context.recommended_profile_id = {context['recommended_profile_id']!r}")

        if dry_run:
            with patch("app.services.gpt_api.chat", side_effect=scripted_chat_factory(pick="retry_profile")):
                await h.decisions.step(record["decision_id"])
        else:
            await h.decisions.step(record["decision_id"])

        async with h.sessions() as s:
            from app.services.decision_state import DecisionRecord
            row = await s.get(DecisionRecord, record["decision_id"])
            print(f"After step(): status={row.status} proposal.action={row.proposal.get('action')}")
            if row.status == "human_review":
                print(f"  (unexpected escalation reason: {row.policy_result.get('reason')})")
            if row.status == "waiting_worker":
                ticket = await s.get(RecoveryAction, row.action_id)
                # Simulate the native worker's real verifier marking this ticket
                # verified -- same mechanism tests/observe_v1 fixtures use;
                # running the real excel_gpt executor is out of scope here
                # (see module docstring).
                ticket.result = {"simulated": True, "note": "script-simulated worker success"}
                ticket.verification_result = {"verified": True, "source": "e2e-script simulated verifier"}
                ticket.status = "verified"
                await s.commit()

        await h.decisions.tick()
        async with h.sessions() as s:
            from app.services.decision_state import DecisionRecord
            row = await s.get(DecisionRecord, record["decision_id"])
            print(f"After tick(): status={row.status}")
            result = {"decision_id": row.decision_id, "final_status": row.status,
                "recommended_profile_id": context["recommended_profile_id"],
                "proposal_action": row.proposal.get("action")}
        print(f"Scenario 1 result: {json.dumps(result, ensure_ascii=False)}")
        return result


async def run_scenario_timeout_escalation(dry_run: bool) -> dict:
    print("\n=== Scenario 2: uncertain_request (timeout) -> REQUIRES_HUMAN_INTERVENTION ===")
    async with Harness() as h:
        await h.seed_incident(incident_type="uncertain_request", with_profile_launch=False)
        await h.decisions.configure(project_id=PROJECT_ID, execution_id=EXECUTION_ID, enabled=True,
            initiator="e2e-script", rationale="scripted decision ownership")
        record = await h.decisions.begin(project_id=PROJECT_ID, execution_id=EXECUTION_ID,
            incident_id="e2e-incident")
        print(f"DecisionRecord created: {record['decision_id']} status={record['status']}")

        context = await h.decisions.context(record["decision_id"])
        print(f"allowed_actions = {context['allowed_actions']}")
        assert "retry_same" not in context["allowed_actions"], (
            "retry_same must not be offered for an uncertain_request incident")
        assert "retry_profile" not in context["allowed_actions"], (
            "retry_profile must not be offered for an uncertain_request incident")

        if dry_run:
            with patch("app.services.gpt_api.chat", side_effect=scripted_chat_factory(pick="human_review")):
                await h.decisions.step(record["decision_id"])
        else:
            await h.decisions.step(record["decision_id"])

        async with h.sessions() as s:
            from app.services.decision_state import DecisionRecord
            row = await s.get(DecisionRecord, record["decision_id"])
            print(f"After step(): status={row.status}")
            summary = (row.policy_result or {}).get("summary")
            if summary:
                print(f"status_label = {summary['status_label']}")
                print(f"summary.text = {summary['text']}")
            result = {"decision_id": row.decision_id, "final_status": row.status, "summary": summary}
        print(f"Scenario 2 result: {json.dumps(result, ensure_ascii=False)}")
        return result


async def main(dry_run: bool) -> int:
    results = {}
    results["invalid_json"] = await run_scenario_invalid_json(dry_run)
    results["timeout_escalation"] = await run_scenario_timeout_escalation(dry_run)

    ok = (results["invalid_json"]["final_status"] == "done"
          and results["timeout_escalation"]["final_status"] == "human_review"
          and results["timeout_escalation"]["summary"] is not None
          and results["timeout_escalation"]["summary"]["status_label"] == "REQUIRES_HUMAN_INTERVENTION")
    if not dry_run:
        # A real model makes its own choice; only require the loop reached a
        # terminal, non-stuck state rather than the exact scripted action.
        ok = (results["invalid_json"]["final_status"] in {"done", "human_review"}
              and results["timeout_escalation"]["final_status"] == "human_review")

    print("\n=== Summary ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    print(f"\n{'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    print(f"Mode: {'DRY_RUN (scripted, zero network/cost)' if DRY_RUN else 'LIVE (real API calls, real cost)'}")
    exit_code = asyncio.run(main(DRY_RUN))
    sys.exit(exit_code)
