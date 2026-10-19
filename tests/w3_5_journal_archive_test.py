from __future__ import annotations

import gzip
import importlib.util
import json
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.models import Base
from app.services import journal_archive as ja
from app.services import operator_audit, spend_guard
from app.services.action_layer import store

ROOT = Path(__file__).resolve().parent.parent
NOW = datetime(2026, 10, 7, 12, 0, 0)
OLD = NOW - timedelta(days=200)
NEW = NOW - timedelta(days=3)


def _make_db(path: Path) -> Path:
    eng = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(eng, tables=[
        spend_guard.SpendLedger.__table__, spend_guard.ProviderTaskLedger.__table__,
        operator_audit.OperatorAudit.__table__, *store._TABLES])
    eng.dispose()
    with closing(sqlite3.connect(path)) as db:
        for ddl in (*spend_guard._TRIGGERS, *spend_guard._TASK_TRIGGERS, *operator_audit._TRIGGERS, *store._TRIGGERS):
            db.execute(ddl)
        for i, (ts, usd, proj) in enumerate([(OLD, 1.5, 1), (OLD, 2.0, 1), (OLD, 0.25, 2), (NEW, 4.0, 1)]):
            db.execute("INSERT INTO spend_ledger (created_at, day, kind, provider, project_id, run_id, source, usd, "
                       "credits, note, request_id, ref) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                       (ts.isoformat(sep=" "), ts.strftime("%Y-%m-%d"), "actual", "kie", proj, f"r{proj}", "t",
                        usd, 1.0, f"n{i}", "", ""))
        db.execute("INSERT INTO provider_task_ledger (created_at, task_id, key, provider, api, model, event, status, note,"
                   " run_id, request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (OLD.isoformat(sep=" "), "done-old", "k", "kie", "a", "m", "create", "success", "", "", ""))
        db.execute("INSERT INTO provider_task_ledger (created_at, task_id, key, provider, api, model, event, status, note,"
                   " run_id, request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (OLD.isoformat(sep=" "), "live", "k", "kie", "a", "m", "create", "pending", "", "", ""))
        db.execute("INSERT INTO provider_task_ledger (created_at, task_id, key, provider, api, model, event, status, note,"
                   " run_id, request_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (NEW.isoformat(sep=" "), "live", "k", "kie", "a", "m", "poll", "pending", "", "", ""))
        for ts in (OLD, NEW):
            db.execute("INSERT INTO action_layer_history (created_at, plan_id, event, actor, request_text, action_id, "
                       "outcome, error) VALUES (?,?,?,?,?,?,?,?)", (ts.isoformat(sep=" "), "p", "e", "a", "", "x", "", ""))
            db.execute("INSERT INTO operator_audit (ts, request_id, method, path, actor, status_code, duration_ms, "
                       "outcome) VALUES (?,?,?,?,?,?,?,?)", (ts.isoformat(sep=" "), "q", "POST", "/api/x", "local", 200, 1, "ok"))
        for ts, cr in ((OLD, 5.0), (OLD, 3.0), (NEW, 1.0)):
            db.execute("INSERT INTO action_layer_spend (session_key, plan_id, step_index, kind, credits, created_at) "
                       "VALUES (?,?,?,?,?,?)", ("s1", "p", 0, "reserve", cr, ts.isoformat(sep=" ")))
        db.commit()
    return path


def _q(path, sql, *args):
    with closing(sqlite3.connect(path)) as db:
        return db.execute(sql, args).fetchall()


def _triggers(path):
    return sorted(r[0] for r in _q(path, "SELECT name FROM sqlite_master WHERE type='trigger'"))


def _fingerprint(path):
    tabs = [r[0] for r in _q(path, "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    return {t: _q(path, f"SELECT * FROM {t} ORDER BY 1") for t in tabs}, _triggers(path)


def test_dry_run_changes_nothing_and_creates_no_files(tmp_path):
    db = _make_db(tmp_path / "state.db")
    before = _fingerprint(db)
    rep = ja.archive_journals(db, days=90, now=NOW)
    assert not rep.apply and rep.ok
    by = {t.table: t for t in rep.tables}
    assert by["spend_ledger"].candidates == 3 and by["spend_ledger"].status == "dry-run"
    assert by["provider_task_ledger"].candidates == 1
    assert by["action_layer_history"].candidates == 1 and by["operator_audit"].candidates == 1
    assert by["observation_events"].status == "missing"
    assert _fingerprint(db) == before
    assert not (tmp_path / "archive").exists()
    assert "сухой прогон" in rep.summary_ru()


def test_apply_moves_rows_keeps_triggers_and_preserves_sums(tmp_path):
    db = _make_db(tmp_path / "state.db")
    triggers = _triggers(db)
    usd_before = _q(db, "SELECT SUM(usd) FROM spend_ledger")[0][0]
    cr_before = _q(db, "SELECT SUM(credits) FROM action_layer_spend WHERE session_key='s1'")[0][0]
    proj1 = _q(db, "SELECT SUM(usd) FROM spend_ledger WHERE project_id=1")[0][0]
    rep = ja.archive_journals(db, days=90, apply=True, now=NOW)
    assert rep.ok, rep.summary_ru()
    assert _q(db, "SELECT SUM(usd) FROM spend_ledger")[0][0] == pytest.approx(usd_before)
    assert _q(db, "SELECT SUM(usd) FROM spend_ledger WHERE project_id=1")[0][0] == pytest.approx(proj1)
    assert _q(db, "SELECT SUM(credits) FROM action_layer_spend WHERE session_key='s1'")[0][0] == pytest.approx(cr_before)
    assert _q(db, "SELECT count(*) FROM spend_ledger WHERE kind='actual'")[0][0] == 1
    assert _q(db, "SELECT count(*) FROM spend_ledger WHERE kind='archive'")[0][0] == 2
    assert _q(db, "SELECT task_id FROM provider_task_ledger ORDER BY id") == [("live",), ("live",)]
    assert _q(db, "SELECT count(*) FROM action_layer_history")[0][0] == 1
    assert _q(db, "SELECT count(*) FROM operator_audit")[0][0] == 1
    assert set(triggers) <= set(_triggers(db))
    with closing(sqlite3.connect(db)) as c:
        for sql in ("DELETE FROM spend_ledger", "UPDATE spend_ledger SET usd=0", "DELETE FROM action_layer_history",
                    "DELETE FROM journal_archive_log", "UPDATE journal_archive_log SET note='x'"):
            with pytest.raises(sqlite3.DatabaseError, match="append-only"):
                c.execute(sql)
    log = _q(db, "SELECT table_name, rows_archived, rows_deleted FROM journal_archive_log ORDER BY id")
    assert ("spend_ledger", 3, 3) in log and ("operator_audit", 1, 1) in log
    files = sorted((tmp_path / "archive").glob("*.jsonl.gz"))
    assert len(files) == len(log)
    for f in files:
        assert ja.verify_archive(f)["ok"]
    sp = next(f for f in files if f.name.startswith("spend_ledger"))
    with gzip.open(sp, "rt", encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh]
    assert sorted(r["usd"] for r in rows) == [0.25, 1.5, 2.0]


def test_second_apply_is_noop_and_rollups_not_rearchived(tmp_path):
    db = _make_db(tmp_path / "state.db")
    ja.archive_journals(db, days=90, apply=True, now=NOW)
    snap = _fingerprint(db)
    rep = ja.archive_journals(db, days=90, apply=True, now=NOW)
    assert rep.ok and all(t.candidates == 0 for t in rep.tables if t.status != "missing")
    assert _fingerprint(db) == snap


def test_corrupted_archive_rolls_back_deletion(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "state.db")
    before = _fingerprint(db)
    real = ja._export

    def corrupt(*a, **kw):
        out = real(*a, **kw)
        p = out["path"]
        data = bytearray(p.read_bytes())
        data[len(data) // 2] ^= 0xFF
        p.write_bytes(bytes(data))
        return out

    monkeypatch.setattr(ja, "_export", corrupt)
    rep = ja.archive_journals(db, days=90, apply=True, now=NOW)
    assert not rep.ok
    assert all(t.status in ("failed", "empty", "missing") for t in rep.tables)
    failed = [t for t in rep.tables if t.status == "failed"]
    assert failed and "экспорт не прошёл проверку" in failed[0].error
    after = _fingerprint(db)
    assert after[0] == before[0] and after[1] == before[1]


def test_failure_inside_transaction_restores_triggers_and_rows(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "state.db")
    before = _fingerprint(db)

    def boom(*a, **kw):
        raise ja.ArchiveError("сбой после удаления")

    monkeypatch.setattr(ja, "_insert_rollups", boom)
    rep = ja.archive_journals(db, days=90, apply=True, now=NOW, tables=["spend_ledger"])
    assert not rep.ok and "сбой после удаления" in rep.tables[0].error
    assert _fingerprint(db)[0]["spend_ledger"] == before[0]["spend_ledger"]
    assert _triggers(db) == before[1] or set(before[1]) <= set(_triggers(db))
    with closing(sqlite3.connect(db)) as c, pytest.raises(sqlite3.DatabaseError, match="append-only"):
        c.execute("DELETE FROM spend_ledger")


def test_row_count_mismatch_rolls_back(tmp_path, monkeypatch):
    db = _make_db(tmp_path / "state.db")
    real = ja._select_ids
    calls = {"n": 0}

    def shifted(conn, spec, cutoff):
        ids = real(conn, spec, cutoff)
        calls["n"] += 1
        return ids if calls["n"] == 1 else ids[:-1]

    monkeypatch.setattr(ja, "_select_ids", shifted)
    rep = ja.archive_journals(db, days=90, apply=True, now=NOW, tables=["operator_audit", "action_layer_history"])
    assert not rep.ok
    assert _q(db, "SELECT count(*) FROM operator_audit")[0][0] == 2
    assert _q(db, "SELECT count(*) FROM action_layer_history")[0][0] == 2


def test_validation_and_cli(tmp_path, capsys):
    with pytest.raises(ja.ArchiveError):
        ja.archive_journals(tmp_path / "no.db")
    db = _make_db(tmp_path / "state.db")
    with pytest.raises(ja.ArchiveError):
        ja.archive_journals(db, days=0)
    spec = importlib.util.spec_from_file_location("archive_journals_cli", ROOT / "scripts" / "archive_journals.py")
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    assert cli.main(["--db", str(db), "--days", "90"]) == 0
    out = capsys.readouterr().out
    assert "сухой прогон" in out
    assert not (tmp_path / "archive").exists()
    assert cli.main(["--db", str(db), "--days", "90", "--apply", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["apply"] is True and rep["ok"] is True
    f = next((tmp_path / "archive").glob("operator_audit_*.jsonl.gz"))
    assert cli.main(["--verify", str(f)]) == 0
    f.write_bytes(f.read_bytes()[:-5])
    assert cli.main(["--verify", str(f)]) == 1
