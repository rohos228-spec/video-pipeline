import sqlite3
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"
st = Path("data/videos/testovyy-trukraym/storage")
files = [f for f in st.rglob("*") if f.is_file()] if st.exists() else []
print("storage files", len(files))
ok = fail = 0
for f in files[:15]:
    url = BASE + "/api/files?path=" + urllib.parse.quote(str(f.resolve()))
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            n = r.headers.get("Content-Length") or "?"
            print("OK", r.status, f.name, n)
            ok += 1
    except Exception as e:  # noqa: BLE001
        print("FAIL", f.name, e)
        fail += 1

need = [
    "plan",
    "script",
    "split",
    "hero",
    "image_prompts",
    "images",
    "animation_prompts",
    "videos",
    "audio",
    "music",
    "assemble",
]
db = sqlite3.connect("data/state.db")
done = {
    r[0]
    for r in db.execute(
        "select nr.node_type from node_runs nr "
        "join workflow_runs wr on wr.id=nr.workflow_run_id "
        "where wr.project_id=50 and nr.status='done'"
    )
}
print("canonical", [(s, s in done) for s in need])
print("all_core", all(s in done for s in need))
print("storage_http", ok, "fail", fail)
stamp = Path("data/videos/testovyy-trukraym/final/MONTAGE_STAMP.txt")
print(stamp.read_text(encoding="utf-8")[:280] if stamp.exists() else "no stamp")
