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
import test_scene_agents_dry_run as sd
from app.models import (
    Base, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun,
)
from app.orchestrator.steps import scene_design
from app.services import decision_config, observation, revisions
from app.services.diagnosis import Incident, get_diagnoser
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.services.scene_design import cells as sd_cells
from app.services.scene_design import runner
from app.settings import settings

EXECUTION = sd.EXECUTION
VO = ("Альфа долго открывает старую книгу и читает вслух первую главу.",
      "Бета медленно закрывает книгу и уходит из читального зала.")
CHARACTERS = {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий", "одежда": "пальто",
                              "характер": "сдержанный"}]}
WORLD = {"locations": [{"id": "loc01", "name": "Зал", "описание": "высокие окна"}]}
ACTION = {"scenes": [{"id_scene": "scene_01", "start_words": "Альфа долго открывает",
                      "end_words": "уходит из читального зала.", "время_сек": 16,
                      "структура_сцены": "continuity", "тип_стыка": "none", "переход_в_сцену": "cut",
                      "цепь_действия": "открывает книгу; читает; закрывает", "смысл_сцены": "чтение",
                      "location": "loc01", "кто_в_кадре": "c01"}]}
CAMERA = {"shot_plan": [
    {"id_scene": "scene_01", "phase_index": 1, "beat": "открывает", "цитата": "Альфа долго открывает старую книгу",
     "крупность": "VLS→MS→CU", "особенность_сцены": "Интерьер", "композиция": "центр", "угол": "ровный",
     "движение": "статика", "переход": "cut", "тип_стыка": "none", "набор": "", "мотив": "книга",
     "кто_в_кадре": "c01"},
    {"id_scene": "scene_01", "phase_index": 2, "beat": "закрывает", "цитата": "Бета медленно закрывает книгу",
     "крупность": "MS→CU", "особенность_сцены": "Интерьер", "композиция": "центр", "угол": "ровный",
     "движение": "статика", "переход": "cut", "тип_стыка": "none", "набор": "", "мотив": "книга",
     "кто_в_кадре": "c01"}]}


class AssembleFixture(unittest.IsolatedAsyncioTestCase):
    fixture_node_type = "sd_assemble"

    async def asyncSetUp(self):
        sd.SCRIPT["replies"], sd.SCRIPT["bodies"] = [], []
        await base_tests.DurableActions.asyncSetUp(self)
        self.stack = ExitStack()
        self.data = Path(self.tmp.name) / "videos" / "actions-fixture"
        self.data.mkdir(parents=True)
        for patcher in (patch.object(settings, "data_dir", Path(self.tmp.name)),
                        patch("app.services.gpt_client.gpt_ask_fresh", new=sd.fake_ask_fresh)):
            self.stack.enter_context(patcher)
        self.observer = observation._observer
        self.nodes = [{"id": "n1", "type": "sd_assemble", "data": {"groupId": "g1", "sd_agent": "assemble"}},
                      {"id": "n2", "type": "script", "data": {"groupId": "g2"}}]
        self.graph = revisions.graph_version(self.nodes, [])
        async with self.sessions() as master:
            (await master.get(Workflow, 9)).nodes = deepcopy(self.nodes)
            (await master.get(WorkflowRun, 11)).nodes_snapshot = deepcopy(self.nodes)
            scope = await master.get(observation.ExecutionControl, "exec-durable")
            scope.execution_id, scope.node_keys, scope.graph_version = EXECUTION, ["n1", "n2"], self.graph
            (await master.get(Project, 7)).status = ProjectStatus.scene_assembling
            await master.commit()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.data / 'project.db'}")
        self.business = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(connection)
        meta = {"canvas_graph": {"nodes": self.nodes, "edges": []}, "scene_design_enabled": True}
        async with self.business() as seed:
            seed.add_all([
                Project(id=7, slug="actions-fixture", topic="synthetic scene assembly",
                        status=ProjectStatus.scene_assembling, auto_mode=False, meta=deepcopy(meta),
                        script_text=" ".join(VO)),
                Workflow(id=9, name="actions fixture", nodes=self.nodes, edges=[]),
                WorkflowRun(id=11, workflow_id=9, project_id=7, nodes_snapshot=self.nodes, edges_snapshot=[]),
                NodeRun(workflow_run_id=11, node_key="n1", node_type="sd_assemble", status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11, node_key="n2", node_type="script", status=NodeRunStatus.pending),
            ])
            for number, text in enumerate(VO, start=1):
                seed.add(Frame(project_id=7, number=number, uuid=f"{number:02x}" * 16, sort_key=float(number),
                               voiceover_text=text, duration_seconds=8.0, attrs={}))
            await seed.commit()
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            project.status, project.meta = ProjectStatus.scene_assembling, deepcopy(meta)
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
        from app.services import db_v2
        full_vo = "\n".join(VO)
        async with self.business() as session:
            project = await session.get(Project, 7)
            await db_v2.backfill_project_v2(session, project)
            for name, data in (("characters", CHARACTERS), ("world", WORLD), ("action", ACTION), ("camera", CAMERA)):
                runner.save_checkpoint(project, name, data)
            await session.commit()
            for name, data in (("characters", CHARACTERS), ("world", WORLD), ("action", ACTION), ("camera", CAMERA)):
                await sd_cells.store_cells(session, project, name,
                                           sd_cells.slice_to_cells(project, name, data, full_vo))
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

    async def frames(self):
        async with self.business() as session:
            return list((await session.scalars(select(Frame).where(Frame.project_id == 7)
                                               .order_by(Frame.sort_key, Frame.number))).all())

    async def worker(self, replies):
        sd.SCRIPT["replies"] = list(replies)
        async with self.business() as session:
            project = await session.get(Project, 7)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            self.assertTrue(queue_node_for_start(node, project_id=7, initiator="worker"))
            self.assertTrue(start_node_running(node, project_id=7, initiator="worker"))
            await session.commit()
            try:
                async with observation.observe_attempt(session, project):
                    await scene_design.run_assemble(session, project, None)
            except Exception as error:
                await session.rollback()
                node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
                fail_node(node, str(error)[:200], project_id=7, initiator="worker")
                node.finished_at, node.attempts = datetime.utcnow(), 9
                project = await session.get(Project, 7)
                project.status = ProjectStatus.paused
                project.meta = {**project.meta, "step_failure": {"total_fails": {"scene_assembling": 9},
                    "last_running": "scene_assembling", "abandoned_at": "synthetic"}}
                await session.commit()
                raise
            complete_node(node, project_id=7, initiator="worker")
            await session.commit()


class SceneAssembleDryRun(AssembleFixture):

    async def incidents(self):
        async with self.sessions() as session:
            return (await session.scalars(select(Incident).where(Incident.execution_id == EXECUTION))).all()

    async def test_the_step_writes_child_frames_before_any_request_but_a_failure_rolls_them_back(self):
        before = await self.frames()
        self.assertEqual(len(before), 2)
        with self.assertRaises(Exception):
            await self.worker(["I cannot produce JSON for this.", "still no json"])
        after = await self.frames()
        self.assertEqual([(f.id, f.uuid, f.voiceover_text, f.attrs) for f in before],
                         [(f.id, f.uuid, f.voiceover_text, f.attrs) for f in after],
                         "the camera subdivision of the failed attempt was rolled back")
        self.assertGreaterEqual(len(sd.SCRIPT["bodies"]), 1)
        await self.diagnose()
        incidents = [i for i in await self.incidents() if i.type in {"invalid_json", "missing_payload"}]
        self.assertEqual([i.type for i in incidents], ["invalid_json"], "the failure itself is typed like the agents'")
        self.assertEqual(await self._facts("data_applied"), [], "nothing was applied")

    async def _facts(self, kind):
        await self.deliver()
        async with self.sessions() as session:
            rows = (await session.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION))).all()
        return [r for r in rows if r.kind == kind]


if __name__ == "__main__":
    unittest.main(verbosity=2)
