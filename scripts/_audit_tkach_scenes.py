"""Live parent_uuid vs QC Сцена vs chips vs leftover sort_key."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, ".")
from app.services.montage_board import _shot_kind_payload
from app.services.montage_scene_editor import scene_group
from app.services.vo_shot_expand import (
    _cs,
    _explicit_scene_number,
    _frame_place,
    is_coverage_leftover,
    is_shot_child,
    planned_shots_from_attrs,
)

QC = Path("data/videos/tkach/group_script_frames_qc_tables.md")
con = sqlite3.connect("data/videos/tkach/project.db")

qc_scene: dict[int, int] = {}
qc_place: dict[int, str] = {}
in_table = False
for line in QC.read_text(encoding="utf-8").splitlines():
    if line.startswith("| № | Сцена |"):
        in_table = True
        continue
    if not in_table:
        continue
    if not line.startswith("|"):
        break
    parts = [p.strip() for p in line.strip("|").split("|")]
    if len(parts) < 3 or parts[0] in {"---", "№"}:
        continue
    if not parts[0].isdigit():
        continue
    n = int(parts[0])
    if parts[1].isdigit():
        qc_scene[n] = int(parts[1])
        qc_place[n] = parts[2]


def load():
    frames = []
    for row in con.execute(
        "select id, number, uuid, sort_key, attrs from frames order by "
        "coalesce(sort_key, number), number"
    ):
        fid, number, uuid, sort_key, attrs = row
        frames.append(
            SimpleNamespace(
                id=fid,
                number=number,
                uuid=uuid,
                sort_key=sort_key,
                attrs=json.loads(attrs) if attrs else {},
            )
        )
    return frames


frames = load()
live = [f for f in frames if not is_coverage_leftover(f)]
left = [f for f in frames if is_coverage_leftover(f)]
print(f"frames={len(frames)} live={len(live)} leftover={len(left)}")
print(f"live numbers {live[0].number}-{live[-1].number}")
if left:
    print(f"leftover numbers {left[0].number}-{left[-1].number} sort {left[0].sort_key}-{left[-1].sort_key}")
    live_sorts = [f.sort_key for f in live if f.sort_key is not None]
    if live_sorts:
        print(f"live sort {min(live_sorts)}-{max(live_sorts)}")

# leftover interleaved in sort order?
inter = []
prev_live = None
for f in frames:
    if is_coverage_leftover(f):
        if prev_live is not None and prev_live < 190:
            inter.append((prev_live, f.number, f.sort_key))
    else:
        prev_live = f.number
print(f"leftover interleaved after live<190: {len(inter)} e.g. {inter[:8]}")

print("\n=== LIVE groups parent_uuid vs QC scene vs chip ===\n")
seen = set()
span_two_qc = []
chip_vs_vo = []
for fr in live:
    p, mem = scene_group(frames, fr)
    if p.number in seen:
        continue
    seen.add(p.number)
    members = [m for m in mem if not is_coverage_leftover(m)]
    nums = [m.number for m in members]
    qc_set = sorted({qc_scene.get(n) for n in nums if n in qc_scene})
    cs_set = sorted({_explicit_scene_number(m) for m in members})
    roles = []
    for m in members:
        kind, pn, _ = _shot_kind_payload(m, frames)
        vo_p = scene_group(frames, m)[0].number
        if kind == "child" and pn and pn != vo_p:
            chip_vs_vo.append((m.number, "chip", pn, "vo", vo_p))
        roles.append((m.number, kind, pn, vo_p, qc_scene.get(m.number), _explicit_scene_number(m), (_frame_place(m) or "")[:40]))
    flag = ""
    if len([x for x in qc_set if x is not None]) > 1:
        flag = " SPAN-QC-SCENES"
        span_two_qc.append((p.number, nums, qc_set))
    print(f"VO#{p.number} n={nums[0]}-{nums[-1]} ({len(nums)}) qc_scenes={qc_set} cs.сцена={cs_set}{flag}")
    if flag or any(r[1] == "child" and r[2] != p.number for r in roles):
        for n, kind, pn, vo_p, qcs, css, pl in roles:
            print(f"    #{n} {kind:6} chip→{pn} vo→{vo_p} QC={qcs} cs={css} {pl}")

print(f"\nVO groups spanning 2+ QC scenes: {len(span_two_qc)}")
for p, nums, qcs in span_two_qc[:20]:
    print(f"  parent #{p} frames {nums} QC {qcs}")
print(f"chip parent != vo parent: {len(chip_vs_vo)}")
for row in chip_vs_vo[:30]:
    print(" ", row)

# Adjacent QC scene change that is still a child
print("\n=== QC scene change but still child of previous parent ===")
prev = None
bad = 0
for fr in live:
    q = qc_scene.get(fr.number)
    kind, pn, _ = _shot_kind_payload(fr, frames)
    vo_p = scene_group(frames, fr)[0].number
    if prev and q and prev[0] and q != prev[0]:
        if kind == "child" or (vo_p == prev[3] and fr.number != vo_p):
            bad += 1
            if bad <= 40:
                print(
                    f"  #{fr.number} QC {prev[0]}→{q} kind={kind} chip→{pn} vo→{vo_p} "
                    f"(prev #{prev[1]} kind={prev[2]})"
                )
    prev = (q, fr.number, kind, vo_p)
print(f"QC-scene-change-still-child: {bad}")

# New QC scene whose first frame is child
print("\n=== first frame of QC scene is child ===")
first_of = {}
for n, s in qc_scene.items():
    first_of.setdefault(s, n)
n_child_heads = 0
for s, n in sorted(first_of.items()):
    fr = next((f for f in live if f.number == n), None)
    if fr is None:
        continue
    kind, pn, _ = _shot_kind_payload(fr, frames)
    if kind == "child":
        n_child_heads += 1
        if n_child_heads <= 25:
            print(f"  QC scene {s} starts #{n} as CHILD of {pn} place={(_frame_place(fr) or '')[:50]}")
print(f"QC scene starts that are children: {n_child_heads}")

# Same shot_parent on two consecutive vo cards
print("\n=== same chip parent on two consecutive VO cards ===")
cards = []
seen = set()
for fr in live:
    p, mem = scene_group(frames, fr)
    if p.number in seen:
        continue
    seen.add(p.number)
    members = [m for m in mem if not is_coverage_leftover(m)]
    chips = set()
    for m in members:
        kind, pn, _ = _shot_kind_payload(m, frames)
        if kind == "child" and pn:
            chips.add(pn)
        if kind == "parent":
            chips.add(m.number)
    cards.append((p.number, chips, [m.number for m in members]))
for a, b in zip(cards, cards[1:]):
    overlap = (a[1] & b[1]) - {a[0], b[0]}
    # parent of A appearing as chip on B
    if a[0] in b[1] or b[0] in a[1] or overlap:
        print(f"  VO#{a[0]} {a[2]} chips={sorted(a[1])} | VO#{b[0]} {b[2]} chips={sorted(b[1])}")
