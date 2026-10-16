import json
import unittest
from contextlib import ExitStack, asynccontextmanager
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_actions_v1_contract as base_tests
from app.models import (
    Base, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, SceneDesignCell, Workflow, WorkflowRun,
)
from app.orchestrator.steps import scene_design
from app.services import observation, revisions
from app.services.diagnosis import Incident, get_diagnoser
from app.services.gpt_api import GptChatResult
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.services.observation import observe_model
from app.settings import settings

EXECUTION = "00000000-0000-4000-8000-0000000000c1"
SCRIPT = {"replies": [], "bodies": []}
VO = ("Альфа открывает книгу.", "Бета закрывает книгу.")
GOOD_CHARACTERS = {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий, около 45",
                                   "одежда": "серое пальто", "характер": "сдержанный"}]}


@observe_model
async def fake_http_post(*, url, headers, body, timeout, use_model):
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


class SceneAgentFixture(unittest.IsolatedAsyncioTestCase):
    fixture_node_type = "sd_agent"
    node_type = "sd_agent"
    agent = "characters"

    async def asyncSetUp(self):
        SCRIPT["replies"], SCRIPT["bodies"] = [], []
        await base_tests.DurableActions.asyncSetUp(self)
        self.stack = ExitStack()
        self.data = Path(self.tmp.name) / "videos" / "actions-fixture"
        self.data.mkdir(parents=True)
        for patcher in (patch.object(settings, "data_dir", Path(self.tmp.name)),
                        patch("app.services.gpt_client.gpt_ask_fresh", new=fake_ask_fresh)):
            self.stack.enter_context(patcher)
        self.observer = observation._observer

        marker = {"sd_agent": self.agent} if self.node_type == "sd_agent" else {}
        self.nodes = [{"id": "n1", "type": self.node_type, "data": {"groupId": "g1", **marker}},
                      {"id": "n2", "type": "script", "data": {"groupId": "g2"}}]
        self.graph = revisions.graph_version(self.nodes, [])
        async with self.sessions() as master:
            (await master.get(Workflow, 9)).nodes = deepcopy(self.nodes)
            (await master.get(WorkflowRun, 11)).nodes_snapshot = deepcopy(self.nodes)
            scope = await master.get(observation.ExecutionControl, "exec-durable")
            scope.execution_id, scope.node_keys, scope.graph_version = EXECUTION, ["n1", "n2"], self.graph
            project = await master.get(Project, 7)
            project.status = ProjectStatus.scene_designing
            await master.commit()

        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.data / 'project.db'}")
        self.business = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(connection)
        meta = {"canvas_graph": {"nodes": self.nodes, "edges": []}, "scene_design_enabled": True,
                "scene_design": {"only_agent": self.agent}}
        async with self.business() as seed:
            seed.add_all([
                Project(id=7, slug="actions-fixture", topic="synthetic scene design",
                        status=ProjectStatus.scene_designing, auto_mode=False, meta=deepcopy(meta)),
                Workflow(id=9, name="actions fixture", nodes=self.nodes, edges=[]),
                WorkflowRun(id=11, workflow_id=9, project_id=7, nodes_snapshot=self.nodes, edges_snapshot=[]),
                NodeRun(workflow_run_id=11, node_key="n1", node_type=self.node_type, status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11, node_key="n2", node_type="script", status=NodeRunStatus.pending),
            ])
            for number, text in enumerate(VO, start=1):
                seed.add(Frame(project_id=7, number=number, uuid=f"{number:02x}" * 16, sort_key=float(number),
                               voiceover_text=text, duration_seconds=3.0, attrs={}))
            await seed.commit()
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            project.status, project.meta = ProjectStatus.scene_designing, deepcopy(meta)
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
        await self.observer.reload()

    async def asyncTearDown(self):
        try:
            await self.engine.dispose()
            self.stack.close()
        finally:
            await base_tests.DurableActions.asyncTearDown(self)

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

    async def facts(self, kind=None):
        await self.deliver()
        async with self.sessions() as session:
            rows = (await session.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all()
        return [r for r in rows if kind is None or r.kind == kind]

    async def incidents(self):
        async with self.sessions() as session:
            return (await session.scalars(select(Incident).where(Incident.execution_id == EXECUTION))).all()

    async def cells(self):
        async with self.business() as session:
            return list((await session.scalars(select(SceneDesignCell).where(SceneDesignCell.project_id == 7)
                                               .order_by(SceneDesignCell.id))).all())

    async def worker(self, replies):
        SCRIPT["replies"] = list(replies)
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            self.assertTrue(queue_node_for_start(node, project_id=7, initiator="worker"))
            self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
            await session.commit()
            try:
                async with observation.observe_attempt(session, project):
                    await scene_design.run(session, project, None)
            except Exception as error:
                await session.rollback()
                node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
                fail_node(node, str(error)[:200], project_id=7, initiator="worker")
                node.finished_at, node.attempts = datetime.utcnow(), 1
                await session.commit()
                raise
            complete_node(node, project_id=7, initiator="worker")
            await session.commit()

    async def failed_attempt(self, replies):
        with self.assertRaises(Exception) as caught:
            await self.worker(replies)
        await self.diagnose()
        return caught.exception

    def types(self, incidents):
        return sorted(i.type for i in incidents)


class SceneAgentDryRun(SceneAgentFixture):
    agent = "characters"

    async def test_unparseable_reply_is_typed_invalid_json_with_a_request_bound_parse_fact(self):
        await self.failed_attempt(["I cannot produce JSON for this."])
        parse = (await self.facts("parse_result"))[-1]
        payload = parse.payload
        self.assertEqual((payload["contract"], payload["agent"]), ("native_scene_agent", "characters"))
        self.assertEqual((payload["payload_required"], payload["payload_present"]), (True, False))
        self.assertEqual(payload["failure_class"], "invalid_json")
        self.assertIs(payload["syntax_valid"], False)
        starts = await self.facts("model_start")
        self.assertEqual(parse.request_id, starts[-1].request_id)
        found = [i for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}]
        self.assertEqual([i.type for i in found], ["invalid_json"])
        self.assertTrue(found[0].details["currently_relevant"])
        self.assertFalse(found[0].details["evidence_incomplete"])
        self.assertEqual(await self.cells(), [])
        self.assertEqual(await self.facts("data_applied"), [], "this step writes no transactional apply fact")

    async def test_json_without_the_required_list_is_typed_missing_payload(self):
        await self.failed_attempt(['```json\n{"note": "nothing here"}\n```'])
        payload = (await self.facts("parse_result"))[-1].payload
        self.assertEqual((payload["failure_class"], payload["syntax_valid"], payload["payload_present"]),
                         ("missing_payload", True, False))
        self.assertEqual([i.type for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}],
                         ["missing_payload"])

    async def test_agent_declared_error_is_a_missing_payload_not_a_json_incident(self):
        await self.failed_attempt([json.dumps({"characters": [], "error": "нет индивидуальных персонажей"},
                                              ensure_ascii=False)])
        self.assertEqual([i.type for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}],
                         ["missing_payload"])

    async def test_a_slice_the_native_parser_accepts_but_the_contract_rejects_is_contract_rejected(self):
        bad = {"characters": [{"id": "hero", "имя": "Альфа", "внешность": "высокий"}]}
        await self.worker([json.dumps(bad, ensure_ascii=False)])
        stored = await self.cells()
        self.assertTrue(stored and all(c.status == "rejected" for c in stored), "the native converter rejects the id")
        parse = (await self.facts("parse_result"))[-1].payload
        self.assertEqual((parse["failure_class"], parse["payload_present"], parse["parsed"], parse["schema_valid"]),
                         ("contract_rejected", True, True, False))
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"], proof["agent"]),
                         ("failed", "contract_rejected", "characters"))
        self.assertEqual(proof["errors"], ["invalid_characters_slice:bad_id_pattern:hero"])
        await self.diagnose()
        types = self.types(await self.incidents())
        self.assertIn("contract_rejected", types)
        self.assertFalse(set(types) & {"invalid_json", "missing_payload"}, types)

    async def test_a_valid_slice_is_stored_verified_and_raises_no_incident(self):
        await self.worker([json.dumps(GOOD_CHARACTERS, ensure_ascii=False)])
        stored = await self.cells()
        self.assertTrue(stored and all(c.status == "ok" and c.agent == "characters" for c in stored))
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"]), ("passed", None))
        self.assertEqual(proof["agents_checked"], ["characters"])
        self.assertEqual(proof["contract"], "native_scene_agent")
        self.assertIs(proof["semantic_quality_verified"], False)
        parse = (await self.facts("parse_result"))[-1].payload
        self.assertEqual((parse["payload_present"], parse["schema_valid"], parse["failure_class"]), (True, True, None))
        await self.diagnose()
        self.assertEqual(await self.incidents(), [])

    async def test_an_observed_execution_has_no_recovery_grant_at_all(self):
        await self.failed_attempt(["I cannot produce JSON for this."])
        incident = next(i for i in await self.incidents() if i.type == "invalid_json")
        with self.assertRaisesRegex(ValueError, "managed execution disabled or held"):
            await self.supervisor.recover_node(project_id=7, execution_id=EXECUTION,
                incident_id=incident.incident_id, node_key="n1", idempotency_key="sd-retry-0001",
                initiator="dry-run", rationale="no recovery grant exists for an observed scene node")


class WorldAgentDryRun(SceneAgentFixture):
    agent = "world"

    async def test_a_world_without_places_is_a_valid_empty_slice(self):
        await self.worker([json.dumps({"locations": [], "report": "локаций:0"}, ensure_ascii=False)])
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"], proof["agent"]), ("passed", None, "world"))
        self.assertEqual(await self.cells(), [])
        parse = (await self.facts("parse_result"))[-1].payload
        self.assertEqual((parse["payload_present"], parse["failure_class"]), (True, None))
        await self.diagnose()
        self.assertEqual(await self.incidents(), [])

    async def test_a_world_location_with_a_bad_id_is_contract_rejected(self):
        await self.worker([json.dumps({"locations": [{"id": "place1", "name": "зал"}]}, ensure_ascii=False)])
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["failure_class"]), ("failed", "contract_rejected"))

    async def test_a_world_reply_without_any_list_is_missing_payload(self):
        await self.failed_attempt(['{"report": "no list"}'])
        self.assertEqual([i.type for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}],
                         ["missing_payload"])


class LegacySceneDesignDryRun(SceneAgentFixture):
    node_type = "scene_design"
    fixture_node_type = "scene_design"
    agent = "characters"

    async def test_the_legacy_node_gets_typed_parse_facts_and_a_result_check_for_the_agent_that_ran(self):
        await self.worker([json.dumps(GOOD_CHARACTERS, ensure_ascii=False)])
        parse = (await self.facts("parse_result"))[-1].payload
        self.assertEqual((parse["agent"], parse["contract"], parse["payload_present"]),
                         ("characters", "native_scene_agent", True))
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["agent"], proof["agents_checked"]),
                         ("passed", "legacy", ["characters"]))
        await self.diagnose()
        self.assertEqual(await self.incidents(), [])

    async def test_the_legacy_node_failure_is_typed_too(self):
        await self.failed_attempt(["no JSON at all"])
        self.assertEqual([i.type for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}],
                         ["invalid_json"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
