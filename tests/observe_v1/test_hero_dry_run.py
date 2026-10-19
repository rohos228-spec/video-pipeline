import unittest
from contextlib import ExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project, ProjectStatus
from app.orchestrator.steps import generate_hero
from app.services import media_admission, media_spend, outsee_retry, spend_guard
from app.settings import settings

PID = 7
KEY = "hero_1_v1"


class GptWritingADifferentPromptEveryTime:
    def __init__(self):
        self.asks = 0

    async def ask_fresh(self, text, *, timeout=600, project_id=None):
        self.asks += 1
        return (f"Character turnaround sheet, version {self.asks}: a tall man about 45 in a grey coat, "
                "front, side and back views, neutral expression, plain light background, soft studio light.")


class HeroDryRun(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data = root / "videos" / "hero-fixture"
        self.data.mkdir(parents=True)
        self.ledger_engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'ledger.db'}")
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'master.db'}")
        for engine in (self.ledger_engine, self.engine):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
        self.ledger = async_sessionmaker(self.ledger_engine, expire_on_commit=False)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            session.add(Project(id=PID, slug="hero-fixture", topic="Простые вещи", status=ProjectStatus.generating_hero,
                                auto_mode=False, hero_mode="hero", hero_count=1,
                                hero_descriptions=["Высокий мужчина лет сорока пяти в сером пальто, сдержанный."],
                                hero_variations=[1], meta={}))
            await session.commit()
        self.gpt = GptWritingADifferentPromptEveryTime()
        self.posts, self.prompts_at_post, self.prompt_file_existed_at_post = 0, [], []
        self.bot = SimpleNamespace(send_message=AsyncMock())

        async def lost_answer_after_post(prompt, out_path, **kwargs):
            self.posts += 1
            self.prompts_at_post.append(prompt)
            self.prompt_file_existed_at_post.append(
                (self.data / "characters" / ".hero_prompt_1_v1.txt").is_file())
            from app.bots.outsee_http import OutseeHttpError
            raise OutseeHttpError("answer lost", context={"submit_unknown": True})

        @asynccontextmanager
        async def no_browser():
            yield object()

        self.stack = ExitStack()
        for patcher in (patch.object(settings, "data_dir", Path(self.tmp.name)),
                        patch("app.db.SessionLocal", self.ledger),
                        patch.object(media_spend, "_enabled", lambda provider: True),
                        patch("app.bots.outsee_http.outsee_api_key", lambda: "synthetic"),
                        patch("app.bots.outsee_http.generate_image", lost_answer_after_post),
                        patch.object(generate_hero, "get_gpt_client", lambda: self.gpt),
                        patch.object(generate_hero, "browser_session", no_browser),
                        patch.object(generate_hero, "send_hitl_photo", AsyncMock()),
                        patch.object(outsee_retry, "sleep_cancellable", AsyncMock())):
            self.stack.enter_context(patcher)

    async def asyncTearDown(self):
        await media_spend.drain()
        await self.engine.dispose()
        await self.ledger_engine.dispose()
        self.stack.close()
        self.tmp.cleanup()

    async def attempt(self):
        async with self.sessions() as session:
            project = await session.get(Project, PID)
            project.status = ProjectStatus.generating_hero
            await session.commit()
            await generate_hero.run(session, project, self.bot)
            await session.commit()
            return project.status

    async def ledger_rows(self):
        await media_spend.drain()
        async with self.ledger() as session:
            await spend_guard.ensure_task_schema(session)
            return (await session.scalars(select(spend_guard.ProviderTaskLedger)
                                          .order_by(spend_guard.ProviderTaskLedger.id))).all()

    def saved(self):
        path = self.data / "characters" / ".hero_prompt_1_v1.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    async def test_the_prompt_is_saved_before_the_paid_call_and_reused_after_a_restart(self):
        await self.attempt()
        self.assertEqual((self.gpt.asks, self.posts), (1, 1))
        self.assertEqual(self.prompt_file_existed_at_post, [True], "saved BEFORE the paid call")
        first_prompt = self.saved()
        self.assertIn("version 1", first_prompt)
        self.assertEqual(self.prompts_at_post[0].strip(), first_prompt.strip())
        rows = await self.ledger_rows()
        self.assertTrue(rows and all(r.api == "outsee" for r in rows), "an Outsee submit: no Kie task id exists")
        self.assertTrue(any(r.status == "submit_unknown" for r in rows), "the lost answer left an unresolved intent")
        await self.attempt()
        self.assertEqual(self.gpt.asks, 1, "GPT was not asked again: the saved prompt is reused")
        self.assertEqual(self.saved(), first_prompt)
        self.assertEqual(self.posts, 1, "no second submit: the guard stopped it before the provider")

    async def test_a_normal_repeat_without_an_unfinished_generation_builds_a_new_prompt_as_before(self):
        async with self.sessions() as session:
            pass
        await self.attempt()
        async with self.ledger() as session:
            await spend_guard.ensure_task_schema(session)
        await media_spend.drain()
        from app.services import outsee_guard
        await outsee_guard.release_unknown(PID, KEY)
        await self.attempt()
        self.assertEqual(self.gpt.asks, 2, "no unfinished generation: regeneration writes a fresh prompt")
        self.assertIn("version 2", self.saved())
        self.assertEqual(self.posts, 2)

    async def test_without_the_fix_the_second_prompt_would_differ_which_is_what_the_cache_prevents(self):
        await self.attempt()
        first = self.saved()
        (self.data / "characters" / ".hero_prompt_1_v1.txt").unlink()
        await self.attempt()
        self.assertEqual(self.gpt.asks, 2)
        self.assertNotEqual(self.saved(), first, "a model never writes the same prompt twice")
        self.assertEqual(self.posts, 1, "even then the Outsee guard (keyed by hero, not by prompt) held the submit")

    async def test_a_kie_model_slug_never_reaches_the_kie_seam_in_the_native_route(self):
        from pathlib import Path as P
        kie = AsyncMock(side_effect=AssertionError("kie seam reached"))
        seen = []

        class OutseeSeam:
            async def generate_image(self, prompt, out_path, **kwargs):
                seen.append(kwargs.get("model_slug"))
                from app.bots.outsee import GenerationResult
                return GenerationResult(file_path=out_path, raw_url=None, gen_id=None)

        with patch("app.bots.kie_http.generate_kie_image", kie):
            await outsee_retry.generate_image_with_retries(
                OutseeSeam(), None, prompt="x" * 150, out_path=P("hero_1_v1_abcdef12.png"),
                model_slug="seedream-5-pro", project_id=PID, gpt_rewrite=False)
        self.assertEqual(seen, ["seedream-5-pro"])
        kie.assert_not_called()

    async def test_hero_admits_only_a_local_resume_of_a_recorded_result(self):
        from app.orchestrator.node_spec import capability_spec_for, coverage_level
        from app.services import media_local_resume
        from app.services.node_capabilities import capability_for, require_managed_capability
        self.assertFalse(media_admission.admitted("hero"))
        self.assertTrue(media_local_resume.admitted("hero"))
        self.assertTrue(capability_for("hero").managed)
        with self.assertRaises(ValueError):
            require_managed_capability("hero")
        spec = capability_spec_for("hero")
        self.assertEqual(coverage_level(spec), "managed")
        self.assertIsNone(spec.forbidden_code)
        self.assertEqual(spec.recovery_actions, ("retry_same",))


if __name__ == "__main__":
    unittest.main(verbosity=2)
