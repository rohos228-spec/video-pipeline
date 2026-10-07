"""W1-7 / A15: backup before migrations, journal copy, restore check, restore, schedule, tracker (no network)."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.services import schema_migrations as sm
from app.services import tracker_schema

ROOT = Path(__file__).resolve().parent.parent


def _open(path):
    """sqlite3 connection that is really closed on exit (Windows keeps the file locked otherwise)."""
    return closing(sqlite3.connect(path))


def _make_db(path: Path, rows: int = 5) -> Path:
    db = sqlite3.connect(str(path))
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE legacy (id INTEGER PRIMARY KEY, note TEXT)")
    db.execute("CREATE TABLE spend_ledger (id INTEGER PRIMARY KEY, usd REAL, note TEXT)")
    db.executemany("INSERT INTO legacy(note) VALUES (?)", [(f"n{i}",) for i in range(rows)])
    db.executemany("INSERT INTO spend_ledger(usd, note) VALUES (?, ?)", [(0.1 * i, f"s{i}") for i in range(rows)])
    db.commit()
    db.close()
    return path


def _count(path: Path, table: str) -> int:
    with _open(str(path)) as db:
        return db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------- file level
def test_backup_is_verified_has_manifest_and_separate_journal(tmp_path):
    db = _make_db(tmp_path / "state.db")
    res = sm.backup_sqlite(db, tag="Pre Migration!")
    assert res.path.parent == tmp_path / "backups" and res.path.name.endswith(".bak.db")
    assert ".pre-migration." in res.path.name
    assert res.manifest_path.is_file() and res.tables == {"legacy": 5, "spend_ledger": 5}
    assert _count(res.path, "legacy") == 5
    assert res.journal_path and ".journal." in res.journal_path.name
    with _open(str(res.journal_path)) as j:  # only journal tables, with rows
        assert [r[0] for r in j.execute("SELECT name FROM sqlite_master WHERE type='table'")] == ["spend_ledger"]
        assert j.execute("SELECT count(*) FROM spend_ledger").fetchone()[0] == 5
    assert sm.verify_backup(res.path)["ok"] is True
    assert not list((tmp_path / "backups").glob("*.part"))


def test_verify_detects_tampered_and_garbage_copies(tmp_path):
    res = sm.backup_sqlite(_make_db(tmp_path / "state.db"))
    data = bytearray(res.path.read_bytes())
    data[-50] ^= 0xFF
    res.path.write_bytes(bytes(data))
    bad = sm.verify_backup(res.path)
    assert bad["ok"] is False and any("sha256" in p for p in bad["problems"])
    junk = tmp_path / "backups" / "state.manual.x.bak.db"
    junk.write_bytes(b"not a database at all" * 50)
    assert sm.verify_backup(junk)["ok"] is False
    assert sm.verify_backup(tmp_path / "nope.bak.db")["ok"] is False


def test_backup_of_missing_or_unreadable_source_raises(tmp_path):
    with pytest.raises(sm.BackupError):
        sm.backup_sqlite(tmp_path / "absent.db")
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"garbage" * 300)
    with pytest.raises(sm.BackupError):
        sm.backup_sqlite(broken)
    assert not list((tmp_path / "backups").glob("*.bak.db")) and not list((tmp_path / "backups").glob("*.part"))


def test_restore_into_clean_folder_passes_integrity_and_keeps_rows(tmp_path):
    res = sm.backup_sqlite(_make_db(tmp_path / "state.db", rows=7))
    clean = tmp_path / "clean"
    out = sm.restore_backup(res.path, clean / "state.db")
    assert out["moved_aside"] == []
    with _open(str(clean / "state.db")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM legacy").fetchone()[0] == 7


def test_restore_over_existing_moves_current_file_aside_and_never_deletes(tmp_path):
    db = _make_db(tmp_path / "state.db", rows=3)
    res = sm.backup_sqlite(db)
    with _open(str(db)) as c:  # changes after the backup
        c.execute("INSERT INTO legacy(note) VALUES ('later')")
        c.commit()
    out = sm.restore_backup(res.path, db)
    assert _count(db, "legacy") == 3
    aside = Path(out["moved_aside"][0])
    assert aside.exists() and _count(aside, "legacy") == 4  # the post-backup state is still on disk


def test_restore_refuses_bad_backup_and_leaves_target_untouched(tmp_path):
    db = _make_db(tmp_path / "state.db", rows=3)
    res = sm.backup_sqlite(db)
    res.path.write_bytes(res.path.read_bytes()[:-100] + b"x" * 100)
    before = db.read_bytes()
    with pytest.raises(sm.RestoreError):
        sm.restore_backup(res.path, db)
    assert db.read_bytes() == before and not list(tmp_path.glob("*.before-restore-*"))


def test_prune_keeps_only_newest_of_each_tag(tmp_path):
    db = _make_db(tmp_path / "state.db")
    made = []
    for _ in range(4):
        made.append(sm.backup_sqlite(db, tag="scheduled", keep=2).path)
        time.sleep(0.01)
    left = sm.backup_files(tmp_path / "backups", "state", "scheduled")
    assert left == made[-2:]
    assert len(list((tmp_path / "backups").glob("state.journal.scheduled.*.bak.db"))) == 2
    sm.backup_sqlite(db, tag="manual")  # another tag is never pruned by this one
    assert len(sm.backup_files(tmp_path / "backups", "state", "manual")) == 1


# ---------------------------------------------------------------- before migrations
@pytest.fixture
async def engine_for(tmp_path):
    engines = []

    def make(path: Path):
        eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
        engines.append(eng)
        return eng
    yield make
    for e in engines:
        await e.dispose()


async def test_existing_database_is_copied_before_the_first_pending_migration(tmp_path, engine_for):
    db = _make_db(tmp_path / "state.db", rows=4)
    eng = engine_for(db)
    async with eng.begin() as conn:
        report = await sm.migrate_orchestrator_schema(conn)
    assert report.failed is None and report.applied and report.backup
    backup = Path(report.backup)
    with _open(str(backup)) as c:  # the copy is the state BEFORE migrating
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "legacy" in names and sm.MIGRATIONS_TABLE not in names
        assert c.execute("SELECT count(*) FROM legacy").fetchone()[0] == 4
    assert report.as_dict()["backup"] == report.backup and report.journal_backup
    async with eng.begin() as conn:  # nothing pending: no new copy
        again = await sm.migrate_orchestrator_schema(conn)
    assert again.backup is None and not again.applied
    assert len(sm.backup_files(tmp_path / "backups", "state", "pre-migration")) == 1


async def test_fresh_database_needs_no_backup(tmp_path, engine_for):
    eng = engine_for(tmp_path / "fresh.db")
    async with eng.begin() as conn:
        report = await sm.migrate_orchestrator_schema(conn)
    assert report.backup is None and not (tmp_path / "backups").exists()


async def test_failing_migration_keeps_backup_rolls_back_and_names_restore_command(tmp_path, engine_for, monkeypatch):
    async def good(conn):
        await conn.exec_driver_sql("CREATE TABLE w17_good (x INTEGER)")

    async def bad(conn):
        await conn.exec_driver_sql("CREATE TABLE w17_half (x INTEGER)")
        raise RuntimeError("boom")

    plan = (sm.Migration("9001_good", "ok", good, lambda: "g"), sm.Migration("9002_bad", "fails", bad, lambda: "b"))
    monkeypatch.setattr(sm, "MIGRATIONS", plan)
    db = _make_db(tmp_path / "state.db", rows=2)
    eng = engine_for(db)
    with pytest.raises(sm.MigrationFailed) as info:
        async with eng.begin() as conn:
            await sm.migrate_orchestrator_schema(conn)
    rep = info.value.report
    assert rep.failed == "9002_bad" and rep.backup and Path(rep.backup).is_file()
    with _open(str(db)) as c:  # the working database is exactly as before: no half table, no rows
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "w17_half" not in names  # the failing step rolled back as a unit
        assert c.execute("SELECT count(*) FROM legacy").fetchone()[0] == 2
    assert sm.verify_backup(rep.backup)["ok"]
    with _open(rep.backup) as c:  # the copy predates every migration of this run
        assert "w17_good" not in {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "backup_db.py" in sm.restore_command(rep.backup, db) and str(db) in sm.restore_command(rep.backup, db)
    await eng.dispose()  # Studio stopped: a file held open by a live process is refused (see restore_backup)
    sm.restore_backup(rep.backup, db)  # and the command really restores
    assert _count(db, "legacy") == 2


async def test_backup_failure_blocks_migration_fail_closed(tmp_path, engine_for, monkeypatch):
    def boom(*a, **k):
        raise sm.BackupError("нет места")
    monkeypatch.setattr(sm, "backup_sqlite", boom)
    db = _make_db(tmp_path / "state.db")
    eng = engine_for(db)
    with pytest.raises(sm.BackupError):
        async with eng.begin() as conn:
            await sm.migrate_orchestrator_schema(conn)
    with _open(str(db)) as c:
        names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert sm.MIGRATIONS_TABLE not in names


async def test_backup_can_be_made_while_the_app_holds_a_write_transaction(tmp_path, engine_for):
    """create_all runs in the same transaction before the migrations: the copy must not deadlock on it."""
    db = _make_db(tmp_path / "state.db", rows=2)
    eng = engine_for(db)
    async with eng.begin() as conn:
        await conn.exec_driver_sql("INSERT INTO legacy(note) VALUES ('uncommitted')")
        report = await asyncio.wait_for(sm.migrate_orchestrator_schema(conn), 60)
    assert report.backup
    with _open(str(report.backup)) as c:  # committed state only: the open transaction's row is not in the copy
        assert c.execute("SELECT count(*) FROM legacy").fetchone()[0] == 2


# ---------------------------------------------------------------- schedule
def test_scheduled_backup_respects_interval_and_checks_restore(tmp_path):
    db = _make_db(tmp_path / "state.db")
    first = sm.run_scheduled_backups([db], interval_h=6)
    assert first[0]["status"] == "backed_up" and first[0]["verified"] is True
    assert sm.run_scheduled_backups([db], interval_h=6)[0]["status"] == "skipped"  # fresh copy exists
    old = Path(first[0]["path"])
    stale = time.time() - 7 * 3600
    os.utime(old, (stale, stale))
    third = sm.run_scheduled_backups([db], interval_h=6)
    assert third[0]["status"] == "backed_up" and third[0]["path"] != str(old)
    assert sm.run_scheduled_backups([db], interval_h=6, force=True)[0]["status"] == "backed_up"
    assert sm.run_scheduled_backups([tmp_path / "gone.db"])[0]["status"] == "missing"


def test_scheduled_backup_reports_failure_instead_of_raising(tmp_path):
    broken = tmp_path / "state.db"
    broken.write_bytes(b"garbage" * 300)
    out = sm.run_scheduled_backups([broken])
    assert out[0]["status"] == "failed" and "error" in out[0]


async def test_scheduled_loop_stops_on_event_and_never_raises(tmp_path):
    db = _make_db(tmp_path / "state.db")
    stop = asyncio.Event()
    task = asyncio.create_task(sm.scheduled_backup_loop([db, tmp_path / "gone.db"], check_every_s=0.05, stop=stop))
    await asyncio.sleep(0.3)
    stop.set()
    await asyncio.wait_for(task, 10)
    assert len(sm.backup_files(tmp_path / "backups", "state", "scheduled")) == 1


# ---------------------------------------------------------------- tracker.db
def test_tracker_existing_database_is_copied_before_its_migration(tmp_path):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL, "
               "provider TEXT NOT NULL, model TEXT NOT NULL)")
    db.execute("INSERT INTO api_calls(timestamp, provider, model) VALUES ('t', 'kie', 'm')")
    db.commit()
    applied = tracker_schema.migrate_tracker_db(db)
    db.close()
    assert applied == ["0001_api_calls", "0002_legacy_columns"]
    copies = sm.backup_files(tmp_path / "backups", "tracker", "pre-migration")
    assert len(copies) == 1 and _count(copies[0], "api_calls") == 1
    with _open(str(copies[0])) as c:  # the copy is from before the new columns were added
        assert "user_name" not in {r[1] for r in c.execute("PRAGMA table_info(api_calls)")}


def test_tracker_fresh_and_already_migrated_databases_are_not_copied(tmp_path):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    tracker_schema.migrate_tracker_db(db)
    tracker_schema.migrate_tracker_db(db)
    db.close()
    assert not (tmp_path / "backups").exists()


def test_tracker_backup_failure_stops_the_migration(tmp_path, monkeypatch):
    path = tmp_path / "tracker.db"
    db = sqlite3.connect(str(path))
    db.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY, timestamp TEXT, provider TEXT, model TEXT)")
    db.commit()

    def boom(*a, **k):
        raise sm.BackupError("диск полон")
    monkeypatch.setattr(sm, "backup_sqlite", boom)
    with pytest.raises(sm.BackupError):
        tracker_schema.migrate_tracker_db(db)
    assert db.execute("SELECT name FROM sqlite_master WHERE name=?", (tracker_schema.MIGRATIONS_TABLE,)).fetchone() is None
    db.close()


# ---------------------------------------------------------------- command line
def _cli():
    spec = importlib.util.spec_from_file_location("w17_backup_db", ROOT / "scripts" / "backup_db.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_backup_verify_restore_roundtrip(tmp_path, capsys):
    cli = _cli()
    db = _make_db(tmp_path / "state.db", rows=6)
    assert cli.main(["backup", "--db", str(db), "--quiet"]) == 0
    assert cli.main(["verify", "--db", str(db)]) == 0
    latest = sm.latest_backup(tmp_path / "backups", "state")
    with _open(str(db)) as c:
        c.execute("DELETE FROM legacy")
        c.commit()
    assert cli.main(["restore", str(latest), "--target", str(db)]) == 0
    assert _count(db, "legacy") == 6
    capsys.readouterr()
    assert cli.main(["list", "--db", str(db)]) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [r["backup"] for r in listed] == [str(latest)]


def test_cli_reports_failures_with_nonzero_exit(tmp_path):
    cli = _cli()
    assert cli.main(["verify", "--db", str(tmp_path / "state.db")]) == 1  # no copies: nothing was verified
    assert cli.main(["restore", str(tmp_path / "nope.bak.db"), "--target", str(tmp_path / "state.db")]) == 1
    junk = tmp_path / "state.db"
    junk.write_bytes(b"garbage" * 300)
    assert cli.main(["backup", "--db", str(junk), "--quiet"]) == 1


def test_cli_default_targets_use_env_and_never_read_dotenv(tmp_path):
    cli = _cli()
    targets = cli.default_targets(tmp_path, {"SQLITE_PATH": "custom/s.db"})
    assert targets == [tmp_path / "custom" / "s.db", tmp_path / "api-tracker" / "tracker.db"]
    assert cli.default_targets(tmp_path, {})[0] == tmp_path / "data" / "state.db"
    src = (ROOT / "scripts" / "backup_db.py").read_text(encoding="utf-8")
    assert "dotenv" not in src and "load_dotenv" not in src and "read_text" not in src


def test_studio_cmd_saves_edits_before_update_and_has_no_reset_or_stash():
    text = (ROOT / "STUDIO.cmd").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not line.strip().lower().startswith("rem"))
    assert "git reset" not in code.lower() and "stash" not in code.lower() and "clean -" not in code.lower()
    assert "tracked.patch" in code and "VP_HARD_RESET_ALLOWED=0" in code and "backup_db.py" in code
    export = (ROOT / "scripts" / "EXPORT-FULL-BACKUP.cmd").read_text(encoding="utf-8")
    assert export.index("backup_db.py") < export.index("export-full-backup.ps1")
