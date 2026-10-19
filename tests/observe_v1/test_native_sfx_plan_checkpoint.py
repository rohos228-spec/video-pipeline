from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus
from app.orchestrator.steps import plan_sfx
from app.services.ai_result_io import ai_job_checkpoint, save_ai_job_checkpoint
from app.services.sfx_plan import plan_sfx_events
from app.settings import settings


class NativeSfxPlanCheckpoint(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "project"
        self.data.mkdir()
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///" + str(self.data / "project.db")
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        self.checkpoint = {
            "events": [
                {
                    "frame_number": 2, "t_start": 3.512345,
                    "duration": 1.212345, "kind": "HIT",
                    "prompt": "light desk hit", "gain": 0.5, "duck": False,
                },
                {
                    "frame_number": 1, "t_start": 0.512345,
                    "duration": 1.012345, "kind": "whoosh",
                    "prompt": "soft paper whoosh", "gain": 0.2, "duck": True,
                },
            ]
        }
        async with self.sessions() as session:
            project = Project(
                id=2, slug="synthetic-sfx-checkpoint", topic="Synthetic SFX",
                status=ProjectStatus.sfx_planning, meta={},
            )
            save_ai_job_checkpoint(project, "sfx_plan", self.checkpoint)
            session.add(project)
            for number, uid in enumerate(("af" * 16, "bf" * 16), 1):
                session.add(Frame(
                    project_id=2, number=number, uuid=uid, sort_key=number,
                    voiceover_text="Synthetic voiceover " + str(number),
                    duration_seconds=3.0, attrs={},
                ))
            await session.commit()

        async with self.sessions() as session:
            self.source_frames = await self._frame_snapshot(session)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def _frame_snapshot(self, session):
        frames = (await session.scalars(
            select(Frame).where(Frame.project_id == 2).order_by(Frame.number)
        )).all()
        return [
            (frame.uuid, frame.number, frame.sort_key, frame.voiceover_text,
             frame.duration_seconds, copy.deepcopy(frame.attrs))
            for frame in frames
        ]

    async def _assert_persisted(self, expected_status, checkpoint, *, ready):
        async with self.sessions() as reader:
            project = await reader.get(Project, 2)
            self.assertEqual(project.status, expected_status)
            self.assertEqual(ai_job_checkpoint(project, "sfx_plan"), checkpoint)
            self.assertEqual(bool(project.meta.get("sfx_plan")), ready)
            self.assertEqual(await self._frame_snapshot(reader), self.source_frames)

    def _assert_output(self, events):
        output = json.loads((self.data / "sfx_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(output, {
            "total_duration": 6.0,
            "frame_starts": {"1": 0.0, "2": 3.0},
            "events": [{**event.to_dict(), "offset_in_frame": 0.512} for event in events],
        })

    async def test_valid_persisted_checkpoint_replays_native_parse_without_model(self):
        no_model = AsyncMock(side_effect=AssertionError("checkpoint must not call model"))
        with patch.object(Project, "data_dir", property(lambda _: self.data)), \
             patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
            for _ in range(2):
                async with self.sessions() as session:
                    project = await session.get(Project, 2)
                    self.assertEqual(ai_job_checkpoint(project, "sfx_plan"), self.checkpoint)
                    frames = (await session.scalars(
                        select(Frame).where(Frame.project_id == 2).order_by(Frame.number)
                    )).all()
                    events = await plan_sfx_events(session, project, frames)
                    self.assertEqual(
                        [(event.frame_number, event.t_start, event.duration, event.kind)
                         for event in events],
                        [(1, 0.512, 1.012, "whoosh"), (2, 3.512, 1.212, "hit")],
                    )
                    self._assert_output(events)
                    self.assertEqual(project.status, ProjectStatus.sfx_planning)
                    await session.commit()
                await self._assert_persisted(
                    ProjectStatus.sfx_planning, self.checkpoint, ready=False
                )
        no_model.assert_not_called()
        no_model.assert_not_awaited()

    async def test_valid_persisted_checkpoint_reaches_ready_only_through_native_step(self):
        no_model = AsyncMock(side_effect=AssertionError("checkpoint must not call model"))
        with patch.object(Project, "data_dir", property(lambda _: self.data)), \
             patch.object(settings, "sfx_enabled", True), \
             patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
            async with self.sessions() as session:
                project = await session.get(Project, 2)
                self.assertEqual(ai_job_checkpoint(project, "sfx_plan"), self.checkpoint)
                await plan_sfx.run(session, project)
                self.assertEqual(project.status, ProjectStatus.sfx_plan_ready)
            await self._assert_persisted(
                ProjectStatus.sfx_plan_ready, self.checkpoint, ready=True
            )
        output = json.loads((self.data / "sfx_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(output["total_duration"], 6.0)
        self.assertEqual([event["frame_number"] for event in output["events"]], [1, 2])
        no_model.assert_not_called()
        no_model.assert_not_awaited()

    async def _assert_invalid_checkpoint_rejected(self, field, value):
        invalid = copy.deepcopy(self.checkpoint)
        invalid["events"][0][field] = value
        async with self.sessions() as writer:
            project = await writer.get(Project, 2)
            save_ai_job_checkpoint(project, "sfx_plan", invalid)
            await writer.commit()

        no_model = AsyncMock(side_effect=AssertionError("invalid cache must not call model"))
        with patch.object(Project, "data_dir", property(lambda _: self.data)), \
             patch.object(settings, "sfx_enabled", True), \
             patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
            async with self.sessions() as session:
                project = await session.get(Project, 2)
                self.assertEqual(ai_job_checkpoint(project, "sfx_plan"), invalid)
                with self.assertRaises(RuntimeError):
                    await plan_sfx.run(session, project)
                self.assertEqual(project.status, ProjectStatus.sfx_planning)
                self.assertFalse(project.meta.get("sfx_plan"))
                self.assertFalse((self.data / "sfx_plan.json").exists())
                await session.commit()
            await self._assert_persisted(
                ProjectStatus.sfx_planning, invalid, ready=False
            )
        no_model.assert_not_called()
        no_model.assert_not_awaited()

    async def test_persisted_checkpoint_time_outside_current_timeline_rejected(self):
        await self._assert_invalid_checkpoint_rejected("t_start", 99.0)

    async def test_persisted_checkpoint_unknown_kind_rejected(self):
        await self._assert_invalid_checkpoint_rejected("kind", "laser")

    async def test_persisted_checkpoint_invalid_gain_rejected(self):
        await self._assert_invalid_checkpoint_rejected("gain", 2.0)

    async def test_persisted_checkpoint_invalid_duration_rejected(self):
        await self._assert_invalid_checkpoint_rejected("duration", 0.1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
