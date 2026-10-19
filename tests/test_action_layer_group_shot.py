from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Frame, Project
from app.services.action_layer import executor
from app.services.action_layer.builtin_group import _specs
from app.services.action_layer.spec import registry
from tests.action_layer_support import env

BASE = "/api/action-layer"


@pytest.fixture(autouse=True)
def _group_actions():
    specs = _specs()
    for s in specs:
        registry.unregister(s.id)
        registry.register(s)
    yield
    for s in specs:
        registry.unregister(s.id)


async def _run(env, steps, *, expect_approval=False):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})
    assert r.status_code == 200, r.text
    pid = r.json()["plan_id"]
    assert r.json()["preview"]["needs_approval"] is expect_approval
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 200
    await executor.wait(pid)
    return (await env.client.get(f"{BASE}/plans/{pid}")).json()


async def _frame_uuid(env, project, number=1):
    async with env.factory() as s:
        fr = await s.scalar(select(Frame).where(
            Frame.project_id == project, Frame.number == number))
        if not fr.uuid:
            fr.uuid = f"fr-{project}-{number}"
            await s.commit()
        return fr.uuid


async def test_shot_menu_edits_write_db_without_spending(env) -> None:
    project = await env.add_project(frames=2)
    uuid = await _frame_uuid(env, project)
    view = await _run(env, [
        {"action_id": "shot.edit_field", "params": {
            "project_id": project, "frame_uuid": uuid, "field": "action", "value": "герой входит"}},
        {"action_id": "shot.edit_field", "params": {
            "project_id": project, "frame_uuid": uuid, "field": "vo", "value": "новый закадр"}},
        {"action_id": "shot.add_cell", "params": {"project_id": project, "voiceover": "хвост"}},
        {"action_id": "shot.get_menu", "params": {"project_id": project}},
    ])
    assert view["status"] == "succeeded", view
    assert view["spent_credits"] == 0.0 and view["risk_max"] == "local_safe"
    assert view["steps"][3]["result"]["cell_count"] >= 2
    async with env.factory() as s:
        frames = (await s.scalars(select(Frame).where(Frame.project_id == project))).all()
        by_num = {f.number: f for f in frames}
        assert by_num[1].voiceover_text == "новый закадр"
        assert by_num[1].attrs.get("shot01_action") == "герой входит"
        assert any(f.voiceover_text == "хвост" for f in frames)


async def test_shot_edit_unknown_field_and_frame_rejected(env) -> None:
    project = await env.add_project(frames=1)
    uuid = await _frame_uuid(env, project)
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [{
        "action_id": "shot.edit_field", "params": {
            "project_id": project, "frame_uuid": uuid, "field": "status", "value": "x"}}]})
    assert r.status_code == 422
    view = await _run(env, [{"action_id": "shot.edit_field", "params": {
        "project_id": project, "frame_uuid": "no-such", "field": "action", "value": "x"}}])
    assert view["status"] == "failed" and "HTTP 400" in view["steps"][0]["error"]


async def test_project_stop_is_safe_when_nothing_runs(env) -> None:
    project = await env.add_project(frames=1)
    view = await _run(env, [{"action_id": "project.stop", "params": {"project_id": project}}])
    assert view["status"] == "succeeded", view
    assert set(view["steps"][0]["result"]) >= {"stopped", "message"}


async def test_group_catalog_and_insert_into_canvas(env) -> None:
    project = await env.add_project(frames=1)
    async with env.factory() as s:
        (await s.get(Project, project)).meta = {"canvas_graph": {"nodes": [
            {"id": "n_topic", "type": "topic", "position": {"x": 0, "y": 0}, "data": {}}],
            "edges": []}}
        await s.commit()
    view = await _run(env, [
        {"action_id": "group.list", "params": {}},
        {"action_id": "group.get", "params": {"group_id": "script_frames_qc"}},
        {"action_id": "group.insert", "params": {
            "project_id": project, "group_id": "script_frames_qc", "after": "n_topic"}},
    ])
    assert view["status"] == "succeeded", [x.get("error") for x in view["steps"]]
    groups = view["steps"][0]["result"]["groups"]
    assert any(g["id"] == "script_frames_qc" and g["builtin"] for g in groups)
    async with env.factory() as s:
        meta = (await s.get(Project, project)).meta or {}
    ids = [n["id"] for n in meta["canvas_graph"]["nodes"]]
    assert any(i.endswith("_fw_shots") for i in ids)


async def test_group_unknown_and_builtin_protected(env) -> None:
    for step in (
        {"action_id": "group.get", "params": {"group_id": "no_such_group"}},
        {"action_id": "group.delete", "params": {"group_id": "script_frames_qc"}},
        {"action_id": "group.update", "params": {"group_id": "script_frames_qc", "title": "x"}},
        {"action_id": "group.update", "params": {"group_id": "script_frames_qc"}},
    ):
        r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [step]})
        assert r.status_code == 422, step


async def test_group_delete_needs_approval(env, monkeypatch) -> None:
    from app.web.routers import node_groups as router_mod
    calls = []
    monkeypatch.setattr(router_mod, "delete_custom_group", lambda gid: calls.append(gid))
    monkeypatch.setattr(router_mod, "list_node_groups", lambda: [
        {"id": "my_group", "title": "t", "builtin": False}])
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [{
        "action_id": "group.delete", "params": {"group_id": "my_group"}}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["preview"]["needs_approval"] is True
    assert body["preview"]["risk_max"] == "destructive"
    assert "my_group" in body["preview"]["destructive_effects"][0]["text"]
    assert (await env.client.post(f"{BASE}/plans/{body['plan_id']}/run")).status_code == 403
    assert calls == []
