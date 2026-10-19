from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import Frame, Project
from app.services.action_layer import executor
from tests.action_layer_support import env

BASE = "/api/action-layer"


async def _run_plan(env, steps, text=""):
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": steps, "request_text": text})
    assert r.status_code == 200, r.text
    pid = r.json()["plan_id"]
    assert r.json()["preview"]["needs_approval"] is False
    assert (await env.client.post(f"{BASE}/plans/{pid}/run")).status_code == 200
    await executor.wait(pid)
    return (await env.client.get(f"{BASE}/plans/{pid}")).json()


async def test_read_actions_return_board_and_job_states(env) -> None:
    project = await env.add_project(frames=3)
    view = await _run_plan(env, [
        {"action_id": "montage.get_board", "params": {"project_id": project}},
        {"action_id": "montage.apply_status", "params": {"project_id": project}},
        {"action_id": "montage.montage_status", "params": {"project_id": project}},
        {"action_id": "montage.recover_status", "params": {"project_id": project}},
    ])
    assert view["status"] == "succeeded", view
    board = view["steps"][0]["result"]
    assert board["frame_count"] == 3 and len(board["frames"]) == 3
    assert board["frames"][0]["frame_id"] and board["frames"][0]["voiceover_text"] == "реплика 1"
    assert board["pending_ops"] == []
    for step in view["steps"][1:]:
        assert step["result"]["job"].get("status") in ("idle", None)


async def test_write_actions_change_db_and_never_spend(env) -> None:
    project = await env.add_project(frames=2)
    ids = await env.frame_ids(project)
    view = await _run_plan(env, [
        {"action_id": "montage.set_voiceover",
         "params": {"project_id": project, "frame_id": ids[1], "text": "новый закадр"}},
        {"action_id": "montage.set_coverage",
         "params": {"project_id": project, "frame_number": 2, "type": "coverage_plan",
                    "value": "КРУПНЫЙ"}},
        {"action_id": "montage.queue_ops", "params": {"project_id": project, "ops": [
            {"type": "image_regen", "frame_number": 1, "shot": 1},
            {"type": "video_regen_prompt", "frame_number": 2, "prompt": "плавный наезд"}]}},
        {"action_id": "montage.queue_ops", "params": {"project_id": project, "ops": [
            {"type": "image_regen", "frame_number": 1, "shot": 1},
            {"type": "image_ai_change", "frame_number": 2, "instruction": "теплее"}]}},
        {"action_id": "montage.swap_shots",
         "params": {"project_id": project, "frame_number": 1, "kind": "both"}},
        {"action_id": "montage.get_board", "params": {"project_id": project}},
    ], text="поправь монтаж")
    assert view["status"] == "succeeded", view
    assert view["spent_credits"] == 0.0 and view["risk_max"] == "local_safe"
    results = [s["result"] for s in view["steps"]]
    assert results[0]["voiceover_text"] == "новый закадр"
    assert results[1]["ok"] is True
    assert results[2]["queued"] == 2 and results[2]["applied"] is False
    assert results[3]["total_in_queue"] == 3
    assert results[4]["prompts_swapped"] is True
    board = results[5]
    assert board["frames"][0]["voiceover_text"] == "новый закадр"
    assert sorted(o["type"] for o in board["pending_ops"]) == [
        "image_ai_change", "image_regen", "video_regen_prompt"]

    async with env.factory() as s:
        frames = {f.number: f for f in (await s.scalars(select(Frame).where(
            Frame.project_id == project))).all()}
        assert frames[1].voiceover_text == "новый закадр"
        assert frames[2].attrs.get("крупность") == "КРУПНЫЙ"
        assert frames[1].image_prompt != "img 1"
    async with env.factory() as s:
        meta = (await s.get(Project, project)).meta or {}
    assert "apply_job" not in (meta.get("montage_board") or meta)


async def test_invalid_montage_params_fail_preview_not_run(env) -> None:
    project = await env.add_project(frames=1)
    for bad in (
        {"action_id": "montage.set_coverage", "params": {
            "project_id": project, "frame_number": 1, "type": "coverage_delete", "value": "x"}},
        {"action_id": "montage.set_voiceover", "params": {"project_id": project, "frame_id": 1}},
        {"action_id": "montage.queue_ops", "params": {"project_id": project, "ops": [
            {"type": "image_regen", "frame_number": 1, "evil": 1}]}},
        {"action_id": "montage.swap_shots", "params": {
            "project_id": project, "frame_number": 1, "kind": "delete"}},
        {"action_id": "montage.get_board", "params": {"project_id": project, "x": 1}},
    ):
        r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [bad]})
        assert r.status_code == 422, bad
    r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [
        {"action_id": "montage.get_board", "params": {"project_id": 9999}}]})
    assert r.status_code == 422


async def test_backend_error_becomes_failed_step(env) -> None:
    project = await env.add_project(frames=1)
    view = await _run_plan(env, [
        {"action_id": "montage.set_voiceover",
         "params": {"project_id": project, "frame_id": 987654, "text": "x"}},
        {"action_id": "montage.get_board", "params": {"project_id": project}}])
    assert view["status"] == "failed"
    assert view["steps"][0]["status"] == "failed" and (
        "HTTP 404" in view["steps"][0]["error"] or "правка не начата" in view["steps"][0]["error"])
    assert view["steps"][1]["status"] == "skipped"


async def test_queue_and_swap_blocked_while_apply_is_running(env, monkeypatch) -> None:
    from app.services import montage_board_apply_job as job_mod
    project = await env.add_project(frames=1)
    monkeypatch.setattr(job_mod, "get_apply_job", lambda p: {"status": "running"})
    for step in (
        {"action_id": "montage.queue_ops", "params": {
            "project_id": project, "ops": [{"type": "image_regen", "frame_number": 1}]}},
        {"action_id": "montage.swap_shots", "params": {"project_id": project, "frame_number": 1}},
    ):
        r = await env.client.post(f"{BASE}/plans/preview", json={"steps": [step]})
        assert r.status_code == 422
        assert "no_apply_running" in str(r.json()["detail"]["preview"]["errors"])
