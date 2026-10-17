"""Media node `music` (Suno V5_5, instrumental, DIRECT Kie API) through the PRODUCTION step generate_music.run, with spend
recording. ONE real generation at most, never a retry that spends again.

Sibling of scripts/e2e_elevenlabs_nodes.py (same modes, same work dir / settings patching / result.json / caps discipline,
none of its files edited). This stand proves the paid Kie branch of the `music` step: kie_http.run_generation
(create_task -> poll_task -> download, media_resume intent/bound rows, media_spend recording, spend_guard gate).

Flow (both modes):
  A (free)  GET /api/v1/chat/credit BEFORE anything paid. A missing balance, a failed request or a balance at or below
            MIN_BALANCE_CREDITS STOPS the run: no paid request is sent and the verdict is NOT PASSED with the reason.
  B (paid)  the throwaway project is seeded in a scratch work dir; generate_music.run is called ONCE. The GPT prompt call of
            the step is replaced by a fixed short instrumental prompt (the step would otherwise spend a text-LLM request);
            everything after it is the production code. Then: an mp3 exists and is non-empty, its duration is > 5 s (when
            ffprobe can measure it), an Artifact(kind=music) row exists, the kie spend row is written and correlated, and the
            balance is read again to report the delta. A timeout or an exception gives NOT PASSED with the reason; the Gate
            refuses a second createTask in any case.

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no key: a fake Kie server on 127.0.0.1 (balance, createTask, record-info polling, mp3 download)
          answers the real production HTTP client. Every request that is not on the allow list is refused and counted.
          --dry-scenario also exercises the stop / failure branches (low or missing balance, failed task, rejected submit).
  --live  PAID, capped: ONE real createTask (api.kie.ai or KIE_API_BASE_URL, https only). Needs --env-file; ONLY the names
          KIE_API_KEY and KIE_API_BASE_URL are read from it, nothing is printed. Caps: --max-real-posts (default 1),
          --max-usd (default 0.70, hard ceiling 1.00; refused when the catalog price of the generation exceeds it).
          NOT RUN in live mode when written.

What is real: the whole production code of the step's Kie branch, spend_guard (kill switch, disk guard, caps), media_spend
recording (rows in the scratch spend_ledger with project / run / request correlation), media_resume. In --live also the
balance reads, the createTask, the polling and the download.
What is NOT real: the seeded project, the GPT prompt (fixed text), the provider in --dry, the musical quality (never judged).

Usage (repository root, project venv):
    python scripts/e2e_music_kie.py --dry --work-dir <empty dir outside the repository>
    python scripts/e2e_music_kie.py --live --work-dir <dir> --env-file <env file> --max-real-posts 1 --max-usd 0.70
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import uuid as _uuid
import wave
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard

DEFAULT_BASE_URL = "https://api.kie.ai"
BALANCE_PATH = "/api/v1/chat/credit"
CREATE_PATH = "/api/v1/generate"
POLL_PATH = "/api/v1/generate/record-info"
CALLBACK_PLACEHOLDER = "https://localhost/kie-callback"
ENV_NAMES = ("KIE_API_KEY", "KIE_API_BASE_URL")
SECRET_NAMES = ("KIE_API_KEY",)
FAKE_KEY = "fake-kie-dry-key"

CREDITS_PER_GENERATION_HINT = 122.0
CREDIT_USD_HINT = 0.005
MIN_BALANCE_CREDITS = 244.0
MIN_DURATION_S = 5.0
MUSIC_PROMPT = "Calm ambient instrumental, soft piano and warm pads, slow tempo, no vocals"
MUSIC_PROMPT_MAX_CHARS = 200
MUSIC_MODEL = "V5_5"
DEFAULT_MAX_REAL_POSTS = 1
DEFAULT_MAX_USD = 0.70
HARD_CEILING_USD = 1.0
MAX_BALANCE_READS = 3
MAX_POLLS = 400
MAX_DOWNLOADS = 3
AUDIO_EXTS = (".mp3", ".wav", ".m4a", ".ogg", ".flac")
DRY_SCENARIOS = ("ok", "low_balance", "balance_missing", "balance_error", "task_failed", "create_rejected")
SETTLE_WAIT_S = 8.0



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


def planned_real_posts() -> int:
    return 1


def worst_case_usd() -> float:
    return round(CREDITS_PER_GENERATION_HINT * CREDIT_USD_HINT, 6)


def authorisation_problem(max_real_posts: int, max_usd: float) -> str | None:
    """The stand refuses a run whose real POST or conservative price exceed what it was authorised to do."""
    if max_real_posts < 1 or max_usd <= 0:
        return "--max-real-posts must be >= 1 and --max-usd > 0"
    if max_usd > HARD_CEILING_USD + 1e-9:
        return f"--max-usd {max_usd} is above the hard ceiling ${HARD_CEILING_USD}"
    if planned_real_posts() > max_real_posts:
        return f"the music node needs {planned_real_posts()} real POST, --max-real-posts is {max_real_posts}"
    if worst_case_usd() > max_usd + 1e-9:
        return f"worst case ${worst_case_usd()} of the planned real POST exceeds --max-usd {max_usd}"
    return None


def app_cap_usd(max_usd: float) -> float:
    """Application caps (run / project / daily) = the authorised amount: a second line of defence behind the Gate."""
    return round(min(float(max_usd), HARD_CEILING_USD), 2)


def base_url_problem(url: str) -> str | None:
    """A live base URL must be a plain https origin on a non-loopback host (the Gate allows exactly that host)."""
    parts = urlsplit(url or "")
    if parts.scheme != "https" or not parts.hostname:
        return "KIE_API_BASE_URL must be an https URL"
    if is_loopback(parts.hostname):
        return "KIE_API_BASE_URL must not be a loopback address in --live"
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.port not in (None, 443):
        return "KIE_API_BASE_URL must be a plain origin (no path, port, query or credentials)"
    return None


def is_loopback(host: str | None) -> bool:
    h = (host or "").strip("[]").lower()
    return h in ("127.0.0.1", "localhost", "::1")


def balance_stop_reason(credits: float | None) -> str | None:
    """Why the run must stop BEFORE the paid call, or None when the balance allows it."""
    if credits is None:
        return "balance_unavailable: GET /api/v1/chat/credit gave no number (missing, failed or key refused); no paid call"
    if credits <= MIN_BALANCE_CREDITS:
        return (f"balance_too_low: {credits} credits is at or below the minimum {MIN_BALANCE_CREDITS} "
                f"(one generation is about {CREDITS_PER_GENERATION_HINT}); no paid call")
    return None


def probe_duration(path: Path) -> float | None:
    """Duration in seconds by ffprobe, or None when it cannot be measured."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file():
        return None
    try:
        out = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1",
                              str(path)], check=True, timeout=60, capture_output=True, text=True).stdout.strip()
        value = float(out)
        return value if value > 0 else None
    except Exception:
        return None


def check(name: str, ok: bool, detail: Any = None) -> dict:
    return {"name": name, "ok": bool(ok), "detail": detail}


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    failed = [c["name"] for c in checks if not c["ok"]]
    return not failed, failed


def final_verdict(mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return "PASS (all checks, live: one real Kie Suno generation within the caps; the music step proven end to end on the real provider)"
    return "DRY OK (fake Kie server on loopback: machinery proven offline, NOT a live proof)"


@dataclass
class Judgement:
    action: str
    kind: str
    reason: str = ""


@dataclass
class Gate:
    """Every outgoing request is judged here (pure bookkeeping, no I/O): the send hook obeys it.

    Allowed, and nothing else: GET <base>/api/v1/chat/credit (<= MAX_BALANCE_READS), ONE POST <base>/api/v1/generate
    (instrumental, V5_5, short prompt), GET <base>/api/v1/generate/record-info?taskId=<the created task> and GET of a result
    URL that the poll answer itself named (https, an audio file; same host as a learned URL). In dry mode the same list
    on http://127.0.0.1:<port>."""
    mode: str
    max_real_posts: int = DEFAULT_MAX_REAL_POSTS
    host: str = "api.kie.ai"
    scheme: str = "https"
    port: int | None = None
    task_id: str = ""
    wire: list = field(default_factory=list)
    refused: list = field(default_factory=list)
    downloads: dict = field(default_factory=dict)

    def count(self, kind: str) -> int:
        return sum(1 for w in self.wire if w["kind"] == kind)

    def _refuse(self, kind: str, why: str, reason: str) -> Judgement:
        self.refused.append({"kind": kind, "why": why, "reason": reason[:160]})
        return Judgement("refuse", kind, reason)

    def learn_downloads(self, urls: list[str]) -> None:
        for url in urls:
            parts = urlsplit(str(url))
            if parts.hostname and Path(parts.path.lower()).suffix in AUDIO_EXTS:
                self.downloads[str(url)] = parts.hostname.lower()

    def _origin_ok(self, scheme: str, host: str, port: int | None) -> bool:
        if host != self.host or scheme != self.scheme:
            return False
        return self.port is None or port == self.port

    def judge(self, method: str, scheme: str, host: str | None, path: str, body: Any = None, *,
              port: int | None = None, params: dict | None = None, url: str = "") -> Judgement:
        method, host_l = method.upper(), (host or "").strip("[]").lower()
        path, params = path or "", params or {}
        label = f"{method} {scheme}://{host_l}{path}"
        if method == "GET" and url and (url in self.downloads or (
                host_l in self.downloads.values() and Path(path.lower()).suffix in AUDIO_EXTS)):
            if scheme != self.scheme or (self.mode == "live" and is_loopback(host_l)) or (
                    self.mode == "dry" and not is_loopback(host_l)):
                return self._refuse("download", "foreign", f"refused: download {label} is not on the allow list")
            if self.count("download") >= MAX_DOWNLOADS:
                return self._refuse("download", "download_cap", f"download cap reached ({MAX_DOWNLOADS})")
            self.wire.append({"kind": "download", "host": host_l})
            return Judgement("pass", "download")
        if not self._origin_ok(scheme, host_l, port):
            return self._refuse("other", "foreign", f"refused: {label} is not on the allow list")
        if method == "GET" and path == BALANCE_PATH:
            if self.count("balance") >= MAX_BALANCE_READS:
                return self._refuse("balance", "balance_cap", f"balance read cap reached ({MAX_BALANCE_READS})")
            self.wire.append({"kind": "balance"})
            return Judgement("pass", "balance")
        if method == "GET" and path == POLL_PATH:
            wanted = self.task_id
            if not wanted or str(params.get("taskId") or "") != wanted:
                return self._refuse("poll", "foreign_task", "poll of a task this stand did not create")
            if self.count("poll") >= MAX_POLLS:
                return self._refuse("poll", "poll_cap", f"poll cap reached ({MAX_POLLS})")
            self.wire.append({"kind": "poll"})
            return Judgement("pass", "poll")
        if method == "POST" and path == CREATE_PATH:
            body = body if isinstance(body, dict) else {}
            if body.get("instrumental") is not True or str(body.get("model") or "") != MUSIC_MODEL \
                    or body.get("customMode") is not False:
                return self._refuse("create", "payload", "createTask must be instrumental, non-custom, model " + MUSIC_MODEL)
            if len(str(body.get("prompt") or "")) > MUSIC_PROMPT_MAX_CHARS or not str(body.get("prompt") or "").strip():
                return self._refuse("create", "prompt_size", f"prompt empty or longer than {MUSIC_PROMPT_MAX_CHARS} characters")
            if body.get("callBackUrl") not in (None, CALLBACK_PLACEHOLDER):
                return self._refuse("create", "callback", "callBackUrl must stay the placeholder")
            if len(self.wire_of("create")) >= self.max_real_posts:
                return self._refuse("create", "paid_cap", f"real post cap reached ({self.max_real_posts})")
            self.wire.append({"kind": "create", "prompt_chars": len(str(body.get("prompt") or "")),
                              "instrumental": body.get("instrumental"), "model": body.get("model"),
                              "customMode": body.get("customMode")})
            return Judgement("pass", "create")
        return self._refuse("other", "foreign", f"refused: {label} is not on the allow list")

    def wire_of(self, kind: str) -> list:
        return [w for w in self.wire if w["kind"] == kind]



_FIXTURES: dict[float, bytes] = {}


def make_audio(seconds: float) -> bytes:
    """A real, decodable audio file of `seconds`: an ffmpeg mp3, or an in-process WAV when ffmpeg cannot encode."""
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
                subprocess.run([ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=330:duration={key}",
                                "-c:a", "libmp3lame", str(out)], check=True, timeout=60, capture_output=True)
                data = out.read_bytes()
            except Exception:
                data = None
    if not data:
        import io
        import math
        import struct
        rate = 8000
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(b"".join(struct.pack("<h", int(0.3 * 32767 * math.sin(2 * math.pi * 330 * i / rate)))
                                   for i in range(int(rate * key))))
        data = buf.getvalue()
    _FIXTURES[key] = data
    return data


class FakeKie:
    """A fake Kie server on 127.0.0.1 (dry only): balance, createTask, record-info polling, the mp3 file.

    The poll answers PENDING, then FIRST_SUCCESS without a clip (the step must keep waiting), then SUCCESS with one clip.
    The key the production client sends is checked: any other Authorization is a 401."""

    def __init__(self, scenario: str = "ok", seconds: float = 8.0, credits: float = 1000.0) -> None:
        self.scenario = scenario
        self.audio = make_audio(seconds)
        self.seconds = seconds
        self.credits = 5.0 if scenario == "low_balance" else credits
        self.posts: list[dict] = []
        self.requests: list[tuple[str, str]] = []
        self.polls = 0
        self.task_id = "fake-suno-task-0001"
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        return int(self._server.server_address[1]) if self._server else 0

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a, **_k) -> None:
                return

            def _send(self, status: int, payload: Any = None, raw: bytes | None = None, ctype: str = "application/json"):
                data = raw if raw is not None else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _authorised(self) -> bool:
                if self.headers.get("Authorization") != f"Bearer {FAKE_KEY}":
                    self._send(200, {"code": 401, "msg": "fake kie: wrong or missing key"})
                    return False
                return True

            def do_GET(self) -> None:
                parts = urlsplit(self.path)
                fake.requests.append(("GET", parts.path))
                if parts.path == BALANCE_PATH:
                    if not self._authorised():
                        return
                    if fake.scenario == "balance_error":
                        return self._send(200, {"code": 500, "msg": "fake kie: balance service down"})
                    if fake.scenario == "balance_missing":
                        return self._send(200, {"code": 200, "msg": "success", "data": None})
                    return self._send(200, {"code": 200, "msg": "success", "data": fake.credits})
                if parts.path == POLL_PATH:
                    if not self._authorised():
                        return
                    task = (parse_qs(parts.query).get("taskId") or [""])[0]
                    fake.polls += 1
                    base = {"taskId": task}
                    if fake.scenario == "task_failed":
                        data = {**base, "status": "GENERATE_AUDIO_FAILED", "errorMessage": "fake kie: generation failed"}
                    elif fake.polls == 1:
                        data = {**base, "status": "PENDING", "response": None}
                    elif fake.polls == 2:
                        data = {**base, "status": "FIRST_SUCCESS", "response": {"sunoData": []}}
                    else:
                        data = {**base, "status": "SUCCESS", "response": {"sunoData": [{
                            "id": "fake-clip-1", "audioUrl": f"{fake.base_url}/files/dry-music.mp3",
                            "duration": fake.seconds, "title": "dry"}]}}
                    return self._send(200, {"code": 200, "msg": "success", "data": data})
                if parts.path == "/files/dry-music.mp3":
                    return self._send(200, raw=fake.audio, ctype="audio/mpeg")
                return self._send(404, {"code": 404, "msg": "fake kie: no such route"})

            def do_POST(self) -> None:
                parts = urlsplit(self.path)
                fake.requests.append(("POST", parts.path))
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except Exception:
                    body = {}
                if parts.path != CREATE_PATH:
                    return self._send(404, {"code": 404, "msg": "fake kie: no such route"})
                if not self._authorised():
                    return
                fake.posts.append(body)
                if fake.scenario == "create_rejected":
                    return self._send(200, {"code": 500, "msg": "fake kie: submit rejected"})
                fake.credits -= CREDITS_PER_GENERATION_HINT
                return self._send(200, {"code": 200, "msg": "success", "data": {"taskId": fake.task_id}})

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()
            self._server = None



def env_overrides(data: Path, *, cap: float, dry: bool) -> dict[str, str]:
    """Environment of the in-process app (applied BEFORE the application is imported). Never a real key here."""
    out = {
        "DATA_DIR": str(data), "SQLITE_PATH": str(data / "state.db"),
        "KIE_API_KEY": FAKE_KEY, "KIE_API_BASE_URL": "http://127.0.0.1:9",
        "GPT_API_KEY": "", "GPT_BASE_URL": "", "GPT_RELAY_TOKEN": "", "VIBECODE_API_KEY": "",
        "ELEVENLABS_API_KEY": "", "ELEVENLABS_PROXY_URL": "", "OUTSEE_API_KEY": "",
        "SPEND_DAILY_CAP_USD": str(cap), "SPEND_PROJECT_CAP_USD": str(cap), "SPEND_RUN_CAP_USD": str(cap),
        "MEDIA_SPEND_RECORDING": "true",
        "TELEGRAM_ENABLED": "false", "TELEGRAM_BOT_TOKEN": "", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false",
        "WEB_ENABLED": "false", "HITL_AUTO_APPROVE": "false", "BROWSER_CDP_AUTO_RECOVER": "false",
        "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false", "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused",
    }
    if dry:
        out.update({"DISK_GUARD_LOW_FREE_GB": "0.001", "DISK_GUARD_CRITICAL_FREE_GB": "0.0005"})
    return out


def reality_report(mode: str, counters: dict) -> dict:
    live = mode == "live"
    real = counters.get("wire_create", 0) if live else 0
    return {
        "real": [x for x in (
            "production step in-process: generate_music.run -> kie_http.run_generation (create_task, poll_task, download, "
            "media_resume intent/bound rows) with paid_seam + media_spend recording",
            "spend_guard (kill switch, disk guard, caps) and the scratch spend_ledger with project / run / request correlation",
            f"{real} real Kie Suno createTask (+ balance reads, polling, download)" if real else None) if x],
        "not_real": [x for x in (
            "seeded project: synthetic, built by this script (voiceover.txt is a stub)",
            "the GPT prompt of the step: a fixed short instrumental prompt (the text-LLM call is not made)",
            None if live else "the provider: a local fake Kie server on loopback (nothing left the machine, nothing was paid)",
            "musical quality and fit: never judged") if x],
    }


def reality_not_proven(mode: str) -> list[str]:
    out = ["the price of a real Suno generation is the catalog estimate (12 credits, uncalibrated); the true charge is only "
           "the balance delta, which can lag or include other consumers of the same account",
           "one tiny generation does not prove the 900 s timeout path, a lost answer after submit, or the media_resume "
           "recovery on the real provider",
           "Suno may return two clips; only the first (longest) is downloaded, the second is not checked"]
    if mode != "live":
        out.insert(0, "anything about the real Kie: this is a dry run on a fake server, NOT a live proof")
    return out



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--dry", action="store_true", help="FREE: fake Kie server on loopback, nothing leaves the machine")
    p.add_argument("--live", action="store_true", help="PAID, capped: ONE real Kie Suno generation")
    p.add_argument("--env-file", help="env file; only " + ", ".join(ENV_NAMES) + " are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, result.json (recreated)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS, help="hard cap of real paid POSTs (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help=f"authorised spend (hard ceiling ${HARD_CEILING_USD}); refused when the catalog price exceeds it")
    p.add_argument("--dry-scenario", choices=DRY_SCENARIOS, default="ok",
                   help="dry only: exercise a stop / failure branch of the flow on the fake server")
    args = p.parse_args(argv)
    if bool(args.dry) == bool(args.live):
        p.error("choose exactly one of --dry and --live")
    if not args.work_dir:
        p.error("--work-dir is required")
    if args.live and not args.env_file:
        p.error("--live needs --env-file")
    if args.live and args.dry_scenario != "ok":
        p.error("--dry-scenario is for --dry only")
    problem = authorisation_problem(args.max_real_posts, args.max_usd)
    if problem:
        p.error(problem)
    return args


class Rec:
    """Collects the checks of the flow and the facts for result.json."""

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
    return [r for r in rows if r["provider"] == "kie" and (project_id is None or r["project_id"] == project_id)]


def _correlated(row: dict, pid: int) -> bool:
    return row.get("project_id") == pid and bool(row.get("run_id")) and bool(row.get("request_id"))


class _FakeGpt:
    """Stands in for the text LLM of the step: the fixed prompt, no request."""

    async def new_conversation(self) -> None:
        return None

    async def ask_with_files(self, *_a, **_k) -> str:
        return MUSIC_PROMPT


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    base_url = DEFAULT_BASE_URL
    if mode == "live":
        if not Path(args.env_file).is_file():
            print("env file not found; nothing sent")
            return 3
        live_env = load_env_whitelist(args.env_file, ENV_NAMES)
        if not live_env.get("KIE_API_KEY"):
            print("env file has no KIE_API_KEY; nothing sent")
            return 3
        base_url = (live_env.get("KIE_API_BASE_URL") or DEFAULT_BASE_URL).strip().rstrip("/")
        problem = base_url_problem(base_url)
        if problem:
            print(problem + "; nothing sent")
            return 3
    secrets = sorted((v for k, v in live_env.items() if k in SECRET_NAMES and len(v) >= 6), key=len, reverse=True)

    def scrub(text: str) -> str:
        for s in secrets:
            text = text.replace(s, "***")
        return text

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    os.chdir(ROOT)
    cap = app_cap_usd(args.max_usd)
    fake: FakeKie | None = None
    if mode == "dry":
        fake = FakeKie(args.dry_scenario)
        fake.start()
        base_url = fake.base_url
    key = live_env["KIE_API_KEY"] if mode == "live" else FAKE_KEY
    os.environ.update(env_overrides(data, cap=cap, dry=(mode == "dry")))
    os.environ["KIE_API_KEY"] = key
    os.environ["KIE_API_BASE_URL"] = base_url

    import httpx
    from app.bots import kie_http, kie_kling
    from app.settings import settings
    settings.kie_api_key = key
    settings.kie_api_base_url = base_url
    settings.gpt_api_key = ""
    settings.gpt_relay_token = ""
    settings.gpt_base_url = ""
    settings.media_spend_recording = True
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap
    for guarded in (settings.data_dir, settings.sqlite_path):
        if not guard._inside(Path(guarded).resolve(), work):
            if fake:
                fake.stop()
            raise guard.refuse(f"настройки указывают на {guarded}, это вне рабочей папки {work}; ничего не отправлено.")

    from app import main as app_main
    from app.services import gpt_client, media_spend
    await app_main._init_db()

    parts = urlsplit(base_url)
    gate = Gate(mode=mode, max_real_posts=args.max_real_posts, host=(parts.hostname or "").lower(),
                scheme=parts.scheme, port=parts.port if mode == "dry" else None)
    original_send = httpx.AsyncClient.send
    original_keys = kie_kling._iter_kie_keys
    original_interval = kie_http._POLL_INTERVAL_S
    original_gpt = gpt_client.get_gpt_client

    async def send(client, request, **kwargs):
        try:
            body = json.loads(request.content.decode("utf-8") or "{}")
        except Exception:
            body = {}
        judged = gate.judge(request.method, request.url.scheme, request.url.host, request.url.path, body,
                            port=request.url.port, params=dict(request.url.params), url=str(request.url))
        if judged.action == "refuse":
            raise RuntimeError("stand: " + judged.reason)
        response = await original_send(client, request, **kwargs)
        try:
            if judged.kind == "create":
                inner = (response.json().get("data") or {})
                gate.task_id = str(inner.get("taskId") or "") if isinstance(inner, dict) else ""
            elif judged.kind == "poll":
                gate.learn_downloads(kie_http._result_media_urls((response.json().get("data") or {})))
        except Exception:
            pass
        return response

    httpx.AsyncClient.send = send
    kie_kling._iter_kie_keys = lambda: [("KIE_API_KEY", key)]
    kie_kling.reset_working_kie_key()
    gpt_client.get_gpt_client = lambda *a, **k: _FakeGpt()
    if mode == "dry":
        kie_http._POLL_INTERVAL_S = 0.05
    media_spend.reset_gap_state()

    rec = Rec()
    result: dict[str, Any] = {"mode": mode, "dry_scenario": args.dry_scenario if mode == "dry" else None,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"},
                              "app_caps_usd": cap, "worst_case_usd": worst_case_usd()}
    print(f"preflight: mode={mode} app_caps=${cap} worst_case=${result['worst_case_usd']} "
          f"max_real_posts={args.max_real_posts} base={gate.scheme}://{gate.host}", flush=True)
    state_db = data / "state.db"
    try:
        credits_before = await kie_http.get_credits()
        rec.facts["balance_before_credits"] = credits_before
        reason = balance_stop_reason(credits_before)
        rec.check("balance_read_before_any_paid_call", credits_before is not None, {"credits": credits_before})
        if credits_before is not None:
            rec.check("balance_above_the_minimum", reason is None,
                      {"credits": credits_before, "minimum": MIN_BALANCE_CREDITS})
        if reason:
            result["stopped_before_paid_call"] = reason
            print("STOP:", reason, flush=True)
        else:
            await _music_flow(rec, gate, state_db, args, mode, credits_before, result, scrub)
        await media_spend.drain()
    except Exception as exc:
        result["error"] = scrub(f"{type(exc).__name__}: {exc}")
        import traceback
        result["traceback"] = scrub(traceback.format_exc())[-2500:]
        print("STOP:", result["error"], flush=True)
    finally:
        httpx.AsyncClient.send = original_send
        kie_kling._iter_kie_keys = original_keys
        kie_http._POLL_INTERVAL_S = original_interval
        gpt_client.get_gpt_client = original_gpt
        if fake:
            fake.stop()
    return _finish(args, mode, result, rec, gate, state_db, scrub, work, fake)



async def _seed() -> int:
    from app.db import session_scope
    from app.models import Project, ProjectStatus
    slug = f"music-kie-{_uuid.uuid4().hex[:6]}"
    async with session_scope() as session:
        project = Project(slug=slug, topic="music node kie e2e", status=ProjectStatus.new, auto_mode=False,
                          general_plan="", script_text="", meta={})
        session.add(project)
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(
        "Короткий тестовый закадровый текст для проверки музыкального шага. Спокойный ровный тон.", encoding="utf-8")
    return pid


async def _run_music_step(pid: int) -> Exception | None:
    """The production step generate_music.run in its own session and request scope. Returns the exception it ended with."""
    from app.db import session_scope
    from app.models import Project, ProjectStatus
    from app.orchestrator.steps import generate_music
    from app.services import request_context
    async with session_scope() as session:
        (await session.get(Project, pid)).status = ProjectStatus.generating_music
    try:
        with request_context.project_scope(pid, run_id=f"native:{pid}:music"):
            async with session_scope() as session:
                project = await session.get(Project, pid)
                await generate_music.run(session, project, None)
        return None
    except Exception as exc:
        return exc


async def _music_flow(rec: Rec, gate: Gate, state_db: Path, args, mode: str, credits_before: float, result: dict,
                      scrub) -> None:
    from sqlalchemy import select
    from app.bots import kie_http
    from app.db import SessionLocal
    from app.models import Artifact, ArtifactKind, Project, ProjectStatus
    from app.services import media_spend
    from app.services.kie_catalog import CREDIT_USD

    pid = await _seed()
    result["project_id"] = pid
    err = await _run_music_step(pid)
    await media_spend.drain()
    if err is not None:
        ctx = getattr(err, "context", None)
        task = (ctx or {}).get("task_id") if isinstance(ctx, dict) else None
        result["error"] = scrub(f"{type(err).__name__}: {err}"[:400])
        result["step_error_task_id"] = task or gate.task_id or None
        print("STEP ERROR:", result["error"], flush=True)
    rec.check("step_finished_without_error", err is None, result.get("error"))

    async with SessionLocal() as session:
        project = await session.get(Project, pid)
        arts = list((await session.scalars(select(Artifact).where(
            Artifact.project_id == pid, Artifact.kind == ArtifactKind.music))).all())
        status = project.status
        music_dir = project.data_dir / "music"
    files = sorted(p for p in music_dir.glob("music_*.mp3")) if music_dir.is_dir() else []
    path = files[0] if files else Path("")
    size = path.stat().st_size if files and path.is_file() else 0
    duration = probe_duration(path) if size else None
    rec.facts.update({"file": path.name or None, "bytes": size, "duration_s": duration, "task_id": gate.task_id or None})
    rec.check("music_ready_status", status == ProjectStatus.music_ready, {"status": str(status)})
    rec.check("mp3_exists_and_is_not_empty", len(files) == 1 and size > 1000, {"files": [f.name for f in files], "bytes": size})
    measurable = shutil.which("ffprobe") is not None
    rec.check("mp3_duration_over_5_seconds", (duration is not None and duration > MIN_DURATION_S) or
              (duration is None and not measurable),
              {"duration_s": duration, "measurable": measurable, "min": MIN_DURATION_S})
    rec.check("artifact_music_row_points_to_the_file",
              len(arts) == 1 and bool(files) and Path(arts[0].path).resolve() == path.resolve(),
              {"artifacts": [a.path for a in arts]})
    created = gate.wire_of("create")
    rec.check("payload_was_instrumental_v5_5_non_custom",
              len(created) == 1 and created[0]["instrumental"] is True and created[0]["model"] == MUSIC_MODEL
              and created[0]["customMode"] is False, created[:1])

    rows = _spend_rows(state_db, pid)
    create_rows = [r for r in rows if str(r["note"]).startswith("create suno suno-music task=")]
    rec.check("spend_record_written_and_correlated",
              len(create_rows) == 1 and create_rows[0]["kind"] == "estimated" and create_rows[0]["usd"] > 0
              and create_rows[0]["ref"] == gate.task_id and all(_correlated(r, pid) for r in rows)
              and media_spend.gap_count() == 0 and not media_spend.uncorrelated_counts(),
              {"rows": [(r["kind"], r["usd"], r["note"], r["run_id"]) for r in rows], "gaps": media_spend.gap_count(),
               "uncorrelated": media_spend.uncorrelated_counts()})
    ledger_usd = round(sum(float(r["usd"] or 0.0) for r in rows), 6)
    rec.facts["ledger_usd_estimate"] = ledger_usd
    rec.check("ledger_estimate_within_the_authorised_usd", ledger_usd <= args.max_usd + 1e-9,
              {"ledger_usd": ledger_usd, "max_usd": args.max_usd})

    await asyncio.sleep(0.2 if mode == "dry" else SETTLE_WAIT_S)
    credits_after = await kie_http.get_credits()
    delta = None if credits_after is None else round(credits_before - credits_after, 4)
    billed = None if delta is None else round(delta * CREDIT_USD, 6)
    rec.facts.update({"balance_after_credits": credits_after, "credits_delta": delta, "billed_usd_by_balance": billed})
    rec.check("balance_read_after_and_delta_reported", credits_after is not None, {
        "before": credits_before, "after": credits_after, "credits_delta": delta, "billed_usd": billed,
        "catalog_estimate_credits": CREDITS_PER_GENERATION_HINT})
    rec.check("balance_delta_within_the_authorised_usd", billed is None or billed <= args.max_usd + 1e-9,
              {"billed_usd": billed, "max_usd": args.max_usd})



def _finish(args, mode, result, rec: Rec, gate: Gate, state_db, scrub, work, fake) -> int:
    counters = {"wire_create": gate.count("create"), "wire_balance": gate.count("balance"), "wire_poll": gate.count("poll"),
                "wire_download": gate.count("download"), "refused": len(gate.refused),
                "real_posts": gate.count("create") if mode == "live" else 0}
    checks = list(rec.checks)
    stopped = result.get("stopped_before_paid_call")
    if stopped:
        checks.append(check("stopped_without_any_paid_post", counters["wire_create"] == 0,
                            {"create_posts": counters["wire_create"]}))
        checks.append(check("run_reached_the_paid_step", False, stopped))
    else:
        checks.append(check("exactly_one_create_post_on_the_wire", counters["wire_create"] == 1,
                            {"posts": counters["wire_create"]}))
    checks.append(check("caps_not_exceeded", counters["wire_create"] <= min(1, args.max_real_posts),
                        {"create_posts": counters["wire_create"], "max_real_posts": args.max_real_posts}))
    checks.append(check("nothing_foreign_or_over_the_cap_was_attempted", not gate.refused, gate.refused[:5]))
    if mode == "dry" and fake is not None:
        checks.append(check("dry_only_loopback_was_touched", all(h.get("host", "127.0.0.1") in ("127.0.0.1",)
                                                                  for h in gate.wire) and not gate.refused,
                            {"fake_requests": len(fake.requests)}))
    ok, failed = verdict(checks)
    if result.get("error") and "flow_error" not in failed:
        ok = False
        failed = ["flow_error"] + failed
        checks.insert(0, check("flow_completed_without_error", False, scrub(str(result["error"]))))
    reason = stopped or result.get("error") or (", ".join(failed) if failed else None)
    spend = _spend_rows(state_db)
    reality = reality_report(mode, counters)
    summary = {"mode": mode, "verdict": final_verdict(mode, ok), "reason": scrub(str(reason)) if reason else None,
               "failed_checks": failed, "real_posts": counters["real_posts"], "wire_create": counters["wire_create"],
               "balance": {"before_credits": rec.facts.get("balance_before_credits"),
                           "after_credits": rec.facts.get("balance_after_credits"),
                           "credits_delta": rec.facts.get("credits_delta"),
                           "billed_usd_by_balance": rec.facts.get("billed_usd_by_balance")},
               "task_id": rec.facts.get("task_id") or result.get("step_error_task_id"),
               "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd,
                        "app_caps_usd": result.get("app_caps_usd"), "worst_case_usd": result.get("worst_case_usd")},
               "not_proven": reality_not_proven(mode)}
    out = {**result, "counters": counters, "checks": checks, "ok": ok, "failed": failed, "reality": reality,
           "summary": summary, "facts": rec.facts, "spend_rows": spend,
           "gate_wire": gate.wire, "gate_refused": gate.refused}
    (work / "result.json").write_text(scrub(json.dumps(out, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"mode: {mode} | real/wire createTask: {counters['real_posts']}/{counters['wire_create']} "
          f"| balance reads: {counters['wire_balance']} | polls: {counters['wire_poll']}")
    for c in checks:
        print(("PASS " if c["ok"] else "FAIL ") + c["name"])
        if not c["ok"]:
            print("     detail:", scrub(json.dumps(c["detail"], ensure_ascii=False, default=str))[:500])
    print("balance:", summary["balance"])
    print("real:", reality["real"])
    print("not real:", reality["not_real"])
    print("not proven:", summary["not_proven"])
    if summary["reason"] and not ok:
        print("reason:", summary["reason"])
    print("verdict:", summary["verdict"])
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
