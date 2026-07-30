import json
import sqlite3
import time
import urllib.request
from pathlib import Path

ROOT = Path("data/videos/testovyy-trukraym")


def snap():
    p = json.load(
        urllib.request.urlopen("http://127.0.0.1:8765/api/projects/50", timeout=30)
    )
    conn = sqlite3.connect("data/state.db")
    arts = dict(
        conn.execute(
            "select kind, count(*) from artifacts where project_id=50 group by kind"
        ).fetchall()
    )
    png = len(list(ROOT.rglob("*.png")))
    mp4 = len(list(ROOT.rglob("*.mp4")))
    return p["status"], arts, png, mp4


if __name__ == "__main__":
    import sys

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    delay = int(sys.argv[2]) if len(sys.argv) > 2 else 15
    last = None
    for i in range(n):
        st, arts, png, mp4 = snap()
        print(i, st, "arts", arts, "png", png, "mp4", mp4)
        if last and st != last and not st.endswith("ing") and "generating" not in st:
            # ready plateau
            if i > 0:
                pass
        last = st
        if st in ("assembled", "published", "failed", "paused"):
            break
        time.sleep(delay)
