from __future__ import annotations

import math
import uuid as uuidlib

import pytest

from app.models import Frame, Project
from app.services.action_layer import builtin_wave3 as w3
from app.services.action_layer import node_run_cost as nrc
from app.services.action_layer.context import ActionCallError
from app.services.action_layer.spec import ActionRegistry, CostEstimate
from tests.action_layer_support import env

NODE = "n_excel_gpt_fw_shots"


def _reg() -> ActionRegistry:
    from app.services.action_layer import builtin_group as bg
    reg = ActionRegistry()
    bg.register_group_actions(reg)
    w3.register_wave3(reg)
    return reg


def _frames(n_parents: int, *, vo_chars: int = 120, children_per_parent: int = 0) -> list[dict]:
    out, number = [], 0
    for i in range(n_parents):
        number += 1
        pu = f"{i:032x}"
        out.append({"uuid": pu, "number": number, "voiceover_text": "слово " * (vo_chars // 6),
                    "meaning": "", "image_prompt": "", "animation_prompt": "", "attrs": {}})
        for k in range(children_per_parent):
            number += 1
            out.append({"uuid": f"{i:016x}{k:016x}", "number": number, "voiceover_text": "",
                        "attrs": {"camera_subdivide": {"parent_uuid": pu, "role": "shot",
                                                       "shot_index": k + 2}}})
    return out


def _basis(node_key=NODE, frames=None, **kw) -> nrc.RunBasis:
    base = dict(node_key=node_key, frames=_frames(20) if frames is None else frames, entities=[],
                prompt_chars=20000, user_text_chars=0, project_text_chars=0)
    base.update(kw)
    return nrc.compute_run_basis(**base)



def test_worst_requests_per_pack_matches_code_paths() -> None:
    assert nrc.worst_requests_for_pack(1) == 2 * 3
    assert nrc.worst_requests_for_pack(2) == 2 * 8
    assert nrc.worst_requests_for_pack(4) == 2 * 18
    assert nrc.worst_requests_for_pack(8) == nrc.worst_requests_for_pack(40) == 2 * 22
    sizes = [nrc.worst_requests_for_pack(m) for m in range(1, 30)]
    assert sizes == sorted(sizes) and min(sizes) >= 2


def test_worst_case_follows_split_levels_of_the_runner(monkeypatch) -> None:
    from app.services import adaptive_llm_batches as alb
    nrc._chunk_calls.cache_clear()
    monkeypatch.setattr(alb, "NEXT_SPLIT", {})
    try:
        assert nrc._chunk_calls(8, 1, 2) < 22
    finally:
        monkeypatch.undo()
        nrc._chunk_calls.cache_clear()
    assert nrc._chunk_calls(8, 1, 2) == 22



def test_basis_packs_units_and_requests() -> None:
    b = _basis(frames=_frames(20))
    assert (b.packs, b.units, b.rows) == (3, 20, 20)
    assert b.min_requests == 3
    assert b.max_requests == sum(nrc.worst_requests_for_pack(m) for m in (8, 8, 4))
    assert b.node_kind == "_fw_shots"
    assert set(b.declare()) == {"min_llm_requests", "max_llm_requests", "max_input_tokens",
                                "max_output_tokens"}


def test_heavy_cell_goes_solo_and_children_do_not_add_units() -> None:
    frames = _frames(9, children_per_parent=2)
    frames[0]["voiceover_text"] = "слово " * 1000
    b = _basis(frames=frames)
    assert b.units == 9 and b.packs == 2


def test_tokens_grow_with_prompt_and_project_text_and_data() -> None:
    small = _basis(prompt_chars=1000)
    big = _basis(prompt_chars=40000)
    assert big.max_input_tokens - small.max_input_tokens >= math.floor(
        39000 * nrc.PROMPT_MARGIN / nrc.CHARS_PER_TOKEN) - 2
    more_data = _basis(frames=_frames(20, vo_chars=3000))
    assert more_data.max_input_tokens > small.max_input_tokens
    plan_small = _basis(node_key="n_excel_gpt_fw_script", project_text_chars=0)
    plan_big = _basis(node_key="n_excel_gpt_fw_script", project_text_chars=50000)
    assert plan_big.max_input_tokens > plan_small.max_input_tokens
    other = _basis(project_text_chars=50000)
    assert other.max_input_tokens == _basis().max_input_tokens
    assert _basis().max_output_tokens > nrc.REASONING_ALLOWANCE_TOKENS


def test_qc_excludes_shot_children_and_empty_or_unknown_node() -> None:
    frames = _frames(3, children_per_parent=3)
    assert _basis(node_key="n_excel_gpt_fw_qc", frames=frames).rows == 3
    assert _basis(frames=[]) is None
    assert _basis(frames=[{"uuid": "a" * 32, "number": 1, "voiceover_text": "", "attrs": {}}]
                  ) is None
    with pytest.raises(ValueError):
        _basis(node_key="n_excel_gpt_fw_frames")



def test_price_uses_vibecode_cost_times_catalog_markup() -> None:
    from app.services.api_tracker_hook import vibecode_cost
    from app.services.kie_catalog import CREDIT_USD
    from app.services.vibecode_catalog import PRICE_MARKUP
    credits, why = nrc.price_request_credits(20000, 10000)
    usd = vibecode_cost(nrc.text_model_id(), 20000, 10000)
    assert credits == pytest.approx(usd * PRICE_MARKUP / CREDIT_USD)
    assert "не откалибрована" in why and PRICE_MARKUP == 3.0


def test_price_unknown_when_model_has_no_snapshot_price(monkeypatch) -> None:
    monkeypatch.setattr("app.services.api_tracker_hook.vibecode_cost", lambda *a, **k: None)
    assert nrc.price_request_credits(1000, 1000) is None
    est = w3._estimate_node_run({"project_id": 1, "node_key": NODE, "min_llm_requests": 1,
                                 "max_llm_requests": 2, "max_input_tokens": 10,
                                 "max_output_tokens": 10})
    assert est.known is False



DECL = {"min_llm_requests": 3, "max_llm_requests": 120, "max_input_tokens": 30000,
        "max_output_tokens": 20000}


def test_estimator_known_only_with_declared_basis_and_says_uncalibrated() -> None:
    base = {"project_id": 1, "node_key": NODE}
    assert w3._estimate_node_run(base).known is False
    for missing in DECL:
        params = {**base, **{k: v for k, v in DECL.items() if k != missing}}
        assert w3._estimate_node_run(params).known is False, missing
    est = w3._estimate_node_run({**base, **DECL})
    assert est.known and 0 < est.low < est.high
    unit, _ = nrc.price_request_credits(DECL["max_input_tokens"], DECL["max_output_tokens"])
    assert est.high == round(unit * 120, 2) and est.low == round(unit * 3, 2)
    assert "верхняя оценка, не откалибровано" in est.basis
    assert "max_tokens не задаётся" in est.basis and "не возвращает" in est.basis
    bad = w3._estimate_node_run({**base, **DECL, "min_llm_requests": 500})
    assert bad.known is False


def test_estimator_report_node_free_other_nodes_unknown() -> None:
    free = w3._estimate_node_run({"project_id": 1, "node_key": "n_excel_gpt_fw_report"})
    assert (free.low, free.high) == (0.0, 0.0) and "LLM" in free.basis
    for key in ("n_excel_gpt_fw_frames", "n_excel_gpt_fw_check_script", "n_excel_gpt_7",
                "custom_node"):
        est = w3._estimate_node_run({"project_id": 1, "node_key": key, **DECL})
        assert est.known is False, key


def test_run_step_flags_unchanged_and_basis_params_optional() -> None:
    spec = _reg().get("node.run_step")
    assert (spec.risk, spec.spends_money, spec.destructive, spec.reversible,
            spec.idempotent) == ("destructive", True, True, False, False)
    assert spec.is_job and spec.stop is not None and spec.poll_interval_s == 3.0
    reg = _reg()
    assert reg.validate_params("node.run_step", {"project_id": 1, "node_key": NODE}) == []
    assert reg.validate_params("node.run_step", {"project_id": 1, "node_key": NODE, **DECL}) == []
    assert reg.validate_params("node.run_step", {"project_id": 1, "node_key": NODE,
                                                  "max_llm_requests": 0})
    assert reg.validate_params("node.run_step", {"project_id": 1, "node_key": NODE,
                                                  "extra": 1})
    assert [s.id for s in reg.list()].count("node.run_step") == 1



class FakeCtx:
    def __init__(self, *, frames=None, variant="scenes_to_frames_ru", user_text="",
                 gpt_text_error=None, status="plan_ready"):
        self.calls = []
        self.proj = {"id": 1, "status": status, "topic": "тема", "general_plan": "",
                     "script_text": "", "prompt_overrides": {}, "meta": {
                         "canvas_graph": {"nodes": [{"id": NODE, "type": "excel_gpt",
                                                     "data": {}}], "edges": []},
                         "prompt_slot_variants": {NODE: {"main": variant}}}}
        self.frames = _frames(20) if frames is None else frames
        self.user_text, self.gpt_text_error = user_text, gpt_text_error

    async def call(self, method, path, *, params=None, json=None, files=None, timeout=0):
        self.calls.append((method, path, params))
        if path == "/projects/1":
            return self.proj
        if path == "/prompt-studio/projects/1/gpt-text/excel_gpt":
            if self.gpt_text_error:
                raise self.gpt_text_error
            return {"text": self.user_text}
        if path == "/db/projects/1/graph":
            return {"frames": self.frames, "entities": []}
        return {}


async def test_load_basis_reads_real_prompt_file_and_project_data() -> None:
    from app.services.prompt_library import resolve_script_frames_qc_prompt_path
    basis = await w3._load_basis(FakeCtx(user_text="u" * 400),
                                 {"project_id": 1, "node_key": NODE})
    assert isinstance(basis, nrc.RunBasis) and basis.packs == 3
    real = len(resolve_script_frames_qc_prompt_path("scenes_to_frames_ru").read_text(
        encoding="utf-8"))
    assert basis.parts["prompt_chars"] == real > 10000
    assert basis.parts["user_text_chars"] == 400


async def test_load_basis_refuses_other_prompt_variant_missing_text_and_no_frames() -> None:
    p = {"project_id": 1, "node_key": NODE}
    other = await w3._load_basis(FakeCtx(variant="scene_grammar_unified_agent"), p)
    assert isinstance(other, str) and "scenes_to_frames_ru" in other
    err = await w3._load_basis(FakeCtx(gpt_text_error=ActionCallError(500, "x")), p)
    assert isinstance(err, str) and "сопроводительный" in err
    none = await w3._load_basis(FakeCtx(frames=[]), p)
    assert isinstance(none, str) and "запускать нечего" in none
    unk = await w3._load_basis(FakeCtx(), {"project_id": 1, "node_key": "n_excel_gpt_fw_frames"})
    assert isinstance(unk, str) and "script_frames_qc" in unk


async def test_precondition_demands_declared_cost_not_below_computed() -> None:
    pre = next(p for p in _reg().get("node.run_step").preconditions
               if p.id == "run_cost_declared")
    ctx = FakeCtx()
    base = {"project_id": 1, "node_key": NODE}
    assert "заявите" in await pre.check(ctx, base)
    basis = await w3._load_basis(ctx, base)
    ok = {**base, **basis.declare()}
    assert await pre.check(ctx, ok) is None
    for key in ("max_llm_requests", "max_input_tokens", "max_output_tokens"):
        low = {**ok, key: ok[key] - 1}
        msg = await pre.check(ctx, low)
        assert msg and key in msg and str(ok[key]) in msg, key
    assert "min_llm_requests больше" in await pre.check(
        ctx, {**ok, "min_llm_requests": ok["max_llm_requests"] + 1})
    grown = FakeCtx(frames=_frames(40))
    assert "по данным проекта" in await pre.check(grown, ok)
    assert await pre.check(ctx, {"project_id": 1, "node_key": "n_excel_gpt_fw_report"}) is None


async def test_cost_basis_action_matches_estimator_and_executor_reverifies() -> None:
    reg = _reg()
    ctx = FakeCtx()
    base = {"project_id": 1, "node_key": NODE}
    info = await reg.get("node.get_run_cost_basis").executor(ctx, base)
    assert info["estimable"] and info["uncalibrated"] and info["packs"] == 3
    est = reg.get("node.run_step").estimate({**base, **info["declare"]})
    assert est.known and est.high == info["upper_bound_credits"]
    assert est.low == info["lower_bound_credits"] and est.low < est.high
    run = reg.get("node.run_step").executor
    started = await run(ctx, {**base, **info["declare"]})
    assert started == {"started": True, "node_key": NODE}
    assert ctx.calls[-1] == ("POST", "/projects/1/steps/excel_gpt/run",
                             {"node_key": NODE, "mode": "resume"})
    n = len(ctx.calls)
    lied = {**info["declare"], "max_llm_requests": 1}
    with pytest.raises(RuntimeError, match="не совпадает"):
        await run(ctx, {**base, **lied})
    with pytest.raises(RuntimeError, match="не совпадает"):
        await run(ctx, base)
    assert not any(c[0] == "POST" for c in ctx.calls[n:])
    report = await run(ctx, {"project_id": 1, "node_key": "n_excel_gpt_fw_report"})
    assert report["started"] is True


async def test_cost_basis_action_reports_not_estimable_instead_of_guessing() -> None:
    reg = _reg()
    out = await reg.get("node.get_run_cost_basis").executor(
        FakeCtx(variant="shots_qc_ru"), {"project_id": 1, "node_key": NODE})
    assert out["estimable"] is False and "shots_qc_ru" in out["reason"]
    free = await reg.get("node.get_run_cost_basis").executor(
        FakeCtx(), {"project_id": 1, "node_key": "n_excel_gpt_fw_report"})
    assert free["free"] is True



async def _project_with_cells(env, cells=20) -> int:
    pid = await env.add_project(frames=0)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        p.meta = {"canvas_graph": {"nodes": [{"id": NODE, "type": "excel_gpt", "data": {}}],
                                   "edges": []},
                  "prompt_slot_variants": {NODE: {"main": "scenes_to_frames_ru"}}}
        for i in range(1, cells + 1):
            s.add(Frame(project_id=pid, number=i, voiceover_text="реплика про ночь " * 8,
                        uuid=uuidlib.uuid4().hex, attrs={}))
        await s.commit()
    return pid


@pytest.fixture
def not_managed(monkeypatch):
    async def _no(pid):
        return False
    monkeypatch.setattr("app.services.actions_v1.managed_project_enabled", _no)


async def test_real_flow_basis_then_run_step_preview_is_known_and_approvable(
        env, not_managed, monkeypatch) -> None:
    from app.settings import settings
    pid = await _project_with_cells(env)
    base = "/api/action-layer"
    r = await env.client.post(f"{base}/plans/preview", json={"steps": [{
        "action_id": "node.get_run_cost_basis", "params": {"project_id": pid, "node_key": NODE}}]})
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    assert (await env.client.post(f"{base}/plans/{plan}/run")).status_code == 200
    from app.services.action_layer import executor
    await executor.wait(plan)
    view = (await env.client.get(f"{base}/plans/{plan}")).json()
    assert view["status"] == "succeeded", view
    info = view["steps"][0]["result"]
    assert info["estimable"] and info["packs"] == 3 and info["units"] == 20
    params = {"project_id": pid, "node_key": NODE, **info["declare"]}
    r0 = await env.client.post(f"{base}/plans/preview", json={"steps": [{
        "action_id": "node.run_step", "params": {"project_id": pid, "node_key": NODE}}]})
    assert r0.status_code == 422 and "run_cost_declared" in str(r0.json())
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 1e9)
    r1 = await env.client.post(f"{base}/plans/preview", json={"steps": [{
        "action_id": "node.run_step", "params": params}]})
    assert r1.status_code == 200, r1.text
    pv = r1.json()["preview"]
    step = pv["steps"][0]
    assert step["cost"]["known"] and step["cost"]["high"] == info["upper_bound_credits"]
    assert "не откалибровано" in step["cost"]["basis"]
    assert pv["needs_approval"] and pv["budget"]["fits"] is True and pv["ok"] is True
    a = await env.client.post(f"{base}/plans/{r1.json()['plan_id']}/approve")
    assert a.status_code == 200, a.text
    monkeypatch.setattr(settings, "action_layer_session_cap_credits", 1.0)
    r2 = await env.client.post(f"{base}/plans/preview", json={"steps": [{
        "action_id": "node.run_step", "params": params}]})
    assert r2.json()["preview"]["budget"]["fits"] is False
    a2 = await env.client.post(f"{base}/plans/{r2.json()['plan_id']}/approve")
    assert a2.status_code == 409 and a2.json()["detail"]["code"] == "over_budget"
    low = {**params, "max_llm_requests": 1}
    r3 = await env.client.post(f"{base}/plans/preview", json={"steps": [{
        "action_id": "node.run_step", "params": low}]})
    assert r3.status_code == 422 and "run_cost_declared" in str(r3.json())
