"""F10 (dry part): ONE tiny project through the WHOLE production chain on a REAL server process and the REAL worker.

    python -X utf8 scripts/e2e_full_chain.py --dry --until E --work-dir C:\\video-pipeline\\_f10_work\\dry1

Plan: docs/REGISTRAR_FULL_PLAN.md phase F, row F10 (this file is the DRY part: loopback fakes, no money, no keys, no
network beyond 127.0.0.1; the live run is a separate, owner-authorised step).

What it does
  * a fresh empty work dir OUTSIDE the repository (state.db, data dir, tracker db, copy of prompts/);
  * the real ``python -m app.main`` as a child (through the sibling stand's thin launcher: socket guard, secrets
    blanked) - so the real FastAPI app, the real worker loop, the real auto_advance, the real step executors;
  * ONE loopback server plays EVERY provider (text LLM chat/completions, Outsee image + video, ElevenLabs TTS + SFX,
    Kie Suno music); each paid seam is counted by the server and, independently, by the send hook inside the child;
  * the project is created through the real API with a topic, auto mode is switched on, the pipeline is started with
    the UI start of its first step; the stand polls until the end level or a stop; the human steps (final approval)
    are done through the production routes, never by patching.
  * it records per node status / timing, provider request counts, spend_ledger rows by node, operator_audit rows of the
    stand's own calls, the Russian timeline, the final.mp4 (ffprobe), and scans the work dir for secrets.

Levels (``--until``; the stand reports honestly the highest level REACHED and why it stopped). The default graph is
linear and interleaves text and media nodes, so a level is "everything up to this status":
  A  plan, script (+ plan/script verdict), split, excel_gpt(1)            ends at enrich_1_ready (no media before it)
  B  + hero / items (skipped: the tiny project has no characters), excel_gpt(2), image_prompts, images      images_ready
  C  + animation_prompts, videos (image-to-video: the start frame goes to a public file host, faked on loopback)
  D  + audio (TTS + real faster-whisper alignment), music (Kie Suno), sfx_plan, sfx_gen (ElevenLabs)         sfx_ready
  E  + assemble + approve_final (the stand is the HUMAN: POST /api/hitl/{id}/decision); publication is NOT started

STAND SEAMS (``SEAMS``; each one is listed in the report as "not real" and tied to a finding, ``--no-seams`` /
``--without-seam NAME`` switch them off to reproduce the finding):
  auto_on_project_db  F10-D1  PRODUCTION FLAG WORKER_USE_PROJECT_DB=true (no code patch any more): the worker's auto_advance
                      runs on the project's own project.db session
(F10-D2 store_mirror and F10-D5 hitl_to_master were removed: both fixed in W45, the stand passes without them.)

``--keep-auto`` (level E) does not hand the final to a human: it only watches whether the automation approves it (F10-D6).

LIVE mode (``--live`` / ``--live-preflight``) is in scripts/e2e_full_chain_live.py (same Chain / Report / checks, real
providers, hard spend ceilings); this file delegates to it. Run ``--live-preflight`` first: it pays nothing.

Exit codes: 0 PASS (no FAIL; KNOWN findings allowed unless --strict), 1 FAIL, 2 bad arguments / refused before start.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import uuid as _uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
SELF = Path(__file__).resolve()
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_full_chain", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


loc = _load_sibling("e2e_action_layer_local_exec")      # Api, Db, Report, server_env, serve_main, ffprobe, helpers
prod = _load_sibling("e2e_media_production_route")      # make_png, outsee paths
qc = _load_sibling("e2e_vibecode_script_frames_qc")     # SSE / JSON chat bodies, request_text
scan = _load_sibling("scan_secrets")                    # scan_tree

DEFAULT_PORT = 18831
DEFAULT_FAKE_PORT = 18832
DEFAULT_VOICE = os.environ.get("VP_STAND_VOICE_WAV", "")  # wav с русской речью (SAPI); путь задаёт вызывающий
DEFAULT_ASR = "medium"
LEVELS = ("A", "B", "C", "D", "E")
EXIT_PASS, EXIT_FAIL, EXIT_ARGS = 0, 1, 2

# fake credentials: short on purpose (scan_secrets looks for sk-/vk-/Bearer + 20 chars)
FAKE_KEYS = {"vibecode": "fake-llm-key", "outsee": "fake-outsee-key", "elevenlabs": "fake-eleven-key", "kie": "fake-kie-key"}
PROVIDERS = ("llm", "outsee_image", "outsee_video", "tts", "sfx", "kie_music")

OUTSEE_IMAGES = "/api/v1/images/generate"
OUTSEE_VIDEOS = "/api/v1/videos/generate"
OUTSEE_STATUS = "/api/v1/generations/"
OUTSEE_BALANCE = "/api/v1/balance"
KIE_BALANCE = "/api/v1/chat/credit"
KIE_CREATE = "/api/v1/generate"
KIE_POLL = "/api/v1/generate/record-info"
TTS_RE = re.compile(r"^/v1/text-to-speech/[^/?#]+$")
SFX_PATH = "/v1/sound-generation"
CHAT_PATH = "/v1/chat/completions"
# public file hosts outsee_http.ensure_public_image_url uploads a video start frame to (outsee_http.py:1002-1075)
PUBLIC_IMAGE_HOSTS = ("litterbox.catbox.moe", "catbox.moe", "uguu.se", "0x0.st", "tmpfiles.org")

# ───────────────────────────── pure helpers (unit-tested) ─────────────────────────────

# node type of the default graph -> (level, provider kind of its paid seam or None)
NODE_LEVEL: dict[str, str] = {
    "plan": "A", "script": "A", "split": "A", "excel_gpt": "A", "image_prompts": "A", "animation_prompts": "A",
    "sfx_plan": "A",
    "hero": "B", "items": "B", "images": "B",
    "videos": "C",
    "audio": "D", "music": "D", "sfx_gen": "D",
    "assemble": "E", "publish": "E",
}


def parse_until(value: str) -> str:
    v = str(value or "").strip().upper()
    if v not in LEVELS:
        raise ValueError(f"--until must be one of {', '.join(LEVELS)} (got {value!r})")
    return v


def level_index(level: str) -> int:
    return LEVELS.index(level)


# the same, per node KEY of the default graph (the two excel_gpt nodes and the text nodes sit at different levels)
NODE_KEY_LEVEL: dict[str, str] = {
    "n_plan": "A", "n_script": "A", "n_split": "A", "n_excel_gpt_1": "A",
    "n_hero": "B", "n_items": "B", "n_excel_gpt_2": "B", "n_image_prompts": "B", "n_images": "B",
    "n_animation_prompts": "C", "n_videos": "C",
    "n_audio": "D", "n_music": "D", "n_sfx_plan": "D", "n_sfx_gen": "D",
    "n_assemble": "E", "n_publish": "E",
}


def node_in_scope(node_type: str, until: str, node_key: str | None = None) -> bool:
    lvl = NODE_KEY_LEVEL.get(node_key) if node_key else None
    lvl = lvl or NODE_LEVEL.get(node_type)
    return lvl is not None and level_index(lvl) <= level_index(until)


def has_russian(text: Any) -> bool:
    return bool(re.search("[А-Яа-яЁё]", str(text or "")))


def english_only_titles(items: list[dict]) -> list[str]:
    out = []
    for i in items:
        title = i.get("title_ru") if isinstance(i, dict) else None
        if not has_russian(title):
            out.append(str(title)[:120])
    return out


def count_by(rows: list[dict], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        k = str(r.get(key))
        out[k] = out.get(k, 0) + 1
    return dict(sorted(out.items()))


def ledger_by_node(rows: list[dict]) -> dict[str, dict]:
    """spend_ledger rows grouped by the node that spent (``node`` column or the note prefix when absent)."""
    out: dict[str, dict] = {}
    for r in rows:
        if r.get("kind") == "event":
            continue
        node = str(r.get("node") or r.get("step") or r.get("provider") or "?")
        g = out.setdefault(node, {"rows": 0, "usd": 0.0, "credits": 0.0, "missing_project": 0, "missing_run": 0})
        g["rows"] += 1
        g["usd"] = round(g["usd"] + float(r.get("usd") or 0), 6)
        g["credits"] = round(g["credits"] + float(r.get("credits") or 0), 6)
        if r.get("project_id") in (None, ""):
            g["missing_project"] += 1
        if r.get("run_id") in (None, ""):
            g["missing_run"] += 1
    return out


def duplicate_keys(rows: list[dict], key: str) -> list[str]:
    seen: dict[str, int] = {}
    for r in rows:
        k = r.get(key)
        if k:
            seen[str(k)] = seen.get(str(k), 0) + 1
    return sorted(k for k, n in seen.items() if n > 1)


def expected_paid_counts(frames: int, *, hero: int = 0, items: int = 0, sfx_events: int = 0, music: int = 1,
                         tts_per_frame: int = 1) -> dict[str, int]:
    """Provider requests a CLEAN run sends: one image per frame (+ heroes + items), one video per frame, one TTS per
    frame, one music generation, one SFX per planned event. Used both as the dry expectation and as the live ceiling."""
    return {"outsee_image": frames + hero + items, "outsee_video": frames, "tts": frames * tts_per_frame,
            "kie_music": music, "sfx": sfx_events}


def verdict_of(failed: list[str], known: dict, level_reached: str | None, until: str) -> str:
    if failed:
        return "FAIL"
    if level_reached != until:
        return "FAIL"
    return "PASS" + (f" (with {len(known)} KNOWN finding(s))" if known else "")


class Report(loc.Report):
    """loc.Report plus WARN advisories (never fail)."""

    def __init__(self, strict: bool = False) -> None:
        super().__init__(strict=strict)
        self.advisories: list[str] = []

    def advise(self, text: str) -> None:
        self.advisories.append(text)
        print("ADVISE " + text[:300], flush=True)


# ───────────────────────────── fake providers (one loopback server) ─────────────────────────────

def _ff() -> str:
    return loc.ffmpeg_exe()


def make_mp4(seconds: float = 4.0, size: str = "270x480", audio: bool = False) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "clip.mp4"
        cmd = [_ff(), "-y", "-v", "error", "-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration={seconds}"]
        if audio:
            cmd += ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}"]
        cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast"]
        if audio:
            cmd += ["-c:a", "aac", "-shortest"]
        cmd.append(str(out))
        subprocess.run(cmd, check=True, timeout=120, capture_output=True)
        return out.read_bytes()


def make_mp3(seconds: float = 12.0) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "a.mp3"
        subprocess.run([_ff(), "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=220:duration={seconds}",
                        "-c:a", "libmp3lame", str(out)], check=True, timeout=60, capture_output=True)
        return out.read_bytes()


def wav_to_mp3_bytes(wav: bytes) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "a.wav", Path(tmp) / "a.mp3"
        src.write_bytes(wav)
        subprocess.run([_ff(), "-y", "-v", "error", "-i", str(src), "-c:a", "libmp3lame", str(out)],
                       check=True, timeout=60, capture_output=True)
        return out.read_bytes()


class FakeProviders:
    """Every provider the chain talks to, on ONE loopback port. Counts what it receives per provider."""

    def __init__(self, port: int, work: Path, oracle: "Callable[[dict, 'FakeProviders'], str]",
                 voice_wav: Path | None = None) -> None:
        self.port, self.work, self.oracle = port, Path(work), oracle
        self.counts: dict[str, int] = {p: 0 for p in PROVIDERS}
        self.extra: dict[str, int] = {"outsee_poll": 0, "outsee_download": 0, "kie_poll": 0, "kie_download": 0,
                                      "auth_failures": 0, "not_found": 0, "balance": 0}
        self.bodies: dict[str, list[dict]] = {p: [] for p in PROVIDERS}
        self.llm_log: list[dict] = []
        self.voice_wav = Path(voice_wav) if voice_wav else None
        self._png: bytes | None = None
        self._mp4: bytes | None = None
        self._mp3: bytes | None = None
        self._sfx_cache: dict[float, bytes] = {}
        self._lock = threading.Lock()
        self._kie_polls: dict[str, int] = {}
        self._tts_cache: dict[str, bytes] = {}
        outer = self
        (self.work / "llm_requests").mkdir(parents=True, exist_ok=True)

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a, **k):
                pass

            def _send(self, code: int, body: bytes, ctype: str = "application/json", extra: dict | None = None):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                for k, v in (extra or {}).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _json(self, code: int, payload: Any):
                self._send(code, json.dumps(payload, ensure_ascii=False).encode("utf-8"))

            def _auth(self, kind: str) -> bool:
                key = FAKE_KEYS[kind]
                got = self.headers.get("Authorization") or self.headers.get("xi-api-key") or ""
                if got in (f"Bearer {key}", key):
                    return True
                with outer._lock:
                    outer.extra["auth_failures"] += 1
                self._json(401, {"error": "bad key", "detail": {"status": "invalid_api_key"}})
                return False

            def _body(self) -> bytes:
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_POST(self):  # noqa: N802
                raw = self._body()
                path = urlsplit(self.path).path
                try:
                    body = json.loads(raw.decode("utf-8") or "{}")
                except ValueError:
                    body = {}
                if path == CHAT_PATH:
                    return outer._chat(self, body)
                if path in (OUTSEE_IMAGES, OUTSEE_VIDEOS):
                    if not self._auth("outsee"):
                        return
                    video = path == OUTSEE_VIDEOS
                    with outer._lock:
                        kind = "outsee_video" if video else "outsee_image"
                        outer.counts[kind] += 1
                        n = outer.counts[kind]
                        outer.bodies[kind].append({k: body.get(k) for k in ("model", "aspect_ratio", "resolution", "duration_sec")}
                                                  | {"prompt_chars": len(str(body.get("prompt") or "")),
                                                     "has_image": bool(body.get("image_url") or body.get("image_urls"))})
                    return self._json(200, {"id": f"{'fakevid' if video else 'fakeimg'}-{n:04d}", "status": "queued"})
                if path.startswith("/hostfake/"):
                    with outer._lock:
                        outer.extra["upload"] = outer.extra.get("upload", 0) + 1
                        n = outer.extra["upload"]
                        if outer._png is None:
                            outer._png = prod.make_png()
                    url = f"http://127.0.0.1:{outer.port}/files/hosted_frame_{n}.png"
                    if path.endswith("uguu.se"):
                        return self._json(200, {"success": True, "files": [{"url": url}]})
                    if path.endswith("tmpfiles.org"):
                        return self._json(200, {"data": {"url": f"http://127.0.0.1:{outer.port}/files/hosted_frame_{n}.png"}})
                    return self._send(200, url.encode("utf-8"), "text/plain")
                if TTS_RE.match(path):
                    if not self._auth("elevenlabs"):
                        return
                    return outer._tts(self, body, path)
                if path == SFX_PATH:
                    if not self._auth("elevenlabs"):
                        return
                    with outer._lock:
                        outer.counts["sfx"] += 1
                        outer.bodies["sfx"].append({"duration": body.get("duration_seconds"),
                                                    "prompt_chars": len(str(body.get("text") or ""))})
                        try:
                            dur = float(body.get("duration_seconds") or 1.0)
                        except (TypeError, ValueError):
                            dur = 1.0
                        key = round(dur, 1)
                        if key not in outer._sfx_cache:
                            outer._sfx_cache[key] = make_mp3(dur)      # the clip is as long as the request asked for
                        data = outer._sfx_cache[key]
                    return self._send(200, data, "audio/mpeg")
                if path == KIE_CREATE:
                    if not self._auth("kie"):
                        return
                    with outer._lock:
                        outer.counts["kie_music"] += 1
                        outer.bodies["kie_music"].append({k: body.get(k) for k in ("model", "instrumental", "customMode")})
                    return self._json(200, {"code": 200, "msg": "success", "data": {"taskId": "fake-suno-task-0001"}})
                with outer._lock:
                    outer.extra["not_found"] += 1
                return self._json(404, {"error": "not found", "path": path})

            def do_GET(self):  # noqa: N802
                parts = urlsplit(self.path)
                path = parts.path
                if path.startswith(OUTSEE_STATUS):
                    if not self._auth("outsee"):
                        return
                    gid = path[len(OUTSEE_STATUS):]
                    with outer._lock:
                        outer.extra["outsee_poll"] += 1
                    ext = "mp4" if gid.startswith("fakevid") else "png"
                    return self._json(200, {"id": gid, "status": "completed",
                                            "result_urls": [f"http://127.0.0.1:{outer.port}/files/{gid}.{ext}"]})
                if path == OUTSEE_BALANCE:
                    if not self._auth("outsee"):
                        return
                    with outer._lock:
                        outer.extra["balance"] += 1
                    return self._json(200, {"balance": 100.0})
                if path == KIE_BALANCE:
                    if not self._auth("kie"):
                        return
                    with outer._lock:
                        outer.extra["balance"] += 1
                    return self._json(200, {"code": 200, "msg": "success", "data": 100000.0})
                if path == KIE_POLL:
                    if not self._auth("kie"):
                        return
                    task = (parse_qs(parts.query).get("taskId") or [""])[0]
                    with outer._lock:
                        outer.extra["kie_poll"] += 1
                        n = outer._kie_polls[task] = outer._kie_polls.get(task, 0) + 1
                    if n == 1:
                        data = {"taskId": task, "status": "PENDING", "response": None}
                    else:
                        data = {"taskId": task, "status": "SUCCESS", "response": {"sunoData": [{
                            "id": "fake-clip-1", "audioUrl": f"http://127.0.0.1:{outer.port}/files/music.mp3",
                            "duration": 12.0, "title": "fake"}]}}
                    return self._json(200, {"code": 200, "msg": "success", "data": data})
                if path.startswith("/files/"):
                    with outer._lock:
                        if path.endswith(".mp4"):
                            outer.extra["outsee_download"] += 1
                            if outer._mp4 is None:
                                outer._mp4 = make_mp4(4.0)
                            return self._send(200, outer._mp4, "video/mp4")
                        if path.endswith(".mp3"):
                            outer.extra["kie_download"] += 1
                            if outer._mp3 is None:
                                outer._mp3 = make_mp3(12.0)
                            return self._send(200, outer._mp3, "audio/mpeg")
                        outer.extra["outsee_download"] += 1
                        if outer._png is None:
                            outer._png = prod.make_png()
                        return self._send(200, outer._png, "image/png")
                with outer._lock:
                    outer.extra["not_found"] += 1
                return self._json(404, {"error": "not found", "path": path})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True, name="fake-providers")

    # --- text LLM ---
    def _chat(self, h: BaseHTTPRequestHandler, body: dict) -> None:
        if not h._auth("vibecode"):
            return
        text = qc.request_text(body)
        with self._lock:
            self.counts["llm"] += 1
            n = self.counts["llm"]
        try:
            answer = self.oracle({"text": text, "body": body, "n": n}, self)
        except Exception as exc:  # noqa: BLE001 - an oracle bug must be visible, not silent
            answer = f"ORACLE ERROR: {type(exc).__name__}: {exc}"
        rec = {"n": n, "chars": len(text), "answer_chars": len(answer), "stream": bool(body.get("stream")),
               "model": body.get("model")}
        self.llm_log.append(rec)
        (self.work / "llm_requests" / f"{n:04d}.json").write_text(
            json.dumps({"request_text": text, "answer": answer}, ensure_ascii=False, indent=1), encoding="utf-8")
        rid = f"chatcmpl-{_uuid.uuid4().hex[:12]}"
        if body.get("stream"):
            h._send(200, qc.sse_chat_body(answer, rid), "text/event-stream")
        else:
            h._send(200, qc.chat_json_body(answer, rid))

    # --- TTS: real speech for the real text (Windows SAPI) when available, else the supplied wav ---
    def _tts(self, h: BaseHTTPRequestHandler, body: dict, path: str) -> None:
        text = str(body.get("text") or "")
        with self._lock:
            self.counts["tts"] += 1
            self.bodies["tts"].append({"chars": len(text), "model": body.get("model_id")})
        data = self.speech_for(text)
        h._send(200, data, "audio/mpeg")

    def speech_for(self, text: str) -> bytes:
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            hit = self._tts_cache.get(key)
        if hit is not None:
            return hit
        mp3 = sapi_mp3(text, self.work) if os.name == "nt" else None
        if mp3 is None and self.voice_wav is not None and self.voice_wav.is_file():
            mp3 = wav_to_mp3_bytes(self.voice_wav.read_bytes())
        if mp3 is None:
            mp3 = make_mp3(3.0)
        with self._lock:
            self._tts_cache[key] = mp3
        return mp3

    def start(self) -> "FakeProviders":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def snapshot(self) -> dict:
        return {"counts": dict(self.counts), "extra": dict(self.extra),
                "bodies": {k: v[:50] for k, v in self.bodies.items()}}


def sapi_mp3(text: str, work: Path) -> bytes | None:
    """The Russian text spoken by Windows SAPI (Irina) as mp3; None when SAPI / the voice is not there."""
    ps = shutil.which("powershell")
    if not ps or not text.strip():
        return None
    tmp = Path(work) / "_sapi"
    tmp.mkdir(parents=True, exist_ok=True)
    wav, txt = tmp / f"{_uuid.uuid4().hex[:8]}.wav", tmp / "t.txt"
    tid = _uuid.uuid4().hex[:8]
    txt = tmp / f"{tid}.txt"
    txt.write_text(text, encoding="utf-8")
    script = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "try { $s.SelectVoice('Microsoft Irina Desktop') } catch { };"
        f"$s.SetOutputToWaveFile('{wav}');"
        f"$s.Speak([System.IO.File]::ReadAllText('{txt}', [System.Text.Encoding]::UTF8));$s.Dispose()")
    try:
        subprocess.run([ps, "-NoProfile", "-NonInteractive", "-Command", script], check=True, timeout=120,
                       capture_output=True)
        if not wav.is_file() or wav.stat().st_size < 1000:
            return None
        return wav_to_mp3_bytes(wav.read_bytes())
    except (subprocess.SubprocessError, OSError):
        return None
    finally:
        for p in (wav, txt):
            try:
                p.unlink()
            except OSError:
                pass


# ───────────────────────────── request classification (child hook and tests) ─────────────────────────────

def classify_request(method: str, host: str, path: str, fake_port: int | None = None, port: int | None = None) -> str:
    """Provider bucket of ONE outgoing request (by path: the stand points every provider at one loopback port)."""
    m, p = method.upper(), path or ""
    if p == CHAT_PATH or p.endswith("/chat/completions"):
        return "llm"
    if m == "POST" and p == OUTSEE_IMAGES:
        return "outsee_image"
    if m == "POST" and p == OUTSEE_VIDEOS:
        return "outsee_video"
    if m == "POST" and TTS_RE.match(p):
        return "tts"
    if m == "POST" and p == SFX_PATH:
        return "sfx"
    if m == "POST" and p == KIE_CREATE:
        return "kie_music"
    if p.startswith("/hostfake/"):
        return "upload"
    if p.startswith(OUTSEE_STATUS) or p == OUTSEE_BALANCE:
        return "outsee_free"
    if p in (KIE_BALANCE, KIE_POLL):
        return "kie_free"
    if p.startswith("/files/"):
        return "download"
    return "other"


# ───────────────────────────── child process: settings, send hook ─────────────────────────────

def child_main() -> int:
    """Thin launcher of ``python -m app.main``: the sibling's socket guard / secret blanking, then OUR seams."""
    import runpy
    real_run = runpy.run_module

    def run_with_seams(name, *a, **k):
        install_child_seams(json.loads(os.environ["FC_CFG"]))
        return real_run(name, *a, **k)

    runpy.run_module = run_with_seams
    return loc.serve_main()


def wire_append(path: Path, event: dict) -> None:
    event = {**event, "t": round(time.time(), 3)}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def install_child_seams(cfg: dict) -> None:
    work = Path(cfg["work"])
    wire = work / "wire.jsonl"
    fake = int(cfg["fake_port"])
    sys.path.insert(0, str(ROOT))
    from app.settings import settings

    def put(name: str, value) -> None:
        try:
            setattr(settings, name, value)
        except Exception:  # noqa: BLE001
            object.__setattr__(settings, name, value)

    base = f"http://127.0.0.1:{fake}"
    put("vibecode_api_key", FAKE_KEYS["vibecode"])
    put("vibecode_base_url", f"{base}/v1")
    put("gpt_api_key", FAKE_KEYS["vibecode"])
    put("gpt_base_url", base)
    put("outsee_api_key", FAKE_KEYS["outsee"])
    put("outsee_api_base_url", base)
    put("image_provider", "outsee")
    put("video_provider", "outsee")
    put("kie_api_key", FAKE_KEYS["kie"])
    put("kie_api_base_url", base)
    put("elevenlabs_api_key", FAKE_KEYS["elevenlabs"])
    for name in ("spend_daily_cap_usd", "spend_project_cap_usd", "spend_run_cap_usd"):
        put(name, float(cfg["app_cap_usd"]))
    from app.services import elevenlabs_api
    elevenlabs_api.ELEVENLABS_API_BASE_URL = f"{base}/v1"

    import httpx
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if isinstance(getattr(client, "_transport", None), httpx.ASGITransport):
            return await original_send(client, request, **kwargs)
        host = (request.url.host or "").lower()
        if host == "api.elevenlabs.io":        # sfx_gen.py has the URL as a literal: the stand's seam moves it to the fake
            request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=fake)
            request.headers["host"] = f"127.0.0.1:{fake}"
            host = "127.0.0.1"
        elif host in PUBLIC_IMAGE_HOSTS:       # image-to-video: the start frame is uploaded to a PUBLIC file host
            request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=fake, path=f"/hostfake/{host}")
            request.headers["host"] = f"127.0.0.1:{fake}"
            host = "127.0.0.1"
        if not loc.is_loopback_host(host):
            wire_append(wire, {"event": "refused", "host": host, "method": request.method, "path": request.url.path})
            raise RuntimeError(f"stand: non-loopback request refused ({host})")
        bucket = classify_request(request.method, host, request.url.path)
        if bucket not in ("download", "outsee_free", "kie_free"):
            wire_append(wire, {"event": "request", "bucket": bucket, "method": request.method,
                               "path": request.url.path, "port": request.url.port})
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send

    from app.bots import outsee_http

    async def no_curl_post(*_a, **_k):
        return None        # the curl submit fallback is a second transport the hook could not count

    outsee_http._post_generate_via_curl = no_curl_post


def read_wire(path: Path) -> list[dict]:
    out: list[dict] = []
    if not Path(path).is_file():
        return out
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            out.append({"event": "unreadable"})
    return out


def wire_counts(wire: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for e in wire:
        if e.get("event") == "request":
            out[e["bucket"]] = out.get(e["bucket"], 0) + 1
    return out



# ───────────────────────────── text oracle: valid, contract-shaped answers for every text node ─────────────────────────────

FRAME_TEXTS = (
    "Человек находит на старом столе пожелтевшую записку и долго читает неровные строки.",
    "Он понимает, что эти слова написаны для него много лет назад, и тихо складывает бумагу.",
    "Тогда он возвращает записку на прежнее место, чтобы её нашёл следующий.",
)
SCRIPT_TEXT = " ".join(FRAME_TEXTS)
PLAN_TEXT = ("Короткий ролик о человеке и старой записке. Вступление: комната, стол, пожелтевшая бумага. Развитие: "
             "человек читает строки и узнаёт собственный почерк из прошлого. Финал: он аккуратно кладёт записку "
             "обратно, чтобы её нашёл следующий. Тон спокойный, камера неторопливая, свет тёплый.")
IMAGE_PROMPT_TEXT = ("Кинематографичный кадр: человек в сером пальто стоит у старого деревянного стола, держит жёлтую "
                     "записку, мягкий тёплый свет из окна слева, лёгкая пыль в воздухе, средний план, глубина резкости "
                     "небольшая, спокойная палитра охры и серого, без текста и логотипов на кадре, вертикальный формат.")
HERO_CHARACTER = {"id": "c01", "имя": "Человек", "внешность": "мужчина около сорока лет, усталое лицо, короткие волосы",
                  "одежда": "серое длинное пальто", "характер": "спокойный, сосредоточенный", "правила": ""}
HERO_PROMPT_TEXT = ("Character turnaround reference sheet of a calm man in his forties with a tired face and short hair, "
                    "long grey coat, front view, side view, back view and three-quarter view on a neutral light grey "
                    "background, even studio lighting, watercolor wash on archival paper, rough ink, muted grey-blue "
                    "palette, no text, no logos, no labels.")
MUSIC_PROMPT_TEXT = "Calm ambient instrumental, soft piano and warm pads, slow tempo, no vocals"
ANIMATION_PROMPT_TEXT = ("Медленный наезд камеры на человека: он осторожно разворачивает жёлтую записку, свет мягко "
                         "колеблется, пылинки плывут в воздухе, голоса нет. NO VOICE.")


class TextOracle:
    """Classifies the LLM request by its text and answers with the shape that node's parser expects."""

    def __init__(self, with_hero: bool = False) -> None:
        self.kinds: list[str] = []
        self.unknown: list[str] = []
        self.sfx_events = 0
        self.sfx_requests = 0
        self.with_hero = with_hero
        self.frames: list[dict] = []         # filled from the project's own frames when a later node asks about them

    MARKERS = (
        ("# ЗАПИСЬ СЦЕНАРИЯ", "plan"),
        ("# ЗАПИСЬ ЗАКАДРА", "script"),
        ("# РАЗБИВКА", "split"),
        ("# ПРОМТЫ КАРТИНОК", "image_prompts"),
    )

    def __call__(self, ctx: dict, fake: "FakeProviders") -> str:
        text = ctx["text"]
        kind = self.classify(text)
        self.kinds.append(kind)
        fn = getattr(self, "answer_" + kind, None)
        if fn is None:
            self.unknown.append(f"{ctx['n']}:{kind}")
            return "ORACLE: нет ответа для запроса вида " + kind
        return fn(text, ctx)

    @classmethod
    def classify(cls, text: str) -> str:
        for marker, kind in cls.MARKERS:
            if marker in text:
                return kind
        if '"decision": "approved"' in text and '"criteria"' in text:
            return "verdict"
        if text.startswith("# Доп. работа с Excel"):
            return "excel_gpt"
        if text.startswith("# Агент: промты анимации"):
            return "animation_prompts"
        if "Сделай промт для генерации персонажа, который описан ниже" in text:
            return "hero_prompt"
        if "Ты — звукорежиссёр короткого вертикального ролика" in text:
            return "sfx_plan"
        if "составь один промт для генерации фоновой инструментальной музыки в Suno" in text:
            return "music_prompt"
        return "unknown"

    @staticmethod
    def frame_uuids(text: str) -> list[str]:
        """uuids of the frames of THIS batch, from the attached db_frames json (the oracle answers only those).
        Only the attachments part is read: the instructions above it quote example uuids."""
        tail = text.rsplit("## Приложенные файлы", 1)[-1]
        return list(dict.fromkeys(re.findall(r'"uuid":\s*"([0-9a-fA-F]{8,})"', tail)))

    def answer_image_prompts(self, text: str, ctx: dict) -> str:
        ops = [{"frame_uuid": u, "fields": {"промт_картинки": IMAGE_PROMPT_TEXT}} for u in self.frame_uuids(text)]
        return json.dumps({"ops": ops}, ensure_ascii=False)

    @staticmethod
    def answer_hero_prompt(text: str, ctx: dict) -> str:
        return HERO_PROMPT_TEXT

    @staticmethod
    def answer_music_prompt(text: str, ctx: dict) -> str:
        return MUSIC_PROMPT_TEXT

    def answer_sfx_plan(self, text: str, ctx: dict) -> str:
        """One short event per frame, placed 0.5 s after the frame start (the prompt's own timeline JSON is read)."""
        m = re.search(r"# ТАЙМЛАЙН КАДРОВ \(JSON\)\s*(\[.*?\])\s*\n\s*\n# СЦЕНЫ", text, re.S)
        timeline = json.loads(m.group(1)) if m else []
        kinds_m = re.search(r"kind — СТРОГО из:\s*([^.]*?)\.", text)
        kinds = [k.strip() for k in (kinds_m.group(1).split(",") if kinds_m else []) if k.strip()] or ["whoosh", "hit"]
        events = []
        for i, f in enumerate(timeline):
            events.append({"frame_number": int(f["frame_number"]), "t_start": round(float(f["start"]) + 0.5, 2),
                           "duration": 1.0, "kind": kinds[i % len(kinds)],
                           "prompt": "soft paper whoosh" if i % 2 == 0 else "light desk hit", "gain": 0.5, "duck": False})
        # the application sends one paid request per DISTINCT (prompt, duration): a repeated event is a free copy
        self.sfx_events = len(events)
        self.sfx_requests = len({(e["prompt"], e["duration"]) for e in events})
        return json.dumps({"events": events}, ensure_ascii=False)

    def answer_animation_prompts(self, text: str, ctx: dict) -> str:
        """Phase 1 carries the master prompt only (the node waits for a 'ready'); the batches carry frame_uuid lines."""
        uuids = list(dict.fromkeys(re.findall(r"frame_uuid:\s*([0-9a-fA-F]{8,})", text)))
        if not uuids:
            return "Ready to receive the original frame batch."
        return json.dumps({"ops": [{"frame_uuid": u, "fields": {"промт_видео": ANIMATION_PROMPT_TEXT}} for u in uuids]},
                          ensure_ascii=False)

    def answer_excel_gpt(self, text: str, ctx: dict) -> str:
        fields = {"смысл": "Человек читает записку и понимает, что она адресована ему."}
        if self.with_hero:
            fields["персонажи"] = "c01"
        ops = [{"frame_uuid": u, "fields": dict(fields)} for u in self.frame_uuids(text)]
        out: dict = {"ops": ops}
        if self.with_hero:
            out["characters"] = [HERO_CHARACTER]
        return json.dumps(out, ensure_ascii=False)

    @staticmethod
    def answer_verdict(text: str, ctx: dict) -> str:
        """A well-formed approving verdict; every criterion quotes the checked text literally (the prompt demands it)."""
        names = re.findall(r'"(\w+)":\s*\{\s*"verdict"', text) or ["overall"]
        pool = [PLAN_TEXT, SCRIPT_TEXT, IMAGE_PROMPT_TEXT, ANIMATION_PROMPT_TEXT, *FRAME_TEXTS]
        quote = ""
        for cand in pool:
            if cand[:60] in text:
                quote = " ".join(cand.split()[:9])
                break
        crit = {n: {"verdict": "pass", "score": 5, "evidence_quote": quote} for n in names}
        return json.dumps({"decision": "approved", "confidence": 0.95, "criteria": crit, "issues": [], "fix_hints": [],
                           "red_flags": []}, ensure_ascii=False)

    @staticmethod
    def answer_plan(text: str, ctx: dict) -> str:
        return json.dumps({"ops": [{"target": "project", "fields": {"общий_план": PLAN_TEXT}}]}, ensure_ascii=False)

    @staticmethod
    def answer_script(text: str, ctx: dict) -> str:
        return json.dumps({"ops": [{"target": "project", "fields": {"закадровый_текст": SCRIPT_TEXT}}]}, ensure_ascii=False)

    @staticmethod
    def answer_split(text: str, ctx: dict) -> str:
        return json.dumps({"ops": [{"target": "replace_frames",
                                    "frames": [{"закадр": t, "длительность": 6} for t in FRAME_TEXTS]}]}, ensure_ascii=False)


# ───────────────────────────── driver (parent process) ─────────────────────────────

LAST_RUNNING = {"A": "enriching_1", "B": "generating_images", "C": "generating_videos", "D": "generating_sfx"}
STATUS_END = {"A": "enrich_1_ready", "B": "images_ready", "C": "videos_ready", "D": "sfx_ready", "E": "assembled"}


def status_order_table() -> list[str]:
    """ProjectStatus values in the linear order of the default graph (only those the chain passes)."""
    return ["new", "planning", "plan_ready", "scripting", "script_ready", "splitting", "frames_ready",
            "enriching_1", "enrich_1_ready", "generating_hero", "hero_ready", "generating_items", "items_ready",
            "enriching_2", "enrich_2_ready", "generating_image_prompts", "image_prompts_ready", "generating_images",
            "images_ready", "generating_animation_prompts", "animation_prompts_ready", "generating_videos",
            "videos_ready", "generating_audio", "audio_ready", "generating_music", "music_ready", "sfx_planning",
            "sfx_plan_ready", "generating_sfx", "sfx_ready", "assembling", "assembled", "publishing", "published"]


def status_rank(status: str) -> int:
    table = status_order_table()
    return table.index(status) if status in table else -1


def level_reached(statuses_seen: list[str]) -> str | None:
    best = None
    seen = {s for s in statuses_seen}
    top = max((status_rank(s) for s in seen), default=-1)
    for lvl in LEVELS:
        if top >= status_rank(STATUS_END[lvl]):
            best = lvl
    return best


class Chain:
    def __init__(self, args, work: Path, api, db, report: Report, fake: FakeProviders) -> None:
        self.args, self.work, self.api, self.db, self.R, self.fake = args, work, api, db, report, fake
        self.pid = 0
        self.slug = ""
        self.timeline_seen: list[tuple[float, str]] = []
        self.status_log: list[dict] = []
        self.t0 = time.time()
        self.stop_reason = ""
        self.operator_starts: set[tuple[str, str]] = set()
        self.sfx_events = len(FRAME_TEXTS)
        self.human_final = False
        self.human_final_detail: Any = "не выполнялось"
        self.auto_off = False
        self.auto_final: dict | None = None

    def project(self) -> dict:
        """Full project through the API (used before/after the run: GET /api/projects/{id} also heals and commits)."""
        code, body = self.api.get(f"/api/projects/{self.pid}")
        return body if code == 200 and isinstance(body, dict) else {}

    def peek(self) -> dict:
        """Status, auto flag and meta straight from the project's own project.db, read-only: the stand is a quiet
        observer during the run (a polling GET could itself roll a finishing step back, finding F10-D8)."""
        try:
            pdb = loc.Db(Path(self.work) / "data" / "videos" / self.slug / "project.db")
            rows = pdb.q("SELECT status, auto_mode, meta FROM projects WHERE id = ?", (self.pid,))
        except sqlite3.Error:
            return {}
        if not rows:
            return {}
        try:
            meta = json.loads(rows[0]["meta"] or "{}")
        except ValueError:
            meta = {}
        return {"status": rows[0]["status"], "auto_mode": bool(rows[0]["auto_mode"]), "meta": meta}

    def create(self) -> bool:
        R = self.R
        payload = {"title": "F10 tiny chain", "topic": self.args.topic, "auto_mode": True, "hero_mode": "auto",
                   "aspect_ratio": "9_16"}
        payload.update(getattr(self, "create_extra", None) or {})        # the live part sets hero_mode / generators
        code, body = self.api.post("/api/projects", payload)
        if not R.check("проект создан через настоящий API (201)", code == 201 and isinstance(body, dict) and body.get("id"),
                       (code, str(body)[:300])):
            return False
        self.pid, self.slug = int(body["id"]), str(body.get("slug") or "")
        R.check("проект создан в авто-режиме", bool(body.get("auto_mode")), body.get("auto_mode"))
        return True

    def start(self) -> bool:
        code, body = self.api.post(f"/api/projects/{self.pid}/steps/plan/run")
        return self.R.check("запуск конвейера: ▶ шага plan через настоящую ручку (200)", code == 200, (code, str(body)[:300]))

    # the pipeline holds after an excel_gpt node by design (enrich_xlsx._hold_pipeline_after_excel_gpt): the OPERATOR
    # presses the next node's start. The stand does it through the same production route the UI button uses.
    MANUAL_START_AFTER = {"enrich_1_ready": "hero", "enrich_2_ready": "img_pr"}

    def operator_start_if_held(self, project: dict, status: str) -> None:
        meta = project.get("meta") or {}
        step = self.MANUAL_START_AFTER.get(status)
        if not step or not meta.get("auto_await_manual_start"):
            return
        key = (status, step)
        if key in self.operator_starts:
            return
        self.operator_starts.add(key)
        self.wait_node_done({"enrich_1_ready": "n_excel_gpt_1", "enrich_2_ready": "n_excel_gpt_2"}[status])
        code, body = self.api.post(f"/api/projects/{self.pid}/steps/{step}/run")
        self.R.check(f"оператор: ▶ шага {step} после {status} через ручку UI (200)", code == 200, (code, str(body)[:300]))
        print(f"  оператор нажал ▶ {step} (после {status})", flush=True)

    def hand_over_to_human(self, st: str) -> None:
        """Level E: the final approval is the HUMAN's. Auto mode would approve it itself (see finding F10-D6), so the
        operator (the stand) switches auto mode off while the last paid node runs and presses the assemble start."""
        if not self.auto_off and status_rank(st) >= status_rank("generating_sfx"):
            code, body = self.api.req("PATCH", f"/api/projects/{self.pid}", json_body={"auto_mode": False})
            self.R.check("оператор: автопродвижение выключено перед сборкой, финал остаётся за человеком (200)",
                         code == 200, (code, str(body)[:200]))
            self.auto_off = True
        if self.auto_off and st == "sfx_ready" and ("sfx_ready", "assemble") not in self.operator_starts:
            self.operator_starts.add(("sfx_ready", "assemble"))
            self.wait_node_done("n_sfx_gen")
            code, body = self.api.post(f"/api/projects/{self.pid}/steps/assemble/run")
            self.R.check("оператор: ▶ шага assemble после sfx_ready через ручку UI (200)", code == 200, (code, str(body)[:300]))
            print("  оператор нажал ▶ assemble (после sfx_ready)", flush=True)

    def approve_final_as_human(self) -> bool:
        """POST /api/hitl/{id}/decision - the route the UI button uses. Publication is NOT approved here."""
        code, rows = self.api.get(f"/api/hitl/project/{self.pid}")
        pending = [h for h in (rows if isinstance(rows, list) else []) if h.get("kind") == "approve_final"
                   and h.get("decision") == "pending"]
        if not self.R.check("в проекте есть запрос approve_final, ожидающий человека", bool(pending),
                            [(h.get("kind"), h.get("decision")) for h in (rows if isinstance(rows, list) else [])]):
            self.human_final_detail = "запрос approve_final не найден в ожидании (автоматика могла решить сама)"
            return False
        hid = pending[-1]["id"]
        code, body = self.api.post(f"/api/hitl/{hid}/decision", {"decision": "approve"})
        ok = self.R.check("человек одобрил финал: POST /api/hitl/{id}/decision (200)", code == 200,
                          (code, str(body)[:300]), known="F10-D5")
        self.human_final = bool(ok and code == 200)
        self.human_final_detail = {"hitl_id": hid, "code": code}
        return self.human_final

    def stop_auto_at_last_node(self, until: str, st: str) -> None:
        """Levels A-D: the operator switches auto mode off while the LAST node of the level runs, so the worker does
        not start the next node (the report then describes exactly the nodes of the level, no half-run overrun)."""
        if self.auto_off or status_rank(st) < status_rank(LAST_RUNNING[until]):
            return
        code, body = self.api.req("PATCH", f"/api/projects/{self.pid}", json_body={"auto_mode": False})
        self.R.check(f"оператор: автопродвижение выключено на последней ноде уровня {until} (200)", code == 200,
                     (code, str(body)[:200]))
        self.auto_off = True

    def wait_node_done(self, node_key: str, seconds: float = 20.0) -> bool:
        """The operator presses the next start only after the previous node is closed in the runtime: an early start
        cancels the still-finishing advance task (step_cancel.kill_active_generation reason=explicit_ui_start)."""
        end = time.time() + seconds
        while time.time() < end:
            rows = self.db.q("SELECT n.status FROM node_runs n JOIN workflow_runs w ON w.id = n.workflow_run_id "
                             "WHERE w.project_id = ? AND n.node_key = ?", (self.pid, node_key))
            if rows and rows[0]["status"] == "done":
                time.sleep(0.8)
                return True
            time.sleep(0.4)
        return False

    def observe_auto_final(self, seconds: float = 40.0) -> str:
        """--keep-auto: the stand does NOT touch the final request; it only watches whether the automation closes it
        (finding F10-D6). Nothing here approves anything."""
        end = time.time() + seconds
        rows: list = []
        while time.time() < end:
            code, got = self.api.get(f"/api/hitl/project/{self.pid}")
            rows = [h for h in (got if isinstance(got, list) else []) if h.get("kind") == "approve_final"]
            if rows and all(h.get("decision") != "pending" for h in rows):
                break
            time.sleep(2.0)
        self.auto_final = {"requests": [{k: h.get(k) for k in ("id", "kind", "decision", "decided_at")} for h in rows],
                           "status_after": self.peek().get("status"), "waited_s": round(seconds - max(0.0, end - time.time()), 1)}
        return ""

    def poll_until(self, until: str, timeout_s: float) -> str:
        """Poll the project status; returns the stop reason ('' = level reached)."""
        end = time.time() + timeout_s
        last = None
        last_change = time.time()
        stall_s = float(self.args.stall_s)
        final_tried = False
        while time.time() < end:
            p = self.peek()
            st = str(p.get("status") or "")
            if st != last:
                self.status_log.append({"t": round(time.time() - self.t0, 2), "status": st})
                print(f"  [{time.time() - self.t0:6.1f}s] status={st}", flush=True)
                last, last_change = st, time.time()
            hook = getattr(self, "tick_hook", None)          # the live part: spend ceilings / frame gate
            if hook is not None:
                why = hook(st)
                if why:
                    return why
            if until == "E":
                if not self.args.keep_auto:
                    self.hand_over_to_human(st)
                if st == "assembled" and not final_tried:
                    final_tried = True
                    if self.args.keep_auto:
                        return self.observe_auto_final()
                    time.sleep(1.0)
                    self.approve_final_as_human()
                    return "" if self.human_final else "approve_final не принят человеком через ручку"
            else:
                self.stop_auto_at_last_node(until, st)
                if status_rank(st) >= status_rank(STATUS_END[until]):
                    return ""
            self.operator_start_if_held(p, st)
            if st in ("paused", "failed"):
                return f"проект остановился в статусе {st}"
            if time.time() - last_change > stall_s:
                return f"статус {st} не менялся {int(stall_s)} с"
            time.sleep(0.5)
        return f"таймаут {int(timeout_s)} с, последний статус {last}"


# ───────────────────────────── capture and checks (after the run) ─────────────────────────────

RUNNING_STATUSES = {"planning", "scripting", "splitting", "enriching_1", "enriching_2", "enriching_3", "enriching_4",
                    "enriching_5", "generating_hero", "generating_items", "generating_image_prompts", "generating_images",
                    "generating_animation_prompts", "generating_videos", "generating_audio", "generating_music",
                    "sfx_planning", "generating_sfx", "assembling", "publishing"}


def settle(chain: "Chain", seconds: float = 90.0) -> str:
    """After the level is reached: switch auto mode off through the production route and wait until no step runs, so
    the counters below describe a quiet system (the worker may have started the next node already)."""
    code, _ = chain.api.req("PATCH", f"/api/projects/{chain.pid}", json_body={"auto_mode": False})
    end = time.time() + seconds
    st = ""
    while time.time() < end:
        st = str(chain.project().get("status") or "")
        if st not in RUNNING_STATUSES:
            return st
        time.sleep(1.0)
    return st


def node_table(db: "loc.Db", pid: int) -> list[dict]:
    rows = db.q("SELECT n.node_key, n.node_type, n.status, n.attempts, n.started_at, n.finished_at, n.error "
                "FROM node_runs n JOIN workflow_runs w ON w.id = n.workflow_run_id WHERE w.project_id = ? ORDER BY n.id",
                (pid,))
    out = []
    for r in rows:
        dur = None
        try:
            from datetime import datetime
            if r["started_at"] and r["finished_at"]:
                a = datetime.fromisoformat(str(r["started_at"]))
                b = datetime.fromisoformat(str(r["finished_at"]))
                dur = round((b - a).total_seconds(), 1)
        except ValueError:
            pass
        out.append({**r, "seconds": dur})
    return out


def ledger_rows(db: "loc.Db") -> list[dict]:
    return db.q("SELECT id, kind, provider, project_id, run_id, source, usd, credits, note, ref, request_id "
                "FROM spend_ledger ORDER BY id")


def note_family(note: str) -> str:
    """Which paid seam a spend_ledger row belongs to (the table has no node column): by the note's first words."""
    n = (note or "").lower()
    for key, fam in (("tts", "audio"), ("sfx", "sfx_gen"), ("suno", "music"), ("create suno", "music"),
                     ("video", "videos"), ("veo", "videos"), ("image", "images"), ("gpt", "text"), ("chat", "text")):
        if key in n:
            return fam
    return "other"


def summarize_ledger(rows: list[dict], pid: int) -> dict:
    paid = [r for r in rows if r.get("kind") != "event"]
    by_provider = count_by([r for r in paid if r.get("kind") == "estimated"], "provider")
    by_family = count_by([{"fam": note_family(r.get("note"))} for r in paid], "fam")
    return {"rows": len(paid), "by_provider": by_provider, "by_family": by_family,
            "usd": round(sum(float(r.get("usd") or 0) for r in paid), 6),
            "credits": round(sum(float(r.get("credits") or 0) for r in paid), 6),
            "without_project": sum(1 for r in paid if r.get("project_id") in (None, "")),
            "other_project": sum(1 for r in paid if r.get("project_id") not in (None, "", pid)),
            "without_run": sum(1 for r in paid if not r.get("run_id")),
            "duplicate_refs": duplicate_keys([r for r in paid if r.get("kind") == "settle"], "ref"),
            "run_ids": sorted({str(r.get("run_id")) for r in paid})[:10]}


def fetch_timeline(api, pid: int, pages: int = 40) -> list[dict]:
    items: list[dict] = []
    cursor = None
    for _ in range(pages):
        params = {"limit": 500}
        if cursor:
            params["cursor"] = cursor
        code, body = api.get(f"/api/projects/{pid}/timeline", params=params)
        if code != 200 or not isinstance(body, dict):
            break
        items.extend(body.get("items") or [])
        cursor = body.get("next_cursor")
        if not cursor:
            break
    return items


# ───────────────────────────── recording API and evaluation ─────────────────────────────

class RecordingApi(loc.Api):
    """loc.Api that remembers every state-changing call of the stand (each must have an operator_audit row)."""

    def __init__(self, base: str) -> None:
        super().__init__(base)
        self.mutations: list[dict] = []

    def req(self, method: str, path: str, **kw):
        code, body = super().req(method, path, **kw)
        if method.upper() != "GET":
            self.mutations.append({"method": method.upper(), "path": path.split("?")[0], "status": code})
        return code, body


def audit_gaps(mutations: list[dict], audit_rows: list[dict]) -> list[dict]:
    """Stand mutations that have NO operator_audit row (same method + path + status). Pure; unit-tested."""
    pool = [(a["method"], a["path"].split("?")[0], int(a["status_code"])) for a in audit_rows]
    missing = []
    for m in mutations:
        key = (m["method"], m["path"], int(m["status"]))
        if key in pool:
            pool.remove(key)
        else:
            missing.append(m)
    return missing


def audit_unattributed(audit_rows: list[dict]) -> list[dict]:
    return [a for a in audit_rows if not str(a.get("actor") or "").strip() or not str(a.get("outcome") or "").strip()]


def compare_wire_to_fake(wire: dict[str, int], fake: dict[str, int]) -> dict[str, tuple[int, int]]:
    """Buckets where the in-child send hook and the loopback provider disagree: bucket -> (wire, fake)."""
    out = {}
    for k in PROVIDERS:
        w, f = int(wire.get(k, 0)), int(fake.get(k, 0))
        if w != f:
            out[k] = (w, f)
    return out


def expected_counts_for(until: str, frames: int, sfx_events: int, hero: int = 0) -> dict[str, int]:
    """Exact paid requests a CLEAN run to ``until`` sends in THIS stand (hero / items are skipped unless --with-hero)."""
    lvl = level_index(until)
    exp = {"outsee_image": frames + hero if lvl >= 1 else 0, "outsee_video": frames if lvl >= 2 else 0,
           "tts": 1 if lvl >= 3 else 0, "kie_music": 1 if lvl >= 3 else 0, "sfx": sfx_events if lvl >= 3 else 0}
    return exp


LOG_EVIDENCE = {
    "F10-D1 clamp_status_to_data откатил ready в new": "clamp_status_to_data: ",
    "F10-D8 берём master (откат статуса при чтении)": "— берём master",
    "ошибки шагов (ERROR)": " | ERROR ",
    "трассировки (Traceback)": "Traceback (most recent call last)",
    "заплатка стенда сообщила о сбое": "stand seam",
}


def log_evidence(log_path: Path) -> dict[str, int]:
    """How often the server log carries the lines the findings are about (the log is the stand's evidence file)."""
    try:
        text = Path(log_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    clean = re.sub(r"\[[0-9;]*m", "", text)
    return {name: clean.count(needle) for name, needle in LOG_EVIDENCE.items()}


def data_fingerprint(root: Path) -> str | None:
    """Digest of (relative path, size, mtime) of the files under the repo data/ folder, transient sqlite/log files
    excluded: the stand never writes there, this only shows whether somebody else did during the run."""
    root = Path(root)
    if not root.exists():
        return None
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix in (".log",) or p.name.endswith(("-wal", "-shm", "-journal")):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        h.update(f"{p.relative_to(root).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode("utf-8"))
    return h.hexdigest()


def secrets_in(path: Path) -> dict:
    return scan.scan_tree([Path(path)])


class Evidence:
    """Everything the evaluation reads, gathered once (so the checks stay deterministic and testable)."""

    def __init__(self) -> None:
        self.project: dict = {}
        self.nodes: list[dict] = []
        self.ledger: list[dict] = []
        self.audit: list[dict] = []
        self.timeline: list[dict] = []
        self.hitl: list = []
        self.incidents: list[dict] = []
        self.degradations: list[dict] = []
        self.idem: list[dict] = []
        self.publications = 0
        self.frames = 0
        self.final_probe: dict = {}
        self.final_path = ""
        self.voice_seconds: float | None = None
        self.wire: list[dict] = []
        self.net_attempts: list[dict] = []


def gather(chain: "Chain", work: Path) -> Evidence:
    ev = Evidence()
    db, pid = chain.db, chain.pid
    ev.project = chain.project()
    ev.nodes = node_table(db, pid)
    ev.ledger = ledger_rows(db)
    ev.audit = db.q("SELECT method, path, status_code, actor, outcome, project_id FROM operator_audit ORDER BY id")
    ev.timeline = fetch_timeline(chain.api, pid)
    code, hitl = chain.api.get(f"/api/hitl/project/{pid}")
    ev.hitl = hitl if code == 200 and isinstance(hitl, list) else []
    ev.incidents = db.q("SELECT incident_id, node_key, type, severity, status, details FROM observation_incidents "
                        "WHERE project_id = ?", (pid,)) if db.has_table("observation_incidents") else []
    ev.degradations = db.q("SELECT * FROM observation_degradations") if db.has_table("observation_degradations") else []
    ev.idem = db.q("SELECT event, key, attempt_id FROM outsee_idempotency ORDER BY id") if db.has_table("outsee_idempotency") else []
    ev.publications = len(db.q("SELECT id FROM publications WHERE project_id = ?", (pid,))) if db.has_table("publications") else 0
    pdb = loc.Db(Path(work) / "data" / "videos" / chain.slug / "project.db")
    try:
        ev.frames = len(pdb.q("SELECT id FROM frames WHERE project_id = ?", (pid,)))
        arts = pdb.q("SELECT kind, path FROM artifacts WHERE project_id = ? ORDER BY id", (pid,))
    except sqlite3.Error:
        arts = []
    finals = [a["path"] for a in arts if str(a["kind"]).endswith("final_video")]
    ev.final_path = finals[-1] if finals else ""
    if ev.final_path and Path(ev.final_path).is_file():
        ev.final_probe = loc.ffprobe(Path(ev.final_path))
    voices = [a["path"] for a in arts if str(a["kind"]).endswith("audio")]
    if voices and Path(voices[-1]).is_file():
        ev.voice_seconds = loc.ffprobe(Path(voices[-1])).get("duration")
    ev.wire = read_wire(Path(work) / "wire.jsonl")
    ev.net_attempts = loc.read_jsonl(Path(work) / "net_attempts.jsonl")
    return ev


# ───────────────────────────── checks ─────────────────────────────

LEDGER_PROVIDER_OF = {"llm": "vibecode", "outsee_image": "outsee", "outsee_video": "outsee", "tts": "elevenlabs",
                      "sfx": "elevenlabs", "kie_music": "kie"}


def uploads_expected(frames: int, hero: int = 0) -> int:
    """Free uploads to the public file hosts: one start frame per video, and (with a hero) one reference per image."""
    return frames + (frames if hero else 0)


def expected_llm_calls(until: str, frames: int, hero: int = 0) -> dict[str, int]:
    """Text-model requests a CLEAN run to ``until`` sends: one per text node, one per frame for each excel_gpt node
    (the default graph has two), two plan/script verdicts, animation prompts = one 'ready' + one batch of up to 5 frames."""
    lvl = level_index(until)
    exp = {"plan": 1, "script": 1, "verdict": 2, "split": 1, "excel_gpt": frames}
    if lvl >= 1:
        exp["excel_gpt"] = 2 * frames
        exp["image_prompts"] = 1
        if hero:
            exp["hero_prompt"] = hero
    if lvl >= 2:
        exp["animation_prompts"] = 1 + (frames + 4) // 5
    if lvl >= 3:
        exp["music_prompt"] = 1
        exp["sfx_plan"] = 1
    return exp


def ledger_expectation(wire_counts_: dict[str, int]) -> dict[str, int]:
    """Paid wire requests grouped by the provider name the ledger uses (one ledger row per paid request)."""
    out: dict[str, int] = {}
    for bucket, n in wire_counts_.items():
        prov = LEDGER_PROVIDER_OF.get(bucket)
        if prov:
            out[prov] = out.get(prov, 0) + int(n)
    return out


def level_after(statuses: list[str], final_ok: bool, human_final: bool) -> str | None:
    reached = level_reached(statuses)
    if reached == "E" and not (final_ok and human_final):
        return "D"
    return reached


def run_checks(chain: "Chain", ev: Evidence, fake: FakeProviders, oracle: "TextOracle", until: str, work: Path,
               R: Report, human_final: bool, repo_data_before: float | None) -> dict:  # noqa: C901
    pid = chain.pid
    seen = [e["status"] for e in chain.status_log]
    final_ok = bool(ev.final_probe.get("has_video") and ev.final_probe.get("has_audio"))
    reached = level_after(seen, final_ok, human_final or chain.auto_final is not None)
    R.check(f"достигнут уровень {until} (достигнут: {reached or 'ни один'})",
            reached is not None and level_index(reached) >= level_index(until),
            chain.stop_reason or "ок")

    # --- nodes ---
    scope = [n for n in ev.nodes if node_in_scope(n["node_type"], until, n["node_key"]) and n["node_type"] != "publish"]
    for n in scope:
        R.check(f"нода {n['node_key']} ({n['node_type']}): статус done", n["status"] == "done",
                {k: n.get(k) for k in ("status", "attempts", "error")})
        if int(n.get("attempts") or 0) > 1:
            R.advise(f"нода {n['node_key']}: {n['attempts']} попыток (повтор после отказа)")

    # --- providers: independent counters agree, exact counts per seam ---
    wc = wire_counts(ev.wire)
    diff = compare_wire_to_fake(wc, fake.counts)
    R.check("счётчики провайдеров: отправлено (хук внутри сервера) = принято (loopback-провайдер)", not diff, diff or wc)
    R.check("ни одного отказа по ключу и ни одного неизвестного пути у провайдера",
            fake.extra["auth_failures"] == 0 and fake.extra["not_found"] == 0, fake.extra)
    hero_n = 1 if chain.args.with_hero else 0
    exp = expected_counts_for(until, ev.frames, oracle.sfx_requests or chain.sfx_events, hero_n)
    for bucket in ("outsee_image", "outsee_video", "tts", "kie_music", "sfx"):
        got = fake.counts[bucket]
        if exp[bucket] or until == "E":
            R.check(f"{bucket}: платных запросов ровно {exp[bucket]} (по числу кадров/единиц, без дублей)",
                    got == exp[bucket], {"получено": got, "ожидалось": exp[bucket], "кадров": ev.frames})
        elif got:
            R.advise(f"{bucket}: {got} запрос(ов) сверх уровня {until} (воркер успел начать следующий узел)")
    got_llm = count_by([{"k": k} for k in oracle.kinds], "k")
    for kind, n in expected_llm_calls(until, ev.frames, hero_n).items():
        R.check(f"текстовая модель: запросов узла «{kind}» ровно {n} (без повторного запуска шага и без отказов контракта)",
                got_llm.get(kind, 0) == n, {"получено": got_llm.get(kind, 0), "ожидалось": n})
    if level_index(until) >= level_index("C"):
        want_up = uploads_expected(ev.frames, hero_n)
        R.check("видео и картинки по референсу: одна загрузка кадра на публичный файловый хост на каждый платный запрос "
                "с картинкой на входе (без дублей)", fake.extra.get("upload", 0) == want_up,
                {"загрузок": fake.extra.get("upload", 0), "ожидалось": want_up, "кадров": ev.frames, "героев": hero_n})
        R.advise(f"{fake.extra.get('upload', 0)} картинок(и) уходят на ПУБЛИЧНЫЕ файловые хосты (litterbox/catbox/uguu/0x0), "
                 "потому что Yandex S3 не настроен: стартовые кадры видео" + (" и референсы героя для картинок" if hero_n else "")
                 + "; в стенде это loopback-подмена, в живом прогоне кадры проекта окажутся на сторонних сайтах")
    intents = [r for r in ev.idem if r["event"] == "intent"]
    want_intents = (ev.frames if level_index(until) >= 1 else 0) + (ev.frames if level_index(until) >= 2 else 0)
    R.check("Outsee: одно намерение (intent) в идемпотентности на каждую картинку и каждый клип, ключи не повторяются",
            len(intents) == len({r["key"] for r in intents}) == want_intents,
            [(r["key"]) for r in intents])
    refused = [e for e in ev.wire if e.get("event") == "refused"]
    R.check("сеть: ни одного запроса за пределы loopback (хук отправки и сторож сокетов)",
            not refused and not ev.net_attempts, {"refused": refused[:3], "net_attempts": ev.net_attempts[:3]})

    # --- spend ledger ---
    led = summarize_ledger(ev.ledger, pid)
    want = ledger_expectation(wc)
    for prov, n in want.items():
        have = led["by_provider"].get(prov, 0)
        R.check(f"spend_ledger: платных строк провайдера {prov} столько же, сколько запросов ({n})", have == n,
                {"строк": have, "запросов": n})
    paid_media = [r for r in ev.ledger if r.get("kind") != "event" and r.get("provider") in ("outsee", "kie", "elevenlabs")]
    R.check("spend_ledger: у каждой строки медиа-провайдера есть project_id этого проекта и run_id",
            all(r.get("project_id") == pid and r.get("run_id") for r in paid_media),
            [(r["provider"], r["project_id"], r["run_id"]) for r in paid_media if r.get("project_id") != pid or not r.get("run_id")][:5])
    R.check("spend_ledger: у строк текстовой модели есть project_id и run_id (платные вызовы проверок auto_advance)",
            all(r.get("project_id") == pid and r.get("run_id") for r in ev.ledger
                if r.get("kind") != "event" and r.get("provider") == "vibecode"),
            {"без проекта/запуска": led["without_project"], "без run_id": led["without_run"]}, known="F10-D4")
    R.check("spend_ledger: нет строк чужого проекта", led["other_project"] == 0, led["other_project"])

    # --- operator audit ---
    gaps = audit_gaps(chain.api.mutations, ev.audit)
    R.check(f"operator_audit: каждое из {len(chain.api.mutations)} изменяющих обращений стенда записано (метод, путь, код)",
            not gaps, gaps[:5])
    R.check("operator_audit: у каждой записи есть автор и исход", not audit_unattributed(ev.audit),
            audit_unattributed(ev.audit)[:3])

    # --- timeline ---
    items = ev.timeline
    R.check("лента проекта (GET /api/projects/{id}/timeline) не пуста", len(items) > 0, len(items))
    eng = english_only_titles(items)
    R.check("лента: у каждой строки заголовок по-русски (нет английских заголовков)", not eng, eng[:5], known="F10-D9")
    kinds = count_by(items, "kind")
    R.check("лента: есть строки запусков нод, трат и действий оператора",
            all(any(k in str(kind) for kind in kinds) for k in ("node", "spend", "operator")), kinds)

    # --- journal gaps / typed errors ---
    R.check("журнал наблюдения: ни одного неописанного пробела (observation_degradations пуста)",
            not ev.degradations, len(ev.degradations))
    blocking = []
    for inc in ev.incidents:
        try:
            d = json.loads(inc["details"]) if isinstance(inc["details"], str) else (inc["details"] or {})
        except ValueError:
            d = {}
        if d.get("blocking"):
            blocking.append((inc["node_key"], inc["type"], str(d.get("fact"))[:120]))
    typed = [i for i in ev.incidents if i.get("type")]
    R.check("инциденты: каждый записанный инцидент типизирован (type и статус)", len(typed) == len(ev.incidents),
            len(ev.incidents))
    known_nodes = {"n_hero": "F10-D3", "n_sfx_gen": "F10-D10"}
    # F10-D11: the anim_pr sidecar spawned by the images step inherits its attempt; a model call still in flight when
    # the attempt closes is recorded as an unconfirmed request of n_images (timing dependent, not on every run)
    sidecar_race = [b for b in blocking if b[0] == "n_images" and b[1] == "uncertain_request"]
    other = [b for b in blocking if b[0] not in known_nodes and b not in sidecar_race]
    R.check("у чистого прогона нет блокирующих инцидентов наблюдения (кроме описанных находок)", not other, other)
    R.check("независимая проверка images: нет неподтверждённого запроса от фонового sidecar промтов анимации",
            not sidecar_race, sidecar_race, known="F10-D11")
    R.check("независимая проверка hero знает о законно пропущенном шаге (нет блокирующего инцидента n_hero)",
            not [b for b in blocking if b[0] == "n_hero"], [b for b in blocking if b[0] == "n_hero"], known="F10-D3")
    if level_index(until) >= level_index("D"):
        R.check("независимая проверка sfx_gen принимает результат платного провайдера (нет блокирующего инцидента n_sfx_gen)",
                not [b for b in blocking if b[0] == "n_sfx_gen"], [b for b in blocking if b[0] == "n_sfx_gen"],
                known="F10-D10")
    non_blocking = [(i["node_key"], i["type"]) for i in ev.incidents if (i["node_key"], i["type"], ) not in
                    [(b[0], b[1]) for b in blocking]]
    if non_blocking:
        R.advise(f"неблокирующие инциденты наблюдения: {non_blocking}")

    # --- HITL ---
    hitl_rows = [(h.get("kind"), h.get("decision"), h.get("frame_id")) for h in ev.hitl if isinstance(h, dict)]
    pend = [h for h in hitl_rows if h[1] == "pending"]
    if pend:
        R.advise(f"после автопродвижения остались неснятые запросы подтверждения: {pend}")

    # --- final ---
    if level_index(until) >= level_index("E"):
        R.check("final.mp4 существует и в нём есть видео и звук (ffprobe)", final_ok, ev.final_probe or ev.final_path)
        if ev.voice_seconds and ev.final_probe.get("duration"):
            d = abs(float(ev.final_probe["duration"]) - float(ev.voice_seconds))
            R.check("длительность final.mp4 близка к длительности озвучки (допуск 2 с)", d <= 2.0,
                    {"final": ev.final_probe.get("duration"), "озвучка": ev.voice_seconds})
        if chain.auto_final is not None:
            decided = [r for r in chain.auto_final["requests"] if r.get("decision") not in (None, "pending")]
            R.check("approve_final не закрыт автоматикой без человека (--keep-auto наблюдает, стенд ничего не одобряет)",
                    not decided, chain.auto_final, known="F10-D6")
        else:
            R.check("approve_final принят человеком через production-ручку, а не автоматикой",
                    human_final, chain.human_final_detail)
        if chain.auto_final is None:
            fin = [h for h in ev.hitl if isinstance(h, dict) and h.get("kind") == "approve_final"]
            R.check("решение человека видно в базе проекта (GET /api/hitl/project/{pid}: approve_final уже не pending)",
                    bool(fin) and all(h.get("decision") != "pending" for h in fin),
                    [(h.get("id"), h.get("decision")) for h in fin], known="F10-D5")
            pub = [n for n in ev.nodes if n["node_type"] == "publish"]
            R.check("узел publish не остался в состоянии running без шага публикации",
                    not any(n["status"] == "running" for n in pub), [(n["node_key"], n["status"]) for n in pub],
                    known="F10-D5")
        R.check("публикация не запускалась (нет записей publications, статус проекта не published)",
                ev.publications == 0 and str(ev.project.get("status")) != "published",
                {"publications": ev.publications, "status": ev.project.get("status")})
        R.advise("субтитры выключены по умолчанию в проекте: в этом прогоне они не проверялись")

    # --- repository data untouched ---
    slug_dir = ROOT / "data" / "videos" / chain.slug
    R.check("проект стенда не создан в боевой папке data/ репозитория (нет data/videos/<slug>)",
            not slug_dir.exists(), str(slug_dir))
    if repo_data_before is not None:
        after = data_fingerprint(ROOT / "data")
        if after != repo_data_before:
            R.advise("файлы в data/ репозитория изменились за время прогона (стенд туда не пишет: рабочая папка и база "
                     "его сервера вне репозитория; вероятнее всего работал другой процесс, например чужие тесты)")
    return {"level_reached": reached, "ledger": led, "counts_wire": wc, "counts_fake": dict(fake.counts),
            "timeline_kinds": kinds, "hitl": hitl_rows, "incidents_blocking": blocking}


SEAMS = ("auto_on_project_db",)

# ───────────────────────────── findings, honest lists, report ─────────────────────────────

# Findings of THIS stand about production code (file:line checked by a drift test). ``masked_by`` names the stand seam
# that lets the run continue; without it (``--no-seams``) the run stops where ``stops_at`` says.
FINDINGS: list[dict] = [
    {"id": "F10-D1", "level": "A", "node": "plan -> script (auto_advance)",
     "title": "Воркер двигает auto_mode-проект по строке master (state.db), а содержимое проекта лежит только в project.db: "
              "clamp_status_to_data откатывает plan_ready в new, цепочка встаёт после первой же ноды.",
     "where": ["app/main.py:717 (maybe_auto_advance_for_worker(s, ap, bot): без флага WORKER_USE_PROJECT_DB сессия master)",
               "app/orchestrator/auto_advance.py:1550 (clamp_status_to_data)",
               "app/services/step_data_guard.py:449 (лог «статус опережал данные»)"],
     "repro": "python scripts/e2e_full_chain.py --dry --until A --no-seams --work-dir <пустая папка вне репозитория>: "
              "статус planning -> plan_ready -> new, в логе сервера clamp_status_to_data: plan_ready -> new.",
     "masked_by": "боевой флаг WORKER_USE_PROJECT_DB=true (шов auto_on_project_db)", "stops_at": "plan_ready"},
    {"id": "F10-D3", "level": "B", "node": "hero (пропущен, нет героев) -> наблюдение",
     "title": "Независимая проверка результата hero не знает, что шаг законно пропущен (hero_skipped_empty): без файла "
              "героя она пишет блокирующий инцидент media_failure «no_hero_artifact» с передачей человеку, хотя конвейер "
              "идёт дальше.",
     "where": ["app/services/media_verification.py:238-239 (items.append(... 'no_hero_artifact'))"],
     "repro": "любой прогон стенда без персонажей: observation_incidents, type=media_failure, node_key=n_hero, "
              "details.fact «герои: не прошли проверку 1 из 1: hero (no_hero_artifact)».",
     "masked_by": None, "stops_at": None},
    {"id": "F10-D4", "level": "A", "node": "plan/script (проверка «Вердикт») -> spend_ledger",
     "title": "Платные вызовы текстовой модели для проверки в auto_advance записываются в spend_ledger без project_id и "
              "без run_id (вызов идёт вне запроса и вне тика шага).",
     "where": ["app/orchestrator/auto_advance.py:1816 (run_verdict_review вызывается из auto_advance, вне тика шага)",
               "app/services/advance_runner.py:51 (request_context.ensure_request_scope есть только у тика шага)"],
     "repro": "SELECT project_id, run_id FROM spend_ledger WHERE provider='vibecode': строки с NULL / ''.",
     "masked_by": None, "stops_at": None},
    {"id": "F10-D5", "level": "E", "node": "approve_final (решение человека) -> publish",
     "title": "Решение человека по approve_final теперь доходит до project.db (закрыто в W45, шов hitl_to_master убран), "
              "но узел publish в master остаётся running: шага публикации нет, публикацию не запускали.",
     "where": ["app/services/hitl_apply.py (apply_hitl_side_effects: узел publish не переводится)"],
     "repro": "любой прогон стенда до уровня E: узел n_publish в статусе running после решения человека.",
     "masked_by": None, "stops_at": None},
    {"id": "F10-D6", "level": "E", "node": "assembled -> approve_final (auto_mode)",
     "title": "В auto_mode воркер сам закрывает approve_final: за ~2 с после assembled запрос уже approved без человека "
              "(прогон --keep-auto), статус уходит в publishing; публикацию затем останавливает только "
              "SOCIAL_PUBLISH_ENABLED=false (publish_skipped), а узел publish остаётся running. Барьер человека держится "
              "лишь на выключенном auto_mode до сборки.",
     "where": ["app/orchestrator/auto_advance.py:1714-1724 (auto-approve visual, no GPT check)",
               "app/orchestrator/auto_advance.py:851 (_record_unchecked_auto_approve)"],
     "repro": "python scripts/e2e_full_chain.py --dry --until E --keep-auto ...: auto_final.requests[0].decision = approved, "
              "status_after = publishing; человек ничего не нажимал.",
     "masked_by": "стенд выключает auto_mode до сборки и сам одобряет финал как человек", "stops_at": None},
    {"id": "F10-D7", "level": "A", "node": "excel_gpt (граф по умолчанию)",
     "title": "Узлы «Работа с GPT» графа по умолчанию отправляют модели шаблон с неподставленными метками "
              "{{VAR:EXCEL_GPT_SHEET}}, {{VAR:EXCEL_GPT_TASK}}, {{BLOCK:enrich_*}}; воркер после них сам не идёт дальше "
              "(нужен ▶ оператора на следующей ноде).",
     "where": ["prompts/05_excel_gpt/default.md:5,12 (метки в мастер-промте)",
               "app/orchestrator/steps/enrich_xlsx.py:1116-1126 (_hold_pipeline_after_excel_gpt: пауза по замыслу)"],
     "repro": "work/llm_requests/0006.json: в тексте запроса литералы {{VAR:...}} и {{BLOCK:...}}.",
     "masked_by": "оператор нажимает ▶ через ручку UI", "stops_at": None},
    {"id": "F10-D8", "level": "A", "node": "plan (опрос GET /api/projects/{id} во время завершения шага)",
     "title": "GET /api/projects/{id} не только читает: он подтягивает runtime из master и коммитит. Если опрос попал в окно "
              "между фиксацией plan_ready в project.db и записью в master, правило «берём master» откатывает статус в "
              "planning и шаг plan запускается второй раз (второй платный вызов модели). Наблюдалось один раз при опросе "
              "каждые 0,5 с (прогон x12, два запроса plan в llm_requests); интерфейс опрашивает реже, воспроизводится "
              "не по заказу.",
     "where": ["app/web/routers/projects.py:222-234 (_heal_get_runtime_from_master + commit в GET)",
               "app/project_db.py:730-737 (pull_master_runtime: «берём master»)"],
     "repro": "work/llm_requests: два запроса «Тема ролика» подряд; server.log: «pull_master_runtime: #2 project.db=plan_ready "
              "master=planning — берём master». Стенд поэтому читает статус из project.db без GET во время прогона.",
     "masked_by": "стенд опрашивает project.db только на чтение", "stops_at": None},
    {"id": "F10-D9", "level": "A", "node": "лента проекта (timeline)",
     "title": "Заголовок строки ленты для события node_change состоит из технических статусов и английских слов "
              "(«running -> done (worker)», «pending -> queued (api)»): ни одной русской буквы в title_ru.",
     "where": ["app/services/timeline_ru.py:490-494 (describe: transitions из payload без перевода статусов и инициаторов)"],
     "repro": "GET /api/projects/<pid>/timeline: строки с title_ru без кириллицы (список в result.json: timeline_english_titles).",
     "masked_by": None, "stops_at": None},
    {"id": "F10-D10", "level": "D", "node": "sfx_gen -> независимая проверка результата",
     "title": "С платным провайдером sfx_gen пишет файлы .mp3, а независимая проверка результата открывает их как WAV "
              "44,1 кГц (wave.open): wave.Error, «committed native sfx result unavailable», блокирующий инцидент "
              "media_failure у ноды sfx_gen при полностью успешной ноде (3 файла, 0 проблем).",
     "where": ["app/services/sfx_gen.py:372 (ext = \"mp3\" if use_api else \"wav\")",
               "app/services/sfx_plan.py:538 (_sfx_wav_record: wave.open(...) на любом файле события)"],
     "repro": "любой прогон уровня D/E стенда: observation_incidents, type=media_failure, node_key=n_sfx_gen; "
              "событие node_result_verification: errors=['Error'].",
     "masked_by": None, "stops_at": None},
    {"id": "F10-D11", "level": "B", "node": "anim_pr_sidecar -> n_images (попытка наследуется)",
     "title": "Повторный платный вызов исправлен: результат sidecar для тех же входов не стирается при входе в шаг "
              "animation_prompts (step_replace). Остаётся: задача sidecar наследует попытку n_images, и вызов модели, не "
              "законченный к её закрытию, оставляет блокирующий инцидент uncertain_request у n_images (зависит от "
              "времени, бывает не в каждом прогоне).",
     "where": ["app/services/anim_pr_sidecar.py (ensure_anim_pr_sidecar: create_task копирует контекст попытки)"],
     "repro": "редкий прогон стенда до уровня B: инцидент uncertain_request у n_images (проверка «нет неподтверждённого "
              "запроса от фонового sidecar» даёт KNOWN F10-D11).",
     "masked_by": None, "stops_at": None},
]


def reality_lists(until: str, seams: list[str], real_whisper: bool) -> dict[str, list[str]]:
    lvl = level_index(until)
    return {
        "real": [x for x in (
            "настоящий процесс python -m app.main (тонкий запуск: сторож сокетов, ключи обнулены), пустая база вне репозитория",
            "настоящий воркер, auto_advance, harness-гейты, исполнители нод, проверки результата, наблюдение, лента, аудит",
            "путь оператора по HTTP: создание проекта, ▶ шагов, PATCH проекта, решение по запросу подтверждения",
            "каждый платный шов (картинки, видео, озвучка, SFX, музыка) считается дважды: хук внутри сервера и сам провайдер",
            "spend_ledger, operator_audit, observation_*, лента /timeline читаются из базы сервера",
            "настоящий ffmpeg/ffprobe" if lvl >= 3 else None,
            "настоящий faster-whisper (кэш, офлайн) по озвучке" if real_whisper and lvl >= 3 else None) if x],
        "not_real": [x for x in (
            "все провайдеры: один loopback-сервер (чат LLM, Outsee картинки и видео, ElevenLabs TTS и SFX, Kie Suno, "
            "публичные файловые хосты для стартового кадра), ключи фиктивные",
            "ответы модели: заранее заданные тексты стенда, формы проверены продакшен-разборщиками, смысл не оценивается",
            "картинки и клипы: однотонные/тестовые файлы; озвучка: Windows SAPI (Irina), не TTS провайдера",
            f"заплатки стенда (не поведение продакшена): {', '.join(seams) if seams else 'нет (--no-seams)'}",
            "хост ElevenLabs api.elevenlabs.io в sfx_gen зашит литералом: стенд переадресует его на loopback в хуке отправки",
            "цены: провайдеры не тарифицируются, суммы в spend_ledger нулевые/«cost_unknown»") if x],
        "not_proven": [x for x in (
            "поведение настоящих провайдеров, их ответов, ошибок, цен, лимитов и задержек",
            "качество картинок, видео, голоса, музыки и текста (не оценивается)",
            "публикация: человеческое подтверждение площадок и запуск публикации не проверялись",
            "субтитры (выключены по умолчанию в проекте)",
            "длинный проект (60-75 с, десятки кадров), параллельная работа человека в интерфейсе, перезапуск сервера посреди цепочки",
            "соответствие оценки слоя фактическим балансам провайдеров (нужен живой прогон)") if x],
    }


def live_estimate(frames: int, sfx_events: int, hero: int = 0, items: int = 0, ledger_usd: float | None = None) -> dict:
    """Paid requests a LIVE run of the same chain would send (ceilings for the owner), from the dry counts."""
    llm = expected_llm_calls("E", frames, hero)
    return {
        "frames": frames,
        "outsee_image": frames + hero + items,
        "outsee_video": frames,
        "elevenlabs_tts": 1,
        "elevenlabs_sfx": sfx_events,
        "kie_suno_music": 1,
        "text_llm": sum(llm.values()),
        "text_llm_by_node": llm,
        "free_but_external": {"public_file_host_uploads": uploads_expected(frames, hero)},
        "dry_ledger_total_usd": ledger_usd,
        "worst_case_if_a_result_is_rejected": {
            "elevenlabs_sfx": sfx_events * 3,
            "note": "sfx_gen повторяет платный запрос до 3 раз на событие, если проверка файла не прошла (наблюдалось в "
                    "сухом прогоне при коротком/длинном клипе); видео и картинки при отказе провайдера идут по лестнице "
                    "повторов и запасной модели (Kling через Kie) - в сухом прогоне не возникала",
        },
        "note": "TTS - ОДИН запрос на весь закадр (frame_audio.synthesize_per_frame_audio), не по кадрам; героев и "
                "предметов в малом проекте нет (hero/items пропущены); число запросов текстовой модели = 9 + 2*кадров + "
                "ceil(кадров/5)",
    }


def print_report(R: Report, result: dict) -> None:
    passed = [c for c in R.checks if c["ok"]]
    failed = [c for c in R.checks if not c["ok"] and not c.get("known")]
    known = [c for c in R.checks if not c["ok"] and c.get("known")]
    print("\n===== ИТОГ =====")
    print(f"уровень запрошен: {result['until']}; достигнут: {result.get('level_reached')}; стоп: {result.get('stop_reason') or 'нет'}")
    print(f"PASS: {len(passed)}  FAIL: {len(failed)}  KNOWN: {len(known)}  ADVISORY: {len(R.advisories)}")
    for c in failed:
        print("  FAIL  ", c["name"], "|", str(c["detail"])[:240])
    for c in known:
        print("  KNOWN ", c["known"], c["name"], "|", str(c["detail"])[:200])
    print("\n----- по нодам -----")
    for n in result.get("nodes", []):
        print(f"  {n['node_key']:<22} {n['node_type']:<18} {n['status']:<8} попыток={n['attempts']} {n.get('seconds')} с")
    print("\n----- запросы к провайдерам (отправлено / принято) -----")
    for k in PROVIDERS:
        print(f"  {k:<14} {result['counts_wire'].get(k, 0)} / {result['counts_fake'].get(k, 0)}")
    print("\n----- находки о продакшен-коде -----")
    for f in FINDINGS:
        print(f"  {f['id']} [{f['level']}] {f['node']}: {f['title'][:150]}")
        for w in f["where"]:
            print("       ", w)
        print("        закрыто заплаткой стенда:", f["masked_by"] or "нет")
    for key, title in (("real", "РЕАЛЬНО"), ("not_real", "НЕ реально (подмена)"), ("not_proven", "НЕ доказано")):
        print(title + ":")
        for line in result["reality"][key]:
            print("  -", line)
    if R.advisories:
        print("СОВЕТЫ:")
        for a in R.advisories:
            print("  *", a[:300])
    print("\nОЦЕНКА платных запросов живого прогона той же цепочки:", json.dumps(result["live_estimate"], ensure_ascii=False))
    print("ВЕРДИКТ:", result["verdict"])
    print("result.json:", result["result_json"])


# ───────────────────────────── main ─────────────────────────────

def build_cfg(work: Path, port: int, fake_port: int, app_cap_usd: float, seams: list[str] | None = None) -> dict:
    return {"work": str(work), "port": port, "fake_port": fake_port, "app_cap_usd": app_cap_usd,
            "seams": list(SEAMS if seams is None else seams)}


def server_env(work: Path, port: int, cfg: dict, asr_model: str) -> dict[str, str]:
    env = loc.server_env(os.environ, work, port, asr_model)
    env.pop("WORKER_ENABLED", None)
    env.update({
        "FC_CFG": json.dumps(cfg), "TEXT_LLM_PROVIDER": "vibecode", "IMAGE_PROVIDER": "outsee", "VIDEO_PROVIDER": "outsee",
        "GPT_MAX_RETRIES": "1", "FLEET_ENABLED": "false", "FLEET_AUTO_PULL": "false", "WEB_ENABLED": "true",
        "HITL_AUTO_APPROVE": "false", "BROWSER_CDP_AUTO_RECOVER": "false", "OUTSEE_HTTP_FALLBACK_CDP": "false",
        "AUDIO_USE_ELEVENLABS_FALLBACK": "true", "NVIDIA_ASR_PRELOAD_ON_STARTUP": "false", "SFX_ENABLED": "true",
        "SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_KEY": "unused", "MEDIA_SPEND_RECORDING": "true",
        "SPEND_DAILY_CAP_USD": str(cfg["app_cap_usd"]), "SPEND_PROJECT_CAP_USD": str(cfg["app_cap_usd"]),
        "SPEND_RUN_CAP_USD": str(cfg["app_cap_usd"]), "DISK_GUARD_LOW_FREE_GB": "0.001",
        "DISK_GUARD_CRITICAL_FREE_GB": "0.0005", "NO_PROXY": "127.0.0.1,localhost,::1", "no_proxy": "127.0.0.1,localhost,::1",
        "PYTHONUNBUFFERED": "1",
    })
    if "auto_on_project_db" in (cfg.get("seams") or []):
        env["WORKER_USE_PROJECT_DB"] = "true"   # боевой флаг F10-D1, не заплатка кода
    else:
        env.pop("WORKER_USE_PROJECT_DB", None)
    return env


def preflight(args) -> tuple[Path | None, str]:
    work = Path(args.work_dir).resolve()
    if loc.inside_repo(work):
        return None, "work-dir должен лежать ВНЕ репозитория"
    if work.exists() and any(work.iterdir()):
        return None, f"work-dir {work} не пуст: нужна новая пустая папка"
    try:
        loc.ffmpeg_exe()
    except RuntimeError as exc:
        return None, str(exc)
    if not shutil.which("ffprobe"):
        return None, "ffprobe не найден в PATH"
    if level_index(args.until) >= level_index("D"):
        if loc.find_cached_model(args.asr_model) is None:
            return None, f"модель faster-whisper {args.asr_model!r} не в локальном кэше (офлайн)"
    return work, ""


def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry", action="store_true", required=True, help="единственный режим: loopback-подмены, без денег и сети")
    ap.add_argument("--until", default="E", help="A|B|C|D|E (см. описание уровней)")
    ap.add_argument("--work-dir", required=True, help="пустая папка ВНЕ репозитория")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--fake-port", type=int, default=DEFAULT_FAKE_PORT)
    ap.add_argument("--voice-wav", default=DEFAULT_VOICE, help="озвучка-запасной вариант, если SAPI недоступен")
    ap.add_argument("--asr-model", default=DEFAULT_ASR)
    ap.add_argument("--timeout-s", type=int, default=1500)
    ap.add_argument("--stall-s", type=int, default=240, help="сколько секунд статус может не меняться")
    ap.add_argument("--topic", default="Человек находит старую записку и возвращает её на место")
    ap.add_argument("--no-seams", action="store_true",
                    help="без заплаток стенда: воспроизводит находки F10-D1/D2 (цепочка встаёт после plan)")
    ap.add_argument("--without-seam", action="append", default=[], choices=SEAMS,
                    help="отключить одну заплатку стенда (повторяемо), например auto_on_project_db: показывает находку F10-D1")
    ap.add_argument("--with-hero", action="store_true",
                    help="добавить в ответы excel_gpt реестр персонажей: нода hero делает платный референс (по умолчанию "
                         "в малом проекте героев нет, hero и items пропускаются)")
    ap.add_argument("--keep-auto", action="store_true",
                    help="уровень E без передачи финала человеку: только наблюдать, закроет ли approve_final автоматика (F10-D6)")
    ap.add_argument("--strict", action="store_true", help="KNOWN-находки тоже считаются провалом")
    args = ap.parse_args(argv)
    args.until = parse_until(args.until)
    return args


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--serve":
        return child_main()
    if any(a in ("--live", "--live-preflight") for a in argv):      # the paid part lives in its own sibling script
        return _load_sibling("e2e_full_chain_live").main(argv)
    try:
        args = parse_args(argv)
    except ValueError as exc:
        print("ОТКАЗ:", exc, file=sys.stderr)
        return EXIT_ARGS
    work, why = preflight(args)
    if work is None:
        print("ОТКАЗ до запуска:", why, file=sys.stderr)
        return EXIT_ARGS
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "prompts", work / "prompts")
    seams = [] if args.no_seams else [x for x in SEAMS if x not in args.without_seam]
    cfg = build_cfg(work, args.port, args.fake_port, app_cap_usd=50.0, seams=seams)
    env = server_env(work, args.port, cfg, args.asr_model)
    R = Report(strict=args.strict)
    repo_data = ROOT / "data"
    repo_data_before = data_fingerprint(repo_data)
    result: dict[str, Any] = {"mode": "dry", "until": args.until, "port": args.port, "work_dir": str(work),
                              "seams": seams, "nodes": [], "counts_wire": {}, "counts_fake": {}}
    oracle = TextOracle(with_hero=args.with_hero)
    fake = FakeProviders(args.fake_port, work, oracle, voice_wav=Path(args.voice_wav))
    fake.start()
    proc: subprocess.Popen | None = None
    log = None
    chain: Chain | None = None
    ev: Evidence | None = None
    info: dict = {}
    t_start = time.time()
    try:
        if loc.find_cached_model(args.asr_model) is not None:
            loc.link_cached_model(work / "data", args.asr_model)
        (work / "data").mkdir(parents=True, exist_ok=True)
        log = open(work / "server.log", "wb")
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(SELF), "--serve"], cwd=str(work), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{args.port}"
        if not R.check("сервер стартовал и отвечает /api/health", loc.wait_ready(base, proc), f"см. {work / 'server.log'}"):
            raise SystemExit(1)
        api = RecordingApi(base)
        db = loc.Db(work / "data" / "state.db")
        chain = Chain(args, work, api, db, R, fake)
        if chain.create() and chain.start():
            chain.stop_reason = chain.poll_until(args.until, args.timeout_s)
            print("СТОП:", chain.stop_reason or f"уровень {args.until} достигнут")
            settle(chain)
            ev = gather(chain, work)
    except SystemExit:
        pass
    except Exception as exc:  # noqa: BLE001
        R.check("сценарий дошёл до конца без исключений", False, f"{type(exc).__name__}: {exc}")
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
        fake.stop()
        loc.unlink_cached_model(work / "data", args.asr_model)
    if chain is not None and ev is not None:
        try:
            info = run_checks(chain, ev, fake, oracle, args.until, work, R, chain.human_final, repo_data_before)
        except Exception as exc:  # noqa: BLE001
            R.check("проверки результата выполнены без исключений", False, f"{type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
        rep = secrets_in(work)
        R.check("секреты: в рабочей папке (базы, журналы, ответы) нет ключей и токенов (scan_secrets.scan_tree)",
                rep["total_matches"] == 0, {"hits": rep["hits"][:3], "files": rep["files_scanned"]})
        if oracle.unknown:
            R.check("стенд знает ответ на каждый текстовый запрос узла", False, oracle.unknown[:8])
        result.update({
            "nodes": ev.nodes, "status_log": chain.status_log, "stop_reason": chain.stop_reason,
            "audit_rows": len(ev.audit), "stand_mutations": chain.api.mutations,
            "timeline_items": len(ev.timeline), "incidents": [{k: i[k] for k in ("node_key", "type", "severity", "status")}
                                                               for i in ev.incidents],
            "hitl": info.get("hitl"), "ledger": info.get("ledger"), "level_reached": info.get("level_reached"),
            "final_probe": ev.final_probe, "final_path": ev.final_path, "voice_seconds": ev.voice_seconds,
            "counts_wire": info.get("counts_wire", {}), "counts_fake": info.get("counts_fake", {}),
            "timeline_kinds": info.get("timeline_kinds"), "frames": ev.frames,
            "timeline_sample": ev.timeline[:40], "timeline_english_titles": english_only_titles(ev.timeline)[:40],
            "auto_final": chain.auto_final, "human_final": chain.human_final_detail,
            "llm_requests_by_kind": count_by([{"k": k} for k in oracle.kinds], "k"),
            "provider_extra": dict(fake.extra), "seconds": round(time.time() - t_start, 1),
            "log_evidence": log_evidence(work / "server.log"),
        })
    result["checks"] = R.checks
    result["failed"] = R.failed
    result["known_defects"] = R.known_defects
    result["advisories"] = R.advisories
    result["findings"] = FINDINGS
    result["reality"] = reality_lists(args.until, seams, level_index(args.until) >= 3)
    result["live_estimate"] = live_estimate(result.get("frames") or len(FRAME_TEXTS), oracle.sfx_events or (chain.sfx_events if chain else 0),
                                            hero=1 if args.with_hero else 0,
                                            ledger_usd=(result.get("ledger") or {}).get("usd"))
    result["verdict"] = verdict_of(R.failed, R.known_defects, result.get("level_reached"), args.until)
    result["result_json"] = str(work / "result.json")
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print_report(R, result)
    return EXIT_PASS if not R.failed and result.get("level_reached") == args.until else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
