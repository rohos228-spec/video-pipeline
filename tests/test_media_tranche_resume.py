"""Media tranche: resume-only recovery for paid Kie media, never a second paid submit.

Only the provider boundary is faked (createTask POST, status GET, file download), exactly as in
test_media_resume_scenarios. Everything else is the real code: kie_http.run_generation, media_resume,
the retry ladders, the provider_task_ledger, media_recovery.assess / resume_poll and the
resume-only guard in media_spend. No network, no paid call.
"""
import inspect
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import httpx

from app.bots import kie_http
from app.models import Project, ProjectStatus
from app.services import media_recovery, media_resume, media_spend, outsee_retry, spend_guard
from app.services.video_error_policy import VideoErrorAction, classify_video_error, is_no_resubmit
from app.settings import settings

from tests.test_media_resume_scenarios import PID, Env, ProcessDied


class ResumeOnlyGuard(Env):
    """Inside resume_only() no seam may send a new paid request; known tasks still resume."""

    async def test_create_task_is_refused_before_any_ledger_row_or_post(self):
        with media_spend.resume_only():
            with self.assertRaises(media_spend.ResumeOnlyBlocked) as cm:
                await self.gen_image()
        self.assertEqual(self.creates, 0)
        self.assertEqual(await self.task_rows(), [])  # not even an intent row
        self.assertTrue(cm.exception.context["no_resubmit"] and cm.exception.context["resume_only"])
        self.assertFalse(cm.exception.context["request_sent"])

    async def test_scope_ends_with_the_context_manager(self):
        with media_spend.resume_only():
            self.assertTrue(media_spend.in_resume_only())
        self.assertFalse(media_spend.in_resume_only())
        await self.gen_image()
        self.assertEqual(self.creates, 1)

    async def test_every_other_paid_seam_refuses_and_closes_its_coroutine(self):
        async def never():
            raise AssertionError("a provider call must not run in a resume-only scope")

        with media_spend.resume_only():
            for call in (
                lambda c: media_spend.track_kling(c, kie_id="kling-2-6", duration=5, has_frame=False),
                lambda c: media_spend.track_eleven_post(c, chars=10, model="m"),
            ):
                coro = never()
                with self.assertRaises(media_spend.ResumeOnlyBlocked):
                    await call(coro)
                self.assertEqual(inspect.getcoroutinestate(coro), inspect.CORO_CLOSED)

            outsee = media_spend.outsee_tracked(lambda *a, **k: never(), "video")
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await outsee("prompt", Path("clip_001_ab12cd34.mp4"), model_slug="m", project_id=PID)

            @media_spend.track_eleven_sfx
            async def sfx(prompt, duration, out_path):
                raise AssertionError("must not run")

            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await sfx("p", 1.0, Path("x.mp3"))

    async def test_blocked_error_is_a_no_resubmit_hold_for_the_video_ladder(self):
        exc = media_spend.ResumeOnlyBlocked("kie", "createTask")
        self.assertTrue(is_no_resubmit(exc))
        self.assertEqual(classify_video_error(exc).action, VideoErrorAction.HOLD_UNKNOWN)

    async def test_ladder_without_a_known_task_stops_with_zero_posts(self):
        with media_spend.resume_only():
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await self.ladder_video()
        self.assertEqual((self.creates, self.polls), (0, 0))  # no burned attempt, no retry, no rewrite
        self.assertEqual(await self.task_rows(), [])

    async def test_ladder_resumes_a_known_task_inside_the_scope(self):
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.ladder_video()
        with media_spend.resume_only():
            res = await self.ladder_video("clip_001_cc12cd34.mp4")
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))
        self.assertTrue(res.file_path.exists())

    async def test_poll_timeout_inside_the_scope_stays_pending_and_never_creates(self):
        with self.assertRaises(ProcessDied):
            self.poll_script = [ProcessDied()]
            await self.ladder_video()
        self.poll_script = ["waiting"] * 400
        patches = self.clock_patches()
        for p in patches:
            p.start()
        try:
            with media_spend.resume_only():
                with self.assertRaises(kie_http.KieTaskPending):  # not ResumeOnlyBlocked: the task is known
                    await self.ladder_video("clip_001_dd12cd34.mp4")
        finally:
            for p in patches:
                p.stop()
        self.assertEqual(self.creates, 1)

    async def test_unknown_outcome_still_raises_its_own_typed_error(self):
        self.create_script = [httpx.ReadTimeout("slow")]
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await self.gen_image()
        with media_spend.resume_only():
            with self.assertRaises(kie_http.KieSubmitUnknownError):  # begin() answers before any submit
                await self.gen_image(out=self.out("frame_012_66666666.png"))
        self.assertEqual(self.creates, 1)


class RestartKeepsTheSameTaskForEveryPaidMediaKey(Env):
    """Regression: restart during generating_* never creates a second task (hero, items, music, clips)."""

    CASES = (("hero_1_v1_ab12cd34.png", "hero_1_v1_99999999.png"),
             ("predmet2_ab12cd34.png", "predmet2_99999999.png"),
             ("music_ab12cd34.mp3", "music_99999999.mp3"),
             ("clip_007_ab12cd34.mp4", "clip_007_99999999.mp4"))

    async def test_restart_resumes_instead_of_resubmitting(self):
        for first, second in self.CASES:
            with self.subTest(file=first):
                before = self.creates
                self.poll_script = [ProcessDied()]
                with self.assertRaises(ProcessDied):
                    await self.gen_image(out=self.out(first))
                res = await self.gen_image(out=self.out(second))
                self.assertEqual(self.creates, before + 1)  # one createTask across the restart
                self.assertEqual(res.gen_id, f"T-{before + 1}")

    async def test_polling_timeout_never_creates_a_new_task_for_items_and_music(self):
        for name in ("predmet3_ab12cd34.png", "music_ab12cd35.mp3"):
            with self.subTest(file=name):
                before = self.creates
                self.poll_script = ["waiting"] * 400
                patches = self.clock_patches()
                for p in patches:
                    p.start()
                try:
                    with self.assertRaises(kie_http.KieTaskPending):
                        await self.gen_image(out=self.out(name), timeout_s=60)
                finally:
                    for p in patches:
                        p.stop()
                self.poll_script = []
                await self.gen_image(out=self.out(name.replace("ab12", "ef56")))
                self.assertEqual(self.creates, before + 1)

    async def test_unrelated_names_stay_outside_the_ledger_guard(self):
        for name in ("sfx_ab12cd34.mp3", "voice_ab12cd34.mp3", "hero.png"):
            ctx = media_resume.make_ctx(PID, name, "m", "jobs", {})
            self.assertEqual(ctx.key, "", name)
        for name, key in (("hero_1_v1_ab12cd34.png", "hero_1_v1"), ("predmet2_ab12cd34.png", "predmet2"),
                          ("music_ab12cd34.mp3", "music")):
            self.assertEqual(media_resume.make_ctx(PID, name, "m", "jobs", {}).key, key)


class ProjectEnv(Env):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.data_patch = patch.object(settings, "data_dir", Path(self.tmp.name) / "data")
        self.data_patch.start()

    async def asyncTearDown(self):
        self.data_patch.stop()
        await super().asyncTearDown()

    async def make_project(self, status=ProjectStatus.generating_videos, *, project_id=PID, slug="tranche-test"):
        async with self.sessions() as s:
            pr = Project(id=project_id, topic="t", slug=slug, status=status, meta={})
            s.add(pr)
            await s.commit()
            await s.refresh(pr)
        self.pid = pr.id
        return pr

    async def gen_for(self, name, prompt="p"):
        spec, payload = self.image_spec_payload(prompt)
        return await kie_http.run_generation(spec, payload, self.out(name), timeout_s=60, project_id=self.pid)


class VideosResumeDryRun(ProjectEnv):
    """videos: restart -> the supervisor resumes the SAME task, downloads, and submits nothing."""

    async def test_resume_poll_downloads_the_finished_clip_with_zero_new_requests(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.gen_for("clip_001_ab12cd34.mp4")
        self.assertEqual(self.creates, 1)
        before = await media_recovery.assess(pr, "videos")
        self.assertEqual(before["verdict"]["mode"], "resume_poll")
        self.assertEqual(before["keys"][0]["task_id"], "T-1")
        self.assertFalse(before["submits_new_paid_request"])

        report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual(report["new_paid_requests"], 0)
        self.assertEqual(report["resumed"], [{"key": "clip_001", "task_id": "T-1", "outcome": "downloaded",
                                              "error": None}])
        self.assertEqual(self.creates, 1)  # the only createTask is the original one
        clips = list((pr.data_dir / "videos").glob("clip_001_*.png"))
        self.assertEqual(len(clips), 1)  # the fake provider returns image bytes for any download
        rows = [(r.task_id, r.status) for r in await self.task_rows() if r.task_id == "T-1"]
        self.assertEqual(rows[-1], ("T-1", "downloaded"))
        after = await media_recovery.assess(pr, "videos")
        self.assertEqual((after["verdict"]["mode"], after["verdict"]["code"]), ("nothing_to_do", "downloaded"))
        again = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual((again["resumed"], again["stats"]["checked"], self.creates), ([], 0, 1))

    async def test_pending_task_is_left_for_the_node_to_resume_and_is_never_replaced(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.ladder_video("clip_001_ab12cd34.mp4")
        self.poll_script = ["waiting"]
        report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual(report["resumed"][0]["outcome"], "pending")
        self.assertEqual(self.creates, 1)
        res = await self.ladder_video("clip_001_cc12cd34.mp4")  # the native ladder resumes the same task
        self.assertEqual((self.creates, res.gen_id), (1, "T-1"))

    async def test_poll_error_during_resume_is_reported_and_never_becomes_a_submit(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.gen_for("clip_001_ab12cd34.mp4")
        self.poll_script = [httpx.ConnectError("down")]
        report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual((report["resumed"][0]["error"], report["stats"]["errors"]), ("ConnectError", 1))
        self.assertEqual(self.creates, 1)

    async def test_submit_with_unknown_outcome_is_denied_and_resume_does_nothing(self):
        pr = await self.make_project()
        self.create_script = [httpx.ReadTimeout("slow")]
        with self.assertRaises(kie_http.KieSubmitUnknownError):
            await self.gen_for("clip_002_ab12cd34.mp4")
        report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]),
                         ("denied", "unknown_outcome_kie"))
        self.assertEqual((report["resumed"], self.polls, self.creates), ([], 0, 1))

    async def test_outsee_has_no_task_id_so_an_unknown_submit_is_typed_and_not_resumable(self):
        pr = await self.make_project()
        async with self.sessions() as s:
            await spend_guard.append_task_event(
                s, project_id=pr.id, key="clip_003", task_id="intent:abc123", provider="outsee", api="outsee",
                model="seedance", event="intent", status="submit_unknown", note="ph=aa video")
            await s.commit()
        report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual((report["verdict"]["mode"], report["verdict"]["code"]),
                         ("denied", "unknown_outcome_outsee"))
        self.assertEqual(report["keys"][0]["provider"], "outsee")
        self.assertEqual((report["resumed"], self.polls, self.creates), ([], 0, 0))

    async def test_retry_same_is_allowed_once_and_only_after_a_provable_failure(self):
        pr = await self.make_project()

        async def always_fail(client, request, **kw):
            if "recordInfo" in str(request.url):
                return httpx.Response(200, json={"code": 200, "data": {"state": "fail", "failMsg": "bad"}},
                                      request=request)
            return await self._send(client, request, **kw)

        with patch.object(httpx.AsyncClient, "send", always_fail):
            with self.assertRaises(kie_http.KieHttpError):
                await self.gen_for("clip_004_ab12cd34.mp4")
            first = await media_recovery.assess(pr, "videos")
            self.assertEqual((first["verdict"]["mode"], first["verdict"]["code"]), ("retry_same", "provably_failed"))
            with self.assertRaises(kie_http.KieHttpError):  # the one allowed retry also failed
                await self.gen_for("clip_004_bb12cd34.mp4")
            second = await media_recovery.assess(pr, "videos")
        self.assertEqual((second["verdict"]["mode"], second["verdict"]["code"]), ("denied", "retry_cap_reached"))
        self.assertEqual(self.creates, 2)
        self.assertEqual(second["retry_same_max"], 1)

    async def test_provider_failure_found_by_resume_makes_the_key_retryable_once(self):
        pr = await self.make_project()
        self.poll_script = [ProcessDied()]
        with self.assertRaises(ProcessDied):
            await self.gen_for("clip_005_ab12cd34.mp4")

        async def fail_state(client, request, **kw):
            return httpx.Response(200, json={"code": 200, "data": {"state": "fail", "failMsg": "x"}}, request=request)

        with patch.object(httpx.AsyncClient, "send", fail_state):
            report = await media_recovery.resume_poll(pr, "videos")
        self.assertEqual(report["resumed"][0]["outcome"], "failed")
        after = await media_recovery.assess(pr, "videos")
        self.assertEqual(after["verdict"]["mode"], "retry_same")
        self.assertEqual(self.creates, 1)

    async def test_only_ledger_nodes_can_be_resumed(self):
        pr = await self.make_project()
        with self.assertRaises(ValueError):
            await media_recovery.resume_poll(pr, "assemble")
        with self.assertRaises(ValueError):
            await media_recovery.assess(pr, "publish")


class StartupReconcileCoversMediaNodes(ProjectEnv):
    """Regression: a restart during generating_hero / items / music resumes through the ledger too."""

    async def test_reconcile_downloads_into_the_node_folder_without_a_create(self):
        for n, (status, name, folder) in enumerate((
                (ProjectStatus.generating_items, "predmet1_ab12cd34.png", "items"),
                (ProjectStatus.generating_hero, "hero_1_v1_ab12cd34.png", "characters"),
                (ProjectStatus.generating_music, "music_ab12cd34.mp3", "music"))):
            with self.subTest(status=status.value):
                pr = await self.make_project(status, project_id=100 + n, slug=f"reconcile-{folder}")
                self.poll_script = [ProcessDied()]
                with self.assertRaises(ProcessDied):
                    await self.gen_for(name)
                before = self.creates
                stats = await media_resume.reconcile_on_startup()
                self.assertEqual((stats["downloaded"], stats["errors"]), (1, 0))
                self.assertEqual(self.creates, before)
                self.assertEqual(len(list((pr.data_dir / folder).glob("*_*"))), 1)


class RecoveryProfilesAreComplete(unittest.TestCase):
    def test_every_media_node_has_a_profile_with_a_single_retry(self):
        nodes = {"videos", "hero", "items", "images", "music", "audio", "sfx_gen", "assemble"}
        self.assertEqual(set(media_recovery.RECOVERY_PROFILES), nodes)
        for node, profile in media_recovery.RECOVERY_PROFILES.items():
            self.assertEqual(profile["retry_same_max"], 1, node)
            for field in ("resume", "retry_only_if", "never_if"):
                self.assertGreaterEqual(len(profile[field].split()), 4, (node, field))

    def test_aggregate_prefers_the_safest_mode(self):
        modes = [{"key": "a", "mode": "retry_same", "code": "x"}, {"key": "b", "mode": "denied", "code": "y"},
                 {"key": "c", "mode": "resume_poll", "code": "z"}]
        self.assertEqual(media_recovery.aggregate(modes)["mode"], "denied")
        self.assertEqual(media_recovery.aggregate(modes[:1] + modes[2:])["mode"], "resume_poll")
        self.assertEqual(media_recovery.aggregate([])["mode"], "nothing_to_do")

    def test_classify_states_unknown_beats_open_beats_failed(self):
        now = datetime.utcnow()

        def t(task_id, status, intent=False, api="jobs"):
            return {"task_id": task_id, "status": status, "intent": intent, "api": api, "model": "m", "ph": "p",
                    "first_at": now}

        self.assertEqual(media_recovery.classify_states(
            [t("T-1", "submitted"), t("intent:1", "submit_unknown", True)])["code"], "unknown_outcome_kie")
        self.assertEqual(media_recovery.classify_states([t("T-1", "failed"), t("T-2", "pending")])["mode"],
                         "resume_poll")
        self.assertEqual(media_recovery.classify_states([t("T-1", "failed")])["mode"], "retry_same")
        self.assertEqual(media_recovery.classify_states([t("T-1", "failed"), t("T-2", "failed")])["code"],
                         "retry_cap_reached")
        self.assertEqual(media_recovery.classify_states([])["mode"], "nothing_to_do")


if __name__ == "__main__":
    unittest.main()
