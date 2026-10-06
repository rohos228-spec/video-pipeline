"""Declarative node knowledge: snapshot equality, registry conformance over every
registered node spec, onboarding a node by data only, and the manifest loader."""
import asyncio
import json
import unittest
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import test_decision_v1 as decision_tests
import app.services.decision_v1  # noqa: F401  (registers the named contract builders)
from app.models import ProjectStatus
from app.orchestrator import node_registry, node_spec
from app.orchestrator.node_spec import (
    CapabilitySpec, VerifierSpec, capability_from_manifest, registered_capability_specs,
)
from app.services import node_capabilities
from app.services.node_capabilities import READ_ACTIONS, capability_for

SNAPSHOT = Path(__file__).resolve().parents[1] / "fixtures" / "node_capabilities_snapshot.json"
MEDIA_KINDS = {"image", "video", "audio", "assembly", "publication"}
RETRY = {"retry_same", "retry_profile"}
KNOWN_ACTIONS = set(READ_ACTIONS) | RETRY | {"finish"}
ALL_SPECS = sorted(registered_capability_specs())


def _jsonable(value):
    return json.loads(json.dumps(value, ensure_ascii=False))


# --- snapshot -----------------------------------------------------------

def test_capabilities_equal_pre_refactor_snapshot():
    expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    for node_type, frozen in expected.items():
        cap = capability_for(node_type)
        assert _jsonable(asdict(cap)) == frozen["asdict"], node_type
        assert _jsonable(cap.contract()) == frozen["contract"], node_type
    registered = set(registered_capability_specs())
    assert registered <= set(expected), "a node was added without a snapshot entry"


ORIGINAL_TWELVE = {"plan", "script", "split", "image_prompts", "excel_gpt", "images", "videos",
                   "audio", "assemble", "sfx_plan", "sfx_gen", "publish"}


def test_twelve_original_nodes_are_registered():
    assert ORIGINAL_TWELVE <= set(ALL_SPECS)
    assert ORIGINAL_TWELVE <= set(json.loads(SNAPSHOT.read_text(encoding="utf-8")))


# --- conformance over all registered specs ------------------------------

@pytest.mark.parametrize("node_type", ALL_SPECS)
def test_spec_conformance(node_type):
    spec = registered_capability_specs()[node_type]
    assert spec.node_type == node_type
    # A coverage-only entry (result_kind 'unsupported') deliberately carries no executor: it
    # is the same payload as an unregistered type plus the reason in coverage data.
    if spec.result_kind != "unsupported":
        assert spec.executor
    assert spec.phases and isinstance(spec.effects, tuple)
    actions = set(spec.actions)
    assert actions <= KNOWN_ACTIONS, actions - KNOWN_ACTIONS
    assert {"read_events", "read_contract", "human_review"} <= actions
    # managed requires a named acceptance artifact; the built capability agrees.
    if spec.managed:
        assert spec.acceptance, "managed without acceptance"
    assert capability_for(node_type).managed == spec.effective_managed
    assert node_registry.is_work_node_type(node_type)  # excel_gpt is virtual, not in WORK_NODES
    # retry requires a managed, text-like node; a profile retry additionally needs profile rules.
    if actions & RETRY:
        assert spec.effective_managed
        assert spec.result_kind not in MEDIA_KINDS
        assert spec.recovery_continuation in node_spec._KNOWN_RECOVERY_CONTINUATIONS
    if "retry_profile" in actions:
        assert spec.profile_compatible, "profile retry without profile_compatible"
    if spec.profile_compatible:
        assert "retry_profile" in actions
    # media nodes never expose retry and are not profile compatible.
    if spec.result_kind in MEDIA_KINDS:
        assert not actions & RETRY
        assert not spec.profile_compatible
    # names resolve.
    if spec.verifier is not None:
        assert spec.verifier in node_spec.registered_verifiers(), spec.verifier
    if spec.contract is not None:
        assert spec.contract in node_spec.registered_contract_builders(), spec.contract
    assert node_spec.contract_name_for(capability_for(node_type)) in node_spec.registered_contract_builders()
    if spec.successor is not None:
        assert spec.successor in node_spec.KNOWN_SUCCESSORS


def test_every_verifier_and_binding_is_consistent():
    for name, verifier in node_spec.registered_verifiers().items():
        assert verifier.name == name
        assert callable(verifier.resolve())
        assert verifier.success_errors in {"gap", "raise"}
    for node_type, name in node_spec._VERIFIER_BINDINGS.items():
        assert name in node_spec.registered_verifiers(), (node_type, name)


def test_unknown_node_type_is_unsupported_and_read_only():
    cap = capability_for("not_a_registered_node")
    assert cap.result_kind == "unsupported" and not cap.managed
    assert not set(cap.actions) & (RETRY | {"finish"})


# --- manifest loader ----------------------------------------------------

def _manifest(**block):
    return {"nodeapi": 1, "type": "manifest_text",
            "orchestrator": {"result_kind": "structured", "executor": "text_job.run", **block}}


def test_manifest_roundtrip_and_fail_closed_managed():
    spec = capability_from_manifest(_manifest(managed=True, phases=["running"], effects=["project_database"]))
    assert spec.managed is False and spec.node_type == "manifest_text"
    assert spec.actions == READ_ACTIONS and spec.recovery_continuation == "none"
    accepted = capability_from_manifest(_manifest(managed=True, acceptance="synthetic_text_scenario"))
    assert accepted.managed and accepted.effective_managed


@pytest.mark.parametrize("bad", [
    {"nodeapi": 2},
    {"type": "Bad-Name"},
    {"orchestrator": None},
    {"orchestrator": {"result_kind": "structured"}},
    {"orchestrator": {"result_kind": "structured", "executor": "x", "surprise": 1}},
    {"orchestrator": {"result_kind": "structured", "executor": "x", "actions": ["shell"]}},
    {"orchestrator": {"result_kind": "structured", "executor": "x", "managed": "yes"}},
    {"orchestrator": {"result_kind": "structured", "executor": "x", "recovery_continuation": "teleport"}},
    {"orchestrator": {"result_kind": "structured", "executor": "x", "phases": "running"}},
])
def test_manifest_rejects_invalid(bad):
    manifest = {**_manifest(), **bad}
    with pytest.raises(ValueError):
        capability_from_manifest(manifest)
    with pytest.raises(ValueError):
        capability_from_manifest("not a mapping")


# --- add a node by data -------------------------------------------------

SYNTH = "synthetic_text"


def _register_synthetic(**overrides):
    fields = dict(
        node_id=SYNTH, version="1", step_code=SYNTH,
        running_status=ProjectStatus.planning, ready_status=ProjectStatus.plan_ready,
        result_kind="synthetic_kind", executor="synthetic.run",
        phases=("running", "parse", "commit"), effects=("project_database",),
        allowed_actions=READ_ACTIONS + ("retry_same", "retry_profile", "finish"),
        managed=True, acceptance="synthetic_text_scenario", profile_compatible=True,
        native_json_retry=False, recovery_continuation="none",
        verifier_name="synthetic_result", contract_name="synthetic_contract")
    fields.update(overrides)
    node_spec.register_node(node_spec.NodeSpec(**fields))


def _unregister_synthetic():
    for table in (node_registry.WORK_NODES, node_registry.NODE_TYPE_TO_STEP_CODE,
                  node_registry.NODE_TYPE_TO_RUNNING, node_registry.NODE_TYPE_TO_READY):
        table.pop(SYNTH, None)
    node_registry.STEP_CODE_TO_NODE_TYPE.pop(SYNTH, None)
    node_capabilities.unregister_capability_spec(SYNTH)
    node_spec._VERIFIER_SPECS.pop("synthetic_result", None)
    node_spec._CONTRACT_BUILDERS.pop("synthetic_contract", None)
    node_spec._VERIFIERS.pop(SYNTH, None)


class AddNodeByData(unittest.TestCase):
    def setUp(self):
        self.addCleanup(_unregister_synthetic)
        self.calls = []

        async def observe(session, project, node_type):
            self.calls.append(node_type)

        async def contract(ctx):
            return {"synthetic": True, "node_type": ctx.capability.node_type}

        node_spec.register_verifier(VerifierSpec("synthetic_result", observe, managed_only=True,
                                                 success_errors="raise", on_error=True))
        node_spec.register_contract_builder("synthetic_contract", contract)
        _register_synthetic()

    def test_capability_for_reflects_the_data(self):
        cap = capability_for(SYNTH)
        assert cap.result_kind == "synthetic_kind" and cap.managed
        assert cap.profile_compatible and cap.recovery_continuation == "none"
        assert "retry_same" in cap.actions
        assert node_spec.contract_name_for(cap) == "synthetic_contract"
        assert node_spec.verifier_spec_for(SYNTH).name == "synthetic_result"

    def test_conformance_rules_hold_for_the_new_node(self):
        test_spec_conformance(SYNTH)
        assert SYNTH in registered_capability_specs()

    def test_managed_without_acceptance_is_forced_off(self):
        _unregister_synthetic()
        node_spec.register_verifier(VerifierSpec("synthetic_result", lambda *a: None))
        node_spec.register_contract_builder("synthetic_contract", lambda c: None)
        _register_synthetic(acceptance=None)
        assert capability_for(SYNTH).managed is False

    def test_verifier_hook_runs_without_core_changes(self):
        ctx = SimpleNamespace(scope=SimpleNamespace(mode="managed", execution_id="e"), observer=MagicMock())
        asyncio.run(node_spec.run_verifier_after_success(ctx, None, None, SYNTH))
        asyncio.run(node_spec.run_verifier_after_error(ctx, None, None, SYNTH, ValueError("x")))
        assert self.calls == [SYNTH, SYNTH]
        ordinary = SimpleNamespace(scope=SimpleNamespace(mode="ordinary", execution_id="e"), observer=MagicMock())
        asyncio.run(node_spec.run_verifier_after_success(ordinary, None, None, SYNTH))
        assert self.calls == [SYNTH, SYNTH]  # managed_only: untouched outside managed scope

    def test_unknown_contract_name_fails_closed(self):
        cap = replace(capability_for(SYNTH))
        node_spec._CAPABILITY_SPECS[SYNTH] = replace(node_spec._CAPABILITY_SPECS[SYNTH], contract="nope")
        with pytest.raises(ValueError):
            node_spec.contract_builder_for(cap)



class SyntheticNodeDecision(decision_tests.DecisionDurability):
    """The Decision layer reads the new node's contract and allow-list from data."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.addCleanup(_unregister_synthetic)

        async def contract(ctx):
            return node_spec_panel({"synthetic": True, "node_key": ctx.row.node_key,
                                    "result_kind": ctx.capability.result_kind})

        node_spec.register_contract_builder("synthetic_contract", contract)
        node_spec.register_verifier(VerifierSpec("synthetic_result", lambda *a: None))
        _register_synthetic()

    async def test_synthetic_node_read_contract_and_allow_list(self):
        record = await self.native_contract_fixture(SYNTH)
        before = await self.loop.context(record["decision_id"])
        contract = await self.loop.read_contract(record["decision_id"])
        self.assertEqual(contract, {"synthetic": True, "node_key": "n1", "result_kind": "synthetic_kind"})
        self.assertEqual(before["node_capability"]["result_kind"], "synthetic_kind")
        for action in ("read_events", "read_contract", "retry_same", "finish"):
            self.assertIn(action, before["allowed_actions"])

    async def test_synthetic_read_only_node_gets_no_retry(self):
        _unregister_synthetic()
        node_spec.register_contract_builder("synthetic_contract", lambda c: None)
        node_spec.register_verifier(VerifierSpec("synthetic_result", lambda *a: None))
        _register_synthetic(allowed_actions=READ_ACTIONS, managed=False, profile_compatible=False,
                            acceptance=None)
        record = await self.native_contract_fixture(SYNTH)
        before = await self.loop.context(record["decision_id"])
        self.assertNotIn("retry_same", before["allowed_actions"])
        self.assertNotIn("finish", before["allowed_actions"])


def node_spec_panel(value):
    return value


for _name in dir(decision_tests.DecisionDurability):
    if _name.startswith("test_") and _name not in {
            "test_synthetic_node_read_contract_and_allow_list",
            "test_synthetic_read_only_node_gets_no_retry"}:
        setattr(SyntheticNodeDecision, _name, None)
