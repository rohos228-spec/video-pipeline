"""Scene-space validator (CANON §6). One function, frozen rule_id list."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from app.generation_options import OUTSEE_PROMPT_MAX_CHARS

Issue = dict[str, Any]

SHOT_SIZES = ("EWS", "WS", "FS", "MWS", "MS", "MCU", "CU", "ECU")
SIZE_INDEX = {code: i for i, code in enumerate(SHOT_SIZES)}
_SIZE_OK_PAIRS = frozenset({frozenset({"CU", "ECU"}), frozenset({"EWS", "WS"})})
_LEGACY_SIZE = {"VLS": "EWS", "LS": "WS", "INSERT": "ECU"}

ANGLE_H = (
    "frontal",
    "three-quarter",
    "profile",
    "rear three-quarter",
    "over-the-shoulder",
    "from behind",
)
ANGLE_V = (
    "eye-level",
    "slight low",
    "low",
    "slight high",
    "high",
    "overhead",
    "dutch",
)
SCREEN_DIRS = ("L→R", "R→L", "to camera", "from camera", "none")
BEAT_ROLES = (
    "beat",
    "turning_point",
    "reaction",
    "insert",
    "establish",
    "reestablish",
)
CROSSING_METHODS = ("pov", "reestablish", "overhead", "camera_move")
FRAME_SPACE_FIELDS = (
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
_DIR_OPP = {
    "L→R": "R→L",
    "R→L": "L→R",
    "to camera": "from camera",
    "from camera": "to camera",
}
_DELTA_META = frozenset({"turn", "turn_subject", "monotony"})
_STYLE_MARKERS = ("PROMPT:", "PROMPT ", "Стиль:", "Style:")
_NEG_RE = re.compile(r"NEGATIVE\s+PROMPT|NEGATIVE:", re.IGNORECASE)
_TREE_PHRASE_RE = re.compile(r"«([^»]+)»")
_MOVE_M = 1.0
_EPS = 1e-12
_MS_INDEX = SIZE_INDEX["MS"]

_FALLBACK_FORBIDDEN = (
    "всё изменилось в один момент",
    "казалось…, но…",
    "история, которая…",
    "вы не поверите",
    "секрет прост",
    "это меняет всё",
    "давайте разберёмся",
)


class DegenerateAxisError(ValueError):
    """Camera on the action axis (cross == 0). Local fallback if stream 1 is absent."""


def _bind_geom() -> tuple[Any, Any, Any, type[Exception]]:
    try:
        from app.services.scene_space.geom import apply_deltas as ad
        from app.services.scene_space.geom import axis_side as side
        from app.services.scene_space.geom import normalize_pair as npair

        err: type[Exception] = DegenerateAxisError
        try:
            from app.services.scene_space.errors import DegenerateAxisError as geom_err

            err = geom_err
        except ImportError:
            pass
        return npair, side, ad, err
    except ImportError:
        pass
    try:
        from app.services.scene_space.store import apply_deltas as ad
        from app.services.scene_space.store import axis_side as side
        from app.services.scene_space.store import normalize_pair as npair

        err = DegenerateAxisError
        try:
            from app.services.scene_space.errors import DegenerateAxisError as geom_err

            err = geom_err
        except ImportError:
            pass
        return npair, side, ad, err
    except ImportError:
        return _normalize_pair, _axis_side, _apply_deltas, DegenerateAxisError


def _normalize_pair(a: str, b: str) -> tuple[str, str]:
    lo, hi = sorted((str(a), str(b)))
    return lo, hi


def _xy(point: Any) -> tuple[float, float]:
    if isinstance(point, Mapping):
        return float(point["x"]), float(point["y"])
    if hasattr(point, "x") and hasattr(point, "y"):
        return float(point.x), float(point.y)
    return float(point[0]), float(point[1])


def _axis_side(a_xy: Any, b_xy: Any, cam_xy: Any) -> str:
    ax, ay = _xy(a_xy)
    bx, by = _xy(b_xy)
    cx, cy = _xy(cam_xy)
    cross = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if cross == 0:
        raise DegenerateAxisError("camera on axis (cross==0)")
    return "A" if cross > 0 else "B"


def _apply_deltas(plan: list[dict], deltas_in_order: list[dict]) -> list[dict]:
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
            if key in _DELTA_META:
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


normalize_pair, axis_side, apply_deltas, GeomDegenerate = _bind_geom()


def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def forbidden_phrases_path() -> Path:
    return repo_root() / "prompts" / "blocks" / "forbidden_phrases" / "ai_cliches_ru.md"


def load_forbidden_phrases(path: Path | None = None) -> list[str]:
    """Phrases in «ёлочки» and after «Не использовать штампы:» (CONTRACTS §4)."""
    target = path if path is not None else forbidden_phrases_path()
    text = ""
    if target.is_file():
        text = target.read_text(encoding="utf-8")
    phrases: list[str] = []
    if text:
        phrases.extend(m.strip() for m in _TREE_PHRASE_RE.findall(text) if m.strip())
        marker = "не использовать штампы:"
        lower = text.casefold()
        idx = lower.find(marker)
        if idx >= 0:
            tail = text[idx + len(marker) :]
            for m in _TREE_PHRASE_RE.findall(tail):
                if m.strip():
                    phrases.append(m.strip())
    if not phrases:
        phrases.extend(_FALLBACK_FORBIDDEN)
    out: list[str] = []
    seen: set[str] = set()
    for p in phrases:
        key = p.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    return out


def split_prompt_positive(text: str) -> str:
    """Positive part: everything before NEGATIVE PROMPT / NEGATIVE:."""
    if not text:
        return ""
    match = _NEG_RE.search(text)
    if match is None:
        return text
    return text[: match.start()]


def has_style_marker(text: str) -> bool:
    positive = split_prompt_positive(text or "")
    low = positive.casefold()
    return any(marker.casefold() in low for marker in _STYLE_MARKERS)


def has_negative_marker(text: str) -> bool:
    return _NEG_RE.search(text or "") is not None


def issues_exit_code(issues: Sequence[Mapping[str, Any]]) -> int:
    if any(i.get("level") == "error" for i in issues):
        return 2
    if any(i.get("level") == "warning" for i in issues):
        return 1
    return 0


def _issue(level: str, rule_id: str, shot_order: int | None, message: str) -> Issue:
    return {
        "level": level,
        "rule_id": rule_id,
        "shot_order": shot_order,
        "message": message,
    }


def _frame_get(frame: Any, name: str, default: Any = None) -> Any:
    if frame is None:
        return default
    if isinstance(frame, Mapping):
        return frame.get(name, default)
    return getattr(frame, name, default)


def _frame_attrs(frame: Any) -> dict:
    raw = _frame_get(frame, "attrs", {}) or {}
    return raw if isinstance(raw, dict) else {}


def _parse_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _parse_pair(raw: Any) -> tuple[str, str] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (list, tuple)) and len(raw) >= 2:
        return normalize_pair(str(raw[0]), str(raw[1]))
    text = str(raw).strip()
    if "|" not in text:
        return None
    a, b = text.split("|", 1)
    return normalize_pair(a.strip(), b.strip())


def _pair_raw_normalized(raw: Any) -> bool:
    if raw is None or raw == "":
        return True
    if not isinstance(raw, str) or "|" not in raw:
        return False
    a, b = raw.split("|", 1)
    lo, hi = normalize_pair(a.strip(), b.strip())
    return raw == f"{lo}|{hi}"


def _plan_item(plan: Sequence[Mapping[str, Any]], item_id: str) -> dict | None:
    for row in plan:
        if str(row.get("id") or "") == str(item_id):
            return dict(row)
    return None


def compute_screen_pos_map(
    plan: Sequence[Mapping[str, Any]], lo: str, hi: str
) -> dict[str, str]:
    """L/R from camera look: clockwise perp of (mid-cam) is screen-right.

    CANON §2.5 axis-perp cannot split the two endpoints (they share the same
    projection on a vector perpendicular to the axis). Screen x is the
    projection of (pos − cam) onto that look-derived right.
    """
    a = _plan_item(plan, lo)
    b = _plan_item(plan, hi)
    if a is None or b is None:
        return {}
    ax, ay = float(a["x"]), float(a["y"])
    bx, by = float(b["x"]), float(b["y"])
    cam = _plan_item(plan, "cam")
    ids = [lo, hi]
    if cam is not None and "x" in cam and "y" in cam:
        cx, cy = float(cam["x"]), float(cam["y"])
        mid = ((ax + bx) / 2.0, (ay + by) / 2.0)
        look = (mid[0] - cx, mid[1] - cy)
        if math.hypot(*look) <= _EPS:
            right = (by - ay, -(bx - ax))
            origin = (ax, ay)
        else:
            right = (look[1], -look[0])
            origin = (cx, cy)
    else:
        right = (by - ay, -(bx - ax))
        origin = (ax, ay)
    projs = {
        i: (float((_plan_item(plan, i) or {}).get("x") or 0) - origin[0]) * right[0]
        + (float((_plan_item(plan, i) or {}).get("y") or 0) - origin[1]) * right[1]
        for i in ids
    }
    if abs(projs[lo] - projs[hi]) <= _EPS:
        return {lo: "C", hi: "C"}
    ordered = sorted(ids, key=lambda i: (projs[i], i))
    return {ordered[0]: "L", ordered[1]: "R"}


def subject_moved_reestablish(
    prev_plan: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
    *,
    meters: float = _MOVE_M,
) -> bool:
    prev = {str(p.get("id")): p for p in prev_plan}
    for row in plan:
        sid = str(row.get("id") or "")
        if not sid or sid == "cam":
            continue
        if "w" in row and "h" in row and "facing" not in row:
            continue
        old = prev.get(sid)
        if old is None:
            continue
        dx = float(row.get("x") or 0) - float(old.get("x") or 0)
        dy = float(row.get("y") or 0) - float(old.get("y") or 0)
        if math.hypot(dx, dy) >= meters:
            return True
    return False


def _body_ids(plan: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for row in plan:
        sid = str(row.get("id") or "")
        if not sid or sid == "cam":
            continue
        if "w" in row and "h" in row and "facing" not in row:
            continue
        out.add(sid)
    return out


def _action_class(role: str | None) -> str:
    if role == "reaction":
        return "reaction"
    if role == "insert":
        return "insert"
    if role in {"establish", "reestablish"}:
        return "establish"
    return "action"


def _junction(prev: str, cur: str) -> str:
    if prev == cur:
        return "error_same"
    if frozenset({prev, cur}) in _SIZE_OK_PAIRS:
        return "ok"
    delta = abs(SIZE_INDEX[prev] - SIZE_INDEX[cur])
    if delta == 1:
        return "warn_adj"
    if delta == 2:
        return "ok"
    return "warn_smash"


def _h_index(name: str) -> int | None:
    try:
        return ANGLE_H.index(name)
    except ValueError:
        return None


def _v_index(name: str) -> int | None:
    try:
        return ANGLE_V.index(name)
    except ValueError:
        return None


def _circ_dist(i: int, j: int, n: int) -> int:
    d = abs(i - j)
    return min(d, n - d)


def names_under_30(prev_h: str, prev_v: str, cur_h: str, cur_v: str) -> bool:
    ih, jh = _h_index(prev_h), _h_index(cur_h)
    iv, jv = _v_index(prev_v), _v_index(cur_v)
    if None in (ih, jh, iv, jv):
        return prev_h == cur_h and prev_v == cur_v
    dh = _circ_dist(ih, jh, len(ANGLE_H))
    dv = abs(iv - jv)
    if dh == 0 and dv == 0:
        return True
    return dh <= 1 and dv < 2


def _cam_orbit_deg(prev_cam: Any, cur_cam: Any, look: tuple[float, float]) -> float | None:
    try:
        p = _xy(prev_cam)
        c = _xy(cur_cam)
    except (TypeError, ValueError, KeyError):
        return None
    v1 = (p[0] - look[0], p[1] - look[1])
    v2 = (c[0] - look[0], c[1] - look[1])
    n1 = math.hypot(*v1)
    n2 = math.hypot(*v2)
    if n1 <= _EPS or n2 <= _EPS:
        return 0.0
    dot = max(-1.0, min(1.0, (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)))
    return math.degrees(math.acos(dot))


def _turn_marked(delta: Mapping[str, Any] | None) -> bool:
    if not delta:
        return False
    turn = delta.get("turn")
    if turn is True:
        return True
    if isinstance(turn, Mapping):
        return True
    if delta.get("turn_subject"):
        return True
    for key, blob in delta.items():
        if key in _DELTA_META:
            continue
        if isinstance(blob, Mapping) and blob.get("turn") is True:
            return True
    return False


def _dirs_opposite(prev_dir: str | None, cur_dir: str | None) -> bool:
    a = prev_dir or "none"
    b = cur_dir or "none"
    if a in {"none", ""} or b in {"none", ""}:
        return False
    return _DIR_OPP.get(a) == b


def _look_mid(plan: Sequence[Mapping[str, Any]], pair: tuple[str, str] | None) -> tuple[float, float] | None:
    if pair is None:
        return None
    a = _plan_item(plan, pair[0])
    b = _plan_item(plan, pair[1])
    if a is None or b is None:
        return None
    return ((float(a["x"]) + float(b["x"])) / 2.0, (float(a["y"]) + float(b["y"])) / 2.0)


def validate_scene(
    scene_id: str,
    frames_space_rows: Sequence[Mapping[str, Any]],
    space_json: Mapping[str, Any] | None,
    frames_by_uuid: Mapping[str, Any] | None,
) -> list[Issue]:
    """Run every CANON §6 check. Returns ``{level, rule_id, shot_order, message}``."""
    issues: list[Issue] = []
    space = dict(space_json or {})
    frames_by_uuid = frames_by_uuid or {}
    forbidden = load_forbidden_phrases()
    rows = sorted(
        (dict(r) for r in frames_space_rows),
        key=lambda r: (int(r.get("shot_order") or 0), str(r.get("uuid") or "")),
    )
    base_plan = [dict(p) for p in (space.get("plan") or [])]
    locked: dict[str, str] = {}
    for axis in space.get("axes") or []:
        if not isinstance(axis, Mapping):
            continue
        parsed = _parse_pair(axis.get("pair") or axis.get("axis_pair"))
        side = str(axis.get("side_locked") or "").strip().upper()
        if parsed and side in {"A", "B"}:
            locked[f"{parsed[0]}|{parsed[1]}"] = side

    prev_row: dict | None = None
    prev_plan = [dict(p) for p in base_plan]
    prev_pair: tuple[str, str] | None = None
    close_streak = 0
    sizes_seen: set[str] = set()
    accents: list[str] = []
    roles: list[str] = []
    monotony_seq: list[tuple[int, str, str]] = []
    coverage_master = False
    coverage_singles: set[str] = set()
    coverage_reaction = False
    dialogue_pair: tuple[str, str] | None = None
    for axis in space.get("axes") or []:
        if isinstance(axis, Mapping):
            parsed = _parse_pair(axis.get("pair"))
            if parsed:
                dialogue_pair = parsed
                break

    accumulated: list[dict] = []
    for row in rows:
        order = row.get("shot_order")
        try:
            order_i = int(order) if order is not None else None
        except (TypeError, ValueError):
            order_i = None
            issues.append(
                _issue("error", "schema", None, f"{scene_id}: shot_order не INTEGER ≥ 1")
            )

        uuid = str(row.get("uuid") or "")
        if not uuid:
            issues.append(_issue("error", "schema", order_i, f"{scene_id}: нет uuid"))
        elif uuid not in frames_by_uuid or frames_by_uuid.get(uuid) is None:
            issues.append(
                _issue(
                    "error",
                    "missing_uuid",
                    order_i,
                    f"{scene_id}: frame_uuid {uuid!r} нет в БД / frames_by_uuid",
                )
            )

        if order_i is not None and order_i < 1:
            issues.append(
                _issue("error", "schema", order_i, f"{scene_id}: shot_order должен быть ≥ 1")
            )

        for key in ("reestablish_due", "manual"):
            val = row.get(key)
            if val is None:
                continue
            try:
                iv = int(val)
            except (TypeError, ValueError):
                issues.append(
                    _issue("error", "schema", order_i, f"{scene_id}: {key} не 0|1")
                )
                continue
            if iv not in (0, 1):
                issues.append(
                    _issue("error", "schema", order_i, f"{scene_id}: {key} не 0|1")
                )

        shot_size = row.get("shot_size")
        if shot_size:
            size_raw = str(shot_size)
            if size_raw not in SIZE_INDEX:
                mapped = _LEGACY_SIZE.get(size_raw.upper())
                issues.append(
                    _issue(
                        "error",
                        "schema",
                        order_i,
                        f"{scene_id}: неизвестный shot_size {size_raw!r}"
                        + (f" (легаси → {mapped}, в frames_space не хранить)" if mapped else ""),
                    )
                )
                shot_size = None
            else:
                sizes_seen.add(size_raw)

        angle_h = row.get("angle_h")
        angle_v = row.get("angle_v")
        if angle_h not in (None, "") and angle_h not in ANGLE_H:
            issues.append(
                _issue("error", "schema", order_i, f"{scene_id}: angle_h {angle_h!r} не из 2.4")
            )
        if angle_v not in (None, "") and angle_v not in ANGLE_V:
            issues.append(
                _issue("error", "schema", order_i, f"{scene_id}: angle_v {angle_v!r} не из 2.4")
            )

        screen_dir = row.get("screen_dir")
        if screen_dir not in (None, "") and screen_dir not in SCREEN_DIRS:
            issues.append(
                _issue(
                    "error",
                    "schema",
                    order_i,
                    f"{scene_id}: screen_dir {screen_dir!r} не из канона",
                )
            )

        beat_role = row.get("beat_role")
        if beat_role not in (None, "") and beat_role not in BEAT_ROLES:
            issues.append(
                _issue(
                    "error",
                    "schema",
                    order_i,
                    f"{scene_id}: beat_role {beat_role!r} не из канона",
                )
            )
        if beat_role:
            roles.append(str(beat_role))

        crossing = row.get("crossing_method")
        if crossing not in (None, "") and crossing not in CROSSING_METHODS:
            issues.append(
                _issue(
                    "error",
                    "schema",
                    order_i,
                    f"{scene_id}: crossing_method {crossing!r} не из канона",
                )
            )

        axis_side_val = row.get("axis_side")
        if axis_side_val not in (None, "") and axis_side_val not in {"A", "B"}:
            issues.append(
                _issue(
                    "error",
                    "schema",
                    order_i,
                    f"{scene_id}: axis_side {axis_side_val!r} не A|B",
                )
            )

        if not _pair_raw_normalized(row.get("axis_pair")):
            issues.append(
                _issue(
                    "error",
                    "pair_norm",
                    order_i,
                    f"{scene_id}: axis_pair {row.get('axis_pair')!r} не канон lo|hi",
                )
            )

        pair = _parse_pair(row.get("axis_pair"))
        if dialogue_pair is None and pair is not None:
            dialogue_pair = pair

        delta = _parse_json(row.get("space_delta_json")) or {}
        if not isinstance(delta, dict):
            issues.append(
                _issue("error", "schema", order_i, f"{scene_id}: space_delta_json не object")
            )
            delta = {}
        accumulated.append(delta if isinstance(delta, dict) else {})
        plan = apply_deltas(base_plan, accumulated)

        stored_pos = _parse_json(row.get("screen_pos"))
        if isinstance(stored_pos, str):
            stored_pos = _parse_json(stored_pos)
        if stored_pos is None:
            stored_pos = {}
        if stored_pos is not None and not isinstance(stored_pos, dict):
            issues.append(
                _issue("error", "schema", order_i, f"{scene_id}: screen_pos не JSON-объект")
            )
            stored_pos = {}

        cam = _plan_item(plan, "cam")
        computed_side: str | None = None
        if pair is not None:
            a = _plan_item(plan, pair[0])
            b = _plan_item(plan, pair[1])
            if a is not None and b is not None and cam is not None:
                try:
                    computed_side = axis_side(
                        (float(a["x"]), float(a["y"])),
                        (float(b["x"]), float(b["y"])),
                        (float(cam["x"]), float(cam["y"])),
                    )
                except GeomDegenerate as exc:
                    issues.append(
                        _issue(
                            "error",
                            "degenerate",
                            order_i,
                            f"{scene_id}: вырождение оси (cross==0): {exc}",
                        )
                    )
                expected_pos = compute_screen_pos_map(plan, pair[0], pair[1])
                if expected_pos:
                    for sid, want in expected_pos.items():
                        got = stored_pos.get(sid)
                        if got != want:
                            issues.append(
                                _issue(
                                    "error",
                                    "screen_pos",
                                    order_i,
                                    f"{scene_id}: screen_pos[{sid}]={got!r} расчёт={want!r}",
                                )
                            )
            key = f"{pair[0]}|{pair[1]}"
            if computed_side:
                if key not in locked:
                    locked[key] = computed_side
                stored_side = axis_side_val if axis_side_val in {"A", "B"} else computed_side
                if stored_side != locked[key]:
                    if crossing in CROSSING_METHODS:
                        locked[key] = stored_side
                    else:
                        issues.append(
                            _issue(
                                "error",
                                "side_lock",
                                order_i,
                                f"{scene_id}: сторона {stored_side} ≠ запертая {locked[key]} для {key}",
                            )
                        )
                if (
                    prev_row is not None
                    and prev_pair == pair
                    and prev_row.get("axis_side") in {"A", "B"}
                    and stored_side in {"A", "B"}
                    and stored_side != prev_row.get("axis_side")
                    and crossing not in CROSSING_METHODS
                ):
                    issues.append(
                        _issue(
                            "error",
                            "side_cross_silent",
                            order_i,
                            f"{scene_id}: смена стороны {prev_row.get('axis_side')}→{stored_side} без crossing_method",
                        )
                    )

        if prev_row is not None:
            prev_dir = prev_row.get("screen_dir")
            if _dirs_opposite(prev_dir, screen_dir) and not _turn_marked(delta):
                prev_turn = _turn_marked(_parse_json(prev_row.get("space_delta_json")) or {})
                prev_insert = prev_row.get("beat_role") == "insert"
                cur_insert = beat_role == "insert"
                if not (prev_turn and prev_insert) and not (cur_insert and _turn_marked(delta)):
                    issues.append(
                        _issue(
                            "error",
                            "screen_dir",
                            order_i,
                            f"{scene_id}: screen_dir {prev_dir}→{screen_dir} без кадра поворота",
                        )
                    )

            prev_size = prev_row.get("shot_size")
            if (
                shot_size in SIZE_INDEX
                and prev_size in SIZE_INDEX
                and (pair is None or prev_pair == pair or prev_pair is None or pair is None)
            ):
                verdict = _junction(str(prev_size), str(shot_size))
                if verdict == "error_same":
                    issues.append(
                        _issue(
                            "error",
                            "size_same",
                            order_i,
                            f"{scene_id}: одинаковая крупность {shot_size} на стыке",
                        )
                    )
                elif verdict == "warn_adj":
                    issues.append(
                        _issue(
                            "warning",
                            "size_adjacent",
                            order_i,
                            f"{scene_id}: соседняя крупность {prev_size}→{shot_size}",
                        )
                    )
                elif verdict == "warn_smash" and beat_role != "insert":
                    issues.append(
                        _issue(
                            "warning",
                            "size_smash",
                            order_i,
                            f"{scene_id}: smash {prev_size}→{shot_size} без beat_role=insert",
                        )
                    )

            prev_cam = _plan_item(prev_plan, "cam")
            look = _look_mid(plan, pair or prev_pair)
            jumped = False
            if cam is not None and prev_cam is not None and look is not None:
                deg = _cam_orbit_deg(prev_cam, cam, look)
                if deg is not None and deg < 30.0:
                    jumped = True
            else:
                ph, pv = prev_row.get("angle_h"), prev_row.get("angle_v")
                ch, cv = angle_h, angle_v
                if (ph and pv and ch and cv and names_under_30(str(ph), str(pv), str(ch), str(cv))) or (
                    ph == ch and pv == cv
                ):
                    jumped = True
            if jumped:
                issues.append(
                    _issue(
                        "error",
                        "angle_30",
                        order_i,
                        f"{scene_id}: скачок точки съёмки < 30°",
                    )
                )

            if int(prev_row.get("reestablish_due") or 0) == 1 and shot_size not in {"WS", "EWS"}:
                issues.append(
                    _issue(
                        "error",
                        "reestablish",
                        order_i,
                        f"{scene_id}: reestablish_due кадра {prev_row.get('shot_order')} "
                        f"не погашен WS/EWS (есть {shot_size!r})",
                    )
                )

        if shot_size in SIZE_INDEX and SIZE_INDEX[str(shot_size)] > _MS_INDEX:
            close_streak += 1
        else:
            close_streak = 0

        frame_obj = frames_by_uuid.get(uuid) if uuid else None
        accent = _frame_attrs(frame_obj).get("accent")
        if accent:
            accents.append(str(accent))

        for field in ("image_prompt", "animation_prompt"):
            text = _frame_get(frame_obj, field) or ""
            if not str(text).strip():
                continue
            body = str(text)
            if len(body) > OUTSEE_PROMPT_MAX_CHARS:
                issues.append(
                    _issue(
                        "error",
                        "prompt_len",
                        order_i,
                        f"{scene_id}: {field} {len(body)} > {OUTSEE_PROMPT_MAX_CHARS}",
                    )
                )
            positive = split_prompt_positive(body)
            lower_pos = positive.casefold()
            for phrase in forbidden:
                if phrase.casefold() in lower_pos:
                    issues.append(
                        _issue(
                            "error",
                            "prompt_forbidden",
                            order_i,
                            f"{scene_id}: в позитиве {field} штамп {phrase!r}",
                        )
                    )
                    break
            if not has_style_marker(body) or not has_negative_marker(body):
                issues.append(
                    _issue(
                        "error",
                        "prompt_style",
                        order_i,
                        f"{scene_id}: в {field} нет стилевого блока и/или NEGATIVE",
                    )
                )

        if frame_obj is None and uuid:
            pass
        elif uuid:
            img = str(_frame_get(frame_obj, "image_prompt") or "")
            anim = str(_frame_get(frame_obj, "animation_prompt") or "")
            if not img.strip() and not anim.strip():
                issues.append(
                    _issue(
                        "error",
                        "prompt_style",
                        order_i,
                        f"{scene_id}: нет image_prompt/animation_prompt",
                    )
                )

        if pair is not None and isinstance(stored_pos, dict):
            present = {sid for sid in pair if sid in stored_pos}
            if shot_size in {"EWS", "WS", "FS"} and present == set(pair):
                coverage_master = True
            if len(present) == 1:
                coverage_singles.add(next(iter(present)))
        if beat_role in {"reaction", "insert"}:
            coverage_reaction = True

        surface = str(space.get("monotony_surface") or "ground")
        bodies = len(_body_ids(plan))
        mono = delta.get("monotony") if isinstance(delta.get("monotony"), dict) else None
        if isinstance(mono, dict):
            try:
                bodies = int(mono.get("bodies", bodies))
            except (TypeError, ValueError):
                bodies = bodies
            surface = str(mono.get("surface") or surface)
            aclass = str(mono.get("action_class") or _action_class(beat_role))
        else:
            aclass = _action_class(str(beat_role) if beat_role else "beat")
        monotony_seq.append((bodies, surface, aclass))

        prev_row = row
        prev_plan = plan
        prev_pair = pair

    if rows and int(rows[-1].get("reestablish_due") or 0) == 1:
        issues.append(
            _issue(
                "error",
                "reestablish",
                int(rows[-1]["shot_order"]) if rows[-1].get("shot_order") else None,
                f"{scene_id}: reestablish_due на последнем кадре не погашен",
            )
        )

    # 2.8 / 2.9 warnings
    charge = space.get("value_charge") or {}
    if isinstance(charge, str):
        charge = _parse_json(charge) or {}
    start = str((charge or {}).get("start") or "")
    end = str((charge or {}).get("end") or "")
    if start not in {"+", "-"} or end not in {"+", "-"} or start == end:
        issues.append(
            _issue(
                "warning",
                "no_turn",
                None,
                f"{scene_id}: сцена не поворачивает",
            )
        )

    if len(sizes_seen) < 3:
        issues.append(
            _issue(
                "warning",
                "interest_sizes",
                None,
                f"{scene_id}: разнообразие крупностей {len(sizes_seen)} < 3",
            )
        )
    if len(accents) != len(set(accents)):
        issues.append(
            _issue(
                "warning",
                "interest_accent",
                None,
                f"{scene_id}: accent повторяется внутри сцены",
            )
        )
    if "turning_point" not in roles:
        issues.append(
            _issue(
                "warning",
                "interest_turning_point",
                None,
                f"{scene_id}: нет кадра turning_point",
            )
        )
    if not any(r in {"reaction", "insert"} for r in roles):
        issues.append(
            _issue(
                "warning",
                "interest_reaction",
                None,
                f"{scene_id}: нет кадра reaction/insert",
            )
        )
    for i in range(len(monotony_seq) - 2):
        if monotony_seq[i] == monotony_seq[i + 1] == monotony_seq[i + 2]:
            issues.append(
                _issue(
                    "warning",
                    "interest_monotony",
                    rows[i + 2].get("shot_order"),
                    f"{scene_id}: три подряд одинаковые тройки монотонии {monotony_seq[i]}",
                )
            )
            break

    unpaid_geo = False
    close_run = 0
    prev_ids: set[str] | None = None
    prev_pair_s: str | None = None
    prev_plan_geo = [dict(p) for p in base_plan]
    for idx, row in enumerate(rows):
        plan = apply_deltas(base_plan, accumulated[: idx + 1])
        moved = subject_moved_reestablish(prev_plan_geo, plan) if idx else False
        ids_now = _body_ids(plan)
        roster = prev_ids is not None and ids_now != prev_ids
        pair_s = str(row.get("axis_pair") or "")
        pair_changed = bool(prev_pair_s and pair_s and pair_s != prev_pair_s)
        sz = row.get("shot_size")
        if sz in SIZE_INDEX and SIZE_INDEX[str(sz)] > _MS_INDEX:
            close_run += 1
        else:
            close_run = 0
        due = bool(moved or roster or pair_changed or close_run >= 3)
        nxt = rows[idx + 1] if idx + 1 < len(rows) else None
        if due and (nxt is None or nxt.get("shot_size") not in {"WS", "EWS"}):
            unpaid_geo = True
        prev_ids = ids_now
        prev_pair_s = pair_s
        prev_plan_geo = plan
    if unpaid_geo:
        issues.append(
            _issue(
                "warning",
                "interest_geography",
                None,
                f"{scene_id}: после перемещения/входа/крупных нет переустановки WS/EWS",
            )
        )

    if dialogue_pair is not None:
        need = {dialogue_pair[0], dialogue_pair[1]}
        if not (coverage_master and need <= coverage_singles and coverage_reaction):
            issues.append(
                _issue(
                    "warning",
                    "interest_coverage",
                    None,
                    f"{scene_id}: диалог двоих: нужен мастер + одиночный на каждого + реакция",
                )
            )

    return issues


def load_fixture(path: Path) -> tuple[str, dict, list[dict], dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"fixture {path} is not a JSON object")
    scene_id = str(data.get("scene_id") or data.get("id") or path.stem)
    space = data.get("space_json") or data.get("space") or {}
    if isinstance(space, str):
        space = json.loads(space)
    rows: list[dict] = []
    frames_by_uuid: dict[str, Any] = {}
    top_rows = data.get("frames_space") or data.get("frames_space_rows")
    if isinstance(top_rows, list):
        rows = [dict(r) for r in top_rows]
    for fr in data.get("frames") or data.get("frame_rows") or []:
        if not isinstance(fr, dict):
            continue
        uuid = str(fr.get("uuid") or "")
        if uuid:
            frames_by_uuid[uuid] = fr
        nested = fr.get("frames_space") or fr.get("space")
        if isinstance(nested, dict):
            row = dict(nested)
            row.setdefault("uuid", uuid)
            row.setdefault("scene_id", scene_id)
            rows.append(row)
        elif "shot_order" in fr and not top_rows:
            rows.append(fr)
    for row in rows:
        row.setdefault("scene_id", scene_id)
        uid = str(row.get("uuid") or "")
        if uid and uid not in frames_by_uuid:
            frames_by_uuid[uid] = row
    return scene_id, space, rows, frames_by_uuid


def format_validation_md(
    results: Sequence[tuple[str, list[Issue]]],
    *,
    note: str | None = None,
) -> str:
    lines = ["# VALIDATION", "", "Generated by `scripts/scene_space_validate.py`.", ""]
    if note:
        lines.extend([note, ""])
    lines += ["## Summary", "", "| scene_id | errors | warnings |", "|---|---:|---:|"]
    for sid, issues in results:
        err = sum(1 for i in issues if i.get("level") == "error")
        warn = sum(1 for i in issues if i.get("level") == "warning")
        lines.append(f"| {sid} | {err} | {warn} |")
    lines.append("")
    for sid, issues in results:
        lines += [f"## {sid}", ""]
        if not issues:
            lines += ["чисто.", ""]
            continue
        lines += [
            "| level | rule_id | shot_order | message |",
            "|---|---|---|---|",
        ]
        for i in issues:
            order = i.get("shot_order")
            order_s = "—" if order is None else str(order)
            msg = str(i.get("message") or "").replace("|", "\\|")
            lines.append(
                f"| {i.get('level')} | `{i.get('rule_id')}` | {order_s} | {msg} |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
