"""W2-3 (B02 + B03): корреляция трат (проект, запуск, запрос) и единая модель траты.

Класс доказательства: U/D на заменителях провайдера и сети. Живых прогонов нет: ни сети, ни платных вызовов,
ни боевой базы. Все базы лежат во временных каталогах.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.services import api_tracker_hook as hook
from app.services import media_spend, request_context as rc, spend_guard
from app.services.observation import _cost_project
from app.settings import settings
from tests.action_layer_support import env  # noqa: F401


@pytest.fixture(autouse=True)
def _clean_state():
    media_spend.reset_gap_state()
    yield
    media_spend.reset_gap_state()


@pytest_asyncio.fixture
async def ledger(tmp_path, monkeypatch):
    """Настоящий spend_guard на временной SQLite; media_spend пишет в неё через app.db.SessionLocal."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ledger.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    monkeypatch.setattr("app.db.SessionLocal", sessions)
    monkeypatch.setattr(media_spend, "TEST_FORCE", True)
    monkeypatch.setattr(media_spend, "_provider_key", lambda provider: "real-looking-key")
    monkeypatch.setattr(settings, "media_spend_recording", True)
    monkeypatch.setattr(settings, "gpt_api_key", "real-looking-key")
    monkeypatch.setattr(settings, "vibecode_api_key", "real-looking-key")
    async with sessions() as s:  # таблицы заранее: первые записи из разных задач не гоняются за CREATE TABLE
        await spend_guard.ensure_schema(s)
        await spend_guard.ensure_task_schema(s)
    yield sessions
    await engine.dispose()


async def _rows(sessions):
    await media_spend.drain()
    await spend_guard_drain()
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        return (await s.scalars(select(spend_guard.SpendLedger).order_by(spend_guard.SpendLedger.id))).all()


async def spend_guard_drain():
    while spend_guard._tasks:
        await asyncio.gather(*list(spend_guard._tasks), return_exceptions=True)


# ------------------------------------------------------------------ request_context (B02 основа)

def test_request_scope_nests_and_restores():
    assert rc.get_request_id() == "" and rc.get_run_id() == "" and rc.get_project_id() is None
    with rc.request_scope("req-1", run_id="exec-9", project_id=4):
        assert rc.correlation() == {"project_id": 4, "run_id": "exec-9", "request_id": "req-1"}
        with rc.ensure_request_scope("op-", project_id=5):
            # запрос оператора сохраняется, проект и запуск внутренней операции уточняются
            assert rc.get_request_id() == "req-1" and rc.get_project_id() == 5 and rc.get_run_id() == "exec-9"
        assert rc.get_project_id() == 4
    assert rc.get_request_id() == "" and rc.get_run_id() == "" and rc.get_project_id() is None


def test_ensure_request_scope_creates_an_id_when_there_is_no_request():
    with rc.ensure_request_scope("tick-", run_id=rc.native_run_id(3), project_id=3) as snap:
        assert snap["request_id"].startswith("tick-") and len(snap["request_id"]) <= 64
        assert snap["run_id"].startswith("native:3:")
        assert _cost_project.get() == 3  # старый путь чтения тоже видит проект
    assert rc.get_request_id() == "" and _cost_project.get() is None


def test_bind_run_and_unbind_run_drop_only_what_they_created():
    rid = rc.set_request_id("operator-req")
    try:
        rc.bind_run("plan:abc", 8, "plan-")
        assert rc.get_run_id() == "plan:abc" and rc.get_project_id() == 8 and rc.get_request_id() == "operator-req"
        rc.unbind_run("plan:other")  # чужой запуск не снимается
        assert rc.get_run_id() == "plan:abc"
        rc.unbind_run("plan:abc")
        assert rc.get_run_id() == "" and rc.get_project_id() is None and rc.get_request_id() == "operator-req"
    finally:
        rc.reset_request_id(rid)
    rc.bind_run("plan:z", 1, "plan-")
    assert rc.get_request_id().startswith("plan-")
    rc.unbind_run()
    assert rc.get_request_id() == ""


# ------------------------------------------------------------------ B02: нативные строки получают проект/запуск/запрос

async def test_native_media_rows_carry_project_run_request_and_ref(ledger):
    with rc.ensure_request_scope("tick-", run_id="exec-77", project_id=7):
        media_spend.record("elevenlabs", usd=0.3, note="tts chars=10 uncalibrated")
        media_spend._after_create_ok("jobs/createTask", "TASK-1")
        media_spend.record("outsee", usd=0.0, unknown=True, note="image m ok")
        rid = rc.get_request_id()
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    assert len(rows) == 3
    for r in rows:
        assert r.project_id == 7 and r.run_id == "exec-77" and r.request_id == rid
    assert {r.ref for r in rows if r.provider == "kie"} == {"TASK-1"}
    async with ledger() as s:
        task_rows = (await s.scalars(select(spend_guard.ProviderTaskLedger))).all()
        assert len(task_rows) == 1
        assert task_rows[0].task_id == "TASK-1" and task_rows[0].run_id == "exec-77" and task_rows[0].request_id == rid
        assert await spend_guard.correlation_gaps(s) == []


async def test_row_without_context_is_traced_not_hidden(ledger):
    media_spend.record("elevenlabs", usd=0.1, note="tts")
    rows = await _rows(ledger)
    assert rows[0].project_id is None and rows[0].run_id == "" and rows[0].request_id == ""
    assert media_spend.uncorrelated_counts() == {"elevenlabs": 1}
    async with ledger() as s:
        assert len(await spend_guard.correlation_gaps(s)) == 1


async def test_background_write_keeps_the_callers_ids_not_the_later_ones(ledger):
    with rc.ensure_request_scope("req-", run_id="exec-1", project_id=1):
        media_spend.record("elevenlabs", usd=0.2, note="a")
        first = rc.get_request_id()
    with rc.ensure_request_scope("req-", run_id="exec-2", project_id=2):
        media_spend.record("elevenlabs", usd=0.2, note="b")
    rows = await _rows(ledger)
    assert [(r.project_id, r.run_id) for r in rows] == [(1, "exec-1"), (2, "exec-2")]
    assert rows[0].request_id == first and rows[1].request_id != first


async def test_old_ledger_tables_get_the_new_columns_and_stay_append_only(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'old.db'}")
    old_sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TABLE spend_ledger (id INTEGER PRIMARY KEY, created_at DATETIME, day VARCHAR(10) NOT NULL, "
            "kind VARCHAR(12) NOT NULL, provider VARCHAR(32), project_id INTEGER, run_id VARCHAR(80), "
            "source VARCHAR(32), usd FLOAT, credits FLOAT, note VARCHAR(200))")
        await conn.exec_driver_sql(
            "CREATE TABLE provider_task_ledger (id INTEGER PRIMARY KEY, created_at DATETIME, project_id INTEGER, "
            "key VARCHAR(120), task_id VARCHAR(100) NOT NULL, provider VARCHAR(32), api VARCHAR(16), model VARCHAR(64), "
            "event VARCHAR(16), status VARCHAR(16), est_credits FLOAT, est_usd FLOAT, note VARCHAR(200))")
        await conn.exec_driver_sql(
            "INSERT INTO spend_ledger (day, kind, provider, project_id, run_id, source, usd, credits, note) "
            "VALUES ('2026-01-01', 'actual', 'kie', 3, '', 'media_native', 0.5, 0, 'old row')")
    spend_guard._ensured.discard(str(engine.url))
    spend_guard._task_ensured.discard(str(engine.url))
    async with old_sessions() as s:
        await spend_guard.ensure_schema(s)
        await spend_guard.ensure_task_schema(s)
        await spend_guard.record_actual(s, project_id=3, usd=0.1, provider="kie", run_id="r", request_id="q1", ref="t")
        await s.commit()
        cols = {r[1] for r in (await s.execute(text("PRAGMA table_info(spend_ledger)"))).all()}
        assert {"request_id", "ref"} <= cols
        tcols = {r[1] for r in (await s.execute(text("PRAGMA table_info(provider_task_ledger)"))).all()}
        assert {"run_id", "request_id"} <= tcols
        old = (await s.scalars(select(spend_guard.SpendLedger).order_by(spend_guard.SpendLedger.id))).all()
        assert old[0].note == "old row" and old[0].request_id == "" and old[1].request_id == "q1"
        with pytest.raises(Exception, match="append-only"):
            await s.execute(text("UPDATE spend_ledger SET usd = 0"))
        await s.rollback()
    # повторный вызов ничего не ломает
    spend_guard._ensured.discard(str(engine.url))
    async with old_sessions() as s:
        await spend_guard.ensure_schema(s)
    await engine.dispose()


# ------------------------------------------------------------------ B03: единая модель траты

def test_unified_kind_mapping():
    uk = spend_guard.unified_kind
    assert uk("estimated", "media_native", "tts chars=3 uncalibrated") == "estimated"
    assert uk("estimated", "media_native", "image m ok cost_unknown=true") == "estimated"
    assert uk("actual", "media_native", "create jobs m rejected http=422") == "actual"
    assert uk("actual", "media_native", "tts chars=3 uncalibrated") == "estimated"  # старая строка: не факт
    assert uk("actual", "native_call", "basis=computed uncalibrated") == "estimated"
    assert uk("settle", "media_native", "basis=reported") == "actual"
    assert uk("settle", "media_native", "") == "estimated"
    assert uk("settle", "action_layer", "basis=closes_reserve") == "reserved"
    assert uk("reserve", "action_layer", "") == "reserved"
    assert uk("event", "write_failed", "x") == "gap"
    assert uk("event", "media_402", "x") == "event"
    assert spend_guard.execution_of("exec-5") == "exec-5"
    assert spend_guard.execution_of("plan:abc") is None and spend_guard.execution_of("native:1:ab") is None
    assert spend_guard.execution_of("") is None


async def test_no_actual_row_is_marked_uncalibrated_after_a_synthetic_run(ledger, monkeypatch):
    """Все пути, которые раньше могли записать расчётную цену как факт: ElevenLabs, Vibecode/Kie по токенам, Outsee."""
    from app.services import observation

    with rc.ensure_request_scope("tick-", run_id="exec-1", project_id=9):
        media_spend.record("elevenlabs", usd=0.3, note="tts chars=10 uncalibrated")
        media_spend.record("elevenlabs", usd=0.12, note="sfx dur=3 uncalibrated")
        media_spend.record("outsee", usd=0.0, unknown=True, note="video m ok")
        media_spend.record("kie", usd=0.0, note="create jobs m rejected http=422")  # отказ: факт 0
        observation._track_api_cost(model="vibecode-model", usage={"prompt_tokens": 1000, "completion_tokens": 100},
                                    duration_sec=1.0, provider="vibecode")
        observation._track_api_cost(model="gpt-5.6-sol", usage={"prompt_tokens": 1000, "completion_tokens": 100},
                                    duration_sec=1.0, provider="kie")
        hook.flush(10)
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    assert len(rows) == 6
    for r in rows:
        low = r.note.lower()
        assert not (r.kind == "actual" and ("uncalibrated" in low or "cost_unknown" in low or "computed" in low)), r.note
        assert spend_guard.unified_kind(r.kind, r.source, r.note) != "actual" or r.usd == 0.0
    vib = [r for r in rows if r.provider == "vibecode"][0]
    assert vib.kind == "estimated" and "uncalibrated" in vib.note
    kie_text = [r for r in rows if r.source == "native_call" and r.provider == "kie"][0]
    assert kie_text.kind == "estimated"
    assert [r.kind for r in rows if "rejected" in r.note] == ["actual"]
    async with ledger() as s:
        assert await spend_guard.correlation_gaps(s) == []
        assert media_spend.uncorrelated_counts() == {}


async def test_provider_reported_eleven_amount_is_a_fact_row(ledger):
    class Resp:
        status_code = 200
        headers = {"character-cost": "20"}

    async def post():
        return Resp()

    with rc.ensure_request_scope("tick-", run_id="exec-1", project_id=2):
        await media_spend.track_eleven_post(post(), chars=10, model="m")
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    est = [r for r in rows if r.kind == "estimated"][0]
    settle = [r for r in rows if r.kind == "settle"][0]
    assert "uncalibrated" in est.note and spend_guard.unified_kind(est.kind, est.source, est.note) == "estimated"
    assert "basis=reported" in settle.note and spend_guard.unified_kind(settle.kind, settle.source, settle.note) == "actual"
    assert settle.run_id == "exec-1" and settle.request_id == est.request_id and settle.project_id == 2
    assert sum(r.usd for r in rows) == pytest.approx(20 * media_spend.ELEVENLABS_USD_PER_CHAR)  # один вызов - одна сумма


async def test_run_totals_net_a_closed_reserve(ledger):
    async with ledger() as s:
        with rc.ensure_request_scope("req-", run_id="plan:p1", project_id=3):
            await spend_guard.reserve(s, project_id=3, run_id="plan:p1", estimate_usd=1.0, source="action_layer",
                                      credits=200, track=False, ref="plan:p1#0")
            await spend_guard.record_actual(s, project_id=3, usd=0.4, provider="kie", source="media_native",
                                            kind="estimated", note="create x")
            await spend_guard.settle(s, project_id=3, run_id="plan:p1", reserved_usd=1.0, actual_usd=0.0,
                                     source="action_layer", basis="closes_reserve")
        await s.commit()
        totals = await spend_guard.run_totals(s, "plan:p1")
    assert totals["total_usd"] == pytest.approx(0.4)
    assert totals["reserved_usd"] == pytest.approx(0.0)
    assert totals["estimated_usd"] == pytest.approx(0.4)
    assert totals["rows"] == 3


# ------------------------------------------------------------------ B02: цепочка одним запросом

async def test_chain_operator_plan_step_money_task_in_one_query(tmp_path):
    from app.models import Base
    from app.services import operator_audit
    from app.services.action_layer import store

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chain.db'}")
    sessions = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(
            c, tables=[operator_audit.OperatorAudit.__table__, store.ActionHistory.__table__]))
    spend_guard._ensured.discard(str(engine.url))
    spend_guard._task_ensured.discard(str(engine.url))
    async with sessions() as s:
        await spend_guard.ensure_schema(s)
        await spend_guard.ensure_task_schema(s)
        s.add(operator_audit.OperatorAudit(request_id="req-A", method="POST", path="/api/action-layer/plans/p1/run",
                                           project_id=6, status_code=200, outcome="ok"))
        store.append_history(s, event="step_started", plan_id="p1", step_index=0, project_id=6,
                             execution_id=None, request_id="req-A")
        await spend_guard.record_actual(s, project_id=6, usd=0.2, provider="kie", source="media_native",
                                        kind="estimated", note="create", run_id="plan:p1", request_id="req-A",
                                        ref="TASK-9")
        await spend_guard.append_task_event(s, project_id=6, key="k", task_id="TASK-9", provider="kie", api="jobs",
                                            model="m", event="submitted", status="submitted", run_id="plan:p1",
                                            request_id="req-A")
        await spend_guard.record_actual(s, project_id=6, usd=9.0, provider="kie", request_id="req-OTHER")
        await s.commit()
        chain = await spend_guard.correlation_chain(s, "req-A")
        assert await spend_guard.correlation_chain(s, "") == []
    assert [c["stage"] for c in chain] == ["operator", "plan_step", "money", "provider_task"] or \
        {c["stage"] for c in chain} == {"operator", "plan_step", "money", "provider_task"}
    assert {c["request_id"] for c in chain} == {"req-A"}
    money = [c for c in chain if c["stage"] == "money"][0]
    task = [c for c in chain if c["stage"] == "provider_task"][0]
    assert money["ref"] == "TASK-9" == task["ref"] and money["run_id"] == "plan:p1" == task["run_id"]
    assert [c for c in chain if c["stage"] == "plan_step"][0]["ref"] == "p1#0"
    await engine.dispose()


# ------------------------------------------------------------------ B02: трекер вызовов

def test_tracker_row_metadata_carries_ids_of_the_caller(monkeypatch, tmp_path):
    import json
    import sqlite3

    monkeypatch.setattr(hook, "_TRACKER_DB", tmp_path / "tracker.db")
    with rc.ensure_request_scope("tick-", run_id="plan:p7", project_id=12):
        rid = rc.get_request_id()
        hook.record_api_call(provider="kie", model="gpt-5.6-sol", prompt_tokens=10, completion_tokens=1)
    assert hook.flush(10)
    with sqlite3.connect(tmp_path / "tracker.db") as c:
        meta_json, source = c.execute("SELECT metadata_json, project_source FROM api_calls").fetchone()
    meta = json.loads(meta_json)
    assert meta["run_id"] == "plan:p7" and meta["plan_id"] == "p7" and meta["request_id"] == rid
    assert meta["project_id"] == 12 and source == "12"


# ------------------------------------------------------------------ B02: точки входа

def test_tick_run_id_is_the_execution_or_a_native_tick(monkeypatch):
    from app.services import actions_v1, advance_runner

    monkeypatch.setattr(actions_v1, "get_permission", lambda pid: None)
    a, b = advance_runner._tick_run_id(5), advance_runner._tick_run_id(5)
    assert a.startswith("native:5:") and a != b
    monkeypatch.setattr(actions_v1, "get_permission", lambda pid: SimpleNamespace(execution_id="exec-42"))
    assert advance_runner._tick_run_id(5) == "exec-42"


async def test_advance_job_sets_project_run_and_request_for_the_whole_tick(monkeypatch):
    from app.services import actions_v1, advance_runner

    seen: dict = {}

    async def admit(project_id):
        return SimpleNamespace(allowed=True)

    class _Boom:
        async def __aenter__(self):
            seen.update(rc.correlation())
            raise RuntimeError("stop here: the context was captured")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(actions_v1, "worker_entry_admission", admit)
    monkeypatch.setattr(actions_v1, "get_permission", lambda pid: None)
    monkeypatch.setattr(advance_runner, "project_db_session_scope", lambda pid: _Boom())
    with pytest.raises(RuntimeError, match="context was captured"):
        await advance_runner.advance_project_job(31, bot=None)
    assert seen["project_id"] == 31 and seen["run_id"].startswith("native:31:") and seen["request_id"].startswith("tick-")
    assert rc.correlation() == {"project_id": None, "run_id": "", "request_id": ""}  # после такта всё снято


async def test_generation_queue_attributes_spend_to_the_project(ledger, monkeypatch):
    from app.models import ProjectStatus
    from app.orchestrator.auto_advance import TRANSITIONS
    from app.services import gen_queue, project_control

    status = next(iter(TRANSITIONS))
    project = SimpleNamespace(id=55, status=status, auto_mode=True, meta={})
    assert isinstance(status, ProjectStatus)

    async def advance(session, proj):
        media_spend.record("kie", usd=0.05, note="create jobs m task=Q1", ref="Q1")
        return True

    monkeypatch.setattr(gen_queue, "_advance_ready_project", advance)
    monkeypatch.setattr(project_control, "auto_awaits_manual_start", lambda p: False)
    assert await gen_queue._start_or_advance_project(None, project, queue_pos=1) == 1
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    assert len(rows) == 1
    assert rows[0].project_id == 55 and rows[0].run_id.startswith("queue:55:") and rows[0].request_id.startswith("queue-")
    assert rows[0].ref == "Q1"
    assert rc.get_project_id() is None and _cost_project.get() is None


async def test_regeneration_attributes_spend_to_the_project(ledger, monkeypatch, tmp_path):
    from app.services import montage_board_regen as mbr

    prep = mbr.ImageRegenPrep(project_id=61, frame_number=1, shot=1, prompt_text="p", file_path=tmp_path / "f.png")
    monkeypatch.setattr(mbr, "_image_api_enabled", lambda: True)
    monkeypatch.setattr(mbr, "get_gpt_client", lambda: object())

    async def fake_generate(*a, **kw):
        media_spend.record("outsee", usd=0.0, unknown=True, note="image m ok")
        return SimpleNamespace(file_path=str(tmp_path / "f.png"))

    monkeypatch.setattr(mbr, "generate_image_with_retries", fake_generate)
    await mbr.execute_image_regen(prep)
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    assert len(rows) == 1
    assert rows[0].project_id == 61 and rows[0].run_id.startswith("regen:61:") and rows[0].request_id.startswith("regen-")
    assert rc.get_project_id() is None


async def test_regeneration_inside_a_plan_keeps_the_plan_run(ledger, monkeypatch, tmp_path):
    from app.services import montage_board_regen as mbr

    prep = mbr.ImageRegenPrep(project_id=62, frame_number=1, shot=1, prompt_text="p", file_path=tmp_path / "f.png")
    monkeypatch.setattr(mbr, "_image_api_enabled", lambda: True)
    monkeypatch.setattr(mbr, "get_gpt_client", lambda: object())

    async def fake_generate(*a, **kw):
        media_spend.record("outsee", usd=0.0, unknown=True, note="image m ok")
        return SimpleNamespace(file_path=str(tmp_path / "f.png"))

    monkeypatch.setattr(mbr, "generate_image_with_retries", fake_generate)
    with rc.request_scope("operator-req", run_id="plan:pp", project_id=62):
        await mbr.execute_image_regen(prep)
    rows = [r for r in await _rows(ledger) if r.kind != "event"]
    assert rows[0].run_id == "plan:pp" and rows[0].request_id == "operator-req" and rows[0].project_id == 62


# ------------------------------------------------------------------ B02+B03: слой действий

async def test_action_layer_step_native_spend_is_one_logical_spend_per_run(env, ledger, monkeypatch):
    """Шаг плана: резерв слоя + нативная трата + закрытие резерва. Все строки с проектом, запуском plan:<id> и
    запросом; сумма по запуску равна нативной трате (не удвоена); в конце контекст шага снят."""
    from app.services.action_layer import approval, executor, service, store
    from tests.action_layer_support import fresh_registry, make_spec

    op = approval.OperatorIdentity("web_operator:w23")
    monkeypatch.setattr("app.db.SessionLocal", env.factory)
    pid = await env.add_project()
    schema = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
              "additionalProperties": False}
    seen: dict = {}

    async def paid(ctx, params):
        seen.update(rc.correlation())
        media_spend.record("kie", usd=0.02, credits=4.0, note="create jobs m task=PL1", ref="PL1")
        await media_spend.drain()
        return {"ok": True}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(2, 10), schema=schema))
    plan_id, preview = await service.create_plan(env.app, [{"action_id": "t.paid", "params": {"project_id": pid}}],
                                                 operator=op, request_text="w2-3", registry=reg)
    assert plan_id, preview
    await service.approve_plan(env.app, plan_id, op, registry=reg)
    await service.prepare_run(plan_id, op, registry=reg)
    executor.launch(env.app, plan_id, op.id, registry=reg)
    await executor.wait(plan_id)
    run = f"plan:{plan_id}"
    assert seen["project_id"] == pid and seen["run_id"] == run and seen["request_id"].startswith("plan-")
    async with env.factory() as s:
        rows = [r for r in (await s.scalars(select(spend_guard.SpendLedger).order_by(spend_guard.SpendLedger.id))).all()
                if r.run_id == run]
        kinds = [(r.source, r.kind) for r in rows]
        assert ("action_layer", "reserve") in kinds and ("media_native", "estimated") in kinds
        for r in rows:
            assert r.project_id == pid and r.request_id == seen["request_id"]
        reserve_row = [r for r in rows if r.kind == "reserve"][0]
        assert reserve_row.ref == f"{run}#0"
        native = [r for r in rows if r.source == "media_native"][0]
        assert native.ref == "PL1"
        totals = await spend_guard.run_totals(s, run)
        assert await spend_guard.correlation_gaps(s) == []
    assert totals["total_usd"] == pytest.approx(0.02)  # одна логическая трата по запуску
    assert rc.get_run_id() == "" and rc.get_project_id() is None  # привязка шага снята после сверки
