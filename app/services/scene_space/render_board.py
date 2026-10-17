"""Montage HTML board: shots in order, 2.9 summary, validator violations highlighted."""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from app.services.scene_space.render_plan import (
    _as_dict,
    _by_id,
    _xy,
    normalize_frames,
    normalize_space,
    replay_states,
)

INTEREST_RULES: tuple[tuple[str, str], ...] = (
    ("interest_size_variety", "разнообразие крупностей (минимум 3 кода)"),
    ("interest_unique_accents", "уникальность акцентов"),
    ("interest_turning_point", "точка поворота покрыта"),
    ("interest_reaction", "есть реакция или деталь"),
    ("interest_monotony", "нет монотонии трёх кадров"),
    ("interest_geography", "география держится (переустановка)"),
    ("interest_coverage", "покрытие диалога двоих"),
)

ROLE_TO_ACTION = {
    "reaction": "reaction",
    "insert": "insert",
    "establish": "establish",
    "reestablish": "establish",
    "beat": "action",
    "turning_point": "action",
}

MASTER_SIZES = frozenset({"EWS", "WS", "FS"})
REESTABLISH_SIZES = frozenset({"EWS", "WS"})
LEVEL_ERR = frozenset({"error", "err"})
LEVEL_WARN = frozenset({"warning", "warn"})


def _esc(text: Any) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


def _screen_pos_map(frame: dict[str, Any]) -> dict[str, str]:
    raw = frame.get("_screen_pos")
    if isinstance(raw, dict) and raw:
        return {str(k): str(v) for k, v in raw.items()}
    return {str(k): str(v) for k, v in _as_dict(frame.get("screen_pos")).items()}


def _screen_pos_text(frame: dict[str, Any]) -> str:
    mapping = _screen_pos_map(frame)
    if mapping:
        return json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raw = frame.get("screen_pos")
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    return json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _accent(frame: dict[str, Any]) -> str:
    val = frame.get("accent")
    if val is None:
        attrs = frame.get("attrs")
        if isinstance(attrs, dict):
            val = attrs.get("accent")
    if val is None:
        return ""
    return str(val).strip()


def _pair_ids(frame: dict[str, Any]) -> tuple[str, str] | None:
    raw = frame.get("axis_pair")
    if not raw:
        return None
    text = str(raw)
    if "|" not in text:
        return None
    a, b = text.split("|", 1)
    a, b = a.strip(), b.strip()
    if not a or not b:
        return None
    return (a, b)


def _bodies_count(plan: list[dict[str, Any]]) -> int:
    n = 0
    for item in plan:
        sid = str(item.get("id") or "")
        if sid and sid != "cam":
            n += 1
    return n


def _action_class(role: Any) -> str:
    return ROLE_TO_ACTION.get(str(role or "").strip(), "action")


def _monotony_triple(space: dict[str, Any], state: dict[str, Any], frame: dict[str, Any]) -> tuple[int, str, str]:
    delta = frame.get("_delta") if isinstance(frame.get("_delta"), dict) else _as_dict(frame.get("space_delta_json"))
    mono = delta.get("monotony") if isinstance(delta.get("monotony"), dict) else None
    if mono:
        try:
            bodies = int(mono.get("bodies"))
        except (TypeError, ValueError):
            bodies = _bodies_count(state.get("plan") or [])
        surface = str(mono.get("surface") or space.get("monotony_surface") or "ground")
        action = str(mono.get("action_class") or _action_class(frame.get("beat_role")))
        return (bodies, surface, action)
    surface = str(space.get("monotony_surface") or "ground")
    return (
        _bodies_count(state.get("plan") or []),
        surface,
        _action_class(frame.get("beat_role")),
    )


def _moved_ge_1m(state: dict[str, Any]) -> bool:
    prev = _by_id(state.get("prev_plan") or [])
    cur = _by_id(state.get("plan") or [])
    if not prev:
        return False
    for sid, item in cur.items():
        if sid == "cam":
            continue
        now = _xy(item)
        old = _xy(prev.get(sid))
        if now and old and (now[0] - old[0]) ** 2 + (now[1] - old[1]) ** 2 >= 1.0:
            return True
    return False


def interest_criteria_29(space: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Local 2.9 summary. Each item: rule_id, ok, label, detail."""
    space = normalize_space(space)
    frames = normalize_frames(frames)
    states = replay_states(space, frames)
    results: list[dict[str, Any]] = []

    sizes = [str(f.get("shot_size") or "") for f in frames]
    uniq_sizes = {s for s in sizes if s}
    ok_var = len(uniq_sizes) >= 3
    results.append(
        {
            "rule_id": "interest_size_variety",
            "ok": ok_var,
            "label": INTEREST_RULES[0][1],
            "detail": f"коды={json.dumps(sorted(uniq_sizes), ensure_ascii=False)} count={len(uniq_sizes)}",
        }
    )

    accents = [_accent(f) for f in frames]
    nonempty = [a for a in accents if a]
    ok_acc = len(nonempty) == len(set(nonempty))
    results.append(
        {
            "rule_id": "interest_unique_accents",
            "ok": ok_acc,
            "label": INTEREST_RULES[1][1],
            "detail": f"accents={json.dumps(nonempty, ensure_ascii=False)}",
        }
    )

    roles = [str(f.get("beat_role") or "") for f in frames]
    ok_tp = "turning_point" in roles
    results.append(
        {
            "rule_id": "interest_turning_point",
            "ok": ok_tp,
            "label": INTEREST_RULES[2][1],
            "detail": "turning_point" if ok_tp else "нет turning_point",
        }
    )

    ok_rx = any(r in ("reaction", "insert") for r in roles)
    results.append(
        {
            "rule_id": "interest_reaction",
            "ok": ok_rx,
            "label": INTEREST_RULES[3][1],
            "detail": "есть reaction/insert" if ok_rx else "нет reaction/insert",
        }
    )

    triples = [_monotony_triple(space, states[i], frames[i]) for i in range(len(frames))]
    mono_hit = False
    for i in range(len(triples) - 2):
        if triples[i] == triples[i + 1] == triples[i + 2]:
            mono_hit = True
            break
    results.append(
        {
            "rule_id": "interest_monotony",
            "ok": not mono_hit,
            "label": INTEREST_RULES[4][1],
            "detail": "повтор тройки" if mono_hit else "монотонии нет",
        }
    )

    geo_ok = True
    geo_detail = "ok"
    for i, frame in enumerate(frames):
        due = frame.get("reestablish_due")
        try:
            due_i = int(due or 0)
        except (TypeError, ValueError):
            due_i = 0
        if due_i == 1:
            nxt = frames[i + 1] if i + 1 < len(frames) else None
            nxt_size = str((nxt or {}).get("shot_size") or "")
            if nxt is None or nxt_size not in REESTABLISH_SIZES:
                geo_ok = False
                geo_detail = f"reestablish_due на shot_order={frame.get('shot_order')} не погашен"
                break
        if i < len(states) and _moved_ge_1m(states[i]):
            this_size = str(frame.get("shot_size") or "")
            nxt = frames[i + 1] if i + 1 < len(frames) else None
            nxt_size = str((nxt or {}).get("shot_size") or "")
            role = str(frame.get("beat_role") or "")
            if this_size not in REESTABLISH_SIZES and role != "reestablish" and nxt_size not in REESTABLISH_SIZES:
                geo_ok = False
                geo_detail = f"перемещение без переустановки shot_order={frame.get('shot_order')}"
                break
    results.append(
        {
            "rule_id": "interest_geography",
            "ok": geo_ok,
            "label": INTEREST_RULES[5][1],
            "detail": geo_detail,
        }
    )

    pair = None
    for frame in frames:
        pair = _pair_ids(frame)
        if pair:
            break
    if pair is None:
        results.append(
            {
                "rule_id": "interest_coverage",
                "ok": True,
                "label": INTEREST_RULES[6][1],
                "detail": "не диалог двоих — критерий не применяется",
            }
        )
    else:
        a, b = pair
        has_master = False
        single_a = False
        single_b = False
        has_reaction = False
        for frame in frames:
            pos = _screen_pos_map(frame)
            size = str(frame.get("shot_size") or "")
            in_a, in_b = a in pos, b in pos
            if size in MASTER_SIZES and in_a and in_b:
                has_master = True
            if in_a and not in_b:
                single_a = True
            if in_b and not in_a:
                single_b = True
            if str(frame.get("beat_role") or "") == "reaction":
                has_reaction = True
        ok_cov = has_master and single_a and single_b and has_reaction
        results.append(
            {
                "rule_id": "interest_coverage",
                "ok": ok_cov,
                "label": INTEREST_RULES[6][1],
                "detail": (
                    f"master={has_master} single_{a}={single_a} "
                    f"single_{b}={single_b} reaction={has_reaction}"
                ),
            }
        )
    return results


def css_level(level: Any) -> str:
    text = str(level or "").strip().lower()
    if text in LEVEL_ERR:
        return "err"
    if text in LEVEL_WARN:
        return "warn"
    if text in ("ok", "pass", "info"):
        return "ok"
    return "warn"


def normalize_violations(raw: Any) -> list[dict[str, Any]]:
    items: list[Any]
    if raw is None:
        items = []
    elif isinstance(raw, list):
        items = raw
    elif isinstance(raw, dict):
        if isinstance(raw.get("violations"), list):
            items = raw["violations"]
        elif isinstance(raw.get("issues"), list):
            items = raw["issues"]
        elif isinstance(raw.get("findings"), list):
            items = raw["findings"]
        else:
            items = [raw]
    elif hasattr(raw, "violations"):
        items = list(raw.violations or [])
    elif hasattr(raw, "issues"):
        items = list(raw.issues or [])
    else:
        items = []
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            if hasattr(item, "__dict__"):
                item = {
                    "rule_id": getattr(item, "rule_id", None) or getattr(item, "code", ""),
                    "level": getattr(item, "level", None) or getattr(item, "severity", "warn"),
                    "message": getattr(item, "message", None) or getattr(item, "text", ""),
                    "shot_order": getattr(item, "shot_order", None),
                    "uuid": getattr(item, "uuid", None),
                }
            else:
                continue
        rule_id = str(item.get("rule_id") or item.get("code") or item.get("rule") or "").strip()
        if not rule_id:
            continue
        level = css_level(item.get("level") or item.get("severity") or "warn")
        try:
            order = item.get("shot_order")
            order_i = int(order) if order is not None and str(order) != "" else None
        except (TypeError, ValueError):
            order_i = None
        out.append(
            {
                "rule_id": rule_id,
                "level": level,
                "message": str(item.get("message") or item.get("text") or ""),
                "shot_order": order_i,
                "uuid": str(item.get("uuid") or "") or None,
            }
        )
    out.sort(
        key=lambda v: (
            0 if v["level"] == "err" else 1,
            v["rule_id"],
            v.get("shot_order") if v.get("shot_order") is not None else 10**9,
            v.get("uuid") or "",
            v.get("message") or "",
        )
    )
    return out


def try_call_validate(module: Any, scene_id: str, space: dict[str, Any], frames: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Call stream-4 validate if present. Expected: validate_scene(scene_id, space, frames)."""
    names = (
        "validate_scene",
        "validate_scene_space",
        "run_validation",
        "check_scene",
        "validate",
    )
    last_err: Exception | None = None
    for name in names:
        fn = getattr(module, name, None)
        if not callable(fn):
            continue
        attempts = (
            lambda f=fn: f(
                scene_id,
                frames,
                space,
                {str(r.get("uuid")): r for r in frames if r.get("uuid")},
            ),
            lambda f=fn: f(scene_id, space, frames),
            lambda f=fn: f(scene_id=scene_id, space=space, frames=frames),
            lambda f=fn: f(scene_id=scene_id, space_json=space, frames=frames),
            lambda f=fn: f(space, frames),
            lambda f=fn: f(scene_id),
        )
        for attempt in attempts:
            try:
                return normalize_violations(attempt())
            except TypeError as exc:
                last_err = exc
                continue
    if last_err:
        raise last_err
    raise AttributeError(
        "scene_space.validate has no validate_scene / validate_scene_space / "
        "run_validation / check_scene / validate"
    )


def _violations_for_frame(frame: dict[str, Any], violations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    uid = str(frame.get("uuid") or "")
    order = frame.get("shot_order")
    hit: list[dict[str, Any]] = []
    for item in violations:
        if item["rule_id"].startswith("interest_"):
            continue
        if item.get("uuid") and uid and item["uuid"] == uid:
            hit.append(item)
            continue
        if item.get("shot_order") is not None and order is not None and item["shot_order"] == order:
            hit.append(item)
    return hit


def render_board_html(
    scene_id: str,
    space: dict[str, Any],
    frames: list[dict[str, Any]],
    violations: list[dict[str, Any]] | None = None,
) -> str:
    space = normalize_space(space)
    frames = normalize_frames(frames)
    violations = normalize_violations(violations)
    interest = interest_criteria_29(space, frames)
    interest_from_val = {v["rule_id"]: v for v in violations if v["rule_id"].startswith("interest_")}
    scene_level = [v for v in violations if v.get("shot_order") is None and not v.get("uuid") and not v["rule_id"].startswith("interest_")]

    lines: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="ru">',
        "<head>",
        '<meta charset="utf-8"/>',
        f"<title>montage {_esc(scene_id)}</title>",
        "<style>",
        "body{font-family:Segoe UI,Arial,sans-serif;margin:16px;color:#111}",
        "h1,h2{margin:0 0 8px}",
        "table{border-collapse:collapse;width:100%;margin-top:12px}",
        "th,td{border:1px solid #ccc;padding:6px 8px;vertical-align:top;font-size:13px}",
        "th{background:#f4f4f4;text-align:left}",
        ".err{background:#ffd6d6}",
        ".warn{background:#fff3cd}",
        ".ok{background:#e6f4ea}",
        "ul.violations{margin:0;padding-left:18px}",
        "section{margin-bottom:20px}",
        "</style>",
        "</head>",
        "<body>",
        f"<h1>scene {_esc(scene_id)}</h1>",
        '<section id="interest">',
        "<h2>2.9 критерии интересности</h2>",
        "<table>",
        "<thead><tr><th>rule_id</th><th>статус</th><th>критерий</th><th>деталь</th></tr></thead>",
        "<tbody>",
    ]
    for item in interest:
        overlay = interest_from_val.get(item["rule_id"])
        ok = item["ok"]
        if overlay and overlay["level"] in ("err", "warn"):
            ok = False
        css = "ok" if ok else "warn"
        detail = item["detail"]
        if overlay and overlay.get("message"):
            detail = f"{detail}; validator: {overlay['message']}"
        lines.append(
            f'<tr class="{css}" data-rule="{_esc(item["rule_id"])}">'
            f'<td>{_esc(item["rule_id"])}</td>'
            f"<td>{'ok' if ok else 'fail'}</td>"
            f'<td>{_esc(item["label"])}</td>'
            f"<td>{_esc(detail)}</td></tr>"
        )
    lines.extend(["</tbody></table>", "</section>"])

    if scene_level:
        lines.append('<section id="scene-violations"><h2>нарушения сцены</h2><ul class="violations">')
        for item in scene_level:
            text = item["rule_id"] if not item["message"] else f'{item["rule_id"]}: {item["message"]}'
            lines.append(f'<li class="{item["level"]}" data-rule="{_esc(item["rule_id"])}">{_esc(text)}</li>')
        lines.append("</ul></section>")

    lines.extend(
        [
            '<section id="shots">',
            "<h2>кадры</h2>",
            "<table>",
            (
                "<thead><tr><th>shot_order</th><th>shot_size</th><th>angle_v</th>"
                "<th>angle_h</th><th>axis_side</th><th>screen_pos</th>"
                "<th>screen_dir</th><th>accent</th><th>beat_role</th>"
                "<th>violations</th></tr></thead>"
            ),
            "<tbody>",
        ]
    )
    for frame in frames:
        hits = _violations_for_frame(frame, violations)
        row_cls = ""
        if any(v["level"] == "err" for v in hits):
            row_cls = ' class="err"'
        elif any(v["level"] == "warn" for v in hits):
            row_cls = ' class="warn"'
        v_html = "<ul class=\"violations\">"
        if hits:
            for item in hits:
                text = item["rule_id"] if not item["message"] else f'{item["rule_id"]}: {item["message"]}'
                v_html += (
                    f'<li class="{item["level"]}" data-rule="{_esc(item["rule_id"])}">'
                    f"{_esc(text)}</li>"
                )
        else:
            v_html += "<li>—</li>"
        v_html += "</ul>"
        lines.append(
            f'<tr{row_cls} data-order="{_esc(frame.get("shot_order"))}">'
            f'<td>{_esc(frame.get("shot_order"))}</td>'
            f'<td>{_esc(frame.get("shot_size"))}</td>'
            f'<td>{_esc(frame.get("angle_v"))}</td>'
            f'<td>{_esc(frame.get("angle_h"))}</td>'
            f'<td>{_esc(frame.get("axis_side"))}</td>'
            f"<td>{_esc(_screen_pos_text(frame))}</td>"
            f'<td>{_esc(frame.get("screen_dir"))}</td>'
            f"<td>{_esc(_accent(frame))}</td>"
            f'<td>{_esc(frame.get("beat_role"))}</td>'
            f"<td>{v_html}</td></tr>"
        )
    lines.extend(["</tbody></table>", "</section>", "</body>", "</html>", ""])
    return "\n".join(lines)


def write_board(
    path: Path,
    scene_id: str,
    space: dict[str, Any],
    frames: list[dict[str, Any]],
    violations: list[dict[str, Any]] | None = None,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_board_html(scene_id, space, frames, violations), encoding="utf-8", newline="\n")
    return path
