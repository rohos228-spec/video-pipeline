"""Sidecar Место vs montage VO parent — what the pictures actually locked to."""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")
from app.services.montage_board import _shot_kind_payload
from app.services.montage_scene_editor import scene_group
from app.services.vo_shot_expand import is_coverage_leftover

SCENE_DIR = Path("data/videos/tkach/scenes")
PLACE_RE = re.compile(
    r"(?:Место и время|Место)\s*:\s*([^;\n]+)",
    re.I,
)
PRESERVE_RE = re.compile(r"Preserve:\s*([^,\n]+)", re.I)


def sidecar_place(n: int) -> str:
    files = sorted(SCENE_DIR.glob(f"frame_{n:03d}_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        prompt = str(data.get("prompt") or "")
        m = PLACE_RE.search(prompt)
        if m:
            return m.group(1).strip().rstrip(".")
        m = PRESERVE_RE.search(prompt)
        if m:
            return m.group(1).strip()
    return ""


con = sqlite3.connect("data/videos/tkach/project.db")
frames = []
for number, uuid, attrs in con.execute(
    "select number, uuid, attrs from frames order by number"
):
    frames.append(
        SimpleNamespace(
            number=number,
            uuid=uuid,
            attrs=json.loads(attrs) if attrs else {},
        )
    )
live = [f for f in frames if not is_coverage_leftover(f)]

seen = set()
mixed = 0
child_wrong_still = 0
print("VO groups: sidecar places vs montage parent\n")
for fr in live:
    p, mem = scene_group(frames, fr)
    if p.number in seen:
        continue
    seen.add(p.number)
    members = [m for m in mem if not is_coverage_leftover(m)]
    places = []
    rows = []
    for m in members:
        pl = sidecar_place(m.number) or "?"
        if pl not in places:
            places.append(pl)
        kind, pn, _ = _shot_kind_payload(m, frames)
        rows.append((m.number, kind, pn, pl))
    flag = " MIX-SCENES" if len([x for x in places if x != "?"]) > 1 else ""
    if flag:
        mixed += 1
    nums = [m.number for m in members]
    print(f"VO#{p.number} {nums[0]}-{nums[-1]} ({len(nums)}) sidecar={places}{flag}")
    if flag:
        for n, kind, pn, pl in rows:
            print(f"    #{n} {kind:6} parent={pn}  {pl}")
        # children whose sidecar place != parent's sidecar place
        parent_pl = sidecar_place(p.number)
        for n, kind, pn, pl in rows:
            if kind == "child" and parent_pl and pl not in ("?", parent_pl) and pl.casefold() != parent_pl.casefold():
                child_wrong_still += 1

print(f"\nmixed VO groups (sidecar): {mixed}")
print(f"children locked to other place than montage parent: {child_wrong_still}")

# adjacent frames: sidecar place change vs shot_kind
print("\nplace-change in sidecar vs chip:")
prev = None
new_place_child = 0
same_place_parent = 0
for fr in live:
    pl = sidecar_place(fr.number)
    if not pl:
        continue
    kind, pn, _ = _shot_kind_payload(fr, frames)
    if prev:
        prev_pl, prev_kind, prev_n = prev
        if pl.casefold() != prev_pl.casefold() and kind == "child":
            new_place_child += 1
            if new_place_child <= 25:
                print(f"  NEW PLACE still child #{fr.number} {pl!r} chip→{pn} after #{prev_n} {prev_pl!r}")
        if pl.casefold() == prev_pl.casefold() and kind == "parent":
            same_place_parent += 1
            if same_place_parent <= 25:
                print(f"  SAME PLACE new parent #{fr.number} {pl!r} after #{prev_n}")
    prev = (pl, kind, fr.number)
print(f"new-place-is-child: {new_place_child}")
print(f"same-place-is-parent: {same_place_parent}")
