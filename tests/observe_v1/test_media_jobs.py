"""Durable media gates, tested with real SQLite and the common ActionSupervisor."""
import asyncio
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, Workflow, WorkflowRun, NodeRun, NodeRunStatus
from app.services import observation
from app.services.observation import ExecutionControl, digest, AttemptContext
from app.services.actions_v1 import ActionSupervisor, clear_permission, reload_permissions


def policy(origin="http://127.0.0.1:18766"):
    return {"version": 1, "mode": "synthetic", "online_enabled": False,
            "endpoint": None, "decision": None, "profiles": {}, "allowed_actions": [],
            "media": {"enabled": True, "provider": "kie", "origin": origin,
                      "download_origins": [origin], "model_ids": ["z-image", "wan-2-7-t2v"],
                      "max_bytes": 1048576, "timeout_s": 10, "poll_interval_s": 0.01,
                      "max_polls": 10}}


class MediaJobs(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from app.services.media_jobs import MediaJob, MediaRequest
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cfg = self.root / "policy.json"
        self.cfg.write_text(json.dumps(policy()))
        self.env = patch.dict(os.environ, {"ORCHESTRATOR_RUNTIME_CONFIG": str(self.cfg)})
        self.env.start()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.root / 'state.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(conn)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.old = observation._observer
        self.observer = observation.Observer(self.sessions, body_root=self.root / "bodies")
        observation._observer = self.observer
        self.nodes = [{"id": "image", "type": "images", "data": {"groupId": "media"}},
                      {"id": "foreign", "type": "videos", "data": {"groupId": "other"}}]
        graph = digest({"nodes": self.nodes, "edges": []})
        async with self.sessions() as s:
            s.add(Project(id=7, slug="synthetic-media", topic="synthetic", meta={}))
            s.add(Workflow(id=1, name="media", nodes=self.nodes, edges=[]))
            s.add(WorkflowRun(id=1, workflow_id=1, project_id=7, nodes_snapshot=self.nodes, edges_snapshot=[]))
            s.add_all([NodeRun(workflow_run_id=1, node_key=n["id"], node_type=n["type"],
                               status=NodeRunStatus.pending) for n in self.nodes])
            self.scope = ExecutionControl(execution_id="52b01b6a-8625-47f1-a605-8596dc45dad2", project_id=7, workflow_run_id=1,
                node_keys=["image"], graph_version=graph, mode="observe", observer_state="active")
            s.add(self.scope)
            await s.commit()
        await ActionSupervisor(self.sessions).enable(project_id=7, execution_id="52b01b6a-8625-47f1-a605-8596dc45dad2",
            group_id="media", node_keys=["image"], graph_version=graph,
            initiator="test", rationale="synthetic HTTP only", request_limit=8)
        await self.observer.reload()
        self.scope = self.observer.scopes[1]
        self.session = self.sessions()
        self.project = await self.session.get(Project, 7)
        # Project.data_dir is derived from settings; use the actual isolated location.
        self.project.data_dir.mkdir(parents=True, exist_ok=True)
        self.output = self.project.data_dir / "images" / "frame_001_12345678.png"
        self.token = observation._attempt.set(AttemptContext(self.observer, self.scope,
            self.session, "project", "image", "attempt-original"))

    async def asyncTearDown(self):
        observation._attempt.reset(self.token)
        await self.session.close()
        observation._observer = self.old
        clear_permission(7)
        self.env.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def open_job(self, path=None):
        from app.services.media_jobs import open_job
        return await open_job(provider="kie", kind="image", model_id="z-image",
                              payload={"model": "z-image", "input": {"prompt": "synthetic"}},
                              out_path=path or self.output)

    async def test_restart_known_job_has_one_submit_and_same_artifact_identity(self):
        from app.services.media_jobs import MediaJob
        job = await self.open_job()
        await job.reserve("generation_submit")
        await job.update(phase="running", provider_job_id="job-001")
        await reload_permissions(self.sessions)
        recovered = await self.open_job(self.output.with_name("frame_001_abcdef12.png"))
        self.assertEqual(recovered.job_id, job.job_id)
        self.assertEqual(recovered.path, job.path)
        self.assertEqual(recovered.provider_job_id, "job-001")
        with self.assertRaisesRegex(RuntimeError, "submit"):
            await recovered.reserve("generation_submit")
        await recovered.reserve("status")
        async with self.sessions() as s:
            from app.services.actions_v1 import ActionExecutionControl
            self.assertEqual((await s.get(ActionExecutionControl, "52b01b6a-8625-47f1-a605-8596dc45dad2")).requests_used, 2)

    async def test_uncertain_submit_never_resubmits_after_reload(self):
        job = await self.open_job()
        await job.reserve("generation_submit")
        await reload_permissions(self.sessions)
        with self.assertRaisesRegex(RuntimeError, "unknown|uncertain"):
            await self.open_job()

    async def test_shared_atomic_budget_has_durable_operation_categories(self):
        from app.services.media_jobs import MediaRequest
        job = await self.open_job()
        await job.reserve("generation_submit")
        await job.update(phase="running", provider_job_id="budget-job")
        results = await asyncio.gather(*(job.reserve("status") for _ in range(12)), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, str) for x in results), 7)
        await reload_permissions(self.sessions)
        with self.assertRaises(RuntimeError):
            await job.reserve("download")
        async with self.sessions() as s:
            rows = (await s.scalars(select(MediaRequest))).all()
            self.assertEqual(len(rows), 8)
            self.assertEqual({r.category for r in rows}, {"status", "generation_submit"})
            self.assertEqual({r.cost_status for r in rows}, {"unknown_tariff"})

    async def test_stop_scope_revision_and_direct_service_bypass_block(self):
        from app.services.media_jobs import assert_entry
        job = await self.open_job()
        with self.assertRaisesRegex(RuntimeError, "scope"):
            await assert_entry(project_id=99, provider="kie")
        ctx = observation._attempt.get()
        ctx.node_key = "foreign"
        with self.assertRaisesRegex(RuntimeError, "scope"):
            await job.reserve("status")
        ctx.node_key = "image"
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            project.meta = {"user_stop": True}
            await s.commit()
        with self.assertRaises(RuntimeError):
            await job.reserve("generation_submit")
        token = observation._attempt.set(None)
        try:
            with self.assertRaisesRegex(RuntimeError, "context"):
                await assert_entry(project_id=7, provider="kie")
        finally:
            observation._attempt.reset(token)

    async def test_config_revision_and_nonallowlisted_network_rejected(self):
        from app.services.media_jobs import check_url
        job = await self.open_job()
        for url in ("http://169.254.169.254/x", "http://127.0.0.1:9999/x",
                    "http://localhost:18766/x", "file:///etc/passwd",
                    "http://127.0.0.1:18766@evil.test/x"):
            with self.assertRaises(ValueError):
                check_url(url, policy()["media"], download=True)
        self.cfg.write_text(json.dumps(policy("http://127.0.0.1:18767")))
        with self.assertRaisesRegex(RuntimeError, "revision"):
            await job.reserve("generation_submit")

    async def test_close_cannot_release_unknown_or_active_provider_job(self):
        job = await self.open_job()
        await job.reserve("generation_submit")
        with self.assertRaisesRegex(ValueError, "media"):
            await ActionSupervisor(self.sessions).close(project_id=7,
                execution_id=self.scope.execution_id, initiator="test",
                rationale="close must not release an outstanding provider operation",
                idempotency_key="close-media-job")
        await job.update(phase="running", provider_job_id="known-provider-job")
        with self.assertRaisesRegex(ValueError, "media"):
            await ActionSupervisor(self.sessions).close(project_id=7,
                execution_id=self.scope.execution_id, initiator="test",
                rationale="running external job remains tracked", idempotency_key="close-media-known")


if __name__ == "__main__":
    unittest.main(verbosity=2)
