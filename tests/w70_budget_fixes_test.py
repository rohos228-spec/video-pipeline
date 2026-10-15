"""W70: K9 (двойной учёт резерва слоя и нативных строк), K10 (атомарное снятие резерва), K11 (автосверка баланса
не выдаёт траты вне окна за расхождение). Сети и денег нет: базы временные, адаптеры подменены."""
from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models import Base
from app.services import balance_adapters as ba, spend_guard, spend_reconcile as sr
from app.services.action_layer import budget
from app.services.diagnosis import Incident
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401
from tests.w40_money_gates_test import (OP, _clean_executor_globals, _end_orphan_watch,  # noqa: F401
                                        _unresolved_step)


@pytest.fixture
async def sessions(tmp_path, monkeypatch):
    from app.services import actions_v1  # noqa: F401
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'w70.db'}", poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    spend_guard._ensured.clear()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(spend_guard, "_sessions", lambda: factory)
    from app.services import api_tracker_hook
    monkeypatch.setattr(api_tracker_hook, "_TRACKER_DB", tmp_path / "no_tracker.db")
    sr._adapters.clear()
    ba._active.clear()
    yield factory
    sr._adapters.clear()
    ba._active.clear()
    await engine.dispose()


# ------------------------------------------------------------------ K9


async def _rows(s, rows):
    await spend_guard.ensure_schema(s)
    for kind, source, run, usd, pid in rows:
        s.add(spend_guard.SpendLedger(day=spend_guard._today(), kind=kind, source=source, run_id=run, usd=usd,
                                      project_id=pid, provider="kie"))
    await s.commit()


async def test_k9_plan_reserve_and_native_rows_are_counted_once(sessions):
    async with sessions() as s:
        await _rows(s, [("reserve", "action_layer", "plan:p1", 1.0, 5),
                        ("actual", "native_call", "plan:p1", 0.9, 5)])
        sums = await spend_guard.conservative_sums(s, project_id=5, run_id="plan:p1")
    assert sums["run"] == pytest.approx(1.0)
    assert sums["project"] == pytest.approx(1.0)
    assert sums["daily"] == pytest.approx(1.0)


async def test_k9_native_above_reserve_and_other_runs_still_add_up(sessions):
    async with sessions() as s:
        await _rows(s, [("reserve", "action_layer", "plan:p1", 1.0, 5),
                        ("actual", "native_call", "plan:p1", 1.4, 5),       # факт выше резерва: считается факт
                        ("reserve", "action_layer", "plan:p2", 2.0, 5),
                        ("actual", "native_call", "native:t1", 0.5, 5),     # не план: суммируется как раньше
                        ("reserve", "managed", "exec-1", 0.25, 5)])
        sums = await spend_guard.conservative_sums(s, project_id=5, run_id="plan:p1")
    assert sums["run"] == pytest.approx(1.4)
    assert sums["project"] == pytest.approx(1.4 + 2.0 + 0.5 + 0.25)


async def test_k9_closed_reserve_with_native_counted_stays_exact(sessions):
    async with sessions() as s:
        await _rows(s, [("reserve", "action_layer", "plan:p1", 1.0, 5),
                        ("settle", "action_layer", "plan:p1", -1.0, 5),     # native_counted: резерв закрыт нулём
                        ("actual", "native_call", "plan:p1", 0.9, 5)])
        sums = await spend_guard.conservative_sums(s, project_id=5, run_id="plan:p1")
    assert sums["run"] == pytest.approx(0.9)


async def test_k9_paid_gate_no_false_refusal_for_own_plan(sessions, monkeypatch):
    from app.services import kill_switch
    monkeypatch.setattr(settings, "spend_run_cap_usd", 1.5)
    monkeypatch.setattr(settings, "spend_project_cap_usd", 0.0)
    monkeypatch.setattr(settings, "spend_daily_cap_usd", 0.0)

    async def no_guard(_factory):
        return None
    monkeypatch.setattr(kill_switch, "guard_native", no_guard)
    spend_guard.reset_inflight()
    async with sessions() as s:
        await _rows(s, [("reserve", "action_layer", "plan:p1", 1.0, 5),
                        ("actual", "native_call", "plan:p1", 0.9, 5)])
    ticket = await spend_guard.paid_gate(provider="kie", what="t", estimate_usd=0.3, project_id=5,
                                         run_id="plan:p1", register=False)
    assert ticket.estimate_usd == pytest.approx(0.3)           # было 1.9 + 0.3 > 1.5: ложный отказ
    with pytest.raises(spend_guard.PaidCallBlocked):            # настоящий потолок по-прежнему держится
        await spend_guard.paid_gate(provider="kie", what="t", estimate_usd=0.6, project_id=5,
                                    run_id="plan:p1", register=False)


# ------------------------------------------------------------------ K10


async def test_k10_second_release_cannot_double_release(env, monkeypatch):  # noqa: F811
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    await _end_orphan_watch()
    # Оба вызова прошли проверку «резерв есть» до записи (двойной клик / повтор без ключа): подсовываем устаревший ответ.
    async with env.factory() as s:
        stale = await budget.unresolved_reserves(s)
    assert len(stale) == 1

    async def stale_rows(_session, **_kw):
        return list(stale)
    async with env.factory() as s:
        monkeypatch.setattr(budget, "unresolved_reserves", stale_rows)
        out = await budget.release_reserve(s, plan_id=plan_id, step_index=0, operator=OP.id, reason="сверил кабинет")
        await s.commit()
    assert out["released_credits"] == pytest.approx(5.0)
    async with env.factory() as s:
        with pytest.raises(budget.BudgetError) as e:
            await budget.release_reserve(s, plan_id=plan_id, step_index=0, operator=OP.id, reason="второй клик")
        assert e.value.code == "not_releasable"
        await s.rollback()
    monkeypatch.undo()
    async with env.factory() as s:
        assert await budget.spent(s, budget.session_key(plan_id)) == pytest.approx(0.0)  # не ушло в минус
        rows = (await s.scalars(select(spend_guard.SpendLedger).where(
            spend_guard.SpendLedger.run_id == f"plan:{plan_id}"))).all()
        assert sum(float(r.usd or 0.0) for r in rows) == pytest.approx(0.0)
        assert sum(1 for r in rows if "released_by_operator" in (r.note or "")) == 1


# ------------------------------------------------------------------ K11


def _snap(v):
    return sr.manual_snapshot("outsee", v)


def test_k11_compare_foreign_spend_makes_gap_inconclusive():
    r = sr.compare(provider="outsee", before=_snap(10.0), after=_snap(8.0), ledger_usd=1.0, foreign_usd=1.0)
    assert r["status"] == sr.STATUS_INCONCLUSIVE and not r["converged"]
    assert "не доказано" in sr.message_ru(r)


def test_k11_compare_real_gap_without_foreign_spend_is_still_a_gap():
    r = sr.compare(provider="outsee", before=_snap(10.0), after=_snap(8.0), ledger_usd=1.0)
    assert r["status"] == sr.STATUS_GAP


def test_k11_compare_converged_stays_converged_with_foreign_spend():
    r = sr.compare(provider="outsee", before=_snap(10.0), after=_snap(9.0), ledger_usd=1.0, foreign_usd=5.0)
    assert r["status"] == sr.STATUS_CONVERGED


def _balances(monkeypatch, values, hook=None):
    from app.bots import outsee_http
    it = iter(values)
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        if hook is not None:
            await hook(calls["n"])
        return {"balance_usd": next(it)}
    monkeypatch.setattr(outsee_http, "fetch_balance", fetch)


async def _record(sessions, project, usd):
    async with sessions() as s:
        await spend_guard.record_actual(s, project_id=project, usd=usd, provider="outsee")
        await s.commit()


async def _window(monkeypatch, sessions, values, *, during=None, hook=None):
    monkeypatch.setattr(settings, "spend_reconcile_auto", True)
    monkeypatch.setattr(settings, "image_provider", "outsee")
    _balances(monkeypatch, values, hook)
    win = await ba.open_auto_window("generating_images", 7)
    assert win is not None
    if during:
        await during()
    return await ba.close_auto_window(win)


async def test_k11_spend_of_another_project_in_window_is_inconclusive_no_incident(monkeypatch, sessions):
    async def during():
        await _record(sessions, 7, 0.5)
        await _record(sessions, 99, 0.5)       # тот же провайдер, чужой проект: баланс упал на 1.0
    res = await _window(monkeypatch, sessions, [10.0, 9.0], during=during)
    assert res["status"] == sr.STATUS_INCONCLUSIVE and res["foreign_usd"] == pytest.approx(0.5)
    async with sessions() as s:
        assert (await s.scalars(select(Incident))).all() == []
        src = [r[0] for r in (await s.execute(text("SELECT source FROM spend_ledger WHERE kind='event'"))).all()]
        rep = await sr.project_report(s, 7)
    assert src == [sr.SOURCE_INCONCLUSIVE]
    assert rep["reconciliations_gap"] == 0 and rep["reconciliations_inconclusive"] == 1
    assert rep["balance_reconciled"] is False


async def test_k11_spend_between_window_mark_and_before_snapshot_is_inconclusive(monkeypatch, sessions):
    async def hook(n):
        if n == 1:                              # внутри снимка «до»: запись уже в окне, а в балансе её нет
            await _record(sessions, 7, 0.5)
    res = await _window(monkeypatch, sessions, [10.0, 10.0], hook=hook)
    assert res["status"] == sr.STATUS_INCONCLUSIVE and res["boundary_usd"] == pytest.approx(0.5)


async def test_k11_plain_gap_in_clean_window_is_still_reported(monkeypatch, sessions):
    async def during():
        await _record(sessions, 7, 0.5)
    res = await _window(monkeypatch, sessions, [10.0, 9.0], during=during)
    assert res["status"] == sr.STATUS_GAP and res["incident_id"]
