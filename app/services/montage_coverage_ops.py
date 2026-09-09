"""Правки покрытия (Кадр / План / Действие) с панели монтажа."""

from __future__ import annotations

from typing import Any

from loguru import logger
from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Artifact, Frame, FrameEdge, FrameText, Project, PromptVersion
from app.services.montage_board_meta import slot_key_from_op
from app.services.vo_shot_expand import (
    _cs,
    _flag_attrs,
    _set_cs,
    coverage_shot_id,
    find_coverage_parent_frame,
    is_shot_child,
    main_action_text,
    planned_shots_from_attrs,
)

COVERAGE_OP_TYPES = frozenset(
    {
        "coverage_plan",
        "coverage_action",
        "coverage_kind",
        "coverage_delete",
        "coverage_template",
        "coverage_anchors",
    }
)

COVERAGE_PLAN_CHOICES = (
    "ОБЩИЙ",
    "ДАЛЬНИЙ",
    "СРЕДНИЙ",
    "КРУПНЫЙ",
    "ДЕТАЛЬ",
)


def _by_number(frames: list[Frame], number: int) -> Frame | None:
    for fr in frames:
        if int(fr.number) == int(number):
            return fr
    return None


def _patch_kadry_item(frame: Frame, **fields: Any) -> None:
    planned = planned_shots_from_attrs(frame)
    if not planned:
        item: dict[str, Any] = {"id": coverage_shot_id(frame) or f"{frame.number}-K1"}
        planned = [item]
    else:
        item = planned[0]
        sid = coverage_shot_id(frame)
        if sid:
            for cand in planned:
                if str(cand.get("id") or "").strip() == sid:
                    item = cand
                    break
    for key, val in fields.items():
        if val is None:
            continue
        item[key] = val
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["кадры"] = planned
    frame.attrs = attrs
    _flag_attrs(frame)


def _remove_kadry_id(frame: Frame, shot_id: str) -> None:
    sid = (shot_id or "").strip()
    if not sid:
        return
    planned = [
        item
        for item in planned_shots_from_attrs(frame)
        if str(item.get("id") or "").strip() != sid
    ]
    attrs = dict(getattr(frame, "attrs", None) or {})
    if planned:
        attrs["кадры"] = planned
    else:
        attrs.pop("кадры", None)
        attrs.pop("shots", None)
    frame.attrs = attrs
    _flag_attrs(frame)


def _children_of(frames: list[Frame], parent: Frame) -> list[Frame]:
    out: list[Frame] = []
    for fr in frames:
        if int(fr.number) == int(parent.number):
            continue
        found = find_coverage_parent_frame(frames, fr)
        if found is not None and int(found.number) == int(parent.number):
            out.append(fr)
    return out


def _would_cycle(frames: list[Frame], child: Frame, new_parent: Frame) -> bool:
    cur: Frame | None = new_parent
    seen: set[int] = set()
    while cur is not None:
        cid = int(cur.id or 0) or int(cur.number)
        if int(cur.number) == int(child.number):
            return True
        if cid in seen:
            break
        seen.add(cid)
        nxt = find_coverage_parent_frame(frames, cur)
        if nxt is None or int(nxt.number) == int(cur.number):
            break
        cur = nxt
    return False


def _refresh_shots_in_beat(frames: list[Frame], parent: Frame) -> None:
    kids = _children_of(frames, parent)
    total = 1 + len(kids)
    _set_cs(parent, shots_in_beat=total, role="vo_parent", parent_uuid=parent.uuid)
    for i, kid in enumerate(sorted(kids, key=lambda f: (f.sort_key or 0.0, f.number))):
        _set_cs(kid, shots_in_beat=total, shot_index=i + 2)


async def _load_frames(session: AsyncSession, project_id: int) -> list[Frame]:
    return list(
        (
            await session.execute(
                select(Frame)
                .where(Frame.project_id == project_id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )


def apply_coverage_plan(frame: Frame, plan: str, frames: list[Frame]) -> None:
    text = (plan or "").strip()
    if not text:
        raise RuntimeError("план пустой")
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["крупность"] = text
    frame.attrs = attrs
    _flag_attrs(frame)
    _set_cs(frame, план=text, крупность=text)
    _patch_kadry_item(frame, план=text)
    parent = find_coverage_parent_frame(frames, frame)
    if parent is not None and int(parent.number) != int(frame.number):
        _patch_kadry_item_on_parent_ladder(parent, frame, план=text)


def apply_coverage_action(frame: Frame, action: str, frames: list[Frame]) -> None:
    text = (action or "").strip()
    if not text:
        raise RuntimeError("действие пустое")
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["shot01_action"] = text
    attrs["действие"] = text
    frame.attrs = attrs
    _flag_attrs(frame)
    _patch_kadry_item(frame, действие=text)
    parent = find_coverage_parent_frame(frames, frame)
    if parent is not None and int(parent.number) != int(frame.number):
        _patch_kadry_item_on_parent_ladder(parent, frame, действие=text)


def apply_coverage_template(
    frame: Frame,
    frames: list[Frame],
    template: str,
) -> dict[str, Any]:
    """Формат сцены (шаблон T*/X*) на всю группу ячейки + план по лестнице.

    Своё написанное «действие» не затираем — только заглушки каталога
    переписываем под новую роль кадра.
    """
    from app.services.montage_scene_editor import frame_place, scene_group
    from app.services.shot_templates import (
        catalog_shot_rows,
        compose_shot_action,
        is_stub_shot_action,
        normalize_template_id,
        parse_scene_chain,
        template_exists,
    )

    tid = normalize_template_id(template)
    if not tid:
        raise RuntimeError("формат сцены пустой")
    if not template_exists(tid):
        raise RuntimeError(f"формата сцены {tid} нет в каталоге")
    parent, members = scene_group(frames, frame)
    rows = catalog_shot_rows(tid)
    chain = parse_scene_chain(main_action_text(parent))
    scene_action = str(chain[0].get("action") or "") if chain else ""
    place = frame_place(parent)
    rewritten = 0
    for i, member in enumerate(members):
        row = rows[i] if i < len(rows) else None
        extra: dict[str, Any] = {"шаблон": tid}
        patch: dict[str, Any] = {"шаблон": tid}
        if row is not None:
            plan = str(row.get("plan") or "").split("/")[0].strip()
            angle = str(row.get("angle") or "").split("/")[0].strip()
            if plan:
                extra["план"] = plan
                extra["крупность"] = plan
                patch["план"] = plan
                attrs = dict(getattr(member, "attrs", None) or {})
                attrs["крупность"] = plan
                member.attrs = attrs
            if angle and angle != "—":
                extra["ракурс"] = angle
                patch["ракурс"] = angle
            current = str(
                (getattr(member, "attrs", None) or {}).get("shot01_action") or ""
            )
            if is_stub_shot_action(current):
                text = compose_shot_action(
                    plan=plan,
                    place=frame_place(member) or place,
                    scene_action=scene_action,
                    catalog_action=str(row.get("action") or ""),
                    catalog_role=str(row.get("role") or ""),
                )
                apply_coverage_action(member, text, frames)
                rewritten += 1
        _set_cs(member, **extra)
        _patch_kadry_item(member, **patch)
        if member is not parent:
            _patch_kadry_item_on_parent_ladder(parent, member, **patch)
    _flag_attrs(parent)
    return {
        "шаблон": tid,
        "ladder": len(rows),
        "frames": len(members),
        "rewritten_actions": rewritten,
        "missing_frames": max(0, len(rows) - len(members)),
        "extra_frames": max(0, len(members) - len(rows)) if rows else 0,
    }


def apply_coverage_anchors(
    frame: Frame,
    frames: list[Frame],
    anchors: list[Any],
) -> dict[str, Any]:
    """Якоря закадра → биты[] родителя + пересборка кусков VO по кадрам.

    Кадры не вставляем и не удаляем: якорей больше, чем кадров — лишние
    остаются в биты[] для следующего прогона нод (в отчёте ``missing_frames``).
    """
    from app.services.montage_scene_editor import (
        cell_full_text,
        normalize_anchor_rows,
        scene_group,
        split_vo_by_anchors,
    )

    rows = normalize_anchor_rows(anchors)
    if not rows:
        raise RuntimeError("нет ни одного якоря")
    parent, members = scene_group(frames, frame)
    full = cell_full_text(parent, members)
    if not full:
        raise RuntimeError("у ячейки нет закадрового текста")
    parts = split_vo_by_anchors(full, [row["якорь"] for row in rows])
    if not parts:
        raise RuntimeError("якоря не нашлись в тексте ячейки")

    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["биты"] = rows
    attrs["vo_cell_full"] = full
    parent.attrs = attrs
    _flag_attrs(parent)

    used = min(len(parts), len(members))
    # Хвост текста не теряем: последний кадр группы забирает остаток.
    assigned = list(parts[: used - 1]) + [" ".join(parts[used - 1 :])] if used else []
    for i, member in enumerate(members[:used]):
        piece = " ".join((assigned[i] or "").split())
        if not piece:
            continue
        member.voiceover_text = piece
        _set_cs(member, vo_shot=piece)
        _patch_kadry_item(member, закадр=piece)
        if member is not parent:
            _patch_kadry_item_on_parent_ladder(parent, member, закадр=piece)
    return {
        "anchors": len(rows),
        "parts": len(parts),
        "frames": len(members),
        "assigned": used,
        "missing_frames": max(0, len(parts) - len(members)),
    }


def _patch_kadry_item_on_parent_ladder(
    parent: Frame, child: Frame, **fields: Any
) -> None:
    planned = planned_shots_from_attrs(parent)
    if not planned:
        return
    sid = coverage_shot_id(child)
    item = None
    if sid:
        for cand in planned:
            if str(cand.get("id") or "").strip() == sid:
                item = cand
                break
    if item is None and len(planned) > 1:
        idx = int((_cs_shot_index(child) or 1) - 1)
        if 0 <= idx < len(planned):
            item = planned[idx]
    if item is None:
        return
    for key, val in fields.items():
        item[key] = val
    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["кадры"] = planned
    parent.attrs = attrs
    _flag_attrs(parent)


def _cs_shot_index(frame: Frame) -> int | None:
    raw = _cs(frame).get("shot_index")
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def apply_coverage_kind(
    frame: Frame,
    frames: list[Frame],
    *,
    kind: str,
    parent_number: int | None,
) -> None:
    role = (kind or "").strip().lower()
    if role not in ("parent", "child"):
        raise RuntimeError("нужно выбрать родитель или дочерний")
    if role == "parent":
        old_parent = find_coverage_parent_frame(frames, frame)
        _set_cs(
            frame,
            role="vo_parent",
            parent_uuid=frame.uuid,
            coverage_parent_id="",
        )
        _patch_kadry_item(frame, parent_id=None)
        if old_parent is not None and int(old_parent.number) != int(frame.number):
            sid = coverage_shot_id(frame)
            _remove_kadry_id(old_parent, sid)
            _refresh_shots_in_beat(frames, old_parent)
        _refresh_shots_in_beat(frames, frame)
        return

    if parent_number is None:
        raise RuntimeError("для дочернего кадра выберите родителя")
    parent = _by_number(frames, int(parent_number))
    if parent is None:
        raise RuntimeError(f"родитель #{parent_number} не найден")
    if int(parent.number) == int(frame.number):
        raise RuntimeError("нельзя сделать кадр дочерним самому себе")
    if _would_cycle(frames, frame, parent):
        raise RuntimeError("нельзя привязать кадр к своему потомку")

    old_parent = find_coverage_parent_frame(frames, frame)
    former_kids = _children_of(frames, frame)
    parent_sid = coverage_shot_id(parent) or f"{parent.number}-K1"
    _set_cs(
        frame,
        role="shot",
        parent_uuid=parent.uuid,
        coverage_parent_id=parent_sid,
    )
    _patch_kadry_item(frame, parent_id=parent_sid)
    if old_parent is not None and int(old_parent.number) != int(parent.number):
        _remove_kadry_id(old_parent, coverage_shot_id(frame))
        _refresh_shots_in_beat(frames, old_parent)
    for kid in former_kids:
        _set_cs(
            kid,
            role="shot",
            parent_uuid=parent.uuid,
            coverage_parent_id=parent_sid,
        )
        _patch_kadry_item(kid, parent_id=parent_sid)
    _refresh_shots_in_beat(frames, parent)


async def delete_coverage_child(
    session: AsyncSession,
    project: Project,
    frame: Frame,
    frames: list[Frame],
) -> None:
    parent = find_coverage_parent_frame(frames, frame)
    is_child = parent is not None and int(parent.number) != int(frame.number)
    if not (is_shot_child(frame) or is_child):
        raise RuntimeError("удалять можно только дочерний кадр")
    drop_id = int(frame.id)
    sid = coverage_shot_id(frame)
    await session.execute(
        update(Artifact).where(Artifact.frame_id == drop_id).values(frame_id=None)
    )
    await session.execute(delete(PromptVersion).where(PromptVersion.frame_id == drop_id))
    await session.execute(delete(FrameText).where(FrameText.frame_id == drop_id))
    await session.execute(
        delete(FrameEdge).where(
            FrameEdge.project_id == project.id,
            or_(
                FrameEdge.from_frame_id == drop_id,
                FrameEdge.to_frame_id == drop_id,
            ),
        )
    )
    await session.delete(frame)
    await session.flush()
    if parent is not None:
        remaining = [fr for fr in frames if int(fr.id) != drop_id]
        _remove_kadry_id(parent, sid)
        _refresh_shots_in_beat(remaining, parent)


async def apply_coverage_op(
    session: AsyncSession,
    project: Project,
    op: dict[str, Any],
) -> dict[str, Any]:
    """Записать план/действие/родство. Highlight ключ слота покрытия."""
    op_type = str(op.get("type") or "").strip()
    if op_type not in COVERAGE_OP_TYPES:
        raise RuntimeError(f"неизвестная coverage-операция: {op_type}")
    frame_number = int(op["frame_number"])
    frames = await _load_frames(session, int(project.id))
    frame = _by_number(frames, frame_number)
    if frame is None:
        raise RuntimeError(f"кадр {frame_number} не найден")

    report: dict[str, Any] | None = None
    if op_type == "coverage_plan":
        apply_coverage_plan(frame, str(op.get("plan") or ""), frames)
    elif op_type == "coverage_action":
        apply_coverage_action(frame, str(op.get("action") or ""), frames)
    elif op_type == "coverage_template":
        report = apply_coverage_template(frame, frames, str(op.get("template") or ""))
    elif op_type == "coverage_anchors":
        report = apply_coverage_anchors(frame, frames, list(op.get("anchors") or []))
    elif op_type == "coverage_kind":
        parent_raw = op.get("parent_number")
        parent_number = int(parent_raw) if parent_raw not in (None, "") else None
        apply_coverage_kind(
            frame,
            frames,
            kind=str(op.get("kind") or ""),
            parent_number=parent_number,
        )
    elif op_type == "coverage_delete":
        await delete_coverage_child(session, project, frame, frames)

    await session.flush()
    highlight = slot_key_from_op(op)
    logger.info(
        "montage coverage #{} {} frame {} → {}",
        project.id,
        op_type,
        frame_number,
        highlight,
    )
    out = {
        "ok": True,
        "highlight": highlight,
        "frame_number": frame_number,
        "op": op,
        # Якоря меняют только нарезку закадра — картинку не трогаем.
        "regen_image": op_type not in ("coverage_delete", "coverage_anchors"),
    }
    if report is not None:
        out["report"] = report
    return out
