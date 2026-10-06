"""Bounded scene-design contracts; parent executes RED/GREEN in offline Docker.

Ownership: this test file, then app/services/scene_contracts.py after captured RED.
No inherited Excel recovery fixture and no replacement worker or provider.

Native worker fixture for the parent's integration:
* Workflow/default WorkflowRun/NodeRun initially pending; marked excel_gpt nodes
  camera -> assemble with matching canvas snapshot and project-scoped permission.
* DB-v2 backfilled Frame/FrameText/Scene rows, scene_design_enabled=True,
  scene_design.only_agent='camera'; original prepare_node_for_step_start and
  advance_runner.advance_project_job dispatch scene_d -> scene_design.run.
* For assemble, original checkpoints for characters/world/action/camera are
  required by runner.agents_all_done. Capture assembly frames AFTER native
  camera_expand, before validate_payload/apply_scene_design. Reuse scene_asm ->
  run_assemble, original harness/HITL/FSM. Never seed done or mock validation.

Managed sd_* capability/admission and observation hooks are parent prerequisites.
This suite verifies native staging/apply effects and a pure read-only helper; it
does not assert a managed worker acceptance without those prerequisites.

Helper API proposed for the parent:
  scene_contract(node)
  scene_input_snapshot(project, frames, *, cells=())
  verify_scene_agent(project, node, cells, *, frames, snapshot, agent_slice)
  verify_scene_assembly(project, frames, *, snapshot, payload)

"passed" proves bounded row/payload integrity only. Commit provenance comes
from the existing observation outbox; HITL and semantic quality remain explicit.
"""

from __future__ import annotations

import copy
import importlib
import unittest
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, Frame, FrameText, Project, Scene, SceneDesignCell
from app.services.excel_gpt_node import effective_node_type, slot_index_from_node
from app.services.scene_design import cells as native_cells
from app.services.scene_design import context_builder
from app.services.scene_design.assembler import validate_payload


CAMERA = {"id": "camera-node", "type": "excel_gpt", "data": {"sd_agent": "camera"}}
ASSEMBLER = {"id": "assemble-node", "type": "excel_gpt", "data": {"sd_agent": "assemble"}}
VO_PARTS = ("Альфа открывает книгу.", "Бета закрывает книгу.")


def helper():
    # Import inside tests: a missing owned helper is the intended RED signal.
    return importlib.import_module("app.services.scene_contracts")


def project_fixture():
    return Project(
        id=7, topic="Synthetic scene contract", slug="scene-contract-fixture",
        script_text="A stale script; frame voiceover is native source of truth.",
        meta={"scene_design_enabled": True},
    )


def frames_fixture():
    return [
        Frame(
            id=number, project_id=7, number=number, uuid=f"{number:024x}",
            sort_key=float(number), scene_id=31, voiceover_text=text,
            duration_seconds=99.0, attrs={"existing_note": "preserve"},
        )
        for number, text in enumerate(VO_PARTS, start=1)
    ]


def camera_slice():
    return {"shot_plan": [
        {
            "id_scene": "sc01", "цитата": text, "крупность": "средний",
            "особенность_сцены": "Деталь объекта", "тип_стыка": "action",
            "композиция": "Книга в центре", "движение": "static",
        }
        for text in VO_PARTS
    ]}


def assembly_payload(frames):
    return {
        "characters": [],
        "scenes": [{
            "id_scene": "sc01", "start_words": "Альфа открывает",
            "end_words": "закрывает книгу.",
            "время_сек": round(sum(context_builder.frame_seconds(f)[0] for f in frames), 1),
        }],
        "ops": [
            {"frame_uuid": f.uuid, "fields": {
                "id_scene": "sc01", "место": "читальный зал",
                "действие": "открывает книгу" if f.number == 1 else "закрывает книгу",
            }}
            for f in frames
        ],
        "report": "Synthetic bounded row integrity fixture",
    }


def applied_frames(frames, payload):
    output = copy.deepcopy(frames)
    by_uuid = {f.uuid: f for f in output}
    for op in payload["ops"]:
        fields = op["fields"]
        by_uuid[op["frame_uuid"]].attrs = {
            **by_uuid[op["frame_uuid"]].attrs,
            "shot01_id_scene": fields["id_scene"], "place": fields["место"],
            "shot01_action": fields["действие"],
        }
    return output


class SceneContracts(unittest.TestCase):
    def setUp(self):
        self.project = project_fixture()
        self.frames = frames_fixture()
        self.slice = camera_slice()
        self.cells = native_cells.slice_to_cells(
            self.project, "camera", self.slice,
            context_builder.full_voiceover(self.project, self.frames),
        )

    def snapshot(self, *, cells=()):
        return helper().scene_input_snapshot(self.project, self.frames, cells=cells)

    def verify_camera(self, *, project=None, cells=None, frames=None,
                      snapshot=None, agent_slice=None):
        return helper().verify_scene_agent(
            project if project is not None else self.project, CAMERA,
            cells if cells is not None else self.cells,
            frames=frames if frames is not None else self.frames,
            snapshot=snapshot if snapshot is not None else self.snapshot(),
            agent_slice=agent_slice if agent_slice is not None else self.slice,
        )

    def assert_bounded_proof(self, result):
        self.assertFalse(result["semantic_quality_verified"])
        self.assertFalse(result["business_commit_claim"])
        self.assertTrue(result["human_approval_preserved"])

    def test_marked_camera_and_assembler_resolve_original_specs_without_excel_slots(self):
        from app.orchestrator.node_registry import SD_AGENT_STEP_CODES, spec_for_node
        from app.services.scene_design.agents import CATEGORY_AGENTS

        self.assertIn("camera", CATEGORY_AGENTS)
        self.assertIn("camera", SD_AGENT_STEP_CODES.values())
        for node, node_type, step_code, executor in (
            (CAMERA, "sd_agent", "scene_d", "scene_design.run"),
            (ASSEMBLER, "sd_assemble", "scene_asm", "scene_design.run_assemble"),
        ):
            with self.subTest(node_type=node_type):
                self.assertEqual(effective_node_type(node), node_type)
                self.assertEqual(slot_index_from_node(node), 0)
                self.assertEqual(spec_for_node(node).step_code, step_code)
                contract = helper().scene_contract(node)
                self.assertTrue(contract["supported"])
                self.assertEqual(contract["node_type"], node_type)
                self.assertEqual(contract["step_code"], step_code)
                self.assertIn(executor, contract["executor"])
                self.assertTrue(contract["source_references"])
                self.assertTrue(contract["human_approval_preserved"])
                self.assertFalse(contract["semantic_quality_verified"])
                self.assertEqual(set(contract["actions"]), {
                    "read_events", "read_contract", "wait", "human_review",
                })

    def test_camera_contract_names_staging_and_assembler_names_actual_apply_fields(self):
        camera = helper().scene_contract(CAMERA)
        assembler = helper().scene_contract(ASSEMBLER)
        self.assertEqual(camera["agent"], "camera")
        self.assertEqual(camera["result_kind"], "scene_design_cells")
        self.assertEqual(assembler["result_kind"], "scene_assembly")
        self.assertIn("SceneDesignCell", str(camera["output_fields"]))
        for actual_field in ("scene_registry", "shot01_id_scene", "shot01_action", "place"):
            self.assertIn(actual_field, str(assembler["output_fields"]))

    def test_legacy_camera_marker_uses_same_bounded_contract(self):
        for node in (
            {"id": "legacy-camera", "type": "excel_gpt", "data": {"agent": "camera"}},
            {"id": "legacy-camera", "type": "sd_agent", "data": {"agent": "camera"}},
        ):
            with self.subTest(node=node):
                contract = helper().scene_contract(node)
                self.assertTrue(contract["supported"])
                self.assertEqual(contract["agent"], "camera")
                self.assertEqual(contract["step_code"], "scene_d")

    def test_unbounded_markers_and_other_excel_nodes_remain_explicit_unknown(self):
        for node in (
            {"id": "ordinary", "type": "excel_gpt", "data": {}},
            {"id": "removed", "type": "excel_gpt", "data": {"sd_agent": "style"}},
            {"id": "invented", "type": "excel_gpt", "data": {"sd_agent": "invented"}},
            {"id": "ambiguous", "type": "sd_agent", "data": {}},
            {"id": "conflicting", "type": "sd_assemble", "data": {"sd_agent": "camera"}},
            {"id": "wrong-route", "type": "plan", "data": {"sd_agent": "camera"}},
        ):
            with self.subTest(node=node):
                contract = helper().scene_contract(node)
                self.assertFalse(contract["supported"])
                self.assertEqual(contract["status"], "unknown")
                self.assertNotIn("retry_same", contract["actions"])
                self.assertNotIn("retry_profile", contract["actions"])
                self.assertNotIn("finish", contract["actions"])

    def test_every_slice_agent_marker_has_its_own_bounded_contract(self):
        for agent in ("skeleton", "characters", "world", "action"):
            node = {"id": agent + "-node", "type": "excel_gpt", "data": {"sd_agent": agent}}
            with self.subTest(agent=agent):
                contract = helper().scene_contract(node)
                self.assertTrue(contract["supported"])
                self.assertEqual((contract["node_type"], contract["agent"], contract["step_code"]),
                                 ("sd_agent", agent, "scene_d"))
                self.assertEqual(contract["result_kind"], "scene_design_cells")
                shape = contract["slice_contract"]
                self.assertEqual(shape["list_key"], __import__(
                    "app.services.scene_design.agents", fromlist=["LIST_KEY"]).LIST_KEY[agent])
                self.assertEqual(set(shape["failure_classes"]), {"invalid_json", "missing_payload",
                                                                  "contract_rejected"})
                self.assertNotIn("retry_same", contract["actions"])

    def test_legacy_scene_design_node_is_observed_as_the_union_and_still_read_only(self):
        contract = helper().scene_contract({"id": "monolith", "type": "scene_design", "data": {}})
        self.assertTrue(contract["supported"] and contract["legacy"])
        self.assertEqual(contract["agent"], "legacy")
        self.assertEqual(set(contract["slice_contracts"]), {"skeleton", "characters", "world", "camera", "action"})
        self.assertNotIn("retry_same", contract["actions"])
        self.assertFalse(contract["managed_execution_verified"])

    def test_valid_camera_staging_is_read_only_and_uses_native_frame_voiceover(self):
        before_slice = copy.deepcopy(self.slice)
        before_attrs = copy.deepcopy([f.attrs for f in self.frames])
        result = self.verify_camera()
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["cell_count"], len(self.cells))
        self.assertEqual(len(result["sha256"]), 64)
        self.assert_bounded_proof(result)
        self.assertEqual(self.slice, before_slice)
        self.assertEqual([f.attrs for f in self.frames], before_attrs)
        self.assertEqual(self.project.meta, {"scene_design_enabled": True})

    def test_camera_missing_input_or_slice_is_unknown_even_with_valid_cells(self):
        for snapshot, agent_slice in ((None, self.slice), ({}, self.slice), (self.snapshot(), None)):
            with self.subTest(snapshot=snapshot, agent_slice=agent_slice):
                result = helper().verify_scene_agent(
                    self.project, CAMERA, self.cells, frames=self.frames,
                    snapshot=snapshot, agent_slice=agent_slice,
                )
                self.assertEqual(result["status"], "unknown")
                self.assert_bounded_proof(result)

    def test_empty_and_rejected_camera_cells_cannot_pass(self):
        bad_slice = camera_slice()
        bad_slice["shot_plan"][0]["цитата"] = "Words absent from native VO"
        rejected = native_cells.slice_to_cells(self.project, "camera", bad_slice,
            context_builder.full_voiceover(self.project, self.frames))
        self.assertTrue(any(c.status == "rejected" for c in rejected))
        for cells, agent_slice in (([], self.slice), (rejected, bad_slice)):
            with self.subTest(cells=len(cells)):
                self.assertEqual(self.verify_camera(cells=cells, agent_slice=agent_slice)["status"], "failed")

    def test_camera_revalidates_persisted_cells_instead_of_trusting_ok_label(self):
        for field, value in (("особенность_сцены", "invented feature"),
                             ("тип_стыка", "teleport"), ("цитата", "missing quote")):
            cells = copy.deepcopy(self.cells)
            target = next(c for c in cells if c.field == field)
            target.value = value
            target.status = "ok"
            with self.subTest(field=field):
                self.assertEqual(self.verify_camera(cells=cells)["status"], "failed")

    def test_camera_rejects_partial_duplicate_cross_project_and_bad_offset_rows(self):
        foreign = copy.deepcopy(self.cells)
        foreign[0].project_id = 8
        bad_offset = copy.deepcopy(self.cells)
        next(c for c in bad_offset if c.field == "цитата").vo_offset = 99999
        for label, cells in (
            ("partial", self.cells[:-1]), ("duplicate", self.cells + [copy.deepcopy(self.cells[0])]),
            ("foreign", foreign), ("quote-offset", bad_offset),
        ):
            with self.subTest(label=label):
                self.assertEqual(self.verify_camera(cells=cells)["status"], "failed")

    def test_camera_requires_target_cells_and_preserves_other_agent_facts(self):
        world = native_cells.slice_to_cells(self.project, "world",
            {"locations": [{"id": "loc01", "name": "зал"}]}, "\n".join(VO_PARTS))
        self.assertTrue(world)
        self.assertEqual(self.verify_camera(cells=world)["status"], "failed")
        snapshot = self.snapshot(cells=world)
        self.assertEqual(self.verify_camera(cells=world + self.cells, snapshot=snapshot)["status"], "passed")
        changed = copy.deepcopy(world)
        changed[0].value = "unauthorized mutation"
        self.assertEqual(self.verify_camera(cells=changed + self.cells, snapshot=snapshot)["status"], "failed")

    def test_camera_cannot_change_frame_voiceover_set_or_production_attrs(self):
        snapshot = self.snapshot()
        for label, frames in (("voiceover", copy.deepcopy(self.frames)),
                              ("attrs", copy.deepcopy(self.frames)),
                              ("frame-set", self.frames[:-1])):
            if label == "voiceover":
                frames[0].voiceover_text = "changed by camera"
            elif label == "attrs":
                frames[0].attrs = {"shot01_action": "unauthorized production write"}
            with self.subTest(label=label):
                self.assertEqual(self.verify_camera(frames=frames, snapshot=snapshot)["status"], "failed")

    def test_disabled_pass_through_and_done_metadata_are_not_output_proof(self):
        project = copy.deepcopy(self.project)
        project.meta = {"scene_design_enabled": False, "scene_design": {"status": "done"}}
        self.assertEqual(self.verify_camera(project=project)["status"], "unknown")
        project.meta = {"scene_design_enabled": True, "scene_design": {"status": "done"}}
        self.assertEqual(self.verify_camera(project=project, cells=[])["status"], "failed")

    def test_camera_cannot_write_production_scene_registry(self):
        snapshot = self.snapshot()
        project = copy.deepcopy(self.project)
        project.meta = {**project.meta, "scene_registry": assembly_payload(self.frames)["scenes"]}
        self.assertEqual(self.verify_camera(project=project, snapshot=snapshot)["status"], "failed")

    def assembly_result(self, *, frames=None, payload=None, snapshot=None):
        payload = payload if payload is not None else assembly_payload(self.frames)
        project = copy.deepcopy(self.project)
        project.meta = {**project.meta, "scene_registry": copy.deepcopy(payload["scenes"])}
        return helper().verify_scene_assembly(
            project, frames if frames is not None else applied_frames(self.frames, payload),
            snapshot=snapshot if snapshot is not None else self.snapshot(), payload=payload,
        )

    def test_assembler_uses_native_validator_and_actual_registry_and_frame_attrs(self):
        payload = assembly_payload(self.frames)
        self.assertEqual(validate_payload(self.project, self.frames, payload,
            context_builder.full_voiceover(self.project, self.frames)), [])
        with patch("app.services.scene_design.assembler.validate_payload", wraps=validate_payload) as native:
            result = self.assembly_result(payload=payload)
        self.assertTrue(native.called, "Reuse the original validator; do not add a second engine")
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["frame_count"], 2)
        self.assertEqual(len(result["sha256"]), 64)
        self.assert_bounded_proof(result)
        # Registry string sc01 is not the actual Scene primary key (31).
        self.assertTrue(all(f.scene_id == 31 for f in self.frames))

    def test_assembler_missing_payload_or_input_remains_unknown(self):
        for snapshot, payload in ((None, assembly_payload(self.frames)), (self.snapshot(), None)):
            with self.subTest(snapshot=snapshot, payload=payload):
                result = helper().verify_scene_assembly(self.project, self.frames,
                    snapshot=snapshot, payload=payload)
                self.assertEqual(result["status"], "unknown")
                self.assert_bounded_proof(result)

    def test_assembler_rejects_native_coverage_quote_and_timing_failures(self):
        good = assembly_payload(self.frames)
        partial, duplicate, bad_quote, bad_time = (copy.deepcopy(good) for _ in range(4))
        partial["ops"].pop()
        duplicate["ops"].append(copy.deepcopy(duplicate["ops"][0]))
        bad_quote["scenes"][0]["start_words"] = "not in full voiceover"
        bad_time["scenes"][0]["время_сек"] = 500.0
        for label, payload in (("coverage", partial), ("duplicate", duplicate),
                               ("quote", bad_quote), ("timing", bad_time)):
            with self.subTest(label=label):
                self.assertEqual(self.assembly_result(payload=payload)["status"], "failed")

    def test_assembler_failures_carry_a_typed_failure_class(self):
        good = assembly_payload(self.frames)
        partial = copy.deepcopy(good)
        partial["ops"].pop()
        for label, payload, expected in (("no object", "not a payload", "invalid_json"),
                                         ("no scenes or ops", {"scenes": [], "ops": []}, "missing_payload"),
                                         ("partial coverage", partial, "contract_rejected")):
            with self.subTest(label=label):
                if isinstance(payload, dict) and payload["scenes"]:
                    result = self.assembly_result(payload=payload, frames=applied_frames(self.frames, good))
                else:
                    result = helper().verify_scene_assembly(self.project, self.frames,
                        snapshot=self.snapshot(), payload=payload)
                self.assertEqual((result["status"], result["failure_class"]), ("failed", expected))
        passed = self.assembly_result(payload=good)
        self.assertEqual((passed["status"], passed["failure_class"]), ("passed", None))

    def test_assembler_detects_valid_payload_not_applied_to_production_rows(self):
        payload = assembly_payload(self.frames)
        for field in ("shot01_id_scene", "place", "shot01_action"):
            frames = applied_frames(self.frames, payload)
            frames[0].attrs = {**frames[0].attrs, field: "unapplied or stale"}
            with self.subTest(field=field):
                self.assertEqual(self.assembly_result(frames=frames, payload=payload)["status"], "failed")
        # The model's valid JSON is insufficient when the DB registry is absent.
        result = helper().verify_scene_assembly(self.project, applied_frames(self.frames, payload),
            snapshot=self.snapshot(), payload=payload)
        self.assertEqual(result["status"], "failed")

    def test_assembler_rejects_foreign_frames_uuid_or_voiceover_drift(self):
        payload = assembly_payload(self.frames)
        for field, value in (("project_id", 8), ("uuid", "f" * 24),
                             ("voiceover_text", "rewritten voiceover")):
            frames = applied_frames(self.frames, payload)
            setattr(frames[0], field, value)
            with self.subTest(field=field):
                self.assertEqual(self.assembly_result(frames=frames, payload=payload)["status"], "failed")


class SliceAgentContracts(unittest.TestCase):
    """skeleton / characters / world / action: slice shape, stored cells, typed failure classes."""

    VALID = {
        "skeleton": {"scenes": [{"id_scene": "scene_01", "кадры": [1, 2]}],
                     "locations_seed": [{"id": "loc01", "якорь": "Альфа открывает"}]},
        "characters": {"characters": [{"id": "c01", "имя": "Альфа", "внешность": "высокий"}]},
        "world": {"locations": [{"id": "loc01", "name": "зал", "описание": "читальный зал"}]},
        "action": {"scenes": [{"id_scene": "scene_01", "start_words": "Альфа открывает",
                               "end_words": "закрывает книгу.", "время_сек": 6,
                               "структура_сцены": "opening", "тип_стыка": "action",
                               "переход_в_сцену": "cut",
                               "цепь_действия": [{"subject": "c01", "action": "открывает книгу", "beat": "setup"}]}]},
    }

    def setUp(self):
        self.project = project_fixture()
        self.frames = frames_fixture()
        self.vo = context_builder.full_voiceover(self.project, self.frames)

    def node(self, agent):
        return {"id": agent + "-node", "type": "excel_gpt", "data": {"sd_agent": agent}}

    def cells(self, agent, data):
        return native_cells.slice_to_cells(self.project, agent, data, self.vo)

    def verify(self, agent, data, *, cells=None, snapshot_cells=(), frames=None):
        snapshot = helper().scene_input_snapshot(self.project, self.frames, cells=snapshot_cells)
        return helper().verify_scene_agent(
            self.project, self.node(agent), self.cells(agent, data) if cells is None else cells,
            frames=frames if frames is not None else self.frames, snapshot=snapshot, agent_slice=data)

    def test_a_valid_slice_with_its_native_cells_passes_for_every_agent(self):
        for agent, data in self.VALID.items():
            with self.subTest(agent=agent):
                result = self.verify(agent, copy.deepcopy(data))
                self.assertEqual(result["status"], "passed", result["errors"])
                self.assertIsNone(result["failure_class"])
                self.assertFalse(result["semantic_quality_verified"])
                self.assertGreater(result["cell_count"], 0)

    def test_an_empty_world_is_a_valid_slice_but_an_empty_characters_list_is_not(self):
        self.assertEqual(self.verify("world", {"locations": []})["status"], "passed")
        failed = self.verify("characters", {"characters": []})
        self.assertEqual((failed["status"], failed["failure_class"]), ("failed", "missing_payload"))

    def test_slice_shape_failures_are_typed_like_the_diagnosis_types_incidents(self):
        shape = helper().slice_shape_failure
        self.assertEqual(shape("characters", "no object")[0], "invalid_json")
        self.assertEqual(shape("characters", {"note": 1})[0], "missing_payload")
        self.assertEqual(shape("characters", {"characters": [], "error": "нет персонажей"})[0], "missing_payload")
        self.assertEqual(shape("action", {"scenes": []})[0], "missing_payload")
        self.assertEqual(shape("characters", {"characters": [{"имя": "x"}]})[0], "contract_rejected")
        self.assertEqual(shape("characters", {"characters": [{"id": "hero"}]})[1], ["bad_id_pattern:hero"])
        self.assertEqual(shape("world", {"locations": [{"id": "loc01"}, {"id": "loc01"}]})[1], ["duplicate_id:loc01"])
        self.assertEqual(shape("skeleton", {"scenes": [{"id_scene": "s1"}]})[1], ["item_missing_кадры:1"])
        self.assertEqual(shape("characters", {"characters": [{"id": "c01"}]}), (None, []))

    def test_reply_failures_are_typed_invalid_json_missing_payload_or_contract_rejected(self):
        classify = helper().classify_slice_failure
        boom = ValueError("native parser said no")
        self.assertIsNone(classify("characters", "anything", None))
        self.assertEqual(classify("characters", "I cannot do that", boom), "invalid_json")
        self.assertEqual(classify("characters", '```json\n{"note": 1}\n```', boom), "missing_payload")
        self.assertEqual(classify("action", '{"scenes": []}', boom), "missing_payload")
        self.assertEqual(classify("characters", '{"characters": [{"id": "c01"}]}', boom), "contract_rejected")
        self.assertEqual(helper().classify_assembly_failure("no json", None, boom), "invalid_json")
        self.assertEqual(helper().classify_assembly_failure('{"scenes": [], "ops": []}', None, boom), "missing_payload")
        self.assertEqual(helper().classify_assembly_failure(None, {"scenes": [{}], "ops": [{}]}, None, ["x"]),
                         "contract_rejected")
        self.assertIsNone(helper().classify_assembly_failure(None, {"scenes": [{}], "ops": [{}]}))

    def test_a_bad_slice_or_tampered_cells_fail_with_the_right_class(self):
        bad_id = {"characters": [{"id": "hero", "имя": "Альфа"}]}
        failed = self.verify("characters", bad_id)
        self.assertEqual((failed["status"], failed["failure_class"]), ("failed", "contract_rejected"))
        good = copy.deepcopy(self.VALID["characters"])
        tampered = self.cells("characters", good)
        tampered[0].value = "unauthorized mutation"
        self.assertEqual(self.verify("characters", good, cells=tampered)["failure_class"], "contract_rejected")
        missing = self.verify("characters", good, cells=[])
        self.assertEqual((missing["status"], missing["failure_class"]), ("failed", "missing_payload"))
        not_object = helper().verify_scene_agent(self.project, self.node("characters"), [], frames=self.frames,
            snapshot=helper().scene_input_snapshot(self.project, self.frames), agent_slice=["not", "an object"])
        self.assertEqual((not_object["status"], not_object["failure_class"]), ("failed", "invalid_json"))

    def test_an_agent_cannot_change_frames_or_other_agents_cells(self):
        world = self.cells("world", self.VALID["world"])
        data = copy.deepcopy(self.VALID["characters"])
        ok = self.verify("characters", data, cells=self.cells("characters", data) + world, snapshot_cells=world)
        self.assertEqual(ok["status"], "passed", ok["errors"])
        changed = copy.deepcopy(world)
        changed[0].value = "rewritten by characters"
        bad = self.verify("characters", data, cells=self.cells("characters", data) + changed, snapshot_cells=world)
        self.assertEqual(bad["status"], "failed")
        self.assertIn("non_characters_cells_changed", bad["errors"])
        frames = copy.deepcopy(self.frames)
        frames[0].voiceover_text = "changed"
        self.assertEqual(self.verify("action", copy.deepcopy(self.VALID["action"]), frames=frames)["status"], "failed")

    def test_legacy_node_verifies_every_agent_that_ran_and_names_the_failing_one(self):
        slices = {"characters": copy.deepcopy(self.VALID["characters"]), "world": copy.deepcopy(self.VALID["world"])}
        cells = self.cells("characters", slices["characters"]) + self.cells("world", slices["world"])
        node = {"id": "monolith", "type": "scene_design", "data": {}}
        snapshot = helper().scene_input_snapshot(self.project, self.frames)
        result = helper().verify_scene_agent(self.project, node, cells, frames=self.frames, snapshot=snapshot,
                                             agent_slice=slices)
        self.assertEqual(result["status"], "passed", result["errors"])
        self.assertEqual(result["agents_checked"], ["characters", "world"])
        broken = copy.deepcopy(cells)
        next(c for c in broken if c.agent == "world").status = "rejected"
        failed = helper().verify_scene_agent(self.project, node, broken, frames=self.frames, snapshot=snapshot,
                                             agent_slice=slices)
        self.assertEqual(failed["status"], "failed")
        self.assertTrue(any(code.startswith("world:") for code in failed["errors"]))


class NativeSceneEffects(unittest.IsolatedAsyncioTestCase):
    """Real original writers against synthetic SQLite; no manufactured status."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as session:
            project = project_fixture()
            session.add(project)
            session.add(Scene(id=31, project_id=7, sort_key=1.0, title="Original DB-v2 container"))
            session.add_all(frames_fixture())
            await session.flush()
            for frame in frames_fixture():
                session.add(FrameText(project_id=7, frame_id=frame.id, kind="voiceover", text=frame.voiceover_text))
            await session.commit()

    async def read_project(self, session):
        project = await session.get(Project, 7)
        frames = list((await session.scalars(select(Frame).where(Frame.project_id == 7)
            .order_by(Frame.sort_key, Frame.number))).all())
        cells = list((await session.scalars(select(SceneDesignCell).where(SceneDesignCell.project_id == 7)
            .order_by(SceneDesignCell.id))).all())
        return project, frames, cells

    async def test_original_camera_store_is_independently_readable_without_production_write(self):
        async with self.sessions() as writer:
            project, frames, _ = await self.read_project(writer)
            snapshot = helper().scene_input_snapshot(project, frames)
            cells = native_cells.slice_to_cells(project, "camera", camera_slice(),
                context_builder.full_voiceover(project, frames))
            stats = await native_cells.store_cells(writer, project, "camera", cells, chunk_size=2)
            self.assertEqual(stats, {"stored": len(cells), "rejected": 0})
        async with self.sessions() as reader:
            project, frames, cells = await self.read_project(reader)
            result = helper().verify_scene_agent(project, CAMERA, cells, frames=frames,
                snapshot=snapshot, agent_slice=camera_slice())
            self.assertEqual(result["status"], "passed")
            self.assertTrue(all(f.attrs == {"existing_note": "preserve"} for f in frames))
            self.assertNotIn("scene_registry", project.meta)
            self.assertEqual(await reader.scalar(select(Scene.id).where(Scene.project_id == 7)), 31)

    async def test_original_rejected_cells_remain_visible_and_never_verify_as_success(self):
        async with self.sessions() as writer:
            project, frames, _ = await self.read_project(writer)
            snapshot = helper().scene_input_snapshot(project, frames)
            bad_slice = camera_slice()
            bad_slice["shot_plan"][0]["тип_стыка"] = "teleport"
            cells = native_cells.slice_to_cells(project, "camera", bad_slice,
                context_builder.full_voiceover(project, frames))
            stats = await native_cells.store_cells(writer, project, "camera", cells)
            self.assertGreater(stats["rejected"], 0)
        async with self.sessions() as reader:
            project, frames, cells = await self.read_project(reader)
            self.assertTrue(any(c.status == "rejected" and c.error for c in cells))
            result = helper().verify_scene_agent(project, CAMERA, cells, frames=frames,
                snapshot=snapshot, agent_slice=bad_slice)
            self.assertEqual(result["status"], "failed")

    async def test_original_assembler_apply_matches_committed_registry_and_alias_fields(self):
        from app.services.scene_design.apply import apply_scene_design

        async with self.sessions() as writer:
            project, frames, _ = await self.read_project(writer)
            snapshot = helper().scene_input_snapshot(project, frames)
            payload = assembly_payload(frames)
            self.assertEqual(validate_payload(project, frames, payload,
                context_builder.full_voiceover(project, frames)), [])
            # Nonempty character cards avoid checkpoint/disk fallback in the
            # original apply function. Entity creation is the original writer.
            payload["characters"] = [{"id": "c01", "имя": "Альфа", "внешность": "синтетическая"}]
            applied = await apply_scene_design(writer, project, payload)
            self.assertTrue(applied["ok"])
            self.assertEqual(applied["updated"], 2)
            await writer.commit()
        async with self.sessions() as reader:
            project, frames, _ = await self.read_project(reader)
            self.assertEqual(project.meta["scene_registry"][0]["id_scene"], "sc01")
            self.assertEqual([f.attrs["shot01_action"] for f in frames], ["открывает книгу", "закрывает книгу"])
            self.assertTrue(all(f.attrs["place"] == "читальный зал" for f in frames))
            self.assertTrue(all(f.scene_id == 31 for f in frames))
            result = helper().verify_scene_assembly(project, frames, snapshot=snapshot, payload=payload)
            self.assertEqual(result["status"], "passed")
            self.assertFalse(result["business_commit_claim"])


if __name__ == "__main__":
    unittest.main()
