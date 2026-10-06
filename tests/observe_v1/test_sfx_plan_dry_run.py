"""Dry run of the sfx_plan retry path: observation -> diagnosis -> decision -> action -> verification.

No network, no provider, no paid call. What is REAL: plan_sfx.run, sfx_plan.plan_sfx_events,
ai_result_io.text_job, the observe_model wrapper (request identity, budget charge, first-body
check), observe_attempt, the journal and outbox delivery, the Diagnoser, the DecisionSupervisor
and its policy, ActionSupervisor.recover_node, the claim, the reconcile verification and the
recovery cap. What is SCRIPTED: the model boundary only (the low-level POST returns canned
replies) and the Decision model (canned proposals). What is SEEDED by hand: the native
step-failure counter that proves "native retries exhausted" (a runtime policy outside the
observation scope) and the master/project runtime copy, which uses the application's own
copy functions on the test databases.
"""
import json
import unittest
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_actions_v1_contract as base_tests
from app.models import (
    Base, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun,
)
from app.orchestrator.steps import plan_sfx
from app.services import decision_config, observation
from app.services.actions_recovery import RecoveryAction, finish_job_tracking, reconcile
from app.services.diagnosis import Incident, get_diagnoser
from app.services.gpt_api import GptChatResult
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.services.observation import observe_model
from app.settings import settings

EXECUTION = "00000000-0000-4000-8000-0000000000a1"
SCRIPT = {"replies": [], "bodies": []}


def plan_reply(**overrides):
    event = {"frame_number": 1, "t_start": 0.5, "duration": 1.0, "kind": "whoosh",
             "prompt": "soft paper whoosh", "gain": 0.5, "duck": False}
    second = {"frame_number": 2, "t_start": 3.5, "duration": 1.0, "kind": "hit",
              "prompt": "light desk hit", "gain": 0.6, "duck": False}
    return json.dumps({"events": [{**event, **overrides}, second]}, ensure_ascii=False)


@observe_model
async def fake_http_post(*, url, headers, body, timeout, use_model):
    """The model boundary: the real observe_model wrapper records and gates every call."""
    SCRIPT["bodies"].append(body)
    if not SCRIPT["replies"]:
        raise AssertionError("unscripted model request: the dry run allows exactly the scripted ones")
    return GptChatResult(text=SCRIPT["replies"].pop(0), model=use_model, finish_reason="stop",
                         usage={"prompt_tokens": 10, "completion_tokens": 5},
                         raw={"status": "completed"}, response_id=f"resp-{len(SCRIPT['bodies'])}")


async def fake_ask_fresh(text, *, timeout=600, project_id=None, max_retries=None):
    result = await fake_http_post(url="https://synthetic.invalid/codex/v1/responses", headers={},
        body={"model": "gpt-5-6-sol", "input": [{"role": "user", "content": text}]},
        timeout=timeout, use_model="gpt-5-6-sol")
    return result.text


class DryRun(unittest.IsolatedAsyncioTestCase):
    fixture_node_type = "sfx_plan"

    async def asyncSetUp(self):
        SCRIPT["replies"], SCRIPT["bodies"] = [], []
        await base_tests.DurableActions.asyncSetUp(self)
        self.stack = ExitStack()
        self.data = Path(self.tmp.name) / "videos" / "actions-fixture"
        self.data.mkdir(parents=True)
        for patcher in (patch.object(settings, "data_dir", Path(self.tmp.name)),
                        patch.object(settings, "sfx_enabled", True),
                        patch.object(settings, "gpt_api_key", "synthetic-observe-test-key"),
                        patch.object(settings, "gpt_base_url", "http://127.0.0.1:18766"),
                        patch("app.services.gpt_client.gpt_ask_fresh", new=fake_ask_fresh)):
            self.stack.enter_context(patcher)
        cfg = decision_config.validate_config({'version': 1, 'mode': 'synthetic', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': sorted(decision_config.ALL_ACTIONS)})
        self.stack.enter_context(patch.object(decision_config, "runtime_config", return_value=cfg))
        self.observer = observation._observer

        # Business database (project.db) next to the master database, like production.
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.data / 'project.db'}")
        self.business = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(connection)
        canvas = {"canvas_graph": {"nodes": self.nodes, "edges": []}}
        async with self.business() as seed:
            seed.add_all([
                Project(id=7, slug="actions-fixture", topic="synthetic sfx", status=ProjectStatus.sfx_planning,
                        auto_mode=False, meta=dict(canvas)),
                Workflow(id=9, name="actions fixture", nodes=self.nodes, edges=[]),
                WorkflowRun(id=11, workflow_id=9, project_id=7, nodes_snapshot=self.nodes, edges_snapshot=[]),
                NodeRun(workflow_run_id=11, node_key="n1", node_type="sfx_plan", status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11, node_key="n2", node_type="script", status=NodeRunStatus.pending),
                Frame(project_id=7, number=1, uuid="af" * 16, sort_key=1, voiceover_text="Альфа закрывает папку.",
                      duration_seconds=3.0, attrs={"place": "desk", "main_action": "close"}),
                Frame(project_id=7, number=2, uuid="bf" * 16, sort_key=2, voiceover_text="Бета открывает папку.",
                      duration_seconds=3.0, attrs={"place": "desk", "main_action": "open"}),
            ])
            await seed.commit()
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            project.status, project.meta = ProjectStatus.sfx_planning, dict(canvas)
            scope = await master.get(observation.ExecutionControl, "exec-durable")
            scope.execution_id, scope.node_keys = EXECUTION, ["n1", "n2"]
            await master.commit()

        @asynccontextmanager
        async def project_scope(_project_id):
            async with self.business() as session:
                yield session
                await session.commit()

        async def push(session, project):
            await self.copy_runtime(session, project)

        async def no_pull(*_args):
            return None

        self.stack.enter_context(patch("app.project_db.project_db_session_scope", project_scope))
        self.stack.enter_context(patch("app.project_db.push_runtime_to_master", push))
        self.stack.enter_context(patch("app.project_db.pull_master_runtime_into_project", no_pull))
        await self.supervisor.enable(project_id=7, execution_id=EXECUTION, group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="dry-run", rationale="sfx_plan dry run", request_limit=20)
        await self.observer.reload()

    async def asyncTearDown(self):
        try:
            await self.engine.dispose()
            self.stack.close()
        finally:
            await base_tests.DurableActions.asyncTearDown(self)

    # ---- runtime bookkeeping (the application's own copy functions) --------------------
    async def copy_runtime(self, session, project):
        from app.project_db import _apply_runtime_fields, _copy_noderuns_between_sessions
        async with self.sessions() as master:
            _apply_runtime_fields(project, await master.get(Project, 7))
            await _copy_noderuns_between_sessions(session, master, 7)
            await master.commit()

    async def sync(self):
        async with self.business() as session:
            await self.copy_runtime(session, await session.get(Project, 7))

    async def deliver(self):
        await self.observer.consume(EXECUTION, "master", self.sessions)
        await self.observer.consume(EXECUTION, "project", self.business)

    async def diagnose(self):
        await self.sync()
        await self.deliver()
        await get_diagnoser(self.observer).update(EXECUTION)
        self.assertEqual(get_diagnoser(self.observer).failures, {})

    async def facts(self, kind=None, attempt=None):
        await self.deliver()
        async with self.sessions() as session:
            rows = (await session.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all()
        return [r for r in rows if (kind is None or r.kind == kind)
                and (attempt is None or r.attempt_id == attempt)]

    async def incidents(self):
        async with self.sessions() as session:
            return (await session.scalars(select(Incident).where(Incident.execution_id == EXECUTION))).all()

    # ---- the worker, as the runtime drives it -------------------------------------------
    async def worker(self, replies, *, start=True):
        """One native attempt of the original step under observe_attempt (what advance does)."""
        SCRIPT["replies"] = list(replies)
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            if start:
                self.assertTrue(queue_node_for_start(node, project_id=7, initiator="worker"))
                self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
                await session.commit()
            try:
                async with observation.observe_attempt(session, project):
                    await plan_sfx.run(session, project)
            except Exception:
                await session.rollback()
                raise
            complete_node(node, project_id=7, initiator="worker")
            await session.commit()

    async def exhaust_native_retries(self, error="native step retries exhausted"):
        """The runtime's step-failure policy after 9 failed native attempts (outside observation)."""
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            fail_node(node, error, project_id=7, initiator="worker")
            node.finished_at, node.attempts = datetime.utcnow(), 9
            project.status = ProjectStatus.paused
            project.meta = {**project.meta, "step_failure": {"total_fails": {"sfx_planning": 9},
                "last_running": "sfx_planning", "abandoned_at": "synthetic"}}
            await session.commit()
        await self.diagnose()

    async def failed_attempt(self, replies, error_type=Exception):
        with self.assertRaises(error_type) as caught:
            await self.worker(replies)
        await self.exhaust_native_retries()
        return caught.exception

    async def typed_incident(self):
        found = [i for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}]
        self.assertEqual(len(found), 1, [(i.type, i.status) for i in await self.incidents()])
        return found[0]

    def decision_model(self, actions):
        """The Decision model: canned proposals, validated by the real policy."""
        queue = list(actions)

        async def reply(**kwargs):
            context = json.loads(kwargs["prompt"])
            action = queue.pop(0)
            self.assertIn(action, context["allowed_actions"])
            proposal = dict(action=action, arguments={}, evidence_ids=context["evidence_ids"][:1],
                            context_version=context["context_version"],
                            explanation="synthetic facts, no hidden reasoning")
            return GptChatResult(text=json.dumps(proposal), model="synthetic", raw={"status": "completed"})
        return patch("app.services.gpt_api.chat", side_effect=reply)

    # ======================================================================================
    # observation + diagnosis
    # ======================================================================================
    async def test_unparseable_replies_are_typed_invalid_json_and_request_bound(self):
        error = await self.failed_attempt(["no json here", "still {not json"])
        self.assertEqual(type(error).__name__, "TextJobFailed")
        parse = (await self.facts("parse_result"))[-1]
        payload = parse.payload
        self.assertEqual((payload["contract"], payload["source"]), ("native_sfx_plan", "model"))
        self.assertIs(payload["payload_required"], True)
        self.assertIs(payload["parsed"], False)
        self.assertIs(payload["payload_present"], False)
        self.assertIs(payload["syntax_valid"], False)
        starts = await self.facts("model_start")
        self.assertEqual(len(starts), 2)
        self.assertEqual(parse.request_id, starts[-1].request_id, "bound to the last completed request")
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "invalid_json")
        self.assertFalse(incident.details["evidence_incomplete"])
        self.assertTrue(incident.details["currently_relevant"])

    async def test_json_without_events_is_typed_missing_payload(self):
        await self.failed_attempt(['{"note": "no events here"}'], ValueError)
        payload = (await self.facts("parse_result"))[-1].payload
        self.assertIs(payload["syntax_valid"], True)
        self.assertIs(payload["parsed"], False)
        self.assertIs(payload["payload_present"], False)
        self.assertEqual((await self.typed_incident()).type, "missing_payload")

    async def test_timeline_rule_violation_is_a_rejected_contract_not_a_json_incident(self):
        bad = plan_reply(kind="laser")
        error = await self.failed_attempt([bad, bad], RuntimeError)
        self.assertEqual(type(error).__name__, "TextJobFailed")
        payload = (await self.facts("parse_result"))[-1].payload
        self.assertIs(payload["parsed"], True)
        self.assertIs(payload["payload_present"], True)
        self.assertIs(payload["schema_valid"], False)
        types = {i.type for i in await self.incidents()}
        self.assertIn("contract_rejected", types)
        self.assertFalse(types & {"invalid_json", "missing_payload"})

    async def test_transport_failure_is_not_a_parse_failure(self):
        # No scripted reply: the fake boundary raises, which is a transport-class error.
        with self.assertRaises(AssertionError):
            await self.worker([])
        self.assertEqual(await self.facts("parse_result"), [])

    async def test_success_records_request_bound_parse_and_transactional_data_applied(self):
        await self.worker([plan_reply()])
        parse = (await self.facts("parse_result"))[-1]
        starts = await self.facts("model_start")
        self.assertEqual(len(starts), 1)
        self.assertEqual(parse.request_id, starts[0].request_id)
        self.assertIs(parse.payload["payload_present"], True)
        applied = await self.facts("data_applied")
        self.assertEqual(len(applied), 1)
        self.assertIs(applied[0].payload["transactional"], True)
        self.assertIs(applied[0].payload["result"]["ok"], True)
        self.assertEqual(applied[0].request_id, parse.request_id)
        self.assertEqual(applied[0].db_scope, "project")
        self.assertTrue((self.data / "sfx_plan.json").is_file())
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual(proof["status"], "passed")

    async def test_rolled_back_plan_leaves_no_application_fact(self):
        await self.failed_attempt([plan_reply(kind="laser")] * 2, RuntimeError)
        self.assertEqual(await self.facts("data_applied"), [])
        self.assertFalse((self.data / "sfx_plan.json").exists())

    # ======================================================================================
    # checkpoint reset
    # ======================================================================================
    async def seed_invalid_checkpoint(self):
        from app.services.ai_result_io import save_ai_job_checkpoint
        async with self.business() as session:
            project = await session.get(Project, 7)
            save_ai_job_checkpoint(project, "sfx_plan", {"events": [{"frame_number": 1, "t_start": 99.0,
                "duration": 1.0, "kind": "whoosh", "prompt": "x", "gain": 0.5, "duck": False}]})
            await session.commit()

    async def test_ordinary_attempt_keeps_the_native_rule_invalid_cache_makes_no_request(self):
        await self.seed_invalid_checkpoint()
        with self.assertRaises(RuntimeError):
            await self.worker([plan_reply()])
        self.assertEqual(SCRIPT["bodies"], [], "an invalid cache is rejected without a model call")
        async with self.business() as session:
            self.assertIsNotNone((await session.get(Project, 7)).meta["ai_jobs"]["sfx_plan"])

    async def test_recovery_attempt_drops_an_invalid_checkpoint_and_reaches_the_model(self):
        from app.services.sfx_plan import _reset_invalid_checkpoint_for_recovery
        await self.seed_invalid_checkpoint()
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            queue_node_for_start(node, project_id=7, initiator="worker")
            start_node_running(node, project_id=7, initiator="worker")
            await session.commit()
            async with observation.observe_attempt(session, project):
                ctx = observation._attempt.get()
                ctx.native_node_type = "sfx_plan"
                self.assertFalse(await _reset_invalid_checkpoint_for_recovery(project, total_duration=6.0))
                session.info["recovery_attempt_id"] = ctx.attempt_id
                self.assertTrue(await _reset_invalid_checkpoint_for_recovery(project, total_duration=6.0))
                self.assertNotIn("sfx_plan", (project.meta or {}).get("ai_jobs", {}))
                self.assertFalse(await _reset_invalid_checkpoint_for_recovery(project, total_duration=6.0))
                session.info.pop("recovery_attempt_id")
        checks = [f.payload for f in await self.facts("runtime_check")
                  if f.payload.get("check") == "sfx_plan_checkpoint_reset"]
        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["status"], "passed")

    async def test_valid_checkpoint_is_never_reset(self):
        from app.services.ai_result_io import save_ai_job_checkpoint
        from app.services.sfx_plan import _reset_invalid_checkpoint_for_recovery
        async with self.business() as session:
            project = await session.get(Project, 7)
            save_ai_job_checkpoint(project, "sfx_plan", json.loads(plan_reply()))
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            queue_node_for_start(node, project_id=7, initiator="worker")
            start_node_running(node, project_id=7, initiator="worker")
            await session.commit()
            async with observation.observe_attempt(session, project):
                ctx = observation._attempt.get()
                session.info["recovery_attempt_id"] = ctx.attempt_id
                self.assertFalse(await _reset_invalid_checkpoint_for_recovery(project, total_duration=6.0))
                self.assertIn("sfx_plan", project.meta["ai_jobs"])
                session.info.pop("recovery_attempt_id")

    # ======================================================================================
    # managed verification gate
    # ======================================================================================
    async def test_managed_pass_through_without_a_plan_is_held_not_verified(self):
        with patch.object(settings, "sfx_enabled", False):
            with self.assertRaisesRegex(RuntimeError, "verification not confirmed"):
                await self.worker([])
        async with self.sessions() as session:
            control = await session.get(base_tests.ActionExecutionControl, EXECUTION)
            self.assertTrue(control.held)
        self.assertEqual(SCRIPT["bodies"], [])

    async def test_native_failure_does_not_hold_the_execution(self):
        await self.failed_attempt([plan_reply(kind="laser")] * 2, RuntimeError)
        async with self.sessions() as session:
            self.assertFalse((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)

    async def test_observation_scope_never_raises_from_the_gate(self):
        from app.services.sfx_plan import observe_sfx_plan_result
        async with self.sessions() as master:
            scope = await master.get(observation.ExecutionControl, EXECUTION)
            scope.mode = "observe"
            await master.commit()
        await self.observer.reload()
        with patch.object(settings, "sfx_enabled", False):
            await self.worker([])  # pass-through: journal only, no hold, no exception
        async with self.sessions() as session:
            self.assertFalse((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)
        self.assertTrue(callable(observe_sfx_plan_result))

    # ======================================================================================
    # the whole path
    # ======================================================================================
    async def test_failed_attempt_to_decision_to_one_verified_retry(self):
        from app.services import decision_v1
        # 1. observation: the original step fails twice with unusable replies.
        await self.failed_attempt(["no json here", "still {not json"])
        first_bodies = list(SCRIPT["bodies"])
        self.assertEqual(len(first_bodies), 2)
        # 2. diagnosis: one typed, current, complete incident.
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "invalid_json")
        # 3. decision: read the contract, then ask for retry_same.
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=7, execution_id=EXECUTION, enabled=True,
                             initiator="dry-run", rationale="sfx_plan dry run decision")
        record = await loop.begin(project_id=7, execution_id=EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertEqual(context["node_capability"]["node_type"], "sfx_plan")
        self.assertIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        contract = await loop.read_contract(record["decision_id"])
        self.assertEqual(contract["contract"]["node_type"], "sfx_plan")
        self.assertIs(contract["contract"]["payload_required"], True)
        self.assertNotIn("nonempty_payload_required", contract, "not the excel_gpt contract")
        with self.decision_model(["read_contract", "retry_same"]):
            await loop.step(record["decision_id"])
            await loop.step(record["decision_id"])
        # 4. action: exactly one recovery ticket, queued for the ORIGINAL worker.
        async with self.sessions() as session:
            ticket = await session.scalar(select(RecoveryAction))
            self.assertEqual(ticket.status, "queued")
            self.assertEqual(ticket.preconditions["action_type"], "retry_same")
            self.assertEqual(ticket.preconditions["effective_node_type"], "sfx_plan")
            self.assertEqual(ticket.budget_state["recovery_limit"], 1)
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.action_id), ("waiting_worker", ticket.action_id))
        # 5. the original worker runs the retry: the SAME first request, now a valid reply.
        await self.worker([plan_reply()], start=False)
        finish_job_tracking(7)
        second_bodies = SCRIPT["bodies"][len(first_bodies):]
        self.assertEqual(len(second_bodies), 1)
        self.assertEqual(second_bodies[0], first_bodies[0], "recovery's first request equals the source's")
        # 6. verification of the attempt, from the journal.
        await self.sync()
        await self.deliver()
        await reconcile(self.observer)
        async with self.sessions() as session:
            ticket = await session.scalar(select(RecoveryAction))
            self.assertEqual(ticket.status, "verified", ticket.verification_result)
            checks = ticket.verification_result
            self.assertTrue(checks["data_applied"] and checks["accepted_payload"] and checks["result_confirmed"])
            self.assertTrue(checks["node_done"] and checks["transactional_done"] and checks["requests_complete"])
            self.assertEqual(ticket.result["continuation"], "original runtime; mandatory HITL untouched")
            self.assertFalse((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)
        self.assertTrue((self.data / "sfx_plan.json").is_file())
        async with self.business() as session:
            project = await session.get(Project, 7)
            self.assertTrue(project.meta.get("sfx_plan"))
            self.assertEqual(project.status, ProjectStatus.sfx_plan_ready)
        # 7. cap: the decision owner refuses a competing recovery, and the database refuses a
        #    second ticket for the same node and execution; sfx_gen was never started.
        with self.assertRaisesRegex(ValueError, "decision owner"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="second-retry-0001",
                initiator="dry-run", rationale="second retry must be refused")
        from sqlalchemy.exc import IntegrityError
        async with self.sessions() as session:
            session.add(RecoveryAction(action_id="second", execution_id=EXECUTION, project_id=7,
                incident_id=incident.incident_id, node_key="n1", source_attempt_id="s", new_attempt_id="n",
                idempotency_key="second-ticket-0001", preconditions={}, budget_state={}, status="reserved"))
            with self.assertRaises(IntegrityError):
                await session.commit()
        self.assertEqual(len(SCRIPT["bodies"]), 3, "two failed requests plus one recovery request, nothing else")

    async def test_retry_is_refused_when_the_failed_attempt_applied_data(self):
        await self.failed_attempt(["no json here", "still {not json"])
        incident = await self.typed_incident()
        async with self.sessions() as session:
            source = await session.scalar(select(observation.ObservationEvent).where(
                observation.ObservationEvent.kind == "node_start"))
            session.add(observation.ObservationEvent(event_id="late-apply", execution_id=EXECUTION, project_id=7,
                workflow_run_id=11, node_key="n1", attempt_id=source.attempt_id, db_scope="project",
                kind="data_applied", payload={"result": {"ok": True}, "transactional": True},
                created_at=datetime.utcnow()))
            await session.commit()
        await get_diagnoser(self.observer).update(EXECUTION)
        with self.assertRaisesRegex(ValueError, "partial apply"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="blocked-retry-0001",
                initiator="dry-run", rationale="apply evidence must block the retry")
        async with self.sessions() as session:
            self.assertIsNone(await session.scalar(select(RecoveryAction)))

    async def test_profile_retry_is_denied_by_capability(self):
        await self.failed_attempt(["no json here", "still {not json"])
        incident = await self.typed_incident()
        with self.assertRaisesRegex(ValueError, "denied by node capability"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="profile-retry-0001",
                initiator="dry-run", rationale="profile retry is not a sfx_plan capability",
                profile_overlay_id="no-such-overlay")


if __name__ == "__main__":
    unittest.main(verbosity=2)
