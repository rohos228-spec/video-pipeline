"""One-shot: tkach montage parent/child vs scene vs place. Delete after."""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from types import SimpleNamespace

sys.path.insert(0, ".")
from app.services.montage_board import _shot_kind_payload
from app.services.montage_scene_editor import frame_place, scene_group
from app.services.vo_shot_expand import (
    _cs,
    coverage_parent_shot_id,
    coverage_shot_id,
    find_coverage_parent_frame,
    frame_scene_number,
    is_coverage_leftover,
    uses_parent_still,
)

con = sqlite3.connect("data/videos/tkach/project.db")
frames = []
for id_, number, uuid, vo, status, attrs in con.execute(
    "select id, number, uuid, voiceover_text, status, attrs from frames order by number"
):
    a = json.loads(attrs) if attrs else {}
    frames.append(
        SimpleNamespace(
            id=id_,
            number=number,
            uuid=uuid,
            voiceover_text=vo or "",
            status=status,
            attrs=a,
        )
    )

live = [f for f in frames if not is_coverage_leftover(f)]
print("live", len(live), "all", len(frames))

print("\n=== frames 1-50 ===")
hdr = (
    f"{'n':>4} {'role':12} {'ck':6} {'place':28} {'sc':3} "
    f"{'sid':16} {'pid':14} {'voP':3} {'shotK':6} {'shotP':5} {'still':6}"
)
print(hdr)
for fr in live:
    if fr.number > 50:
        break
    cs = _cs(fr)
    role = str(cs.get("role") or "")[:12]
    ck = str(cs.get("coverage_kind") or "-")[:6]
    place = (frame_place(fr) or "")[:28]
    sc = frame_scene_number(fr)
    sid = coverage_shot_id(fr)[:16]
    pid = (coverage_parent_shot_id(fr) or "")[:14]
    p, _mem = scene_group(frames, fr)
    kind, pn, _sid = _shot_kind_payload(fr, frames)
    still = find_coverage_parent_frame(frames, fr) if uses_parent_still(fr) else None
    still_n = still.number if still is not None else None
    print(
        f"{fr.number:4} {role:12} {ck:6} {place:28} {str(sc):3} "
        f"{sid:16} {pid:14} {p.number:3} {kind:6} {str(pn):5} {str(still_n):6}"
    )

ranges: list[dict] = []
for fr in live:
    p, _mem = scene_group(frames, fr)
    scene = int(p.number)
    last = ranges[-1] if ranges else None
    child_of_last = bool(last and scene == last["scene"] and fr.number != scene)
    if last and (last["scene"] == scene or child_of_last):
        last["frames"].append(fr)
    else:
        ranges.append({"key": fr.number, "scene": scene, "frames": [fr]})

print("\nscene blocks", len(ranges))
parent_to_blocks: dict[int, set[int]] = defaultdict(set)
parent_to_frames: dict[int, list] = defaultdict(list)
for ri, r in enumerate(ranges):
    for fr in r["frames"]:
        kind, pn, _sid = _shot_kind_payload(fr, frames)
        if kind == "child" and pn:
            parent_to_blocks[pn].add(ri)
            parent_to_frames[pn].append((fr.number, ri, r["key"], r["scene"]))

split_parents = [(p, sorted(blocks)) for p, blocks in parent_to_blocks.items() if len(blocks) > 1]
print("parents whose children sit in 2+ scene blocks:", len(split_parents))
for p, blocks in split_parents[:30]:
    print(f"  parent #{p} blocks={blocks} kids={parent_to_frames[p]}")

print("\n=== adjacent: place change vs parent/child ===")
prev = None
place_change_wrong = []
for fr in live:
    pl = frame_place(fr)
    kind, pn, _sid = _shot_kind_payload(fr, frames)
    if prev is not None:
        prev_pl, prev_kind, prev_fr = prev
        if pl and prev_pl and pl.casefold() != prev_pl.casefold():
            if kind == "child":
                place_change_wrong.append(
                    ("new-place-is-child", fr.number, pl, pn, prev_fr.number, prev_pl)
                )
        if pl and prev_pl and pl.casefold() == prev_pl.casefold():
            if kind == "parent":
                place_change_wrong.append(
                    ("same-place-is-parent", fr.number, pl, None, prev_fr.number, prev_pl)
                )
    prev = (pl, kind, fr)
print("place vs kind mismatches", len(place_change_wrong))
for row in place_change_wrong:
    print(" ", row)

print("\n=== shot_parent != vo_scene ===")
mismatch = 0
for fr in live:
    kind, pn, _sid = _shot_kind_payload(fr, frames)
    p, _mem = scene_group(frames, fr)
    if kind == "child" and pn and int(pn) != int(p.number):
        mismatch += 1
        if mismatch <= 25:
            print(
                f"  #{fr.number} shot_parent={pn} vo_scene={p.number} "
                f"place={frame_place(fr)!r} sc={frame_scene_number(fr)} "
                f"sid={coverage_shot_id(fr)}"
            )
print("total chip-parent != scene-block", mismatch)

print("\ncoverage_kind", Counter(str(_cs(f).get("coverage_kind") or "-") for f in live))
print("role", Counter(str(_cs(f).get("role") or "-") for f in live))

print("\n=== each scene block: unique places + parent chips ===")
for ri, r in enumerate(ranges):
    places = []
    kinds = []
    for fr in r["frames"]:
        pl = frame_place(fr) or "?"
        if pl not in places:
            places.append(pl)
        kind, pn, _sid = _shot_kind_payload(fr, frames)
        kinds.append(f"{fr.number}:{kind}:{pn or '-'}")
    flag = " MIX" if len(places) > 1 else ""
    nums = [fr.number for fr in r["frames"]]
    print(f"  block{ri:02d} VO#{r['scene']} n={nums[0]}-{nums[-1]} ({len(nums)}) places={places}{flag}")
    if len(places) > 1:
        print(f"           {kinds}")
