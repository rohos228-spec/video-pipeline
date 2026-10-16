from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Project
from app.services import prompt_active_global
from app.services.action_layer import store as al_store
from app.services.action_layer.context import ActionContext
from app.services.action_layer.spec import ensure_builtin_actions, registry
from app.services.change_control import service, tiers
from app.services.change_control import store as cs
from app.services.change_control.errors import ChangeRefused
from app.services.change_control.gateway import CtxGateway
from app.services.change_control.targets import target_title
from tests.action_layer_support import env
from tests.change_control_support import PLAN_PROMPT, ccenv, prompts_root

CC = "/api/change-control"
AL = "/api/action-layer"


def _body(pid, **over):
    body = {"tier": "T1", "kind": "step_params", "project_id": pid, "node_key": "n_split",
            "rationale": "сократить ячейки",
            "candidate": {"step": "split", "values": {"cell_max_chars": 600}}}
    body.update(over)
    return body


async def _live_split(env):
    async with env.factory() as s:
        return (await s.get(Project, env.pid)).meta["node_step_params"]["split"]["cell_max_chars"]


def _gw(env):
    return CtxGateway(ActionContext(app=env.app, operator="x"))



async def test_reject_closes_an_open_proposal_and_changes_nothing_else(ccenv) -> None:
    view = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    r = await ccenv.client.post(f"{CC}/proposals/{view['id']}/reject", json={"reason": "не нужно"})
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["status"] == "rejected" and got["can_apply"] is False
    assert got["rejected_by"] == "local_web_operator" and got["reject_reason"] == "не нужно"
    assert got["rejected_at"]
    assert await _live_split(ccenv) == 400
    async with ccenv.factory() as s:
        assert (await s.scalars(select(cs.ConfigVersion))).all() == []
        hist = [h for h in (await s.scalars(select(al_store.ActionHistory))).all()
                if h.event == "change_rejected"]
    assert len(hist) == 1 and hist[0].outcome == "rejected" and hist[0].error == "не нужно"
    assert hist[0].params["proposal_id"] == view["id"] and hist[0].actor == "local_web_operator"
    again = await ccenv.client.post(f"{CC}/proposals/{view['id']}/reject", json={"reason": "ещё раз"})
    assert again.status_code == 409 and again.json()["detail"]["code"] == "status_not_rejectable"
    with pytest.raises(ChangeRefused) as exc:
        await service.apply(_gw(ccenv), view["id"], view["digest"], approved_by="local_web_operator")
    assert exc.value.code == "status_not_applicable"


async def test_reject_works_for_developer_review_and_validates_input(ccenv) -> None:
    t3 = (await ccenv.client.post(f"{CC}/proposals", json={
        "tier": "T3", "kind": "developer_review", "rationale": "нужен код",
        "candidate": {"summary": "правка", "diff": "-a\n+b"}})).json()
    r = await ccenv.client.post(f"{CC}/proposals/{t3['id']}/reject",
                                json={"reason": "принято к сведению"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    other = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    for body in ({"reason": "да"}, {}, {"reason": "норм", "status": "applied"}, {"reason": "x" * 501}):
        r = await ccenv.client.post(f"{CC}/proposals/{other['id']}/reject", json=body)
        assert r.status_code == 422, body
    r = await ccenv.client.post(f"{CC}/proposals/{'0' * 32}/reject", json={"reason": "нет такого"})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "proposal_not_found"
    assert (await ccenv.client.get(f"{CC}/proposals/{other['id']}")).json()["status"] == "proposed"


async def test_reject_refuses_approved_applied_and_non_humans(ccenv) -> None:
    view = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    for status in ("approved", "applied", "rolled_back", "failed"):
        async with ccenv.factory() as s:
            row = await s.get(cs.ChangeProposal, view["id"])
            row.status = status
            await s.commit()
        r = await ccenv.client.post(f"{CC}/proposals/{view['id']}/reject", json={"reason": "отмена"})
        assert r.status_code == 409, status
        assert r.json()["detail"]["code"] == "status_not_rejectable", status
    for who in ("planner:x", "model:y", "system"):
        with pytest.raises(ChangeRefused) as exc:
            await service.reject(view["id"], "отмена", actor=who)
        assert exc.value.code == "operator_required"
    assert await _live_split(ccenv) == 400


async def test_list_marks_proposals_with_an_outdated_base_as_stale(ccenv) -> None:
    first = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    second = (await ccenv.client.post(f"{CC}/proposals", json=_body(
        ccenv.pid, candidate={"step": "split", "values": {"cell_max_chars": 700}}))).json()
    items = (await ccenv.client.get(f"{CC}/proposals")).json()["items"]
    assert [i["stale"] for i in items] == [False, False]
    await service.apply(_gw(ccenv), first["id"], first["digest"], approved_by="local_web_operator")
    items = {i["id"]: i for i in (await ccenv.client.get(f"{CC}/proposals")).json()["items"]}
    assert items[first["id"]]["stale"] is False and items[first["id"]]["status"] == "applied"
    assert items[second["id"]]["stale"] is True
    assert (await ccenv.client.get(f"{CC}/proposals/{second['id']}")).json()["stale"] is True
    r = await ccenv.client.post(f"{CC}/proposals/{second['id']}/reject", json={"reason": "устарело"})
    assert r.json()["status"] == "rejected" and r.json()["stale"] is False
    assert await _live_split(ccenv) == 600



async def _prompt_base(env, node="n_plan"):
    r = await env.client.get(f"{CC}/prompt-base", params={"project_id": env.pid, "node_key": node})
    assert r.status_code == 200, r.text
    return r.json()


async def test_prompt_base_default_global_slot_and_override_sources(ccenv) -> None:
    base = await _prompt_base(ccenv)
    assert (base["source"], base["variant"], base["text"]) == ("default", "default", PLAN_PROMPT)
    assert base["text_chars"] == len(PLAN_PROMPT) and "missing_reason" not in base
    assert base["step"] == "plan" and base["node_type"] == "plan"
    new_text = PLAN_PROMPT.replace("Keep the structure", "Keep strictly the structure")
    prop = (await ccenv.client.post(f"{CC}/proposals", json={
        "tier": "T2", "kind": "prompt_text", "project_id": ccenv.pid, "node_key": "n_plan",
        "rationale": "уточнить", "candidate": {"text": new_text}})).json()
    assert prop["candidate"]["base_text_sha256"] == base["base_text_sha256"]
    prompt_active_global.set_global_active("plan", "plan_alt")
    glob = await _prompt_base(ccenv)
    assert (glob["source"], glob["variant"]) == ("global_active", "plan_alt")
    patch = {"meta": {"prompt_slot_variants": {"n_plan": {"main": "default"}}}}
    assert (await ccenv.client.patch(f"/api/projects/{ccenv.pid}", json=patch)).status_code == 200
    slot = await _prompt_base(ccenv)
    assert (slot["source"], slot["variant"], slot["text"]) == ("slot", "default", PLAN_PROMPT)
    assert (await ccenv.client.patch(f"/api/projects/{ccenv.pid}", json={
        "prompt_overrides": {"plan": "plan_alt"}})).status_code == 200
    ov = await _prompt_base(ccenv)
    assert (ov["source"], ov["variant"]) == ("project_override", "plan_alt")
    gpt = await _prompt_base(ccenv, "n_gpt")
    assert gpt["source"] == "default" and gpt["step"] == "excel_gpt"


async def test_prompt_base_reports_a_missing_file_and_bounds_the_text(ccenv) -> None:
    (ccenv.prompts / "01_plan" / "default.md").unlink()
    fell_back = await _prompt_base(ccenv)
    assert (fell_back["source"], fell_back["variant"]) == ("fallback", "plan_alt")
    (ccenv.prompts / "01_plan" / "plan_alt.md").unlink()
    miss = await _prompt_base(ccenv)
    assert miss["source"] == "missing" and miss["text"] == "" and miss["text_chars"] == 0
    assert "default" in miss["missing_reason"]
    big = "x" * (tiers.PROMPT_MAX_CHARS + 50)
    (ccenv.prompts / "01_plan" / "default.md").write_bytes(big.encode())
    cut = await _prompt_base(ccenv)
    assert cut["truncated"] is True and len(cut["text"]) == tiers.PROMPT_MAX_CHARS
    assert cut["text_chars"] == len(big) and cut["source"] == "default"


async def test_prompt_base_is_read_only_and_checks_the_node(ccenv) -> None:
    r = await ccenv.client.get(f"{CC}/prompt-base",
                               params={"project_id": ccenv.pid, "node_key": "n_hero"})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "node_type_not_allowed"
    r = await ccenv.client.get(f"{CC}/prompt-base",
                               params={"project_id": ccenv.pid, "node_key": "zzz"})
    assert r.json()["detail"]["code"] == "node_not_found"
    r = await ccenv.client.get(f"{CC}/prompt-base",
                               params={"project_id": ccenv.pid + 9, "node_key": "n_plan"})
    assert r.status_code == 404
    assert (await ccenv.client.get(f"{CC}/prompt-base")).status_code == 422
    for verb in ("post", "put", "patch", "delete"):
        assert (await getattr(ccenv.client, verb)(f"{CC}/prompt-base")).status_code in (404, 405)



async def test_effect_texts_are_readable_russian_without_internal_ids(ccenv) -> None:
    ensure_builtin_actions()
    view = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    step = {"action_id": "change.apply",
            "params": {"proposal_id": view["id"], "expected_digest": view["digest"]}}
    r = await ccenv.client.post(f"{AL}/plans/preview", json={"steps": [step]})
    assert r.status_code == 200, r.text
    effect = r.json()["preview"]["destructive_effects"][0]["text"]
    assert "change." not in effect and "project:" not in effect and "rollback" not in effect
    assert view["id"][:8] in effect and "отдельной версией" in effect
    await service.apply(_gw(ccenv), view["id"], view["digest"], approved_by="local_web_operator")
    rb = {"action_id": "change.rollback",
          "params": {"target": view["target"], "expected_version": 2}}
    r = await ccenv.client.post(f"{AL}/plans/preview", json={"steps": [rb]})
    assert r.status_code == 200, r.text
    text = r.json()["preview"]["destructive_effects"][0]["text"]
    assert "change." not in text and "project:" not in text and view["target"] not in text
    assert "параметры шага ноды «Разбивка на блоки», проект" in text and "версии 2" in text
    assert target_title("project:3:n_excel_gpt_2:prompt") == (
        "промпт ноды «Работа с GPT (n_excel_gpt_2)», проект 3")
    assert target_title("project:3:n_plan:options") == "настройки ноды «Сценарий», проект 3"
    assert target_title("project:3:n_other:options") == "настройки ноды «n_other», проект 3"
    assert target_title("garbage") == "настройка проекта"
    for aid in ("change.propose", "change.apply", "change.rollback", "change.get", "change.list"):
        spec = registry.get(aid)
        assert "change." not in spec.description_ru and "change." not in spec.title_ru, aid
