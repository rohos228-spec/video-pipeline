"""Force #60 into scene_designing and start sd_act / scene_d cleanly."""
from __future__ import annotations

import json
import sqlite3
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "state.db"
BASE = "http://127.0.0.1:8765"
SD = ROOT / "data/videos/spesivcevy-chrono-dyn/scene_design"


def http(method: str, path: str, body: bytes | None = b"{}") -> tuple[int, dict]:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            code = resp.status
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} {path}: {raw[:1000]}")
        return e.code, {}
    try:
        return code, json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        print(raw[:500])
        return code, {}


def patch_db() -> None:
    con = sqlite3.connect(str(DB))
    row = con.execute("SELECT status, meta FROM projects WHERE id=60").fetchone()
    meta = json.loads(row[1] or "{}")
    print("before status:", row[0])
    print(
        "frames:",
        con.execute(
            "SELECT count(*), sum(CASE WHEN voiceover_text IS NOT NULL "
            "AND trim(voiceover_text)!='' THEN 1 ELSE 0 END) "
            "FROM frames WHERE project_id=60"
        ).fetchone(),
    )
    # Keep chars/world/style checkpoints; ensure action/camera not done in meta.
    sd = dict(meta.get("scene_design") or {})
    agents = dict(sd.get("agents") or {})
    for drop in ("action", "camera", "assemble"):
        agents.pop(drop, None)
    sd["agents"] = agents
    sd["status"] = "running"
    sd.pop("error", None)
    meta["scene_design"] = sd
    meta.pop("paused_from_status", None)
    # clear failure backoff that may block soft-retry
    sf = dict(meta.get("step_failure") or {})
    if "total_fails" in sf:
        tf = dict(sf["total_fails"])
        tf.pop("scene_designing", None)
        tf.pop("scene_assembling", None)
        sf["total_fails"] = tf
    sf.pop("last_error", None)
    sf.pop("last_error_code", None)
    meta["step_failure"] = sf
    meta["paused_from_status"] = "frames_ready"  # so resume lands correctly if needed

    con.execute(
        "UPDATE projects SET status=?, meta=?, updated_at=datetime('now') WHERE id=60",
        ("frames_ready", json.dumps(meta, ensure_ascii=False)),
    )
    # Fix any stale running node_runs for sd agents
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master").fetchall()]
    if "node_runs" in tables:
        con.execute(
            "UPDATE node_runs SET status='pending', error=NULL, "
            "progress=0, progress_text=NULL, started_at=NULL, finished_at=NULL "
            "WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE project_id=60) "
            "AND (node_type IN ('sd_agent','sd_assemble','scene_design') "
            "OR node_key LIKE '%sd_%')"
        )
        print("reset node_runs for sd_*")
    con.commit()
    print("after status:", con.execute("SELECT status FROM projects WHERE id=60").fetchone()[0])
    con.close()

    # Ensure action.json absent (force regen); keep v8bak
    for name in ("action.json", "camera.json", "scene_design_meta.json"):
        p = SD / name
        if p.exists():
            p.unlink()
            print("removed", p.name)


def main() -> int:
    patch_db()
    code, body = http("POST", "/api/projects/60/steps/sd_act/run", b"{}")
    print("sd_act HTTP", code, "status=", body.get("status"))
    # If still not designing, try scene_d
    if body.get("status") != "scene_designing":
        code2, body2 = http("POST", "/api/projects/60/steps/scene_d/run", b"{}")
        print("scene_d HTTP", code2, "status=", body2.get("status"))
    con = sqlite3.connect(str(DB))
    print("final DB status:", con.execute("SELECT status FROM projects WHERE id=60").fetchone())
    meta = json.loads(con.execute("SELECT meta FROM projects WHERE id=60").fetchone()[0] or "{}")
    print("scene_design meta:", json.dumps(meta.get("scene_design"), ensure_ascii=False)[:500])
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
