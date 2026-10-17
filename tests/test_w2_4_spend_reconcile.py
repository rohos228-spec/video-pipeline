from __future__ import annotations

import sqlite3
from datetime import datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.services import spend_guard, spend_reconcile as sr
from app.services.diagnosis import Incident
from app.services.telemetry import project_telemetry, reconcile_summary


@pytest.fixture(autouse=True)
def _clean_adapters():
    sr._adapters.clear()
    yield
    sr._adapters.clear()


@pytest.fixture
async def sessions(tmp_path):
    from app.services import actions_v1
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'm.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    spend_guard._ensured.clear()
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
def tracker(tmp_path):
    path = tmp_path / "tracker.db"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE api_calls (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, provider TEXT,"
                  " cost_usd REAL, project_source TEXT)")
    return path


def add_tracker(path, usd, provider="vibecode", project="7"):
    with sqlite3.connect(path) as c:
        c.execute("INSERT INTO api_calls (timestamp, provider, cost_usd, project_source) VALUES (?,?,?,?)",
                  (datetime.utcnow().isoformat(), provider, usd, project))


async def add_actual(sessions, usd, provider="outsee", project=7):
    async with sessions() as s:
        await spend_guard.record_actual(s, project_id=project, usd=usd, provider=provider)
        await s.commit()




def snaps(before, after):
    return sr.manual_snapshot("outsee", before), sr.manual_snapshot("outsee", after)


def test_compare_converged_within_tolerance():
    b, a = snaps(10.0, 9.0)
    r = sr.compare(provider="outsee", before=b, after=a, ledger_usd=1.04)
    assert r["status"] == sr.STATUS_CONVERGED and r["converged"]
    assert r["gap_usd"] == pytest.approx(-0.04)
    assert "сходится" in sr.message_ru(r)


def test_compare_gap_above_tolerance_both_directions():
    b, a = snaps(10.0, 9.0)
    under = sr.compare(provider="outsee", before=b, after=a, ledger_usd=0.5)
    assert under["status"] == sr.STATUS_GAP and "без записи" in under["reason_ru"]
    over = sr.compare(provider="outsee", before=b, after=a, ledger_usd=2.0)
    assert over["status"] == sr.STATUS_GAP and "завышены" in over["reason_ru"]


def test_compare_tolerance_is_configurable():
    b, a = snaps(10.0, 9.0)
    assert sr.compare(provider="outsee", before=b, after=a, ledger_usd=1.2, tolerance=0.05)["status"] == "gap"
    assert sr.compare(provider="outsee", before=b, after=a, ledger_usd=1.2, tolerance=0.25)["status"] == "converged"
    with pytest.raises(ValueError):
        sr.compare(provider="outsee", before=b, after=a, ledger_usd=1, tolerance=-1)


def test_compare_sums_both_journals_and_overlap():
    b, a = snaps(10.0, 8.0)
    r = sr.compare(provider="outsee", before=b, after=a, ledger_usd=1.5, tracker_usd=0.6, overlap_usd=0.1)
    assert r["journal_usd"] == pytest.approx(2.0) and r["converged"]


def test_compare_balance_rise_and_zero_zero():
    r = sr.compare(provider="outsee", before=sr.manual_snapshot("outsee", 5), after=sr.manual_snapshot("outsee", 9),
                   ledger_usd=0)
    assert r["status"] == "gap" and "вырос" in r["reason_ru"]
    z = sr.compare(provider="outsee", before=sr.manual_snapshot("outsee", 5), after=sr.manual_snapshot("outsee", 5),
                   ledger_usd=0)
    assert z["converged"]


async def test_no_snapshot_is_never_reported_as_converged():
    snap = await sr.take_snapshot("outsee")
    assert not snap.ok and "не подключён" in snap.error
    r = sr.compare(provider="outsee", before=snap, after=snap, ledger_usd=1.0)
    assert r["status"] == sr.STATUS_NO_SNAPSHOT and not r["converged"]
    assert "не проверена" in sr.message_ru(r)


async def test_adapter_sync_async_and_failure():
    sr.register_balance_adapter("Kie.ai", lambda: 12.5)
    assert (await sr.take_snapshot("kie")).balance_usd == 12.5

    async def aio():
        return "3.25"
    sr.register_balance_adapter("elevenlabs", aio)
    assert (await sr.take_snapshot("elevenlabs")).balance_usd == 3.25

    def boom():
        raise RuntimeError("кабинет недоступен")
    sr.register_balance_adapter("outsee", boom)
    s = await sr.take_snapshot("outsee")
    assert not s.ok and "кабинет недоступен" in s.error




async def test_window_converges_and_writes_ok_row_without_incident(sessions, tracker):
    balances = iter([10.0, 8.5])
    sr.register_balance_adapter("outsee", lambda: next(balances))
    async with sr.reconcile_window(sessions, "outsee", project_id=7, tracker_db=tracker) as out:
        await add_actual(sessions, 1.0)
        await add_actual(sessions, 0.5)
        await add_actual(sessions, 99.0, provider="kie")
    assert out["result"]["status"] == "converged" and out["incident_id"] is None
    async with sessions() as s:
        rows = (await s.execute(text("SELECT source FROM spend_ledger WHERE kind='event'"))).all()
        assert [r[0] for r in rows] == [sr.SOURCE_OK]
        assert (await s.scalars(select(Incident))).all() == []


async def test_window_gap_creates_incident_and_gap_row(sessions, tracker):
    balances = iter([10.0, 7.0])
    sr.register_balance_adapter("outsee", lambda: next(balances))
    async with sr.reconcile_window(sessions, "outsee", project_id=7, tracker_db=tracker) as out:
        await add_actual(sessions, 1.0)
    r = out["result"]
    assert r["status"] == "gap" and r["gap_usd"] == pytest.approx(2.0)
    async with sessions() as s:
        inc = (await s.scalars(select(Incident))).one()
        assert inc.type == sr.INCIDENT_TYPE and inc.status == "open" and inc.project_id == 7
        assert inc.details["blocking"] is False and "расхождение" in inc.details["fact"]
        assert inc.incident_id == out["incident_id"]
        src = (await s.execute(text("SELECT source, usd FROM spend_ledger WHERE kind='event'"))).one()
        assert src[0] == sr.SOURCE_GAP and src[1] == 0.0


async def test_window_counts_tracker_rows_and_runs_even_if_step_raises(sessions, tracker):
    add_tracker(tracker, 5.0)
    balances = iter([10.0, 9.0])
    sr.register_balance_adapter("vibecode", lambda: next(balances))
    with pytest.raises(RuntimeError):
        async with sr.reconcile_window(sessions, "vibecode", project_id=7, tracker_db=tracker) as out:
            add_tracker(tracker, 1.0)
            add_tracker(tracker, 7.0, provider="kie")
            add_tracker(tracker, 7.0, project="8")
            raise RuntimeError("шаг упал")
    assert out["result"]["tracker_usd"] == pytest.approx(1.0) and out["result"]["converged"]


async def test_same_window_incident_is_not_duplicated(sessions, tracker):
    async with sessions() as s:
        start = await sr.mark(s, tracker_db=tracker)
    b, a = sr.manual_snapshot("outsee", 10), sr.manual_snapshot("outsee", 5)
    async with sessions() as s:
        r1 = await sr.reconcile_now(s, provider="outsee", project_id=7, start=start, before=b, after=a,
                                    tracker_db=tracker)
    async with sessions() as s:
        r2 = await sr.reconcile_now(s, provider="outsee", project_id=7, start=start, before=b, after=a,
                                    tracker_db=tracker)
        assert r1["incident_id"] == r2["incident_id"]
        assert len((await s.scalars(select(Incident))).all()) == 1


async def test_missing_snapshot_opens_warning_incident(sessions, tracker):
    async with sr.reconcile_window(sessions, "outsee", project_id=7, tracker_db=tracker) as out:
        pass
    assert out["result"]["status"] == sr.STATUS_NO_SNAPSHOT
    async with sessions() as s:
        assert (await s.scalars(select(Incident))).one().severity == "warning"




async def test_project_report_and_telemetry_read_it(sessions, tracker, monkeypatch):
    from app.services import api_tracker_hook
    monkeypatch.setattr(api_tracker_hook, "_TRACKER_DB", tracker)
    add_tracker(tracker, 0.4)
    async with sessions() as s:
        await spend_guard.reserve(s, project_id=7, run_id="runA", estimate_usd=2.0, source="planner",
                                  provider="kie", track=False)
        await spend_guard.reserve(s, project_id=7, run_id="runB", estimate_usd=1.0, source="planner",
                                  provider="kie", track=False)
        await spend_guard.settle(s, project_id=7, run_id="runB", reserved_usd=1.0, actual_usd=0.8,
                                 source="planner", provider="kie")
        await spend_guard.record_actual(s, project_id=7, usd=0.5, provider="outsee")
        await s.commit()
    b, a = sr.manual_snapshot("outsee", 4), sr.manual_snapshot("outsee", 3)
    async with sessions() as s:
        start = sr.Mark(0, 0)
        await sr.reconcile_now(s, provider="outsee", project_id=7, start=start, before=b, after=a,
                               tracker_db=tracker)
    async with sessions() as s:
        rep = await sr.project_report(s, 7, tracker_db=tracker)
        assert rep["estimated_usd"] == pytest.approx(3.0)
        assert rep["actual_usd"] == pytest.approx(0.5 - 0.2)
        assert rep["open_reserves_usd"] == pytest.approx(2.0) and rep["open_reserves_runs"] == 1
        assert rep["ledger_total_usd"] == pytest.approx(2.0 + 0.8 + 0.5)
        assert rep["tracker_total_usd"] == pytest.approx(0.4)
        assert rep["reconciliations_total"] == 1 and rep["balance_reconciled"]
        tele = await project_telemetry(s, 7)
    rec = tele["reconcile"]
    assert rec["available"] and rec["estimated_usd"] == rep["estimated_usd"]
    assert any("Открытые резервы" in line for line in rec["lines_ru"])


async def test_report_without_tables_and_summary_without_reconcile(sessions, tracker):
    async with sessions() as s:
        rep = await sr.project_report(s, 99, tracker_db=tracker)
    assert rep["ledger_total_usd"] == 0 and not rep["balance_reconciled"]
    lines = reconcile_summary(rep)["lines_ru"]
    assert "не выполнялась" in lines[-1]
    assert reconcile_summary(None)["available"] is False
