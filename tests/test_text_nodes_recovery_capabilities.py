import pytest

from app.services.node_capabilities import (
    READ_ACTIONS, capability_for, project_text_contract, project_text_parse_evidence)

RECOVERY_ACTIONS = {"retry_same", "retry_profile", "finish"}
MALFORMED = '{"ops": }'


@pytest.mark.parametrize("node_type", ["plan", "script"])
def test_plan_and_script_are_recovery_ready_by_capability_data(node_type):
    cap = capability_for(node_type)
    assert cap.managed and cap.result_kind == "project_text"
    assert RECOVERY_ACTIONS <= set(cap.actions) and set(READ_ACTIONS) <= set(cap.actions)
    assert cap.profile_compatible is True
    assert cap.native_json_retry is False
    assert cap.recovery_continuation == "none"
    assert "human_approval" in cap.phases and "hitl_request" in cap.effects


@pytest.mark.parametrize("node_type", ["split", "image_prompts"])
def test_split_and_image_prompts_are_recovery_ready_since_w5_d05(node_type):
    cap = capability_for(node_type)
    assert cap.managed
    assert set(cap.actions) == set(READ_ACTIONS) | RECOVERY_ACTIONS
    assert cap.profile_compatible is True and cap.native_json_retry is False
    assert cap.volatile_attachment_names is True and cap.recovery_continuation == "none"


def test_excel_gpt_behaviour_is_unchanged():
    cap = capability_for("excel_gpt")
    assert set(cap.actions) == set(READ_ACTIONS) | RECOVERY_ACTIONS
    assert cap.profile_compatible and cap.native_json_retry
    assert cap.recovery_continuation == "excel_gpt_chain"


def test_profile_eligibility_is_capability_data_not_result_kind():
    sfx = capability_for("sfx_plan")
    assert sfx.result_kind == "structured" and sfx.managed and sfx.profile_compatible
    assert "retry_profile" in sfx.actions
    compatible = {t for t in ("plan", "script", "split", "image_prompts", "excel_gpt", "images", "audio",
                              "videos", "assemble", "sfx_plan", "sfx_gen", "publish", "animation_prompts",
                              "sd_agent", "sd_assemble") if capability_for(t).profile_compatible}
    assert compatible == {"plan", "script", "split", "image_prompts", "excel_gpt", "sfx_plan", "sd_agent", "sd_assemble"}


def test_no_other_node_gained_recovery_actions():
    for node_type in ("publish", "scene_design", "enrich_1", "future-unsupported"):
        assert not RECOVERY_ACTIONS & set(capability_for(node_type).actions), node_type
    for node_type in ("images", "hero", "audio", "sfx_gen"):
        actions = set(capability_for(node_type).actions)
        assert "retry_same" in actions and "retry_profile" not in actions, node_type


def test_sd_agent_gets_retry_same_and_profile_per_type_with_agent_admission():
    from app.orchestrator.node_spec import capability_spec_for
    cap = capability_for("sd_agent")
    spec = capability_spec_for("sd_agent")
    assert cap.managed and "retry_same" in cap.actions and "retry_profile" in cap.actions
    assert cap.profile_compatible is True and cap.recovery_continuation == "none"
    assert not spec.recovery_only and not spec.local_recovery and spec.recovery_cap == 1


def test_assemble_gets_retry_same_only_as_a_local_rerun():
    from app.orchestrator.node_spec import capability_spec_for
    cap = capability_for("assemble")
    spec = capability_spec_for("assemble")
    assert cap.managed and spec.local_recovery and not spec.recovery_only
    assert "retry_same" in cap.actions and "retry_profile" not in cap.actions
    assert cap.profile_compatible is False and cap.recovery_continuation == "none"


def test_media_nodes_are_recovery_only():
    for node_type in ("videos", "items", "music"):
        from app.orchestrator.node_spec import capability_spec_for
        cap = capability_for(node_type)
        assert capability_spec_for(node_type).recovery_only is True, node_type
        assert cap.profile_compatible is False and "retry_profile" not in cap.actions, node_type


def test_sfx_plan_gets_retry_same_and_profile():
    cap = capability_for("sfx_plan")
    assert cap.managed and set(cap.actions) == set(READ_ACTIONS) | RECOVERY_ACTIONS
    assert cap.profile_compatible is True and cap.native_json_retry is False
    assert cap.recovery_continuation == "none" and cap.volatile_attachment_names is False


@pytest.mark.parametrize("node_type", ["animation_prompts"])
def test_animation_prompts_gets_retry_same_only(node_type):
    cap = capability_for(node_type)
    assert cap.managed
    assert set(cap.actions) == set(READ_ACTIONS) | {"retry_same", "finish"}
    assert cap.profile_compatible is False and cap.native_json_retry is False
    assert cap.recovery_continuation == "none"
    assert cap.volatile_attachment_names is (node_type == "animation_prompts")


@pytest.mark.parametrize("node_type", ["plan", "script"])
def test_injected_malformed_json_is_typed_invalid_json_payload_required(node_type):
    from app.services.diagnosis import derive_incidents
    proof = project_text_parse_evidence(MALFORMED, "", node_type)
    assert proof["syntax_valid"] is False and proof["parsed"] is False and proof["payload_present"] is False
    assert proof["payload_required"] is True
    scope = type("S", (), {"project_id": 2, "execution_id": "e", "node_keys": ["n"], "observer_state": "active",
                           "gaps": []})()
    base = dict(project_id=2, execution_id="e", node_key="n", attempt_id="a", db_scope="project", id=1)
    events = [
        {**base, "event_id": "s", "kind": "model_start", "request_id": "r1", "payload": {}, "id": 1},
        {**base, "event_id": "p", "kind": "parse_result", "request_id": "r1", "payload": proof, "id": 2},
        {**base, "event_id": "x", "kind": "node_error", "request_id": None, "payload": {}, "id": 3},
    ]
    incidents = derive_incidents(scope, events, {})
    assert "invalid_json" in {i["type"] for i in incidents}


def test_valid_script_voiceover_is_never_a_payload_incident():
    text = "Синтетический закадр. " * 20
    proof = project_text_parse_evidence("<<<VOICEOVER>>>" + text + "<<<END>>>", text, "script")
    assert proof["parsed"] is True and proof["schema_valid"] is True and proof["payload_required"] is False


def test_script_without_any_usable_payload_requires_one_but_short_text_does_not_change():
    empty = project_text_parse_evidence("plain refusal", "", "script")
    assert empty["payload_required"] is True and empty["payload_present"] is False
    short = project_text_parse_evidence("short", "short", "script")
    assert short["payload_required"] is False and short["schema_valid"] is False


def test_split_and_image_prompts_type_only_a_total_failure_as_recoverable_json_incident():
    import inspect
    from app.services import node_capabilities as nc
    assert "'payload_required':notframes_spec" in inspect.getsource(nc.record_split_parse).replace(" ", "")
    assert "'payload_required':False" in inspect.getsource(nc.record_image_prompt_parse).replace(" ", "")
    assert "'payload_required':True" in inspect.getsource(nc.record_image_prompt_unusable).replace(" ", "")


def test_contract_text_describes_bounded_hitl_preserving_recovery():
    for node_type in ("plan", "script"):
        contract = project_text_contract(node_type)
        assert contract["human_approval_preserved"] is True
        assert "Never approves the mandatory HITL" in contract["retry_policy"]
        assert "no supervisor recovery" not in contract["retry_policy"]


def test_prompt_variants_stay_the_registered_closed_set():
    from app.services.prompt_library import APPROVED_PROFILE_PROMPTS, apply_approved_profile_prompt
    assert set(APPROVED_PROFILE_PROMPTS) == {"original-contract-v1", "repair-json-only-v1", "require-apply-ops-v1"}
    with pytest.raises(ValueError):
        apply_approved_profile_prompt("original prompt", "model-written-free-edit")
    out = apply_approved_profile_prompt("original prompt", "repair-json-only-v1")
    assert out.startswith("original prompt") and "ORIGINAL node schema" in out


@pytest.mark.parametrize("shape", ["text", "messages_list"])
def test_effective_body_accepts_only_text_responses_shapes(shape):
    from app.services import decision_config, profiles_v1
    from unittest.mock import patch
    candidate = {"model_id": "gpt-5-6-sol", "provider": "kie", "kind": "text", "channel": "stable",
                 "reasoning_effort": "low", "max_output_tokens": 4096, "prompt_variant": "repair-json-only-v1",
                 "synthetic_only": False}
    with patch.object(profiles_v1, "validate_candidate", lambda c: None):
        body = {"model": "gpt-5-6-sol", "stream": True,
                "input": "original prompt" if shape == "text" else [{"role": "user", "content": "original prompt"}]}
        out = profiles_v1.effective_body(body, candidate)
        assert out["model"] == "gpt-5-6-sol" and out["max_output_tokens"] == 4096
        text = out["input"] if shape == "text" else out["input"][0]["content"]
        assert text.startswith("original prompt") and "repair-json-only-v1" in text
        for bad in ({"model": "m", "messages": [{"role": "user", "content": "x"}]},
                    {"model": "m", "input": [{"role": "user", "content": [{"type": "input_image"}]}]},
                    {"model": "m", "input": {"unexpected": 1}}):
            with pytest.raises(ValueError):
                profiles_v1.effective_body(bad, candidate)
    assert decision_config
