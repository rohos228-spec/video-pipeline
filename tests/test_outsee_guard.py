import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots import outsee_http
from app.bots.outsee import OutseeDownloadError
from app.bots.outsee_http import OutseeApiError, OutseeSubmitUnknownError
from app.models import Base
from app.services import media_resume, media_spend, outsee_guard, outsee_retry, spend_guard
from app.services.step_cancel import StepCancelledError
from app.services.video_error_policy import is_no_resubmit

PID = 7


class Env(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/t.db")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.calls = 0
        self.patches = [
            patch("app.db.SessionLocal", self.sessions),
            patch.object(media_spend, "_enabled", lambda p: True),
            patch.object(media_spend, "_managed", lambda: False),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        await media_spend.drain()
        for p in self.patches:
            p.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    def out(self, name="frame_012_ab12cd34.png"):
        return Path(self.tmp.name) / name

    def tracked(self, behave):
        async def gen(prompt, out_path, **kw):
            self.calls += 1
            return await behave()
        return media_spend.outsee_tracked(gen, "image")

    async def call(self, behave, **kw):
        kw.setdefault("model_slug", "nano-banana-2")
        kw.setdefault("project_id", PID)
        return await self.tracked(behave)("a prompt", self.out(), **kw)

    async def rows(self):
        async with self.sessions() as s:
            await spend_guard.ensure_task_schema(s)
            return (await s.scalars(select(spend_guard.ProviderTaskLedger).order_by(spend_guard.ProviderTaskLedger.id))).all()


async def accepted_then(exc):
    outsee_http._probe_mark(task_id="G-1")
    raise exc


async def sent_no_answer():
    outsee_http._probe_mark(maybe_sent=True)
    raise OutseeSubmitUnknownError("no answer")


async def ok():
    outsee_http._probe_mark(task_id="G-1", done=True)
    return "result"


class Guard(Env):
    async def test_unknown_submit_blocks_next_call_and_names_the_reason(self):
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(sent_no_answer)
        self.assertEqual(self.calls, 1)
        with self.assertRaises(OutseeSubmitUnknownError) as ctx:
            await self.call(ok)
        self.assertEqual(self.calls, 1)
        self.assertTrue(is_no_resubmit(ctx.exception))
        self.assertIn("без известного исхода", str(ctx.exception))
        self.assertIn("outsee_guard.release_unknown", str(ctx.exception))
        intents = [r for r in await self.rows() if r.task_id.startswith(spend_guard.INTENT_PREFIX)]
        self.assertEqual([r.status for r in intents], ["submit_unknown"])
        self.assertEqual({r.api for r in intents}, {"outsee"})

    async def test_human_release_allows_a_new_submit(self):
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(sent_no_answer)
        self.assertEqual(await outsee_guard.release_unknown(PID, "frame_012"), 1)
        self.assertEqual(await outsee_guard.release_unknown(PID, "frame_012"), 0)
        self.assertEqual(await self.call(ok), "result")
        self.assertEqual(self.calls, 2)

    async def test_accepted_task_without_a_result_is_unknown_not_retried(self):
        timeout = OutseeApiError("Outsee: таймаут ожидания id=G-1", context={"last": {}})
        with self.assertRaises(OutseeSubmitUnknownError) as ctx:
            await self.call(lambda: accepted_then(timeout))
        self.assertEqual(ctx.exception.context["task_id"], "G-1")
        self.assertIs(ctx.exception.__cause__, timeout)
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(ok)
        self.assertEqual(self.calls, 1)

    async def test_provider_declared_failure_is_a_known_outcome(self):
        failed = OutseeApiError("Outsee generation failed", context={"id": "G-1", "status": {"status": "failed"}})
        with self.assertRaises(OutseeApiError) as ctx:
            await self.call(lambda: accepted_then(failed))
        self.assertIs(ctx.exception, failed)
        self.assertEqual(await self.call(ok), "result")
        self.assertEqual(self.calls, 2)

    async def test_rejected_or_never_sent_submit_does_not_block(self):
        async def refused():
            raise OutseeApiError("Outsee API images/generate: bad", context={"status": 400, "code": "invalid_request"})
        with self.assertRaises(OutseeApiError):
            await self.call(refused)
        self.assertEqual(await self.call(ok), "result")
        self.assertEqual(self.calls, 2)

    async def test_download_failure_after_a_finished_generation_does_not_block(self):
        async def downloaded_badly():
            outsee_http._probe_mark(task_id="G-1", done=True)
            raise OutseeDownloadError("download", context={"img_url": "https://x/y.png"})
        with self.assertRaises(OutseeDownloadError):
            await self.call(downloaded_badly)
        self.assertEqual(await self.call(ok), "result")

    async def test_success_resolves_the_intent(self):
        self.assertEqual(await self.call(ok), "result")
        self.assertEqual(await self.call(ok), "result")
        self.assertEqual(self.calls, 2)

    async def test_user_cancel_propagates_unchanged_and_keeps_the_hold(self):
        with self.assertRaises(StepCancelledError):
            await self.call(lambda: accepted_then(StepCancelledError("stop")))
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(ok)
        self.assertEqual(self.calls, 1)

    async def test_cancel_while_the_post_is_in_flight_keeps_the_hold(self):
        async def cancelled_in_flight():
            outsee_http._probe_mark(in_flight=True)
            raise StepCancelledError("kill switch")
        with self.assertRaises(StepCancelledError):
            await self.call(cancelled_in_flight)
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(ok)
        self.assertEqual(self.calls, 1)

    async def test_cancel_before_anything_was_sent_does_not_block(self):
        async def cancelled_early():
            raise StepCancelledError("stop")
        with self.assertRaises(StepCancelledError):
            await self.call(cancelled_early)
        self.assertEqual(await self.call(ok), "result")

    async def test_other_frames_and_projects_are_not_blocked(self):
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(sent_no_answer)
        async def gen(prompt, out_path, **kw):
            return "other"
        wrapped = media_spend.outsee_tracked(gen, "image")
        self.assertEqual(await wrapped("p", self.out("frame_013_ab12cd34.png"), model_slug="m", project_id=PID), "other")
        self.assertEqual(await wrapped("p", self.out(), model_slug="m", project_id=PID + 1), "other")

    async def test_kie_resume_ignores_outsee_intents(self):
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.call(sent_no_answer)
        ctx = media_resume.Ctx(PID, "frame_012", "nano-banana-2", "jobs", "0123456789abcdef")
        self.assertIsNone(await media_resume.begin(ctx))

    async def test_calls_without_project_or_key_are_untouched(self):
        async def bad():
            raise RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            await self.call(bad, project_id=None)
        gen_calls = []

        async def gen(*a, **k):
            gen_calls.append(1)
            return "x"
        wrapped = media_spend.outsee_tracked(gen, "image")
        self.assertEqual(await wrapped("p", self.out("music_001.mp3"), model_slug="m", project_id=PID), "x")
        self.assertEqual(await wrapped(model_slug="m"), "x")
        self.assertEqual(len(gen_calls), 2)
        self.assertEqual([r for r in await self.rows() if r.api == "outsee"], [])

    async def test_disabled_window_turns_the_guard_off(self):
        with patch.object(media_resume, "window_s", lambda: 0):
            with self.assertRaises(RuntimeError):
                await self.call(_raise_runtime)
            self.assertEqual([r for r in await self.rows() if r.api == "outsee"], [])


async def _raise_runtime():
    raise RuntimeError("boom")


class Classify(unittest.TestCase):
    def test_table(self):
        c = outsee_guard.classify_failure
        err = OutseeApiError("x", context={})
        self.assertEqual(c(err, {"task_id": None, "maybe_sent": False, "done": False}), "not_sent")
        self.assertEqual(c(err, {"task_id": None, "maybe_sent": True, "done": False}), "unknown")
        self.assertEqual(c(err, {"task_id": "1", "maybe_sent": False, "done": False}), "unknown")
        self.assertEqual(c(err, {"task_id": "1", "maybe_sent": False, "done": True}), "done")
        self.assertEqual(c(OutseeApiError("x", context={"status": {"s": "failed"}}),
                           {"task_id": "1", "done": False}), "rejected")
        self.assertEqual(c(OutseeSubmitUnknownError("x"), {"task_id": None, "done": False}), "unknown")


class RealSubmitPath(Env):

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.sent = []
        self.post_script = []
        self.more = [
            patch.object(outsee_http, "outsee_api_key", lambda: "test-key"),
            patch.object(outsee_http, "_POLL_INTERVAL_S", 0.0),
            patch.object(httpx.AsyncClient, "send", self._send_fn()),
            patch.object(outsee_retry, "sleep_cancellable", self._nosleep),
            patch.object(outsee_http.asyncio, "sleep", _fast_sleep),
        ]
        for p in self.more:
            p.start()

    async def asyncTearDown(self):
        for p in self.more:
            p.stop()
        await super().asyncTearDown()

    async def _nosleep(self, *a, **k):
        return None

    def _send_fn(self):
        async def send(client, request, **kw):
            self.sent.append((request.method, str(request.url)))
            if request.method == "POST":
                step = self.post_script.pop(0) if self.post_script else {"id": 5, "status": "queued"}
                if isinstance(step, BaseException):
                    raise step
                return httpx.Response(200, json=step, request=request)
            return httpx.Response(200, json={"status": "processing"}, request=request)
        return send

    def posts(self):
        return [s for s in self.sent if s[0] == "POST"]

    async def ladder(self, **kw):
        return await outsee_retry.generate_image_with_retries(
            None, None, prompt="a prompt", out_path=self.out(), model_slug="nano-banana-2",
            project_id=PID, timeout=0.001, **kw)

    async def test_read_timeout_on_submit_is_one_post_and_blocks_the_ladder(self):
        self.post_script = [httpx.ReadTimeout("slow")]
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.ladder()
        self.assertEqual(len(self.posts()), 1)
        with self.assertRaises(OutseeSubmitUnknownError) as ctx:
            await self.ladder()
        self.assertEqual(len(self.posts()), 1)
        self.assertIn("Повторная отправка запрещена", str(ctx.exception))

    async def test_poll_ending_without_a_result_blocks_the_ladder_after_one_post(self):
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.ladder()
        self.assertEqual(len(self.posts()), 1)
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.ladder()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual(await outsee_guard.release_unknown(PID, "frame_012"), 1)

    async def test_response_without_id_is_unknown_not_a_second_post(self):
        self.post_script = [{"status": "queued"}]
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.ladder()
        self.assertEqual(len(self.posts()), 1)

    async def test_connect_error_is_not_sent_and_does_not_block(self):
        self.post_script = [httpx.ConnectError("refused")] * 3
        with patch.object(outsee_http, "_post_generate_via_curl", _no_curl):
            with self.assertRaises(OutseeApiError) as ctx:
                await self.ladder(max_attempts_per_prompt=1)
        self.assertNotIsInstance(ctx.exception, OutseeSubmitUnknownError)
        self.assertEqual(await outsee_guard._unresolved(outsee_guard.Ctx(PID, "frame_012", "", "image", "")), [])


_REAL_SLEEP = asyncio.sleep


async def _fast_sleep(delay, *a, **k):
    await _REAL_SLEEP(0)


async def _no_curl(path, body):
    return None


if __name__ == "__main__":
    unittest.main()
