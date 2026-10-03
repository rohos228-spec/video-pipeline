"""A2: per-project body quota, no per-write directory scan, retention cleanup of closed executions only."""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base

from app.services import observation
from app.services.actions_recovery import RecoveryAction
from app.services.decision_state import DecisionRecord
from app.services.diagnosis import Incident
from app.services.observation import ExecutionControl, ObservationEvent
DAY = 24 * 3600


def body(tag, size=500):
    return {"tag": tag, "pad": "x" * size}


class Fixture(unittest.IsolatedAsyncioTestCase):
    """Same wiring as test_journal.JournalAcceptance, without re-running its tests."""
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.master_engine = create_async_engine(f"sqlite+aiosqlite:///{self.root}/master.db")
        self.project_engine = create_async_engine(f"sqlite+aiosqlite:///{self.root}/project.db")
        self.master = async_sessionmaker(self.master_engine, expire_on_commit=False)
        self.project = async_sessionmaker(self.project_engine, expire_on_commit=False)
        for engine in (self.master_engine, self.project_engine):
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
                await observation.migrate_observation_schema(conn)
        self.observer = observation.Observer(self.master, body_root=self.root / "bodies")
        self.scope = await self.observer.enable(project_id=2, workflow_run_id=7,
            node_keys=["script", "report"], graph_version="synthetic-graph-sha")

    async def asyncTearDown(self):
        await self.master_engine.dispose()
        await self.project_engine.dispose()
        self.tmp.cleanup()


class BodyStore(Fixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.observer.MAX_TOTAL = 4000
        self.observer.RETENTION_SECONDS = 3 * DAY
        self.a = self.scope  # project 2
        self.b = await self.observer.enable(project_id=3, workflow_run_id=8, node_keys=["script"],
                                            graph_version="synthetic-graph-b")

    def fill(self, scope):
        """Write until the quota blocks; returns (stored refs, blocked ref)."""
        stored = []
        for i in range(100):
            ref = self.observer.save_body(scope.execution_id, body(f"{scope.execution_id}-{i}"))
            if ref.get("gap"):
                return stored, ref
            stored.append(ref)
        self.fail("quota never blocked")

    async def events(self, kind):
        async with self.master() as session:
            return (await session.scalars(select(ObservationEvent).where(
                ObservationEvent.kind == kind).order_by(ObservationEvent.id))).all()

    async def closed_execution(self, project_id, run_id, *, age_days, bodies=2, size=400):
        scope = await self.observer.enable(project_id=project_id, workflow_run_id=run_id, node_keys=["script"],
                                           graph_version=f"synthetic-{project_id}")
        refs = [self.observer.save_body(scope.execution_id, body(f"{project_id}-{i}", size)) for i in range(bodies)]
        await self.observer.stop(scope.execution_id)
        old = time.time() - age_days * DAY
        for ref in refs:
            os.utime(self.observer.body_root / ref["path"], (old, old))
        return scope, refs

    def rescan(self):
        self.observer._body_index = None  # as after a restart: mtimes come from disk

    async def test_quota_is_per_project(self):
        stored_a, blocked = self.fill(self.a)
        self.assertEqual(blocked["gap"], "body_total_limit")
        self.assertGreater(len(stored_a), 3)
        self.assertEqual(self.observer.save_body(self.a.execution_id, body("again"))["gap"], "body_total_limit")
        stored_b, blocked_b = self.fill(self.b)  # untouched by project 2 filling its quota
        self.assertEqual(len(stored_b), len(stored_a))
        usage = self.observer.body_usage()
        self.assertEqual(set(usage), {2, 3})
        self.assertLessEqual(max(usage.values()), 4000)

    async def test_identical_body_is_deduplicated_not_counted_twice(self):
        first = self.observer.save_body(self.a.execution_id, body("same"))
        before = self.observer.body_usage()[2]
        self.assertEqual(self.observer.save_body(self.a.execution_id, body("same")), first)
        self.assertEqual(self.observer.body_usage()[2], before)

    async def test_write_path_does_not_scan_the_store(self):
        self.observer.MAX_TOTAL = 10 ** 9
        self.observer.save_body(self.b.execution_id, body("warm", 10))
        scans = []
        real_scandir = os.scandir

        def counting(*args, **kwargs):
            scans.append(args)
            return real_scandir(*args, **kwargs)
        with patch.object(observation.os, "scandir", new=counting), \
                patch.object(Path, "glob", side_effect=AssertionError("glob on the write path")), \
                patch.object(Path, "rglob", side_effect=AssertionError("rglob on the write path")):
            for i in range(300):
                self.assertIn("path", self.observer.save_body(self.a.execution_id, body(i, 20)))
        self.assertEqual(scans, [])
        self.assertEqual(self.observer.body_usage()[2] > 300 * 20, True)

    async def test_index_is_rebuilt_once_after_restart_and_quota_still_holds(self):
        stored_a, _ = self.fill(self.a)
        restarted = observation.Observer(self.master, body_root=self.root / "bodies", max_total=4000)
        await restarted.reload()
        self.assertEqual(restarted.save_body(self.a.execution_id, body("after restart"))["gap"], "body_total_limit")
        self.assertIn("path", restarted.save_body(self.b.execution_id, body("other project")))
        self.assertEqual(restarted.body_usage()[2], self.observer.body_usage()[2])

    async def test_blocked_write_is_a_visible_event_and_managed_fails_closed(self):
        self.a.mode = "managed"
        self.fill(self.a)
        self.assertIn(self.a.execution_id, self.observer.failures)
        self.assertEqual(self.a.observer_state, "degraded")
        self.fill(self.b)  # observe mode: visible, but not a gate
        self.assertNotIn(self.b.execution_id, self.observer.failures)
        self.assertEqual(self.observer.save_body(self.a.execution_id, body("more"))["gap"], "body_total_limit")
        await self.observer.flush_events()
        events = await self.events("body_quota_blocked")
        self.assertEqual({e.execution_id for e in events}, {self.a.execution_id, self.b.execution_id})
        payload = next(e.payload for e in events if e.execution_id == self.a.execution_id)
        self.assertEqual((payload["limit"], payload["project_id"]), (4000, 2))
        self.assertGreater(payload["bytes"] + payload["project_bytes"], 4000)
        # repeated blocks inside the interval are counted, not flooded
        self.assertEqual(len([e for e in events if e.execution_id == self.a.execution_id]), 1)
        clock = [self.observer._clock() + 61]
        self.observer._clock = lambda: clock[0]
        self.observer.save_body(self.a.execution_id, body("later"))
        await self.observer.flush_events()
        later = [e for e in await self.events("body_quota_blocked") if e.execution_id == self.a.execution_id]
        self.assertEqual(len(later), 2)
        self.assertGreaterEqual(later[1].payload["suppressed_since_last"], 1)

    async def test_cleanup_removes_only_old_closed_unreferenced_execution_bodies(self):
        live_ref = self.observer.save_body(self.a.execution_id, body("live"))
        os.utime(self.observer.body_root / live_ref["path"], (1, 1))  # ancient but live
        old, old_refs = await self.closed_execution(4, 14, age_days=10)
        old2, _ = await self.closed_execution(5, 15, age_days=9)
        young, young_refs = await self.closed_execution(6, 16, age_days=1)
        pinned, pinned_refs = await self.closed_execution(7, 17, age_days=10)
        self.observer.pin(pinned.execution_id)
        incident, incident_refs = await self.closed_execution(8, 18, age_days=10)
        decision, decision_refs = await self.closed_execution(9, 19, age_days=10)
        recovery, recovery_refs = await self.closed_execution(10, 20, age_days=10)
        resolved, _ = await self.closed_execution(11, 21, age_days=10)
        async with self.master() as session:
            def incident_row(scope, status):
                return Incident(incident_id=str(uuid.uuid4()), execution_id=scope.execution_id,
                    project_id=scope.project_id, type="journal_gap", severity="warning", status=status, details={})
            session.add_all([
                incident_row(incident, "human_review"), incident_row(resolved, "resolved_confirmed"),
                DecisionRecord(decision_id=str(uuid.uuid4()), execution_id=decision.execution_id, project_id=9,
                    incident_id=str(uuid.uuid4()), node_key="script", source_attempt_id="a", context_version="v",
                    status="waiting_worker"),
                RecoveryAction(action_id=str(uuid.uuid4()), execution_id=recovery.execution_id, project_id=10,
                    incident_id=str(uuid.uuid4()), node_key="script", source_attempt_id="a", new_attempt_id="b",
                    idempotency_key="k", preconditions={}, budget_state={}, status="await_verify"),
            ])
            await session.commit()
        self.rescan()
        before = self.observer.body_usage()
        done = await self.observer.cleanup_bodies(force=True)
        self.assertEqual(set(done), {old.execution_id, old2.execution_id, resolved.execution_id})
        # untouched: live, young, pinned, open incident, open decision, open recovery
        for scope, refs in ((self.a, [live_ref]), (young, young_refs), (pinned, pinned_refs),
                            (incident, incident_refs), (decision, decision_refs), (recovery, recovery_refs)):
            for ref in refs:
                data = json.loads((self.observer.body_root / ref["path"]).read_text())
                self.assertNotIn("purged", data, scope.execution_id)
        # purged: the file name (journal reference) still resolves, content is a tombstone with sha and size
        for ref in old_refs:
            path = self.observer.body_root / ref["path"]
            self.assertTrue(path.is_file())
            tomb = json.loads(path.read_text())
            self.assertEqual((tomb["purged"], tomb["sha256"], tomb["bytes"]), (True, ref["sha256"], ref["bytes"]))
            self.assertLess(path.stat().st_size, ref["bytes"])
        self.assertTrue((self.observer.body_root / old.execution_id / ".purged").is_file())
        after = self.observer.body_usage()
        self.assertLess(after[4], before[4])
        self.assertEqual(after[2], before[2])
        events = await self.events("body_cleanup")
        self.assertEqual({e.execution_id for e in events}, {old.execution_id, old2.execution_id, resolved.execution_id})
        payload = next(e.payload for e in events if e.execution_id == old.execution_id)
        self.assertEqual((payload["reason"], payload["files"]), ("retention", 2))
        self.assertGreater(payload["bytes_before"], payload["bytes_after"])
        # idempotent, even after a restart rescan
        self.rescan()
        self.assertEqual(await self.observer.cleanup_bodies(force=True), [])
        self.assertEqual(len(await self.events("body_cleanup")), 3)

    async def test_cleanup_frees_quota_for_a_blocked_project(self):
        scope, refs = await self.closed_execution(4, 14, age_days=10, bodies=2)
        closed = self.observer.body_usage()[4]
        self.assertEqual(closed, 2 * refs[0]["bytes"])
        # a live execution of the same project shares its quota (attributed by hand: one open per project)
        live = str(uuid.uuid4())
        self.observer._execution_project[live] = 4
        self.observer.MAX_TOTAL = closed + 300
        self.assertEqual(self.observer.save_body(live, body("blocked", 400))["gap"], "body_total_limit")
        self.rescan()
        self.observer._execution_project[live] = 4
        await self.observer.cleanup_bodies()  # requested by the block itself, not forced
        self.assertLess(self.observer.body_usage()[4], closed)
        self.assertIn("path", self.observer.save_body(live, body("fits now", 400)))

    async def test_cleanup_is_skipped_for_executions_it_cannot_prove_closed(self):
        ref = self.observer.save_body(self.a.execution_id, body("live"))
        os.utime(self.observer.body_root / ref["path"], (1, 1))
        orphan = str(uuid.uuid4())
        orphan_ref = self.observer.save_body(orphan, body("orphan"))
        os.utime(self.observer.body_root / orphan_ref["path"], (1, 1))
        self.rescan()
        self.assertEqual(await self.observer.cleanup_bodies(force=True), [])
        for r in (ref, orphan_ref):
            self.assertNotIn("purged", json.loads((self.observer.body_root / r["path"]).read_text()))

    async def test_uncertain_commit_blocks_cleanup(self):
        scope, refs = await self.closed_execution(4, 14, age_days=10)
        self.observer.commit_uncertainty[scope.execution_id] = "business_commit_unconfirmed"
        self.rescan()
        self.assertEqual(await self.observer.cleanup_bodies(force=True), [])

    async def test_cleanup_waits_for_interval_unless_requested(self):
        scope, _ = await self.closed_execution(4, 14, age_days=10)
        self.rescan()
        clock = [100.0]
        self.observer._clock = lambda: clock[0]
        self.assertEqual(await self.observer.cleanup_bodies(), [scope.execution_id])  # first run is due
        scope2, _ = await self.closed_execution(5, 15, age_days=10)
        self.rescan()
        clock[0] += 10
        self.assertEqual(await self.observer.cleanup_bodies(), [])
        clock[0] += self.observer.CLEANUP_INTERVAL
        self.assertEqual(await self.observer.cleanup_bodies(), [scope2.execution_id])


class BodyLimitSettings(unittest.TestCase):
    def test_defaults_equal_the_old_constants_and_are_configurable(self):
        from app.settings import Settings
        default = Settings(_env_file=None)
        self.assertEqual(default.observer_body_quota_bytes, 32 * 1024 * 1024)
        self.assertEqual(default.observer_body_retention_seconds, 7 * 24 * 3600)
        custom = Settings(_env_file=None, OBSERVER_BODY_QUOTA_BYTES=1234, OBSERVER_BODY_RETENTION_SECONDS=99)
        self.assertEqual((custom.observer_body_quota_bytes, custom.observer_body_retention_seconds), (1234, 99))

    def test_observer_takes_limits_from_settings(self):
        from app.settings import settings
        previous = observation._observer
        observation._observer = None
        try:
            with patch.object(settings, "observer_body_quota_bytes", 777), \
                    patch.object(settings, "observer_body_retention_seconds", 55):
                observer = observation.get_observer()
            self.assertEqual((observer.MAX_TOTAL, observer.RETENTION_SECONDS), (777, 55))
            self.assertEqual(observation.Observer(None, body_root=Path("x")).MAX_TOTAL, 32 * 1024 * 1024)
        finally:
            observation._observer = previous


if __name__ == "__main__":
    unittest.main(verbosity=2)
