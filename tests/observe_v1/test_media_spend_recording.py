"""Native paid media/audio calls are recorded best-effort; no network, stubs only."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

import test_actions_v1_contract as actions_tests
from app.bots import kie_http
from app.services import elevenlabs_api, kill_switch, media_spend, spend_guard
from app.services.observation import _cost_project
from app.settings import settings


class MediaSpendBase(actions_tests.DurableActions):
    async def asyncSetUp(self):
        kill_switch.reset_cache()
        await super().asyncSetUp()
        self.patches = [patch("app.db.SessionLocal", self.sessions), patch.object(media_spend, "_enabled", lambda p: True),
                        patch.object(settings, "spend_auto_kill_on_402", True)]
        for p in self.patches:
            p.start()
        self.token = _cost_project.set(7)

    async def asyncTearDown(self):
        _cost_project.reset(self.token)
        for p in self.patches:
            p.stop()
        kill_switch.reset_cache()
        await super().asyncTearDown()

    async def rows(self):
        await media_spend.drain()
        async with self.sessions() as s:
            return (await s.scalars(select(spend_guard.SpendLedger).order_by(spend_guard.SpendLedger.id))).all()

    async def task_rows(self):
        await media_spend.drain()
        async with self.sessions() as s:
            return (await s.scalars(select(spend_guard.ProviderTaskLedger).order_by(spend_guard.ProviderTaskLedger.id))).all()

    async def run_gen(self, spec_id, create_payload, polls=2):
        """Run kie_http.run_generation against stubbed createTask / recordInfo / download."""
        from app.services import kie_catalog

        async def post(url, body, timeout=120.0):
            return SimpleNamespace(status_code=200), create_payload, "stub"

        state = {"gets": 0}

        async def send(client, request, **kw):
            if "recordInfo" in str(request.url):
                state["gets"] += 1
                done = state["gets"] >= polls
                body = {"code": 200, "data": {"state": "success" if done else "waiting",
                        "resultJson": '{"resultUrls": ["https://cdn.example/out.png"]}'}}
                return httpx.Response(200, json=body, request=request)
            return httpx.Response(200, content=b"x" * 64, request=request)

        spec = kie_catalog.get_model(spec_id)
        payload = kie_catalog.build_payload(spec, {"prompt": "p", "aspect_ratio": "9:16"})
        with patch.object(kie_http, "kie_post_json", post), patch.object(httpx.AsyncClient, "send", send), \
                patch.object(kie_http, "_headers", lambda: {}), patch.object(kie_http, "_POLL_INTERVAL_S", 0.0), \
                patch.object(kie_http, "kie_api_base_url", lambda: "https://kie.invalid"):
            return await kie_http.run_generation(spec, payload, Path(self.tmp.name) / "frame_012_ab12cd34.png",
                                                 timeout_s=60)


class KieRecording(MediaSpendBase):
    async def test_priced_submit_poll_and_task_ledger(self):
        res = await self.run_gen("z-image", {"code": 200, "data": {"taskId": "T-1"}})
        self.assertEqual(res.gen_id, "T-1")  # native result unchanged
        rows = await self.rows()
        create = [r for r in rows if r.note.startswith("create")]
        self.assertEqual(len(create), 1)
        self.assertAlmostEqual(create[0].usd, 0.004)
        self.assertAlmostEqual(create[0].credits, 0.8)
        self.assertEqual((create[0].provider, create[0].project_id, create[0].source), ("kie", 7, "media_native"))
        poll = [r for r in rows if r.note.startswith("poll")]
        self.assertEqual(len(poll), 1)
        self.assertEqual(poll[0].usd, 0.0)
        self.assertIn("polls=2", poll[0].note)
        tasks = await self.task_rows()
        self.assertEqual([(t.event, t.status) for t in tasks], [("submitted", "submitted"), ("poll", "succeeded")])
        self.assertEqual((tasks[0].task_id, tasks[0].key, tasks[0].project_id), ("T-1", "frame_012", 7))
        self.assertAlmostEqual(tasks[0].est_usd, 0.004)

    async def test_unpriced_model_records_request_row_cost_unknown(self):
        from app.services import kie_catalog
        spec = {**kie_catalog.get_model("z-image"), "pricing": {}}
        real = kie_catalog.get_model
        with patch.object(kie_catalog, "get_model", lambda i: spec if i == "z-image" else real(i)):
            await self.run_gen("z-image", {"code": 200, "data": {"taskId": "T-2"}}, polls=1)
        create = [r for r in await self.rows() if r.note.startswith("create")][0]
        self.assertEqual(create.usd, 0.0)
        self.assertIn("cost_unknown=true", create.note)

    async def test_timeout_after_submit_is_billed_as_uncertain(self):
        from app.services import kie_catalog
        spec = kie_catalog.get_model("z-image")
        payload = kie_catalog.build_payload(spec, {"prompt": "p"})

        async def boom(url, body, timeout=120.0):
            raise httpx.ReadTimeout("slow")
        with patch.object(kie_http, "kie_post_json", boom), patch.object(kie_http, "kie_api_base_url", lambda: "https://x"):
            with media_spend.kie_scope(spec, payload, "frame_001_aaaaaaaa.png"):
                with self.assertRaises(httpx.ReadTimeout):  # raised unchanged
                    await kie_http.create_task("jobs", None, payload)
        create = [r for r in await self.rows() if r.note.startswith("create")][0]
        self.assertIn("uncertain", create.note)
        self.assertAlmostEqual(create.usd, 0.004)
        self.assertEqual(await self.task_rows(), [])  # no task id, no task row

    async def test_402_engages_kill_switch_with_audit_and_native_error_unchanged(self):
        with self.assertRaises(kie_http.KieHttpError) as cm:
            await self.run_gen("z-image", {"code": 402, "msg": "Insufficient credits"})
        self.assertEqual(cm.exception.context["provider_code"], 402)
        rows = await self.rows()
        self.assertTrue((await kill_switch.status(self.sessions))["active"])
        self.assertTrue(any(r.kind == "event" and r.source == "media_402" and r.provider == "kie" for r in rows))
        rej = [r for r in rows if r.note.startswith("create")][0]
        self.assertEqual(rej.usd, 0.0)  # definite rejection is not billed
        self.assertIn("rejected http=402", rej.note)

    async def test_402_with_setting_off_audits_but_does_not_kill(self):
        with patch.object(settings, "spend_auto_kill_on_402", False):
            with self.assertRaises(kie_http.KieHttpError):
                await self.run_gen("z-image", {"code": 402, "msg": "no"})
            rows = await self.rows()
        self.assertFalse((await kill_switch.status(self.sessions))["active"])
        self.assertTrue(any(r.source == "media_402" for r in rows))


class NeverRaises(MediaSpendBase):
    async def test_broken_database_does_not_affect_the_call(self):
        def broken():
            raise RuntimeError("db down")
        with patch("app.db.SessionLocal", broken):
            res = await self.run_gen("z-image", {"code": 200, "data": {"taskId": "T-9"}}, 1)
            await media_spend.drain()
        self.assertEqual(res.gen_id, "T-9")

    async def test_recording_helpers_swallow_everything(self):
        with patch.object(media_spend, "_project_id", side_effect=RuntimeError("x")):
            media_spend.record("kie", usd=1.0, note="n")
            media_spend._after_create_ok("jobs", "t")
        media_spend.count_poll()
        with patch.object(spend_guard, "record_actual", side_effect=RuntimeError("x")):
            media_spend.record("kie", usd=1.0, note="n")
            await media_spend.drain()

    async def test_recording_off_for_synthetic_key_and_under_pytest(self):
        self.patches[1].stop()
        try:
            self.assertFalse(media_spend._enabled("elevenlabs"))  # pytest guard
            with patch.object(media_spend, "TEST_FORCE", True), patch.object(settings, "elevenlabs_api_key", "synthetic-1"):
                self.assertFalse(media_spend._enabled("elevenlabs"))
            with patch.object(media_spend, "TEST_FORCE", True), patch.object(settings, "elevenlabs_api_key", "real-looking"):
                self.assertTrue(media_spend._enabled("elevenlabs"))
        finally:
            self.patches[1].start()

    async def test_record_does_not_block_on_the_database(self):
        gate = asyncio.Event()

        async def slow(*a, **k):
            await gate.wait()
        with patch.object(media_spend, "_write", slow):
            media_spend.record("kie", usd=1.0, note="n")  # returns immediately (scheduled only)
            self.assertTrue(media_spend._tasks)
            gate.set()
            await media_spend.drain()


class ElevenLabsRecording(MediaSpendBase):
    def stub(self, handler):
        async def send(client, request, **kw):
            return handler(request)
        return patch.object(httpx.AsyncClient, "send", send)

    async def synth(self, text="Привет мир"):
        out = Path(self.tmp.name) / "voice.mp3"
        with patch.object(settings, "elevenlabs_api_key", "k"), patch.object(settings, "elevenlabs_proxy_url", ""):
            return await elevenlabs_api.synthesize_speech(text, out)

    async def test_success_recorded_with_char_estimate_and_header_settle(self):
        h = lambda req: httpx.Response(200, content=b"a" * 2000, headers={"character-cost": "5"}, request=req)
        with self.stub(h):
            out = await self.synth()
        self.assertTrue(out.exists())
        rows = await self.rows()
        chars = len("Привет мир")
        est = [r for r in rows if r.kind == "actual"][0]
        self.assertAlmostEqual(est.usd, chars * media_spend.ELEVENLABS_USD_PER_CHAR)
        self.assertEqual((est.provider, est.project_id), ("elevenlabs", 7))
        settle = [r for r in rows if r.kind == "settle"]
        self.assertAlmostEqual(settle[0].usd, (5 - chars) * media_spend.ELEVENLABS_USD_PER_CHAR)

    async def test_402_engages_kill_switch_and_error_text_unchanged(self):
        h = lambda req: httpx.Response(402, json={"detail": "quota"}, request=req)
        with self.stub(h):
            with self.assertRaises(elevenlabs_api.ElevenLabsApiError) as cm:
                await self.synth()
        self.assertIn("402 Payment Required", str(cm.exception))
        await self.rows()
        self.assertTrue((await kill_switch.status(self.sessions))["active"])

    async def test_timeout_is_wrapped_as_uncertain_and_each_post_recorded(self):
        calls = []

        def h(req):
            calls.append(1)
            raise httpx.ReadTimeout("slow", request=req)
        real_sleep = asyncio.sleep

        async def fast(s, *a):
            await real_sleep(0)
        with self.stub(h), patch("asyncio.sleep", fast):
            with self.assertRaises(elevenlabs_api.ElevenLabsApiError) as cm:
                await self.synth()
        self.assertIsInstance(cm.exception, elevenlabs_api.ElevenLabsUncertainError)  # still an ElevenLabsApiError
        self.assertEqual(len(calls), 3)  # native retry count unchanged
        rows = [r for r in await self.rows() if r.kind == "actual"]
        self.assertEqual(len(rows), 3)
        self.assertTrue(all("uncertain" in r.note for r in rows))

    async def test_connect_error_not_uncertain(self):
        def h(req):
            raise httpx.ConnectError("refused", request=req)
        real_sleep = asyncio.sleep

        async def fast(s, *a):
            await real_sleep(0)
        with self.stub(h), patch("asyncio.sleep", fast):
            with self.assertRaises(elevenlabs_api.ElevenLabsApiError) as cm:
                await self.synth()
        self.assertNotIsInstance(cm.exception, elevenlabs_api.ElevenLabsUncertainError)

    async def test_sfx_decorator_records_and_402_kills(self):
        from app.services import sfx_gen

        async def ok(prompt, duration, out_path):
            return out_path
        await media_spend.track_eleven_sfx(ok)("p", 2.0, Path("x.mp3"))
        rows = await self.rows()
        self.assertAlmostEqual(rows[0].usd, 2.0 * media_spend.ELEVENLABS_SFX_USD_PER_SEC)

        async def bad(prompt, duration, out_path):
            raise RuntimeError("11labs sfx 402: quota")
        with self.assertRaises(RuntimeError):
            await media_spend.track_eleven_sfx(bad)("p", 1.0, Path("x.mp3"))
        await media_spend.drain()
        self.assertTrue((await kill_switch.status(self.sessions))["active"])
        self.assertTrue(hasattr(sfx_gen._elevenlabs_sfx, "__wrapped__"))


class OutseeRecording(MediaSpendBase):
    async def test_ok_and_402(self):
        async def gen(*a, **k):
            return "result"
        self.assertEqual(await media_spend.outsee_tracked(gen, "image")(model_slug="gpt-image-2"), "result")
        rows = await self.rows()
        self.assertIn("cost_unknown=true", rows[0].note)
        self.assertEqual(rows[0].provider, "outsee")

        class Api(RuntimeError):
            context = {"status": 402, "code": "payment"}

        async def bad(*a, **k):
            raise Api("Outsee API images/generate: no credits")
        with self.assertRaises(Api):
            await media_spend.outsee_tracked(bad, "image")(model_slug="x")
        await media_spend.drain()
        self.assertTrue((await kill_switch.status(self.sessions))["active"])


class TaskLedger(MediaSpendBase):
    async def test_append_only_and_latest_status_listing(self):
        async with self.sessions() as s:
            for ev, st in (("submitted", "submitted"), ("poll", "pending"), ("poll", "succeeded")):
                await spend_guard.append_task_event(s, project_id=7, key="frame_001", task_id="T", provider="kie",
                                                    api="jobs", model="m", event=ev, status=st, est_usd=0.1)
            await s.commit()
        for stmt in ("UPDATE provider_task_ledger SET status = 'x'", "DELETE FROM provider_task_ledger"):
            async with self.sessions() as s:
                with self.assertRaises(DBAPIError):
                    await s.execute(text(stmt))
                await s.rollback()
        async with self.sessions() as s:
            listed = await spend_guard.list_provider_tasks(s, 7)
            self.assertEqual(await spend_guard.list_provider_tasks(s, 8), [])
        self.assertEqual(len(listed), 1)
        self.assertEqual((listed[0]["status"], listed[0]["events"], listed[0]["key"]), ("succeeded", 3, "frame_001"))

    async def test_route_lists_tasks(self):
        from app.web.routers import observations
        async with self.sessions() as s:
            await spend_guard.append_task_event(s, project_id=7, key="clip_002", task_id="T7", provider="kie",
                                                api="jobs", model="m", event="submitted", status="submitted")
            await s.commit()
        with patch.object(observations, "get_observer", lambda: SimpleNamespace(master=self.sessions)):
            out = await observations.provider_tasks(7)
        self.assertEqual(out["tasks"][0]["task_id"], "T7")
