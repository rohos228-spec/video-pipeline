"""Dump VO parent кадры[] / action vs QC for mixed groups."""
from __future__ import annotations

import json
import sqlite3
import sys
from types import SimpleNamespace

sys.path.insert(0, ".")
from app.services.montage_scene_editor import scene_group, shot_sequence_text, main_action_text
from app.services.vo_shot_expand import (
    _cs,
    is_coverage_leftover,
    planned_shots_from_attrs,
)

con = sqlite3.connect("data/videos/tkach/project.db")
frames = []
for number, uuid, vo, ip, attrs in con.execute(
    "select number, uuid, voiceover_text, image_prompt, attrs from frames order by number"
):
    a = json.loads(attrs) if attrs else {}
    frames.append(
        SimpleNamespace(
            number=number,
            uuid=uuid,
            voiceover_text=vo or "",
            image_prompt=ip or "",
            attrs=a,
        )
    )

live = [f for f in frames if not is_coverage_leftover(f)]
seen = set()
parents = []
for fr in live:
    p, mem = scene_group(frames, fr)
    if p.number in seen:
        continue
    seen.add(p.number)
    parents.append((p, [m for m in mem if not is_coverage_leftover(m)]))

print("=== VO parents: кадры места / действие / scene_action ===\n")
for p, mem in parents[:12]:
    shots = planned_shots_from_attrs(p)
    seq = shot_sequence_text(p, mem)
    main = (main_action_text(p) or "")[:180]
    places = []
    scenes = []
    for s in shots:
        pl = str(s.get("место") or s.get("place") or "")
        sc = s.get("сцена")
        if pl not in places:
            places.append(pl)
        if sc not in scenes:
            scenes.append(sc)
    print(
        f"VO#{p.number} members={[m.number for m in mem]} "
        f"kadry={len(shots)} unique_места={places} сцены={scenes}"
    )
    print(f"  main={main!r}")
    print(f"  seq={seq[:220]!r}")
    for i, s in enumerate(shots[:10]):
        print(
            f"    [{i}] id={s.get('id')} sc={s.get('сцена')} "
            f"place={s.get('место') or s.get('place')!r} "
            f"parent={s.get('parent_id')!r} "
            f"act={(str(s.get('действие') or s.get('action') or '')[:50])!r}"
        )
    print()

print("=== per-child cs.место vs кадры[0].место vs prompt head (7-17) ===")
for fr in live:
    if fr.number < 7 or fr.number > 17:
        continue
    cs = _cs(fr)
    shots = planned_shots_from_attrs(fr)
    kplace = shots[0].get("место") if shots else None
    prompt = (fr.image_prompt or "").replace("\n", " ")[:90]
    print(
        f"  #{fr.number} cs.место={cs.get('место')!r} cs.сцена={cs.get('сцена')!r} "
        f"kadry0.место={kplace!r} act={cs.get('действие') or (shots[0].get('действие') if shots else '')!r}"
    )
    print(f"         prompt={prompt!r}")
