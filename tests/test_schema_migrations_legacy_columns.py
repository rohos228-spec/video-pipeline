"""Migrations 0007-0009: the old startup ALTER TABLE column additions, now versioned (no network)."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm

LEGACY_IDS = ["0007_frames_v2_columns", "0008_frames_space_manual", "0009_projects_columns"]
NEW_PROJECT_COLS = {c for c, _ in sm.PROJECTS_COLUMNS}


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    yield eng
    await eng.dispose()


async def _old_schema(eng) -> None:
    """A database from before those columns existed, with rows."""
    async with eng.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE projects (id INTEGER PRIMARY KEY, slug VARCHAR(120) NOT NULL, status VARCHAR(20))")
        await conn.exec_driver_sql(
            "CREATE TABLE frames (id INTEGER PRIMARY KEY, project_id INTEGER, number INTEGER, voiceover_text TEXT)")
        await conn.exec_driver_sql(
            "CREATE TABLE scenes_space (scene_id TEXT NOT NULL PRIMARY KEY, space_json TEXT NOT NULL)")
        await conn.exec_driver_sql(
            "CREATE TABLE frames_space (uuid TEXT NOT NULL PRIMARY KEY, scene_id TEXT NOT NULL, "
            "shot_order INTEGER NOT NULL, reestablish_due INTEGER NOT NULL DEFAULT 0)")
        await conn.exec_driver_sql("INSERT INTO projects (id, slug, status) VALUES (7, 'old-one', 'new')")
        await conn.exec_driver_sql("INSERT INTO frames (id, project_id, number, voiceover_text) VALUES (1, 7, 1, 'hi')")
        await conn.exec_driver_sql("INSERT INTO scenes_space VALUES ('s1', '{}')")
        await conn.exec_driver_sql("INSERT INTO frames_space (uuid, scene_id, shot_order) VALUES ('u1', 's1', 1)")


async def _cols(eng, table: str) -> set[str]:
    async with eng.connect() as conn:
        return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns(table))}


async def _run(eng, **kw):
    async with eng.begin() as conn:
        return await sm.run_migrations(conn, **kw)


def test_legacy_migrations_are_registered_after_0006():
    ids = [m.id for m in sm.MIGRATIONS]
    assert ids[6:9] == LEGACY_IDS and ids == sorted(ids)
    by_id = {m.id: m for m in sm.MIGRATIONS}
    assert by_id["0009_projects_columns"].scopes == (sm.SCOPE_MASTER,)
    assert sm.SCOPE_PROJECT in by_id["0007_frames_v2_columns"].scopes


async def test_old_schema_gets_columns_and_keeps_data(engine):
    await _old_schema(engine)
    report = await _run(engine)
    assert set(LEGACY_IDS) <= set(report.applied) and report.failed is None
    assert {"uuid", "sort_key", "scene_id"} <= await _cols(engine, "frames")
    assert "manual" in await _cols(engine, "frames_space")
    assert NEW_PROJECT_COLS <= await _cols(engine, "projects")
    async with engine.connect() as conn:
        assert tuple((await conn.exec_driver_sql("SELECT slug, status FROM projects")).fetchone()) == ("old-one", "new")
        assert tuple((await conn.exec_driver_sql("SELECT number, voiceover_text, uuid FROM frames")).fetchone()) == (
            1, "hi", None)
        assert tuple((await conn.exec_driver_sql("SELECT scene_id, manual FROM frames_space")).fetchone()) == ("s1", 0)


async def test_added_column_defaults_match_the_legacy_ddl(engine):
    await _old_schema(engine)
    await _run(engine)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO projects (id, slug, status) VALUES (8, 'new-one', 'new')")
        await conn.exec_driver_sql("INSERT INTO frames_space (uuid, scene_id, shot_order) VALUES ('u2', 's1', 2)")
        row = (await conn.exec_driver_sql(
            "SELECT enrich_slots_count, image_relax, auto_mode FROM projects WHERE id = 8")).fetchone()
        assert tuple(row) == (3, 0, 0)
        assert (await conn.exec_driver_sql("SELECT manual FROM frames_space WHERE uuid = 'u2'")).scalar() == 0
    with pytest.raises(Exception, match="CHECK"):  # the CHECK on manual survives
        async with engine.begin() as conn:
            await conn.exec_driver_sql("UPDATE frames_space SET manual = 5")


async def test_second_run_is_a_no_op(engine):
    await _old_schema(engine)
    await _run(engine)
    shape = {t: await _cols(engine, t) for t in ("projects", "frames", "frames_space")}
    async with engine.connect() as conn:
        before = await sm.applied_migrations(conn)
    again = await _run(engine)
    assert again.applied == [] and again.drift == [] and set(LEGACY_IDS) <= set(again.skipped)
    assert shape == {t: await _cols(engine, t) for t in shape}
    async with engine.connect() as conn:
        assert await sm.applied_migrations(conn) == before


async def test_database_that_already_has_the_columns_is_only_recorded(engine):
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE frames (id INTEGER PRIMARY KEY, uuid VARCHAR(64), "
                                   "sort_key FLOAT, scene_id INTEGER)")
        await conn.exec_driver_sql("INSERT INTO frames (id, uuid, sort_key, scene_id) VALUES (1, 'abc', 10.0, 2)")
    report = await _run(engine)
    assert "0007_frames_v2_columns" in report.applied and report.failed is None
    async with engine.connect() as conn:
        assert tuple((await conn.exec_driver_sql("SELECT uuid, sort_key, scene_id FROM frames")).fetchone()) == (
            "abc", 10.0, 2)


async def test_missing_tables_are_skipped_not_created(engine):
    report = await _run(engine)
    assert set(LEGACY_IDS) <= set(report.applied) and report.failed is None
    async with engine.connect() as conn:
        names = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    assert not {"projects", "frames", "frames_space"} & names


async def test_project_scope_skips_the_projects_migration(engine):
    await _old_schema(engine)
    report = await _run(engine, scope=sm.SCOPE_PROJECT)
    assert "0009_projects_columns" not in report.applied and "0007_frames_v2_columns" in report.applied
    assert "image_generator" not in await _cols(engine, "projects")


async def test_failing_column_migration_rolls_back_every_column_of_it(engine):
    await _old_schema(engine)

    async def partly_bad(conn):
        await sm.add_missing_columns(conn, "frames", (("good_col", "INTEGER"), ("bad_col", "NOT A TYPE ((")))

    bad = sm.Migration("0001_partly_bad", "bad ddl", partly_bad, lambda: "x")
    with pytest.raises(sm.MigrationFailed) as info:
        await _run(engine, migrations=(bad,))
    assert info.value.report.failed == "0001_partly_bad"
    cols = await _cols(engine, "frames")
    assert "good_col" not in cols and "bad_col" not in cols
    async with engine.connect() as conn:
        assert await sm.applied_migrations(conn) == []
        assert (await conn.exec_driver_sql("SELECT voiceover_text FROM frames")).scalar() == "hi"


async def test_legacy_entry_points_still_work_and_agree_with_the_runner(engine):
    from app.services.db_v2 import migrate_db_v2_schema
    from app.services.scene_space.migrate import migrate_scene_space_schema

    await _old_schema(engine)
    async with engine.begin() as conn:
        await migrate_db_v2_schema(conn)
        await migrate_scene_space_schema(conn)
        await migrate_db_v2_schema(conn)
    assert {"uuid", "sort_key", "scene_id"} <= await _cols(engine, "frames")
    assert "manual" in await _cols(engine, "frames_space")
    report = await _run(engine)
    assert {"0007_frames_v2_columns", "0008_frames_space_manual"} <= set(report.skipped)
    assert "0009_projects_columns" in report.applied and report.drift == []
