import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.models import Project, WorkflowRun
from app.services import observation, prompt_active_global, prompt_library, revisions
from app.services.actions_recovery import RecoveryAction, claim, input_revision
from app.services.observation import ObservationEvent
from app.settings import settings
import test_actions_v1_contract as base_tests


class RecoveryRevisionScope(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = base_tests.DurableActions.asyncSetUp
    asyncTearDown = base_tests.DurableActions.asyncTearDown
    exhausted_fixture = base_tests.DurableActions.exhausted_fixture
    retry = base_tests.DurableActions.retry
    assert_no_dispatch = base_tests.DurableActions.assert_no_dispatch

    def isolate_prompts(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.prompts = Path(tmp.name)
        (self.prompts / "05_excel_gpt").mkdir(parents=True)
        (self.prompts / "05_excel_gpt" / "default.md").write_text("gpt master", encoding="utf-8")
        for target in (prompt_library, prompt_active_global):
            patcher = patch.object(target, "PROMPTS_ROOT", self.prompts)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def record(self, canvas=None):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            if canvas is not None:
                project.meta = {**project.meta, "canvas_graph": canvas}
            parts = {}
            revision = await input_revision(s, project, "n1", parts=parts)
            start = await s.scalar(select(ObservationEvent).where(ObservationEvent.kind == "node_start"))
            start.payload = {**start.payload, "recovery_input_revision": revision, "recovery_revision_parts": parts}
            await s.commit()

    async def set_canvas(self, canvas):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            project.meta = {**project.meta, "canvas_graph": canvas}
            await s.commit()

    async def claim_now(self):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            return await claim(observation._observer, s, project, "n1", "ordinary-attempt",
                               execution_id="exec-durable")

    def canvas(self):
        return {"nodes": copy.deepcopy(self.nodes), "edges": []}

    async def test_ticket_records_scheme_and_node_config_revision(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record()
        ticket = await self.retry()
        pre = ticket["preconditions"]
        self.assertEqual(pre["revision_scheme"], revisions.REVISION_SCHEME)
        self.assertEqual(pre["input_parts"]["revision_scheme"], revisions.REVISION_SCHEME)
        self.assertEqual(pre["node_config_revision"], pre["input_parts"]["node_config_revision"])
        self.assertEqual(pre["graph_version"], revisions.graph_version(self.nodes, []))

    async def test_irrelevant_changes_after_reservation_keep_the_claim_valid(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record(self.canvas())
        ticket = await self.retry()
        canvas = self.canvas()
        canvas["nodes"][1]["data"]["modelId"] = "other-model"
        canvas["nodes"][1]["data"]["outputMode"] = "text"
        canvas["nodes"][0]["data"]["label"] = "Renamed in the editor"
        canvas["nodes"][0]["position"] = {"x": 42, "y": 42}
        await self.set_canvas(canvas)
        (self.prompts / "05_excel_gpt" / "unrelated_variant.md").write_text("unused", encoding="utf-8")
        with patch.object(settings, "whisper_model", "other-whisper"), \
                patch.object(settings, "watchdog_model_request_timeout_s", 991):
            self.assertEqual(await self.claim_now(), ticket["new_attempt_id"])

    async def relevant_change_is_rejected(self, mutate):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record(self.canvas())
        await self.retry()
        await mutate()
        with self.assertRaisesRegex(RuntimeError, "revisions changed before claim"):
            await self.claim_now()

    async def test_own_prompt_file_invalidates_the_ticket(self):
        async def mutate():
            (self.prompts / "05_excel_gpt" / "default.md").write_text("gpt master, edited", encoding="utf-8")
        await self.relevant_change_is_rejected(mutate)

    async def test_own_model_override_invalidates_the_ticket(self):
        async def mutate():
            canvas = self.canvas()
            canvas["nodes"][0]["data"]["modelId"] = "gpt-5.5-vibecode"
            await self.set_canvas(canvas)
        await self.relevant_change_is_rejected(mutate)

    async def test_provider_setting_invalidates_the_ticket(self):
        async def mutate():
            patcher = patch.object(settings, "gpt_timeout_s", 4321)
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.relevant_change_is_rejected(mutate)

    async def test_executor_module_invalidates_the_ticket(self):
        async def mutate():
            real = revisions._file_hash
            patcher = patch.object(revisions, "_file_hash", side_effect=lambda rel: real(rel) + (
                "x" if rel == "app/orchestrator/steps/enrich_xlsx.py" else ""))
            patcher.start()
            self.addCleanup(patcher.stop)
        await self.relevant_change_is_rejected(mutate)

    async def test_edges_invalidate_the_reservation_by_graph_structure(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record()
        async with self.sessions() as s:
            run = await s.get(WorkflowRun, 11)
            run.edges_snapshot = [{"source": "n1", "target": "n2"}]
            await s.commit()
        with self.assertRaisesRegex(ValueError, "graph revision changed"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_source_attempt_recorded_under_legacy_scheme_is_rejected_with_a_reason(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record()
        async with self.sessions() as s:
            start = await s.scalar(select(ObservationEvent).where(ObservationEvent.kind == "node_start"))
            legacy_parts = {k: v for k, v in start.payload["recovery_revision_parts"].items()
                            if k != "revision_scheme"}
            start.payload = {**start.payload, "recovery_revision_parts": legacy_parts}
            await s.commit()
        with self.assertRaisesRegex(ValueError, "legacy scheme 1"):
            await self.retry()
        await self.assert_no_dispatch()

    async def test_queued_ticket_reserved_under_legacy_scheme_is_rejected_at_claim(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record()
        await self.retry()
        async with self.sessions() as s:
            row = await s.scalar(select(RecoveryAction))
            pre = dict(row.preconditions)
            pre["input_parts"] = {k: v for k, v in pre["input_parts"].items() if k != "revision_scheme"}
            row.preconditions = pre
            await s.commit()
        with self.assertRaisesRegex(RuntimeError, "legacy scheme 1"):
            await self.claim_now()
        async with self.sessions() as s:
            self.assertEqual((await s.scalar(select(RecoveryAction))).status, "queued")

    async def test_execution_with_legacy_graph_version_is_rejected_with_a_reason(self):
        self.isolate_prompts()
        await self.exhausted_fixture()
        await self.record()
        legacy = revisions.legacy_graph_version(self.nodes, [])
        async with self.sessions() as s:
            (await s.get(base_tests.ActionExecutionControl, "exec-durable")).graph_version = legacy
            (await s.get(observation.ExecutionControl, "exec-durable")).graph_version = legacy
            await s.commit()
        with self.assertRaisesRegex(ValueError, "legacy scheme 1"):
            await self.retry()
        await self.assert_no_dispatch()


if __name__ == "__main__":
    unittest.main()
