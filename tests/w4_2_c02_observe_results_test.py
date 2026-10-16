from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import app.services.decision_v1
from app.models import Base, Frame, Project, ProjectStatus
from app.orchestrator import node_spec
from app.services import node_capabilities as nc
from app.services.diagnosis import derive_incidents

PID = 4
LONG_PROMPT = ("Широкий план: пустая читальная комната, косой свет из окна, пыль в воздухе, деревянные полки до потолка. " * 3)


class Journal:

    def __init__(self, mode="observe", exc=None):
        self.facts = []
        self.gaps = []
        self.ctx = SimpleNamespace(
            scope=SimpleNamespace(mode=mode, project_id=PID, execution_id="x"),
            observer=SimpleNamespace(mark_gap=lambda execution, error: self.gaps.append(error), master=lambda: None),
            attempt_exception=exc, attempt_id="a1", node_key="n1")

    async def record(self, kind, payload=None, **kw):
        self.facts.append((kind, dict(payload or {})))

    def __enter__(self):
        self.patches = [patch("app.services.observation._attempt"), patch("app.services.observation.record_fact",
                                                                          self.record)]
        attempt = self.patches[0].start()
        attempt.get.return_value = self.ctx
        self.patches[1].start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()

    def kinds(self):
        return [k for k, _ in self.facts]

    def events(self, node_key="n1"):
        return [dict(id=i, event_id=f"e{i}", execution_id="x", project_id=PID, node_key=node_key, attempt_id="a1",
                     request_id=None, kind=k, payload=p, db_scope="project", created_at="2026-10-07 12:00:00")
                for i, (k, p) in enumerate(self.facts, 1)]

    def incidents(self):
        scope = SimpleNamespace(execution_id="x", project_id=PID, node_keys=["n1"], observer_state="active", gaps=[])
        return derive_incidents(scope, self.events(), {})


class Registry(unittest.TestCase):
    def test_the_four_native_hooks_run_in_every_scope_and_excel_gpt_has_a_named_check(self):
        for node_type in ("plan", "script", "split", "image_prompts"):
            spec = node_spec.verifier_spec_for(node_type)
            self.assertIsNotNone(spec, node_type)
            self.assertFalse(spec.managed_only, node_type)
            self.assertEqual(spec.success_errors, "raise")
        excel = node_spec.verifier_spec_for("excel_gpt")
        self.assertEqual(excel.name, "excel_gpt_result")
        self.assertFalse(excel.managed_only)
        self.assertEqual(excel.success_errors, "gap")
        self.assertTrue(callable(excel.resolve()))
        self.assertEqual(node_spec.coverage_level(node_spec.capability_spec_for("excel_gpt")), "managed")
        self.assertIn("verifier:excel_gpt_result", node_spec.coverage_evidence(node_spec.capability_spec_for("excel_gpt")))


class ProjectText(unittest.IsolatedAsyncioTestCase):
    async def test_a_broken_plan_in_observe_mode_is_an_incident_and_does_not_stop_the_run(self):
        project = SimpleNamespace(general_plan="коротко", script_text="")
        with Journal() as j:
            await node_spec.run_verifier_after_success(j.ctx, SimpleNamespace(), project, "plan")
        self.assertEqual(j.kinds(), ["node_result_verification", "runtime_check"])
        self.assertTrue(j.facts[0][1]["observe_only"] and j.facts[0][1]["run_held"] is False)
        (item,) = j.incidents()
        self.assertEqual(item["type"], "contract_rejected")
        self.assertIn("результата ноды plan", item["fact"])
        self.assertEqual(j.gaps, [])

    async def test_a_broken_script_in_observe_mode_does_not_raise(self):
        project = SimpleNamespace(general_plan="x", script_text="мало")
        with Journal() as j:
            await nc.observe_project_text_result(project, "script")
        self.assertEqual(j.kinds(), ["node_result_verification", "runtime_check"])
        self.assertEqual(j.facts[1][1]["status"], "failed")

    async def test_a_good_script_in_observe_mode_is_only_a_passed_record(self):
        project = SimpleNamespace(general_plan="x", script_text="Слово. " * 60)
        with Journal() as j:
            await nc.observe_project_text_result(project, "script")
        self.assertEqual(j.kinds(), ["node_result_verification"])
        self.assertEqual(j.facts[0][1]["status"], "passed")
        self.assertEqual(j.incidents(), [])

    async def test_managed_mode_still_raises_on_a_broken_script(self):
        project = SimpleNamespace(general_plan="x", script_text="мало")
        with Journal(mode="managed") as j:
            with self.assertRaises(RuntimeError):
                await nc.observe_project_text_result(project, "script")
        self.assertEqual(j.kinds(), ["node_result_verification"])
        self.assertNotIn("observe_only", j.facts[0][1])

    async def test_a_failed_native_attempt_is_not_double_reported_in_observe_mode(self):
        project = SimpleNamespace(general_plan="x", script_text="")
        with Journal(exc=ValueError("native failure")) as j:
            await nc.observe_project_text_result(project, "script")
        self.assertEqual(j.facts, [])

    async def test_a_journal_failure_is_a_gap_not_a_node_failure(self):
        project = SimpleNamespace(general_plan="x", script_text="")
        j = Journal()

        async def boom(kind, payload=None, **kw):
            raise OSError("journal down")

        with j, patch("app.services.observation.record_fact", boom):
            await nc.observe_project_text_result(project, "script")
        self.assertEqual(len(j.gaps), 1)


class Db(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 't.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as s:
            s.add(Project(id=PID, topic="t", slug="w42-c02", status=ProjectStatus.images_ready, meta={}))
            await s.commit()
        self.addAsyncCleanup(self.engine.dispose)

    async def put_frames(self, rows):
        async with self.sessions() as s:
            for number, voiceover, prompt in rows:
                s.add(Frame(project_id=PID, number=number, uuid=f"{number:032x}", sort_key=number,
                            voiceover_text=voiceover, image_prompt=prompt, attrs={}))
            await s.commit()


class Split(Db):
    VO = "Первая фраза про библиотеку. Вторая фраза про читателей."

    async def run_split(self, mode="observe"):
        async with self.sessions() as s:
            project = await s.get(Project, PID)
            with Journal(mode=mode) as j:
                j.ctx.split_input_voiceover = self.VO
                held = AsyncMock()
                with patch("app.services.actions_v1.ActionSupervisor") as supervisor:
                    supervisor.return_value.set_held = held
                    try:
                        await nc.observe_split_result(s, project, "split")
                        raised = None
                    except RuntimeError as exc:
                        raised = exc
            return j, held, raised

    async def test_a_split_that_lost_voiceover_is_an_incident_without_a_hold(self):
        await self.put_frames([(1, "Первая фраза про библиотеку.", ""), (2, "Что-то совсем другое.", "")])
        j, held, raised = await self.run_split()
        self.assertIsNone(raised)
        held.assert_not_called()
        self.assertEqual(j.kinds(), ["node_result_verification", "runtime_check"])
        self.assertIn("voiceover_changed_or_missing", j.facts[0][1]["errors"])
        (item,) = j.incidents()
        self.assertEqual(item["type"], "contract_rejected")

    async def test_a_good_split_in_observe_mode_is_a_passed_record_only(self):
        await self.put_frames([(1, "Первая фраза про библиотеку.", ""), (2, "Вторая фраза про читателей.", "")])
        j, held, raised = await self.run_split()
        self.assertIsNone(raised)
        self.assertEqual((j.kinds(), j.facts[0][1]["status"]), (["node_result_verification"], "passed"))
        self.assertEqual(j.incidents(), [])

    async def test_a_managed_split_failure_still_holds_and_raises(self):
        await self.put_frames([(1, "Первая фраза про библиотеку.", ""), (2, "Что-то совсем другое.", "")])
        j, held, raised = await self.run_split(mode="managed")
        self.assertIsInstance(raised, RuntimeError)
        held.assert_awaited_once()


class ImagePrompts(Db):
    def snapshot(self, *, required=True):
        return {"project_id": PID, "frames": [
            {"project_id": PID, "uuid": f"{n:032x}", "number": n, "voiceover_text": f"текст {n}",
             "required_prompt": required} for n in (1, 2)]}

    async def run_hook(self, mode="observe"):
        async with self.sessions() as s:
            project = await s.get(Project, PID)
            with Journal(mode=mode) as j:
                j.ctx.image_prompt_input = self.snapshot()
                held = AsyncMock()
                with patch("app.services.actions_v1.ActionSupervisor") as supervisor:
                    supervisor.return_value.set_held = held
                    try:
                        await nc.observe_image_prompt_result(s, project, "image_prompts")
                        raised = None
                    except RuntimeError as exc:
                        raised = exc
            return j, held, raised

    async def test_short_prompts_in_observe_mode_are_an_incident_that_says_nothing_was_held(self):
        await self.put_frames([(1, "текст 1", "коротко"), (2, "текст 2", LONG_PROMPT)])
        j, held, raised = await self.run_hook()
        self.assertIsNone(raised)
        held.assert_not_called()
        self.assertTrue(j.ctx.image_prompt_verification_recorded)
        self.assertEqual(j.kinds(), ["node_output", "node_result_verification"])
        proof = j.facts[1][1]
        self.assertTrue(proof["observe_only"] and proof["run_held"] is False and proof["status"] == "failed")
        (item,) = j.incidents()
        self.assertEqual(item["type"], "verification_hold")
        self.assertIn("Режим наблюдения", item["fact"])
        self.assertNotIn("Снять удержание может только человек", item["fact"])

    async def test_good_prompts_in_observe_mode_raise_nothing(self):
        await self.put_frames([(1, "текст 1", LONG_PROMPT), (2, "текст 2", LONG_PROMPT)])
        j, held, raised = await self.run_hook()
        self.assertIsNone(raised)
        self.assertEqual(j.facts[1][1]["status"], "passed")
        self.assertEqual(j.incidents(), [])

    async def test_managed_mode_still_holds_the_run(self):
        await self.put_frames([(1, "текст 1", "коротко"), (2, "текст 2", LONG_PROMPT)])
        j, held, raised = await self.run_hook(mode="managed")
        self.assertIsInstance(raised, RuntimeError)
        held.assert_awaited_once()
        self.assertNotIn("observe_only", j.facts[1][1])

    async def test_a_failed_attempt_in_observe_mode_adds_no_second_record(self):
        await self.put_frames([(1, "текст 1", "коротко"), (2, "текст 2", LONG_PROMPT)])
        async with self.sessions() as s:
            project = await s.get(Project, PID)
            with Journal(exc=ValueError("native")) as j:
                await nc.observe_image_prompt_result(s, project, "image_prompts")
        self.assertEqual(j.facts, [])

    def test_the_explanation_of_a_managed_proof_is_unchanged(self):
        proof = {"status": "failed", "errors": ["missing_or_short_parent_prompt:1"], "failures_total": 1,
                 "failures": [{"criterion": "missing_or_short_parent_prompt", "number": 1, "primary_len": 3,
                               "second_len": 0}]}
        self.assertTrue(nc.explain_image_prompt_verification(proof)["summary"].endswith(
            "Снять удержание может только человек."))


class ExcelGpt(Db):
    def test_frame_structure_catches_duplicates_and_empty_ids(self):
        mk = lambda n, u, pid=PID: SimpleNamespace(number=n, uuid=u, project_id=pid)
        self.assertEqual(nc.verify_frame_structure([mk(1, "a"), mk(2, "b")], project_id=PID)["status"], "passed")
        self.assertEqual(nc.verify_frame_structure([mk(1, "a"), mk(1, "b")], project_id=PID)["reason"],
                         "duplicate_number")
        self.assertEqual(nc.verify_frame_structure([mk(1, "a"), mk(2, "a")], project_id=PID)["reason"],
                         "duplicate_uuid")
        self.assertEqual(nc.verify_frame_structure([mk(1, " ")], project_id=PID)["reason"], "missing_uuid")
        self.assertEqual(nc.verify_frame_structure([mk(0, "a")], project_id=PID)["reason"], "bad_number")
        self.assertEqual(nc.verify_frame_structure([mk(1, "a", pid=99)], project_id=PID)["reason"], "project_scope")
        none = nc.verify_frame_structure([], project_id=PID)
        self.assertEqual((none["status"], none["reason"]), ("unknown", "no_frames_yet"))

    async def test_hook_records_a_passed_structure_and_no_incident(self):
        await self.put_frames([(1, "a", ""), (2, "b", "")])
        async with self.sessions() as s:
            with Journal() as j:
                await nc.observe_excel_gpt_result(s, None, "excel_gpt")
        self.assertEqual((j.kinds(), j.facts[0][1]["status"], j.facts[0][1]["frame_count"]),
                         (["node_result_verification"], "passed", 2))
        self.assertEqual(j.incidents(), [])

    async def test_hook_on_a_broken_structure_is_an_incident_in_both_scopes_and_never_raises(self):
        broken = [SimpleNamespace(number=1, uuid="a", project_id=PID), SimpleNamespace(number=1, uuid="b",
                                                                                      project_id=PID)]
        for mode in ("observe", "managed"):
            session = SimpleNamespace(no_autoflush=_NoAutoflush(), scalars=AsyncMock(
                return_value=SimpleNamespace(all=lambda: broken)))
            with Journal(mode=mode) as j:
                await nc.observe_excel_gpt_result(session, None, "excel_gpt")
            self.assertEqual(j.kinds(), ["node_result_verification", "runtime_check"], mode)
            self.assertEqual(len(j.incidents()), 1, mode)

    async def test_a_read_failure_is_a_gap_and_a_failed_attempt_is_skipped(self):
        session = SimpleNamespace(no_autoflush=_NoAutoflush(), scalars=AsyncMock(side_effect=OSError("db")))
        with Journal() as j:
            await nc.observe_excel_gpt_result(session, None, "excel_gpt")
        self.assertEqual((j.facts, len(j.gaps)), ([], 1))
        with Journal(exc=ValueError("x")) as j:
            await nc.observe_excel_gpt_result(session, None, "excel_gpt")
        self.assertEqual((j.facts, j.gaps), ([], []))


class _NoAutoflush:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


if __name__ == "__main__":
    unittest.main()
