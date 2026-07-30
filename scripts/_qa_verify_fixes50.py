"""Verify LIVE QA fixes on project 50 after main update (no audio/music runs)."""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.load(r)


def main() -> None:
    ver = get("/api/studio-version")
    print(
        "studio",
        ver.get("build"),
        "backend",
        ver.get("backend_git"),
        "ui_stale",
        ver.get("ui_stale"),
        "pipeline_ok",
        ver.get("pipeline_ok"),
    )
    p = get("/api/projects/50")
    print("project", p.get("status"), p.get("slug"))
    frames = get("/api/projects/50/frames")
    print(
        "frames",
        len(frames),
        "img_pr",
        sum(1 for f in frames if (f.get("image_prompt") or "").strip()),
        "anim_pr",
        sum(1 for f in frames if (f.get("animation_prompt") or "").strip()),
    )

    db = sqlite3.connect(ROOT / "data" / "state.db")
    rows = db.execute(
        """
        SELECT nr.node_key, nr.node_type, nr.status
        FROM node_runs nr
        JOIN workflow_runs wr ON wr.id = nr.workflow_run_id
        WHERE wr.project_id = 50
        """
    ).fetchall()
    print("node_runs", Counter(r[2] for r in rows))
    print("failed", [r for r in rows if r[2] == "failed"])

    meta_raw = db.execute("SELECT meta FROM projects WHERE id=50").fetchone()[0]
    meta = json.loads(meta_raw) if isinstance(meta_raw, str) else (meta_raw or {})
    print("auto_await", meta.get("auto_await_manual_start"))
    print("active_excel", meta.get("active_excel_gpt_node_key"))
    for k, v in (meta.get("excel_gpt_nodes") or {}).items():
        if v.get("checkMode") or v.get("checkFix"):
            print("check_node", k, "checkMode", v.get("checkMode"), "checkFix", v.get("checkFix"))

    key = "n_excel_gpt_1785060123306"
    try:
        r = get(f"/api/projects/50/gpt-operator/{key}/resolve")
        print(
            "resolve",
            key,
            "checkMode",
            r.get("checkMode"),
            "checkFix",
            r.get("checkFix"),
        )
        last = r.get("lastResult") or r.get("checkResult") or {}
        if isinstance(last, dict):
            print("last verdict", last.get("verdict"), "file", last.get("file") or last.get("rewrite_file"))
    except Exception as e:
        print("resolve ERR", e)

    # disk artifacts (skip judging audio/music quality)
    base = ROOT / "data" / "videos" / "testovyy-trukraym"
    print(
        "disk",
        "scenes",
        len(list((base / "scenes").glob("*.png"))),
        "videos",
        len(list((base / "videos").glob("*.mp4"))),
        "heroes",
        len(list((base / "characters").glob("*.png"))),
        "final",
        (base / "final" / "testovyy-trukraym.mp4").exists(),
    )

    # code contract present
    op = (ROOT / "app" / "services" / "gpt_operator.py").read_text(encoding="utf-8")
    print("D5_contract", "# КОНТРАКТ API" in op)
    planner = (ROOT / "app" / "orchestrator" / "graph" / "planner.py").read_text(encoding="utf-8")
    print("D11_fail_skip", "is_fail_edge_kind" in planner and "не условие первого запуска" in planner)


if __name__ == "__main__":
    main()
