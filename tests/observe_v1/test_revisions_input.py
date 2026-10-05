"""input_revision scoping: irrelevant changes keep it, relevant changes move it.

Real project/session objects on a temporary database and a temporary prompts
directory. No provider, no worker, no network.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Project
from app.services import prompt_active_global, prompt_library, revisions
from app.services.actions_recovery import input_revision
from app.settings import settings

NODES = [
    {"id": "n_plan", "type": "plan", "position": {"x": 0, "y": 0}, "data": {"label": "Plan"}},
    {"id": "n_script", "type": "script", "position": {"x": 1, "y": 0}, "data": {"label": "Script"}},
    {"id": "n_gpt", "type": "excel_gpt", "position": {"x": 2, "y": 0}, "data": {"label": "GPT", "slotIndex": 1}},
]
EDGES = [{"id": "e0", "source": "n_plan", "target": "n_script"},
         {"id": "e1", "source": "n_script", "target": "n_gpt"}]


class InputRevisionScope(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.prompts = root / "prompts"
        for folder, name, text in (("01_plan", "default", "plan master"), ("01_plan", "other", "plan alt"),
                                   ("02_script", "default", "script master"),
                                   ("05_excel_gpt", "default", "gpt master"), ("blocks", "b", "block")):
            (self.prompts / folder).mkdir(parents=True, exist_ok=True)
            (self.prompts / folder / f"{name}.md").write_text(text, encoding="utf-8")
        for target in (prompt_library, prompt_active_global):
            patcher = patch.object(target, "PROMPTS_ROOT", self.prompts)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'project.db'}")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        self.nodes, self.edges = copy.deepcopy(NODES), copy.deepcopy(EDGES)
        async with self.sessions() as s:
            s.add(Project(id=7, slug="rev", topic="topic", meta={}))
            await s.commit()
        await self.save_canvas()

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def save_canvas(self, **meta_changes):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            meta = {**(project.meta or {}), **meta_changes,
                    "canvas_graph": {"nodes": copy.deepcopy(self.nodes), "edges": copy.deepcopy(self.edges)}}
            project.meta = meta
            await s.commit()

    async def change_project(self, **columns):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            for key, value in columns.items():
                setattr(project, key, value)
            await s.commit()

    async def rev(self, node_key="n_plan", parts=None):
        async with self.sessions() as s:
            project = await s.get(Project, 7)
            return await input_revision(s, project, node_key, parts=parts)

    def write(self, rel, text):
        (self.prompts / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.prompts / rel).write_text(text, encoding="utf-8")

    # ---- stability ------------------------------------------------------------------
    async def test_same_inputs_give_the_same_revision_and_recorded_parts(self):
        parts = {}
        first = await self.rev(parts=parts)
        self.assertEqual(first, await self.rev())
        self.assertEqual(parts["revision_scheme"], revisions.REVISION_SCHEME)
        self.assertEqual(parts["node_config_revision"],
                         revisions.node_config_revision(self.nodes[0], {}))
        for key in ("structure", "node_config", "prompts", "model_route", "contract", "node_code", "topic"):
            self.assertIn(key, parts)

    # ---- irrelevant changes do not invalidate ---------------------------------------
    async def test_unrelated_prompt_files_do_not_invalidate(self):
        base = await self.rev()
        self.write("01_plan/other.md", "edited alternative of the same step")
        self.write("02_script/default.md", "another node's prompt")
        self.write("05_excel_gpt/default.md", "yet another node's prompt")
        self.write("05_excel_gpt/new_variant.md", "a new file nobody uses")
        self.write("07_animation/default.md", "unrelated step")
        self.assertEqual(base, await self.rev())

    async def test_unrelated_settings_do_not_invalidate(self):
        base = await self.rev()
        for name, value in (("whisper_model", "whisper-x"), ("telegram_owner_chat_id", 12345),
                            ("watchdog_model_request_timeout_s", 777), ("elevenlabs_model_id", "other"),
                            ("action_layer_step_timeout_s", 91), ("worker_max_parallel", 9)):
            with self.subTest(name), patch.object(settings, name, value):
                self.assertEqual(base, await self.rev())

    async def test_other_nodes_configuration_and_cosmetics_do_not_invalidate(self):
        base = await self.rev()
        self.nodes[1]["data"].update(modelId="gpt-other", outputMode="text", promptVariant="v2")
        self.nodes[2]["data"].update(modelId="gpt-third")
        self.nodes[0]["data"]["label"] = "Renamed plan"
        self.nodes[0]["data"]["description"] = "cosmetic"
        self.nodes[0]["position"] = {"x": 500, "y": 500}
        await self.save_canvas(excel_gpt_nodes={"n_gpt": {"outputMode": "text"}},
                               prompt_slot_variants={"n_gpt": {"main": "v3"}}, telegram_note="free text")
        self.assertEqual(base, await self.rev())

    # ---- relevant changes invalidate -------------------------------------------------
    async def test_own_prompt_file_invalidates(self):
        base = await self.rev()
        self.write("01_plan/default.md", "plan master, edited")
        self.assertNotEqual(base, await self.rev())

    async def test_switching_to_another_prompt_variant_invalidates(self):
        base = await self.rev()
        await self.change_project(prompt_overrides={"plan": "other"})
        self.assertNotEqual(base, await self.rev())

    async def test_scoped_text_override_and_step_params_invalidate(self):
        base = await self.rev()
        await self.change_project(gpt_text_overrides={"plan": "explicit text"})
        with_override = await self.rev()
        self.assertNotEqual(base, with_override)
        await self.change_project(gpt_text_overrides={"script": "text of another step"})
        self.assertEqual(base, await self.rev())
        await self.save_canvas(node_step_params={"plan": {"duration_seconds": 60}})
        self.assertNotEqual(base, await self.rev())

    async def test_own_model_override_and_provider_settings_invalidate(self):
        base = await self.rev()
        self.nodes[0]["data"]["modelId"] = "gpt-5.5-vibecode"
        await self.save_canvas()
        own = await self.rev()
        self.assertNotEqual(base, own)
        for name, value in (("gpt_model", "model-b"), ("gpt_timeout_s", 4242), ("gpt_reasoning_effort", "xhigh"),
                            ("gpt_base_url", "http://127.0.0.1:1")):
            with self.subTest(name), patch.object(settings, name, value):
                self.assertNotEqual(own, await self.rev())

    async def test_own_node_options_edges_and_group_invalidate(self):
        base = await self.rev()
        self.nodes[0]["data"]["outputMode"] = "text"
        await self.save_canvas()
        options = await self.rev()
        self.assertNotEqual(base, options)
        self.edges.append({"id": "e9", "source": "n_plan", "target": "n_gpt"})
        await self.save_canvas()
        edges = await self.rev()
        self.assertNotEqual(options, edges)
        self.nodes[2]["data"]["groupId"] = "g1"
        await self.save_canvas()
        self.assertNotEqual(edges, await self.rev())

    async def test_data_inputs_invalidate(self):
        base = await self.rev()
        await self.change_project(topic="another topic")
        self.assertNotEqual(base, await self.rev())

    async def test_block_composition_inputs_only_count_when_blocks_are_in_use(self):
        base = await self.rev()
        self.write("blocks/b.md", "block, edited")
        self.assertEqual(base, await self.rev())
        await self.change_project(prompt_overrides={"use_blocks_v2": True})
        composed = await self.rev()
        self.write("blocks/b.md", "block, edited again")
        self.assertNotEqual(composed, await self.rev())

    # ---- code modules ----------------------------------------------------------------
    def patch_code(self, changed):
        real = revisions._file_hash
        return patch.object(revisions, "_file_hash",
                            side_effect=lambda rel: real(rel) + ("x" if rel in changed else ""))

    async def test_code_list_is_bounded_and_resolved_from_capability_data(self):
        from app.services.node_capabilities import capability_for
        files = revisions.code_files("plan", capability_for("plan"))
        for expected in ("app/orchestrator/steps/make_plan.py", "app/services/xlsx_step_runners.py",
                         "app/services/node_capabilities.py", "app/services/db_apply.py",
                         "app/services/node_write_contract.py", "app/services/gpt_client.py"):
            self.assertIn(expected, files)
        for unrelated in ("app/orchestrator/pipeline.py", "app/orchestrator/steps/enrich_xlsx.py"):
            self.assertNotIn(unrelated, files)
        self.assertLess(len(files), 12)
        self.assertIn("app/orchestrator/steps/enrich_xlsx.py",
                      revisions.code_files("excel_gpt", capability_for("excel_gpt")))

    async def test_executor_and_apply_modules_invalidate_unrelated_code_does_not(self):
        base = await self.rev()
        base_gpt = await self.rev("n_gpt")
        with self.patch_code({"app/orchestrator/pipeline.py", "app/services/telegram_unrelated.py"}):
            self.assertEqual(base, await self.rev())
            self.assertEqual(base_gpt, await self.rev("n_gpt"))
        for module in ("app/orchestrator/steps/make_plan.py", "app/services/xlsx_step_runners.py",
                       "app/services/db_apply.py", "app/services/node_capabilities.py"):
            with self.subTest(module), self.patch_code({module}):
                self.assertNotEqual(base, await self.rev())
        # enrich_xlsx is the executor of excel_gpt only.
        with self.patch_code({"app/orchestrator/steps/enrich_xlsx.py"}):
            self.assertEqual(base, await self.rev())
            self.assertNotEqual(base_gpt, await self.rev("n_gpt"))

    # ---- fail-closed ------------------------------------------------------------------
    async def test_whatever_cannot_be_determined_is_never_equal(self):
        with patch.object(revisions, "_file_hash", side_effect=OSError("unreadable")):
            self.assertNotEqual(await self.rev(), await self.rev())
        with patch.object(revisions, "resolved_prompt_inputs", side_effect=RuntimeError("no prompt")):
            self.assertNotEqual(await self.rev(), await self.rev())
        with patch.object(revisions, "model_route", side_effect=RuntimeError("no route")):
            self.assertNotEqual(await self.rev(), await self.rev())
        self.assertNotEqual(await self.rev("n_missing"), await self.rev("n_missing"))
        parts_a, parts_b = {}, {}
        with patch.object(revisions, "_file_hash", side_effect=OSError("unreadable")):
            await self.rev(parts=parts_a)
            await self.rev(parts=parts_b)
        self.assertNotEqual(parts_a["node_code"], parts_b["node_code"])
        self.assertEqual(parts_a["topic"], parts_b["topic"])

    async def test_missing_prompt_file_is_a_stable_distinct_state(self):
        base = await self.rev()
        (self.prompts / "01_plan" / "default.md").unlink()
        missing = await self.rev()
        self.assertNotEqual(base, missing)
        self.assertEqual(missing, await self.rev())


if __name__ == "__main__":
    unittest.main()
