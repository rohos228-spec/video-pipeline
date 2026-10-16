"""Native text nodes (plan/script/split/image_prompts): real executor -> incident -> Decision -> recovery.

Generalises scripts/e2e_live_excel_gpt_recovery.py (imported, not edited) to the four
native text/project nodes. The REAL application runs in-process: FastAPI, worker, observer,
diagnosis, Decision supervisor, profile overlay, recovery verifier and the real native
executors (make_plan / make_script / split_frames / generate_image_prompts via
xlsx_step_runners). Each node type is seeded with the minimal real project state the
executor needs (same seeding as tests/observe_v1/project_text_acceptance.prepare).

NOT real by construction: the FIRST worker attempts. Every non-recovery worker POST is
answered locally with the malformed text '{"ops": }' (never leaves the machine, costs
nothing). Everything after the incident (Decision turns, recovery attempt) would go to the
provider in --live.

Safety: --dry (default) refuses every provider-bound request (physically, in the httpx
send hook). --live needs --env-file and the owner-approved caps below; it aborts before a
request that would exceed --max-real-posts and stops when the estimate passes --max-usd.
Credentials are read only for the GPT_* names in _ENV_WHITELIST, placed in this process'
environment only and never printed or written.

--probe-retry (dry/diagnostic): instead of the Decision loop, once native retries are
exhausted POST the operator endpoint /actions/retry (retry_same) so the real recover_node
precondition checks run against the real state; the recovery attempt's own provider
request is refused in dry, so it ends in human_review. Reports whether the preconditions
accepted or the exact reason they refused.

Usage (repository root, project venv):
    python scripts/e2e_text_nodes_recovery.py --node plan --seed-dir <seed> --work-dir <empty dir>
    python scripts/e2e_text_nodes_recovery.py --node plan --probe-retry --seed-dir ... --work-dir ...
    python scripts/e2e_text_nodes_recovery.py --live --env-file <.env> --node script --seed-dir ... --work-dir ...
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("e2e_live_excel_gpt_recovery", _HERE / "e2e_live_excel_gpt_recovery.py")
base = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = base
_spec.loader.exec_module(base)

PORT, MALFORMED = base.PORT, base.MALFORMED
guard = base.guard
BASE: str | None = None
PROFILE_ID = base.PROFILE_ID
TEXT = ("Синтетический текст: человек находит папку, читает записку и возвращает её на стол. " * 6).strip()
NODE_TYPES = ("plan", "script", "split", "image_prompts")
TERMINAL_DECISION = {"done", "human_review", "failed", "rejected", "cancelled"}


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--node", required=True, choices=NODE_TYPES)
    p.add_argument("--live", action="store_true", help="send the Decision and recovery requests to the provider")
    p.add_argument("--probe-retry", choices=("same", "profile"),
                   help="dry diagnostic: operator-driven retry_same / approved-profile retry instead of Decision")
    p.add_argument("--simulate-recovery", action="store_true",
                   help="with --probe-retry (dry only): answer the RECOVERY attempt's request locally with a valid "
                        "fixture instead of refusing it, so recovery verification can be observed offline")
    p.add_argument("--env-file", help="env file with GPT_* settings (required for --live)")
    p.add_argument("--seed-dir", required=True, help="data dir holding state.db and videos/<slug>/")
    p.add_argument("--slug", default=None, help="slug of the project inside the seed (default: the fixture slug)")
    p.add_argument("--project-id", type=int, default=None, help="project id in the seed (default: looked up by slug)")
    p.add_argument("--work-dir", required=True, help="scratch dir; seed is copied here")
    p.add_argument("--port", type=int, default=PORT, help="API port (distinct ports allow parallel dry runs)")
    p.add_argument("--max-real-posts", type=int, default=6)
    p.add_argument("--max-usd", type=float, default=0.10)
    p.add_argument("--timeout-s", type=int, default=900)
    return p.parse_args()


def _prepare_data(seed: Path, work: Path, typ: str, node_key: str,
                  slug: str | None = None, project_id: int | None = None) -> Path:
    data = guard.prepare_scratch_data(seed, work)
    pid = base.configure(data, slug, project_id, PORT)
    video_dir = data / "videos" / base.SLUG
    db_paths = [data / "state.db", video_dir / "project.db"]
    node = {"id": node_key, "type": typ, "position": {"x": 100, "y": 100},
            "data": {"label": "Native " + typ, "modelId": "gpt-kie"}}
    nodes, edges = [node], []
    for path in db_paths:
        with guard.connect_scratch(path, work) as db:
            meta = json.loads(db.execute("SELECT meta FROM projects WHERE id=?", (pid,)).fetchone()[0])
            meta.setdefault("canvas_graph", {}).update(nodes=nodes, edges=edges)
            meta.pop("step_failure", None)
            meta.pop("active_excel_gpt_node_key", None)
            db.execute("DELETE FROM frames WHERE project_id=?", (pid,))
            db.execute("DELETE FROM hitl_requests WHERE project_id=?", (pid,))
            db.execute("UPDATE projects SET status='new',general_plan=?,script_text=?,meta=? WHERE id=?",
                       (None if typ == "plan" else TEXT, TEXT if typ in {"split", "image_prompts"} else None,
                        json.dumps(meta), pid))
            if typ == "image_prompts":
                words = TEXT.split()
                mid = len(words) // 2
                for number, vo in enumerate((" ".join(words[:mid]), " ".join(words[mid:])), 1):
                    db.execute(
                        "INSERT INTO frames(project_id,number,voiceover_text,status,attrs,uuid,sort_key,created_at,updated_at)"
                        " VALUES(?,?,?,'planned','{}',?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                        (pid, number, vo, ("af" if number == 1 else "bf") * 16, number))
            run = db.execute("SELECT id FROM workflow_runs WHERE project_id=? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()[0]
            db.execute("UPDATE workflow_runs SET nodes_snapshot=?,edges_snapshot=? WHERE id=?",
                       (json.dumps(nodes), json.dumps(edges), run))
            db.execute("DELETE FROM node_runs WHERE workflow_run_id=?", (run,))
            db.execute("INSERT INTO node_runs(workflow_run_id,node_key,node_type,status,progress,attempts,meta,"
                       "created_at,updated_at) VALUES(?,?,?,'pending',0,0,'{}',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)",
                       (run, node_key, typ))
    voiceover = video_dir / "voiceover.txt"
    if typ in {"split", "image_prompts"}:
        voiceover.write_text(TEXT, encoding="utf-8")
    elif voiceover.exists():
        voiceover.unlink()
    return data


def _runtime_config(work: Path, probe: bool) -> Path:
    actions = ["read_events", "read_contract", "retry_profile", "human_review", "wait", "finish"]
    if probe:
        actions.insert(2, "retry_same")
    cfg = {
        "version": 1, "mode": "production", "online_enabled": True,
        "model_transport": "real", "external_calls_enabled": True,
        "endpoint": "https://api.kie.ai/codex/v1/responses",
        "decision": {"catalog_id": "gpt-kie", "model_id": "gpt-5-6-sol", "reasoning_effort": "low",
                     "max_output_tokens": 1024, "timeout_s": 60},
        "profiles": {PROFILE_ID: {"model_id": "gpt-5-6-sol", "provider": "kie", "kind": "text",
                                  "channel": "stable", "reasoning_effort": "low", "max_output_tokens": 4096,
                                  "prompt_variant": "repair-json-only-v1", "synthetic_only": False}},
        "allowed_actions": actions,
        "budget_policy": {"policy_id": "live-e2e-text-nodes", "max_requests": 60},
    }
    path = work / "runtime.json"
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def _valid_answer(typ: str) -> str:
    field = "общий_план" if typ == "plan" else "закадровый_текст"
    if typ == "plan" or typ == "script":
        return json.dumps({"ops": [{"target": "project", "fields": {field: TEXT}}]}, ensure_ascii=False)
    raise RuntimeError("no simulated valid answer for " + typ)


def _db_state(data: Path, node_key: str) -> dict:
    out = {}
    pid = base.PROJECT_ID
    with sqlite3.connect(data / "videos" / base.SLUG / "project.db") as db:
        row = db.execute("SELECT status,attempts FROM node_runs WHERE node_key=?", (node_key,)).fetchone()
        out["node"] = row[0] if row else None
        out["attempts"] = row[1] if row else None
        prow = db.execute("SELECT status,length(general_plan),length(script_text),meta FROM projects WHERE id=?",
                          (pid,)).fetchone()
        out["project_status"], out["plan_len"], out["script_len"] = prow[0], prow[1], prow[2]
        fs = (json.loads(prow[3]).get("step_failure") or {})
        out["total_fails"] = fs.get("total_fails")
        out["abandoned"] = bool(fs.get("abandoned_at"))
        out["frames"] = db.execute("SELECT count(*) FROM frames WHERE project_id=?", (pid,)).fetchone()[0]
        out["hitl"] = db.execute("SELECT kind,decision FROM hitl_requests WHERE project_id=?", (pid,)).fetchall()
    return out


async def main() -> int:
    args = _args()
    typ = args.node
    global BASE
    node_key = f"native-{typ}-A"
    if args.live and (args.probe_retry or args.simulate_recovery):
        print("--probe-retry/--simulate-recovery are dry-only diagnostics")
        return 2
    if args.simulate_recovery and not args.probe_retry:
        print("--simulate-recovery needs --probe-retry")
        return 2
    work, seed = Path(args.work_dir).resolve(), Path(args.seed_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    env = {}
    if args.live:
        if not args.env_file:
            print("--live needs --env-file")
            return 2
        env = base._load_env(args.env_file)
        if not env.get("GPT_API_KEY") or env["GPT_API_KEY"].startswith("synthetic"):
            print("env file has no real GPT_API_KEY")
            return 2
    data = _prepare_data(seed, work, typ, node_key, args.slug, args.project_id)
    BASE = base.BASE = base.api_base(base.PROJECT_ID, args.port)
    root = _HERE.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.update({
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "TEXT_LLM_PROVIDER": "kie", "GPT_API_KEY": "dry-run-key-not-a-real-credential",
        "GPT_BASE_URL": "https://api.kie.ai", "GPT_CHAT_PATH": "/codex/v1/responses",
        "GPT_MODEL": "gpt-5-6-sol", "GPT_API_MODE": "responses", "GPT_MAX_RETRIES": "1",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1", "WEB_PORT": str(args.port), "HITL_AUTO_APPROVE": "false",
        "BROWSER_CDP_AUTO_RECOVER": "false", "OUTSEE_HTTP_FALLBACK_CDP": "false",
        "AUDIO_USE_ELEVENLABS_FALLBACK": "false", "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
        "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
        "ORCHESTRATOR_RUNTIME_CONFIG": str(_runtime_config(work, args.probe_retry == "same")),
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
    refused_kinds: list[str] = []
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
                                  content=base._sse(MALFORMED, rid), request=request)
        kind = "real_decision" if is_decision else "real_worker_recovery"
        real_total = counters["real_decision"] + counters["real_worker_recovery"]
        if args.simulate_recovery and is_recovery and not is_decision:
            counters["simulated_recovery"] = counters.get("simulated_recovery", 0) + 1
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=base._sse(_valid_answer(typ), f"resp_simulated_{counters['simulated_recovery']}"),
                                  request=request)
        if not args.live:
            counters["refused"] += 1
            refused_kinds.append(kind)
            raise RuntimeError(f"dry-run: provider request refused ({kind})")
        if real_total >= args.max_real_posts:
            counters["refused"] += 1
            raise RuntimeError(f"real request cap reached ({args.max_real_posts})")
        spent = base._spent_usd(tracker_db)
        if spent >= args.max_usd:
            counters["refused"] += 1
            raise RuntimeError(f"cost cap reached (${spent:.4f} >= ${args.max_usd})")
        counters[kind] += 1
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send
    from app import main as app_main
    task = asyncio.create_task(app_main.main())
    result: dict = {"mode": "live" if args.live else "dry", "node_type": typ, "node": node_key,
                    "profile": PROFILE_ID, "probe_retry": args.probe_retry}
    try:
        await base._wait_api()
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            from _operator_headers import attach_operator_token
            await attach_operator_token(client, BASE)
            options = (await client.get(BASE + "/observations/launch-options")).json()
            print("launch-options configured:", options["configured"], "mode:", options["mode"],
                  "strategies:", options["strategies"], "profiles:", [p["profile_id"] for p in options["profiles"]])
            node_info = next(n for n in options["nodes"] if n["node_key"] == node_key)
            print("node capability:", {k: node_info[k] for k in ("supported", "result_kind", "actions")})
            result["node_capability"] = node_info
            if not node_info["supported"]:
                print("node is not supported by the managed launch gate; aborting")
                return 1
            strategies = ["wait", "human_review"]
            profile_ids: list[str] = []
            if args.probe_retry == "same":
                strategies.append("retry_same")
            elif args.probe_retry == "profile" or "retry_profile" in node_info["actions"]:
                if "retry_profile" not in node_info["actions"]:
                    result["stopped_at"] = "capability has no retry_profile; strategy not requested"
                    raise RuntimeError(result["stopped_at"])
                strategies.append("retry_profile")
                profile_ids = [PROFILE_ID]
            body = {"workflow_run_id": options["workflow_run_id"], "graph_version": options["graph_version"],
                    "node_keys": [node_key], "strategies": strategies, "profile_ids": profile_ids,
                    "request_limit": 40, "rationale": "owner-approved text node recovery verification",
                    "idempotency_key": f"live-e2e-{typ}-01"}
            if profile_ids:
                body["profile_catalog_hash"] = options["profile_catalog_hash"]
            response = await client.post(BASE + "/observations/launch", json=body)
            print("launch:", response.status_code, (response.text[:300] if response.status_code >= 300 else "ok"))
            result["strategies"] = strategies
            if response.status_code >= 300:
                result["launch_error"] = response.text[:500]
                result["stopped_at"] = "launch gate refused the strategy catalog"
                raise RuntimeError("launch refused: " + response.text[:300])
            execution = response.json()["execution_id"]
            result["execution_id"] = execution
            if args.probe_retry:
                response = await client.post(BASE + f"/observations/{execution}/decisions/control", json={
                    "enabled": False, "rationale": "dry probe: operator-driven recovery, Decision loop off"})
                print("decisions/control(off):", response.status_code)
                response.raise_for_status()
            else:
                response = await client.post(BASE + f"/observations/{execution}/decisions/control", json={
                    "enabled": True, "rationale": "owner-approved decision loop for text node recovery verification"})
                print("decisions/control:", response.status_code)
                response.raise_for_status()
            await _poll(client, args, execution, node_key, data, counters, result)
    except RuntimeError as exc:
        result["error"] = str(exc)
        print("STOP:", exc)
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    await asyncio.sleep(3)
    result["refused_kinds"] = refused_kinds
    result["tracker_rows"] = base._tracker_rows(tracker_db)
    result["spent_usd_estimate"] = round(sum(r["cost_usd"] or 0 for r in result["tracker_rows"]), 6)
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    recs = (result.get("decisions") or {}).get("records") or []
    all_incidents = (result.get("diagnoses") or {}).get("incidents", [])
    incidents = {"total": len(all_incidents), "current": [(i["type"], i["status"], i.get("blocking")) for i in
                 all_incidents if i.get("currently_relevant")]}
    result["incident_types_seen"] = sorted({i["type"] for i in all_incidents})
    print("\n=== RESULT ===")
    print("mode:", result["mode"], "| node:", typ, "| db:", result.get("db_state"))
    print("incidents:", incidents, "| types seen:", result["incident_types_seen"])
    print("stopped_at:", result.get("stopped_at"))
    print("decision:", [(r["status"], r["model_steps"]) for r in recs], "| recoveries:",
          [(r["status"], (r.get("verification_result") or {}).get("verified")) for r in
           (result.get("actions") or {}).get("recoveries", [])])
    if result.get("probe"):
        print("probe retry_same:", result["probe"])
    print("counters:", result.get("counters"), "| refused:", refused_kinds, "| est. spend $%.4f"
          % result["spent_usd_estimate"])
    ok = bool(recs) and recs[0]["status"] == "done" and (result.get("db_state") or {}).get("node") in {
        "done", "waiting_hitl"}
    if result["mode"] == "live":
        print("PASS" if ok else "NOT PASSED (see result.json)")
        return 0 if ok else 1
    print("DRY RUN COMPLETE (no pass/fail claim without a real Decision)")
    return 0


async def _probe(client, args, prefix, execution, node_key, incident_id) -> dict:
    """Run the REAL recover_node precondition checks through the supported entry points."""
    if args.probe_retry == "same":
        response = await client.post(prefix + "/actions/retry", json={
            "incident_id": incident_id, "node_key": node_key,
            "rationale": "dry precondition probe for text node recovery",
            "idempotency_key": f"probe-retry-{node_key}"})
        return {"mode": "retry_same", "http": response.status_code, "accepted": response.status_code < 300,
                "body": response.text[:500]}
    state = (await client.get(prefix + "/profiles", params={"node_key": node_key})).json()
    if not state.get("available"):
        return {"mode": "retry_profile", "accepted": False, "stage": "profiles/state", "reason": state.get("reason")}
    response = await client.post(prefix + "/profiles/approve", json={
        "node_key": node_key, "profile_id": PROFILE_ID, "base_version": state["base_version"],
        "catalog_hash": state["catalog_hash"], "rationale": "dry profile probe for text node recovery"})
    if response.status_code >= 300:
        return {"mode": "retry_profile", "accepted": False, "stage": "profiles/approve",
                "http": response.status_code, "reason": response.text[:500]}
    overlay_id = response.json()["overlay_id"]
    from app.services.observation import get_observer
    from app.services.profiles_v1 import ProfilesSupervisor
    try:
        ticket = await ProfilesSupervisor(get_observer()).recover(
            overlay_id=overlay_id, project_id=base.PROJECT_ID, execution_id=execution, incident_id=incident_id,
            node_key=node_key, idempotency_key=f"probe-profile-{node_key}", initiator="dry_probe",
            rationale="dry profile probe for text node recovery")
    except (ValueError, LookupError) as exc:
        return {"mode": "retry_profile", "accepted": False, "stage": "recover_node", "reason": str(exc)}
    return {"mode": "retry_profile", "accepted": True, "overlay_id": overlay_id, "ticket_status": ticket["status"]}


async def _poll(client, args, execution, node_key, data, counters, result) -> None:
    deadline = time.monotonic() + args.timeout_s
    last = None
    probed = False
    prefix = BASE + f"/observations/{execution}"
    decisions = actions = diagnoses = {}
    while time.monotonic() < deadline:
        decisions = (await client.get(prefix + "/decisions")).json()
        actions = (await client.get(prefix + "/actions")).json()
        diagnoses = (await client.get(prefix + "/diagnoses")).json()
        db_state = _db_state(data, node_key)
        incidents = [(i["type"], i["status"]) for i in diagnoses.get("incidents", [])
                     if i.get("currently_relevant", True)]
        state = (db_state["node"], db_state["attempts"], db_state["total_fails"], db_state["abandoned"], incidents,
                 [(d["status"], d["model_steps"]) for d in decisions["records"]],
                 [r["status"] for r in actions["recoveries"]], actions["requests_used"], dict(counters))
        if state != last:
            print(time.strftime("%H:%M:%S"), state, flush=True)
            last = state
        result.update(decisions=decisions, actions=actions, diagnoses=diagnoses, db_state=db_state,
                      counters=dict(counters))
        if args.probe_retry:
            if not probed and db_state["abandoned"] and db_state["node"] == "failed":
                if diagnoses.get("lagging"):
                    await asyncio.sleep(2)
                    continue
                relevant = [i for i in diagnoses.get("incidents", []) if i.get("currently_relevant")
                            and i["node_key"] == node_key]
                invalid = [i for i in relevant if i["type"] in {"invalid_json", "missing_payload"}]
                if not invalid:
                    first_seen = result.setdefault("_probe_wait_since", time.monotonic())
                    if time.monotonic() - first_seen < 60:
                        await asyncio.sleep(2)
                        continue
                    result["probe"] = {"sent": False,
                                       "reason": "no current invalid_json/missing_payload incident after 60s; "
                                                 "current types: " + str(sorted({i['type'] for i in relevant}))}
                    result.pop("_probe_wait_since", None)
                    return
                probed = True
                result["probe"] = await _probe(client, args, prefix, execution, node_key, invalid[0]["incident_id"])
                print("probe:", result["probe"], flush=True)
                if not result["probe"].get("accepted"):
                    return
            if probed:
                tickets = actions["recoveries"]
                if tickets and tickets[0]["status"] in {"human_review", "verified"}:
                    return
        else:
            recs = decisions["records"]
            if recs and recs[0]["status"] in TERMINAL_DECISION:
                return
            if not recs and db_state["abandoned"] and db_state["node"] == "failed" and not diagnoses.get("lagging"):
                result.setdefault("_exhausted_at", time.monotonic())
                if time.monotonic() - result["_exhausted_at"] > 45:
                    result["stopped_at"] = "native retries exhausted; no Decision record was created"
                    result.pop("_exhausted_at", None)
                    return
        if db_state["node"] in {"done", "waiting_hitl"} and not actions["recoveries"]:
            result["stopped_at"] = "node completed natively (no failure to recover)"
            return
        await asyncio.sleep(1)
    result["stopped_at"] = "timeout"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
