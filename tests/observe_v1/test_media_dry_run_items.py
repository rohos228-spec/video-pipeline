import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import select

import media_dry_run_base as base
import w4_5_real_step_stand as stand
from app.bots import outsee as outsee_module
from app.bots.outsee import GenerationResult, OutseeBot, OutseeImageError
from app.models import Artifact, ArtifactKind, Project, ProjectStatus
from app.orchestrator.steps import generate_items
from app.services import media_admission, media_spend, media_verification

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


class StandOutseeBot(OutseeBot):

    owner = None

    async def _generate_image_on_page(self, page, *, prompt, out_path, model_slug=None, **kw):
        owner = StandOutseeBot.owner
        owner.outsee_calls.append({"model_slug": model_slug, "out_path": out_path, "prompt": prompt})
        step = owner.outsee_script.pop(0) if owner.outsee_script else "ok"
        outsee_module._mark_submit(maybe_sent=True)
        if isinstance(step, BaseException):
            raise step
        out_path.write_bytes(base.media_bytes("image", base.Path(owner.tmp.name) / "scratch"))
        outsee_module._mark_submit(done=True)
        return GenerationResult(file_path=out_path, gen_id="stand")


class ItemsDryRun(stand.RealStepStand, base.MediaDryRun):
    fixture_node_type = "items"
    status = ProjectStatus.generating_items
    running_key = "generating_items"
    api = "jobs"
    media_kind = "image"
    result_ext = ".png"
    key = "predmet1"
    surface_failures = False

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.outsee_calls, self.outsee_script = [], []
        StandOutseeBot.owner = self

        @asynccontextmanager
        async def browser():
            yield SimpleNamespace(open_page=self.fake_open_page)

        async def open_page(*_a, **_k):
            return SimpleNamespace()

        self.fake_open_page = open_page
        for patcher in (patch.object(generate_items, "browser_session", browser),
                        patch.object(generate_items, "OutseeBot", StandOutseeBot),
                        patch.object(generate_items, "get_gpt_client", lambda: base.FakeGpt()),
                        patch.object(media_spend, "TEST_FORCE", True)):
            self.stack.enter_context(patcher)

    async def native_step(self, session, project):
        await generate_items.run(session, project, None)

    def result_files(self):
        return (self.data / "items").glob("predmet1_*.png")

    async def change_request_inputs(self):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            project.item_descriptions = ["синяя тарелка"]
            await session.commit()

    async def intent_rows(self):
        return [r for r in await self.ledger_rows() if r.api == "outsee"]

    async def test_real_step_makes_one_outsee_request_and_no_kie_task(self):
        await self.worker()
        self.assertEqual(self.last_status_change, (ProjectStatus.generating_items, ProjectStatus.items_ready))
        self.assertEqual(len(self.outsee_calls), 1)
        self.assertEqual(self.outsee_calls[0]["model_slug"], "gpt-image-2-vip")
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))
        rows = await self.intent_rows()
        self.assertTrue(rows and all(r.task_id.startswith("intent:") for r in rows), [r.task_id for r in rows])
        self.assertEqual([r.status for r in rows][-1], "bound")
        self.assertEqual([i for i in await self.task_ids() if not i.startswith("intent:")], [])
        self.assertEqual(len(list(self.result_files())), 1)
        async with self.business() as session:
            arts = (await session.scalars(select(Artifact).where(Artifact.kind == ArtifactKind.item_reference))).all()
        self.assertEqual([(a.meta["item_index"], a.meta["item_id"]) for a in arts], [(1, "predmet1")])

    async def test_a_kie_model_slug_still_goes_to_outsee_never_to_kie(self):
        for factory in (self.business, self.sessions, self.ledger):
            async with factory() as session:
                project = await session.get(Project, base.PID)
                graph = dict(project.meta["canvas_graph"])
                graph["nodes"] = [{**n, "data": {**n["data"], "modelId": "z-image"}} if n["type"] == "items" else n
                                  for n in graph["nodes"]]
                project.meta = {**project.meta, "canvas_graph": graph}
                await session.commit()
        await self.worker()
        self.assertEqual([c["model_slug"] for c in self.outsee_calls], ["z-image"])
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))
        self.assertEqual([i for i in await self.task_ids() if not i.startswith("intent:")], [])
        self.assertEqual(len(list(self.result_files())), 1)

    async def test_real_step_success_is_verified_by_the_attempt_session_not_failed_too_early(self):
        await self.worker()
        proof = (await self.facts("node_result_verification"))[-1].payload
        self.assertEqual((proof["node_type"], proof["status"], proof["reason"]), ("items", "passed", None))
        async with self.business() as reader:
            fresh = await media_verification.build_proof(reader, await reader.get(Project, base.PID), "items")
        self.assertEqual((fresh["status"], fresh["items_passed"]), ("passed", 1), fresh)

    async def test_failed_outsee_request_rolls_the_step_back_and_the_supervisor_has_nothing_to_resume(self):
        self.outsee_script = [OutseeImageError("synthetic provider refusal")] * 12
        with self.assertRaises(RuntimeError) as caught:
            await self.worker()
        self.assertIn("не удалось сгенерить", str(caught.exception))
        self.assertGreaterEqual(len(self.outsee_calls), 1)
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))
        await self.exhaust_native_retries("items step failed")
        self.assertEqual(len(list(self.result_files())), 0)
        incident = await self.media_incident()
        report = await base.media_recovery.assess(SimpleNamespace(id=base.PID, data_dir=self.data), "items")
        self.assertNotEqual(report["verdict"]["mode"], "resume_poll")
        await self.enable()
        calls_before = len(self.outsee_calls)
        with self.assertRaises((media_admission.MediaAdmissionDenied, ValueError)):
            await self.recover(incident)
        self.assertEqual((len(self.outsee_calls), self.creates, await self.tickets()), (calls_before, 0, []))

    async def test_killed_process_then_restart_never_sends_a_second_paid_outsee_request(self):
        self.outsee_script = [base.ProcessDied()]
        real_record = stand.observation.record_fact

        async def dead_process_writes_nothing(kind, payload, **kw):
            if kind == "node_error" and payload.get("error_type") == "ProcessDied":
                return None
            return await real_record(kind, payload, **kw)

        with patch.object(stand.observation, "record_fact", dead_process_writes_nothing):
            with self.assertRaises(base.ProcessDied):
                await self.worker()
        self.assertEqual(len(self.outsee_calls), 1)
        unresolved = [r for r in await self.intent_rows() if r.status == "submit_unknown"]
        self.assertEqual(len(unresolved), 1)
        with self.assertRaises(RuntimeError):
            await self.worker()
        self.assertEqual(len(self.outsee_calls), 1, "the second attempt must not reach the provider")
        await self.exhaust_native_retries("items step failed")
        self.assertEqual((self.creates, self.polls, self.downloads), (0, 0, 0))
        incident = await self.media_incident()
        await self.enable()
        with self.assertRaises(media_admission.MediaAdmissionDenied) as denied:
            await self.recover(incident)
        self.assertIn("unknown_outcome_outsee", str(denied.exception))
        self.assertEqual((len(self.outsee_calls), await self.tickets()), (1, []))


for _name in [n for n in vars(base.MediaDryRun) if n.startswith("test_")]:
    setattr(ItemsDryRun, _name, None)
del _name


if __name__ == "__main__":
    unittest.main(verbosity=2)
