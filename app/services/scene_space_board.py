"""Карта площадки для «Улучшить сцену».

Родитель фиксирует гвозди места и кто где стоит.
Дочерний кадр не рисует новый мир — только CAM / MOVE / FACE / SIZE.
"""

from __future__ import annotations

import json
import re
from typing import Any

SPACE_ATTR = "scene_space_board"
SPACE_DELTA_ATTR = "space_delta"

_PLAN_RE = re.compile(r"^(ДАЛЬНИЙ|ОБЩИЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ)$", re.I)
_CAM_RE = re.compile(
    r"CAM\s*@\s*([^\s,;→>-]+)\s*(?:→|->|смотрит)\s*([^\s,;]+)",
    re.IGNORECASE,
)
_MOVE_RE = re.compile(r"MOVE\s+(\S+)\s*@\s*(\S+)", re.IGNORECASE)
_FACE_RE = re.compile(r"FACE\s+(\S+)\s*(?:→|->)\s*(\S+)", re.IGNORECASE)
_SIZE_RE = re.compile(r"SIZE\s+(\S+)", re.IGNORECASE)
_STAND_RE = re.compile(
    r"(\S+)@(\S+)(?:\s*(?:→|->)\s*(\S+))?",
)
_WALK_RE = re.compile(
    r"ид[её]т|подош|выхо|захо|шагн|беж|подход|уход",
    re.IGNORECASE,
)
_NAIL_HINTS: tuple[tuple[str, str], ...] = (
    (r"лесополос", "лесополоса"),
    (r"железн\w*\s*дорог", "железная дорога"),
    (r"дорог", "дорога"),
    (r"колей", "колея"),
    (r"троп", "тропа"),
    (r"ствол", "ствол"),
    (r"дерев", "дерево"),
    (r"\bпол[еяю]\b", "поле"),
    (r"станк", "станок"),
    (r"коридор", "коридор"),
    (r"\bдом", "дом"),
    (r"двер", "дверь"),
    (r"стол", "стол"),
    (r"окн", "окно"),
    (r"полк", "полка"),
    (r"кроват", "кровать"),
    (r"стен", "стена"),
    (r"кухн", "кухня"),
    (r"архив", "архив"),
    (r"лес", "лес"),
)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _fold(text: Any) -> str:
    return _norm(text).casefold()


def _plan(raw: Any, default: str = "ОБЩИЙ") -> str:
    text = _norm(raw).upper().replace("ПЛАН", "").strip()
    if _PLAN_RE.match(text):
        return text
    return default


def copy_board(board: dict[str, Any] | None) -> dict[str, Any]:
    return json.loads(json.dumps(board or {}, ensure_ascii=False))


def _split_list(raw: Any) -> list[str]:
    if isinstance(raw, list):
        return [_norm(x) for x in raw if _norm(x)]
    text = _norm(raw)
    if not text:
        return []
    parts = re.split(r"\s*[|,;/]\s*", text)
    return [_norm(p) for p in parts if _norm(p)]


def match_nail(name: str, nails: list[str]) -> str:
    want = _fold(name).strip("@")
    if not want:
        return ""
    for nail in nails:
        if _fold(nail) == want:
            return nail
    for nail in nails:
        folded = _fold(nail)
        if want in folded or folded in want:
            return nail
    return ""


def _who_key(row: dict[str, str], token: str) -> bool:
    token_f = _fold(token).lstrip("@")
    if not token_f:
        return False
    code = _fold(row.get("кто") or "")
    name = _fold(row.get("имя") or "")
    first = name.split()[0] if name else ""
    return token_f in {code, name, first} or (len(token_f) >= 3 and token_f in name)


def extract_nails(place: str, vo: str) -> list[str]:
    blob = f"{place} {vo}"
    folded = _fold(blob)
    out: list[str] = []
    seen: set[str] = set()
    loc = _norm(place)
    if loc and loc.casefold() not in {"—", "-"}:
        out.append(loc)
        seen.add(_fold(loc))
    for pattern, label in _NAIL_HINTS:
        if re.search(pattern, folded, re.IGNORECASE):
            if _fold(label) in seen:
                continue
            out.append(label)
            seen.add(_fold(label))
        if len(out) >= 5:
            break
    pad = ("вход", "центр", "глубина")
    for extra in pad:
        if len(out) >= 3:
            break
        if _fold(extra) not in seen:
            out.append(extra)
            seen.add(_fold(extra))
    return out[:5]


def _stand_rows(
    characters: list[dict[str, str]],
    nails: list[str],
    camera_at: str,
) -> list[dict[str, str]]:
    spots = [n for n in nails if _fold(n) != _fold(camera_at)] or list(nails)
    rows: list[dict[str, str]] = []
    for i, char in enumerate(characters):
        code = _norm(char.get("code") or "")
        name = _norm(char.get("name") or "")
        if not code and not name:
            continue
        where = spots[min(i, len(spots) - 1)]
        rows.append(
            {
                "кто": code or name,
                "имя": name or code,
                "где": where,
                "лицо": camera_at,
            }
        )
    return rows


def _axis(rows: list[dict[str, str]]) -> str:
    if len(rows) >= 2:
        a = rows[0].get("кто") or rows[0].get("имя")
        b = rows[1].get("кто") or rows[1].get("имя")
        return f"{a}—{b}"
    if rows:
        return str(rows[0].get("кто") or rows[0].get("имя") or "")
    return ""


def _screen(rows: list[dict[str, str]], camera: dict[str, str]) -> dict[str, str]:
    left = ""
    right = ""
    if rows:
        left = rows[0].get("имя") or rows[0].get("кто") or ""
    if len(rows) > 1:
        right = rows[1].get("имя") or rows[1].get("кто") or ""
    look = camera.get("смотрит") or ""
    at = camera.get("где") or ""
    closer = look or (rows[0].get("где") if rows else "")
    farther = ""
    for row in rows:
        if _fold(row.get("где") or "") != _fold(closer):
            farther = row.get("где") or ""
            break
    if not farther and at and _fold(at) != _fold(closer):
        farther = at
    return {
        "L": left,
        "R": right,
        "ближе": closer,
        "дальше": farther,
    }


def default_board(
    *,
    place: str,
    characters: list[dict[str, str]],
    vo: str = "",
    plan: str = "ОБЩИЙ",
) -> dict[str, Any]:
    nails = extract_nails(place, vo)
    camera_at = nails[0]
    look_at = nails[1] if len(nails) > 1 else nails[0]
    stands = _stand_rows(characters, nails, camera_at)
    if stands:
        look_at = stands[0]["где"]
    camera = {
        "где": camera_at,
        "смотрит": look_at,
        "план": _plan(plan),
        "высота": "глаза",
    }
    return {
        "место": _norm(place) or "место сцены",
        "гвозди": nails,
        "ось": _axis(stands),
        "сторона": "A",
        "стоит": stands,
        "камера": camera,
        "экран": _screen(stands, camera),
    }


def parse_stands(raw: Any, nails: list[str]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    items: list[Any]
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str) and raw.strip():
        items = re.split(r"\s*;\s*", raw.strip())
    else:
        return []
    for item in items:
        if isinstance(item, dict):
            who = _norm(item.get("кто") or item.get("code") or item.get("id") or "")
            name = _norm(item.get("имя") or item.get("name") or "")
            where = match_nail(str(item.get("где") or item.get("at") or ""), nails)
            face = match_nail(
                str(item.get("лицо") or item.get("face") or item.get("к") or ""),
                nails,
            )
            if not who and not name:
                continue
            rows.append(
                {
                    "кто": who or name,
                    "имя": name or who,
                    "где": where or (nails[0] if nails else ""),
                    "лицо": face or (nails[0] if nails else ""),
                }
            )
            continue
        text = _norm(item)
        hit = _STAND_RE.search(text.replace(" ", ""))
        if not hit:
            hit = _STAND_RE.search(text)
        if not hit:
            continue
        who, where_raw, face_raw = hit.group(1), hit.group(2), hit.group(3) or ""
        where = match_nail(where_raw, nails)
        face = match_nail(face_raw, nails) if face_raw else ""
        rows.append(
            {
                "кто": who,
                "имя": who,
                "где": where or (nails[0] if nails else ""),
                "лицо": face or (nails[0] if nails else ""),
            }
        )
    return rows


def parse_camera(raw: Any, nails: list[str], plan: str) -> dict[str, str]:
    if isinstance(raw, dict):
        at = match_nail(str(raw.get("где") or raw.get("at") or ""), nails)
        look = match_nail(
            str(raw.get("смотрит") or raw.get("look") or raw.get("к") or ""),
            nails,
        )
        return {
            "где": at or (nails[0] if nails else ""),
            "смотрит": look or (nails[min(1, len(nails) - 1)] if nails else ""),
            "план": _plan(raw.get("план") or plan),
            "высота": _norm(raw.get("высота") or "глаза") or "глаза",
        }
    text = _norm(raw)
    hit = _CAM_RE.search(text) if text else None
    if hit:
        at = match_nail(hit.group(1), nails)
        look = match_nail(hit.group(2), nails)
        return {
            "где": at or (nails[0] if nails else ""),
            "смотрит": look or (nails[min(1, len(nails) - 1)] if nails else ""),
            "план": _plan(plan),
            "высота": "глаза",
        }
    return {
        "где": nails[0] if nails else "",
        "смотрит": nails[min(1, len(nails) - 1)] if nails else "",
        "план": _plan(plan),
        "высота": "глаза",
    }


def parse_board(
    raw: Any,
    *,
    fallback: dict[str, Any],
) -> dict[str, Any]:
    board = copy_board(fallback)
    data = raw if isinstance(raw, dict) else {}
    nails = _split_list(data.get("гвозди") or data.get("nails"))
    if 3 <= len(nails) <= 7:
        board["гвозди"] = nails
    else:
        nails = list(board.get("гвозди") or [])
    if data.get("место"):
        board["место"] = _norm(data.get("место"))
    stands = parse_stands(data.get("стоит") or data.get("stand"), nails)
    if stands:
        by_code = {_fold(r.get("кто") or ""): r for r in stands}
        merged: list[dict[str, str]] = []
        for old in board.get("стоит") or []:
            hit = by_code.get(_fold(old.get("кто") or ""))
            if hit:
                row = dict(old)
                row["где"] = hit["где"] or row.get("где") or ""
                row["лицо"] = hit["лицо"] or row.get("лицо") or ""
                if hit.get("имя") and hit["имя"] != hit["кто"]:
                    row["имя"] = hit["имя"]
                merged.append(row)
            else:
                merged.append(dict(old))
        known = {_fold(r.get("кто") or "") for r in merged}
        for row in stands:
            if _fold(row.get("кто") or "") not in known:
                merged.append(row)
        board["стоит"] = merged
    if data.get("камера") or data.get("camera"):
        board["камера"] = parse_camera(
            data.get("камера") or data.get("camera"),
            nails,
            str((board.get("камера") or {}).get("план") or "ОБЩИЙ"),
        )
    if data.get("ось"):
        board["ось"] = _norm(data.get("ось"))
    board["экран"] = _screen(
        list(board.get("стоит") or []),
        dict(board.get("камера") or {}),
    )
    return board


def apply_delta(board: dict[str, Any], delta: str) -> dict[str, Any]:
    """Гвозди не меняются. CAM/MOVE/FACE/SIZE правят камеру и людей."""
    out = copy_board(board)
    nails = list(out.get("гвозди") or [])
    camera = dict(out.get("камера") or {})
    stands = [dict(r) for r in (out.get("стоит") or [])]
    text = _norm(delta)
    if not text:
        out["камера"] = camera
        out["стоит"] = stands
        out["экран"] = _screen(stands, camera)
        return out
    if re.search(r"REESTABLISH", text, re.IGNORECASE):
        camera["план"] = "ОБЩИЙ"
    for hit in _CAM_RE.finditer(text):
        at = match_nail(hit.group(1), nails)
        look = match_nail(hit.group(2), nails)
        if at:
            camera["где"] = at
        if look:
            camera["смотрит"] = look
    for hit in _MOVE_RE.finditer(text):
        where = match_nail(hit.group(2), nails)
        if not where:
            continue
        for row in stands:
            if _who_key(row, hit.group(1)):
                row["где"] = where
    for hit in _FACE_RE.finditer(text):
        face = match_nail(hit.group(2), nails)
        if not face:
            continue
        for row in stands:
            if _who_key(row, hit.group(1)):
                row["лицо"] = face
    size = _SIZE_RE.search(text)
    if size:
        camera["план"] = _plan(size.group(1), default=str(camera.get("план") or "СРЕДНИЙ"))
    out["гвозди"] = nails
    out["камера"] = camera
    out["стоит"] = stands
    out["экран"] = _screen(stands, camera)
    return out


def shot_delta(shot: dict[str, Any] | None) -> str:
    if not isinstance(shot, dict):
        return ""
    parts = [
        _norm(shot.get("дельта") or shot.get("delta") or ""),
        _norm(shot.get("cam") or ""),
        _norm(shot.get("move") or ""),
    ]
    return "; ".join(p for p in parts if p)


def infer_delta(
    *,
    parent: dict[str, Any],
    plan: str,
    piece: str,
    action: str,
) -> str:
    cmds: list[str] = []
    parent_plan = _plan((parent.get("камера") or {}).get("план") or "ОБЩИЙ")
    child_plan = _plan(plan, default=parent_plan)
    if child_plan != parent_plan:
        cmds.append(f"SIZE {child_plan}")
    nails = list(parent.get("гвозди") or [])
    blob = f"{piece} {action}"
    mentioned = [n for n in nails if _fold(n) and _fold(n) in _fold(blob)]
    cam_at = _fold((parent.get("камера") or {}).get("где") or "")
    if mentioned and _WALK_RE.search(blob):
        stands = list(parent.get("стоит") or [])
        who = ""
        if stands:
            who = str(stands[0].get("кто") or stands[0].get("имя") or "")
        dest = next((n for n in mentioned if _fold(n) != cam_at), mentioned[0])
        if who and dest:
            cmds.append(f"MOVE {who} @{dest}")
    elif mentioned:
        look = mentioned[0]
        at = (parent.get("камера") or {}).get("где") or (nails[0] if nails else "")
        if _fold(look) != cam_at:
            cmds.append(f"CAM @{at} →{look}")
    return "; ".join(cmds)


def _people_line(board: dict[str, Any], *, face_camera: bool) -> str:
    cam_at = (board.get("камера") or {}).get("где") or ""
    parts: list[str] = []
    for row in board.get("стоит") or []:
        who = row.get("имя") or row.get("кто") or "персонаж"
        where = row.get("где") or ""
        face = "камере" if face_camera else (row.get("лицо") or cam_at)
        if where:
            parts.append(f"{who} у {where}, лицом к {face}")
        else:
            parts.append(f"{who} лицом к {face}")
    return "; ".join(parts)


def compile_parent_prompt(board: dict[str, Any]) -> str:
    cam = board.get("камера") or {}
    plan = cam.get("план") or "ОБЩИЙ"
    nails = ", ".join(board.get("гвозди") or [])
    people = _people_line(board, face_camera=True)
    screen = board.get("экран") or {}
    bits = [
        f"{plan} план. Место: {board.get('место') or 'сцена'}.",
        f"Гвозди площадки (не двигать): {nails}." if nails else "",
        f"{people}." if people else "Персонажи сцены лицом к камере.",
        (
            f"Камера у {cam.get('где') or 'входа'}, смотрит на {cam.get('смотрит') or 'центр'}, "
            f"высота {cam.get('высота') or 'глаза'}."
        ),
    ]
    scr = []
    if screen.get("L"):
        scr.append(f"слева {screen['L']}")
    if screen.get("R"):
        scr.append(f"справа {screen['R']}")
    if screen.get("ближе"):
        scr.append(f"ближе {screen['ближе']}")
    if screen.get("дальше"):
        scr.append(f"дальше {screen['дальше']}")
    if scr:
        bits.append("В кадре: " + ", ".join(scr) + ".")
    bits.append(
        "Все персонажи лицом к камере. Один кадр, не коллаж. "
        "Не переставлять гвозди и людей."
    )
    return " ".join(b for b in bits if b)


def compile_parent_action(board: dict[str, Any]) -> str:
    cam = board.get("камера") or {}
    nails = ", ".join(board.get("гвозди") or [])
    people = _people_line(board, face_camera=True)
    loc = board.get("место") or "место"
    return (
        f"место {loc}, гвозди: {nails}. {people}. "
        f"камера у {cam.get('где') or loc} смотрит на {cam.get('смотрит') or loc}, "
        "лицом к камере"
    )


def _delta_ru(parent: dict[str, Any], child: dict[str, Any], delta: str) -> str:
    pcam = parent.get("камера") or {}
    ccam = child.get("камера") or {}
    bits: list[str] = []
    if _fold(ccam.get("где")) != _fold(pcam.get("где")) or _fold(
        ccam.get("смотрит")
    ) != _fold(pcam.get("смотрит")):
        bits.append(
            f"камера встала у {ccam.get('где')}, смотрит на {ccam.get('смотрит')}"
        )
    if _plan(ccam.get("план")) != _plan(pcam.get("план")):
        bits.append(f"план {ccam.get('план')}")
    pstand = {
        _fold(r.get("кто") or r.get("имя")): r for r in (parent.get("стоит") or [])
    }
    for row in child.get("стоит") or []:
        old = pstand.get(_fold(row.get("кто") or row.get("имя") or ""))
        if not old:
            continue
        who = row.get("имя") or row.get("кто")
        if _fold(row.get("где")) != _fold(old.get("где")):
            bits.append(f"{who} шагнул к {row.get('где')}")
        elif _fold(row.get("лицо")) != _fold(old.get("лицо")):
            bits.append(f"{who} повернулся к {row.get('лицо')}")
    if not bits:
        if _SIZE_RE.search(delta or ""):
            bits.append(f"план {ccam.get('план')}, камера та же")
        else:
            bits.append("камера и люди как у родителя")
    return "; ".join(bits)


def compile_child_action(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    delta: str,
    beat: str,
) -> str:
    nails = ", ".join(parent.get("гвозди") or [])
    change = _delta_ru(parent, child, delta)
    body = _norm(beat)
    return (
        f"та же площадка ({nails}). изменение: {change}. "
        f"действие: {body}".strip()
    )


def compile_child_prompt(
    parent: dict[str, Any],
    child: dict[str, Any],
    *,
    delta: str,
    beat: str,
) -> str:
    nails = ", ".join(parent.get("гвозди") or [])
    people = _people_line(child, face_camera=False)
    change = _delta_ru(parent, child, delta)
    cam = child.get("камера") or {}
    return (
        f"Та же площадка что у родителя. Гвозди те же: {nails}. "
        f"{people}. "
        f"Изменение относительно родителя: {change}. "
        f"Камера у {cam.get('где')}, смотрит на {cam.get('смотрит')}, "
        f"план {cam.get('план')}. "
        f"Действие этого кадра: {_norm(beat)}. "
        "Не переставлять деревья, стены и людей, кроме этого изменения. "
        "Один кадр, не коллаж."
    )
