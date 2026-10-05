"""Live accuracy eval of the action planner on a sample of tests/fixtures/action_planner_eval.json.

Sends a small sample of the Russian eval requests through the real planner (strict validation,
catalog pre-filter, offered-only actions) and reports accuracy of action ids / params / clarification
and the tokens used. The planner only builds plan previews: nothing is created, approved or executed.
PAID (capped by --max-requests and --max-usd). Refuses to run without --live.
Only VIBECODE_API_KEY / VIBECODE_BASE_URL / GPT_RELAY_TOKEN / GPT_PROXY_URL / GPT_BASE_URL are read from
--env-file; nothing secret is printed or written.
The scoring helpers (load_cases, sample_cases, score_case) are offline and unit-tested.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

_ENV = ("VIBECODE_API_KEY", "VIBECODE_BASE_URL", "GPT_RELAY_TOKEN", "GPT_PROXY_URL", "GPT_BASE_URL")
FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "action_planner_eval.json"


def load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in _ENV:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def load_cases(path: Path = FIXTURE) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))["cases"]


def sample_cases(cases: list[dict], n: int) -> list[dict]:
    """Deterministic spread over the fixture, round-robin by workflow so every area is hit first."""
    n = max(0, min(n, len(cases)))
    by_wf: dict[str, list[dict]] = {}
    for c in cases:
        by_wf.setdefault(c.get("workflow", ""), []).append(c)
    out: list[dict] = []
    queues = [list(v) for _, v in sorted(by_wf.items())]
    while len(out) < n and any(queues):
        for q in queues:
            if q and len(out) < n:
                out.append(q.pop(0))
    return out


def _subset(expected, actual) -> bool:
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(k in actual and _subset(v, actual[k])
                                                for k, v in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(expected) <= len(actual)
                and all(_subset(e, a) for e, a in zip(expected, actual)))
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().lower() in actual.strip().lower()
    return expected == actual


def score_case(case: dict, steps: list[dict], clarification: str | None, error_code: str | None) -> dict:
    """Compare a planner result with the expectation. Params are compared as a subset (project_id ignored)."""
    kind = case["outcome"]
    got = [s.get("action_id") for s in steps]
    row = {"id": case["id"], "outcome": kind, "got_actions": got, "clarified": bool(clarification),
           "error": error_code}
    if kind == "plan":
        want = [s["action_id"] for s in case["steps"]]
        row["ids_ok"] = got == want
        row["set_ok"] = set(got) == set(want)
        row["params_ok"] = row["ids_ok"] and all(
            _subset({k: v for k, v in w["params"].items() if k != "project_id"}, g.get("params") or {})
            for w, g in zip(case["steps"], steps))
        row["ok"] = row["params_ok"]
    elif kind == "clarify":
        row["ok"] = bool(clarification) and not steps
    else:  # refuse: no steps are acceptable (clarification or a server rejection)
        row["ok"] = not steps
    return row


def summarize(rows: list[dict]) -> dict:
    plans = [r for r in rows if r["outcome"] == "plan"]
    return {
        "requests": len(rows),
        "accuracy": round(sum(1 for r in rows if r.get("ok")) / max(1, len(rows)), 3),
        "plan_ids_exact": round(sum(1 for r in plans if r.get("ids_ok")) / max(1, len(plans)), 3),
        "plan_params_ok": round(sum(1 for r in plans if r.get("params_ok")) / max(1, len(plans)), 3),
        "clarify_ok": [r["ok"] for r in rows if r["outcome"] == "clarify"],
        "refuse_ok": [r["ok"] for r in rows if r["outcome"] == "refuse"],
        "prompt_tokens": sum(r.get("prompt_tokens", 0) for r in rows),
        "completion_tokens": sum(r.get("completion_tokens", 0) for r in rows),
        "avg_prompt_tokens": round(sum(r.get("prompt_tokens", 0) for r in rows) / max(1, len(rows))),
        "est_cost_usd": round(sum(r.get("est_cost_usd") or 0.0 for r in rows), 5),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--seed-data", required=True, help="a data dir copy with a project to plan against")
    parser.add_argument("--project-id", type=int, default=2)
    parser.add_argument("--max-requests", type=int, default=10)
    parser.add_argument("--max-usd", type=float, default=0.10)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.live:
        print("needs --live")
        return 2
    os.environ.update(load_env(args.env_file))
    work = Path(tempfile.mkdtemp(prefix="eval-planner-"))
    shutil.copytree(args.seed_data, work / "data")
    os.environ.update(SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", TELEGRAM_ENABLED="false",
                      DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"))
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if not (os.environ.get("VIBECODE_API_KEY") or "").strip():
        print("VIBECODE_API_KEY missing in env file; nothing sent")
        return 3
    from app.main import _init_db
    from app.services.action_layer import planner
    from app.services.action_layer.spec import ensure_builtin_actions

    await _init_db()
    ensure_builtin_actions()
    rows: list[dict] = []
    spent = 0.0
    for case in sample_cases(load_cases(), args.max_requests):
        if spent >= args.max_usd:
            print(f"stopped: estimated spend {spent:.4f} USD reached --max-usd {args.max_usd}")
            break
        ctx = case.get("context") or {}
        started = time.monotonic()
        steps, clar, code, meta = [], None, None, {}
        try:
            outcome = await planner.plan_from_text(
                case["text"], operator="local_web_operator", project_id=args.project_id,
                selected_scene=ctx.get("selected_scene"), selected_frame=ctx.get("selected_frame"))
            steps = [s if isinstance(s, dict) else {"action_id": getattr(s, "action_id", None),
                                                    "params": getattr(s, "params", None)}
                     for s in (outcome.reply.steps or [])]
            clar, meta = outcome.reply.needs_clarification, outcome.meta
        except Exception as exc:  # noqa: BLE001 - a planner refusal/rejection is a scored result
            code = getattr(exc, "code", type(exc).__name__)
        row = score_case(case, steps, clar, code)
        row.update(seconds=round(time.monotonic() - started, 1),
                   prompt_tokens=meta.get("prompt_tokens", 0), completion_tokens=meta.get("completion_tokens", 0),
                   est_cost_usd=meta.get("est_cost_usd"), catalog_offered=meta.get("catalog_offered"))
        spent += row["est_cost_usd"] or 0.0
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False, default=str))
    report = {"summary": summarize(rows), "rows": rows}
    print(json.dumps(report["summary"], ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
