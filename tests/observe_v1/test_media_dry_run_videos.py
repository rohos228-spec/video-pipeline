"""Dry run of the supervised resume of `videos` (Kie task of one clip): see media_dry_run_base.

C07 (W4-5): the node body is the REAL step ``generate_videos.run`` (claim loop, shot1 job with its own
sessions, the paid ladder ``generate_video_with_retries``, ``kie_http``, post-step validation). Only the
provider boundary is substituted: the Kie HTTP (createTask / status / download, from the base), the public
upload of the start frame and the Telegram send of the step. The Outsee browser of the step is not opened
(the video generator of the fixture is a Kie one, ``seedance_1_5_pro``); see w4_5_real_step_stand for the
honest limits (mirror of the master rows, ProcessDied modelling).
"""
import unittest
from unittest.mock import patch

import pytest

import media_dry_run_base as base
import w4_5_real_step_stand as stand
from app.bots import kie_kling
from app.models import Frame, HITLRequest, Project, ProjectStatus
from app.orchestrator.steps import generate_videos
from sqlalchemy import select

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


from app.bots.outsee import OutseeImageError  # noqa: E402


class VideosDryRun(stand.RealStepStand, base.MediaDryRun):
    fixture_node_type = "videos"
    status = ProjectStatus.generating_videos
    running_key = "generating_videos"
    api = "jobs"
    media_kind = "video"
    result_ext = ".mp4"
    key = "clip_001"
    project_fields = {"video_generator": "seedance_1_5_pro"}

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.uploads, self.ids_at_first_poll, self.swallowed = 0, None, []
        scenes = self.data / "scenes"
        scenes.mkdir(parents=True, exist_ok=True)
        # a valid shot_01 scene image (>= 200 KB, PNG magic) as the step expects on disk
        (scenes / "frame_001_ab12cd34.png").write_bytes(base.media_bytes("image", self.data / "scratch") + bytes(210_000))

        async def upload(*_a, **_k):
            self.uploads += 1
            return "https://cdn.example/start-frame.png"

        for patcher in (patch("app.bots.outsee_http.ensure_public_image_url", upload),
                        patch.object(generate_videos, "get_gpt_client", lambda: base.FakeGpt()),
                        patch.object(generate_videos, "_video_http_primary", lambda: True),
                        # the Kling 2.6 fallback rung of the ladder has its own HTTP seam: same provider substitute
                        patch.object(kie_kling, "kie_post_json", self.fake_post),
                        patch.object(kie_kling, "kie_api_configured", lambda: True),
                        patch.object(kie_kling, "kie_api_base_url", lambda: "https://kie.invalid"),
                        patch.object(kie_kling, "_POLL_INTERVAL_S", 0.0),
                        patch.object(generate_videos, "generate_video_with_retries",
                                     self.record_swallowed(generate_videos.generate_video_with_retries)),
                        patch.object(generate_videos, "asyncio", stand.DyingAsyncio())):
            self.stack.enter_context(patcher)

    async def fake_send(self, client, request, **kw):
        url = str(request.url)
        if ("recordInfo" in url or "record-info" in url) and self.ids_at_first_poll is None:
            self.ids_at_first_poll = await self.task_ids()
        return await super().fake_send(client, request, **kw)

    async def native_step(self, session, project):
        self.swallowed = []
        await generate_videos.run(session, project, None)
        self.surface_swallowed(project)

    def result_files(self):
        return (self.data / "videos").glob("clip_001_*.mp4")

    async def change_request_inputs(self):
        async with self.business() as session:
            frame = await session.scalar(select(Frame).where(Frame.number == 1))
            frame.animation_prompt = "быстрый отъезд от папки"
            await session.commit()

    async def test_provably_failed_task_gets_no_paid_retry_from_the_supervisor(self):
        """The whole real ladder failed (every task provably failed at the provider). The service no longer calls
        the node retryable: seven failed tasks hit the retry cap, the verdict is a typed denial, and the
        supervisor submits nothing (the old one-task case "retry_same, not admitted" cannot arise from the real
        ladder, which never stops after one failed task)."""
        from app.generation_options import VIDEO_FALLBACK_ATTEMPTS, VIDEO_PRIMARY_TOTAL_ATTEMPTS
        from app.services.video_error_policy import VideoLadderExhaustedError
        ladder_total = VIDEO_PRIMARY_TOTAL_ATTEMPTS + VIDEO_FALLBACK_ATTEMPTS
        self.poll_script = ["fail"] * 40
        await self.fail_natively(VideoLadderExhaustedError)
        self.assertEqual(self.creates, ladder_total)
        incident = await self.media_incident()
        report = await base.media_recovery.assess(base.SimpleNamespace(id=base.PID, data_dir=self.data),
                                                  self.fixture_node_type)
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]), ("denied", "retry_cap_reached"))
        await self.enable()
        with self.assertRaises(base.media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)
        self.assertEqual(caught.exception.code, "verdict_denied")
        self.assertIn("retry_cap_reached", str(caught.exception))
        self.assertEqual((self.creates, await self.tickets()), (ladder_total, []))

    async def test_two_simultaneous_recoveries_of_one_node_make_exactly_one_ticket(self):
        await self.killed_then_native_poll_timeout()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["success"]
        creates_before = self.creates
        import asyncio
        results = await asyncio.gather(self.recover(incident, "race-0000000a"),
                                       self.recover(incident, "race-0000000b"), return_exceptions=True)
        won = [r for r in results if not isinstance(r, BaseException)]
        refused = [r for r in results if isinstance(r, BaseException)]
        self.assertEqual((len(won), len(refused)), (1, 1), results)
        self.assertIsInstance(refused[0], ValueError, refused[0])
        tickets = await self.tickets()
        self.assertEqual(len(tickets), 1)
        self.assertEqual(self.creates, creates_before, "a race must never create a second paid task")
        self.assertEqual(len(list(self.result_files())), 1)

    async def test_the_failed_attempt_may_edit_frame_progress_without_invalidating_the_baseline(self):
        """The real videos step writes per-frame bookkeeping (fail counter, in-flight marker) while it
        fails. That is not a change of the REQUEST inputs and must not block the resume."""
        await self.killed_then_native_poll_timeout()
        async with self.business() as session:
            frame = await session.scalar(select(Frame).where(Frame.number == 1))
            frame.attrs = {**(frame.attrs or {}), "video_gen_fail_count": 4, "video_gen_inflight": True}
            frame.status = frame.status
            await session.commit()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["success"]
        result = await self.recover(incident)
        self.assertEqual(result["status"], "verified", result["verification_result"])

    async def test_outsee_intent_without_a_task_id_is_never_resumed(self):
        """Outsee leaves only an intent row: unknown outcome, no id to poll, nothing to resume."""
        from app.services import spend_guard
        await self.killed_then_native_poll_timeout()
        async with self.ledger() as session:
            await spend_guard.append_task_event(session, project_id=base.PID, key="clip_003",
                task_id="intent:abc123", provider="outsee", api="outsee", model="seedance",
                event="intent", status="submit_unknown", note="ph=aa video")
            await session.commit()
        incident = await self.media_incident()
        await self.enable()
        self.poll_script = ["success"]
        from app.services import media_admission
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await self.recover(incident)
        self.assertIn("unknown_outcome_outsee", str(caught.exception))
        self.assertEqual((self.downloads, await self.tickets()), (0, []))

    # ---------------------------------------------------------------- C07: what only the real step can show
    async def test_real_step_one_submit_task_id_recorded_before_the_first_poll_clip_arrives(self):
        """D: the real step, one frame, the provider answers on the second poll. Exactly one createTask, the task
        id is already in the ledger when the first status request goes out, the clip is downloaded once, the
        step advances the project and hands over to the human check."""
        self.poll_script = ["waiting", "success"]
        await self.worker()
        self.assertEqual((self.creates, self.polls, self.downloads), (1, 2, 1))
        self.assertEqual([i for i in self.ids_at_first_poll if not i.startswith("intent:")], ["T-1"])
        self.assertEqual(self.uploads, 1, "the start frame goes to the provider once, as a public url")
        self.assertEqual(self.last_status_change, (ProjectStatus.generating_videos, ProjectStatus.videos_ready))
        self.assertEqual(len(list(self.result_files())), 1)
        async with self.business() as session:
            asked = (await session.scalars(select(HITLRequest))).all()
        self.assertEqual([str(r.kind.value) for r in asked], ["approve_videos"], "the step asks the human once")
        statuses = [r.status for r in await self.ledger_rows() if r.task_id == "T-1"]
        self.assertEqual(statuses[-1], "downloaded")

    async def test_real_step_success_is_verified_by_the_attempt_session_not_failed_too_early(self):
        """D (fixed in W4 integration): the clip row is only flushed when the step returns. The verification of
        the same attempt reads the flushed rows through the attempt's own session, so a successful step is judged
        passed (it used to be failed/missing_clip from the committed-only read)."""
        from app.services import media_verification
        self.poll_script = ["success"]
        await self.worker()
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["node_type"], proof["status"]), ("videos", "passed"), proof)
        self.assertEqual(proof["items"][0]["provenance"]["task_status"], "downloaded")
        self.assertEqual(proof["counts"]["clips_total"], 1)
        async with self.business() as reader:
            fresh = await media_verification.build_proof(reader, await reader.get(Project, base.PID), "videos")
        self.assertEqual((fresh["status"], fresh["items_passed"]), ("passed", 1), fresh)

    async def test_real_step_ladder_submits_at_most_seven_tasks_then_skips_the_frame(self):
        """D: every task fails at the provider. The real ladder (primary rungs, then the Kling fallback rungs)
        makes at most primary + fallback submits, each with its own task id; the step marks the frame as skipped
        and returns without a clip. No submit beyond the ladder."""
        from app.generation_options import VIDEO_FALLBACK_ATTEMPTS, VIDEO_PRIMARY_TOTAL_ATTEMPTS
        from app.services.video_error_policy import VideoLadderExhaustedError
        ladder_total = VIDEO_PRIMARY_TOTAL_ATTEMPTS + VIDEO_FALLBACK_ATTEMPTS
        self.assertLessEqual(ladder_total, 7)
        self.poll_script = ["fail"] * 40
        error = await self.fail_natively(VideoLadderExhaustedError)
        self.assertIsInstance(error, VideoLadderExhaustedError)
        self.assertEqual(self.creates, ladder_total)
        ids = [i for i in await self.task_ids() if not i.startswith("intent:")]
        self.assertEqual(ids, sorted(f"T-{n}" for n in range(1, ladder_total + 1)))
        self.assertEqual(len(list(self.result_files())), 0)
        frame = await self.step_database_frame()
        self.assertTrue(frame.attrs.get("video_gen_skip"), frame.attrs)
        self.assertGreaterEqual(int(frame.attrs.get("video_gen_fail_count") or 0), 5)

    async def test_default_video_generator_is_outsee_so_the_real_step_makes_no_kie_request(self):
        """D (route fact): with the default generator (veo_3_fast) the real step goes to Outsee, not to Kie.
        Without an Outsee key and a browser it fails before any request: zero Kie submits, zero polls."""
        from app.models import Project
        for factory in (self.business, self.ledger):
            async with factory() as session:
                project = await session.get(Project, base.PID)
                project.video_generator = None
                await session.commit()
        with self.assertRaises(OutseeImageError):
            await self.worker()
        self.assertEqual((self.creates, self.polls, self.downloads, self.uploads), (0, 0, 0, 0))
        self.assertEqual(len(list(self.result_files())), 0)
        self.assertEqual([i for i in await self.task_ids() if not i.startswith("intent:")], [])
        self.assertTrue(self.swallowed, "the ladder raised the typed Outsee failure to the step")
        self.assertEqual(type(self.swallowed[-1]).__name__, "OutseeImageError")

    async def test_frame_failure_is_not_swallowed_so_the_attempt_ends_with_node_error_and_recovery_is_allowed(self):
        """D (fixed in W4 integration): the step used to swallow the frame failure and return; the attempt ended
        with node_end and the supervised recovery was refused as an unfinished attempt. Now the step raises the
        typed failure after the frame loop (no extra paid call: only the recorded error is re-raised), the attempt
        ends with node_error and the resume of the SAME provider task is not refused."""
        await self.killed_then_native_poll_timeout()
        facts = await self.facts()
        last_attempt = [f for f in facts if f.kind in {"node_start", "node_end", "node_error"}]
        self.assertEqual([f.kind for f in last_attempt][-2:], ["node_start", "node_error"], [f.kind for f in last_attempt])
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["node_type"], proof["status"]), ("videos", "failed"))
        incident = await self.media_incident()
        self.assertEqual(incident.type, "media_failure")
        await self.enable()
        self.poll_script = ["success"]
        creates_before = self.creates
        result = await self.recover(incident)
        self.assertEqual(result["status"], "verified", result.get("verification_result"))
        self.assertEqual((self.creates, len(await self.tickets())), (creates_before, 1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
