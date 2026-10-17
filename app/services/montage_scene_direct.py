"""«Улучшить сцену» — прямой запуск, без группы нод script_frames_qc.

Промт оператора → персонажи + кадры с покрытием. Паспорта сцены нет.
Закадр только нарезаем на шоты.
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_apply import extract_apply_ops_json
from app.services.montage_coverage_ops import apply_coverage_scene_action
from app.services.montage_scene_editor import cell_full_text, scene_group
from app.services.scene_character_match import (
    format_character_codes,
    format_character_labels,
    intersect_codes_with_registry,
    load_character_registry,
    match_registry_characters,
)
from app.services.scene_vo_split import split_scene_vo
from app.services.shot_templates import format_scene_chain, parse_scene_chain
from app.services.vo_shot_expand import _flag_attrs, _set_cs

REPORT_ATTR = "montage_improve_report"
_MASTER_PLANS = frozenset({"ОБЩИЙ", "СРЕДНИЙ", "ДАЛЬНИЙ"})
_PLAN_RE = re.compile(r"^(ДАЛЬНИЙ|ОБЩИЙ|СРЕДНИЙ|КРУПНЫЙ|ДЕТАЛЬ)$", re.I)
_JSON_FENCE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.S | re.I)


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split())


def _plan(raw: Any, *, master: bool = False) -> str:
    text = _norm(raw).upper().replace("ПЛАН", "").strip()
    if _PLAN_RE.match(text):
        plan = text
    else:
        plan = "ОБЩИЙ" if master else "СРЕДНИЙ"
    if master and plan not in _MASTER_PLANS:
        return "ОБЩИЙ"
    return plan


def build_direct_improve_prompt(
    *,
    operator_prompt: str,
    vo: str,
    place: str,
    registry_labels: str,
    matched_labels: str,
    passport_note: str = "",
) -> str:
    from app.services.montage_coverage_ops import (
        COVERAGE_ANGLE_CHOICES,
        COVERAGE_LIGHT_CHOICES,
        COVERAGE_MOVE_CHOICES,
        COVERAGE_PLAN_CHOICES,
        COVERAGE_STITCH_CHOICES,
    )
    from app.services.montage_scene_improve import SHOT_ACTION_BODY_RULES, SHOT_ROLES
    from app.services.scene_shot_grammar import OBJECTS

    order = (operator_prompt or "").strip() or "сформулируй сцену по закадру"
    stitch = ", ".join(key for key, _label in COVERAGE_STITCH_CHOICES)
    _ = place, passport_note, COVERAGE_LIGHT_CHOICES
    return (
        "Сначала обработай заказ оператора — он главный.\n"
        f"Заказ: {order}\n\n"
        f"{SHOT_ACTION_BODY_RULES}\n"
        "персонажи — только коды из реестра, кто в заказе или закадре.\n"
        "Кадры — строго по заказу: одно полное действие на кадр и камера.\n"
        "Закадр не переписывай и не режь по нему число кадров — его нарежет код.\n"
        f"план: {', '.join(COVERAGE_PLAN_CHOICES)}. "
        f"ракурс: {', '.join(COVERAGE_ANGLE_CHOICES)}. "
        f"движение: {', '.join(COVERAGE_MOVE_CHOICES)}. "
        f"стык: {stitch}. "
        f"роль: {', '.join(SHOT_ROLES)}. "
        f"объект: {', '.join(OBJECTS)}.\n\n"
        f"Закадр сцены:\n{vo}\n\n"
        f"Реестр персонажей проекта: {registry_labels or 'пусто'}\n"
        f"Уже совпали с текстом: {matched_labels or 'никто'}\n\n"
        "Ответ — JSON:\n"
        '{"сцена":"…","персонажи":"c01, c02","план_родителя":"ОБЩИЙ",'
        '"кадры":[{"действие":"коридор, слева стеллаж, справа окно; '
        'c01 следователь в центре тянет папку","план":"СРЕДНИЙ","ракурс":"3/4",'
        '"движение":"статика","стык":"cut","роль":"действие","объект":"тело"}]}\n'
    )


def _as_text(val: Any) -> str:
    if isinstance(val, list):
        return ", ".join(part for part in (_as_text(x) for x in val) if part)
    return _norm(val)


def _json_dict(text: str) -> dict[str, Any]:
    chunks: list[str] = []
    raw = (text or "").strip()
    if raw:
        chunks.append(raw)
        chunks.extend(_JSON_FENCE.findall(raw))
        start, end = raw.find("{"), raw.rfind("}")
        if start != -1 and end > start:
            chunks.append(raw[start : end + 1])
    for chunk in chunks:
        try:
            parsed = json.loads(chunk)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    extracted = extract_apply_ops_json(raw) if raw else None
    return dict(extracted) if isinstance(extracted, dict) else {}


def parse_direct_reply(reply: str) -> dict[str, Any]:
    text = (reply or "").strip()
    data = _json_dict(text)
    ops = data.get("ops") or data.get("actions") or []
    for op in ops:
        if not isinstance(op, dict):
            continue
        fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
        for key, val in {**op, **fields}.items():
            if key in {"fields", "ops", "actions", "frame_uuid", "uuid"}:
                continue
            if val not in (None, "") and key not in data:
                data[key] = val
        break
    chain = parse_scene_chain(text)
    shots = data.get("кадры") or data.get("shots") or []
    if not isinstance(shots, list):
        shots = []
    shots = [dict(s) for s in shots if isinstance(s, dict)]
    if not shots and chain:
        shots = [
            {
                "действие": str(row.get("action") or "").strip(),
                "план": "ОБЩИЙ" if i == 0 else "СРЕДНИЙ",
                "место": str(row.get("place") or "").strip(),
            }
            for i, row in enumerate(chain)
            if str(row.get("action") or "").strip()
        ]
    if not shots:
        acts = data.get("главное_действие")
        if isinstance(acts, list):
            shots = [
                {"действие": _norm(a), "план": "ОБЩИЙ" if i == 0 else "СРЕДНИЙ"}
                for i, a in enumerate(acts)
                if _norm(a)
            ]
        elif isinstance(acts, str) and parse_scene_chain(acts):
            shots = [
                {
                    "действие": str(row.get("action") or "").strip(),
                    "план": "ОБЩИЙ" if i == 0 else "СРЕДНИЙ",
                }
                for i, row in enumerate(parse_scene_chain(acts))
                if str(row.get("action") or "").strip()
            ]
    return {
        "сцена": _as_text(data.get("сцена") or data.get("scene") or ""),
        "место": _as_text(data.get("место") or data.get("place") or ""),
        "персонажи": _as_text(data.get("персонажи") or data.get("characters") or ""),
        "свет": _as_text(
            data.get("свет") or data.get("light") or data.get("освещение") or ""
        ),
        "предметы": _as_text(
            data.get("предметы") or data.get("props") or data.get("items") or ""
        ),
        "фон": _as_text(data.get("фон") or data.get("bg") or data.get("background") or ""),
        "смысл": _as_text(data.get("смысл") or data.get("sense") or ""),
        "акцент": _as_text(data.get("акцент") or data.get("accent") or ""),
        "особенность": _as_text(
            data.get("особенность") or data.get("feature") or ""
        ),
        "тип": _as_text(data.get("тип") or data.get("visual_type") or ""),
        "набор": _as_text(data.get("набор") or data.get("set") or ""),
        "план_родителя": _plan(
            data.get("план_родителя") or data.get("план") or "", master=True
        ),
        "промт_картинки": _as_text(
            data.get("промт_картинки")
            or data.get("image_prompt")
            or data.get("промт")
            or ""
        ),
        "кадры": shots,
    }


def pick_scene_characters(
    *,
    haystack: str,
    gpt_raw: str,
    entities: list[Any],
) -> list[dict[str, str]]:
    """Реестр ∩ текст (заказ+закадр). GPT может только сузить список."""
    mentioned = match_registry_characters(haystack, entities)
    gpt_chars = intersect_codes_with_registry(gpt_raw, entities)
    allowed = {row["code"] for row in mentioned}
    if gpt_chars:
        picked = [row for row in gpt_chars if row.get("code") in allowed]
        return picked or mentioned
    return mentioned


def default_parent_prompt(
    *,
    place: str,
    who: str,
    scene: str,
    plan: str,
) -> str:
    faces = who or "персонажи сцены"
    loc = place or "место сцены"
    body = scene or "видно место и кто в кадре"
    return (
        f"{plan} план. {loc}. {faces} лицом к камере. {body}. "
        "Один кадр, не коллаж."
    )


def _shot_action(shot: dict[str, Any]) -> str:
    return _norm(
        shot.get("действие") or shot.get("action") or shot.get("шаг") or ""
    )


def _action_tokens(text: str) -> set[str]:
    folded = re.sub(r"[^\wа-яё]+", " ", _norm(text).casefold(), flags=re.IGNORECASE)
    return {w for w in folded.split() if len(w) > 2}


def actions_too_similar(left: str, right: str) -> bool:
    a = _action_tokens(left)
    b = _action_tokens(right)
    if not a or not b:
        return False
    return len(a & b) >= 0.6 * min(len(a), len(b))


def vo_beat(piece: str) -> str:
    """Действие кадра из его куска закадра — не копия соседнего промта."""
    return _norm(piece)


def _is_parent_still_action(act: str, parent_action: str) -> bool:
    """Still родителя («лицом к камере») не считается кадром сцены."""
    text = _norm(act)
    if not text:
        return True
    if "лицом к камере" in text.casefold():
        return True
    parent = _norm(parent_action)
    return bool(parent) and text.casefold() == parent.casefold()


def unique_gpt_shots(
    shots: list[dict[str, Any]],
    *,
    parent_action: str,
) -> list[dict[str, Any]]:
    """Уникальные кадры GPT: без повтора и без still родителя."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for shot in shots:
        act = _shot_action(shot)
        if _is_parent_still_action(act, parent_action):
            continue
        key = act.casefold()
        if key in seen:
            continue
        seen.add(key)
        row = dict(shot)
        row["действие"] = act
        row["план"] = _plan(shot.get("план") or "СРЕДНИЙ")
        out.append(row)
    return out


def unique_gpt_beats(
    shots: list[dict[str, Any]],
    *,
    parent_action: str,
) -> list[tuple[str, str]]:
    """Уникальные действия GPT: без повтора и без still родителя."""
    return [
        (_shot_action(row), _plan(row.get("план") or "СРЕДНИЙ"))
        for row in unique_gpt_shots(shots, parent_action=parent_action)
    ]


def infer_light(*texts: Any) -> str:
    blob = " ".join(_norm(t) for t in texts if t).casefold()
    for needle, val in (
        ("ноч", "ночной"),
        ("закат", "закат"),
        ("рассвет", "рассвет"),
        ("туман", "туман"),
        ("контров", "контровой"),
        ("холодн", "холодный верхний"),
    ):
        if needle in blob:
            return val
    return "дневной"




def attach_shot_coverage(
    kadry: list[dict[str, Any]],
    *,
    place: str,
    cell_number: int,
) -> list[dict[str, Any]]:
    """Дописать план/ракурс/движение/стык/роль/объект на каждый шот."""
    from app.services.montage_scene_improve import normalize_shots

    packed = normalize_shots(kadry, cell_number=cell_number, place=place, anchors=1)
    for dest, src in zip(kadry, packed):
        dest["план"] = src.get("план") or dest.get("план") or "СРЕДНИЙ"
        dest["ракурс"] = src.get("ракурс") or dest.get("ракурс") or "фронт"
        dest["движение"] = src.get("движение") or dest.get("движение") or "статика"
        dest["стык"] = src.get("стык") or dest.get("стык") or "cut"
        dest["роль"] = src.get("роль") or dest.get("роль") or "действие"
        dest["объект"] = src.get("объект") or dest.get("объект") or "тело"
        if src.get("зона") and not dest.get("зона"):
            dest["зона"] = src["зона"]
    return kadry


def _beats_from_operator(operator_prompt: str, parent_action: str) -> list[tuple[str, str]]:
    from app.services.montage_scene_improve import loose_action_steps, operator_action_beats

    loose = loose_action_steps(operator_prompt, allow_sentences=False)
    raw = [_norm(row.get("action")) for row in loose if _norm(row.get("action"))]
    if len(raw) < 2:
        raw = operator_action_beats(operator_prompt)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for act in raw:
        if _is_parent_still_action(act, parent_action):
            continue
        key = act.casefold()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append((act, "СРЕДНИЙ"))
    return out


def _beats_from_vo(vo: str, parent_plan: str) -> list[tuple[str, str]]:
    pieces = split_scene_vo(vo)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for i, piece in enumerate(pieces):
        act = vo_beat(piece)
        if not act:
            continue
        key = act.casefold()
        if key in seen:
            act = f"{act} · кадр {i + 1}"
            key = act.casefold()
        seen.add(key)
        out.append((act, parent_plan if i == 0 else "СРЕДНИЙ"))
    return out


def build_kadry(
    *,
    vo: str,
    place: str,
    parent_plan: str,
    parent_action: str,
    shots: list[dict[str, Any]],
    cell_number: int,
    operator_prompt: str = "",
) -> list[dict[str, Any]]:
    """Кадры сцены из промта/GPT. Родительский still сюда не входит.

    Число кадров = число действий заказа, не число кусков закадра.
    Закадр только нарезаем на готовые шоты.
    """
    from app.services.montage_coverage_ops import MAX_IMPROVE_SHOTS
    from app.services.montage_scene_improve import is_raw_prompt_dump, split_vo_for_shots

    gpt_shots = [dict(s) for s in shots if isinstance(s, dict)]
    if operator_prompt and is_raw_prompt_dump(
        [{"action": _shot_action(s)} for s in gpt_shots],
        operator_prompt,
    ):
        gpt_shots = []
    rows = unique_gpt_shots(gpt_shots, parent_action=parent_action)
    op_rows = [
        {"действие": act, "план": plan}
        for act, plan in _beats_from_operator(operator_prompt, parent_action)
    ]
    if len(op_rows) > len(rows):
        rows = op_rows
    if not rows:
        rows = [
            {"действие": act, "план": plan}
            for act, plan in _beats_from_vo(vo, parent_plan)
        ]
    rows = rows[:MAX_IMPROVE_SHOTS]
    if not rows:
        return []
    parts = split_vo_for_shots(vo, len(rows))
    if len(parts) < len(rows):
        parts = list(parts) + [""] * (len(rows) - len(parts))
    master = f"{int(cell_number)}-K1"
    out: list[dict[str, Any]] = []
    used: set[str] = set()
    for i, (row, piece) in enumerate(zip(rows, parts)):
        act = _shot_action(row)
        if act.casefold() in used:
            act = f"{act} · кадр {i + 1}"
        used.add(act.casefold())
        shot = {
            "id": f"{int(cell_number)}-K{i + 1}",
            "parent_id": None if i == 0 else master,
            "порядок": i + 1,
            "сцена": 1,
            "план": _plan(row.get("план") or "СРЕДНИЙ"),
            "ракурс": row.get("ракурс") or row.get("angle") or "",
            "движение": row.get("движение") or row.get("move") or "",
            "стык": row.get("стык") or row.get("переход") or "",
            "роль": row.get("роль") or row.get("role") or "",
            "объект": row.get("объект") or row.get("object") or "",
            "место": _norm(row.get("место") or row.get("place") or place),
            "действие": act,
            "закадр": piece,
        }
        out.append(shot)
    return attach_shot_coverage(out, place=place, cell_number=cell_number)


async def improve_cell_scene(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    passport: dict[str, Any] | None = None,
    anchors: list[Any] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Прямой прогон одной VO-ячейки. ``anchors`` не режут текст — режет вес VO."""
    from app.services.gpt_client import gpt_ask_fresh
    from app.services.montage_action_gpt import apply_cell_passport

    _ = anchors
    frames = list(
        (
            await _load_frames(session, int(project.id))
        )
    )
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    parent, members = scene_group(frames, frame)
    vo = _norm(cell_full_text(parent, members))
    if not vo:
        raise RuntimeError("у сцены нет закадрового текста")
    _ = passport
    op_passport: dict[str, Any] = {}
    op_prompt = _norm(operator_prompt)
    place = ""

    entities = await load_character_registry(session, int(project.id))
    haystack = f"{op_prompt}\n{vo}"
    matched = match_registry_characters(haystack, entities)
    registry_labels = ", ".join(
        f"{str(e.code or '').strip()} · {str(e.name or '').strip()}".strip(" ·")
        for e in entities
        if str(getattr(e, "code", "") or "").strip()
        or str(getattr(e, "name", "") or "").strip()
    )

    parsed: dict[str, Any] = {}
    gpt_status = "fallback"
    chip_note = ", ".join(
        f"{key}={_norm(op_passport.get(key))}"
        for key in (
            "place",
            "characters",
            "light",
            "props",
            "bg",
            "sense",
            "accent",
            "feature",
            "set",
        )
        if _norm(op_passport.get(key))
    )
    prompt = build_direct_improve_prompt(
        operator_prompt=op_prompt,
        vo=vo,
        place=place,
        registry_labels=registry_labels,
        matched_labels=format_character_labels(matched),
        passport_note=chip_note,
    )
    try:
        reply = await gpt_ask_fresh(
            prompt, timeout=timeout, project_id=int(project.id)
        )
        parsed = parse_direct_reply(reply)
        if parsed.get("кадры") or parsed.get("сцена") or parsed.get("промт_картинки"):
            gpt_status = "ok"
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "montage direct-improve GPT skip #{} frame={}: {}",
            project.id,
            frame_id,
            str(exc)[:240],
        )

    scene_text = parsed.get("сцена") or op_prompt or vo[:180]
    place = ""
    chars = pick_scene_characters(
        haystack=haystack,
        gpt_raw=str(parsed.get("персонажи") or ""),
        entities=entities,
    )
    codes = format_character_codes(chars)
    labels = format_character_labels(chars)
    parent_plan = _plan(parsed.get("план_родителя") or "ОБЩИЙ", master=True)
    parent_action = ""
    new_passport: dict[str, str] = {}

    kadry = build_kadry(
        vo=vo,
        place=place,
        parent_plan=parent_plan,
        parent_action=parent_action,
        shots=list(parsed.get("кадры") or []),
        cell_number=int(parent.number),
        operator_prompt=op_prompt,
    )
    if not kadry:
        raise RuntimeError("не удалось нарезать сцену на кадры")

    chain_text = format_scene_chain(
        [
            {
                "n": i + 1,
                "place": "",
                "action": str(shot.get("действие") or ""),
                "vo": str(shot.get("закадр") or ""),
            }
            for i, shot in enumerate(kadry)
        ]
    )
    frames = await _load_frames(session, int(project.id))
    parent, _members = scene_group(frames, parent)
    apply_report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        frames,
        chain_text,
        grow=True,
        kadry=kadry,
        lock_parent_plan=False,
    )
    await session.flush()
    frames = await _load_frames(session, int(project.id))
    parent, members = scene_group(frames, parent)
    applied = apply_cell_passport(parent, frames, new_passport)
    attrs = dict(parent.attrs or {})
    if codes:
        attrs["персонажи"] = codes
        attrs["персонажи_сцены"] = codes
        attrs["characters"] = codes
    attrs["scene_summary"] = scene_text
    nodes = [
        {"node": "prompt", "status": "ok", "detail": "заказ оператора принят"},
        {
            "node": "scene",
            "status": gpt_status,
            "detail": scene_text[:120],
        },
        {
            "node": "characters",
            "status": "ok",
            "detail": labels or "реестр: никого в тексте",
        },
        {
            "node": "parent",
            "status": "ok",
            "detail": f"{parent_plan} · лицом к камере",
        },
        {
            "node": "shots",
            "status": "ok",
            "detail": f"кадров {len(kadry)} · +{apply_report.get('inserted_frames') or 0}",
        },
    ]
    report = {
        "nodes": nodes,
        "сцена": scene_text,
        "characters": chars,
        "shots": kadry,
        "passport": new_passport,
        "warnings": [],
    }
    attrs["parent_still_plan"] = parent_plan
    attrs[REPORT_ATTR] = report
    parent.attrs = attrs
    _flag_attrs(parent)
    _set_cs(parent, coverage_kind="parent", use_parent_still=False)
    for member in members:
        if int(member.id) == int(parent.id):
            continue
        extra = {"coverage_kind": "child", "use_parent_still": True}
        if codes:
            mattrs = dict(member.attrs or {})
            mattrs["персонажи"] = codes
            mattrs["characters"] = codes
            member.attrs = mattrs
            _flag_attrs(member)
        _set_cs(member, **extra)
    await session.flush()

    logger.info(
        "montage direct-improve #{} cell={} chars={} shots={} inserted={}",
        project.id,
        parent.number,
        codes,
        len(kadry),
        apply_report.get("inserted_frames"),
    )
    return {
        "ok": True,
        "mode": "improve",
        "frame_id": int(parent.id),
        "frame_number": int(parent.number),
        "replace_ns": [],
        "passport_applied": applied,
        "chain": apply_report.get("chain") or chain_text,
        "shots": apply_report.get("shots"),
        "skipped_shots": apply_report.get("skipped_shots"),
        "inserted_frames": apply_report.get("inserted_frames"),
        "report": apply_report,
        "improve_report": report,
        "image_ops": [],
        "images": 0,
    }


async def _load_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    from sqlalchemy import select

    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == int(project_id))
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
