import io
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

if not Path("/.dockerenv").is_file():
    raise RuntimeError("Restart safety tests require the parent's isolated Docker runner")

import httpx
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

import test_actions_v1_contract as actions_fixture
import test_media_jobs as media_fixture
from app import db, project_db
from app.models import NodeRun, NodeRunStatus, Project
from app.services import actions_recovery, observation
from app.services.actions_v1 import (
    ActionExecutionControl,
    ActionRecord,
    ActionSupervisor,
    clear_permission,
    get_permission,
    worker_entry_admission,
)
from app.services.decision_config import DecisionRuntimeBinding
from app.services.media_jobs import JobHandle, MediaJob, MediaRequest
from app.services.media_transport import current_job
from app.settings import settings


class SimulatedProcessLoss(BaseException):
    pass


class ManagedRecoveryRestartSafety(unittest.IsolatedAsyncioTestCase):
    exhausted_fixture = actions_fixture.DurableActions.exhausted_fixture

    async def asyncSetUp(self):
        await actions_fixture.DurableActions.asyncSetUp(self)
        self.root = Path(self.tmp.name)
        data_patch = patch.object(settings, "data_dir", self.root / "projects")
        data_patch.start()
        self.addCleanup(data_patch.stop)
        master_patch = patch.object(db, "SessionLocal", self.sessions)
        master_patch.start()
        self.addCleanup(master_patch.stop)
        self.attempt_token = observation._attempt.set(None)
        self.observer = observation._observer

    async def asyncTearDown(self):
        observation._attempt.reset(self.attempt_token)
        await project_db.close_all_project_engines()
        await actions_fixture.DurableActions.asyncTearDown(self)

    async def recover(self):
        return await self.supervisor.recover_node(
            project_id=7, execution_id="exec-durable", incident_id="source-incident",
            node_key="n1", idempotency_key="restart-recovery-0001",
            initiator="synthetic", rationale="durable claim crash window",
        )

    async def reserve_and_claim(self):
        await self.exhausted_fixture()
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            project_db.register_project_data_dir(7, project.data_dir)
            await project_db.migrate_single_project_data(master, project, create_backup=False)
        await self.observer.reload()
        ticket = await self.recover()
        async with project_db.project_db_session_scope(7) as business:
            project = await business.get(Project, 7)
            original_topic = project.topic
            actual = await actions_recovery.claim(
                self.observer, business, project, "n1", "unclaimed-worker-attempt",
                execution_id="exec-durable",
            )
            self.assertEqual(actual, ticket["new_attempt_id"])
            project.topic = "uncommitted worker edit"
            await business.flush()
            await business.rollback()
        return ticket, original_topic

    async def reconstruct(self):
        observation._attempt.set(None)
        clear_permission(7)
        actions_recovery.finish_job_tracking(7)
        await project_db.close_all_project_engines()
        await self.engine.dispose()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        db.SessionLocal = self.sessions
        self.supervisor = ActionSupervisor(self.sessions)
        self.observer = observation.Observer(self.sessions, body_root=self.root / "bodies")
        observation._observer = self.observer
        self.assertIsNone(get_permission(7))
        self.assertEqual(self.observer.scopes, {})
        await self.observer.reload()

    async def assert_single_ticket(self, ticket, status):
        async with self.sessions() as master:
            rows = list(await master.scalars(select(actions_recovery.RecoveryAction)))
            records = list(await master.scalars(select(ActionRecord).where(
                ActionRecord.action_kind == "node_recovery")))
            control = await master.get(ActionExecutionControl, "exec-durable")
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(rows[0].action_id, ticket["action_id"])
        self.assertEqual(rows[0].new_attempt_id, ticket["new_attempt_id"])
        self.assertEqual(rows[0].status, status)
        self.assertTrue(rows[0].result["worker_claimed"])
        self.assertEqual(records[0].action_id, rows[0].action_id)
        self.assertEqual(records[0].attempt_id, rows[0].new_attempt_id)
        self.assertEqual(records[0].status, "running" if status == "claimed" else status)
        self.assertEqual(control.requests_used, 0)
        self.assertEqual(get_permission(7).requests_used, 0)
        self.assertEqual(actions_recovery.restricted_node(7), "n1")
        return rows[0]

    async def assert_worker_cannot_reclaim(self, original_topic):
        entered = False
        async with project_db.project_db_session_scope(7) as business:
            project = await business.get(Project, 7)
            self.assertEqual(project.topic, original_topic)
            node = await business.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            self.assertEqual(node.status, NodeRunStatus.running)
            self.assertEqual(node.attempts, 9)
            self.assertEqual(project.meta["step_failure"]["total_fails"]["enriching_1"], 9)
            with self.assertRaisesRegex(RuntimeError, "already consumed|dispatch uncertain"):
                async with observation.observe_attempt(business, project):
                    entered = True
            await business.rollback()
        self.assertFalse(entered, "claimed ticket must block before the native executor")

    async def test_claim_commit_survives_business_rollback_and_two_runtime_reconstructions(self):
        ticket, original_topic = await self.reserve_and_claim()
        for restart in range(2):
            with self.subTest(restart=restart):
                await self.reconstruct()
                await self.assert_single_ticket(ticket, "claimed")
                await self.assert_worker_cannot_reclaim(original_topic)
                replay = await self.recover()
                self.assertTrue(replay["idempotent_replay"])
                self.assertEqual(replay["action_id"], ticket["action_id"])
                self.assertEqual(replay["new_attempt_id"], ticket["new_attempt_id"])
                await self.assert_single_ticket(ticket, "claimed")

    async def test_abandoned_claim_reconciles_to_review_once_without_dispatch_or_success(self):
        ticket, original_topic = await self.reserve_and_claim()
        await self.reconstruct()
        claimed = await self.assert_single_ticket(ticket, "claimed")
        with patch.object(actions_recovery, "datetime", wraps=datetime) as clock:
            clock.utcnow.return_value = claimed.created_at + timedelta(seconds=31)
            await actions_recovery.reconcile(self.observer)
        reviewed = await self.assert_single_ticket(ticket, "human_review")
        self.assertFalse(reviewed.verification_result["verified"])
        self.assertFalse(reviewed.verification_result["node_end"])
        self.assertFalse(reviewed.verification_result["node_done"])
        self.assertTrue(get_permission(7).held)
        self.assertFalse((await worker_entry_admission(7)).allowed)
        await self.reconstruct()
        await actions_recovery.reconcile(self.observer)
        await self.assert_worker_cannot_reclaim(original_topic)
        await self.assert_single_ticket(ticket, "human_review")
        replay = await self.recover()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["status"], "human_review")
        async with self.sessions() as master:
            verifications = list(await master.scalars(select(observation.ObservationOutbox).where(
                observation.ObservationOutbox.kind == "recovery_verification")))
        self.assertEqual(len(verifications), 1)
        self.assertEqual(verifications[0].attempt_id, ticket["new_attempt_id"])


class ManagedMediaRestartSafety(unittest.IsolatedAsyncioTestCase):
    open_job = media_fixture.MediaJobs.open_job

    async def asyncSetUp(self):
        await media_fixture.MediaJobs.asyncSetUp(self)
        self.execution_id = self.scope.execution_id
        self.job_token = current_job.set(None)
        self.calls = []
        self.origin = media_fixture.policy()["media"]["origin"]
        image = io.BytesIO()
        Image.new("RGB", (64, 96), "blue").save(image, "PNG")
        self.image = image.getvalue()
        original_client = httpx.AsyncClient

        def client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(self.respond)
            kwargs["trust_env"] = False
            return original_client(*args, **kwargs)

        transport_patch = patch.object(httpx, "AsyncClient", side_effect=client)
        transport_patch.start()
        self.addCleanup(transport_patch.stop)

    async def asyncTearDown(self):
        current_job.reset(self.job_token)
        await media_fixture.MediaJobs.asyncTearDown(self)

    def respond(self, request):
        self.assertEqual(str(request.url).split(request.url.path, 1)[0], self.origin)
        self.calls.append((request.method, request.url.path))
        if (request.method, request.url.path) == ("POST", "/api/v1/jobs/createTask"):
            return httpx.Response(200, json={"code": 200, "data": {"taskId": "restart-job-001"}})
        if (request.method, request.url.path) == ("GET", "/api/v1/jobs/recordInfo"):
            self.assertEqual(request.url.params["taskId"], "restart-job-001")
            return httpx.Response(200, json={"code": 200, "data": {
                "state": "success", "resultUrls": [self.origin + "/artifact.png"],
            }})
        if (request.method, request.url.path) == ("GET", "/artifact.png"):
            return httpx.Response(200, content=self.image, headers={"content-type": "image/png"})
        raise AssertionError("unexpected HTTP operation in restart test")

    async def generate(self, path=None):
        from app.bots.kie_http import run_generation
        from app.services.kie_catalog import get_model
        return await run_generation(get_model("z-image"),
            {"model": "z-image", "input": {"prompt": "synthetic"}}, path or self.output)

    async def reconstruct(self, *, reload=True):
        observation._attempt.set(None)
        current_job.set(None)
        await self.session.rollback()
        await self.session.close()
        clear_permission(7)
        actions_recovery.finish_job_tracking(7)
        await self.engine.dispose()
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.observer = observation.Observer(self.sessions, body_root=self.root / "bodies")
        observation._observer = self.observer
        self.assertIsNone(get_permission(7))
        self.assertEqual(self.observer.scopes, {})
        self.session = self.sessions()
        self.project = await self.session.get(Project, 7)
        if reload:
            await self.observer.reload()
            self.scope = self.observer.scopes[1]
            observation._attempt.set(observation.AttemptContext(
                self.observer, self.scope, self.session, "project", "image", "attempt-restarted"))

    async def durable_state(self):
        async with self.sessions() as master:
            jobs = list(await master.scalars(select(MediaJob)))
            requests = list(await master.scalars(select(MediaRequest).order_by(MediaRequest.created_at)))
            control = await master.get(ActionExecutionControl, self.execution_id)
            binding = await master.get(DecisionRuntimeBinding, self.execution_id)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].execution_id, self.execution_id)
        self.assertEqual(jobs[0].node_key, "image")
        self.assertIsNotNone(binding)
        self.assertEqual(jobs[0].config_hash, binding.config_hash)
        self.assertEqual(control.requests_used, len(requests))
        self.assertEqual({r.job_id for r in requests}, {jobs[0].job_id})
        self.assertEqual({r.execution_id for r in requests}, {self.execution_id})
        self.assertEqual({r.node_key for r in requests}, {"image"})
        return jobs[0], requests, control

    async def crash_after_submit_reservation(self):
        original_reserve = JobHandle.reserve

        async def reserve_then_crash(job, category):
            request_id = await original_reserve(job, category)
            if category == "generation_submit":
                raise SimulatedProcessLoss()
            return request_id

        with patch.object(JobHandle, "reserve", reserve_then_crash):
            with self.assertRaises(SimulatedProcessLoss):
                await self.generate()
        job, requests, control = await self.durable_state()
        self.assertEqual(job.phase, "submitted")
        self.assertIsNone(job.provider_job_id)
        self.assertEqual([(r.category, r.status) for r in requests], [("generation_submit", "reserved")])
        self.assertFalse(control.held)
        self.assertEqual(self.calls, [])
        return job.job_id, requests[0].request_id

    async def assert_unknown_submit_never_repeats(self, job_id, request_id, delivery):
        calls = list(self.calls)
        for restart in range(2):
            with self.subTest(restart=restart):
                await self.reconstruct()
                with self.assertRaisesRegex(RuntimeError, "unknown|held"):
                    await self.generate(self.output.with_name("frame_001_abcdef12.png"))
                job, requests, control = await self.durable_state()
                self.assertEqual(job.job_id, job_id)
                self.assertEqual(job.phase, "submitted")
                self.assertIsNone(job.provider_job_id)
                self.assertEqual(job.attempt_id, "attempt-original")
                self.assertEqual(len(requests), 1)
                self.assertEqual(requests[0].request_id, request_id)
                self.assertEqual(requests[0].status, delivery)
                self.assertEqual(requests[0].category, "generation_submit")
                self.assertEqual(requests[0].attempt_id, "attempt-original")
                self.assertEqual(control.requests_used, 1)
                self.assertEqual(get_permission(7).requests_used, 1)
                self.assertTrue(control.held)
                self.assertFalse((await worker_entry_admission(7)).allowed)
                self.assertEqual(self.calls, calls)

    async def test_submit_reservation_without_transport_survives_reconstruction_without_retry(self):
        job_id, request_id = await self.crash_after_submit_reservation()
        await self.assert_unknown_submit_never_repeats(job_id, request_id, "reserved")

    async def test_received_submit_without_committed_provider_id_never_posts_twice(self):
        from app.bots import kie_http
        original_create = kie_http.create_task

        async def create_then_crash(*args, **kwargs):
            task_id = await original_create(*args, **kwargs)
            self.assertEqual(task_id, "restart-job-001")
            raise SimulatedProcessLoss()

        with patch.object(kie_http, "create_task", create_then_crash):
            with self.assertRaises(SimulatedProcessLoss):
                await self.generate()
        job, requests, control = await self.durable_state()
        self.assertEqual(job.phase, "submitted")
        self.assertIsNone(job.provider_job_id)
        self.assertEqual([(r.category, r.status) for r in requests], [("generation_submit", "received")])
        self.assertEqual(self.calls, [("POST", "/api/v1/jobs/createTask")])
        self.assertFalse(control.held)
        await self.assert_unknown_submit_never_repeats(job.job_id, requests[0].request_id, "received")

    async def test_cold_runtime_blocks_unsettled_submit_before_permission_and_scope_reload(self):
        await self.crash_after_submit_reservation()
        await self.reconstruct(reload=False)
        job, requests, control = await self.durable_state()
        self.assertEqual(job.phase, "submitted")
        self.assertEqual(len(requests), 1)
        self.assertTrue(control.enabled)
        self.assertFalse(control.held)
        self.assertIsNone(observation._attempt.get())
        admission = await worker_entry_admission(7)
        self.assertFalse(admission.allowed,
            "durable managed execution with an unsettled submit must block a cold worker: " + admission.reason)
        from app.services.advance_runner import advance_project_job
        with patch("app.services.advance_runner.project_db_session_scope",
                   side_effect=AssertionError("cold managed worker opened a business session")) as business:
            result = await advance_project_job(7, None)
        business.assert_not_called()
        self.assertIsNone(result.new_status)
        self.assertEqual(self.calls, [])

    async def test_known_job_reconstructs_and_verifies_original_path_without_resubmit_or_budget_reset(self):
        from app.bots import kie_http
        with patch.object(kie_http, "poll_task", side_effect=SimulatedProcessLoss()):
            with self.assertRaises(SimulatedProcessLoss):
                await self.generate()
        job, requests, control = await self.durable_state()
        self.assertEqual(job.phase, "queued")
        self.assertEqual(job.provider_job_id, "restart-job-001")
        self.assertEqual(len(requests), 1)
        self.assertFalse(control.held)
        original_job_id, original_path = job.job_id, Path(job.path)
        original_request_id = requests[0].request_id
        await self.reconstruct()
        changed_suffix = self.output.with_name("frame_001_abcdef12.png")
        recovered = await self.open_job(changed_suffix)
        self.assertEqual(recovered.job_id, original_job_id)
        self.assertEqual(recovered.path, original_path)
        self.assertEqual(recovered.provider_job_id, "restart-job-001")
        with self.assertRaisesRegex(RuntimeError, "duplicate submit"):
            await recovered.reserve("generation_submit")
        _, requests, control = await self.durable_state()
        self.assertEqual(len(requests), 1)
        self.assertEqual(control.requests_used, 1)
        result = await self.generate(changed_suffix)
        self.assertEqual(result.file_path, original_path)
        self.assertEqual(result.gen_id, "restart-job-001")
        self.assertTrue(result.file_path.is_file())
        job, requests, control = await self.durable_state()
        self.assertEqual(job.job_id, original_job_id)
        self.assertEqual(job.phase, "verified")
        self.assertEqual(job.verification["status"], "passed")
        self.assertEqual((job.verification["width"], job.verification["height"]), (64, 96))
        self.assertEqual(job.attempt_id, "attempt-original")
        submit = next(r for r in requests if r.category == "generation_submit")
        self.assertEqual(submit.request_id, original_request_id)
        self.assertEqual(submit.attempt_id, "attempt-original")
        self.assertCountEqual([r.category for r in requests], ["generation_submit", "status", "download"])
        self.assertTrue(all(r.status == "received" for r in requests))
        self.assertTrue(all(r.attempt_id == "attempt-restarted" for r in requests
                            if r.category != "generation_submit"))
        self.assertEqual(control.requests_used, 3)
        self.assertEqual(self.calls, [("POST", "/api/v1/jobs/createTask"),
            ("GET", "/api/v1/jobs/recordInfo"), ("GET", "/artifact.png")])
        calls = list(self.calls)
        proof = dict(job.verification)
        await self.reconstruct()
        replay = await self.generate(changed_suffix)
        self.assertEqual(replay.file_path, original_path)
        job, requests, control = await self.durable_state()
        self.assertEqual(job.job_id, original_job_id)
        self.assertEqual(job.verification, proof)
        self.assertEqual(control.requests_used, 3)
        self.assertEqual(get_permission(7).requests_used, 3)
        self.assertEqual(len(requests), 3)
        self.assertEqual(self.calls, calls)


if __name__ == "__main__":
    unittest.main(verbosity=2)
