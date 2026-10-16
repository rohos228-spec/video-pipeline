import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest

import media_dry_run_base as base
from app.orchestrator.node_spec import capability_spec_for
from app.services import (
    local_node_recovery, media_admission, media_recovery, media_spend, node_capabilities, revisions, sfx_gen,
)
from app.services.ai_result_io import ai_job_checkpoint
from app.services.sfx_plan import SfxEvent
from app.settings import settings

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")

_REAL_CLIENT = httpx.AsyncClient


class _Sess:
    def __init__(self):
        self.commits = 0

    async def flush(self):
        return None

    async def commit(self):
        self.commits += 1


class Wire:

    def __init__(self, mp3: bytes, script):
        self.mp3, self.script, self.posts = mp3, list(script), []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.posts.append(str(request.url))
        step = self.script[len(self.posts) - 1] if len(self.posts) <= len(self.script) else "ok"
        if step == "ok":
            return httpx.Response(200, content=self.mp3)
        if isinstance(step, int):
            return httpx.Response(step, text="not found")
        if step == "timeout":
            raise httpx.ReadTimeout("t")
        raise step

    def client(self, *a, **k):
        k.pop("transport", None)
        return _REAL_CLIENT(*a, transport=httpx.MockTransport(self.handler), **k)


class SfxGenDryRun(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mp3 = base.media_bytes("audio", self.root / "scratch")
        self.project = SimpleNamespace(id=base.PID, data_dir=self.root / "project", meta={})
        self.events = [SfxEvent(i + 1, float(i), 2.0, "hit", f"p{i}", 0.5, False) for i in range(3)]
        self.sess = _Sess()
        self.facts: list = []
        import app.services.observation as obs
        self.stack = [patch.object(obs, "emit", lambda kind, payload=None, **k: self.facts.append((kind, payload))),
                      patch.object(settings, "elevenlabs_api_key", "test-key")]
        for cm in self.stack:
            cm.start()

    async def asyncTearDown(self):
        for cm in reversed(self.stack):
            cm.stop()
        self.tmp.cleanup()

    async def run_node(self, wire: Wire, **kw):
        with patch("httpx.AsyncClient", wire.client):
            return await sfx_gen.generate_sfx_files(self.sess, self.project, self.events, **kw)


    async def test_a_wrong_endpoint_is_recorded_with_its_http_status_not_as_a_silent_local_synth(self):
        wire = Wire(self.mp3, [404, 404, 404])
        files = await self.run_node(wire)
        self.assertEqual([f["provider"] for f in files], ["local_synth"] * 3)
        self.assertTrue(all(u.endswith("/v1/sound-generation") for u in wire.posts))
        fallbacks = [p for k, p in self.facts if k == "provider_fallback"]
        self.assertEqual([p["http_status"] for p in fallbacks], [404, 404, 404])
        self.assertEqual({p["may_have_charged"] for p in fallbacks}, {"no"})
        self.assertEqual(files[0]["provider_fallback"]["http_status"], 404)

    async def test_a_timeout_has_no_http_status_and_stays_uncertain(self):
        files = await self.run_node(Wire(self.mp3, ["timeout", "ok", "ok"]))
        fb = files[0]["provider_fallback"]
        self.assertEqual((fb["error_class"], fb["http_status"], fb["may_have_charged"]),
                         ("ReadTimeout", None, "uncertain"))


    async def failed_attempt(self):
        wire = Wire(self.mp3, ["ok", "ok", asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(wire)
        return wire

    def four_events(self):
        self.events = [SfxEvent(i + 1, float(i), 2.0, "hit", f"p{i}", 0.5, False) for i in range(4)]

    async def test_1_a_failed_attempt_keeps_the_paid_events_in_a_per_event_checkpoint(self):
        wire = await self.failed_attempt()
        self.assertEqual(len(wire.posts), 3)
        self.assertEqual(len(list((self.project.data_dir / "sfx").glob("sfx_*.mp3"))), 2)
        prog = ai_job_checkpoint(self.project, "sfx_progress")
        self.assertEqual(sorted(prog["events"]), ["0", "1"])
        self.assertEqual(prog["events"]["0"]["provider"], "elevenlabs")
        self.assertEqual(list(prog["inflight"]), ["2"])
        self.assertIsNone(ai_job_checkpoint(self.project, "sfx_files"))
        self.assertGreaterEqual(self.sess.commits, 5)

    async def test_2_the_resume_only_rerun_after_a_failed_attempt_makes_zero_posts_and_does_not_resend(self):
        await self.failed_attempt()
        wire = Wire(self.mp3, [])
        hits: list = []
        with media_spend.resume_only(hits):
            again = await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])
        self.assertEqual(hits, [])
        self.assertEqual([f["provider"] for f in again], ["elevenlabs", "elevenlabs", "local_synth"])
        self.assertEqual(again[2]["provider_fallback"]["may_have_charged"], "uncertain")
        self.assertIsNone(ai_job_checkpoint(self.project, "sfx_progress"))
        self.assertEqual(len(ai_job_checkpoint(self.project, "sfx_files")["files"]), 3)
        report = await media_recovery.assess(self.project, "sfx_gen")
        self.assertEqual(report["keys"][0]["code"], "unknown_outcome_elevenlabs")

    async def test_2a_failure_after_2_of_4_events_rerun_posts_only_the_two_unsent_events(self):
        self.four_events()
        wire = Wire(self.mp3, [])
        real = media_spend.assert_submit_allowed
        calls = []

        def guard(provider, what="submit"):
            calls.append(1)
            if len(calls) == 3:
                raise media_spend.ResumeOnlyBlocked(provider, what)
            return real(provider, what)
        with patch.object(media_spend, "assert_submit_allowed", guard):
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await self.run_node(wire)
        self.assertEqual(len(wire.posts), 2)
        prog = ai_job_checkpoint(self.project, "sfx_progress")
        self.assertEqual((sorted(prog["events"]), prog["inflight"]), (["0", "1"], {}))
        rerun = Wire(self.mp3, [])
        files = await self.run_node(rerun)
        self.assertEqual(len(rerun.posts), 2)
        self.assertEqual([f["idx"] for f in files], [0, 1, 2, 3])
        self.assertEqual({f["provider"] for f in files}, {"elevenlabs"})

    async def test_2c_failure_after_2_of_4_events_the_sent_but_unanswered_third_is_never_resent(self):
        self.four_events()
        wire = Wire(self.mp3, ["ok", "ok", asyncio.CancelledError()])
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(wire)
        self.assertEqual(len(wire.posts), 3)
        rerun = Wire(self.mp3, [])
        files = await self.run_node(rerun)
        self.assertEqual(len(rerun.posts), 1)
        self.assertEqual([f["provider"] for f in files],
                         ["elevenlabs", "elevenlabs", "local_synth", "elevenlabs"])
        self.assertEqual(files[2]["provider_fallback"]["may_have_charged"], "uncertain")
        again = Wire(self.mp3, [])
        await self.run_node(again, retry_degraded=True)
        self.assertEqual(again.posts, [])

    async def test_2d_a_third_run_after_a_second_failure_does_not_resend_checkpointed_events(self):
        self.four_events()
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(Wire(self.mp3, ["ok", asyncio.CancelledError()]))
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(Wire(self.mp3, ["ok", asyncio.CancelledError()]))
        final = Wire(self.mp3, [])
        files = await self.run_node(final)
        self.assertEqual(final.posts, [])
        self.assertEqual([f["provider"] for f in files],
                         ["elevenlabs", "local_synth", "elevenlabs", "local_synth"])

    async def test_2b_the_resume_only_rerun_finishes_only_when_every_event_is_already_checkpointed(self):
        first = Wire(self.mp3, ["ok", "ok", "ok"])
        done = await self.run_node(first)
        self.assertEqual(len(first.posts), 3)
        wire = Wire(self.mp3, [])
        with media_spend.resume_only():
            again = await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])
        self.assertEqual([f["path"] for f in again], [f["path"] for f in done])

    async def test_3_a_degraded_event_retry_needs_a_new_paid_post_and_is_refused_in_resume_only(self):
        await self.run_node(Wire(self.mp3, ["ok", 500, "ok"]))
        rec = ai_job_checkpoint(self.project, "sfx_files")["files"][1]
        self.assertEqual(rec["provider_fallback"]["http_status"], 500)
        wire = Wire(self.mp3, [])
        with media_spend.resume_only():
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])

    async def test_3b_an_uncertain_fallback_is_never_retried_it_stays_with_a_human(self):
        await self.run_node(Wire(self.mp3, ["ok", "timeout", "ok"]))
        wire = Wire(self.mp3, [])
        again = await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])
        self.assertEqual(again[1]["provider"], "local_synth")
        report = await media_recovery.assess(self.project, "sfx_gen")
        self.assertEqual(report["keys"][0]["code"], "unknown_outcome_elevenlabs")

    async def test_4_no_static_assessment_no_admission_no_local_verifier_no_managed_spec(self):
        report = await media_recovery.assess(self.project, "sfx_gen")
        self.assertNotIn("local_only_no_provider_spend", {i["code"] for i in report["keys"]})
        self.assertEqual(report["verdict"]["mode"], "nothing_to_do")
        with self.assertRaises(media_admission.MediaAdmissionDenied) as caught:
            await media_admission.admit(self.project, "sfx_gen")
        self.assertEqual(caught.exception.code, "node_not_admitted")
        self.assertNotIn("sfx_gen", revisions.LOCAL_RECOVERY_NODES)
        self.assertNotIn("sfx_gen", local_node_recovery._VERIFIERS)
        self.assertFalse(local_node_recovery.spec_ok("sfx_gen"))
        spec = capability_spec_for("sfx_gen")
        self.assertFalse(spec.local_recovery)
        self.assertTrue(node_capabilities.capability_for("sfx_gen").managed and spec.recovery_only)
        self.assertEqual((spec.verifier, spec.forbidden_code), ("sfx_result", None))
        self.assertEqual(spec.recovery_actions, ("retry_same",))

    async def test_5_the_blocking_reasons_are_written_in_the_capability_limits(self):
        text = " ".join(capability_spec_for("sfx_gen").limits)
        for needle in ("чекпоинт", "sfx_progress", "resume_local", "ТОЛЬКО локальное возобновление", "denied"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
