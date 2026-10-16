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

    project_fields: dict = {}
    project_meta: dict = {"outsee_streams": 1, "img_streams": 1}
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
        async with self.business() as session:
            return await session.scalar(select(Frame).where(Frame.project_id == base.PID, Frame.number == number))

    async def worker(self, *, start=True):
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
        async def recording(*args, **kwargs):
            try:
                return await real(*args, **kwargs)
            except Exception as exc:
                self.swallowed.append(exc)
                raise
        return recording

    def surface_swallowed(self, project):
        if self.surface_failures and self.swallowed and project.status is self.status:
            raise self.swallowed[-1]

    async def fail_natively(self, error_type=Exception):
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
