"""Bounded, read-only contracts for the ORIGINAL Entity/Excel hero path.

Parent owns Docker RED/GREEN, shared observer/capability wiring, and the actual
worker fixture. This file does not execute an outbound executor or enable hero.
The requested helper is intentionally absent until parent supplies captured RED.

Native route: generate_hero.run -> _load_entity_characters -> _run_excel ->
_generate_one_excel_character -> original GPT/Outsee -> hero_reference Artifact.
Entity/Excel is a batch WITHOUT HITL; legacy hero approves each hero/variation.
GenerationResult.gen_id identifies a native attempt; it is not proof of a
durable provider job, request budget, business commit, or managed admission.

Proposed helper seam (app.services.native_entity_contracts):
  hero_entity_contract()
  hero_entity_input_snapshot(entities, *, project_id)
  async verify_hero_entity_results(artifacts, *, project_id, snapshot, root,
                                  scope, provenance=None)

Snapshots retain raw Entity identities/fields in deterministic native order
and the original parser's characters, augmented only with entity_id and role.
Provenance is an explicit
observation input, never synthesized from Artifact.meta.hero_prompt or a PNG.
Each provenance entry binds scope, input hash, entity/role, Artifact UUID, a
completed native generation request/result, file hash, and reference UUIDs.
Base characters additionally bind the completed GPT request/response and the
cached hero_prompt hash. Reference variations use the original local prompt;
their base-model request is absent, rather than copied from a previous task.

Items are deferred: their executor reads Project.item_descriptions, filters
empty values and renumbers the remainder. Empty items succeed without work;
generation errors raise after reverting to hero_ready. Entity props cannot be
substituted for that different input contract.
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest


PROJECT_ID = 7
SCOPE = {
    "execution_id": "00000000-0000-4000-8000-000000000071",
    "node_key": "native-hero",
    "attempt_id": "00000000-0000-4000-8000-000000000072",
}


def contracts():
    try:
        return import_module("app.services.native_entity_contracts")
    except ModuleNotFoundError as exc:
        if exc.name == "app.services.native_entity_contracts":
            raise AssertionError("missing bounded read-only native hero contracts") from exc
        raise


def entities():
    # Same field names as app.models.Entity, including the native Russian aliases.
    return [
        SimpleNamespace(id=12, project_id=PROJECT_ID, type="character", code="c02",
            name="Mira in a coat", sort_key=2.0,
            attrs={"look": "Same person, older", "clothes": "A blue coat",
                   "char": "Calm", "rules": "Keep c01 identity; c01 again."}),
        SimpleNamespace(id=11, project_id=PROJECT_ID, type="character", code="c01",
            name="Mira", sort_key=1.0,
            attrs={"внешность": "Short dark hair", "одежда": "Work clothes",
                   "характер": "Curious", "правила": "Base character."}),
    ]


def assert_unclaimed(test, proof):
    test.assertIs(proof["semantic_quality_verified"], False, proof)
    test.assertIs(proof["business_commit_claim"], False, proof)
    test.assertIs(proof["managed_eligible"], False, proof)


class NativeHeroInputContract(unittest.TestCase):
    def test_contract_names_the_actual_image_executor_and_keeps_admission_closed(self):
        from app.orchestrator.steps import generate_hero
        from app.services.node_capabilities import READ_ACTIONS, capability_for, require_managed_capability

        contract = contracts().hero_entity_contract()
        self.assertEqual(contract["node_type"], "hero")
        self.assertEqual(contract["artifact_kind"], "hero_reference")
        self.assertEqual(contract["executor"], "app.orchestrator.steps.generate_hero.run")
        self.assertEqual(contract["entity_executor"],
                         "app.orchestrator.steps.generate_hero._run_excel")
        self.assertEqual(contract["generation_executor"],
                         "app.orchestrator.steps.generate_hero._generate_one_excel_character")
        for name in ("run", "_run_excel", "_generate_one_excel_character"):
            self.assertTrue(callable(getattr(generate_hero, name)))
        self.assertTrue(contract["outbound_image_generation"])
        self.assertIn("native_image_generation", contract["phases"])
        self.assertIn("artifact_apply", contract["phases"])
        self.assertFalse(contract["managed"])
        self.assertEqual(tuple(contract["actions"]), READ_ACTIONS)
        self.assertFalse(contract["semantic_quality_verified"])
        self.assertFalse(capability_for("hero").managed)
        with self.assertRaises(ValueError):
            require_managed_capability("hero")

    def test_entity_batch_and_legacy_variation_hitl_are_distinct_native_rules(self):
        from app.orchestrator.steps.generate_hero import _excel_batch_auto

        contract = contracts().hero_entity_contract()
        self.assertTrue(_excel_batch_auto(SimpleNamespace()))
        self.assertFalse(contract["entity_excel_hitl_required"])
        self.assertTrue(contract["legacy_variation_hitl_required"])
        self.assertEqual(contract["input_sources"], ["entity", "xlsx"])
        self.assertEqual(contract["parser"],
                         "app.services.excel_characters.characters_from_entities")

    def test_snapshot_uses_original_parser_fields_identity_and_reference_roles(self):
        from app.services.excel_characters import characters_from_entities

        rows = entities()
        before = deepcopy(rows)
        snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
        self.assertEqual(rows, before)
        self.assertEqual(snapshot["project_id"], PROJECT_ID)
        self.assertEqual(snapshot["source"], "entity")
        self.assertEqual({row["id"] for row in snapshot["entities"]}, {11, 12})
        self.assertEqual({row["code"] for row in snapshot["entities"]}, {"c01", "c02"})
        self.assertEqual(snapshot["characters"], [
            {**ch.to_dict(), "entity_id": entity_id, "role": role}
            for ch, entity_id, role in zip(characters_from_entities(rows), (11, 12),
                                         ("base", "reference_variation"))
        ])
        self.assertEqual(snapshot["characters"][1]["ref_ids"], ["c01"])
        self.assertEqual(snapshot["characters"][0]["look"], "Short dark hair")
        snapshot["entities"][0]["attrs"]["look"] = "Changed copy"
        snapshot["characters"][1]["ref_ids"].append("foreign")
        self.assertEqual(rows, before, "snapshot must detach nested native JSON")

    def test_native_empty_invalid_and_noncharacter_rows_do_not_become_targets(self):
        from app.services.excel_characters import characters_from_entities

        rows = entities() + [
            SimpleNamespace(id=13, project_id=PROJECT_ID, type="character", code="c03",
                            name="", sort_key=3.0, attrs={}),
            SimpleNamespace(id=14, project_id=PROJECT_ID, type="character", code="../bad",
                            name="Invalid identifier", sort_key=4.0, attrs={}),
            SimpleNamespace(id=15, project_id=PROJECT_ID, type="prop", code="p01",
                            name="Optional prop", sort_key=5.0, attrs={}),
        ]
        snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
        self.assertEqual(len(snapshot["entities"]), 5, "retain parser input, including ignored rows")
        self.assertEqual([ch["id"] for ch in snapshot["characters"]],
                         [ch.id for ch in characters_from_entities(rows)])
        self.assertEqual([ch["id"] for ch in snapshot["characters"]], ["c01", "c02"])

    def test_rules_only_is_native_input_and_unknown_reference_tokens_are_not_invented(self):
        rows = entities()
        rows[0].name = ""
        rows[0].attrs = {"rules": "Use c01, c01; ignore unknown c99 and self c02."}
        snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
        self.assertEqual(snapshot["characters"][1]["name"], "")
        self.assertEqual(snapshot["characters"][1]["ref_ids"], ["c01"])
        self.assertEqual(snapshot["characters"][1]["role"], "reference_variation")

    def test_identity_keeps_native_case_unicode_and_sort_order(self):
        from app.services.excel_characters import characters_from_entities

        rows = entities()
        rows[1].code = "Мира-1"
        rows[0].code = "Costume_A"
        rows[0].attrs["rules"] = "Same Мира-1, unknown мира-1."
        snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
        self.assertEqual([ch["id"] for ch in snapshot["characters"]],
                         [ch.id for ch in characters_from_entities(rows)])
        self.assertEqual(snapshot["characters"][1]["ref_ids"], ["Мира-1"])

    def test_snapshot_does_not_depend_on_database_result_iteration_order(self):
        rows = entities()
        forward = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
        reverse = contracts().hero_entity_input_snapshot(list(reversed(rows)), project_id=PROJECT_ID)
        self.assertEqual(forward, reverse)


class HeroResultFixture:
    def prepare_results(self):
        from PIL import Image
        from app.models import ArtifactKind
        from app.services.observation import digest

        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "characters").mkdir()
        self.rows = entities()
        self.snapshot = contracts().hero_entity_input_snapshot(self.rows, project_id=PROJECT_ID)
        self.artifacts = []
        self.provenance = []
        for index, ch in enumerate(self.snapshot["characters"], 1):
            path = self.root / "characters" / (ch["id"] + ".png")
            Image.new("RGB", (128, 72), (index * 40, 60, 100)).save(path, compress_level=0)
            metadata = {"excel_id": ch["id"], "excel_ref_ids": list(ch["ref_ids"]),
                        "excel_prompt_name": None, "excel_used_refs": bool(ch["ref_ids"])}
            prompt = "A character turnaround sheet on white background. " * 8
            if not ch["ref_ids"]:
                metadata["hero_prompt"] = prompt
            artifact = SimpleNamespace(id=100 + index, project_id=PROJECT_ID, frame_id=None,
                kind=ArtifactKind.hero_reference, uuid=f"{index:032x}", path=str(path), meta=metadata)
            self.artifacts.append(artifact)
            # Synthetic observation input for this read-only seam; parent must
            # separately capture it from the actual native executor/worker.
            self.provenance.append({
                **SCOPE, "project_id": PROJECT_ID, "input_sha256": digest(self.snapshot),
                "entity_id": ch["entity_id"], "entity_code": ch["id"], "role": ch["role"],
                "artifact_uuid": artifact.uuid,
                "model": None if ch["ref_ids"] else {
                    "request_id": "00000000-0000-4000-8000-000000000081",
                    "response_id": "synthetic-native-base-response", "complete": True,
                    "sha256": digest(prompt)},
                "generation": {
                    "request_id": f"00000000-0000-4000-8000-{90 + index:012d}",
                    "gen_id": f"00000000-0000-4000-8000-{100 + index:012d}",
                    "file_path": str(path), "sha256": sha256(path.read_bytes()).hexdigest(),
                    "complete": True,
                    "reference_artifact_uuids": [] if not ch["ref_ids"] else [self.artifacts[0].uuid]},
            })

    async def check(self, expected, *, artifacts=None, snapshot=None, provenance=None, scope=None):
        artifacts = self.artifacts if artifacts is None else artifacts
        snapshot = self.snapshot if snapshot is None else snapshot
        provenance = self.provenance if provenance is None else provenance
        scope = SCOPE if scope is None else scope
        before = deepcopy((artifacts, snapshot, provenance, scope))
        files = {str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        proof = await contracts().verify_hero_entity_results(artifacts,
            project_id=PROJECT_ID, snapshot=snapshot, root=self.root,
            scope=scope, provenance=provenance)
        self.assertEqual(proof["status"], expected, proof)
        assert_unclaimed(self, proof)
        self.assertEqual((artifacts, snapshot, provenance, scope), before)
        self.assertEqual({str(p): p.read_bytes() for p in self.root.rglob("*") if p.is_file()}, files)
        return proof


class NativeHeroResultContract(HeroResultFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.prepare_results()

    async def test_positive_binds_native_base_and_reference_outputs_but_does_not_admit_managed(self):
        proof = await self.check("passed")
        self.assertEqual(proof["artifact_count"], 2)
        self.assertEqual(proof["structural_status"], "passed")
        self.assertEqual(proof["provenance_status"], "passed")
        self.assertEqual(len(proof["input_sha256"]), 64)
        self.assertEqual(len(proof["output_sha256"]), 64)
        self.assertNotIn("hero_prompt", self.artifacts[1].meta,
                         "native reference variation has no new GPT prompt")

    async def test_absent_input_and_incomplete_capture_are_explicit_unknown(self):
        candidates = [None, {}, {"project_id": PROJECT_ID, "source": "entity"}]
        for field in ("entities", "characters", "source", "project_id"):
            incomplete = deepcopy(self.snapshot)
            incomplete.pop(field)
            candidates.append(incomplete)
        for snapshot in candidates:
            with self.subTest(snapshot=snapshot):
                proof = await contracts().verify_hero_entity_results(self.artifacts,
                    project_id=PROJECT_ID, snapshot=snapshot, root=self.root,
                    scope=SCOPE, provenance=self.provenance)
                self.assertEqual(proof["status"], "unknown", proof)
                assert_unclaimed(self, proof)

    async def test_xlsx_fallback_cannot_be_proven_by_an_entity_only_capture(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["source"] = "xlsx"
        await self.check("unknown", snapshot=snapshot)

    async def test_empty_entity_source_does_not_invent_a_native_optional_hero_skip(self):
        # Native loader may still read XLSX or fall back to legacy descriptions.
        snapshot = contracts().hero_entity_input_snapshot([], project_id=PROJECT_ID)
        await self.check("unknown", artifacts=[], snapshot=snapshot, provenance=[])

    async def test_missing_foreign_duplicated_and_wrong_kind_results_fail(self):
        cases = ("missing", "empty", "foreign_project", "frame_artifact", "wrong_kind",
                 "foreign_character", "extra_character", "duplicate_uuid", "duplicate_row",
                 "missing_uuid", "missing_excel_id", "duplicate_row_id")
        from app.models import ArtifactKind

        for case in cases:
            with self.subTest(case=case):
                output = deepcopy(self.artifacts)
                if case == "missing":
                    output.pop()
                elif case == "empty":
                    output.clear()
                elif case == "foreign_project":
                    output[0].project_id = 99
                elif case == "frame_artifact":
                    output[0].frame_id = 321
                elif case == "wrong_kind":
                    output[0].kind = ArtifactKind.item_reference
                elif case == "foreign_character":
                    output[0].meta["excel_id"] = "c99"
                elif case == "extra_character":
                    extra = deepcopy(output[0])
                    extra.id, extra.uuid, extra.meta["excel_id"] = 103, "3" * 32, "c99"
                    output.append(extra)
                elif case == "duplicate_uuid":
                    output[1].uuid = output[0].uuid
                elif case == "duplicate_row":
                    output.append(deepcopy(output[0]))
                elif case == "missing_uuid":
                    output[0].uuid = ""
                elif case == "missing_excel_id":
                    output[0].meta.pop("excel_id")
                elif case == "duplicate_row_id":
                    output[1].id = output[0].id
                await self.check("failed", artifacts=output)

    async def test_foreign_or_ambiguous_entity_identities_fail_even_with_readable_pngs(self):
        for case in ("foreign_project", "duplicate_code", "duplicate_id"):
            with self.subTest(case=case):
                rows = entities()
                if case == "foreign_project":
                    rows[0].project_id = 99
                elif case == "duplicate_code":
                    rows[0].code = rows[1].code
                else:
                    rows[0].id = rows[1].id
                snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
                await self.check("failed", snapshot=snapshot)

    async def test_input_roles_are_rechecked_against_original_rules_not_a_caller_label(self):
        snapshot = deepcopy(self.snapshot)
        snapshot["characters"][1]["role"] = "base"
        snapshot["characters"][1]["ref_ids"] = []
        await self.check("failed", snapshot=snapshot)

    async def test_native_reference_metadata_must_match_captured_roles_and_references(self):
        for case in ("lost_ref", "foreign_ref", "duplicate_ref", "false_role", "base_has_refs"):
            with self.subTest(case=case):
                output = deepcopy(self.artifacts)
                if case == "lost_ref":
                    output[1].meta["excel_ref_ids"] = []
                elif case == "foreign_ref":
                    output[1].meta["excel_ref_ids"] = ["c99"]
                elif case == "duplicate_ref":
                    output[1].meta["excel_ref_ids"] = ["c01", "c01"]
                elif case == "false_role":
                    output[1].meta["excel_used_refs"] = False
                else:
                    output[0].meta["excel_used_refs"] = True
                await self.check("failed", artifacts=output)

    async def test_scope_identity_role_input_and_artifact_provenance_cannot_be_borrowed(self):
        changes = {
            "execution_id": "foreign-execution", "node_key": "native-items",
            "attempt_id": "foreign-attempt", "project_id": 99,
            "entity_id": 999, "entity_code": "c99", "role": "base",
            "input_sha256": "0" * 64, "artifact_uuid": "f" * 32,
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                provenance = deepcopy(self.provenance)
                provenance[1][field] = value
                await self.check("failed", provenance=provenance)

    async def test_readable_cache_and_cached_prompt_without_request_result_provenance_are_unknown(self):
        for provenance in ([], [self.provenance[0]]):
            with self.subTest(count=len(provenance)):
                proof = await self.check("unknown", provenance=provenance)
                self.assertEqual(proof["structural_status"], "passed")
                self.assertEqual(proof["provenance_status"], "unknown")
        proof = await contracts().verify_hero_entity_results(self.artifacts,
            project_id=PROJECT_ID, snapshot=self.snapshot, root=self.root,
            scope=SCOPE, provenance=None)
        self.assertEqual(proof["status"], "unknown", proof)
        assert_unclaimed(self, proof)

    async def test_missing_request_response_generation_identity_or_completion_is_unknown(self):
        for section, fields in (("model", ("request_id", "response_id", "complete", "sha256")),
                                ("generation", ("request_id", "gen_id", "complete", "sha256"))):
            for field in fields:
                with self.subTest(section=section, field=field):
                    provenance = deepcopy(self.provenance)
                    provenance[0][section].pop(field)
                    await self.check("unknown", provenance=provenance)
        for section in ("model", "generation"):
            provenance = deepcopy(self.provenance)
            provenance[0][section]["complete"] = False
            await self.check("unknown", provenance=provenance)

    async def test_conflicting_prompt_result_path_or_reference_binding_fails(self):
        changes = ((0, "model", "sha256", "0" * 64),
                   (0, "generation", "file_path", str(self.root / "characters" / "other.png")),
                   (0, "generation", "sha256", "0" * 64),
                   (1, "generation", "reference_artifact_uuids", []),
                   (1, "generation", "reference_artifact_uuids", ["f" * 32]))
        for index, section, field, value in changes:
            with self.subTest(section=section, field=field, value=value):
                provenance = deepcopy(self.provenance)
                provenance[index][section][field] = value
                await self.check("failed", provenance=provenance)

    async def test_reference_variation_does_not_borrow_the_base_model_completion(self):
        provenance = deepcopy(self.provenance)
        provenance[1]["model"] = deepcopy(provenance[0]["model"])
        await self.check("failed", provenance=provenance)

    async def test_duplicate_provenance_or_generation_id_is_not_complete_coverage(self):
        provenance = deepcopy(self.provenance)
        provenance.append(deepcopy(provenance[0]))
        await self.check("failed", provenance=provenance)
        for field in ("request_id", "gen_id"):
            provenance = deepcopy(self.provenance)
            provenance[1]["generation"][field] = provenance[0]["generation"][field]
            await self.check("failed", provenance=provenance)

    async def test_invalid_or_missing_actual_file_is_failed_even_with_recorded_pass_claim(self):
        path = Path(self.artifacts[0].path)
        good = path.read_bytes()
        for content in (b"", b"not a PNG", good[:32]):
            with self.subTest(bytes=len(content)):
                path.write_bytes(content)
                await self.check("failed")
        path.write_bytes(good)
        path.unlink()
        await self.check("failed")

    async def test_changed_readable_image_is_detected_by_actual_hash(self):
        from PIL import Image

        Image.new("RGB", (128, 72), "red").save(self.artifacts[0].path)
        await self.check("failed")

    async def test_path_outside_project_or_missing_path_is_failed_without_touching_it(self):
        for path in (str(self.root.parent / "foreign-hero.png"), "", None):
            with self.subTest(path=path):
                output = deepcopy(self.artifacts)
                output[0].path = path
                await self.check("failed", artifacts=output)

    async def test_regeneration_history_is_allowed_but_latest_native_result_is_verified(self):
        previous = deepcopy(self.artifacts[0])
        previous.id, previous.uuid = 50, "a" * 32
        previous.meta["hero_prompt"] = "An earlier native cached prompt."
        # Native _excel_artifact_for_id orders by descending Artifact.id.
        await self.check("passed", artifacts=[previous, *self.artifacts])
        latest = deepcopy(self.artifacts[0])
        latest.id, latest.uuid = 150, "b" * 32
        # A newer row with no captured request/result cannot borrow old evidence.
        await self.check("unknown", artifacts=[*self.artifacts, latest])


class NativeHeroSQLiteContract(HeroResultFixture, unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.models import Artifact, Base, Entity, Project

        self.prepare_results()
        self.engine = create_async_engine(f"sqlite+aiosqlite:///{self.root / 'synthetic.db'}")
        self.addAsyncCleanup(self.engine.dispose)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        async with self.sessions() as session:
            session.add_all([Project(id=PROJECT_ID, slug="synthetic-hero", topic="synthetic"),
                             Project(id=99, slug="synthetic-foreign", topic="synthetic")])
            session.add_all([Entity(**deepcopy(vars(row))) for row in self.rows])
            session.add_all([Artifact(**deepcopy(vars(row))) for row in self.artifacts])
            session.add(Entity(id=99, project_id=99, type="character", code="c99",
                               name="Foreign character", sort_key=1.0, attrs={}))
            await session.commit()

    async def test_committed_model_rows_match_the_original_entity_loader(self):
        from sqlalchemy import select
        from app.models import Artifact, Entity, HITLRequest, Project
        from app.orchestrator.steps.generate_hero import _load_entity_characters

        async with self.sessions() as session:
            project = await session.get(Project, PROJECT_ID)
            native_characters = await _load_entity_characters(session, project)
            rows = (await session.scalars(select(Entity).where(Entity.project_id == PROJECT_ID,
                Entity.type == "character").order_by(Entity.sort_key, Entity.id))).all()
            artifacts = (await session.scalars(select(Artifact).where(
                Artifact.project_id == PROJECT_ID).order_by(Artifact.id))).all()
            snapshot = contracts().hero_entity_input_snapshot(rows, project_id=PROJECT_ID)
            projected = [{key: value for key, value in ch.items() if key not in {"entity_id", "role"}}
                         for ch in snapshot["characters"]]
            self.assertEqual(projected, [ch.to_dict() for ch in native_characters])
            before = (project.status, deepcopy(project.meta),
                      [(a.id, a.uuid, a.path, deepcopy(a.meta)) for a in artifacts])
            proof = await contracts().verify_hero_entity_results(artifacts,
                project_id=PROJECT_ID, snapshot=snapshot, root=self.root,
                scope=SCOPE, provenance=self.provenance)
            self.assertEqual(proof["status"], "passed", proof)
            assert_unclaimed(self, proof)
            self.assertEqual((project.status, project.meta,
                [(a.id, a.uuid, a.path, a.meta) for a in artifacts]), before)
            self.assertFalse(session.new)
            self.assertFalse(session.dirty)
            self.assertFalse(session.deleted)
            self.assertEqual((await session.scalars(select(HITLRequest))).all(), [])

    async def test_latest_artifact_selection_matches_native_regeneration_lookup(self):
        from sqlalchemy import select
        from app.models import Artifact, Project
        from app.orchestrator.steps.generate_hero import _excel_artifact_for_id

        older = deepcopy(self.artifacts[0])
        older.id, older.uuid = 50, "a" * 32
        async with self.sessions() as session:
            session.add(Artifact(**vars(older)))
            await session.commit()
        async with self.sessions() as session:
            project = await session.get(Project, PROJECT_ID)
            selected = await _excel_artifact_for_id(session, project, "c01")
            self.assertEqual(selected.uuid, self.artifacts[0].uuid)
            output = (await session.scalars(select(Artifact).where(
                Artifact.project_id == PROJECT_ID))).all()
            proof = await contracts().verify_hero_entity_results(output,
                project_id=PROJECT_ID, snapshot=self.snapshot, root=self.root,
                scope=SCOPE, provenance=self.provenance)
            self.assertEqual(proof["status"], "passed", proof)
            assert_unclaimed(self, proof)

    async def test_disk_only_native_cache_is_unknown_and_never_repaired_into_provenance(self):
        from sqlalchemy import delete, select
        from app.models import Artifact, Project
        from app.orchestrator.steps.generate_hero import _excel_ids_with_artifact

        async with self.sessions() as session:
            await session.execute(delete(Artifact).where(Artifact.project_id == PROJECT_ID))
            await session.commit()
        async with self.sessions() as session:
            project = await session.get(Project, PROJECT_ID)
            # Use the native helper's actual data directory in this fixture only.
            from unittest.mock import patch
            with patch.object(Project, "data_dir", new=property(lambda _: self.root)):
                generated = await _excel_ids_with_artifact(session, project)
            self.assertEqual(generated, {"c01", "c02"}, "native disk-cache shortcut is retained")
            proof = await contracts().verify_hero_entity_results([],
                project_id=PROJECT_ID, snapshot=self.snapshot, root=self.root,
                scope=SCOPE, provenance=[])
            self.assertEqual(proof["status"], "unknown", proof)
            assert_unclaimed(self, proof)
            self.assertEqual((await session.scalars(select(Artifact))).all(), [])
            self.assertFalse(session.new, "read-only verification must not recover DB rows")


if __name__ == "__main__":
    if not Path("/.dockerenv").exists():
        raise SystemExit("Parent runs this target only in the isolated Docker container")
    unittest.main(verbosity=2)
