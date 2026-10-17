"""Geometry of cinematic scene space.

Units: meters. x right, y depth. facing degrees: 0 = +Y (into depth),
clockwise (90 = +X right, 180 = −Y, 270 = −X left). 0 and 360 are equivalent.
"""

from __future__ import annotations

import math
from typing import Any

from app.services.scene_space.errors import DegenerateAxisError

_DELTA_META_KEYS = frozenset({"turn", "turn_subject", "monotony"})


def normalize_pair(a: str, b: str) -> tuple[str, str]:
    """Canonical pair order: sort ids as strings → (lo, hi)."""
    lo, hi = sorted((str(a), str(b)))
    return lo, hi


def _xy(point: Any) -> tuple[float, float]:
    if isinstance(point, dict):
        return float(point["x"]), float(point["y"])
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def axis_side(a_xy: Any, b_xy: Any, cam_xy: Any) -> str:
    """Side of axis a→b on which the camera sits: ``A`` if cross>0, ``B`` if cross<0.

    Pass coordinates of the *normalized* pair (lo, hi) as a, b.
    Raises DegenerateAxisError when the camera is on the axis (cross == 0).
    """
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
    """Unit look vector. 0=+Y, clockwise: 90=+X, 180=−Y, 270=−X. 0≡360."""
    rad = math.radians(float(degrees) % 360.0)
    return (math.sin(rad), math.cos(rad))


def apply_deltas(plan: list[dict], deltas_in_order: list[dict]) -> list[dict]:
    """Replay object patches onto a copy of ``plan``. Does not mutate inputs.

    Delta keys that are plan ids merge x/y/facing (and other fields).
    Removal: ``{"c03": {"_remove": true}}``. Top-level metadata
    (``turn``, ``turn_subject``, ``monotony``) is ignored.
    """
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
