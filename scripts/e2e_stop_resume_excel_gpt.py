"""Real-worker verification of user stop and continue on the excel_gpt node (zero cost, dry only).

The whole application (FastAPI + real worker + observer + diagnosis + Decision supervisor) runs
against a copied seed. Every worker POST to the provider endpoint is answered locally with a
malformed-JSON SSE body and every Decision/recovery (provider-bound) request is refused, so cost
is exactly 0. There is no --live mode on purpose.

Two scenarios (one process each, fresh data copy):
  project   POST /api/projects/<id>/stop       (the UI "stop" button: step_cancel stop-file + user_stop)
            then POST /api/projects/<id>/continue (the UI "continue" button)
  observer  POST /api/projects/<id>/observations/{id}/stop (observer-only stop, business_action=None)

Usage:
    python scripts/e2e_stop_resume_excel_gpt.py --scenario project --seed-dir <seed> --work-dir <dir>
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import importlib.util
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

_BASE_SCRIPT = Path(__file__).with_name("e2e_live_excel_gpt_recovery.py")
_spec = importlib.util.spec_from_file_location("e2e_live_excel_gpt_recovery", _BASE_SCRIPT)
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

NODE, PROFILE_ID, MALFORMED = base.NODE, base.PROFILE_ID, base.MALFORMED
SLUG: str = base.SLUG
ROOT_URL: str | None = None
COUNTER_KEYS = ("injected_worker", "real_decision", "real_worker_recovery", "refused")


def total_requests(counters: dict) -> int:
    """All provider-bound attempts seen by the transport hook, injected or refused."""
    return sum(int(counters.get(k, 0)) for k in COUNTER_KEYS)


def incident_pairs(diagnoses: dict) -> list[tuple[str, str]]:
    """Sorted (type, status) pairs from the /diagnoses payload."""
    return sorted((str(i.get("type")), str(i.get("status"))) for i in (diagnoses or {}).get("incidents", []))


def event_kinds(events: dict) -> dict[str, int]:
    return dict(collections.Counter(e["kind"] for e in (events or {}).get("events", [])))


def counters_frozen_after(samples: list[dict], t_mark: float, settle_s: float) -> bool:
    """True when the request total does not change in any sample taken settle_s after t_mark.

    samples: [{"t": monotonic seconds, "counters": {...}}]. Samples inside the settle window are
    ignored (an in-flight request may still land); with no sample past the window the answer is
    False because nothing was proven.
    """
    late = [s for s in samples if s["t"] >= t_mark + settle_s]
    if not late:
        return False
    first = total_requests(late[0]["counters"])
    return all(total_requests(s["counters"]) == first for s in late)


def counter_delta(samples: list[dict], t_from: float) -> int:
    after = [s for s in samples if s["t"] >= t_from]
    if not after:
        return 0
    before = [s for s in samples if s["t"] < t_from]
    start = total_requests(before[-1]["counters"]) if before else 0
    return total_requests(after[-1]["counters"]) - start


def probe_json(resp) -> dict:
    """Compact, secret-free record of an HTTP probe: status plus the error detail or ok keys."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    out = {"status": resp.status_code}
    if detail is not None:
        out["detail"] = str(detail)[:200]
    elif isinstance(body, dict):
        out["keys"] = sorted(k for k in body if k != "project")[:8]
    return out


def decision_steps(decisions: dict) -> list[tuple[str, int]]:
    return [(r["status"], int(r["model_steps"])) for r in (decisions or {}).get("records", [])]


def classify_continue(before: dict, after: dict) -> str:
    """Factual one-word outcome of /continue from two snapshots (see snapshot())."""
    if after["node_status"] == "running" or after["counters_total"] > before["counters_total"]:
        return "worker_restarted"
    if before["user_stop"] and not after["user_stop"]:
        if after["node_status"] != before["node_status"] or after["project_status"] != before["project_status"]:
            return "state_changed_no_worker_run"
        return "stop_cleared_only"
    return "no_effect"


def _args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--scenario", choices=("project", "observer"), default="project")
    p.add_argument("--seed-dir", required=True)
    p.add_argument("--slug", default=None, help="slug of the project inside the seed (default: the fixture slug)")
    p.add_argument("--project-id", type=int, default=None, help="project id in the seed (default: looked up by slug)")
    p.add_argument("--work-dir", required=True)
    p.add_argument("--running-timeout-s", type=int, default=120)
    p.add_argument("--after-stop-s", type=int, default=45, help="observe window after stop")
    p.add_argument("--after-continue-s", type=int, default=60, help="observe window after continue")
    return p.parse_args()


def _project_row(data: Path) -> dict:
    with sqlite3.connect(data / "videos" / SLUG / "project.db") as db:
        status, meta = db.execute("SELECT status, meta FROM projects WHERE id=?", (base.PROJECT_ID,)).fetchone()
    meta = json.loads(meta or "{}")
    return {"project_status": status, "user_stop": bool(meta.get("user_stop")),
            "auto_await_manual_start": bool(meta.get("auto_await_manual_start")),
            "step_failure": bool(meta.get("step_failure"))}


def _node_row(data: Path) -> dict:
    with sqlite3.connect(data / "videos" / SLUG / "project.db") as db:
        row = db.execute("SELECT status, attempts FROM node_runs WHERE node_key=?", (NODE,)).fetchone()
    return {"node_status": row[0], "node_attempts": row[1]} if row else {"node_status": None, "node_attempts": None}


async def snapshot(client, data: Path, execution: str, counters: dict) -> dict:
    prefix = f"{ROOT_URL}/observations"
    decisions = (await client.get(f"{prefix}/{execution}/decisions")).json()
    actions = (await client.get(f"{prefix}/{execution}/actions")).json()
    diag = (await client.get(f"{prefix}/{execution}/diagnoses")).json()
    events = (await client.get(f"{prefix}/{execution}/events", params={"limit": 500})).json()
    scopes = (await client.get(prefix)).json()
    scope = next((s for s in scopes if s.get("execution_id") == execution), {})
    stop_file = Path(os.environ["DATA_DIR"]) / ".stop" / "project_2.stop"
    snap = {**_project_row(data), **_node_row(data),
            "observer_state": scope.get("observer_state"),
            "stop_file": stop_file.exists(),
            "decisions": decision_steps(decisions), "decision_enabled": decisions.get("enabled"),
            "recoveries": [r["status"] for r in actions["recoveries"]],
            "actions_held": actions["held"], "requests_used": actions["requests_used"],
            "action_kinds": sorted({r["action_kind"] + ":" + r["status"] for r in actions["records"]}),
            "incidents": incident_pairs(diag), "events": event_kinds(events),
            "counters": dict(counters), "counters_total": total_requests(counters)}
    return snap


def _key(s: dict) -> str:
    return json.dumps({k: v for k, v in s.items() if k != "events"}, sort_keys=True, default=str)


async def observe(client, data, execution, counters, seconds, samples, label, stop_when=None):
    end = time.monotonic() + seconds
    last = None
    snap = None
    while time.monotonic() < end:
        snap = await snapshot(client, data, execution, counters)
        samples.append({"t": time.monotonic(), "counters": dict(counters), "snap": snap, "label": label})
        if _key(snap) != last:
            print(time.strftime("%H:%M:%S"), label, {k: v for k, v in snap.items() if k != "events"})
            last = _key(snap)
        if stop_when and stop_when(snap):
            break
        await asyncio.sleep(1)
    return snap


async def main() -> int:
    args = _args()
    work, seed = Path(args.work_dir).resolve(), Path(args.seed_dir).resolve()
    work.mkdir(parents=True, exist_ok=True)
    data = base._prepare_data(seed, work, args.slug, args.project_id)
    global SLUG, ROOT_URL
    SLUG, ROOT_URL = base.SLUG, base.BASE
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ.update({
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "TEXT_LLM_PROVIDER": "kie", "GPT_API_KEY": "dry-run-key-not-a-real-credential",
        "GPT_BASE_URL": "https://api.kie.ai", "GPT_CHAT_PATH": "/codex/v1/responses",
        "GPT_MODEL": "gpt-5-6-sol", "GPT_API_MODE": "responses", "GPT_MAX_RETRIES": "1",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1", "WEB_PORT": str(base.PORT), "HITL_AUTO_APPROVE": "false",
        "BROWSER_CDP_AUTO_RECOVER": "false", "OUTSEE_HTTP_FALLBACK_CDP": "false",
        "AUDIO_USE_ELEVENLABS_FALLBACK": "false", "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
        "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
        "ORCHESTRATOR_RUNTIME_CONFIG": str(base._runtime_config(work)),
    })
    import httpx
    import app.services.api_tracker_hook as tracker
    tracker_db = work / "tracker.db"
    if tracker_db.exists():
        tracker_db.unlink()
    tracker._TRACKER_DB = tracker_db
    from app.services import observation
    from app.services.decision_transport import current as decision_hook

    counters = {k: 0 for k in COUNTER_KEYS}
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if request.method != "POST" or not request.url.path.endswith("/codex/v1/responses"):
            return await original_send(client, request, **kwargs)
        ctx = observation._attempt.get()
        is_decision = decision_hook.get() is not None
        is_recovery = bool(ctx and ctx.session.info.get("recovery_attempt_id") == ctx.attempt_id)
        if not is_decision and not is_recovery:
            counters["injected_worker"] += 1
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=base._sse(MALFORMED, f"resp_injected_{counters['injected_worker']}"),
                                  request=request)
        counters["refused"] += 1
        raise RuntimeError("dry-run: provider request refused (%s)" % ("decision" if is_decision else "recovery"))

    httpx.AsyncClient.send = send
    from app import main as app_main
    task = asyncio.create_task(app_main.main())
    samples: list[dict] = []
    result: dict = {"scenario": args.scenario, "node": NODE, "checks": {}}
    checks = result["checks"]
    try:
        await base._wait_api()
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            from _operator_headers import attach_operator_token
            await attach_operator_token(client, ROOT_URL)
            options = (await client.get(ROOT_URL + "/observations/launch-options")).json()
            response = await client.post(ROOT_URL + "/observations/launch", json={
                "workflow_run_id": options["workflow_run_id"], "graph_version": options["graph_version"],
                "node_keys": [NODE], "strategies": ["wait", "human_review", "retry_profile"],
                "profile_ids": [PROFILE_ID], "profile_catalog_hash": options["profile_catalog_hash"],
                "request_limit": 40, "rationale": "dry e2e stop/continue verification",
                "idempotency_key": "e2e-stop-resume-01"})
            print("launch:", response.status_code, response.text[:300] if response.status_code >= 300 else "ok")
            response.raise_for_status()
            execution = response.json()["execution_id"]
            result["execution_id"] = execution

            pre = await observe(client, data, execution, counters, args.running_timeout_s, samples, "pre-stop",
                                stop_when=lambda s: s["node_status"] == "running" and s["counters"]["injected_worker"] >= 1)
            checks["running_before_stop"] = pre["node_status"] == "running"
            result["before_stop"] = pre
            if not checks["running_before_stop"]:
                print("node never reached running; abort")
                return 1

            t_stop = time.monotonic()
            if args.scenario == "project":
                resp = await client.post(ROOT_URL + "/stop")
                print(f"POST {ROOT_URL}/stop ->", resp.status_code, {k: v for k, v in resp.json().items() if k != "project"})
            else:
                resp = await client.post(f"{ROOT_URL}/observations/{execution}/stop")
                print("POST observations/stop ->", resp.status_code, resp.json())
            result["stop_response"] = {"status": resp.status_code, "body": {k: v for k, v in resp.json().items() if k != "project"}}
            after_stop = await observe(client, data, execution, counters, args.after_stop_s, samples, "after-stop")
            result["after_stop"] = after_stop
            checks["counters_frozen_after_stop"] = counters_frozen_after(samples, t_stop, 8.0)
            checks["requests_after_stop"] = counter_delta(samples, t_stop + 8.0)
            checks["decision_model_steps_after_stop"] = sum(m for _, m in after_stop["decisions"])
            checks["incident_user_stop"] = any(t == "user_stop" for t, _ in after_stop["incidents"])

            if args.scenario == "project":
                before_cont = after_stop
                t_cont = time.monotonic()
                resp = await client.post(ROOT_URL + "/continue")
                body = resp.json()
                print(f"POST {ROOT_URL}/continue ->", resp.status_code, {k: v for k, v in body.items() if k != "project"})
                result["continue_response"] = {"status": resp.status_code, "body": {k: v for k, v in body.items() if k != "project"}}
                after_cont = await observe(client, data, execution, counters, args.after_continue_s, samples, "after-continue")
                result["after_continue"] = after_cont
                checks["continue_outcome"] = classify_continue(before_cont, after_cont)
                checks["requests_after_continue"] = counter_delta(samples, t_cont)
                probes = result["probes"] = {}
                resp = await client.post(f"{ROOT_URL}/observations/{execution}/actions/start", json={
                    "node_key": NODE, "step_code": "excel_gpt", "rationale": "probe start after stop",
                    "idempotency_key": "e2e-stop-resume-start-01"})
                probes["managed_actions_start"] = probe_json(resp)
                resp = await client.post(f"{ROOT_URL}/steps/excel_gpt/run", params={"node_key": NODE})
                probes["native_run_while_managed"] = probe_json(resp)
                print("probes:", probes)
                resp = await client.post(f"{ROOT_URL}/observations/{execution}/actions/close", json={
                    "rationale": "close managed permission to allow native run", "idempotency_key": "e2e-stop-resume-close-01"})
                probes["managed_actions_close"] = probe_json(resp)
                t_native = time.monotonic()
                resp = await client.post(f"{ROOT_URL}/steps/excel_gpt/run", params={"node_key": NODE})
                probes["native_run_after_close"] = probe_json(resp)
                print("probes:", probes)
                after_native = await observe(client, data, execution, counters, args.after_continue_s, samples, "after-native-run")
                result["after_native_run"] = after_native
                checks["native_run_after_close_started_worker"] = (
                    after_native["node_status"] == "running" or counter_delta(samples, t_native) > 0)
                checks["user_stop_after_native_run"] = after_native["user_stop"]
                await client.post(ROOT_URL + "/stop")
                await asyncio.sleep(2)
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
    result["counters"] = dict(counters)
    result["tracker_rows"] = len(base._tracker_rows(tracker_db))
    (work / f"result-{args.scenario}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== RESULT ===")
    print(json.dumps({"probes": result.get("probes"), "after_native_run": (
        {k: result["after_native_run"][k] for k in ("node_status", "project_status", "user_stop", "decisions", "incidents", "counters")}
        if "after_native_run" in result else None), "checks": checks, "counters": result["counters"], "tracker_rows": result["tracker_rows"],
                      "stop_response": result.get("stop_response"), "continue_response": result.get("continue_response"),
                      "after_stop": {k: result["after_stop"][k] for k in (
                          "node_status", "project_status", "user_stop", "stop_file", "observer_state", "decisions",
                          "incidents", "events")},
                      "after_continue": {k: result["after_continue"][k] for k in (
                          "node_status", "project_status", "user_stop", "decisions", "incidents")} if "after_continue" in result else None},
                     ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
