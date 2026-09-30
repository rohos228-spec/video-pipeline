"""One-call node registration, wrapping the existing WORK_NODES /
_CAPABILITIES dicts — not a new workflow engine or discovery mechanism.

See docs/ADDING_NEW_AGENT.md for the full manual procedure this wraps, and
docs/ARCHITECTURE.md ("Registry") for why this exists. Registering a node
still requires: a ProjectStatus running/ready pair in app/models.py, a
steps/<name>.py executor, and a dispatch branch in pipeline.py::advance().
This module only removes the need to hand-edit node_registry.WORK_NODES and
node_capabilities._CAPABILITIES separately, and keeps them from drifting out
of sync (the gap fixed for sfx_plan/sfx_gen in commit cea901af was exactly
that: a WORK_NODES entry with no matching _CAPABILITIES entry).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from app.models import ProjectStatus


@dataclass(frozen=True)
class NodeSpec:
    """Full declared contract for one node type.

    input_schema / output_schema / permissions are documentation-level
    contracts (dict shape, not enforced by a validator here) until a
    concrete need to validate them mechanically arises — this mirrors how
    existing contracts (e.g. node_capabilities.split_contract) are read by
    humans and by Decision.read_contract, not machine-validated today.
    """

    node_id: str
    version: str
    step_code: str
    running_status: ProjectStatus
    ready_status: ProjectStatus
    result_kind: str
    executor: str
    phases: tuple[str, ...]
    effects: tuple[str, ...]
    allowed_actions: tuple[str, ...]
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    permissions: tuple[str, ...] = ()
    managed: bool = False
    verifier: Callable[..., Any] | None = None
    linear: bool = False


def register_node(spec: NodeSpec) -> None:
    """Populate node_registry.WORK_NODES and node_capabilities._CAPABILITIES
    from one NodeSpec. Raises if the node_id is already registered anywhere
    a caller might not expect an overwrite — registration is meant to run
    once at import time, not to patch an existing node type.
    """
    from app.orchestrator import node_registry
    from app.services import node_capabilities

    if spec.node_id in node_registry.WORK_NODES:
        raise ValueError(f"node type already registered: {spec.node_id!r}")

    node_registry.WORK_NODES[spec.node_id] = node_registry.WorkNodeSpec(
        spec.node_id, spec.step_code, spec.running_status, spec.ready_status,
    )
    node_registry.STEP_CODE_TO_NODE_TYPE[spec.step_code] = spec.node_id
    node_registry.NODE_TYPE_TO_STEP_CODE[spec.node_id] = spec.step_code
    node_registry.RUNNING_TO_NODE_TYPE.setdefault(spec.running_status, spec.node_id)
    node_registry.READY_TO_NODE_TYPE.setdefault(spec.ready_status, spec.node_id)
    node_registry.NODE_TYPE_TO_RUNNING[spec.node_id] = spec.running_status
    node_registry.NODE_TYPE_TO_READY[spec.node_id] = spec.ready_status
    if spec.linear:
        node_registry.LINEAR_NODE_TYPES.append(spec.node_id)
        node_registry.LINEAR_RUNNING_PIPELINE.append((spec.running_status, spec.node_id))

    node_capabilities._CAPABILITIES[spec.node_id] = node_capabilities.NodeCapability(
        spec.node_id, spec.result_kind, spec.executor, spec.phases, spec.effects,
        spec.allowed_actions, spec.managed,
    )

    if spec.verifier is not None:
        _VERIFIERS[spec.node_id] = spec.verifier


_VERIFIERS: dict[str, Callable[..., Any]] = {}


def verifier_for(node_id: str) -> Callable[..., Any] | None:
    """The observe-result verifier registered for this node, if any.

    Nodes registered the old way (direct WORK_NODES/_CAPABILITIES edits, as
    every pre-existing node type still is) have no entry here — callers must
    keep falling back to the per-family `if capability.node_type in {...}`
    dispatch in observation.py for those until they're migrated onto
    register_node(). This is additive: it does not change behavior for any
    existing node type.
    """
    return _VERIFIERS.get(node_id)
