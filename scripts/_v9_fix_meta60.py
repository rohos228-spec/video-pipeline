"""Inspect / patch project #60 meta for scene_design V9 rerun."""
from __future__ import annotations

import json
import sqlite3
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "state.db"
BASE = "http://127.0.0.1:8765"


def main() -> None:
    con = sqlite3.connect(str(DB))
    row = con.execute("SELECT status, meta_json FROM projects WHERE id=60").fetchone()
    print("status:", row[0])
    meta = json.loads(row[1] or "{}")
    keys = sorted(meta.keys())
    print("meta keys:", keys)
    for k in (
        "paused_from_status",
        "scene_design",
        "scene_design_variant",
        "last_error",
        "soft_retry",
        "user_stop",
        "auto_await_manual_start",
    ):
        if k in meta:
            print(f"  {k}:", json.dumps(meta[k], ensure_ascii=False)[:400])
    # node runs
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()]
    print("has node_runs:", "node_runs" in tables or "noderuns" in str(tables).lower())
    for t in tables:
        if "node" in t.lower() or "run" in t.lower():
            print(" table:", t)
    if "node_runs" in tables:
        rows = con.execute(
            "SELECT node_key, status, error_text FROM node_runs WHERE project_id=60 "
            "AND (node_key LIKE '%sd%' OR node_key LIKE '%scene%' OR node_key LIKE '%action%') "
            "ORDER BY id DESC LIMIT 20"
        ).fetchall()
        for r in rows:
            print(" nr:", r)
    con.close()

    with urllib.request.urlopen(f"{BASE}/api/projects/60", timeout=30) as resp:
        p = json.loads(resp.read().decode())
    print("api.status", p.get("status"))
    print("api.meta.scene_design", (p.get("meta") or {}).get("scene_design"))


if __name__ == "__main__":
    main()
