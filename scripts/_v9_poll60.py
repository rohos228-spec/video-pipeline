"""Poll #60 until scene agents done/failed."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "state.db"
SD = ROOT / "data/videos/spesivcevy-chrono-dyn/scene_design"


def snapshot() -> str:
    con = sqlite3.connect(str(DB))
    status = con.execute("SELECT status FROM projects WHERE id=60").fetchone()[0]
    meta = json.loads(con.execute("SELECT meta FROM projects WHERE id=60").fetchone()[0] or "{}")
    con.close()
    sd = meta.get("scene_design") or {}
    agents = sd.get("agents") or {}
    ag = {k: (v.get("status") if isinstance(v, dict) else v) for k, v in agents.items()}
    files = [p.name for p in SD.glob("*.json") if p.is_file()]
    err = (sd.get("error") or "")[:180]
    return f"status={status} sd={sd.get('status')} agents={ag} files={files} err={err}"


def main() -> None:
    print(time.strftime("%H:%M:%S"), snapshot(), flush=True)
    deadline = time.time() + 3600
    last = ""
    while time.time() < deadline:
        time.sleep(20)
        cur = snapshot()
        if cur != last:
            print(time.strftime("%H:%M:%S"), cur, flush=True)
            last = cur
        con = sqlite3.connect(str(DB))
        status = con.execute("SELECT status FROM projects WHERE id=60").fetchone()[0]
        meta = json.loads(
            con.execute("SELECT meta FROM projects WHERE id=60").fetchone()[0] or "{}"
        )
        con.close()
        sd = meta.get("scene_design") or {}
        agents = sd.get("agents") or {}
        action_ok = isinstance(agents.get("action"), dict) and agents["action"].get("status") == "done"
        camera_ok = isinstance(agents.get("camera"), dict) and agents["camera"].get("status") == "done"
        if status in ("scene_agents_ready", "scene_design_ready", "paused") and (
            action_ok and camera_ok
        ):
            print("READY", flush=True)
            return
        if status == "paused" or sd.get("status") == "failed":
            print("FAILED", flush=True)
            return
        if action_ok and not camera_ok and status == "scene_agents_ready":
            # action done but camera missing — still need sd_cam
            print("ACTION_DONE_NEED_CAMERA", flush=True)
            return
    print("TIMEOUT", flush=True)


if __name__ == "__main__":
    main()
