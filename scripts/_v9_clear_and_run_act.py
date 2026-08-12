"""Resume #60 and start sd_act via Studio API."""
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


def http_json(method: str, path: str, body: bytes | None = b"{}") -> dict:
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=body,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} {path}: {raw[:800]}")
        raise
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        print(raw[:800])
        return {}


def main() -> int:
    con = sqlite3.connect(str(DB))
    print("before:", con.execute("SELECT id,slug,status FROM projects WHERE id=60").fetchone())
    con.close()

    r = http_json("POST", "/api/projects/60/resume", b"{}")
    print("resume status:", r.get("status") or r.get("project", {}).get("status"), list(r)[:8])

    r2 = http_json("POST", "/api/projects/60/steps/sd_act/run", b"{}")
    print(
        "sd_act run status:",
        r2.get("status"),
        "keys:",
        list(r2)[:12],
    )

    con = sqlite3.connect(str(DB))
    print("after:", con.execute("SELECT id,slug,status FROM projects WHERE id=60").fetchone())
    con.close()

    for meta in (
        ROOT / "data/videos/spesivcevy-chrono-dyn/scene_design/scene_design_meta.json",
        ROOT
        / "data/videos/spesivcevy/storage/n_storage_1786293092094/scene_design_meta.json",
    ):
        if meta.is_file():
            m = json.loads(meta.read_text(encoding="utf-8"))
            print("meta@", meta.parent.name, m.get("status"), m.get("agents"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
