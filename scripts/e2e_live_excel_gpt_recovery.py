"""Real excel_gpt worker -> incident -> Decision -> retry_profile -> verified recovery.

What is real: the whole application (FastAPI + worker + observer + diagnosis + Decision
supervisor + profile overlay + recovery verifier), the real `n_excel_gpt_fw_script`
executor and prompts, the real Kie model for the Decision turns and for the recovery
attempt.

What is NOT real, by construction: the FIRST worker attempt. A real model cannot be made
to return broken JSON on demand, so every non-recovery worker POST is answered locally
with the same malformed text the existing exhaustion fixtures use ('{"ops": }'); it never
leaves the machine and costs nothing. Everything after the incident goes to the provider.

Safety: --dry (default) refuses every provider-bound request, so it costs nothing.
--live requires the owner-approved caps below; the script aborts before sending a request
that would exceed --max-real-posts, and stops when the cost estimate passes --max-usd.
Credentials are read from the env file given by --env-file, only the GPT_* names listed in
_ENV_WHITELIST, and are placed into this process' environment only (never printed, never
written to the repository).

Usage (from the repository root, with the project's venv):
    python scripts/e2e_live_excel_gpt_recovery.py --seed-dir <seed data dir> --work-dir <empty dir>
    python scripts/e2e_live_excel_gpt_recovery.py --live --env-file <.env> --seed-dir ... --work-dir ...
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
import e2e_guard as guard

_ENV_WHITELIST = ("GPT_API_KEY", "GPT_BASE_URL", "GPT_CHAT_PATH", "GPT_API_MODE", "GPT_PROXY_URL",
                  "GPT_RELAY_TOKEN", "GPT_TIMEOUT_S")
SLUG = "diagnostic-synthetic-scene-2026-09-24"
NODE = "n_excel_gpt_fw_script"
PROFILE_ID = "production-json-repair-v1"
MALFORMED = '{"ops": }'
PORT = 18765
PROJECT_ID: int | None = None
BASE: str | None = None


def api_base(project_id: int, port: int = PORT) -> str:
    return f"http://127.0.0.1:{port}/api/projects/{project_id}"


def configure(data: Path, slug: str | None, project_id: int | None, port: int = PORT) -> int:
    """Pick the project of the seed COPY (by slug or explicit id) and build the API base URL."""
    global SLUG, PROJECT_ID, BASE
    SLUG = slug or SLUG
    PROJECT_ID = guard.resolve_project_id(data / "state.db", SLUG, project_id)
    BASE = api_base(PROJECT_ID, port)
    return PROJECT_ID


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--live", action="store_true", help="send the Decision and recovery requests to the provider")
    p.add_argument("--env-file", help="env file with GPT_* settings (required for --live)")
    p.add_argument("--seed-dir", required=True, help="data dir holding state.db and videos/<slug>/")
    p.add_argument("--slug", default=None, help="slug of the project inside the seed (default: the fixture slug)")
    p.add_argument("--project-id", type=int, default=None, help="project id in the seed (default: looked up by slug)")
    p.add_argument("--work-dir", required=True, help="scratch dir; seed is copied here")
    p.add_argument("--max-real-posts", type=int, default=6)
    p.add_argument("--max-usd", type=float, default=0.10)
    p.add_argument("--timeout-s", type=int, default=900)
    return p.parse_args()


def _load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() in _ENV_WHITELIST:
            out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _prepare_data(seed: Path, work: Path, slug: str | None = None, project_id: int | None = None) -> Path:
    data = guard.prepare_scratch_data(seed, work)
    pid = configure(data, slug, project_id)
    db_paths = [data / "state.db", data / "videos" / SLUG / "project.db"]
    node = None
    with guard.connect_scratch(db_paths[0], work) as db:
        nodes = json.loads(db.execute(
            "SELECT nodes_snapshot FROM workflow_runs WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()[0])
        node = next(n for n in nodes if n["id"] == NODE)
    graph_nodes, graph_edges = [node], []
    for path in db_paths:
        with guard.connect_scratch(path, work) as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=?", (pid,)).fetchone()[0])
            meta.setdefault("canvas_graph", {}).update(nodes=graph_nodes, edges=graph_edges)
            meta.pop("step_failure", None)
            db.execute("UPDATE projects SET meta=?,status='new' WHERE id=?", (json.dumps(meta), pid))
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE project_id=?",
                       (json.dumps(graph_nodes), json.dumps(graph_edges), pid))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id IN "
                       "(SELECT id FROM workflow_runs WHERE project_id=?) AND node_key<>?", (pid, NODE))
            db.execute("UPDATE node_runs SET status='pending',progress=0,attempts=0,error=NULL WHERE node_key=?",
                       (NODE,))
    return data


def _runtime_config(work: Path) -> Path:
    cfg = {
        "version": 1, "mode": "production", "online_enabled": True,
        "model_transport": "real", "external_calls_enabled": True,
        "endpoint": "https://api.kie.ai/codex/v1/responses",
        "decision": {"catalog_id": "gpt-kie", "model_id": "gpt-5-6-sol", "reasoning_effort": "low",
                     "max_output_tokens": 1024, "timeout_s": 60},
        "profiles": {PROFILE_ID: {"model_id": "gpt-5-6-sol", "provider": "kie", "kind": "text",
                                  "channel": "stable", "reasoning_effort": "low", "max_output_tokens": 4096,
                                  "prompt_variant": "repair-json-only-v1", "synthetic_only": False}},
        "allowed_actions": ["read_events", "read_contract", "retry_profile", "human_review", "wait", "finish"],
        "budget_policy": {"policy_id": "live-e2e-excel-gpt", "max_requests": 60},
    }
    path = work / "runtime.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def _sse(text: str, rid: str) -> bytes:
    response = {"id": rid, "status": "completed", "output": [{"content": [{"type": "output_text", "text": text}]}],
                "usage": {"input_tokens": 0, "output_tokens": 0, "fixture": True}}
    events = [{"type": "response.created", "response": {"id": rid}},
              {"type": "response.output_text.delta", "delta": text},
              {"type": "response.output_text.done", "text": text},
              {"type": "response.completed", "response": response}]
    return "".join("data: " + json.dumps(e, ensure_ascii=False) + "\n\n" for e in events).encode("utf-8")


async def main() -> int:
    args = _args()
    work, seed = Path(args.work_dir).resolve(), Path(args.seed_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = {}
    if args.live:
        if not args.env_file:
            print("--live needs --env-file")
            return 2
        env = _load_env(args.env_file)
        if not env.get("GPT_API_KEY") or env["GPT_API_KEY"].startswith("synthetic"):
            print("env file has no real GPT_API_KEY")
            return 2
    data = _prepare_data(seed, work, args.slug, args.project_id)
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.update({
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "TEXT_LLM_PROVIDER": "kie", "GPT_API_KEY": "dry-run-key-not-a-real-credential",
        "GPT_BASE_URL": "https://api.kie.ai", "GPT_CHAT_PATH": "/codex/v1/responses",
        "GPT_MODEL": "gpt-5-6-sol", "GPT_API_MODE": "responses", "GPT_MAX_RETRIES": "1",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1", "WEB_PORT": str(PORT), "HITL_AUTO_APPROVE": "false",
        "BROWSER_CDP_AUTO_RECOVER": "false", "OUTSEE_HTTP_FALLBACK_CDP": "false",
        "AUDIO_USE_ELEVENLABS_FALLBACK": "false", "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
        "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
        "ORCHESTRATOR_RUNTIME_CONFIG": str(_runtime_config(work)),
    })
    os.environ.update(env)
    import httpx
    import app.services.api_tracker_hook as tracker
    tracker_db = work / "tracker.db"
    if tracker_db.exists():
        tracker_db.unlink()
    tracker._TRACKER_DB = tracker_db
    from app.services import observation
    from app.services.decision_transport import current as decision_hook

    counters = {"injected_worker": 0, "real_decision": 0, "real_worker_recovery": 0, "refused": 0}
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if request.method != "POST" or not request.url.path.endswith("/codex/v1/responses"):
            return await original_send(client, request, **kwargs)
        ctx = observation._attempt.get()
        is_decision = decision_hook.get() is not None
        is_recovery = bool(ctx and ctx.session.info.get("recovery_attempt_id") == ctx.attempt_id)
        if not is_decision and not is_recovery:
            counters["injected_worker"] += 1
            rid = f"resp_injected_{counters['injected_worker']}"
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=_sse(MALFORMED, rid), request=request)
        kind = "real_decision" if is_decision else "real_worker_recovery"
        real_total = counters["real_decision"] + counters["real_worker_recovery"]
        if not args.live:
            counters["refused"] += 1
            raise RuntimeError(f"dry-run: provider request refused ({kind})")
        if real_total >= args.max_real_posts:
            counters["refused"] += 1
            raise RuntimeError(f"real request cap reached ({args.max_real_posts})")
        spent = _spent_usd(tracker_db)
        if spent >= args.max_usd:
            counters["refused"] += 1
            raise RuntimeError(f"cost cap reached (${spent:.4f} >= ${args.max_usd})")
        counters[kind] += 1
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send
    from app import main as app_main
    task = asyncio.create_task(app_main.main())
    result: dict = {"mode": "live" if args.live else "dry", "node": NODE, "profile": PROFILE_ID}
    try:
        await _wait_api()
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            from _operator_headers import attach_operator_token
            await attach_operator_token(client, BASE)
            options = (await client.get(BASE + "/observations/launch-options")).json()
            print("launch-options configured:", options["configured"], "mode:", options["mode"],
                  "strategies:", options["strategies"], "profiles:", [p["profile_id"] for p in options["profiles"]])
            node_info = next(n for n in options["nodes"] if n["node_key"] == NODE)
            assert node_info["supported"], node_info
            response = await client.post(BASE + "/observations/launch", json={
                "workflow_run_id": options["workflow_run_id"], "graph_version": options["graph_version"],
                "node_keys": [NODE], "strategies": ["wait", "human_review", "retry_profile"],
                "profile_ids": [PROFILE_ID], "profile_catalog_hash": options["profile_catalog_hash"],
                "request_limit": 40, "rationale": "owner-approved real recovery verification",
                "idempotency_key": "live-e2e-excel-gpt-01"})
            print("launch:", response.status_code, (response.text[:300] if response.status_code >= 300 else "ok"))
            response.raise_for_status()
            launch = response.json()
            execution = launch["execution_id"]
            result["execution_id"] = execution
            response = await client.post(BASE + f"/observations/{execution}/decisions/control", json={
                "enabled": True, "rationale": "owner-approved decision loop for the real recovery verification"})
            print("decisions/control:", response.status_code)
            response.raise_for_status()
            deadline = time.monotonic() + args.timeout_s
            last = None
            decisions = actions = {}
            while time.monotonic() < deadline:
                decisions = (await client.get(BASE + f"/observations/{execution}/decisions")).json()
                actions = (await client.get(BASE + f"/observations/{execution}/actions")).json()
                node_status = _node_status(data)
                state = (node_status, [(d["status"], d["model_steps"]) for d in decisions["records"]],
                         [r["status"] for r in actions["recoveries"]], actions["requests_used"], dict(counters))
                if state != last:
                    print(time.strftime("%H:%M:%S"), state)
                    last = state
                recs = decisions["records"]
                if recs and recs[0]["status"] in {"done", "human_review"}:
                    break
                await asyncio.sleep(1)
            result.update(decisions=decisions, actions=actions, node_status=_node_status(data),
                          counters=dict(counters))
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    await asyncio.sleep(3)
    result["tracker_rows"] = _tracker_rows(tracker_db)
    result["spent_usd_estimate"] = round(sum(r["cost_usd"] or 0 for r in result["tracker_rows"]), 6)
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    recs = (result.get("decisions") or {}).get("records") or []
    print("\n=== RESULT ===")
    print("mode:", result["mode"], "| node:", result.get("node_status"), "| decision:",
          [(r["status"], r["model_steps"]) for r in recs], "| recoveries:",
          [r["status"] for r in (result.get("actions") or {}).get("recoveries", [])])
    print("counters:", result.get("counters"), "| tracker rows:", len(result["tracker_rows"]),
          "| est. spend $%.4f" % result["spent_usd_estimate"])
    ok = bool(recs) and recs[0]["status"] == "done" and result.get("node_status") in {"done", "waiting_hitl"}
    print("PASS" if ok else "NOT PASSED (see result.json)")
    return 0 if ok or not args.live else 1


async def _wait_api() -> None:
    import httpx
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        for _ in range(120):
            try:
                if (await client.get(BASE)).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("application API did not start")


def _node_status(data: Path) -> str | None:
    with sqlite3.connect(data / "videos" / SLUG / "project.db") as db:
        row = db.execute("SELECT status FROM node_runs WHERE node_key=?", (NODE,)).fetchone()
    return row[0] if row else None


def _tracker_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        return [dict(r) for r in db.execute(
            "SELECT model,prompt_tokens,completion_tokens,cached_tokens,cost_usd,status_code,project_source "
            "FROM api_calls ORDER BY id")]


def _spent_usd(db_path: Path) -> float:
    return sum((r["cost_usd"] or 0) for r in _tracker_rows(db_path))


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
