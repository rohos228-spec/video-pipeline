"""Media node `images` (Outsee) through the PRODUCTION route: worker -> observation -> diagnosis -> Decision ->
supervised recovery -> verification. At most ONE real paid provider request.

Sibling of scripts/e2e_vibecode_text_nodes.py (same idea, same helpers imported from
scripts/e2e_vibecode_script_frames_qc.py, none of them edited) and of scripts/live_media_probe.py (the paid SEAM
probe). The probe proves that one request leaves once; this stand proves the machinery AROUND it for a whole node.

Why `images` via Outsee (docs/MEDIA_NODES_READINESS.md, docs/NODE_COVERAGE.md):
  * one Outsee image is the cheapest paid unit of the media nodes (about $0.03); Kie is unusable (negative
    balance), Seedance is forbidden, ElevenLabs nodes (`audio`, `sfx_gen`) resume only from a FINISHED local file
    and have no request-key record of the paid request itself;
  * `images` is the one media node where the recovery-only semantic has a durable record of the paid request
    BEFORE and AFTER the call (outsee_idempotency: intent -> result with path, size, sha256 of the file; the
    provider-task ledger carries the same intent): the supervised recovery is `resume_local` of that recorded
    result, never a paid resend, and it can be exercised with exactly one real submit;
  * the whole native step (generate_images.run) is the real executor, so the real worker, the native retry
    counter, the independent media verifier, the diagnosis and the Decision supervisor all take part.
Outsee has NO task id on the client side. "The recorded provider task" here is the recorded request-key result
(file + sha256 + raw_url), not an id that could be polled; the stand says so in its report.

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no .env: a fake Outsee API server on 127.0.0.1 plays the provider (submit / status /
          download), the Decision model is a local stub, every non-loopback request is refused and counted.
  --live  PAID, capped: the same flow, but the ONE paid submit (POST /api/v1/images/generate) and its free status /
          download requests go to the real Outsee. Needs --env-file; only OUTSEE_API_KEY, OUTSEE_API_BASE_URL,
          OUTSEE_DEFAULT_IMAGE_MODEL (and with --real-decision the Vibecode names) are read from it, nothing else,
          nothing is printed. Hard caps: --max-real-posts (default 1), --max-usd (default 0.25; refused when
          max-real-posts x $0.10 conservative price exceeds it). Any other request is refused and counted.
          NOT RUN in live mode when written.

The flow (both modes):
  1. seed a synthetic project (one frame with an image prompt, one `images` node, animation prompt present so the
     side task makes no model request); guard probes against the loopback fake: kill switch, disk guard, spend cap
     each refuse a paid submit BEFORE the first byte (no POST reaches the fake);
  2. POST /observations (observe-only, as the owner does), POST /steps/img/run (the UI start) -> the real worker runs
     generate_images.run: ONE paid submit, the result is recorded (intent -> result) and the file is on disk;
  3. the OUTCOME LOSS is simulated locally and for free: the tail of the step after the paid result is made to fail
     (HITL card send and post-step validation raise) on every native attempt, so the result is never taken by the
     step, the native retry counter runs out and the project pauses. Native retries must NOT submit again;
  4. diagnosis types a media incident, actions are enabled, the kill switch is probed against the recovery, the
     Decision loop is enabled: Decision (stub or --real-decision on Vibecode) -> retry_same -> resume_local of the
     recorded result inside media_spend.resume_only() -> independent verification (file re-read and decoded, no new
     provider task, request-key log unchanged);
  5. after the run: a repeated identical request is answered from the record (no POST), an other request under
     resume-only is refused, a second paid submit is refused by the application's own spend cap.

What is real: the whole application in-process (FastAPI, worker, observer, diagnosis, Decision supervisor, recovery,
verification), the real native executor generate_images.run, outsee_retry, outsee_http, media_spend, outsee_guard,
outsee_idempotency, spend_guard (kill switch, disk guard, caps), media_local_resume. In --live also the Outsee
submit, status and download (one image), optionally the Decision model.
What is NOT real: the seeded project, the loss of the outcome (injected), the provider in --dry, the Decision model
unless --real-decision, the picture quality (never judged).
The application's own estimate of one Outsee submit is $0.30 (spend_guard.MEDIA_SUBMIT_ESTIMATE_USD, conservative;
the real price is about $0.03). The stand sets the application's caps to 1.5 estimates, so exactly one submit is
admitted and the next one is refused by the application itself; --max-usd is the stand's own authorisation.

Usage (repository root, project venv):
    python scripts/e2e_media_production_route.py --work-dir <empty dir outside the repository> --dry
    python scripts/e2e_media_production_route.py --work-dir <dir> --live --env-file <env file>
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import threading
import time
import uuid as _uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_media_route", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


qc = _load_sibling("e2e_vibecode_script_frames_qc")

PORT = 18768
FAKE_PORT = 18769
NODE = "images"
STEP_CODE = "img"
TARGET = "media-images-A"
GROUP_ID = "media-g1"
FRAME_KEY = "frame_001"
FAKE_KEY = "fake-outsee-dry-key"
GENERATE_PATH = "/api/v1/images/generate"
STATUS_PREFIX = "/api/v1/generations/"
BALANCE_PATH = "/api/v1/balance"
OUTSEE_GET_PATHS = (STATUS_PREFIX, BALANCE_PATH)

OUTSEE_ENV_NAMES = ("OUTSEE_API_KEY", "OUTSEE_API_BASE_URL", "OUTSEE_DEFAULT_IMAGE_MODEL")
DECISION_ENV_NAMES = tuple(qc.LIVE_ENV_NAMES)
SECRET_NAMES = ("OUTSEE_API_KEY", "VIBECODE_API_KEY", "GPT_RELAY_TOKEN", "GPT_PROXY_URL")
DEFAULT_OUTSEE_BASE = "https://outsee.io"
IMAGE_GENERATORS = ("gpt_image_2_vip", "nano_banana_2")
PRICE_HINT_USD = 0.10
DEFAULT_MAX_REAL_POSTS = 1
DEFAULT_MAX_USD = 0.25
DEFAULT_MAX_DECISION_POSTS = 3
GATE_CAP_FACTOR = 1.5
TERMINAL_DECISION = set(qc.TERMINAL_DECISION)

IMAGE_PROMPT = ("A calm still life photograph: a wooden desk by a large window, a blue paper folder, a ceramic mug and an "
                "open notebook, soft morning light, neutral colours, shallow depth of field, no people, no text, no logos.")
ANIMATION_PROMPT = "Slow push-in towards the blue folder on the desk, soft morning light, no voice."
VOICEOVER = "Папка лежит на столе у окна, рядом чашка и раскрытый блокнот."

FAULT_HITL = "simulated outcome loss: the HITL card of the new image could not be sent"
FAULT_TAIL = "simulated outcome loss: the post-step validation of the images step crashed"



def load_env_whitelist(path: str, names: tuple[str, ...]) -> dict[str, str]:
    """Only `names` from an env file; everything else is ignored and nothing is printed."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in names:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def outsee_base(env: dict[str, str]) -> str:
    return (env.get("OUTSEE_API_BASE_URL") or DEFAULT_OUTSEE_BASE).strip().rstrip("/")


def outsee_host(env: dict[str, str]) -> str:
    return (urlsplit(outsee_base(env)).hostname or "").lower()


def base_problem(env: dict[str, str]) -> str | None:
    """Why the configured Outsee base is not acceptable for a paid run (None = fine)."""
    base = outsee_base(env)
    parts = urlsplit(base)
    if parts.scheme != "https":
        return f"OUTSEE_API_BASE_URL must be https (got scheme {parts.scheme!r})"
    host = (parts.hostname or "").lower()
    if not host or qc.is_loopback(host):
        return "OUTSEE_API_BASE_URL must be a real host in --live (loopback is the fake of --dry)"
    if parts.username or parts.password or parts.query or parts.fragment:
        return "OUTSEE_API_BASE_URL must be a plain base url"
    return None


def worst_case_usd(max_real_posts: int) -> float:
    return round(max_real_posts * PRICE_HINT_USD, 6)


def authorisation_problem(max_real_posts: int, max_usd: float) -> str | None:
    """The stand refuses a run whose conservative worst case exceeds what it was authorised to spend."""
    if max_real_posts < 1 or max_usd <= 0:
        return "--max-real-posts must be >= 1 and --max-usd > 0"
    if worst_case_usd(max_real_posts) > max_usd + 1e-9:
        return (f"{max_real_posts} real submit(s) x ${PRICE_HINT_USD} (conservative price) = "
                f"${worst_case_usd(max_real_posts)} exceeds --max-usd {max_usd}")
    return None


def gate_cap_usd(estimate_usd: float) -> float:
    """Application caps (run / project / daily): one submit estimate passes, the second is refused."""
    return round(float(estimate_usd) * GATE_CAP_FACTOR, 2)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hosts_in_text(text: str) -> set[str]:
    """https hosts named in a provider answer (the download hosts Outsee itself hands us)."""
    import re
    return {(urlsplit(u).hostname or "").lower() for u in re.findall(r"https://[^\s\"'\\<>]+", text or "")} - {""}


@dataclass
class Judgement:
    action: str
    kind: str
    reason: str = ""


@dataclass
class Gate:
    """Every outgoing request is judged here (pure bookkeeping, no I/O): the send hook obeys it.

    `phase` is "main" while the production flow runs and "probe" while guard probes run. A POST to the fake
    submit path during a probe is a guard failure (counted in `probe_posts`), never a real request."""
    mode: str
    max_paid_posts: int = DEFAULT_MAX_REAL_POSTS
    outsee_host: str = ""
    fake_port: int = FAKE_PORT
    real_decision: bool = False
    decision_hosts: frozenset = frozenset()
    max_decision_posts: int = DEFAULT_MAX_DECISION_POSTS
    phase: str = "probe"
    paid_posts: int = 0
    probe_posts: int = 0
    decision_posts: int = 0
    refused_cap: int = 0
    unexpected_model: int = 0
    learned_hosts: set = field(default_factory=set)
    refused: list = field(default_factory=list)

    def _refuse(self, kind: str, reason: str, host: str | None, *, count_host: bool = True) -> Judgement:
        if count_host:
            self.refused.append({"host": str(host), "kind": kind, "reason": reason[:160]})
        return Judgement("refuse", kind, reason)

    def learn(self, text: str) -> None:
        if self.mode == "live":
            self.learned_hosts |= {h for h in hosts_in_text(text) if h and not qc.is_loopback(h)}

    def judge(self, method: str, host: str | None, port: int | None, path: str, *, is_decision: bool) -> Judgement:
        method, host_l = method.upper(), (host or "").strip("[]").lower()
        is_model = method == "POST" and (qc.is_chat_completions(path) or qc.is_kie_responses(path))
        if qc.is_loopback(host_l):
            if port == self.fake_port and method == "POST" and path == GENERATE_PATH:
                if self.phase != "main":
                    self.probe_posts += 1
                    return Judgement("pass", "fake_submit_during_probe", "a guard failed to stop the probe submit")
                if self.mode == "live":
                    return self._refuse("fake_submit_in_live", "the main flow must reach the real host", host_l)
                if self.paid_posts >= self.max_paid_posts:
                    self.refused_cap += 1
                    return self._refuse("paid_cap", f"paid post cap reached ({self.max_paid_posts})", host_l)
                self.paid_posts += 1
                return Judgement("pass", "paid_post_fake")
            if is_model:
                if is_decision and not qc.is_kie_responses(path):
                    return Judgement("stub_decision", "decision_stub")
                self.unexpected_model += 1
                return self._refuse("unexpected_model", "no model request is expected outside the Decision", host_l)
            return Judgement("pass", "local")
        if self.mode != "live":
            return self._refuse("dry_foreign", f"dry: non-loopback request refused ({method} {host_l})", host_l)
        if host_l == self.outsee_host:
            if method == "POST" and path == GENERATE_PATH:
                if self.phase != "main":
                    return self._refuse("paid_in_probe", "no real submit during guard probes", host_l)
                if self.paid_posts >= self.max_paid_posts:
                    self.refused_cap += 1
                    return self._refuse("paid_cap", f"paid post cap reached ({self.max_paid_posts})", host_l)
                self.paid_posts += 1
                return Judgement("pass", "paid_post_real")
            if method == "GET" and any(path.startswith(p) or path == p for p in OUTSEE_GET_PATHS):
                return Judgement("pass", "outsee_get")
            return self._refuse("outsee_other", f"refused: {method} {host_l}{path} is not on the allow list", host_l)
        if method == "GET" and host_l in self.learned_hosts:
            return Judgement("pass", "download_get")
        if (self.real_decision and is_model and is_decision and host_l in self.decision_hosts
                and not qc.is_kie_responses(path)):
            if self.decision_posts >= self.max_decision_posts:
                self.refused_cap += 1
                return self._refuse("decision_cap", f"decision post cap reached ({self.max_decision_posts})", host_l)
            self.decision_posts += 1
            return Judgement("real_decision", "decision_real")
        return self._refuse("foreign", f"refused: {method} {host_l}{path} is not an allowed request", host_l)


def numeric_fields(payload: Any) -> dict[str, float]:
    """Top-level numeric fields of a balance answer (the unit is not documented, so only raw numbers)."""
    if not isinstance(payload, dict):
        return {}
    return {str(k): float(v) for k, v in payload.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}


def balance_delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {k: round(before[k] - after[k], 6) for k in before if k in after and before[k] != after[k]}



def make_png(side: int = 512) -> bytes:
    """A real, decodable PNG above the native scene threshold (200 KB): noise does not compress."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.frombytes("RGB", (side, side), os.urandom(side * side * 3)).save(buf, "PNG")
    return buf.getvalue()


class FakeOutsee:
    """The three Outsee endpoints the application uses for an image, on 127.0.0.1. Counts what it receives."""

    def __init__(self, port: int, key: str = FAKE_KEY):
        self.port, self.key = port, key
        self.posts = self.polls = self.downloads = self.balances = self.auth_failures = 0
        self.bodies: list[dict] = []
        self._png: bytes | None = None
        self._lock = threading.Lock()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a, **k):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json"):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, payload: dict):
                self._send(code, json.dumps(payload).encode("utf-8"))

            def _auth(self) -> bool:
                if self.headers.get("Authorization") == f"Bearer {outer.key}":
                    return True
                with outer._lock:
                    outer.auth_failures += 1
                self._json(401, {"error": "bad key"})
                return False

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if self.path != GENERATE_PATH:
                    return self._json(404, {"error": "not found"})
                if not self._auth():
                    return
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    return self._json(400, {"error": "bad json"})
                with outer._lock:
                    outer.posts += 1
                    n = outer.posts
                    outer.bodies.append({"model": body.get("model"), "aspect_ratio": body.get("aspect_ratio"),
                                         "prompt_chars": len(str(body.get("prompt") or ""))})
                self._json(200, {"id": f"fake-{n:04d}", "status": "queued"})

            def do_GET(self):
                if self.path.startswith(STATUS_PREFIX):
                    if not self._auth():
                        return
                    gid = self.path[len(STATUS_PREFIX):].split("?")[0]
                    with outer._lock:
                        outer.polls += 1
                    return self._json(200, {"id": gid, "status": "completed",
                                            "result_urls": [f"http://127.0.0.1:{outer.port}/files/{gid}.png"]})
                if self.path == BALANCE_PATH:
                    if not self._auth():
                        return
                    with outer._lock:
                        outer.balances += 1
                    return self._json(200, {"balance": 10.0})
                if self.path.startswith("/files/"):
                    with outer._lock:
                        outer.downloads += 1
                        if outer._png is None:
                            outer._png = make_png()
                        data = outer._png
                    return self._send(200, data, "image/png")
                return self._json(404, {"error": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fake-outsee")

    def start(self) -> "FakeOutsee":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def counts(self) -> dict:
        return {"posts": self.posts, "polls": self.polls, "downloads": self.downloads, "auth_failures": self.auth_failures}



def env_overrides(data: Path, port: int, *, fake_base: str, gate_cap: float, dry: bool) -> dict[str, str]:
    """Environment of the in-process app: the sibling's synthetic loopback one plus the media switches."""
    out = qc.env_overrides(data, port)
    out.update({
        "OUTSEE_API_KEY": FAKE_KEY, "OUTSEE_API_BASE_URL": fake_base, "IMAGE_PROVIDER": "outsee",
        "OUTSEE_DEFAULT_IMAGE_MODEL": "gpt-image-2",
        "SPEND_DAILY_CAP_USD": str(gate_cap), "SPEND_PROJECT_CAP_USD": str(gate_cap), "SPEND_RUN_CAP_USD": str(gate_cap),
        "MEDIA_SPEND_RECORDING": "true", "KIE_API_KEY": "", "ELEVENLABS_API_KEY": "",
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
    })
    if dry:
        out.update({"DISK_GUARD_LOW_FREE_GB": "0.001", "DISK_GUARD_CRITICAL_FREE_GB": "0.0005"})
    return out


def reality_report(mode: str, snap: dict) -> dict:
    """What is real and what is not in THIS run, from the counters (never 'verified live' by itself)."""
    live = mode == "live"
    counters = snap.get("counters", {})
    real_submits = counters.get("paid_posts", 0) if live else 0
    real_decisions = counters.get("decision_posts", 0) if live else 0
    return {
        "real": [x for x in (
            "application, worker, observer, diagnosis, Decision supervisor, recovery and verification in-process",
            "native executor generate_images.run, outsee_retry, outsee_http, media_spend, outsee_guard, outsee_idempotency, "
            "spend_guard (kill switch, disk guard, caps), media_local_resume",
            f"{real_submits} real Outsee submit(s) with its status polls and one image download" if real_submits else None,
            f"{real_decisions} real Decision request(s) to Vibecode" if real_decisions else None) if x],
        "not_real": [x for x in (
            "seeded project: synthetic, built by this script (one frame, one images node)",
            "the loss of the outcome: injected locally (HITL send and post-step validation raise on every native attempt)",
            None if live else "the provider: a fake Outsee API on 127.0.0.1 (nothing left the machine, nothing was paid)",
            "Decision model: local stub (nothing was sent to a provider)" if not (live and counters.get("decision_posts", 0))
            else None,
            "Outsee has no task id on the client side: the recovery reads the RECORDED REQUEST-KEY RESULT, "
            "no provider task is polled",
            "picture quality and the meaning of the result: not judged",
            "the supervisor does not start the node: the node stays failed, a human continues it") if x],
    }


def reality_not_proven(mode: str) -> list[str]:
    out = ["the price of a real Outsee image is not calibrated by the application (cost_unknown rows)"]
    if mode != "live":
        out.insert(0, "anything about the real Outsee: this is a dry run on a fake provider, NOT a live proof")
    return out



def check(name: str, ok: bool, detail: Any = None, *, needs: str | None = None) -> dict:
    return qc.check(name, ok, detail, needs=needs)


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    return qc.verdict(checks)


def _blocked(probes: dict, name: str, code: str) -> bool:
    p = probes.get(name) or {}
    return bool(p.get("blocked")) and p.get("code") == code and p.get("posts_delta") == 0


def evaluate(snap: dict, *, mode: str, target: str = TARGET, max_real_posts: int = DEFAULT_MAX_REAL_POSTS) -> list[dict]:
    """Design assertions over a collected run snapshot (dry and live share them)."""
    counters = snap.get("counters", {})
    probes = snap.get("probes", {})
    incidents = [i for i in (snap.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == target]
    media_inc = [i for i in incidents if i.get("type") == "media_failure"]
    records = (snap.get("decisions") or {}).get("records") or []
    recoveries = (snap.get("actions") or {}).get("recoveries") or []
    ticket = recoveries[0] if recoveries else {}
    precond = ticket.get("preconditions") or {}
    ver = ticket.get("verification_result") or {}
    idem = snap.get("idem_rows") or []
    events = [r.get("event") for r in idem if r.get("key") == FRAME_KEY]
    results = [r for r in idem if r.get("key") == FRAME_KEY and r.get("event") == "result"]
    recorded = snap.get("recorded_file") or {}
    spend = [r for r in (snap.get("spend_rows") or []) if r.get("provider") == "outsee"]
    ledger = snap.get("ledger_rows") or []
    node_status = (snap.get("node_status") or {}).get(target)
    attempts = int(snap.get("node_attempts") or 0)
    stepf = snap.get("step_failure") or {}
    ctxs = snap.get("decision_contexts") or []
    media_ctx = next((c.get("media_recovery") for c in ctxs if c.get("media_recovery")), None) or {}
    verified_file = (ver.get("files") or [{}])[0]
    paid = counters.get("paid_posts", 0)
    out = [
        check("exactly_one_paid_submit_at_most", paid == 1 and paid <= max_real_posts,
              {"paid_posts": paid, "max_real_posts": max_real_posts, "fake_server_posts": counters.get("fake_posts")}),
        check("application_records_agree_on_one_submit",
              events.count("intent") == 1 and sum(1 for r in ledger if r.get("api") == "outsee" and r.get("key") == FRAME_KEY
                                                   and r.get("event") == "intent") == 1,
              {"idempotency_intents": events.count("intent"),
               "ledger_intents": sum(1 for r in ledger if r.get("event") == "intent")}),
        check("outcome_loss_injected_and_native_retries_ran",
              counters.get("injected_hitl", 0) >= 1 and counters.get("injected_tail", 0) >= 1 and attempts >= 3,
              {"hitl": counters.get("injected_hitl"), "tail": counters.get("injected_tail"), "node_attempts": attempts}),
        check("no_second_submit_after_the_loss", paid == 1 and attempts >= 3 and counters.get("refused_cap", 0) == 0,
              {"paid_posts": paid, "node_attempts": attempts, "refused_cap": counters.get("refused_cap")}),
        check("native_retries_exhausted_and_project_paused", bool(stepf.get("abandoned_at")) and node_status == "failed",
              {"step_failure": {k: stepf.get(k) for k in ("total_fails", "abandoned_at", "abandoned_threshold")},
               "node": node_status}),
        check("result_recorded_before_the_loss",
              events[:2] == ["intent", "result"] and len(results) == 1 and recorded.get("exists") is True
              and recorded.get("sha256") == results[0].get("file_sha256") and "consumed" not in events,
              {"events": events, "recorded_file": recorded}),
        check("intent_records_written", len(events) >= 2 and any(
            r.get("api") == "outsee" and r.get("key") == FRAME_KEY and r.get("event") in ("intent", "intent_done")
            for r in ledger), {"idempotency_events": events, "ledger": [(r.get("event"), r.get("status")) for r in ledger][:6]}),
        check("spend_ledger_written", len(spend) >= 1 and any("image" in str(r.get("note")) for r in spend),
              [(r.get("kind"), r.get("usd"), str(r.get("note"))[:50]) for r in spend][:4]),
        check("media_incident_typed_by_diagnosis", bool(media_inc) and any(i.get("media") is True for i in media_inc),
              [(i.get("type"), i.get("status")) for i in incidents][-6:]),
        check("decision_sees_resume_local_of_the_recorded_result",
              media_ctx.get("operation") == "resume_local" and media_ctx.get("submits_new_paid_request") is False
              and any(k.get("key") == FRAME_KEY and k.get("mode") == "resume_local" for k in media_ctx.get("keys") or []),
              media_ctx, needs="decision_v1._media_info and media_local_resume.assess"),
        check("decision_done_with_retry_same", bool(records) and records[0].get("status") == "done"
              and precond.get("action_type") == "retry_same",
              {"decision": [(r.get("status"), r.get("model_steps")) for r in records], "action": precond.get("action_type")}),
        check("recovery_ticket_is_resume_local_and_verified",
              ticket.get("status") == "verified" and ver.get("verified") is True
              and precond.get("media_operation") == "resume_local" and precond.get("submits_new_paid_request") is False,
              {"status": ticket.get("status"), "operation": precond.get("media_operation"), "problems": ver.get("problems")}),
        check("resume_read_the_recorded_result",
              verified_file.get("status") == "passed" and verified_file.get("key") == FRAME_KEY
              and str(verified_file.get("path") or "").replace("\\", "/") == snap.get("recorded_relative_path") and recorded.get("exists") is True,
              {"verified_file": {k: verified_file.get(k) for k in ("key", "path", "status")},
               "recorded_relative_path": snap.get("recorded_relative_path")}),
        check("recovery_made_zero_new_paid_requests",
              ver.get("new_paid_requests") == 0 and ver.get("new_provider_task_ids") == [] and paid == 1
              and ver.get("node_started") is False and ver.get("semantic_quality_verified") is False,
              {"new_paid_requests": ver.get("new_paid_requests"), "new_task_ids": ver.get("new_provider_task_ids"),
               "node_started": ver.get("node_started")}),
        check("supervisor_did_not_start_the_node", node_status == "failed", node_status),
        check("incident_closed_by_the_verified_recovery", any(
            i.get("incident_id") == ticket.get("incident_id") and i.get("status") == "resolved_confirmed"
            and (i.get("resolution") or {}).get("kind") == "supervisor_recovery_verified" for i in incidents),
            [(i.get("type"), i.get("status"), (i.get("resolution") or {}).get("kind")) for i in incidents
             if i.get("incident_id") == ticket.get("incident_id")]),
        check("kill_switch_stops_the_paid_seam", _blocked(probes, "kill_switch", "kill_switch_active"), probes.get("kill_switch")),
        check("kill_switch_stops_the_recovery", (probes.get("kill_switch_recovery") or {}).get("refused") is True
              and (probes.get("kill_switch_recovery") or {}).get("tickets_after") == 0, probes.get("kill_switch_recovery")),
        check("disk_guard_stops_the_paid_seam", _blocked(probes, "disk_guard", "disk_guard_blocked"), probes.get("disk_guard")),
        check("spend_cap_stops_the_paid_seam", _blocked(probes, "spend_cap", "spend_cap_daily"), probes.get("spend_cap")),
        check("spend_cap_stops_a_second_submit", _blocked(probes, "second_submit_cap", "spend_cap_daily"),
              probes.get("second_submit_cap")),
        check("identical_request_is_answered_from_the_record_without_a_post",
              (probes.get("identical_request") or {}).get("adopted") is True
              and (probes.get("identical_request") or {}).get("posts_delta") == 0, probes.get("identical_request")),
        check("other_request_under_resume_only_is_refused", (probes.get("resume_only_other") or {}).get("refused") is True
              and (probes.get("resume_only_other") or {}).get("posts_delta") == 0, probes.get("resume_only_other")),
        check("no_probe_reached_a_provider", counters.get("probe_posts", 0) == 0, counters.get("probe_posts")),
        check("no_unexpected_model_or_foreign_request", counters.get("unexpected_model", 0) == 0
              and not snap.get("refused_hosts"), {"unexpected_model": counters.get("unexpected_model"),
                                                  "refused": snap.get("refused_hosts")}),
        check("hitl_none_pending_and_no_second_ticket", len(recoveries) == 1 and not snap.get("hitl_pending"),
              {"tickets": len(recoveries), "hitl": snap.get("hitl_pending")}),
    ]
    if mode == "live":
        out += [
            check("caps_not_exceeded", paid <= max_real_posts and counters.get("decision_posts", 0)
                  <= snap.get("max_decision_posts", DEFAULT_MAX_DECISION_POSTS),
                  {"paid_posts": paid, "max_real_posts": max_real_posts, "decision_posts": counters.get("decision_posts")}),
            check("real_submit_sent", paid == 1, paid),
        ]
    else:
        out.append(check("provider_independently_saw_one_submit",
                         counters.get("fake_posts") == 1 and counters.get("fake_auth_failures") == 0,
                         {"fake_posts": counters.get("fake_posts"), "auth_failures": counters.get("fake_auth_failures")}))
        out.append(check("dry_nothing_left_the_machine", counters.get("refused_foreign", 0) == 0
                         and not snap.get("refused_hosts") and paid == 1, {"refused": snap.get("refused_hosts")}))
    return out


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return "PASS (all checks, live: one real Outsee submit; machinery proven end to end on the real provider)"
    return "DRY OK (fake provider and stub Decision: machinery proven offline, NOT a live proof)"



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="FREE: fake Outsee on 127.0.0.1 + Decision stub, nothing leaves the machine")
    p.add_argument("--live", action="store_true", help="PAID, capped: ONE real Outsee image submit (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(OUTSEE_ENV_NAMES) + " (and the Vibecode names with "
                   "--real-decision) are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS,
                   help="hard cap of real paid Outsee submits (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help=f"authorised spend; refused when max-real-posts x ${PRICE_HINT_USD} exceeds it (live)")
    p.add_argument("--real-decision", action="store_true",
                   help="live: the Decision model is the real Vibecode (extra small requests, capped separately)")
    p.add_argument("--max-decision-posts", type=int, default=DEFAULT_MAX_DECISION_POSTS)
    p.add_argument("--image-generator", choices=IMAGE_GENERATORS, default="gpt_image_2_vip",
                   help="Outsee image generator id of the project (the slug actually sent is printed)")
    p.add_argument("--port", type=int, default=PORT)
    p.add_argument("--fake-port", type=int, default=FAKE_PORT)
    p.add_argument("--request-limit", type=int, default=60)
    p.add_argument("--timeout-s", type=int, default=420)
    p.add_argument("--settle-s", type=float, default=4.0)
    p.add_argument("--sleep-minutes", type=float, default=0.01, help="native step-failure sleep (patched for speed)")
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.real_decision and not args.live:
        p.error("--real-decision is a --live option")
    if args.max_decision_posts < 1:
        p.error("--max-decision-posts must be >= 1")
    problem = authorisation_problem(args.max_real_posts, args.max_usd)
    if problem:
        p.error(problem)
    if args.live and args.max_real_posts != 1:
        p.error("--live allows exactly one real paid submit (--max-real-posts 1)")
    return args


def _sqlite_ro(path: Path):
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def _rows(state_db: Path, sql: str, params: tuple = ()) -> list[dict]:
    if not state_db.exists():
        return []
    try:
        with _sqlite_ro(state_db) as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute(sql, params).fetchall()]
    except sqlite3.Error:
        return []


def read_records(state_db: Path) -> dict:
    return {
        "idem_rows": _rows(state_db, "SELECT event,key,attempt_id,request_sha,model,file_path,file_size,file_sha256,raw_url,note "
                                     "FROM outsee_idempotency ORDER BY id"),
        "ledger_rows": _rows(state_db, "SELECT api,key,event,status,provider,note FROM provider_task_ledger ORDER BY id"),
        "spend_rows": _rows(state_db, "SELECT provider,kind,usd,source,note,project_id,run_id FROM spend_ledger ORDER BY id"),
    }


async def _seed(args: argparse.Namespace) -> tuple[int, str]:
    """Workflow + Project + WorkflowRun + ONE pending `images` node + ONE frame with its image prompt."""
    from app.db import session_scope
    from app.models import Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus

    node = {"id": TARGET, "type": NODE, "position": {"x": 100.0, "y": 100.0},
            "data": {"label": "Native images", "groupId": GROUP_ID}}
    nodes, edges = [node], []
    slug = f"media-images-{_uuid.uuid4().hex[:6]}"
    from app.web.settings_default import seed_default_workflow
    async with session_scope() as session:
        await seed_default_workflow(session)
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, "img_streams": 1}
        project = Project(slug=slug, topic="media production route e2e", status=ProjectStatus.image_prompts_ready,
                          auto_mode=False, general_plan=VOICEOVER, script_text=VOICEOVER, meta=meta,
                          image_generator=args.image_generator, aspect_ratio="9_16", image_resolution="2k")
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=TARGET, node_type=NODE, status=NodeRunStatus.pending))
        session.add(Frame(project_id=project.id, number=1, uuid=("01" * 12), sort_key=1.0, voiceover_text=VOICEOVER,
                          image_prompt=IMAGE_PROMPT, animation_prompt=ANIMATION_PROMPT, duration_seconds=3.0, attrs={}))
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(VOICEOVER, encoding="utf-8")
    return pid, slug


def _runtime_config(port: int, request_limit: int, endpoint: str | None) -> dict:
    cfg = qc.runtime_config(port, request_limit, "retry_same", endpoint=endpoint)
    cfg["budget_policy"] = {"policy_id": "media-production-route-e2e", "max_requests": request_limit}
    return cfg


def _node_status(project_db: Path) -> dict[str, str]:
    return qc._node_status(project_db)


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    if mode == "live":
        live_env = load_env_whitelist(args.env_file, OUTSEE_ENV_NAMES + (DECISION_ENV_NAMES if args.real_decision else ()))
        if not live_env.get("OUTSEE_API_KEY"):
            print("env file has no OUTSEE_API_KEY; nothing sent")
            return 3
        problem = base_problem(live_env)
        if problem:
            print("refused: " + problem + "; nothing sent")
            return 3
        if args.real_decision and (not live_env.get("VIBECODE_API_KEY") or live_env["VIBECODE_API_KEY"].startswith("synthetic")):
            print("--real-decision needs VIBECODE_API_KEY in the env file; nothing sent")
            return 3
    secrets = sorted((v for k, v in live_env.items() if k in SECRET_NAMES and len(v) >= 6), key=len, reverse=True)

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    os.chdir(ROOT)
    fake = FakeOutsee(args.fake_port).start()
    decision_endpoint = qc.live_endpoint(live_env) if (mode == "live" and args.real_decision) else None
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(_runtime_config(args.port, args.request_limit, decision_endpoint), indent=2),
                            encoding="utf-8")

    os.environ.update(env_overrides(data, args.port, fake_base=fake.base_url, gate_cap=0.45, dry=(mode == "dry")))
    from app.services import spend_guard
    estimate = float(spend_guard.MEDIA_SUBMIT_ESTIMATE_USD)
    cap = gate_cap_usd(estimate)
    os.environ.update({"SPEND_DAILY_CAP_USD": str(cap), "SPEND_PROJECT_CAP_USD": str(cap), "SPEND_RUN_CAP_USD": str(cap)})
    if mode == "live":
        os.environ["OUTSEE_API_KEY"] = live_env["OUTSEE_API_KEY"]
        os.environ["OUTSEE_API_BASE_URL"] = outsee_base(live_env)
        if live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL"):
            os.environ["OUTSEE_DEFAULT_IMAGE_MODEL"] = live_env["OUTSEE_DEFAULT_IMAGE_MODEL"]
        if args.real_decision:
            overrides, drop = qc.live_env_overrides({}, live_env)
            for name in drop:
                os.environ.pop(name, None)
            os.environ.update(overrides)
    os.environ["ORCHESTRATOR_RUNTIME_CONFIG"] = str(runtime_path)

    import httpx
    from app.settings import settings
    settings.gpt_relay_token = live_env.get("GPT_RELAY_TOKEN", "") if args.real_decision else ""
    settings.gpt_proxy_url = (live_env.get("GPT_PROXY_URL") or None) if args.real_decision else None
    settings.vibecode_api_key = (live_env.get("VIBECODE_API_KEY") if args.real_decision else None) or qc.SYNTHETIC_KEY
    settings.vibecode_base_url = live_env.get("VIBECODE_BASE_URL", "") if args.real_decision else f"http://127.0.0.1:{args.port}/v1"
    settings.gpt_base_url = live_env.get("GPT_BASE_URL", "") if args.real_decision else f"http://127.0.0.1:{args.port}"
    settings.kie_api_key = ""
    settings.elevenlabs_api_key = ""
    settings.outsee_api_key = live_env["OUTSEE_API_KEY"] if mode == "live" else FAKE_KEY
    real_base = outsee_base(live_env) if mode == "live" else fake.base_url
    settings.outsee_api_base_url = real_base
    settings.image_provider = "outsee"
    settings.outsee_default_image_model = live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL") or "gpt-image-2"
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap

    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if tracker._TRACKER_DB.exists():
        tracker._TRACKER_DB.unlink()
    from app.services import observation, outsee_idempotency, step_failure_policy
    from app.services.decision_transport import current as decision_hook
    step_failure_policy.SLEEP_MINUTES = args.sleep_minutes
    step_failure_policy.XLSX_SHEET_FORMAT_SLEEP_MINUTES = args.sleep_minutes
    from app import main as app_main
    await app_main._init_db()
    pid, slug = await _seed(args)
    project_db = data / "videos" / slug / "project.db"
    state_db = data / "state.db"
    project_root = data / "videos" / slug
    base = f"http://127.0.0.1:{args.port}/api/projects/{pid}"

    gate = Gate(mode=mode, max_paid_posts=args.max_real_posts, outsee_host=outsee_host(live_env) if mode == "live" else "",
                fake_port=args.fake_port, real_decision=args.real_decision,
                decision_hosts=qc.allowed_hosts(live_env) if args.real_decision else frozenset(),
                max_decision_posts=args.max_decision_posts)
    counters: dict[str, Any] = {"injected_hitl": 0, "injected_tail": 0, "decision_stub": 0}
    decision_contexts: list[dict] = []
    seen_prompts: list[dict] = []
    original_send = httpx.AsyncClient.send
    original_make_ctx = outsee_idempotency.make_ctx

    def spy_make_ctx(project_id, out_path, model, prompt, media="image"):
        ctx = original_make_ctx(project_id, out_path, model, prompt, media)
        if ctx.usable and ctx.key == FRAME_KEY:
            seen_prompts.append({"model": model, "prompt": prompt})
        return ctx

    async def send(client, request, **kwargs):
        host, port, path = request.url.host, request.url.port, request.url.path
        is_decision = decision_hook.get() is not None
        judged = gate.judge(request.method, host, port, path, is_decision=is_decision)
        if judged.action == "refuse":
            raise RuntimeError(judged.reason)
        if judged.action == "stub_decision":
            body = json.loads(request.content.decode("utf-8") or "{}")
            text = qc.request_text(body)
            ctx = qc.decision_context(text)
            if ctx is None:
                raise RuntimeError("decision stub: no context in the request")
            counters["decision_stub"] += 1
            decision_contexts.append({"turn": counters["decision_stub"], "incident_type": ctx.get("incident_type"),
                                      "allowed_actions": ctx.get("allowed_actions"),
                                      "media_recovery": ctx.get("media_recovery")})
            answer = json.dumps(qc.decision_proposal(counters["decision_stub"], ctx, "retry_same"), ensure_ascii=False)
            return httpx.Response(200, headers={"content-type": "application/json"},
                                  content=qc.chat_json_body(answer, f"chatcmpl-{_uuid.uuid4().hex[:12]}"), request=request)
        if judged.action == "real_decision":
            body = json.loads(request.content.decode("utf-8") or "{}")
            ctx = qc.decision_context(qc.request_text(body))
            if ctx is not None:
                decision_contexts.append({"turn": gate.decision_posts, "incident_type": ctx.get("incident_type"),
                                          "allowed_actions": ctx.get("allowed_actions"),
                                          "media_recovery": ctx.get("media_recovery")})
        response = await original_send(client, request, **kwargs)
        if judged.kind in ("outsee_get", "paid_post_real"):
            try:
                gate.learn(response.text)
            except Exception:
                pass
        return response

    from app.bots import outsee_http
    from app.orchestrator.steps import generate_images
    from app.services import post_step_validate

    async def lost_hitl(*_a, **_k):
        counters["injected_hitl"] += 1
        raise RuntimeError(FAULT_HITL)

    async def broken_tail(*_a, **_k):
        counters["injected_tail"] += 1
        raise RuntimeError(FAULT_TAIL)

    async def no_curl_post(*_a, **_k):
        return None

    original_download_curl = outsee_http._download_via_curl
    original_post_curl = outsee_http._post_generate_via_curl
    original_hitl = generate_images.send_hitl_photo
    original_final = post_step_validate.finalize_or_retry

    async def guarded_download_curl(url, out_path):
        host = (urlsplit(url).hostname or "").lower()
        if qc.is_loopback(host) or (mode == "live" and (host == gate.outsee_host or host in gate.learned_hosts)):
            return await original_download_curl(url, out_path)
        return False

    outsee_idempotency.make_ctx = spy_make_ctx
    generate_images.send_hitl_photo = lost_hitl
    post_step_validate.finalize_or_retry = broken_tail
    outsee_http._post_generate_via_curl = no_curl_post
    outsee_http._download_via_curl = guarded_download_curl
    httpx.AsyncClient.send = send

    result: dict[str, Any] = {"mode": mode, "node": NODE, "target": TARGET, "project_id": pid, "slug": slug,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"},
                              "app_estimate_usd": estimate, "app_caps_usd": cap, "worst_case_usd": worst_case_usd(args.max_real_posts)}
    probes: dict[str, Any] = {}
    advisories: list[str] = []
    task = asyncio.create_task(app_main.main())
    execution = None
    try:
        await qc._wait_api(base)
        slug_sent = outsee_http.studio_id_to_outsee_image_slug(
            __import__("app.generation_options", fromlist=["x"]).IMAGE_GENERATORS_BY_ID[args.image_generator].outsee_slug)
        result["model_slug_sent"] = slug_sent
        from app.services import disk_guard
        free_gb = disk_guard.check(paid=True).free_gb
        result["disk_free_gb"] = round(free_gb, 1) if free_gb is not None else None
        print(f"preflight: mode={mode} model={slug_sent} app_estimate=${estimate} app_caps=${cap} "
              f"worst_case=${result['worst_case_usd']} disk_free_gb={result['disk_free_gb']}", flush=True)
        from app.services import access_guard
        operator = {"x-vp-operator": access_guard.get_operator_token()}
        async with httpx.AsyncClient(timeout=30, trust_env=False, headers=operator,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            await _run_flow(args, mode, client, base, fake, gate, counters, probes, result, advisories, project_db, state_db,
                            project_root, pid, seen_prompts, real_base)
            execution = result.get("execution_id")
    except RuntimeError as exc:
        result["error"] = scrub(str(exc))
        print("STOP:", scrub(str(exc)))
    finally:
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        httpx.AsyncClient.send = original_send
        outsee_idempotency.make_ctx = original_make_ctx
        outsee_http._download_via_curl = original_download_curl
        outsee_http._post_generate_via_curl = original_post_curl
        generate_images.send_hitl_photo = original_hitl
        post_step_validate.finalize_or_retry = original_final
        fake.stop()
    await asyncio.sleep(1)
    return _finish(args, mode, result, gate, counters, probes, advisories, decision_contexts, fake, state_db, project_db,
                   project_root, execution, scrub, work, pid)


async def _run_flow(args, mode, client, base, fake, gate, counters, probes, result, advisories, project_db, state_db,
                    project_root, pid, seen_prompts, real_base) -> None:
    from app.bots import outsee_http
    from app.services import media_spend
    from app.settings import settings
    prefix = base + "/observations"

    settings.outsee_api_base_url = fake.base_url
    gate.phase = "probe"
    await _probe_guards(client, base, fake, gate, probes, project_root, pid)
    settings.outsee_api_base_url = real_base
    gate.phase = "main"

    r = await client.post(prefix, json={"node_keys": [TARGET]})
    print("observation:", r.status_code, flush=True)
    r.raise_for_status()
    execution = r.json()["execution_id"]
    result["execution_id"] = execution
    balance_before = await _balance(outsee_http) if mode == "live" else {}
    r = await client.post(base + f"/steps/{STEP_CODE}/run", params={"node_key": TARGET})
    print("start images:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
    r.raise_for_status()

    await _wait_exhausted(args, project_db, counters, gate, fake)
    result["native_exhausted_at"] = time.strftime("%H:%M:%S")
    balance_after = await _balance(outsee_http) if mode == "live" else {}
    if mode == "live":
        delta = balance_delta(balance_before, balance_after)
        result["balance"] = {"before": balance_before, "after": balance_after, "delta": delta}
        advisories.append(f"Outsee balance fields before/after the run (unit not documented): delta={delta or 'not measurable'}")

    incident = await _wait_incident(client, prefix + f"/{execution}", args.timeout_s)
    result["incident_id"] = incident["incident_id"] if incident else None
    if not incident:
        raise RuntimeError("no current media incident of the images node after the native retries were exhausted")

    await media_spend.drain()
    settings.outsee_api_base_url = fake.base_url
    gate.phase = "probe"
    await _probe_after(fake, gate, probes, project_root, pid, seen_prompts)
    settings.outsee_api_base_url = real_base
    gate.phase = "main"
    result["fake_after_probes"] = fake.counts()

    r = await client.post(prefix + f"/{execution}/actions/enable", json={
        "group_id": GROUP_ID, "node_keys": [TARGET], "rationale": "media production route e2e: supervised recovery",
        "request_limit": min(args.request_limit, 60)})
    print("actions/enable:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
    r.raise_for_status()
    await _probe_kill_switch_recovery(client, base, prefix + f"/{execution}", incident["incident_id"], probes)
    r = await client.post(prefix + f"/{execution}/decisions/control", json={
        "enabled": True, "rationale": f"{mode} decision loop for the media recovery verification"})
    print("decisions/control:", r.status_code, flush=True)
    r.raise_for_status()
    await _poll_recovery(client, prefix + f"/{execution}", args, project_db, counters, gate, result)


async def _balance(outsee_http) -> dict[str, float]:
    try:
        return numeric_fields(await outsee_http.fetch_balance())
    except Exception as exc:
        print("balance not readable:", type(exc).__name__, flush=True)
        return {}


async def _probe_one(name: str, probes: dict, fake, gate, coro_factory, blocked_types: tuple) -> None:
    before = fake.posts + gate.paid_posts + gate.probe_posts
    entry: dict[str, Any] = {"blocked": False, "code": ""}
    try:
        await coro_factory()
    except blocked_types as exc:
        entry.update(blocked=True, code=getattr(exc, "code", type(exc).__name__))
    except Exception as exc:
        entry.update(blocked=False, code=type(exc).__name__, error=str(exc)[:160])
    entry["posts_delta"] = fake.posts + gate.paid_posts + gate.probe_posts - before
    probes[name] = entry


async def _probe_guards(client, base, fake, gate, probes, project_root, pid) -> None:
    from app.bots import outsee_http
    from app.services import spend_guard
    from app.settings import settings
    out = project_root / "probe"
    out.mkdir(parents=True, exist_ok=True)

    def call():
        return outsee_http.generate_image("probe", out / "frame_900_aaaaaaaa.png", model_slug="nano-banana-2",
                                          project_id=None)

    prefix = base + "/observations"
    r = await client.post(prefix + "/kill-switch", json={"active": True, "rationale": "e2e probe: kill switch on"})
    r.raise_for_status()
    await _probe_one("kill_switch", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    r = await client.post(prefix + "/kill-switch", json={"active": False, "rationale": "e2e probe: kill switch off"})
    r.raise_for_status()
    saved = {k: os.environ.get(k) for k in ("DISK_GUARD_LOW_FREE_GB", "DISK_GUARD_CRITICAL_FREE_GB")}
    os.environ["DISK_GUARD_LOW_FREE_GB"] = os.environ["DISK_GUARD_CRITICAL_FREE_GB"] = "1000000000"
    try:
        await _probe_one("disk_guard", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    saved_cap = settings.spend_daily_cap_usd
    settings.spend_daily_cap_usd = 0.01
    try:
        await _probe_one("spend_cap", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    finally:
        settings.spend_daily_cap_usd = saved_cap
    probes["guards_restored"] = {"kill_switch_off": True, "disk_env_restored": True, "cap_restored": saved_cap}


async def _probe_kill_switch_recovery(client, base, prefix, incident_id, probes) -> None:
    """The emergency stop refuses the supervised recovery before any ticket exists (route of the owner's manual retry)."""
    from app.services.actions_recovery import RecoveryAction
    from app.services.observation import get_observer
    from sqlalchemy import func, select

    async def tickets() -> int:
        async with get_observer().master() as s:
            return int(await s.scalar(select(func.count()).select_from(RecoveryAction)) or 0)

    await client.post(base + "/observations/kill-switch", json={"active": True, "rationale": "e2e probe: stop the recovery"})
    r = await client.post(prefix + "/actions/retry", json={
        "incident_id": incident_id, "node_key": TARGET, "rationale": "e2e probe: recovery under the kill switch",
        "idempotency_key": "kill-switch-probe-" + _uuid.uuid4().hex[:8]})
    after = await tickets()
    await client.post(base + "/observations/kill-switch", json={"active": False, "rationale": "e2e probe: resume"})
    probes["kill_switch_recovery"] = {"status": r.status_code, "refused": r.status_code == 409
                                      and "kill switch" in r.text.lower(), "tickets_after": after,
                                      "detail": r.text[:160]}


async def _probe_after(fake, gate, probes, project_root, pid, seen_prompts) -> None:
    from app.bots import outsee_http
    from app.services import media_spend, outsee_idempotency, outsee_retry, spend_guard
    scenes = project_root / "scenes"
    entry: dict[str, Any] = {"adopted": False}
    before = fake.posts + gate.paid_posts + gate.probe_posts
    if seen_prompts:
        seen = seen_prompts[-1]
        try:
            res = await outsee_retry.generate_image_with_retries(
                None, None, prompt=seen["prompt"], out_path=scenes / "frame_001_deadbeef.png", model_slug=seen["model"],
                project_id=pid, gpt_rewrite=False, idempotent=True)
            entry.update(adopted=str(getattr(res, "gen_id", "")).startswith("idem:"), file=Path(str(res.file_path)).name)
        except Exception as exc:
            entry.update(error=f"{type(exc).__name__}: {str(exc)[:120]}")
    else:
        entry["error"] = "the request of the step was not captured"
    entry["posts_delta"] = fake.posts + gate.paid_posts + gate.probe_posts - before
    probes["identical_request"] = entry
    entry = {"refused": False}
    before = fake.posts + gate.paid_posts + gate.probe_posts
    try:
        with media_spend.resume_only():
            await outsee_retry.generate_image_with_retries(
                None, None, prompt="a different request for the same frame", out_path=scenes / "frame_001_cafebabe.png",
                model_slug=(seen_prompts[-1]["model"] if seen_prompts else None), project_id=pid, gpt_rewrite=False,
                idempotent=True)
    except outsee_idempotency.OutseeResumeOnlyError:
        entry["refused"] = True
    except Exception as exc:
        entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
    entry["posts_delta"] = fake.posts + gate.paid_posts + gate.probe_posts - before
    probes["resume_only_other"] = entry

    def call():
        return outsee_http.generate_image("another frame", scenes / "frame_002_aaaaaaaa.png", model_slug="nano-banana-2",
                                          project_id=None)

    await _probe_one("second_submit_cap", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))


async def _wait_exhausted(args, project_db, counters, gate, fake) -> None:
    deadline = time.monotonic() + args.timeout_s
    last = None
    while time.monotonic() < deadline:
        fs = qc._step_failure(project_db)
        nodes = _node_status(project_db)
        state = (nodes.get(TARGET), fs.get("total_fails"), bool(fs.get("abandoned_at")), gate.paid_posts,
                 counters["injected_hitl"], counters["injected_tail"])
        if state != last:
            print(time.strftime("%H:%M:%S"), "node:", state, flush=True)
            last = state
        if fs.get("abandoned_at") and nodes.get(TARGET) == "failed":
            return
        await asyncio.sleep(1)
    raise RuntimeError("native retries were not exhausted in time (timeout)")


async def _wait_incident(client, prefix, timeout_s) -> dict | None:
    deadline = time.monotonic() + min(timeout_s, 120)
    while time.monotonic() < deadline:
        data = (await client.get(prefix + "/diagnoses")).json()
        for i in data.get("incidents", []):
            if (i.get("node_key") == TARGET and i.get("type") == "media_failure" and i.get("currently_relevant")
                    and not (i.get("echo_of"))):
                return i
        await asyncio.sleep(1)
    return None


async def _poll_recovery(client, prefix, args, project_db, counters, gate, result) -> None:
    deadline = time.monotonic() + args.timeout_s
    last = None
    settled = None
    while time.monotonic() < deadline:
        decisions = (await client.get(prefix + "/decisions")).json()
        actions = (await client.get(prefix + "/actions")).json()
        diagnoses = (await client.get(prefix + "/diagnoses")).json()
        result.update(decisions=decisions, actions=actions, diagnoses=diagnoses)
        recs, recoveries = decisions["records"], actions["recoveries"]
        state = ([(d["status"], d["model_steps"]) for d in recs], [r["status"] for r in recoveries], gate.paid_posts,
                 counters["decision_stub"], gate.decision_posts)
        if state != last:
            print(time.strftime("%H:%M:%S"), state, flush=True)
            last = state
        done = bool(recs) and recs[0]["status"] in TERMINAL_DECISION and bool(recoveries) and \
            recoveries[0]["status"] in ("verified", "human_review", "failed", "rejected")
        escalated = bool(recs) and recs[0]["status"] in TERMINAL_DECISION and not recoveries
        if done or escalated:
            settled = settled or time.monotonic()
            if time.monotonic() - settled > args.settle_s:
                return
        else:
            settled = None
        await asyncio.sleep(1)
    result["stopped_at"] = "timeout"


def _finish(args, mode, result, gate, counters, probes, advisories, decision_contexts, fake, state_db, project_db,
            project_root, execution, scrub, work, pid) -> int:
    records = read_records(state_db)
    idem_results = [r for r in records["idem_rows"] if r.get("key") == FRAME_KEY and r.get("event") == "result"]
    recorded: dict[str, Any] = {"exists": False}
    rel = None
    if idem_results:
        path = Path(idem_results[-1]["file_path"])
        recorded = {"path_name": path.name, "exists": path.is_file(), "sha256": sha256_file(path) if path.is_file() else None,
                    "bytes": path.stat().st_size if path.is_file() else 0}
        try:
            rel = path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
    events = qc._events(state_db, execution)
    attempts = sum(1 for e in events if e["kind"] == "node_start" and e.get("node_key") == TARGET)
    fs = qc._step_failure(project_db)
    snap: dict[str, Any] = {
        **result, **records,
        "counters": {**counters, "paid_posts": gate.paid_posts, "probe_posts": gate.probe_posts,
                     "decision_posts": gate.decision_posts, "refused_cap": gate.refused_cap,
                     "unexpected_model": gate.unexpected_model, "refused_foreign": len([r for r in gate.refused
                                                                                       if r["kind"] in ("dry_foreign", "foreign")]),
                     "fake_posts": fake.posts if mode == "dry" else None, "fake_auth_failures": fake.auth_failures},
        "probes": probes, "recorded_file": recorded, "recorded_relative_path": rel,
        "node_status": _node_status(project_db), "node_attempts": attempts, "step_failure": fs,
        "hitl_pending": qc._hitl_pending(project_db), "decision_contexts": decision_contexts,
        "refused_hosts": sorted({r["host"] for r in gate.refused if r["kind"] in ("dry_foreign", "foreign", "outsee_other")}),
        "max_decision_posts": args.max_decision_posts,
    }
    checks = evaluate(snap, mode=mode, max_real_posts=args.max_real_posts)
    ok, failed = verdict(checks)
    if result.get("error"):
        ok = False
        failed = ["flow_error"] + failed
        checks.insert(0, check("flow_completed_without_error", False, scrub(str(result["error"]))))
    reality = reality_report(mode, snap)
    summary = {
        "mode": mode, "node": NODE, "verdict": final_verdict(mode, ok), "failed_checks": failed,
        "paid_posts": gate.paid_posts, "decision_posts": gate.decision_posts, "probe_posts": gate.probe_posts,
        "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd, "app_caps_usd": result.get("app_caps_usd"),
                 "worst_case_usd": result.get("worst_case_usd")},
        "model_slug_sent": result.get("model_slug_sent"), "node_attempts": attempts, "advisories": advisories,
        "not_proven": reality_not_proven(mode)}
    slim = {k: v for k, v in snap.items() if k not in ("idem_rows",)}
    slim.update(checks=checks, ok=ok, failed=failed, reality=reality, summary=summary,
                idem_events=[(r["event"], r["key"]) for r in records["idem_rows"]])
    (work / "result.json").write_text(scrub(json.dumps(slim, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {NODE} (Outsee) | mode: {mode} | node status: {snap['node_status']} | native attempts: {attempts}")
    print("counters:", scrub(json.dumps({k: v for k, v in snap["counters"].items()}, default=str)))
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"] + ("" if c["ok"] or not c["needs"] else f"   [needs: {c['needs']}]"))
        if not c["ok"]:
            print("     detail:", scrub(json.dumps(c["detail"], ensure_ascii=False, default=str))[:400])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    for note in advisories:
        print("advisory:", scrub(note))
    print("verdict:", summary["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
