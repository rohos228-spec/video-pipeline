"""Real status machine, startup migration and durable observation REST API."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import inspect, select

from app.models import NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
from app.services import observation
from app.services.node_status_machine import complete_node, start_node_running
from app.web.routers import observations
import test_journal


class RuntimeAcceptance(test_journal.JournalAcceptance):
    async def asyncSetUp(self):
        await super().asyncSetUp()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
