"""script_frames_qc on Vibecode chat/completions: real app, local Vibecode stub, incident -> Decision -> recovery.

OFFLINE ONLY. There is deliberately no --live mode. Every HTTP request whose host is not
loopback is refused inside the httpx send hook (RuntimeError), so nothing can leave the
machine and nothing is paid for. Credentials are synthetic and set in this process only;
no .env file is read by this script.

What is real: the whole application in-process (FastAPI, worker, observer, diagnosis,
Decision supervisor, recovery verifier), the real six-node group `script_frames_qc`
(`n_excel_gpt_fw_script/check_script/action/shots/qc/report`) on the real executors,
prompts, code passes, gpt_api Vibecode transport (URL, headers, SSE parser, observe_model).

What is NOT real (the only fixtures):
  * the Vibecode model: a local stub answers POST .../v1/chat/completions (SSE when the
    request has stream=true, JSON otherwise) with the valid answers of
    scripts/run_script_frames_qc_space_test.py (SCENES, BITS, ACTION, PLAN, _kadry);
  * one chosen node (--break-node, default script) is answered with a broken answer
    (--break-mode) on every NON-recovery worker request, until the native retries are
    exhausted; the recovery attempt (retry_same) gets the valid answer;
  * the Decision model: a second stub on the same Vibecode chat/completions route
    (non-stream JSON) proposing read_events -> retry_same -> finish.

The project is seeded programmatically (no --seed-dir): Workflow/Project/WorkflowRun +
insert_node_group(script_frames_qc); the fw_script node creates its own seed VO cell from
project.script_text. The launch graph keeps the six group nodes in a linear `after` chain
(unless --native-graph), like tests/observe_v1/group_runner.prepare_launch_graph.

Limits: the seed has ONE voiceover cell, so every node (qc included) runs as a single pack; multi-pack /
partial-commit recovery (gate G3 with several packs) needs a seed with several VO units and is not covered here
(parse_result linkage for parallel packs is unit-tested in tests/test_vibecode_qc_response.py).

Checks that depend on code outside this script's author are tagged `needs` in result.json
(e.g. journal `model_end.complete` for chat/completions, `node_error.rule`); they are
reported separately so a red run points at the missing piece.

Usage (repository root, project venv):
    python scripts/e2e_vibecode_script_frames_qc.py --work-dir <empty dir>
    python scripts/e2e_vibecode_script_frames_qc.py --work-dir <dir> --break-node shots --break-mode undercount
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid as _uuid
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_space():
    spec = importlib.util.spec_from_file_location("space_harness_vibecode", _HERE / "run_script_frames_qc_space_test.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


space = _load_space()
SCENES, FULL_VO, BITS, ACTION, PLAN, _kadry = space.SCENES, space.FULL_VO, space.BITS, space.ACTION, space.PLAN, space._kadry

PORT = 18766
GROUP_ID = "script_frames_qc"
NODE_SUFFIXES = ("script", "check_script", "action", "shots", "qc", "report")
NODE_KEYS = tuple(f"n_excel_gpt_fw_{s}" for s in NODE_SUFFIXES)
BREAKABLE = ("script", "action", "shots", "qc")
BREAK_MODES = ("malformed", "truncated", "undercount")
CONTINUE_MARKER = "Предыдущий ответ оборван сетью"
MALFORMED = '{"ops": }'
TRUNCATED = '{"ops":[{"frame_uuid":"x","fields":{"биты":[{"порядок":1,'
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
PROVIDER_HOSTS = ("vibecode.moe", "kie.ai")
TERMINAL_DECISION = {"done", "human_review", "failed", "rejected", "cancelled"}
SYNTHETIC_KEY = "synthetic-vibecode-fixture"
PROFILE_ID = "vibecode-json-repair-v1"
# Same candidate as config/orchestrator-runtime.vibecode.example.json (a test compares them).
PROFILE = {"model_id": "gpt-5.6-sol", "provider": "vibecode", "kind": "text", "channel": "stable",
           "reasoning_effort": None, "max_output_tokens": None, "prompt_variant": "repair-json-only-v1",
           "synthetic_only": False}
RECOVERIES = ("retry_same", "retry_profile")
FORBIDDEN_PROFILE_KEYS = ("input", "reasoning", "max_output_tokens", "max_tokens", "max_completion_tokens",
                          "reasoning_effort")
CHECK_REPORT = ("# ОТЧЁТ ПРОВЕРКИ\nverdict: pass\nmode: fix\nsource_prompts: n_excel_gpt_fw_script\n\n"
                "## summary\nСинтетическая проверка формата битов: pass.\n")


# ───────────────────────────── pure helpers (unit-tested) ─────────────────────────────

def is_loopback(host: str | None) -> bool:
    return (host or "").strip("[]").lower() in LOOPBACK_HOSTS


def refusal_reason(method: str, host: str | None) -> str | None:
    """Why the send hook must refuse this request (None = allowed). Dry is the only mode."""
    if not is_loopback(host):
        return f"dry-run: non-loopback request refused ({method} {host})"
    return None


def is_chat_completions(path: str) -> bool:
    return path.rstrip("/").endswith("/chat/completions")


def is_kie_responses(path: str) -> bool:
    return path.rstrip("/").endswith("/codex/v1/responses")


def node_suffix(node_key: str | None) -> str | None:
    key = node_key or ""
    for suffix in sorted(NODE_SUFFIXES, key=len, reverse=True):
        if key.endswith("_fw_" + suffix):
            return suffix
    return None


def sse_chat_body(text: str, rid: str, *, model: str = "gpt-5.6-sol", style: str = "stream",
                  finish: str = "stop", status_completed: bool = False) -> bytes:
    """OpenAI-compatible chat/completions SSE, as the Vibecode parser expects.

    style `stream`: text deltas, finish chunk, [DONE]. `stream-usage`: same plus a trailing
    usage-only chunk (no choices). `status_completed` adds the Kie-style `status` to the last
    chunk (documents that the app must not need it).
    """
    cut = max(1, len(text) // 2)
    base = {"id": rid, "object": "chat.completion.chunk", "model": model}
    chunks = [
        {**base, "choices": [{"index": 0, "delta": {"role": "assistant", "content": text[:cut]}, "finish_reason": None}]},
        {**base, "choices": [{"index": 0, "delta": {"content": text[cut:]}, "finish_reason": None}]},
    ]
    final = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}
    if status_completed:
        final["status"] = "completed"
    if style != "stream-usage":
        final["usage"] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    chunks.append(final)
    if style == "stream-usage":
        chunks.append({**base, "choices": [], "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
    return ("".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode("utf-8")


def chat_json_body(text: str, rid: str, *, model: str = "gpt-5.6-sol", finish: str = "stop") -> bytes:
    return json.dumps({"id": rid, "object": "chat.completion", "model": model,
                       "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}},
                      ensure_ascii=False).encode("utf-8")


def request_text(body: dict) -> str:
    """All message text of a chat/completions body (string or list-of-parts content)."""
    parts: list[str] = []
    for message in body.get("messages") or []:
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(str(p.get("text") or "") for p in content if isinstance(p, dict))
    return "\n".join(parts)


def pack_for_request(frames: list[dict], text: str) -> list[dict]:
    """The frames whose uuid appears in the request text (the attached db_frames batch).

    Frame uuids are 24-hex strings (db_v2.new_frame_uuid), not RFC UUIDs, so the project DB is the
    source of truth and the request text is only searched for them. No match -> all frames.
    """
    low = (text or "").lower()
    pack = [f for f in frames if str(f.get("uuid") or "").lower() and str(f["uuid"]).lower() in low]
    return pack or list(frames)


def decision_context(text: str) -> dict | None:
    """The Decision context dict ({... 'allowed_actions' ...}) embedded in the request text."""
    decoder = json.JSONDecoder()
    for opening in re.finditer(r"\{", text or ""):
        try:
            value, _end = decoder.raw_decode(text[opening.start():])
        except ValueError:
            continue
        if isinstance(value, dict) and "allowed_actions" in value and "context_version" in value:
            return value
    return None


def decision_proposal(turn: int, ctx: dict, recovery: str = "retry_same") -> dict:
    """read_events -> retry_same|retry_profile -> finish; falls back to human_review when not allowed."""
    wanted = ("read_events", recovery, "finish")[min(max(turn, 1), 3) - 1]
    allowed = list(ctx.get("allowed_actions") or [])
    action = wanted if wanted in allowed else ("human_review" if "human_review" in allowed else wanted)
    evidence = list(ctx.get("evidence_ids") or [])[:1]
    arguments: dict = {}
    if action == "read_events":
        arguments = {"event_ids": evidence}
    elif action == "retry_profile":
        eligible = [p.get("profile_id") for p in ctx.get("eligible_profiles") or []]
        approved = [p.get("overlay_id") for p in ctx.get("approved_profiles") or []
                    if p.get("profile_id") in (None, PROFILE_ID)]
        if PROFILE_ID in eligible:
            arguments = {"profile_id": PROFILE_ID}
        elif approved:
            arguments = {"overlay_id": approved[0]}
        elif "human_review" in allowed:
            action = "human_review"
    return {"action": action, "arguments": arguments, "evidence_ids": evidence,
            "context_version": ctx["context_version"],
            "explanation": "synthetic bounded proposal from recorded runtime facts"}


def last_user_text(body: dict) -> str:
    for message in reversed(body.get("messages") or []):
        if isinstance(message, dict) and message.get("role") == "user":
            content = message.get("content")
            return content if isinstance(content, str) else ""
    return ""


def profile_request_facts(body: dict, suffix: str) -> dict:
    """What the recovery request looks like to the provider (checked for the Vibecode profile)."""
    return {"has_messages": isinstance(body.get("messages"), list) and bool(body.get("messages")),
            "forbidden_keys": sorted(k for k in FORBIDDEN_PROFILE_KEYS if k in body),
            "model": body.get("model"),
            "suffix_on_last_user": bool(suffix) and last_user_text(body).endswith(suffix)}


def frame_vo(frame: dict) -> str:
    return " ".join(str(frame.get("voiceover_text") or "").split())


def valid_answer(suffix: str, frames: list[dict], *, qc_answer: str = "noop") -> str:
    """Valid model answer for one pack of `frames` ({uuid, number, voiceover_text, attrs})."""
    if suffix == "check_script":
        return CHECK_REPORT
    ops: list[dict] = []
    for frame in frames:
        uid, number, vo = str(frame["uuid"]), int(frame.get("number") or 1), frame_vo(frame)
        if suffix == "script":
            ops.append({"frame_uuid": uid, "fields": {"биты": BITS}})
        elif suffix == "action" and vo == FULL_VO:
            ops.append({"frame_uuid": uid, "fields": {"главное_действие": ACTION, "площадка": PLAN}})
        elif suffix == "shots" and vo == FULL_VO:
            ops.append({"frame_uuid": uid, "fields": {"кадры": _kadry(number)}})
        elif suffix == "qc" and qc_answer == "edit" and vo.startswith("Он бежал"):
            kadry = [dict(k) for k in ((frame.get("attrs") or {}).get("кадры") or []) if isinstance(k, dict)]
            if kadry:
                kadry = [kadry[0]]
                kadry[0]["действие"] = "Иван бежит по улице к открытой двери дома"
                kadry[0].pop("меняет", None)
                ops.append({"frame_uuid": uid, "fields": {"кадры": kadry}})
    return json.dumps({"ops": ops}, ensure_ascii=False)


def broken_answer(mode: str, suffix: str, frames: list[dict]) -> str:
    if mode == "truncated":
        return TRUNCATED
    if mode == "undercount" and suffix == "shots":
        ops = [{"frame_uuid": str(f["uuid"]), "fields": {"кадры": _kadry(int(f.get("number") or 1))[:2]}}
               for f in frames if frame_vo(f) == FULL_VO]
        return json.dumps({"ops": ops}, ensure_ascii=False)
    return MALFORMED


def runtime_config(port: int, max_requests: int = 60, recovery: str = "retry_same") -> dict:
    """Production-mode Vibecode Decision config, loopback only (the send hook enforces it)."""
    cfg = {
        "version": 1, "mode": "production", "online_enabled": True,
        "endpoint": f"http://127.0.0.1:{port}/v1/chat/completions",
        "model_transport": "real", "external_calls_enabled": True,
        "retrieval_allowed": False, "continuation_allowed": False,
        "decision": {"catalog_id": "gpt-5.6-sol-vibecode", "model_id": "gpt-5.6-sol",
                     "reasoning_effort": None, "max_output_tokens": 2048, "timeout_s": 60},
        "profiles": {},
        "allowed_actions": ["read_events", "read_contract", "human_review", "wait", "finish", "retry_same"],
        "budget_policy": {"policy_id": "vibecode-script-frames-qc-e2e", "max_requests": max_requests},
    }
    if recovery == "retry_profile":
        cfg["profiles"] = {PROFILE_ID: dict(PROFILE)}
        cfg["allowed_actions"] = [*cfg["allowed_actions"], "retry_profile"]
    return cfg


def env_overrides(data: Path, port: int) -> dict[str, str]:
    """Process environment for the in-process app. Synthetic key, loopback base, no relay/proxy."""
    return {
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "TEXT_LLM_PROVIDER": "vibecode", "VIBECODE_API_KEY": SYNTHETIC_KEY,
        "VIBECODE_BASE_URL": f"http://127.0.0.1:{port}/v1",
        "GPT_API_KEY": SYNTHETIC_KEY, "GPT_BASE_URL": f"http://127.0.0.1:{port}",
        "GPT_MAX_RETRIES": "1", "HARNESS_GATE_DISABLED": "true",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "true", "WEB_HOST": "127.0.0.1", "WEB_PORT": str(port), "HITL_AUTO_APPROVE": "false",
        "BROWSER_CDP_AUTO_RECOVER": "false", "OUTSEE_HTTP_FALLBACK_CDP": "false",
        "AUDIO_USE_ELEVENLABS_FALLBACK": "false", "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false",
        "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
    }


def check(name: str, ok: bool, detail: Any = None, *, needs: str | None = None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail, "needs": needs}


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    """(all ok, names of failed checks). `needs` is informational: it says whose work is missing."""
    failed = [c["name"] for c in checks if not c["ok"]]
    return not failed, failed


def evaluate(snapshot: dict, *, break_node: str, break_mode: str, qc_answer: str = "noop",
             recovery: str = "retry_same") -> list[dict]:
    """Design assertions over a collected run snapshot (pure; the run's snapshot is built in main/_poll)."""
    target = f"n_excel_gpt_fw_{break_node}"
    counters = snapshot.get("counters", {})
    incidents = [i for i in (snapshot.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == target]
    records = (snapshot.get("decisions") or {}).get("records") or []
    recoveries = (snapshot.get("actions") or {}).get("recoveries") or []
    nodes = snapshot.get("node_status") or {}
    events = snapshot.get("events") or []
    out = [
        check("no_provider_host_attempt", not [h for h in snapshot.get("refused_hosts", [])
              if any(h.endswith(p) for p in PROVIDER_HOSTS)], snapshot.get("refused_hosts")),
        check("only_vibecode_chat_completions", counters.get("kie_responses", 0) == 0
              and counters.get("worker_total", 0) > 0, {"kie_responses": counters.get("kie_responses"),
                                                         "worker_total": counters.get("worker_total")}),
        check("decision_on_vibecode_route", counters.get("decision", 0) >= 1, counters.get("decision")),
    ]
    if break_mode == "truncated":
        # A cut answer (finish_reason=length) is uncertain: managed mode must hold, never POST again or recover.
        return [c for c in out if c["name"] != "decision_on_vibecode_route"] + [
            check("truncated_answer_posted_once", counters.get("by_node", {}).get(target) == 1, counters.get("by_node")),
            check("truncated_answer_holds_execution", (snapshot.get("actions") or {}).get("held") is True,
                  (snapshot.get("actions") or {}).get("held")),
            check("truncated_answer_not_recovered", not recoveries and nodes.get(target) != "done",
                  {"recoveries": [r["status"] for r in recoveries], "node": nodes.get(target)}),
            check("truncated_answer_has_incident", any(i["status"] in {"human_review", "open"} for i in incidents),
                  [(i["type"], i["status"]) for i in incidents]),
        ]
    if break_mode == "undercount":
        rules = [e["payload"].get("rule") for e in events if e["kind"] == "node_error" and e.get("node_key") == target]
        out += [
            check("incident_contract_rejected", any(i["type"] == "contract_rejected" for i in incidents),
                  [(i["type"], i["status"]) for i in incidents]),
            check("no_automatic_recovery_for_semantic_failure", not recoveries, [r["status"] for r in recoveries]),
            check("node_error_carries_rule", any(isinstance(r, str) and r.startswith("R-") for r in rules), rules,
                  needs="observation.py: add optional `rule` (apply_ops_batches.pass_stop_rule) to the node_error fact"),
        ]
        return out
    started = [e for e in events if e["kind"] == "model_start" and e.get("node_key") == target]
    ended = [e for e in events if e["kind"] == "model_end" and e.get("node_key") == target]
    out += [
        check("incident_invalid_json", any(i["type"] in {"invalid_json", "missing_payload"} for i in incidents),
              [(i["type"], i["status"], i.get("evidence_incomplete")) for i in incidents]),
        check("incident_current_and_complete_while_open", any(
            v["type"] in {"invalid_json", "missing_payload"} and v.get("node_key") == target and not v.get("evidence_incomplete")
            for v in (snapshot.get("incident_seen") or {}).values()),
            list((snapshot.get("incident_seen") or {}).values())[:4]),
        check("incident_resolved_after_recovery", any(i["type"] in {"invalid_json", "missing_payload"}
              and i["status"] == "resolved_confirmed" for i in incidents), [(i["type"], i["status"]) for i in incidents]),
        check("journal_model_end_complete_on_chat_completions", bool(started) and bool(ended)
              and all(e["payload"].get("complete") is True for e in ended),
              {"model_start": len(started), "model_end": len(ended),
               "complete": [e["payload"].get("complete") for e in ended][:6]},
              needs="observation.py / gpt_api.py: model_end.complete for chat/completions (llm_completion.response_complete)"),
        check("decision_done_with_retry_same", bool(records) and records[0].get("status") == "done"
              and 2 <= int(records[0].get("model_steps") or 0) <= 3,
              [(r.get("status"), r.get("model_steps")) for r in records],
              needs="decision_v1.py Vibecode route (HttpDecision) and decision_config production Vibecode"),
        check("recovery_verified", bool(recoveries) and recoveries[0].get("status") == "verified"
              and (recoveries[0].get("verification_result") or {}).get("verified") is True,
              [(r.get("status"), (r.get("verification_result") or {}).get("verified")) for r in recoveries],
              needs="actions_recovery.py gates G3/G9 on chat/completions completeness"),
        check("recovery_request_on_vibecode_profile", counters.get("worker_recovery", 0) >= 1,
              {"worker_recovery": counters.get("worker_recovery"), "kie_responses": counters.get("kie_responses")}),
        check("target_node_done", nodes.get(target) == "done", nodes.get(target)),
        check("chain_continued_all_six_done", all(nodes.get(k) == "done" for k in NODE_KEYS), nodes),
        check("hitl_none_pending", not snapshot.get("hitl_pending") and not any(
            v == "waiting_hitl" for v in nodes.values()), {"hitl": snapshot.get("hitl_pending")}),
    ]
    if recovery == "retry_profile":
        rq = snapshot.get("recovery_requests") or []
        ctx_events = [e for e in events if e["kind"] == "profile_effective_context" and e.get("node_key") == target]
        verif = [e for e in events if e["kind"] == "recovery_verification" and e.get("node_key") == target]
        out += [
            check("decision_chose_retry_profile", bool(recoveries) and (recoveries[0].get("preconditions") or {}).get("action_type") == "retry_profile",
                  [(r.get("preconditions") or {}).get("action_type") for r in recoveries]),
            check("profile_effective_context_recorded", any(
                (e["payload"].get("profile_id") == PROFILE_ID
                 and (e["payload"].get("candidate") or {}).get("provider") == "vibecode") for e in ctx_events),
                [(e["payload"].get("profile_id"), (e["payload"].get("candidate") or {}).get("provider")) for e in ctx_events]),
            check("profile_request_is_vibecode_chat_body", bool(rq) and all(
                r["host"] in LOOPBACK_HOSTS and is_chat_completions(r["path"]) and r["has_messages"]
                and not r["forbidden_keys"] for r in rq), rq[:3]),
            check("profile_repair_suffix_on_last_user_message", bool(rq) and all(r["suffix_on_last_user"] for r in rq),
                  [r["suffix_on_last_user"] for r in rq]),
            check("profile_model_is_vibecode_model", bool(rq) and all(r["model"] == PROFILE["model_id"] for r in rq),
                  [r["model"] for r in rq]),
            check("recovery_verification_event_verified", any(e["payload"].get("verified") is True for e in verif),
                  [e["payload"] for e in verif][:2]),
        ]
    starts = {(e.get("node_key"), e.get("request_id")) for e in events if e["kind"] == "model_start"}
    parses = [e for e in events if e["kind"] == "parse_result"]
    out.append(check("parse_result_linked_to_own_model_request", bool(parses) and all(
        (e.get("node_key"), e.get("request_id")) in starts for e in parses),
        [(e.get("node_key"), bool(e.get("request_id"))) for e in parses if (e.get("node_key"), e.get("request_id")) not in starts][:5]))
    if qc_answer == "noop":
        out.append(check("qc_noop_accepted_without_status", any(
            e["kind"] == "runtime_check" and e.get("node_key") == "n_excel_gpt_fw_qc"
            and e["payload"].get("check") == "qc_noop" and e["payload"].get("status") == "passed" for e in events),
            [e["payload"].get("check") for e in events if e["kind"] == "runtime_check"][:8]))
    return out


# ───────────────────────────── runtime (needs the app) ─────────────────────────────

def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", default=True,
                   help="default and only mode: every non-loopback request is refused (there is no --live)")
    p.add_argument("--work-dir", required=True, help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--break-node", choices=BREAKABLE, default="script")
    p.add_argument("--break-mode", choices=BREAK_MODES, default="malformed")
    p.add_argument("--recovery", choices=RECOVERIES, default="retry_same",
                   help="what the Decision stub proposes: retry_same, or retry_profile with the Vibecode profile "
                        + PROFILE_ID + " (adds it to the runtime config)")
    p.add_argument("--qc-answer", choices=("noop", "edit"), default="noop",
                   help="valid fw_qc answer: {'ops':[]} (needs completeness without status) or an edit")
    p.add_argument("--sse-style", choices=("stream", "stream-usage"), default="stream")
    p.add_argument("--status-completed", action="store_true",
                   help="add Kie-style status=completed to the last chunk (not needed by a correct app)")
    p.add_argument("--native-graph", action="store_true", help="keep the group's native edges (verdict loop) instead of a linear chain")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--request-limit", type=int, default=60)
    p.add_argument("--timeout-s", type=int, default=600)
    p.add_argument("--sleep-minutes", type=float, default=0.03, help="native step-failure sleep (patched for speed)")
    args = p.parse_args(argv)
    if args.recovery == "retry_profile" and args.break_mode != "malformed":
        p.error("--recovery retry_profile applies to the malformed-JSON scenario")
    if args.break_mode == "undercount" and args.break_node != "shots":
        p.error("--break-mode undercount only applies to --break-node shots")
    return args


def _frames(project_db: Path) -> list[dict]:
    if not project_db.exists():
        return []
    with sqlite3.connect(project_db) as db:
        rows = db.execute("SELECT uuid,number,voiceover_text,attrs FROM frames ORDER BY number").fetchall()
    return [{"uuid": u, "number": n, "voiceover_text": vo or "", "attrs": json.loads(attrs or "{}")}
            for u, n, vo, attrs in rows]


async def _seed(*, native_graph: bool = False) -> tuple[int, str]:
    """Workflow + Project + WorkflowRun + group, six nodes only (programmatic, no seed dir)."""
    from app.db import session_scope
    from app.models import Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus, NodeRun
    from app.services.node_groups import insert_node_group
    from sqlalchemy import delete, select

    kinds = ["topic", "plan", "script", "split", "hero"]
    nodes = [{"id": f"n_{k}", "type": k, "position": {"x": 80.0 + i * 290.0, "y": 200.0}, "data": {"label": k}}
             for i, k in enumerate(kinds)]
    edges = [{"id": f"e_{i}", "source": f"n_{a}", "target": f"n_{b}"} for i, (a, b) in enumerate(zip(kinds, kinds[1:], strict=False))]
    slug = f"vibecode-qc-{_uuid.uuid4().hex[:6]}"
    async with session_scope() as session:
        # Not the default workflow: startup would refresh a default one to the factory graph.
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        project = Project(slug=slug, topic="vibecode script_frames_qc e2e", status=ProjectStatus.script_ready,
                          script_text=FULL_VO,
                          meta={"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}})
        session.add(project)
        await session.flush()
        session.add(WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                                nodes_snapshot=nodes, edges_snapshot=edges))
        await insert_node_group(session, project, GROUP_ID)
        await session.flush()
        run = (await session.scalars(select(WorkflowRun).where(WorkflowRun.project_id == project.id)
                                     .order_by(WorkflowRun.id.desc()))).first()
        by_id = {n["id"]: n for n in run.nodes_snapshot}
        group_nodes = [by_id[k] for k in NODE_KEYS]
        native = [dict(e) for e in run.edges_snapshot if e.get("source") in NODE_KEYS and e.get("target") in NODE_KEYS]
        group_edges = native if native_graph and native else _linear_edges()
        run.nodes_snapshot = group_nodes
        run.edges_snapshot = group_edges
        meta = dict(project.meta or {})
        graph = dict(meta.get("canvas_graph") or {})
        graph["nodes"], graph["edges"] = group_nodes, group_edges
        meta["canvas_graph"] = graph
        project.meta = meta
        await session.execute(delete(NodeRun).where(NodeRun.workflow_run_id == run.id, NodeRun.node_key.notin_(NODE_KEYS)))
        pid = int(project.id)
        data_dir = project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(FULL_VO, encoding="utf-8")
    return pid, slug


def _linear_edges() -> list[dict]:
    return [{"id": f"vibecode-qc-edge-{i}", "source": a, "target": b, "data": {"kind": "after"}}
            for i, (a, b) in enumerate(zip(NODE_KEYS, NODE_KEYS[1:], strict=False))]


def _events(state_db: Path, execution: str | None) -> list[dict]:
    if not state_db.exists() or not execution:
        return []
    with sqlite3.connect(state_db) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT kind,node_key,attempt_id,request_id,payload FROM observation_events "
                          "WHERE execution_id=? ORDER BY id", (execution,)).fetchall()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload"] or "{}")
        except ValueError:
            payload = {}
        out.append({"kind": r["kind"], "node_key": r["node_key"], "attempt_id": r["attempt_id"],
                    "request_id": r["request_id"], "payload": payload})
    return out


def _node_status(project_db: Path) -> dict[str, str]:
    if not project_db.exists():
        return {}
    with sqlite3.connect(project_db) as db:
        return {k: s for k, s in db.execute("SELECT node_key,status FROM node_runs")}


def _hitl_pending(project_db: Path) -> list:
    if not project_db.exists():
        return []
    with sqlite3.connect(project_db) as db:
        try:
            return db.execute("SELECT kind,decision FROM hitl_requests WHERE decision IS NULL OR decision='pending'").fetchall()
        except sqlite3.Error:
            return []


def _step_failure(project_db: Path) -> dict:
    if not project_db.exists():
        return {}
    with sqlite3.connect(project_db) as db:
        row = db.execute("SELECT meta FROM projects LIMIT 1").fetchone()
    return (json.loads(row[0] or "{}").get("step_failure") or {}) if row else {}


async def _wait_api(base: str) -> None:
    import httpx
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        for _ in range(120):
            try:
                if (await client.get(base)).status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("application API did not start")


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    work = Path(args.work_dir).resolve()
    data = work / "data"
    if data.exists():
        shutil.rmtree(data)
    work.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True)
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(runtime_config(args.port, args.request_limit, args.recovery), indent=2), encoding="utf-8")
    os.chdir(ROOT)
    os.environ.update(env_overrides(data, args.port))
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(runtime_path)

    import httpx
    from app.settings import settings
    # Belt and braces over environment precedence: nothing may route through a relay/proxy.
    settings.gpt_relay_token = ""
    settings.gpt_proxy_url = None
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    from app.services import observation, step_failure_policy
    from app.services.decision_transport import current as decision_hook
    step_failure_policy.SLEEP_MINUTES = args.sleep_minutes
    step_failure_policy.XLSX_SHEET_FORMAT_SLEEP_MINUTES = args.sleep_minutes

    import app.services.shots_report as shots_report
    shots_report.find_project_root = lambda: work  # the HTML report goes to <work>/exports, not the repository
    from app import main as app_main
    await app_main._init_db()
    pid, slug = await _seed(native_graph=args.native_graph)
    project_db = data / "videos" / slug / "project.db"
    base = f"http://127.0.0.1:{args.port}/api/projects/{pid}"
    target = f"n_excel_gpt_fw_{args.break_node}"

    counters = {"worker_total": 0, "worker_broken": 0, "worker_recovery": 0, "decision": 0,
                "kie_responses": 0, "refused": 0, "by_node": {}}
    refused_hosts: list[str] = []
    request_log: list[dict] = []
    recovery_requests: list[dict] = []
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    repair_suffix = APPROVED_PROFILE_PROMPTS[PROFILE["prompt_variant"]]
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        host, path = request.url.host, request.url.path
        reason = refusal_reason(request.method, host)
        if reason:
            counters["refused"] += 1
            refused_hosts.append(str(host))
            raise RuntimeError(reason)
        if request.method != "POST" or not (is_chat_completions(path) or is_kie_responses(path)):
            return await original_send(client, request, **kwargs)
        if is_kie_responses(path):
            counters["kie_responses"] += 1
            request_log.append({"route": "kie_responses", "path": path})
            raise RuntimeError("Kie Responses route used; this group must run on Vibecode chat/completions")
        body = json.loads(request.content.decode("utf-8") or "{}")
        stream = bool(body.get("stream"))
        text = request_text(body)
        rid = f"chatcmpl-{_uuid.uuid4().hex[:12]}"
        if decision_hook.get() is not None:
            counters["decision"] += 1
            ctx = decision_context(text)
            if ctx is None:
                raise RuntimeError("decision stub: no context in the request")
            answer = json.dumps(decision_proposal(counters["decision"], ctx, args.recovery), ensure_ascii=False)
            request_log.append({"route": "decision", "turn": counters["decision"], "stream": stream})
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=chat_json_body(answer, rid), request=request)
        attempt = observation._attempt.get()
        key = getattr(attempt, "node_key", None)
        suffix = node_suffix(key)
        if suffix is None:
            raise RuntimeError(f"worker stub: request outside the group (node_key={key!r})")
        recovery = bool(attempt and attempt.session.info.get("recovery_attempt_id") == attempt.attempt_id)
        counters["worker_total"] += 1
        counters["worker_recovery"] += 1 if recovery else 0
        counters["by_node"][key] = counters["by_node"].get(key, 0) + 1
        frames = _frames(project_db)
        pack = pack_for_request(frames, text)
        broken = key == target and not recovery
        finish = "stop"
        if CONTINUE_MARKER in text:
            # gpt_api's "continue after a cut" follow-up: nothing more to say, the first text stays salvaged.
            counters["continue_requests"] = counters.get("continue_requests", 0) + 1
            answer = ""
        elif broken:
            counters["worker_broken"] += 1
            answer = broken_answer(args.break_mode, suffix, pack)
            finish = "length" if args.break_mode == "truncated" else "stop"
        else:
            answer = valid_answer(suffix, pack, qc_answer=args.qc_answer)
        if recovery:
            recovery_requests.append({"node": key, "path": path, "host": host,
                                      **profile_request_facts(body, repair_suffix)})
        request_log.append({"route": "worker", "node": key, "recovery": recovery, "broken": broken,
                            "stream": stream, "path": path, "pack_numbers": [f.get("number") for f in pack],
                            "matched": any(str(f["uuid"]).lower() in text.lower() for f in frames)})
        if stream:
            content = sse_chat_body(answer, rid, style=args.sse_style, status_completed=args.status_completed, finish=finish)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content, request=request)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=chat_json_body(answer, rid, finish=finish), request=request)

    httpx.AsyncClient.send = send
    task = asyncio.create_task(app_main.main())
    result: dict[str, Any] = {"mode": "dry", "target": target, "break_mode": args.break_mode, "project_id": pid,
                              "slug": slug, "args": vars(args)}
    execution = None
    try:
        await _wait_api(base)
        async with httpx.AsyncClient(timeout=30, trust_env=False,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            options = (await client.get(base + "/observations/launch-options")).json()
            print("launch-options configured:", options["configured"], "mode:", options["mode"],
                  "strategies:", options["strategies"])
            result["launch_options"] = {k: options[k] for k in ("configured", "mode", "strategies", "reason")}
            if not options["configured"]:
                raise RuntimeError("Decision runtime not configured: " + str(options.get("reason")))
            unsupported = [n["node_key"] for n in options["nodes"] if n["node_key"] in NODE_KEYS and not n["supported"]]
            if unsupported:
                raise RuntimeError(f"nodes not supported by the managed launch gate: {unsupported}")
            strategies = ["wait", "human_review", "retry_same"]
            profile_ids: list[str] = []
            profile_hash: dict = {}
            if args.recovery == "retry_profile":
                if PROFILE_ID not in [p["profile_id"] for p in options.get("profiles", [])]:
                    raise RuntimeError(f"launch-options do not offer {PROFILE_ID}: {options.get('profiles')}")
                strategies.append("retry_profile")
                profile_ids = [PROFILE_ID]
                profile_hash = {"profile_catalog_hash": options["profile_catalog_hash"]}
            response = await client.post(base + "/observations/launch", json={
                "workflow_run_id": options["workflow_run_id"], "graph_version": options["graph_version"],
                "node_keys": list(NODE_KEYS), "strategies": strategies,
                "profile_ids": profile_ids, **profile_hash, "request_limit": args.request_limit,
                "rationale": "offline vibecode script_frames_qc recovery verification",
                "idempotency_key": f"vibecode-qc-e2e-{_uuid.uuid4().hex[:8]}"})
            print("launch:", response.status_code, (response.text[:400] if response.status_code >= 300 else "ok"))
            if response.status_code >= 300:
                result["launch_error"] = response.text[:600]
                raise RuntimeError("launch refused: " + response.text[:300])
            execution = response.json()["execution_id"]
            result["execution_id"] = execution
            if args.break_node:
                response = await client.post(base + f"/observations/{execution}/decisions/control", json={
                    "enabled": True, "rationale": "offline decision loop on the Vibecode loopback stub"})
                print("decisions/control:", response.status_code)
                response.raise_for_status()
            await _poll(client, base, execution, args, project_db, counters, result)
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
    await asyncio.sleep(2)
    result.update(recovery_requests=recovery_requests, counters=counters, refused_hosts=sorted(set(refused_hosts)), request_log=request_log[-80:],
                  node_status=_node_status(project_db), hitl_pending=_hitl_pending(project_db),
                  step_failure={k: v for k, v in _step_failure(project_db).items() if k in {"total_fails", "abandoned_at", "last_error"}},
                  events=_events(data / "state.db", execution))
    checks = evaluate(result, break_node=args.break_node, break_mode=args.break_mode, qc_answer=args.qc_answer,
                      recovery=args.recovery)
    ok, failed = verdict(checks)
    result["warnings"] = sorted({i["type"] for i in (result.get("diagnoses") or {}).get("incidents", [])
                                 if i.get("node_key") != target and i["type"] not in {"runtime_wait"}})
    result["checks"], result["ok"], result["failed"] = checks, ok, failed
    slim = {k: v for k, v in result.items() if k != "events"}
    slim["event_kinds"] = sorted({e["kind"] for e in result["events"]})
    (work / "result.json").write_text(json.dumps(slim, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print("\n=== RESULT ===")
    print("target:", target, "| mode:", args.break_mode, "| nodes:", result["node_status"])
    print("counters:", {k: v for k, v in counters.items() if k != "by_node"}, "| by node:", counters["by_node"])
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] or not c["needs"] else f"   [needs: {c['needs']}]"))
        if not c["ok"]:
            print("     detail:", json.dumps(c["detail"], ensure_ascii=False, default=str)[:300])
    if result["warnings"]:
        print("other incident types seen (informational):", result["warnings"])
    print("OK" if ok else "NOT OK: " + ", ".join(failed))
    return 0 if ok else 1


async def _poll(client, base, execution, args, project_db, counters, result) -> None:
    deadline = time.monotonic() + args.timeout_s
    prefix = base + f"/observations/{execution}"
    target = f"n_excel_gpt_fw_{args.break_node}"
    last = None
    last_progress = None
    idle_since = time.monotonic()
    settled_since = None
    while time.monotonic() < deadline:
        decisions = (await client.get(prefix + "/decisions")).json()
        actions = (await client.get(prefix + "/actions")).json()
        diagnoses = (await client.get(prefix + "/diagnoses")).json()
        nodes = _node_status(project_db)
        result.update(decisions=decisions, actions=actions, diagnoses=diagnoses)
        seen = result.setdefault("incident_seen", {})
        for i in diagnoses.get("incidents", []):
            if i.get("currently_relevant"):
                seen[i["incident_id"]] = {k: i.get(k) for k in ("type", "status", "node_key", "blocking", "evidence_incomplete")}
        recs, recoveries = decisions["records"], actions["recoveries"]
        state = (nodes.get(target), [(d["status"], d["model_steps"]) for d in recs],
                 [r["status"] for r in recoveries], [(i["type"], i["status"]) for i in diagnoses.get("incidents", [])],
                 {k: v for k, v in counters.items() if k != "by_node"})
        if state != last:
            print(time.strftime("%H:%M:%S"), state, flush=True)
            last = state
        chain_done = all(nodes.get(k) == "done" for k in NODE_KEYS)
        decision_done = bool(recs) and recs[0]["status"] in TERMINAL_DECISION
        progress = (state, tuple(sorted(nodes.items())))
        if progress != last_progress:
            last_progress, idle_since = progress, time.monotonic()
        # Settled: the whole chain is done, or the decision ended and nothing has changed for a while
        # (e.g. human_review / a final failure / undercount that is never recovered).
        idle = time.monotonic() - idle_since
        final = chain_done or (decision_done and idle > 45) or (args.break_mode == "undercount" and idle > 45)
        if final:
            settled_since = settled_since or time.monotonic()
            if time.monotonic() - settled_since > 5:
                return
        else:
            settled_since = None
        await asyncio.sleep(1)
    result["stopped_at"] = "timeout"


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
