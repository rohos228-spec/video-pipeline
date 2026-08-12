"""Force #60 frames_ready → sd_cam with action checkpoint kept."""
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
    meta = json.loads(con.execute("SELECT meta FROM projects WHERE id=60").fetchone()[0] or "{}")
    sd = dict(meta.get("scene_design") or {})
    agents = dict(sd.get("agents") or {})
    # keep action done; drop camera
    agents.pop("camera", None)
    sd["agents"] = agents
    sd["status"] = "running"
    sd.pop("error", None)
    meta["scene_design"] = sd
    sf = dict(meta.get("step_failure") or {})
    sf["total_fails"] = {}
    sf.pop("last_error", None)
    sf.pop("last_error_code", None)
    meta["step_failure"] = sf
    con.execute(
        "UPDATE projects SET status='frames_ready', meta=?, updated_at=datetime('now') WHERE id=60",
        (json.dumps(meta, ensure_ascii=False),),
    )
    con.execute(
        "UPDATE node_runs SET status='pending', error=NULL, progress=0, "
        "started_at=NULL, finished_at=NULL "
        "WHERE workflow_run_id IN (SELECT id FROM workflow_runs WHERE project_id=60) "
        "AND (node_type IN ('sd_agent','sd_assemble') OR node_key LIKE '%sd_%')"
    )
    con.commit()
    con.close()

    req = urllib.request.Request(
        f"{BASE}/api/projects/60/steps/sd_cam/run",
        data=b"{}",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.loads(resp.read().decode())
    print("sd_cam", body.get("status"))


if __name__ == "__main__":
    main()
