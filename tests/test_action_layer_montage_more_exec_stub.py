from __future__ import annotations

import pytest

from app.services.action_layer import builtin_montage_more as bm
from app.services.action_layer.spec import ensure_builtin_actions, registry

ensure_builtin_actions()

FLUX = {"image_generator": "flux_2_pro", "image_resolution": "2k"}
KLING = {"video_generator": "kling_v3_turbo_i2v", "video_resolution": "720p"}


class FakeCtx:

    def __init__(self, routes: dict) -> None:
        self.routes = routes
        self.calls: list[tuple] = []

    async def call(self, method, path, *, params=None, json=None, timeout=120.0):
        self.calls.append((method, path, params, json))
        hit = self.routes.get((method, path))
        if hit is None:
            raise AssertionError(f"unexpected call {method} {path}")
        value = hit(params, json) if callable(hit) else hit
        if isinstance(value, Exception):
            raise value
        return value

    def posted(self, path_suffix: str) -> list[tuple]:
        return [c for c in self.calls if c[0] == "POST" and c[1].endswith(path_suffix)]


CANVAS = {"canvas_graph": {"nodes": [{"id": "n1", "type": "images",
                                      "data": {"modelId": "flux-2-pro"}}], "edges": []}}


def project(**over) -> dict:
    return {"id": 1, "meta": CANVAS, "image_generator": "flux_2_pro", "image_resolution": "2k",
            "video_generator": "kling_v3_turbo_i2v", "video_resolution": "720p", **over}


def board(ops=(), frames=()) -> dict:
    return {"frames": list(frames), "meta": {"pending_ops": list(ops), "video_trims": {}}}


def routes(ops=(), frames=(), **extra) -> dict:
    base = "/projects/1/montage-board"
    r = {("GET", "/projects/1"): project(), ("GET", base): board(ops, frames),
         ("POST", base + "/queue"): lambda p, j: {"ok": True, "pending_ops": j["pending_ops"]},
         ("POST", base + "/apply"): {"started": True, "ok": True,
                                     "job": {"status": "running", "total_ops": 2}},
         ("POST", "/projects/1/stop"): {"message": "ok"}}
    r.update(extra)
    return r


IMG1 = {"type": "image_regen", "frame_number": 1, "shot": 1}
IMG2 = {"type": "image_ai_change", "frame_number": 2, "shot": 1}
VID1 = {"type": "video_regen", "frame_number": 1, "shot": 1}


def spec(action_id):
    return registry.get(action_id)


async def test_apply_queue_posts_apply_directly_for_video_only_queue() -> None:
    ctx = FakeCtx(routes(ops=[VID1]))
    p = {"project_id": 1, "max_image_ops": 0, "max_video_ops": 1, **KLING}
    out = await spec("montage.apply_queue").executor(ctx, p)
    assert out["apply_started"] is True and out["video_ops"] == 1 and out["image_ops"] == 0
    (call,) = ctx.posted("/apply")
    assert call[3] == {"pending_ops": [VID1], "frame_numbers": []}


async def test_apply_queue_sends_images_first_and_filters_by_frame() -> None:
    ctx = FakeCtx(routes(ops=[VID1, IMG2, IMG1]))
    p = {"project_id": 1, "frame_numbers": [1], "max_image_ops": 1, "max_video_ops": 1,
         **FLUX, **KLING}
    out = await spec("montage.apply_queue").executor(ctx, p)
    assert out["image_ops"] == 1 and out["video_ops"] == 1
    body = ctx.posted("/apply")[0][3]
    assert [o["type"] for o in body["pending_ops"]] == ["image_ai_change", "image_regen", "video_regen"]
    assert body["frame_numbers"] == [1]


async def test_apply_queue_refuses_bigger_queue_or_other_model_before_any_apply() -> None:
    s = spec("montage.apply_queue")
    ctx = FakeCtx(routes(ops=[IMG1, IMG2]))
    with pytest.raises(RuntimeError, match="больше заявленной"):
        await s.executor(ctx, {"project_id": 1, "max_image_ops": 1, "max_video_ops": 0, **FLUX})
    ctx = FakeCtx(routes(ops=[IMG1]))
    with pytest.raises(RuntimeError, match="не совпадает"):
        await s.executor(ctx, {"project_id": 1, "max_image_ops": 1, "max_video_ops": 0,
                               "image_generator": "seedream_5_pro", "image_resolution": "2k"})
    ctx = FakeCtx(routes(ops=[]))
    with pytest.raises(RuntimeError, match="нечего применять"):
        await s.executor(ctx, {"project_id": 1, "max_image_ops": 1, "max_video_ops": 0, **FLUX})
    for c in (ctx,):
        assert not c.posted("/apply")


async def test_apply_queue_already_running_is_an_error() -> None:
    r = routes(ops=[IMG1])
    r[("POST", "/projects/1/montage-board/apply")] = {"started": False, "already_running": True,
                                                       "ok": False}
    with pytest.raises(RuntimeError, match="уже идёт"):
        await spec("montage.apply_queue").executor(
            FakeCtx(r), {"project_id": 1, "max_image_ops": 1, "max_video_ops": 0, **FLUX})


async def test_apply_poll_maps_job_states_and_reports_no_credits() -> None:
    s = spec("montage.apply_queue")
    started = {"apply_started": True}

    def job(**kw):
        return FakeCtx({("GET", "/projects/1/montage-board/apply-status"): {"job": kw}})

    p = {"project_id": 1}
    st = await s.poll(job(status="running", done_ops=1, total_ops=4), p, started)
    assert not st.done and st.progress == 0.25 and st.note == "1/4"
    st = await s.poll(job(status="done", done_ops=4, total_ops=4), p, started)
    assert st.done and st.ok and st.progress == 1.0
    assert st.credits_spent is None
    st = await s.poll(job(status="error", error="Outsee 500"), p, started)
    assert st.done and not st.ok and st.error == "Outsee 500"
    st = await s.poll(job(status="cancelled"), p, started)
    assert st.done and not st.ok and "остановлено" in st.error
    st = await s.poll(job(status="idle"), p, started)
    assert st.done and not st.ok
    st = await s.poll(FakeCtx({}), p, {"apply_started": False})
    assert st.done and st.ok


async def test_stop_calls_project_stop() -> None:
    ctx = FakeCtx(routes())
    for action_id in ("montage.apply_queue", "montage.run_montage", "montage.recover_outsee",
                      "montage.align_audio", "montage.improve_scene"):
        await spec(action_id).stop(ctx, {"project_id": 1})
    assert [c[1] for c in ctx.calls] == ["/projects/1/stop"] * 5


def improve_routes(image_ops, queue=()):
    base = "/projects/1/montage-board"
    r = routes(ops=queue)
    r[("POST", base + "/frames/5/scene-improve")] = {"ok": True, "image_ops": image_ops,
                                                      "inserted_frames": 2, "message": "готово"}
    return r


IMPROVE = {"project_id": 1, "frame_id": 5, "prompt": "ночь", "max_image_ops": 3, **FLUX}


async def test_improve_scene_runs_llm_then_queues_and_applies_scene_images() -> None:
    ops = [{"type": "image_ai_change", "frame_number": 1, "shot": 1, "slot": "parent",
            "instruction": "a"},
           {"type": "image_ai_change", "frame_number": 1, "shot": 1, "instruction": "b"},
           {"type": "video_regen", "frame_number": 9}]
    ctx = FakeCtx(improve_routes(ops, queue=[IMG1, {"type": "image_regen", "frame_number": 7}]))
    out = await spec("montage.improve_scene").executor(ctx, IMPROVE)
    assert out["improved"] and out["applied"] and out["apply_started"] and out["image_ops"] == 2
    improve_body = ctx.posted("/scene-improve")[0][3]
    assert improve_body == {"prompt": "ночь", "anchors": []}
    queue = ctx.posted("/queue")[0][3]["pending_ops"]
    assert [(o["frame_number"], o.get("slot")) for o in queue] == [(7, None), (1, "parent"), (1, None)]
    apply_body = ctx.posted("/apply")[0][3]
    assert apply_body["frame_numbers"] == [1]


async def test_improve_scene_does_not_apply_more_ops_than_declared() -> None:
    ops = [{"type": "image_ai_change", "frame_number": n, "shot": 1} for n in range(1, 6)]
    ctx = FakeCtx(improve_routes(ops))
    out = await spec("montage.improve_scene").executor(ctx, IMPROVE)
    assert out["improved"] and out["applied"] is False and out["apply_started"] is False
    assert "больше заявленных" in out["reason"]
    assert not ctx.posted("/apply") and not ctx.posted("/queue")


async def test_improve_scene_does_not_apply_when_video_is_queued_for_scene_frames() -> None:
    ops = [{"type": "image_ai_change", "frame_number": 1, "shot": 1}]
    ctx = FakeCtx(improve_routes(ops, queue=[VID1]))
    out = await spec("montage.improve_scene").executor(ctx, IMPROVE)
    assert out["applied"] is False and "видео" in out["reason"]
    assert not ctx.posted("/apply")


async def test_improve_scene_checks_model_before_spending_on_llm() -> None:
    ctx = FakeCtx(improve_routes([]))
    with pytest.raises(RuntimeError, match="не совпадает"):
        await spec("montage.improve_scene").executor(
            ctx, {**IMPROVE, "image_generator": "flux_2_pro", "image_resolution": "1k"})
    assert not ctx.posted("/scene-improve")


async def test_generate_scene_images_builds_ops_from_board_and_applies() -> None:
    frames = [{"number": 1, "shot_kind": "parent", "has_end_still": True},
              {"number": 2, "shot_kind": "child", "shot_parent_number": 1}]
    ctx = FakeCtx(routes(frames=frames))
    out = await spec("montage.generate_scene_images").executor(
        ctx, {"project_id": 1, "frame_numbers": [1, 2], "scene_prompt": "утро", **FLUX})
    assert out["applied"] and out["image_ops"] == 4
    assert ctx.posted("/apply")[0][3]["frame_numbers"] == [1, 2]


async def test_generate_scene_images_fails_loudly_on_video_conflict_or_no_live_frames() -> None:
    frames = [{"number": 1, "shot_kind": "parent"}]
    ctx = FakeCtx(routes(ops=[VID1], frames=frames))
    with pytest.raises(RuntimeError, match="видео"):
        await spec("montage.generate_scene_images").executor(
            ctx, {"project_id": 1, "frame_numbers": [1], **FLUX})
    assert not ctx.posted("/apply")
    ctx = FakeCtx(routes(frames=[{"number": 1, "shot_leftover": True}]))
    with pytest.raises(RuntimeError, match="нет живых кадров"):
        await spec("montage.generate_scene_images").executor(
            ctx, {"project_id": 1, "frame_numbers": [1], **FLUX})


async def test_llm_actions_map_params_to_endpoints() -> None:
    base = "/projects/1/montage-board/frames/5"
    ctx = FakeCtx({("POST", base + "/scene-action-generate"): {
        "ok": True, "frame_id": 5, "frame_number": 1, "replace_ns": [2], "chain": "1. дом — шаг"},
        ("POST", base + "/scene-variants"): {"variants": ["a", "b"]}})
    out = await spec("montage.generate_scene").executor(
        ctx, {"project_id": 1, "frame_id": 5, "prompt": "x", "replace_ns": [2]})
    assert out["replace_ns"] == [2]
    assert ctx.calls[0][3] == {"prompt": "x", "replace_ns": [2]}
    out = await spec("montage.suggest_scene_variants").executor(
        ctx, {"project_id": 1, "frame_id": 5, "kind": "action", "desc": "d"})
    assert out["variants"] == ["a", "b"] and ctx.calls[1][3] == {"kind": "action", "desc": "d", "count": 3}


async def test_coverage_backed_actions_send_expected_bodies() -> None:
    cov = ("POST", "/projects/1/montage-board/coverage")
    ctx = FakeCtx({cov: {"ok": True}})
    await spec("montage.set_scene_chain").executor(
        ctx, {"project_id": 1, "frame_number": 2, "action": "а → б"})
    await spec("montage.set_anchors").executor(ctx, {"project_id": 1, "frame_number": 2, "anchors": [
        {"anchor": "дом", "change": "светлее", "main": True}]})
    await spec("montage.set_voiceover_span").executor(
        ctx, {"project_id": 1, "frame_number": 2, "text": "кусок", "start": 3, "end": 9})
    await spec("montage.set_voiceover_span").executor(
        ctx, {"project_id": 1, "frame_number": 2, "clear": True})
    await spec("montage.delete_child_frame").executor(ctx, {"project_id": 1, "frame_number": 3})
    bodies = [c[3] for c in ctx.calls]
    assert bodies[0] == {"type": "coverage_scene_action", "frame_number": 2, "shot": 1,
                         "action": "а → б"}
    assert bodies[1]["anchors"] == [{"якорь": "дом", "изменение": "светлее", "главный": True}]
    assert bodies[2] == {"type": "coverage_vo_span", "frame_number": 2, "start": 3, "end": 9,
                         "text": "кусок"}
    assert bodies[3] == {"type": "coverage_vo_span", "frame_number": 2, "clear": True}
    assert bodies[4] == {"type": "coverage_delete", "frame_number": 3}


async def test_queue_cancel_and_trim_use_queue_endpoint_with_force_clear_only_when_empty() -> None:
    base = "/projects/1/montage-board"
    ctx = FakeCtx(routes(ops=[IMG1, IMG2]))
    out = await spec("montage.cancel_queue_ops").executor(
        ctx, {"project_id": 1, "ops": [{"type": "image_regen", "frame_number": 1}]})
    assert out == {"removed": 1, "left_in_queue": 1}
    assert ctx.posted("/queue")[0][3] == {"pending_ops": [IMG2], "force_clear": False}
    ctx = FakeCtx(routes(ops=[IMG1]))
    await spec("montage.cancel_queue_ops").executor(ctx, {"project_id": 1, "clear_all": True})
    assert ctx.posted("/queue")[0][3] == {"pending_ops": [], "force_clear": True}
    ctx = FakeCtx(routes(ops=[IMG1]))
    out = await spec("montage.set_video_trim").executor(
        ctx, {"project_id": 1, "frame_number": 4, "shot": 2, "start": 0.5, "end": 3.0})
    assert out["key"] == "4:2" and ctx.posted("/queue")[0][3]["video_trims"] == {
        "4:2": {"start": 0.5, "end": 3.0}}
    with pytest.raises(ValueError):
        await spec("montage.set_video_trim").executor(
            FakeCtx(routes()), {"project_id": 1, "frame_number": 4, "shot": 1, "start": 3, "end": 1})
    del base


async def test_job_starters_report_already_running_and_pass_flags() -> None:
    base = "/projects/1/montage-board"
    ctx = FakeCtx({("POST", base + "/montage"): {"started": True},
                   ("POST", base + "/recover-outsee"): {"started": False, "already_running": True},
                   ("POST", "/projects/1/audio-align"): {"started": True, "job": {"method": "silence"}}})
    assert (await spec("montage.run_montage").executor(ctx, {"project_id": 1}))["started"]
    with pytest.raises(RuntimeError, match="уже идёт"):
        await spec("montage.recover_outsee").executor(ctx, {"project_id": 1})
    out = await spec("montage.align_audio").executor(
        ctx, {"project_id": 1, "method": "silence", "force_asr": True})
    assert out["method"] == "silence"
    assert ctx.calls[-1][2] == {"method": "silence", "force_asr": True, "run_assemble": False}


async def test_set_assemble_params_merges_into_existing_node_step_params() -> None:
    nsp = {"plan": {"duration_seconds": 60}, "assemble": {"bgm_level": 40, "send_to_main_pc": True}}
    ctx = FakeCtx({("GET", "/projects/1"): {"meta": {"node_step_params": nsp, "x": 1}},
                   ("PATCH", "/projects/1"): {}})
    await spec("montage.set_assemble_params").executor(
        ctx, {"project_id": 1, "bgm_level": 70, "subtitles_enabled": True})
    sent = ctx.calls[-1][3]["meta"]["node_step_params"]
    assert sent["plan"] == {"duration_seconds": 60}
    assert sent["assemble"] == {"bgm_level": 70, "send_to_main_pc": True, "subtitles_enabled": True}


async def test_cost_basis_reports_priced_and_unpriced() -> None:
    r = routes(ops=[IMG1, VID1])
    r[("GET", "/projects/1")] = project(video_generator=None, video_resolution=None)
    out = await spec("montage.get_cost_basis").executor(FakeCtx(r), {"project_id": 1})
    assert out["effective"]["image_generator"] == "flux_2_pro" and out["image_priced"] is True
    assert out["effective"]["video_generator"] == "veo_3_fast" and out["video_priced"] is False
    assert out["queue"] == {"image_ops": 1, "video_ops": 1}
