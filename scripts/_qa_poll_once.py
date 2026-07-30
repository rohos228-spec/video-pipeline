import json
import sqlite3
import urllib.request
from pathlib import Path

p = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/projects/50", timeout=30))
print("status", p["status"], "script", len(p.get("script_text") or ""), "plan", len(p.get("general_plan") or ""))

logs = sorted(Path("data").glob("backend*.log"), key=lambda x: x.stat().st_mtime)
log = logs[-1]
print("log", log.name, "mtime", log.stat().st_mtime)
lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
keys = ("script", "testovyy", "project 50", "make_script", "ERROR", "Error", "Traceback", "gpt", "GPT")
for ln in lines[-100:]:
    low = ln.lower()
    if any(k.lower() in low for k in keys):
        print(ln[-240:])

conn = sqlite3.connect("data/state.db")
conn.row_factory = sqlite3.Row
row = conn.execute(
    "select status,progress,progress_text,error,attempts from node_runs where workflow_run_id=50 and node_key='n_script'"
).fetchone()
print("node_script", dict(row) if row else None)
wr = conn.execute("select id,status,updated_at from workflow_runs where project_id=50").fetchone()
print("wr", dict(wr) if wr else None)
hitl = conn.execute(
    "select id,kind,decision from hitl_requests where project_id=50 order by id desc limit 5"
).fetchall()
print("hitl", [dict(r) for r in hitl])
