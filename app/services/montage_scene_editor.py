"""Редактор сцены на доске монтажа: состояние карточки кадра + подбор вариантов.

Одно место, откуда UI получает всё, что можно править у кадра:

- роль в покрытии (VO-родитель / дочерний шот) и кто родитель;
- формат сцены — шаблон ``T0…T10`` / ``X1``, ``X2`` из каталога
  ``templates/shot_templates/shot_templates.json``;
- крупность (план) и «действие» этого кадра;
- якорь закадра **этого** кадра (``attrs.биты[].якорь`` по слоту шота);
- общие поля сцены / VO-ячейки (полный закадр, персонажи, свет, предметы);
- лестница кадров сцены (``attrs.кадры``) с закадром каждого кадра.

Варианты («подобрать вариантами») собираются тем же знанием, что и ноды
группы сцен: каталог шаблонов, предвыбор по дереву «Выбор», текст ячейки
и короткое описание видимой сцены от оператора.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

from app.services.montage_coverage_ops import (
    COVERAGE_ANGLE_CHOICES,
    COVERAGE_LIGHT_CHOICES,
    COVERAGE_MOVE_CHOICES,
    COVERAGE_PLAN_CHOICES,
    canonical_stitch,
    choices_with_current,
    stitch_choices_for_ui,
    stitch_label,
)
from app.services.shot_templates import (
    compose_shot_action,
    format_shot_templates_catalog,
    normalize_template_id,
    parse_scene_chain,
    plain_scene_vo,
    select_template_when,
    template_choices_for_ui,
    template_ladder_for_ui,
)
from app.services.vo_shot_expand import (
    bits_from_attrs,
    coverage_shot_id,
    is_shot_child,
    main_action_text,
    planned_shots_from_attrs,
)

VARIANT_KINDS = ("action", "template", "anchors")
MAX_VARIANTS = 5


def _attrs(frame: Any) -> dict[str, Any]:
    raw = getattr(frame, "attrs", None)
    return raw if isinstance(raw, dict) else {}


def _cs(frame: Any) -> dict[str, Any]:
    raw = _attrs(frame).get("camera_subdivide")
    return raw if isinstance(raw, dict) else {}


def _norm(text: str) -> str:
    return " ".join((text or "").split())


def frame_template_id(frame: Any) -> str:
    """Шаблон сцены этого кадра: camera_subdivide → кадры[0]."""
    cs = _cs(frame)
    tid = normalize_template_id(str(cs.get("шаблон") or cs.get("template") or ""))
    if tid:
        return tid
    planned = planned_shots_from_attrs(frame)
    if planned:
        return normalize_template_id(
            str(planned[0].get("шаблон") or planned[0].get("template") or "")
        )
    return ""


def frame_plan(frame: Any) -> str:
    """Крупность кадра: camera_subdivide → attrs → кадры[0]."""
    cs = _cs(frame)
    for key in ("план", "крупность"):
        val = _norm(str(cs.get(key) or ""))
        if val:
            return val
    val = _norm(str(_attrs(frame).get("крупность") or ""))
    if val:
        return val
    planned = planned_shots_from_attrs(frame)
    if planned:
        return _norm(str(planned[0].get("план") or planned[0].get("plan") or ""))
    return ""


def frame_action(frame: Any) -> str:
    """Действие этого кадра (не цепь сцен ячейки)."""
    attrs = _attrs(frame)
    for key in ("shot01_action", "действие"):
        val = _norm(str(attrs.get(key) or ""))
        if val:
            return val
    planned = planned_shots_from_attrs(frame)
    if planned:
        return _norm(str(planned[0].get("действие") or planned[0].get("action") or ""))
    return ""


def frame_place(frame: Any) -> str:
    cs = _cs(frame)
    for src in (cs.get("место"), _attrs(frame).get("place"), _attrs(frame).get("место")):
        val = _norm(str(src or ""))
        if val:
            return val
    planned = planned_shots_from_attrs(frame)
    if planned:
        return _norm(str(planned[0].get("место") or planned[0].get("place") or ""))
    return ""


def scene_group(frames: list[Any], frame: Any) -> tuple[Any, list[Any]]:
    """VO-родитель ячейки + шоты с тем же parent_uuid.

    ``coverage_parent_id`` (X1 / «место уже было») — реюз still другой
    сцены, не членство в этой ячейке.
    """
    parent = frame
    if is_shot_child(frame):
        uid = str(_cs(frame).get("parent_uuid") or "").strip()
        if uid:
            found = next(
                (
                    fr
                    for fr in frames
                    if str(getattr(fr, "uuid", "") or "") == uid
                ),
                None,
            )
            if found is not None:
                parent = found
    parent_uuid = str(getattr(parent, "uuid", "") or "")
    members = [parent]
    if parent_uuid:
        for other in frames:
            if int(other.number) == int(parent.number):
                continue
            if str(_cs(other).get("parent_uuid") or "") == parent_uuid:
                members.append(other)
    members.sort(
        key=lambda m: (
            int(_cs(m).get("shot_index") or 0) or int(m.number or 0),
            int(m.number or 0),
        )
    )
    return parent, members


def cell_full_text(parent: Any, members: list[Any]) -> str:
    """Полный текст VO-ячейки: vo_cell_full → склейка кусков → текст родителя."""
    full = _norm(str(_attrs(parent).get("vo_cell_full") or ""))
    if full:
        return full
    joined = _norm(
        " ".join(
            _norm(str(getattr(m, "voiceover_text", "") or ""))
            for m in members
            if _norm(str(getattr(m, "voiceover_text", "") or ""))
        )
    )
    if joined:
        return joined
    return _norm(str(getattr(parent, "voiceover_text", "") or ""))


def anchor_positions(full: str, anchors: list[str]) -> list[int]:
    """Позиции якорей в тексте по порядку. -1 — якорь не найден."""
    text = _norm(full)
    lower = text.lower()
    out: list[int] = []
    cursor = 0
    for raw in anchors:
        anchor = _norm(raw)
        if not anchor:
            out.append(-1)
            continue
        idx = text.find(anchor, cursor)
        if idx < 0:
            idx = lower.find(anchor.lower(), cursor)
        out.append(idx)
        if idx >= 0:
            cursor = idx + max(len(anchor), 1)
    return out


def split_vo_by_anchors(full: str, anchors: list[str]) -> list[str]:
    """Нарезка ячейки по якорям: якорь = начало своего кадра.

    Первый кусок всегда начинается с начала текста (даже если первый якорь
    стоит не на первом слове) — иначе хвост ячейки теряется. Ненайденный
    якорь склеивается с предыдущим куском.
    """
    text = _norm(full)
    if not text or not anchors:
        return [text] if text else []
    positions = anchor_positions(text, anchors)
    starts: list[int] = []
    for i, pos in enumerate(positions):
        if i == 0:
            starts.append(0)
            continue
        if pos < 0:
            continue
        if starts and pos <= starts[-1]:
            continue
        starts.append(pos)
    parts: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        parts.append(text[start:end].strip())
    return [p for p in parts if p]


def normalize_anchor_rows(raw: Any) -> list[dict[str, Any]]:
    """Якоря от UI → биты[] с порядком и единственным «главный»."""
    rows: list[dict[str, Any]] = []
    if not isinstance(raw, list):
        return rows
    for item in raw:
        if isinstance(item, str):
            item = {"якорь": item}
        if not isinstance(item, dict):
            continue
        anchor = _norm(str(item.get("якорь") or item.get("anchor") or ""))
        if not anchor:
            continue
        rows.append(
            {
                "порядок": len(rows) + 1,
                "якорь": anchor,
                "изменение": _norm(
                    str(item.get("изменение") or item.get("change") or "")
                ),
                "главный": bool(item.get("главный") or item.get("main")),
            }
        )
    if rows and not any(r["главный"] for r in rows):
        rows[0]["главный"] = True
    else:
        main_seen = False
        for row in rows:
            if row["главный"] and not main_seen:
                main_seen = True
            else:
                row["главный"] = False
    return rows


def _plain_names(raw: Any) -> str:
    if isinstance(raw, str):
        return _norm(raw)
    if isinstance(raw, list):
        names: list[str] = []
        for item in raw:
            if isinstance(item, str):
                names.append(_norm(item))
            elif isinstance(item, dict):
                names.append(
                    _norm(
                        str(
                            item.get("name")
                            or item.get("имя")
                            or item.get("code")
                            or item.get("id")
                            or ""
                        )
                    )
                )
        return ", ".join(x for x in names if x)
    if isinstance(raw, dict):
        return _plain_names(list(raw.values()))
    return ""


def _attr_text(frame: Any, *keys: str) -> str:
    attrs = _attrs(frame)
    for key in keys:
        val = attrs.get(key)
        text = _plain_names(val) if isinstance(val, (list, dict)) else _norm(str(val or ""))
        if text:
            return text
    return ""


def _cs_text(frame: Any, *keys: str) -> str:
    cs = _cs(frame)
    for key in keys:
        val = _norm(str(cs.get(key) or ""))
        if val:
            return val
    return ""


def _shot_field(frame: Any, *keys: str) -> str:
    """Значение шота: camera_subdivide → attrs → кадры[]."""
    found = _cs_text(frame, *keys)
    if found:
        return found
    attrs = _attrs(frame)
    for key in keys:
        val = _norm(str(attrs.get(key) or ""))
        if val:
            return val
    planned = planned_shots_from_attrs(frame)
    if planned:
        item = planned[0]
        for key in keys:
            val = _norm(str(item.get(key) or ""))
            if val:
                return val
    return ""


def _who_from_kadry(parent: Any) -> str:
    seen: list[str] = []
    for item in planned_shots_from_attrs(parent):
        text = _plain_names(
            item.get("персонажи")
            or item.get("кто_в_кадре")
            or item.get("who")
            or ""
        )
        if text and text not in seen:
            seen.append(text)
    return ", ".join(seen)


def _first_attr(frames: list[Any], *keys: str) -> str:
    for fr in frames:
        text = _attr_text(fr, *keys)
        if text:
            return text
    return ""


def _vo_anchor_hint(text: str) -> str:
    """Короткий якорь из начала закадра кадра, если бит шота не найден."""
    words = _norm(text).split()
    if not words:
        return ""
    return " ".join(words[:8])


def _assign_bits_to_members(
    bits: list[dict[str, Any]], members: list[Any]
) -> list[int | None]:
    """Какой кадр группы владеет битом. None — бит ещё без визуального кадра."""
    n_bits = len(bits)
    n_members = len(members)
    if n_bits == 0 or n_members == 0:
        return [None] * n_bits
    if n_members == 1:
        return [0] * n_bits
    if n_bits == n_members:
        return list(range(n_bits))
    used: set[int] = set()
    out: list[int | None] = []
    for item in bits:
        anchor = _norm(str(item.get("якорь") or "")).casefold()
        found: int | None = None
        if anchor:
            for i, member in enumerate(members):
                if i in used:
                    continue
                vo = _norm(str(getattr(member, "voiceover_text", "") or "")).casefold()
                if anchor and vo and anchor in vo:
                    found = i
                    break
        out.append(found)
        if found is not None:
            used.add(found)
    return out


def _cell_anchor_rows(
    parent: Any, members: list[Any], full: str
) -> tuple[list[dict[str, Any]], list[str]]:
    bits = bits_from_attrs(parent)
    ordered = sorted(bits, key=lambda item: int(item.get("порядок") or 0))
    anchors = [_norm(str(item.get("якорь") or "")) for item in ordered]
    positions = anchor_positions(full, anchors)
    owners = _assign_bits_to_members(ordered, members)
    rows: list[dict[str, Any]] = []
    for i, item in enumerate(ordered):
        owner_i = owners[i] if i < len(owners) else None
        owner = members[owner_i] if owner_i is not None else None
        rows.append(
            {
                "порядок": int(item.get("порядок") or i + 1),
                "якорь": anchors[i],
                "изменение": _norm(
                    str(item.get("изменение") or item.get("глагол") or "")
                ),
                "главный": bool(item.get("главный")),
                "offset": positions[i],
                "found": positions[i] >= 0,
                "cell_index": i,
                "frame_number": int(owner.number) if owner is not None else None,
            }
        )
    parts = split_vo_by_anchors(full, anchors) if anchors else []
    return rows, parts


def _anchor_payload(parent: Any, full: str) -> dict[str, Any]:
    """Все якоря ячейки (совместимость). Для UI кадра — ``_frame_anchor_payload``."""
    rows, parts = _cell_anchor_rows(parent, [parent], full)
    return {
        "text": full,
        "bits": rows,
        "preview": parts,
        "covers_text": _norm(" ".join(parts)) == _norm(full) if parts else False,
    }


def _frame_anchor_payload(
    parent: Any, members: list[Any], frame: Any, full: str
) -> dict[str, Any]:
    """Якоря только этого кадра + полный список ячейки для склейки при сохранении."""
    rows, parts = _cell_anchor_rows(parent, members, full)
    frame_no = int(frame.number)
    frame_rows = [row for row in rows if row.get("frame_number") == frame_no]
    frame_text = _norm(str(getattr(frame, "voiceover_text", "") or ""))
    if not frame_rows and frame_text:
        hint = _vo_anchor_hint(frame_text)
        if hint:
            frame_rows = [
                {
                    "порядок": int(_cs(frame).get("shot_index") or 0) or 1,
                    "якорь": hint,
                    "изменение": "",
                    "главный": False,
                    "offset": 0,
                    "found": True,
                    "cell_index": None,
                    "frame_number": frame_no,
                    "derived": True,
                }
            ]
    return {
        "text": frame_text,
        "bits": frame_rows,
        "preview": [frame_text] if frame_text else [],
        "covers_text": bool(
            frame_rows
            and frame_text
            and any(
                frame_text.casefold().startswith(_norm(str(row["якорь"])).casefold())
                for row in frame_rows
                if _norm(str(row.get("якорь") or ""))
            )
        ),
        "cell": {
            "text": full,
            "bits": rows,
            "preview": parts,
            "covers_text": _norm(" ".join(parts)) == _norm(full) if parts else False,
        },
        "can_add": len(members) <= 1,
    }


def frame_board_scene_cell(frames: list[Any], frame: Any) -> dict[str, Any]:
    """Сводка для клетки монтажа: якорь ЭТОГО кадра + общие поля сцены."""
    parent, members = scene_group(frames, frame)
    full = cell_full_text(parent, members)
    payload = _frame_anchor_payload(parent, members, frame, full)
    bits = list(payload.get("bits") or [])
    scene = _scene_common(parent, members)
    return {
        "shot_anchors": len(bits),
        "shot_anchor": str(bits[0].get("якорь") or "") if bits else "",
        "scene_place": scene.get("place") or "",
        "scene_set": scene.get("set") or "",
        "scene_characters": scene.get("characters") or "",
        "scene_lighting": scene.get("lighting") or "",
        "vo_scene_number": int(parent.number),
        "vo_scene_size": len(members),
    }


def frame_bits_count(frames: list[Any], frame: Any) -> int:
    """Сколько якорей принадлежит этому кадру (не всей ячейке)."""
    return int(frame_board_scene_cell(frames, frame)["shot_anchors"])


def _scene_common(parent: Any, members: list[Any]) -> dict[str, Any]:
    """Поля, общие для VO-ячейки / сцены, а не для отдельного шота."""
    lookup = [parent, *[m for m in members if m is not parent]]
    places: list[str] = []
    for member in members:
        place = frame_place(member)
        if place and place not in places:
            places.append(place)
    sets: list[str] = []
    for member in members:
        st = _cs_text(member, "набор", "set")
        if st and st not in sets:
            sets.append(st)
    characters = _first_attr(
        lookup, "персонажи_сцены", "персонажи", "characters", "persons"
    ) or _who_from_kadry(parent)
    return {
        "id_scene": _first_attr(lookup, "shot01_id_scene", "id_scene")
        or _cs_text(parent, "сцена", "scene"),
        "scene_no": _cs_text(parent, "сцена", "scene"),
        "place": places[0] if len(places) == 1 else (places[0] if places else ""),
        "places": places,
        "set": sets[0] if len(sets) == 1 else " · ".join(sets),
        "characters": characters,
        "lighting": _first_attr(
            lookup, "освещение_сцены", "scene_lighting", "освещение", "lighting"
        ),
        "props": _first_attr(
            lookup, "предметы", "shot01_props", "items_seed", "items"
        ),
        "accent": _first_attr(lookup, "акцент", "accent"),
        "sense": _first_attr(lookup, "смысл_сцены", "scene_sense"),
        "visual_type": _first_attr(lookup, "тип_сцены", "visual_type"),
        "bg": _first_attr(lookup, "фон", "shot01_bg"),
        "feature": _first_attr(lookup, "особенность_сцены", "scene_feature"),
    }


def _shots_payload(parent: Any, members: list[Any]) -> list[dict[str, Any]]:
    planned = planned_shots_from_attrs(parent)
    by_id = {
        _norm(str(item.get("id") or "")): item for item in planned if item.get("id")
    }
    out: list[dict[str, Any]] = []
    for i, item in enumerate(planned):
        sid = _norm(str(item.get("id") or ""))
        owner = next(
            (m for m in members if coverage_shot_id(m) == sid and sid),
            members[i] if i < len(members) else None,
        )
        out.append(
            {
                "id": sid,
                "порядок": int(item.get("порядок") or i + 1),
                "parent_id": _norm(str(item.get("parent_id") or "")),
                "шаблон": normalize_template_id(str(item.get("шаблон") or "")),
                "план": _norm(str(item.get("план") or item.get("plan") or "")),
                "ракурс": _norm(str(item.get("ракурс") or item.get("angle") or "")),
                "движение": _norm(str(item.get("движение") or item.get("move") or "")),
                "место": _norm(str(item.get("место") or item.get("place") or "")),
                "действие": _norm(str(item.get("действие") or item.get("action") or "")),
                "закадр": _norm(str(item.get("закадр") or "")),
                "frame_number": int(owner.number) if owner is not None else None,
            }
        )
    # Кадры группы без строки в кадры[] — показываем, чтобы не терялись.
    for m in members:
        sid = coverage_shot_id(m)
        if sid and sid in by_id:
            continue
        if any(row["frame_number"] == int(m.number) for row in out):
            continue
        out.append(
            {
                "id": sid,
                "порядок": int(_cs(m).get("shot_index") or 0) or int(m.number or 0),
                "parent_id": "",
                "шаблон": frame_template_id(m),
                "план": frame_plan(m),
                "ракурс": _norm(str(_cs(m).get("ракурс") or "")),
                "движение": _norm(str(_cs(m).get("движение") or "")),
                "место": frame_place(m),
                "действие": frame_action(m),
                "закадр": _norm(str(getattr(m, "voiceover_text", "") or "")),
                "frame_number": int(m.number),
            }
        )
    return out


def build_scene_editor_state(frames: list[Any], frame: Any) -> dict[str, Any]:
    """Всё редактируемое у кадра одним payload для панели монтажа."""
    parent, members = scene_group(frames, frame)
    is_child = frame is not parent
    full = cell_full_text(parent, members)
    scene_action = main_action_text(parent)
    chain = [
        {
            "n": int(row["n"]),
            "place": _norm(str(row.get("place") or "")),
            "action": _norm(str(row.get("action") or "")),
            "vo": plain_scene_vo(str(row.get("vo") or "")),
        }
        for row in parse_scene_chain(scene_action)
    ]
    current_tid = frame_template_id(frame) or frame_template_id(parent)
    place = frame_place(frame) or frame_place(parent)
    auto_tid = ""
    if chain:
        own = next(
            (
                row
                for row in chain
                if place and row["place"].casefold() == place.casefold()
            ),
            chain[0],
        )
        auto_tid = select_template_when(
            {"blob": f"{own['place']} {own['action']} {own['vo']}", "place": own["place"]}
        )
    elif scene_action or full:
        auto_tid = select_template_when({"blob": f"{place} {scene_action} {full}", "place": place})

    plan_current = frame_plan(frame)
    angle_current = _shot_field(frame, "ракурс", "angle")
    move_current = _shot_field(frame, "движение", "move")
    stitch_current = canonical_stitch(
        _shot_field(frame, "переход", "тип_стыка", "stitch", "transition")
    )
    plan_choices = choices_with_current(COVERAGE_PLAN_CHOICES, plan_current)

    anchors = _frame_anchor_payload(parent, members, frame, full)
    cell_bits = list((anchors.get("cell") or {}).get("bits") or [])
    shots = _shots_payload(parent, members)
    by_fn: dict[int, list[dict[str, Any]]] = {}
    for row in cell_bits:
        fn = row.get("frame_number")
        if fn is None:
            continue
        by_fn.setdefault(int(fn), []).append(row)
    members_by_no = {int(m.number): m for m in members}
    for shot in shots:
        fn = shot.get("frame_number")
        owned = by_fn.get(int(fn), []) if fn is not None else []
        shot["якорь"] = owned[0]["якорь"] if owned else ""
        owner = members_by_no.get(int(fn)) if fn is not None else None
        if owner is not None:
            if not shot.get("ракурс"):
                shot["ракурс"] = _cs_text(owner, "ракурс", "angle")
            if not shot.get("движение"):
                shot["движение"] = _cs_text(owner, "движение", "move")

    cs = _cs(frame)
    meaning = _norm(str(getattr(frame, "meaning", "") or ""))
    duration = getattr(frame, "duration_seconds", None)
    try:
        duration_val = float(duration) if duration is not None else None
    except (TypeError, ValueError):
        duration_val = None

    scene_fields = _scene_common(parent, members)
    scene_fields["anchors"] = anchors["cell"]
    scene_fields["action"] = scene_action
    scene_fields["chain"] = chain
    light_current = _norm(str(scene_fields.get("lighting") or ""))
    set_current = _norm(
        str(scene_fields.get("set") or cs.get("набор") or cs.get("set") or "")
    )

    return {
        "frame": {
            "frame_id": int(getattr(frame, "id", 0) or 0),
            "number": int(frame.number),
            "uuid": str(getattr(frame, "uuid", "") or ""),
            "role": "child" if is_child or is_shot_child(frame) else "parent",
            "shot_id": coverage_shot_id(frame),
            "shot_index": int(_cs(frame).get("shot_index") or 0) or None,
            "shots_in_beat": int(_cs(frame).get("shots_in_beat") or 0) or None,
            "place": place,
            "angle": angle_current,
            "move": move_current,
            "set": set_current,
            "meaning": meaning,
            "duration": duration_val,
        },
        "parent": {
            "number": int(parent.number),
            "frame_id": int(getattr(parent, "id", 0) or 0),
            "shot_id": coverage_shot_id(parent),
        },
        "parent_choices": [
            {
                "number": int(m.number),
                "role": "child" if is_shot_child(m) else "parent",
                "vo": _norm(str(getattr(m, "voiceover_text", "") or ""))[:70],
            }
            for m in frames
            if int(m.number) != int(frame.number)
        ],
        "group": [int(m.number) for m in members],
        "vo": {
            "frame_text": _norm(str(getattr(frame, "voiceover_text", "") or "")),
            "cell_full": full,
        },
        "plan": {"current": plan_current, "choices": plan_choices},
        "angle": {
            "current": angle_current,
            "choices": choices_with_current(COVERAGE_ANGLE_CHOICES, angle_current),
        },
        "move": {
            "current": move_current,
            "choices": choices_with_current(COVERAGE_MOVE_CHOICES, move_current),
        },
        "stitch": {
            "current": stitch_current,
            "label": stitch_label(stitch_current),
            "choices": stitch_choices_for_ui(stitch_current),
        },
        "light": {
            "current": light_current,
            "choices": choices_with_current(COVERAGE_LIGHT_CHOICES, light_current),
        },
        "set": {"current": set_current},
        "action": {"current": frame_action(frame)},
        "scene_action": {"current": scene_action, "chain": chain},
        "template": {
            "current": current_tid,
            "auto": auto_tid,
            "ladder": template_ladder_for_ui(current_tid or auto_tid),
            "choices": template_choices_for_ui(),
            "group_len": len(members),
        },
        "anchors": {
            "text": anchors["text"],
            "bits": anchors["bits"],
            "preview": anchors["preview"],
            "covers_text": anchors["covers_text"],
            "can_add": anchors["can_add"],
        },
        "scene": scene_fields,
        "shots": shots,
    }


_VARIANT_JSON_HINT = (
    'Ответ — только JSON: {"варианты": [ … ]}. Без markdown-обёртки, '
    "без пояснений вокруг JSON."
)


def _variant_prompt_action(state: dict[str, Any], desc: str, count: int) -> str:
    frame = state["frame"]
    tpl = state["template"]
    ladder = "\n".join(
        f"  {row['n']}. {row['shot_id']} · {row['plan']} · роль {row['role']}: {row['action']}"
        for row in tpl.get("ladder") or []
    )
    return "\n".join(
        part
        for part in [
            "# ЗАДАЧА",
            f"Перепиши «действие» кадра #{frame['number']} ролика. "
            f"Дай {count} разных варианта одного и того же шага — не {count} "
            "разных сцен.",
            "",
            "# ЧТО ЭТО ЗА КАДР",
            f"роль в покрытии: {'дочерний шот' if frame['role'] == 'child' else 'VO-родитель ячейки'}",
            f"шаблон сцены: {tpl.get('current') or '—'} (предвыбор по дереву: {tpl.get('auto') or '—'})",
            f"позиция в лестнице: {frame.get('shot_index') or 1} из {frame.get('shots_in_beat') or tpl.get('group_len') or 1}",
            f"крупность: {state['plan']['current'] or '—'}",
            f"место: {frame.get('place') or '—'}",
            f"закадр этого кадра: «{state['vo']['frame_text'] or '—'}»",
            f"закадр всей ячейки: «{state['vo']['cell_full'] or '—'}»",
            f"цепь сцен ячейки:\n{state['scene_action']['current'] or '—'}",
            f"текущее действие: «{state['action']['current'] or '—'}»",
            f"лестница шаблона:\n{ladder}" if ladder else "",
            "",
            "# ЧТО ВИДНО В КАДРЕ (от оператора)",
            desc or "(оператор не уточнил — опирайся на закадр и лестницу)",
            "",
            "# ПРАВИЛА",
            "- «действие» = полное описание ЭТОГО кадра: помещение, кто в "
            "кадре, одежда, видимый поступок, эмоция если она читается.",
            "- Это ШАГ развития, а не бытовой глагол. «взял / посмотрел / "
            "переложил / достал / открыл» как самоцель — брак: покажи, что "
            "поменялось в ситуации после кадра.",
            "- Не пересказывай закадр словами закадра и не описывай кадр "
            "соседа: у каждого кадра свой поступок.",
            "- Роль из лестницы шаблона держи: master — география, insert — "
            "деталь предмета, reaction — лицо после жеста.",
            "- Не копируй слоганы каталога («тянет / открывает / берёт», "
            "«лицо после», «реакция») — это роль, а не текст.",
            "",
            "# ФОРМАТ",
            _VARIANT_JSON_HINT,
            'Элемент: {"действие": "…", "план": "КРУПНЫЙ", "почему": "чем этот '
            'вариант отличается"}. «план» — из списка: '
            + ", ".join(state["plan"]["choices"]),
        ]
        if part is not None
    )


def _variant_prompt_template(state: dict[str, Any], desc: str, count: int) -> str:
    tpl = state["template"]
    catalog = format_shot_templates_catalog(max_chars=9000)
    return "\n".join(
        [
            "# ЗАДАЧА",
            f"Подбери {count} варианта формата сцены (шаблон T*/X*) для "
            f"ячейки закадра кадра #{state['frame']['number']}. "
            "Первый вариант — самый точный по дереву «Выбор».",
            "",
            "# ЯЧЕЙКА",
            f"закадр: «{state['vo']['cell_full'] or '—'}»",
            f"цепь сцен: {state['scene_action']['current'] or '—'}",
            f"место: {state['frame'].get('place') or '—'}",
            f"сейчас стоит: {tpl.get('current') or '—'}; "
            f"предвыбор по дереву: {tpl.get('auto') or '—'}",
            f"кадров в группе сейчас: {tpl.get('group_len')}",
            "",
            "# ЧТО ВИДНО В КАДРЕ (от оператора)",
            desc or "(оператор не уточнил)",
            "",
            catalog,
            "",
            "# ПРАВИЛА",
            "- Шаблон отвечает на вопрос дерева «Выбор», а не выбирается "
            "ради чередования: T8>T8>T8 — норма.",
            "- T1 — только речь (диалог/спор/допрос). Молчаливая передача "
            "предмета — T2, приказ/новость — T7, длительная работа руками — T5.",
            "- T3 — ТОЛЬКО смена места, без находки, взгляда и работы рук.",
            "",
            "# ФОРМАТ",
            _VARIANT_JSON_HINT,
            'Элемент: {"шаблон": "T5", "почему": "какой вопрос дерева дал «да»", '
            '"лестница": "T5-K0;T5-K1;T5-K2"}',
        ]
    )


def _variant_prompt_anchors(state: dict[str, Any], desc: str, count: int) -> str:
    anchors = state["anchors"]
    frame_text = state["vo"].get("frame_text") or anchors.get("text") or ""
    current = "; ".join(
        f"«{row['якорь']}»" for row in anchors.get("bits") or [] if row.get("якорь")
    )
    return "\n".join(
        [
            "# ЗАДАЧА",
            f"Подбери {count} варианта якоря закадра для кадра "
            f"#{state['frame']['number']} — только этого шота, не всей ячейки.",
            "Якорь = дословный кусок ЗАКАДРА ЭТОГО КАДРА, с которого "
            "начинается этот визуальный кадр.",
            "",
            "# ЗАКАДР ЭТОГО КАДРА (из него якорь, дословно)",
            frame_text or "—",
            "",
            "# ЯЧЕЙКА ЦЕЛИКОМ (контекст, не режь её)",
            state["vo"].get("cell_full") or "—",
            "",
            f"# ЯКОРЬ ЭТОГО КАДРА СЕЙЧАС\n{current or '—'}",
            "",
            "# ЧТО ВИДНО В КАДРЕ (от оператора)",
            desc or "(оператор не уточнил)",
            "",
            "# ПРАВИЛА",
            "- Каждый «якорь» — ДОСЛОВНАЯ подстрока закадра ЭТОГО кадра. "
            "Ни одного своего слова, ни одной правки пунктуации.",
            "- Один вариант = один якорь (один бит). Не предлагай нарезку "
            "соседних кадров.",
            "- «изменение» пиши как «было → стало» для этого кадра.",
            "",
            "# ФОРМАТ",
            _VARIANT_JSON_HINT,
            'Элемент: {"почему": "…", "биты": [{"порядок": 1, "якорь": '
            '"дословный кусок этого кадра", "изменение": "было → стало"}]}',
        ]
    )


_PROMPT_BUILDERS = {
    "action": _variant_prompt_action,
    "template": _variant_prompt_template,
    "anchors": _variant_prompt_anchors,
}


def build_variant_prompt(
    state: dict[str, Any],
    *,
    kind: str,
    desc: str = "",
    count: int = 3,
) -> str:
    builder = _PROMPT_BUILDERS.get(kind)
    if builder is None:
        raise ValueError(f"неизвестный вид вариантов: {kind}")
    return builder(state, _norm(desc), max(1, min(MAX_VARIANTS, int(count))))


def _clean_action_variant(raw: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    action = _norm(str(raw.get("действие") or raw.get("action") or ""))
    if not action:
        return None
    plan = _norm(str(raw.get("план") or raw.get("plan") or "")).upper()
    if plan and plan not in {p.upper() for p in state["plan"]["choices"]}:
        plan = ""
    return {
        "действие": action,
        "план": plan,
        "почему": _norm(str(raw.get("почему") or raw.get("why") or "")),
    }


def _clean_template_variant(raw: dict[str, Any], _state: dict[str, Any]) -> dict[str, Any] | None:
    from app.services.shot_templates import template_exists

    tid = normalize_template_id(str(raw.get("шаблон") or raw.get("template") or ""))
    if not tid or not template_exists(tid):
        return None
    return {
        "шаблон": tid,
        "лестница": _norm(str(raw.get("лестница") or "")),
        "почему": _norm(str(raw.get("почему") or raw.get("why") or "")),
    }


def _clean_anchors_variant(raw: dict[str, Any], state: dict[str, Any]) -> dict[str, Any] | None:
    rows = normalize_anchor_rows(raw.get("биты") or raw.get("bits"))
    if not rows:
        return None
    full = (
        state.get("vo", {}).get("frame_text")
        or state["anchors"].get("text")
        or ""
    )
    positions = anchor_positions(full, [row["якорь"] for row in rows])
    kept: list[dict[str, Any]] = []
    for row, pos in zip(rows, positions, strict=False):
        if pos < 0:
            continue
        kept.append(row)
    # В мультишоте у кадра один якорь — лишние отбрасываем.
    if int(state.get("template", {}).get("group_len") or 0) > 1:
        kept = kept[:1]
    kept = normalize_anchor_rows(kept)
    if not kept:
        return None
    parts = split_vo_by_anchors(full, [row["якорь"] for row in kept])
    return {
        "биты": kept,
        "preview": parts,
        "dropped": len(rows) - len(kept),
        "почему": _norm(str(raw.get("почему") or raw.get("why") or "")),
    }


_CLEANERS = {
    "action": _clean_action_variant,
    "template": _clean_template_variant,
    "anchors": _clean_anchors_variant,
}


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _variants_payload(reply: str) -> dict[str, Any] | None:
    """JSON из ответа: голый объект → fence → поиск по маркеру «варианты».

    Голый ``json.loads`` идёт первым: маркерный поиск не находит ключ, если
    модель отдала его в escape-виде (``\\u0432\\u0430…``).
    """
    raw = (reply or "").strip()
    if not raw:
        return None
    for cand in (raw, *(m.group(1) for m in _FENCE_RE.finditer(raw))):
        try:
            data = json.loads(cand)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and (
            isinstance(data.get("варианты"), list) or isinstance(data.get("variants"), list)
        ):
            return data
    from app.services.scene_design.agents import extract_json_object

    return extract_json_object(raw, marker_keys=("варианты", "variants"))


def parse_variants(reply: str, *, kind: str, state: dict[str, Any]) -> list[dict[str, Any]]:
    """Ответ модели → чистые варианты. Мусор молча отбрасываем."""
    payload = _variants_payload(reply)
    if payload is None:
        return []
    raw_list = payload.get("варианты") or payload.get("variants") or []
    if not isinstance(raw_list, list):
        return []
    cleaner = _CLEANERS[kind]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        item = cleaner(raw, state)
        if item is None:
            continue
        key = re.sub(r"\W+", "", str(item.get("действие") or item.get("шаблон") or "")).lower()
        if kind == "anchors":
            key = "|".join(row["якорь"] for row in item["биты"])
        if key and key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= MAX_VARIANTS:
            break
    return out


async def generate_scene_variants(
    state: dict[str, Any],
    *,
    kind: str,
    desc: str = "",
    count: int = 3,
    project_id: int | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Подбор вариантов знанием группы нод сцен (каталог T/X + закадр + описание)."""
    if kind not in VARIANT_KINDS:
        raise ValueError(f"неизвестный вид вариантов: {kind}")
    from app.services.gpt_client import gpt_ask_fresh

    prompt = build_variant_prompt(state, kind=kind, desc=desc, count=count)
    reply = await gpt_ask_fresh(prompt, timeout=timeout, project_id=project_id)
    variants = parse_variants(reply, kind=kind, state=state)
    logger.info(
        "montage scene variants #{} kind={} frame={} → {}",
        project_id,
        kind,
        state["frame"]["number"],
        len(variants),
    )
    return {
        "ok": bool(variants),
        "kind": kind,
        "variants": variants,
        "raw_len": len(reply or ""),
    }


def preview_template_ladder(state: dict[str, Any], tid: str) -> dict[str, Any]:
    """Что даст выбор шаблона: план/роль на каждый существующий кадр группы."""
    rows = template_ladder_for_ui(tid)
    group = int(state["template"].get("group_len") or 1)
    place = state["frame"].get("place") or ""
    scene_action = ""
    chain = state["scene_action"].get("chain") or []
    if chain:
        scene_action = chain[0].get("action") or ""
    out: list[dict[str, Any]] = []
    for i in range(max(group, len(rows))):
        row = rows[i] if i < len(rows) else None
        out.append(
            {
                "position": i + 1,
                "has_frame": i < group,
                "shot_id": row["shot_id"] if row else "",
                "план": row["plan"] if row else "",
                "роль": row["role"] if row else "",
                "действие": compose_shot_action(
                    plan=row["plan"],
                    place=place,
                    scene_action=scene_action,
                    catalog_action=row["action"],
                    catalog_role=row["role"],
                )
                if row
                else "",
            }
        )
    return {"шаблон": normalize_template_id(tid), "rows": out, "group_len": group}
