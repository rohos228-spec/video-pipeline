"""W4-5 / C07: shared pieces of the media dry runs that execute the REAL node step.

Used with media_dry_run_base.MediaDryRun (which is not edited here): ``class X(RealStepStand, base.MediaDryRun)``.

What is REAL in a stand built from this mixin: the step body itself (``generate_videos.run`` /
``generate_items.run``) with its claim loop, ladder, own database sessions and post-step validation, the paid
ladder, the guard rows, kie_http, the provider task ledger, the observation / verification / diagnosis /
recovery chain of the base harness.
What is SCRIPTED (the provider substitute only): the Kie HTTP boundary (createTask / status / download, from the
base), the public upload of a start frame, the Outsee browser page work (items), the Decision model and the
Telegram send of the step.

Honest limits, stated once:
* The step's own ``SessionLocal()`` sessions read the master copy of Project/Frame; in a stand that copy is the
  ledger database, into which the rows of the fixture are mirrored once at set-up. The real project.db / master
  routing of production is not reproduced.
* A killed process is modelled by ``ProcessDied`` (BaseException): ``asyncio.gather(return_exceptions=True)``
  would otherwise keep a BaseException as an ordinary "result" and the step would go on living, so the stand's
  ``gather`` re-raises it (a dead process ends the whole step).
* ``surface_failures`` (videos only): the real step swallows every per-frame provider failure in its job and
  returns without raising, so the observed attempt has no ``node_error`` fact and the supervised recovery
  refuses it ("unfinished attempt"). That is a REAL gap (pinned by a test). To still exercise the recovery
  chain behind it, the stand can re-raise the swallowed failure after the step returned; this adapter is a
  stand-side assumption about a future fix, not production behaviour.
* No live proof: nothing here touches a network, a paid provider or real data.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select

import media_dry_run_base as base
from app.models import Frame, NodeRun, Project
from app.services import observation
from app.services.node_status_machine import complete_node, fail_node, queue_node_for_start, start_node_running


class DyingAsyncio:
    """``asyncio`` as seen by a step module in a stand: gather lets a dead process stop the step."""

    def __getattr__(self, name):
        return getattr(asyncio, name)

    @staticmethod
    async def gather(*aws, return_exceptions=False):
        results = await asyncio.gather(*aws, return_exceptions=True)
        for item in results:
            if isinstance(item, BaseException) and not isinstance(item, (Exception, asyncio.CancelledError)):
                raise item
        if not return_exceptions:
            for item in results:
                if isinstance(item, BaseException):
                    raise item
        return results


def clone_row(model, source):
    values = {column.key: getattr(source, column.key) for column in sa_inspect(model).column_attrs}
    return model(**values)


class RealStepStand:
    """Mixin: mirrors the fixture into the step's own database and runs the node worker like the runtime."""

    # Project columns the stand sets before the mirror is taken (e.g. the video generator).
    project_fields: dict = {}
    # extra project.meta keys (e.g. the outsee streams of the step)
    project_meta: dict = {"outsee_streams": 1, "img_streams": 1}
    # re-raise a failure the step swallowed (see the module docstring); False = the step as it really behaves
    surface_failures = True

    async def asyncSetUp(self):
        await super().asyncSetUp()
        for factory in (self.sessions, self.business):
            async with factory() as session:
                project = await session.get(Project, base.PID)
                project.meta = {**dict(project.meta or {}), **self.project_meta}
                for key, value in self.project_fields.items():
                    setattr(project, key, value)
                await session.commit()
        await self.mirror_step_database()

    async def mirror_step_database(self):
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            frames = (await session.scalars(select(Frame).where(Frame.project_id == base.PID)
                                            .order_by(Frame.number))).all()
            copies = [clone_row(Project, project), *[clone_row(Frame, f) for f in frames]]
        async with self.ledger() as session:
            session.add_all(copies)
            await session.commit()

    async def step_database_frame(self, number=1):
        async with self.ledger() as session:
            return await session.scalar(select(Frame).where(Frame.project_id == base.PID, Frame.number == number))

    async def worker(self, *, start=True):
        """One advance tick of the original step under observe_attempt, as advance_project_job does it:
        the node is completed only when the project status moved on; a step that returns without moving
        the status is NOT a completed node (and not an exception either)."""
        async with self.business() as session:
            project = await session.get(Project, base.PID)
            node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            if start:
                self.assertTrue(queue_node_for_start(node, project_id=base.PID, initiator="worker"))
                self.assertTrue(start_node_running(node, project_id=base.PID, initiator="worker"))
                await session.commit()
            previous = project.status
            try:
                async with observation.observe_attempt(session, project):
                    await self.native_step(session, project)
            except BaseException:
                await session.rollback()
                node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
                fail_node(node, "attempt failed", project_id=base.PID, initiator="worker")
                node.finished_at = datetime.utcnow()
                await session.commit()
                raise
            self.last_status_change = (previous, project.status)
            if project.status != previous:
                node = await session.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
                complete_node(node, project_id=base.PID, initiator="worker")
            await session.commit()

    def record_swallowed(self, real):
        """Wrap the ladder the step calls: the step still swallows the failure, the stand only remembers it."""
        async def recording(*args, **kwargs):
            try:
                return await real(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - re-raised unchanged
                self.swallowed.append(exc)
                raise
        return recording

    def surface_swallowed(self, project):
        if self.surface_failures and self.swallowed and project.status is self.status:
            raise self.swallowed[-1]

    async def fail_natively(self, error_type=Exception):
        """A failed native attempt. With ``surface_failures`` the swallowed failure reaches observe_attempt as
        an exception of the expected type; without it the step returns quietly (its real behaviour) and the
        runtime only counts the stalled step."""
        self.swallowed = []
        if self.surface_failures:
            with self.assertRaises(error_type) as caught:
                await self.worker()
            await self.exhaust_native_retries()
            return caught.exception
        await self.worker()
        self.assertEqual(self.last_status_change[0], self.last_status_change[1],
                         "a failed native attempt must not advance the project status")
        await self.exhaust_native_retries()
        return None

    @asynccontextmanager
    async def fake_browser_session(self):
        yield SimpleNamespace(open_page=self.fake_open_page)

    async def fake_open_page(self, *a, **k):
        raise AssertionError("the stand never drives a real page")
