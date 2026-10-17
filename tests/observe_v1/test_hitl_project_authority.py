import asyncio
from contextlib import ExitStack
from datetime import datetime
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

if not Path("/.dockerenv").is_file():
    raise RuntimeError("HITL authority tests require the parent's isolated Docker runner")

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app import db, project_db
from app.models import Base, HITLDecision, HITLKind, HITLRequest, Project, ProjectStatus
from app.settings import settings
from app.web import deps
from app.web.routers import hitl


class HITLProjectAuthority(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.master_path = self.root / "master.db"
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.master_path}")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        for obj, attr, value in (
            (settings, "data_dir", self.root / "data"),
            (settings, "sqlite_path", self.master_path),
            (db, "engine", self.engine),
            (db, "SessionLocal", self.sessions),
            (deps, "SessionLocal", self.sessions),
            (project_db, "_ENGINES", {}),
            (project_db, "_SESSION_MAKERS", {}),
            (project_db, "_PROJECT_DIR_CACHE", {}),
            (project_db, "_LOCK", asyncio.Lock()),
        ):
            self.resources.enter_context(patch.object(obj, attr, value))
        self.addAsyncCleanup(project_db.close_all_project_engines)
        async with self.sessions() as session:
            session.add_all([self._project(7), self._project(8), self._project(17)])
            await session.flush()
            session.add_all([
                self._request(71, 7, "master-stale-target"),
                self._request(83, 8, "master-stale-foreign"),
                self._request(91, 17, "master-only-pending"),
            ])
            await session.commit()
        self.target_dir = await self._project_file(
            7, owners=(7, 8), requests=(
                (71, 7, "worker-project-target-older"),
                (74, 7, "worker-project-target-newer"),
                (75, 8, "foreign-row-inside-target-db"),
            ),
        )
        self.foreign_dir = await self._project_file(
            8, owners=(8,), requests=((71, 8, "foreign-project-colliding-id"),),
        )
        self.app = FastAPI()
        self.app.include_router(hitl.router, prefix="/api")
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://local",
        )
        self.addAsyncCleanup(self.client.aclose)

    @staticmethod
    def _project(project_id):
        return Project(id=project_id, slug=f"synthetic-hitl-{project_id}",
                       topic="Read-only pending HITL input fixture", status=ProjectStatus.new,
                       auto_mode=False, meta={"synthetic_only": True})

    @staticmethod
    def _request(hitl_id, project_id, origin):
        return HITLRequest(
            id=hitl_id, project_id=project_id, kind=HITLKind.approve_final,
            decision=HITLDecision.pending, decided_at=None,
            payload={"step": "final", "synthetic_only": True, "fixture_origin": origin,
                     "video_path": f"/synthetic-inputs/project-{project_id}/{origin}.mp4"},
            created_at=datetime(2026, 9, 30, 12, 0, 0),
        )

    async def _project_file(self, project_id, *, owners, requests):
        folder = settings.data_dir / "videos" / f"synthetic-hitl-{project_id}"
        engine = await project_db.get_project_engine(folder)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = await project_db.get_project_sessionmaker(folder)
        async with sessions() as session:
            session.add_all([self._project(owner) for owner in owners])
            await session.flush()
            session.add_all([self._request(*args) for args in requests])
            await session.commit()
        return folder

    def _snapshot(self):
        files = {}
        for path in sorted(self.root.rglob("*.db")):
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                files[str(path.relative_to(self.root))] = tuple(connection.iterdump())
        return {"sqlite": files,
                "backups": sorted(str(p.relative_to(self.root)) for p in self.root.rglob("*.bak"))}

    async def _get(self, project_id):
        before = self._snapshot()
        self.assertFalse(self.app.dependency_overrides, "authority reads must exercise original routing")
        try:
            response = await self.client.get(f"/api/hitl/project/{project_id}")
        finally:
            self.assertEqual(self._snapshot(), before, "GET must not alter rows/schema, migrate DBs or create backups")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def _assert_pending(self, rows, project_id, expected):
        self.assertEqual([(r["id"], r["payload"]["fixture_origin"]) for r in rows], expected)
        for row in rows:
            self.assertEqual(row["project_id"], project_id)
            self.assertEqual(row["kind"], "approve_final")
            self.assertEqual(row["decision"], "pending")
            self.assertIsNone(row["decided_at"])
            self.assertEqual(row["payload"], {
                "step": "final", "synthetic_only": True,
                "fixture_origin": row["payload"]["fixture_origin"],
                "video_path": f"/synthetic-inputs/project-{project_id}/{row['payload']['fixture_origin']}.mp4",
            })

    async def test_worker_project_history_wins_over_master_same_id_and_foreign_rows(self):
        rows = await self._get(7)
        self._assert_pending(rows, 7, [
            (74, "worker-project-target-newer"),
            (71, "worker-project-target-older"),
        ])

    async def test_empty_existing_project_db_does_not_use_stale_master_history(self):
        async with self.sessions() as session:
            session.add(self._project(29))
            await session.flush()
            session.add(self._request(101, 29, "master-stale-empty-authority"))
            await session.commit()
        await self._project_file(29, owners=(29,), requests=())
        rows = await self._get(29)
        self.assertEqual(rows, [], "existing empty project DB is authoritative; master is not a fallback for missing rows")

    async def test_same_hitl_id_in_two_project_files_stays_with_requested_owner(self):
        foreign = await self._get(8)
        self._assert_pending(foreign, 8, [(71, "foreign-project-colliding-id")])
        target = await self._get(7)
        self._assert_pending(target, 7, [
            (74, "worker-project-target-newer"),
            (71, "worker-project-target-older"),
        ])
        self.assertNotEqual(foreign[0]["payload"], target[-1]["payload"])

    async def test_master_only_project_fallback_does_not_create_project_db(self):
        missing_dir = settings.data_dir / "videos" / "synthetic-hitl-17"
        self.assertFalse(missing_dir.exists())
        rows = await self._get(17)
        self._assert_pending(rows, 17, [(91, "master-only-pending")])
        self.assertFalse(missing_dir.exists(), "read-only fallback must not initialize or migrate project storage")

    async def test_existing_master_project_without_data_directory_can_still_be_read(self):
        with patch.object(Project, "data_dir", property(lambda _project: None)):
            rows = await self._get(17)
        self._assert_pending(rows, 17, [(91, "master-only-pending")])

    async def test_unknown_project_preserves_existing_empty_get_behavior(self):
        rows = await self._get(999)
        self.assertEqual(rows, [], "legacy GET for a nonexistent project returns 200 []")

    async def test_stale_directory_registration_does_not_authorize_missing_master_project(self):
        orphan = await self._project_file(
            42, owners=(42,), requests=((71, 42, "orphan-project-colliding-id"),),
        )
        project_db.register_project_data_dir(42, orphan)
        rows = await self._get(42)
        self.assertEqual(rows, [], "cached foreign/orphan storage cannot expose pending requests for an unknown master project")

    async def test_explicit_master_dependency_override_remains_compatible(self):
        entered = []

        async def master_fixture():
            async with self.sessions() as session:
                entered.append(str(session.bind.url.database))
                yield session

        before = self._snapshot()
        self.app.dependency_overrides[deps.get_session] = master_fixture
        try:
            response = await self.client.get("/api/hitl/project/7")
        finally:
            self.app.dependency_overrides.clear()
            self.assertEqual(self._snapshot(), before, "override compatibility GET must remain read-only")
        self.assertEqual(response.status_code, 200, response.text)
        self._assert_pending(response.json(), 7, [(71, "master-stale-target")])
        self.assertEqual(entered, [str(self.master_path)])

    async def _guard_master_fixture(self, project_id, *, slug=None):
        project = self._project(project_id)
        if slug is not None:
            project.slug = slug
        async with self.sessions() as session:
            session.add(project)
            await session.flush()
            session.add(self._request(101, project_id, "guard-stale-master"))
            await session.commit()
        return project

    async def _guard_external_file_fixture(self, folder, project):
        self.assertTrue(folder.resolve().is_relative_to(self.root.resolve()))
        engine = await project_db.get_project_engine(folder)
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        sessions = await project_db.get_project_sessionmaker(folder)
        async with sessions() as session:
            owner = self._project(project.id)
            owner.slug = project.slug
            session.add(owner)
            await session.flush()
            session.add(self._request(71, project.id, "blocked-external-pending"))
            await session.commit()
        return folder / "project.db"

    def _guard_snapshot(self, unreadable):
        unreadable = set(unreadable)
        inventory = {}
        logical = {}
        for path in sorted(self.root.rglob("*")):
            if path.name.endswith(("-wal", "-shm")):
                continue
            key = str(path.relative_to(self.root))
            if path.is_symlink():
                inventory[key] = ("symlink", os.readlink(path))
            elif path.is_dir():
                inventory[key] = ("directory",)
            elif path.is_file():
                inventory[key] = ("file", hashlib.sha256(path.read_bytes()).hexdigest())
                if path.suffix == ".db" and path not in unreadable:
                    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as connection:
                        logical[key] = tuple(connection.iterdump())
        return {"inventory": inventory, "logical": logical}

    async def _guarded_get(self, project_id, status, *, unreadable=(), read_folder=None):
        before = self._guard_snapshot(unreadable)
        self.assertFalse(self.app.dependency_overrides)
        with ExitStack() as guards:
            factory = guards.enter_context(patch.object(
                hitl, "get_project_sessionmaker", wraps=project_db.get_project_sessionmaker))
            forbidden = [guards.enter_context(patch.object(project_db, name, wraps=getattr(project_db, name)))
                         for name in ("init_project_db", "migrate_project_by_id", "migrate_single_project_data",
                                      "pull_master_runtime_into_project", "sync_project_row_to_project_db")]
            try:
                response = await self.client.get(f"/api/hitl/project/{project_id}")
            finally:
                self.assertEqual(self._guard_snapshot(unreadable), before,
                                 "blocked GET must not create storage, replace links, repair corrupt bytes or write data/schema")
                for operation in forbidden:
                    operation.assert_not_called()
                if read_folder is None:
                    factory.assert_not_called()
                else:
                    factory.assert_awaited_once_with(read_folder.resolve())
        self.assertEqual(response.status_code, status, response.text)
        self.assertIsInstance(response.json().get("detail"), str)

    async def test_project_folder_outside_data_dir_is_rejected_without_open_or_master_fallback(self):
        project = await self._guard_master_fixture(31, slug="../../outside-data")
        outside = project.data_dir.resolve()
        self.assertFalse(outside.is_relative_to(settings.data_dir.resolve()))
        await self._guard_external_file_fixture(outside, project)
        await self._guarded_get(31, 400)

    async def test_existing_project_db_symlink_outside_data_dir_is_rejected_without_open(self):
        project = await self._guard_master_fixture(32)
        external = await self._guard_external_file_fixture(self.root / "external-db", project)
        folder = project.data_dir
        folder.mkdir(parents=True)
        candidate = folder / "project.db"
        candidate.symlink_to(external)
        self.assertTrue(candidate.is_file())
        self.assertFalse(candidate.resolve().is_relative_to(settings.data_dir.resolve()))
        await self._guarded_get(32, 400)
        self.assertTrue(candidate.is_symlink())
        self.assertEqual(candidate.resolve(), external.resolve())

    async def test_dangling_project_db_is_rejected_without_initialization_or_master_fallback(self):
        folder = settings.data_dir / "videos" / "synthetic-hitl-17"
        folder.mkdir(parents=True)
        candidate = folder / "project.db"
        target = settings.data_dir / "uninitialized-target" / "missing.db"
        candidate.symlink_to(target)
        self.assertTrue(candidate.is_symlink())
        self.assertFalse(candidate.exists())
        self.assertFalse(target.parent.exists())
        await self._guarded_get(17, 503, unreadable=(candidate,))
        self.assertTrue(candidate.is_symlink())
        self.assertFalse(target.parent.exists(), "GET must not create a dangling link's target directory")
        self.assertFalse(target.exists())

    async def test_corrupt_existing_project_db_is_rejected_without_repair_or_master_fallback(self):
        folder = settings.data_dir / "videos" / "synthetic-hitl-17"
        folder.mkdir(parents=True)
        candidate = folder / "project.db"
        corrupt = b"explicit synthetic corrupt SQLite fixture\x00\xff\x00"
        candidate.write_bytes(corrupt)
        self.assertTrue(candidate.is_file())
        await self._guarded_get(17, 503, unreadable=(candidate,), read_folder=folder)
        self.assertEqual(candidate.read_bytes(), corrupt)


if __name__ == '__main__':
    unittest.main(verbosity=2)
