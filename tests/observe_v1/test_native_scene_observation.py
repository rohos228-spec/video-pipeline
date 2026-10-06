"""Tests first for journaling the ORIGINAL native camera/assembler effects.

Only this new test file is owned here. Parent captures Docker RED/GREEN and
owns all production hooks. No worker fixture, capability, admission, recovery,
provider, executor, parser, validator or business writer is replaced here.
All application/SQLAlchemy imports are deferred until parent execution.

Proposed async APIs in the existing app.services.scene_contracts module:
  record_scene_input(project, node, frames, *, cells=())
  record_scene_parse(reply, parsed, *, node, validation_performed,
                     problems=(), error=None, origin='model')
  observe_scene_result(session, project, node, *, agent_slice=None, payload=None)

Input is captured AFTER native camera DB-v2 backfill, or AFTER assembler
camera_expand, before their writes. Parse receives the actual native result,
check outcome and exception; it does not invent validation or run apply_ops.
Unvalidated chunks remain unknown. Local assembly has no model request ID.
Result independently reads project-scoped committed rows and delegates to
verify_scene_agent/verify_scene_assembly. Missing input/body evidence remains
unknown even when output is filled. The journal never flushes, commits or
rolls back the native business session, repairs output, approves HITL or
changes execution authority. Its own journal uses the existing observer.

This suite establishes bounded journal contracts, not worker/API acceptance,
managed eligibility, full fanout, transaction provenance or artistic quality.
"""

from __future__ import annotations
from app.services import revisions

import asyncio
from contextlib import contextmanager
from copy import deepcopy
from importlib import import_module
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch


EXECUTION = "00000000-0000-4000-8000-000000000031"
ATTEMPT = "00000000-0000-4000-8000-000000000032"
REQUEST_A = "00000000-0000-4000-8000-000000000033"
REQUEST_B = "00000000-0000-4000-8000-000000000034"
REQUEST_RACE = "00000000-0000-4000-8000-000000000039"
CAMERA = {"id": "native-camera", "type": "excel_gpt", "data": {"sd_agent": "camera"}}
ASSEMBLER = {"id": "native-assemble", "type": "excel_gpt", "data": {"sd_agent": "assemble"}}
OUTSIDE = {"id": "outside-scene", "type": "excel_gpt", "data": {"sd_agent": "world"}}
VO = ("Альфа открывает книгу.", "Бета закрывает книгу.")


def camera_payload():
    return {"shot_plan": [{
        "id_scene": "sc01", "цитата": text, "крупность": "средний",
        "особенность_сцены": "Деталь объекта", "тип_стыка": "action",
        "композиция": "Книга в центре", "движение": "push-in",
    } for text in VO]}


def assembly_payload(frames):
    from app.services.scene_design.context_builder import frame_seconds

    return {
        # Real cards keep the original writer out of disk/checkpoint fallback.
        "characters": [{"id": "c01", "имя": "Альфа", "внешность": "синтетическая"}],
        "scenes": [{"id_scene": "sc01", "start_words": "Альфа открывает",
                    "end_words": "закрывает книгу.",
                    "время_сек": round(sum(frame_seconds(frame)[0] for frame in frames), 1)}],
        "ops": [{"frame_uuid": frame.uuid, "fields": {
            "id_scene": "sc01", "место": "читальный зал",
            "действие": "открывает книгу" if frame.number == 1 else "закрывает книгу",
        }} for frame in frames],
        "report": "Synthetic native journal fixture",
    }


class NativeSceneObservation(unittest.IsolatedAsyncioTestCase):
    """Own native SQLite fixture; no Excel/actions test inheritance."""

    async def asyncSetUp(self):
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.models import Base, Frame, FrameText, HITLDecision, HITLKind, HITLRequest, NodeRun, NodeRunStatus, Project, ProjectStatus, Scene, Workflow, WorkflowRun
        from app.services import observation
        from app.services.actions_v1 import ActionExecutionControl, ActionRecord, get_permission
        from app.services.node_capabilities import capability_for
        from app.settings import settings

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        settings_patch = patch.object(settings, "data_dir", root)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.database = root / "videos" / "native-scene-fixture" / "project.db"
        self.database.parent.mkdir(parents=True)
        self.master_engine = create_async_engine(f"sqlite+aiosqlite:///{root / 'master.db'}")
        self.business_engine = create_async_engine(f"sqlite+aiosqlite:///{self.database}")
        self.addAsyncCleanup(self.master_engine.dispose)
        self.addAsyncCleanup(self.business_engine.dispose)
        self.sessions = async_sessionmaker(self.master_engine, expire_on_commit=False)
        self.business_sessions = async_sessionmaker(self.business_engine, expire_on_commit=False)
        for engine in (self.master_engine, self.business_engine):
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                await observation.migrate_observation_schema(connection)
        self.nodes = deepcopy([CAMERA, ASSEMBLER, OUTSIDE])
        async with self.sessions() as seed:
            seed.add(Project(id=7, slug="native-scene-fixture", topic="synthetic",
                             status=ProjectStatus.scene_designing, meta={}))
            seed.add(Workflow(id=9, name="native scene journal", nodes=self.nodes, edges=[]))
            seed.add(WorkflowRun(id=11, workflow_id=9, project_id=7,
                                 nodes_snapshot=self.nodes, edges_snapshot=[]))
            seed.add_all([NodeRun(workflow_run_id=11, node_key=node["id"], node_type=typ,
                                  status=NodeRunStatus.pending)
                          for node, typ in zip(self.nodes, ("sd_agent", "sd_assemble", "sd_agent"))])
            self.scope = observation.ExecutionControl(
                execution_id=EXECUTION, project_id=7, workflow_run_id=11,
                node_keys=[CAMERA["id"], ASSEMBLER["id"]],
                graph_version=revisions.graph_version(self.nodes, []),
                mode="observe", observer_state="active", cursors={}, gaps=[])
            seed.add(self.scope)
            await seed.commit()
        async with self.business_sessions() as seed:
            seed.add_all([
                Project(id=7, slug="native-scene-fixture", topic="synthetic",
                        status=ProjectStatus.scene_designing,
                        script_text="Stale script; native Frame voiceover is authoritative.",
                        meta={"scene_design_enabled": True,
                              "canvas_graph": {"nodes": self.nodes, "edges": []}}),
                Project(id=8, slug="foreign-scene-fixture", topic="outside scope", meta={}),
                Scene(id=31, project_id=7, sort_key=1.0, title="Original DB-v2 container"),
            ])
            await seed.flush()
            for number, text in enumerate(VO, 1):
                frame = Frame(id=number, project_id=7, number=number, sort_key=float(number),
                              uuid=f"{number:024x}", scene_id=31, voiceover_text=text,
                              duration_seconds=99.0, attrs={"existing_note": {"keep": [number]}})
                seed.add(frame)
                seed.add(FrameText(project_id=7, frame_id=number, kind="voiceover", text=text))
            seed.add(Frame(id=8, project_id=8, number=1, uuid="e" * 24,
                           voiceover_text="Foreign input.", attrs={}))
            # This is an existing native pending approval input, not a scene
            # approval invented by the journal or a manufactured decision.
            seed.add(HITLRequest(project_id=7, kind=HITLKind.approve_script,
                                 decision=HITLDecision.pending, payload={"fixture": "preserve"}))
            await seed.commit()
        self.saved_observer = observation._observer
        self.observer = observation.Observer(self.sessions, body_root=root / "bodies")
        self.observer.scopes[11] = self.scope
        observation._observer = self.observer
        self.addCleanup(setattr, observation, "_observer", self.saved_observer)
        self.capabilities = {typ: capability_for(typ) for typ in ("sd_agent", "sd_assemble")}
        self.assertIsNone(get_permission(7), "Scene fixture never installs managed permission")

    def api(self, name):
        module = import_module("app.services.scene_contracts")
        function = getattr(module, name, None)
        self.assertTrue(callable(function), "missing native scene journal seam: " + name)
        return function

    async def inputs(self, session, *, project_id=7):
        from sqlalchemy import select
        from app.models import Frame, Project, SceneDesignCell

        project = await session.get(Project, project_id)
        frames = list((await session.scalars(select(Frame).where(Frame.project_id == project_id)
                      .order_by(Frame.sort_key, Frame.number))).all())
        cells = list((await session.scalars(select(SceneDesignCell).where(SceneDesignCell.project_id == project_id)
                     .order_by(SceneDesignCell.id))).all())
        return project, frames, cells

    async def state(self):
        from sqlalchemy import select
        from app.models import Entity, Frame, FrameText, HITLRequest, Project, Scene, SceneDesignCell

        async with self.business_sessions() as reader:
            return {model.__tablename__: [dict(row) for row in
                    (await reader.execute(select(model.__table__).order_by(model.id))).mappings().all()]
                    for model in (Project, Frame, FrameText, Scene, SceneDesignCell, Entity, HITLRequest)}

    @contextmanager
    def context(self, session, *, node=CAMERA, completed=None, attempt_id=ATTEMPT):
        from app.services import observation

        ctx = observation.AttemptContext(self.observer, self.scope, session,
                                         "project", node["id"], attempt_id)
        ctx.last_request_id = REQUEST_RACE
        attempt_token = observation._attempt.set(ctx)
        request_token = observation._request.set(None)
        completed_token = observation._completed_model_request.set((ctx, completed) if completed else None)
        try:
            yield ctx
        finally:
            observation._completed_model_request.reset(completed_token)
            observation._request.reset(request_token)
            observation._attempt.reset(attempt_token)

    @contextmanager
    def business_sql(self):
        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        statements = []

        def capture(connection, cursor, statement, parameters, context, executemany):
            if Path(str(connection.engine.url.database)).resolve() == self.database.resolve():
                statements.append(statement)

        event.listen(Engine, "before_cursor_execute", capture)
        try:
            yield statements
        finally:
            event.remove(Engine, "before_cursor_execute", capture)

    def no_business_writes(self, statements):
        writes = [sql for sql in statements if sql.lstrip().split(None, 1)[0].upper()
                  in {"INSERT", "UPDATE", "DELETE", "REPLACE", "CREATE", "ALTER", "DROP"}]
        self.assertEqual(writes, [], "Journal must not write or autoflush native business rows")

    async def facts(self):
        from sqlalchemy import select
        from app.services import observation

        await self.observer.consume(EXECUTION, "master", self.sessions)
        await self.observer.consume(EXECUTION, "project", self.business_sessions)
        async with self.sessions() as reader:
            return list((await reader.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all())

    def one(self, facts, kind, *, node=CAMERA):
        found = [fact for fact in facts if fact.kind == kind and fact.node_key == node["id"]]
        self.assertEqual(len(found), 1, (kind, [(fact.kind, fact.node_key, fact.payload) for fact in facts]))
        fact = found[0]
        self.assertEqual((fact.execution_id, fact.project_id, fact.workflow_run_id, fact.attempt_id),
                         (EXECUTION, 7, 11, ATTEMPT))
        return fact

    def body(self, fact):
        reference = fact.payload["body"]
        self.assertNotIn("gap", reference)
        path = (self.observer.body_root / reference["path"]).resolve()
        self.assertTrue(path.is_relative_to(self.observer.body_root.resolve()))
        return json.loads(path.read_text(encoding="utf-8"))

    def unclaimed(self, proof):
        for key in ("business_commit_claim", "managed_execution_verified",
                    "semantic_quality_verified", "human_approval_verified"):
            self.assertIs(proof[key], False, proof)
        self.assertIs(proof["human_approval_preserved"], True)

    async def unchanged_authority(self):
        from sqlalchemy import select
        from app.models import NodeRun, NodeRunStatus, Project, ProjectStatus
        from app.services.actions_v1 import ActionExecutionControl, ActionRecord, get_permission
        from app.services.node_capabilities import capability_for
        from app.services.observation import ExecutionControl

        self.assertIsNone(get_permission(7))
        for typ, original in self.capabilities.items():
            self.assertEqual(capability_for(typ), original)
        async with self.sessions() as reader:
            self.assertEqual((await reader.get(ExecutionControl, EXECUTION)).mode, "observe")
            self.assertIsNone(await reader.get(ActionExecutionControl, EXECUTION))
            self.assertEqual(list((await reader.scalars(select(ActionRecord))).all()), [])
            self.assertEqual((await reader.get(Project, 7)).status, ProjectStatus.scene_designing)
            states = {node.node_key: node.status for node in (await reader.scalars(select(NodeRun))).all()}
            self.assertEqual(states, {node["id"]: NodeRunStatus.pending for node in self.nodes})

    def parsed_camera(self, value=None, *, validate=True):
        from app.services.scene_design.agents import parse_agent_slice

        reply = json.dumps(camera_payload() if value is None else value, ensure_ascii=False)
        return reply, parse_agent_slice("camera", reply, validate=validate)

    async def store_camera(self, session, project, frames, parsed):
        from app.services.scene_design import cells, context_builder

        converted = cells.slice_to_cells(project, "camera", parsed,
                                         context_builder.full_voiceover(project, frames))
        stats = await cells.store_cells(session, project, "camera", converted, chunk_size=2)
        self.assertEqual(stats["stored"], len(converted))
        return stats

    async def test_public_journal_seams_are_present(self):
        for name in ("record_scene_input", "record_scene_parse", "observe_scene_result"):
            with self.subTest(api=name):
                self.api(name)

    async def test_native_fixture_parser_validator_and_routes_are_calibrated(self):
        from app.services.scene_contracts import scene_contract
        from app.services.scene_design.agents import parse_assembler_payload
        from app.services.scene_design.assembler import validate_payload
        from app.services.scene_design.context_builder import full_voiceover

        reply, parsed = self.parsed_camera()
        self.assertEqual(parsed, json.loads(reply))
        async with self.business_sessions() as business:
            project, frames, _ = await self.inputs(business)
            payload = parse_assembler_payload(json.dumps(assembly_payload(frames), ensure_ascii=False))
            self.assertEqual(validate_payload(project, frames, payload, full_voiceover(project, frames)), [])
        self.assertEqual(scene_contract(CAMERA)["step_code"], "scene_d")
        self.assertEqual(scene_contract(ASSEMBLER)["step_code"], "scene_asm")
        await self.unchanged_authority()

    async def test_default_off_has_no_sql_body_event_or_input_access(self):
        from app.services import observation

        record_input, record_parse, result = (self.api(name) for name in
            ("record_scene_input", "record_scene_parse", "observe_scene_result"))
        before = await self.state()
        self.assertIsNone(observation._attempt.get())
        with self.business_sql() as statements:
            await record_input(None, None, None, cells=None)
            await record_parse(None, None, node=None, validation_performed=False)
            await result(None, None, None)
        self.assertEqual(statements, [])
        self.assertEqual(await self.facts(), [])
        self.assertEqual(list(self.observer.body_root.glob("**/*.json")), [])
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_stopped_observer_is_noop_before_touching_inputs(self):
        functions = [self.api(name) for name in ("record_scene_input", "record_scene_parse", "observe_scene_result")]
        self.scope.observer_state = "stopped"
        async with self.business_sessions() as business:
            with self.context(business), self.business_sql() as statements:
                await functions[0](None, None, None)
                await functions[1](None, None, node=None, validation_performed=False)
                await functions[2](None, None, None)
        self.assertEqual(statements, [])
        self.assertEqual(await self.facts(), [])
        self.assertEqual(list(self.observer.body_root.glob("**/*.json")), [])

    async def test_foreign_project_or_node_never_reads_or_journals_other_rows(self):
        record_input, record_parse, result = (self.api(name) for name in
            ("record_scene_input", "record_scene_parse", "observe_scene_result"))
        async with self.business_sessions() as business:
            foreign, frames, cells = await self.inputs(business, project_id=8)
            with self.context(business), self.business_sql() as statements:
                await record_input(foreign, CAMERA, frames, cells=cells)
                await result(business, foreign, CAMERA, agent_slice=camera_payload())
                await record_parse("{}", {}, node=ASSEMBLER, validation_performed=False)
            project, frames, cells = await self.inputs(business)
            with self.context(business, node=OUTSIDE), self.business_sql() as outside_sql:
                await record_input(project, OUTSIDE, frames, cells=cells)
                await result(business, project, OUTSIDE)
        self.assertEqual(statements, [])
        self.assertEqual(outside_sql, [])
        self.assertEqual(await self.facts(), [])
        self.assertEqual(list(self.observer.body_root.glob("**/*.json")), [])
        await self.unchanged_authority()

    async def test_input_uses_detached_scene_snapshot_and_native_boundary(self):
        from app.services.scene_contracts import scene_contract, scene_input_snapshot
        from app.services.observation import digest

        record_input = self.api("record_scene_input")
        before = await self.state()
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            expected = scene_input_snapshot(project, frames, cells=cells)
            with self.context(business), self.business_sql() as statements:
                await record_input(project, CAMERA, frames, cells=cells)
            frames[0].attrs["existing_note"]["keep"].append("after capture")
        self.no_business_writes(statements)
        fact = self.one(await self.facts(), "node_input")
        self.assertEqual(fact.payload["contract"], "native_scene_camera")
        self.assertEqual(fact.payload["snapshot_boundary"], scene_contract(CAMERA)["snapshot_boundary"])
        self.assertEqual(fact.payload["input_sha256"], digest(expected))
        self.assertEqual(self.body(fact), expected)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_camera_parse_uses_actual_parser_and_task_local_request(self):
        from app.services.observation import digest

        record_parse = self.api("record_scene_parse")
        reply, parsed = self.parsed_camera()
        original = deepcopy(parsed)
        before = await self.state()
        async with self.business_sessions() as business:
            with self.context(business, completed=REQUEST_A), self.business_sql() as statements:
                await record_parse(reply, parsed, node=CAMERA, validation_performed=True)
        self.assertEqual(parsed, original)
        self.assertEqual(statements, [])
        facts = await self.facts()
        fact = self.one(facts, "parse_result")
        self.assertEqual(fact.request_id, REQUEST_A)
        self.assertEqual(fact.payload["contract"], "native_scene_camera")
        self.assertIs(fact.payload["syntax_valid"], True)
        self.assertIs(fact.payload["schema_valid"], True)
        self.assertEqual(fact.payload["sha256"], digest(parsed))
        self.assertEqual(self.body(fact), {"text": reply, "parsed": parsed})
        check = self.one(facts, "runtime_check")
        self.assertEqual(check.payload["check"], "native_scene_camera_parse")
        self.assertEqual(check.payload["status"], "passed")
        self.assertFalse(check.payload["business_commit_claim"])
        self.assertFalse(any(f.kind in {"apply_input", "data_applied"} for f in facts))
        self.assertEqual(await self.state(), before)

    async def test_native_camera_rejection_is_failed_parse_without_apply(self):
        from app.services.scene_design.agents import SceneDesignAgentError, parse_agent_slice

        record_parse = self.api("record_scene_parse")
        reply = json.dumps({"shot_plan": []})
        with self.assertRaises(SceneDesignAgentError) as caught:
            parse_agent_slice("camera", reply)
        error = caught.exception
        before = await self.state()
        async with self.business_sessions() as business:
            with self.context(business, completed=REQUEST_A):
                await record_parse(reply, None, node=CAMERA, validation_performed=True, error=error)
        facts = await self.facts()
        fact = self.one(facts, "parse_result")
        self.assertIs(fact.payload["syntax_valid"], True)
        self.assertIs(fact.payload["schema_valid"], False)
        self.assertEqual(fact.payload["error_type"], type(error).__name__)
        self.assertEqual(self.one(facts, "runtime_check").payload["status"], "failed")
        self.assertFalse(any(f.kind in {"apply_input", "data_applied"} for f in facts))
        self.assertEqual(await self.state(), before)

    async def test_unvalidated_native_chunk_does_not_claim_passed_schema(self):
        record_parse = self.api("record_scene_parse")
        value = {"shot_plan": [{"цитата": VO[0], "движение": "static"}] * 16}
        reply, parsed = self.parsed_camera(value, validate=False)
        from app.services.scene_design.agents import SceneDesignAgentError, parse_agent_slice

        with self.assertRaises(SceneDesignAgentError):
            parse_agent_slice("camera", reply, validate=True)
        async with self.business_sessions() as business:
            with self.context(business, completed=REQUEST_A):
                await record_parse(reply, parsed, node=CAMERA, validation_performed=False)
        facts = await self.facts()
        fact = self.one(facts, "parse_result")
        self.assertIs(fact.payload["parsed"], True)
        self.assertIsNone(fact.payload["schema_valid"])
        check = self.one(facts, "runtime_check")
        self.assertIs(check.payload["performed"], False)
        self.assertEqual(check.payload["status"], "unknown")

    async def test_parallel_camera_parses_do_not_use_shared_last_request(self):
        from app.services import observation

        record_parse = self.api("record_scene_parse")
        reply, parsed = self.parsed_camera()
        gate = asyncio.Event()
        async with self.business_sessions() as business:
            with self.context(business) as ctx:
                async def parse_one(request_id, wait):
                    token = observation._completed_model_request.set((ctx, request_id))
                    try:
                        if wait:
                            await gate.wait()
                        else:
                            ctx.last_request_id = REQUEST_B
                            gate.set()
                        await record_parse(reply, parsed, node=CAMERA, validation_performed=True)
                    finally:
                        observation._completed_model_request.reset(token)
                await asyncio.gather(parse_one(REQUEST_A, True), parse_one(REQUEST_B, False))
        facts = [f for f in await self.facts() if f.kind == "parse_result"]
        self.assertEqual(len(facts), 2)
        self.assertEqual({f.request_id for f in facts}, {REQUEST_A, REQUEST_B})

    async def test_assembly_parse_distinguishes_extraction_from_actual_coverage_check(self):
        from app.services.scene_design.agents import parse_assembler_payload
        from app.services.scene_design.assembler import validate_payload
        from app.services.scene_design.context_builder import full_voiceover

        record_parse = self.api("record_scene_parse")
        before = await self.state()
        async with self.business_sessions() as business:
            project, frames, _ = await self.inputs(business)
            payload = assembly_payload(frames)
            payload["ops"].pop()
            reply = json.dumps(payload, ensure_ascii=False)
            parsed = parse_assembler_payload(reply)  # Extraction accepts it.
            problems = validate_payload(project, frames, parsed, full_voiceover(project, frames))
            self.assertTrue(problems, "Native validator must detect missing frame coverage")
            original = deepcopy(parsed)
            with self.context(business, node=ASSEMBLER, completed=REQUEST_A), self.business_sql() as statements:
                await record_parse(reply, parsed, node=ASSEMBLER,
                                   validation_performed=True, problems=problems)
            self.assertEqual(parsed, original)
        self.assertEqual(statements, [])
        facts = await self.facts()
        fact = self.one(facts, "parse_result", node=ASSEMBLER)
        self.assertIs(fact.payload["parsed"], True)
        self.assertIs(fact.payload["schema_valid"], False)
        self.assertEqual(fact.payload["validation_problems"], problems)
        self.assertEqual(self.one(facts, "runtime_check", node=ASSEMBLER).payload["status"], "failed")
        self.assertEqual(await self.state(), before)

    async def test_assembly_parse_failures_are_typed_like_the_diagnosis_types_incidents(self):
        record_parse = self.api("record_scene_parse")
        async with self.business_sessions() as business:
            project, frames, _ = await self.inputs(business)
            payload = assembly_payload(frames)
            no_ops = {**payload, "ops": []}
            cases = (("no json at all", None, ValueError("no JSON"), "invalid_json", False),
                     (json.dumps(no_ops, ensure_ascii=False), None, ValueError("empty ops"), "missing_payload", False),
                     (json.dumps(payload, ensure_ascii=False), payload, None, "contract_rejected", True))
            for index, (reply, parsed, error, expected, present) in enumerate(cases):
                problems = ["frame coverage"] if expected == "contract_rejected" else ()
                with self.context(business, node=ASSEMBLER, completed=REQUEST_A, attempt_id=ATTEMPT[:-2] + f"7{index}"):
                    await record_parse(reply, parsed, node=ASSEMBLER, validation_performed=True,
                                       error=error, problems=problems)
        facts = [f for f in await self.facts() if f.kind == "parse_result"]
        self.assertEqual(len(facts), 3)
        for fact, (_, _, _, expected, present) in zip(facts, cases):
            self.assertEqual(fact.payload["contract"], "native_scene_assembly")
            self.assertEqual((fact.payload["failure_class"], fact.payload["payload_required"],
                              fact.payload["payload_present"]), (expected, True, present))
        # a payload that is there but rejected must not look like a missing one to the diagnosis
        self.assertIs(facts[2].payload["parsed"], True)

    async def test_local_assembly_warnings_have_no_invented_model_request(self):
        from app.services.scene_design.assembler import validate_payload
        from app.services.scene_design.context_builder import full_voiceover

        record_parse = self.api("record_scene_parse")
        async with self.business_sessions() as business:
            project, frames, _ = await self.inputs(business)
            payload = assembly_payload(frames)
            payload["scenes"][0]["start_words"] = "Absent native voiceover quote"
            problems = validate_payload(project, frames, payload, full_voiceover(project, frames))
            self.assertTrue(problems)
            with self.context(business, node=ASSEMBLER, completed=REQUEST_RACE):
                await record_parse(None, payload, node=ASSEMBLER, origin="local",
                                   validation_performed=True, problems=problems)
        fact = self.one(await self.facts(), "parse_result", node=ASSEMBLER)
        self.assertIsNone(fact.request_id)
        self.assertIsNone(fact.payload["syntax_valid"])
        self.assertEqual(fact.payload["origin"], "local")
        self.assertIs(fact.payload["schema_valid"], False)
        self.assertEqual(fact.payload["validation_problems"], problems)

    async def test_filled_camera_output_without_captured_input_stays_unknown(self):
        result = self.api("observe_scene_result")
        _, parsed = self.parsed_camera()
        async with self.business_sessions() as business:
            project, frames, _ = await self.inputs(business)
            await self.store_camera(business, project, frames, parsed)
            before = await self.state()
            with self.context(business), self.business_sql() as statements:
                await result(business, project, CAMERA, agent_slice=parsed)
        self.no_business_writes(statements)
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual(proof["status"], "unknown")
        self.unclaimed(proof)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_lost_input_body_keeps_filled_camera_output_unknown(self):
        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        _, parsed = self.parsed_camera()
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            with self.context(business):
                with patch.object(self.observer, "save_body", return_value={"gap": "body_size_limit"}):
                    await record_input(project, CAMERA, frames, cells=cells)
                await self.store_camera(business, project, frames, parsed)
                with self.business_sql() as statements:
                    await result(business, project, CAMERA, agent_slice=parsed)
        self.no_business_writes(statements)
        facts = await self.facts()
        self.assertIn("gap", self.one(facts, "node_input").payload["body"])
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "unknown")
        self.unclaimed(proof)

    async def test_actual_camera_rows_use_shared_verifier_once_without_autoflush(self):
        from app.services import scene_contracts

        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        _, parsed = self.parsed_camera()
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            with self.context(business):
                await record_input(project, CAMERA, frames, cells=cells)
                await self.store_camera(business, project, frames, parsed)
                committed = await self.state()
                frames[0].voiceover_text = "Uncommitted drift; independent reader must ignore it."
                with patch.object(scene_contracts, "verify_scene_agent", wraps=scene_contracts.verify_scene_agent) as verify, self.business_sql() as statements:
                    await result(business, project, CAMERA, agent_slice=parsed)
                    await result(business, project, CAMERA, agent_slice=parsed)
                self.assertEqual(verify.call_count, 1)
                self.assertEqual(verify.call_args.kwargs["agent_slice"], parsed)
        self.no_business_writes(statements)
        facts = await self.facts()
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "passed")
        self.unclaimed(proof)
        output = self.body(self.one(facts, "node_output"))
        self.assertTrue(all(row["project_id"] == 7 for row in output["frames"] + output["cells"]))
        self.assertEqual([row["voiceover_text"] for row in output["frames"]], list(VO))
        self.assertEqual(await self.state(), committed)
        await self.unchanged_authority()

    async def test_rejected_actual_camera_cells_cannot_be_reported_as_passed(self):
        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        value = camera_payload()
        value["shot_plan"][0]["цитата"] = "Absent native quote"
        _, parsed = self.parsed_camera(value)
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            with self.context(business):
                await record_input(project, CAMERA, frames, cells=cells)
                stats = await self.store_camera(business, project, frames, parsed)
                self.assertGreater(stats["rejected"], 0)
                await result(business, project, CAMERA, agent_slice=parsed)
        facts = await self.facts()
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "failed")
        self.assertTrue(any(row["status"] == "rejected" for row in self.body(self.one(facts, "node_output"))["cells"]))
        self.unclaimed(proof)

    async def test_actual_assembly_apply_parameters_and_output_reuse_shared_verifier(self):
        from app.services import db_apply, scene_contracts
        from app.services.scene_design.agents import parse_assembler_payload
        from app.services.scene_design.apply import apply_scene_design
        from app.services.scene_design.assembler import validate_payload
        from app.services.scene_design.context_builder import full_voiceover

        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            payload = parse_assembler_payload(json.dumps(assembly_payload(frames), ensure_ascii=False))
            self.assertEqual(validate_payload(project, frames, payload, full_voiceover(project, frames)), [])
            original = deepcopy(payload)
            with self.context(business, node=ASSEMBLER, completed=REQUEST_A):
                await record_input(project, ASSEMBLER, frames, cells=cells)
                with patch.object(db_apply, "apply_ops", new=AsyncMock(wraps=db_apply.apply_ops)) as apply:
                    applied = await apply_scene_design(business, project, payload)
                self.assertTrue(applied["ok"], applied)
                args, kwargs = apply.await_args
                self.assertIs(args[0], business)
                self.assertIs(args[1], project)
                self.assertEqual(args[2], original["ops"])
                self.assertEqual(kwargs, {"characters": original["characters"],
                                         "scenes": original["scenes"], "export_xlsx": False})
                await business.commit()  # Only the ORIGINAL writer's transaction.
                committed = await self.state()
                with patch.object(scene_contracts, "verify_scene_assembly", wraps=scene_contracts.verify_scene_assembly) as verify, self.business_sql() as statements:
                    await result(business, project, ASSEMBLER, payload=payload)
                self.assertEqual(verify.call_count, 1)
                self.assertEqual(verify.call_args.kwargs["payload"], payload)
        self.no_business_writes(statements)
        facts = await self.facts()
        input_fact = self.one(facts, "node_input", node=ASSEMBLER)
        self.assertEqual(input_fact.payload["contract"], "native_scene_assembly")
        self.assertEqual(input_fact.payload["snapshot_boundary"], scene_contracts.scene_contract(ASSEMBLER)["snapshot_boundary"])
        apply_fact = self.one(facts, "apply_input", node=ASSEMBLER)
        self.assertEqual(self.body(apply_fact), {"ops": original["ops"],
                                               "characters": original["characters"], "scenes": original["scenes"]})
        proof = self.one(facts, "node_result_verification", node=ASSEMBLER).payload
        self.assertEqual(proof["status"], "passed")
        self.unclaimed(proof)
        self.assertFalse(proof["entities_verified"])
        self.assertFalse(proof["scene_space_verified"])
        output = self.body(self.one(facts, "node_output", node=ASSEMBLER))
        self.assertTrue(all(row["scene_id"] == 31 for row in output["frames"]))
        self.assertEqual(output["scene_registry"][0]["id_scene"], "sc01")
        self.assertEqual([row["attrs"]["shot01_action"] for row in output["frames"]],
                         ["открывает книгу", "закрывает книгу"])
        self.assertEqual(await self.state(), committed)
        await self.unchanged_authority()

    async def test_failed_native_session_is_read_independently_without_repair(self):
        from sqlalchemy.exc import IntegrityError
        from app.models import Frame

        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        _, parsed = self.parsed_camera()
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            with self.context(business):
                await record_input(project, CAMERA, frames, cells=cells)
                await self.store_camera(business, project, frames, parsed)
                committed = await self.state()
                business.add(Frame(project_id=7, number=1, uuid="d" * 24, voiceover_text="Duplicate number", attrs={}))
                with self.assertRaises(IntegrityError):
                    await business.flush()
                self.assertFalse(business.is_active)
                with patch.object(business, "rollback", new=AsyncMock(wraps=business.rollback)) as rollback, patch.object(business, "commit", new=AsyncMock(wraps=business.commit)) as commit, patch.object(business, "flush", new=AsyncMock(wraps=business.flush)) as flush, self.business_sql() as statements:
                    await result(business, project, CAMERA, agent_slice=parsed)
                rollback.assert_not_awaited()
                commit.assert_not_awaited()
                flush.assert_not_awaited()
                self.assertFalse(business.is_active)
        self.no_business_writes(statements)
        self.assertTrue(any(sql.lstrip().upper().startswith("SELECT") and "scene_design_cells" in sql for sql in statements))
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual(proof["status"], "passed", "Only previously stored row integrity is checked")
        self.unclaimed(proof)
        self.assertEqual(await self.state(), committed)
        await self.unchanged_authority()

    async def test_camera_input_does_not_become_assembly_input_provenance(self):
        record_input, result = self.api("record_scene_input"), self.api("observe_scene_result")
        async with self.business_sessions() as business:
            project, frames, cells = await self.inputs(business)
            with self.context(business):
                await record_input(project, CAMERA, frames, cells=cells)
            with self.context(business, node=ASSEMBLER):
                await result(business, project, ASSEMBLER, payload=assembly_payload(frames))
        proof = self.one(await self.facts(), "node_result_verification", node=ASSEMBLER).payload
        self.assertEqual(proof["status"], "unknown")
        self.unclaimed(proof)

    async def test_original_camera_runner_records_native_parse_fact(self):
        from app.services.scene_design import runner

        # Unit external reply only. Native prompt, parser and return value stay real.
        reply, expected = self.parsed_camera()
        async with self.business_sessions() as business:
            project, _, _ = await self.inputs(business)
            with self.context(business, completed=REQUEST_A), patch("app.services.gpt_client.gpt_ask_fresh", new=AsyncMock(return_value=reply)) as ask:
                actual = await runner._run_one_agent(project, "camera", "Native fixture context", timeout=30)
            ask.assert_awaited_once()
            self.assertEqual(actual, expected)
        facts = await self.facts()
        fact = self.one(facts, "parse_result")
        self.assertEqual(fact.request_id, REQUEST_A)
        self.assertEqual(fact.payload["contract"], "native_scene_camera")
        self.assertIs(fact.payload["schema_valid"], True)
        self.assertFalse(any(f.kind in {"apply_input", "data_applied"} for f in facts))


if __name__ == "__main__":
    unittest.main()
