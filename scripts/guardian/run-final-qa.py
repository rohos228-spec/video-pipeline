#!/usr/bin/env python3
"""Единый финальный прогон QA -> docs/QA-FINAL-REPORT.md"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASE = "http://127.0.0.1:8765"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
REPORT = ROOT / "docs" / "QA-FINAL-REPORT.md"

STATUS_ORDER = [
    "new",
    "plan_ready",
    "script_ready",
    "frames_ready",
    "hero_ready",
    "items_ready",
    "enrich_1_ready",
    "enrich_5_ready",
    "image_prompts_ready",
    "images_ready",
    "animation_prompts_ready",
    "videos_ready",
    "audio_ready",
    "assembled",
]

GPT_CHAIN = [
    ("plan", "plan_ready", 300),
    ("script", "script_ready", 900),
    ("split", "frames_ready", 900),
    ("img_pr", "image_prompts_ready", 900),
    ("anim_pr", "animation_prompts_ready", 900),
]

BOT_CHAIN = [
    ("hero", "hero_ready", 1800),
    ("img", "images_ready", 3600),
    ("video", "videos_ready", 7200),
    ("audio", "audio_ready", 1800),
    ("assemble", "assembled", 1200),
]


def log(lines: list[str], msg: str) -> None:
    lines.append(msg)
    enc = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(msg.encode(enc, errors="replace").decode(enc))


def http(method: str, path: str, body: dict | None = None, timeout: float = 60.0) -> tuple[int, object]:
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    headers = {"Accept": "application/json"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, raw


def status_index(st: str) -> int:
    try:
        return STATUS_ORDER.index(st)
    except ValueError:
        return -1


def wait_status(pid: int, ready: str, timeout: int) -> tuple[bool, str]:
    end = time.time() + timeout
    last = ""
    while time.time() < end:
        code, body = http("GET", f"/api/projects/{pid}")
        if code == 200 and isinstance(body, dict):
            last = str(body.get("status", ""))
            if last == ready:
                return True, last
            if status_index(last) >= status_index(ready):
                return True, last
            if "fail" in last.lower():
                return False, last
        time.sleep(5)
    return False, last


def run_step(pid: int, step: str, ready: str, timeout: int, lines: list[str]) -> bool:
    code, body = http("GET", f"/api/projects/{pid}")
    st = body.get("status", "") if isinstance(body, dict) else ""
    if status_index(st) >= status_index(ready):
        log(lines, f"  [skip] {step} already at {st}")
        return True
    code, _ = http("POST", f"/api/projects/{pid}/steps/{step}/run")
    if code != 200:
        log(lines, f"  [FAIL] {step} start http={code}")
        return False
    ok, fin = wait_status(pid, ready, timeout)
    mark = "ok" if ok else "FAIL"
    log(lines, f"  [{mark}] {step} -> {ready} ({fin})")
    return ok


def run_cmd(lines: list[str], title: str, cmd: list[str], cwd: Path | None = None) -> int:
    log(lines, f"\n### {title}")
    run_args: list[str] | str = cmd
    if sys.platform == "win32":
        run_args = " ".join(f'"{c}"' if " " in c else c for c in cmd)
    try:
        p = subprocess.run(
            run_args,
            cwd=cwd or ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=sys.platform == "win32",
        )
    except Exception as e:
        lines.append(f"    ERROR: {e}")
        lines.append("    exit=1")
        return 1
    tail = (p.stdout or "") + (p.stderr or "")
    for ln in tail.strip().splitlines()[-8:]:
        lines.append(f"    {ln}")
    lines.append(f"    exit={p.returncode}")
    return p.returncode


def main() -> int:
    lines: list[str] = []
    started = datetime.now(timezone.utc).isoformat()
    log(lines, f"==> FINAL QA {started}")

    # Health
    code, h = http("GET", "/api/health", timeout=5)
    backend_ok = code == 200 and isinstance(h, dict) and h.get("status") == "ok"
    log(lines, f"backend: {'ok' if backend_ok else 'DOWN'}")
    if not backend_ok:
        lines.append("\n## STOP: backend down on :8765")
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        return 1

    results: dict[str, int] = {}

    results["audit"] = run_cmd(
        lines,
        "Studio audit",
        ["powershell", "-ExecutionPolicy", "Bypass", "-File", str(ROOT / "scripts/guardian/run-studio-audit.ps1")],
    )

    results["pytest_web"] = run_cmd(
        lines,
        "Web pytest",
        [str(PY), "-m", "pytest", "tests/test_web_api_integration.py", "tests/test_web_dry_run_step.py", "tests/test_studio_version.py", "-q", "--tb=no"],
    )

    results["api_matrix"] = run_cmd(
        lines,
        "API matrix",
        [str(PY), str(ROOT / "scripts/guardian/run-full-verification.py"), "--skip-live"],
    )

    web = ROOT / "web"
    results["e2e"] = run_cmd(lines, "Playwright e2e", ["npm", "run", "test:e2e"], cwd=web)

    results["pytest_all"] = run_cmd(
        lines,
        "Full pytest",
        [str(PY), "-m", "pytest", "tests/", "-q", "--tb=no"],
    )

    # Projects
    code, projects = http("GET", "/api/projects")
    smoke_id = full_id = None
    if code == 200 and isinstance(projects, list):
        for p in projects:
            slug = p.get("slug") or ""
            if "qa-smoke" in slug:
                smoke_id = p["id"]
            if "qa-full" in slug:
                full_id = p["id"]
    log(lines, f"\n### Live pipeline QA-SMOKE id={smoke_id}")
    if smoke_id:
        for step, ready, to in GPT_CHAIN:
            run_step(smoke_id, step, ready, to, lines)

    log(lines, f"\n### Live pipeline QA-FULL id={full_id}")
    if full_id:
        for step, ready, to in GPT_CHAIN:
            run_step(full_id, step, ready, to, lines)
        code, p = http("GET", f"/api/projects/{full_id}")
        st = p.get("status", "") if isinstance(p, dict) else ""
        hm = p.get("hero_mode", "") if isinstance(p, dict) else ""
        if hm != "no_hero" and status_index(st) >= status_index("frames_ready"):
            for step, ready, to in BOT_CHAIN:
                if not run_step(full_id, step, ready, to, lines):
                    log(lines, f"  [stop] bot chain at {step}")
                    break
        else:
            log(lines, f"  [skip] bot chain hero_mode={hm} status={st}")

    # Project 15 regression
    code, p15 = http("GET", "/api/projects/15")
    if code == 200 and isinstance(p15, dict):
        log(lines, f"\n### Project 15: status={p15.get('status')}")

    # Write markdown report
    passed = sum(1 for v in results.values() if v == 0)
    failed = sum(1 for v in results.values() if v != 0)
    md = [
        "# QA FINAL REPORT",
        f"Generated: {datetime.now(timezone.utc).isoformat()}",
        "",
        "## Automation",
        f"- Studio audit: **{'PASS' if results.get('audit') == 0 else 'FAIL'}**",
        f"- Web pytest: **{'PASS' if results.get('pytest_web') == 0 else 'FAIL'}**",
        f"- API matrix: **{'PASS' if results.get('api_matrix') == 0 else 'FAIL'}**",
        f"- Playwright e2e: **{'PASS' if results.get('e2e') == 0 else 'FAIL'}**",
        f"- Full pytest: **{'PASS' if results.get('pytest_all') == 0 else 'FAIL'}** (known outsee/enrich failures OK)",
        "",
        "## Live pipeline",
        f"- QA-SMOKE #{smoke_id}",
        f"- QA-FULL #{full_id}",
        "",
        "## Log",
        "```",
        *lines,
        "```",
    ]
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text("\n".join(md), encoding="utf-8")
    except Exception as e:
        log(lines, f"report write failed: {e}")
    log(lines, f"\n==> Report: {REPORT}")
    log(lines, f"==> Automation {passed}/{passed+failed} green")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as e:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(f"# QA FINAL REPORT\n\nCRASH: {e}\n", encoding="utf-8")
        raise
