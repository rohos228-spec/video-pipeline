"""Shared harness of the media dry runs (videos, items, music): observation -> diagnosis -> decision ->
resume-only recovery ticket -> independent verification.

No network, no provider, no paid call. What is REAL: observe_attempt and the journal, the media_result
verifier (independent read, ffprobe on real files), the Diagnoser, the DecisionSupervisor and its policy,
ActionSupervisor.enable / recover_node and its gates (kill switch, scope, budget, input revision, graph,
one ticket per node and execution), media_admission, media_recovery.assess / resume_poll, media_resume
and kie_http (create / poll / download code paths), the provider_task_ledger and the resume-only guard.
What is SCRIPTED: the provider HTTP boundary only (createTask POST, status GET, file download), the
Decision model (canned proposals) and, for videos and items, the node body (the node's own paid seam is
called with the node's own key names; the Outsee browser ladder is not involved). The native step-failure
counter (a runtime policy outside observation) is seeded by hand, as in the sfx_plan dry run.
"""
import asyncio
import json
import shutil
import subprocess
import unittest
from contextlib import ExitStack, asynccontextmanager
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import test_actions_v1_contract as base_tests
from app.bots import kie_http
from app.models import (
    Base, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun,
)
from app.services import (
    decision_config, kill_switch, media_admission, media_recovery, media_spend, observation, outsee_retry,
    spend_guard,
)
from app.services.actions_recovery import RecoveryAction
from app.services.diagnosis import Incident, get_diagnoser
from app.services.gpt_api import GptChatResult
from app.services.node_status_machine import (
    complete_node, fail_node, queue_node_for_start, start_node_running,
)
from app.settings import settings

EXECUTION = "00000000-0000-4000-8000-0000000000b1"
PID = 7
HAVE_FFMPEG = bool(shutil.which("ffmpeg") and shutil.which("ffprobe"))


class ProcessDied(BaseException):
    """Stands in for a killed process: not caught by any `except Exception`."""


_CACHE: dict[str, bytes] = {}


def media_bytes(kind: str, scratch: Path) -> bytes:
    """Real, decodable files (ffmpeg / PIL), so the independent probe is the real one."""
    if kind in _CACHE:
        return _CACHE[kind]
    scratch.mkdir(parents=True, exist_ok=True)
    if kind == "image":
        from PIL import Image
        import random
        rnd = random.Random(7)
        img = Image.new("RGB", (64, 64))
        img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256)) for _ in range(64 * 64)])
        path = scratch / "x.png"
        img.save(path)
    elif kind == "video":
        path = scratch / "x.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=blue:s=64x96:r=10:d=1",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True, timeout=60)
    else:
        path = scratch / "x.mp3"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                        "-c:a", "libmp3lame", str(path)], check=True, timeout=60)
    _CACHE[kind] = path.read_bytes()
    return _CACHE[kind]


_REAL_SLEEP = asyncio.sleep


class FakeClock:
    """A virtual clock for the poll window: waiting costs no wall time."""

    def __init__(self):
        self.t = 0.0

    def time(self):
        return self.t

    async def sleep(self, d):
        self.t += max(float(d), 1.0)
        await _REAL_SLEEP(0)

    def proxy(self):
        return SimpleNamespace(get_event_loop=lambda: self, get_running_loop=lambda: self, sleep=self.sleep,
                               CancelledError=asyncio.CancelledError, TimeoutError=asyncio.TimeoutError)


class FakeGpt:
    async def new_conversation(self):
        return None

    async def ask_with_files(self, *a, **k):
        return "Instrumental calm background music for a short educational video, soft pads, slow tempo."


class MediaDryRun(base_tests.DurableActions):
    """Subclasses set the node and implement ``native_step``."""

    fixture_node_type = "videos"
    status = ProjectStatus.generating_videos
    running_key = "generating_videos"
    api = "jobs"            # Kie task family of the node's paid request
    media_kind = "video"    # real file kind returned by the fake download
    result_ext = ".mp4"
    key = "clip_001"        # provider_task_ledger key of the node's task
    single_submit = False   # videos: run one Kie submit instead of the whole ladder

    # ---------------------------------------------------------------- provider boundary (the only fake)
    def poll_body(self, step):
        if self.api == "suno":
            if step == "waiting":
                return {"code": 200, "data": {"status": "PENDING"}}
            if step == "fail":
                return {"code": 200, "data": {"status": "GENERATE_AUDIO_FAILED", "failMsg": "bad"}}
            return {"code": 200, "data": {"status": "SUCCESS", "response": {"sunoData": [
                {"audioUrl": "https://cdn.example/a.mp3", "duration": 30}]}}}
        if step == "waiting":
            return {"code": 200, "data": {"state": "waiting"}}
        if step == "fail":
            return {"code": 200, "data": {"state": "fail", "failMsg": "bad"}}
        return {"code": 200, "data": {"state": "success", "resultJson": json.dumps(
            {"resultUrls": ["https://cdn.example/out" + self.result_ext]})}}

    async def fake_post(self, url, body, timeout=120.0):
        self.creates += 1
        step = self.create_script.pop(0) if self.create_script else f"T-{self.creates}"
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(status_code=200), {"code": 200, "data": {"taskId": step}}, "stub"

    def send_function(self):
        async def send(client, request, **kw):
            return await self.fake_send(client, request, **kw)
        return send

    async def fake_send(self, client, request, **kw):
        url = str(request.url)
        if "recordInfo" in url or "record-info" in url:
            self.polls += 1
            self.poll_in_resume_only.append(media_spend.in_resume_only())
            step = self.poll_script.pop(0) if self.poll_script else "success"
            if isinstance(step, BaseException):
                raise step
            return httpx.Response(200, json=self.poll_body(step), request=request)
        self.downloads += 1
        return httpx.Response(200, content=media_bytes(self.media_kind, Path(self.tmp.name) / "scratch"),
                              request=request)

    # ---------------------------------------------------------------- set-up
    async def asyncSetUp(self):
        self.creates = self.polls = self.downloads = 0
        self.create_script, self.poll_script, self.poll_in_resume_only = [], [], []
        await base_tests.DurableActions.asyncSetUp(self)
        self.stack = ExitStack()
        self.data = Path(self.tmp.name) / "videos" / "actions-fixture"
        self.data.mkdir(parents=True)
        (self.data / "voiceover.txt").write_text("Закадровый текст ролика о простых вещах.", encoding="utf-8")
        # the provider task ledger lives in its own database, as in production (app.db.SessionLocal)
        self.ledger_engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'ledger.db'}")
        async with self.ledger_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.ledger = async_sessionmaker(self.ledger_engine, expire_on_commit=False)
        for patcher in (patch.object(settings, "data_dir", Path(self.tmp.name)),
                        patch.object(settings, "gpt_api_key", "synthetic-observe-test-key"),
                        patch.object(settings, "gpt_base_url", "http://127.0.0.1:18766"),
                        patch("app.db.SessionLocal", self.ledger),
                        patch.object(media_spend, "_enabled", lambda provider: True),
                        patch.object(kie_http, "kie_post_json", self.fake_post),
                        patch.object(httpx.AsyncClient, "send", self.send_function()),
                        patch.object(kie_http, "_headers", lambda: {}),
                        patch.object(kie_http, "_POLL_INTERVAL_S", 0.0),
                        patch.object(kie_http, "kie_api_base_url", lambda: "https://kie.invalid"),
                        patch.object(kie_http, "kie_configured", lambda: True),
                        patch.object(outsee_retry, "sleep_cancellable", self.nosleep),
                        patch("app.services.gpt_client.get_gpt_client", lambda: FakeGpt())):
            self.stack.enter_context(patcher)
        cfg = decision_config.validate_config({'version': 1, 'mode': 'synthetic', 'online_enabled': True,
            'endpoint': 'http://127.0.0.1:18766/codex/v1/responses',
            'decision': {'catalog_id': 'gpt-kie', 'model_id': 'gpt-5-6-sol', 'reasoning_effort': 'low',
                         'max_output_tokens': 1024, 'timeout_s': 30},
            'profiles': {}, 'allowed_actions': sorted(decision_config.ALL_ACTIONS)})
        self.stack.enter_context(patch.object(decision_config, "runtime_config", return_value=cfg))
        self.observer = observation._observer
        await kill_switch.ensure_table(self.sessions)
        kill_switch.reset_cache()

        self.engine_biz = create_async_engine(f"sqlite+aiosqlite:///{self.data / 'project.db'}")
        self.business = async_sessionmaker(self.engine_biz, expire_on_commit=False)
        async with self.engine_biz.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(connection)
        canvas = {"canvas_graph": {"nodes": self.nodes, "edges": []}}
        async with self.business() as seed:
            seed.add_all([
                Project(id=PID, slug="actions-fixture", topic="Простые вещи", status=self.status,
                        auto_mode=False, meta=dict(canvas), item_descriptions=["красная кружка"]),
                Workflow(id=9, name="actions fixture", nodes=self.nodes, edges=[]),
                WorkflowRun(id=11, workflow_id=9, project_id=PID, nodes_snapshot=self.nodes, edges_snapshot=[]),
                NodeRun(workflow_run_id=11, node_key="n1", node_type=self.fixture_node_type,
                        status=NodeRunStatus.pending),
                NodeRun(workflow_run_id=11, node_key="n2", node_type="script", status=NodeRunStatus.pending),
                Frame(project_id=PID, number=1, uuid="af" * 16, sort_key=1, voiceover_text="Альфа закрывает папку.",
                      duration_seconds=3.0, animation_prompt="медленный наезд на папку", attrs={}),
            ])
            await seed.commit()
        async with self.sessions() as master:
            project = await master.get(Project, PID)
            project.status, project.meta = self.status, dict(canvas)
            project.item_descriptions = ["красная кружка"]
            project.topic = "Простые вещи"
            scope = await master.get(observation.ExecutionControl, "exec-durable")
            scope.execution_id, scope.node_keys = EXECUTION, ["n1", "n2"]
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
        await self.observer.reload()
        self.first_task = "T-1"

    async def nosleep(self, *a, **k):
        return None

    async def asyncTearDown(self):
        try:
            await media_spend.drain()
            await self.engine_biz.dispose()
            await self.ledger_engine.dispose()
            self.stack.close()
            kill_switch.reset_cache()
        finally:
            await base_tests.DurableActions.asyncTearDown(self)

    async def enable(self):
        """Opt in AFTER the failure: the failed attempt was observed without a managed scope."""
        await self.supervisor.enable(project_id=PID, execution_id=EXECUTION, group_id="g1", node_keys=["n1"],
            graph_version=self.graph, initiator="dry-run", rationale="media recovery dry run", request_limit=20)
        await self.observer.reload()

    # ---------------------------------------------------------------- runtime bookkeeping
    async def copy_runtime(self, session, project):
        from app.project_db import _apply_runtime_fields, _copy_noderuns_between_sessions
        async with self.sessions() as master:
            _apply_runtime_fields(project, await master.get(Project, PID))
            await _copy_noderuns_between_sessions(session, master, PID)
            await master.commit()

    async def sync(self):
        async with self.business() as session:
            await self.copy_runtime(session, await session.get(Project, PID))

    async def deliver(self):
        await self.observer.consume(EXECUTION, "master", self.sessions)
        await self.observer.consume(EXECUTION, "project", self.business)

    async def diagnose(self):
        await self.sync()
        await self.deliver()
        await get_diagnoser(self.observer).update(EXECUTION)
        self.assertEqual(get_diagnoser(self.observer).failures, {})

    async def facts(self, kind=None):
        await self.deliver()
        async with self.sessions() as session:
            rows = (await session.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all()
        return [r for r in rows if kind is None or r.kind == kind]

    async def incidents(self):
        async with self.sessions() as session:
            return (await session.scalars(select(Incident).where(Incident.execution_id == EXECUTION))).all()

    async def ledger_rows(self):
        await media_spend.drain()
        async with self.ledger() as session:
            await spend_guard.ensure_task_schema(session)
            return (await session.scalars(select(spend_guard.ProviderTaskLedger)
                                          .order_by(spend_guard.ProviderTaskLedger.id))).all()

    async def task_ids(self):
        return sorted({r.task_id for r in await self.ledger_rows()})

    # ---------------------------------------------------------------- the node
    def out_path(self, suffix="ab12cd34"):
        raise NotImplementedError

    async def native_step(self, session, project):
        """One native attempt of the node body. Subclasses call the node's own paid seam."""
        raise NotImplementedError

    async def worker(self, *, start=True):
        """One native attempt of the original step under observe_attempt (what advance does)."""
        async with self.business() as session:
            project = await session.get(Project, PID)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            if start:
                self.assertTrue(queue_node_for_start(node, project_id=PID, initiator="worker"))
                self.assertTrue(start_node_running(node, project_id=PID, initiator="worker"))
                await session.commit()
            try:
                async with observation.observe_attempt(session, project):
                    await self.native_step(session, project)
            except BaseException:
                await session.rollback()
                # what the runtime does with every failed (or restarted) attempt
                node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
                fail_node(node, "attempt failed", project_id=PID, initiator="worker")
                node.finished_at = datetime.utcnow()
                await session.commit()
                raise
            complete_node(node, project_id=PID, initiator="worker")
            await session.commit()

    async def exhaust_native_retries(self, error="native step retries exhausted"):
        """The runtime's step-failure policy after 9 failed native attempts (outside observation)."""
        async with self.business() as session:
            project = await session.get(Project, PID)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            fail_node(node, error, project_id=PID, initiator="worker")
            node.finished_at, node.attempts = datetime.utcnow(), 9
            project.status = ProjectStatus.paused
            project.meta = {**project.meta, "step_failure": {"total_fails": {self.running_key: 9},
                "last_running": self.running_key, "abandoned_at": "synthetic"}}
            await session.commit()
        await self.diagnose()

    async def fail_natively(self, error_type=Exception):
        """An attempt that ends in an Exception (what the runtime counts and diagnoses), then exhaustion."""
        with self.assertRaises(error_type) as caught:
            await self.worker()
        await self.exhaust_native_retries()
        return caught.exception

    async def media_incident(self):
        found = [i for i in await self.incidents()
                 if i.type in {"media_failure", "uncertain_request"} and i.details.get("media")]
        self.assertTrue(found, [(i.type, i.status) for i in await self.incidents()])
        return sorted(found, key=lambda i: i.type != "media_failure")[0]

    def decision_model(self, actions):
        """The Decision model: canned proposals, validated by the real policy."""
        queue = list(actions)

        async def reply(**kwargs):
            context = json.loads(kwargs["prompt"])
            action = queue.pop(0)
            self.assertIn(action, context["allowed_actions"])
            proposal = dict(action=action, arguments={}, evidence_ids=context["evidence_ids"][:1],
                            context_version=context["context_version"],
                            explanation="synthetic facts, no hidden reasoning")
            return GptChatResult(text=json.dumps(proposal), model="synthetic", raw={"status": "completed"})
        return patch("app.services.gpt_api.chat", side_effect=reply)

    async def recover(self, incident, key="media-recovery-0001"):
        return await self.supervisor.recover_node(project_id=PID, execution_id=EXECUTION,
            incident_id=incident.incident_id, node_key="n1", idempotency_key=key,
            initiator="dry-run", rationale="media resume dry run")

    async def tickets(self):
        async with self.sessions() as session:
            return (await session.scalars(select(RecoveryAction))).all()

    # ---------------------------------------------------------------- scenario building blocks
    async def killed_then_native_poll_timeout(self):
        """Attempt 1: the process dies at the first status request (no journal end, nothing counted).
        Attempt 2 (restart): the node resumes the SAME task id (no second createTask) and its poll window
        ends: an ordinary native failure with the provider task still open."""
        self.poll_script = [ProcessDied()]
        real_record = observation.record_fact

        async def dead_process_writes_nothing(kind, payload, **kw):
            if kind == "node_error" and payload.get("error_type") == "ProcessDied":
                return None  # a killed process cannot journal its own death
            return await real_record(kind, payload, **kw)

        with patch.object(observation, "record_fact", dead_process_writes_nothing):
            with self.assertRaises(ProcessDied):
                await self.worker()
        self.assertEqual((self.creates, self.polls), (1, 1))
        self.poll_script = ["waiting"] * 3000
        clock = self.clock_patches()
        for p in clock:
            p.start()
        try:
            error = await self.fail_natively(kie_http.KieTaskPending)
        finally:
            for p in clock:
                p.stop()
        self.assertEqual(self.creates, 1, "a restart never creates a second task")
        return error

    def clock_patches(self):
        from app.bots import kie_kling
        self.clock = FakeClock()
        return [patch.object(kie_http, "asyncio", self.clock.proxy()),
                patch.object(kie_kling, "asyncio", self.clock.proxy())]

    # ======================================================================================
    # the scenarios every promoted media node must pass
    # ======================================================================================
    async def test_restart_mid_poll_then_supervised_resume_downloads_with_zero_new_requests(self):
        from app.services import decision_v1
        await self.killed_then_native_poll_timeout()
        # observation + diagnosis: the failure is a typed media incident of THIS node
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["contract"], proof["node_type"], proof["status"]),
                         ("native_media", self.fixture_node_type, "failed"))
        incident = await self.media_incident()
        self.assertTrue(incident.details["currently_relevant"])
        self.assertIs(incident.details["media"], True)
        ids_before = await self.task_ids()
        self.assertEqual([i for i in ids_before if not i.startswith("intent:")], ["T-1"])
        # the baseline of the request inputs was recorded by the observed (non-managed) attempt
        start = (await self.facts("node_start"))[-1]
        self.assertTrue(start.payload["recovery_input_revision"])
        await self.enable()
        # decision: the context tells the model what retry_same would do here
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=PID, execution_id=EXECUTION, enabled=True,
                             initiator="dry-run", rationale="media dry run decision")
        record = await loop.begin(project_id=PID, execution_id=EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertEqual(context["node_capability"]["node_type"], self.fixture_node_type)
        self.assertEqual(context["media_recovery"]["operation"], "resume_poll")
        self.assertIs(context["media_recovery"]["submits_new_paid_request"], False)
        self.assertEqual(context["media_recovery"]["keys"][0]["task_id"], "T-1")
        self.assertIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        # the same task is now finished at the provider
        self.poll_script = ["success"]
        polls_before, creates_before = self.polls, self.creates
        with self.decision_model(["retry_same"]):
            await loop.step(record["decision_id"])
        # action: ONE ticket, executed in the supervisor process, final
        ticket = (await self.tickets())[0]
        self.assertEqual(ticket.status, "verified", ticket.verification_result)
        self.assertEqual(ticket.preconditions["media_operation"], "resume_poll")
        self.assertIs(ticket.preconditions["submits_new_paid_request"], False)
        self.assertEqual(ticket.budget_state["recovery_limit"], 1)
        verification = ticket.verification_result
        self.assertTrue(verification["verified"])
        self.assertEqual(verification["new_paid_requests"], 0)
        self.assertEqual(verification["new_provider_task_ids"], [])
        self.assertEqual(verification["files"][0]["status"], "passed")
        self.assertIs(verification["node_started"], False)
        self.assertEqual(verification["assessment_after"]["mode"], "nothing_to_do")
        # the provider boundary: one status request, one download, no createTask, inside resume_only
        self.assertEqual((self.creates, self.polls - polls_before, self.downloads), (creates_before, 1, 1))
        self.assertEqual(self.poll_in_resume_only[-1], True)
        self.assertEqual(await self.task_ids(), ids_before)
        statuses = [r.status for r in await self.ledger_rows() if r.task_id == "T-1"]
        self.assertEqual(statuses[-1], "downloaded")
        self.assertEqual(len(list(self.result_files())), 1)
        # the supervisor never starts the node: it is still the failed native attempt
        async with self.sessions() as session:
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            self.assertEqual(node.status, NodeRunStatus.failed)
            self.assertFalse((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.action_id), ("waiting_worker", ticket.action_id))
        # the persisted worker verification completes the decision without another model step
        await loop.tick()
        async with self.sessions() as session:
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual(row.status, "done")
            self.assertTrue(row.verification["verified"])
        # cap: a second recovery of the node is refused, and the database refuses a second ticket
        with self.assertRaisesRegex(ValueError, "decision owner"):
            await self.recover(incident, "second-media-0001")
        from sqlalchemy.exc import IntegrityError
        async with self.sessions() as session:
            session.add(RecoveryAction(action_id="second", execution_id=EXECUTION, project_id=PID,
                incident_id=incident.incident_id, node_key="n1", source_attempt_id="s", new_attempt_id="n",
                idempotency_key="second-ticket-0001", preconditions={}, budget_state={}, status="reserved"))
            with self.assertRaises(IntegrityError):
                await session.commit()
        self.assertEqual(self.creates, 1, "one createTask in the whole story")

    def result_files(self):
        raise NotImplementedError

    async def test_resume_without_a_decision_layer_also_makes_zero_requests_and_is_idempotent(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["success"]
        result = await self.recover(incident)
        self.assertEqual(result["status"], "verified")
        self.assertIs(result["idempotent_replay"], False)
        again = await self.recover(incident)  # same idempotency key: replay, nothing executed again
        self.assertIs(again["idempotent_replay"], True)
        self.assertEqual((self.creates, self.downloads, len(await self.tickets())), (1, 1, 1))

    async def test_poll_window_still_open_is_human_review_and_holds_without_any_submit(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["waiting"]
        result = await self.recover(incident)
        self.assertEqual(result["status"], "human_review")
        self.assertIn(f"{self.key}:pending", result["verification_result"]["problems"])
        self.assertEqual(result["verification_result"]["new_paid_requests"], 0)
        self.assertEqual((self.creates, self.downloads), (1, 0))
        async with self.sessions() as session:
            self.assertTrue((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)
        self.assertEqual([i for i in await self.task_ids() if not i.startswith("intent:")], ["T-1"])
        # the one recovery of the node is spent
        with self.assertRaisesRegex(ValueError, "single recovery already consumed|held"):
            await self.recover(incident, "another-media-0002")

    async def test_unknown_submit_outcome_is_denied_typed_and_polls_nothing(self):
        self.create_script = [httpx.ReadTimeout("slow")]
        await self.fail_natively(kie_http.KieSubmitUnknownError)
        self.assertEqual((self.creates, self.polls), (1, 0))
        incident = await self.media_incident()
        await self.enable()
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)
        self.assertEqual(caught.exception.code, "verdict_denied")
        self.assertIn("unknown_outcome_kie", str(caught.exception))
        self.assertEqual((self.creates, self.polls, self.downloads), (1, 0, 0))
        self.assertEqual(await self.tickets(), [])
        # the decision context offers no retry_same: only reading, waiting and a human
        from app.services import decision_v1
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=PID, execution_id=EXECUTION, enabled=True,
                             initiator="dry-run", rationale="media dry run decision")
        record = await loop.begin(project_id=PID, execution_id=EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertIsNone(context["media_recovery"]["operation"])
        self.assertEqual(context["media_recovery"]["denied"], "verdict_denied")
        self.assertNotIn("retry_same", context["allowed_actions"])

    async def test_provably_failed_task_gets_no_paid_retry_from_the_supervisor(self):
        self.single_submit = True
        self.poll_script = ["fail"]
        await self.fail_natively(kie_http.KieHttpError)
        self.assertEqual(self.creates, 1)
        incident = await self.media_incident()
        report = await media_recovery.assess(SimpleNamespace(id=PID, data_dir=self.data), self.fixture_node_type)
        self.assertEqual(report["verdict"]["mode"], "retry_same")  # the service knows it is retryable ...
        await self.enable()
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)  # ... and the supervisor still refuses to submit again
        self.assertEqual(caught.exception.code, "retry_same_not_admitted")
        self.assertEqual((self.creates, [i for i in await self.task_ids() if not i.startswith("intent:")],
                          await self.tickets()), (1, ["T-1"], []))

    async def test_kill_switch_refuses_before_any_ticket_or_request(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        await kill_switch.set_state(self.sessions, active=True, actor="operator", rationale="emergency stop test")
        self.poll_script = ["success"]
        polls_before = self.polls
        with self.assertRaisesRegex(ValueError, "kill switch active"):
            await self.recover(incident)
        self.assertEqual((self.polls, self.downloads, await self.tickets()), (polls_before, 0, []))
        await kill_switch.set_state(self.sessions, active=False, actor="operator", rationale="emergency stop off")
        result = await self.recover(incident)
        self.assertEqual(result["status"], "verified")

    async def test_spend_caps_are_irrelevant_to_a_resume_and_nothing_is_reserved(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["success"]
        spies = [patch.object(spend_guard, name, side_effect=AssertionError(f"{name} must not run"))
                 for name in ("reserve", "record_actual", "schedule_actual")]
        caps = [patch.object(settings, "spend_run_cap_usd", 0.0001),
                patch.object(settings, "spend_project_cap_usd", 0.0001),
                patch.object(settings, "spend_daily_cap_usd", 0.0001)]
        with ExitStack() as stack:
            for patcher in (*spies, *caps):
                stack.enter_context(patcher)
            result = await self.recover(incident)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["verification_result"]["new_paid_requests"], 0)
        # a paid submit inside the resume scope is refused even with every cap wide open
        with media_spend.resume_only():
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await kie_http.create_task("jobs", "/api/v1/jobs/createTask", {"model": "m", "input": {}})
        self.assertEqual(self.creates, 1)

    async def test_changed_request_inputs_invalidate_the_baseline(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        await self.change_request_inputs()
        self.poll_script = ["success"]
        with self.assertRaisesRegex(ValueError, "input/config revision changed"):
            await self.recover(incident)
        self.assertEqual((self.polls, self.downloads, await self.tickets()), (self.polls, 0, []))

    async def test_user_stop_refuses_the_resume(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        async with self.sessions() as session:
            project = await session.get(Project, PID)
            project.meta = {**project.meta, "user_stop": True}
            await session.commit()
        with self.assertRaisesRegex(ValueError, "user_stop"):
            await self.recover(incident)
        self.assertEqual((self.downloads, await self.tickets()), (0, []))

    async def test_a_node_outside_the_admitted_set_never_gets_a_media_ticket(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        # assemble is not here any more: it is admitted as a LOCAL worker node (test_media_dry_run_local_nodes.py)
        for node_type in ("hero", "audio", "sfx_gen", "images", "publish"):
            with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
                await media_admission.admit(SimpleNamespace(id=PID, data_dir=self.data), node_type)
            self.assertEqual(caught.exception.code, "node_not_admitted", node_type)
        self.assertEqual(await self.tickets(), [])
        self.assertTrue(incident)

    async def test_interrupted_ticket_is_held_never_repeated(self):
        from app.services.actions_recovery import reconcile
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        async with self.sessions() as session:
            session.add(RecoveryAction(action_id="stuck", execution_id=EXECUTION, project_id=PID,
                incident_id=incident.incident_id, node_key="n1", source_attempt_id="s", new_attempt_id="n",
                idempotency_key="stuck-ticket-0001", preconditions={"media": True, "media_operation": "resume_poll"},
                budget_state={}, status="claimed", created_at=datetime(2020, 1, 1)))
            await session.commit()
        polls_before = self.polls
        await reconcile(self.observer)
        async with self.sessions() as session:
            ticket = await session.get(RecoveryAction, "stuck")
            self.assertEqual(ticket.status, "human_review")
            self.assertTrue((await session.get(base_tests.ActionExecutionControl, EXECUTION)).held)
        self.assertEqual((self.polls, self.creates), (polls_before, 1))

    async def change_request_inputs(self):
        raise NotImplementedError


if __name__ == "__main__":
    unittest.main(verbosity=2)
