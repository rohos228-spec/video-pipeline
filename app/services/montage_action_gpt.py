"""Генерация главное_действие одной VO-ячейки промтом action с доски монтажа."""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_apply import extract_apply_ops_json
from app.services.montage_coverage_ops import (
    SCENE_FIELD_BY_OP,
    apply_coverage_light,
    apply_coverage_scene_action,
    apply_coverage_set,
    apply_scene_field,
)
from app.services.montage_scene_editor import (
    cell_full_text,
    frame_place,
    scene_group,
)
from app.services.prompt_library import resolve_excel_gpt_prompt_path
from app.services.shot_templates import format_scene_chain, parse_scene_chain
from app.services.vo_shot_expand import bits_from_attrs, main_action_text

ACTION_PROMPT_NAME = "main_action_from_bits_ru"
ACTION_PROMPT_GROUP = "script_frames_qc"

_PASSPORT_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("place", ("place", "место")),
    ("characters", ("characters", "персонажи")),
    ("light", ("light", "lighting", "освещение")),
    ("set", ("set", "набор")),
    ("sense", ("sense", "смысл")),
    ("visual_type", ("visual_type", "тип")),
    ("props", ("props", "предметы")),
    ("bg", ("bg", "фон")),
    ("accent", ("accent", "акцент")),
    ("feature", ("feature", "особенность")),
)


def parse_replace_ns(raw: Any) -> list[int]:
    """Номера сцен ``N.`` внутри ячейки. Пусто = переписать всю цепь."""
    if raw is None or raw is False:
        return []
    items: list[Any]
    if isinstance(raw, str):
        items = [p for p in re.split(r"[,\s;]+", raw) if p]
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = [raw]
    out: list[int] = []
    seen: set[int] = set()
    for item in items:
        try:
            n = int(item)
        except (TypeError, ValueError):
            continue
        if n < 1 or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out


def merge_scene_chain(
    base: list[dict[str, Any]],
    patch: list[dict[str, Any]],
    replace_ns: list[int] | None,
) -> list[dict[str, Any]]:
    """Склеить цепь ячейки: пустой replace_ns заменяет всё, иначе только ``N.``."""
    patch_rows = [dict(row) for row in patch if isinstance(row, dict)]
    wanted = parse_replace_ns(replace_ns)
    if not wanted:
        out: list[dict[str, Any]] = []
        for i, row in enumerate(patch_rows, start=1):
            item = dict(row)
            item["n"] = i
            out.append(item)
        return out

    by_n: dict[int, dict[str, Any]] = {}
    for row in base:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            continue
        if n < 1:
            continue
        item = dict(row)
        item["n"] = n
        by_n[n] = item

    patch_by_n: dict[int, dict[str, Any]] = {}
    unmatched: list[dict[str, Any]] = []
    for row in patch_rows:
        try:
            n = int(row.get("n") or 0)
        except (TypeError, ValueError):
            n = 0
        item = dict(row)
        if n in wanted:
            item["n"] = n
            patch_by_n[n] = item
        else:
            unmatched.append(item)

    leftover = [n for n in wanted if n not in patch_by_n]
    for n, row in zip(leftover, unmatched, strict=False):
        item = dict(row)
        item["n"] = n
        patch_by_n[n] = item

    for n, new in patch_by_n.items():
        old = by_n.get(n) or {}
        if not str(new.get("vo") or "").strip() and str(old.get("vo") or "").strip():
            new["vo"] = old.get("vo") or ""
        if not str(new.get("place") or "").strip() and str(old.get("place") or "").strip():
            new["place"] = old.get("place") or ""
        new["n"] = n
        by_n[n] = new
    return [by_n[k] for k in sorted(by_n)]


def extract_main_action_from_reply(reply: str, frame_uuid: str = "") -> str:
    """Достать ``главное_действие`` из apply-ops или из цепи ``N.`` в тексте."""
    text = (reply or "").strip()
    if not text:
        raise ValueError("GPT не вернул главное_действие")
    picked = ""
    data = extract_apply_ops_json(text)
    if data:
        ops = data.get("ops") or data.get("actions") or []
        uuid = (frame_uuid or "").strip()
        fallback = ""
        for op in ops:
            if not isinstance(op, dict):
                continue
            fields = op.get("fields") if isinstance(op.get("fields"), dict) else {}
            action = str(
                fields.get("главное_действие")
                or fields.get("main_action")
                or op.get("главное_действие")
                or ""
            ).strip()
            if not action:
                continue
            op_uuid = str(op.get("frame_uuid") or op.get("uuid") or "").strip()
            if uuid and op_uuid and op_uuid != uuid:
                if not fallback:
                    fallback = action
                continue
            picked = action
            break
        if not picked:
            picked = fallback
    if not picked:
        chain = parse_scene_chain(text)
        if chain:
            picked = format_scene_chain(chain)
    if not picked:
        raise ValueError("GPT не вернул главное_действие")
    return picked


def _passport_value(passport: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        raw = passport.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def apply_cell_passport(
    frame: Frame,
    frames: list[Frame],
    passport: dict[str, Any] | None,
) -> list[str]:
    """Записать паспорт ячейки до GPT, чтобы промт видел место/свет/персонажей."""
    if not isinstance(passport, dict):
        return []
    applied: list[str] = []
    values = {
        name: _passport_value(passport, keys) for name, keys in _PASSPORT_FIELDS
    }
    place = values["place"]
    if place:
        apply_scene_field(frame, frames, SCENE_FIELD_BY_OP["coverage_place"], place)
        applied.append("place")
    characters = values["characters"]
    if characters:
        apply_scene_field(
            frame, frames, SCENE_FIELD_BY_OP["coverage_characters"], characters
        )
        applied.append("characters")
    light = values["light"]
    if light:
        apply_coverage_light(frame, light, frames)
        applied.append("light")
    scene_set = values["set"]
    if scene_set:
        apply_coverage_set(frame, scene_set, frames)
        applied.append("set")
    for key, op_type in (
        ("sense", "coverage_sense"),
        ("visual_type", "coverage_visual_type"),
        ("props", "coverage_props"),
        ("bg", "coverage_bg"),
        ("accent", "coverage_accent"),
        ("feature", "coverage_feature"),
    ):
        text = values[key]
        if not text:
            continue
        apply_scene_field(frame, frames, SCENE_FIELD_BY_OP[op_type], text)
        applied.append(key)
    return applied


def load_action_prompt() -> str:
    path = resolve_excel_gpt_prompt_path(
        ACTION_PROMPT_NAME, group_id=ACTION_PROMPT_GROUP
    )
    return path.read_text(encoding="utf-8")


def _bits_payload(parent: Frame) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in bits_from_attrs(parent):
        out.append(
            {
                "порядок": item.get("порядок"),
                "якорь": str(item.get("якорь") or "").strip(),
                "изменение": str(
                    item.get("изменение") or item.get("глагол") or ""
                ).strip(),
            }
        )
    return out


def _chain_rows(text: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in parse_scene_chain(text):
        rows.append(
            {
                "n": int(row["n"]),
                "place": str(row.get("place") or "").strip(),
                "action": str(row.get("action") or "").strip(),
                "vo": str(row.get("vo") or "").strip(),
            }
        )
    return rows


def build_action_generate_prompt(
    *,
    parent: Frame,
    members: list[Frame],
    operator_prompt: str,
    replace_ns: list[int],
    passport: dict[str, Any] | None = None,
    character_labels: str = "",
    mode: str = "",
) -> str:
    """Промт action v7 + одна ячейка монтажа (не весь проект)."""
    rules = load_action_prompt()
    full = cell_full_text(parent, members)
    place = frame_place(parent)
    chain_text = main_action_text(parent)
    chain = _chain_rows(chain_text)
    pass_map = passport if isinstance(passport, dict) else {}
    cell = {
        "frame_uuid": str(getattr(parent, "uuid", "") or ""),
        "frame_number": int(parent.number),
        "voiceover_text": full,
        "bits": _bits_payload(parent),
        "паспорт": {
            "место": _passport_value(pass_map, ("place", "место")) or place,
            "персонажи": character_labels
            or _passport_value(pass_map, ("characters", "персонажи")),
            "свет": _passport_value(pass_map, ("light", "lighting", "освещение")),
            "набор": _passport_value(pass_map, ("set", "набор")),
            "смысл": _passport_value(pass_map, ("sense", "смысл")),
            "тип": _passport_value(pass_map, ("visual_type", "тип")),
            "предметы": _passport_value(pass_map, ("props", "предметы")),
            "фон": _passport_value(pass_map, ("bg", "фон")),
            "акцент": _passport_value(pass_map, ("accent", "акцент")),
            "особенность": _passport_value(pass_map, ("feature", "особенность")),
        },
        "текущая_цепь": chain_text,
        "сцены": chain,
        "промт_оператора": (operator_prompt or "").strip(),
        "replace_ns": replace_ns,
        "mode": mode,
    }
    if replace_ns:
        task = (
            "Перепиши ТОЛЬКО сцены с номерами "
            + ", ".join(str(n) for n in replace_ns)
            + ". В главное_действие верни только эти сцены, с теми же N. "
            "Остальные не пиши."
        )
    elif mode == "improve":
        task = (
            "Разверни главное_действие этой ячейки в подробную цепь смысла.\n"
            "Каждое N. — отдельный видимый кадр, не слоган на всю ячейку.\n"
            "Цепь развивает сюжет: вступление в место (что видно целиком) → "
            "действие рук/тела → перебивка (деталь предмета, взгляд, улика) → "
            "реакция или следствие.\n"
            "Не выдумывай другое место, если паспорт и закадр его не дали.\n"
            "Не пиши камеру, крупность и коды шаблонов.\n"
            "Если закадр длиннее 40 знаков — минимум три кадра N.\n"
            "Весь закадр в скобках без дыр."
        )
    else:
        task = "Напиши главное_действие заново только для этой ячейки."
    extra = (operator_prompt or "").strip()
    if extra:
        task = f"{task}\nПромт оператора: {extra}"
    payload = json.dumps(cell, ensure_ascii=False, indent=2)
    return (
        f"{rules.rstrip()}\n\n"
        "## задача оператора\n"
        f"{task}\n\n"
        "Вход — одна VO-ячейка:\n"
        f"{payload}\n"
    )


async def _map_character_labels(
    session: AsyncSession, project_id: int, raw: str
) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        from app.models import Entity

        ents = list(
            (
                await session.execute(
                    select(Entity).where(Entity.project_id == project_id)
                )
            )
            .scalars()
            .all()
        )
    except Exception:  # noqa: BLE001
        return text
    names = {
        str(getattr(e, "code", "") or "").strip(): str(getattr(e, "name", "") or "").strip()
        for e in ents
        if str(getattr(e, "type", "") or "") == "character"
        and str(getattr(e, "code", "") or "").strip()
    }
    if not names:
        return text
    parts = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()]
    mapped: list[str] = []
    for part in parts:
        name = names.get(part) or names.get(part.lower())
        mapped.append(f"{part} · {name}" if name and name != part else part)
    return ", ".join(mapped)


async def generate_cell_scene_action(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    replace_ns: list[int] | None = None,
    passport: dict[str, Any] | None = None,
    timeout: float = 180.0,
    mode: str = "",
) -> dict[str, Any]:
    """Паспорт → GPT action на одну ячейку → merge кусков → apply_coverage_scene_action."""
    from app.services.gpt_client import gpt_ask_fresh

    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == int(project.id))
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    parent, members = scene_group(frames, frame)
    wanted = parse_replace_ns(replace_ns)
    base_chain = _chain_rows(main_action_text(parent))
    if wanted:
        known = {int(row["n"]) for row in base_chain}
        missing = [n for n in wanted if n not in known]
        if missing:
            raise RuntimeError(
                "в ячейке нет сцен "
                + ", ".join(str(n) for n in missing)
                + " — сначала сгенерируйте цепь"
            )

    applied = apply_cell_passport(parent, frames, passport)
    await session.flush()

    labels = await _map_character_labels(
        session,
        int(project.id),
        _passport_value(passport or {}, ("characters", "персонажи"))
        or str((getattr(parent, "attrs", None) or {}).get("персонажи_сцены") or "")
        or str((getattr(parent, "attrs", None) or {}).get("персонажи") or ""),
    )
    prompt = build_action_generate_prompt(
        parent=parent,
        members=members,
        operator_prompt=operator_prompt,
        replace_ns=wanted,
        passport=passport,
        character_labels=labels,
        mode=mode,
    )
    reply = await gpt_ask_fresh(prompt, timeout=timeout, project_id=int(project.id))
    extracted = extract_main_action_from_reply(
        reply, str(getattr(parent, "uuid", "") or "")
    )
    patch_chain = _chain_rows(extracted)
    if not patch_chain:
        raise ValueError("GPT не вернул сцены в форме N. место — шаг")
    merged = merge_scene_chain(base_chain, patch_chain, wanted)
    chain_text = format_scene_chain(merged)
    if not chain_text.strip():
        raise RuntimeError("после склейки цепь пустая")
    report = await apply_coverage_scene_action(
        session,
        project,
        parent,
        frames,
        chain_text,
        grow=(mode == "improve"),
    )
    await session.flush()
    logger.info(
        "montage action-generate #{} cell={} replace_ns={} shots={} passport={}",
        project.id,
        parent.number,
        wanted,
        report.get("shots"),
        applied,
    )
    return {
        "ok": True,
        "frame_id": int(parent.id),
        "frame_number": int(parent.number),
        "replace_ns": wanted,
        "passport_applied": applied,
        "chain": report.get("chain") or chain_text,
        "shots": report.get("shots"),
        "skipped_shots": report.get("skipped_shots"),
        "inserted_frames": report.get("inserted_frames"),
        "report": report,
    }


def scene_image_instruction(
    *,
    beat: str = "",
    passport: dict[str, Any] | None = None,
    shot: dict[str, Any] | None = None,
) -> str:
    """Заметка оператора для ИИзменения: паспорт ячейки + действие и покрытие кадра."""
    p = passport or {}

    def _get(*keys: str) -> str:
        for key in keys:
            val = str(p.get(key) or "").strip()
            if val:
                return val
        return ""

    parts: list[str] = []
    place = _get("place", "место", "set", "набор")
    if place:
        parts.append(f"Место только: {place}. Не выдумывай другое место.")
    light = _get("light", "lighting", "освещение")
    if light:
        parts.append(f"Свет: {light}.")
    chars = _get("characters", "персонажи")
    if chars:
        parts.append(f"Персонажи: {chars}.")
    bg = _get("bg", "фон")
    if bg:
        parts.append(f"Фон: {bg}.")
    sense = _get("sense", "смысл")
    if sense:
        parts.append(f"Смысл: {sense}.")
    act = " ".join((beat or "").split())
    if act:
        parts.append(f"Действие этого кадра: {act}.")
    cam = shot or {}
    camera = ", ".join(
        f"{label} {str(cam.get(key) or '').strip()}"
        for key, label in (("план", "план"), ("ракурс", "ракурс"), ("движение", "движение"))
        if str(cam.get(key) or "").strip()
    )
    if camera:
        parts.append(f"Камера: {camera}.")
    accent = _get("accent", "акцент")
    if accent and str(cam.get("роль") or "") in {"реакция", "перебивка"}:
        parts.append(f"Акцент: {accent}.")
    parts.append("Один кадр, не коллаж.")
    return " ".join(parts)


def build_scene_image_ops(
    members: list[Any],
    *,
    passport: dict[str, Any] | None = None,
    chain: str = "",
    frame_ids: list[int] | None = None,
    kadry: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """ИИзменение на видимые кадры ячейки. leftover и лишние id пропускаем."""
    from app.services.montage_board import _action_for_frame
    from app.services.montage_board_frames import _visible_and_extras
    from app.services.shot_templates import split_scene_action_beats

    visible, _extras = _visible_and_extras(list(members), frame_ids)
    if not visible:
        return []
    beats = split_scene_action_beats(chain)
    ops: list[dict[str, Any]] = []
    for i, fr in enumerate(visible):
        beat = ""
        if i < len(beats):
            beat = beats[i]
        elif beats:
            beat = beats[-1]
        if not beat:
            beat = _action_for_frame(fr)
        shot = kadry[i] if kadry and i < len(kadry) else None
        ops.append(
            {
                "type": "image_ai_change",
                "frame_number": int(fr.number),
                "shot": 1,
                "instruction": scene_image_instruction(
                    beat=beat, passport=passport, shot=shot
                ),
            }
        )
    return ops


async def generate_cell_scene_with_images(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    *,
    operator_prompt: str = "",
    passport: dict[str, Any] | None = None,
    frame_ids: list[int] | None = None,
    timeout: float = 180.0,
    mode: str = "",
) -> dict[str, Any]:
    """GPT-сцены ячейки (мягкий fallback на текст цепи) + ops ИИзменения.

    ``mode="improve"`` — ячейка точечно через 6 нод группы script_frames_qc
    (``montage_scene_improve``).
    """
    if mode == "improve":
        from app.services.montage_scene_improve import improve_cell_scene

        return await improve_cell_scene(
            session,
            project,
            frame_id,
            operator_prompt=operator_prompt,
            passport=passport,
            timeout=timeout,
        )
    gen_error = ""
    result: dict[str, Any] | None = None
    grow = mode == "improve"
    try:
        result = await generate_cell_scene_action(
            session,
            project,
            frame_id,
            operator_prompt=operator_prompt,
            replace_ns=[],
            passport=passport,
            timeout=timeout,
            mode=mode,
        )
    except (RuntimeError, ValueError) as exc:
        gen_error = str(exc)
        logger.warning(
            "scene-generate-with-images GPT skip #{} frame={}: {}",
            project.id,
            frame_id,
            gen_error[:240],
        )
        frames = list(
            (
                await session.execute(
                    select(Frame)
                    .where(Frame.project_id == int(project.id))
                    .order_by(Frame.sort_key, Frame.number)
                )
            )
            .scalars()
            .all()
        )
        frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
        if frame is None:
            raise RuntimeError(f"кадр {frame_id} не найден") from exc
        parent, _members = scene_group(frames, frame)
        apply_cell_passport(parent, frames, passport)
        await session.flush()
        chain_src = (operator_prompt or "").strip() or main_action_text(parent)
        if not chain_src:
            raise RuntimeError(gen_error or "последовательность кадров пустая") from exc
        report = await apply_coverage_scene_action(
            session, project, parent, frames, chain_src, grow=grow
        )
        await session.flush()
        result = {
            "ok": True,
            "frame_id": int(parent.id),
            "frame_number": int(parent.number),
            "replace_ns": [],
            "fallback": True,
            "generate_error": gen_error,
            "chain": report.get("chain") or chain_src,
            "shots": report.get("shots"),
            "skipped_shots": report.get("skipped_shots"),
            "inserted_frames": report.get("inserted_frames"),
            "report": report,
        }
    assert result is not None
    frames = list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == int(project.id))
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        frame = next(
            (fr for fr in frames if int(fr.number) == int(result.get("frame_number") or 0)),
            None,
        )
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден после generate")
    parent, members = scene_group(frames, frame)
    chain = str(result.get("chain") or main_action_text(parent) or operator_prompt or "")
    ops = build_scene_image_ops(
        members,
        passport=passport,
        chain=chain,
        frame_ids=None if grow else frame_ids,
    )
    result["image_ops"] = ops
    result["images"] = len(ops)
    result["mode"] = mode
    if gen_error and "generate_error" not in result:
        result["generate_error"] = gen_error
    return result
