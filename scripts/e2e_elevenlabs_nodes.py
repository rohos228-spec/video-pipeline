"""Media nodes `sfx_gen` and `audio` (ElevenLabs, DIRECT API) through the PRODUCTION seams, with spend recording and
loss recovery. At most ONE real SFX POST and ONE real TTS POST.

Sibling of scripts/e2e_media_production_route.py (same modes, same work dir / settings patching / result.json / caps
discipline, none of its files edited). That stand proves the Outsee `images` node; this one proves the two ElevenLabs
nodes whose recovery has NO provider task id:

  * `sfx_gen`: the real native step generate_sfx.run -> sfx_gen.generate_sfx_files -> _elevenlabs_sfx (paid_seam +
    track_eleven_sfx). Idempotence is the per-event checkpoint ai_jobs["sfx_progress"] / ["sfx_files"] (there is NO request-key
    sidecar for SFX), a "sent, no answer" mark (inflight) blocks an automatic resend;
  * `audio`: the real TTS seam synthesize_speech (paid_call + track_eleven_post + request-key sidecar *.tts.json + the
    .tts_uncertain_<key> marker), inside tts_run_budget exactly as generate_audio._run wraps it. The Whisper alignment that
    generate_audio runs AFTER the paid POST is NOT part of this stand (it is local, free, needs a model and a text of 50+
    characters, while the live cap here is 40 characters).

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no .env: the httpx transport is replaced (httpx.AsyncClient.send) by a local fake that answers the
          two ElevenLabs endpoints with a real, decodable audio file (ffmpeg mp3, or an in-process WAV when ffmpeg cannot
          encode), so the real verification (ffprobe + levels) runs. Every other request is refused and counted.
  --live  PAID, capped: the same flow, but the ONE SFX POST (duration <= 1.0 s, 'short soft click') and the ONE TTS POST
          (text <= 40 characters, a short Russian phrase) go to the real api.elevenlabs.io. Needs --env-file; ONLY the
          names ELEVENLABS_API_KEY (and optional ELEVENLABS_PROXY_URL) are read from it, nothing is printed.
          Caps: --max-real-posts (default 2 total, 1 per kind), --max-usd (default 0.05; refused when the conservative
          price of the planned real POSTs exceeds it). Everything the loss scenarios need (a timeout after send, a killed
          process) is SIMULATED locally BEFORE the wire in both modes: it never costs a request.
          NOT RUN in live mode when written.

Scenarios (both modes):
  sfx_gen  S1  one real POST; the tail of the step is made to fail (outcome loss) -> the native checkpoint keeps the paid
               event; the rerun INSIDE media_spend.resume_only() finishes with zero paid-seam hits; the supervised
               media_local_resume (resume_local) verifies the file with zero new paid requests; a plain rerun makes no POST;
               the lost FILE is not silently resent under resume-only.
           S2  (simulated) the process dies after the POST was sent: the inflight mark blocks an automatic resend, the event
               gets a local substitute with may_have_charged=uncertain, recovery assess says unknown_outcome (denied).
  audio    A1  one real POST; file + sidecar + spend row; a rerun is answered from the sidecar (no POST); the answer the
               node held is lost -> the sidecar copy is taken without a POST also under resume-only; the supervised
               media_local_resume (resume_local) verifies it; a different text under resume-only is refused before the wire.
           A2  (simulated) a read timeout after send: the .tts_uncertain_ marker is written and blocks every automatic
               resend; recovery assess says unknown_outcome_elevenlabs (denied); the failure is typed in the journal.

What is real: the whole production code of both seams, spend_guard (kill switch, disk guard, caps), media_spend recording
(rows in the scratch spend_ledger with project / run / request correlation), the checkpoints, the verification
(audio_verification + media_verification._probe), media_recovery / media_local_resume. In --live also the two requests.
What is NOT real: the seeded projects, the injected losses, the provider in --dry, the voice quality (never judged), the
journal of the full observation (facts are collected from observation.emit), and Whisper alignment of the audio node.

Usage (repository root, project venv):
    python scripts/e2e_elevenlabs_nodes.py --dry --node both --work-dir <empty dir outside the repository>
    python scripts/e2e_elevenlabs_nodes.py --live --node both --work-dir <dir> --env-file <env file>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import uuid as _uuid
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard  # noqa: E402  (A19: refuses to touch live data)

ELEVEN_HOST = "api.elevenlabs.io"
TTS_PATH_RE = re.compile(r"^/v1/text-to-speech/[^/?#]+$")
SFX_PATH = "/v1/sound-generation"
ENV_NAMES = ("ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL")
SECRET_NAMES = ("ELEVENLABS_API_KEY", "ELEVENLABS_PROXY_URL")
FAKE_KEY = "fake-elevenlabs-dry-key"

# Conservative prices of the planned real POSTs: the application's own UNCALIBRATED constants
# (media_spend.ELEVENLABS_USD_PER_CHAR / ELEVENLABS_SFX_USD_PER_SEC; a test keeps them equal).
USD_PER_CHAR_HINT = 0.0003
USD_PER_SFX_SEC_HINT = 0.012
TTS_TEXT = "Папка лежит на столе у окна."          # 28 characters
TTS_TEXT_UNCERTAIN = "Чашка стоит рядом с блокнотом."   # simulated timeout scenario (never leaves the machine)
TTS_TEXT_OTHER = "Совсем другой текст."             # refused under resume-only
TTS_MAX_CHARS = 40
SFX_MAX_SECONDS = 1.0
SFX_PROMPT = "short soft click"
SFX_EVENT_SECONDS = 1.0
SFX_MAX_PROMPT_CHARS = 80
DEFAULT_MAX_REAL_POSTS = 2
DEFAULT_MAX_USD = 0.05
SIM_ALLOWANCE_USD = 0.10     # simulated losses are recorded as estimated rows too; they must not trip the application caps
NODES = ("sfx_gen", "audio", "both")


class ProcessDied(BaseException):
    """Stands in for a killed process: not caught by any `except Exception` of the production code."""


# ───────────────────────────── pure helpers (unit-tested) ─────────────────────────────

def load_env_whitelist(path: str, names: tuple[str, ...] = ENV_NAMES) -> dict[str, str]:
    """Only `names` from an env file; everything else is ignored and nothing is printed."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in names:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def nodes_of(node: str) -> tuple[str, ...]:
    return ("sfx_gen", "audio") if node == "both" else (node,)


def planned_real_posts(node: str) -> int:
    return len(nodes_of(node))


def worst_case_usd(node: str) -> float:
    total = 0.0
    if "sfx_gen" in nodes_of(node):
        total += SFX_EVENT_SECONDS * USD_PER_SFX_SEC_HINT
    if "audio" in nodes_of(node):
        total += TTS_MAX_CHARS * USD_PER_CHAR_HINT
    return round(total, 6)


def authorisation_problem(node: str, max_real_posts: int, max_usd: float) -> str | None:
    """The stand refuses a run whose real POSTs or conservative worst case exceed what it was authorised to do."""
    if max_real_posts < 1 or max_usd <= 0:
        return "--max-real-posts must be >= 1 and --max-usd > 0"
    if planned_real_posts(node) > max_real_posts:
        return f"--node {node} needs {planned_real_posts(node)} real POST(s), --max-real-posts is {max_real_posts}"
    if worst_case_usd(node) > max_usd + 1e-9:
        return f"worst case ${worst_case_usd(node)} of the planned real POSTs exceeds --max-usd {max_usd}"
    return None


def app_cap_usd(node: str) -> float:
    """Application caps (run / project / daily): the real POSTs plus room for the simulated losses' estimate rows."""
    return round(worst_case_usd(node) + SIM_ALLOWANCE_USD, 2)


def is_loopback(host: str | None) -> bool:
    h = (host or "").strip("[]").lower()
    return h in ("127.0.0.1", "localhost", "::1")


def check(name: str, ok: bool, detail: Any = None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    failed = [c["name"] for c in checks if not c["ok"]]
    return not failed, failed


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return "PASS (all checks, live: real ElevenLabs POSTs within the caps; machinery proven end to end on the real provider)"
    return "DRY OK (fake transport: machinery proven offline, NOT a live proof)"


@dataclass
class Judgement:
    action: str            # pass | fault | refuse
    kind: str              # tts | sfx | other
    reason: str = ""
    fault: str | None = None


@dataclass
class Gate:
    """Every outgoing request is judged here (pure bookkeeping, no I/O): the send hook obeys it.

    Only POST https://api.elevenlabs.io/v1/text-to-speech/<voice> and /v1/sound-generation exist for the stand. A simulated
    loss (`arm`) is delivered INSTEAD of the request: it never reaches the wire and is counted separately."""
    mode: str                                   # dry | live
    max_real_posts: int = DEFAULT_MAX_REAL_POSTS
    max_per_kind: int = 1
    host: str = ELEVEN_HOST
    wire: list = field(default_factory=list)    # posts let through (dry: answered by the fake, live: sent for real)
    sim: list = field(default_factory=list)     # simulated losses delivered
    refused: list = field(default_factory=list)
    fault: str | None = None

    def arm(self, fault: str | None) -> None:
        self.fault = fault

    @property
    def total_attempts(self) -> int:
        return len(self.wire) + len(self.sim)

    def count(self, kind: str) -> int:
        return sum(1 for w in self.wire if w["kind"] == kind)

    def attempts(self, kind: str) -> int:
        """Wire posts plus delivered simulated losses of one kind: the number of times production tried to POST."""
        return self.count(kind) + sum(1 for x in self.sim if x["kind"] == kind)

    def _refuse(self, kind: str, why: str, reason: str) -> Judgement:
        self.refused.append({"kind": kind, "why": why, "reason": reason[:160]})
        return Judgement("refuse", kind, reason)

    def judge(self, method: str, scheme: str, host: str | None, path: str, body: Any) -> Judgement:
        method, host_l = method.upper(), (host or "").strip("[]").lower()
        kind = "tts" if TTS_PATH_RE.match(path or "") else "sfx" if path == SFX_PATH else "other"
        if not (method == "POST" and scheme == "https" and host_l == self.host and kind != "other"):
            return self._refuse("other", "foreign", f"refused: {method} {scheme}://{host_l}{path} is not on the allow list")
        body = body if isinstance(body, dict) else {}
        if kind == "tts":
            if len(str(body.get("text") or "")) > TTS_MAX_CHARS:
                return self._refuse(kind, "tts_too_long", f"tts text longer than {TTS_MAX_CHARS} characters")
        else:
            try:
                dur = float(body.get("duration_seconds"))
            except (TypeError, ValueError):
                dur = 99.0
            if dur > SFX_MAX_SECONDS + 1e-9 or len(str(body.get("text") or "")) > SFX_MAX_PROMPT_CHARS:
                return self._refuse(kind, "sfx_too_long", f"sfx duration {dur} s / prompt too long")
        if self.fault:
            fault, self.fault = self.fault, None
            self.sim.append({"kind": kind, "fault": fault})
            return Judgement("fault", kind, "simulated loss", fault=fault)
        if len(self.wire) >= self.max_real_posts:
            return self._refuse(kind, "paid_cap", f"real post cap reached ({self.max_real_posts})")
        if self.count(kind) >= self.max_per_kind:
            return self._refuse(kind, "kind_cap", f"only {self.max_per_kind} real {kind} post(s) allowed")
        self.wire.append({"kind": kind, "chars": len(str(body.get("text") or ""))})
        return Judgement("pass", kind)


# ───────────────────────────── audio fixtures (dry) ─────────────────────────────

_FIXTURES: dict[float, bytes] = {}


def make_audio(seconds: float) -> bytes:
    """A real, decodable audio file of `seconds`: an ffmpeg mp3 (what ElevenLabs returns), or an in-process WAV
    when ffmpeg cannot encode. Either passes the application's verification (ffprobe + levels)."""
    key = round(float(seconds), 3)
    if key in _FIXTURES:
        return _FIXTURES[key]
    data: bytes | None = None
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "x.mp3"
            try:
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={key}",
                                "-c:a", "libmp3lame", str(out)], check=True, timeout=60, capture_output=True)
                data = out.read_bytes()
            except Exception:  # noqa: BLE001 - no encoder: the WAV below
                data = None
    if not data:
        import io
        import math
        import struct
        rate = 22050
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / rate)))
                                   for i in range(int(rate * key))))
        data = buf.getvalue()
    _FIXTURES[key] = data
    return data


# ───────────────────────────── environment and honest reporting ─────────────────────────────

def env_overrides(data: Path, *, cap: float, dry: bool) -> dict[str, str]:
    """Environment of the in-process app (applied BEFORE the application is imported). Never a real key here."""
    out = {
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "ELEVENLABS_API_KEY": FAKE_KEY, "ELEVENLABS_PROXY_URL": "",
        "SPEND_DAILY_CAP_USD": str(cap), "SPEND_PROJECT_CAP_USD": str(cap), "SPEND_RUN_CAP_USD": str(cap),
        "MEDIA_SPEND_RECORDING": "true", "SFX_ENABLED": "true",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "false", "HITL_AUTO_APPROVE": "false", "BROWSER_CDP_AUTO_RECOVER": "false",
        "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false", "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
        "KIE_API_KEY": "", "OUTSEE_API_KEY": "",
    }
    if dry:  # a free-space threshold that does not depend on the machine
        out.update({"DISK_GUARD_LOW_FREE_GB": "0.001", "DISK_GUARD_CRITICAL_FREE_GB": "0.0005"})
    return out


def reality_report(mode: str, counters: dict) -> dict:
    live = mode == "live"
    real = counters.get("wire_posts", 0) if live else 0
    return {
        "real": [x for x in (
            "production seams in-process: generate_sfx.run / sfx_gen._elevenlabs_sfx (paid_seam, track_eleven_sfx, checkpoints) "
            "and elevenlabs_api.synthesize_speech (paid_call, track_eleven_post, sidecar, uncertain marker)",
            "spend_guard (kill switch, disk guard, caps), media_spend recording into the scratch spend_ledger, "
            "media_recovery / media_local_resume, audio_verification and media_verification",
            f"{real} real ElevenLabs POST(s)" if real else None) if x],
        "not_real": [x for x in (
            "seeded projects: synthetic, built by this script (one sfx event / one TTS phrase)",
            "the losses (tail of the step, killed process, read timeout after send): injected locally before the wire",
            None if live else "the provider: a local fake transport (nothing left the machine, nothing was paid)",
            "voice and sound quality: never judged",
            "Whisper alignment of the audio node and the full observation journal (facts are collected from emit)") if x],
    }


def reality_not_proven(mode: str) -> list[str]:
    out = ["the price of a real ElevenLabs request is not calibrated by the application (estimated rows, uncalibrated)",
           "sfx_gen has no request-key sidecar: the only idempotence is the per-event checkpoint; a lost event FILE under a "
           "normal (not resume-only) rerun is a new paid POST by design"]
    if mode != "live":
        out.insert(0, "anything about the real ElevenLabs: this is a dry run on a fake transport, NOT a live proof")
    return out


# ───────────────────────────── runtime ─────────────────────────────

def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--node", choices=NODES, default="both", help="sfx_gen | audio | both")
    p.add_argument("--dry", action="store_true", help="FREE: fake transport, nothing leaves the machine")
    p.add_argument("--live", action="store_true", help="PAID, capped: at most one real SFX POST and one real TTS POST")
    p.add_argument("--env-file", help="env file; only " + ", ".join(ENV_NAMES) + " are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, result.json (recreated)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS, help="hard cap of real paid POSTs (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help="authorised spend; refused when the conservative price of the planned real POSTs exceeds it")
    p.add_argument("--no-proxy", action="store_true",
                   help="ignore ELEVENLABS_PROXY_URL from the env file (direct route; for a proxy that drops connections)")
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    problem = authorisation_problem(args.node, args.max_real_posts, args.max_usd)
    if problem:
        p.error(problem)
    return args


class Rec:
    """Collects the checks of the scenarios and the facts for result.json."""

    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.facts: dict[str, Any] = {}

    def check(self, name: str, ok: bool, detail: Any = None) -> bool:
        self.checks.append(check(name, ok, detail))
        print(("PASS " if ok else "FAIL ") + name, flush=True)
        return bool(ok)


def _spend_rows(state_db: Path, project_id: int | None = None) -> list[dict]:
    if not state_db.exists():
        return []
    try:
        with sqlite3.connect(state_db.as_uri() + "?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            rows = [dict(r) for r in db.execute(
                "SELECT provider,kind,usd,source,note,project_id,run_id,request_id,ref FROM spend_ledger ORDER BY id")]
    except sqlite3.Error:
        return []
    return [r for r in rows if r["provider"] == "elevenlabs" and (project_id is None or r["project_id"] == project_id)]


def _correlated(row: dict, pid: int) -> bool:
    return row.get("project_id") == pid and bool(row.get("run_id")) and bool(row.get("request_id"))


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    if mode == "live":
        live_env = load_env_whitelist(args.env_file, ENV_NAMES)
        if not live_env.get("ELEVENLABS_API_KEY"):
            print("env file has no ELEVENLABS_API_KEY; nothing sent")
            return 3
        if args.no_proxy:
            live_env.pop("ELEVENLABS_PROXY_URL", None)
        proxy = live_env.get("ELEVENLABS_PROXY_URL")
        if proxy and urlsplit(proxy).scheme not in ("http", "https", "socks5", "socks5h"):
            print("ELEVENLABS_PROXY_URL has an unsupported scheme; nothing sent")
            return 3
    if not (shutil.which("ffprobe") and shutil.which("ffmpeg")):
        print("ffmpeg/ffprobe are required (the real verification runs on real files); nothing sent")
        return 3
    secrets = sorted((v for k, v in live_env.items() if k in SECRET_NAMES and len(v) >= 6), key=len, reverse=True)

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)  # A19: refuses live data; removes only <work>/data made by these scripts
    os.chdir(ROOT)
    cap = app_cap_usd(args.node)
    os.environ.update(env_overrides(data, cap=cap, dry=(mode == "dry")))
    if mode == "live":
        os.environ["ELEVENLABS_API_KEY"] = live_env["ELEVENLABS_API_KEY"]
        os.environ["ELEVENLABS_PROXY_URL"] = live_env.get("ELEVENLABS_PROXY_URL", "")

    import httpx
    from app.settings import settings
    # Belt and braces over environment precedence (a stray repository .env must not add a route or a key):
    settings.elevenlabs_api_key = live_env["ELEVENLABS_API_KEY"] if mode == "live" else FAKE_KEY
    settings.elevenlabs_proxy_url = (live_env.get("ELEVENLABS_PROXY_URL") or None) if mode == "live" else None
    settings.kie_api_key = ""
    settings.media_spend_recording = True
    settings.sfx_enabled = True
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap
    for guarded in (settings.data_dir, settings.sqlite_path):  # no write outside the work dir, ever
        if not guard._inside(Path(guarded).resolve(), work):
            raise guard.refuse(f"настройки указывают на {guarded}, это вне рабочей папки {work}; ничего не отправлено.")

    from app import main as app_main
    from app.services import media_spend, observation
    await app_main._init_db()

    gate = Gate(mode=mode, max_real_posts=args.max_real_posts)
    facts: list[dict] = []
    original_send = httpx.AsyncClient.send
    original_emit = observation.emit

    async def send(client, request, **kwargs):
        try:
            body = json.loads(request.content.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            body = {}
        judged = gate.judge(request.method, request.url.scheme, request.url.host, request.url.path, body)
        if judged.action == "refuse":
            raise RuntimeError("stand: " + judged.reason)
        if judged.action == "fault":
            if judged.fault == "kill":
                raise ProcessDied("simulated: the process died after the request was sent")
            raise httpx.ReadTimeout("simulated: read timeout after the request was sent", request=request)
        if mode == "dry":
            if judged.kind == "tts":
                audio = make_audio(1.5)
                headers = {"content-type": "audio/mpeg", "character-cost": str(len(str(body.get("text") or "")))}
            else:
                audio = make_audio(float(body.get("duration_seconds") or SFX_EVENT_SECONDS))
                headers = {"content-type": "audio/mpeg"}
            return httpx.Response(200, headers=headers, content=audio, request=request)
        return await original_send(client, request, **kwargs)

    def emit(kind, payload=None, **kw):
        facts.append({"kind": kind, "payload": payload if isinstance(payload, dict) else {}})
        return None

    httpx.AsyncClient.send = send
    observation.emit = emit
    media_spend.reset_gap_state()

    rec = Rec()
    result: dict[str, Any] = {"mode": mode, "node": args.node,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"},
                              "app_caps_usd": cap, "worst_case_usd": worst_case_usd(args.node)}
    print(f"preflight: mode={mode} node={args.node} app_caps=${cap} worst_case=${result['worst_case_usd']} "
          f"max_real_posts={args.max_real_posts}", flush=True)
    state_db = data / "state.db"
    try:
        if "sfx_gen" in nodes_of(args.node):
            await _sfx_scenarios(rec, gate, facts, state_db)
        if "audio" in nodes_of(args.node):
            await _audio_scenarios(rec, gate, facts, state_db)
        await media_spend.drain()
    except Exception as exc:  # noqa: BLE001
        result["error"] = scrub(f"{type(exc).__name__}: {exc}")
        import traceback
        result["traceback"] = scrub(traceback.format_exc())[-2500:]
        print("STOP:", result["error"], flush=True)
    finally:
        httpx.AsyncClient.send = original_send
        observation.emit = original_emit
    return _finish(args, mode, result, rec, gate, facts, state_db, scrub, work)


# ───────────────────────────── seeding and runners ─────────────────────────────

async def _seed(kind: str, meta: dict | None = None) -> int:
    from app.db import session_scope
    from app.models import Project, ProjectStatus
    slug = f"eleven-{kind}-{_uuid.uuid4().hex[:6]}"
    async with session_scope() as session:
        project = Project(slug=slug, topic="elevenlabs nodes e2e", status=ProjectStatus.new, auto_mode=False,
                          general_plan="", script_text="", meta=meta or {})
        session.add(project)
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    return pid


async def _load(pid: int):
    from app.db import SessionLocal
    from app.models import Project
    async with SessionLocal() as session:
        project = await session.get(Project, pid)
        session.expunge(project)
        return project


async def _set_status(pid: int, status) -> None:
    from app.db import session_scope
    from app.models import Project
    async with session_scope() as session:
        (await session.get(Project, pid)).status = status


async def _run_sfx_step(pid: int, tag: str, *, tail_fault: bool = False, hits: list | None = None) -> BaseException | None:
    """The production step generate_sfx.run in its own session and request scope. Returns the exception it ended with."""
    from contextlib import nullcontext
    from app.db import session_scope
    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_sfx
    from app.services import media_spend, request_context
    await _set_status(pid, ProjectStatus.generating_sfx)
    original = generate_sfx.degradation_summary

    def broken(*_a, **_k):
        raise RuntimeError("simulated outcome loss: the tail of the sfx_gen step crashed after the paid POST")

    if tail_fault:
        generate_sfx.degradation_summary = broken
    try:
        with request_context.project_scope(pid, run_id=f"native:{pid}:{tag}"):
            with (media_spend.resume_only(hits) if hits is not None else nullcontext()):
                async with session_scope() as session:
                    project = await session.get(Project, pid)
                    await generate_sfx.run(session, project)
        return None
    except BaseException as exc:  # noqa: BLE001 - ProcessDied is a BaseException on purpose
        return exc
    finally:
        generate_sfx.degradation_summary = original


def _sfx_plan_meta() -> dict:
    return {"ai_jobs": {"sfx_plan": {"events": [{
        "frame_number": 1, "t_start": 0.0, "duration": SFX_EVENT_SECONDS, "kind": "hit", "prompt": SFX_PROMPT,
        "gain": 0.5, "duck": False}]}}}


async def _sfx_scenarios(rec: Rec, gate: Gate, facts: list, state_db: Path) -> None:
    from app.services import media_admission, media_local_resume, media_recovery, media_spend
    from app.services.ai_result_io import ai_job_checkpoint
    from app.services.audio_verification import verify_audio

    # ---- S1: one real POST, outcome lost at the tail of the step, recovered without a second POST ----
    pid = await _seed("sfx", _sfx_plan_meta())
    before = gate.count("sfx")
    err = await _run_sfx_step(pid, "loss", tail_fault=True)
    await media_spend.drain()
    project = await _load(pid)
    prog = ai_job_checkpoint(project, "sfx_progress") or {}
    ev = (prog.get("events") or {}).get("0") or {}
    path = Path(str(ev.get("path") or ""))
    rec.check("sfx_one_post_sent", gate.count("sfx") - before == 1 and gate.count("sfx") == 1,
              {"sfx_wire_posts": gate.count("sfx"), "ended_with": type(err).__name__})
    rec.check("sfx_outcome_loss_injected", isinstance(err, RuntimeError) and "simulated outcome loss" in str(err)
              and ai_job_checkpoint(project, "sfx_files") is None, {"error": str(err)[:120]})
    v = verify_audio(path, expect_duration=SFX_EVENT_SECONDS, duration_tol=0.6) if path.is_file() else None
    rec.check("sfx_paid_event_checkpointed_and_file_verifies",
              ev.get("provider") == "elevenlabs" and path.is_file() and bool(v and v.ok) and not (prog.get("inflight") or {}),
              {"provider": ev.get("provider"), "file": path.name, "verification": (v.to_dict() if v else None)})
    rows = _spend_rows(state_db, pid)
    sfx_rows = [r for r in rows if str(r["note"]).startswith("sfx dur=")]
    rec.check("sfx_spend_record_written_and_correlated",
              len(sfx_rows) == 1 and sfx_rows[0]["kind"] == "estimated" and sfx_rows[0]["usd"] > 0
              and all(_correlated(r, pid) for r in sfx_rows) and media_spend.gap_count() == 0
              and not media_spend.uncorrelated_counts(),
              {"rows": [(r["kind"], r["usd"], r["note"], r["run_id"]) for r in rows], "gaps": media_spend.gap_count(),
               "uncorrelated": media_spend.uncorrelated_counts()})
    bad_facts = [f for f in facts if f["kind"] in ("provider_fallback", "sfx_node_degraded")]
    rec.check("sfx_no_fallback_or_degraded_fact_on_the_paid_path", not bad_facts, bad_facts[:3])

    hits: list = []
    err2 = await _run_sfx_step(pid, "resume", hits=hits)  # the recovery rerun: resume-only, no paid seam may be touched
    await media_spend.drain()
    project = await _load(pid)
    files = (ai_job_checkpoint(project, "sfx_files") or {}).get("files") or []
    rec.check("sfx_rerun_in_resume_only_finishes_without_post",
              err2 is None and hits == [] and gate.count("sfx") == 1 and len(files) == 1
              and files[0].get("provider") == "elevenlabs" and Path(files[0]["path"]) == path
              and (project.meta or {}).get("sfx_generated") is True and (project.meta or {}).get("sfx_degraded") is False
              and ai_job_checkpoint(project, "sfx_progress") is None,
              {"error": repr(err2), "paid_seam_hits": hits, "sfx_wire_posts": gate.count("sfx"),
               "meta": {k: (project.meta or {}).get(k) for k in ("sfx_generated", "sfx_degraded", "sfx_provider")}})
    try:
        admission = await media_local_resume.admit(project, "sfx_gen")
        verified = await media_local_resume.run_and_verify(project, "sfx_gen", admission)
        sup = {"operation": admission["operation"], "verified": verified["verified"], "problems": verified["problems"],
               "new_paid_requests": verified["new_paid_requests"], "node_started": verified["node_started"],
               "files": [(f["key"], f.get("status")) for f in verified["files"]]}
        ok = (admission["operation"] == "resume_local" and verified["verified"] is True
              and verified["new_paid_requests"] == 0 and verified["node_started"] is False)
    except media_admission.MediaAdmissionDenied as exc:
        sup, ok = {"denied": exc.code}, False
    rec.check("sfx_supervised_resume_local_verifies_the_file_without_a_paid_request",
              ok and gate.count("sfx") == 1, {**sup, "sfx_wire_posts": gate.count("sfx")})
    err3 = await _run_sfx_step(pid, "again")  # a plain native rerun: the checkpoint answers, no POST
    rec.check("sfx_plain_rerun_makes_no_second_post", err3 is None and gate.count("sfx") == 1 and gate.attempts("sfx") == 1,
              {"error": repr(err3), "attempts": gate.attempts("sfx")})
    # the file itself is lost: under resume-only a new paid POST is refused before the wire (never a silent resend)
    path.unlink()
    hits4: list = []
    attempts_before = gate.attempts("sfx")
    err4 = await _run_sfx_step(pid, "lostfile", hits=hits4)
    rec.check("sfx_lost_file_is_not_resent_under_resume_only",
              isinstance(err4, media_spend.ResumeOnlyBlocked) and len(hits4) == 1 and gate.attempts("sfx") == attempts_before,
              {"error": type(err4).__name__, "hits": hits4, "attempts": gate.attempts("sfx")})

    # ---- S2 (simulated): the process dies after the POST was sent -> the inflight mark blocks a resend ----
    pid2 = await _seed("sfx2", _sfx_plan_meta())
    gate.arm("kill")
    err = await _run_sfx_step(pid2, "kill")
    await media_spend.drain()
    project2 = await _load(pid2)
    prog2 = ai_job_checkpoint(project2, "sfx_progress") or {}
    rows2 = _spend_rows(state_db, pid2)
    rec.check("sfx_kill_after_send_leaves_the_sent_no_answer_mark",
              isinstance(err, ProcessDied) and list((prog2.get("inflight") or {})) == ["0"] and gate.fault is None
              and any("uncertain" in str(r["note"]) and r["kind"] == "estimated" and r["usd"] > 0 for r in rows2),
              {"error": type(err).__name__, "inflight": prog2.get("inflight"), "fault_delivered": gate.fault is None,
               "rows": [(r["kind"], r["usd"], r["note"]) for r in rows2]})
    attempts_before = gate.attempts("sfx")
    facts_before = len(facts)
    err = await _run_sfx_step(pid2, "after-kill")
    await media_spend.drain()
    project2 = await _load(pid2)
    files2 = (ai_job_checkpoint(project2, "sfx_files") or {}).get("files") or []
    fb = (files2[0].get("provider_fallback") or {}) if files2 else {}
    path2 = Path(str(files2[0].get("path"))) if files2 else Path("")
    v2 = verify_audio(path2, expect_duration=SFX_EVENT_SECONDS, duration_tol=0.6) if files2 and path2.is_file() else None
    new_facts = [f for f in facts[facts_before:] if f["kind"] == "provider_fallback"]
    rec.check("sfx_uncertain_event_is_not_resent_and_is_marked",
              err is None and gate.attempts("sfx") == attempts_before and len(files2) == 1
              and fb.get("may_have_charged") == "uncertain" and fb.get("error_class") == "InterruptedAfterSend"
              and files2[0].get("provider") == "local_synth" and bool(v2 and v2.ok)
              and (project2.meta or {}).get("sfx_degraded") is True,
              {"error": repr(err), "attempts": gate.attempts("sfx"), "fallback": fb,
               "verification_ok": bool(v2 and v2.ok)})
    rec.check("sfx_uncertain_outcome_is_in_the_journal", any(
        f["payload"].get("may_have_charged") == "uncertain" for f in new_facts), new_facts[:2])
    report = await media_recovery.assess(project2, "sfx_gen")
    denied = None
    try:
        await media_local_resume.admit(project2, "sfx_gen")
    except media_admission.MediaAdmissionDenied as exc:
        denied = exc.code
    err = await _run_sfx_step(pid2, "again-after-kill")
    rec.check("sfx_recovery_denies_a_paid_resend_after_unknown_outcome",
              report["verdict"]["mode"] == "denied" and report["verdict"]["code"] == "unknown_outcome_elevenlabs"
              and denied == "verdict_denied" and err is None and gate.attempts("sfx") == attempts_before,
              {"verdict": report["verdict"]["mode"], "code": report["verdict"]["code"], "admit": denied,
               "attempts": gate.attempts("sfx")})


async def _tts(pid: int, text: str, out_path: Path, tag: str, *, hits: list | None = None):
    """The production TTS seam, wrapped as generate_audio._run wraps it (request scope, tts_run_budget)."""
    from contextlib import nullcontext
    from app.services import media_spend, request_context
    from app.services.elevenlabs_api import synthesize_speech, tts_run_budget
    try:
        with request_context.project_scope(pid, run_id=f"native:{pid}:{tag}"):
            with (media_spend.resume_only(hits) if hits is not None else nullcontext()):
                with tts_run_budget():
                    await synthesize_speech(text, out_path)
        return None
    except BaseException as exc:  # noqa: BLE001
        return exc


async def _audio_scenarios(rec: Rec, gate: Gate, facts: list, state_db: Path) -> None:
    from app.orchestrator.steps import generate_audio
    from app.services import elevenlabs_api as el
    from app.services import media_admission, media_local_resume, media_recovery, media_spend
    from app.services.audio_verification import classify_audio_failure, verify_audio

    # ---- A1: one real POST, sidecar, rerun from the sidecar, the node's answer lost -> recovered without a POST ----
    pid = await _seed("audio")
    project = await _load(pid)
    audio_dir = project.data_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out1 = audio_dir / "voice_full_aaaa1111.mp3"
    err = await _tts(pid, TTS_TEXT, out1, "first")
    await media_spend.drain()
    key = el._request_key(_voice_id(), _model(), _voice_settings(),
                          el.normalize_text_for_tts(TTS_TEXT))
    sidecar = out1.with_name(out1.name + el._SIDECAR_SUFFIX)
    v = verify_audio(out1) if out1.is_file() else None
    rec.check("tts_one_post_sent_and_file_verifies",
              err is None and gate.count("tts") == 1 and out1.is_file() and bool(v and v.ok),
              {"error": repr(err), "tts_wire_posts": gate.count("tts"), "verification": (v.to_dict() if v else None)})
    side = json.loads(sidecar.read_text(encoding="utf-8")) if sidecar.is_file() else {}
    rec.check("tts_request_key_sidecar_written_and_no_marker",
              side.get("key") == key and not list(audio_dir.glob(el._UNCERTAIN_PREFIX + "*")),
              {"sidecar": sidecar.name, "key_matches": side.get("key") == key})
    rows = _spend_rows(state_db, pid)
    tts_rows = [r for r in rows if str(r["note"]).startswith("tts ")]
    rec.check("tts_spend_record_written_and_correlated",
              len(tts_rows) >= 1 and tts_rows[0]["kind"] == "estimated" and tts_rows[0]["usd"] > 0
              and f"chars={len(TTS_TEXT)}" in tts_rows[0]["note"] and all(_correlated(r, pid) for r in tts_rows)
              and media_spend.gap_count() == 0 and not media_spend.uncorrelated_counts(),
              {"rows": [(r["kind"], r["usd"], r["note"], r["run_id"]) for r in rows], "gaps": media_spend.gap_count(),
               "uncorrelated": media_spend.uncorrelated_counts()})
    out2 = audio_dir / "voice_full_bbbb2222.mp3"
    err = await _tts(pid, TTS_TEXT, out2, "rerun")
    rec.check("tts_rerun_is_answered_from_the_sidecar_without_a_post",
              err is None and gate.count("tts") == 1 and gate.attempts("tts") == 1 and out2.is_file()
              and out2.read_bytes() == out1.read_bytes(),
              {"error": repr(err), "tts_wire_posts": gate.count("tts")})
    # the answer the node held (the tail of the step died after the POST) is lost: the sidecar copy answers, also
    # under resume-only, where any paid seam would raise
    out2.unlink()
    hits: list = []
    out3 = audio_dir / "voice_full_cccc3333.mp3"
    err = await _tts(pid, TTS_TEXT, out3, "recover", hits=hits)
    rec.check("tts_lost_answer_recovered_under_resume_only_without_a_post",
              err is None and hits == [] and out3.is_file() and gate.attempts("tts") == 1,
              {"error": repr(err), "paid_seam_hits": hits, "attempts": gate.attempts("tts")})
    project = await _load(pid)
    try:
        admission = await media_local_resume.admit(project, "audio")
        verified = await media_local_resume.run_and_verify(project, "audio", admission)
        sup = {"operation": admission["operation"], "verified": verified["verified"], "problems": verified["problems"],
               "new_paid_requests": verified["new_paid_requests"], "node_started": verified["node_started"],
               "files": [(f["key"], f.get("status")) for f in verified["files"]]}
        ok = (admission["operation"] == "resume_local" and verified["verified"] is True
              and verified["new_paid_requests"] == 0 and verified["node_started"] is False and len(verified["files"]) >= 1)
    except media_admission.MediaAdmissionDenied as exc:
        sup, ok = {"denied": exc.code}, False
    rec.check("tts_supervised_resume_local_verifies_the_file_without_a_paid_request",
              ok and gate.attempts("tts") == 1, {**sup, "attempts": gate.attempts("tts")})
    hits2: list = []
    err = await _tts(pid, TTS_TEXT_OTHER, audio_dir / "voice_full_dddd4444.mp3", "other", hits=hits2)
    rec.check("tts_other_text_under_resume_only_is_refused_before_the_wire",
              isinstance(err, media_spend.ResumeOnlyBlocked) and len(hits2) == 1 and gate.attempts("tts") == 1,
              {"error": type(err).__name__, "hits": hits2, "attempts": gate.attempts("tts")})

    # ---- A2 (simulated): read timeout after send -> marker blocks every automatic resend ----
    pid2 = await _seed("audio2")
    project2 = await _load(pid2)
    adir2 = project2.data_dir / "audio"
    adir2.mkdir(parents=True, exist_ok=True)
    gate.arm("timeout")
    attempts_before = gate.attempts("tts")
    err = await _tts(pid2, TTS_TEXT_UNCERTAIN, adir2 / "voice_full_eeee5555.mp3", "timeout")
    await media_spend.drain()
    markers = sorted(p.name for p in adir2.glob(el._UNCERTAIN_PREFIX + "*"))
    rows2 = _spend_rows(state_db, pid2)
    rec.check("tts_timeout_after_send_writes_the_uncertain_marker",
              isinstance(err, el.ElevenLabsUncertainError) and len(markers) == 1 and gate.fault is None
              and gate.attempts("tts") == attempts_before + 1
              and any("uncertain" in str(r["note"]) and r["usd"] > 0 and r["kind"] == "estimated" for r in rows2),
              {"error": type(err).__name__, "markers": markers, "fault_delivered": gate.fault is None,
               "rows": [(r["kind"], r["usd"], r["note"]) for r in rows2]})
    attempts_before = gate.attempts("tts")
    err = await _tts(pid2, TTS_TEXT_UNCERTAIN, adir2 / "voice_full_ffff6666.mp3", "after-timeout")
    err_b = await _tts(pid2, TTS_TEXT_UNCERTAIN, adir2 / "voice_full_gggg7777.mp3", "after-timeout-2")
    rec.check("tts_marker_blocks_the_automatic_resend",
              isinstance(err, el.ElevenLabsUncertainError) and isinstance(err_b, el.ElevenLabsUncertainError)
              and gate.attempts("tts") == attempts_before and not list(adir2.glob("voice_full_ffff6666*")),
              {"errors": [type(err).__name__, type(err_b).__name__], "attempts": gate.attempts("tts")})
    report = await media_recovery.assess(project2, "audio")
    denied = None
    try:
        await media_local_resume.admit(project2, "audio")
    except media_admission.MediaAdmissionDenied as exc:
        denied = exc.code
    rec.check("tts_recovery_denies_a_paid_resend_after_unknown_outcome",
              report["verdict"]["mode"] == "denied" and report["verdict"]["code"] == "unknown_outcome_elevenlabs"
              and denied == "verdict_denied", {"verdict": report["verdict"]["mode"], "code": report["verdict"]["code"],
                                               "admit": denied})
    facts_before = len(facts)
    generate_audio._report_failure(project2, err)  # the step's own typing of the failure into the journal
    failure = [f for f in facts[facts_before:] if f["kind"] == "audio_step_failure"]
    rec.check("tts_uncertain_failure_is_typed_in_the_journal",
              len(failure) == 1 and failure[0]["payload"].get("failure_class") == classify_audio_failure(err)[0] == "tts_failed"
              and failure[0]["payload"].get("error_class") == "ElevenLabsUncertainError", failure[:1])


def _voice_id() -> str:
    from app.services.elevenlabs_voices import DEFAULT_ELEVENLABS_VOICE_ID
    return str(DEFAULT_ELEVENLABS_VOICE_ID).strip()


def _model() -> str:
    from app.settings import settings
    return str(getattr(settings, "elevenlabs_model_id", "eleven_multilingual_v2") or "eleven_multilingual_v2").strip()


def _voice_settings() -> dict:
    """The payload shape _request_key reads (it takes payload.get("voice_settings"))."""
    return {"voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}


# ───────────────────────────── result ─────────────────────────────

def _finish(args, mode, result, rec: Rec, gate: Gate, facts, state_db, scrub, work) -> int:
    counters = {"wire_posts": len(gate.wire), "wire_sfx": gate.count("sfx"), "wire_tts": gate.count("tts"),
                "simulated_losses": len(gate.sim), "refused": len(gate.refused),
                "real_posts": len(gate.wire) if mode == "live" else 0}
    checks = list(rec.checks)
    expected = {"sfx_gen": 1, "audio": 1}
    for node in nodes_of(args.node):
        kind = "sfx" if node == "sfx_gen" else "tts"
        checks.append(check(f"{node}_exactly_one_post_on_the_wire", counters[f"wire_{kind}"] == expected[node],
                            {"posts": counters[f"wire_{kind}"]}))
    checks.append(check("caps_not_exceeded", len(gate.wire) <= args.max_real_posts
                        and all(gate.count(k) <= 1 for k in ("sfx", "tts")),
                        {"wire_posts": len(gate.wire), "max_real_posts": args.max_real_posts}))
    checks.append(check("nothing_foreign_or_over_the_cap_was_attempted", not gate.refused, gate.refused[:5]))
    checks.append(check("every_simulated_loss_was_delivered", gate.fault is None, gate.fault))
    if mode == "dry":
        checks.append(check("dry_nothing_left_the_machine", len(gate.refused) == 0, gate.refused[:3]))
    ok, failed = verdict(checks)
    if result.get("error"):
        ok = False
        failed = ["flow_error"] + failed
        checks.insert(0, check("flow_completed_without_error", False, scrub(str(result["error"]))))
    spend = _spend_rows(state_db)
    reality = reality_report(mode, counters)
    summary = {"mode": mode, "node": args.node, "verdict": final_verdict(mode, ok), "failed_checks": failed,
               "real_posts": counters["real_posts"], "wire_posts": counters["wire_posts"],
               "simulated_losses": counters["simulated_losses"],
               "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd,
                        "app_caps_usd": result.get("app_caps_usd"), "worst_case_usd": result.get("worst_case_usd")},
               "not_proven": reality_not_proven(mode)}
    out = {**result, "counters": counters, "checks": checks, "ok": ok, "failed": failed, "reality": reality,
           "summary": summary, "spend_rows": spend,
           "facts": [f for f in facts if f["kind"] in ("provider_fallback", "sfx_node_degraded", "audio_step_failure")][:20],
           "gate_wire": gate.wire, "gate_sim": gate.sim, "gate_refused": gate.refused}
    (work / "result.json").write_text(scrub(json.dumps(out, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {args.node} | mode: {mode} | real/wire POSTs: {counters['real_posts']}/{counters['wire_posts']} "
          f"| simulated losses: {counters['simulated_losses']}")
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"])
        if not c["ok"]:
            print("     detail:", scrub(json.dumps(c["detail"], ensure_ascii=False, default=str))[:500])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    print("verdict:", summary["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
