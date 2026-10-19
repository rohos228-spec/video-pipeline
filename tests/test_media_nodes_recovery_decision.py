import pytest

from app.orchestrator.node_registry import WORK_NODES
from app.services.node_capabilities import (
    MEDIA_RECOVERY_DECISIONS, READ_ACTIONS, capability_for, media_recovery_decision)

RECOVERY_ACTIONS = {"retry_same", "retry_profile", "finish"}

KNOWN_NON_MEDIA = {
    "plan", "script", "split", "scene_design", "sd_agent", "sd_assemble",
    "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5",
    "image_prompts", "animation_prompts", "sfx_plan",
}
MEDIA = set(MEDIA_RECOVERY_DECISIONS)
RESUME_ONLY = {"videos", "items", "music"}
LOCAL_RETRY = {"assemble"}
LOCAL_RESUME = {"hero", "images", "audio", "sfx_gen"}
HUMAN_ONLY = MEDIA - RESUME_ONLY - LOCAL_RETRY - LOCAL_RESUME


def test_every_registry_node_has_a_media_decision_or_is_known_non_media():
    unclassified = set(WORK_NODES) - MEDIA - KNOWN_NON_MEDIA
    assert not unclassified, f"new node type needs a capability/media decision: {sorted(unclassified)}"
    assert MEDIA <= set(WORK_NODES), "decision table names a node not in the registry"
    assert not MEDIA & KNOWN_NON_MEDIA


def test_generating_status_nodes_are_never_silently_non_media():
    for node_type, spec in WORK_NODES.items():
        if spec.running_status.name.startswith("generating_") and node_type not in KNOWN_NON_MEDIA:
            assert node_type in MEDIA, node_type


@pytest.mark.parametrize("node_type", sorted(HUMAN_ONLY))
def test_media_node_is_human_only_with_honest_reason(node_type):
    cap = capability_for(node_type)
    assert not RECOVERY_ACTIONS & set(cap.actions)
    assert set(cap.actions) <= set(READ_ACTIONS) | {"read_events", "read_contract", "human_review"}
    assert cap.profile_compatible is False
    decision = media_recovery_decision(node_type)
    assert decision["decision"] == "human_only"
    assert len(decision["reason"].split()) >= 8, "reason must be a real sentence, not a stub"
    for key in ("cost_class", "provider"):
        assert decision[key].strip()
    assert set(decision) == {"decision", "cost_class", "provider", "reason"}


@pytest.mark.parametrize("node_type", sorted(LOCAL_RESUME))
def test_local_resume_media_node_offers_one_retry_slot_for_a_recorded_result_only(node_type):
    cap = capability_for(node_type)
    assert cap.managed and "retry_same" in cap.actions and "retry_profile" not in cap.actions
    assert cap.profile_compatible is False and cap.recovery_continuation == "none"
    decision = media_recovery_decision(node_type)
    assert decision["decision"] == "resume_local"
    assert len(decision["reason"].split()) >= 8
    assert "resume_local" in decision["reason"] and "zero new paid requests" in decision["reason"]
    assert set(decision) == {"decision", "cost_class", "provider", "reason"}


@pytest.mark.parametrize("node_type", sorted(RESUME_ONLY))
def test_resume_only_media_node_offers_one_retry_slot_and_no_profile_change(node_type):
    cap = capability_for(node_type)
    assert cap.managed and "retry_same" in cap.actions and "retry_profile" not in cap.actions
    assert cap.profile_compatible is False and cap.recovery_continuation == "none"
    decision = media_recovery_decision(node_type)
    assert decision["decision"] == "resume_only"
    assert len(decision["reason"].split()) >= 8
    assert "zero new paid requests" in decision["reason"]
    assert set(decision) == {"decision", "cost_class", "provider", "reason"}


@pytest.mark.parametrize("node_type", sorted(LOCAL_RETRY))
def test_local_retry_node_offers_one_retry_slot_and_no_provider_call(node_type):
    cap = capability_for(node_type)
    assert cap.managed and "retry_same" in cap.actions and "retry_profile" not in cap.actions
    assert cap.profile_compatible is False and cap.recovery_continuation == "none"
    decision = media_recovery_decision(node_type)
    assert decision["decision"] == "local_retry" and decision["cost_class"].startswith("no_provider_spend")
    assert "resume-only scope" in decision["reason"] and "approve_final" in decision["reason"]
    assert set(decision) == {"decision", "cost_class", "provider", "reason"}


def test_paid_media_reasons_state_the_double_pay_risk_or_missing_route():
    risk_words = ("double", "charged", "re-pay", "re-submit", "never resubmitted", "twice",
                  "irreversible", "no recovery verifier", "unknown", "paid re-send", "paid re-submit")
    for node_type in MEDIA - LOCAL_RETRY:
        reason = MEDIA_RECOVERY_DECISIONS[node_type]["reason"].lower()
        assert any(w in reason for w in risk_words), node_type


def test_capability_contract_shape_is_unchanged_by_decision_data():
    keys = set(capability_for("images").contract())
    assert "reason" not in keys and "decision" not in keys
    assert "decision" not in {f for f in capability_for("videos").__dataclass_fields__}


def test_unregistered_type_has_no_media_decision():
    assert media_recovery_decision("future-unsupported") is None
    assert media_recovery_decision("plan") is None
