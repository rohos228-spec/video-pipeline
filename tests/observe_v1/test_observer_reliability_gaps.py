"""A1: observer degradation is isolated per execution and reversible without erasing the fact."""
from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base

from app.services import observation
from app.services.observation import ExecutionControl, ObservationDegradation, ObservationEvent

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


class ObserverRecovery(Fixture):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.a = self.scope
        self.b = await self.observer.enable(project_id=3, workflow_run_id=8, node_keys=["script"],
                                            graph_version="synthetic-graph-b")
        self.now = [1000.0]
        self.observer._clock = lambda: self.now[0]
        self.observer.RECOVERY_CLEAN_PASSES = 3
        self.observer.RECOVERY_MIN_SECONDS = 10

    def consume_fails_for(self, execution_id):
        real = self.observer.consume
        self.calls = {}

        async def consume(eid, db_scope, factory):
            self.calls[eid] = self.calls.get(eid, 0) + 1
            if eid == execution_id:
                raise OSError("database is locked")
            return await real(eid, db_scope, factory)
        return patch.object(self.observer, "consume", new=consume)

    async def stored(self, execution_id):
        async with self.master() as session:
            return await session.get(ExecutionControl, execution_id)

    async def kinds(self, execution_id):
        async with self.master() as session:
            return [e.kind for e in (await session.scalars(select(ObservationEvent).where(
                ObservationEvent.execution_id == execution_id).order_by(ObservationEvent.id))).all()]

    async def degradations(self):
        async with self.master() as session:
            return (await session.scalars(select(ObservationDegradation).order_by(ObservationDegradation.id))).all()

    async def degrade_a(self):
        """One failed pass, then a successful one that makes the gap durable."""
        with self.consume_fails_for(self.a.execution_id):
            await self.observer.catch_up()
        await self.observer.catch_up()

    async def test_failure_marks_only_its_own_execution_and_others_keep_delivering(self):
        async with self.master() as session:
            self.observer.stage(session, self.b, "node_status", db_scope="master", node_key="script",
                                attempt_id="attempt-b", payload={"status": "done"})
            await session.commit()
        with self.consume_fails_for(self.a.execution_id):
            await self.observer.catch_up()
        self.assertIn(self.a.execution_id, self.observer.failures)
        self.assertNotIn(self.b.execution_id, self.observer.failures)
        self.assertEqual(self.a.observer_state, "degraded")
        self.assertEqual(self.b.observer_state, "active")
        self.assertIn("node_status", await self.kinds(self.b.execution_id))
        self.assertEqual((await self.stored(self.b.execution_id)).gaps, [])

    async def test_gap_is_durable_visible_and_blocks_until_recovered(self):
        await self.degrade_a()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual(stored.observer_state, "degraded")
        self.assertEqual(len(stored.gaps), 1)
        self.assertTrue(stored.gaps[0]["recoverable"])
        self.assertEqual(stored.gaps[0]["error_type"], "OSError")
        self.assertNotIn(self.a.execution_id, self.observer.failures)
        self.assertEqual(self.a.observer_state, "degraded")  # still fail-closed
        rows = await self.degradations()
        self.assertEqual([(r.execution_id, r.error_type, r.attempts) for r in rows],
                         [(self.a.execution_id, "OSError", 1)])
        self.assertIsNone(rows[0].cleared_at)
        self.assertIn("observer_degraded", await self.kinds(self.a.execution_id))
        self.assertEqual(await self.kinds(self.b.execution_id), [])

    async def test_recovery_needs_clean_passes_and_time_then_keeps_history(self):
        await self.degrade_a()
        for _ in range(4):  # enough passes, but no time has elapsed
            await self.observer.catch_up()
        self.assertEqual((await self.stored(self.a.execution_id)).observer_state, "degraded")
        self.assertEqual(len((await self.stored(self.a.execution_id)).gaps), 1)
        self.now[0] += 11
        await self.observer.catch_up()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual((stored.observer_state, stored.gaps), ("active", []))
        self.assertEqual((self.a.observer_state, self.a.gaps), ("active", []))
        rows = await self.degradations()
        self.assertEqual(len(rows), 1)  # the fact is kept, only closed
        self.assertIsNotNone(rows[0].cleared_at)
        self.assertTrue(rows[0].cleared_reason.startswith("consumer_clean_"))
        kinds = await self.kinds(self.a.execution_id)
        self.assertEqual([k for k in kinds if k.startswith("observer_")], ["observer_degraded", "observer_recovered"])

    async def test_repeated_failure_resets_recovery_and_does_not_duplicate_the_gap(self):
        await self.degrade_a()
        await self.observer.catch_up()
        await self.observer.catch_up()
        self.now[0] += 11
        with self.consume_fails_for(self.a.execution_id):
            await self.observer.catch_up()
        await self.observer.catch_up()
        await self.observer.catch_up()  # would have recovered without the second failure
        stored = await self.stored(self.a.execution_id)
        self.assertEqual(stored.observer_state, "degraded")
        self.assertEqual(len(stored.gaps), 1)
        self.assertEqual((await self.degradations())[0].attempts, 2)

    async def test_failing_execution_is_retried_with_backoff_not_every_pass(self):
        with self.consume_fails_for(self.a.execution_id):
            await self.observer.catch_up()
            await self.observer.catch_up()  # second try is immediate
            self.assertEqual(self.calls[self.a.execution_id], 2)
            await self.observer.catch_up()  # now backing off: not called
            self.assertEqual(self.calls[self.a.execution_id], 2)
            self.now[0] += 1.1
            await self.observer.catch_up()
            self.assertEqual(self.calls[self.a.execution_id], 3)
            await self.observer.catch_up()
            self.assertEqual(self.calls[self.a.execution_id], 3)
            self.now[0] += 2.1
            await self.observer.catch_up()
            self.assertEqual(self.calls[self.a.execution_id], 4)
            # b is never delayed by a's backoff
            self.assertGreaterEqual(self.calls[self.b.execution_id], 6)

    async def test_gap_from_lost_data_is_never_cleared_automatically(self):
        self.observer.mark_gap(self.a.execution_id, RuntimeError("event could not be staged"))
        await self.observer.catch_up()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual(len(stored.gaps), 1)
        self.assertNotIn("recoverable", stored.gaps[0])
        for _ in range(10):
            self.now[0] += 60
            await self.observer.catch_up()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual((stored.observer_state, len(stored.gaps)), ("degraded", 1))
        self.assertNotIn("observer_recovered", await self.kinds(self.a.execution_id))

    async def test_recovery_closes_only_recoverable_gaps_and_stays_degraded_with_a_permanent_one(self):
        await self.degrade_a()
        self.observer.mark_gap(self.a.execution_id, RuntimeError("event could not be staged"))
        await self.observer.catch_up()
        self.assertEqual(len((await self.stored(self.a.execution_id)).gaps), 2)
        for _ in range(4):
            self.now[0] += 11
            await self.observer.catch_up()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual(len(stored.gaps), 1)
        self.assertNotIn("recoverable", stored.gaps[0])
        self.assertEqual(stored.observer_state, "degraded")
        self.assertEqual(self.a.observer_state, "degraded")
        self.assertIsNotNone((await self.degradations())[0].cleared_at)

    async def test_gap_and_recovery_survive_restart(self):
        await self.degrade_a()
        restarted = observation.Observer(self.master, body_root=self.root / "bodies")
        restarted._clock = lambda: self.now[0]
        restarted.RECOVERY_CLEAN_PASSES, restarted.RECOVERY_MIN_SECONDS = 3, 10
        await restarted.reload()
        scope = next(s for s in restarted.scopes.values() if s.execution_id == self.a.execution_id)
        self.assertEqual((scope.observer_state, len(scope.gaps)), ("degraded", 1))
        for _ in range(4):
            self.now[0] += 11
            await restarted.catch_up()
        stored = await self.stored(self.a.execution_id)
        self.assertEqual((stored.observer_state, stored.gaps), ("active", []))
        self.assertEqual(scope.observer_state, "active")

    async def test_loop_stage_failure_marks_no_execution_and_other_stages_keep_running(self):
        counts = {"consumer": 0, "decision": 0, "reconcile": 0}

        async def consumer():
            counts["consumer"] += 1

        async def decide():
            counts["decision"] += 1

        async def reconcile():
            counts["reconcile"] += 1
            raise RuntimeError("synthetic reconcile failure")

        async def nothing():
            return None
        self.observer.LOOP_INTERVAL = 0.01
        with patch.object(self.observer, "reload", new=nothing), patch.object(self.observer, "catch_up", new=consumer), \
                patch.object(self.observer, "_diagnose", new=nothing), patch.object(self.observer, "_reconcile", new=reconcile), \
                patch.object(self.observer, "_decide", new=decide), patch.object(self.observer, "maintain", new=nothing):
            task = asyncio.create_task(self.observer.loop())
            await asyncio.sleep(0.3)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertGreater(counts["consumer"], 3)
        self.assertGreater(counts["decision"], 3)
        self.assertEqual(counts["reconcile"], 1)  # backing off
        self.assertEqual(self.observer.failures, {})
        self.assertEqual((self.a.observer_state, self.b.observer_state), ("active", "active"))
        self.assertEqual(self.observer.stage_errors, {"reconcile": "RuntimeError"})
        self.assertEqual((await self.stored(self.a.execution_id)).gaps, [])

    async def test_stage_recovers_and_its_history_row_is_closed(self):
        async def broken():
            raise OSError("synthetic")

        async def fine():
            return None
        await self.observer._run_stage("reconcile", broken)
        rows = await self.degradations()
        self.assertEqual([(r.execution_id, r.stage, r.cleared_at) for r in rows], [("", "reconcile", None)])
        await self.observer._run_stage("reconcile", fine)  # inside backoff: skipped
        self.assertEqual(self.observer.stage_errors, {"reconcile": "OSError"})
        self.now[0] += 2
        await self.observer._run_stage("reconcile", fine)
        self.assertEqual(self.observer.stage_errors, {})
        rows = await self.degradations()
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0].cleared_at)


if __name__ == "__main__":
    unittest.main(verbosity=2)
