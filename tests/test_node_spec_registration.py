"""Proves a new node type can be registered through node_spec.register_node
without touching DecisionSupervisor, ActionSupervisor, or the FSM dispatch
in pipeline.py — only registry/capability wiring, per docs/ADDING_NEW_AGENT.md.

The running/ready statuses used here are plain sentinel strings, not real
ProjectStatus enum members: this test only exercises the registration and
lookup mechanics (node_registry.WORK_NODES, node_capabilities._CAPABILITIES),
not a real dispatchable pipeline node (which would additionally need a
ProjectStatus enum change and a pipeline.py::advance() branch, deliberately
out of scope for this unit test).
"""
from __future__ import annotations

import pytest

from app.orchestrator import node_registry
from app.orchestrator.node_spec import NodeSpec, register_node, verifier_for
from app.services import node_capabilities

TEST_NODE_ID = "test_only_probe_node"
TEST_STEP_CODE = "test_only_probe"
TEST_RUNNING = "test_only_probe_running"
TEST_READY = "test_only_probe_ready"


@pytest.fixture(autouse=True)
def _cleanup_registration():
    yield
    node_registry.WORK_NODES.pop(TEST_NODE_ID, None)
    node_registry.STEP_CODE_TO_NODE_TYPE.pop(TEST_STEP_CODE, None)
    node_registry.NODE_TYPE_TO_STEP_CODE.pop(TEST_NODE_ID, None)
    node_registry.RUNNING_TO_NODE_TYPE.pop(TEST_RUNNING, None)
    node_registry.READY_TO_NODE_TYPE.pop(TEST_READY, None)
    node_registry.NODE_TYPE_TO_RUNNING.pop(TEST_NODE_ID, None)
    node_registry.NODE_TYPE_TO_READY.pop(TEST_NODE_ID, None)
    if TEST_NODE_ID in node_registry.LINEAR_NODE_TYPES:
        node_registry.LINEAR_NODE_TYPES.remove(TEST_NODE_ID)
        node_registry.LINEAR_RUNNING_PIPELINE[:] = [
            row for row in node_registry.LINEAR_RUNNING_PIPELINE if row[1] != TEST_NODE_ID
        ]
    node_capabilities.unregister_capability_spec(TEST_NODE_ID)
    node_capabilities._CAPABILITIES.pop(TEST_NODE_ID, None)
    node_spec_module_verifiers = __import__(
        "app.orchestrator.node_spec", fromlist=["_VERIFIERS"]
    )._VERIFIERS
    node_spec_module_verifiers.pop(TEST_NODE_ID, None)


def test_register_node_wires_registry_and_capability_from_one_call():
    def _verifier(*args, **kwargs):
        return {"status": "passed"}

    spec = NodeSpec(
        node_id=TEST_NODE_ID,
        version="1",
        step_code=TEST_STEP_CODE,
        running_status=TEST_RUNNING,
        ready_status=TEST_READY,
        result_kind="structured",
        executor="test_only_probe.run → nothing",
        phases=("running", "commit"),
        effects=("project_database",),
        allowed_actions=node_capabilities.READ_ACTIONS,
        input_schema={"topic": "str"},
        output_schema={"result": "str"},
        permissions=("read_project",),
        managed=False,
        verifier=_verifier,
    )

    register_node(spec)

    assert node_registry.is_work_node_type(TEST_NODE_ID)
    assert node_registry.WORK_NODES[TEST_NODE_ID].step_code == TEST_STEP_CODE
    assert node_registry.RUNNING_TO_NODE_TYPE[TEST_RUNNING] == TEST_NODE_ID
    assert node_registry.READY_TO_NODE_TYPE[TEST_READY] == TEST_NODE_ID

    capability = node_capabilities.capability_for(TEST_NODE_ID)
    assert capability.node_type == TEST_NODE_ID
    assert capability.result_kind == "structured"
    assert capability.managed is False
    assert capability.actions == node_capabilities.READ_ACTIONS

    assert verifier_for(TEST_NODE_ID) is _verifier
    assert verifier_for("plan") is None  # pre-existing nodes are unmigrated, by design


def test_register_node_rejects_duplicate_node_id():
    spec = NodeSpec(
        node_id="plan",  # already registered by hand in node_registry.WORK_NODES
        version="1",
        step_code="dup",
        running_status=TEST_RUNNING,
        ready_status=TEST_READY,
        result_kind="structured",
        executor="dup.run",
        phases=("running",),
        effects=(),
        allowed_actions=node_capabilities.READ_ACTIONS,
    )
    with pytest.raises(ValueError, match="already registered"):
        register_node(spec)


def test_register_node_linear_flag_appends_to_pipeline_order():
    spec = NodeSpec(
        node_id=TEST_NODE_ID,
        version="1",
        step_code=TEST_STEP_CODE,
        running_status=TEST_RUNNING,
        ready_status=TEST_READY,
        result_kind="structured",
        executor="test_only_probe.run",
        phases=("running",),
        effects=(),
        allowed_actions=node_capabilities.READ_ACTIONS,
        linear=True,
    )
    register_node(spec)
    assert TEST_NODE_ID in node_registry.LINEAR_NODE_TYPES
    assert (TEST_RUNNING, TEST_NODE_ID) in node_registry.LINEAR_RUNNING_PIPELINE
