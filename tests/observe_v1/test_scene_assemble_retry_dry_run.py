"""Dry run of the sd_assemble supervised retry: observation -> diagnosis -> decision -> action -> verification.

No network, no provider, no paid call. REAL: scene_design.run_assemble (cell backfill, chronology, camera
subdivision, the chunked assembler with its chunk checkpoints, validation, apply_scene_design through
db_apply.apply_ops, the transactional `data_applied` fact, the failure handler with its rollback), the
observe_model wrapper, observe_attempt, the managed result gate (independent read session), the journal,
the Diagnoser, the DecisionSupervisor and its policy, ActionSupervisor.enable / recover_node, the claim, the
reconcile verification and the recovery cap. SCRIPTED: the model boundary only (the assembler chunk replies:
the "good" reply of a chunk is built by the application's own local assembler from the chunk's own request)
and the Decision model (canned proposals). SEEDED: the agents' checkpoints and cells (what the earlier steps
leave) and the native step-failure counter, by the application's own writers.

The two facts that made sd_assemble unmanaged are removed and proven here:
  1. a failed attempt no longer commits the camera-subdivision child frames (the failure handler rolls the
     attempt's uncommitted work back), so the input revision of the retry equals the attempt's;
  2. the child frames get a uuid derived from the parent and the shot number, so the rebuilt frames, and with
     them the chunked assembler requests, are identical across the failed attempt and the retry.
"""
import json
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select

import test_actions_v1_contract as base_tests
import test_scene_agents_dry_run as sd
import test_scene_assemble_dry_run as asm
from app.models import NodeRun, Project, ProjectStatus
from app.orchestrator.steps import scene_design
from app.services import decision_config, observation, revisions
from app.services.actions_recovery import RecoveryAction, finish_job_tracking, reconcile
from app.services.gpt_api import GptChatResult
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.services.scene_design import assembler as sd_assembler
from app.services.scene_design import camera_expand
from app.services.scene_design.agents import ASSEMBLER
from app.settings import settings

EXECUTION = sd.EXECUTION
# Two narrative scenes (one per voiceover parent): the registry ids stay unique across the assembler chunks.
ACTION = {"scenes": [
    {**asm.ACTION["scenes"][0], "id_scene": "scene_01", "start_words": "Альфа долго открывает",
     "end_words": "читает вслух первую главу.", "время_сек": 8, "цепь_действия": "открывает книгу; читает"},
    {**asm.ACTION["scenes"][0], "id_scene": "scene_02", "start_words": "Бета медленно закрывает",
     "end_words": "уходит из читального зала.", "время_сек": 8, "цепь_действия": "закрывает книгу; уходит"}]}
CAMERA = {"shot_plan": [
    asm.CAMERA["shot_plan"][0],
    {**asm.CAMERA["shot_plan"][1], "id_scene": "scene_02", "phase_index": 1}]}
GOOD = "@GOOD"
BAD = "I cannot produce JSON for this."


class ManagedAssembleFixture(asm.AssembleFixture):
    """The real-step assembly fixture plus a managed execution, the Decision layer and a smart model."""

    async def asyncSetUp(self):
        self.stack_seed = [patch.object(asm, "ACTION", ACTION), patch.object(asm, "CAMERA", CAMERA)]
        for patcher in self.stack_seed:
            patcher.start()
            self.addCleanup(patcher.stop)
        await super().asyncSetUp()
        cfg = decision_config.validate_config({'version': 1, 'mode': 'synthetic', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': sorted(decision_config.ALL_ACTIONS)})
        for patcher in (patch.object(decision_config, "runtime_config", return_value=cfg),
                        patch.object(settings, "scene_design_assemble_chunk_frames", 2),
                        patch("app.services.gpt_client.gpt_ask_fresh", new=self.smart_ask_fresh)):
            self.stack.enter_context(patcher)
        await self.supervisor.enable(project_id=7, execution_id=EXECUTION, group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="dry-run", rationale="sd_assemble dry run", request_limit=20)
        await self.observer.reload()

    # ---- the model boundary -----------------------------------------------------------------
    @staticmethod
    def chunk_input(text):
        start = text.index("# ЯЧЕЙКИ АГЕНТОВ")
        start = text.index("\n", start) + 1
        value, _ = json.JSONDecoder().raw_decode(text[start:])
        return value

    def good_chunk(self, text):
        """What a valid model would answer to THIS chunk: the application's own local assembler."""
        chunk = self.chunk_input(text)
        frames = [SimpleNamespace(uuid=kid["uuid"], voiceover_text=kid.get("закадр") or "", number=kid["number"])
                  for scene in chunk["scenes_chrono"] for kid in scene["кадры"]]
        return sd_assembler.build_local_assembler_payload(chunk, frames)

    async def smart_ask_fresh(self, text, **kwargs):
        if sd.SCRIPT["replies"] and sd.SCRIPT["replies"][0] == GOOD:
            sd.SCRIPT["replies"][0] = json.dumps(self.good_chunk(text), ensure_ascii=False)
        return await sd.fake_ask_fresh(text, **kwargs)

    # ---- the worker, as the runtime drives it (start=False: the recovery attempt) -------------
    async def run_step(self, replies, *, start=True):
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
                    await scene_design.run_assemble(session, project, None)
            except Exception:
                await session.rollback()
                raise
            complete_node(node, project_id=7, initiator="worker")
            await session.commit()

    async def exhaust_native_retries(self):
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            fail_node(node, "native step retries exhausted", project_id=7, initiator="worker")
            node.finished_at, node.attempts = datetime.utcnow(), 9
            project.status = ProjectStatus.paused
            project.meta = {**project.meta, "step_failure": {"total_fails": {"scene_assembling": 9},
                "last_running": "scene_assembling", "abandoned_at": "synthetic"}}
            await session.commit()
        await self.diagnose()

    async def failed_native_attempts(self, replies):
        with self.assertRaises(Exception) as caught:
            await self.run_step(replies)
        await self.exhaust_native_retries()
        return caught.exception

    async def typed_incident(self):
        found = [i for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}]
        self.assertEqual(len(found), 1, [(i.type, i.status) for i in await self.incidents()])
        return found[0]

    async def incidents(self):
        from app.services.diagnosis import Incident
        async with self.sessions() as session:
            return (await session.scalars(select(Incident).where(Incident.execution_id == EXECUTION))).all()

    async def facts(self, kind=None):
        await self.deliver()
        async with self.sessions() as session:
            rows = (await session.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all()
        return [r for r in rows if kind is None or r.kind == kind]

    async def ticket(self):
        async with self.sessions() as session:
            return await session.scalar(select(RecoveryAction))

    async def held(self):
        async with self.sessions() as session:
            return (await session.get(base_tests.ActionExecutionControl, EXECUTION)).held

    async def input_revision(self):
        async with self.business() as session:
            return await revisions.compute_input_revision(session, await session.get(Project, 7), "n1")

    async def frame_rows(self):
        return [(f.id, f.number, f.uuid, f.sort_key, f.voiceover_text, f.duration_seconds, f.start_ts, f.end_ts,
                 json.dumps(f.attrs, sort_keys=True, ensure_ascii=False)) for f in await self.frames()]

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

    async def decide_retry(self, incident):
        from app.services import decision_v1
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=7, execution_id=EXECUTION, enabled=True,
                             initiator="dry-run", rationale="sd_assemble dry run decision")
        record = await loop.begin(project_id=7, execution_id=EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertEqual(context["node_capability"]["node_type"], "sd_assemble")
        self.assertIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        contract = await loop.read_contract(record["decision_id"])
        self.assertEqual(contract["contract"]["agent"], "assemble")
        with self.decision_model(["read_contract", "retry_same"]):
            await loop.step(record["decision_id"])
            await loop.step(record["decision_id"])
        return record

    async def verify_ticket(self):
        await self.sync()
        await self.deliver()
        await reconcile(self.observer)
        return await self.ticket()


class SceneAssembleRetry(ManagedAssembleFixture):

    async def test_the_failed_attempt_leaves_the_input_exactly_as_it_found_it(self):
        revision, rows = await self.input_revision(), await self.frame_rows()
        self.assertEqual(len(rows), 2)
        await self.failed_native_attempts([BAD])
        self.assertEqual(await self.frame_rows(), rows, "camera subdivision was rolled back with the failed attempt")
        self.assertEqual(await self.input_revision(), revision)
        self.assertEqual(await self.facts("data_applied"), [], "nothing was applied")
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "invalid_json")
        self.assertTrue(incident.details["currently_relevant"])
        self.assertFalse(incident.details["evidence_incomplete"])

    async def test_failure_in_the_first_chunk_then_one_verified_retry_with_identical_first_request(self):
        from app.services import decision_v1
        rows_before = await self.frame_rows()
        # 1. observation + diagnosis: the failed attempt asked once (first chunk) and got no JSON.
        await self.failed_native_attempts([BAD])
        first_bodies = list(sd.SCRIPT["bodies"])
        self.assertEqual(len(first_bodies), 1)
        self.assertEqual(await self.frame_rows(), rows_before)
        incident = await self.typed_incident()
        # 2. decision -> exactly one ticket, queued for the original worker.
        record = await self.decide_retry(incident)
        ticket = await self.ticket()
        self.assertEqual(ticket.status, "queued")
        self.assertEqual(ticket.preconditions["action_type"], "retry_same")
        self.assertEqual(ticket.preconditions["effective_node_type"], "sd_assemble")
        self.assertEqual(ticket.budget_state["recovery_limit"], 1)
        async with self.sessions() as session:
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.action_id), ("waiting_worker", ticket.action_id))
        # 3. the original worker runs the retry: chunk 1 and chunk 2, both valid.
        await self.run_step([GOOD, GOOD], start=False)
        finish_job_tracking(7)
        retry_bodies = sd.SCRIPT["bodies"][len(first_bodies):]
        self.assertEqual(len(retry_bodies), 2, "two chunk requests")
        self.assertEqual(retry_bodies[0], first_bodies[0], "the first request of the retry equals the failed one")
        self.assertNotEqual(retry_bodies[0], retry_bodies[1])
        # 4. data applied exactly once: four frames, no duplicate children.
        frames = await self.frames()
        self.assertEqual(len(frames), 4)
        self.assertEqual(len({f.uuid for f in frames}), 4)
        children = [f for f in frames
                    if int(((f.attrs or {}).get("camera_subdivide") or {}).get("shot_index") or 1) > 1]
        parents = [f for f in frames if f not in children]
        self.assertEqual((len(parents), len(children)), (2, 2))
        self.assertEqual({f.uuid for f in children},
                         {camera_expand.derived_shot_uuid(p.uuid, 2) for p in parents})
        self.assertEqual(" ".join(" ".join(f.voiceover_text for f in frames).split()), " ".join(asm.VO).strip())
        applied = await self.facts("data_applied")
        self.assertEqual(len(applied), 1)
        self.assertIs(applied[0].payload["transactional"], True)
        self.assertTrue(applied[0].payload["result"], "the result of the original apply_ops")
        self.assertEqual(applied[0].db_scope, "project")
        # 5. independent verification of the result.
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"], proof["agent"]), ("passed", None, "assemble"))
        async with self.business() as session:
            project = await session.get(Project, 7)
            registry = project.meta.get("scene_registry")
            self.assertTrue(registry)
            self.assertEqual(project.status, ProjectStatus.scene_design_ready)
        for frame in await self.frames():
            self.assertTrue((frame.attrs or {}).get("shot01_id_scene"), frame.uuid)
            self.assertIn((frame.attrs or {}).get("shot01_id_scene"), {sc["id_scene"] for sc in registry})
        ticket = await self.verify_ticket()
        self.assertEqual(ticket.status, "verified", ticket.verification_result)
        checks = ticket.verification_result
        self.assertTrue(checks["data_applied"] and checks["accepted_payload"] and checks["result_confirmed"])
        self.assertTrue(checks["node_done"] and checks["transactional_done"] and checks["requests_complete"])
        self.assertFalse(await self.held())
        # 6. cap: a competing recovery is refused, the database refuses a second ticket.
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
        self.assertEqual(len(sd.SCRIPT["bodies"]), 3, "one failed request plus two chunk requests, nothing else")

    async def test_failure_in_the_second_chunk_drops_the_chunk_results_and_replays_the_same_requests(self):
        from app.services.scene_design import runner
        rows_before = await self.frame_rows()
        await self.failed_native_attempts([GOOD, BAD])
        first_bodies = list(sd.SCRIPT["bodies"])
        self.assertEqual(len(first_bodies), 2)
        self.assertEqual(await self.frame_rows(), rows_before, "chunk 1 succeeded, still no business row changed")
        async with self.business() as session:
            project = await session.get(Project, 7)
            self.assertIsNone(runner.load_chunk_checkpoint(project, ASSEMBLER, "p1"),
                              "a managed failure drops the chunk results so the repeat starts like the attempt")
        incident = await self.typed_incident()
        await self.decide_retry(incident)
        await self.run_step([GOOD, GOOD], start=False)
        finish_job_tracking(7)
        retry_bodies = sd.SCRIPT["bodies"][len(first_bodies):]
        self.assertEqual(retry_bodies, first_bodies, "the retry replays the failed attempt's request sequence")
        self.assertEqual(len(await self.frames()), 4)
        self.assertEqual(len(await self.facts("data_applied")), 1)
        ticket = await self.verify_ticket()
        self.assertEqual(ticket.status, "verified", ticket.verification_result)

    async def test_an_unobserved_native_failure_keeps_the_chunk_results_for_the_next_native_attempt(self):
        from app.services.scene_design import runner
        sd.SCRIPT["replies"] = [GOOD, BAD]
        async with self.business() as session:
            project = await session.get(Project, 7)
            with self.assertRaises(Exception):
                await scene_design.run_assemble(session, project, None)
        async with self.business() as session:
            project = await session.get(Project, 7)
            self.assertIsNotNone(runner.load_chunk_checkpoint(project, ASSEMBLER, "p1"))
            self.assertEqual(len(await self.frames()), 2, "the subdivision is rolled back in every run")

    async def test_json_without_scenes_is_a_missing_payload_and_retried_the_same_way(self):
        await self.failed_native_attempts(['{"report": "no list here"}'])
        incident = await self.typed_incident()
        self.assertEqual(incident.type, "missing_payload")
        await self.decide_retry(incident)
        await self.run_step([GOOD, GOOD], start=False)
        finish_job_tracking(7)
        ticket = await self.verify_ticket()
        self.assertEqual(ticket.status, "verified", ticket.verification_result)

    async def test_retry_is_refused_when_the_failed_attempt_applied_data(self):
        await self.failed_native_attempts([BAD])
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

    async def test_profile_retry_with_an_unapproved_overlay_is_refused_without_a_ticket(self):
        # W5 D05: sd_assemble is profile compatible, so the capability no longer denies a profile retry; an unapproved overlay is still refused and no ticket is issued
        await self.failed_native_attempts([BAD])
        incident = await self.typed_incident()
        with self.assertRaisesRegex(ValueError, "execution grant|profile outside|unknown or incompatible"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="profile-retry-0001",
                initiator="dry-run", rationale="profile retry is not an assembly capability",
                profile_overlay_id="no-such-overlay")
        self.assertIsNone(await self.ticket())

    async def test_a_successful_first_attempt_applies_once_and_passes_the_managed_gate(self):
        await self.run_step([GOOD, GOOD])
        self.assertEqual(len(await self.frames()), 4)
        self.assertEqual(len(await self.facts("data_applied")), 1)
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual(proof["status"], "passed")
        self.assertFalse(await self.held())

    async def test_subdivision_children_have_a_derived_identity(self):
        a, b = camera_expand.derived_shot_uuid("ab" * 12, 2), camera_expand.derived_shot_uuid("ab" * 12, 2)
        self.assertEqual(a, b)
        self.assertEqual(len(a), 24)
        self.assertNotEqual(a, camera_expand.derived_shot_uuid("ab" * 12, 3))
        self.assertNotEqual(a, camera_expand.derived_shot_uuid("cd" * 12, 2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
