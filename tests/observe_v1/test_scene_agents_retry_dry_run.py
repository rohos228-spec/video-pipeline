import json
import unittest
from contextlib import ExitStack
from datetime import datetime
from unittest.mock import patch

from sqlalchemy import select

import test_actions_v1_contract as base_tests
import test_scene_agents_dry_run as sd
from app.models import NodeRun, Project, ProjectStatus, SceneDesignCell
from app.services import decision_config, observation, scene_contracts
from app.services.actions_recovery import RecoveryAction, finish_job_tracking, reconcile
from app.services.gpt_api import GptChatResult
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.orchestrator.steps import scene_design

EXECUTION = sd.EXECUTION
GOOD_WORLD = {"locations": [{"id": "loc01", "name": "Читальный зал", "описание": "высокие окна, дубовые столы"}],
              "report": "локаций:1"}


class ManagedSceneFixture(sd.SceneAgentFixture):
    enable_scope = True

    async def asyncSetUp(self):
        await super().asyncSetUp()
        cfg = decision_config.validate_config({'version': 1, 'mode': 'synthetic', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': sorted(decision_config.ALL_ACTIONS)})
        self.stack.enter_context(patch.object(decision_config, "runtime_config", return_value=cfg))
        from app.services import db_v2
        async with self.business() as session:
            await db_v2.backfill_project_v2(session, await session.get(Project, 7))
            await session.commit()
        if self.enable_scope:
            await self.enable()

    async def enable(self):
        result = await self.supervisor.enable(project_id=7, execution_id=EXECUTION, group_id="g1",
            node_keys=["n1"], graph_version=self.graph, initiator="dry-run",
            rationale="sd_agent dry run", request_limit=20)
        await self.observer.reload()
        return result

    async def worker(self, replies, *, start=True):
        sd.SCRIPT["replies"] = list(replies)
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            if start:
                self.assertTrue(queue_node_for_start(node, project_id=7, initiator="worker"))
                self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
                await session.commit()
            try:
                async with observation.observe_attempt(session, project):
                    await scene_design.run(session, project, None)
            except Exception:
                await session.rollback()
                raise
            complete_node(node, project_id=7, initiator="worker")
            await session.commit()

    async def exhaust_native_retries(self, error="native step retries exhausted"):
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            fail_node(node, error, project_id=7, initiator="worker")
            node.finished_at, node.attempts = datetime.utcnow(), 9
            project.status = ProjectStatus.paused
            project.meta = {**project.meta, "step_failure": {"total_fails": {"scene_designing": 9},
                "last_running": "scene_designing", "abandoned_at": "synthetic"}}
            await session.commit()
        await self.diagnose()

    async def failed_native_attempts(self, replies):
        with self.assertRaises(Exception) as caught:
            await self.worker(replies)
        await self.exhaust_native_retries()
        return caught.exception

    async def typed_incident(self):
        found = [i for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}]
        self.assertEqual(len(found), 1, [(i.type, i.status) for i in await self.incidents()])
        return found[0]

    def decision_model(self, actions):
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

    async def ticket(self):
        async with self.sessions() as session:
            return await session.scalar(select(RecoveryAction))

    async def held(self):
        async with self.sessions() as session:
            return (await session.get(base_tests.ActionExecutionControl, EXECUTION)).held

    async def decide_retry(self, incident):
        from app.services import decision_v1
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=7, execution_id=EXECUTION, enabled=True,
                             initiator="dry-run", rationale="sd_agent dry run decision")
        record = await loop.begin(project_id=7, execution_id=EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertEqual(context["node_capability"]["node_type"], "sd_agent")
        self.assertIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        contract = await loop.read_contract(record["decision_id"])
        self.assertEqual(contract["contract"]["agent"], self.agent)
        self.assertEqual(contract["contract"]["retry_admitted_agents"], ["characters", "world"])
        self.assertEqual(set(contract["contract"]["refused_agents"]), {"skeleton", "camera", "action", "legacy"})
        with self.decision_model(["read_contract", "retry_same"]):
            await loop.step(record["decision_id"])
            await loop.step(record["decision_id"])
        return record


class AdmittedAgentRetry:
    bad_reply = "I cannot produce JSON for this."
    no_list_reply = '{"report": "no list here"}'
    good_reply = ""

    async def test_failed_attempt_to_decision_to_one_verified_retry(self):
        from app.services import decision_v1
        await self.failed_native_attempts([self.bad_reply])
        first_bodies = list(sd.SCRIPT["bodies"])
        self.assertEqual(len(first_bodies), 1)
        self.assertEqual(await self.cells(), [])
        self.assertEqual(await self.facts("data_applied"), [], "nothing was applied by the failed attempt")
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "invalid_json")
        self.assertFalse(incident.details["evidence_incomplete"])
        self.assertTrue(incident.details["currently_relevant"])
        record = await self.decide_retry(incident)
        ticket = await self.ticket()
        self.assertEqual(ticket.status, "queued")
        self.assertEqual(ticket.preconditions["action_type"], "retry_same")
        self.assertEqual(ticket.preconditions["effective_node_type"], "sd_agent")
        self.assertEqual(ticket.budget_state["recovery_limit"], 1)
        async with self.sessions() as session:
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.action_id), ("waiting_worker", ticket.action_id))
        await self.worker([self.good_reply], start=False)
        finish_job_tracking(7)
        second_bodies = sd.SCRIPT["bodies"][len(first_bodies):]
        self.assertEqual(len(second_bodies), 1)
        self.assertEqual(second_bodies[0], first_bodies[0], "recovery's first request equals the source's")
        stored = await self.cells()
        self.assertTrue(stored and all(c.status == "ok" and c.agent == self.agent for c in stored))
        applied = await self.facts("data_applied")
        self.assertEqual(len(applied), 1)
        self.assertIs(applied[0].payload["transactional"], True)
        result = applied[0].payload["result"]
        self.assertEqual((result["ok"], result["agent"], result["cells_stored"], result["cells_rejected"]),
                         (True, self.agent, len(stored), 0))
        self.assertEqual(applied[0].db_scope, "project")
        parse = (await self.facts("parse_result"))[-1]
        self.assertEqual(applied[0].request_id, parse.request_id)
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"], proof["agent"]), ("passed", None, self.agent))
        await self.sync()
        await self.deliver()
        await reconcile(self.observer)
        ticket = await self.ticket()
        self.assertEqual(ticket.status, "verified", ticket.verification_result)
        checks = ticket.verification_result
        self.assertTrue(checks["data_applied"] and checks["accepted_payload"] and checks["result_confirmed"])
        self.assertTrue(checks["node_done"] and checks["transactional_done"] and checks["requests_complete"])
        self.assertEqual(ticket.result["continuation"], "original runtime; mandatory HITL untouched")
        self.assertFalse(await self.held())
        async with self.business() as session:
            self.assertEqual((await session.get(Project, 7)).status, ProjectStatus.frames_ready,
                             "the fan-out is not complete: the original step hands back to the planner")
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
        self.assertEqual(len(sd.SCRIPT["bodies"]), 2, "one failed request plus one recovery request, nothing else")

    async def test_json_without_the_list_is_a_missing_payload_and_retried_the_same_way(self):
        await self.failed_native_attempts([self.no_list_reply])
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "missing_payload")
        await self.decide_retry(incident)
        await self.worker([self.good_reply], start=False)
        finish_job_tracking(7)
        await self.sync()
        await self.deliver()
        await reconcile(self.observer)
        self.assertEqual((await self.ticket()).status, "verified", (await self.ticket()).verification_result)

    async def test_retry_is_refused_when_the_failed_attempt_applied_data(self):
        await self.failed_native_attempts([self.bad_reply])
        incident = await self.typed_incident()
        async with self.sessions() as session:
            source = await session.scalar(select(observation.ObservationEvent).where(
                observation.ObservationEvent.kind == "node_start"))
            session.add(observation.ObservationEvent(event_id="late-apply", execution_id=EXECUTION, project_id=7,
                workflow_run_id=11, node_key="n1", attempt_id=source.attempt_id, db_scope="project",
                kind="data_applied", payload={"result": {"ok": True}, "transactional": True},
                created_at=datetime.utcnow()))
            await session.commit()
        from app.services.diagnosis import get_diagnoser
        await get_diagnoser(self.observer).update(EXECUTION)
        with self.assertRaisesRegex(ValueError, "partial apply"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="blocked-retry-0001",
                initiator="dry-run", rationale="apply evidence must block the retry")
        self.assertIsNone(await self.ticket())

    async def test_profile_retry_with_an_unapproved_overlay_is_refused_without_a_ticket(self):
        await self.failed_native_attempts([self.bad_reply])
        incident = await self.typed_incident()
        with self.assertRaisesRegex(ValueError, "execution grant|profile outside|unknown or incompatible"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="profile-retry-0001",
                initiator="dry-run", rationale="profile retry is not a scene agent capability",
                profile_overlay_id="no-such-overlay")
        self.assertIsNone(await self.ticket())

    async def test_a_failure_inside_the_cell_transaction_keeps_the_old_cells_and_stages_no_fact(self):
        await self.worker([self.good_reply])
        before = [(c.kind, c.target_key, c.field, c.value) for c in await self.cells()]
        applied_before = len(await self.facts("data_applied"))
        self.assertEqual(applied_before, 1)
        broken = SceneDesignCell(project_id=None, agent=self.agent, kind="x", target_key="x", field="x",
                                 value="x", seq=0, status="ok")
        with patch("app.services.scene_design.cells.slice_to_cells", return_value=[broken]):
            with self.assertRaises(Exception):
                await self.worker([], start=True)
        after = [(c.kind, c.target_key, c.field, c.value) for c in await self.cells()]
        self.assertEqual(after, before, "the delete of the old cells was rolled back with the failed insert")
        self.assertEqual(len(await self.facts("data_applied")), applied_before, "no fact for an unapplied result")

    async def test_the_transactional_store_writes_exactly_what_the_chunked_store_writes(self):
        from app.services.scene_design import cells as sd_cells
        await self.worker([self.good_reply])
        transactional = [(c.kind, c.target_key, c.field, c.value, c.seq, c.vo_offset, c.status, c.error)
                         for c in await self.cells()]
        async with self.business() as session:
            project = await session.get(Project, 7)
            slice_data = json.loads(self.good_reply)
            converted = sd_cells.slice_to_cells(project, self.agent, slice_data, "\n".join(sd.VO))
            await sd_cells.store_cells(session, project, self.agent, converted)
        chunked = [(c.kind, c.target_key, c.field, c.value, c.seq, c.vo_offset, c.status, c.error)
                   for c in await self.cells()]
        self.assertEqual(chunked, transactional)


class CharactersRetry(AdmittedAgentRetry, ManagedSceneFixture):
    agent = "characters"
    good_reply = json.dumps(sd.GOOD_CHARACTERS, ensure_ascii=False)


class WorldRetry(AdmittedAgentRetry, ManagedSceneFixture):
    agent = "world"
    good_reply = json.dumps(GOOD_WORLD, ensure_ascii=False)


class RefusedAgents(ManagedSceneFixture):
    enable_scope = False
    agent = "camera"

    async def refuse(self, agent, code):
        from app.models import Workflow, WorkflowRun
        nodes = [{"id": "n1", "type": "sd_agent", "data": {"groupId": "g1", "sd_agent": agent}}, self.nodes[1]]
        graph = scene_contracts
        from app.services import revisions
        version = revisions.graph_version(nodes, [])
        async with self.sessions() as master:
            (await master.get(Workflow, 9)).nodes = nodes
            run = await master.get(WorkflowRun, 11)
            run.nodes_snapshot = nodes
            scope = await master.get(observation.ExecutionControl, EXECUTION)
            scope.graph_version = version
            await master.commit()
        await self.observer.reload()
        with self.assertRaisesRegex(ValueError, rf"scene agent '{agent}' is not admitted .*\({code}\)"):
            await self.supervisor.enable(project_id=7, execution_id=EXECUTION, group_id="g1", node_keys=["n1"],
                graph_version=version, initiator="dry-run", rationale="refused agent", request_limit=5)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(base_tests.ActionExecutionControl, EXECUTION))

    async def test_skeleton_is_refused_with_its_reason(self):
        await self.refuse("skeleton", "two_stage_state")

    async def test_camera_is_refused_with_its_reason(self):
        await self.refuse("camera", "chunked_requests")

    async def test_action_is_refused_with_its_reason(self):
        await self.refuse("action", "chunked_requests")

    async def test_every_refusal_text_names_a_concrete_mechanism(self):
        for agent, (code, reason) in scene_contracts.RETRY_REFUSED_AGENTS.items():
            self.assertTrue(code and len(reason.split()) >= 15, agent)
        self.assertEqual(set(scene_contracts.RETRY_REFUSED_AGENTS) | set(scene_contracts.RETRY_ADMITTED_AGENTS),
                         {*scene_contracts.SLICE_AGENTS, scene_contracts.LEGACY_AGENT})

    async def test_the_launch_options_panel_marks_the_refused_node_as_unsupported_with_the_reason(self):
        from app.orchestrator.node_spec import node_admission_problem
        for agent in ("skeleton", "camera", "action"):
            node = {"id": "n", "type": "excel_gpt", "data": {"sd_agent": agent}}
            self.assertIn("not admitted", node_admission_problem(node))
        for agent in ("characters", "world"):
            self.assertIsNone(node_admission_problem({"id": "n", "type": "excel_gpt", "data": {"sd_agent": agent}}))
        self.assertIsNone(node_admission_problem({"id": "n", "type": "excel_gpt", "data": {}}))


class RecoveryNodeAdmission(ManagedSceneFixture):
    agent = "characters"
    bad_reply = "I cannot produce JSON for this."

    async def turn_node_into(self, agent, node_type="sd_agent"):
        from app.models import Workflow, WorkflowRun
        from app.services import revisions
        nodes = [{"id": "n1", "type": node_type, "data": {"groupId": "g1", "sd_agent": agent}}, self.nodes[1]]
        version = revisions.graph_version(nodes, [])
        async with self.sessions() as master:
            (await master.get(Workflow, 9)).nodes = nodes
            (await master.get(WorkflowRun, 11)).nodes_snapshot = nodes
            (await master.get(observation.ExecutionControl, EXECUTION)).graph_version = version
            (await master.get(base_tests.ActionExecutionControl, EXECUTION)).graph_version = version
            await master.commit()
        await self.observer.reload()

    async def refused_at_recover(self, agent, code, node_type="sd_agent"):
        from app.services.actions_recovery import NodeNotAdmitted
        await self.failed_native_attempts([self.bad_reply])
        incident = await self.typed_incident()
        await self.turn_node_into(agent, node_type)
        with self.assertRaises(NodeNotAdmitted) as caught:
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key=f"refused-{agent}-0001",
                initiator="dry-run", rationale="a refused agent must never get a ticket")
        self.assertEqual(caught.exception.code, "node_not_admitted")
        named = "legacy" if node_type == "scene_design" else agent
        self.assertRegex(str(caught.exception), rf"scene agent '{named}' is not admitted .*\({code}\)")
        self.assertIn("не допущен", str(caught.exception))
        self.assertIsInstance(caught.exception, ValueError)
        self.assertIsNone(await self.ticket(), "no recovery ticket was issued")

    async def test_recover_node_refuses_skeleton(self):
        await self.refused_at_recover("skeleton", "two_stage_state")

    async def test_recover_node_refuses_camera(self):
        await self.refused_at_recover("camera", "chunked_requests")

    async def test_recover_node_refuses_action(self):
        await self.refused_at_recover("action", "chunked_requests")

    async def test_recover_node_refuses_the_legacy_scene_design_node(self):
        await self.refused_at_recover("characters", "legacy_union", node_type="scene_design")

    async def refused_at_claim(self, agent, code):
        from app.orchestrator import node_spec
        from app.services.actions_recovery import NodeNotAdmitted
        await self.failed_native_attempts([self.bad_reply])
        incident = await self.typed_incident()
        await self.decide_retry(incident)
        ticket = await self.ticket()
        self.assertEqual(ticket.status, "queued")
        bodies_before = len(sd.SCRIPT["bodies"])
        original = node_spec._NODE_ADMISSIONS["sd_agent"]

        def refuse_as(node):
            data = dict(node.get("data") or {}, sd_agent=agent)
            return original({**node, "data": data})
        node_spec.register_node_admission("sd_agent", refuse_as, replace=True)
        try:
            with self.assertRaises(NodeNotAdmitted) as caught:
                await self.worker([self.good_reply()], start=False)
        finally:
            node_spec.register_node_admission("sd_agent", original, replace=True)
        self.assertRegex(str(caught.exception), rf"scene agent '{agent}' is not admitted .*\({code}\)")
        self.assertIsInstance(caught.exception, RuntimeError)
        self.assertEqual((await self.ticket()).status, "queued", "the ticket was not consumed")
        self.assertEqual(len(sd.SCRIPT["bodies"]), bodies_before, "the retry sent no request")
        self.assertEqual(await self.facts("data_applied"), [])

    def good_reply(self):
        return json.dumps(sd.GOOD_CHARACTERS, ensure_ascii=False)

    async def test_claim_refuses_skeleton(self):
        await self.refused_at_claim("skeleton", "two_stage_state")

    async def test_claim_refuses_camera(self):
        await self.refused_at_claim("camera", "chunked_requests")

    async def test_claim_refuses_action(self):
        await self.refused_at_claim("action", "chunked_requests")

    async def test_admitted_agent_still_passes_both_checks(self):
        from app.services.actions_recovery import NodeNotAdmitted
        await self.failed_native_attempts([self.bad_reply])
        incident = await self.typed_incident()
        await self.decide_retry(incident)
        self.assertEqual((await self.ticket()).status, "queued")
        try:
            await self.worker([self.good_reply()], start=False)
        except NodeNotAdmitted:
            self.fail("an admitted agent must not be refused inside recovery")
        finish_job_tracking(7)
        self.assertNotEqual((await self.ticket()).status, "queued")


class ManagedResultGate(ManagedSceneFixture):
    agent = "characters"

    async def test_a_contract_rejected_slice_holds_the_managed_execution_and_fails_the_attempt(self):
        bad = {"characters": [{"id": "hero", "имя": "Альфа", "внешность": "высокий"}]}
        with self.assertRaisesRegex(RuntimeError, "verification not confirmed"):
            await self.worker([json.dumps(bad, ensure_ascii=False)])
        self.assertTrue(await self.held())
        applied = await self.facts("data_applied")
        self.assertEqual(len(applied), 1, "the rejected cells WERE committed: a retry is refused as a partial apply")
        self.assertIs(applied[0].payload["result"]["ok"], False)
        self.assertEqual(applied[0].payload["result"]["cells_rejected"], len(await self.cells()))

    async def test_a_disabled_scene_design_pass_through_is_held_not_verified(self):
        async with self.business() as session:
            project = await session.get(Project, 7)
            project.meta = {**project.meta, "scene_design_enabled": False}
            await session.commit()
        with self.assertRaisesRegex(RuntimeError, "verification not confirmed"):
            await self.worker([])
        self.assertTrue(await self.held())
        self.assertEqual(sd.SCRIPT["bodies"], [])


class LegacyNodeStaysUnmanaged(sd.SceneAgentFixture):
    node_type = "scene_design"
    fixture_node_type = "scene_design"
    agent = "characters"

    async def test_a_managed_execution_refuses_the_legacy_scene_node_with_its_reason(self):
        with self.assertRaisesRegex(ValueError, r"scene agent 'legacy' is not admitted .*\(legacy_union\)"):
            await self.supervisor.enable(project_id=7, execution_id=EXECUTION, group_id="g1", node_keys=["n1"],
                graph_version=self.graph, initiator="dry-run", rationale="legacy must not start managed",
                request_limit=10)
        async with self.sessions() as session:
            self.assertIsNone(await session.get(base_tests.ActionExecutionControl, EXECUTION))
        self.assertEqual(sd.SCRIPT["bodies"], [])
        await self.worker([json.dumps(sd.GOOD_CHARACTERS, ensure_ascii=False)])
        self.assertTrue(await self.cells())


if __name__ == "__main__":
    unittest.main(verbosity=2)
