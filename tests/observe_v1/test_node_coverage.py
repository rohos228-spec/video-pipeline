"""Node coverage: every registered node type is listed honestly (the "add a node" guard), each
level is backed by evidence in the registries, each missing recovery carries a coded reason, and
the panel endpoint serves exactly that data. No database for the data tests, no provider."""
import asyncio
import importlib
import json

import httpx
import pytest
from fastapi import FastAPI

import app.services.decision_v1  # noqa: F401  (registers the named contract builders)
from app.orchestrator import node_registry, node_spec
from app.orchestrator.node_spec import (
    COVERAGE_FIELDS, COVERAGE_LEVELS, KNOWN_FORBIDDEN_CODES, CapabilitySpec, capability_from_manifest,
    coverage_evidence, coverage_level, registered_capability_specs,
)
from app.services import node_capabilities, node_coverage
from app.services.node_capabilities import MEDIA_RECOVERY_DECISIONS, READ_ACTIONS, capability_for

RETRY = {"retry_same", "retry_profile"}
SPECS = registered_capability_specs()
REGISTRY_TYPES = node_coverage.registry_node_types()

# Changing this set is a conscious decision: these are the only node types with automatic recovery.
RECOVERING = {"plan", "script", "excel_gpt", "sfx_plan", "animation_prompts", "videos", "items", "music"}
# Managed for supervised RECOVERY only: a resume of a known provider task, never a start or a paid re-submit.
RECOVERY_ONLY = {"videos", "items", "music"}
EXPECTED_LEVELS = {
    "managed": {"plan", "script", "split", "image_prompts", "excel_gpt", "images", "sfx_plan",
                "animation_prompts", "videos", "items", "music"},
    "observed": {"sfx_gen", "scene_design", "sd_agent", "sd_assemble", "hero",
                 "audio", "assemble",
                 "enrich_1", "enrich_2", "enrich_3", "enrich_4", "enrich_5"},
    "human_only": {"publish"},  # publish stays human-only forever (irreversible external effect)
}


# ---- the "add a node" test ------------------------------------------------------------

def test_every_registered_node_type_has_a_capability_spec():
    """Fails when a node type is added to the pipeline registry without a CapabilitySpec."""
    assert node_coverage.uncovered_node_types() == []
    assert set(REGISTRY_TYPES) <= set(SPECS)


def test_the_guard_fires_for_a_node_type_added_without_a_spec():
    probe = "coverage_probe_node"
    node_registry.WORK_NODES[probe] = node_registry.WorkNodeSpec(
        probe, probe, next(iter(node_registry.WORK_NODES.values())).running_status,
        next(iter(node_registry.WORK_NODES.values())).ready_status)
    try:
        assert probe in node_coverage.uncovered_node_types()
        assert probe not in {row["node_type"] for row in node_coverage.node_coverage()["nodes"]}
        assert node_coverage.node_coverage()["uncovered"] == [probe]
    finally:
        del node_registry.WORK_NODES[probe]
    assert node_coverage.uncovered_node_types() == []


def test_registry_lists_all_23_work_nodes_and_the_virtual_excel_gpt():
    assert len(node_registry.WORK_NODES) == 23
    assert set(REGISTRY_TYPES) == set(node_registry.WORK_NODES) | {"excel_gpt"}
    assert len(REGISTRY_TYPES) == len(set(REGISTRY_TYPES))


# ---- levels are derived from registry evidence, and pinned --------------------------------

def test_levels_are_exactly_the_expected_partition():
    actual = {level: {t for t in REGISTRY_TYPES if coverage_level(SPECS[t]) == level} for level in COVERAGE_LEVELS}
    assert actual == EXPECTED_LEVELS
    assert set().union(*actual.values()) == set(REGISTRY_TYPES)


@pytest.mark.parametrize("node_type", REGISTRY_TYPES)
def test_level_matches_its_evidence(node_type):
    spec = SPECS[node_type]
    level = coverage_level(spec)
    assert level in COVERAGE_LEVELS
    evidence = coverage_evidence(spec)
    if level == "managed":
        assert spec.effective_managed and spec.acceptance
    elif level == "observed":
        assert not spec.effective_managed and evidence, "observed needs a result check or a description"
    else:
        assert not spec.effective_managed and not evidence
        assert not spec.recovery_actions
    for name in evidence:
        assert name.split(":", 1)[0] in {"verifier", "hook", "descriptive_contract"}


@pytest.mark.parametrize("node_type", [t for t in REGISTRY_TYPES if SPECS[t].observation_hook])
def test_observation_hooks_resolve_to_real_callables(node_type):
    module, _, attr = SPECS[node_type].observation_hook.partition(":")
    assert callable(getattr(importlib.import_module(module), attr))


# ---- every missing recovery has a coded reason; every recovery is bounded -------------------

@pytest.mark.parametrize("node_type", REGISTRY_TYPES)
def test_no_recovery_means_a_coded_reason_and_recovery_means_a_cap(node_type):
    spec = SPECS[node_type]
    if spec.recovery_actions:
        assert spec.forbidden_code is None and spec.forbidden_reason is None
        assert spec.recovery_cap == 1, "the supervisor recovers a node once per execution"
        assert spec.limits, "a recovering node must state what stays forbidden"
        assert spec.verifies, "a recovering node must state what is verified"
    else:
        assert spec.forbidden_code in KNOWN_FORBIDDEN_CODES, node_type
        assert spec.recovery_cap == 0
        # a real sentence, not a stub
        assert len(spec.forbidden_reason.split()) >= 10, node_type
        assert not RETRY & set(spec.actions)


def test_only_the_expected_nodes_recover():
    assert {t for t in REGISTRY_TYPES if SPECS[t].recovery_actions} == RECOVERING
    for node_type in RECOVERING:
        assert SPECS[node_type].effective_managed


def test_every_media_decision_node_is_resume_only_or_has_a_coded_human_only_reason():
    for node_type, decision in MEDIA_RECOVERY_DECISIONS.items():
        spec = SPECS[node_type]
        if node_type in RECOVERY_ONLY:
            assert decision["decision"] == "resume_only", node_type
            assert spec.recovery_only and spec.effective_managed and spec.forbidden_code is None, node_type
            assert spec.recovery_actions == ("retry_same",), node_type
        else:
            assert decision["decision"] == "human_only", node_type
            assert spec.forbidden_code and not spec.recovery_actions and not spec.recovery_only, node_type


def test_recovery_only_nodes_can_never_be_started_by_the_supervisor():
    """Managed for recovery only: a managed launch or a managed worker attempt of the node is refused."""
    for node_type in RECOVERY_ONLY:
        with pytest.raises(ValueError, match="recovery only"):
            node_capabilities.require_managed_capability(node_type)
    for node_type in RECOVERING - RECOVERY_ONLY:
        assert node_capabilities.require_managed_capability(node_type).managed


def test_only_recovery_only_nodes_carry_the_flag():
    assert {t for t, s in SPECS.items() if s.recovery_only} == RECOVERY_ONLY
    assert not COVERAGE_FIELDS - {"verifies", "limits", "observation_hook", "forbidden_code",
                                  "forbidden_reason", "recovery_cap", "recovery_only"}


def test_coverage_only_entries_change_no_behaviour():
    """A coverage-only entry is the same capability payload as an unregistered type."""
    for node_type in ("scene_design", "sd_agent", "sd_assemble", "hero", "enrich_3"):
        cap = capability_for(node_type)
        unknown = capability_for("future-unsupported")
        assert (cap.result_kind, cap.executor, cap.phases, cap.effects, cap.actions, cap.managed) == (
            unknown.result_kind, unknown.executor, unknown.phases, unknown.effects, unknown.actions, unknown.managed)
        assert cap.node_type == node_type


def test_forbidden_code_table_has_no_unused_or_unexplained_codes():
    used = {s.forbidden_code for s in SPECS.values() if s.forbidden_code}
    assert used <= set(KNOWN_FORBIDDEN_CODES)
    assert set(KNOWN_FORBIDDEN_CODES) <= used, "remove codes nothing uses"
    assert all(len(text.split()) >= 5 for text in KNOWN_FORBIDDEN_CODES.values())


# ---- coverage data stays out of the recovery revision and the capability payload -----------

def test_coverage_notes_do_not_enter_the_recovery_revision():
    from app.services import revisions
    cap = capability_for("sfx_plan")
    data = revisions.capability_data("sfx_plan", cap)
    assert not COVERAGE_FIELDS & set(data["spec"])
    original = SPECS["sfx_plan"]
    from dataclasses import replace
    try:
        node_spec._CAPABILITY_SPECS["sfx_plan"] = replace(original, limits=("edited note",), verifies=())
        assert revisions.capability_data("sfx_plan", cap) == data
    finally:
        node_spec._CAPABILITY_SPECS["sfx_plan"] = original


def test_capability_payload_has_no_coverage_fields():
    keys = set(capability_for("sfx_plan").contract())
    assert not (COVERAGE_FIELDS | {"forbidden", "level"}) & keys


# ---- manifest accepts and validates the coverage block ------------------------------------

def _manifest(**block):
    return {"nodeapi": 1, "type": "manifest_text",
            "orchestrator": {"result_kind": "structured", "executor": "text_job.run", **block}}


def test_manifest_carries_coverage_data():
    spec = capability_from_manifest(_manifest(
        verifies=["Результат перечитывается"], limits=["Один повтор"], forbidden_code="legacy_node_type",
        forbidden_reason="Устаревший тип узла: переносим граф на новый тип прежде чем включать", recovery_cap=1,
        observation_hook="app.services.observation:observe_attempt"))
    assert spec.forbidden_code == "legacy_node_type" and spec.recovery_cap == 1
    assert spec.verifies == ("Результат перечитывается",)


@pytest.mark.parametrize("bad", [
    {"forbidden_code": "because_i_said_so"},
    {"recovery_cap": 2},
    {"recovery_cap": True},
    {"verifies": "not a list"},
])
def test_manifest_rejects_bad_coverage_data(bad):
    with pytest.raises(ValueError):
        capability_from_manifest(_manifest(**bad))


# ---- the panel data and its endpoint ------------------------------------------------------

def test_panel_data_is_complete_serializable_and_honest():
    data = node_coverage.node_coverage()
    json.dumps(data, ensure_ascii=False)
    rows = {row["node_type"]: row for row in data["nodes"]}
    assert set(rows) == set(REGISTRY_TYPES) and data["uncovered"] == []
    assert data["summary"]["total"] == len(REGISTRY_TYPES) == 24
    assert data["summary"]["with_automatic_recovery"] == len(RECOVERING)
    assert sum(data["summary"][level] for level in COVERAGE_LEVELS) == 24
    assert data["automatic_actions"] is False and data["semantic_quality_verified"] is False
    for node_type, row in rows.items():
        assert row["level"] in COVERAGE_LEVELS and row["level_label"] and row["title"]
        if node_type in RECOVERING:
            assert row["forbidden"] is None and set(row["recovery"]["actions"]) <= RETRY
            assert row["recovery"]["cap_per_node"] == 1
        else:
            assert row["recovery"]["actions"] == []
            assert row["forbidden"]["code"] in KNOWN_FORBIDDEN_CODES
            assert row["forbidden"]["reason"] and row["forbidden"]["meaning"]
    assert rows["sfx_plan"]["recovery"]["actions"] == ["retry_same"]
    for node_type in RECOVERING:
        assert rows[node_type]["recovery"]["resume_only"] is (node_type in RECOVERY_ONLY), node_type
    assert rows["videos"]["level"] == "managed" and rows["videos"]["recovery"]["profile_compatible"] is False
    assert rows["hero"]["forbidden"]["code"] == "nondeterministic_paid_request"
    assert rows["assemble"]["forbidden"]["code"] == "media_recovery_not_admitted"
    assert rows["sfx_plan"]["recovery"]["profile_compatible"] is False
    assert rows["plan"]["recovery"]["profile_compatible"] is True
    assert rows["publish"]["level"] == "human_only"


def test_endpoint_serves_the_same_data():
    from app.web.routers import observations as api

    async def call():
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
            return await client.get("/api/projects/7/observations/node-coverage")

    response = asyncio.run(call())
    assert response.status_code == 200
    body = response.json()
    assert {row["node_type"] for row in body["nodes"]} == set(REGISTRY_TYPES)
    assert body["uncovered"] == [] and body["summary"]["total"] == 24
    assert next(r for r in body["nodes"] if r["node_type"] == "hero")["forbidden"]["code"] == "nondeterministic_paid_request"


def test_new_specs_are_visible_in_capability_registry_and_verifiers_resolve():
    for name in ("sfx_plan_result", "sfx_result", "animation_result"):
        assert callable(node_spec.registered_verifiers()[name].resolve())
    assert node_spec.verifier_spec_for("animation_prompts").name == "animation_result"
    assert node_spec.verifier_spec_for("sfx_plan").name == "sfx_plan_result"
    assert node_spec.verifier_spec_for("sfx_gen").name == "sfx_result"
    assert node_spec.contract_name_for(capability_for("sfx_plan")) == "sfx_plan"
    assert node_spec.contract_name_for(capability_for("animation_prompts")) == "animation_prompts"
    assert node_capabilities.capability_for("sfx_plan").actions == READ_ACTIONS + ("retry_same", "finish")


# ---- scene nodes and animation degradation: what is checked, what stays forbidden -------------

SCENE_NODES = {"scene_design": "legacy_node_type", "sd_agent": "partial_commit",
               "sd_assemble": "pre_request_commit"}


@pytest.mark.parametrize("node_type", sorted(SCENE_NODES))
def test_scene_nodes_are_observed_through_the_result_hook_and_recovery_stays_forbidden(node_type):
    spec = SPECS[node_type]
    assert coverage_level(spec) == "observed" and "hook:" + spec.observation_hook in coverage_evidence(spec)
    assert spec.observation_hook == "app.services.scene_contracts:observe_scene_result"
    assert spec.forbidden_code == SCENE_NODES[node_type] and not spec.recovery_actions
    assert any("invalid_json" in line and "contract_rejected" in line for line in spec.verifies), node_type


def test_scene_contracts_describe_every_agent_marker_and_never_offer_a_retry():
    from app.services.scene_contracts import SLICE_AGENTS, scene_contract
    for agent in SLICE_AGENTS:
        contract = scene_contract({"id": "n", "type": "sd_agent", "data": {"sd_agent": agent}})
        assert contract["supported"] and contract["agent"] == agent, agent
        assert set(contract["actions"]) == {"read_events", "read_contract", "wait", "human_review"}
    legacy = scene_contract({"id": "n", "type": "scene_design", "data": {}})
    assert legacy["supported"] and legacy["agent"] == "legacy" and "retry_same" not in legacy["actions"]


def test_animation_prompts_names_its_degradation_fact_and_it_is_not_a_recovery_incident():
    from app.services.decision_v1 import RECOVERY_INCIDENTS
    from app.services.diagnosis import TITLES
    from app.services.native_animation_contracts import animation_prompts_contract
    assert any("animation_degraded" in line for line in SPECS["animation_prompts"].limits)
    assert "animation_degraded" in TITLES and "animation_degraded" not in RECOVERY_INCIDENTS
    assert animation_prompts_contract()["degradation_facts"]["fact_kind"] == "animation_degraded"


# ---- local media nodes (assemble, audio, sfx_gen): decided by a dry run, kept observed ----------

LOCAL_MEDIA = {"assemble": "media_recovery_not_admitted", "audio": "paid_no_idempotency",
               "sfx_gen": "paid_no_idempotency"}


@pytest.mark.parametrize("node_type", sorted(LOCAL_MEDIA))
def test_local_media_nodes_stay_observed_with_an_honest_code_and_no_recovery_action(node_type):
    spec = SPECS[node_type]
    assert coverage_level(spec) == "observed" and spec.forbidden_code == LOCAL_MEDIA[node_type]
    assert not spec.recovery_actions and not spec.recovery_only and node_type not in RECOVERING
    assert not capability_for(node_type).managed
    assert MEDIA_RECOVERY_DECISIONS[node_type]["decision"] == "human_only"


def test_assemble_reason_names_what_the_dry_run_proved_and_what_is_missing():
    from pathlib import Path
    spec = SPECS["assemble"]
    reason = spec.forbidden_reason
    for proven in ("сухим прогоном", "атомарная запись", "один повтор", "недостижимость платного"):
        assert proven in reason.lower(), proven
    for missing in ("проверка", "модельного запроса", "медиа-инцидент", "ревизии входа"):
        assert missing in reason.lower(), missing
    assert any("test_media_dry_run_local_nodes.py" in line for line in spec.limits)
    assert (Path(__file__).parent / "test_media_dry_run_local_nodes.py").is_file()
    assert "unknown" in MEDIA_RECOVERY_DECISIONS["assemble"]["reason"]


def test_audio_and_sfx_gen_say_that_a_finished_file_is_not_resumed_by_the_supervisor():
    assert any("resume_local" in line and "надзор" in line for line in SPECS["audio"].limits)
    assert any("resume_local" in line for line in SPECS["sfx_gen"].limits)
