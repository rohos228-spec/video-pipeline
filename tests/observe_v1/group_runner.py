"""Existing synthetic HTTP answers relocated from diag_v2; no runtime wrapper."""
from __future__ import annotations
import asyncio,json,os,re,sqlite3
from collections import Counter
from http.server import BaseHTTPRequestHandler
from pathlib import Path
import httpx

ROOT = Path("/results")

DATA = ROOT / "free-run-data"

SEED = Path("/srv/seed")

SLUG = "diagnostic-synthetic-scene-2026-09-24"

DB = DATA / "videos" / SLUG / "project.db"

NODES = (
    "n_excel_gpt_fw_script",
    "n_excel_gpt_fw_check_script",
    "n_excel_gpt_fw_action",
    "n_excel_gpt_fw_shots",
    "n_excel_gpt_fw_qc",
    "n_excel_gpt_fw_report",
)

MODE = os.environ.get("DIAGNOSTIC_SCENARIO", "group")

def frames() -> list[tuple[str, int, str, dict]]:
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT uuid,number,voiceover_text,attrs FROM frames WHERE project_id=2 ORDER BY number"
        ).fetchall()
    return [(uid, number, vo or "", json.loads(attrs or "{}")) for uid, number, vo, attrs in rows]

def split_vo(vo: str) -> list[str]:
    words = vo.split()
    parts: list[str] = []
    part = ""
    for word in words:
        if part and len(part) + 1 + len(word) > 60:
            parts.append(part)
            part = word
        else:
            part = f"{part} {word}".strip()
    if part:
        parts.append(part)
    if len(parts) > 1 and len(parts[-1]) < 13:
        parts[-2] += " " + parts.pop()
    return parts

def synthetic_answer(node: str, attempt: int) -> str:
    if MODE == "malformed" and node == NODES[0] and attempt == 1:
        return '{"ops": }'
    if node == NODES[1]:
        return (
            "# ОТЧЁТ ПРОВЕРКИ\nverdict: pass\nmode: fix\n"
            "source_prompts: n_excel_gpt_fw_script\n\n## summary\n"
            "Синтетическая проверка формата битов: pass.\n"
        )
    ops = []
    for uid, number, vo, attrs in frames():
        if node == NODES[0]:
            clauses = [x.strip() for x in re.split(r"[.!?;…]+", vo) if x.strip()]
            bits = [
                {"порядок": i, "глагол": "говорит",
                 "изменение": "смысл из закадра", "якорь": clause[:80]}
                for i, clause in enumerate(clauses, 1)
            ]
            fields = {"биты": bits}
        elif node == NODES[2]:
            steps = ["вошёл в комнату", "подошёл к столу", "открыл папку",
                     "положил лист", "указал на запись", "закрыл папку"]
            chunks = split_vo(vo)
            action_steps = [steps[i % len(steps)] for i in range(len(chunks))]
            fields = {"главное_действие": (
                f"1. комната — {' → '.join(action_steps)}\n({vo})"
            )}
        elif node in (NODES[3], NODES[4]):
            chunks = split_vo(vo)
            actions = ["вошёл в комнату", "подошёл к столу", "открыл папку",
                       "положил лист", "указал на запись", "закрыл папку"]
            objects = ["место", "тело", "предмет", "предмет", "предмет", "предмет"]
            shots = [
                {"id": f"{number}-S1-K{i+1}",
                 "parent_id": None if i == 0 else f"{number}-S1-K1",
                 "порядок": i + 1, "сцена": 1, "место": "комната",
                 "действие": actions[i % len(actions)],
                 "объект": objects[i % len(objects)], "закадр": chunk}
                for i, chunk in enumerate(chunks)
            ]
            if node == NODES[4]:
                # A full, identical list is an explicit synthetic QC write.
                # Record this intervention; do not claim it tested model quality.
                existing = attrs.get("кадры")
                if isinstance(existing, list) and existing:
                    shots = existing
            fields = {"кадры": shots}
        else:
            raise ValueError(f"No fixture contract for {node}")
        ops.append({"frame_uuid": uid, "fields": fields})
    return json.dumps({"ops": ops}, ensure_ascii=False)

class FixtureHandler(BaseHTTPRequestHandler):
    counts: Counter = Counter()
    response_ids: list[str] = []

    def do_GET(self):
        # The fixture has no recoverable model answer beyond its completed SSE.
        # A truncated JSON text triggers the application's normal retrieve path.
        self.send_error(404, "No separate recovery payload in this fixture")

    def do_POST(self):
        if self.path != "/codex/v1/responses":
            self.send_error(404)
            return
        node = self.headers.get("x-diagnostic-node", "")
        if node not in NODES[:-1]:
            self.send_error(422, "Node outside diagnostic group")
            return
        self.rfile.read(int(self.headers.get("content-length", "0")))
        type(self).counts[node] += 1
        response_id = f"resp_{MODE}_{node}_{type(self).counts[node]}"
        type(self).response_ids.append(response_id)
        answer = synthetic_answer(node, type(self).counts[node])
        response = {
            "id": response_id,
            "status": "completed",
            "output": [{"content": [{"type": "output_text", "text": answer}]}],
            "usage": {"input_tokens": 0, "output_tokens": 0, "fixture": True},
        }
        events = [
            {"type": "response.created", "response": {"id": response_id}},
            {"type": "response.output_text.delta", "delta": answer},
            {"type": "response.output_text.done", "text": answer},
            {"type": "response.completed", "response": response},
        ]
        body = "".join(
            "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"
            for event in events
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        return

def snapshot() -> dict:
    with sqlite3.connect(DB) as conn:
        rows = conn.execute(
            "SELECT node_key,status,error FROM node_runs WHERE node_key IN "
            "(" + ",".join("?" for _ in NODES) + ") ORDER BY node_key", NODES
        ).fetchall()
        frame_rows = conn.execute(
            "SELECT number,voiceover_text,attrs FROM frames WHERE project_id=2 ORDER BY number"
        ).fetchall()
        project_row = conn.execute("SELECT status,meta FROM projects WHERE id=2").fetchone()
    nodes = {key: {"status": status, "error_type": bool(error)}
             for key, status, error in rows}
    details = []
    for number, vo, attrs_json in frame_rows:
        attrs = json.loads(attrs_json or "{}")
        shots = attrs.get("кадры") or []
        details.append({
            "number": number,
            "voiceover_chars": len(vo or ""),
            "bits_count": len(attrs.get("биты") or []),
            "action_chars": len(attrs.get("главное_действие") or ""),
            "shot_count": len(shots),
            "shot_ids": [str(s.get("id")) for s in shots if isinstance(s, dict)],
            "shot_voiceover_matches": " ".join(
                str(s.get("закадр") or "") for s in shots if isinstance(s, dict)
            ).split() == (vo or "").split() if shots else None,
        })
    meta = json.loads(project_row[1] or "{}") if project_row else {}
    report = meta.get("shots_report_path")
    return {
        "nodes": nodes,
        "project_status": project_row[0] if project_row else None,
        "frames": details,
        "report_path": report,
    }

async def wait_api() -> None:
    async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
        for _ in range(120):
            try:
                response = await client.get("http://127.0.0.1:18765/api/projects/2")
                if response.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(1)
    raise RuntimeError("Application API did not start")
