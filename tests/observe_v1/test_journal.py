"""Offline acceptance contracts for the durable observe journal.

Run in the prepared image with --network none, not against customer databases.
unittest is deliberate: the existing runtime image needs no extra packages.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base
from app.services.observation import (
    ExecutionControl,
    ObservationEvent,
    ObservationOutbox,
    Observer,
    redact,
)


class JournalAcceptance(unittest.IsolatedAsyncioTestCase):
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
        self.observer = Observer(self.master, body_root=self.root / "bodies")
        self.scope = await self.observer.enable(
            project_id=2, workflow_run_id=7,
            node_keys=["script", "report"], graph_version="synthetic-graph-sha",
        )

    async def asyncTearDown(self):
        await self.master_engine.dispose()
        await self.project_engine.dispose()
        self.tmp.cleanup()

    async def event_count(self):
        async with self.master() as session:
            return len((await session.scalars(select(ObservationEvent))).all())

    async def test_same_transaction_rollback_does_not_deliver_success(self):
        async with self.project() as session:
            self.observer.stage(
                session, self.scope, "data_applied", db_scope="project",
                node_key="script", attempt_id="attempt-1", payload={"updated": 1},
            )
            await session.flush()
            await session.rollback()
        await self.observer.consume(self.scope.execution_id, "project", self.project)
        self.assertEqual(await self.event_count(), 0)
        async with self.project() as session:
            self.assertEqual(len((await session.scalars(select(ObservationOutbox))).all()), 0)

    async def test_restart_replay_has_persisted_cursor_and_no_duplicates(self):
        async with self.project() as session:
            self.observer.stage(
                session, self.scope, "node_status", db_scope="project",
                node_key="script", attempt_id="attempt-1", payload={"status": "done"},
            )
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "project", self.project)
        restarted = Observer(self.master, body_root=self.root / "bodies")
        await restarted.consume(self.scope.execution_id, "project", self.project)
        self.assertEqual(await self.event_count(), 1)
        async with self.master() as session:
            stored = await session.get(ExecutionControl, self.scope.execution_id)
            self.assertGreater(stored.cursors.get("project", 0), 0)

    async def test_catchup_after_restart_reads_committed_but_undelivered_event(self):
        async with self.project() as session:
            self.observer.stage(
                session, self.scope, "artifact_created", db_scope="project",
                node_key="report", attempt_id="attempt-2", payload={"bytes": 120},
            )
            await session.commit()
        self.assertEqual(await self.event_count(), 0)
        restarted = Observer(self.master, body_root=self.root / "bodies")
        await restarted.consume(self.scope.execution_id, "project", self.project)
        self.assertEqual(await self.event_count(), 1)

    async def test_scope_rejects_another_project_and_node(self):
        async with self.project() as session:
            for pid, node in ((1, "script"), (2, "video")):
                accepted = self.observer.stage(
                    session, self.scope, "node_status", db_scope="project",
                    project_id=pid, node_key=node, payload={"status": "running"},
                )
                self.assertFalse(accepted)
            await session.commit()
        await self.observer.consume(self.scope.execution_id, "project", self.project)
        self.assertEqual(await self.event_count(), 0)

    async def test_redaction_preserves_numeric_usage_and_removes_credentials(self):
        body = {"Authorization": "Bearer synthetic-secret-123", "api_key": "synthetic-key",
                "GPT_API_KEY": "synthetic-provider-key", "X-API-Key": "synthetic-header-key",
                "Cookie": "session=synthetic-cookie", "access_token": "synthetic-access",
                "usage": {"input_tokens": 12, "output_tokens": 9, "total_tokens": 21},
                "nested": [{"message": "Authorization: Bearer synthetic-secret-123"}],
                "text": '{"api_key":"synthetic-json-key"}'}
        sanitized = redact(body)
        self.assertEqual(sanitized["usage"], body["usage"])
        for secret in ("synthetic-secret-123", "synthetic-key", "synthetic-cookie", "synthetic-access",
                       "synthetic-provider-key", "synthetic-header-key", "synthetic-json-key"):
            self.assertNotIn(secret, json.dumps(sanitized))

    async def test_body_files_are_bounded_and_do_not_expose_secret(self):
        reference = self.observer.save_body(
            self.scope.execution_id, {"api_key": "synthetic-key", "usage": {"total_tokens": 21}},
        )
        contents = (self.root / "bodies" / reference["path"]).read_text()
        self.assertNotIn("synthetic-key", contents)
        self.assertIn("21", contents)
        self.assertEqual(len(reference["sha256"]), 64)
        oversized = self.observer.save_body(self.scope.execution_id, "x" * (1024 * 1024 + 1))
        self.assertTrue(oversized["gap"])

    async def test_sources_and_correlations_are_preserved(self):
        for source, factory in (("master", self.master), ("project", self.project)):
            async with factory() as session:
                self.observer.stage(
                    session, self.scope, "model_end", db_scope=source,
                    node_key="script", attempt_id="attempt-1", request_id="request-1",
                    payload={"usage": {"total_tokens": 21}},
                )
                await session.commit()
            await self.observer.consume(self.scope.execution_id, source, factory)
        async with self.master() as session:
            rows = (await session.scalars(select(ObservationEvent))).all()
            self.assertEqual({r.db_scope for r in rows}, {"master", "project"})
            self.assertEqual({r.attempt_id for r in rows}, {"attempt-1"})
            self.assertEqual({r.request_id for r in rows}, {"request-1"})
            self.assertEqual({r.workflow_run_id for r in rows}, {7})

    async def test_stopping_observer_does_not_claim_business_failure(self):
        await self.observer.stop(self.scope.execution_id)
        async with self.master() as session:
            scope = await session.get(ExecutionControl, self.scope.execution_id)
            self.assertEqual(scope.observer_state, "stopped")
            self.assertNotIn("business_outcome", scope.__dict__)


if __name__ == "__main__":
    unittest.main(verbosity=2)
