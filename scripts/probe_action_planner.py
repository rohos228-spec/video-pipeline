"""Live probe of the action planner against the real Vibecode route: at most 2 requests.

Sends two short Russian requests through the real planner (strict validation, catalog-only actions)
and prints the plan it produced, tokens and an uncalibrated catalog-price estimate.
The planner only builds a plan preview: nothing is created, approved or executed here.
PAID (a few cents at most). Refuses to run without --live.
Only VIBECODE_API_KEY / VIBECODE_BASE_URL / GPT_RELAY_TOKEN / GPT_PROXY_URL / GPT_BASE_URL are read from
--env-file; nothing secret is printed or written.
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
REQUESTS = ("покажи состояние доски монтажа", "поменяй местами кадры 2 и 3")


def load_env(path: str) -> dict:
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in _ENV:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def _step(step) -> dict:
    if isinstance(step, dict):
        return {"action_id": step.get("action_id"), "params": step.get("params")}
    return {"action_id": getattr(step, "action_id", None), "params": getattr(step, "params", None)}


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--seed-data", required=True, help="a data dir copy with a project to plan against")
    parser.add_argument("--project-id", type=int, default=2)
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    if not args.live:
        print("needs --live")
        return 2
    os.environ.update(load_env(args.env_file))
    work = Path(tempfile.mkdtemp(prefix="probe-planner-"))
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
    results = []
    for text in REQUESTS:
        started = time.monotonic()
        row = {"request": text}
        try:
            outcome = await planner.plan_from_text(text, operator="local_web_operator", project_id=args.project_id)
            reply = outcome.reply
            row.update(ok=True, seconds=round(time.monotonic() - started, 1),
                       explanation_ru=getattr(reply, "explanation_ru", None),
                       needs_clarification=getattr(reply, "needs_clarification", None),
                       steps=[_step(s) for s in (getattr(reply, "steps", None) or [])],
                       meta=outcome.meta)
        except Exception as exc:
            row.update(ok=False, seconds=round(time.monotonic() - started, 1), error=type(exc).__name__,
                       code=getattr(exc, "code", None), message=str(exc)[:300])
        results.append(row)
        print(json.dumps(row, ensure_ascii=False, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(results, indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
