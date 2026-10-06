"""The code revision of a node covers a whole package directory named by its executor ('pkg:<name>').

sd_agent runs the scene_design/* package (cells, runner, agents, ...): an edit of any file in it must move the
node's code revision, two computations of an unchanged tree must agree, and a package that cannot be resolved is
still fail-closed. Real temporary files; no provider, no network.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project
from app.services import revisions
from app.services.actions_recovery import input_revision
from app.services.node_capabilities import capability_for

PACKAGE = Path(revisions.PROJECT_ROOT) / "app" / "services" / "scene_design"


def _cap(executor):
    return SimpleNamespace(executor=executor)


class PackageResolver(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for rel, text in (("app/services/pkg/__init__.py", ""), ("app/services/pkg/b.py", "b = 1\n"),
                          ("app/services/pkg/a.py", "a = 1\n"), ("app/services/pkg/sub/c.py", "c = 1\n"),
                          ("app/services/pkg/__pycache__/a.cpython-312.pyc.py", "junk"),
                          ("app/services/pkg/notes.txt", "not code"),
                          ("app/services/empty/readme.txt", "no source"),
                          ("app/services/flat.py", "x = 1\n")):
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        patcher = patch.object(revisions, "PROJECT_ROOT", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def files(self, executor="flat.run → pkg:pkg"):
        return revisions.executor_modules(_cap(executor))

    def test_package_expands_to_every_source_file_sorted_and_without_caches(self):
        self.assertEqual(self.files(), [
            "app/services/flat.py", "app/services/pkg/__init__.py", "app/services/pkg/a.py",
            "app/services/pkg/b.py", "app/services/pkg/sub/c.py"])

    def test_the_listing_is_stable_across_calls(self):
        self.assertEqual(self.files(), self.files())

    def test_a_file_edit_changes_exactly_that_files_hash_and_a_new_file_joins_the_list(self):
        def hashes():
            return {rel: revisions._file_hash(rel) for rel in self.files()}
        base = hashes()
        self.assertEqual(base, hashes())
        (self.root / "app/services/pkg/sub/c.py").write_text("c = 2\n", encoding="utf-8")
        edited = hashes()
        self.assertEqual({k for k in base if base[k] != edited[k]}, {"app/services/pkg/sub/c.py"})
        (self.root / "app/services/pkg/d.py").write_text("d = 1\n", encoding="utf-8")
        self.assertIn("app/services/pkg/d.py", hashes())
        (self.root / "app/services/pkg/a.py").unlink()
        self.assertNotIn("app/services/pkg/a.py", hashes())

    def test_unresolved_modules_and_packages_stay_fail_closed(self):
        for executor in ("flat.run → pkg:missing", "flat.run → pkg:empty", "nope.run", "pkg:",
                         "flat.run → pkg:../services", "pkg:a.b"):
            with self.subTest(executor), self.assertRaises(LookupError):
                self.files(executor)
        with self.assertRaises(LookupError):
            self.files("")

    def test_a_plain_module_chunk_still_resolves_as_before(self):
        self.assertEqual(self.files("flat.run"), ["app/services/flat.py"])


class SceneDesignPackageRevision(unittest.IsolatedAsyncioTestCase):
    """sd_agent's real code list and real input revision."""

    def test_sd_agent_code_files_cover_the_whole_scene_design_package(self):
        files = revisions.code_files("sd_agent", capability_for("sd_agent"))
        on_disk = sorted(p.relative_to(revisions.PROJECT_ROOT).as_posix() for p in PACKAGE.rglob("*.py")
                         if "__pycache__" not in p.parts)
        self.assertIn("app/services/scene_design/cells.py", on_disk)
        for rel in on_disk:
            self.assertIn(rel, files)
        for rel in ("app/orchestrator/steps/scene_design.py", "app/services/scene_contracts.py"):
            self.assertIn(rel, files)
        self.assertEqual(files, sorted(files))
        self.assertEqual(files, revisions.code_files("sd_agent", capability_for("sd_agent")))

    def test_other_node_types_do_not_pick_the_package_up(self):
        for node_type in ("plan", "script", "excel_gpt"):
            files = revisions.code_files(node_type, capability_for(node_type))
            self.assertFalse([f for f in files if f.startswith("app/services/scene_design/")], node_type)

    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'project.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        nodes = [{"id": "n_sd", "type": "sd_agent", "position": {"x": 0, "y": 0},
                  "data": {"label": "Characters", "sd_agent": "characters"}}]
        async with self.sessions() as s:
            s.add(Project(id=7, slug="rev", topic="topic",
                          meta={"canvas_graph": {"nodes": copy.deepcopy(nodes), "edges": []}}))
            await s.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def rev(self, parts=None):
        async with self.sessions() as s:
            return await input_revision(s, await s.get(Project, 7), "n_sd", parts=parts)

    def patch_code(self, changed):
        real = revisions._file_hash
        return patch.object(revisions, "_file_hash",
                            side_effect=lambda rel: real(rel) + ("x" if rel in changed else ""))

    async def test_revision_is_stable_and_resolved(self):
        parts = {}
        first = await self.rev(parts=parts)
        self.assertEqual(first, await self.rev())
        again = {}
        await self.rev(parts=again)
        self.assertEqual(parts["node_code"], again["node_code"])

    async def test_an_edit_of_any_package_file_moves_the_revision(self):
        base = await self.rev()
        for name in ("cells.py", "runner.py", "agents.py", "__init__.py"):
            rel = f"app/services/scene_design/{name}"
            with self.subTest(rel), self.patch_code({rel}):
                self.assertNotEqual(base, await self.rev())
        with self.patch_code({"app/services/scene_design/cells.py"}):
            self.assertEqual(await self.rev(), await self.rev())

    async def test_an_unrelated_module_does_not_move_it(self):
        base = await self.rev()
        with self.patch_code({"app/orchestrator/pipeline.py", "app/services/telegram_unrelated.py"}):
            self.assertEqual(base, await self.rev())

    async def test_an_unresolvable_package_makes_the_revision_unstable(self):
        with patch.object(revisions, "_package_files", side_effect=LookupError("gone")):
            self.assertNotEqual(await self.rev(), await self.rev())


if __name__ == "__main__":
    unittest.main()
