"""Проверка #50: последовательность нод, файлы API, логи (без Run audio/music)."""
from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8765"
PID = 50
SKIP_RUN = {"audio", "music", "n_audio", "n_music"}

# Ожидаемый порядок пайплайна (step / node type)
PIPELINE_ORDER = [
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


def get(path: str, timeout: float = 30):
    with urllib.request.urlopen(BASE + path, timeout=timeout) as r:
        return r.status, r.headers, r.read()


def get_json(path: str):
    _, _, body = get(path)
    return json.loads(body.decode("utf-8"))


def head_or_get_ok(url: str) -> tuple[int, int]:
    """Return (status, nbytes)."""
    if url.startswith("/"):
        url = BASE + url
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read(64)
            # try Content-Length
            cl = r.headers.get("Content-Length")
            n = int(cl) if cl and cl.isdigit() else len(data)
            # if small first chunk, still count as ok if status 200
            if cl and cl.isdigit():
                n = int(cl)
            else:
                rest = r.read()
                n = len(data) + len(rest)
            return r.status, n
    except urllib.error.HTTPError as e:
        return e.code, 0
    except Exception as e:  # noqa: BLE001
        print("  GET ERR", url[:80], type(e).__name__, e)
        return 0, 0


def main() -> None:
    ver = get_json("/api/studio-version")
    print(
        "=== STUDIO ===",
        f"build={ver.get('build')} backend={ver.get('backend_git')} "
        f"ui_stale={ver.get('ui_stale')} pipeline_ok={ver.get('pipeline_ok')}",
    )

    p = get_json(f"/api/projects/{PID}")
    print(
        "=== PROJECT ===",
        p.get("status"),
        p.get("slug"),
        "auto_mode=",
        p.get("auto_mode"),
        "hero_mode=",
        p.get("hero_mode"),
    )

    frames = get_json(f"/api/projects/{PID}/frames")
    print(
        "=== FRAMES ===",
        len(frames),
        "img_pr",
        sum(1 for f in frames if (f.get("image_prompt") or "").strip()),
        "anim_pr",
        sum(1 for f in frames if (f.get("animation_prompt") or "").strip()),
        "voiceover",
        sum(1 for f in frames if (f.get("voiceover_text") or "").strip()),
    )

    # Node runs order by finished_at
    db = sqlite3.connect(ROOT / "data" / "state.db")
    db.row_factory = sqlite3.Row
    wr = db.execute(
        "SELECT id, status FROM workflow_runs WHERE project_id=? ORDER BY id DESC LIMIT 1",
        (PID,),
    ).fetchone()
    print("=== WORKFLOW_RUN ===", dict(wr) if wr else None)
    rows = db.execute(
        """
        SELECT nr.node_key, nr.node_type, nr.status, nr.started_at, nr.finished_at, nr.error
        FROM node_runs nr
        WHERE nr.workflow_run_id=?
        ORDER BY COALESCE(nr.finished_at, nr.started_at, nr.created_at)
        """,
        (wr["id"],),
    ).fetchall()
    print("=== NODE_RUNS by time ===", Counter(r["status"] for r in rows))
    done_seq = [r for r in rows if r["status"] == "done"]
    for r in done_seq:
        print(
            f"  {r['finished_at'] or r['started_at']}  {r['status']:8}  "
            f"{r['node_type']:20}  {r['node_key']}"
        )
    failed = [r for r in rows if r["status"] == "failed"]
    print("FAILED", len(failed))
    for r in failed:
        print(" ", r["node_key"], r["error"])

    # Graph edges — snapshot из WorkflowRun (схема workflows разная по версиям)
    snap = db.execute(
        "SELECT nodes_snapshot, edges_snapshot FROM workflow_runs WHERE id=?",
        (wr["id"],),
    ).fetchone()
    nodes = json.loads(snap["nodes_snapshot"] or "[]")
    edges = json.loads(snap["edges_snapshot"] or "[]")

    by_id = {n["id"]: n for n in nodes}
    print("=== GRAPH === nodes", len(nodes), "edges", len(edges))

    # Main chain: find first plan → follow after/pass edges
    def edge_kind(e):
        d = e.get("data") or {}
        return str(d.get("kind") or e.get("kind") or "after")

    outs: dict[str, list[tuple[str, str]]] = {}
    for e in edges:
        s, t = e.get("source"), e.get("target")
        if s and t:
            outs.setdefault(s, []).append((t, edge_kind(e)))

    plan = next((n["id"] for n in nodes if n.get("type") == "plan"), None)
    chain = []
    cur = plan
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        typ = (by_id.get(cur) or {}).get("type")
        chain.append((cur, typ))
        nxt = None
        for t, k in outs.get(cur, []):
            if k in ("after", "pass", "gate", ""):
                # prefer pipeline types
                tt = (by_id.get(t) or {}).get("type")
                if tt in PIPELINE_ORDER or tt in ("excel_gpt", "hitl_gate", "items"):
                    nxt = t
                    break
        if nxt is None:
            for t, k in outs.get(cur, []):
                if k in ("after", "pass", "gate", ""):
                    nxt = t
                    break
        cur = nxt
    print("=== MAIN CHAIN (after/pass) ===")
    for i, (k, t) in enumerate(chain, 1):
        st = next((r["status"] for r in rows if r["node_key"] == k), "?")
        print(f"  {i:02d}. {t:20} {k:40} {st}")

    # Artifacts DB + HTTP
    arts = db.execute(
        "SELECT id, kind, uuid, path, frame_id FROM artifacts WHERE project_id=? ORDER BY kind, id",
        (PID,),
    ).fetchall()
    print("=== ARTIFACTS DB ===", Counter(a["kind"] for a in arts))
    print("=== ARTIFACT HTTP ===")
    bad = []
    for a in arts:
        url = f"/api/artifacts/{a['uuid']}/file"
        status, n = head_or_get_ok(url)
        ok = status == 200 and n > 0
        mark = "OK" if ok else "FAIL"
        print(f"  {mark} {status} {n:10}  {a['kind']:16} {Path(a['path'] or '').name}")
        if not ok:
            bad.append((a["kind"], a["uuid"], status, n))

    # Project assets API
    try:
        assets = get_json(f"/api/projects/{PID}/assets")
    except Exception as e:  # noqa: BLE001
        print("assets ERR", e)
        assets = []
    print("=== ASSETS API ===", len(assets))
    asset_bad = []
    checked = 0
    for a in assets:
        prev = a.get("preview_url") or ""
        if not prev:
            continue
        # skip huge? still check
        status, n = head_or_get_ok(prev if prev.startswith("http") else prev)
        checked += 1
        if status != 200 or n <= 0:
            asset_bad.append((a.get("kind"), prev[:60], status, n))
            print("  FAIL asset", a.get("kind"), status, n, prev[:70])
        if checked >= 40:
            break
    print(f"  checked {checked} preview urls, bad {len(asset_bad)}")

    # xlsx download
    for nk in ("n_plan", "n_split", None):
        q = f"?node_key={nk}" if nk else ""
        path = f"/api/projects/{PID}/xlsx{q}"
        try:
            st, _, body = get(path)
            print(f"  xlsx {nk or 'project'} status={st} bytes={len(body)}")
        except Exception as e:  # noqa: BLE001
            print(f"  xlsx {nk} ERR", e)

    # Disk parity
    base = ROOT / "data" / "videos" / "testovyy-trukraym"
    disk = {
        "scenes": len(list((base / "scenes").glob("*.png"))),
        "videos": len(list((base / "videos").glob("*.mp4"))),
        "heroes": len(list((base / "characters").glob("*.png"))),
        "final": (base / "final" / "testovyy-trukraym.mp4").is_file(),
        "xlsx": (base / "project.xlsx").is_file(),
        "voiceover": (base / "voiceover.txt").is_file(),
    }
    print("=== DISK ===", disk)

    # Recent log errors for #50
    logs = sorted((ROOT / "data").glob("backend-*.log"), key=lambda p: p.stat().st_mtime)
    log = logs[-1] if logs else ROOT / "data" / "backend.log"
    print("=== LOG ===", log.name)
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    recent = text[-400:]
    err50 = [
        ln
        for ln in recent
        if ("#50" in ln or "testovyy-trukraym" in ln)
        and ("ERROR" in ln or "WARNING" in ln or "Traceback" in ln)
    ]
    print(f"  recent lines with #50 ERROR/WARN: {len(err50)}")
    for ln in err50[-12:]:
        print(" ", ln[-200:])

    print("\n=== SUMMARY ===")
    print("status", p.get("status"))
    print("artifact_http_fail", len(bad))
    print("asset_http_fail", len(asset_bad))
    print("node_failed", len(failed))
    print("pipeline_disk_ok", disk["scenes"] == 9 and disk["videos"] == 9 and disk["final"])
    print("SKIP_RUN", sorted(SKIP_RUN))


if __name__ == "__main__":
    main()
