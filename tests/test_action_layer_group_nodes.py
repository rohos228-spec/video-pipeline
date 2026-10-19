from __future__ import annotations

import pytest

from app.services.action_layer import builtin_group as bg
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ActionRegistry, registry
from tests.action_layer_support import env

NODE = "n_excel_gpt_fw_shots"


class FakeCtx:

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    async def call(self, method, path, *, params=None, json=None, timeout=0):
        self.calls.append((method, path, params, json))
        val = self.routes.get((method, path))
        if isinstance(val, Exception):
            raise val
        if callable(val):
            return val(params, json)
        return val if val is not None else {}


def _project(status="plan_ready", **meta):
    base = {"canvas_graph": {"nodes": [
        {"id": NODE, "type": "excel_gpt", "data": {}},
        {"id": "n_topic", "type": "topic", "data": {}}], "edges": []}}
    base.update(meta)
    return {"id": 1, "status": status, "meta": base}


def _routes(project=None, **extra):
    r = {("GET", "/projects/1"): project or _project()}
    for suffix in ("apply-status", "montage-status", "recover-outsee-status"):
        r[("GET", f"/projects/1/montage-board/{suffix}")] = {"job": {"status": "idle"}}
    r.update(extra)
    return r


@pytest.fixture
def specs():
    reg = ActionRegistry()
    bg.register_group_actions(reg)
    return reg


async def _failures(spec, ctx, params):
    out = {}
    for pre in spec.preconditions:
        try:
            msg = await pre.check(ctx, params)
        except ActionCallError as exc:
            msg = f"HTTP {exc.status}"
        if msg:
            out[pre.id] = msg
    return out


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


async def test_run_step_preconditions_pass_on_idle_project(specs, not_managed) -> None:
    spec = specs.get("node.run_step")
    assert await _failures(spec, FakeCtx(_routes()), {"project_id": 1, "node_key": NODE}) == {}


async def test_run_step_refused_when_managed(specs, monkeypatch) -> None:
    async def _yes(pid):
        return True
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _yes)
    fails = await _failures(specs.get("node.run_step"), FakeCtx(_routes()),
                            {"project_id": 1, "node_key": NODE})
    assert list(fails) == ["not_managed"] and "Actions-v1" in fails["not_managed"]


async def test_run_step_refused_when_managed_check_unavailable(specs, monkeypatch) -> None:
    async def _boom(pid):
        raise RuntimeError("no storage")
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _boom)
    fails = await _failures(specs.get("node.run_step"), FakeCtx(_routes()),
                            {"project_id": 1, "node_key": NODE})
    assert "not_managed" in fails


async def test_run_step_refused_while_anything_runs(specs, not_managed) -> None:
    spec = specs.get("node.run_step")
    busy = FakeCtx(_routes(project=_project(status="enriching_1")))
    assert "project_idle" in await _failures(spec, busy, {"project_id": 1, "node_key": NODE})
    routes = _routes()
    routes[("GET", "/projects/1/montage-board/apply-status")] = {"job": {"status": "running"}}
    fails = await _failures(spec, FakeCtx(routes), {"project_id": 1, "node_key": NODE})
    assert list(fails) == ["no_montage_jobs"]


async def test_run_step_refused_for_missing_disabled_or_non_gpt_node(specs, not_managed) -> None:
    spec = specs.get("node.run_step")
    ctx = FakeCtx(_routes())
    assert "node_runnable" in await _failures(spec, ctx, {"project_id": 1, "node_key": "n_none"})
    assert "node_runnable" in await _failures(spec, ctx, {"project_id": 1, "node_key": "n_topic"})
    off = FakeCtx(_routes(project=_project(disabled_nodes=[NODE])))
    fails = await _failures(spec, off, {"project_id": 1, "node_key": NODE})
    assert "отключена" in fails["node_runnable"]


async def test_run_step_executor_poll_and_stop(specs) -> None:
    spec = specs.get("node.run_step")
    params = {"project_id": 1, "node_key": NODE}
    state = {"status": "enriching_1"}
    ctx = FakeCtx({
        ("POST", "/projects/1/steps/excel_gpt/run"): {"status": "enriching_1"},
        ("GET", "/projects/1"): lambda p, j: {"status": state["status"]},
        ("GET", "/projects/1/run"): {"node_runs": [{"node_key": NODE, "status": "failed",
                                                    "error": "GPT упал"}]},
        ("POST", "/projects/1/stop"): {"message": "stopped"},
    })
    started = await spec.executor(ctx, params)
    assert ctx.calls[0] == ("POST", "/projects/1/steps/excel_gpt/run",
                            {"node_key": NODE, "mode": "resume"}, None)
    assert (await spec.poll(ctx, params, started)).done is False
    state["status"] = "enrich_1_ready"
    done = await spec.poll(ctx, params, started)
    assert done.done and not done.ok and "GPT упал" in done.error
    await spec.stop(ctx, params)
    assert ctx.calls[-1][:2] == ("POST", "/projects/1/stop")


async def test_poll_success_when_node_not_failed(specs) -> None:
    spec = specs.get("node.run_step")
    ctx = FakeCtx({("GET", "/projects/1"): {"status": "enrich_1_ready"},
                   ("GET", "/projects/1/run"): {"node_runs": [{"node_key": NODE, "status": "done"}]}})
    st = await spec.poll(ctx, {"project_id": 1, "node_key": NODE}, {})
    assert st.done and st.ok and st.result["final_status"] == "enrich_1_ready"


async def test_project_stop_swallows_nothing_to_stop(specs) -> None:
    spec = specs.get("project.stop")
    ctx = FakeCtx({("POST", "/projects/1/stop"): ActionCallError(400, "нечего останавливать")})
    assert (await spec.executor(ctx, {"project_id": 1}))["stopped"] is False
    boom = FakeCtx({("POST", "/projects/1/stop"): ActionCallError(500, "x")})
    with pytest.raises(ActionCallError):
        await spec.executor(boom, {"project_id": 1})


async def test_set_config_blocks_output_mode_on_group_nodes(specs) -> None:
    spec = specs.get("node.set_config")
    ctx = FakeCtx(_routes())
    fails = await _failures(spec, ctx, {"project_id": 1, "node_key": NODE, "outputMode": "text"})
    assert list(fails) == ["output_mode_allowed"]
    assert await _failures(spec, ctx, {"project_id": 1, "node_key": NODE, "role": "review"}) == {}
    none = await _failures(spec, ctx, {"project_id": 1, "node_key": NODE})
    assert list(none) == ["has_changes"]


async def test_llm_route_never_offers_model_switch_for_group_nodes(specs) -> None:
    spec = specs.get("node.get_llm_route")
    res = await spec.executor(FakeCtx({}), {"project_id": 1, "node_key": NODE})
    assert res["forced_vibecode"] is True and "игнорируется" in res["note"]
    other = await spec.executor(FakeCtx({}), {"project_id": 1, "node_key": "n_custom"})
    assert other["forced_vibecode"] is False
    assert not any("model" in s.id and s.id != "node.get_llm_route" for s in specs.list())


async def test_prompt_variant_and_disabled_write_full_meta_buckets(specs) -> None:
    ctx = FakeCtx(_routes(project=_project(
        prompt_slot_variants={"other": {"main": "a"}}, disabled_nodes=["n_x"])))
    await specs.get("node.set_prompt_variant").executor(
        ctx, {"project_id": 1, "node_key": NODE, "variant": "shots_qc_ru"})
    sent = ctx.calls[-1][3]["meta"]["prompt_slot_variants"]
    assert sent == {NODE: {"main": "shots_qc_ru"}}
    assert "other" not in sent
    res = await specs.get("node.set_disabled").executor(
        ctx, {"project_id": 1, "node_key": NODE, "disabled": True})
    assert res["disabled_nodes"] == ["n_x", NODE]
    res = await specs.get("node.set_disabled").executor(
        ctx, {"project_id": 1, "node_key": "n_x", "disabled": False})
    assert res["disabled_nodes"] == []


async def test_reset_from_step_validates_step(specs, not_managed) -> None:
    spec = specs.get("node.reset_from_step")
    routes = _routes()
    routes[("GET", "/projects/steps/catalog")] = [{"code": "plan"}, {"code": "split"}]
    assert await _failures(spec, FakeCtx(routes), {"project_id": 1, "step_code": "plan"}) == {}
    bad = await _failures(spec, FakeCtx(routes), {"project_id": 1, "step_code": "nope"})
    assert list(bad) == ["step_known"]


async def test_run_step_preview_refused_without_canvas_node(env) -> None:
    project = await env.add_project(frames=1)
    specs_ = bg._specs()
    for s in specs_:
        registry.unregister(s.id)
        registry.register(s)
    try:
        r = await env.client.post("/api/action-layer/plans/preview", json={"steps": [{
            "action_id": "node.run_step", "params": {"project_id": project, "node_key": NODE}}]})
        assert r.status_code == 422
        assert "node_runnable" in str(r.json()["detail"]["preview"])
    finally:
        for s in specs_:
            registry.unregister(s.id)
