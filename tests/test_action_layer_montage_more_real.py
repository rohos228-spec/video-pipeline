"""Действия на реальных эндпоинтах монтажа и временной БД. Платные планы только preview, не run."""
from __future__ import annotations

from sqlalchemy import select

from app.models import Frame, Project
from app.services.action_layer import executor
from tests.action_layer_support import env  # noqa: F401

BASE = "/api/action-layer"
CANVAS = {"canvas_graph": {"nodes": [{"id": "n1", "type": "images",
                                      "data": {"modelId": "flux-2-pro"}}], "edges": []}}


async def _setup(env, frames=3, *, kie=True) -> int:
    pid = await env.add_project(frames=frames)
    async with env.factory() as s:
        p = await s.get(Project, pid)
        if kie:
            p.meta = dict(CANVAS)
            p.image_generator, p.image_resolution = "flux_2_pro", "2k"
            p.video_generator, p.video_resolution = "seedance_1_5_pro", "720p"
        await s.commit()
    return pid


async def _preview(env, steps):
    return await env.client.post(f"{BASE}/plans/preview", json={"steps": steps})


async def _run(env, steps, *, approve=False):
    r = await _preview(env, steps)
    assert r.status_code == 200, r.text
    plan = r.json()["plan_id"]
    if approve:
        a = await env.client.post(f"{BASE}/plans/{plan}/approve")
        assert a.status_code == 200, a.text
    assert (await env.client.post(f"{BASE}/plans/{plan}/run")).status_code == 200
    await executor.wait(plan)
    return (await env.client.get(f"{BASE}/plans/{plan}")).json()


async def test_cost_basis_reads_project_settings_and_queue(env) -> None:
    pid = await _setup(env)
    view = await _run(env, [
        {"action_id": "montage.queue_ops", "params": {"project_id": pid, "ops": [
            {"type": "image_regen", "frame_number": 1}, {"type": "video_regen", "frame_number": 2}]}},
        {"action_id": "montage.get_cost_basis", "params": {"project_id": pid}}])
    assert view["status"] == "succeeded", view
    out = view["steps"][1]["result"]
    assert out["effective"]["image_generator"] == "flux_2_pro"
    assert out["effective"]["image_resolution"] == "2k"
    assert out["image_priced"] and out["video_priced"]
    assert out["queue"] == {"image_ops": 1, "video_ops": 1}


async def test_apply_queue_preview_has_real_cost_and_needs_approval(env) -> None:
    from app.services.action_layer import builtin_montage_more as bm
    pid = await _setup(env)
    await _run(env, [{"action_id": "montage.queue_ops", "params": {"project_id": pid, "ops": [
        {"type": "image_regen", "frame_number": 1}, {"type": "image_ai_change", "frame_number": 2},
        {"type": "video_regen", "frame_number": 3}]}}])
    params = {"project_id": pid, "max_image_ops": 2, "max_video_ops": 1,
              "image_generator": "flux_2_pro", "image_resolution": "2k",
              "video_generator": "seedance_1_5_pro", "video_resolution": "720p"}
    r = await _preview(env, [{"action_id": "montage.apply_queue", "params": params}])
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["risk_max"] == "destructive"
    step = pv["steps"][0]
    assert step["spends_money"] and step["destructive"] and step["reversible"] is False
    vunit = bm._kie_unit("video", "seedance_1_5_pro", "720p")[0]
    assert step["cost"]["known"] and step["cost"]["low"] == round(2 * 7 + vunit, 2)
    assert step["cost"]["high"] > step["cost"]["low"]
    assert pv["cost"]["unknown_steps"] == 0 and pv["destructive_effects"]
    assert pv["budget"]["fits"] is True
    # одно и то же, но очередь больше заявленной -> предусловие, а не запуск
    bad = await _preview(env, [{"action_id": "montage.apply_queue",
                                "params": {**params, "max_image_ops": 1}}])
    assert bad.status_code == 422 and "queue_within_limits" in str(bad.json())
    # другая модель, чем в проекте
    bad = await _preview(env, [{"action_id": "montage.apply_queue",
                                "params": {**params, "image_generator": "seedream_5_pro"}}])
    assert bad.status_code == 422 and "cost_basis_matches" in str(bad.json())


async def test_unpriced_outsee_model_blocks_approval_fail_closed(env) -> None:
    pid = await _setup(env, kie=False)     # без ноды картинок: модель по умолчанию, Outsee
    await _run(env, [{"action_id": "montage.queue_ops", "params": {
        "project_id": pid, "ops": [{"type": "image_regen", "frame_number": 1}]}}])
    r = await _preview(env, [{"action_id": "montage.apply_queue", "params": {
        "project_id": pid, "max_image_ops": 1, "max_video_ops": 0,
        "image_generator": "gpt_image_2_vip", "image_resolution": "2k"}}])
    assert r.status_code == 200, r.text
    pv = r.json()["preview"]
    assert pv["cost"]["unknown_steps"] == 1 and pv["cost"]["known"] is False
    assert pv["budget"]["fits"] is False
    a = await env.client.post(f"{BASE}/plans/{r.json()['plan_id']}/approve")
    assert a.status_code == 409 and a.json()["detail"]["code"] == "over_budget"


async def test_apply_preview_blocked_while_apply_running_or_queue_empty(env, monkeypatch) -> None:
    from app.services import montage_board_apply_job as job_mod
    pid = await _setup(env)
    params = {"project_id": pid, "max_image_ops": 1, "max_video_ops": 0,
              "image_generator": "flux_2_pro", "image_resolution": "2k"}
    r = await _preview(env, [{"action_id": "montage.apply_queue", "params": params}])
    assert r.status_code == 422 and "queue_within_limits" in str(r.json())   # очередь пуста
    await _run(env, [{"action_id": "montage.queue_ops", "params": {
        "project_id": pid, "ops": [{"type": "image_regen", "frame_number": 1}]}}])
    monkeypatch.setattr(job_mod, "get_apply_job", lambda p: {"status": "running"})
    r = await _preview(env, [{"action_id": "montage.apply_queue", "params": params}])
    assert r.status_code == 422 and "no_apply_running" in str(r.json())


async def test_improve_and_scene_images_preview_costs_and_conflicts(env) -> None:
    pid = await _setup(env, frames=2)
    ids = await env.frame_ids(pid)
    imp = {"project_id": pid, "frame_id": ids[1], "prompt": "ночь", "max_image_ops": 4,
           "image_generator": "flux_2_pro", "image_resolution": "2k"}
    r = await _preview(env, [{"action_id": "montage.improve_scene", "params": imp}])
    assert r.status_code == 200, r.text
    step = r.json()["preview"]["steps"][0]
    assert step["cost"]["known"] and step["cost"]["high"] > 40
    gen = {"project_id": pid, "frame_numbers": [1, 2], "image_generator": "flux_2_pro",
           "image_resolution": "2k"}
    r = await _preview(env, [{"action_id": "montage.generate_scene_images", "params": gen}])
    assert r.status_code == 200 and r.json()["preview"]["steps"][0]["cost"]["known"]
    for bad in (
        {"action_id": "montage.improve_scene", "params": {**imp, "frame_id": 999999}},
        {"action_id": "montage.generate_scene_images", "params": {**gen, "frame_numbers": [1, 77]}},
    ):
        assert (await _preview(env, [bad])).status_code == 422
    for action_id, extra in (("montage.generate_scene", {"prompt": "x"}),
                             ("montage.suggest_scene_variants", {"kind": "action"})):
        r = await _preview(env, [{"action_id": action_id, "params": {
            "project_id": pid, "frame_id": ids[1], **extra}}])
        assert r.status_code == 200, r.text
        st = r.json()["preview"]["steps"][0]
        assert st["cost"]["known"] and st["spends_money"]


async def test_local_actions_on_real_endpoints(env) -> None:
    pid = await _setup(env, frames=3)
    ids = await env.frame_ids(pid)
    view = await _run(env, [
        {"action_id": "montage.insert_frame", "params": {
            "project_id": pid, "after_frame_id": ids[1], "voiceover": "новый кадр"}},
        {"action_id": "montage.queue_ops", "params": {"project_id": pid, "ops": [
            {"type": "image_regen", "frame_number": 1}, {"type": "video_regen", "frame_number": 2}]}},
        {"action_id": "montage.set_video_trim", "params": {
            "project_id": pid, "frame_number": 2, "shot": 1, "start": 0.5, "end": 2.5}},
        {"action_id": "montage.cancel_queue_ops", "params": {
            "project_id": pid, "ops": [{"type": "image_regen", "frame_number": 1}]}},
        {"action_id": "montage.set_assemble_params", "params": {
            "project_id": pid, "bgm_level": 55, "subtitles_enabled": True}},
        {"action_id": "montage.get_board", "params": {"project_id": pid}},
    ])
    assert view["status"] == "succeeded", view
    assert view["spent_credits"] == 0.0 and view["risk_max"] == "local_safe"
    res = [s["result"] for s in view["steps"]]
    assert res[0]["number"] == 4 and res[2]["key"] == "2:1"
    assert res[3] == {"removed": 1, "left_in_queue": 1}
    board = res[5]
    assert board["frame_count"] == 4 and [o["type"] for o in board["pending_ops"]] == ["video_regen"]
    async with env.factory() as s:
        meta = (await s.get(Project, pid)).meta
    assert meta["node_step_params"]["assemble"] == {"bgm_level": 55, "subtitles_enabled": True}
    assert meta["canvas_graph"] == CANVAS["canvas_graph"]    # остальной meta не затёрт
    assert "montage_board" in meta or any("video_trims" in str(v) for v in meta.values())


async def test_empty_queue_cancel_uses_force_clear_and_arg_preconditions(env) -> None:
    pid = await _setup(env)
    view = await _run(env, [
        {"action_id": "montage.queue_ops", "params": {
            "project_id": pid, "ops": [{"type": "image_regen", "frame_number": 1}]}},
        {"action_id": "montage.cancel_queue_ops", "params": {"project_id": pid, "clear_all": True}},
        {"action_id": "montage.get_board", "params": {"project_id": pid}}])
    assert view["status"] == "succeeded", view
    assert view["steps"][1]["result"]["left_in_queue"] == 0
    assert view["steps"][2]["result"]["pending_ops"] == []
    for bad in ({"action_id": "montage.cancel_queue_ops", "params": {"project_id": pid}},
                {"action_id": "montage.set_assemble_params", "params": {"project_id": pid}},
                {"action_id": "montage.set_voiceover_span",
                 "params": {"project_id": pid, "frame_number": 1}},
                {"action_id": "montage.insert_frame", "params": {"project_id": pid, "kind": "child"}}):
        assert (await _preview(env, [bad])).status_code == 422, bad


async def test_destructive_delete_frame_needs_approval_and_removes_frame(env) -> None:
    pid = await _setup(env, frames=3)
    ids = await env.frame_ids(pid)
    steps = [{"action_id": "montage.delete_frame", "params": {"project_id": pid, "frame_id": ids[3]}}]
    r = await _preview(env, steps)
    pv = r.json()["preview"]
    assert pv["needs_approval"] and pv["destructive_effects"][0]["text"]
    run_no_approval = await env.client.post(f"{BASE}/plans/{r.json()['plan_id']}/run")
    assert run_no_approval.status_code == 403
    view = await _run(env, steps, approve=True)
    assert view["status"] == "succeeded", view
    assert view["steps"][0]["result"]["deleted"] == 1
    async with env.factory() as s:
        left = (await s.execute(select(Frame.number).where(Frame.project_id == pid))).scalars().all()
    assert sorted(left) == [1, 2]


async def test_structure_actions_reach_endpoints_and_report_backend_errors(env) -> None:
    pid = await _setup(env, frames=3)
    ids = await env.frame_ids(pid)
    view = await _run(env, [
        {"action_id": "montage.merge_scenes", "params": {
            "project_id": pid, "left_frame_id": ids[1], "right_frame_id": ids[2]}},
        {"action_id": "montage.explode_scene", "params": {"project_id": pid, "frame_id": ids[1]}},
    ], approve=True)
    first = view["steps"][0]
    assert first["status"] == "succeeded", view
    assert first["result"]["merged_frames"] >= 1
    # у синтетических кадров нет шотов: бэкенд отказывает, шаг падает с текстом ошибки
    assert view["status"] == "failed" and view["steps"][1]["status"] == "failed"
    assert "HTTP 400" in view["steps"][1]["error"]
    bad = await _run(env, [{"action_id": "montage.split_scene", "params": {
        "project_id": pid, "frame_id": ids[3]}}], approve=True)
    assert bad["status"] == "failed" and "HTTP 400" in bad["steps"][0]["error"]   # первый кадр сцены
