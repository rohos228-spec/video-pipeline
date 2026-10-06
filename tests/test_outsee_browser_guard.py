"""Outsee browser (UI) path: no blind second Generate click after a submit with an unknown outcome.

Fake page / session / bot objects only; the ledger is a temporary sqlite file. No network, no paid calls.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bots import outsee as outsee_mod
from app.bots import outsee_http
from app.bots.outsee import (
    OutseeBot,
    OutseeContentRejectedError,
    OutseeDownloadError,
    OutseeImageError,
    _mark_submit,
)
from app.bots.outsee_http import OutseeApiError, OutseeSubmitUnknownError
from app.models import Base
from app.services import media_spend, outsee_guard, spend_guard
from app.services.video_error_policy import is_no_resubmit

PID = 11


class FakePage:
    url = "https://outsee.io/create"


class FakeSession:
    async def open_page(self, url, reuse=True):
        return FakePage()


class Env(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.tmp.name}/t.db")
        async with self.engine.begin() as c:
            await c.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.clicks = 0
        self.patches = [
            patch("app.db.SessionLocal", self.sessions),
            patch.object(media_spend, "TEST_FORCE", True),
            patch.object(media_spend, "_managed", lambda: False),
            patch.object(media_spend, "_enabled", lambda p: False),  # no API key: the browser guard must not need it
        ]
        for p in self.patches:
            p.start()
        self.bot = OutseeBot(FakeSession())  # type: ignore[arg-type]

        async def no_http(**kw):
            return None

        self.bot._try_http_image = no_http  # type: ignore[method-assign]
        self.bot._try_http_video = no_http  # type: ignore[method-assign]

    async def asyncTearDown(self):
        await media_spend.drain()
        for p in self.patches:
            p.stop()
        await self.engine.dispose()
        self.tmp.cleanup()

    def out(self, name="frame_021_ab12cd34.png"):
        return Path(self.tmp.name) / name

    def page_flow(self, behave):
        """Replace the on-page part; ``behave`` runs after the (fake) click like the real flow would."""
        async def on_page(page, **kw):
            return await behave()
        self.bot._generate_image_on_page = on_page  # type: ignore[method-assign]

    async def gen(self, **kw):
        kw.setdefault("model_slug", "nano-banana-2")
        kw.setdefault("project_id", PID)
        return await self.bot.generate_image("a prompt", self.out(), **kw)

    async def rows(self):
        async with self.sessions() as s:
            await spend_guard.ensure_task_schema(s)
            return (await s.scalars(select(spend_guard.ProviderTaskLedger)
                                    .order_by(spend_guard.ProviderTaskLedger.id))).all()

    async def clicking(self, then):
        """Behavior: click Generate (marks the probe like the real code), then ``then``."""
        self.clicks += 1
        _mark_submit(maybe_sent=True)
        return await then()


async def ok_result():
    _mark_submit(done=True)
    return outsee_mod.GenerationResult(file_path=Path("x.png"), raw_url="https://x/y.png", gen_id="g")


class BrowserGuard(Env):
    async def test_timeout_after_the_click_blocks_a_second_click(self):
        async def never_finishes():
            raise OutseeImageError("outsee image: не дождались картинки", context={"gen_id": "g"})

        self.page_flow(lambda: self.clicking(never_finishes))
        with self.assertRaises(OutseeSubmitUnknownError) as ctx:
            await self.gen()
        self.assertTrue(is_no_resubmit(ctx.exception))
        self.assertEqual(ctx.exception.context["exc_type"], "OutseeImageError")
        self.assertEqual(self.clicks, 1)
        with self.assertRaises(OutseeSubmitUnknownError) as again:
            await self.gen()
        self.assertEqual(self.clicks, 1)  # the page was never driven again: no second paid click
        self.assertIn("без известного исхода", str(again.exception))
        self.assertTrue(is_no_resubmit(again.exception))
        intents = [r for r in await self.rows() if r.task_id.startswith(spend_guard.INTENT_PREFIX)]
        self.assertEqual([(r.api, r.status) for r in intents], [("outsee", "submit_unknown")])

    async def test_human_release_allows_a_new_click(self):
        async def never_finishes():
            raise OutseeImageError("timeout")

        self.page_flow(lambda: self.clicking(never_finishes))
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.gen()
        self.assertEqual(await outsee_guard.release_unknown(PID, "frame_021"), 1)
        self.page_flow(lambda: self.clicking(ok_result))
        result = await self.gen()
        self.assertEqual(result.gen_id, "g")
        self.assertEqual(self.clicks, 2)

    async def test_cancel_or_crash_after_the_click_stays_unresolved(self):
        async def cancelled():
            raise asyncio.CancelledError()

        self.page_flow(lambda: self.clicking(cancelled))
        with self.assertRaises(asyncio.CancelledError):
            await self.gen()
        self.page_flow(lambda: self.clicking(ok_result))
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.gen()
        self.assertEqual(self.clicks, 1)

    async def test_failure_before_the_click_is_not_an_unknown_submit(self):
        async def no_button():
            self.clicks += 1
            raise OutseeImageError("outsee image: не найдена кнопка Generate")

        self.page_flow(no_button)
        with self.assertRaises(OutseeImageError) as first:
            await self.gen()
        self.assertNotIsInstance(first.exception, OutseeSubmitUnknownError)
        self.page_flow(lambda: self.clicking(ok_result))
        await self.gen()
        self.assertEqual(self.clicks, 2)

    async def test_moderation_banner_after_the_click_is_a_known_rejection(self):
        async def moderated():
            outsee_mod._raise_outsee_failure(text="Контент отклонён: запрещённые слова", gen_id="g",
                                             elapsed=3.0, in_result=True)

        self.page_flow(lambda: self.clicking(moderated))
        with self.assertRaises(OutseeContentRejectedError):
            await self.gen()  # the original error reaches the rewrite ladder untouched
        self.page_flow(lambda: self.clicking(ok_result))
        await self.gen()  # the rewritten prompt may be submitted
        self.assertEqual(self.clicks, 2)

    async def test_download_failure_after_the_result_is_known_does_not_block(self):
        async def bad_download():
            _mark_submit(done=True)
            raise OutseeDownloadError("download", context={"img_url": "https://x/y.png"})

        self.page_flow(lambda: self.clicking(bad_download))
        with self.assertRaises(OutseeDownloadError):
            await self.gen()
        self.page_flow(lambda: self.clicking(ok_result))
        await self.gen()
        self.assertEqual(self.clicks, 2)

    async def test_success_resolves_the_intent_as_bound(self):
        self.page_flow(lambda: self.clicking(ok_result))
        await self.gen()
        await self.gen()
        done = [r.status for r in await self.rows() if r.event == "intent_done"]
        self.assertEqual(done, ["bound", "bound"])

    async def test_other_frames_are_not_blocked(self):
        async def never_finishes():
            raise OutseeImageError("timeout")

        self.page_flow(lambda: self.clicking(never_finishes))
        with self.assertRaises(OutseeSubmitUnknownError):
            await self.gen()
        self.page_flow(lambda: self.clicking(ok_result))
        await self.bot.generate_image("p", self.out("frame_022_ab12cd34.png"), model_slug="nano-banana-2",
                                      project_id=PID)
        self.assertEqual(self.clicks, 2)

    async def test_resume_only_scope_refuses_a_browser_submit_before_any_click(self):
        self.page_flow(lambda: self.clicking(ok_result))
        with media_spend.resume_only():
            with self.assertRaises(media_spend.ResumeOnlyBlocked):
                await self.gen()
        self.assertEqual(self.clicks, 0)

    async def test_call_without_a_project_or_frame_key_passes_through(self):
        async def never_finishes():
            raise OutseeImageError("timeout")

        self.page_flow(lambda: self.clicking(never_finishes))
        with self.assertRaises(OutseeImageError) as no_project:
            await self.bot.generate_image("p", self.out(), model_slug="nano-banana-2")
        self.assertNotIsInstance(no_project.exception, OutseeSubmitUnknownError)
        with self.assertRaises(OutseeImageError) as no_key:
            await self.bot.generate_image("p", self.out("character.png"), project_id=PID)
        self.assertNotIsInstance(no_key.exception, OutseeSubmitUnknownError)
        self.assertEqual(self.clicks, 2)
        self.assertEqual(await self.rows(), [])

    async def test_video_and_regenerate_use_the_same_key_space(self):
        ctx = outsee_guard.make_browser_ctx((self.out("clip_003_ab12cd34.mp4"),), {"project_id": PID}, "image",
                                            prompt_first=False)
        self.assertEqual((ctx.key, ctx.browser, ctx.usable), ("clip_003", True, True))
        ctx = outsee_guard.make_browser_ctx(("p", self.out("frame_021_ab12cd34.png")), {"project_id": PID}, "video")
        self.assertEqual(ctx.key, "frame_021")

        class FakeVideoBot:
            calls = 0

            @outsee_mod._browser_guarded("video")
            async def generate_video(self, prompt, out_path, **kw):
                FakeVideoBot.calls += 1
                _mark_submit(maybe_sent=True)
                raise OutseeImageError("video timeout")

            @outsee_mod._browser_guarded("image", prompt_first=False)
            async def regenerate_image(self, out_path, **kw):
                FakeVideoBot.calls += 1
                _mark_submit(maybe_sent=True)
                raise OutseeImageError("retry timeout")

        bot = FakeVideoBot()
        clip = self.out("clip_003_ab12cd34.mp4")
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.generate_video("p", clip, project_id=PID, model_slug="veo")
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.generate_video("p", clip, project_id=PID, model_slug="veo")
        self.assertEqual(FakeVideoBot.calls, 1)
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.regenerate_image(self.out("frame_030_ab12cd34.png"), project_id=PID)
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.regenerate_image(self.out("frame_030_ab12cd34.png"), project_id=PID)
        self.assertEqual(FakeVideoBot.calls, 2)


class ClickMarksTheProbe(Env):
    """The real click helpers mark ``maybe_sent`` before they touch the page."""

    async def test_trigger_generate_video_marks_before_the_first_click(self):
        seen: list[dict] = []

        class Btn:
            first = None

            def __init__(self):
                self.first = self

            async def count(self):
                return 1

            async def is_visible(self):
                return True

            async def bounding_box(self):
                return {"x": 0, "y": 0, "width": 100, "height": 40}

        class Mouse:
            async def click(self, x, y):
                seen.append(dict(outsee_http.submit_probe.get() or {}))

        class Page:
            mouse = Mouse()

            def get_by_role(self, role, name=None, exact=False):
                return Btn()

        async def started(page):
            return True

        self.bot._generation_started = started  # type: ignore[method-assign]
        probe = {"maybe_sent": False, "task_id": None, "done": False}
        token = outsee_http.submit_probe.set(probe)
        try:
            with patch("app.services.step_cancel.sleep_cancellable", _no_sleep):
                await self.bot._trigger_generate_video(Page(), input_sel=None)
        finally:
            outsee_http.submit_probe.reset(token)
        self.assertTrue(seen and seen[0]["maybe_sent"] is True)
        self.assertTrue(probe["maybe_sent"])

    async def test_page_declared_failure_marks_rejected_and_result_marks_done(self):
        probe = {"maybe_sent": True, "task_id": None, "done": False}
        token = outsee_http.submit_probe.set(probe)
        try:
            with self.assertRaises(OutseeContentRejectedError):
                outsee_mod._raise_outsee_failure(text="запрещённые слова", gen_id="g", elapsed=1.0, in_result=False)
            self.assertTrue(probe["rejected"])
            _mark_submit(done=True)
            self.assertTrue(probe["done"])
        finally:
            outsee_http.submit_probe.reset(token)
        self.assertEqual(outsee_guard.classify_failure(OutseeImageError("x"), {**probe, "done": False}), "rejected")

    async def test_click_sites_are_marked_in_the_flow_sources(self):
        import inspect

        image = inspect.getsource(OutseeBot._generate_image_on_page)
        self.assertLess(image.index("_mark_submit(maybe_sent=True)"), image.index("page.locator(gen_sel).first.click()"))
        self.assertIn("_mark_submit(done=True)", image)
        regen = inspect.getsource(OutseeBot.regenerate_image)
        self.assertLess(regen.index("_mark_submit(maybe_sent=True)"), regen.index("page.locator(retry_sel).first.click()"))
        click = inspect.getsource(OutseeBot._click_generate_button)
        self.assertLess(click.index("_mark_submit(maybe_sent=True)"), click.index("_physical_mouse_click("))
        video = inspect.getsource(OutseeBot._generate_video_on_page)
        self.assertIn("_mark_submit(done=True)", video)
        for name in ("generate_image", "generate_video", "regenerate_image"):
            self.assertTrue(hasattr(getattr(OutseeBot, name), "__wrapped__"), name)


async def _no_sleep(*a, **k):
    return None


class ApiFallbackIsHeldWhenAPaidTaskMayExist(Env):
    """OutseeBot used to fall back from the Developer API to the browser on any API error: a second paid submit."""

    def setUp(self):
        super().setUp()
        self.ran_browser = 0

    async def _bot_with_api(self, api_behave):
        bot = OutseeBot(FakeSession())  # type: ignore[arg-type]

        async def on_page(page, **kw):
            self.ran_browser += 1
            return await ok_result()

        bot._generate_image_on_page = on_page  # type: ignore[method-assign]

        async def fake_api(**kw):
            return await api_behave()

        stack = [patch.object(outsee_http, "outsee_http_enabled", lambda: True),
                 patch.object(outsee_http, "generate_image", fake_api)]
        for p in stack:
            p.start()
            self.addCleanup(p.stop)
        return bot

    async def test_api_error_with_a_known_task_id_does_not_fall_back_to_the_browser(self):
        async def api():
            outsee_http._probe_mark(task_id="G-9")
            raise OutseeApiError("Outsee: таймаут ожидания id=G-9", context={"last": {}})

        bot = await self._bot_with_api(api)
        with self.assertRaises(OutseeSubmitUnknownError) as ctx:
            await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(ctx.exception.context["task_id"], "G-9")
        self.assertEqual(self.ran_browser, 0)
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(self.ran_browser, 0)

    async def test_unknown_api_submit_never_reaches_the_browser(self):
        async def api():
            outsee_http._probe_mark(maybe_sent=True)
            raise OutseeSubmitUnknownError("no answer")

        bot = await self._bot_with_api(api)
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(self.ran_browser, 0)

    async def test_api_refusal_with_nothing_sent_still_falls_back(self):
        async def api():
            raise OutseeApiError("Outsee API images/generate: bad", context={"status": 400})

        bot = await self._bot_with_api(api)
        result = await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(result.gen_id, "g")
        self.assertEqual(self.ran_browser, 1)

    async def test_provider_declared_api_failure_still_falls_back_and_probe_is_clean(self):
        seen = {}

        async def api():
            outsee_http._probe_mark(task_id="G-1")
            raise OutseeApiError("failed", context={"id": "G-1", "status": {"status": "failed"}})

        bot = await self._bot_with_api(api)

        async def on_page(page, **kw):
            self.ran_browser += 1
            seen.update(outsee_http.submit_probe.get() or {})
            return await ok_result()

        bot._generate_image_on_page = on_page  # type: ignore[method-assign]
        await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(self.ran_browser, 1)
        self.assertIsNone(seen["task_id"])  # the failed API task is not mistaken for the browser submit

    async def test_finished_api_generation_with_a_failed_download_is_not_regenerated_in_the_browser(self):
        async def api():
            outsee_http._probe_mark(task_id="G-1", done=True)
            raise OutseeDownloadError("download", context={"img_url": "https://x/y.png"})

        bot = await self._bot_with_api(api)
        with self.assertRaises(OutseeDownloadError):
            await bot.generate_image("p", self.out(), model_slug="nano-banana-2", project_id=PID)
        self.assertEqual(self.ran_browser, 0)

    async def test_without_a_guard_context_the_hold_still_applies(self):
        async def api():
            outsee_http._probe_mark(task_id="G-2")
            raise OutseeApiError("poll ended", context={})

        bot = await self._bot_with_api(api)
        with self.assertRaises(OutseeSubmitUnknownError):
            await bot.generate_image("p", self.out(), model_slug="nano-banana-2")  # no project id: no ledger
        self.assertEqual(self.ran_browser, 0)
        self.assertEqual(await self.rows(), [])


if __name__ == "__main__":
    unittest.main()
