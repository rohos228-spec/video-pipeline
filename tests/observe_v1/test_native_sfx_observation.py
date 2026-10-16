from __future__ import annotations

from contextlib import contextmanager, ExitStack
from copy import deepcopy
import hashlib
from importlib import import_module
import json
import math
from pathlib import Path
import struct
import unittest
from unittest.mock import AsyncMock, patch
import wave


EXECUTION = "00000000-0000-4000-8000-000000000057"
ATTEMPT = "00000000-0000-4000-8000-000000000058"
REQUEST = "00000000-0000-4000-8000-000000000059"
STALE_REQUEST = "00000000-0000-4000-8000-000000000060"
FRAME_FIELDS = ("id", "project_id", "uuid", "number", "sort_key",
                "voiceover_text", "meaning", "duration_seconds", "attrs")
IDENTITY = ("id", "project_id", "uuid", "number")
GENERATION_CASES = {
    "test_generation_input_detaches_selected_native_plan_and_provider",
    "test_original_generation_commits_two_real_wavs_with_frame_provenance",
    "test_committed_wavs_without_captured_input_stay_unknown",
    "test_foreign_file_checkpoint_is_rejected_without_repair",
    "test_unreadable_native_wav_is_failed_read_evidence",
    "test_partial_committed_file_coverage_is_failed_without_status_change",
    "test_sfx_gen_proof_carries_the_shared_media_contract",
    "test_failed_sfx_gen_attempt_never_passes_on_earlier_files",
}


def plan_payload():
    return {"events": [
        {"frame_number": 1, "t_start": 3.512345, "duration": 0.612345,
         "kind": "HIT", "prompt": " light desk hit ", "gain": 0.5, "duck": False},
        {"frame_number": 2, "t_start": 0.512345, "duration": 0.812345,
         "kind": "whoosh", "prompt": "soft paper whoosh", "gain": 0.2, "duck": True},
    ]}


class NativeSfxObservation(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from test_actions_v1_contract import DurableActions
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from app.models import Base, Frame, NodeRun, Project, ProjectStatus
        from app.services import observation
        from app.services.node_status_machine import queue_node_for_start, start_node_running
        from app.settings import settings

        self.fixture_node_type = "sfx_gen" if self._testMethodName in GENERATION_CASES else "sfx_plan"
        self.running = (ProjectStatus.generating_sfx if self.fixture_node_type == "sfx_gen"
                        else ProjectStatus.sfx_planning)
        await DurableActions.asyncSetUp(self)
        self.settings_patches = ExitStack()
        self.settings_patches.enter_context(patch.object(settings, "data_dir", Path(self.tmp.name)))
        self.settings_patches.enter_context(patch.object(settings, "sfx_enabled", True))
        self.settings_patches.enter_context(patch.object(settings, "elevenlabs_api_key", ""))
        self.observer = observation.get_observer()
        async with self.sessions() as master:
            project = await master.get(Project, 7)
            project.status = self.running
            project.auto_mode = False
            project.meta = {"canvas_graph": {"nodes": self.nodes, "edges": []}}
            node = await master.scalar(select(NodeRun).where(NodeRun.node_key == "n1"))
            queue_node_for_start(node, project_id=7, initiator="worker")
            start_node_running(node, project_id=7, initiator="worker")
            scope = await master.get(observation.ExecutionControl, "exec-durable")
            scope.execution_id = EXECUTION
            scope.node_keys = ["n1"]
            await master.commit()
            self.scope = scope
        self.observer.scopes[self.scope.workflow_run_id] = self.scope

        self.directory = Path(self.tmp.name) / "videos" / "actions-fixture"
        self.directory.mkdir(parents=True)
        self.database = self.directory / "project.db"
        self.business_engine = create_async_engine(f"sqlite+aiosqlite:///{self.database}")
        self.business_sessions = async_sessionmaker(self.business_engine, expire_on_commit=False)
        async with self.business_engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            await observation.migrate_observation_schema(connection)
        self.payload = plan_payload()
        async with self.business_sessions() as seed:
            seed.add_all([
                Project(id=7, slug="actions-fixture", topic="synthetic SFX", status=self.running,
                        auto_mode=False, meta={"canvas_graph": {"nodes": self.nodes, "edges": []},
                                               "ai_jobs": {"sfx_plan": deepcopy(self.payload)}}),
                Project(id=8, slug="foreign-sfx-fixture", topic="outside scope", meta={}),
                Frame(project_id=7, number=1, uuid="af" * 16, sort_key=2.0,
                      voiceover_text="Альфа закрывает папку.\n", meaning="Close folder",
                      duration_seconds=3.0, attrs={"place": "desk", "main_action": "close",
                                                 "existing_note": {"keep": ["first"]}}),
                Frame(project_id=7, number=2, uuid="bf" * 16, sort_key=1.5,
                      voiceover_text="Бета открывает папку.  ", meaning="Open folder",
                      duration_seconds=3.0, attrs={"place": "desk", "main_action": "open",
                                                 "existing_note": {"keep": ["second"]}}),
                Frame(project_id=8, number=999, uuid="ef" * 16, sort_key=1.0,
                      voiceover_text="Foreign input", duration_seconds=3.0, attrs={}),
            ])
            await seed.commit()

    async def asyncTearDown(self):
        from test_actions_v1_contract import DurableActions

        try:
            await self.business_engine.dispose()
        finally:
            self.settings_patches.close()
            await DurableActions.asyncTearDown(self)

    def api(self, name):
        function = getattr(import_module("app.services.sfx_plan"), name, None)
        if function is None and name == "record_sfx_generation_input":
            function = getattr(import_module("app.services.sfx_gen"), name, None)
        self.assertTrue(callable(function), "missing approved bounded SFX observation API: " + name)
        return function

    async def inputs(self, session):
        from sqlalchemy import select
        from app.models import Frame, Project

        project = await session.get(Project, 7)
        frames = list((await session.scalars(select(Frame).where(Frame.project_id == 7)
                       .order_by(Frame.sort_key, Frame.number))).all())
        return project, frames

    def plan_inputs(self, project, frames):
        native = import_module("app.services.sfx_plan")
        timeline = native.frame_timeline(frames)
        scenes = [{"frame_number": frame.number, "place": frame.attrs.get("place") or "",
                   "main_action": frame.attrs.get("main_action") or "",
                   "meaning": (frame.meaning or "")[:120]} for frame in frames]
        total = timeline[-1]["end"]
        prompt = native._PLAN_PROMPT.format(timeline=json.dumps(timeline, ensure_ascii=False),
            scenes=json.dumps(scenes, ensure_ascii=False), total=total,
            max_per_frame=native.MAX_EVENTS_PER_FRAME, min_gap=native.MIN_GAP_S,
            kinds=", ".join(native.SFX_KINDS))
        return dict(timeline=timeline, total_duration=total, scenes=scenes, prompt=prompt)

    @contextmanager
    def context(self, session, *, completed=None):
        from app.services import observation

        ctx = observation.AttemptContext(self.observer, self.scope, session,
                                        "project", "n1", ATTEMPT)
        ctx.native_node_type = self.fixture_node_type
        ctx.last_request_id = STALE_REQUEST
        attempt = observation._attempt.set(ctx)
        request = observation._request.set(None)
        done = observation._completed_model_request.set((ctx, completed) if completed else None)
        try:
            yield ctx
        finally:
            observation._completed_model_request.reset(done)
            observation._request.reset(request)
            observation._attempt.reset(attempt)

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
        self.assertEqual(writes, [], "bounded observation cannot write/autoflush business data")

    async def state(self):
        from sqlalchemy import select
        from app.models import Artifact, Frame, NodeRun, Project

        async with self.business_sessions() as reader:
            rows = {}
            for model in (Project, Frame, Artifact, NodeRun):
                rows[model.__tablename__] = [dict(row) for row in (await reader.execute(
                    select(model.__table__).order_by(model.id))).mappings().all()]
        return rows

    async def facts(self):
        from sqlalchemy import select
        from app.services import observation

        await self.observer.consume(EXECUTION, "master", self.sessions)
        async with self.sessions() as reader:
            return list((await reader.scalars(select(observation.ObservationEvent).where(
                observation.ObservationEvent.execution_id == EXECUTION)
                .order_by(observation.ObservationEvent.id))).all())

    def one(self, facts, kind):
        found = [fact for fact in facts if fact.kind == kind]
        self.assertEqual(len(found), 1, (kind, [fact.kind for fact in facts]))
        self.assertEqual((found[0].execution_id, found[0].project_id, found[0].node_key),
                         (EXECUTION, 7, "n1"))
        return found[0]

    def body(self, fact):
        reference = fact.payload["body"]
        self.assertNotIn("gap", reference)
        path = (self.observer.body_root / reference["path"]).resolve()
        self.assertTrue(path.is_relative_to(self.observer.body_root.resolve()))
        return json.loads(path.read_text(encoding="utf-8"))

    def unclaimed(self, proof):
        for key in ("managed_eligible", "business_commit_claim", "semantic_quality_verified", "mix_verified"):
            self.assertIs(proof[key], False, proof)

    async def unchanged_authority(self):
        from sqlalchemy import select
        from app.models import NodeRun, NodeRunStatus, Project
        from app.services.actions_v1 import ActionExecutionControl, ActionRecord, get_permission
        from app.services.node_capabilities import capability_for
        from app.services.observation import ExecutionControl

        self.assertIn("retry_same", capability_for("sfx_plan").actions)
        self.assertTrue(capability_for("sfx_gen").managed)
        self.assertIsNone(get_permission(7))
        async with self.sessions() as reader:
            self.assertEqual((await reader.get(ExecutionControl, EXECUTION)).mode, "observe")
            self.assertIsNone(await reader.get(ActionExecutionControl, EXECUTION))
            self.assertEqual(list((await reader.scalars(select(ActionRecord))).all()), [])
            self.assertEqual((await reader.get(Project, 7)).status, self.running)
            nodes = {row.node_key: row.status for row in (await reader.scalars(select(NodeRun))).all()}
            self.assertEqual(nodes, {"n1": NodeRunStatus.running, "n2": NodeRunStatus.pending})

    def step(self):
        return import_module("app.orchestrator.steps.generate_sfx" if self.fixture_node_type == "sfx_gen"
                             else "app.orchestrator.steps.plan_sfx")

    async def capture(self, project, frames):
        native = import_module("app.services.sfx_plan")
        if self.fixture_node_type == "sfx_plan":
            await self.api("record_sfx_plan_input")(project, frames, **self.plan_inputs(project, frames))
        else:
            payload = deepcopy(project.meta["ai_jobs"]["sfx_plan"])
            await self.api("record_sfx_generation_input")(project, frames, source="checkpoint",
                payload=payload, events=native._parse_events(payload), provider="local_synth")

    async def native_commit(self, session, project):
        no_model = AsyncMock(side_effect=AssertionError("cached native SFX must not call a model"))
        with patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
            await self.step().run(session, project)
        no_model.assert_not_called()
        no_model.assert_not_awaited()

    @contextmanager
    def result_spy(self):
        original = self.api("observe_sfx_result")
        async def guarded(*args, **kwargs):
            before = await self.state()
            with self.business_sql() as statements:
                result = await original(*args, **kwargs)
            self.no_business_writes(statements)
            self.assertEqual(await self.state(), before, "result evidence cannot alter business rows")
            return result
        spy = AsyncMock(side_effect=guarded)
        with patch.object(import_module("app.services.sfx_plan"), "observe_sfx_result", new=spy):
            yield spy

    async def test_public_observation_seams_are_present(self):
        for name in ("record_sfx_plan_input", "record_sfx_generation_input",
                     "record_sfx_parse", "observe_sfx_result"):
            with self.subTest(api=name):
                self.api(name)

    async def disabled_calls(self):
        await self.api("record_sfx_plan_input")(None, None, timeline=None, total_duration=None,
                                               scenes=None, prompt=None)
        await self.api("record_sfx_generation_input")(None, None, source=None, payload=None,
                                                      events=None, provider=None)
        await self.api("record_sfx_parse")(None, None, None, source=None)
        await self.api("observe_sfx_result")(None, None)

    async def test_default_off_touches_no_inputs_sql_files_bodies_or_events(self):
        from app.services import observation

        self.assertIsNone(observation._attempt.get())
        before = await self.state()
        bodies = sorted(self.observer.body_root.glob("**/*.json"))
        with self.business_sql() as statements, \
             patch("wave.open", side_effect=AssertionError("default-off WAV read")), \
             patch.object(Path, "read_bytes", side_effect=AssertionError("default-off file read")):
            await self.disabled_calls()
        self.assertEqual(statements, [])
        self.assertEqual(await self.facts(), [])
        self.assertEqual(sorted(self.observer.body_root.glob("**/*.json")), bodies)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_stopped_context_touches_no_inputs_sql_files_bodies_or_events(self):
        before = await self.state()
        self.scope.observer_state = "stopped"
        async with self.business_sessions() as business:
            with self.context(business), self.business_sql() as statements, \
                 patch("wave.open", side_effect=AssertionError("stopped WAV read")):
                await self.disabled_calls()
            self.assertEqual(statements, [])
        self.assertEqual(await self.facts(), [])
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_plan_input_detaches_original_order_timeline_scenes_and_prompt(self):
        from app.services.observation import digest

        record = self.api("record_sfx_plan_input")
        before = await self.state()
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            args = self.plan_inputs(project, frames)
            expected = {"project_id": 7,
                "frames": [{key: deepcopy(getattr(frame, key)) for key in FRAME_FIELDS} for frame in frames],
                **deepcopy(args), "cached_payload": deepcopy(self.payload)}
            with self.context(business), self.business_sql() as statements:
                await record(project, frames, **args)
                frames[0].attrs["existing_note"]["keep"].append("uncommitted mutation")
                args["scenes"][0]["place"] = "uncommitted change"
            self.no_business_writes(statements)
        fact = self.one(await self.facts(), "node_input")
        self.assertEqual(fact.payload["contract"], "native_sfx_plan")
        self.assertEqual(fact.payload["input_sha256"], digest(expected))
        self.assertEqual(self.body(fact), expected)
        self.assertEqual([row["number"] for row in expected["frames"]], [2, 1])
        self.assertEqual(expected["total_duration"], 6.0)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_generation_input_detaches_selected_native_plan_and_provider(self):
        native = import_module("app.services.sfx_plan")
        from app.services.observation import digest

        record = self.api("record_sfx_generation_input")
        before = await self.state()
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            payload = deepcopy(self.payload)
            events = native._parse_events(payload)
            expected = {"project_id": 7,
                "frames": [{key: deepcopy(getattr(frame, key)) for key in FRAME_FIELDS} for frame in frames],
                "source": "checkpoint", "payload": deepcopy(payload),
                "events": [event.to_dict() for event in events], "provider": "local_synth"}
            with self.context(business), self.business_sql() as statements:
                await record(project, frames, source="checkpoint", payload=payload,
                             events=events, provider="local_synth")
                payload["events"][0]["kind"] = "uncommitted change"
            self.no_business_writes(statements)
        fact = self.one(await self.facts(), "node_input")
        self.assertEqual(fact.payload["contract"], "native_sfx_gen")
        self.assertEqual(fact.payload["input_sha256"], digest(expected))
        self.assertEqual(self.body(fact), expected)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_fresh_accepted_parse_binds_actual_completed_request_and_native_events(self):
        from app.services.ai_result_io import parse_json_object
        from app.services.observation import digest

        native = import_module("app.services.sfx_plan")
        record = self.api("record_sfx_parse")
        reply = json.dumps(self.payload, ensure_ascii=False)
        payload = parse_json_object(reply)
        events = native._parse_events(payload)
        problems = native.validate_events(events, total_duration=6.0)
        self.assertEqual(problems, [])
        before = await self.state()
        async with self.business_sessions() as business:
            with self.context(business, completed=REQUEST), self.business_sql() as statements:
                await record(payload, events, problems, source="model", reply=reply)
            self.no_business_writes(statements)
        facts = await self.facts()
        parsed = self.one(facts, "parse_result")
        self.assertEqual(parsed.request_id, REQUEST)
        self.assertEqual(parsed.payload["source"], "model")
        for key in ("syntax_valid", "parsed", "schema_valid"):
            self.assertIs(parsed.payload[key], True)
        self.assertIsNone(parsed.payload["error_type"])
        normalized = [event.to_dict() for event in events]
        self.assertEqual(parsed.payload["sha256"], digest(normalized))
        self.assertEqual(self.body(parsed)["payload"], payload)
        self.assertEqual(self.body(parsed)["events"], normalized)
        self.assertEqual([event["frame_number"] for event in normalized], [2, 1])
        self.assertEqual(normalized[1]["kind"], "hit")
        self.assertEqual(normalized[1]["t_start"], 3.512)
        gate = self.one(facts, "runtime_check").payload
        self.assertEqual(gate["check"], "native_sfx_events_validation")
        self.assertTrue(gate["performed"])
        self.assertEqual(gate["status"], "passed")
        self.assertIs(gate["business_commit_claim"], False)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def assert_cached_failure(self, payload, expected_error):
        from app.models import Project
        from app.services import observation
        from app.services.ai_result_io import save_ai_job_checkpoint

        self.api("record_sfx_parse")
        self.api("observe_sfx_result")
        async with self.business_sessions() as seed:
            save_ai_job_checkpoint(await seed.get(Project, 7), "sfx_plan", payload)
            await seed.commit()
        before = await self.state()
        no_model = AsyncMock(side_effect=AssertionError("cached rejection must not request a model"))
        async with self.business_sessions() as business:
            project, _ = await self.inputs(business)
            with patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
                with self.assertRaises(expected_error) as caught:
                    async with observation.observe_attempt(business, project):
                        await self.step().run(business, project)
            self.assertIs(type(caught.exception), expected_error)
            self.assertEqual(project.status, self.running)
            self.assertFalse(project.meta.get("sfx_plan"))
        no_model.assert_not_called()
        no_model.assert_not_awaited()
        facts = await self.facts()
        parsed = self.one(facts, "parse_result")
        self.assertEqual(parsed.payload["source"], "checkpoint")
        self.assertIsNone(parsed.request_id)
        self.assertIsNone(parsed.payload["syntax_valid"])
        self.assertIs(parsed.payload["schema_valid"], False)
        self.assertIs(parsed.payload["parsed"], expected_error is RuntimeError)
        self.assertEqual(parsed.payload["error_type"], expected_error.__name__)
        self.assertEqual(self.body(parsed)["payload"], payload)
        self.assertEqual(self.one(facts, "node_error").payload["error_type"], expected_error.__name__)
        self.assertFalse(any(fact.kind in {"model_start", "model_end", "node_end"} for fact in facts))
        self.assertFalse((self.directory / "sfx_plan.json").exists())
        self.assertFalse((self.directory / "sfx").exists())
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()
        return parsed

    async def test_actual_cached_shape_failure_remains_value_error_not_json_outage(self):
        parsed = await self.assert_cached_failure({"events": "not a list"}, ValueError)
        self.assertEqual(self.body(parsed)["events"], [])

    async def cached_domain_failure(self, field, value):
        native = import_module("app.services.sfx_plan")
        payload = deepcopy(self.payload)
        payload["events"][0][field] = value
        events = native._parse_events(payload)
        problems = native.validate_events(events, total_duration=6.0)
        self.assertTrue(problems)
        parsed = await self.assert_cached_failure(payload, RuntimeError)
        self.assertEqual(parsed.payload["problems"], problems)
        self.assertEqual(self.body(parsed)["events"], [event.to_dict() for event in events])

    async def test_actual_cached_time_validation_failure_remains_runtime_error(self):
        await self.cached_domain_failure("t_start", 99.0)

    async def test_actual_cached_kind_validation_failure_remains_runtime_error(self):
        await self.cached_domain_failure("kind", "laser")

    async def test_actual_cached_gain_validation_failure_remains_runtime_error(self):
        await self.cached_domain_failure("gain", 2.0)

    async def test_actual_cached_duration_validation_failure_remains_runtime_error(self):
        await self.cached_domain_failure("duration", 0.1)

    async def test_original_plan_commits_validated_cache_result_once_without_model_identity(self):
        from app.models import ProjectStatus
        from app.services import observation

        source = await self.state()
        with self.result_spy() as spy:
            async with self.business_sessions() as business:
                project, _ = await self.inputs(business)
                async with observation.observe_attempt(business, project):
                    await self.native_commit(business, project)
                self.assertEqual(project.status, ProjectStatus.sfx_plan_ready)
                before = await self.state()
            spy.assert_awaited_once()
        self.assertEqual(before["frames"], source["frames"])
        self.assertEqual(before["artifacts"], source["artifacts"])
        facts = await self.facts()
        self.one(facts, "node_input")
        parsed = self.one(facts, "parse_result")
        self.assertEqual(parsed.payload["source"], "checkpoint")
        self.assertIsNone(parsed.request_id)
        self.assertIsNone(parsed.payload["syntax_valid"])
        self.assertIs(parsed.payload["schema_valid"], True)
        output = self.body(self.one(facts, "node_output"))
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "passed")
        self.assertEqual(output["project_id"], 7)
        self.assertEqual(output["plan_payload"], self.payload)
        actual_file = json.loads((self.directory / "sfx_plan.json").read_text(encoding="utf-8"))
        self.assertEqual(output["plan_file"], actual_file)
        self.assertEqual(actual_file["total_duration"], 6.0)
        self.unclaimed(proof)
        self.one(facts, "node_end")
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_committed_plan_without_captured_input_stays_unknown(self):
        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, _ = await self.inputs(business)
            await self.native_commit(business, project)
            before = await self.state()
            with self.context(business), self.business_sql() as statements:
                await observe(business, project, "sfx_plan")
            self.no_business_writes(statements)
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual(proof["status"], "unknown")
        self.assertIsNone(proof["input_sha256"])
        self.unclaimed(proof)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    def actual_wav(self, path):
        with wave.open(str(path), "rb") as reader:
            self.assertEqual((reader.getnchannels(), reader.getsampwidth(), reader.getframerate()),
                             (1, 2, 44100))
            count = reader.getnframes()
            pcm = reader.readframes(count)
        power = sum(sample[0] ** 2 for sample in struct.iter_unpack("<h", pcm)) / count
        self.assertGreater(power, 0)
        return count / 44100, 20 * math.log10(math.sqrt(power) / 32768)

    async def test_original_generation_commits_two_real_wavs_with_frame_provenance(self):
        from app.models import ProjectStatus
        from app.services import observation

        source = await self.state()
        with self.result_spy() as spy:
            async with self.business_sessions() as business:
                project, frames = await self.inputs(business)
                async with observation.observe_attempt(business, project):
                    await self.native_commit(business, project)
                self.assertEqual(project.status, ProjectStatus.sfx_ready)
                before = await self.state()
            spy.assert_awaited_once()
        self.assertEqual(before["frames"], source["frames"])
        self.assertEqual(before["artifacts"], source["artifacts"])
        facts = await self.facts()
        captured = self.one(facts, "node_input")
        self.assertEqual(captured.payload["contract"], "native_sfx_gen")
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "passed")
        self.assertEqual(proof["input_sha256"], captured.payload["input_sha256"])
        self.unclaimed(proof)
        files = proof["files"]
        self.assertEqual([item["idx"] for item in files], [0, 1])
        by_number = {frame.number: frame for frame in frames}
        records = project.meta["ai_jobs"]["sfx_files"]["files"]
        self.assertEqual(len(records), 2)
        for item, record in zip(files, records):
            path = Path(record["path"])
            duration, rms = self.actual_wav(path)
            self.assertEqual(Path(item["path"]).resolve(), path.resolve())
            self.assertEqual(item["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertAlmostEqual(item["duration_seconds"], duration, places=6)
            self.assertAlmostEqual(item["rms_db"], rms, places=5)
            self.assertGreaterEqual(item["rms_db"], -60.0)
            self.assertEqual(item["provider"], "local_synth")
            self.assertEqual(item["source_frame"], {key: getattr(by_number[record["frame_number"]], key)
                                                    for key in IDENTITY})
            self.assertIsNone(item["artifact_row"])
        self.assertEqual(before["artifacts"], [])
        self.one(facts, "node_end")
        self.assertFalse(any(fact.kind in {"model_start", "model_end", "artifact_saved"} for fact in facts))
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_committed_wavs_without_captured_input_stay_unknown(self):
        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, _ = await self.inputs(business)
            await self.native_commit(business, project)
            before = await self.state()
            with self.context(business), self.business_sql() as statements:
                await observe(business, project, "sfx_gen")
            self.no_business_writes(statements)
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual(proof["status"], "unknown")
        self.assertIsNone(proof["input_sha256"])
        self.unclaimed(proof)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def generation_fault(self, mutation):
        from app.services.ai_result_io import save_ai_job_checkpoint

        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            with self.context(business):
                await self.capture(project, frames)
                await self.native_commit(business, project)
                records = deepcopy(project.meta["ai_jobs"]["sfx_files"]["files"])
                mutation(records)
                save_ai_job_checkpoint(project, "sfx_files", {"files": records})
                await business.commit()
                before = await self.state()
                with self.business_sql() as statements:
                    await observe(business, project, "sfx_gen")
                    await observe(business, project, "sfx_gen")
                self.no_business_writes(statements)
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual(proof["status"], "failed")
        self.unclaimed(proof)
        self.assertEqual(await self.state(), before)
        await self.unchanged_authority()

    async def test_foreign_file_checkpoint_is_rejected_without_repair(self):
        foreign = Path(self.tmp.name) / "foreign.wav"
        native = import_module("app.services.sfx_gen")
        native._write_wav(foreign, native._synth("hit", 0.612, seed=1001))
        await self.generation_fault(lambda records: records[1].update(path=str(foreign)))
        self.assertTrue(foreign.is_file(), "observation cannot remove foreign evidence")

    async def test_unreadable_native_wav_is_failed_read_evidence(self):
        def corrupt(records):
            Path(records[0]["path"]).write_bytes(b"broken WAV evidence")
        await self.generation_fault(corrupt)

    async def test_partial_committed_file_coverage_is_failed_without_status_change(self):
        await self.generation_fault(lambda records: records.pop())

    async def test_sfx_gen_proof_carries_the_shared_media_contract(self):
        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            with self.context(business):
                await self.capture(project, frames)
                await self.native_commit(business, project)
                await observe(business, project, "sfx_gen")
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual((proof["status"], proof["contract"], proof["node_type"]), ("passed", "native_media", "sfx_gen"))
        self.assertEqual(proof["items_total"], 2)
        self.assertNotIn("attempt_failure", proof)
        self.unclaimed(proof)

    async def test_failed_sfx_gen_attempt_never_passes_on_earlier_files(self):
        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            with self.context(business) as ctx:
                await self.capture(project, frames)
                await self.native_commit(business, project)
                ctx.attempt_exception = RuntimeError("sfx_gen: ни один файл не прошёл верификацию")
                await observe(business, project, "sfx_gen")
        proof = self.one(await self.facts(), "node_result_verification").payload
        self.assertEqual((proof["status"], proof["contract"]), ("failed", "native_media"))
        self.assertEqual(proof["reason"], "attempt_failed:RuntimeError")
        self.assertEqual(proof["attempt_failure"], {"error_type": "RuntimeError", "retryable_by_node": False,
                                                    "new_verified_artifact": False})
        self.unclaimed(proof)

    async def test_failed_sql_session_reads_committed_plan_without_repair(self):
        from sqlalchemy.exc import IntegrityError, PendingRollbackError
        from app.models import Frame

        observe = self.api("observe_sfx_result")
        async with self.business_sessions() as business:
            project, frames = await self.inputs(business)
            with self.context(business):
                await self.capture(project, frames)
                await self.native_commit(business, project)
                committed = await self.state()
                project.meta = {"uncommitted": "must not become observed output"}
                business.add(Frame(project_id=7, number=1, uuid="cf" * 16,
                                   voiceover_text="Duplicate native frame", attrs={}))
                with self.assertRaises(IntegrityError):
                    await business.flush()
                self.assertFalse(business.is_active)
                with self.business_sql() as statements, ExitStack() as spies:
                    rollback = spies.enter_context(patch.object(business, "rollback", new=AsyncMock(wraps=business.rollback)))
                    commit = spies.enter_context(patch.object(business, "commit", new=AsyncMock(wraps=business.commit)))
                    flush = spies.enter_context(patch.object(business, "flush", new=AsyncMock(wraps=business.flush)))
                    await observe(business, project, "sfx_plan")
                    rollback.assert_not_awaited()
                    commit.assert_not_awaited()
                    flush.assert_not_awaited()
                self.no_business_writes(statements)
                self.assertTrue(any(sql.lstrip().upper().startswith("SELECT") for sql in statements))
                with self.assertRaises(PendingRollbackError):
                    await business.refresh(project)
        facts = await self.facts()
        proof = self.one(facts, "node_result_verification").payload
        self.assertEqual(proof["status"], "passed")
        self.assertEqual(self.body(self.one(facts, "node_output"))["plan_payload"], self.payload)
        self.unclaimed(proof)
        self.assertEqual(await self.state(), committed)
        await self.unchanged_authority()

    async def test_result_diagnostic_failure_does_not_replace_actual_cached_native_error(self):
        from app.models import Project
        from app.services import observation
        from app.services.ai_result_io import save_ai_job_checkpoint

        self.api("observe_sfx_result")
        native = import_module("app.services.sfx_plan")
        payload = deepcopy(self.payload)
        payload["events"][0]["kind"] = "laser"
        async with self.business_sessions() as seed:
            save_ai_job_checkpoint(await seed.get(Project, 7), "sfx_plan", payload)
            await seed.commit()
        diagnostic = AsyncMock(side_effect=LookupError("synthetic read diagnostic unavailable"))
        original = None
        no_model = AsyncMock(side_effect=AssertionError("invalid cache must not call model"))
        with patch.object(native, "observe_sfx_result", new=diagnostic), \
             patch("app.services.gpt_client.gpt_ask_fresh", new=no_model):
            async with self.business_sessions() as business:
                project, _ = await self.inputs(business)
                with self.assertRaises(RuntimeError) as caught:
                    async with observation.observe_attempt(business, project):
                        try:
                            await self.step().run(business, project)
                        except RuntimeError as error:
                            original = error
                            raise
                self.assertIs(caught.exception, original)
        diagnostic.assert_awaited_once()
        self.assertEqual(self.observer.failures[EXECUTION], "LookupError")
        no_model.assert_not_called()
        self.assertEqual(self.one(await self.facts(), "node_error").payload["error_type"], "RuntimeError")
        await self.unchanged_authority()


if __name__ == "__main__":
    unittest.main(verbosity=2)
