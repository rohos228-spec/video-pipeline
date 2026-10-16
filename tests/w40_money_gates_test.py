from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.services import spend_guard
from app.services.action_layer import approval, budget, executor, service, store
from app.services.action_layer.spec import JobState
from tests.action_layer_support import env, fresh_registry, make_spec

OP = approval.OperatorIdentity("web_operator:w40")
PROJ_SCHEMA = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
               "additionalProperties": False}


@pytest.fixture(autouse=True)
def _clean_executor_globals():
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    executor._ORPHANS.clear()
    executor._CLAIMS.clear()
    yield
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    executor._ORPHANS.clear()
    executor._CLAIMS.clear()


class _StubbornJob:

    def __init__(self) -> None:
        self.stopped = 0

    async def start(self, ctx, params):
        return {"started": True}

    async def poll(self, ctx, params, started):
        return JobState(done=False, progress=0.1, note="идёт")

    async def stop(self, ctx, params):
        self.stopped += 1


async def _unresolved_step(env, monkeypatch) -> tuple[str, int]:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "0.3")
    pid = await env.add_project()
    job = _StubbornJob()
    spec = make_spec("t.stubborn", job.start, risk="paid", spends=True, cost=(1, 5), schema=PROJ_SCHEMA,
                     poll=job.poll, stop=job.stop, poll_interval_s=0.05)
    reg = fresh_registry(spec)
    plan_id, preview = await service.create_plan(
        env.app, [{"action_id": "t.stubborn", "params": {"project_id": pid}}], operator=OP,
        request_text="w40", registry=reg)
    assert plan_id, preview
    await service.approve_plan(env.app, plan_id, OP, registry=reg)
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await executor.wait(plan_id)
    view = await service.plan_view(plan_id)
    assert view["steps"][0]["status"] == "unknown"
    assert view["spent_credits"] == pytest.approx(5.0)
    return plan_id, pid


async def _end_orphan_watch() -> None:
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    await asyncio.sleep(0.05)
    executor._ORPHANS.clear()


async def test_unresolved_reserve_is_marked_once_and_stays_held(env, monkeypatch) -> None:
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    async with env.factory() as s:
        rows = await budget.unresolved_reserves(s, skip_plans={plan_id})
    assert rows == []
    await _end_orphan_watch()
    async with env.factory() as s:
        rows = await budget.unresolved_reserves(s)
    assert [(r["plan_id"], r["step_index"], r["held_credits"]) for r in rows] == [(plan_id, 0, 5.0)]
    monkeypatch.setattr("app.settings.settings.action_layer_reserve_stale_s", 0.001, raising=False)
    await asyncio.sleep(0.01)
    fresh = await executor.mark_stale_reserves()
    again = await executor.mark_stale_reserves()
    assert [r["plan_id"] for r in fresh] == [plan_id] and again == []
    events = [h["event"] for h in (await service.history(plan_id=plan_id))["items"]]
    assert events.count("reserve_unresolved") == 1
    async with env.factory() as s:
        assert await budget.spent(s, budget.session_key(plan_id)) == pytest.approx(5.0)


async def test_operator_releases_unresolved_reserve_with_a_reason(env, monkeypatch) -> None:
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    await _end_orphan_watch()
    async with env.factory() as s:
        with pytest.raises(budget.BudgetError) as e:
            await budget.release_reserve(s, plan_id=plan_id, step_index=0, operator=OP.id, reason=" ")
        assert e.value.code == "reason_required"
        with pytest.raises(budget.BudgetError) as e2:
            await budget.release_reserve(s, plan_id=plan_id, step_index=7, operator=OP.id, reason="сверил кабинет")
        assert e2.value.code == "not_releasable"
    async with env.factory() as s:
        out = await budget.release_reserve(s, plan_id=plan_id, step_index=0, operator=OP.id,
                                           reason="сверил кабинет Kie: списаний нет")
        await s.commit()
    assert out["released_credits"] == pytest.approx(5.0)
    async with env.factory() as s:
        assert await budget.spent(s, budget.session_key(plan_id)) == pytest.approx(0.0)
        assert await budget.unresolved_reserves(s) == []
        rows = (await s.scalars(select(spend_guard.SpendLedger).where(
            spend_guard.SpendLedger.run_id == f"plan:{plan_id}").order_by(spend_guard.SpendLedger.id))).all()
        assert sum(float(r.usd or 0.0) for r in rows) == pytest.approx(0.0)
        assert any("released_by_operator" in (r.note or "") for r in rows)
        with pytest.raises(budget.BudgetError):
            await budget.release_reserve(s, plan_id=plan_id, step_index=0, operator=OP.id, reason="повтор")
    events = [h["event"] for h in (await service.history(plan_id=plan_id))["items"]]
    assert "reserve_released" in events


async def test_reserve_routes_list_and_release(env, monkeypatch) -> None:
    plan_id, _pid = await _unresolved_step(env, monkeypatch)
    await _end_orphan_watch()
    r = await env.client.get("/api/action-layer/reserves")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_held_credits"] == pytest.approx(5.0) and body["items"][0]["plan_id"] == plan_id
    bad = await env.client.post("/api/action-layer/reserves/release",
                                json={"plan_id": plan_id, "step_index": 0, "reason": "  "})
    assert bad.status_code == 422
    ok = await env.client.post("/api/action-layer/reserves/release",
                               json={"plan_id": plan_id, "step_index": 0, "reason": "сверил кабинет Kie"})
    assert ok.status_code == 200, ok.text
    assert (await env.client.get("/api/action-layer/reserves")).json()["items"] == []
    dup = await env.client.post("/api/action-layer/reserves/release",
                                json={"plan_id": plan_id, "step_index": 0, "reason": "сверил кабинет Kie"})
    assert dup.status_code == 409 and dup.json()["detail"]["code"] == "not_releasable"



def test_default_caps_are_high_fuses_and_the_loop_counters_stay_on() -> None:
    from app.settings import Settings
    fields = Settings.model_fields
    daily, project, run = (fields[k].default for k in ("spend_daily_cap_usd", "spend_project_cap_usd", "spend_run_cap_usd"))
    assert daily >= 200 and project >= 100 and run >= 50 and run <= project <= daily
    assert fields["action_layer_session_cap_credits"].default >= 10000
    assert fields["regen_max_per_frame"].default >= 1 and fields["regen_max_per_run"].default >= 1


def test_spend_limits_doc_table_matches_the_settings() -> None:
    import re
    from pathlib import Path
    from app.settings import Settings
    doc = (Path(__file__).resolve().parent.parent / "docs" / "SPEND_LIMITS.md").read_text(encoding="utf-8")
    for env_name, field in (("SPEND_DAILY_CAP_USD", "spend_daily_cap_usd"), ("SPEND_PROJECT_CAP_USD", "spend_project_cap_usd"),
                            ("SPEND_RUN_CAP_USD", "spend_run_cap_usd"), ("REGEN_MAX_USD_PER_RUN", "regen_max_usd_per_run")):
        row = re.search(rf"\| `{env_name}` \| ([0-9.]+) \|", doc)
        assert row, f"{env_name}: нет строки в docs/SPEND_LIMITS.md"
        assert float(row.group(1)) == float(Settings.model_fields[field].default), env_name
    assert "никогда не отклоняются" not in doc
