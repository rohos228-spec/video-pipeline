"""Dry run that DECIDES whether a supervised retry of the local media nodes (assemble, and the finished-file
parts of audio and sfx_gen) can be admitted. Result: it cannot yet, and this file pins exactly why.

No network, no provider, no paid call. REAL: observe_attempt and the journal, the media_result verifier,
the Diagnoser, the DecisionSupervisor, ActionSupervisor.enable / recover_node and its gates, media_admission,
media_recovery.assess, the real ``assemble._assemble_body`` (atomic write, ``verify_artifact`` with ffprobe on
real files, in-node retry, approve_final barrier). SCRIPTED: the montage engine ``_assemble_core`` (it is a local
ffmpeg pipeline, not a provider: here it writes a real small mp4 or fails like ffmpeg), the Telegram sender
and the mass-factory hook. Nothing here can reach a provider; the file also proves that by guard and by scan.

What the evidence says (each point is a test below):

PROVEN SAFE (so a supervisor retry would not be unsafe in itself)
* in-node retry: exactly one, only for ffmpeg errors and failed verification, never for input errors or a cancel;
* atomic output: the previous final survives every failed attempt, a part file never lingers;
* ``verify_artifact`` runs on the NEW file before it replaces the old one; a failed or unknown proof changes nothing;
* approve_final is created once per (project, sha256), stays ``pending`` for a human, nothing here approves it;
* no paid request is reachable on the assemble path (guard + scan).

NOT PROVEN / MISSING (so the node stays observed, forbidden code ``media_recovery_not_admitted``)
* the worker dispatch of ``recover_node`` is built around a MODEL request (``model_start`` facts, an
  ``invalid_json`` / ``missing_payload`` incident, the first-body hash) and its ``reconcile`` verification needs
  ``data_applied`` + ``parse_result``: an assemble attempt emits neither, so it could never reach ``verified``;
* the media branch of ``recover_node`` never dispatches a worker (it resumes a provider task inside the
  supervisor process); assemble has no provider task and needs the worker (Bot, project session, ffmpeg);
* a failed assemble with an earlier verified final is NOT typed as a media incident (the proof passes for the
  old file; the failure is a plain ``contract_rejected``), and the request-input baseline is not recorded for an
  unmanaged assemble attempt, so the input-revision gate has nothing to compare.
"""
import ast
import hashlib
import inspect
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

import media_dry_run_base as base
from app.models import (
    Artifact, ArtifactKind, HITLDecision, HITLKind, HITLRequest, NodeRun, Project, ProjectStatus,
)
from app.orchestrator.steps import assemble as asm
from app.services import (
    actions_recovery, media_admission, media_recovery, media_spend, node_capabilities, revisions, spend_guard,
)
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

    # ================================================================ 2. what is MISSING, as executable facts
    async def test_a_failed_assemble_with_an_earlier_final_is_not_typed_as_a_media_incident(self):
        await self.seed_old_final()
        self.core_script = [RuntimeError("ffmpeg exited with 1")] * 2
        await self.fail_natively(RuntimeError)
        incidents = await self.incidents()
        self.assertFalse([i for i in incidents if i.details.get("media")],
                         "if this fails the diagnosis types the failure: re-evaluate the admission")
        self.assertIn("contract_rejected", {i.type for i in incidents})
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["node_type"], proof["status"]), ("assemble", "passed"),
                         "the proof re-reads the OLD final: it says passed although the attempt failed")
        start = (await self.facts("node_start"))[-1]
        self.assertIsNone(start.payload["recovery_input_revision"],
                          "no request-input baseline is recorded for an unmanaged assemble attempt")
        await self.enable()
        plain = [i for i in incidents if i.type == "contract_rejected"][0]
        with self.assertRaisesRegex(ValueError, "current mandatory JSON/payload incident required"):
            await self.recover(plain)
        self.assertEqual(await self.tickets(), [])

    async def test_a_failed_assemble_without_a_final_is_a_typed_media_failure_but_the_node_is_not_admitted(self):
        self.core_script = [RuntimeError("ffmpeg exited with 1")] * 2
        await self.fail_natively(RuntimeError)
        incident = await self.media_incident()
        self.assertEqual(incident.type, "media_failure")
        self.assertEqual(incident.details["verification_status"], "failed")
        await self.enable()
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)
        self.assertEqual(caught.exception.code, "node_not_admitted")
        with self.assertRaises(media_admission.MediaAdmissionDenied):
            await media_admission.admit(self.project_stub(), "assemble")
        self.assertEqual(await self.tickets(), [])
        # the decision layer offers the model reading and a human, never retry_same, for this node
        from app.services import decision_v1
        loop = decision_v1.DecisionSupervisor(self.observer)
        await loop.configure(project_id=base.PID, execution_id=base.EXECUTION, enabled=True,
                             initiator="dry-run", rationale="local node dry run decision")
        record = await loop.begin(project_id=base.PID, execution_id=base.EXECUTION, incident_id=incident.incident_id)
        context = await loop.context(record["decision_id"])
        self.assertNotIn("retry_same", context["allowed_actions"])
        self.assertNotIn("retry_profile", context["allowed_actions"])
        self.assertEqual(self.core_calls, 2, "nothing ran again: the supervisor never re-dispatched the node")

    def test_assemble_is_in_no_admission_set_and_cannot_be_started_by_the_supervisor(self):
        self.assertNotIn("assemble", media_admission.ADMITTED_NODES)
        self.assertNotIn("assemble", revisions.MEDIA_RESUME_NODES)
        capability = node_capabilities.capability_for("assemble")
        self.assertFalse(capability.managed)
        self.assertFalse({"retry_same", "retry_profile", "finish"} & set(capability.actions))
        with self.assertRaisesRegex(ValueError, "no accepted managed executor"):
            node_capabilities.require_managed_capability("assemble")
        decision = node_capabilities.media_recovery_decision("assemble")
        self.assertEqual(decision["decision"], "human_only")

    def test_the_service_knows_assemble_is_retryable_but_that_verdict_is_not_incident_based(self):
        import asyncio
        report = asyncio.run(media_recovery.assess(self.project_stub(), "assemble"))
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]),
                         ("retry_same", "local_only_no_provider_spend"))
        self.assertIs(report["submits_new_paid_request"], False)
        # the same verdict for a project that never failed: it is a static statement about the node, not evidence
        self.assertEqual(report["keys"][0]["key"], "final")

    def test_reconcile_verifies_a_dispatched_recovery_only_through_model_facts(self):
        source = inspect.getsource(actions_recovery.reconcile)
        self.assertIn("(verification['data_applied'] and verification['accepted_payload'])", source)
        self.assertIn("required_checks", source)
        # an assemble attempt emits no data_applied / parse_result: see test_the_assemble_attempt_emits_no_model_facts
        run_source = inspect.getsource(asm)
        for fact in ("data_applied", "parse_result", "model_start"):
            self.assertNotIn(fact, run_source)

    async def test_the_assemble_attempt_emits_no_model_facts(self):
        await self.worker()
        kinds = {f.kind for f in await self.facts()}
        self.assertFalse(kinds & {"model_start", "model_end", "parse_result", "data_applied", "apply_input"}, kinds)
        self.assertIn("node_result_verification", kinds)

    def test_the_media_branch_of_recover_node_never_dispatches_a_worker(self):
        source = inspect.getsource(actions_recovery.recover_node)
        media_at = source.index("return await _execute_media(")
        dispatch_at = source.index("start_node_running(node")
        self.assertLess(media_at, dispatch_at, "media returns BEFORE the worker dispatch line")
        self.assertIn("incident.type not in {\"invalid_json\", \"missing_payload\"} and not media", source)


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
        self.assertEqual(capability_spec_for("assemble").forbidden_code, "media_recovery_not_admitted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
