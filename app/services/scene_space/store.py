"""DB access for cinematic scene space. State on a shot = plan + deltas, not copies."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.scene_space.errors import DegenerateAxisError
from app.services.scene_space.geom import apply_deltas, axis_side, normalize_pair
from app.services.scene_space.migrate import (
    downgrade_scene_space_schema,
    migrate_scene_space_schema,
)
from app.services.scene_space.models import FrameSpace, SceneSpace

__all__ = [
    "DegenerateAxisError",
    "apply_deltas",
    "axis_side",
    "downgrade_scene_space_schema",
    "get_scene_space",
    "list_frame_spaces",
    "migrate_scene_space_schema",
    "normalize_pair",
    "state_at",
    "upsert_frame_space",
    "upsert_scene_space",
]

_FRAME_COLUMNS = (
    "uuid",
    "scene_id",
    "shot_order",
    "axis_pair",
    "axis_side",
    "screen_pos",
    "screen_dir",
    "shot_size",
    "angle_v",
    "angle_h",
    "beat_role",
    "crossing_method",
    "space_delta_json",
    "reestablish_due",
    "manual",
)

_JSON_FIELDS = frozenset({"screen_pos", "space_delta_json"})


def _dump_json(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)


def _load_json(value: Any) -> Any:
    if value is None or value == "":
        return None
    if isinstance(value, (dict, list)):
        return value
    return json.loads(value)


def _frame_to_dict(row: FrameSpace) -> dict:
    out: dict[str, Any] = {}
    for name in _FRAME_COLUMNS:
        val = getattr(row, name)
        if name in _JSON_FIELDS:
            val = _load_json(val)
        out[name] = val
    return out


async def upsert_scene_space(session: AsyncSession, scene_id: str, space: dict) -> None:
    sid = str(scene_id)
    payload = json.dumps(space, ensure_ascii=False)
    existing = await session.get(SceneSpace, sid)
    if existing is None:
        session.add(SceneSpace(scene_id=sid, space_json=payload))
    else:
        existing.space_json = payload
    await session.flush()


async def upsert_frame_space(session: AsyncSession, row: dict) -> None:
    uuid = row.get("uuid")
    scene_id = row.get("scene_id")
    shot_order = row.get("shot_order")
    if not uuid or not scene_id or shot_order is None:
        raise ValueError("upsert_frame_space requires uuid, scene_id, shot_order")
    payload: dict[str, Any] = {
        "uuid": str(uuid),
        "scene_id": str(scene_id),
        "shot_order": int(shot_order),
        "axis_pair": row.get("axis_pair"),
        "axis_side": row.get("axis_side"),
        "screen_pos": _dump_json(row.get("screen_pos")),
        "screen_dir": row.get("screen_dir"),
        "shot_size": row.get("shot_size"),
        "angle_v": row.get("angle_v"),
        "angle_h": row.get("angle_h"),
        "beat_role": row.get("beat_role"),
        "crossing_method": row.get("crossing_method"),
        "space_delta_json": _dump_json(row.get("space_delta_json")),
        "reestablish_due": int(row.get("reestablish_due") or 0),
        "manual": int(row.get("manual") or 0),
    }
    existing = await session.get(FrameSpace, payload["uuid"])
    if existing is None:
        session.add(FrameSpace(**payload))
    else:
        for key, value in payload.items():
            if key == "uuid":
                continue
            setattr(existing, key, value)
    await session.flush()


async def get_scene_space(session: AsyncSession, scene_id: str) -> dict | None:
    row = await session.get(SceneSpace, str(scene_id))
    if row is None:
        return None
    parsed = json.loads(row.space_json)
    if not isinstance(parsed, dict):
        raise ValueError(f"scenes_space.space_json is not an object: {scene_id}")
    return parsed


async def list_frame_spaces(session: AsyncSession, scene_id: str) -> list[dict]:
    result = await session.execute(
        select(FrameSpace)
        .where(FrameSpace.scene_id == str(scene_id))
        .order_by(FrameSpace.shot_order, FrameSpace.uuid)
    )
    return [_frame_to_dict(row) for row in result.scalars().all()]


async def state_at(session: AsyncSession, scene_id: str, shot_order: int) -> dict:
    """space_json with plan = base plan plus deltas for frames with shot_order <= N."""
    space = await get_scene_space(session, scene_id)
    if space is None:
        raise KeyError(scene_id)
    plan = [dict(item) for item in (space.get("plan") or [])]
    frames = await list_frame_spaces(session, scene_id)
    deltas = []
    for frame in frames:
        if int(frame["shot_order"]) > int(shot_order):
            continue
        delta = frame.get("space_delta_json") or {}
        if isinstance(delta, dict):
            deltas.append(delta)
    out = dict(space)
    out["plan"] = apply_deltas(plan, deltas)
    return out
