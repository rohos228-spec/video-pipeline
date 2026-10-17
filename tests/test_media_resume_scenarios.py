import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots import kie_http, kie_kling
from app.models import Base, Project, ProjectStatus
from app.services import kie_catalog, media_resume, media_spend, outsee_retry, spend_guard
from app.services.video_error_policy import VideoLadderExhaustedError

_REAL_SLEEP = asyncio.sleep
PID = 7


class ProcessDied(BaseException):
    pass


class FakeClock:
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


PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 250000


class Env(unittest.IsolatedAsyncioTestCase):
    poll_state = "success"

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/t.db")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.creates = 0
        self.polls = 0
        self.create_script = []
        self.poll_script = []
        self.clock = FakeClock()
        self.patches = [
            patch("app.db.SessionLocal", self.sessions),
            patch.object(media_spend, "_enabled", lambda p: True),
            patch.object(kie_http, "kie_post_json", self._post),
            patch.object(httpx.AsyncClient, "send", self._send_fn()),
            patch.object(kie_http, "_headers", lambda: {}),
            patch.object(kie_http, "_POLL_INTERVAL_S", 0.0),
            patch.object(kie_http, "kie_api_base_url", lambda: "https://kie.invalid"),
            patch.object(kie_http, "kie_configured", lambda: True),
            patch.object(outsee_retry, "sleep_cancellable", self._nosleep),
        ]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        await media_spend.drain()
        for p in self.patches:
            p.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    def _send_fn(self):
        async def send(client, request, **kw):
            return await self._send(client, request, **kw)
        return send

    async def _nosleep(self, *a, **k):
        return None

    async def _post(self, url, body, timeout=120.0):
        self.creates += 1
        step = self.create_script.pop(0) if self.create_script else f"T-{self.creates}"
        if isinstance(step, BaseException):
            raise step
        return SimpleNamespace(status_code=200), {"code": 200, "data": {"taskId": step}}, "stub"

    async def _send(self, client, request, **kw):
        if "recordInfo" in str(request.url):
            self.polls += 1
            step = self.poll_script.pop(0) if self.poll_script else "success"
            if isinstance(step, BaseException):
                raise step
            if isinstance(step, tuple):
                return httpx.Response(200, json={"code": step[1], "msg": "err"}, request=request)
            if step == "waiting":
                return httpx.Response(200, json={"code": 200, "data": {"state": "waiting"}}, request=request)
            body = {"code": 200, "data": {"state": "success",
                    "resultJson": '{"resultUrls": ["https://cdn.example/out.png"]}'}}
            return httpx.Response(200, json=body, request=request)
        return httpx.Response(200, content=PNG, request=request)

    def clock_patches(self):
        return [patch.object(kie_http, "asyncio", self.clock.proxy()),
                patch.object(kie_kling, "asyncio", self.clock.proxy())]

    def out(self, name="frame_012_ab12cd34.png"):
        return Path(self.tmp.name) / name

    def image_spec_payload(self, prompt="p"):
        spec = kie_catalog.get_model("z-image")
        return spec, kie_catalog.build_payload(spec, {"prompt": prompt, "aspect_ratio": "9:16"})

    async def gen_image(self, out=None, prompt="p", timeout_s=60):
        spec, payload = self.image_spec_payload(prompt)
        return await kie_http.run_generation(spec, payload, out or self.out(), timeout_s=timeout_s, project_id=PID)

    async def task_rows(self):
        await media_spend.drain()
        async with self.sessions() as s:
            return (await s.scalars(select(spend_guard.ProviderTaskLedger).order_by(spend_guard.ProviderTaskLedger.id))).all()

    async def ladder_video(self, name="clip_001_ab12cd34.mp4", **kw):
        return await outsee_retry.generate_video_with_retries(
            None, None, prompt="a calm shot", out_path=self.out(name), project_id=PID,
            model_slug="seedance-1-5-pro", resolution="720p", aspect_ratio="9:16", timeout=60, **kw)


class ImageResume(Env):
    async def test_R1_restart_mid_generation_resumes_without_second_create(self):
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.gen_image()
        self.assertEqual(self.creates, 1)
        res = await self.gen_image(out=self.out("frame_012_99999999.png"))
        self.assertEqual(self.creates, 1)
        self.assertEqual(res.gen_id, "T-1")
        self.assertTrue(res.file_path.exists())
        statuses = [(r.event, r.status) for r in await self.task_rows() if r.task_id == "T-1"]
        self.assertEqual(statuses[-1], ("downloaded", "downloaded"))

    async def test_R2_task_id_is_in_the_ledger_before_the_first_poll(self):
        seen = {}

        async def probe(client, request, **kw):
            if "recordInfo" in str(request.url) and "rows" not in seen:
                async with self.sessions() as s:
                    seen["rows"] = [(r.task_id, r.event) for r in (await s.scalars(
                        select(spend_guard.ProviderTaskLedger))).all()]
            return await self._send(client, request, **kw)
        with patch.object(httpx.AsyncClient, "send", probe):
            await self.gen_image()
        self.assertIn(("T-1", "bound"), seen["rows"])

    async def test_R3_succeeded_but_not_downloaded_is_downloaded_without_new_create(self):
        async def broken(client, request, **kw):
            if "recordInfo" in str(request.url):
                return await self._send(client, request, **kw)
            raise httpx.ReadTimeout("slow", request=request)
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with patch.object(httpx.AsyncClient, "send", broken):
                with self.assertRaises(kie_http.KieTaskPending) as cm:
                    await self.gen_image()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(cm.exception.context["kind"], "download")
        res = await self.gen_image(out=self.out("frame_012_77777777.png"))
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_R4_poll_timeout_keeps_polling_same_task_never_creates_new(self):
        self.poll_script = ["waiting"] * 400
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with self.assertRaises(kie_http.KieTaskPending):
                await self.gen_image(timeout_s=60)
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.creates, 1)
        self.assertGreater(self.polls, 10)
        self.poll_script = []
        res = await self.gen_image(out=self.out("frame_012_55555555.png"))
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_R5_poll_5xx_and_network_errors_are_tolerated(self):
        self.poll_script = [("code", 502), ("code", 500), httpx.ReadTimeout("x"), httpx.ConnectError("y"),
                            ("code", 503), "success"]
        res = await self.gen_image()
        self.assertEqual((self.creates, res.gen_id, self.polls), (1, "T-1", 6))

    async def test_R6_create_read_timeout_does_not_resubmit_within_window(self):
        self.create_script = [httpx.ReadTimeout("slow")]
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await self.gen_image()
        self.assertEqual(self.creates, 1)
        with self.assertRaises(kie_http.KieSubmitUnknownError) as cm:
            await self.gen_image(out=self.out("frame_012_66666666.png"))
        self.assertEqual(self.creates, 1)
        self.assertTrue(cm.exception.context["no_resubmit"])
        self.assertEqual(await media_resume.release_unknown(PID, "frame_012"), 1)
        res = await self.gen_image(out=self.out("frame_012_44444444.png"))
        self.assertEqual((self.creates, res.gen_id), (2, "T-2"))

    async def test_R7_connect_error_before_send_is_not_blocking(self):
        self.create_script = [httpx.ConnectError("refused")]
        with self.assertRaises(httpx.ConnectError):
            await self.gen_image()
        res = await self.gen_image(out=self.out("frame_012_33333333.png"))
        self.assertEqual((self.creates, res.gen_id), (2, "T-2"))

    async def test_R8_happy_path_is_unchanged(self):
        res = await self.gen_image()
        self.assertEqual((res.gen_id, self.creates, self.polls), ("T-1", 1, 1))
        self.assertEqual(res.raw_url, "https://cdn.example/out.png")
        self.assertEqual(res.file_path.read_bytes(), PNG)
        self.assertFalse(self.out().with_name(self.out().name + ".part").exists())
        events = [(r.task_id, r.event, r.status) for r in await self.task_rows()]
        self.assertIn(("T-1", "downloaded", "downloaded"), events)
        self.assertEqual([e for e in events if e[1] == "intent"][0][2], "submit_unknown")
        self.assertEqual([e for e in events if e[1] == "intent_done"][0][2], "bound")

    async def test_R9_provider_fail_then_new_submit_is_allowed(self):
        async def fail_once(client, request, **kw):
            if "recordInfo" in str(request.url) and not getattr(self, "_failed", False):
                self._failed = True
                return httpx.Response(200, json={"code": 200, "data": {"state": "fail", "failMsg": "bad"}},
                                      request=request)
            return await self._send(client, request, **kw)
        with patch.object(httpx.AsyncClient, "send", fail_once):
            with self.assertRaises(kie_http.KieHttpError):
                await self.gen_image()
            res = await self.gen_image(out=self.out("frame_012_22222222.png"))
        self.assertEqual((self.creates, res.gen_id), (2, "T-2"))

    async def test_R10_other_prompt_and_other_frame_are_independent(self):
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.gen_image()
        await self.gen_image(out=self.out("frame_013_ab12cd34.png"))
        await self.gen_image(out=self.out("frame_012_11111111.png"), prompt="edited")
        self.assertEqual(self.creates, 3)

    async def test_R11_window_zero_disables_resume_but_keeps_no_resubmit_policy(self):
        from app.settings import settings
        with patch.object(settings, "media_resume_window_s", 0):
            self.create_script = [httpx.ReadTimeout("slow")]
            with self.assertRaises(kie_http.KieSubmitUnknownError):
                await self.gen_image()
            self.poll_script = [ProcessDied()]
            with self.assertRaises(ProcessDied):
                await self.gen_image()
            await self.gen_image(out=self.out("frame_012_00000001.png"))
        self.assertEqual(self.creates, 3)


class VideoLadder(Env):
    async def test_R12_ladder_poll_timeout_is_not_burned_and_not_resubmitted(self):
        self.poll_script = ["waiting"] * 400
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with self.assertRaises(kie_http.KieTaskPending):
                await self.ladder_video()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.creates, 1)

    async def test_R13_ladder_create_read_timeout_one_post_only(self):
        self.create_script = [httpx.ReadTimeout("slow")] * 5
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await self.ladder_video()
        self.assertEqual(self.creates, 1)
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await self.ladder_video("clip_001_bb12cd34.mp4")
        self.assertEqual(self.creates, 1)
        self.assertNotIsInstance(kie_http.KieSubmitUnknownError("x"), VideoLadderExhaustedError)

    async def test_R14_ladder_connect_error_still_retries_free(self):
        self.create_script = [httpx.ConnectError("refused"), httpx.ConnectTimeout("t")]
        res = await self.ladder_video()
        self.assertEqual((self.creates, res.gen_id), (3, "T-3"))

    async def test_R15_ladder_restart_resumes_same_clip_task(self):
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.ladder_video()
        res = await self.ladder_video("clip_001_cc12cd34.mp4")
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_R16_ladder_provider_fail_still_burns_and_resubmits(self):
        async def fail_first(client, request, **kw):
            if "recordInfo" in str(request.url) and self.polls == 0:
                self.polls += 1
                return httpx.Response(200, json={"code": 200, "data": {"state": "fail", "failMsg": "bad"}},
                                      request=request)
            return await self._send(client, request, **kw)
        with patch.object(httpx.AsyncClient, "send", fail_first):
            res = await self.ladder_video()
        self.assertEqual((self.creates, res.gen_id), (2, "T-2"))


class ImageLadder(Env):
    async def test_R17_image_ladder_reraises_no_resubmit_without_rewrite_or_retry(self):
        calls = {"n": 0}

        class Bot:
            async def generate_image(self, *a, **k):
                calls["n"] += 1
                raise kie_http.KieTaskPending("pending", task_id="T-9")
        with self.assertRaises(kie_http.KieTaskPending):
            await outsee_retry.generate_image_with_retries(
                Bot(), None, prompt="p", out_path=self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(calls["n"], 1)


class KlingFallback(Env):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.kp = [patch.object(kie_kling, "kie_api_configured", lambda: True),
                   patch.object(kie_kling, "kie_api_base_url", lambda: "https://kie.invalid"),
                   patch.object(kie_kling, "_auth_headers", lambda: {}),
                   patch.object(kie_kling, "_POLL_INTERVAL_S", 0.0),
                   patch.object(kie_kling, "kie_post_json", self._post),
                   patch.object(kie_kling, "_frame_to_public_url", self._url)]
        for p in self.kp:
            p.start()

    async def asyncTearDown(self):
        for p in self.kp:
            p.stop()
        await super().asyncTearDown()

    async def _url(self, f):
        return None

    async def _send(self, client, request, **kw):
        if "record-info" in str(request.url) or "recordInfo" in str(request.url):
            self.polls += 1
            step = self.poll_script.pop(0) if self.poll_script else "success"
            if isinstance(step, BaseException):
                raise step
            if step == "waiting":
                return httpx.Response(200, json={"code": 200, "data": {"state": "waiting"}}, request=request)
            return httpx.Response(200, json={"code": 200, "data": {"state": "success",
                                  "resultJson": '{"resultUrls": ["https://cdn.example/v.mp4"]}'}}, request=request)
        return httpx.Response(200, content=b"v" * 2048, request=request)

    async def test_R18_kling_create_timeout_and_restart(self):
        self.create_script = [httpx.ReadTimeout("slow")]
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await kie_kling.generate_video("p", self.out("clip_002_ab12cd34.mp4"), project_id=PID)
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await kie_kling.generate_video("p", self.out("clip_002_ab12cd35.mp4"), project_id=PID)
        self.assertEqual(self.creates, 1)

    async def test_R19_kling_poll_budget_end_is_pending_then_resumes(self):
        self.poll_script = ["waiting"] * 400
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with self.assertRaises(kie_http.KieTaskPending):
                await kie_kling.generate_video("p", self.out("clip_002_ab12cd34.mp4"), project_id=PID, timeout=30)
        finally:
            for p in patches:
                p.stop()
        self.poll_script = []
        res = await kie_kling.generate_video("p", self.out("clip_002_ab12cd36.mp4"), project_id=PID)
        self.assertEqual(self.creates, 1)
        self.assertTrue(res.file_path.exists())


class StartupReconcile(Env):
    async def make_project(self, status=ProjectStatus.generating_images):
        async with self.sessions() as s:
            pr = Project(topic="t", slug="resume-test", status=status, meta={})
            s.add(pr)
            await s.commit()
            await s.refresh(pr)
            return pr

    async def test_R20_reconcile_downloads_succeeded_task_without_any_create(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self._gen_for(pr.id)
        self.assertEqual(self.creates, 1)
        stats = await media_resume.reconcile_on_startup()
        self.assertEqual((stats["checked"], stats["downloaded"], stats["errors"]), (1, 1, 0))
        files = list((pr.data_dir / "scenes").glob("frame_012_*.png"))
        self.assertEqual(len(files), 1)
        self.assertEqual(self.creates, 1)
        again = await media_resume.reconcile_on_startup()
        self.assertEqual(again["checked"], 0)

    async def test_R21_reconcile_leaves_pending_task_for_the_step_to_resume(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self._gen_for(pr.id)
        self.poll_script = ["waiting"]
        stats = await media_resume.reconcile_on_startup()
        self.assertEqual((stats["pending"], stats["downloaded"]), (1, 0))
        res = await self._gen_for(pr.id, name="frame_012_abababab.png")
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_R22_reconcile_ignores_projects_not_in_generating_state(self):
        pr = await self.make_project(ProjectStatus.images_ready)
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self._gen_for(pr.id)
        stats = await media_resume.reconcile_on_startup()
        self.assertEqual(stats["checked"], 0)

    async def _gen_for(self, pid, name="frame_012_ab12cd34.png"):
        spec, payload = self.image_spec_payload()
        out = Path(self.tmp.name) / name
        return await kie_http.run_generation(spec, payload, out, timeout_s=60, project_id=pid)


if __name__ == "__main__":
    unittest.main()
