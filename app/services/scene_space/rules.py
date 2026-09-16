"""Blocking rules CANON §2.2–2.8 (angles, axis side, screen, reestablish, drama).

Prefers ``app.services.scene_space.geom`` for ``normalize_pair`` / ``axis_side``.
geom.py is owned by stream 1 and is not created here. If the import is missing,
a private CANON §2.3 fallback (same cross-product formula) lives at the bottom
of this file only.

Request to stream 1: export ``normalize_pair``, ``axis_side``, and
``DegenerateAxisError`` from ``geom`` (CONTRACTS §2). ``store`` may re-export.
"""

from __future__ import annotations

import json
import math
from typing import Any, Callable, Iterable, Mapping, Sequence

from app.services.scene_space.shot_size import size_from_ru, size_index

ANGLE_H: tuple[str, ...] = (
    "frontal",
    "three-quarter",
    "profile",
    "rear three-quarter",
    "over-the-shoulder",
    "from behind",
)
ANGLE_V: tuple[str, ...] = (
    "eye-level",
    "slight low",
    "low",
    "slight high",
    "high",
    "overhead",
    "dutch",
)

SCREEN_DIRS: tuple[str, ...] = (
    "L→R",
    "R→L",
    "to camera",
    "from camera",
    "none",
)
BEAT_ROLES: tuple[str, ...] = (
    "beat",
    "turning_point",
    "reaction",
    "insert",
    "establish",
    "reestablish",
)
CROSSING_METHODS: tuple[str, ...] = ("pov", "reestablish", "overhead", "camera_move")

_MS_INDEX = size_index("MS")
_MOVE_M = 1.0
_EPS = 1e-12
_DIR_OPP = {
    "L→R": "R→L",
    "R→L": "L→R",
    "to camera": "from camera",
    "from camera": "to camera",
}

_H_ALIAS: dict[str, str] = {
    "front": "frontal",
    "frontal": "frontal",
    "фронт": "frontal",
    "фронтальн": "frontal",
    "3/4": "three-quarter",
    "three quarter": "three-quarter",
    "three-quarter": "three-quarter",
    "threequarter": "three-quarter",
    "три четверти": "three-quarter",
    "profile": "profile",
    "профиль": "profile",
    "rear three-quarter": "rear three-quarter",
    "rear 3/4": "rear three-quarter",
    "rear three quarter": "rear three-quarter",
    "ots": "over-the-shoulder",
    "over-the-shoulder": "over-the-shoulder",
    "over the shoulder": "over-the-shoulder",
    "overshoulder": "over-the-shoulder",
    "через плечо": "over-the-shoulder",
    "from behind": "from behind",
    "behind": "from behind",
    "сзади": "from behind",
    "со спины": "from behind",
}
_V_ALIAS: dict[str, str] = {
    "eye-level": "eye-level",
    "eye level": "eye-level",
    "eyelevel": "eye-level",
    "уровень глаз": "eye-level",
    "slight low": "slight low",
    "slightly low": "slight low",
    "low": "low",
    "снизу": "low",
    "slight high": "slight high",
    "slightly high": "slight high",
    "high": "high",
    "сверху": "high",
    "overhead": "overhead",
    "top": "overhead",
    "отвес": "overhead",
    "вид сверху": "overhead",
    "dutch": "dutch",
    "голланд": "dutch",
}

_DELTA_META = frozenset({"turn", "turn_subject", "monotony", "service_shot"})

GeomAxisSide = Callable[[Any, Any, Any], str]
GeomNormalize = Callable[[str, str], tuple[str, str]]

_GEOM_SOURCE = "fallback"


class DegenerateAxisError(ValueError):
    """cross == 0: camera is on the axis line (CANON §2.3 / CONTRACTS)."""


def _xy(point: Any) -> tuple[float, float]:
    if point is None:
        raise DegenerateAxisError("missing point for axis_side")
    if isinstance(point, Mapping):
        return float(point["x"]), float(point["y"])
    if isinstance(point, (int, float)):
        raise DegenerateAxisError("axis_side expected an xy pair")
    seq = list(point)
    if len(seq) < 2:
        raise DegenerateAxisError("axis_side expected an xy pair")
    return float(seq[0]), float(seq[1])


def _fallback_normalize_pair(a: str, b: str) -> tuple[str, str]:
    lo, hi = sorted((str(a), str(b)))
    return lo, hi


def _fallback_axis_side(a_xy: Any, b_xy: Any, cam_xy: Any) -> str:
    """CANON §2.3 exact formula. Caller must pass already-normalized pair order."""
    ax, ay = _xy(a_xy)
    bx, by = _xy(b_xy)
    cx, cy = _xy(cam_xy)
    axis_x = bx - ax
    axis_y = by - ay
    to_x = cx - ax
    to_y = cy - ay
    cross = axis_x * to_y - axis_y * to_x
    if cross == 0 or abs(cross) <= _EPS:
        raise DegenerateAxisError("camera on axis (cross == 0)")
    return "A" if cross > 0 else "B"


def _bind_geom() -> tuple[GeomNormalize, GeomAxisSide, type[Exception], str]:
    try:
        from app.services.scene_space.geom import axis_side as geom_side
        from app.services.scene_space.geom import normalize_pair as geom_pair

        err: type[Exception] = DegenerateAxisError
        try:
            from app.services.scene_space.geom import DegenerateAxisError as geom_err

            err = geom_err
        except ImportError:
            try:
                from app.services.scene_space.errors import (
                    DegenerateAxisError as geom_err,
                )

                err = geom_err
            except ImportError:
                pass
        return geom_pair, geom_side, err, "geom"
    except ImportError:
        pass
    try:
        from app.services.scene_space.store import axis_side as store_side
        from app.services.scene_space.store import normalize_pair as store_pair

        err = DegenerateAxisError
        try:
            from app.services.scene_space.errors import DegenerateAxisError as store_err

            err = store_err
        except ImportError:
            pass
        return store_pair, store_side, err, "store"
    except ImportError:
        return (
            _fallback_normalize_pair,
            _fallback_axis_side,
            DegenerateAxisError,
            "fallback",
        )


normalize_pair, axis_side, DegenerateAxisError, _GEOM_SOURCE = _bind_geom()


def pair_key(a: str, b: str) -> str:
    lo, hi = normalize_pair(str(a), str(b))
    return f"{lo}|{hi}"


def parse_pair(raw: Any) -> tuple[str, str] | None:
    if raw is None:
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return normalize_pair(str(raw[0]), str(raw[1]))
    text = str(raw).strip()
    if not text:
        return None
    if "|" in text:
        a, b = text.split("|", 1)
        return normalize_pair(a.strip(), b.strip())
    parts = [p for p in text.replace(",", " ").split() if p]
    if len(parts) >= 2:
        return normalize_pair(parts[0], parts[1])
    return None


def canon_angle_h(text: Any) -> str:
    raw = " ".join(str(text or "").replace("_", " ").split()).casefold()
    if not raw:
        return "frontal"
    if raw in ANGLE_H:
        return raw
    if raw in _H_ALIAS:
        return _H_ALIAS[raw]
    for key, val in _H_ALIAS.items():
        if key and key in raw:
            return val
    return "frontal"


def canon_angle_v(text: Any) -> str:
    raw = " ".join(str(text or "").replace("_", " ").split()).casefold()
    if not raw:
        return "eye-level"
    if raw in ANGLE_V:
        return raw
    if raw in _V_ALIAS:
        return _V_ALIAS[raw]
    for key, val in _V_ALIAS.items():
        if key and key in raw:
            return val
    return "eye-level"


def canon_screen_dir(text: Any) -> str:
    raw = " ".join(str(text or "").split())
    if not raw:
        return "none"
    aliases = {
        "l->r": "L→R",
        "l→r": "L→R",
        "r->l": "R→L",
        "r→l": "R→L",
        "to camera": "to camera",
        "to_camera": "to camera",
        "from camera": "from camera",
        "from_camera": "from camera",
        "none": "none",
    }
    return aliases.get(raw.casefold(), raw if raw in SCREEN_DIRS else "none")


def canon_beat_role(text: Any) -> str:
    raw = str(text or "").strip().casefold()
    aliases = {
        "tp": "turning_point",
        "turning point": "turning_point",
        "поворот": "turning_point",
        "точка поворота": "turning_point",
        "реакция": "reaction",
        "вставка": "insert",
        "установка": "establish",
        "переустановка": "reestablish",
        "бит": "beat",
    }
    if raw in BEAT_ROLES:
        return raw
    return aliases.get(raw, "beat")


def canon_crossing(text: Any) -> str | None:
    if text is None:
        return None
    raw = str(text).strip().casefold()
    if raw in {"", "none", "null", "0"}:
        return None
    aliases = {
        "pov": "pov",
        "pov-мост": "pov",
        "reestablish": "reestablish",
        "переустановка": "reestablish",
        "overhead": "overhead",
        "отвес": "overhead",
        "camera_move": "camera_move",
        "camera-move": "camera_move",
        "проезд": "camera_move",
    }
    return aliases.get(raw, raw if raw in CROSSING_METHODS else None)


def _h_index(name: str) -> int:
    return ANGLE_H.index(canon_angle_h(name))


def _v_index(name: str) -> int:
    return ANGLE_V.index(canon_angle_v(name))


def _circ_dist(i: int, j: int, n: int) -> int:
    d = abs(i - j)
    return min(d, n - d)


def angle_h_step(current: str, steps: int = 2) -> str:
    i = _h_index(current)
    return ANGLE_H[(i + int(steps)) % len(ANGLE_H)]


def names_under_30(prev_h: str, prev_v: str, cur_h: str, cur_v: str) -> bool:
    """True when named angles are a <30° equivalent (CANON §2.2, no coords)."""
    dh = _circ_dist(_h_index(prev_h), _h_index(cur_h), len(ANGLE_H))
    dv = abs(_v_index(prev_v) - _v_index(cur_v))
    if dh == 0 and dv == 0:
        return True
    return dh <= 1 and dv < 2


def _as_plan_item(plan: Sequence[Mapping[str, Any]], item_id: str) -> dict[str, Any] | None:
    for row in plan:
        if str(row.get("id") or "") == str(item_id):
            return dict(row)
    return None


def look_at_xy(
    plan: Sequence[Mapping[str, Any]], pair: tuple[str, str] | None
) -> tuple[float, float] | None:
    if pair is None:
        return None
    a = _as_plan_item(plan, pair[0])
    b = _as_plan_item(plan, pair[1])
    if a is None or b is None:
        return None
    return ((float(a["x"]) + float(b["x"])) / 2.0, (float(a["y"]) + float(b["y"])) / 2.0)


def cam_world_angle_deg(
    prev_cam: Any,
    cur_cam: Any,
    look: tuple[float, float] | None,
) -> float | None:
    if look is None:
        return None
    try:
        p = _xy(prev_cam)
        c = _xy(cur_cam)
    except (DegenerateAxisError, KeyError, TypeError, ValueError):
        return None
    v1 = (p[0] - look[0], p[1] - look[1])
    v2 = (c[0] - look[0], c[1] - look[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 <= _EPS or n2 <= _EPS:
        return 0.0
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(dot))


def angle_jump(
    prev_h: str,
    prev_v: str,
    cur_h: str,
    cur_v: str,
    *,
    prev_cam: Any = None,
    cur_cam: Any = None,
    look: tuple[float, float] | None = None,
) -> bool:
    """True = error «скачок» (point moved < 30°)."""
    if prev_cam is not None and cur_cam is not None and look is not None:
        deg = cam_world_angle_deg(prev_cam, cur_cam, look)
        if deg is not None:
            return deg < 30.0
    return names_under_30(prev_h, prev_v, cur_h, cur_v)


def rotate_xy(
    point: tuple[float, float],
    origin: tuple[float, float],
    deg: float,
) -> tuple[float, float]:
    rad = math.radians(deg)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    vx = point[0] - origin[0]
    vy = point[1] - origin[1]
    return (origin[0] + vx * cos_a - vy * sin_a, origin[1] + vx * sin_a + vy * cos_a)


def cam_on_locked_side(
    a_xy: Any,
    b_xy: Any,
    cam_xy: Any,
    locked: str,
    *,
    degrees_list: Sequence[float] = (0.0, 35.0, -35.0, 50.0, -50.0, 90.0, -90.0),
    look: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """Rotate camera around the pair midpoint until it sits on ``locked`` side."""
    cam = _xy(cam_xy)
    origin = look or (
        (_xy(a_xy)[0] + _xy(b_xy)[0]) / 2.0,
        (_xy(a_xy)[1] + _xy(b_xy)[1]) / 2.0,
    )
    for deg in degrees_list:
        candidate = rotate_xy(cam, origin, deg) if deg else cam
        try:
            if axis_side(a_xy, b_xy, candidate) == locked:
                return candidate
        except DegenerateAxisError:
            continue
    perp = _side_nudge(a_xy, b_xy, locked)
    return (cam[0] + perp[0], cam[1] + perp[1])


def _side_nudge(a_xy: Any, b_xy: Any, locked: str) -> tuple[float, float]:
    ax, ay = _xy(a_xy)
    bx, by = _xy(b_xy)
    # Left of a→b is (-ay_axis, ax_axis); side A is cross>0 = left.
    left = (ay - by, bx - ax)
    n = math.hypot(*left) or 1.0
    sign = 1.0 if locked == "A" else -1.0
    return (sign * left[0] / n * 0.35, sign * left[1] / n * 0.35)


def compute_screen_pos(
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str],
    on_screen: Iterable[str] | None = None,
) -> str:
    """JSON string of L/R/C for axis participants (CANON §2.5 / CONTRACTS).

    CONTRACTS: smaller screen-x is L. Camera-right is a 90° clockwise rotation
    of the look vector (cam → pair midpoint). The literal axis-perpendicular
    ``(axis.y, -axis.x)`` is used only when the look vector is degenerate;
    on a standard two-shot that vector is depth, so both subjects would be C.
    """
    lo, hi = normalize_pair(pair[0], pair[1])
    a = _as_plan_item(plan, lo)
    b = _as_plan_item(plan, hi)
    cam = _as_plan_item(plan, "cam")
    if a is None or b is None:
        return "{}"
    ax, ay = float(a["x"]), float(a["y"])
    bx, by = float(b["x"]), float(b["y"])
    if cam is not None:
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        fx = mx - float(cam["x"])
        fy = my - float(cam["y"])
    else:
        fx, fy = 0.0, 0.0
    if abs(fx) <= _EPS and abs(fy) <= _EPS:
        axis_x, axis_y = bx - ax, by - ay
        right = (axis_y, -axis_x)
    else:
        right = (fy, -fx)

    def _proj(item: Mapping[str, Any]) -> float:
        return float(item["x"]) * right[0] + float(item["y"]) * right[1]

    allowed = None if on_screen is None else {str(s) for s in on_screen}
    ids = [lo, hi]
    if allowed is not None:
        ids = [i for i in ids if i in allowed]
        if not ids:
            return "{}"
    projs = {}
    for i in ids:
        item = _as_plan_item(plan, i)
        if item is None:
            continue
        projs[i] = _proj(item)
    if not projs:
        return "{}"
    if len(projs) == 1:
        sid = next(iter(projs))
        other = hi if sid == lo else lo
        other_item = _as_plan_item(plan, other)
        pos = "C"
        if other_item is not None:
            op = _proj(other_item)
            if projs[sid] < op:
                pos = "L"
            elif projs[sid] > op:
                pos = "R"
        return json.dumps({sid: pos}, ensure_ascii=False, separators=(",", ":"))

    ordered = sorted(projs, key=lambda i: (projs[i], i))
    out: dict[str, str] = {}
    if abs(projs[ordered[0]] - projs[ordered[-1]]) <= _EPS:
        for i in projs:
            out[i] = "C"
    else:
        out[ordered[0]] = "L"
        out[ordered[-1]] = "R"
        for i in projs:
            out.setdefault(i, "C")
    return json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def compute_screen_dir(
    prev_plan: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str] | None,
    *,
    threshold: float = 0.05,
) -> str:
    """Canonical screen_dir of the active-axis subject with a nonzero delta."""
    if pair is None:
        return "none"
    lo, hi = normalize_pair(pair[0], pair[1])
    a = _as_plan_item(plan, lo)
    b = _as_plan_item(plan, hi)
    if a is None or b is None:
        return "none"
    cam = _as_plan_item(plan, "cam")
    ax, ay = float(a["x"]), float(a["y"])
    bx, by = float(b["x"]), float(b["y"])
    if cam is not None:
        mx, my = (ax + bx) / 2.0, (ay + by) / 2.0
        fx = mx - float(cam["x"])
        fy = my - float(cam["y"])
        if abs(fx) > _EPS or abs(fy) > _EPS:
            right = (fy, -fx)
        else:
            right = (by - ay, -(bx - ax))
    else:
        right = (by - ay, -(bx - ax))
    rn = math.hypot(*right) or 1.0
    right = (right[0] / rn, right[1] / rn)

    best: tuple[float, str, tuple[float, float], dict[str, Any]] | None = None
    for sid in (lo, hi):
        p0 = _as_plan_item(prev_plan, sid)
        p1 = _as_plan_item(plan, sid)
        if p0 is None or p1 is None:
            continue
        delta = (float(p1["x"]) - float(p0["x"]), float(p1["y"]) - float(p0["y"]))
        mag = math.hypot(*delta)
        if mag < threshold:
            continue
        if best is None or mag > best[0]:
            best = (mag, sid, delta, p1)
    if best is None:
        return "none"
    _mag, _sid, delta, pos = best
    along_right = delta[0] * right[0] + delta[1] * right[1]
    along_cam = 0.0
    if cam is not None:
        to_cam = (float(cam["x"]) - float(pos["x"]), float(cam["y"]) - float(pos["y"]))
        tn = math.hypot(*to_cam)
        if tn > _EPS:
            along_cam = (delta[0] * to_cam[0] + delta[1] * to_cam[1]) / tn
    if abs(along_cam) > abs(along_right) and abs(along_cam) >= threshold:
        return "to camera" if along_cam > 0 else "from camera"
    if abs(along_right) >= threshold:
        return "L→R" if along_right > 0 else "R→L"
    return "none"


def dirs_are_opposite(prev_dir: str, cur_dir: str) -> bool:
    a = canon_screen_dir(prev_dir)
    b = canon_screen_dir(cur_dir)
    if a in {"none", ""} or b in {"none", ""}:
        return False
    return _DIR_OPP.get(a) == b


def turn_marked(delta: Mapping[str, Any] | None, subject: str | None = None) -> bool:
    if not delta:
        return False
    turn = delta.get("turn")
    if turn is True:
        return True
    if isinstance(turn, Mapping):
        sub = turn.get("subject")
        if subject and sub and str(sub) != str(subject):
            return False
        return True
    if delta.get("turn_subject"):
        if subject and str(delta.get("turn_subject")) != str(subject):
            return False
        return True
    if subject:
        blob = delta.get(str(subject))
        if isinstance(blob, Mapping) and blob.get("turn") is True:
            return True
    return False


def body_ids(plan: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in plan:
        sid = str(row.get("id") or "")
        if not sid or sid == "cam":
            continue
        if "w" in row and "h" in row and "facing" not in row:
            continue
        out.add(sid)
    return out


def subject_moved(
    prev_plan: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
    *,
    meters: float = _MOVE_M,
) -> bool:
    prev = {str(p.get("id")): p for p in prev_plan}
    for row in plan:
        sid = str(row.get("id") or "")
        if sid not in body_ids(plan):
            continue
        old = prev.get(sid)
        if old is None:
            continue
        dx = float(row.get("x") or 0) - float(old.get("x") or 0)
        dy = float(row.get("y") or 0) - float(old.get("y") or 0)
        if math.hypot(dx, dy) >= meters:
            return True
    return False


def roster_changed(
    prev_plan: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
) -> bool:
    return body_ids(prev_plan) != body_ids(plan)


def reestablish_trigger(
    *,
    moved: bool,
    roster: bool,
    pair_changed: bool,
    close_streak: int,
) -> bool:
    return bool(moved or roster or pair_changed or close_streak >= 3)


def is_close_size(code: str) -> bool:
    try:
        return size_index(code) > _MS_INDEX
    except ValueError:
        return False


def pays_reestablish(code: str) -> bool:
    try:
        return size_from_ru(code) in {"WS", "EWS"}
    except Exception:
        return False


def value_charge_of(space: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not space:
        return None
    raw = space.get("value_charge")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, Mapping):
        return None
    start = str(raw.get("start") or "").strip()
    end = str(raw.get("end") or "").strip()
    if start not in {"+", "-"} or end not in {"+", "-"}:
        return None
    return {"start": start, "end": end}


def value_charge_turns(charge: Mapping[str, str] | None) -> bool:
    if not charge:
        return False
    return charge.get("start") in {"+", "-"} and charge.get("start") != charge.get("end")


def infer_value_charge(bits: Sequence[Mapping[str, Any]] | None) -> dict[str, str]:
    has_tp = any(_bit_is_turning(b) for b in (bits or []))
    if has_tp:
        return {"start": "+", "end": "-"}
    return {"start": "+", "end": "+"}


def _bit_is_turning(bit: Mapping[str, Any]) -> bool:
    if bit.get("turning_point") is True or bit.get("is_turning_point") is True:
        return True
    if bit.get("точка_поворота") is True:
        return True
    role = canon_beat_role(bit.get("beat_role") or bit.get("role") or bit.get("роль"))
    return role == "turning_point"


def apply_delta(
    plan: Sequence[Mapping[str, Any]],
    delta: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {
        str(p.get("id")): dict(p) for p in plan if p.get("id") is not None
    }
    if not delta:
        return list(by_id.values())
    for key, change in delta.items():
        sid = str(key)
        if sid in _DELTA_META:
            continue
        if isinstance(change, Mapping) and change.get("_remove") is True:
            by_id.pop(sid, None)
            continue
        row = by_id.get(sid) or {"id": sid}
        if isinstance(change, Mapping):
            for field, val in change.items():
                if str(field).startswith("_") or field == "turn":
                    continue
                row[field] = val
        by_id[sid] = row
    return list(by_id.values())


def plan_cam(plan: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    return _as_plan_item(plan, "cam")


def ensure_cam(plan: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = [dict(p) for p in plan]
    if any(str(p.get("id")) == "cam" for p in rows):
        return rows
    rows.append({"id": "cam", "x": 0.0, "y": -2.0, "facing": 0.0})
    return rows


def side_for_plan(
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str],
) -> str:
    lo, hi = normalize_pair(pair[0], pair[1])
    a = _as_plan_item(plan, lo)
    b = _as_plan_item(plan, hi)
    cam = _as_plan_item(plan, "cam")
    if a is None or b is None or cam is None:
        raise DegenerateAxisError("axis_side needs lo, hi, and cam on the plan")
    return axis_side((float(a["x"]), float(a["y"])), (float(b["x"]), float(b["y"])), (float(cam["x"]), float(cam["y"])))


def locked_sides_from_space(space: Mapping[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not space:
        return out
    for axis in space.get("axes") or []:
        if not isinstance(axis, Mapping):
            continue
        parsed = parse_pair(axis.get("pair") or axis.get("axis_pair"))
        side = str(axis.get("side_locked") or axis.get("axis_side") or "").strip().upper()
        if parsed and side in {"A", "B"}:
            out[f"{parsed[0]}|{parsed[1]}"] = side
    return out
