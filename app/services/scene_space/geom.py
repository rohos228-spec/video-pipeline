
from __future__ import annotations

import math
from typing import Any

from app.services.scene_space.errors import DegenerateAxisError

_DELTA_META_KEYS = frozenset({"turn", "turn_subject", "monotony"})


def normalize_pair(a: str, b: str) -> tuple[str, str]:
    lo, hi = sorted((str(a), str(b)))
    return lo, hi


def _xy(point: Any) -> tuple[float, float]:
    if isinstance(point, dict):
        return float(point["x"]), float(point["y"])
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def axis_side(a_xy: Any, b_xy: Any, cam_xy: Any) -> str:
    ax, ay = _xy(a_xy)
    bx, by = _xy(b_xy)
    cx, cy = _xy(cam_xy)
    axis_x = bx - ax
    axis_y = by - ay
    to_cam_x = cx - ax
    to_cam_y = cy - ay
    cross = axis_x * to_cam_y - axis_y * to_cam_x
    if cross == 0:
        raise DegenerateAxisError("camera on axis (cross==0)")
    return "A" if cross > 0 else "B"


def facing_vector(degrees: float) -> tuple[float, float]:
    rad = math.radians(float(degrees) % 360.0)
    return (math.sin(rad), math.cos(rad))


def apply_deltas(plan: list[dict], deltas_in_order: list[dict]) -> list[dict]:
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for item in plan:
        kid = str(item["id"])
        by_id[kid] = dict(item)
        order.append(kid)
    for delta in deltas_in_order:
        if not delta:
            continue
        for key, patch in delta.items():
            if key in _DELTA_META_KEYS:
                continue
            if not isinstance(patch, dict):
                continue
            kid = str(key)
            if patch.get("_remove"):
                by_id.pop(kid, None)
                if kid in order:
                    order.remove(kid)
                continue
            merged = dict(by_id[kid]) if kid in by_id else {"id": kid}
            for field, value in patch.items():
                if field == "_remove":
                    continue
                merged[field] = value
            if kid not in by_id:
                order.append(kid)
            by_id[kid] = merged
    return [by_id[i] for i in order if i in by_id]
