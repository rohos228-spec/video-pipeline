"""Вставка / удаление кадров и правка закадра с панели монтажа."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, FrameStatus, FrameText, Project
from app.services.db_v2 import insert_frame_after
from app.services.frame_tombstones import remember_deleted
from app.services.montage_coverage_ops import (
    _children_of,
    _clear_child_scene_chain,
    _load_frames,
    _refresh_shots_in_beat,
    delete_coverage_child,
)
from app.services.montage_board_meta import (
    drop_pending_ops_for_frames,
    montage_meta,
    set_montage_meta,
)
from app.services.vo_shot_expand import _cs, _flag_attrs, _set_cs, is_shot_child


async def upsert_frame_voiceover(session: AsyncSession, frame: Frame, text: str) -> None:
    """Frame.voiceover_text + FrameText(kind=voiceover)."""
    vo = (text or "").strip()
    frame.voiceover_text = vo
    existing = (
        await session.execute(
            select(FrameText).where(
                FrameText.frame_id == frame.id,
                FrameText.kind == "voiceover",
            )
        )
    ).scalars().first()
    if not vo:
        if existing is not None:
            existing.text = ""
        return
    if existing is None:
        session.add(
            FrameText(
                project_id=int(frame.project_id),
                frame_id=int(frame.id),
                kind="voiceover",
                text=vo,
            )
        )
        return
    existing.text = vo


async def set_montage_voiceover(
    session: AsyncSession,
    project: Project,
    frame_id: int,
    text: str,
) -> Frame:
    frame = await session.get(Frame, frame_id)
    if frame is None or int(frame.project_id) != int(project.id):
        raise RuntimeError(f"кадр {frame_id} не найден")
    await upsert_frame_voiceover(session, frame, text)
    await session.flush()
    logger.info(
        "montage voiceover #{} frame {} → {} симв.",
        project.id,
        frame.number,
        len(frame.voiceover_text or ""),
    )
    return frame


async def insert_montage_frame(
    session: AsyncSession,
    project: Project,
    *,
    after_frame_id: int | None,
    voiceover: str = "",
    kind: str = "parent",
) -> Frame:
    """Вставить кадр.

    ``parent`` — новая VO-ячейка после кадра / в начало.
    ``child`` — шот в ту же сцену, сразу после указанного кадра.
    """
    from app.services.montage_board import _vo_parent_and_members
    from app.services.montage_coverage_ops import _new_shot_from_parent, _refresh_shots_in_beat

    kind_n = (kind or "parent").strip().lower()
    if kind_n in {"child", "shot"}:
        if after_frame_id is None:
            raise ValueError("для шота в сцене нужен кадр, после которого вставлять")
        frames = await _load_frames(session, int(project.id))
        after = next((fr for fr in frames if int(fr.id) == int(after_frame_id)), None)
        if after is None:
            raise ValueError(f"кадр {after_frame_id} не найден")
        parent, _members = _vo_parent_and_members(frames, after)
        fr = await insert_frame_after(
            session,
            project,
            after_frame_id=int(after.id),
        )
        fr.status = FrameStatus.planned
        fr.voiceover_text = ""
        _new_shot_from_parent(parent, fr)
        await session.flush()
        await upsert_frame_voiceover(session, fr, "")
        ordered = await _load_frames(session, int(project.id))
        live_parent = next(
            (x for x in ordered if int(x.id) == int(parent.id)), parent
        )
        _refresh_shots_in_beat(ordered, live_parent)
        await session.flush()
        live = await session.get(Frame, fr.id)
        out = live if live is not None else fr
        logger.info(
            "montage insert child #{} after={} → frame {} parent={}",
            project.id,
            after_frame_id,
            out.number,
            live_parent.number,
        )
        return out

    fr = await insert_frame_after(
        session,
        project,
        after_frame_id=after_frame_id,
    )
    uid = str(fr.uuid or "").strip()
    vo = (voiceover or "").strip()
    fr.status = FrameStatus.planned
    fr.voiceover_text = vo
    _set_cs(fr, role="vo_parent", parent_uuid=uid)
    await session.flush()
    await upsert_frame_voiceover(session, fr, vo)
    await session.flush()
    live = await session.get(Frame, fr.id)
    out = live if live is not None else fr
    logger.info(
        "montage insert #{} after={} → frame {} uuid={}",
        project.id,
        after_frame_id,
        out.number,
        out.uuid,
    )
    return out


async def _drop_frame_rows(
    session: AsyncSession,
    project: Project,
    frames_to_drop: list[Frame],
) -> None:
    """Пометить кадры. Строки, тексты, промты и связи не стираются."""
    pending = [fr for fr in frames_to_drop if getattr(fr, "id", None)]
    if not pending:
        return
    for fr in pending:
        attrs = dict(getattr(fr, "attrs", None) or {})
        attrs["активен"] = False
        attrs["удалён"] = True
        attrs["карантин"] = True
        attrs["пометка"] = "_drop_frame_rows: кадр скрыт, не удалён"
        fr.attrs = attrs
        _flag_attrs(fr)
    await session.flush()
    logger.warning(
        "WARN _drop_frame_rows: кадры {} скрыты, строки сохранены",
        [int(fr.number) for fr in pending],
    )


def _merge_ladders_by_id(parent_rows: Any, child_rows: Any) -> list[dict[str, Any]]:
    """Лестница головы плюс шоты ребёнка. Совпадающий id не дублируется."""
    out: list[dict[str, Any]] = []
    index: dict[str, int] = {}

    def _take(raw: Any, *, child: bool) -> None:
        if not isinstance(raw, list):
            return
        for row in raw:
            if not isinstance(row, dict):
                continue
            sid = str(row.get("id") or "").strip()
            item = dict(row)
            if sid and sid in index:
                if child:
                    merged = dict(out[index[sid]])
                    merged.update(item)
                    out[index[sid]] = merged
                continue
            index[sid or f"#{len(out)}"] = len(out)
            out.append(item)

    _take(parent_rows, child=False)
    _take(child_rows, child=True)
    return out


def _promote_first_child(parent: Frame, kids: list[Frame]) -> Frame | None:
    """Голова сцены удалена — первый шот становится VO-родителем, остальные остаются."""
    if not kids:
        return None
    ordered = sorted(kids, key=lambda fr: (float(fr.sort_key or 0.0), int(fr.number or 0)))
    head = ordered[0]
    src = dict(getattr(parent, "attrs", None) or {})
    dst = dict(getattr(head, "attrs", None) or {})
    import copy

    for key in ("vo_cell_full", "главное_действие", "main_action", "биты"):
        if src.get(key) and not dst.get(key):
            val = src[key]
            dst[key] = copy.deepcopy(val) if isinstance(val, (list, dict)) else val
    for key in ("кадры", "shots"):
        if src.get(key) or dst.get(key):
            dst[key] = _merge_ladders_by_id(src.get(key), dst.get(key))
    head.attrs = dst
    _flag_attrs(head)
    _set_cs(
        head,
        role="vo_parent",
        parent_uuid=head.uuid,
        coverage_kind="parent",
        use_parent_still=False,
        coverage_parent_id="",
        coverage_parent_number=None,
    )
    for kid in ordered[1:]:
        _set_cs(
            kid,
            role="shot",
            parent_uuid=head.uuid,
            coverage_kind="child",
            use_parent_still=True,
            coverage_parent_number=int(head.number),
        )
    return head


async def delete_montage_frame(
    session: AsyncSession,
    project: Project,
    frame_id: int,
) -> dict[str, Any]:
    """Удалить только этот кадр. Детей сцены не трогаем — повышаем первого."""
    from app.services.ensure_frames_from_disk import quarantine_frame_media

    frames = await _load_frames(session, int(project.id))
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError(f"кадр {frame_id} не найден")
    number = int(frame.number)
    if is_shot_child(frame):
        await delete_coverage_child(session, project, frame, frames)
        board = montage_meta(project)
        drop_pending_ops_for_frames(board, {number})
        set_montage_meta(project, board)
        remember_deleted(project, number)
        logger.info("montage delete child #{} frame {}", project.id, number)
        return {"ok": True, "frame_id": frame_id, "number": number, "deleted": 1}

    kids = _children_of(frames, frame)
    new_head = _promote_first_child(frame, kids)
    quarantine_frame_media(project, number)
    await _drop_frame_rows(session, project, [frame])
    if new_head is not None:
        leftover = await _load_frames(session, int(project.id))
        live = next((fr for fr in leftover if int(fr.id) == int(new_head.id)), new_head)
        _refresh_shots_in_beat(leftover, live)
        await session.flush()
    board = montage_meta(project)
    drop_pending_ops_for_frames(board, {number})
    set_montage_meta(project, board)
    remember_deleted(project, number)
    logger.info(
        "montage delete frame #{} number {} (сцена жива, шотов {})",
        project.id,
        number,
        len(kids),
    )
    return {
        "ok": True,
        "frame_id": frame_id,
        "number": number,
        "deleted": 1,
        "promoted": int(new_head.number) if new_head is not None else None,
    }


async def merge_montage_scenes(
    session: AsyncSession,
    project: Project,
    *,
    left_frame_id: int,
    right_frame_id: int,
) -> dict[str, Any]:
    """Склеить две соседние VO-ячейки в одну сцену. Номера кадров не трогаем.

    Якоря обеих сцен переезжают на родителя склеенной сцены.
    """
    from app.services.montage_scene_editor import (
        cell_full_text,
        claim_scene_bits,
        scene_anchor_bits,
        scene_group,
    )

    frames = await _load_frames(session, int(project.id))
    left = next((fr for fr in frames if int(fr.id) == int(left_frame_id)), None)
    right = next((fr for fr in frames if int(fr.id) == int(right_frame_id)), None)
    if left is None or right is None:
        raise RuntimeError("кадр для склейки сцен не найден")
    left_parent, left_members = scene_group(frames, left)
    right_parent, right_members = scene_group(frames, right)
    if int(left_parent.id) == int(right_parent.id):
        raise RuntimeError("эти кадры уже в одной сцене")

    left_key = (float(left_parent.sort_key or 0.0), int(left_parent.number or 0))
    right_key = (float(right_parent.sort_key or 0.0), int(right_parent.number or 0))
    if left_key > right_key:
        left_parent, right_parent = right_parent, left_parent
        left_members, right_members = right_members, left_members

    left_full = cell_full_text(left_parent, left_members)
    right_full = cell_full_text(right_parent, right_members)
    merged_vo = " ".join(part for part in (left_full, right_full) if part).strip()

    before_numbers = [int(fr.number) for fr in frames]
    for member in right_members:
        was_head = int(member.id) == int(right_parent.id)
        _set_cs(member, role="shot", parent_uuid=left_parent.uuid)
        if was_head:
            _set_cs(
                member,
                coverage_kind="parent",
                use_parent_still=False,
                coverage_parent_id="",
                coverage_parent_number=None,
            )
            chain_attrs = dict(getattr(member, "attrs", None) or {})
            chain = str(
                chain_attrs.get("главное_действие") or chain_attrs.get("main_action") or ""
            ).strip()
            if chain:
                chain_attrs["главное_действие_архив"] = chain
                member.attrs = chain_attrs
                _flag_attrs(member)
                logger.warning(
                    "WARN merge_montage_scenes: цепь правой сцены #{} сохранена в архиве",
                    member.number,
                )
            _clear_child_scene_chain(member)
            attrs = dict(getattr(member, "attrs", None) or {})
            if "vo_cell_full" in attrs:
                attrs.pop("vo_cell_full", None)
                member.attrs = attrs
                _flag_attrs(member)

    if merged_vo:
        attrs = dict(getattr(left_parent, "attrs", None) or {})
        attrs["vo_cell_full"] = merged_vo
        left_parent.attrs = attrs
        _flag_attrs(left_parent)

    ordered = await _load_frames(session, int(project.id))
    live_left = next(
        (fr for fr in ordered if int(fr.id) == int(left_parent.id)), left_parent
    )
    _refresh_shots_in_beat(ordered, live_left)
    await session.flush()

    after = await _load_frames(session, int(project.id))
    after_numbers = [int(fr.number) for fr in after]
    if after_numbers != before_numbers:
        raise RuntimeError("склейка сцен не должна менять номера кадров")
    _, members = scene_group(after, live_left)
    anchors = scene_anchor_bits(after, live_left, members)
    claim_scene_bits(after, live_left, members, anchors)
    await session.flush()
    logger.info(
        "montage merge scenes #{} {}+{} → ячейка {} кадров={}",
        project.id,
        left_parent.number,
        right_parent.number,
        live_left.number,
        len(members),
    )
    from app.services.montage_scene_editor import scene_display_ordinals

    return {
        "ok": True,
        "parent_id": int(live_left.id),
        "parent_number": int(live_left.number),
        "parent_ordinal": scene_display_ordinals(after).get(int(live_left.id)),
        "merged_frames": len(right_members),
        "vo_scene_size": len(members),
    }


def _promote_to_vo_parent(frame: Frame) -> None:
    uid = str(getattr(frame, "uuid", "") or "").strip()
    if not uid:
        raise RuntimeError(f"у кадра #{frame.number} нет uuid — нельзя сделать сценой")
    _set_cs(
        frame,
        role="vo_parent",
        parent_uuid=uid,
        coverage_kind="parent",
        use_parent_still=False,
        coverage_parent_id="",
        coverage_parent_number=None,
        leftover=False,
        shot_index=1,
        shots_in_beat=1,
        montage_split=True,
    )


def _rewrite_vo_cell(parent: Frame, members: list[Frame]) -> None:
    """vo_cell_full / кадры[] / главное_действие только из текущих членов."""
    from app.services.montage_scene_editor import frame_action, frame_place
    from app.services.shot_templates import format_scene_chain

    vo = " ".join(
        (getattr(m, "voiceover_text", None) or "").strip()
        for m in members
        if (getattr(m, "voiceover_text", None) or "").strip()
    )
    attrs = dict(getattr(parent, "attrs", None) or {})
    if vo:
        attrs["vo_cell_full"] = vo
    else:
        attrs.pop("vo_cell_full", None)
    place = frame_place(parent)
    old_kadry = attrs.get("кадры")
    kadry: list[dict[str, Any]] = []
    chain: list[dict[str, Any]] = []
    for i, member in enumerate(members):
        act = frame_action(member)
        loc = frame_place(member) or place
        piece = (getattr(member, "voiceover_text", None) or "").strip()
        kadry.append(
            {
                "id": f"{int(parent.number)}-K{i + 1}",
                "порядок": i + 1,
                "действие": act,
                "место": loc,
                "закадр": piece,
            }
        )
        chain.append({"n": i + 1, "place": loc, "action": act, "vo": piece})
    from app.services.db_apply import merge_kadry_keep_missing

    attrs["кадры"] = merge_kadry_keep_missing(old_kadry, kadry)
    chain_text = format_scene_chain(chain)
    if chain_text:
        attrs["главное_действие"] = chain_text
        attrs["main_action"] = chain_text
    parent.attrs = attrs
    _flag_attrs(parent)


def _timeline_members(members: list[Frame]) -> list[Frame]:
    return sorted(
        members,
        key=lambda m: (float(getattr(m, "sort_key", 0) or 0.0), int(m.number or 0)),
    )


def _visible_and_extras(
    members: list[Frame],
    frame_ids: list[int] | None,
) -> tuple[list[Frame], list[Frame]]:
    """Кадры, которые делим (как на доске) vs хвост, который прячем."""
    ordered = _timeline_members(members)
    live = [m for m in ordered if not bool(_cs(m).get("leftover"))]
    if frame_ids:
        want = {int(x) for x in frame_ids}
        visible = [m for m in live if int(m.id) in want]
        extras = [m for m in ordered if m not in visible]
        return visible, extras
    leftovers = [m for m in ordered if bool(_cs(m).get("leftover"))]
    return live, leftovers


def _mark_leftover_shots(parent: Frame, extras: list[Frame]) -> None:
    uid = str(getattr(parent, "uuid", "") or "")
    for extra in extras:
        _set_cs(
            extra,
            role="shot",
            parent_uuid=uid,
            leftover=True,
        )


def _split_anchor_bits(frames: list[Frame], parents: list[Frame]) -> None:
    """После разделения каждый якорь уходит в сцену, где лежит его текст."""
    from app.services.montage_scene_editor import (
        claim_scene_bits,
        scene_anchor_bits,
        scene_group,
        scene_index,
    )

    index = scene_index(frames)
    plan: list[tuple[Frame, list[Frame], list[dict[str, Any]]]] = []
    for head in parents:
        parent, members = scene_group(frames, head)
        plan.append((parent, members, scene_anchor_bits(frames, parent, members, index=index)))
    for parent, members, rows in plan:
        claim_scene_bits(frames, parent, members, rows)


SPLIT_UNDO_KEY = "split_undo"
SPLIT_UNDO_LIMIT = 10
_MISSING = object()


def _attrs_snapshot(frames: list[Frame]) -> dict[int, dict[str, Any]]:
    return {int(fr.id): deepcopy(dict(getattr(fr, "attrs", None) or {})) for fr in frames}


def _val_sha(value: Any) -> str:
    if value is _MISSING:
        return "missing"
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _record_split_undo(
    project: Project,
    *,
    kind: str,
    before: dict[int, dict[str, Any]],
    after: list[Frame],
    scene_frame_ids: list[int],
    at_frame_id: int,
) -> str | None:
    changes: dict[str, dict[str, Any]] = {}
    for fr in after:
        old = before.get(int(fr.id))
        if old is None:
            continue
        new = dict(getattr(fr, "attrs", None) or {})
        keys: dict[str, Any] = {}
        for key in set(old) | set(new):
            ov = old.get(key, _MISSING)
            nv = new.get(key, _MISSING)
            if _val_sha(ov) == _val_sha(nv):
                continue
            keys[key] = {
                "had": ov is not _MISSING,
                "before": None if ov is _MISSING else ov,
                "after_sha": _val_sha(nv),
            }
        if keys:
            changes[str(int(fr.id))] = keys
    if not changes:
        return None
    undo_id = hashlib.sha1(
        f"{project.id}:{datetime.now(timezone.utc).isoformat()}:{at_frame_id}".encode()
    ).hexdigest()[:12]
    board = montage_meta(project)
    stack = [e for e in (board.get(SPLIT_UNDO_KEY) or []) if isinstance(e, dict)]
    stack.append(
        {
            "id": undo_id,
            "kind": kind,
            "at_frame_id": int(at_frame_id),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "scene_frame_ids": [int(x) for x in scene_frame_ids],
            "changes": changes,
        }
    )
    set_montage_meta(project, {SPLIT_UNDO_KEY: stack[-SPLIT_UNDO_LIMIT:]})
    return undo_id


def split_undo_info(project: Project) -> dict[str, Any]:
    stack = [e for e in (montage_meta(project).get(SPLIT_UNDO_KEY) or []) if isinstance(e, dict)]
    last = stack[-1] if stack else None
    return {
        "can_undo": last is not None,
        "undo_id": last.get("id") if last else None,
        "kind": last.get("kind") if last else None,
        "at_frame_id": last.get("at_frame_id") if last else None,
        "ts": last.get("ts") if last else None,
        "depth": len(stack),
    }


async def undo_montage_split(
    session: AsyncSession,
    project: Project,
    *,
    undo_id: str | None = None,
) -> dict[str, Any]:
    board = montage_meta(project)
    stack = [e for e in (board.get(SPLIT_UNDO_KEY) or []) if isinstance(e, dict)]
    if not stack:
        raise RuntimeError("нет деления, которое можно отменить")
    entry = stack[-1]
    if undo_id and str(undo_id) != str(entry.get("id")):
        raise RuntimeError("можно отменить только самое последнее деление")
    frames = await _load_frames(session, int(project.id))
    by_id = {int(fr.id): fr for fr in frames}
    changes: dict[str, dict[str, Any]] = entry.get("changes") or {}
    conflicts: list[str] = []
    for fid_s, keys in changes.items():
        fr = by_id.get(int(fid_s))
        if fr is None:
            conflicts.append(f"кадр id={fid_s} удалён")
            continue
        attrs = dict(getattr(fr, "attrs", None) or {})
        for key, spec in keys.items():
            cur = attrs.get(key, _MISSING)
            if _val_sha(cur) != spec.get("after_sha"):
                conflicts.append(f"#{fr.number}")
                break
    if conflicts:
        raise RuntimeError(
            "после деления эти кадры правили ("
            + ", ".join(dict.fromkeys(conflicts))
            + "): отмена затёрла бы правки. Верните сцены вручную (склейка)."
        )
    before_numbers = [int(fr.number) for fr in frames]
    restored_keys = 0
    for fid_s, keys in changes.items():
        fr = by_id[int(fid_s)]
        attrs = dict(getattr(fr, "attrs", None) or {})
        for key, spec in keys.items():
            if spec.get("had"):
                attrs[key] = deepcopy(spec.get("before"))
            else:
                attrs.pop(key, None)
            restored_keys += 1
        fr.attrs = attrs
        _flag_attrs(fr)
    await session.flush()
    after = await _load_frames(session, int(project.id))
    if [int(fr.number) for fr in after] != before_numbers:
        raise RuntimeError("отмена деления не должна менять номера кадров")
    set_montage_meta(project, {SPLIT_UNDO_KEY: stack[:-1] or None})
    scene_ids = [int(x) for x in entry.get("scene_frame_ids") or []] or [int(x) for x in by_id]
    from app.models import ArtifactKind

    kinds = (ArtifactKind.scene_image, ArtifactKind.scene_video)
    media = (
        await session.execute(
            select(Artifact.kind)
            .where(Artifact.project_id == int(project.id))
            .where(Artifact.frame_id.in_(scene_ids))
            .where(Artifact.kind.in_(kinds))
        )
    ).scalars().all()
    images = sum(1 for k in media if k == ArtifactKind.scene_image)
    videos = sum(1 for k in media if k == ArtifactKind.scene_video)
    logger.info("montage split undo #{} id={} frames={}", project.id, entry.get("id"), len(changes))
    return {
        "ok": True,
        "undo_id": entry.get("id"),
        "kind": entry.get("kind"),
        "restored_frames": len(changes),
        "restored_keys": restored_keys,
        "paid_media_kept": {"images": images, "videos": videos, "total": images + videos},
        "can_undo_more": bool(stack[:-1]),
    }


async def split_montage_scene_at(
    session: AsyncSession,
    project: Project,
    *,
    at_frame_id: int,
    frame_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Кадр ``at`` и все правее в ячейке — новая VO-сцена. Номера не трогаем."""
    from app.services.montage_scene_editor import scene_display_ordinals, scene_group

    frames = await _load_frames(session, int(project.id))
    at = next((fr for fr in frames if int(fr.id) == int(at_frame_id)), None)
    if at is None:
        raise RuntimeError("кадр для разделения сцены не найден")
    parent, members = scene_group(frames, at)
    visible, extras = _visible_and_extras(members, frame_ids)
    undo_snap = _attrs_snapshot(frames)
    idx = next(
        (i for i, m in enumerate(visible) if int(m.id) == int(at.id)),
        -1,
    )
    if idx < 1:
        raise RuntimeError("первый кадр сцены нельзя отделить — это и есть сцена")
    left = visible[:idx]
    right = visible[idx:]
    before_numbers = [int(fr.number) for fr in frames]

    new_parent = right[0]
    _promote_to_vo_parent(new_parent)
    new_uid = str(new_parent.uuid or "")
    for extra in right[1:]:
        _set_cs(
            extra,
            role="shot",
            parent_uuid=new_uid,
            leftover=False,
            coverage_kind="",
        )
        _clear_child_scene_chain(extra)
    _rewrite_vo_cell(parent, left)
    _rewrite_vo_cell(new_parent, right)
    _mark_leftover_shots(parent, extras)
    await session.flush()

    ordered = await _load_frames(session, int(project.id))
    live_left = next((fr for fr in ordered if int(fr.id) == int(parent.id)), parent)
    live_right = next(
        (fr for fr in ordered if int(fr.id) == int(new_parent.id)), new_parent
    )
    _refresh_shots_in_beat(ordered, live_left)
    _refresh_shots_in_beat(ordered, live_right)
    await session.flush()
    _split_anchor_bits(ordered, [live_left, live_right])
    await session.flush()

    after = await _load_frames(session, int(project.id))
    after_numbers = [int(fr.number) for fr in after]
    if after_numbers != before_numbers:
        raise RuntimeError("разделение сцен не должно менять номера кадров")
    undo_id = _record_split_undo(
        project, kind="at", before=undo_snap, after=after,
        scene_frame_ids=[int(m.id) for m in members], at_frame_id=int(at.id),
    )
    ordinals = scene_display_ordinals(after)
    logger.info(
        "montage split scene #{} at {} → ячейки {} + {}",
        project.id,
        at.number,
        live_left.number,
        live_right.number,
    )
    return {
        "ok": True,
        "left_id": int(live_left.id),
        "left_number": int(live_left.number),
        "right_id": int(live_right.id),
        "right_number": int(live_right.number),
        "left_ordinal": ordinals.get(int(live_left.id)),
        "right_ordinal": ordinals.get(int(live_right.id)),
        "left_size": len(left),
        "right_size": len(right),
        "undo_id": undo_id,
    }


async def split_montage_scene_frames(
    session: AsyncSession,
    project: Project,
    *,
    frame_id: int,
    frame_ids: list[int] | None = None,
) -> dict[str, Any]:
    """Каждый шот VO-ячейки — отдельная сцена. Номера кадров не трогаем."""
    from app.services.montage_scene_editor import scene_group

    frames = await _load_frames(session, int(project.id))
    frame = next((fr for fr in frames if int(fr.id) == int(frame_id)), None)
    if frame is None:
        raise RuntimeError("кадр для разделения сцены не найден")
    _parent, members = scene_group(frames, frame)
    visible, extras = _visible_and_extras(members, frame_ids)
    if len(visible) < 2:
        raise RuntimeError("в сцене один кадр — делить нечего")
    undo_snap = _attrs_snapshot(frames)
    before_numbers = [int(fr.number) for fr in frames]
    head = visible[0]
    for member in visible[1:]:
        _promote_to_vo_parent(member)
        _clear_child_scene_chain(member)
        _rewrite_vo_cell(member, [member])
    _rewrite_vo_cell(head, [head])
    _mark_leftover_shots(head, extras)
    await session.flush()
    ordered = await _load_frames(session, int(project.id))
    for member in visible:
        live = next((fr for fr in ordered if int(fr.id) == int(member.id)), member)
        _refresh_shots_in_beat(ordered, live)
    await session.flush()
    _split_anchor_bits(
        ordered,
        [next((fr for fr in ordered if int(fr.id) == int(m.id)), m) for m in visible],
    )
    await session.flush()
    after = await _load_frames(session, int(project.id))
    after_numbers = [int(fr.number) for fr in after]
    if after_numbers != before_numbers:
        raise RuntimeError("разделение сцен не должно менять номера кадров")
    undo_id = _record_split_undo(
        project, kind="all", before=undo_snap, after=after,
        scene_frame_ids=[int(m.id) for m in members], at_frame_id=int(head.id),
    )
    from app.services.montage_scene_editor import scene_display_ordinals

    ordinals = scene_display_ordinals(after)
    logger.info(
        "montage split-all #{} cell={} → {} сцен",
        project.id,
        head.number,
        len(visible),
    )
    return {
        "ok": True,
        "scenes": len(visible),
        "parent_id": int(head.id),
        "parent_number": int(head.number),
        "parent_ordinal": ordinals.get(int(head.id)),
        "undo_id": undo_id,
    }
