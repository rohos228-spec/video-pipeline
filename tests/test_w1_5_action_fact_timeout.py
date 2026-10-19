from __future__ import annotations

import asyncio
import hashlib

import pytest
from sqlalchemy import func, select

from app.services import kill_switch, spend_guard
from app.services.action_layer import (
    approval, attachments, budget, builtin_montage as bm, executor, service, store)
from app.services.action_layer import builtin_montage_more as bmm
from app.services.action_layer.spec import ActionSpec, JobState, ensure_builtin_actions, registry
from tests.action_layer_support import env, fresh_registry, make_spec

OP = approval.OperatorIdentity("web_operator:w15")


@pytest.fixture(autouse=True)
def _clean_executor_globals():
    executor._CLAIMS.clear()
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    executor._ORPHANS.clear()
    yield
    executor._CLAIMS.clear()
    for info in list(executor._ORPHANS.values()):
        task = info.get("task")
        if task is not None:
            task.cancel()
    executor._ORPHANS.clear()
PROJ_SCHEMA = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1}},
               "additionalProperties": False}


async def _plan(env, reg, *steps):
    plan_id, preview = await service.create_plan(env.app, list(steps), operator=OP,
                                                 request_text="w1-5", registry=reg)
    assert plan_id, preview
    return plan_id


async def _go(env, reg, plan_id, *, approve=False):
    if approve:
        await service.approve_plan(env.app, plan_id, OP, registry=reg)
    await service.prepare_run(plan_id, OP, registry=reg)
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await executor.wait(plan_id)
    return await service.plan_view(plan_id)


async def _ledger_sum(env, *, source=None, project_id=None, run_id=None, column="credits"):
    col = getattr(spend_guard.SpendLedger, column)
    q = select(func.coalesce(func.sum(col), 0.0))
    if source:
        q = q.where(spend_guard.SpendLedger.source == source)
    if project_id is not None:
        q = q.where(spend_guard.SpendLedger.project_id == project_id)
    if run_id:
        q = q.where(spend_guard.SpendLedger.run_id == run_id)
    async with env.factory() as s:
        await spend_guard.ensure_schema(s)
        return float(await s.scalar(q) or 0.0)


async def _native_row(env, project_id, credits, note="kie submit"):
    async with env.factory() as s:
        await spend_guard.record_actual(s, project_id=project_id, usd=credits * 0.005,
                                        provider="kie", source="media_native", note=note,
                                        credits=credits)
        await s.commit()



async def test_measured_native_spend_closes_reserve_without_double_counting(env) -> None:
    pid = await env.add_project()

    async def paid(ctx, params):
        await _native_row(env, params["project_id"], 4.0)
        return {"ok": True}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(2, 10),
                                   schema=PROJ_SCHEMA))
    plan_id = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert view["status"] == "succeeded"
    step = view["steps"][0]
    assert step["cost"]["actual"] == pytest.approx(4.0)
    assert view["actual_credits"] == pytest.approx(4.0)
    assert view["spent_credits"] == pytest.approx(4.0)
    assert await _ledger_sum(env, source="action_layer", run_id=f"plan:{plan_id}") == pytest.approx(0.0)
    assert await _ledger_sum(env, project_id=pid) == pytest.approx(4.0)
    hist = (await service.history(plan_id=plan_id))["items"]
    done = [h for h in hist if h["event"] == "step_succeeded"][0]
    assert done["cost_recorded"] == pytest.approx(4.0)


async def test_action_reported_credits_win_and_are_settled(env) -> None:
    pid = await env.add_project()

    async def paid(ctx, params):
        return {"credits_spent": 3.0}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(1, 8),
                                   schema=PROJ_SCHEMA))
    plan_id = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert view["steps"][0]["cost"]["actual"] == pytest.approx(3.0)
    assert await _ledger_sum(env, run_id=f"plan:{plan_id}") == pytest.approx(3.0)


async def test_unknown_cost_row_closes_reserve_but_keeps_actual_unknown(env) -> None:
    pid = await env.add_project()

    async def paid(ctx, params):
        await _native_row(env, params["project_id"], 0.0, note="kie submit cost_unknown=true")
        return {}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(1, 6),
                                   schema=PROJ_SCHEMA))
    plan_id = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert view["steps"][0]["cost"]["actual"] is None
    assert view["spent_credits"] == pytest.approx(0.0)
    assert await _ledger_sum(env, source="action_layer", run_id=f"plan:{plan_id}") == pytest.approx(0.0)


async def test_write_gap_keeps_reserve_conservatively(env) -> None:
    pid = await env.add_project()

    async def paid(ctx, params):
        await _native_row(env, params["project_id"], 0.0, note="outsee submit write_failed cost_unknown=true")
        return {}

    reg = fresh_registry(make_spec("t.paid", paid, risk="paid", spends=True, cost=(1, 6),
                                   schema=PROJ_SCHEMA))
    plan_id = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert view["steps"][0]["cost"]["actual"] is None
    assert view["spent_credits"] == pytest.approx(6.0)


async def test_failed_paid_step_releases_reserve_so_cap_does_not_self_block(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 25.0)
    pid = await env.add_project()

    async def boom(ctx, params):
        raise RuntimeError("provider 500")

    reg = fresh_registry(make_spec("t.paid", boom, risk="paid", spends=True, cost=(5, 20),
                                   schema=PROJ_SCHEMA))
    plan_id = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert view["status"] == "failed"
    assert view["spent_credits"] == pytest.approx(0.0)
    assert view["steps"][0]["cost"]["actual"] == pytest.approx(0.0)
    assert await _ledger_sum(env, project_id=pid) == pytest.approx(0.0)
    plan2 = await _plan(env, reg, {"action_id": "t.paid", "params": {"project_id": pid}})
    await service.approve_plan(env.app, plan2, OP, registry=reg)


async def test_measure_helper_ignores_other_projects_and_old_rows(env) -> None:
    from datetime import datetime, timedelta
    p1, p2 = await env.add_project(), None
    await _native_row(env, p1, 2.5)
    await _native_row(env, 999, 7.0)
    async with env.factory() as s:
        got = await budget.measure_native_credits(s, project_id=p1,
                                                  since=datetime.utcnow() - timedelta(minutes=1))
        assert got == pytest.approx(2.5)
        future = await budget.measure_native_credits(s, project_id=p1,
                                                     since=datetime.utcnow() + timedelta(minutes=1))
        assert future == 0.0
        assert await budget.measure_native_credits(s, project_id=p2, since=datetime.utcnow()) is None



class _Job:
    def __init__(self, *, finish_on_stop: bool) -> None:
        self.stopped = 0
        self.done = False
        self.finish_on_stop = finish_on_stop
        self.poll_fail = False

    async def start(self, ctx, params):
        return {"started": True}

    async def poll(self, ctx, params, started):
        if self.poll_fail and not self.stopped:
            raise RuntimeError("poll 502")
        if self.done or (self.finish_on_stop and self.stopped):
            return JobState(done=True, ok=False, error="остановлено")
        return JobState(done=False, progress=0.1, note="идёт")

    async def stop(self, ctx, params):
        self.stopped += 1


def _job_spec(job: _Job) -> ActionSpec:
    return make_spec("t.job", job.start, risk="paid", spends=True, cost=(1, 5), schema=PROJ_SCHEMA,
                     poll=job.poll, stop=job.stop, poll_interval_s=0.05)


async def test_timeout_stops_paid_job_and_frees_project(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "3")
    pid = await env.add_project()
    job = _Job(finish_on_stop=True)
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert job.stopped == 1
    assert view["status"] == "failed"
    assert view["steps"][0]["status"] == "unknown"
    assert "не подтверждена" not in view["steps"][0]["error"]
    assert view["spent_credits"] == pytest.approx(0.0)
    assert executor.orphan_busy(pid) is None
    events = [h["event"] for h in (await service.history(plan_id=plan_id))["items"]]
    assert "job_stop_after_failure" in events
    plan2 = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    await service.approve_plan(env.app, plan2, OP, registry=reg)
    await service.prepare_run(plan2, OP, registry=reg)


async def test_unconfirmed_stop_keeps_project_busy_until_server_job_ends(env, monkeypatch) -> None:
    from app.settings import settings
    monkeypatch.setattr(settings, "action_layer_step_timeout_s", 0.3)
    monkeypatch.setenv("ACTION_LAYER_STOP_GRACE_S", "0.3")
    pid = await env.add_project()
    job = _Job(finish_on_stop=False)
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert job.stopped == 1
    assert "не подтверждена" in view["steps"][0]["error"]
    assert view["spent_credits"] == pytest.approx(5.0)
    assert executor.orphan_busy(pid) is not None
    plan2 = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    await service.approve_plan(env.app, plan2, OP, registry=reg)
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(plan2, OP, registry=reg)
    assert e.value.code == "project_busy"
    await _native_row(env, pid, 2.0)
    job.done = True
    for _ in range(100):
        if executor.orphan_busy(pid) is None:
            break
        await asyncio.sleep(0.05)
    assert executor.orphan_busy(pid) is None
    async with env.factory() as s:
        assert await budget.spent(s, budget.session_key(plan_id)) == pytest.approx(2.0)
    await service.prepare_run(plan2, OP, registry=reg)


async def test_poll_exception_stops_started_job(env) -> None:
    pid = await env.add_project()
    job = _Job(finish_on_stop=True)
    job.poll_fail = True
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert job.stopped == 1
    assert view["status"] == "failed" and view["steps"][0]["status"] == "failed"
    assert "poll 502" in view["steps"][0]["error"]


async def test_job_error_state_does_not_call_stop(env) -> None:
    pid = await env.add_project()
    job = _Job(finish_on_stop=True)
    job.done = True
    reg = fresh_registry(_job_spec(job))
    plan_id = await _plan(env, reg, {"action_id": "t.job", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_id, approve=True)
    assert job.stopped == 0 and view["steps"][0]["status"] == "failed"



class QueueServer:

    def __init__(self, ops=None, rev=None) -> None:
        self.ops = list(ops or [])
        self.trims: dict = {}
        self.rev = rev
        self.posts: list[dict] = []
        self.on_get = None
        self.gets = 0

    async def call(self, method, path, *, params=None, json=None, timeout=120.0):
        if method == "GET":
            self.gets += 1
            if self.on_get:
                self.on_get(self.gets)
            meta = {"pending_ops": list(self.ops), "video_trims": dict(self.trims)}
            if self.rev is not None:
                meta["queue_rev"] = self.rev
            return {"frames": [], "meta": meta}
        self.posts.append(json)
        if self.rev is not None and json.get("expected_rev") != self.rev:
            return {"ok": False, "rejected": "queue_rev_mismatch", "pending_ops": self.ops}
        self.ops = list(json["pending_ops"])
        if "video_trims" in json:
            self.trims = dict(json["video_trims"])
        if self.rev is not None:
            self.rev += 1
        return {"ok": True, "pending_ops": self.ops}


HUMAN = {"type": "image_regen", "frame_number": 9, "shot": 1}
MINE = {"type": "image_regen", "frame_number": 1, "shot": 1}


async def test_human_edit_between_read_and_write_is_kept() -> None:
    srv = QueueServer()

    def human_adds_once(n):
        if n == 2 and HUMAN not in srv.ops:
            srv.ops.append(HUMAN)

    srv.on_get = human_adds_once
    out = await bm._queue_ops(srv, {"project_id": 1, "ops": [MINE]})
    assert out["queued"] == 1
    assert HUMAN in srv.ops and MINE in srv.ops


async def test_two_layer_edits_in_parallel_keep_both() -> None:
    srv = QueueServer()
    other = {"type": "image_regen", "frame_number": 2, "shot": 1}
    await asyncio.gather(bm._queue_ops(srv, {"project_id": 1, "ops": [MINE]}),
                         bm._queue_ops(srv, {"project_id": 1, "ops": [other]}))
    assert MINE in srv.ops and other in srv.ops


async def test_cancel_and_trim_do_not_drop_human_ops_and_queue_rev_is_sent() -> None:
    srv = QueueServer(ops=[MINE, HUMAN], rev=7)
    out = await bmm._cancel_queue_ops(srv, {"project_id": 1, "ops": [MINE]})
    assert out == {"removed": 1, "left_in_queue": 1}
    assert srv.posts[-1]["expected_rev"] == 7 and srv.ops == [HUMAN]
    out = await bmm._set_video_trim(srv, {"project_id": 1, "frame_number": 3, "shot": 1,
                                          "start": 0.5})
    assert out["key"] == "3:1" and srv.trims["3:1"]["start"] == 0.5 and srv.ops == [HUMAN]


async def test_rev_mismatch_retries_on_fresh_queue() -> None:
    srv = QueueServer(ops=[HUMAN], rev=1)
    bumped = {"done": False}

    def human_saves(n):
        if n == 2 and not bumped["done"]:
            bumped["done"] = True
            srv.rev += 1
            srv.ops.append({"type": "image_regen", "frame_number": 5, "shot": 1})

    srv.on_get = human_saves
    await bm._queue_ops(srv, {"project_id": 1, "ops": [MINE]})
    assert {o["frame_number"] for o in srv.ops} == {1, 5, 9}


async def test_endless_conflict_gives_clear_error_and_never_overwrites() -> None:
    srv = QueueServer(ops=[HUMAN])
    counter = {"n": 0}

    def always_changing(n):
        counter["n"] += 1
        srv.ops = [HUMAN, {"type": "image_regen", "frame_number": 100 + counter["n"], "shot": 1}]

    srv.on_get = always_changing
    with pytest.raises(RuntimeError, match="параллельно"):
        await bm._queue_ops(srv, {"project_id": 1, "ops": [MINE]})
    assert srv.posts == []



async def _attachment(env, content: bytes, pid: int) -> tuple[str, str]:
    rel = f"{attachments.ATTACH_DIR}/{pid}/abc_pic.png"
    path = env.tmp_path / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    async with env.factory() as s:
        s.add(attachments.ActionAttachment(
            id="a" * 32, project_id=pid, operator="o", name="pic.png", kind="image", ext=".png",
            content_type="image/png", size=len(content), sha256=hashlib.sha256(content).hexdigest(),
            rel_path=rel))
        await s.commit()
    return rel, str(path)


async def test_attachment_swap_after_upload_is_refused_at_run(env) -> None:
    pid = await env.add_project()
    rel, abs_path = await _attachment(env, b"\x89PNG\r\n\x1a\n" + b"x" * 64, pid)
    schema = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1},
                                               "source_path": {"type": "string"}},
              "additionalProperties": False}
    ran = []

    async def use(ctx, params):
        ran.append(1)
        return {}

    reg = fresh_registry(make_spec("t.upload", use, risk="local_safe", schema=schema))
    step = {"action_id": "t.upload", "params": {"project_id": pid, "source_path": rel}}
    assert await executor.verify_attachments({"source_path": rel}) is None
    plan_ok = await _plan(env, reg, step)
    assert (await _go(env, reg, plan_ok))["status"] == "succeeded" and ran == [1]

    with open(abs_path, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n" + b"y" * 64)
    assert "sha256" in (await executor.verify_attachments({"source_path": rel}))
    plan_bad = await _plan(env, reg, step)
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(plan_bad, OP, registry=reg)
    assert e.value.code == "attachment_changed"
    assert ran == [1]


async def test_swap_between_prepare_and_step_is_refused_by_executor(env) -> None:
    pid = await env.add_project()
    rel, abs_path = await _attachment(env, b"\x89PNG\r\n\x1a\n" + b"x" * 64, pid)
    schema = {"type": "object", "properties": {"project_id": {"type": "integer", "minimum": 1},
                                               "source_path": {"type": "string"}},
              "additionalProperties": False}
    ran = []

    async def use(ctx, params):
        ran.append(1)
        return {}

    reg = fresh_registry(make_spec("t.upload", use, risk="local_safe", schema=schema))
    plan_id = await _plan(env, reg, {"action_id": "t.upload",
                                     "params": {"project_id": pid, "source_path": rel}})
    await service.prepare_run(plan_id, OP, registry=reg)
    with open(abs_path, "ab") as fh:
        fh.write(b"tail")
    executor.launch(env.app, plan_id, OP.id, registry=reg)
    await executor.wait(plan_id)
    view = await service.plan_view(plan_id)
    assert view["steps"][0]["status"] == "failed" and ran == []
    assert "sha256" in view["steps"][0]["error"]


async def test_unregistered_attachment_path_and_non_attachment_path(env) -> None:
    assert "реестре" in (await executor.verify_attachments(
        {"source_path": f"{attachments.ATTACH_DIR}/1/ghost.png"}))
    assert await executor.verify_attachments({"source_path": "uploads/x.png"}) is None
    assert await executor.verify_attachments({"data_base64": "AAAA"}) is None



async def test_parallel_prepare_run_on_one_project_lets_only_one_through(env) -> None:
    pid = await env.add_project()

    async def quick(ctx, params):
        return {}

    reg = fresh_registry(make_spec("t.q", quick, risk="local_safe", schema=PROJ_SCHEMA))
    a = await _plan(env, reg, {"action_id": "t.q", "params": {"project_id": pid}})
    b = await _plan(env, reg, {"action_id": "t.q", "params": {"project_id": pid}})
    res = await asyncio.gather(service.prepare_run(a, OP, registry=reg),
                               service.prepare_run(b, OP, registry=reg), return_exceptions=True)
    refused = [r for r in res if isinstance(r, service.RunRefused)]
    assert len(refused) == 1 and refused[0].code == "project_busy"
    executor.release_claim(a)
    executor.release_claim(b)


async def test_project_stop_is_not_blocked_by_kill_switch(env) -> None:
    pid = await env.add_project()
    ran = []

    async def stop(ctx, params):
        ran.append("stop")
        return {"stopped": True}

    async def other(ctx, params):
        ran.append("other")
        return {}

    reg = fresh_registry(make_spec("project.stop", stop, risk="local_safe", schema=PROJ_SCHEMA),
                         make_spec("t.other", other, risk="local_safe", schema=PROJ_SCHEMA))
    await kill_switch.set_state(env.factory, active=True, actor="op", rationale="stop all now")
    plan_stop = await _plan(env, reg, {"action_id": "project.stop", "params": {"project_id": pid}})
    view = await _go(env, reg, plan_stop)
    assert view["status"] == "succeeded" and ran == ["stop"]
    plan_other = await _plan(env, reg, {"action_id": "t.other", "params": {"project_id": pid}})
    with pytest.raises(service.RunRefused) as e:
        await service.prepare_run(plan_other, OP, registry=reg)
    assert e.value.code == "kill_switch_active"
    mixed = await _plan(env, reg, {"action_id": "project.stop", "params": {"project_id": pid}},
                        {"action_id": "t.other", "params": {"project_id": pid}})
    with pytest.raises(service.RunRefused):
        await service.prepare_run(mixed, OP, registry=reg)
    await kill_switch.set_state(env.factory, active=False, actor="op", rationale="resume ok")


async def test_unlink_ref_cannot_delete_files_and_delete_manual_ref_cannot_unlink() -> None:
    ensure_builtin_actions()
    base = {"project_id": 1, "frame_number": 1, "ref_id": "r1"}
    assert registry.validate_params("montage.unlink_ref", {**base, "kind": "manual"})
    assert registry.validate_params("montage.delete_manual_ref", {**base, "kind": "character"})
    assert not registry.validate_params("montage.unlink_ref", {**base, "kind": "item"})

    class Ctx:
        calls: list = []

        async def call(self, method, path, **kw):
            self.calls.append((method, path, kw))
            return {"ok": True, "refs": []}

    ctx = Ctx()
    with pytest.raises(ValueError):
        await bmm._unlink_ref(ctx, {**base, "kind": "manual"})
    with pytest.raises(ValueError):
        await bmm._delete_manual_ref(ctx, {**base, "kind": "character"})
    assert ctx.calls == []
    await bmm._unlink_ref(ctx, {**base, "kind": "character"})
    await bmm._delete_manual_ref(ctx, {**base, "kind": "manual"})
    assert [c[2]["params"]["kind"] for c in ctx.calls] == ["character", "manual"]
    assert registry.get("montage.unlink_ref").destructive is False
    assert registry.get("montage.delete_manual_ref").destructive is True


def test_change_propose_has_no_hidden_llm_call() -> None:
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parents[1] / "app" / "services"
    files = list((root / "change_control").glob("*.py")) + [root / "action_layer" / "builtin_change.py"]
    banned = re.compile(r"^\s*(?:from|import)\s+[\w.]*(?:gpt_api|kie_http|vibecode|openai|anthropic"
                        r"|httpx|requests|aiohttp|llm)", re.IGNORECASE | re.MULTILINE)
    offenders = [f.name for f in files if banned.search(f.read_text(encoding="utf-8"))]
    assert offenders == []
    ensure_builtin_actions()
    spec = registry.get("change.propose")
    assert spec.spends_money is False and spec.poll is None
