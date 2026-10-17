"""Top-down scene plan: deterministic SVG (and optional PNG) per frame.

Does not own store/validate. Replay uses store.apply_deltas when importable,
otherwise a local delta fold that matches CONTRACTS §1.2.
"""

from __future__ import annotations

import json
import math
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

PAD_M = 2.0
PX_PER_M = 40
SUBJECT_R = 0.22
FACE_LEN = 0.55
CAM_LEN = 0.42
MOTION_LEN = 0.70
STROKE_AXIS = 0.07
STROKE_THIN = 0.04
FILL_A = "rgba(0,128,0,0.15)"
FILL_B = "rgba(255,165,0,0.15)"
FILL_A_RGBA = (0, 128, 0, 38)
FILL_B_RGBA = (255, 165, 0, 38)
META_DELTA_KEYS = frozenset({"turn", "monotony", "turn_subject"})
CAM_ID = "cam"


def fmt(n: float) -> str:
    return f"{float(n):.3f}"


def _xe(text: Any) -> str:
    return xml_escape(str(text if text is not None else ""), {"'": "&apos;", '"': "&quot;"})


def _as_dict(raw: Any) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            val = json.loads(text)
        except json.JSONDecodeError:
            return {}
        return val if isinstance(val, dict) else {}
    return {}


def _as_list(raw: Any) -> list[Any]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            val = json.loads(text)
        except json.JSONDecodeError:
            return []
        return val if isinstance(val, list) else []
    return []


def parse_scene_dump(data: dict[str, Any]) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Accept a scene dump JSON object. Returns (scene_id, space_json, frames)."""
    scene_id = str(data.get("scene_id") or "")
    space = data.get("space_json")
    if space is None:
        space = data.get("space")
    if space is None and isinstance(data.get("plan"), list):
        space = data
    frames = data.get("frames")
    if frames is None:
        frames = data.get("frames_space")
    return scene_id, normalize_space(space), normalize_frames(frames or [])


def load_json_dump(path: Path) -> tuple[str, dict[str, Any], list[dict[str, Any]], list[Any]]:
    """Load --from-json dump. Fourth item is optional violations list."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"scene dump must be a JSON object: {path}")
    scene_id, space, frames = parse_scene_dump(payload)
    violations = payload.get("violations")
    if violations is None:
        violations = payload.get("issues") or []
    return scene_id, space, frames, violations


def normalize_space(raw: Any) -> dict[str, Any]:
    space = _as_dict(raw)
    if "plan" not in space and "space_json" in space:
        space = _as_dict(space.get("space_json"))
    plan = _as_list(space.get("plan"))
    obstacles = _as_list(space.get("obstacles"))
    axes = _as_list(space.get("axes"))
    out = dict(space)
    out["plan"] = [dict(p) for p in plan if isinstance(p, dict)]
    out["obstacles"] = [dict(o) for o in obstacles if isinstance(o, dict)]
    out["axes"] = [dict(a) for a in axes if isinstance(a, dict)]
    return out


def normalize_frames(frames: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in frames:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        nested = row.get("space")
        if isinstance(nested, dict):
            for key, val in nested.items():
                row.setdefault(key, val)
        attrs = row.get("attrs")
        if isinstance(attrs, dict) and not row.get("accent") and attrs.get("accent"):
            row["accent"] = attrs["accent"]
        if "space_delta_json" in row:
            parsed = _as_dict(row.get("space_delta_json"))
            row["_delta"] = parsed
            if not isinstance(row.get("space_delta_json"), dict):
                row["space_delta_json"] = parsed
        else:
            row["_delta"] = {}
        if isinstance(row.get("screen_pos"), str):
            parsed_pos = _as_dict(row.get("screen_pos"))
            row["_screen_pos"] = parsed_pos
        elif isinstance(row.get("screen_pos"), dict):
            row["_screen_pos"] = dict(row["screen_pos"])
        else:
            row["_screen_pos"] = {}
        try:
            row["shot_order"] = int(row.get("shot_order") or 0)
        except (TypeError, ValueError):
            row["shot_order"] = 0
        out.append(row)
    out.sort(key=lambda r: (r["shot_order"], str(r.get("uuid") or "")))
    return out


def apply_deltas_local(plan: list[dict[str, Any]], deltas_in_order: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold deltas onto a copy of plan. Matches CONTRACTS delta shape."""
    order = [str(p.get("id")) for p in plan if p.get("id") is not None]
    by_id: dict[str, dict[str, Any]] = {}
    for item in plan:
        pid = item.get("id")
        if pid is None:
            continue
        by_id[str(pid)] = deepcopy(item)
        by_id[str(pid)]["id"] = str(pid)
    for delta in deltas_in_order:
        if not isinstance(delta, dict):
            continue
        for key, change in delta.items():
            sid = str(key)
            if sid in META_DELTA_KEYS:
                continue
            if not isinstance(change, dict):
                continue
            if change.get("_remove"):
                by_id.pop(sid, None)
                if sid in order:
                    order.remove(sid)
                continue
            if sid not in by_id:
                by_id[sid] = {"id": sid}
                order.append(sid)
            for field, value in change.items():
                if field == "_remove":
                    continue
                by_id[sid][field] = value
            by_id[sid]["id"] = sid
    return [by_id[i] for i in order if i in by_id]


def _apply_deltas(plan: list[dict[str, Any]], deltas_in_order: list[dict[str, Any]]) -> list[dict[str, Any]]:
    try:
        from app.services.scene_space.store import apply_deltas as store_apply
    except ImportError:
        return apply_deltas_local(plan, deltas_in_order)
    try:
        result = store_apply(deepcopy(plan), list(deltas_in_order))
    except Exception:
        return apply_deltas_local(plan, deltas_in_order)
    if isinstance(result, list):
        return [dict(p) for p in result if isinstance(p, dict)]
    return apply_deltas_local(plan, deltas_in_order)


def _plan_from_state_obj(st: Any) -> list[dict[str, Any]] | None:
    if isinstance(st, list):
        return [dict(p) for p in st if isinstance(p, dict)]
    if isinstance(st, dict) and isinstance(st.get("plan"), list):
        return [dict(p) for p in st["plan"] if isinstance(p, dict)]
    return None


def replay_states(space: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One state dict per frame: plan after deltas with shot_order <= this frame."""
    space = normalize_space(space)
    frames = normalize_frames(frames)
    initial = [dict(p) for p in space.get("plan") or []]
    obstacles = [dict(o) for o in space.get("obstacles") or []]
    axes = [dict(a) for a in space.get("axes") or []]
    acc: list[dict[str, Any]] = []
    states: list[dict[str, Any]] = []
    prev_plan: list[dict[str, Any]] | None = None
    for frame in frames:
        attached = _plan_from_state_obj(frame.get("_state_plan") or frame.get("_state"))
        if attached is not None:
            plan = attached
        else:
            delta = frame.get("_delta")
            if not isinstance(delta, dict):
                delta = _as_dict(frame.get("space_delta_json"))
            acc.append(delta)
            plan = _apply_deltas(initial, acc)
        states.append(
            {
                "plan": plan,
                "obstacles": obstacles,
                "axes": axes,
                "frame": frame,
                "prev_plan": prev_plan,
                "space": space,
            }
        )
        prev_plan = plan
    return states


def _xy(item: dict[str, Any] | None) -> tuple[float, float] | None:
    if not item:
        return None
    try:
        return (float(item["x"]), float(item["y"]))
    except (KeyError, TypeError, ValueError):
        return None


def _by_id(plan: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(p["id"]): p for p in plan if p.get("id") is not None}


def _facing_vec(deg: Any) -> tuple[float, float]:
    """0 → +Y (depth), clockwise: 90 → +X."""
    try:
        ang = float(deg)
    except (TypeError, ValueError):
        ang = 0.0
    rad = math.radians(ang % 360.0)
    return (math.sin(rad), math.cos(rad))


def _parse_pair(raw: Any) -> tuple[str, str] | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text or "|" not in text:
        return None
    left, right = text.split("|", 1)
    lo, hi = left.strip(), right.strip()
    if not lo or not hi:
        return None
    return (lo, hi)


def _active_pair(state: dict[str, Any]) -> tuple[str, str] | None:
    frame = state.get("frame") or {}
    parsed = _parse_pair(frame.get("axis_pair"))
    if parsed:
        return parsed
    for axis in state.get("axes") or []:
        pair = axis.get("pair")
        if isinstance(pair, (list, tuple)) and len(pair) >= 2:
            a, b = str(pair[0]), str(pair[1])
            if a > b:
                a, b = b, a
            return (a, b)
        parsed = _parse_pair(axis.get("pair"))
        if parsed:
            return parsed
    return None


def _locked_side(state: dict[str, Any]) -> str | None:
    frame = state.get("frame") or {}
    side = str(frame.get("axis_side") or "").strip().upper()
    if side in ("A", "B"):
        return side
    pair = _active_pair(state)
    if not pair:
        return None
    want = f"{pair[0]}|{pair[1]}"
    for axis in state.get("axes") or []:
        ap = axis.get("pair")
        key = None
        if isinstance(ap, (list, tuple)) and len(ap) >= 2:
            a, b = str(ap[0]), str(ap[1])
            if a > b:
                a, b = b, a
            key = f"{a}|{b}"
        else:
            parsed = _parse_pair(ap)
            if parsed:
                key = f"{parsed[0]}|{parsed[1]}"
        if key == want:
            locked = str(axis.get("side_locked") or "").strip().upper()
            if locked in ("A", "B"):
                return locked
    return None


def world_bounds(states: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for state in states:
        for item in state.get("plan") or []:
            pt = _xy(item)
            if pt:
                xs.append(pt[0])
                ys.append(pt[1])
        for obs in state.get("obstacles") or []:
            try:
                x, y = float(obs["x"]), float(obs["y"])
                w, h = float(obs.get("w") or 0), float(obs.get("h") or 0)
            except (KeyError, TypeError, ValueError):
                continue
            xs.extend([x, x + w])
            ys.extend([y, y + h])
    if not xs:
        return (-PAD_M, -PAD_M, PAD_M, PAD_M)
    return (min(xs), min(ys), max(xs), max(ys))


def scene_viewbox(states: list[dict[str, Any]]) -> tuple[float, float, float, float]:
    """SVG viewBox (x, y, w, h). y is SVG-down after world Y flip. Pad 2 m."""
    xmin, ymin, xmax, ymax = world_bounds(states)
    if xmax <= xmin:
        xmax = xmin + 0.001
    if ymax <= ymin:
        ymax = ymin + 0.001
    vb_x = xmin - PAD_M
    vb_y = -(ymax + PAD_M)
    vb_w = (xmax - xmin) + 2 * PAD_M
    vb_h = (ymax - ymin) + 2 * PAD_M
    return (vb_x, vb_y, vb_w, vb_h)


def svg_xy(x: float, y: float) -> tuple[float, float]:
    return (x, -y)


def _clip_halfplane(
    poly: list[tuple[float, float]],
    a: tuple[float, float],
    b: tuple[float, float],
    side: str,
) -> list[tuple[float, float]]:
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    if dx * dx + dy * dy < 1e-16:
        return []

    def cross(p: tuple[float, float]) -> float:
        return dx * (p[1] - ay) - dy * (p[0] - ax)

    def inside(p: tuple[float, float]) -> bool:
        c = cross(p)
        if abs(c) <= 1e-12:
            return True
        return c > 0 if side == "A" else c < 0

    def intersect(p: tuple[float, float], q: tuple[float, float]) -> tuple[float, float]:
        px, py = p
        qx, qy = q
        rx, ry = qx - px, qy - py
        den = dx * ry - dy * rx
        if abs(den) < 1e-15:
            return q
        t = -cross(p) / den
        return (px + t * rx, py + t * ry)

    if not poly:
        return []
    out: list[tuple[float, float]] = []
    prev = poly[-1]
    prev_in = inside(prev)
    for cur in poly:
        cur_in = inside(cur)
        if cur_in:
            if not prev_in:
                out.append(intersect(prev, cur))
            out.append(cur)
        elif prev_in:
            out.append(intersect(prev, cur))
        prev, prev_in = cur, cur_in
    return out


def _view_rect_world(vb: tuple[float, float, float, float]) -> list[tuple[float, float]]:
    vb_x, vb_y, vb_w, vb_h = vb
    # SVG y = -world_y, so world y runs from -(vb_y+vb_h) to -vb_y
    xmin = vb_x
    xmax = vb_x + vb_w
    ymax = -vb_y
    ymin = -(vb_y + vb_h)
    return [(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)]


def _axis_line_clipped(
    a: tuple[float, float],
    b: tuple[float, float],
    vb: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Infinite axis through a→b, clipped to the view rectangle (Liang–Barsky)."""
    rect = _view_rect_world(vb)
    xmin = min(p[0] for p in rect)
    xmax = max(p[0] for p in rect)
    ymin = min(p[1] for p in rect)
    ymax = max(p[1] for p in rect)
    ax, ay = a
    dx, dy = b[0] - a[0], b[1] - a[1]
    if dx * dx + dy * dy < 1e-16:
        return None
    t0, t1 = -1e6, 1e6
    p_q = ((-dx, ax - xmin), (dx, xmax - ax), (-dy, ay - ymin), (dy, ymax - ay))
    for p_i, q_i in p_q:
        if abs(p_i) < 1e-15:
            if q_i < 0:
                return None
            continue
        t = q_i / p_i
        if p_i < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return ((ax + t0 * dx, ay + t0 * dy), (ax + t1 * dx, ay + t1 * dy))


def _norm2(dx: float, dy: float) -> tuple[float, float] | None:
    mag = math.hypot(dx, dy)
    if mag < 1e-12:
        return None
    return (dx / mag, dy / mag)


def _arrow_head(
    x: float, y: float, ux: float, uy: float, size: float = 0.16
) -> list[tuple[float, float]]:
    px, py = -uy, ux
    return [
        (x, y),
        (x - ux * size + px * size * 0.45, y - uy * size + py * size * 0.45),
        (x - ux * size - px * size * 0.45, y - uy * size - py * size * 0.45),
    ]


def _screen_right(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float] | None:
    dx, dy = b[0] - a[0], b[1] - a[1]
    return _norm2(dy, -dx)


def _motion_world(state: dict[str, Any]) -> tuple[tuple[float, float], tuple[float, float]] | None:
    """Return (start, end) in world for the motion arrow, if any."""
    frame = state.get("frame") or {}
    plan = _by_id(state.get("plan") or [])
    prev = _by_id(state.get("prev_plan") or [])
    pair = _active_pair(state)
    candidates: list[str] = []
    if pair:
        candidates.extend(pair)
    candidates.extend(sorted(k for k in plan if k != CAM_ID))
    seen: set[str] = set()
    for sid in candidates:
        if sid in seen or sid == CAM_ID:
            continue
        seen.add(sid)
        cur = _xy(plan.get(sid))
        old = _xy(prev.get(sid)) if prev else None
        if cur and old and math.hypot(cur[0] - old[0], cur[1] - old[1]) >= 0.05:
            return (old, cur)
    screen_dir = str(frame.get("screen_dir") or "none").strip()
    if screen_dir in ("", "none"):
        return None
    origin = None
    if pair:
        origin = _xy(plan.get(pair[0]))
    if origin is None:
        for sid in sorted(plan):
            if sid != CAM_ID:
                origin = _xy(plan.get(sid))
                if origin:
                    break
    if origin is None:
        return None
    cam = _xy(plan.get(CAM_ID))
    vec: tuple[float, float] | None = None
    if screen_dir == "L→R" and pair:
        pa, pb = _xy(plan.get(pair[0])), _xy(plan.get(pair[1]))
        if pa and pb:
            vec = _screen_right(pa, pb)
    elif screen_dir == "R→L" and pair:
        pa, pb = _xy(plan.get(pair[0])), _xy(plan.get(pair[1]))
        if pa and pb:
            right = _screen_right(pa, pb)
            if right:
                vec = (-right[0], -right[1])
    elif screen_dir == "to camera" and cam:
        vec = _norm2(cam[0] - origin[0], cam[1] - origin[1])
    elif screen_dir == "from camera" and cam:
        vec = _norm2(origin[0] - cam[0], origin[1] - cam[1])
    if not vec:
        return None
    end = (origin[0] + vec[0] * MOTION_LEN, origin[1] + vec[1] * MOTION_LEN)
    return (origin, end)


def _pts(poly: list[tuple[float, float]]) -> str:
    parts = []
    for x, y in poly:
        sx, sy = svg_xy(x, y)
        parts.append(f"{fmt(sx)},{fmt(sy)}")
    return " ".join(parts)


def render_frame_svg(state: dict[str, Any], viewbox: tuple[float, float, float, float]) -> str:
    vb_x, vb_y, vb_w, vb_h = viewbox
    frame = state.get("frame") or {}
    plan = list(state.get("plan") or [])
    obstacles = list(state.get("obstacles") or [])
    plan.sort(key=lambda p: str(p.get("id") or ""))
    obstacles.sort(key=lambda o: str(o.get("id") or ""))
    by_id = _by_id(plan)
    pair = _active_pair(state)
    side = _locked_side(state)
    shot_order = frame.get("shot_order") if frame.get("shot_order") is not None else ""
    shot_size = frame.get("shot_size") if frame.get("shot_size") is not None else ""
    axis_side = side or frame.get("axis_side") or ""

    parts: list[str] = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{fmt(vb_x)} {fmt(vb_y)} '
            f'{fmt(vb_w)} {fmt(vb_h)}">'
        ),
        "<g id=\"world\">",
    ]

    if pair and side in ("A", "B"):
        pa, pb = _xy(by_id.get(pair[0])), _xy(by_id.get(pair[1]))
        if pa and pb:
            fill_poly = _clip_halfplane(_view_rect_world(viewbox), pa, pb, side)
            if len(fill_poly) >= 3:
                fill = FILL_A if side == "A" else FILL_B
                parts.append(
                    f'<polygon fill="{fill}" stroke="none" points="{_pts(fill_poly)}"/>'
                )

    for obs in obstacles:
        try:
            ox, oy = float(obs["x"]), float(obs["y"])
            ow, oh = float(obs.get("w") or 0), float(obs.get("h") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        corners = [(ox, oy), (ox + ow, oy), (ox + ow, oy + oh), (ox, oy + oh)]
        oid = _xe(obs.get("id") or "obs")
        parts.append(
            f'<polygon id="obs-{oid}" fill="#888888" fill-opacity="0.55" '
            f'stroke="#444444" stroke-width="{fmt(STROKE_THIN)}" points="{_pts(corners)}"/>'
        )

    if pair:
        pa, pb = _xy(by_id.get(pair[0])), _xy(by_id.get(pair[1]))
        if pa and pb:
            clipped = _axis_line_clipped(pa, pb, viewbox)
            if clipped:
                (x1, y1), (x2, y2) = clipped
                s1 = svg_xy(x1, y1)
                s2 = svg_xy(x2, y2)
                parts.append(
                    f'<line id="axis" x1="{fmt(s1[0])}" y1="{fmt(s1[1])}" '
                    f'x2="{fmt(s2[0])}" y2="{fmt(s2[1])}" stroke="#000000" '
                    f'stroke-width="{fmt(STROKE_AXIS)}"/>'
                )

    for item in plan:
        sid = str(item.get("id") or "")
        if sid == CAM_ID:
            continue
        pt = _xy(item)
        if not pt:
            continue
        cx, cy = svg_xy(*pt)
        fx, fy = _facing_vec(item.get("facing"))
        tip = svg_xy(pt[0] + fx * FACE_LEN, pt[1] + fy * FACE_LEN)
        parts.append(
            f'<circle id="subj-{_xe(sid)}" cx="{fmt(cx)}" cy="{fmt(cy)}" '
            f'r="{fmt(SUBJECT_R)}" fill="#2c5282" stroke="#102a43" '
            f'stroke-width="{fmt(STROKE_THIN)}"/>'
        )
        parts.append(
            f'<line id="face-{_xe(sid)}" x1="{fmt(cx)}" y1="{fmt(cy)}" '
            f'x2="{fmt(tip[0])}" y2="{fmt(tip[1])}" stroke="#102a43" '
            f'stroke-width="{fmt(STROKE_THIN)}" marker-end="none"/>'
        )
        n = _norm2(fx, fy)
        if n:
            head = _arrow_head(pt[0] + fx * FACE_LEN, pt[1] + fy * FACE_LEN, n[0], n[1], 0.14)
            parts.append(f'<polygon fill="#102a43" stroke="none" points="{_pts(head)}"/>')
        lx, ly = svg_xy(pt[0], pt[1] + SUBJECT_R + 0.12)
        parts.append(
            f'<text x="{fmt(lx)}" y="{fmt(ly)}" font-size="0.28" '
            f'text-anchor="middle" font-family="monospace" fill="#111111">{_xe(sid)}</text>'
        )

    motion = _motion_world(state)
    if motion:
        (mx1, my1), (mx2, my2) = motion
        n = _norm2(mx2 - mx1, my2 - my1)
        s1 = svg_xy(mx1, my1)
        s2 = svg_xy(mx2, my2)
        parts.append(
            f'<line id="motion" x1="{fmt(s1[0])}" y1="{fmt(s1[1])}" '
            f'x2="{fmt(s2[0])}" y2="{fmt(s2[1])}" stroke="#c05621" '
            f'stroke-width="{fmt(STROKE_AXIS)}"/>'
        )
        if n:
            head = _arrow_head(mx2, my2, n[0], n[1], 0.18)
            parts.append(f'<polygon id="motion-head" fill="#c05621" stroke="none" points="{_pts(head)}"/>')

    cam = by_id.get(CAM_ID)
    cam_pt = _xy(cam) if cam else None
    if cam_pt:
        fx, fy = _facing_vec(cam.get("facing") if cam else 0)
        n = _norm2(fx, fy) or (0.0, 1.0)
        px, py = -n[1], n[0]
        tip = (cam_pt[0] + n[0] * CAM_LEN, cam_pt[1] + n[1] * CAM_LEN)
        left = (
            cam_pt[0] - n[0] * CAM_LEN * 0.45 + px * CAM_LEN * 0.38,
            cam_pt[1] - n[1] * CAM_LEN * 0.45 + py * CAM_LEN * 0.38,
        )
        right = (
            cam_pt[0] - n[0] * CAM_LEN * 0.45 - px * CAM_LEN * 0.38,
            cam_pt[1] - n[1] * CAM_LEN * 0.45 - py * CAM_LEN * 0.38,
        )
        parts.append(
            f'<polygon id="cam" fill="#111111" stroke="#000000" '
            f'stroke-width="{fmt(STROKE_THIN)}" points="{_pts([tip, left, right])}"/>'
        )
        lx, ly = svg_xy(cam_pt[0], cam_pt[1] - CAM_LEN * 0.7)
        parts.append(
            f'<text x="{fmt(lx)}" y="{fmt(ly)}" font-size="0.28" '
            f'text-anchor="middle" font-family="monospace" fill="#111111">cam</text>'
        )

    parts.append("</g>")
    parts.append("<g id=\"labels\" font-family=\"monospace\" font-size=\"0.32\" fill=\"#000000\">")
    tx = vb_x + 0.15
    ty = vb_y + 0.40
    parts.append(f'<text x="{fmt(tx)}" y="{fmt(ty)}">shot_order={_xe(shot_order)}</text>')
    parts.append(f'<text x="{fmt(tx)}" y="{fmt(ty + 0.40)}">shot_size={_xe(shot_size)}</text>')
    parts.append(f'<text x="{fmt(tx)}" y="{fmt(ty + 0.80)}">axis_side={_xe(axis_side)}</text>')
    parts.append("</g>")
    parts.append("</svg>")
    parts.append("")
    return "\n".join(parts)


def render_frame_png(
    state: dict[str, Any],
    viewbox: tuple[float, float, float, float],
    path: Path,
) -> None:
    from PIL import Image, ImageDraw

    vb_x, vb_y, vb_w, vb_h = viewbox
    w = max(1, int(round(vb_w * PX_PER_M)))
    h = max(1, int(round(vb_h * PX_PER_M)))
    img = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    draw = ImageDraw.Draw(img, "RGBA")

    def px(x: float, y: float) -> tuple[int, int]:
        sx, sy = svg_xy(x, y)
        return (int(round((sx - vb_x) * PX_PER_M)), int(round((sy - vb_y) * PX_PER_M)))

    def poly(points: list[tuple[float, float]]) -> list[tuple[int, int]]:
        return [px(x, y) for x, y in points]

    frame = state.get("frame") or {}
    plan = sorted(state.get("plan") or [], key=lambda p: str(p.get("id") or ""))
    obstacles = sorted(state.get("obstacles") or [], key=lambda o: str(o.get("id") or ""))
    by_id = _by_id(plan)
    pair = _active_pair(state)
    side = _locked_side(state)

    if pair and side in ("A", "B"):
        pa, pb = _xy(by_id.get(pair[0])), _xy(by_id.get(pair[1]))
        if pa and pb:
            fill_poly = _clip_halfplane(_view_rect_world(viewbox), pa, pb, side)
            if len(fill_poly) >= 3:
                fill = FILL_A_RGBA if side == "A" else FILL_B_RGBA
                draw.polygon(poly(fill_poly), fill=fill)

    for obs in obstacles:
        try:
            ox, oy = float(obs["x"]), float(obs["y"])
            ow, oh = float(obs.get("w") or 0), float(obs.get("h") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        corners = [(ox, oy), (ox + ow, oy), (ox + ow, oy + oh), (ox, oy + oh)]
        draw.polygon(poly(corners), fill=(136, 136, 136, 140), outline=(68, 68, 68, 255))

    if pair:
        pa, pb = _xy(by_id.get(pair[0])), _xy(by_id.get(pair[1]))
        if pa and pb:
            clipped = _axis_line_clipped(pa, pb, viewbox)
            if clipped:
                draw.line([px(*clipped[0]), px(*clipped[1])], fill=(0, 0, 0, 255), width=3)

    r_px = max(2, int(round(SUBJECT_R * PX_PER_M)))
    for item in plan:
        sid = str(item.get("id") or "")
        if sid == CAM_ID:
            continue
        pt = _xy(item)
        if not pt:
            continue
        cx, cy = px(*pt)
        draw.ellipse((cx - r_px, cy - r_px, cx + r_px, cy + r_px), fill=(44, 82, 130, 255), outline=(16, 42, 67, 255))
        fx, fy = _facing_vec(item.get("facing"))
        tip = (pt[0] + fx * FACE_LEN, pt[1] + fy * FACE_LEN)
        draw.line([px(*pt), px(*tip)], fill=(16, 42, 67, 255), width=2)
        n = _norm2(fx, fy)
        if n:
            head = _arrow_head(tip[0], tip[1], n[0], n[1], 0.14)
            draw.polygon(poly(head), fill=(16, 42, 67, 255))
        lx, ly = px(pt[0], pt[1] + SUBJECT_R + 0.12)
        draw.text((lx - 8, ly - 10), sid, fill=(17, 17, 17, 255))

    motion = _motion_world(state)
    if motion:
        (mx1, my1), (mx2, my2) = motion
        draw.line([px(mx1, my1), px(mx2, my2)], fill=(192, 86, 33, 255), width=3)
        n = _norm2(mx2 - mx1, my2 - my1)
        if n:
            head = _arrow_head(mx2, my2, n[0], n[1], 0.18)
            draw.polygon(poly(head), fill=(192, 86, 33, 255))

    cam = by_id.get(CAM_ID)
    cam_pt = _xy(cam) if cam else None
    if cam_pt:
        fx, fy = _facing_vec(cam.get("facing") if cam else 0)
        n = _norm2(fx, fy) or (0.0, 1.0)
        perp_x, perp_y = -n[1], n[0]
        tip = (cam_pt[0] + n[0] * CAM_LEN, cam_pt[1] + n[1] * CAM_LEN)
        left = (
            cam_pt[0] - n[0] * CAM_LEN * 0.45 + perp_x * CAM_LEN * 0.38,
            cam_pt[1] - n[1] * CAM_LEN * 0.45 + perp_y * CAM_LEN * 0.38,
        )
        right = (
            cam_pt[0] - n[0] * CAM_LEN * 0.45 - perp_x * CAM_LEN * 0.38,
            cam_pt[1] - n[1] * CAM_LEN * 0.45 - perp_y * CAM_LEN * 0.38,
        )
        draw.polygon(poly([tip, left, right]), fill=(17, 17, 17, 255), outline=(0, 0, 0, 255))
        lx, ly = px(cam_pt[0], cam_pt[1] - CAM_LEN * 0.7)
        draw.text((lx - 10, ly - 8), "cam", fill=(17, 17, 17, 255))

    tx, ty = px_hud(vb_x + 0.15, vb_y + 0.40, vb_x, vb_y)
    shot_order = frame.get("shot_order") if frame.get("shot_order") is not None else ""
    shot_size = frame.get("shot_size") if frame.get("shot_size") is not None else ""
    axis_side = side or frame.get("axis_side") or ""
    draw.text((tx, ty), f"shot_order={shot_order}", fill=(0, 0, 0, 255))
    draw.text((tx, ty + 14), f"shot_size={shot_size}", fill=(0, 0, 0, 255))
    draw.text((tx, ty + 28), f"axis_side={axis_side}", fill=(0, 0, 0, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, format="PNG")


def px_hud(svg_x: float, svg_y: float, vb_x: float, vb_y: float) -> tuple[int, int]:
    return (int(round((svg_x - vb_x) * PX_PER_M)), int(round((svg_y - vb_y) * PX_PER_M)))


def plan_filename(shot_order: int) -> str:
    return f"plan_{int(shot_order):02d}.svg"


def write_plans(
    out_dir: Path,
    space: dict[str, Any],
    frames: list[dict[str, Any]],
    *,
    png: bool = True,
) -> list[Path]:
    """Write plan_NN.svg (and optional PNG) for each frame. Shared viewBox. Returns SVG paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    states = replay_states(space, frames)
    if not states:
        raise ValueError("no frames to render")
    viewbox = scene_viewbox(states)
    written: list[Path] = []
    for state in states:
        order = int((state.get("frame") or {}).get("shot_order") or (len(written) + 1))
        svg_path = out_dir / plan_filename(order)
        svg_path.write_text(render_frame_svg(state, viewbox), encoding="utf-8", newline="\n")
        written.append(svg_path)
        if png:
            with suppress(Exception):
                render_frame_png(state, viewbox, out_dir / f"plan_{order:02d}.png")
    return written
