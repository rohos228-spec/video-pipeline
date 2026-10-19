import unittest
from unittest.mock import patch

import pytest
from sqlalchemy import select

import media_dry_run_base as base
import test_media_dry_run_items as ref
import w4_5_real_step_stand as stand
from app.models import Artifact, ArtifactKind, ProjectStatus
from app.services import media_spend, outsee_idempotency as idem
from app.services.video_error_policy import is_no_resubmit

pytestmark = pytest.mark.skipif(not base.HAVE_FFMPEG, reason="ffmpeg/ffprobe are required")


class ItemsIdempotency(ref.ItemsDryRun):
    async def attempts(self, key="predmet1"):
        async with self.ledger() as session:
            return await idem._attempts(session, base.PID, key)

    async def test_the_result_is_recorded_and_consumed_by_the_step(self):
        await self.worker()
        self.assertEqual(len(self.outsee_calls), 1)
        atts = await self.attempts()
        self.assertEqual([a["events"] for a in atts], [["intent", "result", "consumed"]])
        self.assertEqual(atts[0]["state"], "consumed")

    @unittest.expectedFailure
    async def test_process_dies_after_the_paid_result_restart_takes_the_file_and_pays_nothing(self):
        async def dies_after_result(project_id, key):
            raise base.ProcessDied()

        real_record = stand.observation.record_fact

        async def dead_process_writes_nothing(kind, payload, **kw):
            if kind == "node_error" and payload.get("error_type") == "ProcessDied":
                return None
            return await real_record(kind, payload, **kw)

        with patch.object(stand.observation, "record_fact", dead_process_writes_nothing), \
                patch.object(idem, "mark_consumed", dies_after_result):
            with self.assertRaises(base.ProcessDied):
                await self.worker()
        self.assertEqual(len(self.outsee_calls), 1)
        self.assertEqual([a["state"] for a in await self.attempts()], ["result"])
        files_before = sorted(p.name for p in self.result_files())
        self.assertEqual(len(files_before), 1)
        async with self.business() as session:
            arts = (await session.scalars(select(Artifact).where(Artifact.kind == ArtifactKind.item_reference))).all()
        self.assertLessEqual(len(arts), 1)

        await self.worker()
        self.assertEqual(len(self.outsee_calls), 1, "второй платный запрос не должен уйти")
        self.assertEqual(self.last_status_change, (ProjectStatus.generating_items, ProjectStatus.items_ready))
        self.assertEqual(sorted(p.name for p in self.result_files()), files_before)
        async with self.business() as session:
            arts = (await session.scalars(select(Artifact).where(Artifact.kind == ArtifactKind.item_reference))).all()
        self.assertEqual([a.path.split("\\")[-1].split("/")[-1] for a in arts], files_before)
        self.assertEqual((await self.attempts())[0]["state"], "consumed")

    async def test_process_dies_after_send_restart_is_blocked_with_a_typed_uncertain_status(self):
        self.outsee_script = [base.ProcessDied()]
        real_record = stand.observation.record_fact

        async def dead_process_writes_nothing(kind, payload, **kw):
            if kind == "node_error" and payload.get("error_type") == "ProcessDied":
                return None
            return await real_record(kind, payload, **kw)

        with patch.object(stand.observation, "record_fact", dead_process_writes_nothing):
            with self.assertRaises(base.ProcessDied):
                await self.worker()
        self.assertEqual([a["events"] for a in await self.attempts()], [["intent"]])
        with self.assertRaises(RuntimeError) as blocked:
            await self.worker()
        self.assertIn("исход платного запроса в Outsee неизвестен", str(blocked.exception))
        self.assertTrue(is_no_resubmit(blocked.exception.__cause__), type(blocked.exception.__cause__))
        self.assertEqual(len(self.outsee_calls), 1)
        found = self.data / "items" / "found_in_history.png"
        found.write_bytes(base.media_bytes("image", base.Path(self.tmp.name) / "scratch"))
        self.assertTrue(await idem.adopt_file(base.PID, "predmet1", found))
        await self.worker()
        self.assertEqual(len(self.outsee_calls), 1)
        self.assertEqual(self.last_status_change, (ProjectStatus.generating_items, ProjectStatus.items_ready))
        async with self.business() as session:
            arts = (await session.scalars(select(Artifact).where(Artifact.kind == ArtifactKind.item_reference))).all()
        self.assertEqual([a.path for a in arts], [str(found)])


for _name in [n for n, v in vars(ref.ItemsDryRun).items() if n.startswith("test_") and v is not None]:
    setattr(ItemsIdempotency, _name, None)
del _name

if __name__ == "__main__":
    unittest.main(verbosity=2)
