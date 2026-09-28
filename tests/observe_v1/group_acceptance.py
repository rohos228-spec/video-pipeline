"""Actual API -> worker -> six-node group; only the external HTTP transport is synthetic.

The test runner stops the app at the group boundary. Observation itself does not.
Prepared answers are reused unchanged from diag_v2/group_runner.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import sys
import threading
import time
import uuid
from pathlib import Path
from http.server import ThreadingHTTPServer

import httpx

ROOT = Path("/results")
DATA = ROOT / "free-run-data"
SOURCE = Path("/observe-source")
sys.path.insert(0, str(SOURCE))
sys.path.insert(0, "/observe-fixtures")
import group_runner as fixtures
from coverage_capture import begin
finish_coverage = begin(SOURCE)


async def run():
    shutil.copytree("/srv/seed", DATA)
    mode = os.environ.get("OBSERVE_GROUP_MODE", "observe")
    browser_mode = os.environ.get("OBSERVE_BROWSER", "").lower() == "true"
    os.environ.update({
        "DATA_DIR": str(DATA), "SQLITE_PATH": str(DATA / "state.db"),
        "TEXT_LLM_PROVIDER": "kie", "GPT_API_KEY": "synthetic-fixture-token",
        "GPT_BASE_URL": "http://127.0.0.1:18766", "GPT_CHAT_PATH": "/codex/v1/responses",
        "GPT_MODEL": "gpt-5-6-sol", "GPT_API_MODE": "responses", "GPT_MAX_RETRIES": "4",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false",
        "FLEET_AUTO_PULL": "false", "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1",
        "WEB_PORT": "18765", "HITL_AUTO_APPROVE": "false", "BROWSER_CDP_AUTO_RECOVER": "false",
        "OUTSEE_HTTP_FALLBACK_CDP": "false", "AUDIO_USE_ELEVENLABS_FALLBACK": "false",
        "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
    })
    fixtures.MODE = "malformed" if mode in {"malformed","unrepaired"} else "group"
    if mode == "unrepaired":
        original_answer=fixtures.synthetic_answer
        fixtures.synthetic_answer=lambda node,attempt: original_answer(node,1) if node==fixtures.NODES[0] else original_answer(node,attempt)
    sock = socket.socket()
    sock.settimeout(1)
    try:
        error = sock.connect_ex(("192.0.2.1", 9))
        assert error != 0, "Network none not enforced"
    finally:
        sock.close()
    server = ThreadingHTTPServer(("127.0.0.1", 18766), fixtures.FixtureHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    from app.services import observation
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if request.url.host not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("Test transport refuses non-loopback request")
        if request.url.port == 18766 and request.method == "POST":
            ctx = observation._attempt.get()
            if ctx:
                node = ctx.node_key
            else:
                with sqlite3.connect(fixtures.DB) as conn:
                    meta = json.loads(conn.execute("SELECT meta FROM projects WHERE id=2").fetchone()[0])
                    node = meta.get("active_excel_gpt_node_key", "")
            assert node in fixtures.NODES[:-1], "Provider call outside selected text group"
            request.headers["x-diagnostic-node"] = node
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send  # External transport only; no worker/planner/DB replacements.
    from app import main
    task = asyncio.create_task(main.main())
    result = {"run_id": "observe-20260928-" + str(uuid.uuid4()), "mode": mode, "paid_calls": 0,
              "network": "none", "operator_starts": 1, "scope_boundary": "test runner"}
    try:
        await fixtures.wait_api()
        async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
            base = "http://127.0.0.1:18765/api/projects/2"
            execution_id = None
            if browser_mode:
                # User enables observation in the real panel. No node is started
                # until the explicit test-runner signal is written via docker exec.
                (ROOT / "browser-ready.json").write_text(json.dumps({"project_id":2,"nodes":list(fixtures.NODES),"state":fixtures.snapshot()}))
                while not (ROOT / "browser-run-requested").exists():
                    await asyncio.sleep(0.2)
                response = await client.get(base + "/observations")
                response.raise_for_status()
                active = [s for s in response.json() if s["observer_state"] != "stopped"]
                assert len(active) == 1, "UI must explicitly enable one scope"
                execution_id = active[0]["execution_id"]
            elif mode != "off":
                response = await client.post(base + "/observations", json={"node_keys": list(fixtures.NODES)})
                response.raise_for_status()
                execution_id = response.json()["execution_id"]
            if execution_id:
                response = await client.post(base + f"/observations/{execution_id}/pin")
                response.raise_for_status()
            if mode == "actions":
                run_response = await client.get(base + "/run")
                run_response.raise_for_status()
                run_snapshot = run_response.json()
                graph_nodes = {str(n.get("id")): n for n in run_snapshot.get("nodes_snapshot", [])}
                selected = [graph_nodes.get(key) for key in fixtures.NODES]
                assert all(selected), "managed test nodes missing from immutable workflow graph"
                group_ids = {str((n.get("data") or {}).get("groupId") or "") for n in selected}
                assert len(group_ids) == 1 and "" not in group_ids, group_ids
                scope_response = await client.post(base + f"/observations/{execution_id}/actions/enable", json={
                    "group_id": group_ids.pop(), "node_keys": list(fixtures.NODES),
                    "rationale": "synthetic isolated actions-v1 group acceptance", "request_limit": 12,
                })
                scope_response.raise_for_status()
                start_response = await client.post(base + f"/observations/{execution_id}/actions/start", json={
                    "node_key": fixtures.NODES[0], "step_code": str(selected[0].get("type") or ""),
                    "rationale": "synthetic isolated acceptance start", "idempotency_key": "actions-test-start-0001",
                })
                assert start_response.is_success, start_response.text
                result["managed_start"] = start_response.json()
            else:
                response = await client.post(base + "/steps/excel_gpt/run", params={"node_key": fixtures.NODES[0]})
                response.raise_for_status()
            if mode == "stop":
                response = await client.post(base + f"/observations/{execution_id}/stop")
                response.raise_for_status()
                assert response.json()["business_action"] is None
                result["observer_stop_during_pipeline"] = True
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                state = fixtures.snapshot()
                if state["nodes"].get(fixtures.NODES[-1], {}).get("status") == "done":
                    break
                if any(n["status"] == "failed" for n in state["nodes"].values()):
                    break
                await asyncio.sleep(0.1)
            result.update({"execution_id":execution_id,"state":state})
            if mode == "unrepaired":
                assert state['nodes'][fixtures.NODES[0]]['status']=='failed',state
                assert all(state['nodes'][key]['status']=='pending' for key in fixtures.NODES[1:]),state
                result['fault']='external synthetic transport always returns prepared malformed answer'
            else:
                assert all(state["nodes"].get(key, {}).get("status") == "done" for key in fixtures.NODES), state
                path = Path(state["report_path"])
                assert path.exists() and path.stat().st_size > 0
                result['html']={"path":str(path),"bytes":path.stat().st_size,"sha256":hashlib.sha256(path.read_bytes()).hexdigest()}
            if execution_id:
                await observation.get_observer().catch_up()
                response = await client.get(base + f"/observations/{execution_id}/events", params={"limit": 500})
                response.raise_for_status()
                history = response.json()
                (ROOT / "api-history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2))
                kinds = {key: [e["kind"] for e in history["events"] if e["node_key"] == key] for key in fixtures.NODES}
                if mode not in {"stop","unrepaired"}:
                    for key in fixtures.NODES:
                        assert "node_start" in kinds[key] and "node_end" in kinds[key], (key, kinds[key])
                    for key in fixtures.NODES[:-1]:
                        assert "model_start" in kinds[key] and "model_end" in kinds[key], (key, kinds[key])
                    assert "artifact_created" in kinds[fixtures.NODES[-1]]
                    assert "node_input" in kinds[fixtures.NODES[-1]]
                text_check = [e for e in history["events"] if e["node_key"] == fixtures.NODES[1] and e["kind"] == "parse_result"]
                assert all(not e["payload"]["payload_required"] for e in text_check), "Text verdict must not require apply-ops"
                if mode == "malformed":
                    assert "json_retry" in kinds[fixtures.NODES[0]]
                    assert "parse_result" in kinds[fixtures.NODES[0]]
                response = await client.get(base + f"/observations/{execution_id}/context")
                response.raise_for_status()
                result["context"] = response.json()
                assert "node_contexts" in result["context"], "Persistent per-node inputs/model/checks missing"
                assert result["context"]["business_group_outcome"] == ("failed" if mode=='unrepaired' else "done")
                from app.services.diagnosis import get_diagnoser
                await get_diagnoser(observation.get_observer()).update(execution_id)
                response=await client.get(base+f"/observations/{execution_id}/diagnoses")
                response.raise_for_status()
                result['diagnosis']=response.json()
                (ROOT/'diagnosis.json').write_text(json.dumps(result['diagnosis'],ensure_ascii=False,indent=2))
                formats=[i for i in result['diagnosis']['incidents'] if i['type'] in {'invalid_json','missing_payload'}]
                if mode=='malformed':
                    assert len(formats)==1,result['diagnosis']
                    assert formats[0]['status']=='resolved_confirmed',formats
                if mode=='unrepaired':
                    attempts={e['attempt_id'] for e in history['events'] if e['kind']=='node_start'}
                    assert {i['attempt_id'] for i in formats}==attempts
                    assert all(i['status']=='human_review' for i in formats)
                    assert sum(fixtures.FixtureHandler.counts.values())==2*len(attempts)
                else:
                    assert sum(fixtures.FixtureHandler.counts.values()) == (6 if mode=='malformed' else 5)
                if mode == "actions":
                    controls = await client.get(base + f"/observations/{execution_id}/actions")
                    controls.raise_for_status()
                    managed = controls.json()
                    assert managed["enabled"] and not managed["held"], managed
                    assert managed["requests_used"] == 5, managed
                    assert any(a["action_kind"] == "start_node" and a["status"] == "submitted"
                               for a in managed["records"]), managed
                    result["actions"] = managed
                    verification = await client.post(base + f"/observations/{execution_id}/actions/verify", json={
                        "node_key": fixtures.NODES[-1],
                        "rationale": "verify synthetic terminal node runtime completion",
                        "idempotency_key": "actions-test-verify-0001",
                    })
                    assert verification.is_success, verification.text
                    result["verification"] = verification.json()
                    assert result["verification"]["status"] == "verified", result["verification"]
                    run_after = await client.get(base + "/run")
                    run_after.raise_for_status()
                    all_states = {str(n["node_key"]): n["status"] for n in run_after.json().get("node_runs", [])}
                    boundary_targets = {str(edge.get("target")) for edge in run_snapshot.get("edges_snapshot", [])
                        if str(edge.get("source")) in set(fixtures.NODES)
                        and str(edge.get("target")) not in set(fixtures.NODES)}
                    assert boundary_targets, "selected managed group must have an outgoing graph boundary"
                    assert all(all_states.get(key) == "pending" for key in boundary_targets), {
                        "boundary_targets": sorted(boundary_targets), "node_states": all_states,
                    }
                    result["group_boundary"] = {key: all_states[key] for key in sorted(boundary_targets)}
                if mode == "stop":
                    assert result["context"]["observer_state"] == "stopped"
                with sqlite3.connect(DATA / "state.db") as conn:
                    assert conn.execute("SELECT count(*) FROM observation_events WHERE project_id!=2").fetchone()[0] == 0
            else:
                with sqlite3.connect(DATA / "state.db") as conn:
                    assert conn.execute("SELECT count(*) FROM observation_events").fetchone()[0] == 0
            result["accepted"] = True
    except BaseException as exc:
        result["accepted"] = False
        result["error_type"] = type(exc).__name__
        result["error"] = observation.redact(str(exc))
        raise
    finally:
        finish_coverage(ROOT)
        result["fixture_counts"] = dict(fixtures.FixtureHandler.counts)
        # Persist the reached group boundary before cancellation. A shutdown
        # failure must not hide completed nodes or already-written evidence.
        (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        task.cancel()
        done, pending = await asyncio.wait({task}, timeout=10)
        if pending:
            result["shutdown_gap"] = "app.main cancellation did not finish within 10 seconds"
            (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        elif not task.cancelled():
            error = task.exception()
            if error:
                result["shutdown_error_type"] = type(error).__name__
        server.shutdown()
        server.server_close()
        httpx.AsyncClient.send = original_send
        manifest = json.loads((SOURCE / "source-manifest.json").read_text())
        loaded = []
        for name, module in sorted(sys.modules.items()):
            filename = getattr(module, "__file__", None)
            if not name.startswith("app.") or not filename:
                continue
            path = Path(filename).resolve()
            assert path.is_relative_to(SOURCE), (name, str(path))
            relative = path.relative_to(SOURCE).as_posix()
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            assert manifest.get(relative) == sha, name
            loaded.append({"module": name, "path": relative, "sha256": sha})
        (ROOT / "loaded-modules.json").write_text(json.dumps(loaded, indent=2))
        (ROOT / "group-result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        if pending:
            # Only this synthetic runner process; preserve data, do not claim a
            # clean application shutdown. Prevent infinite cancellation loops.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(3)
        if browser_mode and result.get("accepted"):
            # At the boundary app.main is cancelled. Replay saved history in
            # the dedicated history_replay.py process. Binding in this process
            # races the original HTTP socket's shutdown (captured Errno 98).
            (ROOT / "browser-history-mode.json").write_text(json.dumps({"worker":"stopped by test runner at group boundary", "lifespan":"off", "frontend_sha256":hashlib.sha256((SOURCE / "web/out/index.html").read_bytes()).hexdigest()}))
        if result.get("accepted") and not pending:
            # Background executor threads may outlive the cancelled synthetic
            # app even after all test-owned artifacts and hashes are flushed.
            # Exit this disposable runner cleanly only after acceptance evidence
            # and loaded-module verification have been persisted.
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(0)


if __name__ == "__main__":
    asyncio.run(run())
