from __future__ import annotations

from tests.action_layer_support import env
from tests.change_control_support import ccenv, prompts_root

CC = "/api/change-control"


def _body(pid, **over):
    body = {"tier": "T1", "kind": "step_params", "project_id": pid, "node_key": "n_split",
            "rationale": "сократить ячейки",
            "candidate": {"step": "split", "values": {"cell_max_chars": 600}}}
    body.update(over)
    return body


async def test_post_creates_a_proposal_and_get_reads_it_back(ccenv) -> None:
    r = await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))
    assert r.status_code == 200, r.text
    view = r.json()
    assert view["status"] == "proposed" and view["tier"] == "T1" and view["can_apply"] is True
    assert view["proposer"] == "local_web_operator"
    assert view["target"] == f"project:{ccenv.pid}:n_split:step_params"
    assert view["diff"] == "split.cell_max_chars: 400 -> 600" and len(view["digest"]) == 64
    got = (await ccenv.client.get(f"{CC}/proposals/{view['id']}")).json()
    assert got["id"] == view["id"] and got["current_version"] == 0
    again = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    assert again["id"] == view["id"] and again["deduplicated"] is True


async def test_list_filters_and_hides_big_texts(ccenv) -> None:
    big = {"tier": "T2", "kind": "prompt_text", "project_id": ccenv.pid, "node_key": "n_plan",
           "rationale": "уточнить", "candidate": {"text": (
               "Plan master prompt. Topic: {{VAR:PROJECT_TOPIC}}. Write a plan and answer with "
               "JSON only.\nKeep strictly the structure of the answer.")}}
    t2 = (await ccenv.client.post(f"{CC}/proposals", json=big)).json()
    await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))
    r = (await ccenv.client.get(f"{CC}/proposals", params={"project_id": ccenv.pid})).json()
    assert r["count"] == 2
    item = next(i for i in r["items"] if i["id"] == t2["id"])
    assert "text" not in item["candidate"] and item["candidate"]["text_chars"] > 40 and item["diff"] == ""
    assert (await ccenv.client.get(f"{CC}/proposals/{t2['id']}")).json()["candidate"]["text"]
    only = (await ccenv.client.get(f"{CC}/proposals", params={"tier": "T2"})).json()
    assert [i["id"] for i in only["items"]] == [t2["id"]]
    none = (await ccenv.client.get(f"{CC}/proposals", params={"status": "applied"})).json()
    assert none["count"] == 0
    assert (await ccenv.client.get(f"{CC}/proposals", params={"tier": "T9"})).status_code == 422


async def test_refusals_carry_a_stable_code(ccenv) -> None:
    r = await ccenv.client.post(f"{CC}/proposals", json=_body(
        ccenv.pid, candidate={"step": "split", "values": {"cell_max_chars": 5000}}))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "lint_failed"
    assert r.json()["detail"]["errors"]
    r = await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid, node_key="n_hero"))
    assert r.json()["detail"]["code"] == "node_type_not_allowed"
    r = await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid + 99))
    assert r.status_code == 404 and r.json()["detail"]["code"] == "project_not_found"
    r = await ccenv.client.get(f"{CC}/proposals/{'0' * 32}")
    assert r.status_code == 404 and r.json()["detail"]["code"] == "proposal_not_found"


async def test_the_body_cannot_name_the_proposer_or_an_approval(ccenv) -> None:
    for extra in ({"proposer": "human"}, {"approved_by": "x"}, {"status": "applied"},
                  {"approval_id": "x"}):
        r = await ccenv.client.post(f"{CC}/proposals", json={**_body(ccenv.pid), **extra})
        assert r.status_code == 422, extra


async def test_there_is_no_apply_approve_or_rollback_endpoint(ccenv) -> None:
    view = (await ccenv.client.post(f"{CC}/proposals", json=_body(ccenv.pid))).json()
    paths = {p for p in ccenv.app.openapi()["paths"] if p.startswith("/api/change-control")}
    assert paths == {f"{CC}/allow-list", f"{CC}/proposals", f"{CC}/proposals/{{proposal_id}}",
                     f"{CC}/proposals/{{proposal_id}}/reject", f"{CC}/prompt-base",
                     f"{CC}/versions"}
    assert not [p for p in paths if any(w in p for w in ("apply", "approve", "rollback"))]
    for verb in ("get", "put", "patch", "delete"):
        r = await getattr(ccenv.client, verb)(f"{CC}/proposals/{view['id']}/reject")
        assert r.status_code in (404, 405), verb
    for tail in ("apply", "approve", "rollback"):
        for verb in ("post", "put", "patch", "delete"):
            r = await getattr(ccenv.client, verb)(f"{CC}/proposals/{view['id']}/{tail}")
            assert r.status_code in (404, 405), (verb, tail, r.status_code)
    for verb in ("put", "patch", "delete"):
        r = await getattr(ccenv.client, verb)(f"{CC}/proposals/{view['id']}")
        assert r.status_code in (404, 405)
    r = await ccenv.client.post(f"{CC}/versions", params={"target": view["target"]})
    assert r.status_code in (404, 405)


async def test_versions_and_allow_list(ccenv) -> None:
    target = f"project:{ccenv.pid}:n_split:step_params"
    r = (await ccenv.client.get(f"{CC}/versions", params={"target": target})).json()
    assert r == {"target": target, "current_version": 0, "items": []}
    bad = await ccenv.client.get(f"{CC}/versions", params={"target": "nope"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "bad_target"
    allow = (await ccenv.client.get(f"{CC}/allow-list")).json()
    assert allow["tiers"]["T3"]["applicable"] is False and allow["tiers"]["T0"]["applicable"] is False
    assert allow["tiers"]["T1"]["applicable"] and allow["tiers"]["T2"]["applicable"]
    assert set(allow["node_types"]) == {"plan", "script", "split", "excel_gpt"}
    assert allow["node_types"]["excel_gpt"]["node_options"] == [
        "outputMode", "emitKinds", "checkFix", "useSnapshot"]
    assert allow["step_param_limits"]["split"]["cell_max_chars"]["max"] == 2000
