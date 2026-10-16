"""Media nodes `hero`, `items`, `videos` (Outsee) through the PRODUCTION route: worker -> observation -> diagnosis ->
Decision -> recovery -> verification. At most ONE real paid provider request PER NODE.

Sibling of scripts/e2e_media_production_route.py (node `images`), which is imported, not edited: its request Gate,
checks, fake-server idea, fault idea, flow helpers and constants are reused. `--node images` (the default) simply runs
that script. Every node is run separately (own work dir, own application, own ports): the lead runs the three live
commands one after another.

Why these three, and what they have in common (docs/MEDIA_NODES_READINESS.md, docs/NODE_COVERAGE.md):
  * `hero` and `items` are Outsee image nodes with a durable request-key record (outsee_idempotency: intent -> result with
    path, size and sha256 of the file), exactly as `images`: the supervised recovery is `resume_local` of that record and
    never a paid resend. `hero` needs a prompt from ChatGPT: the stand replaces that GPT client by a local stub that
    answers a DIFFERENT text on every call (a real GPT does too), so a retry that asks GPT again would change the
    request and pay again - the stand watches for exactly that.
  * `videos` is the Outsee Veo clip node. There is NO request-key record for a video (generate_video_with_retries has no
    idempotent mode; media_local_resume admits only images/hero/items/audio/sfx_gen), and the Outsee ledger holds
    intents only (no task id), so the supervised recovery has nothing to resume. What protects the money there is the
    native disk recovery: a finished clip on disk is adopted by recover_scene_videos_from_disk and never generated
    again. The stand proves THAT (one paid POST, the clip survives the loss, native retries adopt it), and records what the
    supervisor does with such an incident (it must not pay and must not start the node) without pretending it resumes.

The outcome loss is simulated locally and for free, AFTER the paid result exists on disk:
  hero    the HITL card send raises on every native attempt (the step has already saved and "consumed" its result);
  items   the database write of the new item row (AsyncSession.flush of the item_reference artifact) raises on every attempt,
          after the paid file was downloaded and recorded;
  videos  post-step validation and the HITL text send raise on every attempt (the clip is on disk and committed).

CHOICE OF MODEL, RESOLUTION, DURATION (read from the code, no network):
  * hero/items: app.bots.outsee_http.OUTSEE_WIRED_IMAGE_MODELS = (nano-banana-2, gpt-image-2). Only `gpt-image-2` has a
    measured price (live probe 2026-10-07: about $0.03 for one image, MEDIA_NODES_READINESS.md); nano-banana-2 has none
    on Outsee (Kie list price $0.06). Default generator gpt_image_2_vip -> slug `gpt-image-2`. The API client forces
    resolution "2K" for every image (outsee_http.generate_image), so the resolution is not selectable. Conservative price
    used for the authorisation: $0.10 per image (the probe step cap), the application's own estimate is $0.30.
  * videos: OUTSEE_WIRED_VIDEO_MODELS = (veo-3-1-lite,) - the ONLY wired video model (Seedance and Kling are Kie models
    and are not reachable here; a Seedance slug is refused by this script anyway). Resolution is forced to 720p for Veo
    Lite by the client (1080p is invalid). Durations allowed by the client: 4, 6, 8; the step passes none (default 8), so
    the stand forces the SHORTEST, 4 s, with a wrapper on outsee_http.generate_video (Veo returns about 8 s; the client
    trims to 4 locally). Price table (api-tracker/app/pricing.py): veo-3-1-lite $0.15 per generation. Conservative price
    used for the authorisation: $0.30 (twice the table). The application's own in-flight estimate of one Outsee video is
    $1.00 (spend_guard.OUTSEE_VIDEO_ESTIMATE_USD, uncalibrated): the stand sets the application's caps just above it so
    that exactly one submit is admitted. The sum for the three nodes is $0.10 + $0.10 + $0.30 = $0.50.
  * image-to-video is NOT used on the wire: the step needs a start frame on disk (the stand seeds one) but the wrapper
    sends a TEXT-TO-VIDEO request (no image_url): the production start-frame upload goes to a public file host
    (Yandex storage / litterbox / catbox) that the stand does not allow and the env whitelist does not configure. So the
    frame-upload path of the video step is NOT proven by this stand.

Modes (exactly one of --dry / --live is required):
  --dry   FREE, offline, no .env: a fake Outsee API on 127.0.0.1 plays the provider (submit / status / download, PNG for
          images and a real ffmpeg-made MP4 for video), the Decision model is a local stub, every non-loopback request is
          refused and counted.
  --live  PAID, capped: the same flow, but the ONE paid submit of the chosen node and its free status / download requests
          go to the real Outsee. Needs --env-file; only OUTSEE_API_KEY, OUTSEE_API_BASE_URL, OUTSEE_DEFAULT_IMAGE_MODEL (and
          with --real-decision the Vibecode names) are read from it, nothing else, nothing is printed. Hard caps:
          --max-real-posts (exactly 1), --max-usd (default 0.30 per node; refused when the node's conservative price
          exceeds it). The paid body is checked before it leaves: model must be the wired one, a video must be 720p,
          4 s, silent, text-only; a seedance / 1080p / unknown-model body is refused. Any other request is refused.
          NOT RUN in live mode when written.

Usage (repository root, project venv; --work-dir must be an empty dir OUTSIDE the repository):
    python scripts/e2e_media_production_route_more.py --node hero   --work-dir <dir> --dry
    python scripts/e2e_media_production_route_more.py --node items  --work-dir <dir> --dry
    python scripts/e2e_media_production_route_more.py --node videos --work-dir <dir> --dry
    python scripts/e2e_media_production_route_more.py --node hero   --work-dir <dir> --live --env-file <env file>
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
import tempfile
import threading
import time
import uuid as _uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
import e2e_guard as guard


def _load_script(name: str, alias: str):
    spec = importlib.util.spec_from_file_location(alias, _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


base = _load_script("e2e_media_production_route", "e2e_media_production_route_for_more")
qc = base.qc

PORT = 18778
FAKE_PORT = 18779
IMAGES_PATH = base.GENERATE_PATH
VIDEOS_PATH = "/api/v1/videos/generate"
PAID_PATHS = (IMAGES_PATH, VIDEOS_PATH)
STATUS_PREFIX = base.STATUS_PREFIX
BALANCE_PATH = base.BALANCE_PATH
FAKE_KEY = base.FAKE_KEY
OUTSEE_ENV_NAMES = base.OUTSEE_ENV_NAMES
DECISION_ENV_NAMES = base.DECISION_ENV_NAMES
SECRET_NAMES = base.SECRET_NAMES
IMAGE_GENERATORS = base.IMAGE_GENERATORS
DEFAULT_MAX_USD = 0.30
DEFAULT_MAX_REAL_POSTS = 1
VIDEO_MODEL = "veo-3-1-lite"
VIDEO_SECONDS = 4
VIDEO_RESOLUTION = "720p"
IMAGE_MODELS = ("gpt-image-2", "nano-banana-2")
TERMINAL_DECISION = base.TERMINAL_DECISION

HERO_DESC = "A friendly robot librarian with a round blue head, one amber eye and a small brass key, standing in a quiet library."
ITEM_DESC = "A brass pocket watch with an open lid, a thin chain and a blue enamel dial, on a plain light background."
ANIMATION_PROMPT = "Slow push-in towards the blue folder on the desk, soft morning light, no voice, no text."

FAULT_HITL = "simulated outcome loss: the HITL card of the new hero could not be sent"
FAULT_TAIL = "simulated outcome loss: the database write of the new item row failed"
LOST_CLIPS_DIR = "_stand_lost_clips"
FAULT_VALIDATE = "simulated outcome loss: the post-step validation of the video step crashed"


@dataclass(frozen=True)
class NodeProfile:
    node: str
    step_code: str
    target: str
    key: str
    paid_path: str
    price_usd: float
    result_dir: str
    kind: str
    file_prefix: str


PROFILES = {
    "hero": NodeProfile("hero", "hero", "media-hero-A", "hero_1_v1", IMAGES_PATH, 0.10, "characters", "image", "hero_1_v1"),
    "items": NodeProfile("items", "items", "media-items-A", "predmet1", IMAGES_PATH, 0.10, "items", "image", "predmet1"),
    "videos": NodeProfile("videos", "video", "media-videos-A", "clip_001", VIDEOS_PATH, 0.30, "videos", "video", "clip_001"),
}
NODES = ("images",) + tuple(PROFILES)



def worst_case_usd(node: str, max_real_posts: int) -> float:
    return round(max_real_posts * PROFILES[node].price_usd, 6)


def authorisation_problem(node: str, max_real_posts: int, max_usd: float) -> str | None:
    """Refuse a run whose conservative worst case exceeds what it was authorised to spend."""
    if node not in PROFILES:
        return f"unknown node {node!r}"
    if max_real_posts < 1 or max_usd <= 0:
        return "--max-real-posts must be >= 1 and --max-usd > 0"
    if worst_case_usd(node, max_real_posts) > max_usd + 1e-9:
        return (f"{max_real_posts} real submit(s) x ${PROFILES[node].price_usd} (conservative price of {node}) = "
                f"${worst_case_usd(node, max_real_posts)} exceeds --max-usd {max_usd}")
    return None


def gate_cap_usd(node: str, estimate_usd: float) -> float:
    """Application caps (run / project / daily): exactly one submit of the node passes, the second is refused.
    Image estimate $0.30 -> $0.45; the Outsee video estimate is $1.00 and one recorded unknown row counts $0.30 on top
    of a new reservation, so a cap of 1.2 estimates admits the first and refuses the second."""
    factor = 1.2 if node == "videos" else base.GATE_CAP_FACTOR
    return round(float(estimate_usd) * factor, 2)


def body_problem(path: str, body: dict) -> str | None:
    """Why a paid body must not leave (None = fine). Applied to the real host too: it is the stand's own authorisation."""
    model = str(body.get("model") or "").lower()
    if "seedance" in model:
        return "a Seedance body is forbidden"
    if path == IMAGES_PATH:
        if model not in IMAGE_MODELS:
            return f"image model {model!r} is not one of {IMAGE_MODELS}"
        if str(body.get("resolution") or "").upper() not in ("", "1K", "2K"):
            return f"image resolution {body.get('resolution')!r} is not allowed"
        return None
    if path == VIDEOS_PATH:
        if model != VIDEO_MODEL:
            return f"video model {model!r} is not {VIDEO_MODEL!r}"
        if str(body.get("resolution") or "").lower() != VIDEO_RESOLUTION:
            return f"video resolution {body.get('resolution')!r} is not {VIDEO_RESOLUTION} (1080p is forbidden)"
        if int(body.get("duration_sec") or 0) != VIDEO_SECONDS:
            return f"video duration {body.get('duration_sec')!r} is not the shortest ({VIDEO_SECONDS} s)"
        if body.get("generate_audio") is not False:
            return "a video must be silent (generate_audio false)"
        if body.get("image_url") or body.get("image_urls"):
            return "this stand sends text-to-video only (no frame upload)"
        return None
    return f"path {path!r} is not a paid path"


@dataclass
class MediaGate(base.Gate):
    """The images Gate plus: a paid POST of the OTHER media kind is never let through (probe: counted as a guard failure)."""
    paid_path: str = IMAGES_PATH

    def judge(self, method: str, host: str | None, port: int | None, path: str, *, is_decision: bool):
        if method.upper() == "POST" and path in PAID_PATHS and path != self.paid_path:
            host_l = (host or "").strip("[]").lower()
            if qc.is_loopback(host_l) and port == self.fake_port and self.phase != "main":
                self.probe_posts += 1
                return base.Judgement("pass", "fake_submit_during_probe", "a guard failed to stop the probe submit")
            return self._refuse("wrong_paid_path", f"the node pays on {self.paid_path} only (got {path})", host_l)
        return super().judge(method, host, port, path, is_decision=is_decision)



def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if not exe:
        raise RuntimeError("ffmpeg is required for the video stand (not on PATH)")
    return exe


def make_mp4(seconds: int = 8, size: str = "1280x720", with_audio: bool = True) -> bytes:
    """A real MP4 made by ffmpeg (lavfi): what Veo returns - about 8 s, 720p, AAC audio the client strips."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        cmd = [_ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}"]
        if with_audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast"]
        if with_audio:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd.append(str(out))
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        return out.read_bytes()


class FakeOutseeMedia:
    """The Outsee endpoints the application uses for an image AND for a video, on 127.0.0.1. Counts what it receives."""

    def __init__(self, port: int, key: str = FAKE_KEY):
        self.port, self.key = port, key
        self.posts_images = self.posts_videos = self.polls = self.downloads = self.balances = self.auth_failures = 0
        self.bodies: list[dict] = []
        self._png: bytes | None = None
        self._mp4: bytes | None = None
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
                if self.path not in PAID_PATHS:
                    return self._json(404, {"error": "not found"})
                if not self._auth():
                    return
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    return self._json(400, {"error": "bad json"})
                video = self.path == VIDEOS_PATH
                with outer._lock:
                    if video:
                        outer.posts_videos += 1
                        n = outer.posts_videos
                    else:
                        outer.posts_images += 1
                        n = outer.posts_images
                    outer.bodies.append({"path": self.path, "model": body.get("model"),
                                         "aspect_ratio": body.get("aspect_ratio"), "resolution": body.get("resolution"),
                                         "duration_sec": body.get("duration_sec"), "generate_audio": body.get("generate_audio"),
                                         "has_image": bool(body.get("image_url") or body.get("image_urls")),
                                         "prompt_chars": len(str(body.get("prompt") or ""))})
                self._json(200, {"id": f"{'fakevid' if video else 'fake'}-{n:04d}", "status": "queued"})

            def do_GET(self):
                if self.path.startswith(STATUS_PREFIX):
                    if not self._auth():
                        return
                    gid = self.path[len(STATUS_PREFIX):].split("?")[0]
                    with outer._lock:
                        outer.polls += 1
                    ext = "mp4" if gid.startswith("fakevid") else "png"
                    return self._json(200, {"id": gid, "status": "completed",
                                            "result_urls": [f"http://127.0.0.1:{outer.port}/files/{gid}.{ext}"]})
                if self.path == BALANCE_PATH:
                    if not self._auth():
                        return
                    with outer._lock:
                        outer.balances += 1
                    return self._json(200, {"balance": 10.0})
                if self.path.startswith("/files/"):
                    with outer._lock:
                        outer.downloads += 1
                        if self.path.endswith(".mp4"):
                            if outer._mp4 is None:
                                outer._mp4 = make_mp4()
                            return self._send(200, outer._mp4, "video/mp4")
                        if outer._png is None:
                            outer._png = base.make_png()
                        data = outer._png
                    return self._send(200, data, "image/png")
                return self._json(404, {"error": "not found"})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fake-outsee-media")

    def start(self) -> "FakeOutseeMedia":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def posts(self) -> int:
        return self.posts_images + self.posts_videos

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def counts(self) -> dict:
        return {"posts": self.posts, "posts_images": self.posts_images, "posts_videos": self.posts_videos,
                "polls": self.polls, "downloads": self.downloads, "auth_failures": self.auth_failures}


class StubGpt:
    """The hero prompt writer: local, free, and - like a real GPT - NOT deterministic (every answer differs)."""

    def __init__(self) -> None:
        self.calls = 0

    async def ask_fresh(self, text: str, timeout: float = 0, project_id: int | None = None, **_k) -> str:
        self.calls += 1
        return (f"Character reference sheet, variant {self.calls}: {HERO_DESC} Front view, side view and three-quarter view "
                f"on a neutral light grey background, even studio lighting, clean flat colours, no text, no logos. "
                f"(stub answer {self.calls}-{_uuid.uuid4().hex[:6]})")

    async def ask(self, text: str, *a, **k) -> str:
        return await self.ask_fresh(text)


@asynccontextmanager
async def _no_browser():
    yield None



def env_overrides(data: Path, port: int, *, fake_base: str, gate_cap: float, dry: bool) -> dict[str, str]:
    out = base.env_overrides(data, port, fake_base=fake_base, gate_cap=gate_cap, dry=dry)
    out.update({"VIDEO_PROVIDER": "outsee", "OUTSEE_DEFAULT_VIDEO_MODEL": VIDEO_MODEL})
    return out


def reality_report(node: str, mode: str, snap: dict) -> dict:
    live = mode == "live"
    counters = snap.get("counters", {})
    real_submits = counters.get("paid_posts", 0) if live else 0
    real_decisions = counters.get("decision_posts", 0) if live else 0
    prof = PROFILES[node]
    what = "video clip" if prof.kind == "video" else "image"
    step = {"hero": "generate_hero.run", "items": "generate_items.run", "videos": "generate_videos.run"}[node]
    real = [x for x in (
        "application, worker, observer, diagnosis, Decision supervisor, recovery and verification in-process",
        f"native executor {step}, outsee_retry, outsee_http, media_spend, outsee_guard, spend_guard (kill switch, disk guard, caps)"
        + (", outsee_idempotency, media_local_resume" if prof.kind == "image" else ", recover_scene_videos_from_disk"),
        f"{real_submits} real Outsee submit(s) with its status polls and one {what} download" if real_submits else None,
        f"{real_decisions} real Decision request(s) to Vibecode" if real_decisions else None,
        "ffprobe / PIL checks of the downloaded file (artifact_verifier.verify_artifact)") if x]
    not_real = [x for x in (
        f"seeded project: synthetic, built by this script (one {node} unit)",
        {"hero": "the loss of the outcome: injected locally (the HITL card send raises on every native attempt)",
         "items": "the loss of the outcome: injected locally (the database write of the new item row raises on every attempt)",
         "videos": "the loss of the outcome: injected locally (post-step validation and HITL text raise on every attempt)"}[node],
        None if live else "the provider: a fake Outsee API on 127.0.0.1 (nothing left the machine, nothing was paid)",
        "Decision model: local stub (nothing was sent to a provider)" if not (live and counters.get("decision_posts", 0)) else None,
        "hero prompt writer: a local GPT stub (answers differ per call, like a real GPT); no ChatGPT request" if node == "hero" else None,
        "the video request is TEXT-TO-VIDEO with duration forced to 4 s by a wrapper; the production start-frame upload "
        "(public file host) is not exercised" if node == "videos" else None,
        "Outsee has no task id on the client side: the recovery reads the RECORDED REQUEST-KEY RESULT, no provider task is polled"
        if prof.kind == "image" else
        "Outsee video has no request-key record and no task id: there is nothing for the supervisor to resume",
        "picture / clip quality and the meaning of the result: not judged",
        "the supervisor does not start the node: a human continues it") if x]
    return {"real": real, "not_real": not_real}


def reality_not_proven(node: str, mode: str) -> list[str]:
    out = ["the price of a real Outsee request is not calibrated by the application (cost_unknown rows)"]
    if node == "videos":
        out.append("supervised resume of an Outsee video: not implemented (no request-key record for video)")
        out.append("start-frame upload of the video step (image-to-video): not exercised")
    if mode != "live":
        out.insert(0, "anything about the real Outsee: this is a dry run on a fake provider, NOT a live proof")
    return out



def check(name: str, ok: bool, detail: Any = None, *, needs: str | None = None) -> dict:
    return qc.check(name, ok, detail, needs=needs)


def verdict(checks: list[dict]) -> tuple[bool, list[str]]:
    return qc.verdict(checks)


def artifact_checks(node: str, snap: dict) -> list[dict]:
    """The downloaded file, re-read by the application's own verifier (PIL for an image, ffprobe for a video)."""
    prof = PROFILES[node]
    art = snap.get("artifact_check") or {}
    out = [check("downloaded_file_passes_the_real_verifier", art.get("status") == "passed",
                 {k: art.get(k) for k in ("status", "reason", "bytes", "format", "width", "height", "duration", "streams")},
                 needs="artifact_verifier.verify_artifact (PIL / ffprobe)")]
    if prof.kind == "video":
        dur = art.get("duration") or 0
        out.append(check("clip_is_short_silent_and_not_above_720p",
                         art.get("status") == "passed" and 2.0 <= dur <= 5.5 and "audio" not in (art.get("streams") or [])
                         and 0 < min(art.get("height") or 0, art.get("width") or 0) <= 720,
                         {"duration": art.get("duration"), "streams": art.get("streams"),
                          "width": art.get("width"), "height": art.get("height")}))
    else:
        out.append(check("image_has_real_pixels", art.get("status") == "passed" and (art.get("width") or 0) > 0
                         and (art.get("height") or 0) > 0, {"width": art.get("width"), "height": art.get("height")}))
    return out


def evaluate_image_node(node: str, snap: dict, *, mode: str, max_real_posts: int = 1) -> list[dict]:
    """hero / items: the images checks of the sibling script on this node's key, plus the artifact and the body checks."""
    prof = PROFILES[node]
    saved = (base.FRAME_KEY, base.TARGET, base.NODE)
    base.FRAME_KEY, base.TARGET, base.NODE = prof.key, prof.target, node
    counters = dict(snap.get("counters", {}))
    injected = int(counters.get("injected_hitl", 0)) + int(counters.get("injected_tail", 0))
    counters["injected_hitl"] = counters["injected_tail"] = injected
    snap = {**snap, "counters": counters}
    try:
        checks = base.evaluate(snap, mode=mode, target=prof.target, max_real_posts=max_real_posts)
    finally:
        base.FRAME_KEY, base.TARGET, base.NODE = saved
    checks += artifact_checks(node, snap)
    checks += body_checks(node, snap)
    return checks


def body_checks(node: str, snap: dict) -> list[dict]:
    prof = PROFILES[node]
    bodies = snap.get("paid_bodies") or []
    return [
        check("paid_body_is_the_cheapest_allowed_and_clean",
              len(bodies) == 1 and all(body_problem(prof.paid_path, b) is None for b in bodies),
              {"bodies": bodies, "problems": [body_problem(prof.paid_path, b) for b in bodies]}),
        check("worst_case_within_authorisation", snap.get("worst_case_usd", 1e9) <= snap.get("max_usd", 0) + 1e-9,
              {"worst_case_usd": snap.get("worst_case_usd"), "max_usd": snap.get("max_usd")}),
    ]


def evaluate_videos(snap: dict, *, mode: str, max_real_posts: int = 1) -> list[dict]:
    """videos: no request-key record exists. What is proven: ONE paid POST, the clip survives the loss on disk, native
    retries adopt it (no second POST), the supervisor neither pays nor starts the node, guards stop the paid seam."""
    prof = PROFILES["videos"]
    counters = snap.get("counters", {})
    probes = snap.get("probes", {})
    paid = counters.get("paid_posts", 0)
    node_status = (snap.get("node_status") or {}).get(prof.target)
    attempts = int(snap.get("node_attempts") or 0)
    stepf = snap.get("step_failure") or {}
    clips = snap.get("clips_on_disk") or []
    recoveries = (snap.get("actions") or {}).get("recoveries") or []
    ticket = recoveries[0] if recoveries else {}
    ver = ticket.get("verification_result") or {}
    ledger = snap.get("ledger_rows") or []
    spend = [r for r in (snap.get("spend_rows") or []) if r.get("provider") == "outsee"]
    incidents = [i for i in (snap.get("diagnoses") or {}).get("incidents", []) if i.get("node_key") == prof.target]
    records = (snap.get("decisions") or {}).get("records") or []
    out = [
        check("exactly_one_paid_submit_at_most", paid == 1 and paid <= max_real_posts,
              {"paid_posts": paid, "max_real_posts": max_real_posts, "fake_posts": counters.get("fake_posts")}),
        check("provider_ledger_binds_exactly_one_submit",
              sum(1 for r in ledger if r.get("api") == "outsee" and str(r.get("key")) == prof.key
                  and r.get("event") == "intent_done" and r.get("status") == "bound") == 1
              and not any(r.get("event") == "intent_done" and r.get("status") not in ("bound", "rejected") for r in ledger
                          if r.get("api") == "outsee" and str(r.get("key")) == prof.key),
              [(r.get("event"), r.get("status"), r.get("key")) for r in ledger][:6]),
        check("outcome_loss_injected_and_native_retries_ran",
              counters.get("injected_validate", 0) >= 1 and attempts >= 3,
              {"validate": counters.get("injected_validate"), "node_attempts": attempts}),
        check("no_second_submit_after_the_loss", paid == 1 and attempts >= 3 and counters.get("refused_cap", 0) == 0,
              {"paid_posts": paid, "node_attempts": attempts, "refused_cap": counters.get("refused_cap")}),
        check("clip_survived_the_loss_on_disk_exactly_once", len(clips) == 1 and clips[0].get("exists") is True,
              clips),
        check("native_retries_exhausted_and_project_paused", bool(stepf.get("abandoned_at")) and node_status == "failed",
              {"step_failure": {k: stepf.get(k) for k in ("total_fails", "abandoned_at", "abandoned_threshold")},
               "node": node_status}),
        check("spend_ledger_written", len(spend) >= 1 and any("video" in str(r.get("note")) for r in spend),
              [(r.get("kind"), r.get("usd"), str(r.get("note"))[:50]) for r in spend][:4]),
        check("media_incident_typed_by_diagnosis", any(i.get("type") == "media_failure" and i.get("media") is True
                                                       for i in incidents),
              [(i.get("type"), i.get("status")) for i in incidents][-6:]),
        check("decision_loop_ran", bool(records) and records[0].get("status") in TERMINAL_DECISION,
              [(r.get("status"), r.get("model_steps")) for r in records]),
        check("supervisor_made_zero_new_paid_requests_and_did_not_start_the_node",
              paid == 1 and node_status == "failed" and (not ticket or (ver.get("new_paid_requests") in (0, None)
                                                                         and ver.get("node_started") in (False, None))),
              {"tickets": [(t.get("status"), (t.get("verification_result") or {}).get("new_paid_requests"))
                           for t in recoveries], "node": node_status}),
        check("kill_switch_stops_the_paid_seam", base._blocked(probes, "kill_switch", "kill_switch_active"),
              probes.get("kill_switch")),
        check("kill_switch_stops_the_recovery", (probes.get("kill_switch_recovery") or {}).get("refused") is True
              and (probes.get("kill_switch_recovery") or {}).get("tickets_after") == 0, probes.get("kill_switch_recovery")),
        check("disk_guard_stops_the_paid_seam", base._blocked(probes, "disk_guard", "disk_guard_blocked"),
              probes.get("disk_guard")),
        check("spend_cap_stops_the_paid_seam", base._blocked(probes, "spend_cap", "spend_cap_daily"), probes.get("spend_cap")),
        check("spend_cap_stops_a_second_submit", base._blocked(probes, "second_submit_cap", "spend_cap_daily"),
              probes.get("second_submit_cap")),
        check("other_request_under_resume_only_is_refused", (probes.get("resume_only_other") or {}).get("refused") is True
              and (probes.get("resume_only_other") or {}).get("posts_delta") == 0, probes.get("resume_only_other")),
        check("no_probe_reached_a_provider", counters.get("probe_posts", 0) == 0, counters.get("probe_posts")),
        check("no_unexpected_model_or_foreign_request", counters.get("unexpected_model", 0) == 0
              and not snap.get("refused_hosts"), {"unexpected_model": counters.get("unexpected_model"),
                                                  "refused": snap.get("refused_hosts")}),
        check("hitl_none_pending", not snap.get("hitl_pending"), snap.get("hitl_pending")),
    ]
    out += artifact_checks("videos", snap)
    out += body_checks("videos", snap)
    if mode == "live":
        out += [check("caps_not_exceeded", paid <= max_real_posts and counters.get("decision_posts", 0)
                      <= snap.get("max_decision_posts", base.DEFAULT_MAX_DECISION_POSTS),
                      {"paid_posts": paid, "decision_posts": counters.get("decision_posts")}),
                check("real_submit_sent", paid == 1, paid)]
    else:
        out.append(check("provider_independently_saw_one_submit",
                         counters.get("fake_posts") == 1 and counters.get("fake_auth_failures") == 0,
                         {"fake_posts": counters.get("fake_posts"), "auth_failures": counters.get("fake_auth_failures")}))
        out.append(check("dry_nothing_left_the_machine", counters.get("refused_foreign", 0) == 0
                         and not snap.get("refused_hosts") and paid == 1, {"refused": snap.get("refused_hosts")}))
    return out


def evaluate(node: str, snap: dict, *, mode: str, max_real_posts: int = 1) -> list[dict]:
    if node == "videos":
        return evaluate_videos(snap, mode=mode, max_real_posts=max_real_posts)
    return evaluate_image_node(node, snap, mode=mode, max_real_posts=max_real_posts)


def final_verdict(node: str, mode: str, ok: bool) -> str:
    if not ok:
        return "NOT PASSED"
    if mode == "live":
        return f"PASS (all checks, live: one real Outsee submit; {node} machinery proven end to end on the real provider)"
    return f"DRY OK ({node}: fake provider and stub Decision: machinery proven offline, NOT a live proof)"



def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--node", choices=NODES, default="images", help="media node (default images = the sibling script)")
    p.add_argument("--dry", action="store_true", help="FREE: fake Outsee on 127.0.0.1 + Decision stub")
    p.add_argument("--live", action="store_true", help="PAID, capped: ONE real Outsee submit of the node (needs --env-file)")
    p.add_argument("--env-file", help="env file; only " + ", ".join(OUTSEE_ENV_NAMES) + " (and the Vibecode names with "
                   "--real-decision) are read from it")
    p.add_argument("--work-dir", help="scratch dir for data/, runtime.json, result.json (recreated)")
    p.add_argument("--max-real-posts", type=int, default=DEFAULT_MAX_REAL_POSTS, help="hard cap of real paid submits (live)")
    p.add_argument("--max-usd", type=float, default=DEFAULT_MAX_USD,
                   help="authorised spend for this node; refused when the node's conservative price exceeds it")
    p.add_argument("--real-decision", action="store_true", help="live: the Decision model is the real Vibecode (capped)")
    p.add_argument("--max-decision-posts", type=int, default=base.DEFAULT_MAX_DECISION_POSTS)
    p.add_argument("--image-generator", choices=IMAGE_GENERATORS, default="gpt_image_2_vip",
                   help="hero / items: Outsee image generator id (default: the only one with a measured price)")
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
    if args.node != "images":
        problem = authorisation_problem(args.node, args.max_real_posts, args.max_usd)
        if problem:
            p.error(problem)
    if args.live and args.max_real_posts != 1:
        p.error("--live allows exactly one real paid submit (--max-real-posts 1)")
    return args


def _strip_node(argv: list[str]) -> list[str]:
    out, skip = [], False
    for i, a in enumerate(argv):
        if skip:
            skip = False
            continue
        if a == "--node":
            skip = True
            continue
        if a.startswith("--node="):
            continue
        out.append(a)
    return out


async def _seed(args: argparse.Namespace, prof: NodeProfile) -> tuple[int, str]:
    """Workflow + Project + WorkflowRun + ONE pending node of the profile + the unit it needs."""
    from app.db import session_scope
    from app.models import Frame, NodeRun, NodeRunStatus, Project, ProjectStatus, Workflow, WorkflowRun, WorkflowRunStatus

    node = {"id": prof.target, "type": prof.node, "position": {"x": 100.0, "y": 100.0},
            "data": {"label": f"Native {prof.node}", "groupId": base.GROUP_ID}}
    nodes, edges = [node], []
    slug = f"media-{prof.node}-{_uuid.uuid4().hex[:6]}"
    voiceover = base.VOICEOVER
    extra: dict[str, Any] = {"hero": dict(status=ProjectStatus.frames_ready, hero_mode="hero", hero_count=1,
                                          hero_descriptions=[HERO_DESC], hero_variations=[1]),
                             "items": dict(status=ProjectStatus.hero_ready, item_descriptions=[ITEM_DESC]),
                             "videos": dict(status=ProjectStatus.animation_prompts_ready, video_generator="veo_3_1_lite",
                                            video_resolution=VIDEO_RESOLUTION, video_relax=True)}[prof.node]
    from app.web.settings_default import seed_default_workflow
    async with session_scope() as session:
        await seed_default_workflow(session)
        wf = Workflow(name=f"wf-{slug}", is_default=False, nodes=nodes, edges=edges)
        session.add(wf)
        await session.flush()
        meta = {"canvas_graph": {"workflow_id": wf.id, "nodes": nodes, "edges": edges}, "img_streams": 1}
        project = Project(slug=slug, topic=f"media {prof.node} production route e2e", auto_mode=False, general_plan=voiceover,
                          script_text=voiceover, meta=meta, image_generator=args.image_generator, aspect_ratio="9_16",
                          image_resolution="2k", **extra)
        session.add(project)
        await session.flush()
        run = WorkflowRun(project_id=project.id, workflow_id=wf.id, status=WorkflowRunStatus.new,
                          nodes_snapshot=nodes, edges_snapshot=edges)
        session.add(run)
        await session.flush()
        session.add(NodeRun(workflow_run_id=run.id, node_key=prof.target, node_type=prof.node, status=NodeRunStatus.pending))
        session.add(Frame(project_id=project.id, number=1, uuid=("01" * 12), sort_key=1.0, voiceover_text=voiceover,
                          image_prompt=base.IMAGE_PROMPT, animation_prompt=ANIMATION_PROMPT, duration_seconds=3.0, attrs={}))
        await session.flush()
        pid, data_dir = int(project.id), project.data_dir
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "voiceover.txt").write_text(voiceover, encoding="utf-8")
    if prof.node == "videos":
        scenes = data_dir / "scenes"
        scenes.mkdir(parents=True, exist_ok=True)
        (scenes / "frame_001_abcdef01.png").write_bytes(base.make_png())
    return pid, slug


def _install_faults(prof: NodeProfile, counters: dict, restore: list) -> None:
    """Stand-level patches only: the outcome loss and the browser / GPT seams that would bypass the send hook."""
    def patch(obj, name, value):
        restore.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    async def lost(counter: str, text: str):
        counters[counter] += 1
        raise RuntimeError(text)

    if prof.node == "hero":
        from app.orchestrator.steps import generate_hero
        stub = StubGpt()
        counters["gpt_stub"] = stub
        patch(generate_hero, "get_gpt_client", lambda: stub)
        patch(generate_hero, "browser_session", _no_browser)

        async def lost_hitl(*_a, **_k):
            await lost("injected_hitl", FAULT_HITL)
        patch(generate_hero, "send_hitl_photo", lost_hitl)
    elif prof.node == "items":
        from sqlalchemy.ext.asyncio import AsyncSession
        from app.models import Artifact, ArtifactKind
        from app.orchestrator.steps import generate_items
        patch(generate_items, "browser_session", _no_browser)
        original_flush = AsyncSession.flush

        async def failing_write(self, *a, **k):
            if any(isinstance(o, Artifact) and o.kind is ArtifactKind.item_reference for o in self.sync_session.new):
                await lost("injected_tail", FAULT_TAIL)
            return await original_flush(self, *a, **k)
        patch(AsyncSession, "flush", failing_write)
    else:
        from app.bots import outsee_http
        from app.orchestrator.steps import generate_videos
        from app.services import post_step_validate
        if hasattr(generate_videos, "browser_session"):
            patch(generate_videos, "browser_session", _no_browser)

        async def broken_validate(*a, **_k):
            project = a[1] if len(a) > 1 else None
            root = getattr(project, "data_dir", None)
            if root is not None:
                aside = Path(root) / LOST_CLIPS_DIR
                aside.mkdir(parents=True, exist_ok=True)
                for clip in (Path(root) / "videos").glob("clip_*.mp4"):
                    if clip.is_file():
                        clip.replace(aside / clip.name)
            await lost("injected_validate", FAULT_VALIDATE)

        async def lost_text(*_a, **_k):
            await lost("injected_hitl", FAULT_HITL)
        patch(post_step_validate, "finalize_or_retry", broken_validate)
        patch(generate_videos, "send_hitl_text", lost_text)
        original_video = outsee_http.generate_video

        async def short_text_video(prompt, out_path, **kw):
            kw["duration"] = VIDEO_SECONDS
            for name in ("reference_image", "last_frame_image", "first_frame_url", "last_frame_url"):
                kw.pop(name, None)
            return await original_video(prompt, out_path, **kw)
        patch(outsee_http, "generate_video", short_text_video)


async def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _args(argv)
    if args.node == "images":
        return await base.main(_strip_node(argv))
    prof = PROFILES[args.node]
    mode = "live" if args.live else "dry"
    live_env: dict[str, str] = {}
    if mode == "live":
        live_env = base.load_env_whitelist(args.env_file, OUTSEE_ENV_NAMES + (DECISION_ENV_NAMES if args.real_decision else ()))
        if not live_env.get("OUTSEE_API_KEY"):
            print("env file has no OUTSEE_API_KEY; nothing sent")
            return 3
        problem = base.base_problem(live_env)
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

    if "app.settings" in sys.modules:
        print("refused: the application settings were loaded before this stand set its scratch environment "
              "(they would point at the live data folder); run the stand in its own process; nothing was started")
        return 3

    base.NODE, base.STEP_CODE, base.TARGET, base.FRAME_KEY = prof.node, prof.step_code, prof.target, prof.key
    base.GENERATE_PATH = prof.paid_path

    work = Path(args.work_dir).resolve()
    data = guard.reset_scratch_data(work)
    os.chdir(ROOT)
    fake = FakeOutseeMedia(args.fake_port).start()
    decision_endpoint = qc.live_endpoint(live_env) if (mode == "live" and args.real_decision) else None
    runtime_path = work / "runtime.json"
    runtime_path.write_text(json.dumps(base._runtime_config(args.port, args.request_limit, decision_endpoint), indent=2),
                            encoding="utf-8")

    os.environ.update(env_overrides(data, args.port, fake_base=fake.base_url, gate_cap=0.45, dry=(mode == "dry")))
    from app.services import spend_guard
    estimate = float(spend_guard.OUTSEE_VIDEO_ESTIMATE_USD if prof.kind == "video" else spend_guard.MEDIA_SUBMIT_ESTIMATE_USD)
    cap = gate_cap_usd(prof.node, estimate)
    os.environ.update({"SPEND_DAILY_CAP_USD": str(cap), "SPEND_PROJECT_CAP_USD": str(cap), "SPEND_RUN_CAP_USD": str(cap)})
    if mode == "live":
        os.environ["OUTSEE_API_KEY"] = live_env["OUTSEE_API_KEY"]
        os.environ["OUTSEE_API_BASE_URL"] = base.outsee_base(live_env)
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
    real_base = base.outsee_base(live_env) if mode == "live" else fake.base_url
    settings.outsee_api_base_url = real_base
    settings.image_provider = "outsee"
    settings.video_provider = "outsee"
    settings.outsee_default_image_model = live_env.get("OUTSEE_DEFAULT_IMAGE_MODEL") or "gpt-image-2"
    settings.outsee_default_video_model = VIDEO_MODEL
    settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = cap

    import app.services.api_tracker_hook as tracker
    tracker._TRACKER_DB = work / "tracker.db"
    if tracker._TRACKER_DB.exists():
        tracker._TRACKER_DB.unlink()
    from app.services import outsee_idempotency, step_failure_policy
    from app.services.decision_transport import current as decision_hook
    step_failure_policy.SLEEP_MINUTES = args.sleep_minutes
    step_failure_policy.XLSX_SHEET_FORMAT_SLEEP_MINUTES = args.sleep_minutes
    from app import main as app_main
    if Path(str(settings.data_dir)).resolve() != Path(data).resolve():
        fake.stop()
        print(f"refused: the application settings point at {settings.data_dir}, not at the scratch data {data}; "
              "nothing was started or written", flush=True)
        return 3
    await app_main._init_db()
    pid, slug = await _seed(args, prof)
    project_db = data / "videos" / slug / "project.db"
    state_db = data / "state.db"
    project_root = data / "videos" / slug
    api = f"http://127.0.0.1:{args.port}/api/projects/{pid}"

    gate = MediaGate(mode=mode, max_paid_posts=args.max_real_posts,
                     outsee_host=base.outsee_host(live_env) if mode == "live" else "", fake_port=args.fake_port,
                     real_decision=args.real_decision,
                     decision_hosts=qc.allowed_hosts(live_env) if args.real_decision else frozenset(),
                     max_decision_posts=args.max_decision_posts, paid_path=prof.paid_path)
    counters: dict[str, Any] = {"injected_hitl": 0, "injected_tail": 0, "injected_validate": 0, "decision_stub": 0}
    decision_contexts: list[dict] = []
    seen_prompts: list[dict] = []
    paid_bodies: list[dict] = []
    original_send = httpx.AsyncClient.send
    original_make_ctx = outsee_idempotency.make_ctx

    def spy_make_ctx(project_id, out_path, model, prompt, media="image"):
        ctx = original_make_ctx(project_id, out_path, model, prompt, media)
        if ctx.usable and ctx.key == prof.key:
            seen_prompts.append({"model": model, "prompt": prompt})
        return ctx

    async def send(client, request, **kwargs):
        host, port, path = request.url.host, request.url.port, request.url.path
        if request.method.upper() == "POST" and path in PAID_PATHS and gate.phase == "main":
            try:
                body = json.loads(request.content.decode("utf-8") or "{}")
            except ValueError:
                body = {}
            problem = body_problem(path, body) if path == prof.paid_path else f"wrong paid path {path}"
            if problem:
                gate.refused.append({"host": str(host), "kind": "paid_body", "reason": problem[:160]})
                raise RuntimeError("paid body refused: " + problem)
            paid_bodies.append({"path": path, "model": body.get("model"), "resolution": body.get("resolution"),
                                "aspect_ratio": body.get("aspect_ratio"), "duration_sec": body.get("duration_sec"),
                                "generate_audio": body.get("generate_audio"),
                                "has_image": bool(body.get("image_url") or body.get("image_urls")),
                                "prompt_chars": len(str(body.get("prompt") or ""))})
        is_decision = decision_hook.get() is not None
        judged = gate.judge(request.method, host, port, path, is_decision=is_decision)
        if judged.action == "refuse":
            if paid_bodies and judged.kind in ("paid_cap",) and request.method.upper() == "POST":
                paid_bodies.pop()
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
    restore: list = []

    async def no_curl_post(*_a, **_k):
        return None

    original_download_curl = outsee_http._download_via_curl

    async def guarded_download_curl(url, out_path):
        from urllib.parse import urlsplit
        host = (urlsplit(url).hostname or "").lower()
        if qc.is_loopback(host) or (mode == "live" and (host == gate.outsee_host or host in gate.learned_hosts)):
            return await original_download_curl(url, out_path)
        return False

    _install_faults(prof, counters, restore)
    restore.append((outsee_http, "_post_generate_via_curl", outsee_http._post_generate_via_curl))
    restore.append((outsee_http, "_download_via_curl", original_download_curl))
    restore.append((outsee_idempotency, "make_ctx", original_make_ctx))
    outsee_idempotency.make_ctx = spy_make_ctx
    outsee_http._post_generate_via_curl = no_curl_post
    outsee_http._download_via_curl = guarded_download_curl
    httpx.AsyncClient.send = send

    result: dict[str, Any] = {"mode": mode, "node": prof.node, "target": prof.target, "project_id": pid, "slug": slug,
                              "args": {k: v for k, v in vars(args).items() if k != "env_file"},
                              "app_estimate_usd": estimate, "app_caps_usd": cap,
                              "worst_case_usd": worst_case_usd(prof.node, args.max_real_posts), "max_usd": args.max_usd}
    probes: dict[str, Any] = {}
    advisories: list[str] = []
    task = asyncio.create_task(app_main.main())
    execution = None
    try:
        await qc._wait_api(api)
        if prof.kind == "video":
            result["model_slug_sent"] = VIDEO_MODEL
        else:
            from app.generation_options import IMAGE_GENERATORS_BY_ID
            result["model_slug_sent"] = outsee_http.studio_id_to_outsee_image_slug(
                IMAGE_GENERATORS_BY_ID[args.image_generator].outsee_slug)
        from app.services import disk_guard
        free_gb = disk_guard.check(paid=True).free_gb
        result["disk_free_gb"] = round(free_gb, 1) if free_gb is not None else None
        print(f"preflight: node={prof.node} mode={mode} model={result['model_slug_sent']} app_estimate=${estimate} "
              f"app_caps=${cap} worst_case=${result['worst_case_usd']} disk_free_gb={result['disk_free_gb']}", flush=True)
        from app.services import access_guard
        operator = {"x-vp-operator": access_guard.get_operator_token()}
        async with httpx.AsyncClient(timeout=30, trust_env=False, headers=operator,
                                     limits=httpx.Limits(max_keepalive_connections=0)) as client:
            await _run_flow(args, prof, mode, client, api, fake, gate, counters, probes, result, advisories, project_db,
                            state_db, project_root, pid, seen_prompts, real_base)
            execution = result.get("execution_id")
        result["artifact_check"] = await _verify_downloaded(prof, project_root, state_db)
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
        for obj, name, value in reversed(restore):
            setattr(obj, name, value)
        fake.stop()
    await asyncio.sleep(1)
    return _finish(args, prof, mode, result, gate, counters, probes, advisories, decision_contexts, fake, state_db, project_db,
                   project_root, execution, scrub, work, paid_bodies)


async def _verify_downloaded(prof: NodeProfile, project_root: Path, state_db: Path) -> dict:
    """The file the provider handed us, re-read by the application's own verifier (PIL / ffprobe), not by the stand."""
    from app.services.artifact_verifier import verify_artifact
    directory = project_root / prof.result_dir
    pattern = prof.file_prefix + "_*" + (".mp4" if prof.kind == "video" else ".png")
    files = [p for d in (directory, project_root / LOST_CLIPS_DIR) if d.is_dir() for p in d.glob(pattern) if p.is_file()]
    files.sort(key=lambda p: p.stat().st_mtime)
    if not files:
        return {"status": "failed", "reason": "no_result_file", "dir": prof.result_dir}
    got = await verify_artifact(files[-1], root=project_root, kind=prof.kind)
    return {k: v for k, v in got.items() if k in ("status", "reason", "bytes", "sha256", "format", "width", "height",
                                                 "duration", "streams")} | {"file": files[-1].name}



async def _run_flow(args, prof, mode, client, api, fake, gate, counters, probes, result, advisories, project_db, state_db,
                    project_root, pid, seen_prompts, real_base) -> None:
    from app.bots import outsee_http
    from app.services import media_spend
    from app.settings import settings
    prefix = api + "/observations"

    settings.outsee_api_base_url = fake.base_url
    gate.phase = "probe"
    await _probe_guards(prof, client, api, fake, gate, probes, project_root)
    settings.outsee_api_base_url = real_base
    gate.phase = "main"

    r = await client.post(prefix, json={"node_keys": [prof.target]})
    print("observation:", r.status_code, flush=True)
    r.raise_for_status()
    execution = r.json()["execution_id"]
    result["execution_id"] = execution
    balance_before = await base._balance(outsee_http) if mode == "live" else {}
    r = await client.post(api + f"/steps/{prof.step_code}/run", params={"node_key": prof.target})
    print(f"start {prof.node}:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
    r.raise_for_status()

    await base._wait_exhausted(args, project_db, counters, gate, fake)
    result["native_exhausted_at"] = time.strftime("%H:%M:%S")
    balance_after = await base._balance(outsee_http) if mode == "live" else {}
    if mode == "live":
        delta = base.balance_delta(balance_before, balance_after)
        result["balance"] = {"before": balance_before, "after": balance_after, "delta": delta}
        advisories.append(f"Outsee balance fields before/after the run (unit not documented): delta={delta or 'not measurable'}")

    incident = await base._wait_incident(client, prefix + f"/{execution}", args.timeout_s)
    result["incident_id"] = incident["incident_id"] if incident else None
    if not incident:
        raise RuntimeError(f"no current media incident of the {prof.node} node after the native retries were exhausted")

    await media_spend.drain()
    settings.outsee_api_base_url = fake.base_url
    gate.phase = "probe"
    await _probe_after(prof, fake, gate, probes, project_root, pid, seen_prompts)
    settings.outsee_api_base_url = real_base
    gate.phase = "main"
    result["fake_after_probes"] = fake.counts()

    r = await client.post(prefix + f"/{execution}/actions/enable", json={
        "group_id": base.GROUP_ID, "node_keys": [prof.target], "rationale": f"media {prof.node} production route e2e",
        "request_limit": min(args.request_limit, 60)})
    print("actions/enable:", r.status_code, (r.text[:300] if r.status_code >= 300 else "ok"), flush=True)
    r.raise_for_status()
    await base._probe_kill_switch_recovery(client, api, prefix + f"/{execution}", incident["incident_id"], probes)
    r = await client.post(prefix + f"/{execution}/decisions/control", json={
        "enabled": True, "rationale": f"{mode} decision loop for the media recovery verification"})
    print("decisions/control:", r.status_code, flush=True)
    r.raise_for_status()
    await base._poll_recovery(client, prefix + f"/{execution}", args, project_db, counters, gate, result)


def _probe_call(prof: NodeProfile, out: Path, name: str):
    from app.bots import outsee_http
    if prof.kind == "video":
        return outsee_http.generate_video("probe", out / f"{name}.mp4", model_slug=VIDEO_MODEL, project_id=None)
    return outsee_http.generate_image("probe", out / f"{name}.png", model_slug="nano-banana-2", project_id=None)


async def _probe_guards(prof, client, api, fake, gate, probes, project_root) -> None:
    from app.services import spend_guard
    from app.settings import settings
    out = project_root / "probe"
    out.mkdir(parents=True, exist_ok=True)

    def call():
        return _probe_call(prof, out, "clip_900_aaaaaaaa" if prof.kind == "video" else "frame_900_aaaaaaaa")

    prefix = api + "/observations"
    r = await client.post(prefix + "/kill-switch", json={"active": True, "rationale": "e2e probe: kill switch on"})
    r.raise_for_status()
    await base._probe_one("kill_switch", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    r = await client.post(prefix + "/kill-switch", json={"active": False, "rationale": "e2e probe: kill switch off"})
    r.raise_for_status()
    saved = {k: os.environ.get(k) for k in ("DISK_GUARD_LOW_FREE_GB", "DISK_GUARD_CRITICAL_FREE_GB")}
    os.environ["DISK_GUARD_LOW_FREE_GB"] = os.environ["DISK_GUARD_CRITICAL_FREE_GB"] = "1000000000"
    try:
        await base._probe_one("disk_guard", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    saved_cap = settings.spend_daily_cap_usd
    settings.spend_daily_cap_usd = 0.01
    try:
        await base._probe_one("spend_cap", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))
    finally:
        settings.spend_daily_cap_usd = saved_cap
    probes["guards_restored"] = {"kill_switch_off": True, "disk_env_restored": True, "cap_restored": saved_cap}


async def _probe_after(prof, fake, gate, probes, project_root, pid, seen_prompts) -> None:
    from app.services import media_spend, outsee_idempotency, outsee_retry, spend_guard
    folder = project_root / prof.result_dir
    if prof.kind == "image":
        entry: dict[str, Any] = {"adopted": False}
        before = fake.posts + gate.paid_posts + gate.probe_posts
        if seen_prompts:
            seen = seen_prompts[-1]
            try:
                res = await outsee_retry.generate_image_with_retries(
                    None, None, prompt=seen["prompt"], out_path=folder / f"{prof.key}_deadbeef.png",
                    model_slug=seen["model"], project_id=pid, gpt_rewrite=False, idempotent=True)
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
                    None, None, prompt="a different request for the same unit", out_path=folder / f"{prof.key}_cafebabe.png",
                    model_slug=(seen_prompts[-1]["model"] if seen_prompts else None), project_id=pid, gpt_rewrite=False,
                    idempotent=True)
        except outsee_idempotency.OutseeResumeOnlyError:
            entry["refused"] = True
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        entry["posts_delta"] = fake.posts + gate.paid_posts + gate.probe_posts - before
        probes["resume_only_other"] = entry
    else:
        from app.settings import settings
        entry = {"refused": False}
        before = fake.posts + gate.paid_posts + gate.probe_posts
        saved_caps = (settings.spend_daily_cap_usd, settings.spend_project_cap_usd, settings.spend_run_cap_usd)
        settings.spend_daily_cap_usd = settings.spend_project_cap_usd = settings.spend_run_cap_usd = 1000.0
        try:
            from app.bots import outsee_http
            tracked = media_spend.outsee_tracked(outsee_http.generate_video, "video")
            with media_spend.resume_only():
                await tracked("another clip", folder / "clip_002_cafebabe.mp4", model_slug=VIDEO_MODEL, project_id=pid)
        except media_spend.ResumeOnlyBlocked:
            entry["refused"] = True
        except Exception as exc:
            entry["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
        finally:
            settings.spend_daily_cap_usd, settings.spend_project_cap_usd, settings.spend_run_cap_usd = saved_caps
        entry["posts_delta"] = fake.posts + gate.paid_posts + gate.probe_posts - before
        probes["resume_only_other"] = entry

    def call():
        return _probe_call(prof, folder, "clip_002_aaaaaaaa" if prof.kind == "video" else "frame_002_aaaaaaaa")

    await base._probe_one("second_submit_cap", probes, fake, gate, call, (spend_guard.PaidCallBlocked,))


def _finish(args, prof, mode, result, gate, counters, probes, advisories, decision_contexts, fake, state_db, project_db,
            project_root, execution, scrub, work, paid_bodies) -> int:
    records = base.read_records(state_db)
    snap: dict[str, Any] = {}
    idem_results = [r for r in records["idem_rows"] if r.get("key") == prof.key and r.get("event") == "result"]
    recorded: dict[str, Any] = {"exists": False}
    rel = None
    if idem_results:
        path = Path(idem_results[-1]["file_path"])
        recorded = {"path_name": path.name, "exists": path.is_file(),
                    "sha256": base.sha256_file(path) if path.is_file() else None,
                    "bytes": path.stat().st_size if path.is_file() else 0}
        try:
            rel = path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            rel = path.name
    clips = []
    if prof.kind == "video":
        folder = project_root / prof.result_dir
        clips = [{"name": p.name, "exists": True, "bytes": p.stat().st_size}
                 for d in (folder, project_root / LOST_CLIPS_DIR) if d.is_dir()
                 for p in sorted(d.glob(prof.file_prefix + "_*.mp4"))]
    events = qc._events(state_db, execution)
    attempts = sum(1 for e in events if e["kind"] == "node_start" and e.get("node_key") == prof.target)
    fs = qc._step_failure(project_db)
    stub = counters.pop("gpt_stub", None)
    snap = {
        **result, **records,
        "counters": {**counters, "paid_posts": gate.paid_posts, "probe_posts": gate.probe_posts,
                     "decision_posts": gate.decision_posts, "refused_cap": gate.refused_cap,
                     "unexpected_model": gate.unexpected_model,
                     "refused_foreign": len([r for r in gate.refused if r["kind"] in ("dry_foreign", "foreign")]),
                     "fake_posts": fake.posts if mode == "dry" else None, "fake_auth_failures": fake.auth_failures,
                     "gpt_stub_calls": getattr(stub, "calls", None)},
        "probes": probes, "recorded_file": recorded, "recorded_relative_path": rel, "clips_on_disk": clips,
        "node_status": base._node_status(project_db), "node_attempts": attempts, "step_failure": fs,
        "hitl_pending": qc._hitl_pending(project_db), "decision_contexts": decision_contexts, "paid_bodies": paid_bodies,
        "refused_hosts": sorted({r["host"] for r in gate.refused
                                 if r["kind"] in ("dry_foreign", "foreign", "outsee_other", "wrong_paid_path", "paid_body")}),
        "max_decision_posts": args.max_decision_posts,
    }
    checks = evaluate(prof.node, snap, mode=mode, max_real_posts=args.max_real_posts)
    ok, failed = verdict(checks)
    if result.get("error"):
        ok = False
        failed = ["flow_error"] + failed
        checks.insert(0, check("flow_completed_without_error", False, scrub(str(result["error"]))))
    reality = reality_report(prof.node, mode, snap)
    summary = {
        "mode": mode, "node": prof.node, "verdict": final_verdict(prof.node, mode, ok), "failed_checks": failed,
        "paid_posts": gate.paid_posts, "decision_posts": gate.decision_posts, "probe_posts": gate.probe_posts,
        "caps": {"max_real_posts": args.max_real_posts, "max_usd": args.max_usd, "app_caps_usd": result.get("app_caps_usd"),
                 "worst_case_usd": result.get("worst_case_usd")},
        "model_slug_sent": result.get("model_slug_sent"), "node_attempts": attempts, "advisories": advisories,
        "not_proven": reality_not_proven(prof.node, mode)}
    slim = {k: v for k, v in snap.items() if k not in ("idem_rows",)}
    slim.update(checks=checks, ok=ok, failed=failed, reality=reality, summary=summary,
                idem_events=[(r["event"], r["key"]) for r in records["idem_rows"]])
    (work / "result.json").write_text(scrub(json.dumps(slim, ensure_ascii=False, indent=2, default=str)), encoding="utf-8")
    print("\n=== RESULT ===")
    print(f"node: {prof.node} (Outsee) | mode: {mode} | node status: {snap['node_status']} | native attempts: {attempts}")
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
