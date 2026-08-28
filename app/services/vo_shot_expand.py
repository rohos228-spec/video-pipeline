"""Покрытие кадры[] → ячейки закадра 1:1.

После нод shots / frames / QC группа script_frames_qc вызывает
``apply_shot_coverage_to_vo_cells``: недостающие шоты вставляются, затем
каждый кадр становится своей VO-ячейкой. Текст режется по клаузам
(``split_text_into_parts``), не по квоте 27–54 из GPT ``кадры[].закадр``.
"""

from __future__ import annotations

import copy
import re
from typing import Any

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Frame, Project
from app.services.db_v2 import insert_frame_after
from app.services.scene_design.camera_expand import (
    renumber_frames_by_sort_key,
    split_text_into_parts,
    vo_chunk_is_dangling,
)

_ATTR_KEY = "camera_subdivide"
_VO_SHOT_KEY = "vo_shot"
_CLAUSE_RE = re.compile(r"(?<=[.!?…])\s+")
_VO_CHARS_PER_SEC = 14.0
_MIN_SEC = 2.0
_MAX_SHOTS = 3


def is_shot_child(frame: Any) -> bool:
    """Визуальный дочерний кадр покрытия, не VO-родитель."""
    return str(_cs(frame).get("role") or "") == "shot"


_COVERAGE_SHOT_RE = re.compile(r"^(.+)-K(\d+)$")
_PARENT_SCENE_LOCK = (
    "Image 1 is the previous coverage still of the SAME scene "
    "(layout / set / wardrobe / lighting / cast-count / prop-identity lock). "
    "Preserve: the same room architecture, furniture placement, wall color, "
    "the SAME people already visible (same body count — do not invent extras), "
    "their clothes, lighting direction, and the SAME named objects "
    "(the painting/portrait/document/table in Image 1 is that exact object, "
    "not a replacement). "
    "Change: camera position, framing, and this shot's action only. "
    "If punching in on a painting, it must be the painting already in Image 1. "
    "If Image 1 shows one person at the table, the result still shows one person. "
    "Do not invent a new location or a new person. The viewer must recognize "
    "the next camera in the same take."
)
_CHAR_SHEET_LOCK = (
    "Image 2 is the character sheet: identity only (face/body/clothes). "
    "Do not copy the sheet pose or layout."
)


def planned_shots_from_attrs(frame: Any) -> list[dict[str, Any]]:
    """Список кадров ноды «сцены → кадры» (attrs.кадры)."""
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("кадры") or attrs.get("shots")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def coverage_shot_id(frame: Any) -> str:
    """id шота покрытия: ``кадры[0].id`` или ``camera_subdivide.shot_id``."""
    planned = planned_shots_from_attrs(frame)
    if planned:
        sid = str(planned[0].get("id") or "").strip()
        if sid:
            return sid
    return str(_cs(frame).get("shot_id") or "").strip()


def parse_coverage_shot(shot_id: str) -> tuple[str, int] | None:
    m = _COVERAGE_SHOT_RE.match((shot_id or "").strip())
    if not m:
        return None
    return m.group(1), int(m.group(2))


def is_coverage_child(frame: Any) -> bool:
    """K2/K3 после flatten (role=vo_parent) или настоящий shot-ребёнок."""
    if is_shot_child(frame):
        return True
    parsed = parse_coverage_shot(coverage_shot_id(frame))
    return parsed is not None and parsed[1] >= 2


def coverage_parent_shot_id(frame: Any) -> str:
    parsed = parse_coverage_shot(coverage_shot_id(frame))
    if parsed is None or parsed[1] < 2:
        return ""
    return f"{parsed[0]}-K1"


def find_coverage_parent_frame(frames: list[Any], child: Any) -> Any | None:
    """Родитель покрытия: тот же group-K1. Не предыдущий K2 для K3."""
    parent_sid = coverage_parent_shot_id(child)
    if not parent_sid:
        return None
    child_uid = str(getattr(child, "uuid", "") or "")
    for fr in frames:
        if coverage_shot_id(fr) != parent_sid:
            continue
        if child_uid and str(getattr(fr, "uuid", "") or "") == child_uid:
            continue
        return fr
    return None


def merge_parent_scene_refs(
    parent_png: Any | None,
    other: list[Any],
    *,
    max_refs: int = 2,
) -> list[Any]:
    """Слот 1 = PNG родителя (layout lock), слот 2 = персонаж."""
    if parent_png is None:
        return list(other)[:max_refs]
    out = [parent_png]
    parent_key = str(parent_png)
    for item in other:
        if str(item) == parent_key:
            continue
        out.append(item)
        if len(out) >= max_refs:
            break
    return out


def with_parent_scene_lock(
    prompt: str,
    *,
    has_parent_ref: bool,
    has_char_ref: bool = False,
) -> str:
    """Контракт Preserve/Change для GPT Image 2, если прикреплён still родителя."""
    raw = (prompt or "").strip()
    if not has_parent_ref:
        return raw
    if raw.startswith("Image 1 is the previous coverage still"):
        return raw
    parts = [_PARENT_SCENE_LOCK]
    if has_char_ref:
        parts.append(_CHAR_SHEET_LOCK)
    return "\n".join(parts) + "\n\n" + raw


def flattened_coverage_groups(
    frames: list[Any],
) -> dict[str, list[Any]]:
    """K1+K2+… после flatten: группа по префиксу id (`5` из `5-K2`).

    После promote каждый кадр — vo_parent с ``parent_uuid`` = сам себе,
    поэтому ``_group_by_parent`` их не склеивает.
    """
    buckets: dict[str, list[tuple[int, Any]]] = {}
    for fr in frames:
        if not _is_pipeline_frame(fr):
            continue
        parsed = parse_coverage_shot(coverage_shot_id(fr))
        if parsed is None:
            continue
        group, k = parsed
        buckets.setdefault(group, []).append((k, fr))
    out: dict[str, list[Any]] = {}
    for group, items in buckets.items():
        items.sort(key=lambda item: item[0])
        ks = [k for k, _ in items]
        if 1 not in ks or max(ks) < 2:
            continue
        out[group] = [fr for _, fr in items]
    return out


def merge_flattened_coverage_members(
    members: list[Any],
) -> tuple[Any, list[Any]]:
    """Склеить K1+K2+K3 обратно на родителя: полный закадр + кадры[]."""
    if len(members) < 2:
        return members[0], []
    parent = members[0]
    extra = list(members[1:])
    shots: list[dict[str, Any]] = []
    parts: list[str] = []
    parent_sid = ""
    for i, fr in enumerate(members):
        planned = planned_shots_from_attrs(fr)
        shot = copy.deepcopy(planned[0]) if planned else {}
        if not isinstance(shot, dict):
            shot = {}
        piece = (getattr(fr, "voiceover_text", None) or "").strip()
        parts.append(piece)
        sid = str(shot.get("id") or coverage_shot_id(fr) or "").strip()
        if i == 0:
            parent_sid = sid
        shot["id"] = sid
        shot["порядок"] = i + 1
        shot["parent_id"] = None if i == 0 else parent_sid or None
        shot["закадр"] = piece
        shots.append(shot)
    full = " ".join(" ".join(parts).split())
    if full:
        parent.voiceover_text = full
        parent.duration_seconds = vo_duration_sec(full, shots=1)
    attrs = dict(getattr(parent, "attrs", None) or {})
    attrs["кадры"] = shots
    attrs["vo_cell_full"] = full
    parent.attrs = attrs
    _flag_attrs(parent)
    _set_cs(
        parent,
        role="vo_parent",
        parent_uuid=str(getattr(parent, "uuid", "") or ""),
        shot_index=1,
        shots_in_beat=len(shots),
        shot_id=parent_sid,
    )
    return parent, extra


async def collapse_flattened_coverage_cells(
    session: AsyncSession,
    project: Project,
) -> dict[str, Any]:
    """Перед повторным script_frames_qc: K2/K3 → снова дети на K1.

    Иначе GPT видит 44 самостоятельных ячейки и пишет покрытие на покрытие.
    """
    from sqlalchemy import delete, or_, select as sel, update

    from app.models import Artifact, Frame, FrameEdge, FrameText, PromptVersion
    from app.services.scene_design.camera_expand import renumber_frames_by_sort_key

    frames = list(
        (
            await session.execute(
                sel(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    groups = flattened_coverage_groups(frames)
    extra: list[Any] = []
    merged = 0
    for members in groups.values():
        _parent, drop = merge_flattened_coverage_members(members)
        extra.extend(drop)
        merged += 1
    drop_ids = [int(fr.id) for fr in extra if getattr(fr, "id", None)]
    if drop_ids:
        await session.execute(
            update(Artifact)
            .where(Artifact.frame_id.in_(drop_ids))
            .values(frame_id=None)
        )
        await session.execute(delete(PromptVersion).where(PromptVersion.frame_id.in_(drop_ids)))
        await session.execute(delete(FrameText).where(FrameText.frame_id.in_(drop_ids)))
        await session.execute(
            delete(FrameEdge).where(
                FrameEdge.project_id == project.id,
                or_(
                    FrameEdge.from_frame_id.in_(drop_ids),
                    FrameEdge.to_frame_id.in_(drop_ids),
                ),
            )
        )
        for fr in extra:
            if getattr(fr, "id", None):
                await session.delete(fr)
        await session.flush()
        await renumber_frames_by_sort_key(session, project)
    report = {
        "groups": merged,
        "deleted": len(drop_ids),
    }
    if merged or drop_ids:
        logger.info(
            "[#{}] collapse flattened coverage: groups={} deleted={}",
            project.id,
            merged,
            len(drop_ids),
        )
    return report


def resolve_shot_plan(
    original_vo: str, planned: list[dict[str, Any]]
) -> tuple[int, list[str]]:
    """Сколько шотов: только из кадры[]. Без плана не выдумывать нарезку."""
    text = (original_vo or "").strip()
    if planned:
        partition = kadry_vo_partition(text, planned)
        if partition:
            return len(partition), partition
        need = max(1, len(planned))
        return need, split_text_into_parts(text, need)
    return 1, [text] if text else [""]


def bits_from_attrs(frame: Any) -> list[dict[str, Any]]:
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return []
    raw = attrs.get("биты") or attrs.get("bits")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def kadry_from_bits(full_vo: str, bits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """1 бит → 1 кадр. Склейка закадр = весь текст ячейки, без хвостов."""
    text = " ".join((full_vo or "").split())
    ordered = sorted(
        (item for item in bits if isinstance(item, dict)),
        key=lambda item: int(item.get("порядок") or 0),
    )
    if not text or not ordered:
        return []
    starts: list[int] = []
    cursor = 0
    for i, item in enumerate(ordered):
        anchor = " ".join(str(item.get("якорь") or "").split())
        idx = -1
        if anchor:
            idx = text.find(anchor, cursor)
            if idx < 0:
                idx = text.lower().find(anchor.lower(), cursor)
        if idx < 0:
            parts = split_text_into_parts(text, len(ordered))
            while parts and not str(parts[-1] or "").strip():
                parts.pop()
            if " ".join(" ".join(parts).split()) != text:
                return []
            return _kadry_rows(ordered[: len(parts)], parts)
        starts.append(0 if i == 0 else idx)
        cursor = idx + max(len(anchor), 1)
    parts: list[str] = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        if end < start:
            end = start
        parts.append(text[start:end].strip())
    if " ".join(" ".join(parts).split()) != text:
        parts = split_text_into_parts(text, len(ordered))
        while parts and not str(parts[-1] or "").strip():
            parts.pop()
        if " ".join(" ".join(parts).split()) != text:
            return []
        ordered = ordered[: len(parts)]
    return _kadry_rows(ordered, parts)


def _kadry_rows(
    bits: list[dict[str, Any]], parts: list[str]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    master = "B01-K1"
    for i, (item, piece) in enumerate(zip(bits, parts, strict=False)):
        sid = f"B01-K{i + 1}"
        rows.append(
            {
                "id": sid,
                "порядок": i + 1,
                "parent_id": None if i == 0 else master,
                "закадр": piece,
                "действие": str(
                    item.get("изменение") or item.get("глагол") or ""
                ).strip(),
            }
        )
    return rows


def ensure_kadry_from_bits(frame: Any) -> int:
    """Если кадры[] пустые, а биты есть — собрать покрытие по якорям."""
    if planned_shots_from_attrs(frame):
        return 0
    bits = bits_from_attrs(frame)
    full = (getattr(frame, "voiceover_text", None) or "").strip()
    planned = kadry_from_bits(full, bits)
    if not planned:
        return 0
    attrs = dict(getattr(frame, "attrs", None) or {})
    attrs["кадры"] = planned
    attrs["vo_cell_full"] = " ".join(full.split())
    frame.attrs = attrs
    _flag_attrs(frame)
    return len(planned)


_SCENE_CHAIN_RE = re.compile(r"(?m)^\s*\d+\.\s+\S")


def looks_like_scene_chain(text: str) -> bool:
    """Нумерованная цепь сцен: «1. место — действие» + кусок в скобках."""
    raw = (text or "").strip()
    if not raw:
        return False
    return bool(_SCENE_CHAIN_RE.search(raw) and "(" in raw)


def _apply_shot_meta(frame: Any, shot: dict[str, Any] | None) -> None:
    if not shot:
        return
    attrs = dict(getattr(frame, "attrs", None) or {})
    place = str(shot.get("место") or shot.get("place") or "").strip()
    action = str(shot.get("действие") or shot.get("action") or "").strip()
    if place:
        attrs["place"] = place
    if action:
        attrs["shot01_action"] = action
        existing = str(
            attrs.get("main_action") or attrs.get("главное_действие") or ""
        ).strip()
        # Expand не затирает цепь сцен (и любой уже записанный main_action).
        if not existing:
            attrs["main_action"] = action
    frame.attrs = attrs
    _flag_attrs(frame)
    extra: dict[str, Any] = {}
    for src, dst in (
        ("план", "план"),
        ("ракурс", "ракурс"),
        ("id", "shot_id"),
        ("parent_id", "coverage_parent_id"),
        ("сцена", "сцена"),
        ("место", "место"),
    ):
        val = shot.get(src)
        if val not in (None, ""):
            extra[dst] = val
    plan = str(shot.get("план") or "").strip()
    if plan:
        extra["крупность"] = plan
    if extra:
        _set_cs(frame, **extra)


def shots_needed_for_vo(text: str) -> int:
    """Сколько визуальных шотов на ячейку: по фразам, не больше 3."""
    raw = (text or "").strip()
    if not raw:
        return 1
    clauses = [p.strip() for p in _CLAUSE_RE.split(raw) if p.strip()]
    n = len(clauses) if len(clauses) > 1 else 1
    if n <= 1 and len(raw.split()) >= 8:
        n = 2
    return max(1, min(_MAX_SHOTS, n))


def vo_duration_sec(text: str, *, shots: int = 1) -> float:
    """Длительность ячейки по 14 символам/сек; на шот не короче 2с."""
    chars = len((text or "").strip())
    total = max(_MIN_SEC * max(1, shots), chars / _VO_CHARS_PER_SEC if chars else _MIN_SEC)
    return round(total, 2)


def _is_pipeline_frame(frame: Any) -> bool:
    if not str(getattr(frame, "uuid", "") or "").strip():
        return False
    attrs = getattr(frame, "attrs", None)
    if isinstance(attrs, dict) and attrs.get("from_disk_media"):
        return False
    return True


def _cs(frame: Any) -> dict[str, Any]:
    attrs = getattr(frame, "attrs", None)
    if not isinstance(attrs, dict):
        return {}
    raw = attrs.get(_ATTR_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _flag_attrs(frame: Any) -> None:
    state = getattr(frame, "_sa_instance_state", None)
    if state is None:
        return
    from sqlalchemy.orm.attributes import flag_modified

    flag_modified(frame, "attrs")


def _set_cs(frame: Any, **fields: Any) -> None:
    attrs = dict(getattr(frame, "attrs", None) or {})
    cs = dict(attrs.get(_ATTR_KEY) or {})
    cs.update(fields)
    attrs[_ATTR_KEY] = cs
    frame.attrs = attrs
    _flag_attrs(frame)


def repair_split_vo_on_parents(frames: list[Any]) -> int:
    """Вернуть полный закадр разбивки на родителя, с детей снять.

    Если expand уже нарезал Frame.voiceover_text — склеить обратно.
    Не генерирует новый текст.
    """
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        if not _is_pipeline_frame(fr):
            continue
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    repaired = 0
    for members in groups.values():
        members.sort(
            key=lambda m: int(_cs(m).get("shot_index") or 0)
        )
        if len(members) <= 1:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        parts = [(getattr(m, "voiceover_text", None) or "").strip() for m in members]
        child_parts = [
            (getattr(m, "voiceover_text", None) or "").strip()
            for m in members
            if m is not parent
        ]
        if not any(child_parts):
            continue
        unique = {p for p in parts if p}
        if len(unique) == 1:
            joined = next(iter(unique))
        else:
            joined = " ".join(p for p in parts if p)
        if joined:
            parent.voiceover_text = joined
        for m in members:
            if m is not parent:
                m.voiceover_text = ""
        repaired += 1
    return repaired


def apply_vo_shot_cuts(frames: list[Any]) -> int:
    """Дубликат ячейки разбивки режем по шотам. voiceover_text не трогаем."""
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        if not _is_pipeline_frame(fr):
            continue
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    updated = 0
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        original = (getattr(parent, "voiceover_text", None) or "").strip()
        if not original:
            continue
        parts = split_text_into_parts(original, len(members))
        for i, fr in enumerate(members):
            piece = parts[i] if i < len(parts) else ""
            if str(_cs(fr).get(_VO_SHOT_KEY) or "") == piece:
                continue
            _set_cs(fr, **{_VO_SHOT_KEY: piece})
            updated += 1
    return updated


def _group_by_parent(frames: list[Any]) -> dict[str, list[Any]]:
    groups: dict[str, list[Any]] = {}
    for fr in frames:
        if not _is_pipeline_frame(fr):
            continue
        cs = _cs(fr)
        parent = str(cs.get("parent_uuid") or getattr(fr, "uuid", "") or "").strip()
        if not parent:
            continue
        groups.setdefault(parent, []).append(fr)
    for members in groups.values():
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0))
    return groups


def _norm_words(text: str) -> list[str]:
    return " ".join((text or "").split()).split()


def kadry_vo_partition(full: str, planned: list[dict[str, Any]]) -> list[str] | None:
    """закадр из кадры[], только если склейка = ячейка и нет обрубков."""
    parts = [str(item.get("закадр") or "").strip() for item in planned]
    if not parts or not all(parts):
        return None
    if _norm_words(" ".join(parts)) != _norm_words(full):
        return None
    if any(vo_chunk_is_dangling(p) for p in parts):
        return None
    return parts


def _cell_full_text(parent: Any, members: list[Any]) -> str:
    attrs = getattr(parent, "attrs", None) or {}
    full = str(attrs.get("vo_cell_full") or "").strip() if isinstance(attrs, dict) else ""
    if full:
        return full
    joined = " ".join(
        (getattr(m, "voiceover_text", None) or "").strip()
        for m in members
        if (getattr(m, "voiceover_text", None) or "").strip()
    )
    joined = " ".join(joined.split())
    if joined:
        return joined
    return (getattr(parent, "voiceover_text", None) or "").strip()


def apply_shot_voiceover_to_cells(frames: list[Any]) -> int:
    """После QC: закадр ячейки → voiceover_text каждого кадра группы.

    Режем по фразам/клаузам, не по 27–54 и не по словам. Склейка непустых
    кусков = исходная ячейка. Полный текст — в attrs.vo_cell_full.
    """
    updated = 0
    for members in _group_by_parent(frames).values():
        if not members:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        full = _cell_full_text(parent, members)
        if not full:
            continue
        planned = planned_shots_from_attrs(parent)
        parts = split_text_into_parts(full, len(members))
        attrs = dict(getattr(parent, "attrs", None) or {})
        attrs["vo_cell_full"] = full
        if planned:
            for i, item in enumerate(planned):
                piece = parts[i] if i < len(parts) else ""
                if str(item.get("закадр") or "") != piece:
                    item["закадр"] = piece
                    updated += 1
            attrs["кадры"] = planned
        parent.attrs = attrs
        _flag_attrs(parent)
        for i, fr in enumerate(members):
            piece = parts[i] if i < len(parts) else ""
            if (getattr(fr, "voiceover_text", None) or "") != piece:
                fr.voiceover_text = piece
                updated += 1
            _set_cs(fr, **{_VO_SHOT_KEY: piece})
    return updated


def promote_shots_to_vo_cells(frames: list[Any]) -> tuple[int, list[Any]]:
    """Каждый кадр покрытия → своя ячейка закадра.

    Текст исходной ячейки режется по числу ``кадры[]``. ``parent_uuid`` =
    uuid самого кадра. Лишние дети сверх ``кадры[]`` — на удаление.
    """
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_VIDEO_PROMPT_ATTR,
    )

    updated = 0
    extra: list[Any] = []
    for members in list(_group_by_parent(frames).values()):
        if not members:
            continue
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        full = _cell_full_text(parent, members)
        if not full:
            continue
        planned = planned_shots_from_attrs(parent)
        n = max(1, len(planned) if planned else len(members))
        if n > len(members):
            # Expand ещё не создал слоты — не отбрасываем хвост закадра.
            n = len(members)
        parts = split_text_into_parts(full, n)
        while len(parts) > 1 and not str(parts[-1] or "").strip():
            parts.pop()
        n = max(1, len(parts))
        keep = members[:n]
        extra.extend(members[n:])
        for i, fr in enumerate(keep):
            piece = parts[i] if i < len(parts) else ""
            shot = copy.deepcopy(planned[i]) if i < len(planned) else None
            if isinstance(shot, dict):
                shot["закадр"] = piece
                shot["порядок"] = 1
                shot["parent_id"] = None
            uid = str(getattr(fr, "uuid", "") or "")
            attrs = dict(getattr(fr, "attrs", None) or {})
            attrs["vo_cell_full"] = piece
            if shot:
                attrs["кадры"] = [shot]
            s2 = str(attrs.get(SHOT2_PROMPT_ATTR) or attrs.get("промт_картинки_2") or "").strip()
            v2 = str(
                attrs.get(SHOT2_VIDEO_PROMPT_ATTR) or attrs.get("animation_prompt_shot2") or ""
            ).strip()
            if not str(getattr(fr, "image_prompt", None) or "").strip() and s2:
                fr.image_prompt = s2
            if not str(getattr(fr, "animation_prompt", None) or "").strip() and v2:
                fr.animation_prompt = v2
            attrs.pop(SHOT2_PROMPT_ATTR, None)
            attrs.pop("промт_картинки_2", None)
            attrs.pop(SHOT2_VIDEO_PROMPT_ATTR, None)
            attrs.pop("промты_детей", None)
            attrs.pop("child_prompts", None)
            if i > 0:
                attrs.pop("биты", None)
                attrs.pop("bits", None)
                attrs.pop("главное_действие", None)
                attrs.pop("main_action", None)
            fr.attrs = attrs
            _flag_attrs(fr)
            if (getattr(fr, "voiceover_text", None) or "") != piece:
                fr.voiceover_text = piece
            fr.duration_seconds = vo_duration_sec(piece, shots=1)
            _set_cs(
                fr,
                role="vo_parent",
                parent_uuid=uid,
                shot_index=1,
                shots_in_beat=1,
                **{_VO_SHOT_KEY: piece},
            )
            _apply_shot_meta(fr, shot)
            updated += 1
    return updated, extra


async def rebuild_vo_cells_from_shots(
    session: AsyncSession,
    project: Project,
    frames: list[Any],
) -> tuple[list[Any], dict[str, Any]]:
    """Пересобрать ячейки закадра 1:1 с кадрами. Лишние Frame удалить."""
    from sqlalchemy import delete, or_, update

    from app.models import Artifact, FrameEdge, FrameText, PromptVersion
    from app.services.scene_design.camera_expand import renumber_frames_by_sort_key

    n_cells, extra = promote_shots_to_vo_cells(frames)
    drop = [fr for fr in extra if getattr(fr, "id", None)]
    seen = {id(fr) for fr in drop}
    for fr in frames:
        if id(fr) in seen or not getattr(fr, "id", None):
            continue
        vo = (getattr(fr, "voiceover_text", None) or "").strip()
        if not _is_pipeline_frame(fr) or not vo:
            drop.append(fr)
            seen.add(id(fr))
    drop_ids = [int(fr.id) for fr in drop]
    if drop_ids:
        await session.execute(
            update(Artifact)
            .where(Artifact.frame_id.in_(drop_ids))
            .values(frame_id=None)
        )
        await session.execute(delete(PromptVersion).where(PromptVersion.frame_id.in_(drop_ids)))
        await session.execute(delete(FrameText).where(FrameText.frame_id.in_(drop_ids)))
        await session.execute(
            delete(FrameEdge).where(
                FrameEdge.project_id == project.id,
                or_(
                    FrameEdge.from_frame_id.in_(drop_ids),
                    FrameEdge.to_frame_id.in_(drop_ids),
                ),
            )
        )
        for fr in drop:
            await session.delete(fr)
        await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    work = [f for f in ordered if _is_pipeline_frame(f)]
    await session.execute(
        delete(FrameEdge).where(
            FrameEdge.project_id == project.id,
            FrameEdge.type == "next",
        )
    )
    for a, b in zip(work, work[1:]):
        session.add(
            FrameEdge(
                project_id=project.id,
                from_frame_id=a.id,
                to_frame_id=b.id,
                type="next",
            )
        )
    await session.flush()
    await session.execute(
        delete(FrameText).where(
            FrameText.project_id == project.id,
            FrameText.kind == "voiceover",
        )
    )
    for fr in work:
        vo = (getattr(fr, "voiceover_text", None) or "").strip()
        if not vo:
            continue
        session.add(
            FrameText(
                project_id=project.id,
                frame_id=fr.id,
                kind="voiceover",
                text=vo,
                sort_key=1.0,
            )
        )
    await session.flush()
    report = {
        "cells": n_cells,
        "deleted": len(drop_ids),
        "pipeline": len(work),
    }
    logger.info(
        "[#{}] rebuild VO cells from shots: cells={} deleted={} pipeline={}",
        project.id,
        n_cells,
        len(drop_ids),
        len(work),
    )
    return ordered, report


async def reseed_vo_cells_without_kadry(
    session: AsyncSession,
    project: Project,
) -> dict[str, Any]:
    """Несколько ячеек без кадры[], склейка = целый закадр → снова 1 seed + биты."""
    from sqlalchemy import select as sel

    from app.services import db_v2

    full = " ".join((db_v2.resolve_full_voiceover_text(project) or "").split())
    frames = list(
        (
            await session.execute(
                sel(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    work = [fr for fr in frames if _is_pipeline_frame(fr)]
    if len(work) <= 1 or not full:
        return {"reseeding": False, "cells": len(work)}
    if any(planned_shots_from_attrs(fr) for fr in work):
        return {"reseeding": False, "cells": len(work), "reason": "has_kadry"}
    joined = " ".join(
        " ".join((getattr(fr, "voiceover_text", None) or "").split()) for fr in work
    )
    if joined != full:
        return {"reseeding": False, "cells": len(work), "reason": "join_mismatch"}
    bits: list[Any] = []
    for fr in work:
        found = bits_from_attrs(fr)
        if found:
            bits = found
            break
    seed = await db_v2.ensure_single_seed_vo_cell(session, project, full)
    attrs = dict(getattr(seed, "attrs", None) or {})
    if bits:
        attrs["биты"] = bits
    seed.attrs = attrs
    _flag_attrs(seed)
    await session.flush()
    logger.info(
        "[#{}] reseed VO without кадры[]: cells {}→1 bits={}",
        project.id,
        len(work),
        len(bits),
    )
    return {"reseeding": True, "cells_before": len(work), "bits": len(bits)}


async def apply_shot_coverage_to_vo_cells(
    session: AsyncSession,
    project: Project,
) -> dict[str, Any]:
    """Хвост группы script_frames_qc: кадры[] → ячейки закадра 1:1 + R49."""
    from sqlalchemy import select as sel

    from app.models import Frame

    frames = list(
        (
            await session.execute(
                sel(Frame)
                .where(Frame.project_id == project.id)
                .order_by(Frame.sort_key, Frame.number)
            )
        )
        .scalars()
        .all()
    )
    frames, exp = await expand_vo_cells_into_shots(session, project, frames)
    n_cam = inherit_camera_on_children(frames)
    n_dist = distribute_coverage_prompts(frames)
    frames, rebuilt = await rebuild_vo_cells_from_shots(session, project, frames)
    work = [f for f in frames if _is_pipeline_frame(f)]
    xlsx: dict[str, Any] | None = None
    try:
        from app.services.db_apply import export_project_xlsx

        xlsx = export_project_xlsx(project, work)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[#{}] coverage export xlsx skipped: {}", project.id, exc)
    out = {
        **rebuilt,
        "expand": exp,
        "camera": n_cam,
        "dist": n_dist,
        "xlsx": xlsx,
    }
    logger.info(
        "[#{}] shot coverage → VO cells: pipeline={} deleted={} expand={}",
        project.id,
        out.get("pipeline"),
        out.get("deleted"),
        exp,
    )
    return out


def distribute_coverage_prompts(frames: list[Any]) -> int:
    """Промты покрытия: с родителя в shot2 детей. image_prompt детей не трогаем.

    Классический shot2 на родителе не запускаем, если есть дети — иначе
    дубль PNG. Статус shot2 переезжает на ребёнка.
    """
    from app.services.plan_shot2 import (
        SHOT2_PROMPT_ATTR,
        SHOT2_STATUS_ATTR,
        SHOT2_VIDEO_PROMPT_ATTR,
    )

    updated = 0
    for members in _group_by_parent(frames).values():
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            members[0],
        )
        children = [m for m in members if m is not parent]
        if not children:
            continue
        pattrs = dict(getattr(parent, "attrs", None) or {})
        listed = pattrs.get("промты_детей") or pattrs.get("child_prompts") or []
        if not isinstance(listed, list):
            listed = []
        parent_shot2 = str(pattrs.get(SHOT2_PROMPT_ATTR) or "").strip()
        parent_vid2 = str(pattrs.get(SHOT2_VIDEO_PROMPT_ATTR) or "").strip()
        for i, child in enumerate(children):
            entry = listed[i] if i < len(listed) and isinstance(listed[i], dict) else {}
            img = str(
                entry.get("промт_картинки")
                or entry.get("image_prompt")
                or (parent_shot2 if i == 0 else "")
                or ""
            ).strip()
            vid = str(
                entry.get("промт_видео")
                or entry.get("animation_prompt")
                or (parent_vid2 if i == 0 else "")
                or ""
            ).strip()
            existing_img = str(getattr(child, "image_prompt", None) or "").strip()
            existing_vid = str(getattr(child, "animation_prompt", None) or "").strip()
            if not img:
                img = existing_img
            if not vid:
                vid = existing_vid
            cattrs = dict(getattr(child, "attrs", None) or {})
            if img:
                cattrs[SHOT2_PROMPT_ATTR] = img
                cattrs[SHOT2_STATUS_ATTR] = "image_prompt_ready"
                updated += 1
            if vid:
                cattrs[SHOT2_VIDEO_PROMPT_ATTR] = vid
                if not str(getattr(child, "animation_prompt", None) or "").strip():
                    child.animation_prompt = vid
            child.attrs = cattrs
            _flag_attrs(child)
            if getattr(child, "image_prompt", None):
                child.image_prompt = ""
        if children:
            pattrs[SHOT2_STATUS_ATTR] = "skipped"
            parent.attrs = pattrs
            _flag_attrs(parent)
    return updated


def inherit_camera_on_children(frames: list[Any]) -> int:
    """Дети берут движение/набор с родителя; крупность уже из плана шота."""
    updated = 0
    for members in _group_by_parent(frames).values():
        parent = next(
            (m for m in members if _cs(m).get("role") == "vo_parent"),
            None,
        )
        if parent is None:
            continue
        pcs = _cs(parent)
        move = str(pcs.get("движение") or pcs.get("move") or "").strip()
        nab = str(pcs.get("набор") or pcs.get("set") or "").strip()
        if not move and not nab:
            continue
        for child in members:
            if child is parent:
                continue
            extra: dict[str, Any] = {}
            ccs = _cs(child)
            if move and not str(ccs.get("движение") or "").strip():
                extra["движение"] = move
            if nab and not str(ccs.get("набор") or "").strip():
                extra["набор"] = nab
            if extra:
                _set_cs(child, **extra)
                updated += 1
    return updated


async def expand_vo_cells_into_shots(
    session: AsyncSession,
    project: Project,
    frames: list[Frame],
) -> tuple[list[Frame], dict[str, Any]]:
    """Вставить недостающие шоты по кадры[] / эвристике. Уже раскрытые ячейки не трогаем."""
    report: dict[str, Any] = {
        "skipped": False,
        "parents": 0,
        "inserted": 0,
        "repaired_vo": 0,
        "vo_shot_cuts": 0,
        "cells_expanded": 0,
        "cells_already": 0,
        "frames_before": len(frames),
        "frames_after": len(frames),
    }
    work = [f for f in frames if _is_pipeline_frame(f)]
    groups = _group_by_parent(work)
    parents = [
        f
        for f in work
        if not is_shot_child(f)
    ]
    parents.sort(key=lambda f: (f.sort_key is None, f.sort_key or 0.0, f.number or 0))
    inserted = 0
    cells_expanded = 0
    cells_already = 0
    report["repaired_vo"] = repair_split_vo_on_parents(work)

    for parent in reversed(parents):
        members = list(groups.get(str(parent.uuid or ""), []) or [])
        if parent not in members:
            members = [parent, *[m for m in members if m is not parent]]
        members.sort(key=lambda m: int(_cs(m).get("shot_index") or 0) or (m.number or 0))
        original_vo = _cell_full_text(parent, members)
        planned = planned_shots_from_attrs(parent)
        partition = kadry_vo_partition(original_vo, planned)
        if partition:
            need, vo_parts = len(partition), partition
        else:
            need, vo_parts = resolve_shot_plan(original_vo, planned)
        children = [m for m in members if m is not parent]
        have = 1 + len(children)
        parent_uuid = parent.uuid
        total_sec = vo_duration_sec(original_vo, shots=need)
        part_sec = round(total_sec / need, 2)
        start_ts = parent.start_ts
        end_ts = parent.end_ts

        if have < need:
            after_id = children[-1].id if children else parent.id
            for _ in range(need - have):
                child = await insert_frame_after(
                    session, project, after_frame_id=after_id
                )
                children.append(child)
                after_id = child.id
                inserted += 1
            cells_expanded += 1
        else:
            cells_already += 1
            if need <= 1:
                _set_cs(
                    parent,
                    role="vo_parent",
                    parent_uuid=parent_uuid,
                    shot_index=1,
                    shots_in_beat=1,
                    vo_shot=vo_parts[0] if vo_parts else original_vo,
                )
                _apply_shot_meta(parent, planned[0] if planned else None)
                if not parent.duration_seconds:
                    parent.duration_seconds = part_sec
                continue

        group = [parent, *children]
        # лишние дети (have > need) остаются, но план — need слотов
        parent.voiceover_text = original_vo
        for i, fr in enumerate(group):
            if i == 0:
                fr.start_ts = start_ts
                fr.end_ts = end_ts
                fr.voiceover_text = original_vo
            else:
                fr.start_ts = None
                fr.end_ts = None
                if i < need:
                    fr.voiceover_text = ""
            if i < need:
                fr.duration_seconds = part_sec
                _set_cs(
                    fr,
                    role="vo_parent" if i == 0 else "shot",
                    parent_uuid=parent_uuid,
                    shot_index=i + 1,
                    shots_in_beat=need,
                    vo_shot=vo_parts[i] if i < len(vo_parts) else "",
                )
                _apply_shot_meta(fr, planned[i] if i < len(planned) else None)

    await session.flush()
    tail_keys = [f.sort_key or 0.0 for f in work]
    tail = (max(tail_keys) if tail_keys else 0.0) + 1000.0
    for i, fr in enumerate(frames):
        if _is_pipeline_frame(fr):
            continue
        fr.sort_key = tail + i
    await session.flush()
    ordered = await renumber_frames_by_sort_key(session, project)
    report["parents"] = len(parents)
    report["inserted"] = inserted
    report["cells_expanded"] = cells_expanded
    report["cells_already"] = cells_already
    report["skipped"] = inserted == 0
    report["vo_shot_cuts"] = apply_vo_shot_cuts(
        [f for f in ordered if _is_pipeline_frame(f)]
    )
    report["frames_after"] = len(ordered)
    logger.info(
        "[#{}] vo_shot_expand: parents={} inserted={} expanded={} already={} "
        "vo_shot_cuts={} frames {}→{}",
        project.id,
        report["parents"],
        inserted,
        cells_expanded,
        cells_already,
        report["vo_shot_cuts"],
        report["frames_before"],
        report["frames_after"],
    )
    return ordered, report
