"""Ingest live Scene/Frame rows into scenes_space / frames_space.

Overlay only: does not insert Frame rows. Blocking/service shots are a
separate rewrite pass (assemble hook, ``--insert-service``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Entity, Frame, Project, Scene
from app.services.db_v2 import new_frame_uuid
from app.services.scene_space.migrate import migrate_scene_space_schema
from app.services.scene_space.geom import axis_side, normalize_pair
from app.services.scene_space.shot_size import size_from_ru
from app.services.scene_space.store import (
    get_scene_space,
    list_frame_spaces,
    upsert_frame_space,
    upsert_scene_space,
)
from app.services.scene_space.validate import compute_screen_pos_map

_SPACE_ID = re.compile(r"^p(\d+):s(\d+)$")

_ANGLE_H = (
    ("over-the-shoulder", "over-the-shoulder"),
    ("через плечо", "over-the-shoulder"),
    ("rear three-quarter", "rear three-quarter"),
    ("three-quarter", "three-quarter"),
    ("три четверти", "three-quarter"),
    ("from behind", "from behind"),
    ("со спины", "from behind"),
    ("сзади", "from behind"),
    ("profile", "profile"),
    ("профиль", "profile"),
    ("frontal", "frontal"),
    ("фронт", "frontal"),
    ("анфас", "frontal"),
)
_ANGLE_V = (
    ("overhead", "overhead"),
    ("сверху", "overhead"),
    ("slight low", "slight low"),
    ("slight high", "slight high"),
    ("dutch", "dutch"),
    ("low", "low"),
    ("high", "high"),
    ("eye-level", "eye-level"),
    ("уровень глаз", "eye-level"),
)


def space_id_for(project_id: int, scene_pk: int) -> str:
    return f"p{int(project_id)}:s{int(scene_pk)}"


def parse_space_id(scene_id: str) -> tuple[int, int] | None:
    m = _SPACE_ID.match(str(scene_id or "").strip())
    if not m:
        return None
    return int(m.group(1)), int(m.group(2))


def overlay_hash(meaning: str, frames: list[Frame]) -> str:
    payload = {
        "meaning": meaning or "",
        "uuids": [str(f.uuid) for f in frames],
        "sizes": [_krupnost_text(f) for f in frames],
    }
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def default_plan(codes: list[str]) -> tuple[list[dict], list[dict]]:
    """Place bodies on y=2, camera at mid_x,y=0 facing +Y. Side locks to B."""
    ids = [c for c in codes if c and c != "cam"]
    if not ids:
        ids = ["c01", "c02"]
    if len(ids) == 1:
        ids = [ids[0], "look"]
    plan: list[dict] = []
    for i, code in enumerate(ids):
        facing = 90.0 if i == 0 else 270.0
        plan.append({"id": code, "x": float(i) * 2.0, "y": 2.0, "facing": facing})
    mid_x = (plan[0]["x"] + plan[-1]["x"]) / 2.0
    plan.append({"id": "cam", "x": mid_x, "y": 0.0, "facing": 0.0})
    lo, hi = normalize_pair(ids[0], ids[1])
    a = next(p for p in plan if p["id"] == lo)
    b = next(p for p in plan if p["id"] == hi)
    cam = next(p for p in plan if p["id"] == "cam")
    side = axis_side((a["x"], a["y"]), (b["x"], b["y"]), (cam["x"], cam["y"]))
    axes = [{"pair": [lo, hi], "side_locked": side}]
    return plan, axes


def _krupnost_text(frame: Frame) -> str:
    attrs = frame.attrs if isinstance(frame.attrs, dict) else {}
    cs = attrs.get("camera_subdivide")
    cs = cs if isinstance(cs, dict) else {}
    for src in (cs, attrs):
        for key in ("план", "крупность"):
            val = str(src.get(key) or "").strip()
            if val:
                return val
    kadry = attrs.get("кадры")
    if isinstance(kadry, list) and kadry and isinstance(kadry[0], dict):
        for key in ("план", "крупность"):
            val = str(kadry[0].get(key) or "").strip()
            if val:
                return val
    return ""


def _angle_text(frame: Frame) -> str:
    attrs = frame.attrs if isinstance(frame.attrs, dict) else {}
    cs = attrs.get("camera_subdivide")
    cs = cs if isinstance(cs, dict) else {}
    for src in (cs, attrs):
        for key in ("ракурс", "angle"):
            val = str(src.get(key) or "").strip()
            if val:
                return val
    return ""


def _map_shot_size(text: str) -> str:
    try:
        return size_from_ru(text)
    except ValueError:
        return "MS"


def _map_angle_h(text: str) -> str:
    raw = (text or "").casefold()
    for needle, code in _ANGLE_H:
        if needle in raw:
            return code
    return "frontal"


def _map_angle_v(text: str) -> str:
    raw = (text or "").casefold()
    for needle, code in _ANGLE_V:
        if needle in raw:
            return code
    return "eye-level"


def _character_codes(session_entities: list[Entity], frames: list[Frame]) -> list[str]:
    codes = [
        str(e.code).strip()
        for e in session_entities
        if e.code and str(e.type or "") == "character"
    ]
    codes = [c for c in codes if c]
    if codes:
        return codes
    found: list[str] = []
    for fr in frames:
        attrs = fr.attrs if isinstance(fr.attrs, dict) else {}
        raw = attrs.get("characters") or attrs.get("персонажи") or ""
        if isinstance(raw, list):
            parts = [str(x).strip() for x in raw]
        else:
            parts = [p.strip() for p in str(raw).replace(";", ",").split(",")]
        for p in parts:
            if p and p not in found:
                found.append(p)
    return found or ["c01", "c02"]


def _present_ids(codes: list[str], frame: Frame) -> list[str]:
    attrs = frame.attrs if isinstance(frame.attrs, dict) else {}
    raw = attrs.get("characters") or attrs.get("персонажи") or ""
    if isinstance(raw, list):
        parts = [str(x).strip() for x in raw if str(x).strip()]
    else:
        parts = [p.strip() for p in str(raw).replace(";", ",").split(",") if p.strip()]
    bodies = [c for c in codes if c != "look"]
    if parts:
        hit = [p for p in parts if p in bodies]
        if hit:
            return hit
    return bodies[:2]


def _beat_role(order: int, n: int, size: str) -> str:
    if order == 1:
        return "establish"
    if size == "ECU":
        return "insert"
    if size in {"CU", "MCU"} and order == n:
        return "turning_point"
    if size in {"CU", "MCU"} and order > 1:
        return "reaction" if order == n - 1 else "beat"
    return "beat"


async def _load_scene_frames(session: AsyncSession, scene: Scene) -> list[Frame]:
    rows = (
        await session.execute(
            select(Frame)
            .where(Frame.scene_id == scene.id)
            .order_by(Frame.sort_key, Frame.number)
        )
    ).scalars().all()
    return list(rows)


async def _ensure_schema(session: AsyncSession) -> None:
    conn = await session.connection()
    await migrate_scene_space_schema(conn)


async def ingest_scene(
    session: AsyncSession,
    project: Project,
    scene: Scene,
) -> dict[str, Any]:
    """Write overlay space rows for one pipeline Scene. No Frame inserts."""
    await _ensure_schema(session)
    frames = await _load_scene_frames(session, scene)
    sid = space_id_for(project.id, scene.id)
    if not frames:
        return {"scene_id": sid, "frames": 0, "skipped": True, "reason": "no_frames"}

    for fr in frames:
        if not fr.uuid:
            fr.uuid = new_frame_uuid()
    await session.flush()

    meaning = scene.meaning or scene.title or ""
    digest = overlay_hash(meaning, frames)
    existing = await get_scene_space(session, sid)
    if existing and existing.get("overlay_hash") == digest:
        rows = await list_frame_spaces(session, sid)
        if len(rows) == len(frames):
            logger.info("scene_space ingest {}: overlay hash match, skip", sid)
            return {
                "scene_id": sid,
                "frames": len(frames),
                "skipped": True,
                "reason": "unchanged",
            }

    entities = (
        await session.execute(
            select(Entity)
            .where(Entity.project_id == project.id, Entity.type == "character")
            .order_by(Entity.sort_key, Entity.id)
        )
    ).scalars().all()
    codes = _character_codes(list(entities), frames)
    plan, axes = default_plan(codes)
    if existing and existing.get("plan"):
        plan = list(existing["plan"])
        axes = list(existing.get("axes") or axes)

    pair = tuple(axes[0]["pair"]) if axes else normalize_pair(codes[0], codes[1] if len(codes) > 1 else "look")
    lo, hi = normalize_pair(pair[0], pair[1])
    axis_pair = f"{lo}|{hi}"
    by_id = {p["id"]: p for p in plan}
    cam = by_id["cam"]
    a = by_id[lo]
    b = by_id[hi]
    side = axis_side((a["x"], a["y"]), (b["x"], b["y"]), (cam["x"], cam["y"]))
    pos_map = compute_screen_pos_map(plan, lo, hi)
    n = len(frames)
    by_uuid = {r["uuid"]: r for r in (await list_frame_spaces(session, sid))}

    space_doc = {
        "plan": plan,
        "obstacles": list((existing or {}).get("obstacles") or []),
        "axes": [{"pair": [lo, hi], "side_locked": side}],
        "value_charge": (existing or {}).get("value_charge") or {"start": "+", "end": "-"},
        "overlay_hash": digest,
        "input_hash": (existing or {}).get("input_hash") or "",
        "monotony_surface": (existing or {}).get("monotony_surface") or "ground",
    }
    await upsert_scene_space(session, sid, space_doc)

    written = 0
    skipped_manual = 0
    for order, fr in enumerate(frames, start=1):
        prev = by_uuid.get(str(fr.uuid))
        if prev and int(prev.get("manual") or 0) == 1:
            skipped_manual += 1
            continue
        size = _map_shot_size(_krupnost_text(fr))
        ang = _angle_text(fr)
        present = _present_ids([p["id"] for p in plan], fr)
        if len(present) <= 1:
            pid = present[0] if present else lo
            screen_pos = {pid: "C"}
        else:
            screen_pos = {k: pos_map[k] for k in present if k in pos_map} or dict(pos_map)
        row = {
            "uuid": str(fr.uuid),
            "scene_id": sid,
            "shot_order": order,
            "axis_pair": axis_pair,
            "axis_side": side,
            "screen_pos": screen_pos,
            "screen_dir": "none",
            "shot_size": size,
            "angle_v": _map_angle_v(ang),
            "angle_h": _map_angle_h(ang),
            "beat_role": _beat_role(order, n, size),
            "crossing_method": None,
            "space_delta_json": {},
            "reestablish_due": 0,
            "manual": 0,
        }
        await upsert_frame_space(session, row)
        written += 1

    scene.attrs = {**(scene.attrs or {}), "scene_space_id": sid}
    logger.info(
        "scene_space ingest {}: frames={} written={} manual_skip={}",
        sid,
        n,
        written,
        skipped_manual,
    )
    return {
        "scene_id": sid,
        "frames": n,
        "written": written,
        "skipped_manual": skipped_manual,
        "skipped": False,
    }


async def ingest_project(
    session: AsyncSession,
    project: Project,
) -> dict[str, Any]:
    scenes = (
        await session.execute(
            select(Scene).where(Scene.project_id == project.id).order_by(Scene.sort_key, Scene.id)
        )
    ).scalars().all()
    reports: list[dict[str, Any]] = []
    for sc in scenes:
        reports.append(await ingest_scene(session, project, sc))
    return {
        "project_id": project.id,
        "scenes": len(reports),
        "reports": reports,
    }


async def ingest_space_id(session: AsyncSession, scene_id: str) -> dict[str, Any]:
    parsed = parse_space_id(scene_id)
    if parsed is None:
        raise RuntimeError(f"not a live scene_id: {scene_id}")
    pid, sid = parsed
    project = await session.get(Project, pid)
    scene = await session.get(Scene, sid)
    if project is None or scene is None or scene.project_id != pid:
        raise RuntimeError(f"pipeline Scene {scene_id} not found")
    return await ingest_scene(session, project, scene)


async def apply_blocking_after_ingest(
    session: AsyncSession,
    scene_id: str,
    meaning: str,
) -> dict[str, Any]:
    """Assemble-only: overlay already written; rewrite may insert service Frames."""
    from app.services.scene_space.rewrite import rewrite

    return await rewrite(session, scene_id, meaning)
