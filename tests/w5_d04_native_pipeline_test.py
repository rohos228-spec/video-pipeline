import inspect
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from sqlalchemy import select

from app.bots import kie_http, kie_kling
from app.models import Project, ProjectStatus
from app.services import media_resume, media_spend, spend_guard
from app.services.video_error_policy import is_no_resubmit
from tests import test_media_resume_scenarios as base

PID = base.PID


class NoUrlsEnv(base.Env):

    no_urls = False

    async def _send(self, client, request, **kw):
        if self.no_urls and "recordInfo" in str(request.url):
            self.polls += 1
            return httpx.Response(200, json={"code": 200, "data": {"state": "success", "resultJson": "{}"}},
                                  request=request)
        return await super()._send(client, request, **kw)


class RestartWithLiveTask(base.Env):
    async def test_D04_1_restart_during_generation_keeps_one_paid_task_for_every_frame(self):
        self.poll_script = [base.ProcessDied(), base.ProcessDied()]
        for number, prompt in ((1, "первый"), (2, "второй")):
            with self.assertRaises(base.ProcessDied):
                await self.gen_image(out=self.out(f"frame_00{number}_aaaaaaaa.png"), prompt=prompt)
        self.assertEqual(self.creates, 2)
        first = await self.gen_image(out=self.out("frame_001_bbbbbbbb.png"), prompt="первый")
        second = await self.gen_image(out=self.out("frame_002_bbbbbbbb.png"), prompt="второй")
        self.assertEqual(self.creates, 2, "ни одной новой платной задачи")
        self.assertEqual((first.gen_id, second.gen_id), ("T-1", "T-2"))

    async def test_D04_1_a_finished_frame_is_not_resubmitted_by_the_ledger_but_by_the_step_check(self):
        await self.gen_image(out=self.out("frame_003_aaaaaaaa.png"), prompt="третий")
        self.assertEqual(self.creates, 1)
        await self.gen_image(out=self.out("frame_003_bbbbbbbb.png"), prompt="третий")
        self.assertEqual(self.creates, 2)

    async def test_D04_1_startup_reconcile_then_step_pays_once(self):
        async with self.sessions() as s:
            pr = Project(topic="t", slug="d04", status=ProjectStatus.generating_images, meta={})
            s.add(pr)
            await s.commit()
            await s.refresh(pr)
        spec, payload = self.image_spec_payload()
        self.poll_script = [base.ProcessDied()]
        with self.assertRaises(base.ProcessDied):
            await kie_http.run_generation(spec, payload, self.out("frame_012_aaaaaaaa.png"), timeout_s=60,
                                          project_id=pr.id)
        self.poll_script = ["waiting"]
        stats = await media_resume.reconcile_on_startup()
        self.assertEqual((stats["pending"], stats["errors"], self.creates), (1, 0, 1))
        res = await kie_http.run_generation(spec, payload, self.out("frame_012_bbbbbbbb.png"), timeout_s=60,
                                            project_id=pr.id)
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))


class BurnWithLiveTask(NoUrlsEnv):
    async def test_D04_2_burn_on_a_generic_error_does_not_create_a_second_task(self):
        self.no_urls = True
        with self.assertRaises(kie_kling.KieKlingError) as caught:
            await self.ladder_video()
        self.assertEqual(caught.exception.context.get("error_kind"), "no_key")
        self.assertEqual(self.creates, 1, "основная модель: одна задача на все сожжённые попытки")
        self.assertGreaterEqual(self.polls, 4, "попытки лестницы опрашивали ту же задачу")
        rows = [r for r in await self.task_rows() if not r.task_id.startswith(spend_guard.INTENT_PREFIX)]
        self.assertEqual({r.task_id for r in rows}, {"T-1"})

    async def test_D04_2_after_the_burn_a_good_answer_is_taken_from_the_same_task(self):
        self.no_urls = True
        with self.assertRaises(kie_kling.KieKlingError):
            await self.ladder_video()
        self.no_urls = False
        res = await self.ladder_video("clip_001_dddddddd.mp4")
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_D04_2_poll_timeout_burn_is_held_not_resubmitted(self):
        self.poll_script = ["waiting"] * 400
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with self.assertRaises(kie_http.KieTaskPending) as caught:
                await self.ladder_video()
        finally:
            for p in patches:
                p.stop()
        self.assertTrue(is_no_resubmit(caught.exception))
        self.assertEqual(self.creates, 1)


class TaskIdBeforePolling(base.Env):
    async def test_D04_3_task_id_is_durable_before_the_first_status_request(self):
        seen = {}

        async def probe(client, request, **kw):
            if "recordInfo" in str(request.url) and "rows" not in seen:
                async with self.sessions() as s:
                    seen["rows"] = [(r.task_id, r.event, r.status) for r in (await s.scalars(
                        select(spend_guard.ProviderTaskLedger))).all()]
            return await self._send(client, request, **kw)

        with patch.object(httpx.AsyncClient, "send", probe):
            await self.gen_image()
        self.assertIn(("T-1", "bound", "submitted"), seen["rows"])

    async def test_D04_3_process_dies_between_the_create_answer_and_the_task_id_row(self):
        real = spend_guard.append_task_event

        async def dies_on_bound(session, **kw):
            if kw.get("event") == "bound":
                raise base.ProcessDied()
            return await real(session, **kw)

        with patch.object(spend_guard, "append_task_event", dies_on_bound):
            with self.assertRaises(base.ProcessDied):
                await self.gen_image()
        self.assertEqual(self.creates, 1)
        with self.assertRaises(kie_http.KieSubmitUnknownError) as caught:
            await self.gen_image(out=self.out("frame_012_99999999.png"))
        self.assertTrue(is_no_resubmit(caught.exception))
        self.assertEqual(self.creates, 1)
        self.assertEqual(await media_resume.release_unknown(PID, "frame_012"), 1)
        res = await self.gen_image(out=self.out("frame_012_88888888.png"))
        self.assertEqual((self.creates, res.gen_id), (2, "T-2"))


class DownloadRightAway(base.Env):
    async def test_D04_4_download_follows_the_success_in_the_same_call(self):
        res = await self.gen_image()
        self.assertEqual((self.creates, self.polls), (1, 1))
        self.assertTrue(res.file_path.exists() and res.file_path.stat().st_size > 1000)
        states = [(r.event, r.status) for r in await self.task_rows() if r.task_id == "T-1"]
        self.assertEqual(states[-1], ("downloaded", "downloaded"))

    async def test_D04_4_failed_download_is_held_and_the_next_call_downloads_without_a_new_task(self):
        async def broken(client, request, **kw):
            if "recordInfo" in str(request.url):
                return await self._send(client, request, **kw)
            raise httpx.ReadTimeout("slow", request=request)

        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with patch.object(httpx.AsyncClient, "send", broken):
                with self.assertRaises(kie_http.KieTaskPending) as caught:
                    await self.ladder_video()
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(caught.exception.context["kind"], "download")
        self.assertTrue(is_no_resubmit(caught.exception))
        self.assertEqual(self.creates, 1)
        res = await self.ladder_video("clip_001_eeeeeeee.mp4")
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))


class ManagedTransportBoundary(unittest.TestCase):
    def test_D04_5_managed_references_stay_refused_fail_closed(self):
        from app.services import media_transport
        self.assertIn("headers", inspect.signature(media_transport.json_request).parameters)
        source = inspect.getsource(kie_http.generate_kie_image)
        self.assertIn("managed reference upload has no authorized adapter", source)


class OutseeStartupReport(base.Env):
    async def test_D04_1_startup_names_outsee_frames_that_must_not_be_resent(self):
        from app.services import outsee_idempotency as idem
        async with self.sessions() as s:
            pr = Project(topic="t", slug="d04o", status=ProjectStatus.generating_images, meta={})
            s.add(pr)
            await s.commit()
            await s.refresh(pr)
        with patch.object(media_spend, "TEST_FORCE", True), patch.object(idem, "_ensured", set()):
            ctx = idem.make_ctx(pr.id, Path(self.tmp.name) / "frame_001_aaaaaaaa.png", "nano-banana-2", "p")
            await idem._write(ctx, "a" * 32, "intent")
            done = Path(self.tmp.name) / "frame_002_aaaaaaaa.png"
            done.write_bytes(base.PNG)
            ctx2 = idem.make_ctx(pr.id, done, "nano-banana-2", "p2")
            await idem._write(ctx2, "b" * 32, "intent")
            await idem._write(ctx2, "b" * 32, "result", file_path=str(done), file_size=done.stat().st_size,
                              fsha=idem.file_sha256(done))
            stats = await media_resume.reconcile_on_startup()
        self.assertEqual((stats["outsee_uncertain"], stats["outsee_result_on_record"]), (1, 1))
        self.assertEqual(self.creates, 0)


if __name__ == "__main__":
    unittest.main()
