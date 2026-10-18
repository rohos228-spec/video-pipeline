"""Assign frames_space rows: sizes, angles, sides, screen_dir, roles, service shots.

``assign_blocking`` does not write the DB. If a §2 rule cannot hold, a service
shot is inserted (reestablish WS/EWS, turn, POV bridge, overhead). Axis side is
never flipped silently.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

from app.services.scene_space import rules as R
from app.services.scene_space.shot_size import (
    junction_verdict,
    size_at,
    size_from_ru,
    size_index,
    size_step,
)

_ROLE_SIZE = {
    "establish": "WS",
    "reestablish": "WS",
    "turning_point": "MCU",
    "reaction": "CU",
    "insert": "ECU",
    "beat": "MS",
}
_SCHEMA_KEYS = (
    "uuid",
    "scene_id",
    "shot_order",
    "axis_pair",
    "axis_side",
    "screen_pos",
    "screen_dir",
    "shot_size",
    "angle_v",
    "angle_h",
    "beat_role",
    "crossing_method",
    "space_delta_json",
    "reestablish_due",
    "manual",
)


def assign_blocking(
    scene_state: dict,
    bits: list[dict],
    frames: list[dict],
) -> list[dict]:
    """Return frames_space rows for a scene. No DB writes."""
    space = _space_of(scene_state)
    scene_id = _scene_id_of(scene_state, space, frames)
    desired = _desired_shots(scene_id, space, bits or [], frames or [])
    if not desired:
        return []

    plan0 = R.ensure_cam(list(space.get("plan") or _plan_from_desired(desired)))
    locked = R.locked_sides_from_space(space)
    out: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = [dict(p) for p in plan0]
    prev_plan = [dict(p) for p in plan]
    prev_row: dict[str, Any] | None = None
    close_streak = 0
    due_pending = False
    scene_dir: str | None = None
    svc_seq = 0
    tp_done = False

    def _svc_uuid(kind: str, anchor: str) -> str:
        nonlocal svc_seq
        svc_seq += 1
        blob = f"svc|{scene_id}|{anchor}|{kind}|{svc_seq}"
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]

    for idx, want in enumerate(desired):
        delta = dict(want.get("space_delta_json") or {})
        new_plan = R.apply_delta(plan, delta)
        new_plan = R.ensure_cam(new_plan)
        pair = want.get("pair")
        if pair is None:
            pair = _pair_from_plan(want.get("subjects") or [], new_plan)
        on_screen = want.get("on_screen")
        if not on_screen and pair:
            on_screen = _on_screen_for_size(want, pair)

        if due_pending:
            try:
                pay_size = size_from_ru(str(want.get("shot_size") or _ROLE_SIZE["reestablish"]))
            except ValueError:
                pay_size = "WS"
            if not R.pays_reestablish(pay_size):
                row = _emit_reestablish(
                    scene_id=scene_id,
                    uuid=_svc_uuid("reestablish", str(want.get("uuid") or idx)),
                    plan=plan,
                    pair=pair,
                    locked=locked,
                    screen_dir=scene_dir or "none",
                    on_screen=list(pair) if pair else None,
                    prev_row=prev_row,
                )
                out.append(row)
                prev_row = row
                prev_plan = [dict(p) for p in plan]
                due_pending = False
                close_streak = 0
            else:
                want["shot_size"] = pay_size
                if want.get("beat_role") in (None, "", "beat"):
                    want["beat_role"] = "reestablish"
                due_pending = False

        pair, locked, new_plan, delta, prev_row, out, scene_dir = _resolve_side(
            scene_id=scene_id,
            want=want,
            pair=pair,
            plan=new_plan,
            delta=delta,
            locked=locked,
            prev_row=prev_row,
            out=out,
            scene_dir=scene_dir,
            svc_uuid=lambda kind: _svc_uuid(kind, str(want.get("uuid") or idx)),
        )

        motion_dir = R.compute_screen_dir(prev_plan, new_plan, pair)
        if scene_dir is None:
            if motion_dir != "none":
                scene_dir = motion_dir
            row_dir = motion_dir
        elif R.dirs_are_opposite(scene_dir, motion_dir):
            if not R.turn_marked(delta):
                turn_id = _turn_subject(pair, delta, motion_dir)
                turn_delta: dict[str, Any] = {"turn": {"subject": turn_id}} if turn_id else {"turn": True}
                facing = _facing_flip(new_plan, turn_id)
                if facing is not None and turn_id:
                    turn_delta[turn_id] = {"facing": facing}
                row = _row(
                    scene_id=scene_id,
                    uuid=_svc_uuid("turn", str(want.get("uuid") or idx)),
                    plan=new_plan,
                    pair=pair,
                    locked=locked,
                    shot_size=_safe_size(want.get("shot_size"), "MS", prev_row, pair),
                    angle_h=R.angle_h_step(str(prev_row.get("angle_h") or "frontal"), 2) if prev_row else "profile",
                    angle_v=R.canon_angle_v(want.get("angle_v")),
                    beat_role="insert",
                    crossing_method=None,
                    screen_dir=scene_dir,
                    on_screen=on_screen,
                    delta=turn_delta,
                    due=0,
                    manual=0,
                    prev_row=prev_row,
                )
                out.append(row)
                prev_row = row
            scene_dir = motion_dir
            row_dir = motion_dir
        else:
            row_dir = scene_dir if motion_dir == "none" else scene_dir

        same_object = _same_object(prev_row, pair, on_screen)
        size = _pick_size(want, prev_row, same_object)
        angle_h, angle_v, cam_fix = _pick_angles(
            want, prev_row, same_object, new_plan, pair, locked
        )
        if cam_fix is not None:
            cam_delta = dict(delta.get("cam") or {})
            cam_delta.update({"x": cam_fix[0], "y": cam_fix[1]})
            delta["cam"] = cam_delta
            new_plan = R.apply_delta(new_plan, {"cam": cam_delta})

        role = R.canon_beat_role(want.get("beat_role"))
        if want.get("turning_point") or _want_tp(want, bits or [], idx):
            role = "turning_point"
            tp_done = True
        if idx == 0 and role == "beat" and not frames:
            role = "establish"

        if (
            same_object
            and prev_row
            and junction_verdict(prev_row["shot_size"], size) == "warn_smash"
            and role != "insert"
        ):
            size = _bridge_size(prev_row["shot_size"], size)

        if R.is_close_size(size):
            close_streak += 1
        else:
            close_streak = 0

        moved = R.subject_moved(prev_plan, new_plan)
        roster = R.roster_changed(prev_plan, new_plan)
        pair_changed = bool(
            prev_row
            and pair
            and prev_row.get("axis_pair")
            and prev_row.get("axis_pair") != f"{pair[0]}|{pair[1]}"
        )
        due = 1 if R.reestablish_trigger(
            moved=moved,
            roster=roster,
            pair_changed=pair_changed,
            close_streak=close_streak,
        ) else 0

        crossing = R.canon_crossing(want.get("crossing_method"))
        row = _row(
            scene_id=scene_id,
            uuid=str(want.get("uuid") or _svc_uuid("shot", str(idx))),
            plan=new_plan,
            pair=pair,
            locked=locked,
            shot_size=size,
            angle_h=angle_h,
            angle_v=angle_v,
            beat_role=role,
            crossing_method=crossing,
            screen_dir=row_dir,
            on_screen=on_screen,
            delta=delta,
            due=due,
            manual=1 if want.get("manual") else 0,
            prev_row=prev_row,
            preserve=want if want.get("manual") else None,
        )
        out.append(row)
        if due:
            due_pending = True
        prev_row = row
        prev_plan = [dict(p) for p in new_plan]
        plan = new_plan

    if due_pending and prev_row is not None:
        pair = R.parse_pair(prev_row.get("axis_pair"))
        row = _emit_reestablish(
            scene_id=scene_id,
            uuid=_svc_uuid("reestablish", "tail"),
            plan=plan,
            pair=pair,
            locked=locked,
            screen_dir=scene_dir or prev_row.get("screen_dir") or "none",
            on_screen=None,
            prev_row=prev_row,
        )
        out.append(row)

    if not tp_done:
        _stamp_turning_point(out, bits or [])

    for order, row in enumerate(out, start=1):
        row["shot_order"] = order
        extra = [key for key in row if key not in _SCHEMA_KEYS]
        for key in extra:
            row.pop(key, None)
    return out


def _space_of(scene_state: Mapping[str, Any] | None) -> dict[str, Any]:
    if not scene_state:
        return {}
    raw = scene_state.get("space_json")
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = {}
        if isinstance(parsed, dict):
            return parsed
    if isinstance(raw, dict):
        return dict(raw)
    if "plan" in scene_state or "axes" in scene_state or "value_charge" in scene_state:
        return dict(scene_state)
    return {}


def _scene_id_of(
    scene_state: Mapping[str, Any] | None,
    space: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
) -> str:
    for blob in (scene_state, space, frames[0] if frames else None):
        if isinstance(blob, Mapping):
            sid = blob.get("scene_id")
            if sid:
                return str(sid)
    return "fix:unknown"


def _plan_from_desired(desired: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    ids: list[str] = []
    for want in desired:
        for sid in want.get("subjects") or []:
            if sid and sid not in ids and sid != "cam":
                ids.append(str(sid))
    if not ids:
        ids = ["c01", "c02"]
    plan: list[dict[str, Any]] = []
    n = len(ids)
    for i, sid in enumerate(ids):
        x = -1.0 + (2.0 * i / max(n - 1, 1)) if n > 1 else 0.0
        plan.append({"id": sid, "x": x, "y": 2.0, "facing": 180.0})
    plan.append({"id": "cam", "x": 0.0, "y": -2.0, "facing": 0.0})
    return plan


def _as_delta(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return dict(raw) if isinstance(raw, dict) else {}


def _as_int_manual(raw: Any) -> int:
    if raw in (True, 1, "1"):
        return 1
    return 0


def _subjects_of(blob: Mapping[str, Any]) -> list[str]:
    for key in ("subjects", "ids", "персонажи", "on_screen", "screen_ids"):
        val = blob.get(key)
        if isinstance(val, (list, tuple)):
            return [str(x) for x in val if x]
        if isinstance(val, str) and val.strip():
            parsed = R.parse_pair(val)
            if parsed:
                return [parsed[0], parsed[1]]
            return [p for p in val.replace(",", " ").split() if p]
    parsed = R.parse_pair(blob.get("axis_pair") or blob.get("pair"))
    if parsed:
        return [parsed[0], parsed[1]]
    return []


def _desired_shots(
    scene_id: str,
    space: Mapping[str, Any],
    bits: Sequence[Mapping[str, Any]],
    frames: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    bit_list = [dict(b) for b in bits if isinstance(b, Mapping)]
    frame_list = [dict(f) for f in frames if isinstance(f, Mapping)]
    n = max(len(bit_list), len(frame_list))
    if n == 0:
        return []
    out: list[dict[str, Any]] = []
    for i in range(n):
        bit = bit_list[i] if i < len(bit_list) else {}
        frame = frame_list[i] if i < len(frame_list) else {}
        attrs = frame.get("attrs") if isinstance(frame.get("attrs"), dict) else {}
        uuid = (
            frame.get("uuid")
            or bit.get("uuid")
            or bit.get("frame_uuid")
            or hashlib.sha256(f"frame|{scene_id}|{i}".encode("utf-8")).hexdigest()[:24]
        )
        role = R.canon_beat_role(
            bit.get("beat_role")
            or bit.get("role")
            or bit.get("роль")
            or frame.get("beat_role")
        )
        if bit.get("turning_point") or bit.get("is_turning_point") or bit.get("точка_поворота"):
            role = "turning_point"
        size_raw = (
            frame.get("shot_size")
            or attrs.get("крупность")
            or attrs.get("shot_size")
            or frame.get("крупность")
            or bit.get("shot_size")
            or bit.get("крупность")
            or _ROLE_SIZE.get(role, "MS")
        )
        try:
            shot_size = size_from_ru(str(size_raw))
        except ValueError:
            shot_size = _ROLE_SIZE.get(role, "MS")
        subjects = _subjects_of(bit) or _subjects_of(frame) or _subjects_of(attrs)
        pair = R.parse_pair(
            bit.get("axis_pair")
            or frame.get("axis_pair")
            or (subjects[:2] if len(subjects) >= 2 else None)
        )
        out.append(
            {
                "uuid": str(uuid),
                "shot_size": shot_size,
                "angle_h": frame.get("angle_h") or bit.get("angle_h") or attrs.get("angle_h"),
                "angle_v": frame.get("angle_v") or bit.get("angle_v") or attrs.get("angle_v"),
                "beat_role": role,
                "crossing_method": frame.get("crossing_method") or bit.get("crossing_method"),
                "space_delta_json": _as_delta(
                    frame.get("space_delta_json") or bit.get("space_delta_json")
                ),
                "manual": _as_int_manual(frame.get("manual") or bit.get("manual")),
                "subjects": subjects,
                "pair": pair,
                "on_screen": _subjects_of(
                    {"on_screen": bit.get("on_screen") or frame.get("on_screen")}
                )
                or None,
                "turning_point": role == "turning_point",
                "axis_side": frame.get("axis_side") or bit.get("axis_side"),
                "screen_pos": frame.get("screen_pos"),
                "screen_dir": frame.get("screen_dir"),
            }
        )
    if out and out[0]["beat_role"] == "beat" and not frame_list:
        out[0]["beat_role"] = "establish"
        if out[0]["shot_size"] in {"MS", "MCU"}:
            out[0]["shot_size"] = "WS"
    return out


def _pair_from_plan(
    subjects: Sequence[str],
    plan: Sequence[Mapping[str, Any]],
) -> tuple[str, str] | None:
    bodies = [str(s) for s in subjects if s and s != "cam"]
    if len(bodies) >= 2:
        return R.normalize_pair(bodies[0], bodies[1])
    present = [i for i in sorted(R.body_ids(plan))]
    if len(bodies) == 1 and present:
        others = [i for i in present if i != bodies[0]]
        if others:
            return R.normalize_pair(bodies[0], others[0])
    if len(present) >= 2:
        return R.normalize_pair(present[0], present[1])
    return None


def _on_screen_for_size(
    want: Mapping[str, Any],
    pair: tuple[str, str],
) -> list[str]:
    try:
        idx = size_index(str(want.get("shot_size") or "MS"))
    except ValueError:
        idx = size_index("MS")
    if idx <= size_index("FS"):
        return [pair[0], pair[1]]
    if idx >= size_index("MCU"):
        subj = want.get("subjects") or []
        if subj:
            sid = str(subj[0])
            if sid in pair:
                return [sid]
        return [pair[0]]
    return [pair[0], pair[1]]


def _same_object(
    prev_row: Mapping[str, Any] | None,
    pair: tuple[str, str] | None,
    on_screen: Sequence[str] | None,
) -> bool:
    if prev_row is None:
        return False
    if pair and prev_row.get("axis_pair") == f"{pair[0]}|{pair[1]}":
        return True
    prev_pos = prev_row.get("screen_pos")
    prev_ids: set[str] = set()
    if isinstance(prev_pos, str) and prev_pos:
        try:
            prev_ids = set(json.loads(prev_pos))
        except json.JSONDecodeError:
            prev_ids = set()
    elif isinstance(prev_pos, dict):
        prev_ids = set(prev_pos)
    cur_ids = set(on_screen or (list(pair) if pair else []))
    return bool(prev_ids & cur_ids)


def _pick_size(
    want: Mapping[str, Any],
    prev_row: Mapping[str, Any] | None,
    same_object: bool,
) -> str:
    role = R.canon_beat_role(want.get("beat_role"))
    try:
        desired = size_from_ru(str(want.get("shot_size") or _ROLE_SIZE.get(role, "MS")))
    except ValueError:
        desired = _ROLE_SIZE.get(role, "MS")
    if want.get("manual") and prev_row is None:
        return desired
    if not same_object or prev_row is None:
        return desired
    prev_size = str(prev_row["shot_size"])
    verdict = junction_verdict(prev_size, desired)
    if verdict == "error_same":
        for step in (2, -2, 3, -3, 1, -1):
            cand = size_step(prev_size, step)
            if cand == prev_size:
                continue
            nv = junction_verdict(prev_size, cand)
            if nv == "error_same":
                continue
            if nv == "warn_smash" and role != "insert":
                continue
            return cand
        return size_step(prev_size, 2)
    if verdict == "warn_smash" and role != "insert":
        return _bridge_size(prev_size, desired)
    return desired


def _bridge_size(prev_size: str, desired: str) -> str:
    pi = size_index(prev_size)
    di = size_index(desired)
    direction = 1 if di > pi else -1
    cand = size_at(pi + 2 * direction)
    if cand != prev_size and junction_verdict(prev_size, cand) != "error_same":
        return cand
    return size_step(prev_size, 2 * direction)


def _safe_size(
    raw: Any,
    fallback: str,
    prev_row: Mapping[str, Any] | None,
    pair: tuple[str, str] | None,
) -> str:
    try:
        size = size_from_ru(str(raw or fallback))
    except ValueError:
        size = fallback
    if prev_row and pair and prev_row.get("axis_pair") == f"{pair[0]}|{pair[1]}":
        if junction_verdict(str(prev_row["shot_size"]), size) == "error_same":
            return size_step(str(prev_row["shot_size"]), 2)
    return size


def _pick_angles(
    want: Mapping[str, Any],
    prev_row: Mapping[str, Any] | None,
    same_object: bool,
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str] | None,
    locked: Mapping[str, str],
) -> tuple[str, str, tuple[float, float] | None]:
    h = R.canon_angle_h(want.get("angle_h"))
    v = R.canon_angle_v(want.get("angle_v"))
    if want.get("manual"):
        return h, v, None
    if not same_object or prev_row is None:
        return h, v, None
    prev_h = str(prev_row.get("angle_h") or "frontal")
    prev_v = str(prev_row.get("angle_v") or "eye-level")
    cur_cam = R.plan_cam(plan)
    look = R.look_at_xy(plan, pair)
    jumped = R.angle_jump(prev_h, prev_v, h, v)
    cam_fix = None
    if jumped:
        h = R.angle_h_step(prev_h, 2)
        if R.names_under_30(prev_h, prev_v, h, v):
            vi = list(R.ANGLE_V).index(R.canon_angle_v(v))
            v = R.ANGLE_V[min(len(R.ANGLE_V) - 1, vi + 2)]
    if cur_cam is not None and look is not None and pair is not None:
        key = f"{pair[0]}|{pair[1]}"
        side = locked.get(key)
        a = None
        b = None
        for row in plan:
            if str(row.get("id")) == pair[0]:
                a = row
            elif str(row.get("id")) == pair[1]:
                b = row
        if a is not None and b is not None and side in {"A", "B"}:
            cam_fix = R.cam_on_locked_side(
                (float(a["x"]), float(a["y"])),
                (float(b["x"]), float(b["y"])),
                cur_cam,
                side,
                degrees_list=(35.0, -35.0, 50.0, -50.0, 90.0) if jumped else (0.0, 35.0, -35.0),
                look=look,
            )
            same_cam = (
                abs(cam_fix[0] - float(cur_cam["x"])) <= 1e-9
                and abs(cam_fix[1] - float(cur_cam["y"])) <= 1e-9
            )
            if same_cam:
                cam_fix = None
    return h, v, cam_fix


def _resolve_side(
    *,
    scene_id: str,
    want: Mapping[str, Any],
    pair: tuple[str, str] | None,
    plan: list[dict[str, Any]],
    delta: dict[str, Any],
    locked: dict[str, str],
    prev_row: dict[str, Any] | None,
    out: list[dict[str, Any]],
    scene_dir: str | None,
    svc_uuid,
) -> tuple:
    if pair is None:
        return pair, locked, plan, delta, prev_row, out, scene_dir
    key = f"{pair[0]}|{pair[1]}"
    try:
        side = R.side_for_plan(plan, pair)
    except R.DegenerateAxisError:
        prefer = locked.get(key) or "A"
        row = _overhead_row(
            scene_id=scene_id,
            uuid=svc_uuid("overhead"),
            plan=plan,
            pair=pair,
            locked=locked,
            screen_dir=scene_dir or "none",
            prev_row=prev_row,
            new_side=prefer,
        )
        out.append(row)
        prev_row = row
        a = b = cam = None
        for item in plan:
            if str(item.get("id")) == pair[0]:
                a = item
            elif str(item.get("id")) == pair[1]:
                b = item
            elif str(item.get("id")) == "cam":
                cam = item
        if a is not None and b is not None and cam is not None:
            nudged = R.cam_on_locked_side(
                (float(a["x"]), float(a["y"])),
                (float(b["x"]), float(b["y"])),
                cam,
                prefer,
            )
            delta = dict(delta)
            delta["cam"] = {"x": nudged[0], "y": nudged[1]}
            plan = R.apply_delta(plan, {"cam": delta["cam"]})
        locked[key] = prefer
        return pair, locked, plan, delta, prev_row, out, scene_dir

    existing = locked.get(key)
    if existing is None:
        locked[key] = side
        return pair, locked, plan, delta, prev_row, out, scene_dir
    if side == existing:
        return pair, locked, plan, delta, prev_row, out, scene_dir

    method = R.canon_crossing(want.get("crossing_method"))
    if method is None and _cam_crossed(delta, existing, side):
        method = "camera_move"
    if method is None:
        kind = _crossing_kind(want)
        row = _crossing_row(
            kind=kind,
            scene_id=scene_id,
            uuid=svc_uuid(kind),
            plan=plan,
            pair=pair,
            new_side=side,
            locked=locked,
            screen_dir=scene_dir or "none",
            prev_row=prev_row,
            want=want,
        )
        out.append(row)
        prev_row = row
        method = kind
    locked[key] = side
    if method and isinstance(want, dict):
        want["crossing_method"] = method
    return pair, locked, plan, delta, prev_row, out, scene_dir


def _cam_crossed(delta: Mapping[str, Any], old_side: str, new_side: str) -> bool:
    if old_side == new_side:
        return False
    cam = delta.get("cam")
    return isinstance(cam, Mapping) and ("x" in cam or "y" in cam)


def _crossing_kind(want: Mapping[str, Any]) -> str:
    if R.canon_angle_v(want.get("angle_v")) == "overhead":
        return "overhead"
    try:
        if size_index(str(want.get("shot_size") or "MS")) >= size_index("MCU"):
            return "pov"
    except ValueError:
        pass
    return "reestablish"


def _crossing_row(
    *,
    kind: str,
    scene_id: str,
    uuid: str,
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str],
    new_side: str,
    locked: Mapping[str, str],
    screen_dir: str,
    prev_row: Mapping[str, Any] | None,
    want: Mapping[str, Any],
) -> dict[str, Any]:
    if kind == "overhead":
        return _overhead_row(
            scene_id=scene_id,
            uuid=uuid,
            plan=plan,
            pair=pair,
            locked=locked,
            screen_dir=screen_dir,
            prev_row=prev_row,
            new_side=new_side,
        )
    prev_h = str(prev_row.get("angle_h") or "frontal") if prev_row else "frontal"
    if kind == "pov":
        size = "CU"
        role = "insert"
        angle_h = "over-the-shoulder"
        if R.names_under_30(prev_h, str(prev_row.get("angle_v") or "eye-level") if prev_row else "eye-level", angle_h, "eye-level"):
            angle_h = R.angle_h_step(prev_h, 2)
        angle_v = "eye-level"
        on_screen = list(want.get("subjects") or [pair[0]])[:1] or [pair[0]]
    else:
        size = "WS"
        role = "reestablish"
        angle_h = R.angle_h_step(prev_h, 2) if prev_row else "frontal"
        angle_v = "eye-level"
        on_screen = [pair[0], pair[1]]
    if prev_row and junction_verdict(str(prev_row["shot_size"]), size) == "error_same":
        size = size_step(str(prev_row["shot_size"]), 2)
        if not R.pays_reestablish(size) and kind == "reestablish":
            size = "EWS" if str(prev_row["shot_size"]) == "WS" else "WS"
    return _row(
        scene_id=scene_id,
        uuid=uuid,
        plan=plan,
        pair=pair,
        locked={**dict(locked), f"{pair[0]}|{pair[1]}": new_side},
        shot_size=size,
        angle_h=angle_h,
        angle_v=angle_v,
        beat_role=role,
        crossing_method=kind,
        screen_dir=screen_dir,
        on_screen=on_screen,
        delta={"service_shot": kind},
        due=0,
        manual=0,
        prev_row=prev_row,
    )


def _overhead_row(
    *,
    scene_id: str,
    uuid: str,
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str] | None,
    locked: Mapping[str, str],
    screen_dir: str,
    prev_row: Mapping[str, Any] | None,
    new_side: str | None = None,
) -> dict[str, Any]:
    size = "WS"
    if prev_row and junction_verdict(str(prev_row["shot_size"]), size) == "error_same":
        size = "EWS"
    lock_map = dict(locked)
    if pair and new_side in {"A", "B"}:
        lock_map[f"{pair[0]}|{pair[1]}"] = new_side
    return _row(
        scene_id=scene_id,
        uuid=uuid,
        plan=plan,
        pair=pair,
        locked=lock_map,
        shot_size=size,
        angle_h=R.angle_h_step(str(prev_row.get("angle_h") or "frontal"), 2) if prev_row else "frontal",
        angle_v="overhead",
        beat_role="insert",
        crossing_method="overhead",
        screen_dir=screen_dir,
        on_screen=list(pair) if pair else None,
        delta={"service_shot": "overhead"},
        due=0,
        manual=0,
        prev_row=prev_row,
        axis_side_override=new_side,
    )


def _emit_reestablish(
    *,
    scene_id: str,
    uuid: str,
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str] | None,
    locked: Mapping[str, str],
    screen_dir: str,
    on_screen: Sequence[str] | None,
    prev_row: Mapping[str, Any] | None,
) -> dict[str, Any]:
    size = "WS"
    if prev_row and str(prev_row.get("shot_size")) == "WS":
        size = "EWS"
    elif prev_row and junction_verdict(str(prev_row["shot_size"]), "WS") == "error_same":
        size = "EWS"
    prev_h = str(prev_row.get("angle_h") or "frontal") if prev_row else "frontal"
    return _row(
        scene_id=scene_id,
        uuid=uuid,
        plan=plan,
        pair=pair,
        locked=locked,
        shot_size=size,
        angle_h=R.angle_h_step(prev_h, 2) if prev_row else "frontal",
        angle_v="eye-level",
        beat_role="reestablish",
        crossing_method=None,
        screen_dir=screen_dir,
        on_screen=on_screen or (list(pair) if pair else None),
        delta={"service_shot": "reestablish"},
        due=0,
        manual=0,
        prev_row=prev_row,
    )


def _row(
    *,
    scene_id: str,
    uuid: str,
    plan: Sequence[Mapping[str, Any]],
    pair: tuple[str, str] | None,
    locked: Mapping[str, str],
    shot_size: str,
    angle_h: str,
    angle_v: str,
    beat_role: str,
    crossing_method: str | None,
    screen_dir: str,
    on_screen: Sequence[str] | None,
    delta: Mapping[str, Any] | None,
    due: int,
    manual: int,
    prev_row: Mapping[str, Any] | None,
    preserve: Mapping[str, Any] | None = None,
    axis_side_override: str | None = None,
) -> dict[str, Any]:
    axis_pair = f"{pair[0]}|{pair[1]}" if pair else None
    side: str | None = axis_side_override
    if pair and side is None:
        try:
            computed = R.side_for_plan(plan, pair)
        except R.DegenerateAxisError:
            computed = locked.get(axis_pair or "")
        side = computed or locked.get(axis_pair or "")
    screen_pos = "{}"
    if pair:
        screen_pos = R.compute_screen_pos(plan, pair, on_screen)
    if preserve and preserve.get("manual"):
        if preserve.get("shot_size"):
            try:
                shot_size = size_from_ru(str(preserve.get("shot_size")))
            except ValueError:
                pass
        if preserve.get("angle_h"):
            angle_h = R.canon_angle_h(preserve.get("angle_h"))
        if preserve.get("angle_v"):
            angle_v = R.canon_angle_v(preserve.get("angle_v"))
        if preserve.get("axis_side") in {"A", "B"}:
            side = str(preserve.get("axis_side"))
        if preserve.get("screen_pos"):
            sp = preserve.get("screen_pos")
            screen_pos = sp if isinstance(sp, str) else json.dumps(sp, ensure_ascii=False)
        if preserve.get("screen_dir"):
            screen_dir = R.canon_screen_dir(preserve.get("screen_dir"))
        if preserve.get("beat_role"):
            beat_role = R.canon_beat_role(preserve.get("beat_role"))
        if "crossing_method" in preserve:
            crossing_method = R.canon_crossing(preserve.get("crossing_method"))
    return {
        "uuid": str(uuid),
        "scene_id": scene_id,
        "shot_order": 0,
        "axis_pair": axis_pair,
        "axis_side": side,
        "screen_pos": screen_pos,
        "screen_dir": R.canon_screen_dir(screen_dir),
        "shot_size": shot_size,
        "angle_v": R.canon_angle_v(angle_v),
        "angle_h": R.canon_angle_h(angle_h),
        "beat_role": R.canon_beat_role(beat_role),
        "crossing_method": crossing_method,
        "space_delta_json": dict(delta or {}),
        "reestablish_due": 1 if due else 0,
        "manual": 1 if manual else 0,
    }


def _turn_subject(
    pair: tuple[str, str] | None,
    delta: Mapping[str, Any],
    _motion_dir: str,
) -> str | None:
    if R.turn_marked(delta):
        turn = delta.get("turn")
        if isinstance(turn, Mapping) and turn.get("subject"):
            return str(turn["subject"])
        if delta.get("turn_subject"):
            return str(delta["turn_subject"])
    if pair:
        return pair[0]
    return None


def _facing_flip(plan: Sequence[Mapping[str, Any]], sid: str | None) -> float | None:
    if not sid:
        return None
    for row in plan:
        if str(row.get("id")) == sid and "facing" in row:
            try:
                return float(row.get("facing") or 0.0) + 180.0
            except (TypeError, ValueError):
                return 180.0
    return None


def _want_tp(
    want: Mapping[str, Any],
    bits: Sequence[Mapping[str, Any]],
    idx: int,
) -> bool:
    if want.get("turning_point") or R.canon_beat_role(want.get("beat_role")) == "turning_point":
        return True
    if idx < len(bits) and R.canon_beat_role(
        bits[idx].get("beat_role") if isinstance(bits[idx], Mapping) else None
    ) == "turning_point":
        return True
    return False


def _stamp_turning_point(rows: list[dict[str, Any]], bits: Sequence[Mapping[str, Any]]) -> None:
    if any(r.get("beat_role") == "turning_point" for r in rows):
        return
    content = [
        r
        for r in rows
        if r.get("beat_role") not in {"insert", "reestablish"}
        and not (r.get("space_delta_json") or {}).get("service_shot")
    ]
    if not content:
        content = rows
    if not content:
        return
    target = content[len(content) // 2]
    target["beat_role"] = "turning_point"
