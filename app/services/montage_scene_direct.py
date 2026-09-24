"""«Улучшить сцену» — прямой запуск, без группы нод script_frames_qc.

Промт оператора → формулировка сцены → персонажи из реестра по закадру →
родитель (ОБЩИЙ/СРЕДНИЙ, лицом к камере, промт картинки) → нарезка кадров
с закадром 13–90 (цифра = 4).
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
from app.services.montage_scene_editor import cell_full_text, frame_place, scene_group
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
) -> str:
    order = (operator_prompt or "").strip() or "сформулируй сцену по закадру"
    return (
        "Сначала обработай заказ оператора — он главный.\n"
        f"Заказ: {order}\n\n"
        "Сформулируй сцену: о ком речь, где, что происходит.\n"
        "Персонажей бери только из реестра и только если они названы в закадре "
        "или в заказе. Не выдумывай новых.\n"
        "Потом родительский кадр: ОБЩИЙ или СРЕДНИЙ, все выбранные персонажи "
        "лицом к камере. Напиши промт картинки родителя.\n"
        "Потом кадры сцены по порядку: 1) место и кто в кадре, 2+) действие, "
        "и каждый кадр — новое действие, без копий одной фразы.\n"
        "Закадр не переписывай — его нарежет код.\n\n"
        f"Место: {place or '—'}\n"
        f"Закадр сцены:\n{vo}\n\n"
        f"Реестр персонажей проекта: {registry_labels or 'пусто'}\n"
        f"Уже совпали с текстом: {matched_labels or 'никто'}\n\n"
        "Ответ — JSON:\n"
        '{"сцена":"…","место":"…","персонажи":"c01, c02",'
        '"план_родителя":"ОБЩИЙ","промт_картинки":"…",'
        '"кадры":[{"действие":"…","план":"ОБЩИЙ"}]}\n'
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


def unique_gpt_beats(
    shots: list[dict[str, Any]],
    *,
    parent_action: str,
) -> list[tuple[str, str]]:
    """Уникальные действия GPT, без повтора и без клона родителя."""
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for shot in shots:
        act = _shot_action(shot)
        if not act:
            continue
        key = act.casefold()
        if key in seen:
            continue
        if actions_too_similar(act, parent_action):
            continue
        seen.add(key)
        out.append((act, _plan(shot.get("план") or "СРЕДНИЙ")))
    return out


def build_kadry(
    *,
    vo: str,
    place: str,
    parent_plan: str,
    parent_action: str,
    shots: list[dict[str, Any]],
    cell_number: int,
) -> list[dict[str, Any]]:
    pieces = split_scene_vo(vo)
    if not pieces:
        return []
    unused = unique_gpt_beats(shots, parent_action=parent_action)
    used: set[str] = set()
    master = f"{int(cell_number)}-K1"
    out: list[dict[str, Any]] = []
    for i, piece in enumerate(pieces):
        if i == 0:
            plan = parent_plan
            act = parent_action or "видно место, персонажи лицом к камере"
        else:
            act = ""
            plan = "СРЕДНИЙ"
            while unused:
                cand, cand_plan = unused.pop(0)
                if cand.casefold() in used or actions_too_similar(cand, parent_action):
                    continue
                act = cand
                plan = cand_plan
                break
            if not act or act.casefold() in used:
                act = vo_beat(piece)
            if act.casefold() in used:
                act = f"{vo_beat(piece)} · кадр {i + 1}"
        used.add(act.casefold())
        out.append(
            {
                "id": f"{int(cell_number)}-K{i + 1}",
                "parent_id": None if i == 0 else master,
                "порядок": i + 1,
                "сцена": 1,
                "план": plan,
                "ракурс": "фронт",
                "место": place,
                "действие": act,
                "закадр": piece,
            }
        )
    return out


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
    op_passport = dict(passport or {})
    op_prompt = _norm(operator_prompt)
    place = _norm(op_passport.get("place") or frame_place(parent) or "")

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
    prompt = build_direct_improve_prompt(
        operator_prompt=op_prompt,
        vo=vo,
        place=place,
        registry_labels=registry_labels,
        matched_labels=format_character_labels(matched),
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
    place = parsed.get("место") or place
    chars = pick_scene_characters(
        haystack=haystack,
        gpt_raw=str(parsed.get("персонажи") or ""),
        entities=entities,
    )
    codes = format_character_codes(chars)
    labels = format_character_labels(chars)
    parent_plan = _plan(parsed.get("план_родителя") or "ОБЩИЙ", master=True)
    parent_action = (
        f"место {place}, {labels or 'персонажи сцены'} лицом к камере"
        if labels or place
        else "видно место, персонажи лицом к камере"
    )
    img_prompt = parsed.get("промт_картинки") or default_parent_prompt(
        place=place,
        who=labels,
        scene=scene_text,
        plan=parent_plan,
    )
    if "лицом к камере" not in img_prompt.casefold():
        img_prompt = f"{img_prompt} Все персонажи лицом к камере."

    kadry = build_kadry(
        vo=vo,
        place=place,
        parent_plan=parent_plan,
        parent_action=parent_action,
        shots=list(parsed.get("кадры") or []),
        cell_number=int(parent.number),
    )
    if not kadry:
        raise RuntimeError("не удалось нарезать сцену на кадры")

    new_passport = dict(op_passport)
    if place:
        new_passport["place"] = place
    if codes:
        new_passport["characters"] = codes
    applied = apply_cell_passport(parent, frames, new_passport)
    chain_text = format_scene_chain(
        [
            {
                "n": i + 1,
                "place": str(shot.get("место") or place),
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
    )
    await session.flush()
    frames = await _load_frames(session, int(project.id))
    parent, members = scene_group(frames, parent)
    parent.image_prompt = img_prompt
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
    attrs[REPORT_ATTR] = report
    parent.attrs = attrs
    _flag_attrs(parent)
    _set_cs(parent, план=parent_plan, coverage_kind="parent", use_parent_still=False)
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
