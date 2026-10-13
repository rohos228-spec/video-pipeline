"""Versioned migrations of orchestrator-owned tables: fresh DB, existing DB, rerun, rollback, drift (no network)."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm
from app.services.observation import migrate_observation_schema

MASTER_TABLES = {"observation_events", "supervisor_kill_switch", "spend_ledger", "provider_task_ledger",
                 "action_layer_history", "action_layer_plans", "change_proposals", "config_versions",
                 "action_layer_attachments"}
ALL_IDS = [m.id for m in sm.MIGRATIONS]


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}")
    yield eng
    await eng.dispose()


async def _tables(eng) -> set[str]:
    async with eng.connect() as conn:
        return set(await conn.run_sync(lambda c: inspect(c).get_table_names()))


async def _run(eng, **kw):
    async with eng.begin() as conn:
        return await sm.run_migrations(conn, **kw)


async def test_fresh_database_gets_every_migration_in_order(engine):
    report = await _run(engine)
    assert report.applied == ALL_IDS and not report.skipped and not report.drift and report.failed is None
    assert MASTER_TABLES <= await _tables(engine)
    async with engine.connect() as conn:
        rows = await sm.applied_migrations(conn)
    assert [r["id"] for r in rows] == ALL_IDS
    assert all(len(r["checksum"]) == 64 and r["applied_at"] for r in rows)


async def test_rerun_changes_nothing(engine):
    await _run(engine)
    async with engine.connect() as conn:
        before = await sm.applied_migrations(conn)
    report = await _run(engine)
    assert report.applied == [] and report.skipped == ALL_IDS and not report.drift
    async with engine.connect() as conn:
        assert await sm.applied_migrations(conn) == before


async def test_existing_database_is_recorded_without_touching_data(engine):
    async with engine.begin() as conn:
        await migrate_observation_schema(conn)
        await conn.exec_driver_sql(
            "INSERT INTO supervisor_kill_switch (key, active, changed_by, rationale, source, revision, changed_at) "
            "VALUES ('global', 1, 'tester', 'keep me', 'test', 3, CURRENT_TIMESTAMP)")
    report = await _run(engine)
    assert report.applied == ALL_IDS and report.failed is None
    async with engine.connect() as conn:
        row = (await conn.exec_driver_sql("SELECT active, rationale FROM supervisor_kill_switch")).fetchall()
    assert [tuple(r) for r in row] == [(1, "keep me")]


async def test_legacy_tables_are_not_touched(engine):
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE legacy_things (id INTEGER PRIMARY KEY, name TEXT)")
        await conn.exec_driver_sql("INSERT INTO legacy_things (name) VALUES ('x')")
    await _run(engine)
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql("SELECT name FROM legacy_things")).scalar() == "x"


async def test_project_scope_creates_only_observation_tables(engine):
    report = await _run(engine, scope=sm.SCOPE_PROJECT)
    # 0007/0008 (frames columns) and 0009 (projects columns) also run per project database (S-1); on a bare DB
    # they are no-ops
    assert report.applied == ["0001_observation_core", "0007_frames_v2_columns", "0008_frames_space_manual",
                              "0009_projects_columns"]
    tables = await _tables(engine)
    assert "observation_events" in tables and "spend_ledger" not in tables
    assert "change_proposals" not in tables and "action_layer_plans" not in tables


async def test_failed_migration_rolls_back_and_is_reported(engine):
    async def boom(conn):
        await conn.exec_driver_sql("CREATE TABLE half_done (id INTEGER PRIMARY KEY)")
        raise RuntimeError("explode")

    ok = sm.Migration("0001_ok", "ok", lambda conn: conn.exec_driver_sql("CREATE TABLE ok_table (id INTEGER)"),
                      lambda: "a")
    bad = sm.Migration("0002_bad", "bad", boom, lambda: "b")
    later = sm.Migration("0003_later", "never runs", lambda conn: conn.exec_driver_sql(
        "CREATE TABLE later_table (id INTEGER)"), lambda: "c")
    with pytest.raises(sm.MigrationFailed) as info:
        async with engine.begin() as conn:
            await sm.run_migrations(conn, migrations=(ok, bad, later))
    report = info.value.report
    assert report.applied == ["0001_ok"] and report.failed == "0002_bad" and "explode" in report.error
    tables = await _tables(engine)
    assert "ok_table" in tables and "half_done" not in tables and "later_table" not in tables
    async with engine.connect() as conn:
        assert [r["id"] for r in await sm.applied_migrations(conn)] == ["0001_ok"]

    fixed = sm.Migration("0002_bad", "fixed", lambda conn: conn.exec_driver_sql(
        "CREATE TABLE half_done (id INTEGER PRIMARY KEY)"), lambda: "b")
    report = await _run(engine, migrations=(ok, fixed, later))
    assert report.skipped == ["0001_ok"] and report.applied == ["0002_bad", "0003_later"]


async def test_changed_schema_is_reported_as_drift_and_not_rewritten(engine):
    first = sm.Migration("0001_x", "x", lambda conn: conn.exec_driver_sql("CREATE TABLE x (id INTEGER)"),
                         lambda: "v1")
    await _run(engine, migrations=(first,))
    changed = sm.Migration("0001_x", "x", first.apply, lambda: "v2")
    report = await _run(engine, migrations=(changed,))
    assert report.drift == ["0001_x"] and report.applied == []
    async with engine.connect() as conn:
        assert (await sm.applied_migrations(conn))[0]["checksum"] == "v1"


async def test_registered_migrations_have_no_drift_on_a_fresh_run(engine):
    await _run(engine)
    assert (await _run(engine)).drift == []


async def test_unknown_recorded_ids_are_reported_not_failed(engine):
    await _run(engine)
    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO schema_migrations (id, applied_at, checksum) "
                                "VALUES ('9999_future', CURRENT_TIMESTAMP, 'z')"))
    report = await _run(engine)
    assert report.unknown == ["9999_future"] and report.failed is None


async def test_migrations_must_be_ordered_and_unique(engine):
    noop = sm.Migration("0002_b", "b", lambda conn: conn.exec_driver_sql("SELECT 1"), lambda: "a")
    earlier = sm.Migration("0001_a", "a", noop.apply, lambda: "a")
    with pytest.raises(ValueError):
        await _run(engine, migrations=(noop, earlier))


async def test_append_only_triggers_survive_migration(engine):
    await _run(engine)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO provider_task_ledger (created_at, task_id, key, provider, api, model, event, status, note)"
            " VALUES (CURRENT_TIMESTAMP, 't1', 'k', 'p', 'a', 'm', 'e', 's', '')")
    with pytest.raises(Exception, match="append-only"):
        async with engine.begin() as conn:
            await conn.exec_driver_sql("DELETE FROM provider_task_ledger")


async def test_attachments_migration_0006_is_idempotent_and_keeps_rows(engine):
    assert "0006_action_attachments" in ALL_IDS and ALL_IDS == sorted(ALL_IDS)
    report = await _run(engine)
    assert "0006_action_attachments" in report.applied
    cols = await _columns(engine, "action_layer_attachments")
    assert {"id", "project_id", "name", "kind", "size", "sha256", "rel_path", "created_at",
            "consumed_at", "consumed_plan_id"} <= cols
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "INSERT INTO action_layer_attachments (id, project_id, operator, name, kind, ext, content_type, "
            "size, sha256, rel_path, created_at, consumed_plan_id) VALUES ('a' || hex(zeroblob(15)), 1, 'op', "
            "'v.mp3', 'audio', '.mp3', '', 3, 'x', 'attachments/1/a_v.mp3', CURRENT_TIMESTAMP, '')")
    again = await _run(engine)
    assert again.applied == [] and again.drift == [] and "0006_action_attachments" in again.skipped
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql("SELECT count(*) FROM action_layer_attachments")).scalar() == 1


async def test_attachments_migration_records_a_database_that_already_has_the_table(engine):
    from app.services.action_layer.attachments import ensure_attachments_schema
    async with engine.begin() as conn:
        await ensure_attachments_schema(conn)
        await conn.exec_driver_sql(
            "INSERT INTO action_layer_attachments (id, project_id, operator, name, kind, ext, content_type, "
            "size, sha256, rel_path, created_at, consumed_plan_id) VALUES ('b' || hex(zeroblob(15)), 2, 'op', "
            "'p.png', 'image', '.png', '', 3, 'x', 'attachments/2/b_p.png', CURRENT_TIMESTAMP, '')")
    report = await _run(engine)
    assert report.failed is None and "0006_action_attachments" in report.applied
    async with engine.connect() as conn:
        assert (await conn.exec_driver_sql("SELECT name FROM action_layer_attachments")).scalar() == "p.png"
    assert (await _run(engine)).drift == []


async def test_project_scope_does_not_create_the_attachments_table(engine):
    await _run(engine, scope=sm.SCOPE_PROJECT)
    assert "action_layer_attachments" not in await _tables(engine)


async def _columns(eng, table: str) -> set[str]:
    async with eng.connect() as conn:
        return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns(table))}
