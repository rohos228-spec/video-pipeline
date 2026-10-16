"""Plan step F05: kill the process RIGHT AFTER a real paid Outsee submit; after the restart there must be NO second
paid task for the same frame.

Sibling of scripts/e2e_media_production_route.py (imported through `_load_sibling`, not copied, not edited: its Gate,
FakeOutsee, seed, env and check helpers are reused, so the safety model is the same). That stand proves the machinery
AROUND one submit when the outcome is lost INSIDE a living process. This stand proves the one case a living process can
never prove: the process itself dies, nothing is cleaned up, and the next process must not pay again.

Target: node `images`, ONE frame, the real native step `generate_images.run` (the paid seam: spend_guard.paid_seam ->
outsee_http._post_generate, under outsee_idempotency intent -> result -> consumed rows and the provider-ledger intent).

Two child processes, each a separate python, on the SAME work dir and the same state.db:
  phase 1  runs generate_images.run for the seeded frame. The send hook lets exactly ONE paid POST through, writes the
           wire counter to <work>/wire.jsonl (before the request bytes and again when the response has arrived), and
           the moment the provider's answer (the whole submit response, task id included) is in the process, BEFORE the
           status poll, the download, the file, the 'result' row or any spend row: os._exit(137). A real hard kill: no
           finally, no atexit, no flush, no rollback.
  phase 2  a NEW process on the same state: what the owner does after the restart (reconcile on startup, the step is
           started again). Whatever the production code does is recorded as it is, not assumed. The Gate preloads the
           wire counter, so the whole stand allows ONE paid POST across both phases: a second one is refused AND counted.

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no .env: a fake Outsee API on 127.0.0.1 (in the parent, so it survives the kill) plays the
          provider; every non-loopback request is refused and counted.
          --dry-scenario second_submit is the NEGATIVE CONTROL: before the restart the stand lets a human release the
          open attempt (outsee_idempotency.release, the owner's own tool) and widens the Gate to two posts, so the
          stand's own production code sends a SECOND POST. The verdict must be NOT PASSED; if it is not, the check is blind.
  --live  PAID, capped: the one paid submit goes to the real Outsee. Needs --env-file; only OUTSEE_API_KEY,
          OUTSEE_API_BASE_URL and OUTSEE_DEFAULT_IMAGE_MODEL are read from it, nothing is printed. Hard caps:
          --max-real-posts (default 1, exactly 1 allowed), --max-usd (default 0.30, hard ceiling 1.00; refused when
          max-real-posts x $0.10 conservative price exceeds it). The Gate refuses everything but the intended Outsee
          requests. NOT RUN in live mode when written.

Exit codes: 0 passed, 1 not passed, 2 bad arguments, 3 refused before anything was sent.

Usage (repository root, project venv):
    python scripts/e2e_media_kill_after_submit.py --work-dir <empty dir outside the repository> --dry
    python scripts/e2e_media_kill_after_submit.py --work-dir <dir> --dry --dry-scenario second_submit
    python scripts/e2e_media_kill_after_submit.py --work-dir <dir> --live --env-file <env file>
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_kill_stand", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


prod = _load_sibling("e2e_media_production_route")
qc = prod.qc

FAKE_PORT = 18771
NODE = prod.NODE
FRAME_KEY = prod.FRAME_KEY
KILL_EXIT_CODE = 137
DEFAULT_MAX_REAL_POSTS = 1
DEFAULT_MAX_USD = 0.30
HARD_CEILING_USD = 1.00
SCENARIOS = ("none", "second_submit")
OPEN_VERDICT_CODES = ("outsee_uncertain_submit", "outsee_resume_only_no_result")
WIRE_FILE = "wire.jsonl"
SEED_FILE = "seed.json"

EXIT_PASS, EXIT_FAIL, EXIT_ARGS, EXIT_REFUSED = 0, 1, 2, 3



def authorisation_problem(max_real_posts: int, max_usd: float) -> str | None:
    """Same rule as the production-route stand, plus a hard ceiling of $1.00 on what can ever be authorised."""
    if max_usd > HARD_CEILING_USD + 1e-9:
        return f"--max-usd {max_usd} exceeds the hard ceiling ${HARD_CEILING_USD}"
    return prod.authorisation_problem(max_real_posts, max_usd)


def wire_append(path: Path, event: dict) -> None:
    """One durable line (open, write, fsync, close): it must survive an os._exit that follows at once."""
    line = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with open(path, "a", encoding="utf-8") as f:
        f.write(line)
        f.flush()
        os.fsync(f.fileno())


def read_wire(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except ValueError:
                out.append({"event": "unreadable_line"})
    return out


def wire_posts(wire: list[dict], phase: int | None = None) -> int:
    """Paid POSTs that LEFT the process (counted before their bytes were sent)."""
    return sum(1 for e in wire if e.get("event") == "post_sent" and (phase is None or e.get("phase") == phase))


def classify_phase2(phase2: dict) -> str:
    """What the production code did on the restart, from the child's own record (never assumed)."""
    if not phase2:
        return "no_record"
    code = str(phase2.get("error_code") or "")
    if code == "outsee_uncertain_submit":
        return "refused_unknown_outcome"
    if code == "outsee_resume_only_no_result":
        return "refused_resume_only"
    if phase2.get("adopted_recorded_result"):
        return "reused_recorded_result"
    if phase2.get("new_post_attempted"):
        return "sent_second_request"
    if phase2.get("step_outcome") == "returned" and phase2.get("valid_image_on_disk"):
        return "completed_with_file"
    return "other"


def reality_report(mode: str, snap: dict) -> dict:
    live = mode == "live"
    paid = snap.get("wire_posts", 0)
    return {
        "real": [x for x in (
            "two separate python processes on one work dir and one state.db; the first one is hard-killed by os._exit(137)",
            "native step generate_images.run, outsee_retry, outsee_http, media_spend, outsee_guard, outsee_idempotency, "
            "spend_guard (kill switch, disk guard, caps): the production code that decides whether the restart pays again",
            f"{paid} real Outsee submit(s)" if (live and paid) else None) if x],
        "not_real": [x for x in (
            "seeded project: synthetic, built by this script (one frame, one images node)",
            None if live else "the provider: a fake Outsee API on 127.0.0.1 (nothing left the machine, nothing was paid)",
            "the kill point: the process dies when the submit RESPONSE is in memory (after the paid POST, before poll, "
            "download, file, result row); a kill between the request bytes and the response is not separately exercised "
            "(the intent row is already on disk in both cases, so the restart sees the same state)",
            "the worker, observation and Decision layers are not started: generate_images.run is called directly; the "
            "HITL card and Telegram are stubs (no message is sent)",
            "Outsee has no task id on the client side: the stand cannot poll the killed task, it can only show that "
            "the restart did not create another one",
            "picture quality: not judged") if x],
    }


def reality_not_proven(mode: str) -> list[str]:
    out = [
        "whether the killed task finished and was billed on the provider side: the application has no task id to ask; "
        "check the Outsee history and balance by hand (one task expected)",
        "behaviour of Outsee on a timeout or a dropped connection (F05 asks for a record of it): not exercised here",
        "the price of a real Outsee image is not calibrated by the application (cost_unknown rows)",
    ]
    if mode != "live":
        out.insert(0, "anything about the real Outsee: this is a dry run on a fake provider, NOT a live proof")
    return out



def check(name: str, ok: bool, detail: Any = None, *, needs: str | None = None) -> dict:
    return prod.check(name, ok, detail, needs=needs)


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    return prod.verdict(checks)


def _events(rows: list[dict]) -> list[str]:
    return [r.get("event") for r in rows if r.get("key") == FRAME_KEY]


def _spend_total(rows: list[dict]) -> float:
    return round(sum(float(r.get("usd") or 0) for r in rows if r.get("provider") == "outsee"), 6)


def evaluate(snap: dict, *, mode: str, max_real_posts: int = DEFAULT_MAX_REAL_POSTS) -> list[dict]:
    """Design assertions over a collected run snapshot (dry and live share them)."""
    wire = snap.get("wire") or []
    p1, p2 = snap.get("phase1") or {}, snap.get("phase2") or {}
    rec1, rec2 = snap.get("records_after_phase1") or {}, snap.get("records_after_phase2") or {}
    ev1, ev2 = _events(rec1.get("idem_rows") or []), _events(rec2.get("idem_rows") or [])
    posts_total, posts_p1, posts_p2 = wire_posts(wire), wire_posts(wire, 1), wire_posts(wire, 2)
    responses_p1 = sum(1 for e in wire if e.get("event") == "response_received" and e.get("phase") == 1)
    kind = classify_phase2(p2)
    spend1, spend2 = rec1.get("spend_rows") or [], rec2.get("spend_rows") or []
    ledger1, ledger2 = rec1.get("ledger_rows") or [], rec2.get("ledger_rows") or []
    files1, files2 = snap.get("files_after_phase1") or [], snap.get("files_after_phase2") or []
    gate_refusals = int(p1.get("gate_refused_cap", 0)) + int(p2.get("gate_refused_cap", 0))
    out = [
        check("phase1_killed_by_a_hard_exit_after_the_paid_submit",
              snap.get("phase1_returncode") == KILL_EXIT_CODE and posts_p1 == 1 and responses_p1 == 1,
              {"returncode": snap.get("phase1_returncode"), "posts_phase1": posts_p1, "responses_phase1": responses_p1}),
        check("wire_counter_persisted_before_the_kill", posts_p1 == 1 and responses_p1 == 1 and
              [e.get("event") for e in wire if e.get("phase") == 1][:2] == ["post_sent", "response_received"],
              [(e.get("phase"), e.get("event")) for e in wire]),
        check("intent_on_disk_before_the_kill_and_no_verdict_after_it",
              ev1 == ["intent"] and any(r.get("api") == "outsee" and r.get("key") == FRAME_KEY and r.get("event") == "intent"
                                        for r in ledger1),
              {"idempotency_events": ev1, "ledger": [(r.get("event"), r.get("status")) for r in ledger1][:4]}),
        check("nothing_was_taken_into_the_project_by_the_killed_process", not files1,
              {"scene_files_after_phase1": files1}),
        check("exactly_one_paid_post_across_both_phases", posts_total == 1 and posts_total <= max_real_posts,
              {"posts_phase1": posts_p1, "posts_phase2": posts_p2, "max_real_posts": max_real_posts}),
        check("restart_sent_no_paid_request", posts_p2 == 0 and gate_refusals == 0 and not p2.get("new_post_attempted"),
              {"posts_phase2": posts_p2, "gate_refused_cap": gate_refusals, "attempted": p2.get("new_post_attempted")}),
        check("restart_outcome_is_a_safe_one_and_is_recorded",
              kind in ("refused_unknown_outcome", "refused_resume_only", "reused_recorded_result"),
              {"production_did": kind, "error_type": p2.get("error_type"), "error_code": p2.get("error_code"),
               "step_outcome": p2.get("step_outcome")}),
        check("restart_wrote_no_second_intent_and_no_result",
              ev2 == ["intent"] and ev2.count("intent") == 1 and "result" not in ev2 and "consumed" not in ev2,
              {"idempotency_events_after_restart": ev2}),
        check("provider_ledger_has_one_open_intent_for_a_human",
              sum(1 for r in ledger2 if r.get("api") == "outsee" and r.get("key") == FRAME_KEY and r.get("event") == "intent") == 1
              and not any(r.get("event") == "intent_done" and r.get("key") == FRAME_KEY for r in ledger2),
              [(r.get("event"), r.get("status")) for r in ledger2][:6]),
        check("restart_added_no_charge_and_no_spend_row",
              _spend_total(spend2) == _spend_total(spend1) and len(spend2) == len(spend1),
              {"rows_after_phase1": len(spend1), "rows_after_phase2": len(spend2),
               "usd_after_phase1": _spend_total(spend1), "usd_after_phase2": _spend_total(spend2)}),
        check("project_does_not_report_success_without_a_file",
              not p2.get("project_images_ready") and p2.get("frame_status") not in ("image_generated", "image_approved")
              and not files2 and not p2.get("valid_image_on_disk"),
              {"project_status": p2.get("project_status"), "frame_status": p2.get("frame_status"),
               "files_after_restart": files2, "step_outcome": p2.get("step_outcome"), "error_type": p2.get("error_type")}),
        check("no_foreign_request_in_either_phase", not (p1.get("refused_hosts") or p2.get("refused_hosts")),
              {"phase1": p1.get("refused_hosts"), "phase2": p2.get("refused_hosts")}),
        check("restart_process_exited_cleanly", snap.get("phase2_returncode") == 0 and bool(p2),
              {"returncode": snap.get("phase2_returncode"), "has_record": bool(p2)}),
    ]
    if mode == "live":
        out.append(check("caps_not_exceeded", posts_total <= max_real_posts, {"posts": posts_total, "max": max_real_posts}))
    else:
        out.append(check("provider_independently_saw_one_submit",
                         snap.get("fake_posts") == 1 and snap.get("fake_auth_failures") == 0,
                         {"fake_posts": snap.get("fake_posts"), "auth_failures": snap.get("fake_auth_failures")}))
    return out


def advisories(snap: dict) -> list[str]:
    """Findings that are not pass/fail checks but that the owner must read."""
    out = []
    wire = snap.get("wire") or []
    spend1 = (snap.get("records_after_phase1") or {}).get("spend_rows") or []
    if wire_posts(wire, 1) == 1 and not [r for r in spend1 if r.get("provider") == "outsee"]:
        out.append("GAP: the killed submit left NO spend_ledger row (the money is recorded after the call returns, the "
                   "in-flight estimate dies with the process): one paid task exists at the provider but is not on the "
                   "application's books; the project/daily cap sums count the open attempt "
                   "(spend_guard.conservative_sums) as one unknown-cost row until a human releases it, but the ledger "
                   "itself has no row: reconcile with the provider's balance")
    kind = classify_phase2(snap.get("phase2") or {})
    if kind == "refused_unknown_outcome":
        out.append("the restart stays blocked on this frame until a human releases it or names the found file "
                   "(outsee_idempotency.release / adopt_file); the step swallows the refusal into a failed frame and an "
                   "owner note, it does not raise")
    return out


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return "PASS (all checks, live: one real Outsee submit, hard kill, restart paid nothing)"
    return "DRY OK (fake provider: kill/restart machinery proven offline, NOT a live proof)"



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="FREE: fake Outsee on 127.0.0.1, nothing leaves the machine")
    p.add_argument("--live", action="store_true", help="PAID, capped: ONE real Outsee image submit (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(prod.OUTSEE_ENV_NAMES) + " are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, wire.jsonl, result.json (recreated)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS, help="hard cap of real paid Outsee submits (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help=f"authorised spend (hard ceiling ${HARD_CEILING_USD}); refused when max-real-posts x "
                        f"${prod.PRICE_HINT_USD} exceeds it")
    p.add_argument("--dry-scenario", choices=SCENARIOS, default="none",
                   help="dry only. second_submit: negative control, the stand's own production code sends a second POST")
    p.add_argument("--image-generator", choices=prod.IMAGE_GENERATORS, default="gpt_image_2_vip")
    p.add_argument("--fake-port", type=int, default=FAKE_PORT)
    p.add_argument("--port", type=int, default=prod.PORT, help="synthetic web port the app environment names (not opened)")
    p.add_argument("--timeout-s", type=int, default=240, help="per child process")
    p.add_argument("--child", type=int, choices=(1, 2), help=argparse.SUPPRESS)
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.live and args.dry_scenario != "none":
        p.error("--dry-scenario is a --dry option")
    problem = authorisation_problem(args.max_real_posts, args.max_usd)
    if problem:
        p.error(problem)
    if args.live and args.max_real_posts != 1:
        p.error("--live allows exactly one real paid submit (--max-real-posts 1)")
    return args


def posts_allowed(args: argparse.Namespace) -> int:
    """The Gate's cap across both phases: one, except in the negative control, which must be able to reach two."""
    return 2 if args.dry_scenario == "second_submit" else args.max_real_posts



def _rows(state_db: Path, sql: str) -> list[dict]:
    """Plain read connection: a database left by a killed process may need its WAL recovered, read-only mode cannot."""
    if not state_db.exists():
        return []
    try:
        db = sqlite3.connect(state_db)
        try:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql).fetchall()]
        finally:
            db.close()
    except sqlite3.Error:
        return []


def read_records(state_db: Path) -> dict:
    return {
        "idem_rows": _rows(state_db, "SELECT event,key,attempt_id,request_sha,model,file_path,file_sha256,raw_url,note "
                                     "FROM outsee_idempotency ORDER BY id"),
        "ledger_rows": _rows(state_db, "SELECT api,key,event,status,provider,note FROM provider_task_ledger ORDER BY id"),
        "spend_rows": _rows(state_db, "SELECT provider,kind,usd,source,note FROM spend_ledger ORDER BY id"),
    }


def scene_files(data: Path, slug: str) -> list[dict]:
    scenes = data / "videos" / slug / "scenes"
    if not scenes.is_dir():
        return []
    return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(scenes.glob("frame_*.png")) if p.is_file()]



async def child_main(args: argparse.Namespace) -> int:
    phase = int(args.child)
    mode = "live" if args.live else "dry"
    work = Path(args.work_dir).resolve()
    data = guard.assert_scratch_data_dir(work / "data")
    wire_path, seed_path = work / WIRE_FILE, work / SEED_FILE
    live_env: dict[str, str] = {}
    if mode == "live":
        live_env = prod.load_env_whitelist(args.env_file, prod.OUTSEE_ENV_NAMES)
    os.chdir(ROOT)

    os.environ.update(prod.env_overrides(data, args.port, fake_base=f"http://127.0.0.1:{args.fake_port}",
                                         gate_cap=0.45, dry=(mode == "dry")))
    from app.services import spend_guard
    estimate = float(spend_guard.MEDIA_SUBMIT_ESTIMATE_USD)
    cap = prod.gate_cap_usd(estimate)
    os.environ.update({"SPEND_DAILY_CAP_USD": str(cap), "SPEND_PROJECT_CAP_USD": str(cap), "SPEND_RUN_CAP_USD": str(cap)})
    real_base = f"http://127.0.0.1:{args.fake_port}"
    if mode == "live":
        os.environ["OUTSEE_API_KEY"] = live_env["OUTSEE_API_KEY"]
        os.environ["OUTSEE_API_BASE_URL"] = real_base = prod.outsee_base(live_env)
        if live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL"):
            os.environ["OUTSEE_DEFAULT_IMAGE_MODEL"] = live_env["OUTSEE_DEFAULT_IMAGE_MODEL"]

    import httpx
    from app.settings import settings
    settings.gpt_relay_token, settings.gpt_proxy_url = "", None
    settings.vibecode_api_key = qc.SYNTHETIC_KEY
    settings.vibecode_base_url = f"http://127.0.0.1:{args.port}/v1"
    settings.gpt_base_url = f"http://127.0.0.1:{args.port}"
    settings.kie_api_key = settings.elevenlabs_api_key = ""
    settings.outsee_api_key = live_env["OUTSEE_API_KEY"] if mode == "live" else prod.FAKE_KEY
    settings.outsee_api_base_url = real_base
    settings.image_provider = "outsee"
    settings.outsee_default_image_model = live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL") or "gpt-image-2"
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap

    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    from app import main as app_main
    await app_main._init_db()

    gate = prod.Gate(mode=mode, max_paid_posts=posts_allowed(args), outsee_host=prod.outsee_host(live_env) if mode == "live" else "",
                     fake_port=args.fake_port, phase="main")
    gate.paid_posts = wire_posts(read_wire(wire_path))
    state: dict[str, Any] = {"new_post_attempted": False, "post_attempts_this_phase": 0}
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        judged = gate.judge(request.method, request.url.host, request.url.port, request.url.path, is_decision=False)
        if judged.action == "refuse":
            if judged.kind == "paid_cap":
                state["new_post_attempted"] = True
            raise RuntimeError(judged.reason)
        paid = judged.kind in ("paid_post_fake", "paid_post_real")
        if paid:
            state["post_attempts_this_phase"] += 1
            state["new_post_attempted"] = phase == 2
            wire_append(wire_path, {"event": "post_sent", "phase": phase, "n": gate.paid_posts, "t": time.time()})
        response = await original_send(client, request, **kwargs)
        if paid:
            wire_append(wire_path, {"event": "response_received", "phase": phase, "status": response.status_code, "t": time.time()})
            if phase == 1:
                os._exit(KILL_EXIT_CODE)
        if judged.kind in ("outsee_get", "paid_post_real"):
            try:
                gate.learn(response.text)
            except Exception:
                pass
        return response

    from app.bots import outsee_http
    from app.orchestrator.steps import generate_images

    async def no_curl_post(*_a, **_k):
        return None

    original_download_curl = outsee_http._download_via_curl

    async def guarded_download_curl(url, out_path):
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
        if qc.is_loopback(host) or (mode == "live" and (host == gate.outsee_host or host in gate.learned_hosts)):
            return await original_download_curl(url, out_path)
        return False

    stubs = {"hitl_cards": 0, "telegram_calls": 0}

    async def stub_hitl(*_a, **_k):
        stubs["hitl_cards"] += 1

    class NoBot:
        def __getattr__(self, _name):
            async def call(*_a, **_k):
                stubs["telegram_calls"] += 1
            return call

    outsee_http._post_generate_via_curl = no_curl_post
    outsee_http._download_via_curl = guarded_download_curl
    generate_images.send_hitl_photo = stub_hitl
    httpx.AsyncClient.send = send

    from app.models import Frame, Project, ProjectStatus
    from app.project_db import project_db_session_scope
    from app.services import outsee_idempotency
    from app.services.scan_frames import disk_has_valid_frame_image

    record: dict[str, Any] = {"phase": phase, "mode": mode, "scenario": args.dry_scenario}
    seen_refusals: list[dict] = []
    original_check = outsee_idempotency.check

    async def spy_check(ctx):
        try:
            verdict_ = await original_check(ctx)
        except Exception as exc:
            context = getattr(exc, "context", None)
            seen_refusals.append({"type": type(exc).__name__, "code": (context or {}).get("code") if isinstance(context, dict) else None,
                                  "request_sent": (context or {}).get("request_sent") if isinstance(context, dict) else None})
            raise
        if verdict_ is not None:
            seen_refusals.append({"type": "adopt", "code": verdict_.get("code"), "attempt_id": verdict_.get("attempt_id")})
        return verdict_

    outsee_idempotency.check = spy_check
    if phase == 1:
        pid, slug = await prod._seed(args)
        seed_path.write_text(json.dumps({"pid": pid, "slug": slug}), encoding="utf-8")
    else:
        seeded = json.loads(seed_path.read_text(encoding="utf-8"))
        pid, slug = int(seeded["pid"]), str(seeded["slug"])
        try:
            from app.services import media_resume
            await media_resume.reconcile_on_startup()
            record["reconcile_on_startup"] = "ok"
        except Exception as exc:
            record["reconcile_on_startup"] = f"{type(exc).__name__}: {str(exc)[:100]}"
        if args.dry_scenario == "second_submit":
            record["negative_control_released"] = await outsee_idempotency.release(pid, FRAME_KEY, note="negative control")

    scenes = data / "videos" / slug / "scenes"
    try:
        async with project_db_session_scope(pid) as session:
            project = await session.get(Project, pid)
            project.status = ProjectStatus.generating_images
            await session.commit()
            try:
                await generate_images.run(session, project, NoBot())
                record["step_outcome"] = "returned"
                await session.commit()
            except BaseException as exc:
                record["step_outcome"] = "raised"
                record["error_type"] = type(exc).__name__
                ctx = getattr(exc, "context", None)
                record["error_code"] = (ctx or {}).get("code") if isinstance(ctx, dict) else None
                record["error_text"] = str(exc)[:300]
                try:
                    await session.rollback()
                except Exception:
                    pass
            from sqlalchemy import select
            await session.refresh(project)
            frame = (await session.execute(select(Frame).where(Frame.project_id == pid))).scalars().first()
            record["project_status"] = project.status.value
            record["project_images_ready"] = project.status is ProjectStatus.images_ready
            record["frame_status"] = frame.status.value if frame is not None else None
            record["frame_fail_reason"] = (frame.attrs or {}).get("fail_reason") if frame is not None else None
            record["valid_image_on_disk"] = bool(frame is not None and disk_has_valid_frame_image(scenes, frame.number))
    finally:
        httpx.AsyncClient.send = original_send
        outsee_idempotency.check = original_check
    record["idempotency_checks"] = seen_refusals
    if not record.get("error_code") and seen_refusals:
        record["error_type"] = record.get("error_type") or seen_refusals[-1]["type"]
        record["error_code"] = seen_refusals[-1]["code"]
        record["error_swallowed_by_step"] = True

    record["adopted_recorded_result"] = (bool(record.get("valid_image_on_disk")) and not state["new_post_attempted"]
                                         and any(r.get("type") == "adopt" for r in seen_refusals))
    record.update(new_post_attempted=state["new_post_attempted"], post_attempts_this_phase=state["post_attempts_this_phase"],
                  gate_paid_posts=gate.paid_posts, gate_refused_cap=gate.refused_cap, refused_hosts=sorted(
                      {r["host"] for r in gate.refused if r["kind"] in ("dry_foreign", "foreign", "outsee_other")}),
                  stubs=stubs)
    (work / f"phase{phase}.json").write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0



def _child_env(args: argparse.Namespace) -> dict[str, str]:
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    for name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "VIBECODE_API_KEY", "GPT_API_KEY", "OUTSEE_API_KEY", "KIE_API_KEY",
                 "ELEVENLABS_API_KEY"):
        env.pop(name, None)
    return env


def _run_child(args: argparse.Namespace, phase: int, work: Path, scrub) -> tuple[int | None, str]:
    cmd = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--child", str(phase), "--work-dir", str(work),
           "--live" if args.live else "--dry", "--fake-port", str(args.fake_port), "--port", str(args.port),
           "--dry-scenario", args.dry_scenario, "--image-generator", args.image_generator,
           "--max-real-posts", str(args.max_real_posts), "--max-usd", str(args.max_usd)]
    if args.live:
        cmd += ["--env-file", args.env_file]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, env=_child_env(args), capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=args.timeout_s)
        code, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired as exc:
        code, out = None, f"timeout after {args.timeout_s}s\n" + str(exc.stdout or "")[-800:]
    out = scrub(out)
    (work / f"child{phase}.log").write_text(out, encoding="utf-8")
    return code, out[-1200:]


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    if args.child:
        return asyncio.run(child_main(args))
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    if mode == "live":
        if not Path(args.env_file).is_file():
            print("env file not found; nothing sent")
            return EXIT_REFUSED
        live_env = prod.load_env_whitelist(args.env_file, prod.OUTSEE_ENV_NAMES)
        if not live_env.get("OUTSEE_API_KEY"):
            print("env file has no OUTSEE_API_KEY; nothing sent")
            return EXIT_REFUSED
        problem = prod.base_problem(live_env)
        if problem:
            print("refused: " + problem + "; nothing sent")
            return EXIT_REFUSED
    secrets = sorted((v for k, v in live_env.items() if k in prod.SECRET_NAMES and len(v) >= 6), key=len, reverse=True)

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    for stale in (WIRE_FILE, SEED_FILE, "phase1.json", "phase2.json", "result.json"):
        (work / stale).unlink(missing_ok=True)
    state_db = data / "state.db"
    print(f"preflight: mode={mode} scenario={args.dry_scenario} posts_allowed={posts_allowed(args)} "
          f"worst_case=${prod.worst_case_usd(args.max_real_posts)} max_usd={args.max_usd}", flush=True)

    fake = prod.FakeOutsee(args.fake_port).start() if mode == "dry" else None
    result: dict[str, Any] = {"mode": mode, "scenario": args.dry_scenario, "node": NODE,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"}}
    try:
        print("phase 1: run the step, hard-kill right after the paid submit ...", flush=True)
        code1, tail1 = _run_child(args, 1, work, scrub)
        result["phase1_returncode"] = code1
        seed = json.loads((work / SEED_FILE).read_text(encoding="utf-8")) if (work / SEED_FILE).exists() else {}
        slug = seed.get("slug", "")
        result["records_after_phase1"] = read_records(state_db)
        result["files_after_phase1"] = scene_files(data, slug)
        print(f"phase 1 exit code: {code1} (expected {KILL_EXIT_CODE}); wire posts: {wire_posts(read_wire(work / WIRE_FILE))}", flush=True)
        if code1 != KILL_EXIT_CODE:
            print("phase 1 tail:", tail1[-600:])
        print("phase 2: a NEW process on the same state, the step is started again ...", flush=True)
        code2, tail2 = _run_child(args, 2, work, scrub)
        result["phase2_returncode"] = code2
        if code2 != 0:
            print("phase 2 tail:", tail2[-600:])
        result["records_after_phase2"] = read_records(state_db)
        result["files_after_phase2"] = scene_files(data, slug)
    finally:
        if fake is not None:
            result["fake_posts"], result["fake_auth_failures"] = fake.posts, fake.auth_failures
            result["fake_counts"] = fake.counts()
            fake.stop()
    for n in (1, 2):
        p = work / f"phase{n}.json"
        result[f"phase{n}"] = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    result["wire"] = read_wire(work / WIRE_FILE)
    result["wire_posts"] = wire_posts(result["wire"])
    return _finish(args, mode, result, scrub, work)


def _finish(args, mode, snap, scrub, work) -> int:
    checks = evaluate(snap, mode=mode, max_real_posts=args.max_real_posts)
    ok, failed = verdict(checks)
    reality = reality_report(mode, snap)
    p2_kind = classify_phase2(snap.get("phase2") or {})
    notes = advisories(snap)
    summary = {"mode": mode, "scenario": args.dry_scenario, "advisories": notes, "verdict": final_verdict(mode, ok), "failed_checks": failed,
               "paid_posts": snap.get("wire_posts"), "production_did_on_restart": p2_kind,
               "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd, "worst_case_usd": prod.worst_case_usd(args.max_real_posts)},
               "not_proven": reality_not_proven(mode)}
    out = {**snap, "checks": checks, "ok": ok, "failed": failed, "reality": reality, "summary": summary}
    (work / "result.json").write_text(scrub(json.dumps(out, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {NODE} (Outsee) | mode: {mode} | scenario: {args.dry_scenario} | paid posts on the wire: {snap.get('wire_posts')}")
    print("production code on the restart:", p2_kind, "|", scrub(json.dumps(
        {k: (snap.get("phase2") or {}).get(k) for k in ("step_outcome", "error_type", "error_code", "project_status", "frame_status")},
        ensure_ascii=False, default=str)))
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"])
        if not c["ok"]:
            print("     detail:", scrub(json.dumps(c["detail"], ensure_ascii=False, default=str))[:400])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    for note in notes:
        print("advisory:", scrub(note))
    print("verdict:", summary["verdict"])
    return EXIT_PASS if ok else EXIT_FAIL


if __name__ == "__main__":
    raise SystemExit(main())
