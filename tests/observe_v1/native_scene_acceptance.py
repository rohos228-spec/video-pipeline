"""Bounded camera acceptance in the existing group/main/seed/loopback harness.

Parent integration, owned elsewhere:
* call prepare(fixtures, ROOT, 'native-scene-camera') before app imports;
* use handler_for(fixtures, ROOT) for the existing loopback server;
* call run(client, fixtures, observer, ROOT, result, prepared) after wait_api.
The shared HTTP transport must keep its existing observed-node identity guard.
No alternate main, worker, executor, provider, or server is started here.

This is observation mode only. No Actions enable/launch, profile, approval,
retry, force-wipe, or successful NodeRun write is issued. Existing frames are
preserved. An empty seed must acquire frames through the original split API and
worker first; this module never inserts/updates Frame or staging rows.

Camera input is captured by the external HTTP fixture after native DB-v2
backfill, before the camera reply and store_cells. Acceptance covers this camera
node only, with actual input/parse/output/result journal bodies for its native
attempt. Missing fanout/scene HITL remains diagnostic; assembler, full factory
and managed acceptance stay false. Earlier archived diagnostic captures remain
unchanged and separate from this later worker capture and helper GREEN tests.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import sqlite3
import sys
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


SPLIT = "native-scene-split-A"
CAMERA = "native-scene-camera-B"
ASSEMBLER = "native-scene-assembler-C"
OUTSIDE = "outside-native-scene-Z"
PROJECT_ID = 2
CAMERA_JOURNAL_KINDS = ("node_input", "parse_result", "node_output", "node_result_verification")


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


@contextmanager
def _read_only(path):
    connection = sqlite3.connect(Path(path).as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()


def _table_exists(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _read_state(fixtures):
    with _read_only(fixtures.DB) as db:
        project = dict(db.execute("SELECT * FROM projects WHERE id=?", (PROJECT_ID,)).fetchone())
        project["meta"] = json.loads(project["meta"] or "{}")
        frames = [dict(row) for row in db.execute(
            "SELECT * FROM frames WHERE project_id=? ORDER BY sort_key,number", (PROJECT_ID,))]
        for row in frames:
            row["attrs"] = json.loads(row["attrs"] or "{}")
        cells = [dict(row) for row in db.execute(
            "SELECT * FROM scene_design_cells WHERE project_id=? ORDER BY id", (PROJECT_ID,))]
        scenes = [dict(row) for row in db.execute(
            "SELECT * FROM scenes WHERE project_id=? ORDER BY sort_key,id", (PROJECT_ID,))]
        texts = [dict(row) for row in db.execute(
            "SELECT * FROM frame_texts WHERE project_id=? ORDER BY id", (PROJECT_ID,))]
        hitl = [dict(row) for row in db.execute(
            "SELECT * FROM hitl_requests WHERE project_id=? ORDER BY id", (PROJECT_ID,))]
        nodes = [dict(row) for row in db.execute(
            "SELECT nr.* FROM node_runs nr JOIN workflow_runs wr ON nr.workflow_run_id=wr.id "
            "WHERE wr.project_id=? ORDER BY nr.node_key", (PROJECT_ID,))]
    return {"project": project, "frames": frames, "cells": cells,
            "scenes": scenes, "texts": texts, "hitl": hitl, "nodes": nodes}


def _objects(state, *, data_dir=None):
    project = SimpleNamespace(**state["project"])
    if data_dir is not None:
        project.data_dir = Path(data_dir)
    return (project,
            [SimpleNamespace(**row) for row in state["frames"]],
            [SimpleNamespace(**row) for row in state["cells"]])


def prepare(fixtures, root, mode):
    """Initialize only the fresh copied graph; never populate successful output."""
    root = Path(root)
    assert Path("/.dockerenv").is_file(), "Parent offline container only"
    assert "app.settings" not in sys.modules, "Prepare before application settings are loaded"
    assert mode == "native-scene-camera", "This draft covers one native camera probe"
    paths = (fixtures.DATA / "state.db", fixtures.DB)
    for path in paths:
        assert path.resolve().is_relative_to(root.resolve()), "Synthetic copied seed must be inside results"
        assert path.is_file(), "Reuse the existing group seed, do not create another stand"
    diagnostics = []
    initial = []
    for path in paths:
        with _read_only(path) as db:
            row = db.execute("SELECT status,meta,script_text FROM projects WHERE id=?", (PROJECT_ID,)).fetchone()
            assert row, "Existing synthetic seed project required"
            runs = db.execute("SELECT id FROM workflow_runs WHERE project_id=?", (PROJECT_ID,)).fetchall()
            if len(runs) != 1:
                diagnostics.append("seed requires exactly one existing WorkflowRun:" + path.name)
            if _table_exists(db, "action_execution_controls"):
                if db.execute("SELECT 1 FROM action_execution_controls WHERE project_id=?", (PROJECT_ID,)).fetchone():
                    diagnostics.append("seed carries managed control history:" + path.name)
            if _table_exists(db, "managed_run_allowances"):
                if db.execute("SELECT 1 FROM managed_run_allowances WHERE project_id=?", (PROJECT_ID,)).fetchone():
                    diagnostics.append("seed carries managed allowance ownership:" + path.name)
            initial.append({"path": str(path), "run_ids": [r[0] for r in runs],
                "frame_count": db.execute("SELECT count(*) FROM frames WHERE project_id=?", (PROJECT_ID,)).fetchone()[0],
                "script_text": row["script_text"], "meta": json.loads(row["meta"] or "{}")})
    if any(item["meta"].get("user_stop") for item in initial):
        diagnostics.append("seed has user_stop; do not clear it to manufacture acceptance")
    prepared = {"scenario": mode, "node_key": CAMERA, "split_key": SPLIT,
        "assembler_key": ASSEMBLER, "outside": OUTSIDE,
        "managed_enable_authorized": False, "diagnostics": diagnostics,
        "initial_frame_counts": [item["frame_count"] for item in initial],
        "ready": not diagnostics,
        "interventions": ["fresh copied seed graph initialized before startup; every NodeRun initially pending",
            "preserve script and Frame/Scene/FrameText/staging contents; auto_mode initially off",
            "native split API prerequisite only when the seed has no frames",
            "native marked excel_gpt camera API launch; no executor, validation or FSM replacement",
            "capture native input at the external loopback request boundary after native backfill",
            "no managed enable, retry/profile, HITL approval, force-wipe or assembly success injection"]}
    if diagnostics:
        _write(root / "native-scene-prepared.json", prepared)
        return prepared

    nodes = [
        {"id": SPLIT, "type": "split", "position": {"x": 0, "y": 0},
         "data": {"label": "Synthetic native split prerequisite", "modelId": "gpt-kie"}},
        {"id": CAMERA, "type": "excel_gpt", "position": {"x": 200, "y": 0},
         "data": {"label": "Native camera", "sd_agent": "camera", "modelId": "gpt-kie"}},
        {"id": ASSEMBLER, "type": "excel_gpt", "position": {"x": 400, "y": 0},
         "data": {"label": "Native assembler prerequisite boundary", "sd_agent": "assemble", "modelId": "gpt-kie"}},
        {"id": OUTSIDE, "type": "images", "position": {"x": 600, "y": 0},
         "data": {"label": "Outside camera probe"}},
    ]
    edges = [{"id": "native-scene-edge-" + str(index), "source": source, "target": target,
              "data": {"kind": "after"}}
             for index, (source, target) in enumerate(zip((SPLIT, CAMERA, ASSEMBLER), (CAMERA, ASSEMBLER, OUTSIDE)))]
    for item, path in zip(initial, paths):
        with sqlite3.connect(path) as db:
            meta = item["meta"]
            meta["canvas_graph"] = {"nodes": nodes, "edges": edges}
            meta["scene_design_enabled"] = True
            # No existing Frame or checkpoint is changed. start_step owns the
            # native per-agent only_agent/invalidation and all running states.
            db.execute("UPDATE projects SET status='new',auto_mode=0,meta=? WHERE id=?",
                (json.dumps(meta), PROJECT_ID))
            run_id = item["run_ids"][0]
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                (json.dumps(nodes), json.dumps(edges), run_id))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
            for node, node_type in zip(nodes, ("split", "sd_agent", "sd_assemble", "images")):
                db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,created_at,updated_at) "
                    "VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (run_id, node["id"], node_type))
    config = {"version": 1, "mode": "synthetic", "online_enabled": False,
              "endpoint": None, "decision": None, "profiles": {}, "allowed_actions": []}
    config_path = root / "synthetic-native-scene-runtime.json"
    _write(config_path, config)
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(config_path)
    prepared["config_sha256"] = hashlib.sha256(config_path.read_bytes()).hexdigest()
    fixtures.NODES = (SPLIT, CAMERA, ASSEMBLER, OUTSIDE)

    def answer(node, attempt):
        if node == SPLIT:
            voiceover_file = fixtures.DB.parent / "voiceover.txt"
            source = voiceover_file.read_text(encoding="utf-8-sig") if voiceover_file.is_file() else initial[1]["script_text"]
            assert isinstance(source, str) and source.strip(), "Missing real split input is diagnostic"
            words = source.split()
            assert len(words) >= 2, "Native split source is incomplete"
            midpoint = len(words) // 2
            return json.dumps({"ops": [{"target": "replace_frames", "frames": [
                {"закадр": " ".join(words[:midpoint])}, {"закадр": " ".join(words[midpoint:])},
            ]}]}, ensure_ascii=False)
        if node == CAMERA:
            state = _read_state(fixtures)
            shots = [{"id_scene": "scene_01", "цитата": frame["voiceover_text"].strip(),
                      "крупность": "средний", "особенность_сцены": "Деталь объекта",
                      "тип_стыка": "action", "композиция": "Синтетическая композиция",
                      "движение": "push-in"}
                     for frame in state["frames"] if (frame["voiceover_text"] or "").strip()]
            assert shots, "No native camera frame voiceover available"
            value = {"shot_plan": shots}
            _write(root / ("native-camera-reply-" + str(attempt) + ".json"), value)
            return json.dumps(value, ensure_ascii=False)
        raise AssertionError("No fabricated assembler/fanout answer: " + str(node))

    fixtures.synthetic_answer = answer
    prepared["nodes"] = nodes
    _write(root / "native-scene-prepared.json", prepared)
    return prepared


def handler_for(fixtures, root, *, base_handler=None):
    """Reuse the existing SSE handler; intercept only external fixture bytes."""
    root = Path(root)
    base_handler = base_handler or fixtures.FixtureHandler

    class Handler(base_handler):
        counts = Counter()
        response_ids = []
        requests = []

        def do_POST(self):
            if self.path != "/codex/v1/responses":
                return self.send_error(404)
            node = self.headers.get("x-diagnostic-node", "")
            if node not in {SPLIT, CAMERA}:
                return self.send_error(422, "Outside bounded native camera/split fixture")
            length = int(self.headers.get("content-length", "0"))
            if not 0 < length <= 4 * 1024 * 1024:
                return self.send_error(413)
            raw = self.rfile.read(length)
            type(self).requests.append({"node_key": node,
                "attempt_id": self.headers.get("x-diagnostic-attempt"),
                "body": json.loads(raw), "ordinal": len(type(self).requests) + 1})
            _write(root / "native-scene-worker-requests.json", type(self).requests)
            if node == CAMERA:
                from app.services.scene_contracts import scene_input_snapshot

                state = _read_state(fixtures)
                project, frames, cells = _objects(state)
                ordinal = type(self).counts[CAMERA] + 1
                _write(root / ("native-camera-input-" + str(ordinal) + ".json"), {
                    "snapshot": scene_input_snapshot(project, frames, cells=cells), "state": state,
                    "boundary": "actual camera HTTP request after native backfill, before fixture response/staging",
                    "node_key": node, "request_ordinal": ordinal,
                })
            # The existing handler owns SSE shape, response IDs and counts.
            original = self.rfile
            self.rfile = io.BytesIO(raw)
            try:
                return super().do_POST()
            finally:
                self.rfile = original

    fixtures.native_scene_handler = Handler
    return Handler


async def _history(client, prefix):
    events, after = [], 0
    while True:
        response = await client.get(prefix + "/events", params={"after": after, "limit": 500})
        response.raise_for_status()
        page = response.json()
        events.extend(page["events"])
        if not page["has_more"]:
            return events
        assert page["next_cursor"] > after, "History cursor must advance"
        after = page["next_cursor"]


async def _wait_native(fixtures, key):
    deadline = time.monotonic() + 150
    state = _read_state(fixtures)
    while time.monotonic() < deadline:
        state = _read_state(fixtures)
        node = next((row for row in state["nodes"] if row["node_key"] == key), None)
        if node and node["status"] in {"done", "failed", "waiting_hitl"}:
            return state
        # Native only_agent can return frames_ready before NodeRun replication.
        # Do not infer completion from this Project status or staged cells alone.
        await asyncio.sleep(0.25)
    return state


def _one_camera_fact(facts, kind):
    matching = [fact for fact in facts if fact["kind"] == kind]
    assert len(matching) == 1, ("Exactly one camera journal fact required", kind, len(matching))
    return matching[0]


def _journal_body(observer, fact, evidence):
    reference = fact["payload"].get("body")
    assert isinstance(reference, dict) and not reference.get("gap"), (fact["kind"], reference)
    relative = Path(reference["path"])
    sha = reference["sha256"]
    assert isinstance(sha, str) and len(sha) == 64
    assert relative.parts == (fact["execution_id"], sha + ".json"), "Body must belong to this execution"
    body_root = Path(observer.body_root).resolve()
    folder = (body_root / fact["execution_id"]).resolve()
    path = (body_root / relative).resolve()
    assert folder.parent == body_root and path.is_relative_to(folder), "Body outside execution directory"
    raw = path.read_bytes()
    assert len(raw) == reference["bytes"], "Body size differs from journal reference"
    assert hashlib.sha256(raw).hexdigest() == sha, "Body bytes differ from journal SHA256"
    body = json.loads(raw)
    assert isinstance(body, dict), "Native camera fact needs a complete object body"
    evidence.setdefault("body_refs", {})[fact["kind"]] = dict(reference)
    return body


def _verify_camera_journal(observer, events, *, execution, camera_node, state,
                           captured, reply, transport_request, evidence):
    """Read saved native facts and rows; no reconstructed facts or status writes."""
    from app.services.observation import digest
    from app.services.scene_contracts import scene_input_snapshot, verify_scene_agent

    facts = [event for event in events if event["node_key"] == CAMERA]
    start = _one_camera_fact(facts, "node_start")
    end = _one_camera_fact(facts, "node_end")
    model_start = _one_camera_fact(facts, "model_start")
    model_end = _one_camera_fact(facts, "model_end")
    journal = {kind: _one_camera_fact(facts, kind) for kind in CAMERA_JOURNAL_KINDS}
    checks = [fact for fact in facts if fact["kind"] == "runtime_check"
              and fact["payload"].get("check") == "native_scene_camera_parse"]
    assert len(checks) == 1, "Actual native camera parser check required once"
    check = checks[0]
    attempt = start["attempt_id"]
    assert attempt and start["workflow_run_id"]
    required = [start, end, model_start, model_end, *journal.values(), check]
    for fact in required:
        assert (fact["execution_id"], fact["project_id"], fact["workflow_run_id"],
                fact["node_key"], fact["attempt_id"]) == (
                    execution, PROJECT_ID, start["workflow_run_id"], CAMERA, attempt), "Native journal identity mismatch"
        assert fact["db_scope"] == "master", "Diagnostic fact must identify its independent journal source"
    assert not any(fact["kind"] in {"node_error", "model_error", "model_retrieve_start"} for fact in facts)
    request_id = model_start["request_id"]
    assert request_id and all(fact["request_id"] == request_id for fact in
                              (model_end, journal["parse_result"], check)), "Actual parser must link its own completed model request"
    assert all(journal[kind]["request_id"] is None for kind in
               ("node_input", "node_output", "node_result_verification")), "Row evidence must not invent a model request identity"
    assert transport_request["node_key"] == CAMERA and transport_request["attempt_id"] == attempt
    assert model_start["payload"]["method"] == "POST"
    assert model_start["payload"]["path"] == "/codex/v1/responses"
    assert model_end["payload"]["complete"] is True and model_end["payload"]["response_id"]
    assert end["payload"]["worker_returned"] is True and end["payload"]["business_commit_claim"] is False
    ordered = [start, journal["node_input"], model_start, model_end,
               journal["parse_result"], check, journal["node_output"],
               journal["node_result_verification"], end]
    assert all(left["id"] < right["id"] for left, right in zip(ordered, ordered[1:])), "Native capture boundary order required"

    model_input = _journal_body(observer, model_start, evidence)
    model_output = _journal_body(observer, model_end, evidence)
    assert model_input == transport_request["body"]
    assert model_start["payload"]["input_revision"] == digest(model_input)
    assert json.loads(model_output["text"]) == reply, "Actual returned model text must match the external fixture reply"
    bodies = {kind: _journal_body(observer, fact, evidence) for kind, fact in journal.items()}
    input_body = bodies["node_input"]
    assert input_body == captured["snapshot"], "Native input journal must match the independent HTTP-boundary snapshot"
    input_payload = journal["node_input"]["payload"]
    assert input_payload["contract"] == "native_scene_camera"
    assert input_payload["snapshot_boundary"] == "after native DB-v2 backfill, before camera staging"
    assert input_payload["input_sha256"] == digest(input_body)
    assert input_payload["frame_count"] == len(input_body["frames"]) == 2
    parsed = journal["parse_result"]["payload"]
    parsed_body = bodies["parse_result"]
    assert parsed["contract"] == "native_scene_camera" and parsed["agent"] == "camera"
    assert parsed["origin"] == "model" and parsed["parser"] == "scene_design.agents.parse_agent_slice"
    assert all(parsed[key] is True for key in ("syntax_valid", "parsed", "schema_valid", "validation_performed"))
    assert not parsed["validation_problems"] and parsed["error_type"] is None
    assert parsed["semantic_quality_verified"] is False
    assert parsed_body["text"] == model_output["text"] and parsed_body["parsed"] == reply
    assert parsed["sha256"] == digest(parsed_body["parsed"])
    assert check["payload"]["performed"] is True and check["payload"]["status"] == "passed"
    assert not check["payload"]["validation_problems"] and check["payload"]["error_type"] is None
    assert check["payload"]["business_commit_claim"] is False

    project, frames, cells = _objects(state)
    committed = scene_input_snapshot(project, frames, cells=cells)
    assert bodies["node_output"] == {
        "project_id": PROJECT_ID, "node_type": "sd_agent", "frames": committed["frames"],
        "cells": committed["cells"], "scene_registry": committed["scene_registry"],
    }, "Native output journal must match all actual rows, including rejected/non-camera cells"
    proof = verify_scene_agent(project, camera_node, cells, frames=frames,
        snapshot=input_body, agent_slice=parsed_body["parsed"])
    recorded = {key: value for key, value in journal["node_result_verification"]["payload"].items() if key != "body"}
    assert proof["status"] == "passed" and proof["cell_count"] == 14 and proof["frame_count"] == 2, proof
    assert bodies["node_result_verification"] == recorded == proof, "Typed verifier must prove the same input, accepted slice and committed rows"
    output = journal["node_output"]["payload"]
    assert output["contract"] == "native_scene_camera" and output["frame_count"] == 2
    assert output["source"] == "committed original executor rows; independent read session"
    assert output["sha256"] == proof["sha256"]
    # Each digest is checked with its original producer's encoding. The body
    # reference hash, observer digest and compact typed-proof hash are distinct.
    evidence.update(status="passed", execution_id=execution, workflow_run_id=start["workflow_run_id"],
        node_key=CAMERA, attempt_id=attempt, request_id=request_id,
        event_ids={fact["kind"]: fact["event_id"] for fact in required},
        journal_input_sha256=input_payload["input_sha256"], parsed_sha256=parsed["sha256"],
        typed_input_sha256=proof["input_sha256"], output_sha256=proof["sha256"], verification=proof)


async def run(client, fixtures, observer, root, result, prepared, *, browser=False):
    """Accept this native camera node only after its real worker/journal checks."""
    root = Path(root)
    result.update(accepted=False, status="diagnostic", managed_execution_verified=False,
        semantic_quality_verified=False, factory_completed=False, full_scene_fanout_accepted=False,
        managed_accepted=False, autonomous_continuation_claim=False,
        bounded_camera_node_accepted=False, camera_journal_acceptance=False, accepted_node_keys=[],
        acceptance_scope="single native camera node; full scene family unaccepted",
        interventions=prepared["interventions"], diagnostics=list(prepared["diagnostics"]),
        camera_worker_effect_verified=False, assembler_execution_verified=False, assembler_accepted=False,
        hitl_boundary_verified=False, hitl_accepted=False, human_approval_verified=False,
        business_commit_claim=False, operator_starts=0, camera_operator_starts=0, expected_route=[
            "projects.run_project_step", "project_steps.start_step marked excel_gpt -> scene_d",
            "main worker -> advance_runner.advance_project_job -> pipeline.advance_project",
            "scene_design.run -> runner.run_category_agents -> cells.store_cells"])
    if not prepared["ready"]:
        _write(root / "native-scene-result.json", result)
        return
    assert not browser, "Draft uses one actual API camera launch; shared browser ownership stays with parent"
    base = "http://127.0.0.1:18765/api/projects/2"
    response = await client.post(base + "/observations", json={"node_keys": [SPLIT, CAMERA, ASSEMBLER]})
    response.raise_for_status()
    scope = response.json()
    assert response.status_code == 201 and scope["mode"] == "observe", scope
    execution = scope["execution_id"]
    result["execution_id"] = execution
    prefix = base + "/observations/" + execution
    actions_response = await client.get(prefix + "/actions")
    actions_response.raise_for_status()
    actions = actions_response.json()
    assert not actions["enabled"] and not actions["recoveries"], actions
    state = _read_state(fixtures)
    if not state["frames"]:
        if not (state["project"].get("script_text") or "").strip() and not (fixtures.DB.parent / "voiceover.txt").is_file():
            result["diagnostics"].append("missing native split input; no camera start attempted")
            _write(root / "native-scene-result.json", result)
            return
        response = await client.post(base + "/steps/split/run", params={"node_key": SPLIT, "mode": "resume", "force_wipe": False})
        result["operator_starts"] += 1
        result["split_api"] = {"status_code": response.status_code, "body": response.json()}
        if response.status_code != 200:
            result["diagnostics"].append("original split API refused prerequisite")
            _write(root / "native-scene-result.json", result)
            return
        state = await _wait_native(fixtures, SPLIT)
        split_node = next(row for row in state["nodes"] if row["node_key"] == SPLIT)
        if split_node["status"] != "done" or not state["frames"]:
            result["diagnostics"].append("native split not complete; preserve harness/HITL/failure boundary")
            result["split_boundary"] = state
            _write(root / "native-scene-result.json", result)
            return

    before_camera = _read_state(fixtures)
    _write(root / "native-scene-before-camera-api.json", before_camera)
    response = await client.post(base + "/steps/excel_gpt/run",
        params={"node_key": CAMERA, "mode": "resume", "force_wipe": False})
    result["operator_starts"] += 1
    result["camera_operator_starts"] += 1
    result["camera_api"] = {"status_code": response.status_code, "body": response.json()}
    if response.status_code != 200:
        result["diagnostics"].append("original marked camera API refused start")
        _write(root / "native-scene-result.json", result)
        return
    after = await _wait_native(fixtures, CAMERA)
    await observer.catch_up()
    events = await _history(client, prefix)
    _write(root / "native-scene-api-history.json", {"events": events})
    _write(root / "native-scene-after-camera.json", after)
    counts = dict(fixtures.native_scene_handler.counts)
    result["fixture_request_counts"] = counts
    input_path = root / "native-camera-input-1.json"
    reply_path = root / "native-camera-reply-1.json"
    if not input_path.is_file() or not reply_path.is_file():
        result["diagnostics"].append("original camera reached no complete request/snapshot/reply boundary")
        _write(root / "native-scene-result.json", result)
        return
    captured = json.loads(input_path.read_text(encoding="utf-8"))
    reply = json.loads(reply_path.read_text(encoding="utf-8"))
    from app.services.scene_contracts import verify_scene_agent
    from app.services.scene_design import agents, runner

    project, frames, cells = _objects(after, data_dir=fixtures.DB.parent)
    camera_node = next(node for node in prepared["nodes"] if node["id"] == CAMERA)
    proof = verify_scene_agent(project, camera_node, cells, frames=frames,
        snapshot=captured["snapshot"], agent_slice=reply)
    result["camera_row_verification"] = proof
    nodes = {row["node_key"]: row for row in after["nodes"]}
    result["native_camera_boundary"] = {
        "project_status": after["project"]["status"],
        "node_status": nodes[CAMERA]["status"],
        "hitl": after["hitl"],
        "human_approval_verified": False,
    }
    _write(root / "native-scene-result.json", result)
    camera_events = [event for event in events if event["node_key"] == CAMERA]
    starts = [event for event in camera_events if event["kind"] == "node_start"]
    errors = [event for event in camera_events if event["kind"] == "node_error"]
    requests = json.loads((root / "native-scene-worker-requests.json").read_text(encoding="utf-8"))
    camera_requests = [request for request in requests if request["node_key"] == CAMERA]
    assert counts.get(CAMERA) == len(camera_requests) == 1, "One actual original camera request required"
    assert camera_requests[0]["body"]["model"] == "gpt-5-6-sol", "Original per-node Kie model mapping required"
    assert len(starts) == 1 and starts[0]["attempt_id"], "Original observed worker attempt required"
    assert proof["status"] == "passed", proof
    assert after["frames"] == captured["state"]["frames"], "Camera must preserve actual Frame rows after native backfill"
    assert after["texts"] == captured["state"]["texts"]
    assert after["scenes"] == captured["state"]["scenes"]
    original_hitl = {row["id"]: row for row in captured["state"]["hitl"]}
    final_hitl = {row["id"]: row for row in after["hitl"]}
    assert all(final_hitl.get(key) == row for key, row in original_hitl.items()), "Preserve existing HITL requests"
    assert all(row["decision"] == "pending" for key, row in final_hitl.items() if key not in original_hitl), "Native new HITL must remain pending"
    assert nodes[ASSEMBLER]["status"] == nodes[OUTSIDE]["status"] == "pending"
    assert not any(event["kind"] == "node_start" and event["node_key"] == ASSEMBLER for event in events)
    if errors or nodes[CAMERA]["status"] != "done":
        result["diagnostics"].append("native camera harness/HITL/FSM incomplete; staging rows alone do not prove completed worker execution")
        result["native_camera_boundary"]["worker_errors"] = errors
        _write(root / "native-scene-result.json", result)
        return
    needed = [name for wave in agents.agent_waves(project) for name in wave]
    missing = [name for name in needed if runner.load_checkpoint(project, name) is None]
    result.update(camera_worker_effect_verified=True,
        camera_attempt_id=starts[0]["attempt_id"],
        assembler_prerequisites={"needed": needed, "missing": missing,
            "agents_all_done": runner.agents_all_done(project), "attempted": False,
            "native_node_status": nodes[ASSEMBLER]["status"]},
        actual_project_status=after["project"]["status"], actual_camera_status=nodes[CAMERA]["status"])
    if missing:
        result["diagnostics"].append("incomplete native fanout; assembler not started: " + ", ".join(missing))
    else:
        result["diagnostics"].append("assembler needs separate capture after native camera_expand; camera probe does not prove its apply effects")
    result["diagnostics"].append("scene HITL/factory boundary not accepted; no approval supplied")
    actions_response = await client.get(prefix + "/actions")
    actions_response.raise_for_status()
    actions = actions_response.json()
    assert not actions["enabled"] and not actions["recoveries"] and not actions["records"], actions
    result["actions"] = actions
    result["managed_remained_disabled"] = True
    # A quiet window exposes native duplicate worker starts without clearing
    # stops/backoff, resetting nodes, or issuing another API launch.
    await asyncio.sleep(12)
    await observer.catch_up()
    final_events = await _history(client, prefix)
    assert len([e for e in final_events if e["node_key"] == CAMERA and e["kind"] == "node_start"]) == 1
    assert dict(fixtures.native_scene_handler.counts) == counts
    final_state = _read_state(fixtures)
    final_nodes = {row["node_key"]: row for row in final_state["nodes"]}
    assert final_nodes[CAMERA]["status"] == "done", "Native camera must remain DONE"
    assert final_nodes[ASSEMBLER]["status"] == final_nodes[OUTSIDE]["status"] == "pending"
    assert not any(e["kind"] == "node_start" and e["node_key"] in {ASSEMBLER, OUTSIDE} for e in final_events)
    for table in ("frames", "cells", "texts", "scenes", "hitl"):
        assert final_state[table] == after[table], ("Quiet window changed native rows", table)
    assert result["camera_operator_starts"] == 1
    actions_response = await client.get(prefix + "/actions")
    actions_response.raise_for_status()
    actions = actions_response.json()
    assert not actions["enabled"] and not actions["recoveries"] and not actions["records"], actions
    result["actions"] = actions
    _write(root / "native-scene-api-history.json", {"events": final_events})
    _write(root / "native-scene-final-state.json", final_state)
    camera_facts = [event for event in final_events if event["node_key"] == CAMERA]
    journal_evidence = {"status": "unknown", "required_kinds": list(CAMERA_JOURNAL_KINDS),
        "fact_counts": dict(Counter(fact["kind"] for fact in camera_facts)), "body_refs": {}}
    result["camera_journal_verification"] = journal_evidence
    _write(root / "native-scene-result.json", result)
    try:
        actual_requests = json.loads((root / "actual-worker-requests.json").read_text(encoding="utf-8"))
        actual_camera_requests = [request for request in actual_requests if request["node_key"] == CAMERA]
        assert len(actual_camera_requests) == 1, "Exactly one original transport camera POST required"
        assert actual_camera_requests[0]["body"] == camera_requests[0]["body"]
        _verify_camera_journal(observer, final_events, execution=execution, camera_node=camera_node,
            state=final_state, captured=captured, reply=reply,
            transport_request=actual_camera_requests[0], evidence=journal_evidence)
        assert journal_evidence["attempt_id"] == result["camera_attempt_id"]
        assert journal_evidence["workflow_run_id"] == final_nodes[CAMERA]["workflow_run_id"]
        assert journal_evidence["verification"] == proof
    except (AssertionError, KeyError, OSError, TypeError, ValueError) as error:
        journal_evidence.update(status="unknown", failure_type=type(error).__name__, reason=str(error))
        result["diagnostics"].append("bounded camera journal acceptance incomplete; actual native rows/status remain recorded")
        _write(root / "native-scene-result.json", result)
        raise
    result.update(accepted=True, status="accepted", bounded_camera_node_accepted=True,
        accepted_node_keys=[CAMERA], camera_journal_acceptance=True,
        one_camera_api_start=True, one_camera_model_post=True, camera_frames_preserved=True)
    _write(root / "native-scene-result.json", result)
