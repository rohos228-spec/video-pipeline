from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit


MODE = "native-entity-observe"
NODE = "native-hero-entity-A"
OUTSIDE = "outside-native-entity-items-Z"
PROJECT_ID = 2
CHARACTER_CODE = "c01"
IMAGE_SIZE = (1024, 576)
PROMPT = (
    "A dense 16:9 character model sheet of Mira on a pure white background. "
    "Show full-body front, three-quarter, side and back views, plus head turnaround. "
    "Keep short dark hair, work clothes, natural proportions and a curious expression. "
    "No scenery, text, labels or captions. Consistent clothing and identity across all views."
)


def _save(root, name, value):
    (root / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def _reader(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _entity_rows(path):
    with closing(_reader(path)) as db:
        rows = db.execute("SELECT id,project_id,type,code,name,sort_key,attrs FROM entities "
            "WHERE project_id=? AND type='character' ORDER BY sort_key,id", (PROJECT_ID,)).fetchall()
    return [{"id": identity, "project_id": project, "type": typ, "code": code,
             "name": name, "sort_key": order, "attrs": json.loads(attrs or "{}")}
            for identity, project, typ, code, name, order, attrs in rows]


def _state(path, run_id):
    with closing(_reader(path)) as db:
        status, auto_mode, meta = db.execute("SELECT status,auto_mode,meta FROM projects WHERE id=?",
                                              (PROJECT_ID,)).fetchone()
        nodes = dict(db.execute("SELECT node_key,status FROM node_runs WHERE workflow_run_id=?", (run_id,)))
        artifacts = db.execute("SELECT id,project_id,frame_id,kind,uuid,path,meta FROM artifacts "
            "WHERE project_id=? ORDER BY id", (PROJECT_ID,)).fetchall()
        hitl = db.execute("SELECT kind,decision,payload FROM hitl_requests WHERE project_id=?",
                          (PROJECT_ID,)).fetchall()
        pilot = db.execute("SELECT status FROM projects WHERE id=1").fetchone()
        frames = db.execute("SELECT uuid,number,voiceover_text,attrs FROM frames "
                            "WHERE project_id=? ORDER BY number", (PROJECT_ID,)).fetchall()
    return {"project_status": status, "auto_mode": bool(auto_mode), "meta": json.loads(meta or "{}"), "nodes": nodes,
        "artifacts": [{"id": identity, "project_id": project, "frame_id": frame, "kind": kind,
                       "uuid": uid, "path": path, "meta": json.loads(metadata or "{}")}
                      for identity, project, frame, kind, uid, path, metadata in artifacts],
        "hitl": [{"kind": kind, "decision": decision, "payload": json.loads(payload or "{}")}
                 for kind, decision, payload in hitl],
        "pilot_status": pilot[0] if pilot else None, "frames": frames}


def _observe_state(path, execution, run_id):
    with closing(_reader(path)) as db:
        scope = db.execute("SELECT project_id,workflow_run_id,node_keys,mode,observer_state,gaps "
            "FROM observation_executions WHERE execution_id=?", (execution,)).fetchone()
        assert scope, "the real enable API must persist the observation scope"
        project, workflow, nodes, mode, observer_state, gaps = scope
        assert (project, workflow, json.loads(nodes), mode) == (PROJECT_ID, run_id, [NODE], "observe"), scope
        assert observer_state == "active" and json.loads(gaps or "[]") == [], scope
        assert not db.execute("SELECT 1 FROM action_execution_controls WHERE execution_id=?",
                              (execution,)).fetchone(), "observe-only case must not enable Actions"


async def _events(client, base, execution):
    history, after = [], 0
    while True:
        response = await client.get(base + "/observations/" + execution + "/events",
                                    params={"after": after, "limit": 500})
        response.raise_for_status()
        page = response.json()
        history.extend(page["events"])
        if not page["has_more"]:
            return history
        assert page["next_cursor"] > after, "observation pagination must advance"
        after = page["next_cursor"]


def prepare(fixtures, root, mode):
    assert mode == MODE, "this draft accepts only the bounded observe-only hero mode"
    assert Path("/.dockerenv").is_file(), "parent executes acceptance only in its isolated container"
    assert "app.settings" not in sys.modules, "prepare before immutable settings are loaded"
    root = Path(root)
    paths = {"master": fixtures.DATA / "state.db", "project": fixtures.DB}
    for path in paths.values():
        assert Path(path).resolve().is_relative_to(root.resolve()), "use the existing runner's copied seed"
        with closing(_reader(path)) as db:
            assert db.execute("SELECT count(*) FROM workflow_runs WHERE project_id=?",
                              (PROJECT_ID,)).fetchone()[0] == 1, "one existing immutable run required"
            assert db.execute("SELECT count(*) FROM artifacts WHERE project_id=?", (PROJECT_ID,)).fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM entities WHERE project_id=? AND type='character'",
                              (PROJECT_ID,)).fetchone()[0] == 0
            assert db.execute("SELECT count(*) FROM hitl_requests WHERE project_id=?", (PROJECT_ID,)).fetchone()[0] == 0
            assert not db.execute("SELECT 1 FROM entities WHERE id=99001").fetchone()
    assert not any((fixtures.DB.parent / "characters").glob("*.png")), "no preexisting native hero cache"

    nodes = [{"id": NODE, "type": "hero", "position": {"x": 100, "y": 100},
              "data": {"label": "Synthetic Entity hero"}},
             {"id": OUTSIDE, "type": "items", "position": {"x": 500, "y": 100},
              "data": {"label": "Outside observe-only case"}}]
    edges = [{"id": "native-entity-after", "source": NODE, "target": OUTSIDE, "data": {"kind": "after"}}]
    run_ids = {}
    for name, path in paths.items():
        with sqlite3.connect(path) as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=?", (PROJECT_ID,)).fetchone()[0])
            meta["canvas_graph"].update(nodes=nodes, edges=edges)
            for key in ("active_excel_gpt_node_key", "step_failure", "excel_hero", "hero_skipped_empty"):
                meta.pop(key, None)
            meta.update(excel_hero_enabled=True, img_streams=1, outsee_streams=1)
            db.execute("UPDATE projects SET status='new',auto_mode=0,hero_mode='auto',hero_count=NULL,"
                "hero_description=NULL,hero_descriptions='[]',hero_variations='[]',meta=? WHERE id=?",
                (json.dumps(meta, ensure_ascii=False), PROJECT_ID))
            db.execute("INSERT INTO entities(id,project_id,type,code,name,sort_key,attrs,created_at,updated_at) "
                "VALUES(99001,?,'character',?,'Mira',1.0,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                (PROJECT_ID, CHARACTER_CODE, json.dumps({"внешность": "Short dark hair",
                    "одежда": "Work clothes", "характер": "Curious", "правила": "Base character."},
                    ensure_ascii=False)))
            run_id = db.execute("SELECT id FROM workflow_runs WHERE project_id=? ORDER BY id DESC LIMIT 1",
                                (PROJECT_ID,)).fetchone()[0]
            run_ids[name] = run_id
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                       (json.dumps(nodes), json.dumps(edges), run_id))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run_id,))
            for node in nodes:
                db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,"
                    "created_at,updated_at) VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                    (run_id, node["id"], node["type"]))

    os.environ.update(IMAGE_PROVIDER="outsee", OUTSEE_API_KEY="synthetic-native-entity-token",
                      OUTSEE_API_BASE_URL="http://127.0.0.1:18766", OUTSEE_HTTP_API="true",
                      OUTSEE_HTTP_FALLBACK_CDP="false")
    assert os.environ.get("TEXT_LLM_PROVIDER") == "kie", "retain the existing group runner's native GPT route"
    assert os.environ.get("GPT_BASE_URL") == "http://127.0.0.1:18766"
    assert os.environ.get("GPT_CHAT_PATH") == "/codex/v1/responses"
    assert os.environ.get("GPT_MODEL") == "gpt-5-6-sol" and os.environ.get("GPT_API_MODE") == "responses"
    assert os.environ.get("OUTSEE_DEFAULT_IMAGE_MODEL", "gpt-image-2") == "gpt-image-2", "retain the native API default"
    config = {"version": 1, "mode": "synthetic", "online_enabled": False,
              "endpoint": None, "decision": None, "profiles": {}, "allowed_actions": []}
    config_path = root / "synthetic-native-entity-runtime.json"
    _save(root, config_path.name, config)
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(config_path)
    fixtures.NODES = (NODE, OUTSIDE)
    fixtures.synthetic_answer = lambda node, attempt: PROMPT if node == NODE else "Unexpected outside request"
    prepared = {"scenario": mode, "node_type": "hero", "node_key": NODE, "outside": OUTSIDE,
        "project_id": PROJECT_ID, "run_ids": run_ids, "entity_code": CHARACTER_CODE,
        "scope": "observe-only original Entity hero, one base character, manual API start",
        "managed": False, "native_reference_uploads_covered": False,
        "shared_media_counter_verified": False, "native_generation_provenance_complete": False,
        "interventions": ["existing group runner copies its original synthetic seed",
            "new/pending initial graph and one Entity input before startup; no completed statuses seeded",
            "original prompt slots retained; original default image catalog choice and one native stream",
            "existing loopback transport only; no native functions patched",
            "one observation enable and one original hero start; no Actions enable/recovery or Decision",
            "read both DB scopes after startup; no DB repair or inferred Artifact provenance"]}
    prepared["initial"] = {name: _state(path, run_ids[name]) for name, path in paths.items()}
    assert run_ids["master"] == run_ids["project"], "native journal scopes must bind the same existing run"
    for value in prepared["initial"].values():
        assert value["project_status"] == "new" and value["auto_mode"] is False
        assert value["nodes"] == {NODE: "pending", OUTSIDE: "pending"}
    _save(root, "native-entity-prepared.json", prepared)
    return prepared


def handler_for(fixtures, root):
    from PIL import Image

    root = Path(root)
    assert not fixtures.FixtureHandler.counts and not fixtures.FixtureHandler.response_ids, "fresh existing transport required"
    stream = io.BytesIO()
    Image.new("RGB", IMAGE_SIZE, "steelblue").save(stream, "PNG", compress_level=0)
    image_bytes = stream.getvalue()
    assert 200_000 <= len(image_bytes) < 256 * 1024 * 1024
    assert IMAGE_SIZE[0] * IMAGE_SIZE[1] < 40_000_000
    transport_lock = threading.Lock()

    class Handler(fixtures.FixtureHandler):
        media_counts = Counter()
        transport = []
        jobs = {}
        image_sha256 = hashlib.sha256(image_bytes).hexdigest()

        def record(self, entry):
            with transport_lock:
                entry = {"fixture_http_ordinal": len(type(self).transport) + 1, **entry}
                type(self).transport.append(entry)
                _save(root, "native-entity-transport.json", type(self).transport)

        def response(self, body, content_type="application/json"):
            content = json.dumps(body).encode() if isinstance(body, dict) else body
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_POST(self):
            path = urlsplit(self.path).path
            assert self.headers.get("x-diagnostic-node") == NODE, "existing runner must bind POST to native worker scope"
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.loads(raw)
            if path == "/codex/v1/responses":
                self.record({"method": "POST", "path": path, "node_key": NODE, "body": body})
                original_stream = self.rfile
                self.rfile = io.BytesIO(raw)
                try:
                    returned = super().do_POST()
                finally:
                    self.rfile = original_stream
                self.record({"kind": "fixture_model_response", "response_id": type(self).response_ids[-1],
                             "sha256": hashlib.sha256(PROMPT.encode()).hexdigest()})
                return returned
            if path != "/api/v1/images/generate":
                self.record({"method": "POST", "path": path, "unexpected": True})
                return self.send_error(404)
            assert body["model"] == "gpt-image-2", body
            assert body["aspect_ratio"] == "16:9", body
            assert body["resolution"] == "2K", body
            assert not body.get("image_urls") and not body.get("reference_images"), "base-only case must not upload references"
            assert "P2-EXCEL-c01-" in body["prompt"] and PROMPT in body["prompt"]
            type(self).media_counts["generation_submit"] += 1
            job_id = "synthetic-native-entity-" + str(type(self).media_counts["generation_submit"])
            type(self).jobs[job_id] = body
            response = {"id": job_id, "status": "queued"}
            self.record({"method": "POST", "path": path, "node_key": NODE, "body": body,
                         "fixture_response": response})
            return self.response(response)

        def do_GET(self):
            path = urlsplit(self.path).path
            if path.startswith("/api/v1/generations/"):
                job_id = path.rsplit("/", 1)[-1]
                assert job_id in type(self).jobs
                type(self).media_counts["status"] += 1
                response = {"id": job_id, "status": "completed",
                            "result_url": "http://127.0.0.1:18766/artifacts/" + job_id + ".png"}
                self.record({"method": "GET", "path": path, "fixture_response": response})
                return self.response(response)
            if path.startswith("/artifacts/") and path.endswith(".png"):
                job_id = path.rsplit("/", 1)[-1][:-4]
                assert job_id in type(self).jobs
                type(self).media_counts["download"] += 1
                self.record({"method": "GET", "path": path, "bytes": len(image_bytes),
                             "sha256": type(self).image_sha256})
                return self.response(image_bytes, "image/png")
            self.record({"method": "GET", "path": path, "unexpected": True})
            return super().do_GET()

    return Handler


async def run(client, fixtures, observer, root, result, prepared, handler):
    from app.services.native_entity_contracts import hero_entity_input_snapshot, verify_hero_entity_results
    from app.services.node_capabilities import capability_for
    from app.settings import settings

    root = Path(root)
    base = "http://127.0.0.1:18765/api/projects/2"
    paths = {"master": fixtures.DATA / "state.db", "project": fixtures.DB}
    assert prepared["scenario"] == MODE and "retry_profile" not in capability_for("hero").actions
    assert settings.resolved_text_llm_provider() == "kie"
    assert settings.gpt_api_effective_base_url == "http://127.0.0.1:18766"
    assert settings.gpt_chat_path_effective == "/codex/v1/responses"
    assert settings.gpt_model_effective == "gpt-5-6-sol" and settings.gpt_api_mode_effective == "responses"
    assert settings.image_provider == "outsee" and settings.outsee_http_api is True
    assert settings.outsee_api_base_url == "http://127.0.0.1:18766"
    assert settings.outsee_default_image_model == "gpt-image-2" and settings.outsee_http_fallback_cdp is False
    rows = _entity_rows(fixtures.DB)
    input_snapshot = hero_entity_input_snapshot([SimpleNamespace(**row) for row in rows], project_id=PROJECT_ID)
    assert len(input_snapshot["characters"]) == 1
    assert input_snapshot["characters"][0]["role"] == "base"
    assert input_snapshot["characters"][0]["ref_ids"] == []
    assert _entity_rows(paths["master"]) == rows, "fresh inputs must agree across native session scopes"
    _save(root, "native-entity-input.json", input_snapshot)

    result.update(accepted=False, operator_starts=0, interventions=prepared["interventions"],
        scope_boundary="observe-only manual entry; auto_mode false; existing runner stops application",
        acceptance_scope=prepared["scope"], managed=False, semantic_quality_verified=False,
        shared_media_counter_verified=False, native_generation_provenance_complete=False)
    enabled = await client.post(base + "/observations", json={"node_keys": [NODE]})
    assert enabled.is_success, (enabled.status_code, enabled.text)
    execution = enabled.json()["execution_id"]
    result["execution_id"] = execution
    _observe_state(paths["master"], execution, prepared["run_ids"]["master"])
    response = await client.post(base + "/steps/hero/run", params={"node_key": NODE, "mode": "resume", "force_wipe": False})
    result["original_start"] = {"status_code": response.status_code, "body": response.json()}
    assert response.is_success, (response.status_code, response.text)
    result["operator_starts"] = 1

    deadline = time.monotonic() + 120
    state = {}
    while time.monotonic() < deadline:
        state = {name: _state(path, prepared["run_ids"][name]) for name, path in paths.items()}
        if any(value["nodes"].get(NODE) == "failed" or value["meta"].get("step_failure")
               for value in state.values()):
            break
        if all(value["project_status"] == "hero_ready" and value["nodes"].get(NODE) == "done"
               for value in state.values()):
            break
        await asyncio.sleep(.25)
    result["native_state"] = state
    _save(root, "native-entity-db-state.json", state)
    await observer.catch_up()
    history = await _events(client, base, execution)
    _save(root, "api-history.json", {"events": history})
    result["transport_counts"] = {"model": dict(handler.counts), "media": dict(handler.media_counts)}
    assert state["project"]["project_status"] == "hero_ready", state
    assert state["project"]["nodes"][NODE] == "done", state
    for name, value in state.items():
        assert value["project_status"] == "hero_ready" and value["nodes"][NODE] == "done", (name, value)
        assert value["auto_mode"] is False
        assert value["nodes"][OUTSIDE] == "pending", (name, value)
        assert value["hitl"] == [], "Entity batch has no legacy HITL request"
        assert value["frames"] == prepared["initial"][name]["frames"], "hero must preserve frame input"
        assert value["pilot_status"] == prepared["initial"][name]["pilot_status"]
        assert _entity_rows(paths[name]) == rows, "input identities and native descriptions must survive"
    cfg = state["project"]["meta"]["excel_hero"]
    expected_characters = [{key: value for key, value in ch.items() if key not in {"entity_id", "role"}}
                           for ch in input_snapshot["characters"]]
    assert cfg == {"source": "entity", "characters": expected_characters}, "worker must reuse the original parser and input fields"
    assert not state["project"]["meta"].get("hero_skipped_empty"), "native parser must not skip the supplied Entity"

    artifacts = state["master"]["artifacts"]
    assert len(artifacts) == 1 and not state["project"]["artifacts"], state
    artifact = artifacts[0]
    assert artifact["kind"] == "hero_reference" and artifact["frame_id"] is None
    assert artifact["meta"]["excel_id"] == CHARACTER_CODE
    assert artifact["meta"]["excel_prompt_name"] == input_snapshot["characters"][0]["prompt_name"]
    assert artifact["meta"]["excel_ref_ids"] == [] and artifact["meta"]["excel_used_refs"] is False
    assert artifact["meta"]["hero_prompt"] == PROMPT
    actual_path = Path(artifact["path"]).resolve()
    assert actual_path == (fixtures.DB.parent / "characters" / "c01.png").resolve()
    assert hashlib.sha256(actual_path.read_bytes()).hexdigest() == handler.image_sha256

    facts = [event for event in history if event["node_key"] == NODE]
    starts = [event for event in facts if event["kind"] == "node_start"]
    ends = [event for event in facts if event["kind"] == "node_end"]
    model_starts = [event for event in facts if event["kind"] == "model_start"]
    model_ends = [event for event in facts if event["kind"] == "model_end"]
    assert len(starts) == len(ends) == len(model_starts) == len(model_ends) == 1, facts
    assert starts[0]["payload"]["project_status"] == "generating_hero"
    assert ends[0]["payload"]["project_status"] == "hero_ready"
    attempt_id = starts[0]["attempt_id"]
    assert attempt_id and ends[0]["attempt_id"] == attempt_id
    assert model_starts[0]["attempt_id"] == model_ends[0]["attempt_id"] == attempt_id
    assert model_starts[0]["request_id"] == model_ends[0]["request_id"] and model_starts[0]["request_id"]
    assert model_ends[0]["payload"]["complete"] is True
    assert model_ends[0]["payload"]["response_id"] == handler.response_ids[0]
    assert len(handler.response_ids) == 1
    assert all(event["execution_id"] == execution and event["project_id"] == PROJECT_ID for event in facts)
    transitions = [transition for event in facts if event["kind"] == "node_change" and event["db_scope"] == "project"
        and event["payload"].get("transactional") is True for transition in event["payload"].get("transitions", [])]
    assert any(transition.get("from") == "running" and transition.get("to") == "done"
               and transition.get("initiator") == "worker" for transition in transitions), "original worker/FSM must commit completion"
    assert not any(event["kind"] in {"node_error", "model_error"} for event in facts)
    assert not any(event["node_key"] == OUTSIDE and event["kind"] == "node_start" for event in history)
    assert dict(handler.counts) == {NODE: 1}
    assert dict(handler.media_counts) == {"generation_submit": 1, "status": 1, "download": 1}
    assert not any(entry.get("unexpected") for entry in handler.transport), handler.transport
    gpt_requests = [entry for entry in handler.transport if entry.get("path") == "/codex/v1/responses"]
    assert len(gpt_requests) == 1 and gpt_requests[0]["body"]["model"] == "gpt-5-6-sol"
    native_prompt_body = json.dumps(gpt_requests[0]["body"], ensure_ascii=False)
    assert "Mira" in native_prompt_body and "Short dark hair" in native_prompt_body

    proof = await verify_hero_entity_results([SimpleNamespace(**row) for row in artifacts],
        project_id=PROJECT_ID, snapshot=input_snapshot, root=fixtures.DB.parent,
        scope={"execution_id": execution, "node_key": NODE, "attempt_id": attempt_id}, provenance=None)
    assert proof["status"] == "unknown" and proof["structural_status"] == "passed", proof
    assert proof["provenance_status"] == "unknown", proof
    assert proof["semantic_quality_verified"] is False and proof["business_commit_claim"] is False
    assert proof["managed_eligible"] is False
    image_proof = proof["artifacts"][0]["verification"]
    assert image_proof["status"] == "passed" and image_proof["format"] == "PNG"
    assert (image_proof["width"], image_proof["height"]) == IMAGE_SIZE
    assert image_proof["bytes"] >= 200_000 and image_proof["sha256"] == handler.image_sha256
    _save(root, "native-entity-verification.json", proof)
    result.update(native_entity_verification=proof,
        db_authority={"artifact_write": "master via original SessionLocal wave",
                      "outer_worker": "project.db", "artifact_rows_repaired_or_projected_by_fixture": False})

    before_counts = dict(handler.counts), dict(handler.media_counts)
    await asyncio.sleep(12)
    assert before_counts == (dict(handler.counts), dict(handler.media_counts)), "no repeated or outside work"
    final = {name: _state(path, prepared["run_ids"][name]) for name, path in paths.items()}
    assert all(final[name] == value for name, value in state.items()), "native boundary must remain stable"
    assert all(_entity_rows(path) == rows for path in paths.values())
    assert hashlib.sha256(actual_path.read_bytes()).hexdigest() == handler.image_sha256
    await observer.catch_up()
    final_history = await _events(client, base, execution)
    _save(root, "api-history.json", {"events": final_history})
    assert [event for event in final_history if event["kind"] in {"node_start", "node_end", "model_start", "model_end", "node_error", "model_error"}] == [
        event for event in history if event["kind"] in {"node_start", "node_end", "model_start", "model_end", "node_error", "model_error"}]
    assert not any(entry.get("unexpected") for entry in handler.transport)
    _observe_state(paths["master"], execution, prepared["run_ids"]["master"])
    assert "retry_profile" not in capability_for("hero").actions
    result.update(accepted=True, paid_calls=0,
        accepted_claim="original observe-only base-character worker path and structural output",
        provenance_limit="native generation request/result binding and shared media counter remain unproven")
