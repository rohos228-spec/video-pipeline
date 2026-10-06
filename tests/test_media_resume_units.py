"""Resume, never resubmit: policy mapping, prompt hash, ledger state rules. Stubs only."""
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots.kie_http import KieHttpError, KieSubmitUnknownError, KieTaskPending, KieUncertainError
from app.models import Base
from app.settings import settings as media_resume_settings
from app.services import media_resume, spend_guard
from app.services.video_error_policy import VideoErrorAction, classify_video_error, is_no_resubmit


class PolicyMapping(unittest.TestCase):
    def test_U1_new_errors_map_to_no_resubmit_action(self):
        for exc in (KieSubmitUnknownError("x"), KieTaskPending("y", task_id="t")):
            c = classify_video_error(exc)
            self.assertEqual((c.code, c.action), ("media_no_resubmit", VideoErrorAction.HOLD_UNKNOWN))
            self.assertTrue(is_no_resubmit(exc))
            self.assertIsInstance(exc, KieUncertainError)  # still an uncertain Kie error

    def test_U2_old_actions_unchanged(self):
        self.assertEqual(classify_video_error(KieHttpError("x", context={"provider_code": 502})).action,
                         VideoErrorAction.RETRY_SAME)
        self.assertEqual(classify_video_error(KieHttpError("task fail", context={"provider_code": 501})).action,
                         VideoErrorAction.RETRY_BURN)
        self.assertEqual(classify_video_error(KieHttpError("x", context={"provider_code": 402})).action,
                         VideoErrorAction.STOP_AUTH)
        self.assertFalse(is_no_resubmit(KieHttpError("x", context={"provider_code": 500})))

    def test_U3_submit_error_classes(self):
        c = media_resume.classify_submit_error
        self.assertEqual(c(httpx.ConnectError("x")), "not_sent")
        self.assertEqual(c(httpx.ConnectTimeout("x")), "not_sent")
        self.assertEqual(c(httpx.ReadTimeout("x")), "unknown")
        self.assertEqual(c(httpx.WriteError("x")), "unknown")
        self.assertEqual(c(httpx.RemoteProtocolError("x")), "unknown")
        self.assertEqual(c(KieHttpError("no id", context={"no_task_id": True, "provider_code": 500})), "unknown")
        self.assertEqual(c(KieHttpError("gw", context={"provider_code": 504})), "unknown")
        self.assertEqual(c(KieHttpError("pay", context={"provider_code": 402})), "rejected")
        self.assertEqual(c(KieHttpError("srv", context={"provider_code": 503})), "rejected")

    def test_U4_prompt_hash_ignores_reference_urls_but_not_prompt(self):
        a = {"model": "m", "input": {"prompt": "p", "image_urls": ["https://a/1.png"], "aspect_ratio": "9:16"}}
        b = {"model": "m", "input": {"prompt": "p", "image_urls": ["https://b/2.png"], "aspect_ratio": "9:16"}}
        c = {"model": "m", "input": {"prompt": "q", "image_urls": ["https://a/1.png"], "aspect_ratio": "9:16"}}
        self.assertEqual(media_resume.prompt_hash("m", a), media_resume.prompt_hash("m", b))
        self.assertNotEqual(media_resume.prompt_hash("m", a), media_resume.prompt_hash("m", c))

    def test_U5_only_known_paid_media_keys_are_resumable(self):
        ok = media_resume.make_ctx(7, "frame_001_ab12cd34.png", "m", "jobs", {})
        clip = media_resume.make_ctx(7, "clip_001_s2_ab12cd34.mp4", "m", "jobs", {})
        music = media_resume.make_ctx(7, "music_ab12cd34.mp3", "m", "suno", {})
        hero = media_resume.make_ctx(7, "hero_1_v1_ab12cd34.png", "m", "jobs", {})
        item = media_resume.make_ctx(7, "predmet3_ab12cd34.png", "m", "jobs", {})
        other = media_resume.make_ctx(7, "sfx_ab12cd34.mp3", "m", "jobs", {})
        self.assertEqual((ok.key, clip.key, music.key, hero.key, item.key, other.key),
                         ("frame_001", "clip_001_s2", "music", "hero_1_v1", "predmet3", ""))
        self.assertFalse(media_resume.make_ctx(None, "frame_001_ab12cd34.png", "m", "jobs", {}).usable)


class LedgerRules(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/u.db")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.patches = [patch("app.db.SessionLocal", self.sessions),
                        patch.object(media_resume.media_spend, "_enabled", lambda p: True)]
        for p in self.patches:
            p.start()
        self.ctx = media_resume.Ctx(7, "frame_001", "z-image", "jobs", "abc123def456abcd")

    async def asyncTearDown(self):
        for p in self.patches:
            p.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    async def row(self, task_id, status, event="x", note="", age_s=0, ph=True):
        async with self.sessions() as s:
            await spend_guard.ensure_task_schema(s)
            s.add(spend_guard.ProviderTaskLedger(
                project_id=7, key="frame_001", task_id=task_id, provider="kie", api="jobs", model="z-image",
                event=event, status=status, note=(f"ph={self.ctx.ph} " if ph else "") + note,
                created_at=datetime.utcnow() - timedelta(seconds=age_s)))
            await s.commit()

    async def test_U6_open_statuses_resume_terminal_ones_do_not(self):
        for st in ("submitted", "pending", "succeeded"):
            with self.subTest(st=st):
                tid = f"T-{st}"
                await self.row(tid, st)
                self.assertEqual(await media_resume.begin(self.ctx), tid)
                await self.row(tid, "failed")
                self.assertIsNone(await media_resume.begin(self.ctx))
        await self.row("T-d", "succeeded")
        await self.row("T-d", "downloaded")
        self.assertIsNone(await media_resume.begin(self.ctx))

    async def test_U7_task_older_than_window_is_not_resumed(self):
        await self.row("T-old", "pending", age_s=3601)
        self.assertIsNone(await media_resume.begin(self.ctx))
        with patch.object(media_resume_settings, "media_resume_window_s", 7200):
            self.assertEqual(await media_resume.begin(self.ctx), "T-old")

    async def test_U8_unresolved_intent_blocks_and_resolution_unblocks(self):
        await self.row("intent:aa", "submit_unknown", event="intent")
        with self.assertRaises(KieSubmitUnknownError) as cm:
            await media_resume.begin(self.ctx)
        self.assertIn("frame_001", str(cm.exception))
        await self.row("intent:aa", "rejected", event="intent_done")
        self.assertIsNone(await media_resume.begin(self.ctx))

    async def test_U9_intent_for_other_prompt_does_not_block(self):
        await self.row("intent:bb", "submit_unknown", event="intent", ph=False, note="ph=ffffffffffffffff")
        self.assertIsNone(await media_resume.begin(self.ctx))

    async def test_U10_disabled_context_never_touches_the_ledger(self):
        with patch.object(media_resume.media_spend, "_enabled", lambda p: False):
            self.assertIsNone(await media_resume.begin(self.ctx))
            calls = []

            async def create():
                calls.append(1)
                return "T-1"
            self.assertEqual(await media_resume.submit(self.ctx, create), "T-1")
        async with self.sessions() as s:
            self.assertEqual(await spend_guard.find_task_states(
                s, project_id=7, key="frame_001", model=None, since=datetime.utcnow() - timedelta(days=1)), [])

    async def test_U11_ledger_stays_append_only(self):
        await self.row("T-1", "submitted")
        from sqlalchemy import text
        async with self.sessions() as s:
            with self.assertRaises(Exception):
                await s.execute(text("UPDATE provider_task_ledger SET status='failed'"))


if __name__ == "__main__":
    unittest.main()
