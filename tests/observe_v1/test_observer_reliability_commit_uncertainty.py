"""A3: an unconfirmed business commit is persisted, restored after restart and cleared only by a person."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, NodeRun, NodeRunStatus, Project, Workflow, WorkflowRun
from app.services import observation
from app.services.diagnosis import Diagnoser, Incident
from app.services.node_status_machine import start_node_running
from app.services.observation import CommitUncertaintyMarker, ExecutionControl, ObservationEvent
from app.web.routers import observations

REASON = "business_commit_unconfirmed"


class Restartable(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.master_engine = create_async_engine(f"sqlite+aiosqlite:///{self.root}/master.db")
        self.master = async_sessionmaker(self.master_engine, expire_on_commit=False)
        async with self.master_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(conn)
        self.observer = self.new_observer()
        self.scope = await self.observer.enable(project_id=2, workflow_run_id=7, node_keys=["script"],
                                                graph_version="synthetic-graph-sha")
        self.eid = self.scope.execution_id

    def new_observer(self):
        """A fresh process: nothing in memory, same databases and body store."""
        return observation.Observer(self.master, body_root=self.root / "bodies")

    async def asyncTearDown(self):
        await self.master_engine.dispose()
        self.tmp.cleanup()

    async def markers(self):
        async with self.master() as session:
            return (await session.scalars(select(CommitUncertaintyMarker).order_by(CommitUncertaintyMarker.id))).all()

    async def incidents(self):
        async with self.master() as session:
            return (await session.scalars(select(Incident))).all()

    async def mark_and_persist(self, observer=None):
        observer = observer or self.observer
        observer.note_commit_uncertainty(self.eid, REASON)
        await asyncio.gather(*observer._tasks)
        await observer.persist_uncertainty()


class CommitUncertaintyPersistence(Restartable):
    async def test_marker_is_persisted_and_restored_after_restart(self):
        await self.mark_and_persist()
        rows = await self.markers()
        self.assertEqual([(r.execution_id, r.project_id, r.reason, r.cleared_at) for r in rows],
                         [(self.eid, 2, REASON, None)])
        restarted = self.new_observer()
        self.assertNotIn(self.eid, restarted.commit_uncertainty)
        await restarted.reload()
        self.assertEqual(restarted.commit_uncertainty, {self.eid: REASON})

    async def test_noting_twice_keeps_one_open_row(self):
        await self.mark_and_persist()
        await self.mark_and_persist()
        self.assertEqual(len(await self.markers()), 1)

    async def test_failed_write_is_kept_and_retried_by_the_loop_maintenance(self):
        with patch.object(self.observer, "master", side_effect=OSError("database is locked")):
            self.observer.note_commit_uncertainty(self.eid, REASON)
            await asyncio.gather(*self.observer._tasks)
            await self.observer.persist_uncertainty()
        self.assertEqual(self.observer.commit_uncertainty, {self.eid: REASON})  # still blocking
        self.assertEqual(self.observer._uncertainty_unsaved, {self.eid: REASON})
        self.assertEqual(await self.markers(), [])
        await self.observer.maintain()
        self.assertEqual(len(await self.markers()), 1)
        self.assertEqual(self.observer._uncertainty_unsaved, {})

    async def test_restart_keeps_the_journal_gap_open_instead_of_closing_it_as_not_observed(self):
        await self.mark_and_persist()
        await Diagnoser(self.observer).update(self.eid, snapshot={})
        before = await self.incidents()
        self.assertEqual([(i.type, i.status) for i in before], [("journal_gap", before[0].status)])
        self.assertNotEqual(before[0].status, "resolved_confirmed")
        # restart; the diagnoser runs before anything else has reloaded the observer
        restarted = self.new_observer()
        await Diagnoser(restarted).update(self.eid, snapshot={})
        after = await self.incidents()
        self.assertEqual(len(after), 1)
        self.assertNotEqual(after[0].status, "resolved_confirmed")
        self.assertTrue(after[0].details.get("currently_relevant"))
        self.assertNotEqual((after[0].details.get("resolution") or {}).get("kind"), "condition_not_observed")
        self.assertIn(self.eid, restarted.commit_uncertainty)

    async def test_without_a_persisted_marker_the_old_failure_mode_is_reproducible(self):
        """Control: memory-only marker (what the code did before) is closed as not observed after restart."""
        self.observer.commit_uncertainty[self.eid] = REASON  # set directly, never persisted
        await Diagnoser(self.observer).update(self.eid, snapshot={})
        await Diagnoser(self.new_observer()).update(self.eid, snapshot={})
        rows = await self.incidents()
        self.assertEqual(rows[0].status, "resolved_confirmed")
        self.assertEqual(rows[0].details["resolution"]["kind"], "condition_not_observed")


class CommitUncertaintyClearing(Restartable):
    async def test_clear_is_audited_keeps_history_and_survives_restart(self):
        await self.mark_and_persist()
        self.assertTrue(await self.observer.clear_commit_uncertainty(self.eid, initiator="web_operator:1",
                                                                     reason="checked the project database by hand"))
        self.assertNotIn(self.eid, self.observer.commit_uncertainty)
        rows = await self.markers()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0].cleared_at)
        self.assertEqual((rows[0].cleared_by, rows[0].clear_reason),
                         ("web_operator:1", "checked the project database by hand"))
        async with self.master() as session:
            events = (await session.scalars(select(ObservationEvent).where(ObservationEvent.kind == "observer_control"))).all()
        self.assertTrue(any(e.payload.get("action") == "commit_uncertainty_cleared" for e in events))
        self.assertFalse(await self.observer.clear_commit_uncertainty(self.eid, initiator="x", reason="again"))
        restarted = self.new_observer()
        await restarted.reload()
        self.assertEqual(restarted.commit_uncertainty, {})
        # a later uncertainty is a new row, the old one stays as history
        await self.mark_and_persist(restarted)
        self.assertEqual([r.cleared_at is None for r in await self.markers()], [False, True])

    async def test_after_clear_the_journal_gap_resolves(self):
        await self.mark_and_persist()
        diagnoser = Diagnoser(self.observer)
        await diagnoser.update(self.eid, snapshot={})
        await self.observer.clear_commit_uncertainty(self.eid, initiator="op", reason="verified manually")
        await diagnoser.update(self.eid, snapshot={})
        self.assertEqual((await self.incidents())[0].status, "resolved_confirmed")

    async def test_endpoint_clears_for_the_right_project_only(self):
        await self.mark_and_persist()
        app = FastAPI()
        app.include_router(observations.router, prefix="/api")
        previous = observation._observer
        observation._observer = self.observer
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
                url = f"/api/projects/{{}}/observations/{self.eid}/commit-uncertainty/clear"
                self.assertEqual((await client.post(url.format(1), json={"reason": "checked by hand"})).status_code, 404)
                self.assertEqual((await client.post(url.format(2), json={"reason": "x"})).status_code, 422)
                self.assertIn(self.eid, self.observer.commit_uncertainty)
                ok = await client.post(url.format(2), json={"reason": "checked by hand"})
                self.assertEqual((ok.status_code, ok.json()), (200, {"cleared": True, "business_action": None}))
        finally:
            observation._observer = previous
        self.assertEqual(self.observer.commit_uncertainty, {})
        self.assertIsNotNone((await self.markers())[0].cleared_by)


class CommitUncertaintyFromRealHook(Restartable):
    async def test_shared_commit_failure_persists_marker_and_survives_restart(self):
        async with self.master() as session:
            session.add_all([Project(id=2, slug="synthetic-observe", topic="synthetic"),
                Workflow(id=3, name="synthetic", nodes=[{"id": "script", "type": "excel_gpt"}], edges=[])])
            await session.flush()
            session.add(WorkflowRun(id=7, project_id=2, workflow_id=3, nodes_snapshot=[{"id": "script"}],
                                    edges_snapshot=[]))
            await session.flush()
            session.add(NodeRun(workflow_run_id=7, node_key="script", node_type="excel_gpt"))
            await session.commit()
        previous = observation._observer
        observation._observer = self.observer
        try:
            async with self.master_engine.begin() as conn:
                await conn.execute(text("DROP TABLE observation_outbox"))
            async with self.master() as session:
                node = (await session.scalars(select(NodeRun))).one()
                start_node_running(node, project_id=2, initiator="worker")
                with self.assertRaises(Exception):
                    await session.commit()
                await session.rollback()
            self.assertEqual(self.observer.commit_uncertainty[self.eid], REASON)
            self.assertIn(self.eid, self.observer.failures)
            await asyncio.gather(*self.observer._tasks)
            await self.observer.persist_uncertainty()
        finally:
            observation._observer = previous
        self.assertEqual([(r.execution_id, r.reason) for r in await self.markers()], [(self.eid, REASON)])
        restarted = self.new_observer()
        await restarted.reload()
        self.assertEqual(restarted.commit_uncertainty, {self.eid: REASON})
        async with self.master() as session:
            self.assertEqual((await session.scalars(select(NodeRun))).one().status, NodeRunStatus.pending)


if __name__ == "__main__":
    unittest.main(verbosity=2)
