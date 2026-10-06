"""Live probe of the REAL native paid media seams: at most one request per selected step.

Steps (each opt-in via --steps, default none):
  tts    ONE ElevenLabs TTS POST through app.services.elevenlabs_api.synthesize_speech (fixed Russian text, <=120 chars)
  sfx    ONE ElevenLabs sound-effect POST through app.services.sfx_gen._elevenlabs_sfx (shortest allowed duration)
  image  ONE Outsee Developer API image through app.bots.outsee_http.generate_image, wrapped exactly like the
         production API path in outsee_retry (media_spend.outsee_tracked -> outsee_guard intent rows)

Hard caps (docs/MEDIA_NODES_READINESS.md section 6): tts $0.10, sfx $0.05, image $0.10; global --max-submits 3 and
--total-usd 0.25 are checked BEFORE every step (also through the app's spend_guard.check_allowed, whose daily/project/run
caps are set to --total-usd for this throw-away database).

After each step the script reports what the app's own guards recorded (spend_ledger / provider_task_ledger rows), whether
the output file exists, and proves OFFLINE (no network) that a second call under media_spend.resume_only is refused.

PAID. Refuses to run without --live. Only the whitelisted variable names are read from --env-file; nothing secret is
printed or written (every output string is scrubbed of the values that were read). DATA_DIR, SQLITE_PATH and the tracker
DB live in a temp dir that is removed unless --keep-work is given.
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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

_ENV = (
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_PROXY_URL",
    "ELEVENLABS_PROXY_ALT_URL",
    "AUDIO_USE_ELEVENLABS_FALLBACK",
    "OUTSEE_API_KEY",
    "OUTSEE_API_BASE_URL",
    "OUTSEE_DEFAULT_IMAGE_MODEL",
)
_SECRET = ("ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL", "ELEVENLABS_PROXY_ALT_URL", "OUTSEE_API_KEY")

STEPS = ("tts", "sfx", "image")
STEP_CAP_USD = {"tts": 0.10, "sfx": 0.05, "image": 0.10}
STEP_NEEDS = {"tts": "ELEVENLABS_API_KEY", "sfx": "ELEVENLABS_API_KEY", "image": "OUTSEE_API_KEY"}

TTS_TEXT = "Привет! Это короткая проверка озвучки."
SFX_PROMPT = "short soft click"
SFX_DURATION_S = 0.5  # minimum the production seam allows (min(max(duration, 0.5), 22.0))
IMAGE_PROMPT = "a plain white circle on a black background"
assert len(TTS_TEXT) <= 120

DEFAULT_MAX_SUBMITS = 3
DEFAULT_TOTAL_USD = 0.25
PROBE_PROJECT_ID = 900001  # int project id so the Outsee intent guard is active (key must start with frame_)


def load_env(path: str) -> dict:
    """Only the whitelisted names; everything else in the file is ignored."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in _ENV:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def estimate_usd(step: str) -> float:
    """Pre-flight estimate. tts/sfx use the app's own (uncalibrated) constants; image price is unknown -> its cap."""
    if step == "tts":
        try:
            from app.services.media_spend import ELEVENLABS_USD_PER_CHAR
        except Exception:  # noqa: BLE001
            ELEVENLABS_USD_PER_CHAR = 0.0003
        return round(len(TTS_TEXT) * ELEVENLABS_USD_PER_CHAR, 6)
    if step == "sfx":
        try:
            from app.services.media_spend import ELEVENLABS_SFX_USD_PER_SEC
        except Exception:  # noqa: BLE001
            ELEVENLABS_SFX_USD_PER_SEC = 0.012
        return round(SFX_DURATION_S * ELEVENLABS_SFX_USD_PER_SEC, 6)
    return STEP_CAP_USD["image"]


@dataclass
class Deps:
    """Everything that touches the app, the DB or the network; tests inject fakes."""
    prepare: Callable[[dict, Path], None]
    runners: dict[str, Callable[[Path], Awaitable[dict]]]
    snapshot: Callable[[], Awaitable[dict]]  # {"actual_rows", "task_events", "spent_usd"}
    guard: Callable[[float], Awaitable[dict]]  # spend_guard.check_allowed shape: {"allowed": bool, "code": str}
    work: Path | None = None
    cleanup: bool = True
    real: bool = False  # True: create the app schema in the throw-away DB before the first step


class _Printer:
    def __init__(self, env: dict):
        vals = {v for v in env.values() if isinstance(v, str) and len(v) >= 6}
        self._vals = sorted(vals, key=len, reverse=True)

    def scrub(self, text: str) -> str:
        for v in self._vals:
            text = text.replace(v, "***")
        return text

    def row(self, row: dict) -> None:
        print(self.scrub(json.dumps(row, ensure_ascii=False, default=str)))


def _file_facts(path: Any) -> dict:
    p = Path(str(path)) if path else None
    exists = bool(p and p.is_file())
    return {"output_exists": exists, "output_bytes": p.stat().st_size if exists else 0}


async def _audio_seconds(path: Any) -> float | None:
    try:
        from app.services.media_probe import probe_duration
        return round(float(await probe_duration(Path(str(path)))), 3)
    except Exception:  # noqa: BLE001
        return None


# ----------------------------------------------------------------------------- real seams

def _real_prepare(env: dict, work: Path) -> None:
    os.environ.update(env)
    os.environ.update(SUPABASE_URL="http://127.0.0.1:9", SUPABASE_KEY="unused", TELEGRAM_ENABLED="false",
                      DATA_DIR=str(work / "data"), SQLITE_PATH=str(work / "data" / "state.db"))
    (work / "data").mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent.parent
    os.chdir(root)
    sys.path.insert(0, str(root))
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"


async def _real_init() -> None:
    from app.main import _init_db
    await _init_db()


def _real_deps(work: Path, total_usd: float, cleanup: bool) -> Deps:
    def prepare(env: dict, w: Path) -> None:
        # caps of the app's own spend guard: the throw-away DB only ever sees this run's spend
        os.environ.update(SPEND_DAILY_CAP_USD=str(total_usd), SPEND_PROJECT_CAP_USD=str(total_usd),
                          SPEND_RUN_CAP_USD=str(total_usd))
        _real_prepare(env, w)

    async def snapshot() -> dict:
        from sqlalchemy import func, select
        from app.db import SessionLocal
        from app.services import media_spend, spend_guard
        await media_spend.drain()
        async with SessionLocal() as s:
            await spend_guard.ensure_schema(s)
            await spend_guard.ensure_task_schema(s)
            actual = await s.scalar(select(func.count()).select_from(spend_guard.SpendLedger)
                                    .where(spend_guard.SpendLedger.kind == "actual"))
            spent = await s.scalar(select(func.coalesce(func.sum(spend_guard.SpendLedger.usd), 0.0)))
            tasks = await s.scalar(select(func.count()).select_from(spend_guard.ProviderTaskLedger))
            await s.commit()
        return {"actual_rows": int(actual or 0), "task_events": int(tasks or 0), "spent_usd": float(spent or 0.0)}

    async def guard(est: float) -> dict:
        from app.db import SessionLocal
        from app.services import spend_guard
        async with SessionLocal() as s:
            res = await spend_guard.check_allowed(None, est, "live_media_probe", session=s)
            await s.commit()
            return res

    return Deps(prepare=prepare, runners=_real_runners(), snapshot=snapshot, guard=guard, work=work, cleanup=cleanup, real=True)


def _real_runners() -> dict[str, Callable[[Path], Awaitable[dict]]]:
    async def tts(work: Path) -> dict:
        from app.services import elevenlabs_api as el
        from app.services import media_spend
        out = work / "tts" / "probe_tts.mp3"
        res: dict = {"output": out, "production_function": "elevenlabs_api.synthesize_speech"}
        try:
            with el.tts_run_budget(1):  # the app's own per-run POST ceiling: a retry POST is refused
                await el.synthesize_speech(TTS_TEXT, out)
        except Exception as exc:  # noqa: BLE001
            res["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        # offline: same text, same dir -> served from the sidecar cache, zero POST
        if out.is_file():
            with el.tts_run_budget(1) as budget:
                try:
                    await el.synthesize_speech(TTS_TEXT, out)
                    res["second_call_cache_hit_no_post"] = budget[0] == 0
                except Exception as exc:  # noqa: BLE001
                    res["second_call_cache_hit_no_post"] = False
                    res["cache_check_error"] = type(exc).__name__
        # offline: a cache MISS (fresh dir) under resume_only must be refused before any byte is sent
        hits: list = []
        try:
            with media_spend.resume_only(hits):
                await el.synthesize_speech(TTS_TEXT, work / "tts_resume_check" / "probe_tts.mp3")
            res["second_call_refused"] = False
        except media_spend.ResumeOnlyBlocked:
            res["second_call_refused"] = len(hits) == 1
        except Exception as exc:  # noqa: BLE001
            res["second_call_refused"] = False
            res["resume_check_error"] = type(exc).__name__
        res["audio_seconds"] = await _audio_seconds(out) if out.is_file() else None
        return res

    async def sfx(work: Path) -> dict:
        from app.services import media_spend, sfx_gen
        out = work / "sfx" / "probe_sfx.mp3"
        res: dict = {"output": out, "production_function": "sfx_gen._elevenlabs_sfx (track_eleven_sfx)"}
        try:
            await sfx_gen._elevenlabs_sfx(SFX_PROMPT, SFX_DURATION_S, out)
        except Exception as exc:  # noqa: BLE001
            res["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        hits: list = []
        try:
            with media_spend.resume_only(hits):
                await sfx_gen._elevenlabs_sfx(SFX_PROMPT, SFX_DURATION_S, work / "sfx_resume_check" / "probe_sfx.mp3")
            res["second_call_refused"] = False
        except media_spend.ResumeOnlyBlocked:
            res["second_call_refused"] = len(hits) == 1
        except Exception as exc:  # noqa: BLE001
            res["second_call_refused"] = False
            res["resume_check_error"] = type(exc).__name__
        res["audio_seconds"] = await _audio_seconds(out) if out.is_file() else None
        return res

    async def image(work: Path) -> dict:
        from app.bots import outsee_http
        from app.services import media_spend
        from app.settings import settings
        # same wrapping as outsee_retry.generate_image_with_retries uses for the API path
        tracked = media_spend.outsee_tracked(outsee_http.generate_image, "image")
        slug = outsee_http.studio_id_to_outsee_image_slug(settings.outsee_default_image_model or None)
        out = work / "image" / "frame_probe.png"  # frame_ prefix = resumable key for the outsee_guard intent row
        res: dict = {"output": out, "model": slug,
                     "production_function": "outsee_http.generate_image via media_spend.outsee_tracked"}

        async def call(path: Path):
            return await tracked(IMAGE_PROMPT, path, model_slug=slug, aspect_ratio="9:16", resolution="2K",
                                 timeout=300.0, project_id=PROBE_PROJECT_ID)
        try:
            result = await call(out)
            res["output"] = result.file_path
        except Exception as exc:  # noqa: BLE001
            res["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        hits: list = []
        try:
            with media_spend.resume_only(hits):
                await call(work / "image_resume_check" / "frame_probe.png")
            res["second_call_refused"] = False
        except media_spend.ResumeOnlyBlocked:
            res["second_call_refused"] = len(hits) == 1
        except Exception as exc:  # noqa: BLE001
            res["second_call_refused"] = False
            res["resume_check_error"] = type(exc).__name__
        return res

    return {"tts": tts, "sfx": sfx, "image": image}


# ----------------------------------------------------------------------------- driver

def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--env-file", default="")
    parser.add_argument("--steps", default="", help="comma list of: " + ",".join(STEPS) + " (default: none)")
    parser.add_argument("--max-submits", type=int, default=DEFAULT_MAX_SUBMITS)
    parser.add_argument("--total-usd", type=float, default=DEFAULT_TOTAL_USD)
    parser.add_argument("--out", default="", help="optional JSON file for the rows (scrubbed)")
    parser.add_argument("--keep-work", action="store_true", help="keep the temp dir with the produced files")
    return parser.parse_args(argv)


async def _run(args: argparse.Namespace, steps: list[str], env: dict, deps: Deps, pr: _Printer) -> int:
    work = deps.work
    rows: list[dict] = []
    before = await deps.snapshot()
    submits_done = 0
    planned_usd = 0.0
    halted = ""
    for step in steps:
        row: dict[str, Any] = {"step": step, "ok": False, "cap_usd": STEP_CAP_USD[step]}
        est = estimate_usd(step)
        row["est_usd"] = est
        refuse = ""
        snap0 = await deps.snapshot()
        spent_so_far = max(snap0["spent_usd"] - before["spent_usd"], planned_usd)
        if halted:
            refuse = f"halted: {halted}"
        elif not env.get(STEP_NEEDS[step]):
            row["skipped"] = True
            refuse = f"{STEP_NEEDS[step]} missing in env file; nothing sent"
        elif est > STEP_CAP_USD[step] + 1e-9:
            refuse = f"estimate {est} exceeds step cap {STEP_CAP_USD[step]}"
        elif submits_done + 1 > args.max_submits:
            refuse = f"--max-submits {args.max_submits} reached"
        elif spent_so_far + est > args.total_usd + 1e-9:
            refuse = f"--total-usd {args.total_usd} would be exceeded ({spent_so_far:.4f} + {est:.4f})"
        else:
            verdict = await deps.guard(est)
            if not verdict.get("allowed", False):
                refuse = f"spend_guard refused: {verdict.get('code') or 'not allowed'}"
        if refuse:
            row.update(refused=True, error=refuse, seconds=0.0, submits=0)
            rows.append(row)
            pr.row(row)
            continue

        started = time.monotonic()
        try:
            res = await deps.runners[step](work)
        except Exception as exc:  # noqa: BLE001
            res = {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
        row["seconds"] = round(time.monotonic() - started, 1)
        snap1 = await deps.snapshot()
        submits = snap1["actual_rows"] - snap0["actual_rows"]
        spend = round(snap1["spent_usd"] - snap0["spent_usd"], 6)
        planned_usd += est
        submits_done += max(1, submits)
        output = res.pop("output", None)
        row.update(_file_facts(output))
        if res.get("audio_seconds") is not None or step in ("tts", "sfx"):
            row["audio_seconds"] = res.pop("audio_seconds", None)
        row.update(submits=submits, task_events=snap1["task_events"] - snap0["task_events"],
                   guard_spend_usd=spend, guard_spend_total_usd=round(snap1["spent_usd"] - before["spent_usd"], 6))
        row.update({k: v for k, v in res.items() if k != "error"})
        row["ok"] = "error" not in res and row["output_exists"]
        if "error" in res:
            row["error"] = res["error"]
        elif not row["output_exists"]:
            row["error"] = "no output file"
        violations = []
        if submits > 1:
            violations.append(f"{submits} provider submits recorded for one unit")
        if spend > STEP_CAP_USD[step] + 1e-9:
            violations.append(f"recorded spend {spend} above step cap {STEP_CAP_USD[step]}")
        if row.get("second_call_refused") is not True:
            violations.append("second call under resume_only was not proven refused")
        if violations:
            row["violations"] = violations
            halted = "; ".join(violations)
        rows.append(row)
        pr.row(row)

    summary = {"summary": True, "steps": steps, "submits_total": submits_done,
               "planned_usd": round(planned_usd, 6), "max_submits": args.max_submits, "total_usd_cap": args.total_usd,
               "all_ok": bool(rows) and all(r.get("ok") for r in rows),
               "violations": [v for r in rows for v in r.get("violations", [])]}
    pr.row(summary)
    if args.out:
        Path(args.out).write_text(pr.scrub(json.dumps(rows + [summary], indent=1, ensure_ascii=False, default=str)),
                                  encoding="utf-8")
    return 0 if summary["all_ok"] and not summary["violations"] else 1


def main(argv: list[str] | None = None, *, deps: Deps | None = None) -> int:
    args = parse_args(argv)
    if not args.live:
        print("needs --live")
        return 2
    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    unknown = [s for s in steps if s not in STEPS]
    if unknown:
        print(f"unknown step(s): {', '.join(unknown)}; allowed: {', '.join(STEPS)}")
        return 2
    if not steps:
        print("no steps selected; pass --steps tts,sfx,image (each step is opt-in and paid)")
        return 2
    steps = list(dict.fromkeys(steps))
    if args.max_submits < 1 or args.total_usd <= 0:
        print("--max-submits must be >= 1 and --total-usd > 0")
        return 2
    if not args.env_file:
        print("--env-file is required")
        return 2
    env = load_env(args.env_file)
    pr = _Printer(env)
    work = (deps.work if deps is not None else None) or Path(tempfile.mkdtemp(prefix="probe-media-"))
    if deps is None:
        deps = _real_deps(work, args.total_usd, not args.keep_work)
    deps.work = work
    try:
        deps.prepare(env, work)

        async def go() -> int:
            if deps.real:
                await _real_init()
            return await _run(args, steps, env, deps, pr)

        return asyncio.run(go())
    finally:
        if deps.cleanup and not args.keep_work:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
