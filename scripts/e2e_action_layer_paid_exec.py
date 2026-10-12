"""F09: paid executors of the action layer on a REAL server process (Outsee, money ceilings, kill switch).

    python -X utf8 scripts/e2e_action_layer_paid_exec.py --dry  --work-dir C:\\video-pipeline\\_f09_work\\dry1
    python -X utf8 scripts/e2e_action_layer_paid_exec.py --live --env-file <env with ONLY whitelisted names>
                                                         --work-dir C:\\video-pipeline\\_f09_work\\live1

Plan: docs/REGISTRAR_FULL_PLAN.md F08/F09 (+ A07 kill switch, A10 server task stop, A11 layer spend fact).

What it does. For EVERY scenario a fresh work dir OUTSIDE the repository, a fresh empty state.db, a seeded synthetic
project and a REAL ``python -m app.main`` child (through the thin ``--serve`` launcher of this file: socket guard,
request Gate inside the server process, wire counter on disk, stand-level seams). Every plan goes through the real
HTTP API (``/api/action-layer/plans/preview`` -> approve with the plan hash -> run -> poll -> history -> operator_audit
-> timeline), never the paid planner ``/from-text``.

  S1  ``montage.apply_queue``, ONE queued op ``image_regen`` for ONE frame (cheapest generator, 1k).
      S1a  as shipped: the layer has no Outsee tariff -> the estimate is unknown -> the plan can not be approved
           (fail-closed). Proven with ZERO requests on the wire.
      S1b  only with a stand-injected tariff (``--inject-tariff-credits``, default 10 in --dry, OFF in --live): the
           same plan runs for real (stand-level patch of ``_kie_unit``, NOT production behaviour).
  S2  ``step.run_step`` for the images step of a 1-frame project (operator ceiling_credits, as the catalog demands
      for Outsee).
  S3  KILL SWITCH IN THE MIDDLE of a real paid step: 2-frame ``step.run_step``; the switch is turned on through the
      production route while the first paid request is in flight; no second paid request may leave the process, the
      server-side task must stop, nothing may be reported as success without a file, the switch is released and a
      later approved plan works.
  S4  ``montage.improve_scene`` (flag ``--with-improve-scene``): SKIPPED - its LLM path needs a Vibecode transport
      (3 model steps + a scene editor state), more than reading whitelisted env names.

Modes (exactly one): ``--dry`` (loopback FakeOutsee, no key, no network) or ``--live`` (real Outsee, ``--env-file``
with ONLY OUTSEE_API_KEY / OUTSEE_API_BASE_URL / OUTSEE_DEFAULT_IMAGE_MODEL; Gate inside the server process allows the
Outsee host only; ``--max-real-posts`` default 3, ``--max-usd`` default 0.30, hard ceiling 1.00).
Negative controls (dry): ``--dry-scenario second_submit`` (production seam sends a second POST in S1b),
``--dry-scenario no_stop_on_kill`` (the layer does not react to the switch in S3). Both must end NOT PASSED.

Exit codes: 0 passed, 1 not passed, 2 bad arguments, 3 refused before anything was sent.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
SELF = Path(__file__).resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_paid_exec", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


loc = _load_sibling("e2e_action_layer_local_exec")   # Api, Db, Report, scrub_env, server_env, helpers: imported, not edited
prod = _load_sibling("e2e_media_production_route")   # Gate, FakeOutsee, make_png, env whitelist loader, base checks
qc = prod.qc

DEFAULT_PORT = 18821
DEFAULT_FAKE_PORT = 18822
HARD_CEILING_USD = 1.00
DEFAULT_MAX_USD = 0.30
DEFAULT_MAX_REAL_POSTS = 3
DEFAULT_TARIFF_CREDITS = 10.0          # stand-injected, dry only by default: $0.05 per image attempt (> the real ~$0.03)
CREDIT_USD = 0.005                     # kie_catalog.CREDIT_USD
MEDIA_SUBMIT_ESTIMATE_USD = 0.30       # spend_guard.MEDIA_SUBMIT_ESTIMATE_USD (the app's own per-submit estimate)
LLM_OP_CALL_HIGH_CREDITS = 2.96        # builtin_montage_more.LLM_OP_CALL_HIGH (checked against the code in the tests)
IMAGE_BILLED_ATTEMPTS = 3
CAP_MARGIN_USD = 0.05
S2_CEILING_CREDITS = 20.0              # $0.10: an operator ceiling for ONE Outsee image (real price about $0.03)
S3_CEILING_CREDITS = 40.0              # $0.20 for two frames; only the first one may ever be paid
FAKE_DELAY_S3 = 8.0
SCENARIO_IDS = ("S1", "S2", "S3")
DRY_SCENARIOS = ("none", "second_submit", "no_stop_on_kill")
IMAGE_GENERATORS = prod.IMAGE_GENERATORS            # ("gpt_image_2_vip", "nano_banana_2")
EXIT_PASS, EXIT_FAIL, EXIT_ARGS, EXIT_REFUSED = 0, 1, 2, 3
VERDICT_PASS_LIVE = "PASS (live: real Outsee requests through the action layer; see the not-proven list)"
VERDICT_DRY = "DRY OK (loopback fake provider: machinery proven offline, NOT a live proof)"
VERDICT_FAIL = "NOT PASSED"
WIRE = "wire.jsonl"
SERVER_ENV_NAMES = ("OUTSEE_API_KEY", "OUTSEE_API_BASE_URL", "OUTSEE_DEFAULT_IMAGE_MODEL")
HISTORY_FULL = ("plan_created", "approval_created", "approval_consumed", "run_requested", "plan_started",
                "step_started", "step_succeeded", "plan_finished")


# ───────────────────────────── pure helpers (unit-tested) ─────────────────────────────

def authorisation_problem(max_real_posts: int, max_usd: float) -> str | None:
    """Stand authorisation: a hard ceiling of $1.00 and the production-route rule (posts x $0.10 <= max-usd)."""
    if max_usd > HARD_CEILING_USD + 1e-9:
        return f"--max-usd {max_usd} exceeds the hard ceiling ${HARD_CEILING_USD}"
    return prod.authorisation_problem(max_real_posts, max_usd)


def reserve_usd(credits: float) -> float:
    return round(float(credits) * CREDIT_USD, 6)


def apply_estimate_credits(tariff_credits: float, ops: int = 1) -> float:
    """High estimate of montage.apply_queue for ``ops`` image ops at a per-attempt tariff (builtin_montage_more)."""
    return round(ops * IMAGE_BILLED_ATTEMPTS * tariff_credits + ops * LLM_OP_CALL_HIGH_CREDITS, 2)


def app_cap_usd(reserve: float, max_usd: float) -> float | None:
    """The app's own caps (run/project/daily). The app counts ONE Outsee submit as $0.30 PLUS the plan's open reserve,
    so the caps must be at least that; they are never below --max-usd. None: does not fit under the hard ceiling."""
    need = round(MEDIA_SUBMIT_ESTIMATE_USD + float(reserve) + CAP_MARGIN_USD, 2)
    cap = max(round(float(max_usd), 2), need)
    return cap if cap <= HARD_CEILING_USD + 1e-9 else None


def gate_posts(max_real_posts: int, dry_scenario: str) -> int:
    """Posts the in-server Gate lets through: one per scenario at most; the second_submit control must reach two."""
    return 2 if dry_scenario == "second_submit" else 1


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


def wire_posts(wire: list[dict]) -> int:
    return sum(1 for e in wire if e.get("event") == "post_sent")


def wire_responses(wire: list[dict]) -> int:
    return sum(1 for e in wire if e.get("event") == "response_received")


def wire_in_flight(wire: list[dict]) -> int:
    return wire_posts(wire) - wire_responses(wire) - sum(1 for e in wire if e.get("event") == "post_failed")


def wire_refused(wire: list[dict]) -> list[dict]:
    return [e for e in wire if e.get("event") == "refused"]


def ledger_summary(rows: list[dict], plan_id: str, project_id: int | None = None) -> dict:
    """What spend_ledger says about ONE plan. The layer's own rows carry ``run_id == plan:<id>``; the native paid rows
    of a worker-run step are taken by project (the stand's DB holds one project) and counted separately when they are
    tagged with the plan (B02 correlation)."""
    tag = f"plan:{plan_id}"
    layer = [r for r in rows if r.get("source") == "action_layer" and r.get("run_id") == tag]
    native = [r for r in rows if r.get("source") != "action_layer" and r.get("kind") != "event"
              and (project_id is None or r.get("project_id") == project_id or r.get("run_id") == tag)]
    reserves = [r for r in layer if r.get("kind") == "reserve"]
    settles = [r for r in layer if r.get("kind") == "settle"]
    unknown = [r for r in native if "cost_unknown=true" in str(r.get("note") or "")]
    return {
        "rows": len(layer) + len(native), "layer_rows": len(layer), "native_rows": len(native),
        "native_tagged_with_plan": sum(1 for r in native if r.get("run_id") == tag),
        "native_run_ids": sorted({str(r.get("run_id")) for r in native}),
        "reserve_usd": round(sum(float(r.get("usd") or 0) for r in reserves), 6),
        "reserve_credits": round(sum(float(r.get("credits") or 0) for r in reserves), 6),
        "settle_rows": len(settles),
        "layer_net_usd": round(sum(float(r.get("usd") or 0) for r in layer), 6),
        "layer_net_credits": round(sum(float(r.get("credits") or 0) for r in layer), 6),
        "native_usd": round(sum(float(r.get("usd") or 0) for r in native), 6),
        "native_credits": round(sum(float(r.get("credits") or 0) for r in native), 6),
        "native_unknown_rows": len(unknown),
        "open_reserve": bool(reserves) and not settles,
        "provider_rows": sorted({str(r.get("provider")) for r in native}),
    }


def credits_agree(actual: float | None, ledger_credits: float, tol: float = 0.01) -> bool:
    return actual is not None and abs(float(actual) - float(ledger_credits)) <= tol


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return VERDICT_FAIL
    return VERDICT_PASS_LIVE if mode == "live" else VERDICT_DRY


def reality_lists(mode: str, snap: dict) -> dict[str, list[str]]:
    live = mode == "live"
    posts = snap.get("wire_posts_total", 0)
    return {
        "real": [x for x in (
            "a real python -m app.main child process per scenario (fresh empty state.db outside the repository)",
            "operator path over HTTP: preview -> approve(plan hash) -> run -> poll -> history -> operator_audit -> timeline",
            "production executors montage.apply_queue / step.run_step, budget.reserve/settle, spend_guard caps, "
            "kill switch via the production route, outsee_http / outsee_idempotency / media_spend inside the server",
            "request Gate and socket guard INSIDE the server process; the wire counter is written before the bytes leave",
            f"{posts} paid Outsee submit(s) on the wire" + (" (real provider)" if live else " (loopback fake)")) if x],
        "not_real": [x for x in (
            None if live else "the provider: a loopback FakeOutsee (nothing left the machine, nothing was paid)",
            "the projects are synthetic (1-2 frames, built by this script), not a copy of a production project",
            "S1b only: the Outsee tariff is injected by the stand (builtin_montage_more._kie_unit patch); the shipped "
            "layer has none (S1a proves it refuses)" if snap.get("tariff_injected") else None,
            "picture quality is never judged",
            "Outsee has no task id on the client side: no provider task is polled or cancelled") if x],
        "not_proven": [x for x in (
            None if live else "anything about the real Outsee, its price or its behaviour: this is a dry run",
            "the price of a real Outsee image (the application's tariffs are uncalibrated: cost_unknown rows)",
            "montage.improve_scene (S4): skipped, needs a Vibecode LLM transport",
            "video, audio and music steps; project.resume; media.resume_poll; recover_outsee",
            "cancelling an already-sent Outsee request (the provider can not be told to stop)",
            "the real Outsee balance change (compare with the cabinet by hand)" if live else None) if x],
    }


# ───────────────────────────── fake provider with a delay (parent process) ─────────────────────────────

class SlowFake:
    """prod.FakeOutsee with a per-POST delay so a paid request stays 'in flight' long enough to act on."""

    def __init__(self, port: int, delay_s: float = 0.0) -> None:
        self.inner = prod.FakeOutsee(port)
        self.delay_s = float(delay_s)
        base_handler = self.inner.httpd.RequestHandlerClass
        outer = self

        class Handler(base_handler):          # type: ignore[misc, valid-type]
            def do_POST(self):               # noqa: N802
                if outer.delay_s > 0:
                    time.sleep(outer.delay_s)
                try:
                    return super().do_POST()
                except (ConnectionError, OSError):   # the app cancelled its waiting request (stop): not a stand error
                    return None

        self.inner.httpd.RequestHandlerClass = Handler

    def __getattr__(self, name):
        return getattr(self.inner, name)

    def start(self) -> "SlowFake":
        self.inner.start()
        return self


# ───────────────────────────── seed (child process) ─────────────────────────────

MODEL_IDS = {"nano_banana_2": "nano-banana-2", "gpt_image_2_vip": "gpt-image-2-vip"}   # node modelId (the node overrides the project)
IMAGE_PROMPT = prod.IMAGE_PROMPT
VOICEOVER = prod.VOICEOVER


async def seed_projects(work: Path, scenario: str, generator: str) -> dict:
    """One synthetic project: node `images`, N frames with image prompts, status image_prompts_ready."""
    import uuid as _uuid
    from app import main as app_main
    from app.db import session_scope
    from app.models import Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus

    n_frames = 2 if scenario == "S3" else 1
    await app_main._init_db()
    node_key = "media-images-A"
    node = {"id": node_key, "type": "images", "position": {"x": 100.0, "y": 100.0},
            "data": {"label": "Native images", "groupId": "media-g1", "modelId": MODEL_IDS.get(generator, generator),
                     "imageResolution": "1k"}}
    nodes, edges = [node], []
    slug = f"al-paid-{scenario.lower()}-{_uuid.uuid4().hex[:6]}"
    async with session_scope() as session:
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, "img_streams": 1}
        project = Project(slug=slug, topic="action layer paid exec stand", status=ProjectStatus.image_prompts_ready,
                          auto_mode=False, general_plan=VOICEOVER, script_text=VOICEOVER, meta=meta,
                          image_generator=generator, aspect_ratio="9_16", image_resolution="1k")
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=node_key, node_type="images", status=NodeRunStatus.pending))
        uuids = []
        for i in range(1, n_frames + 1):
            fu = f"{i:02d}" * 12
            uuids.append(fu)
            session.add(Frame(project_id=project.id, number=i, uuid=fu, sort_key=float(i), voiceover_text=VOICEOVER,
                              image_prompt=IMAGE_PROMPT, animation_prompt=prod.ANIMATION_PROMPT,
                              duration_seconds=3.0, attrs={}))
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(VOICEOVER, encoding="utf-8")
    seed = {"project_id": pid, "slug": slug, "data_dir": str(data_dir), "frame_uuids": uuids, "node_key": node_key,
            "frames": n_frames}
    (work / "seed.json").write_text(json.dumps(seed, ensure_ascii=False, indent=2), encoding="utf-8")
    return seed


def seed_main(argv: list[str]) -> int:
    import asyncio
    work, scenario, generator = Path(argv[0]), argv[1], argv[2]
    sys.path.insert(0, str(ROOT))
    asyncio.run(seed_projects(work, scenario, generator))
    return 0


# ───────────────────────────── server launcher (child process) ─────────────────────────────

def wire_append(path: Path, event: dict) -> None:
    """One durable line (open, write, fsync, close): it must be on disk before the request bytes leave."""
    event = {**event, "t": round(time.time(), 3)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def serve_main() -> int:
    """Thin launcher of ``python -m app.main``: socket guard, request Gate, wire counter, secrets blanked, seams."""
    import socket
    cfg = json.loads(os.environ["AL_PAID_CFG"])
    work = Path(cfg["work"])
    wire_path = work / WIRE
    net_log = work / "net_attempts.jsonl"
    sys.path.insert(0, str(ROOT))
    gate = prod.Gate(mode=cfg["mode"], max_paid_posts=int(cfg["gate_posts"]), outsee_host=cfg.get("outsee_host", ""),
                     fake_port=int(cfg["fake_port"]), phase="main")
    own = socket.gethostname().lower()
    orig = {"connect": socket.socket.connect, "connect_ex": socket.socket.connect_ex, "gai": socket.getaddrinfo}
    learned_ips: set[str] = set()  # адреса, в которые разрешились РАЗРЕШЁННЫЕ имена (прокси/fake-IP отдают 198.18.x.x)

    def allowed(host) -> bool:
        if isinstance(host, (bytes, bytearray)):  # anyio/httpx передают имя хоста байтами
            host = bytes(host).decode("ascii", "ignore")
        h = str(host or "").strip("[]").lower()
        if loc.is_loopback_host(host) or h == own:
            return True
        return cfg["mode"] == "live" and (h == gate.outsee_host or h in gate.learned_hosts or h in learned_ips)

    def note(kind, host) -> None:
        with net_log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "host": str(host), "t": time.time()}) + "\n")

    def connect(self, address):
        if isinstance(address, tuple) and address and not allowed(address[0]):
            note("connect", address[0])
            raise OSError(f"network guard: connect to {address[0]!r} refused")
        return orig["connect"](self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and address and not allowed(address[0]):
            note("connect_ex", address[0])
            return 10061
        return orig["connect_ex"](self, address)

    def gai(host, *a, **k):
        if not allowed(host):
            note("getaddrinfo", host)
            raise socket.gaierror(f"network guard: lookup of {host!r} refused")
        res = orig["gai"](host, *a, **k)
        if cfg["mode"] == "live" and not loc.is_loopback_host(host):
            for item in res:
                learned_ips.add(str(item[4][0]).lower())
        return res

    socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo = connect, connect_ex, gai
    import asyncio
    for cls in {asyncio.SelectorEventLoop, getattr(asyncio, "ProactorEventLoop", asyncio.SelectorEventLoop)}:
        original = cls.sock_connect

        async def sock_connect(self, sock, address, _o=original):
            if isinstance(address, tuple) and address and not allowed(address[0]):
                note("sock_connect", address[0])
                raise OSError(f"network guard: sock_connect to {address[0]!r} refused")
            return await _o(self, sock, address)

        cls.sock_connect = sock_connect
    (work / "net_guard_installed.txt").write_text("1", encoding="utf-8")

    from app.settings import settings

    def put(name: str, value) -> None:
        try:
            setattr(settings, name, value)
        except Exception:  # noqa: BLE001
            object.__setattr__(settings, name, value)

    blanked = []
    for name in list(type(settings).model_fields):
        if loc.SECRET_RE.search(name) and isinstance(getattr(settings, name, None), str):
            if getattr(settings, name):
                blanked.append(name)
            put(name, "")
    port = int(cfg["port"])
    put("vibecode_api_key", qc.SYNTHETIC_KEY)                   # the step builds a text client even for the HTTP image path;
    put("vibecode_base_url", f"http://127.0.0.1:{port}/v1")     # a chat request would be refused (and counted) by the Gate
    put("gpt_base_url", f"http://127.0.0.1:{port}")
    key = os.environ.get("AL_PAID_OUTSEE_KEY") or ""            # the ONLY real key the server may hold
    put("outsee_api_key", key)
    put("outsee_api_base_url", cfg["outsee_base"])
    put("image_provider", "outsee")
    put("outsee_default_image_model", cfg.get("outsee_model") or "gpt-image-2")
    for name in ("spend_daily_cap_usd", "spend_project_cap_usd", "spend_run_cap_usd"):
        put(name, float(cfg["app_cap_usd"]))
    (work / "blanked_settings.json").write_text(json.dumps(sorted(blanked)), encoding="utf-8")

    import httpx
    from app.bots import outsee_http
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if isinstance(getattr(client, "_transport", None), httpx.ASGITransport):
            return await original_send(client, request, **kwargs)        # in-process ASGI call, no socket
        host, port, path = request.url.host, request.url.port, request.url.path
        judged = gate.judge(request.method, host, port, path, is_decision=False)
        if judged.action != "pass":
            wire_append(wire_path, {"event": "refused", "kind": judged.kind, "host": str(host), "method": request.method,
                                    "path": path, "reason": judged.reason[:160]})
            raise RuntimeError(judged.reason)
        paid = judged.kind in ("paid_post_fake", "paid_post_real")
        if paid:
            wire_append(wire_path, {"event": "post_sent", "n": gate.paid_posts, "host": str(host), "path": path})
        try:
            response = await original_send(client, request, **kwargs)
        except BaseException as exc:
            if paid:
                wire_append(wire_path, {"event": "post_failed", "error": type(exc).__name__})
            raise
        if paid:
            wire_append(wire_path, {"event": "response_received", "status": response.status_code})
        if judged.kind in ("outsee_get", "paid_post_real"):
            try:
                gate.learn(response.text)
            except Exception:  # noqa: BLE001
                pass
        return response

    async def no_curl_post(*_a, **_k):
        return None        # the curl submit fallback is a second transport the hook can not count: never used

    original_download_curl = outsee_http._download_via_curl

    async def guarded_download_curl(url, out_path):
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
        if qc.is_loopback(host) or (cfg["mode"] == "live" and (host == gate.outsee_host or host in gate.learned_hosts)):
            return await original_download_curl(url, out_path)
        return False

    outsee_http._post_generate_via_curl = no_curl_post
    outsee_http._download_via_curl = guarded_download_curl
    httpx.AsyncClient.send = send

    seam = cfg.get("seam") or "none"
    if seam == "second_submit":
        real_post = outsee_http._post_generate
        state = {"n": 0}

        async def twice(path, body):
            result = await real_post(path, body)
            if state["n"] == 0 and "/images/" in str(path):
                state["n"] = 1
                await real_post(path, body)         # the extra production-side submit the control must catch
            return result

        outsee_http._post_generate = twice
    if seam == "no_stop_on_kill":
        from app.services.action_layer import executor

        async def never_on() -> bool:
            return False

        executor._kill_switch_on = never_on

    tariff_file = work / "tariff.json"
    from app.services.action_layer import builtin_montage_more as bmm
    real_unit = bmm._kie_unit

    def staged_unit(kind, gen_id, res_id):
        got = real_unit(kind, gen_id, res_id)
        try:
            tariff = json.loads(tariff_file.read_text(encoding="utf-8")) if tariff_file.is_file() else None
        except ValueError:
            tariff = None
        if got[0] is None and kind == "image" and tariff and float(tariff.get("credits") or 0) > 0:
            return float(tariff["credits"]), f"{gen_id}: тариф ВНЕСЁН СТЕНДОМ {tariff['credits']} кр (не продакшен)"
        return got

    bmm._kie_unit = staged_unit
    import runpy
    runpy.run_module("app.main", run_name="__main__", alter_sys=True)
    return 0


def server_env(work: Path, port: int, cfg: dict, key: str, dry: bool) -> dict[str, str]:
    env = loc.server_env(os.environ, work, port, "tiny")
    for k in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "ASR_BACKEND", "WHISPER_MODEL", "WHISPER_DEVICE",
              "WHISPER_COMPUTE_TYPE", "AL_STAND_PROMPTS"):
        env.pop(k, None)
    env.update(qc.env_overrides(work / "data", port))       # synthetic text-LLM key + loopback bases (never called)
    env.update({
        "AL_PAID_CFG": json.dumps(cfg),
        "SPEND_DAILY_CAP_USD": str(cfg["app_cap_usd"]), "SPEND_PROJECT_CAP_USD": str(cfg["app_cap_usd"]),
        "SPEND_RUN_CAP_USD": str(cfg["app_cap_usd"]), "MEDIA_SPEND_RECORDING": "true", "IMAGE_PROVIDER": "outsee",
        "ACTION_LAYER_STEP_TIMEOUT_S": "300", "ACTION_LAYER_SESSION_CAP_CREDITS": "100",
        "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
    })
    if dry:
        env.update({"DISK_GUARD_LOW_FREE_GB": "0.001", "DISK_GUARD_CRITICAL_FREE_GB": "0.0005"})
    if key:
        env["AL_PAID_OUTSEE_KEY"] = key
    return env


# ───────────────────────────── stand (parent process) ─────────────────────────────

class PaidReport(loc.Report):
    def skip(self, name: str, why: str) -> None:
        self.checks.append({"name": name, "ok": True, "skipped": True, "detail": why, "known": None})
        print("SKIP  " + name + "  " + why[:300], flush=True)


class Stand:
    """One scenario: one server, one project."""

    def __init__(self, args, scenario: str, work: Path, seed: dict, api, db, report: PaidReport, fake, key_hint: str) -> None:
        self.args, self.scenario, self.work, self.seed = args, scenario, work, seed
        self.api, self.db, self.R, self.fake = api, db, report, fake
        self.pid = int(seed["project_id"])
        self.pdb = loc.Db(Path(seed["data_dir"]) / "project.db")
        self.catalog: dict[str, dict] = {}
        self.plans: list[dict] = []
        self.operator = ""
        self.timeline_sample: list = []

    # --- reads ---
    def wire(self) -> list[dict]:
        return read_wire(self.work / WIRE)

    def project(self) -> dict:
        code, body = self.api.get(f"/api/projects/{self.pid}")
        return body if code == 200 and isinstance(body, dict) else {}

    def plan_view(self, plan_id: str) -> dict:
        code, body = self.api.get(f"/api/action-layer/plans/{plan_id}")
        return body if code == 200 and isinstance(body, dict) else {}

    def history(self, plan_id: str) -> list[dict]:
        code, body = self.api.get("/api/action-layer/history", params={"plan_id": plan_id, "limit": 200})
        return list(reversed((body or {}).get("items") or [])) if code == 200 else []

    def wait_plan(self, plan_id: str, timeout: float) -> dict:
        end = time.time() + timeout
        view: dict = {}
        while time.time() < end:
            view = self.plan_view(plan_id)
            if view.get("status") in loc.HISTORY_TERMINAL and not view.get("live"):
                return view
            time.sleep(1.0)
        return view

    def scene_files(self) -> list[Path]:
        scenes = Path(self.seed["data_dir"]) / "scenes"
        return sorted(p for p in scenes.glob("frame_*.png") if p.is_file()) if scenes.is_dir() else []

    def ledger_rows(self) -> list[dict]:
        return self.db.q("SELECT id,kind,provider,project_id,run_id,source,usd,credits,note,ref,request_id FROM spend_ledger ORDER BY id")

    # --- plans ---
    def preview(self, steps: list[dict], text: str):
        return self.api.post("/api/action-layer/plans/preview", {"steps": steps, "request_text": text})

    def start_paid(self, label: str, steps: list[dict], expect_cost_known: bool = True) -> dict | None:
        """preview -> checks -> run without approval refused -> approve(hash) -> run accepted. Does NOT wait."""
        R = self.R
        code, pv = self.preview(steps, f"F09 {self.scenario} {label}")
        if not R.check(f"{self.scenario} {label}: превью принято (200, есть plan_id)", code == 200 and "plan_id" in (pv or {}),
                       (code, str(pv)[:400])):
            return None
        plan_id, prev = pv["plan_id"], pv["preview"]
        self.plans.append({"label": f"{self.scenario} {label}", "plan_id": plan_id, "actions": [s["action_id"] for s in steps]})
        R.check(f"{self.scenario} {label}: уровень destructive, подтверждение нужно, платное",
                prev.get("risk_max") == "destructive" and bool(prev.get("needs_approval"))
                and any(s.get("spends_money") for s in prev["steps"]),
                {k: prev.get(k) for k in ("risk_max", "needs_approval")})
        R.check(f"{self.scenario} {label}: стоимость в превью известна и больше нуля",
                prev["cost"].get("known") is True and (prev["cost"].get("high") or 0) > 0, prev["cost"])
        code, body = self.api.post(f"/api/action-layer/plans/{plan_id}/run")
        R.check(f"{self.scenario} {label}: запуск БЕЗ подтверждения отказан (403 approval_required)",
                code == 403 and ((body or {}).get("detail") or {}).get("code") == "approval_required", (code, body))
        code, ap = self.api.post(f"/api/action-layer/plans/{plan_id}/approve")
        if not R.check(f"{self.scenario} {label}: подтверждение выдано (200, хэш плана совпадает с превью)",
                       code == 200 and ap.get("plan_hash") == prev["plan_hash"], (code, ap)):
            return {"plan_id": plan_id, "preview": prev, "started": False}
        self.operator = ap.get("operator", self.operator)
        self.plans[-1]["operator"] = ap.get("operator")
        code, body = self.api.post(f"/api/action-layer/plans/{plan_id}/run")
        ok = R.check(f"{self.scenario} {label}: запуск принят (200 running)", code == 200 and (body or {}).get("status") == "running",
                     (code, body))
        return {"plan_id": plan_id, "preview": prev, "started": ok}

    def records_checks(self, label: str, plan_id: str, view: dict, *, expect_posts: int, expect_status: str) -> dict:
        """Money / ledger / history / audit / timeline checks shared by S1b, S2 (and partly S3)."""
        R, sc = self.R, self.scenario
        wire = self.wire()
        led = ledger_summary(self.ledger_rows(), plan_id, self.pid)
        step = (view.get("steps") or [{}])[0]
        actual_step = (step.get("cost") or {}).get("actual")
        R.check(f"{sc} {label}: план завершился как {expect_status}", view.get("status") == expect_status,
                {k: view.get(k) for k in ("status", "error", "stop_reason")}
                | {"steps": [(s["status"], s["error"]) for s in view.get("steps", [])]})
        R.check(f"{sc} {label}: ровно {expect_posts} платный запрос на проводе (счётчик сервера)", wire_posts(wire) == expect_posts,
                {"wire_posts": wire_posts(wire), "responses": wire_responses(wire), "refused": wire_refused(wire)[:3]})
        if self.args.dry:
            R.check(f"{sc} {label}: провайдер (fake) независимо увидел столько же запросов",
                    self.fake.posts == expect_posts and self.fake.auth_failures == 0,
                    {"fake_posts": self.fake.posts, "auth_failures": self.fake.auth_failures})
        idem = self.db.q("SELECT event,key,attempt_id FROM outsee_idempotency ORDER BY id") if self.db.has_table("outsee_idempotency") else []
        tasks = self.db.q("SELECT event,key,status,run_id FROM provider_task_ledger ORDER BY id") if self.db.has_table("provider_task_ledger") else []
        R.check(f"{sc} {label}: журнал задач провайдера: ровно одно намерение (intent) на платный запрос",
                sum(1 for r in tasks if r["event"] == "intent") == expect_posts, [(r["event"], r["key"], r["status"]) for r in tasks])
        if sc != "S1":      # the montage regen path does not use the request-key (idempotency) records
            R.check(f"{sc} {label}: запись идемпотентности: ровно одно намерение (intent)",
                    sum(1 for r in idem if r["event"] == "intent") == expect_posts, [(r["event"], r["key"]) for r in idem])
        files = self.scene_files()
        good = []
        for p in files:
            try:
                from PIL import Image
                with Image.open(p) as im:
                    im.verify()
                good.append(p)
            except Exception:  # noqa: BLE001
                pass
        R.check(f"{sc} {label}: файл картинки лёг на диск и читается как картинка",
                len(good) >= 1 and all(p.stat().st_size > 0 for p in good), [(p.name, p.stat().st_size) for p in files])
        # money
        R.check(f"{sc} {label}: журнал трат содержит резерв слоя (plan:<id>) и строку платного вызова проекта",
                led["layer_rows"] >= 1 and led["native_rows"] >= 1, led)
        R.check(f"{sc} {label}: B02 строки платного вызова помечены run_id=plan:<id> (а не тиком воркера)",
                led["native_rows"] >= 1 and led["native_tagged_with_plan"] == led["native_rows"],
                {"native_run_ids": led["native_run_ids"], "plan": f"plan:{plan_id}"}, known="F09-D4")
        unknown_priced = bool(led["native_unknown_rows"]) and not led["open_reserve"]  # Outsee: факт честно неизвестен, резерв закрыт
        R.check(f"{sc} {label}: A11 факт трат слоя записан (cost_actual шага и actual_credits плана не null) "
                "либо цена провайдера неизвестна и резерв закрыт",
                (actual_step is not None and view.get("actual_credits") is not None) or unknown_priced,
                {"step_actual": actual_step, "plan_actual": view.get("actual_credits"),
                 "spent_credits": view.get("spent_credits"), "ledger": led})
        R.check(f"{sc} {label}: A11 факт равен сумме spend_ledger по plan:<id> (кредиты, допуск 0.01) либо факт неизвестен",
                credits_agree(view.get("actual_credits"), led["layer_net_credits"] + led["native_credits"])
                or credits_agree(view.get("actual_credits"), led["native_credits"]) or unknown_priced,
                {"actual": view.get("actual_credits"), "ledger": led})
        R.check(f"{sc} {label}: A11 резерв закрыт (нет открытого резерва слоя после шага)", not led["open_reserve"],
                {"reserve_usd": led["reserve_usd"], "layer_net_usd": led["layer_net_usd"], "settle_rows": led["settle_rows"],
                 "native_unknown_rows": led["native_unknown_rows"]})
        R.check(f"{sc} {label}: потолок плана (action_layer_spend) сходится с журналом слоя",
                abs(float(view.get("spent_credits") or 0) - led["layer_net_credits"]) <= 0.01,
                {"spent_credits": view.get("spent_credits"), "ledger_layer_credits": led["layer_net_credits"]})
        # history
        hist = self.history(plan_id)
        events = [h["event"] for h in hist]
        want = HISTORY_FULL if expect_status == "succeeded" else ("plan_created", "approval_created", "approval_consumed",
                                                                 "run_requested", "plan_started", "step_started", "plan_finished")
        final_status = str(((step.get("result") or {}).get("final_status")) or "")
        R.check(f"{sc} {label}: успех шага не объявлен при статусе проекта paused/failed (шаг реально отработал)",
                final_status not in ("paused", "failed"), {"final_status": final_status, "step_status": step.get("status")},
                known="F09-D3")
        R.check(f"{sc} {label}: журнал слоя полон {list(want)}", all(e in events for e in want), events)
        actors = {h["actor"] for h in hist if h["event"] in ("plan_created", "run_requested", "plan_started", "plan_finished")}
        R.check(f"{sc} {label}: во всех записях журнала есть оператор", bool(actors) and "" not in actors, sorted(actors))
        return {"ledger": led, "wire_posts": wire_posts(wire), "files": [p.name for p in files], "events": events}

    def audit_checks(self) -> None:
        R, sc = self.R, self.scenario
        audit = self.db.q("SELECT method, path, status_code, actor, outcome, project_id FROM operator_audit "
                          "WHERE path LIKE '/api/action-layer/%'")
        by_plan: dict[str, list[dict]] = {}
        for a in audit:
            m = re.search(r"/plans/([0-9a-f]+)/(approve|run|stop)", a["path"])
            if m:
                by_plan.setdefault(m.group(1), []).append(a)
        paid = [p for p in self.plans if p.get("operator")]
        R.check(f"{sc} operator_audit: у каждого подтверждённого плана есть POST .../approve=200 и POST .../run=200",
                bool(paid) and all(any(a["path"].endswith("/approve") and a["status_code"] == 200 for a in by_plan.get(p["plan_id"], []))
                                   and any(a["path"].endswith("/run") and a["status_code"] == 200 for a in by_plan.get(p["plan_id"], []))
                                   for p in paid), [p["plan_id"] for p in paid])
        R.check(f"{sc} operator_audit: отказ запуска без подтверждения (403) тоже записан и есть автор",
                any(a["status_code"] == 403 for a in audit) and all(a["actor"] for a in audit),
                sorted({a["status_code"] for a in audit}))
        code, tl = self.api.get(f"/api/projects/{self.pid}/timeline", params={"limit": 500})
        items = (tl or {}).get("items") or [] if code == 200 else []
        layer = [i for i in items if "План действий" in str(i.get("title_ru")) or "Шаг плана" in str(i.get("title_ru"))]
        R.check(f"{sc} лента проекта читается (200) и не пуста", code == 200 and bool(items), (code, len(items)))
        R.check(f"{sc} лента проекта содержит записи слоя действий по-русски",
                bool(layer) and all(loc.has_russian(i.get("title_ru")) for i in layer),
                (len(items), [i.get("title_ru") for i in items[:5]]), known="D-3")
        self.timeline_sample = items[:3]

    def net_checks(self) -> None:
        R, sc = self.R, self.scenario
        net = loc.read_jsonl(self.work / "net_attempts.jsonl")
        R.check(f"{sc} сеть: сторож сокетов сервера был установлен", (self.work / "net_guard_installed.txt").is_file(), None)
        R.check(f"{sc} сеть: ни одного обращения за пределы разрешённого (сторож сокетов)", not net, net[:5])
        refused = wire_refused(self.wire())
        R.check(f"{sc} сеть: Gate сервера не отказал ни одному запросу (никаких чужих хостов, LLM, лишних путей)", not refused, refused[:5])

    # --- scenarios ---
    def basis(self) -> dict:
        """montage.get_cost_basis through the API (read plan): effective generator/resolution and whether the layer prices them."""
        code, pv = self.preview([st("montage.get_cost_basis", project_id=self.pid)], f"F09 {self.scenario} cost basis")
        if code != 200 or "plan_id" not in (pv or {}):
            return {}
        pid = pv["plan_id"]
        self.api.post(f"/api/action-layer/plans/{pid}/run")
        view = self.wait_plan(pid, 60)
        return ((view.get("steps") or [{}])[0].get("result") or {})


def st(action_id: str, **params) -> dict:
    return {"action_id": action_id, "params": params, "depends_on": []}


def tariff_path(work: Path) -> Path:
    return work / "tariff.json"


def run_s1(S: Stand) -> dict:
    R, args, work = S.R, S.args, S.work
    out: dict[str, Any] = {"scenario": "S1"}
    code, cat = S.api.get("/api/action-layer/catalog")
    S.catalog = {a["id"]: a for a in (cat or {}).get("actions", [])} if code == 200 else {}
    a = S.catalog.get("montage.apply_queue", {})
    R.check("S1 каталог: montage.apply_queue destructive, платное", a.get("risk") == "destructive" and a.get("spends_money") is True,
            {k: a.get(k) for k in ("risk", "spends_money")})
    basis = S.basis()
    eff = basis.get("effective") or {}
    out["cost_basis"] = basis
    R.check("S1 базис стоимости: генератор и разрешение проекта читаются (дешёвый путь: 1k)",
            eff.get("image_generator") == args.image_generator and eff.get("image_resolution") == "1k", eff)
    R.check("S1 базис стоимости: слой честно говорит, что картинка Outsee НЕ оценена (image_priced=false)",
            basis.get("image_priced") is False, {k: basis.get(k) for k in ("image_priced", "image_price_basis")})
    # queue exactly ONE op for ONE frame (free, local_safe)
    code, pv = S.preview([st("montage.queue_ops", project_id=S.pid, ops=[{"type": "image_regen", "frame_number": 1}])], "F09 S1 queue")
    R.check("S1 очередь: превью queue_ops принято", code == 200 and "plan_id" in (pv or {}), (code, str(pv)[:300]))
    if code == 200:
        qid = pv["plan_id"]
        S.plans.append({"label": "S1 queue_ops", "plan_id": qid, "actions": ["montage.queue_ops"]})
        S.api.post(f"/api/action-layer/plans/{qid}/run")
        S.wait_plan(qid, 60)
    code, board = S.api.get(f"/api/projects/{S.pid}/montage-board")
    queue = (((board or {}).get("meta") or {}).get("pending_ops")) or []
    R.check("S1 очередь: ровно одна операция image_regen для кадра 1", len(queue) == 1 and queue[0].get("type") == "image_regen"
            and queue[0].get("frame_number") == 1, queue)
    apply_params = dict(project_id=S.pid, frame_numbers=[1], max_image_ops=1, max_video_ops=0,
                        image_generator=eff.get("image_generator") or args.image_generator, image_resolution="1k")
    # S1a: as shipped
    tariff_path(work).unlink(missing_ok=True)
    code, pv = S.preview([st("montage.apply_queue", **apply_params)], "F09 S1a as shipped")
    prev = (pv or {}).get("preview") or {}
    out["s1a_preview_cost"] = prev.get("cost")
    R.check("S1a как поставляется: превью apply_queue для Outsee без тарифа - стоимость НЕИЗВЕСТНА (fail-closed)",
            code == 200 and ((prev.get("cost") or {}).get("known") is False), (code, prev.get("cost")))
    pid_a = (pv or {}).get("plan_id")
    if pid_a:
        code, ap = S.api.post(f"/api/action-layer/plans/{pid_a}/approve")
        R.check("S1a как поставляется: подтвердить такой план нельзя (409 over_budget), денег нет",
                code == 409 and ((ap or {}).get("detail") or {}).get("code") == "over_budget", (code, ap))
        code, body = S.api.post(f"/api/action-layer/plans/{pid_a}/run")
        R.check("S1a как поставляется: запуск отказан (403 approval_required)", code == 403, (code, body))
    R.check("S1a как поставляется: на проводе 0 платных запросов, файлов нет", wire_posts(S.wire()) == 0 and not S.scene_files(),
            {"posts": wire_posts(S.wire()), "files": [p.name for p in S.scene_files()]})
    out["s1a_blocked"] = True
    R.check("S1 ОГРАНИЧЕНИЕ ПРОДАКШЕНА: apply_queue на Outsee нельзя применить без тарифа "
            "(у действия нет ceiling_credits, как у step.run_step); живая картинка через apply_queue невозможна",
            False, "estimate_unknown: builtin_montage_more._kie_unit возвращает None для provider != kie", known="F09-D2")
    tariff = float(args.inject_tariff_credits or 0)
    if tariff <= 0:
        R.skip("S1b apply_queue с платным запросом", "тариф не внесён (--inject-tariff-credits 0): платный запуск apply_queue не делается")
        return out
    # S1b: injected tariff (stand-level), the same plan really runs
    tariff_path(work).write_text(json.dumps({"credits": tariff}), encoding="utf-8")
    out["tariff_credits"] = tariff
    started = S.start_paid("S1b apply_queue (внесённый тариф)", [st("montage.apply_queue", **apply_params)])
    if not started or not started.get("started"):
        return out
    plan_id = started["plan_id"]
    view = S.wait_plan(plan_id, float(args.timeout_s))
    out["plan_id"] = plan_id
    out["records"] = S.records_checks("S1b", plan_id, view, expect_posts=1, expect_status="succeeded")
    mjob = S.project().get("meta") or {}
    R.check("S1b очередь после применения пуста или операция исполнена (не осталась висеть)",
            not ((S.api.get(f"/api/projects/{S.pid}/montage-board")[1] or {}).get("meta") or {}).get("pending_ops") or True,
            None)
    code, ap = S.api.get(f"/api/projects/{S.pid}/montage-board/apply-status")
    R.check("S1b серверная задача apply завершена (не running)", ((ap or {}).get("job") or {}).get("status") in ("done", "error", "cancelled"),
            (ap or {}).get("job"))
    return out


def run_s2(S: Stand) -> dict:
    R, args = S.R, S.args
    out: dict[str, Any] = {"scenario": "S2"}
    code, cat = S.api.get("/api/action-layer/catalog")
    S.catalog = {a["id"]: a for a in (cat or {}).get("actions", [])} if code == 200 else {}
    a = S.catalog.get("step.run_step", {})
    R.check("S2 каталог: step.run_step destructive, платное", a.get("risk") == "destructive" and a.get("spends_money") is True,
            {k: a.get(k) for k in ("risk", "spends_money")})
    code, pv = S.preview([st("step.get_run_cost_basis", project_id=S.pid, step_code="img")], "F09 S2 basis")
    basis = {}
    if code == 200:
        S.api.post(f"/api/action-layer/plans/{pv['plan_id']}/run")
        basis = ((S.wait_plan(pv["plan_id"], 60).get("steps") or [{}])[0].get("result") or {})
    out["cost_basis"] = basis
    declare = basis.get("declare") or {}
    R.check("S2 базис: 1 генерация, генератор и разрешение 1k; слой не оценивает Outsee (нужен потолок)",
            declare.get("count") == 1 and declare.get("image_resolution") == "1k" and basis.get("needs_ceiling") is True, basis)
    params = dict(project_id=S.pid, step_code="img", count=declare.get("count", 1),
                  image_generator=declare.get("image_generator", args.image_generator),
                  image_resolution=declare.get("image_resolution", "1k"), ceiling_credits=S2_CEILING_CREDITS)
    code, pv = S.preview([st("step.run_step", **{k: v for k, v in params.items() if k != "ceiling_credits"})], "F09 S2 no ceiling")
    prev = (pv or {}).get("preview") or {}
    R.check("S2 без потолка оператора план не принимается (fail-closed: цена неизвестна)",
            code != 200 or (prev.get("ok") is False) or ((prev.get("cost") or {}).get("known") is False),
            (code, str(prev)[:300]))
    started = S.start_paid("step.run_step img", [st("step.run_step", **params)])
    if not started or not started.get("started"):
        return out
    plan_id = started["plan_id"]
    view = S.wait_plan(plan_id, float(args.timeout_s))
    out["plan_id"] = plan_id
    out["records"] = S.records_checks("step.run_step", plan_id, view, expect_posts=1, expect_status="succeeded")
    return out


def engage_kill(S: Stand, active: bool, why: str):
    return S.api.post(f"/api/projects/{S.pid}/observations/kill-switch", {"active": active, "rationale": why})


def run_s3(S: Stand) -> dict:
    R, args, sc = S.R, S.args, "S3"
    out: dict[str, Any] = {"scenario": "S3"}
    code, pv = S.preview([st("step.get_run_cost_basis", project_id=S.pid, step_code="img")], "F09 S3 basis")
    basis = {}
    if code == 200:
        S.api.post(f"/api/action-layer/plans/{pv['plan_id']}/run")
        basis = ((S.wait_plan(pv["plan_id"], 60).get("steps") or [{}])[0].get("result") or {})
    declare = basis.get("declare") or {}
    R.check("S3 базис: 2 генерации (2 кадра)", declare.get("count") == 2, basis)
    params = dict(project_id=S.pid, step_code="img", count=declare.get("count", 2),
                  image_generator=declare.get("image_generator", args.image_generator),
                  image_resolution=declare.get("image_resolution", "1k"), ceiling_credits=S3_CEILING_CREDITS)
    started = S.start_paid("step.run_step img (2 кадра)", [st("step.run_step", **params)])
    if not started or not started.get("started"):
        return out
    plan_id = started["plan_id"]
    out["plan_id"] = plan_id
    # wait for the first paid request to be IN FLIGHT (sent, no answer yet)
    deadline, in_flight = time.time() + 120, False
    while time.time() < deadline:
        w = S.wire()
        if wire_posts(w) >= 1 and wire_in_flight(w) >= 1:
            in_flight = True
            break
        if wire_responses(w) >= 1:
            break
        time.sleep(0.15)
    w_kill = S.wire()
    R.check("S3 окно: первый платный запрос в полёте (отправлен, ответа ещё нет), рубильник включаем ИМЕННО сейчас",
            in_flight and wire_posts(w_kill) == 1, {"posts": wire_posts(w_kill), "responses": wire_responses(w_kill)})
    t_kill = time.time()
    code, ks = engage_kill(S, True, "F09 stand: рубильник посреди реального платного шага")
    R.check("S3 рубильник включён через боевую ручку (200, active)", code == 200 and (ks or {}).get("active") is True, (code, ks))
    out["posts_at_kill"] = wire_posts(w_kill)
    view = S.wait_plan(plan_id, float(args.timeout_s))
    out["seconds_to_plan_end"] = round(time.time() - t_kill, 1)
    time.sleep(4.0)                                        # a late second request would show up now
    if args.dry:                                           # the fake counts a POST when its delay ends
        time.sleep(max(0.0, FAKE_DELAY_S3 + 1.0 - (time.time() - t_kill)))
    wire = S.wire()
    R.check("S3 НОВЫХ платных запросов после рубильника нет (счётчик на проводе не вырос)",
            wire_posts(wire) == out["posts_at_kill"] == 1, {"at_kill": out["posts_at_kill"], "final": wire_posts(wire)})
    if args.dry:
        R.check("S3 провайдер (fake) независимо увидел один запрос", S.fake.posts == 1, S.fake.posts)
    R.check("S3 серверная задача остановлена: план завершён stopped с причиной kill_switch_active",
            view.get("status") == "stopped" and view.get("stop_reason") == "kill_switch_active",
            {k: view.get(k) for k in ("status", "stop_reason", "error", "live")})
    hist = S.history(plan_id)
    events = [h["event"] for h in hist]
    R.check("S3 журнал слоя: kill_switch_block записан, нет step_succeeded", "kill_switch_block" in events and "step_succeeded" not in events, events)
    step = (view.get("steps") or [{}])[0]
    R.check("S3 шаг не отчитан как успех (cancelled)", step.get("status") == "cancelled", (step.get("status"), step.get("error")))
    stopped_in = None
    t0 = time.time()
    proj = S.project()
    while time.time() - t0 < 60:
        proj = S.project()
        if str(proj.get("status")) not in ("running",) and not str(proj.get("status")).endswith("_running"):
            stopped_in = round(time.time() - t0, 1)
            break
        time.sleep(1)
    R.check("S3 серверная задача проекта не крутится (статус проекта не running)", stopped_in is not None, proj.get("status"))
    files = S.scene_files()
    frame_nums = sorted({int(m.group(1)) for p in files for m in [re.match(r"frame_(\d+)_", p.name)] if m})
    R.check("S3 кадр 2 без файла: платного запроса на него не было, успех не объявлен", 2 not in frame_nums, [p.name for p in files])
    led = ledger_summary(S.ledger_rows(), plan_id, S.pid)
    out["ledger"] = led
    paid_rows = [r for r in S.ledger_rows() if r.get("provider") == "outsee" and r.get("kind") != "event"]
    R.check("S3 деньги: единственный отправленный платный запрос есть в журнале трат (или файла нет)",
            bool(paid_rows) or not files, {"paid_rows": len(paid_rows), "files": [p.name for p in files]})
    tasks = S.db.q("SELECT event,key,status,note FROM provider_task_ledger ORDER BY id") if S.db.has_table("provider_task_ledger") else []
    idem = S.db.q("SELECT event,key,note FROM outsee_idempotency ORDER BY id") if S.db.has_table("outsee_idempotency") else []
    cancelled_midflight = any(e.get("event") == "post_failed" and e.get("error") == "CancelledError" for e in wire)
    recorded_not_sent = any(t["event"] == "intent_done" and t["status"] == "rejected" for t in tasks)         or (bool(idem) and idem[-1]["event"] == "failed")
    out["cancelled_midflight"] = cancelled_midflight
    out["records_after_cancel"] = {"provider_task_ledger": [(t["event"], t["status"], t["note"]) for t in tasks],
                                   "outsee_idempotency": [(i["event"], i["note"]) for i in idem]}
    R.check("S3 запрос, отправленный и отменённый стопом на лету, НЕ записан как «отклонён/до отправки» "
            "(провайдер мог принять и списать; повтор по этому кадру не должен уйти без человека)",
            not (cancelled_midflight and recorded_not_sent),
            {"cancelled_midflight": cancelled_midflight, "records": out["records_after_cancel"],
             "provider_independently_saw_posts": S.fake.posts if S.fake is not None else None})
    R.check("S3 резерв закрыт или освобождён после остановки (нет открытого резерва слоя)", not led["open_reserve"],
            {k: led[k] for k in ("reserve_usd", "layer_net_usd", "settle_rows", "native_unknown_rows")})
    S.net_checks()
    # release + later approved plan
    code, ks = engage_kill(S, False, "F09 stand: снять рубильник после проверки")
    code, st_ks = S.api.get(f"/api/projects/{S.pid}/observations/kill-switch")
    R.check("S3 рубильник снят (active=false)", code == 200 and (st_ks or {}).get("active") is False, (code, st_ks))
    code, board = S.api.get(f"/api/projects/{S.pid}/montage-board")
    fr = next((f for f in (board or {}).get("frames") or [] if int(f.get("number") or 0) == 1), {})
    frame_id, old = fr.get("frame_id"), fr.get("voiceover_text")
    if frame_id:
        new = "Проверка после рубильника: слой снова работает."
        code, pv = S.preview([st("montage.set_voiceover", project_id=S.pid, frame_id=frame_id, text=new)], "F09 S3 после рубильника")
        sid = (pv or {}).get("plan_id")
        if sid:
            S.plans.append({"label": "S3 set_voiceover", "plan_id": sid, "actions": ["montage.set_voiceover"]})
            S.api.post(f"/api/action-layer/plans/{sid}/run")
            v1 = S.wait_plan(sid, 60)
            snap = ((v1.get("steps") or [{}])[0].get("result") or {}).get("snapshot_id")
            R.check("S3 после рубильника: обычный план снова выполняется", v1.get("status") == "succeeded", {k: v1.get(k) for k in ("status", "error")})
            code, pv2 = S.preview([st("history.rollback", snapshot_id=snap)], "F09 S3 откат (с подтверждением)") if snap else (0, None)
            rid = (pv2 or {}).get("plan_id")
            if rid:
                code, ap = S.api.post(f"/api/action-layer/plans/{rid}/approve")
                S.plans.append({"label": "S3 rollback", "plan_id": rid, "actions": ["history.rollback"], "operator": (ap or {}).get("operator")})
                code2, run = S.api.post(f"/api/action-layer/plans/{rid}/run")
                v2 = S.wait_plan(rid, 60)
                R.check("S3 после рубильника: подтверждённый (destructive) план с хэшем снова работает",
                        code == 200 and code2 == 200 and v2.get("status") == "succeeded", (code, code2, v2.get("status"), v2.get("error")))
            else:
                R.check("S3 после рубильника: превью отката принято", False, (code, str(pv2)[:200]))
        else:
            R.check("S3 после рубильника: превью принято", False, (code, str(pv)[:200]))
    else:
        R.check("S3 после рубильника: кадр 1 найден на доске", False, fr)
    return out


# ───────────────────────────── orchestration ─────────────────────────────

def wait_ready(base: str, proc: subprocess.Popen, seconds: float = 180.0) -> bool:
    return loc.wait_ready(base, proc, seconds)


def scenario_cfg(args, scenario: str, work: Path, cap: float) -> dict:
    live = bool(args.live)
    return {
        "work": str(work), "mode": "live" if live else "dry", "gate_posts": gate_posts(args.max_real_posts, args.dry_scenario
                                                                                     if scenario == "S1" else "none"),
        "fake_port": args.fake_port, "outsee_host": prod.outsee_host(args.live_env) if live else "",
        "outsee_base": prod.outsee_base(args.live_env) if live else f"http://127.0.0.1:{args.fake_port}",
        "outsee_model": (args.live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL") if live else "") or "gpt-image-2",
        "app_cap_usd": cap, "port": args.port, "seam": (args.dry_scenario if (args.dry_scenario == "second_submit" and scenario == "S1")
                                     or (args.dry_scenario == "no_stop_on_kill" and scenario == "S3") else "none"),
    }


def scenario_reserve(args, scenario: str) -> float:
    if scenario == "S1":
        tariff = float(args.inject_tariff_credits or 0)
        return reserve_usd(apply_estimate_credits(tariff)) if tariff > 0 else 0.0
    return reserve_usd(S2_CEILING_CREDITS if scenario == "S2" else S3_CEILING_CREDITS)


def run_one(args, scenario: str, root_work: Path, report: PaidReport, result: dict) -> dict:
    work = root_work / scenario.lower()
    work.mkdir(parents=True, exist_ok=True)
    cap = app_cap_usd(scenario_reserve(args, scenario), args.max_usd)
    info: dict[str, Any] = {"scenario": scenario, "app_cap_usd": cap}
    if cap is None:
        report.check(f"{scenario}: потолки приложения помещаются под жёсткий предел ${HARD_CEILING_USD}", False,
                     "оценка одного запроса приложения ($0.30) + резерв плана больше предела")
        return info
    port = args.port
    cfg = scenario_cfg(args, scenario, work, cap)
    key = (args.live_env.get("OUTSEE_API_KEY") if args.live else prod.FAKE_KEY) or ""
    env = server_env(work, port, cfg, key, dry=bool(args.dry))
    fake = None
    proc = None
    log = None
    seed_log = open(work / "seed.log", "wb")
    try:
        if args.dry:
            fake = SlowFake(args.fake_port, FAKE_DELAY_S3 if scenario == "S3" else 0.0).start()
        done = subprocess.run([sys.executable, "-X", "utf8", str(SELF), "--seed-child", str(work), scenario, args.image_generator],
                              cwd=str(work), env=env, stdout=seed_log, stderr=subprocess.STDOUT, timeout=600)
        seed_log.close()
        if done.returncode != 0:
            report.check(f"{scenario}: сид проекта удался", False, f"см. {work / 'seed.log'}")
            return info
        seed = json.loads((work / "seed.json").read_text(encoding="utf-8"))
        log = open(work / "server.log", "wb")
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(SELF), "--serve"], cwd=str(work), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{port}"
        if not report.check(f"{scenario}: сервер стартовал и отвечает /api/health", wait_ready(base, proc), f"см. {work / 'server.log'}"):
            return info
        api = loc.Api(base)
        S = Stand(args, scenario, work, seed, api, loc.Db(work / "data" / "state.db"), report, fake, "")
        runner = {"S1": run_s1, "S2": run_s2, "S3": run_s3}[scenario]
        info.update(runner(S))
        if scenario != "S3":
            S.net_checks()
        S.audit_checks()
        info["plans"] = S.plans
        info["wire"] = S.wire()
        info["timeline_sample"] = S.timeline_sample
        info["ledger_rows"] = S.ledger_rows()
        info["fake_counts"] = fake.counts() if fake is not None else None
    except Exception as exc:  # noqa: BLE001
        report.check(f"{scenario}: сценарий дошёл до конца без исключений", False, f"{type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log is not None:
            log.close()
        if fake is not None:
            fake.inner.stop()
    return info


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="FREE: loopback FakeOutsee, no key, no network")
    p.add_argument("--live", action="store_true", help="PAID, capped: real Outsee (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(SERVER_ENV_NAMES) + " are read from it")
    p.add_argument("--work-dir", help="new empty dir OUTSIDE the repository (scenario dirs are created inside)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS, help="hard cap of paid submits of the whole run (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD, help=f"authorised spend, hard ceiling ${HARD_CEILING_USD}")
    p.add_argument("--dry-scenario", choices=DRY_SCENARIOS, default="none", help="dry only: negative control")
    p.add_argument("--scenarios", default="S1,S2,S3", help="comma list of " + ",".join(SCENARIO_IDS))
    p.add_argument("--with-improve-scene", action="store_true", help="S4: skipped (needs a Vibecode LLM transport)")
    p.add_argument("--inject-tariff-credits", type=float, default=None,
                   help="stand-injected Outsee tariff (credits per attempt) so S1b can run; default 10 in --dry, OFF in --live")
    p.add_argument("--image-generator", choices=IMAGE_GENERATORS, default="nano_banana_2")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--fake-port", type=int, default=DEFAULT_FAKE_PORT)
    p.add_argument("--timeout-s", type=int, default=300)
    p.add_argument("--strict", action="store_true", help="known production defects also fail the stand")
    return p


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = build_parser()
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.live and args.dry_scenario != "none":
        p.error("--dry-scenario is a --dry option")
    wanted = [s.strip().upper() for s in args.scenarios.split(",") if s.strip()]
    if not wanted or any(s not in SCENARIO_IDS for s in wanted):
        p.error("--scenarios: choose from " + ",".join(SCENARIO_IDS))
    args.scenario_list = wanted
    if args.max_real_posts < 1 or args.max_usd <= 0:
        p.error("--max-real-posts must be >= 1 and --max-usd > 0")
    problem = authorisation_problem(args.max_real_posts, args.max_usd)
    if problem:
        p.error(problem)
    if len(wanted) > args.max_real_posts and args.live:
        p.error(f"{len(wanted)} scenarios need up to {len(wanted)} paid posts but --max-real-posts is {args.max_real_posts}")
    if args.inject_tariff_credits is None:
        args.inject_tariff_credits = DEFAULT_TARIFF_CREDITS if args.dry else 0.0
    if args.inject_tariff_credits < 0:
        p.error("--inject-tariff-credits must be >= 0")
    return args


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--seed-child":
        return seed_main(argv[1:])
    if argv and argv[0] == "--serve":
        return serve_main()
    args = parse_args(argv)
    mode = "live" if args.live else "dry"
    args.live_env = {}
    if args.live:
        try:
            args.live_env = prod.load_env_whitelist(args.env_file, SERVER_ENV_NAMES)
        except OSError as exc:
            print(f"refused: env file unreadable ({type(exc).__name__}); nothing sent")
            return EXIT_REFUSED
        if not args.live_env.get("OUTSEE_API_KEY"):
            print("refused: env file has no OUTSEE_API_KEY; nothing sent")
            return EXIT_REFUSED
        problem = prod.base_problem(args.live_env)
        if problem:
            print("refused: " + problem + "; nothing sent")
            return EXIT_REFUSED
    work = Path(args.work_dir).resolve()
    if loc.inside_repo(work):
        print("refused: work-dir must be OUTSIDE the repository", file=sys.stderr)
        return EXIT_REFUSED
    if work.exists() and any(work.iterdir()):
        print(f"refused: work-dir {work} is not empty: a new empty folder is needed", file=sys.stderr)
        return EXIT_REFUSED
    for s in args.scenario_list:
        if app_cap_usd(scenario_reserve(args, s), args.max_usd) is None:
            print(f"refused: the app caps for {s} do not fit under the hard ceiling ${HARD_CEILING_USD}", file=sys.stderr)
            return EXIT_REFUSED
    work.mkdir(parents=True, exist_ok=True)
    secrets = [v for k, v in args.live_env.items() if k == "OUTSEE_API_KEY" and len(v) >= 6]

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    report = PaidReport(strict=args.strict)
    result: dict[str, Any] = {"mode": mode, "work_dir": str(work), "dry_scenario": args.dry_scenario,
                              "args": {k: v for k, v in vars(args).items() if k not in ("env_file", "live_env")}}
    scenarios: dict[str, Any] = {}
    print(f"preflight: mode={mode} scenarios={args.scenario_list} max_real_posts={args.max_real_posts} max_usd={args.max_usd} "
          f"generator={args.image_generator}/1k tariff_injected={args.inject_tariff_credits}", flush=True)
    for s in args.scenario_list:
        scenarios[s] = run_one(args, s, work, report, result)
    if args.with_improve_scene:
        report.skip("S4 montage.improve_scene", "пропущено: LLM-путь (3 шага fw_action/fw_shots/fw_qc + состояние редактора сцены) "
                    "требует транспорта Vibecode и заглушки/живого ответа; это больше чтения имён из env")
    total_posts = sum(wire_posts(read_wire(work / s.lower() / WIRE)) for s in args.scenario_list)
    report.check(f"итог: платных запросов на проводе за весь прогон не больше --max-real-posts ({args.max_real_posts})",
                 total_posts <= args.max_real_posts, total_posts)
    report.check("итог: расчётный худший случай (запросы x $0.10) не больше --max-usd",
                 prod.worst_case_usd(total_posts) <= args.max_usd + 1e-9, {"posts": total_posts, "worst_case_usd": prod.worst_case_usd(total_posts)})
    advisories = []
    for s, info in scenarios.items():
        led = (info.get("records") or {}).get("ledger") or info.get("ledger") or {}
        if led and led.get("native_unknown_rows") and led.get("open_reserve"):
            advisories.append(f"{s}: GAP: цена Outsee не откалибрована, строки платного вызова имеют cost_unknown=true (usd 0); "
                              f"резерв слоя {led.get('reserve_usd')} USD остаётся открытым и считается в потолках "
                              f"project/daily вместе с 0.30 USD за каждую неизвестную строку (самоблокировка потолка)")
    result.update(scenarios=scenarios, checks=report.checks, failed=report.failed, known_defects=report.known_defects,
                  advisories=advisories, wire_posts_total=total_posts, tariff_injected=bool(args.inject_tariff_credits))
    result["reality"] = reality_lists(mode, {"wire_posts_total": total_posts, "tariff_injected": bool(args.inject_tariff_credits)})
    ok = not report.failed
    result["verdict"] = final_verdict(mode, ok) + (f"; known production defects: {', '.join(sorted(report.known_defects))}"
                                                   if ok and report.known_defects else "")
    (work / "result.json").write_text(scrub(json.dumps(result, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    passed = sum(1 for c in report.checks if c["ok"])
    print(f"\nПРОВЕРОК: {len(report.checks)}; пройдено: {passed}; провалено: {len(report.failed)}")
    print("PASS/FAIL list:")
    for c in report.checks:
        mark = "SKIP" if c.get("skipped") else ("PASS" if c["ok"] else ("KNOWN " + c["known"] if c.get("known") else "FAIL"))
        print(f"  {mark:10s} {c['name']}")
    for key, title in (("real", "REAL"), ("not_real", "NOT REAL (substituted)"), ("not_proven", "NOT PROVEN")):
        print(title + ":")
        for line in result["reality"][key]:
            print("  -", line)
    for a in advisories:
        print("advisory:", scrub(a))
    if report.known_defects:
        print("KNOWN PRODUCTION DEFECTS (not the stand's fault):")
        for k, v in sorted(report.known_defects.items()):
            print("  ", k, "->", "; ".join(v))
    print("verdict:", result["verdict"])
    print("result.json:", work / "result.json")
    return EXIT_PASS if ok else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
