"""Migration, geom, and store for cinematic scene space."""

from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models import Base, Project
from app.services.scene_space.errors import DegenerateAxisError
from app.services.scene_space.geom import (
    apply_deltas,
    axis_side,
    facing_vector,
    normalize_pair,
)
from app.services.scene_space.migrate import (
    downgrade_scene_space_schema,
    migrate_scene_space_schema,
)
from app.services.scene_space.store import (
    get_scene_space,
    list_frame_spaces,
    state_at,
    upsert_frame_space,
    upsert_scene_space,
)
from app.settings import settings

_ROOT = Path(__file__).resolve().parents[1]
_CLI_PATH = _ROOT / "scripts" / "scene_space_migrate.py"
_HOOKS = (
    "app/main.py",
    "app/web/api.py",
    "app/project_db.py",
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("scene_space_migrate_cli", _CLI_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _table_names(rows) -> set[str]:
    return {r[0] for r in rows}


@pytest.fixture
async def mem_engine():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    yield engine
    await engine.dispose()


@pytest.fixture
async def session(mem_engine) -> AsyncSession:
    async with mem_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_scene_space_schema(conn)
    factory = async_sessionmaker(mem_engine, expire_on_commit=False)
    async with factory() as s:
        yield s


async def test_migrate_sql_scene_id_text_and_manual(mem_engine):
    async with mem_engine.begin() as conn:
        await migrate_scene_space_schema(conn)
        scenes = (await conn.exec_driver_sql("PRAGMA table_info(scenes_space)")).fetchall()
        frames = (await conn.exec_driver_sql("PRAGMA table_info(frames_space)")).fetchall()
        scene_types = {r[1]: r[2] for r in scenes}
        frame_types = {r[1]: r[2] for r in frames}
        assert scene_types["scene_id"].upper() == "TEXT"
        assert frame_types["scene_id"].upper() == "TEXT"
        assert "manual" in frame_types
        assert frame_types["manual"].upper().startswith("INT")
        idx = (await conn.exec_driver_sql("PRAGMA index_list(frames_space)")).fetchall()
        assert "idx_frames_space_scene" in {r[1] for r in idx}


async def test_migrate_adds_manual_to_existing_table(mem_engine):
    async with mem_engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE frames_space ("
            "uuid TEXT PRIMARY KEY, scene_id TEXT NOT NULL, shot_order INTEGER NOT NULL)"
        )
        await migrate_scene_space_schema(conn)
        cols = {r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(frames_space)")).fetchall()}
        assert "manual" in cols
        names = _table_names(
            (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        )
        assert "scenes_space" in names


async def test_up_down_up_and_downgrade_keeps_pipeline_tables(mem_engine):
    async with mem_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await migrate_scene_space_schema(conn)
        names_up = _table_names(
            (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        )
        assert "frames_space" in names_up and "scenes_space" in names_up
        assert {"frames", "scenes", "projects"} <= names_up
        await downgrade_scene_space_schema(conn)
        names_down = _table_names(
            (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        )
        assert "frames_space" not in names_down
        assert "scenes_space" not in names_down
        assert {"frames", "scenes", "projects"} <= names_down
        await migrate_scene_space_schema(conn)
        names_again = _table_names(
            (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        )
        assert "frames_space" in names_again and "scenes_space" in names_again
        assert {"frames", "scenes", "projects"} <= names_again


async def test_cli_up_down_up_uses_settings_sqlite():
    url = settings.db_url
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        cli = _load_cli()
        assert await cli.main(["--up"]) == 0
        assert await cli.main(["--down"]) == 0
        async with engine.begin() as conn:
            names = _table_names(
                (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            )
            assert "frames_space" not in names
            assert "scenes_space" not in names
            assert {"frames", "scenes", "projects"} <= names
        assert await cli.main(["--up"]) == 0
        async with engine.begin() as conn:
            names = _table_names(
                (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
            )
            assert "frames_space" in names and "scenes_space" in names
            assert {"frames", "scenes", "projects"} <= names
    finally:
        await engine.dispose()


def test_hooks_import_models_and_await_migrate():
    for rel in _HOOKS:
        text = (_ROOT / rel).read_text(encoding="utf-8")
        assert "models as _scene_space_models" in text, rel
        assert "await migrate_scene_space_schema(conn)" in text, rel


def test_normalize_pair_sorts_ids_as_strings():
    assert normalize_pair("c02", "c01") == ("c01", "c02")
    assert normalize_pair("c01", "c02") == ("c01", "c02")
    assert normalize_pair("10", "2") == ("10", "2")


def test_axis_side_a_b_and_pair_order():
    lo, hi = normalize_pair("c02", "c01")
    assert (lo, hi) == ("c01", "c02")
    a = {"id": "c01", "x": 0.0, "y": 0.0}
    b = {"id": "c02", "x": 2.0, "y": 0.0}
    cam_a = {"x": 1.0, "y": 1.0}
    cam_b = {"x": 1.0, "y": -1.0}
    assert axis_side(a, b, cam_a) == "A"
    assert axis_side(a, b, cam_b) == "B"
    assert axis_side((0.0, 0.0), (2.0, 0.0), (1.0, 1.0)) == "A"


def test_camera_on_axis_raises_degenerate():
    with pytest.raises(DegenerateAxisError, match="cross==0"):
        axis_side((0.0, 0.0), (2.0, 0.0), (1.0, 0.0))


def test_facing_0_360_90_270():
    x0, y0 = facing_vector(0)
    x360, y360 = facing_vector(360)
    assert math.isclose(x0, 0.0, abs_tol=1e-12)
    assert math.isclose(y0, 1.0, abs_tol=1e-12)
    assert math.isclose(x0, x360, abs_tol=1e-12)
    assert math.isclose(y0, y360, abs_tol=1e-12)
    x90, y90 = facing_vector(90)
    assert math.isclose(x90, 1.0, abs_tol=1e-12)
    assert math.isclose(y90, 0.0, abs_tol=1e-12)
    x270, y270 = facing_vector(270)
    assert math.isclose(x270, -1.0, abs_tol=1e-12)
    assert math.isclose(y270, 0.0, abs_tol=1e-12)


def test_apply_deltas_merges_removes_skips_meta_no_mutate():
    plan = [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 0.0, "facing": 0},
    ]
    snapshot = json.loads(json.dumps(plan))
    out = apply_deltas(
        plan,
        [
            {"turn": {"subject": "c02"}},
            {"c02": {"x": 0.6, "y": 2.8, "facing": 90}, "cam": {"x": 1.0}},
            {"c03": {"x": 4.0, "y": 1.0, "facing": 0}},
            {"c03": {"_remove": True}},
        ],
    )
    assert plan == snapshot
    by_id = {p["id"]: p for p in out}
    assert by_id["c02"]["x"] == 0.6
    assert by_id["c02"]["y"] == 2.8
    assert by_id["c02"]["facing"] == 90
    assert by_id["cam"]["x"] == 1.0
    assert by_id["cam"]["y"] == 0.0
    assert "c03" not in by_id
    assert [p["id"] for p in out] == ["c01", "c02", "cam"]


_SPACE = {
    "plan": [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 0.0, "facing": 0},
    ],
    "obstacles": [{"id": "wall1", "x": -1, "y": 0, "w": 0.2, "h": 3}],
    "axes": [{"pair": ["c01", "c02"], "side_locked": "A"}],
    "value_charge": {"start": "+", "end": "-"},
    "input_hash": "abc",
    "monotony_surface": "ground",
}


def _frame_row(uuid: str, order: int, delta: dict | None = None, **extra) -> dict:
    row = {
        "uuid": uuid,
        "scene_id": "fix:dialogue",
        "shot_order": order,
        "axis_pair": "c01|c02",
        "axis_side": "A",
        "screen_pos": {"c01": "L", "c02": "R"},
        "screen_dir": "none",
        "shot_size": "MS",
        "angle_v": "eye-level",
        "angle_h": "frontal",
        "beat_role": "beat",
        "crossing_method": None,
        "space_delta_json": delta if delta is not None else {},
        "reestablish_due": 0,
        "manual": 0,
    }
    row.update(extra)
    return row


async def test_store_roundtrip_and_state_at_replays_deltas(session: AsyncSession):
    await upsert_scene_space(session, "fix:dialogue", _SPACE)
    got = await get_scene_space(session, "fix:dialogue")
    assert got is not None
    assert got["plan"][0]["id"] == "c01"
    assert got["value_charge"] == {"start": "+", "end": "-"}

    await upsert_frame_space(session, _frame_row("aaaaaaaaaaaaaaaaaaaaaaaa", 1, {}))
    await upsert_frame_space(
        session,
        _frame_row(
            "bbbbbbbbbbbbbbbbbbbbbbbb",
            2,
            {"c02": {"x": 0.6, "y": 2.8, "facing": 180}, "cam": {"x": 1.0, "y": -1.2}},
        ),
    )
    await upsert_frame_space(
        session,
        _frame_row("cccccccccccccccccccccccc", 3, {"c02": {"facing": 90}}),
    )
    await session.commit()

    rows = await list_frame_spaces(session, "fix:dialogue")
    assert [r["shot_order"] for r in rows] == [1, 2, 3]
    assert "plan" not in (rows[1]["space_delta_json"] or {})
    assert rows[1]["space_delta_json"]["c02"]["x"] == 0.6
    assert rows[0]["screen_pos"]["c01"] == "L"

    st0 = await state_at(session, "fix:dialogue", 0)
    by0 = {p["id"]: p for p in st0["plan"]}
    assert by0["c02"]["x"] == 1.0
    assert by0["cam"]["x"] == 0.5

    st1 = await state_at(session, "fix:dialogue", 1)
    by1 = {p["id"]: p for p in st1["plan"]}
    assert by1["c02"]["x"] == 1.0

    st2 = await state_at(session, "fix:dialogue", 2)
    by2 = {p["id"]: p for p in st2["plan"]}
    assert by2["c02"]["x"] == 0.6
    assert by2["c02"]["y"] == 2.8
    assert by2["cam"]["x"] == 1.0
    assert by2["cam"]["y"] == -1.2
    assert st2["obstacles"] == _SPACE["obstacles"]

    st3 = await state_at(session, "fix:dialogue", 3)
    by3 = {p["id"]: p for p in st3["plan"]}
    assert by3["c02"]["x"] == 0.6
    assert by3["c02"]["facing"] == 90

    replay = apply_deltas(
        _SPACE["plan"],
        [
            {},
            {"c02": {"x": 0.6, "y": 2.8, "facing": 180}, "cam": {"x": 1.0, "y": -1.2}},
            {"c02": {"facing": 90}},
        ],
    )
    assert st3["plan"] == replay

    await upsert_frame_space(
        session,
        _frame_row("bbbbbbbbbbbbbbbbbbbbbbbb", 2, {"c02": {"x": 9.0}}, shot_size="CU"),
    )
    await session.commit()
    again = await list_frame_spaces(session, "fix:dialogue")
    assert again[1]["shot_size"] == "CU"
    assert again[1]["space_delta_json"]["c02"]["x"] == 9.0


async def test_get_scene_space_missing_and_state_at_keyerror(session: AsyncSession):
    assert await get_scene_space(session, "fix:missing") is None
    with pytest.raises(KeyError):
        await state_at(session, "fix:missing", 1)


async def test_delete_middle_frame_state_replays_remaining(session: AsyncSession):
    await upsert_scene_space(session, "fix:dialogue", _SPACE)
    await upsert_frame_space(session, _frame_row("aaaaaaaaaaaaaaaaaaaaaaaa", 1, {}))
    await upsert_frame_space(
        session, _frame_row("bbbbbbbbbbbbbbbbbbbbbbbb", 2, {"c02": {"x": 5.0}})
    )
    await upsert_frame_space(
        session, _frame_row("cccccccccccccccccccccccc", 3, {"c02": {"y": 4.0}})
    )
    await session.commit()
    from app.services.scene_space.models import FrameSpace

    mid = await session.get(FrameSpace, "bbbbbbbbbbbbbbbbbbbbbbbb")
    assert mid is not None
    await session.delete(mid)
    await session.commit()
    st = await state_at(session, "fix:dialogue", 99)
    by_id = {p["id"]: p for p in st["plan"]}
    assert by_id["c02"]["x"] == 1.0
    assert by_id["c02"]["y"] == 4.0


async def test_create_all_registers_space_tables(mem_engine):
    from app.services.scene_space import models as _scene_space_models  # noqa: F401

    async with mem_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        names = _table_names(
            (await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'")).fetchall()
        )
        assert "scenes_space" in names
        assert "frames_space" in names


async def test_pipeline_project_survives_space_downgrade(session: AsyncSession):
    session.add(Project(slug="keep-me", topic="t", status=None))  # type: ignore[arg-type]
    await session.commit()
    conn = await session.connection()
    await downgrade_scene_space_schema(conn)
    await migrate_scene_space_schema(conn)
    from sqlalchemy import select

    got = (await session.execute(select(Project).where(Project.slug == "keep-me"))).scalar_one()
    assert got.topic == "t"
