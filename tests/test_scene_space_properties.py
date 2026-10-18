"""CANON §7 property tests 9–10 (store/rewrite). Tests 1–8 are in test_scene_space_geom.py."""

from __future__ import annotations

import inspect
import json

import pytest

_SPACE = {
    "plan": [
        {"id": "c01", "x": 0.0, "y": 2.0, "facing": 0},
        {"id": "c02", "x": 1.0, "y": 2.0, "facing": 180},
        {"id": "cam", "x": 0.5, "y": 0.0, "facing": 0},
    ],
    "obstacles": [{"id": "wall1", "x": -1, "y": 0, "w": 0.2, "h": 3}],
    "axes": [{"pair": ["c01", "c02"], "side_locked": "B"}],
    "value_charge": {"start": "+", "end": "-"},
    "input_hash": "prop-9",
    "monotony_surface": "ground",
}


def _frame_row(uuid: str, order: int, delta: dict | None = None, **extra) -> dict:
    row = {
        "uuid": uuid,
        "scene_id": "fix:prop",
        "shot_order": order,
        "axis_pair": "c01|c02",
        "axis_side": "B",
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


async def test_delete_middle_frame_state_stable():
    """§7.9 Removing a middle frame: later state = replay of remaining deltas."""
    store = pytest.importorskip("app.services.scene_space.store")
    models = pytest.importorskip("app.services.scene_space.models")
    migrate = pytest.importorskip("app.services.scene_space.migrate")
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base

    apply_deltas = store.apply_deltas
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate.migrate_scene_space_schema(conn)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            await store.upsert_scene_space(session, "fix:prop", _SPACE)
            await store.upsert_frame_space(session, _frame_row("a" * 24, 1, {}))
            await store.upsert_frame_space(
                session, _frame_row("b" * 24, 2, {"c02": {"x": 5.0}})
            )
            await store.upsert_frame_space(
                session, _frame_row("c" * 24, 3, {"c02": {"y": 4.0}})
            )
            await session.commit()
            mid = await session.get(models.FrameSpace, "b" * 24)
            assert mid is not None
            await session.delete(mid)
            await session.commit()
            st = await store.state_at(session, "fix:prop", 99)
            expected = apply_deltas(
                _SPACE["plan"],
                [{}, {"c02": {"y": 4.0}}],
            )
            by_got = {p["id"]: p for p in st["plan"]}
            by_exp = {p["id"]: p for p in expected}
            assert by_got["c02"]["x"] == by_exp["c02"]["x"] == 1.0
            assert by_got["c02"]["y"] == by_exp["c02"]["y"] == 4.0
    finally:
        await engine.dispose()


async def test_rebuild_idempotent():
    """§7.10 Second rebuild on unchanged input writes 0 INSERT/UPDATE."""
    rewrite = pytest.importorskip("app.services.scene_space.rewrite")
    store = pytest.importorskip("app.services.scene_space.store")
    migrate = pytest.importorskip("app.services.scene_space.migrate")
    rebuild = (
        getattr(rewrite, "rebuild", None)
        or getattr(rewrite, "rebuild_shots", None)
        or getattr(rewrite, "rebuild_scene", None)
        or getattr(rewrite, "rebuild_frames", None)
    )
    if rebuild is None:
        pytest.fail(
            "app.services.scene_space.rewrite has no rebuild/rebuild_shots/"
            f"rebuild_scene/rebuild_frames; names={dir(rewrite)}"
        )
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.models import Base, Frame, Project, ProjectStatus, Scene

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await migrate.migrate_scene_space_schema(conn)
        factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
        async with factory() as session:
            session.add(Project(slug="prop-rebuild", topic="t", status=ProjectStatus.new))
            await session.flush()
            proj = (
                await session.execute(select(Project).where(Project.slug == "prop-rebuild"))
            ).scalar_one()
            sc = Scene(
                project_id=proj.id,
                sort_key=10.0,
                title="prop",
                meaning="prop scene turns",
                attrs={"scene_space_id": "fix:prop"},
            )
            session.add(sc)
            await session.flush()
            session.add(
                Frame(
                    project_id=proj.id,
                    scene_id=sc.id,
                    number=1,
                    voiceover_text="ok",
                    uuid="a" * 24,
                    image_prompt="PROMPT: table\nNEGATIVE PROMPT: blur",
                    attrs={"accent": "hands"},
                )
            )
            await store.upsert_scene_space(session, "fix:prop", _SPACE)
            await store.upsert_frame_space(session, _frame_row("a" * 24, 1, {}))
            await session.commit()

            async def _call():
                spec = inspect.signature(rebuild)
                kwargs = {}
                if "session" in spec.parameters:
                    kwargs["session"] = session
                if "scene_id" in spec.parameters:
                    kwargs["scene_id"] = "fix:prop"
                if inspect.iscoroutinefunction(rebuild):
                    if kwargs:
                        return await rebuild(**kwargs)
                    return await rebuild("fix:prop")
                if kwargs:
                    return rebuild(**kwargs)
                return rebuild("fix:prop")

            first = await _call()
            second = await _call()
            payload = second
            if inspect.isawaitable(payload):
                payload = await payload
            if isinstance(payload, str):
                payload = json.loads(payload)
            assert isinstance(payload, dict), payload
            changed = payload.get("changed") or payload
            inserted = changed.get("inserted") or []
            updated = changed.get("updated") or []
            assert inserted == [], (first, second)
            assert updated == [], (first, second)
    finally:
        await engine.dispose()
