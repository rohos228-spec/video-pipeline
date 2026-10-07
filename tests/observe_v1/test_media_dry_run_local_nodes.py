"""Dry run of the supervised retry of the local media nodes: assemble is ADMITTED, audio and sfx_gen are decided here.

No network, no provider, no paid call. REAL: observe_attempt and the journal, the media_result verifier, the
Diagnoser, the DecisionSupervisor, ActionSupervisor.enable / recover_node and its gates, the claim, the reconcile
verification, media_admission, local_node_recovery, media_recovery.assess, the real ``assemble._assemble_body``
(atomic write, ``verify_artifact`` with ffprobe on real files, in-node retry, approve_final barrier). SCRIPTED: the
montage engine ``_assemble_core`` (a local ffmpeg pipeline, not a provider: here it writes a real small mp4 or fails
like ffmpeg), the Telegram sender and the mass-factory hook. Nothing here can reach a provider; the file also proves
that by guard and by scan.

PROVEN SAFE (the base the admission rests on)
* in-node retry: exactly one, only for ffmpeg errors and failed verification, never for input errors or a cancel;
* atomic output: the previous final survives every failed attempt, a part file never lingers;
* ``verify_artifact`` runs on the NEW file before it replaces the old one; a failed or unknown proof changes nothing;
* approve_final is created once per (project, sha256), stays ``pending`` for a human, nothing here approves it;
* no paid request is reachable on the assemble path (guard + scan).

THE THREE CORE GAPS, CLOSED (each was pinned by a test that flipped)
1. a general non-model worker recovery: ``recover_node`` dispatches the original worker for a local node
   (``CapabilitySpec.local_recovery``) under a ticket; the attempt runs inside ``media_spend.resume_only`` and counts
   any refused paid seam; ``reconcile`` verifies the ticket by its OWN rule from independent facts (the new file is
   newer than the claim, ``verify_artifact`` again, the digest equals the artifact, the ``approve_final`` of that file
   is still pending, the provider ledger is unchanged, no paid seam hit, no model request, no part file);
   the same caps as the text nodes (kill switch, one ticket per node and execution, budget, input revision,
   user_stop, decision owner) and the same fail-closed behaviour;
2. a failed assemble is typed by the attempt's OWN outcome (a typed media failure with ``attempt_failure``), never by
   an old final that happens to pass;
3. the input baseline is recorded for the unmanaged attempt and covers the true inputs (voiceover, frame set, clip /
   voice / music / sfx files, assembly parameters), not progress counters or the attempt's own outputs.
"""
import ast
import hashlib
import inspect
import os
import subprocess
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

import media_dry_run_base as base
import test_actions_v1_contract as base_tests
from app.models import (
    Artifact, ArtifactKind, Frame, HITLDecision, HITLKind, HITLRequest, NodeRun, Project, ProjectStatus,
)
from app.orchestrator.node_spec import KNOWN_FORBIDDEN_CODES, capability_spec_for
from app.orchestrator.steps import assemble as asm
from app.services import (
    actions_recovery, decision_v1, kill_switch, local_node_recovery, media_admission, media_recovery, media_spend,
    node_capabilities, revisions, spend_guard,
)
from app.services.actions_recovery import RecoveryAction, finish_job_tracking, reconcile
from app.services.step_cancel import StepCancelledError

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")

_VIDEO: dict[str, bytes] = {}


def final_bytes(tag: str, scratch: Path, seconds: int = 3) -> bytes:
    """A real small mp4 with a video and an audio stream (what verify_artifact(kind='assembly') requires)."""
    if tag not in _VIDEO:
        scratch.mkdir(parents=True, exist_ok=True)
        path = scratch / f"{tag}.mp4"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color={tag}:s=64x96:r=10:d={seconds}",
                        "-f", "lavfi", "-i", f"sine=frequency={440 if tag == 'blue' else 880}:duration={seconds}",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path)],
                       check=True, timeout=60)
        _VIDEO[tag] = path.read_bytes()
    return _VIDEO[tag]


class AssembleDryRun(base.MediaDryRun):
    fixture_node_type = "assemble"
    status = ProjectStatus.assembling
    running_key = "assembling"
    api = "none"
    key = "final"

    # the Kie scenarios of the base class are about provider tasks: not applicable to a local node
    for _name in [n for n in dir(base.MediaDryRun) if n.startswith("test")]:
        locals()[_name] = None
    del _name

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.core_calls = 0
        self.core_hook = None  # an awaitable run inside the montage engine (a node that misbehaves)
        self.in_resume_only: list = []
        self.core_script: list = []
        self.sent: list[dict] = []
        self.final_during_core: list = []
        self.scratch = Path(self.tmp.name) / "scratch"
        fake_core = self.fake_core
        self.stack.enter_context(patch.object(asm, "_assemble_core", fake_core))
        self.stack.enter_context(patch.object(asm, "send_hitl_video", self.fake_hitl))

        async def child(session, project):
            return None

        import app.services.mass_factory as mass_factory
        self.stack.enter_context(patch.object(mass_factory, "on_child_montage_complete", child))

    # ---------------------------------------------------------------- the scripted montage engine
    @property
    def final_path(self) -> Path:
        return self.data / "final" / "actions-fixture.mp4"

    async def fake_core(self, session, project, bot, *, out_path, **kw):
        self.core_calls += 1
        self.in_resume_only.append(media_spend.in_resume_only())
        if self.core_hook is not None:
            await self.core_hook()
        self.final_during_core.append(self.final_path.read_bytes() if self.final_path.exists() else None)
        step = self.core_script.pop(0) if self.core_script else "good"
        if isinstance(step, BaseException):
            out_path.write_bytes(b"half written output")  # a killed ffmpeg leaves a part file
            raise step
        if step == "garbage":
            out_path.write_bytes(b"this is not a video")
            return 3.0, 0.0
        out_path.write_bytes(final_bytes("red", self.scratch))
        return 3.0, 0.0

    async def fake_hitl(self, bot, session, project, *, kind, video_path, caption, payload):
        request = HITLRequest(project_id=project.id, kind=kind, payload=payload)
        session.add(request)
        await session.flush()
        self.sent.append(payload)
        return request

    async def native_step(self, session, project):
        await asm._assemble_body(session, project, None)

    def result_files(self):
        return self.final_path.parent.glob("*.mp4")

    # ---------------------------------------------------------------- facts
    async def seed_old_final(self) -> bytes:
        """A previous, verified final: file, artifact row and recorded digest."""
        old = final_bytes("blue", self.scratch, seconds=4)
        self.final_path.parent.mkdir(parents=True, exist_ok=True)
        self.final_path.write_bytes(old)
        async with self.business() as session:
            session.add(Artifact(project_id=base.PID, kind=ArtifactKind.final_video, uuid="old" * 8,
                                 path=str(self.final_path),
                                 meta={"sha256": hashlib.sha256(old).hexdigest()}))
            await session.commit()
        return old

    async def approvals(self):
        async with self.business() as session:
            return (await session.scalars(select(HITLRequest).where(
                HITLRequest.kind == HITLKind.approve_final))).all()

    async def finals(self):
        async with self.business() as session:
            return (await session.scalars(select(Artifact).where(
                Artifact.kind == ArtifactKind.final_video))).all()

    def part_files(self):
        return list(self.final_path.parent.glob(".*.part.mp4"))

    def project_stub(self):
        return SimpleNamespace(id=base.PID, data_dir=self.data)

    # ================================================================ 1. what is PROVEN SAFE
    async def test_one_in_node_retry_replaces_the_old_final_only_after_verification_and_leaves_one_pending_approval(self):
        old = await self.seed_old_final()
        self.core_script = [RuntimeError("ffmpeg exited with 1")]
        await self.worker()
        self.assertEqual(self.core_calls, 2, "first attempt + exactly one retry")
        # the previous final survived the failed first attempt and was still there when the retry started
        self.assertEqual(self.final_during_core, [old, old])
        new = self.final_path.read_bytes()
        self.assertEqual(new, final_bytes("red", self.scratch))
        self.assertNotEqual(new, old)
        self.assertEqual(self.part_files(), [])
        approvals = await self.approvals()
        self.assertEqual(len(approvals), 1)
        self.assertIs(approvals[0].decision, HITLDecision.pending, "nothing here approves the final: a human does")
        self.assertEqual(approvals[0].payload["sha256"], hashlib.sha256(new).hexdigest())
        self.assertEqual(self.sent, [{"step": "final", "sha256": hashlib.sha256(new).hexdigest()}])
        digests = [(a.meta or {}).get("sha256") for a in await self.finals()]
        self.assertIn(hashlib.sha256(new).hexdigest(), digests)

    async def test_two_ffmpeg_failures_stop_at_one_retry_keep_the_old_final_and_create_no_approval(self):
        old = await self.seed_old_final()
        self.core_script = [RuntimeError("ffmpeg exited with 1"), RuntimeError("ffmpeg exited with 1")]
        with self.assertRaises(RuntimeError):
            await self.worker()
        self.assertEqual((asm.ASSEMBLE_MAX_ATTEMPTS, self.core_calls), (2, 2))
        self.assertEqual(self.final_path.read_bytes(), old)
        self.assertEqual(self.part_files(), [])
        self.assertEqual((await self.approvals(), self.sent), ([], []))

    async def test_a_failed_or_unknown_verification_changes_nothing_and_is_retried_once(self):
        old = await self.seed_old_final()
        self.core_script = ["garbage", "garbage"]
        with self.assertRaises(asm.AssembleVerificationError):
            await self.worker()
        self.assertEqual(self.core_calls, 2)
        self.assertEqual(self.final_path.read_bytes(), old)
        self.assertEqual((self.part_files(), await self.approvals(), self.sent), ([], [], []))
        async with self.business() as session:
            self.assertIs((await session.get(Project, base.PID)).status, ProjectStatus.assembling)

    async def test_an_input_error_is_never_retried(self):
        self.core_script = [RuntimeError("нет кадров с видео-клипами")]
        with self.assertRaises(RuntimeError):
            await self.worker()
        self.assertEqual(self.core_calls, 1)

    async def test_a_cancel_is_never_retried(self):
        self.core_script = [StepCancelledError("stop")]
        with self.assertRaises(StepCancelledError):
            await self.worker()
        self.assertEqual(self.core_calls, 1)

    async def test_a_second_assemble_of_the_same_output_does_not_create_a_second_approval(self):
        await self.worker()
        first = await self.approvals()
        async with self.business() as session:  # the same output assembled again (a human re-runs the node)
            project = await session.get(Project, base.PID)
            project.status = ProjectStatus.assembling
            await asm._assemble_body(session, project, None)
            await session.commit()
        again = await self.approvals()
        self.assertEqual((len(first), len(again), len(self.sent)), (1, 1, 1))
        self.assertIs(again[0].decision, HITLDecision.pending)

    async def test_no_paid_request_is_reachable_on_the_assemble_path(self):
        spies = [patch.object(spend_guard, name, side_effect=AssertionError(f"{name} must not run"))
                 for name in ("reserve", "record_actual", "schedule_actual")]
        from app.bots import kie_http
        spies.append(patch.object(kie_http, "kie_post_json", side_effect=AssertionError("createTask must not run")))
        guarded = []
        real = media_spend.assert_submit_allowed
        spies.append(patch.object(media_spend, "assert_submit_allowed",
                                  lambda *a, **k: (guarded.append(a), real(*a, **k))[1]))
        from contextlib import ExitStack
        with ExitStack() as stack:
            for spy in spies:
                stack.enter_context(spy)
            with media_spend.resume_only():
                await self.worker()
        self.assertEqual(guarded, [], "not a single paid seam was even asked")
        self.assertEqual(await self.task_ids(), [], "the provider task ledger holds nothing for assemble")
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))

    def test_static_scan_the_modules_assemble_runs_import_no_paid_provider_seam(self):
        root = Path(asm.__file__).resolve().parents[3]
        modules = ["app/orchestrator/steps/assemble.py", "app/services/assembly.py", "app/services/montage/variant2.py",
                   "app/services/montage/r15.py", "app/services/montage_asr.py", "app/services/sfx_mix.py",
                   "app/services/bgm.py", "app/services/artifact_verifier.py", "app/services/subtitles.py",
                   "app/services/plan_timestamps.py"]
        banned = ("kie_http", "kie_kling", "elevenlabs_api", "app.bots.outsee", "outsee_retry", "gpt_api", "gpt_client",
                  "text_llm", "media_jobs", "spend_guard")
        offenders = []
        for rel in modules:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import) else
                         [node.module or ""] + [a.name for a in node.names] if isinstance(node, ast.ImportFrom) else [])
                offenders += [(rel, n) for n in names if any(b in n for b in banned)]
        self.assertEqual(offenders, [])
        # the one helper that talks to Outsee (hero restore) is NOT part of recover_before_assemble
        from app.services import artifact_recovery
        source = inspect.getsource(artifact_recovery.recover_before_assemble)
        for forbidden in ("hero", "outsee", "synthesize", "generate"):
            self.assertNotIn(forbidden, source.lower())

    # ================================================================ 2. the three core gaps, closed
    async def failed_assemble(self, script=None, *, old_final=True):
        """The native attempt fails (ffmpeg twice by default), the native retries are exhausted, diagnosis ran."""
        if old_final:
            await self.seed_old_final()
        self.core_script = list(script) if script is not None else [RuntimeError("ffmpeg exited with 1")] * 2
        await self.fail_natively(RuntimeError)
        return await self.media_incident()

    async def revision(self):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            return await actions_recovery.input_revision(session, project, "n1")

    async def mutate(self, change):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            await change(session, project)
            await session.commit()

    async def decide(self, incident):
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=base.PID, execution_id=base.EXECUTION, enabled=True,
                             initiator="dry-run", rationale="local node dry run decision")
        record = await loop.begin(project_id=base.PID, execution_id=base.EXECUTION, incident_id=incident.incident_id)
        return loop, record, await loop.context(record["decision_id"])

    async def settle(self, *, rejected=False):
        """What the observer loop does after the worker returns: deliver the journal, reconcile the ticket.
        ``rejected``: the evidence is final-negative, so the late-evidence window is over before the last pass."""
        await self.sync()
        await self.deliver()
        await reconcile(self.observer)
        if rejected:
            await self.age_ticket()
            await reconcile(self.observer)

    async def age_ticket(self):
        """The 30 s window in which reconcile still waits for late evidence is over."""
        async with self.sessions() as session:
            row = (await session.scalars(select(RecoveryAction))).one()
            row.result = {**row.result, "worker_returned_at": "2020-01-01T00:00:00"}
            await session.commit()

    async def run_queued_retry(self, *, rejected=False):
        await self.worker(start=False)
        finish_job_tracking(base.PID)
        await self.settle(rejected=rejected)

    async def only_ticket(self):
        tickets = await self.tickets()
        self.assertEqual(len(tickets), 1)
        return tickets[0]

    async def control(self):
        async with self.sessions() as session:
            return await session.get(base_tests.ActionExecutionControl, base.EXECUTION)

    async def test_gap2_a_failed_assemble_with_an_earlier_final_is_typed_by_its_own_outcome_not_by_the_old_file(self):
        incident = await self.failed_assemble()
        old = final_bytes("blue", self.scratch, seconds=4)
        self.assertEqual(self.final_path.read_bytes(), old, "the previous final survived the failed attempt")
        self.assertEqual((incident.type, incident.details["verification_status"]), ("media_failure", "failed"))
        self.assertIs(incident.details["media"], True)
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["node_type"], proof["status"]), ("assemble", "failed"),
                         "the proof no longer passes because an OLD final.mp4 lies on disk")
        self.assertEqual(proof["reason"], "attempt_failed:RuntimeError")
        self.assertEqual(proof["attempt_failure"],
                         {"error_type": "RuntimeError", "retryable_by_node": True, "new_verified_artifact": False})
        self.assertIs(proof["items"][0]["stale_earlier_result"], True, "the old file is reported as stale")
        self.assertEqual(proof["items_passed"], 0)

    async def test_gap2_a_cancel_is_not_typed_as_a_failure_and_the_old_proof_rule_is_unchanged(self):
        await self.seed_old_final()
        self.core_script = [StepCancelledError("stop")]
        with self.assertRaises(StepCancelledError):
            await self.worker()
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertNotIn("attempt_failure", proof, "a user cancel is not an assemble failure")

    async def test_gap2_a_failed_assemble_without_a_final_is_typed_the_same_way(self):
        incident = await self.failed_assemble(old_final=False)
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["status"], proof["attempt_failure"]["retryable_by_node"]), ("failed", True))
        self.assertEqual(incident.type, "media_failure")

    async def test_gap3_the_baseline_covers_the_true_inputs_and_ignores_progress(self):
        incident = await self.failed_assemble()
        start = (await self.facts("node_start"))[-1]
        recorded = start.payload["recovery_input_revision"]
        self.assertTrue(recorded, "the unmanaged assemble attempt now records its input baseline")
        parts = start.payload["recovery_revision_parts"]
        self.assertIn("assemble_inputs", parts)
        self.assertIn("revision_scheme", parts)
        self.assertEqual(await self.revision(), recorded, "same inputs, same revision")

        async def progress(session, project):
            for frame in (await session.scalars(select(Frame))).all():
                frame.start_ts, frame.end_ts, frame.duration_seconds = 0.5, 3.5, 3.0
            session.add(Artifact(project_id=base.PID, kind=ArtifactKind.whisper_words, uuid="w" * 8, path="x.json"))
            session.add(Artifact(project_id=base.PID, kind=ArtifactKind.subtitle, uuid="s" * 8, path="x.ass"))
            project.status = ProjectStatus.audio_ready
            (self.data / "audio").mkdir(exist_ok=True)
            (self.data / "audio" / "words_ab12cd34.json").write_text("{}", encoding="utf-8")
            (self.data / "subs").mkdir(exist_ok=True)
            (self.data / "subs" / "subs_ab12cd34.ass").write_text("x", encoding="utf-8")
            self.final_path.write_bytes(b"a later final")

        await self.mutate(progress)
        self.assertEqual(await self.revision(), recorded, "frame timings, ASR/subtitle artifacts, status and the final "
                                                           "file are progress, not input")
        seen = {recorded}

        async def voiceover(session, project):
            frame = (await session.scalars(select(Frame))).first()
            frame.voiceover_text = frame.voiceover_text + " Новое предложение."

        async def clip(session, project):
            (self.data / "videos").mkdir(exist_ok=True)
            (self.data / "videos" / "clip_001_ab12cd34.mp4").write_bytes(b"clip")

        async def params(session, project):
            project.meta = {**project.meta, "node_step_params": {"assemble": {"skip_intro_enabled": True,
                                                                              "skip_intro_seconds": 1.0}}}

        async def voice(session, project):
            (self.data / "audio" / "voice_full.mp3").write_bytes(b"voice")

        async def more_frames(session, project):
            session.add(Frame(project_id=base.PID, number=2, uuid="bf" * 16, sort_key=2, voiceover_text="Бета.",
                              duration_seconds=2.0, attrs={}))

        current = recorded
        for change in (voiceover, clip, params, voice, more_frames):
            await self.mutate(change)
            current = await self.revision()
            self.assertNotIn(current, seen, change.__name__ + " must change the revision")
            seen.add(current)
        self.assertEqual(await self.revision(), current, "the revision is stable")
        self.assertTrue(incident)

    async def test_gap3_an_input_change_after_the_failure_refuses_the_retry(self):
        incident = await self.failed_assemble()
        await self.enable()

        async def voiceover(session, project):
            (await session.scalars(select(Frame))).first().voiceover_text = "Совсем другой текст."

        await self.mutate(voiceover)
        with self.assertRaisesRegex(ValueError, "input/config revision changed"):
            await self.recover(incident)
        self.assertEqual((await self.tickets(), self.core_calls), ([], 2))

    async def test_a_failed_assemble_is_admitted_for_the_supervisor_and_nothing_runs_until_a_decision(self):
        incident = await self.failed_assemble()
        await self.enable()
        loop, record, context = await self.decide(incident)
        self.assertEqual(context["node_capability"]["node_type"], "assemble")
        self.assertEqual(context["media_recovery"]["operation"], "retry_local_worker")
        self.assertIs(context["media_recovery"]["submits_new_paid_request"], False)
        self.assertIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        self.assertEqual((await self.tickets(), self.core_calls), ([], 2),
                         "nothing ran again: a ticket exists only after the decision asks for it")

    async def test_an_input_error_is_typed_but_the_node_itself_does_not_retry_it_so_the_supervisor_does_not_either(self):
        incident = await self.failed_assemble([RuntimeError("нет кадров с видео-клипами")])
        self.assertEqual(self.core_calls, 1)
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual(proof["attempt_failure"]["retryable_by_node"], False)
        await self.enable()
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)
        self.assertEqual(caught.exception.code, "failure_not_retryable")
        self.assertEqual(await self.tickets(), [])
        loop, record, context = await self.decide(incident)
        self.assertIsNone(context["media_recovery"]["operation"])
        self.assertEqual(context["media_recovery"]["denied"], "failure_not_retryable")
        self.assertNotIn("retry_same", context["allowed_actions"])

    # ================================================================ 3. gap 1: the non-model worker recovery
    async def test_failed_attempt_to_decision_to_one_verified_worker_retry(self):
        old = final_bytes("blue", self.scratch, seconds=4)
        incident = await self.failed_assemble()
        ids_before = await self.task_ids()
        await self.enable()
        loop, record, context = await self.decide(incident)
        with self.decision_model(["retry_same"]):
            await loop.step(record["decision_id"])
        # the ticket: ONE, queued for the ORIGINAL worker (not executed in the supervisor process)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "queued")
        pre = ticket.preconditions
        self.assertEqual((pre["media"], pre["local_worker"], pre["media_operation"]), (True, True, "retry_local_worker"))
        self.assertEqual((pre["effective_node_type"], pre["action_type"]), ("assemble", "retry_same"))
        self.assertIs(pre["submits_new_paid_request"], False)
        self.assertIs(pre["resume_only_scope"], True)
        self.assertEqual(pre["ledger_before"], local_node_recovery.ledger_summary_of([]))
        self.assertEqual(ticket.budget_state["recovery_limit"], 1)
        self.assertEqual(self.core_calls, 2, "the supervisor did not run the node itself")
        async with self.sessions() as session:
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual((row.status, row.action_id), ("waiting_worker", ticket.action_id))
        # the original worker runs it once, inside the resume-only scope
        await self.run_queued_retry()
        self.assertEqual(self.core_calls, 3)
        self.assertEqual(self.in_resume_only, [False, False, True], "only the supervised attempt is guarded")
        self.assertEqual(self.final_during_core[-1], old, "the old final stayed in place until the new one verified")
        new = self.final_path.read_bytes()
        self.assertEqual(new, final_bytes("red", self.scratch))
        # verification from independent facts: a verified ticket with every check true
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "verified", ticket.verification_result)
        checks = ticket.verification_result
        for name in checks["local_required_checks"]:
            self.assertIs(checks[name], True, name)
        for name in ("node_done", "node_end", "result_confirmed", "graph_matches", "audit_available",
                     "no_user_stop", "permission_active", "requests_complete", "transactional_done"):
            self.assertIs(checks[name], True, name)
        self.assertEqual((checks["data_applied"], checks["accepted_payload"]), (False, False),
                         "no model facts exist for assemble and none are required")
        self.assertEqual(checks["local_verifier"], "assemble")
        details = checks["local_details"]
        self.assertEqual(details["sha256"], hashlib.sha256(new).hexdigest())
        self.assertEqual(details["approve_final"]["pending"], 1)
        self.assertIs(details["approve_final"]["approved_by_supervisor"], False)
        self.assertEqual(details["paid_seam_hits"], 0)
        self.assertEqual(details["ledger_before"], details["ledger_after"])
        self.assertEqual(ticket.result["continuation"], "original runtime; mandatory HITL untouched")
        self.assertFalse((await self.control()).held)
        await self.diagnose()
        late = [(i.type, i.status) for i in await self.incidents() if i.attempt_id == ticket.new_attempt_id]
        self.assertEqual(late, [], "a successful retry opens no incident (its proof is read inside its own transaction)")
        proofs = [f.payload for f in await self.facts("node_result_verification")
                  if f.attempt_id == ticket.new_attempt_id]
        self.assertEqual([p["status"] for p in proofs], ["passed"])
        self.assertNotIn("attempt_failure", proofs[0])
        # the human barrier and the ledger
        approvals = await self.approvals()
        self.assertEqual(len(approvals), 1)
        self.assertIs(approvals[0].decision, HITLDecision.pending, "the supervisor never approves the final")
        self.assertEqual(approvals[0].payload["sha256"], hashlib.sha256(new).hexdigest())
        self.assertEqual(await self.task_ids(), ids_before, "the provider ledger did not move")
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))
        self.assertEqual(self.part_files(), [])
        facts = [f for f in await self.facts() if f.attempt_id == ticket.new_attempt_id]
        kinds = {f.kind for f in facts}
        self.assertFalse(kinds & {"model_start", "model_end", "parse_result", "data_applied"}, kinds)
        guard = [f for f in facts if f.kind == "local_recovery_guard"][-1].payload
        self.assertEqual((guard["resume_only"], guard["paid_seam_hits"]), (True, 0))
        # the decision completes from the persisted verification, without another model step
        await loop.tick()
        async with self.sessions() as session:
            row = await session.get(decision_v1.DecisionRecord, record["decision_id"])
            self.assertEqual(row.status, "done")
            self.assertTrue(row.verification["verified"])
        # cap: the decision owner refuses a competing recovery; the database refuses a second ticket
        with self.assertRaisesRegex(ValueError, "decision owner"):
            await self.recover(incident, "second-local-0001")
        from sqlalchemy.exc import IntegrityError
        async with self.sessions() as session:
            session.add(RecoveryAction(action_id="second", execution_id=base.EXECUTION, project_id=base.PID,
                incident_id=incident.incident_id, node_key="n1", source_attempt_id="s", new_attempt_id="n",
                idempotency_key="second-ticket-0001", preconditions={}, budget_state={}, status="reserved"))
            with self.assertRaises(IntegrityError):
                await session.commit()
        self.assertEqual(self.core_calls, 3, "one retry in the whole story")

    async def test_without_a_decision_layer_the_same_ticket_is_one_dispatch_and_idempotent(self):
        incident = await self.failed_assemble()
        await self.enable()
        result = await self.recover(incident)
        self.assertEqual(result["status"], "queued")
        again = await self.recover(incident)
        self.assertIs(again["idempotent_replay"], True)
        self.assertEqual(len(await self.tickets()), 1)
        await self.run_queued_retry()
        self.assertEqual((await self.only_ticket()).status, "verified")
        self.assertEqual(self.core_calls, 3)

    async def test_a_swallowed_paid_seam_refusal_still_fails_the_verification(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)

        async def node_that_tries_to_pay_and_ignores_the_refusal():
            try:
                media_spend.assert_submit_allowed("elevenlabs", "tts post")
            except media_spend.ResumeOnlyBlocked:
                pass  # a node swallowing the refusal and finishing anyway

        self.core_hook = node_that_tries_to_pay_and_ignores_the_refusal
        await self.run_queued_retry(rejected=True)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertIs(ticket.verification_result["no_paid_seam_hit"], False)
        self.assertEqual(ticket.verification_result["local_details"]["paid_seam_hits"], 1)
        self.assertIs(ticket.verification_result["verify_artifact_passed"], True, "only the guard failed")
        self.assertTrue((await self.control()).held)

    async def test_a_ledger_that_grew_during_the_retry_fails_the_verification(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)

        async def a_task_appears():
            async with self.ledger() as session:
                await spend_guard.append_task_event(session, project_id=base.PID, key="clip_001", task_id="X-1",
                    provider="kie", api="jobs", model="m", event="created", status="submitted")
                await session.commit()

        self.core_hook = a_task_appears
        await self.run_queued_retry(rejected=True)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertIs(ticket.verification_result["ledger_unchanged"], False)
        self.assertTrue((await self.control()).held)

    async def test_an_approval_decided_before_verification_fails_the_barrier_check(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)
        await self.worker(start=False)
        finish_job_tracking(base.PID)
        async with self.business() as session:
            for request in (await session.scalars(select(HITLRequest))).all():
                request.decision = HITLDecision.approved
            await session.commit()
        await self.settle(rejected=True)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertIs(ticket.verification_result["approve_final_pending"], False)

    async def test_a_final_that_is_older_than_the_claim_is_not_this_attempts_result(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)
        await self.worker(start=False)
        finish_job_tracking(base.PID)
        os.utime(self.final_path, (1_000_000, 1_000_000))
        await self.settle(rejected=True)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertIs(ticket.verification_result["produced_after_claim"], False)
        self.assertIs(ticket.verification_result["verify_artifact_passed"], True)

    async def test_a_retry_that_fails_again_is_held_for_a_human_and_keeps_the_old_final(self):
        old = final_bytes("blue", self.scratch, seconds=4)
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)
        self.core_script = [RuntimeError("ffmpeg exited with 1")] * 2
        with self.assertRaises(RuntimeError):
            await self.worker(start=False)
        finish_job_tracking(base.PID)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertTrue((await self.control()).held)
        self.assertEqual(self.final_path.read_bytes(), old)
        self.assertEqual((await self.approvals(), self.sent, self.part_files()), ([], [], []))
        self.assertEqual(self.core_calls, 4, "the in-node retry ran, nothing more")
        with self.assertRaisesRegex(ValueError, "single recovery already consumed|held"):
            await self.recover(incident, "another-local-0002")

    async def test_a_ledger_change_between_the_ticket_and_the_claim_blocks_the_run(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)
        async with self.ledger() as session:
            await spend_guard.append_task_event(session, project_id=base.PID, key="clip_002", task_id="X-2",
                provider="kie", api="jobs", model="m", event="created", status="submitted")
            await session.commit()
        calls = self.core_calls
        with self.assertRaisesRegex(RuntimeError, "ledger changed"):
            await self.worker(start=False)
        self.assertEqual(self.core_calls, calls, "the node never ran")
        self.assertEqual((await self.only_ticket()).status, "queued")

    async def test_an_input_change_between_the_ticket_and_the_claim_blocks_the_run(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)

        async def clip(session, project):
            (self.data / "videos").mkdir(exist_ok=True)
            (self.data / "videos" / "clip_009_ab12cd34.mp4").write_bytes(b"new clip")

        await self.mutate(clip)
        calls = self.core_calls
        with self.assertRaisesRegex(RuntimeError, "revisions changed before claim"):
            await self.worker(start=False)
        self.assertEqual(self.core_calls, calls)

    async def test_kill_switch_user_stop_and_a_spent_ticket_refuse_before_any_dispatch(self):
        incident = await self.failed_assemble()
        await self.enable()
        await kill_switch.set_state(self.sessions, active=True, actor="operator", rationale="emergency stop test")
        with self.assertRaisesRegex(ValueError, "kill switch active"):
            await self.recover(incident)
        await kill_switch.set_state(self.sessions, active=False, actor="operator", rationale="emergency stop off")
        async with self.sessions() as session:
            project = await session.get(Project, base.PID)
            project.meta = {**project.meta, "user_stop": True}
            await session.commit()
        with self.assertRaisesRegex(ValueError, "user_stop"):
            await self.recover(incident)
        self.assertEqual((await self.tickets(), self.core_calls), ([], 2))

    async def test_a_fresh_managed_start_of_assemble_is_refused_only_a_claimed_recovery_may_run_it(self):
        await self.failed_assemble()
        await self.enable()
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            project.status = ProjectStatus.assembling
            await session.commit()
        calls = self.core_calls
        with self.assertRaisesRegex(ValueError, "recovery only"):
            await self.worker(start=False)  # a managed attempt WITHOUT a claimed ticket
        self.assertEqual((self.core_calls, await self.tickets()), (calls, []))
        with self.assertRaisesRegex(ValueError, "recovery only"):
            node_capabilities.require_managed_capability("assemble")
        self.assertTrue(node_capabilities.require_managed_capability("assemble", recovery_claim=True).managed)

    async def test_an_interrupted_local_ticket_is_held_never_repeated(self):
        incident = await self.failed_assemble()
        await self.enable()
        await self.recover(incident)
        async with self.sessions() as session:  # the process died after the claim: no worker end ever arrives
            ticket = (await session.scalars(select(RecoveryAction))).one()
            ticket.status, ticket.created_at = "claimed", datetime(2020, 1, 1)
            await session.commit()
        await reconcile(self.observer)
        ticket = await self.only_ticket()
        self.assertEqual(ticket.status, "human_review")
        self.assertTrue((await self.control()).held)
        self.assertEqual(self.core_calls, 2)

    async def test_a_text_style_incident_for_assemble_never_reaches_the_local_path(self):
        incident = await self.failed_assemble()
        await self.enable()
        plain = [i for i in await self.incidents() if i.type == "contract_rejected"]
        for item in plain:
            with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required|"
                                                   "admitted for media incidents only"):
                await self.recover(item, "plain-local-0001")
        self.assertEqual(await self.tickets(), [])
        self.assertTrue(incident)

    # ================================================================ 4. what changed in the registries
    def test_assemble_is_admitted_as_a_local_worker_node_not_as_a_provider_resume(self):
        self.assertIn("assemble", media_admission.ADMITTED_NODES)
        self.assertIn("assemble", media_admission.LOCAL_NODES)
        self.assertNotIn("assemble", media_admission.RESUME_NODES)
        self.assertNotIn("assemble", revisions.MEDIA_RESUME_NODES)
        self.assertTrue(media_admission.local_worker("assemble"))
        self.assertFalse(media_admission.resumable("assemble"))
        capability = node_capabilities.capability_for("assemble")
        self.assertTrue(capability.managed)
        self.assertIn("retry_same", capability.actions)
        self.assertNotIn("retry_profile", capability.actions)
        spec = capability_spec_for("assemble")
        self.assertTrue(spec.local_recovery and not spec.recovery_only and spec.forbidden_code is None)
        self.assertEqual(spec.recovery_cap, 1)
        self.assertEqual(node_capabilities.media_recovery_decision("assemble")["decision"], "local_retry")

    def test_the_provider_resume_operation_never_runs_a_local_node(self):
        import asyncio
        with self.assertRaisesRegex(ValueError, "original worker"):
            asyncio.run(media_admission.run_and_verify(self.project_stub(), "assemble", {"operation": "x"}))

    def test_the_service_verdict_is_static_and_admission_needs_the_failed_attempts_own_facts(self):
        import asyncio
        report = asyncio.run(media_recovery.assess(self.project_stub(), "assemble"))
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]),
                         ("retry_same", "local_only_no_provider_spend"))
        self.assertIs(report["submits_new_paid_request"], False)
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            asyncio.run(media_admission.admit(self.project_stub(), "assemble"))
        self.assertEqual(caught.exception.code, "source_proof_missing")
        fact = SimpleNamespace(kind="node_result_verification", payload={"contract": "native_media", "status": "failed"})
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            local_node_recovery.check_source([fact])
        self.assertEqual(caught.exception.code, "source_proof_missing", "a proof that does not type the attempt")

    def test_the_local_rule_is_separate_from_the_model_rule_in_reconcile_and_the_text_rule_is_untouched(self):
        source = inspect.getsource(actions_recovery.reconcile)
        self.assertIn("(verification['data_applied'] and verification['accepted_payload'])", source)
        self.assertIn("required_checks", source)
        self.assertIn('(row.preconditions or {}).get("local_worker")', source)
        self.assertIn("local_node_recovery.verify", source)
        # the node itself still emits no model facts
        run_source = inspect.getsource(asm)
        for fact in ("data_applied", "parse_result", "model_start"):
            self.assertNotIn(fact, run_source)

    async def test_the_assemble_attempt_emits_no_model_facts(self):
        await self.worker()
        kinds = {f.kind for f in await self.facts()}
        self.assertFalse(kinds & {"model_start", "model_end", "parse_result", "data_applied", "apply_input"}, kinds)
        self.assertIn("node_result_verification", kinds)
        self.assertNotIn("local_recovery_guard", kinds, "an ordinary attempt is not guarded or changed")

    def test_the_media_branch_of_recover_node_dispatches_a_worker_only_for_a_local_node(self):
        source = inspect.getsource(actions_recovery.recover_node)
        self.assertIn("if media and not local:", source)
        media_at = source.index("return await _execute_media(")
        dispatch_at = source.index("start_node_running(node")
        self.assertLess(media_at, dispatch_at, "provider resumes still return BEFORE the worker dispatch line")
        self.assertIn("local = bool(media and media_admission.local_worker(node_type))", source)
        # W4-4: the text-style incident check is now the retry class (recovery_class), still only when not media
        self.assertIn("(rclass is None and not media)", source)


class FinishedFileNodes(unittest.IsolatedAsyncioTestCase):
    """audio and sfx_gen: only the already-finished-file / local parts could ever be resumed by a supervisor,
    never a new TTS or sound-effect request. Whether that is admissible is decided here, from facts."""

    async def asyncSetUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.project = SimpleNamespace(id=base.PID, data_dir=self.root / "project", meta={})
        (self.project.data_dir / "audio").mkdir(parents=True)
        self.voice = self.project.data_dir / "audio" / "voice_full.mp3"
        self.voice.write_bytes(base.media_bytes("audio", self.root / "scratch"))

    async def asyncTearDown(self):
        self.tmp.cleanup()

    async def test_audio_finished_file_is_a_resume_local_verdict_but_the_node_is_not_admitted(self):
        marker = self.project.data_dir / "audio" / ".tts_uncertain_deadbeef"
        marker.write_text("x", encoding="utf-8")  # a stale marker of an earlier timeout
        report = await media_recovery.assess(self.project, "audio")
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]), ("resume_local", "finished_file_on_disk"))
        self.assertEqual(report["keys"][0]["stale_markers"], [marker.name])
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await media_admission.admit(self.project, "audio")
        self.assertEqual(caught.exception.code, "node_not_admitted")
        self.assertNotIn("audio", media_admission.ADMITTED_NODES)
        self.assertFalse(node_capabilities.capability_for("audio").managed)

    async def test_resume_local_has_no_operation_behind_it_so_nothing_registers_the_finished_file(self):
        report = await media_recovery.assess(self.project, "audio")
        admission = {"operation": "resume_local", "verdict": report["verdict"], "report": report}

        async def fingerprint(_project_id):
            return frozenset()

        with patch.object(media_admission, "task_fingerprint", fingerprint):
            outcome = await media_admission.run_and_verify(self.project, "audio", admission)
        # the operation asks nobody and writes nothing: the Artifact / frame timings that make the file the
        # node's result are created only by the node's own run (generate_audio disk branch), which needs the worker
        self.assertEqual((outcome["resumed"], outcome["new_paid_requests"], outcome["node_started"]), ([], 0, False))
        self.assertFalse(outcome["verified"])
        self.assertIn("audio:not_resumed", outcome["problems"])

    async def test_the_paid_seams_of_audio_and_sfx_refuse_before_a_byte_leaves_inside_resume_only(self):
        sent = []

        async def post():
            sent.append("tts post")

        async def sfx(prompt, duration, out_path):
            sent.append("sfx post")

        with media_spend.resume_only():
            coro = post()
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await media_spend.track_eleven_post(coro, chars=12)
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await media_spend.track_eleven_sfx(sfx)("p", 1.0, self.root / "x.wav")
        self.assertEqual(sent, [])
        # outside the scope the same wrapper does call through: the guard is the scope, not a stub
        await media_spend.track_eleven_sfx(sfx)("p", 1.0, self.root / "x.wav")
        self.assertEqual(sent, ["sfx post"])

    async def test_sfx_gen_has_no_resume_local_verdict_at_all(self):
        from app.services.ai_result_io import save_ai_job_checkpoint
        # finished files, no fallback: nothing for a supervisor to do (the node reuses its checkpoint by itself)
        self.project.meta = {}
        save_ai_job_checkpoint(self.project, "sfx_files", {"files": [
            {"idx": 0, "provider": "elevenlabs", "path": "sfx/a.wav"},
            {"idx": 1, "provider": "elevenlabs", "path": "sfx/b.wav"}]})
        report = await media_recovery.assess(self.project, "sfx_gen")
        self.assertEqual(report["verdict"]["mode"], "nothing_to_do")
        # degraded events: a native bounded retry (retry_same) or a human (denied), never a local resume
        save_ai_job_checkpoint(self.project, "sfx_files", {"files": [
            {"idx": 0, "provider": "local_synth", "provider_fallback": {"may_have_charged": "no", "error_class": "http_500"}},
            {"idx": 1, "provider": "local_synth", "provider_fallback": {"may_have_charged": "uncertain", "error_class": "timeout"}},
            {"idx": 2, "provider": "local_synth", "provider_retry": 1, "provider_fallback": {"may_have_charged": "no"}}]})
        report = await media_recovery.assess(self.project, "sfx_gen")
        modes = {i["key"]: i["mode"] for i in report["keys"]}
        self.assertEqual(modes, {"sfx_0": "retry_same", "sfx_1": "denied", "sfx_2": "denied"})
        self.assertNotIn("resume_local", set(modes.values()))
        with self.assertRaises(media_admission.MediaAdmissionDenied):
            await media_admission.admit(self.project, "sfx_gen")

    def test_the_local_nodes_keep_their_honest_forbidden_codes(self):
        from app.orchestrator.node_spec import capability_spec_for
        self.assertEqual(capability_spec_for("audio").forbidden_code, "paid_no_idempotency")
        self.assertEqual(capability_spec_for("sfx_gen").forbidden_code, "paid_no_idempotency")
        # assemble was promoted by this dry run: no forbidden code any more
        self.assertIsNone(capability_spec_for("assemble").forbidden_code)
        self.assertNotIn("media_recovery_not_admitted", KNOWN_FORBIDDEN_CODES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
