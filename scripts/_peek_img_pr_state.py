"""Count img_pr targets in tkach project.db."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
db = sqlite3.connect(str(ROOT / "data/videos/tkach/project.db"))
rows = db.execute(
    "select number, uuid, voiceover_text, image_prompt, attrs from frames "
    "where project_id=63 order by number"
).fetchall()


def role(attrs: str) -> str:
    try:
        blob = json.loads(attrs or "{}")
    except json.JSONDecodeError:
        return ""
    return str((blob.get("camera_subdivide") or {}).get("role") or "")


parents = []
shots = []
other = []
for num, uid, vo, prompt, attrs in rows:
    r = role(attrs or "")
    rec = (num, uid, len(vo or ""), len((prompt or "").strip()), r)
    if r == "shot":
        shots.append(rec)
    elif (vo or "").strip():
        parents.append(rec)
    else:
        other.append(rec)

filled = [p for p in parents if p[3] >= 200]
empty = [p for p in parents if p[3] == 0]
short = [p for p in parents if 0 < p[3] < 200]
print("frames_total", len(rows))
print("vo_parents", len(parents))
print("shot_children", len(shots))
print("no_vo_other", len(other))
print("parents_filled_ge200", len(filled))
print("parents_empty", len(empty))
print("parents_short", len(short))
print("parent_numbers", [p[0] for p in parents])
ckpt_path = ROOT / "data/videos/tkach/tmp_gpt/img_pr_checkpoint.json"
if ckpt_path.is_file():
    ckpt = json.loads(ckpt_path.read_text(encoding="utf-8"))
    print("ckpt_ops", len(ckpt.get("ops") or []), "ckpt_done", len(ckpt.get("done_uuids") or []))
