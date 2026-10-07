"""Migration 0010: change_proposals reject columns, formerly a raw ALTER in change_control/store.py (no network)."""
from __future__ import annotations

import pytest
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm
from app.services.change_control import store as cs

NEW = {"rejected_by", "reject_reason", "rejected_at"}


@pytest.fixture
async def engine(tmp_path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    yield eng
    await eng.dispose()


async def _cols(eng):
    async with eng.connect() as conn:
        return {c["name"] for c in await conn.run_sync(lambda c: inspect(c).get_columns("change_proposals"))}


async def _legacy_table(eng):
    """change_proposals as it was before the reject columns, with a row."""
    from sqlalchemy.schema import CreateTable
    async with eng.begin() as conn:
        ddl = str(await conn.run_sync(lambda c: CreateTable(cs.ChangeProposal.__table__).compile(c)))
        await conn.exec_driver_sql(ddl)
        for col in NEW:
            await conn.exec_driver_sql(f"ALTER TABLE change_proposals DROP COLUMN {col}")
        cols = [r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(change_proposals)")).fetchall()]
        notnull = [r[1] for r in (await conn.exec_driver_sql("PRAGMA table_info(change_proposals)")).fetchall()
                   if r[3] and r[4] is None]
    return cols, notnull


def test_0010_is_registered_and_ordered():
    ids = [m.id for m in sm.MIGRATIONS]
    assert "0010_change_proposals_reject_columns" in ids and ids == sorted(ids)


async def test_legacy_table_gets_the_columns_once_and_keeps_rows(engine):
    cols, notnull = await _legacy_table(engine)
    assert not (NEW & set(cols))
    async with engine.begin() as conn:
        vals = {c: "x" for c in notnull}
        if vals:
            names = ", ".join(vals)
            await conn.exec_driver_sql(
                f"INSERT INTO change_proposals ({names}) VALUES ({', '.join(repr(v) for v in vals.values())})")
    async with engine.begin() as conn:
        report = await sm.run_migrations(conn)
    assert report.failed is None, report.error
    assert "0010_change_proposals_reject_columns" in report.applied
    assert NEW <= await _cols(engine)
    async with engine.connect() as conn:
        n = (await conn.exec_driver_sql("SELECT count(*) FROM change_proposals")).scalar()
    assert n == (1 if notnull else 0)
    async with engine.begin() as conn:
        again = await sm.run_migrations(conn)
    assert again.applied == [] and again.drift == []


async def test_ensure_schema_alone_no_longer_alters(engine):
    await _legacy_table(engine)
    async with engine.begin() as conn:
        await cs.ensure_change_control_schema(conn)
    assert not (NEW & await _cols(engine))  # only the versioned migration adds columns now


async def test_fresh_database_has_columns_without_alter(engine):
    async with engine.begin() as conn:
        report = await sm.run_migrations(conn)
    assert report.failed is None and NEW <= await _cols(engine)
