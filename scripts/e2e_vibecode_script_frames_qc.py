"""script_frames_qc on Vibecode chat/completions: real app, incident -> Decision -> recovery.

Three modes. DEFAULT (offline): every HTTP request whose host is not loopback is refused inside the httpx
send hook (RuntimeError), so nothing can leave the machine and nothing is paid for; credentials are synthetic
and no .env file is read.
--dry: the live-shaped flow (see below) with every provider-bound request refused and counted; proves the
injection plumbing and the caps without sending anything. --env-file is optional (only to check the relay route).
--live (paid, capped): the broken node's worker requests get a locally injected malformed answer (reality cannot be
made to return broken JSON on demand; free), the other nodes' worker requests get the local valid stub answer
(free, counted `stubbed_other`), and ONLY the Decision request(s) and the RECOVERY worker request go to the real
Vibecode /v1/chat/completions. Hard caps: --max-real-posts (default 6), --max-usd (default 0.10, estimated from the
recorded usage with api_tracker_hook.vibecode_cost x3), only vibecode.moe (or the configured relay host) may
receive a request, everything else is refused and counted. Keys are read from --env-file ONLY by the names in
LIVE_ENV_NAMES and are never printed or written.

What is real: the whole application in-process (FastAPI, worker, observer, diagnosis,
Decision supervisor, recovery verifier), the real group `script_frames_qc`
(its nodes are read from the group definition; today skeleton, site, shots, QC, report) on the real executors,
prompts, code passes, gpt_api Vibecode transport (URL, headers, SSE parser, observe_model).

What is NOT real (the only fixtures; in live only the injected answers and `stubbed_other` remain):
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
project.script_text. The launch graph keeps the group nodes in a linear `after` chain
(unless --native-graph), like tests/observe_v1/group_runner.prepare_launch_graph.

Limits: the seed has ONE voiceover cell, so every node (qc included) runs as a single pack; multi-pack /
partial-commit recovery (gate G3 with several packs) needs a seed with several VO units and is not covered here
(parse_result linkage for parallel packs is unit-tested in tests/test_vibecode_qc_response.py).

Checks that depend on code outside this script's author are tagged `needs` in result.json
(e.g. journal `model_end.complete` for chat/completions, `node_error.rule`); they are
reported separately so a red run points at the missing piece.

Usage (repository root, project venv):
    python scripts/e2e_vibecode_script_frames_qc.py --work-dir <empty dir>
    python scripts/e2e_vibecode_script_frames_qc.py --work-dir <dir> --dry
    python scripts/e2e_vibecode_script_frames_qc.py --work-dir <dir> --live --env-file <env file>
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
_GROUP_CACHE: dict[str, tuple] = {}


def _group_nodes() -> tuple:
    """(local_key, node id, has_model) of every node of the group, from the group definition.

    Lazy on purpose: importing app.* loads app.settings, which must happen after main() set the environment.
    The group is reworked by the customer now and then; nothing here hard-codes its composition.
    """
    if "nodes" not in _GROUP_CACHE:
        from app.services.node_groups import get_node_group
        group = get_node_group(GROUP_ID)
        _GROUP_CACHE["nodes"] = tuple(
            (n.local_key, n.preferred_id, (n.operator_config or {}).get("transport") != "code") for n in group.nodes)
        _GROUP_CACHE["edges"] = tuple(tuple(e) for e in group.internal_edges)
    return _GROUP_CACHE["nodes"]


def node_suffixes() -> tuple[str, ...]:
    return tuple(k for k, _id, _m in _group_nodes())


def node_keys() -> tuple[str, ...]:
    return tuple(i for _k, i, _m in _group_nodes())


def model_suffixes() -> tuple[str, ...]:
    """Nodes that call the model (the final code-only report node does not)."""
    return tuple(k for k, _i, m in _group_nodes() if m)


def target_key(suffix: str) -> str:
    return dict((k, i) for k, i, _m in _group_nodes())[suffix]
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
LIVE_ENV_NAMES = ("VIBECODE_API_KEY", "VIBECODE_BASE_URL", "GPT_RELAY_TOKEN", "GPT_PROXY_URL", "GPT_BASE_URL")
DIRECT_HOST = "vibecode.moe"
COST_MARKUP = 3.0  # pessimistic multiplier over the catalog list price
FORBIDDEN_PROFILE_KEYS = ("input", "reasoning", "max_output_tokens", "max_tokens", "max_completion_tokens",
                          "reasoning_effort")

# ───────────────────────────── pure helpers (unit-tested) ─────────────────────────────

def is_loopback(host: str | None) -> bool:
    return (host or "").strip("[]").lower() in LOOPBACK_HOSTS


def refusal_reason(method: str, host: str | None) -> str | None:
    """Why the send hook must refuse this request (None = allowed). Dry is the only mode."""
    if not is_loopback(host):
        return f"dry-run: non-loopback request refused ({method} {host})"
    return None


# ───────────────────────────── live-mode helpers (pure, unit-tested) ─────────────────────────────

def load_env_whitelist(path: str) -> dict[str, str]:
    """Only the names in LIVE_ENV_NAMES from an env file; nothing else is kept, nothing is printed."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in LIVE_ENV_NAMES:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def relay_base(env: dict) -> str | None:
    """VPS relay base, same rule as settings.vps_relay_base_url (token + a base that is not kie/vibecode)."""
    token, base = (env.get("GPT_RELAY_TOKEN") or "").strip(), (env.get("GPT_BASE_URL") or "").strip().rstrip("/")
    if not token or not base or any(h in base.lower() for h in ("kie.ai", "vibecode.moe")):
        return None
    return base


def live_endpoint(env: dict) -> str:
    """The Vibecode chat/completions URL the app will build (gpt_api._chat_url) for these env values."""
    base = relay_base(env) or (env.get("VIBECODE_BASE_URL") or "https://vibecode.moe/v1").strip().rstrip("/")
    return base + ("/chat/completions" if base.lower().endswith("/v1") else "/v1/chat/completions")


def allowed_hosts(env: dict) -> frozenset[str]:
    """The only non-loopback hosts that may receive a real request: vibecode.moe or the configured relay."""
    from urllib.parse import urlsplit
    hosts = {DIRECT_HOST}
    relay = relay_base(env)
    if relay:
        hosts.add((urlsplit(relay).hostname or "").lower())
    hosts.discard("")
    return frozenset(hosts)


def real_post_refusal(host: str | None, *, allowed: frozenset[str], real_posts: int, max_posts: int,
                      spent_usd: float, max_usd: float) -> str | None:
    """Why a provider-bound request must be refused ("host:" or "cap:" prefix), None = may be sent."""
    if (host or "").strip("[]").lower() not in allowed:
        return f"host: {host!r} is not an allowed provider host"
    if real_posts >= max_posts:
        return f"cap: real post cap reached ({max_posts})"
    if spent_usd >= max_usd:
        return f"cap: cost cap reached (${spent_usd:.4f} >= ${max_usd})"
    return None


def estimate_usd(rows: list[dict], cost_fn, markup: float = COST_MARKUP) -> tuple[float, int]:
    """(pessimistic USD, rows with zero usage) from tracker rows; catalog price x markup, else recorded cost x markup."""
    total, zero = 0.0, 0
    for row in rows:
        prompt, completion, cached = (int(row.get(k) or 0) for k in ("prompt_tokens", "completion_tokens", "cached_tokens"))
        cost = cost_fn(row.get("model"), prompt, completion, cached)
        if cost is None:
            cost = float(row.get("cost_usd") or 0.0)
        if not (prompt or completion or cost):
            zero += 1
        total += cost * markup
    return round(total, 6), zero


def live_env_overrides(base: dict[str, str], env: dict) -> tuple[dict[str, str], list[str]]:
    """(environment to set, names to remove) for live/dry: loopback bases dropped, real values only from `env`."""
    out = {k: v for k, v in base.items() if k not in ("VIBECODE_BASE_URL", "GPT_BASE_URL", "VIBECODE_API_KEY")}
    out["VIBECODE_API_KEY"] = env.get("VIBECODE_API_KEY") or SYNTHETIC_KEY
    drop = ["VIBECODE_BASE_URL", "GPT_BASE_URL", "GPT_RELAY_TOKEN", "GPT_PROXY_URL"]
    for name in tuple(drop):
        if env.get(name):
            out[name] = env[name]
            drop.remove(name)
    return out, drop


def slim_record(record: dict | None) -> dict:
    """Scalar fields of a Decision/recovery record (no nested prompts or contexts)."""
    return {k: v for k, v in (record or {}).items() if isinstance(v, (str, int, float, bool)) or v is None}


def is_chat_completions(path: str) -> bool:
    return path.rstrip("/").endswith("/chat/completions")


def is_kie_responses(path: str) -> bool:
    return path.rstrip("/").endswith("/codex/v1/responses")


def node_suffix(node_key: str | None) -> str | None:
    key = node_key or ""
    for suffix in sorted(node_suffixes(), key=len, reverse=True):
        if key == target_key(suffix):
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


def valid_answer(suffix: str, frames: list[dict], *, qc_answer: str = "noop", with_action: bool = False) -> str:
    """Valid model answer for one pack of `frames` ({uuid, number, voiceover_text, attrs}).

    `with_action` also writes the legacy `главное_действие` from the site node. The reworked group no longer
    has an action node, and without that field the shots pass cannot hard-stop (see --break-mode undercount).
    """
    ops: list[dict] = []
    for frame in frames:
        uid, number, vo = str(frame["uuid"]), int(frame.get("number") or 1), frame_vo(frame)
        if suffix == "script":
            ops.append({"frame_uuid": uid, "fields": {"биты": BITS}})
        elif suffix == "action" and vo == FULL_VO:
            ops.append({"frame_uuid": uid, "fields": ({"главное_действие": ACTION, "площадка": PLAN} if with_action else {"площадка": PLAN})})
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
        # Valid JSON, zero shots. The shots pass hard-stops on this ("GPT не вернул кадры") only when the frame has
        # `главное_действие`, hence with_action in the undercount scenario; a partial undercount is a soft WARN.
        ops = [{"frame_uuid": str(f["uuid"]), "fields": {"кадры": []}}
               for f in frames if frame_vo(f) == FULL_VO]
        return json.dumps({"ops": ops}, ensure_ascii=False)
    return MALFORMED


def runtime_config(port: int, max_requests: int = 60, recovery: str = "retry_same", *,
                   endpoint: str | None = None) -> dict:
    """Production-mode Vibecode Decision config; loopback unless `endpoint` (live/dry) is given."""
    cfg = {
        "version": 1, "mode": "production", "online_enabled": True,
        "endpoint": endpoint or f"http://127.0.0.1:{port}/v1/chat/completions",
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
        if endpoint:  # live: the real Decision must pick the profile, not the plain retry
            cfg["allowed_actions"].remove("retry_same")
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
             recovery: str = "retry_same", live_hosts: frozenset[str] | None = None) -> list[dict]:
    """Design assertions over a collected run snapshot (pure; the run's snapshot is built in main/_poll)."""
    target = target_key(break_node)
    counters = snapshot.get("counters", {})
    incidents = [i for i in (snapshot.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == target]
    records = (snapshot.get("decisions") or {}).get("records") or []
    recoveries = (snapshot.get("actions") or {}).get("recoveries") or []
    nodes = snapshot.get("node_status") or {}
    events = snapshot.get("events") or []
    out = [
        check("no_provider_host_attempt", not snapshot.get("refused_hosts", []) if live_hosts is not None
              else not [h for h in snapshot.get("refused_hosts", []) if any(h.endswith(p) for p in PROVIDER_HOSTS)],
              snapshot.get("refused_hosts")),
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
              and (int(records[0].get("model_steps") or 0) >= 1 if live_hosts is not None
                   else 2 <= int(records[0].get("model_steps") or 0) <= 3),
              [(r.get("status"), r.get("model_steps")) for r in records],
              needs="decision_v1.py Vibecode route (HttpDecision) and decision_config production Vibecode"),
        check("recovery_verified", bool(recoveries) and recoveries[0].get("status") == "verified"
              and (recoveries[0].get("verification_result") or {}).get("verified") is True,
              [(r.get("status"), (r.get("verification_result") or {}).get("verified")) for r in recoveries],
              needs="actions_recovery.py gates G3/G9 on chat/completions completeness"),
        check("recovery_request_on_vibecode_profile", counters.get("worker_recovery", 0) >= 1,
              {"worker_recovery": counters.get("worker_recovery"), "kie_responses": counters.get("kie_responses")}),
        check("target_node_done", nodes.get(target) == "done", nodes.get(target)),
        check("chain_continued_all_nodes_done", all(nodes.get(k) == "done" for k in node_keys()), nodes),
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
                (r["host"] in LOOPBACK_HOSTS or r["host"] in (live_hosts or ())) and is_chat_completions(r["path"]) and r["has_messages"]
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
    if qc_answer == "noop" and "qc" in node_suffixes():
        out.append(check("qc_noop_accepted_without_status", any(
            e["kind"] == "runtime_check" and e.get("node_key") == target_key("qc")
            and e["payload"].get("check") == "qc_noop" and e["payload"].get("status") == "passed" for e in events),
            [e["payload"].get("check") for e in events if e["kind"] == "runtime_check"][:8]))
    return out


def evaluate_live(snapshot: dict, *, break_node: str, break_mode: str, qc_answer: str, recovery: str,
                  hosts: frozenset[str], max_real_posts: int, max_usd: float) -> list[dict]:
    """The design checks over the live run plus what only the live run can show (caps, real posts)."""
    counters, spend = snapshot.get("counters", {}), snapshot.get("spend") or {}
    out = evaluate(snapshot, break_node=break_node, break_mode=break_mode, qc_answer=qc_answer,
                   recovery=recovery, live_hosts=hosts)
    out += [
        check("caps_not_exceeded", counters.get("real_total", 0) <= max_real_posts
              and float(spend.get("estimated_usd") or 0) <= max_usd,
              {"real_total": counters.get("real_total"), "max_real_posts": max_real_posts,
               "estimated_usd": spend.get("estimated_usd"), "max_usd": max_usd}),
        check("broken_answers_injected_locally", counters.get("injected", 0) >= 1, counters.get("injected")),
        check("usage_recorded_for_real_posts", counters.get("real_total", 0) == 0 or int(spend.get("rows") or 0) >= 1,
              {"real_total": counters.get("real_total"), "tracker_rows": spend.get("rows")},
              needs="api_tracker_hook: a row with usage per real Vibecode request (the spend estimate reads it)"),
    ]
    if break_mode == "malformed":
        out += [
            check("real_decision_request_sent", counters.get("real_decision", 0) >= 1, counters.get("real_decision")),
            check("real_recovery_request_sent", counters.get("real_worker_recovery", 0) >= 1,
                  counters.get("real_worker_recovery")),
        ]
    return out


# ───────────────────────────── runtime (needs the app) ─────────────────────────────

def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true",
                   help="live-shaped flow, every provider-bound request refused and counted (nothing is sent)")
    p.add_argument("--live", action="store_true",
                   help="PAID, capped: Decision + recovery requests go to the real Vibecode route (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(LIVE_ENV_NAMES) + " are read from it")
    p.add_argument("--max-real-posts", type=int, default=6, help="hard cap of real provider requests (live)")
    p.add_argument("--max-usd", type=float, default=0.10, help="hard cap of the estimated spend, catalog price x3 (live)")
    p.add_argument("--work-dir", required=True, help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--break-node", choices=model_suffixes(), default=model_suffixes()[0])
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
    if args.live and args.dry:
        p.error("--live and --dry are mutually exclusive")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.max_real_posts < 1 or args.max_usd <= 0:
        p.error("--max-real-posts must be >= 1 and --max-usd > 0")
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
    """Workflow + Project + WorkflowRun + group, its nodes only (programmatic, no seed dir)."""
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
        group_nodes = [by_id[k] for k in node_keys()]
        native = [dict(e) for e in run.edges_snapshot if e.get("source") in node_keys() and e.get("target") in node_keys()]
        group_edges = native if native_graph and native else _linear_edges()
        run.nodes_snapshot = group_nodes
        run.edges_snapshot = group_edges
        meta = dict(project.meta or {})
        graph = dict(meta.get("canvas_graph") or {})
        graph["nodes"], graph["edges"] = group_nodes, group_edges
        meta["canvas_graph"] = graph
        project.meta = meta
        await session.execute(delete(NodeRun).where(NodeRun.workflow_run_id == run.id, NodeRun.node_key.notin_(node_keys())))
        pid = int(project.id)
        data_dir = project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(FULL_VO, encoding="utf-8")
    return pid, slug


def _linear_edges() -> list[dict]:
    return [{"id": f"vibecode-qc-edge-{i}", "source": a, "target": b, "data": {"kind": "after"}}
            for i, (a, b) in enumerate(zip(node_keys(), node_keys()[1:], strict=False))]


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


def _tracker_rows(db_path: Path) -> list[dict]:
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(db_path) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(
                "SELECT model,prompt_tokens,completion_tokens,cached_tokens,cost_usd FROM api_calls ORDER BY id")]
    except sqlite3.Error:
        return []


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
    # The environment must be set before anything imports app.settings (_args reads the group definition).
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--work-dir")
    pre.add_argument("--port", type=int, default=PORT)
    pre.add_argument("--request-limit", type=int, default=60)
    pre.add_argument("--recovery", default="retry_same")
    pre.add_argument("--live", action="store_true")
    pre.add_argument("--dry", action="store_true")
    pre.add_argument("--env-file")
    early, _rest = pre.parse_known_args(argv)
    if not early.work_dir or (early.live and (early.dry or not early.env_file)):
        print("--work-dir is required; --live needs --env-file and excludes --dry")
        return 2
    mode = "live" if early.live else "dry" if early.dry else "offline"
    live_env: dict[str, str] = {}
    if mode != "offline" and early.env_file:
        live_env = load_env_whitelist(early.env_file)  # values stay in this dict and the process environment
    if mode == "live" and (not live_env.get("VIBECODE_API_KEY") or live_env["VIBECODE_API_KEY"].startswith("synthetic")):
        print("env file has no VIBECODE_API_KEY; nothing sent")
        return 3
    hosts = allowed_hosts(live_env)
    work = Path(early.work_dir).resolve()
    data = work / "data"
    if data.exists():
        shutil.rmtree(data)
    work.mkdir(parents=True, exist_ok=True)
    data.mkdir(parents=True)
    runtime_path = work / "runtime.json"
    endpoint = live_endpoint(live_env) if mode != "offline" else None
    runtime_path.write_text(json.dumps(runtime_config(early.port, early.request_limit, early.recovery, endpoint=endpoint),
                                       indent=2), encoding="utf-8")
    os.chdir(ROOT)
    if mode == "offline":
        os.environ.update(env_overrides(data, early.port))
    else:
        overrides, drop = live_env_overrides(env_overrides(data, early.port), live_env)
        for name in drop:
            os.environ.pop(name, None)
        os.environ.update(overrides)
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(runtime_path)
    args = _args(argv)

    import httpx
    from app.settings import settings
    # Belt and braces over environment precedence: offline nothing may route through a relay/proxy; live/dry
    # use exactly the whitelisted env-file values (a stray repository .env cannot add a route).
    settings.gpt_relay_token = live_env.get("GPT_RELAY_TOKEN", "") if mode != "offline" else ""
    settings.gpt_proxy_url = (live_env.get("GPT_PROXY_URL") or None) if mode != "offline" else None
    if mode != "offline":
        settings.vibecode_api_key = live_env.get("VIBECODE_API_KEY") or SYNTHETIC_KEY
        settings.vibecode_base_url = live_env.get("VIBECODE_BASE_URL", "")
        settings.gpt_base_url = live_env.get("GPT_BASE_URL", "")
    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if tracker._TRACKER_DB.exists():
        tracker._TRACKER_DB.unlink()
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
    target = target_key(args.break_node)

    counters = {"worker_total": 0, "worker_broken": 0, "worker_recovery": 0, "decision": 0,
                "kie_responses": 0, "refused": 0, "by_node": {}}
    if mode != "offline":
        counters.update(injected=0, stubbed_other=0, real_decision=0, real_worker_recovery=0, real_total=0,
                        refused_cap=0, refused_dry=0)
    aborted: list[str] = []
    refused_hosts: list[str] = []
    request_log: list[dict] = []
    recovery_requests: list[dict] = []
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS
    repair_suffix = APPROVED_PROFILE_PROMPTS[PROFILE["prompt_variant"]]
    original_send = httpx.AsyncClient.send

    def spend_now() -> tuple[float, int, int]:
        rows = _tracker_rows(work / "tracker.db")
        usd, zero = estimate_usd(rows, tracker.vibecode_cost)
        return usd, zero, len(rows)

    def local_worker(request, body, text, key, suffix, recovery):
        """Answer a worker request locally: injected broken answer for the target, valid stub for the rest."""
        stream = bool(body.get("stream"))
        rid = f"chatcmpl-{_uuid.uuid4().hex[:12]}"
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
            answer = valid_answer(suffix, pack, qc_answer=args.qc_answer, with_action=args.break_mode == "undercount")
        if mode != "offline":
            counters["injected" if broken else "stubbed_other"] += 1
        if recovery:
            recovery_requests.append({"node": key, "path": request.url.path, "host": request.url.host,
                                      **profile_request_facts(body, repair_suffix)})
        request_log.append({"route": "worker", "node": key, "recovery": recovery, "broken": broken, "real": False,
                            "stream": stream, "path": request.url.path, "pack_numbers": [f.get("number") for f in pack],
                            "matched": any(str(f["uuid"]).lower() in text.lower() for f in frames)})
        if stream:
            content = sse_chat_body(answer, rid, style=args.sse_style, status_completed=args.status_completed, finish=finish)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=content, request=request)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=chat_json_body(answer, rid, finish=finish), request=request)

    def refuse(request, kind: str, why: str, *, host_rule: bool):
        counters["refused"] += 1
        if host_rule:
            refused_hosts.append(str(request.url.host))
        elif kind == "dry":
            counters["refused_dry"] += 1
        else:
            counters["refused_cap"] += 1
        request_log.append({"route": "refused", "kind": kind, "host_rule": host_rule})
        raise RuntimeError(why)

    async def send_live(client, request, **kwargs):
        host, path = request.url.host, request.url.path
        if is_loopback(host):
            if request.method == "POST" and (is_chat_completions(path) or is_kie_responses(path)):
                refuse(request, "loopback_model_route", "no local model route in live/dry mode", host_rule=True)
            return await original_send(client, request, **kwargs)
        if request.method != "POST" or not is_chat_completions(path) or str(host).lower() not in hosts:
            refuse(request, "foreign", f"refused: {request.method} {host}{path} is not an allowed provider request",
                   host_rule=True)
        body = json.loads(request.content.decode("utf-8") or "{}")
        text = request_text(body)
        is_decision = decision_hook.get() is not None
        attempt = observation._attempt.get()
        key = getattr(attempt, "node_key", None)
        recovery = bool(attempt and attempt.session.info.get("recovery_attempt_id") == attempt.attempt_id)
        suffix = node_suffix(key)
        if not is_decision:
            if suffix is None:
                refuse(request, "outside_group", f"worker request outside the group (node_key={key!r})", host_rule=True)
            if not recovery:
                return local_worker(request, body, text, key, suffix, False)
        kind = "decision" if is_decision else "worker_recovery"
        if mode == "dry":
            refuse(request, "dry", f"dry: provider request refused ({kind})", host_rule=False)
        usd, _zero, _rows = spend_now()
        if usd >= args.max_usd and not aborted:
            aborted.append("cost")
        reason = (aborted and f"cap: aborted, estimate ${usd:.4f} passed ${args.max_usd}") or real_post_refusal(
            host, allowed=hosts, real_posts=counters["real_total"], max_posts=args.max_real_posts,
            spent_usd=usd, max_usd=args.max_usd)
        if reason:
            refuse(request, kind, reason, host_rule=reason.startswith("host:"))
        counters["real_total"] += 1
        counters["real_decision" if is_decision else "real_worker_recovery"] += 1
        if is_decision:
            counters["decision"] += 1
        else:
            counters["worker_total"] += 1
            counters["worker_recovery"] += 1
            counters["by_node"][key] = counters["by_node"].get(key, 0) + 1
            recovery_requests.append({"node": key, "path": path, "host": host, **profile_request_facts(body, repair_suffix)})
        response = await original_send(client, request, **kwargs)
        request_log.append({"route": kind, "node": key, "real": True, "status": response.status_code, "path": path,
                            "stream": bool(body.get("stream"))})
        return response

    async def send(client, request, **kwargs):
        if mode != "offline":
            return await send_live(client, request, **kwargs)
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
        text = request_text(body)
        if decision_hook.get() is not None:
            counters["decision"] += 1
            ctx = decision_context(text)
            if ctx is None:
                raise RuntimeError("decision stub: no context in the request")
            answer = json.dumps(decision_proposal(counters["decision"], ctx, args.recovery), ensure_ascii=False)
            request_log.append({"route": "decision", "turn": counters["decision"], "stream": bool(body.get("stream"))})
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=chat_json_body(answer, f"chatcmpl-{_uuid.uuid4().hex[:12]}"), request=request)
        attempt = observation._attempt.get()
        key = getattr(attempt, "node_key", None)
        suffix = node_suffix(key)
        if suffix is None:
            raise RuntimeError(f"worker stub: request outside the group (node_key={key!r})")
        recovery = bool(attempt and attempt.session.info.get("recovery_attempt_id") == attempt.attempt_id)
        return local_worker(request, body, text, key, suffix, recovery)

    httpx.AsyncClient.send = send
    task = asyncio.create_task(app_main.main())
    result: dict[str, Any] = {"mode": mode, "target": target, "break_mode": args.break_mode, "project_id": pid,
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
            unsupported = [n["node_key"] for n in options["nodes"] if n["node_key"] in node_keys() and not n["supported"]]
            if unsupported:
                raise RuntimeError(f"nodes not supported by the managed launch gate: {unsupported}")
            strategies = ["wait", "human_review", "retry_same"]
            if mode != "offline" and args.recovery == "retry_profile":
                strategies.remove("retry_same")
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
                "node_keys": list(node_keys()), "strategies": strategies,
                "profile_ids": profile_ids, **profile_hash, "request_limit": args.request_limit,
                "rationale": f"{mode} vibecode script_frames_qc recovery verification",
                "idempotency_key": f"vibecode-qc-e2e-{_uuid.uuid4().hex[:8]}"})
            print("launch:", response.status_code, (response.text[:400] if response.status_code >= 300 else "ok"))
            if response.status_code >= 300:
                result["launch_error"] = response.text[:600]
                raise RuntimeError("launch refused: " + response.text[:300])
            execution = response.json()["execution_id"]
            result["execution_id"] = execution
            if args.break_node:
                response = await client.post(base + f"/observations/{execution}/decisions/control", json={
                    "enabled": True, "rationale": f"{mode} decision loop for the script_frames_qc recovery verification"})
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
    if mode == "offline":
        checks = evaluate(result, break_node=args.break_node, break_mode=args.break_mode, qc_answer=args.qc_answer,
                          recovery=args.recovery)
    else:
        usd, zero_rows, rows = spend_now()
        result["spend"] = {"estimated_usd": usd, "rows": rows, "zero_usage_rows": zero_rows, "markup": COST_MARKUP,
                           "aborted_on_cost": bool(aborted)}
        checks = evaluate_live(result, break_node=args.break_node, break_mode=args.break_mode, qc_answer=args.qc_answer,
                               recovery=args.recovery, hosts=hosts, max_real_posts=args.max_real_posts,
                               max_usd=args.max_usd)
    ok, failed = verdict(checks)
    result["warnings"] = sorted({i["type"] for i in (result.get("diagnoses") or {}).get("incidents", [])
                                 if i.get("node_key") != target and i["type"] not in {"runtime_wait"}})
    result["checks"], result["ok"], result["failed"] = checks, ok, failed
    slim = {k: v for k, v in result.items() if k != "events"}
    slim["event_kinds"] = sorted({e["kind"] for e in result["events"]})
    dry_ok = mode == "dry" and counters["real_total"] == 0 and counters["injected"] >= 1 and counters["refused_dry"] >= 1
    if mode != "offline":
        recs = (result.get("decisions") or {}).get("records") or []
        recoveries = (result.get("actions") or {}).get("recoveries") or []
        slim["summary"] = {
            "mode": mode, "route": "relay" if relay_base(live_env) else "direct",
            "posts": {k: counters[k] for k in ("injected", "real_total", "real_decision", "real_worker_recovery",
                                               "refused", "refused_cap", "refused_dry", "stubbed_other")},
            "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd},
            "estimated_spend_usd": result["spend"]["estimated_usd"],
            "decision_record": slim_record(recs[0]) if recs else None,
            "recovery_record": slim_record(recoveries[0]) if recoveries else None,
            "verification": recoveries[0].get("verification_result") if recoveries else None,
            "per_node_done": {k: result["node_status"].get(k) == "done" for k in node_keys()},
            "verdict": "PASS" if ok and mode == "live" else "NOT PASSED",
            "dry_check_ok": dry_ok if mode == "dry" else None,
            "failed_checks": failed}
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
    if mode == "offline":
        print("OK" if ok else "NOT OK: " + ", ".join(failed))
        return 0 if ok else 1
    summary = slim["summary"]
    print("posts:", summary["posts"], "| est. spend $%.4f" % summary["estimated_spend_usd"],
          "| caps:", summary["caps"], "| per node:", summary["per_node_done"])
    print("PASS" if summary["verdict"] == "PASS" else "NOT PASSED" + (": " + ", ".join(failed) if failed else ""))
    if mode == "dry":
        print("DRY CHECK OK (nothing sent)" if dry_ok else "DRY CHECK FAILED")
        return 0 if dry_ok else 1
    return 0 if ok else 1


async def _poll(client, base, execution, args, project_db, counters, result) -> None:
    deadline = time.monotonic() + args.timeout_s
    prefix = base + f"/observations/{execution}"
    target = target_key(args.break_node)
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
        chain_done = all(nodes.get(k) == "done" for k in node_keys())
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
