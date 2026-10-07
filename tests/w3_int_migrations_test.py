"""W3 интеграция: миграции 0013-0015 идемпотентны и совместимы с таблицами, которые модули создают сами."""
from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm

pytestmark = pytest.mark.raw_client
NEW = ("0013_notification_log", "0014_journal_archive_log", "0015_create_jobs")


async def _run(url):
    eng = create_async_engine(url)
    try:
        async with eng.begin() as conn:
            return await sm.run_migrations(conn)
    finally:
        await eng.dispose()


async def test_migrations_create_tables_with_triggers_and_are_idempotent(tmp_path):
    db = tmp_path / "state.db"
    url = f"sqlite+aiosqlite:///{db}"
    first = await _run(url)
    assert first.failed is None, first.error
    assert set(NEW) <= set(first.applied)
    second = await _run(url)
    assert second.failed is None and not second.applied and not second.drift
    con = sqlite3.connect(db)
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"notification_log", "journal_archive_log", "create_jobs"} <= names
    assert {"notification_log_no_update", "notification_log_no_delete", "journal_archive_log_no_update",
            "journal_archive_log_no_delete"} <= names
    con.execute("INSERT INTO notification_log(ts,channel,kind,ok) VALUES ('2026-01-01 00:00:00','telegram','x',1)")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("DELETE FROM notification_log")
    con.close()


async def test_tables_created_by_modules_do_not_break_migrations(tmp_path):
    from app.services import create_jobs, journal_archive, notification_log
    db = tmp_path / "state.db"
    con = sqlite3.connect(db)
    con.executescript(notification_log.NOTIFICATION_LOG_DDL)
    con.executescript(create_jobs.CREATE_JOBS_DDL)
    for ddl in journal_archive.ARCHIVE_LOG_DDL:
        con.execute(ddl)
    con.commit()
    con.close()
    rep = await _run(f"sqlite+aiosqlite:///{db}")
    assert rep.failed is None, rep.error
    assert set(NEW) <= set(rep.applied)


def test_journal_tables_cover_new_journals():
    assert {"notification_log", "journal_archive_log", "operator_audit", "publications"} <= set(sm.JOURNAL_TABLES)
