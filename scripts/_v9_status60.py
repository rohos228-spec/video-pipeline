"""Status dump for project #60."""
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
    print("project:", con.execute("SELECT id,slug,status FROM projects WHERE id=60").fetchone())
    cols = [r[1] for r in con.execute("PRAGMA table_info(projects)").fetchall()]
    print("project cols sample:", [c for c in cols if "status" in c or "step" in c or "pause" in c])
    # dump a few useful columns if present
    want = [c for c in cols if c in ("status", "current_step", "pipeline_status", "error_text", "paused_at")]
    if want:
        q = "SELECT " + ",".join(want) + " FROM projects WHERE id=60"
        print("fields:", dict(zip(want, con.execute(q).fetchone())))
    con.close()

    with urllib.request.urlopen(f"{BASE}/api/projects/60", timeout=30) as resp:
        p = json.loads(resp.read().decode("utf-8"))
    print("api status:", p.get("status"))
    steps = p.get("steps") or p.get("pipeline") or []
    if isinstance(steps, list):
        for s in steps[:40]:
            if isinstance(s, dict):
                code = s.get("code") or s.get("step_code") or s.get("id")
                if code and ("sd_" in str(code) or "scene" in str(code)):
                    print(" api step:", code, s.get("status"), str(s.get("error_text") or "")[:120])
    elif isinstance(steps, dict):
        for k, v in steps.items():
            if "sd_" in k or "scene" in k:
                print(" api step:", k, v)

    sd = ROOT / "data/videos/spesivcevy-chrono-dyn/scene_design"
    print("files:", sorted(x.name for x in sd.glob("*") if x.is_file()))
    meta = sd / "scene_design_meta.json"
    if meta.is_file():
        m = json.loads(meta.read_text(encoding="utf-8"))
        print("meta:", m.get("status"), m.get("agents"))


if __name__ == "__main__":
    main()
