"""W5 интеграция: миграция 0016 идемпотентна и совместима с таблицей, которую модуль создаёт сам."""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm

pytestmark = pytest.mark.raw_client
TABLES = {"outsee_idempotency", "action_layer_calls", "action_layer_snapshots"}


async def _run(url):
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            return await sm.run_migrations(conn)
    finally:
        await eng.dispose()


async def test_0016_creates_tables_triggers_and_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    url = f"sqlite+aiosqlite:///{db}"
    first = await _run(url)
    assert first.failed is None and "0016_outsee_idempotency" in first.applied
    second = await _run(url)
    assert second.failed is None and not second.applied and not second.drift
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert TABLES <= names
    assert {"outsee_idempotency_no_update", "outsee_idempotency_no_delete"} <= names
    con.close()


async def test_table_created_by_the_module_does_not_break_the_migration(tmp_path):
    from app.services import outsee_idempotency as oi
    db = tmp_path / "state.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with eng.begin() as conn:
        await conn.run_sync(lambda c: oi.OutseeIdempotency.__table__.create(c, checkfirst=True))
        for ddl in oi._TRIGGERS:
            await conn.exec_driver_sql(ddl)
    await eng.dispose()
    rep = await _run(f"sqlite+aiosqlite:///{db}")
    assert rep.failed is None, rep.error
    assert "0016_outsee_idempotency" in rep.applied


def test_journal_tables_cover_w5_journals():
    assert TABLES <= set(sm.JOURNAL_TABLES)


def test_0004_fingerprint_does_not_include_w5_tables():
    from app.services.action_layer import store
    assert sm._action_layer_fp() == sm.tables_fingerprint(
        [t for t in store._TABLES if t.name not in TABLES], store._TRIGGERS)
