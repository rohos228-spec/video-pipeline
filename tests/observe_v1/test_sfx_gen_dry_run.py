"""Dry run of sfx_gen: why the node is NOT promoted to supervised recovery (and what is pinned instead).

No network, no paid call. REAL: ``sfx_gen.generate_sfx_files`` (the node's own function), the real ``_elevenlabs_sfx``
with its ``track_eleven_sfx`` guard, ``media_spend.resume_only``, the real checkpoint helpers, ffprobe/ffmpeg on real
mp3/wav files, ``media_recovery.assess`` and ``media_admission.admit``. SCRIPTED: only the HTTP boundary
(``httpx.AsyncClient`` with a ``MockTransport``): every POST of the node is counted there.

DECISION: sfx_gen stays observed. The admissible supervised retry would be a re-run inside ``resume_only`` (zero paid
requests), but the node cannot finish such a re-run after a failed attempt:

1. the per-event progress is NOT checkpointed: ``sfx_files`` is written once, after the whole loop. A failed attempt
   leaves no checkpoint, so a re-run finds no finished event to reuse;
2. every event without a checkpoint needs a new paid POST. Inside ``resume_only`` the first one raises
   ``ResumeOnlyBlocked`` (zero POSTs reach the wire) and the whole re-run fails: it can only finish when every event is
   already checkpointed, i.e. when there is nothing left to repair;
3. an event that fell back after a provider answer (``may_have_charged=no``) is retried by the native layer with a new
   paid POST; inside ``resume_only`` that retry is refused and the re-run fails; after a timeout (``uncertain``) the
   event is never retried (the earlier POST may have been charged), which stays with a human;
4. there is no static assessment of the form "all planned events are done" (``media_recovery.assess`` reads only the
   checkpoint records, it has no plan), no typing of the attempt's own failure (the node uses the ``sfx_result``
   verifier, not ``media_result``) and no independent verifier for a re-run (``local_node_recovery._VERIFIERS``).
"""
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
    async def flush(self):
        return None


class Wire:
    """The scripted provider boundary: one entry per POST that actually reached the wire."""

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
        raise step  # a BaseException that no `except Exception` catches (a killed process)

    def client(self, *a, **k):
        k.pop("transport", None)
        return _REAL_CLIENT(*a, transport=httpx.MockTransport(self.handler), **k)


class SfxGenDryRun(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mp3 = base.media_bytes("audio", self.root / "scratch")  # a real 2 s mp3
        self.project = SimpleNamespace(id=base.PID, data_dir=self.root / "project", meta={})
        self.events = [SfxEvent(i + 1, float(i), 2.0, "hit", f"p{i}", 0.5, False) for i in range(3)]
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
            return await sfx_gen.generate_sfx_files(_Sess(), self.project, self.events, **kw)

    # -- the status of a provider answer is never silent again ------------------------------------------------

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

    # -- why the node is not promoted ---------------------------------------------------------------------------

    async def failed_attempt(self):
        wire = Wire(self.mp3, ["ok", "ok", asyncio.CancelledError()])  # two events paid and done, then the process dies
        with self.assertRaises(asyncio.CancelledError):
            await self.run_node(wire)
        return wire

    async def test_1_a_failed_attempt_leaves_no_event_checkpoint_although_two_events_were_paid(self):
        wire = await self.failed_attempt()
        self.assertEqual(len(wire.posts), 3)
        self.assertEqual(len(list((self.project.data_dir / "sfx").glob("sfx_*.mp3"))), 2)  # files exist on disk ...
        self.assertIsNone(ai_job_checkpoint(self.project, "sfx_files"))                    # ... but nothing records them

    async def test_2_the_resume_only_rerun_after_a_failed_attempt_makes_zero_posts_and_cannot_finish(self):
        await self.failed_attempt()
        wire = Wire(self.mp3, [])
        hits: list = []
        with media_spend.resume_only(hits):
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])           # zero provider POSTs: that part is safe
        self.assertEqual(len(hits), 1)             # but the first event already needs a new paid one
        self.assertIsNone(ai_job_checkpoint(self.project, "sfx_files"))  # and no result is produced

    async def test_2b_the_resume_only_rerun_finishes_only_when_every_event_is_already_checkpointed(self):
        first = Wire(self.mp3, ["ok", "ok", "ok"])
        done = await self.run_node(first)
        self.assertEqual(len(first.posts), 3)
        wire = Wire(self.mp3, [])
        with media_spend.resume_only():
            again = await self.run_node(wire, retry_degraded=True)
        self.assertEqual(wire.posts, [])
        self.assertEqual([f["path"] for f in again], [f["path"] for f in done])  # nothing left to repair

    async def test_3_a_degraded_event_retry_needs_a_new_paid_post_and_is_refused_in_resume_only(self):
        await self.run_node(Wire(self.mp3, ["ok", 500, "ok"]))   # event 1 fell back after a provider answer
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
        # the assessment reads checkpoint records only: it has no plan, so it cannot say "all planned events are done"
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
        self.assertFalse(node_capabilities.capability_for("sfx_gen").managed)
        self.assertEqual((spec.verifier, spec.forbidden_code), ("sfx_result", "paid_no_idempotency"))

    async def test_5_the_blocking_reasons_are_written_in_the_capability_limits(self):
        text = " ".join(capability_spec_for("sfx_gen").limits)
        for needle in ("чекпоинт", "ResumeOnlyBlocked", "sfx_result", "Не допущен"):
            self.assertIn(needle, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
