"""Inspect action.json timing vs phase counts for camera budget issues."""
from __future__ import annotations

import json
from pathlib import Path

P = Path("data/videos/spesivcevy-chrono-dyn/scene_design/action.json")
data = json.loads(P.read_text(encoding="utf-8"))
scenes = data.get("сцены") or data.get("scenes") or []
print("n_scenes", len(scenes))
bad = []
for sc in scenes:
    if not isinstance(sc, dict):
        continue
    chain = sc.get("цепь_действия") or []
    n = len(chain) if isinstance(chain, list) else 0
    try:
        sec = float(sc.get("время_сек") or 0)
    except (TypeError, ValueError):
        sec = 0.0
    sp = (sec / n) if n else 0.0
    if sec < 0.05 or (n >= 2 and sec < 2.0 * n) or (n and sp < 1.9):
        bad.append((sc.get("id_scene"), sec, n, round(sp, 2), sc.get("start_words", "")[:40]))
print("bad_budget", len(bad))
for row in bad[:40]:
    print(row)
print("sum_sec", round(sum(float(s.get("время_сек") or 0) for s in scenes if isinstance(s, dict)), 2))
print("sum_phases", sum(len(s.get("цепь_действия") or []) for s in scenes if isinstance(s, dict)))
