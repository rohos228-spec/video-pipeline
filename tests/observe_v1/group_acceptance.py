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
    fixtures.MODE = "malformed" if mode == "malformed" else "group"
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
            if mode != "off":
                response = await client.post(base + "/observations", json={"node_keys": list(fixtures.NODES)})
                response.raise_for_status()
                execution_id = response.json()["execution_id"]
            response = await client.post(base + "/steps/excel_gpt/run", params={"node_key": fixtures.NODES[0]})
            response.raise_for_status()
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                state = fixtures.snapshot()
                if state["nodes"].get(fixtures.NODES[-1], {}).get("status") == "done":
                    break
                if any(n["status"] == "failed" for n in state["nodes"].values()):
                    break
                await asyncio.sleep(0.1)
            assert all(state["nodes"].get(key, {}).get("status") == "done" for key in fixtures.NODES), state
            path = Path(state["report_path"])
            assert path.exists() and path.stat().st_size > 0
            result.update({"execution_id": execution_id, "state": state,
                "html": {"path": str(path), "bytes": path.stat().st_size,
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}})
            if execution_id:
                await observation.get_observer().catch_up()
                response = await client.get(base + f"/observations/{execution_id}/events", params={"limit": 500})
                response.raise_for_status()
                history = response.json()
                (ROOT / "api-history.json").write_text(json.dumps(history, ensure_ascii=False, indent=2))
                kinds = {key: [e["kind"] for e in history["events"] if e["node_key"] == key] for key in fixtures.NODES}
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
                assert result["context"]["business_group_outcome"] == "done"
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
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
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


if __name__ == "__main__":
    asyncio.run(run())
