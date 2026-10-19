from __future__ import annotations

import pytest

from app.services import kie_catalog
from app.services.action_layer import builtin_montage_more as bm
from app.services.action_layer.spec import ActionRegistry, ensure_builtin_actions, registry

ensure_builtin_actions()

EXPECTED = {
    "montage.cancel_queue_ops": ("local_safe", False, False, False, True, False),
    "montage.apply_queue": ("destructive", True, True, False, False, True),
    "montage.improve_scene": ("destructive", True, True, False, False, True),
    "montage.generate_scene_images": ("destructive", True, True, False, False, True),
    "montage.generate_scene": ("destructive", True, True, False, False, False),
    "montage.suggest_scene_variants": ("paid", True, False, False, True, False),
    "montage.set_scene_chain": ("destructive", False, True, False, True, False),
    "montage.set_anchors": ("destructive", False, True, False, False, False),
    "montage.set_voiceover_span": ("local_safe", False, False, False, True, False),
    "montage.delete_child_frame": ("destructive", False, True, False, False, False),
    "montage.insert_frame": ("local_safe", False, False, False, False, False),
    "montage.merge_scenes": ("destructive", False, True, False, False, False),
    "montage.split_scene": ("destructive", False, True, False, False, False),
    "montage.explode_scene": ("destructive", False, True, False, False, False),
    "montage.delete_frame": ("destructive", False, True, False, False, False),
    "montage.swap_slots": ("local_safe", False, False, True, False, False),
    "montage.move_image": ("local_safe", False, False, True, False, False),
    "montage.delete_image": ("destructive", False, True, False, True, False),
    "montage.delete_video": ("destructive", False, True, False, True, False),
    "montage.set_video_trim": ("local_safe", False, False, False, True, False),
    "montage.list_ref_assets": ("read", False, False, False, True, False),
    "montage.link_ref": ("local_safe", False, False, False, False, False),
    "montage.unlink_ref": ("local_safe", False, False, False, True, False),
    "montage.delete_manual_ref": ("destructive", False, True, False, True, False),
    "montage.recover_outsee": ("destructive", False, True, False, False, True),
    "montage.run_montage": ("destructive", False, True, False, True, True),
    "montage.align_audio": ("local_safe", False, False, False, True, True),
    "montage.set_assemble_params": ("local_safe", False, False, False, True, False),
    "montage.get_cost_basis": ("read", False, False, False, True, False),
}


def test_all_actions_registered_with_expected_flags() -> None:
    for action_id, flags in EXPECTED.items():
        spec = registry.get(action_id)
        got = (spec.risk, spec.spends_money, spec.destructive, spec.reversible,
               spec.idempotent, spec.is_job)
        assert got == flags, action_id
    assert len(registry.list(prefix="montage.")) == 8 + len(EXPECTED) + 6


def test_paid_have_estimators_and_destructive_have_effects_text() -> None:
    for spec in registry.list(prefix="montage."):
        if spec.spends_money:
            assert spec.estimator is not None, spec.id
        if spec.destructive:
            assert spec.destructive_effects is not None, spec.id
        if spec.is_job:
            assert spec.stop is not None, spec.id
        assert spec.needs_approval == (spec.spends_money or spec.destructive)


def test_register_more_rejects_duplicates_all_or_nothing() -> None:
    fresh = ActionRegistry()
    bm.register_more(fresh)
    restore = fresh.get("frame.restore")
    assert (restore.risk, restore.spends_money, restore.destructive, restore.reversible,
            restore.idempotent, restore.is_job) == ("destructive", False, True, True, True, False)
    assert len(fresh) == len(EXPECTED) + 1
    with pytest.raises(Exception):
        bm.register_more(fresh)
    assert len(fresh) == len(EXPECTED) + 1


def test_schemas_are_strict_and_reject_unknown_fields() -> None:
    for action_id in EXPECTED:
        errs = registry.validate_params(action_id, {"project_id": 1, "evil": 1})
        assert any("unknown field" in e for e in errs), action_id


@pytest.mark.parametrize("action_id,params", [
    ("montage.apply_queue", {"project_id": 1, "max_image_ops": 1}),
    ("montage.apply_queue", {"project_id": 1, "max_image_ops": 1, "max_video_ops": 0,
                             "image_resolution": "8k"}),
    ("montage.improve_scene", {"project_id": 1, "frame_id": 1, "prompt": "x", "max_image_ops": 99,
                               "image_generator": "flux_2_pro", "image_resolution": "2k"}),
    ("montage.set_video_trim", {"project_id": 1, "frame_number": 1, "shot": 3}),
    ("montage.delete_manual_ref", {"project_id": 1, "frame_number": 1, "ref_id": "a",
                                   "kind": "character"}),
    ("montage.swap_slots", {"project_id": 1, "kind": "audio", "a_frame": 1, "a_shot": 1,
                            "b_frame": 2, "b_shot": 1}),
])
def test_bad_params_are_rejected(action_id, params) -> None:
    assert registry.validate_params(action_id, params)


def test_kie_unit_matches_catalog_and_is_unknown_for_outsee() -> None:
    unit, _ = bm._kie_unit("image", "flux_2_pro", "2k")
    spec = kie_catalog.get_model("flux-2-pro")
    assert unit == kie_catalog.estimate_credits(spec, {"resolution": "2K"})["credits"] == 7
    assert bm._kie_unit("image", "flux_2_pro", "1k")[0] == 5
    assert bm._kie_unit("image", "gpt_image_2_vip", "2k")[0] is None
    assert bm._kie_unit("video", "veo_3_1_lite", "720p")[0] is None
    assert bm._kie_unit("video", "veo_3_fast", "1080p")[0] is None
    assert bm._kie_unit("image", "flux_2_pro", None)[0] is None
    assert bm._kie_unit("video", "kling_2_6", "720p")[0] is None
    vunit, _ = bm._kie_unit("video", "kling_v3_turbo_i2v", "720p")
    vspec = kie_catalog.get_model("kling-v3-turbo-i2v")
    want = kie_catalog.estimate_credits(vspec, {"duration": 5, "resolution": "720p",
                                                 "generate_audio": False})["credits"]
    assert vunit == want and vunit > 0


def test_apply_estimate_is_upper_bound_with_attempts_and_llm() -> None:
    spec = registry.get("montage.apply_queue")
    est = spec.estimate({"project_id": 1, "max_image_ops": 3, "max_video_ops": 0,
                         "image_generator": "flux_2_pro", "image_resolution": "2k"})
    assert est.known and est.low == 21.0
    assert est.high == pytest.approx(
        round(3 * bm.IMAGE_BILLED_ATTEMPTS * 7 + 3 * bm.LLM_OP_CALL_HIGH, 2))
    ev = spec.estimate({"project_id": 1, "max_image_ops": 0, "max_video_ops": 2,
                        "video_generator": "kling_v3_turbo_i2v", "video_resolution": "720p"})
    vunit = bm._kie_unit("video", "kling_v3_turbo_i2v", "720p")[0]
    assert ev.known and ev.low == round(2 * vunit, 2)
    assert ev.high >= 2 * bm.VIDEO_BILLED_ATTEMPTS * vunit


@pytest.mark.parametrize("p", [
    {"project_id": 1, "max_image_ops": 2, "max_video_ops": 0},
    {"project_id": 1, "max_image_ops": 2, "max_video_ops": 0,
     "image_generator": "gpt_image_2_vip", "image_resolution": "2k"},
    {"project_id": 1, "max_image_ops": 0, "max_video_ops": 0},
    {"project_id": 1, "max_image_ops": 0, "max_video_ops": 1,
     "video_generator": "veo_3_1_lite", "video_resolution": "720p"},
])
def test_estimate_unknown_fails_closed(p) -> None:
    est = registry.get("montage.apply_queue").estimate(p)
    assert not est.known and est.low is None and est.high is None


def test_improve_scene_images_and_llm_estimates() -> None:
    imp = registry.get("montage.improve_scene").estimate({
        "project_id": 1, "frame_id": 1, "prompt": "x", "max_image_ops": 6,
        "image_generator": "flux_2_pro", "image_resolution": "2k"})
    assert imp.known and imp.high > 3 * bm.LLM_SCENE_CALL_HIGH + 6 * 3 * 7
    unk = registry.get("montage.improve_scene").estimate({
        "project_id": 1, "frame_id": 1, "prompt": "x", "max_image_ops": 6,
        "image_generator": "gpt_image_2_vip", "image_resolution": "2k"})
    assert not unk.known
    gen = registry.get("montage.generate_scene_images").estimate({
        "project_id": 1, "frame_numbers": [1, 2, 3],
        "image_generator": "flux_2_pro", "image_resolution": "1k"})
    assert gen.known and gen.low == 15.0 and gen.high > 9 * 3 * 5
    for action_id in ("montage.generate_scene", "montage.suggest_scene_variants"):
        est = registry.get(action_id).estimate({})
        assert est.known and 0 < est.low < est.high
    assert bm.LLM_SCENE_CALL_HIGH == pytest.approx((20000 * 1.6 + 4000 * 8.4) / 1e6 / 0.005)


def test_scene_image_ops_port_of_panel_logic() -> None:
    frames = [
        {"number": 1, "shot_kind": "parent", "shot_plan": "ОБЩИЙ", "has_end_still": True,
         "characters": "c01"},
        {"number": 2, "shot_kind": "child", "shot_parent_number": 1, "shot_action": "идёт"},
        {"number": 3, "shot_kind": "child", "shot_parent_number": 1, "shot_leftover": True},
        {"number": 9, "shot_kind": "parent"},
    ]
    ops = bm.scene_image_ops(frames, [1, 2, 3], "общий заказ")
    assert [(o["frame_number"], o["shot"], o.get("slot")) for o in ops] == [
        (1, 1, "parent"), (1, 1, None), (1, 2, None), (2, 1, None)]
    assert ops[0]["instruction"] == "общий заказ"
    assert "Персонажи: c01" in ops[1]["instruction"]
    assert ops[2]["instruction"].endswith("Конечный кадр сцены.")
    assert all(o["type"] == "image_ai_change" for o in ops)
    assert len(ops) <= 3 * 3
