"""Local node `assemble` through the PRODUCTION route: worker -> observation -> diagnosis -> Decision -> ONE supervised
re-run under a ticket -> independent verification. Everything is local and FREE: no provider, no key, no network.

Sibling of scripts/e2e_media_production_route.py (paid Outsee nodes; imported, not edited: its request Gate, flow
helpers and report style are reused). `assemble` has no provider job and no paid request: its supervised recovery is
the only one that RE-RUNS the node (through the original worker, once, inside media_spend.resume_only) and verifies the
ticket from independent facts (app/services/local_node_recovery.py).

The flow:
  1. seed a small synthetic project: 3 frames, 3 tiny local clips (ffmpeg lavfi `color`, 160x284, 2 s each) as
     scene_video rows, 3 per-frame voice files and the full voice (ffmpeg lavfi `sine`, 1.5 s per frame, mp3) as an
     `audio` artifact in per_frame mode;
  2. observe-only scope, UI start of the node (POST /steps/assemble/run): the REAL step assemble.run runs the REAL
     ffmpeg assembly (app.services.assembly.assemble) and the REAL verify_artifact + track check. The forced failure:
     the assembly call is made to fail like ffmpeg ("ffmpeg exited with 1") on every call, so the in-node retry
     (exactly one) and the native retries (3 cycles of 3) run out and the project pauses;
  3. the failure is switched OFF (the "cause is gone"), diagnosis types a media incident whose failed attempt proved
     its OWN repeatable failure, actions are enabled, the kill switch is probed against the recovery, the Decision loop
     is enabled: Decision (local stub) -> retry_same -> ONE ticket -> the original worker re-runs assemble ONCE inside
     resume-only -> a real final.mp4 -> reconcile verifies from independent facts (file newer than the claim,
     verify_artifact again, digest equals the artifact row, approve_final of that file still PENDING, provider ledger
     unchanged, zero refused paid seams, no model request by the attempt, no part file);
  4. afterwards the stand re-reads the final with the application's own verifier (ffprobe: video + audio, duration) and
     checks that the approval is still with the human (pending, never approved by the supervisor).

ASR / subtitles. The per-frame voice route needs no ASR and subtitles are OFF (the default). Whether a Whisper is
available locally is probed WITHOUT network and reported: `faster_whisper` import + the names of the cached models in
the Hugging Face cache directory (nothing is loaded or downloaded). A lavfi sine tone has no words, so a real ASR could
not be exercised on it anyway: ASR and burned subtitles are listed as NOT proven.

Usage (repository root, project venv; --work-dir must be an empty dir OUTSIDE the repository):
    python scripts/e2e_assemble_local.py --work-dir <dir> --dry
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard  # noqa: E402  (A19: refuses to touch live data)


def _load_script(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


base = _load_script("e2e_media_production_route", "e2e_media_production_route_for_assemble")  # imported, not edited
qc = base.qc

PORT = 18808
NODE = "assemble"
STEP_CODE = "assemble"
TARGET = "media-assemble-A"
GROUP_ID = "media-g1"
MEDIA_PROVIDERS = ("outsee", "kie", "elevenlabs", "suno", "grsai")
FRAME_SECONDS = 1.5
CLIP_SECONDS = 2
COLORS = ("red", "green", "blue")
VOICEOVERS = ("Первый кадр: папка лежит на столе.", "Второй кадр: рядом стоит чашка.", "Третий кадр: раскрыт блокнот.")
FAULT = "ffmpeg exited with 1"      # what a failed ffmpeg run looks like to the node (retryable by the node itself)
REQUIRED_TICKET_CHECKS = ("artifact_row", "file_inside_project", "claimed_at_known", "produced_after_claim",
                          "verify_artifact_passed", "digest_matches_artifact", "approve_final_pending",
                          "journal_proof_matches", "ledger_unchanged", "no_paid_seam_hit", "no_model_requests",
                          "no_part_files")


# ───────────────────────────── local media made by ffmpeg (lavfi), free ─────────────────────────────

def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is required for the assemble stand (not on PATH)")
    return exe


def _run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True, timeout=120, capture_output=True)


def make_clip(path: Path, color: str, seconds: int = CLIP_SECONDS, size: str = "160x284") -> None:
    _run([_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"color=c={color}:s={size}:r=24:d={seconds}",
          "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", str(path)])


def make_voice(path: Path, frequency: int, seconds: float = FRAME_SECONDS) -> None:
    _run([_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration={seconds}:sample_rate=44100",
          "-ac", "1", "-c:a", "libmp3lame", "-b:a", "64k", str(path)])


def concat_voice(parts: list[Path], out: Path) -> None:
    listing = out.parent / "voice_parts.txt"
    listing.write_text("\n".join(f"file '{p.as_posix()}'" for p in parts), encoding="utf-8")
    try:
        _run([_ffmpeg(), "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)])
    finally:
        listing.unlink(missing_ok=True)


def asr_probe() -> dict:
    """Is a local Whisper available? Imports/lists only: nothing is loaded, nothing is downloaded, no network."""
    import importlib.util as iu
    hub = Path(os.environ.get("HF_HOME") or (Path.home() / ".cache" / "huggingface")) / "hub"
    try:
        cached = sorted(p.name for p in hub.glob("models--*") if "whisper" in p.name.lower())
    except OSError:
        cached = []
    return {"faster_whisper_installed": iu.find_spec("faster_whisper") is not None, "cached_whisper_models": cached,
            "used_by_this_stand": False,
            "why": "per-frame voice route needs no ASR; subtitles are off; a sine tone has no words"}


# ───────────────────────────── checks (pure, unit-tested) ─────────────────────────────

def check(name: str, ok: bool, detail: Any = None, *, needs: str | None = None) -> dict:
    return qc.check(name, ok, detail, needs=needs)


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    return qc.verdict(checks)


def _blocked(probes: dict, name: str, code: str) -> bool:
    return base._blocked(probes, name, code)


def reality_report() -> dict:
    return {
        "real": [
            "application, worker, observer, diagnosis, Decision supervisor, recovery ticket and verification in-process",
            "native step assemble.run: recover_before_assemble, the per-frame timeline, the REAL ffmpeg assembly "
            "(app.services.assembly.assemble: cut, concat, mux), verify_artifact (ffprobe) and the track check, the "
            "in-node retry, the atomic write, approve_final creation",
            "local_node_recovery (admission, resume-only scope, reconcile verification), media_spend.resume_only, spend_guard, kill switch",
            "ffprobe check of the final by artifact_verifier.verify_artifact after the run"],
        "not_real": [
            "seeded project: synthetic, built by this script (3 frames, 3 lavfi clips, a lavfi sine voice)",
            "the forced failure: injected locally (the assembly call raises 'ffmpeg exited with 1' until the stand switches it off)",
            "Decision model: local stub (nothing was sent to a provider)",
            "Telegram: off (the approve_final request is created in the database, no message was sent)",
            "ASR / Whisper and burned subtitles: not used (per-frame voice route, subtitles off)",
            "picture, voice and montage quality: not judged"]}


def reality_not_proven() -> list[str]:
    return ["ASR / Whisper timing and burned subtitles on the assembly path (no local faster_whisper in the venv unless "
            "asr_probe says otherwise; a sine tone has no words)",
            "assembly of real Outsee clips (a different size / codec / audio track than tiny lavfi clips)",
            "the montage-v3 (excel-r15) path: this stand uses the per-frame voice path",
            "approval: stays with the human by design; this stand never approves"]


def evaluate(snap: dict) -> list[dict]:
    counters = snap.get("counters", {})
    probes = snap.get("probes", {})
    incidents = [i for i in (snap.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == TARGET]
    media_inc = [i for i in incidents if i.get("type") == "media_failure"]
    records = (snap.get("decisions") or {}).get("records") or []
    recoveries = (snap.get("actions") or {}).get("recoveries") or []
    ticket = recoveries[0] if recoveries else {}
    precond = ticket.get("preconditions") or {}
    ver = ticket.get("verification_result") or {}
    ctxs = snap.get("decision_contexts") or []
    media_ctx = next((c.get("media_recovery") for c in ctxs if c.get("media_recovery")), None) or {}
    node_status = (snap.get("node_status") or {}).get(TARGET)
    attempts = int(snap.get("node_attempts") or 0)
    stepf = snap.get("step_failure") or {}
    before = snap.get("state_at_exhaustion") or {}
    final = snap.get("final_check") or {}
    approvals = snap.get("approvals") or []
    mine = [a for a in approvals if final.get("sha256") and a.get("sha256") == final.get("sha256")]
    out = [
        check("failure_injected_and_native_retries_ran", counters.get("injected_failures", 0) >= 4 and attempts >= 3,
              {"injected": counters.get("injected_failures"), "node_attempts": attempts}),
        check("in_node_retry_is_exactly_one", counters.get("injected_failures", 0) >= 2
              and counters.get("injected_failures", 0) % 2 == 0, {"injected": counters.get("injected_failures")}),
        check("native_retries_exhausted_and_project_paused", bool(stepf.get("abandoned_at")) and before.get("node") == "failed",
              {"step_failure": {k: stepf.get(k) for k in ("total_fails", "abandoned_at", "abandoned_threshold")},
               "node_at_exhaustion": before.get("node")}),
        check("nothing_was_published_by_the_failed_phase", before.get("final_files") == [] and before.get("approvals") == 0
              and before.get("part_files") == [], before),
        check("media_incident_typed_by_diagnosis", bool(media_inc) and any(i.get("media") is True for i in media_inc),
              [(i.get("type"), i.get("status")) for i in incidents][-6:]),
        check("decision_sees_the_local_worker_rerun_as_the_only_offer",
              media_ctx.get("operation") == "retry_local_worker" and media_ctx.get("submits_new_paid_request") is False,
              media_ctx, needs="decision_v1._media_info and local_node_recovery.admit"),
        check("decision_done_with_retry_same", bool(records) and records[0].get("status") == "done"
              and precond.get("action_type") == "retry_same",
              {"decision": [(r.get("status"), r.get("model_steps")) for r in records], "action": precond.get("action_type")}),
        check("exactly_one_ticket_and_it_is_verified", len(recoveries) == 1 and ticket.get("status") == "verified"
              and ver.get("verified") is True, {"tickets": [(t.get("status")) for t in recoveries],
                                                "problems": ver.get("problems")}),
        check("ticket_checks_all_true_from_independent_facts",
              bool(ver) and ver.get("result_confirmed") is True
              and all(ver.get(k) is True for k in REQUIRED_TICKET_CHECKS),
              {k: ver.get(k) for k in REQUIRED_TICKET_CHECKS}, needs="local_node_recovery._verify_assemble"),
        check("recovery_attempt_ran_with_zero_paid_seam_hits",
              ver.get("no_paid_seam_hit") is True and (ver.get("local_details") or {}).get("paid_seam_hits") == 0,
              (ver.get("local_details") or {}).get("paid_seam_hits")),
        check("incident_closed_by_the_verified_recovery", any(
            i.get("incident_id") == ticket.get("incident_id") and i.get("status") == "resolved_confirmed"
            for i in incidents), [(i.get("type"), i.get("status")) for i in incidents if i.get("incident_id") == ticket.get("incident_id")]),
        check("final_file_passes_the_real_verifier_after_the_run",
              final.get("status") == "passed" and {"video", "audio"} <= set(final.get("streams") or [])
              and 3.5 <= (final.get("duration") or 0) <= 6.0,
              {k: final.get(k) for k in ("status", "reason", "streams", "duration", "width", "height", "bytes")},
              needs="artifact_verifier.verify_artifact (ffprobe)"),
        check("approve_final_for_this_file_is_pending_and_unapproved", len(mine) == 1 and mine[0].get("decision") == "pending"
              and not any(a.get("decision") in ("approved", "approve") for a in approvals), approvals),
        check("no_part_file_is_left", not snap.get("part_files"), snap.get("part_files")),
        check("kill_switch_stops_the_recovery", (probes.get("kill_switch_recovery") or {}).get("refused") is True
              and (probes.get("kill_switch_recovery") or {}).get("tickets_after") == 0, probes.get("kill_switch_recovery")),
        check("no_provider_ledger_and_no_spend", not snap.get("ledger_rows") and not snap.get("spend_rows_paid"),
              {"ledger": snap.get("ledger_rows"), "spend": snap.get("spend_rows_paid")}),
        check("nothing_left_the_machine", counters.get("refused_foreign", 0) == 0 and not snap.get("refused_hosts")
              and counters.get("unexpected_model", 0) == 0,
              {"refused": snap.get("refused_hosts"), "unexpected_model": counters.get("unexpected_model")}),
        check("asr_probe_is_reported", isinstance((snap.get("asr_probe") or {}).get("faster_whisper_installed"), bool),
              snap.get("asr_probe")),
    ]
    return out


def final_verdict(ok: bool) -> str:
    return ("DRY OK (local assemble: real ffmpeg + real verification + one supervised re-run; no provider involved; "
            "ASR/subtitles NOT exercised)" if ok else "NOT PASSED")


# ───────────────────────────── runtime ─────────────────────────────

def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", required=True,
                   help="the only mode: FREE and local (no provider, no key, no network)")
    p.add_argument("--work-dir", help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--request-limit", type=int, default=60)
    p.add_argument("--timeout-s", type=int, default=420)
    p.add_argument("--settle-s", type=float, default=4.0)
    p.add_argument("--sleep-minutes", type=float, default=0.01, help="native step-failure sleep (patched for speed)")
    args = p.parse_args(argv)
    if not args.work_dir:
        p.error("--work-dir is required")
    return args


async def _seed(args: argparse.Namespace) -> tuple[int, str, Path]:
    from app.db import session_scope
    from app.models import (Artifact, ArtifactKind, Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow,
                            WorkflowRun, WorkflowRunStatus)

    node = {"id": TARGET, "type": NODE, "position": {"x": 100.0, "y": 100.0},
            "data": {"label": "Native assemble", "groupId": GROUP_ID}}
    nodes, edges = [node], []
    slug = f"media-assemble-{_uuid.uuid4().hex[:6]}"
    text = " ".join(VOICEOVERS)
    async with session_scope() as session:
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, "img_streams": 1}
        project = Project(slug=slug, topic="media assemble production route e2e", status=ProjectStatus.audio_ready,
                          auto_mode=False, general_plan=text, script_text=text, meta=meta)
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=TARGET, node_type=NODE, status=NodeRunStatus.pending))
        frames = []
        for i, voice in enumerate(VOICEOVERS, start=1):
            fr = Frame(project_id=project.id, number=i, uuid=(f"{i:02d}" * 12), sort_key=float(i), voiceover_text=voice,
                       image_prompt="local frame", animation_prompt="local clip", duration_seconds=FRAME_SECONDS, attrs={})
            session.add(fr)
            frames.append(fr)
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
        videos, audio_dir = data_dir / "videos", data_dir / "audio"
        videos.mkdir(parents=True, exist_ok=True)
        audio_dir.mkdir(parents=True, exist_ok=True)
        parts = []
        for i, (fr, color) in enumerate(zip(frames, COLORS), start=1):
            clip = videos / f"clip_{i:03d}_{_uuid.uuid4().hex[:8]}.mp4"
            make_clip(clip, color)
            session.add(Artifact(project_id=project.id, frame_id=fr.id, kind=ArtifactKind.scene_video,
                                 uuid=_uuid.uuid4().hex, path=str(clip), meta={"shot": 1}))
            part = audio_dir / f"frame_{i:03d}.mp3"
            make_voice(part, 330 + 110 * i)
            parts.append(part)
        voice_full = audio_dir / "voice_full.mp3"
        concat_voice(parts, voice_full)
        session.add(Artifact(project_id=project.id, kind=ArtifactKind.audio, uuid=_uuid.uuid4().hex,
                             path=str(voice_full), meta={"mode": "per_frame"}))
        await session.flush()
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(text, encoding="utf-8")
    return pid, slug, data_dir


def _approvals(project_db: Path) -> list[dict]:
    rows = base._rows(project_db, "SELECT kind, decision, payload FROM hitl_requests ORDER BY id")
    out = []
    for r in rows:
        try:
            payload = json.loads(r.get("payload") or "{}")
        except ValueError:
            payload = {}
        if str(r.get("kind")).endswith("approve_final"):
            out.append({"kind": str(r.get("kind")), "decision": str(r.get("decision")).split(".")[-1].lower(),
                        "sha256": payload.get("sha256")})
    return out


def _final_state(project_root: Path, project_db: Path) -> dict:
    final_dir = project_root / "final"
    return {"final_files": sorted(p.name for p in final_dir.glob("*.mp4") if not p.name.startswith(".")) if final_dir.is_dir() else [],
            "part_files": sorted(p.name for p in final_dir.glob(".*.part.mp4")) if final_dir.is_dir() else [],
            "approvals": len(_approvals(project_db)), "node": base._node_status(project_db).get(TARGET)}


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    if "app.settings" in sys.modules:
        print("refused: the application settings were loaded before this stand set its scratch environment "
              "(they would point at the live data folder); run the stand in its own process; nothing was started")
        return 3
    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)  # A19: refuses live data
    os.chdir(ROOT)
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(base._runtime_config(args.port, args.request_limit, None), indent=2), encoding="utf-8")
    os.environ.update(base.env_overrides(data, args.port, fake_base="http://127.0.0.1:9", gate_cap=0.45, dry=True))
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(runtime_path)

    import httpx
    from app.settings import settings
    if Path(str(settings.data_dir)).resolve() != Path(data).resolve():
        print(f"refused: the application settings point at {settings.data_dir}, not at the scratch data {data}; "
              "nothing was started or written", flush=True)
        return 3
    settings.gpt_relay_token = ""
    settings.gpt_proxy_url = None
    settings.vibecode_api_key = qc.SYNTHETIC_KEY
    settings.vibecode_base_url = f"http://127.0.0.1:{args.port}/v1"
    settings.gpt_base_url = f"http://127.0.0.1:{args.port}"
    settings.kie_api_key = ""
    settings.elevenlabs_api_key = ""
    settings.outsee_api_key = ""
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if tracker._TRACKER_DB.exists():
        tracker._TRACKER_DB.unlink()
    from app.services import step_failure_policy
    from app.services.decision_transport import current as decision_hook
    step_failure_policy.SLEEP_MINUTES = args.sleep_minutes
    step_failure_policy.XLSX_SHEET_FORMAT_SLEEP_MINUTES = args.sleep_minutes
    from app import main as app_main
    await app_main._init_db()
    pid, slug, project_root = await _seed(args)
    project_db = data / "videos" / slug / "project.db"
    state_db = data / "state.db"
    api = f"http://127.0.0.1:{args.port}/api/projects/{pid}"

    base.NODE, base.STEP_CODE, base.TARGET, base.GROUP_ID = NODE, STEP_CODE, TARGET, GROUP_ID
    gate = base.Gate(mode="dry", max_paid_posts=1, fake_port=0)
    gate.phase = "main"
    counters: dict[str, Any] = {"injected_failures": 0, "injected_hitl": 0, "injected_tail": 0, "decision_stub": 0}
    decision_contexts: list[dict] = []
    inject = {"on": True}
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        host, port, path = request.url.host, request.url.port, request.url.path
        judged = gate.judge(request.method, host, port, path, is_decision=decision_hook.get() is not None)
        if judged.action == "refuse":
            raise RuntimeError(judged.reason)
        if judged.action == "stub_decision":
            body = json.loads(request.content.decode("utf-8") or "{}")
            ctx = qc.decision_context(qc.request_text(body))
            if ctx is None:
                raise RuntimeError("decision stub: no context in the request")
            counters["decision_stub"] += 1
            decision_contexts.append({"turn": counters["decision_stub"], "incident_type": ctx.get("incident_type"),
                                      "allowed_actions": ctx.get("allowed_actions"),
                                      "media_recovery": ctx.get("media_recovery")})
            answer = json.dumps(qc.decision_proposal(counters["decision_stub"], ctx, "retry_same"), ensure_ascii=False)
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=qc.chat_json_body(answer, f"chatcmpl-{_uuid.uuid4().hex[:12]}"), request=request)
        return await original_send(client, request, **kwargs)

    from app.orchestrator.steps import assemble as asm
    real_assemble = asm.assemble

    async def forced_failure(*a, **k):
        if inject["on"]:
            counters["injected_failures"] += 1
            raise RuntimeError(FAULT)
        return await real_assemble(*a, **k)

    asm.assemble = forced_failure
    httpx.AsyncClient.send = send

    result: dict[str, Any] = {"mode": "dry", "node": NODE, "target": TARGET, "project_id": pid, "slug": slug,
                              "args": {k: v for k, v in vars(args).items()}, "asr_probe": asr_probe()}
    probes: dict[str, Any] = {}
    task = asyncio.create_task(app_main.main())
    execution = None
    try:
        await qc._wait_api(api)
        from app.services import access_guard
        operator = {"x-vp-operator": access_guard.get_operator_token()}
        print(f"preflight: node={NODE} mode=dry local-only asr={result['asr_probe']}", flush=True)
        async with httpx.AsyncClient(timeout=30, trust_env=False, headers=operator,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            prefix = api + "/observations"
            r = await client.post(prefix, json={"node_keys": [TARGET]})
            print("observation:", r.status_code, flush=True)
            r.raise_for_status()
            execution = r.json()["execution_id"]
            result["execution_id"] = execution
            r = await client.post(api + f"/steps/{STEP_CODE}/run", params={"node_key": TARGET})
            print("start assemble:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
            r.raise_for_status()
            await base._wait_exhausted(args, project_db, counters, gate, None)
            result["state_at_exhaustion"] = _final_state(project_root, project_db)
            inject["on"] = False            # the cause is gone: the supervised re-run may now succeed
            incident = await base._wait_incident(client, prefix + f"/{execution}", args.timeout_s)
            result["incident_id"] = incident["incident_id"] if incident else None
            if not incident:
                raise RuntimeError("no current media incident of the assemble node after the native retries were exhausted")
            r = await client.post(prefix + f"/{execution}/actions/enable", json={
                "group_id": GROUP_ID, "node_keys": [TARGET], "rationale": "assemble production route e2e: supervised re-run",
                "request_limit": min(args.request_limit, 60)})
            print("actions/enable:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
            r.raise_for_status()
            await base._probe_kill_switch_recovery(client, api, prefix + f"/{execution}", incident["incident_id"], probes)
            r = await client.post(prefix + f"/{execution}/decisions/control", json={
                "enabled": True, "rationale": "dry decision loop for the local assemble re-run"})
            print("decisions/control:", r.status_code, flush=True)
            r.raise_for_status()
            await base._poll_recovery(client, prefix + f"/{execution}", args, project_db, counters, gate, result)
        result["final_check"] = await _verify_final(project_root)
    except RuntimeError as exc:
        result["error"] = str(exc)
        print("STOP:", exc)
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        httpx.AsyncClient.send = original_send
        asm.assemble = real_assemble
    await asyncio.sleep(1)
    return _finish(args, result, gate, counters, probes, decision_contexts, state_db, project_db, project_root, execution, work)


async def _verify_final(project_root: Path) -> dict:
    from app.services.artifact_verifier import verify_artifact
    final_dir = project_root / "final"
    files = sorted(p for p in final_dir.glob("*.mp4") if not p.name.startswith(".")) if final_dir.is_dir() else []
    if not files:
        return {"status": "failed", "reason": "no_final_file"}
    got = await verify_artifact(files[-1], root=project_root, kind="assembly")
    return {k: v for k, v in got.items() if k in ("status", "reason", "bytes", "sha256", "format", "width", "height",
                                                 "duration", "streams")} | {"file": files[-1].name}


def _finish(args, result, gate, counters, probes, decision_contexts, state_db, project_db, project_root, execution, work) -> int:
    records = base.read_records(state_db)
    events = qc._events(state_db, execution)
    attempts = sum(1 for e in events if e["kind"] == "node_start" and e.get("node_key") == TARGET)
    snap: dict[str, Any] = {
        **result,
        "counters": {**counters, "paid_posts": gate.paid_posts, "unexpected_model": gate.unexpected_model,
                     "refused_foreign": len([r for r in gate.refused if r["kind"] in ("dry_foreign", "foreign")])},
        "probes": probes, "ledger_rows": records["ledger_rows"],
        "spend_rows_paid": [r for r in records["spend_rows"] if str(r.get("provider")).lower() in MEDIA_PROVIDERS],  # not the (stub) Decision request rows
        "node_status": base._node_status(project_db), "node_attempts": attempts, "step_failure": qc._step_failure(project_db),
        "decision_contexts": decision_contexts, "approvals": _approvals(project_db),
        "part_files": _final_state(project_root, project_db)["part_files"],
        "refused_hosts": sorted({r["host"] for r in gate.refused if r["kind"] in ("dry_foreign", "foreign")}),
    }
    checks = evaluate(snap)
    ok, failed = verdict(checks)
    if result.get("error"):
        ok = False
        failed = ["flow_error"] + failed
        checks.insert(0, check("flow_completed_without_error", False, str(result["error"])))
    reality = reality_report()
    summary = {"mode": "dry", "node": NODE, "verdict": final_verdict(ok), "failed_checks": failed,
               "node_attempts": attempts, "asr_probe": result.get("asr_probe"), "not_proven": reality_not_proven()}
    slim = dict(snap)
    slim.update(checks=checks, ok=ok, failed=failed, reality=reality, summary=summary)
    (work / "result.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {NODE} | mode: dry (local, free) | node status: {snap['node_status']} | native attempts: {attempts}")
    print("counters:", json.dumps(snap["counters"], default=str))
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] or not c["needs"] else f"   [needs: {c['needs']}]"))
        if not c["ok"]:
            print("     detail:", json.dumps(c["detail"], ensure_ascii=False, default=str)[:400])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    print("asr probe:", result.get("asr_probe"))
    print("verdict:", summary["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
