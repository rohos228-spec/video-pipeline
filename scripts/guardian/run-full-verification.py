#!/usr/bin/env python3
"""Полная API-верификация Studio (часть FULL-VERIFICATION.md)."""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

BASE = "http://127.0.0.1:8765"
SAFE_DRY = ("plan", "script", "split", "img_pr", "anim_pr", "assemble")
FORBIDDEN_DRY = ("hero", "items", "img", "video", "audio")
ALL_STEPS = (
    "plan",
    "script",
    "split",
    "hero",
    "items",
    "enrich_1",
    "enrich_2",
    "enrich_3",
    "enrich_4",
    "enrich_5",
    "img_pr",
    "img",
    "anim_pr",
    "video",
    "audio",
    "assemble",
)


@dataclass
class Row:
    phase: str
    check_id: str
    name: str
    ok: bool
    detail: str = ""


@dataclass
class Report:
    started: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    rows: list[Row] = field(default_factory=list)
    qa_smoke_id: int | None = None
    qa_full_id: int | None = None

    def add(self, phase: str, cid: str, name: str, ok: bool, detail: str = "") -> None:
        self.rows.append(Row(phase, cid, name, ok, detail))
        mark = "ok" if ok else "FAIL"
        line = f"  [{mark}] {cid}: {name}" + (f" - {detail}" if detail else "")
        enc = getattr(sys.stdout, "encoding", None) or "utf-8"
        print(line.encode(enc, errors="replace").decode(enc))


def http(
    method: str,
    path: str,
    body: dict | None = None,
    timeout: float = 30.0,
) -> tuple[int, Any]:
    url = f"{BASE}{path}"
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = raw
        return e.code, payload


def find_or_create_project(report: Report, topic: str, hero_mode: str, slug_hint: str) -> int | None:
    code, projects = http("GET", "/api/projects")
    if code != 200 or not isinstance(projects, list):
        report.add("prep", "P0", "list projects", False, str(projects))
        return None
    for p in projects:
        if slug_hint in (p.get("slug") or ""):
            return int(p["id"])
    code, created = http(
        "POST",
        "/api/projects",
        {"topic": topic, "hero_mode": hero_mode, "auto_mode": False},
    )
    if code not in (200, 201) or not isinstance(created, dict):
        report.add("prep", "P1", f"create {slug_hint}", False, str(created))
        return None
    pid = int(created["id"])
    report.add("prep", "P1", f"create {slug_hint}", True, f"id={pid}")
    return pid


def wait_status(pid: int, want: set[str], timeout_sec: int = 300) -> tuple[bool, str]:
    deadline = time.time() + timeout_sec
    last = ""
    while time.time() < deadline:
        code, body = http("GET", f"/api/projects/{pid}")
        if code == 200 and isinstance(body, dict):
            last = str(body.get("status", ""))
            if last in want:
                return True, last
            if last.endswith("_failed") or last == "failed":
                return False, last
        time.sleep(4)
    return False, last


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--skip-live",
        action="store_true",
        help="Skip live GPT pipeline steps (plan..anim_pr)",
    )
    args = parser.parse_args()

    report = Report()
    print(f"==> Full API verification {BASE}\n")

    # A — health
    code, h = http("GET", "/api/health")
    report.add("A", "A1", "health", code == 200 and isinstance(h, dict) and h.get("status") == "ok")

    code, sv = http("GET", "/api/studio-version")
    report.add(
        "A",
        "A2",
        "studio-version",
        code == 200 and isinstance(sv, dict) and "build" in sv and "ui_stale" in sv,
    )

    code, cat = http("GET", "/api/projects/steps/catalog")
    ok_cat = (
        code == 200
        and isinstance(cat, list)
        and any(x.get("code") == "plan" and x.get("label") for x in cat)
    )
    report.add("A", "A3", "steps catalog", ok_cat, f"codes={len(cat) if isinstance(cat, list) else 0}")

    code, wfs = http("GET", "/api/workflows")
    def_wf = None
    if code == 200 and isinstance(wfs, list):
        for w in wfs:
            if w.get("is_default"):
                def_wf = w
                break
    report.add("A", "A4", "workflows default", def_wf is not None)

    if def_wf:
        code, wf = http("GET", f"/api/workflows/{def_wf['id']}")
        report.add(
            "A",
            "A5",
            "workflow nodes",
            code == 200 and isinstance(wf, dict) and len(wf.get("nodes") or []) > 0,
        )

    # QA projects
    report.qa_smoke_id = find_or_create_project(
        report,
        "QA-SMOKE автопроверка 3 кадра",
        "no_hero",
        "qa-smoke",
    )
    report.qa_full_id = find_or_create_project(
        report,
        "QA-FULL автопроверка пайплайн",
        "hero",
        "qa-full",
    )

    for label, pid in (("smoke", report.qa_smoke_id), ("full", report.qa_full_id)):
        if pid is None:
            continue
        code, _ = http("POST", f"/api/projects/{pid}/ensure-run")
        report.add("prep", f"E-{label}", "ensure-run", code == 200)
        code, _ = http("POST", f"/api/projects/{pid}/ensure-run")
        report.add("prep", f"E2-{label}", "ensure-run idempotent", code == 200)

    smoke = report.qa_smoke_id
    if smoke:
        for step in SAFE_DRY:
            code, body = http("POST", f"/api/projects/{smoke}/steps/{step}/run?dry_run=true")
            # На новом проекте только plan; остальные — 400 (нет prerequisite).
            expect_ok = code in (200, 400)
            report.add("dry", f"D-{step}", f"dry_run {step}", expect_ok, str(code))
        for step in FORBIDDEN_DRY:
            code, body = http("POST", f"/api/projects/{smoke}/steps/{step}/run?dry_run=true")
            report.add(
                "dry",
                f"DF-{step}",
                f"dry_run forbidden {step}",
                code == 400,
                str(body)[:120] if body else "",
            )

    # Per-step dry_run on full (may fail if graph disables step)
    full = report.qa_full_id
    if full:
        for step in ALL_STEPS:
            code, body = http("POST", f"/api/projects/{full}/steps/{step}/run?dry_run=true")
            if step in FORBIDDEN_DRY:
                expect = code == 400
            else:
                expect = code in (200, 400)
            report.add(
                "nodes",
                f"N-{step}",
                f"dry_run/full {step}",
                expect,
                f"http={code}",
            )

        code, detail = http("GET", f"/api/projects/{full}")
        if code == 200 and isinstance(detail, dict):
            report.add(
                "nodes",
                "N-get",
                "get project recompute",
                detail.get("hero_mode") == "hero",
                f"status={detail.get('status')}",
            )
            code, frames = http("GET", f"/api/projects/{full}/frames")
            report.add(
                "nodes",
                "N-frames",
                "list frames",
                code == 200,
                f"count={len(frames) if isinstance(frames, list) else '?'}",
            )
            for kind in ("images", "videos"):
                code, mr = http("GET", f"/api/projects/{full}/media-review?kind={kind}")
                report.add("nodes", f"N-media-{kind}", f"media-review {kind}", code == 200)

            code, hitl = http("GET", f"/api/hitl/project/{full}")
            report.add(
                "nodes",
                "N-hitl",
                "list hitl",
                code == 200,
                f"items={len(hitl) if isinstance(hitl, list) else '?'}",
            )

    # Live pipeline on smoke (GPT steps only; bots skipped)
    LIVE_GPT_STEPS = (
        ("plan", "plan_ready", 300),
        ("script", "script_ready", 600),
        ("split", "frames_ready", 600),
        ("img_pr", "image_prompts_ready", 600),
        ("anim_pr", "animation_prompts_ready", 600),
    )

    def run_live_step(pid: int, step: str, ready: str, timeout: int) -> None:
        code, body = http("GET", f"/api/projects/{pid}")
        st0 = body.get("status") if isinstance(body, dict) else ""
        if st0 == ready:
            report.add("pipeline", f"L-{step}", f"live {step}", True, f"already {ready}")
            return
        # Проект уже прошёл дальше по пайплайну — не считаем ошибкой.
        order = (
            "new",
            "plan_ready",
            "script_ready",
            "frames_ready",
            "image_prompts_ready",
            "animation_prompts_ready",
            "videos_ready",
            "audio_ready",
            "assembled",
        )
        if st0 in order and ready in order and order.index(st0) >= order.index(ready):
            report.add("pipeline", f"L-{step}", f"live {step}", True, f"past {ready} ({st0})")
            return
        code, _ = http("POST", f"/api/projects/{pid}/steps/{step}/run")
        if code != 200:
            report.add("pipeline", f"L-{step}", f"live {step} start", False, f"http={code}")
            return
        ok, st = wait_status(pid, {ready}, timeout_sec=timeout)
        report.add("pipeline", f"L-{step}", f"live {step} -> {ready}", ok, f"status={st}")

    # Регрессия на боевом проекте #15 (если есть)
    code, p15 = http("GET", "/api/projects/15")
    if code == 200 and isinstance(p15, dict):
        st = p15.get("status", "")
        report.add(
            "regress",
            "R15-status",
            "project 15 not frames_ready with videos",
            not (st == "frames_ready"),
            f"status={st}",
        )
        code, vids = http("GET", "/api/projects/15/assets?kind=videos")
        n = len(vids) if isinstance(vids, list) else 0
        report.add("regress", "R15-videos", "project 15 has videos", n >= 1, f"count={n}")

    if smoke and not args.skip_live:
        run_live_step(smoke, "plan", "plan_ready", 300)
        for step, ready, timeout in LIVE_GPT_STEPS[1:]:
            run_live_step(smoke, step, ready, timeout)
    elif smoke and args.skip_live:
        report.add("pipeline", "L-skip", "live GPT pipeline", True, "skipped")

    # Summary
    failed = [r for r in report.rows if not r.ok]
    out_path = (
        __import__("pathlib").Path(__file__).resolve().parents[2]
        / "docs"
        / f"QA-RUN-API-{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.json"
    )
    out_path.write_text(
        json.dumps(
            {
                "started": report.started,
                "qa_smoke_id": report.qa_smoke_id,
                "qa_full_id": report.qa_full_id,
                "passed": sum(1 for r in report.rows if r.ok),
                "failed": len(failed),
                "rows": [r.__dict__ for r in report.rows],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"\n==> JSON: {out_path}")
    print(f"==> {len(report.rows) - len(failed)}/{len(report.rows)} passed, {len(failed)} failed")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  - {r.check_id} {r.name}: {r.detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
