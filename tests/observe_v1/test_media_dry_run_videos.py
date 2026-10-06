"""Dry run of the supervised resume of `videos` (Kie task of one clip): see media_dry_run_base.

The node body is the node's own paid ladder, outsee_retry.generate_video_with_retries (Kie Seedance first),
called with the node's own clip file names; the Kie HTTP boundary is the only fake. The Outsee browser
ladder of the real step is not involved (Outsee has no task id and is never resumed).
"""
import unittest
import uuid

import pytest

import media_dry_run_base as base
from app.bots import kie_http
from app.models import Frame, ProjectStatus
from app.services import kie_catalog, outsee_retry
from sqlalchemy import select

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


class VideosDryRun(base.MediaDryRun):
    fixture_node_type = "videos"
    status = ProjectStatus.generating_videos
    running_key = "generating_videos"
    api = "jobs"
    media_kind = "video"
    result_ext = ".mp4"
    key = "clip_001"
    single_submit = False

    async def native_step(self, session, project):
        out = project.data_dir / "videos" / f"clip_001_{uuid.uuid4().hex[:8]}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        if self.single_submit:  # one submit, no fallback rungs: a clean "provably failed" history
            spec = kie_catalog.get_model("z-image")
            payload = kie_catalog.build_payload(spec, {"prompt": "p", "aspect_ratio": "9:16"})
            await kie_http.run_generation(spec, payload, out, timeout_s=60, project_id=project.id)
            return
        await outsee_retry.generate_video_with_retries(
            None, None, prompt="a calm shot", out_path=out, project_id=project.id,
            model_slug="seedance-1-5-pro", resolution="720p", aspect_ratio="9:16", timeout=60)

    def result_files(self):
        return (self.data / "videos").glob("clip_001_*.mp4")

    async def change_request_inputs(self):
        async with self.business() as session:
            frame = await session.scalar(select(Frame).where(Frame.number == 1))
            frame.animation_prompt = "быстрый отъезд от папки"
            await session.commit()

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
