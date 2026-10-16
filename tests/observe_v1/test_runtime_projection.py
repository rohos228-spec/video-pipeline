from __future__ import annotations
from app.services import revisions

import tempfile
import unittest
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db, project_db
from app.models import (
    Base, Frame, HITLDecision, HITLKind, HITLRequest, NodeRun, NodeRunStatus,
    Project, ProjectStatus, Workflow, WorkflowRun,
)
from app.orchestrator.graph.planner import load_graph_for_project
from app.orchestrator.steps import make_plan
from app.services import observation
from app.services import media_view, profiles_v1
from app.services.actions_v1 import clear_permission
from app.services.db_apply import apply_ops
from app.services.managed_launch import validate_scope
from app.services.node_capabilities import image_prompt_input_snapshot, verify_image_prompts
from app.services.node_status_machine import start_node_running
from app.services.run_sync import (
    complete_active_node_for_step, ensure_run_for_project,
    mark_running_node_failed, sync_run_for_project,
)
from app.settings import settings
from app.web.routers import observations, projects


PLAN = ("Синтетический план: человек входит в комнату, находит папку, читает "
        "записку и возвращает её на стол. Камера следует за движением героя. " * 6).strip()
PROMPT = "A synthetic room, a person reading a note, camera framing and lighting. " * 6


class RuntimeProjection(unittest.IsolatedAsyncioTestCase):
    project_id = 71

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(settings, "data_dir", self.root))
        self.patches.enter_context(patch.object(settings, "sqlite_path", self.root / "master.db"))
        self.patches.enter_context(patch.object(settings, "telegram_enabled", False))
        self.patches.enter_context(patch.object(settings, "harness_gate_disabled", True))
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.root / 'master.db'}")
        self.addAsyncCleanup(self.engine.dispose)
        self.master = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches.enter_context(patch.object(db, "SessionLocal", self.master))
        self.observer = observation.Observer(self.master, body_root=self.root / "bodies")
        self.patches.enter_context(patch.object(observation, "_observer", self.observer))
        self.addCleanup(clear_permission, self.project_id)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        app = FastAPI()
        app.include_router(projects.router, prefix="/api")
        app.include_router(observations.router, prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)

    async def seed(self, node_type="plan"):
        self.nodes = [
            {"id": "native", "type": node_type, "data": {"groupId": "native-group"}},
            {"id": "outside", "type": "images" if node_type == "image_prompts" else "script",
             "data": {"groupId": "outside-group"}},
        ]
        self.edges = [{"id": "after-native", "source": "native", "target": "outside"}]
        async with self.master() as session:
            project = Project(id=self.project_id, slug="runtime-projection", topic="synthetic",
                              meta={"canvas_graph": {"nodes": self.nodes, "edges": self.edges}})
            session.add(project)
            session.add(Workflow(id=91, name="synthetic runtime projection", is_default=True,
                                 nodes=self.nodes, edges=self.edges))
            await session.flush()
            self.run_id = await ensure_run_for_project(self.project_id, 91, session=session)
            await session.commit()
            self.directory = project.data_dir
            await project_db.migrate_single_project_data(session, project, create_backup=False)
        self.project = await project_db.get_project_sessionmaker(self.directory)
        db_key = str(project_db.resolve_project_db_path(self.directory).resolve())

        async def close_own_project():
            engine = project_db._ENGINES.pop(db_key, None)
            project_db._SESSION_MAKERS.pop(db_key, None)
            if engine is not None:
                await engine.dispose()

        self.addAsyncCleanup(close_own_project)
        prior_directory = project_db._PROJECT_DIR_CACHE.get(self.project_id)
        project_db.register_project_data_dir(self.project_id, self.directory)

        def restore_own_directory():
            project_db.unregister_project_data_dir(self.project_id)
            if prior_directory is not None:
                project_db.register_project_data_dir(self.project_id, prior_directory)

        self.addCleanup(restore_own_directory)
        self.scope = await self.observer.enable(project_id=self.project_id, workflow_run_id=self.run_id,
            node_keys=["native"], graph_version=revisions.graph_version(self.nodes, self.edges))
        self.base = f"/api/projects/{self.project_id}"
        self.context_url = f"{self.base}/observations/{self.scope.execution_id}/context"

    async def start_native(self, status, *, push=True):
        async with self.master() as session:
            project = await session.get(Project, self.project_id)
            project.status = status
            node = await session.scalar(select(NodeRun).where(
                NodeRun.workflow_run_id == self.run_id, NodeRun.node_key == "native"))
            self.assertTrue(start_node_running(node, project_id=self.project_id, initiator="worker"))
            await session.commit()
            if push:
                await project_db.push_runtime_to_project_db(session, project)

    async def finish_plan(self, *, push):
        await self.seed()
        await self.start_native(ProjectStatus.planning)
        runner = AsyncMock(return_value=SimpleNamespace(plan_text=PLAN))
        async with self.project() as session:
            project = await session.get(Project, self.project_id)
            with patch.object(make_plan.xsr, "run_plan_xlsx", runner):
                async with observation.observe_attempt(session, project):
                    await make_plan.run(session, project, None)
            await complete_active_node_for_step(session, project,
                prev_status=ProjectStatus.planning, new_status=project.status)
            await sync_run_for_project(self.project_id, session=session)
            await session.commit()
            if push:
                await project_db.push_runtime_to_master(session, project)
        runner.assert_awaited_once()
        await self.deliver_fixture_events()

    async def deliver_fixture_events(self):
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        await self.observer.consume(self.scope.execution_id, "project", self.project)

    async def read_json(self, url):
        response = await self.client.get(url)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_native_plan_content_and_pending_hitl_are_authoritative_without_copied_run_status(self):
        await self.finish_plan(push=True)
        async with self.master() as session:
            master_project = await session.get(Project, self.project_id)
            self.assertEqual(master_project.status, ProjectStatus.plan_ready)
            self.assertIsNone(master_project.general_plan, "runtime projection does not replicate content")
            self.assertIsNone(await session.scalar(select(HITLRequest.id)),
                              "native HITL rows belong to the project DB")
        async with self.project() as session:
            project = await session.get(Project, self.project_id)
            run = await session.get(WorkflowRun, self.run_id)
            graph = await load_graph_for_project(session, project)
            self.assertEqual(project.general_plan, PLAN)
            self.assertEqual(graph.node_type("native"), "plan")
            self.assertEqual(graph.derived_node_states(project)["native"], NodeRunStatus.done)
            self.assertEqual(graph.derived_node_states(project)["outside"], NodeRunStatus.pending)
            derived_status = run.status.value
            approval = (await session.scalars(select(HITLRequest))).one()
            approval_id = approval.id
            self.assertEqual((approval.kind, approval.decision), (HITLKind.approve_plan, HITLDecision.pending))
        context = await self.read_json(self.context_url)
        self.assertEqual(context["project"]["workflow_status"], derived_status)
        self.assertEqual(context["project"]["nodes"]["native"]["status"], "done")
        self.assertEqual(context["wait_reasons"]["native"]["hitl_request_id"], approval_id)
        self.assertEqual(context["wait_reasons"]["native"]["decision"], "pending")
        self.assertEqual((await self.read_json(self.base))["general_plan"], PLAN)
        async with self.project() as session:
            approval = await session.get(HITLRequest, approval_id)
            self.assertEqual(approval.decision, HITLDecision.pending)
            approval.decision = HITLDecision.approved
            await session.commit()
        self.assertNotIn("native", (await self.read_json(self.context_url))["wait_reasons"])
        async with self.project() as session:
            outside = await session.scalar(select(NodeRun).where(NodeRun.node_key == "outside"))
            self.assertEqual((outside.status, outside.attempts), (NodeRunStatus.pending, 0))

    async def test_read_during_native_completion_does_not_restore_stale_master_planning(self):
        await self.finish_plan(push=False)
        before = await self.read_json(self.context_url)
        self.assertEqual(before["master"]["project_status"], "planning")
        self.assertEqual(before["project"]["project_status"], "plan_ready")
        self.assertEqual(before["project"]["nodes"]["native"]["status"], "done")
        approval_id = before["wait_reasons"]["native"]["hitl_request_id"]
        detail = await self.read_json(self.base)
        run = await self.read_json(f"{self.base}/run")
        after = await self.read_json(self.context_url)
        self.assertEqual(detail["general_plan"], PLAN)
        self.assertEqual({n["node_key"]: n["status"] for n in run["node_runs"]},
                         {"native": "done", "outside": "pending"})
        self.assertEqual(after["wait_reasons"]["native"]["hitl_request_id"], approval_id)
        self.assertEqual(after["project"]["project_status"], "plan_ready",
                         "GET must preserve committed native completion while master push is pending")
        self.assertEqual(detail["status"], "plan_ready")

    async def test_unstarted_project_still_accepts_a_fresh_master_running_command(self):
        await self.seed()
        await self.start_native(ProjectStatus.planning, push=False)
        async with project_db.project_db_session_scope(self.project_id) as session:
            project = await session.get(Project, self.project_id)
            self.assertEqual(project.status, ProjectStatus.new)
            self.assertTrue(await project_db.pull_master_runtime_into_project(session, project))
            self.assertEqual(project.status, ProjectStatus.planning)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "native"))
            self.assertEqual(node.status, NodeRunStatus.pending,
                             "default pull imports the command, not master NodeRun projections")

    async def test_completed_native_plan_accepts_newer_master_start_or_higher_attempt(self):
        await self.finish_plan(push=False)
        node_query = select(NodeRun.__table__).where(
            NodeRun.workflow_run_id == self.run_id).order_by(NodeRun.node_key)
        hitl_query = select(HITLRequest.__table__).where(
            HITLRequest.project_id == self.project_id).order_by(HITLRequest.id)
        business_query = select(Project.general_plan, Project.script_text, Project.topic).where(
            Project.id == self.project_id)
        async with self.project() as session:
            nodes_before = [dict(row) for row in (await session.execute(node_query)).mappings()]
            hitl_before = [dict(row) for row in (await session.execute(hitl_query)).mappings()]
            business_before = tuple((await session.execute(business_query)).one())
        completed = next(row for row in nodes_before if row["node_key"] == "native")
        self.assertEqual(completed["status"], NodeRunStatus.done)
        self.assertIsNotNone(completed["started_at"])
        self.assertIsNotNone(completed["finished_at"])
        self.assertEqual(business_before[0], PLAN)
        self.assertEqual([(row["kind"], row["decision"]) for row in hitl_before],
                         [(HITLKind.approve_plan, HITLDecision.pending)])
        command_query = select(NodeRun).where(
            NodeRun.workflow_run_id == self.run_id, NodeRun.node_key == "native")
        async with self.master() as session:
            command = await session.scalar(command_query)
            self.assertEqual(command.status, NodeRunStatus.running)
            old_started_at, old_attempts = command.started_at, command.attempts
        self.assertIsNotNone(old_started_at)
        self.assertLessEqual(old_started_at, completed["started_at"])
        self.assertLessEqual(old_attempts, completed["attempts"])
        commands = (
            ("newer_started_at", completed["finished_at"] + timedelta(seconds=1), old_attempts),
            ("higher_attempts", old_started_at, completed["attempts"] + 1),
        )
        for reason, started_at, attempts in commands:
            with self.subTest(command=reason):
                async with self.master() as session:
                    command = await session.scalar(command_query)
                    command.started_at = started_at
                    command.attempts = attempts
                    await session.commit()
                async with self.project() as session:
                    project = await session.get(Project, self.project_id)
                    self.assertEqual(project.status, ProjectStatus.plan_ready)
                    self.assertTrue(await project_db.pull_master_runtime_into_project(session, project))
                    self.assertEqual(project.status, ProjectStatus.planning)
                    self.assertEqual(await session.scalar(select(Project.__table__.c.status).where(
                        Project.id == self.project_id)), ProjectStatus.planning)
                    self.assertEqual(tuple((await session.execute(business_query)).one()), business_before)
                    self.assertEqual([dict(row) for row in (await session.execute(hitl_query)).mappings()],
                                     hitl_before)
                    self.assertEqual([dict(row) for row in (await session.execute(node_query)).mappings()],
                                     nodes_before)
                    await session.rollback()

    async def test_rejected_image_prompts_context_and_managed_validation_read_native_node_rows(self):
        await self.seed("image_prompts")
        await self.start_native(ProjectStatus.generating_image_prompts)
        async with self.project() as session:
            project = await session.get(Project, self.project_id)
            session.add_all([
                Frame(project_id=self.project_id, number=1, uuid="af" * 16,
                      voiceover_text="Первая исходная строка.", attrs={}),
                Frame(project_id=self.project_id, number=2, uuid="bf" * 16,
                      voiceover_text="Вторая исходная строка.", attrs={}),
            ])
            await session.commit()
            frames = list(await session.scalars(select(Frame).order_by(Frame.number)))
            snapshot = image_prompt_input_snapshot(frames, project_id=self.project_id)
            with self.assertRaisesRegex(RuntimeError, "native image prompt rejection"):
                async with observation.observe_attempt(session, project):
                    await apply_ops(session, project, [{"frame_uuid": "af" * 16,
                        "fields": {"промт_картинки": PROMPT}}], node_kind="img_pr")
                    await session.commit()
                    frames = list(await session.scalars(select(Frame).order_by(Frame.number)))
                    proof = verify_image_prompts(frames, project_id=self.project_id, snapshot=snapshot)
                    self.assertEqual(proof["status"], "failed")
                    self.assertTrue(proof["voiceover_preserved"])
                    await observation.record_fact("node_result_verification", proof)
                    raise RuntimeError("native image prompt rejection: " + proof["reason"])
            await mark_running_node_failed(session, project, "native image prompt rejection", initiator="worker")
            await sync_run_for_project(self.project_id, session=session)
            await session.commit()
        await self.deliver_fixture_events()
        before = await self.read_json(self.context_url)
        self.assertEqual(before["master"]["nodes"]["native"]["status"], "running")
        self.assertEqual(before["project"]["nodes"]["native"]["status"], "failed")
        self.assertEqual(before["business_group_outcome"], "failed")
        self.assertTrue(before["disagreement"], "report stale projection without treating it as native success")
        self.assertEqual(before["latest_problem"]["kind"], "node_error")
        run_dto = await self.read_json(f"{self.base}/run")
        self.assertEqual({n["node_key"]: n["status"] for n in run_dto["node_runs"]},
                         {"native": "failed", "outside": "pending"})
        async with self.project() as session:
            project = await session.get(Project, self.project_id)
            run = await session.get(WorkflowRun, self.run_id)
            rows = list(await session.scalars(select(NodeRun).where(NodeRun.workflow_run_id == self.run_id)))
            with self.assertRaisesRegex(ValueError, "fresh pending nodes"):
                validate_scope(run, project, rows, ["native"])
            frames = list(await session.scalars(select(Frame).order_by(Frame.number)))
            self.assertEqual([f.image_prompt for f in frames], [PROMPT, None])
            self.assertEqual([f.voiceover_text for f in frames],
                             ["Первая исходная строка.", "Вторая исходная строка."])
            self.assertIsNone(await session.scalar(select(HITLRequest.id)))
        after = await self.read_json(self.context_url)
        self.assertEqual(after["project"]["nodes"], before["project"]["nodes"])
        self.assertEqual(after["business_group_outcome"], "failed")


if __name__ == "__main__":
    unittest.main(verbosity=2)
