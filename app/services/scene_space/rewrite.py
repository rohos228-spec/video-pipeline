"""Scene-space rewrite / rebuild / fixture seed (stream 5).

rewrite(meaning) rebuilds bits + shot breakdown. Rows with manual=1 keep
frames_space fields. Idempotent via scenes_space.space_json.input_hash.

If store / blocking / validate are missing in this worktree, schema is created
with raw SQL matching CONTRACTS, and rebuild keeps a valid fixture skeleton.
Does not create store.py.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from loguru import logger
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Frame, Project, ProjectStatus, Scene
from app.services.db_v2 import insert_frame_after, new_frame_uuid

try:
    from app.services.scene_space.errors import DegenerateAxisError
except ImportError:  # stream 1 may not be merged yet

    class DegenerateAxisError(ValueError):
        """cross == 0 — camera on the axis line."""


_REPO_ROOT = Path(__file__).resolve().parents[3]
FIXTURE_DIR = _REPO_ROOT / "tests" / "fixtures" / "scene_space"
FIXTURE_IDS = ("fix:dialogue", "fix:cross", "fix:turn")

_SHOT_HASH_KEYS = (
    "shot_order",
    "shot_size",
    "beat_role",
    "axis_pair",
    "axis_side",
    "screen_pos",
    "screen_dir",
    "angle_v",
    "angle_h",
    "crossing_method",
    "reestablish_due",
    "space_delta_json",
)

_CREATE_SCENES = """
CREATE TABLE IF NOT EXISTS scenes_space (
    scene_id TEXT PRIMARY KEY,
    space_json TEXT NOT NULL
)
"""

_CREATE_FRAMES = """
CREATE TABLE IF NOT EXISTS frames_space (
    uuid TEXT PRIMARY KEY,
    scene_id TEXT NOT NULL,
    shot_order INTEGER NOT NULL,
    axis_pair TEXT,
    axis_side TEXT,
    screen_pos TEXT,
    screen_dir TEXT,
    shot_size TEXT,
    angle_v TEXT,
    angle_h TEXT,
    beat_role TEXT,
    crossing_method TEXT,
    space_delta_json TEXT,
    reestablish_due INTEGER NOT NULL DEFAULT 0,
    manual INTEGER NOT NULL DEFAULT 0 CHECK (manual IN (0,1)),
    FOREIGN KEY (scene_id) REFERENCES scenes_space(scene_id)
)
"""

_CREATE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_frames_space_scene
ON frames_space (scene_id, shot_order)
"""

_EMPTY_CHANGED = {"inserted": [], "updated": [], "skipped": []}

_PROMPT_NEG = "extra limbs, watermark, readable lettering"

# ---------------------------------------------------------------------------
# Geometry (CONTRACTS / CANON §2.3–2.5) — used when store.geom is absent
# ---------------------------------------------------------------------------


def normalize_pair(a: str, b: str) -> tuple[str, str]:
    try:
        from app.services.scene_space.store import normalize_pair as _np

        return _np(a, b)
    except ImportError:
        lo, hi = sorted((str(a), str(b)))
        return lo, hi


def axis_side(a_xy: Sequence[float], b_xy: Sequence[float], cam_xy: Sequence[float]) -> str:
    try:
        from app.services.scene_space.store import axis_side as _as

        return _as(a_xy, b_xy, cam_xy)
    except ImportError:
        ax = float(b_xy[0]) - float(a_xy[0])
        ay = float(b_xy[1]) - float(a_xy[1])
        tx = float(cam_xy[0]) - float(a_xy[0])
        ty = float(cam_xy[1]) - float(a_xy[1])
        cross = ax * ty - ay * tx
        if abs(cross) <= 1e-9:
            raise DegenerateAxisError("cross == 0")
        return "A" if cross > 0 else "B"


def apply_deltas(plan: list[dict], deltas_in_order: list[dict]) -> list[dict]:
    try:
        from app.services.scene_space.store import apply_deltas as _ad

        return _ad(plan, deltas_in_order)
    except ImportError:
        by_id: dict[str, dict] = {str(p["id"]): dict(p) for p in plan}
        for delta in deltas_in_order:
            if not delta:
                continue
            for key, val in delta.items():
                if key in {"turn", "monotony", "turn_subject"}:
                    continue
                if isinstance(val, dict) and val.get("_remove"):
                    by_id.pop(str(key), None)
                    continue
                kid = str(key)
                if kid not in by_id:
                    by_id[kid] = {"id": kid}
                if isinstance(val, dict):
                    by_id[kid].update(
                        {k: v for k, v in val.items() if not str(k).startswith("_")}
                    )
        return list(by_id.values())


def facing_towards(frm: Sequence[float], to: Sequence[float]) -> float:
    dx = float(to[0]) - float(frm[0])
    dy = float(to[1]) - float(frm[1])
    if abs(dx) <= 1e-12 and abs(dy) <= 1e-12:
        return 0.0
    return round(math.degrees(math.atan2(dx, dy)) % 360.0, 2)


def _xy(plan: Sequence[Mapping], oid: str) -> tuple[float, float]:
    for p in plan:
        if str(p.get("id")) == oid:
            return float(p["x"]), float(p["y"])
    raise KeyError(oid)


def _cam(plan: Sequence[Mapping]) -> dict:
    for p in plan:
        if str(p.get("id")) == "cam":
            return dict(p)
    raise KeyError("cam")


def camera_right_xy(facing_deg: float) -> tuple[float, float]:
    """Facing 0 = +Y; clockwise. Camera-right = facing + 90° = +X at facing 0."""
    rad = math.radians(float(facing_deg))
    return math.cos(rad), -math.sin(rad)


def screen_pos_for(
    plan: Sequence[Mapping],
    pair: tuple[str, str],
    members: Sequence[str],
) -> dict[str, str]:
    cam = _cam(plan)
    rx, ry = camera_right_xy(float(cam.get("facing") or 0.0))
    cx, cy = float(cam["x"]), float(cam["y"])
    out: dict[str, str] = {}
    scored: list[tuple[float, str]] = []
    for mid in members:
        x, y = _xy(plan, mid)
        scored.append(((x - cx) * rx + (y - cy) * ry, mid))
    if len(scored) == 1:
        out[scored[0][1]] = "C"
        return out
    scored.sort(key=lambda t: (t[0], t[1]))
    n = len(scored)
    for i, (_proj, mid) in enumerate(scored):
        if n == 2:
            out[mid] = "L" if i == 0 else "R"
        elif i == 0:
            out[mid] = "L"
        elif i == n - 1:
            out[mid] = "R"
        else:
            out[mid] = "C"
    return out


def south_cam(az_deg: float, mid: tuple[float, float] = (2.0, 2.0), r: float = 4.2) -> dict:
    """Camera on the south arc (y < mid.y). az 0 = due south, + = toward +X."""
    rad = math.radians(float(az_deg))
    x = mid[0] + r * math.sin(rad)
    y = mid[1] - r * math.cos(rad)
    facing = facing_towards((x, y), mid)
    return {"id": "cam", "x": round(x, 3), "y": round(y, 3), "facing": facing}


def north_cam(mid: tuple[float, float] = (2.0, 2.0), r: float = 4.5) -> dict:
    x, y = mid[0], mid[1] + r
    return {"id": "cam", "x": round(x, 3), "y": round(y, 3), "facing": facing_towards((x, y), mid)}


def action_class(beat_role: str) -> str:
    role = (beat_role or "beat").strip()
    if role in {"reaction"}:
        return "reaction"
    if role in {"insert"}:
        return "insert"
    if role in {"establish", "reestablish"}:
        return "establish"
    return "action"


# ---------------------------------------------------------------------------
# Canonical hash
# ---------------------------------------------------------------------------


def canonical_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def input_hash(meaning: str, bits: list[dict], shots: list[dict]) -> str:
    payload = {
        "bits": [{"i": b.get("i"), "meaning_tag": b.get("meaning_tag")} for b in bits],
        "meaning": meaning,
        "uuids": [
            str(s.get("uuid"))
            for s in shots
            if int(s.get("manual") or 0) != 1 and not _is_service_row(s)
        ],
    }
    return hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()


def _shot_hash_view(row: Mapping[str, Any]) -> dict[str, Any]:
    view: dict[str, Any] = {}
    for key in _SHOT_HASH_KEYS:
        val = row.get(key)
        if key == "screen_pos":
            val = _as_json_text(val)
        elif key == "space_delta_json":
            val = _as_json_obj(val)
        elif key == "crossing_method":
            val = val or None
        elif key == "reestablish_due":
            val = int(val or 0)
        view[key] = val
    return view


def _as_json_text(val: Any) -> str:
    if val is None:
        return "{}"
    if isinstance(val, str):
        try:
            parsed = json.loads(val)
        except json.JSONDecodeError:
            return val
        return canonical_dumps(parsed)
    return canonical_dumps(val)


def _as_json_obj(val: Any) -> dict:
    if val is None:
        return {}
    if isinstance(val, str):
        if not val.strip():
            return {}
        return json.loads(val)
    if isinstance(val, dict):
        return val
    return {}


def _is_service_row(row: Mapping[str, Any]) -> bool:
    delta = _as_json_obj(row.get("space_delta_json"))
    if delta.get("service_shot"):
        return True
    return False


def derive_bits(meaning: str, rows: Sequence[Mapping[str, Any]]) -> list[dict]:
    tag = hashlib.sha256((meaning or "").encode("utf-8")).hexdigest()[:12]
    bits: list[dict] = []
    i = 0
    for row in sorted(rows, key=lambda r: int(r.get("shot_order") or 0)):
        if _is_service_row(row):
            continue
        i += 1
        bits.append({"beat_role": "beat", "meaning_tag": tag, "i": i})
    return bits


# ---------------------------------------------------------------------------
# Schema + store fallback (raw SQL, not store.py)
# ---------------------------------------------------------------------------


async def ensure_scene_space_schema(conn) -> None:
    try:
        from app.services.scene_space.store import migrate_scene_space_schema

        await migrate_scene_space_schema(conn)
        return
    except ImportError:
        pass
    await conn.exec_driver_sql(_CREATE_SCENES)
    await conn.exec_driver_sql(_CREATE_FRAMES)
    await conn.exec_driver_sql(_CREATE_INDEX)


async def _upsert_scene_space(session: AsyncSession, scene_id: str, space: dict) -> None:
    try:
        from app.services.scene_space.store import upsert_scene_space

        await upsert_scene_space(session, scene_id, space)
        return
    except ImportError:
        pass
    payload = canonical_dumps(space)
    await session.execute(
        text(
            "INSERT INTO scenes_space(scene_id, space_json) VALUES (:id, :js) "
            "ON CONFLICT(scene_id) DO UPDATE SET space_json = excluded.space_json"
        ),
        {"id": scene_id, "js": payload},
    )


async def _upsert_frame_space(session: AsyncSession, row: dict) -> None:
    try:
        from app.services.scene_space.store import upsert_frame_space

        await upsert_frame_space(session, row)
        return
    except ImportError:
        pass
    params = _row_sql_params(row)
    await session.execute(
        text(
            """
            INSERT INTO frames_space (
                uuid, scene_id, shot_order, axis_pair, axis_side, screen_pos,
                screen_dir, shot_size, angle_v, angle_h, beat_role,
                crossing_method, space_delta_json, reestablish_due, manual
            ) VALUES (
                :uuid, :scene_id, :shot_order, :axis_pair, :axis_side, :screen_pos,
                :screen_dir, :shot_size, :angle_v, :angle_h, :beat_role,
                :crossing_method, :space_delta_json, :reestablish_due, :manual
            )
            ON CONFLICT(uuid) DO UPDATE SET
                scene_id = excluded.scene_id,
                shot_order = excluded.shot_order,
                axis_pair = excluded.axis_pair,
                axis_side = excluded.axis_side,
                screen_pos = excluded.screen_pos,
                screen_dir = excluded.screen_dir,
                shot_size = excluded.shot_size,
                angle_v = excluded.angle_v,
                angle_h = excluded.angle_h,
                beat_role = excluded.beat_role,
                crossing_method = excluded.crossing_method,
                space_delta_json = excluded.space_delta_json,
                reestablish_due = excluded.reestablish_due,
                manual = frames_space.manual
            """
        ),
        params,
    )


async def _get_scene_space(session: AsyncSession, scene_id: str) -> dict | None:
    try:
        from app.services.scene_space.store import get_scene_space

        return await get_scene_space(session, scene_id)
    except ImportError:
        pass
    res = await session.execute(
        text("SELECT space_json FROM scenes_space WHERE scene_id = :id"),
        {"id": scene_id},
    )
    hit = res.first()
    if hit is None:
        return None
    raw = hit[0]
    return json.loads(raw) if isinstance(raw, str) else raw


async def _list_frame_spaces(session: AsyncSession, scene_id: str) -> list[dict]:
    try:
        from app.services.scene_space.store import list_frame_spaces

        rows = await list_frame_spaces(session, scene_id)
        return [_normalize_db_row(r) for r in rows]
    except ImportError:
        pass
    res = await session.execute(
        text(
            "SELECT uuid, scene_id, shot_order, axis_pair, axis_side, screen_pos, "
            "screen_dir, shot_size, angle_v, angle_h, beat_role, crossing_method, "
            "space_delta_json, reestablish_due, manual FROM frames_space "
            "WHERE scene_id = :id ORDER BY shot_order"
        ),
        {"id": scene_id},
    )
    keys = (
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
    return [_normalize_db_row(dict(zip(keys, tup, strict=True))) for tup in res.fetchall()]


def _normalize_db_row(row: Mapping[str, Any]) -> dict:
    out = dict(row)
    out["shot_order"] = int(out.get("shot_order") or 0)
    out["reestablish_due"] = int(out.get("reestablish_due") or 0)
    out["manual"] = int(out.get("manual") or 0)
    if out.get("crossing_method") == "":
        out["crossing_method"] = None
    return out


def _row_sql_params(row: Mapping[str, Any]) -> dict:
    return {
        "uuid": row["uuid"],
        "scene_id": row["scene_id"],
        "shot_order": int(row["shot_order"]),
        "axis_pair": row.get("axis_pair"),
        "axis_side": row.get("axis_side"),
        "screen_pos": _as_json_text(row.get("screen_pos")),
        "screen_dir": row.get("screen_dir") or "none",
        "shot_size": row["shot_size"],
        "angle_v": row.get("angle_v"),
        "angle_h": row.get("angle_h"),
        "beat_role": row.get("beat_role"),
        "crossing_method": row.get("crossing_method") or None,
        "space_delta_json": canonical_dumps(_as_json_obj(row.get("space_delta_json"))),
        "reestablish_due": int(row.get("reestablish_due") or 0),
        "manual": int(row.get("manual") or 0),
    }


def _space_json_core(space: Mapping[str, Any], *, meaning_hash_bits: dict) -> dict:
    """Keep frozen CONTRACTS keys; extra hash bookkeeping stays inside input_hash."""
    return {
        "axes": space.get("axes") or [],
        "input_hash": meaning_hash_bits["input_hash"],
        "monotony_surface": space.get("monotony_surface") or "ground",
        "obstacles": space.get("obstacles") or [],
        "plan": space.get("plan") or [],
        "value_charge": space.get("value_charge") or {"start": "+", "end": "-"},
    }


# ---------------------------------------------------------------------------
# Isolated DB (never production data/state.db)
# ---------------------------------------------------------------------------


def default_sandbox_db() -> Path:
    return _REPO_ROOT / "tasks" / "out" / "scene_space.db"


@asynccontextmanager
async def isolated_session(db_path: Path | str) -> AsyncIterator[AsyncSession]:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path.resolve().as_posix()}"
    engine = create_async_engine(url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_scene_space_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


@asynccontextmanager
async def memory_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await ensure_scene_space_schema(conn)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
    await engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _fid(kind: str, n: int) -> str:
    prefixes = {"dialogue": "d1a1", "cross": "c205", "turn": "f3f1"}
    return f"{prefixes[kind]}{n:020x}"


def _prompt(body: str) -> str:
    return f"PROMPT: {body}\nNEGATIVE PROMPT: {_PROMPT_NEG}"


def _pair_ids() -> tuple[str, str]:
    return normalize_pair("c01", "c02")


def _base_plan(cam: dict | None = None) -> list[dict]:
    c = cam or south_cam(-70.0)
    return [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 90.0},
        {"id": "c02", "x": 4.0, "y": 2.0, "facing": 270.0},
        {"id": "cam", "x": c["x"], "y": c["y"], "facing": c["facing"]},
    ]


def _obstacles() -> list[dict]:
    return [{"id": "desk1", "x": 1.6, "y": 2.15, "w": 1.2, "h": 0.35}]


def _axes(side: str) -> list[dict]:
    lo, hi = _pair_ids()
    return [{"pair": [lo, hi], "side_locked": side}]


def _assemble_frames(
    *,
    kind: str,
    scene_id: str,
    shots: list[dict],
    plan0: list[dict],
    voice_stub: str,
) -> list[dict]:
    lo, hi = _pair_ids()
    pair_s = f"{lo}|{hi}"
    acc_plan = [dict(p) for p in plan0]
    deltas_acc: list[dict] = []
    frames: list[dict] = []
    for i, spec in enumerate(shots, start=1):
        delta = dict(spec.get("delta") or {})
        members = list(spec["members"])
        bodies = len(members)
        mon = {
            "action_class": action_class(spec["beat_role"]),
            "bodies": bodies,
            "surface": "ground",
        }
        delta.setdefault("monotony", mon)
        if spec.get("turn_subject"):
            delta["turn"] = {"subject": spec["turn_subject"]}
        deltas_acc.append(delta)
        acc_plan = apply_deltas(plan0, deltas_acc)
        cam = _cam(acc_plan)
        a_xy = _xy(acc_plan, lo)
        b_xy = _xy(acc_plan, hi)
        side = axis_side(a_xy, b_xy, (float(cam["x"]), float(cam["y"])))
        spos = screen_pos_for(acc_plan, (lo, hi), members)
        uid = _fid(kind, i)
        frames.append(
            {
                "uuid": uid,
                "number": i,
                "voiceover_text": f"{voice_stub} {i}.",
                "image_prompt": _prompt(spec["prompt"]),
                "animation_prompt": _prompt(f"slow hold, {spec['prompt']}"),
                "attrs": {"accent": spec["accent"]},
                "space": {
                    "shot_order": i,
                    "axis_pair": pair_s,
                    "axis_side": side,
                    "screen_pos": spos,
                    "screen_dir": spec["screen_dir"],
                    "shot_size": spec["shot_size"],
                    "angle_v": spec["angle_v"],
                    "angle_h": spec["angle_h"],
                    "beat_role": spec["beat_role"],
                    "crossing_method": spec.get("crossing_method"),
                    "space_delta_json": delta,
                    "reestablish_due": int(spec.get("reestablish_due") or 0),
                    "manual": int(spec.get("manual") or 0),
                },
            }
        )
    return frames


def build_dialogue_fixture() -> dict:
    """Two-shot coverage: master + single each + reaction; 3+ sizes; unique accents."""
    az = [-70, 70, -50, 50, -30, 30, -10, 10, -60, 55]
    cams = [south_cam(a) for a in az]
    plan0 = _base_plan(cams[0])
    shots = [
        dict(
            shot_size="EWS",
            beat_role="establish",
            members=["c01", "c02"],
            accent="lobby",
            screen_dir="none",
            angle_h="frontal",
            angle_v="eye-level",
            prompt="archive lobby, two clerks at a steel desk, cold window",
            delta={},
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="steel-desk",
            screen_dir="none",
            angle_h="profile",
            angle_v="slight low",
            prompt="full figures at the desk, folder closed between them",
            delta={"cam": {k: cams[1][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MS",
            beat_role="beat",
            members=["c01"],
            accent="folder",
            screen_dir="none",
            angle_h="over-the-shoulder",
            angle_v="eye-level",
            prompt="clerk c01 waist-up, folder in both hands",
            delta={"cam": {k: cams[2][k] for k in ("x", "y", "facing")}},
            manual=1,
        ),
        dict(
            shot_size="CU",
            beat_role="beat",
            members=["c02"],
            accent="pupils",
            screen_dir="none",
            angle_h="rear three-quarter",
            angle_v="slight high",
            prompt="clerk c02 face, eyes on the folder",
            delta={"cam": {k: cams[3][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="ECU",
            beat_role="insert",
            members=["c02"],
            accent="clasp",
            screen_dir="none",
            angle_h="frontal",
            angle_v="high",
            prompt="metal folder clasp, fingers on the latch",
            delta={"cam": {k: cams[4][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MCU",
            beat_role="reaction",
            members=["c01"],
            accent="breath",
            screen_dir="none",
            angle_h="profile",
            angle_v="eye-level",
            prompt="c01 head and shoulders, held breath",
            delta={"cam": {k: cams[5][k] for k in ("x", "y", "facing")}},
            reestablish_due=1,
        ),
        dict(
            shot_size="WS",
            beat_role="insert",
            members=["c01", "c02"],
            accent="rain-wide",
            screen_dir="none",
            angle_h="from behind",
            angle_v="slight low",
            prompt="wide smash to the room, rain on the glass",
            delta={"cam": {k: cams[6][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MWS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="window-mix",
            screen_dir="none",
            angle_h="three-quarter",
            angle_v="eye-level",
            prompt="knees-up two-shot, window light on the desk",
            delta={"cam": {k: cams[7][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MCU",
            beat_role="turning_point",
            members=["c02"],
            accent="stamp",
            screen_dir="none",
            angle_h="frontal",
            angle_v="dutch",
            prompt="c02 presses a stamp, charge flips",
            delta={"cam": {k: cams[8][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="ECU",
            beat_role="insert",
            members=["c02"],
            accent="wax-seal",
            screen_dir="none",
            angle_h="over-the-shoulder",
            angle_v="slight high",
            prompt="wax seal on the folder, cracked edge",
            delta={"cam": {k: cams[9][k] for k in ("x", "y", "facing")}},
        ),
    ]
    frames = _assemble_frames(
        kind="dialogue",
        scene_id="fix:dialogue",
        shots=shots,
        plan0=plan0,
        voice_stub="Folder talk",
    )
    side0 = frames[0]["space"]["axis_side"]
    return {
        "scene_id": "fix:dialogue",
        "meaning": "Two clerks argue over a folder; the winner then loses it.",
        "value_charge": {"start": "+", "end": "-"},
        "monotony_surface": "ground",
        "plan": plan0,
        "obstacles": _obstacles(),
        "axes": _axes(side0),
        "project": {"slug": "fix-dialogue", "topic": "dialogue fixture"},
        "scene": {"title": "folder dispute", "place": "archive"},
        "frames": frames,
    }


def build_cross_fixture() -> dict:
    """Walk across axis + reestablish + crossing_method (camera to far bank)."""
    az = [-70, 65, -48, 48, -28, 28, -8, 12]
    cams = [south_cam(a) for a in az]
    north = north_cam()
    plan0 = _base_plan(cams[0])
    xs = [4.0, 4.22, 4.45, 4.68, 4.9, 6.5]
    shots = [
        dict(
            shot_size="EWS",
            beat_role="establish",
            members=["c01", "c02"],
            accent="street-master",
            screen_dir="L→R",
            angle_h="frontal",
            angle_v="eye-level",
            prompt="street two-shot, c02 about to walk right",
            delta={},
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="stride",
            screen_dir="L→R",
            angle_h="profile",
            angle_v="slight low",
            prompt="c02 takes a step along the curb",
            delta={
                "cam": {k: cams[1][k] for k in ("x", "y", "facing")},
                "c02": {"x": xs[1], "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="MS",
            beat_role="beat",
            members=["c02"],
            accent="shoulder",
            screen_dir="L→R",
            angle_h="over-the-shoulder",
            angle_v="eye-level",
            prompt="c02 waist-up walking",
            delta={
                "cam": {k: cams[2][k] for k in ("x", "y", "facing")},
                "c02": {"x": xs[2], "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="CU",
            beat_role="beat",
            members=["c02"],
            accent="boot",
            screen_dir="L→R",
            angle_h="rear three-quarter",
            angle_v="slight high",
            prompt="c02 face while walking",
            delta={
                "cam": {k: cams[3][k] for k in ("x", "y", "facing")},
                "c02": {"x": xs[3], "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="MS",
            beat_role="beat",
            members=["c02"],
            accent="mid-walk",
            screen_dir="L→R",
            angle_h="three-quarter",
            angle_v="eye-level",
            prompt="c02 mid-stride, still on the locked side",
            delta={
                "cam": {k: cams[4][k] for k in ("x", "y", "facing")},
                "c02": {"x": xs[4], "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="axis-cross-step",
            screen_dir="L→R",
            angle_h="from behind",
            angle_v="slight low",
            prompt="c02 covers more than a meter, geography shifts",
            delta={
                "cam": {k: cams[5][k] for k in ("x", "y", "facing")},
                "c02": {"x": xs[5], "y": 2.0, "facing": 90.0},
            },
            reestablish_due=1,
        ),
        dict(
            shot_size="EWS",
            beat_role="reestablish",
            members=["c01", "c02"],
            accent="geo-wide",
            screen_dir="none",
            angle_h="frontal",
            angle_v="eye-level",
            prompt="wide geography after the move, same locked side",
            delta={"cam": {k: cams[6][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="hold",
            screen_dir="none",
            angle_h="profile",
            angle_v="slight high",
            prompt="hold the pair before the bank change",
            delta={"cam": {k: cams[7][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="EWS",
            beat_role="reestablish",
            members=["c01", "c02"],
            accent="far-bank",
            screen_dir="none",
            angle_h="from behind",
            angle_v="slight low",
            prompt="new bank of the street, camera across the axis",
            delta={"cam": {k: north[k] for k in ("x", "y", "facing")}},
            crossing_method="reestablish",
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="settle",
            screen_dir="none",
            angle_h="three-quarter",
            angle_v="eye-level",
            prompt="pair settled on the new side of the axis",
            delta={"cam": {k: last[k] for k in ("x", "y", "facing")}},
        ),
    ]
    # last cam must stay on the NEW side (north). south_cam would silently recross.
    shots[-1]["delta"] = {
        "cam": {
            "x": round(north["x"] + 1.4, 3),
            "y": round(north["y"] - 0.2, 3),
            "facing": facing_towards((north["x"] + 1.4, north["y"] - 0.2), (2.0, 2.0)),
        }
    }
    frames = _assemble_frames(
        kind="cross",
        scene_id="fix:cross",
        shots=shots,
        plan0=plan0,
        voice_stub="Crossing",
    )
    side0 = frames[0]["space"]["axis_side"]
    return {
        "scene_id": "fix:cross",
        "meaning": "c02 walks the curb; geography is reset; camera recrosses the axis.",
        "value_charge": {"start": "+", "end": "-"},
        "monotony_surface": "ground",
        "plan": plan0,
        "obstacles": [{"id": "curb1", "x": 3.0, "y": 1.2, "w": 4.0, "h": 0.2}],
        "axes": _axes(side0),
        "project": {"slug": "fix-cross", "topic": "cross fixture"},
        "scene": {"title": "axis walk", "place": "street"},
        "frames": frames,
    }


def build_turn_fixture() -> dict:
    """Physical turn shot + smash insert (MS→ECU)."""
    az = [-70, 68, -52, 52, -32, 32, -12, 12, -58, 50]
    cams = [south_cam(a) for a in az]
    plan0 = _base_plan(cams[0])
    shots = [
        dict(
            shot_size="EWS",
            beat_role="establish",
            members=["c01", "c02"],
            accent="yard",
            screen_dir="L→R",
            angle_h="frontal",
            angle_v="eye-level",
            prompt="yard, c01 walking right, c02 waiting",
            delta={},
        ),
        dict(
            shot_size="FS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="walk-out",
            screen_dir="L→R",
            angle_h="profile",
            angle_v="slight low",
            prompt="c01 full figure walking right",
            delta={
                "cam": {k: cams[1][k] for k in ("x", "y", "facing")},
                "c01": {"x": 0.4, "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="MS",
            beat_role="beat",
            members=["c01"],
            accent="torso",
            screen_dir="L→R",
            angle_h="over-the-shoulder",
            angle_v="eye-level",
            prompt="c01 waist-up still going right",
            delta={
                "cam": {k: cams[2][k] for k in ("x", "y", "facing")},
                "c01": {"x": 0.7, "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="ECU",
            beat_role="insert",
            members=["c01"],
            accent="smash-heel",
            screen_dir="L→R",
            angle_h="rear three-quarter",
            angle_v="high",
            prompt="smash insert of the heel striking grit",
            delta={"cam": {k: cams[3][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MCU",
            beat_role="reaction",
            members=["c02"],
            accent="flinch",
            screen_dir="L→R",
            angle_h="three-quarter",
            angle_v="eye-level",
            prompt="c02 flinch at the sound",
            delta={"cam": {k: cams[4][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MWS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="path",
            screen_dir="L→R",
            angle_h="from behind",
            angle_v="slight low",
            prompt="path two-shot before the pivot",
            delta={
                "cam": {k: cams[5][k] for k in ("x", "y", "facing")},
                "c01": {"x": 1.0, "y": 2.0, "facing": 90.0},
            },
        ),
        dict(
            shot_size="MCU",
            beat_role="insert",
            members=["c01"],
            accent="pivot",
            screen_dir="R→L",
            angle_h="frontal",
            angle_v="dutch",
            prompt="c01 turns in place, facing reverses",
            delta={
                "cam": {k: cams[6][k] for k in ("x", "y", "facing")},
                "c01": {"x": 1.0, "y": 2.0, "facing": 270.0},
            },
            turn_subject="c01",
        ),
        dict(
            shot_size="ECU",
            beat_role="turning_point",
            members=["c01"],
            accent="after-face",
            screen_dir="R→L",
            angle_h="profile",
            angle_v="slight high",
            prompt="eyes after the turn, charge flips",
            delta={"cam": {k: cams[7][k] for k in ("x", "y", "facing")}},
        ),
        dict(
            shot_size="MCU",
            beat_role="beat",
            members=["c01"],
            accent="reverse-step",
            screen_dir="R→L",
            angle_h="over-the-shoulder",
            angle_v="eye-level",
            prompt="c01 steps back left after the turn",
            delta={
                "cam": {k: cams[8][k] for k in ("x", "y", "facing")},
                "c01": {"x": 0.5, "y": 2.0, "facing": 270.0},
            },
        ),
        dict(
            shot_size="MWS",
            beat_role="beat",
            members=["c01", "c02"],
            accent="stop-mark",
            screen_dir="R→L",
            angle_h="three-quarter",
            angle_v="slight low",
            prompt="both figures after the reverse, stop mark on the grit",
            delta={"cam": {k: cams[9][k] for k in ("x", "y", "facing")}},
        ),
    ]
    frames = _assemble_frames(
        kind="turn",
        scene_id="fix:turn",
        shots=shots,
        plan0=plan0,
        voice_stub="Turn",
    )
    side0 = frames[0]["space"]["axis_side"]
    return {
        "scene_id": "fix:turn",
        "meaning": "c01 walks right, turns in the frame, then walks left.",
        "value_charge": {"start": "+", "end": "-"},
        "monotony_surface": "ground",
        "plan": plan0,
        "obstacles": [{"id": "post1", "x": -0.4, "y": 1.0, "w": 0.2, "h": 0.2}],
        "axes": _axes(side0),
        "project": {"slug": "fix-turn", "topic": "turn fixture"},
        "scene": {"title": "pivot", "place": "yard"},
        "frames": frames,
    }


_BUILDERS = {
    "fix:dialogue": build_dialogue_fixture,
    "fix:cross": build_cross_fixture,
    "fix:turn": build_turn_fixture,
}

_FILE_FOR_ID = {
    "fix:dialogue": "dialogue.json",
    "fix:cross": "cross.json",
    "fix:turn": "turn.json",
}


def load_fixture(scene_id: str) -> dict:
    name = _FILE_FOR_ID[scene_id]
    path = FIXTURE_DIR / name
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return _BUILDERS[scene_id]()


def dump_fixture_files(directory: Path | None = None) -> list[Path]:
    directory = directory or FIXTURE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for scene_id, builder in _BUILDERS.items():
        path = directory / _FILE_FOR_ID[scene_id]
        path.write_text(
            json.dumps(builder(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(path)
    return written


def fixture_frame_counts(fx: dict | None = None) -> dict[str, int]:
    out: dict[str, int] = {}
    for sid in FIXTURE_IDS:
        data = fx[sid] if isinstance(fx, dict) and sid in (fx or {}) else load_fixture(sid)
        out[sid] = len(data["frames"])
    return out


# ---------------------------------------------------------------------------
# Seed
# ---------------------------------------------------------------------------


def _space_from_fixture(data: dict, *, meaning: str, rows: list[dict]) -> dict:
    bits = derive_bits(meaning, rows)
    non_manual = [r for r in rows if int(r.get("manual") or 0) != 1]
    return {
        "plan": data["plan"],
        "obstacles": data.get("obstacles") or [],
        "axes": data.get("axes") or [],
        "value_charge": data.get("value_charge") or {"start": "+", "end": "-"},
        "monotony_surface": data.get("monotony_surface") or "ground",
        "input_hash": input_hash(meaning, bits, non_manual),
    }


async def seed_fixture(session: AsyncSession, data: dict | str | Path) -> dict:
    """Insert Project/Scene/Frame + scenes_space/frames_space. Isolated DB only."""
    if not isinstance(data, dict):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    scene_id = str(data["scene_id"])
    existing = await _get_scene_space(session, scene_id)
    if existing is not None:
        rows = await _list_frame_spaces(session, scene_id)
        return {
            "scene_id": scene_id,
            "seeded": False,
            "frames": len(rows),
            "changed": dict(_EMPTY_CHANGED),
        }

    slug = data["project"]["slug"]
    found = (
        await session.execute(select(Project).where(Project.slug == slug))
    ).scalar_one_or_none()
    if found is None:
        proj = Project(
            slug=slug,
            topic=data["project"]["topic"],
            status=ProjectStatus.new,
            hero_mode="auto",
        )
        session.add(proj)
        await session.flush()
    else:
        proj = found

    sc = Scene(
        project_id=proj.id,
        sort_key=1.0,
        title=data["scene"].get("title"),
        place=data["scene"].get("place"),
        meaning=data["meaning"],
        scene_type="fixture",
        attrs={"scene_space_id": scene_id},
    )
    session.add(sc)
    await session.flush()

    rows_for_hash: list[dict] = []
    inserted: list[str] = []
    for spec in data["frames"]:
        space = dict(spec["space"])
        uid = str(spec["uuid"])
        fr = Frame(
            project_id=proj.id,
            scene_id=sc.id,
            number=int(spec["number"]),
            voiceover_text=spec.get("voiceover_text") or "",
            meaning=data["meaning"],
            image_prompt=spec.get("image_prompt"),
            animation_prompt=spec.get("animation_prompt"),
            attrs=dict(spec.get("attrs") or {}),
            uuid=uid,
            sort_key=float(spec["number"]) * 10.0,
        )
        session.add(fr)
        await session.flush()
        row = {
            "uuid": uid,
            "scene_id": scene_id,
            **space,
        }
        rows_for_hash.append(row)
        inserted.append(uid)

    await session.flush()
    space_doc = _space_from_fixture(data, meaning=data["meaning"], rows=rows_for_hash)
    await _upsert_scene_space(session, scene_id, space_doc)
    for row in rows_for_hash:
        await _upsert_frame_space(session, row)

    sc.attrs = {
        **dict(sc.attrs or {}),
        "scene_space_id": scene_id,
        "bits": derive_bits(data["meaning"], rows_for_hash),
    }
    await session.flush()
    logger.info(
        "scene_space seed {}: inserted={}",
        scene_id,
        len(inserted),
    )
    return {
        "scene_id": scene_id,
        "seeded": True,
        "frames": len(inserted),
        "changed": {"inserted": inserted, "updated": [], "skipped": []},
    }


async def seed_all_fixtures(session: AsyncSession) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sid in FIXTURE_IDS:
        out[sid] = await seed_fixture(session, load_fixture(sid))
    return out


# ---------------------------------------------------------------------------
# rewrite / rebuild
# ---------------------------------------------------------------------------


async def _pipeline_project_for(session: AsyncSession, scene_id: str) -> tuple[Project, Scene]:
    rows = await _list_frame_spaces(session, scene_id)
    if not rows:
        raise RuntimeError(f"scene {scene_id} has no frames_space rows — seed first")
    fr = (
        await session.execute(select(Frame).where(Frame.uuid == rows[0]["uuid"]))
    ).scalar_one_or_none()
    if fr is None or fr.scene_id is None:
        raise RuntimeError(f"pipeline Frame missing for {scene_id}")
    sc = await session.get(Scene, fr.scene_id)
    proj = await session.get(Project, fr.project_id)
    if sc is None or proj is None:
        raise RuntimeError(f"pipeline Scene/Project missing for {scene_id}")
    return proj, sc


def _desired_via_blocking(
    space: dict,
    bits: list[dict],
    rows: list[dict],
) -> list[dict] | None:
    try:
        from app.services.scene_space.blocking import assign_blocking
    except ImportError:
        return None
    payload = dict(space)
    if rows and not payload.get("scene_id"):
        payload["scene_id"] = rows[0].get("scene_id")
    try:
        out = assign_blocking(payload, bits, [dict(r) for r in rows])
    except Exception as exc:  # noqa: BLE001
        logger.warning("assign_blocking failed ({}): {}", type(exc).__name__, exc)
        return None
    if not out:
        return None
    return list(out)


async def _insert_service_frame(
    session: AsyncSession,
    project: Project,
    scene: Scene,
    after_uuid: str | None,
    scene_id: str,
    desired: dict,
) -> str:
    after_id = None
    if after_uuid:
        prev = (
            await session.execute(select(Frame).where(Frame.uuid == after_uuid))
        ).scalar_one_or_none()
        after_id = prev.id if prev is not None else None
    fr = await insert_frame_after(
        session,
        project,
        after_frame_id=after_id,
        scene_id=scene.id,
    )
    keep_uuid = str(desired.get("uuid") or "").strip() or new_frame_uuid()
    fr.uuid = keep_uuid
    fr.voiceover_text = ""
    fr.image_prompt = _prompt("service shot, same room, same pair")
    fr.animation_prompt = _prompt("service shot hold")
    fr.attrs = dict(fr.attrs or {})
    await session.flush()
    row = {
        "uuid": keep_uuid,
        "scene_id": scene_id,
        **{k: desired[k] for k in desired if k != "uuid"},
    }
    row["uuid"] = keep_uuid
    await _upsert_frame_space(session, row)
    return keep_uuid


async def rewrite(session: AsyncSession, scene_id: str, meaning: str) -> dict:
    """Rebuild bits + shots for a new meaning. Preserves manual=1 rows."""
    space = await _get_scene_space(session, scene_id)
    rows = await _list_frame_spaces(session, scene_id)
    if space is None or not rows:
        if scene_id in _BUILDERS:
            await seed_fixture(session, load_fixture(scene_id))
            space = await _get_scene_space(session, scene_id)
            rows = await _list_frame_spaces(session, scene_id)
        else:
            from app.services.scene_space.pipeline import ingest_space_id, parse_space_id

            if parse_space_id(scene_id) is None:
                raise RuntimeError(f"unknown scene {scene_id}; seed fixtures first")
            await ingest_space_id(session, scene_id)
            space = await _get_scene_space(session, scene_id)
            rows = await _list_frame_spaces(session, scene_id)
            if space is None or not rows:
                raise RuntimeError(f"unknown scene {scene_id}; ingest produced nothing")
    assert space is not None

    meaning = str(meaning)
    content_rows = [r for r in rows if not _is_service_row(r)]
    bits = derive_bits(meaning, content_rows)
    desired = _desired_via_blocking(space, bits, content_rows) or [dict(r) for r in content_rows]
    # align uuids / scene_id
    by_order = {int(r["shot_order"]): r for r in rows}
    aligned: list[dict] = []
    for i, d in enumerate(desired, start=1):
        item = dict(d)
        order = int(item.get("shot_order") or i)
        item["shot_order"] = order
        item["scene_id"] = scene_id
        if not item.get("uuid") and order in by_order:
            item["uuid"] = by_order[order]["uuid"]
            item["manual"] = by_order[order].get("manual", 0)
        aligned.append(item)

    new_hash = input_hash(meaning, bits, content_rows)
    old_hash = str(space.get("input_hash") or "")
    if new_hash == old_hash:
        logger.info("scene_space rewrite {}: unchanged hash, skip", scene_id)
        return {"scene_id": scene_id, "changed": dict(_EMPTY_CHANGED)}

    proj, sc = await _pipeline_project_for(session, scene_id)
    sc.meaning = meaning
    sc.attrs = {**dict(sc.attrs or {}), "bits": bits, "scene_space_id": scene_id}

    inserted: list[str] = []
    updated: list[str] = []
    skipped: list[str] = []
    last_uuid = rows[-1]["uuid"] if rows else None

    for item in aligned:
        uid = item.get("uuid")
        existing = next((r for r in rows if r["uuid"] == uid), None) if uid else None
        if existing is not None and int(existing.get("manual") or 0) == 1:
            skipped.append(existing["uuid"])
            continue
        if existing is None:
            uid = await _insert_service_frame(
                session, proj, sc, last_uuid, scene_id, item
            )
            inserted.append(uid)
            last_uuid = uid
            continue
        write = dict(existing)
        for key in _SHOT_HASH_KEYS:
            if key in item:
                write[key] = item[key]
        write["uuid"] = existing["uuid"]
        write["scene_id"] = scene_id
        write["manual"] = int(existing.get("manual") or 0)
        await _upsert_frame_space(session, write)
        updated.append(existing["uuid"])

    fresh_rows = await _list_frame_spaces(session, scene_id)
    space_doc = dict(space)
    space_doc["input_hash"] = new_hash
    space_doc["value_charge"] = space.get("value_charge") or {"start": "+", "end": "-"}
    await _upsert_scene_space(session, scene_id, _space_json_core(space_doc, meaning_hash_bits={"input_hash": new_hash}))
    # keep plan/axes from original space_json_core — _space_json_core copies them
    _ = fresh_rows
    logger.info(
        "scene_space rewrite {}: inserted={} updated={} skipped={}",
        scene_id,
        inserted,
        updated,
        skipped,
    )
    return {
        "scene_id": scene_id,
        "changed": {"inserted": inserted, "updated": updated, "skipped": skipped},
    }


async def rebuild(session: AsyncSession, scene_id: str) -> dict:
    """Rebuild shot breakdown from current meaning. Same idempotency as rewrite."""
    space = await _get_scene_space(session, scene_id)
    rows = await _list_frame_spaces(session, scene_id)
    if space is None or not rows:
        if scene_id in _BUILDERS:
            await seed_fixture(session, load_fixture(scene_id))
        else:
            from app.services.scene_space.pipeline import ingest_space_id, parse_space_id

            if parse_space_id(scene_id) is None:
                raise RuntimeError(f"unknown scene {scene_id}")
            await ingest_space_id(session, scene_id)
    _proj, sc = await _pipeline_project_for(session, scene_id)
    meaning = sc.meaning or ""
    return await rewrite(session, scene_id, meaning)


# ---------------------------------------------------------------------------
# Coverage (2.9) — used by tests/e2e without validate.py
# ---------------------------------------------------------------------------

_MASTER_SIZES = {"EWS", "WS", "FS"}
_SIZE_INDEX = ["EWS", "WS", "FS", "MWS", "MS", "MCU", "CU", "ECU"]


def _pos_ids(screen_pos: Any) -> set[str]:
    obj = _as_json_obj(screen_pos) if not isinstance(screen_pos, dict) else screen_pos
    if not obj and isinstance(screen_pos, str):
        try:
            obj = json.loads(screen_pos)
        except json.JSONDecodeError:
            obj = {}
    return set(obj.keys())


def coverage_dialogue(rows: Sequence[Mapping], accents: Sequence[str]) -> list[str]:
    gaps: list[str] = []
    sizes = {str(r.get("shot_size")) for r in rows}
    if len(sizes) < 3:
        gaps.append("interest_sizes")
    if len(accents) != len(set(accents)):
        gaps.append("interest_accent")
    roles = {str(r.get("beat_role")) for r in rows}
    if "turning_point" not in roles:
        gaps.append("interest_turning_point")
    if not ({"reaction", "insert"} & roles):
        gaps.append("interest_reaction")
    lo, hi = _pair_ids()
    has_master = any(
        str(r.get("shot_size")) in _MASTER_SIZES and {lo, hi} <= _pos_ids(r.get("screen_pos"))
        for r in rows
    )
    has_s1 = any(_pos_ids(r.get("screen_pos")) == {lo} for r in rows)
    has_s2 = any(_pos_ids(r.get("screen_pos")) == {hi} for r in rows)
    has_re = any(str(r.get("beat_role")) == "reaction" for r in rows)
    if not (has_master and has_s1 and has_s2 and has_re):
        gaps.append("interest_coverage")
    return gaps


def coverage_cross(rows: Sequence[Mapping]) -> list[str]:
    gaps: list[str] = []
    if not any(r.get("crossing_method") for r in rows):
        gaps.append("crossing_method")
    if not any(int(r.get("reestablish_due") or 0) == 1 for r in rows):
        gaps.append("reestablish_due")
    if not any(str(r.get("beat_role")) == "reestablish" for r in rows):
        gaps.append("reestablish_role")
    dirs = {str(r.get("screen_dir")) for r in rows}
    if "L→R" not in dirs:
        gaps.append("screen_dir")
    return gaps


def coverage_turn(rows: Sequence[Mapping]) -> list[str]:
    gaps: list[str] = []
    has_turn = False
    has_smash = False
    prev_size = None
    for r in rows:
        delta = _as_json_obj(r.get("space_delta_json"))
        if delta.get("turn") or delta.get("turn_subject"):
            has_turn = True
        if str(r.get("beat_role")) == "insert" and prev_size:
            try:
                gap = abs(_SIZE_INDEX.index(str(r.get("shot_size"))) - _SIZE_INDEX.index(prev_size))
            except ValueError:
                gap = 0
            if gap >= 3:
                has_smash = True
        prev_size = str(r.get("shot_size"))
    if not has_turn:
        gaps.append("turn")
    if not has_smash:
        gaps.append("smash_insert")
    if not any(str(r.get("beat_role")) == "insert" for r in rows):
        gaps.append("insert")
    return gaps


def missing_modules() -> dict[str, str]:
    req: dict[str, str] = {}
    for label, path in (
        ("store", "app.services.scene_space.store"),
        ("blocking", "app.services.scene_space.blocking"),
        ("validate", "app.services.scene_space.validate"),
        ("render_plan", "app.services.scene_space.render_plan"),
        ("render_board", "app.services.scene_space.render_board"),
    ):
        try:
            __import__(path)
        except ImportError:
            req[label] = path
    return req


async def run_e2e(session: AsyncSession, *, meaning: str = "A wins then loses the folder") -> dict:
    """Seed three fixtures, rewrite/rebuild twice, report missing stream 1–4 modules."""
    seeds = await seed_all_fixtures(session)
    counts = {sid: seeds[sid]["frames"] for sid in FIXTURE_IDS}
    dialogue_rows = await _list_frame_spaces(session, "fix:dialogue")
    cross_rows = await _list_frame_spaces(session, "fix:cross")
    turn_rows = await _list_frame_spaces(session, "fix:turn")

    def _accents(sid: str) -> list[str]:
        fx = load_fixture(sid)
        return [str(fr["attrs"]["accent"]) for fr in fx["frames"]]

    r1 = await rewrite(session, "fix:dialogue", meaning)
    r2 = await rewrite(session, "fix:dialogue", meaning)
    b1 = await rebuild(session, "fix:dialogue")
    b2 = await rebuild(session, "fix:dialogue")

    validate_result: dict[str, Any] = {"imported": False}
    try:
        from app.services.scene_space.validate import (
            load_fixture as load_validate_fixture,
            validate_scene,
        )

        by_scene: dict[str, dict[str, int]] = {}
        for sid in FIXTURE_IDS:
            stem = sid.split(":", 1)[1]
            _sid, space, rows, frames = load_validate_fixture(FIXTURE_DIR / f"{stem}.json")
            issues = validate_scene(sid, rows, space, frames)
            by_scene[sid] = {
                "errors": sum(1 for i in issues if i.get("level") == "error"),
                "warnings": sum(1 for i in issues if i.get("level") == "warning"),
            }
        validate_result = {"imported": True, "by_scene": by_scene}
    except ImportError:
        validate_result["request"] = (
            "stream 4: app/services/scene_space/validate.py and "
            "scripts/scene_space_validate.py --fixtures"
        )
    except Exception as exc:  # noqa: BLE001
        validate_result["error"] = f"{type(exc).__name__}: {exc}"

    return {
        "scene_ids": list(FIXTURE_IDS),
        "frame_counts": counts,
        "seeds": {k: {"seeded": v["seeded"], "frames": v["frames"]} for k, v in seeds.items()},
        "rewrite_first": r1,
        "rewrite_second": r2,
        "rebuild_first": b1,
        "rebuild_second": b2,
        "coverage": {
            "fix:dialogue": coverage_dialogue(dialogue_rows, _accents("fix:dialogue")),
            "fix:cross": coverage_cross(cross_rows),
            "fix:turn": coverage_turn(turn_rows),
        },
        "missing_modules": missing_modules(),
        "validate": validate_result,
        "db": "isolated (not data/state.db)",
    }
