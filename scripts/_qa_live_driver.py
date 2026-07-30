"""LIVE QA driver for project #50 testovyy-trukraym.

Polls status, auto-approves text HITL when needed, appends journal lines,
and starts the next pipeline step when the previous one becomes ready.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JOURNAL = ROOT / "docs" / "QA-RUN-LIVE.md"
STATE = ROOT / "data" / "_qa_live_state.json"
PID = 50
BASE = "http://127.0.0.1:8765"

# ordered pipeline after plan
PIPELINE = [
    ("script", "script_ready", "scripting"),
    ("split", "frames_ready", "splitting"),
    ("hero", "hero_ready", "generating_hero"),
    ("items", "items_ready", "generating_items"),
    ("enrich", "enrich_1_ready", "enriching"),  # may vary
    ("img_pr", "image_prompts_ready", "generating_image_prompts"),
    ("img", "images_ready", "generating_images"),
    ("anim_pr", "animation_prompts_ready", "generating_animation_prompts"),
    ("video", "videos_ready", "generating_videos"),
    ("audio", "audio_ready", "generating_audio"),
    ("music", "music_ready", "generating_music"),
    ("assemble", "assembled", "assembling"),
]

READY_SET = {ready for _, ready, _ in PIPELINE}
RUNNING_PREFIXES = (
    "scripting",
    "splitting",
    "generating_",
    "enriching",
    "assembling",
    "publishing",
)


def now() -> str:
    return datetime.now().strftime("%H:%M")


def iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def http_json(method: str, path: str, body: dict | None = None, timeout: int = 60):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        BASE + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"} if data else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", errors="replace")
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"detail": raw}


def append_journal(doing: str, done: str, result: str, bug: str = "-") -> None:
    line = f"| {now()} | {doing} | {done} | {result} | {bug} |"
    # Windows console may be cp1251 — never crash the driver on print
    try:
        print(line)
    except UnicodeEncodeError:
        print(line.encode("ascii", "replace").decode("ascii"))
    text = JOURNAL.read_text(encoding="utf-8")
    marker = "## Журнал"
    if marker not in text:
        text += "\n" + marker + "\n\n| Время | Делаю | Сделал | Результат | Баг/фикс |\n|-------|-------|--------|-----------|----------|\n"
    # insert after header row of journal table
    lines = text.splitlines()
    out = []
    inserted = False
    for i, ln in enumerate(lines):
        out.append(ln)
        if (
            not inserted
            and ln.startswith("|-------")
            and i > 0
            and "Время" in lines[i - 1]
        ):
            # find journal section only
            # look back for Журнал nearby
            window = "\n".join(lines[max(0, i - 8) : i])
            if "Журнал" in window or "делаю" in window.lower():
                out.append(line)
                inserted = True
    if not inserted:
        out.append(line)
    JOURNAL.write_text("\n".join(out) + "\n", encoding="utf-8")


def load_state() -> dict:
    if STATE.exists():
        return json.loads(STATE.read_text(encoding="utf-8"))
    return {"started_steps": [], "last_status": None, "approvals": []}


def save_state(st: dict) -> None:
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


def project() -> dict:
    _, p = http_json("GET", f"/api/projects/{PID}")
    return p


def approve_pending_text_hitl(st: dict) -> None:
    code, items = http_json("GET", f"/api/hitl/project/{PID}")
    if code != 200 or not isinstance(items, list):
        return
    for h in items:
        if h.get("decision") != "pending":
            continue
        kind = h.get("kind") or ""
        # auto-approve text/media reviews during LIVE QA
        if kind.startswith("approve_"):
            hid = h["id"]
            c, resp = http_json("POST", f"/api/hitl/{hid}/decision", {"decision": "approve"})
            msg = f"HITL {hid} {kind} -> {c}"
            print(iso(), msg)
            if hid not in st.get("approvals", []):
                st.setdefault("approvals", []).append(hid)
                append_journal(
                    f"HITL {kind}",
                    f"approve #{hid}",
                    "OK" if c == 200 else str(resp)[:120],
                    "-" if c == 200 else "HITL FAIL",
                )


def next_step_for_status(status: str) -> str | None:
    # map ready status -> next step code
    mapping = {
        "plan_ready": "script",
        "script_ready": "split",
        "frames_ready": "hero",
        "hero_ready": "items",
        "items_ready": "img_pr",  # enrich may be separate via excel_gpt
        "enrich_1_ready": "img_pr",
        "enrich_2_ready": "img_pr",
        "enrich_3_ready": "img_pr",
        "enrich_4_ready": "img_pr",
        "enrich_5_ready": "img_pr",
        "image_prompts_ready": "img",
        "images_ready": "anim_pr",
        "animation_prompts_ready": "video",
        "videos_ready": "audio",
        "audio_ready": "music",
        "music_ready": "assemble",
    }
    return mapping.get(status)


def is_running(status: str) -> bool:
    return any(status.startswith(p) for p in RUNNING_PREFIXES) or status.endswith("ing")


def maybe_start_next(st: dict, status: str) -> None:
    if is_running(status):
        return
    step = next_step_for_status(status)
    if not step:
        return
    key = f"{step}@{status}"
    if key in st.get("started_steps", []):
        return
    print(iso(), "START", step, "from", status)
    code, resp = http_json("POST", f"/api/projects/{PID}/steps/{step}/run")
    st.setdefault("started_steps", []).append(key)
    detail = resp.get("status") if isinstance(resp, dict) else str(resp)[:200]
    append_journal(
        f"Run {step}",
        f"POST /steps/{step}/run",
        f"{code} -> {detail}",
        "-" if code == 200 else "RUN FAIL",
    )


def tail_backend_errors(n: int = 40) -> list[str]:
    log = ROOT / "data" / "backend.log"
    if not log.exists():
        # pick newest backend-*.log
        cands = sorted((ROOT / "data").glob("backend-*.log"), key=lambda p: p.stat().st_mtime)
        if not cands:
            return []
        log = cands[-1]
    lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    interesting = [
        ln
        for ln in lines[-200:]
        if any(x in ln.lower() for x in ("error", "traceback", "fail", "exception", "testovyy", "project=50", " #50"))
    ]
    return interesting[-n:]


def main_once() -> str:
    st = load_state()
    p = project()
    status = p.get("status") or "?"
    script_len = len(p.get("script_text") or "")
    plan_len = len(p.get("general_plan") or "")
    print(iso(), "status=", status, "plan=", plan_len, "script=", script_len)

    if status != st.get("last_status"):
        append_journal(
            "poll status",
            f"{st.get('last_status')} -> {status}",
            f"plan={plan_len} script={script_len}",
        )
        st["last_status"] = status

    approve_pending_text_hitl(st)
    maybe_start_next(st, status)
    save_state(st)
    return status


def main_loop(seconds: int = 900, interval: int = 20) -> None:
    append_journal("driver loop", f"{seconds}s interval={interval}", "start")
    end = time.time() + seconds
    while time.time() < end:
        try:
            status = main_once()
            if status == "assembled":
                append_journal("pipeline", "assembled", "DONE", "—")
                break
        except Exception as e:
            append_journal("driver error", type(e).__name__, str(e)[:160], "DRIVER")
            print("ERR", e)
        time.sleep(interval)


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "once":
        main_once()
    else:
        secs = int(sys.argv[1]) if len(sys.argv) > 1 else 900
        main_loop(secs)
