"""Real status machine, startup migration and durable observation REST API."""
from __future__ import annotations

import unittest
import asyncio
import sys
import os
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import inspect, select, text

from app.models import NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
from app.services import observation
from app.services.node_status_machine import complete_node, start_node_running
from app.web.routers import observations
import test_journal


class RuntimeAcceptance(test_journal.JournalAcceptance):
    async def test_pending_native_approval_visible_after_done_and_clears_on_real_decision(self):
        from app.models import HITLRequest, HITLKind, HITLDecision
        async with self.master() as session:
            node=(await session.scalars(select(NodeRun))).one()
            start_node_running(node,project_id=2,initiator='worker')
            complete_node(node,project_id=2)
            req=HITLRequest(project_id=2,kind=HITLKind.approve_plan)
            other=HITLRequest(project_id=2,kind=HITLKind.approve_script)
            session.add_all([req,other]);await session.flush()
            req_id=req.id
            self.observer.stage(session,self.scope,'node_start',db_scope='master',
                node_key='script',attempt_id='native-attempt',payload={})
            self.observer.stage(session,self.scope,'human_approval_pending',db_scope='master',
                node_key='script',attempt_id='native-attempt',payload={'hitl_request_id':req_id,
                    'kind':'approve_plan','reason':'подтверждение плана человеком','initiator':'original executor'})
            await session.commit()
        await self.observer.consume(self.scope.execution_id,'master',self.master)
        async with self.master() as session:
            # Context's ordinary timeline page remains bounded at 500. An
            # older pending approval must still be read by exact scoped ID.
            session.add_all([ObservationEvent(event_id='approval-noise-'+str(i),
                execution_id=self.scope.execution_id,project_id=2,workflow_run_id=7,
                node_key='report',attempt_id='noise',db_scope='master',kind='runtime_check',payload={})
                for i in range(510)])
            await session.commit()
        url=f'/api/projects/2/observations/{self.scope.execution_id}/context'
        context=(await self.client.get(url)).json()
        self.assertEqual(context['wait_reasons']['script']['hitl_request_id'],req_id)
        self.assertEqual(context['master']['nodes']['script']['status'],'done')
        async with self.master() as session:
            req=await session.get(HITLRequest,req_id)
            self.assertEqual(req.decision,HITLDecision.pending,'read must not approve HITL')
            req.decision=HITLDecision.approved
            await session.commit()
        self.assertNotIn('script',(await self.client.get(url)).json()['wait_reasons'])

    async def test_real_worker_wait_is_scoped_and_not_guessed_from_status(self):
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key": "script"}
            observation.stage_project_fact(session, project, "runtime_wait", {"reason":"queue_predecessor", "blocking_project_id":1, "initiator":"worker"})
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        response=await self.client.get(f"/api/projects/2/observations/{self.scope.execution_id}/context")
        self.assertEqual(response.json()["wait_reasons"]["script"]["reason"], "queue_predecessor")

    async def test_actual_json_repair_records_performed_not_semantic_success(self):
        from app.services.db_apply import repair_apply_ops_json_text
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key":"script"}
            async with observation.observe_attempt(session,project):
                self.assertEqual(repair_apply_ops_json_text('{""order":1}'), '{"order":1}')
            await session.commit()
        await self.observer.consume(self.scope.execution_id,"master",self.master)
        async with self.master() as session:
            rows=(await session.scalars(select(observation.ObservationEvent))).all()
            self.assertTrue(any(r.kind=="runtime_check" and r.payload.get("check")=="json_text_repair" and r.payload.get("changed") for r in rows))

    async def test_retention_never_deletes_old_acceptance_bodies_and_can_pin(self):
        old = self.observer.save_body(self.scope.execution_id, {"acceptance": "old"})
        path = self.observer.body_root / old["path"]
        os.utime(path, (1, 1))
        self.observer.pin(self.scope.execution_id)
        self.observer.save_body(self.scope.execution_id, {"acceptance": "new"})
        self.assertTrue(path.exists())
        self.assertTrue(self.observer.is_pinned(self.scope.execution_id))

    async def test_known_initiator_survives_actual_fsm_transaction(self):
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        async with self.master() as session:
            row = (await session.scalars(select(observation.ObservationEvent))).one()
            self.assertTrue(any(t["initiator"] == "worker" for t in row.payload["transitions"]))

    async def test_real_write_contract_is_observed_without_new_validation(self):
        from app.services.node_write_contract import filter_ops_for_node
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key": "script"}
            async with observation.observe_attempt(session, project):
                result = filter_ops_for_node([{"target": "frame", "fields": {"img_pr": "synthetic"}}], node_kind="excel_gpt")
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        async with self.master() as session:
            rows = (await session.scalars(select(observation.ObservationEvent))).all()
            self.assertTrue(any(r.kind == "runtime_check" and r.payload["check"] == "write_contract" for r in rows))

    async def test_failed_consumer_preserves_done_and_user_stop_then_drains_once(self):
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"user_stop": True}
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            complete_node(node, project_id=2)
            await session.commit()
        with patch.object(self.observer, "consume", side_effect=OSError("synthetic consumer failure")):
            await self.observer.catch_up()
        self.assertIn(self.scope.execution_id, self.observer.failures)
        await self.observer.catch_up()
        count = await self.event_count()
        await self.observer.catch_up()
        self.assertEqual(await self.event_count(), count)
        async with self.master() as session:
            self.assertTrue((await session.get(Project, 2)).meta["user_stop"])
            self.assertEqual((await session.scalars(select(NodeRun))).one().status, NodeRunStatus.done)
            self.assertTrue((await session.get(observation.ExecutionControl, self.scope.execution_id)).gaps)

    async def test_shared_outbox_failure_marks_commit_unconfirmed(self):
        async with self.master_engine.begin() as conn:
            await conn.execute(text("DROP TABLE observation_outbox"))
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            with self.assertRaises(Exception):
                await session.commit()
            await session.rollback()
        self.assertIn(self.scope.execution_id, self.observer.failures)
        self.assertEqual(self.observer.commit_uncertainty[self.scope.execution_id], "business_commit_unconfirmed")
        async with self.master() as session:
            self.assertEqual((await session.scalars(select(NodeRun))).one().status, NodeRunStatus.pending)

    async def test_panel_api_removes_raw_bodies_credentials_and_absolute_paths(self):
        async with self.master() as session:
            self.observer.stage(session, self.scope, "runtime_check", db_scope="master", node_key="script",
                payload={"body": {"path": "relative/hash.json"}, "path": "C:\\private\\secret.txt",
                         "text": "raw body must stay local", "api_key": "synthetic-secret"})
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        response = await self.client.get(f"/api/projects/2/observations/{self.scope.execution_id}/events")
        self.assertNotIn("private", response.text)
        self.assertNotIn("raw body must stay local", response.text)
        self.assertNotIn("synthetic-secret", response.text)

    async def asyncSetUp(self):
        await super().asyncSetUp()
        # ASGITransport skips lifespan; use the same additive migration as
        # app startup instead of depending on model import order in create_all.
        for engine in (self.master_engine, self.project_engine):
            async with engine.begin() as conn:
                await observation.migrate_observation_schema(conn)
        self.previous = observation._observer
        observation._observer = self.observer
        async with self.master() as session:
            session.add_all([
                Project(id=2, slug="synthetic-observe", topic="synthetic"),
                Workflow(id=3, name="synthetic", nodes=[{"id": "script", "type": "excel_gpt"},
                    {"id": "report", "type": "excel_gpt"}], edges=[]),
            ])
            await session.flush()
            session.add(WorkflowRun(id=7, project_id=2, workflow_id=3,
                nodes_snapshot=[{"id": "script"}, {"id": "report"}], edges_snapshot=[]))
            await session.flush()
            session.add(NodeRun(workflow_run_id=7, node_key="script", node_type="excel_gpt"))
            await session.commit()
        self.app = FastAPI()
        self.app.include_router(observations.router, prefix="/api")
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://localhost")

    async def asyncTearDown(self):
        await self.client.aclose()
        observation._observer = self.previous
        await super().asyncTearDown()

    async def test_real_machine_status_is_journaled_and_replay_api_is_durable(self):
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            await session.commit()
            complete_node(node, project_id=2)
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        url = f"/api/projects/2/observations/{self.scope.execution_id}/events"
        first = await self.client.get(url)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(len(first.json()["events"]), 2)
        observation._observer = observation.Observer(self.master, body_root=self.root / "bodies")
        await observation._observer.reload()
        again = await self.client.get(url)
        self.assertEqual(first.json(), again.json())
        outside = await self.client.get(url.replace("/projects/2/", "/projects/1/"))
        self.assertEqual(outside.status_code, 404)

    async def test_observer_off_does_not_change_machine_or_record_events(self):
        await self.observer.stop(self.scope.execution_id)
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            complete_node(node, project_id=2)
            await session.commit()
            self.assertEqual(node.status, NodeRunStatus.done)
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        self.assertEqual(await self.event_count(), 0)

    async def test_real_machine_rollback_leaves_no_success_event(self):
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            start_node_running(node, project_id=2, initiator="worker")
            await session.flush()
            await session.rollback()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        self.assertEqual(await self.event_count(), 0)

    async def test_api_validates_scope_and_never_starts_business_work(self):
        await self.observer.stop(self.scope.execution_id)
        response = await self.client.post("/api/projects/2/observations", json={"node_keys": ["video"]})
        self.assertEqual(response.status_code, 422)
        response = await self.client.post("/api/projects/2/observations", json={"node_keys": ["script", "report"]})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["mode"], "observe")
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            self.assertEqual(node.status, NodeRunStatus.pending)

    async def test_worker_attempt_scope_records_begin_end_and_is_read_only(self):
        from app.services.observation import observe_attempt
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key": "script"}
            await session.commit()
            async with observe_attempt(session, project):
                pass
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "master", self.master)
        async with self.master() as session:
            rows = (await session.scalars(select(observation.ObservationEvent))).all()
            self.assertEqual([row.kind for row in rows], ["node_start", "node_end"])
            self.assertEqual(len({row.attempt_id for row in rows}), 1)
            self.assertIsNotNone(rows[0].attempt_id)

    async def test_body_write_error_is_observer_gap_not_node_failure(self):
        from app.services.observation import emit, observe_attempt
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key": "script"}
            with patch.object(self.observer, "save_body", side_effect=OSError("synthetic disk error")):
                async with observe_attempt(session, project):
                    emit("node_input", body={"test": True})
            await session.commit()
        self.assertEqual(self.observer.failures[self.scope.execution_id], "OSError")
        async with self.master() as session:
            node = (await session.scalars(select(NodeRun))).one()
            self.assertEqual(node.status, NodeRunStatus.pending)

    async def test_project_startup_creates_observer_tables_without_test_create_all(self):
        from app.project_db import get_project_engine, init_project_db
        directory = self.root / "startup-synthetic"
        await init_project_db(directory)
        engine = await get_project_engine(directory)
        async with engine.connect() as conn:
            names = await conn.run_sync(lambda sync: inspect(sync).get_table_names())
        self.assertIn("observation_outbox", names)
        await engine.dispose()

    async def test_force_kill_preserves_written_stream_and_marks_missing_end(self):
        # Storage/crash probe, separately from the six-node worker acceptance.
        # Kill only the child started by this test; no model/HTTP operation occurs.
        ready = self.root / "child-ready"
        program = '''
import asyncio, sys
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.services import observation as o
async def main():
    eng = create_async_engine("sqlite+aiosqlite:///" + sys.argv[1])
    factory = async_sessionmaker(eng, expire_on_commit=False)
    observer = o.Observer(factory, body_root=Path(sys.argv[1]).parent / "bodies")
    async with factory() as s:
        scope = await s.get(o.ExecutionControl, sys.argv[2])
    project_engine = create_async_engine("sqlite+aiosqlite:///" + sys.argv[3])
    async with async_sessionmaker(project_engine)() as project_session:
        o._attempt.set(o.AttemptContext(observer, scope, project_session, "project", "script", "attempt-crash"))
        await o.record_fact("model_start", {"model": "synthetic"}, request_id="request-crash")
        await o.record_fact("model_stream_event", {"type": "response.output_text.delta", "complete": False},
            request_id="request-crash", body={"event": {"delta": "synthetic partial"}, "accumulated_text": "synthetic partial"})
        Path(sys.argv[4]).write_text("ready")
        await asyncio.Event().wait()
asyncio.run(main())
'''
        child = await asyncio.create_subprocess_exec(sys.executable, "-c", program,
            str(self.root / "master.db"), self.scope.execution_id, str(self.root / "project.db"), str(ready))
        try:
            for _ in range(100):
                if ready.exists():
                    break
                await asyncio.sleep(0.05)
            self.assertTrue(ready.exists(), "Synthetic producer failed before writing its events")
        finally:
            if child.returncode is None:
                child.kill()
            await child.wait()
        restarted = observation.Observer(self.master, body_root=self.root / "bodies")
        await restarted.consume(self.scope.execution_id, "master", self.master)
        response = await self.client.get(f"/api/projects/2/observations/{self.scope.execution_id}/context")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(any(g["kind"] == "requests_without_recorded_end" for g in response.json()["data_gaps"]))
        async with self.master() as session:
            rows = (await session.scalars(select(observation.ObservationEvent))).all()
            self.assertEqual([r.kind for r in rows], ["model_start", "model_stream_event"])
            body = rows[-1].payload["body"]
            self.assertIn("synthetic partial", (self.root / "bodies" / body["path"]).read_text())

    async def test_stop_prevents_further_attempt_events_but_drains_committed_tail(self):
        from app.services.observation import emit, observe_attempt
        async with self.master() as session:
            project = await session.get(Project, 2)
            project.meta = {"active_excel_gpt_node_key": "script"}
            await session.commit()
            async with observe_attempt(session, project):
                await self.observer.stop(self.scope.execution_id)
                emit("must_not_be_recorded", {"synthetic": True})
            await session.commit()
        await self.observer.catch_up()
        async with self.master() as session:
            rows = (await session.scalars(select(observation.ObservationEvent))).all()
            self.assertEqual([r.kind for r in rows], ["node_start"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
