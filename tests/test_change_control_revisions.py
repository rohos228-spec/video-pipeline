from __future__ import annotations

from app.models import Project
from app.services import revisions
from app.services.action_layer.context import ActionContext
from app.services.change_control import service
from app.services.change_control.gateway import CtxGateway
from tests.action_layer_support import env
from tests.change_control_support import (
    GPT_PROMPT, HUMAN, PLAN_PROMPT, ccenv, prompts_root)

NODES = ("n_plan", "n_script", "n_split", "n_gpt")


async def _revs(env) -> dict:
    out = {}
    async with env.factory() as s:
        project = await s.get(Project, env.pid)
        for key in NODES:
            out[key] = await revisions.compute_input_revision(s, project, key)
    return out


def _gateway(env) -> CtxGateway:
    return CtxGateway(ActionContext(app=env.app, operator=HUMAN))


async def _apply(env, **kw):
    gw = _gateway(env)
    view = await service.propose(gw, proposer=HUMAN, rationale="тест", project_id=env.pid, **kw)
    return view, await service.apply(gw, view["id"], view["digest"], approved_by=HUMAN)


async def _rollback(env, target, version):
    return await service.rollback(_gateway(env), target, version, approved_by=HUMAN)


def _changed(before: dict, after: dict) -> set:
    return {k for k in NODES if before[k] != after[k]}


async def test_baseline_revisions_are_stable(ccenv) -> None:
    first = await _revs(ccenv)
    assert first == await _revs(ccenv)


async def test_step_param_change_invalidates_only_that_node_and_rollback_restores_it(ccenv) -> None:
    before = await _revs(ccenv)
    _view, res = await _apply(ccenv, tier="T1", kind="step_params", node_key="n_split",
                              candidate={"step": "split", "values": {"cell_max_chars": 600}})
    after = await _revs(ccenv)
    assert _changed(before, after) == {"n_split"}
    await _rollback(ccenv, f"project:{ccenv.pid}:n_split:step_params", res["version"])
    assert await _revs(ccenv) == before


async def test_prompt_variant_change_invalidates_only_that_node(ccenv) -> None:
    before = await _revs(ccenv)
    _view, res = await _apply(ccenv, tier="T1", kind="prompt_variant", node_key="n_plan",
                              candidate={"variant": "plan_alt"})
    applied = await _revs(ccenv)
    assert _changed(before, applied) == {"n_plan"}
    await _rollback(ccenv, f"project:{ccenv.pid}:n_plan:prompt", res["version"])
    after_rb = await _revs(ccenv)
    assert after_rb["n_plan"] != applied["n_plan"]
    assert _changed(before, after_rb) <= {"n_plan"}


async def test_prompt_text_change_invalidates_only_that_node(ccenv) -> None:
    before = await _revs(ccenv)
    text = GPT_PROMPT.replace("for the style line", "for the style line, briefly")
    _view, res = await _apply(ccenv, tier="T2", kind="prompt_text", node_key="n_gpt",
                              candidate={"text": text})
    assert _changed(before, await _revs(ccenv)) == {"n_gpt"}
    assert (ccenv.prompts / "05_excel_gpt" / "default.md").read_text(encoding="utf-8") == GPT_PROMPT
    await _rollback(ccenv, f"project:{ccenv.pid}:n_gpt:prompt", res["version"])
    after_rb = await _revs(ccenv)
    assert _changed(before, after_rb) <= {"n_gpt"}


async def test_node_options_change_moves_node_config_revision_of_that_node(ccenv) -> None:
    before = await _revs(ccenv)
    async with ccenv.factory() as s:
        meta_before = dict((await s.get(Project, ccenv.pid)).meta)
    node = next(n for n in meta_before["canvas_graph"]["nodes"] if n["id"] == "n_gpt")
    cfg_before = revisions.node_config_revision(node, meta_before)
    await _apply(ccenv, tier="T1", kind="node_options", node_key="n_gpt",
                 candidate={"values": {"useSnapshot": True}})
    async with ccenv.factory() as s:
        meta_after = dict((await s.get(Project, ccenv.pid)).meta)
    assert revisions.node_config_revision(node, meta_after) != cfg_before
    plan_node = next(n for n in meta_before["canvas_graph"]["nodes"] if n["id"] == "n_plan")
    assert revisions.node_config_revision(plan_node, meta_after) == \
        revisions.node_config_revision(plan_node, meta_before)
    assert _changed(before, await _revs(ccenv)) == {"n_gpt"}


async def test_structure_hash_is_untouched_so_execution_permissions_are_not_invalidated(ccenv) -> None:
    async with ccenv.factory() as s:
        meta = (await s.get(Project, ccenv.pid)).meta
        graph = meta["canvas_graph"]
        before = revisions.structure_hash(graph["nodes"], graph["edges"])
    await _apply(ccenv, tier="T1", kind="step_params", node_key="n_split",
                 candidate={"step": "split", "values": {"cell_max_chars": 600}})
    async with ccenv.factory() as s:
        graph = (await s.get(Project, ccenv.pid)).meta["canvas_graph"]
        assert revisions.structure_hash(graph["nodes"], graph["edges"]) == before
