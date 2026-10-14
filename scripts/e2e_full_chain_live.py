"""F10 (LIVE part): the same ONE tiny project through the WHOLE production chain, but the providers are REAL and PAID.

    python -X utf8 scripts/e2e_full_chain.py --live-preflight --until E --frames 1 --env-file <env file>      # pays nothing
    python -X utf8 scripts/e2e_full_chain.py --live --until E --frames 1 --work-dir <new empty dir outside the repo> \\
        --env-file <env file> --max-text-usd 2 --max-media-usd 1 [--with-music] [--with-sfx]

(``scripts/e2e_full_chain.py`` delegates every ``--live*`` call here; running this file directly works the same.)

What is the same as the dry stand: the real ``python -m app.main`` child (real worker, auto_advance with the production flag
``WORKER_USE_PROJECT_DB=true``, real executors), the same ``Chain`` driver (operator ▶ after the excel_gpt holds, the human
approves the final through ``POST /api/hitl/{id}/decision``), the same ``Report`` and evidence gatherer.

What is different (all of it is a SAFETY layer, none of it changes production behaviour):
  * keys: read ONLY by the names in ``ENV_NAMES`` from the file given by ``--env-file`` (or the environment variable
    ``VP_E2E_ENV_FILE``) by ``load_env_whitelist`` (nothing else is kept, nothing is printed, the .env of the repository is
    never opened by this script); they reach the child through its environment and are copied into ``settings``.
  * before anything starts: missing key NAMES (never values) -> refusal; the request estimate (``estimate``) is printed and a
    run whose estimate is above ``--max-text-usd`` / ``--max-media-usd`` is refused. The ceilings cannot be raised above the
    owner's rule ($2 text/LLM, $1 media per run).
  * the child's send hook (``judge_request``) lets through only the paid seams the chain needs, counts them, refuses a paid
    request over its per-bucket cap, any POST to a host that is not a configured provider/file host, an unknown POST, and
    Seedance 2.5 on Kie. The application caps (SPEND_*_CAP_USD) stay on as a second, looser wall.
  * the parent polls ``spend_ledger`` every two seconds; a text or media total over its ceiling (unknown-cost rows counted at a
    conservative price) or more frames than ``--frames`` stops the run at once (auto mode off, server killed).
  * music is OFF by default (a silent local mp3 is put into the project's music folder, the production "music on disk" path:
    no Suno, no money); sfx is OFF by default (SFX_ENABLED=false, the production pass-through). ``--with-music`` / ``--with-sfx``
    make them real and paid.
  * publication is never started (SOCIAL_PUBLISH_ENABLED=false; the stand only approves the final as a human).

Exit codes: 0 PASS, 1 FAIL, 2 bad arguments / refused before start (no key, estimate above a ceiling, unsafe work dir).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent
SELF = Path(__file__).resolve()
for _p in (str(_HERE), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_sibling(name: str):
    spec = importlib.util.spec_from_file_location(name + "_for_live", _HERE / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


fc = _load_sibling("e2e_full_chain")      # Chain, Report, gather, helpers, status tables
loc = fc.loc
scan = fc.scan

EXIT_PASS, EXIT_FAIL, EXIT_ARGS = fc.EXIT_PASS, fc.EXIT_FAIL, fc.EXIT_ARGS
DEFAULT_PORT = 18851
DEFAULT_ASR = "medium"

# ───────────────────────────── keys: whitelisted loader, nothing printed ─────────────────────────────

ENV_NAMES = ("VIBECODE_API_KEY", "VIBECODE_BASE_URL", "GPT_API_KEY", "GPT_BASE_URL", "GPT_RELAY_TOKEN", "GPT_PROXY_URL",
             "KIE_API_KEY", "KIE_API_BASE_URL", "OUTSEE_API_KEY", "OUTSEE_API_BASE_URL", "ELEVENLABS_API_KEY")
URL_NAMES = ("VIBECODE_BASE_URL", "GPT_BASE_URL", "KIE_API_BASE_URL", "OUTSEE_API_BASE_URL")
ENV_FILE_VAR = "VP_E2E_ENV_FILE"
MIN_KEY_LEN = 8
DEFAULTS = {"VIBECODE_BASE_URL": "https://vibecode.moe/v1", "KIE_API_BASE_URL": "https://api.kie.ai",
            "OUTSEE_API_BASE_URL": "https://outsee.io"}
KIE_UPLOAD_HOST = "kieai.redpandaai.co"
ELEVEN_HOST = "api.elevenlabs.io"


def load_env_whitelist(path: str, names: tuple[str, ...] = ENV_NAMES) -> dict[str, str]:
    """Only ``names`` from an env file; everything else is ignored and NOTHING is printed."""
    out: dict[str, str] = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            if key.strip() in names:
                out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def usable(value: str | None) -> bool:
    v = (value or "").strip()
    return len(v) >= MIN_KEY_LEN and not v.lower().startswith(("fake", "synthetic", "changeme", "your"))


def url_problem(name: str, value: str) -> str | None:
    parts = urlsplit(value.strip())
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return f"{name} должен быть https"
    if not host or loc.is_loopback_host(host):
        return f"{name} должен указывать на настоящий хост, не на loopback"
    if parts.username or parts.password or parts.query or parts.fragment:
        return f"{name} должен быть простым адресом без логина, запроса и якоря"
    return None


# ───────────────────────────── what a run needs and what it costs ─────────────────────────────

PROVIDER_CHOICES = {"text": ("vibecode", "kie"), "image": ("outsee", "kie"), "video": ("outsee", "kie")}
# Conservative per-request prices of THIS stand (owner-unconfirmed estimates, same spirit as the application's own
# uncalibrated constants); they are used for the pre-start estimate and for rows of unknown cost in the ledger check.
PRICE = {"llm_request": 0.10, "image": 0.10, "video": 0.40, "tts_per_char": 0.0003, "sfx_per_sec": 0.012, "sfx_seconds": 4.0,
         "music": 0.60}
UNKNOWN_ROW_USD = {"text": 0.05, "media": 0.30}      # = spend_guard.DEFAULT_REQUEST_ESTIMATE_USD / UNKNOWN_ROW_ESTIMATE_USD
RETRY_FACTOR = {"image": 2, "video": 2, "sfx": 3, "tts": 2, "llm": 2}
MAX_TEXT_CEILING, MAX_MEDIA_CEILING, MAX_FRAMES = 2.0, 1.0, 5
FORBIDDEN_MODEL_RE = re.compile(r"seedance[-_ /.]?2[-_.]?5", re.I)
TEXT_PROVIDERS = {"vibecode", "gpt", "llm"}


def required_keys(args) -> dict[str, str]:
    """Key NAME -> why it is needed, for exactly the levels and providers this run uses."""
    lvl = fc.level_index(args.until)
    need: dict[str, str] = {}
    need["VIBECODE_API_KEY" if args.text_provider == "vibecode" else "KIE_API_KEY"] = f"текст ({args.text_provider})"
    if lvl >= 1:
        need.setdefault("OUTSEE_API_KEY" if args.image_provider == "outsee" else "KIE_API_KEY", f"картинки ({args.image_provider})")
    if lvl >= 2:
        need.setdefault("OUTSEE_API_KEY" if args.video_provider == "outsee" else "KIE_API_KEY", f"видео ({args.video_provider})")
    if lvl >= 3:
        need["ELEVENLABS_API_KEY"] = "озвучка (ElevenLabs напрямую)"
        if args.with_music:
            need.setdefault("KIE_API_KEY", "музыка (Kie Suno)")
    return need


def missing_keys(live_env: dict[str, str], need: dict[str, str]) -> list[str]:
    return sorted(n for n in need if not usable(live_env.get(n)))


def estimate(args) -> dict:
    """Paid requests and a conservative USD estimate of THIS run, before anything starts."""
    n, lvl = int(args.frames), fc.level_index(args.until)
    llm = fc.expected_llm_calls(args.until, n)
    if not args.with_music:
        llm.pop("music_prompt", None)           # music comes from disk: no prompt request, no Suno
    if not args.with_sfx:
        llm.pop("sfx_plan", None)               # SFX_ENABLED=false: sfx_plan is a pass-through
    n_llm = sum(llm.values())
    sfx_events = int(args.sfx_max_events) if (args.with_sfx and lvl >= 3) else 0
    req = {"llm": n_llm, "image": n if lvl >= 1 else 0, "video": n if lvl >= 2 else 0, "tts": 1 if lvl >= 3 else 0,
           "sfx": sfx_events, "music": 1 if (args.with_music and lvl >= 3) else 0}
    media = {"image": round(req["image"] * PRICE["image"], 4), "video": round(req["video"] * PRICE["video"], 4),
             "tts": round(req["tts"] * n * int(args.tts_chars_per_frame) * PRICE["tts_per_char"], 4),
             "sfx": round(sfx_events * PRICE["sfx_seconds"] * PRICE["sfx_per_sec"], 4),
             "music": round(req["music"] * PRICE["music"], 4)}
    text_usd = round(n_llm * PRICE["llm_request"], 4)
    media_usd = round(sum(media.values()), 4)
    worst_media = round(sum(media[k] * RETRY_FACTOR.get(k, 1) for k in media), 4)
    return {"frames": n, "until": args.until, "requests": req, "llm_by_node": llm, "text_usd": text_usd,
            "media_usd": media_usd, "media_breakdown": media, "worst_case_text_usd": round(text_usd * RETRY_FACTOR["llm"], 4),
            "worst_case_media_usd": worst_media, "prices": dict(PRICE),
            "public_file_host_uploads": args.frames if lvl >= 2 else 0}


def estimate_problem(est: dict, max_text: float, max_media: float) -> str | None:
    if est["text_usd"] > max_text + 1e-9:
        return f"оценка текста ${est['text_usd']} выше потолка --max-text-usd {max_text}"
    if est["media_usd"] > max_media + 1e-9:
        return (f"оценка медиа ${est['media_usd']} выше потолка --max-media-usd {max_media} "
                f"({', '.join(f'{k}={v}' for k, v in est['media_breakdown'].items() if v)})")
    return None


def wire_caps(args) -> dict[str, int]:
    """Per-bucket ceilings on PAID requests the child lets out (the request-count wall under the money walls)."""
    n, lvl = int(args.frames), fc.level_index(args.until)
    est_llm = estimate(args)["requests"]["llm"]
    caps = {"llm": est_llm * 2 + 4, "outsee_image": 0, "outsee_video": 0, "kie_media": 0, "tts": 0, "sfx": 0, "kie_music": 0}
    if lvl >= 1:
        caps["outsee_image" if args.image_provider == "outsee" else "kie_media"] += n + 2
    if lvl >= 2:
        caps["outsee_video" if args.video_provider == "outsee" else "kie_media"] += n + 1
    if lvl >= 3:
        caps["tts"] = 3
        caps["sfx"] = int(args.sfx_max_events) * 3 if args.with_sfx else 0
        caps["kie_music"] = 1 if args.with_music else 0
    return caps


def allowed_post_hosts(live_env: dict[str, str]) -> list[str]:
    hosts = set(fc.PUBLIC_IMAGE_HOSTS) | {ELEVEN_HOST, KIE_UPLOAD_HOST}
    for name in ("VIBECODE_BASE_URL", "GPT_BASE_URL", "KIE_API_BASE_URL", "OUTSEE_API_BASE_URL"):
        value = live_env.get(name) or DEFAULTS.get(name) or ""
        host = (urlsplit(value).hostname or "").lower()
        if host:
            hosts.add(host)
    return sorted(hosts)


def video_extra(args) -> dict:
    extra: dict[str, Any] = {"hero_mode": "no_hero"}
    extra["video_generator"] = "veo_3_1_lite" if args.video_provider == "outsee" else "seedance_1_5_pro"
    if args.image_provider == "kie":
        extra["image_generator"] = "z_image"
    return extra


# ───────────────────────────── the child's send hook (pure judgement, unit-tested) ─────────────────────────────

PAID_BUCKETS = ("llm", "outsee_image", "outsee_video", "tts", "sfx", "kie_music", "kie_media")


def classify_live(method: str, host: str, path: str) -> str:
    m, p, h = method.upper(), path or "", (host or "").lower()
    if m == "POST" and h in fc.PUBLIC_IMAGE_HOSTS:
        return "upload"
    b = fc.classify_request(method, host, p)
    if b not in ("other",):
        return b
    if m == "POST":
        if p.endswith("/chat/completions") or p.endswith("/responses"):
            return "llm"
        if p.endswith("/jobs/createTask"):
            return "kie_media"
        if "upload" in p.lower():
            return "upload"
    return "other"


def host_allowed(host: str, allowed) -> bool:
    h = (host or "").lower()
    return loc.is_loopback_host(h) or any(h == a or h.endswith("." + a) for a in allowed)


def model_of(body: bytes | None) -> str:
    try:
        data = json.loads((body or b"{}").decode("utf-8", "replace"))
        return str(data.get("model") or "")[:80] if isinstance(data, dict) else ""
    except ValueError:
        return ""


def judge_request(method: str, host: str, scheme: str, path: str, body: bytes | None, counts: dict, caps: dict,
                  allowed) -> tuple[str, str, str]:
    """-> (action pass|refuse, bucket, reason). ``counts`` is updated when a PAID request passes."""
    m = method.upper()
    bucket = classify_live(m, host, path)
    if loc.is_loopback_host(host):
        return "pass", bucket, ""
    if m in ("GET", "HEAD"):
        if scheme != "https":
            return "refuse", bucket, "http_get_non_loopback"
        return "pass", bucket, ""
    if m != "POST":
        return "refuse", bucket, "method_not_allowed"
    if not host_allowed(host, allowed):
        return "refuse", bucket, "host_not_allowed"
    if scheme != "https":
        return "refuse", bucket, "not_https"
    if bucket == "other":
        return "refuse", bucket, "unknown_post"
    if bucket in ("kie_media", "kie_music") and FORBIDDEN_MODEL_RE.search(model_of(body) + " " + (body or b"")[:4000].decode("utf-8", "replace")):
        return "refuse", bucket, "forbidden_model"
    if bucket in PAID_BUCKETS:
        if counts.get(bucket, 0) + 1 > int(caps.get(bucket, 0)):
            return "refuse", bucket, "cap_exceeded"
        counts[bucket] = counts.get(bucket, 0) + 1
    return "pass", bucket, ""


def live_settings_plan(cfg: dict, env: dict) -> dict[str, Any]:
    """settings.<name> -> value for the child: explicit values from the whitelisted env, everything unneeded blanked
    (a stray repository .env cannot add a key or a route)."""
    vb = (env.get("VIBECODE_BASE_URL") or DEFAULTS["VIBECODE_BASE_URL"]).rstrip("/")
    text = cfg["text_provider"]
    plan: dict[str, Any] = {
        "text_llm_provider": text, "image_provider": cfg["image_provider"], "video_provider": cfg["video_provider"],
        "vibecode_api_key": env.get("VIBECODE_API_KEY", "") if text == "vibecode" else "",
        "vibecode_base_url": vb if text == "vibecode" else DEFAULTS["VIBECODE_BASE_URL"],
        "gpt_relay_token": env.get("GPT_RELAY_TOKEN", ""), "gpt_proxy_url": env.get("GPT_PROXY_URL") or None,
        "kie_api_key": env.get("KIE_API_KEY", ""), "kie_api_base_url": (env.get("KIE_API_BASE_URL") or DEFAULTS["KIE_API_BASE_URL"]).rstrip("/"),
        "outsee_api_key": env.get("OUTSEE_API_KEY", ""), "outsee_api_base_url": (env.get("OUTSEE_API_BASE_URL") or DEFAULTS["OUTSEE_API_BASE_URL"]).rstrip("/"),
        "elevenlabs_api_key": env.get("ELEVENLABS_API_KEY", ""), "elevenlabs_proxy_url": None,
        "sfx_enabled": bool(cfg["with_sfx"]), "social_publish_enabled": False, "media_spend_recording": True,
        "browser_cdp_url": "http://127.0.0.1:9", "browser_cdp_auto_recover": False, "outsee_http_fallback_cdp": False,
        "spend_daily_cap_usd": float(cfg["app_cap_usd"]), "spend_project_cap_usd": float(cfg["app_cap_usd"]),
        "spend_run_cap_usd": float(cfg["app_cap_usd"]),
    }
    if text == "vibecode":           # parity with the dry stand: the shared gpt_* route points at the same service
        plan["gpt_api_key"] = env.get("VIBECODE_API_KEY", "")
        plan["gpt_base_url"] = re.sub(r"/v1$", "", vb)
    else:
        plan["gpt_api_key"] = env.get("GPT_API_KEY") or env.get("KIE_API_KEY", "")
        plan["gpt_base_url"] = env.get("GPT_BASE_URL", "")
    return plan


def install_live_hook(cfg: dict) -> None:
    work = Path(cfg["work"])
    wire = work / "wire.jsonl"
    caps, allowed = cfg["wire_caps"], cfg["allowed_post_hosts"]
    counts: dict[str, int] = {}
    lock = threading.Lock()
    import httpx
    original_send = httpx.AsyncClient.send

    async def send(client, request, **kwargs):
        if isinstance(getattr(client, "_transport", None), httpx.ASGITransport):
            return await original_send(client, request, **kwargs)
        host = (request.url.host or "").lower()
        try:
            body = request.content
        except Exception:  # noqa: BLE001 - a streamed body is unknown to the model check
            body = None
        with lock:
            action, bucket, reason = judge_request(request.method, host, request.url.scheme, request.url.path, body, counts,
                                                   caps, allowed)
        if action == "refuse":
            fc.wire_append(wire, {"event": "refused", "host": host, "method": request.method, "path": request.url.path,
                                  "bucket": bucket, "reason": reason})
            raise RuntimeError(f"stand: request refused ({reason}, {bucket})")
        if bucket not in ("download", "outsee_free", "kie_free", "other"):
            fc.wire_append(wire, {"event": "request", "bucket": bucket, "method": request.method, "host": host,
                                  "path": request.url.path, "model": model_of(body) if bucket in PAID_BUCKETS else ""})
        return await original_send(client, request, **kwargs)

    httpx.AsyncClient.send = send
    from app.bots import outsee_http

    async def no_curl_post(*_a, **_k):
        return None        # the curl submit fallback is a second transport the hook could not count

    outsee_http._post_generate_via_curl = no_curl_post


def child_live_main() -> int:
    import runpy
    cfg = json.loads(os.environ["FC_LIVE_CFG"])
    sys.path.insert(0, str(ROOT))
    import app.services.prompt_library as pl
    pl.PROMPTS_ROOT = Path(os.environ["AL_STAND_PROMPTS"])
    from app.settings import settings
    for name, value in live_settings_plan(cfg, os.environ).items():
        try:
            setattr(settings, name, value)
        except Exception:  # noqa: BLE001
            object.__setattr__(settings, name, value)
    install_live_hook(cfg)
    runpy.run_module("app.main", run_name="__main__", alter_sys=True)
    return 0


# ───────────────────────────── ledger: the fact of the spend ─────────────────────────────

def row_class(r: dict) -> str:
    prov = str(r.get("provider") or "").lower()
    note = str(r.get("note") or "").lower()
    if prov in TEXT_PROVIDERS:
        return "text"
    if prov == "kie" and any(w in note for w in ("gpt", "chat", "llm", "text", "responses")):
        return "text"
    return "media"


def ledger_spend(rows: list[dict]) -> dict:
    """Logical spend (reserve + settle delta + actual, as in spend_guard.run_totals) split into text and media; a row of
    unknown cost (usd 0 with an unknown/uncalibrated mark) counts at a conservative price."""
    out = {"text_usd": 0.0, "media_usd": 0.0, "unknown_rows": 0, "paid_rows": 0}
    for r in rows:
        if r.get("kind") == "event":
            continue
        cls = row_class(r)
        usd = float(r.get("usd") or 0.0)
        note = str(r.get("note") or "").lower()
        out["paid_rows"] += 1
        if r.get("kind") == "actual" and usd == 0.0 and any(m in note for m in ("cost_unknown", "uncalibrated", "basis=estimate")):
            usd = UNKNOWN_ROW_USD[cls]
            out["unknown_rows"] += 1
        out[f"{cls}_usd"] = round(out[f"{cls}_usd"] + usd, 6)
    return out


def ceiling_breach(spend: dict, max_text: float, max_media: float) -> str:
    if spend["text_usd"] > max_text + 1e-9:
        return f"СТОП-ПОТОЛОК: текст по журналу ${spend['text_usd']:.4f} выше потолка ${max_text}"
    if spend["media_usd"] > max_media + 1e-9:
        return f"СТОП-ПОТОЛОК: медиа по журналу ${spend['media_usd']:.4f} выше потолка ${max_media}"
    return ""


def make_silent_mp3(seconds: int = 60) -> bytes:
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "silence.mp3"
        subprocess.run([fc._ff(), "-y", "-v", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono", "-t", str(seconds),
                        "-c:a", "libmp3lame", "-b:a", "32k", str(out)], check=True, timeout=60, capture_output=True)
        return out.read_bytes()


class DeadApi:
    """Stands in for the API after the server was killed (a hard stop): evidence is then read from the databases only."""

    def __init__(self, mutations: list[dict]) -> None:
        self.mutations = mutations

    def req(self, *a, **k):
        return 0, None

    get = post = req


# ───────────────────────────── checks ─────────────────────────────

FILE_KINDS_BY_LEVEL = {"scene_image": "B", "scene_video": "C", "audio": "D", "final_video": "E"}


def artifact_problems(arts: list[dict], until: str) -> list[str]:
    """Every artifact row must point at a real, non-empty file (or a folder with files); the kinds the level needs must exist."""
    bad: list[str] = []
    for a in arts:
        p = Path(str(a.get("path") or ""))
        if not str(a.get("path") or ""):
            bad.append(f"{a.get('kind')}: пустой путь")
        elif p.is_file():
            if p.stat().st_size <= 0:
                bad.append(f"{a.get('kind')}: пустой файл {p.name}")
        elif p.is_dir():
            if not any(x.is_file() and x.stat().st_size > 0 for x in p.rglob("*")):
                bad.append(f"{a.get('kind')}: в папке {p.name} нет файлов")
        else:
            bad.append(f"{a.get('kind')}: файла нет {p.name}")
    have = {str(a.get("kind")) for a in arts}
    for kind, lvl in FILE_KINDS_BY_LEVEL.items():
        if fc.level_index(until) >= fc.level_index(lvl) and kind not in have:
            bad.append(f"нет артефакта {kind} для уровня {until}")
    return bad


def run_checks_live(chain, ev, args, work: Path, R, human_final: bool, caps: dict, ceilings: tuple[float, float],
                    repo_data_before) -> dict:  # noqa: C901
    until, pid = args.until, chain.pid
    seen = [e["status"] for e in chain.status_log]
    final_ok = bool(ev.final_probe.get("has_video") and ev.final_probe.get("has_audio"))
    reached = fc.level_after(seen, final_ok, human_final)
    R.check(f"достигнут уровень {until} (достигнут: {reached or 'ни один'})",
            reached is not None and fc.level_index(reached) >= fc.level_index(until), chain.stop_reason or "ок")
    R.check("жёсткая остановка по потолку или по числу кадров не сработала", not str(chain.stop_reason).startswith("СТОП"),
            chain.stop_reason or "ок")

    scope = [n for n in ev.nodes if fc.node_in_scope(n["node_type"], until, n["node_key"]) and n["node_type"] != "publish"]
    for n in scope:
        R.check(f"нода {n['node_key']} ({n['node_type']}): статус done", n["status"] in ("done", "skipped"),
                {k: n.get(k) for k in ("status", "attempts", "error")})
        if int(n.get("attempts") or 0) > 1:
            R.advise(f"нода {n['node_key']}: {n['attempts']} попыток (повтор после отказа)")
    R.check(f"кадров в проекте не больше --frames {args.frames}", 0 < ev.frames <= int(args.frames), ev.frames)

    # --- providers: ceilings, not exact numbers ---
    wc = fc.wire_counts(ev.wire)
    for bucket in fc.wire_counts(ev.wire):
        if bucket in caps:
            R.check(f"{bucket}: платных запросов {wc[bucket]} не больше потолка {caps.get(bucket, 0)}",
                    wc[bucket] <= int(caps.get(bucket, 0)), wc[bucket])
    refused = [e for e in ev.wire if e.get("event") == "refused"]
    R.check("хук отправки не отказал ни одному запросу (чужой хост, неизвестный POST, потолок, запрещённая модель)",
            not refused, [(e.get("reason"), e.get("bucket"), e.get("host")) for e in refused[:5]])
    forbidden = [e for e in ev.wire if e.get("event") == "request" and FORBIDDEN_MODEL_RE.search(str(e.get("model") or ""))]
    R.check("Seedance 2.5 не вызывался", not forbidden, len(forbidden))

    # --- no second payment for the same request key ---
    intents = [r for r in ev.idem if r["event"] == "intent"]
    keys = [r["key"] for r in intents]
    R.check("Outsee: ключи намерений (intent) не повторяются", len(keys) == len(set(keys)), len(keys) - len(set(keys)))
    outsee_posts = wc.get("outsee_image", 0) + wc.get("outsee_video", 0)
    R.check("Outsee: платных отправок не больше, чем намерений с уникальным ключом (нет повторной оплаты по ключу)",
            outsee_posts <= len(set(keys)), {"отправок": outsee_posts, "намерений": len(set(keys))})
    led = fc.summarize_ledger(ev.ledger, pid)
    R.check("spend_ledger: нет повторных строк settle с одним и тем же ref", not led["duplicate_refs"], led["duplicate_refs"])
    spend = ledger_spend(ev.ledger)
    R.check(f"расход по журналу: текст ${spend['text_usd']:.4f} не больше потолка ${ceilings[0]}",
            spend["text_usd"] <= ceilings[0] + 1e-9, spend)
    R.check(f"расход по журналу: медиа ${spend['media_usd']:.4f} не больше потолка ${ceilings[1]}",
            spend["media_usd"] <= ceilings[1] + 1e-9, spend)
    paid_media = [r for r in ev.ledger if r.get("kind") != "event" and row_class(r) == "media"]
    R.check("spend_ledger: у каждой строки медиа есть project_id этого проекта и run_id",
            all(r.get("project_id") == pid and r.get("run_id") for r in paid_media),
            [(r["provider"], r["project_id"], r["run_id"]) for r in paid_media if r.get("project_id") != pid or not r.get("run_id")][:5])
    R.check("spend_ledger: нет строк чужого проекта", led["other_project"] == 0, led["other_project"])
    R.check("spend_ledger: нет событий потери записи (write_failed)", not any(r.get("source") == "write_failed" for r in ev.ledger), "")

    # --- files on disk ---
    pdb = loc.Db(Path(work) / "data" / "videos" / chain.slug / "project.db")
    try:
        arts = pdb.q("SELECT kind, path FROM artifacts WHERE project_id = ? ORDER BY id", (pid,))
    except sqlite3.Error:
        arts = []
    probs = artifact_problems(arts, until)
    R.check("артефакты: каждый файл существует, не пуст; нужные виды для уровня есть", not probs, probs[:6])
    if fc.level_index(until) >= fc.level_index("C"):
        clips = [a["path"] for a in arts if str(a["kind"]) == "scene_video" and Path(a["path"]).is_file()]
        probes = [loc.ffprobe(Path(c)) for c in clips]
        R.check("клипы: ffprobe видит видео ненулевой длительности", bool(probes) and all(p.get("has_video") and float(p.get("duration") or 0) > 0 for p in probes),
                [(p.get("has_video"), p.get("duration")) for p in probes])

    # --- operator audit, journal, incidents ---
    gaps = fc.audit_gaps(chain.api.mutations, ev.audit)
    R.check(f"operator_audit: каждое из {len(chain.api.mutations)} изменяющих обращений стенда записано", not gaps, gaps[:5])
    R.check("operator_audit: у каждой записи есть автор и исход", not fc.audit_unattributed(ev.audit), fc.audit_unattributed(ev.audit)[:3])
    R.check("журнал наблюдения: observation_degradations пуста", not ev.degradations, len(ev.degradations))
    typed = [i for i in ev.incidents if i.get("type") and i.get("status")]
    R.check("инциденты: каждый типизирован (type и статус)", len(typed) == len(ev.incidents), len(ev.incidents))
    blocking = []
    for inc in ev.incidents:
        try:
            d = json.loads(inc["details"]) if isinstance(inc["details"], str) else (inc["details"] or {})
        except ValueError:
            d = {}
        if d.get("blocking"):
            blocking.append((inc["node_key"], inc["type"], str(d.get("fact"))[:120]))
    known = {"n_hero": "F10-D3", "n_sfx_gen": "F10-D10"}
    other = [b for b in blocking if b[0] not in known and not (b[0] == "n_images" and b[1] == "uncertain_request")]
    R.check("нет блокирующих инцидентов наблюдения (кроме описанных находок F10-D3/D10/D11)", not other, other)
    if blocking and not other:
        R.advise(f"блокирующие инциденты из списка известных находок: {[(b[0], b[1]) for b in blocking]}")

    # --- final, publication, repository ---
    if fc.level_index(until) >= fc.level_index("E"):
        R.check("final.mp4 существует, в нём есть видео и звук (ffprobe)", final_ok, ev.final_probe or ev.final_path)
        R.check("approve_final принят человеком через production-ручку", human_final, chain.human_final_detail)
    R.check("публикация не запускалась (нет записей publications, статус не published)",
            ev.publications == 0 and str(ev.project.get("status")) != "published",
            {"publications": ev.publications, "status": ev.project.get("status")})
    slug_dir = ROOT / "data" / "videos" / chain.slug
    R.check("проект не создан в боевой папке data/ репозитория", not slug_dir.exists(), str(slug_dir))
    if repo_data_before is not None and fc.data_fingerprint(ROOT / "data") != repo_data_before:
        R.advise("файлы в data/ репозитория изменились за время прогона (стенд туда не пишет; вероятно, другой процесс)")
    return {"level_reached": reached, "ledger": led, "spend": spend, "counts_wire": wc, "blocking": blocking}


# ───────────────────────────── arguments, preflight ─────────────────────────────

def parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true", help="ПЛАТНЫЙ прогон с потолками (нужны --work-dir и ключи)")
    mode.add_argument("--live-preflight", action="store_true", help="ничего не платит: ключи по именам, оценка, потолки")
    ap.add_argument("--until", default="E", help="A|B|C|D|E")
    ap.add_argument("--work-dir", help="новая пустая папка ВНЕ репозитория (обязательна для --live)")
    ap.add_argument("--env-file", default=os.environ.get(ENV_FILE_VAR), help=f"файл с ключами (или переменная {ENV_FILE_VAR}); читаются только имена из белого списка")
    ap.add_argument("--max-text-usd", type=float, default=MAX_TEXT_CEILING)
    ap.add_argument("--max-media-usd", type=float, default=MAX_MEDIA_CEILING)
    ap.add_argument("--frames", type=int, default=1)
    ap.add_argument("--with-music", action="store_true", help="настоящая музыка Kie Suno (≈ $0.6, съедает потолок медиа)")
    ap.add_argument("--with-sfx", action="store_true", help="настоящие звуки ElevenLabs (по умолчанию SFX_ENABLED=false)")
    ap.add_argument("--sfx-max-events", type=int, default=3)
    ap.add_argument("--tts-chars-per-frame", type=int, default=160)
    ap.add_argument("--text-provider", choices=PROVIDER_CHOICES["text"], default="vibecode")
    ap.add_argument("--image-provider", choices=PROVIDER_CHOICES["image"], default="outsee")
    ap.add_argument("--video-provider", choices=PROVIDER_CHOICES["video"], default="outsee")
    ap.add_argument("--app-cap-usd", type=float, default=10.0, help="внешняя стена SPEND_*_CAP_USD приложения (мягче потолков стенда)")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--asr-model", default=DEFAULT_ASR)
    ap.add_argument("--timeout-s", type=int, default=3600)
    ap.add_argument("--stall-s", type=int, default=600)
    ap.add_argument("--topic", default="Человек находит старую записку и возвращает её на место. Сценарий: ровно ОДНА короткая "
                                       "фраза до 15 слов, одна сцена, ровно один кадр.")
    args = ap.parse_args(argv)
    args.until = fc.parse_until(args.until)
    args.keep_auto = False
    args.with_hero = False
    if args.live and not args.work_dir:
        ap.error("--live требует --work-dir")
    if not 1 <= args.frames <= MAX_FRAMES:
        ap.error(f"--frames должен быть от 1 до {MAX_FRAMES}")
    if args.max_text_usd <= 0 or args.max_media_usd <= 0:
        ap.error("потолки должны быть больше нуля")
    if args.max_text_usd > MAX_TEXT_CEILING or args.max_media_usd > MAX_MEDIA_CEILING:
        ap.error(f"потолки выше правила владельца (текст ${MAX_TEXT_CEILING}, медиа ${MAX_MEDIA_CEILING} на прогон) не принимаются")
    if args.sfx_max_events < 1 or args.tts_chars_per_frame < 1:
        ap.error("--sfx-max-events и --tts-chars-per-frame должны быть положительными")
    return args


def print_estimate(args, est: dict, need: dict[str, str], missing: list[str]) -> None:
    print("===== ЖИВОЙ ПРОГОН F10: оценка до старта =====")
    print(f"уровень: {args.until}; кадров: {args.frames}; текст: {args.text_provider}; картинки: {args.image_provider}; "
          f"видео: {args.video_provider} ({'veo-3-1-lite' if args.video_provider == 'outsee' else 'seedance-1-5-pro'}); "
          f"музыка: {'Suno (платно)' if args.with_music else 'файл с диска (бесплатно)'}; звуки: {'ElevenLabs (платно)' if args.with_sfx else 'выкл.'}")
    print("запросов:", json.dumps(est["requests"], ensure_ascii=False), "| текстовые по узлам:", json.dumps(est["llm_by_node"], ensure_ascii=False))
    print(f"оценка: текст ${est['text_usd']} (худший случай ${est['worst_case_text_usd']}) при потолке ${args.max_text_usd}; "
          f"медиа ${est['media_usd']} (худший случай ${est['worst_case_media_usd']}) при потолке ${args.max_media_usd}")
    print("медиа по статьям:", json.dumps(est["media_breakdown"], ensure_ascii=False), "| цены-подсказки:", json.dumps(est["prices"]))
    print("нужные ключи (имена):", ", ".join(f"{k} [{v}]" for k, v in need.items()), "| отсутствуют:", ", ".join(missing) or "нет")
    if est["public_file_host_uploads"]:
        print(f"ВНИМАНИЕ: {est['public_file_host_uploads']} стартовый кадр(ов) видео уходят на ПУБЛИЧНЫЕ файловые хосты "
              "(litterbox/catbox/uguu/0x0/tmpfiles): Yandex S3 не настроен.")
    print("публикация: не запускается; WORKER_USE_PROJECT_DB=true (D1); сервер слушает только 127.0.0.1")


def preflight_live(args) -> tuple[dict | None, dict, str]:
    """Everything that can be decided WITHOUT paying and without any network call: -> (live_env or None, estimate, refusal)."""
    est = estimate(args)
    need = required_keys(args)
    live_env: dict[str, str] = {}
    if not args.env_file:
        return None, est, f"не указан файл ключей: --env-file или переменная {ENV_FILE_VAR}"
    if not Path(args.env_file).is_file():
        return None, est, "файл ключей не найден (путь не печатается); ничего не отправлено"
    live_env = load_env_whitelist(args.env_file)
    miss = missing_keys(live_env, need)
    print_estimate(args, est, need, miss)
    if miss:
        return live_env, est, "нет ключей (только имена): " + ", ".join(miss)
    for name in URL_NAMES:
        if live_env.get(name):
            why = url_problem(name, live_env[name])
            if why:
                return live_env, est, why
    why = estimate_problem(est, args.max_text_usd, args.max_media_usd)
    if why:
        return live_env, est, why
    return live_env, est, ""


def print_live_report(R, result: dict) -> None:
    passed = [c for c in R.checks if c["ok"]]
    failed = [c for c in R.checks if not c["ok"] and not c.get("known")]
    known = [c for c in R.checks if not c["ok"] and c.get("known")]
    print("\n===== ИТОГ ЖИВОГО ПРОГОНА =====")
    print(f"уровень запрошен: {result['until']}; достигнут: {result.get('level_reached')}; стоп: {result.get('stop_reason') or 'нет'}")
    print(f"PASS: {len(passed)}  FAIL: {len(failed)}  KNOWN: {len(known)}  ADVISORY: {len(R.advisories)}")
    for c in failed:
        print("  FAIL  ", c["name"], "|", str(c["detail"])[:240])
    for c in known:
        print("  KNOWN ", c["known"], c["name"], "|", str(c["detail"])[:200])
    for n in result.get("nodes", []):
        print(f"  {n['node_key']:<22} {n['node_type']:<18} {n['status']:<8} попыток={n['attempts']} {n.get('seconds')} с")
    print("запросов к провайдерам (по хуку):", json.dumps(result.get("counts_wire"), ensure_ascii=False))
    print("расход по журналу (факт + консервативные оценки неизвестных строк):", json.dumps(result.get("spend"), ensure_ascii=False))
    for a in R.advisories:
        print("  *", a[:300])
    print("НЕ доказано:", "; ".join(result["not_proven"]))
    print("ВЕРДИКТ:", result["verdict"])
    print("result.json:", result["result_json"])


NOT_PROVEN = ("качество картинок, видео, голоса, текста (не оценивается)", "публикация (не запускалась)",
              "точные цены провайдеров: суммы — записи журнала и консервативные оценки, не счёт провайдера (сверить по кабинетам)",
              "сторонние библиотеки вне httpx (прямые сокеты) хук не видит; Chrome CDP заменён на мёртвый адрес",
              "длинный проект, перезапуск сервера посреди цепочки, параллельная работа человека")


def main(argv: list[str] | None = None) -> int:  # noqa: C901
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--serve-live":
        return child_live_main()
    try:
        args = parse_args(argv)
    except ValueError as exc:
        print("ОТКАЗ:", exc, file=sys.stderr)
        return EXIT_ARGS
    live_env, est, why = preflight_live(args)
    if why:
        print("ОТКАЗ до запуска:", why, file=sys.stderr)
        return EXIT_ARGS
    if args.live_preflight:
        if args.work_dir:
            work_ok, why2 = fc.preflight(args)
            if work_ok is None:
                print("ОТКАЗ до запуска:", why2, file=sys.stderr)
                return EXIT_ARGS
        print("ПРЕДПОЛЁТ ПРОЙДЕН: ключи на месте, оценка в пределах потолков. Ничего не отправлялось и не оплачивалось.")
        return EXIT_PASS
    work, why = fc.preflight(args)
    if work is None:
        print("ОТКАЗ до запуска:", why, file=sys.stderr)
        return EXIT_ARGS
    assert live_env is not None
    caps = wire_caps(args)
    ceilings = (float(args.max_text_usd), float(args.max_media_usd))
    work.mkdir(parents=True, exist_ok=True)
    shutil.copytree(ROOT / "prompts", work / "prompts")
    cfg = {"work": str(work), "port": args.port, "fake_port": 0, "app_cap_usd": float(args.app_cap_usd), "seams": list(fc.SEAMS),
           "text_provider": args.text_provider, "image_provider": args.image_provider, "video_provider": args.video_provider,
           "with_sfx": bool(args.with_sfx), "wire_caps": caps, "allowed_post_hosts": allowed_post_hosts(live_env)}
    env = fc.server_env(work, args.port, cfg, args.asr_model)
    env.update({"FC_LIVE_CFG": json.dumps(cfg), "TEXT_LLM_PROVIDER": args.text_provider, "IMAGE_PROVIDER": args.image_provider,
                "VIDEO_PROVIDER": args.video_provider, "GPT_MAX_RETRIES": "2", "SFX_ENABLED": "true" if args.with_sfx else "false",
                "SOCIAL_PUBLISH_ENABLED": "false", "BROWSER_CDP_URL": "http://127.0.0.1:9", "SUPABASE_URL": "http://127.0.0.1:9"})
    need = required_keys(args)
    if args.text_provider == "vibecode":
        need.setdefault("VIBECODE_API_KEY", "")
    for name in ENV_NAMES:      # only what the chosen providers use travels to the child
        if live_env.get(name) and (name in need or name.endswith("_BASE_URL") or name in ("GPT_RELAY_TOKEN", "GPT_PROXY_URL", "GPT_API_KEY")):
            env[name] = live_env[name]
    if env.get("WORKER_USE_PROJECT_DB") != "true":
        print("ОТКАЗ до запуска: WORKER_USE_PROJECT_DB не включён в окружении сервера (D1)", file=sys.stderr)
        return EXIT_ARGS
    needles = {n: v.encode("utf-8") for n, v in live_env.items() if n.endswith(("_KEY", "_TOKEN")) and len(v) >= MIN_KEY_LEN}

    R = fc.Report(strict=False)
    repo_data_before = fc.data_fingerprint(ROOT / "data")
    result: dict[str, Any] = {"mode": "live", "until": args.until, "port": args.port, "work_dir": str(work), "nodes": [],
                              "estimate": est, "ceilings": {"text_usd": ceilings[0], "media_usd": ceilings[1]},
                              "wire_caps": caps, "not_proven": list(NOT_PROVEN)}
    proc: subprocess.Popen | None = None
    log = None
    chain = None
    ev = None
    info: dict = {}
    t_start = time.time()
    killed = False
    try:
        if loc.find_cached_model(args.asr_model) is not None:
            loc.link_cached_model(work / "data", args.asr_model)
        (work / "data").mkdir(parents=True, exist_ok=True)
        log = open(work / "server.log", "wb")
        proc = subprocess.Popen([sys.executable, "-X", "utf8", "-u", str(SELF), "--serve-live"], cwd=str(work), env=env,
                                stdout=log, stderr=subprocess.STDOUT)
        base = f"http://127.0.0.1:{args.port}"
        if not R.check("сервер стартовал и отвечает /api/health", loc.wait_ready(base, proc), f"см. {work / 'server.log'}"):
            raise SystemExit(1)
        api = fc.RecordingApi(base)
        db = loc.Db(work / "data" / "state.db")
        chain = fc.Chain(args, work, api, db, R, None)
        chain.create_extra = video_extra(args)
        if chain.create():
            if fc.level_index(args.until) >= fc.level_index("D") and not args.with_music:
                music_dir = work / "data" / "videos" / chain.slug / "music"
                music_dir.mkdir(parents=True, exist_ok=True)
                (music_dir / "music_standin_silence.mp3").write_bytes(make_silent_mp3(60))
            state_db = loc.Db(work / "data" / "state.db")
            last_check = [0.0]

            def tick(_status: str) -> str:
                if time.time() - last_check[0] < 2.0:
                    return ""
                last_check[0] = time.time()
                try:
                    spend = ledger_spend(fc.ledger_rows(state_db))
                    chain.live_spend = spend
                    why_stop = ceiling_breach(spend, *ceilings)
                    if why_stop:
                        return why_stop
                    n = len(loc.Db(work / "data" / "videos" / chain.slug / "project.db").q(
                        "SELECT id FROM frames WHERE project_id = ?", (chain.pid,)))
                    if n > int(args.frames):
                        return f"СТОП-КАДРЫ: модель разбила сценарий на {n} кадров, а разрешено {args.frames}; до платных медиа"
                except sqlite3.Error:
                    pass
                return ""

            chain.tick_hook = tick
            if chain.start():
                chain.stop_reason = chain.poll_until(args.until, args.timeout_s)
                print("СТОП:", chain.stop_reason or f"уровень {args.until} достигнут")
                if str(chain.stop_reason).startswith("СТОП"):
                    api.req("PATCH", f"/api/projects/{chain.pid}", json_body={"auto_mode": False})
                    proc.kill()
                    killed = True
                    chain.api = DeadApi(api.mutations)
                else:
                    fc.settle(chain)
                ev = fc.gather(chain, work)
    except SystemExit:
        pass
    except KeyboardInterrupt:
        R.check("прогон не прерван оператором", False, "KeyboardInterrupt")
    except Exception as exc:  # noqa: BLE001
        R.check("сценарий дошёл до конца без исключений", False, f"{type(exc).__name__}: {exc}")
        import traceback
        traceback.print_exc()
    finally:
        if proc is not None:
            if not killed:
                proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
        if log is not None:
            log.close()
        loc.unlink_cached_model(work / "data", args.asr_model)
    if chain is not None and ev is not None:
        try:
            info = run_checks_live(chain, ev, args, work, R, chain.human_final, caps, ceilings, repo_data_before)
        except Exception as exc:  # noqa: BLE001
            R.check("проверки результата выполнены без исключений", False, f"{type(exc).__name__}: {exc}")
        rep = scan.scan_tree([work], needles)
        R.check("секреты: в рабочей папке нет ключей и токенов (scan_secrets.scan_tree, синтаксис и значения ключей этого прогона)",
                rep["total_matches"] == 0, {"hits": [h["found"] for h in rep["hits"][:3]], "files": rep["files_scanned"]})
        result.update({"nodes": ev.nodes, "status_log": chain.status_log, "stop_reason": chain.stop_reason, "frames": ev.frames,
                       "ledger": info.get("ledger"), "spend": info.get("spend"), "counts_wire": info.get("counts_wire", {}),
                       "level_reached": info.get("level_reached"), "final_probe": ev.final_probe, "audit_rows": len(ev.audit),
                       "incidents": [{k: i[k] for k in ("node_key", "type", "severity", "status")} for i in ev.incidents],
                       "seconds": round(time.time() - t_start, 1), "stand_mutations": chain.api.mutations,
                       "log_evidence": fc.log_evidence(work / "server.log")})
    result.update({"checks": R.checks, "failed": R.failed, "known_defects": R.known_defects, "advisories": R.advisories})
    result["args"] = {k: v for k, v in vars(args).items() if k != "env_file"}
    result["verdict"] = fc.verdict_of(R.failed, R.known_defects, result.get("level_reached"), args.until)
    result["result_json"] = str(work / "result.json")
    (work / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    final = scan.scan_tree([work], needles)
    if final["total_matches"]:
        print("ВНИМАНИЕ: в рабочей папке найден секрет после записи протокола:", final["hits"][:2], file=sys.stderr)
    print_live_report(R, result)
    return EXIT_PASS if not R.failed and result.get("level_reached") == args.until else EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
