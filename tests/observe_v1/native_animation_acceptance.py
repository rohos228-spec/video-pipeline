
from __future__ import annotations

import asyncio
from collections import Counter
import hashlib
import json
import os
import re
import sqlite3
import sys
from threading import Lock
import time
from types import SimpleNamespace


PROJECT_ID = 2
NODE_KEY = "native-animation-prompts-A"
OUTSIDE_KEY = "outside-native-animation-videos-Z"
MODES = {"native-animation", "native-animation-corrupt"}
READ_ACTIONS = ["read_events", "read_contract", "wait", "human_review"]
MODEL = "gpt-5-6-sol"
FRAME_INPUTS = (
    {"uuid": "af" * 12, "number": 1, "sort_key": 1.0,
     "voiceover_text": "Человек открывает синюю папку на столе.",
     "image_prompt": "A person at a wooden desk opens a blue folder. Medium shot, soft window light, quiet room."},
    {"uuid": "bf" * 12, "number": 2, "sort_key": 2.0,
     "voiceover_text": "Человек закрывает папку и кладёт её на стол.",
     "image_prompt": "The same person closes the blue folder and places it on the same wooden desk. Static medium shot."},
)
ANIMATIONS = {
    FRAME_INPUTS[0]["uuid"]: "A continuous medium shot: the person slowly opens the blue folder on the wooden desk. NO VOICE.",
    FRAME_INPUTS[1]["uuid"]: "A continuous medium shot: the person closes the blue folder and gently places it on the desk. NO VOICE.",
}
_COLUMNS = "id,project_id,uuid,number,sort_key,scene_id,voiceover_text,image_prompt,animation_prompt,attrs"
_BATCH_LEAD = "Источник №1 для промт_видео — ПРОМТ КАРТИНКИ каждого кадра ниже."
_INITIAL_FILE = re.compile(r"prompt_anim_pr_\d{8}_\d{6}\.md")
_BATCH_ROW = re.compile(
    r"^Позиция \d+ метка=F\d+\nframe_uuid:\s*(\S+)\n"
    r"ID изображения:[^\n]+\nПромт картинки:\s*([^\n]+)\n"
    r"Закадровый текст:\s*([^\n]+)", re.MULTILINE,
)


def _write(root, name, value):
    (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _state(fixtures):
    with sqlite3.connect(fixtures.DB) as db:
        db.row_factory = sqlite3.Row
        frames = [dict(row) for row in db.execute(
            f"SELECT {_COLUMNS} FROM frames WHERE project_id=? ORDER BY number", (PROJECT_ID,),
        )]
        for frame in frames:
            frame["attrs"] = json.loads(frame["attrs"] or "{}")
        scenes = [dict(row) for row in db.execute(
            "SELECT id,project_id,sort_key,title,attrs FROM scenes WHERE project_id=? ORDER BY sort_key,id",
            (PROJECT_ID,),
        )]
        for scene in scenes:
            scene["attrs"] = json.loads(scene["attrs"] or "{}")
        nodes = {row[0]: row[1] for row in db.execute(
            "SELECT node_key,status FROM node_runs WHERE workflow_run_id IN "
            "(SELECT id FROM workflow_runs WHERE project_id=?)", (PROJECT_ID,),
        )}
        project = db.execute("SELECT status,auto_mode,meta,gpt_text_overrides FROM projects WHERE id=?",
                             (PROJECT_ID,)).fetchone()
    return {"frames": frames, "scenes": scenes, "nodes": nodes, "project_status": project[0],
            "auto_mode": bool(project[1]), "project_meta": json.loads(project[2] or "{}"),
            "gpt_text_overrides": json.loads(project[3] or "{}")}


def prepare(fixtures, root, mode):
    assert mode in MODES, mode
    assert "app.settings" not in sys.modules, "prepare before application settings/startup"
    project_dir = fixtures.DB.parent
    assert (project_dir / "project.xlsx").is_file(), "retain the seed's original harness workbook"
    assert not any(path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
                   for path in (project_dir / "scenes").glob("*")), "use a fresh text-only source"
    nodes = [
        {"id": NODE_KEY, "type": "animation_prompts", "position": {"x": 100, "y": 100},
         "data": {"label": "Synthetic original animation prompts", "modelId": "gpt-kie"}},
        {"id": OUTSIDE_KEY, "type": "videos", "position": {"x": 500, "y": 100},
         "data": {"label": "Outside animation scope"}},
    ]
    edges = [{"id": "native-animation-after", "source": NODE_KEY,
              "target": OUTSIDE_KEY, "data": {"kind": "after"}}]
    paths = (fixtures.DATA / "state.db", fixtures.DB)
    scene_id = 1
    for path in paths:
        with sqlite3.connect(path) as db:
            scene_id = max(scene_id, db.execute("SELECT coalesce(max(id),0)+1 FROM scenes").fetchone()[0])
    for path in paths:
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM frames WHERE project_id=?", (PROJECT_ID,)).fetchone()[0] == 0
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=?", (PROJECT_ID,)).fetchone()[0] or "{}")
            meta.setdefault("canvas_graph", {}).update(nodes=nodes, edges=edges)
            for field in ("active_excel_gpt_node_key", "step_failure", "user_stop", "mass_lane_user_stop"):
                meta.pop(field, None)
            db.execute("UPDATE projects SET status='new',auto_mode=0,script_text=?,meta=? WHERE id=?",
                       (" ".join(row["voiceover_text"] for row in FRAME_INPUTS), json.dumps(meta), PROJECT_ID))
            db.execute(
                "INSERT INTO scenes(id,project_id,sort_key,title,attrs,created_at,updated_at) "
                "VALUES(?,?,10.0,?,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (scene_id, PROJECT_ID, "Synthetic animation input scene"),
            )
            for frame in FRAME_INPUTS:
                attrs = {"camera_subdivide": {"role": "parent"}, "fixture_input": "native-animation"}
                db.execute(
                    "INSERT INTO frames(project_id,number,voiceover_text,image_prompt,animation_prompt,status,"
                    "attrs,uuid,sort_key,scene_id,created_at,updated_at) "
                    "VALUES(?,?,?,?,NULL,'planned',?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (PROJECT_ID, frame["number"], frame["voiceover_text"], frame["image_prompt"],
                     json.dumps(attrs), frame["uuid"], frame["sort_key"], scene_id),
                )
            run_ids = [row[0] for row in db.execute("SELECT id FROM workflow_runs WHERE project_id=?", (PROJECT_ID,))]
            assert len(run_ids) == 1, "fresh source must have one unambiguous workflow run"
            run_id = run_ids[0]
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                       (json.dumps(nodes), json.dumps(edges), run_id))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
            for node in nodes:
                db.execute(
                    "INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,"
                    "created_at,updated_at) VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (run_id, node["id"], node["type"]),
                )
    os.environ["HARNESS_GATE_DISABLED"] = "false"
    config = {"version": 1, "mode": "synthetic", "online_enabled": False,
              "endpoint": "http://127.0.0.1:18766/codex/v1/responses",
              "decision": {"catalog_id": "gpt-kie", "model_id": MODEL,
                           "reasoning_effort": "low", "max_output_tokens": 1024, "timeout_s": 30},
              "profiles": {}, "allowed_actions": READ_ACTIONS}
    config_path = root / "synthetic-native-animation-runtime.json"
    config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(config_path)
    fixtures.NODES = (NODE_KEY, OUTSIDE_KEY)
    prepared = {
        "scenario": mode, "node_type": "animation_prompts", "node_key": NODE_KEY,
        "outside": OUTSIDE_KEY, "expect_corrupt": mode.endswith("-corrupt"),
        "scope": [NODE_KEY], "admission": "observation_only", "managed_eligible": False,
        "request_allowance": 2, "native_attempt_allowance": 1,
        "config_sha256": hashlib.sha256(config_path.read_bytes()).hexdigest(),
        "harness_enabled": True, "primary_png_count": 0,
        "scene_id": scene_id,
        "native_input_boundary": "after backfill/unpack, before pending collection",
        "startup_status_policy": "initial new or original compute_actual_status; no animation execution/output",
        "initial_state": _state(fixtures),
        "interventions": [
            "fresh synthetic graph, Scene and two linked parent Frame inputs in both DB replicas before startup",
            "UUID/order/scene links/attrs initialized before startup; no patches after native input capture",
            "image prompts and VO are source inputs; animation prompts remain NULL; nodes pending",
            "original workbook retained, manual auto_mode=False, original harness enabled",
            "startup may derive input status; original animation data guard checked before the one API start",
            "explicit existing gpt-kie model choice; loopback HTTP responses only",
            "observation API plus one original anim_pr step API start; no managed admission",
            "corrupt mode: one original stop API call after first actual node error to bound retries",
        ],
    }
    fixtures.native_animation_prepared = prepared
    _write(root, "native-animation-prepared.json", prepared)
    return prepared


def _input_text(body):
    inputs = body.get("input")
    assert isinstance(inputs, list) and inputs, "original Responses input must be present"
    current = next((item for item in reversed(inputs) if item.get("role") == "user"), None)
    assert current is not None, "current original user input unavailable"
    content = current.get("content")
    if isinstance(content, str):
        return content
    assert isinstance(content, list) and content
    assert all(part.get("type") == "input_text" for part in content), "text-only native batch must not invent vision inputs"
    return "\n".join(part["text"] for part in content)


def handler_for(fixtures, root, mode=None, *, base_handler=None):
    prepared = fixtures.native_animation_prepared
    assert mode is None or mode == prepared["scenario"]

    class Handler(base_handler or fixtures.FixtureHandler):
        counts = Counter()
        response_ids = []
        requests = []
        violations = []
        lock = Lock()

        def do_GET(self):
            self.send_error(404, "No retrieval/salvage payload in native animation fixture")

        def do_POST(self):
            body = None
            try:
                assert self.path == "/codex/v1/responses", self.path
                node = self.headers.get("x-diagnostic-node", "")
                assert node == NODE_KEY, "model request outside animation node"
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
                assert body["model"] == MODEL and body["stream"] is True
                text = _input_text(body)
                _, initial_separator, initial_message = text.rpartition("\n\n## Сопроводительный текст\n")
                initial_file = _INITIAL_FILE.search(initial_message) if initial_separator else None
                if initial_file and not _BATCH_ROW.search(initial_message):
                    phase, message = "phase1", initial_message
                    batch = []
                else:
                    prefix, separator, tail = text.rpartition("\n\n" + _BATCH_LEAD)
                    assert separator and prefix.startswith("[Инструкция]\n"), "original batch system/build_input boundary unavailable"
                    phase, message = "shot01_batch", _BATCH_LEAD + tail
                    batch = list(_BATCH_ROW.finditer(message))
                with self.lock:
                    expected_phase = "phase1" if not self.requests else "shot01_batch"
                    assert len(self.requests) < 2 and phase == expected_phase, "bounded fixture received repeated or unexpected native phase"
                    if phase == "phase1":
                        assert initial_file is not None, "phase1 must carry the original master prompt file context"
                        answer = "Ready to receive the original frame batch."
                    else:
                        expected = {frame["uuid"]: frame for frame in FRAME_INPUTS}
                        supplied = [match.group(1) for match in batch]
                        assert supplied == [frame["uuid"] for frame in FRAME_INPUTS], supplied
                        assert "Картинок/ленты нет" in message, "original text-only batch marker unavailable"
                        for match in batch:
                            frame = expected[match.group(1)]
                            assert match.group(2) == frame["image_prompt"]
                            assert match.group(3) == frame["voiceover_text"]
                        answer = '{"ops": }' if prepared["expect_corrupt"] else json.dumps({"ops": [
                            {"frame_uuid": uid, "fields": {"промт_видео": ANIMATIONS[uid]}}
                            for uid in reversed(supplied)
                        ]}, ensure_ascii=False)
                    ordinal = len(self.requests) + 1
                    response_id = f"resp_native_animation_{prepared['scenario']}_{ordinal}"
                    self.counts[node] += 1
                    self.response_ids.append(response_id)
                    self.requests.append({"node_key": node, "phase": phase, "body": body,
                                          "body_sha256": _digest(body), "response_id": response_id,
                                          "current_input_sha256": _digest(text), "message": message,
                                          "message_sha256": _digest(message),
                                          "response_text": answer, "http_status": 200})
                    _write(root, "fixture-native-animation-requests.json", self.requests)
                response = {"id": response_id, "status": "completed",
                            "output": [{"content": [{"type": "output_text", "text": answer}]}],
                            "usage": {"input_tokens": 0, "output_tokens": 0, "fixture": True}}
                events = [{"type": "response.created", "response": {"id": response_id}},
                          {"type": "response.output_text.delta", "delta": answer},
                          {"type": "response.output_text.done", "text": answer},
                          {"type": "response.completed", "response": response}]
                payload = "".join("data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
                                  for event in events).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
            except AssertionError as exc:
                with self.lock:
                    self.violations.append({"path": self.path, "error": str(exc), "body": body})
                    _write(root, "fixture-native-animation-violations.json", self.violations)
                self.send_error(422, "Native animation fixture contract violation")

    return Handler


async def _history(client, prefix):
    events, after = [], 0
    for _ in range(100):
        response = await client.get(prefix + "/events", params={"after": after, "limit": 500})
        response.raise_for_status()
        page = response.json()
        events.extend(page["events"])
        if not page["has_more"]:
            return events
        assert page["next_cursor"] > after
        after = page["next_cursor"]
    raise AssertionError("bounded animation event pagination exceeded")


async def run(client, fixtures, observer, root, result, prepared, *, browser=False):
    result.update(accepted=False, operator_starts=0, operator_stops=0,
                  admission="observation_only", managed_eligible=False)
    from app.models import Project, ProjectStatus
    from app.project_db import project_db_session_scope
    from app.services import animation_prompt_gpt as native_animation, gpt_api
    from app.services.native_animation_contracts import animation_prompt_input_snapshot, verify_animation_prompts
    from app.services.node_capabilities import capability_for
    from app.services.project_state import compute_actual_status
    from app.services.step_data_guard import can_enter_running
    from app.telegram.menu import step_by_code

    assert not browser, "initial native animation acceptance uses original HTTP step API"
    assert "retry_profile" not in capability_for("animation_prompts").actions, "no profile retry for animation"
    state_before = _state(fixtures)
    assert state_before["frames"] == prepared["initial_state"]["frames"], "source Frame inputs changed after preparation"
    assert state_before["scenes"] == prepared["initial_state"]["scenes"]
    assert all(row["scene_id"] == prepared["scene_id"] for row in state_before["frames"])
    assert state_before["gpt_text_overrides"] == prepared["initial_state"]["gpt_text_overrides"]
    assert state_before["nodes"] == {NODE_KEY: "pending", OUTSIDE_KEY: "pending"}
    assert state_before["auto_mode"] is False
    assert all(row["animation_prompt"] is None for row in state_before["frames"])
    async with project_db_session_scope(PROJECT_ID) as input_session:
        native_project = await input_session.get(Project, PROJECT_ID)
        assert native_project is not None and native_project.auto_mode is False
        actual_status = await compute_actual_status(input_session, native_project)
        ok, reason, suggested_status = await can_enter_running(
            input_session, native_project, ProjectStatus.generating_animation_prompts,
        )
        step = step_by_code("anim_pr")
        assert step is not None and step.running_status is ProjectStatus.generating_animation_prompts
        admission_calibration = {
            "original_guard": "step_data_guard.can_enter_running",
            "allowed": ok, "reason": reason,
            "suggested_status": suggested_status.value if suggested_status is not None else None,
            "original_actual_status": actual_status.value,
            "baseline_project_status": state_before["project_status"],
            "declared_step_requires": step.requires.value if step.requires is not None else None,
            "primary_png_count": prepared["primary_png_count"],
            "status_or_progress_writes": False,
            "managed_eligible": False,
        }
    _write(root, "native-animation-baseline-after-startup.json", state_before)
    _write(root, "native-animation-admission-calibration.json", admission_calibration)
    assert ok, admission_calibration
    assert state_before["project_status"] in {"new", actual_status.value}, admission_calibration
    assert actual_status is ProjectStatus.script_ready, "the initial inputs have no split-completion claim or animation output"
    guard_state = _state(fixtures)
    for field in ("frames", "scenes", "nodes", "auto_mode", "gpt_text_overrides"):
        assert guard_state[field] == state_before[field], (field, "guard calibration changed source inputs")
    project = SimpleNamespace(id=PROJECT_ID, data_dir=fixtures.DB.parent,
                              gpt_text_overrides=state_before["gpt_text_overrides"])
    snapshot = animation_prompt_input_snapshot(
        [SimpleNamespace(**row) for row in state_before["frames"]], project_id=PROJECT_ID, project=project,
    )
    _write(root, "native-animation-input.json", snapshot)
    base = "http://127.0.0.1:18765/api/projects/2"
    scope_response = await client.post(base + "/observations", json={"node_keys": [NODE_KEY]})
    assert scope_response.status_code == 201, (scope_response.status_code, scope_response.text)
    scope = scope_response.json()
    assert scope["mode"] == "observe" and scope["node_keys"] == [NODE_KEY], scope
    prefix = base + "/observations/" + scope["execution_id"]
    result.update(execution_id=scope["execution_id"], observation_scope=scope,
                  admission="observation_only", managed_launch=None, managed_eligible=False,
                  operator_starts=0, operator_stops=0, scope_boundary="original manual API; auto_mode=False",
                  interventions=prepared["interventions"], native_exhaustion_tested=False,
                  outage_backoff_tested=False, local_fallback_tested=False,
                  baseline_after_startup=state_before, admission_calibration=admission_calibration)
    start_params = {"node_key": NODE_KEY, "dry_run": False, "mode": "resume", "force_wipe": False}
    _write(root, "native-animation-api-start-request.json",
           {"method": "POST", "url": base + "/steps/anim_pr/run", "params": start_params})
    response = await client.post(base + "/steps/anim_pr/run", params=start_params)
    result["operator_starts"] = 1
    start_response = response.json()
    _write(root, "native-animation-api-start.json", start_response)
    _write(root, "native-animation-state-after-api-start.json", _state(fixtures))
    response.raise_for_status()
    assert start_response["status"] in {"generating_animation_prompts", "animation_prompts_ready"}, (
        "original API did not dispatch animation; this is not a worker logging RED", start_response,
    )
    deadline = time.monotonic() + 90
    reached = False
    while time.monotonic() < deadline:
        state = _state(fixtures)
        await observer.catch_up()
        with sqlite3.connect(fixtures.DATA / "state.db") as db:
            errors = db.execute("SELECT count(*) FROM observation_events WHERE execution_id=? AND node_key=? AND kind='node_error'",
                                (scope["execution_id"], NODE_KEY)).fetchone()[0]
        if prepared["expect_corrupt"] and errors:
            stop = await client.post(base + "/stop")
            stop.raise_for_status()
            result["operator_stops"] = 1
            result["negative_boundary"] = "original stop API after first native parser error; no exhaustion or retry action"
            _write(root, "native-animation-api-stop.json", stop.json())
            reached = True
            break
        if not prepared["expect_corrupt"] and (state["nodes"].get(NODE_KEY) in {"done", "failed"} or errors):
            reached = True
            break
        await asyncio.sleep(.1)
    assert reached, "original animation worker did not reach a bounded terminal/error boundary"
    await asyncio.sleep(2)
    await observer.catch_up()
    state = _state(fixtures)
    history = await _history(client, prefix)
    _write(root, "api-history.json", {"events": history})
    _write(root, "native-animation-committed-output.json", state)
    assert not (root / "fixture-native-animation-violations.json").exists(), "unexpected/repeated HTTP request"
    requests = json.loads((root / "fixture-native-animation-requests.json").read_text(encoding="utf-8"))
    assert len(requests) == 2 and [request["phase"] for request in requests] == ["phase1", "shot01_batch"]
    assert all(request["node_key"] == NODE_KEY and request["body"]["model"] == MODEL for request in requests)
    assert requests[0]["current_input_sha256"] != requests[1]["current_input_sha256"]
    assert requests[0]["message_sha256"] != requests[1]["message_sha256"]
    native_frames = [SimpleNamespace(**row) for row in state_before["frames"]]
    prompt_name = _INITIAL_FILE.search(requests[0]["message"]).group(0)
    master = (project.data_dir / "tmp_gpt" / prompt_name).read_text(encoding="utf-8")
    initial_message = native_animation.build_initial_message(project, native_frames, prompt_file_name=prompt_name)
    batch_items = [native_animation.FrameImageBatchItem(
        frame=frame, image_path=None,
        image_id=native_animation.image_id_for_frame(project, frame, None),
        voiceover=native_animation.voiceover_for_frame(project, frame),
    ) for frame in native_frames]
    batch_message = native_animation.build_batch_message(batch_items)
    assert requests[0]["message"] == initial_message
    assert requests[1]["message"] == batch_message
    master_system = master.strip()
    if len(master_system) > 12_000:
        master_system = master_system[:12_000] + "\n…"
    expected_inputs = [gpt_api.build_input(prompt=master, accompanying=initial_message, input_paths=[]),
                       gpt_api.build_input(prompt=batch_message, input_paths=[], system=master_system or None)]
    for request, expected_input in zip(requests, expected_inputs):
        assert request["body"]["input"] == expected_input, request["phase"]
    calibration = {"original_message_builders": True, "original_build_input": True,
                   "distinct_current_inputs": True, "distinct_messages": True,
                   "phases": [{"phase": request["phase"], "input_sha256": _digest(request["body"]["input"]),
                               "message_sha256": request["message_sha256"]} for request in requests]}
    _write(root, "native-animation-phase-calibration.json", calibration)
    assert state["nodes"][OUTSIDE_KEY] == "pending" and state["auto_mode"] is False
    assert state["scenes"] == state_before["scenes"]
    assert state["gpt_text_overrides"] == state_before["gpt_text_overrides"]
    assert len(state["frames"]) == 2
    for before, after in zip(state_before["frames"], state["frames"]):
        for field in before:
            if field != "animation_prompt":
                assert after[field] == before[field], (field, before, after)
    proof = verify_animation_prompts([SimpleNamespace(**row) for row in state["frames"]],
                                    project_id=PROJECT_ID, project=project, snapshot=snapshot)
    expected_status = "failed" if prepared["expect_corrupt"] else "passed"
    assert proof["status"] == expected_status, proof
    facts = [event for event in history if event["node_key"] == NODE_KEY]
    starts = [event for event in facts if event["kind"] == "node_start"]
    assert len(starts) == 1, "one original worker attempt only; native retry/exhaustion is outside this case"
    attempt = starts[0]["attempt_id"]
    assert all(event["attempt_id"] == attempt for event in facts if event["kind"] in {
        "model_start", "model_end", "parse_result", "data_applied", "node_result_verification"})
    model_starts = [event for event in facts if event["kind"] == "model_start"]
    model_ends = [event for event in facts if event["kind"] == "model_end"]
    assert len(model_starts) == len(model_ends) == 2
    request_ids = {event["request_id"] for event in model_starts}
    assert None not in request_ids and len(request_ids) == 2
    assert {event["request_id"] for event in model_ends} == request_ids
    assert {event["payload"]["response_id"] for event in model_ends} == {request["response_id"] for request in requests}
    assert all(event["payload"]["complete"] is True for event in model_ends)
    by_response = {event["payload"]["response_id"]: event for event in model_ends}
    batch_request_id = by_response[requests[1]["response_id"]]["request_id"]
    parsed = [event for event in facts if event["kind"] == "parse_result"]
    assert len(parsed) == 1 and parsed[0]["request_id"] == batch_request_id
    assert parsed[0]["payload"]["schema_valid"] is (not prepared["expect_corrupt"])
    recorded = [event for event in facts if event["kind"] == "node_result_verification"]
    assert len(recorded) == 1 and recorded[0]["payload"]["status"] == expected_status
    assert recorded[0]["payload"]["input_sha256"] == proof["input_sha256"]
    assert recorded[0]["payload"]["frame_set_sha256"] == proof["frame_set_sha256"]
    assert len([event for event in facts if event["kind"] == "node_input"]) == 1
    assert len([event for event in facts if event["kind"] == "node_output"]) == 1
    applied = [event for event in facts if event["kind"] == "data_applied"
               and event.get("db_scope") == "project" and event["payload"].get("transactional") is True]
    if prepared["expect_corrupt"]:
        assert all(row["animation_prompt"] is None for row in state["frames"])
        assert state["nodes"][NODE_KEY] != "done" and not applied
        node_errors = [event for event in facts if event["kind"] == "node_error"]
        assert len(node_errors) == 1 and node_errors[0]["payload"]["error_type"] == "RuntimeError"
        failures = state["project_meta"].get("step_failure") or {}
        assert not failures.get("abandoned_at"), "native exhaustion is outside this bounded negative"
    else:
        assert state["nodes"][NODE_KEY] == "done" and state["project_status"] == "animation_prompts_ready"
        assert all(row["animation_prompt"] == ANIMATIONS[row["uuid"]]
                   and len(row["animation_prompt"].strip()) >= 10
                   and not row["animation_prompt"].lstrip().startswith("{") for row in state["frames"])
        assert len(applied) == 1 and applied[0]["request_id"] == parsed[0]["request_id"]
        assert applied[0]["payload"].get("result", {}).get("ok") is True
        assert not any(event["kind"] in {"node_error", "model_error", "apply_error"} for event in facts)
        gates = [event for event in facts if event["kind"] == "runtime_check"
                 and event["payload"].get("step") == "anim_pr"]
        assert gates and any(event["payload"].get("performed") is True
                             and event["payload"].get("status") == "passed" for event in gates)
    for checked in (proof, recorded[0]["payload"]):
        assert all(checked[key] is False for key in (
            "business_commit_claim", "semantic_quality_verified", "video_quality_verified", "managed_eligible"))
    actions_response = await client.get(prefix + "/actions")
    actions_response.raise_for_status()
    actions = actions_response.json()
    assert not actions["configured"] and not actions["enabled"] and not actions["recoveries"]
    with sqlite3.connect(fixtures.DATA / "state.db") as db:
        assert db.execute("SELECT status FROM projects WHERE id=1").fetchone()[0] == "new"
    result.update(accepted=True, bounded_original_worker_acceptance=True,
                  verification=recorded[0]["payload"], independent_committed_rows_verification=proof,
                  native_state=state, fixture_requests=len(requests), native_attempts=1,
                  phase_calibration=calibration, native_input_boundary=prepared["native_input_boundary"],
                  business_apply_count=len(applied), managed_eligible=False,
                  semantic_quality_verified=False, video_quality_verified=False,
                  actions=actions, committed_output_sha256=_digest(state["frames"]))
    _write(root, "native-animation-result.json", result)
