"""W6 интеграция: миграция 0017 (publish_approvals, publication_manual_actions) идемпотентна и совместима с ленивым ensure_schema."""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm

pytestmark = pytest.mark.raw_client
TABLES = {"publish_approvals", "publication_manual_actions"}


async def _run(url):
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            return await sm.run_migrations(conn)
    finally:
        await eng.dispose()


async def test_0017_creates_tables_triggers_and_is_idempotent(tmp_path):
    db = tmp_path / "state.db"
    url = f"sqlite+aiosqlite:///{db}"
    first = await _run(url)
    assert first.failed is None and "0017_publish_approvals" in first.applied
    second = await _run(url)
    assert second.failed is None and not second.applied and not second.drift
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert TABLES <= names
    assert {"publication_manual_no_update", "publication_manual_no_delete"} <= names
    con.execute("INSERT INTO publication_manual_actions(publication_id,project_id,platform,sha256,action,actor,created_at)"
                " VALUES (1,1,'yt','h','confirm_manual','op',1)")
    for sql in ("UPDATE publication_manual_actions SET actor='x'", "DELETE FROM publication_manual_actions"):
        with pytest.raises(sqlite3.DatabaseError):
            con.execute(sql)
    con.close()


async def test_tables_created_lazily_by_the_store_do_not_break_the_migration(tmp_path):
    from app.services import publications_store as ps
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    for ddl in (*ps._DDL, *ps._DDL_W6):
        con.execute(ddl)
    con.commit()
    con.close()
    rep = await _run(f"sqlite+aiosqlite:///{db}")
    assert rep.failed is None, rep.error
    assert "0017_publish_approvals" in rep.applied


async def test_migration_then_lazy_ensure_schema_is_compatible(tmp_path):
    from sqlalchemy.ext.asyncio import AsyncSession
    from app.services import publications_store as ps
    url = f"sqlite+aiosqlite:///{tmp_path / 'state.db'}"
    assert (await _run(url)).failed is None
    eng = create_async_engine(url)
    try:
        async with AsyncSession(eng) as session:
            await ps.ensure_schema(session)
            await ps.ensure_schema(session)
    finally:
        await eng.dispose()


def test_journal_tables_cover_w6_journals():
    assert TABLES <= set(sm.JOURNAL_TABLES)


def test_0012_fingerprint_is_unchanged_by_w6_tables():
    from app.services import publications_store as ps
    assert not any("publish_approvals" in d or "publication_manual_actions" in d for d in ps._DDL)
